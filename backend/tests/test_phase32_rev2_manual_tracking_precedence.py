"""
Phase-32 rev-2 — Manual / Scan tracking-ID precedence fix on
POST /api/orders/pending/{order_id}/ship.

Bug RCA:
    ship_pending_order ignored a non-empty `manual_tracking_id`
    whenever the resolved courier was AUTO-mode (manual_tracking=False).
    Operators scanning physical AWB stickers into the form saw their
    scan REPLACED by a freshly minted series_prefix + next_number, and
    the courier's `next_number` counter was bumped each time. The fix
    flips precedence: if the operator supplies a non-empty
    manual_tracking_id, it wins regardless of the courier flag, and
    the counter is NOT incremented.

Coverage (4 cases on /api/orders/pending/{id}/ship):
  1. AUTO-mode courier + manual_tracking_id='SCAN9999' →
     response.tracking_id == 'SCAN9999', courier next_number unchanged.
  2. MANUAL-mode courier + manual_tracking_id='SCAN9999' →
     tracking_id == 'SCAN9999', next_number unchanged (regression).
  3. AUTO-mode courier + manual_tracking_id='' (empty) →
     auto-generated tracking_id (series_prefix + zero-padded number),
     next_number incremented by 1 (regression).
  4. MANUAL-mode courier + manual_tracking_id missing/None →
     HTTP 400 with 'uses manual tracking' detail (regression).

Reference: /app/backend/routers/shipments_write.py lines 826-880.
Credentials: /app/memory/test_credentials.md → user2@test.com / User@12345
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


def _list_couriers(api_client, user_headers):
    r = api_client.get(
        f"{BASE_URL}/api/couriers", headers=user_headers, timeout=20,
    )
    assert r.status_code == 200, f"Couriers fetch failed: {r.status_code}"
    data = r.json()
    return data if isinstance(data, list) else (
        data.get("couriers") or data.get("items") or []
    )


def _get_courier(api_client, user_headers, courier_id):
    """Fetch single courier doc (incl. next_number) post-ship."""
    items = _list_couriers(api_client, user_headers)
    for c in items:
        if (c.get("id") or c.get("_id")) == courier_id:
            return c
    return None


def _ensure_auto_courier(api_client, user_headers):
    """Find or create an auto-mode courier."""
    for c in _list_couriers(api_client, user_headers):
        if not c.get("manual_tracking"):
            return c
    unique = uuid.uuid4().hex[:4].upper()
    payload = {
        "name": f"TEST_AUTO_{unique}",
        "series_prefix": f"AUTO{unique}",
        "next_number": 1,
        "number_padding": 4,
        "manual_tracking": False,
    }
    r = api_client.post(
        f"{BASE_URL}/api/couriers", headers=user_headers,
        json=payload, timeout=20,
    )
    assert r.status_code in (200, 201), (
        f"Could not create auto courier: {r.status_code} {r.text[:300]}"
    )
    return r.json()


def _ensure_manual_courier(api_client, user_headers):
    """Find or create a manual-mode courier. On plans capped at 1
    courier (Silver), we temporarily flip the existing courier's
    manual_tracking flag to True — caller is responsible for restore.
    """
    for c in _list_couriers(api_client, user_headers):
        if c.get("manual_tracking"):
            return c
    # Try create first
    unique = uuid.uuid4().hex[:4].upper()
    payload = {
        "name": f"TEST_MANUAL_{unique}",
        "series_prefix": "",
        "manual_tracking": True,
    }
    r = api_client.post(
        f"{BASE_URL}/api/couriers", headers=user_headers,
        json=payload, timeout=20,
    )
    if r.status_code in (200, 201):
        return r.json()
    # Plan-limit hit — flip the auto courier to manual instead.
    auto = _ensure_auto_courier(api_client, user_headers)
    cid = auto.get("id") or auto.get("_id")
    pr = api_client.put(
        f"{BASE_URL}/api/couriers/{cid}",
        headers=user_headers,
        json={"manual_tracking": True},
        timeout=20,
    )
    assert pr.status_code in (200, 201), (
        f"Could not flip courier to manual: {pr.status_code} {pr.text[:300]}"
    )
    return _get_courier(api_client, user_headers, cid)


def _restore_auto(api_client, user_headers, courier_id):
    """Best-effort restore: flip back to auto-mode after manual tests."""
    try:
        api_client.put(
            f"{BASE_URL}/api/couriers/{courier_id}",
            headers=user_headers,
            json={"manual_tracking": False},
            timeout=20,
        )
    except Exception:
        pass


def _create_pending(api_client, user_headers, suffix):
    text = (
        f"Name: TEST_P32R2_{suffix}\n"
        "Phone: 9999955555\n"
        "Address: 7 Manual Scan Lane\n"
        "City: Mumbai\n"
        "State: Maharashtra\n"
        "Pincode: 400001\n"
        "Item: ScanTest\n"
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


def _ship(api_client, user_headers, pid, courier_id, manual_tracking_id=None):
    body = {"courier_id": courier_id}
    if manual_tracking_id is not None:
        body["manual_tracking_id"] = manual_tracking_id
    return api_client.post(
        f"{BASE_URL}/api/orders/pending/{pid}/ship",
        headers=user_headers,
        json=body,
        timeout=60,
    )


# ════════════════════════════ Phase-32 rev-2 precedence fix ══════════
class TestManualTrackingIdPrecedence:
    """Non-empty manual_tracking_id ALWAYS wins, regardless of courier."""

    def test_auto_courier_with_manual_id_uses_manual_no_counter_bump(
        self, api_client, user_headers,
    ):
        """CRITICAL FIX: AUTO-mode courier + scanned AWB →
        tracking_id = the scanned value, NOT a fresh sequential one.
        Counter MUST NOT be incremented.
        """
        courier = _ensure_auto_courier(api_client, user_headers)
        courier_id = courier.get("id") or courier.get("_id")
        before_num = int(courier.get("next_number") or 1)

        pid = _create_pending(
            api_client, user_headers, uuid.uuid4().hex[:6],
        )
        scan_awb = f"SCAN{uuid.uuid4().hex[:6].upper()}"
        r = _ship(api_client, user_headers, pid, courier_id, scan_awb)
        assert r.status_code == 200, (
            f"AUTO+manual_id: expected 200, got {r.status_code} "
            f"{r.text[:300]}"
        )
        ship = r.json()
        assert ship.get("tracking_id") == scan_awb, (
            f"tracking_id={ship.get('tracking_id')!r} MUST equal scanned "
            f"AWB {scan_awb!r} (AUTO-mode courier must respect manual_id)"
        )

        # Counter must NOT have moved.
        after = _get_courier(api_client, user_headers, courier_id)
        after_num = int(after.get("next_number") or 0)
        assert after_num == before_num, (
            f"next_number bumped despite manual override: "
            f"before={before_num} after={after_num}"
        )

    def test_manual_courier_with_manual_id_uses_manual(
        self, api_client, user_headers,
    ):
        """REGRESSION: MANUAL-mode + manual_id → tracking_id=manual_id,
        next_number unchanged (already correct before fix).
        """
        courier = _ensure_manual_courier(api_client, user_headers)
        courier_id = courier.get("id") or courier.get("_id")
        before_num = int(courier.get("next_number") or 0)

        try:
            pid = _create_pending(
                api_client, user_headers, uuid.uuid4().hex[:6],
            )
            scan_awb = f"MAN{uuid.uuid4().hex[:6].upper()}"
            r = _ship(api_client, user_headers, pid, courier_id, scan_awb)
            assert r.status_code == 200, (
                f"MANUAL+manual_id: {r.status_code} {r.text[:300]}"
            )
            ship = r.json()
            assert ship.get("tracking_id") == scan_awb, (
                f"tracking_id={ship.get('tracking_id')!r} expected {scan_awb!r}"
            )

            after = _get_courier(api_client, user_headers, courier_id)
            after_num = int(after.get("next_number") or 0)
            assert after_num == before_num, (
                f"manual-mode next_number bumped unexpectedly: "
                f"before={before_num} after={after_num}"
            )
        finally:
            _restore_auto(api_client, user_headers, courier_id)

    def test_auto_courier_empty_manual_id_autogenerates_and_bumps(
        self, api_client, user_headers,
    ):
        """REGRESSION: AUTO-mode + empty/missing manual_id →
        auto-generated tracking (series_prefix + zero-padded next_num),
        counter bumped by 1.
        """
        courier = _ensure_auto_courier(api_client, user_headers)
        courier_id = courier.get("id") or courier.get("_id")
        # Re-fetch to get latest next_number (other tests may have run).
        latest = _get_courier(api_client, user_headers, courier_id)
        before_num = int(latest.get("next_number") or 1)
        padding = int(latest.get("number_padding") or 4)
        prefix = latest.get("series_prefix") or ""
        expected_tracking = f"{prefix}{str(before_num).zfill(padding)}"

        pid = _create_pending(
            api_client, user_headers, uuid.uuid4().hex[:6],
        )
        # Test BOTH: empty string AND missing key. Use empty string here.
        r = _ship(api_client, user_headers, pid, courier_id, "")
        assert r.status_code == 200, (
            f"AUTO+empty manual_id: {r.status_code} {r.text[:300]}"
        )
        ship = r.json()
        assert ship.get("tracking_id") == expected_tracking, (
            f"tracking_id={ship.get('tracking_id')!r} expected "
            f"{expected_tracking!r} (prefix+padded next_num)"
        )

        after = _get_courier(api_client, user_headers, courier_id)
        after_num = int(after.get("next_number") or 0)
        assert after_num == before_num + 1, (
            f"next_number not bumped: before={before_num} after={after_num} "
            f"(expected {before_num + 1})"
        )

    def test_manual_courier_no_manual_id_saves_empty(
        self, api_client, user_headers,
    ):
        """Phase-35 (2026-06) — CONTRACT CHANGE.
        MANUAL-mode + no manual_id → HTTP 200 with `tracking_id=""`.
        Operators (esp. India Post users) often save the shipment first,
        then visit the post-office counter later to get the AWB. Forcing
        tracking-ID at save broke that real-world workflow. The shipment
        now persists with an empty tracking_id; user fills it in via
        Edit when they have the receipt sticker.

        Previously this asserted 400 — that guard was the bug.
        """
        courier = _ensure_manual_courier(api_client, user_headers)
        courier_id = courier.get("id") or courier.get("_id")

        try:
            pid = _create_pending(
                api_client, user_headers, uuid.uuid4().hex[:6],
            )
            r = _ship(api_client, user_headers, pid, courier_id, None)
            assert r.status_code == 200, (
                f"MANUAL+no manual_id: expected 200, got {r.status_code} "
                f"{r.text[:300]}"
            )
            body = r.json()
            assert body.get("tracking_id", "<missing>") == "", (
                f"expected empty tracking_id, got {body.get('tracking_id')!r}"
            )
        finally:
            _restore_auto(api_client, user_headers, courier_id)
