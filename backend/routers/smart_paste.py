"""
Smart Paste + Master Order ID + Sheets-from-Master endpoints — Phase-4b
incremental refactor.

Extracts 7 endpoints out of the server.py monolith. Heavy LLM-touching
endpoints (/smart-paste/chat, /smart-paste/photo, /smart-paste create)
STAY in server.py — they share too much state with shipment creation.

Endpoints relocated (all under /api):

  Smart Paste — preview / dry-run / metadata
  ------------------------------------------
  GET  /smart-paste/default-prompt   smart_paste_default_prompt
  POST /smart-paste/parse            smart_paste_parse
  POST /smart-paste/check-duplicate  smart_paste_check_duplicate

  Master Order ID counter (admin/migration helpers)
  -------------------------------------------------
  GET  /orders/master-id-counter     get_master_id_counter
  POST /orders/master-id-counter     set_master_id_counter
  GET  /orders/peek-master-id        peek_master_id_endpoint

  Google Sheets — pull Master rows back to user sheet
  ---------------------------------------------------
  POST /sheets/sync-from-master      sync_from_master_endpoint

Pattern: late-binding `init()` — same as routers/wallet.py. Every
helper / model / global the route bodies reference is pulled in via
`from server import …` inside init(), AFTER server.py finishes
initialising.
"""
from datetime import datetime, timedelta
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel


smart_paste_router = APIRouter(prefix="/api", tags=["smart-paste"])


# ============================== Models ==============================

class SmartPasteRequest(BaseModel):
    """Body for /smart-paste/parse and /smart-paste/check-duplicate."""
    text: str
    use_ai: Optional[bool] = True


class _SyncFromMasterPayload(BaseModel):
    """Body for POST /sheets/sync-from-master."""
    overwrite: Optional[bool] = True


class _CounterSetPayload(BaseModel):
    """Body for POST /orders/master-id-counter."""
    seq: int  # The seq value the NEXT allocation should produce.
    force: Optional[bool] = False  # Allow lowering (risk of duplicates).


