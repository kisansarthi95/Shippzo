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
        """
        await _ensure_indexes()
        result = _cs_pkg.parse_notification(
            sender = payload.sender,
            title  = payload.title,
            text   = payload.text,
        )

        event_id  = str(uuid.uuid4())
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

        # ── No partner / no AWB / no status keyword → just log & exit
        if not result.get("matched"):
            event_doc["action"] = "ignored"
            await db.courier_sync_events.insert_one(event_doc)
            return {
                "ok":      True,
                "matched": False,
                "reason":  result.get("reason", "unknown"),
                "event_id": event_id,
            }

        # ── Partner config gate — user must have enabled it.
        cfg = await db.courier_partner_configs.find_one(
            {
                "user_id":      current_user["id"],
                "partner_key":  result["partner_key"],
            },
            {"_id": 0},
        )
        if not cfg or not cfg.get("enabled"):
            event_doc["action"] = "partner_disabled"
            await db.courier_sync_events.insert_one(event_doc)
            return {
                "ok":      True,
                "matched": True,
                "action":  "partner_disabled",
                "event_id": event_id,
            }

        # ── Find the shipment (scoped to this user). Match on either
        #    tracking_id OR manual_tracking_id OR order_id (some users
        #    save the AWB directly in the order id field).
        awb = result["tracking_id"]
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
        if not ship:
            event_doc["action"] = "no_shipment_found"
            await db.courier_sync_events.insert_one(event_doc)
            return {
                "ok":          True,
                "matched":     True,
                "action":      "no_shipment_found",
                "tracking_id": awb,
                "event_id":    event_id,
            }

        event_doc["shipment_id"] = ship["id"]

        # ── Decide whether to mutate the shipment.
        # Phase-1 rule: never downgrade an already-Delivered shipment.
        # If the canonical status is "Delivered" and we don't yet
        # have a delivered_at, stamp it.
        current_status = (ship.get("status") or "").strip()
        new_status = result.get("shipment_status") or ""
        canonical  = result.get("canonical_status") or ""

        if current_status == "Delivered" and canonical != "Delivered":
            event_doc["action"] = "ignored_delivered"
            await db.courier_sync_events.insert_one(event_doc)
            return {
                "ok":      True,
                "matched": True,
                "action":  "ignored_delivered",
                "event_id": event_id,
            }

        update_set: Dict[str, Any] = {
            "last_courier_status_text":     canonical,
            "last_courier_status_at":       now_iso,
            "last_courier_status_source":   "auto_sync_sms",
            "last_courier_status_partner":  result.get("partner_key", ""),
            "updated_at":                   now_iso,
        }
        if new_status and current_status != new_status:
            update_set["status"] = new_status
        if canonical == "Delivered" and not ship.get("delivered_at"):
            update_set["delivered_at"] = (
                result.get("event_date") or now_iso
            )

        # No-op fast-path — nothing meaningful changed.
        meaningful_fields = {"status", "delivered_at"}
        meaningful_change = any(
            (k in update_set and update_set[k] != ship.get(k))
            for k in meaningful_fields
        )

        await db.shipments.update_one(
            {"id": ship["id"], "user_id": current_user["id"]},
            {"$set": update_set},
        )

        event_doc["action"] = "updated" if meaningful_change else "already_in_sync"
        await db.courier_sync_events.insert_one(event_doc)
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

    _logger.info(
        "courier_sync router mounted: 6 endpoints, partners=%s",
        list(_cs_pkg.PARTNERS.keys()),
    )
