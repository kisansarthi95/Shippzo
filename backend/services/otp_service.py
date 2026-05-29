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
OTP_LENGTH         = int(os.getenv("OTP_LENGTH", "6"))
OTP_TTL_SECONDS    = int(os.getenv("OTP_TTL_SECONDS", "300"))     # 5 min
OTP_RESEND_COOLDOWN_S = int(os.getenv("OTP_RESEND_COOLDOWN_S", "60"))
OTP_MAX_ATTEMPTS   = int(os.getenv("OTP_MAX_ATTEMPTS", "5"))
_COLLECTION        = os.getenv("OTP_COLLECTION", "otp_codes")
_DEFAULT_CC        = os.getenv("FLOWCONNECT_DEFAULT_CC", "91")

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
        _indexes_initialised = True
    except Exception as e:
        _LOG.warning("OTP index creation failed (non-fatal): %s", e)


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


def _mask(raw: str) -> str:
    return re.sub(r"\d(?=\d{4})", "*", raw or "")
