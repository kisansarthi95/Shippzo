"""Shipment Print Status — persistent "Printed" flag on shipments.

Additive-only router. Adds ONE new endpoint that toggles the
`print_status` field on a shipment without touching any other
existing PUT/PATCH/DELETE handler. Reprint is intentionally a no-op
on the DB — the "Printed" flag stays sticky once confirmed.

Endpoint:
  PUT /api/shipments/{shipment_id}/print-status
    body: {"printed": true | false}
    → sets shipment.print_status = "Printed" (when printed=true)
                                = ""         (when printed=false, e.g., unmark)
       and stamps `printed_at` on the true case.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

_logger = logging.getLogger("routers.print_status")

print_status_router = APIRouter(prefix="/api", tags=["print-status"])


class PrintStatusPayload(BaseModel):
    printed: bool


def init() -> None:
    from server import (  # noqa: WPS433 — late import mirrors sibling routers
        db,
        get_current_user as _get_current_user,
        utcnow_iso,
    )

    @print_status_router.put("/shipments/{shipment_id}/print-status")
    async def set_print_status_endpoint(
        shipment_id: str,
        payload: PrintStatusPayload,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        # Fetch the shipment first so we can enforce the "must have a
        # tracking id to mark as printed" invariant. Aligns with the
        # frontend button state contract — the button is disabled
        # (grey) when tracking_id is empty, so no legitimate client
        # would ever POST here without a tracking id. Guard so a
        # rogue client can't create ghost-printed shipments.
        existing = await db.shipments.find_one(
            {"id": shipment_id, "user_id": current_user["id"]},
            {"_id": 0, "tracking_id": 1, "manual_tracking_id": 1},
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Shipment not found")

        if payload.printed:
            trk = (
                str(existing.get("tracking_id") or "").strip()
                or str(existing.get("manual_tracking_id") or "").strip()
            )
            if not trk:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "Tracking ID Required — Please add the Courier "
                        "Tracking ID before marking this shipment as "
                        "printed."
                    ),
                )

        now = utcnow_iso()
        update: Dict[str, Any] = {
            "print_status": "Printed" if payload.printed else "",
            "updated_at":   now,
        }
        if payload.printed:
            update["printed_at"] = now
        # When un-marking we DELIBERATELY do NOT null out `printed_at`
        # so the previous print event stays in the audit trail. The UI
        # only reads `print_status` for its state machine.

        res = await db.shipments.update_one(
            {"id": shipment_id, "user_id": current_user["id"]},
            {"$set": update},
        )
        if res.matched_count == 0:
            # Extremely rare race — the row disappeared between the
            # find_one above and here (concurrent DELETE). Surface as
            # a plain 404 rather than a 500.
            raise HTTPException(status_code=404, detail="Shipment not found")

        return {
            "ok":           True,
            "print_status": update["print_status"],
            "printed_at":   update.get("printed_at", ""),
        }

    _logger.info("print_status router mounted")
