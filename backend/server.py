from fastapi import FastAPI, APIRouter, HTTPException, Depends, Request, BackgroundTasks, Body, Query
from fastapi.responses import PlainTextResponse
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReturnDocument

# CRITICAL: load .env BEFORE importing any module that reads
# os.environ at module-load time (auth.py reads JWT_SECRET).
# Otherwise the JWT secret would be regenerated on every restart and
# all sessions would be invalidated.
load_dotenv()

# Phase-1 auth (email+password, JWT, per-user data isolation)
from auth import (
    SignupRequest, LoginRequest, UserPublic,
    ForgotPasswordRequest, CompleteProfileRequest,
    hash_password, verify_password, make_token, user_public,
    get_current_user_factory, utcnow_iso as auth_utcnow_iso,
    seed_demo_shipments, seed_default_courier, claim_legacy_data_for_admin,
)
# Phase-3a subscription plans + usage enforcement
from plans import (
    PLANS as PLAN_TABLE,
    public_plan_list,
    plan_start_payload,
    ensure_can_create_label,
    bump_label_usage,
    usage_summary,
    plan_room_status,
)
# Phase-4a credit wallet
from wallet import (
    ensure_wallet as wallet_ensure,
    get_balance as wallet_balance,
    list_history as wallet_list_history,
    require_balance as wallet_require,
    charge_for_label as wallet_charge,
    add_credits as wallet_add_credits,
    classify_and_cost as wallet_classify_and_cost,
    LabelCostBreakdown,
    compute_label_cost,
)
# Phase-4b+ Smart Paste AI
from smart_paste_ai import (
    parse_paste_via_llm,
    parse_image_with_ai,
    to_legacy_fields as sm_to_legacy_fields,
    DEFAULT_SHIPBOT_PROMPT,
)
from pincode_lookup import enrich_with_pincode, validate_pincode_consistency, resolve_city, resolve_pincode
from contact_settings import (
    ContactSaveSettings,
    default_settings as contact_default_settings,
    build_contact as contact_build,
    to_vcard as contact_to_vcard,
)
from fastapi import Depends as _AuthDepends  # noqa: F401
import os
import io
import csv
import re
import json
import logging
import httpx
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, Iterable
import uuid
from datetime import datetime, timezone, timedelta
from dateutil.relativedelta import relativedelta

# Coupon system (2026-04-30)
from coupons import (
    CouponCreate, CouponUpdate,
    coupon_to_api, validate_coupon, new_coupon_doc, ensure_code_valid,
)

# Google Sheets writer (Service Account)
try:
    from sheet_writer import append_order_row as sheet_append_order_row
    from sheet_writer import append_order_row_to_user_sheet as sheet_append_user
    from sheet_writer import sync_master_to_user_sheet as sheet_sync_master_to_user
    from sheet_writer import probe_connection as sheet_probe_connection
    from sheet_writer import mark_row_deleted as sheet_mark_row_deleted
    from sheet_writer import parse_row_from_updated_range as sheet_parse_row_from_updated_range
    from sheet_writer import update_row_status as sheet_update_row_status
    from sheet_writer import get_service_account_email as sheet_get_sa_email
    from sheet_writer import read_user_sheet as sheet_read_user_sheet
    from sheet_writer import sync_user_sheet_headers as sheet_sync_user_sheet_headers
    from sheet_writer import write_row_cells_to_user_sheet as sheet_write_row_cells_to_user_sheet
    from sheet_writer import append_row_cells_to_user_sheet as sheet_append_row_cells_to_user_sheet
except Exception as _sheet_import_err:  # pragma: no cover
    sheet_append_order_row = None  # type: ignore
    sheet_append_user = None  # type: ignore
    sheet_sync_master_to_user = None  # type: ignore
    sheet_probe_connection = None  # type: ignore
    sheet_mark_row_deleted = None  # type: ignore
    sheet_parse_row_from_updated_range = None  # type: ignore
    sheet_update_row_status = None  # type: ignore
    sheet_get_sa_email = None  # type: ignore
    sheet_read_user_sheet = None  # type: ignore
    sheet_sync_user_sheet_headers = None  # type: ignore
    sheet_write_row_cells_to_user_sheet = None  # type: ignore
    sheet_append_row_cells_to_user_sheet = None  # type: ignore


ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# FastAPI app + routers must be declared BEFORE endpoint decorators
# (auth_router is referenced by the @auth_router.post decorators below).
app = FastAPI()
api_router = APIRouter(prefix="/api")
auth_router = APIRouter(prefix="/api/auth", tags=["auth"])


# ------------- Helper: human-readable user display ID ---------------
# Every user gets a short, sortable identifier like "USR-00042" that
# admins can quote over the phone without reading a UUID aloud.
# Backed by an auto-incrementing counter in db.counters.

async def _next_display_id() -> str:
    """Return the next USR-XXXXX string atomically."""
    doc = await db.counters.find_one_and_update(
        {"_id": "user_display_id"},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=True,
    )
    seq = int((doc or {}).get("seq") or 1)
    return f"USR-{seq:05d}"


async def _backfill_display_ids():
    """Assign display_id to any users created before Phase-4d. Idempotent."""
    missing = await db.users.count_documents({"display_id": {"$in": [None, ""]}})
    if missing == 0:
        return 0
    cursor = db.users.find(
        {"display_id": {"$in": [None, ""]}}, {"_id": 0, "id": 1, "created_at": 1},
    ).sort("created_at", 1)
    async for u in cursor:
        did = await _next_display_id()
        await db.users.update_one({"id": u["id"]}, {"$set": {"display_id": did}})
    return missing


# ─────────────────────────────────────────────────────────────────────────
# Phase-7d Master Order ID
# ─────────────────────────────────────────────────────────────────────────
# Format: YYMMDD + zero-padded global sequence (min 5 digits, can grow).
# The sequence is GLOBAL across ALL users and NEVER resets — even when
# the date rolls over. Sequence is stored atomically in `db.counters`
# under `_id="master_order_id"`.
#
# Examples:
#   2026-04-29 + seq=1      → "2604290001"
#   2026-04-29 + seq=42     → "2604290042"
#   2026-04-29 + seq=99999  → "2604299999"
#   2026-04-29 + seq=100000 → "260429100000"  (auto-grows past 5 digits)
#   2026-04-30 + seq=100001 → "260430100001"  (still continues)
async def generate_master_order_id() -> str:
    """Atomically allocate the next Master Order ID. Caller must persist."""
    doc = await db.counters.find_one_and_update(
        {"_id": "master_order_id"},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=True,
    )
    seq = int((doc or {}).get("seq") or 1)
    seq_str = str(seq).zfill(5)  # 5-digit min, auto-grows
    # IST date — the app's customers are in India, so the YYMMDD prefix
    # must reflect the local calendar day. (UTC would roll over at
    # 5:30 AM IST and confuse users in the early-morning hours.)
    ist_now = datetime.utcnow() + timedelta(hours=5, minutes=30)
    yymmdd = ist_now.strftime("%y%m%d")
    return f"{yymmdd}{seq_str}"


async def peek_next_master_order_id() -> str:
    """Read the counter WITHOUT incrementing — returns the ID that the
    NEXT generate_master_order_id() call will produce. Used for live
    preview in the New Shipment form so we don't waste sequences when
    the user opens the form but doesn't save.

    Note: there's an inherent TOCTOU window — by the time the user hits
    Save another shipment may have been created. The actual saved ID
    is always whatever generate_master_order_id() returns at save time.
    """
    doc = await db.counters.find_one({"_id": "master_order_id"})
    seq = int((doc or {}).get("seq", 0)) + 1
    seq_str = str(seq).zfill(5)
    ist_now = datetime.utcnow() + timedelta(hours=5, minutes=30)
    yymmdd = ist_now.strftime("%y%m%d")
    return f"{yymmdd}{seq_str}"


