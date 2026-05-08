"""
SLA Engine — Phase G3
=====================
Scans shipments hourly, detects per-stage SLA breaches, applies the
admin's cooldown / escalation rules, and writes an entry to the
`sla_alerts` collection per (shipment, stage) breach.

The actual WhatsApp send happens CLIENT-SIDE when the admin opens the
"Action Required" widget — we don't have a WhatsApp Business API key,
so the server's job ends at "alert recorded".

Key tables:
  shipments            — read; we look at status + stage timestamps
  admin_config         — stage_rules + alert recipients
  sla_alerts           — new collection; one doc per breach
                         { user_id, shipment_id, stage, raised_at,
                           level (1/2/3 escalation), recipients,
                           priority, channel, template, vars,
                           dismissed: bool, last_sent_at }
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from stage_rules import (
    DEFAULT_STAGE_RULES_DOC,
    STAGES,
    STAGE_TO_TEMPLATE,
    merge_with_defaults as _merge_stage_rules,
)

_LOG = logging.getLogger(__name__)


# Stage → field on the shipment doc that stores when the shipment
# entered that stage. Falls back to created_at when missing.
_STAGE_TIMESTAMP_FIELD: Dict[str, str] = {
    "Pending":       "created_at",
    "Processing":    "processing_started_at",
    "Ready to Ship": "ready_to_ship_at",
    "Shipped":       "shipped_at",
    "Delivered":     "delivered_at",
    "Feedback":      "delivered_at",     # feedback SLA counts from delivery
}

# Map shipment status field values → canonical stage names. Includes
# legacy aliases ("Dispatch", "Sent", etc) so older data still routes.
_STATUS_TO_STAGE: Dict[str, str] = {
    "pending":         "Pending",
    "Pending":         "Pending",
    "processing":      "Processing",
    "Processing":      "Processing",
    "ready_to_ship":   "Ready to Ship",
    "Ready to Ship":   "Ready to Ship",
    "Ready":           "Ready to Ship",
    "shipped":         "Shipped",
    "Shipped":         "Shipped",
    "Dispatch":        "Shipped",       # legacy alias
    "Sent":            "Shipped",       # legacy alias
    "delivered":       "Delivered",
    "Delivered":       "Delivered",
    # Feedback isn't a real shipment status — it's a derived stage
    # (Delivered + N days). Handled separately in the scan loop.
}


def _parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        s = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hours_between(a: datetime, b: datetime) -> float:
    return (a - b).total_seconds() / 3600.0


def _days_between(a: datetime, b: datetime) -> float:
    return _hours_between(a, b) / 24.0


def _escalation_level(days_overdue: float, escalation: List[Dict[str, Any]]) -> int:
    """Returns 1-based escalation level for current days_overdue.
    Higher day_after_sla wins. Default 1 if no escalation steps."""
    level = 1
    for idx, step in enumerate(escalation or []):
        if days_overdue >= int(step.get("day_after_sla") or 0):
            level = idx + 1
    return level


def _resolve_recipients(
    rules: Dict[str, Any],
    recipient_keys: List[str],
) -> Dict[str, List[str]]:
    """Translates ['admin', 'team'] selector → actual phone numbers and
    push-user-ids using the global recipients block from stage_rules."""
    phones: List[str] = []
    push_ids: List[str] = []
    if "admin" in (recipient_keys or []):
        admin_n = str(rules.get("alert_admin_number") or "").strip()
        if admin_n:
            phones.append(admin_n)
        push_ids.extend(rules.get("alert_app_user_ids") or [])
    if "team" in (recipient_keys or []):
        phones.extend(rules.get("alert_team_numbers") or [])
    # De-dupe while preserving order.
    seen_p, seen_u = set(), set()
    phones = [p for p in phones if p and p not in seen_p and not seen_p.add(p)]
    push_ids = [u for u in push_ids if u and u not in seen_u and not seen_u.add(u)]
    return {"phones": phones, "push_ids": push_ids}


async def scan_user_shipments(
    db,
    *,
    user_id: str,
    rules: Dict[str, Any],
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Scans one user's shipments and returns the list of NEW alerts
    that should be raised right now. Does NOT write them — the caller
    persists into `sla_alerts`. Cooldown and dedup are applied here.

    Returns a list of dicts ready to insert into `sla_alerts`.
    """
    now = now or _now()
    stages_cfg = rules.get("stages") or {}
    if not rules.get("global_enabled", True):
        return []

    # Pull every active shipment for this user — exclude already-Delivered
    # ones for non-feedback stages. Feedback scan is separate below.
    cursor = db.shipments.find(
        {"user_id": user_id},
        {
            "_id": 0, "id": 1, "status": 1,
            "created_at": 1, "processing_started_at": 1,
            "ready_to_ship_at": 1, "shipped_at": 1, "delivered_at": 1,
            "customer_name": 1, "customer_phone": 1, "order_id": 1,
            "tracking_id": 1, "items": 1, "courier_name": 1,
            "sla_alert_log": 1,
        },
    )
    rows = await cursor.to_list(5000)

    alerts: List[Dict[str, Any]] = []
    for s in rows:
        stage = _STATUS_TO_STAGE.get(str(s.get("status") or "").strip())
        if not stage or stage not in STAGES:
            continue

        # ── Per-stage SLA check ────────────────────────────────────
        cfg = stages_cfg.get(stage) or {}
        if not cfg.get("alert_enabled", False):
            continue
        sla_days = int(cfg.get("sla_days") or 0)
        if sla_days <= 0:
            continue
        ts_field = _STAGE_TIMESTAMP_FIELD.get(stage, "created_at")
        ts = _parse_iso(s.get(ts_field)) or _parse_iso(s.get("created_at"))
        if not ts:
            continue
        days_in_stage = _days_between(now, ts)
        if days_in_stage < sla_days:
            continue
        days_overdue = days_in_stage - sla_days

        # Cooldown: skip if last alert for this (shipment, stage) was
        # raised within `cooldown_hours`.
        cooldown_h = int(cfg.get("cooldown_hours") or 24)
        log = (s.get("sla_alert_log") or {}).get(stage) or {}
        last_iso = log.get("last_at")
        last_dt = _parse_iso(last_iso) if last_iso else None
        if last_dt and _hours_between(now, last_dt) < cooldown_h:
            continue

        level = _escalation_level(days_overdue, cfg.get("escalation") or [])
        # Pick the escalation step matching `level-1`, fallback to base config.
        steps = cfg.get("escalation") or []
        step = steps[level - 1] if 0 <= level - 1 < len(steps) else None
        recipients_keys = (
            (step or {}).get("recipients")
            or cfg.get("alert_recipients")
            or ["admin"]
        )
        priority = (step or {}).get("priority") or cfg.get("alert_priority") or "medium"
        recips = _resolve_recipients(rules, recipients_keys)

        alerts.append({
            "id":           str(uuid.uuid4()),
            "user_id":      user_id,
            "shipment_id":  s["id"],
            "stage":        stage,
            "raised_at":    now.isoformat(),
            "days_overdue": round(days_overdue, 2),
            "sla_days":     sla_days,
            "level":        level,
            "priority":     priority,
            "channel":      cfg.get("alert_channel") or "both",
            "recipients":   recipients_keys,
            "phones":       recips["phones"],
            "push_ids":     recips["push_ids"],
            "dismissed":    False,
            "shipment": {
                "customer_name":  s.get("customer_name") or "",
                "customer_phone": s.get("customer_phone") or "",
                "order_id":       s.get("order_id") or "",
                "tracking_id":    s.get("tracking_id") or "",
                "items":          s.get("items") or [],
                "courier_name":   s.get("courier_name") or "",
            },
        })

    return alerts


