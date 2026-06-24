"""
Phase-32 rev-4 (Iteration 17) — Auto-retry on Order ID collisions
for auto-generated IDs in shipment creation.

Both backend paths must transparently regenerate a fresh order_id
when an AUTO-GENERATED id collides, instead of returning 409:

    A) POST /api/shipments — direct create
    B) POST /api/orders/pending/{id}/ship — ship-from-pending

User-typed / external order_ids still surface a clean 409 so the
operator can pick a new value.

Source under test:
  • /app/backend/routers/shipments_write.py  (lines ~466-537, ~1043-1106, ~1264-1300)
  • /app/backend/server.py                   (generate_master_order_id)
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
def user_courier_id(api, user_auth):
    r = api.get(
        f"{BASE_URL}/api/couriers",
        headers=user_auth["headers"], timeout=20,
    )
    assert r.status_code == 200
    data = r.json()
    items = data if isinstance(data, list) else (
        data.get("couriers") or data.get("items") or []
    )
    for c in items:
        if not c.get("manual_tracking"):
            return c.get("id") or c.get("_id")
    if items:
        return items[0].get("id") or items[0].get("_id")
    pytest.skip("No courier available for user2")


@pytest.fixture(scope="module", autouse=True)
def _setup(mongo, user_auth):
    """Top-up wallet so 402 doesn't mask the behaviour we want to
    assert, AND ensure auto_generate_master_order_id is ON in
    settings (required for the autogen-retry branches)."""
    uid = user_auth["user_id"]
    if uid:
        mongo.wallets.update_one(
            {"user_id": uid},
            {"$set": {
                "user_id": uid,
                "total_credits": 100000,
                "used_credits": 0,
                "remaining_credits": 100000,
            }},
            upsert=True,
        )
        mongo.settings.update_one(
            {"user_id": uid},
            {"$set": {"order_id_auto_generate": True}},
            upsert=True,
        )
    yield


def _autogen_style_id():
    """Generate an 11-digit fake YYMMDD<seq> id in a far-future range
    that won't collide with the real counter's current sequence."""
    suffix = str(uuid.uuid4().int)[-5:].zfill(5)
    # Use far-future YYMMDD prefix `991231` so it cannot match a real
    # counter-generated id (which uses today's IST date).
    return f"991231{suffix}"


def _shipment_payload(courier_id, order_id, master_order_id=None,
                      payment_mode="Prepaid"):
    unique = uuid.uuid4().hex[:6].upper()
    p = {
        "customer_name": f"TEST_REV4_{unique}",
        "customer_phone": "9876543210",
        "address_line1": "1 Retry Lane",
        "city": "Ahmedabad",
        "state": "Gujarat",
        "pincode": "380001",
        "items": ["Widget"],
        "courier_id": courier_id,
        "tracking_id": f"TST{unique}",
        "payment_mode": payment_mode,
        "amount": 100,
    }
    if order_id is not None:
        p["order_id"] = order_id
    if master_order_id is not None:
        p["master_order_id"] = master_order_id
    return p


# ════════════════════════ A) POST /api/shipments autogen retry ═════
class TestPostShipmentAutogenRetry:
    """A1, A2, A3 from the review request."""

    def test_A1_autogen_happy_path(
        self, api, user_auth, user_courier_id,
    ):
        """A1 — Omit order_id entirely → server allocates a fresh
        master_order_id and copies it into order_id. Must 200."""
        payload = _shipment_payload(user_courier_id, order_id=None)
        # No order_id, no master_order_id → server auto-generates both.
        r = api.post(
            f"{BASE_URL}/api/shipments",
            headers=user_auth["headers"],
            json=payload, timeout=60,
        )
        assert r.status_code == 200, (
            f"A1 happy path failed: {r.status_code} {r.text[:300]}"
        )
        body = r.json()
        oid = body.get("order_id") or ""
        moid = body.get("master_order_id") or ""
        assert oid and moid, (
            f"A1: response missing order_id/master_order_id: {body}"
        )
        assert oid == moid, (
            f"A1: autogen path must set order_id == master_order_id; "
            f"got order_id={oid!r}, master_order_id={moid!r}"
        )

    def test_A2_autogen_collision_silently_retries(
        self, api, user_auth, user_courier_id, mongo,
    ):
        """A2 — Pre-seed a shipment row with order_id=X for user2.
        Then POST a new shipment with master_order_id=order_id=X
        (autogen-style, matches `\\d{6}\\d{5,}` regex). The server
        MUST silently regenerate and return 200 with a DIFFERENT
        order_id (NOT 409, NOT 500)."""
        uid = user_auth["user_id"]
        x = _autogen_style_id()

        # Seed: a shipment row that already owns order_id=X for user2.
        # Leave master_order_id="" so the in-handler pre-check on
        # `master_order_id` doesn't pre-empt — we want the duplicate
        # to fire at the actual insert site so the autogen-retry
        # loop kicks in.
        mongo.shipments.insert_one({
            "id": str(uuid.uuid4()),
            "order_id": x,
            "master_order_id": "",
            "user_id": uid,
            "customer_name": "TEST_REV4_SEED_A2",
            "tracking_id": f"SEED-{uuid.uuid4().hex[:6]}",
            "courier_id": user_courier_id,
            "courier_name": "Seed",
            "status": "shipped",
            "amount": 0.0, "cod_amount": 0.0, "token_amount": 0.0,
            "items": [], "item_description": "",
            "payment_mode": "Prepaid",
        })

        # POST with autogen-style master_order_id == order_id == X.
        payload = _shipment_payload(
            user_courier_id, order_id=x, master_order_id=x,
        )
        r = api.post(
            f"{BASE_URL}/api/shipments",
            headers=user_auth["headers"],
            json=payload, timeout=60,
        )
        # Cleanup seed regardless of outcome
        try:
            mongo.shipments.delete_one(
                {"order_id": x, "user_id": uid, "master_order_id": ""},
            )
        except Exception:
            pass

        assert r.status_code != 409, (
            f"A2: autogen collision MUST NOT return 409; got 409 "
            f"with detail={r.text[:400]}"
        )
        assert r.status_code != 500, (
            f"A2: autogen collision MUST NOT 500; got: {r.text[:400]}"
        )
        assert r.status_code == 200, (
            f"A2: expected 200 after silent retry; got "
            f"{r.status_code} {r.text[:400]}"
        )
        body = r.json()
        new_oid = body.get("order_id") or ""
        new_moid = body.get("master_order_id") or ""
        assert new_oid and new_oid != x, (
            f"A2: server must REGENERATE order_id; "
            f"original={x!r}, returned={new_oid!r}"
        )
        # And the regenerated value must be reflected on master too.
        assert new_moid == new_oid, (
            f"A2: regenerated master_order_id must equal new order_id; "
            f"master={new_moid!r}, order={new_oid!r}"
        )

    def test_A3_explicit_user_order_id_collision_returns_409(
        self, api, user_auth, user_courier_id,
    ):
        """A3 — A user-typed order_id (NOT matching master_order_id)
        that already exists for the same user MUST still return 409
        with a helpful message — no silent regeneration of user input."""
        oid = f"TEST_REV4_TYPED_{uuid.uuid4().hex[:8].upper()}"

        # First insert: must succeed.
        p1 = _shipment_payload(user_courier_id, order_id=oid)
        r1 = api.post(
            f"{BASE_URL}/api/shipments",
            headers=user_auth["headers"],
            json=p1, timeout=60,
        )
        assert r1.status_code == 200, (
            f"A3 seed insert failed: {r1.status_code} {r1.text[:300]}"
        )

        # Second insert: same explicit order_id → must 409 (not retry).
        p2 = _shipment_payload(user_courier_id, order_id=oid)
        r2 = api.post(
            f"{BASE_URL}/api/shipments",
            headers=user_auth["headers"],
            json=p2, timeout=60,
        )
        assert r2.status_code == 409, (
            f"A3: explicit duplicate must 409; got "
            f"{r2.status_code} {r2.text[:400]}"
        )
        detail = (r2.json().get("detail") or "")
        assert oid in detail, (
            f"A3: 409 detail must mention the colliding id {oid!r}; "
            f"got: {detail!r}"
        )
        lower = detail.lower()
        assert "already exists" in lower, (
            f"A3: 409 must contain the user-friendly phrase 'already exists'; "
            f"got: {detail!r}"
        )


# ════════════════ B) POST /orders/pending/{id}/ship autogen retry ═══
class TestShipPendingAutogenRetry:
    """B1, B2, B3 from the review request."""

    def _smart_paste_pending(self, api, headers, label=""):
        unique = uuid.uuid4().hex[:6].upper()
        paste = (
            f"Name: TEST_REV4_PEND_{label}_{unique}\n"
            "Phone: 9999988888\n"
            "Address: 1 Pending Retry Lane\n"
            "City: Mumbai\n"
            "State: Maharashtra\n"
            "Pincode: 400001\n"
            "Item: Widget\n"
            "Amount: 500\n"
            "Payment: COD"
        )
        r = api.post(
            f"{BASE_URL}/api/smart-paste",
            headers=headers,
            json={"text": paste, "skip_llm": True},
            timeout=60,
        )
        assert r.status_code == 200, (
            f"smart-paste failed: {r.status_code} {r.text[:300]}"
        )
        return r.json()

    def test_B1_pending_ship_happy_path(
        self, api, user_auth, user_courier_id,
    ):
        """B1 — Smart-Paste → ship pending order. Must 200."""
        pending = self._smart_paste_pending(
            api, user_auth["headers"], label="B1",
        )
        pid = pending["id"]
        r = api.post(
            f"{BASE_URL}/api/orders/pending/{pid}/ship",
            headers=user_auth["headers"],
            json={"courier_id": user_courier_id},
            timeout=60,
        )
        assert r.status_code == 200, (
            f"B1 ship failed: {r.status_code} {r.text[:300]}"
        )
        body = r.json()
        assert body.get("tracking_id")
        assert body.get("order_id")
        assert body.get("master_order_id")

    def test_B2_pending_autogen_collision_silently_retries(
        self, api, user_auth, user_courier_id, mongo,
    ):
        """B2 — Pre-seed a shipment with order_id=X for user2, then
        create a pending order whose master_order_id == order_id == X
        (i.e., autogen path). POST /ship MUST 200 with a regenerated
        id (different from X), and the pending row's master_order_id
        / order_id MUST be updated in Mongo."""
        uid = user_auth["user_id"]
        x = _autogen_style_id()

        # Seed shipment row holding order_id=X for user2.
        seed_id = str(uuid.uuid4())
        mongo.shipments.insert_one({
            "id": seed_id,
            "order_id": x,
            "master_order_id": "",
            "user_id": uid,
            "customer_name": "TEST_REV4_SEED_B2",
            "tracking_id": f"SEED-{uuid.uuid4().hex[:6]}",
            "courier_id": user_courier_id,
            "courier_name": "Seed",
            "status": "shipped",
            "amount": 0.0, "cod_amount": 0.0, "token_amount": 0.0,
            "items": [], "item_description": "",
            "payment_mode": "Prepaid",
        })

        # Create pending via smart-paste, then patch to autogen state
        # (master_order_id == order_id == X).
        pending = self._smart_paste_pending(
            api, user_auth["headers"], label="B2",
        )
        pid = pending["id"]
        upd = mongo.pending_orders.update_one(
            {"id": pid, "user_id": uid},
            {"$set": {
                "order_id": x,
                "master_order_id": x,
                "order_id_hint": "",
            }},
        )
        assert upd.matched_count == 1, (
            f"B2: could not patch pending {pid} to autogen-collision state"
        )

        r = api.post(
            f"{BASE_URL}/api/orders/pending/{pid}/ship",
            headers=user_auth["headers"],
            json={"courier_id": user_courier_id},
            timeout=60,
        )

        # Cleanup seed regardless of outcome
        try:
            mongo.shipments.delete_one({"id": seed_id})
        except Exception:
            pass

        assert r.status_code != 409, (
            f"B2: autogen collision MUST NOT 409; got 409 detail: "
            f"{r.text[:400]}"
        )
        assert r.status_code != 500, (
            f"B2: autogen collision MUST NOT 500; got: {r.text[:400]}"
        )
        assert r.status_code == 200, (
            f"B2: expected 200 after silent retry; got "
            f"{r.status_code} {r.text[:400]}"
        )
        body = r.json()
        new_oid = body.get("order_id") or ""
        new_moid = body.get("master_order_id") or ""
        assert new_oid and new_oid != x, (
            f"B2: shipment must REGENERATE order_id; "
            f"colliding={x!r}, returned={new_oid!r}"
        )
        assert new_moid == new_oid, (
            f"B2: regenerated master_order_id must equal new order_id; "
            f"master={new_moid!r}, order={new_oid!r}"
        )

        # Verify pending row in Mongo was updated to the new ids.
        prow = mongo.pending_orders.find_one(
            {"id": pid, "user_id": uid}, {"_id": 0},
        )
        assert prow is not None, (
            "B2: pending row vanished — should still be present (or "
            "marked shipped) with updated ids"
        )
        # Either order_id was bumped on the pending row, or it was
        # marked shipped — either way, the stale X must NOT remain.
        assert prow.get("order_id") != x, (
            f"B2: pending.order_id should have been updated away from "
            f"{x!r}; got {prow.get('order_id')!r}"
        )
        assert prow.get("master_order_id") != x, (
            f"B2: pending.master_order_id should have been updated away "
            f"from {x!r}; got {prow.get('master_order_id')!r}"
        )

    def test_B3_pending_explicit_collision_returns_409(
        self, api, user_auth, user_courier_id, mongo,
    ):
        """B3 — Pending row with USER-TYPED order_id (different from
        master_order_id) that collides with an existing shipment must
        STILL surface 409 — no silent retry on user input."""
        uid = user_auth["user_id"]
        typed_oid = f"TEST_REV4_PEND_TYPED_{uuid.uuid4().hex[:8].upper()}"

        # 1) Seed a shipment that owns the typed order_id.
        p1 = _shipment_payload(user_courier_id, order_id=typed_oid)
        r1 = api.post(
            f"{BASE_URL}/api/shipments",
            headers=user_auth["headers"],
            json=p1, timeout=60,
        )
        assert r1.status_code == 200, (
            f"B3 seed shipment failed: {r1.status_code} {r1.text[:300]}"
        )

        # 2) Smart-paste a pending row; then force its order_id to the
        #    typed colliding value while keeping master_order_id distinct
        #    (so the autogen-check evaluates False).
        pending = self._smart_paste_pending(
            api, user_auth["headers"], label="B3",
        )
        pid = pending["id"]
        distinct_master = _autogen_style_id()  # different from typed_oid
        upd = mongo.pending_orders.update_one(
            {"id": pid, "user_id": uid},
            {"$set": {
                "order_id": typed_oid,
                "order_id_hint": typed_oid,
                "master_order_id": distinct_master,
            }},
        )
        assert upd.matched_count == 1

        # 3) Ship → must 409 (NOT retried).
        r = api.post(
            f"{BASE_URL}/api/orders/pending/{pid}/ship",
            headers=user_auth["headers"],
            json={"courier_id": user_courier_id},
            timeout=60,
        )
        assert r.status_code != 500, (
            f"B3: explicit pending dup MUST NOT 500; got: {r.text[:400]}"
        )
        assert r.status_code == 409, (
            f"B3: explicit pending dup must 409; got "
            f"{r.status_code} {r.text[:400]}"
        )
        detail = (r.json().get("detail") or "")
        assert typed_oid in detail, (
            f"B3: 409 detail must mention the colliding id "
            f"{typed_oid!r}; got: {detail!r}"
        )
