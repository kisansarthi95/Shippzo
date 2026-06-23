"""
Phase-33 — Weight Parser (POST /api/shipments, PUT /api/shipments/{id}).

The backend now normalises the operator-typed `weight` string to a
structured triple (weight, weight_value, weight_unit) before persisting:

  Happy paths:
    "250gm"    → weight="250 g",  value=250,   unit="g"
    "500 gm"   → weight="500 g",  value=500,   unit="g"
    "1kg"      → weight="1 kg",   value=1,     unit="kg"
    "2 kg"     → weight="2 kg",   value=2,     unit="kg"
    "1.5 kg"   → weight="1.5 kg", value=1.5,   unit="kg"
    "250g"     → weight="250 g",  value=250,   unit="g"
    "3 grams"  → weight="3 g",    value=3,     unit="g"
    "500"      → infers g  (>50 implies grams)
    "1.5"      → infers kg (<=50 implies kg)

  Rejection (HTTP 422 "Couldn't parse weight"):
    "abc", "10x20", "heavy"

  Blank pass-through (200 OK, all three weight* fields blank/zero):
    "", None / missing

PUT /api/shipments/{id} weight edit:
    {"weight": "750gm"} → weight="750 g", value=750, unit="g"

Reference:
  /app/backend/routers/shipments_write.py (_WEIGHT_RE / _parse_weight /
  _apply_weight_parse — lines ~93-167)

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
BASE_URL = (
    os.environ.get("EXPO_PUBLIC_BACKEND_URL")
    or os.environ.get("EXPO_BACKEND_URL")
)
if not BASE_URL:
    raise RuntimeError("EXPO_PUBLIC_BACKEND_URL / EXPO_BACKEND_URL missing")
BASE_URL = BASE_URL.rstrip("/")

USER_EMAIL = "user2@test.com"
USER_PASSWORD = "User@12345"


# ─────────────────────────────────────────────────────────── fixtures ──
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
    assert r.status_code == 200, f"Couriers fetch failed: {r.status_code}"
    data = r.json()
    items = data if isinstance(data, list) else (
        data.get("couriers") or data.get("items") or []
    )
    for c in items:
        if not c.get("manual_tracking"):
            return c.get("id") or c.get("_id")
    pytest.skip("No auto-tracking courier available")


def _base_payload(courier_id, **overrides):
    """Minimal valid Prepaid shipment payload — weight overridden per test."""
    unique = uuid.uuid4().hex[:6].upper()
    base = {
        "customer_name": f"TEST_P33_WT_{unique}",
        "customer_phone": "9876543210",
        "address_line1": "1 Weight Lane",
        "city": "Ahmedabad",
        "state": "Gujarat",
        "pincode": "380001",
        "items": ["Widget"],
        "courier_id": courier_id,
        "tracking_id": f"TESTW{unique}",
        "payment_mode": "Prepaid",
        "amount": 100,
    }
    base.update(overrides)
    return base


# ════════════════════════════════════ POST /api/shipments — happy paths
class TestWeightParserHappyPaths:
    """Verify the 9 documented happy-path normalisations."""

    @pytest.mark.parametrize(
        "raw,expected_display,expected_value,expected_unit",
        [
            ("250gm",   "250 g",   250.0, "g"),
            ("500 gm",  "500 g",   500.0, "g"),
            ("1kg",     "1 kg",    1.0,   "kg"),
            ("2 kg",    "2 kg",    2.0,   "kg"),
            ("1.5 kg",  "1.5 kg",  1.5,   "kg"),
            ("250g",    "250 g",   250.0, "g"),
            ("3 grams", "3 g",     3.0,   "g"),
            # Bare number heuristic — >50 → grams; <=50 → kg
            ("500",     "500 g",   500.0, "g"),
            ("1.5",     "1.5 kg",  1.5,   "kg"),
        ],
    )
    def test_post_weight_parsed(
        self, api_client, user_headers, user_courier_id,
        raw, expected_display, expected_value, expected_unit,
    ):
        payload = _base_payload(user_courier_id, weight=raw)
        r = api_client.post(
            f"{BASE_URL}/api/shipments",
            headers=user_headers, json=payload, timeout=60,
        )
        assert r.status_code == 200, (
            f"weight={raw!r}: expected 200, got {r.status_code} {r.text[:300]}"
        )
        body = r.json()
        assert body["weight"] == expected_display, (
            f"weight={raw!r}: weight={body['weight']!r} "
            f"expected {expected_display!r}"
        )
        assert float(body["weight_value"]) == expected_value, (
            f"weight={raw!r}: weight_value={body['weight_value']!r} "
            f"expected {expected_value}"
        )
        assert body["weight_unit"] == expected_unit, (
            f"weight={raw!r}: weight_unit={body['weight_unit']!r} "
            f"expected {expected_unit!r}"
        )


# ════════════════════════════ POST /api/shipments — rejection (HTTP 422)
class TestWeightParserRejection:
    """Garbage strings → 422 with 'Couldn't parse weight' detail."""

    @pytest.mark.parametrize("raw", ["abc", "10x20", "heavy"])
    def test_post_invalid_weight_returns_422(
        self, api_client, user_headers, user_courier_id, raw,
    ):
        payload = _base_payload(user_courier_id, weight=raw)
        r = api_client.post(
            f"{BASE_URL}/api/shipments",
            headers=user_headers, json=payload, timeout=60,
        )
        assert r.status_code == 422, (
            f"weight={raw!r}: expected 422, got {r.status_code} {r.text[:300]}"
        )
        body = r.json()
        detail = str(body.get("detail", ""))
        assert "Couldn't parse weight" in detail or "parse weight" in detail, (
            f"weight={raw!r}: detail did not mention parse failure: "
            f"{detail!r}"
        )


