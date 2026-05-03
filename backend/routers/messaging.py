"""
Phase-12 Messaging — Courier Rules, WhatsApp Templates, Dispatch Confirmation
==============================================================================

Implements three closely-related features as a single self-contained router:

  1. Courier Rules           — per-courier `delivery_eta_days` for the
                               Delivery-Confirmation queue. Two layers:
                                 • admin defaults (admin_config.courier_rules)
                                 • per-user overrides (settings.courier_rules)
  2. WhatsApp Templates      — 4 message types × 3 languages (gu/hi/en).
                               Layered the same way (admin defaults +
                               user overrides) plus the user's preferred
                               default language.
  3. Dispatch Confirmation   — post-Shipped flow that mirrors the existing
                               Delivery-Confirmation queue. Once a parcel
                               flips to Shipped, it sits here until the
                               admin sends the "your parcel is on its way"
                               WhatsApp ping (then `dispatch_msg_status="sent"`).

Late-binding pattern: server.py defines `db`, `get_current_user`,
`_require_admin`, and `utcnow_iso`; this module's `init()` imports them
once at startup and registers all routes. No circular-import risk.

Public:
  - `messaging_router` — APIRouter (prefix /api)
  - `init()`           — late-binding registration
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field


messaging_router = APIRouter(prefix="/api", tags=["messaging-v2"])


# ---------------------------------------------------------------------------
# Constants & defaults
# ---------------------------------------------------------------------------

# Days after `shipped_at` before a parcel enters the delivery-confirmation
# queue when no per-courier rule applies.
DEFAULT_COURIER_DELIVERY_ETA = 5

LANGUAGES = ["gu", "hi", "en"]
TEMPLATE_TYPES = [
    "shipment_sent",          # When parcel is created/booked.
    "dispatch_confirmation",  # When parcel flips to Shipped (sent successfully).
    "delivery_confirmation",  # X days later — "did you receive?".
    "delivery_done",          # After customer confirms received (thanks msg).
    "feedback_request",       # After Feedback stage — asks for a review/rating.
]

# Bundled defaults. {var} placeholders are substituted client-side at send time.
# Available variables: customer_name, order_id, tracking_id, courier, eta_days
DEFAULT_TEMPLATES: Dict[str, Dict[str, str]] = {
    "shipment_sent": {
        "gu": (
            "નમસ્તે {customer_name} 👋\n"
            "તમારો ઓર્ડર #{order_id} બુક થઈ ગયો છે.\n"
            "Courier: {courier}\nTracking: {tracking_id}"
        ),
        "hi": (
            "नमस्ते {customer_name} 👋\n"
            "आपका ऑर्डर #{order_id} बुक हो गया है।\n"
            "Courier: {courier}\nTracking: {tracking_id}"
        ),
        "en": (
            "Hi {customer_name} 👋\n"
            "Your order #{order_id} has been booked.\n"
            "Courier: {courier}\nTracking: {tracking_id}"
        ),
    },
    "dispatch_confirmation": {
        "gu": (
            "નમસ્તે {customer_name} 👋\n"
            "તમારું પાર્સલ #{order_id} સફળતાપૂર્વક મોકલવામાં આવ્યું છે.\n"
            "Courier: {courier}\nTracking: {tracking_id}\n"
            "અપેક્ષિત ડિલિવરી: {eta_days} દિવસ."
        ),
        "hi": (
            "नमस्ते {customer_name} 👋\n"
            "आपका पार्सल #{order_id} सफलतापूर्वक भेज दिया गया है।\n"
            "Courier: {courier}\nTracking: {tracking_id}\n"
            "अनुमानित डिलीवरी: {eta_days} दिन।"
        ),
        "en": (
            "Hi {customer_name} 👋\n"
            "Your parcel #{order_id} has been dispatched successfully.\n"
            "Courier: {courier}\nTracking: {tracking_id}\n"
            "Expected delivery: {eta_days} days."
        ),
    },
    "delivery_confirmation": {
        "gu": (
            "નમસ્તે {customer_name} 🙏\n"
            "તમારો પાર્સલ #{order_id} મળી ગયો છે?\n\n"
            "Reply: YES / NO"
        ),
        "hi": (
            "नमस्ते {customer_name} 🙏\n"
            "क्या आपको आपका पार्सल #{order_id} मिल गया है?\n\n"
            "Reply: YES / NO"
        ),
        "en": (
            "Hi {customer_name} 🙏\n"
            "Did you receive your parcel #{order_id}?\n\n"
            "Reply: YES / NO"
        ),
    },
    "delivery_done": {
        "gu": (
            "ઓર્ડર #{order_id} મળી ગયો તેની પુષ્ટિ માટે આભાર 🙏\n"
            "ફરી મળતા રહીશું!"
        ),
        "hi": (
            "ऑर्डर #{order_id} मिलने की पुष्टि के लिए धन्यवाद 🙏\n"
            "फिर मिलते रहेंगे!"
        ),
        "en": (
            "Thank you for confirming receipt of order #{order_id} 🙏\n"
            "Looking forward to serving you again!"
        ),
    },
    "feedback_request": {
        "gu": (
            "નમસ્તે {customer_name} 🌟\n"
            "તમારા ઓર્ડર #{order_id} અંગે તમારો અનુભવ શેર કરશો?\n"
            "તમારા feedback થી અમે વધુ સારી service આપી શકીશું.\n\n"
            "⭐ Google પર rating આપો: {google_review_url}\n"
            "🛒 Website પર review: {website_url}\n\n"
            "આભાર! 🙏"
        ),
        "hi": (
            "नमस्ते {customer_name} 🌟\n"
            "क्या आप अपने ऑर्डर #{order_id} का अनुभव साझा करेंगे?\n"
            "आपके feedback से हम बेहतर सेवा दे सकते हैं।\n\n"
            "⭐ Google पर rating दें: {google_review_url}\n"
            "🛒 Website पर review: {website_url}\n\n"
            "धन्यवाद! 🙏"
        ),
        "en": (
            "Hi {customer_name} 🌟\n"
            "How was your experience with order #{order_id}?\n"
            "Your feedback helps us improve.\n\n"
            "⭐ Rate us on Google: {google_review_url}\n"
            "🛒 Review on website: {website_url}\n\n"
            "Thank you! 🙏"
        ),
    },
}


def _days_since_iso(iso_str: Optional[str]) -> int:
    """UTC days between `iso_str` and now. Returns 0 on parse failure."""
    if not iso_str:
        return 0
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - dt
        return max(0, int(delta.total_seconds() // 86400))
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Pydantic request models
# ---------------------------------------------------------------------------

class CourierRulePayload(BaseModel):
    """Body shape for PUT /admin|me/courier-rules."""
    rules: Dict[str, Dict[str, Any]] = Field(default_factory=dict)


class TemplatePayload(BaseModel):
    """Body shape for PUT /admin|me/whatsapp-templates."""
    templates: Dict[str, Dict[str, str]] = Field(default_factory=dict)
    default_language: Optional[str] = None  # only respected on /me/...
    # Phase-12: per-user business links referenced by the feedback
    # template's {google_review_url} / {website_url} variables. Only
    # respected on /me/... (admin config ignores this field).
    business_links: Optional[Dict[str, str]] = None


class DispatchBulkRequest(BaseModel):
    shipment_ids: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Late-binding init — pulls helpers from server.py once.
# ---------------------------------------------------------------------------

def init() -> None:
    from server import (  # noqa: WPS433 — intentional late import
        db,
        get_current_user as _get_current_user,
        _require_admin as _require_admin_helper,
        utcnow_iso,
    )

    # =====================================================================
    # Internal helpers (closure-scoped so they can use `db` cleanly).
    # =====================================================================

    async def _load_admin_courier_rules() -> Dict[str, Dict[str, Any]]:
        doc = await db.admin_config.find_one(
            {"_id": "default"}, {"_id": 0, "courier_rules": 1},
        ) or {}
        rules = dict(doc.get("courier_rules") or {})
        # Ensure the catch-all fallback always exists in the response.
        rules.setdefault(
            "_default_", {"delivery_eta_days": DEFAULT_COURIER_DELIVERY_ETA},
        )
        return rules

    async def _load_user_courier_rules(user_id: str) -> Dict[str, Dict[str, Any]]:
        s = await db.settings.find_one(
            {"user_id": user_id}, {"_id": 0, "courier_rules": 1},
        ) or {}
        return dict(s.get("courier_rules") or {})

    async def resolve_courier_eta(user_id: str, courier_name: str) -> int:
        """Resolves the delivery_eta_days for a courier.
        Precedence: user override > admin default > _default_ override > constant.
        """
        u = await _load_user_courier_rules(user_id)
        a = await _load_admin_courier_rules()
        cn = (courier_name or "").strip()
        if cn:
            uv = u.get(cn, {}).get("delivery_eta_days")
            if uv is not None:
                try:
                    return max(0, int(uv))
                except Exception:
                    pass
            av = a.get(cn, {}).get("delivery_eta_days")
            if av is not None:
                try:
                    return max(0, int(av))
                except Exception:
                    pass
        # Fall back to _default_.
        for layer in (u, a):
            v = (layer.get("_default_") or {}).get("delivery_eta_days")
            if v is not None:
                try:
                    return max(0, int(v))
                except Exception:
                    pass
        return DEFAULT_COURIER_DELIVERY_ETA

    # Expose helper for use elsewhere (e.g. server.py delivery-confirmation).
    init.resolve_courier_eta = resolve_courier_eta  # type: ignore[attr-defined]

    def _clean_rules_payload(rules_in: Dict[str, Any]) -> Dict[str, Dict[str, int]]:
        cleaned: Dict[str, Dict[str, int]] = {}
        for k, v in (rules_in or {}).items():
            if not isinstance(k, str):
                continue
            key = k.strip()
            if not key:
                continue
            eta = (v or {}).get("delivery_eta_days") if isinstance(v, dict) else None
            if eta is None or eta == "":
                continue
            try:
                eta_int = int(eta)
            except (TypeError, ValueError):
                continue
            if eta_int < 0 or eta_int > 60:
                continue
            cleaned[key] = {"delivery_eta_days": eta_int}
        return cleaned

    # =====================================================================
    # Courier Rules — admin
    # =====================================================================

    @messaging_router.get("/admin/courier-rules")
    async def admin_get_courier_rules(
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        _require_admin_helper(current_user)
        rules = await _load_admin_courier_rules()
        return {
            "rules": rules,
            "default_eta_days": DEFAULT_COURIER_DELIVERY_ETA,
        }

    @messaging_router.put("/admin/courier-rules")
    async def admin_put_courier_rules(
        payload: CourierRulePayload,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        _require_admin_helper(current_user)
        cleaned = _clean_rules_payload(payload.rules)
        await db.admin_config.update_one(
            {"_id": "default"},
            {"$set": {"courier_rules": cleaned}},
            upsert=True,
        )
        return await admin_get_courier_rules(current_user)

    # =====================================================================
    # Courier Rules — per-user
    # =====================================================================

    @messaging_router.get("/me/courier-rules")
    async def me_get_courier_rules(
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        admin_rules = await _load_admin_courier_rules()
        user_rules = await _load_user_courier_rules(current_user["id"])
        # The user's own couriers — for the picker UI.
        cursor = db.couriers.find(
            {"user_id": current_user["id"]}, {"_id": 0, "name": 1},
        )
        couriers = await cursor.to_list(500)
        names = sorted({(c.get("name") or "").strip() for c in couriers if c.get("name")})
        return {
            "admin_rules": admin_rules,
            "user_rules": user_rules,
            "courier_names": names,
            "default_eta_days": DEFAULT_COURIER_DELIVERY_ETA,
        }

    @messaging_router.put("/me/courier-rules")
    async def me_put_courier_rules(
        payload: CourierRulePayload,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        cleaned = _clean_rules_payload(payload.rules)
        await db.settings.update_one(
            {"user_id": current_user["id"]},
            {"$set": {"courier_rules": cleaned}},
            upsert=True,
        )
        return await me_get_courier_rules(current_user)

    # =====================================================================
    # WhatsApp Templates
    # =====================================================================

    def _merge_templates(
        saved: Dict[str, Dict[str, str]],
        fallback: Dict[str, Dict[str, str]],
    ) -> Dict[str, Dict[str, str]]:
        out: Dict[str, Dict[str, str]] = {}
        for t in TEMPLATE_TYPES:
            out[t] = {}
            for lang in LANGUAGES:
                v = (saved.get(t) or {}).get(lang)
                out[t][lang] = (v if (v and str(v).strip()) else fallback[t][lang])
        return out

    def _clean_templates_payload(
        templates_in: Dict[str, Any],
    ) -> Dict[str, Dict[str, str]]:
        cleaned: Dict[str, Dict[str, str]] = {}
        for t in TEMPLATE_TYPES:
            tdata = templates_in.get(t) or {}
            if not isinstance(tdata, dict):
                continue
            for lang in LANGUAGES:
                val = tdata.get(lang)
                if val is None:
                    continue
                s = str(val).strip()
                if s:
                    cleaned.setdefault(t, {})[lang] = s[:5000]
        return cleaned

    @messaging_router.get("/admin/whatsapp-templates")
    async def admin_get_templates(
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        _require_admin_helper(current_user)
        doc = await db.admin_config.find_one(
            {"_id": "default"}, {"_id": 0, "whatsapp_templates": 1},
        ) or {}
        saved = doc.get("whatsapp_templates") or {}
        merged = _merge_templates(saved, DEFAULT_TEMPLATES)
        return {
            "templates": merged,
            "saved_overrides": saved,
            "defaults": DEFAULT_TEMPLATES,
            "types": TEMPLATE_TYPES,
            "languages": LANGUAGES,
        }

    @messaging_router.put("/admin/whatsapp-templates")
    async def admin_put_templates(
        payload: TemplatePayload,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        _require_admin_helper(current_user)
        cleaned = _clean_templates_payload(payload.templates)
        await db.admin_config.update_one(
            {"_id": "default"},
            {"$set": {"whatsapp_templates": cleaned}},
            upsert=True,
        )
        return await admin_get_templates(current_user)

    @messaging_router.get("/me/whatsapp-templates")
    async def me_get_templates(
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        admin_doc = await db.admin_config.find_one(
            {"_id": "default"}, {"_id": 0, "whatsapp_templates": 1},
        ) or {}
        admin_saved = admin_doc.get("whatsapp_templates") or {}
        admin_merged = _merge_templates(admin_saved, DEFAULT_TEMPLATES)

        user_doc = await db.settings.find_one(
            {"user_id": current_user["id"]},
            {
                "_id": 0,
                "whatsapp_templates": 1,
                "default_message_language": 1,
                "business_links": 1,
            },
        ) or {}
        user_saved = user_doc.get("whatsapp_templates") or {}
        user_lang = (user_doc.get("default_message_language") or "gu").lower()
        if user_lang not in LANGUAGES:
            user_lang = "gu"
        # business_links are user-level settings referenced by the
        # {google_review_url} / {website_url} template variables.
        biz = user_doc.get("business_links") or {}
        return {
            "admin_templates": admin_merged,    # what the user sees as base
            "user_templates": user_saved,       # only fields user explicitly set
            "default_language": user_lang,
            "types": TEMPLATE_TYPES,
            "languages": LANGUAGES,
            "defaults": DEFAULT_TEMPLATES,
            "business_links": {
                "google_review_url": str(biz.get("google_review_url") or ""),
                "website_url": str(biz.get("website_url") or ""),
            },
        }

    @messaging_router.put("/me/whatsapp-templates")
    async def me_put_templates(
        payload: TemplatePayload,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        cleaned = _clean_templates_payload(payload.templates)
        update: Dict[str, Any] = {"whatsapp_templates": cleaned}
        if payload.default_language is not None:
            lang = str(payload.default_language).lower()
            if lang in LANGUAGES:
                update["default_message_language"] = lang
        # business_links: accepted as an optional nested object. Only
        # fields actually sent are written; the client can null-out a
        # URL by sending empty string.
        if payload.business_links is not None:
            bl = payload.business_links or {}
            cleaned_links: Dict[str, str] = {}
            for k in ("google_review_url", "website_url"):
                v = bl.get(k)
                if v is None:
                    continue
                s = str(v).strip()
                # Cheap URL sanity: must look http(s) or start with www
                # — otherwise we store whatever the user typed so they
                # see it round-trip, but the template will just show
                # the raw text at send time.
                cleaned_links[k] = s[:500]
            update["business_links"] = cleaned_links
        await db.settings.update_one(
            {"user_id": current_user["id"]},
            {"$set": update},
            upsert=True,
        )
        return await me_get_templates(current_user)

    @messaging_router.get("/me/resolve-template")
    async def me_resolve_template(
        ttype: str,
        lang: Optional[str] = None,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Returns the most-specific template body for a given type+language.
        Precedence: user override → admin default → bundled fallback.

        Phase-12: server-side substitution for the two business-level
        variables `{google_review_url}` and `{website_url}`. These live
        in settings.business_links (per-user) so every template type
        can reference them without the client having to know where
        they're stored. Customer-specific variables (name, order_id,
        courier, …) remain client-side substitutions because they
        depend on the specific shipment being messaged.
        """
        if ttype not in TEMPLATE_TYPES:
            raise HTTPException(400, "invalid template type")
        user_doc = await db.settings.find_one(
            {"user_id": current_user["id"]},
            {
                "_id": 0,
                "default_message_language": 1,
                "whatsapp_templates": 1,
                "business_links": 1,
            },
        ) or {}
        chosen_lang = (lang or user_doc.get("default_message_language") or "gu").lower()
        if chosen_lang not in LANGUAGES:
            chosen_lang = "gu"

        # Resolution cascade: user > admin > bundled fallback.
        template_body = ""
        source = "bundled"
        u = ((user_doc.get("whatsapp_templates") or {}).get(ttype) or {}).get(chosen_lang)
        if u and str(u).strip():
            template_body = u
            source = "user"
        else:
            admin_doc = await db.admin_config.find_one(
                {"_id": "default"}, {"_id": 0, "whatsapp_templates": 1},
            ) or {}
            a = ((admin_doc.get("whatsapp_templates") or {}).get(ttype) or {}).get(chosen_lang)
            if a and str(a).strip():
                template_body = a
                source = "admin"
            else:
                template_body = DEFAULT_TEMPLATES[ttype][chosen_lang]
                source = "bundled"

        # Business-link substitution (graceful: empty string if the user
        # hasn't set the URL yet — the surrounding template copy still
        # reads fine, just without a clickable URL to paste).
        links = user_doc.get("business_links") or {}
        gurl = str(links.get("google_review_url") or "").strip()
        wurl = str(links.get("website_url") or "").strip()
        resolved = (
            template_body
            .replace("{google_review_url}", gurl)
            .replace("{website_url}", wurl)
        )

        return {
            "template": resolved,
            "raw_template": template_body,     # un-substituted (for editor preview)
            "language": chosen_lang,
            "source": source,
            "business_links": {
                "google_review_url": gurl,
                "website_url": wurl,
            },
        }

    # =====================================================================
    # Dispatch Confirmation — post-Shipped notification flow
    # =====================================================================

    @messaging_router.get("/shipments/dispatch-confirmation")
    async def dispatch_confirmation_list(
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """All shipments currently in "Shipped" status, with bucket counts
        for the List / Sent / Pending tabs (`dispatch_msg_status`)."""
        q = {"user_id": current_user["id"], "status": "Shipped"}
        rows = await db.shipments.find(q, {"_id": 0}).sort("shipped_at", -1).to_list(5000)
        for r in rows:
            r["days_since_shipped"] = _days_since_iso(
                r.get("shipped_at") or r.get("created_at"),
            )
        counts = {
            "list": len(rows),
            "sent": sum(1 for r in rows if r.get("dispatch_msg_status") == "sent"),
            "pending": sum(
                1 for r in rows
                if (r.get("dispatch_msg_status") or "pending") == "pending"
            ),
        }
        return {"shipments": rows, "counts": counts}

    @messaging_router.post("/shipments/dispatch-confirmation/mark-sent")
    async def dispatch_confirmation_mark_sent(
        payload: DispatchBulkRequest,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Bulk-mark shipments as dispatch-message-sent. Same-day repeat
        sends are blocked to avoid spam (matches delivery-confirmation rule).
        """
        ids = [i for i in (payload.shipment_ids or []) if i]
        if not ids:
            return {
                "updated": 0, "skipped": 0,
                "updated_ids": [], "skipped_ids": [],
            }
        today = utcnow_iso()[:10]
        rows = await db.shipments.find(
            {"user_id": current_user["id"], "id": {"$in": ids}},
            {"_id": 0, "id": 1, "dispatch_msg_status": 1, "dispatch_msg_sent_at": 1},
        ).to_list(len(ids))
        updated_ids: List[str] = []
        skipped_ids: List[str] = []
        for r in rows:
            last = r.get("dispatch_msg_sent_at") or ""
            if r.get("dispatch_msg_status") == "sent" and last.startswith(today):
                skipped_ids.append(r["id"])
                continue
            updated_ids.append(r["id"])
        if updated_ids:
            await db.shipments.update_many(
                {"user_id": current_user["id"], "id": {"$in": updated_ids}},
                {"$set": {
                    "dispatch_msg_status": "sent",
                    "dispatch_msg_sent_at": utcnow_iso(),
                }},
            )
        return {
            "updated": len(updated_ids),
            "skipped": len(skipped_ids),
            "updated_ids": updated_ids,
            "skipped_ids": skipped_ids,
        }

    @messaging_router.post("/shipments/dispatch-confirmation/reset")
    async def dispatch_confirmation_reset(
        payload: DispatchBulkRequest,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Manually flip dispatch_msg_status back to 'pending' for retries.
        Useful if a chat failed to open or message was wrong."""
        ids = [i for i in (payload.shipment_ids or []) if i]
        if not ids:
            return {"updated": 0}
        res = await db.shipments.update_many(
            {"user_id": current_user["id"], "id": {"$in": ids}},
            {"$set": {"dispatch_msg_status": "pending"}},
        )
        return {"updated": int(res.modified_count)}

    # =====================================================================
    # Updated Delivery Confirmation list — uses per-courier ETA rules
    # =====================================================================

    @messaging_router.get("/shipments/delivery-confirmation-v2")
    async def delivery_confirmation_list_v2(
        threshold_days: Optional[int] = None,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Same shape as the legacy /delivery-confirmation but threshold
        is resolved per-shipment from the courier rules.
        Pass `threshold_days` to override globally (legacy behaviour).
        """
        q = {
            "user_id": current_user["id"],
            "status": "Shipped",
            "confirmation_status": {"$ne": "confirmed"},
        }
        rows = await db.shipments.find(q, {"_id": 0}).sort("shipped_at", 1).to_list(5000)
        admin_rules = await _load_admin_courier_rules()
        user_rules = await _load_user_courier_rules(current_user["id"])

        def _eta_for(courier_name: str) -> int:
            cn = (courier_name or "").strip()
            if cn:
                uv = (user_rules.get(cn) or {}).get("delivery_eta_days")
                if uv is not None:
                    try:
                        return max(0, int(uv))
                    except Exception:
                        pass
                av = (admin_rules.get(cn) or {}).get("delivery_eta_days")
                if av is not None:
                    try:
                        return max(0, int(av))
                    except Exception:
                        pass
            for layer in (user_rules, admin_rules):
                v = (layer.get("_default_") or {}).get("delivery_eta_days")
                if v is not None:
                    try:
                        return max(0, int(v))
                    except Exception:
                        pass
            return DEFAULT_COURIER_DELIVERY_ETA

        enriched: List[Dict[str, Any]] = []
        eta_min = 10 ** 9
        eta_max = 0
        for r in rows:
            days = _days_since_iso(r.get("shipped_at") or r.get("created_at"))
            r["days_since_shipped"] = days
            eta = _eta_for(r.get("courier_name") or "")
            r["courier_eta_days"] = eta
            if eta < eta_min:
                eta_min = eta
            if eta > eta_max:
                eta_max = eta
            override = (
                int(threshold_days) if (threshold_days is not None) else eta
            )
            if days >= override:
                enriched.append(r)
        if eta_min == 10 ** 9:
            eta_min = DEFAULT_COURIER_DELIVERY_ETA
            eta_max = DEFAULT_COURIER_DELIVERY_ETA
        counts = {
            "list":    len(enriched),
            "sent":    sum(1 for r in enriched if r.get("confirmation_status") == "sent"),
            "replied": sum(1 for r in enriched if r.get("confirmation_status") == "replied"),
            "pending": sum(
                1 for r in enriched
                if (r.get("confirmation_status") or "pending") == "pending"
            ),
        }
        return {
            "shipments": enriched,
            "counts": counts,
            "eta_min": eta_min,
            "eta_max": eta_max,
            "threshold_override": threshold_days,
        }
