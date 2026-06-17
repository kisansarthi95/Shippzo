"""
Phase-31 rev-2 — REVERSED Order Amount semantic.

Canonical formula (single source of truth, rev-2):
    cod_amount   = "COD to Collect" entered verbatim by the operator
    token_amount = advance already received online
    amount       = cod_amount + token_amount   (Total Order Value, gross)

NO subtraction is ever performed.  No clamping.  Token may exceed COD.

Endpoints under test:
    POST  /api/shipments
    PUT   /api/shipments/{id}
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


def _new_payload(courier_id, **overrides):
    unique = uuid.uuid4().hex[:6].upper()
    base = {
        "customer_name": f"TEST_P31R2_{unique}",
        "customer_phone": "9876543210",
        "address_line1": "1 COD Rev2 Lane",
        "city": "Ahmedabad",
        "state": "Gujarat",
        "pincode": "380001",
        "items": ["Widget"],
        "courier_id": courier_id,
        "tracking_id": f"TESTR{unique}",
        "payment_mode": "Prepaid",
        "amount": 0,
    }
    base.update(overrides)
    return base


# =================================================  POST /api/shipments  =
class TestCreateShipmentMath:
    """COD: amount = cod + token (gross); cod_amount = verbatim entered."""

    def test_cod_amount_with_token_legacy_payload(
        self, api_client, user_headers, user_courier_id,
    ):
        """Legacy client: only `amount`+`token` sent (no cod_amount).
        amount=1000, token=200, COD →
        response: amount=1200, cod_amount=1000, token=200.
        """
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
        assert body["cod_amount"] == 1000, (
            f"cod_amount={body['cod_amount']} expected 1000 (verbatim)"
        )
        assert body["amount"] == 1200, (
            f"amount={body['amount']} expected 1200 (= cod+token)"
        )
        assert body["token_amount"] == 200

    def test_cod_with_explicit_cod_amount(
        self, api_client, user_headers, user_courier_id,
    ):
        """Frontend sends BOTH amount and cod_amount.
        Backend must trust cod_amount=1000 verbatim; amount=cod+token=1200.
        """
        payload = _new_payload(
            user_courier_id,
            payment_mode="COD",
            amount=1000,
            cod_amount=1000,
            token_amount=200,
        )
        r = api_client.post(
            f"{BASE_URL}/api/shipments", headers=user_headers,
            json=payload, timeout=60,
        )
        assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
        body = r.json()
        assert body["cod_amount"] == 1000
        assert body["amount"] == 1200
        assert body["token_amount"] == 200

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

    def test_cod_token_exceeds_cod_no_clamp(
        self, api_client, user_headers, user_courier_id,
    ):
        """amount=300 token=400 (token > cod): NO clamp, NO error.
        Response: amount=700 (=300+400), cod_amount=300 verbatim.
        """
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
        assert body["cod_amount"] == 300, (
            f"cod_amount={body['cod_amount']} expected 300 (verbatim, no clamp)"
        )
        assert body["amount"] == 700, (
            f"amount={body['amount']} expected 700 (= 300+400, no clamp)"
        )
        assert body["token_amount"] == 400

    def test_prepaid_amount_no_cod(
        self, api_client, user_headers, user_courier_id,
    ):
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


# =================================================  PUT /api/shipments/{id}  =
class TestUpdateShipmentMath:
    """Editing amount/cod_amount on a COD shipment re-derives the total."""

    def test_put_amount_treated_as_new_cod(
        self, api_client, user_headers, user_courier_id,
    ):
        """Create COD cod=1000 token=200 (total=1200). PUT amount=2000.
        Backend (rev-2) treats the legacy `amount` field on a COD row
        as the new COD to collect, keeps existing token=200, derives
        Total=2200. Expected: cod_amount=2000, amount=2200, token=200.
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
        assert r.status_code == 200, f"create: {r.status_code} {r.text[:300]}"
        ship = r.json()
        sid = ship["id"]
        # Baseline sanity (rev-2 semantic):
        assert ship["cod_amount"] == 1000
        assert ship["amount"] == 1200

        # PUT amount=2000 — no payment_mode in payload → fallback to
        # existing row's payment_mode=COD; no token in payload → fallback
        # to existing token=200.
        r = api_client.put(
            f"{BASE_URL}/api/shipments/{sid}",
            headers=user_headers,
            json={"amount": 2000},
            timeout=60,
        )
        assert r.status_code == 200, f"update: {r.status_code} {r.text[:300]}"
        updated = r.json()
        assert updated["payment_mode"] == "COD"
        assert updated["token_amount"] == 200
        assert updated["cod_amount"] == 2000, (
            f"cod_amount={updated['cod_amount']} expected 2000"
        )
        assert updated["amount"] == 2200, (
            f"amount={updated['amount']} expected 2200 (= 2000 + 200 existing token)"
        )

        # Persistence GET
        r = api_client.get(
            f"{BASE_URL}/api/shipments/{sid}",
            headers=user_headers, timeout=30,
        )
        assert r.status_code == 200
        persisted = r.json()
        assert persisted["cod_amount"] == 2000
        assert persisted["amount"] == 2200

    def test_put_cod_amount_recomputes_total(
        self, api_client, user_headers, user_courier_id,
    ):
        """Create COD cod=1000 token=200; PUT cod_amount=1500.
        Expected: cod_amount=1500, amount=1700 (= 1500 + 200)."""
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
        assert r.status_code == 200, f"create: {r.status_code} {r.text[:300]}"
        sid = r.json()["id"]

        r = api_client.put(
            f"{BASE_URL}/api/shipments/{sid}",
            headers=user_headers,
            json={"cod_amount": 1500},
            timeout=60,
        )
        assert r.status_code == 200, f"update: {r.status_code} {r.text[:300]}"
        updated = r.json()
        assert updated["cod_amount"] == 1500
        assert updated["amount"] == 1700, (
            f"amount={updated['amount']} expected 1700 (= 1500 + 200)"
        )
        assert updated["token_amount"] == 200


