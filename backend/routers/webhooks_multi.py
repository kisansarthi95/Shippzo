"""
Webhooks Multi — Phase F3 (multiple named webhooks per user with
event-type-based ingest).

This is the v2 webhook system. Each user can create UNLIMITED webhook
endpoints, each with:
  • A friendly name (shown as the source badge in Pending Orders).
  • An event_type that decides how the payload is processed:
      - new_order            → create a pending order (default behaviour)
      - order_status_update  → look up an existing shipment / pending
                                order and update its status
      - abandoned_order      → store as an abandoned-cart record
                                (Phase-3 dashboard reads this collection)
      - customer_created     → store as a customer record
      - customer_updated     → upsert customer record
      - custom               → same as new_order but skips the
                                Dukaan/Shopify auto-mapping heuristics
  • A unique 32-char secret (rotatable, embedded in the URL).
  • A per-webhook field mapping (JSON key → schema field).
  • An enabled flag (pause without losing config).
  • A recent_samples ring buffer (last 10 unmapped payloads).
  • Per-webhook stats (received / imported / last_received_at).

Mongo collection: `user_webhooks`
Indexes (created on first init): { secret: 1 } unique sparse.

Endpoints (auth-required, owner only):
  GET    /api/me/webhooks                  list all webhooks
  POST   /api/me/webhooks                  create a new webhook
  GET    /api/me/webhooks/{id}             get single webhook details
  PUT    /api/me/webhooks/{id}             update name/event_type/enabled/mapping
  POST   /api/me/webhooks/{id}/rotate      rotate secret (URL changes)
  DELETE /api/me/webhooks/{id}             delete webhook
  POST   /api/me/webhooks/{id}/preview     preview a sample payload

Public ingest (no auth header — secret-in-URL):
  POST /api/webhook/orders/{secret}        same URL pattern as v1 — the
                                            handler looks up the secret
                                            in user_webhooks first, then
                                            falls back to the legacy
                                            users.webhook_secret. This
                                            keeps every old URL working
                                            while letting users migrate
                                            piecemeal.

Backward compatibility:
  The legacy single-webhook endpoints (`/api/me/webhook-config*`) live
  in `routers/webhook.py` and continue to back the v1 admin UI. The
  v2 UI uses the routes in this module and creates rows in
  `user_webhooks`. The public ingest endpoint is shared (lives in
  v1 file) and now consults BOTH stores.

Pattern: late-binding `init()` — same convention as routers/webhook.py.
"""
from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Request
from pydantic import BaseModel, Field

from import_schema import (
    SCHEMA_FIELDS,
    build_pending_doc_from_mapping,
    suggest_mapping,
    validate_mapping_field,
)


webhooks_multi_router = APIRouter(prefix="/api", tags=["webhooks-multi"])


# ── Event types ──────────────────────────────────────────────────────
EVENT_TYPES: List[Dict[str, str]] = [
    {
        "key":         "new_order",
        "label":       "New Order",
        "description": "A brand new order was placed → create a pending order",
    },
    {
        "key":         "order_status_update",
        "label":       "Order Status Update",
        "description": "An existing order's status changed → update its status",
    },
    {
        "key":         "abandoned_order",
        "label":       "Abandoned Order",
        "description": "Customer started checkout but didn't pay → log as abandoned cart",
    },
    {
        "key":         "customer_created",
        "label":       "Customer Created",
        "description": "A new customer signed up → save customer record",
    },
    {
        "key":         "customer_updated",
        "label":       "Customer Updated",
        "description": "Customer details changed → upsert customer record",
    },
    {
        "key":         "custom",
        "label":       "Custom",
        "description": "Generic webhook — you map every JSON key manually",
    },
]
EVENT_KEYS: List[str] = [e["key"] for e in EVENT_TYPES]


