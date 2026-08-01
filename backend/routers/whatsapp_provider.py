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
                        "business_name"],
     "default_template":
        "⭐ Hi {customer_name}, how was your experience with order "
        "*{order_id}*? Reply 1–5 to rate us.\n— {business_name}"},
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


# Catalogue of fields the admin can tick — these are the things our
# context-builder knows how to extract. Adding a new field here is
# safe; absent contexts just yield blank values.
AVAILABLE_FIELDS: List[Dict[str, str]] = [
    {"key": "customer_name",     "label": "Customer Name"},
    {"key": "customer_phone",    "label": "Customer Phone"},
    {"key": "order_id",          "label": "Order / Shipment ID"},
    {"key": "tracking_id",       "label": "Tracking ID"},
    {"key": "courier_name",      "label": "Courier Name"},
    {"key": "token_amount",      "label": "Token Amount"},
    {"key": "total_amount",      "label": "Total Amount"},
    {"key": "cod_amount",        "label": "COD Amount"},
    {"key": "address",           "label": "Customer Address"},
    {"key": "city",              "label": "Customer City"},
    {"key": "pincode",           "label": "Pincode"},
    {"key": "state",             "label": "State"},
    {"key": "product_name",      "label": "Product Name"},
    {"key": "quantity",          "label": "Quantity"},
    {"key": "eta_days",          "label": "ETA (days)"},
    {"key": "current_stage",     "label": "Current Stage"},
    {"key": "business_name",     "label": "Business / Shop Name"},
    {"key": "business_phone",    "label": "Business Phone"},
    {"key": "otp",               "label": "OTP Code (auth events only)"},
    {"key": "contact_email",     "label": "Registered Email (auth events)"},
    {"key": "event_type",        "label": "Event Type"},
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
    template_preview: Optional[str]               = Field(None, max_length=3000)
    # Phase F4.9 — first-class boolean so the toggle survives reloads.
    template_enabled: Optional[bool]              = None
    selected_fields:  Optional[List[str]]         = None
    custom_fields:    Optional[List[CustomField]] = None
    variable_mapping: Optional[Dict[str, str]]    = None


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
def _shipment_to_context(
    shipment: Dict[str, Any],
    business_name: str = "",
    business_phone: str = "",
) -> Dict[str, Any]:
    """Flatten a Shippzo shipment row into the canonical context the
    event-trigger system uses to fill variables."""
    if not shipment:
        return {}
    return {
        "customer_name":  shipment.get("customer_name") or "",
        "customer_phone": shipment.get("customer_phone") or shipment.get("phone") or "",
        "order_id":       shipment.get("order_id") or shipment.get("id") or "",
        "tracking_id":    shipment.get("tracking_id") or "",
        "courier_name":   shipment.get("courier") or shipment.get("courier_name") or "",
        "token_amount":   str(shipment.get("token_amount") or shipment.get("token") or ""),
        "total_amount":   str(shipment.get("total_amount") or shipment.get("amount") or ""),
        "cod_amount":     str(shipment.get("cod_amount") or shipment.get("cod") or ""),
        "address":        shipment.get("address") or "",
        "city":           shipment.get("city") or "",
        "pincode":        str(shipment.get("pincode") or ""),
        "state":          shipment.get("state") or "",
        "product_name":   shipment.get("product_name") or shipment.get("product") or "",
        "quantity":       str(shipment.get("quantity") or shipment.get("qty") or ""),
        "eta_days":       str(shipment.get("eta_days") or ""),
        "current_stage":  shipment.get("status") or "",
        "business_name":  business_name or "",
        "business_phone": business_phone or "",
    }


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

    # Phase F8.1 — the registered email ALWAYS rides along on auth
    # events (user requirement: `"contact_email": <registered email>`
    # in the current webhook payload) even when the operator's saved
    # field list pre-dates this feature. Ticked/custom fields above win
    # if they already set the key.
    if context.get("contact_email") and "contact_email" not in payload:
        payload["contact_email"] = str(context["contact_email"])

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
        api_token     = (cfg.get("api_token") or "").strip()
        base_url      = (cfg.get("base_url") or "").strip()
        template      = (cfg.get("endpoint_template") or "").strip()
        # Phase F5.3 — Simple mode. If the operator hasn't set a
        # per-event automation_id, we treat the Base URL as the full
        # endpoint (POST data straight to it). This is the recommended
        # happy path for providers like FlowConnect where each
        # automation is a distinct URL that already contains all
        # routing info. The template + automation_id fields are kept
        # for advanced integrations (e.g. Twilio-style with sub-paths).
        if not (api_token and base_url):
            result["skipped"] = True
            result["reason"]  = "provider not fully configured"
            return result

        # Phase F8.2 — Guard against stage events accidentally reusing a
        # single-automation OTP URL. If the operator saved base_url as a
        # full "…/execute" endpoint (i.e. a specific automation, which
        # is typically reserved for OTP templates) AND this stage event
        # has NO per-event automation_id override, sending would
        # misroute customer stage notifications through the OTP
        # automation. Fail fast with a clear error so the operator
        # fixes the config in Admin → WhatsApp Provider. Auth events
        # are exempt — they are the legitimate consumer of an OTP
        # automation URL.
        _is_stage_event = (event_key or "").lower().startswith("stage_")
        _looks_like_single_automation_url = bool(
            re.search(r"/[^/]+/execute/?$", base_url)
        )
        if _is_stage_event and (not automation_id) and _looks_like_single_automation_url:
            reason = (
                "stage event blocked: Base URL points to a specific "
                "automation endpoint (…/execute) that is typically the "
                "OTP template. Set a per-event automation_id for this "
                "stage in Admin → WhatsApp Provider → Advanced Settings, "
                "or change the provider Base URL to the automations "
                "root (…/api/automations)."
            )
            result.update({
                "success": False,
                "skipped": False,
                "reason":  reason,
            })
            try:
                await _write_log(
                    db,
                    event_key=event_key,
                    phone=phone or context.get("customer_phone") or "",
                    success=False, status_code=None,
                    request={"guard": "stage_event_blocked_on_otp_url"},
                    response=None,
                    error=reason,
                    duration_ms=0,
                )
            except Exception:
                pass
            return result

        if not automation_id:
            # Simple mode: base_url is treated as the full execute
            # endpoint. Used by events that share the single default
            # automation (e.g. the shipment-stage triggers). Unchanged
            # behaviour — do NOT regress these.
            endpoint = base_url
        else:
            # Per-event automation. base_url may be stored either as the
            # automations *root* (…/api/automations) or as a *full*
            # execute URL (…/api/automations/<id>/execute) depending on
            # how the operator saved it. Normalise to the root, then
            # compose THIS event's own automation endpoint so each
            # trigger can target a distinct automation (e.g. a dedicated
            # OTP-template automation for signup/login).
            root = re.sub(r"/[^/]+/execute/?$", "", base_url.rstrip("/"))
            if "{automation_id}" in template and "{base_url}" in template:
                endpoint = template.format(
                    base_url=root.rstrip("/"),
                    automation_id=automation_id.strip("/"),
                )
            else:
                endpoint = f"{root.rstrip('/')}/{automation_id.strip('/')}/execute"

        target_phone = phone or context.get("customer_phone") or ""
        if not target_phone:
            result["skipped"] = True
            result["reason"]  = "no destination phone"
            return result

        endpoint = endpoint  # already computed above (Phase F5.3)
        payload  = _build_payload(
            cfg=cfg, trigger=trigger, context=context, phone=target_phone,
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
        if not any(c["event_key"] == event_key for c in EVENT_CATALOG):
            raise HTTPException(status_code=404, detail="Unknown event_key")
        patch: Dict[str, Any] = {}
        if payload.enabled is not None:
            patch["enabled"] = bool(payload.enabled)
        if payload.automation_id is not None:
            patch["automation_id"] = payload.automation_id.strip()
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
    @whatsapp_provider_router.get("/available-fields")
    async def get_available_fields(
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ) -> Dict[str, Any]:
        _require_admin_helper(current_user)
        return {"fields": AVAILABLE_FIELDS}

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
        # Sensible auto-fill so OTP-type events still have a code.
        defaults = {
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
            # Phase F8.1 — registered email rides along on auth events.
            "contact_email":  (
                (current_user.get("contact_email") or "").strip()
                or (current_user.get("email") or "").strip()
            ),
        }
        context = {**defaults, **sample}
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


# Public re-exports for server.py wiring.
__all__ = [
    "whatsapp_provider_router",
    "init",
    "seed_default_events",
    "dispatch_event",
    "STAGE_TO_EVENT_KEY",
    "_shipment_to_context",
    "EVENT_CATALOG",
]
