"""
Shipment write/mutation endpoints — Phase-5c-2 incremental refactor.

Extracts the 4 heaviest shipment-creation/modification endpoints out
of server.py monolith. These all share the same complex pipeline:
  plan-room gate → wallet pre-flight → master-sheet backup →
  Mongo insert → user-sheet auto-sync → wallet charge → counter bump.

Endpoints relocated (all under /api):

  POST   /shipments                          create_shipment       (heaviest)
  PUT    /shipments/{shipment_id}            update_shipment        (re-charge + sheet sync)
  DELETE /shipments/{shipment_id}            delete_shipment        (sheet tombstone)
  POST   /orders/pending/{order_id}/ship     ship_pending_order     (Pending → Shipment)

Pattern: late-binding `init()` — same as routers/wallet.py.

Heavy dependencies pulled in via late-bind:
  • Models: Shipment, ShipmentCreate, ShipmentUpdate, ShipOrderRequest
  • Plan/wallet:  plan_room_status, wallet_classify_and_cost,
                  wallet_require, wallet_charge, wallet_balance,
                  bump_label_usage
  • Master-sheet: _backup_shipment_to_master_sheet,
                  sheet_parse_row_from_updated_range,
                  sheet_update_row_status, sheet_mark_row_deleted
  • Custom-fields: _write_custom_values_to_user_sheet_bg
  • Order-id:    generate_master_order_id
  • Misc:        utcnow_iso, strip_id, user_has_feature
"""
from datetime import datetime
import re
import uuid
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException


shipments_write_router = APIRouter(prefix="/api", tags=["shipments-write"])


