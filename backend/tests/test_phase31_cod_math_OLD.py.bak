"""
Phase-31 — Token / Advance double-counting bug fix.

Canonical formula (single source of truth):
    Total Order Value (amount) = entered amount (verbatim)
    COD to Collect (cod_amount) = max(0, amount − token_amount)

Endpoints under test:
    POST  /api/shipments
    PUT   /api/shipments/{id}                 (server only has PUT; PATCH not defined)
    GET   /api/shipments/export/csv
    POST  /api/smart-paste  +  POST /api/orders/pending/{id}/ship

Credentials per /app/memory/test_credentials.md:
    user2@test.com / User@12345
"""
import csv
import io
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
def user_token(api_client):
    r = api_client.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": USER_EMAIL, "password": USER_PASSWORD},
        timeout=20,
    )
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text[:200]}"
    return r.json()["token"]


@pytest.fixture(scope="module")
def user_headers(user_token):
    return {
        "Authorization": f"Bearer {user_token}",
        "Content-Type": "application/json",
    }


@pytest.fixture(scope="module")
def user_courier_id(api_client, user_headers):
    r = api_client.get(f"{BASE_URL}/api/couriers", headers=user_headers, timeout=20)
    if r.status_code == 200:
        data = r.json()
        items = data if isinstance(data, list) else (data.get("couriers") or data.get("items") or [])
        if items:
            return items[0].get("id") or items[0].get("_id")
    pytest.skip("No courier available")


def _new_payload(courier_id, **overrides):
    unique = uuid.uuid4().hex[:6].upper()
    base = {
        "customer_name": f"TEST_P31MATH_{unique}",
        "customer_phone": "9876543210",
        "address_line1": "1 COD Math Lane",
        "city": "Ahmedabad",
        "state": "Gujarat",
        "pincode": "380001",
        "items": ["Widget"],
        "courier_id": courier_id,
        "tracking_id": f"TESTM{unique}",
        "payment_mode": "Prepaid",
        "amount": 0,
    }
    base.update(overrides)
    return base


# =================================================  POST /api/shipments  =
class TestCreateShipmentMath:
    """COD: amount stored verbatim; cod_amount = max(0, amount - token)."""

    def test_cod_amount_with_token(self, api_client, user_headers, user_courier_id):
        """amount=1000, token=200, COD → amount=1000, cod_amount=800."""
        payload = _new_payload(
            user_courier_id,
            payment_mode="COD",
            amount=1000,
            token_amount=200,
        )
        r = api_client.post(
            f"{BASE_URL}/api/shipments", headers=user_headers,
            json=payload, timeout=60,
        )
        assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
        body = r.json()
        assert body["amount"] == 1000, f"amount={body['amount']} expected 1000"
        assert body["cod_amount"] == 800, f"cod_amount={body['cod_amount']} expected 800"
        assert body["token_amount"] == 200, f"token={body['token_amount']} expected 200"
        TestCreateShipmentMath.cod_with_token_id = body["id"]

    def test_cod_no_token(self, api_client, user_headers, user_courier_id):
        """amount=500, token=0, COD → amount=500, cod_amount=500."""
        payload = _new_payload(
            user_courier_id,
            payment_mode="COD",
            amount=500,
            token_amount=0,
        )
        r = api_client.post(
            f"{BASE_URL}/api/shipments", headers=user_headers,
            json=payload, timeout=60,
        )
        assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
        body = r.json()
        assert body["amount"] == 500
        assert body["cod_amount"] == 500
        assert body["token_amount"] == 0

    def test_cod_token_exceeds_amount_clamps_to_zero(
        self, api_client, user_headers, user_courier_id,
    ):
        """amount=300, token=400, COD → cod_amount must clamp to 0 (no negative)."""
        payload = _new_payload(
            user_courier_id,
            payment_mode="COD",
            amount=300,
            token_amount=400,
        )
        r = api_client.post(
            f"{BASE_URL}/api/shipments", headers=user_headers,
            json=payload, timeout=60,
        )
        assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
        body = r.json()
        assert body["amount"] == 300
        assert body["cod_amount"] == 0, (
            f"cod_amount={body['cod_amount']} expected 0 (clamped, must not go negative)"
        )
        assert body["token_amount"] == 400

    def test_prepaid_amount_no_cod(self, api_client, user_headers, user_courier_id):
        """Prepaid amount=750 → amount=750, cod_amount=0."""
        payload = _new_payload(
            user_courier_id,
            payment_mode="Prepaid",
            amount=750,
        )
        r = api_client.post(
            f"{BASE_URL}/api/shipments", headers=user_headers,
            json=payload, timeout=60,
        )
        assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
        body = r.json()
        assert body["amount"] == 750
        assert body["cod_amount"] == 0

    def test_cod_no_explicit_cod_amount_payload(
        self, api_client, user_headers, user_courier_id,
    ):
        """Regression: send only amount + token, omit cod_amount entirely.
        Backend must derive cod_amount without KeyError / NaN."""
        payload = _new_payload(
            user_courier_id,
            payment_mode="COD",
            amount=900,
            token_amount=100,
        )
        payload.pop("cod_amount", None)
        r = api_client.post(
            f"{BASE_URL}/api/shipments", headers=user_headers,
            json=payload, timeout=60,
        )
        assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
        body = r.json()
        assert body["amount"] == 900
        assert body["cod_amount"] == 800
        assert isinstance(body["cod_amount"], (int, float))