def _user_q(user: Dict[str, Any], extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return a Mongo filter scoped to this user's data. Prevents users
    from reading/writing each other's shipments/couriers/settings."""
    q = {"user_id": user["id"]}
    if extra:
        q.update(extra)
    return q


mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# Bind the auth dependency now that `db` exists.
get_current_user = get_current_user_factory(db)


async def _legacy_with_pincode_enrich(schema):
    """Run pincode → state/district enrichment on the SCHEMA-key dict
    (PINCODE/STATE/CITY) before converting to legacy keys (pincode/
    state/city). Used by every Smart Paste path so the user always
    gets state/district auto-filled when a valid pincode is present.

    Returns just the legacy fields dict — same as before. For
    pincode-mismatch warnings use `_legacy_with_pincode_enrich_v2`.
    """
    try:
        await enrich_with_pincode(db, schema)
    except Exception:
        pass
    return sm_to_legacy_fields(schema)


async def _legacy_with_pincode_enrich_v2(schema):
    """Like `_legacy_with_pincode_enrich`, but ALSO collects pincode-vs-
    address consistency warnings via `validate_pincode_consistency`.
    Returns (legacy_fields, warnings).
    """
    warnings: List[str] = []
    try:
        await enrich_with_pincode(db, schema)
    except Exception:
        pass
    try:
        warnings = await validate_pincode_consistency(db, schema)
    except Exception:
        # Validation must never break smart-paste.
        warnings = []
    return sm_to_legacy_fields(schema), warnings


# --- Auth endpoints ---------------------------------------------------

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
    display_id = await _next_display_id()

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
                # only existing free-trial / former-trial accounts count
                # — paying customers signing up a 2nd account on their
                # device should still get the trial.
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
        # No trial: empty plan + no expiry. The plan-gate middleware
        # treats this as "needs to subscribe" and shows upgrade prompts.
        plan_for_user = ""
        plan_started = ""
        plan_expires = ""
    else:
        plan_for_user = trial_spec["plan"]
        plan_started = trial_spec["plan_started_at"]
        plan_expires = trial_spec["plan_expires_at"]

    user_doc = {
        "id": uid,
        "display_id": display_id,
        "email": email,
        "password_hash": hash_password(payload.password),
        "name": payload.name.strip(),
        "shop_name": payload.shop_name.strip(),
        "phone": phone,
        "device_fingerprint": fp,         # store for future checks
        "trial_consumed": not deny_free_trial,  # mark trial as used iff granted
        "trial_denied_reason": "duplicate_device" if deny_free_trial else "",
        "is_admin": is_first,
        "plan": plan_for_user,
        "plan_started_at": plan_started,
        "plan_expires_at": plan_expires,
        # Phase G — primary business category collected on the signup form.
        # `pbc` is the validated slug (or "" if the older client omitted it).
        # Persisted alongside a timestamp so analytics can compute "users
        # who picked a category in the last N days" without joining audit logs.
        "primary_business_category":    pbc,
        "primary_business_category_at": (now if pbc else ""),
        "created_at": now,
    }
    await db.users.insert_one(user_doc)

    if is_first:
        # Developer/admin account — inherits the legacy rows so nothing is orphaned.
        claimed = await claim_legacy_data_for_admin(db, uid)
        logger.info(f"Admin {email} claimed legacy rows: {claimed}")
    else:
        # Fresh user — seed starter courier only (no more demo shipments;
        # 2026-05-25: removed the 15 sample rows so production users see
        # a clean inbox from day 1).
        cid = await seed_default_courier(db, uid)
        # Trial bonus: 50 free credits so new users can try Photo OCR
        # and AI text parsing comfortably before topping up via Wallet.
        # 2026-05-25: bumped from 10 → 50 for a more generous starter
        # experience.
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
    out = user_public(user_doc)
    out["_token"] = token  # stashed for the /signup response
    response = {**user_public(user_doc), **{"token": token}}
    if deny_free_trial:
        # Frontend uses this to show a friendly "device already used a
        # trial — please subscribe to continue" notice on first login.
        response["trial_denied"] = True
        response["trial_denied_reason"] = "duplicate_device"
    return response  # type: ignore


@auth_router.post("/login")
async def auth_login(payload: LoginRequest):
    email = payload.email.lower().strip()
    user = await db.users.find_one({"email": email})
    if not user or not verify_password(payload.password, user.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = make_token(user["id"], email)
    return {**user_public(user), "token": token}


@auth_router.get("/me", response_model=UserPublic)
async def auth_me(current_user: Dict[str, Any] = Depends(get_current_user)):
    return user_public(current_user)


@auth_router.get("/context")
async def auth_context(current_user: Dict[str, Any] = Depends(get_current_user)):
    """Phase B+C — returns whether the active session is an OWNER or a
    TEAM-MEMBER and, in the latter case, which permission keys are
    granted. The frontend reads this once at app boot and uses it to
    drive UI gating (`<Gated permission="...">` / `usePermission`)."""
    team = current_user.get("_team")
    # Phase G — surface primary_business_category so the auth gate can
    # redirect new users to /onboarding/business-category before they
    # land on the dashboard. Empty string when not set.
    pbc = (current_user.get("primary_business_category") or "").strip()
    shop_name = (current_user.get("shop_name") or "").strip()
    phone     = (current_user.get("phone") or "").strip()
    # Phase G2 (rev-2) — `needs_profile_completion` is now a STRICT
    # opt-in flag set only when a brand-new Google-OAuth user is
    # created (auth_provider="google" + missing the data Google
    # doesn't provide). It is NOT computed from empty fields, because
    # legacy / demo accounts created before Phase G2 don't have those
    # fields populated and shouldn't be re-interrogated on every login.
    # Once the user submits /auth/complete-profile the flag flips to
    # False and stays there forever — re-logins will never re-trigger
    # the gate. Form-based email/password signups (`/auth/signup`)
    # already collect every mandatory field up-front, so the flag is
    # always False for that path.
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
        # Convenience flag for the splash gate so it doesn't have to
        # reproduce the same emptiness check on every render.
        "needs_onboarding_category": (not bool(team)) and (not pbc),
        # Phase G2 — owner is missing one or more of the mandatory
        # post-Google-signup fields → frontend redirects to
        # /(auth)/complete-profile instead of the dashboard.
        "needs_profile_completion": needs_profile_completion,
    }


# Phase G2 — "Complete your profile" — used by the post-Google-signup
# gate to capture the data Google doesn't provide. POST is idempotent;
# it can be re-called to fix typos before the user is unblocked.
@auth_router.post("/complete-profile")
async def complete_profile(
    payload: CompleteProfileRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
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
    # Normalise the phone the same way /signup does — strip non-digits,
    # require 10–13 digits, store the last 10 (drops country-code
    # prefixes for forgot-password lookup parity).
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
    now = utcnow_iso()
    await db.users.update_one(
        {"id": current_user["id"]},
        {"$set": {
            "shop_name":                     shop_name,
            "phone":                         phone,
            "primary_business_category":     slug,
            "primary_business_category_at":  now,
            "profile_completed_at":          now,
            # Phase G2 (rev-2) — turn the post-Google-signup gate off
            # so re-logins land directly on the dashboard. The flag
            # was set at /auth/google/session for new Google users
            # only; once cleared here it's never re-raised.
            "needs_profile_completion":      False,
        }},
    )
    return {
        "ok": True,
        "shop_name": shop_name,
        "phone": phone,
        "primary_business_category": slug,
    }


# Phase G — "What do you sell?" onboarding category. Lives next to
# /auth/context above so the gate logic + the writer share a single
# review surface. POST is idempotent: re-posting the same slug is a
# no-op; posting a different slug overwrites (so the user can change
# their mind from Settings later).
class BusinessCategoryPayload(BaseModel):
    category: str


@auth_router.get("/business-categories")
async def list_business_categories():
    """Public list of selectable categories (slug + label + emoji) so
    the frontend onboarding screen always renders the latest list
    without bundling a stale duplicate."""
    from business_categories import BUSINESS_CATEGORIES
    return {"categories": BUSINESS_CATEGORIES}


@auth_router.post("/business-category")
async def set_business_category(
    payload: BusinessCategoryPayload,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Persist the user's primary business category. Validated against
    the canonical slug list so analytics aggregations (top sellers,
    growth, shipment volume) can JOIN on the slug without worrying
    about free-text noise."""
    from business_categories import is_valid_category
    slug = (payload.category or "").strip()
    if not is_valid_category(slug):
        raise HTTPException(
            status_code=400,
            detail="Unknown business category. Use one of /api/auth/business-categories.",
        )
    # Team-member sessions inherit the owner's category — they can't
    # set their own. Block the write so analytics stay clean.
    if current_user.get("_team"):
        raise HTTPException(
            status_code=403,
            detail="Only the account owner can set the primary business category.",
        )
    await db.users.update_one(
        {"id": current_user["id"]},
        {"$set": {
            "primary_business_category":     slug,
            "primary_business_category_at":  utcnow_iso(),
        }},
    )
    return {"ok": True, "category": slug}


@auth_router.post("/logout")
async def auth_logout():
    # JWT is stateless; the client just drops the token. This endpoint
    # exists so the frontend has something consistent to call (e.g. for
    # analytics or future server-side revocation lists).
    return {"ok": True}


# ────────── Forgot-password (no-OTP self-service) ──────────
#
# Since we don't have SMTP/SMS infra yet, we gate the reset with TWO
# factors the user must know: their email AND their registered mobile
# number. An attacker would need BOTH pieces to forge a reset — which
# is rare in practice for small-business SaaS. To keep this honest:
#
#   1. Rate-limit to 3 failed attempts per email per hour.
#   2. Log every attempt (success or failure) for audit.
#   3. Last-10-digit normalisation so "+91" prefixes don't trip users.
#
# When SMTP/SMS infra is added later, wrap this behind an OTP step
# without breaking the API shape.

_PWD_RESET_MAX_ATTEMPTS = 3
_PWD_RESET_WINDOW_SEC = 3600  # 1 hour


async def _count_recent_pwd_failures(email: str) -> int:
    cutoff = datetime.utcnow() - timedelta(seconds=_PWD_RESET_WINDOW_SEC)
    return await db.pwd_reset_attempts.count_documents({
        "email": email,
        "ok": False,
        "at": {"$gte": cutoff.isoformat() + "+00:00"},
    })


async def _log_pwd_attempt(email: str, ok: bool, reason: str = ""):
    await db.pwd_reset_attempts.insert_one({
        "email": email,
        "ok": bool(ok),
        "reason": reason[:120] if reason else "",
        "at": datetime.utcnow().isoformat() + "+00:00",
    })


@auth_router.post("/forgot-password")
async def auth_forgot_password(payload: ForgotPasswordRequest):
    """Reset password using registered email + phone as a 2-factor gate."""
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
    # Legacy users signed up before phone was required — tell them to
    # contact support (can't self-reset without phone on file).
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
        # Don't hint which field is wrong — just fail generically.
        raise HTTPException(
            status_code=400,
            detail=(
                "The details don't match our records. Double-check your "
                "registered mobile number and try again."
            ),
        )

    # Identity confirmed. Set the new password + issue a fresh token.
    await db.users.update_one(
        {"id": user["id"]},
        {"$set": {
            "password_hash": hash_password(payload.new_password),
            "password_changed_at": datetime.utcnow().isoformat() + "+00:00",
        }},
    )
    await _log_pwd_attempt(email, True, "self-reset via phone")
    token = make_token(user["id"], email)
    fresh = await db.users.find_one({"id": user["id"]}, {"_id": 0}) or user
    return {**user_public(fresh), "token": token}


# ────────── Admin-initiated password reset ──────────
# Note: AdminResetPasswordRequest model + the actual handler now live in
# /app/backend/routers/admin.py (Phase-2 incremental refactor). Kept
# this comment as a breadcrumb for future code-archaeologists.


# --- Google OAuth (Emergent hosted) -----------------------------------
#
# Flow (web-only via Emergent Auth):
#   1. Frontend redirects to https://auth.emergentagent.com/?redirect=<origin>/
#   2. After Google consent user is sent back to <origin>/#session_id=XXXX
#   3. Frontend extracts session_id and POSTs it here.
#   4. We exchange it server-side against Emergent's /session-data endpoint
#      (NEVER call that from the browser — it leaks the session_token).
#   5. If the email is new → we create a user, seed demo data (or claim legacy
#      rows if they're the very first signup). If it exists → we log them in.
#   6. We respond with the same JWT shape as /auth/login so the client can
#      stash it identically.
#
# REMINDER: DO NOT HARDCODE THE URL, OR ADD ANY FALLBACKS OR REDIRECT URLS,
# THIS BREAKS THE AUTH — the redirect origin is derived from window.location
# on the client (see login.tsx).
class GoogleSessionRequest(BaseModel):
    session_id: str


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
            # Social users have no password; the email/password login endpoint
            # will reject this account (empty hash → verify_password = False).
            "password_hash": "",
            "name": prof.get("name") or email.split("@")[0],
            "shop_name": "",
            "picture": prof.get("picture") or "",
            "auth_provider": "google",
            # Phase G2 (rev-2) — Google only gives us email + name.
            # Mark the user as needing the post-signup "Complete your
            # profile" gate so they fill in Business Name / Mobile /
            # Category before the dashboard unlocks. Cleared by
            # /auth/complete-profile. Existing users that log in via
            # Google a second time DO NOT pass through this branch
            # (they're matched by email above), so the flag is never
            # re-raised once they've completed it.
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
            # 2026-05-25 — Removed seed_demo_shipments(); new accounts
            # now start with a clean shipments inbox.
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
        # Ensure the existing user is marked as Google-linked (useful later).
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


# --- Demo data clear (per-user) ---------------------------------------



# --------------------------------------------------------------------
# (app + router setup moved to top of file for decorator availability)
# --------------------------------------------------------------------


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------- Models ----------------------

class Courier(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    series_prefix: str = ""
    next_number: int = 1
    number_padding: int = 4
    contact_phone: str = ""
    contact_email: str = ""
    website_url: str = ""
    tracking_url_template: str = ""   # e.g. "https://nandan.com/track?id={tracking_id}"
    customer_id: str = ""             # e.g. India Post customer ID printed under courier name on label
    notes: str = ""
    # Phase-4d: per-courier tracking-ID format validation. Used by
    # scanner (reject garbled reads) and manual-entry (inline error).
    tracking_id_prefix: str = ""      # e.g. "EG" for India Post Speed Post
    tracking_id_suffix: str = ""      # e.g. "IN" for India Post
    tracking_id_length: int = 0       # exact length incl. prefix+suffix, 0 = no check
    tracking_id_min_length: int = 0   # lower bound if exact length unknown
    tracking_id_max_length: int = 0   # upper bound
    tracking_id_pattern: str = ""     # optional regex (advanced users)
    # Phase-23 (2026-05-17) — Manual tracking workflow.
    # When True, the sequential `series_prefix + next_number` autogen
    # is skipped for this courier. Tracking ID is captured manually
    # (typed/scanned from the physical sticker) — required for India
    # Post Speed Post, Anjani Courier physical stickers etc. where the
    # AWB on the printed label is NOT a sequential per-shop counter.
    #
    # Defaults to False so existing shops & DB rows behave identically
    # to before; this is a purely opt-in flag per courier.
    manual_tracking: bool = False
    created_at: str = Field(default_factory=utcnow_iso)


class CourierCreate(BaseModel):
    name: str
    series_prefix: Optional[str] = ""
    next_number: Optional[int] = 1
    number_padding: Optional[int] = 4
    contact_phone: Optional[str] = ""
    contact_email: Optional[str] = ""
    website_url: Optional[str] = ""
    tracking_url_template: Optional[str] = ""
    customer_id: Optional[str] = ""
    notes: Optional[str] = ""
    tracking_id_prefix: Optional[str] = ""
    tracking_id_suffix: Optional[str] = ""
    tracking_id_length: Optional[int] = 0
    tracking_id_min_length: Optional[int] = 0
    tracking_id_max_length: Optional[int] = 0
    tracking_id_pattern: Optional[str] = ""
    # Phase-23 — Manual tracking workflow (opt-in, default False).
    manual_tracking: Optional[bool] = False


class CourierUpdate(BaseModel):
    name: Optional[str] = None
    series_prefix: Optional[str] = None
    next_number: Optional[int] = None
    number_padding: Optional[int] = None
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None
    website_url: Optional[str] = None
    tracking_url_template: Optional[str] = None
    customer_id: Optional[str] = None
    notes: Optional[str] = None
    tracking_id_prefix: Optional[str] = None
    tracking_id_suffix: Optional[str] = None
    tracking_id_length: Optional[int] = None
    tracking_id_min_length: Optional[int] = None
    tracking_id_max_length: Optional[int] = None
    tracking_id_pattern: Optional[str] = None
    manual_tracking: Optional[bool] = None


class SenderAddress(BaseModel):
    name: str = ""
    phone: str = ""
    address_line1: str = ""
    address_line2: str = ""
    city: str = ""
    state: str = ""
    pincode: str = ""
    show_contact: bool = True


class SheetConfig(BaseModel):
    url: str = ""
    sheet_id: str = ""
    gid: str = ""
    tab_name: str = ""
    headers: List[str] = Field(default_factory=list)
    column_mapping: Dict[str, str] = Field(default_factory=dict)
    auto_refresh_minutes: int = 0   # 0 = disabled
    # Phase H — auto-sync toggles. When ON, every create / status
    # change / delete in the app fires a best-effort write to the
    # user's PERSONAL sheet (separate from the central Master Sheet).
    auto_sync_create: bool = True   # write new rows on POST /shipments
    auto_sync_status: bool = True   # update the row when status changes
    auto_sync_delete: bool = True   # tombstone the row when a shipment is removed


class BrandConfig(BaseModel):
    name: str = ""          # e.g. "Mahek Creations"
    logo_base64: str = ""   # optional: data uri or base64 string for label top


class LabelFields(BaseModel):
    """Toggles for optional fields shown on the printed label."""
    oid: bool = True
    dispatch_date: bool = True
    weight: bool = True
    item: bool = True
    phone: bool = True
    alt_phone: bool = False  # secondary/alternative phone (off by default)
    customer_id: bool = True
    token_info: bool = False
    box_dimensions: bool = False
    shipment_notes: bool = False


# ---------------------------------------------------------------------------
# Custom user-defined fields printed on the label.
#
# Positions available on the label canvas:
#   "header_top"     → tiny row above the brand block (e.g. GST No, FSSAI)
#   "from_block"     → inside the sender (from) footer block
#   "to_block"       → inside the receiver (deliver-to) block, bottom
#   "meta_row"       → next to Wt / Box / Item line
#   "notes_area"     → below the deliver-to block, styled like shipment notes
#   "footer_bottom"  → last line, above the barcode strip
# ---------------------------------------------------------------------------
class CustomLabelField(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    label: str = ""             # e.g. "GST:"  (printed as bold prefix)
    value: str = ""             # static value to print (ignored if source="shipment")
    position: str = "meta_row"  # one of the positions above
    enabled: bool = True
    bold: bool = True           # value in bold?
    size: str = "sm"            # "xs" | "sm" | "md"
    # "static"  = same value for every label (e.g. GST No.)
    # "shipment"= per-order value; user types it in the New Shipment form
    #             (optionally auto-filled from a Google Sheet column).
    source: str = "static"
    # Optional: for "shipment"-sourced fields, map to a Google Sheet column
    # header. Smart Paste will populate shipment.custom_values[id] from it.
    sheet_column: str = ""
    # Placeholder shown in the Add Shipment input when source="shipment"
    placeholder: str = ""


# Phase-8: Per-field "Required" toggles. Same dict shape used in
# Settings.field_requirements + sent verbatim to the frontend so
# Smart Paste & New Shipment forms can dynamically validate.
# Built-in fields use the snake_case shipment schema keys; user
# Custom Fields use their `user_custom_fields.id` and live in their
# own `required` column on the doc (NOT in this dict).
DEFAULT_FIELD_REQUIREMENTS: Dict[str, bool] = {
    "customer_name":      True,
    "customer_phone":     True,
    "customer_alt_phone": False,
    "address_line1":      True,
    "city":               True,
    "state":              True,
    "pincode":            True,
    "items":              False,
    "amount":             True,
    "payment_mode":       True,
    "token_amount":       False,  # conditional — required only when COD
    "courier_name":       False,
    "order_id":           False,
    "weight":             True,
    "notes":              False,
}


class Settings(BaseModel):
    id: str = "default"
    sender: SenderAddress = Field(default_factory=SenderAddress)
    brand: BrandConfig = Field(default_factory=BrandConfig)
    whatsapp_template: str = (
        "નમસ્તે {customer_name}, તમારું પાર્સલ {courier} દ્વારા મોકલાયું છે. "
        "Tracking ID: {tracking_id}\nTrack here: {tracking_url}\n"
        "અપેક્ષિત ડિલિવરી: {eta_days} દિવસ."
    )
    copy_template: str = (
        "Hi {customer_name}, your order #{order_id} has been shipped via {courier}. "
        "Tracking ID: {tracking_id}. Track here: {tracking_url}"
    )
    default_eta_days: int = 7
    prefer_logo: bool = True  # true = show logo if uploaded; false = always show brand name
    logo_shape: str = "square"  # "square" | "wide"
    shipment_tagline: str = ""  # Default tagline/notice for all shipments (e.g. "Har Pal Prakruti ke Sang"). Used if per-order shipment_notes is empty.
    sheet: SheetConfig = Field(default_factory=SheetConfig)
    label_fields: LabelFields = Field(default_factory=LabelFields)
    custom_fields: List[CustomLabelField] = Field(default_factory=list)
    # Phase-4b+: per-user customisation of the Smart Paste AI parser.
    # Kept OPTIONAL so existing users aren't forced to set it — if empty,
    # we fall back to the bundled DEFAULT_SHIPBOT_PROMPT.
    smart_paste_instructions: str = ""
    smart_paste_ai_enabled: bool = True
    # Phase-4b+ tunable AI address-processing charges per complexity
    # bucket. Spec default: simple 0.5 · medium 1 · complex 2 (max cap 2).
    ai_cost_simple: float = 0.5
    ai_cost_medium: float = 1.0
    ai_cost_complex: float = 2.0
    # Phase-7d: Order ID auto-generation. When ON, the server stamps every
    # new pending order with a Master Order ID (YYMMDD + global 5-digit
    # sequence). User Order ID stays optional — if blank, master is copied.
    # When OFF, the user MUST provide their own order ID at save time.
    order_id_auto_generate: bool = True
    # Phase-7e: When auto-generate is ON, also auto-fill the Order ID
    # in the New Shipment form (manual entry) so the user doesn't have
    # to type it. Users with their own custom numbering can turn this
    # OFF independently.
    order_id_autofill_in_new_shipment: bool = True

    # Phase-8: Per-field "Required" toggles. Maps the canonical field
    # key (e.g. "customer_name", "customer_phone", "weight") to a
    # boolean — true means the Smart Paste summary modal AND the New
    # Shipment manual form will both block save when blank, false
    # means optional. Built-in defaults match the previous hardcoded
    # REQUIRED list, so existing users see no change until they
    # tweak Settings → Field Requirements.
    field_requirements: Dict[str, bool] = Field(
        default_factory=lambda: dict(DEFAULT_FIELD_REQUIREMENTS),
    )


class SettingsUpdate(BaseModel):
    sender: Optional[SenderAddress] = None
    brand: Optional[BrandConfig] = None
    whatsapp_template: Optional[str] = None
    copy_template: Optional[str] = None
    default_eta_days: Optional[int] = None
    prefer_logo: Optional[bool] = None
    logo_shape: Optional[str] = None
    shipment_tagline: Optional[str] = None
    sheet: Optional[SheetConfig] = None
    label_fields: Optional[LabelFields] = None
    custom_fields: Optional[List[CustomLabelField]] = None
    smart_paste_instructions: Optional[str] = None
    smart_paste_ai_enabled: Optional[bool] = None
    ai_cost_simple: Optional[float] = None
    ai_cost_medium: Optional[float] = None
    ai_cost_complex: Optional[float] = None
    order_id_auto_generate: Optional[bool] = None
    order_id_autofill_in_new_shipment: Optional[bool] = None
    field_requirements: Optional[Dict[str, bool]] = None


class Shipment(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tracking_id: str
    courier_id: Optional[str] = None
    courier_name: str = ""
    # Phase-7d Order ID System
    # master_order_id → server-generated, immutable, YYMMDD + 5-digit seq.
    # order_id        → user's own optional reference. Falls back to master.
    master_order_id: str = ""
    order_id: str = ""
    customer_name: str
    customer_phone: str = ""
    customer_alt_phone: str = ""   # secondary/alternative 10-digit number
    customer_email: str = ""       # Phase-3: optional email for invoice / B2B
    customer_gstin: str = ""       # Phase-3: optional 15-char GST number for B2B
    address_line1: str = ""
    address_line2: str = ""
    city: str = ""
    state: str = ""
    pincode: str = ""
    payment_mode: str = "Prepaid"
    amount: float = 0.0         # NEW: always-on amount (prepaid OR COD)
    cod_amount: float = 0.0     # kept for backwards compat, equals amount for COD
    items: List[str] = Field(default_factory=list)
    item_description: str = ""  # fallback text
    weight: str = ""
    # Token / advance payment tracking (for COD split)
    token_amount: float = 0.0   # advance already collected (prepaid portion)
    box_dimensions: str = ""    # e.g. "30×20×10 cm"
    shipment_notes: str = ""    # free text, shown on label if toggled on
    # Phase 2 — Packing variant + courier rate snapshot for reporting.
    # Captured at save time so historical reports stay stable even if
    # the variant row is later edited or deleted.
    variant_id: str = ""
    variant_name: str = ""
    package_type: str = ""
    category: str = ""
    rate_applied: float = 0.0   # ₹ actually charged for this shipment
    rate_basis: str = ""        # "within_state" | "outside_state" | ""
    # Per-shipment dynamic custom field values.
    # Key = CustomLabelField.id, Value = the text to print for this shipment.
    custom_values: Dict[str, str] = Field(default_factory=dict)
    status: str = "Pending"
    created_at: str = Field(default_factory=utcnow_iso)
    delivered_at: Optional[str] = None
    # Phase-9/10: warehouse scan timestamps. Set by /scan-dispatch and
    # /scan-ship respectively; surfaced via GET /shipments so the UI
    # can render "dispatched 2 min ago" / "shipped at …" indicators.
    dispatched_at: Optional[str] = None
    shipped_at: Optional[str] = None
    # Phase-11: Delivery-confirmation state machine (post-Shipped).
    #   "pending"   — auto-flagged, not contacted yet
    #   "sent"      — WhatsApp template dispatched (waiting on reply)
    #   "replied"   — customer replied (details stored verbatim)
    #   "confirmed" — admin confirmed delivery (moves status to Delivered)
    #   "failed"    — customer said parcel NOT received
    confirmation_status: str = "pending"
    last_confirmation_sent_at: Optional[str] = None
    last_confirmation_reply: Optional[str] = None
    # Phase-12: Dispatch confirmation flow (post-Shipped notify customer).
    #   "pending" — newly shipped, customer not yet notified
    #   "sent"    — "your parcel is on its way" message dispatched
    #   "skipped" — admin-marked as not-needed (e.g. local hand-delivery)
    dispatch_msg_status: str = "pending"
    dispatch_msg_sent_at: Optional[str] = None
    # Phase-12: ISO timestamp of the Pending → Processing flip; set by
    # /shipments/bulk-mark-processing. Lets the UI display "Started
    # processing 2h ago" badges.
    processing_started_at: Optional[str] = None
    sheet_row_key: str = ""     # used to dedupe/reference imported rows
    # Soft-delete audit: if this shipment was appended to the Master Sheet
    # (via Smart Paste), we remember the exact row number so deletion can
    # mark it as "DELETED" instead of actually removing the row.
    sheet_row_num: Optional[int] = None
    # Phase F2.1 / F2.4 — preserved from source PendingOrder when this
    # shipment was created via CSV / Excel / Webhook import that
    # carried a real-world status + timestamp. Empty strings on
    # rows that came in via manual Add-Shipment.
    imported_status: str = ""
    imported_at: str = ""
    # Phase-19 — "Modified" tag. Flipped to True the first time an admin
    # edits a shipment via the pencil/edit form (PUT /shipments/{id}
    # with any non-status field). The shipment STAYS in its current
    # status; the Modified filter in the Shipments tab queries this
    # boolean so edited orders surface there alongside their main stage.
    is_modified: bool = False
    modified_at: Optional[str] = None


class ShipmentCreate(BaseModel):
    tracking_id: str
    courier_id: Optional[str] = None
    courier_name: Optional[str] = ""
    master_order_id: Optional[str] = ""
    order_id: Optional[str] = ""
    customer_name: str
    customer_phone: Optional[str] = ""
    customer_alt_phone: Optional[str] = ""
    customer_email: Optional[str] = ""        # Phase-3 Smart Paste enhancement
    customer_gstin: Optional[str] = ""        # Phase-3 Smart Paste enhancement
    address_line1: Optional[str] = ""
    address_line2: Optional[str] = ""
    city: Optional[str] = ""
    state: Optional[str] = ""
    pincode: Optional[str] = ""
    payment_mode: Optional[str] = "Prepaid"
    amount: Optional[float] = 0.0
    cod_amount: Optional[float] = 0.0
    items: Optional[List[str]] = None
    item_description: Optional[str] = ""
    weight: Optional[str] = ""
    token_amount: Optional[float] = 0.0
    box_dimensions: Optional[str] = ""
    shipment_notes: Optional[str] = ""
    # Phase 2 — Packing variant snapshot.
    variant_id: Optional[str] = ""
    variant_name: Optional[str] = ""
    package_type: Optional[str] = ""
    category: Optional[str] = ""
    rate_applied: Optional[float] = 0.0
    rate_basis: Optional[str] = ""
    custom_values: Optional[Dict[str, str]] = None
    sheet_row_key: Optional[str] = ""


class ShipmentUpdate(BaseModel):
    tracking_id: Optional[str] = None
    courier_id: Optional[str] = None
    courier_name: Optional[str] = None
    order_id: Optional[str] = None
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    customer_alt_phone: Optional[str] = None
    customer_email: Optional[str] = None
    customer_gstin: Optional[str] = None
    address_line1: Optional[str] = None
    address_line2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    pincode: Optional[str] = None
    payment_mode: Optional[str] = None
    amount: Optional[float] = None
    cod_amount: Optional[float] = None
    items: Optional[List[str]] = None
    item_description: Optional[str] = None
    weight: Optional[str] = None
    token_amount: Optional[float] = None
    box_dimensions: Optional[str] = None
    shipment_notes: Optional[str] = None
    status: Optional[str] = None
    # Phase 2 — allow editing variant snapshot on an existing shipment
    # (e.g. when the user swaps the variant on a draft shipment). Keep
    # optional; absent = leave existing value untouched.
    variant_id: Optional[str] = None
    variant_name: Optional[str] = None
    package_type: Optional[str] = None
    category: Optional[str] = None
    rate_applied: Optional[float] = None
    rate_basis: Optional[str] = None


# ---------------------- Helpers ----------------------

def strip_id(doc: dict) -> dict:
    if doc and "_id" in doc:
        doc.pop("_id", None)
    return doc


async def seed_defaults():
    # Default tracking URL templates for common couriers
    default_tracking_urls = {
        "Nandan Courier": "https://nandancourier.com/track?id={tracking_id}",
        "DTDC": "https://www.dtdc.in/tracking/tracking_results.asp?strCnno={tracking_id}",
        "India Post": "https://www.indiapost.gov.in/_layouts/15/dop.portal.tracking/trackconsignment.aspx?LocationId={tracking_id}",
        "ST Courier": "https://stcourier.com/track/shipment?trackingNumber={tracking_id}",
        "Trackon": "https://trackon.in/Tracking/MultiTracking?trackingNo={tracking_id}",
        "Anjani Courier": "https://anjanicourier.in/tracking?awb={tracking_id}",
        "Professional Courier": "https://www.tpcindia.com/Tracking2.aspx?id={tracking_id}",
        "Delhivery": "https://www.delhivery.com/track/package/{tracking_id}",
        "BlueDart": "https://www.bluedart.com/tracking?awb={tracking_id}",
        "Ekart": "https://ekartlogistics.com/shipmenttrack/{tracking_id}",
    }

    existing = await db.couriers.count_documents({})
    if existing == 0:
        defaults = [
            Courier(name="Nandan Courier", series_prefix="ND", next_number=1, number_padding=5,
                    contact_phone="", website_url="https://www.nandancourier.com",
                    tracking_url_template=default_tracking_urls["Nandan Courier"]),
            Courier(name="DTDC", series_prefix="DT", next_number=1, number_padding=5,
                    contact_phone="", website_url="https://www.dtdc.in",
                    tracking_url_template=default_tracking_urls["DTDC"]),
            Courier(name="India Post", series_prefix="IP", next_number=1, number_padding=5,
                    contact_phone="1800 266 6868", website_url="https://www.indiapost.gov.in",
                    tracking_url_template=default_tracking_urls["India Post"]),
            Courier(name="ST Courier", series_prefix="ST", next_number=1, number_padding=5,
                    tracking_url_template=default_tracking_urls["ST Courier"]),
            Courier(name="Trackon", series_prefix="TR", next_number=1, number_padding=5,
                    tracking_url_template=default_tracking_urls["Trackon"]),
            Courier(name="Anjani Courier", series_prefix="AJ", next_number=1, number_padding=5,
                    tracking_url_template=default_tracking_urls["Anjani Courier"]),
        ]
        await db.couriers.insert_many([c.model_dump() for c in defaults])
    else:
        # Migration: fill in missing tracking_url_template for existing couriers by matching name
        cursor = db.couriers.find(
            {"$or": [
                {"tracking_url_template": {"$in": ["", None]}},
                {"tracking_url_template": {"$exists": False}},
            ]},
            {"_id": 0, "id": 1, "name": 1},
        )
        async for c in cursor:
            nm = (c.get("name") or "").strip()
            # try exact match first, then case-insensitive contains
            url = default_tracking_urls.get(nm)
            if not url:
                low = nm.lower()
                for k, v in default_tracking_urls.items():
                    if k.lower() in low or low in k.lower():
                        url = v
                        break
            if url:
                await db.couriers.update_one(
                    {"id": c["id"]},
                    {"$set": {"tracking_url_template": url}},
                )

    s = await db.settings.find_one({"id": "default"})
    if not s:
        await db.settings.insert_one(Settings().model_dump())
    else:
        # ensure new fields exist without wiping
        patch = {}
        if "sheet" not in s:
            patch["sheet"] = SheetConfig().model_dump()
        if "copy_template" not in s:
            patch["copy_template"] = Settings().copy_template
        if patch:
            await db.settings.update_one({"id": "default"}, {"$set": patch})


# ---------------------- Routes ----------------------



# -------- Couriers --------

# Courier-partner limit per subscription plan.
# Admins bypass these limits entirely. free_trial + silver get 1 partner so
# the default seeded "Nandan Courier" works out of the box but no new
# partner can be added without upgrading. Gold users may run two carriers
# in parallel (the common "India Post + private courier" pattern).
# Platinum users get up to 5 — enough for any realistic multi-carrier setup.
COURIER_LIMITS: Dict[str, Optional[int]] = {
    "free_trial": 1,
    "silver": 1,
    "gold": 2,
    "platinum": 5,
}

# Human-readable label shown in upgrade prompts.
_PLAN_LABELS = {
    "free_trial": "Free Trial",
    "silver": "Silver",
    "gold": "Gold",
    "platinum": "Platinum",
}


def _courier_limit_for_plan(plan_key: str) -> Optional[int]:
    """Return the max couriers allowed on this plan, or None for unlimited."""
    plan_key = (plan_key or "free_trial").lower()
    if plan_key not in COURIER_LIMITS:
        # Unknown / legacy plan codes fall back to silver's cap.
        return COURIER_LIMITS["silver"]
    return COURIER_LIMITS[plan_key]


def _next_tier_suggestion(plan_key: str) -> Optional[str]:
    """Suggest the next reasonable tier so upgrade CTAs are concrete.
    Returns None when the caller is already on the top-most paid tier
    (Platinum) — the frontend shows a neutral "limit reached" message
    instead of an upgrade nudge.
    """
    order = ["free_trial", "silver", "gold", "platinum"]
    try:
        i = order.index((plan_key or "free_trial").lower())
    except ValueError:
        return "Gold"
    if i >= len(order) - 1:
        return None  # already on Platinum — nothing higher to upgrade to
    nxt = order[i + 1]
    return _PLAN_LABELS.get(nxt, "Gold")


# [refactor] Couriers CRUD + tracking helpers moved to routers/couriers.py

# ──────────────────────────────────────────────────────────────────
# Phase 2 — Courier Packing Variants & Rate Management
# ──────────────────────────────────────────────────────────────────
# Per-courier packing variants (e.g. "ODC 320gm Cover", "Saree Box L").
# Each variant captures Package Type, Category, Dimensions (LxWxH cm),
# Weight (g), and TWO rates: within-state and outside-state. Plan-wise
# cap (free=1, silver=2, gold=5, platinum=8) is enforced on create —
# admin bypasses entirely.
# ──────────────────────────────────────────────────────────────────

PACKAGE_TYPES = ["Cover", "Poly Bag", "Small Box", "Medium Box", "Large Box", "Tube"]
CATEGORIES    = ["Electronics", "Clothing", "Medical", "Documents", "Home Goods", "Other"]


class CourierVariant(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    courier_id: str
    variant_name: str
    package_type: str = ""        # one of PACKAGE_TYPES (free-form fallback OK)
    category: str = ""             # one of CATEGORIES (free-form fallback OK)
    length_cm: float = 0
    width_cm: float = 0
    height_cm: float = 0
    weight_g: float = 0            # in grams
    within_state_rate: float = 0   # ₹
    outside_state_rate: float = 0  # ₹
    active: bool = True
    created_at: str = Field(default_factory=utcnow_iso)


class CourierVariantCreate(BaseModel):
    variant_name: str
    package_type: Optional[str] = ""
    category: Optional[str] = ""
    length_cm: Optional[float] = 0
    width_cm: Optional[float] = 0
    height_cm: Optional[float] = 0
    weight_g: Optional[float] = 0
    within_state_rate: Optional[float] = 0
    outside_state_rate: Optional[float] = 0
    active: Optional[bool] = True


class CourierVariantUpdate(BaseModel):
    variant_name: Optional[str] = None
    package_type: Optional[str] = None
    category: Optional[str] = None
    length_cm: Optional[float] = None
    width_cm: Optional[float] = None
    height_cm: Optional[float] = None
    weight_g: Optional[float] = None
    within_state_rate: Optional[float] = None
    outside_state_rate: Optional[float] = None
    active: Optional[bool] = None


def _packing_variant_cap_for_user(user: Dict[str, Any]) -> Optional[int]:
    """Return the variants-per-courier cap for this user's plan, with
    admin overrides applied. Returns None for unlimited (admins)."""
    if user.get("is_admin"):
        return None
    plan_key = (user.get("plan") or "free_trial").lower()
    spec = PLAN_TABLE.get(plan_key) or PLAN_TABLE["free_trial"]
    return int(spec.packing_variant_cap)


# [refactor] Courier Variants + /me/all-variants moved to routers/couriers.py

# ──────────────────────────────────────────────────────────────────
# Phase 2D — User-defined custom Categories
# ──────────────────────────────────────────────────────────────────
# Per-user list of additional category names beyond the built-in
# CATEGORIES. Surfaced wherever variants are edited (Fixed list +
# Flexible mode in New Shipment) so the user can grow their taxonomy
# without admin intervention.
# ──────────────────────────────────────────────────────────────────


# [refactor] /me/categories + variants/copy-from moved to routers/couriers.py


# -------- Settings --------

async def get_settings(current_user: Dict[str, Any] = Depends(get_current_user)):
    # Each user has their own settings doc. If missing, create a fresh one
    # tagged with this user's id so future reads/writes find it.
    doc = await db.settings.find_one({"user_id": current_user["id"]}, {"_id": 0})
    if not doc:
        s = Settings()
        d = s.model_dump()
        d["user_id"] = current_user["id"]
        d["id"] = f"settings_{current_user['id'][:8]}"
        await db.settings.insert_one(d)
        return s
    return Settings(**doc)


async def update_settings(
    payload: SettingsUpdate,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    update: Dict[str, Any] = {}
    if payload.sender is not None:
        update["sender"] = payload.sender.model_dump()
    if payload.brand is not None:
        update["brand"] = payload.brand.model_dump()
    if payload.whatsapp_template is not None:
        update["whatsapp_template"] = payload.whatsapp_template
    if payload.copy_template is not None:
        update["copy_template"] = payload.copy_template
    if payload.default_eta_days is not None:
        update["default_eta_days"] = payload.default_eta_days
    if payload.sheet is not None:
        update["sheet"] = payload.sheet.model_dump()
    if payload.prefer_logo is not None:
        update["prefer_logo"] = payload.prefer_logo
    if payload.logo_shape is not None:
        update["logo_shape"] = payload.logo_shape
    if payload.shipment_tagline is not None:
        update["shipment_tagline"] = payload.shipment_tagline
    if payload.label_fields is not None:
        update["label_fields"] = payload.label_fields.model_dump()
    if payload.custom_fields is not None:
        # Replace entire list; cap at 6 to avoid label clutter / abuse.
        update["custom_fields"] = [
            f.model_dump() for f in payload.custom_fields[:6]
        ]
    if payload.smart_paste_instructions is not None:
        # Cap to 8 KB so an overly long prompt can't crash the LLM round-trip.
        update["smart_paste_instructions"] = (payload.smart_paste_instructions or "")[:8000]
    if payload.smart_paste_ai_enabled is not None:
        update["smart_paste_ai_enabled"] = bool(payload.smart_paste_ai_enabled)
    # Phase-7d: Master Order ID auto-generate flag.
    if payload.order_id_auto_generate is not None:
        update["order_id_auto_generate"] = bool(payload.order_id_auto_generate)
    # Phase-7e: New-Shipment auto-fill flag.
    if payload.order_id_autofill_in_new_shipment is not None:
        update["order_id_autofill_in_new_shipment"] = bool(
            payload.order_id_autofill_in_new_shipment
        )
    # Phase-8: Per-field "Required" toggles. Caller may send a partial
    # dict — we MERGE it onto the existing one so toggling one field
    # never resets the rest. Unknown keys (not in DEFAULT_FIELD_
    # REQUIREMENTS) are silently dropped to keep the dict clean.
    if payload.field_requirements is not None:
        existing_doc = await db.settings.find_one(
            {"user_id": current_user["id"]},
            {"_id": 0, "field_requirements": 1},
        ) or {}
        merged = dict(existing_doc.get("field_requirements") or DEFAULT_FIELD_REQUIREMENTS)
        for k, v in (payload.field_requirements or {}).items():
            if k in DEFAULT_FIELD_REQUIREMENTS:
                merged[k] = bool(v)
        update["field_requirements"] = merged
    # Phase-4b+: AI credit rate card — clamp 0 ≤ x ≤ 2 (spec cap).
    for _f in ("ai_cost_simple", "ai_cost_medium", "ai_cost_complex"):
        _v = getattr(payload, _f)
        if _v is not None:
            try:
                update[_f] = max(0.0, min(2.0, round(float(_v), 2)))
            except (TypeError, ValueError):
                pass
    if not update:
        raise HTTPException(status_code=400, detail="No fields to update")
    # Per-user settings doc. Ensures tenants don't overwrite each other.
    settings_filter = {"user_id": current_user["id"]}
    update["user_id"] = current_user["id"]
    res = await db.settings.find_one_and_update(
        settings_filter,
        {"$set": update, "$setOnInsert": {"id": f"settings_{current_user['id'][:8]}"}},
        upsert=True,
        return_document=True,
    )
    return Settings(**strip_id(res))


# -------- Google Sheet integration (public link) --------

def parse_sheet_url(url: str) -> Dict[str, str]:
    """Extract sheet_id and gid from a Google Sheets URL."""
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", url)
    if not m:
        raise HTTPException(
            status_code=400,
            detail="Invalid Google Sheet URL. Paste the full URL from your browser.",
        )
    sheet_id = m.group(1)
    gid_match = re.search(r"[?#&]gid=(\d+)", url)
    gid = gid_match.group(1) if gid_match else "0"
    return {"sheet_id": sheet_id, "gid": gid}


async def fetch_sheet_csv(sheet_id: str, gid: str) -> str:
    export = (
        f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"
    )
    async with httpx.AsyncClient(follow_redirects=True, timeout=20) as cli:
        r = await cli.get(export)
    if r.status_code != 200 or "text/csv" not in r.headers.get("content-type", "") + " ":
        # Some Google responses return text/html with login page when not public
        body_start = r.text[:200]
        if "<html" in body_start.lower() or "Sign in" in r.text:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Sheet is not public. Open Share → General access → "
                    "'Anyone with the link → Viewer' and try again."
                ),
            )
        raise HTTPException(status_code=400, detail=f"Could not fetch sheet ({r.status_code}).")
    return r.text


def parse_csv_rows(csv_text: str) -> Dict[str, Any]:
    buf = io.StringIO(csv_text)
    reader = csv.reader(buf)
    rows = list(reader)
    if not rows:
        return {"headers": [], "rows": []}
    headers = [h.strip() for h in rows[0]]
    data_rows: List[Dict[str, str]] = []
    for r in rows[1:]:
        # skip fully empty rows
        if not any((cell or "").strip() for cell in r):
            continue
        rec = {}
        for i, h in enumerate(headers):
            rec[h] = r[i].strip() if i < len(r) else ""
        data_rows.append(rec)
    return {"headers": headers, "rows": data_rows}


class SheetPreviewRequest(BaseModel):
    url: str




def auto_guess_mapping(headers: List[str]) -> Dict[str, str]:
    """Best-effort auto column mapping for common names (English+Gujarati+Hindi)."""
    lookups = {
        "order_id": ["order id", "orderid", "order no", "order number", "ઓર્ડર", "order"],
        "customer_name": ["name", "customer", "customer name", "full name", "નામ", "ग्राहक"],
        "phone": ["phone", "mobile", "contact", "whatsapp", "phone number", "mobile number",
                  "નંબર", "ફોન", "mob"],
        "address": ["address", "full address", "delivery address", "સરનામું", "पता"],
        "city": ["city", "શહેર", "शहर"],
        "state": ["state", "રાજ્ય", "राज्य"],
        "pincode": ["pincode", "pin code", "zip", "postal", "pin"],
        "item": ["item", "items", "product", "products", "order item", "product name",
                 "what you want", "વસ્તુ", "आइटम"],
        "amount": ["amount", "price", "total", "total amount", "cod amount", "order amount",
                   "રકમ", "राशि"],
        "timestamp": ["timestamp", "date", "created", "submitted"],
    }
    mapping: Dict[str, str] = {}
    lowered = {h.lower().strip(): h for h in headers}
    for key, options in lookups.items():
        for opt in options:
            # exact match first
            if opt in lowered:
                mapping[key] = lowered[opt]
                break
        if key in mapping:
            continue
        for opt in options:
            for lh, orig in lowered.items():
                if opt in lh:
                    mapping[key] = orig
                    break
            if key in mapping:
                break
    return mapping


async def sheets_orders(
    background_tasks: BackgroundTasks,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    doc = await db.settings.find_one({"user_id": current_user["id"]}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=400, detail="Settings not configured")
    s = Settings(**doc)
    cfg = s.sheet
    if not cfg.sheet_id:
        raise HTTPException(status_code=400, detail="Google Sheet not connected")

    # Phase-5: Service-Account-first read. If the user has shared their
    # Sheet with our SA the sheet stays PRIVATE — no public-link required.
    # Fall back to public-CSV path only if SA can't access AND CSV works.
    data: Dict[str, Any] = {"headers": [], "rows": []}
    access_method = "public_csv"
    if sheet_read_user_sheet is not None:
        sa_resp = sheet_read_user_sheet(cfg.sheet_id, cfg.gid or "0")
        if sa_resp.get("ok"):
            data = {"headers": sa_resp["headers"], "rows": sa_resp["rows"]}
            access_method = "service_account"
        elif sa_resp.get("error") in ("SHEET_NOT_SHARED", "SHEET_NOT_FOUND"):
            try:
                csv_text = await fetch_sheet_csv(cfg.sheet_id, cfg.gid or "0")
                data = parse_csv_rows(csv_text)
            except HTTPException:
                sa_email = sheet_get_sa_email() if sheet_get_sa_email else ""
                raise HTTPException(
                    status_code=403,
                    detail=(
                        "Can't read your Google Sheet. Please share it with "
                        f"{sa_email or '<our service account>'} (Editor) — "
                        "this keeps your Sheet private."
                    ),
                )
        else:
            # Unknown SA error → try public path as last resort.
            try:
                csv_text = await fetch_sheet_csv(cfg.sheet_id, cfg.gid or "0")
                data = parse_csv_rows(csv_text)
            except HTTPException:
                raise HTTPException(status_code=400, detail="Sheet read failed")
    else:
        csv_text = await fetch_sheet_csv(cfg.sheet_id, cfg.gid or "0")
        data = parse_csv_rows(csv_text)

    # Detect header changes — but be smart about it. The legacy logic
    # naïvely compared `data["headers"] != cfg.headers` which fired
    # `headers_changed=True` forever once the user added a new column
    # (because cfg.headers was set on first-connect and never updated).
    #
    # New logic:
    #   • cfg.headers empty → first read; persist + return False.
    #   • Old headers ⊆ new headers (purely additive) → silently
    #     update cfg.headers + return False (existing column_mapping
    #     keeps working).
    #   • A previously-mapped column was removed/renamed → return
    #     True so the UI can prompt the user to remap.
    headers_changed = False
    fresh_headers = list(data.get("headers") or [])
    saved_headers = list(cfg.headers or [])
    if fresh_headers and fresh_headers != saved_headers:
        if not saved_headers:
            # First-ever read after connect — just persist.
            try:
                await db.settings.update_one(
                    {"user_id": current_user["id"]},
                    {"$set": {"sheet.headers": fresh_headers}},
                )
            except Exception:
                logger.exception("failed to bootstrap sheet.headers")
        else:
            # Determine if any *currently mapped* column was lost.
            mapped_cols = {
                (cfg.column_mapping or {}).get(k) for k in (cfg.column_mapping or {})
            }
            mapped_cols.discard("")
            mapped_cols.discard(None)
            lost = [c for c in mapped_cols if c not in fresh_headers]
            if lost:
                # Real schema change — keep the warning ON so the UI
                # can prompt remap. Don't auto-update `cfg.headers`
                # so the user sees the original mapping context.
                headers_changed = True
            else:
                # Purely additive change → silently sync.
                try:
                    await db.settings.update_one(
                        {"user_id": current_user["id"]},
                        {"$set": {"sheet.headers": fresh_headers}},
                    )
                except Exception:
                    logger.exception("failed to refresh sheet.headers (additive)")

    mapping = cfg.column_mapping or {}

    # Phase F2.3 — Legacy mapping keys (phone, item, timestamp) ↔
    # canonical keys (customer_phone, items, created_at_override).
    # Both spellings are accepted on read so users with old saved
    # mappings keep working AND new mappings using the canonical
    # names from import_schema work too. Build a unified lookup.
    SHEET_KEY_ALIASES = {
        "phone":             "customer_phone",
        "customer_phone":    "phone",
        "item":              "items",
        "items":             "item",
        "timestamp":         "created_at_override",
        "created_at_override": "timestamp",
    }

    def mapped_field(row: Dict[str, str], key: str) -> str:
        col = mapping.get(key)
        if not col:
            alias = SHEET_KEY_ALIASES.get(key)
            if alias:
                col = mapping.get(alias)
        if not col:
            return ""
        return row.get(col, "")

    # Find shipments that were originally imported from this sheet
    # OR were created from any other source (paste / file / webhook /
    # smart-paste) and later auto-appended to the master sheet by the
    # ship pipeline. Either way we need to mark the matching sheet row
    # as already_shipped so it doesn't double-count in the All filter.
    #
    # Phase F3.9.11 — Expanded match keys. Previously we only matched
    # via `sheet_row_key`, which is set only when a shipment was
    # ORIGINALLY imported from a user sheet. Newly-appended sheet rows
    # for paste/file/webhook-shipped orders had no matching
    # sheet_row_key on any shipment, so they showed up as a fresh
    # "ghost" entry and inflated the All count by +1 after every
    # ship action. We now also match by tracking_id and
    # master_order_id which DO exist on every shipment.
    imported_keys: set = set()
    tracking_ids:  set = set()
    master_ids:    set = set()
    order_ids:     set = set()
    existing = await db.shipments.find(
        {"user_id": current_user["id"]},
        {
            "_id": 0,
            "sheet_row_key":  1,
            "tracking_id":    1,
            "master_order_id": 1,
            "order_id":       1,
        },
    ).to_list(20000)
    for e in existing:
        if e.get("sheet_row_key"):
            imported_keys.add(e["sheet_row_key"])
        if e.get("tracking_id"):
            tracking_ids.add(str(e["tracking_id"]).strip())
        if e.get("master_order_id"):
            master_ids.add(str(e["master_order_id"]).strip())
        if e.get("order_id"):
            order_ids.add(str(e["order_id"]).strip())

    def mapped(row: Dict[str, str], key: str) -> str:
        # Backwards-compat shim — keeps the inner loop unchanged but
        # all reads go through the alias-aware `mapped_field` helper.
        return mapped_field(row, key)

    orders = []
    for idx, row in enumerate(data["rows"]):
        row_key = _row_key(row, mapping, idx)
        # Phase F3.9.11 — Multi-key dedupe. We now flag a sheet row as
        # already_shipped when ANY of its identifiers match an
        # existing shipment, not just the original sheet_row_key.
        # Without this, a paste/webhook order that gets shipped and
        # then auto-backed-up to the master sheet shows up as a fresh
        # "ghost" row in the All filter on the very next refresh.
        sheet_tracking = (mapped(row, "tracking_id") or "").strip()
        sheet_master   = (mapped(row, "master_order_id") or "").strip()
        sheet_order_id = (mapped(row, "order_id") or "").strip()
        is_shipped = (
            row_key in imported_keys
            or (sheet_tracking and sheet_tracking in tracking_ids)
            or (sheet_master   and sheet_master   in master_ids)
            or (sheet_order_id and sheet_order_id in order_ids)
        )
        orders.append({
            "row_key": row_key,
            "row_index": idx + 2,  # spreadsheet row (1-indexed + header)
            "order_id": mapped(row, "order_id"),
            "customer_name": mapped(row, "customer_name"),
            "phone": mapped(row, "phone"),
            "address": mapped(row, "address"),
            "city": mapped(row, "city"),
            "state": mapped(row, "state"),
            "pincode": mapped(row, "pincode"),
            "item": mapped(row, "item"),
            "amount": mapped(row, "amount"),
            "timestamp": mapped(row, "timestamp"),
            # Phase F2.3 — surface the new canonical fields too so the
            # ship-this-row UI can pre-fill the same metadata that
            # CSV/Webhook imports already deliver.
            "status":       mapped(row, "status"),
            "payment_mode": mapped(row, "payment_mode"),
            "weight":       mapped(row, "weight"),
            "items_full":   mapped(row, "items"),
            "alt_phone":    mapped(row, "customer_alt_phone"),
            "email":        mapped(row, "customer_email"),
            "gstin":        mapped(row, "customer_gstin"),
            "category":     mapped(row, "category"),
            "notes":        mapped(row, "notes"),
            "token_amount": mapped(row, "token_amount"),
            "box_dimensions": mapped(row, "box_dimensions"),
            "courier_hint": mapped(row, "courier_hint"),
            "already_shipped": is_shipped,
            "raw": row,
        })
    # ---- Auto-backup any new user-sheet rows to Master Sheet (background) ----
    # This is fire-and-forget — the user gets their list immediately and
    # the sync runs after the response is sent. Per-row dedup state is
    # stored in `user_sheet_master_backups` so repeat reads don't double
    # the master rows.
    user_name_for_log = (
        current_user.get("full_name")
        or current_user.get("name")
        or (current_user.get("email", "").split("@")[0])
    )
    background_tasks.add_task(
        _sync_user_sheet_to_master_bg,
        current_user["id"],
        user_name_for_log or "",
        list(data.get("rows") or []),
        dict(mapping),
    )

    # Cache the unshipped-sheet-order count so the Home dashboard can
    # combine it with smart-paste-pending count in a single cheap DB
    # read (no extra gspread call from `/orders/pending-count`). Stored
    # alongside a timestamp so staleness can be judged later if needed.
    unshipped_count = sum(1 for o in orders if not o.get("already_shipped"))
    try:
        await db.settings.update_one(
            {"user_id": current_user["id"]},
            {"$set": {
                "sheet.unshipped_count_cached": int(unshipped_count),
                "sheet.unshipped_count_at": utcnow_iso(),
            }},
        )
    except Exception:
        logger.exception("failed to cache sheet.unshipped_count")

    return {
        "headers": data["headers"],
        "headers_changed": headers_changed,
        "orders": orders,
        "total": len(orders),
        "access_method": access_method,
    }


def _row_key(row: Dict[str, str], mapping: Dict[str, str], idx: int) -> str:
    order_col = mapping.get("order_id")
    phone_col = mapping.get("phone")
    name_col = mapping.get("customer_name")
    parts = []
    if order_col and row.get(order_col):
        parts.append(row[order_col])
    if phone_col and row.get(phone_col):
        parts.append(row[phone_col])
    if name_col and row.get(name_col):
        parts.append(row[name_col])
    if not parts:
        parts.append(str(idx))
    return "|".join(parts).strip()[:200]


# ---------------------------------------------------------------------------
# Background: User-Sheet → Master-Sheet auto-backup
# ---------------------------------------------------------------------------
# When the user opens the "Sheet Orders" tab in the app we trigger a
# silent, fire-and-forget backup of any new rows in their personal
# Google Sheet to the central Master Sheet. This way rows that the
# user types DIRECTLY into their own sheet (not through Smart Paste
# or Add Shipment) are still archived to master without any manual
# action — honouring the universal "data backup is mandatory on every
# plan" policy.
#
# Dedup state lives in the `user_sheet_master_backups` collection,
# keyed by (user_id, row_key). On first read we ensure the unique
# index exists.
_USER_SHEET_BACKUP_INDEX_READY = False


async def _ensure_user_sheet_backup_index() -> None:
    """Idempotently create the unique compound index on first call."""
    global _USER_SHEET_BACKUP_INDEX_READY
    if _USER_SHEET_BACKUP_INDEX_READY:
        return
    try:
        await db.user_sheet_master_backups.create_index(
            [("user_id", 1), ("row_key", 1)], unique=True, name="uid_rowkey_unique",
        )
        _USER_SHEET_BACKUP_INDEX_READY = True
    except Exception:
        # If it already exists or Mongo is busy, just proceed — the
        # unique constraint will still be honoured if it was created
        # in a previous boot.
        _USER_SHEET_BACKUP_INDEX_READY = True


def _row_to_master_payload(
    row: Dict[str, str], mapping: Dict[str, str]
) -> Dict[str, str]:
    """Translate a user-sheet row + column mapping into the kwargs that
    `sheet_writer.append_order_row` expects. Missing fields become "".
    """
    def m(key: str) -> str:
        col = mapping.get(key)
        if not col:
            return ""
        return str(row.get(col, "") or "").strip()

    return {
        "order_id": m("order_id"),
        "name": m("customer_name"),
        "phone": m("phone"),
        "address": m("address"),
        "city": m("city"),
        "state": m("state"),
        "pincode": m("pincode"),
        "item_type": m("item"),
        "amount": m("amount"),
        "payment_mode": m("payment_mode"),
        "weight": m("weight"),
        "alt_phone": m("alt_phone"),
        "token_amount": m("token_amount"),
    }


async def _sync_user_sheet_to_master_bg(
    user_id: str,
    user_name: str,
    rows: List[Dict[str, str]],
    mapping: Dict[str, str],
) -> None:
    """Background task: append any user-sheet rows that haven't been
    backed up yet to the Master Sheet, then record the row_key in
    `user_sheet_master_backups` so subsequent reads skip them.

    Errors are swallowed (this is best-effort, foreground response
    must not break) and only surfaced via logger so admins can spot
    quota / auth issues in the logs.
    """
    if sheet_append_order_row is None or not rows:
        return
    try:
        await _ensure_user_sheet_backup_index()
        # Pull all already-backed-up keys for this user in one query.
        existing_cursor = db.user_sheet_master_backups.find(
            {"user_id": user_id}, {"_id": 0, "row_key": 1},
        )
        existing = {d["row_key"] async for d in existing_cursor}
    except Exception:
        existing = set()

    # Allocate skipped rows up to a sane batch size so a 5000-row sheet
    # doesn't block the worker forever. The next /sheets/orders call
    # will pick up the rest.
    # ALSO: each append performs ~3 Sheets API reads under the hood, and
    # Google enforces a 60 reads-per-minute-per-SA quota. We cap at 20
    # rows/call AND sleep ~1.2s between writes so a single bg sync
    # consumes well below quota even if the user has the app open in
    # multiple tabs.
    BATCH_LIMIT = 20
    SLEEP_BETWEEN_ROWS = 1.2  # seconds
    appended = 0
    for idx, row in enumerate(rows):
        if appended >= BATCH_LIMIT:
            break
        key = _row_key(row, mapping, idx)
        if not key or key in existing:
            continue
        # Composite-key fallback safety: if the row has only ONE part
        # AND that part is a small integer like a row index, skip — we
        # don't want to back up obvious blanks.
        if "|" not in key and key.isdigit() and len(key) <= 3:
            continue
        payload = _row_to_master_payload(row, mapping)
        # Strict empty-row guard: skip if name+phone+address are all
        # empty (likely a stray blank row in the user's sheet).
        if not (payload["name"] or payload["phone"] or payload["address"]):
            continue
        try:
            sheet_meta = sheet_append_order_row(
                user_id=user_id,
                user_name=user_name or user_id[:8],
                master_order_id="",  # user's own sheet rows have no master id
                notice="auto-backup from user sheet",
                status="Pending",
                **payload,
            )
            sheet_row_num = None
            if sheet_parse_row_from_updated_range and sheet_meta:
                try:
                    sheet_row_num = sheet_parse_row_from_updated_range(
                        sheet_meta.get("updated_range")
                    )
                except Exception:
                    sheet_row_num = None
            await db.user_sheet_master_backups.insert_one({
                "user_id": user_id,
                "row_key": key,
                "sheet_row_num": sheet_row_num,
                "backed_up_at": utcnow_iso(),
            })
            existing.add(key)
            appended += 1
            # Spacing between writes keeps us comfortably under Google's
            # 60 reads/min/user quota. Use asyncio.sleep so we don't
            # block the event loop for other requests.
            try:
                import asyncio as _asyncio
                await _asyncio.sleep(SLEEP_BETWEEN_ROWS)
            except Exception:
                pass
        except Exception as e:
            # DuplicateKeyError → already inserted via a parallel call.
            # Anything else (sheet write failure, quota, auth) → log
            # and move on; we'll retry on the next page-load.
            try:
                from pymongo.errors import DuplicateKeyError
                if isinstance(e, DuplicateKeyError):
                    existing.add(key)
                    continue
            except Exception:
                pass
            logger.warning(f"User-sheet → Master backup row_key={key!r} failed: {e}")
            continue

    if appended:
        logger.info(
            f"User-sheet → Master backup: appended {appended} new row(s) for user {user_id}"
        )


# -------- Shipments --------





# ---------------------------------------------------------------------------
# Phase-9: Scan-to-Dispatch — warehouse-optimised barcode workflow.
# ---------------------------------------------------------------------------
# A single POST endpoint that atomically resolves a scanned barcode
# (tracking_id) into one of three outcomes and returns a compact,
# structured result the mobile scanner UI maps to either a cream
# success toast, a warn banner, or a red failure badge.
#
#   * "moved"   → status was "Pending", now flipped to "Dispatch"
#   * "already" → status was already "Dispatch" (idempotent no-op)
#   * "failed"  → not found OR in a wrong status (Shipped/Delivered/…)
#
# The client loops calls on each scan; duplicate-scan debouncing is
# handled on the client (spec: "ignore within a few seconds").





# ---------------------------------------------------------------------------
# Phase-10: Scan-to-Shipped — second half of the warehouse workflow.
# Atomic Dispatch → Shipped transition. Same outcome contract as
# /scan-dispatch above so the mobile scanner can share its UI code.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Phase-12: Manually flip Pending shipments to Processing.
# Designed for the warehouse "I'm starting to pack this batch" action.
# Bulk-friendly so the operator can multi-select on the Shipments tab and
# move 50 rows in a single round-trip.
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# Phase-11: Delivery Confirmation (Shipped → Delivered via WhatsApp ping).
# ---------------------------------------------------------------------------
# A confirmation workflow, NOT a scanner. Once a parcel has been
# "Shipped" for N days (default 5), it enters the delivery-confirmation
# queue. Admin bulk-selects, taps "Send WhatsApp" to open the WhatsApp
# deep link pre-filled with the Gujarati template, and optionally
# "Mark as Delivered" to flip status → Delivered.
#
# State machine (shipment.confirmation_status):
#   pending   → auto-flagged, not contacted yet
#   sent      → WhatsApp message dispatched today
#   replied   → customer replied (manual mark)
#   confirmed → admin confirmed delivery (status becomes "Delivered")
#   failed    → customer said not received

DELIVERY_CONF_MIN_DAYS = 5   # default threshold — overridable per user











async def sheets_sample_template():
    """Return a CSV with ideal column layout + example rows for users to import into Google Sheets."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "Timestamp", "Order ID", "Name", "Phone", "Address",
        "City", "State", "Pincode", "Item", "Amount", "Payment Mode",
    ])
    samples = [
        ["2026-01-15 10:30:00", "ORD-1001", "Ramesh Patel", "9876543210",
         "12, Navrangpura Main Road, Ellisbridge",
         "Ahmedabad", "Gujarat", "380006",
         "Cotton Kurta Large - Blue", "850", "COD"],
        ["2026-01-15 11:12:45", "ORD-1002", "Priya Shah", "9823456710",
         "B-204, Sunrise Apts, Satellite Road",
         "Ahmedabad", "Gujarat", "380015",
         "Silk Saree Red; Matching Blouse", "2499", "Prepaid"],
        ["2026-01-15 14:02:10", "ORD-1003", "Rahul Mehta", "9812345678",
         "Shop 7, Main Bazaar, Near Bus Stand",
         "Rajkot", "Gujarat", "360001",
         "Men Jeans 32 - Dark Blue", "1299", "COD"],
        ["2026-01-15 16:47:22", "ORD-1004", "Anjali Desai", "9801234567",
         "45, Gulab Nagar, Adajan",
         "Surat", "Gujarat", "395009",
         "Kids T-shirt Small; Shorts", "699", "Prepaid"],
    ]
    for row in samples:
        w.writerow(row)
    return PlainTextResponse(
        buf.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="courier_sheet_template.csv"'
        },
    )










