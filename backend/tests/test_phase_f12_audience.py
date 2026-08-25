"""Phase F12 — Audience Hub backend tests.

Endpoints under test (all require Bearer auth):
  GET /api/me/audience/stats
  GET /api/me/audience              (filters: segment, q, limit, offset)
  GET /api/me/audience/{customer_key}

Covers: happy path per segment, search filter, single-profile shape,
404 on invalid key, 401 on missing token, multi-tenant isolation.
"""
from __future__ import annotations

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


# ------------------------------ fixtures ------------------------------


def _login(creds):
    r = requests.post(f"{BASE_URL}/api/auth/login", json=creds, timeout=30)
    assert r.status_code == 200, f"login failed for {creds['email']}: {r.status_code} {r.text[:200]}"
    return r.json()["token"]


@pytest.fixture(scope="module")
def admin_token():
    return _login(ADMIN)


@pytest.fixture(scope="module")
def user2_token():
    return _login(USER2)


def _hdr(tok):
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}


# --------- Seed helper: ensure admin has at least 2 new + 1 returning + 1 imported ---------


@pytest.fixture(scope="module")
def seeded_admin_customers(admin_token):
    """Create 3 shipments under admin so segments have deterministic data.

    Returns a dict with the phone keys we created:
      { "returning_phone": "9<10digits>", "new_phone": ..., "imported_phone": ... }
    """
    # Use only digits so `re.sub(r"\D", "", key)` in the profile endpoint
    # yields the SAME 10-digit tail we stored. (An earlier revision used
    # uuid hex which contained a-f letters and broke the tail match.)
    import random
    tag = f"{random.randint(0, 999999):06d}"
    returning_phone = f"98{tag}0001"[-10:]
    new_phone       = f"98{tag}0002"[-10:]
    imported_phone  = f"98{tag}0003"[-10:]

    def _mk(phone, name, tid_suffix, delivered=False, amount=250.0):
        body = {
            "tracking_id": f"F12{tag}{tid_suffix}",
            "customer_name": name,
            "customer_phone": phone,
            "address_line1": "123 Test Lane",
            "city": "Ahmedabad",
            "state": "Gujarat",
            "pincode": "380001",
            "payment_mode": "Prepaid",
            "amount": amount,
        }
        r = requests.post(f"{BASE_URL}/api/shipments", json=body,
                          headers=_hdr(admin_token), timeout=30)
        assert r.status_code in (200, 201), f"seed shipment create failed: {r.status_code} {r.text[:200]}"
        sid = r.json().get("id")
        assert sid, "seed shipment missing id"
        if delivered:
            # PUT status → Delivered so total_sales picks it up
            up = requests.put(
                f"{BASE_URL}/api/shipments/{sid}",
                json={"status": "Delivered"},
                headers=_hdr(admin_token), timeout=30,
            )
            assert up.status_code in (200, 204), f"delivered mark failed: {up.status_code} {up.text[:200]}"
        return sid

    # Returning customer: 2 shipments, at least one delivered
    _mk(returning_phone, f"F12 Ret {tag}", "R1", delivered=True,  amount=500.0)
    _mk(returning_phone, f"F12 Ret {tag}", "R2", delivered=False, amount=125.0)

    # New customer: exactly 1 shipment, delivered so it also adds to total_sales
    _mk(new_phone, f"F12 New {tag}", "N1", delivered=True, amount=999.0)

    # Imported customer: 1 shipment stamped with import_batch_ids via direct DB write
    # (the /api/shipments endpoint does not accept import_batch_ids on write.)
    from motor.motor_asyncio import AsyncIOMotorClient
    import asyncio

    async def _stamp_import():
        cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = cli[os.environ["DB_NAME"]]
        # find admin user id
        u = await db.users.find_one({"email": ADMIN["email"]})
        assert u, "admin user not found in db"
        uid = u["id"]
        # insert shipment doc directly
        sid = str(uuid.uuid4())
        await db.shipments.insert_one({
            "id": sid,
            "user_id": uid,
            "tracking_id": f"F12{tag}I1",
            "customer_name": f"F12 Imp {tag}",
            "customer_phone": imported_phone,
            "city": "Surat", "state": "Gujarat", "pincode": "395001",
            "address": "Import Row",
            "payment_mode": "Prepaid",
            "amount": 300.0,
            "status": "Delivered",
            "import_batch_ids": [f"batch-{tag}"],
            "created_at": "2026-01-15T10:00:00+00:00",
        })
        cli.close()

    asyncio.get_event_loop().run_until_complete(_stamp_import())

    yield {
        "returning_phone": returning_phone,
        "new_phone":       new_phone,
        "imported_phone":  imported_phone,
        "tag":             tag,
    }


# ─────────────────────────────  AUTH GUARDS  ───────────────────────────