# ═══════════════════════════ POST /api/shipments — blank pass-through ══
class TestWeightParserBlankPassThrough:
    """Blank / missing weight is accepted; all three fields are blank."""

    def test_post_empty_string_weight(
        self, api_client, user_headers, user_courier_id,
    ):
        payload = _base_payload(user_courier_id, weight="")
        r = api_client.post(
            f"{BASE_URL}/api/shipments",
            headers=user_headers, json=payload, timeout=60,
        )
        assert r.status_code == 200, (
            f"empty weight: got {r.status_code} {r.text[:300]}"
        )
        body = r.json()
        assert body["weight"] == "", f"weight={body['weight']!r} expected ''"
        assert float(body["weight_value"]) == 0.0
        assert body["weight_unit"] == ""

    def test_post_missing_weight_key(
        self, api_client, user_headers, user_courier_id,
    ):
        """No `weight` key in body → defaults / blank."""
        payload = _base_payload(user_courier_id)
        payload.pop("weight", None)
        r = api_client.post(
            f"{BASE_URL}/api/shipments",
            headers=user_headers, json=payload, timeout=60,
        )
        assert r.status_code == 200, (
            f"missing weight: got {r.status_code} {r.text[:300]}"
        )
        body = r.json()
        assert body["weight"] == ""
        assert float(body["weight_value"]) == 0.0
        assert body["weight_unit"] == ""

    def test_post_weight_none(
        self, api_client, user_headers, user_courier_id,
    ):
        """weight=None explicitly → blank pass-through (Optional[str])."""
        payload = _base_payload(user_courier_id, weight=None)
        r = api_client.post(
            f"{BASE_URL}/api/shipments",
            headers=user_headers, json=payload, timeout=60,
        )
        assert r.status_code == 200, (
            f"weight=None: got {r.status_code} {r.text[:300]}"
        )
        body = r.json()
        assert body["weight"] == ""
        assert float(body["weight_value"]) == 0.0
        assert body["weight_unit"] == ""


# ════════════════════════════════════════ PUT /api/shipments/{id} edit ══
class TestWeightParserPutUpdate:
    """Edit weight on existing shipment → server re-normalises triple."""

    def test_put_weight_edit_parses(
        self, api_client, user_headers, user_courier_id,
    ):
        # 1) Create with initial weight "1 kg"
        create_payload = _base_payload(user_courier_id, weight="1 kg")
        r = api_client.post(
            f"{BASE_URL}/api/shipments",
            headers=user_headers, json=create_payload, timeout=60,
        )
        assert r.status_code == 200, f"create: {r.status_code} {r.text[:300]}"
        ship = r.json()
        sid = ship["id"]
        assert ship["weight"] == "1 kg"
        assert float(ship["weight_value"]) == 1.0
        assert ship["weight_unit"] == "kg"

        # 2) PUT new weight "750gm"
        r = api_client.put(
            f"{BASE_URL}/api/shipments/{sid}",
            headers=user_headers,
            json={"weight": "750gm"},
            timeout=60,
        )
        assert r.status_code == 200, f"put: {r.status_code} {r.text[:300]}"
        updated = r.json()
        assert updated["weight"] == "750 g", (
            f"weight after PUT={updated['weight']!r} expected '750 g'"
        )
        assert float(updated["weight_value"]) == 750.0
        assert updated["weight_unit"] == "g"

        # 3) GET to verify persistence
        r = api_client.get(
            f"{BASE_URL}/api/shipments/{sid}",
            headers=user_headers, timeout=30,
        )
        assert r.status_code == 200
        persisted = r.json()
        assert persisted["weight"] == "750 g"
        assert float(persisted["weight_value"]) == 750.0
        assert persisted["weight_unit"] == "g"