# ───────── Phase-15: Auto-fill State + Pincode from City ──────────────
# Smart Paste Summary Card calls this when the user has typed/pasted a
# city / locality but doesn't know the pincode + state. Backed by India
# Post (cached forever in Mongo). Returns up to 8 pincode candidates so
# the user just taps one to confirm — no typing required.









# ---------------------------------------------------------------------------
# Master Sheet backup helper — MANDATORY for every shipment-creation path
# ---------------------------------------------------------------------------
# User policy (2026-04-29): Regardless of the user's plan tier, every
# shipment created in the system MUST be appended to the central Master
# Sheet so we have a per-user backup keyed off `user_id`. The user's own
# personal Google Sheet is OPTIONAL — restore from master is the gated
# Premium feature, but the backup itself is universal.
#
# This helper hides the column-mapping detail from each call-site and
# centralises the error contract: if the Master Sheet write fails we
# raise HTTP 502 the same way the smart-paste flow already does. The
# upstream caller is expected to invoke this BEFORE inserting into Mongo
# so a failed sheet write doesn't leave a "ghost" Mongo row.
# A sentinel returned by `_backup_shipment_to_master_sheet` when the
# Master Sheet write was deferred due to transient API problems
# (Google Sheets quota / 5xx). The caller should save the shipment to
# Mongo with `master_backup_status="pending"` and a periodic worker
# (or the next call from this user) will retry the write.
_BACKUP_DEFERRED_SENTINEL: Dict[str, Any] = {"deferred": True}


def _is_transient_sheet_error(err: Exception) -> bool:
    """Mirror sheet_writer._is_transient — keep server.py side standalone
    so we don't have to import private helpers."""
    msg = str(err)
    if any(s in msg for s in ("[429]", "[500]", "[502]", "[503]", "[504]")):
        return True
    lowered = msg.lower()
    return (
        "quota exceeded" in lowered
        or "rate limit" in lowered
        or "resource_exhausted" in lowered
        or "user rate limit exceeded" in lowered
    )


async def _backup_shipment_to_master_sheet(
    *, current_user: Dict[str, Any], data: Dict[str, Any], notice: str,
) -> Dict[str, Any]:
    """Append a single shipment record to the Master Sheet.

    Returns:
        - sheet_meta dict with `updated_range` on success (write succeeded
          immediately or after retries inside sheet_writer).
        - `_BACKUP_DEFERRED_SENTINEL` (a dict with `deferred=True`) if the
          write failed with a transient quota / 5xx error — caller should
          mark the shipment `master_backup_status="pending"` and continue.
        - Raises HTTPException(502/503) on permanent failures (auth, sheet
          missing, malformed config, etc.) — caller surfaces these to the
          user since they can't be auto-recovered.
    """
    if sheet_append_order_row is None:
        raise HTTPException(
            status_code=503,
            detail="Google Sheets integration not configured on server.",
        )
    addr_text = " ".join(filter(None, [
        str(data.get("address_line1") or data.get("address") or ""),
        str(data.get("address_line2") or ""),
    ])).strip()
    items_val = data.get("items") or data.get("item_description") or ""
    if isinstance(items_val, list):
        item_type_text = ", ".join(str(x) for x in items_val if x)
    else:
        item_type_text = str(items_val or "")
    user_name_val = (
        current_user.get("full_name")
        or current_user.get("name")
        or (current_user.get("email", "").split("@")[0])
        or current_user.get("id", "")[:8]
    )
    try:
        return sheet_append_order_row(
            user_id=current_user["id"],
            user_name=user_name_val,
            master_order_id=str(data.get("master_order_id") or "") or "",
            order_id=str(data.get("order_id") or "") or "",
            name=str(data.get("customer_name") or data.get("name") or "") or "",
            phone=str(data.get("customer_phone") or data.get("phone") or "") or "",
            alt_phone=str(data.get("customer_alt_phone") or data.get("alt_phone") or "") or "",
            address=addr_text,
            city=str(data.get("city") or "") or "",
            state=str(data.get("state") or "") or "",
            pincode=str(data.get("pincode") or "") or "",
            item_type=item_type_text,
            amount=data.get("amount") or "",
            token_amount=data.get("token_amount") or "",
            weight=str(data.get("weight") or "") or "",
            payment_mode=str(data.get("payment_mode") or "") or "",
            status=str(data.get("status") or "Pending") or "Pending",
            notice=notice,
        )
    except Exception as e:
        if _is_transient_sheet_error(e):
            # Quota / temporary outage — defer rather than blocking the
            # user. The shipment will still be saved to Mongo with
            # `master_backup_status="pending"` and the next call (or the
            # periodic retry worker) will push it.
            logger.warning(
                "Master Sheet backup deferred (transient): %s", str(e)[:200]
            )
            # Stash the original payload for the retry worker.
            return {
                **_BACKUP_DEFERRED_SENTINEL,
                "_pending_payload": {
                    "user_id": current_user["id"],
                    "user_name": user_name_val,
                    "master_order_id": str(data.get("master_order_id") or "") or "",
                    "order_id": str(data.get("order_id") or "") or "",
                    "name": str(data.get("customer_name") or data.get("name") or "") or "",
                    "phone": str(data.get("customer_phone") or data.get("phone") or "") or "",
                    "alt_phone": str(data.get("customer_alt_phone") or data.get("alt_phone") or "") or "",
                    "address": addr_text,
                    "city": str(data.get("city") or "") or "",
                    "state": str(data.get("state") or "") or "",
                    "pincode": str(data.get("pincode") or "") or "",
                    "item_type": item_type_text,
                    "amount": data.get("amount") or "",
                    "token_amount": data.get("token_amount") or "",
                    "weight": str(data.get("weight") or "") or "",
                    "payment_mode": str(data.get("payment_mode") or "") or "",
                    "status": str(data.get("status") or "Pending") or "Pending",
                    "notice": notice,
                },
            }
        # Permanent failure — auth, missing sheet, etc. Surface to caller.
        logger.exception("Master Sheet backup failed (permanent)")
        raise HTTPException(
            status_code=502,
            detail=f"Master Sheet backup failed — order not saved. Reason: {e}",
        )








# ---------------------- Pending Orders (Smart Paste + Sheet queue) ----------------------

class PendingOrder(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source: str = "paste"  # "paste" | "sheet" | "manual"
    status: str = "pending"  # "pending" | "shipped" | "skipped"

    # Phase-7d Order ID System.
    #
    # master_order_id  → server-generated, format YYMMDD + zero-padded
    #                    global counter (e.g. "2604290001"). Immutable.
    # order_id         → user's own optional reference (e.g. "ABC-001").
    #                    Falls back to master_order_id if blank at save.
    master_order_id: str = ""
    order_id: str = ""

    # Customer data
    customer_name: str = ""
    customer_phone: str = ""
    customer_alt_phone: str = ""
    customer_email: str = ""        # Phase-3 Smart Paste enhancement
    customer_gstin: str = ""        # Phase-3 Smart Paste enhancement (B2B)
    address_line1: str = ""
    address_line2: str = ""
    city: str = ""
    state: str = ""
    pincode: str = ""
    items: str = ""  # comma separated
    amount: float = 0
    token_amount: float = 0.0   # advance / token already collected (COD orders)
    payment_mode: str = "COD"  # "COD" | "PAID"

    # Hints from paste
    courier_hint: str = ""
    order_id_hint: str = ""
    weight: str = ""
    notes: str = ""

    # Source-specific
    sheet_row_num: Optional[int] = None
    raw_text: str = ""  # original pasted message (trimmed)

    # Link when shipped
    shipment_id: Optional[str] = None
    tracking_id: Optional[str] = None

    # Parse confidence (per field: "high" | "medium" | "low" | "missing")
    confidence: Dict[str, str] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)

    # Per-user Custom Fields values (Smart Paste flow). Maps
    # user_custom_fields.id → string value. Routed to the user's
    # personal sheet column at create time and re-routed at ship time.
    custom_values: Dict[str, Any] = Field(default_factory=dict)

    created_at: str = Field(default_factory=utcnow_iso)
    processed_at: Optional[str] = None

    # Phase F1 — file-import metadata (filename, format, imported_at,
    # row_index). Populated by /api/orders/import/commit so the Orders
    # tab can render the "File Imports" queue badge with provenance.
    # Optional/nullable so existing paste/sheet/manual rows stay
    # backwards-compatible.
    source_meta: Optional[Dict[str, Any]] = None

    # Phase F1.2 — extra parcel/box fields surfaced from CSV/Excel
    # imports. These mirror the Add-Shipment form so a user can map
    # ANY column from their export. Optional, default empty/zero so
    # existing rows stay valid; consumed downstream by ship-this-order.
    box_dimensions: str   = ""
    box_length:    float = 0.0
    box_width:     float = 0.0
    box_height:    float = 0.0
    category:      str   = ""

    # Phase F2.1 — Status + Timestamp from imported sources
    # (CSV/Excel/Google Sheet/Webhook). When the source row already
    # carries a real-world Status (Shipped / Delivered / Cancelled /
    # …) and / or a creation timestamp, those values land here on the
    # PendingOrder so ship_pending_order can copy them straight to the
    # resulting Shipment instead of resetting to "Pending" + now().
    # Both default to "" so legacy paste/sheet/manual rows stay valid.
    imported_status: str = ""
    imported_at:     str = ""

    # Phase-21 — "NEW" badge + Repeat-customer marker on pending cards.
    # `viewed` flips to True the moment the operator taps the card in
    # the Orders tab (POST /orders/pending/{id}/mark-viewed). Until
    # then, the UI paints a NEW badge so freshly-ingested orders are
    # impossible to miss. `is_repeat_customer` is set at ingest time
    # when the customer's phone matches an existing shipment for the
    # same user → UI paints a REPEAT badge alongside (or instead of)
    # NEW. Both default to False so legacy rows stay valid.
    viewed: bool = False
    is_repeat_customer: bool = False


class SmartPasteRequest(BaseModel):
    text: str
    use_ai: Optional[bool] = True   # Phase-4b+: LLM-driven parse by default
    skip_llm: Optional[bool] = False  # fast path when text is already canonical
    # Optional: per-user custom-field values keyed by user_custom_fields.id
    # Backend writes them to the user's personal sheet at the configured
    # column letter (best-effort) and persists on the PendingOrder so
    # they flow into the eventual shipment.
    custom_values: Optional[Dict[str, str]] = None


class SmartPasteChatRequest(BaseModel):
    """Turn-based Smart Paste chat.

    The client holds the conversation state (current known fields + list
    of what's still missing) and sends the user's natural-language reply
    on each turn. We build a synthetic structured block from `fields`,
    append the reply, re-run the LLM, and return merged fields plus an
    AI message the client can render as a chat bubble.
    """
    fields: Dict[str, Any] = {}
    reply: str = ""


class SmartPastePhotoRequest(BaseModel):
    """Image-based Smart Paste.
    The client captures or picks a photo, base64-encodes it (no
    data:URI prefix), and POSTs it here with the MIME type. We send it
    to Gemini Vision and return the same shape as /smart-paste/chat.
    """
    image_base64: str
    mime: str = "image/jpeg"



class ShipOrderRequest(BaseModel):
    courier_id: str
    # optional overrides before creating the shipment
    overrides: Optional[Dict[str, Any]] = None
    # Phase-23 (2026-05-17) — Manual tracking workflow.
    # When the chosen courier has `manual_tracking: true`, the frontend
    # captures the AWB from the physical sticker and passes it here.
    # Falls back to sequential auto-generation if absent (preserves
    # original behaviour for every other courier).
    manual_tracking_id: Optional[str] = None


def _normalize_digits(s: str) -> str:
    """Convert Gujarati/Hindi digits to English."""
    if not s:
        return s
    gu = "૦૧૨૩૪૫૬૭૮૯"
    hi = "०१२३४५६७८९"
    out = []
    for ch in s:
        if ch in gu:
            out.append(str(gu.index(ch)))
        elif ch in hi:
            out.append(str(hi.index(ch)))
        else:
            out.append(ch)
    return "".join(out)


