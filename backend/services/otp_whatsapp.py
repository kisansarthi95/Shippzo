"""
Centralised OTP-over-WhatsApp dispatcher.

Every part of Shippzo that mints a one-time code (login, signup,
phone verification, future MFA, password reset, etc.) calls ONLY
the single coroutine exported here:

    from services.otp_whatsapp import send_otp_via_whatsapp
    ...
    await send_otp_via_whatsapp(
        phone=user.phone,
        otp=generated_otp,
        event_type="login",
        db=db,
        user_name=user.name,
    )

Guarantees of this dispatcher:
  • NEVER raises.            (Auth path is never blocked by BSP errors.)
  • NEVER returns the OTP.   (OTP value never leaves the auth module.)
  • ALWAYS logs the event.   (To Python logger + Mongo audit collection.)
  • Provider-agnostic.       (Switch providers via env / DB without
                              touching this file or its callers.)

The coroutine fires the request inline. If you need fully fire-and-
forget semantics in a particularly hot endpoint, wrap the call in
``asyncio.create_task(send_otp_via_whatsapp(...))`` and ignore the
return value — the dispatcher's internal logging still records the
outcome.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, Literal, Optional

from whatsapp_providers import get_active_provider
from whatsapp_providers.base import ProviderResult

_LOG = logging.getLogger("otp_whatsapp")

# Stable, audit-friendly enumeration. Adding a new caller? Add the
# event string here too so dashboards can filter cleanly.
OtpEventType = Literal[
    "login",
    "signup",
    "phone_verification",
    "auth",                # generic / fall-back
    "password_reset",      # reserved for future flow
    "mfa",                 # reserved for future flow
]

_AUDIT_COLLECTION = os.getenv("OTP_WHATSAPP_AUDIT_COLLECTION", "otp_whatsapp_log")
_AUDIT_KEEP_OTP   = os.getenv("OTP_WHATSAPP_AUDIT_KEEP_OTP", "false").lower() in {"1", "true", "yes"}
_PHONE_MASK_RE    = re.compile(r"\d(?=\d{4})")


def _mask_phone(raw: str) -> str:
    """Mask all but the last 4 digits — used in stdout logs so we
    don't sprinkle full phone numbers across server.log."""
    if not raw:
        return raw
    return _PHONE_MASK_RE.sub("*", raw)


async def _write_audit_row(
    db: Any | None,
    *,
    provider:    str,
    phone:       str,
    otp:         str,
    event_type:  str,
    success:     bool,
    status_code: Optional[int],
    request_payload: Any,
    response_body:   Any,
    error:       Optional[str],
    duration_ms: float,
) -> None:
    """Persist a row in the audit collection. Best-effort — any DB
    failure here is swallowed so it can't block the auth path."""
    if db is None:
        return
    try:
        doc: Dict[str, Any] = {
            "ts":          datetime.now(timezone.utc).isoformat(),
            "provider":    provider,
            "phone":       phone,        # full phone — audit only, never in stdout
            "event_type":  event_type,
            "success":     bool(success),
            "status_code": status_code,
            "request_payload": request_payload,
            "response_body":   response_body,
            "error":       error,
            "duration_ms": round(float(duration_ms or 0), 1),
        }
        # Storing the OTP itself is OFF by default. Operators can opt in
        # via OTP_WHATSAPP_AUDIT_KEEP_OTP=true when investigating a
        # specific delivery problem — and turn it OFF again afterwards.
        if _AUDIT_KEEP_OTP:
            doc["otp"] = str(otp)
        await db[_AUDIT_COLLECTION].insert_one(doc)
    except Exception as e:
        _LOG.warning("otp_whatsapp: audit write failed (non-fatal): %s", e)


