"""
WhatsApp Provider router — Phase-28 (Dynamic Event-Trigger System).

PURPOSE
=======
Lets a Super Admin manage WhatsApp messaging end-to-end from the UI,
without ever editing env files or redeploying.

What can the admin configure here?
  1. Global provider connection (FlowConnect / WATI / Custom):
       - provider slug
       - base_url + endpoint template
       - api_token
       - enabled (global kill-switch)

  2. Per-event triggers (8 events out of the box):
       - OTP — Login
       - OTP — Signup
       - Stage: Pending
       - Stage: Processing
       - Stage: Ready to Ship
       - Stage: Shipped
       - Stage: Delivered
       - Stage: Feedback

     For every event the admin sets:
       - enabled toggle
       - automation_id            (FlowConnect's automation reference)
       - template_preview         (reference copy of the template TEXT,
                                   to be pasted into FlowConnect)
       - selected_fields[]        (which app fields to push as variables)
       - custom_fields[]          (extra static / manual variables)
       - variable_mapping{}       (rename App-field → Provider-variable)

KEY DESIGN PRINCIPLE
====================
We DO NOT send the message text to the provider. The template lives
inside the provider's own automation; we only push the DATA needed to
render it. This matches FlowConnect's `{%contact.<key>%}` style.

Public API surface:
  Admin CRUD:
    GET    /api/admin/whatsapp-provider/config
    PUT    /api/admin/whatsapp-provider/config
    GET    /api/admin/whatsapp-provider/events
    GET    /api/admin/whatsapp-provider/events/{event_key}
    PUT    /api/admin/whatsapp-provider/events/{event_key}
    POST   /api/admin/whatsapp-provider/test
    GET    /api/admin/whatsapp-provider/available-fields

  Internal dispatch helper (used by other routers):
    await dispatch_event(db, event_key, context, *, phone=None)

DB Collections:
  - whatsapp_provider_config   (single doc, _id="main")
  - whatsapp_event_triggers    (8 docs, indexed by event_key)
  - whatsapp_provider_log      (audit log for every fired event)
"""
from __future__ import annotations

import logging
import os
import random
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field


_LOG = logging.getLogger("routers.whatsapp_provider")

whatsapp_provider_router = APIRouter(
    prefix="/api/admin/whatsapp-provider",
    tags=["admin-whatsapp-provider"],
)


# ─── Canonical event catalogue ──────────────────────────────────────
# Single source of truth — UI lists these in this order, dispatcher
# only fires events whose key is in here. Adding a new trigger is a
# one-line addition (plus a hook at the call site).
EVENT_CATALOG: List[Dict[str, Any]] = [
    # ── Auth events ──
    {"event_key": "otp_login",        "category": "auth",
     "label":   "OTP — Login",
     "sub":     "Sent when an existing user signs in with their phone",
     "default_fields": ["customer_name", "customer_phone", "otp", "event_type",
                        "contact_email"],
     "default_template":
        "🔐 Your Shippzo login OTP is *{otp}*.\n"
        "It expires in 10 minutes. Don't share it with anyone."},
    {"event_key": "otp_signup",       "category": "auth",
     "label":   "OTP — Signup",
     "sub":     "Sent when a new account is being created",
     "default_fields": ["customer_name", "customer_phone", "otp", "event_type",
                        "contact_email"],
     "default_template":
        "👋 Welcome to Shippzo!\n"
        "Your signup verification OTP is *{otp}*.\n"
        "It expires in 10 minutes."},
    # Phase F8.1 — OTP-verified password reset. `contact_email` carries
    # the user's registered email so the operator's automation can also
    # deliver the code via email.
    {"event_key": "otp_password_reset", "category": "auth",
     "label":   "OTP — Password Reset",
     "sub":     "Sent when a user resets their password (includes contact_email)",
     "default_fields": ["customer_name", "customer_phone", "otp", "event_type",
                        "contact_email"],
     "default_template":
        "🔑 Your Shippzo password reset OTP is *{otp}*.\n"
        "It expires in 10 minutes. If you didn't request this, ignore it."},

    # ── Shipment-stage events (must mirror stage_rules.STAGES) ──
    {"event_key": "stage_pending",       "category": "stage",
     "label":   "Stage: Pending",
     "sub":     "Sent when an order enters the Pending stage",
     "default_fields": ["customer_name", "customer_phone", "order_id",
                        "token_amount", "business_name"],
     "default_template":
        "Hi {customer_name}, we've received your order *{order_id}*.\n"
        "We'll start processing it shortly.\n— {business_name}"},
    {"event_key": "stage_processing",    "category": "stage",
     "label":   "Stage: Processing",
     "sub":     "Sent when an order enters the Processing stage",
     "default_fields": ["customer_name", "customer_phone", "order_id",
                        "business_name"],
     "default_template":
        "Hi {customer_name}, your order *{order_id}* is being prepared.\n"
        "We'll notify you when it ships.\n— {business_name}"},
    {"event_key": "stage_ready_to_ship", "category": "stage",
     "label":   "Stage: Ready to Ship",
     "sub":     "Sent when the parcel is packed and ready to dispatch",
     "default_fields": ["customer_name", "customer_phone", "order_id",
                        "business_name"],
     "default_template":
        "Hi {customer_name}, your order *{order_id}* is packed and "
        "ready to ship.\n— {business_name}"},
    {"event_key": "stage_shipped",       "category": "stage",
     "label":   "Stage: Shipped",
     "sub":     "Sent when the parcel has been handed over to courier",
     "default_fields": ["customer_name", "customer_phone", "order_id",
                        "tracking_id", "courier_name", "eta_days",
                        "business_name"],
     "default_template":
        "🚚 Hi {customer_name}, your order *{order_id}* has shipped via "
        "*{courier_name}*.\nTracking: {tracking_id}\nETA: {eta_days} "
        "days.\n— {business_name}"},
    {"event_key": "stage_out_for_delivery", "category": "stage",
     "label":   "Stage: Out for Delivery",
     "sub":     "Sent when the parcel is out for delivery from the local hub",
     "default_fields": ["customer_name", "customer_phone", "order_id",
                        "tracking_id", "courier_name", "business_name"],
     "default_template":
        "📦 Hi {customer_name}, your order *{order_id}* is out for "
        "delivery today.\nTracking: {tracking_id}\nCourier: "
        "*{courier_name}*.\n— {business_name}"},
    {"event_key": "stage_delivered",     "category": "stage",
     "label":   "Stage: Delivered",
     "sub":     "Sent when the courier marks the parcel as delivered",
     "default_fields": ["customer_name", "customer_phone", "order_id",
                        "business_name"],
     "default_template":
        "✅ Hi {customer_name}, your order *{order_id}* has been "
        "delivered. Thank you for shopping with us!\n— {business_name}"},
    {"event_key": "stage_feedback",      "category": "stage",
     "label":   "Stage: Feedback",
     "sub":     "Sent when the order moves to the Feedback request stage",
     "default_fields": ["customer_name", "customer_phone", "order_id",
                        "business_name", "google_review_link"],
     "default_template":
        "⭐ Hi {customer_name}, how was your experience with order "
        "*{order_id}*? Please share your review here: "
        "{google_review_link}\n— {business_name}"},
    # Phase F8.9 — Abandoned cart recovery. Fires when an abandoned
    # cart crosses the merchant-configured delay threshold (Instant /
    # 5m / 15m / 30m / 1h). Uses the same shipment/order variables
    # the operator already maps for stages, so no new mapping is
    # required — just a Webhook URL in the event editor.
    {"event_key": "abandoned_cart_recovery", "category": "recovery",
     "label":   "Abandoned Cart Recovery",
     "sub":     "Fires after the merchant-set delay for abandoned carts",
     "default_fields": ["customer_name", "customer_phone", "order_id",
                        "product_name", "total_amount", "customer_email",
                        "checkout_url", "business_name"],
     "default_template":
        "👋 Hi {customer_name}, we noticed you left *{product_name}* in "
        "your cart. Complete your order here: {checkout_url}\n— "
        "{business_name}"},
]


