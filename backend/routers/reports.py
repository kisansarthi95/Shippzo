"""
Courier Billing Report — Phase 2.5
══════════════════════════════════════════════════════════════════
Aggregates each user's own shipments by courier_partner over a
chosen period and emits two surfaces:

  1. JSON for the in-app Reports screen (KPI cards + detail table).
  2. Multi-sheet .xlsx for one-tap download / share with the courier
     partner directly from WhatsApp / email.

Data source: the `shipments` collection. Phase 2B introduced the per-
shipment fields (rate_applied, rate_basis, package_type, category)
that this report relies on. Older rows have rate_applied=0 — they're
counted in volume but contribute ₹0 to charges; the UI surfaces a
`rows_without_rate` KPI so the user can fix them up manually.
"""
from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse


reports_router = APIRouter(prefix="/api", tags=["reports"])


# ──────────────────────────────────────────────────────────────────
# Helpers (pure-fn — no DB / auth dependency)
# ──────────────────────────────────────────────────────────────────


def _resolve_period(
    range_key: str,
    from_iso: Optional[str],
    to_iso: Optional[str],
) -> Dict[str, Any]:
    """Map UI range chips to (from, to) datetimes in UTC. The UI may
    also send `from` / `to` for full custom ranges — those win when
    range_key=='custom'."""
    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    def fmt_label(start: datetime, end: datetime) -> str:
        if start.year == end.year and start.month == end.month:
            return start.strftime("%B %Y")
        return f"{start.strftime('%-d %b')} – {end.strftime('%-d %b %Y')}"

    if range_key == "custom" and from_iso and to_iso:
        try:
            start = datetime.fromisoformat(from_iso.replace("Z", "+00:00"))
            end   = datetime.fromisoformat(to_iso.replace("Z", "+00:00"))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, f"Invalid custom range: {exc}") from exc
    elif range_key == "this_week":
        start = today - timedelta(days=today.weekday())
        end   = today + timedelta(days=1)
    elif range_key == "last_week":
        end   = today - timedelta(days=today.weekday())
        start = end - timedelta(days=7)
    elif range_key == "last_month":
        first_this = today.replace(day=1)
        end        = first_this
        start      = (first_this - timedelta(days=1)).replace(day=1)
    elif range_key == "last_30":
        start = today - timedelta(days=30)
        end   = today + timedelta(days=1)
    else:  # default: this_month
        start = today.replace(day=1)
        end   = today + timedelta(days=1)

    return {
        "from":  start.isoformat(),
        "to":    end.isoformat(),
        "label": fmt_label(start, end - timedelta(seconds=1)),
    }


def _bucket_payment(pm: Optional[str]) -> str:
    norm = (pm or "").strip().upper()
    if norm == "COD":
        return "COD"
    if norm in ("PREPAID", "PAID"):
        return "PREPAID"
    return "Other"


# ──────────────────────────────────────────────────────────────────
# init() — server.py calls this on startup so we can close over the
# late-bound `db` handle and the `get_current_user` dependency.
# ──────────────────────────────────────────────────────────────────


