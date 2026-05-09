"""
Webhook Ingest — Phase F2 (real-time JSON-payload bulk-import).

External systems (Shopify, WooCommerce, custom Zapier flows, etc.)
POST a JSON order payload to /api/webhook/orders/{secret_token}. We
look up the user from the secret, apply their saved field mapping
(JSON key → schema field), and insert a pending_orders document with
source="webhook". The Orders tab picks it up automatically.

Endpoints:
  POST /api/webhook/orders/{secret}        public ingest endpoint
                                           (no auth header — secret-in-URL)
  GET  /api/me/webhook-config              current secret + URL + mapping
  POST /api/me/webhook-config/rotate       generate a fresh secret
                                           (invalidates the old URL)
  PUT  /api/me/webhook-config              save the JSON-key → schema-
                                           field mapping for this user

Auth model:
  Per-user 32-char secret embedded in the URL. Simple, rotatable, no
  HMAC complexity. Cancellation = rotate and ignore old URL. (HMAC
  signature is documented as a follow-up if a customer demands it.)

Mapping shape:
  {
    "json_key_in_payload": "schema_field",
    ...
  }
  e.g. {"shipping_name": "customer_name", "shipping_phone": "customer_phone"}.
  Multiple JSON keys mapping to "address" auto-merge with " ", same as
  CSV/Excel.

Pattern: late-binding `init()` — same as routers/file_import.py.
"""
from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Request
from pydantic import BaseModel

from import_schema import (
    SCHEMA_FIELDS,
    build_pending_doc_from_mapping,
    suggest_mapping,
    validate_mapping_field,
    is_custom_field_mapping,
    custom_field_id,
)


webhook_router = APIRouter(prefix="/api", tags=["webhook"])


class WebhookMappingPayload(BaseModel):
    mapping: Dict[str, str]


class WebhookNamePayload(BaseModel):
    """Phase F2.5 — friendly source label shown on every pending-order
    card that came in via this user's webhook URL. Kept short so the
    UI badge reads cleanly (e.g. "Shopify", "Dukaan", "Meesho")."""
    name: str


class WebhookRotatePayload(BaseModel):
    """Optional payload accepted by /me/webhook-config/rotate so the
    UI can pass the friendly name in a single round-trip on the very
    first generation. `name` is trimmed + capped to 32 chars."""
    name: str | None = None


def _gen_secret() -> str:
    """32-char URL-safe secret, ~190 bits entropy."""
    return secrets.token_urlsafe(24).rstrip("=")


def _build_webhook_url(request: Request, secret: str) -> str:
    """Construct the externally-reachable webhook URL.

    External webhook senders (Dukaan, Shopify, Zapier, …) often refuse
    to deliver to plain `http://` callbacks for security reasons.
    Behind our preview proxy `request.base_url` returns `http://...`
    even though the public ingress is HTTPS, so we MUST upgrade the
    scheme using the X-Forwarded-Proto / X-Forwarded-Host headers
    set by the K8s ingress. We also fall back to the raw `Host`
    header so the URL points at the public hostname, not the in-
    cluster service name.
    """
    fwd_proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
    fwd_host  = request.headers.get("x-forwarded-host", "").split(",")[0].strip()
    fwd_host  = fwd_host or request.headers.get("host", "")
    # Always default to HTTPS — the preview ingress + production both
    # serve TLS, plain-HTTP is never the right answer here.
    proto = (fwd_proto or "https").lower()
    if proto not in ("https", "http"):
        proto = "https"
    if not fwd_host:
        # Last-resort fallback (should never happen — every prod request
        # carries Host). Keep request.base_url so dev curl still works.
        return f"{str(request.base_url).rstrip('/')}/api/webhook/orders/{secret}"
    return f"{proto}://{fwd_host}/api/webhook/orders/{secret}"