# Map between Shipment.status (canonical) and event_key.
STAGE_TO_EVENT_KEY: Dict[str, str] = {
    "Pending":          "stage_pending",
    "Processing":       "stage_processing",
    "Ready to Ship":    "stage_ready_to_ship",
    "Shipped":          "stage_shipped",
    "Out for Delivery": "stage_out_for_delivery",
    "Delivered":        "stage_delivered",
    "Feedback":         "stage_feedback",
}


# ─── Field Catalogue (Phase F11.F — API Builder 1.0) ────────────────
#
# Catalogue of fields the admin can tick — these are the things our
# context-builder knows how to extract. Grouped into sections so the
# frontend can render them under collapsible category headers.
#
# `section` is a display-only hint (Shipment · Timestamps · Financial
# · Business · Auth · Custom). Adding a new field here is safe;
# absent contexts just yield blank values.
#
# The list is intentionally exhaustive — every DB column an operator
# might reasonably want to push into an external WhatsApp / webhook
# integration lives here. Dynamic custom_values keys are appended at
# request-time by the /available-fields endpoint below.
AVAILABLE_FIELDS: List[Dict[str, str]] = [
    # ── Shipment form fields ──────────────────────────────────────
    {"key": "customer_name",     "label": "Customer Name",        "section": "Shipment"},
    {"key": "customer_phone",    "label": "Customer Phone",       "section": "Shipment"},
    {"key": "alt_phone",         "label": "Alt Phone",            "section": "Shipment"},
    {"key": "customer_email",    "label": "Customer Email",       "section": "Shipment"},
    {"key": "gstin",             "label": "GSTIN",                "section": "Shipment"},
    {"key": "order_id",          "label": "Order ID",             "section": "Shipment"},
    {"key": "master_id",         "label": "Master ID",            "section": "Shipment"},
    {"key": "tracking_id",       "label": "Tracking ID",          "section": "Shipment"},
    {"key": "courier_name",      "label": "Courier Partner",      "section": "Shipment"},
    {"key": "product_name",      "label": "Product Name",         "section": "Shipment"},
    {"key": "items",             "label": "Items",                "section": "Shipment"},
    {"key": "quantity",          "label": "Quantity",             "section": "Shipment"},
    {"key": "weight",            "label": "Weight",               "section": "Shipment"},
    {"key": "box_dimensions",    "label": "Box Dimensions",       "section": "Shipment"},
    {"key": "address",           "label": "Address",              "section": "Shipment"},
    {"key": "city",              "label": "City",                 "section": "Shipment"},
    {"key": "pincode",           "label": "Pincode",              "section": "Shipment"},
    {"key": "state",             "label": "State",                "section": "Shipment"},
    # ── Financial / Payment ───────────────────────────────────────
    {"key": "payment_mode",      "label": "Payment Mode",         "section": "Financial"},
    {"key": "amount",            "label": "Amount",               "section": "Financial"},
    {"key": "total_amount",      "label": "Total Amount",         "section": "Financial"},
    {"key": "cod_amount",        "label": "COD Amount",           "section": "Financial"},
    {"key": "token_amount",      "label": "Token Paid",           "section": "Financial"},
    {"key": "cod_collected_amount", "label": "Actual Received Amount", "section": "Financial"},
    {"key": "cod_payment_status","label": "COD Payment Status",   "section": "Financial"},
    {"key": "cod_payment_date",  "label": "Payment Received Date","section": "Financial"},
    {"key": "cod_payer_name",    "label": "Payer Name",           "section": "Financial"},
    # ── Status / Workflow ─────────────────────────────────────────
    {"key": "current_stage",     "label": "Current Stage",        "section": "Status"},
    {"key": "status",            "label": "Status",               "section": "Status"},
    {"key": "confirmation_status","label": "Confirmation Status", "section": "Status"},
    {"key": "eta_days",          "label": "ETA (days)",           "section": "Status"},
    # ── Timestamps ────────────────────────────────────────────────
    {"key": "created_at",        "label": "Created At",           "section": "Timestamps"},
    {"key": "updated_at",        "label": "Updated At",           "section": "Timestamps"},
    {"key": "modified_at",       "label": "Modified At",          "section": "Timestamps"},
    {"key": "booking_date",      "label": "Booking Date",         "section": "Timestamps"},
    {"key": "shipped_at",        "label": "Shipped At",           "section": "Timestamps"},
    {"key": "out_for_delivery_at","label": "Out For Delivery At", "section": "Timestamps"},
    {"key": "delivered_at",      "label": "Delivered At",         "section": "Timestamps"},
    {"key": "last_import_at",    "label": "Last Import At",       "section": "Timestamps"},
    # ── Business / Sender ─────────────────────────────────────────
    {"key": "business_name",     "label": "Business / Shop Name", "section": "Business"},
    {"key": "business_phone",    "label": "Business Phone",       "section": "Business"},
    # ── Auth / OTP (unchanged behaviour) ──────────────────────────
    {"key": "otp",               "label": "OTP Code (auth events only)", "section": "Auth"},
    {"key": "contact_email",     "label": "Registered Email (auth events)", "section": "Auth"},
    # ── Event-specific extras ─────────────────────────────────────
    {"key": "checkout_url",      "label": "Checkout URL (abandoned cart)", "section": "Event"},
    {"key": "google_review_link","label": "Google Review Link (feedback events)", "section": "Event"},
    {"key": "event_type",        "label": "Event Type",           "section": "Event"},
]


# ─── Pydantic Models ────────────────────────────────────────────────
class ProviderConfigUpdate(BaseModel):
    provider:          Optional[str]  = Field(None, max_length=40)
    base_url:          Optional[str]  = Field(None, max_length=500)
    endpoint_template: Optional[str]  = Field(None, max_length=500)
    api_token:         Optional[str]  = Field(None, max_length=500)
    enabled:           Optional[bool] = None
    default_country_code: Optional[str] = Field(None, max_length=4)


class CustomField(BaseModel):
    # `min_length` is NOT enforced here on purpose — the router's
    # save handler intentionally drops entries with a blank `name`
    # rather than reject the whole request with a 422. This keeps the
    # UI's "add row, leave it blank" UX from causing a server-side
    # validation explosion.
    name:  str = Field(..., max_length=64)
    value: str = Field(..., max_length=300)


class EventTriggerUpdate(BaseModel):
    enabled:          Optional[bool]              = None
    automation_id:    Optional[str]               = Field(None, max_length=120)
    # Phase F8.4 — per-event webhook URL. Stage events now route to
    # THIS URL (bypassing the global base_url which is reserved for
    # OTP / auth automations), so operators can point each shipment
    # stage at its own dedicated automation without collision.
    webhook_url:      Optional[str]               = Field(None, max_length=500)
    template_preview: Optional[str]               = Field(None, max_length=3000)
    # Phase F4.9 — first-class boolean so the toggle survives reloads.
    template_enabled: Optional[bool]              = None
    selected_fields:  Optional[List[str]]         = None
    custom_fields:    Optional[List[CustomField]] = None
    variable_mapping: Optional[Dict[str, str]]    = None


# Phase F8.9 — Custom Automation create body. MUST live at module
# scope: this file uses `from __future__ import annotations`, so
# FastAPI resolves body ForwardRefs against module globals — a class
# defined inside init() isn't reachable and every POST fails with a
# 422 "Field required" on `body`.
class CustomEventCreate(BaseModel):
    label: str = Field(..., min_length=2, max_length=64)
    sub:   Optional[str] = Field(None, max_length=200)


