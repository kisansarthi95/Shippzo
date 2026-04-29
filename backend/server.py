from fastapi import FastAPI, APIRouter, HTTPException, Depends, Request
from fastapi.responses import PlainTextResponse
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient

# CRITICAL: load .env BEFORE importing any module that reads
# os.environ at module-load time (auth.py reads JWT_SECRET).
# Otherwise the JWT secret would be regenerated on every restart and
# all sessions would be invalidated.
load_dotenv()

# Phase-1 auth (email+password, JWT, per-user data isolation)
from auth import (
    SignupRequest, LoginRequest, UserPublic,
    ForgotPasswordRequest,
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
from pincode_lookup import enrich_with_pincode, validate_pincode_consistency
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
from typing import List, Optional, Dict, Any
import uuid
from datetime import datetime, timezone, timedelta
from dateutil.relativedelta import relativedelta

# Google Sheets writer (Service Account)
try:
    from sheet_writer import append_order_row as sheet_append_order_row
    from sheet_writer import append_order_row_to_user_sheet as sheet_append_user
    from sheet_writer import probe_connection as sheet_probe_connection
    from sheet_writer import mark_row_deleted as sheet_mark_row_deleted
    from sheet_writer import parse_row_from_updated_range as sheet_parse_row_from_updated_range
    from sheet_writer import update_row_status as sheet_update_row_status
    from sheet_writer import get_service_account_email as sheet_get_sa_email
    from sheet_writer import read_user_sheet as sheet_read_user_sheet
except Exception as _sheet_import_err:  # pragma: no cover
    sheet_append_order_row = None  # type: ignore
    sheet_append_user = None  # type: ignore
    sheet_probe_connection = None  # type: ignore
    sheet_mark_row_deleted = None  # type: ignore
    sheet_parse_row_from_updated_range = None  # type: ignore
    sheet_update_row_status = None  # type: ignore
    sheet_get_sa_email = None  # type: ignore
    sheet_read_user_sheet = None  # type: ignore


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
    trial_spec = plan_start_payload("free_trial")
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
        "created_at": now,
    }
    await db.users.insert_one(user_doc)

    if is_first:
        # Developer/admin account — inherits the legacy rows so nothing is orphaned.
        claimed = await claim_legacy_data_for_admin(db, uid)
        logger.info(f"Admin {email} claimed legacy rows: {claimed}")
    else:
        # Fresh user — seed starter courier + 15 demo shipments.
        cid = await seed_default_courier(db, uid)
        await seed_demo_shipments(db, uid, cid)
        # Trial bonus: 10 free credits so new users can try Photo OCR
        # (~5 calls) and AI text parsing for the first time without
        # paying. After the bonus is consumed they top up via Wallet.
        try:
            await wallet_add_credits(
                db, uid, 10.0,
                ctype="bonus",
                description="Welcome bonus — 10 free credits to try AI features",
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
        trial_spec = plan_start_payload("free_trial")
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
            await seed_demo_shipments(db, uid, cid)
            try:
                await wallet_add_credits(
                    db, uid, 10.0,
                    ctype="bonus",
                    description="Welcome bonus — 10 free credits to try AI features",
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

@api_router.post("/demo/clear")
async def clear_demo_data(current_user: Dict[str, Any] = Depends(get_current_user)):
    """Removes every row still flagged `is_demo: True` for this user.
    Non-demo (real) shipments are never touched, so running this after
    a user has added real orders is safe. Demo rows have no Sheet row,
    so no tombstone write is needed."""
    res = await db.shipments.delete_many({"user_id": current_user["id"], "is_demo": True})
    return {"ok": True, "deleted": res.deleted_count}


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
    # Per-shipment dynamic custom field values.
    # Key = CustomLabelField.id, Value = the text to print for this shipment.
    custom_values: Dict[str, str] = Field(default_factory=dict)
    status: str = "Pending"
    created_at: str = Field(default_factory=utcnow_iso)
    delivered_at: Optional[str] = None
    sheet_row_key: str = ""     # used to dedupe/reference imported rows
    # Soft-delete audit: if this shipment was appended to the Master Sheet
    # (via Smart Paste), we remember the exact row number so deletion can
    # mark it as "DELETED" instead of actually removing the row.
    sheet_row_num: Optional[int] = None


class ShipmentCreate(BaseModel):
    tracking_id: str
    courier_id: Optional[str] = None
    courier_name: Optional[str] = ""
    master_order_id: Optional[str] = ""
    order_id: Optional[str] = ""
    customer_name: str
    customer_phone: Optional[str] = ""
    customer_alt_phone: Optional[str] = ""
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

@api_router.get("/")
async def root():
    return {"message": "Courier Label Manager API"}


# -------- Couriers --------

@api_router.get("/couriers", response_model=List[Courier])
async def list_couriers(current_user: Dict[str, Any] = Depends(get_current_user)):
    docs = await db.couriers.find({"user_id": current_user["id"]}, {"_id": 0}).sort("created_at", 1).to_list(200)
    return [Courier(**d) for d in docs]


@api_router.post("/couriers", response_model=Courier)
async def create_courier(
    payload: CourierCreate,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    courier = Courier(**payload.model_dump())
    doc = courier.model_dump()
    doc["user_id"] = current_user["id"]
    await db.couriers.insert_one(doc)
    return courier


@api_router.put("/couriers/{courier_id}", response_model=Courier)
async def update_courier(
    courier_id: str, payload: CourierUpdate,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    update = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not update:
        raise HTTPException(status_code=400, detail="No fields to update")
    res = await db.couriers.find_one_and_update(
        {"id": courier_id, "user_id": current_user["id"]}, {"$set": update}, return_document=True
    )
    if not res:
        raise HTTPException(status_code=404, detail="Courier not found")
    return Courier(**strip_id(res))


@api_router.delete("/couriers/{courier_id}")
async def delete_courier(
    courier_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    res = await db.couriers.delete_one({"id": courier_id, "user_id": current_user["id"]})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Courier not found")
    return {"ok": True}


@api_router.get("/couriers/{courier_id}", response_model=Courier)
async def get_courier(
    courier_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    doc = await db.couriers.find_one({"id": courier_id, "user_id": current_user["id"]}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Courier not found")
    return Courier(**doc)


@api_router.get("/couriers/{courier_id}/next-tracking")
async def peek_next_tracking(
    courier_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    doc = await db.couriers.find_one({"id": courier_id, "user_id": current_user["id"]}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Courier not found")
    c = Courier(**doc)
    num = str(c.next_number).zfill(c.number_padding)
    return {"tracking_id": f"{c.series_prefix}{num}", "next_number": c.next_number}


@api_router.post("/couriers/{courier_id}/consume-tracking")
async def consume_tracking(
    courier_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    doc = await db.couriers.find_one({"id": courier_id, "user_id": current_user["id"]}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Courier not found")
    c = Courier(**doc)
    tid = f"{c.series_prefix}{str(c.next_number).zfill(c.number_padding)}"
    await db.couriers.update_one({"id": courier_id, "user_id": current_user["id"]}, {"$inc": {"next_number": 1}})
    return {"tracking_id": tid}


# -------- Settings --------

@api_router.get("/settings", response_model=Settings)
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


@api_router.put("/settings", response_model=Settings)
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


@api_router.get("/sheets/service-account")
async def get_sheets_service_account_email(
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Return the Service Account email so the user can share their Sheet
    with it (Editor role). This keeps the user's Sheet PRIVATE — only the
    SA is granted access — instead of forcing them to set "Anyone with
    the link → Viewer" (the older public-CSV path).
    """
    email = sheet_get_sa_email() if sheet_get_sa_email else ""
    return {
        "email": email,
        "instructions": (
            "Open your Google Sheet → Share → paste this email → choose "
            "'Editor' → Send. Then come back here and connect."
        ),
    }


@api_router.post("/sheets/preview")
async def sheets_preview(payload: SheetPreviewRequest):
    parsed = parse_sheet_url(payload.url)

    # Phase-5: Service-Account-first read. Only fall back to the legacy
    # public-CSV path if the SA can't access the sheet AND the user has
    # made it public anyway.
    if sheet_read_user_sheet is not None:
        sa_resp = sheet_read_user_sheet(parsed["sheet_id"], parsed["gid"] or "0")
        if sa_resp.get("ok"):
            headers = sa_resp.get("headers", [])
            rows = sa_resp.get("rows", [])
            guess = auto_guess_mapping(headers)
            return {
                "sheet_id": parsed["sheet_id"],
                "gid": parsed["gid"],
                "headers": headers,
                "sample_rows": rows[:5],
                "total_rows": len(rows),
                "auto_mapping": guess,
                "access_method": "service_account",
            }
        err = (sa_resp.get("error") or "").strip()
        # On "not shared" / "not found" we still try the legacy CSV path
        # so users who have public sheets keep working with no migration.
        if err in ("SHEET_NOT_SHARED", "SHEET_NOT_FOUND"):
            try:
                csv_text = await fetch_sheet_csv(parsed["sheet_id"], parsed["gid"])
                data = parse_csv_rows(csv_text)
                guess = auto_guess_mapping(data["headers"])
                return {
                    "sheet_id": parsed["sheet_id"],
                    "gid": parsed["gid"],
                    "headers": data["headers"],
                    "sample_rows": data["rows"][:5],
                    "total_rows": len(data["rows"]),
                    "auto_mapping": guess,
                    "access_method": "public_csv",
                }
            except HTTPException:
                # Neither SA nor public works — surface the SA-share guide.
                sa_email = sheet_get_sa_email() if sheet_get_sa_email else ""
                raise HTTPException(
                    status_code=403,
                    detail=(
                        "We can't open that sheet. Either:\n"
                        f"  1. Share it with {sa_email or '<our service account>'} "
                        "(Editor) — recommended; keeps it private, OR\n"
                        "  2. Open Share → 'Anyone with the link → Viewer'.\n"
                        "Then try again."
                    ),
                )
        # Some other unexpected SA error → bubble through CSV path.
        try:
            csv_text = await fetch_sheet_csv(parsed["sheet_id"], parsed["gid"])
            data = parse_csv_rows(csv_text)
            guess = auto_guess_mapping(data["headers"])
            return {
                "sheet_id": parsed["sheet_id"],
                "gid": parsed["gid"],
                "headers": data["headers"],
                "sample_rows": data["rows"][:5],
                "total_rows": len(data["rows"]),
                "auto_mapping": guess,
                "access_method": "public_csv",
            }
        except HTTPException:
            raise HTTPException(status_code=400, detail=f"Sheet read failed: {err or 'unknown error'}")

    # Hard fallback (sheet_writer module unavailable) — legacy CSV path only.
    csv_text = await fetch_sheet_csv(parsed["sheet_id"], parsed["gid"])
    data = parse_csv_rows(csv_text)
    guess = auto_guess_mapping(data["headers"])
    return {
        "sheet_id": parsed["sheet_id"],
        "gid": parsed["gid"],
        "headers": data["headers"],
        "sample_rows": data["rows"][:5],
        "total_rows": len(data["rows"]),
        "auto_mapping": guess,
        "access_method": "public_csv",
    }


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


@api_router.get("/sheets/orders")
async def sheets_orders(current_user: Dict[str, Any] = Depends(get_current_user)):
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

    # Detect header changes
    headers_changed = data["headers"] != cfg.headers

    mapping = cfg.column_mapping or {}

    # Find shipments that were imported from this sheet to mark them
    imported_keys = set()
    existing = await db.shipments.find(
        {"user_id": current_user["id"], "sheet_row_key": {"$ne": ""}},
        {"_id": 0, "sheet_row_key": 1},
    ).to_list(5000)
    for e in existing:
        if e.get("sheet_row_key"):
            imported_keys.add(e["sheet_row_key"])

    def mapped(row: Dict[str, str], key: str) -> str:
        col = mapping.get(key)
        if not col:
            return ""
        return row.get(col, "")

    orders = []
    for idx, row in enumerate(data["rows"]):
        row_key = _row_key(row, mapping, idx)
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
            "already_shipped": row_key in imported_keys,
            "raw": row,
        })
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


# -------- Shipments --------

@api_router.get("/shipments", response_model=List[Shipment])
async def list_shipments(
    status: Optional[str] = None,
    courier_id: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 500,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    # Always scope to the logged-in user so one tenant never sees another's data.
    q: dict = {"user_id": current_user["id"]}
    if status:
        q["status"] = status
    if courier_id:
        q["courier_id"] = courier_id
    if search:
        q["$or"] = [
            {"tracking_id": {"$regex": search, "$options": "i"}},
            {"customer_name": {"$regex": search, "$options": "i"}},
            {"customer_phone": {"$regex": search, "$options": "i"}},
            {"city": {"$regex": search, "$options": "i"}},
            {"order_id": {"$regex": search, "$options": "i"}},
        ]
    docs = await db.shipments.find(q, {"_id": 0}).sort("created_at", -1).to_list(limit)
    return [Shipment(**d) for d in docs]


@api_router.get("/shipments/stats")
async def shipments_stats(current_user: Dict[str, Any] = Depends(get_current_user)):
    base = {"user_id": current_user["id"]}
    total = await db.shipments.count_documents(base)
    delivered = await db.shipments.count_documents({**base, "status": "Delivered"})
    pending = await db.shipments.count_documents({**base, "status": "Pending"})
    cod_cursor = db.shipments.aggregate([
        {"$match": {**base, "payment_mode": "COD", "status": {"$ne": "Cancelled"}}},
        {"$group": {"_id": None, "sum": {"$sum": "$amount"}, "count": {"$sum": 1}}},
    ])
    cod_sum = 0.0
    cod_count = 0
    async for row in cod_cursor:
        cod_sum = float(row.get("sum", 0.0))
        cod_count = int(row.get("count", 0))
    prepaid_cursor = db.shipments.aggregate([
        {"$match": {**base, "payment_mode": "Prepaid", "status": {"$ne": "Cancelled"}}},
        {"$group": {"_id": None, "sum": {"$sum": "$amount"}, "count": {"$sum": 1}}},
    ])
    prepaid_sum = 0.0
    prepaid_count = 0
    async for row in prepaid_cursor:
        prepaid_sum = float(row.get("sum", 0.0))
        prepaid_count = int(row.get("count", 0))
    return {
        "total": total,
        "delivered": delivered,
        "pending": pending,
        "cod_total": cod_sum,
        "cod_count": cod_count,
        "prepaid_total": prepaid_sum,
        "prepaid_count": prepaid_count,
        "revenue_total": cod_sum + prepaid_sum,
    }


@api_router.get("/sheets/sample-template", response_class=PlainTextResponse)
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


@api_router.get("/shipments/export/csv", response_class=PlainTextResponse)
async def export_csv(current_user: Dict[str, Any] = Depends(get_current_user)):
    docs = await db.shipments.find({"user_id": current_user["id"]}, {"_id": 0}).sort("created_at", -1).to_list(5000)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "Tracking ID", "Courier", "Order ID", "Customer", "Phone",
        "Address Line 1", "Address Line 2", "City", "State", "Pincode",
        "Payment Mode", "Amount", "Items", "Weight",
        "Status", "Created At", "Delivered At",
    ])
    for d in docs:
        items = d.get("items") or []
        items_str = "; ".join(items) if items else d.get("item_description", "")
        writer.writerow([
            d.get("tracking_id", ""), d.get("courier_name", ""),
            d.get("order_id", ""),
            d.get("customer_name", ""), d.get("customer_phone", ""),
            d.get("address_line1", ""), d.get("address_line2", ""),
            d.get("city", ""), d.get("state", ""), d.get("pincode", ""),
            d.get("payment_mode", ""), d.get("amount", 0),
            items_str, d.get("weight", ""),
            d.get("status", ""), d.get("created_at", ""), d.get("delivered_at", ""),
        ])
    return PlainTextResponse(buf.getvalue(), media_type="text/csv")


@api_router.get("/shipments/by-tracking/{tracking_id}")
async def get_shipment_by_tracking(
    tracking_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    doc = await db.shipments.find_one(
        {
            "user_id": current_user["id"],
            "tracking_id": {"$regex": f"^{tracking_id}$", "$options": "i"},
        },
        {"_id": 0},
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Not found")
    return Shipment(**doc)


@api_router.post("/shipments/bulk-fetch")
async def bulk_fetch(
    payload: Dict[str, List[str]],
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    ids = payload.get("ids", [])
    if not ids:
        return []
    docs = await db.shipments.find(
        {"user_id": current_user["id"], "id": {"$in": ids}},
        {"_id": 0},
    ).to_list(500)
    by_id = {d["id"]: Shipment(**d) for d in docs}
    ordered = [by_id[i].model_dump() for i in ids if i in by_id]
    return ordered


@api_router.get("/customers/by-phone/{phone}")
async def get_customer_by_phone(
    phone: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Look up the most-recent customer record for a given phone number
    within the current user's workspace. Powers the Smart Paste AI
    "repeat customer" suggestion so users don't have to re-type past
    addresses.

    Search order:
      1. shipments collection (most authoritative — already dispatched).
      2. pending_orders collection (pasted but not shipped yet).

    Returns { found: bool, customer: {...} | null, count: int }.
    """
    # Normalise to last 10 digits for robust match (strips +91 / spaces).
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    if len(digits) < 10:
        return {"found": False, "customer": None, "count": 0}
    tail = digits[-10:]
    rx = {"$regex": f"{tail}$"}

    ship_cursor = db.shipments.find(
        {"user_id": current_user["id"], "customer_phone": rx},
        {"_id": 0},
    ).sort("created_at", -1)
    ships: List[Dict[str, Any]] = await ship_cursor.to_list(10)

    if ships:
        s = ships[0]
        last_items = s.get("items") or []
        last_amount = s.get("amount")
        return {
            "found": True,
            "count": len(ships),
            "customer": {
                "customer_name": s.get("customer_name", ""),
                "customer_phone": s.get("customer_phone", ""),
                "address_line1": s.get("address_line1", ""),
                "address_line2": s.get("address_line2", ""),
                "city": s.get("city", ""),
                "state": s.get("state", ""),
                "pincode": s.get("pincode", ""),
                "last_items": last_items,
                "last_amount": last_amount,
                "source": "shipment",
                "last_tracking_id": s.get("tracking_id", ""),
                "last_date": s.get("created_at", ""),
            },
        }

    # Fallback: look in pending orders queue.
    pend_cursor = db.pending_orders.find(
        {"user_id": current_user["id"], "customer_phone": rx},
        {"_id": 0},
    ).sort("created_at", -1)
    pends: List[Dict[str, Any]] = await pend_cursor.to_list(5)
    if pends:
        p = pends[0]
        return {
            "found": True,
            "count": len(pends),
            "customer": {
                "customer_name": p.get("customer_name", ""),
                "customer_phone": p.get("customer_phone", ""),
                "address_line1": p.get("address_line1", ""),
                "address_line2": p.get("address_line2", ""),
                "city": p.get("city", ""),
                "state": p.get("state", ""),
                "pincode": p.get("pincode", ""),
                "source": "pending",
                "last_date": p.get("created_at", ""),
            },
        }
    return {"found": False, "customer": None, "count": 0}




@api_router.get("/shipments/{shipment_id}", response_model=Shipment)
async def get_shipment(
    shipment_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    doc = await db.shipments.find_one(
        {"id": shipment_id, "user_id": current_user["id"]}, {"_id": 0}
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Shipment not found")
    return Shipment(**doc)


@api_router.post("/shipments", response_model=Shipment)
async def create_shipment(
    payload: ShipmentCreate,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    # Phase-3a/4a combined gate:
    #   • If plan has room  → consume a plan slot + AI credit.
    #   • If plan exhausted → rely on wallet overage (paid plans only);
    #     free-trial users must upgrade (no overage).
    #   • Free-trial expired → refuse outright.
    room = await plan_room_status(db, current_user)
    if room["trial_expired"]:
        raise HTTPException(
            status_code=402,
            detail="Your 7-day free trial has expired. Upgrade to continue.",
        )
    if room.get("plan_expired"):
        # Paid subscription past plan_expires_at — block until renewal.
        exp = room.get("plan_expires_at")
        try:
            exp_dt = datetime.fromisoformat(str(exp))
            exp_str = exp_dt.strftime("%d %b %Y")
        except Exception:
            exp_str = str(exp) or "the renewal date"
        raise HTTPException(
            status_code=402,
            detail=(
                f"Your {room.get('plan_name','plan')} subscription expired on "
                f"{exp_str}. Renew from Plans to keep creating labels."
            ),
        )
    if room["daily_blocked"]:
        raise HTTPException(
            status_code=402,
            detail="Daily limit reached (100/day on Platinum). Please try again tomorrow.",
        )
    plan_key = room["plan"]
    plan_has_room = bool(room["plan_has_room"])
    if (not plan_has_room) and plan_key == "free_trial":
        raise HTTPException(
            status_code=402,
            detail=(
                "Free trial limit reached (10 labels). Upgrade to "
                "Silver or higher to keep shipping."
            ),
        )

    data = payload.model_dump()
    addr_text = " ".join(filter(None, [
        data.get("address_line1", ""), data.get("address_line2", ""),
        data.get("city", ""), data.get("state", ""), str(data.get("pincode", "")),
    ])).strip()
    # Fetch per-user AI rate card so Settings → AI Processing Charges
    # takes effect immediately. Defaults 0.5/1/2 used when not set.
    _s = await db.settings.find_one(
        {"user_id": current_user["id"]},
        {"_id": 0, "ai_cost_simple": 1, "ai_cost_medium": 1, "ai_cost_complex": 1},
    ) or {}
    ai_costs = {
        "simple":  float(_s.get("ai_cost_simple", 0.5)),
        "medium":  float(_s.get("ai_cost_medium", 1.0)),
        "complex": float(_s.get("ai_cost_complex", 2.0)),
    }
    # Phase-4b: LLM-backed complexity classification with safe heuristic
    # fallback baked into wallet.classify_and_cost.
    breakdown, ai_reason = await wallet_classify_and_cost(current_user, addr_text, plan_has_room, ai_costs=ai_costs)
    # Re-use the classified complexity for the wallet pre-flight so we
    # don't double-classify.
    breakdown = await wallet_require(
        db, current_user, addr_text, plan_has_room,
        complexity_override=breakdown.ai_complexity,
        ai_costs=ai_costs,
    )

    if data.get("courier_id") and not data.get("courier_name"):
        c = await db.couriers.find_one(
            {"id": data["courier_id"], "user_id": current_user["id"]}, {"_id": 0}
        )
        if c:
            data["courier_name"] = c.get("name", "")
    # ensure amount is populated
    if data.get("payment_mode") == "COD":
        data["cod_amount"] = float(data.get("amount") or data.get("cod_amount") or 0)
    else:
        data["cod_amount"] = 0.0
    data["amount"] = float(data.get("amount") or data.get("cod_amount") or 0)
    if data.get("items") is None:
        data["items"] = []
    if data.get("custom_values") is None:
        data["custom_values"] = {}

    # ---- Phase-7d/e: Master Order ID + User Order ID for manual create ----
    settings_doc = await db.settings.find_one(
        {"user_id": current_user["id"]},
        {"_id": 0, "order_id_auto_generate": 1},
    ) or {}
    auto_gen = bool(settings_doc.get("order_id_auto_generate", True))
    incoming_master = str(data.get("master_order_id") or "").strip()
    user_order_id = str(data.get("order_id") or "").strip()
    if auto_gen:
        if incoming_master and re.match(r"^\d{6}\d{5,}$", incoming_master):
            # Frontend sent a pre-allocated master ID — trust it (atomic
            # peek/consume already happened upstream).
            master_oid = incoming_master
        else:
            master_oid = await generate_master_order_id()
        # uniqueness guard
        retries = 0
        while await db.shipments.find_one({"master_order_id": master_oid, "user_id": current_user["id"]}, {"_id": 1}):
            master_oid = await generate_master_order_id()
            retries += 1
            if retries > 5:
                raise HTTPException(
                    status_code=500,
                    detail="Could not allocate a unique Master Order ID — retry.",
                )
        data["master_order_id"] = master_oid
        if not user_order_id:
            user_order_id = master_oid
        data["order_id"] = user_order_id
    else:
        if not user_order_id:
            raise HTTPException(
                status_code=422,
                detail="Order ID is required when Auto-Generate is OFF. "
                       "Enter your own Order ID or enable Auto-Generate in Settings.",
            )
        data["master_order_id"] = ""
        data["order_id"] = user_order_id

    shipment = Shipment(**data)
    doc = shipment.model_dump()
    doc["user_id"] = current_user["id"]
    await db.shipments.insert_one(doc)
    # Only bump plan counter when the plan actually covered this label.
    if plan_has_room:
        await bump_label_usage(db, current_user)
    # Debit wallet (safe no-op for free-trial + trial-room combo).
    await wallet_charge(db, current_user, doc["id"], breakdown)
    return shipment


@api_router.put("/shipments/{shipment_id}", response_model=Shipment)
async def update_shipment(
    shipment_id: str,
    payload: ShipmentUpdate,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    update = {k: v for k, v in payload.model_dump().items() if v is not None}
    if "status" in update and update["status"] == "Delivered":
        update["delivered_at"] = utcnow_iso()
    if "amount" in update:
        update["cod_amount"] = float(update["amount"]) if update.get("payment_mode", "") == "COD" else 0.0
    if not update:
        raise HTTPException(status_code=400, detail="No fields to update")

    # ---- Two-Way Status Sync: detect status transitions BEFORE mutation
    # so we can write the new value to the Master Sheet row if linked.
    new_status = update.get("status")
    prev_doc = None
    if new_status is not None:
        prev_doc = await db.shipments.find_one(
            {"id": shipment_id, "user_id": current_user["id"]}, {"_id": 0}
        )

    res = await db.shipments.find_one_and_update(
        {"id": shipment_id, "user_id": current_user["id"]},
        {"$set": update},
        return_document=True,
    )
    if not res:
        raise HTTPException(status_code=404, detail="Shipment not found")

    # Best-effort write-back to Google Sheets. Never blocks the local
    # update — logs and moves on so the app stays fast/available.
    if (
        new_status is not None
        and prev_doc is not None
        and (prev_doc.get("status") or "") != new_status
        and prev_doc.get("sheet_row_num")
        and sheet_update_row_status is not None
    ):
        try:
            tracking = prev_doc.get("tracking_id") or res.get("tracking_id") or ""
            extra = f"Tracking: {tracking}" if tracking else None
            sheet_update_row_status(
                int(prev_doc["sheet_row_num"]),
                new_status,
                extra_notice=extra,
            )
            logger.info(
                f"Sheet status sync OK: row={prev_doc['sheet_row_num']} → {new_status}"
            )
        except Exception:
            logger.exception("Sheet status sync failed (non-fatal)")

    return Shipment(**strip_id(res))


@api_router.delete("/shipments/{shipment_id}")
async def delete_shipment(
    shipment_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Soft-delete: if the shipment is linked to a Master Sheet row, mark
    that row's Status="DELETED" before removing the local record. The
    Sheet row itself is preserved as an audit trail so that data never
    disappears from the source-of-truth even when the app-level record
    is removed. Sheet failures do NOT block the local delete — we log
    and proceed so users are never stuck.
    """
    doc = await db.shipments.find_one(
        {"id": shipment_id, "user_id": current_user["id"]}, {"_id": 0}
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Shipment not found")

    sheet_result: Dict[str, Any] = {"attempted": False}
    row_num = doc.get("sheet_row_num")
    if row_num and sheet_mark_row_deleted is not None:
        sheet_result["attempted"] = True
        try:
            reason = (
                f"shipment {doc.get('tracking_id') or doc.get('id')} "
                f"({doc.get('customer_name','')[:40]}) removed from app"
            )
            sheet_result.update(sheet_mark_row_deleted(int(row_num), reason=reason))
        except Exception as e:
            # Don't block local delete — but surface the error to the client
            # so they know the sheet was not marked. Local record still goes.
            logger.exception("Soft-delete sheet mark failed")
            sheet_result["ok"] = False
            sheet_result["error"] = str(e)

    res = await db.shipments.delete_one(
        {"id": shipment_id, "user_id": current_user["id"]}
    )
    if res.deleted_count == 0:
        # Race condition — someone else deleted. Still return 404.
        raise HTTPException(status_code=404, detail="Shipment not found")
    return {"ok": True, "sheet": sheet_result}


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

    created_at: str = Field(default_factory=utcnow_iso)
    processed_at: Optional[str] = None


class SmartPasteRequest(BaseModel):
    text: str
    use_ai: Optional[bool] = True   # Phase-4b+: LLM-driven parse by default
    skip_llm: Optional[bool] = False  # fast path when text is already canonical


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

    return {"fields": result, "confidence": confidence, "warnings": warnings}


@api_router.get("/smart-paste/default-prompt")
async def smart_paste_default_prompt(
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Expose the bundled ShipBot system prompt + the user's current override
    so the Settings screen can pre-fill the textarea."""
    s = await db.settings.find_one(
        {"user_id": current_user["id"]},
        {"_id": 0, "smart_paste_instructions": 1, "smart_paste_ai_enabled": 1},
    ) or {}
    return {
        "default_prompt": DEFAULT_SHIPBOT_PROMPT,
        "user_instructions": s.get("smart_paste_instructions") or "",
        "ai_enabled": s.get("smart_paste_ai_enabled", True),
    }


@api_router.post("/smart-paste/parse")
async def smart_paste_parse(
    payload: SmartPasteRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Parse pasted text only (no save) — for preview/dry-run.

    Phase-4b+: we now try the LLM (ShipBot-style 14-line schema) first,
    then fall back to the deterministic regex parser on any failure so
    the UI never has a blank state. The LLM result also carries:
        missing[]  — fields the user still needs to provide
        complexity — simple/medium/complex classification
        ai_reason  — one-line rationale surfaced in the dialog
    NO wallet charge here — that happens at the final create step.
    """
    text = (payload.text or "").strip()
    legacy = parse_structured_paste(text)

    ai_block: Dict[str, Any] = {
        "used": False, "missing": [], "complexity": "", "reason": "",
        "source": "regex",
    }
    if payload.use_ai is not False:
        # Pick up the user's customisation from Settings (per-tenant).
        s = await db.settings.find_one(
            {"user_id": current_user["id"]}, {"_id": 0, "smart_paste_instructions": 1}
        ) or {}
        custom = (s.get("smart_paste_instructions") or "").strip()
        ai = await parse_paste_via_llm(text, custom_instructions=custom)
        if ai["source"] == "llm":
            mapped = await _legacy_with_pincode_enrich(ai["fields"])
            # Merge: LLM fields WIN over regex where they have a non-empty value.
            merged_fields: Dict[str, Any] = dict(legacy.get("fields", {}))
            for k, v in mapped.items():
                if v:
                    merged_fields[k] = v
            # Post-normalise amount into a float so the UI's numeric field works
            if isinstance(merged_fields.get("amount"), str):
                m = re.search(r"(\d+(?:\.\d+)?)", merged_fields["amount"].replace(",", ""))
                if m:
                    try:
                        merged_fields["amount"] = float(m.group(1))
                    except Exception:
                        pass
            legacy["fields"] = merged_fields
            # Recompute missing_fields AFTER the merge so the UI banner is fresh.
            still_missing = []
            for (schema_key, legacy_key) in [
                ("NAME", "customer_name"), ("PHONE", "customer_phone"),
                ("ADDRESS_1", "address_line1"), ("CITY", "city"),
                ("STATE", "state"), ("PINCODE", "pincode"),
                ("ITEMS", "items"), ("AMOUNT", "amount"),
            ]:
                v = merged_fields.get(legacy_key)
                if not v and (isinstance(v, str) or v in (None, 0)):
                    still_missing.append(schema_key)
            ai_block = {
                "used": True,
                "missing": still_missing,
                "complexity": ai["complexity"],
                "reason": ai["ai_reason"],
                "source": "llm",
            }

    legacy["ai"] = ai_block
    return legacy


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


@api_router.get("/orders/master-id-counter")
async def get_master_id_counter(
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Phase-7f: Read the current global Master Order ID counter.
    The next allocated ID's sequence will be `current_seq + 1`.
    """
    doc = await db.counters.find_one({"_id": "master_order_id"})
    seq = int((doc or {}).get("seq", 0))
    ist_now = datetime.utcnow() + timedelta(hours=5, minutes=30)
    return {
        "current_seq": seq,
        "next_seq": seq + 1,
        "next_master_order_id": f"{ist_now.strftime('%y%m%d')}{str(seq + 1).zfill(5)}",
    }


class _CounterSetPayload(BaseModel):
    seq: int  # The seq value the NEXT allocation should produce.
    force: Optional[bool] = False  # Allow lowering (risk of duplicates).


@api_router.post("/orders/master-id-counter")
async def set_master_id_counter(
    payload: _CounterSetPayload,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Phase-7f: Set the global Master Order ID counter to a specific
    value. Useful when migrating from a legacy system — eg the user
    has already shipped 2200 parcels and wants the next master ID to
    end in `02201` (or `02200` if they pass seq=2199).

    By default, lowering the counter is BLOCKED (creating duplicates
    would break the unique-master_order_id invariant). Pass
    `force: true` to override (admin/migration only — be careful).
    """
    if payload.seq < 0:
        raise HTTPException(status_code=422, detail="seq must be ≥ 0")
    if payload.seq > 9_999_999:
        raise HTTPException(status_code=422, detail="seq too large")
    cur_doc = await db.counters.find_one({"_id": "master_order_id"})
    cur = int((cur_doc or {}).get("seq", 0))
    if payload.seq < cur and not payload.force:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Counter is currently at {cur}. Lowering to {payload.seq} "
                "would risk duplicate Master Order IDs. Pass force=true to override."
            ),
        )
    await db.counters.update_one(
        {"_id": "master_order_id"},
        {"$set": {"seq": int(payload.seq)}},
        upsert=True,
    )
    ist_now = datetime.utcnow() + timedelta(hours=5, minutes=30)
    return {
        "current_seq": int(payload.seq),
        "next_seq": int(payload.seq) + 1,
        "next_master_order_id":
            f"{ist_now.strftime('%y%m%d')}{str(int(payload.seq) + 1).zfill(5)}",
    }


@api_router.get("/orders/peek-master-id")
async def peek_master_id_endpoint(
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Phase-7e: Live preview of the next Master Order ID for the
    New Shipment form. Returns BOTH the predicted master_order_id AND
    the user's two related Settings flags so the frontend can decide
    whether to auto-fill the Order ID input.

    Note: The returned master_order_id is a BEST-GUESS preview. The
    counter is NOT incremented here. The actual ID is allocated only
    when the shipment is saved — so if another user creates a shipment
    in between, the saved ID may differ. Frontend MAY pass the previewed
    value back via `master_order_id` in POST /shipments to avoid
    sequence drift in the common single-user case.
    """
    settings_doc = await db.settings.find_one(
        {"user_id": current_user["id"]},
        {
            "_id": 0,
            "order_id_auto_generate": 1,
            "order_id_autofill_in_new_shipment": 1,
        },
    ) or {}
    auto_gen = bool(settings_doc.get("order_id_auto_generate", True))
    autofill = bool(settings_doc.get("order_id_autofill_in_new_shipment", True))
    if not auto_gen:
        return {
            "master_order_id": "",
            "auto_generate": False,
            "autofill_in_new_shipment": autofill,
        }
    next_id = await peek_next_master_order_id()
    return {
        "master_order_id": next_id,
        "auto_generate": True,
        "autofill_in_new_shipment": autofill,
    }


@api_router.post("/smart-paste/check-duplicate")
async def smart_paste_check_duplicate(
    payload: SmartPasteRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Inspect pasted text for duplicates WITHOUT saving.

    Phase-4b+: we now try the LLM parser first (and merge LLM fields over
    the regex result where non-empty). The ChatGPT-bounce is gone — users
    can paste raw WhatsApp text directly.
    """
    text = (payload.text or "")
    parsed = parse_structured_paste(text)
    fields: Dict[str, Any] = dict(parsed.get("fields", {}) or {})

    # --- LLM pass (best-effort; falls back to regex on any error) ---
    ai_missing: List[str] = []
    ai_complexity = ""
    ai_reason = ""
    ai_source = "regex"
    ai_warnings: List[str] = []
    pincode_warnings: List[str] = []
    try:
        s = await db.settings.find_one(
            {"user_id": current_user["id"]},
            {"_id": 0, "smart_paste_instructions": 1, "smart_paste_ai_enabled": 1},
        ) or {}
        if s.get("smart_paste_ai_enabled", True):
            ai = await parse_paste_via_llm(
                text,
                custom_instructions=(s.get("smart_paste_instructions") or "").strip(),
            )
            if ai.get("source") == "llm":
                ai_warnings = list(ai.get("warnings") or [])
                mapped, pincode_warnings = await _legacy_with_pincode_enrich_v2(ai["fields"])
                for k, v in mapped.items():
                    if v:
                        fields[k] = v
                # Amount as float
                if isinstance(fields.get("amount"), str):
                    m = re.search(r"(\d+(?:\.\d+)?)", fields["amount"].replace(",", ""))
                    if m:
                        try:
                            fields["amount"] = float(m.group(1))
                        except Exception:
                            pass
                ai_source = "llm"
                ai_complexity = ai.get("complexity", "")
                ai_reason = ai.get("ai_reason", "")
                # Compute still-missing fields the user must provide
                for (_sk, _lk) in [
                    ("NAME", "customer_name"), ("PHONE", "customer_phone"),
                    ("ADDRESS_1", "address_line1"), ("CITY", "city"),
                    ("STATE", "state"), ("PINCODE", "pincode"),
                    ("ITEMS", "items"), ("AMOUNT", "amount"),
                ]:
                    v = fields.get(_lk)
                    if not v and (isinstance(v, str) or v in (None, 0)):
                        ai_missing.append(_sk)
    except Exception:
        logger.exception("LLM path failed on check-duplicate — using regex only")

    duplicates = await find_duplicate_matches(
        phone=fields.get("customer_phone", ""),
        order_id=fields.get("order_id", "") or fields.get("order_id_hint", ""),
        user_id=current_user["id"],
    )
    # Combine all warnings from regex + AI + pincode validation.
    all_warnings = list(parsed.get("warnings", []) or [])
    all_warnings.extend(ai_warnings)
    all_warnings.extend(pincode_warnings)
    return {
        "fields": fields,
        "confidence": parsed.get("confidence", {}),
        "warnings": all_warnings,
        "duplicates": duplicates,
        "ai": {
            "used": ai_source == "llm",
            "missing": ai_missing,
            "complexity": ai_complexity,
            "reason": ai_reason,
            "source": ai_source,
        },
    }



# Labels / order used by the chat endpoint to build both the synthetic
# structured block it feeds to the LLM and the natural-language AI
# message returned to the client.
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
    }


@api_router.post("/smart-paste/chat")
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



@api_router.post("/smart-paste/photo")
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



@api_router.post("/smart-paste", response_model=PendingOrder)
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

    # ---- 1b) Phase-B: best-effort write to the user's OWN sheet ----
    # If the user has linked their personal sheet via Settings.sheet,
    # mirror the same row there so they have a private copy. We swallow
    # all exceptions — Master Sheet is the source of truth.
    user_sheet_meta: Dict[str, Any] = {"ok": False, "skipped": True}
    if sheet_append_user is not None:
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
        **{k: v for k, v in fields.items() if k in PendingOrder.model_fields
           and k not in ("sheet_row_num",)},
    )
    # Stash sheet-write metadata on the model's raw_text for debugging if needed
    doc = po.model_dump()
    doc["_sheet_meta"] = sheet_meta
    doc["user_id"] = current_user["id"]
    await db.pending_orders.insert_one(doc)
    return po


@api_router.get("/sheets/probe")
async def sheets_probe():
    """Quick debug endpoint — verifies Service Account can read the Master Sheet."""
    if sheet_probe_connection is None:
        return {"ok": False, "error": "gspread not installed"}
    return sheet_probe_connection()


@api_router.get("/orders/pending", response_model=List[PendingOrder])
async def list_pending_orders(
    source: Optional[str] = None,
    status: Optional[str] = None,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    q: Dict[str, Any] = {"user_id": current_user["id"]}
    if source:
        q["source"] = source
    if status:
        q["status"] = status
    else:
        q["status"] = "pending"
    cursor = db.pending_orders.find(q, {"_id": 0}).sort("created_at", -1)
    return await cursor.to_list(length=500)


@api_router.get("/orders/pending/{order_id}", response_model=PendingOrder)
async def get_pending_order(
    order_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    doc = await db.pending_orders.find_one(
        {"id": order_id, "user_id": current_user["id"]}, {"_id": 0}
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Order not found")
    return doc


@api_router.put("/orders/pending/{order_id}", response_model=PendingOrder)
async def update_pending_order(
    order_id: str,
    payload: Dict[str, Any],
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    # Allow partial field updates (user edits before shipping)
    allowed = {k for k in PendingOrder.model_fields if k not in ("id", "created_at", "source")}
    upd = {k: v for k, v in payload.items() if k in allowed}
    if not upd:
        raise HTTPException(status_code=400, detail="No valid fields to update")
    res = await db.pending_orders.update_one(
        {"id": order_id, "user_id": current_user["id"]}, {"$set": upd}
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Order not found")
    doc = await db.pending_orders.find_one(
        {"id": order_id, "user_id": current_user["id"]}, {"_id": 0}
    )
    return doc


@api_router.delete("/orders/pending/{order_id}")
async def delete_pending_order(
    order_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Soft-delete pending (Smart-Paste) orders: tombstone the Master Sheet
    row if linked, then remove the local record. Sheet failures are logged
    but do not block local deletion."""
    doc = await db.pending_orders.find_one(
        {"id": order_id, "user_id": current_user["id"]}, {"_id": 0}
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Order not found")

    sheet_result: Dict[str, Any] = {"attempted": False}
    row_num = doc.get("sheet_row_num")
    if row_num and sheet_mark_row_deleted is not None:
        sheet_result["attempted"] = True
        try:
            reason = (
                f"pending order {doc.get('order_id_hint') or order_id[:8]} "
                f"({(doc.get('customer_name') or '')[:40]}) removed from app"
            )
            sheet_result.update(sheet_mark_row_deleted(int(row_num), reason=reason))
        except Exception as e:
            logger.exception("Soft-delete sheet mark failed (pending)")
            sheet_result["ok"] = False
            sheet_result["error"] = str(e)

    res = await db.pending_orders.delete_one(
        {"id": order_id, "user_id": current_user["id"]}
    )
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Order not found")
    return {"ok": True, "sheet": sheet_result}


@api_router.post("/orders/pending/{order_id}/ship", response_model=Shipment)
async def ship_pending_order(
    order_id: str,
    payload: ShipOrderRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Promote a pending order to a real shipment — allocates tracking ID."""
    # Phase-3a/4a combined gate
    room = await plan_room_status(db, current_user)
    if room["trial_expired"]:
        raise HTTPException(status_code=402, detail="Your 7-day free trial has expired. Upgrade to continue.")
    if room["daily_blocked"]:
        raise HTTPException(status_code=402, detail="Daily limit reached. Please try again tomorrow.")
    plan_has_room = bool(room["plan_has_room"])
    if (not plan_has_room) and room["plan"] == "free_trial":
        raise HTTPException(status_code=402, detail="Free trial limit reached (10 labels). Upgrade to Silver or higher.")

    order = await db.pending_orders.find_one(
        {"id": order_id, "user_id": current_user["id"]}, {"_id": 0}
    )
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.get("status") == "shipped":
        raise HTTPException(status_code=400, detail="Order already shipped")

    courier = await db.couriers.find_one(
        {"id": payload.courier_id, "user_id": current_user["id"]}, {"_id": 0}
    )
    if not courier:
        raise HTTPException(status_code=404, detail="Courier not found")

    # Allocate tracking ID
    padding = int(courier.get("number_padding") or 4)
    next_num = int(courier.get("next_number") or 1)
    tracking_id = f"{courier.get('series_prefix','')}{str(next_num).zfill(padding)}"
    await db.couriers.update_one(
        {"id": courier["id"], "user_id": current_user["id"]},
        {"$inc": {"next_number": 1}},
    )

    # Build shipment from order + optional overrides
    overrides = payload.overrides or {}
    def get(k, default=""):
        return overrides.get(k, order.get(k, default))

    # items as list (stored as comma separated in pending_orders)
    items_str = get("items", "")
    items_list = [s.strip() for s in (items_str.split(",") if items_str else []) if s.strip()]

    ship_doc = {
        "id": str(uuid.uuid4()),
        "tracking_id": tracking_id,
        "courier_id": courier["id"],
        "courier_name": courier.get("name", ""),
        "customer_name": get("customer_name"),
        "customer_phone": get("customer_phone"),
        "address_line1": get("address_line1"),
        "address_line2": get("address_line2"),
        "city": get("city"),
        "state": get("state"),
        "pincode": get("pincode"),
        "items": items_list,
        "item_description": items_str,
        "amount": float(get("amount", 0) or 0),
        "cod_amount": float(get("amount", 0) or 0) if get("payment_mode") == "COD" else 0,
        "weight": get("weight"),
        "payment_mode": get("payment_mode", "COD"),
        "order_id": get("order_id_hint"),
        "notes": get("notes"),
        "status": "Pending",
        "created_at": utcnow_iso(),
        "updated_at": utcnow_iso(),
        # Carry the Master Sheet row number so a future delete can soft-delete
        # the exact tombstone row (preserves audit trail across app users).
        "sheet_row_num": order.get("sheet_row_num"),
        "user_id": current_user["id"],
    }
    await db.shipments.insert_one(ship_doc)

    # Mark order as shipped + link
    await db.pending_orders.update_one(
        {"id": order_id, "user_id": current_user["id"]},
        {"$set": {
            "status": "shipped",
            "processed_at": utcnow_iso(),
            "shipment_id": ship_doc["id"],
            "tracking_id": tracking_id,
        }},
    )
    # Charge wallet + bump plan counter.
    addr_text = " ".join(filter(None, [
        ship_doc.get("address_line1",""), ship_doc.get("address_line2",""),
        ship_doc.get("city",""), ship_doc.get("state",""), str(ship_doc.get("pincode","")),
    ]))
    _s2 = await db.settings.find_one(
        {"user_id": current_user["id"]},
        {"_id": 0, "ai_cost_simple": 1, "ai_cost_medium": 1, "ai_cost_complex": 1},
    ) or {}
    ai_costs2 = {
        "simple":  float(_s2.get("ai_cost_simple", 0.5)),
        "medium":  float(_s2.get("ai_cost_medium", 1.0)),
        "complex": float(_s2.get("ai_cost_complex", 2.0)),
    }
    # Phase-4b LLM-backed complexity detection (cached & heuristic-safe).
    breakdown, _reason = await wallet_classify_and_cost(current_user, addr_text, plan_has_room, ai_costs=ai_costs2)
    # Wallet may not have been checked above (old-path) — make sure they can pay.
    bal = await wallet_balance(db, current_user["id"])
    if breakdown.total > bal + 1e-6:
        # Shouldn't happen (we already gated), but be safe.
        logger.warning(f"Ship path: wallet underfunded for user {current_user['id']}")
    if plan_has_room:
        await bump_label_usage(db, current_user)
    await wallet_charge(db, current_user, ship_doc["id"], breakdown)

    # ---- Two-Way Status Sync: bump the Master Sheet row from
    # "Pending" to "Dispatched" and stamp the tracking ID into Notice.
    # Best-effort: sheet failures are logged but never block the flow.
    sheet_row = order.get("sheet_row_num")
    if sheet_row and sheet_update_row_status is not None:
        try:
            sheet_update_row_status(
                int(sheet_row),
                "Dispatched",
                extra_notice=f"Tracking: {tracking_id} · {courier.get('name','')}",
            )
            logger.info(
                f"Sheet status sync OK: row={sheet_row} Pending → Dispatched ({tracking_id})"
            )
        except Exception:
            logger.exception("Sheet status sync failed on ship (non-fatal)")

    ship_doc.pop("_id", None)
    return ship_doc


@api_router.get("/orders/pending-count")
async def pending_orders_count(current_user: Dict[str, Any] = Depends(get_current_user)):
    n = await db.pending_orders.count_documents(
        {"user_id": current_user["id"], "status": "pending"}
    )
    return {"count": n}


# ---------------------- Phase-3a Plans & Usage ----------------------


@api_router.get("/plans")
async def list_plans(current_user: Dict[str, Any] = Depends(get_current_user)):
    """Return the 4-tier plan catalogue plus a hint about which plan the
    caller is currently on (so the Plans screen can badge it)."""
    return {
        "plans": public_plan_list(),
        "current": current_user.get("plan") or "free_trial",
    }


@api_router.get("/me/usage")
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
}


def _coerce_notif_prefs(raw: Optional[Dict[str, Any]]) -> Dict[str, bool]:
    raw = raw or {}
    out: Dict[str, bool] = {}
    for k, default in DEFAULT_NOTIFICATION_PREFS.items():
        out[k] = bool(raw.get(k, default))
    return out


@api_router.get("/me/notification-prefs")
async def get_notification_prefs(
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Per-user notification toggles. Surfaced from Settings → Notifications."""
    fresh = await db.users.find_one({"id": current_user["id"]}, {"_id": 0}) or {}
    return _coerce_notif_prefs(fresh.get("notification_prefs"))


class NotificationPrefsRequest(BaseModel):
    trial_ending:    Optional[bool] = None
    plan_expiring:   Optional[bool] = None
    low_credits:     Optional[bool] = None
    payment_success: Optional[bool] = None
    daily_summary:   Optional[bool] = None
    channel_push:    Optional[bool] = None
    channel_email:   Optional[bool] = None


@api_router.put("/me/notification-prefs")
async def put_notification_prefs(
    payload: NotificationPrefsRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    fresh = await db.users.find_one({"id": current_user["id"]}, {"_id": 0}) or {}
    current = _coerce_notif_prefs(fresh.get("notification_prefs"))
    incoming = payload.model_dump(exclude_none=True)
    merged = {**current, **{k: bool(v) for k, v in incoming.items()}}
    await db.users.update_one(
        {"id": current_user["id"]},
        {"$set": {"notification_prefs": merged}},
    )
    return merged


@api_router.post("/me/cancel-subscription")
async def cancel_subscription(
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """User-initiated cancellation. Currently we use Razorpay Orders
    (one-time charges, NOT recurring subscriptions) so there is nothing
    to cancel mid-cycle — the user simply doesn't pay next time. The
    paid plan stays active until plan_expires_at, after which
    ensure_can_create_label blocks label creation.

    This endpoint flips an `auto_renew=false` flag (forward-compatible
    with future Razorpay Subscriptions integration) and stamps a
    cancellation timestamp the Settings UI shows.
    """
    fresh = await db.users.find_one({"id": current_user["id"]}, {"_id": 0}) or {}
    plan = fresh.get("plan") or "free_trial"
    if plan == "free_trial":
        raise HTTPException(
            status_code=400,
            detail="You're on the free trial — there's nothing to cancel.",
        )
    await db.users.update_one(
        {"id": current_user["id"]},
        {"$set": {
            "auto_renew": False,
            "cancelled_at": datetime.utcnow().isoformat() + "+00:00",
        }},
    )
    return {
        "ok": True,
        "plan": plan,
        "plan_expires_at": fresh.get("plan_expires_at"),
        "message": (
            "Auto-renewal cancelled. Your plan stays active until "
            f"{fresh.get('plan_expires_at') or 'expiry'}, after which "
            "you'll be moved to the free trial."
        ),
    }


class UpgradePlanRequest(BaseModel):
    plan: str  # one of free_trial | silver | gold | platinum


@api_router.post("/plans/upgrade")
async def upgrade_plan(
    payload: UpgradePlanRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """MOCK upgrade flow for Phase-3a. Razorpay payment will be added in
    Phase-4. For now this simply switches the user's plan record and
    restarts the relevant validity window (trial_expires_at for
    free_trial, open-ended for paid tiers). No money changes hands.

    SECURITY: Downgrading to free_trial after it's been consumed does
    NOT reset the lifetime trial counter — the user will still hit the
    10-label cap immediately. This prevents "reset-abuse".
    """
    key = (payload.plan or "").strip().lower()
    if key not in PLAN_TABLE:
        raise HTTPException(status_code=400, detail=f"Unknown plan '{payload.plan}'")
    set_payload = plan_start_payload(key)
    # Stamp a flag so the UI can display "Upgrade mocked — Razorpay in Phase 4".
    set_payload["plan_mocked"] = True
    await db.users.update_one({"id": current_user["id"]}, {"$set": set_payload})
    fresh = await db.users.find_one({"id": current_user["id"]}, {"_id": 0})
    return {
        "ok": True,
        "mocked": True,
        "plan": key,
        "plan_started_at": set_payload["plan_started_at"],
        "plan_expires_at": set_payload.get("plan_expires_at"),
        "user": user_public(fresh or {}),
    }


# ---------------------- Phase-4a Credit Wallet ----------------------


@api_router.get("/wallet")
async def get_wallet(current_user: Dict[str, Any] = Depends(get_current_user)):
    w = await wallet_ensure(db, current_user["id"])
    return {
        "total_credits": round(float(w.get("total_credits", 0.0)), 2),
        "used_credits": round(float(w.get("used_credits", 0.0)), 2),
        "remaining_credits": round(float(w.get("remaining_credits", 0.0)), 2),
        "updated_at": w.get("updated_at"),
    }


@api_router.get("/wallet/history")
async def get_wallet_history(
    limit: int = 100,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    entries = await wallet_list_history(db, current_user["id"], limit=max(1, min(500, limit)))
    return {"entries": entries, "count": len(entries)}


class PurchaseCreditsRequest(BaseModel):
    amount_inr: float  # Validated against admin-configured packages below.


@api_router.post("/wallet/purchase")
async def purchase_credits(
    payload: PurchaseCreditsRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """MOCK credit top-up for Phase-4a / 5b.

    Razorpay wiring arrives in Phase-4c. The admin configures available
    `credit_packages` in /admin/global-config; this endpoint matches the
    request amount to a package and credits the user with the BONUSED
    amount (not just 1:1). If no exact-match package is found we fall back
    to a 1:1 conversion so custom amounts still work.
    """
    inr = float(payload.amount_inr or 0)
    if inr <= 0:
        raise HTTPException(status_code=400, detail="amount_inr must be > 0")
    if inr < 10 or inr > 100000:
        raise HTTPException(status_code=400, detail="Top-up must be between ₹10 and ₹1,00,000")
    cfg = await _get_admin_config()
    pkg = next((p for p in cfg["credit_packages"] if int(p["amount_inr"]) == int(inr)), None)
    if pkg:
        credits = float(pkg["credits"])
        bonus_str = f" (incl. {pkg['bonus']} bonus)" if pkg.get("bonus") else ""
        desc = f"Top-up ₹{int(inr)} → {credits:g} credits{bonus_str} (mocked)"
    else:
        credits = round(inr, 2)  # custom amount → 1:1 fallback
        desc = f"Top-up ₹{int(inr)} → {credits:g} credits (mocked)"
    res = await wallet_add_credits(
        db, current_user["id"], credits,
        ctype="purchase",
        description=desc,
    )
    wallet = res["wallet"]
    return {
        "ok": True,
        "mocked": True,
        "amount_inr": inr,
        "credits_added": credits,
        "bonus": (pkg or {}).get("bonus", 0),
        "balance": round(float(wallet.get("remaining_credits", 0.0)), 2),
        "history_id": res["history"]["id"],
    }


@api_router.get("/wallet/quote")
async def wallet_quote(
    address: str = "",
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Dry-run: show the user what ONE more label will cost right now.

    Phase-4b: complexity is classified by the LLM (cached + heuristic
    fallback); the reason string is surfaced so the UI can explain
    *why* an address was tagged simple/medium/complex.
    """
    room = await plan_room_status(db, current_user)
    plan_has_room = bool(room["plan_has_room"]) and not room["trial_expired"] and not room["daily_blocked"]
    _s3 = await db.settings.find_one(
        {"user_id": current_user["id"]},
        {"_id": 0, "ai_cost_simple": 1, "ai_cost_medium": 1, "ai_cost_complex": 1},
    ) or {}
    ai_costs3 = {
        "simple":  float(_s3.get("ai_cost_simple", 0.5)),
        "medium":  float(_s3.get("ai_cost_medium", 1.0)),
        "complex": float(_s3.get("ai_cost_complex", 2.0)),
    }
    bd, reason = await wallet_classify_and_cost(current_user, address, plan_has_room, ai_costs=ai_costs3)
    bal = await wallet_balance(db, current_user["id"])
    return {
        "plan": room["plan"],
        "plan_has_room": plan_has_room,
        "trial_expired": room["trial_expired"],
        "daily_blocked": room["daily_blocked"],
        "ai_complexity": bd.ai_complexity,
        "ai_reason": reason,
        "ai_credits": bd.ai_credits,
        "ai_applies": bd.ai_applies,
        "shipment_credits": bd.shipment_credits,
        "total": bd.total,
        "wallet_balance": round(bal, 2),
        "can_afford": (bd.total <= bal + 1e-6),
        "ai_rates": ai_costs3,
    }


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


async def _get_plan_features_doc() -> Dict[str, List[str]]:
    """Fetch (or seed) the plan->features mapping. Adds new registry keys
    to Platinum automatically so admin doesn't have to re-tick on every
    deploy.
    """
    doc = await db.plan_features.find_one({"_id": "default"})
    if not doc:
        seeded = {p: list(v) for p, v in DEFAULT_PLAN_FEATURES.items()}
        await db.plan_features.insert_one({"_id": "default", "plans": seeded})
        return seeded
    plans = doc.get("plans", {}) or {}
    # Auto-grant any newly-added registry keys to Platinum so power users
    # always have the latest. Other plans stay opt-in (default OFF).
    plat = set(plans.get("platinum", []))
    plat.update(ALL_KEYS)
    if plat != set(plans.get("platinum", [])):
        plans["platinum"] = list(plat)
        await db.plan_features.update_one(
            {"_id": "default"}, {"$set": {"plans": plans}}
        )
    # Also ensure every plan key exists (free_trial, silver, gold, platinum)
    for k in DEFAULT_PLAN_FEATURES.keys():
        plans.setdefault(k, list(DEFAULT_PLAN_FEATURES[k]))
    return plans


def _require_admin(current_user: Dict[str, Any]) -> None:
    if not current_user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")


@api_router.get("/admin/plan-features")
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


@api_router.put("/admin/plan-features")
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


@api_router.get("/me/feature-flags")
async def me_feature_flags(
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """Returns the list of feature keys the CURRENT user can use, based on
    their plan. Admin users get every key automatically so they never lock
    themselves out of the panel they administer."""
    plan = (current_user.get("plan") or "free_trial").lower()
    if current_user.get("is_admin"):
        return {"plan": plan, "features": ALL_KEYS, "is_admin": True}
    plans = await _get_plan_features_doc()
    allowed = plans.get(plan, plans.get("free_trial", []))
    return {"plan": plan, "features": list(allowed), "is_admin": False}


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


class RazorpayCreateOrderRequest(BaseModel):
    """Request to create a Razorpay order for wallet top-up.

    `amount_inr` is the rupee value the user wants to top up — we
    multiply by 100 internally to send paise to Razorpay.
    """
    amount_inr: int


class RazorpayVerifyRequest(BaseModel):
    """Three values returned by Razorpay Checkout on success that we
    need to verify against our server-side secret before crediting the
    wallet."""
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


@api_router.post("/wallet/razorpay/create-order")
async def rzp_create_order(
    payload: RazorpayCreateOrderRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Step 1 of Razorpay top-up flow.

    Creates a Razorpay order on the server (so the order_id is signed
    by Razorpay, not by us). We persist the order locally so the
    /verify endpoint can match the signed payment back to the user
    even before Razorpay sends a webhook.

    Returns the data the client needs to invoke Razorpay Checkout:
        {key_id, order_id, amount (paise), currency, receipt}
    """
    if not _rzp_client:
        raise HTTPException(
            status_code=503,
            detail="Razorpay is not configured on the server.",
        )
    inr = int(payload.amount_inr or 0)
    if inr < 10 or inr > 100000:
        raise HTTPException(
            status_code=400, detail="Amount must be between ₹10 and ₹1,00,000",
        )
    # Find the package that matches (so we know how many credits to grant
    # on success) — fallback to 1:1 if user picked a custom amount.
    cfg = await _get_admin_config()
    pkg = next(
        (p for p in cfg["credit_packages"] if int(p["amount_inr"]) == inr),
        None,
    )
    credits = float(pkg["credits"]) if pkg else float(inr)
    bonus = int(pkg["bonus"]) if pkg else 0

    # Razorpay receipt has a 40-char limit.
    receipt = f"wallet-{current_user['id'][:8]}-{int(datetime.utcnow().timestamp())}"
    try:
        rzp_order = _rzp_client.order.create({
            "amount": inr * 100,  # paise
            "currency": "INR",
            "receipt": receipt,
            "payment_capture": 1,
            "notes": {
                "user_id": current_user["id"],
                "user_email": current_user.get("email", ""),
                "credits": credits,
                "bonus": bonus,
                "purpose": "wallet_topup",
            },
        })
    except Exception as e:
        logger.exception("rzp create order failed")
        raise HTTPException(status_code=502, detail=f"Razorpay error: {e}")

    # Persist locally — used during /verify and for reconciliation.
    await db.razorpay_orders.insert_one({
        "id": str(uuid.uuid4()),
        "user_id": current_user["id"],
        "razorpay_order_id": rzp_order["id"],
        "amount_inr": inr,
        "amount_paise": inr * 100,
        "credits_to_grant": credits,
        "bonus_credits": bonus,
        "status": "created",
        "created_at": datetime.utcnow().isoformat() + "+00:00",
    })

    return {
        "key_id":  _RZP_KEY_ID,
        "order_id": rzp_order["id"],
        "amount_paise": rzp_order["amount"],
        "amount_inr": inr,
        "currency": rzp_order["currency"],
        "receipt": rzp_order["receipt"],
        "credits_to_grant": credits,
        "bonus_credits": bonus,
        "user_email": current_user.get("email", ""),
        "user_name": current_user.get("name", current_user.get("email", "User")),
    }


@api_router.post("/wallet/razorpay/verify")
async def rzp_verify_and_credit(
    payload: RazorpayVerifyRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Step 2 of Razorpay top-up flow.

    Razorpay Checkout calls this endpoint with the three signed
    values. We verify the signature server-side using our key secret,
    then credit the wallet exactly once (idempotent on
    razorpay_payment_id).
    """
    if not _rzp_client:
        raise HTTPException(status_code=503, detail="Razorpay not configured")

    # 1. Look up our local order record.
    order = await db.razorpay_orders.find_one({
        "razorpay_order_id": payload.razorpay_order_id,
        "user_id": current_user["id"],
    })
    if not order:
        raise HTTPException(status_code=404, detail="Order not found for this user")
    if (order.get("purpose") or "wallet_topup") != "wallet_topup":
        raise HTTPException(
            status_code=400,
            detail="This order isn't a wallet top-up. Use /plans/razorpay/verify for plan subscriptions.",
        )

    # 2. Idempotency: if already paid + credited, just return the wallet.
    if order.get("status") == "paid":
        bal = await wallet_balance(db, current_user["id"])
        return {
            "ok": True,
            "already_credited": True,
            "credits_added": order.get("credits_to_grant", 0),
            "balance": bal,
        }

    # 3. Verify signature with Razorpay's util.
    try:
        _rzp_client.utility.verify_payment_signature({
            "razorpay_order_id":   payload.razorpay_order_id,
            "razorpay_payment_id": payload.razorpay_payment_id,
            "razorpay_signature":  payload.razorpay_signature,
        })
    except Exception as e:
        await db.razorpay_orders.update_one(
            {"_id": order["_id"]},
            {"$set": {"status": "verify_failed",
                      "error": str(e),
                      "razorpay_payment_id": payload.razorpay_payment_id}},
        )
        raise HTTPException(status_code=400, detail=f"Payment verification failed: {e}")

    # 4. Credit the wallet (single source of truth).
    credits = float(order.get("credits_to_grant", 0))
    bonus = int(order.get("bonus_credits", 0))
    bonus_str = f" (incl. {bonus} bonus)" if bonus else ""
    desc = f"Top-up ₹{int(order['amount_inr'])} → {credits:g} credits{bonus_str}"

    res = await wallet_add_credits(
        db, current_user["id"], credits,
        ctype="purchase",
        description=desc,
        order_id=payload.razorpay_payment_id,
    )

    # 5. Mark the order paid (so re-submits are idempotent).
    await db.razorpay_orders.update_one(
        {"_id": order["_id"]},
        {"$set": {
            "status": "paid",
            "razorpay_payment_id": payload.razorpay_payment_id,
            "razorpay_signature":  payload.razorpay_signature,
            "paid_at": datetime.utcnow().isoformat() + "+00:00",
        }},
    )

    wallet = res["wallet"]
    return {
        "ok": True,
        "already_credited": False,
        "amount_inr": int(order["amount_inr"]),
        "credits_added": credits,
        "bonus": bonus,
        "balance": round(float(wallet.get("remaining_credits", 0.0)), 2),
        "history_id": res["history"]["id"],
    }


@api_router.post("/wallet/razorpay/webhook")
async def rzp_webhook(request: Request):
    """Optional safety net — Razorpay calls this independently of the
    browser-side /verify call. Useful if the user closes the app
    mid-flow. Verifies signature against RAZORPAY_WEBHOOK_SECRET
    (set this in Razorpay dashboard + .env).
    """
    if not _RZP_WEBHOOK_SECRET or not _rzp_client:
        return {"ok": True, "skipped": "webhook not configured"}
    body = await request.body()
    sig = request.headers.get("x-razorpay-signature", "")
    try:
        _rzp_client.utility.verify_webhook_signature(
            body.decode("utf-8"), sig, _RZP_WEBHOOK_SECRET,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Webhook verify failed: {e}")
    try:
        evt = json.loads(body)
    except Exception:
        return {"ok": True}
    if evt.get("event") == "payment.captured":
        pay = evt.get("payload", {}).get("payment", {}).get("entity", {})
        order_id = pay.get("order_id")
        payment_id = pay.get("id")
        order = await db.razorpay_orders.find_one({"razorpay_order_id": order_id})
        if order and order.get("status") != "paid":
            purpose = order.get("purpose") or "wallet_topup"
            if purpose == "plan_subscription":
                # Plan subscription: switch plan + extend validity.
                plan_key = order.get("plan_key")
                months = int(order.get("months", 1))
                bonus_months = int(order.get("bonus_months", 0))
                user_doc = await db.users.find_one({"id": order["user_id"]}, {"_id": 0}) or {}
                same_plan = (user_doc.get("plan") == plan_key)
                new_expiry = _extend_plan_expiry(
                    user_doc.get("plan_expires_at") if same_plan else None,
                    months, bonus_months,
                )
                await db.users.update_one(
                    {"id": order["user_id"]},
                    {"$set": {
                        "plan": plan_key,
                        "plan_started_at": user_doc.get("plan_started_at") if same_plan else (datetime.utcnow().isoformat() + "+00:00"),
                        "plan_expires_at": new_expiry,
                        "plan_billing_cycle": order.get("billing_cycle"),
                        "plan_mocked": False,
                        "last_paid_payment_id": payment_id,
                        "last_paid_at": datetime.utcnow().isoformat() + "+00:00",
                    }},
                )
                await db.razorpay_orders.update_one(
                    {"_id": order["_id"]},
                    {"$set": {"status": "paid", "razorpay_payment_id": payment_id,
                              "applied_expires_at": new_expiry,
                              "paid_at": datetime.utcnow().isoformat() + "+00:00"}},
                )
            else:
                # Wallet top-up (legacy default).
                credits = float(order.get("credits_to_grant", 0))
                bonus = int(order.get("bonus_credits", 0))
                bonus_str = f" (incl. {bonus} bonus)" if bonus else ""
                desc = f"Top-up ₹{int(order['amount_inr'])} → {credits:g} credits{bonus_str} (webhook)"
                await wallet_add_credits(
                    db, order["user_id"], credits,
                    ctype="purchase", description=desc, order_id=payment_id,
                )
                await db.razorpay_orders.update_one(
                    {"_id": order["_id"]},
                    {"$set": {"status": "paid", "razorpay_payment_id": payment_id,
                              "paid_at": datetime.utcnow().isoformat() + "+00:00"}},
                )
    return {"ok": True}


# ───────────── Razorpay — Plan Subscriptions (Phase-4d) ─────────────
#
# Reuses the same Razorpay infra as wallet top-ups but stores
# `purpose: plan_subscription` in the order doc so the verify and
# webhook handlers can branch on intent.
#
# Pricing is read live from admin_config.plan_pricing so the admin
# can change prices any time (the source of truth is the same as the
# /plans-pricing payload the frontend reads).

class PlanRazorpayCreateOrderRequest(BaseModel):
    """Body for /api/plans/razorpay/create-order."""
    plan_key: str           # silver | gold | platinum
    billing_cycle: str      # monthly | yearly


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


@api_router.post("/plans/razorpay/create-order")
async def rzp_create_plan_order(
    payload: PlanRazorpayCreateOrderRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Step 1 of Plan Subscription Razorpay flow.

    Creates a Razorpay order keyed to a plan/cycle and persists the
    intent so the verify endpoint can extend the user's plan validity
    correctly on success.
    """
    if not _rzp_client:
        raise HTTPException(
            status_code=503, detail="Razorpay is not configured on the server.",
        )
    if payload.plan_key not in PLAN_TABLE:
        raise HTTPException(status_code=400, detail=f"Unknown plan '{payload.plan_key}'")

    cfg = await _get_admin_config()
    meta = _plan_billing_meta(cfg["plan_pricing"], payload.plan_key, payload.billing_cycle)
    inr = int(meta["price_inr"])

    # Razorpay receipt: 40-char limit. Encode plan + cycle for ops debugging.
    cycle_short = "y" if payload.billing_cycle == "yearly" else "m"
    receipt = f"plan-{payload.plan_key[:6]}-{cycle_short}-{current_user['id'][:6]}-{int(datetime.utcnow().timestamp())}"
    receipt = receipt[:40]

    try:
        rzp_order = _rzp_client.order.create({
            "amount": inr * 100,  # paise
            "currency": "INR",
            "receipt": receipt,
            "payment_capture": 1,
            "notes": {
                "user_id": current_user["id"],
                "user_email": current_user.get("email", ""),
                "purpose": "plan_subscription",
                "plan_key": payload.plan_key,
                "billing_cycle": payload.billing_cycle,
                "months": meta["months"],
                "bonus_months": meta["bonus_months"],
            },
        })
    except Exception as e:
        logger.exception("rzp create plan order failed")
        raise HTTPException(status_code=502, detail=f"Razorpay error: {e}")

    await db.razorpay_orders.insert_one({
        "id": str(uuid.uuid4()),
        "user_id": current_user["id"],
        "razorpay_order_id": rzp_order["id"],
        "amount_inr": inr,
        "amount_paise": inr * 100,
        "purpose": "plan_subscription",
        "plan_key": payload.plan_key,
        "billing_cycle": payload.billing_cycle,
        "months": meta["months"],
        "bonus_months": meta["bonus_months"],
        "status": "created",
        "created_at": datetime.utcnow().isoformat() + "+00:00",
    })

    plan_meta = PLAN_TABLE.get(payload.plan_key)
    return {
        "key_id":   _RZP_KEY_ID,
        "order_id": rzp_order["id"],
        "amount_paise": rzp_order["amount"],
        "amount_inr":   inr,
        "currency":     rzp_order["currency"],
        "receipt":      rzp_order["receipt"],
        "purpose":      "plan_subscription",
        "plan_key":     payload.plan_key,
        "plan_name":    getattr(plan_meta, "name", payload.plan_key.title()) if plan_meta else payload.plan_key.title(),
        "billing_cycle": payload.billing_cycle,
        "months":       meta["months"],
        "bonus_months": meta["bonus_months"],
        "user_email":   current_user.get("email", ""),
        "user_name":    current_user.get("name", current_user.get("email", "User")),
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


@api_router.post("/plans/razorpay/verify")
async def rzp_verify_plan_subscription(
    payload: RazorpayVerifyRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Step 2 of Plan Subscription Razorpay flow.

    Verifies the payment signature, switches the user's plan, and
    extends plan_expires_at by (months + bonus_months) — carrying over
    any unused validity if the user is already on a paid plan.
    """
    if not _rzp_client:
        raise HTTPException(status_code=503, detail="Razorpay not configured")

    order = await db.razorpay_orders.find_one({
        "razorpay_order_id": payload.razorpay_order_id,
        "user_id": current_user["id"],
    })
    if not order:
        raise HTTPException(status_code=404, detail="Order not found for this user")
    if order.get("purpose") != "plan_subscription":
        raise HTTPException(
            status_code=400,
            detail="This order isn't a plan subscription. Use /wallet/razorpay/verify for top-ups.",
        )

    # Idempotency
    if order.get("status") == "paid":
        fresh = await db.users.find_one({"id": current_user["id"]}, {"_id": 0})
        return {
            "ok": True,
            "already_credited": True,
            "plan": order.get("plan_key"),
            "billing_cycle": order.get("billing_cycle"),
            "plan_expires_at": (fresh or {}).get("plan_expires_at"),
        }

    # Verify signature
    try:
        _rzp_client.utility.verify_payment_signature({
            "razorpay_order_id":   payload.razorpay_order_id,
            "razorpay_payment_id": payload.razorpay_payment_id,
            "razorpay_signature":  payload.razorpay_signature,
        })
    except Exception as e:
        await db.razorpay_orders.update_one(
            {"_id": order["_id"]},
            {"$set": {
                "status": "verify_failed",
                "error": str(e),
                "razorpay_payment_id": payload.razorpay_payment_id,
            }},
        )
        raise HTTPException(status_code=400, detail=f"Payment verification failed: {e}")

    # Switch the plan + extend validity
    plan_key = order.get("plan_key")
    months = int(order.get("months", 1))
    bonus_months = int(order.get("bonus_months", 0))
    fresh = await db.users.find_one({"id": current_user["id"]}, {"_id": 0}) or {}
    same_plan = (fresh.get("plan") == plan_key)
    new_expiry = _extend_plan_expiry(
        fresh.get("plan_expires_at") if same_plan else None,
        months, bonus_months,
    )

    set_payload = {
        "plan": plan_key,
        "plan_started_at": fresh.get("plan_started_at") if same_plan else (datetime.utcnow().isoformat() + "+00:00"),
        "plan_expires_at": new_expiry,
        "plan_billing_cycle": order.get("billing_cycle"),
        "plan_mocked": False,
        "last_paid_payment_id": payload.razorpay_payment_id,
        "last_paid_at": datetime.utcnow().isoformat() + "+00:00",
    }
    await db.users.update_one({"id": current_user["id"]}, {"$set": set_payload})

    await db.razorpay_orders.update_one(
        {"_id": order["_id"]},
        {"$set": {
            "status": "paid",
            "razorpay_payment_id": payload.razorpay_payment_id,
            "razorpay_signature":  payload.razorpay_signature,
            "paid_at": datetime.utcnow().isoformat() + "+00:00",
            "applied_expires_at": new_expiry,
        }},
    )

    return {
        "ok": True,
        "already_credited": False,
        "plan": plan_key,
        "billing_cycle": order.get("billing_cycle"),
        "amount_inr": int(order.get("amount_inr", 0)),
        "months": months,
        "bonus_months": bonus_months,
        "plan_expires_at": new_expiry,
    }


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


@api_router.put("/admin/global-config")
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


@api_router.get("/plans-pricing")
async def get_plans_pricing_public(
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Public read of plan_pricing + countdown for the Plans screen.
    Available to every logged-in user (not just admins)."""
    cfg = await _get_admin_config()
    return {
        "plan_pricing": cfg["plan_pricing"],
        "countdown": cfg["countdown"],
    }


@api_router.get("/credit-packages")
async def get_credit_packages_public(
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Read-only list every logged-in user can fetch for the Wallet Top-up screen."""
    cfg = await _get_admin_config()
    return {"packages": cfg["credit_packages"]}


@api_router.get("/me/ai-rates")
async def me_ai_rates(
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Read-only: rates the user will be charged for Smart Paste calls.
    These are admin-controlled now; per-user overrides are no longer used."""
    cfg = await _get_admin_config()
    return cfg["global_ai_rates"]


# ---------------------- App setup ----------------------

app.include_router(api_router)
app.include_router(auth_router)

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
_AUTH_EXEMPT_PREFIXES = ("/api/auth/",)
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
    logger.info("Courier Label Manager API started; defaults seeded.")


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
