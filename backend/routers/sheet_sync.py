"""
Sheet-Sync router — Phase-H endpoints extracted from server.py.

Extracted in Phase-29 (2026-05-30) as part of the server.py refactor.
No behavioural change — the route paths, request shapes, and response
payloads are byte-for-byte identical to the previous inline
implementation.

Endpoints (all require a logged-in user; bound to current_user.id):
  GET   /api/me/sheet-sync/status
  PUT   /api/me/sheet-sync/toggles
  POST  /api/me/sheet-sync/run-now
  POST  /api/me/sheet-sync/shipment/{shipment_id}
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, HTTPException

_LOG = logging.getLogger("routers.sheet_sync")

sheet_sync_router = APIRouter(prefix="/api", tags=["sheet-sync"])


def init() -> None:
    """Late-bind handlers so we can pull the shared `db` and auth
    dependency out of server.py without a circular import."""
    from server import (  # noqa: WPS433
        db,
        get_current_user as _get_current_user,
    )

    # ── GET /api/me/sheet-sync/status ──────────────────────
    @sheet_sync_router.get("/me/sheet-sync/status")
    async def me_sheet_sync_status(
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Aggregate counters showing the health of the user's personal
        sheet sync. Used by Settings and the Sheet Sync banner."""
        settings_doc = await db.settings.find_one(
            {"user_id": current_user["id"]}, {"_id": 0, "sheet": 1},
        ) or {}
        cfg = (settings_doc.get("sheet") or {})
        sheet_id = (cfg.get("sheet_id") or "").strip()

        pipeline = [
            {"$match": {"user_id": current_user["id"]}},
            {"$group": {
                "_id":   "$user_sheet_sync_status",
                "count": {"$sum": 1},
            }},
        ]
        counts = {"ok": 0, "pending": 0, "skipped": 0, "error": 0, "never": 0}
        total = 0
        async for row in db.shipments.aggregate(pipeline):
            key = row["_id"] if row["_id"] in counts else "never"
            counts[key] = counts.get(key, 0) + int(row["count"])
            total += int(row["count"])
        # Anything without the field at all → "never".
        explicit_total = sum(counts[k] for k in ("ok", "pending", "skipped", "error"))
        counts["never"] = max(0, total - explicit_total)

        queue_pending = await db.user_sheet_sync_pending.count_documents(
            {"user_id": current_user["id"]},
        )

        return {
            "connected":        bool(sheet_id),
            "sheet_id":         sheet_id,
            "sheet_url":        cfg.get("url") or "",
            "auto_sync_create": bool(cfg.get("auto_sync_create", True)),
            "auto_sync_status": bool(cfg.get("auto_sync_status", True)),
            "auto_sync_delete": bool(cfg.get("auto_sync_delete", True)),
            "shipment_counts":  counts,
            "queue_pending":    int(queue_pending),
            "total_shipments": total,
        }

    # ── PUT /api/me/sheet-sync/toggles ──────────────────────
    @sheet_sync_router.put("/me/sheet-sync/toggles")
    async def me_sheet_sync_toggles(
        payload: Dict[str, Any] = Body(...),
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Update auto_sync_{create,status,delete} flags."""
        update: Dict[str, Any] = {}
        for k in ("auto_sync_create", "auto_sync_status", "auto_sync_delete"):
            if k in payload:
                update[f"sheet.{k}"] = bool(payload[k])
        if not update:
            raise HTTPException(status_code=400, detail="No supported toggles in body")
        await db.settings.update_one(
            {"user_id": current_user["id"]},
            {"$set": update},
            upsert=True,
        )
        return await me_sheet_sync_status(current_user)  # type: ignore[arg-type]

    # ── POST /api/me/sheet-sync/run-now ─────────────────────
    @sheet_sync_router.post("/me/sheet-sync/run-now")
    async def me_sheet_sync_run_now(
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Drain the user's pending sync queue immediately + retry every
        shipment whose sync_status is 'error'. Returns a small summary.
        Capped at 20 ops to stay under Google Sheets per-minute quota."""
        import asyncio as _asyncio
        import user_sheet_sync as _uss
        drained = await _uss.drain_pending_queue(db, batch=10)

        # Also kick a backfill for shipments that have never been synced.
        cursor = db.shipments.find(
            {
                "user_id": current_user["id"],
                "$or": [
                    {"user_sheet_sync_status": {"$exists": False}},
                    {"user_sheet_sync_status": "error"},
                ],
            },
            {"_id": 0},
        ).limit(20)
        backfilled, errored = 0, 0
        async for ship in cursor:
            try:
                res = await _uss.sync_create(db, current_user, ship)
                if res.get("ok"):
                    backfilled += 1
                else:
                    errored += 1
                # Stay under the 60 reads/min Google Sheets quota.
                await _asyncio.sleep(1.2)
            except Exception:
                errored += 1
        return {
            "drained":    drained,
            "backfilled": backfilled,
            "errored":    errored,
            "note":       "Capped at 20 ops/call to respect Google Sheets quota — re-run if more pending.",
        }

    # ── POST /api/me/sheet-sync/shipment/{shipment_id} ─────────────
    @sheet_sync_router.post("/me/sheet-sync/shipment/{shipment_id}")
    async def me_sheet_sync_one(
        shipment_id: str,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Manually re-sync a single shipment (useful from the shipment
        detail screen when an admin sees a stale row)."""
        import user_sheet_sync as _uss
        ship = await db.shipments.find_one(
            {"id": shipment_id, "user_id": current_user["id"]}, {"_id": 0},
        )
        if not ship:
            raise HTTPException(status_code=404, detail="Shipment not found")
        if ship.get("user_sheet_row_num"):
            return await _uss.sync_status_change(
                db, current_user, ship, ship.get("status") or "Pending",
            )
        return await _uss.sync_create(db, current_user, ship)


__all__ = ["sheet_sync_router", "init"]
