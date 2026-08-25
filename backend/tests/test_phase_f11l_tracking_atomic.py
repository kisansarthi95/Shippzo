"""
Phase F11.L — Atomic tracking-ID allocation verification.

Guarantees under test:
  1. POST /api/couriers/{id}/consume-tracking is atomic (find_one_and_update $inc)
     — 20 concurrent calls MUST return 20 unique tracking_ids.
  2. POST /api/shipments with courier_id and blank tracking_id auto-fills
     a unique tracking_id server-side derived from the courier's counter
     (no client-side allocation needed).
  3. MongoDB unique index `uniq_user_trackingId` on (user_id, tracking_id)
     rejects a duplicate direct-Mongo insert for the SAME tenant.
  4. Multi-tenant: user2 CAN reuse a tracking_id that admin already has —
     the index is per-tenant.
"""
import os
import sys
import uuid
import asyncio
import concurrent.futures
from pathlib import Path

import pytest
import requests
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError

# Load backend .env (MONGO_URL, DB_NAME, EXPO_PUBLIC_BACKEND_URL)
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")

BASE_URL = (
    os.environ.get("EXPO_PUBLIC_BACKEND_URL")
    or os.environ.get("EXPO_BACKEND_URL")
    or ""
).rstrip("/")

MONGO_URL = os.environ.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME")

if not BASE_URL:
    pytest.skip(
        "EXPO_PUBLIC_BACKEND_URL not configured",
        allow_module_level=True,
    )

ADMIN = {"email": "admin@test.com", "password": "Admin@12345"}
USER2 = {"email": "user2@test.com", "password": "User@12345"}


# ---------------------------------------------------------------- helpers ----

