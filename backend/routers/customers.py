"""
Customers — Phase F3.3.

Reads from `db.customers` (populated by the webhook ingest path in
routers/webhook.py for event_types customer_created / customer_updated).
Provides:

  GET    /api/me/customers                  list (search + filter)
  GET    /api/me/customers/stats            summary counts
  GET    /api/me/customers/{id}             single detail
  DELETE /api/me/customers/{id}             hard delete (rare)

Pattern: late-binding `init()` — same as the rest of routers/.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query


customers_router = APIRouter(prefix="/api", tags=["customers"])


def _serialise(c: Dict[str, Any]) -> Dict[str, Any]:
    sm = c.get("source_meta") or {}
    return {
        "id":                   c.get("id"),
        "external_customer_id": c.get("external_customer_id") or "",
        "customer_name":        c.get("customer_name") or "",
        "customer_phone":       c.get("customer_phone") or "",
        "customer_email":       c.get("customer_email") or "",
        "address":              c.get("address") or "",
        "city":                 c.get("city") or "",
        "state":                c.get("state") or "",
        "pincode":              c.get("pincode") or "",
        "orders_count":         int(c.get("orders_count") or 0),
        "total_spent":          float(c.get("total_spent") or 0.0),
        "source_created_at":    c.get("source_created_at") or "",
        "created_at":           c.get("created_at") or "",
        "updated_at":           c.get("updated_at") or "",
        "source_app":           sm.get("source_app") or "",
        "webhook_name":         sm.get("webhook_name") or "",
        "last_event":           sm.get("last_event") or "",
    }


def init() -> None:
    import logging
    _logger = logging.getLogger("routers.customers")
    from server import (  # noqa: WPS433
        db,
        get_current_user as _get_current_user,
    )

    @customers_router.get("/me/customers/stats")
    async def customer_stats(
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        uid = current_user["id"]
        total = await db.customers.count_documents({"user_id": uid})
        # Per-source breakdown so the UI can show source chips with counts.
        cur = db.customers.aggregate([
            {"$match": {"user_id": uid}},
            {"$group": {
                "_id": {"$ifNull": ["$source_meta.source_app", ""]},
                "n":   {"$sum": 1},
                "spent": {"$sum": {"$ifNull": ["$total_spent", 0]}},
            }},
        ])
        sources = []
        total_spent = 0.0
        async for row in cur:
            key = row.get("_id") or ""
            sources.append({
                "source_app": key,
                "count":      int(row.get("n") or 0),
                "total_spent": round(float(row.get("spent") or 0.0), 2),
            })
            total_spent += float(row.get("spent") or 0.0)
        return {
            "total":        total,
            "total_spent":  round(total_spent, 2),
            "by_source":    sources,
        }

    @customers_router.get("/me/customers")
    async def list_customers(
        source_app: Optional[str] = Query(default=None),
        q:      Optional[str] = Query(default=None),
        limit:  int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        uid = current_user["id"]
        flt: Dict[str, Any] = {"user_id": uid}
        if source_app:
            flt["source_meta.source_app"] = source_app.strip().lower()
        if q:
            qs = q.strip()
            if qs:
                flt["$or"] = [
                    {"customer_name":        {"$regex": qs, "$options": "i"}},
                    {"customer_phone":       {"$regex": qs, "$options": "i"}},
                    {"customer_email":       {"$regex": qs, "$options": "i"}},
                    {"external_customer_id": {"$regex": qs, "$options": "i"}},
                ]
        cur = (
            db.customers.find(flt, {"_id": 0})
            .sort("updated_at", -1)
            .skip(offset)
            .limit(limit)
        )
        rows = [_serialise(d) async for d in cur]
        total = await db.customers.count_documents(flt)
        return {"customers": rows, "count": len(rows), "total": total}

    @customers_router.get("/me/customers/{cust_id}")
    async def get_customer(
        cust_id: str = Path(...),
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        c = await db.customers.find_one(
            {"id": cust_id, "user_id": current_user["id"]}, {"_id": 0},
        )
        if not c:
            raise HTTPException(status_code=404, detail="Customer not found")
        return _serialise(c)

    @customers_router.delete("/me/customers/{cust_id}")
    async def delete_customer(
        cust_id: str = Path(...),
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        res = await db.customers.delete_one(
            {"id": cust_id, "user_id": current_user["id"]},
        )
        if res.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Customer not found")
        return {"ok": True, "deleted": cust_id}

    _logger.info("customers router mounted (4 endpoints)")
