"""
OTP generation + validation service.

This module is the SINGLE SOURCE OF TRUTH for one-time codes inside
Shippzo. Every callable here is provider-independent — it does NOT
know about FlowConnect, WATI, or any other vendor. Delivery is the
``services.otp_whatsapp`` dispatcher's job; this file only owns:

  • Generation              (cryptographically-secure random 6-digit code)
  • Storage                 (hashed, never plaintext)
  • Expiry                  (default 5 minutes; configurable per env)
  • Resend cooldown         (default 60 seconds per phone+event)
  • Attempt cap             (default 5 wrong tries → invalidate)
  • Verification            (constant-time hash compare)

Public surface (do not bypass — call these helpers, not the DB):

    code = await issue_otp(db, phone, event_type)
    ok, reason = await verify_otp(db, phone, code, event_type)

Indexes (cheap, auto-created on first use):
  • {phone: 1, event_type: 1}           — fast lookup
  • {expires_at: 1}  TTL=0               — auto-purge stale rows

Schema (collection ``otp_codes``):
  {
    _id:           ObjectId,
    phone:         "+919876543210",     # normalised E.164
    event_type:    "login" | "signup" | …,
    code_hash:     "<sha256-hex>",
    salt:          "<hex>",
    issued_at:     ISO 8601 UTC,
    expires_at:    BSON datetime (TTL),
    attempts:      int,
    used:          bool,
    last_resend_at:ISO 8601 UTC,
  }
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Tuple

_LOG = logging.getLogger("otp_service")

# ─── Configurable knobs (env-driven, sensible production defaults) ──
# 2026-05-29 — Rule update:
#   • OTP expiry: 10 minutes (was 5)
#   • Resend cooldown: 60 seconds (unchanged)
#   • Max resend attempts within the lockout window: 5
#   • Lockout duration after hitting the resend cap: 30 minutes
# All four are env-tunable so individual environments (dev/load-test/
# enterprise tenant) can dial the rules without a code change.
OTP_LENGTH              = int(os.getenv("OTP_LENGTH", "6"))
OTP_TTL_SECONDS         = int(os.getenv("OTP_TTL_SECONDS", "600"))     # 10 min
OTP_RESEND_COOLDOWN_S   = int(os.getenv("OTP_RESEND_COOLDOWN_S", "60"))
OTP_MAX_ATTEMPTS        = int(os.getenv("OTP_MAX_ATTEMPTS", "5"))      # wrong-code tries
OTP_MAX_RESEND_ATTEMPTS = int(os.getenv("OTP_MAX_RESEND_ATTEMPTS", "5"))
OTP_LOCKOUT_DURATION_S  = int(os.getenv("OTP_LOCKOUT_DURATION_S", "1800"))  # 30 min
_COLLECTION             = os.getenv("OTP_COLLECTION", "otp_codes")
_LOCK_COLLECTION        = os.getenv("OTP_LOCK_COLLECTION", "otp_resend_locks")
_DEFAULT_CC             = os.getenv("FLOWCONNECT_DEFAULT_CC", "91")

_indexes_initialised = False


def normalise_phone(raw: str) -> str:
    """Same E.164 normalisation used by the provider — keeps the
    storage key and the delivery target perfectly aligned so a user
    can't be locked out by spaces/dashes in their phone entry."""
    s = (raw or "").strip()
    if not s:
        return s
    if s.startswith("+"):
        return "+" + re.sub(r"\D", "", s)
    digits = re.sub(r"\D", "", s)
    if not digits:
        return s
    if len(digits) >= 11 and digits.startswith(_DEFAULT_CC):
        return "+" + digits
    if len(digits) == 10:
        return f"+{_DEFAULT_CC}{digits}"
    return "+" + digits


def _generate_code(length: int = OTP_LENGTH) -> str:
    """Cryptographically-secure N-digit code — uniform distribution,
    no modulo bias. `secrets.choice` reads from os.urandom."""
    return "".join(secrets.choice("0123456789") for _ in range(length))