# =================================================  PUT /api/shipments/{id}  =
class TestUpdateShipmentMath:
    """When admin edits `amount`, cod_amount must auto-recompute."""

    def test_update_amount_recomputes_cod(
        self, api_client, user_headers, user_courier_id,
    ):
        """Create COD amount=1000, token=200; PUT amount=1500
        → expect cod_amount=1300 (= max(0, 1500-200))."""
        # Step 1 — create the base shipment.
        create_payload = _new_payload(
            user_courier_id,
            payment_mode="COD",
            amount=1000,
            token_amount=200,
        )
        r = api_client.post(
            f"{BASE_URL}/api/shipments", headers=user_headers,
            json=create_payload, timeout=60,
        )
        assert r.status_code == 200, f"create failed: {r.status_code} {r.text[:300]}"
        ship = r.json()
        sid = ship["id"]
        assert ship["cod_amount"] == 800

        # Step 2 — update amount only.  Send payment_mode in payload too so
        # the backend's math branch sees a COD context (the current impl
        # reads payment_mode from `update` not the existing doc).
        r = api_client.put(
            f"{BASE_URL}/api/shipments/{sid}",
            headers=user_headers,
            json={"amount": 1500, "payment_mode": "COD"},
            timeout=60,
        )
        assert r.status_code == 200, f"update failed: {r.status_code} {r.text[:300]}"
        updated = r.json()
        assert updated["amount"] == 1500
        assert updated["cod_amount"] == 1300, (
            f"cod_amount={updated['cod_amount']} expected 1300 "
            f"(= 1500 - 200 existing token)"
        )

        # Step 3 — verify persistence via GET.
        r = api_client.get(
            f"{BASE_URL}/api/shipments/{sid}",
            headers=user_headers, timeout=30,
        )
        assert r.status_code == 200
        persisted = r.json()
        assert persisted["amount"] == 1500
        assert persisted["cod_amount"] == 1300

    def test_update_amount_without_payment_mode(
        self, api_client, user_headers, user_courier_id,
    ):
        """Edge case: PUT only amount (no payment_mode in payload) on a
        COD shipment. The canonical math must still recognise the
        existing shipment as COD and produce cod_amount=1300.
        If the current implementation drops to 0 because it only checks
        update.get('payment_mode'), this test will surface that bug.
        """
        create_payload = _new_payload(
            user_courier_id,
            payment_mode="COD",
            amount=1000,
            token_amount=200,
        )
        r = api_client.post(
            f"{BASE_URL}/api/shipments", headers=user_headers,
            json=create_payload, timeout=60,
        )
        assert r.status_code == 200
        sid = r.json()["id"]

        r = api_client.put(
            f"{BASE_URL}/api/shipments/{sid}",
            headers=user_headers,
            json={"amount": 1500},  # payment_mode intentionally omitted
            timeout=60,
        )
        assert r.status_code == 200
        updated = r.json()
        assert updated["amount"] == 1500
        # NOTE: If this assertion fails the bug is in
        # routers/shipments_write.py:357-377 — the COD detection only
        # checks update.get("payment_mode") and ignores the existing
        # row's payment_mode, so cod_amount silently flips to 0.
        assert updated["cod_amount"] == 1300, (
            f"cod_amount={updated['cod_amount']} expected 1300 — "
            "update path is ignoring existing payment_mode=COD"
        )


