"""Phase F8.1 — contact_email + OTP-verified password reset tests.

Covers:
  1. POST /auth/contact-email — set / validate / clear the dedicated
     OTP email; value surfaces in /auth/me.
  2. POST /auth/forgot-password/request-otp — email+phone gate.
  3. POST /auth/forgot-password — OTP is REQUIRED; wrong OTP rejected;
     happy path with a directly-issued code (single-use enforced).
  4. Webhook payload carries `contact_email` (checked via the
     whatsapp_provider_log audit collection).

NOTE: resets admin's password to the SAME value so credentials in
/app/memory/test_credentials.md stay valid.
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

BASE_URL = (
    os.environ.get("EXPO_BACKEND_URL")
    or os.environ.get("EXPO_PUBLIC_BACKEND_URL")
    or "https://logistics-hub-740.preview.emergentagent.com"
).rstrip("/")
API = BASE_URL + "/api"

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASSWORD = "Admin@12345"

# Dedicated reset-test user — keeps the admin account away from the
# 3-failures/hour reset rate limit.
F81_EMAIL = "f81-reset-user@example.com"
F81_PASSWORD = "F81pass@123"
F81_PHONE = "9000011122"


def _run_db(coro_fn):
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

    async def _inner():
        from motor.motor_asyncio import AsyncIOMotorClient
        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ.get("DB_NAME", "test_database")]
        return await coro_fn(db)

    return asyncio.run(_inner())


@pytest.fixture(scope="module", autouse=True)
def f81_user():
    """Idempotently create the dedicated reset-test user and clear any
    rate-limit / OTP state so runs are independent."""

    async def _seed(db):
        from auth import hash_password, utcnow_iso
        import uuid as _uuid
        existing = await db.users.find_one({"email": F81_EMAIL})
        if not existing:
            await db.users.insert_one({
                "id": str(_uuid.uuid4()),
                "email": F81_EMAIL,
                "name": "F81 Reset User",
                "phone": F81_PHONE,
                "password_hash": hash_password(F81_PASSWORD),
                "created_at": utcnow_iso(),
                "plan": "free_trial",
            })
        else:
            await db.users.update_one(
                {"email": F81_EMAIL},
                {"$set": {
                    "phone": F81_PHONE,
                    "password_hash": hash_password(F81_PASSWORD),
                }},
            )
        # Clean slate: rate-limit log + OTP rows/locks for this identity.
        await db.pwd_reset_attempts.delete_many({"email": F81_EMAIL})
        await db.otp_codes.delete_many({"phone": {"$regex": F81_PHONE + "$"}})
        await db.otp_resend_locks.delete_many({"phone": {"$regex": F81_PHONE + "$"}})

    _run_db(_seed)
    yield


@pytest.fixture(scope="module")
def admin_session():
    r = requests.post(
        f"{API}/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=30,
    )
    assert r.status_code == 200, r.text
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {r.json()['token']}"})
    return s


@pytest.fixture(scope="module")
def admin_phone(admin_session):
    me = admin_session.get(f"{API}/auth/me", timeout=15).json()
    phone = (me.get("phone") or "").strip()
    assert phone, "admin must have a registered phone for these tests"
    return phone


class TestContactEmailEndpoint:
    def test_set_valid(self, admin_session):
        r = admin_session.post(
            f"{API}/auth/contact-email",
            json={"contact_email": "otp-inbox@example.com"},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        assert r.json().get("contact_email") == "otp-inbox@example.com"
        me = admin_session.get(f"{API}/auth/me", timeout=15).json()
        assert me.get("contact_email") == "otp-inbox@example.com"

    def test_invalid_rejected(self, admin_session):
        r = admin_session.post(
            f"{API}/auth/contact-email",
            json={"contact_email": "not-an-email"},
            timeout=15,
        )
        assert r.status_code == 400, r.text

    def test_clear(self, admin_session):
        r = admin_session.post(
            f"{API}/auth/contact-email",
            json={"contact_email": ""},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        assert r.json().get("contact_email") == ""

    def test_requires_auth(self):
        r = requests.post(
            f"{API}/auth/contact-email",
            json={"contact_email": "x@y.com"},
            timeout=15,
        )
        assert r.status_code in (401, 403), r.status_code


class TestForgotPasswordOtp:
    def test_request_otp_wrong_phone(self):
        r = requests.post(
            f"{API}/auth/forgot-password/request-otp",
            json={"email": F81_EMAIL, "phone": "1112223334"},
            timeout=20,
        )
        assert r.status_code == 400, r.text

    def test_request_otp_unknown_email(self):
        r = requests.post(
            f"{API}/auth/forgot-password/request-otp",
            json={"email": "nobody-f81@example.com", "phone": "9876543210"},
            timeout=20,
        )
        assert r.status_code == 404, r.text

    def test_reset_requires_otp(self):
        r = requests.post(
            f"{API}/auth/forgot-password",
            json={
                "email": F81_EMAIL, "phone": F81_PHONE,
                "new_password": F81_PASSWORD,
            },
            timeout=20,
        )
        assert r.status_code == 400, r.text
        assert "OTP" in (r.json().get("detail") or "")

    def test_reset_wrong_otp(self):
        r = requests.post(
            f"{API}/auth/forgot-password",
            json={
                "email": F81_EMAIL, "phone": F81_PHONE,
                "otp": "000000", "new_password": F81_PASSWORD,
            },
            timeout=20,
        )
        assert r.status_code == 400, r.text

    def test_request_otp_ok(self):
        r = requests.post(
            f"{API}/auth/forgot-password/request-otp",
            json={"email": F81_EMAIL, "phone": F81_PHONE},
            timeout=20,
        )
        assert r.status_code == 200, r.text
        assert r.json().get("ok") is True

    def test_happy_path_and_single_use(self):
        """Issue a real code directly (DB access), reset with it, then
        confirm the same code cannot be replayed."""

        async def _issue(db):
            from services.otp_service import issue_otp
            return await issue_otp(db, F81_PHONE, "password_reset")

        # Clear the cooldown left by test_request_otp_ok.
        async def _clear(db):
            await db.otp_codes.delete_many({"phone": {"$regex": F81_PHONE + "$"}})
            await db.otp_resend_locks.delete_many({"phone": {"$regex": F81_PHONE + "$"}})

        _run_db(_clear)
        code, _norm = _run_db(_issue)

        new_password = "F81pass@456"
        r = requests.post(
            f"{API}/auth/forgot-password",
            json={
                "email": F81_EMAIL, "phone": F81_PHONE,
                "otp": code, "new_password": new_password,
            },
            timeout=20,
        )
        assert r.status_code == 200, r.text
        assert r.json().get("token")

        # login works with the NEW password
        r = requests.post(
            f"{API}/auth/login",
            json={"email": F81_EMAIL, "password": new_password},
            timeout=20,
        )
        assert r.status_code == 200, r.text

        # replay must fail — OTP is single-use
        r = requests.post(
            f"{API}/auth/forgot-password",
            json={
                "email": F81_EMAIL, "phone": F81_PHONE,
                "otp": code, "new_password": new_password,
            },
            timeout=20,
        )
        assert r.status_code == 400, r.text


class TestWebhookPayloadContactEmail:
    def test_latest_password_reset_log_has_contact_email(self):
        """The request-otp calls above fired the otp_password_reset
        webhook — its logged payload must carry contact_email."""
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

        async def _fetch():
            from motor.motor_asyncio import AsyncIOMotorClient
            c = AsyncIOMotorClient(os.environ["MONGO_URL"])
            db = c[os.environ.get("DB_NAME", "test_database")]
            return await (
                db.whatsapp_provider_log
                .find({"event_key": "otp_password_reset"}, {"_id": 0})
                .sort("ts", -1)
                .to_list(1)
            )

        rows = asyncio.run(_fetch())
        if not rows:
            pytest.skip("no otp_password_reset dispatch logged (provider not configured)")
        req = rows[0].get("request") or {}
        assert req.get("contact_email"), f"contact_email missing in payload: {sorted(req.keys())}"
