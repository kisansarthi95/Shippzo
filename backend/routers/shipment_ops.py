"""
Shipment scan + bulk-mark + delivery-confirmation flows — Phase-5a
incremental refactor.

Extracts 6 mutation endpoints out of server.py monolith. These are
warehouse-workflow operations (state machine flips) that don't touch
the wallet, sheet writes, or custom-fields layer — they're tightly
self-contained, just status transitions.

Endpoints relocated (all under /api):

  Scan flow (4-stage warehouse: Pending → Processing → Dispatch → Shipped)
  -----------------------------------------------------------------------
  POST /shipments/scan-dispatch        scan_to_dispatch
  POST /shipments/scan-ship            scan_to_shipped

  Bulk operations
  ---------------
  POST /shipments/bulk-mark-processing bulk_mark_processing

  Delivery-confirmation queue (Shipped → Delivered via WhatsApp ping)
  ------------------------------------------------------------------
  GET  /shipments/delivery-confirmation
                                       delivery_confirmation_list
  POST /shipments/delivery-confirmation/mark-sent
                                       delivery_confirmation_mark_sent
  POST /shipments/delivery-confirmation/mark-delivered
                                       delivery_confirmation_mark_delivered

Pattern: late-binding `init()` — same as routers/shipments_read.py.

These endpoints DON'T need wallet_charge or sheet writes — they're
just Mongo-side status updates with race-safe atomic-conditional
update_one calls. That's why they extract cleanly.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field


shipment_ops_router = APIRouter(prefix="/api", tags=["shipment-ops"])


# ============================== Models ==============================

class ScanDispatchRequest(BaseModel):
    """Body for /shipments/scan-dispatch and /shipments/scan-ship.
    Both endpoints take a single tracking_id."""
    tracking_id: str


class BulkMarkProcessingRequest(BaseModel):
    """Body for /shipments/bulk-mark-processing."""
    shipment_ids: List[str] = Field(default_factory=list)


class DeliveryConfirmationBulkRequest(BaseModel):
    """Body for /shipments/delivery-confirmation/mark-sent and
    /shipments/delivery-confirmation/mark-delivered."""
    shipment_ids: List[str] = Field(default_factory=list)


# ============================== Constants ==========================

# Default age (in days) at which a Shipped parcel enters the
# delivery-confirmation queue. Overridable via the ?threshold_days=N
# query param on the GET endpoint.
DELIVERY_CONF_MIN_DAYS = 5


def _days_since_iso(iso_str: Optional[str]) -> int:
    """Parse an ISO timestamp and return integer days elapsed since
    `now()`. Returns 0 on parse failure or empty input."""
    if not iso_str:
        return 0
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - dt
        return max(0, int(delta.total_seconds() // 86400))
    except Exception:
        return 0


def init() -> None:
    """Register routes after server.py finishes initialising."""
    from server import (  # noqa: WPS433 — intentional late import
        db,
        get_current_user as _get_current_user,
        utcnow_iso,
    )

    # =================  Scan flow  ====================================

    @shipment_ops_router.post("/shipments/scan-dispatch")
    async def scan_to_dispatch(
        payload: ScanDispatchRequest,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """First scan in the new 4-stage warehouse flow:
           PENDING → PROCESSING → READY_TO_SHIP (a.k.a. "Dispatch") → SHIPPED.

        Source statuses accepted:
          • "Processing"  — primary path (post-label-print)
          • "Pending"     — legacy compat fallback (warn-only) so historic
                            rows that never went through Processing can
                            still be scanned. The response carries a
                            `hint: "skipped_processing"` flag so the UI
                            can nudge operators to flip Pending →
                            Processing first next time.

        Target status remains "Dispatch" in Mongo for back-compat with
        the Two-Way Sheet sync formatter; the UI maps it to the
        "Ready to Ship" label via STATUS_META in shipments.tsx.
        """
        tid = (payload.tracking_id or "").strip()
        if not tid:
            return {
                "outcome": "failed",
                "reason": "empty_tracking_id",
                "message": "Empty barcode",
                "shipment": None,
            }
        doc = await db.shipments.find_one(
            {"user_id": current_user["id"], "tracking_id": tid},
            {"_id": 0},
        )
        if not doc:
            return {
                "outcome": "failed",
                "reason": "not_found",
                "message": f"Tracking {tid} not found",
                "shipment": None,
            }
        status = str(doc.get("status") or "").strip()
        if status in ("Dispatch", "Dispatched", "Ready to Ship", "ReadyToShip"):
            return {
                "outcome": "already",
                "reason": "already_ready_to_ship",
                "message": "Already in Ready to Ship",
                "shipment": doc,
            }
        # Reject anything past Ready-to-Ship (Shipped / Delivered /
        # Feedback) and anything that doesn't make sense for an inbound
        # scan (Cancelled / Returned / Cancel by buyer / Modified).
        if status not in ("Pending", "Processing"):
            return {
                "outcome": "failed",
                "reason": f"wrong_status:{status or 'unknown'}",
                "message": (
                    f"Cannot move to Ready to Ship — status is "
                    f"{status or 'unknown'}"
                ),
                "shipment": doc,
            }
        # Atomic conditional update so a parallel scan can't double-flip.
        res = await db.shipments.update_one(
            {
                "user_id": current_user["id"],
                "tracking_id": tid,
                "status": {"$in": ["Pending", "Processing"]},
            },
            {"$set": {"status": "Dispatch", "dispatched_at": utcnow_iso()}},
        )
        if res.modified_count != 1:
            # Another scan won the race — re-fetch and treat as "already".
            cur = await db.shipments.find_one(
                {"user_id": current_user["id"], "tracking_id": tid},
                {"_id": 0},
            )
            return {
                "outcome": "already",
                "reason": "race_already_ready_to_ship",
                "message": "Already in Ready to Ship",
                "shipment": cur,
            }
        new_doc = await db.shipments.find_one(
            {"user_id": current_user["id"], "tracking_id": tid},
            {"_id": 0},
        )
        payload_out: Dict[str, Any] = {
            "outcome": "moved",
            "reason": "ok",
            "message": f"{tid} moved to Ready to Ship",
            "shipment": new_doc,
        }
        # Surface a soft hint when the operator skipped Processing — the
        # mobile scanner uses this to drop a "Tip: mark as Processing
        # first next time" toast without blocking the flow.
        if status == "Pending":
            payload_out["hint"] = "skipped_processing"
        return payload_out

    @shipment_ops_router.post("/shipments/scan-ship")
    async def scan_to_shipped(
        payload: ScanDispatchRequest,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Phase-10: second half of the warehouse workflow.
        Atomic Dispatch → Shipped transition. Same outcome contract as
        /scan-dispatch above so the mobile scanner can share its UI code.
        """
        tid = (payload.tracking_id or "").strip()
        if not tid:
            return {
                "outcome": "failed",
                "reason": "empty_tracking_id",
                "message": "Empty barcode",
                "shipment": None,
            }
        doc = await db.shipments.find_one(
            {"user_id": current_user["id"], "tracking_id": tid},
            {"_id": 0},
        )
        if not doc:
            return {
                "outcome": "failed",
                "reason": "not_found",
                "message": f"Tracking {tid} not found",
                "shipment": None,
            }
        status = str(doc.get("status") or "").strip()
        if status == "Shipped":
            return {
                "outcome": "already",
                "reason": "already_shipped",
                "message": "Already Shipped",
                "shipment": doc,
            }
        # Only the legal transition Ready-to-Ship → Shipped is allowed
        # (DB value: "Dispatch" / "Dispatched"). Everything else
        # (Pending, Processing, Delivered, Cancelled, …) falls through
        # as failed so the warehouse operator sees a clear red badge.
        if status not in (
            "Dispatch", "Dispatched", "Ready to Ship", "ReadyToShip",
        ):
            return {
                "outcome": "failed",
                "reason": f"wrong_status:{status or 'unknown'}",
                "message": (
                    f"Cannot ship — status is {status or 'unknown'} "
                    "(scan to Ready to Ship first)"
                ),
                "shipment": doc,
            }
        res = await db.shipments.update_one(
            {
                "user_id": current_user["id"],
                "tracking_id": tid,
                "status": {"$in": [
                    "Dispatch", "Dispatched", "Ready to Ship", "ReadyToShip",
                ]},
            },
            {"$set": {"status": "Shipped", "shipped_at": utcnow_iso()}},
        )
        if res.modified_count != 1:
            cur = await db.shipments.find_one(
                {"user_id": current_user["id"], "tracking_id": tid},
                {"_id": 0},
            )
            return {
                "outcome": "already",
                "reason": "race_already_shipped",
                "message": "Already Shipped",
                "shipment": cur,
            }
        new_doc = await db.shipments.find_one(
            {"user_id": current_user["id"], "tracking_id": tid},
            {"_id": 0},
        )
        return {
            "outcome": "moved",
            "reason": "ok",
            "message": f"{tid} moved to Shipped",
            "shipment": new_doc,
        }

    # =================  Bulk operations  =============================

    @shipment_ops_router.post("/shipments/bulk-mark-processing")
    async def bulk_mark_processing(
        payload: BulkMarkProcessingRequest,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Move N shipments from Pending → Processing in one shot.

        Skips rows whose status is anything other than "Pending" so the
        operator never has to worry about the request undoing later
        progress (e.g. accidentally pulling a Shipped row back to
        Processing). Returns per-bucket counts so the UI can confirm:

            {
              "updated":    7,    # actually flipped
              "skipped":    2,    # already past Pending
              "not_found":  1,    # bad id
              "updated_ids":   [...],
              "skipped_ids":   [...],
              "not_found_ids": [...]
            }
        """
        ids = [i for i in (payload.shipment_ids or []) if i]
        if not ids:
            return {
                "updated": 0, "skipped": 0, "not_found": 0,
                "updated_ids": [], "skipped_ids": [], "not_found_ids": [],
            }
        rows = await db.shipments.find(
            {"user_id": current_user["id"], "id": {"$in": ids}},
            {"_id": 0, "id": 1, "status": 1},
        ).to_list(len(ids))
        found_ids = {r["id"] for r in rows}
        not_found_ids = [i for i in ids if i not in found_ids]
        updated_ids: List[str] = []
        skipped_ids: List[str] = []
        for r in rows:
            if str(r.get("status") or "").strip() == "Pending":
                updated_ids.append(r["id"])
            else:
                skipped_ids.append(r["id"])
        if updated_ids:
            await db.shipments.update_many(
                {
                    "user_id": current_user["id"],
                    "id": {"$in": updated_ids},
                    "status": "Pending",
                },
                {"$set": {
                    "status": "Processing",
                    "processing_started_at": utcnow_iso(),
                }},
            )
        return {
            "updated":       len(updated_ids),
            "skipped":       len(skipped_ids),
            "not_found":     len(not_found_ids),
            "updated_ids":   updated_ids,
            "skipped_ids":   skipped_ids,
            "not_found_ids": not_found_ids,
        }

    # =================  Delivery confirmation queue  ==================

    @shipment_ops_router.get("/shipments/delivery-confirmation")
    async def delivery_confirmation_list(
        threshold_days: int = DELIVERY_CONF_MIN_DAYS,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Return shipments needing delivery confirmation.

        Filter: status="Shipped" AND days_since_shipped >= threshold
        AND confirmation_status != "confirmed". Enriched with computed
        `days_since_shipped` and bucket counts for the List / Sent /
        Replied / Pending tabs.
        """
        q = {
            "user_id": current_user["id"],
            "status": "Shipped",
            "confirmation_status": {"$ne": "confirmed"},
        }
        rows = await db.shipments.find(q, {"_id": 0}).sort(
            "shipped_at", 1
        ).to_list(5000)
        enriched: List[Dict[str, Any]] = []
        for r in rows:
            days = _days_since_iso(
                r.get("shipped_at") or r.get("created_at"),
            )
            if days < int(threshold_days or 0):
                continue
            r["days_since_shipped"] = days
            enriched.append(r)
        # Bucket counts for the tabs.
        counts = {
            "list":    len(enriched),
            "sent": sum(
                1 for r in enriched
                if r.get("confirmation_status") == "sent"
            ),
            "replied": sum(
                1 for r in enriched
                if r.get("confirmation_status") == "replied"
            ),
            "pending": sum(
                1 for r in enriched
                if (r.get("confirmation_status") or "pending") == "pending"
            ),
        }
        return {
            "threshold_days": int(threshold_days),
            "counts": counts,
            "shipments": enriched,
        }

    @shipment_ops_router.post(
        "/shipments/delivery-confirmation/mark-sent"
    )
    async def delivery_confirmation_mark_sent(
        payload: DeliveryConfirmationBulkRequest,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Bulk-mark selected shipments as WhatsApp-sent. Safety rule:
        skip any shipment already sent TODAY to avoid accidental spam.
        Returns a breakdown of updated vs skipped ids.
        """
        ids = [i for i in (payload.shipment_ids or []) if i]
        if not ids:
            return {
                "updated": 0, "skipped": 0,
                "updated_ids": [], "skipped_ids": [],
            }
        today_prefix = utcnow_iso()[:10]  # YYYY-MM-DD
        rows = await db.shipments.find(
            {"user_id": current_user["id"], "id": {"$in": ids}},
            {
                "_id": 0, "id": 1,
                "confirmation_status": 1, "last_confirmation_sent_at": 1,
            },
        ).to_list(len(ids))
        updated_ids: List[str] = []
        skipped_ids: List[str] = []
        for r in rows:
            last = r.get("last_confirmation_sent_at") or ""
            if last.startswith(today_prefix):
                skipped_ids.append(r["id"])
                continue
            updated_ids.append(r["id"])
        if updated_ids:
            await db.shipments.update_many(
                {"user_id": current_user["id"], "id": {"$in": updated_ids}},
                {"$set": {
                    "confirmation_status": "sent",
                    "last_confirmation_sent_at": utcnow_iso(),
                }},
            )
        return {
            "updated": len(updated_ids),
            "skipped": len(skipped_ids),
            "updated_ids": updated_ids,
            "skipped_ids": skipped_ids,
        }

    @shipment_ops_router.post(
        "/shipments/delivery-confirmation/mark-delivered"
    )
    async def delivery_confirmation_mark_delivered(
        payload: DeliveryConfirmationBulkRequest,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Bulk-confirm delivery: status → Delivered, confirmation →
        confirmed. Only shipments currently in Shipped status are
        flipped (safety)."""
        ids = [i for i in (payload.shipment_ids or []) if i]
        if not ids:
            return {"updated": 0, "skipped_ids": []}
        res = await db.shipments.update_many(
            {
                "user_id": current_user["id"],
                "id": {"$in": ids},
                "status": "Shipped",
            },
            {"$set": {
                "status": "Delivered",
                "confirmation_status": "confirmed",
                "delivered_at": utcnow_iso(),
            }},
        )
        return {"updated": int(res.modified_count), "requested": len(ids)}
