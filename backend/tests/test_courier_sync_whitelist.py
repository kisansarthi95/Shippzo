"""Tests for the new Courier Sync Status Whitelist + Structured Logging feature.

Verifies:
  A. Parser-level (test-parse): intermediate canonicals return empty shipment_status.
  B. Ingest-level guard: only Booked/Delivered mutate shipment.status; intermediate
     events are written to audit with action='ignored_intermediate_status'.
  C. Audit log contains expected canonical_status + action rows.
  D. Structured logs in backend.err.log carry per-event correlation id and step markers.
"""
from __future__ import annotations

import os
import re
import time
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

# A unique AWB per test run to avoid collisions with prior runs.
RUN_TAG = uuid.uuid4().hex[:6].upper()
TEST_AWB = f"EG{(int(uuid.uuid4().int) % 1000000000):09d}IN"

BACKEND_LOG = "/var/log/supervisor/backend.err.log"


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
    # Ensure india_post partner is enabled.
    rr = s.put(f"{API}/courier-sync/configs/india_post", json={"enabled": True}, timeout=15)
    assert rr.status_code == 200, rr.text
    return s


@pytest.fixture(scope="module")
def manual_courier(admin_session):
    payload = {
        "name": f"TEST_IndiaPost_WL_{RUN_TAG}",
        "manual_tracking": True,
    }
    r = admin_session.post(f"{API}/couriers", json=payload, timeout=15)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(scope="module")
def test_shipment(admin_session, manual_courier):
    payload = {
        "tracking_id": TEST_AWB,
        "courier_id": manual_courier["id"],
        "courier_name": manual_courier["name"],
        "customer_name": f"TEST WL Customer {RUN_TAG}",
        "payment_mode": "Prepaid",
        "amount": 100.0,
    }
    r = admin_session.post(f"{API}/shipments", json=payload, timeout=15)
    assert r.status_code in (200, 201), r.text
    ship = r.json()
    yield ship
    # Teardown — best-effort delete
    try:
        admin_session.delete(f"{API}/shipments/{ship['id']}", timeout=15)
    except Exception:
        pass


def _get_shipment(sess, sid):
    r = sess.get(f"{API}/shipments/{sid}", timeout=15)
    assert r.status_code == 200, r.text
    return r.json()


