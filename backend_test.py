"""
Backend Test — Coupon System Phase-2 (Analytics + Restricted-to-Users)

Live URL: https://logistics-hub-740.preview.emergentagent.com/api
Admin:    admin@test.com / Admin@12345
User2:    user2@test.com / User@12345
"""
import os
import sys
import json
import requests

BASE = "https://logistics-hub-740.preview.emergentagent.com/api"


def _login(email: str, password: str) -> str:
    r = requests.post(f"{BASE}/auth/login", json={"email": email, "password": password}, timeout=30)
    r.raise_for_status()
    data = r.json()
    return data["token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def section(title: str):
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def assert_true(cond: bool, msg: str, fails: list):
    if cond:
        print(f"  ✅ {msg}")
    else:
        print(f"  ❌ {msg}")
        fails.append(msg)


def main():
    fails = []
    created_coupon_id = None

    section("SETUP — login admin + user2")
    admin_tok = _login("admin@test.com", "Admin@12345")
    user2_tok = _login("user2@test.com", "User@12345")
    print(f"  admin token: {admin_tok[:20]}…")
    print(f"  user2 token: {user2_tok[:20]}…")

    # =============== A. ANALYTICS ENDPOINT ================
    section("A1. GET /admin/coupons/analytics as admin → 200")
    r = requests.get(f"{BASE}/admin/coupons/analytics", headers=_auth(admin_tok), timeout=30)
    print(f"  status={r.status_code}")
    assert_true(r.status_code == 200, "A1: status 200", fails)
    try:
        data = r.json()
        print("  keys:", sorted(list(data.keys())))
        print("  totals:", data.get("totals"))
        print("  total_coupons:", data.get("total_coupons"))
        print("  status_counts:", data.get("status_counts"))
        print("  coupons[0:2]:", json.dumps(data.get("coupons", [])[:2], default=str))

        totals = data.get("totals", {})
        assert_true(isinstance(totals, dict), "A1: totals is dict", fails)
        for k in ("redemptions", "total_discount", "total_revenue"):
            assert_true(k in totals, f"A1: totals.{k} present", fails)
            assert_true(isinstance(totals.get(k), int), f"A1: totals.{k} int", fails)

        assert_true(isinstance(data.get("coupons"), list), "A1: coupons is list", fails)
        assert_true(isinstance(data.get("total_coupons"), int), "A1: total_coupons int", fails)

        sc = data.get("status_counts", {})
        for k in ("active", "paused", "scheduled", "expired", "exhausted"):
            assert_true(k in sc, f"A1: status_counts.{k} present", fails)
    except Exception as e:
        assert_true(False, f"A1: parse JSON failed: {e}", fails)

    section("A2. GET /admin/coupons/analytics as user2 → 403")
    r = requests.get(f"{BASE}/admin/coupons/analytics", headers=_auth(user2_tok), timeout=30)
    print(f"  status={r.status_code}, body={r.text[:200]}")
    assert_true(r.status_code == 403, "A2: status 403", fails)

    # =============== B. RESTRICTED-TO-USERS ================
    section("B3. POST /admin/coupons — create TESTUSR1 with allow-list")
    payload = {
        "code": "TESTUSR1",
        "discount_type": "percent",
        "discount_value": 20,
        "valid_from": "2026-04-01T00:00:00.000Z",
        "valid_to": "2027-01-01T00:00:00.000Z",
        "applies_to_plans": [],
        "billing_cycles": [],
        "active": True,
        "restricted_to_users": ["ADMIN@TEST.com", " "],
    }
    r = requests.post(f"{BASE}/admin/coupons", headers=_auth(admin_tok), json=payload, timeout=30)
    print(f"  status={r.status_code}, body={r.text[:400]}")
    assert_true(r.status_code == 200, "B3: status 200", fails)
    try:
        data = r.json()
        cp = data.get("coupon") or {}
        created_coupon_id = cp.get("id")
        rtu = cp.get("restricted_to_users")
        print(f"  restricted_to_users from create response: {rtu}")
        assert_true(rtu == ["admin@test.com"], "B3: restricted_to_users == ['admin@test.com']", fails)
    except Exception as e:
        assert_true(False, f"B3: JSON parse failed: {e}", fails)

    # Read back via GET /admin/coupons to verify persistence
    r = requests.get(f"{BASE}/admin/coupons", headers=_auth(admin_tok), timeout=30)
    if r.status_code == 200:
        coupons = r.json().get("coupons", [])
        match = next((c for c in coupons if c.get("code") == "TESTUSR1"), None)
        if match:
            rtu2 = match.get("restricted_to_users")
            print(f"  restricted_to_users from GET list: {rtu2}")
            assert_true(rtu2 == ["admin@test.com"], "B3b: read-back restricted_to_users == ['admin@test.com']", fails)
        else:
            assert_true(False, "B3b: TESTUSR1 found in list", fails)

    section("B4. POST /coupons/validate as admin → ok=true")
    r = requests.post(
        f"{BASE}/coupons/validate",
        headers=_auth(admin_tok),
        json={"code": "TESTUSR1", "plan_key": "silver", "billing_cycle": "yearly"},
        timeout=30,
    )
    print(f"  status={r.status_code}, body={r.text[:400]}")
    assert_true(r.status_code == 200, "B4: status 200", fails)
    try:
        data = r.json()
        assert_true(data.get("ok") is True, "B4: ok=true", fails)
        assert_true(data.get("code") == "TESTUSR1", "B4: code echoed", fails)
        assert_true(int(data.get("discount") or 0) > 0, "B4: discount > 0", fails)
    except Exception as e:
        assert_true(False, f"B4: JSON parse failed: {e}", fails)

    section("B5. POST /coupons/validate as user2 → ok=false, reason mentions allow-list")
    r = requests.post(
        f"{BASE}/coupons/validate",
        headers=_auth(user2_tok),
        json={"code": "TESTUSR1", "plan_key": "silver", "billing_cycle": "yearly"},
        timeout=30,
    )
    print(f"  status={r.status_code}, body={r.text[:400]}")
    assert_true(r.status_code == 200, "B5: status 200", fails)
    try:
        data = r.json()
        assert_true(data.get("ok") is False, "B5: ok=false", fails)
        reason = (data.get("reason") or "").lower()
        assert_true(
            "not available" in reason or "not in" in reason or "account" in reason,
            f"B5: reason suggests not in allow-list (got: {reason!r})",
            fails,
        )
    except Exception as e:
        assert_true(False, f"B5: JSON parse failed: {e}", fails)

    section("B6. POST /plans/razorpay/create-order as user2 with TESTUSR1 → 400")
    r = requests.post(
        f"{BASE}/plans/razorpay/create-order",
        headers=_auth(user2_tok),
        json={"plan_key": "silver", "billing_cycle": "yearly", "coupon_code": "TESTUSR1"},
        timeout=30,
    )
    print(f"  status={r.status_code}, body={r.text[:400]}")
    assert_true(r.status_code == 400, "B6: status 400", fails)
    try:
        detail = (r.json().get("detail") or "").lower()
        assert_true("coupon" in detail or "not available" in detail, f"B6: detail mentions coupon (got: {detail!r})", fails)
    except Exception:
        pass

    # =============== C. REGRESSION ================
    section("C7. Validate unrestricted coupon as user2")
    # Create a fresh unrestricted coupon to test against (to keep test deterministic)
    unrestricted_code = "TESTOPEN1"
    r = requests.post(
        f"{BASE}/admin/coupons",
        headers=_auth(admin_tok),
        json={
            "code": unrestricted_code,
            "discount_type": "percent",
            "discount_value": 10,
            "valid_from": "2026-04-01T00:00:00.000Z",
            "valid_to": "2027-01-01T00:00:00.000Z",
            "applies_to_plans": [],
            "billing_cycles": [],
            "active": True,
            "restricted_to_users": [],
        },
        timeout=30,
    )
    unrestricted_id = None
    if r.status_code == 200:
        unrestricted_id = r.json().get("coupon", {}).get("id")
        print(f"  created helper coupon {unrestricted_code} id={unrestricted_id}")
    else:
        # Maybe it already exists; fetch the list
        print(f"  could not create helper coupon: {r.status_code} {r.text[:200]}")
        lst = requests.get(f"{BASE}/admin/coupons", headers=_auth(admin_tok), timeout=30).json().get("coupons", [])
        match = next((c for c in lst if c.get("code") == unrestricted_code), None)
        if match:
            unrestricted_id = match.get("id")

    r = requests.post(
        f"{BASE}/coupons/validate",
        headers=_auth(user2_tok),
        json={"code": unrestricted_code, "plan_key": "silver", "billing_cycle": "yearly"},
        timeout=30,
    )
    print(f"  status={r.status_code}, body={r.text[:300]}")
    assert_true(r.status_code == 200, "C7: status 200", fails)
    try:
        data = r.json()
        assert_true(data.get("ok") is True, "C7: ok=true for user2 on unrestricted coupon", fails)
    except Exception as e:
        assert_true(False, f"C7: JSON parse failed: {e}", fails)

    section("C8. Regression on unaffected endpoints")
    for path in ("/settings", "/shipments/stats", "/admin/plan-features"):
        r = requests.get(f"{BASE}{path}", headers=_auth(admin_tok), timeout=30)
        print(f"  GET {path} → {r.status_code}")
        assert_true(r.status_code == 200, f"C8: {path} still 200", fails)

    # =============== D. CLEANUP ================
    section("D9. DELETE /admin/coupons/{TESTUSR1 id}")
    if created_coupon_id:
        r = requests.delete(f"{BASE}/admin/coupons/{created_coupon_id}", headers=_auth(admin_tok), timeout=30)
        print(f"  DELETE TESTUSR1 ({created_coupon_id}) → {r.status_code}, body={r.text[:200]}")
        assert_true(r.status_code == 200, "D9: delete TESTUSR1 returned 200", fails)
    else:
        assert_true(False, "D9: no created_coupon_id to delete", fails)

    if unrestricted_id:
        r = requests.delete(f"{BASE}/admin/coupons/{unrestricted_id}", headers=_auth(admin_tok), timeout=30)
        print(f"  DELETE {unrestricted_code} ({unrestricted_id}) → {r.status_code}")

    # ==== SUMMARY ====
    print("\n" + "=" * 72)
    print(f"FAILS: {len(fails)}")
    for f in fails:
        print(f"  - {f}")
    print("=" * 72)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
