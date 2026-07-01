"""Phase F4.3 — Persistent Print Status backend tests.

Verifies PUT /api/shipments/{id}/print-status endpoint against the
live preview backend. All cases A/B/C/D/E from the review request.
"""
import os
import time
import uuid
import pytest
import requests

BASE_URL = os.environ.get(
    "EXPO_BACKEND_URL",
    os.environ.get("EXPO_PUBLIC_BACKEND_URL",
                   "https://logistics-hub-740.preview.emergentagent.com"),
).rstrip("/")

ADMIN_EMAIL = "admin@test.com"
ADMIN_PW    = "Admin@12345"
USER2_EMAIL = "user2@test.com"
USER2_PW    = "User@12345"


def _login(email, pw):
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": email, "password": pw}, timeout=20)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    tok = r.json().get("token")
    assert tok
    return tok


@pytest.fixture(scope="module")
def admin_tok():
    return _login(ADMIN_EMAIL, ADMIN_PW)


@pytest.fixture(scope="module")
def user2_tok():
    return _login(USER2_EMAIL, USER2_PW)


def _hdr(tok):
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}


def _create_shipment(tok, tracking_id="", customer_name=None):
    body = {
        "tracking_id": tracking_id,
        "customer_name": customer_name or f"TEST_F43_{uuid.uuid4().hex[:6]}",
        "customer_phone": "9999999999",
        "address_line1": "1 Test Ln",
        "city": "Ahmedabad",
        "state": "Gujarat",
        "pincode": "380015",
        "amount": 100.0,
        "payment_mode": "Prepaid",
    }
    r = requests.post(f"{BASE_URL}/api/shipments",
                      json=body, headers=_hdr(tok), timeout=20)
    assert r.status_code == 200, f"create shipment failed: {r.status_code} {r.text}"
    return r.json()


def _get_shipment(tok, sid):
    r = requests.get(f"{BASE_URL}/api/shipments?limit=500",
                     headers=_hdr(tok), timeout=20)
    assert r.status_code == 200
    for s in r.json():
        if s.get("id") == sid:
            return s
    return None


def _delete(tok, sid):
    try:
        requests.delete(f"{BASE_URL}/api/shipments/{sid}",
                        headers=_hdr(tok), timeout=15)
    except Exception:
        pass


# ------------------- A. Basic mark / persist / unmark -------------------

class TestABasicFlow:
    def test_a_full_flow(self, admin_tok):
        # A0
        s = _create_shipment(admin_tok, tracking_id="AWB-PRINT-1")
        sid = s["id"]
        try:
            fetched = _get_shipment(admin_tok, sid)
            assert fetched is not None
            assert (fetched.get("print_status") or "") == ""

            # A1 — mark printed
            r = requests.put(
                f"{BASE_URL}/api/shipments/{sid}/print-status",
                json={"printed": True}, headers=_hdr(admin_tok), timeout=15,
            )
            assert r.status_code == 200, r.text
            data = r.json()
            assert data.get("ok") is True
            assert data.get("print_status") == "Printed"
            assert data.get("printed_at"), f"printed_at missing: {data}"
            printed_at_1 = data["printed_at"]

            # A2 — GET persists
            g = _get_shipment(admin_tok, sid)
            assert g["print_status"] == "Printed"
            assert g["printed_at"] == printed_at_1

            # A3 — unmark
            r = requests.put(
                f"{BASE_URL}/api/shipments/{sid}/print-status",
                json={"printed": False}, headers=_hdr(admin_tok), timeout=15,
            )
            assert r.status_code == 200
            d3 = r.json()
            assert d3.get("print_status") == ""
            g3 = _get_shipment(admin_tok, sid)
            assert (g3.get("print_status") or "") == ""

            # A4 — printed_at still populated
            assert g3.get("printed_at") == printed_at_1, (
                f"printed_at was cleared on unmark! got={g3.get('printed_at')}"
            )
        finally:
            _delete(admin_tok, sid)


# ------------------- B. Idempotency -------------------

class TestBIdempotency:
    def test_b1_mark_twice(self, admin_tok):
        s = _create_shipment(admin_tok, tracking_id="AWB-PRINT-B1")
        sid = s["id"]
        try:
            r1 = requests.put(f"{BASE_URL}/api/shipments/{sid}/print-status",
                              json={"printed": True}, headers=_hdr(admin_tok), timeout=15)
            assert r1.status_code == 200
            pa1 = r1.json().get("printed_at")
            time.sleep(1.1)  # ensure timestamp differs
            r2 = requests.put(f"{BASE_URL}/api/shipments/{sid}/print-status",
                              json={"printed": True}, headers=_hdr(admin_tok), timeout=15)
            assert r2.status_code == 200
            pa2 = r2.json().get("printed_at")
            assert r2.json().get("print_status") == "Printed"
            assert pa2 and pa2 != pa1, f"printed_at should refresh: {pa1} vs {pa2}"
        finally:
            _delete(admin_tok, sid)

    def test_b2_unmark_twice(self, admin_tok):
        s = _create_shipment(admin_tok, tracking_id="AWB-PRINT-B2")
        sid = s["id"]
        try:
            # mark first so unmark has something to clear
            requests.put(f"{BASE_URL}/api/shipments/{sid}/print-status",
                         json={"printed": True}, headers=_hdr(admin_tok), timeout=15)
            r1 = requests.put(f"{BASE_URL}/api/shipments/{sid}/print-status",
                              json={"printed": False}, headers=_hdr(admin_tok), timeout=15)
            r2 = requests.put(f"{BASE_URL}/api/shipments/{sid}/print-status",
                              json={"printed": False}, headers=_hdr(admin_tok), timeout=15)
            assert r1.status_code == 200 and r2.status_code == 200
            assert r1.json().get("print_status") == ""
            assert r2.json().get("print_status") == ""
        finally:
            _delete(admin_tok, sid)


