"""
Phase F11.J — Coupon Analytics endpoint reshape.

Validates that GET /api/admin/coupons/analytics emits the new canonical
shape { totals: {...}, coupons: [...] } while preserving legacy fields
(total_used, active, top5, total_coupons, status_counts).

Auth guard is verified for non-admin users.
"""
import os
import uuid
import pytest
import requests

try:
    # Optional: direct DB access to seed used_count (bypass API strict update)
    from pymongo import MongoClient  # type: ignore
    _MONGO = MongoClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    _DB = _MONGO[os.environ.get("DB_NAME", "test_database")]
except Exception:
    _MONGO = None
    _DB = None

BASE_URL = (
    os.environ.get("EXPO_PUBLIC_BACKEND_URL")
    or os.environ.get("EXPO_BACKEND_URL")
    or "https://logistics-hub-740.preview.emergentagent.com"
).rstrip("/")

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASSWORD = "Admin@12345"
USER_EMAIL = "user2@test.com"
USER_PASSWORD = "User@12345"


# ─────────────────────────── Fixtures ────────────────────────────

@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=30,
    )
    assert r.status_code == 200, f"admin login failed: {r.status_code} {r.text}"
    return r.json()["token"]


@pytest.fixture(scope="module")
def user_token():
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": USER_EMAIL, "password": USER_PASSWORD},
        timeout=30,
    )
    assert r.status_code == 200, f"user2 login failed: {r.status_code} {r.text}"
    return r.json()["token"]


@pytest.fixture(scope="module")
def admin_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"}


@pytest.fixture(scope="module")
def user_headers(user_token):
    return {"Authorization": f"Bearer {user_token}", "Content-Type": "application/json"}


# ─────────────────────────── Helpers ─────────────────────────────

def _create_coupon(headers, code, discount_type, discount_value, min_order=0, used_count=0):
    """Create a coupon and (optionally) hack used_count via update to simulate usage."""
    payload = {
        "code": code,
        "label": f"Test coupon {code}",
        "discount_type": discount_type,
        "discount_value": discount_value,
        "min_order_amount": min_order,
        "max_uses": 100,
        "status": "active",
        "valid_from": "2020-01-01T00:00:00+00:00",
        "valid_to":   "2099-12-31T23:59:59+00:00",
    }
    r = requests.post(
        f"{BASE_URL}/api/admin/coupons", json=payload, headers=headers, timeout=30
    )
    assert r.status_code in (200, 201), f"create failed: {r.status_code} {r.text}"
    coupon = r.json()["coupon"]
    return coupon


def _delete_coupon(headers, coupon_id):
    requests.delete(
        f"{BASE_URL}/api/admin/coupons/{coupon_id}", headers=headers, timeout=30
    )


# ─────────────────────────── Tests ───────────────────────────────

class TestCouponAnalyticsShape:
    """Response shape + backward-compat contract."""

    def test_endpoint_reachable_returns_200(self, admin_headers):
        r = requests.get(
            f"{BASE_URL}/api/admin/coupons/analytics",
            headers=admin_headers,
            timeout=30,
        )
        assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text}"

    def test_canonical_totals_object_present(self, admin_headers):
        r = requests.get(
            f"{BASE_URL}/api/admin/coupons/analytics",
            headers=admin_headers,
            timeout=30,
        )
        data = r.json()
        assert "totals" in data, "missing top-level 'totals' object"
        totals = data["totals"]
        assert isinstance(totals, dict), "'totals' must be a dict"
        for k in ("redemptions", "total_discount", "total_revenue"):
            assert k in totals, f"totals missing key {k}"
            assert isinstance(totals[k], (int, float)), f"totals.{k} not numeric"

    def test_canonical_coupons_array_present(self, admin_headers):
        r = requests.get(
            f"{BASE_URL}/api/admin/coupons/analytics",
            headers=admin_headers,
            timeout=30,
        )
        data = r.json()
        assert "coupons" in data, "missing top-level 'coupons' array"
        assert isinstance(data["coupons"], list), "'coupons' must be an array"

    def test_each_coupon_row_has_required_fields(self, admin_headers):
        # ensure at least one coupon exists for row-shape verification
        code = f"TEST_SHAPE_{uuid.uuid4().hex[:6].upper()}"
        created = _create_coupon(admin_headers, code, "flat", 50)
        try:
            r = requests.get(
                f"{BASE_URL}/api/admin/coupons/analytics",
                headers=admin_headers,
                timeout=30,
            )
            data = r.json()
            match = next((c for c in data["coupons"] if c.get("code") == code), None)
            assert match is not None, "created coupon not in analytics rows"
            for k in ("code", "redemptions", "total_discount", "total_revenue", "status"):
                assert k in match, f"row missing key {k}"
        finally:
            _delete_coupon(admin_headers, created["id"])

    def test_legacy_fields_still_present(self, admin_headers):
        r = requests.get(
            f"{BASE_URL}/api/admin/coupons/analytics",
            headers=admin_headers,
            timeout=30,
        )
        data = r.json()
        for k in ("total_used", "active", "top5", "total_coupons", "status_counts"):
            assert k in data, f"legacy field missing: {k}"
        assert isinstance(data["top5"], list)
        assert isinstance(data["status_counts"], dict)
        assert isinstance(data["total_coupons"], int)


