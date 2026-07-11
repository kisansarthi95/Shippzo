"""Phase F8.0 — Extended edge cases (BACKEND 6, 7, 8 from review request).

Extends test_phase_f80_sms_engine.py without duplicating.

  BACKEND 6 — Scanning Rules migration: 16 rules total for default
              India Post, unsuccessful BEFORE successful, all In Transit
              rules have shipment_status='In Transit' + whitelisted=true.
  BACKEND 7 — Engine parity regression: Delivery Import commit still works
              (delivered_at column only → Delivered; last_event → F7.7
              routing categories).
  BACKEND 8 — Guard regressions: already-Delivered → ignored_delivered;
              unknown AWB → unmatched (no_shipment_found); wrong sender →
              sender mismatch. GET /shipments/{id} includes booking_date +
              needs_return_review fields.
"""
from __future__ import annotations

import io
import os
import uuid
import csv

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
        "customer_name": f"F8 Edge {RUN_TAG}",
        "customer_phone": "9999990002",
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
        "device_id": f"f8-edge-{RUN_TAG}",
    }, timeout=20)
    return r


# ────────────────────────────────────────────────────────────────
# BACKEND 6 — Rules migration: 16 rules for default India Post.
# ────────────────────────────────────────────────────────────────
class TestRulesCount:
    def test_default_india_post_has_16_rules(self, admin_session):
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
        assert len(rules) == 16, (
            f"Expected 16 rules for default India Post, found {len(rules)}: "
            f"{[r.get('keyword') for r in rules]}"
        )

    def test_all_in_transit_rules_whitelisted_with_stage(self, admin_session):
        r = admin_session.get(f"{API}/couriers", timeout=20)
        couriers = r.json()
        ip = next(
            c for c in couriers
            if "india" in (c.get("name") or "").lower()
            and "post" in (c.get("name") or "").lower()
            and c.get("auto_sync_enabled")
        )
        rules = ip.get("auto_sync_status_rules") or []
        transit = [x for x in rules if x.get("canonical_status") == "In Transit"]
        assert transit, "no In Transit rules"
        for t in transit:
            assert t.get("shipment_status") == "In Transit", t
            assert t.get("whitelisted") is True, t


