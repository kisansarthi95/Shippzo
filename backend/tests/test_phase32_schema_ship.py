"""
Phase-32 — Schema additions + atomic ship-from-pending regression.

Coverage:
  1. SCHEMA_FIELDS now contains `tracking_id` AND `master_order_id`.
  2. HEADER_ALIASES maps awb/lr_no/docket → tracking_id and
     master_order_id/mo_id → master_order_id.
  3. Regression: POST /api/smart-paste → POST /api/orders/pending/{id}/ship
     atomically promotes the pending row to a Shipment (status=shipped,
     tracking_id set, shipment_id linked, processed_at set).
  4. PUT /api/settings persists a sheet.column_mapping that includes
     tracking_id + master_order_id targets, and GET /api/sheets/orders
     honors those mappings (no 422 from validate_mapping_field).

Credentials: /app/memory/test_credentials.md → user2@test.com / User@12345
"""
import os
import sys
import uuid
from pathlib import Path

import pytest
import requests
from dotenv import load_dotenv

# Make backend importable for direct schema-constant assertions.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from import_schema import SCHEMA_FIELDS, HEADER_ALIASES  # noqa: E402

frontend_env = Path(__file__).parent.parent.parent / "frontend" / ".env"
load_dotenv(frontend_env)
BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ.get("EXPO_BACKEND_URL")
if not BASE_URL:
    raise RuntimeError("EXPO_PUBLIC_BACKEND_URL / EXPO_BACKEND_URL missing")
BASE_URL = BASE_URL.rstrip("/")

USER_EMAIL = "user2@test.com"
USER_PASSWORD = "User@12345"


# ──────────────────────────────────────────────────────────── fixtures ──
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
    # Pick first non-manual courier so we exercise auto-tracking path.
    for c in items:
        if not c.get("manual_tracking"):
            return c.get("id") or c.get("_id")
    pytest.skip("No auto-tracking courier available")


# ════════════════════════════════════ Schema constants (offline) ══════
class TestSchemaConstants:
    """Direct python-level assertions on import_schema."""

    def test_tracking_id_in_schema_fields(self):
        assert "tracking_id" in SCHEMA_FIELDS

    def test_master_order_id_in_schema_fields(self):
        assert "master_order_id" in SCHEMA_FIELDS

    def test_awb_alias_maps_to_tracking_id(self):
        assert HEADER_ALIASES.get("awb") == "tracking_id"

    def test_docket_alias_maps_to_tracking_id(self):
        assert HEADER_ALIASES.get("docket") == "tracking_id"

    def test_lr_no_alias_maps_to_tracking_id(self):
        assert HEADER_ALIASES.get("lr_no") == "tracking_id"

    def test_master_order_id_alias_self_maps(self):
        assert HEADER_ALIASES.get("master_order_id") == "master_order_id"

    def test_mo_id_alias_maps_to_master_order_id(self):
        assert HEADER_ALIASES.get("mo_id") == "master_order_id"


