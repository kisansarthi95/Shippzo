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
    SCHEMA_FIELDS, build_pending_doc_from_mapping, suggest_mapping,
)


webhook_router = APIRouter(prefix="/api", tags=["webhook"])


class WebhookMappingPayload(BaseModel):
    mapping: Dict[str, str]


def _gen_secret() -> str:
    """32-char URL-safe secret, ~190 bits entropy."""
    return secrets.token_urlsafe(24).rstrip("=")


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
        # URL: take origin from request, append /api/webhook/orders/<secret>
        origin = str(request.base_url).rstrip("/")
        full_url = f"{origin}/api/webhook/orders/{secret}" if secret else None
        return {
            "secret":         secret,
            "url":            full_url,
            "mapping":        u.get("webhook_mapping") or {},
            "schema_fields":  SCHEMA_FIELDS,
            "configured":     bool(secret),
        }

    @webhook_router.post("/me/webhook-config/rotate")
    async def rotate_webhook_secret(
        request: Request,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        new_secret = _gen_secret()
        await db.users.update_one(
            {"id": current_user["id"]},
            {"$set": {
                "webhook_secret":     new_secret,
                "webhook_secret_at":  datetime.now(timezone.utc).isoformat(),
            }},
        )
        origin = str(request.base_url).rstrip("/")
        return {
            "secret": new_secret,
            "url":    f"{origin}/api/webhook/orders/{new_secret}",
        }

    @webhook_router.put("/me/webhook-config")
    async def put_webhook_config(
        payload: WebhookMappingPayload = Body(...),
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        bad = [v for v in payload.mapping.values() if v and v not in SCHEMA_FIELDS]
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
        body: Dict[str, Any] = Body(...),
    ):
        u = await db.users.find_one(
            {"webhook_secret": secret}, {"_id": 0, "id": 1, "webhook_mapping": 1},
        )
        if not u:
            raise HTTPException(status_code=404, detail="Unknown webhook secret")

        mapping: Dict[str, str] = u.get("webhook_mapping") or {}
        if not mapping:
            raise HTTPException(
                status_code=409,
                detail=(
                    "No mapping configured. Visit Settings → Webhook to map "
                    "incoming JSON keys to shipment fields."
                ),
            )

        # Webhooks may send a single order OR a `{orders: [...]}` array
        # OR a top-level array. Normalise to a list.
        if isinstance(body, list):
            rows: List[Any] = body
        elif isinstance(body, dict) and isinstance(body.get("orders"), list):
            rows = body["orders"]
        else:
            rows = [body]

        if len(rows) > 200:
            raise HTTPException(
                status_code=413,
                detail="Webhook batch too large (max 200 orders per call)",
            )

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

            doc.update({
                "id":              str(uuid.uuid4()),
                "user_id":         u["id"],
                "source":          "webhook",
                "status":          "pending",
                "master_order_id": await generate_master_order_id(),
                "created_at":      now,
                "source_meta": {
                    "received_at": now,
                    "remote_keys": list(flat.keys())[:30],   # first 30 for audit
                },
            })
            doc["order_id"] = doc.get("order_id") or doc["master_order_id"]
            await db.pending_orders.insert_one(doc)
            imported += 1

        _logger.info(
            "webhook ingest: user=%s imported=%d skipped=%d",
            u["id"], imported, len(rows) - imported,
        )
        return {
            "ok":       True,
            "imported": imported,
            "skipped":  len(rows) - imported,
            "errors":   errors[:10],
        }

    # ════════════════════════════════════════════════════════════════
    # 3. Sample-payload preview (auth-required helper for the UI)
    # ════════════════════════════════════════════════════════════════

    @webhook_router.post("/me/webhook-config/preview")
    async def webhook_preview(
        body: Dict[str, Any] = Body(
            ..., description="Paste a sample JSON payload here",
        ),
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Lets the UI paste a sample JSON payload (e.g. one captured
        from Shopify) and shows the user every flattened key + a
        suggested mapping (saved-default + alias heuristics)."""
        rows = (
            body if isinstance(body, list)
            else body.get("orders") if isinstance(body, dict) and isinstance(body.get("orders"), list)
            else [body]
        )
        first = rows[0] if rows and isinstance(rows[0], dict) else {}
        flat  = _flatten(first)
        keys  = list(flat.keys())
        u = await db.users.find_one({"id": current_user["id"]}, {"_id": 0, "webhook_mapping": 1}) or {}
        suggested = suggest_mapping(keys, u.get("webhook_mapping"))
        return {
            "keys":          keys,
            "sample_values": {k: flat[k] for k in keys[:30]},
            "schema_fields": SCHEMA_FIELDS,
            "suggested":     suggested,
        }

    _logger.info("webhook router mounted (5 endpoints)")