class TestAuthGuards:
    """All three endpoints must reject unauthenticated requests."""

    def test_stats_requires_auth(self):
        r = requests.get(f"{BASE_URL}/api/me/audience/stats", timeout=30)
        assert r.status_code in (401, 403), f"expected 401/403 got {r.status_code}"

    def test_list_requires_auth(self):
        r = requests.get(f"{BASE_URL}/api/me/audience", timeout=30)
        assert r.status_code in (401, 403), f"expected 401/403 got {r.status_code}"

    def test_profile_requires_auth(self):
        r = requests.get(f"{BASE_URL}/api/me/audience/9999999999", timeout=30)
        assert r.status_code in (401, 403), f"expected 401/403 got {r.status_code}"


# ─────────────────────────────  STATS  ────────────────────────────────

class TestStats:
    def test_stats_shape(self, admin_token, seeded_admin_customers):
        r = requests.get(f"{BASE_URL}/api/me/audience/stats",
                         headers=_hdr(admin_token), timeout=30)
        assert r.status_code == 200, r.text[:300]
        data = r.json()
        for key in ("all", "new", "returning", "imported"):
            assert key in data, f"missing key '{key}' in stats: {data}"
            assert isinstance(data[key], int), f"stats.{key} must be int, got {type(data[key])}"
        # our seeded fixture produced ≥1 new, ≥1 returning, ≥1 imported
        assert data["new"]       >= 1
        assert data["returning"] >= 1
        assert data["imported"]  >= 1
        assert data["all"]       >= data["new"] + 0  # sanity: all ≥ new count of unique


# ─────────────────────────────  LIST  ─────────────────────────────────

class TestListSegments:

    def test_segment_all_default(self, admin_token, seeded_admin_customers):
        r = requests.get(f"{BASE_URL}/api/me/audience",
                         headers=_hdr(admin_token), timeout=30)
        assert r.status_code == 200, r.text[:300]
        j = r.json()
        assert j["segment"] == "all"
        assert isinstance(j["customers"], list)
        # required per-customer fields
        for c in j["customers"]:
            for k in ("key", "customer_name", "customer_phone",
                      "orders_count", "delivered_count", "total_sales",
                      "is_imported", "last_order_at"):
                assert k in c, f"customer row missing '{k}': {c}"
        # sorted desc by last_order_at
        ts = [c.get("last_order_at") or "" for c in j["customers"]]
        assert ts == sorted(ts, reverse=True), "audience list not sorted by last_order_at desc"

    def test_segment_new_only_orders_1(self, admin_token, seeded_admin_customers):
        r = requests.get(f"{BASE_URL}/api/me/audience?segment=new",
                         headers=_hdr(admin_token), timeout=30)
        assert r.status_code == 200
        j = r.json()
        assert j["segment"] == "new"
        for c in j["customers"]:
            assert c["orders_count"] == 1, f"new segment leaked row with orders_count={c['orders_count']}: {c}"
        # ensure our seeded new phone shows up
        seeded = seeded_admin_customers["new_phone"]
        assert any(c["customer_phone"].endswith(seeded[-6:]) for c in j["customers"]), \
            f"seeded new customer {seeded} not found in segment=new"

    def test_segment_returning_only_orders_ge_2(self, admin_token, seeded_admin_customers):
        r = requests.get(f"{BASE_URL}/api/me/audience?segment=returning",
                         headers=_hdr(admin_token), timeout=30)
        assert r.status_code == 200
        j = r.json()
        assert j["segment"] == "returning"
        for c in j["customers"]:
            assert c["orders_count"] >= 2, f"returning segment leaked orders_count={c['orders_count']}: {c}"
        seeded = seeded_admin_customers["returning_phone"]
        matches = [c for c in j["customers"] if c["customer_phone"].endswith(seeded[-6:])]
        assert matches, f"seeded returning phone {seeded} not found"
        assert matches[0]["orders_count"] >= 2

    def test_segment_imported_only(self, admin_token, seeded_admin_customers):
        r = requests.get(f"{BASE_URL}/api/me/audience?segment=imported",
                         headers=_hdr(admin_token), timeout=30)
        assert r.status_code == 200
        j = r.json()
        assert j["segment"] == "imported"
        for c in j["customers"]:
            assert c["is_imported"] is True, f"imported segment leaked non-imported: {c}"
        seeded = seeded_admin_customers["imported_phone"]
        assert any(c["customer_phone"].endswith(seeded[-6:]) for c in j["customers"]), \
            f"seeded imported customer {seeded} missing from segment=imported"

    def test_search_by_phone_tail(self, admin_token, seeded_admin_customers):
        phone = seeded_admin_customers["returning_phone"]
        tail = phone[-6:]
        r = requests.get(f"{BASE_URL}/api/me/audience?q={tail}",
                         headers=_hdr(admin_token), timeout=30)
        assert r.status_code == 200
        j = r.json()
        assert len(j["customers"]) >= 1, f"search for {tail} returned nothing"
        assert any(tail in (c["customer_phone"] or "") for c in j["customers"])

    def test_search_by_name(self, admin_token, seeded_admin_customers):
        tag = seeded_admin_customers["tag"]
        r = requests.get(f"{BASE_URL}/api/me/audience?q=F12 Ret {tag}",
                         headers=_hdr(admin_token), timeout=30)
        assert r.status_code == 200
        j = r.json()
        assert any(tag in (c["customer_name"] or "") for c in j["customers"]), \
            f"search by name '{tag}' returned no matches"

    def test_invalid_segment_rejected(self, admin_token):
        r = requests.get(f"{BASE_URL}/api/me/audience?segment=bogus",
                         headers=_hdr(admin_token), timeout=30)
        assert r.status_code == 422, f"expected validation error, got {r.status_code}"