class TestSendRequest(BaseModel):
    event_key: str           = Field(..., min_length=2, max_length=80)
    phone:     str           = Field(..., min_length=4, max_length=20)
    sample_context: Optional[Dict[str, Any]] = None


# ─── Utility helpers ────────────────────────────────────────────────
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _provider_default_config() -> Dict[str, Any]:
    """Bootstraps from env so an empty DB still talks to FlowConnect."""
    env_endpoint = os.getenv("FLOWCONNECT_ENDPOINT", "")
    # Extract base_url from the legacy single-endpoint env value, e.g.
    # https://login.flowconnect.ai/api/automations/<id>/execute  →
    # base_url = https://login.flowconnect.ai/api/automations
    base_url = ""
    if env_endpoint:
        m = re.match(r"^(.*/automations)/[^/]+/execute/?$", env_endpoint)
        if m:
            base_url = m.group(1)
        else:
            base_url = env_endpoint
    return {
        "_id":              "main",
        "provider":         os.getenv("WHATSAPP_OTP_PROVIDER", "flowconnect"),
        "base_url":         base_url
            or "https://login.flowconnect.ai/api/automations",
        "endpoint_template": "{base_url}/{automation_id}/execute",
        "api_token":        os.getenv("FLOWCONNECT_API_TOKEN", ""),
        "enabled":          os.getenv("WHATSAPP_OTP_ENABLED", "true").lower()
                              in {"1", "true", "yes", "on"},
        "default_country_code": os.getenv("FLOWCONNECT_DEFAULT_CC", "91"),
        "updated_at":       _now_iso(),
        "updated_by":       "system_default",
    }


def _normalise_phone(raw: str, default_cc: str = "91") -> str:
    """Best-effort E.164 normalisation — mirrors FlowConnect provider's
    own logic so saved settings produce the same outgoing format."""
    s = (raw or "").strip()
    if not s:
        return s
    if s.startswith("+"):
        return "+" + re.sub(r"\D", "", s)
    digits = re.sub(r"\D", "", s)
    if not digits:
        return s
    if len(digits) >= 11 and digits.startswith(default_cc):
        return "+" + digits
    if len(digits) == 10:
        return f"+{default_cc}{digits}"
    return "+" + digits


def _build_event_doc_from_catalog(item: Dict[str, Any]) -> Dict[str, Any]:
    """Translate a catalogue entry into the persisted DB shape."""
    return {
        "event_key":         item["event_key"],
        "label":             item["label"],
        "sub":               item.get("sub") or "",
        "category":          item["category"],
        "enabled":           True,
        "automation_id":     "",
        "webhook_url":       "",
        "template_preview":  item.get("default_template") or "",
        "selected_fields":   list(item.get("default_fields") or []),
        "custom_fields":     [],
        "variable_mapping":  {},
        "created_at":        _now_iso(),
        "updated_at":        _now_iso(),
        "updated_by":        "system_seed",
    }


def _event_doc_to_dto(d: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "event_key":        d.get("event_key"),
        "label":            d.get("label") or "",
        "sub":              d.get("sub") or "",
        "category":         d.get("category") or "stage",
        "enabled":          bool(d.get("enabled", True)),
        "automation_id":    d.get("automation_id") or "",
        # Phase F8.4 — per-event webhook URL used by stage events to
        # bypass the global (OTP-shared) base_url. Empty string when
        # not configured; dispatcher treats empty as "skip this send".
        "webhook_url":      d.get("webhook_url") or "",
        "template_preview": d.get("template_preview") or "",
        # Phase F4.9 — `template_enabled` is a first-class persisted
        # bool. Prior versions derived this from `bool(template_preview)`
        # in the UI, so an operator toggling ON but not entering any
        # template text saw the switch snap back OFF on the next open.
        # Back-compat: legacy docs (created before F4.9) never had
        # this key; if they DO carry a non-empty `template_preview`
        # the operator clearly meant it to be enabled — default to
        # True so the toggle doesn't silently go OFF on first load.
        "template_enabled": (
            bool(d["template_enabled"]) if "template_enabled" in d
            else bool((d.get("template_preview") or "").strip())
        ),
        "selected_fields":  list(d.get("selected_fields") or []),
        "custom_fields":    list(d.get("custom_fields") or []),
        "variable_mapping": dict(d.get("variable_mapping") or {}),
        "updated_at":       d.get("updated_at"),
    }


def _config_doc_to_dto(d: Dict[str, Any]) -> Dict[str, Any]:
    token = d.get("api_token") or ""
    return {
        "provider":             d.get("provider") or "flowconnect",
        "base_url":             d.get("base_url") or "",
        "endpoint_template":    d.get("endpoint_template")
            or "{base_url}/{automation_id}/execute",
        "api_token":            token,
        "api_token_masked":     ("•" * max(0, len(token) - 4) + token[-4:])
                                if token else "",
        "enabled":              bool(d.get("enabled", True)),
        "default_country_code": d.get("default_country_code") or "91",
        "updated_at":           d.get("updated_at"),
    }


# ─── Seed + index helpers ───────────────────────────────────────────
async def _ensure_indexes(db: Any) -> None:
    try:
        await db["whatsapp_event_triggers"].create_index(
            "event_key", unique=True,
        )
        await db["whatsapp_provider_log"].create_index(
            [("ts", -1)],
        )
        await db["whatsapp_provider_log"].create_index("event_key")
    except Exception as e:
        _LOG.warning("whatsapp-provider index creation failed: %s", e)


async def seed_default_events(db: Any) -> None:
    """Idempotent — runs once at startup. Adds any missing event-trigger
    docs but never overwrites admin edits."""
    if db is None:
        return
    await _ensure_indexes(db)
    col = db["whatsapp_event_triggers"]
    inserted = 0
    for item in EVENT_CATALOG:
        try:
            existing = await col.find_one({"event_key": item["event_key"]})
            if existing:
                continue
            await col.insert_one(_build_event_doc_from_catalog(item))
            inserted += 1
        except Exception as e:
            _LOG.warning(
                "seed event %s failed: %s", item["event_key"], e,
            )
    # Seed the provider config doc if missing.
    try:
        if not await db["whatsapp_provider_config"].find_one({"_id": "main"}):
            await db["whatsapp_provider_config"].insert_one(
                _provider_default_config(),
            )
    except Exception as e:
        _LOG.warning("seed provider config failed: %s", e)
    if inserted:
        _LOG.info("Seeded %d WhatsApp event triggers", inserted)


# ─── Context builder + dispatcher (used by other routers) ───────────
#
# Phase F11.F — API Builder 1.0
#
# The historical context builder emitted a fixed 15-key dict. Every
# time an operator wanted to reference a new column ("box_dimensions",
# "gstin", "cod_collected_amount", …) we had to ship a code change.
#
# Rebuilt as a full-document flattener:
#
#   1. Copy every scalar column from the shipment row VERBATIM so any
#      column in the DB becomes a mappable variable automatically.
#      Nested objects / arrays are JSON-stringified so they render
#      cleanly inside a WhatsApp template.
#   2. Merge the curated aliases the old catalogue expected
#      (`current_stage` ← `status`, `total_amount` ← `amount`, etc.)
#      so existing saved mappings keep working.
#   3. Flatten `custom_values` — every dynamic user-added field is
#      promoted to a top-level context key so the operator can
#      reference it just like a built-in.
#   4. Preserve underscore-prefixed sidecar keys (`_user_id`) used
#      by the dispatcher for downstream lookups — `_build_payload`
#      strips them before hitting the BSP.

_SCALAR_ALLOWED_TYPES = (str, int, float, bool)


