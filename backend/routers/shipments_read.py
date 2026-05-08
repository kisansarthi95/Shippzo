"""
Shipments — read-only + bulk-fetch + lookup endpoints — Phase-4c
incremental refactor.

Extracts 9 read-leaning endpoints out of server.py. The HEAVIER
mutation endpoints (POST /shipments, PUT /shipments/{id}, DELETE,
scan-dispatch, scan-ship, bulk-mark-processing, delivery-confirmation/*)
STAY in server.py for now — they're tightly coupled with wallet
charges, sheet writes, custom-field handling, and SLA hooks.

Endpoints relocated (all under /api):

  Shipments — list / stats / single / bulk
  ----------------------------------------
  GET  /shipments                     list_shipments
  GET  /shipments/stats               shipments_stats
  GET  /shipments/by-tracking/{tid}   get_shipment_by_tracking
  POST /shipments/bulk-fetch          bulk_fetch
  GET  /shipments/export/csv          export_csv
  GET  /shipments/{shipment_id}       get_shipment

  Customer / address lookup helpers
  ---------------------------------
  GET  /customers/by-phone/{phone}    get_customer_by_phone
  GET  /lookup/by-city                lookup_by_city
  GET  /lookup/by-pincode/{pincode}   lookup_by_pincode

Pattern: late-binding `init()` — same as routers/smart_paste.py.

Note on `/shipments/bulk-fetch`: although it's a POST and has a
side-effect (auto-flips Pending → Processing on first label render),
it's a READ from the API consumer's perspective — they're asking for
the shipment records. The flip is just a label-printing convenience.
"""
import csv
import io
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse


shipments_read_router = APIRouter(prefix="/api", tags=["shipments-read"])


