"""
Phase F4.2 — Tracking-ID-Required gate on PUT /api/shipments/{id}.

Test matrix from review request:
  A. Reject moving beyond Pending without tracking (5 sub-cases → 422)
  B. Allow whitelisted safe transitions without tracking (4 sub-cases → 200)
  C. Adding tracking + status in ONE payload should succeed (3 sub-cases)
  D. Existing tracking → moves freely (4 sub-cases)
  E. Non-status updates should NOT be affected by this gate
  F. Regression — GET/POST/423 terminal-lock ordering

All tests run against the live preview backend using admin@test.com.
Test-created rows are auto-cancelled at the end of each test to keep DB clean.
"""
from __future__ import annotations

import os
import uuid
from typing import Any, Dict

import pytest
import requests


BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL") or os.environ.get(
    "EXPO_BACKEND_URL"
)
assert BASE_URL, "EXPO_PUBLIC_BACKEND_URL / EXPO_BACKEND_URL must be set"
BASE_URL = BASE_URL.rstrip("/")

ADMIN_EMAIL = "admin@test.com"
ADMIN_PWD   = "Admin@12345"

TRACKING_REQUIRED_PREFIX = "Tracking ID Required"


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------
@pytest.fixture(scope="session")
def admin_token() -> str:
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PWD},
        timeout=15,
    )
    assert r.status_code == 200, f"admin login failed: {r.status_code} {r.text}"
    tok = r.json().get("token")
    assert tok, "no token returned"
    return tok


@pytest.fixture(scope="session")
def api(admin_token: str) -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {"Content-Type": "application/json",
         "Authorization": f"Bearer {admin_token}"}
    )
    return s


@pytest.fixture(scope="session")
def courier_id(api: requests.Session) -> str:
    """Return an existing courier id (any) to attach to test shipments."""
    r = api.get(f"{BASE_URL}/api/couriers", timeout=15)
    assert r.status_code == 200, r.text
    couriers = r.json()
    assert isinstance(couriers, list) and couriers, "no couriers on admin"
    return couriers[0]["id"]


CREATED_IDS: list[str] = []


def _create_shipment(
    api: requests.Session,
    courier_id: str,
    *,
    tracking_id: str = "",
    manual_tracking_id: str = "",
    payment_mode: str = "Prepaid",
    amount: float = 100.0,
) -> Dict[str, Any]:
    """Create a shipment with the given tracking values. Returns full doc."""
    tag = uuid.uuid4().hex[:6]
    body: Dict[str, Any] = {
        "tracking_id": tracking_id,
        "courier_id": courier_id,
        "customer_name": f"TEST_F42_{tag}",
        "customer_phone": "9999999999",
        "address_line1": "1 Test Street",
        "city": "Ahmedabad",
        "state": "Gujarat",
        "pincode": "380001",
        "payment_mode": payment_mode,
        "amount": amount,
        "weight": "500 g",
    }
    if manual_tracking_id:
        body["manual_tracking_id"] = manual_tracking_id
    r = api.post(f"{BASE_URL}/api/shipments", json=body, timeout=20)
    assert r.status_code == 200, (
        f"create shipment failed: {r.status_code} {r.text}\n"
        f"body: {body}"
    )
    doc = r.json()
    assert doc.get("id"), f"no id on created doc: {doc}"
    CREATED_IDS.append(doc["id"])
    return doc


def _get_shipment(api: requests.Session, sid: str) -> Dict[str, Any]:
    r = api.get(f"{BASE_URL}/api/shipments/{sid}", timeout=15)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(scope="session", autouse=True)
def _cleanup(api: requests.Session):
    """After all tests, DELETE (which now flips to Cancelled) every test row."""
    yield
    for sid in CREATED_IDS:
        try:
            api.delete(f"{BASE_URL}/api/shipments/{sid}", timeout=10)
        except Exception:
            pass


