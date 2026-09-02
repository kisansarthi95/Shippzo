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
# Phase F4.6 — export helpers (CSV + XLSX) moved to shipments_export.py;
# the imports below are trimmed accordingly.
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException


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
        normalize_tokens_grouped,
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
        # Phase F6.3 — Filter panel drill-downs for the Shipment Import
        # System. `import_batch_id` returns only shipments touched by
        # THAT specific ImportBatch. `payment_batch_id` mirrors the
        # concept for COD settlement batches. Both are optional and
        # compose cleanly with `status` / `search` / `courier_id`.
        import_batch_id:  Optional[str] = None,
        payment_batch_id: Optional[str] = None,
        # Phase F9 — Shipment Filter Phase B.
        # `booking_date_from` / `booking_date_to`  — order booking date.
        # `import_date_from`  / `import_date_to`   — created_at (when
        #                                            the row landed).
        # `batch_status` values:
        #   "in_batch"     — has any payment_batch_id
        #   "not_in_batch" — has no payment_batch_id
        #   "reconciled"   — status = "Delivered" AND has payment_batch_id
        #   "unreconciled" — status = "Delivered" AND has no payment_batch_id
        booking_date_from: Optional[str] = None,
        booking_date_to:   Optional[str] = None,
        import_date_from:  Optional[str] = None,
        import_date_to:    Optional[str] = None,
        batch_status:      Optional[str] = None,
        limit: int = 500,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        # Always scope to the logged-in user so one tenant never sees
        # another's data.
        q: dict = {"user_id": current_user["id"]}
        if import_batch_id:
            q["import_batch_ids"] = import_batch_id
        if payment_batch_id:
            q["payment_batch_id"] = payment_batch_id
        # ── Phase F9 — date range + batch-status filters ─────────────
        if booking_date_from or booking_date_to:
            rng: Dict[str, str] = {}
            if booking_date_from:
                rng["$gte"] = booking_date_from
            if booking_date_to:
                rng["$lte"] = booking_date_to
            q["booking_date"] = rng
        if import_date_from or import_date_to:
            rng2: Dict[str, str] = {}
            if import_date_from:
                rng2["$gte"] = import_date_from
            if import_date_to:
                rng2["$lte"] = import_date_to
            q["created_at"] = rng2
        if batch_status:
            bs = batch_status.strip().lower()
            if bs == "in_batch":
                q["payment_batch_id"] = {"$exists": True, "$ne": None, "$ne": ""}
            elif bs == "not_in_batch":
                q["$or"] = (q.get("$or") or []) + [
                    {"payment_batch_id": {"$exists": False}},
                    {"payment_batch_id": None},
                    {"payment_batch_id": ""},
                ]
            elif bs == "reconciled":
                q["status"] = "Delivered"
                q["payment_batch_id"] = {"$exists": True, "$ne": None, "$ne": ""}
            elif bs == "unreconciled":
                q["status"] = "Delivered"
                q["$or"] = (q.get("$or") or []) + [
                    {"payment_batch_id": {"$exists": False}},
                    {"payment_batch_id": None},
                    {"payment_batch_id": ""},
                ]
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
            or_branches = [
                {"tracking_id":   {"$regex": search, "$options": "i"}},
                {"customer_name": {"$regex": search, "$options": "i"}},
                {"customer_phone": {"$regex": search, "$options": "i"}},
                {"city":          {"$regex": search, "$options": "i"}},
                {"order_id":      {"$regex": search, "$options": "i"}},
            ]
            # Group query tokens with their schwa-compact siblings —
            # produced by ONE call to normalize_text on the FULL
            # query so cross-word collapses fire ("100 gram" → "100g")
            # BEFORE the multilingual OR-grouping.  Each group is
            # OR-ed internally, groups are AND-ed together.
            grouped = normalize_tokens_grouped(search)
            if grouped:
                and_groups = []
                for variants in grouped:
                    if not variants:
                        continue
                    inner_or = [
                        {"_search_blob": {
                            "$regex": _re.escape(v),
                            "$options": "i",
                        }} for v in variants
                    ]
                    and_groups.append(
                        inner_or[0] if len(inner_or) == 1 else {"$or": inner_or}
                    )
                if and_groups:
                    combined = (
                        and_groups[0] if len(and_groups) == 1
                        else {"$and": and_groups}
                    )
                    or_branches.append(combined)
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
        # Phase F13 — Home Dashboard KPIs use the shared "eligible
        # business order" filter: excludes deleted, demo, cancelled/
        # returned rows and requires a customer identity.
        from lib.analytics_scope import (  # noqa: WPS433
            eligible_ship_match,
            normalize_status_expr,
        )

        uid = current_user["id"]
        # Home KPIs measure business volume — a row without a customer
        # identity still generated revenue, so we deliberately do NOT
        # gate on customer_name/phone here. We still exclude
        # cancelled/deleted/demo.
        eligible = eligible_ship_match(uid, require_customer_identity=False)

        # `total` = count of eligible business shipments.
        total = await db.shipments.count_documents(eligible)

        # Grouped status counts (case-normalised so "shipped" and
        # "Shipped" merge into a single bucket).
        by_status: Dict[str, int] = {}
        async for row in db.shipments.aggregate([
            {"$match": eligible},
            {"$addFields": {"_lc": normalize_status_expr()}},
            {"$group": {"_id": "$_lc", "count": {"$sum": 1}}},
        ]):
            by_status[row["_id"] or ""] = int(row["count"])
        delivered = by_status.get("delivered", 0)
        pending   = by_status.get("pending",   0)
        dispatch  = by_status.get("dispatch",  0)
        shipped   = by_status.get("shipped",   0)

        # Payment-mode revenue split — sums `amount` on eligible rows only.
        cod_sum = 0.0
        cod_count = 0
        prepaid_sum = 0.0
        prepaid_count = 0
        async for row in db.shipments.aggregate([
            {"$match": eligible},
            {"$addFields": {
                "_pm": {"$toUpper": {"$ifNull": ["$payment_mode", ""]}},
            }},
            {"$group": {
                "_id":   "$_pm",
                "sum":   {"$sum": {"$convert": {
                    "input": "$amount", "to": "double",
                    "onError": 0, "onNull": 0,
                }}},
                "count": {"$sum": 1},
            }},
        ]):
            pm = (row["_id"] or "").strip().upper()
            s = float(row.get("sum") or 0.0)
            c = int(row.get("count") or 0)
            if pm == "COD":
                cod_sum += s
                cod_count += c
            elif pm in ("PREPAID", "PAID"):
                prepaid_sum += s
                prepaid_count += c

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
        # shows fewer results than the badge promises.  Mirrors the
        # AND-of-OR grouping used by list_shipments via the shared
        # `normalize_tokens_grouped` helper so cross-word collapses
        # ("100 gram" → "100g") fire before multilingual OR-grouping.
        results = []
        for g in groups.values():
            if g["raw_count"] < min_count:
                continue
            grouped = normalize_tokens_grouped(str(g["display"]))
            if not grouped:
                continue
            and_groups = []
            for variants in grouped:
                if not variants:
                    continue
                inner_or = [
                    {"_search_blob": {
                        "$regex": _re.escape(v),
                        "$options": "i",
                    }} for v in variants
                ]
                and_groups.append(
                    inner_or[0] if len(inner_or) == 1 else {"$or": inner_or}
                )
            if not and_groups:
                continue
            q = {**base}
            combined = (
                and_groups[0] if len(and_groups) == 1
                else {"$and": and_groups}
            )
            if "$and" in combined:
                q["$and"] = combined["$and"]
            else:
                q.update(combined)
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

    # =================  Shipments — single / bulk  =====================
    # (Export endpoints — CSV + XLSX — extracted to
    # `routers/shipments_export.py` in Phase F4.6. Register that
    # router BEFORE the "/shipments/{id}" catch-all below.)

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

    # Phase F8.3 — Stage-aware WhatsApp message endpoint.
    # The Shipments-tab WhatsApp button used to send a single hard-
    # coded "Shipped tracking message" regardless of what stage the
    # order was actually in. This endpoint returns the CORRECT text
    # for the shipment's current status (Processing / Delivered /
    # Out for Delivery / etc.), using the admin-configured template
    # if present or the built-in catalogue default as a fallback.
    # Placeholders like {customer_name}, {order_id}, {tracking_id},
    # {courier_name}, {business_name} are resolved server-side against
    # the shipment row so the client can deep-link WhatsApp with the
    # ready-to-send text.
    @shipments_read_router.get(
        "/shipments/{shipment_id}/whatsapp-stage-message",
    )
    async def get_shipment_whatsapp_stage_message(
        shipment_id: str,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        doc = await db.shipments.find_one(
            {"user_id": current_user["id"], "id": shipment_id},
            {"_id": 0},
        )
        if not doc:
            raise HTTPException(status_code=404, detail="Not found")
        # Late import to avoid a circular dep at module load: this
        # router loads before whatsapp_provider seeds itself.
        from routers.whatsapp_provider import (  # noqa: WPS433
            resolve_stage_message as _resolve,
        )
        payload = await _resolve(
            db,
            doc,
            business_name=(current_user.get("shop_name")
                           or current_user.get("name") or ""),
            business_phone=(current_user.get("phone") or ""),
        )
        if not payload:
            # Terminal / unmapped status (e.g. Cancelled, Returned) —
            # tell the client to fall back to its legacy template.
            return {
                "ok":       False,
                "reason":   "no stage-event maps for this status",
                "status":   (doc.get("status") or ""),
                "message":  None,
            }
        return {"ok": True, **payload}

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