def init() -> None:
    """Register routes after server.py finishes initialising."""
    import logging
    _logger = logging.getLogger("routers.shipments_read")
    from server import (  # noqa: WPS433 — intentional late import
        db,
        get_current_user as _get_current_user,
        Shipment,
        utcnow_iso,
        resolve_city,
        resolve_pincode,
    )

    # =================  Shipments — list + stats  =====================

    # Phase F2.2 — alias map so GET /shipments?status=Ready%20to%20Ship
    # also surfaces legacy rows still tagged "Dispatch" / "Dispatched".
    # Mirrors the STATUS_META aliases on the frontend so what the user
    # sees in the UI tab matches what the API returns. NOTE: every alias
    # value listed here MUST also be an existing canonical status value
    # somewhere — otherwise a typo could silently expand a filter.
    STATUS_ALIASES_FOR_FILTER: Dict[str, list] = {
        "Ready to Ship": ["Ready to Ship", "Dispatch", "Dispatched",
                          "ReadyToShip", "READY_TO_SHIP"],
    }

    @shipments_read_router.get("/shipments", response_model=List[Shipment])
    async def list_shipments(
        status: Optional[str] = None,
        courier_id: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 500,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        # Always scope to the logged-in user so one tenant never sees
        # another's data.
        q: dict = {"user_id": current_user["id"]}
        if status:
            aliases = STATUS_ALIASES_FOR_FILTER.get(status)
            if aliases:
                q["status"] = {"$in": aliases}
            else:
                q["status"] = status
        if courier_id:
            q["courier_id"] = courier_id
        if search:
            q["$or"] = [
                {"tracking_id":   {"$regex": search, "$options": "i"}},
                {"customer_name": {"$regex": search, "$options": "i"}},
                {"customer_phone": {"$regex": search, "$options": "i"}},
                {"city":          {"$regex": search, "$options": "i"}},
                {"order_id":      {"$regex": search, "$options": "i"}},
            ]
        docs = await db.shipments.find(q, {"_id": 0}).sort(
            "created_at", -1
        ).to_list(limit)
        return [Shipment(**d) for d in docs]

    @shipments_read_router.get("/shipments/stats")
    async def shipments_stats(
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        base = {"user_id": current_user["id"]}
        total = await db.shipments.count_documents(base)
        delivered = await db.shipments.count_documents(
            {**base, "status": "Delivered"},
        )
        pending = await db.shipments.count_documents(
            {**base, "status": "Pending"},
        )
        # Phase-9: "Dispatch" is an intermediate status between Pending
        # and Shipped used by the barcode "Scan to Dispatch" workflow.
        # Counted separately so the Shipments filter tab can badge it.
        dispatch = await db.shipments.count_documents(
            {**base, "status": "Dispatch"},
        )
        shipped = await db.shipments.count_documents(
            {**base, "status": "Shipped"},
        )
        cod_cursor = db.shipments.aggregate([
            {"$match": {**base, "payment_mode": "COD",
                        "status": {"$ne": "Cancelled"}}},
            {"$group": {"_id": None, "sum": {"$sum": "$amount"},
                        "count": {"$sum": 1}}},
        ])
        cod_sum = 0.0
        cod_count = 0
        async for row in cod_cursor:
            cod_sum = float(row.get("sum", 0.0))
            cod_count = int(row.get("count", 0))
        prepaid_cursor = db.shipments.aggregate([
            {"$match": {**base, "payment_mode": "Prepaid",
                        "status": {"$ne": "Cancelled"}}},
            {"$group": {"_id": None, "sum": {"$sum": "$amount"},
                        "count": {"$sum": 1}}},
        ])
        prepaid_sum = 0.0
        prepaid_count = 0
        async for row in prepaid_cursor:
            prepaid_sum = float(row.get("sum", 0.0))
            prepaid_count = int(row.get("count", 0))
        return {
            "total":         total,
            "delivered":     delivered,
            "pending":       pending,
            "dispatch":      dispatch,
            "shipped":       shipped,
            "cod_total":     cod_sum,
            "cod_count":     cod_count,
            "prepaid_total": prepaid_sum,
            "prepaid_count": prepaid_count,
            "revenue_total": cod_sum + prepaid_sum,
        }

    # =================  Shipments — export / single / bulk  ===========

    @shipments_read_router.get(
        "/shipments/export/csv", response_class=PlainTextResponse
    )
    async def export_csv(
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        docs = await db.shipments.find(
            {"user_id": current_user["id"]}, {"_id": 0},
        ).sort("created_at", -1).to_list(5000)
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            "Tracking ID", "Courier", "Order ID", "Customer", "Phone",
            "Address Line 1", "Address Line 2", "City", "State", "Pincode",
            "Payment Mode", "Amount", "Items", "Weight",
            "Status", "Created At", "Delivered At",
        ])
        for d in docs:
            items = d.get("items") or []
            items_str = (
                "; ".join(items) if items else d.get("item_description", "")
            )
            writer.writerow([
                d.get("tracking_id", ""), d.get("courier_name", ""),
                d.get("order_id", ""),
                d.get("customer_name", ""), d.get("customer_phone", ""),
                d.get("address_line1", ""), d.get("address_line2", ""),
                d.get("city", ""), d.get("state", ""), d.get("pincode", ""),
                d.get("payment_mode", ""), d.get("amount", 0),
                items_str, d.get("weight", ""),
                d.get("status", ""), d.get("created_at", ""),
                d.get("delivered_at", ""),
            ])
        return PlainTextResponse(buf.getvalue(), media_type="text/csv")

    @shipments_read_router.get("/shipments/by-tracking/{tracking_id}")
    async def get_shipment_by_tracking(
        tracking_id: str,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        doc = await db.shipments.find_one(
            {
                "user_id": current_user["id"],
                "tracking_id": {"$regex": f"^{tracking_id}$", "$options": "i"},
            },
            {"_id": 0},
        )
        if not doc:
            raise HTTPException(status_code=404, detail="Not found")
        return Shipment(**doc)

    @shipments_read_router.post("/shipments/bulk-fetch")
    async def bulk_fetch(
        payload: Dict[str, List[str]],
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Fetch N shipments by id for bulk label rendering.

        Phase-12 side-effect: any Pending rows in the requested batch
        are auto-flipped to Processing here. Rationale: calling
        bulk-fetch is the client's signal that it is about to render
        labels — per the new warehouse flow, "Label created" ==
        status = Processing. This gives operators one less manual step
        while remaining idempotent: rows already past Pending are
        untouched.
        """
        ids = payload.get("ids", [])
        if not ids:
            return []
        # Auto-flip Pending → Processing on label print. Do it before
        # the final read so the returned shipments already carry the
        # new status.
        try:
            await db.shipments.update_many(
                {
                    "user_id": current_user["id"],
                    "id": {"$in": ids},
                    "status": "Pending",
                },
                {"$set": {
                    "status": "Processing",
                    "processing_started_at": utcnow_iso(),
                }},
            )
        except Exception:
            # Never let a side-effect hiccup break the main bulk-fetch
            # response (e.g. if the update_many times out, still return
            # the rows so the printer flow keeps working).
            _logger.exception("bulk-fetch auto-Processing flip failed")

        docs = await db.shipments.find(
            {"user_id": current_user["id"], "id": {"$in": ids}},
            {"_id": 0},
        ).to_list(500)
        by_id = {d["id"]: Shipment(**d) for d in docs}
        ordered = [by_id[i].model_dump() for i in ids if i in by_id]
        return ordered

    # =================  Customer + address lookup  ====================

    @shipments_read_router.get("/customers/by-phone/{phone}")
    async def get_customer_by_phone(
        phone: str,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Look up the most-recent customer record for a given phone
        number within the current user's workspace. Powers the Smart
        Paste AI "repeat customer" suggestion so users don't have to
        re-type past addresses.

        Search order:
          1. shipments collection (most authoritative — already dispatched).
          2. pending_orders collection (pasted but not shipped yet).

        Returns { found: bool, customer: {...} | null, count: int }.
        """
        # Normalise to last 10 digits for robust match (strips +91 / spaces).
        digits = "".join(ch for ch in (phone or "") if ch.isdigit())
        if len(digits) < 10:
            return {"found": False, "customer": None, "count": 0}
        tail = digits[-10:]
        rx = {"$regex": f"{tail}$"}

        ship_cursor = db.shipments.find(
            {"user_id": current_user["id"], "customer_phone": rx},
            {"_id": 0},
        ).sort("created_at", -1)
        ships: List[Dict[str, Any]] = await ship_cursor.to_list(10)

        if ships:
            s = ships[0]
            last_items = s.get("items") or []
            last_amount = s.get("amount")
            return {
                "found": True,
                "count": len(ships),
                "customer": {
                    "customer_name":  s.get("customer_name", ""),
                    "customer_phone": s.get("customer_phone", ""),
                    "address_line1":  s.get("address_line1", ""),
                    "address_line2":  s.get("address_line2", ""),
                    "city":           s.get("city", ""),
                    "state":          s.get("state", ""),
                    "pincode":        s.get("pincode", ""),
                    "last_items":     last_items,
                    "last_amount":    last_amount,
                    "source":            "shipment",
                    "last_tracking_id": s.get("tracking_id", ""),
                    "last_date":         s.get("created_at", ""),
                },
            }

        # Fallback: look in pending orders queue.
        pend_cursor = db.pending_orders.find(
            {"user_id": current_user["id"], "customer_phone": rx},
            {"_id": 0},
        ).sort("created_at", -1)
        pends: List[Dict[str, Any]] = await pend_cursor.to_list(5)
        if pends:
            p = pends[0]
            return {
                "found": True,
                "count": len(pends),
                "customer": {
                    "customer_name":  p.get("customer_name", ""),
                    "customer_phone": p.get("customer_phone", ""),
                    "address_line1":  p.get("address_line1", ""),
                    "address_line2":  p.get("address_line2", ""),
                    "city":           p.get("city", ""),
                    "state":          p.get("state", ""),
                    "pincode":        p.get("pincode", ""),
                    "source":         "pending",
                    "last_date":      p.get("created_at", ""),
                },
            }
        return {"found": False, "customer": None, "count": 0}

    @shipments_read_router.get("/lookup/by-city")
    async def lookup_by_city(
        q: str,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """City → state + pincode candidates.

        Query: ?q=Surat (3+ chars required; 2-letter inputs return a
        no-result reply since they yield far too many India-Post matches).

        Response (200):
        {
          "ok": true,
          "city": "Surat",
          "state": "Gujarat",
          "state_confidence": "high",   // high / medium / low
          "suggestions": [
            {"pincode": "395003", "office": "Adajan", "district": "Surat",
             "state": "Gujarat"}, ...
          ],
          "count": 8
        }

        Response when nothing useful found (still 200, no-op):
        { "ok": true, "city": "...", "state": "", "suggestions": [], ...}
        """
        name = (q or "").strip()
        if len(name) < 3:
            return {
                "ok": True, "city": name, "state": "",
                "state_confidence": "low",
                "suggestions": [], "count": 0,
            }
        info = await resolve_city(db, name)
        if not info:
            return {
                "ok": True, "city": name, "state": "",
                "state_confidence": "low",
                "suggestions": [], "count": 0,
            }
        return {"ok": True, "city": name, **info}

    @shipments_read_router.get("/lookup/by-pincode/{pincode}")
    async def lookup_by_pincode(
        pincode: str,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Pincode → state + district + locality. Single-record helper
        for the inline "Confirm pincode" flow. 404 when invalid /
        unknown."""
        info = await resolve_pincode(db, pincode)
        if not info:
            raise HTTPException(status_code=404, detail="Pincode not found")
        return {
            "ok": True, "pincode": pincode,
            "state":    info.get("state", ""),
            "district": info.get("district", ""),
            "taluka":   info.get("taluka", ""),
            "office":   info.get("office", ""),
        }

    # =================  Single shipment GET (registered LAST)  =========
    # Order matters — FastAPI matches routes in registration order, so
    # `/shipments/{shipment_id}` must come AFTER literal-prefix paths
    # like `/shipments/stats`, `/shipments/export/csv` etc. We declare
    # it last to be explicit about that.

    @shipments_read_router.get(
        "/shipments/{shipment_id}", response_model=Shipment
    )
    async def get_shipment(
        shipment_id: str,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        doc = await db.shipments.find_one(
            {"id": shipment_id, "user_id": current_user["id"]}, {"_id": 0},
        )
        if not doc:
            raise HTTPException(status_code=404, detail="Shipment not found")
        return Shipment(**doc)
