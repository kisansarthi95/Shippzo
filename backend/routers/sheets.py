"""
Phase-5f (2026-05-17) — Sheets domain extracted from server.py.

Six endpoints, all preserved bit-for-bit:
  GET  /api/sheets/service-account   — share-this-email helper
  POST /api/sheets/preview           — connect / probe a user sheet
  GET  /api/sheets/orders            — list orders from a connected sheet
  GET  /api/sheets/sample-template   — download an ideal CSV template
  GET  /api/sheets/probe             — debug: SA → Master Sheet round-trip
  POST /api/sheets/sync-headers      — write headers into the user's sheet

Pattern: late-binding `init()` that imports shared helpers from
server.py *after* server.py has finished defining them — same trick
used by routers/admin.py, routers/field_configs.py, etc.

The numerous helper functions these endpoints depend on
(`fetch_sheet_csv`, `parse_csv_rows`, `_row_key`, `auto_guess_mapping`,
`_idx_to_col_letter`, `_sync_user_sheet_to_master_bg`, the
`sheet_*` callables, `_MAPPED_FIELD_HEADERS`, etc.) STAY in
server.py — extracting them is a separate, higher-risk refactor and
this phase is intentionally just an interface re-org.
"""

from __future__ import annotations

import csv
import io
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

log = logging.getLogger(__name__)

sheets_router = APIRouter(prefix="/api/sheets", tags=["sheets"])


class SyncHeadersPayload(BaseModel):
    # Optional overrides — callers may pass a bespoke header list,
    # otherwise we derive from the user's saved settings.
    headers: Optional[List[Dict[str, str]]] = None
    # When true, return the would-write list without actually
    # touching the sheet.
    dry_run: bool = False


# Mirror of `server.SheetPreviewRequest`. Defined LOCALLY so FastAPI
# can resolve the body type at route-build time without triggering a
# circular import on `from server import ...` during reload.
class SheetPreviewRequestLocal(BaseModel):
    url: str


