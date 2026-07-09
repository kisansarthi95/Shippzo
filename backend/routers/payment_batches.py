"""
Payment Batches — Phase F6.3 (2026-06).

A **Payment Batch** groups multiple COD-Payment imports that were
settled together by a courier via a single instrument (cheque, NEFT,
UPI, etc.). This lets merchants answer "which articles were paid in
cheque #2345678?" with a single filter tap on the Shipments screen.

Each PaymentBatch document tracks:
    id               — auto-generated UUID (internal batch id)
    name             — user-defined label ("India Post Aug W2")
    description      — optional free text
    payment_date     — ISO date of the cheque / transfer
    payment_mode     — one of {cheque, neft, bank_transfer, upi, other}
    reference_number — cheque no. / UTR / txn id (searchable, unique check)
    bank_name        — optional
    notes            — optional free text
    total_articles   — computed from shipment_ids at creation
    total_amount     — computed from cod_collected_amount sum
    shipment_ids     — list of Shipment.id linked to this batch
    import_batch_ids — list of ImportBatch.id that produced this payment
    created_at

Endpoints:
    POST /api/shipments/payment-batches                — create standalone
    GET  /api/shipments/payment-batches                — list w/ search
    GET  /api/shipments/payment-batches/{id}           — detail
    GET  /api/shipments/payment-batches/check-duplicate?ref=<x> — warn duplicate
    DELETE /api/shipments/payment-batches/{id}         — remove (soft-unlink shipments)

Integration: The COD-Payment shipment-import commit endpoint accepts
an OPTIONAL `payment_batch` JSON form field. When present, it creates
this batch and stamps `payment_batch_id` on every matched shipment.

Pattern: late-binding `init()` — same as file_import.py.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, ConfigDict


payment_batches_router = APIRouter(prefix="/api", tags=["payment-batches"])


PAYMENT_MODES = {"cheque", "neft", "bank_transfer", "upi", "other"}


class PaymentBatchIn(BaseModel):
    """Input payload for standalone create + for the embed inside the
    COD-Payment import commit request."""
    model_config = ConfigDict(extra="ignore")

    name:             str
    description:      Optional[str] = ""
    payment_date:     str                       # ISO 8601 preferred
    payment_mode:     str                       # cheque / neft / …
    reference_number: str                       # cheque # / UTR / txn #
    bank_name:        Optional[str] = ""
    notes:            Optional[str] = ""


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalise_mode(v: str) -> str:
    """Lower-case and map common aliases (Cheque → cheque, UTR → neft, …)."""
    if not v:
        return "other"
    x = v.strip().lower().replace("-", "_").replace(" ", "_")
    if x in PAYMENT_MODES:
        return x
    if "cheque" in x or "check" in x:
        return "cheque"
    if x == "utr" or "neft" in x or "rtgs" in x:
        return "neft"
    if "upi" in x or "gpay" in x or "phonepe" in x:
        return "upi"
    if "bank" in x or "transfer" in x:
        return "bank_transfer"
    return "other"


def validate_payment_batch(pb: PaymentBatchIn) -> Dict[str, Any]:
    """Convert to sanitised dict with normalised mode. Raises HTTPException
    on validation failure. Also usable from shipment_import.py without
    circular imports (thin function, no db access)."""
    if not pb.name or not pb.name.strip():
        raise HTTPException(status_code=400, detail="Payment batch name is required")
    if not pb.reference_number or not pb.reference_number.strip():
        raise HTTPException(
            status_code=400,
            detail="Reference number (Cheque / UTR / Transaction) is required",
        )
    if not pb.payment_date or not pb.payment_date.strip():
        raise HTTPException(status_code=400, detail="Payment date is required")
    return {
        "name":             pb.name.strip(),
        "description":      (pb.description or "").strip(),
        "payment_date":     pb.payment_date.strip(),
        "payment_mode":     _normalise_mode(pb.payment_mode or ""),
        "reference_number": pb.reference_number.strip(),
        "bank_name":        (pb.bank_name or "").strip(),
        "notes":            (pb.notes or "").strip(),
    }


async def find_duplicate_ref(db, user_id: str, ref_number: str) -> Optional[Dict[str, Any]]:
    """Case-insensitive lookup for an existing payment batch that used
    the same reference number. Returns the offending batch summary or
    None. Exposed so shipment_import.py can surface the warning before
    the actual commit executes."""
    if not ref_number:
        return None
    doc = await db.payment_batches.find_one(
        {
            "user_id":                     user_id,
            "reference_number_lower":      ref_number.strip().lower(),
        },
        {"_id": 0, "id": 1, "name": 1, "payment_date": 1, "payment_mode": 1,
         "reference_number": 1, "total_articles": 1, "total_amount": 1,
         "created_at": 1},
    )
    return doc


async def create_payment_batch(
    db,
    user_id: str,
    pb_data: Dict[str, Any],
    shipment_ids: List[str],
    total_amount: float,
    import_batch_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Insert a PaymentBatch. Caller is expected to have already run
    validate_payment_batch(). If `shipment_ids` is empty, we still
    create the batch (merchant can attach shipments later)."""
    now = _iso_now()
    doc = {
        "id":                     str(uuid.uuid4()),
        "user_id":                user_id,
        "created_at":             now,
        **pb_data,
        "reference_number_lower": pb_data["reference_number"].lower(),
        "shipment_ids":           list(shipment_ids),
        "import_batch_ids":       [import_batch_id] if import_batch_id else [],
        "total_articles":         len(shipment_ids),
        "total_amount":           float(total_amount or 0.0),
    }
    await db.payment_batches.insert_one(doc)
    # Drop the _id field before returning to callers.
    doc.pop("_id", None)
    return doc