def init() -> None:
    """Register routes after server.py finishes initialising."""
    import logging
    _logger = logging.getLogger("routers.shipments_write")
    from server import (  # noqa: WPS433 — intentional late import
        db,
        get_current_user as _get_current_user,
        # Models
        Shipment,
        ShipmentCreate,
        ShipmentUpdate,
        ShipOrderRequest,
        # Plan + wallet helpers
        plan_room_status,
        wallet_classify_and_cost,
        wallet_require,
        wallet_charge,
        wallet_balance,
        bump_label_usage,
        # Master sheet helpers
        _backup_shipment_to_master_sheet,
        sheet_parse_row_from_updated_range,
        sheet_update_row_status,
        sheet_mark_row_deleted,
        # Custom fields helper
        _write_custom_values_to_user_sheet_bg,
        # Master Order ID generator
        generate_master_order_id,
        # Misc helpers
        utcnow_iso,
        strip_id,
        user_has_feature,
    )

    # =================  POST /shipments  ============================

    # ── Phase-33 weight parser (2026-06) ─────────────────────────────
    # Accept the free-form `weight` string typed by the operator
    # (e.g. "250gm", "500 gm", "1kg", "2 kg", "1.5 kg") and return
    # a normalised triple {value, unit, display}. The frontend Add
    # form already concatenates `<number> <unit>` (e.g. "250 g") so
    # by far the common case is "<digits> g" or "<digits> kg". This
    # helper handles every reasonable variation an operator might
    # paste in from a customer message:
    #   • Glued     : "250gm", "1kg", "1.5kg"
    #   • Spaced    : "250 g", "1 kg"
    #   • Long unit : "gram"/"grams", "kilo"/"kilos"/"kilogram"
    #   • Bare nums : "0.5"  → defaults to kg (fractional sounds like kg)
    #                 "500"  → defaults to g (3-digit sounds like g)
    # If NO numeric value can be extracted the helper raises so the
    # POST/PUT endpoint can return a 422 to the client — couriers
    # refuse parcels without weight.
    _WEIGHT_RE = re.compile(
        r"""
        ^\s*
        (?P<num>\d+(?:\.\d+)?)        # 250  | 1.5
        \s*
        (?P<unit>
            kgs?|kilograms?|kilos?|kilo|     # kg variants
            gms?|grams?|gm|g                 # g  variants
        )?
        \s*$
        """,
        re.IGNORECASE | re.VERBOSE,
    )

    def _parse_weight(raw: Any) -> Dict[str, Any]:
        """
        Returns {"value": float, "unit": "g"|"kg", "display": str}.
        Raises HTTPException(422) if no numeric value is parseable
        and the input is not blank.
        """
        if raw in (None, "", 0, 0.0):
            return {"value": 0.0, "unit": "", "display": ""}
        # Numeric already (Pending rows sometimes carry float).
        if isinstance(raw, (int, float)):
            n = float(raw)
            # Heuristic: ≤ 50 implies kg, otherwise g (matches how
            # operators key in parcel weight in the Indian courier
            # market — "1.5" means 1.5 kg, "500" means 500 g).
            unit = "kg" if n <= 50 else "g"
            # Use `:g` formatting (same as the string branch) so
            # 500.0 renders as "500 g" not "500.0 g".
            return {"value": n, "unit": unit, "display": f"{n:g} {unit}"}
        s = str(raw).strip()
        if not s:
            return {"value": 0.0, "unit": "", "display": ""}
        m = _WEIGHT_RE.match(s)
        if not m:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Couldn't parse weight '{s}'. Expected a numeric "
                    f"value with optional unit, e.g. '250gm', '500 g', "
                    f"'1kg', '2 kg'."
                ),
            )
        num = float(m.group("num"))
        unit_raw = (m.group("unit") or "").lower()
        if unit_raw.startswith("k"):
            unit = "kg"
        elif unit_raw:
            unit = "g"
        else:
            # No unit token at all → infer from magnitude (same
            # heuristic as the numeric branch above).
            unit = "kg" if num <= 50 else "g"
        return {"value": num, "unit": unit, "display": f"{num:g} {unit}"}

    def _apply_weight_parse(data: Dict[str, Any]) -> None:
        """In-place: validate `data['weight']` and stamp the parsed
        `weight_value` + `weight_unit` fields. No-op when weight is
        blank (the field is optional unless field-controls require it,
        which the frontend enforces). Centralised so POST, PUT, and
        ship-from-pending all use identical parsing rules.
        """
        # `weight_value` / `weight_unit` may already be present from
        # newer clients — trust them but always re-derive the display
        # string so the DB doesn't end up with "1 kg" + value=500.
        parsed = _parse_weight(data.get("weight", ""))
        if parsed["unit"]:
            data["weight"]       = parsed["display"]
            data["weight_value"] = parsed["value"]
            data["weight_unit"]  = parsed["unit"]
        else:
            data["weight"]       = ""
            data["weight_value"] = 0.0
            data["weight_unit"]  = ""

    def _validate_cod_amounts(
        *, cod: float, token: float, user_id: str = "",
    ) -> None:
        """Phase-31 rev-2 validation helper — shared by POST, PUT, and
        the Smart-Paste → ship-from-pending path.

        Rules (matches the canonical contract):
          • cod_amount   MUST be > 0 for COD-mode shipments.
          • token_amount MUST be ≥ 0 (negative advance is meaningless).
          • token > cod  is allowed (refund / overpay edge case) but
                          logged for audit.

        Raises HTTPException(422) on the hard rules; emits a warning
        but does NOT raise for the soft rule.
        """
        if cod <= 0:
            raise HTTPException(
                status_code=422,
                detail=(
                    "COD to Collect must be greater than zero for "
                    "COD-mode shipments. Use Prepaid mode if the "
                    "courier has nothing to collect at delivery."
                ),
            )
        if token < 0:
            raise HTTPException(
                status_code=422,
                detail="Token / Advance amount cannot be negative.",
            )
        if token > cod:
            _logger.warning(
                "shipment.write — Token (%.2f) exceeds COD (%.2f) "
                "for user %s; accepting but flagging for audit.",
                token, cod, (user_id or "?")[:8],
            )

    # ── Phase-34 (2026-06-24) — CANONICAL Order-Value calculator ──────
    # Single source of truth for the rule:
    #
    #   Total Order Value (amount) = cod_amount + token_amount   (COD)
    #   Total Order Value (amount) = entered amount              (Prepaid)
    #
    # Every write path (POST /shipments, PUT /shipments/{id},
    # ship_pending_order) MUST call this helper instead of computing
    # `cod + token` inline. The frontend's `lib/orderAmounts.ts`
    # `computeOrderAmounts()` mirrors this contract exactly so the
    # client preview and the persisted document never disagree.
    #
    # Validation policy:
    #   • If `validate=True` (default), raises HTTP 422 on COD ≤ 0
    #     (COD-mode only) and on token < 0.
    #   • Soft-warn when token > cod (refund / overpay) — logged,
    #     not rejected.
    def compute_order_amounts(
        *,
        cod: Any,
        token: Any,
        payment_mode: str,
        validate: bool = True,
        user_id: str = "",
    ) -> Dict[str, float]:
        """Return the canonical {amount, cod_amount, token_amount}
        triple for a shipment write. `amount` is ALWAYS derived —
        never stored independently."""
        _cod = float(cod or 0)
        _tok = float(token or 0)
        is_cod = str(payment_mode or "").upper() == "COD"
        if is_cod:
            if validate:
                _validate_cod_amounts(cod=_cod, token=_tok, user_id=user_id)
            return {
                "amount":       _cod + _tok,
                "cod_amount":   _cod,
                "token_amount": _tok,
            }
        # Prepaid / other modes — entered value IS the total; no COD.
        # We still accept the typed value as `amount` so admin reports
        # show the right gross figure.
        return {
            "amount":       _cod,   # operators type the gross into "amount" for prepaid
            "cod_amount":   0.0,
            "token_amount": _tok,
        }

    @shipments_write_router.post("/shipments", response_model=Shipment)
    async def create_shipment(
        payload: ShipmentCreate,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        # Phase-3a/4a combined gate:
        #   • If plan has room  → consume a plan slot + AI credit.
        #   • If plan exhausted → rely on wallet overage (paid plans only);
        #     free-trial users must upgrade (no overage).
        #   • Free-trial expired → refuse outright.
        room = await plan_room_status(db, current_user)
        if room["trial_expired"]:
            raise HTTPException(
                status_code=402,
                detail="Your 7-day free trial has expired. Upgrade to continue.",
            )
        if room.get("plan_expired"):
            exp = room.get("plan_expires_at")
            try:
                exp_dt = datetime.fromisoformat(str(exp))
                exp_str = exp_dt.strftime("%d %b %Y")
            except Exception:
                exp_str = str(exp) or "the renewal date"
            raise HTTPException(
                status_code=402,
                detail=(
                    f"Your {room.get('plan_name','plan')} subscription "
                    f"expired on {exp_str}. Renew from Plans to keep "
                    "creating labels."
                ),
            )
        if room["daily_blocked"]:
            raise HTTPException(
                status_code=402,
                detail=(
                    "Daily limit reached (100/day on Platinum). "
                    "Please try again tomorrow."
                ),
            )
        plan_key = room["plan"]
        plan_has_room = bool(room["plan_has_room"])
        if (not plan_has_room) and plan_key == "free_trial":
            raise HTTPException(
                status_code=402,
                detail=(
                    "Free trial limit reached (10 labels). Upgrade to "
                    "Silver or higher to keep shipping."
                ),
            )

        data = payload.model_dump()
        addr_text = " ".join(filter(None, [
            data.get("address_line1", ""), data.get("address_line2", ""),
            data.get("city", ""), data.get("state", ""),
            str(data.get("pincode", "")),
        ])).strip()
        # Per-user AI rate card so Settings → AI Processing Charges
        # takes effect immediately. Defaults 0.5/1/2 used when not set.
        _s = await db.settings.find_one(
            {"user_id": current_user["id"]},
            {"_id": 0, "ai_cost_simple": 1, "ai_cost_medium": 1,
             "ai_cost_complex": 1},
        ) or {}
        ai_costs = {
            "simple":  float(_s.get("ai_cost_simple", 0.5)),
            "medium":  float(_s.get("ai_cost_medium", 1.0)),
            "complex": float(_s.get("ai_cost_complex", 2.0)),
        }
        # LLM-backed complexity classification with safe heuristic fallback.
        breakdown, ai_reason = await wallet_classify_and_cost(
            current_user, addr_text, plan_has_room, ai_costs=ai_costs,
        )
        breakdown = await wallet_require(
            db, current_user, addr_text, plan_has_room,
            complexity_override=breakdown.ai_complexity,
            ai_costs=ai_costs,
        )

        if data.get("courier_id") and not data.get("courier_name"):
            c = await db.couriers.find_one(
                {"id": data["courier_id"], "user_id": current_user["id"]},
                {"_id": 0},
            )
            if c:
                data["courier_name"] = c.get("name", "")
        # Phase-34 — Use the canonical compute_order_amounts() helper
        # for the POST /shipments path. Single source of truth for
        # `amount = cod + token` (COD) or `amount = entered` (Prepaid).
        _cod_in = (
            float(data["cod_amount"])
            if data.get("cod_amount") is not None
            else float(data.get("amount") or 0)
        )
        _amounts = compute_order_amounts(
            cod=_cod_in,
            token=data.get("token_amount") or 0,
            payment_mode=data.get("payment_mode", ""),
            validate=True,
            user_id=current_user.get("id", ""),
        )
        data["amount"]       = _amounts["amount"]
        data["cod_amount"]   = _amounts["cod_amount"]
        data["token_amount"] = _amounts["token_amount"]
        if data.get("items") is None:
            data["items"] = []
        if data.get("custom_values") is None:
            data["custom_values"] = {}
        # Phase-33 — Parse + validate weight. Stamps weight,
        # weight_value, weight_unit so reports / rate calc can
        # work with numeric values without re-parsing every time.
        _apply_weight_parse(data)

        # ── Phase-7d/e Master Order ID + User Order ID for manual create ──
        settings_doc = await db.settings.find_one(
            {"user_id": current_user["id"]},
            {"_id": 0, "order_id_auto_generate": 1},
        ) or {}
        auto_gen = bool(settings_doc.get("order_id_auto_generate", True))
        incoming_master = str(data.get("master_order_id") or "").strip()
        user_order_id = str(data.get("order_id") or "").strip()

        # Phase-29 TOCTOU fix
        # ------------------------------------------------------------
        # The Add-Shipment form pre-fills `order_id` from the
        # /api/orders/master-id-counter peek endpoint with the SAME
        # value as `master_order_id`. If our collision-retry loop below
        # has to bump `master_oid` to a fresh value, the stale prefill
        # in `order_id` would survive untouched and the two columns
        # would diverge for the row we're about to insert.
        #
        # Detect whether `order_id` is genuine (typed by the user, or
        # supplied by an external import / webhook) vs. an auto-prefill
        # that mirrors the peek master. We treat the value as PREFILL
        # iff it equals `incoming_master` byte-for-byte; in every other
        # case (empty, different number, alphanumeric custom code) we
        # preserve it verbatim.
        order_id_explicit = bool(user_order_id) and (
            user_order_id != incoming_master
        )

        if auto_gen:
            if incoming_master and re.match(r"^\d{6}\d{5,}$", incoming_master):
                # Frontend sent a pre-allocated master ID — trust it
                # (atomic peek/consume already happened upstream).
                master_oid = incoming_master
            else:
                master_oid = await generate_master_order_id()
            retries = 0
            while await db.shipments.find_one(
                {"master_order_id": master_oid}, {"_id": 1},
            ):
                master_oid = await generate_master_order_id()
                retries += 1
                if retries > 5:
                    raise HTTPException(
                        status_code=500,
                        detail=(
                            "Could not allocate a unique Master Order ID "
                            "— retry."
                        ),
                    )
            data["master_order_id"] = master_oid
            if order_id_explicit:
                # User typed / external source — leave their value alone.
                data["order_id"] = user_order_id
            else:
                # Auto-prefill (or empty) — sync to the FINAL master id
                # so the two columns never diverge after retry.
                data["order_id"] = master_oid
        else:
            if not user_order_id:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "Order ID is required when Auto-Generate is OFF. "
                        "Enter your own Order ID or enable Auto-Generate "
                        "in Settings."
                    ),
                )
            data["master_order_id"] = ""
            data["order_id"] = user_order_id

        shipment = Shipment(**data)
        doc = shipment.model_dump()
        doc["user_id"] = current_user["id"]

        # ── Mandatory Master Sheet backup (all plans) ──
        sheet_meta = await _backup_shipment_to_master_sheet(
            current_user=current_user,
            data=doc,
            notice="via Add Shipment",
        )
        if sheet_meta.get("deferred"):
            doc["master_backup_status"] = "pending"
            doc["master_backup_payload"] = (
                sheet_meta.get("_pending_payload") or {}
            )
            doc["master_backup_last_error_at"] = utcnow_iso()
        elif (sheet_meta and sheet_meta.get("updated_range")
              and sheet_parse_row_from_updated_range):
            try:
                row = sheet_parse_row_from_updated_range(
                    sheet_meta["updated_range"],
                )
                if row:
                    doc["sheet_row_num"] = int(row)
                    if hasattr(shipment, "sheet_row_num"):
                        shipment.sheet_row_num = int(row)
                    doc["master_backup_status"] = "ok"
            except Exception:
                pass

        # Phase-32 rev-3 (2026-06-24) — Catch DuplicateKeyError from the
        # compound unique index `(order_id, user_id)` and return a clean
        # 409 instead of letting it bubble up as a generic 500. The
        # index was added in Phase-32 as a TOCTOU guard; we just never
        # wrapped this insert (POST /shipments path). Operators kept
        # seeing "Request failed with status code 500" when their
        # auto-generated order_id collided with an existing row.
        try:
            await db.shipments.insert_one(doc)
        except Exception as e:
            # Avoid importing pymongo at module top to keep this file
            # decoupled from the driver type when running tests.
            if "duplicate key" in str(e).lower() or "E11000" in str(e):
                _logger.warning(
                    "shipment.create — duplicate order_id %r for user %s; "
                    "rejecting with 409.",
                    doc.get("order_id"), current_user.get("id", "?")[:8],
                )
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Order ID '{doc.get('order_id')}' already exists. "
                        "Please use a different Order ID or auto-generate "
                        "a fresh one."
                    ),
                )
            raise

        # Phase H: User personal-sheet auto-sync (best-effort).
        try:
            import user_sheet_sync as _uss
            await _uss.sync_create(db, current_user, doc)
        except Exception:
            _logger.exception(
                "user-sheet auto-sync (create) failed (non-fatal)",
            )

        # Best-effort: write custom-field values to user's personal sheet.
        await _write_custom_values_to_user_sheet_bg(
            current_user, doc.get("custom_values") or {},
        )
        # Only bump plan counter when the plan actually covered this label.
        if plan_has_room:
            await bump_label_usage(db, current_user)
        # Debit wallet (safe no-op for free-trial + trial-room combo).
        await wallet_charge(db, current_user, doc["id"], breakdown)
        return shipment

    # =================  PUT /shipments/{id}  ========================

    @shipments_write_router.put(
        "/shipments/{shipment_id}", response_model=Shipment,
    )
    async def update_shipment(
        shipment_id: str,
        payload: ShipmentUpdate,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        # Phase-33 — Terminal-state lock. Cancelled / Cancel by buyer /
        # Returned shipments are PERMANENTLY dead. Any attempt to
        # mutate them (status change, edit, re-ship workflow, etc.)
        # is rejected at the API boundary with HTTP 423 LOCKED so the
        # frontend can pattern-match the response and show the right
        # banner.  Reads remain fully open.
        from lib.terminal_states import (
            is_terminal_shipment_status, TERMINAL_LOCK_DETAIL,
        )
        existing = await db.shipments.find_one(
            {"id": shipment_id, "user_id": current_user["id"]},
            {"_id": 0, "status": 1, "payment_mode": 1, "token_amount": 1},
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Shipment not found")
        if is_terminal_shipment_status(existing.get("status")):
            raise HTTPException(
                status_code=423,
                detail=TERMINAL_LOCK_DETAIL,
            )

        update = {
            k: v for k, v in payload.model_dump().items() if v is not None
        }
        if "status" in update and update["status"] == "Delivered":
            update["delivered_at"] = utcnow_iso()
        # Phase-33 — Stamp terminal-state metadata when the target
        # status is one of the dead-states so reporting can group /
        # filter on cancelled_at (and we have an audit trail).
        if "status" in update and is_terminal_shipment_status(update["status"]):
            update.setdefault("cancelled_at", utcnow_iso())
            update.setdefault("cancel_reason", "user_action")
        if "amount" in update or "cod_amount" in update:
            # Phase-34 — Canonical compute_order_amounts() helper.
            # Single source of truth for `amount = cod + token`. The
            # frontend sends `cod_amount` as the COD-to-Collect; legacy
            # clients send only `amount` for a COD row which is treated
            # as the COD-to-collect (verbatim fallback).
            _pmode = (
                update.get("payment_mode")
                or existing.get("payment_mode")
                or ""
            )
            _tok = float(
                update["token_amount"]
                if "token_amount" in update and update["token_amount"] is not None
                else (existing.get("token_amount") or 0),
            )
            _cod = float(
                update["cod_amount"]
                if "cod_amount" in update and update["cod_amount"] is not None
                else update.get("amount", 0) or 0,
            )
            _amounts = compute_order_amounts(
                cod=_cod, token=_tok,
                payment_mode=_pmode,
                validate=(_pmode == "COD"),
                user_id=current_user.get("id", ""),
            )
            update["amount"]       = _amounts["amount"]
            update["cod_amount"]   = _amounts["cod_amount"]
            update["token_amount"] = _amounts["token_amount"]
        # Phase-33 — Parse + validate weight when it's part of the
        # update payload. Re-derives weight_value + weight_unit so
        # the row stays self-consistent even when the operator
        # edits only the weight field.
        if "weight" in update or "weight_value" in update or "weight_unit" in update:
            _apply_weight_parse(update)
        if not update:
            raise HTTPException(status_code=400, detail="No fields to update")

        # Phase-19 — Auto-tag as "Modified" when the admin edits any
        # user-facing detail (everything that's NOT the status itself
        # or its auto-derived siblings). Pure status flips coming from
        # the new Next-Stage button on the Shipments card should NOT
        # flag the order as modified — that would defeat the filter.
        _auto_derived = {"status", "delivered_at", "cod_amount"}
        _content_keys = [k for k in update.keys() if k not in _auto_derived]
        if _content_keys:
            update.setdefault("is_modified", True)
            update.setdefault("modified_at", utcnow_iso())

        # Two-Way Status Sync: detect status transitions BEFORE mutation
        # so we can write the new value to the Master Sheet row if linked.
        new_status = update.get("status")
        prev_doc = None
        if new_status is not None:
            prev_doc = await db.shipments.find_one(
                {"id": shipment_id, "user_id": current_user["id"]},
                {"_id": 0},
            )

        res = await db.shipments.find_one_and_update(
            {"id": shipment_id, "user_id": current_user["id"]},
            {"$set": update},
            return_document=True,
        )
        if not res:
            raise HTTPException(status_code=404, detail="Shipment not found")

        # Best-effort Master-Sheet write-back. Plan-gated:
        # `sheet_two_way_status_sync` must be enabled for the user's plan.
        if (
            new_status is not None
            and prev_doc is not None
            and (prev_doc.get("status") or "") != new_status
            and prev_doc.get("sheet_row_num")
            and sheet_update_row_status is not None
            and await user_has_feature(
                current_user, "sheet_two_way_status_sync",
            )
        ):
            try:
                tracking = (
                    prev_doc.get("tracking_id")
                    or res.get("tracking_id") or ""
                )
                extra = f"Tracking: {tracking}" if tracking else None
                sheet_update_row_status(
                    int(prev_doc["sheet_row_num"]),
                    new_status,
                    extra_notice=extra,
                )
                _logger.info(
                    f"Sheet status sync OK: row="
                    f"{prev_doc['sheet_row_num']} → {new_status}"
                )
            except Exception:
                _logger.exception("Sheet status sync failed (non-fatal)")

        # Phase H: User personal-sheet status auto-sync.
        if (
            new_status is not None
            and prev_doc is not None
            and (prev_doc.get("status") or "") != new_status
        ):
            try:
                import user_sheet_sync as _uss
                tracking = (
                    prev_doc.get("tracking_id")
                    or res.get("tracking_id") or ""
                )
                await _uss.sync_status_change(
                    db, current_user, res, new_status,
                    extra_notice=(
                        f"Tracking: {tracking}" if tracking else None
                    ),
                )
            except Exception:
                _logger.exception(
                    "user-sheet status auto-sync failed (non-fatal)",
                )

            # Phase-28: WhatsApp event-trigger dispatch on stage change.
            # Provider-side automation IDs are configured by Super Admin;
            # this call is best-effort and NEVER raises into the API path.
            try:
                from routers.whatsapp_provider import (
                    dispatch_event as _wpp_dispatch,
                    STAGE_TO_EVENT_KEY as _STAGE_MAP,
                    _shipment_to_context as _ship_ctx,
                )
                event_key = _STAGE_MAP.get(new_status)
                if event_key:
                    ctx = _ship_ctx(
                        res,
                        business_name=(current_user.get("shop_name")
                                       or current_user.get("name") or ""),
                        business_phone=(current_user.get("phone") or ""),
                    )
                    await _wpp_dispatch(
                        db, event_key, ctx,
                        phone=ctx.get("customer_phone") or "",
                    )
            except Exception:
                _logger.exception(
                    "WhatsApp event dispatch failed (non-fatal)",
                )

        return Shipment(**strip_id(res))

    # =================  DELETE /shipments/{id}  =====================

    @shipments_write_router.delete("/shipments/{shipment_id}")
    async def delete_shipment(
        shipment_id: str,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Phase-33 — HARD DELETE REMOVED.

        The legacy delete endpoint has been re-purposed into a
        "Cancel Order" flip. The local record is NEVER removed from
        Mongo so that:
          * Reporting / history / customer contacts stay intact.
          * Sync sources (sheet, webhook, file-import) can detect a
            previously-cancelled order ID and refuse to re-insert it
            as a fresh active row.

        Behaviour:
          * If the shipment is already in a terminal state, the call
            is idempotent → 200 with `already_cancelled=True`.
          * Otherwise the status is flipped to `Cancelled`, with
            `cancelled_at` / `cancel_reason="user_action"` stamped.
          * Existing Master-Sheet tombstone / user-sheet status-sync
            integrations still run so the source-of-truth row stays
            in lock-step.
        """
        from lib.terminal_states import is_terminal_shipment_status

        doc = await db.shipments.find_one(
            {"id": shipment_id, "user_id": current_user["id"]}, {"_id": 0},
        )
        if not doc:
            raise HTTPException(status_code=404, detail="Shipment not found")

        # Already terminal → idempotent ack so the frontend can show
        # the right toast without spamming the API.
        if is_terminal_shipment_status(doc.get("status")):
            return {
                "ok": True,
                "already_cancelled": True,
                "status": doc.get("status"),
            }

        sheet_result: Dict[str, Any] = {"attempted": False}
        row_num = doc.get("sheet_row_num")
        if (
            row_num
            and sheet_update_row_status is not None
            and await user_has_feature(
                current_user, "sheet_two_way_status_sync",
            )
        ):
            sheet_result["attempted"] = True
            try:
                tracking = doc.get("tracking_id") or ""
                sheet_update_row_status(
                    int(row_num),
                    "Cancelled",
                    extra_notice=(
                        f"Tracking: {tracking}" if tracking else None
                    ),
                )
                sheet_result["ok"] = True
            except Exception as e:
                _logger.exception("Sheet cancel sync failed (non-fatal)")
                sheet_result["ok"] = False
                sheet_result["error"] = str(e)

        # Flip to Cancelled (no removal) — write audit fields.
        await db.shipments.update_one(
            {"id": shipment_id, "user_id": current_user["id"]},
            {"$set": {
                "status": "Cancelled",
                "cancelled_at": utcnow_iso(),
                "cancel_reason": "user_action",
            }},
        )

        # Best-effort user-sheet status sync (same as the PUT path).
        try:
            import user_sheet_sync as _uss
            tracking = doc.get("tracking_id") or ""
            fresh = await db.shipments.find_one(
                {"id": shipment_id, "user_id": current_user["id"]},
                {"_id": 0},
            )
            await _uss.sync_status_change(
                db, current_user, fresh, "Cancelled",
                extra_notice=(
                    f"Tracking: {tracking}" if tracking else None
                ),
            )
        except Exception:
            _logger.exception(
                "user-sheet auto-sync (cancel) failed (non-fatal)",
            )
        return {"ok": True, "sheet": sheet_result, "status": "Cancelled"}

    # =================  POST /orders/pending/{id}/ship  =============

    @shipments_write_router.post(
        "/orders/pending/{order_id}/ship", response_model=Shipment,
    )
    async def ship_pending_order(
        order_id: str,
        payload: ShipOrderRequest,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Promote a pending order to a real shipment — allocates a
        tracking ID, charges wallet, syncs sheets, and links the
        pending order to the new shipment."""
        # Phase-3a/4a combined gate
        room = await plan_room_status(db, current_user)
        if room["trial_expired"]:
            raise HTTPException(
                status_code=402,
                detail=(
                    "Your 7-day free trial has expired. "
                    "Upgrade to continue."
                ),
            )
        if room["daily_blocked"]:
            raise HTTPException(
                status_code=402,
                detail="Daily limit reached. Please try again tomorrow.",
            )
        plan_has_room = bool(room["plan_has_room"])
        if (not plan_has_room) and room["plan"] == "free_trial":
            raise HTTPException(
                status_code=402,
                detail=(
                    "Free trial limit reached (10 labels). "
                    "Upgrade to Silver or higher."
                ),
            )

        order = await db.pending_orders.find_one(
            {"id": order_id, "user_id": current_user["id"]}, {"_id": 0},
        )
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
        # Phase-33 — Cancelled pending orders cannot be promoted to a
        # shipment. This is the final back-stop after the PUT-update
        # lock: even a direct POST .../ship call gets rejected.
        from lib.terminal_states import (
            is_terminal_pending_status, TERMINAL_LOCK_DETAIL,
        )
        if is_terminal_pending_status(order.get("status")):
            raise HTTPException(
                status_code=423,
                detail=TERMINAL_LOCK_DETAIL,
            )
        if order.get("status") == "shipped":
            raise HTTPException(status_code=400, detail="Order already shipped")

        courier = await db.couriers.find_one(
            {"id": payload.courier_id, "user_id": current_user["id"]},
            {"_id": 0},
        )
        if not courier:
            raise HTTPException(status_code=404, detail="Courier not found")

        # ─── Allocate tracking ID ────────────────────────────────
        # Phase-23 — Manual-tracking couriers (India Post Speed Post
        # stickers, Anjani Courier physical AWB, etc.) skip the
        # sequential counter entirely. The frontend collects the AWB
        # typed/scanned from the printed sticker and passes it as
        # `manual_tracking_id`. For every other courier the original
        # series_prefix + zero-padded next_number path runs unchanged,
        # preserving 100% of historic behaviour.
        # Phase-32 (rev-2, 2026-06) — Tracking-ID precedence flip.
        #
        # OLD behaviour: a courier's `manual_tracking` flag was the
        # single source of truth. If the flag was OFF, we ALWAYS
        # auto-generated `series_prefix + next_number`, silently
        # discarding whatever `payload.manual_tracking_id` the
        # operator had typed/scanned. That broke the Manual / Scan
        # workflow for pending orders — operators expected the
        # scanned AWB to land on the shipment, not a freshly minted
        # sequential code.
        #
        # NEW behaviour (matches the direct POST /shipments path):
        #   1. If the operator passes a non-empty `manual_tracking_id`,
        #      USE IT AS-IS regardless of the courier's flag. The
        #      operator's intent — typed or scanned — wins.
        #   2. Otherwise, if the courier IS configured for manual
        #      tracking, reject (we can't auto-generate for a manual
        #      courier — no `next_number` is maintained).
        #   3. Otherwise (no manual_tracking_id AND auto-mode courier)
        #      generate `series_prefix + next_number` exactly as before
        #      and bump the counter.
        manual_id   = (payload.manual_tracking_id or "").strip()
        manual_mode = bool(courier.get("manual_tracking"))

        if manual_id:
            # Operator typed / scanned an explicit tracking ID — use it
            # regardless of the courier's flag.
            tracking_id = manual_id
            # No counter bump — the supplied AWB is by definition
            # outside our sequential series.
        elif manual_mode:
            # Manual-only courier but operator didn't supply an AWB
            # → still an error.
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Courier '{courier.get('name')}' uses manual tracking. "
                    "Please enter the tracking number from the courier sticker."
                ),
            )
        else:
            # Auto-mode courier with no override → original sequential
            # path runs verbatim, counter is incremented atomically.
            padding  = int(courier.get("number_padding") or 4)
            next_num = int(courier.get("next_number") or 1)
            tracking_id = (
                f"{courier.get('series_prefix','')}"
                f"{str(next_num).zfill(padding)}"
            )
            await db.couriers.update_one(
                {"id": courier["id"], "user_id": current_user["id"]},
                {"$inc": {"next_number": 1}},
            )

        # Build shipment from order + optional overrides
        overrides = payload.overrides or {}

        def _get(k, default=""):
            return overrides.get(k, order.get(k, default))

        # items as list — Phase-32 hardening (2026-06-20):
        # Frontend overrides now pass `items` as a real Python list
        # (the form keeps multiple items as an array), but the
        # PendingOrder rows still store it as a comma-separated string.
        # The old `items_str.split(",")` blew up with AttributeError 500
        # the moment overrides delivered a list. Accept both shapes:
        items_raw = _get("items", "")
        if isinstance(items_raw, list):
            # Already a list — just normalise whitespace + drop empties.
            items_list = [
                str(s).strip()
                for s in items_raw
                if str(s).strip()
            ]
        elif isinstance(items_raw, str):
            items_list = [
                s.strip()
                for s in (items_raw.split(",") if items_raw else [])
                if s.strip()
            ]
        else:
            # Defensive: any other type (e.g. None, dict) → empty list.
            items_list = []

        # Phase F2.1 — Status + Timestamp from imported sources.
        # When the source row already carried a real-world status
        # (Shipped/Delivered/etc.) and/or timestamp, those land on the
        # PendingOrder as `imported_status` / `imported_at`. We copy
        # them onto the resulting Shipment so historical imports land
        # in the right pipeline bucket + carry the original timestamp.
        _imp_status = (order.get("imported_status") or "").strip()
        _imp_at     = (order.get("imported_at") or "").strip()
        _ship_status = _imp_status if _imp_status else "Pending"
        _ship_created_at = _imp_at if _imp_at else utcnow_iso()

        # Phase-31 rev-3 (2026-06-24) — Compute the EFFECTIVE COD-to-Collect
        # ONCE here. The same value is reused by:
        #   • _validate_cod_amounts (below) — gates HTTP 422 on COD ≤ 0.
        #   • ship_doc cod_amount + amount math (further down).
        # Sharing one local var prevents the validator from seeing a
        # different value than what we actually persist (the very bug
        # that slipped through rev-2 — operator overrides cod_amount=0
        # but pending.amount=600, so validator passed and a $0-COD row
        # made it into the DB).
        if overrides.get("cod_amount") is not None:
            _effective_cod = float(overrides.get("cod_amount") or 0)
        else:
            # Legacy / Smart-Paste path: pending.amount IS the COD.
            _effective_cod = float(_get("amount", 0) or 0)
        _effective_token = float(_get("token_amount", 0) or 0)

        # Phase-31 rev-2 validation — also gate the ship-from-pending
        # path. The Pending row stores `amount` as the COD-to-Collect
        # (frontend bulk-paste & smart-paste flows write it that way),
        # so validate the same way we do on direct POST.
        if _get("payment_mode") == "COD":
            _validate_cod_amounts(
                cod=_effective_cod,
                token=_effective_token,
                user_id=current_user.get("id", ""),
            )

        # Phase-33 — Parse + validate weight on the ship-from-pending
        # path too. The pending row may have weight from CSV / paste,
        # so normalise it here before constructing the shipment doc.
        _w_parsed = _parse_weight(_get("weight", ""))

        ship_doc = {
            "id":                 str(uuid.uuid4()),
            "tracking_id":        tracking_id,
            "courier_id":         courier["id"],
            "courier_name":       courier.get("name", ""),
            "customer_name":      _get("customer_name"),
            "customer_phone":     _get("customer_phone"),
            "customer_alt_phone": _get("customer_alt_phone"),
            "customer_email":     _get("customer_email"),
            "customer_gstin":     _get("customer_gstin"),
            "address_line1":      _get("address_line1"),
            "address_line2":      _get("address_line2"),
            "city":               _get("city"),
            "state":              _get("state"),
            "pincode":            _get("pincode"),
            "items":              items_list,
            # `item_description` is the legacy flat string used by old
            # WhatsApp templates / CSV exports — derive it from the
            # normalised list so both shapes stay in sync.
            "item_description":   ", ".join(items_list),
            # Phase-31 rev-3 (2026-06-24) — CANONICAL COD math.
            # Bug: the ship-from-pending path was using `amount`
            # from the overrides for BOTH `cod_amount` AND the
            # downstream total computation. When the frontend
            # actually sent `cod_amount` explicitly in overrides
            # (e.g. operator edited COD-to-Collect on the form
            # right before tapping Ship), we silently discarded
            # it and used the stale pending.amount instead, then
            # added token a second time inside `amount`. Net
            # effect: total inflated by token whenever the
            # operator nudged the COD value.
            #
            # New precedence — matches POST /shipments behaviour:
            #   • cod_amount = overrides.cod_amount when present;
            #                  else fallback to overrides.amount /
            #                  pending.amount (Smart-Paste case).
            #   • amount     = cod_amount + token_amount (Total).
            #   • token_amount = overrides/pending verbatim.
            #
            # Legacy rows that don't carry cod_amount on the
            # pending doc keep the same behaviour they had before
            # this fix (pending.amount is treated as COD-to-Collect
            # because that's how smart-paste ingests it).
            "amount":             0.0,   # placeholder; replaced below
            "cod_amount":         0.0,   # placeholder; replaced below
            "token_amount":       float(_get("token_amount", 0) or 0),
            "weight":             _w_parsed["display"],
            "weight_value":       _w_parsed["value"],
            "weight_unit":        _w_parsed["unit"],
            "payment_mode":       _get("payment_mode", "COD"),
            # Phase-21 — Order-ID priority chain on shipments:
            #   1. PendingOrder.order_id (already resolved at ingest:
            #      upstream order_id if the source sent one, else our
            #      master_order_id when auto-gen is ON, else the user's
            #      manual entry).
            #   2. order_id_hint (regex-parsed legacy hint — only set
            #      when the pasted text contained an explicit "Order
            #      #..." line; kept for backwards-compat with rows that
            #      were ingested BEFORE the resolved order_id was wired
            #      onto smart-paste).
            #   3. master_order_id — final safety net so the Shipment
            #      NEVER ships with a blank Order ID. The user
            #      previously reported missing order_ids on shipments
            #      coming from webhooks / files that had no upstream
            #      id; the master id is the right thing to surface
            #      since it's what we already wrote to the Master Sheet
            #      and what the Order-ID counter assigned to this row.
            "order_id":           (
                _get("order_id")
                or _get("order_id_hint")
                or _get("master_order_id")
            ),
            "master_order_id":    str(order.get("master_order_id") or ""),
            "notes":              _get("notes"),
            "status":             _ship_status,
            "created_at":         _ship_created_at,
            "updated_at":         utcnow_iso(),
            # Phase F2.4 — also persist the raw imported values on the
            # Shipment doc itself so analytics + the Detail page can
            # show "imported from Shopify on 29 Apr" without joining
            # back to the (now-deleted) PendingOrder. Empty strings
            # when the source row didn't carry these fields.
            "imported_status":    _imp_status,
            "imported_at":        _imp_at,
            # Phase F2.1 — carry per-shipment custom-field values from
            # the source PendingOrder so Smart Paste / CSV / Webhook
            # imports preserve dynamic fields end-to-end. Existing
            # `_write_custom_values_to_user_sheet_bg` (below) ALSO
            # writes to the user's Google sheet — both are needed:
            # the user-sheet write is best-effort, the doc embed is
            # mandatory (analytics + label rendering read it).
            "custom_values":      order.get("custom_values") or {},
            # Carry the Master Sheet row number so a future delete can
            # soft-delete the exact tombstone row.
            "sheet_row_num":      order.get("sheet_row_num"),
            "user_id":            current_user["id"],
        }
        # Phase-34 — Use canonical compute_order_amounts() helper.
        # Reusing `_effective_cod` / `_effective_token` here guarantees
        # the validator and the persisted document agree on the same
        # number. Single source of truth for `amount = cod + token`.
        _amounts = compute_order_amounts(
            cod=_effective_cod,
            token=_effective_token,
            payment_mode=_get("payment_mode", "") or "",
            validate=False,   # already validated above
            user_id=current_user.get("id", ""),
        )
        if _get("payment_mode") == "COD":
            ship_doc["amount"]     = _amounts["amount"]
            ship_doc["cod_amount"] = _amounts["cod_amount"]
        else:
            ship_doc["cod_amount"] = 0.0
            ship_doc["amount"]     = float(_get("amount", 0) or 0)
        ship_doc["token_amount"]   = _amounts["token_amount"]

        # Phase F2.1 — when the import landed an already-Delivered row,
        # also stamp delivered_at so analytics + the Detail page show
        # the same timestamp the user saw in their source file.
        if _ship_status == "Delivered" and _imp_at:
            ship_doc["delivered_at"] = _imp_at

        # ── Mandatory Master Sheet backup (all plans) ──
        # PendingOrders that came in via Smart Paste already wrote a row
        # at creation time — `sheet_row_num` is set on the order. CSV /
        # sheet-sync sourced ones don't carry that row, so we MUST append.
        if not ship_doc.get("sheet_row_num"):
            sheet_meta = await _backup_shipment_to_master_sheet(
                current_user=current_user,
                data=ship_doc,
                notice=f"via Ship · Tracking: {tracking_id}",
            )
            if sheet_meta.get("deferred"):
                ship_doc["master_backup_status"] = "pending"
                ship_doc["master_backup_payload"] = (
                    sheet_meta.get("_pending_payload") or {}
                )
                ship_doc["master_backup_last_error_at"] = utcnow_iso()
            elif (sheet_meta and sheet_meta.get("updated_range")
                  and sheet_parse_row_from_updated_range):
                try:
                    row = sheet_parse_row_from_updated_range(
                        sheet_meta["updated_range"],
                    )
                    if row:
                        ship_doc["sheet_row_num"] = int(row)
                        ship_doc["master_backup_status"] = "ok"
                except Exception:
                    pass

        # Phase-32 rev-3 — Catch DuplicateKeyError on the ship-from-pending
        # path too. If a pending row carries an order_id that already
        # exists on a Shipment (e.g. operator manually shipped the
        # order earlier via direct POST then accidentally re-tapped
        # Ship on the lingering pending row), surface a clean 409
        # with a helpful Gujarati/English message instead of a 500.
        try:
            await db.shipments.insert_one(ship_doc)
        except Exception as e:
            if "duplicate key" in str(e).lower() or "E11000" in str(e):
                _logger.warning(
                    "ship_pending_order — duplicate order_id %r for user %s; "
                    "rejecting with 409.",
                    ship_doc.get("order_id"),
                    current_user.get("id", "?")[:8],
                )
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Order ID '{ship_doc.get('order_id')}' is already "
                        "shipped. This pending row was likely created from "
                        "a duplicate paste — please cancel it from Orders, "
                        "or change the Order ID before shipping."
                    ),
                )
            raise

        # Phase H + Phase-31: User personal-sheet auto-sync (best-effort).
        # When a pending order is shipped, also append the full row to
        # the user's own connected Google Sheet (if they have one).
        # The user's sheet gets ONLY their data; the central Master Sheet
        # is the cross-tenant backup. `sync_create` reads the user's
        # `settings.sheet.sheet_id` + `auto_sync_create` flag and skips
        # cleanly when no sheet is connected.
        try:
            import user_sheet_sync as _uss
            await _uss.sync_create(db, current_user, ship_doc)
        except Exception:
            _logger.exception(
                "user-sheet auto-sync (ship-from-pending) failed (non-fatal)",
            )

        # Best-effort: custom-field values to user's personal sheet.
        custom_vals = order.get("custom_values") or {}
        await _write_custom_values_to_user_sheet_bg(current_user, custom_vals)

        # Mark order as shipped + link.
        await db.pending_orders.update_one(
            {"id": order_id, "user_id": current_user["id"]},
            {"$set": {
                "status":       "shipped",
                "processed_at": utcnow_iso(),
                "shipment_id":  ship_doc["id"],
                "tracking_id":  tracking_id,
            }},
        )
        # Charge wallet + bump plan counter.
        addr_text = " ".join(filter(None, [
            ship_doc.get("address_line1", ""),
            ship_doc.get("address_line2", ""),
            ship_doc.get("city", ""), ship_doc.get("state", ""),
            str(ship_doc.get("pincode", "")),
        ]))
        _s2 = await db.settings.find_one(
            {"user_id": current_user["id"]},
            {"_id": 0, "ai_cost_simple": 1, "ai_cost_medium": 1,
             "ai_cost_complex": 1},
        ) or {}
        ai_costs2 = {
            "simple":  float(_s2.get("ai_cost_simple", 0.5)),
            "medium":  float(_s2.get("ai_cost_medium", 1.0)),
            "complex": float(_s2.get("ai_cost_complex", 2.0)),
        }
        breakdown, _reason = await wallet_classify_and_cost(
            current_user, addr_text, plan_has_room, ai_costs=ai_costs2,
        )
        # Wallet may not have been pre-checked above (old-path) — make
        # sure the user can pay.
        bal = await wallet_balance(db, current_user["id"])
        if breakdown.total > bal + 1e-6:
            _logger.warning(
                f"Ship path: wallet underfunded for user "
                f"{current_user['id']}",
            )
        if plan_has_room:
            await bump_label_usage(db, current_user)
        await wallet_charge(db, current_user, ship_doc["id"], breakdown)

        # Two-Way Status Sync: bump the Master Sheet row from "Pending"
        # to "Ready to Ship" and stamp the tracking ID into Notice.
        # Phase F2.2 (2026-05-09): formerly wrote literal "Dispatched";
        # rewritten to "Ready to Ship" so the Sheet matches the app's
        # canonical user-facing label everywhere.
        sheet_row = order.get("sheet_row_num")
        if (
            sheet_row
            and sheet_update_row_status is not None
            and await user_has_feature(
                current_user, "sheet_two_way_status_sync",
            )
        ):
            try:
                sheet_update_row_status(
                    int(sheet_row),
                    "Ready to Ship",
                    extra_notice=(
                        f"Tracking: {tracking_id} · "
                        f"{courier.get('name', '')}"
                    ),
                )
                _logger.info(
                    f"Sheet status sync OK: row={sheet_row} "
                    f"Pending → Ready to Ship ({tracking_id})"
                )
            except Exception:
                _logger.exception(
                    "Sheet status sync failed on ship (non-fatal)",
                )

        ship_doc.pop("_id", None)
        return ship_doc
