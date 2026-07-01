"""
Phase F4.5 — Sheet-Row Dedupe backend tests.

Verifies:
  A. Fresh smart-paste insert (with sheet_row_key + order_id) works.
  B. Duplicate sheet_row_key → skipped: true + reason=duplicate_sheet_row,
     and an audit row lands in the `import_log` collection.
  C. Duplicate order_id (different sheet_row_key) → also skipped.
  D. Empty sheet_row_key + empty order_id → dedup does NOT fire, two
     separate rows are created.
  E. Multi-user isolation.
  F. MongoDB unique partial index `uniq_user_sheetRowKey` exists.
  G. Regression on /api/shipments GET.
"""
import os
import uuid
import asyncio
import pytest
import requests
from typing import Dict, Any, List

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    # Fall back to EXPO_BACKEND_URL if set (spec name).
    BASE_URL = os.environ.get("EXPO_BACKEND_URL", "").rstrip("/")

# Mongo config for direct index + import_log verification.
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "test_database")

SP_ENDPOINT = "/api/smart-paste"


# --------------------------------------------------------------------------- #
#                                Fixtures                                     #
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="module")
def admin_token() -> str:
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": "admin@test.com", "password": "Admin@12345"},
        timeout=15,
    )
    assert r.status_code == 200, f"admin login failed: {r.status_code} {r.text}"
    return r.json()["token"]


@pytest.fixture(scope="module")
def admin_user_id(admin_token: str) -> str:
    r = requests.get(
        f"{BASE_URL}/api/auth/me",
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=15,
    )
    assert r.status_code == 200
    return r.json()["id"]


@pytest.fixture(scope="module")
def second_user_token() -> str:
    """Create a fresh user for multi-tenant isolation test."""
    email = f"TEST_dedupe_{uuid.uuid4().hex[:8]}@example.com"
    password = "Test@12345"
    r = requests.post(
        f"{BASE_URL}/api/auth/signup",
        json={
            "email": email,
            "password": password,
            "name": "TEST Dedupe User",
            "shop_name": "TEST Shop",
            "phone": f"9{uuid.uuid4().int % 10**9:09d}",
        },
        timeout=15,
    )
    if r.status_code == 200:
        return r.json()["token"]
    # If signup already exists, try login
    r2 = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": email, "password": password},
        timeout=15,
    )
    assert r2.status_code == 200, f"user2 auth failed: {r.status_code} {r.text}"
    return r2.json()["token"]


@pytest.fixture(scope="module")
def rand_tag() -> str:
    return uuid.uuid4().hex[:10]


# --------------------------------------------------------------------------- #
#                             Helpers                                         #
# --------------------------------------------------------------------------- #