def _stringify_ctx_value(v: Any) -> Any:
    """Convert a DB value to a WhatsApp-safe context value.

    • None → ""
    • scalars → as-is
    • datetime-like → ISO
    • list / dict → JSON string so it renders deterministically
    """
    if v is None:
        return ""
    if isinstance(_SCALAR_ALLOWED_TYPES, tuple) and isinstance(v, _SCALAR_ALLOWED_TYPES):
        return v
    if hasattr(v, "isoformat"):
        try:
            return v.isoformat()
        except Exception:
            return str(v)
    if isinstance(v, (list, dict)):
        try:
            import json as _json
            return _json.dumps(v, ensure_ascii=False, default=str)
        except Exception:
            return str(v)
    return str(v)


def _shipment_to_context(
    shipment: Dict[str, Any],
    business_name: str = "",
    business_phone: str = "",
) -> Dict[str, Any]:
    """Flatten a Shippzo shipment row into the canonical context the
    event-trigger system uses to fill variables.

    Every top-level column of the shipment doc becomes a context key.
    Additionally, `custom_values` entries are promoted to first-class
    keys so operators can reference `custom_values.my_key` simply as
    `my_key` in their variable_mapping.
    """
    if not shipment:
        return {}

    ctx: Dict[str, Any] = {}

    # ── Pass 1: verbatim scalar copy of every column ────────────────
    for k, v in shipment.items():
        if k.startswith("_"):
            # Mongo `_id` and internal cursors — skip.
            continue
        ctx[k] = _stringify_ctx_value(v)

    # ── Pass 2: legacy aliases so old saved mappings keep working ──
    # Values only fall back to the alias source when the primary key
    # is missing / blank so newer explicit columns always win.
    def _alias(dst: str, *sources: str) -> None:
        cur = ctx.get(dst)
        if cur not in (None, "", 0):
            return
        for s in sources:
            val = shipment.get(s)
            if val not in (None, ""):
                ctx[dst] = _stringify_ctx_value(val)
                return

    _alias("customer_phone", "phone")
    _alias("courier_name",   "courier")
    _alias("token_amount",   "token")
    _alias("total_amount",   "amount")
    _alias("cod_amount",     "cod")
    _alias("product_name",   "product")
    _alias("quantity",       "qty")
    _alias("current_stage",  "status")
    _alias("order_id",       "id")
    _alias("weight",         "weight_kg", "weight_grams")
    _alias("box_dimensions", "dimensions", "package_dimensions")
    _alias("alt_phone",      "alternate_phone", "phone2", "phone_alt")

    # ── Pass 3: flatten custom_values ───────────────────────────────
    # Every user-added key gets promoted to top-level. Prefixed
    # counterparts (`custom_<name>`) are also emitted for operators
    # who prefer a namespaced key — either form works in
    # variable_mapping without ambiguity.
    cvs = shipment.get("custom_values") or {}
    if isinstance(cvs, dict):
        for k, v in cvs.items():
            if not k or k.startswith("_"):
                continue
            val = _stringify_ctx_value(v)
            # Never clobber a first-class column with a same-named
            # custom entry — DB fields win.
            ctx.setdefault(str(k), val)
            ctx.setdefault(f"custom_{k}", val)

    # ── Pass 4: fixed injections ────────────────────────────────────
    ctx["business_name"]  = business_name or ctx.get("business_name") or ""
    ctx["business_phone"] = business_phone or ctx.get("business_phone") or ""
    # Phase F8.8 — customer email must not fall back to admin email.
    ctx["customer_email"] = (shipment.get("customer_email") or "").strip()
    # Phase F8.5 — non-outgoing sidecar for dispatcher lookups.
    # Underscore-prefixed keys are stripped from outbound payload.
    ctx["_user_id"]       = shipment.get("user_id") or ""

    return ctx


async def _fetch_business_google_review_link(db: Any, user_id: str) -> str:
    """Look up the operator's Google Review URL from the user-level
    `settings.business_links` sub-object (populated via Settings →
    WhatsApp Templates in the app; see routers/messaging.py). Returns
    an empty string when unset or on any error so the dispatcher can
    fall through gracefully."""
    if not user_id:
        return ""
    try:
        doc = await db["settings"].find_one(
            {"user_id": user_id},
            {"_id": 0, "business_links": 1},
        )
        links = (doc or {}).get("business_links") or {}
        return str(links.get("google_review_url") or "").strip()
    except Exception:
        _LOG.exception("_fetch_business_google_review_link failed")
        return ""


async def _load_active_config(db: Any) -> Dict[str, Any]:
    doc = await db["whatsapp_provider_config"].find_one({"_id": "main"})
    if not doc:
        return _provider_default_config()
    # Merge any missing keys with defaults so older deployments don't
    # explode after we add a new field to the schema.
    merged = _provider_default_config()
    merged.update({k: v for k, v in doc.items() if v is not None})
    return merged


async def _load_event(db: Any, event_key: str) -> Optional[Dict[str, Any]]:
    return await db["whatsapp_event_triggers"].find_one(
        {"event_key": event_key},
    )


async def _write_log(
    db: Any,
    *,
    event_key:   str,
    phone:       str,
    success:     bool,
    status_code: Optional[int],
    request:     Dict[str, Any],
    response:    Any,
    error:       Optional[str],
    duration_ms: float,
) -> None:
    try:
        await db["whatsapp_provider_log"].insert_one({
            "ts":          _now_iso(),
            "event_key":   event_key,
            "phone":       phone,
            "success":     bool(success),
            "status_code": status_code,
            "request":     request,
            "response":    response,
            "error":       error,
            "duration_ms": round(float(duration_ms or 0), 1),
        })
    except Exception as e:
        _LOG.warning("whatsapp-provider audit write failed: %s", e)


# ─── Placeholder substitution (Phase F6.1) ──────────────────────────
# Custom-field VALUES can reference context variables using FlowConnect's
# own token syntax:
#     {%contact.otp%}          → context["otp"]
#     {%contact.customer_name%}→ context["customer_name"]
#     {contact.order_id}       → context["order_id"]    (unbraced variant)
#     {otp}                    → context["otp"]         (short variant)
#
# If the referenced key isn't in the dispatch context (or is empty), the
# LITERAL placeholder is left untouched so the downstream BSP (e.g.
# FlowConnect) can still resolve it against its own contact record.
# ────────────────────────────────────────────────────────────────────
_PLACEHOLDER_RE = re.compile(
    r"\{%\s*contact\.([a-zA-Z_][a-zA-Z0-9_]*)\s*%\}"     # {%contact.key%}
    r"|\{\s*contact\.([a-zA-Z_][a-zA-Z0-9_]*)\s*\}"      # {contact.key}
    r"|\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}"               # {key}
)


def _substitute_placeholders(value: Any, context: Dict[str, Any]) -> str:
    """Replace `{%contact.<key>%}` (and short variants) with values from
    the dispatch context. Unresolved placeholders are kept as-is so the
    downstream provider can still process them."""
    if value is None:
        return ""
    s = str(value)
    if "{" not in s:
        return s

    def repl(m: "re.Match[str]") -> str:
        key = m.group(1) or m.group(2) or m.group(3)
        if not key:
            return m.group(0)
        if key in context and context[key] not in (None, ""):
            return str(context[key])
        return m.group(0)   # unresolved → forward literal to CRM

    return _PLACEHOLDER_RE.sub(repl, s)