def _login(creds):
    r = requests.post(f"{BASE_URL}/api/auth/login", json=creds, timeout=20)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    body = r.json()
    return body["token"], body["id"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _mkcourier(token, prefix, padding=5, next_number=1):
    """Create a courier under the auth'd user with predictable series."""
    payload = {
        "name": f"TEST-Atomic-{prefix}-{uuid.uuid4().hex[:6]}",
        "series_prefix": prefix,
        "number_padding": padding,
        "next_number": next_number,
        "manual_tracking": False,
    }
    r = requests.post(
        f"{BASE_URL}/api/couriers",
        json=payload,
        headers=_auth(token),
        timeout=20,
    )
    assert r.status_code == 200, f"courier create failed: {r.status_code} {r.text}"
    return r.json()


def _mkshipment(token, courier_id, *, tracking_id=""):
    """POST a minimal shipment (Prepaid ₹100)."""
    payload = {
        "tracking_id":   tracking_id,
        "courier_id":    courier_id,
        "customer_name": f"TEST-Atomic-{uuid.uuid4().hex[:8]}",
        "customer_phone": "9000000000",
        "address_line1": "Test Addr",
        "city": "Delhi", "state": "Delhi", "pincode": "110001",
        "payment_mode": "Prepaid",
        "amount": 100.0,
        "weight": "250 g",
    }
    r = requests.post(
        f"{BASE_URL}/api/shipments",
        json=payload,
        headers=_auth(token),
        timeout=30,
    )
    return r


# ---------------------------------------------------------------- fixtures ---

@pytest.fixture(scope="module")
def admin_ctx():
    tok, uid = _login(ADMIN)
    return {"token": tok, "user_id": uid}


@pytest.fixture(scope="module")
def user2_ctx():
    tok, uid = _login(USER2)
    return {"token": tok, "user_id": uid}


@pytest.fixture(scope="module")
def mongo_db():
    if not MONGO_URL or not DB_NAME:
        pytest.skip("MONGO_URL / DB_NAME not configured")
    client = MongoClient(MONGO_URL)
    yield client[DB_NAME]
    client.close()


# ---------------------------------------------------------------- tests -----

class TestUniqueIndexExists:
    """Index configuration sanity — the unique per-tenant index must exist."""

    def test_uniq_user_trackingId_index_present(self, mongo_db):
        idx = mongo_db.shipments.index_information()
        assert "uniq_user_trackingId" in idx, (
            f"Expected 'uniq_user_trackingId' in shipments indexes, "
            f"got: {list(idx.keys())}"
        )
        spec = idx["uniq_user_trackingId"]
        assert spec.get("unique") is True, "index must be unique"
        # Keys must be user_id + tracking_id (order-agnostic set-check)
        keys = {k for k, _ in spec["key"]}
        assert keys == {"user_id", "tracking_id"}
        # Partial filter must exclude blanks
        pfe = spec.get("partialFilterExpression") or {}
        assert "tracking_id" in pfe


class TestConcurrentConsumeTracking:
    """20 parallel consume-tracking calls must produce 20 UNIQUE ids."""

    def test_concurrent_consume_unique(self, admin_ctx):
        courier = _mkcourier(
            admin_ctx["token"], prefix="ATM", padding=5, next_number=1,
        )
        cid = courier["id"]

        def _hit(_i):
            r = requests.post(
                f"{BASE_URL}/api/couriers/{cid}/consume-tracking",
                headers=_auth(admin_ctx["token"]),
                timeout=30,
            )
            return r.status_code, r.json() if r.ok else r.text

        N = 20
        with concurrent.futures.ThreadPoolExecutor(max_workers=N) as ex:
            results = list(ex.map(_hit, range(N)))

        # All 20 must succeed
        codes = [c for c, _ in results]
        assert all(c == 200 for c in codes), f"non-200s: {codes}"

        ids = [body["tracking_id"] for _, body in results]
        # No blanks (courier is auto-mode)
        assert all(t for t in ids), f"blank tracking id emitted: {ids}"
        # Uniqueness — the acceptance criterion for Phase F11.L
        assert len(set(ids)) == N, (
            f"DUPLICATE tracking ids under 20-way concurrency: "
            f"{sorted(ids)} (dupes: "
            f"{[t for t in ids if ids.count(t) > 1]})"
        )
        # And each id must be ATM + 5-digit zero-padded number
        for t in ids:
            assert t.startswith("ATM") and t[3:].isdigit() and len(t[3:]) == 5, (
                f"bad format: {t}"
            )


class TestShipmentAutoAllocatesTracking:
    """POST /api/shipments must derive tracking_id from courier counter
    when the client sends blank tracking_id (Phase F11.L server-side
    allocation)."""

    def test_shipment_autofills_tracking_id(self, admin_ctx):
        courier = _mkcourier(
            admin_ctx["token"], prefix="AUT", padding=5, next_number=500,
        )
        r = _mkshipment(admin_ctx["token"], courier["id"], tracking_id="")
        assert r.status_code == 200, f"shipment create failed: {r.status_code} {r.text}"
        ship = r.json()
        tid = ship.get("tracking_id") or ""
        # Counter was 500 → post-$inc pre-doc has next_number=500 (pymongo
        # returns pre-update doc; router formats from that value).
        # But some implementations return the post-value — accept either
        # 500 or 501 as long as it matches the prefix + padding.
        assert tid.startswith("AUT"), f"expected AUT prefix, got {tid!r}"
        assert tid[3:].isdigit() and len(tid[3:]) == 5, (
            f"bad zero-padded format: {tid!r}"
        )
        n = int(tid[3:])
        assert n in (500, 501), f"expected 500 or 501, got {n}"

    def test_shipment_concurrent_creates_get_unique_tracking(self, admin_ctx):
        """5 concurrent shipment creates on the same courier must all
        receive DISTINCT tracking_ids (server-side atomic allocation)."""
        courier = _mkcourier(
            admin_ctx["token"], prefix="RCE", padding=5, next_number=1,
        )
        cid = courier["id"]

        def _create(_i):
            r = _mkshipment(admin_ctx["token"], cid, tracking_id="")
            return r.status_code, (r.json() if r.ok else r.text)

        N = 5
        with concurrent.futures.ThreadPoolExecutor(max_workers=N) as ex:
            results = list(ex.map(_create, range(N)))

        for code, body in results:
            assert code == 200, f"non-200 shipment create: {code} {body}"

        tids = [b["tracking_id"] for _, b in results]
        assert all(t for t in tids), f"blank tracking id: {tids}"
        assert len(set(tids)) == N, (
            f"DUPLICATE tracking ids under concurrent POST /shipments: "
            f"{sorted(tids)}"
        )


class TestUniqueIndexRejectsDuplicate:
    """Direct-Mongo insert bypass to prove the DB refuses a duplicate
    (user_id, tracking_id) write. Simulates a bug in the app layer."""

    def test_duplicate_same_user_rejected(
        self, admin_ctx, user2_ctx, mongo_db,
    ):
        uid = admin_ctx["user_id"]
        tid = f"DUP-{uuid.uuid4().hex[:10]}"

        base = {
            "user_id": uid,
            "tracking_id": tid,
            "customer_name": "TEST-Atomic-DupProbe",
            "status": "Pending",
        }
        doc1 = {**base, "id": str(uuid.uuid4())}
        doc2 = {**base, "id": str(uuid.uuid4())}

        # First insert must succeed
        mongo_db.shipments.insert_one(doc1)
        try:
            # Second insert with the SAME (user_id, tracking_id) MUST fail
            with pytest.raises(DuplicateKeyError):
                mongo_db.shipments.insert_one(doc2)
        finally:
            # Cleanup — remove the probe row
            mongo_db.shipments.delete_many(
                {"user_id": uid, "tracking_id": tid},
            )

    def test_multi_tenant_same_tracking_id_allowed(
        self, admin_ctx, user2_ctx, mongo_db,
    ):
        """user2 CAN reuse a tracking_id that admin already has — the
        composite (user_id, tracking_id) index isolates tenants."""
        tid = f"MT-{uuid.uuid4().hex[:10]}"

        admin_doc = {
            "id": str(uuid.uuid4()),
            "user_id": admin_ctx["user_id"],
            "tracking_id": tid,
            "customer_name": "TEST-Atomic-Admin",
            "status": "Pending",
        }
        user2_doc = {
            "id": str(uuid.uuid4()),
            "user_id": user2_ctx["user_id"],
            "tracking_id": tid,   # SAME tracking id, different tenant
            "customer_name": "TEST-Atomic-User2",
            "status": "Pending",
        }
        try:
            mongo_db.shipments.insert_one(admin_doc)
            # Second insert under a DIFFERENT user_id must succeed
            mongo_db.shipments.insert_one(user2_doc)
            # Both must be readable
            hits = list(
                mongo_db.shipments.find(
                    {"tracking_id": tid}, {"user_id": 1, "_id": 0},
                )
            )
            uids = {h["user_id"] for h in hits}
            assert uids == {admin_ctx["user_id"], user2_ctx["user_id"]}, (
                f"expected both tenants to have {tid}, got {uids}"
            )
        finally:
            mongo_db.shipments.delete_many({"tracking_id": tid})

    def test_partial_filter_allows_blank_tracking_ids(
        self, admin_ctx, mongo_db,
    ):
        """Two rows with tracking_id='' (manual-mode couriers pre-AWB)
        under the same user must both insert — partial filter excludes
        blank strings."""
        uid = admin_ctx["user_id"]
        probe1 = {
            "id": str(uuid.uuid4()), "user_id": uid,
            "tracking_id": "", "customer_name": "TEST-Blank1",
            "status": "Pending",
        }
        probe2 = {
            "id": str(uuid.uuid4()), "user_id": uid,
            "tracking_id": "", "customer_name": "TEST-Blank2",
            "status": "Pending",
        }
        try:
            mongo_db.shipments.insert_one(probe1)
            # Must not raise — partial filter excludes ''
            mongo_db.shipments.insert_one(probe2)
        finally:
            mongo_db.shipments.delete_many(
                {"user_id": uid, "id": {"$in": [probe1["id"], probe2["id"]]}},
            )