def _auth(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _make_paste_text(
    *,
    order_id: str,
    name: str = "TEST Buyer",
    phone: str = "9876543210",
) -> str:
    return (
        f"Name: {name}\n"
        f"Phone: {phone}\n"
        f"Address: 12 Test Lane\n"
        f"City: Mumbai\n"
        f"State: Maharashtra\n"
        f"Pincode: 400001\n"
        f"Item: TEST widget\n"
        f"Amount: 199\n"
        f"Payment: COD\n"
        f"Order ID: {order_id}\n"
    )


def _post_smart_paste(
    token: str,
    *,
    text: str,
    sheet_row_key: str = "",
    order_id: str = "",
    skip_llm: bool = True,
) -> requests.Response:
    """Send a smart-paste request.

    We pass sheet_row_key & order_id as EXTRA top-level fields so that
    if the backend accepts them (Phase F4.5 contract) it can dedupe on
    those. If the model ignores them, dedup will only trigger via the
    order_id embedded in the pasted text.
    """
    body: Dict[str, Any] = {
        "text": text,
        "skip_llm": skip_llm,
    }
    if sheet_row_key:
        body["sheet_row_key"] = sheet_row_key
    if order_id:
        body["order_id"] = order_id
    return requests.post(
        f"{BASE_URL}{SP_ENDPOINT}",
        headers=_auth(token),
        json=body,
        timeout=45,
    )


def _get_pending(token: str) -> List[Dict[str, Any]]:
    r = requests.get(
        f"{BASE_URL}/api/orders/pending?limit=200",
        headers=_auth(token),
        timeout=20,
    )
    assert r.status_code == 200, f"pending list failed: {r.status_code} {r.text}"
    data = r.json()
    if isinstance(data, dict) and "items" in data:
        return data["items"]
    return data if isinstance(data, list) else []


# --------------------------------------------------------------------------- #
#                          Test A — Fresh insert                              #
# --------------------------------------------------------------------------- #

class TestA_FreshInsert:
    def test_a1_fresh_smart_paste_creates_row(
        self, admin_token, rand_tag, request,
    ):
        sheet_row_key = f"ROWKEY_A1_{rand_tag}"
        order_id = f"ORDA1_{rand_tag}"
        request.config.cache.set("f45/rowkey_a1", sheet_row_key)
        request.config.cache.set("f45/order_a1", order_id)

        r = _post_smart_paste(
            admin_token,
            text=_make_paste_text(order_id=order_id),
            sheet_row_key=sheet_row_key,
            order_id=order_id,
        )
        assert r.status_code == 200, f"smart-paste failed: {r.status_code} {r.text}"
        body = r.json()
        assert body.get("skipped") in (None, False), (
            f"fresh insert should not be marked skipped, got: {body!r}"
        )
        assert body.get("id"), f"no id returned: {body!r}"
        request.config.cache.set("f45/id_a1", body["id"])
        request.config.cache.set(
            "f45/sheet_row_key_persisted",
            body.get("sheet_row_key") or "",
        )
        # Diagnostic — dump the freshly-persisted Mongo doc so we can see
        # what order_id / sheet_row_key actually landed.
        try:
            from motor.motor_asyncio import AsyncIOMotorClient
            async def _dump():
                c = AsyncIOMotorClient(MONGO_URL)
                d = c[DB_NAME]
                doc = await d.pending_orders.find_one({"id": body["id"]}, {"_id":0})
                c.close()
                return doc
            doc = asyncio.get_event_loop().run_until_complete(_dump())
            print(f"[A1 diag] persisted doc order_id={doc.get('order_id')!r} "
                  f"sheet_row_key={doc.get('sheet_row_key')!r} "
                  f"master_order_id={doc.get('master_order_id')!r}")
        except Exception as e:
            print(f"[A1 diag] mongo dump failed: {e}")

        # Verify pending list has this row
        pending = _get_pending(admin_token)
        ids = [p.get("id") for p in pending]
        assert body["id"] in ids, "A1 row not found in pending list"


# --------------------------------------------------------------------------- #
#                        Test B — Duplicate sheet_row_key                     #
# --------------------------------------------------------------------------- #

class TestB_DuplicateSheetRowKey:
    def test_b1_duplicate_sheet_row_key_skipped(
        self, admin_token, request,
    ):
        sheet_row_key = request.config.cache.get("f45/rowkey_a1", None)
        order_id_a1 = request.config.cache.get("f45/order_a1", None)
        first_id = request.config.cache.get("f45/id_a1", None)
        persisted_srk = request.config.cache.get("f45/sheet_row_key_persisted", "")
        assert sheet_row_key and first_id, "A1 must have run first"

        # Repeat A1 exactly: same sheet_row_key + same order_id + same paste text.
        r = _post_smart_paste(
            admin_token,
            text=_make_paste_text(order_id=order_id_a1),
            sheet_row_key=sheet_row_key,
            order_id=order_id_a1,
        )
        assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text}"
        body = r.json()

        # If sheet_row_key was persisted on A1, the pre-check should fire
        # via sheet_row_key. Even if not, order_id embedded in the text
        # should trigger dedup via the order_id branch.
        assert body.get("skipped") is True, (
            f"B1 expected skipped=true; got body={body!r}. "
            f"Persisted sheet_row_key on A1: {persisted_srk!r}"
        )
        assert body.get("skip_reason") == "duplicate_sheet_row", (
            f"B1 unexpected skip_reason: {body.get('skip_reason')!r}"
        )
        assert body.get("id") == first_id, (
            f"B1 id mismatch: dedup returned {body.get('id')!r}, expected {first_id!r}"
        )

    def test_b2_pending_still_has_single_row(
        self, admin_token, request,
    ):
        sheet_row_key = request.config.cache.get("f45/rowkey_a1", None)
        order_id_a1 = request.config.cache.get("f45/order_a1", None)
        assert sheet_row_key and order_id_a1

        pending = _get_pending(admin_token)
        # Filter by either sheet_row_key or order_id
        matches = [
            p for p in pending
            if p.get("sheet_row_key") == sheet_row_key
            or p.get("order_id") == order_id_a1
        ]
        assert len(matches) == 1, (
            f"expected exactly 1 pending row for A1's identifiers, got {len(matches)}"
        )

    def test_b3_import_log_row_exists(self, admin_user_id, request):
        """Query import_log collection directly."""
        try:
            from motor.motor_asyncio import AsyncIOMotorClient
        except ImportError:
            pytest.skip("motor not installed")

        sheet_row_key = request.config.cache.get("f45/rowkey_a1", None)
        order_id_a1 = request.config.cache.get("f45/order_a1", None)
        first_id = request.config.cache.get("f45/id_a1", None)

        async def _query():
            client = AsyncIOMotorClient(MONGO_URL)
            db = client[DB_NAME]
            docs = await db.import_log.find(
                {"user_id": admin_user_id, "reason": "duplicate_sheet_row"},
                {"_id": 0},
            ).to_list(length=200)
            client.close()
            return docs

        docs = asyncio.get_event_loop().run_until_complete(_query())
        # Find the row that matches this test's identifiers
        match = None
        for d in docs:
            if (d.get("sheet_row_key") == sheet_row_key
                    or d.get("order_id") == order_id_a1
                    or d.get("existing_pending_id") == first_id):
                match = d
                break
        assert match, (
            f"no import_log row found for A1. Fields we searched: "
            f"sheet_row_key={sheet_row_key!r}, order_id={order_id_a1!r}, "
            f"existing_pending_id={first_id!r}. Docs seen: {docs[:3]}"
        )
        assert match["reason"] == "duplicate_sheet_row"
        assert match.get("existing_pending_id"), (
            f"existing_pending_id missing on log row: {match!r}"
        )