def parse_structured_paste(text: str) -> Dict[str, Any]:
    """Parse the fixed format the user pastes (from Custom GPT).

    Accepts BOTH multi-line AND single-line formats. Detects field
    keywords (NAME:, PHONE:, ADDRESS_1:, ...) regardless of newlines.
    Also accepts multi-word variants like "Order ID:" (space) or
    "Payment Mode:" — they are normalised to their canonical
    underscored form below.
    """
    text = _normalize_digits(text or "").strip()
    # Pre-normalise multi-word / hyphenated keys to their canonical
    # underscore form so the token regex below stays simple. The
    # lookahead ensures we only touch a key followed by ":".
    text = re.sub(r"(?i)\border[\s\-]+id(?=\s*:)", "ORDER_ID", text)
    text = re.sub(r"(?i)\bpayment[\s\-]+mode(?=\s*:)", "PAYMENT_MODE", text)
    text = re.sub(r"(?i)\bcustomer[\s\-]+name(?=\s*:)", "CUSTOMER_NAME", text)
    text = re.sub(r"(?i)\baddress[\s\-]+(\d)(?=\s*:)", r"ADDRESS_\1", text)
    # Phase-3 Smart Paste enhancement — multi-word GST/Email key variants.
    text = re.sub(r"(?i)\bgst[\s\-]*(?:no|number|num|in)(?=\s*:)", "GSTIN", text)
    text = re.sub(r"(?i)\be[\s\-]*mail(?=\s*:)", "EMAIL", text)
    text = re.sub(r"(?i)\bemail[\s\-]+id(?=\s*:)", "EMAIL", text)
    result: Dict[str, str] = {}
    confidence: Dict[str, str] = {}
    warnings: List[str] = []

    # Canonical field keys (order matters: longer keys first where ambiguous)
    FIELD_KEYS = [
        ("ADDRESS_1", "address_line1"),
        ("ADDRESS1", "address_line1"),
        ("ADDRESS_2", "address_line2"),
        ("ADDRESS2", "address_line2"),
        ("ADDRESS", "address_line1"),
        ("CUSTOMER_NAME", "customer_name"),
        ("NAME", "customer_name"),
        ("MOBILE", "customer_phone"),
        ("CONTACT", "customer_phone"),
        ("PHONE", "customer_phone"),
        ("ALT_PHONE", "customer_alt_phone"),
        ("ALTERNATE", "customer_alt_phone"),
        ("ALTERNATIVE", "customer_alt_phone"),
        ("CITY", "city"),
        ("STATE", "state"),
        ("PINCODE", "pincode"),
        ("PIN", "pincode"),
        ("ITEMS", "items"),
        ("ITEM", "items"),
        ("AMOUNT", "amount"),
        ("PRICE", "amount"),
        ("TOTAL", "amount"),
        ("TOKEN", "token_amount"),
        ("TOKEN_AMOUNT", "token_amount"),
        ("ADVANCE", "token_amount"),
        ("PAYMENT_MODE", "payment_mode"),
        ("PAYMENT", "payment_mode"),
        ("PAY", "payment_mode"),
        ("COURIER", "courier_hint"),
        ("ORDER_ID", "order_id_hint"),
        ("ORDER", "order_id_hint"),
        ("WEIGHT", "weight"),
        ("WT", "weight"),
        ("NOTES", "notes"),
        ("NOTE", "notes"),
        # Phase-3 Smart Paste enhancement — B2B fields.
        ("GSTIN", "customer_gstin"),
        ("GST", "customer_gstin"),
        ("EMAIL", "customer_email"),
        ("MAIL", "customer_email"),
    ]

    # Build a regex that matches "(KEY):" boundaries.
    keys_alt = "|".join(k for k, _ in FIELD_KEYS)
    pattern = re.compile(rf"\b({keys_alt})\s*:\s*", re.IGNORECASE)
    matches = list(pattern.finditer(text))

    for i, m in enumerate(matches):
        key_raw = m.group(1).upper()
        # find canonical mapping
        mapped = None
        for k, v in FIELD_KEYS:
            if k == key_raw:
                mapped = v
                break
        if not mapped:
            continue
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        val = text[start:end].strip().strip(",;").strip()
        # clean trailing punctuation
        val = re.sub(r"[\s,;.]+$", "", val)
        if val in ("-", "—", "_", "") or val.lower() in ("none", "null", "empty", "n/a", "na"):
            continue
        # Don't overwrite if already set (first occurrence wins)
        if mapped not in result:
            result[mapped] = val

    # Clean & normalize
    if "customer_phone" in result:
        digits = re.sub(r"\D", "", result["customer_phone"])
        if len(digits) > 10:
            digits = digits[-10:]
        result["customer_phone"] = digits
        confidence["customer_phone"] = "high" if len(digits) == 10 else "low"
        if len(digits) != 10:
            warnings.append("Phone number doesn't look like 10 digits")

    if "pincode" in result:
        m = re.search(r"\b(\d{6})\b", result["pincode"])
        if m:
            result["pincode"] = m.group(1)
            confidence["pincode"] = "high"
        else:
            confidence["pincode"] = "low"
            warnings.append("Pincode should be 6 digits")

    if "amount" in result:
        m = re.search(r"(\d+(?:\.\d+)?)", result["amount"].replace(",", ""))
        if m:
            try:
                result["amount"] = float(m.group(1))
                confidence["amount"] = "high"
            except Exception:
                confidence["amount"] = "low"
        else:
            result.pop("amount", None)

    if "payment_mode" in result:
        v = result["payment_mode"].upper()
        if "COD" in v or "CASH" in v or "નકદ" in v or "ડિલિવરી" in v:
            result["payment_mode"] = "COD"
        elif "PAID" in v or "PREPAID" in v or "UPI" in v or "ONLINE" in v:
            result["payment_mode"] = "PAID"
        else:
            result.pop("payment_mode", None)

    for field in ["customer_name", "address_line1", "city", "state", "items"]:
        if result.get(field):
            confidence.setdefault(field, "high")
        else:
            confidence[field] = "missing"

    # ── Phase-3 Smart Paste enhancement: GST + Email validation/opportunistic ──
    # Indian GSTIN format: 15 chars = 2-digit state code, 5 letters PAN entity,
    # 4-digit PAN serial, 1 letter PAN check, 1 entity number, "Z", 1 checksum.
    GSTIN_RE = re.compile(
        r"\b(\d{2}[A-Z]{5}\d{4}[A-Z][A-Z0-9]Z[A-Z0-9])\b"
    )
    EMAIL_RE = re.compile(
        r"\b([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})\b"
    )

    if "customer_gstin" in result:
        cleaned = re.sub(r"[^A-Za-z0-9]", "", result["customer_gstin"]).upper()
        m = GSTIN_RE.search(cleaned)
        if m:
            result["customer_gstin"] = m.group(1)
            confidence["customer_gstin"] = "high"
        else:
            # Bad GST shape — keep but flag low confidence.
            result["customer_gstin"] = cleaned
            confidence["customer_gstin"] = "low"
            warnings.append("GSTIN doesn't match the standard 15-character format")
    else:
        # Opportunistic: scan free text for a GST number even when no
        # explicit "GST:" label is present.
        scan = re.sub(r"[^A-Za-z0-9\s]", " ", text).upper()
        m = GSTIN_RE.search(scan)
        if m:
            result["customer_gstin"] = m.group(1)
            confidence["customer_gstin"] = "high"

    if "customer_email" in result:
        m = EMAIL_RE.search(result["customer_email"])
        if m:
            result["customer_email"] = m.group(1).lower()
            confidence["customer_email"] = "high"
        else:
            confidence["customer_email"] = "low"
            warnings.append("Email address doesn't look valid")
    else:
        # Opportunistic: scan free text for an email even without a label.
        m = EMAIL_RE.search(text)
        if m:
            result["customer_email"] = m.group(1).lower()
            confidence["customer_email"] = "high"

    # ── ORDER_ID HINT SANITISATION (2026-05-25) ──────────────────
    # Even when the operator pasted "Order: 380015" verbatim, the
    # value 380015 is almost certainly the customer's pincode that
    # was mis-labelled (or a leftover from a label template that
    # repeated the same number twice). Real shopkeeper order ids
    # carry a prefix ("AMZ-12345"), are alphanumeric ("OD9876"),
    # or are clearly longer / shorter than 6 digits.
    #
    # Rule A — Drop order_id_hint when it equals the parsed pincode
    #          (pincode wins; the user can re-enter the order id).
    # Rule B — Drop order_id_hint when it's a bare 6-digit numeric
    #          string (^\d{6}$). 6-digit standalone numbers are the
    #          canonical Indian pincode shape and never make valid
    #          order ids on their own.
    #
    # The downstream auto-generator (master_order_id) will assign a
    # fresh id when this field is empty, so dropping a wrong value
    # is strictly safer than keeping it.
    if "order_id_hint" in result:
        oid_raw = str(result.get("order_id_hint") or "").strip()
        pin_val = str(result.get("pincode") or "").strip()
        drop_reason = ""
        # Rule A — exact match with parsed pincode (after pincode
        # was already normalised to its 6-digit form above).
        if pin_val and oid_raw == pin_val:
            drop_reason = f"equals parsed pincode ({pin_val})"
        # Rule B — bare 6-digit numeric value (with no prefix /
        # suffix / non-digit char anywhere in the string).
        elif re.fullmatch(r"\d{6}", oid_raw):
            drop_reason = "bare 6-digit number (looks like a pincode, not an order id)"
        if drop_reason:
            logger.info(
                "parse_structured_paste: dropped order_id_hint=%r — %s",
                oid_raw, drop_reason,
            )
            result.pop("order_id_hint", None)
            warnings.append(
                f"Ignored '{oid_raw}' as order id — auto-generated id will be used."
            )

    return {"fields": result, "confidence": confidence, "warnings": warnings}






# ----------------------------------------------------------------------
# Duplicate detection — Smart Paste MVP Phase 2
# ----------------------------------------------------------------------

def _clean_phone(p: str) -> str:
    """Normalise a phone string to last 10 digits for robust matching."""
    if not p:
        return ""
    digits = "".join(c for c in str(p) if c.isdigit())
    return digits[-10:] if len(digits) >= 10 else digits