def init() -> None:
    """Late-bind helpers from server.py and register the routes inside
    the closure so FastAPI sees real dependencies (not None)."""
    from server import db, get_current_user  # noqa: WPS433

    async def _resolve_token_user(token: str) -> Optional[Dict[str, Any]]:
        """Decode the bearer token via auth.py and return the user dict.
        Used as a fallback for the Excel route which the browser hits
        without an Authorization header."""
        try:
            from auth import decode_token as _decode_token  # type: ignore[attr-defined]
            payload = _decode_token(token)
            uid = payload.get("sub") or payload.get("user_id") or payload.get("id")
            if not uid:
                return None
            return await db.users.find_one({"id": uid}, {"_id": 0})
        except Exception:
            return None

    async def _aggregate(
        user_id: str,
        range_key: str,
        courier_id: Optional[str],
        from_iso: Optional[str],
        to_iso: Optional[str],
    ) -> Dict[str, Any]:
        period = _resolve_period(range_key, from_iso, to_iso)
        match: Dict[str, Any] = {
            "user_id":    user_id,
            "deleted_at": {"$exists": False},
            "created_at": {"$gte": period["from"], "$lt": period["to"]},
        }
        if courier_id and courier_id != "all":
            match["courier_id"] = courier_id

        cursor = db.shipments.find(match, {
            "_id": 0, "id": 1, "courier_id": 1, "courier_name": 1,
            "tracking_id": 1, "order_id": 1, "customer_name": 1,
            "customer_phone": 1, "city": 1, "state": 1, "weight": 1,
            "amount": 1, "payment_mode": 1, "status": 1, "created_at": 1,
            "rate_applied": 1, "rate_basis": 1, "package_type": 1,
            "category": 1, "variant_name": 1,
        }).sort("created_at", 1)
        rows: List[Dict[str, Any]] = await cursor.to_list(5000)

        by_courier: Dict[str, Dict[str, Any]] = {}
        rows_without_rate = 0
        grand_charges = 0.0
        grand_count = 0

        for r in rows:
            cid = r.get("courier_id") or ""
            cname = r.get("courier_name") or "Unknown"
            bucket_id = cid or cname
            bucket = by_courier.setdefault(bucket_id, {
                "courier_id":      cid,
                "courier_name":    cname,
                "total_shipments": 0,
                "total_charges":   0.0,
                "cod":            {"count": 0, "amount": 0.0},
                "prepaid":        {"count": 0, "amount": 0.0},
                "other":          {"count": 0, "amount": 0.0},
                "by_package_type": {},
                "by_state":        {},
                "shipments":       [],
            })

            rate = float(r.get("rate_applied") or 0)
            if rate <= 0:
                rows_without_rate += 1
            bucket["total_shipments"] += 1
            bucket["total_charges"]   += rate
            grand_count   += 1
            grand_charges += rate

            bucket_key = _bucket_payment(r.get("payment_mode"))
            target = bucket["cod"] if bucket_key == "COD" else (
                     bucket["prepaid"] if bucket_key == "PREPAID" else bucket["other"])
            target["count"]  += 1
            target["amount"] += rate

            pt = (r.get("package_type") or "—").strip() or "—"
            pt_b = bucket["by_package_type"].setdefault(pt, {"count": 0, "amount": 0.0})
            pt_b["count"]  += 1
            pt_b["amount"] += rate

            st = (r.get("state") or "—").strip() or "—"
            st_b = bucket["by_state"].setdefault(st, {"count": 0})
            st_b["count"] += 1

            bucket["shipments"].append({
                "id":            r.get("id"),
                "tracking_id":   r.get("tracking_id"),
                "order_id":      r.get("order_id"),
                "date":          (r.get("created_at") or "")[:10],
                "customer_name": r.get("customer_name"),
                "city":          r.get("city"),
                "state":         r.get("state"),
                "weight":        r.get("weight"),
                "rate":          rate,
                "payment_mode":  bucket_key,
                "status":        r.get("status"),
                "package_type":  r.get("package_type"),
                "variant_name":  r.get("variant_name"),
            })

        couriers_out: List[Dict[str, Any]] = []
        for b in by_courier.values():
            b["by_package_type"] = sorted(
                ({"type": k, **v} for k, v in b["by_package_type"].items()),
                key=lambda x: x["count"], reverse=True,
            )
            b["by_state"] = sorted(
                ({"state": k, **v} for k, v in b["by_state"].items()),
                key=lambda x: x["count"], reverse=True,
            )
            couriers_out.append(b)
        couriers_out.sort(key=lambda x: x["total_charges"], reverse=True)

        return {
            "period":   period,
            "couriers": couriers_out,
            "grand_total": {
                "shipments": grand_count,
                "charges":   grand_charges,
            },
            "rows_without_rate": rows_without_rate,
        }

    @reports_router.get("/me/reports/courier-billing")
    async def courier_billing_report(
        range_key: str = Query("this_month", alias="range"),
        courier_id: Optional[str] = Query(None),
        from_iso: Optional[str] = Query(None, alias="from"),
        to_iso: Optional[str] = Query(None, alias="to"),
        current_user: Dict[str, Any] = Depends(get_current_user),
    ):
        return await _aggregate(
            current_user["id"], range_key, courier_id, from_iso, to_iso,
        )

    @reports_router.get("/me/reports/courier-billing/excel")
    async def courier_billing_report_excel(
        request: "Request",  # noqa: F821 — string-typed for forward import
        range_key: str = Query("this_month", alias="range"),
        courier_id: Optional[str] = Query(None),
        from_iso: Optional[str] = Query(None, alias="from"),
        to_iso: Optional[str] = Query(None, alias="to"),
        token: Optional[str] = Query(None),
    ):
        # Prefer the standard Authorization header, fall back to the
        # ?token= query carrier so that browser-initiated downloads
        # (which can't set custom headers) still work.
        current_user: Optional[Dict[str, Any]] = None
        bearer = request.headers.get("authorization") or ""
        if bearer.lower().startswith("bearer "):
            current_user = await _resolve_token_user(bearer.split(" ", 1)[1])
        if current_user is None and token:
            current_user = await _resolve_token_user(token)
        if current_user is None:
            raise HTTPException(401, "Auth required")
        payload = await _aggregate(
            current_user["id"], range_key, courier_id, from_iso, to_iso,
        )
        return _build_excel(payload, current_user)