def _flatten(obj: Any, prefix: str = "", out: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Flatten nested JSON objects to dotted-key paths so users can map
    `customer.name` → schema_field directly. Lists are joined as
    comma-separated strings (best-effort)."""
    if out is None:
        out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                _flatten(v, key, out)
            elif isinstance(v, list):
                # Join scalar lists with comma; skip dict-lists
                # (probably line items — user can map count/sum
                # downstream if needed).
                if all(not isinstance(x, (dict, list)) for x in v):
                    out[key] = ", ".join(str(x) for x in v if x is not None)
                else:
                    out[key] = v   # keep as-is; user likely won't map this
            else:
                out[key] = v
    return out


def init() -> None:
    import logging
    _logger = logging.getLogger("routers.webhook")
    from server import (  # noqa: WPS433
        db,
        get_current_user as _get_current_user,
        generate_master_order_id,
    )

    async def _list_user_custom_fields(user_id: str) -> List[Dict[str, Any]]:
        cur = db.user_custom_fields.find(
            {"user_id": user_id, "active": {"$ne": False}}, {"_id": 0},
        )
        return [doc async for doc in cur]

    # ════════════════════════════════════════════════════════════════
    # 1. Authenticated config (owner only)
    # ════════════════════════════════════════════════════════════════

    @webhook_router.get("/me/webhook-config")
    async def get_webhook_config(
        request: Request,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        u = await db.users.find_one({"id": current_user["id"]}, {"_id": 0}) or {}
        secret = u.get("webhook_secret") or ""
        full_url = _build_webhook_url(request, secret) if secret else None
        custom_fields = await _list_user_custom_fields(current_user["id"])
        recent_samples = u.get("webhook_recent_samples") or []
        return {
            "secret":          secret,
            "url":             full_url,
            # Phase F2.5 — friendly source name (e.g. "Shopify"). Empty
            # string when the user hasn't named the webhook yet; the
            # UI then defaults the badge to a generic "WEBHOOK" pill.
            "name":            (u.get("webhook_name") or "").strip(),
            "mapping":         u.get("webhook_mapping") or {},
            "schema_fields":   SCHEMA_FIELDS,
            "custom_fields":  [
                {"id": cf.get("id"), "label": cf.get("name") or cf.get("label") or ""}
                for cf in custom_fields
            ],
            "configured":      bool(secret),
            "recent_samples":  recent_samples[-5:],
        }

    @webhook_router.post("/me/webhook-config/rotate")
    async def rotate_webhook_secret(
        request: Request,
        payload: WebhookRotatePayload | None = Body(default=None),
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        new_secret = _gen_secret()
        update: Dict[str, Any] = {
            "webhook_secret":     new_secret,
            "webhook_secret_at":  datetime.now(timezone.utc).isoformat(),
        }
        # Phase F2.5 — also accept a friendly name on first generation
        # so the UI can do "rotate + name" in a single round-trip.
        if payload and payload.name:
            update["webhook_name"] = payload.name.strip()[:32]
        await db.users.update_one(
            {"id": current_user["id"]},
            {"$set": update},
        )
        origin_url = _build_webhook_url(request, new_secret)
        return {
            "secret": new_secret,
            "url":    origin_url,
            "name":   update.get("webhook_name", ""),
        }

    @webhook_router.put("/me/webhook-config/name")
    async def put_webhook_name(
        payload: WebhookNamePayload = Body(...),
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Rename an already-generated webhook. The friendly name is
        used purely as a display badge on imported pending orders so
        the user can tell at a glance which storefront sent which
        order (Shopify / Dukaan / Meesho / custom). Capped at 32 chars
        — long enough for any platform name, short enough that the
        badge doesn't wrap on small phones."""
        clean = (payload.name or "").strip()[:32]
        await db.users.update_one(
            {"id": current_user["id"]},
            {"$set": {"webhook_name": clean}},
        )
        return {"ok": True, "name": clean}

    @webhook_router.put("/me/webhook-config")
    async def put_webhook_config(
        payload: WebhookMappingPayload = Body(...),
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        # Phase F2.2 — accept "custom:<id>" pointers to per-user custom
        # fields, in addition to the canonical SCHEMA_FIELDS list.
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
        await db.users.update_one(
            {"id": current_user["id"]},
            {"$set": {"webhook_mapping": payload.mapping}},
        )
        return {"ok": True, "mapping": payload.mapping}

    # ════════════════════════════════════════════════════════════════
    # 2. Public ingest (secret-in-URL, no auth header)
    # ════════════════════════════════════════════════════════════════

    @webhook_router.post("/webhook/orders/{secret}")
    async def webhook_ingest(
        secret: str = Path(..., min_length=8, max_length=128),
        body: Any = Body(...),
    ):
        # Phase F3 — multi-webhook lookup. Try user_webhooks (v2) FIRST,
        # then fall back to the legacy users.webhook_secret (v1). This
        # keeps every old URL working while unlocking the new event-typed
        # ingest paths. The v2 hit also exposes the per-webhook event
        # type and per-webhook stats, which the v1 path didn't have.
        wh_doc: Dict[str, Any] | None = await db.user_webhooks.find_one(
            {"secret": secret},
            {"_id": 0},
        )
        if wh_doc:
            if not wh_doc.get("enabled", True):
                # Paused webhook → 200 OK with explanation so the sender
                # doesn't mark it as broken / stop retrying. The user
                # can re-enable from the v2 admin UI without rotating.
                return {
                    "ok": True,
                    "imported": 0,
                    "skipped":  0,
                    "errors":   [
                        "Webhook is paused. Re-enable it from "
                        "Shippzo → Webhook Config to resume ingestion.",
                    ],
                }
            mapping: Dict[str, str] = wh_doc.get("mapping") or {}
            user_id     = wh_doc["user_id"]
            wh_id       = wh_doc.get("id")
            event_type  = wh_doc.get("event_type") or "new_order"
            badge_name  = (wh_doc.get("name") or "").strip()
        else:
            # Legacy single-webhook path.
            u = await db.users.find_one(
                {"webhook_secret": secret},
                {"_id": 0, "id": 1, "webhook_mapping": 1, "webhook_name": 1},
            )
            if not u:
                raise HTTPException(status_code=404, detail="Unknown webhook secret")
            mapping     = u.get("webhook_mapping") or {}
            user_id     = u["id"]
            wh_id       = None
            event_type  = "new_order"
            badge_name  = (u.get("webhook_name") or "").strip()
        # Phase F2.4 — Forgiving ingest. External senders (Dukaan,
        # Shopify, Zapier, …) often run a "Test webhook" probe BEFORE
        # the user has a chance to configure column mapping. They mark
        # the webhook as broken if they don't get a 2xx back. We
        # therefore accept the payload, persist the latest 10 samples
        # so the user can use them in the in-app preview UI, and return
        # 200 OK with a friendly note instead of 409.
        # Webhooks may send a single order OR a `{orders: [...]}` array
        # OR a top-level array. Normalise to a list.
        if isinstance(body, list):
            rows: List[Any] = body
        elif isinstance(body, dict) and isinstance(body.get("orders"), list):
            rows = body["orders"]
        elif isinstance(body, dict):
            rows = [body]
        else:
            # Even truly invalid bodies (e.g. plain string) shouldn't
            # 5xx the sender's connectivity test — they should see a
            # graceful 200 with an explanation. (Dukaan UI shows this
            # as the response body in the "Test webhook" panel.)
            return {
                "ok": False,
                "imported": 0,
                "skipped": 0,
                "errors": ["Body must be a JSON object, list, or {orders: [...]}"],
            }

        if len(rows) > 200:
            raise HTTPException(
                status_code=413,
                detail="Webhook batch too large (max 200 orders per call)",
            )

        # Always persist the latest received sample (capped at 10) so
        # the owner can use them in the Webhook Config "Preview" UI to
        # quickly set up the column mapping after the first real ping
        # comes in. Best-effort write; a DB hiccup must not prevent
        # the 200 OK we owe the sender.
        if rows and isinstance(rows[0], dict):
            try:
                if wh_id:
                    # v2 → write to user_webhooks ring buffer + bump stats.
                    await db.user_webhooks.update_one(
                        {"id": wh_id},
                        {
                            "$push": {
                                "recent_samples": {
                                    "$each": [{
                                        "received_at": datetime.now(timezone.utc).isoformat(),
                                        "payload":     rows[0],
                                    }],
                                    "$slice": -10,
                                },
                            },
                            "$set": {
                                "stats.last_received_at": datetime.now(timezone.utc).isoformat(),
                            },
                            "$inc": {"stats.total_received": len(rows)},
                        },
                    )
                else:
                    await db.users.update_one(
                        {"id": user_id},
                        {"$push": {
                            "webhook_recent_samples": {
                                "$each": [{
                                    "received_at": datetime.now(timezone.utc).isoformat(),
                                    "payload":     rows[0],
                                }],
                                "$slice": -10,
                            },
                        }},
                    )
            except Exception:
                _logger.exception("Failed to persist webhook sample")

        if not mapping:
            # Sender pinged us before the mapping is configured. We
            # store the sample (above) and respond with success +
            # explanation so their connectivity test passes. The
            # owner sees these samples on the next visit to the
            # Webhook Config screen.
            return {
                "ok": True,
                "imported": 0,
                "skipped": len(rows),
                "errors": [
                    "Connection OK — payload received but no field "
                    "mapping is configured yet. Open the Shippzo app → "
                    "Settings → Webhook Ingest to map incoming keys to "
                    "shipment fields. (We saved this payload as a "
                    "sample for you.)"
                ],
            }

        # Phase F3 — event-type-aware processing. Right now only
        # `new_order` and `custom` create pending_orders documents
        # (their behaviour is identical at ingest time; "custom" just
        # means the owner opted out of auto-mapping suggestions). The
        # other event types (order_status_update / abandoned_order /
        # customer_created / customer_updated) are accepted, recorded
        # in the recent_samples ring buffer above, and acknowledged
        # with a friendly 200 — full processing for those events lives
        # in Phase F3.2 (status sync) and F3.3 (abandoned + customer
        # tabs). Returning 200 here lets the sender's connectivity
        # test pass and lets the owner see real samples in the UI.
        if event_type not in ("new_order", "custom"):
            return {
                "ok": True,
                "imported": 0,
                "skipped":  len(rows),
                "event_type": event_type,
                "errors": [
                    f"Event '{event_type}' received and logged. "
                    "Full processing for this event type is coming in "
                    "an upcoming release.",
                ],
            }

        imported = 0
        errors:   List[str] = []
        now = datetime.now(timezone.utc).isoformat()

        for idx, raw in enumerate(rows):
            if not isinstance(raw, dict):
                errors.append(f"row {idx}: not a JSON object")
                continue
            flat = _flatten(raw)
            try:
                doc = build_pending_doc_from_mapping(flat, mapping)
            except ValueError as e:
                errors.append(f"row {idx}: {e}")
                continue

            if not doc.get("customer_name") and not doc.get("customer_phone"):
                errors.append(f"row {idx}: missing customer_name and customer_phone")
                continue

            # Phase F2.2 — relocate the schema-level "status" /
            # "created_at_override" cells onto dedicated keys consumed
            # by ship_pending_order. Same logic as file_import.py.
            imp_status = (doc.pop("status", "") or "").strip() if "status" in {
                m for m in mapping.values()
            } else ""
            imp_at = (doc.pop("created_at_override", "") or "").strip() if "created_at_override" in {
                m for m in mapping.values()
            } else ""
            doc.update({
                "id":              str(uuid.uuid4()),
                "user_id":         user_id,
                "source":          "webhook",
                "status":          "pending",   # pipeline status
                "imported_status": imp_status,
                "imported_at":     imp_at,
                "master_order_id": await generate_master_order_id(),
                "created_at":      now,
                "source_meta": {
                    "received_at":  now,
                    "remote_keys":  list(flat.keys())[:30],
                    # Phase F2.5 — friendly source name (e.g. "Shopify").
                    # The pending-orders UI uses this as the badge label
                    # so the user can tell at a glance which storefront
                    # an order came from.
                    "webhook_name": badge_name,
                    # Phase F3 — also persist the event type + webhook id
                    # so the Pending Orders screen can colour-code by
                    # storefront and the user can drill down.
                    "webhook_id":   wh_id or "",
                    "event_type":   event_type,
                },
            })
            doc["order_id"] = doc.get("order_id") or doc["master_order_id"]
            await db.pending_orders.insert_one(doc)
            imported += 1

        # Phase F3 — bump per-webhook imported counter (v2 only).
        if wh_id and imported:
            try:
                await db.user_webhooks.update_one(
                    {"id": wh_id},
                    {"$inc": {"stats.total_imported": imported}},
                )
            except Exception:
                _logger.exception("Failed to bump webhook stats.total_imported")

        _logger.info(
            "webhook ingest: user=%s wh=%s event=%s imported=%d skipped=%d",
            user_id, wh_id or "legacy", event_type, imported, len(rows) - imported,
        )
        return {
            "ok":         True,
            "imported":   imported,
            "skipped":    len(rows) - imported,
            "event_type": event_type,
            "errors":     errors[:10],
        }

    # ════════════════════════════════════════════════════════════════
    # 3. Sample-payload preview (auth-required helper for the UI)
    # ════════════════════════════════════════════════════════════════

    @webhook_router.post("/me/webhook-config/preview")
    async def webhook_preview(
        body: Any = Body(
            ..., description="Paste a sample JSON payload here",
        ),
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Lets the UI paste a sample JSON payload (e.g. one captured
        from Shopify) and shows the user every flattened key + a
        suggested mapping (saved-default + alias heuristics + per-user
        custom-field name match)."""
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
        u = await db.users.find_one(
            {"id": current_user["id"]}, {"_id": 0, "webhook_mapping": 1},
        ) or {}
        custom_fields = await _list_user_custom_fields(current_user["id"])
        suggested = suggest_mapping(keys, u.get("webhook_mapping"), custom_fields)
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

    _logger.info("webhook router mounted (5 endpoints)")