async def find_duplicate_matches(
    phone: Optional[str],
    order_id: Optional[str],
    user_id: Optional[str] = None,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """Return a list of duplicate candidates across pending orders and
    shipments. Matches are keyed on (a) last-10-digits of phone, and
    (b) exact order_id (case-insensitive trimmed).

    Each returned dict has:
      {kind: "pending"|"shipment", id, tracking_id?, customer_name,
       customer_phone, order_id, match_on: ["phone"|"order_id"|both],
       status, created_at}

    Results are deduped by id + sorted newest-first, capped at `limit`.
    """
    phone_norm = _clean_phone(phone or "")
    oid_norm = (order_id or "").strip()

    if not phone_norm and not oid_norm:
        return []

    # Build OR query across both keys.
    or_clauses: List[Dict[str, Any]] = []
    if phone_norm:
        # Stored phone may have +91 or spaces; match on ending substring.
        or_clauses.append({"customer_phone": {"$regex": f"{phone_norm}$"}})
    if oid_norm:
        # Case-insensitive exact match on order_id / order_id_hint.
        safe = re.escape(oid_norm)
        or_clauses.append({"order_id": {"$regex": f"^{safe}$", "$options": "i"}})
        or_clauses.append({"order_id_hint": {"$regex": f"^{safe}$", "$options": "i"}})

    query: Dict[str, Any] = {"$or": or_clauses} if or_clauses else {}
    if user_id:
        query["user_id"] = user_id

    # Pending orders (not yet shipped).
    pending_q = {**query, "status": {"$ne": "shipped"}}
    pending_cursor = (
        db.pending_orders.find(pending_q, {"_id": 0}).sort("created_at", -1).limit(limit)
    )
    pending_docs = await pending_cursor.to_list(limit)

    # Recent shipments (any status; UI can decide what to show).
    shipments_cursor = (
        db.shipments.find(query, {"_id": 0}).sort("created_at", -1).limit(limit)
    )
    shipment_docs = await shipments_cursor.to_list(limit)

    def _why(doc: Dict[str, Any]) -> List[str]:
        matched: List[str] = []
        if phone_norm:
            dp = _clean_phone(doc.get("customer_phone") or "")
            if dp and dp == phone_norm:
                matched.append("phone")
        if oid_norm:
            doid = (doc.get("order_id") or doc.get("order_id_hint") or "").strip().lower()
            if doid and doid == oid_norm.lower():
                matched.append("order_id")
        return matched

    results: List[Dict[str, Any]] = []
    for d in pending_docs:
        results.append({
            "kind": "pending",
            "id": d.get("id"),
            "customer_name": d.get("customer_name", ""),
            "customer_phone": d.get("customer_phone", ""),
            "order_id": d.get("order_id") or d.get("order_id_hint") or "",
            "status": d.get("status") or "pending",
            "created_at": d.get("created_at", ""),
            "match_on": _why(d),
        })
    for d in shipment_docs:
        results.append({
            "kind": "shipment",
            "id": d.get("id"),
            "tracking_id": d.get("tracking_id", ""),
            "customer_name": d.get("customer_name", ""),
            "customer_phone": d.get("customer_phone", ""),
            "order_id": d.get("order_id", ""),
            "status": d.get("status") or "",
            "created_at": d.get("created_at", ""),
            "match_on": _why(d),
        })
    # Sort newest first, cap at `limit` overall.
    results.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return results[:limit]














_CHAT_REQUIRED = ["NAME", "PHONE", "ADDRESS_1", "CITY", "STATE", "PINCODE", "AMOUNT", "WEIGHT"]
_CHAT_LABEL = {
    "NAME": "Name",
    "PHONE": "Phone",
    "ALT_PHONE": "Alt Phone",
    "ADDRESS_1": "Address",
    "ADDRESS_2": "Landmark",
    "CITY": "City",
    "STATE": "State",
    "PINCODE": "Pincode",
    "ITEMS": "Items",
    "AMOUNT": "Amount",
    "PAYMENT": "Payment",
    "COURIER": "Courier",
    "ORDER_ID": "Order ID",
    "WEIGHT": "Weight",
    "NOTES": "Notes",
    # Phase-3 Smart Paste enhancement.
    "EMAIL": "Email",
    "GSTIN": "GSTIN",
}


def _legacy_to_schema(legacy: Dict[str, Any]) -> Dict[str, str]:
    """Convert the app's snake_case field dict into the 15-line schema
    keys the LLM and regex parser share."""
    items = legacy.get("items")
    items_text = (
        ", ".join(items) if isinstance(items, list) else (str(items) if items else "")
    )
    amt = legacy.get("amount")
    amt_text = "" if amt in (None, "", 0) else str(amt)
    return {
        "NAME": legacy.get("customer_name", "") or "",
        "PHONE": legacy.get("customer_phone", "") or "",
        "ALT_PHONE": legacy.get("customer_alt_phone", "") or "",
        "ADDRESS_1": legacy.get("address_line1", "") or "",
        "ADDRESS_2": legacy.get("address_line2", "") or "",
        "CITY": legacy.get("city", "") or "",
        "STATE": legacy.get("state", "") or "",
        "PINCODE": legacy.get("pincode", "") or "",
        "ITEMS": items_text,
        "AMOUNT": amt_text,
        "PAYMENT": (legacy.get("payment_mode") or "").upper(),
        "COURIER": legacy.get("courier_name", "") or "",
        "ORDER_ID": legacy.get("order_id", "") or "",
        "WEIGHT": legacy.get("weight", "") or "",
        "NOTES": legacy.get("notes", "") or "",
        # Phase-3 Smart Paste enhancement.
        "EMAIL": legacy.get("customer_email", "") or "",
        "GSTIN": legacy.get("customer_gstin", "") or "",
    }


async def smart_paste_chat(
    payload: SmartPasteChatRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Conversational Smart Paste.

    The client sends the current known fields plus the user's latest
    natural-language reply. We build a synthetic structured block from
    the known fields, append the reply as fresh context, re-run the LLM
    parse (same pipeline as check-duplicate), and return:

      * fields          — merged dict in the app's snake_case schema
      * missing         — remaining REQUIRED keys (uppercase schema)
      * complete        — true when every REQUIRED key has a value
      * ai_message      — markdown-like bullet list the client renders
                          as a chat bubble
      * complexity / reason — from the LLM's classification
    """
    incoming = payload.fields or {}
    # Accept both snake_case (from previous parse) and UPPERCASE schema
    # keys so callers can round-trip without converting.
    if any(k.isupper() for k in incoming.keys()):
        schema = {k: str(incoming.get(k, "") or "") for k in _CHAT_LABEL.keys()}
    else:
        schema = _legacy_to_schema(incoming)

    # Phase-6 chat-aware short-circuits — when the user's last reply is
    # a direct answer to one specific missing field (e.g. they replied
    # just "3.5" or "500g" because we asked them for weight), accept it
    # *literally* without re-running the LLM. This prevents the parcel-
    # weight guard from wiping the answer (the guard correctly clears
    # weight values that aren't accompanied by a "parcel weight" label,
    # but in chat-context the user IS confirming the parcel weight).
    reply_raw = (payload.reply or "").strip()
    # Compute what was missing BEFORE this turn.
    pre_missing: List[str] = [
        k for k in _CHAT_REQUIRED
        if not (schema.get(k) or "").strip()
    ]

    # 1. Weight short-circuit — user replied with a number-ish thing AND
    #    weight was the ONLY (or first) missing required field.
    if (reply_raw and "WEIGHT" in pre_missing
            and not (schema.get("WEIGHT") or "").strip()):
        # Accept any of: "3.5", "3.5 kg", "500g", "500gm", "0.8 kgs"
        wt_match = re.fullmatch(
            r"\s*(\d+(?:[.,]\d+)?)\s*(g|gm|gms|kg|kgs|grams?|kilos?|kilograms?)?\s*",
            reply_raw, flags=re.IGNORECASE,
        )
        if wt_match:
            num = wt_match.group(1).replace(",", ".")
            unit = (wt_match.group(2) or "").lower()
            if not unit:
                # If user just typed "3.5" — assume kg if the number is
                # a small float (< 25), else grams. Couriers in India
                # almost always weigh in kg for anything > a few-hundred
                # grams, so this default is safe.
                try:
                    val = float(num)
                    unit = "kg" if val <= 25 else "g"
                except Exception:
                    unit = "kg"
            # Normalize unit display.
            if unit in ("gm", "gms", "grams", "gram"):
                unit = "g"
            elif unit in ("kgs", "kilos", "kilo", "kilograms", "kilogram"):
                unit = "kg"
            schema["WEIGHT"] = f"{num}{unit}"

    # Build the synthetic block so the LLM has full grounding — known
    # KEY: value lines first, then the user's freeform reply.
    lines: List[str] = []
    for k in _CHAT_LABEL.keys():
        v = (schema.get(k) or "").strip()
        if v:
            lines.append(f"{k}: {v}")
    synthetic = "\n".join(lines)
    reply = reply_raw
    # If our short-circuit already captured the weight from the reply,
    # tag the reply with an explicit "Parcel weight:" label so the
    # downstream parcel-weight guard won't clear it.
    if (schema.get("WEIGHT") or "").strip() and reply:
        # Marker line keeps the LLM from re-classifying the bare number.
        reply = f"Parcel weight: {schema['WEIGHT']}\n\n{reply}"
    combined = synthetic + ("\n\n" + reply if reply else "")

    # Re-parse with the user's custom instructions so their business
    # rules still apply.
    s = await db.settings.find_one(
        {"user_id": current_user["id"]},
        {"_id": 0, "smart_paste_instructions": 1, "smart_paste_ai_enabled": 1},
    ) or {}

    parsed = parse_structured_paste(combined)
    fields: Dict[str, Any] = dict(parsed.get("fields", {}) or {})

    ai_source = "regex"
    complexity = ""
    reason = ""
    try:
        if s.get("smart_paste_ai_enabled", True):
            ai = await parse_paste_via_llm(
                combined,
                custom_instructions=(s.get("smart_paste_instructions") or "").strip(),
            )
            if ai.get("source") == "llm":
                mapped = await _legacy_with_pincode_enrich(ai["fields"])
                for k, v in mapped.items():
                    if v:
                        fields[k] = v
                if isinstance(fields.get("amount"), str):
                    m = re.search(r"(\d+(?:\.\d+)?)", fields["amount"].replace(",", ""))
                    if m:
                        try:
                            fields["amount"] = float(m.group(1))
                        except Exception:
                            pass
                ai_source = "llm"
                complexity = ai.get("complexity", "")
                reason = ai.get("ai_reason", "")
    except Exception:
        logger.exception("LLM path failed on smart-paste/chat — using regex only")

    # Compute what's still required but missing.
    out_schema = _legacy_to_schema(fields)
    missing: List[str] = []
    for k in _CHAT_REQUIRED:
        v = (out_schema.get(k) or "").strip()
        if not v:
            missing.append(k)

    # Compose the AI chat bubble (template-based — fast, no extra LLM
    # call). Shows what we have, what we still need.
    known_bullets = []
    for k in _CHAT_LABEL.keys():
        v = (out_schema.get(k) or "").strip()
        if v and k != "NOTES":
            known_bullets.append(f"• {_CHAT_LABEL[k]}: {v}")
    known_block = "\n".join(known_bullets) if known_bullets else "• (nothing yet)"

    if missing:
        missing_block = "\n".join(
            f"• {_CHAT_LABEL[k]}" for k in missing
        )
        ai_message = (
            f"Got these so far:\n{known_block}\n\n"
            f"Still need:\n{missing_block}\n\n"
            f"Please share (you can type or tap 🎤 on the keyboard to speak)."
        )
    else:
        ai_message = (
            f"All set!\n{known_block}\n\nSaving the order now…"
        )

    return {
        "fields": fields,
        "missing": missing,
        "complete": len(missing) == 0,
        "ai_message": ai_message,
        "complexity": complexity,
        "reason": reason,
        "source": ai_source,
    }



async def smart_paste_photo(
    payload: SmartPastePhotoRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Image-based Smart Paste — accepts a base64 photo of any address
    source (handwritten paper, visiting card, packing slip, screenshot,
    Aadhaar, anything) and returns the SAME shape as /smart-paste/chat.
    Photo OCR is feature-gated by `smart_paste_image_ocr` and always
    billed as the "complex" tier (also for free-trial users — see user
    spec: trial accounts ship with 10 starter credits to cover this).
    """
    # 1. Feature gate.
    plan_key = (current_user.get("plan") or "free_trial").lower()
    if not current_user.get("is_admin"):
        plans_doc = await _get_plan_features_doc()
        allowed = plans_doc.get(plan_key, plans_doc.get("free_trial", []))
        if "smart_paste_image_ocr" not in allowed:
            raise HTTPException(
                status_code=403,
                detail="Photo OCR is not enabled on your plan.",
            )

    # 2. Trim/validate input.
    b64 = (payload.image_base64 or "").strip()
    if b64.startswith("data:"):
        comma = b64.find(",")
        if comma != -1:
            b64 = b64[comma + 1:]
    if len(b64) < 200:
        raise HTTPException(
            status_code=400, detail="Image looks empty / too small.",
        )
    if len(b64) > 16 * 1024 * 1024:
        raise HTTPException(
            status_code=413, detail="Image too large — please resize and retry.",
        )

    # 3. Wallet pre-flight (forced "complex" tier — applies to trial too).
    s = await db.settings.find_one(
        {"user_id": current_user["id"]},
        {"_id": 0, "smart_paste_instructions": 1, "smart_paste_ai_enabled": 1,
         "ai_cost_simple": 1, "ai_cost_medium": 1, "ai_cost_complex": 1},
    ) or {}
    if not s.get("smart_paste_ai_enabled", True):
        raise HTTPException(
            status_code=400, detail="Smart Paste AI is disabled in Settings.",
        )
    cfg = await _get_admin_config()
    global_rates = cfg.get("global_ai_rates") or DEFAULT_AI_RATES
    # Photo OCR is its own tier (default 1.5 cr) — admin tunable.
    # Falls back to "complex" rate if photo_ocr not configured.
    photo_cost = float(
        global_rates.get("photo_ocr",
                         global_rates.get("complex", DEFAULT_AI_RATES["photo_ocr"]))
    )
    bal = await wallet_balance(db, current_user["id"])
    if bal < photo_cost:
        raise HTTPException(
            status_code=402,
            detail=(
                f"Insufficient credits. Photo OCR costs {photo_cost:.2f} "
                f"credits — your balance is {bal:.2f}. "
                f"Top up from Wallet to continue."
            ),
        )

    # 4. Vision call (Gemini 2.5 Pro by default).
    ai = await parse_image_with_ai(
        image_base64=b64,
        mime=(payload.mime or "image/jpeg"),
        custom_instructions=(s.get("smart_paste_instructions") or "").strip(),
    )

    if ai.get("source") != "llm":
        raise HTTPException(
            status_code=502,
            detail=(
                "Photo could not be read — please try again with a clearer "
                "or brighter image."
            ),
        )

    fields, pincode_warnings = await _legacy_with_pincode_enrich_v2(ai["fields"])
    if isinstance(fields.get("amount"), str):
        m = re.search(r"(\d+(?:\.\d+)?)", fields["amount"].replace(",", ""))
        if m:
            try:
                fields["amount"] = float(m.group(1))
            except Exception:
                pass
    # Combine AI's own warnings (e.g. address auto-recovery) with
    # the pincode-mismatch warnings.
    photo_warnings = list(ai.get("warnings") or []) + list(pincode_warnings)

    # 5. Charge wallet — photo OCR debit (1.5 credits default).
    try:
        bd = LabelCostBreakdown(
            ai_credits=photo_cost,
            ai_complexity="photo_ocr",
            ai_applies=True,
            plan_has_room=True,
            shipment_credits=0.0,
            total=photo_cost,
        )
        await wallet_charge(
            db, current_user, f"photo-ocr-{int(datetime.utcnow().timestamp())}", bd,
        )
    except Exception:
        logger.exception("photo-ocr wallet charge failed (non-fatal)")

    # 6. Same response shape as /smart-paste/chat.
    out_schema = _legacy_to_schema(fields)
    missing: List[str] = []
    for k in _CHAT_REQUIRED:
        v = (out_schema.get(k) or "").strip()
        if not v:
            missing.append(k)

    known_bullets = []
    for k in _CHAT_LABEL.keys():
        v = (out_schema.get(k) or "").strip()
        if v and k != "NOTES":
            known_bullets.append(f"• {_CHAT_LABEL[k]}: {v}")
    known_block = "\n".join(known_bullets) if known_bullets else "• (nothing yet)"

    if missing:
        missing_block = "\n".join(f"• {_CHAT_LABEL[k]}" for k in missing)
        ai_message = (
            f"📷 Read the photo. Got these:\n{known_block}\n\n"
            f"Still need:\n{missing_block}\n\n"
            f"Please share (you can type or tap 🎤 on the keyboard to speak)."
        )
    else:
        ai_message = (
            f"✅ Photo decoded. Got everything!\n{known_block}\n\nSaving the order now…"
        )

    return {
        "fields": fields,
        "missing": missing,
        "complete": len(missing) == 0,
        "ai_message": ai_message,
        "complexity": ai.get("complexity", "complex"),
        "reason": ai.get("ai_reason", "vision call"),
        "source": "llm",
        "credits_charged": round(photo_cost, 2),
        "warnings": photo_warnings,
    }



async def smart_paste_create(
    payload: SmartPasteRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    Parse text → write to Google Sheet (Master) → save PendingOrder to Mongo.

    RULE: If the Google Sheet write fails, we DO NOT save to Mongo and
    return 502 so the client never sees a 'ghost' order that isn't in the
    source-of-truth sheet.
    """
    text = (payload.text or "")
    parsed = parse_structured_paste(text)
    fields: Dict[str, Any] = dict(parsed["fields"])

    # Fast path: when the caller has already collected every field (chat
    # flow posts a canonical `KEY: value` block), skip the LLM round-trip
    # entirely — saves 2–4 s on every save.
    skip_llm = bool(getattr(payload, "skip_llm", False))

    # Phase-4b+: merge LLM-extracted fields over the regex pass so raw
    # WhatsApp messages parse cleanly even without the 14-line format.
    try:
        s = await db.settings.find_one(
            {"user_id": current_user["id"]},
            {"_id": 0, "smart_paste_instructions": 1, "smart_paste_ai_enabled": 1},
        ) or {}
        if not skip_llm and s.get("smart_paste_ai_enabled", True):
            ai = await parse_paste_via_llm(
                text,
                custom_instructions=(s.get("smart_paste_instructions") or "").strip(),
            )
            if ai.get("source") == "llm":
                mapped = await _legacy_with_pincode_enrich(ai["fields"])
                for k, v in mapped.items():
                    if v:
                        fields[k] = v
                if isinstance(fields.get("amount"), str):
                    m = re.search(r"(\d+(?:\.\d+)?)", fields["amount"].replace(",", ""))
                    if m:
                        try:
                            fields["amount"] = float(m.group(1))
                        except Exception:
                            pass
                if isinstance(fields.get("token_amount"), str):
                    m = re.search(r"(\d+(?:\.\d+)?)", fields["token_amount"].replace(",", ""))
                    if m:
                        try:
                            fields["token_amount"] = float(m.group(1))
                        except Exception:
                            pass
    except Exception:
        logger.exception("LLM path failed on smart-paste create — using regex only")

    # ---- Phase-7d: Master Order ID + User Order ID ----
    # The Settings.order_id_auto_generate flag controls server stamping.
    # When ON: server allocates a fresh master_order_id; if user's own
    # order_id is blank, we copy master into it so both sheets always
    # have a value.
    # When OFF: user MUST supply their own order_id — we 422 if blank.
    settings_doc = await db.settings.find_one(
        {"user_id": current_user["id"]},
        {"_id": 0, "order_id_auto_generate": 1},
    ) or {}
    auto_gen = bool(settings_doc.get("order_id_auto_generate", True))
    # Handle both `order_id` and the regex parser's `order_id_hint` fallback.
    user_order_id = str(
        fields.get("order_id") or fields.get("order_id_hint") or "",
    ).strip()

    if auto_gen:
        master_oid = await generate_master_order_id()
        # Best-effort uniqueness guard — counter is atomic but defensive.
        retries = 0
        while await db.pending_orders.find_one({"master_order_id": master_oid}, {"_id": 1}):
            master_oid = await generate_master_order_id()
            retries += 1
            if retries > 5:
                raise HTTPException(
                    status_code=500,
                    detail="Could not allocate a unique Master Order ID — retry.",
                )
        fields["master_order_id"] = master_oid
        if not user_order_id:
            user_order_id = master_oid
        fields["order_id"] = user_order_id
    else:
        if not user_order_id:
            raise HTTPException(
                status_code=422,
                detail="Order ID is required when Auto-Generate is OFF. "
                       "Enter your own Order ID or enable Auto-Generate in Settings.",
            )
        fields["master_order_id"] = ""
        fields["order_id"] = user_order_id

    # ---- 1) Write to Google Master Sheet first (atomic) ----
    sheet_meta: Dict[str, Any] = {"ok": False}
    if sheet_append_order_row is not None:
        try:
            addr = " ".join(
                [fields.get("address_line1", ""), fields.get("address_line2", "")]
            ).strip()
            items_val = fields.get("items") or []
            item_type_text = (
                ", ".join(items_val) if isinstance(items_val, list) else str(items_val)
            )
            # Phase-B: pass the new columns (user_name, master_order_id,
            # alt_phone, token_amount, weight) for the extended Master Sheet.
            user_name_val = (
                current_user.get("full_name")
                or current_user.get("email", "").split("@")[0]
                or current_user.get("id", "")[:8]
            )
            sheet_meta = sheet_append_order_row(
                user_id=current_user["id"],
                user_name=user_name_val,
                master_order_id=fields.get("master_order_id", "") or "",
                order_id=fields.get("order_id", "") or "",
                name=fields.get("customer_name", "") or "",
                phone=fields.get("customer_phone", "") or "",
                alt_phone=fields.get("customer_alt_phone", "") or "",
                address=addr,
                city=fields.get("city", "") or "",
                state=fields.get("state", "") or "",
                pincode=fields.get("pincode", "") or "",
                item_type=item_type_text,
                amount=fields.get("amount", "") or "",
                token_amount=fields.get("token_amount", "") or "",
                weight=str(fields.get("weight", "") or ""),
                payment_mode=fields.get("payment_mode", "") or "",
                status="Pending",
                notice="via Smart Paste",
            )
            logger.info(f"Sheet append OK: {sheet_meta.get('updated_range')}")
        except Exception as e:
            logger.exception("Google Sheet write failed")
            raise HTTPException(
                status_code=502,
                detail=f"Google Sheet save failed — order not saved. Reason: {e}",
            )
    else:
        # Library missing — fail loudly so the user knows (Sheet is source of truth)
        raise HTTPException(
            status_code=503,
            detail="Google Sheets integration not configured on server.",
        )

    # ---- 1b) Phase-B / Phase-D: best-effort write to the user's OWN sheet ----
    # PHASE-D (2026-04-29): Disabled by default. The user's sheet is now
    # READ-ONLY from the system's perspective — orders are only written
    # to the central Master Sheet, and the user can pull a personal copy
    # via the "Restore My Orders" button (Phase-C).
    # The auto-mirror feature is gated behind a future Premium plan.
    # When that plan ships, set `admin_config.auto_write_user_sheet = true`
    # to re-enable system-side dual-write.
    user_sheet_meta: Dict[str, Any] = {"ok": False, "skipped": True, "reason": "auto-write disabled (Premium feature)"}
    try:
        adm_cfg = await db.admin_config.find_one(
            {"_id": "default"}, {"_id": 0, "auto_write_user_sheet": 1},
        ) or {}
        auto_write_user_sheet = bool(adm_cfg.get("auto_write_user_sheet", False))
    except Exception:
        auto_write_user_sheet = False
    if auto_write_user_sheet and sheet_append_user is not None:
        try:
            usr_settings = await db.settings.find_one(
                {"user_id": current_user["id"]},
                {"_id": 0, "sheet": 1},
            ) or {}
            user_sheet_cfg = (usr_settings.get("sheet") or {}) if isinstance(
                usr_settings.get("sheet"), dict
            ) else {}
            user_sheet_id = str(user_sheet_cfg.get("sheet_id") or "").strip()
            user_sheet_tab = str(user_sheet_cfg.get("gid") or user_sheet_cfg.get("tab") or "0").strip()
            if user_sheet_id:
                user_sheet_meta = sheet_append_user(
                    sheet_id=user_sheet_id,
                    tab_name=user_sheet_tab,
                    user_id=current_user["id"],
                    user_name=user_name_val,
                    master_order_id=fields.get("master_order_id", "") or "",
                    order_id=fields.get("order_id", "") or "",
                    name=fields.get("customer_name", "") or "",
                    phone=fields.get("customer_phone", "") or "",
                    alt_phone=fields.get("customer_alt_phone", "") or "",
                    address=addr,
                    city=fields.get("city", "") or "",
                    state=fields.get("state", "") or "",
                    pincode=fields.get("pincode", "") or "",
                    item_type=item_type_text,
                    amount=fields.get("amount", "") or "",
                    token_amount=fields.get("token_amount", "") or "",
                    weight=str(fields.get("weight", "") or ""),
                    payment_mode=fields.get("payment_mode", "") or "",
                    status="Pending",
                    notice="via Smart Paste",
                )
                logger.info(
                    f"User-sheet append OK: {user_sheet_meta.get('updated_range')}"
                )
        except Exception as e:
            # Non-fatal — Master Sheet succeeded. Log + continue.
            logger.warning(f"User-sheet write skipped: {e}")
            user_sheet_meta = {"ok": False, "error": str(e)}

    # ---- 2) Now save locally (Mongo) so the app can show the queue fast ----
    # Extract the row number from the append response so we can later
    # soft-delete that exact row if the user deletes from the app.
    sheet_row_num: Optional[int] = None
    if sheet_parse_row_from_updated_range is not None:
        try:
            sheet_row_num = sheet_parse_row_from_updated_range(
                sheet_meta.get("updated_range")
            )
        except Exception:
            sheet_row_num = None

    po = PendingOrder(
        source="paste",
        raw_text=(payload.text or "")[:2000],
        confidence=parsed["confidence"],
        warnings=parsed["warnings"],
        sheet_row_num=sheet_row_num,
        custom_values={
            k: ("" if v is None else str(v))
            for k, v in (payload.custom_values or {}).items()
            if v not in (None, "")
        },
        **{k: v for k, v in fields.items() if k in PendingOrder.model_fields
           and k not in ("sheet_row_num", "custom_values")},
    )
    # Phase-21 — Tag the row as a REPEAT customer if this phone has
    # already been shipped at least once by the same user. Cheap
    # indexed query; never raises.
    try:
        po.is_repeat_customer = await detect_repeat_customer(
            current_user["id"], po.customer_phone,
        )
    except Exception:
        po.is_repeat_customer = False
    # Stash sheet-write metadata on the model's raw_text for debugging if needed
    doc = po.model_dump()
    doc["_sheet_meta"] = sheet_meta
    doc["user_id"] = current_user["id"]
    await db.pending_orders.insert_one(doc)
    # Best-effort: write per-user custom field values to the user's
    # personal Google Sheet (column letters from user_custom_fields).
    # Failures are swallowed inside the helper — never blocks the order.
    try:
        await _write_custom_values_to_user_sheet_bg(
            current_user, po.custom_values or {},
        )
    except Exception:
        logger.exception("custom_values write to user sheet failed (smart-paste)")
    return po


async def sheets_probe():
    """Quick debug endpoint — verifies Service Account can read the Master Sheet."""
    if sheet_probe_connection is None:
        return {"ok": False, "error": "gspread not installed"}
    return sheet_probe_connection()


# ---------------------------------------------------------------------------
# Feature 1: Write mapped headers + custom-field headers to the user's sheet
# ---------------------------------------------------------------------------
# When a user maps logical fields (name, phone, etc) to columns of their
# own Google Sheet, only THEY know which header-name belongs in row 1. We
# make it one tap:
#   • Collect the (column_letter, header_name) pairs from their current
#     mapping AND any custom fields they've defined.
#   • Write them to row 1 of their sheet, skipping cells that already
#     have a non-blank header (so user-authored wording is preserved).
class SyncHeadersPayload(BaseModel):
    # Optional overrides — callers may pass a bespoke header list,
    # otherwise we derive from the user's saved settings.
    headers: Optional[List[Dict[str, str]]] = None
    # When true, skip even blank cells if a custom field's column
    # letter is already used by a mapped built-in field (avoid
    # overwriting a mapping). Defaults to False — caller decides.
    dry_run: bool = False


# Human-readable header text per mappable field — shown in the
# user's sheet row 1 when they tap "Write Headers to Sheet".
_MAPPED_FIELD_HEADERS: Dict[str, str] = {
    "timestamp": "Timestamp",
    "order_id": "Order ID",
    "customer_name": "Name",
    "phone": "Phone",
    "address": "Address",
    "city": "City",
    "state": "State",
    "pincode": "Pincode",
    "item": "Item",
    "amount": "Amount",
    "payment_mode": "Payment Mode",
    "weight": "Weight",
    "alt_phone": "Alt Phone",
    "token_amount": "Token Amount",
}


async def sync_sheet_headers(
    payload: SyncHeadersPayload,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Write the user's mapped + custom-field header names into row 1
    of their connected Google Sheet. Only fills blank cells (existing
    non-blank headers are preserved).

    Returns the list of (column, name) pairs actually written + the
    list of cells we skipped (already had a value).
    """
    if sheet_sync_user_sheet_headers is None:
        raise HTTPException(status_code=503, detail="Sheets integration not configured")

    doc = await db.settings.find_one({"user_id": current_user["id"]}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=400, detail="Settings not configured")
    s = Settings(**doc)
    cfg = s.sheet
    if not cfg.sheet_id:
        raise HTTPException(status_code=400, detail="Google Sheet not connected")

    # Build the list of (column_letter, header_name) to write.
    items: List[tuple] = []
    if payload.headers:
        # Explicit override.
        for item in payload.headers:
            col = (item.get("column") or "").strip().upper()
            name = (item.get("name") or "").strip()
            if col and name:
                items.append((col, name))
    else:
        # Derive from saved mapping + custom fields.
        mapping = cfg.column_mapping or {}
        sheet_headers = cfg.headers or []
        for field_key, col_name in mapping.items():
            if not col_name:
                continue
            # `col_name` is the USER'S current header wording; we map
            # back to a column letter by finding the index in saved
            # `headers`.
            try:
                idx = sheet_headers.index(col_name)
            except ValueError:
                continue
            letter = _idx_to_col_letter(idx)
            items.append((letter, _MAPPED_FIELD_HEADERS.get(field_key, field_key.title())))

        # Custom fields — each has an explicit column letter.
        custom_fields = (
            await db.user_custom_fields.find(
                {"user_id": current_user["id"], "active": {"$ne": False}},
                {"_id": 0},
            ).sort("sort_order", 1).to_list(100)
        )
        for cf in custom_fields:
            col = (cf.get("column_letter") or "").strip().upper()
            name = (cf.get("name") or "").strip()
            if col and name:
                items.append((col, name))

    if payload.dry_run:
        return {"ok": True, "dry_run": True, "would_write": items}

    try:
        result = sheet_sync_user_sheet_headers(
            cfg.sheet_id, cfg.gid or "0",
            headers_to_write=items,
        )
    except Exception as e:
        logger.exception("sync_sheet_headers failed")
        raise HTTPException(status_code=502, detail=f"Header sync failed: {e}")

    return {
        "ok": True,
        "written_count": len(result.get("written", [])),
        "skipped_count": len(result.get("skipped", [])),
        "written": [{"column": c, "name": n} for (c, n) in result.get("written", [])],
        "skipped": [
            {"column": c, "name": n, "existing": existing}
            for (c, n, existing) in result.get("skipped", [])
        ],
    }


def _idx_to_col_letter(idx: int) -> str:
    """0 → A, 1 → B, ..., 25 → Z, 26 → AA."""
    idx = int(idx)
    letters = ""
    n = idx + 1
    while n > 0:
        n, r = divmod(n - 1, 26)
        letters = chr(ord("A") + r) + letters
    return letters


# ---------------------------------------------------------------------------
# Feature 2: Custom Fields (plan-gated)
# ---------------------------------------------------------------------------
# Plan-based per-user custom fields. Users on Gold can define up to 3,
# Platinum up to 5. Admins can raise / lower the caps via admin_config.
# An admin ON/OFF switch per plan lives in feature_registry — the same
# kill-switch pattern the other features use.
CUSTOM_FIELD_DEFAULT_LIMITS: Dict[str, int] = {
    "free_trial": 0,
    "silver": 0,
    "gold": 3,
    "platinum": 5,
}


async def _get_custom_field_limit(user: Dict[str, Any]) -> int:
    """Admin override > plan default. Admins get unlimited (999)."""
    if user.get("is_admin"):
        return 999
    adm_cfg = await db.admin_config.find_one(
        {"_id": "default"}, {"_id": 0, "custom_field_limits": 1},
    ) or {}
    overrides = adm_cfg.get("custom_field_limits") or {}
    plan_key = (user.get("plan") or "free_trial").lower()
    if plan_key in overrides:
        try:
            return int(overrides[plan_key])
        except (TypeError, ValueError):
            pass
    return CUSTOM_FIELD_DEFAULT_LIMITS.get(plan_key, 0)


# [refactor] CustomFieldCreate/Update models moved to routers/custom_fields.py


# [refactor] Contact-Save Settings + VCF endpoints moved to routers/custom_fields.py


# [refactor] Per-user Custom Fields CRUD moved to routers/custom_fields.py


# --- Best-effort: write a shipment's custom-field values to the user's
#     personal Google Sheet at the column letters they configured.
async def _write_custom_values_to_user_sheet_bg(
    current_user: Dict[str, Any],
    custom_values: Dict[str, str],
) -> None:
    """Fire-and-forget: writes `custom_values` into the user's personal
    sheet at column letters defined in `user_custom_fields`. Maps
    custom-field-id → value → column-letter.

    Behaviour:
      • Empty `custom_values` → silent no-op.
      • Sheet not connected → silent no-op (will sync next time the
        user opens /sheets/orders).
      • Quota / 5xx → swallowed + logged; the master-sheet retry
        worker doesn't cover the user-sheet path because the master
        sheet IS the canonical record; user-sheet is a convenience
        mirror.
    """
    if not custom_values or sheet_append_row_cells_to_user_sheet is None:
        return
    try:
        s_doc = await db.settings.find_one(
            {"user_id": current_user["id"]}, {"_id": 0, "sheet": 1},
        )
        sheet_cfg = (s_doc or {}).get("sheet") or {}
        sheet_id = sheet_cfg.get("sheet_id")
        if not sheet_id:
            return  # user hasn't connected a sheet — nothing to do
        gid = sheet_cfg.get("gid") or "0"
        # Build {column_letter: value} from {custom_field_id: value} via
        # the user's custom-field definitions.
        cf_docs = await db.user_custom_fields.find(
            {"user_id": current_user["id"], "active": {"$ne": False}},
            {"_id": 0, "id": 1, "column_letter": 1, "name": 1},
        ).to_list(50)
        id_to_letter = {cf["id"]: cf["column_letter"] for cf in cf_docs}
        cells: Dict[str, str] = {}
        for fid, val in custom_values.items():
            letter = id_to_letter.get(fid)
            if not letter:
                continue
            sval = "" if val is None else str(val)
            if not sval:
                continue
            cells[letter] = sval
        if not cells:
            return
        sheet_append_row_cells_to_user_sheet(sheet_id, gid, cells)
    except Exception as e:
        # Non-fatal — master-sheet backup is the source of truth. Log
        # and move on; the next user-sheet → master sync will pick up
        # any rows that ended up in master but missing from user-sheet.
        logger.warning(
            "Custom values to user-sheet write failed (best-effort): %s",
            str(e)[:200],
        )


# [refactor] Admin custom-field-limits moved to routers/custom_fields.py














# ---------------------- Phase-3a Plans & Usage ----------------------




async def my_usage(current_user: Dict[str, Any] = Depends(get_current_user)):
    """Current plan + live usage counters. Safe to poll on screen focus."""
    return await usage_summary(db, current_user)


# ---------------------- Phase-4d Notification Prefs + Subscription mgmt ----

DEFAULT_NOTIFICATION_PREFS = {
    "trial_ending":     True,   # 3-day-before alert when on trial
    "plan_expiring":    True,   # 7-day-before alert for paid plans
    "low_credits":      True,   # ≤ 5 credits remaining warning
    "payment_success":  True,   # receipt after successful Razorpay payment
    "daily_summary":    False,  # opt-in daily digest
    "channel_push":     True,   # delivery channel — push (default)
    "channel_email":    True,   # delivery channel — email (default)
    # Phase G6 — Operational push events. Each defaults ON so the
    # user starts seeing actionable alerts immediately.
    "sla_breach":         True,  # new SLA breach raised
    "daily_limit_warn":   True,  # WhatsApp daily limit ≥80%
    "morning_reminder":   True,  # 8am IST daily pending bulk-msg digest
    "new_order":          True,  # placeholder for Sheet auto-sync (Phase H)
    "low_wallet":         True,  # wallet balance < ₹100 warning
}


def _coerce_notif_prefs(raw: Optional[Dict[str, Any]]) -> Dict[str, bool]:
    raw = raw or {}
    out: Dict[str, bool] = {}
    for k, default in DEFAULT_NOTIFICATION_PREFS.items():
        out[k] = bool(raw.get(k, default))
    return out



# `_push_event` helper STAYS here because the SLA cron worker calls
# it. The push_sender module-level import that used to live next to
# the (now-relocated) push-token endpoints needs to stay reachable
# at module scope — otherwise this helper raises NameError
# `_push_sender is not defined` on every SLA scan. (Caught by the
# Phase-5d regression test on 2026-05-07.)
import push_sender as _push_sender  # noqa: E402

# Helper used by SLA engine + cron jobs to send a push only when the
# user opted in to that event type. Centralized here so we have one
# place to add throttling / digest logic later.
async def _push_event(
    user_ids: Iterable[str],
    *,
    event_key: str,
    title: str,
    body: str,
    data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Filter user_ids by their notification_prefs[event_key] AND
    channel_push toggle, then dispatch via push_sender."""
    user_ids = list(user_ids)
    if not user_ids:
        return {"sent": 0, "errors": 0, "pruned": 0, "total": 0, "filtered": 0}
    cursor = db.users.find(
        {"id": {"$in": user_ids}},
        {"_id": 0, "id": 1, "notification_prefs": 1},
    )
    eligible: List[str] = []
    async for u in cursor:
        prefs = _coerce_notif_prefs(u.get("notification_prefs"))
        if prefs.get("channel_push", True) and prefs.get(event_key, True):
            eligible.append(u["id"])
    res = await _push_sender.send_push_to_users(
        db, eligible,
        title=title, body=body,
        data={**(data or {}), "event_key": event_key},
        channel_id="default",
    )
    res["filtered"] = len(user_ids) - len(eligible)
    return res






# ---------------------- Phase-4a Credit Wallet ----------------------












# ──────────────────────────────────────────────────────────────────
# Plan-Feature Admin (Phase-5)
# ──────────────────────────────────────────────────────────────────
# An admin (the very first signed-up user; flag `is_admin=True`) can tick or
# untick which features each plan should expose. The mapping is stored in a
# tiny `plan_features` collection — one document with id="default" holding a
# dict[plan_key -> list[feature_key]]. Every regular user fetches their own
# plan's allowed list via /me/feature-flags and the frontend renders/hides
# UI accordingly.
from feature_registry import (
    FEATURE_REGISTRY, ALL_KEYS, DEFAULT_PLAN_FEATURES, get_registry_payload
)


async def user_has_feature(user: dict, feature_key: str) -> bool:
    """Server-side gate. Returns True if `user` is allowed to use
    `feature_key` based on their plan and the admin's plan_features
    configuration. Admins bypass all gates.

    Use this from any backend endpoint or background task to enforce
    plan limits — e.g.,
        if not await user_has_feature(current_user, "sheet_two_way_status_sync"):
            return  # silently skip, or raise 403 depending on context
    """
    if not user:
        return False
    if user.get("is_admin"):
        return True
    plan = (user.get("plan") or "free_trial").lower()
    plans = await _get_plan_features_doc()
    enabled = set(plans.get(plan, []) or [])
    return feature_key in enabled


# Phase-21 — Repeat-customer detector for pending-order ingestion.
# Called by smart-paste / file-import / webhook ingestion right before
# inserting a new pending_orders row. Returns True when this customer
# (by normalised phone) has previously been shipped at least once for
# the same user. Used by the UI to paint a REPEAT badge on the card.
#
# Cheap by design: indexed query on shipments.{user_id, phone_norm}
# with a 1-doc projection + limit. Falls back to False on ANY error so
# ingestion never breaks because of a marker lookup.
def _norm_phone(raw: str) -> str:
    """Strip non-digits, return the last 10 digits (Indian mobiles)."""
    if not raw:
        return ""
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    return digits[-10:] if len(digits) >= 10 else digits


async def detect_repeat_customer(user_id: str, phone: str) -> bool:
    """Return True if `phone` matches an earlier shipment of `user_id`.

    Matches against `shipments.phone` after digit-normalisation (last
    10 digits), so '+91 90999 00008' matches '9099900008'. Safe to
    call from any ingestion path; never raises.
    """
    norm = _norm_phone(phone)
    if not norm or len(norm) < 6:
        return False
    try:
        # Use a regex anchored at the END of the phone field so any
        # stored format (with/without +91, spaces, dashes) still hits.
        # Phase-21 fix — Shipment docs store the customer phone under
        # `customer_phone`; the legacy field name `phone` is unused.
        # Use an `$or` to be defensive against either naming so the
        # detector keeps working even if the schema is migrated later.
        rx = {"$regex": f"{norm}$"}
        doc = await db.shipments.find_one(
            {"user_id": user_id, "$or": [{"customer_phone": rx}, {"phone": rx}]},
            {"_id": 1},
        )
        return doc is not None
    except Exception:
        return False


async def _get_plan_features_doc() -> Dict[str, List[str]]:
    """Fetch (or seed) the plan->features mapping.

    On first read, seeds the doc with DEFAULT_PLAN_FEATURES.

    On subsequent reads:
      • Always keeps Platinum in sync with ALL_KEYS so power users get
        every newly-shipped feature automatically.
      • For NEW feature keys that didn't exist when the doc was first
        seeded, auto-injects them into each plan's saved list IF the
        registry's DEFAULT_PLAN_FEATURES says that feature should be on
        for that plan. This way the admin sees a sensible pre-state in
        the panel for new features without having to tick 14 boxes
        across 3 plans manually after a deploy. Tracked via a
        `known_keys` field on the doc — once a key has been migrated
        for a plan, subsequent admin un-ticks are respected (we never
        re-inject a key the admin has explicitly removed).
    """
    doc = await db.plan_features.find_one({"_id": "default"})
    if not doc:
        # First-time seed
        seeded = {p: list(v) for p, v in DEFAULT_PLAN_FEATURES.items()}
        await db.plan_features.insert_one({
            "_id": "default",
            "plans": seeded,
            "known_keys": list(ALL_KEYS),
        })
        return seeded

    plans = doc.get("plans", {}) or {}
    known_keys: set = set(doc.get("known_keys") or [])
    new_keys: set = set(ALL_KEYS) - known_keys

    dirty = False

    # ── Migration A: auto-inject defaults for brand-new feature keys ──
    if new_keys:
        for plan_key, plan_defaults in DEFAULT_PLAN_FEATURES.items():
            current_set = set(plans.get(plan_key, []))
            for nk in new_keys:
                # Only inject if the registry says this plan should
                # have it on by default.
                if nk in plan_defaults and nk not in current_set:
                    current_set.add(nk)
            plans[plan_key] = list(current_set)
        dirty = True

    # ── Migration B: Platinum always gets EVERY current key ──
    plat_set = set(plans.get("platinum", []))
    if not plat_set.issuperset(ALL_KEYS):
        plat_set.update(ALL_KEYS)
        plans["platinum"] = list(plat_set)
        dirty = True

    # ── Migration C: ensure every plan slug exists ──
    for k in DEFAULT_PLAN_FEATURES.keys():
        if k not in plans:
            plans[k] = list(DEFAULT_PLAN_FEATURES[k])
            dirty = True

    if dirty:
        await db.plan_features.update_one(
            {"_id": "default"},
            {"$set": {"plans": plans, "known_keys": list(set(ALL_KEYS))}},
            upsert=True,
        )
    elif new_keys:
        # No plan changes but mark keys as known so we don't re-evaluate
        # injection on every read.
        await db.plan_features.update_one(
            {"_id": "default"},
            {"$set": {"known_keys": list(set(ALL_KEYS))}},
        )
    return plans


def _require_admin(current_user: Dict[str, Any]) -> None:
    if not current_user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")


async def admin_get_plan_features(
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """Returns the registry (all toggleable features + categories) AND the
    current plan->features mapping so the admin UI can render checkboxes."""
    _require_admin(current_user)
    plans = await _get_plan_features_doc()
    return {
        "registry": get_registry_payload(),
        "plans": plans,  # { "free_trial": [...], "silver": [...], ... }
    }


# ───────────── Admin — Users directory (Phase-4d) ─────────────
# GET /admin/users and GET /admin/users/{user_id} have moved to
# /app/backend/routers/admin.py (Phase-2 incremental refactor).
# This block is intentionally empty — kept as a breadcrumb so future
# `grep "admin/users"` lands on the right file.


class PlanFeaturesPayload(BaseModel):
    plans: Dict[str, List[str]]


async def admin_put_plan_features(
    payload: PlanFeaturesPayload,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Saves the full plan->features mapping. Unknown feature keys are
    silently dropped so a stale frontend can't poison the data."""
    _require_admin(current_user)
    valid_keys = set(ALL_KEYS)
    cleaned: Dict[str, List[str]] = {}
    for plan_key in DEFAULT_PLAN_FEATURES.keys():
        # Accept only known plan slugs; ignore unknown ones to keep schema tidy.
        keys = payload.plans.get(plan_key, [])
        cleaned[plan_key] = [k for k in keys if k in valid_keys]
    await db.plan_features.update_one(
        {"_id": "default"},
        {"$set": {"plans": cleaned}},
        upsert=True,
    )
    return {"ok": True, "plans": cleaned}


# ───────────── Admin — Plan Limits (Phase-13) ─────────────
# Lets the admin tune every numeric knob inside a plan (label cap, bulk
# max, daily cap, trial days) WITHOUT a code push. All values are stored
# in `admin_config.plan_limits` and layered on top of the hardcoded
# defaults in /app/backend/plans.py via resolve_plan(...). Marketing copy
# (name, feel, tagline, badge) is NEVER overrideable.

_PLAN_KEYS_ORDER = ["free_trial", "silver", "gold", "platinum"]


class PlanLimitsRow(BaseModel):
    """One plan's editable numeric fields. Optional — unset = keep default."""
    label_cap: Optional[int] = None
    bulk_max: Optional[int] = None
    daily_cap: Optional[int] = None  # 0 or None = no daily cap
    price_inr: Optional[int] = None
    trial_days: Optional[int] = None  # only meaningful for free_trial
    packing_variant_cap: Optional[int] = None  # Phase 2: max variants per courier
    # Phase A — Team Members
    team_member_cap: Optional[int] = None         # free included team members
    extra_member_price_inr: Optional[int] = None  # ₹/month for an extra slot


class PlanLimitsPayload(BaseModel):
    plans: Dict[str, PlanLimitsRow]


async def admin_get_plan_limits(
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Returns per-plan defaults + admin overrides. Frontend renders the
    override value in each input; empty field = fall back to default."""
    _require_admin(current_user)
    doc = await db.admin_config.find_one(
        {"_id": "default"}, {"_id": 0, "plan_limits": 1}
    ) or {}
    overrides = doc.get("plan_limits") or {}

    defaults_out: Dict[str, Dict[str, Any]] = {}
    merged_out: Dict[str, Dict[str, Any]] = {}
    for key in _PLAN_KEYS_ORDER:
        spec = PLAN_TABLE[key]
        defaults_out[key] = {
            "name": spec.name,
            "label_cap": spec.label_cap,
            "bulk_max": spec.bulk_max,
            "daily_cap": spec.daily_cap,
            "price_inr": spec.price_inr,
            "trial_days": spec.trial_days,
            "period": spec.period,
            "packing_variant_cap": spec.packing_variant_cap,
            "team_member_cap": spec.team_member_cap,
            "extra_member_price_inr": spec.extra_member_price_inr,
        }
        # Merge the stored override (if any) on top so the UI shows
        # the EFFECTIVE current value — not the original default.
        ov = overrides.get(key) or {}
        merged_out[key] = {
            "label_cap":  ov.get("label_cap",  spec.label_cap),
            "bulk_max":   ov.get("bulk_max",   spec.bulk_max),
            "daily_cap":  ov.get("daily_cap",  spec.daily_cap),
            "price_inr":  ov.get("price_inr",  spec.price_inr),
            "trial_days": ov.get("trial_days", spec.trial_days),
            "packing_variant_cap": ov.get("packing_variant_cap", spec.packing_variant_cap),
            "team_member_cap": ov.get("team_member_cap", spec.team_member_cap),
            "extra_member_price_inr": ov.get("extra_member_price_inr", spec.extra_member_price_inr),
        }
    return {
        "order": _PLAN_KEYS_ORDER,
        "defaults": defaults_out,
        "current": merged_out,
        "overrides": overrides,  # raw, for debugging
    }


async def admin_put_plan_limits(
    payload: PlanLimitsPayload,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Persists per-plan numeric overrides. Only the 5 editable fields are
    saved; marketing copy and `period`/`badge` are ignored even if sent.
    Empty / null / negative values fall back to the defaults (stored as
    no-op — the field is omitted from the override dict)."""
    _require_admin(current_user)
    cleaned: Dict[str, Dict[str, Any]] = {}
    for plan_key in _PLAN_KEYS_ORDER:
        row = payload.plans.get(plan_key)
        if row is None:
            continue
        spec = PLAN_TABLE[plan_key]
        out: Dict[str, Any] = {}

        # label_cap, bulk_max, price_inr, packing_variant_cap,
        # team_member_cap, extra_member_price_inr — non-negative ints.
        for f, default_v in (
            ("label_cap",  spec.label_cap),
            ("bulk_max",   spec.bulk_max),
            ("price_inr",  spec.price_inr),
            ("packing_variant_cap", spec.packing_variant_cap),
            ("team_member_cap", spec.team_member_cap),
            ("extra_member_price_inr", spec.extra_member_price_inr),
        ):
            v = getattr(row, f)
            if v is None:
                continue
            try:
                iv = int(v)
                if iv < 0:
                    raise ValueError
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400,
                    detail=f"{plan_key}.{f} must be a non-negative integer",
                )
            # Store override only if it differs from the default (keeps
            # the admin_config document compact).
            if iv != default_v:
                out[f] = iv

        # daily_cap: None / 0 / int — 0 means "no cap"
        if row.daily_cap is not None:
            try:
                dc = int(row.daily_cap)
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400,
                    detail=f"{plan_key}.daily_cap must be an integer or 0",
                )
            normalised = None if dc <= 0 else dc
            if normalised != spec.daily_cap:
                out["daily_cap"] = normalised

        # trial_days: only meaningful for free_trial but we still accept it
        if row.trial_days is not None:
            try:
                td = int(row.trial_days)
                if td < 0:
                    raise ValueError
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400,
                    detail=f"{plan_key}.trial_days must be a non-negative integer",
                )
            if td != (spec.trial_days or 0):
                out["trial_days"] = td

        if out:
            cleaned[plan_key] = out

    await db.admin_config.update_one(
        {"_id": "default"},
        {"$set": {"plan_limits": cleaned}},
        upsert=True,
    )
    # Return the effective merged view so the UI can refresh instantly.
    return await admin_get_plan_limits(current_user)


async def admin_reset_plan_limits(
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """One-click "restore defaults" — wipes all plan-limit overrides so
    the hardcoded values in plans.py take over again."""
    _require_admin(current_user)
    await db.admin_config.update_one(
        {"_id": "default"},
        {"$set": {"plan_limits": {}}},
        upsert=True,
    )
    return await admin_get_plan_limits(current_user)


# ──────────── Admin — WhatsApp Manual Messaging Pricing (Phase-14) ─────
# Per-plan credits charged when a user taps the WhatsApp "Send" button
# on a shipment (delivery confirmation, dispatch notification, etc.).
# These messages are MANUAL (user opens WhatsApp via wa.me deeplink and
# hits send themselves) — we just book-keep the charge. A future phase
# will layer an SMS/WhatsApp API gateway on top of the same pricing
# table, so the schema is designed to be forward-compatible.
#
# Defaults are 0 credits on every plan — admin opts in by raising the
# rate. When the rate is 0, no debit happens (stays free).

_WA_PLAN_ORDER = ["free_trial", "silver", "gold", "platinum"]
_WA_DEFAULT_RATES: Dict[str, float] = {
    "free_trial": 0.0,
    "silver":     0.0,
    "gold":       0.0,
    "platinum":   0.0,
}

# Default credit cost for generating ONE batch of 9 AI templates
# (3 languages × 3 variants). Higher plans pay less. Admin can override
# any of these via PUT /admin/whatsapp-pricing.
_WA_AI_DEFAULT_RATES: Dict[str, float] = {
    "free_trial": 2.0,
    "silver":     1.5,
    "gold":       1.0,
    "platinum":   0.5,
}

# Anti-block daily WhatsApp send limits. Admin can adjust.
_WA_DEFAULT_DAILY_LIMIT = 50           # max messages per user per day
_WA_DEFAULT_WARN_PCT    = 90           # soft-warning kicks in at 90% of limit
_WA_DEFAULT_ALLOW_OVERRIDE = True      # allow user to push past limit with confirm


class WhatsAppPricingRow(BaseModel):
    """Per-plan rate in CREDITS (not ₹) charged per manual message."""
    per_message_credits: Optional[float] = None
    ai_generation_credits: Optional[float] = None  # NEW: per 9-template batch


class WhatsAppPricingPayload(BaseModel):
    enabled: Optional[bool] = None
    plans: Dict[str, WhatsAppPricingRow]
    # NEW — anti-block daily limit knobs (admin-controlled)
    daily_limit: Optional[int] = None
    daily_warning_pct: Optional[int] = None
    allow_override_after_limit: Optional[bool] = None


async def _load_whatsapp_pricing() -> Dict[str, Any]:
    """Read the stored WhatsApp pricing doc, merging defaults so the
    caller never has to care which keys are missing."""
    doc = await db.admin_config.find_one(
        {"_id": "default"}, {"_id": 0, "whatsapp_pricing": 1}
    ) or {}
    saved = doc.get("whatsapp_pricing") or {}
    rates: Dict[str, float] = dict(_WA_DEFAULT_RATES)
    for k, v in (saved.get("rates") or {}).items():
        if k in rates and isinstance(v, (int, float)) and v >= 0:
            rates[k] = float(v)
    ai_rates: Dict[str, float] = dict(_WA_AI_DEFAULT_RATES)
    for k, v in (saved.get("ai_generation_rates") or {}).items():
        if k in ai_rates and isinstance(v, (int, float)) and v >= 0:
            ai_rates[k] = float(v)
    daily_limit = int(saved.get("daily_limit") or _WA_DEFAULT_DAILY_LIMIT)
    if daily_limit < 1:
        daily_limit = _WA_DEFAULT_DAILY_LIMIT
    warn_pct = int(saved.get("daily_warning_pct") or _WA_DEFAULT_WARN_PCT)
    if not (1 <= warn_pct <= 100):
        warn_pct = _WA_DEFAULT_WARN_PCT
    allow_override = bool(saved.get(
        "allow_override_after_limit", _WA_DEFAULT_ALLOW_OVERRIDE,
    ))
    return {
        "enabled":                    bool(saved.get("enabled", False)),
        "rates":                      rates,
        "ai_generation_rates":        ai_rates,
        "daily_limit":                daily_limit,
        "daily_warning_pct":          warn_pct,
        "allow_override_after_limit": allow_override,
    }


async def admin_get_whatsapp_pricing(
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Returns the current enabled-flag and per-plan credit rates, plus
    a copy of the defaults so the UI can render "Reset to default"."""
    _require_admin(current_user)
    data = await _load_whatsapp_pricing()
    return {
        "order":    _WA_PLAN_ORDER,
        "defaults": {"enabled": False, "rates": dict(_WA_DEFAULT_RATES)},
        "current":  data,
    }


async def admin_put_whatsapp_pricing(
    payload: WhatsAppPricingPayload,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Saves the admin's WhatsApp pricing. Accepts fractional credits
    (e.g. 0.5) so admins can set half-credit rates for cheap messaging."""
    _require_admin(current_user)
    cleaned_rates: Dict[str, float] = {}
    cleaned_ai_rates: Dict[str, float] = {}
    for plan_key in _WA_PLAN_ORDER:
        row = payload.plans.get(plan_key)
        if row is None:
            continue
        if row.per_message_credits is not None:
            try:
                v = float(row.per_message_credits)
                if v < 0:
                    raise ValueError
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400,
                    detail=f"{plan_key}.per_message_credits must be ≥ 0",
                )
            cleaned_rates[plan_key] = round(v, 2)
        if row.ai_generation_credits is not None:
            try:
                v = float(row.ai_generation_credits)
                if v < 0:
                    raise ValueError
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400,
                    detail=f"{plan_key}.ai_generation_credits must be ≥ 0",
                )
            cleaned_ai_rates[plan_key] = round(v, 2)

    # Default enabled = whatever's stored already (don't flip implicitly)
    current = await _load_whatsapp_pricing()
    enabled = (
        bool(payload.enabled)
        if payload.enabled is not None
        else current["enabled"]
    )

    daily_limit = current["daily_limit"]
    if payload.daily_limit is not None:
        try:
            dl = int(payload.daily_limit)
            if dl < 1 or dl > 10000:
                raise ValueError
            daily_limit = dl
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400,
                detail="daily_limit must be between 1 and 10000",
            )
    daily_warn_pct = current["daily_warning_pct"]
    if payload.daily_warning_pct is not None:
        try:
            wp = int(payload.daily_warning_pct)
            if not (1 <= wp <= 100):
                raise ValueError
            daily_warn_pct = wp
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400,
                detail="daily_warning_pct must be 1-100",
            )
    allow_override = current["allow_override_after_limit"]
    if payload.allow_override_after_limit is not None:
        allow_override = bool(payload.allow_override_after_limit)

    await db.admin_config.update_one(
        {"_id": "default"},
        {"$set": {
            "whatsapp_pricing": {
                "enabled":                    enabled,
                "rates":                      {**current["rates"], **cleaned_rates},
                "ai_generation_rates":        {**current["ai_generation_rates"], **cleaned_ai_rates},
                "daily_limit":                daily_limit,
                "daily_warning_pct":          daily_warn_pct,
                "allow_override_after_limit": allow_override,
            },
        }},
        upsert=True,
    )
    return await admin_get_whatsapp_pricing(current_user)