def _build_excel(payload: Dict[str, Any], current_user: Dict[str, Any]) -> StreamingResponse:
    """Render the multi-sheet workbook from an aggregated payload.
    Sheet 1 is a roll-up summary; sheets 2+ are per-courier bills."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    HEADER_FILL = PatternFill("solid", fgColor="1F4FBF")
    HEADER_FONT = Font(bold=True, color="FFFFFF", size=11)
    TITLE_FONT  = Font(bold=True, size=14, color="1F4FBF")
    LABEL_FONT  = Font(bold=True, size=10)
    CENTER      = Alignment(horizontal="center", vertical="center")

    business_name = (
        current_user.get("shop_name") or current_user.get("name") or "—"
    )

    # Summary sheet --------------------------------------------------
    summary = wb.active
    summary.title = "Summary"
    summary["A1"] = f"Courier Billing — {payload['period']['label']}"
    summary["A1"].font = TITLE_FONT
    summary.merge_cells("A1:E1")
    summary["A2"] = f"Business: {business_name}"
    summary["A2"].font = LABEL_FONT
    summary.merge_cells("A2:E2")
    summary["A3"] = (
        f"Total shipments: {payload['grand_total']['shipments']}    "
        f"Total charges: ₹{payload['grand_total']['charges']:.0f}"
    )
    summary["A3"].font = LABEL_FONT
    summary.merge_cells("A3:E3")
    if payload["rows_without_rate"]:
        summary["A4"] = (
            f"⚠️ {payload['rows_without_rate']} shipment(s) have no rate set "
            "— they count in volume but contribute ₹0 to charges."
        )
        summary.merge_cells("A4:E4")
        start_row = 6
    else:
        start_row = 5

    headers = ["Courier", "Shipments", "COD ₹", "Prepaid ₹", "Total ₹"]
    for col, h in enumerate(headers, 1):
        cell = summary.cell(row=start_row, column=col, value=h)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = CENTER
    for i, c in enumerate(payload["couriers"], 1):
        r = start_row + i
        summary.cell(row=r, column=1, value=c["courier_name"])
        summary.cell(row=r, column=2, value=c["total_shipments"])
        summary.cell(row=r, column=3, value=round(c["cod"]["amount"]))
        summary.cell(row=r, column=4, value=round(c["prepaid"]["amount"]))
        tot = summary.cell(row=r, column=5, value=round(c["total_charges"]))
        tot.font = Font(bold=True)
    g = start_row + len(payload["couriers"]) + 1
    summary.cell(row=g, column=1, value="GRAND TOTAL").font = Font(bold=True)
    summary.cell(row=g, column=2, value=payload["grand_total"]["shipments"]).font = Font(bold=True)
    summary.cell(row=g, column=5, value=round(payload["grand_total"]["charges"])).font = Font(bold=True)
    for col in range(1, 6):
        summary.column_dimensions[get_column_letter(col)].width = 18

    # Per-courier sheets --------------------------------------------
    for c in payload["couriers"]:
        safe = "".join(ch for ch in (c["courier_name"] or "Courier") if ch not in r'[]:*?/\\')[:28]
        ws = wb.create_sheet(title=safe or "Courier")
        ws["A1"] = f"Courier Bill — {c['courier_name']}"
        ws["A1"].font = TITLE_FONT
        ws.merge_cells("A1:H1")
        ws["A2"] = (
            f"Period: {payload['period']['label']}    "
            f"Shipments: {c['total_shipments']}    "
            f"Total: ₹{c['total_charges']:.0f}"
        )
        ws["A2"].font = LABEL_FONT
        ws.merge_cells("A2:H2")
        ws["A3"] = (
            f"COD: {c['cod']['count']} (₹{c['cod']['amount']:.0f})   "
            f"Prepaid: {c['prepaid']['count']} (₹{c['prepaid']['amount']:.0f})"
        )
        ws.merge_cells("A3:H3")

        ship_headers = [
            "Date", "Tracking ID", "Order ID", "Customer", "City", "State",
            "Weight", "Pkg Type", "Pay Mode", "Rate ₹",
        ]
        for col, h in enumerate(ship_headers, 1):
            cell = ws.cell(row=5, column=col, value=h)
            cell.fill = HEADER_FILL
            cell.font = HEADER_FONT
            cell.alignment = CENTER

        for i, s in enumerate(c["shipments"], 1):
            r = 5 + i
            ws.cell(row=r, column=1,  value=s.get("date"))
            ws.cell(row=r, column=2,  value=s.get("tracking_id") or "")
            ws.cell(row=r, column=3,  value=s.get("order_id") or "")
            ws.cell(row=r, column=4,  value=s.get("customer_name") or "")
            ws.cell(row=r, column=5,  value=s.get("city") or "")
            ws.cell(row=r, column=6,  value=s.get("state") or "")
            ws.cell(row=r, column=7,  value=s.get("weight") or "")
            ws.cell(row=r, column=8,  value=s.get("package_type") or "")
            ws.cell(row=r, column=9,  value=s.get("payment_mode") or "")
            ws.cell(row=r, column=10, value=round(s.get("rate") or 0))

        last = 5 + len(c["shipments"]) + 1
        ws.cell(row=last, column=9,  value="Total").font = Font(bold=True)
        ws.cell(row=last, column=10, value=round(c["total_charges"])).font = Font(bold=True)

        widths = [11, 18, 12, 22, 14, 14, 10, 14, 11, 11]
        for col, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(col)].width = w

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    # Latin-1 only allowed in Content-Disposition — strip Unicode dashes
    # and any other non-ASCII so the period label is safe in the header.
    raw = payload["period"]["label"].replace("–", "-").replace("—", "-")
    fname_period = "".join(ch if ord(ch) < 128 else "_" for ch in raw).replace(" ", "_")
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="CourierBill_{fname_period}.xlsx"',
        },
    )
