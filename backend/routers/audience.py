"""
Audience — Phase F12 (Audience Hub).

Aggregates customer intelligence directly from the `shipments`
collection (the source of truth for real order history). Unlike the
older `/api/me/customers` endpoint — which reads from a webhook-fed
`customers` collection — this router derives audience data from the
actual dispatched shipments, grouped by customer phone number.

Endpoints (all under /api):

  GET  /me/audience/stats            counts: all / new / returning / imported
  GET  /me/audience                  list customers with orders/spend
  GET  /me/audience/{customer_key}   single customer + full order history

`customer_key` is the URL-encoded normalized phone number
(digits only, last-10 preferred). When the phone is missing, we
fallback to a `name:` prefix + slugified customer_name.

Filter semantics:
  - all       : every unique customer (with phone OR name).
  - new       : orders_count == 1
  - returning : orders_count >= 2
  - imported  : any of their shipments has non-empty `import_batch_ids`

Sales / spend definition:
  - `total_sales` sums the `amount` field ONLY when the order status
    is "Delivered" (successful). This matches the user's intent:
    "કુલ સફળ ઓર્ડરના ખર્ચનો સરવાળો".

Pattern: late-binding `init()` — same as other routers/.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query


audience_router = APIRouter(prefix="/api", tags=["audience"])


_DIGITS_RX = re.compile(r"\d+")


def _normalize_phone(phone: Optional[str]) -> str:
    """Return the last 10 digits of the phone if available, else ''."""
    if not phone:
        return ""
    digits = "".join(_DIGITS_RX.findall(phone))
    if not digits:
        return ""
    return digits[-10:] if len(digits) >= 10 else digits


def _customer_key(phone: str, name: str) -> str:
    """Deterministic per-user key. Phone wins; fallback to name slug."""
    ph = _normalize_phone(phone)
    if ph:
        return ph
    nm = (name or "").strip().lower()
    if not nm:
        return ""
    # Slug the name so it survives URL encoding safely.
    slug = re.sub(r"[^a-z0-9]+", "-", nm).strip("-")
    return f"name:{slug}" if slug else ""


def _format_customer_row(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Serialise a single grouped-customer aggregation row."""
    phone = str(doc.get("phone") or "").strip()
    name = str(doc.get("name") or "").strip()
    key = _customer_key(phone, name)
    return {
        "key":           key,
        "customer_name": name,
        "customer_phone": phone,
        "customer_email": str(doc.get("email") or "").strip(),
        "city":          str(doc.get("city") or "").strip(),
        "state":         str(doc.get("state") or "").strip(),
        "address":       str(doc.get("address") or "").strip(),
        "pincode":       str(doc.get("pincode") or "").strip(),
        "orders_count":  int(doc.get("orders_count") or 0),
        "delivered_count": int(doc.get("delivered_count") or 0),
        "total_sales":   round(float(doc.get("total_sales") or 0.0), 2),
        "is_imported":   bool(doc.get("any_imported") or False),
        "last_order_at": str(doc.get("last_order_at") or ""),
    }