class TestAuthGuard:
    """/admin/coupons/analytics must be admin-only."""

    def test_non_admin_forbidden(self, user_headers):
        r = requests.get(
            f"{BASE_URL}/api/admin/coupons/analytics",
            headers=user_headers,
            timeout=30,
        )
        assert r.status_code in (401, 403), (
            f"non-admin got {r.status_code}, expected 401/403"
        )

    def test_no_auth_rejected(self):
        r = requests.get(
            f"{BASE_URL}/api/admin/coupons/analytics", timeout=30
        )
        assert r.status_code in (401, 403), (
            f"unauth got {r.status_code}, expected 401/403"
        )


class TestConsistencyAndMath:
    """Aggregation math + used_count → redemptions mapping."""

    def test_totals_redemptions_equals_sum_of_rows(self, admin_headers):
        r = requests.get(
            f"{BASE_URL}/api/admin/coupons/analytics",
            headers=admin_headers,
            timeout=30,
        )
        data = r.json()
        row_sum = sum(int(c.get("redemptions") or 0) for c in data["coupons"])
        assert data["totals"]["redemptions"] == row_sum, (
            f"totals.redemptions={data['totals']['redemptions']} "
            f"row_sum={row_sum}"
        )

    def test_total_used_legacy_matches_new_totals(self, admin_headers):
        r = requests.get(
            f"{BASE_URL}/api/admin/coupons/analytics",
            headers=admin_headers,
            timeout=30,
        )
        data = r.json()
        assert data["total_used"] == data["totals"]["redemptions"], (
            "legacy total_used must equal new totals.redemptions"
        )

    def test_flat_100_used_3_gives_row_discount_300(self, admin_headers):
        """Per requirement: discount_type='flat', discount_value=100, used_count=3
        → row.total_discount == 300."""
        code = f"TEST_FLAT_{uuid.uuid4().hex[:6].upper()}"
        created = _create_coupon(admin_headers, code, "flat", 100)
        try:
            # Seed used_count directly in Mongo (CouponUpdate excludes used_count
            # by design — it is bumped only via payment-verify path in prod).
            if _DB is None:
                pytest.skip("pymongo unavailable — cannot seed used_count")
            res = _DB.coupons.update_one({"id": created["id"]}, {"$set": {"used_count": 3}})
            assert res.modified_count == 1, "failed to seed used_count in DB"
            r = requests.get(
                f"{BASE_URL}/api/admin/coupons/analytics",
                headers=admin_headers,
                timeout=30,
            )
            data = r.json()
            row = next((c for c in data["coupons"] if c.get("code") == code), None)
            assert row is not None, "seeded coupon missing from analytics"
            assert row["redemptions"] == 3, f"redemptions={row['redemptions']}"
            assert row["total_discount"] == 300, (
                f"expected 300, got {row['total_discount']}"
            )
        finally:
            _delete_coupon(admin_headers, created["id"])

    def test_used_count_maps_to_redemptions(self, admin_headers):
        code = f"TEST_MAP_{uuid.uuid4().hex[:6].upper()}"
        created = _create_coupon(admin_headers, code, "flat", 25)
        try:
            if _DB is None:
                pytest.skip("pymongo unavailable — cannot seed used_count")
            res = _DB.coupons.update_one({"id": created["id"]}, {"$set": {"used_count": 7}})
            assert res.modified_count == 1
            r = requests.get(
                f"{BASE_URL}/api/admin/coupons/analytics",
                headers=admin_headers,
                timeout=30,
            )
            row = next(
                (c for c in r.json()["coupons"] if c.get("code") == code), None
            )
            assert row is not None
            assert row["redemptions"] == 7, "used_count must map to redemptions"
        finally:
            _delete_coupon(admin_headers, created["id"])


class TestEmptyStateShape:
    """Even with zero rows the canonical shape must be present.
    We cannot truncate the DB, but we validate the *shape* is emitted
    regardless of row count (numeric zero-defaults, empty list allowed).
    """

    def test_shape_is_deterministic_regardless_of_rows(self, admin_headers):
        r = requests.get(
            f"{BASE_URL}/api/admin/coupons/analytics",
            headers=admin_headers,
            timeout=30,
        )
        data = r.json()
        # Even if coupons list is populated, verify the shape keys are always emitted
        assert isinstance(data.get("totals"), dict)
        assert isinstance(data.get("coupons"), list)
        # If coupons list happens to be empty in a fresh env, verify zeros
        if len(data["coupons"]) == 0:
            assert data["totals"]["redemptions"] == 0
            assert data["totals"]["total_discount"] == 0
            assert data["totals"]["total_revenue"] == 0
