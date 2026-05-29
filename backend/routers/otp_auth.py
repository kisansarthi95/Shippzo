"""
OTP-based authentication router.

Endpoints
---------
POST /api/auth/otp/request   — Generate + store OTP, deliver via WhatsApp.
POST /api/auth/otp/verify    — Validate OTP. Logs the user in (or signs
                               them up on the spot if they're new) and
                               returns a JWT identical to the one the
                               email/password path issues.

Design contract
---------------
The OTP system is **provider-independent**. The router does NOT import
FlowConnect, WATI, Interakt, or any other vendor module. It only knows:

    1. services.otp_service     — generates / validates the code
    2. services.otp_whatsapp    — dispatches the code to the active provider

Switching providers is purely a configuration change (env var or DB
override) — none of the code below changes.

Side effects
------------
A successful `/verify` call:
  • Creates a brand-new user record if one doesn't exist for that phone
    (signup-via-OTP). The new user is auto-seeded with a default courier
    and the 50-credit welcome bonus, matching the email/password signup.
  • Otherwise reuses the existing user (login-via-OTP).
  • Mints a JWT exactly the way `auth.make_token()` does for any other
    sign-in path so the rest of the app sees a uniform session.

The OTP code itself is NEVER returned to the client — only `{ok: true,
expires_in: 300}` so the UI can render a countdown.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from auth import make_token, seed_default_courier
from services.otp_service import (
    CooldownError,
    LockoutError,
    OTP_TTL_SECONDS,
    issue_otp,
    normalise_phone,
    verify_otp,
)
from services.otp_whatsapp import send_otp_via_whatsapp

_LOG = logging.getLogger("otp_auth")

otp_auth_router = APIRouter(prefix="/api/auth/otp", tags=["auth-otp"])

# Late-binding deps populated by ``init()`` so server.py can wire us in
# without an import cycle (matches the pattern used by every other
# router in this codebase).
_db: Any = None


def init() -> None:
    """Bind the live Mongo handle once server.py has finished bootstrapping.
    Kept idempotent so reloads under watchfiles don't break."""
    global _db
    from server import db as _server_db   # late import on purpose
    _db = _server_db


# ─── Request / response models ──────────────────────────────────────
class OtpRequestBody(BaseModel):
    """Body for POST /auth/otp/request.

    ``event_type`` is purely for logging + provider-side templating —
    it does NOT change the OTP rules. Defaulting to "auth" keeps older
    clients (which haven't been updated yet) working.
    """
    phone:      str = Field(..., min_length=4, max_length=20)
    event_type: str = Field("auth", pattern=r"^(login|signup|phone_verification|auth|password_reset|mfa)$")


class OtpVerifyBody(BaseModel):
    """Body for POST /auth/otp/verify.

    ``name`` / ``shop_name`` are optional and used ONLY when the verify
    call results in a fresh user being created (signup-via-OTP). They
    are ignored for existing users.
    """
    phone:      str = Field(..., min_length=4, max_length=20)
    otp:        str = Field(..., min_length=4, max_length=10)
    event_type: str = Field("auth", pattern=r"^(login|signup|phone_verification|auth|password_reset|mfa)$")
    name:       Optional[str] = None
    shop_name:  Optional[str] = None


# ─── /request ──────────────────────────────────────────────────────
@otp_auth_router.post("/request")
async def otp_request(payload: OtpRequestBody) -> Dict[str, Any]:
    """Generate a fresh OTP, persist it (hashed), deliver via the active
    WhatsApp provider, and return delivery metadata to the UI.

    The OTP value is NEVER part of the response — the UI shows a
    "Check your WhatsApp" screen and a countdown derived from
    ``expires_in``.
    """
    if _db is None:
        raise HTTPException(status_code=503, detail="OTP service not ready yet")

    try:
        code, normalised_phone = await issue_otp(
            _db, payload.phone, payload.event_type,
        )
    except CooldownError as ce:
        # 429 lets the mobile client tell the user how long to wait.
        raise HTTPException(status_code=429, detail=str(ce))
    except LockoutError as le:
        # 2026-05-29 — Hit the 5-resend-in-30-min hard cap. Same 429
        # surface as the cooldown but with a much longer suggested
        # wait window baked into the detail string.
        _LOG.warning("OTP resend lockout: phone=%s event=%s",
                     _mask(payload.phone), payload.event_type)
        raise HTTPException(status_code=429, detail=str(le))
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        _LOG.exception("otp_request: issue_otp failed: %s", e)
        raise HTTPException(status_code=500, detail="Could not issue OTP")

    # Best-effort delivery — non-blocking failure semantics. Even if
    # WhatsApp delivery fails we still return success so the user
    # can retry verification (they may have received it on a previous
    # send) or call /request again after the cooldown.
    delivery = await send_otp_via_whatsapp(
        phone=normalised_phone,
        otp=code,
        event_type=payload.event_type,       # type: ignore[arg-type]
        db=_db,
    )

    # Surface a coarse delivery status so the UI can show a helpful
    # secondary message ("Sent to +91*****3210 via WhatsApp") without
    # ever exposing the OTP itself.
    return {
        "ok":          True,
        "phone":       _mask(normalised_phone),
        "event_type":  payload.event_type,
        "expires_in":  OTP_TTL_SECONDS,
        "delivery":    {
            "channel":  "whatsapp",
            "provider": delivery.get("provider") or "unknown",
            "success":  bool(delivery.get("success")),
        },
    }