# ────────────────────────── Router init ────────────────────────

def init() -> None:
    import logging
    _logger = logging.getLogger("routers.payment_batches")
    from server import (
        db,
        get_current_user as _get_current_user,
    )

    # ── Standalone create (rarely used — usually created via COD import) ──
    @payment_batches_router.post("/payment-batches")
    async def create_batch(
        payload: PaymentBatchIn,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        pb_data = validate_payment_batch(payload)
        dup = await find_duplicate_ref(db, current_user["id"], pb_data["reference_number"])
        # Standalone create returns 409 on dup — cod import path returns
        # a soft warning instead (so the user can override).
        if dup:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "duplicate_reference",
                    "message": f"Reference number '{pb_data['reference_number']}' already used in batch '{dup.get('name')}'",
                    "existing_batch": dup,
                },
            )
        doc = await create_payment_batch(
            db, current_user["id"], pb_data,
            shipment_ids=[], total_amount=0.0,
        )
        _logger.info("payment_batch created: user=%s ref=%s", current_user["id"], pb_data["reference_number"])
        return doc

    # ── Duplicate-check helper for the UI ──
    @payment_batches_router.get("/payment-batches/check-duplicate")
    async def check_duplicate(
        ref: str = Query(..., min_length=1),
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        dup = await find_duplicate_ref(db, current_user["id"], ref)
        return {"duplicate": bool(dup), "batch": dup}

    # ── List with search + filters ──
    @payment_batches_router.get("/payment-batches")
    async def list_batches(
        search:       Optional[str] = None,
        payment_mode: Optional[str] = None,
        date_from:    Optional[str] = None,
        date_to:      Optional[str] = None,
        limit:        int = Query(200, ge=1, le=500),
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        q: Dict[str, Any] = {"user_id": current_user["id"]}
        if payment_mode:
            q["payment_mode"] = _normalise_mode(payment_mode)
        if date_from or date_to:
            df: Dict[str, str] = {}
            if date_from:
                df["$gte"] = date_from
            if date_to:
                df["$lte"] = date_to
            q["payment_date"] = df
        if search and search.strip():
            s = search.strip()
            slow = s.lower()
            # Search across name, reference_number, notes, description
            q["$or"] = [
                {"name":                   {"$regex": s, "$options": "i"}},
                {"reference_number":       {"$regex": s, "$options": "i"}},
                {"reference_number_lower": slow},
                {"notes":                  {"$regex": s, "$options": "i"}},
                {"description":            {"$regex": s, "$options": "i"}},
                {"id":                     s},
            ]
        cursor = db.payment_batches.find(
            q,
            {"_id": 0, "reference_number_lower": 0, "shipment_ids": 0},
        ).sort("created_at", -1).limit(limit)
        return {"batches": [d async for d in cursor]}

    # ── Batch detail (includes shipment_ids so UI can render article list) ──
    @payment_batches_router.get("/payment-batches/{batch_id}")
    async def get_batch(
        batch_id: str,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        doc = await db.payment_batches.find_one(
            {"id": batch_id, "user_id": current_user["id"]},
            {"_id": 0, "reference_number_lower": 0},
        )
        if not doc:
            raise HTTPException(status_code=404, detail="Payment batch not found")
        return doc

    # ── Delete (soft-unlink shipments — keeps historical data intact) ──
    @payment_batches_router.delete("/payment-batches/{batch_id}")
    async def delete_batch(
        batch_id: str,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        doc = await db.payment_batches.find_one(
            {"id": batch_id, "user_id": current_user["id"]},
            {"_id": 0, "shipment_ids": 1},
        )
        if not doc:
            raise HTTPException(status_code=404, detail="Payment batch not found")
        sids = doc.get("shipment_ids") or []
        if sids:
            await db.shipments.update_many(
                {"user_id": current_user["id"], "id": {"$in": sids},
                 "payment_batch_id": batch_id},
                {"$unset": {"payment_batch_id": ""}},
            )
        await db.payment_batches.delete_one({"id": batch_id, "user_id": current_user["id"]})
        return {"ok": True, "unlinked_shipments": len(sids)}

    _logger.info("payment_batches router mounted (5 endpoints)")
