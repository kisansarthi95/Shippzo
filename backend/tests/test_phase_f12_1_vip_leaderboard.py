"""Phase F12.1 — VIP Leaderboard backend tests.

Endpoints under test:
  GET /api/me/audience/stats            — must expose a `vip` key
  GET /api/me/audience?segment=vip      — new ranked segment

Behavioural contract (see /app/backend/routers/audience.py):
  - stats.vip = count of customers with delivered_count >= 1 AND total_sales > 0
  - segment=vip filters to the same predicate, then sorts by total_sales DESC
  - each row in VIP segment carries a 1-based `rank`
  - non-VIP segments MUST NOT carry a `rank` field
  - pagination (offset/limit) preserves the global 1-based rank
  - invalid segment name -> 422
  - unauthenticated -> 401/403
  - multi-tenant isolation preserved
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
import requests

BASE_URL = os.environ.get(
    "EXPO_PUBLIC_BACKEND_URL",
    os.environ.get("EXPO_BACKEND_URL", ""),
).rstrip("/")

if not BASE_URL:
    raise RuntimeError("EXPO_PUBLIC_BACKEND_URL / EXPO_BACKEND_URL must be set")

ADMIN = {"email": "admin@test.com", "password": "Admin@12345"}
USER2 = {"email": "user2@test.com", "password": "User@12345"}


# ------------------------------ helpers / fixtures ------------------------------

def _login(creds):
    r = requests.post(f"{BASE_URL}/api/auth/login", json=creds, timeout=30)
    assert r.status_code == 200, (
        f"login failed for {creds['email']}: {r.status_code} {r.text[:200]}"
    )
    return r.json()["token"]


def _hdr(tok):
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}


@pytest.fixture(scope="module")
def admin_token():
    return _login(ADMIN)


@pytest.fixture(scope="module")
def user2_token():
    return _login(USER2)


@pytest.fixture(scope="module")
def seeded_vip_customers(admin_token):
    """Seed a deterministic set of VIP customers under admin.

    We insert shipments directly via Mongo so we can control the status
    and amount without touching the shipments API. The mix contains:

      * TEST_VIP1  — delivered, amount 9000  (top spender)
      * TEST_VIP2  — delivered, amount 5000
      * TEST_VIP3  — delivered, amount 2500
      * TEST_VIP4  — delivered, amount 1000
      * TEST_VIP5  — delivered, amount  500  (lowest of the VIP group)
      * TEST_NONVIP_ZERO  — delivered but amount 0 (must be excluded)
      * TEST_NONVIP_PENDING — pending only, amount 999 (must be excluded)

    Also cleaned up at teardown to avoid polluting the DB.
    """
    from motor.motor_asyncio import AsyncIOMotorClient

    tag = uuid.uuid4().hex[:6]
    # digits-only phones so the /profile tail-match works
    phones = {
        "TEST_VIP1":            f"91000{tag[:5]}",  # ensure last-10 uniqueness
        "TEST_VIP2":            f"92000{tag[:5]}",
        "TEST_VIP3":            f"93000{tag[:5]}",
        "TEST_VIP4":            f"94000{tag[:5]}",
        "TEST_VIP5":            f"95000{tag[:5]}",
        "TEST_NONVIP_ZERO":     f"96000{tag[:5]}",
        "TEST_NONVIP_PENDING":  f"97000{tag[:5]}",
    }
    plan = [
        ("TEST_VIP1",            9000.0, "Delivered"),
        ("TEST_VIP2",            5000.0, "Delivered"),
        ("TEST_VIP3",            2500.0, "Delivered"),
        ("TEST_VIP4",            1000.0, "Delivered"),
        ("TEST_VIP5",             500.0, "Delivered"),
        ("TEST_NONVIP_ZERO",        0.0, "Delivered"),  # zero sales
        ("TEST_NONVIP_PENDING",   999.0, "Pending"),    # not delivered
    ]

    inserted_ids: list[str] = []

    async def _seed():
        cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = cli[os.environ["DB_NAME"]]
        u = await db.users.find_one({"email": ADMIN["email"]})
        assert u, "admin user not found"
        uid = u["id"]
        for label, amount, status in plan:
            sid = str(uuid.uuid4())
            inserted_ids.append(sid)
            await db.shipments.insert_one({
                "id":              sid,
                "user_id":         uid,
                "tracking_id":     f"F121{tag}{label[-3:]}",
                "customer_name":   f"{label}_{tag}",
                "customer_phone":  phones[label],
                "city":            "Ahmedabad",
                "state":           "Gujarat",
                "pincode":         "380001",
                "address":         "VIP seed",
                "payment_mode":    "Prepaid",
                "amount":          amount,
                "status":          status,
                "import_batch_ids": [],
                "created_at":      "2026-01-10T10:00:00+00:00",
            })
        cli.close()

    async def _cleanup():
        cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = cli[os.environ["DB_NAME"]]
        if inserted_ids:
            await db.shipments.delete_many({"id": {"$in": inserted_ids}})
        cli.close()

    asyncio.get_event_loop().run_until_complete(_seed())
    yield {"phones": phones, "tag": tag}
    asyncio.get_event_loop().run_until_complete(_cleanup())


# ─────────────────────────────  AUTH GUARD  ───────────────────────────

class TestVipAuthGuard:
    def test_vip_list_requires_auth(self):
        r = requests.get(f"{BASE_URL}/api/me/audience?segment=vip", timeout=30)
        assert r.status_code in (401, 403), (
            f"expected 401/403, got {r.status_code} {r.text[:200]}"
        )


# ─────────────────────────────  STATS: vip key  ───────────────────────

class TestStatsVipKey:
    def test_stats_has_vip_key(self, admin_token, seeded_vip_customers):
        r = requests.get(f"{BASE_URL}/api/me/audience/stats",
                         headers=_hdr(admin_token), timeout=30)
        assert r.status_code == 200, r.text[:300]
        data = r.json()
        assert "vip" in data, f"stats missing 'vip' key: {list(data.keys())}"
        assert isinstance(data["vip"], int), (
            f"stats.vip must be int, got {type(data['vip']).__name__}"
        )
        # We seeded 5 VIP customers so admin must have >= 5
        assert data["vip"] >= 5, f"stats.vip should be >=5 after seed, got {data['vip']}"

    def test_stats_vip_matches_list_total(self, admin_token, seeded_vip_customers):
        """stats.vip should equal the total count of segment=vip."""
        s = requests.get(f"{BASE_URL}/api/me/audience/stats",
                         headers=_hdr(admin_token), timeout=30).json()
        lst = requests.get(f"{BASE_URL}/api/me/audience?segment=vip&limit=500",
                           headers=_hdr(admin_token), timeout=30).json()
        assert s["vip"] == lst["total"], (
            f"stats.vip ({s['vip']}) != segment=vip total ({lst['total']})"
        )


# ─────────────────────────────  VIP list contract  ────────────────────

class TestVipList:
    def test_vip_segment_only_qualifying_rows(self, admin_token, seeded_vip_customers):
        """VIP rows must all have total_sales > 0 AND delivered_count >= 1."""
        r = requests.get(f"{BASE_URL}/api/me/audience?segment=vip&limit=500",
                         headers=_hdr(admin_token), timeout=30)
        assert r.status_code == 200, r.text[:300]
        j = r.json()
        assert j["segment"] == "vip"
        assert isinstance(j["customers"], list)
        for c in j["customers"]:
            assert c["total_sales"] > 0, (
                f"VIP row with total_sales<=0 leaked: {c}"
            )
            assert c["delivered_count"] >= 1, (
                f"VIP row with delivered_count<1 leaked: {c}"
            )

    def test_vip_excludes_zero_and_pending(self, admin_token, seeded_vip_customers):
        """Explicit: TEST_NONVIP_ZERO / TEST_NONVIP_PENDING must NOT appear."""
        phones = seeded_vip_customers["phones"]
        r = requests.get(f"{BASE_URL}/api/me/audience?segment=vip&limit=500",
                         headers=_hdr(admin_token), timeout=30)
        assert r.status_code == 200
        vip_phones = {c.get("customer_phone") for c in r.json()["customers"]}
        assert phones["TEST_NONVIP_ZERO"] not in vip_phones, (
            "zero-sales delivered customer leaked into VIP segment"
        )
        assert phones["TEST_NONVIP_PENDING"] not in vip_phones, (
            "pending-only customer leaked into VIP segment"
        )

    def test_vip_sorted_desc_by_total_sales(self, admin_token, seeded_vip_customers):
        r = requests.get(f"{BASE_URL}/api/me/audience?segment=vip&limit=500",
                         headers=_hdr(admin_token), timeout=30)
        assert r.status_code == 200
        rows = r.json()["customers"]
        assert len(rows) >= 5
        sales = [c["total_sales"] for c in rows]
        # strictly non-increasing
        for a, b in zip(sales, sales[1:]):
            assert a >= b, (
                f"VIP list not sorted desc by total_sales: {sales}"
            )

    def test_vip_rank_is_1_based_and_contiguous(self, admin_token, seeded_vip_customers):
        r = requests.get(f"{BASE_URL}/api/me/audience?segment=vip&limit=500",
                         headers=_hdr(admin_token), timeout=30)
        assert r.status_code == 200
        rows = r.json()["customers"]
        assert rows, "expected at least 1 VIP customer"
        for i, c in enumerate(rows):
            assert "rank" in c, f"VIP row missing 'rank' field: {c}"
            assert c["rank"] == i + 1, (
                f"expected rank={i+1} at index {i}, got {c['rank']}"
            )

    def test_vip_top_row_is_highest_spender(self, admin_token, seeded_vip_customers):
        """TEST_VIP1 (9000) should sit at rank 1 unless a bigger real spender exists.

        We only assert that the top row's total_sales >= 9000 (our top seed).
        """
        r = requests.get(f"{BASE_URL}/api/me/audience?segment=vip&limit=500",
                         headers=_hdr(admin_token), timeout=30)
        assert r.status_code == 200
        rows = r.json()["customers"]
        assert rows[0]["rank"] == 1
        assert rows[0]["total_sales"] >= 9000.0, (
            f"top VIP row should be >=9000 (our seed max), got {rows[0]['total_sales']}"
        )


# ─────────────────────────────  Rank is VIP-only  ─────────────────────

class TestRankFieldScoping:
    @pytest.mark.parametrize("segment", ["all", "new", "returning", "imported"])
    def test_non_vip_segments_have_no_rank(self, admin_token, seeded_vip_customers, segment):
        r = requests.get(f"{BASE_URL}/api/me/audience?segment={segment}&limit=100",
                         headers=_hdr(admin_token), timeout=30)
        assert r.status_code == 200, r.text[:300]
        for c in r.json()["customers"]:
            assert "rank" not in c, (
                f"segment={segment} customer row unexpectedly has 'rank': {c}"
            )


# ─────────────────────────────  Pagination-aware rank  ────────────────

class TestVipPaginationRanks:
    def test_offset_shifts_rank_base(self, admin_token, seeded_vip_customers):
        # First page: limit=3, offset=0 -> ranks 1,2,3
        first = requests.get(
            f"{BASE_URL}/api/me/audience?segment=vip&limit=3&offset=0",
            headers=_hdr(admin_token), timeout=30,
        )
        assert first.status_code == 200
        page1 = first.json()["customers"]
        assert len(page1) == 3, f"expected 3 rows on page 1, got {len(page1)}"
        assert [c["rank"] for c in page1] == [1, 2, 3]

        # Second page: limit=3, offset=3 -> ranks 4,5,6
        second = requests.get(
            f"{BASE_URL}/api/me/audience?segment=vip&limit=3&offset=3",
            headers=_hdr(admin_token), timeout=30,
        )
        assert second.status_code == 200
        page2 = second.json()["customers"]
        # We seeded 5 VIP rows; if the tenant has more real VIP rows,
        # page 2 will contain up to 3 rows. If it has only exactly 5, page 2 has 2.
        assert len(page2) >= 2, f"expected >=2 rows on page 2, got {len(page2)}"
        expected_ranks = [4 + i for i in range(len(page2))]
        assert [c["rank"] for c in page2] == expected_ranks, (
            f"pagination rank base wrong: {[c['rank'] for c in page2]} vs {expected_ranks}"
        )

        # Sanity: no overlap between pages
        keys1 = {c["key"] for c in page1}
        keys2 = {c["key"] for c in page2}
        assert not (keys1 & keys2), (
            f"pages overlap: {keys1 & keys2}"
        )

    def test_offset_5_limit_5_starts_at_rank_6(self, admin_token, seeded_vip_customers):
        """Exact playbook spec: offset=5&limit=5 -> ranks start at 6."""
        r = requests.get(
            f"{BASE_URL}/api/me/audience?segment=vip&limit=5&offset=5",
            headers=_hdr(admin_token), timeout=30,
        )
        assert r.status_code == 200
        rows = r.json()["customers"]
        if not rows:
            pytest.skip("tenant has <=5 VIP customers; cannot verify offset=5")
        for i, c in enumerate(rows):
            assert c["rank"] == 6 + i, (
                f"expected rank={6+i} got {c['rank']} at offset=5 row {i}"
            )


# ─────────────────────────────  Validation / negative  ────────────────

class TestSegmentValidation:
    def test_invalid_segment_foo_rejected(self, admin_token):
        r = requests.get(f"{BASE_URL}/api/me/audience?segment=foo",
                         headers=_hdr(admin_token), timeout=30)
        assert r.status_code == 422, (
            f"invalid segment must be 422, got {r.status_code} {r.text[:200]}"
        )

    def test_vip_segment_accepted(self, admin_token):
        r = requests.get(f"{BASE_URL}/api/me/audience?segment=vip",
                         headers=_hdr(admin_token), timeout=30)
        assert r.status_code == 200, (
            f"vip segment must be accepted, got {r.status_code} {r.text[:200]}"
        )


# ─────────────────────────────  Multi-tenant isolation  ───────────────

class TestVipTenantIsolation:
    def test_user2_vip_excludes_admin_seeds(
        self, user2_token, seeded_vip_customers
    ):
        phones = seeded_vip_customers["phones"]
        seeded_phones = {phones[k] for k in
                         ("TEST_VIP1", "TEST_VIP2", "TEST_VIP3",
                          "TEST_VIP4", "TEST_VIP5")}
        r = requests.get(f"{BASE_URL}/api/me/audience?segment=vip&limit=500",
                         headers=_hdr(user2_token), timeout=30)
        assert r.status_code == 200
        u2_phones = {c.get("customer_phone") for c in r.json()["customers"]}
        leaked = seeded_phones & u2_phones
        assert not leaked, (
            f"multi-tenant isolation broken: user2 VIP list contains admin seeds: {leaked}"
        )

    def test_vip_stats_isolated(self, admin_token, user2_token, seeded_vip_customers):
        admin_stats = requests.get(f"{BASE_URL}/api/me/audience/stats",
                                   headers=_hdr(admin_token), timeout=30).json()
        user2_stats = requests.get(f"{BASE_URL}/api/me/audience/stats",
                                   headers=_hdr(user2_token), timeout=30).json()
        # Admin has 5 seeded VIPs — must be > user2's vip count unless user2
        # coincidentally has >=5 real VIPs (unlikely for demo tenant).
        # At minimum they must not be identical objects.
        assert "vip" in admin_stats and "vip" in user2_stats
        assert admin_stats != user2_stats, (
            f"admin & user2 stats identical, isolation suspect: {admin_stats}"
        )
