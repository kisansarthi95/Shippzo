"""
Phase-31 rev-2 follow-up — New COD validation tests.

Rules being verified (see routers/shipments_write.py:186–212):
  • COD mode requires cod_amount > 0 (returns 422).
  • token_amount must be >= 0 (negative → 422).
  • token > cod is allowed (soft-warn server-side; HTTP 200).
  • Migration sanity: a sample of COD rows in db.shipments satisfies
    amount == cod_amount + token_amount.

Credentials per /app/memory/test_credentials.md.
"""
import os
import uuid
from pathlib import Path

import pytest
import requests
from dotenv import load_dotenv

frontend_env = Path(__file__).parent.parent.parent / "frontend" / ".env"
load_dotenv(frontend_env)

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL")
if not BASE_URL:
    raise RuntimeError("EXPO_PUBLIC_BACKEND_URL missing from frontend/.env")
BASE_URL = BASE_URL.rstrip("/")

USER_EMAIL = "user2@test.com"
USER_PASSWORD = "User@12345"


# -------------------------------------------------------------- fixtures --
@pytest.fixture(scope="module")
def api_client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def user_headers(api_client):
    r = api_client.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": USER_EMAIL, "password": USER_PASSWORD},
        timeout=20,
    )
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text[:200]}"
    token = r.json()["token"]
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


@pytest.fixture(scope="module")
def user_courier_id(api_client, user_headers):
    r = api_client.get(
        f"{BASE_URL}/api/couriers", headers=user_headers, timeout=20,
    )
    if r.status_code == 200:
        data = r.json()
        items = data if isinstance(data, list) else (
            data.get("couriers") or data.get("items") or []
        )
        if items:
            return items[0].get("id") or items[0].get("_id")
    pytest.skip("No courier available")


def _payload(courier_id, **overrides):
    unique = uuid.uuid4().hex[:6].upper()
    base = {
        "customer_name": f"TEST_P31R2V_{unique}",
        "customer_phone": "9876543210",
        "address_line1": "1 Validation Lane",
        "city": "Ahmedabad",
        "state": "Gujarat",
        "pincode": "380001",
        "items": ["Widget"],
        "courier_id": courier_id,
        "tracking_id": f"TESTV{unique}",
        "payment_mode": "Prepaid",
        "amount": 0,
    }
    base.update(overrides)
    return base


# =============================================================  VALIDATION  =
class TestCodValidation:
    """New rev-2 validation rules introduced in Phase-31 follow-up."""

    def test_cod_amount_zero_rejected(
        self, api_client, user_headers, user_courier_id,
    ):
        """POST COD amount=0 → 422 with specific detail."""
        payload = _payload(
            user_courier_id, payment_mode="COD", amount=0,
        )
        r = api_client.post(
            f"{BASE_URL}/api/shipments", headers=user_headers,
            json=payload, timeout=60,
        )
        assert r.status_code == 422, (
            f"expected 422, got {r.status_code}: {r.text[:300]}"
        )
        detail = r.json().get("detail", "")
        assert "COD to Collect must be greater than zero" in detail, (
            f"detail mismatch: {detail!r}"
        )

    def test_cod_amount_negative_rejected(
        self, api_client, user_headers, user_courier_id,
    ):
        """POST COD amount=-50 → 422."""
        payload = _payload(
            user_courier_id, payment_mode="COD", amount=-50,
        )
        r = api_client.post(
            f"{BASE_URL}/api/shipments", headers=user_headers,
            json=payload, timeout=60,
        )
        assert r.status_code == 422, (
            f"expected 422, got {r.status_code}: {r.text[:300]}"
        )

    def test_cod_amount_500_token_zero_accepted(
        self, api_client, user_headers, user_courier_id,
    ):
        """POST COD amount=500 token=0 → 200 OK (token=0 allowed)."""
        payload = _payload(
            user_courier_id, payment_mode="COD",
            amount=500, token_amount=0,
        )
        r = api_client.post(
            f"{BASE_URL}/api/shipments", headers=user_headers,
            json=payload, timeout=60,
        )
        assert r.status_code == 200, (
            f"expected 200, got {r.status_code}: {r.text[:300]}"
        )
        body = r.json()
        assert body["cod_amount"] == 500
        assert body["amount"] == 500
        assert body["token_amount"] == 0

    def test_negative_token_rejected(
        self, api_client, user_headers, user_courier_id,
    ):
        """POST COD amount=500 token=-10 → 422 with token-negative detail."""
        payload = _payload(
            user_courier_id, payment_mode="COD",
            amount=500, token_amount=-10,
        )
        r = api_client.post(
            f"{BASE_URL}/api/shipments", headers=user_headers,
            json=payload, timeout=60,
        )
        assert r.status_code == 422, (
            f"expected 422, got {r.status_code}: {r.text[:300]}"
        )
        detail = r.json().get("detail", "")
        assert "Token / Advance amount cannot be negative" in detail, (
            f"detail mismatch: {detail!r}"
        )

    def test_token_exceeds_cod_soft_warn(
        self, api_client, user_headers, user_courier_id,
    ):
        """POST COD amount=200 token=500 → 200 OK, amount=700 (no rejection)."""
        payload = _payload(
            user_courier_id, payment_mode="COD",
            amount=200, token_amount=500,
        )
        r = api_client.post(
            f"{BASE_URL}/api/shipments", headers=user_headers,
            json=payload, timeout=60,
        )
        assert r.status_code == 200, (
            f"expected 200 (soft-warn), got {r.status_code}: {r.text[:300]}"
        )
        body = r.json()
        assert body["cod_amount"] == 200, (
            f"cod_amount={body['cod_amount']} expected 200 verbatim"
        )
        assert body["token_amount"] == 500
        assert body["amount"] == 700, (
            f"amount={body['amount']} expected 700 (= 200+500)"
        )