# =====================================  GET /api/shipments/export/csv  =
class TestCsvExportCodBalance:
    """COD Balance column in CSV must reflect stored cod_amount."""

    def test_cod_balance_uses_stored_cod_amount(
        self, api_client, user_headers, user_courier_id,
    ):
        # Create amount=1000 token=200 → cod_amount=800 stored.
        payload = _new_payload(
            user_courier_id,
            payment_mode="COD",
            amount=1000,
            token_amount=200,
        )
        r = api_client.post(
            f"{BASE_URL}/api/shipments", headers=user_headers,
            json=payload, timeout=60,
        )
        assert r.status_code == 200
        ship = r.json()
        ship_id = ship["id"]
        tracking_id = ship["tracking_id"]

        # Fetch CSV (legacy GET = all shipments for this user).
        r = api_client.get(
            f"{BASE_URL}/api/shipments/export/csv",
            headers=user_headers, timeout=60,
        )
        assert r.status_code == 200, f"csv export failed: {r.status_code}"
        csv_text = r.text.lstrip("\ufeff")  # strip BOM
        reader = csv.DictReader(io.StringIO(csv_text))
        rows = [row for row in reader if row.get("Shipment ID") == ship_id]
        assert rows, f"Shipment {ship_id} (track={tracking_id}) not found in CSV export"
        row = rows[0]

        assert row["Amount"] == "1000.00", f"Amount col={row['Amount']!r}"
        assert row["Token Amount"] == "200.00", f"Token col={row['Token Amount']!r}"
        # The critical Phase-31 assertion: CSV reflects STORED cod_amount,
        # not amount-token recomputed (which happens to match in this row
        # but is the wrong source). The cod_amount stored = 800.
        assert row["COD Balance"] == "800.00", (
            f"COD Balance col={row['COD Balance']!r} expected '800.00'"
        )


# =======================================  Smart Paste → Ship Pending  =
class TestSmartPasteShipMath:
    """Canonical math must apply when promoting a pending order."""

    def test_ship_from_pending_applies_canonical_math(
        self, api_client, user_headers, user_courier_id,
    ):
        """Pending row with amount=600 and token=150 →
        resulting shipment cod_amount=450, amount=600."""
        unique = uuid.uuid4().hex[:6].upper()
        text = (
            f"Name: TEST_P31MATH_PASTE_{unique}\n"
            "Phone: 9123456780\n"
            "Address: 21 Math Lane\n"
            "City: Ahmedabad\n"
            "State: Gujarat\n"
            "Pincode: 380008\n"
            "Item: Math Widget\n"
            "Amount: 600\n"
            "Token: 150\n"
            "Payment: COD"
        )
        r = api_client.post(
            f"{BASE_URL}/api/smart-paste",
            headers=user_headers,
            json={"text": text, "skip_llm": True},
            timeout=60,
        )
        assert r.status_code == 200, f"smart-paste failed: {r.status_code} {r.text[:400]}"
        pending = r.json()
        pending_id = pending.get("id")
        assert pending_id, f"PendingOrder.id missing: {pending}"

        # Sanity: the parser caught amount and token correctly.
        assert float(pending.get("amount") or 0) == 600, (
            f"pending.amount={pending.get('amount')} expected 600"
        )
        assert float(pending.get("token_amount") or 0) == 150, (
            f"pending.token_amount={pending.get('token_amount')} expected 150 "
            "(smart-paste parser may not have extracted Token:)"
        )
        assert (pending.get("payment_mode") or "").upper() == "COD"

        # Ship the pending order.
        r = api_client.post(
            f"{BASE_URL}/api/orders/pending/{pending_id}/ship",
            headers=user_headers,
            json={"courier_id": user_courier_id},
            timeout=60,
        )
        assert r.status_code == 200, f"ship failed: {r.status_code} {r.text[:400]}"
        ship = r.json()
        assert ship["amount"] == 600, f"amount={ship['amount']} expected 600"
        assert ship["token_amount"] == 150, f"token={ship['token_amount']} expected 150"
        assert ship["cod_amount"] == 450, (
            f"cod_amount={ship['cod_amount']} expected 450 (= 600 - 150)"
        )