# ----------------------------------------------------------------------
# Section A — Parser via /test-parse (no DB)
# ----------------------------------------------------------------------
class TestParserWhitelist:
    PARSE_CASES = [
        ("A1", "VA-INPOST-G",
         "Item: EG350860840IN has been Booked at PUNE GPO on 2026-06-30 - IndiaPost",
         True, "Booked", "Shipped"),
        ("A2", "VA-INPOST-G",
         "Item: EG350860840IN has been Delivered on 2026-06-30 at PUNE GPO - IndiaPost",
         True, "Delivered", "Delivered"),
        ("A3", "VA-INPOST-G",
         "Item: EG350860840IN is out for delivery. Delivery will be attempted by - JOHN DOE (BEAT_03) on 2026-06-30 - IndiaPost",
         True, "Out for Delivery", "Out for Delivery"),
        ("A4", "VA-INPOST-G",
         "Item: EG350860840IN in transit at DELHI - IndiaPost",
         True, "In Transit", ""),
        ("A5", "VA-INPOST-G",
         "Item: EG350860840IN could not be delivered. - IndiaPost",
         True, "Undelivered", ""),
        ("A6", "VA-INPOST-G",
         "Item: EG350860840IN has been returned to sender - IndiaPost",
         True, "RTO", ""),
    ]

    @pytest.mark.parametrize("tag,sender,text,matched,canonical,ship_status", PARSE_CASES)
    def test_parse_case(self, admin_session, tag, sender, text, matched, canonical, ship_status):
        r = admin_session.post(
            f"{API}/courier-sync/test-parse",
            json={"sender": sender, "text": text},
            timeout=15,
        )
        assert r.status_code == 200, f"{tag}: {r.status_code} {r.text}"
        d = r.json()
        assert d.get("matched") is matched, f"{tag}: matched mismatch — got {d}"
        assert d.get("canonical_status") == canonical, f"{tag}: canonical mismatch — got {d}"
        assert d.get("shipment_status") == ship_status, f"{tag}: ship_status mismatch — got {d}"

    def test_A7_wrong_sender(self, admin_session):
        r = admin_session.post(
            f"{API}/courier-sync/test-parse",
            json={"sender": "RANDOM-XYZ",
                  "text": "Item: EG350860840IN has been Delivered - IndiaPost"},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("matched") is False, d
        assert d.get("reason") == "sender_not_india_post", d


# ----------------------------------------------------------------------
# Section B + C — Ingest end-to-end + audit log
# ----------------------------------------------------------------------
class TestIngestWhitelist:
    # Capture ingest results so they can be cross-checked against
    # the events endpoint and the backend log.
    _state: dict = {"ingest_results": {}, "initial_status": None}

    def _ingest(self, sess, text):
        body = {
            "sender": "VA-INPOST-G",
            "text": text,
            "device_id": f"test-wl-{RUN_TAG}",
        }
        r = sess.post(f"{API}/courier-sync/ingest", json=body, timeout=20)
        assert r.status_code == 200, f"ingest failed: {r.status_code} {r.text}"
        return r.json()

    def test_B0_capture_initial_status(self, admin_session, test_shipment):
        ship = _get_shipment(admin_session, test_shipment["id"])
        initial = ship.get("status") or ""
        assert initial != "Delivered", f"Precondition: shipment must not start Delivered, got {initial!r}"
        TestIngestWhitelist._state["initial_status"] = initial

    def test_B1_in_transit_ignored(self, admin_session, test_shipment):
        d = self._ingest(
            admin_session,
            f"Item: {TEST_AWB} in transit at DELHI - IndiaPost",
        )
        TestIngestWhitelist._state["ingest_results"]["B1"] = d
        assert d.get("action") == "ignored_intermediate_status", f"B1: {d}"
        assert d.get("canonical") == "In Transit", f"B1 canonical: {d}"
        ship = _get_shipment(admin_session, test_shipment["id"])
        assert ship.get("status") == TestIngestWhitelist._state["initial_status"], (
            f"B1: shipment status changed unexpectedly. Initial="
            f"{TestIngestWhitelist._state['initial_status']!r}, now={ship.get('status')!r}"
        )

    def test_B2_out_for_delivery_updates(self, admin_session, test_shipment):
        """Phase F4.4 — OFD is now whitelisted and MUST update the
        shipment status (was previously ignored_intermediate_status)."""
        d = self._ingest(
            admin_session,
            f"Item: {TEST_AWB} is out for delivery by - X (BEAT_01) - IndiaPost",
        )
        TestIngestWhitelist._state["ingest_results"]["B2"] = d
        assert d.get("action") == "updated", f"B2: {d}"
        assert d.get("canonical") == "Out for Delivery", f"B2 canonical: {d}"
        assert d.get("new_status") == "Out for Delivery", f"B2 new_status: {d}"
        ship = _get_shipment(admin_session, test_shipment["id"])
        assert ship.get("status") == "Out for Delivery", (
            f"B2: shipment status must be Out for Delivery, got {ship.get('status')!r}"
        )
        assert ship.get("out_for_delivery_at"), "B2: out_for_delivery_at not stamped"

    def test_B3_booked_updates(self, admin_session, test_shipment):
        d = self._ingest(
            admin_session,
            f"Item: {TEST_AWB} has been Booked at PUNE - IndiaPost",
        )
        TestIngestWhitelist._state["ingest_results"]["B3"] = d
        assert d.get("action") == "updated", f"B3: {d}"
        assert d.get("canonical") == "Booked", f"B3 canonical: {d}"
        assert d.get("new_status") == "Shipped", f"B3 new_status: {d}"
        ship = _get_shipment(admin_session, test_shipment["id"])
        assert ship.get("status") == "Shipped", f"B3 ship status: {ship.get('status')!r}"

    def test_B4_delivered_updates(self, admin_session, test_shipment):
        d = self._ingest(
            admin_session,
            f"Item: {TEST_AWB} has been Delivered on 2026-06-30 - IndiaPost",
        )
        TestIngestWhitelist._state["ingest_results"]["B4"] = d
        assert d.get("action") == "updated", f"B4: {d}"
        assert d.get("canonical") == "Delivered", f"B4 canonical: {d}"
        ship = _get_shipment(admin_session, test_shipment["id"])
        assert ship.get("status") == "Delivered", f"B4 ship status: {ship.get('status')!r}"
        assert ship.get("delivered_at"), "B4: delivered_at not populated"

    def test_B5_post_delivered_in_transit_ignored(self, admin_session, test_shipment):
        d = self._ingest(
            admin_session,
            f"Item: {TEST_AWB} in transit at MUMBAI - IndiaPost",
        )
        TestIngestWhitelist._state["ingest_results"]["B5"] = d
        # Either ignored_delivered (preferred) or ignored_intermediate_status acceptable.
        assert d.get("action") in ("ignored_delivered", "ignored_intermediate_status"), (
            f"B5 action: {d}"
        )
        ship = _get_shipment(admin_session, test_shipment["id"])
        assert ship.get("status") == "Delivered", f"B5 ship status: {ship.get('status')!r}"

    # --- Section C: audit log ---
    def test_C_audit_events_recorded(self, admin_session, test_shipment):
        # Pull last 200 events for this user; filter by shipment_id.
        r = admin_session.get(f"{API}/courier-sync/events?limit=200", timeout=15)
        assert r.status_code == 200, r.text
        all_events = r.json().get("events", [])
        ship_events = [e for e in all_events if e.get("shipment_id") == test_shipment["id"]]

        expected = {
            "B1": ("In Transit", "ignored_intermediate_status"),
            "B2": ("Out for Delivery", "updated"),
            "B3": ("Booked", "updated"),
            "B4": ("Delivered", "updated"),
        }
        for tag, (canon, action) in expected.items():
            evt_id = TestIngestWhitelist._state["ingest_results"][tag].get("event_id")
            assert evt_id, f"{tag}: no event_id in ingest response"
            matching = [e for e in ship_events if e.get("id") == evt_id]
            assert matching, f"{tag}: event_id {evt_id} not found in audit log"
            e = matching[0]
            assert e.get("canonical_status") == canon, f"{tag} canonical mismatch in audit: {e}"
            assert e.get("action") == action, f"{tag} action mismatch in audit: {e}"

        # B5 — must be either ignored_delivered or ignored_intermediate_status.
        b5_evt = TestIngestWhitelist._state["ingest_results"]["B5"].get("event_id")
        b5_match = next((e for e in ship_events if e.get("id") == b5_evt), None)
        assert b5_match is not None, f"B5 audit event not found"
        assert b5_match.get("action") in ("ignored_delivered", "ignored_intermediate_status"), (
            f"B5 audit action: {b5_match}"
        )


# ----------------------------------------------------------------------
# Section D — Structured logging spot-check
# ----------------------------------------------------------------------
class TestStructuredLogs:
    def _read_log_tail(self, n_bytes=400_000):
        if not os.path.exists(BACKEND_LOG):
            pytest.skip(f"{BACKEND_LOG} not present")
        with open(BACKEND_LOG, "rb") as f:
            try:
                f.seek(-n_bytes, os.SEEK_END)
            except OSError:
                f.seek(0)
            return f.read().decode("utf-8", errors="replace")

    def test_D1_b3_pipeline_steps(self, admin_session):
        b3 = TestIngestWhitelist._state["ingest_results"].get("B3")
        assert b3, "B3 must run first"
        event_id = b3["event_id"]
        prefix = event_id[:8]
        # Give logs a moment to flush.
        time.sleep(1.0)
        log = self._read_log_tail()
        # Lines for this evt prefix:
        lines = [ln for ln in log.splitlines() if f"evt={prefix}" in ln]
        assert lines, f"No log lines found for evt={prefix}. Last 2k log chars:\n{log[-2000:]}"

        required_markers = [
            "step=1 sms_received",
            "step=2 sender_match=YES",
            "step=3 tracking_extracted",
            "step=4 shipment_found=YES",
            "step=5 status_identified",
            "step=6 update_decision=APPLY",
            "step=7 status_updated",
        ]
        joined = "\n".join(lines)
        missing = [m for m in required_markers if m not in joined]
        assert not missing, (
            f"B3 missing log markers {missing}.\nLines found for evt={prefix}:\n{joined}"
        )

    def test_D2_b1_skip_marker(self, admin_session):
        b1 = TestIngestWhitelist._state["ingest_results"].get("B1")
        assert b1, "B1 must run first"
        event_id = b1["event_id"]
        prefix = event_id[:8]
        time.sleep(0.5)
        log = self._read_log_tail()
        lines = [ln for ln in log.splitlines() if f"evt={prefix}" in ln]
        assert lines, f"No log lines for B1 evt={prefix}"
        joined = "\n".join(lines)
        assert "step=6 update_decision=SKIP" in joined, (
            f"B1: missing SKIP decision line.\n{joined}"
        )
        assert "ignored_intermediate_status" in joined, (
            f"B1: missing ignored_intermediate_status reason.\n{joined}"
        )