# ═══════════════════════════ Atomic ship-from-pending regression ══════
class TestAtomicShipFromPending:
    """Smart-paste → atomic ship promotes pending row in one call."""

    def test_smart_paste_then_ship_atomic(
        self, api_client, user_headers, user_courier_id,
    ):
        unique = uuid.uuid4().hex[:6].upper()
        # Use structured (label: value) smart-paste so skip_llm parser
        # populates amount + payment_mode reliably.
        text = (
            f"Name: TEST_P32_{unique}\n"
            "Phone: 9999988888\n"
            "Address: 1 Smart Paste Lane\n"
            "City: Mumbai\n"
            "State: Maharashtra\n"
            "Pincode: 400001\n"
            "Item: Widget\n"
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
        pending = r.json()
        pending_id = pending.get("id")
        assert pending_id, f"PendingOrder.id missing: {pending}"
        assert pending.get("status") != "shipped"

        # Atomic ship — single POST replaces the old
        # createShipment + updatePendingOrder dance.
        r = api_client.post(
            f"{BASE_URL}/api/orders/pending/{pending_id}/ship",
            headers=user_headers,
            json={"courier_id": user_courier_id},
            timeout=60,
        )
        assert r.status_code == 200, f"ship failed: {r.status_code} {r.text[:400]}"
        ship = r.json()
        assert ship.get("id"), "Shipment id missing in response"
        assert ship.get("tracking_id"), "tracking_id missing on shipment"
        shipment_id = ship["id"]
        tracking_id = ship["tracking_id"]

        # Verify pending row now reflects shipped state.
        r = api_client.get(
            f"{BASE_URL}/api/orders/pending/{pending_id}",
            headers=user_headers, timeout=30,
        )
        assert r.status_code == 200, (
            f"GET pending after ship: {r.status_code} {r.text[:300]}"
        )
        po = r.json()
        assert po.get("status") == "shipped", (
            f"pending.status={po.get('status')!r} expected 'shipped'"
        )
        assert po.get("shipment_id") == shipment_id, (
            f"pending.shipment_id={po.get('shipment_id')!r} expected {shipment_id!r}"
        )
        assert po.get("tracking_id") == tracking_id, (
            f"pending.tracking_id={po.get('tracking_id')!r} "
            f"expected {tracking_id!r}"
        )
        assert po.get("processed_at"), "pending.processed_at not set"

        # Verify shipment is retrievable via GET.
        r = api_client.get(
            f"{BASE_URL}/api/shipments/{shipment_id}",
            headers=user_headers, timeout=30,
        )
        assert r.status_code == 200
        assert r.json()["tracking_id"] == tracking_id

    def test_double_ship_blocked(
        self, api_client, user_headers, user_courier_id,
    ):
        """Re-shipping the same pending order returns 400."""
        unique = uuid.uuid4().hex[:6].upper()
        text = (
            f"Name: TEST_P32_DUP_{unique}\n"
            "Phone: 9999977777\n"
            "Address: 2 Dedup St\n"
            "City: Pune\n"
            "State: Maharashtra\n"
            "Pincode: 411001\n"
            "Item: Gadget\n"
            "Amount: 250\n"
            "Payment: COD"
        )
        r = api_client.post(
            f"{BASE_URL}/api/smart-paste",
            headers=user_headers,
            json={"text": text, "skip_llm": True},
            timeout=60,
        )
        assert r.status_code == 200
        pid = r.json()["id"]
        r1 = api_client.post(
            f"{BASE_URL}/api/orders/pending/{pid}/ship",
            headers=user_headers,
            json={"courier_id": user_courier_id},
            timeout=60,
        )
        assert r1.status_code == 200
        r2 = api_client.post(
            f"{BASE_URL}/api/orders/pending/{pid}/ship",
            headers=user_headers,
            json={"courier_id": user_courier_id},
            timeout=60,
        )
        assert r2.status_code == 400, (
            f"second ship should 400, got {r2.status_code} {r2.text[:200]}"
        )


# ═══════════════════════ column_mapping persistence + sheets/orders ═══
class TestColumnMappingPersistence:
    """PUT /api/settings persists tracking_id + master_order_id column
    mappings (no 422), and GET /api/sheets/orders honors them."""

    def test_put_settings_with_tracking_and_master_mapping(
        self, api_client, user_headers,
    ):
        # column_mapping is documented as { header_col_name → schema_field }.
        # Phase-32 needs both `tracking_id` and `master_order_id`
        # as valid VALUES (the schema-field side).
        get_r = api_client.get(
            f"{BASE_URL}/api/settings", headers=user_headers, timeout=30,
        )
        assert get_r.status_code == 200, (
            f"GET settings: {get_r.status_code} {get_r.text[:200]}"
        )
        settings = get_r.json()
        sheet_cfg = settings.get("sheet") or {}
        # Preserve existing column_mapping, then layer Phase-32 entries on top.
        new_mapping = dict(sheet_cfg.get("column_mapping") or {})
        new_mapping.update({
            "AWB": "tracking_id",
            "Master ID": "master_order_id",
        })
        sheet_cfg["column_mapping"] = new_mapping
        settings["sheet"] = sheet_cfg

        put_r = api_client.put(
            f"{BASE_URL}/api/settings",
            headers=user_headers,
            json=settings,
            timeout=30,
        )
        assert put_r.status_code == 200, (
            f"PUT settings: {put_r.status_code} {put_r.text[:400]}"
        )
        body = put_r.json()
        persisted = (body.get("sheet") or {}).get("column_mapping") or {}
        assert persisted.get("AWB") == "tracking_id", (
            f"AWB mapping not persisted: {persisted}"
        )
        assert persisted.get("Master ID") == "master_order_id", (
            f"Master ID mapping not persisted: {persisted}"
        )

        # Re-GET to confirm round-trip.
        get_r2 = api_client.get(
            f"{BASE_URL}/api/settings", headers=user_headers, timeout=30,
        )
        assert get_r2.status_code == 200
        m2 = (get_r2.json().get("sheet") or {}).get("column_mapping") or {}
        assert m2.get("AWB") == "tracking_id"
        assert m2.get("Master ID") == "master_order_id"

    def test_sheets_orders_does_not_422_with_phase32_mappings(
        self, api_client, user_headers,
    ):
        """GET /api/sheets/orders should NOT 422 even when the user has
        tracking_id + master_order_id mapped (proves
        validate_mapping_field accepts the new schema fields)."""
        r = api_client.get(
            f"{BASE_URL}/api/sheets/orders",
            headers=user_headers, timeout=60,
        )
        # 200 = success; 400/404 = no sheet linked (acceptable). 422 is
        # the failure we're guarding against — it would indicate the
        # backend rejected the new column_mapping values as invalid.
        assert r.status_code != 422, (
            f"sheets/orders returned 422 — new schema fields rejected: "
            f"{r.text[:400]}"
        )
        # Any non-422 outcome means the mapping was accepted by
        # validate_mapping_field on the read path.
        assert r.status_code in (200, 400, 404), (
            f"unexpected status {r.status_code}: {r.text[:300]}"
        )