# ── Pydantic schemas ─────────────────────────────────────────────────
class WebhookCreatePayload(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    event_type: str = Field(default="new_order", max_length=32)


class WebhookUpdatePayload(BaseModel):
    name: Optional[str] = Field(default=None, max_length=32)
    event_type: Optional[str] = Field(default=None, max_length=32)
    enabled: Optional[bool] = None
    mapping: Optional[Dict[str, str]] = None


# ── Helpers ──────────────────────────────────────────────────────────
def _gen_secret() -> str:
    """32-char URL-safe secret, ~190 bits entropy."""
    return secrets.token_urlsafe(24).rstrip("=")


def _build_webhook_url(request: Request, secret: str) -> str:
    """Public HTTPS URL for a webhook secret. Same logic as v1."""
    fwd_proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
    fwd_host  = request.headers.get("x-forwarded-host", "").split(",")[0].strip()
    fwd_host  = fwd_host or request.headers.get("host", "")
    proto = (fwd_proto or "https").lower()
    if proto not in ("https", "http"):
        proto = "https"
    if not fwd_host:
        return f"{str(request.base_url).rstrip('/')}/api/webhook/orders/{secret}"
    return f"{proto}://{fwd_host}/api/webhook/orders/{secret}"


def _serialise_webhook(w: Dict[str, Any], request: Request | None = None) -> Dict[str, Any]:
    """Strip _id, attach a freshly-built URL, and trim the recent_samples
    ring buffer to the last 5 (the full 10 is kept on disk, but the
    list view only wants a peek)."""
    samples = w.get("recent_samples") or []
    out = {
        "id":               w.get("id"),
        "name":             w.get("name") or "",
        "event_type":       w.get("event_type") or "new_order",
        "secret":           w.get("secret") or "",
        "enabled":          bool(w.get("enabled", True)),
        "mapping":          w.get("mapping") or {},
        "created_at":       w.get("created_at") or "",
        "secret_rotated_at": w.get("secret_rotated_at") or "",
        "stats":            w.get("stats") or {
            "total_received": 0, "total_imported": 0, "last_received_at": "",
        },
        "recent_samples":   samples[-5:],
    }
    if request is not None and out["secret"]:
        out["url"] = _build_webhook_url(request, out["secret"])
    else:
        out["url"] = ""
    return out


def init() -> None:
    import logging
    _logger = logging.getLogger("routers.webhooks_multi")
    from server import (  # noqa: WPS433
        db,
        get_current_user as _get_current_user,
    )

    async def _list_user_custom_fields(user_id: str) -> List[Dict[str, Any]]:
        cur = db.user_custom_fields.find(
            {"user_id": user_id, "active": {"$ne": False}}, {"_id": 0},
        )
        return [doc async for doc in cur]

    async def _migrate_legacy_webhook_if_needed(user_id: str) -> None:
        """One-shot: if the user has a legacy `webhook_secret` on their
        user doc but no row in `user_webhooks`, copy it into the new
        collection so the v2 UI shows it from day one. Idempotent —
        re-running is a no-op once a row exists for the legacy secret."""
        existing = await db.user_webhooks.count_documents({"user_id": user_id})
        if existing > 0:
            return
        u = await db.users.find_one(
            {"id": user_id},
            {"_id": 0, "webhook_secret": 1, "webhook_name": 1,
             "webhook_mapping": 1, "webhook_recent_samples": 1,
             "webhook_secret_at": 1},
        ) or {}
        legacy_secret = u.get("webhook_secret")
        if not legacy_secret:
            return
        await db.user_webhooks.insert_one({
            "id":          str(uuid.uuid4()),
            "user_id":     user_id,
            "name":        (u.get("webhook_name") or "").strip()[:32] or "My Webhook",
            "event_type":  "new_order",
            "secret":      legacy_secret,
            "mapping":     u.get("webhook_mapping") or {},
            "enabled":     True,
            "created_at":  u.get("webhook_secret_at") or datetime.now(timezone.utc).isoformat(),
            "secret_rotated_at": u.get("webhook_secret_at") or "",
            "recent_samples": (u.get("webhook_recent_samples") or [])[-10:],
            "stats": {
                "total_received":   0,
                "total_imported":   0,
                "last_received_at": "",
            },
        })
        _logger.info("Migrated legacy webhook for user=%s", user_id)

    # ────────────────────────────────────────────────────────────────
    # Endpoints
    # ────────────────────────────────────────────────────────────────

    @webhooks_multi_router.get("/me/webhooks/event-types")
    async def list_event_types(
        _user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Static metadata used by the create / edit form. Kept on the
        server so adding a new event type doesn't need a frontend deploy."""
        return {"event_types": EVENT_TYPES}

    @webhooks_multi_router.get("/me/webhooks")
    async def list_webhooks(
        request: Request,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        await _migrate_legacy_webhook_if_needed(current_user["id"])
        cur = db.user_webhooks.find(
            {"user_id": current_user["id"]},
            {"_id": 0},
        ).sort("created_at", 1)
        rows = [_serialise_webhook(d, request) async for d in cur]
        return {"webhooks": rows, "count": len(rows)}

    @webhooks_multi_router.post("/me/webhooks")
    async def create_webhook(
        request: Request,
        payload: WebhookCreatePayload = Body(...),
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        if payload.event_type not in EVENT_KEYS:
            raise HTTPException(
                status_code=400,
                detail=f"event_type must be one of {EVENT_KEYS}",
            )
        wh_id  = str(uuid.uuid4())
        secret = _gen_secret()
        now    = datetime.now(timezone.utc).isoformat()
        doc = {
            "id":         wh_id,
            "user_id":    current_user["id"],
            "name":       payload.name.strip()[:32],
            "event_type": payload.event_type,
            "secret":     secret,
            "mapping":    {},
            "enabled":    True,
            "created_at": now,
            "secret_rotated_at": now,
            "recent_samples": [],
            "stats": {
                "total_received":   0,
                "total_imported":   0,
                "last_received_at": "",
            },
        }
        await db.user_webhooks.insert_one(doc)
        return _serialise_webhook(doc, request)

    @webhooks_multi_router.get("/me/webhooks/{wh_id}")
    async def get_webhook(
        request: Request,
        wh_id: str = Path(..., min_length=8),
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        doc = await db.user_webhooks.find_one(
            {"id": wh_id, "user_id": current_user["id"]},
            {"_id": 0},
        )
        if not doc:
            raise HTTPException(status_code=404, detail="Webhook not found")
        # Full samples (10) + URL.
        out = _serialise_webhook(doc, request)
        out["recent_samples"] = (doc.get("recent_samples") or [])[-10:]
        # Bonus — schema fields + per-user custom fields for the
        # mapping editor that's part of this screen.
        custom_fields = await _list_user_custom_fields(current_user["id"])
        out["schema_fields"] = SCHEMA_FIELDS
        out["custom_fields"] = [
            {"id": cf.get("id"), "label": cf.get("name") or cf.get("label") or ""}
            for cf in custom_fields
        ]
        return out

    @webhooks_multi_router.put("/me/webhooks/{wh_id}")
    async def update_webhook(
        request: Request,
        wh_id: str,
        payload: WebhookUpdatePayload = Body(...),
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        existing = await db.user_webhooks.find_one(
            {"id": wh_id, "user_id": current_user["id"]},
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Webhook not found")
        update: Dict[str, Any] = {}
        if payload.name is not None:
            clean = payload.name.strip()[:32]
            if not clean:
                raise HTTPException(status_code=400, detail="Name cannot be blank")
            update["name"] = clean
        if payload.event_type is not None:
            if payload.event_type not in EVENT_KEYS:
                raise HTTPException(
                    status_code=400,
                    detail=f"event_type must be one of {EVENT_KEYS}",
                )
            update["event_type"] = payload.event_type
        if payload.enabled is not None:
            update["enabled"] = bool(payload.enabled)
        if payload.mapping is not None:
            custom_fields = await _list_user_custom_fields(current_user["id"])
            cf_ids = {cf.get("id") for cf in custom_fields if cf.get("id")}
            bad = [
                v for v in payload.mapping.values()
                if not validate_mapping_field(v, cf_ids)
            ]
            if bad:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown schema fields: {sorted(set(bad))}",
                )
            update["mapping"] = payload.mapping
        if not update:
            return _serialise_webhook(existing, request)
        await db.user_webhooks.update_one(
            {"id": wh_id, "user_id": current_user["id"]},
            {"$set": update},
        )
        existing.update(update)
        return _serialise_webhook(existing, request)

    @webhooks_multi_router.post("/me/webhooks/{wh_id}/rotate")
    async def rotate_webhook(
        request: Request,
        wh_id: str,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        existing = await db.user_webhooks.find_one(
            {"id": wh_id, "user_id": current_user["id"]},
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Webhook not found")
        new_secret = _gen_secret()
        now = datetime.now(timezone.utc).isoformat()
        await db.user_webhooks.update_one(
            {"id": wh_id, "user_id": current_user["id"]},
            {"$set": {
                "secret":            new_secret,
                "secret_rotated_at": now,
            }},
        )
        existing["secret"]            = new_secret
        existing["secret_rotated_at"] = now
        return _serialise_webhook(existing, request)

    @webhooks_multi_router.delete("/me/webhooks/{wh_id}")
    async def delete_webhook(
        wh_id: str,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        res = await db.user_webhooks.delete_one(
            {"id": wh_id, "user_id": current_user["id"]},
        )
        if res.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Webhook not found")
        return {"ok": True, "deleted": wh_id}

    @webhooks_multi_router.post("/me/webhooks/{wh_id}/preview")
    async def preview_payload(
        wh_id: str,
        body: Any = Body(..., description="Paste a sample JSON payload"),
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Same as the v1 preview endpoint but scoped to a specific
        webhook so its saved mapping seeds the suggestions."""
        from routers.webhook import _flatten  # noqa: WPS433 — reuse helper
        existing = await db.user_webhooks.find_one(
            {"id": wh_id, "user_id": current_user["id"]},
            {"_id": 0, "mapping": 1},
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Webhook not found")
        if isinstance(body, list):
            rows = body
        elif isinstance(body, dict) and isinstance(body.get("orders"), list):
            rows = body["orders"]
        elif isinstance(body, dict):
            rows = [body]
        else:
            rows = []
        first = rows[0] if rows and isinstance(rows[0], dict) else {}
        flat  = _flatten(first)
        keys  = list(flat.keys())
        custom_fields = await _list_user_custom_fields(current_user["id"])
        suggested = suggest_mapping(keys, existing.get("mapping"), custom_fields)
        return {
            "keys":          keys,
            "sample_values": {k: flat[k] for k in keys[:30]},
            "schema_fields": SCHEMA_FIELDS,
            "custom_fields": [
                {"id": cf.get("id"), "label": cf.get("name") or cf.get("label") or ""}
                for cf in custom_fields
            ],
            "suggested":     suggested,
        }

    _logger.info("webhooks_multi router mounted (8 endpoints)")