def _build_payload(
    *,
    cfg:        Dict[str, Any],
    trigger:    Dict[str, Any],
    context:    Dict[str, Any],
    phone:      str,
    event_key:  str = "",
) -> Dict[str, str]:
    """Compose the query-param payload to ship to the provider.

    Rules:
      - api_token + contact_phone + contact_name are always present
        (FlowConnect requires the latter two).
      - Each ticked field is added to the payload; the variable_mapping
        renames the outgoing key while keeping the value intact.
      - Custom (static) fields are appended last and can override
        anything before them — useful for things like "template_id".
    """
    cc = cfg.get("default_country_code") or "91"
    normalised = _normalise_phone(phone, cc)
    payload: Dict[str, str] = {
        "api_token":     cfg.get("api_token", ""),
        "contact_phone": normalised,
        "contact_name":  (context.get("customer_name") or "Customer")[:80],
        "app_name":      "Shippzo",
    }

    mapping = trigger.get("variable_mapping") or {}
    for field_key in (trigger.get("selected_fields") or []):
        value = context.get(field_key, "")
        out_key = mapping.get(field_key) or field_key
        payload[str(out_key)] = "" if value is None else str(value)

    for entry in (trigger.get("custom_fields") or []):
        name  = (entry.get("name") or "").strip() if isinstance(entry, dict) else ""
        value = entry.get("value") if isinstance(entry, dict) else ""
        if name:
            # Phase F6.1 — substitute {%contact.<key>%} placeholders so
            # operators can wire "otp" (or any context var) into a
            # custom field's VALUE by typing {%contact.otp%}. Unresolved
            # tokens are left intact so FlowConnect can still resolve
            # them against its own contact record downstream.
            payload[name] = _substitute_placeholders(value, context)

    # Phase F8.1 / F8.8 — Email routing is strictly split by event
    # category so admin@… can never leak into a customer-facing stage
    # payload:
    #   • Auth events (otp_*)     → auto-inject `contact_email` from
    #                                context (the operator's own
    #                                registered / contact email).
    #   • Stage events (stage_*)  → auto-inject `customer_email` from
    #                                context (the shipment row's
    #                                customer_email; blank when none).
    #                                `contact_email` is NEVER emitted
    #                                for stage events, even if some
    #                                legacy trigger doc still ticks
    #                                it in selected_fields — the loop
    #                                above will send the raw context
    #                                value (empty for stages) which
    #                                is the correct blank behaviour.
    _cat = (event_key or "").lower()
    if _cat.startswith("otp_"):
        if context.get("contact_email") and "contact_email" not in payload:
            payload["contact_email"] = str(context["contact_email"])
    elif _cat:
        # Any non-auth event (stage_*, recovery, custom_*, abandoned
        # cart etc.): emit customer_email — the shipment/order's own
        # email — and strictly strip contact_email to prevent admin
        # email leakage.
        if "customer_email" not in payload:
            payload["customer_email"] = str(
                context.get("customer_email") or ""
            )
        payload.pop("contact_email", None)
    else:
        # Truly unknown / legacy code paths: preserve pre-F8.8 fallback.
        if context.get("contact_email") and "contact_email" not in payload:
            payload["contact_email"] = str(context["contact_email"])

    # Phase F8.5 — the Google Review link ALWAYS rides along on the
    # Feedback stage (user requirement: fetch from admin/business
    # settings and add `google_review_link` to the payload) even when
    # the operator's saved `selected_fields` predates this feature.
    # Ticked/custom fields still win if they already set the key —
    # this only fills the gap for pre-existing trigger docs.
    if context.get("google_review_link") and "google_review_link" not in payload:
        payload["google_review_link"] = str(context["google_review_link"])

    # Phase F8.10 — the abandoned-cart Checkout URL ALWAYS rides along
    # on the auto-recovery event so operators don't have to remember to
    # tick a field. Backfills for any legacy trigger doc that pre-dates
    # this feature; ticked/custom fields still win.
    if context.get("checkout_url") and "checkout_url" not in payload:
        payload["checkout_url"] = str(context["checkout_url"])

    return payload


async def dispatch_event(
    db: Any,
    event_key: str,
    context: Dict[str, Any],
    *,
    phone: Optional[str] = None,
) -> Dict[str, Any]:
    """Fire a configured WhatsApp event.

    NEVER raises — auth / status-change flows must not be blocked by
    BSP errors. Returns a dict describing the outcome.
    """
    result: Dict[str, Any] = {
        "success":    False,
        "event_key":  event_key,
        "skipped":    False,
        "reason":     None,
        "status_code": None,
        "duration_ms": 0,
    }
    try:
        cfg = await _load_active_config(db)
        if not cfg.get("enabled"):
            result["skipped"] = True
            result["reason"]  = "provider globally disabled"
            return result

        trigger = await _load_event(db, event_key)
        if not trigger:
            result["skipped"] = True
            result["reason"]  = f"unknown event: {event_key}"
            return result
        if not trigger.get("enabled"):
            result["skipped"] = True
            result["reason"]  = "event disabled by admin"
            return result

        automation_id = (trigger.get("automation_id") or "").strip()
        webhook_url   = (trigger.get("webhook_url") or "").strip()
        api_token     = (cfg.get("api_token") or "").strip()
        base_url      = (cfg.get("base_url") or "").strip()
        template      = (cfg.get("endpoint_template") or "").strip()

        # ── Phase F8.4 + F8.9 — Route resolution ──────────────────────
        # Auth events (otp_*)                   → global base_url (OTP)
        # Everything else (stage_*, recovery_*,
        #   abandoned_cart_recovery, custom_*)  → per-event webhook_url
        # If webhook_url is empty for a non-auth event, skip cleanly.
        # We NEVER fall back to base_url for non-auth events because
        # base_url is reserved for the OTP automation and cross-
        # contaminating those flows is exactly the bug we prevent.
        _is_auth_event  = (event_key or "").lower().startswith("otp_")
        _needs_webhook  = not _is_auth_event

        if _needs_webhook:
            if not webhook_url:
                result["skipped"] = True
                result["reason"]  = (
                    "event has no webhook URL configured. Add one in "
                    "Admin → WhatsApp Provider → this event → "
                    "Advanced Settings → Webhook URL."
                )
                return result
            if not api_token:
                result["skipped"] = True
                result["reason"]  = "provider api_token missing"
                return result
            endpoint = webhook_url

        else:
            # Auth events (otp_*) and any legacy events. Require the
            # global provider config to be fully set.
            if not (api_token and base_url):
                result["skipped"] = True
                result["reason"]  = "provider not fully configured"
                return result
            if not automation_id:
                # Simple mode: base_url is treated as the full execute
                # endpoint. This is the auth/OTP happy path.
                endpoint = base_url
            else:
                # Per-event automation with base_url as the automations
                # root. Compose the target endpoint.
                root = re.sub(r"/[^/]+/execute/?$", "", base_url.rstrip("/"))
                if "{automation_id}" in template and "{base_url}" in template:
                    endpoint = template.format(
                        base_url=root.rstrip("/"),
                        automation_id=automation_id.strip("/"),
                    )
                else:
                    endpoint = f"{root.rstrip('/')}/{automation_id.strip('/')}/execute"

        # Phase F8.5 — Feedback stage: pull the operator's Google
        # Review URL out of user-level business_links and inject as
        # `google_review_link` (canonical) + `google_review_url`
        # (legacy alias, keeps existing templates working) so the
        # customer's feedback WhatsApp can carry the review link.
        if event_key == "stage_feedback":
            try:
                gurl = await _fetch_business_google_review_link(
                    db, context.get("_user_id") or "",
                )
                if gurl:
                    context = {
                        **context,
                        "google_review_link": gurl,
                        "google_review_url":  gurl,
                    }
            except Exception:
                _LOG.exception(
                    "google_review_link injection failed (non-fatal)",
                )

        target_phone = phone or context.get("customer_phone") or ""
        if not target_phone:
            result["skipped"] = True
            result["reason"]  = "no destination phone"
            return result

        endpoint = endpoint  # already computed above (Phase F5.3)
        payload  = _build_payload(
            cfg=cfg, trigger=trigger, context=context, phone=target_phone,
            event_key=event_key,
        )
        safe_payload = {**payload, "api_token": "***"}

        t0 = time.time()
        async with httpx.AsyncClient(timeout=8.0) as cli:
            resp = await cli.post(
                endpoint,
                params=payload,
                headers={"Accept": "application/json"},
            )
        duration_ms = (time.time() - t0) * 1000.0
        try:
            body = resp.json()
        except Exception:
            body = (resp.text or "")[:500]
        ok = (resp.status_code < 400) and (
            (isinstance(body, dict) and not body.get("error"))
            or (isinstance(body, str) and resp.status_code in (200, 201, 202))
        )
        result.update({
            "success":         bool(ok),
            "status_code":     resp.status_code,
            "duration_ms":     round(duration_ms, 1),
            "reason":          None if ok else f"HTTP {resp.status_code}",
            # Phase F5.8 — expose the actual wire-level bits so the
            # Test Send modal can render a "Live Response Viewer"
            # (previously we swallowed these and only surfaced
            # status_code, which made silent-drop failures — where the
            # BSP returns 200 but never delivers the WhatsApp — hard
            # to debug).
            "endpoint":        endpoint,
            "request_payload": safe_payload,
            "response_body":   body,
        })
        await _write_log(
            db,
            event_key=event_key, phone=target_phone,
            success=bool(ok), status_code=resp.status_code,
            request=safe_payload, response=body,
            error=None if ok else f"HTTP {resp.status_code}",
            duration_ms=duration_ms,
        )
    except Exception as e:
        _LOG.exception("dispatch_event(%s) failed: %s", event_key, e)
        result.update({"success": False, "reason": f"{e.__class__.__name__}: {e}"})
        try:
            await _write_log(
                db,
                event_key=event_key, phone=phone or "",
                success=False, status_code=None,
                request={"event_key": event_key},
                response=None,
                error=f"{e.__class__.__name__}: {e}",
                duration_ms=0,
            )
        except Exception:
            pass
    return result