def init() -> None:
    """Register routes after server.py finishes initialising."""
    import logging
    _logger = logging.getLogger("routers.smart_paste")
    from server import (  # noqa: WPS433 — intentional late import
        db,
        get_current_user as _get_current_user,
        DEFAULT_SHIPBOT_PROMPT,
        parse_paste_via_llm,
        parse_structured_paste,
        _legacy_with_pincode_enrich,
        _legacy_with_pincode_enrich_v2,
        find_duplicate_matches,
        peek_next_master_order_id,
        sheet_sync_master_to_user,
    )

    # =================  Smart Paste — preview / dry-run  =================

    @smart_paste_router.get("/smart-paste/default-prompt")
    async def smart_paste_default_prompt(
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Expose the bundled ShipBot system prompt + the user's current
        override so the Settings screen can pre-fill the textarea."""
        s = await db.settings.find_one(
            {"user_id": current_user["id"]},
            {"_id": 0, "smart_paste_instructions": 1, "smart_paste_ai_enabled": 1},
        ) or {}
        return {
            "default_prompt": DEFAULT_SHIPBOT_PROMPT,
            "user_instructions": s.get("smart_paste_instructions") or "",
            "ai_enabled": s.get("smart_paste_ai_enabled", True),
        }

    @smart_paste_router.post("/smart-paste/parse")
    async def smart_paste_parse(
        payload: SmartPasteRequest,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Parse pasted text only (no save) — for preview/dry-run.

        Phase-4b+: we now try the LLM (ShipBot-style 18-line schema)
        first, then fall back to the deterministic regex parser on any
        failure so the UI never has a blank state. The LLM result also
        carries:
            missing[]  — fields the user still needs to provide
            complexity — simple/medium/complex classification
            ai_reason  — one-line rationale surfaced in the dialog
        NO wallet charge here — that happens at the final create step.
        """
        text = (payload.text or "").strip()
        legacy = parse_structured_paste(text)

        ai_block: Dict[str, Any] = {
            "used": False, "missing": [], "complexity": "", "reason": "",
            "source": "regex",
        }
        if payload.use_ai is not False:
            s = await db.settings.find_one(
                {"user_id": current_user["id"]},
                {"_id": 0, "smart_paste_instructions": 1},
            ) or {}
            custom = (s.get("smart_paste_instructions") or "").strip()
            ai = await parse_paste_via_llm(text, custom_instructions=custom)
            if ai["source"] == "llm":
                mapped = await _legacy_with_pincode_enrich(ai["fields"])
                merged_fields: Dict[str, Any] = dict(legacy.get("fields", {}))
                for k, v in mapped.items():
                    if v:
                        merged_fields[k] = v
                # Post-normalise amount into a float for the UI's numeric field.
                if isinstance(merged_fields.get("amount"), str):
                    m = re.search(
                        r"(\d+(?:\.\d+)?)",
                        merged_fields["amount"].replace(",", ""),
                    )
                    if m:
                        try:
                            merged_fields["amount"] = float(m.group(1))
                        except Exception:
                            pass
                legacy["fields"] = merged_fields
                still_missing: List[str] = []
                for (schema_key, legacy_key) in [
                    ("NAME", "customer_name"), ("PHONE", "customer_phone"),
                    ("ADDRESS_1", "address_line1"), ("CITY", "city"),
                    ("STATE", "state"), ("PINCODE", "pincode"),
                    ("ITEMS", "items"), ("AMOUNT", "amount"),
                ]:
                    v = merged_fields.get(legacy_key)
                    if not v and (isinstance(v, str) or v in (None, 0)):
                        still_missing.append(schema_key)
                ai_block = {
                    "used": True,
                    "missing": still_missing,
                    "complexity": ai["complexity"],
                    "reason": ai["ai_reason"],
                    "source": "llm",
                }
        legacy["ai"] = ai_block
        return legacy

    @smart_paste_router.post("/smart-paste/check-duplicate")
    async def smart_paste_check_duplicate(
        payload: SmartPasteRequest,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Inspect pasted text for duplicates WITHOUT saving.

        Phase-4b+: we now try the LLM parser first (and merge LLM fields
        over the regex result where non-empty). The ChatGPT-bounce is
        gone — users can paste raw WhatsApp text directly.
        """
        text = (payload.text or "")
        parsed = parse_structured_paste(text)
        fields: Dict[str, Any] = dict(parsed.get("fields", {}) or {})

        # --- LLM pass (best-effort; falls back to regex on any error)
        ai_missing: List[str] = []
        ai_complexity = ""
        ai_reason = ""
        ai_source = "regex"
        ai_warnings: List[str] = []
        pincode_warnings: List[str] = []
        try:
            s = await db.settings.find_one(
                {"user_id": current_user["id"]},
                {
                    "_id": 0,
                    "smart_paste_instructions": 1,
                    "smart_paste_ai_enabled": 1,
                },
            ) or {}
            if s.get("smart_paste_ai_enabled", True):
                ai = await parse_paste_via_llm(
                    text,
                    custom_instructions=(
                        s.get("smart_paste_instructions") or ""
                    ).strip(),
                )
                if ai.get("source") == "llm":
                    ai_warnings = list(ai.get("warnings") or [])
                    mapped, pincode_warnings = (
                        await _legacy_with_pincode_enrich_v2(ai["fields"])
                    )
                    for k, v in mapped.items():
                        if v:
                            fields[k] = v
                    if isinstance(fields.get("amount"), str):
                        m = re.search(
                            r"(\d+(?:\.\d+)?)",
                            fields["amount"].replace(",", ""),
                        )
                        if m:
                            try:
                                fields["amount"] = float(m.group(1))
                            except Exception:
                                pass
                    ai_source = "llm"
                    ai_complexity = ai.get("complexity", "")
                    ai_reason = ai.get("ai_reason", "")
                    for (_sk, _lk) in [
                        ("NAME", "customer_name"), ("PHONE", "customer_phone"),
                        ("ADDRESS_1", "address_line1"), ("CITY", "city"),
                        ("STATE", "state"), ("PINCODE", "pincode"),
                        ("ITEMS", "items"), ("AMOUNT", "amount"),
                    ]:
                        v = fields.get(_lk)
                        if not v and (isinstance(v, str) or v in (None, 0)):
                            ai_missing.append(_sk)
        except Exception:
            _logger.exception(
                "LLM path failed on check-duplicate — using regex only",
            )

        duplicates = await find_duplicate_matches(
            phone=fields.get("customer_phone", ""),
            order_id=(
                fields.get("order_id", "") or fields.get("order_id_hint", "")
            ),
            user_id=current_user["id"],
        )
        all_warnings = list(parsed.get("warnings", []) or [])
        all_warnings.extend(ai_warnings)
        all_warnings.extend(pincode_warnings)
        return {
            "fields": fields,
            "confidence": parsed.get("confidence", {}),
            "warnings": all_warnings,
            "duplicates": duplicates,
            "ai": {
                "used": ai_source == "llm",
                "missing": ai_missing,
                "complexity": ai_complexity,
                "reason": ai_reason,
                "source": ai_source,
            },
        }

    # =================  Master Order ID counter  =====================

    @smart_paste_router.get("/orders/master-id-counter")
    async def get_master_id_counter(
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Phase-7f: Read the current global Master Order ID counter.
        The next allocated ID's sequence will be `current_seq + 1`.
        """
        doc = await db.counters.find_one({"_id": "master_order_id"})
        seq = int((doc or {}).get("seq", 0))
        ist_now = datetime.utcnow() + timedelta(hours=5, minutes=30)
        return {
            "current_seq": seq,
            "next_seq": seq + 1,
            "next_master_order_id":
                f"{ist_now.strftime('%y%m%d')}{str(seq + 1).zfill(5)}",
        }

    @smart_paste_router.post("/orders/master-id-counter")
    async def set_master_id_counter(
        payload: _CounterSetPayload,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Phase-7f: Set the global Master Order ID counter to a specific
        value. Useful when migrating from a legacy system — eg the user
        has already shipped 2200 parcels and wants the next master ID
        to end in `02201` (or `02200` if they pass seq=2199).

        By default, lowering the counter is BLOCKED (creating duplicates
        would break the unique-master_order_id invariant). Pass
        `force: true` to override (admin/migration only — be careful).
        """
        if payload.seq < 0:
            raise HTTPException(status_code=422, detail="seq must be ≥ 0")
        if payload.seq > 9_999_999:
            raise HTTPException(status_code=422, detail="seq too large")
        cur_doc = await db.counters.find_one({"_id": "master_order_id"})
        cur = int((cur_doc or {}).get("seq", 0))
        if payload.seq < cur and not payload.force:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Counter is currently at {cur}. Lowering to "
                    f"{payload.seq} would risk duplicate Master Order IDs. "
                    "Pass force=true to override."
                ),
            )
        await db.counters.update_one(
            {"_id": "master_order_id"},
            {"$set": {"seq": int(payload.seq)}},
            upsert=True,
        )
        ist_now = datetime.utcnow() + timedelta(hours=5, minutes=30)
        return {
            "current_seq": int(payload.seq),
            "next_seq": int(payload.seq) + 1,
            "next_master_order_id":
                f"{ist_now.strftime('%y%m%d')}"
                f"{str(int(payload.seq) + 1).zfill(5)}",
        }

    @smart_paste_router.get("/orders/peek-master-id")
    async def peek_master_id_endpoint(
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Phase-7e: Live preview of the next Master Order ID for the
        New Shipment form. Returns BOTH the predicted master_order_id
        AND the user's two related Settings flags so the frontend can
        decide whether to auto-fill the Order ID input.

        Note: The returned master_order_id is a BEST-GUESS preview. The
        counter is NOT incremented here. The actual ID is allocated
        only when the shipment is saved — so if another user creates a
        shipment in between, the saved ID may differ. Frontend MAY
        pass the previewed value back via `master_order_id` in
        POST /shipments to avoid sequence drift in the common
        single-user case.
        """
        settings_doc = await db.settings.find_one(
            {"user_id": current_user["id"]},
            {
                "_id": 0,
                "order_id_auto_generate": 1,
                "order_id_autofill_in_new_shipment": 1,
            },
        ) or {}
        auto_gen = bool(settings_doc.get("order_id_auto_generate", True))
        autofill = bool(
            settings_doc.get("order_id_autofill_in_new_shipment", True),
        )
        if not auto_gen:
            return {
                "master_order_id": "",
                "auto_generate": False,
                "autofill_in_new_shipment": autofill,
            }
        next_id = await peek_next_master_order_id()
        return {
            "master_order_id": next_id,
            "auto_generate": True,
            "autofill_in_new_shipment": autofill,
        }

    # =================  Sheets — sync-from-master  ====================

    @smart_paste_router.post("/sheets/sync-from-master")
    async def sync_from_master_endpoint(
        payload: _SyncFromMasterPayload,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Phase-C: Pull every Master-Sheet row tagged with the caller's
        user_id into the caller's own personal sheet.

        By default (`overwrite=true`), the user's tab data rows are
        CLEARED and replaced with a fresh copy of all matching master
        rows — this reflects any admin edits / deletions made on the
        Master Sheet.

        Pass `{"overwrite": false}` to APPEND only new rows (dedup by
        `master_order_id`). This preserves any local-only rows the
        user added directly in their sheet (rare but supported).

        Requires the user to have linked their personal sheet via
        Settings → Business → "Google Sheet". Returns 422 if not
        configured.
        """
        if sheet_sync_master_to_user is None:
            raise HTTPException(
                503, detail="Google Sheets integration not loaded.",
            )
        s = await db.settings.find_one(
            {"user_id": current_user["id"]}, {"_id": 0, "sheet": 1},
        ) or {}
        sheet_cfg = s.get("sheet") or {}
        if not isinstance(sheet_cfg, dict):
            sheet_cfg = {}
        user_sheet_id = str(sheet_cfg.get("sheet_id") or "").strip()
        user_tab = str(
            sheet_cfg.get("gid") or sheet_cfg.get("tab") or "0",
        ).strip()
        if not user_sheet_id:
            raise HTTPException(
                422,
                detail=(
                    "Link your Google Sheet first in "
                    "Settings → Business → Google Sheet."
                ),
            )
        try:
            result = sheet_sync_master_to_user(
                user_id=current_user["id"],
                user_sheet_id=user_sheet_id,
                user_tab_or_gid=user_tab,
                overwrite=bool(payload.overwrite),
            )
            return result
        except Exception as e:
            _logger.exception("sync_from_master failed")
            raise HTTPException(502, detail=f"Sync failed: {e}")