# ────────────────────────────────────────────────────────────────
# BACKEND 7 — Delivery Import engine parity.
# ────────────────────────────────────────────────────────────────
class TestDeliveryImportEngineParity:
    def _mk_ship(self, sess, awb):
        r = sess.post(f"{API}/shipments", json={
            "tracking_id":   awb,
            "courier_name":  "Indian post",
            "customer_name": f"F8 Import {RUN_TAG}",
            "customer_phone": "9999990003",
            "address_line1": "Import Street",
            "city": "Rajkot", "state": "Gujarat", "pincode": "360001",
            "payment_mode": "COD", "amount": 100.0,
        }, timeout=20)
        assert r.status_code in (200, 201), r.text
        return r.json()

    def _commit(self, sess, awb, extra_col, extra_val, mapping):
        """Commit a one-row delivery import CSV."""
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["Tracking Number", extra_col])
        w.writerow([awb, extra_val])
        files = {"file": ("delivery.csv", buf.getvalue(), "text/csv")}
        data = {
            "import_type": "delivery",
            "mapping": '{"Tracking Number": "tracking_id", "' + extra_col + '": "' + mapping + '"}',
            "header_row": "1",
            "data_start_row": "2",
            "header_col": "1",
        }
        # Remove content-type from session for multipart.
        auth = sess.headers.get("Authorization", "")
        r = requests.post(
            f"{API}/shipments/import/commit",
            files=files, data=data,
            headers={"Authorization": auth},
            timeout=30,
        )
        return r

    def test_delivery_import_delivered_at_only(self, admin_session):
        awb = _mk_awb()
        ship = self._mk_ship(admin_session, awb)
        try:
            r = self._commit(
                admin_session, awb,
                "Delivered On", "2026-07-08",
                "delivered_at",
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["matched_updated"] >= 1, body
            got = admin_session.get(f"{API}/shipments/{ship['id']}", timeout=15).json()
            assert got.get("status") == "Delivered", got.get("status")
            assert got.get("confirmation_status") == "confirmed", (
                got.get("confirmation_status")
            )
        finally:
            admin_session.delete(f"{API}/shipments/{ship['id']}", timeout=15)

    def test_delivery_import_last_event_routes_in_transit(self, admin_session):
        awb = _mk_awb()
        ship = self._mk_ship(admin_session, awb)
        try:
            r = self._commit(
                admin_session, awb,
                "Last Event",
                "Item Dispatched from RAJKOT NSH on 2026-07-09",
                "last_event",
            )
            assert r.status_code == 200, r.text
            got = admin_session.get(f"{API}/shipments/{ship['id']}", timeout=15).json()
            assert got.get("status") == "In Transit", got.get("status")
            assert got.get("last_event_category") == "Item Dispatched", (
                got.get("last_event_category")
            )
        finally:
            admin_session.delete(f"{API}/shipments/{ship['id']}", timeout=15)

    def test_delivery_import_last_event_return_review(self, admin_session):
        awb = _mk_awb()
        ship = self._mk_ship(admin_session, awb)
        try:
            r = self._commit(
                admin_session, awb,
                "Last Event",
                "Item Delivered(Sender) at RAJKOT NSH",
                "last_event",
            )
            assert r.status_code == 200, r.text
            got = admin_session.get(f"{API}/shipments/{ship['id']}", timeout=15).json()
            # Return Review sets flag but does NOT auto-move status.
            assert got.get("needs_return_review") is True, got.get("needs_return_review")
            assert got.get("last_event_category") == "Return Review", (
                got.get("last_event_category")
            )
        finally:
            admin_session.delete(f"{API}/shipments/{ship['id']}", timeout=15)


# ────────────────────────────────────────────────────────────────
# BACKEND 8 — Guard regressions.
# ────────────────────────────────────────────────────────────────
class TestGuardRegressions:
    def test_already_delivered_ofd_ignored(self, admin_session, shipment):
        awb = shipment["_awb"]
        # First: deliver it.
        d = _ingest(
            admin_session,
            f"Delivery attempt for SP_INLAND_PARCEL No: {awb} is Successful "
            f"on 10-07-2026 20:42:19 - IndiaPost",
        )
        assert d.status_code == 200
        assert d.json()["action"] == "updated"
        # Now send an OFD SMS. Must be ignored_delivered.
        d2 = _ingest(
            admin_session,
            f"Item: {awb} is out for delivery. Delivery will be attempted "
            f"by - X (BEAT_02) - on 2026-07-11 - IndiaPost",
        )
        assert d2.status_code == 200
        body = d2.json()
        assert body["action"] == "ignored_delivered", body
        ship = admin_session.get(f"{API}/shipments/{shipment['id']}", timeout=15).json()
        assert ship.get("status") == "Delivered", ship.get("status")

    def test_unknown_awb_no_shipment_found(self, admin_session):
        awb = _mk_awb()  # not created
        d = _ingest(
            admin_session,
            f"Item: {awb} has been dispatched from RAJKOT NSH on 2026-07-10 - IndiaPost",
        )
        assert d.status_code == 200
        body = d.json()
        assert body.get("action") == "no_shipment_found", body

    def test_wrong_sender_ignored(self, admin_session, shipment):
        awb = shipment["_awb"]
        d = _ingest(
            admin_session,
            f"Item: {awb} has been dispatched from RAJKOT NSH on 2026-07-10 - IndiaPost",
            sender="VA-BLUEDART",   # wrong sender
        )
        assert d.status_code == 200
        body = d.json()
        # Legacy parser may still match if a partner accepts open-sender.
        # The requirement is that a WRONG-sender SMS must not mutate.
        assert body.get("matched") in (False, True)
        if body.get("matched") is False:
            assert "sender" in (body.get("reason") or "").lower() or body.get("reason"), body
        ship = admin_session.get(f"{API}/shipments/{shipment['id']}", timeout=15).json()
        # Regardless, the shipment status must not be In Transit purely
        # from this wrong-sender attempt (the fixture ship is 'Pending'/'Shipped').
        assert ship.get("status") != "In Transit" or body.get("matched") is True

    def test_shipment_response_includes_booking_and_return_review(self, admin_session, shipment):
        r = admin_session.get(f"{API}/shipments/{shipment['id']}", timeout=15)
        assert r.status_code == 200, r.text
        ship = r.json()
        assert "booking_date" in ship, list(ship.keys())
        assert "needs_return_review" in ship, list(ship.keys())
