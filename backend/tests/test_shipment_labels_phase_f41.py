"""Backend tests for Shipment Labels (Phase F4.1).

Covers:
  A. Labels CRUD
  B. Shipment label assignment
  C. Delete label with cleanup
  D. Regression on existing shipment endpoints
  E. Multi-user isolation
"""
from __future__ import annotations

import os
import uuid
from typing import Any, Dict, List, Optional

import pytest
import requests

BASE_URL = os.environ.get(
    "EXPO_BACKEND_URL",
    "https://logistics-hub-740.preview.emergentagent.com",
).rstrip("/")

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASSWORD = "Admin@12345"
USER2_EMAIL = "user2@test.com"
USER2_PASSWORD = "User@12345"

EXPECTED_DEFAULT_NAMES = {
    "Address Issue", "Phone Number Issue", "Item Pending", "Return Received",
    "Resend Article", "Manual Review", "VIP Customer", "Urgent",
    "High Priority", "Low Priority",
}

LABEL_FIELDS = {
    "id", "user_id", "name", "icon", "color", "kind",
    "is_default", "created_at", "updated_at",
}


# ---------- Session / fixtures ----------

def _login(email: str, password: str) -> Dict[str, Any]:
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": email, "password": password},
        timeout=20,
    )
    assert r.status_code == 200, f"Login failed for {email}: {r.status_code} {r.text}"
    return r.json()


@pytest.fixture(scope="module")
def admin_session():
    data = _login(ADMIN_EMAIL, ADMIN_PASSWORD)
    s = requests.Session()
    s.headers.update({
        "Content-Type": "application/json",
        "Authorization": f"Bearer {data['token']}",
    })
    return s


@pytest.fixture(scope="module")
def user2_session():
    data = _login(USER2_EMAIL, USER2_PASSWORD)
    s = requests.Session()
    s.headers.update({
        "Content-Type": "application/json",
        "Authorization": f"Bearer {data['token']}",
    })
    return s


@pytest.fixture(scope="module")
def state() -> Dict[str, Any]:
    return {}


# ---------- Helpers ----------

def _get_shipment_by_id(session: requests.Session, sid: str) -> Optional[Dict[str, Any]]:
    r = session.get(f"{BASE_URL}/api/shipments?limit=200", timeout=30)
    assert r.status_code == 200, f"GET /shipments -> {r.status_code} {r.text[:400]}"
    body = r.json()
    rows = body if isinstance(body, list) else body.get("shipments") or body.get("items") or []
    for s in rows:
        if s.get("id") == sid:
            return s
    return None


def _find_or_create_shipment(session: requests.Session) -> str:
    # Try to reuse an existing shipment first
    r = session.get(f"{BASE_URL}/api/shipments?limit=5", timeout=30)
    assert r.status_code == 200, f"GET /shipments -> {r.status_code} {r.text[:400]}"
    body = r.json()
    rows = body if isinstance(body, list) else body.get("shipments") or body.get("items") or []
    if rows:
        return rows[0]["id"]

    # Otherwise create one
    payload = {
        "tracking_id": f"TEST-LBL-{uuid.uuid4().hex[:8]}",
        "customer_name": "TEST Label Customer",
        "customer_phone": "9999999999",
        "address_line1": "TEST address",
        "city": "Ahmedabad",
        "state": "Gujarat",
        "pincode": "380001",
        "payment_mode": "Prepaid",
        "amount": 100.0,
    }
    r = session.post(f"{BASE_URL}/api/shipments", json=payload, timeout=30)
    assert r.status_code in (200, 201), f"POST /shipments -> {r.status_code} {r.text[:400]}"
    return r.json()["id"]


# =====================================================================
# A. Labels CRUD
# =====================================================================