# ─── Route registration (late-bound) ────────────────────────────────
def init() -> None:
    """Bind handlers once server.py has its deps wired up."""
    from server import (  # noqa: WPS433
        db,
        get_current_user as _get_current_user,
        _require_admin as _require_admin_helper,
    )

    # ── GET /config ───────────────────────────────────────────────
    @whatsapp_provider_router.get("/config")
    async def get_config(
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ) -> Dict[str, Any]:
        _require_admin_helper(current_user)
        cfg = await _load_active_config(db)
        return {"config": _config_doc_to_dto(cfg)}

    # ── PUT /config ───────────────────────────────────────────────
    @whatsapp_provider_router.put("/config")
    async def update_config(
        payload: ProviderConfigUpdate,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ) -> Dict[str, Any]:
        _require_admin_helper(current_user)
        patch: Dict[str, Any] = {}
        for key in (
            "provider", "base_url", "endpoint_template",
            "api_token", "default_country_code",
        ):
            v = getattr(payload, key, None)
            if v is not None:
                patch[key] = v.strip() if isinstance(v, str) else v
        if payload.enabled is not None:
            patch["enabled"] = bool(payload.enabled)
        if not patch:
            raise HTTPException(status_code=400, detail="Nothing to update")
        patch["updated_at"] = _now_iso()
        patch["updated_by"] = current_user.get("id") or "admin"
        await db["whatsapp_provider_config"].update_one(
            {"_id": "main"},
            {"$set": patch},
            upsert=True,
        )
        cfg = await _load_active_config(db)
        return {"ok": True, "config": _config_doc_to_dto(cfg)}

    # ── GET /events ───────────────────────────────────────────────
    @whatsapp_provider_router.get("/events")
    async def list_events(
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ) -> Dict[str, Any]:
        _require_admin_helper(current_user)
        await _ensure_indexes(db)
        # Always return events in catalogue order; create any missing
        # ones on the fly so the UI is never empty.
        out: List[Dict[str, Any]] = []
        for item in EVENT_CATALOG:
            doc = await db["whatsapp_event_triggers"].find_one(
                {"event_key": item["event_key"]},
            )
            if not doc:
                doc = _build_event_doc_from_catalog(item)
                await db["whatsapp_event_triggers"].insert_one(doc)
            out.append(_event_doc_to_dto(doc))
        return {"items": out, "count": len(out)}

    # ── GET /events/{event_key} ───────────────────────────────────
    @whatsapp_provider_router.get("/events/{event_key}")
    async def get_event(
        event_key: str,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ) -> Dict[str, Any]:
        _require_admin_helper(current_user)
        doc = await db["whatsapp_event_triggers"].find_one(
            {"event_key": event_key},
        )
        if not doc:
            raise HTTPException(status_code=404, detail="Event not found")
        return {"item": _event_doc_to_dto(doc)}

    # ── PUT /events/{event_key} ───────────────────────────────────
    @whatsapp_provider_router.put("/events/{event_key}")
    async def update_event(
        event_key: str,
        payload: EventTriggerUpdate,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ) -> Dict[str, Any]:
        _require_admin_helper(current_user)
        # Phase F8.9 — custom_* events are runtime-created (not in the
        # built-in EVENT_CATALOG) so exempt them from the catalog check.
        # Their existence is enforced by the DB (find-one-and-update
        # below returns 404 if the doc doesn't exist).
        if not event_key.startswith("custom_") and not any(
            c["event_key"] == event_key for c in EVENT_CATALOG
        ):
            raise HTTPException(status_code=404, detail="Unknown event_key")
        patch: Dict[str, Any] = {}
        if payload.enabled is not None:
            patch["enabled"] = bool(payload.enabled)
        if payload.automation_id is not None:
            patch["automation_id"] = payload.automation_id.strip()
        # Phase F8.4 — persist per-event webhook URL. Stage events use
        # this to route to their own dedicated automation, keeping OTP
        # (auth) traffic completely isolated.
        if payload.webhook_url is not None:
            patch["webhook_url"] = payload.webhook_url.strip()
        if payload.template_preview is not None:
            patch["template_preview"] = payload.template_preview
        # Phase F4.9 — persist the Enable-Template toggle.
        if payload.template_enabled is not None:
            patch["template_enabled"] = bool(payload.template_enabled)
        if payload.selected_fields is not None:
            # De-duplicate while preserving order.
            seen: Dict[str, None] = {}
            for f in payload.selected_fields:
                if f and f not in seen:
                    seen[f] = None
            patch["selected_fields"] = list(seen.keys())
        if payload.custom_fields is not None:
            patch["custom_fields"] = [
                {"name": c.name.strip(), "value": c.value}
                for c in payload.custom_fields
                if c.name.strip()
            ]
        if payload.variable_mapping is not None:
            patch["variable_mapping"] = {
                str(k).strip(): str(v).strip()
                for k, v in payload.variable_mapping.items()
                if str(k).strip() and str(v).strip()
            }
        if not patch:
            raise HTTPException(status_code=400, detail="Nothing to update")
        patch["updated_at"] = _now_iso()
        patch["updated_by"] = current_user.get("id") or "admin"
        # Auto-create the doc if missing (e.g. after a schema reset).
        existing = await db["whatsapp_event_triggers"].find_one(
            {"event_key": event_key},
        )
        if not existing:
            item = next(c for c in EVENT_CATALOG if c["event_key"] == event_key)
            await db["whatsapp_event_triggers"].insert_one(
                {**_build_event_doc_from_catalog(item), **patch},
            )
        else:
            await db["whatsapp_event_triggers"].update_one(
                {"event_key": event_key}, {"$set": patch},
            )
        fresh = await db["whatsapp_event_triggers"].find_one(
            {"event_key": event_key},
        )
        return {"ok": True, "item": _event_doc_to_dto(fresh)}

    # ── GET /available-fields ─────────────────────────────────────
    #
    # Phase F11.F — Future-proof dynamic discovery.
    #
    # Merges the curated catalogue (`AVAILABLE_FIELDS`) with EVERY
    # top-level key discovered on recent shipment / pending_order
    # documents PLUS every dynamic `custom_values` sub-key.
    #
    # Result: when the schema evolves and a new column starts landing
    # on shipments (e.g. `insurance_amount`, `handling_charges`,
    # `pickup_slot_at`), it shows up in the "+ Add Field" dropdown
    # automatically on the very next refresh — no code change, no
    # deploy required.
    @whatsapp_provider_router.get("/available-fields")
    async def get_available_fields(
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ) -> Dict[str, Any]:
        _require_admin_helper(current_user)

        # 1. Seed with the curated catalogue — well-known fields keep
        #    their friendly labels + section grouping.
        by_key: Dict[str, Dict[str, str]] = {}
        for f in AVAILABLE_FIELDS:
            k = f.get("key")
            if k:
                by_key[k] = dict(f)

        # 2. Scan the last N shipments + pending_orders for any keys
        #    the catalogue doesn't cover yet. We only fetch documents
        #    that BELONG to the current admin so a tenant's exotic
        #    custom columns can't leak between accounts.
        sample_query = {"user_id": current_user["id"]}
        # Cap the scan so this endpoint remains fast on large tenants.
        SCAN_LIMIT = 200

        def _humanize(k: str) -> str:
            """`snake_case_key` → `Snake Case Key` for the dropdown."""
            return " ".join(w.capitalize() for w in str(k).replace("-", "_").split("_") if w) or k

        for coll_name, section in (("shipments", "Shipment"), ("pending_orders", "Pending Order")):
            try:
                cursor = db[coll_name].find(sample_query, {"_id": 0}).limit(SCAN_LIMIT)
                async for doc in cursor:
                    for k, v in doc.items():
                        if not k or k.startswith("_") or k == "user_id":
                            continue
                        if k not in by_key:
                            by_key[k] = {
                                "key":     k,
                                "label":   _humanize(k),
                                "section": f"Auto ({section})",
                            }
                    # Custom values — surface every dynamic sub-key.
                    cvs = doc.get("custom_values") or {}
                    if isinstance(cvs, dict):
                        for ck in cvs.keys():
                            if not ck or str(ck).startswith("_"):
                                continue
                            if ck not in by_key:
                                by_key[ck] = {
                                    "key":     str(ck),
                                    "label":   _humanize(str(ck)),
                                    "section": "Custom Fields",
                                }
            except Exception:
                _LOG.exception("available-fields: %s scan failed", coll_name)

        # 3. Emit sorted: curated sections first (in the order they
        #    appear in AVAILABLE_FIELDS), then auto-discovered.
        curated_order = [f["key"] for f in AVAILABLE_FIELDS if f.get("key")]
        seen: Dict[str, None] = {}
        out: List[Dict[str, str]] = []
        for k in curated_order:
            if k in by_key and k not in seen:
                out.append(by_key[k])
                seen[k] = None
        for k, meta in by_key.items():
            if k not in seen:
                out.append(meta)
                seen[k] = None

        return {"fields": out}

    # ── POST /custom-events ───────────────────────────────────────
    # Phase F8.9 — Create a new Custom Automation. Uses the standard
    # shipment/order variable set (no bespoke mapping required) so
    # the operator only needs to paste a Webhook URL in the editor
    # after creation. The `event_key` is derived from the label and
    # de-duplicated with a short random suffix.
    @whatsapp_provider_router.post("/custom-events")
    async def create_custom_event(
        body: CustomEventCreate,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ) -> Dict[str, Any]:
        _require_admin_helper(current_user)
        label = body.label.strip()
        # Derive a URL-safe key from the label; guarantee uniqueness
        # via a 6-char random suffix.
        slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") or "auto"
        suffix = "".join(random.choices("abcdefghijklmnopqrstuvwxyz0123456789", k=6))
        event_key = f"custom_{slug[:24]}_{suffix}"

        # Standard shipment/order variable set — same as stages so no
        # new mapping is required. The operator only needs to set a
        # Webhook URL to activate the trigger.
        doc = {
            "event_key":        event_key,
            "label":            label,
            "sub":              (body.sub or "Custom automation").strip(),
            "category":         "custom",
            "enabled":          True,
            "automation_id":    "",
            "webhook_url":      "",
            "template_preview": "",
            "template_enabled": False,
            "selected_fields":  [
                "customer_name", "customer_phone", "order_id",
                "product_name", "quantity", "total_amount",
                "tracking_id", "courier_name", "business_name",
                "customer_email",
            ],
            "custom_fields":    [],
            "variable_mapping": {},
            "created_at":       _now_iso(),
            "updated_at":       _now_iso(),
            "updated_by":       current_user.get("email") or "admin",
        }
        await db.whatsapp_event_triggers.insert_one(doc)
        return {"ok": True, "item": _event_doc_to_dto(doc)}

    # ── DELETE /events/{event_key} ────────────────────────────────
    # Phase F8.9 — Custom automations can be removed by the operator.
    # Built-in events (stage_*, otp_*, abandoned_cart_recovery) are
    # ONLY seed-owned — a delete on them is disallowed to protect the
    # catalogue and prevent accidental data loss.
    @whatsapp_provider_router.delete("/events/{event_key}")
    async def delete_custom_event(
        event_key: str,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ) -> Dict[str, Any]:
        _require_admin_helper(current_user)
        if not event_key.startswith("custom_"):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Only custom automations can be deleted. Built-in "
                    "events (stages / OTP / abandoned cart recovery) "
                    "are catalogue-managed."
                ),
            )
        res = await db.whatsapp_event_triggers.delete_one({"event_key": event_key})
        if res.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Not found")
        return {"ok": True, "deleted": event_key}

    # ── POST /test ────────────────────────────────────────────────
    @whatsapp_provider_router.post("/test")
    async def test_send(
        payload: TestSendRequest,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ) -> Dict[str, Any]:
        """Send a test message via the configured automation. Uses the
        admin-supplied sample_context to populate variables (with safe
        defaults so the request never goes out empty).

        Phase F5.8 — OTP is now auto-generated (random 6-digit) instead
        of being hardcoded to "123456". Many BSPs silently drop tests
        that repeat the same OTP value because they flag it as fraud
        (which is why the operator kept seeing "success" but never got
        a real WhatsApp). The generated OTP is returned in the response
        so the Live Response Viewer can show what code was actually
        dispatched — no guessing what to expect on the phone."""
        _require_admin_helper(current_user)
        sample = payload.sample_context or {}
        # Auto-generate a fresh 6-digit OTP for OTP-style events.
        # 6 digits is the industry standard (matches SBI, HDFC, Google,
        # Meta, etc.) and gives 1 in 1M brute-force space.
        generated_otp = f"{random.randint(100000, 999999):06d}"
        # Phase F8.8 — email defaults are STRICTLY category-scoped so
        # admin@… can never leak into a stage-event test:
        #   • Auth events  → `contact_email` = admin's registered email.
        #   • Stage events → `customer_email` = "" (operator fills via
        #                    Manual entry or Auto Fetch which pulls
        #                    from the shipment row); `contact_email`
        #                    is deliberately NOT set.
        _is_auth  = (payload.event_key or "").startswith("otp_")
        _is_stage = (payload.event_key or "").startswith("stage_")
        # Sensible auto-fill so OTP-type events still have a code.
        defaults: Dict[str, str] = {
            "customer_name":  current_user.get("name") or "Test User",
            "customer_phone": payload.phone,
            "otp":            generated_otp,
            "event_type":     payload.event_key,
            "order_id":       "TEST-001",
            "tracking_id":    "TRACK-TEST-001",
            "courier_name":   "Delhivery",
            "business_name":  "Shippzo Test Shop",
            "current_stage":  "Test",
            "eta_days":       "3",
            "token_amount":   "100",
            "total_amount":   "1000",
        }
        if _is_auth:
            defaults["contact_email"] = (
                (current_user.get("contact_email") or "").strip()
                or (current_user.get("email") or "").strip()
            )
        if _is_stage:
            # Blank customer_email default; the real value must come
            # from the operator's Manual input or the Auto Fetch pull.
            defaults["customer_email"] = ""
        context = {**defaults, **sample}
        # Phase F8.5 — expose the current admin's user_id so the
        # stage_feedback dispatcher can look up their business_links
        # (Google Review URL) and inject `google_review_link` into
        # the outgoing payload during test-send as well as prod.
        if "_user_id" not in context or not context.get("_user_id"):
            context["_user_id"] = current_user.get("id") or ""
        outcome = await dispatch_event(
            db, payload.event_key, context, phone=payload.phone,
        )
        # Surface the generated OTP at the top level so the frontend
        # doesn't have to fish it out of the request_payload dict.
        return {
            "ok":            bool(outcome.get("success")),
            "generated_otp": generated_otp,
            "result":        outcome,
        }

    # ── GET /logs (last 20 attempts) ──────────────────────────────
    @whatsapp_provider_router.get("/logs")
    async def list_logs(
        current_user: Dict[str, Any] = Depends(_get_current_user),
        event_key: Optional[str] = None,
        limit: int = 20,
    ) -> Dict[str, Any]:
        _require_admin_helper(current_user)
        q: Dict[str, Any] = {}
        if event_key:
            q["event_key"] = event_key
        cursor = db["whatsapp_provider_log"].find(q, {"_id": 0}).sort("ts", -1).limit(
            max(1, min(int(limit or 20), 100))
        )
        rows = [r async for r in cursor]
        return {"items": rows, "count": len(rows)}

    # ── GET /latest-shipment-context ─────────────────────────────────
    # Phase F8.6 — powers the Test Send popup's "Auto Fetch" mode. Given
    # a stage event_key (e.g. `stage_delivered`), find the most-recent
    # shipment belonging to this admin that is currently in the
    # corresponding status, flatten it via `_shipment_to_context`, and
    # return the resulting variable map so the frontend can pre-fill
    # every mapping field with real data. OTP / auth events are not
    # supported (no shipment analogue) — the frontend gates this call
    # to stage-only cards.
    @whatsapp_provider_router.get("/latest-shipment-context")
    async def latest_shipment_context(
        event_key: str,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ) -> Dict[str, Any]:
        _require_admin_helper(current_user)
        if not (event_key or "").startswith("stage_"):
            return {
                "ok":      False,
                "reason":  "auto-fetch is only available for stage events",
                "context": {},
            }
        # Reverse STAGE_TO_EVENT_KEY → find the shipment status(es)
        # that map to this event_key. Multiple statuses can theoretically
        # collapse onto one event (defensive against future aliasing).
        wanted_statuses = [
            st for st, ev in STAGE_TO_EVENT_KEY.items() if ev == event_key
        ]
        if not wanted_statuses:
            return {
                "ok":      False,
                "reason":  f"no shipment status maps to {event_key}",
                "context": {},
            }
        doc = await db["shipments"].find_one(
            {
                "user_id": current_user["id"],
                "status":  {"$in": wanted_statuses},
            },
            {"_id": 0},
            sort=[("created_at", -1)],
        )
        if not doc:
            return {
                "ok":      False,
                "reason":  (
                    f"no shipment found in status {wanted_statuses} for "
                    "this account. Auto-fetch is unavailable — use "
                    "Manual mode to key in test values."
                ),
                "context": {},
            }
        ctx = _shipment_to_context(
            doc,
            business_name=(current_user.get("shop_name")
                           or current_user.get("name") or ""),
            business_phone=(current_user.get("phone") or ""),
        )
        # Strip the underscore-prefixed sidecars (`_user_id`) — they
        # are dispatcher-internal and should not leak into the UI.
        ctx_public = {k: v for k, v in ctx.items() if not k.startswith("_")}
        return {
            "ok":            True,
            "event_key":     event_key,
            "shipment_id":   doc.get("id"),
            "status":        doc.get("status"),
            "context":       ctx_public,
        }


