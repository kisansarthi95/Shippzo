"""Phase F4.4 — Out for Delivery auto-detection + 2h Alert (backend).

Covers the six user-story asks:
  1. Parser: /test-parse recognises the OFD India Post SMS and extracts
     postman name + beat.
  2. First OFD ingest: status flips + attempt-history + counters populate.
  3. Second OFD ingest (same shipment, different postman): history array
     grows to 2, counter increments, anchor `out_for_delivery_at` is
     UNCHANGED, `ofd_alert_fired_at` stays null.
  4. GET /ofd-alerts?hours=0.001 lists the shipment with all fields.
  5. PUT /ofd-alerts/{id}/fired removes shipment from the alerts list.
  6. Subsequent Delivered SMS flips status + clears ofd_alert_fired_at.
"""
from __future__ import annotations

import os
import uuid
import pytest
import requests

BASE_URL = (
    os.environ.get("EXPO_BACKEND_URL")
    or os.environ.get("EXPO_PUBLIC_BACKEND_URL")
    or "https://logistics-hub-740.preview.emergentagent.com"
).rstrip("/")
API = BASE_URL + "/api"

ADMIN = {"email": "admin@test.com", "password": "Admin@12345"}

RUN_TAG = uuid.uuid4().hex[:6].upper()
# Distinct AWB (13-char India Post shape) — unique per test run.
TEST_AWB = f"EG{(int(uuid.uuid4().int) % 1000000000):09d}IN"


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------
@pytest.fixture(scope="module")
def admin_session():
    r = requests.post(f"{API}/auth/login", json=ADMIN, timeout=30)
    assert r.status_code == 200, f"Admin login failed: {r.status_code} {r.text}"
    token = r.json()["token"]
    s = requests.Session()
    s.headers.update({
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })
    rr = s.put(f"{API}/courier-sync/configs/india_post", json={"enabled": True}, timeout=15)
    assert rr.status_code == 200, rr.text
    return s


@pytest.fixture(scope="module")
def manual_courier(admin_session):
    payload = {"name": f"TEST_IP_F44_{RUN_TAG}", "manual_tracking": True}
    r = admin_session.post(f"{API}/couriers", json=payload, timeout=15)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(scope="module")
def shipment(admin_session, manual_courier):
    payload = {
        "tracking_id": TEST_AWB,
        "courier_id": manual_courier["id"],
        "courier_name": manual_courier["name"],
        "customer_name": f"TEST F44 Customer {RUN_TAG}",
        "payment_mode": "Prepaid",
        "amount": 100.0,
    }
    r = admin_session.post(f"{API}/shipments", json=payload, timeout=15)
    assert r.status_code in (200, 201), r.text
    ship = r.json()
    yield ship
    try:
        admin_session.delete(f"{API}/shipments/{ship['id']}", timeout=15)
    except Exception:
        pass


def _get_ship(sess, sid):
    r = sess.get(f"{API}/shipments/{sid}", timeout=15)
    assert r.status_code == 200, r.text
    return r.json()


