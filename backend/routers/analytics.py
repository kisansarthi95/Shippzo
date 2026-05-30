"""
Admin + per-user Analytics router — Phase-I endpoints extracted from
server.py.

Extracted in Phase-29 (2026-05-30) as part of the server.py refactor.
No behavioural change — the route paths, query parameters, request
shapes, and response payloads are byte-for-byte identical to the
previous inline implementation.

Endpoints (all require a logged-in user; admin scope re-validated
at the route level for the platform-wide views):
  GET   /api/analytics/overview           (scope=mine | scope=platform)
  GET   /api/admin/analytics/overview     (legacy admin-only dashboard)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

_LOG = logging.getLogger("routers.analytics")

analytics_router = APIRouter(prefix="/api", tags=["admin-analytics"])


def _range_to_since(range_key: str) -> Optional[datetime]:
    """Convert a UI range key (today / 7d / 30d / 90d / all) to a UTC
    datetime cutoff. Returns None for `all` (no filter)."""
    now = datetime.now(timezone.utc)
    if range_key == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if range_key == "7d":
        return now - timedelta(days=7)
    if range_key == "30d":
        return now - timedelta(days=30)
    if range_key == "90d":
        return now - timedelta(days=90)
    return None


def init() -> None:
    """Late-bind handlers; pulls shared db + auth helpers out of
    server.py at first call rather than at import time so we don't
    take a circular dep."""
    from server import (  # noqa: WPS433
        db,
        get_current_user as _get_current_user,
        _require_admin as _require_admin_helper,
    )

    # ── Phase I.2 — Unified Analytics endpoint ──────────────────
    # `scope=mine` (default): scoped to current_user's shipments.
    # `scope=platform`: admin-only — across all users.
    # Filter params: courier, status, payment_mode, state. All optional.
    @analytics_router.get("/analytics/overview")
    async def analytics_overview(
        range_key: str = Query("30d", alias="range"),
        scope: str = Query("mine"),
        courier: Optional[str] = Query(None),
        status: Optional[str] = Query(None),
        payment_mode: Optional[str] = Query(None),
        state: Optional[str] = Query(None),
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Unified analytics — user's own data by default, admin can flip
        `scope=platform` to see platform-wide aggregates. Filters narrow
        every aggregation: courier, status, payment_mode, state."""
        is_admin = bool(current_user.get("is_admin"))
        if scope == "platform" and not is_admin:
            raise HTTPException(status_code=403, detail="Platform scope is admin-only.")

        since = _range_to_since(range_key)
        iso_since = since.isoformat() if since else None

        # Base shipment match (always excludes soft-deleted rows).
        ship_match: Dict[str, Any] = {"deleted_at": {"$exists": False}}
        if scope != "platform":
            ship_match["user_id"] = current_user["id"]
        if iso_since:
            ship_match["created_at"] = {"$gte": iso_since}
        if courier and courier != "all":
            ship_match["courier_name"] = courier
        if status and status != "all":
            ship_match["status"] = status
        if payment_mode and payment_mode != "all":
            # Accept "COD" / "Prepaid" / "PAID" — normalize.
            pm_norm = payment_mode.strip().upper()
            if pm_norm == "PAID":
                pm_norm = "PREPAID"
            ship_match["payment_mode"] = {"$in": [pm_norm, pm_norm.lower(), pm_norm.title()]}
        if state and state != "all":
            ship_match["state"] = {"$regex": f"^{state.strip()}$", "$options": "i"}

        total_shipments = await db.shipments.count_documents(ship_match)

        # By status -----------------------------------------------------
        by_status: Dict[str, int] = {}
        async for row in db.shipments.aggregate([
            {"$match": ship_match},
            {"$group": {"_id": "$status", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
        ]):
            by_status[row["_id"] or "Unknown"] = int(row["count"])

        delivered_count = sum(int(v) for k, v in by_status.items() if (k or "").lower() == "delivered")
        pending_count = total_shipments - delivered_count

        # By courier ----------------------------------------------------
        by_courier: List[Dict[str, Any]] = []
        async for row in db.shipments.aggregate([
            {"$match": ship_match},
            {"$group": {"_id": "$courier_name", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 10},
        ]):
            by_courier.append({"name": row["_id"] or "Unknown", "count": int(row["count"])})

        # By payment mode -----------------------------------------------
        by_payment: Dict[str, int] = {"COD": 0, "PREPAID": 0, "Other": 0}
        async for row in db.shipments.aggregate([
            {"$match": ship_match},
            {"$group": {"_id": "$payment_mode", "count": {"$sum": 1}}},
        ]):
            raw = (row["_id"] or "").strip().upper()
            if raw == "COD":
                by_payment["COD"] += int(row["count"])
            elif raw in ("PREPAID", "PAID"):
                by_payment["PREPAID"] += int(row["count"])
            else:
                by_payment["Other"] += int(row["count"])

        # Revenue (sum of `amount` field on shipments) ------------------
        revenue_total = 0
        revenue_cod = 0
        revenue_prepaid = 0
        async for row in db.shipments.aggregate([
            {"$match": ship_match},
            {"$group": {
                "_id": {"$toUpper": {"$ifNull": ["$payment_mode", ""]}},
                "sum": {"$sum": {"$convert": {"input": "$amount", "to": "double", "onError": 0, "onNull": 0}}},
            }},
        ]):
            sub = int(row.get("sum") or 0)
            revenue_total += sub
            pm = (row["_id"] or "").strip().upper()
            if pm == "COD":
                revenue_cod += sub
            elif pm in ("PREPAID", "PAID"):
                revenue_prepaid += sub

        # Top 8 states / cities -----------------------------------------
        by_state: List[Dict[str, Any]] = []
        async for row in db.shipments.aggregate([
            {"$match": ship_match},
            {"$group": {"_id": "$state", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 8},
        ]):
            by_state.append({"name": row["_id"] or "Unknown", "count": int(row["count"])})

        by_city: List[Dict[str, Any]] = []
        async for row in db.shipments.aggregate([
            {"$match": ship_match},
            {"$group": {"_id": "$city", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 8},
        ]):
            by_city.append({"name": row["_id"] or "Unknown", "count": int(row["count"])})

        # 30-day creation trend (same scope, ignores other filters so
        # the chart shape is stable). ----------------------------------
        trend_since = (datetime.now(timezone.utc) - timedelta(days=29)).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        trend_match: Dict[str, Any] = {
            "deleted_at": {"$exists": False},
            "created_at": {"$gte": trend_since.isoformat()},
        }
        if scope != "platform":
            trend_match["user_id"] = current_user["id"]
        if courier and courier != "all":
            trend_match["courier_name"] = courier
        by_day_raw: Dict[str, int] = {}
        async for row in db.shipments.aggregate([
            {"$match": trend_match},
            {"$group": {
                "_id":   {"$substr": ["$created_at", 0, 10]},
                "count": {"$sum": 1},
            }},
        ]):
            by_day_raw[row["_id"]] = int(row["count"])
        daily_trend = [
            {
                "date":  (trend_since + timedelta(days=i)).strftime("%Y-%m-%d"),
                "count": int(by_day_raw.get(
                    (trend_since + timedelta(days=i)).strftime("%Y-%m-%d"), 0)),
            }
            for i in range(30)
        ]

        # Filter option lists (so the UI can populate dropdowns) -------
        courier_options: List[str] = []
        async for row in db.shipments.aggregate([
            {"$match": (
                {"deleted_at": {"$exists": False}, "user_id": current_user["id"]}
                if scope != "platform" else {"deleted_at": {"$exists": False}}
            )},
            {"$group": {"_id": "$courier_name"}},
            {"$sort": {"_id": 1}},
            {"$limit": 30},
        ]):
            nm = (row["_id"] or "").strip()
            if nm:
                courier_options.append(nm)

        state_options: List[str] = []
        async for row in db.shipments.aggregate([
            {"$match": (
                {"deleted_at": {"$exists": False}, "user_id": current_user["id"]}
                if scope != "platform" else {"deleted_at": {"$exists": False}}
            )},
            {"$group": {"_id": "$state"}},
            {"$sort": {"_id": 1}},
            {"$limit": 50},
        ]):
            nm = (row["_id"] or "").strip()
            if nm:
                state_options.append(nm)

        status_options: List[str] = []
        async for row in db.shipments.aggregate([
            {"$match": (
                {"deleted_at": {"$exists": False}, "user_id": current_user["id"]}
                if scope != "platform" else {"deleted_at": {"$exists": False}}
            )},
            {"$group": {"_id": "$status"}},
        ]):
            nm = (row["_id"] or "").strip()
            if nm:
                status_options.append(nm)

        payload: Dict[str, Any] = {
            "range":   range_key,
            "scope":   scope,
            "since":   iso_since,
            "filters": {
                "courier":      courier or "all",
                "status":       status or "all",
                "payment_mode": payment_mode or "all",
                "state":        state or "all",
            },
            "filter_options": {
                "couriers": courier_options,
                "statuses": status_options,
                "states":   state_options,
            },
            "kpi": {
                "total":     total_shipments,
                "delivered": delivered_count,
                "pending":   pending_count,
                "revenue":   revenue_total,
                "revenue_cod":     revenue_cod,
                "revenue_prepaid": revenue_prepaid,
            },
            "shipments": {
                "total":      total_shipments,
                "by_status":  by_status,
                "by_courier": by_courier,
                "by_payment": by_payment,
                "by_state":   by_state,
                "by_city":    by_city,
            },
            "trend_30d": daily_trend,
        }

        # Admin-only platform-wide extras (users + system health). -----
        if scope == "platform":
            users_total = await db.users.count_documents({"deleted_at": {"$exists": False}})
            users_today = await db.users.count_documents({
                "created_at": {
                    "$gte": datetime.now(timezone.utc).replace(
                        hour=0, minute=0, second=0, microsecond=0,
                    ).isoformat(),
                },
                "deleted_at": {"$exists": False},
            })
            # Top 5 users by shipment volume in current ship_match.
            user_ids: List[str] = []
            top_user_rows: List[Dict[str, Any]] = []
            async for row in db.shipments.aggregate([
                {"$match": ship_match},
                {"$group": {"_id": "$user_id", "count": {"$sum": 1}}},
                {"$sort": {"count": -1}},
                {"$limit": 5},
            ]):
                user_ids.append(row["_id"])
                top_user_rows.append(row)
            user_lookup: Dict[str, Dict[str, Any]] = {}
            if user_ids:
                cur = db.users.find(
                    {"id": {"$in": user_ids}},
                    {"_id": 0, "id": 1, "email": 1, "name": 1},
                )
                async for u in cur:
                    user_lookup[u["id"]] = u
            top_users = [{
                "user_id": r["_id"],
                "name":    user_lookup.get(r["_id"], {}).get("name") or "",
                "email":   user_lookup.get(r["_id"], {}).get("email") or "—",
                "count":   int(r["count"]),
            } for r in top_user_rows]
            sla_open = await db.sla_alerts.count_documents({"dismissed": False})
            payload["admin"] = {
                "users":     {"total": users_total, "today": users_today},
                "top_users": top_users,
                "sla_open":  sla_open,
            }

        return payload

    # ── Legacy admin-only overview (still used by some dashboards) ──
    @analytics_router.get("/admin/analytics/overview")
    async def admin_analytics_overview(
        range_key: str = Query("30d", alias="range"),
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """One-shot dashboard payload — KPI cards, time-series, top lists."""
        _require_admin_helper(current_user)
        since = _range_to_since(range_key)
        iso_since = since.isoformat() if since else None
        today_midnight = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0,
        ).isoformat()
        seven_days_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

        # ── 1) Users ───────────────────────────────────
        users_total = await db.users.count_documents({"deleted_at": {"$exists": False}})
        users_today = await db.users.count_documents({
            "created_at": {"$gte": today_midnight},
            "deleted_at": {"$exists": False},
        })
        users_7d = await db.users.count_documents({
            "created_at": {"$gte": seven_days_ago},
            "deleted_at": {"$exists": False},
        })
        users_in_range = await db.users.count_documents({
            "created_at": {"$gte": iso_since} if iso_since else {"$exists": True},
            "deleted_at": {"$exists": False},
        })

        # Active = at least 1 shipment created in range.
        if iso_since:
            cur = db.shipments.aggregate([
                {"$match": {"created_at": {"$gte": iso_since}}},
                {"$group": {"_id": "$user_id"}},
                {"$count": "n"},
            ])
            first = await cur.to_list(length=1)
            active_users = (first[0]["n"] if first else 0)
        else:
            active_users = users_total

        # ── 2) Shipments ─────────────────────────────────
        ship_match: Dict[str, Any] = {"deleted_at": {"$exists": False}}
        if iso_since:
            ship_match["created_at"] = {"$gte": iso_since}
        total_shipments = await db.shipments.count_documents(ship_match)

        by_status: Dict[str, int] = {}
        async for row in db.shipments.aggregate([
            {"$match": ship_match},
            {"$group": {"_id": "$status", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
        ]):
            by_status[row["_id"] or "Unknown"] = int(row["count"])

        by_courier: List[Dict[str, Any]] = []
        async for row in db.shipments.aggregate([
            {"$match": ship_match},
            {"$group": {"_id": "$courier_name", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 7},
        ]):
            by_courier.append({"name": row["_id"] or "Unknown", "count": int(row["count"])})

        # ── 3) 30-day creation trend (irrespective of range) ─────────
        trend_since = (datetime.now(timezone.utc) - timedelta(days=29)).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        by_day_raw: Dict[str, int] = {}
        async for row in db.shipments.aggregate([
            {"$match": {
                "deleted_at": {"$exists": False},
                "created_at": {"$gte": trend_since.isoformat()},
            }},
            {"$group": {
                "_id":   {"$substr": ["$created_at", 0, 10]},
                "count": {"$sum": 1},
            }},
        ]):
            by_day_raw[row["_id"]] = int(row["count"])
        daily_trend = [
            {
                "date":  (trend_since + timedelta(days=i)).strftime("%Y-%m-%d"),
                "count": int(by_day_raw.get(
                    (trend_since + timedelta(days=i)).strftime("%Y-%m-%d"), 0)),
            }
            for i in range(30)
        ]

        # ── 4) Top 5 users by shipment volume in range ──────────────
        top_user_rows = []
        user_ids: List[str] = []
        async for row in db.shipments.aggregate([
            {"$match": ship_match},
            {"$group": {"_id": "$user_id", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 5},
        ]):
            user_ids.append(row["_id"])
            top_user_rows.append(row)
        user_lookup: Dict[str, Dict[str, Any]] = {}
        if user_ids:
            cur = db.users.find(
                {"id": {"$in": user_ids}},
                {"_id": 0, "id": 1, "email": 1, "name": 1},
            )
            async for u in cur:
                user_lookup[u["id"]] = u
        top_users = [{
            "user_id": r["_id"],
            "name":    user_lookup.get(r["_id"], {}).get("name") or "",
            "email":   user_lookup.get(r["_id"], {}).get("email") or "—",
            "count":   int(r["count"]),
        } for r in top_user_rows]

        # ── 5) SLA health ────────────────────────────────
        sla_open = await db.sla_alerts.count_documents({"dismissed": False})
        if iso_since:
            sla_dismissed_in_range = await db.sla_alerts.count_documents({
                "dismissed":    True,
                "dismissed_at": {"$gte": iso_since},
            })
        else:
            sla_dismissed_in_range = await db.sla_alerts.count_documents({"dismissed": True})

        # ── 6) WhatsApp activity (today) ─────────────────────
        today_yyyy_mm_dd = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        wa_today_total = 0
        async for row in db.settings.aggregate([
            {"$match": {"wa_daily_counter.day": today_yyyy_mm_dd}},
            {"$group": {"_id": None, "total": {"$sum": "$wa_daily_counter.count"}}},
        ]):
            wa_today_total = int(row.get("total") or 0)

        # ── 7) Sheet-sync health (across all users) ──────────────
        sheet_counts: Dict[str, int] = {}
        async for row in db.shipments.aggregate([
            {"$match": {"deleted_at": {"$exists": False}}},
            {"$group": {"_id": "$user_sheet_sync_status", "count": {"$sum": 1}}},
        ]):
            sheet_counts[row["_id"] or "never"] = int(row["count"])
        queue_pending = await db.user_sheet_sync_pending.count_documents({})
        sheets_connected = await db.settings.count_documents({
            "sheet.sheet_id": {"$exists": True, "$ne": ""},
        })

        # ── 8) Revenue (best-effort; tolerates missing `payments` coll) ─
        revenue_total = 0
        revenue_in_range = 0
        try:
            async for row in db.payments.aggregate([
                {"$match": {"status": "captured"}},
                {"$group": {"_id": None, "sum": {"$sum": "$amount_inr"}}},
            ]):
                revenue_total = int(row.get("sum") or 0)
            match_range: Dict[str, Any] = {"status": "captured"}
            if iso_since:
                match_range["created_at"] = {"$gte": iso_since}
            async for row in db.payments.aggregate([
                {"$match": match_range},
                {"$group": {"_id": None, "sum": {"$sum": "$amount_inr"}}},
            ]):
                revenue_in_range = int(row.get("sum") or 0)
        except Exception:
            pass

        return {
            "range": range_key,
            "since": iso_since,
            "users": {
                "total":       users_total,
                "today":       users_today,
                "last_7_days": users_7d,
                "in_range":    users_in_range,
                "active":      active_users,
            },
            "shipments": {
                "total":      total_shipments,
                "by_status":  by_status,
                "by_courier": by_courier,
            },
            "trend_30d": daily_trend,
            "top_users": top_users,
            "sla": {
                "open":               sla_open,
                "dismissed_in_range": sla_dismissed_in_range,
            },
            "whatsapp": {
                "messages_today": wa_today_total,
            },
            "sheet_sync": {
                "connected_users": sheets_connected,
                "counts":          sheet_counts,
                "queue_pending":   queue_pending,
            },
            "revenue": {
                "total":    revenue_total,
                "in_range": revenue_in_range,
                "currency": "INR",
            },
        }


__all__ = ["analytics_router", "init", "_range_to_since"]
