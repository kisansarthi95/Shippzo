"""Backend test for the two new Admin Users endpoints.

Tests:
  GET /api/admin/users
  GET /api/admin/users/{user_id}
"""
import json
import sys
import requests

BASE_URL = "http://localhost:8001/api"
ADMIN_EMAIL = "admin@test.com"
ADMIN_PASSWORD = "Admin@12345"

results = []


def record(name, ok, detail=""):
    results.append((name, ok, detail))
    icon = "PASS" if ok else "FAIL"
    print(f"[{icon}] {name}{(' — ' + detail) if detail else ''}")


def login(email, password):
    r = requests.post(f"{BASE_URL}/auth/login", json={"email": email, "password": password}, timeout=15)
    r.raise_for_status()
    return r.json()


def main():
    print(f"Base URL: {BASE_URL}\n")
    # Login as admin
    print("== Login admin ==")
    auth = login(ADMIN_EMAIL, ADMIN_PASSWORD)
    token = auth["token"]
    admin_id = auth["id"]
    H = {"Authorization": f"Bearer {token}"}
    print(f"admin_id={admin_id}, is_admin={auth.get('is_admin')}\n")

    # ─────────── Test 1: GET /admin/users ───────────
    print("== Test 1a: GET /admin/users without bearer → 401/403 ==")
    r = requests.get(f"{BASE_URL}/admin/users", timeout=15)
    record("1a list-no-auth blocked (401/403)", r.status_code in (401, 403),
           f"got {r.status_code}: {r.text[:200]}")

    print("\n== Test 1b: GET /admin/users with admin bearer ==")
    r = requests.get(f"{BASE_URL}/admin/users", headers=H, timeout=15)
    record("1b list HTTP 200", r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")
    if r.status_code != 200:
        return finish()
    body = r.json()
    for k in ("total", "limit", "skip", "users", "summary"):
        record(f"1b key '{k}' present", k in body, f"keys={list(body.keys())}")
    summary = body.get("summary", {})
    for k in ("total_users", "admin_count", "plan_counts", "displayed"):
        record(f"1b summary.{k} present", k in summary, f"summary={list(summary.keys())}")

    users = body.get("users", [])
    record("1b users array non-empty", len(users) >= 1, f"len={len(users)}")
    user_keys = ["id", "email", "name", "plan", "is_admin", "plan_billing_cycle",
                 "plan_expires_at", "plan_expired", "plan_days_left", "wallet_balance",
                 "labels_this_month", "created_at", "last_login_at"]
    if users:
        u0 = users[0]
        for k in user_keys:
            record(f"1b user row has '{k}'", k in u0, f"got keys={list(u0.keys())}")
        record("1b wallet_balance is float-like",
               isinstance(u0.get("wallet_balance"), (int, float)),
               f"type={type(u0.get('wallet_balance')).__name__} val={u0.get('wallet_balance')}")
        record("1b labels_this_month is int",
               isinstance(u0.get("labels_this_month"), int),
               f"type={type(u0.get('labels_this_month')).__name__}")
    # Verify admin user appears
    admin_in_list = any(u.get("id") == admin_id for u in users)
    record("1b admin user appears in list", admin_in_list, "")

    # ─────────── Test 1c: ?q=admin@ ───────────
    print("\n== Test 1c: GET /admin/users?q=admin@ ==")
    r = requests.get(f"{BASE_URL}/admin/users", headers=H, params={"q": "admin@"}, timeout=15)
    record("1c HTTP 200", r.status_code == 200, str(r.status_code))
    if r.status_code == 200:
        body_q = r.json()
        users_q = body_q.get("users", [])
        # All returned should match admin@ in email/name/shop (case-insensitive)
        all_match = True
        for u in users_q:
            hay = " ".join([str(u.get("email", "")), str(u.get("name", "")), str(u.get("shop_name", ""))]).lower()
            if "admin@" not in hay:
                all_match = False
                break
        record("1c all users have 'admin@' in email/name/shop (ci)",
               all_match, f"users_q_count={len(users_q)}")

    # ─────────── Test 1d: ?plan=free_trial ───────────
    print("\n== Test 1d: GET /admin/users?plan=free_trial ==")
    r = requests.get(f"{BASE_URL}/admin/users", headers=H, params={"plan": "free_trial"}, timeout=15)
    record("1d HTTP 200", r.status_code == 200, str(r.status_code))
    if r.status_code == 200:
        b = r.json()
        users_p = b.get("users", [])
        all_ft = all(u.get("plan") == "free_trial" for u in users_p)
        record("1d all returned users plan=free_trial",
               all_ft, f"plans={[u.get('plan') for u in users_p][:5]}")

    # ─────────── Test 1e: ?plan=nonsense ───────────
    print("\n== Test 1e: GET /admin/users?plan=nonsense ==")
    r = requests.get(f"{BASE_URL}/admin/users", headers=H, params={"plan": "nonsense"}, timeout=15)
    record("1e HTTP 200 with empty users", r.status_code == 200, str(r.status_code))
    if r.status_code == 200:
        b = r.json()
        record("1e users array empty", b.get("users") == [], f"users_len={len(b.get('users', []))}")

    # ─────────── Test 1f: ?limit=5&skip=0 ───────────
    print("\n== Test 1f: GET /admin/users?limit=5&skip=0 ==")
    r = requests.get(f"{BASE_URL}/admin/users", headers=H, params={"limit": 5, "skip": 0}, timeout=15)
    record("1f HTTP 200", r.status_code == 200, str(r.status_code))
    if r.status_code == 200:
        b = r.json()
        record("1f respects limit (≤5)", len(b.get("users", [])) <= 5,
               f"len={len(b.get('users', []))}")
        record("1f limit echo == 5", b.get("limit") == 5, f"limit={b.get('limit')}")
        record("1f skip echo == 0", b.get("skip") == 0, f"skip={b.get('skip')}")

    # ─────────── Test 1g: summary.plan_counts is on ENTIRE collection ───────────
    print("\n== Test 1g: ?plan=platinum — summary still global ==")
    r1 = requests.get(f"{BASE_URL}/admin/users", headers=H, timeout=15)
    r2 = requests.get(f"{BASE_URL}/admin/users", headers=H, params={"plan": "platinum"}, timeout=15)
    if r1.status_code == 200 and r2.status_code == 200:
        s1 = r1.json().get("summary", {})
        s2 = r2.json().get("summary", {})
        record("1g summary.total_users equal across filtered/unfiltered",
               s1.get("total_users") == s2.get("total_users"),
               f"unfiltered={s1.get('total_users')} filtered={s2.get('total_users')}")
        record("1g summary.plan_counts equal across filtered/unfiltered",
               s1.get("plan_counts") == s2.get("plan_counts"),
               f"u={s1.get('plan_counts')} f={s2.get('plan_counts')}")
        record("1g admin_count equal", s1.get("admin_count") == s2.get("admin_count"),
               f"u={s1.get('admin_count')} f={s2.get('admin_count')}")
    else:
        record("1g requests succeeded", False, f"{r1.status_code} {r2.status_code}")

    # ─────────── Test 2a: GET /admin/users/{admin_id} ───────────
    print(f"\n== Test 2a: GET /admin/users/{admin_id} ==")
    r = requests.get(f"{BASE_URL}/admin/users/{admin_id}", headers=H, timeout=15)
    record("2a HTTP 200", r.status_code == 200, f"{r.status_code}: {r.text[:200]}")
    if r.status_code == 200:
        body = r.json()
        for k in ("user", "wallet", "shipment_count", "paid_orders_count",
                  "recent_shipments", "recent_wallet_tx"):
            record(f"2a key '{k}' present", k in body, f"keys={list(body.keys())}")
        u_obj = body.get("user", {})
        record("2a user.password_hash NOT present", "password_hash" not in u_obj,
               f"keys_in_user={list(u_obj.keys())}")
        record("2a shipment_count is int",
               isinstance(body.get("shipment_count"), int),
               f"type={type(body.get('shipment_count')).__name__}")
        record("2a paid_orders_count is int",
               isinstance(body.get("paid_orders_count"), int),
               f"type={type(body.get('paid_orders_count')).__name__}")
        record("2a recent_shipments is list (≤20)",
               isinstance(body.get("recent_shipments"), list)
               and len(body.get("recent_shipments", [])) <= 20,
               f"len={len(body.get('recent_shipments', []))}")
        record("2a recent_wallet_tx is list (≤15)",
               isinstance(body.get("recent_wallet_tx"), list)
               and len(body.get("recent_wallet_tx", [])) <= 15,
               f"len={len(body.get('recent_wallet_tx', []))}")

    # ─────────── Test 2b: invalid user_id ───────────
    print("\n== Test 2b: GET /admin/users/nonexistent123 → 404 ==")
    r = requests.get(f"{BASE_URL}/admin/users/nonexistent123", headers=H, timeout=15)
    record("2b HTTP 404", r.status_code == 404, f"got {r.status_code}: {r.text[:200]}")
    if r.status_code == 404:
        try:
            detail = r.json().get("detail")
            record("2b detail == 'User not found'", detail == "User not found",
                   f"detail={detail!r}")
        except Exception as e:
            record("2b detail JSON parse", False, str(e))

    # ─────────── Test 2c: no bearer ───────────
    print(f"\n== Test 2c: GET /admin/users/{admin_id} without bearer → 401/403 ==")
    r = requests.get(f"{BASE_URL}/admin/users/{admin_id}", timeout=15)
    record("2c detail-no-auth blocked (401/403)", r.status_code in (401, 403),
           f"got {r.status_code}: {r.text[:200]}")

    finish()


def finish():
    print("\n========== SUMMARY ==========")
    passed = sum(1 for _, ok, _ in results if ok)
    failed = sum(1 for _, ok, _ in results if not ok)
    print(f"Total: {passed + failed} | Passed: {passed} | Failed: {failed}")
    if failed:
        print("\nFailures:")
        for n, ok, d in results:
            if not ok:
                print(f"  - {n}: {d}")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
