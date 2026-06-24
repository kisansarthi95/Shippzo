"""
Phase-31 rev-3 — ship_pending_order COD overrides bug fix verification.

Bug history:
  Pre rev-3, `POST /api/orders/pending/{id}/ship` ignored
  `overrides.cod_amount`. It used pending.amount for BOTH the
  `cod_amount` field on the new shipment AND the `amount` total
  (pending.amount + token). When an operator edited the COD value
  in the Ship form before tapping Ship, the override was discarded
  and the token got double-counted into amount.

Fix:
  cod_amount = overrides.cod_amount if PRESENT else pending.amount
  amount     = cod_amount + token_amount  (COD)
             = override/pending amount    (Prepaid)

Tests in this file cover only the rev-3 fix; the broader rev-2
COD-math regressions live in test_phase31_cod_math.py.

Credentials per /app/memory/test_credentials.md:
    user2@test.com / User@12345
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
    return {
        "Authorization": f"Bearer {r.json()['token']}",
        "Content-Type": "application/json",
    }


@pytest.fixture(scope="module")
def user_courier_id(api_client, user_headers):
    r = api_client.get(
        f"{BASE_URL}/api/couriers", headers=user_headers, timeout=20,
    )
    if r.status_code != 200:
        pytest.skip(f"GET /couriers failed: {r.status_code}")
    data = r.json()
    items = data if isinstance(data, list) else (
        data.get("couriers") or data.get("items") or []
    )
    if not items:
        pytest.skip("No courier available")
    return items[0].get("id") or items[0].get("_id")


# ------------------------------------------------------------- helpers --
def _smart_paste_pending(api_client, user_headers, *, amount, token,
                         payment_mode="COD"):
    """Create a pending order via smart-paste and return the row."""
    unique = uuid.uuid4().hex[:6].upper()
    text = (
        f"Name: TEST_P31R3_{unique}\n"
        "Phone: 9123456780\n"
        "Address: 11 Rev3 Lane\n"
        "City: Ahmedabad\n"
        "State: Gujarat\n"
        "Pincode: 380011\n"
        "Item: Override Widget\n"
        f"Amount: {amount}\n"
        f"Token: {token}\n"
        f"Payment: {payment_mode}"
    )
    r = api_client.post(
        f"{BASE_URL}/api/smart-paste",
        headers=user_headers,
        json={"text": text, "skip_llm": True},
        timeout=60,
    )
    assert r.status_code == 200, f"smart-paste: {r.status_code} {r.text[:300]}"
    return r.json()


# ============================================================= TESTS  ==
class TestRev3CodOverrideHonoured:
    """The override.cod_amount must replace pending.amount as COD."""

    def test_override_cod_amount_used_not_pending_amount(
        self, api_client, user_headers, user_courier_id,
    ):
        """Pending amount=1000, override cod=800/token=200/COD.
        Expected: cod_amount=800, amount=1000 (=800+200), token=200.
        Pre-fix bug: cod=1000, amount=1200 (double-counted token).
        """
        pending = _smart_paste_pending(
            api_client, user_headers, amount=1000, token=200,
        )
        pid = pending["id"]
        assert float(pending["amount"]) == 1000
        assert (pending.get("payment_mode") or "").upper() == "COD"

        r = api_client.post(
            f"{BASE_URL}/api/orders/pending/{pid}/ship",
            headers=user_headers,
            json={
                "courier_id": user_courier_id,
                "overrides": {
                    "cod_amount":   800,
                    "token_amount": 200,
                    "payment_mode": "COD",
                },
            },
            timeout=60,
        )
        assert r.status_code == 200, f"ship: {r.status_code} {r.text[:400]}"
        ship = r.json()
        assert ship["cod_amount"] == 800, (
            f"cod_amount={ship['cod_amount']} expected 800 "
            "(override honoured, NOT pending.amount=1000)"
        )
        assert ship["token_amount"] == 200
        assert ship["amount"] == 1000, (
            f"amount={ship['amount']} expected 1000 (=800+200), "
            "NOT 1200 which would mean token double-counted"
        )

    def test_override_cod_higher_than_pending_amount(
        self, api_client, user_headers, user_courier_id,
    ):
        """Pending amount=500, override cod=1500/token=200/COD.
        Expected: cod=1500, amount=1700 (=1500+200), NOT 700.
        """
        pending = _smart_paste_pending(
            api_client, user_headers, amount=500, token=0,
        )
        pid = pending["id"]
        assert float(pending["amount"]) == 500

        r = api_client.post(
            f"{BASE_URL}/api/orders/pending/{pid}/ship",
            headers=user_headers,
            json={
                "courier_id": user_courier_id,
                "overrides": {
                    "cod_amount":   1500,
                    "token_amount": 200,
                    "payment_mode": "COD",
                },
            },
            timeout=60,
        )
        assert r.status_code == 200, f"ship: {r.status_code} {r.text[:400]}"
        ship = r.json()
        assert ship["cod_amount"] == 1500, (
            f"cod_amount={ship['cod_amount']} expected 1500 (override)"
        )
        assert ship["token_amount"] == 200
        assert ship["amount"] == 1700, (
            f"amount={ship['amount']} expected 1700 (=1500+200), "
            "NOT 700 (=500+200 pre-fix bug)"
        )


class TestRev3SmartPasteFallback:
    """Without cod_amount in overrides, fall back to pending.amount."""

    def test_no_override_cod_uses_pending_amount(
        self, api_client, user_headers, user_courier_id,
    ):
        """Pending amount=600 token=150, ship overrides=courier only.
        Expected: cod=600, amount=750 (=600+150), token=150.
        """
        pending = _smart_paste_pending(
            api_client, user_headers, amount=600, token=150,
        )
        pid = pending["id"]

        r = api_client.post(
            f"{BASE_URL}/api/orders/pending/{pid}/ship",
            headers=user_headers,
            json={"courier_id": user_courier_id},
            timeout=60,
        )
        assert r.status_code == 200, f"ship: {r.status_code} {r.text[:400]}"
        ship = r.json()
        assert ship["cod_amount"] == 600
        assert ship["token_amount"] == 150
        assert ship["amount"] == 750


class TestRev3CodValidation:
    """cod_amount=0 in overrides for COD mode → 422."""

    def test_override_cod_zero_rejected(
        self, api_client, user_headers, user_courier_id,
    ):
        """Override.cod_amount=0 with COD mode must return 422.
        This is the regression check that the validator looks at
        the EFFECTIVE cod (= overrides.cod_amount when present),
        not blindly at pending.amount.
        """
        pending = _smart_paste_pending(
            api_client, user_headers, amount=500, token=0,
        )
        pid = pending["id"]

        r = api_client.post(
            f"{BASE_URL}/api/orders/pending/{pid}/ship",
            headers=user_headers,
            json={
                "courier_id": user_courier_id,
                "overrides": {
                    "cod_amount":   0,
                    "token_amount": 100,
                    "payment_mode": "COD",
                },
            },
            timeout=60,
        )
        assert r.status_code == 422, (
            f"Expected 422 for cod=0 in COD-mode override; got "
            f"{r.status_code}. body={r.text[:300]}"
        )


class TestRev3PrepaidUnaffected:
    """Prepaid path: cod_amount=0, amount=overrides.amount."""

    def test_prepaid_override_amount(
        self, api_client, user_headers, user_courier_id,
    ):
        """Prepaid pending, override amount=999.
        Expected: cod=0, amount=999.
        """
        pending = _smart_paste_pending(
            api_client, user_headers, amount=400, token=0,
            payment_mode="Prepaid",
        )
        pid = pending["id"]
        # Smart paste might normalise payment_mode; tolerate either
        # case but confirm it didn't land as COD.
        pm = (pending.get("payment_mode") or "").upper()
        if pm == "COD":
            pytest.skip(
                "smart-paste returned payment_mode=COD for a "
                "Prepaid pasted row — outside rev-3 scope"
            )

        r = api_client.post(
            f"{BASE_URL}/api/orders/pending/{pid}/ship",
            headers=user_headers,
            json={
                "courier_id": user_courier_id,
                "overrides": {
                    "amount":       999,
                    "payment_mode": "Prepaid",
                },
            },
            timeout=60,
        )
        assert r.status_code == 200, f"ship: {r.status_code} {r.text[:400]}"
        ship = r.json()
        assert ship["cod_amount"] == 0, (
            f"cod_amount={ship['cod_amount']} expected 0 for Prepaid"
        )
        assert ship["amount"] == 999, (
            f"amount={ship['amount']} expected 999 (override.amount)"
        )
