"""
Analytics Scope — single source of truth for "eligible business orders".

Phase F13 (Aug-2026) — Fixes the long-standing analytics-integrity bug
where Dashboard, Audience, Reports, and Top-Customers rankings each
applied their own subtly-different filter (or none at all). Cancelled
orders were being counted in COD/Prepaid/Revenue totals; test/demo
rows leaked into VIP rankings; ranking endpoints truncated at 5000
rows *before* sorting — silently dropping top spenders.

## Eligible Business Order
An "eligible" shipment for KPI/analytics purposes is:

  1. **Not soft-deleted**   → `deleted_at` does NOT exist on the doc.
  2. **Not a demo/test row** → `is_demo` is NOT truthy.
  3. **Not a terminal-cancelled status** — the status (case-insensitive,
     whitespace-trimmed) must not be in the terminal set:
       {"Cancelled", "Cancel by buyer", "Returned"}
  4. **Has a customer identity** — either `customer_name` or
     `customer_phone` must be present (non-empty). Rows lacking any
     customer identity are considered corrupt/junk.

The eligible predicate is applied via `eligible_ship_match()` which
returns a MongoDB filter object. All aggregation pipelines across the
app MUST start from this filter — DO NOT bake status/is_demo checks
inline in individual routers; extend this module instead.

## Ranking Contract
Any "top N" ranking (top customers, top orders, top states…) MUST:
  - Perform `$match → $group → $sort → $limit` **inside** MongoDB.
  - Never `.to_list(5000)` and then Python-sort. That truncates before
    the sort executes and silently drops rows with values larger than
    the arbitrary cap.

## Status Normalisation
When grouping BY status (e.g. "orders per status"), the pipeline
should call `$toLower` on the status field so `"shipped"` and
`"Shipped"` merge to a single bucket. Individual routers can then
Title-case for display.

## Callers (as of Phase F13)
  * routers/analytics.py            — /api/analytics/overview
  * routers/audience.py             — /api/me/audience[/stats]
  * routers/reports.py              — courier-billing, weight-wise,
                                       partner-comparison, reconciliation
  * routers/customers.py            — /api/me/customers/stats
  * routers/shipments_read.py       — /api/shipments/stats (Home KPIs)
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

# Import the canonical terminal-states set so a future change to the
# terminal vocabulary (e.g. adding "Refunded") propagates here for free.
try:
    from lib.terminal_states import TERMINAL_SHIPMENT_STATUSES  # type: ignore
except Exception:  # pragma: no cover — module always present in prod
    TERMINAL_SHIPMENT_STATUSES = {"Cancelled", "Cancel by buyer", "Returned"}


# Both cased and lowercase variants — we compare with $nin in Mongo
# after lower-casing the field, which is the cleanest way to survive
# legacy casing drift like "cancelled" vs "Cancelled".
_TERMINAL_LC = tuple(sorted({s.lower() for s in TERMINAL_SHIPMENT_STATUSES}))


def eligible_ship_match(
    user_id: Optional[str] = None,
    *,
    extra: Optional[Dict[str, Any]] = None,
    require_customer_identity: bool = True,
    include_deleted: bool = False,
    include_demo: bool = False,
    include_cancelled: bool = False,
) -> Dict[str, Any]:
    """Return the base Mongo `$match` filter for eligible shipments.

    Parameters
    ----------
    user_id : optional str
        Scope to a single tenant. Omit for admin platform-wide queries.
    extra : dict
        Additional filter clauses to `$and` into the match (courier,
        date range, payment_mode, etc.).
    require_customer_identity : bool (default True)
        When True, exclude shipments that have neither a
        `customer_name` nor a `customer_phone`. Turn OFF only when the
        caller specifically needs raw shipment counts (e.g. duplicate
        detection tools).
    include_deleted : bool (default False)
        Set True to allow soft-deleted rows. Almost never wanted.
    include_demo : bool (default False)
        Set True to include `is_demo:true` rows. Almost never wanted.
    include_cancelled : bool (default False)
        Set True to also count Cancelled/Returned/Cancel by buyer.
        Useful for a router that wants the raw pipeline shape but not
        the analytics filter — most callers should leave this False.
    """
    match: Dict[str, Any] = {}
    if user_id:
        match["user_id"] = user_id

    if not include_deleted:
        match["deleted_at"] = {"$exists": False}

    if not include_demo:
        # $ne matches both missing field and false — Mongo treats
        # missing as "not equal to true".
        match["is_demo"] = {"$ne": True}

    if not include_cancelled:
        # Compare status lowercased against the terminal set.
        # `$not.$regex` with an anchored alternation is the cheapest
        # single-clause way to say "status NOT any of these".
        # We also handle NULL/missing status defensively — those rows
        # are NOT terminal, so they still qualify.
        alt = "|".join(_TERMINAL_LC)
        match["$expr"] = {
            "$not": {
                "$in": [
                    {"$toLower": {"$ifNull": ["$status", ""]}},
                    list(_TERMINAL_LC),
                ],
            },
        }

    if require_customer_identity:
        # At least one of customer_name / customer_phone must be a
        # non-empty string.
        match["$or"] = [
            {"customer_name":  {"$exists": True, "$nin": ["", None]}},
            {"customer_phone": {"$exists": True, "$nin": ["", None]}},
        ]

    if extra:
        # Merge extra clauses. If both sides declare `$expr` or `$or`
        # we fold them together to avoid clobbering.
        for k, v in extra.items():
            if k == "$expr" and "$expr" in match:
                match["$expr"] = {"$and": [match["$expr"], v]}
            elif k == "$or" and "$or" in match:
                # Wrap both `$or` groups inside an `$and` so we don't
                # broaden the match — both groups must hold.
                match.setdefault("$and", []).append({"$or": match.pop("$or")})
                match["$and"].append({"$or": v})
            elif k == "$and" and "$and" in match:
                match["$and"] = list(match["$and"]) + list(v)
            else:
                match[k] = v
    return match


def is_eligible_status(status: Optional[str]) -> bool:
    """True when the given status string is NOT terminal-cancelled.
    Empty/None counts as eligible (pending-with-no-status still counts)."""
    if not status:
        return True
    return str(status).strip().lower() not in _TERMINAL_LC


def normalize_status_expr() -> Dict[str, Any]:
    """MongoDB `$addFields` snippet that adds a normalised `_status_lc`
    field to each document. Useful for pipelines that group BY status
    so `"shipped"` and `"Shipped"` merge to one bucket.

    Usage:
        pipeline = [
            {"$match": eligible_ship_match(uid)},
            {"$addFields": {"_status_lc": normalize_status_expr()}},
            {"$group": {"_id": "$_status_lc", "count": {"$sum": 1}}},
        ]
    """
    return {"$toLower": {"$ifNull": ["$status", ""]}}


def title_case_status(status: Optional[str]) -> str:
    """Best-effort UI-facing title case for a raw status string.
    Handles the known drift ('shipped' → 'Shipped')."""
    if not status:
        return ""
    s = str(status).strip()
    # Preserve multi-word statuses like "Out for Delivery" — use
    # per-word title case rather than a bare .capitalize().
    return " ".join(w.capitalize() for w in s.split())


__all__ = [
    "eligible_ship_match",
    "is_eligible_status",
    "normalize_status_expr",
    "title_case_status",
    "TERMINAL_SHIPMENT_STATUSES",
]