def init() -> None:
    """Late-bind shared helpers from server.py — must be called once,
    after server.py has finished defining the helpers."""
    from server import (  # noqa: WPS433 — intentional late import
        db,
        get_current_user as _get_current_user,
        utcnow_iso,
        # models
        Settings,
        # parsers / writers
        parse_sheet_url,
        parse_csv_rows,
        fetch_sheet_csv,
        _row_key,
        _sync_user_sheet_to_master_bg,
        auto_guess_mapping,
        _idx_to_col_letter,
        _MAPPED_FIELD_HEADERS,
        # Service-Account-backed sheet wrapper callables (may be None
        # if gspread isn't installed)
        sheet_get_sa_email,
        sheet_read_user_sheet,
        sheet_probe_connection,
        sheet_sync_user_sheet_headers,
    )
    # Local alias so the legacy `logger.exception(...)` calls inside the
    # extracted handlers keep working without importing the module-
    # level `logger` (which would create a circular import during the
    # warm-reload code path).
    logger = log

    # ── 1. service-account email helper ─────────────────────────
    @sheets_router.get("/service-account")
    async def get_sheets_service_account_email(
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Return the Service Account email so the user can share their Sheet
        with it (Editor role). This keeps the user's Sheet PRIVATE — only the
        SA is granted access — instead of forcing them to set "Anyone with
        the link → Viewer" (the older public-CSV path).
        """
        email = sheet_get_sa_email() if sheet_get_sa_email else ""
        return {
            "email": email,
            "instructions": (
                "Open your Google Sheet → Share → paste this email → choose "
                "'Editor' → Send. Then come back here and connect."
            ),
        }

    # ── 2. preview / connect probe ──────────────────────────────
    @sheets_router.post("/preview")
    async def sheets_preview(payload: SheetPreviewRequestLocal):
        parsed = parse_sheet_url(payload.url)

        # Phase-5: Service-Account-first read. Only fall back to the legacy
        # public-CSV path if the SA can't access the sheet AND the user has
        # made it public anyway.
        if sheet_read_user_sheet is not None:
            sa_resp = sheet_read_user_sheet(parsed["sheet_id"], parsed["gid"] or "0")
            if sa_resp.get("ok"):
                headers = sa_resp.get("headers", [])
                rows = sa_resp.get("rows", [])
                guess = auto_guess_mapping(headers)
                return {
                    "sheet_id":      parsed["sheet_id"],
                    "gid":           parsed["gid"],
                    "headers":       headers,
                    "sample_rows":   rows[:5],
                    "total_rows":    len(rows),
                    "auto_mapping":  guess,
                    "access_method": "service_account",
                }
            err = (sa_resp.get("error") or "").strip()
            # On "not shared" / "not found" we still try the legacy CSV path
            # so users who have public sheets keep working with no migration.
            if err in ("SHEET_NOT_SHARED", "SHEET_NOT_FOUND"):
                try:
                    csv_text = await fetch_sheet_csv(parsed["sheet_id"], parsed["gid"])
                    data = parse_csv_rows(csv_text)
                    guess = auto_guess_mapping(data["headers"])
                    return {
                        "sheet_id":      parsed["sheet_id"],
                        "gid":           parsed["gid"],
                        "headers":       data["headers"],
                        "sample_rows":   data["rows"][:5],
                        "total_rows":    len(data["rows"]),
                        "auto_mapping":  guess,
                        "access_method": "public_csv",
                    }
                except HTTPException:
                    # Neither SA nor public works — surface the SA-share guide.
                    sa_email = sheet_get_sa_email() if sheet_get_sa_email else ""
                    raise HTTPException(
                        status_code=403,
                        detail=(
                            "We can't open that sheet. Either:\n"
                            f"  1. Share it with {sa_email or '<our service account>'} "
                            "(Editor) — recommended; keeps it private, OR\n"
                            "  2. Open Share → 'Anyone with the link → Viewer'.\n"
                            "Then try again."
                        ),
                    )
            # Some other unexpected SA error → bubble through CSV path.
            try:
                csv_text = await fetch_sheet_csv(parsed["sheet_id"], parsed["gid"])
                data = parse_csv_rows(csv_text)
                guess = auto_guess_mapping(data["headers"])
                return {
                    "sheet_id":      parsed["sheet_id"],
                    "gid":           parsed["gid"],
                    "headers":       data["headers"],
                    "sample_rows":   data["rows"][:5],
                    "total_rows":    len(data["rows"]),
                    "auto_mapping":  guess,
                    "access_method": "public_csv",
                }
            except HTTPException:
                raise HTTPException(
                    status_code=400,
                    detail=f"Sheet read failed: {err or 'unknown error'}",
                )

        # Hard fallback (sheet_writer module unavailable) — legacy CSV path only.
        csv_text = await fetch_sheet_csv(parsed["sheet_id"], parsed["gid"])
        data = parse_csv_rows(csv_text)
        guess = auto_guess_mapping(data["headers"])
        return {
            "sheet_id":      parsed["sheet_id"],
            "gid":           parsed["gid"],
            "headers":       data["headers"],
            "sample_rows":   data["rows"][:5],
            "total_rows":    len(data["rows"]),
            "auto_mapping":  guess,
            "access_method": "public_csv",
        }

    # ── 3. live orders read from user's sheet ────────────────────
    @sheets_router.get("/orders")
    async def sheets_orders(
        background_tasks: BackgroundTasks,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        doc = await db.settings.find_one({"user_id": current_user["id"]}, {"_id": 0})
        if not doc:
            raise HTTPException(status_code=400, detail="Settings not configured")
        s = Settings(**doc)
        cfg = s.sheet
        if not cfg.sheet_id:
            raise HTTPException(status_code=400, detail="Google Sheet not connected")

        # Phase-5: Service-Account-first read. If the user has shared their
        # Sheet with our SA the sheet stays PRIVATE — no public-link required.
        # Fall back to public-CSV path only if SA can't access AND CSV works.
        data: Dict[str, Any] = {"headers": [], "rows": []}
        access_method = "public_csv"
        if sheet_read_user_sheet is not None:
            sa_resp = sheet_read_user_sheet(cfg.sheet_id, cfg.gid or "0")
            if sa_resp.get("ok"):
                data = {"headers": sa_resp["headers"], "rows": sa_resp["rows"]}
                access_method = "service_account"
            elif sa_resp.get("error") in ("SHEET_NOT_SHARED", "SHEET_NOT_FOUND"):
                try:
                    csv_text = await fetch_sheet_csv(cfg.sheet_id, cfg.gid or "0")
                    data = parse_csv_rows(csv_text)
                except HTTPException:
                    sa_email = sheet_get_sa_email() if sheet_get_sa_email else ""
                    raise HTTPException(
                        status_code=403,
                        detail=(
                            "Can't read your Google Sheet. Please share it with "
                            f"{sa_email or '<our service account>'} (Editor) — "
                            "this keeps your Sheet private."
                        ),
                    )
            else:
                # Unknown SA error → try public path as last resort.
                try:
                    csv_text = await fetch_sheet_csv(cfg.sheet_id, cfg.gid or "0")
                    data = parse_csv_rows(csv_text)
                except HTTPException:
                    raise HTTPException(status_code=400, detail="Sheet read failed")
        else:
            csv_text = await fetch_sheet_csv(cfg.sheet_id, cfg.gid or "0")
            data = parse_csv_rows(csv_text)

        # Detect header changes — but be smart about it.
        headers_changed = False
        fresh_headers = list(data.get("headers") or [])
        saved_headers = list(cfg.headers or [])
        if fresh_headers and fresh_headers != saved_headers:
            if not saved_headers:
                try:
                    await db.settings.update_one(
                        {"user_id": current_user["id"]},
                        {"$set": {"sheet.headers": fresh_headers}},
                    )
                except Exception:
                    logger.exception("failed to bootstrap sheet.headers")
            else:
                mapped_cols = {
                    (cfg.column_mapping or {}).get(k) for k in (cfg.column_mapping or {})
                }
                mapped_cols.discard("")
                mapped_cols.discard(None)
                lost = [c for c in mapped_cols if c not in fresh_headers]
                if lost:
                    headers_changed = True
                else:
                    try:
                        await db.settings.update_one(
                            {"user_id": current_user["id"]},
                            {"$set": {"sheet.headers": fresh_headers}},
                        )
                    except Exception:
                        logger.exception("failed to refresh sheet.headers (additive)")

        mapping = cfg.column_mapping or {}

        SHEET_KEY_ALIASES = {
            "phone":                "customer_phone",
            "customer_phone":       "phone",
            "item":                 "items",
            "items":                "item",
            "timestamp":            "created_at_override",
            "created_at_override":  "timestamp",
        }

        def mapped_field(row: Dict[str, str], key: str) -> str:
            col = mapping.get(key)
            if not col:
                alias = SHEET_KEY_ALIASES.get(key)
                if alias:
                    col = mapping.get(alias)
            if not col:
                return ""
            return row.get(col, "")

        imported_keys: set = set()
        tracking_ids:  set = set()
        master_ids:    set = set()
        order_ids:     set = set()
        existing = await db.shipments.find(
            {"user_id": current_user["id"]},
            {
                "_id":             0,
                "sheet_row_key":   1,
                "tracking_id":     1,
                "master_order_id": 1,
                "order_id":        1,
            },
        ).to_list(20000)
        for e in existing:
            if e.get("sheet_row_key"):
                imported_keys.add(e["sheet_row_key"])
            if e.get("tracking_id"):
                tracking_ids.add(str(e["tracking_id"]).strip())
            if e.get("master_order_id"):
                master_ids.add(str(e["master_order_id"]).strip())
            if e.get("order_id"):
                order_ids.add(str(e["order_id"]).strip())

        def mapped(row: Dict[str, str], key: str) -> str:
            return mapped_field(row, key)

        orders = []
        for idx, row in enumerate(data["rows"]):
            row_key = _row_key(row, mapping, idx)
            sheet_tracking = (mapped(row, "tracking_id") or "").strip()
            sheet_master   = (mapped(row, "master_order_id") or "").strip()
            sheet_order_id = (mapped(row, "order_id") or "").strip()
            is_shipped = (
                row_key in imported_keys
                or (sheet_tracking and sheet_tracking in tracking_ids)
                or (sheet_master   and sheet_master   in master_ids)
                or (sheet_order_id and sheet_order_id in order_ids)
            )
            orders.append({
                "row_key":         row_key,
                "row_index":       idx + 2,
                "order_id":        mapped(row, "order_id"),
                "customer_name":   mapped(row, "customer_name"),
                "phone":           mapped(row, "phone"),
                "address":         mapped(row, "address"),
                "city":            mapped(row, "city"),
                "state":           mapped(row, "state"),
                "pincode":         mapped(row, "pincode"),
                "item":            mapped(row, "item"),
                "amount":          mapped(row, "amount"),
                "timestamp":       mapped(row, "timestamp"),
                "status":          mapped(row, "status"),
                "payment_mode":    mapped(row, "payment_mode"),
                "weight":          mapped(row, "weight"),
                "items_full":      mapped(row, "items"),
                "alt_phone":       mapped(row, "customer_alt_phone"),
                "email":           mapped(row, "customer_email"),
                "gstin":           mapped(row, "customer_gstin"),
                "category":        mapped(row, "category"),
                "notes":           mapped(row, "notes"),
                "token_amount":    mapped(row, "token_amount"),
                "box_dimensions":  mapped(row, "box_dimensions"),
                "courier_hint":    mapped(row, "courier_hint"),
                "already_shipped": is_shipped,
                "raw":             row,
            })

        # Auto-backup any new user-sheet rows to Master Sheet (background)
        user_name_for_log = (
            current_user.get("full_name")
            or current_user.get("name")
            or (current_user.get("email", "").split("@")[0])
        )
        background_tasks.add_task(
            _sync_user_sheet_to_master_bg,
            current_user["id"],
            user_name_for_log or "",
            list(data.get("rows") or []),
            dict(mapping),
        )

        # Cache the unshipped-sheet-order count for the Home dashboard.
        unshipped_count = sum(1 for o in orders if not o.get("already_shipped"))
        try:
            await db.settings.update_one(
                {"user_id": current_user["id"]},
                {"$set": {
                    "sheet.unshipped_count_cached": int(unshipped_count),
                    "sheet.unshipped_count_at": utcnow_iso(),
                }},
            )
        except Exception:
            logger.exception("failed to cache sheet.unshipped_count")

        return {
            "headers":         data["headers"],
            "headers_changed": headers_changed,
            "orders":          orders,
            "total":           len(orders),
            "access_method":   access_method,
        }

    # ── 4. CSV sample template download ──────────────────────────
    @sheets_router.get("/sample-template", response_class=PlainTextResponse)
    async def sheets_sample_template():
        """Return a CSV with ideal column layout + example rows for users
        to import into Google Sheets."""
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow([
            "Timestamp", "Order ID", "Name", "Phone", "Address",
            "City", "State", "Pincode", "Item", "Amount", "Payment Mode",
        ])
        samples = [
            ["2026-01-15 10:30:00", "ORD-1001", "Ramesh Patel", "9876543210",
             "12, Navrangpura Main Road, Ellisbridge",
             "Ahmedabad", "Gujarat", "380006",
             "Cotton Kurta Large - Blue", "850", "COD"],
            ["2026-01-15 11:12:45", "ORD-1002", "Priya Shah", "9823456710",
             "B-204, Sunrise Apts, Satellite Road",
             "Ahmedabad", "Gujarat", "380015",
             "Silk Saree Red; Matching Blouse", "2499", "Prepaid"],
            ["2026-01-15 14:02:10", "ORD-1003", "Rahul Mehta", "9812345678",
             "Shop 7, Main Bazaar, Near Bus Stand",
             "Rajkot", "Gujarat", "360001",
             "Men Jeans 32 - Dark Blue", "1299", "COD"],
            ["2026-01-15 16:47:22", "ORD-1004", "Anjali Desai", "9801234567",
             "45, Gulab Nagar, Adajan",
             "Surat", "Gujarat", "395009",
             "Kids T-shirt Small; Shorts", "699", "Prepaid"],
        ]
        for row in samples:
            w.writerow(row)
        return PlainTextResponse(
            buf.getvalue(),
            media_type="text/csv",
            headers={
                "Content-Disposition": 'attachment; filename="courier_sheet_template.csv"'
            },
        )

    # ── 5. SA → Master Sheet probe (debug) ───────────────────────
    @sheets_router.get("/probe")
    async def sheets_probe():
        """Quick debug endpoint — verifies Service Account can read the Master Sheet."""
        if sheet_probe_connection is None:
            return {"ok": False, "error": "gspread not installed"}
        return sheet_probe_connection()

    # ── 6. write mapped headers into the user's sheet ────────────
    @sheets_router.post("/sync-headers")
    async def sync_sheet_headers(
        payload: SyncHeadersPayload,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Write the user's mapped + custom-field header names into row 1
        of their connected Google Sheet. Only fills blank cells (existing
        non-blank headers are preserved)."""
        if sheet_sync_user_sheet_headers is None:
            raise HTTPException(
                status_code=503, detail="Sheets integration not configured"
            )

        doc = await db.settings.find_one({"user_id": current_user["id"]}, {"_id": 0})
        if not doc:
            raise HTTPException(status_code=400, detail="Settings not configured")
        s = Settings(**doc)
        cfg = s.sheet
        if not cfg.sheet_id:
            raise HTTPException(status_code=400, detail="Google Sheet not connected")

        items: List[tuple] = []
        if payload.headers:
            for item in payload.headers:
                col = (item.get("column") or "").strip().upper()
                name = (item.get("name") or "").strip()
                if col and name:
                    items.append((col, name))
        else:
            mapping = cfg.column_mapping or {}
            sheet_headers = cfg.headers or []
            for field_key, col_name in mapping.items():
                if not col_name:
                    continue
                try:
                    idx = sheet_headers.index(col_name)
                except ValueError:
                    continue
                letter = _idx_to_col_letter(idx)
                items.append(
                    (letter, _MAPPED_FIELD_HEADERS.get(field_key, field_key.title()))
                )

            # Custom fields — each has an explicit column letter.
            custom_fields = (
                await db.user_custom_fields.find(
                    {"user_id": current_user["id"], "active": {"$ne": False}},
                    {"_id": 0},
                ).sort("sort_order", 1).to_list(100)
            )
            for cf in custom_fields:
                col = (cf.get("column_letter") or "").strip().upper()
                name = (cf.get("name") or "").strip()
                if col and name:
                    items.append((col, name))

        if payload.dry_run:
            return {"ok": True, "dry_run": True, "would_write": items}

        try:
            result = sheet_sync_user_sheet_headers(
                cfg.sheet_id, cfg.gid or "0",
                headers_to_write=items,
            )
        except Exception as e:
            logger.exception("sync_sheet_headers failed")
            raise HTTPException(status_code=502, detail=f"Header sync failed: {e}")

        return {
            "ok":            True,
            "written_count": len(result.get("written", [])),
            "skipped_count": len(result.get("skipped", [])),
            "written":       [{"column": c, "name": n} for (c, n) in result.get("written", [])],
            "skipped": [
                {"column": c, "name": n, "existing": existing}
                for (c, n, existing) in result.get("skipped", [])
            ],
        }

    log.info("[sheets] router endpoints registered (Phase-5f)")