# ─── /verify ───────────────────────────────────────────────────────
@otp_auth_router.post("/verify")
async def otp_verify(payload: OtpVerifyBody) -> Dict[str, Any]:
    """Validate the OTP and either log the user in or sign them up.

    Behaviour:
      • Wrong / expired OTP            → 400 with reason from the service
      • Existing user with this phone  → JWT minted, user record returned
      • No user → signup-on-the-fly    → JWT minted, fresh user created,
                                          default courier + welcome bonus
                                          seeded just like email/password
                                          signup.

    OTP value is never echoed back; the response shape matches the
    legacy `/api/auth/signup` and `/api/auth/login` payloads so the
    mobile client can swap auth flows without changing its session
    handler.
    """
    if _db is None:
        raise HTTPException(status_code=503, detail="OTP service not ready yet")

    ok, reason = await verify_otp(
        _db, payload.phone, payload.otp, payload.event_type,
    )
    if not ok:
        # Use 400 so the UI shows the friendly reason text from the
        # service (e.g. "Wrong OTP. 3 attempt(s) left.") rather than a
        # generic "Unauthorized".
        raise HTTPException(status_code=400, detail=reason or "Invalid OTP")

    normalised_phone = normalise_phone(payload.phone)

    # Look up existing user. We match on the normalised phone first
    # (which is how every modern signup stores it) and fall back to
    # raw-digit comparison for legacy rows that pre-date normalisation.
    user = await _db.users.find_one({"phone": normalised_phone})
    if not user:
        # Legacy fallback — strip non-digits and compare suffixes so
        # "+91 98765 43210" stored without spaces still matches.
        digits = "".join(c for c in normalised_phone if c.isdigit())
        if digits:
            user = await _db.users.find_one({
                "phone": {"$regex": digits[-10:] + "$"},
            })

    if user:
        # ─── LOGIN ────────────────────────────────────────────────
        uid   = user["id"]
        token = make_token(uid, user.get("email", "") or normalised_phone)
        _LOG.info(
            "OTP login: user_id=%s phone=%s event=%s",
            uid, _mask(normalised_phone), payload.event_type,
        )
        return {
            "ok":     True,
            "mode":   "login",
            "token":  token,
            "user":   _strip_secret_fields(user),
        }

    # ─── SIGNUP (new phone) ──────────────────────────────────────
    # The OTP-verified phone is the proof of identity; we still
    # collect display name + shop name when the client provides them.
    import uuid
    new_uid    = str(uuid.uuid4())
    display_id = f"u{int(datetime.now(timezone.utc).timestamp())}"
    now        = datetime.now(timezone.utc).isoformat()

    # Mirror the email/password signup path so wallet, plan, audit,
    # and admin-claim logic all behave identically.
    is_first = (await _db.users.count_documents({}) == 0)

    user_doc = {
        "id":           new_uid,
        "display_id":   display_id,
        "email":        "",                    # blank — OTP signup, no email
        "password_hash": "",                   # no password yet (user can add later)
        "name":         (payload.name      or "").strip(),
        "shop_name":    (payload.shop_name or "").strip(),
        "phone":        normalised_phone,
        "auth_method":  "otp",                 # audit hint, harmless to existing code
        "is_admin":     is_first,
        "plan":         "free_trial",
        "trial_consumed": False,
        "created_at":   now,
    }
    await _db.users.insert_one(user_doc)

    # Seed the same starter assets the email/password flow would.
    try:
        await seed_default_courier(_db, new_uid)
    except Exception:
        _LOG.exception("OTP signup: starter courier seed failed (non-fatal)")
    try:
        # Identical 50-credit welcome bonus the email signup grants.
        from wallet import add_credits as wallet_add_credits  # type: ignore
        await wallet_add_credits(
            _db, new_uid, 50.0,
            ctype="bonus",
            description="Welcome bonus — 50 free credits to try AI features",
            order_id=f"otp-signup-bonus-{new_uid[:8]}",
        )
    except Exception:
        _LOG.exception("OTP signup: welcome credits failed (non-fatal)")

    token = make_token(new_uid, "")
    _LOG.info(
        "OTP signup: user_id=%s phone=%s event=%s",
        new_uid, _mask(normalised_phone), payload.event_type,
    )
    return {
        "ok":    True,
        "mode":  "signup",
        "token": token,
        "user":  _strip_secret_fields(user_doc),
    }


# ─── Helpers ────────────────────────────────────────────────────────
def _strip_secret_fields(user: Dict[str, Any]) -> Dict[str, Any]:
    """Return a user dict safe to echo to the client (no password_hash,
    no Mongo internal _id)."""
    out = {k: v for k, v in (user or {}).items() if k not in {"password_hash", "_id"}}
    return out


def _mask(raw: str) -> str:
    """Mask all but the last 4 digits of a phone number for logs."""
    if not raw:
        return raw
    import re as _re
    return _re.sub(r"\d(?=\d{4})", "*", raw)