# ==================================================================
# A. Reject moving beyond Pending without tracking
# ==================================================================
@pytest.mark.parametrize(
    "target_status",
    ["Processing", "Ready to Ship", "Shipped", "Delivered", "Out for Delivery"],
    ids=["A1_Processing", "A2_ReadyToShip", "A3_Shipped",
         "A4_Delivered", "A5_OutForDelivery"],
)
def test_A_reject_without_tracking(api, courier_id, target_status):
    doc = _create_shipment(api, courier_id, tracking_id="")
    sid = doc["id"]
    assert doc.get("status") == "Pending", f"initial status wrong: {doc.get('status')}"
    assert not (doc.get("tracking_id") or "").strip()
    assert not (doc.get("manual_tracking_id") or "").strip()

    r = api.put(
        f"{BASE_URL}/api/shipments/{sid}",
        json={"status": target_status},
        timeout=15,
    )
    assert r.status_code == 422, (
        f"expected 422 for status={target_status}, got "
        f"{r.status_code}: {r.text}"
    )
    detail = r.json().get("detail", "")
    assert detail.startswith(TRACKING_REQUIRED_PREFIX), (
        f"unexpected detail: {detail!r}"
    )

    # confirm status untouched
    fresh = _get_shipment(api, sid)
    assert fresh.get("status") == "Pending", (
        f"status leaked past guard: now {fresh.get('status')}"
    )