# ----------------------------------------------------------------------
# 1. Parser preview
# ----------------------------------------------------------------------
class TestOfdParser:
    def test_1_parses_postman_and_beat(self, admin_session):
        body = {
            "sender": "VA-INPOST-G",
            "text": (
                "Item: EG350862505IN is out for delivery. "
                "Delivery will be attempted by - VAGHELA MIRAL PRADIPBHAI "
                "(BEAT_01) - on 2026-07-04 - IndiaPost"
            ),
        }
        r = admin_session.post(f"{API}/courier-sync/test-parse", json=body, timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("matched") is True, d
        assert d.get("canonical_status") == "Out for Delivery", d
        assert d.get("shipment_status") == "Out for Delivery", d
        assert d.get("tracking_id") == "EG350862505IN", d
        pm = d.get("postman") or {}
        assert pm.get("postman_name") == "VAGHELA MIRAL PRADIPBHAI", d
        assert pm.get("beat") == "BEAT_01", d
        assert d.get("event_date") == "2026-07-04", d


# ----------------------------------------------------------------------
# 2 + 3. Ingest — first & second OFD SMS
# ----------------------------------------------------------------------
class TestOfdIngest:
    _state: dict = {}

    def test_2_first_ofd_flips_status_and_seeds_history(self, admin_session, shipment):
        body = {
            "sender": "VA-INPOST-G",
            "text": (
                f"Item: {TEST_AWB} is out for delivery. "
                "Delivery will be attempted by - VAGHELA MIRAL PRADIPBHAI "
                "(BEAT_01) - on 2026-07-04 - IndiaPost"
            ),
            "device_id": f"test-f44-{RUN_TAG}",
        }
        r = admin_session.post(f"{API}/courier-sync/ingest", json=body, timeout=20)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("action") == "updated", d
        assert d.get("canonical") == "Out for Delivery", d
        assert d.get("new_status") == "Out for Delivery", d

        ship = _get_ship(admin_session, shipment["id"])
        assert ship.get("status") == "Out for Delivery", ship
        assert ship.get("out_for_delivery_at"), "out_for_delivery_at not stamped"
        assert ship.get("last_delivery_person") == "VAGHELA MIRAL PRADIPBHAI", ship
        assert ship.get("last_delivery_beat") == "BEAT_01", ship
        assert int(ship.get("delivery_attempt_count") or 0) == 1, ship
        hist = ship.get("out_for_delivery_history") or []
        assert isinstance(hist, list) and len(hist) == 1, ship
        entry = hist[0]
        for k in ("postman_name", "beat", "attempted_on", "received_at", "raw_phrase"):
            assert k in entry, f"missing key {k} in history entry: {entry}"
        assert entry["postman_name"] == "VAGHELA MIRAL PRADIPBHAI"
        assert entry["beat"] == "BEAT_01"

        TestOfdIngest._state["ofd_at_first"] = ship["out_for_delivery_at"]

    def test_3_second_ofd_appends_history_keeps_anchor(self, admin_session, shipment):
        body = {
            "sender": "VA-INPOST-G",
            "text": (
                f"Item: {TEST_AWB} is out for delivery. "
                "Delivery will be attempted by - DIVYA BHAGAT (BEAT_06) - "
                "on 2026-07-05 - IndiaPost"
            ),
            "device_id": f"test-f44-{RUN_TAG}",
        }
        r = admin_session.post(f"{API}/courier-sync/ingest", json=body, timeout=20)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("action") == "updated", d

        ship = _get_ship(admin_session, shipment["id"])
        assert ship.get("status") == "Out for Delivery", ship
        assert int(ship.get("delivery_attempt_count") or 0) == 2, ship
        assert ship.get("last_delivery_person") == "DIVYA BHAGAT", ship
        assert ship.get("last_delivery_beat") == "BEAT_06", ship
        # anchor unchanged
        assert ship.get("out_for_delivery_at") == TestOfdIngest._state["ofd_at_first"], (
            f"anchor changed! was {TestOfdIngest._state['ofd_at_first']!r}, "
            f"now {ship.get('out_for_delivery_at')!r}"
        )
        # alert flag not yet fired
        assert ship.get("ofd_alert_fired_at") in (None, ""), ship
        hist = ship.get("out_for_delivery_history") or []
        assert len(hist) == 2, ship
        assert hist[-1]["postman_name"] == "DIVYA BHAGAT"
        assert hist[-1]["beat"] == "BEAT_06"


# ----------------------------------------------------------------------
# 4 + 5. /ofd-alerts + /ofd-alerts/{id}/fired
# ----------------------------------------------------------------------
class TestOfdAlerts:
    def test_4_alerts_endpoint_lists_shipment(self, admin_session, shipment):
        # Tiny threshold so the just-created shipment qualifies.
        r = admin_session.get(f"{API}/courier-sync/ofd-alerts?hours=0.001", timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "alerts" in data and "threshold_hours" in data, data
        alerts = data["alerts"]
        match = next((a for a in alerts if a.get("shipment_id") == shipment["id"]), None)
        assert match is not None, f"Shipment {shipment['id']} not in alerts: {alerts}"
        for k in (
            "shipment_id", "tracking_id", "customer_name",
            "hours_elapsed", "delivery_person", "delivery_beat", "attempts",
        ):
            assert k in match, f"missing key {k} in alert entry: {match}"
        assert match["delivery_person"] == "DIVYA BHAGAT"
        assert match["delivery_beat"] == "BEAT_06"
        assert int(match["attempts"]) == 2
        assert match["tracking_id"] == TEST_AWB

    def test_5_fired_marks_shipment_and_filters_out(self, admin_session, shipment):
        r = admin_session.put(
            f"{API}/courier-sync/ofd-alerts/{shipment['id']}/fired",
            timeout=15,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("ok") is True
        assert body.get("shipment_id") == shipment["id"]
        assert body.get("fired_at")

        # Confirm on the shipment doc.
        ship = _get_ship(admin_session, shipment["id"])
        assert ship.get("ofd_alert_fired_at"), ship

        # Re-poll — should no longer be in alerts.
        r2 = admin_session.get(f"{API}/courier-sync/ofd-alerts?hours=0.001", timeout=15)
        assert r2.status_code == 200, r2.text
        alerts = r2.json().get("alerts", [])
        assert not any(a.get("shipment_id") == shipment["id"] for a in alerts), (
            f"Shipment still in alerts after fired: {alerts}"
        )

    def test_5b_fired_unknown_shipment_returns_404(self, admin_session):
        r = admin_session.put(
            f"{API}/courier-sync/ofd-alerts/ZZ_UNKNOWN_{RUN_TAG}/fired",
            timeout=15,
        )
        assert r.status_code == 404, r.text


# ----------------------------------------------------------------------
# 6. Delivered SMS after OFD
# ----------------------------------------------------------------------
class TestDeliveredAfterOfd:
    def test_6_delivered_clears_alert_flag(self, admin_session, shipment):
        body = {
            "sender": "VA-INPOST-G",
            "text": (
                f"Item: {TEST_AWB} has been Delivered on 2026-07-04 "
                "at BHARUCH GPO - IndiaPost"
            ),
            "device_id": f"test-f44-{RUN_TAG}",
        }
        r = admin_session.post(f"{API}/courier-sync/ingest", json=body, timeout=20)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("action") == "updated", d
        assert d.get("canonical") == "Delivered", d

        ship = _get_ship(admin_session, shipment["id"])
        assert ship.get("status") == "Delivered", ship
        assert ship.get("delivered_at"), ship
        assert "2026-07-04" in str(ship.get("delivered_at")), ship
        # Alert flag must be cleared per spec.
        assert ship.get("ofd_alert_fired_at") in (None, ""), (
            f"ofd_alert_fired_at not cleared after delivery: {ship.get('ofd_alert_fired_at')!r}"
        )
