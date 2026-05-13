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

    # Phase F3.9.9 — Auto-recover sweep helper. Module-private. Called
    # from the new_order ingest after we insert each pending_doc.
    # See call site for the full rationale; in short: when the
    # incoming order matches a still-abandoned cart for the same user
    # we flip the cart to `status=recovered_auto` so it vanishes from
    # the Abandoned filter without the operator having to lift a
    # finger.
    async def _auto_recover_abandoned_cart(
        db,
        user_id: str,
        flat: Dict[str, Any],
        raw: Dict[str, Any],
        pending_doc: Dict[str, Any],
    ) -> None:
        # ── 1. Collect candidate cart-side identifiers ────────────
        # Anything the storefront might use as the bridge between
        # the abandoned-cart event and the eventually-completed order.
        cart_id_candidates: set = set()
        for k in (
            "order.uuid", "order.cart_token", "order.checkout_token",
            "cart_token", "checkout_token", "uuid", "checkout_id",
            "cart_id", "order.id", "order.order_id",
        ):
            v = flat.get(k)
            if isinstance(v, (str, int)) and str(v).strip():
                cart_id_candidates.add(str(v).strip())
        ext_oid = (pending_doc.get("external_order_id") or "").strip()
        if ext_oid:
            cart_id_candidates.add(ext_oid)

        # ── 2. PRIMARY match — by cart id ─────────────────────────
        cart = None
        if cart_id_candidates:
            cart = await db.abandoned_carts.find_one(
                {
                    "user_id": user_id,
                    "status":  "abandoned",
                    "external_cart_id": {"$in": list(cart_id_candidates)},
                },
                {"_id": 0},
            )

        # ── 3. FALLBACK match — phone + amount + 24-hour window ──
        if cart is None:
            from datetime import datetime, timezone, timedelta
            phone_raw = pending_doc.get("customer_phone") or ""
            phone_norm = "".join(ch for ch in phone_raw if ch.isdigit())[-10:]
            amount = float(pending_doc.get("amount") or 0.0)
            if phone_norm and amount > 0:
                window_start = (
                    datetime.now(timezone.utc) - timedelta(hours=24)
                ).isoformat()
                lo, hi = amount * 0.95, amount * 1.05
                cart = await db.abandoned_carts.find_one(
                    {
                        "user_id": user_id,
                        "status":  "abandoned",
                        "customer_phone_norm": phone_norm,
                        "cart_value": {"$gte": lo, "$lte": hi},
                        "abandoned_at": {"$gte": window_start},
                    },
                    {"_id": 0},
                )
                # Some carts store customer_phone WITHOUT a `_norm`
                # column. Retry on a regex over the raw column as a
                # last resort so legacy carts also get recovered.
                if cart is None:
                    cart = await db.abandoned_carts.find_one(
                        {
                            "user_id": user_id,
                            "status":  "abandoned",
                            "customer_phone": {
                                "$regex": phone_norm + "$",
                            },
                            "cart_value": {"$gte": lo, "$lte": hi},
                            "abandoned_at": {"$gte": window_start},
                        },
                        {"_id": 0},
                    )

        if cart is None:
            return    # No matching abandoned cart — nothing to do.

        # ── 4. Stamp the cart as auto-recovered ──────────────────
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        await db.abandoned_carts.update_one(
            {"id": cart["id"], "user_id": user_id},
            {"$set": {
                "status":                  "recovered_auto",
                "recovered_at":            now,
                "recovered_pending_order_id": pending_doc.get("id") or "",
                "recovered_external_order_id": ext_oid,
                "updated_at":              now,
            }},
        )
        logging.getLogger("routers.webhook").info(
            "auto-recover: user=%s cart=%s → pending=%s ext_oid=%s",
            user_id, cart.get("id"), pending_doc.get("id"), ext_oid,
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
            source_app  = (wh_doc.get("source_app") or "").strip().lower()
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
            source_app  = ""
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

        if not mapping and event_type in ("new_order", "custom"):
            # Sender pinged us before the mapping is configured. We
            # store the sample (above) and respond with success +
            # explanation so their connectivity test passes. The
            # owner sees these samples on the next visit to the
            # Webhook Config screen.
            return {
                "ok": True,
                "imported": 0,
                "skipped": len(rows),
                "event_type": event_type,
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
        # means the owner opted out of auto-mapping suggestions).
        # `order_status_update` finds an existing shipment / pending
        # order by id and updates its status — see the dedicated
        # branch below. The remaining types (abandoned_order /
        # customer_created / customer_updated) are accepted, recorded
        # in the recent_samples ring buffer above, and acknowledged
        # with a friendly 200 — full processing for those events lives
        # in Phase F3.3 (abandoned + customer tabs).
        if event_type == "order_status_update":
            # Phase F3.2 — Order Status Update.
            # The mapping must point at least to:
            #   • order_id (used to find the existing shipment)
            #   • status   (the new status to set)
            # Optional: created_at_override (if the source carries the
            # transition timestamp; we use that as `dispatched_at`,
            # `delivered_at` etc. depending on the resolved status).
            #
            # Phase F3.2 (rev-2) — Auto-detection. When the user's
            # mapping is missing for this simple event type (the most
            # common case — almost every store uses `order_id` /
            # `status` / `state` literally), we synthesise a sensible
            # default mapping by scanning the row's keys ourselves.
            # This means a brand-new "Status Update" webhook works out
            # of the box without the user opening the mapping screen.
            from import_schema import normalise_status, normalise_timestamp

            updated = 0
            not_found = 0
            errors_status: List[str] = []
            now = datetime.now(timezone.utc).isoformat()

            # Source-key candidates we scan for in order: most-specific
            # first, falling through to generic ones. Done lazily per-
            # row because nested dicts produce different flattened key
            # spaces and we don't want to assume anything.
            ORDER_ID_CANDIDATES = (
                "master_order_id", "order_number", "order_id",
                "orderId", "orderID", "order_no", "id",
            )
            STATUS_CANDIDATES = (
                "status", "order_status", "fulfillment_status",
                "state", "stage",
            )
            TIMESTAMP_CANDIDATES = (
                "updated_at", "shipped_at", "delivered_at",
                "status_updated_at", "event_at", "created_at",
                "timestamp", "date",
            )

            def _pick(flat: Dict[str, Any], candidates: tuple) -> str:
                """Walk the candidate list and return the first matching
                key's value (case-insensitive). Empty string if nothing
                matched — caller decides if that's fatal."""
                low = {k.lower(): k for k in flat.keys()}
                for cand in candidates:
                    real = low.get(cand.lower())
                    if real and flat.get(real) not in (None, ""):
                        return str(flat[real]).strip()
                return ""

            for idx, raw in enumerate(rows):
                if not isinstance(raw, dict):
                    errors_status.append(f"row {idx}: not a JSON object")
                    continue
                flat = _flatten(raw)

                # Try the user-configured mapping first; if it doesn't
                # produce both an order_id and a status we fall back to
                # the auto-detector. This keeps power users who DO want
                # custom mapping (e.g. `event.payload.order.id`) happy
                # while Just Working for the 99% case.
                order_id  = ""
                new_status_raw = ""
                event_ts_raw   = ""

                if mapping:
                    try:
                        doc = build_pending_doc_from_mapping(flat, mapping)
                        order_id        = (doc.get("order_id") or "").strip()
                        new_status_raw  = (doc.get("status")   or "").strip()
                        event_ts_raw    = (doc.get("created_at_override") or "").strip()
                    except ValueError:
                        pass   # fall through to auto

                if not order_id:
                    order_id = _pick(flat, ORDER_ID_CANDIDATES)
                if not new_status_raw:
                    new_status_raw = _pick(flat, STATUS_CANDIDATES)
                if not event_ts_raw:
                    event_ts_raw = _pick(flat, TIMESTAMP_CANDIDATES)

                if not order_id:
                    errors_status.append(
                        f"row {idx}: couldn't find an order id in the "
                        "payload (looked for order_id, id, order_number, "
                        "master_order_id, …).",
                    )
                    continue

                new_status = normalise_status(new_status_raw)

                # Normalise the timestamp; default to "now" if none.
                event_ts = normalise_timestamp(event_ts_raw) or now

                # Match by master_order_id OR order_id, scoped to this
                # user. A single ingest can update either a Shipment
                # (already shipped) or a Pending Order (still in inbox).
                update_set: Dict[str, Any] = {
                    "status_updated_at": event_ts,
                }
                if new_status:
                    update_set["status"] = new_status
                    # Mirror onto well-known timestamp fields so the
                    # rest of the app's analytics / SLA workers don't
                    # need to be re-taught the new event-type semantics.
                    if new_status == "Shipped":
                        update_set["dispatched_at"] = event_ts
                    elif new_status == "Delivered":
                        update_set["delivered_at"] = event_ts
                    elif new_status == "Returned":
                        update_set["returned_at"] = event_ts

                # Match by external_order_id, master_order_id, OR
                # internal id — scoped to this user. A single ingest
                # can update either a Shipment (already shipped) or a
                # Pending Order (still in inbox).
                base_or = [
                    {"external_order_id": order_id},
                    {"master_order_id":   order_id},
                    {"order_id":          order_id},
                    {"id":                order_id},
                ]

                # Phase F3.2 (rev-3) — Source-strict matching.
                # If the status-update webhook has a `source_app` set,
                # we ONLY update orders that came from a webhook with
                # the SAME source_app. This prevents Dukaan #123's
                # status update from accidentally touching Shopify
                # #123. Webhooks without a source_app fall back to
                # the legacy behaviour (any matching order_id).
                ship_filter: Dict[str, Any] = {
                    "user_id": user_id,
                    "$or":     base_or,
                }
                if source_app:
                    ship_filter["source_meta.source_app"] = source_app

                ship_res = await db.shipments.update_one(
                    ship_filter, {"$set": update_set},
                )
                if ship_res.matched_count == 0:
                    # Phase F3.2 (rev-3) — Pending-order auto-update.
                    # When a status update lands on a still-pending
                    # order (i.e. the user hasn't shipped it yet), we
                    # apply the status BUT also raise `needs_review`
                    # so the orange banner on the Pending Orders card
                    # warns the user that the source app says the
                    # order has progressed externally. They can either
                    # accept it (cancel the local pending) or process
                    # the order normally (which clears the flag).
                    pend_set = {
                        **update_set,
                        "needs_review":              True,
                        "needs_review_reason":       (
                            f"Source reported status='{new_status or new_status_raw}' "
                            "while order is still pending."
                        ),
                        "needs_review_at":           event_ts,
                    }
                    pend_res = await db.pending_orders.update_one(
                        ship_filter, {"$set": pend_set},
                    )
                    if pend_res.matched_count == 0 and source_app:
                        # Strict match missed → try one more time without
                        # the source filter so legacy / pre-rev-3 orders
                        # (created before source_app was tracked) still
                        # pick up updates. Logged as a soft-warning.
                        loose_filter: Dict[str, Any] = {
                            "user_id": user_id,
                            "$or":     base_or,
                        }
                        ship_res2 = await db.shipments.update_one(
                            loose_filter, {"$set": update_set},
                        )
                        if ship_res2.matched_count == 0:
                            pend_res2 = await db.pending_orders.update_one(
                                loose_filter, {"$set": pend_set},
                            )
                            if pend_res2.matched_count == 0:
                                not_found += 1
                                errors_status.append(
                                    f"row {idx}: no shipment / pending "
                                    f"order found for order_id='{order_id}'",
                                )
                                continue
                    elif pend_res.matched_count == 0:
                        not_found += 1
                        errors_status.append(
                            f"row {idx}: no shipment / pending order "
                            f"found for order_id='{order_id}'",
                        )
                        continue

                updated += 1

            if wh_id and updated:
                try:
                    await db.user_webhooks.update_one(
                        {"id": wh_id},
                        {"$inc": {"stats.total_imported": updated}},
                    )
                except Exception:
                    _logger.exception("Failed to bump webhook stats.total_imported")

            _logger.info(
                "webhook ingest: user=%s wh=%s event=order_status_update "
                "updated=%d not_found=%d",
                user_id, wh_id or "legacy", updated, not_found,
            )
            return {
                "ok":         True,
                "imported":   updated,
                "skipped":    len(rows) - updated,
                "not_found":  not_found,
                "event_type": event_type,
                "errors":     errors_status[:10],
            }

        if event_type not in ("new_order", "custom"):
            # Phase F3.3 — Abandoned cart + Customer event processing.
            # The remaining v2 event types (abandoned_order /
            # customer_created / customer_updated) get full structured
            # storage here so the dedicated UIs (Abandoned Carts screen
            # + Customers screen) can list / search / recover them.
            #
            # All three flows use auto-detect heuristics on the
            # FLATTENED payload first, then layer in any explicit
            # user-mapping on top (so power users can still override).
            from import_schema import normalise_timestamp

            def _pick(flat: Dict[str, Any], candidates: tuple) -> str:
                low = {k.lower(): k for k in flat.keys()}
                for cand in candidates:
                    real = low.get(cand.lower())
                    if real and flat.get(real) not in (None, ""):
                        return str(flat[real]).strip()
                return ""

            now_iso = datetime.now(timezone.utc).isoformat()

            if event_type == "abandoned_order":
                # Phase F3.8.2 — Each tuple is duplicated with an
                # `order.` prefix so Dukaan-style payloads of the shape
                # {"order": {"buyer": {...}, "shipping_address": {...}}}
                # are auto-detected even when the user hasn't configured
                # a mapping yet. (When a mapping IS configured the
                # build_pending_doc_from_mapping call below takes priority
                # — these candidates are the fallback path.)
                CART_ID_CANDIDATES = (
                    "cart_id", "abandoned_checkout_id", "checkout_id",
                    "cart_token", "token", "id", "order_id",
                    "uuid", "display_order_id", "display_id",
                    "order.uuid", "order.id", "order.order_id",
                    "order.cart_id", "order.checkout_id",
                    "order.display_order_id", "order.display_id",
                    "order.cart_token", "order.token",
                )
                NAME_CANDIDATES = (
                    "customer_name", "customer.name", "name",
                    "billing_name", "shipping_name",
                    "billing_address.name", "shipping_address.name",
                    "customer.first_name", "first_name",
                    "full_name", "buyer.name", "buyer.first_name",
                    "buyer.full_name",
                    "order.customer_name", "order.customer.name",
                    "order.name", "order.full_name",
                    "order.buyer.name", "order.buyer.first_name",
                    "order.buyer.full_name",
                    "order.billing_address.name",
                    "order.billing_address.full_name",
                    "order.billing_address.first_name",
                    "order.shipping_address.name",
                    "order.shipping_address.full_name",
                    "order.shipping_address.first_name",
                )
                LASTNAME_CANDIDATES = (
                    "customer.last_name", "last_name",
                    "billing_address.last_name", "shipping_address.last_name",
                    "buyer.last_name",
                    "order.customer.last_name", "order.last_name",
                    "order.buyer.last_name",
                    "order.billing_address.last_name",
                    "order.shipping_address.last_name",
                )
                PHONE_CANDIDATES = (
                    "customer_phone", "customer.phone", "phone",
                    "mobile", "contact_phone", "billing_phone",
                    "shipping_phone", "billing_address.phone",
                    "shipping_address.phone", "buyer.phone", "buyer_phone",
                    "order.customer_phone", "order.customer.phone",
                    "order.phone", "order.mobile",
                    "order.contact_phone", "order.billing_phone",
                    "order.shipping_phone",
                    "order.buyer.phone", "order.buyer_phone",
                    "order.billing_address.phone",
                    "order.shipping_address.phone",
                )
                EMAIL_CANDIDATES = (
                    "customer_email", "customer.email", "email",
                    "contact_email", "buyer_email", "buyer.email",
                    "order.customer_email", "order.customer.email",
                    "order.email", "order.contact_email",
                    "order.buyer_email", "order.buyer.email",
                )
                VALUE_CANDIDATES = (
                    "total_price", "subtotal_price", "total",
                    "cart_total", "total_amount", "value", "amount",
                    "grand_total", "total_cost",
                    "order.total_price", "order.subtotal_price",
                    "order.total", "order.cart_total",
                    "order.total_amount", "order.value", "order.amount",
                    "order.grand_total", "order.total_cost",
                )
                ABANDONED_AT_CANDIDATES = (
                    "abandoned_at", "abandoned_checkout_at",
                    "updated_at", "created_at", "timestamp",
                    "order.abandoned_at", "order.abandoned_checkout_at",
                    "order.updated_at", "order.created_at",
                    "order.timestamp",
                )
                RECOVERY_URL_CANDIDATES = (
                    "abandoned_checkout_url", "recovery_url",
                    "checkout_url", "abandoned_url", "url",
                    "order_status_url", "payment_url",
                    "order.abandoned_checkout_url", "order.recovery_url",
                    "order.checkout_url", "order.abandoned_url",
                    "order.url", "order.order_status_url",
                    "order.payment_url",
                )
                ITEMS_CANDIDATES = (
                    "items", "line_items", "products", "cart_items",
                    "skus", "order_items",
                    "order.items", "order.line_items",
                    "order.products", "order.cart_items",
                    "order.skus", "order.order_items",
                )
                ADDRESS_CANDIDATES = (
                    "address", "shipping_address.address1",
                    "shipping_address.address_1",
                    "billing_address.address1",
                    "billing_address.address_1",
                    "address_line1", "address1", "address_1",
                    "shipping_address", "buyer.address",
                    "order.address",
                    "order.shipping_address.address1",
                    "order.shipping_address.address_1",
                    "order.billing_address.address1",
                    "order.billing_address.address_1",
                    "order.address_line1", "order.address1",
                    "order.address_1", "order.shipping_address",
                    "order.buyer.address",
                )
                CITY_CANDIDATES = (
                    "city", "shipping_address.city",
                    "billing_address.city",
                    "order.city",
                    "order.shipping_address.city",
                    "order.billing_address.city",
                )
                STATE_CANDIDATES = (
                    "state", "province", "shipping_address.province",
                    "billing_address.province",
                    "shipping_address.state",
                    "billing_address.state",
                    "order.state", "order.province",
                    "order.shipping_address.province",
                    "order.billing_address.province",
                    "order.shipping_address.state",
                    "order.billing_address.state",
                )
                PIN_CANDIDATES = (
                    "pincode", "postal_code", "zip",
                    "shipping_address.zip", "billing_address.zip",
                    "shipping_address.postal_code",
                    "shipping_address.pincode",
                    "billing_address.pincode",
                    "order.pincode", "order.postal_code", "order.zip",
                    "order.shipping_address.zip",
                    "order.billing_address.zip",
                    "order.shipping_address.postal_code",
                    "order.shipping_address.pincode",
                    "order.billing_address.pincode",
                )

                imported = 0
                skipped = 0
                errors_ac: List[str] = []

                for idx, raw in enumerate(rows):
                    if not isinstance(raw, dict):
                        errors_ac.append(f"row {idx}: not a JSON object")
                        skipped += 1
                        continue
                    flat = _flatten(raw)

                    # Phase F3.8.2 — Mapping-first ingest.
                    # If the user has configured a field mapping for
                    # this webhook (typical for Dukaan/Shopify installs
                    # with 30+ keys), apply build_pending_doc_from_mapping
                    # FIRST and use its outputs as the primary source.
                    # _pick(...) heuristics then fill in any gaps the
                    # mapping didn't cover. This fixes the bug where
                    # Dukaan abandoned events with a proper 34-field
                    # mapping were importing 0 rows because the branch
                    # only looked at auto-detect candidates.
                    mapped: Dict[str, Any] = {}
                    if mapping:
                        try:
                            mapped = build_pending_doc_from_mapping(flat, mapping)
                        except Exception:
                            mapped = {}

                    def _from_mapping(key: str) -> str:
                        v = mapped.get(key, "")
                        if v in (None, ""):
                            return ""
                        return str(v).strip()

                    # Mapping wins; _pick is the fallback.
                    cart_id = _from_mapping("order_id") or _pick(flat, CART_ID_CANDIDATES)
                    name    = _from_mapping("customer_name") or _pick(flat, NAME_CANDIDATES)
                    if not _from_mapping("customer_name"):
                        # Only do the first+last join when mapping didn't
                        # already give us a clean joined name. (The
                        # mapping path joins multi-source customer_name
                        # itself — see build_pending_doc_from_mapping.)
                        last = _pick(flat, LASTNAME_CANDIDATES)
                        if name and last and last.lower() not in name.lower():
                            name = f"{name} {last}".strip()
                    phone   = _from_mapping("customer_phone") or _pick(flat, PHONE_CANDIDATES)
                    email   = _from_mapping("customer_email") or _pick(flat, EMAIL_CANDIDATES)

                    # Amount: prefer mapping's numeric coercion.
                    cart_value = 0.0
                    if mapped.get("amount") not in (None, "", 0.0, 0):
                        try:
                            cart_value = float(mapped.get("amount") or 0.0)
                        except (TypeError, ValueError):
                            cart_value = 0.0
                    if not cart_value:
                        value_s = _pick(flat, VALUE_CANDIDATES)
                        try:
                            cart_value = float(str(value_s).replace(",", "")) if value_s else 0.0
                        except Exception:
                            cart_value = 0.0

                    abandoned_at_raw = (
                        _from_mapping("created_at_override")
                        or _pick(flat, ABANDONED_AT_CANDIDATES)
                    )
                    abandoned_at = normalise_timestamp(abandoned_at_raw) or now_iso
                    recovery_url = _pick(flat, RECOVERY_URL_CANDIDATES)
                    # address_line1 is the mapping target the schema joins
                    # multi-column `address` mappings into.
                    address = _from_mapping("address_line1") or _pick(flat, ADDRESS_CANDIDATES)
                    city    = _from_mapping("city")     or _pick(flat, CITY_CANDIDATES)
                    state   = _from_mapping("state")    or _pick(flat, STATE_CANDIDATES)
                    pincode = _from_mapping("pincode")  or _pick(flat, PIN_CANDIDATES)

                    items_raw = None
                    # Phase F3.8.2 — Check `flat` first so nested
                    # paths like `order.line_items` resolve. Fall back
                    # to top-level `raw` for legacy single-object
                    # payloads.
                    for c in ITEMS_CANDIDATES:
                        if c in flat and isinstance(flat[c], list):
                            items_raw = flat[c]
                            break
                        if c in raw and isinstance(raw[c], list):
                            items_raw = raw[c]
                            break
                    items_count = len(items_raw) if isinstance(items_raw, list) else 0
                    items_summary = ""
                    if items_raw:
                        names = []
                        for it in items_raw[:5]:
                            if isinstance(it, dict):
                                t = (it.get("title") or it.get("name")
                                     or it.get("product_name") or "").strip()
                                if t:
                                    names.append(t)
                        if names:
                            items_summary = ", ".join(names)
                            if items_count > len(names):
                                items_summary += f" (+{items_count - len(names)} more)"
                        else:
                            items_summary = f"{items_count} item(s)"

                    if not cart_id and not phone and not email and not name:
                        errors_ac.append(
                            f"row {idx}: couldn't locate cart_id / "
                            "customer_phone / customer_email / "
                            "customer_name in the payload",
                        )
                        skipped += 1
                        continue

                    # Source-strict upsert. Same external_cart_id from
                    # the same source_app collapses into one row (latest
                    # snapshot wins).
                    upsert_filter: Dict[str, Any] = {"user_id": user_id}
                    if cart_id:
                        upsert_filter["external_cart_id"] = cart_id
                        upsert_filter["source_meta.source_app"] = source_app or ""
                    else:
                        upsert_filter["customer_phone"] = phone or ""
                        upsert_filter["customer_email"] = email or ""
                        upsert_filter["source_meta.source_app"] = source_app or ""

                    set_doc = {
                        "user_id":          user_id,
                        "external_cart_id": cart_id,
                        "customer_name":    name,
                        "customer_phone":   phone,
                        "customer_email":   email,
                        "address":          address,
                        "city":             city,
                        "state":            state,
                        "pincode":          pincode,
                        "cart_value":       cart_value,
                        "items_count":      items_count,
                        "items_summary":    items_summary,
                        "items_raw":        items_raw or [],
                        "abandoned_at":     abandoned_at,
                        "recovery_url":     recovery_url,
                        "status":           "abandoned",
                        "updated_at":       now_iso,
                        "source_meta": {
                            "webhook_id":   wh_id or "",
                            "webhook_name": badge_name,
                            "source_app":   source_app,
                            "received_at":  now_iso,
                        },
                    }
                    set_on_insert = {
                        "id":            str(uuid.uuid4()),
                        "created_at":    now_iso,
                        "recovered_at":  None,
                        "dismissed_at":  None,
                        "pending_order_id": None,
                    }
                    await db.abandoned_carts.update_one(
                        upsert_filter,
                        {
                            "$set":         set_doc,
                            "$setOnInsert": set_on_insert,
                        },
                        upsert=True,
                    )
                    imported += 1

                if wh_id and imported:
                    try:
                        await db.user_webhooks.update_one(
                            {"id": wh_id},
                            {"$inc": {"stats.total_imported": imported}},
                        )
                    except Exception:
                        _logger.exception("Failed to bump abandoned-cart stats")

                _logger.info(
                    "webhook ingest: user=%s wh=%s event=abandoned_order "
                    "imported=%d skipped=%d", user_id, wh_id or "legacy",
                    imported, skipped,
                )
                return {
                    "ok":         True,
                    "imported":   imported,
                    "skipped":    skipped,
                    "event_type": event_type,
                    "errors":     errors_ac[:10],
                }

            if event_type in ("customer_created", "customer_updated"):
                CUST_ID_CANDIDATES = (
                    "customer_id", "id", "customer.id", "user_id",
                    "buyer_id",
                )
                NAME_CANDIDATES = (
                    "customer_name", "customer.name", "name",
                    "first_name", "customer.first_name",
                )
                LASTNAME_CANDIDATES = (
                    "customer.last_name", "last_name",
                )
                PHONE_CANDIDATES = (
                    "customer_phone", "customer.phone", "phone",
                    "mobile", "contact_phone",
                )
                EMAIL_CANDIDATES = (
                    "customer_email", "customer.email", "email",
                )
                ADDRESS_CANDIDATES = (
                    "default_address.address1",
                    "addresses.0.address1", "address",
                    "address_line1", "address1",
                )
                CITY_CANDIDATES = (
                    "default_address.city", "addresses.0.city", "city",
                )
                STATE_CANDIDATES = (
                    "default_address.province",
                    "default_address.state", "state", "province",
                )
                PIN_CANDIDATES = (
                    "default_address.zip", "default_address.postal_code",
                    "pincode", "zip", "postal_code",
                )
                ORDERS_CANDIDATES = (
                    "orders_count", "total_orders", "order_count",
                )
                SPENT_CANDIDATES = (
                    "total_spent", "lifetime_spend",
                    "lifetime_value", "ltv",
                )
                CREATED_CANDIDATES = (
                    "created_at", "customer.created_at",
                )
                UPDATED_CANDIDATES = (
                    "updated_at", "customer.updated_at",
                )

                imported = 0
                skipped = 0
                errors_cu: List[str] = []

                for idx, raw in enumerate(rows):
                    if not isinstance(raw, dict):
                        errors_cu.append(f"row {idx}: not a JSON object")
                        skipped += 1
                        continue
                    flat = _flatten(raw)

                    ext_cust_id = _pick(flat, CUST_ID_CANDIDATES)
                    name = _pick(flat, NAME_CANDIDATES)
                    last = _pick(flat, LASTNAME_CANDIDATES)
                    if name and last and last.lower() not in name.lower():
                        name = f"{name} {last}".strip()
                    phone = _pick(flat, PHONE_CANDIDATES)
                    email = _pick(flat, EMAIL_CANDIDATES)

                    if not ext_cust_id and not phone and not email:
                        errors_cu.append(
                            f"row {idx}: couldn't locate customer_id / "
                            "phone / email in the payload",
                        )
                        skipped += 1
                        continue

                    address = _pick(flat, ADDRESS_CANDIDATES)
                    city    = _pick(flat, CITY_CANDIDATES)
                    state   = _pick(flat, STATE_CANDIDATES)
                    pincode = _pick(flat, PIN_CANDIDATES)
                    try:
                        orders_count = int(_pick(flat, ORDERS_CANDIDATES) or 0)
                    except Exception:
                        orders_count = 0
                    try:
                        total_spent = float(
                            str(_pick(flat, SPENT_CANDIDATES) or 0).replace(",", ""),
                        )
                    except Exception:
                        total_spent = 0.0
                    src_created = normalise_timestamp(
                        _pick(flat, CREATED_CANDIDATES),
                    ) or ""
                    src_updated = normalise_timestamp(
                        _pick(flat, UPDATED_CANDIDATES),
                    ) or now_iso

                    # Upsert key: prefer (user_id, source_app, ext_cust_id);
                    # fall back to (user_id, source_app, phone) when
                    # ext_cust_id is missing.
                    upsert_filter: Dict[str, Any] = {
                        "user_id": user_id,
                        "source_meta.source_app": source_app or "",
                    }
                    if ext_cust_id:
                        upsert_filter["external_customer_id"] = ext_cust_id
                    elif phone:
                        upsert_filter["customer_phone"] = phone
                    else:
                        upsert_filter["customer_email"] = email

                    set_doc = {
                        "user_id":              user_id,
                        "external_customer_id": ext_cust_id,
                        "customer_name":        name,
                        "customer_phone":       phone,
                        "customer_email":       email,
                        "address":              address,
                        "city":                 city,
                        "state":                state,
                        "pincode":              pincode,
                        "orders_count":         orders_count,
                        "total_spent":          total_spent,
                        "source_created_at":    src_created,
                        "updated_at":           src_updated,
                        "source_meta": {
                            "webhook_id":   wh_id or "",
                            "webhook_name": badge_name,
                            "source_app":   source_app,
                            "last_event":   event_type,
                            "received_at":  now_iso,
                        },
                    }
                    set_on_insert = {
                        "id":         str(uuid.uuid4()),
                        "created_at": now_iso,
                    }
                    await db.customers.update_one(
                        upsert_filter,
                        {
                            "$set":         set_doc,
                            "$setOnInsert": set_on_insert,
                        },
                        upsert=True,
                    )
                    imported += 1

                if wh_id and imported:
                    try:
                        await db.user_webhooks.update_one(
                            {"id": wh_id},
                            {"$inc": {"stats.total_imported": imported}},
                        )
                    except Exception:
                        _logger.exception("Failed to bump customer stats")

                _logger.info(
                    "webhook ingest: user=%s wh=%s event=%s "
                    "imported=%d skipped=%d", user_id, wh_id or "legacy",
                    event_type, imported, skipped,
                )
                return {
                    "ok":         True,
                    "imported":   imported,
                    "skipped":    skipped,
                    "event_type": event_type,
                    "errors":     errors_cu[:10],
                }

            # Truly unknown event types (future-proofing) — ack with 200.
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
                # Phase F3.2 (rev-3) — preserve the source app's
                # ORIGINAL order id BEFORE we fall through to using
                # the internal master_order_id below. Status updates
                # later use this as their primary match key (so the
                # Dukaan #123 doesn't collide with Shopify #123).
                "external_order_id": (doc.get("order_id") or "").strip(),
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
                    # Phase F3.2 (rev-3) — Source-strict matching key.
                    # Lowercased canonical source identifier (e.g.
                    # "dukaan", "shopify"). Empty string means the
                    # owner didn't tag this webhook with a source app
                    # → status updates fall back to global match.
                    "source_app":   source_app,
                    # Phase F3.7 — Admin Card. Keep the raw inbound
                    # payload AS-IS so the order-edit screen can show
                    # the operator the original webhook body (Dukaan
                    # checkout_url, Shopify gateway, custom fields,
                    # etc.). Capped at the trimmed-down `flat` view +
                    # the original dict so big payloads don't bloat
                    # pending_orders.
                    "raw_payload":  raw if isinstance(raw, dict) else {},
                },
            })
            doc["order_id"] = doc.get("order_id") or doc["master_order_id"]
            # Phase-21 — Tag REPEAT customer at webhook ingest. Indexed
            # phone lookup on shipments; falls back to False on any
            # exception so a marker check NEVER blocks ingestion.
            try:
                from server import detect_repeat_customer
                doc["is_repeat_customer"] = await detect_repeat_customer(
                    user_id, doc.get("customer_phone") or "",
                )
            except Exception:
                doc["is_repeat_customer"] = False
            doc["viewed"] = False
            await db.pending_orders.insert_one(doc)
            imported += 1

            # Phase F3.9.9 — Auto cross-verify abandoned carts.
            # When this new_order matches a still-abandoned cart for
            # the same user, flip the cart to "recovered_auto" so it
            # disappears from the Abandoned filter the moment the
            # customer's order arrives. Match strategy:
            #   PRIMARY  cart.external_cart_id ∈ {doc.external_order_id,
            #                                     raw.order.cart_token,
            #                                     raw.cart_token,
            #                                     raw.checkout_token}
            #   FALLBACK same normalised phone + cart_value within
            #            ±5 % + cart.abandoned_at within last 24 hours.
            # Both checks are scoped to the SAME user so cross-tenant
            # collisions are impossible.
            try:
                await _auto_recover_abandoned_cart(
                    db=db,
                    user_id=user_id,
                    flat=flat,
                    raw=raw,
                    pending_doc=doc,
                )
            except Exception as _ar_exc:  # noqa: BLE001
                logging.getLogger("routers.webhook").warning(
                    "auto-recover sweep failed: %s", _ar_exc,
                )

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
