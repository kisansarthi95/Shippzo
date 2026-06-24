"""
Phase-32 rev-3 (Iteration 16) — Duplicate order_id → HTTP 409 surface.

Bug RCA:
  The Phase-32 compound unique index `(order_id, user_id)` on the
  `shipments` collection started surfacing as raw HTTP 500 to users
  when their auto-generated/typed order_id collided with an existing
  shipment for the same user. Pymongo raised `DuplicateKeyError`
  inside the two `db.shipments.insert_one(...)` call sites in
  `routers/shipments_write.py`:

    • Line ~440  — POST /api/shipments (create_shipment)
    • Line ~1121 — POST /api/orders/pending/{id}/ship (ship_pending_order)

  Both inserts are now wrapped in try/except that string-matches
  "duplicate key" / "E11000" and raises HTTPException(409, ...) with
  a user-friendly detail.

This suite verifies:
  1. POST /api/shipments duplicate order_id (same user) → 409 (NOT 500).
  2. POST /api/shipments unique order_id → 200 (happy-path regression).
  3. Cross-user same order_id allowed → both 200 (compound index is per-user).
  4. POST /api/orders/pending/{id}/ship with a pending row whose
     order_id collides with an existing shipment → 409 (NOT 500).
  5. Detail message contains the colliding order_id verbatim.

Credentials sourced from /app/memory/test_credentials.md.
"""
import os
import uuid
from pathlib import Path

import pytest
import requests
from dotenv import load_dotenv
from pymongo import MongoClient

frontend_env = Path(__file__).parent.parent.parent / "frontend" / ".env"
load_dotenv(frontend_env)
backend_env = Path(__file__).parent.parent / ".env"
load_dotenv(backend_env)

BASE_URL = (
    os.environ.get("EXPO_PUBLIC_BACKEND_URL")
    or os.environ.get("EXPO_BACKEND_URL")
)
if not BASE_URL:
    raise RuntimeError("EXPO_PUBLIC_BACKEND_URL / EXPO_BACKEND_URL missing")
BASE_URL = BASE_URL.rstrip("/")

MONGO_URL = os.environ.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME")
if not (MONGO_URL and DB_NAME):
    raise RuntimeError("MONGO_URL / DB_NAME missing in backend/.env")

USER_EMAIL = "user2@test.com"
USER_PASSWORD = "User@12345"
ADMIN_EMAIL = "admin@test.com"
ADMIN_PASSWORD = "Admin@12345"


# ───────────────────────────────────────────────────── fixtures ──
@pytest.fixture(scope="module")
def api():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(MONGO_URL)
    yield client[DB_NAME]
    client.close()


def _login(api, email, pw):
    r = api.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": email, "password": pw},
        timeout=20,
    )
    assert r.status_code == 200, (
        f"Login {email} failed: {r.status_code} {r.text[:200]}"
    )
    body = r.json()
    return body["token"], body.get("id")


