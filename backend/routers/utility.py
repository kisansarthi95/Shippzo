"""
Phase-5i — Utility / miscellaneous endpoints.
================================================
A tiny domain router that owns the remaining "no real domain" routes
that used to live at the top of server.py:

  * GET  /api/               → health-check / hello message
  * POST /api/demo/clear     → per-user "Clear Demo Data" sweep
                                (shipments + pending_orders + couriers
                                 where is_demo=True)

These were never large enough to deserve their own router, but keeping
them in server.py forced server.py to know about Mongo collections,
auth, and seed data. Lifting them out lets server.py finally drop
back below 6 kLOC and removes the last `@api_router.*` decorators
from the monolith.

The router follows the project's `init(api_router, db, ...)` late-
binding pattern so it can be imported and registered after server.py
has finished setting up the singletons.
"""
from typing import Any, Dict
from fastapi import APIRouter, Depends

# Mounted onto `app` directly (NOT included into the parent
# `api_router`) because server.py wires the parent into `app`
# before the modular router blocks run, and FastAPI doesn't pick
# up routes added to a router after it's been included.
utility_router = APIRouter(prefix="/api", tags=["utility"])

# Filled in by init() — kept as module-level singletons so the
# decorated functions can close over them.
db = None
_get_current_user = None


def init():
    """Bind closures against the server.py singletons and mount the
    routes. Follows the project's standard late-import pattern so we
    avoid the circular-import that would happen if utility.py tried
    to `import server` at module-load time.
    """
    global db, _get_current_user
    from server import db as _db, get_current_user as _gcu  # noqa: WPS433
    db = _db
    _get_current_user = _gcu

    # ----------------------- Routes ----------------------------

    @utility_router.get("/")
    async def root():
        """Simple health-check / hello response. Used by uptime
        monitors and the deployment smoke-test harness."""
        return {"message": "Courier Label Manager API"}

    @utility_router.post("/demo/clear")
    async def clear_demo_data(
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """One-tap "Clear Demo Data" sweep — Phase-21 refresh.

        Sweeps three surfaces for the calling user in a single
        request so the UI can show "Removed N shipments + M couriers"
        without juggling three API calls:

          1. shipments       where is_demo=True
          2. pending_orders  where is_demo=True OR order_id starts
                             with DEMO-ORD- OR master_order_id starts
                             with DEMO (defends against legacy seeds
                             whose is_demo flag was lost in a migration)
          3. couriers        where is_demo=True — but ONLY when no
                             REAL shipment still references the
                             courier. Otherwise the operator's first
                             real order would suddenly lose its
                             carrier link.

        Returns a per-collection breakdown plus a legacy `deleted`
        grand total so old clients keep working.
        """
        uid = current_user["id"]

        # 1) Shipments — straightforward delete.
        sh_res = await db.shipments.delete_many(
            {"user_id": uid, "is_demo": True},
        )

        # 2) Pending orders — both explicit flag AND legacy DEMO-* ids.
        po_res = await db.pending_orders.delete_many({
            "user_id": uid,
            "$or": [
                {"is_demo": True},
                {"order_id": {"$regex": "^DEMO-ORD-"}},
                {"master_order_id": {"$regex": "^DEMO"}},
            ],
        })

        # 3) Couriers — only delete demo couriers that are NOT linked
        # to any real shipment. Otherwise strip the is_demo flag so
        # the row survives but is excluded from future sweeps.
        demo_couriers = await db.couriers.find(
            {"user_id": uid, "is_demo": True},
            {"_id": 0, "id": 1, "name": 1},
        ).to_list(length=50)
        couriers_deleted = 0
        for c in demo_couriers:
            cid = c.get("id")
            cname = c.get("name") or ""
            in_use = await db.shipments.find_one(
                {
                    "user_id": uid,
                    "is_demo": {"$ne": True},
                    "$or": [{"courier_id": cid}, {"courier_name": cname}],
                },
                {"_id": 1},
            )
            if in_use:
                # Keep the courier alive for the real shipment but
                # un-flag it so future sweeps ignore it.
                await db.couriers.update_one(
                    {"user_id": uid, "id": cid},
                    {"$unset": {"is_demo": ""}},
                )
                continue
            r = await db.couriers.delete_one({"user_id": uid, "id": cid})
            couriers_deleted += int(r.deleted_count or 0)

        total = (
            int(sh_res.deleted_count)
            + int(po_res.deleted_count)
            + couriers_deleted
        )
        return {
            "ok":             True,
            "deleted":        total,   # legacy field (grand total)
            "shipments":      int(sh_res.deleted_count),
            "pending_orders": int(po_res.deleted_count),
            "couriers":       couriers_deleted,
        }
