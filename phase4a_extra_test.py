"""
Phase-4a-extra refactor regression test.
Validates 11 plans/coupons assertions + 5 smoke tests.
"""
import os
import sys
import json
import requests

BASE = "https://logistics-hub-740.preview.emergentagent.com/api"
ADMIN_EMAIL = "admin@test.com"
ADMIN_PASSWORD = "Admin@12345"

PASS = []
FAIL = []


def check(label, cond, detail=""):
    if cond:
        PASS.append(label)
        print(f"  PASS  {label}")
    else:
        FAIL.append((label, detail))
        print(f"  FAIL  {label}  {detail}")


def login():
    r = requests.post(f"{BASE}/auth/login",
                      json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
                      timeout=20)
    r.raise_for_status()
    body = r.json()
    return body["token"], body


def hdr(tok):
    return {"Authorization": f"Bearer {tok}"}


def main():
    token, me = login()
    H = hdr(token)
    print(f"Logged in as {me.get('email')} is_admin={me.get('is_admin')}")
    print()

    # ===== Plans/Coupons relocated endpoints =====
    print("=== 1. GET /plans ===")
    r = requests.get(f"{BASE}/plans", headers=H, timeout=15)
    check("1. GET /plans → 200", r.status_code == 200, f"got {r.status_code} body={r.text[:300]}")
    if r.status_code == 200:
        body = r.json()
        plans = body.get("plans") or []
        keys = {p.get("key") for p in plans}
        check("1. plans includes free_trial+silver+gold+platinum",
              {"free_trial", "silver", "gold", "platinum"}.issubset(keys),
              f"got keys={keys}")
        check("1. response has 'current'", "current" in body, f"keys={list(body.keys())}")

    print("=== 2. GET /plans-pricing ===")
    r = requests.get(f"{BASE}/plans-pricing", headers=H, timeout=15)
    check("2. GET /plans-pricing → 200", r.status_code == 200, f"got {r.status_code}")
    if r.status_code == 200:
        body = r.json()
        check("2. response has plan_pricing dict",
              isinstance(body.get("plan_pricing"), dict),
              f"got type={type(body.get('plan_pricing')).__name__}")
        check("2. response has countdown",
              "countdown" in body,
              f"keys={list(body.keys())}")

    print("=== 3. GET /credit-packages ===")
    r = requests.get(f"{BASE}/credit-packages", headers=H, timeout=15)
    check("3. GET /credit-packages → 200", r.status_code == 200, f"got {r.status_code}")
    if r.status_code == 200:
        body = r.json()
        pkgs = body.get("packages") or []
        check("3. packages is non-empty list",
              isinstance(pkgs, list) and len(pkgs) > 0,
              f"got {len(pkgs) if isinstance(pkgs, list) else 'non-list'}")
        if pkgs:
            p0 = pkgs[0]
            check("3. each package has amount_inr/credits/bonus",
                  all(k in p0 for k in ("amount_inr", "credits", "bonus")),
                  f"keys={list(p0.keys())}")

    print("=== 4. GET /me/ai-rates ===")
    r = requests.get(f"{BASE}/me/ai-rates", headers=H, timeout=15)
    check("4. GET /me/ai-rates → 200", r.status_code == 200, f"got {r.status_code}")
    if r.status_code == 200:
        body = r.json()
        for k in ("simple", "medium", "complex"):
            check(f"4. ai-rates has '{k}' (numeric)",
                  k in body and isinstance(body[k], (int, float)),
                  f"got {body.get(k)!r}")

    print("=== 5. GET /admin/coupons ===")
    r = requests.get(f"{BASE}/admin/coupons", headers=H, timeout=15)
    check("5. GET /admin/coupons → 200", r.status_code == 200, f"got {r.status_code}")
    if r.status_code == 200:
        body = r.json()
        check("5. response.coupons is a list",
              isinstance(body.get("coupons"), list),
              f"keys={list(body.keys())}")

    print("=== 6. POST /admin/coupons ===")
    # NOTE: actual CouponCreate model uses applies_to_plans + active +
    # valid_from + valid_to (and no `label` field). The review request
    # used legacy keys; mapping to current schema for the refactor test.
    payload = {
        "code": "TESTREFCTR",
        "discount_type": "percent",
        "discount_value": 10,
        "applies_to_plans": ["silver"],
        "billing_cycles": ["monthly"],
        "active": True,
        "valid_from": "2026-01-01T00:00:00+00:00",
        "valid_to":   "2027-01-01T00:00:00+00:00",
    }
    # Cleanup any pre-existing
    r0 = requests.get(f"{BASE}/admin/coupons", headers=H, timeout=15)
    if r0.status_code == 200:
        for c in (r0.json().get("coupons") or []):
            if c.get("code") == "TESTREFCTR":
                requests.delete(f"{BASE}/admin/coupons/{c['id']}", headers=H, timeout=10)
    r = requests.post(f"{BASE}/admin/coupons", headers=H, json=payload, timeout=15)
    check("6. POST /admin/coupons → 200", r.status_code == 200, f"got {r.status_code} body={r.text[:300]}")
    coupon_id = None
    if r.status_code == 200:
        body = r.json()
        check("6. ok=true", body.get("ok") is True, f"got {body}")
        coupon = body.get("coupon") or {}
        coupon_id = coupon.get("id")
        check("6. coupon has id", bool(coupon_id), f"coupon={coupon}")
        check("6. coupon.code=TESTREFCTR", coupon.get("code") == "TESTREFCTR",
              f"code={coupon.get('code')}")
        # legacy alias check ('label' isn't in current schema; status is computed)
        check("6. coupon has status=active",
              coupon.get("status") == "active",
              f"status={coupon.get('status')}")

    if not coupon_id:
        print("Cannot proceed without coupon_id; aborting follow-on tests.")
    else:
        print(f"=== 7. PUT /admin/coupons/{coupon_id} ===")
        # NOTE: model has no `label` field. Updating discount_value instead.
        r = requests.put(f"{BASE}/admin/coupons/{coupon_id}",
                         headers=H, json={"discount_value": 15}, timeout=15)
        check("7. PUT /admin/coupons/{id} → 200", r.status_code == 200, f"got {r.status_code} {r.text[:300]}")
        if r.status_code == 200:
            body = r.json()
            check("7. coupon.discount_value updated to 15",
                  (body.get("coupon") or {}).get("discount_value") == 15.0,
                  f"got discount_value={(body.get('coupon') or {}).get('discount_value')}")

        print("=== 8. POST /coupons/validate (valid) ===")
        r = requests.post(f"{BASE}/coupons/validate", headers=H,
                         json={"code": "TESTREFCTR", "plan_key": "silver",
                               "billing_cycle": "monthly"}, timeout=15)
        check("8. POST /coupons/validate (valid) → 200", r.status_code == 200,
              f"got {r.status_code} {r.text[:300]}")
        if r.status_code == 200:
            body = r.json()
            print(f"   body={body}")
            check("8. ok=true", body.get("ok") is True, f"got body={body}")
            check("8. code=TESTREFCTR", body.get("code") == "TESTREFCTR")
            for k in ("base_inr", "discount", "final_inr"):
                check(f"8. has numeric '{k}'",
                      isinstance(body.get(k), (int, float)),
                      f"got {body.get(k)!r}")
            check("8. savings_pct == 10",
                  body.get("savings_pct") == 10,
                  f"got {body.get('savings_pct')}")

        print("=== 9. POST /coupons/validate (nonexistent) ===")
        r = requests.post(f"{BASE}/coupons/validate", headers=H,
                         json={"code": "NONEXISTENT", "plan_key": "silver",
                               "billing_cycle": "monthly"}, timeout=15)
        check("9. POST /coupons/validate (nonexistent) → 200",
              r.status_code == 200, f"got {r.status_code} {r.text[:300]}")
        if r.status_code == 200:
            body = r.json()
            print(f"   body={body}")
            check("9. ok=false", body.get("ok") is False, f"got body={body}")
            check("9. has reason", bool(body.get("reason")),
                  f"reason={body.get('reason')}")

        print("=== 10. GET /admin/coupons/analytics ===")
        r = requests.get(f"{BASE}/admin/coupons/analytics", headers=H, timeout=15)
        check("10. GET /admin/coupons/analytics → 200", r.status_code == 200,
              f"got {r.status_code} {r.text[:300]}")
        if r.status_code == 200:
            body = r.json()
            for k in ("total_used", "active", "top5", "total_coupons", "status_counts"):
                check(f"10. has '{k}'", k in body,
                      f"keys={list(body.keys())}")
            check("10. top5 is array", isinstance(body.get("top5"), list),
                  f"top5 type={type(body.get('top5')).__name__}")

        print(f"=== 11. DELETE /admin/coupons/{coupon_id} (cleanup) ===")
        r = requests.delete(f"{BASE}/admin/coupons/{coupon_id}", headers=H, timeout=15)
        check("11. DELETE /admin/coupons/{id} → 200", r.status_code == 200,
              f"got {r.status_code} {r.text[:300]}")
        if r.status_code == 200:
            body = r.json()
            check("11. ok=true", body.get("ok") is True, f"got body={body}")
            check("11. deleted == coupon_id",
                  body.get("deleted") == coupon_id,
                  f"got body={body}")

    # ===== Smoke regression =====
    print()
    print("=== SMOKE REGRESSION ===")
    smokes = [
        ("/wallet", "GET /wallet"),
        ("/wallet/history", "GET /wallet/history"),
        ("/couriers", "GET /couriers"),
        ("/me/feature-flags", "GET /me/feature-flags"),
        ("/me/custom-fields", "GET /me/custom-fields"),
    ]
    for path, label in smokes:
        r = requests.get(f"{BASE}{path}", headers=H, timeout=15)
        check(f"SMOKE {label} → 200", r.status_code == 200,
              f"got {r.status_code} body={r.text[:200]}")

    print()
    print("=" * 60)
    print(f"PASS: {len(PASS)}  FAIL: {len(FAIL)}")
    if FAIL:
        print("\nFAILURES:")
        for label, detail in FAIL:
            print(f"  - {label}: {detail}")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