@pytest.fixture(scope="module")
def user_auth(api):
    token, uid = _login(api, USER_EMAIL, USER_PASSWORD)
    return {
        "headers": {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        "user_id": uid,
    }


@pytest.fixture(scope="module")
def admin_auth(api):
    token, uid = _login(api, ADMIN_EMAIL, ADMIN_PASSWORD)
    return {
        "headers": {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        "user_id": uid,
    }


def _first_courier_id(api, headers):
    r = api.get(f"{BASE_URL}/api/couriers", headers=headers, timeout=20)
    assert r.status_code == 200, f"Couriers fetch: {r.status_code}"
    data = r.json()
    items = data if isinstance(data, list) else (
        data.get("couriers") or data.get("items") or []
    )
    for c in items:
        if not c.get("manual_tracking"):
            return c.get("id") or c.get("_id")
    if items:
        return items[0].get("id") or items[0].get("_id")
    pytest.skip("No courier available")


@pytest.fixture(scope="module")
def user_courier_id(api, user_auth):
    return _first_courier_id(api, user_auth["headers"])


@pytest.fixture(scope="module")
def admin_courier_id(api, admin_auth):
    return _first_courier_id(api, admin_auth["headers"])


@pytest.fixture(scope="module", autouse=True)
def topup_wallets(mongo, user_auth, admin_auth):
    """Avoid 402 'Insufficient credits' which would mask the 409 we
    want to assert. Prior iterations show wallet drains across runs."""
    for uid in (user_auth["user_id"], admin_auth["user_id"]):
        if not uid:
            continue
        mongo.wallets.update_one(
            {"user_id": uid},
            {
                "$set": {
                    "user_id": uid,
                    "total_credits": 100000,
                    "used_credits": 0,
                    "remaining_credits": 100000,
                }
            },
            upsert=True,
        )
    yield


def _shipment_payload(courier_id, order_id, payment_mode="Prepaid"):
    """Build a minimal POST /api/shipments payload with an EXPLICIT
    order_id (not equal to master_order_id), which signals the
    Phase-29 TOCTOU branch to preserve our chosen value verbatim."""
    unique = uuid.uuid4().hex[:6].upper()
    return {
        "customer_name": f"TEST_DUPE_{unique}",
        "customer_phone": "9876543210",
        "address_line1": "1 Duplicate Lane",
        "city": "Ahmedabad",
        "state": "Gujarat",
        "pincode": "380001",
        "items": ["Widget"],
        "courier_id": courier_id,
        "tracking_id": f"TST{unique}",
        "payment_mode": payment_mode,
        "amount": 100,
        "order_id": order_id,
    }


# ════════════════════════════════ POST /api/shipments duplicate ════════
class TestShipmentDuplicateOrderId:

    def test_unique_order_id_happy_path(
        self, api, user_auth, user_courier_id, mongo, request,
    ):
        """Regression: a fresh order_id should still 200."""
        oid = f"DUPECHECK_OK_{uuid.uuid4().hex[:8].upper()}"
        payload = _shipment_payload(user_courier_id, oid)
        r = api.post(
            f"{BASE_URL}/api/shipments",
            headers=user_auth["headers"],
            json=payload, timeout=60,
        )
        assert r.status_code == 200, (
            f"Happy path failed: {r.status_code} {r.text[:300]}"
        )
        body = r.json()
        assert body.get("order_id") == oid, (
            f"order_id round-trip mismatch: {body.get('order_id')!r} vs {oid!r}"
        )
        # GET to confirm persistence.
        sid = body["id"]
        g = api.get(
            f"{BASE_URL}/api/shipments/{sid}",
            headers=user_auth["headers"], timeout=20,
        )
        assert g.status_code == 200
        assert g.json()["order_id"] == oid
        request.config._cleanup_oids = getattr(
            request.config, "_cleanup_oids", []
        )
        request.config._cleanup_oids.append(oid)

    def test_duplicate_order_id_same_user_returns_409(
        self, api, user_auth, user_courier_id,
    ):
        """Core bug-fix verification: two POSTs with same order_id →
        first 200, second MUST be 409 (NOT 500), with detail
        mentioning the colliding order_id."""
        oid = f"DUPECHECK_409_{uuid.uuid4().hex[:8].upper()}"

        p1 = _shipment_payload(user_courier_id, oid)
        r1 = api.post(
            f"{BASE_URL}/api/shipments",
            headers=user_auth["headers"],
            json=p1, timeout=60,
        )
        assert r1.status_code == 200, (
            f"First insert should 200; got {r1.status_code} {r1.text[:300]}"
        )
        assert r1.json().get("order_id") == oid

        # Second POST — same order_id, same user → must 409 (NOT 500).
        p2 = _shipment_payload(user_courier_id, oid)
        r2 = api.post(
            f"{BASE_URL}/api/shipments",
            headers=user_auth["headers"],
            json=p2, timeout=60,
        )
        assert r2.status_code != 500, (
            f"Duplicate insert SHOULD NOT 500 anymore — got 500: "
            f"{r2.text[:400]}"
        )
        assert r2.status_code == 409, (
            f"Expected 409 on duplicate order_id, got "
            f"{r2.status_code} {r2.text[:400]}"
        )
        detail = r2.json().get("detail", "")
        assert oid in detail, (
            f"409 detail must mention the colliding order_id {oid!r}; "
            f"got: {detail!r}"
        )
        # Friendly guidance — should hint at using a different order_id.
        lower = detail.lower()
        assert ("different" in lower) or ("auto-generate" in lower) or (
            "already exists" in lower
        ), f"409 detail should be user-friendly; got: {detail!r}"

    def test_cross_user_same_order_id_allowed(
        self, api, user_auth, admin_auth, user_courier_id, admin_courier_id,
    ):
        """Compound unique is per-user — same order_id across two
        different users must both succeed (200, 200)."""
        oid = f"DUPECHECK_XU_{uuid.uuid4().hex[:8].upper()}"

        p_user = _shipment_payload(user_courier_id, oid)
        r_user = api.post(
            f"{BASE_URL}/api/shipments",
            headers=user_auth["headers"],
            json=p_user, timeout=60,
        )
        assert r_user.status_code == 200, (
            f"user2 insert failed: {r_user.status_code} {r_user.text[:300]}"
        )

        p_admin = _shipment_payload(admin_courier_id, oid)
        r_admin = api.post(
            f"{BASE_URL}/api/shipments",
            headers=admin_auth["headers"],
            json=p_admin, timeout=60,
        )
        assert r_admin.status_code == 200, (
            f"admin insert with same order_id failed: "
            f"{r_admin.status_code} {r_admin.text[:300]} — "
            "compound unique should be (order_id, user_id) per-user"
        )
        assert r_admin.json().get("order_id") == oid


# ═══════════════════════════ POST /api/orders/pending/{id}/ship 409 ═══
class TestShipPendingDuplicateOrderId:

    def test_ship_pending_with_existing_order_id_returns_409(
        self, api, user_auth, user_courier_id, mongo,
    ):
        """Seed a shipment with order_id=X for user2, then create
        a pending row, force its order_id to X via Mongo, then
        attempt to ship — must 409 (NOT 500)."""
        oid = f"DUPECHECK_PEND_{uuid.uuid4().hex[:8].upper()}"

        # 1) Create the first shipment that locks order_id=X.
        p1 = _shipment_payload(user_courier_id, oid)
        r1 = api.post(
            f"{BASE_URL}/api/shipments",
            headers=user_auth["headers"], json=p1, timeout=60,
        )
        assert r1.status_code == 200, (
            f"Seed shipment failed: {r1.status_code} {r1.text[:300]}"
        )

        # 2) Create a pending order via smart-paste (skip_llm regex path).
        unique = uuid.uuid4().hex[:6].upper()
        paste = (
            f"Name: TEST_PENDDUP_{unique}\n"
            "Phone: 9999988888\n"
            "Address: 1 Pending Dup Lane\n"
            "City: Mumbai\n"
            "State: Maharashtra\n"
            "Pincode: 400001\n"
            "Item: Widget\n"
            "Amount: 500\n"
            "Payment: COD"
        )
        rp = api.post(
            f"{BASE_URL}/api/smart-paste",
            headers=user_auth["headers"],
            json={"text": paste, "skip_llm": True},
            timeout=60,
        )
        assert rp.status_code == 200, (
            f"smart-paste failed: {rp.status_code} {rp.text[:300]}"
        )
        pending = rp.json()
        pid = pending["id"]

        # 3) Force the pending order's order_id to the colliding value.
        upd = mongo.pending_orders.update_one(
            {"id": pid, "user_id": user_auth["user_id"]},
            {"$set": {"order_id": oid, "order_id_hint": oid}},
        )
        assert upd.matched_count == 1, (
            f"Could not patch pending order {pid} to force collision"
        )

        # 4) Ship the pending — duplicate insert must surface as 409.
        rs = api.post(
            f"{BASE_URL}/api/orders/pending/{pid}/ship",
            headers=user_auth["headers"],
            json={"courier_id": user_courier_id},
            timeout=60,
        )
        assert rs.status_code != 500, (
            f"ship_pending_order SHOULD NOT 500 on dup — got 500: "
            f"{rs.text[:400]}"
        )
        assert rs.status_code == 409, (
            f"Expected 409 on ship-pending duplicate, got "
            f"{rs.status_code} {rs.text[:400]}"
        )
        detail = rs.json().get("detail", "")
        assert oid in detail, (
            f"409 detail must mention the colliding order_id {oid!r}; "
            f"got: {detail!r}"
        )

    def test_ship_pending_unique_order_id_happy_path(
        self, api, user_auth, user_courier_id,
    ):
        """Regression: a fresh pending → ship works (200)."""
        unique = uuid.uuid4().hex[:6].upper()
        paste = (
            f"Name: TEST_PENDOK_{unique}\n"
            "Phone: 9999977777\n"
            "Address: 2 Pending OK St\n"
            "City: Pune\n"
            "State: Maharashtra\n"
            "Pincode: 411001\n"
            "Item: Gadget\n"
            "Amount: 250\n"
            "Payment: COD"
        )
        rp = api.post(
            f"{BASE_URL}/api/smart-paste",
            headers=user_auth["headers"],
            json={"text": paste, "skip_llm": True}, timeout=60,
        )
        assert rp.status_code == 200
        pid = rp.json()["id"]

        rs = api.post(
            f"{BASE_URL}/api/orders/pending/{pid}/ship",
            headers=user_auth["headers"],
            json={"courier_id": user_courier_id},
            timeout=60,
        )
        assert rs.status_code == 200, (
            f"happy-path ship failed: {rs.status_code} {rs.text[:300]}"
        )
        assert rs.json().get("tracking_id")