# --------------------------------------------------------------------------- #
#         Test C — Duplicate order_id but different sheet_row_key             #
# --------------------------------------------------------------------------- #

class TestC_DuplicateOrderId:
    def test_c1_duplicate_order_id_skipped(self, admin_token, rand_tag, request):
        first_id = request.config.cache.get("f45/id_a1", None)
        order_id_a1 = request.config.cache.get("f45/order_a1", None)
        assert first_id and order_id_a1

        new_row_key = f"ROWKEY_C1_{rand_tag}"
        r = _post_smart_paste(
            admin_token,
            text=_make_paste_text(order_id=order_id_a1),
            sheet_row_key=new_row_key,
            order_id=order_id_a1,
        )
        assert r.status_code == 200, f"C1 expected 200, got {r.status_code}: {r.text}"
        body = r.json()
        assert body.get("skipped") is True, (
            f"C1 expected skipped=true; got body={body!r}"
        )
        assert body.get("skip_reason") == "duplicate_sheet_row", (
            f"C1 unexpected skip_reason: {body.get('skip_reason')!r}"
        )
        assert body.get("id") == first_id, (
            f"C1 dedup should have matched via order_id; got id={body.get('id')!r}, "
            f"expected {first_id!r}"
        )

    def test_c2_no_new_row_for_new_srk(self, admin_token, rand_tag):
        pending = _get_pending(admin_token)
        matches = [
            p for p in pending
            if p.get("sheet_row_key", "").startswith("ROWKEY_C1_")
        ]
        assert len(matches) == 0, (
            f"C1 should NOT have created a new pending row; found {len(matches)}"
        )


# --------------------------------------------------------------------------- #
#             Test D — Missing sheet_row_key AND order_id                     #
# --------------------------------------------------------------------------- #

class TestD_NoDedupWhenBothEmpty:
    def test_d1_two_inserts_create_two_rows(self, admin_token, rand_tag):
        # Paste text with NO order_id line and no sheet_row_key/order_id field.
        text = (
            "Name: TEST NoKey\n"
            "Phone: 9000011111\n"
            "Address: 55 Nowhere Rd\n"
            "City: Delhi\n"
            "State: Delhi\n"
            "Pincode: 110001\n"
            "Item: TEST bulk item " + rand_tag + "\n"
            "Amount: 88\n"
            "Payment: COD\n"
        )
        r1 = _post_smart_paste(admin_token, text=text)
        assert r1.status_code == 200, f"D1a failed: {r1.status_code} {r1.text}"
        b1 = r1.json()
        assert b1.get("skipped") in (None, False), (
            f"D1a should not skip; got {b1!r}"
        )

        r2 = _post_smart_paste(admin_token, text=text)
        assert r2.status_code == 200, f"D1b failed: {r2.status_code} {r2.text}"
        b2 = r2.json()
        assert b2.get("skipped") in (None, False), (
            f"D1b should not skip when both keys empty; got {b2!r}"
        )
        assert b1["id"] != b2["id"], (
            "D1 both inserts returned the same id — dedup fired when it should not"
        )


