"""
Abandoned Carts — Phase F3.3.

Reads from `db.abandoned_carts` (populated by the webhook ingest path
in routers/webhook.py for event_type=abandoned_order). Provides:

  GET    /api/me/abandoned-carts                 list (filter + search)
  GET    /api/me/abandoned-carts/stats           summary counts
  GET    /api/me/abandoned-carts/{id}            single detail
  POST   /api/me/abandoned-carts/{id}/recover    convert → pending_orders
  POST   /api/me/abandoned-carts/{id}/dismiss    mark dismissed
  DELETE /api/me/abandoned-carts/{id}            hard delete

Pattern: late-binding `init()` — same as the rest of routers/.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query


abandoned_carts_router = APIRouter(prefix="/api", tags=["abandoned-carts"])


def _serialise(c: Dict[str, Any]) -> Dict[str, Any]:
    sm = c.get("source_meta") or {}
    return {
        "id":               c.get("id"),
        "external_cart_id": c.get("external_cart_id") or "",
        "customer_name":    c.get("customer_name") or "",
        "customer_phone":   c.get("customer_phone") or "",
        "customer_email":   c.get("customer_email") or "",
        "address":          c.get("address") or "",
        "city":             c.get("city") or "",
        "state":            c.get("state") or "",
        "pincode":          c.get("pincode") or "",
        "cart_value":       float(c.get("cart_value") or 0.0),
        "items_count":      int(c.get("items_count") or 0),
        "items_summary":    c.get("items_summary") or "",
        "abandoned_at":     c.get("abandoned_at") or "",
        "recovery_url":     c.get("recovery_url") or "",
        "status":           c.get("status") or "abandoned",
        "recovered_at":     c.get("recovered_at"),
        "dismissed_at":     c.get("dismissed_at"),
        "pending_order_id": c.get("pending_order_id"),
        "created_at":       c.get("created_at") or "",
        "updated_at":       c.get("updated_at") or "",
        "source_app":       sm.get("source_app") or "",
        "webhook_name":     sm.get("webhook_name") or "",
    }


def init() -> None:
    import logging
    _logger = logging.getLogger("routers.abandoned_carts")
    from server import (  # noqa: WPS433
        db,
        get_current_user as _get_current_user,
        generate_master_order_id,
    )

    @abandoned_carts_router.get("/me/abandoned-carts/stats")
    async def abandoned_stats(
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        uid = current_user["id"]
        pipeline = [
            {"$match": {"user_id": uid}},
            {"$group": {
                "_id": "$status",
                "n":   {"$sum": 1},
                "value": {"$sum": {"$ifNull": ["$cart_value", 0]}},
            }},
        ]
        cnts = {"abandoned": 0, "recovered": 0, "dismissed": 0}
        total_value = 0.0
        recovered_value = 0.0
        async for row in db.abandoned_carts.aggregate(pipeline):
            key = row.get("_id") or "abandoned"
            cnts[key] = int(row.get("n") or 0)
            v = float(row.get("value") or 0.0)
            if key == "abandoned":
                total_value = v
            elif key == "recovered":
                recovered_value = v
        return {
            "abandoned":       cnts.get("abandoned", 0),
            "recovered":       cnts.get("recovered", 0),
            "dismissed":       cnts.get("dismissed", 0),
            "total_value":     round(total_value, 2),
            "recovered_value": round(recovered_value, 2),
        }

    @abandoned_carts_router.get("/me/abandoned-carts")
    async def list_abandoned_carts(
        status: Optional[str] = Query(default=None),
        source_app: Optional[str] = Query(default=None),
        q:      Optional[str] = Query(default=None),
        limit:  int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        uid = current_user["id"]
        flt: Dict[str, Any] = {"user_id": uid}
        if status and status in ("abandoned", "recovered", "dismissed"):
            flt["status"] = status
        if source_app:
            flt["source_meta.source_app"] = source_app.strip().lower()
        if q:
            qs = q.strip()
            if qs:
                flt["$or"] = [
                    {"customer_name":    {"$regex": qs, "$options": "i"}},
                    {"customer_phone":   {"$regex": qs, "$options": "i"}},
                    {"customer_email":   {"$regex": qs, "$options": "i"}},
                    {"external_cart_id": {"$regex": qs, "$options": "i"}},
                ]
        cur = (
            db.abandoned_carts.find(flt, {"_id": 0})
            .sort("abandoned_at", -1)
            .skip(offset)
            .limit(limit)
        )
        rows = [_serialise(d) async for d in cur]
        total = await db.abandoned_carts.count_documents(flt)
        return {"carts": rows, "count": len(rows), "total": total}

    @abandoned_carts_router.get("/me/abandoned-carts/{cart_id}")
    async def get_abandoned_cart(
        cart_id: str = Path(...),
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        c = await db.abandoned_carts.find_one(
            {"id": cart_id, "user_id": current_user["id"]},
            {"_id": 0},
        )
        if not c:
            raise HTTPException(status_code=404, detail="Cart not found")
        return _serialise(c) | {"items_raw": c.get("items_raw") or []}

    @abandoned_carts_router.post("/me/abandoned-carts/{cart_id}/recover")
    async def recover_abandoned_cart(
        cart_id: str = Path(...),
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Convert an abandoned cart into a pending_orders document so
        the user can ship it from the existing Pending Orders inbox."""
        uid = current_user["id"]
        c = await db.abandoned_carts.find_one(
            {"id": cart_id, "user_id": uid}, {"_id": 0},
        )
        if not c:
            raise HTTPException(status_code=404, detail="Cart not found")
        if c.get("status") == "recovered" and c.get("pending_order_id"):
            return {
                "ok": True,
                "already_recovered": True,
                "pending_order_id": c["pending_order_id"],
            }

        now = datetime.now(timezone.utc).isoformat()
        master_oid = await generate_master_order_id()
        sm = c.get("source_meta") or {}

        pending_doc = {
            "id":              str(uuid.uuid4()),
            "user_id":         uid,
            "source":          "abandoned_cart",
            "status":          "pending",
            "imported_status": "",
            "imported_at":     "",
            "master_order_id": master_oid,
            "order_id":        master_oid,
            "external_order_id": c.get("external_cart_id") or "",
            "customer_name":   c.get("customer_name") or "",
            "customer_phone":  c.get("customer_phone") or "",
            "customer_email":  c.get("customer_email") or "",
            "address":         c.get("address") or "",
            "city":            c.get("city") or "",
            "state":           c.get("state") or "",
            "pincode":         c.get("pincode") or "",
            "amount":          float(c.get("cart_value") or 0.0),
            "items":           c.get("items_summary") or "",
            "notes":           f"Recovered from abandoned cart on {now[:10]}",
            "created_at":      now,
            "source_meta": {
                "received_at":  now,
                "webhook_name": sm.get("webhook_name") or "Abandoned Cart",
                "webhook_id":   sm.get("webhook_id") or "",
                "event_type":   "abandoned_order",
                "source_app":   sm.get("source_app") or "",
                "abandoned_cart_id": c.get("id"),
            },
        }
        await db.pending_orders.insert_one(pending_doc)

        await db.abandoned_carts.update_one(
            {"id": cart_id, "user_id": uid},
            {"$set": {
                "status":           "recovered",
                "recovered_at":     now,
                "pending_order_id": pending_doc["id"],
                "updated_at":       now,
            }},
        )
        _logger.info(
            "abandoned cart recovered: user=%s cart=%s pending=%s",
            uid, cart_id, pending_doc["id"],
        )
        return {
            "ok": True,
            "pending_order_id": pending_doc["id"],
            "master_order_id":  master_oid,
        }

    @abandoned_carts_router.post("/me/abandoned-carts/{cart_id}/dismiss")
    async def dismiss_abandoned_cart(
        cart_id: str = Path(...),
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        now = datetime.now(timezone.utc).isoformat()
        res = await db.abandoned_carts.update_one(
            {"id": cart_id, "user_id": current_user["id"]},
            {"$set": {
                "status":       "dismissed",
                "dismissed_at": now,
                "updated_at":   now,
            }},
        )
        if res.matched_count == 0:
            raise HTTPException(status_code=404, detail="Cart not found")
        return {"ok": True, "id": cart_id}

    @abandoned_carts_router.delete("/me/abandoned-carts/{cart_id}")
    async def delete_abandoned_cart(
        cart_id: str = Path(...),
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        res = await db.abandoned_carts.delete_one(
            {"id": cart_id, "user_id": current_user["id"]},
        )
        if res.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Cart not found")
        return {"ok": True, "deleted": cart_id}

    _logger.info("abandoned_carts router mounted (6 endpoints)")