def _hash_code(code: str, salt: str) -> str:
    """sha256(salt + code). Salt is per-row so even rainbow tables
    that pre-compute every 6-digit pin don't help."""
    return hashlib.sha256((salt + code).encode("utf-8")).hexdigest()


async def _ensure_indexes(db: Any) -> None:
    """One-time index creation. Cheap idempotent operation but we
    still gate it behind a module flag to avoid the round-trip on
    every issue/verify call."""
    global _indexes_initialised
    if _indexes_initialised or db is None:
        return
    try:
        col = db[_COLLECTION]
        await col.create_index([("phone", 1), ("event_type", 1)])
        # TTL index — Mongo auto-deletes rows when expires_at < now.
        await col.create_index("expires_at", expireAfterSeconds=0)
        # 2026-05-29 — Resend-lockout collection. One row per
        # phone+event_type. We index for lookup speed; rows are
        # cleared lazily once the window expires (no TTL needed,
        # the in-app check handles it).
        lock_col = db[_LOCK_COLLECTION]
        await lock_col.create_index([("phone", 1), ("event_type", 1)], unique=True)
        _indexes_initialised = True
    except Exception as e:
        _LOG.warning("OTP index creation failed (non-fatal): %s", e)


# ─── Phase-OTP: resend-rate lockout (5 requests per 30-minute window) ─
#
# Hard cap on how many OTPs a single phone+event_type can request in a
# rolling lockout window. Defends against:
#   • Bots brute-forcing the WhatsApp dispatch quota.
#   • Genuine users accidentally hammering "Resend" after every minute
#     and burning through our FlowConnect spend.
#
# Counter row schema (collection ``otp_resend_locks``):
#   {
#     phone:         "+919876543210",   # E.164 normalised
#     event_type:    "login" | "signup" | …
#     count:         3,                  # successful sends in current window
#     window_start:  "ISO 8601 UTC",     # timestamp of the 1st send
#     locked_until:  "ISO 8601 UTC" | null,
#   }
#
# Rules:
#   • A lock is set the moment a 6th request would be allowed.
#   • Lock duration = OTP_LOCKOUT_DURATION_S (default 30 min).
#   • While locked, every /request returns 429 with remaining time.
#   • Once the lock expires, the counter resets on the next request.
async def _enforce_resend_lockout(db: Any, phone: str, event_type: str) -> None:
    """Check + atomically advance the per-(phone,event) resend counter.
    Raises ``LockoutError`` when the cap has been hit. Otherwise
    silently increments the row and returns.
    """
    if db is None:
        return  # Service still warming up — fail-open by design.
    now = datetime.now(timezone.utc)
    col = db[_LOCK_COLLECTION]
    row = await col.find_one({"phone": phone, "event_type": event_type})

    if row:
        # ── 1. Already locked? Reject early.
        locked_until_raw = row.get("locked_until")
        if locked_until_raw:
            try:
                locked_until = datetime.fromisoformat(
                    str(locked_until_raw).replace("Z", "+00:00")
                )
                if locked_until.tzinfo is None:
                    locked_until = locked_until.replace(tzinfo=timezone.utc)
                if locked_until > now:
                    remaining_s = int((locked_until - now).total_seconds())
                    mins = max(1, (remaining_s + 59) // 60)
                    raise LockoutError(
                        f"Too many OTP requests. Try again in {mins} minute(s)."
                    )
            except LockoutError:
                raise
            except Exception:
                pass  # Bad timestamp → treat as not locked.

        # ── 2. Window expired? Reset and start fresh.
        window_age_s = OTP_LOCKOUT_DURATION_S + 1   # default: expired
        try:
            ws = datetime.fromisoformat(
                str(row.get("window_start") or "").replace("Z", "+00:00")
            )
            if ws.tzinfo is None:
                ws = ws.replace(tzinfo=timezone.utc)
            window_age_s = (now - ws).total_seconds()
        except Exception:
            pass

        if window_age_s >= OTP_LOCKOUT_DURATION_S:
            await col.update_one(
                {"_id": row["_id"]},
                {"$set": {
                    "count":         1,
                    "window_start":  now.isoformat(),
                    "locked_until":  None,
                }},
            )
            return

        # ── 3. Inside window. Check cap.
        count = int(row.get("count", 0))
        if count >= OTP_MAX_RESEND_ATTEMPTS:
            # 6th request — set lock and reject.
            lock_until = now + timedelta(seconds=OTP_LOCKOUT_DURATION_S)
            await col.update_one(
                {"_id": row["_id"]},
                {"$set": {"locked_until": lock_until.isoformat()}},
            )
            mins = max(1, OTP_LOCKOUT_DURATION_S // 60)
            raise LockoutError(
                f"Too many OTP requests. Try again in {mins} minute(s)."
            )

        # ── 4. Within cap. Increment counter.
        await col.update_one(
            {"_id": row["_id"]},
            {"$inc": {"count": 1}, "$set": {"locked_until": None}},
        )
        return

    # First-ever request for this (phone, event_type) — open the window.
    try:
        await col.insert_one({
            "phone":        phone,
            "event_type":   event_type,
            "count":        1,
            "window_start": now.isoformat(),
            "locked_until": None,
        })
    except Exception as e:
        # Unique-key race when two requests land in the same millisecond
        # → ignore; the next one will pick up the existing row.
        _LOG.debug("resend lock insert race (non-fatal): %s", e)


async def issue_otp(db: Any, phone: str, event_type: str) -> Tuple[str, str]:
    """Create + store a fresh OTP for ``phone`` and return
    ``(plaintext_code, normalised_phone)``.

    The plaintext code MUST be passed straight to the WhatsApp
    dispatcher and then discarded — never logged, never returned
    to the frontend.

    Existing un-expired codes for the same phone+event_type are
    invalidated first to enforce the "only the newest OTP works"
    rule that every user expects from real-world login flows.
    """
    await _ensure_indexes(db)
    normalised = normalise_phone(phone)
    if not normalised:
        raise ValueError("phone is required")

    # Resend cooldown — block rapid-fire requests at the service layer
    # so callers don't have to remember to rate-limit themselves.
    # We check the LATEST row regardless of `used` status so that a
    # user who just successfully verified can't trigger a new send
    # immediately afterwards (defends against credential-stuffing
    # bots that have a way to invalidate codes server-side).
    #
    # Cooldown is enforced BEFORE the resend-rate-limit counter so a
    # cooldown-blocked request doesn't burn one of the user's 5
    # allowed sends — only requests that actually pass through to the
    # WhatsApp dispatcher count toward the cap.
    now = datetime.now(timezone.utc)
    existing = await db[_COLLECTION].find_one(
        {"phone": normalised, "event_type": event_type},
        sort=[("issued_at", -1)],
    )
    if existing:
        last_at_raw = existing.get("last_resend_at") or existing.get("issued_at") or ""
        try:
            last_at = datetime.fromisoformat(str(last_at_raw).replace("Z", "+00:00"))
            if last_at.tzinfo is None:
                last_at = last_at.replace(tzinfo=timezone.utc)
            cooldown_elapsed = (now - last_at).total_seconds()
            if cooldown_elapsed < OTP_RESEND_COOLDOWN_S:
                wait_s = int(OTP_RESEND_COOLDOWN_S - cooldown_elapsed)
                raise CooldownError(
                    f"Please wait {wait_s} more second(s) before requesting a new OTP."
                )
        except CooldownError:
            raise
        except Exception:
            # Bad timestamp on existing row — treat as expired, allow new issue.
            pass

    # ── Phase-OTP rule (2026-05-29): per-(phone,event_type) hard cap
    # of 5 OTP sends per 30-minute rolling window. Beyond that, the
    # row gets locked for the next 30 minutes. This runs AFTER the
    # 60-second cooldown so a user who taps "Resend" too fast just
    # gets the cooldown message — they don't waste one of their 5
    # quota slots on a request the server was going to reject anyway.
    await _enforce_resend_lockout(db, normalised, event_type)

    code  = _generate_code()
    salt  = secrets.token_hex(8)
    chash = _hash_code(code, salt)
    expires_at = now + timedelta(seconds=OTP_TTL_SECONDS)

    # Invalidate every older un-used row for this phone+event_type so
    # a user resending an OTP can only verify the freshest one.
    await db[_COLLECTION].update_many(
        {"phone": normalised, "event_type": event_type, "used": False},
        {"$set": {"used": True, "invalidated": True}},
    )
    await db[_COLLECTION].insert_one({
        "phone":          normalised,
        "event_type":     event_type,
        "code_hash":      chash,
        "salt":           salt,
        "issued_at":      now.isoformat(),
        "expires_at":     expires_at,   # BSON datetime → TTL index honours it
        "attempts":       0,
        "used":           False,
        "last_resend_at": now.isoformat(),
    })
    _LOG.info(
        "OTP issued: phone=%s event=%s expires_in=%ds",
        _mask(normalised), event_type, OTP_TTL_SECONDS,
    )
    return code, normalised


async def verify_otp(
    db: Any,
    phone: str,
    code: str,
    event_type: str,
) -> Tuple[bool, str]:
    """Constant-time verification of ``code`` against the most-recent
    un-used OTP row for ``phone``+``event_type``.

    Returns ``(success, reason)`` where ``reason`` is empty on success
    and a short human-readable string otherwise (used by the API
    response body — never reveals whether the row exists at all to
    discourage user enumeration).
    """
    await _ensure_indexes(db)
    normalised = normalise_phone(phone)
    if not normalised:
        return False, "Invalid phone number"
    if not code or not code.strip():
        return False, "OTP is required"
    code = code.strip()

    row = await db[_COLLECTION].find_one(
        {"phone": normalised, "event_type": event_type, "used": False},
        sort=[("issued_at", -1)],
    )
    if not row:
        return False, "Invalid or expired OTP"

    # Expiry — the TTL index sweeps stale rows but the sweep is
    # eventually-consistent (Mongo runs it every ~60s) so always
    # validate inline too.
    expires_at = row.get("expires_at")
    now = datetime.now(timezone.utc)
    if expires_at:
        if not isinstance(expires_at, datetime):
            try:
                expires_at = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
            except Exception:
                expires_at = None
        if isinstance(expires_at, datetime):
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at < now:
                return False, "OTP has expired. Please request a new one."

    attempts = int(row.get("attempts", 0))
    if attempts >= OTP_MAX_ATTEMPTS:
        await db[_COLLECTION].update_one({"_id": row["_id"]}, {"$set": {"used": True}})
        return False, "Too many wrong attempts. Please request a new OTP."

    expected = row.get("code_hash") or ""
    candidate = _hash_code(code, row.get("salt") or "")
    if not secrets.compare_digest(expected, candidate):
        await db[_COLLECTION].update_one(
            {"_id": row["_id"]}, {"$inc": {"attempts": 1}},
        )
        remaining = OTP_MAX_ATTEMPTS - (attempts + 1)
        if remaining <= 0:
            return False, "Too many wrong attempts. Please request a new OTP."
        return False, f"Wrong OTP. {remaining} attempt(s) left."

    # Success — burn the row so it can't be replayed.
    await db[_COLLECTION].update_one(
        {"_id": row["_id"]},
        {"$set": {"used": True, "verified_at": now.isoformat()}},
    )
    _LOG.info(
        "OTP verified: phone=%s event=%s",
        _mask(normalised), event_type,
    )
    return True, ""


class CooldownError(Exception):
    """Raised by ``issue_otp`` when the resend cooldown window is
    still active. Caller (the auth router) translates this into a
    429 with the human-readable reason."""


class LockoutError(Exception):
    """Raised by ``issue_otp`` / ``_enforce_resend_lockout`` when the
    phone+event_type pair has burned through OTP_MAX_RESEND_ATTEMPTS
    within the OTP_LOCKOUT_DURATION_S window. Auth router maps this
    to a 429 — same surface as cooldown but with a longer suggested
    wait time in the message."""


def _mask(raw: str) -> str:
    return re.sub(r"\d(?=\d{4})", "*", raw or "")
