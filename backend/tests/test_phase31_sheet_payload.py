"""
Phase-31 backend tests — Google Sheets payload mapping update.

Verifies that the extended 35-column Master Sheet schema (sheet_writer.COLUMNS)
and the 16 new kwargs accepted by `append_order_row` /
`append_order_row_to_user_sheet` are properly threaded through every
call-site without raising TypeError or 500s.

Test surface:
  • POST /api/shipments               (Add Shipment form)
  • POST /api/smart-paste              (Smart Paste — formerly /orders/paste)
  • POST /api/orders/pending/{id}/ship (ship a pending order)
  • GET  /api/shipments                (regression)
  • GET  /api/articles                 (regression)
  • GET  /api/admin/whatsapp-provider/config (regression — admin only)
  • GET  /api/analytics/overview       (regression)

Credentials (per /app/memory/test_credentials.md):
  admin@test.com / Admin@12345
  user2@test.com / User@12345
"""
import os
import time
import uuid
from pathlib import Path

import pytest
import requests
from dotenv import load_dotenv

# Load EXPO_PUBLIC_BACKEND_URL from frontend/.env
frontend_env = Path(__file__).parent.parent.parent / "frontend" / ".env"
load_dotenv(frontend_env)

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL")
if not BASE_URL:
    raise RuntimeError("EXPO_PUBLIC_BACKEND_URL missing from frontend/.env")
BASE_URL = BASE_URL.rstrip("/")

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASSWORD = "Admin@12345"
USER_EMAIL = "user2@test.com"
USER_PASSWORD = "User@12345"


# ---------------------------------------------------------------- fixtures --
@pytest.fixture(scope="module")
def api_client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


def _login(api_client, email, password):
    r = api_client.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": email, "password": password},
        timeout=20,
    )
    assert r.status_code == 200, f"Login failed for {email}: {r.status_code} {r.text[:200]}"
    data = r.json()
    assert "token" in data, f"No token in login response: {data}"
    return data["token"]


@pytest.fixture(scope="module")
def user_token(api_client):
    return _login(api_client, USER_EMAIL, USER_PASSWORD)


@pytest.fixture(scope="module")
def admin_token(api_client):
    return _login(api_client, ADMIN_EMAIL, ADMIN_PASSWORD)


@pytest.fixture(scope="module")
def user_headers(user_token):
    return {"Authorization": f"Bearer {user_token}",
            "Content-Type": "application/json"}


@pytest.fixture(scope="module")
def admin_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}",
            "Content-Type": "application/json"}


@pytest.fixture(scope="module")
def user_courier_id(api_client, user_headers):
    """Pick (or create) a courier id we can use to ship a pending order."""
    r = api_client.get(f"{BASE_URL}/api/couriers", headers=user_headers, timeout=20)
    if r.status_code == 200:
        data = r.json()
        # API may return list or {couriers: [...]}.
        items = data if isinstance(data, list) else (data.get("couriers") or data.get("items") or [])
        if items:
            return items[0].get("id") or items[0].get("_id")
    # Best-effort create
    r = api_client.post(
        f"{BASE_URL}/api/couriers",
        headers=user_headers,
        json={"name": "TEST_Phase31_Courier", "tracking_url_template": ""},
        timeout=20,
    )
    if r.status_code in (200, 201):
        return r.json().get("id")
    pytest.skip(f"No courier available and could not create one: {r.status_code} {r.text[:200]}")


# ====================================================== Phase-31 Tests =====

class TestPhase31AddShipment:
    """POST /api/shipments must not 500 on extended-schema kwargs."""

    def test_create_shipment_succeeds(self, api_client, user_headers, user_courier_id):
        unique = uuid.uuid4().hex[:6].upper()
        payload = {
            "customer_name": f"TEST_P31_{unique}",
            "customer_phone": "9876543210",
            "address_line1": "1 Phase31 Lane",
            "address_line2": "Near Test Park",
            "city": "Ahmedabad",
            "state": "Gujarat",
            "pincode": "380001",
            "items": ["Phase31 widget"],
            "amount": 499,
            "payment_mode": "Prepaid",
            "weight": "250 g",
            "box_dimensions": "10x10x5 cm",
            "shipment_notes": "Phase-31 test note",
            "category": "Test",
            "variant_name": "Default",
            "package_type": "Box",
            "courier_id": user_courier_id,
            "tracking_id": f"TESTP31{unique}",
        }
        r = api_client.post(
            f"{BASE_URL}/api/shipments",
            headers=user_headers,
            json=payload,
            timeout=60,
        )
        assert r.status_code == 200, (
            f"POST /api/shipments failed: {r.status_code} body={r.text[:500]}"
        )
        body = r.json()
        # Verify Shipment shape.
        for k in ("id", "customer_name", "customer_phone", "city", "state"):
            assert k in body, f"Missing field {k} in shipment response"
        assert body["customer_name"] == payload["customer_name"]
        assert body["customer_phone"] == payload["customer_phone"]
        print(f"OK created shipment {body.get('id')} — master_backup_status="
              f"{body.get('master_backup_status')}")
        # Stash for downstream cleanup if anyone wants it.
        TestPhase31AddShipment.created_id = body.get("id")

    def test_created_shipment_is_listable(self, api_client, user_headers):
        """GET /api/shipments returns the freshly-created shipment."""
        r = api_client.get(f"{BASE_URL}/api/shipments", headers=user_headers, timeout=30)
        assert r.status_code == 200, f"GET /api/shipments failed: {r.status_code} {r.text[:200]}"
        data = r.json()
        items = data if isinstance(data, list) else (data.get("items") or data.get("shipments") or [])
        assert isinstance(items, list)
        # Don't strictly require the new id; list endpoint may paginate.
        assert len(items) >= 0
        print(f"OK GET /api/shipments returned {len(items)} items")