class TestA_LabelsCRUD:
    def test_A1_list_labels_and_defaults_present(self, admin_session, state):
        r = admin_session.get(f"{BASE_URL}/api/labels", timeout=20)
        assert r.status_code == 200, f"GET /api/labels -> {r.status_code} {r.text}"
        body = r.json()
        assert "labels" in body and "count" in body
        assert body["count"] >= 10, f"count={body['count']} expected >= 10"
        assert len(body["labels"]) == body["count"]

        names = {row["name"] for row in body["labels"]}
        # All 10 preset names must be present
        missing = EXPECTED_DEFAULT_NAMES - names
        assert not missing, f"Missing default labels: {missing}"

        # Verify shape of each label
        for row in body["labels"]:
            for f in LABEL_FIELDS:
                assert f in row, f"Label missing field '{f}': {row}"
            assert "_id" not in row, "Raw Mongo _id leaked in label"

        # Verify defaults have is_default=true
        for row in body["labels"]:
            if row["name"] in EXPECTED_DEFAULT_NAMES:
                assert row["is_default"] is True, (
                    f"Preset label {row['name']!r} should have is_default=true"
                )

        state["admin_default_labels"] = [
            r for r in body["labels"] if r["is_default"]
        ]

    def test_A2_create_label(self, admin_session, state):
        # Ensure clean slate — remove any leftover "AutoTest Label" from a
        # previous run so the create + 409 duplicate test are deterministic.
        r = admin_session.get(f"{BASE_URL}/api/labels", timeout=20)
        for row in r.json().get("labels", []):
            if row["name"].lower() == "autotest label":
                admin_session.delete(f"{BASE_URL}/api/labels/{row['id']}", timeout=20)

        payload = {"name": "AutoTest Label", "icon": "star", "color": "#EC4899"}
        r = admin_session.post(f"{BASE_URL}/api/labels", json=payload, timeout=20)
        assert r.status_code in (200, 201), f"POST /api/labels -> {r.status_code} {r.text}"
        body = r.json()
        assert body.get("ok") is True
        lab = body["label"]
        assert lab["name"] == "AutoTest Label"
        assert lab["icon"] == "star"
        assert lab["color"] == "#EC4899"
        assert lab["is_default"] is False
        assert lab["kind"] == "custom"
        # Validate id is a UUID
        uuid.UUID(lab["id"])
        state["autotest_label_id"] = lab["id"]

    def test_A3_duplicate_name_case_insensitive(self, admin_session, state):
        # Same name
        r1 = admin_session.post(
            f"{BASE_URL}/api/labels",
            json={"name": "AutoTest Label"},
            timeout=20,
        )
        assert r1.status_code == 409, f"expected 409 got {r1.status_code} {r1.text}"
        assert "already exists" in r1.text.lower()

        # Different casing → still 409
        r2 = admin_session.post(
            f"{BASE_URL}/api/labels",
            json={"name": "autotest label"},
            timeout=20,
        )
        assert r2.status_code == 409, f"case-insensitive dup expected 409 got {r2.status_code} {r2.text}"

    def test_A4_update_label(self, admin_session, state):
        lid = state["autotest_label_id"]
        r = admin_session.put(
            f"{BASE_URL}/api/labels/{lid}",
            json={"name": "Renamed", "color": "#10B981"},
            timeout=20,
        )
        assert r.status_code == 200, f"PUT /api/labels/{lid} -> {r.status_code} {r.text}"
        body = r.json()
        assert body.get("ok") is True
        assert body["label"]["name"] == "Renamed"
        assert body["label"]["color"] == "#10B981"

        # Verify via GET
        r2 = admin_session.get(f"{BASE_URL}/api/labels", timeout=20)
        found = next((row for row in r2.json()["labels"] if row["id"] == lid), None)
        assert found is not None, "updated label vanished from list"
        assert found["name"] == "Renamed"
        assert found["color"] == "#10B981"

    def test_A5_update_empty_body(self, admin_session, state):
        lid = state["autotest_label_id"]
        r = admin_session.put(f"{BASE_URL}/api/labels/{lid}", json={}, timeout=20)
        assert r.status_code == 400, f"expected 400 got {r.status_code} {r.text}"
        assert "no fields" in r.text.lower()

    def test_A6_update_nonexistent(self, admin_session):
        r = admin_session.put(
            f"{BASE_URL}/api/labels/does-not-exist",
            json={"name": "x"},
            timeout=20,
        )
        assert r.status_code == 404, f"expected 404 got {r.status_code} {r.text}"


# =====================================================================
# B. Shipment assignment
# =====================================================================

