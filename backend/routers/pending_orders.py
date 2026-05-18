"""
Pending Orders CRUD — Phase-5b incremental refactor.

Extracts the 5 read/edit/delete endpoints for the "Smart Paste queue"
(Mongo `pending_orders` collection) out of server.py. The HEAVIER
sister endpoint `POST /orders/pending/{id}/ship` STAYS in server.py —
it shares too much state with shipment creation (wallet charge,
sheet writes, custom-fields, two-way sheet status sync). It will be
extracted alongside POST /shipments in Phase 5c.

Endpoints relocated (all under /api):

  Pending Orders
  --------------
  GET    /orders/pending              list_pending_orders
  GET    /orders/pending/{id}         get_pending_order
  PUT    /orders/pending/{id}         update_pending_order
  DELETE /orders/pending/{id}         delete_pending_order
  GET    /orders/pending-count        pending_orders_count

Pattern: late-binding `init()` — same as routers/shipment_ops.py.

Note on /orders/pending-count: this is the Home-dashboard badge count
that combines the Smart-Paste queue (Mongo) with the user's Google
Sheet unshipped-rows cache (read from settings.sheet). It MUST stay
in sync with the cached `unshipped_count_cached` set by /sheets/orders.
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException


pending_orders_router = APIRouter(prefix="/api", tags=["pending-orders"])


def init() -> None:
    """Register routes after server.py finishes initialising."""
    import logging
    _logger = logging.getLogger("routers.pending_orders")
    from server import (  # noqa: WPS433 — intentional late import
        db,
        get_current_user as _get_current_user,
        PendingOrder,
        sheet_mark_row_deleted,
        user_has_feature,
    )

    # =================  List + single read  ===========================

    @pending_orders_router.get(
        "/orders/pending", response_model=List[PendingOrder]
    )
    async def list_pending_orders(
        source: Optional[str] = None,
        status: Optional[str] = None,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        q: Dict[str, Any] = {"user_id": current_user["id"]}
        if source:
            q["source"] = source
        if status:
            q["status"] = status
        else:
            # Default to "pending" — shipped/cancelled rows are visible
            # via the explicit status filter on the Orders tab.
            q["status"] = "pending"
        cursor = db.pending_orders.find(q, {"_id": 0}).sort("created_at", -1)
        return await cursor.to_list(length=500)

    @pending_orders_router.get(
        "/orders/pending/{order_id}", response_model=PendingOrder
    )
    async def get_pending_order(
        order_id: str,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        doc = await db.pending_orders.find_one(
            {"id": order_id, "user_id": current_user["id"]}, {"_id": 0},
        )
        if not doc:
            raise HTTPException(status_code=404, detail="Order not found")
        return doc

    # =================  Update  =======================================

    @pending_orders_router.put(
        "/orders/pending/{order_id}", response_model=PendingOrder
    )
    async def update_pending_order(
        order_id: str,
        payload: Dict[str, Any],
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        # Phase F3.9.2 — Plan-gated: front-end already hides the Edit
        # icon when the flag is off, but power users / API consumers
        # could still hit this endpoint directly. 403 keeps them out.
        if not await user_has_feature(current_user, "pending_orders_edit"):
            raise HTTPException(
                status_code=403,
                detail="Your plan doesn't include editing pending orders.",
            )
        # Phase-33 — Terminal-state lock for pending orders.
        from lib.terminal_states import (
            is_terminal_pending_status, TERMINAL_LOCK_DETAIL,
        )
        existing = await db.pending_orders.find_one(
            {"id": order_id, "user_id": current_user["id"]},
            {"_id": 0, "status": 1},
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Order not found")
        if is_terminal_pending_status(existing.get("status")):
            raise HTTPException(
                status_code=423,
                detail=TERMINAL_LOCK_DETAIL,
            )
        # Allow partial field updates (user edits before shipping).
        # Whitelist against PendingOrder fields so the client can't
        # tamper with id/created_at/source.
        allowed = {
            k for k in PendingOrder.model_fields
            if k not in ("id", "created_at", "source")
        }
        upd = {k: v for k, v in payload.items() if k in allowed}
        if not upd:
            raise HTTPException(status_code=400, detail="No valid fields to update")
        res = await db.pending_orders.update_one(
            {"id": order_id, "user_id": current_user["id"]}, {"$set": upd},
        )
        if res.matched_count == 0:
            raise HTTPException(status_code=404, detail="Order not found")
        doc = await db.pending_orders.find_one(
            {"id": order_id, "user_id": current_user["id"]}, {"_id": 0},
        )
        return doc

    # =================  Cancel (was: soft delete)  ====================

    @pending_orders_router.delete("/orders/pending/{order_id}")
    async def delete_pending_order(
        order_id: str,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Phase-33 — HARD DELETE REMOVED.

        Legacy `DELETE /orders/pending/{id}` is retained as an
        idempotent CANCEL action so older clients keep working. The
        record is NEVER removed from Mongo. Instead the pending order
        flips to `status="cancelled"` with an audit stamp, after
        which every other endpoint (update, ship-now) refuses to
        operate on it.

        Master-Sheet tombstone is still emitted when the user's plan
        includes `sheet_soft_delete_tombstone` so the source-sheet
        row reflects the cancellation (visible audit trail). Sheet
        failures are logged but never block the local cancel write.
        """
        from lib.terminal_states import is_terminal_pending_status
        from server import utcnow_iso

        if not await user_has_feature(current_user, "pending_orders_delete"):
            raise HTTPException(
                status_code=403,
                detail="Your plan doesn't include cancelling pending orders.",
            )
        doc = await db.pending_orders.find_one(
            {"id": order_id, "user_id": current_user["id"]}, {"_id": 0},
        )
        if not doc:
            raise HTTPException(status_code=404, detail="Order not found")

        # Already cancelled → idempotent ack.
        if is_terminal_pending_status(doc.get("status")):
            return {
                "ok": True,
                "already_cancelled": True,
                "status": doc.get("status"),
            }

        sheet_result: Dict[str, Any] = {"attempted": False}
        row_num = doc.get("sheet_row_num")
        if (
            row_num
            and sheet_mark_row_deleted is not None
            and await user_has_feature(
                current_user, "sheet_soft_delete_tombstone",
            )
        ):
            sheet_result["attempted"] = True
            try:
                reason = (
                    f"pending order "
                    f"{doc.get('order_id_hint') or order_id[:8]} "
                    f"({(doc.get('customer_name') or '')[:40]}) "
                    "cancelled in app"
                )
                sheet_result.update(
                    sheet_mark_row_deleted(int(row_num), reason=reason),
                )
            except Exception as e:
                _logger.exception("Cancel sheet mark failed (pending)")
                sheet_result["ok"] = False
                sheet_result["error"] = str(e)

        # Flip status — DO NOT remove the document.
        await db.pending_orders.update_one(
            {"id": order_id, "user_id": current_user["id"]},
            {"$set": {
                "status": "cancelled",
                "cancelled_at": utcnow_iso(),
                "cancel_reason": "user_action",
            }},
        )
        return {"ok": True, "sheet": sheet_result, "status": "cancelled"}

    # =================  Combined badge count  =========================

    @pending_orders_router.get("/orders/pending-count")
    async def pending_orders_count(
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Combined pending-orders badge count for the Home dashboard.

        Adds the Smart-Paste queue (Mongo `pending_orders`) to the
        cached user-Google-Sheet unshipped-rows count. The sheet count
        is set by `/sheets/orders` and read cheaply from the settings
        doc here — never hits gspread directly (respect quota).

        Phase-21 — The badge now mirrors the "New Orders" filter on
        the Orders tab:  excludes Reused / already-shipped sheet rows
        so the home count never silently inflates when a previously
        shipped customer comes back via webhook or stays cached on
        the sheet. `new_count` is a new field that the UI can also
        read directly; legacy clients keep using `count`.

        Response shape (backward-compatible):
          - `count`             — TOTAL (smart_paste + sheet) → UI badge
          - `new_count`         — same as `count` (kept explicit)
          - `smart_paste_count` — breakdown (ages of `pending_orders`)
          - `sheet_count`       — breakdown (cached user-sheet unshipped)
        """
        sp_n = await db.pending_orders.count_documents(
            {"user_id": current_user["id"], "status": "pending"},
        )
        sheet_n = 0
        try:
            doc = await db.settings.find_one(
                {"user_id": current_user["id"]},
                {"_id": 0, "sheet": 1},
            ) or {}
            sheet_cfg = (doc.get("sheet") or {})
            if sheet_cfg.get("sheet_id"):
                cached = sheet_cfg.get("unshipped_count_cached")
                if isinstance(cached, (int, float)):
                    sheet_n = int(cached)
        except Exception:
            _logger.exception("failed to read cached sheet unshipped count")

        total = sp_n + sheet_n
        return {
            "count": total,
            "new_count": total,
            "smart_paste_count": sp_n,
            "sheet_count": sheet_n,
        }

    # =================  Phase-21: mark-viewed (NEW badge clearer)  ====

    @pending_orders_router.post("/orders/pending/{order_id}/mark-viewed")
    async def mark_pending_order_viewed(
        order_id: str,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Flip the `viewed` flag on a pending order to True.

        Called by the Orders tab the FIRST time an operator taps a
        pending-order card. The UI uses this flag to render the green
        ✨ NEW badge → once viewed, the badge goes away on every
        device. Server-side state so the marker syncs across phones.

        Idempotent: returns ok even when the order is already viewed.
        Returns 404 only if the order doesn't belong to this user (or
        doesn't exist). Sheet-source rows aren't in pending_orders so
        this endpoint silently returns ok with viewed=False (frontend
        handles their viewed-state via the abandoned/sheet fallback).
        """
        res = await db.pending_orders.update_one(
            {"id": order_id, "user_id": current_user["id"]},
            {"$set": {"viewed": True}},
        )
        if res.matched_count == 0:
            # Soft-404: order may be a sheet row (which isn't in the
            # pending_orders collection) — frontend stores viewed
            # locally for those. Still return ok=True so the client
            # treats the badge clear as successful.
            return {"ok": True, "viewed": False, "matched": False}
        return {"ok": True, "viewed": True, "matched": True}
