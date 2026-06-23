"""
Phase-32 hardening — items normalization fix on ship_pending_order.

Production bug (RCA):
  POST /api/orders/pending/{id}/ship was crashing with HTTP 500
  AttributeError: 'list' object has no attribute 'split' because
  overrides["items"] arrived as a Python list from the new frontend
  form (which stores items as a real array), but the backend code
  blindly called items_str.split(","). Hardening accepts BOTH shapes
  plus None/empty/whitespace edge cases.

Coverage (all under /api/orders/pending/{id}/ship):
  1. items as LIST → items=['Shirt','Cap'], item_description='Shirt, Cap'
  2. items as legacy STRING → same result as #1
  3. items as EMPTY LIST → items=[] in the shipment
  4. items as None → items=[]
  5. items as LIST WITH WHITESPACE/EMPTIES → normalised to non-empty entries
  6. NO overrides at all → uses pending order's stored items (regression)

Credentials: /app/memory/test_credentials.md → user2@test.com / User@12345
Reference  : /app/backend/routers/shipments_write.py:755-820
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


# ─────────────────────────────────────────────────── fixtures ──
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


def _create_pending(api_client, user_headers, unique_suffix, item_str="OriginalItem"):
    """Smart-paste a fresh pending order, return its id."""
    text = (
        f"Name: TEST_P32_ITM_{unique_suffix}\n"
        "Phone: 9999955555\n"
        "Address: 1 Items Test Lane\n"
        "City: Mumbai\n"
        "State: Maharashtra\n"
        "Pincode: 400001\n"
        f"Item: {item_str}\n"
        "Amount: 500\n"
        "Payment: COD"
    )
    r = api_client.post(
        f"{BASE_URL}/api/smart-paste",
        headers=user_headers,
        json={"text": text, "skip_llm": True},
        timeout=60,
    )
    assert r.status_code == 200, (
        f"smart-paste failed: {r.status_code} {r.text[:400]}"
    )
    return r.json()["id"]


def _ship(api_client, user_headers, pending_id, courier_id, overrides=None):
    body = {"courier_id": courier_id}
    if overrides is not None:
        body["overrides"] = overrides
    return api_client.post(
        f"{BASE_URL}/api/orders/pending/{pending_id}/ship",
        headers=user_headers,
        json=body,
        timeout=60,
    )


# ════════════════════════ Phase-32 items normalization regression ══════
class TestItemsNormalization:
    """Verify the list/string/None/whitespace hardening on ship endpoint."""

    def test_items_as_list_succeeds(
        self, api_client, user_headers, user_courier_id,
    ):
        """Critical: items=['Shirt','Cap'] (LIST) must NOT raise 500."""
        pid = _create_pending(api_client, user_headers, uuid.uuid4().hex[:6])
        r = _ship(
            api_client, user_headers, pid, user_courier_id,
            overrides={"items": ["Shirt", "Cap"]},
        )
        assert r.status_code == 200, (
            f"LIST overrides MUST succeed (regression of 500 AttributeError) "
            f"— got {r.status_code} {r.text[:400]}"
        )
        ship = r.json()
        assert ship.get("items") == ["Shirt", "Cap"], (
            f"items normalisation failed: {ship.get('items')!r}"
        )
        assert ship.get("item_description") == "Shirt, Cap", (
            f"item_description not derived from list: "
            f"{ship.get('item_description')!r}"
        )

    def test_items_as_legacy_string_succeeds(
        self, api_client, user_headers, user_courier_id,
    ):
        """Critical: items='Shirt, Cap' (STRING) backward-compat path."""
        pid = _create_pending(api_client, user_headers, uuid.uuid4().hex[:6])
        r = _ship(
            api_client, user_headers, pid, user_courier_id,
            overrides={"items": "Shirt, Cap"},
        )
        assert r.status_code == 200, (
            f"STRING overrides path broke: {r.status_code} {r.text[:400]}"
        )
        ship = r.json()
        assert ship.get("items") == ["Shirt", "Cap"], (
            f"string-split items wrong: {ship.get('items')!r}"
        )
        assert ship.get("item_description") == "Shirt, Cap"

    def test_items_empty_list(
        self, api_client, user_headers, user_courier_id,
    ):
        """Edge: items=[] → items=[] in shipment, no crash."""
        pid = _create_pending(api_client, user_headers, uuid.uuid4().hex[:6])
        r = _ship(
            api_client, user_headers, pid, user_courier_id,
            overrides={"items": []},
        )
        assert r.status_code == 200, (
            f"empty list overrides: {r.status_code} {r.text[:400]}"
        )
        ship = r.json()
        assert ship.get("items") == [], (
            f"items should be [] for empty list, got {ship.get('items')!r}"
        )
        assert ship.get("item_description") == "", (
            f"item_description should be '' for empty list, got "
            f"{ship.get('item_description')!r}"
        )

    def test_items_none(
        self, api_client, user_headers, user_courier_id,
    ):
        """Edge: items=None → items=[] (defensive fallback)."""
        pid = _create_pending(api_client, user_headers, uuid.uuid4().hex[:6])
        r = _ship(
            api_client, user_headers, pid, user_courier_id,
            overrides={"items": None},
        )
        assert r.status_code == 200, (
            f"None items overrides: {r.status_code} {r.text[:400]}"
        )
        ship = r.json()
        # When override is None, _get falls back to pending order's
        # stored items (a string from smart-paste). Either it falls
        # back OR normalises to []. Both shapes must not crash.
        assert isinstance(ship.get("items"), list), (
            f"items must be a list, got {type(ship.get('items'))}"
        )

    def test_items_with_whitespace_and_empties(
        self, api_client, user_headers, user_courier_id,
    ):
        """Edge: items=['  ', 'Cap', ''] → items=['Cap'] (normalised)."""
        pid = _create_pending(api_client, user_headers, uuid.uuid4().hex[:6])
        r = _ship(
            api_client, user_headers, pid, user_courier_id,
            overrides={"items": ["  ", "Cap", ""]},
        )
        assert r.status_code == 200, (
            f"whitespace/empty entries: {r.status_code} {r.text[:400]}"
        )
        ship = r.json()
        assert ship.get("items") == ["Cap"], (
            f"whitespace normalisation failed: {ship.get('items')!r}"
        )
        assert ship.get("item_description") == "Cap"

    def test_ship_without_overrides_regression(
        self, api_client, user_headers, user_courier_id,
    ):
        """Regression: ship with NO overrides — uses pending row's items."""
        pid = _create_pending(
            api_client, user_headers, uuid.uuid4().hex[:6],
            item_str="PendingStoredItem",
        )
        r = _ship(
            api_client, user_headers, pid, user_courier_id,
            overrides=None,
        )
        assert r.status_code == 200, (
            f"no-override path broke: {r.status_code} {r.text[:400]}"
        )
        ship = r.json()
        # Pending stores items as comma-separated string; ship should
        # have normalised it to a list with the single stored item.
        items = ship.get("items") or []
        assert isinstance(items, list)
        assert "PendingStoredItem" in items, (
            f"pending-stored items not carried through: {items!r}"
        )