class TestB_ShipmentAssignment:
    def test_B0_prep_shipment(self, admin_session, state):
        state["shipment_id"] = _find_or_create_shipment(admin_session)
        # Grab a real default label id
        r = admin_session.get(f"{BASE_URL}/api/labels", timeout=20)
        defaults = [row for row in r.json()["labels"] if row["is_default"]]
        assert defaults, "no default labels found"
        state["default_label_id"] = defaults[0]["id"]

    def test_B1_assign_labels(self, admin_session, state):
        sid = state["shipment_id"]
        lids = [state["autotest_label_id"], state["default_label_id"]]
        r = admin_session.put(
            f"{BASE_URL}/api/shipments/{sid}/labels",
            json={"labels": lids},
            timeout=20,
        )
        assert r.status_code == 200, f"PUT /shipments/{sid}/labels -> {r.status_code} {r.text}"
        body = r.json()
        assert body.get("ok") is True
        assert set(body["labels"]) == set(lids)

        # Verify persistence
        ship = _get_shipment_by_id(admin_session, sid)
        assert ship is not None, "shipment vanished"
        assert set(ship.get("labels", [])) == set(lids)

    def test_B2_idempotency(self, admin_session, state):
        sid = state["shipment_id"]
        lids = [state["autotest_label_id"], state["default_label_id"]]
        r = admin_session.put(
            f"{BASE_URL}/api/shipments/{sid}/labels",
            json={"labels": lids},
            timeout=20,
        )
        assert r.status_code == 200
        body = r.json()
        assert len(body["labels"]) == 2
        assert set(body["labels"]) == set(lids)

    def test_B3_dedupe_input(self, admin_session, state):
        sid = state["shipment_id"]
        a, b = state["autotest_label_id"], state["default_label_id"]
        r = admin_session.put(
            f"{BASE_URL}/api/shipments/{sid}/labels",
            json={"labels": [a, a, b, b]},
            timeout=20,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["labels"]) == 2, f"dedupe failed: {body['labels']}"
        assert set(body["labels"]) == {a, b}

    def test_B4_silent_drop_invalid_ids(self, admin_session, state):
        sid = state["shipment_id"]
        valid = state["autotest_label_id"]
        r = admin_session.put(
            f"{BASE_URL}/api/shipments/{sid}/labels",
            json={"labels": [valid, "fake-nonexistent-id"]},
            timeout=20,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["labels"] == [valid], f"invalid id not dropped: {body['labels']}"

    def test_B5_clear_labels(self, admin_session, state):
        sid = state["shipment_id"]
        r = admin_session.put(
            f"{BASE_URL}/api/shipments/{sid}/labels",
            json={"labels": []},
            timeout=20,
        )
        assert r.status_code == 200, r.text
        assert r.json()["labels"] == []
        ship = _get_shipment_by_id(admin_session, sid)
        assert not (ship.get("labels") or []), (
            f"labels not cleared: {ship.get('labels')}"
        )

    def test_B6_cap_check_over_20(self, admin_session, state):
        sid = state["shipment_id"]
        fake_ids = [f"fake-{i}" for i in range(21)]
        r = admin_session.put(
            f"{BASE_URL}/api/shipments/{sid}/labels",
            json={"labels": fake_ids},
            timeout=20,
        )
        assert r.status_code == 400, f"expected 400 got {r.status_code} {r.text}"
        assert "max 20" in r.text.lower()

    def test_B7_shipment_not_found(self, admin_session):
        r = admin_session.put(
            f"{BASE_URL}/api/shipments/fake-nonexistent/labels",
            json={"labels": []},
            timeout=20,
        )
        assert r.status_code == 404, f"expected 404 got {r.status_code} {r.text}"


# =====================================================================
# C. Delete label with cleanup
# =====================================================================

class TestC_DeleteWithCleanup:
    def test_C1_reassign_label(self, admin_session, state):
        sid = state["shipment_id"]
        r = admin_session.put(
            f"{BASE_URL}/api/shipments/{sid}/labels",
            json={"labels": [state["autotest_label_id"]]},
            timeout=20,
        )
        assert r.status_code == 200
        assert r.json()["labels"] == [state["autotest_label_id"]]

    def test_C2_delete_label(self, admin_session, state):
        lid = state["autotest_label_id"]
        r = admin_session.delete(f"{BASE_URL}/api/labels/{lid}", timeout=20)
        assert r.status_code == 200, f"DELETE /api/labels/{lid} -> {r.status_code} {r.text}"
        assert r.json().get("ok") is True

    def test_C3_verify_cleanup(self, admin_session, state):
        lid = state["autotest_label_id"]
        # No longer in labels list
        r = admin_session.get(f"{BASE_URL}/api/labels", timeout=20)
        ids = {row["id"] for row in r.json()["labels"]}
        assert lid not in ids, "deleted label still present in list"

        # Pulled from shipment.labels
        ship = _get_shipment_by_id(admin_session, state["shipment_id"])
        assert lid not in (ship.get("labels") or []), (
            f"deleted label still on shipment: {ship.get('labels')}"
        )

    def test_C4_delete_nonexistent(self, admin_session):
        r = admin_session.delete(f"{BASE_URL}/api/labels/does-not-exist", timeout=20)
        assert r.status_code == 404, f"expected 404 got {r.status_code} {r.text}"


# =====================================================================
# D. Regression on existing shipment endpoints
# =====================================================================

class TestD_Regression:
    def test_D1_get_shipments_schema_intact(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/shipments?limit=10", timeout=30)
        assert r.status_code == 200, f"GET /shipments -> {r.status_code} {r.text[:400]}"
        body = r.json()
        rows = body if isinstance(body, list) else body.get("shipments") or body.get("items") or []
        # Must have expected core fields
        if rows:
            s0 = rows[0]
            for f in ("id", "customer_name", "status", "tracking_id"):
                assert f in s0, f"shipment missing core field {f}"
            # labels field is additive: either missing, [], or a list
            if "labels" in s0:
                assert isinstance(s0["labels"], list), "labels field must be a list"

    def test_D2_create_shipment_without_labels(self, admin_session, state):
        payload = {
            "tracking_id": f"TEST-REG-{uuid.uuid4().hex[:8]}",
            "customer_name": "TEST Regression",
            "customer_phone": "8888888888",
            "address_line1": "TEST addr",
            "city": "Ahmedabad",
            "state": "Gujarat",
            "pincode": "380001",
            "payment_mode": "Prepaid",
            "amount": 250.0,
        }
        r = admin_session.post(f"{BASE_URL}/api/shipments", json=payload, timeout=30)
        assert r.status_code in (200, 201), f"POST /shipments -> {r.status_code} {r.text[:400]}"
        body = r.json()
        assert body.get("customer_name") == "TEST Regression"
        state["regression_shipment_id"] = body["id"]

    def test_D3_put_shipment_non_labels_update(self, admin_session, state):
        sid = state.get("regression_shipment_id") or state.get("shipment_id")
        assert sid, "no shipment id available"
        r = admin_session.put(
            f"{BASE_URL}/api/shipments/{sid}",
            json={"customer_name": "TEST Renamed Customer"},
            timeout=20,
        )
        assert r.status_code == 200, f"PUT /shipments/{sid} -> {r.status_code} {r.text[:400]}"
        # Verify persistence
        ship = _get_shipment_by_id(admin_session, sid)
        assert ship is not None
        assert ship.get("customer_name") == "TEST Renamed Customer"


# =====================================================================
# E. Multi-user isolation
# =====================================================================

class TestE_MultiUserIsolation:
    def test_E1_user2_has_own_defaults(self, admin_session, user2_session, state):
        # Admin ids
        r_admin = admin_session.get(f"{BASE_URL}/api/labels", timeout=20)
        admin_ids = {row["id"] for row in r_admin.json()["labels"]}
        state["admin_label_ids"] = admin_ids

        # user2 defaults
        r = user2_session.get(f"{BASE_URL}/api/labels", timeout=20)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["count"] >= 10, f"user2 count={body['count']} expected >= 10"
        u2_ids = {row["id"] for row in body["labels"]}
        u2_names = {row["name"] for row in body["labels"]}

        # user2 must have their own 10 preset names
        missing = EXPECTED_DEFAULT_NAMES - u2_names
        assert not missing, f"user2 missing default labels: {missing}"

        # ids must be disjoint from admin's
        overlap = admin_ids & u2_ids
        assert not overlap, f"user2 shares label ids with admin: {overlap}"

    def test_E2_user2_cannot_delete_admin_label(self, admin_session, user2_session, state):
        # Pick any admin default label id
        admin_ids = list(state["admin_label_ids"])
        assert admin_ids
        target = admin_ids[0]
        r = user2_session.delete(f"{BASE_URL}/api/labels/{target}", timeout=20)
        assert r.status_code == 404, (
            f"user2 delete of admin label expected 404 got {r.status_code} {r.text}"
        )
        # And admin still sees the label
        r2 = admin_session.get(f"{BASE_URL}/api/labels", timeout=20)
        still_there = any(row["id"] == target for row in r2.json()["labels"])
        assert still_there, "admin label was deleted cross-tenant — CRITICAL"
