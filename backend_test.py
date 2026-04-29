"""
Coupon System — Backend Verification
Test plan per review request (2026-04-30).
"""
import os
import sys
import json
import requests
from typing import Any, Dict, Optional


BASE = "https://logistics-hub-740.preview.emergentagent.com/api"
ADMIN_EMAIL = "admin@test.com"
ADMIN_PASS = "Admin@12345"
USER2_EMAIL = "user2@test.com"
USER2_PASS = "User@12345"


def log(msg: str) -> None:
    print(msg, flush=True)


def login(email: str, password: str) -> str:
    r = requests.post(f"{BASE}/auth/login", json={"email": email, "password": password}, timeout=30)
    assert r.status_code == 200, f"login {email}: {r.status_code} {r.text}"
    return r.json()["token"]


def h(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


RESULTS = []


def record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    flag = "PASS" if ok else "FAIL"
    log(f"[{flag}] {name}  {detail if detail else ''}")


def main() -> int:
    # Login
    try:
        admin_tok = login(ADMIN_EMAIL, ADMIN_PASS)
        user2_tok = login(USER2_EMAIL, USER2_PASS)
    except Exception as e:
        log(f"FATAL login error: {e}")
        return 1

    payload_create = {
        "code": "TEST25",
        "discount_type": "percent",
        "discount_value": 25,
        "valid_from": "2026-04-01T00:00:00.000Z",
        "valid_to": "2027-01-01T00:00:00.000Z",
        "max_uses": 100,
        "applies_to_plans": ["silver", "gold"],
        "billing_cycles": ["yearly"],
        "active": True,
    }

    # Preemptive cleanup if TEST25 exists
    try:
        r = requests.get(f"{BASE}/admin/coupons", headers=h(admin_tok), timeout=30)
        if r.status_code == 200:
            for c in r.json().get("coupons", []):
                if c.get("code") == "TEST25":
                    requests.delete(f"{BASE}/admin/coupons/{c['id']}", headers=h(admin_tok), timeout=30)
                    log(f"Cleaned pre-existing TEST25 (id={c['id']})")
    except Exception as e:
        log(f"pre-cleanup warn: {e}")

    coupon_id: Optional[str] = None

    # ── A. Admin CRUD ───────────────────────────────────────────────────
    # 1. POST create
    r = requests.post(f"{BASE}/admin/coupons", headers=h(admin_tok), json=payload_create, timeout=30)
    ok = r.status_code == 200 and r.json().get("coupon", {}).get("status") == "active"
    body = {}
    try:
        body = r.json()
    except Exception:
        pass
    if ok:
        coupon_id = body["coupon"]["id"]
        record("A1 POST /admin/coupons → 200 + status=active", True, f"id={coupon_id}")
    else:
        record("A1 POST /admin/coupons → 200 + status=active", False,
               f"status={r.status_code}, body={r.text[:300]}")

    # 2. GET list contains TEST25 active
    r = requests.get(f"{BASE}/admin/coupons", headers=h(admin_tok), timeout=30)
    ok = r.status_code == 200
    coupons = r.json().get("coupons", []) if ok else []
    found = next((c for c in coupons if c.get("code") == "TEST25"), None)
    record("A2 GET /admin/coupons contains TEST25 status=active",
           bool(found and found.get("status") == "active"),
           f"found={bool(found)} status={found.get('status') if found else None}")

    # 3. PUT active=false → paused, then active=true → active
    if coupon_id:
        r = requests.put(f"{BASE}/admin/coupons/{coupon_id}",
                         headers=h(admin_tok), json={"active": False}, timeout=30)
        paused_ok = r.status_code == 200 and r.json().get("coupon", {}).get("status") == "paused"
        record("A3a PUT active=false → status=paused", paused_ok,
               f"status={r.status_code}, returned={r.json().get('coupon', {}).get('status') if r.status_code == 200 else r.text[:200]}")

        r = requests.put(f"{BASE}/admin/coupons/{coupon_id}",
                         headers=h(admin_tok), json={"active": True}, timeout=30)
        active_ok = r.status_code == 200 and r.json().get("coupon", {}).get("status") == "active"
        record("A3b PUT active=true → status=active", active_ok,
               f"returned={r.json().get('coupon', {}).get('status') if r.status_code == 200 else r.text[:200]}")
    else:
        record("A3a PUT active=false", False, "no coupon_id")
        record("A3b PUT active=true", False, "no coupon_id")

    # 4. POST same code → 409
    r = requests.post(f"{BASE}/admin/coupons", headers=h(admin_tok), json=payload_create, timeout=30)
    record("A4 POST duplicate code → 409 Conflict", r.status_code == 409,
           f"got {r.status_code}, body={r.text[:200]}")

    # 5. DELETE → 200, then absent
    if coupon_id:
        r = requests.delete(f"{BASE}/admin/coupons/{coupon_id}", headers=h(admin_tok), timeout=30)
        del_ok = r.status_code == 200
        record("A5a DELETE coupon → 200", del_ok, f"status={r.status_code}")
        r2 = requests.get(f"{BASE}/admin/coupons", headers=h(admin_tok), timeout=30)
        gone = not any(c.get("code") == "TEST25" for c in r2.json().get("coupons", []))
        record("A5b GET no longer contains TEST25", gone, "")
    else:
        record("A5a DELETE", False, "no coupon_id")
        record("A5b GET absent", False, "no coupon_id")

    # ── B. User Validation ─────────────────────────────────────────────
    # 6. Validate NOPE → ok=false, reason mentions missing/no such
    r = requests.post(f"{BASE}/coupons/validate", headers=h(admin_tok),
                      json={"code": "NOPE", "plan_key": "silver", "billing_cycle": "yearly"}, timeout=30)
    body = r.json() if r.status_code == 200 else {}
    reason = (body.get("reason") or "").lower()
    record("B6 validate unknown code → ok=false + reason mentions 'no such'",
           r.status_code == 200 and body.get("ok") is False and ("no such" in reason or "missing" in reason or "no coupon" in reason),
           f"reason={body.get('reason')}")

    # 7. Re-create TEST25
    r = requests.post(f"{BASE}/admin/coupons", headers=h(admin_tok), json=payload_create, timeout=30)
    if r.status_code == 200:
        coupon_id = r.json()["coupon"]["id"]
        log(f"Re-created TEST25 for B7 (id={coupon_id})")
    else:
        record("B7 setup (recreate TEST25)", False, f"status={r.status_code} body={r.text[:200]}")
        coupon_id = None

    # 7. Validate test25 (lowercase) for silver/yearly
    r = requests.post(f"{BASE}/coupons/validate", headers=h(admin_tok),
                      json={"code": "test25", "plan_key": "silver", "billing_cycle": "yearly"}, timeout=30)
    body = r.json() if r.status_code == 200 else {}
    ok = body.get("ok") is True
    base = int(body.get("base_inr") or 0)
    disc = int(body.get("discount") or 0)
    final = int(body.get("final_inr") or 0)
    spct = int(body.get("savings_pct") or 0)
    expected_disc = int(base * 0.25) if base > 0 else 0
    check = (ok and base > 0 and disc == expected_disc and final == base - disc and spct == 25)
    record("B7 validate case-insensitive silver/yearly → ok=true, 25% applied",
           check,
           f"base={base} disc={disc} expected={expected_disc} final={final} savings_pct={spct}")
    SILVER_YEARLY_BASE = base

    # 8. platinum → ok=false, reason mentions "Platinum"
    r = requests.post(f"{BASE}/coupons/validate", headers=h(admin_tok),
                      json={"code": "TEST25", "plan_key": "platinum", "billing_cycle": "yearly"}, timeout=30)
    body = r.json() if r.status_code == 200 else {}
    reason = (body.get("reason") or "")
    record("B8 validate platinum → ok=false, reason mentions 'Platinum'",
           body.get("ok") is False and "platinum" in reason.lower(),
           f"ok={body.get('ok')} reason={reason}")

    # 9. monthly → ok=false, reason mentions "monthly"
    r = requests.post(f"{BASE}/coupons/validate", headers=h(admin_tok),
                      json={"code": "TEST25", "plan_key": "silver", "billing_cycle": "monthly"}, timeout=30)
    body = r.json() if r.status_code == 200 else {}
    reason = (body.get("reason") or "")
    record("B9 validate silver/monthly → ok=false, reason mentions 'monthly'",
           body.get("ok") is False and "monthly" in reason.lower(),
           f"ok={body.get('ok')} reason={reason}")

    # ── C. Razorpay Apply ──────────────────────────────────────────────
    # 10. create-order silver/yearly with TEST25
    r = requests.post(f"{BASE}/plans/razorpay/create-order", headers=h(admin_tok),
                      json={"plan_key": "silver", "billing_cycle": "yearly", "coupon_code": "TEST25"}, timeout=30)
    body = r.json() if r.status_code == 200 else {}
    coupon_meta = body.get("coupon", {}) or {}
    amount_inr = int(body.get("amount_inr") or 0)
    base_inr = int(body.get("base_inr") or 0)
    c_applied = coupon_meta.get("applied") is True
    c_disc = int(coupon_meta.get("discount") or 0)
    check10 = (r.status_code == 200 and c_applied and c_disc > 0 and amount_inr < base_inr)
    record("C10 create-order silver/yearly + TEST25 → applied + amount < base",
           check10,
           f"status={r.status_code} amount_inr={amount_inr} base_inr={base_inr} applied={c_applied} disc={c_disc}")

    # 11. create-order WITHOUT coupon_code → amount == base, applied=false
    r = requests.post(f"{BASE}/plans/razorpay/create-order", headers=h(admin_tok),
                      json={"plan_key": "silver", "billing_cycle": "yearly"}, timeout=30)
    body = r.json() if r.status_code == 200 else {}
    coupon_meta = body.get("coupon", {}) or {}
    amount_inr = int(body.get("amount_inr") or 0)
    base_inr = int(body.get("base_inr") or 0)
    check11 = (r.status_code == 200 and amount_inr == base_inr and coupon_meta.get("applied") is False)
    record("C11 create-order silver/yearly (no coupon) → amount == base, applied=false",
           check11,
           f"status={r.status_code} amount_inr={amount_inr} base_inr={base_inr} applied={coupon_meta.get('applied')}")

    # 12. with coupon_code=TEST25 but plan=platinum → 400 ("Not valid for Platinum plan")
    r = requests.post(f"{BASE}/plans/razorpay/create-order", headers=h(admin_tok),
                      json={"plan_key": "platinum", "billing_cycle": "yearly", "coupon_code": "TEST25"}, timeout=30)
    detail = ""
    try:
        detail = r.json().get("detail", "")
    except Exception:
        detail = r.text[:200]
    record("C12 platinum + TEST25 → 400 with 'Not valid for Platinum plan'",
           r.status_code == 400 and "platinum" in detail.lower(),
           f"status={r.status_code} detail={detail}")

    # ── D. Non-admin lockout ───────────────────────────────────────────
    r = requests.get(f"{BASE}/admin/coupons", headers=h(user2_tok), timeout=30)
    record("D13a user2 GET /admin/coupons → 403", r.status_code == 403,
           f"status={r.status_code}")
    r = requests.post(f"{BASE}/admin/coupons", headers=h(user2_tok), json=payload_create, timeout=30)
    record("D13b user2 POST /admin/coupons → 403", r.status_code == 403,
           f"status={r.status_code}")
    r = requests.post(f"{BASE}/coupons/validate", headers=h(user2_tok),
                      json={"code": "TEST25", "plan_key": "silver", "billing_cycle": "yearly"}, timeout=30)
    body = r.json() if r.status_code == 200 else {}
    record("D13c user2 /coupons/validate → ok=true",
           r.status_code == 200 and body.get("ok") is True,
           f"status={r.status_code} ok={body.get('ok')}")

    # ── E. Regression ──────────────────────────────────────────────────
    # 14. plan-features registry.features length == 57
    r = requests.get(f"{BASE}/admin/plan-features", headers=h(admin_tok), timeout=30)
    body = r.json() if r.status_code == 200 else {}
    feats = ((body.get("registry") or {}).get("features") or [])
    record("E14 /admin/plan-features registry.features length == 57",
           r.status_code == 200 and len(feats) == 57,
           f"status={r.status_code} len={len(feats)}")

    # 15. /settings and /shipments/stats 200
    r = requests.get(f"{BASE}/settings", headers=h(admin_tok), timeout=30)
    record("E15a GET /settings → 200", r.status_code == 200, f"status={r.status_code}")
    r = requests.get(f"{BASE}/shipments/stats", headers=h(admin_tok), timeout=30)
    record("E15b GET /shipments/stats → 200", r.status_code == 200, f"status={r.status_code}")

    # 16. create-order gold/yearly without coupon → works with base price
    r = requests.post(f"{BASE}/plans/razorpay/create-order", headers=h(admin_tok),
                      json={"plan_key": "gold", "billing_cycle": "yearly"}, timeout=30)
    body = r.json() if r.status_code == 200 else {}
    amount_inr = int(body.get("amount_inr") or 0)
    base_inr = int(body.get("base_inr") or 0)
    cm = body.get("coupon", {}) or {}
    record("E16 create-order gold/yearly (no coupon) → amount == base, applied=false",
           r.status_code == 200 and amount_inr == base_inr and cm.get("applied") is False and amount_inr > 0,
           f"status={r.status_code} amount={amount_inr} base={base_inr} applied={cm.get('applied')}")

    # ── F. Cleanup ─────────────────────────────────────────────────────
    # Find TEST25 id and delete
    try:
        r = requests.get(f"{BASE}/admin/coupons", headers=h(admin_tok), timeout=30)
        cleaned = False
        for c in r.json().get("coupons", []):
            if c.get("code") == "TEST25":
                rd = requests.delete(f"{BASE}/admin/coupons/{c['id']}", headers=h(admin_tok), timeout=30)
                cleaned = rd.status_code == 200
                break
        record("F17 Final cleanup: DELETE TEST25", cleaned, "")
    except Exception as e:
        record("F17 cleanup", False, str(e))

    # Summary
    total = len(RESULTS)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = total - passed
    log("\n" + "=" * 60)
    log(f"TOTAL: {total}  PASSED: {passed}  FAILED: {failed}")
    if failed:
        log("FAILED ITEMS:")
        for name, ok, detail in RESULTS:
            if not ok:
                log(f"  - {name}: {detail}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