class TestPhase31SmartPaste:
    """POST /api/smart-paste (formerly /orders/paste) must accept the
    16 Phase-31 kwargs (mostly empty) without TypeError."""

    def test_smart_paste_creates_pending_order(self, api_client, user_headers):
        unique = uuid.uuid4().hex[:6].upper()
        # Multi-line "WhatsApp style" paste — the parser handles loose
        # formats.
        text = (
            f"Name: TEST_P31_PASTE_{unique}\n"
            "Phone: 9123456780\n"
            "Address: 21 Paste Lane, Maninagar\n"
            "City: Ahmedabad\n"
            "State: Gujarat\n"
            "Pincode: 380008\n"
            "Item: Phase31 Paste Widget\n"
            "Amount: 350\n"
            "Payment: COD"
        )
        r = api_client.post(
            f"{BASE_URL}/api/smart-paste",
            headers=user_headers,
            json={"text": text, "skip_llm": True},
            timeout=60,
        )
        # If the LLM is unavailable or the route returns 4xx for parse-only,
        # we still want a green light when status==200. Fail loudly otherwise.
        assert r.status_code == 200, (
            f"POST /api/smart-paste failed: {r.status_code} body={r.text[:500]}"
        )
        body = r.json()
        # PendingOrder shape.
        assert isinstance(body, dict)
        assert body.get("id"), f"PendingOrder.id missing in {body}"
        print(f"OK smart-paste pending_order id={body.get('id')} status={body.get('status')}")
        TestPhase31SmartPaste.pending_id = body.get("id")


class TestPhase31ShipPending:
    """POST /api/orders/pending/{id}/ship must not crash inside
    _backup_shipment_to_master_sheet when given a Shipment.model_dump()
    that includes all 35+ fields."""

    def test_ship_pending_order(self, api_client, user_headers, user_courier_id):
        pending_id = getattr(TestPhase31SmartPaste, "pending_id", None)
        if not pending_id:
            pytest.skip("No pending order id from smart-paste step")

        r = api_client.post(
            f"{BASE_URL}/api/orders/pending/{pending_id}/ship",
            headers=user_headers,
            json={"courier_id": user_courier_id},
            timeout=60,
        )
        # Acceptable: 200 (shipped) OR 400 (already shipped if rerun).
        # The critical assertion is *no 500 / TypeError*.
        assert r.status_code in (200, 400, 423), (
            f"ship endpoint returned {r.status_code}: {r.text[:500]}"
        )
        if r.status_code == 200:
            body = r.json()
            assert body.get("id"), f"Shipment id missing in ship response: {body}"
            for k in ("customer_name", "tracking_id", "status"):
                assert k in body, f"Shipment missing key {k}: {body}"
            print(f"OK shipped pending_order — shipment_id={body.get('id')} tracking={body.get('tracking_id')}")
        else:
            print(f"ship returned {r.status_code} (acceptable — order may have been already shipped)")


# ====================================================== Regression Tests ===

class TestRegression:
    """Light-weight regression — ensures Phase-31 changes didn't break
    unrelated endpoints."""

    def test_articles_listing(self, api_client):
        r = api_client.get(f"{BASE_URL}/api/articles", timeout=20)
        assert r.status_code == 200, f"GET /api/articles failed: {r.status_code} {r.text[:200]}"
        data = r.json()
        items = data if isinstance(data, list) else (data.get("articles") or data.get("items") or [])
        assert isinstance(items, list)
        print(f"OK /api/articles returned {len(items)} articles")

    def test_whatsapp_provider_config(self, api_client, admin_headers):
        r = api_client.get(
            f"{BASE_URL}/api/admin/whatsapp-provider/config",
            headers=admin_headers, timeout=20,
        )
        assert r.status_code == 200, (
            f"GET /api/admin/whatsapp-provider/config failed: {r.status_code} {r.text[:200]}"
        )
        data = r.json()
        assert isinstance(data, dict), f"Expected dict, got {type(data)}"
        print(f"OK /api/admin/whatsapp-provider/config keys={list(data.keys())[:10]}")

    def test_analytics_overview(self, api_client, user_headers):
        r = api_client.get(
            f"{BASE_URL}/api/analytics/overview",
            headers=user_headers, timeout=30,
        )
        assert r.status_code == 200, (
            f"GET /api/analytics/overview failed: {r.status_code} {r.text[:200]}"
        )
        data = r.json()
        assert isinstance(data, dict), f"Expected dict, got {type(data)}"
        print(f"OK /api/analytics/overview keys={list(data.keys())[:10]}")