# ─── Phase F8.3 — Stage-aware WhatsApp message renderer ─────────────
# Powers the Shipments-tab WhatsApp button. Instead of hard-coding a
# single "Shipped" template on the client, the button now asks the
# backend "what should I say for THIS shipment right now?", and the
# server picks the right template based on `shipment.status` — using
# the operator's admin-customised body if present, else falling back
# to the built-in catalogue default. Placeholders like {customer_name}
# and {order_id} are resolved server-side against the shipment row so
# the client just deep-links WhatsApp with the ready-to-send text.
async def resolve_stage_message(
    db: Any,
    shipment: Dict[str, Any],
    *,
    business_name: str = "",
    business_phone: str = "",
) -> Optional[Dict[str, Any]]:
    """Return the WhatsApp text appropriate for the shipment's CURRENT
    stage, or None if no stage-event maps to `shipment.status` (e.g.
    the shipment is in a terminal `Cancelled` / `Returned` state).

    Payload shape:
        {
          "event_key":  "stage_delivered",
          "label":      "Stage: Delivered",
          "status":     "Delivered",
          "source":     "admin" | "default",   # where the text came from
          "message":    "✅ Hi Rita, your order …",
        }
    """
    if not shipment:
        return None
    status = (shipment.get("status") or "").strip()
    event_key = STAGE_TO_EVENT_KEY.get(status)
    if not event_key:
        return None

    ctx = _shipment_to_context(
        shipment,
        business_name=business_name,
        business_phone=business_phone,
    )

    # Phase F8.5 — Feedback stage carries the operator's Google Review
    # link so the customer-facing WhatsApp text can render
    # {google_review_link}. Same source of truth as the dispatcher's
    # auto-inject: users.business_links.google_review_url.
    if event_key == "stage_feedback":
        try:
            gurl = await _fetch_business_google_review_link(
                db, ctx.get("_user_id") or "",
            )
            if gurl:
                ctx = {
                    **ctx,
                    "google_review_link": gurl,
                    "google_review_url":  gurl,
                }
        except Exception:
            _LOG.exception(
                "resolve_stage_message: review-link fetch failed",
            )

    # Prefer the operator's admin-configured body.
    trigger = await _load_event(db, event_key) or {}
    text = (trigger.get("template_preview") or "").strip()
    source = "admin"

    # Fall back to the built-in catalogue default so a fresh workspace
    # (before the operator has customised anything) still produces a
    # useful message per stage.
    if not text:
        for item in EVENT_CATALOG:
            if item["event_key"] == event_key:
                text = (item.get("default_template") or "").strip()
                source = "default"
                break

    if not text:
        return None

    rendered = _substitute_placeholders(text, ctx)

    label = event_key
    for item in EVENT_CATALOG:
        if item["event_key"] == event_key:
            label = item.get("label") or event_key
            break

    return {
        "event_key": event_key,
        "label":     label,
        "status":    status,
        "source":    source,
        "message":   rendered,
    }


# Public re-exports for server.py wiring.
__all__ = [
    "whatsapp_provider_router",
    "init",
    "seed_default_events",
    "dispatch_event",
    "STAGE_TO_EVENT_KEY",
    "_shipment_to_context",
    "EVENT_CATALOG",
    "resolve_stage_message",
]