def init() -> None:
    _logger = logging.getLogger("routers.audience")
    from server import (  # noqa: WPS433
        db,
        get_current_user as _get_current_user,
    )

    async def _aggregate_customers(
        uid: str,
        search: str = "",
    ) -> List[Dict[str, Any]]:
        """Full aggregation pipeline over shipments → grouped customers."""
        match: Dict[str, Any] = {"user_id": uid}
        # Only include shipments with at least a customer_name OR phone —
        # otherwise it's a corrupt row.
        match["$or"] = [
            {"customer_phone": {"$exists": True, "$ne": ""}},
            {"customer_name":  {"$exists": True, "$ne": ""}},
        ]
        if search:
            qs = re.escape(search.strip())
            match["$and"] = [{"$or": [
                {"customer_name":  {"$regex": qs, "$options": "i"}},
                {"customer_phone": {"$regex": qs, "$options": "i"}},
                {"customer_email": {"$regex": qs, "$options": "i"}},
            ]}]

        pipeline: List[Dict[str, Any]] = [
            {"$match": match},
            # Normalise phone: strip non-digits then take last 10.
            # Mongo doesn't have a regex-strip so we approximate:
            # use $substrCP with $strLenCP fallback. When phone is
            # already a clean digit string this works; when it has
            # spaces/prefixes we fall back to grouping by the raw
            # `customer_phone` and let the Python-side re-merge later.
            {"$addFields": {
                "_phone_raw": {"$ifNull": ["$customer_phone", ""]},
            }},
            {"$group": {
                "_id": "$_phone_raw",
                "name":            {"$last": "$customer_name"},
                "email":           {"$last": "$customer_email"},
                "city":            {"$last": "$city"},
                "state":           {"$last": "$state"},
                "address":         {"$last": "$address"},
                "pincode":         {"$last": "$pincode"},
                "phone":           {"$last": "$customer_phone"},
                "orders_count":    {"$sum": 1},
                "delivered_count": {"$sum": {
                    "$cond": [{"$eq": ["$status", "Delivered"]}, 1, 0],
                }},
                "total_sales":     {"$sum": {
                    "$cond": [
                        {"$eq": ["$status", "Delivered"]},
                        {"$ifNull": ["$amount", 0]},
                        0,
                    ],
                }},
                "any_imported":    {"$max": {
                    "$cond": [
                        {"$gt": [
                            {"$size": {"$ifNull": ["$import_batch_ids", []]}},
                            0,
                        ]},
                        1,
                        0,
                    ],
                }},
                "last_order_at":   {"$max": "$created_at"},
            }},
        ]
        raw_rows: List[Dict[str, Any]] = []
        async for r in db.shipments.aggregate(pipeline):
            raw_rows.append(r)

        # Re-merge by normalized phone (Python side) so that the same
        # customer typed as "+91 98..." and "98..." get merged.
        merged: Dict[str, Dict[str, Any]] = {}
        for r in raw_rows:
            key = _customer_key(r.get("phone") or "", r.get("name") or "")
            if not key:
                continue
            if key not in merged:
                merged[key] = {
                    "phone":           r.get("phone") or "",
                    "name":            r.get("name") or "",
                    "email":           r.get("email") or "",
                    "city":            r.get("city") or "",
                    "state":           r.get("state") or "",
                    "address":         r.get("address") or "",
                    "pincode":         r.get("pincode") or "",
                    "orders_count":    int(r.get("orders_count") or 0),
                    "delivered_count": int(r.get("delivered_count") or 0),
                    "total_sales":     float(r.get("total_sales") or 0.0),
                    "any_imported":    bool(r.get("any_imported")),
                    "last_order_at":   r.get("last_order_at") or "",
                }
            else:
                m = merged[key]
                m["orders_count"]    += int(r.get("orders_count") or 0)
                m["delivered_count"] += int(r.get("delivered_count") or 0)
                m["total_sales"]     += float(r.get("total_sales") or 0.0)
                m["any_imported"]    = m["any_imported"] or bool(r.get("any_imported"))
                # Prefer the most-recent row for identity fields.
                if str(r.get("last_order_at") or "") > str(m.get("last_order_at") or ""):
                    m["last_order_at"] = r.get("last_order_at") or ""
                    for f in ("name", "email", "city", "state", "address", "pincode", "phone"):
                        if r.get(f):
                            m[f] = r[f]

        rows = list(merged.values())
        rows.sort(key=lambda x: str(x.get("last_order_at") or ""), reverse=True)
        return rows

    # ─────────────────────────────  STATS  ────────────────────────────

    @audience_router.get("/me/audience/stats")
    async def audience_stats(
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        rows = await _aggregate_customers(current_user["id"], "")
        total     = len(rows)
        new_c     = sum(1 for r in rows if r.get("orders_count") == 1)
        returning = sum(1 for r in rows if r.get("orders_count", 0) >= 2)
        imported  = sum(1 for r in rows if r.get("any_imported"))
        # VIP = customers with delivered_count >= 1 AND non-zero sales.
        vip       = sum(
            1 for r in rows
            if float(r.get("total_sales") or 0) > 0
            and int(r.get("delivered_count") or 0) >= 1
        )
        return {
            "all":       total,
            "new":       new_c,
            "returning": returning,
            "imported":  imported,
            "vip":       vip,
        }

    # ─────────────────────────────  LIST  ─────────────────────────────

    @audience_router.get("/me/audience")
    async def list_audience(
        segment: str = Query(default="all", regex="^(all|new|returning|imported|vip)$"),
        q:       Optional[str] = Query(default=None),
        limit:   int = Query(default=100, ge=1, le=500),
        offset:  int = Query(default=0, ge=0),
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        rows = await _aggregate_customers(current_user["id"], (q or "").strip())

        # Segment filter.
        if segment == "new":
            rows = [r for r in rows if r.get("orders_count") == 1]
        elif segment == "returning":
            rows = [r for r in rows if r.get("orders_count", 0) >= 2]
        elif segment == "imported":
            rows = [r for r in rows if r.get("any_imported")]
        elif segment == "vip":
            # Phase F12.1 — VIP Leaderboard: customers ranked by
            # lifetime sales (delivered only). Requires at least one
            # successful delivery + positive sales.
            rows = [
                r for r in rows
                if float(r.get("total_sales") or 0) > 0
                and int(r.get("delivered_count") or 0) >= 1
            ]
            rows.sort(
                key=lambda r: float(r.get("total_sales") or 0),
                reverse=True,
            )

        total = len(rows)
        page = rows[offset:offset + limit]

        # For VIP segment, stamp a 1-based rank so the UI can render
        # 🥇 🥈 🥉 badges without recomputing on the client.
        serialised = [_format_customer_row(r) for r in page]
        if segment == "vip":
            for i, row in enumerate(serialised):
                row["rank"] = offset + i + 1
        return {
            "customers": serialised,
            "count":     len(page),
            "total":     total,
            "segment":   segment,
        }

    # ─────────────────────────  SINGLE PROFILE  ───────────────────────

    @audience_router.get("/me/audience/{customer_key}")
    async def get_audience_profile(
        customer_key: str = Path(..., min_length=1),
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Full profile: identity + summary + order history."""
        uid = current_user["id"]

        # Fetch all shipments whose normalized phone (or name slug)
        # matches the requested key.
        key = customer_key.strip()
        if not key:
            raise HTTPException(status_code=400, detail="customer_key required")

        match: Dict[str, Any] = {"user_id": uid}
        if key.startswith("name:"):
            slug = key[5:]
            # Best-effort: recover the raw name by loose regex.
            # We convert slug hyphens back to a permissive pattern.
            words = [w for w in slug.split("-") if w]
            if not words:
                raise HTTPException(status_code=404, detail="Customer not found")
            rx = ".*".join(re.escape(w) for w in words)
            match["customer_name"] = {"$regex": f"^{rx}$", "$options": "i"}
            match["$or"] = [
                {"customer_phone": {"$exists": False}},
                {"customer_phone": ""},
                {"customer_phone": None},
            ]
        else:
            # Phone match: last-10-digits regex tail match.
            tail = re.sub(r"\D", "", key)[-10:]
            if not tail:
                raise HTTPException(status_code=404, detail="Customer not found")
            match["customer_phone"] = {"$regex": f"{tail}$"}

        cur = db.shipments.find(match, {"_id": 0}).sort("created_at", -1)
        ships: List[Dict[str, Any]] = await cur.to_list(1000)
        if not ships:
            raise HTTPException(status_code=404, detail="Customer not found")

        # Compose the profile from the most-recent row.
        latest = ships[0]
        orders_count    = len(ships)
        delivered       = [s for s in ships if s.get("status") == "Delivered"]
        delivered_count = len(delivered)
        total_sales     = round(sum(float(s.get("amount") or 0) for s in delivered), 2)
        any_imported    = any((s.get("import_batch_ids") or []) for s in ships)

        # Serialise the order list — keep it compact for the list.
        history = [
            {
                "id":              s.get("id"),
                "tracking_id":     s.get("tracking_id") or "",
                "order_id":        s.get("order_id") or s.get("master_order_id") or "",
                "status":          s.get("status") or "",
                "amount":          float(s.get("amount") or 0),
                "cod_amount":      float(s.get("cod_amount") or 0),
                "payment_mode":    s.get("payment_mode") or "",
                "created_at":      s.get("created_at") or "",
                "delivered_at":    s.get("delivered_at") or "",
                "courier_name":    s.get("courier_name") or "",
                "items":           s.get("items") or [],
                "city":            s.get("city") or "",
                "state":           s.get("state") or "",
                "is_imported":     bool(s.get("import_batch_ids")),
            }
            for s in ships
        ]

        default_address = ", ".join(
            [x for x in (
                latest.get("address"),
                latest.get("city"),
                latest.get("state"),
                latest.get("pincode"),
            ) if x]
        )

        return {
            "key":            _customer_key(
                latest.get("customer_phone") or "",
                latest.get("customer_name") or "",
            ),
            "customer_name":  latest.get("customer_name") or "",
            "customer_phone": latest.get("customer_phone") or "",
            "customer_email": latest.get("customer_email") or "",
            "city":           latest.get("city") or "",
            "state":          latest.get("state") or "",
            "address":        latest.get("address") or "",
            "pincode":        latest.get("pincode") or "",
            "default_address": default_address,
            "orders_count":   orders_count,
            "delivered_count": delivered_count,
            "total_sales":    total_sales,
            "is_imported":    any_imported,
            "first_order_at": ships[-1].get("created_at") or "" if ships else "",
            "last_order_at":  latest.get("created_at") or "",
            "orders":         history,
        }

    _logger.info("audience router mounted (3 endpoints)")
