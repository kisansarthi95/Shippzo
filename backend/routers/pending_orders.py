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

    # =================  Soft delete (sheet tombstone aware)  ==========

    @pending_orders_router.delete("/orders/pending/{order_id}")
    async def delete_pending_order(
        order_id: str,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Soft-delete pending (Smart-Paste) orders: tombstone the
        Master Sheet row if linked, then remove the local record. Sheet
        failures are logged but do not block local deletion."""
        doc = await db.pending_orders.find_one(
            {"id": order_id, "user_id": current_user["id"]}, {"_id": 0},
        )
        if not doc:
            raise HTTPException(status_code=404, detail="Order not found")

        sheet_result: Dict[str, Any] = {"attempted": False}
        row_num = doc.get("sheet_row_num")
        # Plan-gated: same flag as shipment soft-delete tombstone.
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
                    "removed from app"
                )
                sheet_result.update(
                    sheet_mark_row_deleted(int(row_num), reason=reason),
                )
            except Exception as e:
                _logger.exception("Soft-delete sheet mark failed (pending)")
                sheet_result["ok"] = False
                sheet_result["error"] = str(e)

        res = await db.pending_orders.delete_one(
            {"id": order_id, "user_id": current_user["id"]},
        )
        if res.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Order not found")
        return {"ok": True, "sheet": sheet_result}

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

        Response shape (backward-compatible):
          - `count`             — TOTAL (smart_paste + sheet) → UI badge
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
            "smart_paste_count": sp_n,
            "sheet_count": sheet_n,
        }
