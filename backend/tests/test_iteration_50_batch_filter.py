"""
Iteration 50 — F10.1 backend tests
- Verify GET /api/shipments?payment_batch_id=<id> filters correctly
- Verify GET /api/payment-batches lists batches
- Verify GET /api/payment-batches/{id} returns detail
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "https://logistics-hub-740.preview.emergentagent.com").rstrip("/")

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASSWORD = "Admin@12345"


@pytest.fixture(scope="module")
def token():
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=20,
    )
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text}"
    body = r.json()
    assert "token" in body
    return body["token"]


@pytest.fixture
def api(token):
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    return s


# Payment batches list endpoint
class TestPaymentBatches:
    def test_list_batches_200(self, api):
        r = api.get(f"{BASE_URL}/api/payment-batches", timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        # Accept either list or wrapped structure
        assert isinstance(data, (list, dict))
        # Store first batch id for downstream tests
        batches = data if isinstance(data, list) else data.get("batches", [])
        pytest.batches = batches  # type: ignore

    def test_get_batch_detail(self, api):
        batches = getattr(pytest, "batches", [])
        if not batches:
            pytest.skip("No batches available to test detail endpoint")
        bid = batches[0].get("id")
        assert bid, "Batch missing id field"
        r = api.get(f"{BASE_URL}/api/payment-batches/{bid}", timeout=15)
        assert r.status_code == 200, r.text
        b = r.json()
        assert b.get("id") == bid
        assert "name" in b
        assert "total_articles" in b
        assert "total_amount" in b


# Shipments filter by payment_batch_id
class TestShipmentsBatchFilter:
    def test_shipments_filter_by_batch(self, api):
        batches = getattr(pytest, "batches", [])
        if not batches:
            pytest.skip("No batches; can't test payment_batch_id filter")
        # Pick a batch with articles > 0 preferably
        bid = None
        expected_min = 0
        for b in batches:
            if int(b.get("total_articles") or 0) > 0:
                bid = b["id"]
                expected_min = int(b["total_articles"])
                break
        if not bid:
            bid = batches[0]["id"]
        r = api.get(
            f"{BASE_URL}/api/shipments",
            params={"payment_batch_id": bid, "limit": 500},
            timeout=20,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        ships = data if isinstance(data, list) else data.get("shipments", [])
        # Every returned shipment must have this payment_batch_id
        for s in ships:
            assert s.get("payment_batch_id") == bid, (
                f"Shipment {s.get('id')} has payment_batch_id={s.get('payment_batch_id')}, expected {bid}"
            )
        # If batch reported non-zero articles, we should see them
        if expected_min > 0:
            assert len(ships) == expected_min, (
                f"Batch reports {expected_min} articles but filter returned {len(ships)}"
            )

    def test_shipments_filter_by_unknown_batch_returns_empty(self, api):
        r = api.get(
            f"{BASE_URL}/api/shipments",
            params={"payment_batch_id": "batch_does_not_exist_TEST"},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        ships = data if isinstance(data, list) else data.get("shipments", [])
        assert ships == [] or len(ships) == 0

    def test_shipments_no_filter_returns_all(self, api):
        r = api.get(f"{BASE_URL}/api/shipments", params={"limit": 5}, timeout=15)
        assert r.status_code == 200