async def scan_all_users(db, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Top-level cron entry point. Returns aggregated counts."""
    now = now or _now()
    rules_doc = await db.admin_config.find_one(
        {"_id": "default"}, {"_id": 0, "stage_rules": 1},
    ) or {}
    rules = _merge_stage_rules(rules_doc.get("stage_rules") or {})
    if not rules.get("global_enabled", True):
        _LOG.info("SLA scan: global_enabled=False — skipping")
        return {"users_scanned": 0, "alerts_raised": 0, "skipped": True}

    # All distinct users that own at least one shipment.
    users = await db.shipments.distinct("user_id")
    total_raised = 0
    for uid in users:
        try:
            new_alerts = await scan_user_shipments(
                db, user_id=str(uid), rules=rules, now=now,
            )
        except Exception as e:
            _LOG.warning("SLA scan failed for user %s: %s", uid, e)
            continue
        if not new_alerts:
            continue

        # Persist alerts and stamp the per-shipment cooldown log.
        await db.sla_alerts.insert_many(new_alerts)
        # Update sla_alert_log.<stage>.last_at on each shipment.
        for a in new_alerts:
            try:
                await db.shipments.update_one(
                    {"user_id": uid, "id": a["shipment_id"]},
                    {"$set": {
                        f"sla_alert_log.{a['stage']}.last_at": a["raised_at"],
                        f"sla_alert_log.{a['stage']}.level":   a["level"],
                    }},
                )
            except Exception as e:
                _LOG.warning("sla_alert_log update failed: %s", e)
        total_raised += len(new_alerts)

        # Phase G6 — push notification dispatch (best-effort).
        # Only fire when the admin has the `push` channel enabled in
        # display_channels AND the user opted into `sla_breach`.
        try:
            display = (rules.get("display_channels") or {})
            if display.get("push", False):
                # Group counts by stage for a single concise body.
                by_stage: Dict[str, int] = {}
                for a in new_alerts:
                    by_stage[a["stage"]] = by_stage.get(a["stage"], 0) + 1
                body_parts = [f"{n} {s}" for s, n in by_stage.items()]
                title = f"{len(new_alerts)} SLA breach{'es' if len(new_alerts) != 1 else ''}"
                body  = " · ".join(body_parts) + " — tap to review"
                # Late import to avoid cycle (server.py imports sla_engine).
                try:
                    from server import _push_event
                    await _push_event(
                        [uid],
                        event_key="sla_breach",
                        title=title, body=body,
                        data={"type": "sla_breach", "count": len(new_alerts)},
                    )
                except Exception as e:
                    _LOG.warning("SLA push dispatch failed for %s: %s", uid, e)
        except Exception:
            pass  # never let push fail the scan

        _LOG.info("SLA scan: user=%s raised=%d", uid, len(new_alerts))

    _LOG.info(
        "SLA scan complete — users=%d alerts_raised=%d at %s",
        len(users), total_raised, now.isoformat(),
    )
    return {
        "users_scanned": len(users),
        "alerts_raised": total_raised,
        "ran_at":        now.isoformat(),
    }