# --------------------------------------------------------------------------- #
#                    Test E — Multi-user isolation                            #
# --------------------------------------------------------------------------- #

class TestE_MultiUserIsolation:
    def test_e1_second_user_can_reuse_admins_identifiers(
        self, second_user_token, request,
    ):
        sheet_row_key = request.config.cache.get("f45/rowkey_a1", None)
        order_id_a1 = request.config.cache.get("f45/order_a1", None)
        assert sheet_row_key and order_id_a1

        r = _post_smart_paste(
            second_user_token,
            text=_make_paste_text(order_id=order_id_a1),
            sheet_row_key=sheet_row_key,
            order_id=order_id_a1,
        )
        assert r.status_code == 200, f"E1 failed: {r.status_code} {r.text}"
        body = r.json()
        assert body.get("skipped") in (None, False), (
            f"E1: user2 should NOT collide with user1; got {body!r}"
        )

        # user2 should now have exactly 1 pending row for this identifier
        pending2 = _get_pending(second_user_token)
        assert len(pending2) >= 1, "user2 should have at least the just-created row"


# --------------------------------------------------------------------------- #
#             Test F — Unique partial index actually exists                   #
# --------------------------------------------------------------------------- #

class TestF_IndexExists:
    def test_f1_uniq_user_sheetRowKey_index(self):
        try:
            from motor.motor_asyncio import AsyncIOMotorClient
        except ImportError:
            pytest.skip("motor not installed")

        async def _get_indexes():
            client = AsyncIOMotorClient(MONGO_URL)
            db = client[DB_NAME]
            info = await db.pending_orders.index_information()
            client.close()
            return info

        info = asyncio.get_event_loop().run_until_complete(_get_indexes())
        assert "uniq_user_sheetRowKey" in info, (
            f"expected `uniq_user_sheetRowKey` index; existing: {list(info.keys())}"
        )
        spec = info["uniq_user_sheetRowKey"]
        # `key` should be [("user_id", 1), ("sheet_row_key", 1)]
        assert list(spec.get("key")) == [("user_id", 1), ("sheet_row_key", 1)], (
            f"unexpected key spec: {spec.get('key')!r}"
        )
        assert spec.get("unique") is True, f"index not unique: {spec!r}"
        pfe = spec.get("partialFilterExpression") or {}
        assert "sheet_row_key" in pfe, f"partial filter missing sheet_row_key: {pfe!r}"
        srk_filter = pfe["sheet_row_key"]
        assert srk_filter.get("$exists") is True, f"unexpected $exists: {srk_filter!r}"
        assert srk_filter.get("$gt") == "", f"unexpected $gt: {srk_filter!r}"


# --------------------------------------------------------------------------- #
#                       Test G — Regression                                   #
# --------------------------------------------------------------------------- #

class TestG_Regression:
    def test_g1_shipments_list_still_works(self, admin_token):
        r = requests.get(
            f"{BASE_URL}/api/shipments?limit=5",
            headers=_auth(admin_token),
            timeout=20,
        )
        assert r.status_code == 200, f"shipments list broke: {r.status_code} {r.text}"


# --------------------------------------------------------------------------- #
#                          Cleanup                                            #
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="module", autouse=True)
def _cleanup_test_data(admin_user_id):
    """After all tests, delete any TEST_ pending orders + import_log rows we created."""
    yield
    try:
        from motor.motor_asyncio import AsyncIOMotorClient
    except ImportError:
        return

    async def _clean():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        await db.pending_orders.delete_many({
            "user_id": admin_user_id,
            "$or": [
                {"customer_name": {"$regex": "^TEST "}},
                {"sheet_row_key": {"$regex": "^ROWKEY_"}},
                {"order_id": {"$regex": "^ORDA1_"}},
            ],
        })
        await db.import_log.delete_many({
            "user_id": admin_user_id,
            "$or": [
                {"sheet_row_key": {"$regex": "^ROWKEY_"}},
                {"order_id": {"$regex": "^ORDA1_"}},
            ],
        })
        client.close()

    try:
        asyncio.get_event_loop().run_until_complete(_clean())
    except Exception:
        pass
