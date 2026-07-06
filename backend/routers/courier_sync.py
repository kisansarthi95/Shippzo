"""Courier Status Auto Sync — API router (Phase 1, India Post).

Endpoints (all prefixed with `/api`):

  GET   /courier-sync/partners        — list supported partners + regex patterns
  GET   /courier-sync/configs         — per-user partner enable flags
  PUT   /courier-sync/configs/{key}   — toggle / configure a partner for the user
  POST  /courier-sync/ingest          — Android client ingest endpoint (raw SMS)
  GET   /courier-sync/events          — recent sync events (audit log) for the user
  POST  /courier-sync/test-parse      — dry-run parser (no DB writes) — used by
                                       onboarding screen to preview matches

Usage pattern (late-bind init, identical to other routers/*.py files):

    from routers.courier_sync import (
        courier_sync_router,
        init as _init_courier_sync_router,
    )
    _init_courier_sync_router()
    app.include_router(courier_sync_router)
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import courier_sync as _cs_pkg  # noqa: WPS433 — local pkg
from courier_sync import generic_parser as _generic_parser  # noqa: WPS433 — local pkg

_logger = logging.getLogger("routers.courier_sync")

courier_sync_router = APIRouter(prefix="/api/courier-sync", tags=["courier-sync"])


# --------------------------------------------------------------------
# Pydantic schemas
# --------------------------------------------------------------------
class IngestPayload(BaseModel):
    """What the Android NotificationListenerService POSTs for each
    SMS / notification that matched the configured filters."""

    sender:     str = ""
    title:      str = ""
    text:       str = ""
    package:    str = ""   # Android package name of the SMS app
    posted_at:  Optional[str] = None  # ISO ts when the device received the SMS
    device_id:  str = ""   # opaque device identifier (UUID stored on phone)


class TestParseRequest(BaseModel):
    sender:     str = ""
    title:      str = ""
    text:       str = ""


class ConfigUpdate(BaseModel):
    enabled:           Optional[bool] = None
    # Optional overrides — usually omitted (server defaults to the
    # parser's built-in regexes).
    tracking_pattern:  Optional[str] = None
    sender_pattern:    Optional[str] = None


class PartnerInfo(BaseModel):
    key:              str
    name:             str
    channel:          str
    tracking_pattern: str
    sender_pattern:   str
    description:      str
    enabled:          bool = False


class CourierSyncEvent(BaseModel):
    id:               str
    user_id:          str
    partner_key:      str
    sender:           str
    raw_text:         str
    matched:          bool
    reason:           str = ""
    tracking_id:      str = ""
    canonical_status: str = ""
    shipment_status:  str = ""
    matched_phrase:   str = ""
    shipment_id:      str = ""
    action:           str = ""   # "updated" | "already_in_sync" | "no_shipment_found" | "ignored"
    posted_at:        Optional[str] = None
    received_at:      str


# --------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------
def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------
# Late-bind init — registers routes after server.py is up.
# --------------------------------------------------------------------
def init() -> None:
    from server import (  # noqa: WPS433 — intentional late import
        db,
        get_current_user as _get_current_user,
        utcnow_iso,
    )

    # ── helpers reused by multiple endpoints ────────────────────────
    async def _config_doc(user_id: str, partner_key: str) -> Dict[str, Any]:
        """Return the user's config for one partner, creating a
        sensible default (disabled) if it doesn't yet exist."""
        cfg = await db.courier_partner_configs.find_one(
            {"user_id": user_id, "partner_key": partner_key},
            {"_id": 0},
        )
        if cfg:
            return cfg
        partner = _cs_pkg.get_partner(partner_key)
        return {
            "id":               str(uuid.uuid4()),
            "user_id":          user_id,
            "partner_key":      partner_key,
            "partner_name":     (partner or {}).get("name", partner_key),
            "enabled":          False,
            "tracking_pattern": (partner or {}).get("tracking_pattern", ""),
            "sender_pattern":   (partner or {}).get("sender_pattern", ""),
            "created_at":       utcnow_iso(),
            "updated_at":       utcnow_iso(),
        }

    async def _ensure_indexes() -> None:
        """Defensive index creation (idempotent). Safe to call on
        every request — Mongo collapses no-op CreateIndex calls."""
        try:
            await db.courier_partner_configs.create_index(
                [("user_id", 1), ("partner_key", 1)],
                unique=True,
                name="user_partner_uniq",
            )
            await db.courier_sync_events.create_index(
                [("user_id", 1), ("received_at", -1)],
                name="user_received_desc",
            )
            await db.courier_sync_events.create_index(
                [("user_id", 1), ("tracking_id", 1)],
                name="user_tracking",
            )
        except Exception:
            _logger.exception("courier_sync index creation failed (non-fatal)")

    # ── GET /partners ──────────────────────────────────────────────
    @courier_sync_router.get("/partners", response_model=List[PartnerInfo])
    async def list_partners_endpoint(
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        await _ensure_indexes()
        partners = _cs_pkg.list_partners()
        # Annotate each with the user's enabled flag for one-shot UI render.
        user_cfgs = await db.courier_partner_configs.find(
            {"user_id": current_user["id"]}, {"_id": 0},
        ).to_list(length=None)
        enabled_map = {c["partner_key"]: bool(c.get("enabled")) for c in user_cfgs}
        out: List[PartnerInfo] = []
        for p in partners:
            out.append(PartnerInfo(
                key              = p["key"],
                name             = p["name"],
                channel          = p["channel"],
                tracking_pattern = p["tracking_pattern"],
                sender_pattern   = p["sender_pattern"],
                description      = p["description"],
                enabled          = enabled_map.get(p["key"], False),
            ))
        return out

    # ── GET /configs ───────────────────────────────────────────────
    @courier_sync_router.get("/configs")
    async def list_configs_endpoint(
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        await _ensure_indexes()
        cfgs = await db.courier_partner_configs.find(
            {"user_id": current_user["id"]}, {"_id": 0},
        ).to_list(length=None)
        return {"configs": cfgs}

    # ── PUT /configs/{key} ─────────────────────────────────────────
    @courier_sync_router.put("/configs/{partner_key}")
    async def update_config_endpoint(
        partner_key: str,
        payload: ConfigUpdate,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        await _ensure_indexes()
        partner = _cs_pkg.get_partner(partner_key)
        if not partner:
            raise HTTPException(status_code=404, detail="Unknown partner_key")

        existing = await _config_doc(current_user["id"], partner_key)
        update: Dict[str, Any] = {"updated_at": utcnow_iso()}
        if payload.enabled is not None:
            update["enabled"] = bool(payload.enabled)
        if payload.tracking_pattern is not None:
            update["tracking_pattern"] = payload.tracking_pattern
        if payload.sender_pattern is not None:
            update["sender_pattern"] = payload.sender_pattern

        # Upsert with sensible defaults if first-touch.
        await db.courier_partner_configs.update_one(
            {"user_id": current_user["id"], "partner_key": partner_key},
            {
                "$set":         update,
                "$setOnInsert": {
                    "id":            existing["id"],
                    "user_id":       current_user["id"],
                    "partner_key":   partner_key,
                    "partner_name":  partner["name"],
                    "created_at":    existing.get("created_at", utcnow_iso()),
                },
            },
            upsert=True,
        )
        merged = await db.courier_partner_configs.find_one(
            {"user_id": current_user["id"], "partner_key": partner_key},
            {"_id": 0},
        )
        return {"ok": True, "config": merged}

    # ── POST /test-parse ───────────────────────────────────────────
    @courier_sync_router.post("/test-parse")
    async def test_parse_endpoint(
        payload: TestParseRequest,
        current_user: Dict[str, Any] = Depends(_get_current_user),  # noqa: ARG001
    ):
        """Dry-run the parser — does NOT touch shipments or write
        any audit log. Used by the onboarding screen to let the
        operator paste a real SMS and verify it'll be recognised."""
        result = _cs_pkg.parse_notification(
            sender = payload.sender,
            title  = payload.title,
            text   = payload.text,
        )
        return result

    # ── POST /ingest ───────────────────────────────────────────────
    @courier_sync_router.post("/ingest")
    async def ingest_endpoint(
        payload: IngestPayload,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Receive one raw SMS / notification from the Android client
        and (if it matches a partner) update the corresponding
        Shipment.status. Always writes an audit row to
        `courier_sync_events` — even on no-match — so operators can
        see exactly which notifications were ignored and why.

        Status-update whitelist (Phase 1): only canonical statuses
        'Booked' and 'Delivered' are allowed to mutate `shipment.status`.
        Intermediate events (Out for Delivery, In Transit, Dispatched,
        Arrived, RTO, Undelivered) are still parsed and persisted to
        `courier_sync_events` for audit but the shipment row is left
        untouched. This is enforced both here (router-level guard)
        and at the parser level (empty `shipment_status` for
        non-whitelisted canonicals — see india_post._STATUS_RULES).
        """
        await _ensure_indexes()

        # Per-request correlation id — every log line in this pipeline
        # carries it so operators can grep one SMS end-to-end.
        event_id  = str(uuid.uuid4())
        log_tag   = f"[courier_sync.ingest evt={event_id[:8]} usr={current_user['id'][:8]}]"

        # ── Step 1: SMS received ───────────────────────────────────
        _logger.info(
            "%s step=1 sms_received sender=%r title=%r text_len=%d "
            "package=%r posted_at=%r device=%r",
            log_tag,
            (payload.sender or "")[:60],
            (payload.title or "")[:60],
            len(payload.text or ""),
            payload.package or "",
            payload.posted_at or "",
            (payload.device_id or "")[:16],
        )

        try:
            # Phase F5.0 — per-courier config takes priority. We fetch
            # all of the caller's couriers with auto_sync_enabled=True
            # and try each one's config. Only if NONE match do we fall
            # back to the legacy hardcoded partners registry (so old
            # in-code partners like the built-in India Post keep working
            # for users who haven't customised anything).
            user_couriers = await db.couriers.find(
                {
                    "user_id":            current_user["id"],
                    "auto_sync_enabled":  True,
                },
                {"_id": 0},
            ).to_list(length=None)
            result: Dict[str, Any] = {"matched": False, "reason": "", "partner_key": ""}
            if user_couriers:
                result = _generic_parser.parse_with_couriers(
                    couriers = user_couriers,
                    sender   = payload.sender,
                    text     = payload.text,
                    title    = payload.title,
                )
                # Backward-compat: also expose a name-derived slug via
                # `partner_slug` (india_post, nandan_courier, etc.) so
                # tests + audit fields that grep by partner slug keep
                # working. The primary partner_key stays the courier UUID
                # (needed by the /couriers/{id}/sync-events lookup).
                if result.get("matched"):
                    result["partner_slug"] = _generic_parser.partner_slug_for_name(
                        str(result.get("partner_name") or "")
                    )
            # Legacy fallback — only tried when the per-courier path
            # didn't match. This keeps the existing hardcoded India
            # Post partner working for users who haven't opted-in to
            # the new per-courier config UI yet.
            if not result.get("matched"):
                legacy = _cs_pkg.parse_notification(
                    sender = payload.sender,
                    title  = payload.title,
                    text   = payload.text,
                )
                if legacy.get("matched"):
                    result = legacy
                    result["_source"] = "legacy_hardcoded_partner"
                elif not user_couriers:
                    # Preserve legacy failure reason (more actionable)
                    # when the user has no auto-sync couriers at all.
                    result = legacy
        except Exception:
            _logger.exception("%s step=parse_error parser_crashed", log_tag)
            result = {
                "matched": False,
                "reason":  "parser_exception",
                "partner_key": "",
            }

        now_iso   = utcnow_iso()
        event_doc: Dict[str, Any] = {
            "id":               event_id,
            "user_id":          current_user["id"],
            "partner_key":      result.get("partner_key", ""),
            "sender":           payload.sender,
            "title":            payload.title,
            "raw_text":         (payload.text or "")[:2000],
            "package":          payload.package,
            "posted_at":        payload.posted_at or "",
            "device_id":        payload.device_id,
            "received_at":      now_iso,
            "matched":          bool(result.get("matched")),
            "reason":           result.get("reason", ""),
            "tracking_id":      result.get("tracking_id", ""),
            "canonical_status": result.get("canonical_status", ""),
            "shipment_status":  result.get("shipment_status", ""),
            "matched_phrase":   result.get("matched_phrase", ""),
            "event_date":       result.get("event_date", "") or "",
            "postman":          result.get("postman") or {},
            "shipment_id":      "",
            "action":           "",
        }

        # ── Step 2: sender detection / partner match ───────────────
        if not result.get("matched"):
            reason = result.get("reason", "unknown")
            _logger.info(
                "%s step=2 sender_match=NO partner=%r reason=%s — short-circuit",
                log_tag, result.get("partner_key", ""), reason,
            )
            event_doc["action"] = "ignored"
            try:
                await db.courier_sync_events.insert_one(event_doc)
            except Exception:
                _logger.exception("%s step=audit_write_error (no_match)", log_tag)
            return {
                "ok":       True,
                "matched":  False,
                "reason":   reason,
                "event_id": event_id,
            }

        partner_key = result["partner_key"]
        _logger.info(
            "%s step=2 sender_match=YES partner=%s", log_tag, partner_key,
        )

        # ── Step 3: tracking ID extracted ──────────────────────────
        awb = result.get("tracking_id", "")
        _logger.info(
            "%s step=3 tracking_extracted awb=%s all_awbs=%s",
            log_tag, awb, result.get("tracking_ids") or [awb],
        )

        # ── Partner config gate — user must have enabled it.
        # Phase F5.0 — per-courier configs are self-gating (matched
        # only when auto_sync_enabled=True), so skip this check when
        # the parser matched via a courier UUID (not a legacy in-code
        # partner key). Legacy partner_key strings ("india_post" etc.)
        # still go through the courier_partner_configs table.
        is_courier_id = bool(_cs_pkg.get_partner(partner_key)) is False
        if is_courier_id:
            cfg = {"enabled": True}
        else:
            try:
                cfg = await db.courier_partner_configs.find_one(
                    {
                        "user_id":      current_user["id"],
                        "partner_key":  partner_key,
                    },
                    {"_id": 0},
                )
            except Exception:
                _logger.exception("%s step=db_error config_lookup", log_tag)
                cfg = None

        if not cfg or not cfg.get("enabled"):
            _logger.info(
                "%s step=3b partner_disabled partner=%s — skipping update",
                log_tag, partner_key,
            )
            event_doc["action"] = "partner_disabled"
            try:
                await db.courier_sync_events.insert_one(event_doc)
            except Exception:
                _logger.exception("%s step=audit_write_error (partner_disabled)", log_tag)
            return {
                "ok":       True,
                "matched":  True,
                "action":   "partner_disabled",
                "event_id": event_id,
            }

        # ── Step 4: shipment lookup ────────────────────────────────
        try:
            ship = await db.shipments.find_one(
                {
                    "user_id": current_user["id"],
                    "$or": [
                        {"tracking_id":         awb},
                        {"manual_tracking_id":  awb},
                        {"order_id":            awb},
                    ],
                },
                {"_id": 0},
            )
        except Exception:
            _logger.exception("%s step=4 db_error shipment_lookup awb=%s", log_tag, awb)
            event_doc["action"] = "db_error_shipment_lookup"
            try:
                await db.courier_sync_events.insert_one(event_doc)
            except Exception:
                _logger.exception("%s step=audit_write_error (db_lookup)", log_tag)
            raise HTTPException(status_code=500, detail="DB error during shipment lookup")

        if not ship:
            _logger.info(
                "%s step=4 shipment_found=NO awb=%s — recording audit only",
                log_tag, awb,
            )
            event_doc["action"] = "no_shipment_found"
            try:
                await db.courier_sync_events.insert_one(event_doc)
            except Exception:
                _logger.exception("%s step=audit_write_error (no_shipment)", log_tag)
            return {
                "ok":          True,
                "matched":     True,
                "action":      "no_shipment_found",
                "tracking_id": awb,
                "event_id":    event_id,
            }

        event_doc["shipment_id"] = ship["id"]
        _logger.info(
            "%s step=4 shipment_found=YES shipment_id=%s current_status=%r",
            log_tag, ship["id"], (ship.get("status") or ""),
        )

        # ── Step 5: status classification ──────────────────────────
        current_status = (ship.get("status") or "").strip()
        new_status     = result.get("shipment_status") or ""
        canonical      = result.get("canonical_status") or ""

        # Resolve the per-partner whitelist (falls back to a safe
        # default of {"Booked", "Delivered"} if a partner ever ships
        # without one — defensive).
        # Phase F5.0 — new per-courier configs carry the whitelist
        # per-rule (`result["whitelisted"]`). When present, that flag
        # takes priority over the registry lookup.
        partner_cfg     = _cs_pkg.get_partner(partner_key) or {}
        status_whitelist = partner_cfg.get(
            "status_update_whitelist",
            frozenset({"Booked", "Delivered", "Out for Delivery"}),
        )
        # For per-courier matches the parser embeds the whitelisted
        # bit directly; synthesise a whitelist that always contains
        # the canonical iff the rule was flagged whitelisted.
        rule_whitelisted = result.get("whitelisted")
        if rule_whitelisted is not None:
            status_whitelist = (
                frozenset({canonical}) if bool(rule_whitelisted)
                else frozenset()
            )

        _logger.info(
            "%s step=5 status_identified canonical=%r ship_status=%r "
            "phrase=%r whitelist=%s",
            log_tag, canonical, new_status,
            (result.get("matched_phrase") or "")[:40],
            sorted(list(status_whitelist)),
        )

        # ── Guard A: never downgrade an already-Delivered shipment.
        if current_status == "Delivered" and canonical != "Delivered":
            _logger.info(
                "%s step=6 update_decision=SKIP reason=ignored_delivered "
                "(shipment is already Delivered, incoming canonical=%r)",
                log_tag, canonical,
            )
            event_doc["action"] = "ignored_delivered"
            try:
                await db.courier_sync_events.insert_one(event_doc)
            except Exception:
                _logger.exception("%s step=audit_write_error (ignored_delivered)", log_tag)
            return {
                "ok":       True,
                "matched":  True,
                "action":   "ignored_delivered",
                "event_id": event_id,
            }

        # ── Guard B: whitelist — only Booked / Delivered may mutate
        #     shipment.status. Intermediate events are recorded as
        #     audit only.
        if canonical not in status_whitelist:
            _logger.info(
                "%s step=6 update_decision=SKIP reason=ignored_intermediate_status "
                "canonical=%r (not in whitelist %s) — audit-only write",
                log_tag, canonical, sorted(list(status_whitelist)),
            )
            event_doc["action"] = "ignored_intermediate_status"
            event_doc["reason"] = (
                event_doc.get("reason")
                or f"canonical {canonical!r} not in whitelist"
            )
            try:
                await db.courier_sync_events.insert_one(event_doc)
            except Exception:
                _logger.exception(
                    "%s step=audit_write_error (ignored_intermediate)", log_tag,
                )
            return {
                "ok":              True,
                "matched":         True,
                "action":          "ignored_intermediate_status",
                "canonical":       canonical,
                "shipment_id":     ship["id"],
                "tracking_id":     awb,
                "event_id":        event_id,
            }

        # Phase F5.0 — for the audit trail we prefer the human-friendly
        # partner slug (e.g. "india_post") over the courier UUID so the
        # data stays comparable with pre-F5.0 rows AND survives if a
        # courier is deleted+recreated.
        partner_slug_display = str(
            result.get("partner_slug") or partner_key
        )

        # ── Step 6: build update set ───────────────────────────────
        update_set: Dict[str, Any] = {
            "last_courier_status_text":     canonical,
            "last_courier_status_at":       now_iso,
            "last_courier_status_source":   "auto_sync_sms",
            "last_courier_status_partner":  partner_slug_display,
            "updated_at":                   now_iso,
        }
        push_ops: Dict[str, Any] = {}
        if new_status and current_status != new_status:
            update_set["status"] = new_status
        if canonical == "Delivered" and not ship.get("delivered_at"):
            update_set["delivered_at"] = (
                result.get("event_date") or now_iso
            )

        # ── Out for Delivery — append attempt history + set anchor. ───
        # First-attempt anchor `out_for_delivery_at` is only set if
        # missing; subsequent attempts push a new history entry but
        # leave the anchor (used for the 2-hour alert) untouched so
        # the timer keeps counting from the original OFD moment.
        if canonical == "Out for Delivery":
            postman = result.get("postman") or {}
            attempt_iso = (
                result.get("event_date") or now_iso
            )
            attempt_entry = {
                "postman_name":  str(postman.get("postman_name") or ""),
                "beat":          str(postman.get("beat") or ""),
                "attempted_on":  str(attempt_iso),
                "received_at":   now_iso,
                "raw_phrase":    (result.get("matched_phrase") or "")[:80],
            }
            push_ops["out_for_delivery_history"] = attempt_entry
            update_set["last_delivery_person"]     = attempt_entry["postman_name"]
            update_set["last_delivery_beat"]       = attempt_entry["beat"]
            update_set["last_delivery_attempt_at"] = now_iso
            update_set["delivery_attempt_count"]   = int(
                ship.get("delivery_attempt_count") or 0,
            ) + 1
            if not ship.get("out_for_delivery_at"):
                update_set["out_for_delivery_at"] = now_iso
                # Reset the alert flag so a fresh 2h timer starts.
                update_set["ofd_alert_fired_at"]  = None

        # If shipment transitioned back into Delivered, clear the
        # pending-alert flag so a future OFD (very unusual) can
        # re-arm cleanly.
        if canonical == "Delivered":
            update_set["ofd_alert_fired_at"] = None

        meaningful_fields = {"status", "delivered_at", "out_for_delivery_at"}
        meaningful_change = any(
            (k in update_set and update_set[k] != ship.get(k))
            for k in meaningful_fields
        )
        # OFD attempt-count increments and history $push are also
        # user-visible mutations even when the top-level status is
        # already 'Out for Delivery' — flag them as meaningful so
        # the ingest response says action='updated' (not
        # 'already_in_sync').
        if push_ops or (
            "delivery_attempt_count" in update_set
            and update_set["delivery_attempt_count"]
            != int(ship.get("delivery_attempt_count") or 0)
        ):
            meaningful_change = True

        _logger.info(
            "%s step=6 update_decision=APPLY current=%r → new=%r "
            "meaningful=%s set_keys=%s push_keys=%s",
            log_tag, current_status, new_status, meaningful_change,
            sorted(list(update_set.keys())),
            sorted(list(push_ops.keys())),
        )

        # ── Step 7: apply update ───────────────────────────────────
        mongo_update: Dict[str, Any] = {"$set": update_set}
        if push_ops:
            mongo_update["$push"] = push_ops
        try:
            upd_result = await db.shipments.update_one(
                {"id": ship["id"], "user_id": current_user["id"]},
                mongo_update,
            )
        except Exception:
            _logger.exception(
                "%s step=7 db_error shipment_update shipment_id=%s",
                log_tag, ship["id"],
            )
            event_doc["action"] = "db_error_shipment_update"
            try:
                await db.courier_sync_events.insert_one(event_doc)
            except Exception:
                _logger.exception("%s step=audit_write_error (db_update)", log_tag)
            raise HTTPException(status_code=500, detail="DB error during shipment update")

        _logger.info(
            "%s step=7 status_updated shipment_id=%s matched_count=%s modified_count=%s",
            log_tag, ship["id"],
            getattr(upd_result, "matched_count", "?"),
            getattr(upd_result, "modified_count", "?"),
        )

        event_doc["action"] = "updated" if meaningful_change else "already_in_sync"
        try:
            await db.courier_sync_events.insert_one(event_doc)
        except Exception:
            _logger.exception("%s step=audit_write_error (success_write)", log_tag)

        return {
            "ok":              True,
            "matched":         True,
            "action":          event_doc["action"],
            "shipment_id":     ship["id"],
            "tracking_id":     awb,
            "new_status":      new_status,
            "canonical":       canonical,
            "event_id":        event_id,
        }

    # ── GET /events ────────────────────────────────────────────────
    @courier_sync_router.get("/events")
    async def list_events_endpoint(
        limit: int = 50,
        only_matched: bool = False,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        await _ensure_indexes()
        q: Dict[str, Any] = {"user_id": current_user["id"]}
        if only_matched:
            q["matched"] = True
        limit = max(1, min(int(limit or 50), 500))
        rows = await (
            db.courier_sync_events
            .find(q, {"_id": 0})
            .sort("received_at", -1)
            .limit(limit)
            .to_list(length=limit)
        )
        return {"events": rows, "count": len(rows)}

    # ── GET /ofd-alerts ────────────────────────────────────────────
    # Returns Out-for-Delivery shipments where more than `hours` have
    # passed since the FIRST OFD SMS landed AND the shipment is still
    # NOT delivered. Used by the frontend to fire a local push
    # notification "still not delivered after 2h" — the client stamps
    # `ofd_alert_fired_at` via PUT /ofd-alert-fired/{id} so the same
    # shipment doesn't re-alert on every poll.
    @courier_sync_router.get("/ofd-alerts")
    async def ofd_alerts_endpoint(
        hours: float = 2.0,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        # `hours or 2.0` would clobber the intent when the caller
        # explicitly passes 0 (test / debug harnesses do this).
        hours = 2.0 if hours is None else float(hours)
        hours = max(0.0, min(hours, 48.0))
        cutoff = datetime.now(timezone.utc).timestamp() - (hours * 3600)
        # Fetch all OFD shipments and filter in Python (out_for_delivery_at
        # is a string ISO stamp — Mongo comparison via $lt would work but
        # keeping it simple + defensive against timezone / format drift).
        rows = await db.shipments.find(
            {
                "user_id":                 current_user["id"],
                "out_for_delivery_at":     {"$nin": [None, ""], "$exists": True},
                "status":                  {"$ne": "Delivered"},
                "delivered_at":            {"$in": [None, ""]},
                "ofd_alert_fired_at":      {"$in": [None, ""]},
            },
            {
                "_id":                      0,
                "id":                       1,
                "tracking_id":              1,
                "manual_tracking_id":       1,
                "order_id":                 1,
                "customer_name":            1,
                "customer_phone":           1,
                "courier_name":             1,
                "status":                   1,
                "out_for_delivery_at":      1,
                "last_delivery_person":     1,
                "last_delivery_beat":       1,
                "delivery_attempt_count":   1,
                "out_for_delivery_history": 1,
            },
        ).to_list(length=200)

        alerts: List[Dict[str, Any]] = []
        for r in rows:
            ofd_at_raw = str(r.get("out_for_delivery_at") or "")
            try:
                ofd_dt = datetime.fromisoformat(ofd_at_raw.replace("Z", "+00:00"))
            except Exception:
                continue
            if ofd_dt.tzinfo is None:
                ofd_dt = ofd_dt.replace(tzinfo=timezone.utc)
            if ofd_dt.timestamp() > cutoff:
                continue   # still within grace period
            alerts.append({
                "shipment_id":            r.get("id"),
                "tracking_id":            r.get("tracking_id") or r.get("manual_tracking_id") or r.get("order_id"),
                "customer_name":          r.get("customer_name") or "",
                "customer_phone":         r.get("customer_phone") or "",
                "courier_name":           r.get("courier_name") or "",
                "out_for_delivery_at":    ofd_at_raw,
                "hours_elapsed":          round(
                    (datetime.now(timezone.utc).timestamp() - ofd_dt.timestamp()) / 3600,
                    2,
                ),
                "delivery_person":        r.get("last_delivery_person") or "",
                "delivery_beat":          r.get("last_delivery_beat") or "",
                "attempts":               int(r.get("delivery_attempt_count") or 1),
            })
        return {"alerts": alerts, "count": len(alerts), "threshold_hours": hours}

    # ── PUT /ofd-alerts/{shipment_id}/fired ────────────────────────
    # Called by the frontend AFTER it has fired the local push
    # notification. Stamps `ofd_alert_fired_at` on the shipment so
    # the same alert doesn't repeat on every subsequent poll.
    @courier_sync_router.put("/ofd-alerts/{shipment_id}/fired")
    async def ofd_alert_fired_endpoint(
        shipment_id: str,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        now_iso = utcnow_iso()
        res = await db.shipments.update_one(
            {"id": shipment_id, "user_id": current_user["id"]},
            {"$set": {"ofd_alert_fired_at": now_iso, "updated_at": now_iso}},
        )
        if getattr(res, "matched_count", 0) == 0:
            raise HTTPException(status_code=404, detail="Shipment not found")
        return {"ok": True, "shipment_id": shipment_id, "fired_at": now_iso}

    _logger.info(
        "courier_sync router mounted: 8 endpoints, partners=%s",
        list(_cs_pkg.PARTNERS.keys()),
    )