# ==================================================================
# B. Allowed transitions without tracking
# ==================================================================
def test_B1_pending_noop_allowed(api, courier_id):
    doc = _create_shipment(api, courier_id, tracking_id="")
    r = api.put(
        f"{BASE_URL}/api/shipments/{doc['id']}",
        json={"status": "Pending"},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    assert r.json().get("status") == "Pending"


def test_B2_cancelled_allowed_no_tracking(api, courier_id):
    doc = _create_shipment(api, courier_id, tracking_id="")
    r = api.put(
        f"{BASE_URL}/api/shipments/{doc['id']}",
        json={"status": "Cancelled"},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    assert r.json().get("status") == "Cancelled"


def test_B3_cancel_by_buyer_allowed_no_tracking(api, courier_id):
    doc = _create_shipment(api, courier_id, tracking_id="")
    r = api.put(
        f"{BASE_URL}/api/shipments/{doc['id']}",
        json={"status": "Cancel by buyer"},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    assert r.json().get("status") == "Cancel by buyer"


def test_B4_returned_allowed_no_tracking(api, courier_id):
    doc = _create_shipment(api, courier_id, tracking_id="")
    r = api.put(
        f"{BASE_URL}/api/shipments/{doc['id']}",
        json={"status": "Returned"},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    assert r.json().get("status") == "Returned"


# ==================================================================
# C. Adding tracking + status in the same payload
# ==================================================================
def test_C1_tracking_and_status_in_one_payload(api, courier_id):
    doc = _create_shipment(api, courier_id, tracking_id="")
    sid = doc["id"]
    r = api.put(
        f"{BASE_URL}/api/shipments/{sid}",
        json={"tracking_id": "TESTAWB1001", "status": "Processing"},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("status") == "Processing", body
    assert body.get("tracking_id") == "TESTAWB1001", body

    fresh = _get_shipment(api, sid)
    assert fresh.get("status") == "Processing"
    assert fresh.get("tracking_id") == "TESTAWB1001"


def test_C2_manual_tracking_and_status_in_one_payload(api, courier_id):
    doc = _create_shipment(api, courier_id, tracking_id="")
    sid = doc["id"]
    r = api.put(
        f"{BASE_URL}/api/shipments/{sid}",
        json={"manual_tracking_id": "MAN-AWB-2002", "status": "Processing"},
        timeout=15,
    )
    # ShipmentUpdate does NOT declare manual_tracking_id, so FastAPI's
    # Pydantic model will drop it silently. That means the gate should
    # still fire → this test documents ACTUAL behaviour. If ShipmentUpdate
    # is extended in future, adjust here.
    if r.status_code == 200:
        body = r.json()
        assert body.get("status") == "Processing", body
        fresh = _get_shipment(api, sid)
        assert fresh.get("status") == "Processing"
        assert (fresh.get("manual_tracking_id") or "") == "MAN-AWB-2002", fresh
    else:
        # Document unexpected 422 clearly
        pytest.fail(
            f"C2 failed: manual_tracking_id path returned "
            f"{r.status_code} {r.text} — Pydantic ShipmentUpdate likely "
            f"lacks the manual_tracking_id field."
        )


def test_C3_whitespace_tracking_id_should_be_rejected(api, courier_id):
    doc = _create_shipment(api, courier_id, tracking_id="")
    sid = doc["id"]
    r = api.put(
        f"{BASE_URL}/api/shipments/{sid}",
        json={"tracking_id": "   ", "status": "Processing"},
        timeout=15,
    )
    assert r.status_code == 422, (
        f"whitespace tracking should be rejected, got "
        f"{r.status_code}: {r.text}"
    )
    detail = r.json().get("detail", "")
    assert detail.startswith(TRACKING_REQUIRED_PREFIX), detail

    fresh = _get_shipment(api, sid)
    assert fresh.get("status") == "Pending"


# ==================================================================
# D. Existing tracking → moves freely
# ==================================================================
def test_D_full_progression_with_tracking(api, courier_id):
    doc = _create_shipment(api, courier_id, tracking_id="AWB-999")
    sid = doc["id"]

    for status in ["Processing", "Ready to Ship", "Shipped", "Delivered"]:
        r = api.put(
            f"{BASE_URL}/api/shipments/{sid}",
            json={"status": status},
            timeout=15,
        )
        assert r.status_code == 200, (
            f"transition to {status} failed: {r.status_code} {r.text}"
        )
        assert r.json().get("status") == status

    fresh = _get_shipment(api, sid)
    assert fresh.get("status") == "Delivered"
    assert fresh.get("delivered_at"), (
        f"delivered_at not stamped: {fresh.get('delivered_at')!r}"
    )


# ==================================================================
# E. Non-status updates should NOT trigger the gate
# ==================================================================
def test_E1_rename_customer_no_status(api, courier_id):
    doc = _create_shipment(api, courier_id, tracking_id="")
    sid = doc["id"]
    r = api.put(
        f"{BASE_URL}/api/shipments/{sid}",
        json={"customer_name": "TEST_Renamed"},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    fresh = _get_shipment(api, sid)
    assert fresh.get("customer_name") == "TEST_Renamed"
    assert fresh.get("status") == "Pending"


def test_E2_amount_update_no_status(api, courier_id):
    doc = _create_shipment(api, courier_id, tracking_id="")
    sid = doc["id"]
    r = api.put(
        f"{BASE_URL}/api/shipments/{sid}",
        json={"amount": 250},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    fresh = _get_shipment(api, sid)
    assert fresh.get("status") == "Pending"


def test_E3_address_update_no_status(api, courier_id):
    doc = _create_shipment(api, courier_id, tracking_id="")
    sid = doc["id"]
    r = api.put(
        f"{BASE_URL}/api/shipments/{sid}",
        json={"address_line1": "New addr 42"},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    fresh = _get_shipment(api, sid)
    assert fresh.get("address_line1") == "New addr 42"
    assert fresh.get("status") == "Pending"


# ==================================================================
# F. Regression — unrelated endpoints + 423 ordering
# ==================================================================
def test_F1_list_shipments(api):
    r = api.get(f"{BASE_URL}/api/shipments?limit=5", timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body, list), f"expected list, got {type(body)}"


def test_F2_post_default_pending(api, courier_id):
    doc = _create_shipment(api, courier_id, tracking_id="")
    assert doc.get("status") == "Pending"
    assert not (doc.get("tracking_id") or "").strip()


def test_F3_terminal_lock_beats_tracking_gate(api, courier_id):
    """A Cancelled shipment must return 423 (terminal-locked), NOT 422.
    The terminal-lock guard runs BEFORE the tracking guard."""
    doc = _create_shipment(api, courier_id, tracking_id="")
    sid = doc["id"]

    # Flip to Cancelled (backward transition — allowed without tracking).
    r = api.put(
        f"{BASE_URL}/api/shipments/{sid}",
        json={"status": "Cancelled"},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    assert r.json().get("status") == "Cancelled"

    # Now attempt to move it to Processing → should get 423, not 422.
    r2 = api.put(
        f"{BASE_URL}/api/shipments/{sid}",
        json={"status": "Processing"},
        timeout=15,
    )
    assert r2.status_code == 423, (
        f"expected 423 (terminal-locked), got {r2.status_code}: {r2.text}"
    )
    detail = r2.json().get("detail", "")
    assert TRACKING_REQUIRED_PREFIX not in detail, (
        f"terminal-locked shipment should NOT hit the tracking gate; "
        f"got detail={detail!r}"
    )
