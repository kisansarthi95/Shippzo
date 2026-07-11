"""Phase F8.0 — SMS/Notification Update System repair tests.

Covers the shared Shipment Update Engine reuse + new India Post
templates:

  1. Booking SMS  → booking_date stamped (date+time), ONLY if empty;
                    status → Shipped.
  2. Delivered "is Successful on DD-MM-YYYY HH:MM:SS" template →
     Delivered + delivered_at (with time) + confirmation_status
     "confirmed".
  3. "is Unsuccessful" → audit-only (no status change).
  4. OFD SMS → postman name, beat, attempt date, full original message
     in out_for_delivery_history.
  5. In Transit SMS → In Transit stage (Stage Routing parity).
  6. last_event / last_event_category stamped from SMS (engine parity
     with Delivery Import).
  7. Startup migration added the successful/unsuccessful rules + the
     In Transit stage mapping to existing courier configs.
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


def _mk_awb() -> str:
    return f"EG{(int(uuid.uuid4().int) % 1000000000):09d}IN"


@pytest.fixture(scope="module")
def admin_session():
    r = requests.post(f"{API}/auth/login", json=ADMIN, timeout=30)
    assert r.status_code == 200, f"Admin login failed: {r.status_code} {r.text}"
    s = requests.Session()
    s.headers.update({
        "Authorization": f"Bearer {r.json()['token']}",
        "Content-Type": "application/json",
    })
    return s


@pytest.fixture()
def shipment(admin_session):
    awb = _mk_awb()
    r = admin_session.post(f"{API}/shipments", json={
        "tracking_id":   awb,
        "courier_name":  "Indian post",
        "customer_name": f"F8 Test {RUN_TAG}",
        "customer_phone": "9999990001",
        "address_line1": "Test Street",
        "city": "Rajkot", "state": "Gujarat", "pincode": "360001",
        "payment_mode": "COD", "amount": 250.0,
    }, timeout=20)
    assert r.status_code in (200, 201), r.text
    ship = r.json()
    ship["_awb"] = awb
    yield ship
    try:
        admin_session.delete(f"{API}/shipments/{ship['id']}", timeout=15)
    except Exception:
        pass


def _ingest(sess, text, sender="VA-INPOST-G"):
    r = sess.post(f"{API}/courier-sync/ingest", json={
        "sender": sender, "title": sender, "text": text,
        "package": "com.google.android.apps.messaging",
        "device_id": f"f8-test-{RUN_TAG}",
    }, timeout=20)
    assert r.status_code == 200, f"ingest failed: {r.status_code} {r.text}"
    return r.json()


def _get(sess, sid):
    r = sess.get(f"{API}/shipments/{sid}", timeout=15)
    assert r.status_code == 200, r.text
    return r.json()


# ────────────────────────────────────────────────────────────────
# 1. Booking SMS
# ────────────────────────────────────────────────────────────────
class TestBookingSms:
    def test_booking_sets_booking_date_with_time(self, admin_session, shipment):
        awb = shipment["_awb"]
        d = _ingest(
            admin_session,
            f"Article No: {awb} (SP_INLAND_PARCEL) has been booked on "
            f"2026-07-10 14:53:40. - IndiaPost",
        )
        assert d["action"] == "updated", d
        assert d["canonical"] == "Booked", d
        ship = _get(admin_session, shipment["id"])
        assert ship.get("status") == "Shipped", ship.get("status")
        assert ship.get("booking_date") == "2026-07-10T14:53:40", (
            f"booking_date: {ship.get('booking_date')!r}"
        )
        # Engine parity — last_event fields stamped like Delivery Import.
        assert (ship.get("last_event") or "").find(awb) != -1
        assert ship.get("last_event_category"), "last_event_category empty"

    def test_booking_date_not_overwritten(self, admin_session, shipment):
        awb = shipment["_awb"]
        _ingest(
            admin_session,
            f"Article No: {awb} (SP_INLAND_PARCEL) has been booked on "
            f"2026-07-10 14:53:40. - IndiaPost",
        )
        # Second booking SMS with a DIFFERENT date must NOT overwrite.
        _ingest(
            admin_session,
            f"Article No: {awb} (SP_INLAND_PARCEL) has been booked on "
            f"2026-07-12 09:00:00. - IndiaPost",
        )
        ship = _get(admin_session, shipment["id"])
        assert ship.get("booking_date") == "2026-07-10T14:53:40", (
            f"booking_date overwritten: {ship.get('booking_date')!r}"
        )


# ────────────────────────────────────────────────────────────────
# 2 + 3. Delivered (Successful) / Unsuccessful templates
# ────────────────────────────────────────────────────────────────
class TestSuccessfulTemplate:
    def test_successful_delivers_with_datetime(self, admin_session, shipment):
        awb = shipment["_awb"]
        d = _ingest(
            admin_session,
            f"Delivery attempt for SP_INLAND_PARCEL No: {awb} is Successful "
            f"on 10-07-2026 20:42:19 - IndiaPost",
        )
        assert d["action"] == "updated", d
        assert d["canonical"] == "Delivered", d
        ship = _get(admin_session, shipment["id"])
        assert ship.get("status") == "Delivered", ship.get("status")
        # DD-MM-YYYY HH:MM:SS parsed day-first into full ISO datetime.
        assert ship.get("delivered_at") == "2026-07-10T20:42:19", (
            f"delivered_at: {ship.get('delivered_at')!r}"
        )
        # Delivery Confirmation auto-confirmed (engine parity).
        assert ship.get("confirmation_status") == "confirmed", (
            f"confirmation_status: {ship.get('confirmation_status')!r}"
        )

    def test_unsuccessful_does_not_deliver(self, admin_session, shipment):
        awb = shipment["_awb"]
        d = _ingest(
            admin_session,
            f"Delivery attempt for SP_INLAND_PARCEL No: {awb} is Unsuccessful "
            f"on 10-07-2026 18:05:00 - IndiaPost",
        )
        assert d["action"] == "ignored_intermediate_status", d
        assert d["canonical"] == "Undelivered", d
        ship = _get(admin_session, shipment["id"])
        assert ship.get("status") != "Delivered", ship.get("status")
        assert not ship.get("delivered_at"), ship.get("delivered_at")


# ────────────────────────────────────────────────────────────────
# 4. Out for Delivery details
# ────────────────────────────────────────────────────────────────
class TestOfdDetails:
    def test_ofd_full_details_saved(self, admin_session, shipment):
        awb = shipment["_awb"]
        sms = (
            f"Item: {awb} is out for delivery. Delivery will be attempted "
            f"by - SHANIGARAM DURGA PRASAD (BEAT_01) - on 2026-07-10 - IndiaPost"
        )
        d = _ingest(admin_session, sms)
        assert d["action"] == "updated", d
        ship = _get(admin_session, shipment["id"])
        assert ship.get("status") == "Out for Delivery", ship.get("status")
        assert ship.get("last_delivery_person") == "SHANIGARAM DURGA PRASAD"
        assert ship.get("last_delivery_beat") == "BEAT_01"
        assert ship.get("last_delivery_attempt_at") == "2026-07-10"
        hist = ship.get("out_for_delivery_history") or []
        assert len(hist) == 1, hist
        entry = hist[0]
        assert entry.get("postman_name") == "SHANIGARAM DURGA PRASAD"
        assert entry.get("beat") == "BEAT_01"
        assert entry.get("attempted_on") == "2026-07-10"
        # Phase F8.0 — the COMPLETE original OFD event is persisted.
        assert awb in (entry.get("raw_message") or ""), entry
        assert "SHANIGARAM" in (entry.get("raw_message") or ""), entry


# ────────────────────────────────────────────────────────────────
# 5. In Transit stage routing
# ────────────────────────────────────────────────────────────────
class TestInTransitStage:
    def test_dispatched_moves_to_in_transit(self, admin_session, shipment):
        awb = shipment["_awb"]
        d = _ingest(
            admin_session,
            f"Item: {awb} has been dispatched from RAJKOT NSH on 2026-07-10 - IndiaPost",
        )
        assert d["action"] == "updated", d
        assert d["canonical"] == "In Transit", d
        ship = _get(admin_session, shipment["id"])
        assert ship.get("status") == "In Transit", ship.get("status")
        assert ship.get("last_event_category") == "Item Dispatched", (
            ship.get("last_event_category")
        )


# ────────────────────────────────────────────────────────────────
# 7. Migration — scanning rules upgraded in place
# ────────────────────────────────────────────────────────────────
class TestRulesMigration:
    def test_india_post_courier_has_new_rules(self, admin_session):
        r = admin_session.get(f"{API}/couriers", timeout=20)
        assert r.status_code == 200, r.text
        couriers = r.json()
        ip = next(
            (c for c in couriers
             if "india" in (c.get("name") or "").lower()
             and "post" in (c.get("name") or "").lower()
             and c.get("auto_sync_enabled")),
            None,
        )
        assert ip is not None, "No enabled India Post courier found"
        rules = ip.get("auto_sync_status_rules") or []
        kws = [str(x.get("keyword") or "").lower() for x in rules]
        assert any("unsuccessful" in k for k in kws), kws
        assert any("successful" in k and "unsuccessful" not in k for k in kws), kws
        # Unsuccessful must be evaluated BEFORE the successful rule.
        first_unsucc = next(i for i, k in enumerate(kws) if "unsuccessful" in k)
        first_succ = next(
            i for i, k in enumerate(kws)
            if "successful" in k and "unsuccessful" not in k
        )
        assert first_unsucc < first_succ, (first_unsucc, first_succ)
        # In Transit rules now whitelisted with a stage.
        transit = [x for x in rules if x.get("canonical_status") == "In Transit"]
        assert transit, "no In Transit rules"
        for t in transit:
            assert t.get("shipment_status") == "In Transit", t
            assert t.get("whitelisted") is True, t
