"""
Auth router — all email+password, OAuth Google, and self-service
forgot-password endpoints. Extracted from server.py in Phase-29
(2026-05-30) as part of the maintainability refactor.

Behavioural contract: ZERO change.
  • Route paths, request shapes, and response payloads are identical
    to the previous inline implementation in server.py.
  • The Phase-G2 "needs_profile_completion" gate behaves the same way
    (set only on brand-new Google signups; cleared by
    /auth/complete-profile; never re-raised on re-login).
  • The 2-factor (email + phone) forgot-password gate and its 3-per-
    hour rate limit still live here.

Why it's safe to move:
  • Every helper this code touches (hash_password / verify_password /
    make_token / user_public / utcnow_iso / SignupRequest /
    LoginRequest / UserPublic / ForgotPasswordRequest /
    CompleteProfileRequest / seed_default_courier /
    claim_legacy_data_for_admin) already lives in /app/backend/auth.py.
    We just import them here directly.
  • The remaining server-bound symbols (`db`, `logger`, `_next_display_id`,
    `get_current_user`) are pulled in via the standard late-bind init()
    pattern, mirroring routers/faq.py and routers/articles.py.

Endpoints registered:
  POST /api/auth/signup
  POST /api/auth/login
  GET  /api/auth/me
  GET  /api/auth/context
  POST /api/auth/complete-profile
  GET  /api/auth/business-categories
  POST /api/auth/business-category
  POST /api/auth/logout
  POST /api/auth/forgot-password
  POST /api/auth/google/session
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth import (
    SignupRequest, LoginRequest, UserPublic,
    ForgotPasswordRequest, ForgotPasswordOtpRequest, ContactEmailRequest,
    CompleteProfileRequest,
    hash_password, verify_password, make_token, user_public,
    utcnow_iso as auth_utcnow_iso,
    seed_default_courier, claim_legacy_data_for_admin,
)
from plans import plan_start_payload
from wallet import add_credits as wallet_add_credits

_LOG = logging.getLogger("routers.auth")

auth_router = APIRouter(prefix="/api/auth", tags=["auth"])


# ─── Inline-defined Pydantic models (kept local so auth.py doesn't need
#     to grow another schema) ─────────────────────────────────────────
class BusinessCategoryPayload(BaseModel):
    """Body for POST /api/auth/business-category."""
    category: str


class GoogleSessionRequest(BaseModel):
    """Body for POST /api/auth/google/session — the session_id Emergent
    Auth handed to the client after the user finished Google consent."""
    session_id: str


# ─── Forgot-password helpers (only used by this router) ─────────────
# Rate limit: 3 failed attempts per email per hour. Audit-logged.
_PWD_RESET_MAX_ATTEMPTS = 3
_PWD_RESET_WINDOW_SEC   = 3600  # 1 hour


def init() -> None:
    """Late-bind handlers — pulls shared db / auth helpers out of
    server.py at first call so we don't take a circular dep at import
    time.

    Note: we deliberately DO NOT import server.logger here — server.py's
    module-level `logger` is defined late in the file and may not exist
    yet when init() runs during the very first import of server. We
    use this module's local `_LOG` instead.
    """
    from server import (  # noqa: WPS433
        db,
        get_current_user as _get_current_user,
        _next_display_id as _next_display_id_helper,
    )
    logger = _LOG  # local alias so the cut-and-paste handler bodies
                   # below keep reading `logger.exception(...)` etc.

    # ── Local helpers (close over `db`) ─────────────────────────────
    async def _count_recent_pwd_failures(email: str) -> int:
        cutoff = datetime.utcnow() - timedelta(seconds=_PWD_RESET_WINDOW_SEC)
        return await db.pwd_reset_attempts.count_documents({
            "email": email,
            "ok":    False,
            "at":    {"$gte": cutoff.isoformat() + "+00:00"},
        })

    async def _log_pwd_attempt(email: str, ok: bool, reason: str = "") -> None:
        await db.pwd_reset_attempts.insert_one({
            "email":  email,
            "ok":     bool(ok),
            "reason": reason[:120] if reason else "",
            "at":     datetime.utcnow().isoformat() + "+00:00",
        })

    # ── POST /api/auth/signup ───────────────────────────────────────
    @auth_router.post("/signup")
    async def auth_signup(payload: SignupRequest):
        """Create a new account + seed per-user data.

        The very first signup becomes the `admin` and inherits any existing
        pre-multi-tenant data (shipments/couriers/settings that have no
        user_id yet). All subsequent signups get a fresh workspace with 15
        demo shipments + 1 starter courier.
        """
        email = payload.email.lower().strip()
        if await db.users.find_one({"email": email}):
            raise HTTPException(status_code=400, detail="Email already registered")

        # Phase G — primary business category. Required on the form, but
        # we accept blank for backward-compat (older clients) and let the
        # post-login onboarding gate catch them.
        pbc = (payload.primary_business_category or "").strip()
        if pbc:
            from business_categories import is_valid_category
            if not is_valid_category(pbc):
                raise HTTPException(
                    status_code=400,
                    detail="Please pick a valid business category.",
                )

        # Normalise + validate phone — allow digits + optional leading "+".
        phone_raw = (payload.phone or "").strip()
        phone_digits = re.sub(r"\D", "", phone_raw)
        if len(phone_digits) < 10 or len(phone_digits) > 13:
            raise HTTPException(
                status_code=400,
                detail="Please enter a valid 10-digit mobile number.",
            )
        # Store the last 10 digits (drops country code prefixes). This makes
        # forgot-password lookups forgiving of "+91" variations.
        phone = phone_digits[-10:]

        now = auth_utcnow_iso()
        is_first = (await db.users.count_documents({})) == 0
        uid = str(uuid.uuid4())
        display_id = await _next_display_id_helper()

        # Phase-2b: Device-fingerprint anti-abuse — block repeated free
        # trials from the same physical device. We do NOT block the signup
        # itself (legitimate users may share devices), we just deny the new
        # account the free trial. Admin can override later via the plan
        # endpoints if a real customer is affected.
        fp = (payload.device_fingerprint or "").strip()
        deny_free_trial = False
        if fp and not is_first:
            try:
                prior = await db.users.count_documents({
                    "device_fingerprint": fp,
                    "$or": [
                        {"plan": "free_trial"},
                        {"trial_consumed": True},
                    ],
                })
                if prior >= 1:
                    deny_free_trial = True
                    logger.info(
                        f"[anti-abuse] Free trial denied for {email} — "
                        f"device {fp[:12]}… already used by {prior} prior trial signup(s)."
                    )
            except Exception:
                logger.exception("device-fingerprint lookup failed (non-fatal)")
                deny_free_trial = False

        # New users start on the 7-day Free Trial (10 labels one-time) UNLESS
        # the device already burned a trial — then they start with no plan
        # (effectively a paywall on the first action).
        trial_spec = await plan_start_payload(db, "free_trial")
        if deny_free_trial:
            plan_for_user = ""
            plan_started  = ""
            plan_expires  = ""
        else:
            plan_for_user = trial_spec["plan"]
            plan_started  = trial_spec["plan_started_at"]
            plan_expires  = trial_spec["plan_expires_at"]

        user_doc = {
            "id": uid,
            "display_id": display_id,
            "email": email,
            "password_hash": hash_password(payload.password),
            "name": payload.name.strip(),
            "shop_name": payload.shop_name.strip(),
            "phone": phone,
            "device_fingerprint":  fp,
            "trial_consumed":      not deny_free_trial,
            "trial_denied_reason": "duplicate_device" if deny_free_trial else "",
            "is_admin": is_first,
            "plan": plan_for_user,
            "plan_started_at": plan_started,
            "plan_expires_at": plan_expires,
            "primary_business_category":    pbc,
            "primary_business_category_at": (now if pbc else ""),
            "created_at": now,
        }
        await db.users.insert_one(user_doc)

        if is_first:
            claimed = await claim_legacy_data_for_admin(db, uid)
            logger.info(f"Admin {email} claimed legacy rows: {claimed}")
        else:
            cid = await seed_default_courier(db, uid)
            # Trial bonus: 50 free credits so new users can try Photo OCR
            # and AI text parsing comfortably before topping up via Wallet.
            try:
                await wallet_add_credits(
                    db, uid, 50.0,
                    ctype="bonus",
                    description="Welcome bonus — 50 free credits to try AI features",
                    order_id=f"signup-bonus-{uid[:8]}",
                )
            except Exception:
                logger.exception("signup bonus credit grant failed (non-fatal)")

        token = make_token(uid, email)
        response = {**user_public(user_doc), **{"token": token}}
        if deny_free_trial:
            response["trial_denied"] = True
            response["trial_denied_reason"] = "duplicate_device"
        return response  # type: ignore

    # ── POST /api/auth/login ────────────────────────────────────────
    @auth_router.post("/login")
    async def auth_login(payload: LoginRequest):
        email = payload.email.lower().strip()
        user = await db.users.find_one({"email": email})
        if not user or not verify_password(payload.password, user.get("password_hash", "")):
            raise HTTPException(status_code=401, detail="Invalid email or password")
        token = make_token(user["id"], email)
        return {**user_public(user), "token": token}

    # ── GET /api/auth/me ────────────────────────────────────────────
    @auth_router.get("/me", response_model=UserPublic)
    async def auth_me(current_user: Dict[str, Any] = Depends(_get_current_user)):
        return user_public(current_user)

    # ── POST /api/auth/contact-email ─────────────────────────────────
    # Phase F8.1 — dedicated OTP contact email. When set, OTP webhook
    # payloads carry this address as `contact_email`; when blank they
    # fall back to the registered login email.
    @auth_router.post("/contact-email", response_model=UserPublic)
    async def auth_set_contact_email(
        payload: ContactEmailRequest,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        val = (payload.contact_email or "").strip().lower()
        if val and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", val):
            raise HTTPException(status_code=400, detail="Enter a valid email address.")
        await db.users.update_one(
            {"id": current_user["id"]},
            {"$set": {"contact_email": val}},
        )
        fresh = await db.users.find_one({"id": current_user["id"]}, {"_id": 0}) or current_user
        return user_public(fresh)

    # ── GET /api/auth/context ───────────────────────────────────────
    @auth_router.get("/context")
    async def auth_context(current_user: Dict[str, Any] = Depends(_get_current_user)):
        """Phase B+C — returns whether the active session is an OWNER or a
        TEAM-MEMBER and, in the latter case, which permission keys are
        granted. The frontend reads this once at app boot and uses it to
        drive UI gating (`<Gated permission="...">` / `usePermission`)."""
        team = current_user.get("_team")
        pbc = (current_user.get("primary_business_category") or "").strip()
        shop_name = (current_user.get("shop_name") or "").strip()
        phone     = (current_user.get("phone") or "").strip()
        needs_profile_completion = (not bool(team)) and bool(
            current_user.get("needs_profile_completion")
        )
        return {
            "is_team_member":  bool(team),
            "team_member":     team if team else None,
            "user": {
                "id":         current_user.get("id"),
                "name":       current_user.get("name"),
                "email":      current_user.get("email"),
                "is_admin":   bool(current_user.get("is_admin")),
                "plan":       current_user.get("plan"),
                "shop_name":  shop_name,
                "phone":      phone,
                "primary_business_category": pbc,
            },
            "needs_onboarding_category": (not bool(team)) and (not pbc),
            "needs_profile_completion": needs_profile_completion,
        }

    # ── POST /api/auth/complete-profile ─────────────────────────────
    @auth_router.post("/complete-profile")
    async def complete_profile(
        payload: CompleteProfileRequest,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        if current_user.get("_team"):
            raise HTTPException(
                status_code=403,
                detail="Team members inherit the owner's profile.",
            )
        from business_categories import is_valid_category
        slug = (payload.primary_business_category or "").strip()
        if not is_valid_category(slug):
            raise HTTPException(
                status_code=400,
                detail="Please pick a valid business category.",
            )
        phone_digits = re.sub(r"\D", "", payload.phone or "")
        if len(phone_digits) < 10 or len(phone_digits) > 13:
            raise HTTPException(
                status_code=400,
                detail="Please enter a valid 10-digit mobile number.",
            )
        phone = phone_digits[-10:]
        shop_name = (payload.shop_name or "").strip()
        if not shop_name:
            raise HTTPException(
                status_code=400,
                detail="Business name is required.",
            )
        now = auth_utcnow_iso()
        await db.users.update_one(
            {"id": current_user["id"]},
            {"$set": {
                "shop_name":                     shop_name,
                "phone":                         phone,
                "primary_business_category":     slug,
                "primary_business_category_at":  now,
                "profile_completed_at":          now,
                "needs_profile_completion":      False,
            }},
        )
        return {
            "ok": True,
            "shop_name": shop_name,
            "phone": phone,
            "primary_business_category": slug,
        }

    # ── GET /api/auth/business-categories ───────────────────────────
    @auth_router.get("/business-categories")
    async def list_business_categories():
        """Public list of selectable categories (slug + label + emoji)."""
        from business_categories import BUSINESS_CATEGORIES
        return {"categories": BUSINESS_CATEGORIES}

    # ── POST /api/auth/business-category ────────────────────────────
    @auth_router.post("/business-category")
    async def set_business_category(
        payload: BusinessCategoryPayload,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Persist the user's primary business category."""
        from business_categories import is_valid_category
        slug = (payload.category or "").strip()
        if not is_valid_category(slug):
            raise HTTPException(
                status_code=400,
                detail="Unknown business category. Use one of /api/auth/business-categories.",
            )
        if current_user.get("_team"):
            raise HTTPException(
                status_code=403,
                detail="Only the account owner can set the primary business category.",
            )
        await db.users.update_one(
            {"id": current_user["id"]},
            {"$set": {
                "primary_business_category":     slug,
                "primary_business_category_at":  auth_utcnow_iso(),
            }},
        )
        return {"ok": True, "category": slug}

    # ── POST /api/auth/logout ───────────────────────────────────────
    @auth_router.post("/logout")
    async def auth_logout():
        # JWT is stateless; the client just drops the token.
        return {"ok": True}

    # ── POST /api/auth/forgot-password/request-otp ───────────────────
    # Phase F8.1 — step 1 of the OTP-verified reset. Validates the
    # email+phone 2-factor gate (same checks as the final reset call)
    # and dispatches a "password_reset" OTP through the configured
    # webhook. The payload carries `contact_email` = the user's
    # registered email so the operator's automation can deliver the
    # code via email as well as WhatsApp.
    @auth_router.post("/forgot-password/request-otp")
    async def auth_forgot_password_request_otp(payload: ForgotPasswordOtpRequest):
        from services.otp_service import (
            CooldownError, LockoutError,
            OTP_RESEND_COOLDOWN_S, OTP_TTL_SECONDS, issue_otp,
        )
        from services.otp_whatsapp import send_otp_via_whatsapp

        email = payload.email.lower().strip()
        phone_digits = re.sub(r"\D", "", (payload.phone or "").strip())
        if len(phone_digits) < 10:
            raise HTTPException(
                status_code=400,
                detail="Please enter your registered 10-digit mobile number.",
            )
        phone = phone_digits[-10:]

        # Same abuse gate as the final reset call.
        failures = await _count_recent_pwd_failures(email)
        if failures >= _PWD_RESET_MAX_ATTEMPTS:
            raise HTTPException(
                status_code=429,
                detail=(
                    "Too many failed attempts. For security, please wait an hour "
                    "and try again, or contact support."
                ),
            )

        user = await db.users.find_one({"email": email})
        if not user:
            await _log_pwd_attempt(email, False, "otp-request: no user")
            raise HTTPException(
                status_code=404,
                detail="No account with that email. Check your spelling or sign up.",
            )
        user_phone_digits = re.sub(r"\D", "", (user.get("phone") or ""))[-10:]
        if not user_phone_digits or user_phone_digits != phone:
            await _log_pwd_attempt(email, False, "otp-request: phone mismatch")
            raise HTTPException(
                status_code=400,
                detail=(
                    "The details don't match our records. Double-check your "
                    "registered mobile number and try again."
                ),
            )

        try:
            code, normalised_phone = await issue_otp(db, phone, "password_reset")
        except (CooldownError, LockoutError) as ce:
            raise HTTPException(status_code=429, detail=str(ce))
        except Exception:
            _LOG.exception("forgot-password OTP issue failed")
            raise HTTPException(status_code=500, detail="Could not send OTP")

        contact_email = (
            (user.get("contact_email") or "").strip()
            or (user.get("email") or "").strip()
        )
        await send_otp_via_whatsapp(
            phone=normalised_phone,
            otp=code,
            event_type="password_reset",
            db=db,
            user_name=(user.get("name") or "").strip(),
            contact_email=contact_email,
        )
        return {
            "ok":              True,
            "expires_in":      OTP_TTL_SECONDS,
            "resend_cooldown": OTP_RESEND_COOLDOWN_S,
        }

    # ── POST /api/auth/forgot-password ──────────────────────────────
    @auth_router.post("/forgot-password")
    async def auth_forgot_password(payload: ForgotPasswordRequest):
        """Reset password: registered email + phone gate + Phase F8.1
        OTP verification (event_type "password_reset")."""
        email = payload.email.lower().strip()
        phone_raw = (payload.phone or "").strip()
        phone_digits = re.sub(r"\D", "", phone_raw)
        if len(phone_digits) < 10:
            raise HTTPException(
                status_code=400,
                detail="Please enter your registered 10-digit mobile number.",
            )
        phone = phone_digits[-10:]

        # Rate limit first — before revealing anything.
        failures = await _count_recent_pwd_failures(email)
        if failures >= _PWD_RESET_MAX_ATTEMPTS:
            raise HTTPException(
                status_code=429,
                detail=(
                    "Too many failed attempts. For security, please wait an hour "
                    "and try again, or contact support."
                ),
            )

        user = await db.users.find_one({"email": email})
        if not user:
            await _log_pwd_attempt(email, False, "no user")
            raise HTTPException(
                status_code=404,
                detail="No account with that email. Check your spelling or sign up.",
            )

        user_phone = (user.get("phone") or "").strip()
        user_phone_digits = re.sub(r"\D", "", user_phone)[-10:] if user_phone else ""
        if not user_phone_digits:
            await _log_pwd_attempt(email, False, "legacy user, no phone on file")
            raise HTTPException(
                status_code=400,
                detail=(
                    "This account was created before we started storing phone "
                    "numbers. Please contact support to reset your password."
                ),
            )
        if user_phone_digits != phone:
            await _log_pwd_attempt(email, False, "phone mismatch")
            raise HTTPException(
                status_code=400,
                detail=(
                    "The details don't match our records. Double-check your "
                    "registered mobile number and try again."
                ),
            )

        # ── Phase F8.1 — 3rd factor: OTP verification. ────────────
        # The code was issued by /forgot-password/request-otp with
        # purpose "password_reset" and is bound to this phone.
        otp_code = re.sub(r"\D", "", (payload.otp or "").strip())
        if not otp_code:
            raise HTTPException(
                status_code=400,
                detail="Enter the OTP sent to your registered contact.",
            )
        from services.otp_service import (
            LockoutError as _OtpLockout, verify_otp as _verify_otp,
        )
        try:
            otp_ok, otp_reason = await _verify_otp(db, phone, otp_code, "password_reset")
        except _OtpLockout as le:
            await _log_pwd_attempt(email, False, "otp lockout")
            raise HTTPException(status_code=429, detail=str(le))
        if not otp_ok:
            await _log_pwd_attempt(email, False, f"otp {otp_reason}")
            raise HTTPException(
                status_code=400,
                detail=(
                    "The OTP is incorrect or has expired. Request a new one "
                    "and try again."
                ),
            )

        await db.users.update_one(
            {"id": user["id"]},
            {"$set": {
                "password_hash":       hash_password(payload.new_password),
                "password_changed_at": datetime.utcnow().isoformat() + "+00:00",
            }},
        )
        await _log_pwd_attempt(email, True, "self-reset via phone+otp")
        token = make_token(user["id"], email)
        fresh = await db.users.find_one({"id": user["id"]}, {"_id": 0}) or user
        return {**user_public(fresh), "token": token}

    # ── POST /api/auth/google/session ───────────────────────────────
    @auth_router.post("/google/session")
    async def auth_google_session(payload: GoogleSessionRequest):
        if not payload.session_id or len(payload.session_id) < 8:
            raise HTTPException(status_code=400, detail="Missing session_id")
        # 1. Exchange session_id → user profile via Emergent Auth.
        async with httpx.AsyncClient(timeout=15) as cli:
            try:
                r = await cli.get(
                    "https://demobackend.emergentagent.com/auth/v1/env/oauth/session-data",
                    headers={"X-Session-ID": payload.session_id},
                )
            except Exception as e:
                raise HTTPException(status_code=502, detail=f"Emergent Auth unreachable: {e}")
        if r.status_code != 200:
            raise HTTPException(
                status_code=401,
                detail=f"Google session rejected (status {r.status_code})",
            )
        try:
            prof = r.json()
        except Exception:
            raise HTTPException(status_code=502, detail="Invalid response from Emergent Auth")
        email = (prof.get("email") or "").strip().lower()
        if not email:
            raise HTTPException(status_code=400, detail="Google profile missing email")

        # 2. Find-or-create the user. Email is the unique key.
        user = await db.users.find_one({"email": email})
        now = auth_utcnow_iso()
        if user is None:
            is_first = (await db.users.count_documents({})) == 0
            uid = str(uuid.uuid4())
            trial_spec = await plan_start_payload(db, "free_trial")
            user_doc = {
                "id": uid,
                "email": email,
                # Social users have no password; the email/password
                # login endpoint will reject this account (empty hash
                # → verify_password = False).
                "password_hash": "",
                "name": prof.get("name") or email.split("@")[0],
                "shop_name": "",
                "picture": prof.get("picture") or "",
                "auth_provider": "google",
                # Phase G2 (rev-2) — Google only gives us email + name.
                # Mark the user as needing the post-signup "Complete your
                # profile" gate so they fill in Business Name / Mobile /
                # Category before the dashboard unlocks. Cleared by
                # /auth/complete-profile.
                "needs_profile_completion": True,
                "is_admin": is_first,
                "plan": trial_spec["plan"],
                "plan_started_at": trial_spec["plan_started_at"],
                "plan_expires_at": trial_spec["plan_expires_at"],
                "created_at": now,
            }
            await db.users.insert_one(user_doc)
            if is_first:
                claimed = await claim_legacy_data_for_admin(db, uid)
                logger.info(f"Google-admin {email} claimed legacy rows: {claimed}")
            else:
                cid = await seed_default_courier(db, uid)
                try:
                    await wallet_add_credits(
                        db, uid, 50.0,
                        ctype="bonus",
                        description="Welcome bonus — 50 free credits to try AI features",
                        order_id=f"google-bonus-{uid[:8]}",
                    )
                except Exception:
                    logger.exception("google signup bonus failed (non-fatal)")
            user = user_doc
        else:
            # Ensure the existing user is marked as Google-linked.
            update: Dict[str, Any] = {}
            if not user.get("auth_provider"):
                update["auth_provider"] = "google"
            if prof.get("picture") and prof.get("picture") != user.get("picture"):
                update["picture"] = prof["picture"]
            if prof.get("name") and not user.get("name"):
                update["name"] = prof["name"]
            if update:
                await db.users.update_one({"id": user["id"]}, {"$set": update})
                user.update(update)

        token = make_token(user["id"], email)
        return {**user_public(user), "token": token}


__all__ = ["auth_router", "init"]