async def me_get_whatsapp_pricing(
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Read-only endpoint for the normal user's app — exposes the
    per-message rate for the current user's own plan, the AI-generation
    rate for the current user's plan, and the daily-limit / override
    knobs. The UI uses this to show "X credits will be charged",
    surface warnings as the daily count climbs, and decide whether to
    let the user push past the daily limit with a confirm."""
    data = await _load_whatsapp_pricing()
    plan_key = (current_user.get("plan") or "free_trial")
    rate = data["rates"].get(plan_key, 0.0)
    ai_rate = data["ai_generation_rates"].get(plan_key, 0.0)
    return {
        "enabled":                    data["enabled"],
        "plan":                       plan_key,
        "per_message_credits":        rate,
        "ai_generation_credits":      ai_rate,
        "daily_limit":                data["daily_limit"],
        "daily_warning_pct":          data["daily_warning_pct"],
        "allow_override_after_limit": data["allow_override_after_limit"],
    }


# ─────────────────────────────────────────────────────────────────────
# Phase-G1: Stage Rules — Unified SLA + Alert + Template + Escalation
# ─────────────────────────────────────────────────────────────────────
# Admin-configurable behaviour for each of the 6 fixed pipeline stages.
# Drives:
#   • SLA breach detection (background job → internal alert)
#   • Auto customer messaging (Delivered, Feedback)
#   • Cooldown / escalation logic
# Read by /me/stage-rules, written by /admin/stage-rules.

from stage_rules import (
    DEFAULT_STAGE_RULES,
    DEFAULT_STAGE_RULES_DOC,
    STAGES,
    STAGE_TO_TEMPLATE,
    StageRulesPayload,
    merge_with_defaults as _merge_stage_rules,
    normalise_phone as _normalise_alert_phone,
)


async def _load_stage_rules() -> Dict[str, Any]:
    doc = await db.admin_config.find_one(
        {"_id": "default"}, {"_id": 0, "stage_rules": 1},
    ) or {}
    return _merge_stage_rules(doc.get("stage_rules") or {})


async def admin_get_stage_rules(
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Returns the stored stage_rules doc (with defaults merged in)
    plus the canonical defaults so the UI can offer a 'Reset' button
    per stage."""
    _require_admin(current_user)
    return {
        "current":  await _load_stage_rules(),
        "defaults": DEFAULT_STAGE_RULES_DOC,
        "stages":   STAGES,
        "stage_to_template": STAGE_TO_TEMPLATE,
    }


async def admin_put_stage_rules(
    payload: StageRulesPayload,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Save the stage_rules doc. Every field is optional — values left
    out preserve their current stored value. Admin-only."""
    _require_admin(current_user)
    current = await _load_stage_rules()

    # Stages — partial update per stage. Unknown stage names are
    # silently dropped to keep the schema canonical.
    if payload.stages:
        for stage_name, cfg in payload.stages.items():
            if stage_name not in STAGES:
                continue
            current["stages"][stage_name] = {
                **current["stages"][stage_name],
                **{k: v for k, v in cfg.dict().items() if v is not None},
            }

    # Recipients — admin number, team list, app push uuids.
    if payload.alert_admin_number is not None:
        current["alert_admin_number"] = _normalise_alert_phone(
            payload.alert_admin_number,
        )
    if payload.alert_team_numbers is not None:
        cleaned = [
            _normalise_alert_phone(p) for p in (payload.alert_team_numbers or [])
        ]
        current["alert_team_numbers"] = [p for p in cleaned if p]
    if payload.alert_app_user_ids is not None:
        current["alert_app_user_ids"] = [
            str(u).strip() for u in (payload.alert_app_user_ids or []) if str(u).strip()
        ]

    # Master kill-switch.
    if payload.global_enabled is not None:
        current["global_enabled"] = bool(payload.global_enabled)

    # Display channels — partial update; missing keys keep existing.
    if payload.display_channels is not None:
        current["display_channels"] = {
            **(current.get("display_channels") or {}),
            **{k: bool(v) for k, v in payload.display_channels.items()
               if k in ("list", "banner", "push")},
        }

    # Scan interval — clamp [15, 240] minutes.
    if payload.scan_interval_minutes is not None:
        try:
            current["scan_interval_minutes"] = max(
                15, min(240, int(payload.scan_interval_minutes)),
            )
        except (TypeError, ValueError):
            pass

    # Default cooldown — clamp [1, 168] hours.
    if payload.default_cooldown_hours is not None:
        try:
            current["default_cooldown_hours"] = max(
                1, min(168, int(payload.default_cooldown_hours)),
            )
        except (TypeError, ValueError):
            pass

    await db.admin_config.update_one(
        {"_id": "default"},
        {"$set": {"stage_rules": current}},
        upsert=True,
    )
    return await admin_get_stage_rules(current_user)


async def me_get_stage_rules(
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Read-only view for the user-side UI — exposes stages + their
    SLA / template settings. Recipient phone numbers are HIDDEN from
    non-admin callers (only `_admin_visible` flag tells the UI whether
    the admin section should be linked)."""
    rules = await _load_stage_rules()
    is_admin = bool(current_user.get("is_admin"))
    if not is_admin:
        # Strip recipient PII for non-admin views.
        rules = {**rules}
        rules.pop("alert_admin_number", None)
        rules.pop("alert_team_numbers", None)
        rules.pop("alert_app_user_ids", None)
    return {
        "stages":             STAGES,
        "stage_to_template":  STAGE_TO_TEMPLATE,
        "rules":              rules,
        "is_admin":           is_admin,
    }


# ──────────────────────────────────────────────────────────────────
# SLA Engine — Phase G3
# ──────────────────────────────────────────────────────────────────
# Background scanner runs every `scan_interval_minutes` (admin config).
# Writes one doc to `sla_alerts` per (shipment, stage) breach with
# cooldown + escalation applied. Endpoints below let the admin view,
# dismiss, and manually trigger the scan.

import sla_engine as _sla_engine  # noqa: E402

# Latest scan stats are kept in-memory (single-process backend).
_SLA_LAST_RUN: Dict[str, Any] = {
    "ran_at":        None,
    "alerts_raised": 0,
    "users_scanned": 0,
    "next_run_at":   None,
    "running":       False,
}


def _alert_to_public(
    doc: Dict[str, Any],
    *,
    fresh_phones: Optional[List[str]] = None,
    fresh_contacts: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """Strip Mongo internals & PII for non-admin callers.

    If `fresh_phones` is provided we override the phones cached on the
    alert with the admin's CURRENT stage_rules contact numbers — this
    way alerts always route to whatever number the admin most recently
    configured (rather than whatever was stored when the breach was
    first raised, which may be stale demo data).

    `fresh_contacts` (Phase A) carries [{name, phone, role}] team
    members. When non-empty the WhatsApp UI prefers it over the bare
    phones array — falling back gracefully when no team is configured.
    """
    out = {k: v for k, v in doc.items() if k != "_id"}
    if "id" not in out and "_id" in doc:
        out["id"] = str(doc["_id"])
    if fresh_phones is not None:
        out["phones"] = list(fresh_phones)
    if fresh_contacts is not None:
        out["contacts"] = list(fresh_contacts)
    return out


def _current_alert_phones(rules: Dict[str, Any]) -> List[str]:
    """Builds the admin's CURRENT contact list (admin number first,
    then team numbers), de-duped and stripped — used to override
    phones cached on older alert documents so the UI always shows
    the latest configured numbers."""
    phones: List[str] = []
    admin_n = str(rules.get("alert_admin_number") or "").strip()
    if admin_n:
        phones.append(admin_n)
    for n in (rules.get("alert_team_numbers") or []):
        n = str(n or "").strip()
        if n:
            phones.append(n)
    seen = set()
    out: List[str] = []
    for p in phones:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


async def admin_sla_run_now(
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Trigger an SLA scan immediately. Returns the freshly-raised count."""
    _require_admin(current_user)
    if _SLA_LAST_RUN.get("running"):
        return {
            "ok":      False,
            "message": "A scan is already in progress.",
            "stats":   _SLA_LAST_RUN,
        }
    try:
        _SLA_LAST_RUN["running"] = True
        stats = await _sla_engine.scan_all_users(db)
        _SLA_LAST_RUN.update({
            "ran_at":        stats.get("ran_at"),
            "alerts_raised": int(stats.get("alerts_raised") or 0),
            "users_scanned": int(stats.get("users_scanned") or 0),
        })
        return {"ok": True, "stats": _SLA_LAST_RUN}
    finally:
        _SLA_LAST_RUN["running"] = False


async def admin_sla_alerts(
    stage: Optional[str] = None,
    dismissed: Optional[bool] = None,
    user_id: Optional[str] = None,
    limit: int = 100,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Recent SLA alerts across ALL users (admin view)."""
    _require_admin(current_user)
    q: Dict[str, Any] = {}
    if stage:
        q["stage"] = stage
    if dismissed is not None:
        q["dismissed"] = bool(dismissed)
    if user_id:
        q["user_id"] = user_id
    cursor = (
        db.sla_alerts.find(q, {"_id": 0})
        .sort("raised_at", -1)
        .limit(max(1, min(500, int(limit or 100))))
    )
    rows = await cursor.to_list(length=500)
    # Override stale phones with admin's CURRENT contact list so the
    # WhatsApp shortcuts always route to numbers they recognise.
    rules = await _load_stage_rules()
    fresh = _current_alert_phones(rules)
    # Phase A — fetch staff contacts (per alert.user_id) for richer
    # name+role+phone display in the SLA card buttons.
    from routers.team_members import get_team_contacts
    contacts_by_user: Dict[str, List[Dict[str, str]]] = {}
    for r in rows:
        uid = r.get("user_id")
        if uid and uid not in contacts_by_user:
            contacts_by_user[uid] = await get_team_contacts(db, uid)
    return {
        "alerts": [
            _alert_to_public(
                r,
                fresh_phones=fresh,
                fresh_contacts=contacts_by_user.get(r.get("user_id") or "", []),
            )
            for r in rows
        ],
        "stats":  _SLA_LAST_RUN,
    }


async def admin_sla_dismiss(
    alert_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Mark a single alert as resolved/dismissed."""
    _require_admin(current_user)
    res = await db.sla_alerts.update_one(
        {"$or": [{"id": alert_id}, {"shipment_id": alert_id}]},
        {"$set": {
            "dismissed":      True,
            "dismissed_at":   utcnow_iso(),
            "dismissed_by":   current_user.get("email") or current_user.get("id"),
        }},
    )
    return {"ok": True, "matched": res.matched_count, "modified": res.modified_count}


async def admin_sla_dismiss_bulk(
    payload: Dict[str, Any] = Body(...),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Dismiss many alerts at once. Body: {"ids":[...]} or {"stage":"..."}."""
    _require_admin(current_user)
    q: Dict[str, Any] = {}
    ids = payload.get("ids") or []
    if ids:
        q["id"] = {"$in": [str(i) for i in ids]}
    elif payload.get("stage"):
        q["stage"] = str(payload["stage"])
        q["dismissed"] = False
    else:
        raise HTTPException(status_code=400, detail="ids[] or stage required")
    res = await db.sla_alerts.update_many(
        q,
        {"$set": {
            "dismissed":      True,
            "dismissed_at":   utcnow_iso(),
            "dismissed_by":   current_user.get("email") or current_user.get("id"),
        }},
    )
    return {"ok": True, "modified": res.modified_count}


async def me_sla_alerts(
    stage: Optional[str] = None,
    dismissed: Optional[bool] = False,
    limit: int = 100,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Alerts for the CURRENT user — used by the dashboard 'Action
    Required' banner widget. Honours `display_channels.banner` toggle:
    when admin has disabled the banner channel, returns empty list
    so the widget self-hides."""
    rules = await _load_stage_rules()
    if not (rules.get("display_channels") or {}).get("banner", True):
        return {"alerts": [], "channels": rules.get("display_channels"), "muted": True}
    q: Dict[str, Any] = {"user_id": current_user["id"]}
    if stage:
        q["stage"] = stage
    if dismissed is not None:
        q["dismissed"] = bool(dismissed)
    cursor = (
        db.sla_alerts.find(q, {"_id": 0})
        .sort("raised_at", -1)
        .limit(max(1, min(500, int(limit or 100))))
    )
    rows = await cursor.to_list(length=500)
    fresh = _current_alert_phones(rules)
    # Phase A — staff contacts for richer name+role+phone display.
    from routers.team_members import get_team_contacts
    contacts = await get_team_contacts(db, current_user["id"])
    return {
        "alerts":   [
            _alert_to_public(r, fresh_phones=fresh, fresh_contacts=contacts)
            for r in rows
        ],
        "channels": rules.get("display_channels"),
        "muted":    False,
    }


async def admin_sla_summary(
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Per-stage breach counts + last-scan stats for the admin dashboard."""
    _require_admin(current_user)
    pipeline = [
        {"$match": {"dismissed": False}},
        {"$group": {"_id": "$stage", "count": {"$sum": 1}}},
    ]
    by_stage: Dict[str, int] = {}
    async for row in db.sla_alerts.aggregate(pipeline):
        by_stage[row["_id"]] = int(row["count"])
    total_open = sum(by_stage.values())
    total_alerts = await db.sla_alerts.count_documents({})
    return {
        "by_stage":     by_stage,
        "total_open":   total_open,
        "total_all":    total_alerts,
        "last_run":     _SLA_LAST_RUN,
    }




# [refactor] Feature-flag endpoints moved to routers/feature_flags.py


# ──────────────────────────────────────────────────────────────────
# Global Admin Config (Phase-5b)
# ──────────────────────────────────────────────────────────────────
# A single document `admin_config` stores config that the admin manages
# for the WHOLE app — currently:
#   - global_ai_rates  : per-complexity credits charged for Smart Paste
#   - credit_packages  : configurable top-up bundles with bonus credits
#
# Regular users never see the editor; they only consume the values via
# /api/credit-packages and /api/me/ai-rates (read-only).

DEFAULT_AI_RATES = {"simple": 0.5, "medium": 1.0, "complex": 2.0, "photo_ocr": 1.5}


# ───────────── Razorpay (Phase-4c real payments) ─────────────
import razorpay as _razorpay  # noqa: E402

_RZP_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "").strip()
_RZP_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "").strip()
_RZP_WEBHOOK_SECRET = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "").strip()
_rzp_client = (
    _razorpay.Client(auth=(_RZP_KEY_ID, _RZP_KEY_SECRET))
    if _RZP_KEY_ID and _RZP_KEY_SECRET
    else None
)




# Phase-4a refactor note: RazorpayVerifyRequest is re-declared here
# (a duplicate of routers/wallet.py's copy) because the
# /plans/razorpay/verify endpoint that lives in this file still needs
# it. Both models are tiny, identical request schemas — the duplication
# is intentional and harmless. When /plans/razorpay/verify itself
# moves out of server.py, this duplicate can disappear.








# ───────────── Razorpay — Plan Subscriptions (Phase-4d) ─────────────
#
# Reuses the same Razorpay infra as wallet top-ups but stores
# `purpose: plan_subscription` in the order doc so the verify and
# webhook handlers can branch on intent.
#
# Pricing is read live from admin_config.plan_pricing so the admin
# can change prices any time (the source of truth is the same as the
# /plans-pricing payload the frontend reads).



def _plan_billing_meta(
    plan_pricing: Dict[str, Any],
    plan_key: str,
    billing_cycle: str,
) -> Dict[str, Any]:
    """Resolve price + duration for a plan + cycle pair against
    admin_config.plan_pricing. Raises 400 if invalid combo."""
    if plan_key not in ("silver", "gold", "platinum"):
        raise HTTPException(status_code=400, detail=f"Cannot subscribe to plan '{plan_key}'")
    if billing_cycle not in ("monthly", "yearly"):
        raise HTTPException(status_code=400, detail="billing_cycle must be 'monthly' or 'yearly'")
    pp = (plan_pricing or {}).get(plan_key) or {}
    if billing_cycle == "monthly":
        price = int(pp.get("monthly_price") or 0)
        months = 1
        bonus_months = 0
    else:
        price = int(pp.get("yearly_price") or 0)
        months = int(pp.get("yearly_base_months") or 12)
        bonus_months = int(pp.get("yearly_bonus_months") or 0)
    if price <= 0:
        raise HTTPException(
            status_code=400,
            detail="This plan/cycle is not priced. Please contact support.",
        )
    return {
        "price_inr": price,
        "months": months,
        "bonus_months": bonus_months,
        "total_months": months + bonus_months,
    }




def _extend_plan_expiry(
    current_expires_at: Optional[str],
    months: int,
    bonus_months: int,
) -> str:
    """Compute the new plan_expires_at after a successful subscription
    payment. If the user is already on a paid plan that hasn't expired,
    we extend FROM the current expiry (carry-over). Otherwise from now.
    Result is an ISO-8601 string.
    """
    now = datetime.now(timezone.utc)
    base = now
    if current_expires_at:
        try:
            existing = datetime.fromisoformat(str(current_expires_at).replace("Z", "+00:00"))
            if existing.tzinfo is None:
                existing = existing.replace(tzinfo=timezone.utc)
            if existing > now:
                base = existing
        except (ValueError, TypeError):
            base = now
    new_expiry = base + relativedelta(months=int(months) + int(bonus_months))
    return new_expiry.isoformat()




DEFAULT_CREDIT_PACKAGES = [
    # amount_inr → credits credited (with bonus). label optional.
    {"amount_inr": 100,  "credits": 100,  "bonus": 0,  "label": "Starter",   "popular": False},
    {"amount_inr": 500,  "credits": 520,  "bonus": 20, "label": "Saver",     "popular": True},
    {"amount_inr": 1000, "credits": 1080, "bonus": 80, "label": "Value",     "popular": False},
    {"amount_inr": 2000, "credits": 2200, "bonus": 200,"label": "Pro",       "popular": False},
]

# Plan pricing matrix — admin tunable. Yearly = monthly × 12 × 0.75 + 1
# bonus month included (so users get 12 + 1 months access). The frontend
# displays the "+1 month FREE" sticker to make the bonus obvious instead
# of just saying "13 months".
DEFAULT_PLAN_PRICING = {
    "free_trial": {
        "monthly_price": 0,    "monthly_anchor": 0,
        "yearly_price": 0,     "yearly_anchor": 0,
        "yearly_base_months": 12, "yearly_bonus_months": 0,
        "show_strikethrough": False,
    },
    "silver": {
        # Existing plan in app: ₹199/month
        "monthly_price": 199,  "monthly_anchor": 499,
        # 199 × 12 × 0.75 = 1,791 → user pays 1,791 for 12 + 1 months
        "yearly_price": 1791,  "yearly_anchor": 4999,
        "yearly_base_months": 12, "yearly_bonus_months": 1,
        "show_strikethrough": True,
    },
    "gold": {
        # Existing plan: ₹499/month
        "monthly_price": 499,  "monthly_anchor": 999,
        # 499 × 12 × 0.75 = 4,491 → user pays 4,491 for 12 + 1 months
        "yearly_price": 4491,  "yearly_anchor": 9999,
        "yearly_base_months": 12, "yearly_bonus_months": 1,
        "show_strikethrough": True,
    },
    "platinum": {
        # Existing plan: ₹999/month
        "monthly_price": 999,  "monthly_anchor": 1999,
        # 999 × 12 × 0.75 = 8,991 → user pays 8,991 for 12 + 1 months
        "yearly_price": 8991,  "yearly_anchor": 19999,
        "yearly_base_months": 12, "yearly_bonus_months": 1,
        "show_strikethrough": True,
    },
}

# Countdown timer config — admin can choose mode.
DEFAULT_COUNTDOWN = {
    "enabled": True,
    "mode": "per_device",        # "off" | "per_device" | "global"
    "countdown_minutes": 60,     # used in per_device mode
    "global_expires_at": None,   # ISO datetime for global mode
    "headline": "Limited time offer — save up to 60%",
}


async def _get_admin_config() -> Dict[str, Any]:
    doc = await db.admin_config.find_one({"_id": "default"})
    if not doc:
        seeded = {
            "global_ai_rates": dict(DEFAULT_AI_RATES),
            "credit_packages": list(DEFAULT_CREDIT_PACKAGES),
            "plan_pricing":    {k: dict(v) for k, v in DEFAULT_PLAN_PRICING.items()},
            "countdown":       dict(DEFAULT_COUNTDOWN),
            # Phase-B: admin-managed Master Sheet defaults (override env vars).
            "master_sheet_id":  os.getenv("MASTER_SHEET_ID", "") or "",
            "master_sheet_tab": os.getenv("MASTER_SHEET_TAB", "") or "Sheet1",
        }
        await db.admin_config.insert_one({"_id": "default", **seeded})
        return seeded
    # Patch missing keys when new fields are introduced.
    out = {
        "global_ai_rates": doc.get("global_ai_rates") or dict(DEFAULT_AI_RATES),
        "credit_packages": doc.get("credit_packages") or list(DEFAULT_CREDIT_PACKAGES),
        "plan_pricing":    doc.get("plan_pricing")    or {k: dict(v) for k, v in DEFAULT_PLAN_PRICING.items()},
        "countdown":       doc.get("countdown")       or dict(DEFAULT_COUNTDOWN),
        "master_sheet_id":  doc.get("master_sheet_id",  os.getenv("MASTER_SHEET_ID", "")) or "",
        "master_sheet_tab": doc.get("master_sheet_tab", os.getenv("MASTER_SHEET_TAB", "")) or "Sheet1",
    }
    return out


class GlobalConfigPayload(BaseModel):
    global_ai_rates:    Optional[Dict[str, float]]    = None
    credit_packages:    Optional[List[Dict[str, Any]]] = None
    plan_pricing:       Optional[Dict[str, Dict[str, Any]]] = None
    countdown:          Optional[Dict[str, Any]]      = None
    master_sheet_id:    Optional[str]                 = None
    master_sheet_tab:   Optional[str]                 = None


async def admin_put_global_config(
    payload: GlobalConfigPayload,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    _require_admin(current_user)
    update: Dict[str, Any] = {}
    if payload.global_ai_rates is not None:
        # Clamp & sanitise so the admin can't accidentally store negatives.
        rates = payload.global_ai_rates
        update["global_ai_rates"] = {
            "simple":    max(0.0, min(50.0, float(rates.get("simple",    DEFAULT_AI_RATES["simple"])))),
            "medium":    max(0.0, min(50.0, float(rates.get("medium",    DEFAULT_AI_RATES["medium"])))),
            "complex":   max(0.0, min(50.0, float(rates.get("complex",   DEFAULT_AI_RATES["complex"])))),
            "photo_ocr": max(0.0, min(50.0, float(rates.get("photo_ocr", DEFAULT_AI_RATES["photo_ocr"])))),
        }
    if payload.credit_packages is not None:
        cleaned: List[Dict[str, Any]] = []
        for p in payload.credit_packages or []:
            try:
                amount = max(1, int(round(float(p.get("amount_inr", 0)))))
                credits = max(1, float(p.get("credits", amount)))
                bonus = max(0.0, credits - amount)
                cleaned.append({
                    "amount_inr": amount,
                    "credits": round(credits, 2),
                    "bonus": round(bonus, 2),
                    "label": str(p.get("label", "") or "")[:40],
                    "popular": bool(p.get("popular")),
                })
            except (ValueError, TypeError):
                continue
        # Sort smallest amount first for predictable UI ordering.
        cleaned.sort(key=lambda x: x["amount_inr"])
        update["credit_packages"] = cleaned
    if payload.plan_pricing is not None:
        # Validate & sanitise plan pricing. Free trial is always 0/0.
        cleaned_pp: Dict[str, Dict[str, Any]] = {}
        defaults = DEFAULT_PLAN_PRICING
        for k in ("free_trial", "silver", "gold", "platinum"):
            src = payload.plan_pricing.get(k) or {}
            base = defaults[k]
            try:
                if k == "free_trial":
                    cleaned_pp[k] = {
                        "monthly_price": 0,
                        "monthly_anchor": 0,
                        "yearly_price": 0,
                        "yearly_anchor": 0,
                        "yearly_base_months": 12,
                        "yearly_bonus_months": 0,
                        "show_strikethrough": False,
                    }
                    continue
                mp = max(0, int(round(float(src.get("monthly_price", base["monthly_price"])))))
                ma = max(0, int(round(float(src.get("monthly_anchor", base["monthly_anchor"])))))
                yp = max(0, int(round(float(src.get("yearly_price", base["yearly_price"])))))
                ya = max(0, int(round(float(src.get("yearly_anchor", base["yearly_anchor"])))))
                yb = max(1, int(round(float(src.get("yearly_base_months", 12)))))
                ybonus = max(0, int(round(float(src.get("yearly_bonus_months", 1)))))
                cleaned_pp[k] = {
                    "monthly_price": mp,
                    "monthly_anchor": ma,
                    "yearly_price": yp,
                    "yearly_anchor": ya,
                    "yearly_base_months": yb,
                    "yearly_bonus_months": ybonus,
                    "show_strikethrough": bool(src.get("show_strikethrough", base["show_strikethrough"])),
                }
            except (ValueError, TypeError):
                cleaned_pp[k] = dict(base)
        update["plan_pricing"] = cleaned_pp
    if payload.countdown is not None:
        c = payload.countdown
        mode = str(c.get("mode", "per_device") or "per_device")
        if mode not in ("off", "per_device", "global"):
            mode = "per_device"
        try:
            mins = max(1, min(60 * 24 * 30, int(round(float(c.get("countdown_minutes", DEFAULT_COUNTDOWN["countdown_minutes"]))))))
        except (ValueError, TypeError):
            mins = DEFAULT_COUNTDOWN["countdown_minutes"]
        update["countdown"] = {
            "enabled": bool(c.get("enabled", True)),
            "mode": mode,
            "countdown_minutes": mins,
            "global_expires_at": c.get("global_expires_at") or None,
            "headline": str(c.get("headline", DEFAULT_COUNTDOWN["headline"]) or "")[:120],
        }
    # Phase-B: admin can set Master Sheet ID + tab via the panel.
    if payload.master_sheet_id is not None:
        update["master_sheet_id"] = str(payload.master_sheet_id).strip()
    if payload.master_sheet_tab is not None:
        update["master_sheet_tab"] = str(payload.master_sheet_tab).strip() or "Sheet1"
    if update:
        await db.admin_config.update_one(
            {"_id": "default"}, {"$set": update}, upsert=True,
        )
    return await _get_admin_config()








# ---------------------- App setup ----------------------

# Phase-12 modular: messaging (courier rules, WhatsApp templates,
# dispatch confirmation flow). Mounted FIRST so its specific routes
# (e.g. /shipments/dispatch-confirmation) win over api_router's
# wildcard /shipments/{shipment_id}.
try:
    from routers.messaging import (
        messaging_router as _messaging_router,
        init as _init_messaging_router,
    )
    _init_messaging_router()
    app.include_router(_messaging_router)
except Exception as _msg_exc:
    logger.exception(f"Failed to mount messaging router: {_msg_exc}")

# Phase 2.5 — Courier Billing Reports router. Late-bound `init()`
# closes over `db` + `get_current_user` so the reports module doesn't
# need to circular-import them at top-level.
try:
    from routers.reports import (
        reports_router as _reports_router,
        init as _init_reports_router,
    )
    _init_reports_router()
    app.include_router(_reports_router)
except Exception as _rep_exc:
    print(f"Failed to mount reports router: {_rep_exc!r}")

# Phase A — Team Members (staff contacts + per-feature permissions
# stored, plus plan-cap + extra-member purchase flow). The router
# closes over `db` and `get_current_user` so it doesn't need to
# circular-import them.
try:
    from routers.team_members import build_router as _build_team_members_router
    app.include_router(_build_team_members_router(db, get_current_user))
except Exception as _tm_exc:
    logger.exception(f"Failed to mount team_members router: {_tm_exc}")

app.include_router(api_router)
app.include_router(auth_router)

# Phase-21 — Support Tickets router (Support Center backend).
# Mounted right after the core api_router so the /api/support and
# /api/admin/support paths come up alongside the rest of the API.
try:
    from routers.support import (
        support_router as _support_router,
        admin_support_router as _admin_support_router,
        init as _init_support_router,
    )
    _init_support_router()
    app.include_router(_support_router)
    app.include_router(_admin_support_router)
except Exception as _sup_exc:
    logger.exception(f"Failed to mount support router: {_sup_exc}")

# Phase-21 — Video Tutorials router.
try:
    from routers.video_tutorials import (
        tutorials_router       as _vt_router,
        admin_tutorials_router as _admin_vt_router,
        init as _init_vt_router,
    )
    _init_vt_router()
    app.include_router(_vt_router)
    app.include_router(_admin_vt_router)
except Exception as _vt_exc:
    logger.exception(f"Failed to mount video tutorials router: {_vt_exc}")

# Phase-WA1 (2026-05-29) — OTP-based authentication router.
# Provider-independent: knows only services.otp_service (generation /
# validation) + services.otp_whatsapp (delivery dispatcher). Swapping
# FlowConnect for WATI / Interakt / Meta-Cloud requires zero changes
# here — only an env-var flip (WHATSAPP_OTP_PROVIDER=…).
try:
    from routers.otp_auth import (
        otp_auth_router as _otp_auth_router,
        init as _init_otp_auth_router,
    )
    _init_otp_auth_router()
    app.include_router(_otp_auth_router)
except Exception as _otp_exc:
    logger.exception(f"Failed to mount OTP auth router: {_otp_exc}")

# Phase-1 modular refactor — extracted routers. Each router is wired
# in *after* server.py has finished defining its helpers + db, so the
# late-binding `init()` calls inside them can `from server import …`
# without circular-import errors.
try:
    from routers.admin import admin_router as _admin_router, init as _init_admin_router
    _init_admin_router()
    app.include_router(_admin_router)
except Exception as _adm_exc:
    logger.exception(f"Failed to mount admin router: {_adm_exc}")

# Phase-27 — FAQ public + admin CRUD with visibility toggle.
# Mounts the public /api/faq endpoint and the admin /api/admin/faq
# CRUD set. Seeds the 25 default Q&As on first boot (idempotent — a
# re-deploy won't overwrite admin edits).
try:
    from routers.faq import (
        faq_router as _faq_router,
        admin_faq_router as _admin_faq_router,
        init as _init_faq_router,
        seed_default_faqs as _seed_default_faqs,
    )
    _init_faq_router()
    app.include_router(_faq_router)
    app.include_router(_admin_faq_router)
    @app.on_event("startup")
    async def _seed_faq_on_startup():  # noqa: D401
        try:
            await _seed_default_faqs(db)
        except Exception as _seed_exc:
            logger.warning(f"FAQ seed skipped: {_seed_exc}")
except Exception as _faq_exc:
    logger.exception(f"Failed to mount FAQ router: {_faq_exc}")

# Phase-28 — Dynamic WhatsApp Provider config: lets a Super Admin
# control every outbound WhatsApp message (auth OTPs + per-stage
# customer notifications) from a single UI without touching env files.
# Mounts /api/admin/whatsapp-provider/* and seeds the 8 default
# event-trigger docs on first boot. Idempotent.
try:
    from routers.whatsapp_provider import (
        whatsapp_provider_router as _wpp_router,
        init as _init_wpp_router,
        seed_default_events as _seed_wpp_events,
    )
    _init_wpp_router()
    app.include_router(_wpp_router)

    @app.on_event("startup")
    async def _seed_wpp_on_startup():  # noqa: D401
        try:
            await _seed_wpp_events(db)
        except Exception as _seed_exc:
            logger.warning(f"WhatsApp-provider seed skipped: {_seed_exc}")
except Exception as _wpp_exc:
    logger.exception(f"Failed to mount WhatsApp provider router: {_wpp_exc}")

# Phase-24 modular: field-config (per-module field enable/required) admin API.
try:
    from routers.field_configs import (
        field_configs_router as _field_configs_router,
        me_field_configs_router as _me_field_configs_router,
        init as _init_field_configs_router,
    )
    _init_field_configs_router()
    app.include_router(_field_configs_router)
    app.include_router(_me_field_configs_router)
except Exception as _fc_exc:
    logging.getLogger(__name__).exception(f"Failed to mount field_configs router: {_fc_exc}")

# Phase-5f modular: Google Sheets domain (6 endpoints) extracted out of server.py.
try:
    from routers.sheets import (
        sheets_router as _sheets_router,
        init as _init_sheets_router,
    )
    _init_sheets_router()
    app.include_router(_sheets_router)
except Exception as _sh_exc:
    logging.getLogger(__name__).exception(f"Failed to mount sheets router: {_sh_exc}")

# Phase-5g modular: Admin/Rules domain (18 endpoints) re-bound onto a
# dedicated router. Handler bodies stay in server.py (rebinding pattern)
# so this is a pure interface re-org with zero business-logic change.
try:
    from routers.admin_rules import (
        admin_rules_router as _admin_rules_router,
        init as _init_admin_rules_router,
    )
    _init_admin_rules_router()
    app.include_router(_admin_rules_router)
except Exception as _ar_exc:
    logging.getLogger(__name__).exception(f"Failed to mount admin_rules router: {_ar_exc}")

# Phase-5h modular: settings + /me/usage (3 endpoints) re-bound onto a
# dedicated router. Same rebinding pattern — bodies stay in server.py.
try:
    from routers.settings_me import (
        settings_me_router as _settings_me_router,
        init as _init_settings_me_router,
    )
    _init_settings_me_router()
    app.include_router(_settings_me_router)
except Exception as _sm_exc:
    logging.getLogger(__name__).exception(f"Failed to mount settings_me router: {_sm_exc}")

# Phase-5i modular: tiny utility router that owns the last two
# orphan routes left in server.py — `GET /api/` health-check and
# `POST /api/demo/clear` per-user demo wipe. Self-contained (does
# NOT rebind from server.py) so the dead function bodies can finally
# be deleted from server.py.
try:
    from routers.utility import (
        utility_router as _utility_router,
        init as _init_utility_router,
    )
    _init_utility_router()
    app.include_router(_utility_router)
except Exception as _ut_exc:
    logging.getLogger(__name__).exception(f"Failed to mount utility router: {_ut_exc}")

# Phase-3 modular: couriers + variants + categories.
try:
    from routers.couriers import (
        couriers_router as _couriers_router,
        init as _init_couriers_router,
    )
    _init_couriers_router()
    app.include_router(_couriers_router)
except Exception as _cou_exc:
    logger.exception(f"Failed to mount couriers router: {_cou_exc}")

# Phase-3 modular: per-user custom fields + contact-save settings +
# admin custom-field caps.
try:
    from routers.custom_fields import (
        custom_fields_router as _custom_fields_router,
        init as _init_custom_fields_router,
    )
    _init_custom_fields_router()
    app.include_router(_custom_fields_router)
except Exception as _cf_exc:
    logger.exception(f"Failed to mount custom_fields router: {_cf_exc}")

# Phase-3 modular: feature-flag read-only endpoints.
try:
    from routers.feature_flags import (
        feature_flags_router as _feature_flags_router,
        init as _init_feature_flags_router,
    )
    _init_feature_flags_router()
    app.include_router(_feature_flags_router)
except Exception as _ff_exc:
    logger.exception(f"Failed to mount feature_flags router: {_ff_exc}")

# Phase-4a modular: wallet + razorpay top-up endpoints.
try:
    from routers.wallet import (
        wallet_router as _wallet_router,
        init as _init_wallet_router,
    )
    _init_wallet_router()
    app.include_router(_wallet_router)
except Exception as _w_exc:
    import logging as _lg
    _lg.getLogger("server.bootstrap").exception(
        f"Failed to mount wallet router: {_w_exc}",
    )

# Phase-4a-extra modular: plan catalogue + coupon system.
try:
    from routers.plans_coupons import (
        plans_coupons_router as _plans_coupons_router,
        init as _init_plans_coupons_router,
    )
    _init_plans_coupons_router()
    app.include_router(_plans_coupons_router)
except Exception as _pc_exc:
    import logging as _lg
    _lg.getLogger("server.bootstrap").exception(
        f"Failed to mount plans_coupons router: {_pc_exc}",
    )

# Phase-4b modular: smart-paste preview + master-id-counter + sheets-sync.
try:
    from routers.smart_paste import (
        smart_paste_router as _smart_paste_router,
        init as _init_smart_paste_router,
    )
    _init_smart_paste_router()
    app.include_router(_smart_paste_router)
except Exception as _sp_exc:
    import logging as _lg
    _lg.getLogger("server.bootstrap").exception(
        f"Failed to mount smart_paste router: {_sp_exc}",
    )

# Phase-5a modular: scan flow + bulk-mark + delivery-confirmation.
# IMPORTANT: This router MUST be registered BEFORE shipments_read.py
# below — its routes use literal paths like "/shipments/scan-dispatch"
# and "/shipments/delivery-confirmation" which would otherwise be
# swallowed by the catch-all "/shipments/{shipment_id}" GET in
# shipments_read.py. FastAPI matches routes in registration order.
try:
    from routers.shipment_ops import (
        shipment_ops_router as _shipment_ops_router,
        init as _init_shipment_ops_router,
    )
    _init_shipment_ops_router()
    app.include_router(_shipment_ops_router)
except Exception as _so_exc:
    import logging as _lg
    _lg.getLogger("server.bootstrap").exception(
        f"Failed to mount shipment_ops router: {_so_exc}",
    )

# Phase-5b modular: pending-orders CRUD (Smart Paste queue management).
try:
    from routers.pending_orders import (
        pending_orders_router as _pending_orders_router,
        init as _init_pending_orders_router,
    )
    _init_pending_orders_router()
    app.include_router(_pending_orders_router)
except Exception as _po_exc:
    import logging as _lg
    _lg.getLogger("server.bootstrap").exception(
        f"Failed to mount pending_orders router: {_po_exc}",
    )

# Phase-5c-1 modular: plan upgrade + Razorpay subscription endpoints.
try:
    from routers.plans_billing import (
        plans_billing_router as _plans_billing_router,
        init as _init_plans_billing_router,
    )
    _init_plans_billing_router()
    app.include_router(_plans_billing_router)
except Exception as _pb_exc:
    import logging as _lg
    _lg.getLogger("server.bootstrap").exception(
        f"Failed to mount plans_billing router: {_pb_exc}",
    )

# Phase-4c modular: read-only shipments + lookup endpoints.
try:
    from routers.shipments_read import (
        shipments_read_router as _shipments_read_router,
        init as _init_shipments_read_router,
    )
    _init_shipments_read_router()
    app.include_router(_shipments_read_router)
except Exception as _sr_exc:
    import logging as _lg
    _lg.getLogger("server.bootstrap").exception(
        f"Failed to mount shipments_read router: {_sr_exc}",
    )

# Phase-5c-2 modular: heavy shipment mutation endpoints (create/update/
# delete + pending→ship). These all share the same plan-room → wallet →
# Master-Sheet → Mongo → user-sheet auto-sync pipeline.
try:
    from routers.shipments_write import (
        shipments_write_router as _shipments_write_router,
        init as _init_shipments_write_router,
    )
    _init_shipments_write_router()
    app.include_router(_shipments_write_router)
except Exception as _sw_exc:
    import logging as _lg
    _lg.getLogger("server.bootstrap").exception(
        f"Failed to mount shipments_write router: {_sw_exc}",
    )

# Phase-5d modular: notification preferences + push-token registry.
# Pure user-scoped CRUD with no cross-cutting business logic; the only
# helpers it needs from server.py are db, get_current_user, and
# _coerce_notif_prefs (which stays here because the SLA cron worker
# also calls it via _push_event).
try:
    from routers.notifications import (
        notifications_router as _notifications_router,
        init as _init_notifications_router,
    )
    _init_notifications_router()
    app.include_router(_notifications_router)
except Exception as _ntf_exc:
    import logging as _lg
    _lg.getLogger("server.bootstrap").exception(
        f"Failed to mount notifications router: {_ntf_exc}",
    )

# Phase-F1 modular: CSV / XLSX bulk-import → pending_orders.
# Self-contained domain (no shared mutable state with the rest of
# server.py) — only needs db, get_current_user, and
# generate_master_order_id.
try:
    from routers.file_import import (
        file_import_router as _file_import_router,
        init as _init_file_import_router,
    )
    _init_file_import_router()
    app.include_router(_file_import_router)
except Exception as _fi_exc:
    import logging as _lg
    _lg.getLogger("server.bootstrap").exception(
        f"Failed to mount file_import router: {_fi_exc}",
    )


# Phase-F2 modular: Webhook ingest (real-time JSON-payload imports) +
# per-user webhook config endpoints. Same late-binding pattern.
# Note: the public POST /api/webhook/orders/{secret} endpoint is added
# to _AUTH_EXEMPT_PREFIXES below so external systems can call it
# without an auth header (the URL secret is the auth).
try:
    from routers.webhook import (
        webhook_router as _webhook_router,
        init as _init_webhook_router,
    )
    _init_webhook_router()
    app.include_router(_webhook_router)
except Exception as _wh_exc:
    import logging as _lg
    _lg.getLogger("server.bootstrap").exception(
        f"Failed to mount webhook router: {_wh_exc}",
    )

# Phase F3 — multi-webhooks (named, event-typed, unlimited per user).
# Mounts on top of the v1 routes; the public ingest endpoint lives in
# routers/webhook.py and consults BOTH stores so old + new URLs work.
try:
    from routers.webhooks_multi import (
        webhooks_multi_router as _webhooks_multi_router,
        init as _init_webhooks_multi_router,
    )
    _init_webhooks_multi_router()
    app.include_router(_webhooks_multi_router)
except Exception as _whm_exc:
    import logging as _lg
    _lg.getLogger("server.bootstrap").exception(
        f"Failed to mount webhooks_multi router: {_whm_exc}",
    )

# Phase F3.3 — Abandoned Carts (populated by abandoned_order webhooks).
try:
    from routers.abandoned_carts import (
        abandoned_carts_router as _abandoned_carts_router,
        init as _init_abandoned_carts_router,
    )
    _init_abandoned_carts_router()
    app.include_router(_abandoned_carts_router)
except Exception as _ac_exc:
    import logging as _lg
    _lg.getLogger("server.bootstrap").exception(
        f"Failed to mount abandoned_carts router: {_ac_exc}",
    )

# Phase F3.9.8 — Short-link service for WhatsApp recovery messages.
# Compacts the long platform recovery_url into a tidy /api/s/<code>
# alias and surfaces click-tracking via db.short_links + a
# `link_clicked_at` stamp on the linked abandoned_cart.
try:
    from routers.short_links import (
        short_links_router as _short_links_router,
        init as _init_short_links_router,
    )
    _init_short_links_router()
    app.include_router(_short_links_router)
except Exception as _sl_exc:
    import logging as _lg
    _lg.getLogger("server.bootstrap").exception(
        f"Failed to mount short_links router: {_sl_exc}",
    )

# Phase F3.3 — Customers (populated by customer_created/updated webhooks).
try:
    from routers.customers import (
        customers_router as _customers_router,
        init as _init_customers_router,
    )
    _init_customers_router()
    app.include_router(_customers_router)
except Exception as _cu_exc:
    import logging as _lg
    _lg.getLogger("server.bootstrap").exception(
        f"Failed to mount customers router: {_cu_exc}",
    )


# --------------------------------------------------------------------
# Auth middleware — requires a valid bearer token on every /api/*
# route except /api/auth/* (signup/login/me/logout are public/self-auth).
# This is the Phase-1a lock that prevents unauthenticated API access.
# Per-route user_id filtering (data isolation) comes in Phase-1b.
# --------------------------------------------------------------------
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from auth import decode_token as _decode_token

# Endpoints that are intentionally reachable without a token.
# Phase F2: /api/webhook/orders/<secret> is public — the URL secret IS
# the auth (secret-in-URL pattern). External systems (Shopify, Zapier,
# custom webhooks) cannot attach an Authorization header.
_AUTH_EXEMPT_PREFIXES = (
    "/api/auth/",
    "/api/legal/",
    "/api/team/login",
    "/api/webhook/orders/",
    # Phase F3.9.8 — Short-link follow URLs are customer-facing. The
    # short code itself IS the auth — anyone with the link can hit
    # the redirect, by design. POST /api/short-links (creation) is
    # NOT exempt because it stays under the bearer token requirement.
    "/api/s/",
    # Phase-27 — FAQ public-read endpoint. The list is a marketing /
    # education touchpoint; admin CRUD endpoints live under
    # /api/admin/faq which remains bearer-gated.
    "/api/faq",
)
# Phase 2.5 — Excel report endpoints accept ?token= query param instead
# of header (browser cannot attach Authorization to <a href> downloads).
_EXCEL_DOWNLOAD_PATHS = {
    "/api/me/reports/courier-billing/excel",
    "/api/me/reports/return-analysis/excel",
    "/api/me/reports/weight-wise/excel",
    "/api/me/reports/partner-comparison/excel",
    "/api/me/reports/reconciliation/excel",
}
# Admin-only endpoints. For Phase-1a we keep this small; Phase-1b will
# expand as we harden multi-tenancy.
_ADMIN_ONLY_PATHS: set = set()


@app.middleware("http")
async def auth_gate(request, call_next):
    path = request.url.path or ""
    if not path.startswith("/api/"):
        return await call_next(request)
    if any(path.startswith(p) for p in _AUTH_EXEMPT_PREFIXES):
        return await call_next(request)
    # Phase 2.5 — Excel report downloads accept token via ?token= so
    # they can be opened with `Linking.openURL(...)` which cannot
    # attach custom headers. Each endpoint enforces its own auth.
    if path in _EXCEL_DOWNLOAD_PATHS:
        return await call_next(request)
    auth_hdr = request.headers.get("authorization") or request.headers.get("Authorization") or ""
    if not auth_hdr.lower().startswith("bearer "):
        return JSONResponse(
            status_code=401,
            content={"detail": "Authentication required"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = auth_hdr.split(" ", 1)[1].strip()
    try:
        payload = _decode_token(token)
    except HTTPException as e:
        return JSONResponse(status_code=e.status_code, content={"detail": e.detail})
    # Stash for any handler that wants it (Phase-1b will filter queries here).
    request.state.user_id = payload.get("sub")
    request.state.user_email = payload.get("email")
    return await call_next(request)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# Phase-21 — Webhook-replay deduplication.
async def _ensure_pending_orders_dedup_index() -> None:
    """One-time cleanup + unique compound index for webhook replays.

    The index enforces uniqueness on
        (user_id, external_order_id, source_meta.source_app)
    at the database level so even a race between two concurrent webhook
    workers can NEVER produce duplicate pending-order rows. We use a
    PARTIAL filter so rows without an external_order_id (paste, file
    import, smart-paste rows that never carried one) are exempt.

    Steps:
      1. Aggregate pending_orders grouped by the dedup key. For any
         group with count > 1, keep the OLDEST doc (smallest created_at)
         and delete the rest. This is a one-shot heal: if the
         application has been live with the bug, there can be 2-3
         duplicates per replayed order.
      2. Create the partial-unique compound index. Idempotent — if the
         index already exists with the same spec, MongoDB no-ops.
    Both steps are wrapped in their own try/except so an aggregation
    failure on a giant collection never blocks server startup.
    """
    coll = db.pending_orders
    # ---- Step 1: cleanup pre-existing duplicates --------------------
    try:
        pipeline = [
            {"$match": {
                "external_order_id": {"$nin": [None, ""]},
            }},
            {"$group": {
                "_id": {
                    "user_id":           "$user_id",
                    "external_order_id": "$external_order_id",
                    "source_app":        "$source_meta.source_app",
                },
                "ids":      {"$push": {"_id": "$_id", "created_at": "$created_at"}},
                "count":    {"$sum": 1},
            }},
            {"$match": {"count": {"$gt": 1}}},
        ]
        removed = 0
        async for grp in coll.aggregate(pipeline, allowDiskUse=True):
            entries = grp.get("ids") or []
            # Keep the oldest (smallest created_at) — drop the rest.
            entries.sort(key=lambda e: e.get("created_at") or "")
            to_drop = [e["_id"] for e in entries[1:]]
            if to_drop:
                res = await coll.delete_many({"_id": {"$in": to_drop}})
                removed += int(res.deleted_count or 0)
        if removed:
            logger.info(
                "pending_orders dedup cleanup: removed %d duplicate row(s)",
                removed,
            )
    except Exception:
        logger.exception("pending_orders duplicate cleanup failed (non-fatal)")

    # ---- Step 2: create the unique compound index -------------------
    try:
        await coll.create_index(
            [
                ("user_id", 1),
                ("external_order_id", 1),
                ("source_meta.source_app", 1),
            ],
            name="uniq_user_externalOrder_sourceApp",
            unique=True,
            partialFilterExpression={
                # Only enforce uniqueness for rows that actually carry
                # an external_order_id. Paste/file/smart-paste rows
                # often have empty external_order_id and would
                # otherwise collide with each other.
                "external_order_id": {"$exists": True, "$gt": ""},
            },
        )
    except Exception:
        # Possible reasons:
        #   • Index already exists with a different spec (collation,
        #     name) — re-create after dropping is risky in prod, so
        #     we log and continue. The application-level pre-check
        #     still guards against replays.
        logger.exception(
            "pending_orders unique index creation skipped (non-fatal)",
        )



@app.on_event("startup")
async def on_startup():
    await seed_defaults()
    # Backfill display_id on any pre-Phase-4d users so admin/users
    # shows USR-XXXXX for everyone. Idempotent.
    try:
        filled = await _backfill_display_ids()
        if filled:
            logger.info(f"Backfilled display_id for {filled} legacy user(s).")
    except Exception:
        logger.exception("display_id backfill failed (non-fatal)")
    # Phase-21 — Webhook deduplication. Ensure the unique compound index
    # exists on pending_orders so concurrent replays of the same
    # storefront order can NEVER produce duplicate rows, even if the
    # application-level pre-check loses a race. Includes a one-time
    # sweep to remove any legacy duplicates that snuck in before the
    # index existed (otherwise the index creation itself would fail).
    try:
        await _ensure_pending_orders_dedup_index()
    except Exception:
        logger.exception("pending_orders dedup index setup failed (non-fatal)")
    # Kick off the deferred Master Sheet backup retry worker. It loops
    # every 60s, draining up to 5 shipments per cycle whose
    # `master_backup_status == "pending"`. Quota errors keep them
    # pending; success flips them to "ok".
    try:
        import asyncio as _asyncio
        _asyncio.create_task(_master_backup_retry_worker())
    except Exception:
        logger.exception("Failed to start master backup retry worker (non-fatal)")
    # SLA Engine — Phase G3. Runs every `scan_interval_minutes`
    # configured in admin/stage-rules. Defaults to 60min.
    try:
        import asyncio as _asyncio
        _asyncio.create_task(_sla_scan_worker())
    except Exception:
        logger.exception("Failed to start SLA scan worker (non-fatal)")
    # Phase G6 — Morning reminder push (8am IST daily).
    try:
        import asyncio as _asyncio
        _asyncio.create_task(_morning_reminder_worker())
    except Exception:
        logger.exception("Failed to start morning reminder worker (non-fatal)")
    # Phase H — User personal-sheet sync retry worker. Drains the
    # `user_sheet_sync_pending` collection every 90s.
    try:
        import asyncio as _asyncio
        _asyncio.create_task(_user_sheet_drain_worker())
    except Exception:
        logger.exception("Failed to start user-sheet drain worker (non-fatal)")
    logger.info("Courier Label Manager API started; defaults seeded.")


async def _master_backup_retry_worker() -> None:
    """Background loop: periodically retry shipments whose Master Sheet
    backup was deferred due to transient quota / 5xx errors.

    Runs every 60s, processes up to 5 shipments per cycle (one every
    ~2s to stay well under Google's 60-reads/min quota). On success
    flips `master_backup_status` from "pending" to "ok" and stamps
    `sheet_row_num`. On still-transient errors leaves the doc in
    pending state for the next cycle.
    """
    import asyncio as _asyncio
    INTERVAL = 60.0
    BATCH = 5
    PER_ROW_DELAY = 2.0
    while True:
        try:
            await _asyncio.sleep(INTERVAL)
            if sheet_append_order_row is None:
                continue
            cursor = db.shipments.find(
                {"master_backup_status": "pending"},
                {"_id": 0},
            ).limit(BATCH)
            pending = await cursor.to_list(length=BATCH)
            if not pending:
                continue
            for ship in pending:
                payload = ship.get("master_backup_payload") or {}
                if not payload:
                    # No payload to replay — mark as failed so we don't
                    # spin forever on a malformed doc.
                    await db.shipments.update_one(
                        {"id": ship["id"]},
                        {"$set": {"master_backup_status": "failed_no_payload"}},
                    )
                    continue
                try:
                    sheet_meta = sheet_append_order_row(**payload)
                    update_fields: Dict[str, Any] = {
                        "master_backup_status": "ok",
                        "master_backup_completed_at": utcnow_iso(),
                    }
                    if sheet_parse_row_from_updated_range and sheet_meta:
                        try:
                            row_n = sheet_parse_row_from_updated_range(
                                sheet_meta.get("updated_range")
                            )
                            if row_n:
                                update_fields["sheet_row_num"] = int(row_n)
                        except Exception:
                            pass
                    await db.shipments.update_one(
                        {"id": ship["id"]},
                        {"$set": update_fields,
                         "$unset": {"master_backup_payload": ""}},
                    )
                    logger.info(
                        "Master backup retry OK for shipment %s", ship.get("id"),
                    )
                except Exception as e:
                    if _is_transient_sheet_error(e):
                        # Still over quota — leave for next cycle.
                        await db.shipments.update_one(
                            {"id": ship["id"]},
                            {"$set": {
                                "master_backup_last_error_at": utcnow_iso(),
                                "master_backup_last_error": str(e)[:300],
                            }},
                        )
                    else:
                        # Permanent — flag and stop retrying.
                        logger.exception(
                            "Master backup retry permanent failure for %s",
                            ship.get("id"),
                        )
                        await db.shipments.update_one(
                            {"id": ship["id"]},
                            {"$set": {
                                "master_backup_status": "failed",
                                "master_backup_last_error": str(e)[:300],
                            }},
                        )
                # Pace the retries to avoid re-tripping the quota.
                await _asyncio.sleep(PER_ROW_DELAY)
        except _asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Master backup retry worker iteration failed")


async def _sla_scan_worker() -> None:
    """Background loop that runs the SLA breach scanner every
    `scan_interval_minutes` (admin config). Sleeps the configured
    interval, then calls `sla_engine.scan_all_users(db)` and updates
    the in-memory `_SLA_LAST_RUN` cache. Always defers the very first
    scan by 60s after server boot to let DB warm up.
    """
    import asyncio as _asyncio
    await _asyncio.sleep(60.0)   # boot grace period
    while True:
        try:
            rules = await _load_stage_rules()
            interval_min = max(15, min(240, int(
                rules.get("scan_interval_minutes") or 60
            )))
            if not rules.get("global_enabled", True):
                # Admin globally disabled SLAs — sleep & re-check.
                _SLA_LAST_RUN["next_run_at"] = (
                    datetime.now(timezone.utc)
                    + timedelta(minutes=interval_min)
                ).isoformat()
                await _asyncio.sleep(interval_min * 60.0)
                continue
            _SLA_LAST_RUN["running"] = True
            try:
                stats = await _sla_engine.scan_all_users(db)
                _SLA_LAST_RUN.update({
                    "ran_at":        stats.get("ran_at"),
                    "alerts_raised": int(stats.get("alerts_raised") or 0),
                    "users_scanned": int(stats.get("users_scanned") or 0),
                })
                logger.info(
                    "SLA worker: scanned=%s raised=%s",
                    stats.get("users_scanned"), stats.get("alerts_raised"),
                )
            finally:
                _SLA_LAST_RUN["running"] = False
            _SLA_LAST_RUN["next_run_at"] = (
                datetime.now(timezone.utc)
                + timedelta(minutes=interval_min)
            ).isoformat()
            await _asyncio.sleep(interval_min * 60.0)
        except _asyncio.CancelledError:
            return
        except Exception:
            logger.exception("SLA scan worker iteration failed")
            # Back off 5 min on errors instead of busy-looping.
            await _asyncio.sleep(300.0)


async def _user_sheet_drain_worker() -> None:
    """Phase H — periodic retry of failed user-sheet sync ops.
    Drains up to 5 docs every 90s. Stops examining a doc after 10
    failed attempts (queue self-cleans)."""
    import asyncio as _asyncio
    import user_sheet_sync as _uss
    await _asyncio.sleep(90.0)   # boot grace
    while True:
        try:
            res = await _uss.drain_pending_queue(db, batch=5)
            if res.get("examined"):
                logger.info(
                    "user-sheet drain: examined=%s drained=%s failed=%s",
                    res["examined"], res.get("drained"), res.get("failed"),
                )
        except _asyncio.CancelledError:
            return
        except Exception:
            logger.exception("user-sheet drain iteration failed")
        await _asyncio.sleep(90.0)




@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()

async def _morning_reminder_worker() -> None:
    """Phase G6 — Morning reminder push (8am IST daily).
    Walks every active user, computes their pending bulk-message
    counts via /me/bulk-message/dashboard-counts logic, and fires a
    summary push if anything is pending. Idempotent per day via
    `morning_reminder_pushed_day` flag on each user.
    """
    import asyncio as _asyncio
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    IST = _tz(_td(hours=5, minutes=30))
    await _asyncio.sleep(120.0)   # boot grace period
    while True:
        try:
            now_ist = _dt.now(IST)
            # Target = today 08:00 IST. If we're past it, schedule for
            # tomorrow.
            target = now_ist.replace(hour=8, minute=0, second=0, microsecond=0)
            if now_ist >= target:
                target = target + _td(days=1)
            sleep_s = max(60.0, (target - now_ist).total_seconds())
            await _asyncio.sleep(sleep_s)

            today_str = _dt.now(IST).strftime("%Y-%m-%d")
            cursor = db.users.find(
                {"deleted_at": {"$exists": False}},
                {"_id": 0, "id": 1, "email": 1, "morning_reminder_pushed_day": 1},
            )
            async for u in cursor:
                uid = u["id"]
                if u.get("morning_reminder_pushed_day") == today_str:
                    continue
                # Count pending shipments per ttype using existing
                # backend logic. We'll just use total open Pending /
                # Shipped to keep this lean.
                pending = await db.shipments.count_documents({
                    "user_id": uid,
                    "status":   {"$in": ["Pending", "Processing"]},
                    "deleted_at": {"$exists": False},
                })
                shipped = await db.shipments.count_documents({
                    "user_id": uid,
                    "status":   "Shipped",
                    "deleted_at": {"$exists": False},
                })
                if pending == 0 and shipped == 0:
                    continue
                title = "🌅 Good morning — daily ops digest"
                body_parts = []
                if pending: body_parts.append(f"{pending} new orders to process")
                if shipped: body_parts.append(f"{shipped} parcels in transit")
                body = "  ·  ".join(body_parts) + "  — tap to review."
                try:
                    await _push_event(
                        [uid],
                        event_key="morning_reminder",
                        title=title, body=body,
                        data={"type": "morning_reminder",
                              "pending": pending, "shipped": shipped},
                    )
                    await db.users.update_one(
                        {"id": uid},
                        {"$set": {"morning_reminder_pushed_day": today_str}},
                    )
                except Exception:
                    logger.exception("morning push failed for %s", uid)
        except _asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Morning reminder iteration failed")
            await _asyncio.sleep(900.0)


# ──────────────────────────────────────────────────────────────────
# Phase H — User personal-sheet sync admin/utility endpoints
# ──────────────────────────────────────────────────────────────────

# These endpoints are added AFTER the original `app.include_router(api_router)`
# call earlier in the file, so we attach them to a fresh sub-router and
# include it directly on the app (single explicit include below).
sheet_sync_router = APIRouter(prefix="/api", tags=["sheet-sync"])
analytics_router  = APIRouter(prefix="/api", tags=["admin-analytics"])


@sheet_sync_router.get("/me/sheet-sync/status")
async def me_sheet_sync_status(
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Aggregate counters showing the health of the user's personal
    sheet sync. Used by Settings and the Sheet Sync banner."""
    settings_doc = await db.settings.find_one(
        {"user_id": current_user["id"]}, {"_id": 0, "sheet": 1},
    ) or {}
    cfg = (settings_doc.get("sheet") or {})
    sheet_id = (cfg.get("sheet_id") or "").strip()

    pipeline = [
        {"$match": {"user_id": current_user["id"]}},
        {"$group": {
            "_id": "$user_sheet_sync_status",
            "count": {"$sum": 1},
        }},
    ]
    counts = {"ok": 0, "pending": 0, "skipped": 0, "error": 0, "never": 0}
    total = 0
    async for row in db.shipments.aggregate(pipeline):
        key = row["_id"] if row["_id"] in counts else "never"
        counts[key] = counts.get(key, 0) + int(row["count"])
        total += int(row["count"])
    # Anything without the field at all → "never".
    explicit_total = sum(counts[k] for k in ("ok", "pending", "skipped", "error"))
    counts["never"] = max(0, total - explicit_total)

    queue_pending = await db.user_sheet_sync_pending.count_documents(
        {"user_id": current_user["id"]},
    )

    return {
        "connected":          bool(sheet_id),
        "sheet_id":           sheet_id,
        "sheet_url":          cfg.get("url") or "",
        "auto_sync_create":   bool(cfg.get("auto_sync_create", True)),
        "auto_sync_status":   bool(cfg.get("auto_sync_status", True)),
        "auto_sync_delete":   bool(cfg.get("auto_sync_delete", True)),
        "shipment_counts":    counts,
        "queue_pending":      int(queue_pending),
        "total_shipments":    total,
    }


@sheet_sync_router.put("/me/sheet-sync/toggles")
async def me_sheet_sync_toggles(
    payload: Dict[str, Any] = Body(...),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Update auto_sync_{create,status,delete} flags."""
    update: Dict[str, Any] = {}
    for k in ("auto_sync_create", "auto_sync_status", "auto_sync_delete"):
        if k in payload:
            update[f"sheet.{k}"] = bool(payload[k])
    if not update:
        raise HTTPException(status_code=400, detail="No supported toggles in body")
    await db.settings.update_one(
        {"user_id": current_user["id"]},
        {"$set": update},
        upsert=True,
    )
    return await me_sheet_sync_status(current_user)  # type: ignore[arg-type]


@sheet_sync_router.post("/me/sheet-sync/run-now")
async def me_sheet_sync_run_now(
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Drain the user's pending sync queue immediately + retry every
    shipment whose sync_status is 'error'. Returns a small summary.
    Capped at 20 ops to stay under Google Sheets per-minute quota."""
    import asyncio as _asyncio
    import user_sheet_sync as _uss
    drained = await _uss.drain_pending_queue(db, batch=10)

    # Also kick a backfill for shipments that have never been synced.
    cursor = db.shipments.find(
        {
            "user_id": current_user["id"],
            "$or": [
                {"user_sheet_sync_status": {"$exists": False}},
                {"user_sheet_sync_status": "error"},
            ],
        },
        {"_id": 0},
    ).limit(20)
    backfilled, errored = 0, 0
    async for ship in cursor:
        try:
            res = await _uss.sync_create(db, current_user, ship)
            if res.get("ok"):
                backfilled += 1
            else:
                errored += 1
            # Stay under the 60 reads/min Google Sheets quota.
            await _asyncio.sleep(1.2)
        except Exception:
            errored += 1
    return {
        "drained":    drained,
        "backfilled": backfilled,
        "errored":    errored,
        "note":       "Capped at 20 ops/call to respect Google Sheets quota — re-run if more pending.",
    }


@sheet_sync_router.post("/me/sheet-sync/shipment/{shipment_id}")
async def me_sheet_sync_one(
    shipment_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Manually re-sync a single shipment (useful from the shipment
    detail screen when an admin sees a stale row)."""
    import user_sheet_sync as _uss
    ship = await db.shipments.find_one(
        {"id": shipment_id, "user_id": current_user["id"]}, {"_id": 0},
    )
    if not ship:
        raise HTTPException(status_code=404, detail="Shipment not found")
    if ship.get("user_sheet_row_num"):
        return await _uss.sync_status_change(
            db, current_user, ship, ship.get("status") or "Pending",
        )
    return await _uss.sync_create(db, current_user, ship)




# ──────────────────────────────────────────────────────────────────
# Phase I — Admin Analytics Dashboard
# ──────────────────────────────────────────────────────────────────


def _range_to_since(range_key: str) -> Optional[datetime]:
    """Convert a UI range key (today / 7d / 30d / 90d / all) to a UTC
    datetime cutoff. Returns None for `all` (no filter)."""
    now = datetime.now(timezone.utc)
    if range_key == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if range_key == "7d":
        return now - timedelta(days=7)
    if range_key == "30d":
        return now - timedelta(days=30)
    if range_key == "90d":
        return now - timedelta(days=90)
    return None


# ──────────────────────────────────────────────────────────────────
# Phase I.2 — Unified Analytics endpoint (per-user + admin platform)
# ──────────────────────────────────────────────────────────────────
# `scope=mine` (default): scoped to current_user's shipments.
# `scope=platform`: admin-only — across all users.
# Filter params: courier, status, payment_mode, state. All optional.
# Returns a payload that mirrors the legacy admin overview when
# scope=platform, but adds breakdowns useful for individual users
# (by_payment_mode, by_state, top_cities, revenue from shipments).
# ──────────────────────────────────────────────────────────────────
@analytics_router.get("/analytics/overview")
async def analytics_overview(
    range_key: str = Query("30d", alias="range"),
    scope: str = Query("mine"),
    courier: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    payment_mode: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Unified analytics — user's own data by default, admin can flip
    `scope=platform` to see platform-wide aggregates. Filters narrow
    every aggregation: courier, status, payment_mode, state."""
    is_admin = bool(current_user.get("is_admin"))
    if scope == "platform" and not is_admin:
        raise HTTPException(status_code=403, detail="Platform scope is admin-only.")

    since = _range_to_since(range_key)
    iso_since = since.isoformat() if since else None

    # Base shipment match (always excludes soft-deleted rows).
    ship_match: Dict[str, Any] = {"deleted_at": {"$exists": False}}
    if scope != "platform":
        ship_match["user_id"] = current_user["id"]
    if iso_since:
        ship_match["created_at"] = {"$gte": iso_since}
    if courier and courier != "all":
        ship_match["courier_name"] = courier
    if status and status != "all":
        ship_match["status"] = status
    if payment_mode and payment_mode != "all":
        # Accept "COD" / "Prepaid" / "PAID" — normalize.
        pm_norm = payment_mode.strip().upper()
        if pm_norm == "PAID":
            pm_norm = "PREPAID"
        ship_match["payment_mode"] = {"$in": [pm_norm, pm_norm.lower(), pm_norm.title()]}
    if state and state != "all":
        ship_match["state"] = {"$regex": f"^{state.strip()}$", "$options": "i"}

    total_shipments = await db.shipments.count_documents(ship_match)

    # By status -----------------------------------------------------
    by_status: Dict[str, int] = {}
    async for row in db.shipments.aggregate([
        {"$match": ship_match},
        {"$group": {"_id": "$status", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]):
        by_status[row["_id"] or "Unknown"] = int(row["count"])

    delivered_count = sum(int(v) for k, v in by_status.items() if (k or "").lower() == "delivered")
    pending_count = total_shipments - delivered_count

    # By courier ----------------------------------------------------
    by_courier: List[Dict[str, Any]] = []
    async for row in db.shipments.aggregate([
        {"$match": ship_match},
        {"$group": {"_id": "$courier_name", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 10},
    ]):
        by_courier.append({"name": row["_id"] or "Unknown", "count": int(row["count"])})

    # By payment mode -----------------------------------------------
    by_payment: Dict[str, int] = {"COD": 0, "PREPAID": 0, "Other": 0}
    async for row in db.shipments.aggregate([
        {"$match": ship_match},
        {"$group": {"_id": "$payment_mode", "count": {"$sum": 1}}},
    ]):
        raw = (row["_id"] or "").strip().upper()
        if raw == "COD":
            by_payment["COD"] += int(row["count"])
        elif raw in ("PREPAID", "PAID"):
            by_payment["PREPAID"] += int(row["count"])
        else:
            by_payment["Other"] += int(row["count"])

    # Revenue (sum of `amount` field on shipments) ------------------
    revenue_total = 0
    revenue_cod = 0
    revenue_prepaid = 0
    async for row in db.shipments.aggregate([
        {"$match": ship_match},
        {"$group": {
            "_id": {"$toUpper": {"$ifNull": ["$payment_mode", ""]}},
            "sum": {"$sum": {"$convert": {"input": "$amount", "to": "double", "onError": 0, "onNull": 0}}},
        }},
    ]):
        sub = int(row.get("sum") or 0)
        revenue_total += sub
        pm = (row["_id"] or "").strip().upper()
        if pm == "COD":
            revenue_cod += sub
        elif pm in ("PREPAID", "PAID"):
            revenue_prepaid += sub

    # Top 8 states / cities -----------------------------------------
    by_state: List[Dict[str, Any]] = []
    async for row in db.shipments.aggregate([
        {"$match": ship_match},
        {"$group": {"_id": "$state", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 8},
    ]):
        by_state.append({"name": row["_id"] or "Unknown", "count": int(row["count"])})

    by_city: List[Dict[str, Any]] = []
    async for row in db.shipments.aggregate([
        {"$match": ship_match},
        {"$group": {"_id": "$city", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 8},
    ]):
        by_city.append({"name": row["_id"] or "Unknown", "count": int(row["count"])})

    # 30-day creation trend (uses same scope, ignores other filters
    # so the chart shape is stable). -------------------------------
    trend_since = (datetime.now(timezone.utc) - timedelta(days=29)).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    trend_match: Dict[str, Any] = {
        "deleted_at": {"$exists": False},
        "created_at": {"$gte": trend_since.isoformat()},
    }
    if scope != "platform":
        trend_match["user_id"] = current_user["id"]
    if courier and courier != "all":
        trend_match["courier_name"] = courier
    by_day_raw: Dict[str, int] = {}
    async for row in db.shipments.aggregate([
        {"$match": trend_match},
        {"$group": {
            "_id": {"$substr": ["$created_at", 0, 10]},
            "count": {"$sum": 1},
        }},
    ]):
        by_day_raw[row["_id"]] = int(row["count"])
    daily_trend = [
        {
            "date":  (trend_since + timedelta(days=i)).strftime("%Y-%m-%d"),
            "count": int(by_day_raw.get(
                (trend_since + timedelta(days=i)).strftime("%Y-%m-%d"), 0)),
        }
        for i in range(30)
    ]

    # Filter option lists (so the UI can populate dropdowns) -------
    courier_options: List[str] = []
    async for row in db.shipments.aggregate([
        {"$match": (
            {"deleted_at": {"$exists": False}, "user_id": current_user["id"]}
            if scope != "platform" else {"deleted_at": {"$exists": False}}
        )},
        {"$group": {"_id": "$courier_name"}},
        {"$sort": {"_id": 1}},
        {"$limit": 30},
    ]):
        nm = (row["_id"] or "").strip()
        if nm:
            courier_options.append(nm)

    state_options: List[str] = []
    async for row in db.shipments.aggregate([
        {"$match": (
            {"deleted_at": {"$exists": False}, "user_id": current_user["id"]}
            if scope != "platform" else {"deleted_at": {"$exists": False}}
        )},
        {"$group": {"_id": "$state"}},
        {"$sort": {"_id": 1}},
        {"$limit": 50},
    ]):
        nm = (row["_id"] or "").strip()
        if nm:
            state_options.append(nm)

    status_options: List[str] = []
    async for row in db.shipments.aggregate([
        {"$match": (
            {"deleted_at": {"$exists": False}, "user_id": current_user["id"]}
            if scope != "platform" else {"deleted_at": {"$exists": False}}
        )},
        {"$group": {"_id": "$status"}},
    ]):
        nm = (row["_id"] or "").strip()
        if nm:
            status_options.append(nm)

    payload: Dict[str, Any] = {
        "range":   range_key,
        "scope":   scope,
        "since":   iso_since,
        "filters": {
            "courier":      courier or "all",
            "status":       status or "all",
            "payment_mode": payment_mode or "all",
            "state":        state or "all",
        },
        "filter_options": {
            "couriers": courier_options,
            "statuses": status_options,
            "states":   state_options,
        },
        "kpi": {
            "total":     total_shipments,
            "delivered": delivered_count,
            "pending":   pending_count,
            "revenue":   revenue_total,
            "revenue_cod":     revenue_cod,
            "revenue_prepaid": revenue_prepaid,
        },
        "shipments": {
            "total":      total_shipments,
            "by_status":  by_status,
            "by_courier": by_courier,
            "by_payment": by_payment,
            "by_state":   by_state,
            "by_city":    by_city,
        },
        "trend_30d": daily_trend,
    }

    # Admin-only platform-wide extras (users + system health). -----
    if scope == "platform":
        users_total = await db.users.count_documents({"deleted_at": {"$exists": False}})
        users_today = await db.users.count_documents({
            "created_at": {
                "$gte": datetime.now(timezone.utc).replace(
                    hour=0, minute=0, second=0, microsecond=0,
                ).isoformat(),
            },
            "deleted_at": {"$exists": False},
        })
        # Top 5 users by shipment volume in current ship_match.
        user_ids: List[str] = []
        top_user_rows: List[Dict[str, Any]] = []
        async for row in db.shipments.aggregate([
            {"$match": ship_match},
            {"$group": {"_id": "$user_id", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 5},
        ]):
            user_ids.append(row["_id"])
            top_user_rows.append(row)
        user_lookup: Dict[str, Dict[str, Any]] = {}
        if user_ids:
            cur = db.users.find(
                {"id": {"$in": user_ids}},
                {"_id": 0, "id": 1, "email": 1, "name": 1},
            )
            async for u in cur:
                user_lookup[u["id"]] = u
        top_users = [{
            "user_id": r["_id"],
            "name":    user_lookup.get(r["_id"], {}).get("name") or "",
            "email":   user_lookup.get(r["_id"], {}).get("email") or "—",
            "count":   int(r["count"]),
        } for r in top_user_rows]
        sla_open = await db.sla_alerts.count_documents({"dismissed": False})
        payload["admin"] = {
            "users": {"total": users_total, "today": users_today},
            "top_users": top_users,
            "sla_open": sla_open,
        }

    return payload


@analytics_router.get("/admin/analytics/overview")
async def admin_analytics_overview(
    range_key: str = Query("30d", alias="range"),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """One-shot dashboard payload — KPI cards, time-series, top lists."""
    _require_admin(current_user)
    since = _range_to_since(range_key)
    iso_since = since.isoformat() if since else None
    today_midnight = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0,
    ).isoformat()
    seven_days_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

    # ── 1) Users ──────────────────────────────────────────────────
    users_total = await db.users.count_documents({"deleted_at": {"$exists": False}})
    users_today = await db.users.count_documents({
        "created_at": {"$gte": today_midnight},
        "deleted_at": {"$exists": False},
    })
    users_7d = await db.users.count_documents({
        "created_at": {"$gte": seven_days_ago},
        "deleted_at": {"$exists": False},
    })
    users_in_range = await db.users.count_documents({
        "created_at": {"$gte": iso_since} if iso_since else {"$exists": True},
        "deleted_at": {"$exists": False},
    })

    # Active = at least 1 shipment created in range.
    if iso_since:
        cur = db.shipments.aggregate([
            {"$match": {"created_at": {"$gte": iso_since}}},
            {"$group": {"_id": "$user_id"}},
            {"$count": "n"},
        ])
        first = await cur.to_list(length=1)
        active_users = (first[0]["n"] if first else 0)
    else:
        active_users = users_total

    # ── 2) Shipments ──────────────────────────────────────────────
    ship_match: Dict[str, Any] = {"deleted_at": {"$exists": False}}
    if iso_since:
        ship_match["created_at"] = {"$gte": iso_since}
    total_shipments = await db.shipments.count_documents(ship_match)

    by_status: Dict[str, int] = {}
    async for row in db.shipments.aggregate([
        {"$match": ship_match},
        {"$group": {"_id": "$status", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]):
        by_status[row["_id"] or "Unknown"] = int(row["count"])

    by_courier: List[Dict[str, Any]] = []
    async for row in db.shipments.aggregate([
        {"$match": ship_match},
        {"$group": {"_id": "$courier_name", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 7},
    ]):
        by_courier.append({"name": row["_id"] or "Unknown", "count": int(row["count"])})

    # ── 3) 30-day creation trend (always 30 days, irrespective of range) ─
    trend_since = (datetime.now(timezone.utc) - timedelta(days=29)).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    by_day_raw: Dict[str, int] = {}
    async for row in db.shipments.aggregate([
        {"$match": {
            "deleted_at": {"$exists": False},
            "created_at": {"$gte": trend_since.isoformat()},
        }},
        {"$group": {
            "_id": {"$substr": ["$created_at", 0, 10]},
            "count": {"$sum": 1},
        }},
    ]):
        by_day_raw[row["_id"]] = int(row["count"])
    daily_trend = [
        {
            "date":  (trend_since + timedelta(days=i)).strftime("%Y-%m-%d"),
            "count": int(by_day_raw.get(
                (trend_since + timedelta(days=i)).strftime("%Y-%m-%d"), 0)),
        }
        for i in range(30)
    ]

    # ── 4) Top 5 users by shipment volume in range ────────────────
    top_user_rows = []
    user_ids: List[str] = []
    async for row in db.shipments.aggregate([
        {"$match": ship_match},
        {"$group": {"_id": "$user_id", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 5},
    ]):
        user_ids.append(row["_id"])
        top_user_rows.append(row)
    user_lookup: Dict[str, Dict[str, Any]] = {}
    if user_ids:
        cur = db.users.find(
            {"id": {"$in": user_ids}},
            {"_id": 0, "id": 1, "email": 1, "name": 1},
        )
        async for u in cur:
            user_lookup[u["id"]] = u
    top_users = [{
        "user_id": r["_id"],
        "name":    user_lookup.get(r["_id"], {}).get("name") or "",
        "email":   user_lookup.get(r["_id"], {}).get("email") or "—",
        "count":   int(r["count"]),
    } for r in top_user_rows]

    # ── 5) SLA health ─────────────────────────────────────────────
    sla_open = await db.sla_alerts.count_documents({"dismissed": False})
    if iso_since:
        sla_dismissed_in_range = await db.sla_alerts.count_documents({
            "dismissed":    True,
            "dismissed_at": {"$gte": iso_since},
        })
    else:
        sla_dismissed_in_range = await db.sla_alerts.count_documents({"dismissed": True})

    # ── 6) WhatsApp activity (today) ──────────────────────────────
    today_yyyy_mm_dd = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    wa_today_total = 0
    async for row in db.settings.aggregate([
        {"$match": {"wa_daily_counter.day": today_yyyy_mm_dd}},
        {"$group": {"_id": None, "total": {"$sum": "$wa_daily_counter.count"}}},
    ]):
        wa_today_total = int(row.get("total") or 0)

    # ── 7) Sheet-sync health (across all users) ───────────────────
    sheet_counts: Dict[str, int] = {}
    async for row in db.shipments.aggregate([
        {"$match": {"deleted_at": {"$exists": False}}},
        {"$group": {"_id": "$user_sheet_sync_status", "count": {"$sum": 1}}},
    ]):
        sheet_counts[row["_id"] or "never"] = int(row["count"])
    queue_pending = await db.user_sheet_sync_pending.count_documents({})
    sheets_connected = await db.settings.count_documents({
        "sheet.sheet_id": {"$exists": True, "$ne": ""},
    })

    # ── 8) Revenue (best-effort; tolerates missing `payments` coll) ─
    revenue_total = 0
    revenue_in_range = 0
    try:
        async for row in db.payments.aggregate([
            {"$match": {"status": "captured"}},
            {"$group": {"_id": None, "sum": {"$sum": "$amount_inr"}}},
        ]):
            revenue_total = int(row.get("sum") or 0)
        match_range: Dict[str, Any] = {"status": "captured"}
        if iso_since:
            match_range["created_at"] = {"$gte": iso_since}
        async for row in db.payments.aggregate([
            {"$match": match_range},
            {"$group": {"_id": None, "sum": {"$sum": "$amount_inr"}}},
        ]):
            revenue_in_range = int(row.get("sum") or 0)
    except Exception:
        pass

    return {
        "range":      range_key,
        "since":      iso_since,
        "users": {
            "total":       users_total,
            "today":       users_today,
            "last_7_days": users_7d,
            "in_range":    users_in_range,
            "active":      active_users,
        },
        "shipments": {
            "total":      total_shipments,
            "by_status":  by_status,
            "by_courier": by_courier,
        },
        "trend_30d": daily_trend,
        "top_users": top_users,
        "sla": {
            "open":               sla_open,
            "dismissed_in_range": sla_dismissed_in_range,
        },
        "whatsapp": {
            "messages_today": wa_today_total,
        },
        "sheet_sync": {
            "connected_users": sheets_connected,
            "counts":          sheet_counts,
            "queue_pending":   queue_pending,
        },
        "revenue": {
            "total":    revenue_total,
            "in_range": revenue_in_range,
            "currency": "INR",
        },
    }




# Register the Phase-H sub-router on the app (api_router was already
# included earlier so we can't add new routes to it).
app.include_router(sheet_sync_router)
app.include_router(analytics_router)

# Phase J — Legal pages (Privacy, Terms, Refund) hosted directly on
# the backend so they have a stable public URL for Play Store review.
try:
    from legal_pages import legal_router as _legal_router
    app.include_router(_legal_router)
except Exception:
    logger.exception("Failed to mount legal_pages router (non-fatal)")


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