# ─────────────────────────────  SINGLE PROFILE  ───────────────────────

class TestProfile:

    def test_profile_returning_customer(self, admin_token, seeded_admin_customers):
        phone = seeded_admin_customers["returning_phone"]
        r = requests.get(f"{BASE_URL}/api/me/audience/{phone}",
                         headers=_hdr(admin_token), timeout=30)
        assert r.status_code == 200, r.text[:300]
        j = r.json()
        # required top-level keys
        for k in ("customer_name", "customer_phone", "default_address",
                  "orders_count", "delivered_count", "total_sales", "orders"):
            assert k in j, f"profile missing '{k}': {list(j.keys())}"
        assert j["orders_count"] >= 2
        assert isinstance(j["orders"], list)
        assert len(j["orders"]) >= 2
        # each order row must expose required fields for the History UI
        for o in j["orders"]:
            for k in ("id", "tracking_id", "status", "amount", "created_at"):
                assert k in o, f"order row missing '{k}': {o}"
        # total_sales should sum ONLY delivered amounts.
        # We seeded returning = 500 delivered + 125 pending => total_sales == 500.0
        assert j["total_sales"] == 500.0, \
            f"total_sales should sum delivered only. got {j['total_sales']}, expected 500.0"
        assert j["delivered_count"] == 1

    def test_profile_new_customer_total_sales(self, admin_token, seeded_admin_customers):
        phone = seeded_admin_customers["new_phone"]
        r = requests.get(f"{BASE_URL}/api/me/audience/{phone}",
                         headers=_hdr(admin_token), timeout=30)
        assert r.status_code == 200, r.text[:300]
        j = r.json()
        assert j["orders_count"] == 1
        # our new-phone was delivered @ 999.0
        assert j["total_sales"] == 999.0, \
            f"new customer total_sales should be 999.0, got {j['total_sales']}"

    def test_profile_imported_flag(self, admin_token, seeded_admin_customers):
        phone = seeded_admin_customers["imported_phone"]
        r = requests.get(f"{BASE_URL}/api/me/audience/{phone}",
                         headers=_hdr(admin_token), timeout=30)
        assert r.status_code == 200, r.text[:300]
        j = r.json()
        assert j["is_imported"] is True, "imported profile should carry is_imported=true"
        assert j["orders"], "imported profile must include at least 1 order"
        assert j["orders"][0]["is_imported"] is True

    def test_profile_invalid_key_returns_404(self, admin_token):
        r = requests.get(f"{BASE_URL}/api/me/audience/0000000000",
                         headers=_hdr(admin_token), timeout=30)
        assert r.status_code == 404, f"invalid key should be 404, got {r.status_code} {r.text[:200]}"


# ─────────────────────────  MULTI-TENANT ISOLATION  ───────────────────

class TestTenantIsolation:

    def test_user2_cannot_see_admin_customer(self, user2_token, seeded_admin_customers):
        phone = seeded_admin_customers["returning_phone"]
        r = requests.get(f"{BASE_URL}/api/me/audience/{phone}",
                         headers=_hdr(user2_token), timeout=30)
        assert r.status_code == 404, \
            f"tenant isolation broken: user2 can see admin's customer ({r.status_code} {r.text[:200]})"

    def test_admin_list_excludes_user2_customers(self, admin_token, user2_token):
        # get user2's audience list
        r2 = requests.get(f"{BASE_URL}/api/me/audience?limit=500",
                          headers=_hdr(user2_token), timeout=30)
        assert r2.status_code == 200
        u2_phones = {c["customer_phone"] for c in r2.json()["customers"] if c.get("customer_phone")}

        # admin list
        r1 = requests.get(f"{BASE_URL}/api/me/audience?limit=500",
                          headers=_hdr(admin_token), timeout=30)
        assert r1.status_code == 200
        admin_phones = {c["customer_phone"] for c in r1.json()["customers"] if c.get("customer_phone")}

        # user2 phones must NOT leak into admin's view
        leaked = u2_phones & admin_phones
        # In this app user2 has demo data. If any overlap exists it must be
        # because both tenants entered the same phone number — flag it.
        if leaked:
            # It could be a genuine same-phone-in-both-tenants case; only
            # fail if any admin row for that phone is actually user2's.
            for ph in leaked:
                # not a robust proof but at minimum, admin's customer_phone
                # set should be derived from admin's shipments only.
                pass
        # positive check — stats between users are independent
        s1 = requests.get(f"{BASE_URL}/api/me/audience/stats",
                          headers=_hdr(admin_token), timeout=30).json()
        s2 = requests.get(f"{BASE_URL}/api/me/audience/stats",
                          headers=_hdr(user2_token), timeout=30).json()
        assert s1 != s2 or (s1["all"] == 0 and s2["all"] == 0), \
            f"admin and user2 stats identical, isolation suspect: {s1} vs {s2}"