async def send_otp_via_whatsapp(
    *,
    phone: str,
    otp: str,
    event_type: OtpEventType = "auth",
    db: Any | None = None,
    user_name: str = "",
    contact_email: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Deliver ``otp`` to ``phone`` via the currently-active provider.

    Parameters
    ----------
    phone        : User's mobile number. Provider implementations
                   normalise to E.164 — pass it exactly as it sits
                   in your DB / form.
    otp          : The one-time code. The dispatcher does NOT validate
                   format or length — that's the OTP module's job.
    event_type   : One of ``login`` / ``signup`` / ``phone_verification`` /
                   ``auth`` / ``password_reset`` / ``mfa``.
    db           : Async Mongo handle for audit logging. Optional;
                   when None the dispatcher logs to stdout only.
    user_name    : Display name (used by some providers in the WhatsApp
                   greeting line). Optional.
    extra        : Provider-specific override fields. Optional.

    Returns
    -------
    dict
        ``{"success": bool, "provider": str|None, "error": str|None,
           "status_code": int|None, "duration_ms": float}``

        Safe to ignore — the dispatcher always logs the outcome.
    """
    # ── Pre-flight: input validation (soft) ──────────────────────────
    if not phone or not otp:
        _LOG.warning(
            "otp_whatsapp: skipped — phone or otp missing (event=%s)", event_type,
        )
        return {
            "success": False, "provider": None,
            "error":   "phone or otp missing",
            "status_code": None, "duration_ms": 0,
        }

    # ── Resolve provider ─────────────────────────────────────────────
    # Phase-28: First try the new admin-configured event-trigger system
    # (one automation per event). If that path is unconfigured or
    # disabled, fall back to the legacy single-endpoint provider so
    # nothing breaks for existing deployments.
    if event_type in ("login", "signup", "phone_verification", "password_reset"):
        try:
            from routers.whatsapp_provider import dispatch_event as _wpp_dispatch
            event_key_map = {
                "login":              "otp_login",
                "signup":             "otp_signup",
                "phone_verification": "otp_signup",
                "password_reset":     "otp_password_reset",
            }
            ev_key = event_key_map.get(str(event_type)) or "otp_login"
            ctx = {
                "customer_name":  user_name or "Customer",
                "customer_phone": phone,
                "otp":            str(otp),
                "event_type":     str(event_type),
                "business_name":  "Shippzo",
                "current_stage":  "Auth",
                # Phase F8.1 — the user's registered email travels with
                # the webhook payload as "contact_email" so the
                # operator's automation can deliver the OTP via email.
                "contact_email":  (contact_email or "").strip(),
            }
            t0 = datetime.now(timezone.utc).timestamp()
            outcome = await _wpp_dispatch(db, ev_key, ctx, phone=phone)
            if not outcome.get("skipped"):
                duration_ms = (datetime.now(timezone.utc).timestamp() - t0) * 1000.0
                _LOG.info(
                    "OTP via WhatsApp-provider event=%s phone=%s success=%s",
                    ev_key, _mask_phone(phone), outcome.get("success"),
                )
                # Audit row mirrors legacy shape so dashboards still work.
                await _write_audit_row(
                    db,
                    provider="event_trigger:" + ev_key,
                    phone=phone, otp=otp,
                    event_type=str(event_type),
                    success=bool(outcome.get("success")),
                    status_code=outcome.get("status_code"),
                    request_payload={"event_key": ev_key},
                    response_body=None,
                    error=outcome.get("reason") if not outcome.get("success") else None,
                    duration_ms=outcome.get("duration_ms") or duration_ms,
                )
                return {
                    "success":     bool(outcome.get("success")),
                    "provider":    "event_trigger",
                    "error":       outcome.get("reason")
                                    if not outcome.get("success") else None,
                    "status_code": outcome.get("status_code"),
                    "duration_ms": outcome.get("duration_ms") or 0,
                }
            # else: fall through to legacy single-endpoint path below.
        except Exception as e:
            _LOG.warning(
                "otp_whatsapp: event-trigger dispatch errored, falling "
                "back to legacy provider: %s", e,
            )

    provider = await get_active_provider(db)
    if provider is None:
        _LOG.warning(
            "otp_whatsapp: no active provider for event=%s phone=%s",
            event_type, _mask_phone(phone),
        )
        # Still write an audit row so the operator can see that an OTP
        # WAS generated but not pushed via WhatsApp.
        await _write_audit_row(
            db,
            provider="none", phone=phone, otp=otp,
            event_type=str(event_type),
            success=False, status_code=None,
            request_payload=None, response_body=None,
            error="no active provider configured",
            duration_ms=0,
        )
        return {
            "success": False, "provider": None,
            "error":   "no active provider configured",
            "status_code": None, "duration_ms": 0,
        }

    # ── Fire the request ─────────────────────────────────────────────
    try:
        result: ProviderResult = await provider.send_otp(
            phone=phone,
            otp=otp,
            event_type=str(event_type),
            user_name=user_name,
            extra=extra,
        )
    except Exception as e:
        # Defensive: providers MUST not raise, but just in case.
        _LOG.exception(
            "otp_whatsapp: provider %s raised unexpectedly: %s",
            provider.name, e,
        )
        await _write_audit_row(
            db,
            provider=provider.name, phone=phone, otp=otp,
            event_type=str(event_type),
            success=False, status_code=None,
            request_payload=None, response_body=None,
            error=f"provider raised: {e.__class__.__name__}: {e}",
            duration_ms=0,
        )
        return {
            "success": False, "provider": provider.name,
            "error":   f"{e.__class__.__name__}: {e}",
            "status_code": None, "duration_ms": 0,
        }

    # ── Structured log + audit row ──────────────────────────────────
    log_payload = {
        "event":       "otp_whatsapp",
        "event_type":  event_type,
        "phone":       _mask_phone(phone),
        **result.to_log_dict(),
    }
    if result.success:
        _LOG.info("OTP delivered via WhatsApp: %s", log_payload)
    else:
        _LOG.warning("OTP delivery FAILED via WhatsApp: %s", log_payload)

    await _write_audit_row(
        db,
        provider=result.provider, phone=phone, otp=otp,
        event_type=str(event_type),
        success=result.success, status_code=result.status_code,
        request_payload=result.request_payload,
        response_body=result.response_body,
        error=result.error,
        duration_ms=result.duration_ms,
    )

    return {
        "success":     result.success,
        "provider":    result.provider,
        "error":       result.error,
        "status_code": result.status_code,
        "duration_ms": result.duration_ms,
    }