# =======================================  Smart Paste → Ship Pending  =
class TestSmartPasteShipMath:
    """Promoting a pending order applies rev-2 math: amount = cod + token."""

    def test_ship_from_pending_amount_is_cod_plus_token(
        self, api_client, user_headers, user_courier_id,
    ):
        """Pending row amount=600 token=150 →
        resulting shipment cod_amount=600, amount=750 (= 600+150).
        """
        unique = uuid.uuid4().hex[:6].upper()
        text = (
            f"Name: TEST_P31R2_PASTE_{unique}\n"
            "Phone: 9123456780\n"
            "Address: 21 Rev2 Lane\n"
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
        assert r.status_code == 200, f"smart-paste: {r.status_code} {r.text[:400]}"
        pending = r.json()
        pending_id = pending.get("id")
        assert pending_id, f"PendingOrder.id missing: {pending}"

        # Parser sanity
        assert float(pending.get("amount") or 0) == 600
        assert float(pending.get("token_amount") or 0) == 150, (
            f"pending.token={pending.get('token_amount')} expected 150"
        )
        assert (pending.get("payment_mode") or "").upper() == "COD"

        r = api_client.post(
            f"{BASE_URL}/api/orders/pending/{pending_id}/ship",
            headers=user_headers,
            json={"courier_id": user_courier_id},
            timeout=60,
        )
        assert r.status_code == 200, f"ship: {r.status_code} {r.text[:400]}"
        ship = r.json()
        assert ship["token_amount"] == 150
        assert ship["cod_amount"] == 600, (
            f"cod_amount={ship['cod_amount']} expected 600 (verbatim)"
        )
        assert ship["amount"] == 750, (
            f"amount={ship['amount']} expected 750 (= 600 + 150)"
        )


# =====================================  GET /api/shipments/export/csv  =
class TestCsvExportCodBalance:
    """COD Balance column must show the stored cod_amount verbatim."""

    def test_cod_balance_uses_verbatim_cod(
        self, api_client, user_headers, user_courier_id,
    ):
        """Create amount=1000 token=200 COD → stored cod_amount=1000,
        amount=1200. CSV's COD Balance column = "1000.00" (verbatim COD,
        NOT 800 = 1000-200 and NOT 1200 = total).
        """
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

        r = api_client.get(
            f"{BASE_URL}/api/shipments/export/csv",
            headers=user_headers, timeout=60,
        )
        assert r.status_code == 200, f"csv export: {r.status_code}"
        csv_text = r.text.lstrip("\ufeff")
        reader = csv.DictReader(io.StringIO(csv_text))
        rows = [row for row in reader if row.get("Shipment ID") == ship_id]
        assert rows, f"Shipment {ship_id} not found in CSV export"
        row = rows[0]

        # Total amount column = 1200 (rev-2 = cod + token)
        assert row["Amount"] == "1200.00", (
            f"Amount col={row['Amount']!r} expected '1200.00'"
        )
        assert row["Token Amount"] == "200.00", (
            f"Token col={row['Token Amount']!r}"
        )
        # The key Phase-31-rev2 assertion: COD Balance = stored cod_amount
        # verbatim (= 1000), NOT 800 (old subtractive math) and NOT 1200
        # (the gross total).
        assert row["COD Balance"] == "1000.00", (
            f"COD Balance col={row['COD Balance']!r} expected '1000.00' "
            "(verbatim COD; not 800 subtractive, not 1200 total)"
        )