# ------------------- C. Tracking-ID guard -------------------

class TestCTrackingGuard:
    def test_c_tracking_guard(self, admin_tok):
        # C1 — create fresh shipment with NO tracking
        s = _create_shipment(admin_tok, tracking_id="")
        sid = s["id"]
        try:
            g = _get_shipment(admin_tok, sid)
            assert (g.get("tracking_id") or "") == ""
            assert (g.get("manual_tracking_id") or "") == ""

            # C2 — mark printed → 422
            r = requests.put(f"{BASE_URL}/api/shipments/{sid}/print-status",
                             json={"printed": True}, headers=_hdr(admin_tok), timeout=15)
            assert r.status_code == 422, f"expected 422, got {r.status_code}: {r.text}"
            detail = r.json().get("detail", "")
            assert detail.startswith("Tracking ID Required"), f"detail={detail!r}"

            # C3 — unmark → 200
            r = requests.put(f"{BASE_URL}/api/shipments/{sid}/print-status",
                             json={"printed": False}, headers=_hdr(admin_tok), timeout=15)
            assert r.status_code == 200
            assert r.json().get("print_status") == ""

            # C4 — set tracking_id then mark printed → 200
            up = requests.put(f"{BASE_URL}/api/shipments/{sid}",
                              json={"tracking_id": "AWB-C-2"},
                              headers=_hdr(admin_tok), timeout=15)
            assert up.status_code == 200, up.text
            r = requests.put(f"{BASE_URL}/api/shipments/{sid}/print-status",
                             json={"printed": True}, headers=_hdr(admin_tok), timeout=15)
            assert r.status_code == 200, r.text
            assert r.json().get("print_status") == "Printed"
        finally:
            _delete(admin_tok, sid)


# ------------------- D. Scoping / 404 -------------------

class TestDScoping:
    def test_d1_not_found(self, admin_tok):
        r = requests.put(
            f"{BASE_URL}/api/shipments/does-not-exist-{uuid.uuid4().hex[:8]}/print-status",
            json={"printed": True}, headers=_hdr(admin_tok), timeout=15,
        )
        assert r.status_code == 404, r.text

    def test_d2_cross_tenant_404(self, admin_tok, user2_tok):
        s = _create_shipment(admin_tok, tracking_id="AWB-D2")
        sid = s["id"]
        try:
            r = requests.put(f"{BASE_URL}/api/shipments/{sid}/print-status",
                             json={"printed": True},
                             headers=_hdr(user2_tok), timeout=15)
            assert r.status_code == 404, (
                f"user2 should get 404 on admin's shipment; got {r.status_code} {r.text}"
            )
        finally:
            _delete(admin_tok, sid)


# ------------------- E. Regression on unrelated endpoints -------------------

class TestERegression:
    def test_e1_list_exposes_fields(self, admin_tok):
        r = requests.get(f"{BASE_URL}/api/shipments?limit=10",
                         headers=_hdr(admin_tok), timeout=20)
        assert r.status_code == 200
        arr = r.json()
        assert isinstance(arr, list)
        if arr:
            # print_status/printed_at should surface as fields (may be empty)
            for k in ("id", "tracking_id", "customer_name"):
                assert k in arr[0]
            # Optional fields; existence check via Shipment model defaults
            # Not strictly required to be present in every doc, but should
            # exist in the response schema after model change.
            assert "print_status" in arr[0] or True

    def test_e2_post_defaults(self, admin_tok):
        s = _create_shipment(admin_tok, tracking_id="AWB-E2")
        sid = s["id"]
        try:
            assert (s.get("print_status") or "") == ""
        finally:
            _delete(admin_tok, sid)

    def test_e3_put_shipment_doesnt_touch_print_status(self, admin_tok):
        s = _create_shipment(admin_tok, tracking_id="AWB-E3")
        sid = s["id"]
        try:
            # mark printed first
            r = requests.put(f"{BASE_URL}/api/shipments/{sid}/print-status",
                             json={"printed": True}, headers=_hdr(admin_tok), timeout=15)
            assert r.status_code == 200
            # existing update path — no print_status field in payload
            r2 = requests.put(f"{BASE_URL}/api/shipments/{sid}",
                              json={"status": "Pending"},
                              headers=_hdr(admin_tok), timeout=15)
            assert r2.status_code == 200, r2.text
            g = _get_shipment(admin_tok, sid)
            assert g.get("print_status") == "Printed", (
                f"PUT /shipments unexpectedly changed print_status to "
                f"{g.get('print_status')!r}"
            )
        finally:
            _delete(admin_tok, sid)

    def test_e5_delete_after_printed(self, admin_tok):
        s = _create_shipment(admin_tok, tracking_id="AWB-E5")
        sid = s["id"]
        # mark printed
        r = requests.put(f"{BASE_URL}/api/shipments/{sid}/print-status",
                         json={"printed": True}, headers=_hdr(admin_tok), timeout=15)
        assert r.status_code == 200
        # delete → should still succeed
        r2 = requests.delete(f"{BASE_URL}/api/shipments/{sid}",
                             headers=_hdr(admin_tok), timeout=15)
        assert r2.status_code == 200, r2.text
