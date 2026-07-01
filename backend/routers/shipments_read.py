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

from pydantic import BaseModel

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse


shipments_read_router = APIRouter(prefix="/api", tags=["shipments-read"])


def init() -> None:
    """Register routes after server.py finishes initialising."""
    import logging
    import re as _re
    _logger = logging.getLogger("routers.shipments_read")
    from server import (  # noqa: WPS433 — intentional late import
        db,
        get_current_user as _get_current_user,
        Shipment,
        utcnow_iso,
        resolve_city,
        resolve_pincode,
    )
    from utils.search_normalize import (  # noqa: WPS433 — same rationale
        normalize_text,
        normalize_tokens,
        build_search_blob,
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
            # Phase-19 — "Modified" is a virtual filter: it shows every
            # shipment that's been edited via the pencil/edit form,
            # regardless of which actual workflow status (Pending /
            # Processing / Ready to Ship / etc.) the order currently
            # sits in. update_shipment() sets is_modified=true the
            # first time non-status fields are touched.
            if status == "Modified":
                q["is_modified"] = True
            else:
                aliases = STATUS_ALIASES_FOR_FILTER.get(status)
                if aliases:
                    q["status"] = {"$in": aliases}
                else:
                    q["status"] = status
        if courier_id:
            q["courier_id"] = courier_id
        if search:
            # ── Phase C — Universal Smart Search ────────────────────
            #   1. Normalise the query the same way products are
            #      normalised (unidecode + hyphen-join + gram/gm →
            #      "g" + quantity-suffix strip).  This is the SAME
            #      pipeline as the Suggested-Filters generator so
            #      the chip count and the visible list always match.
            #   2. Match on `_search_blob` (a pre-computed field on
            #      every shipment containing the normalised
            #      concatenation of items + customer_name + city +
            #      order_id + notes + weight + …).
            #   3. Fall back to the OLD field-scoped regex for any
            #      shipments that haven't been backfilled yet — they
            #      get their blob computed lazily on the next write.
            n_query = normalize_text(search)
            n_tokens = normalize_tokens(search)
            or_branches = [
                {"tracking_id":   {"$regex": search, "$options": "i"}},
                {"customer_name": {"$regex": search, "$options": "i"}},
                {"customer_phone": {"$regex": search, "$options": "i"}},
                {"city":          {"$regex": search, "$options": "i"}},
                {"order_id":      {"$regex": search, "$options": "i"}},
            ]
            if n_tokens:
                # AND across tokens (subset match) via one $regex
                # per token — cheap on Mongo when the collection is
                # tenant-scoped and small.
                and_tokens = [
                    {"_search_blob": {
                        "$regex": _re.escape(t),
                        "$options": "i",
                    }} for t in n_tokens
                ]
                or_branches.append({"$and": and_tokens})
            q["$or"] = or_branches
        docs = await db.shipments.find(q, {"_id": 0}).sort(
            "created_at", -1
        ).to_list(limit)

        # ── Lazy backfill of `_search_blob`.  Any doc served here
        #    without a blob gets one computed and persisted so future
        #    searches match without a full migration script.  Fire
        #    and forget — never blocks the response.
        _to_backfill = [d for d in docs if not d.get("_search_blob")]
        if _to_backfill:
            import asyncio as _asyncio

            async def _bf():
                try:
                    for d in _to_backfill:
                        blob = build_search_blob(d)
                        await db.shipments.update_one(
                            {"id": d["id"]},
                            {"$set": {"_search_blob": blob}},
                        )
                except Exception:  # noqa: BLE001
                    pass

            _asyncio.create_task(_bf())

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

    # =================  Product Suggestions (Phase C)  ================

    @shipments_read_router.get("/shipments/product-suggestions")
    async def product_suggestions(
        limit: int = 10,
        min_count: int = 2,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Return the top-N most frequent (normalised) product tokens
        across the caller's shipments.

        Each suggestion carries a `count` that MATCHES the number of
        shipments returned by `GET /shipments?search=<display>` — the
        client uses this to render Suggested-Filter chips whose count
        badge equals the resulting list length after the chip is
        tapped.  Implementation:

          1. Iterate the shipments collection scoped to user_id.
          2. For every `items[]` entry, normalise via
             `normalize_text()` (unidecode + hyphen-join + gram/gm
             stripping + quantity-suffix strip).
          3. Group by normalised form, keep the most common ORIGINAL
             display variant, and count.
          4. Second pass — for each group, `count_documents` against
             `_search_blob` using the SAME regex the search endpoint
             uses, so the badge is truthful even when several
             normalised variants collide.
        """
        base = {"user_id": current_user["id"]}
        # Fetch just the items[] array to keep the cursor tiny.
        groups: Dict[str, Dict[str, Any]] = {}
        cursor = db.shipments.find(base, {"_id": 0, "items": 1})
        async for d in cursor:
            arr = d.get("items") or []
            if not isinstance(arr, list):
                continue
            for raw in arr:
                term = str(raw or "").strip()
                if len(term) < 2:
                    continue
                norm = normalize_text(term)
                if not norm:
                    continue
                g = groups.get(norm)
                if g is None:
                    groups[norm] = {
                        "norm": norm,
                        "display": term,
                        "raw_count": 1,
                        "variants": {term: 1},
                    }
                else:
                    g["raw_count"] += 1
                    g["variants"][term] = g["variants"].get(term, 0) + 1
                    # Prefer the most common original spelling — keeps
                    # the chip label recognisable.
                    if g["variants"][term] > g["variants"].get(g["display"], 0):
                        g["display"] = term

        # Second pass — resolve the TRUE match count using the same
        # blob regex as the search endpoint, so tap-through never
        # shows fewer results than the badge promises.
        results = []
        for g in groups.values():
            if g["raw_count"] < min_count:
                continue
            tokens = [t for t in g["norm"].split(" ") if t]
            if not tokens:
                continue
            and_clauses = [
                {"_search_blob": {"$regex": _re.escape(t), "$options": "i"}}
                for t in tokens
            ]
            q = {**base}
            if len(and_clauses) == 1:
                q.update(and_clauses[0])
            else:
                q["$and"] = and_clauses
            match_count = await db.shipments.count_documents(q)
            if match_count < min_count:
                continue
            results.append({
                "display": g["display"],
                "norm": g["norm"],
                "count": match_count,
            })

        # Sort by count DESC, then by display alphabetical for stable order.
        results.sort(key=lambda r: (-r["count"], r["display"].lower()))
        return {"suggestions": results[: max(1, min(limit, 50))]}

    # =================  Shipments — export / single / bulk  ===========

    # 2026-05-25 — POST overload so the frontend can post the EXACT
    # ID list it has on screen (client-side compound filter combines
    # status + 24h/today/week/month/custom date range — we don't want
    # to duplicate that logic server-side and risk drift). When `ids`
    # is empty / missing, exports EVERY shipment owned by the caller.
    # The endpoint stays available as a GET for backward compatibility
    # with older mobile builds — those clients receive all shipments.
    class _ExportCsvBody(BaseModel):
        ids: Optional[List[str]] = None

    async def _build_csv_for_user(user_id: str, ids: Optional[List[str]] = None) -> str:
        mongo_q: Dict[str, Any] = {"user_id": user_id}
        if ids:
            # Cap defensively at 10_000 to avoid huge $in queries.
            mongo_q["id"] = {"$in": list(ids)[:10_000]}
        docs = await db.shipments.find(mongo_q, {"_id": 0}).sort("created_at", -1).to_list(10_000)
        buf = io.StringIO()
        writer = csv.writer(buf)
        # 2026-05-25 — Expanded column set: every field visible on the
        # in-app Shipment Details screen is now present in the CSV,
        # so the user gets a true backup-grade export (Token Amount,
        # Master Order ID, Shipment ID, AWB, alt phone, email, timestamps,
        # courier service, payment_type, notes, status sub-fields …).
        COLUMNS = [
            "Shipment ID",          "Tracking ID",          "Master Order ID",
            "Order ID",             "AWB Number",
            "Status",               "Payment Status",
            "Customer Name",        "Phone",                "Alt Phone",
            "Email",
            "Address Line 1",       "Address Line 2",       "Landmark",
            "City",                 "State",                "Pincode",        "Country",
            "Courier",              "Courier Service",      "Courier Tracking URL",
            "Items",                "Item Description",     "Quantity",
            "Weight",               "Box Dimensions",
            "Payment Mode",         "Payment Type",         "Amount",         "Token Amount",
            "COD Balance",
            "Discount",             "Tax",                  "Shipping Charges",
            "Notes",                "Shipment Notes",       "Internal Notes",
            "Source",
            "Created At",           "Updated At",
            "Shipped At",           "Out For Delivery At",  "Delivered At",
            "Cancelled At",         "Returned At",
        ]
        writer.writerow(COLUMNS)

        for d in docs:
            items = d.get("items") or []
            items_str = "; ".join(items) if items else d.get("item_description", "")
            # Phase-31 — `cod_amount` is now the canonical "balance the
            # courier collects" (= max(0, amount − token) on the
            # backend). Prefer it directly; fall back to the legacy
            # amount−token math only for ancient rows that pre-date
            # the cod_amount field.
            amount = float(d.get("amount") or 0)
            token  = float(d.get("token_amount") or d.get("token") or 0)
            cod_balance = ""
            ptype = (d.get("payment_type") or d.get("payment_mode") or "").upper()
            if ptype == "COD" or ptype == "COD/PARTIAL":
                if "cod_amount" in d and d.get("cod_amount") is not None:
                    cod_balance = f"{float(d['cod_amount'] or 0):.2f}"
                else:
                    cod_balance = f"{max(amount - token, 0):.2f}"
            writer.writerow([
                d.get("id", ""),
                d.get("tracking_id", ""),
                d.get("master_order_id", ""),
                d.get("order_id", ""),
                d.get("awb_number", "") or d.get("awb", ""),
                d.get("status", ""),
                d.get("payment_status", ""),
                d.get("customer_name", ""),
                d.get("customer_phone", ""),
                d.get("customer_alt_phone", "") or d.get("alt_phone", ""),
                d.get("customer_email", ""),
                d.get("address_line1", ""),
                d.get("address_line2", ""),
                d.get("landmark", ""),
                d.get("city", ""),
                d.get("state", ""),
                d.get("pincode", ""),
                d.get("country", "") or "India",
                d.get("courier_name", "") or d.get("courier", ""),
                d.get("courier_service", "") or d.get("service_type", ""),
                d.get("courier_tracking_url", "") or d.get("tracking_url", ""),
                items_str,
                d.get("item_description", ""),
                d.get("quantity", "") or d.get("qty", ""),
                d.get("weight", ""),
                d.get("box_dimensions", "") or d.get("dimensions", ""),
                d.get("payment_mode", ""),
                d.get("payment_type", ""),
                f"{amount:.2f}" if amount else "",
                f"{token:.2f}"  if token  else "",
                cod_balance,
                d.get("discount", ""),
                d.get("tax", ""),
                d.get("shipping_charges", "") or d.get("shipping", ""),
                d.get("notes", ""),
                d.get("shipment_notes", ""),
                d.get("internal_notes", ""),
                d.get("source", "") or d.get("created_via", ""),
                d.get("created_at", ""),
                d.get("updated_at", ""),
                d.get("shipped_at", ""),
                d.get("out_for_delivery_at", ""),
                d.get("delivered_at", ""),
                d.get("cancelled_at", ""),
                d.get("returned_at", ""),
            ])
        # Prepend a UTF-8 BOM so Excel / Numbers / LibreOffice open the
        # download with the correct encoding and Indic / accented
        # characters render right out of the box.
        return "\ufeff" + buf.getvalue()

    @shipments_read_router.get(
        "/shipments/export/csv", response_class=PlainTextResponse
    )
    async def export_csv(
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        # Legacy GET → no filter, export everything for the user.
        body = await _build_csv_for_user(current_user["id"], None)
        return PlainTextResponse(body, media_type="text/csv")

    @shipments_read_router.post(
        "/shipments/export/csv", response_class=PlainTextResponse
    )
    async def export_csv_filtered(
        payload: _ExportCsvBody,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        # POST form lets the frontend send the exact ID list that's
        # visible on screen (after applying status + date filters).
        body = await _build_csv_for_user(current_user["id"], payload.ids)
        return PlainTextResponse(body, media_type="text/csv")

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
