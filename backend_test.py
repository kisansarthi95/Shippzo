"""
Phase-4a Wallet Router Refactor — Backend Regression Test
Tests 7 wallet endpoints relocated from server.py to routers/wallet.py
plus smoke regression on Phase-3 routers.
"""
import os
import sys
import json
import requests

BASE_URL = "https://logistics-hub-740.preview.emergentagent.com/api"
ADMIN_EMAIL = "admin@test.com"
ADMIN_PASSWORD = "Admin@12345"

results = []

def log(ok, name, detail=""):
    mark = "PASS" if ok else "FAIL"
    line = f"[{mark}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    results.append((ok, name, detail))

def login():
    r = requests.post(f"{BASE_URL}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=30)
    assert r.status_code == 200, f"login failed {r.status_code} {r.text}"
    tok = r.json()["token"]
    return tok

def main():
    token = login()
    H = {"Authorization": f"Bearer {token}"}
    log(True, "Login admin@test.com", "got token")

    # ===== Test 1: GET /api/wallet =====
    r = requests.get(f"{BASE_URL}/wallet", headers=H, timeout=30)
    ok = r.status_code == 200
    body = r.json() if ok else {}
    req_keys = {"total_credits", "used_credits", "remaining_credits", "updated_at"}
    shape_ok = ok and req_keys.issubset(body.keys())
    log(shape_ok, "GET /wallet", f"status={r.status_code} keys={sorted(list(body.keys())) if ok else r.text[:120]}")
    initial_balance = float(body.get("remaining_credits", 0.0)) if ok else 0.0

    # ===== Test 2: GET /api/wallet/history =====
    r = requests.get(f"{BASE_URL}/wallet/history", headers=H, timeout=30)
    ok = r.status_code == 200
    body = r.json() if ok else {}
    shape_ok = ok and isinstance(body.get("entries"), list) and isinstance(body.get("count"), int)
    log(shape_ok, "GET /wallet/history", f"status={r.status_code} count={body.get('count') if ok else r.text[:120]}")

    # ===== Test 3: POST /api/wallet/purchase amount=100 =====
    r = requests.post(f"{BASE_URL}/wallet/purchase", headers=H, json={"amount_inr": 100}, timeout=30)
    ok = r.status_code == 200
    body = r.json() if ok else {}
    req_keys = {"ok", "mocked", "amount_inr", "credits_added", "bonus", "balance", "history_id"}
    shape_ok = ok and req_keys.issubset(body.keys()) and body.get("ok") is True and body.get("mocked") is True
    log(shape_ok, "POST /wallet/purchase {amount_inr:100}", f"status={r.status_code} body={body if ok else r.text[:150]}")
    credits_added = float(body.get("credits_added", 0)) if ok else 0
    new_balance = float(body.get("balance", 0)) if ok else 0

    # Verify balance went up via GET /wallet
    r = requests.get(f"{BASE_URL}/wallet", headers=H, timeout=30)
    ok = r.status_code == 200
    body = r.json() if ok else {}
    post_balance = float(body.get("remaining_credits", 0)) if ok else 0
    incr_ok = post_balance > initial_balance
    log(incr_ok, "GET /wallet balance INCREASED after purchase",
        f"initial={initial_balance} → after={post_balance} (credits_added={credits_added})")

    # ===== Test 4: POST /api/wallet/purchase amount=5 (below min) =====
    r = requests.post(f"{BASE_URL}/wallet/purchase", headers=H, json={"amount_inr": 5}, timeout=30)
    ok = r.status_code == 400
    detail = ""
    try:
        detail = r.json().get("detail", "")
    except Exception:
        detail = r.text
    keyword_ok = ok and ("between" in detail.lower() or "minimum" in detail.lower() or "₹10" in detail or "1,00,000" in detail)
    log(keyword_ok, "POST /wallet/purchase {amount_inr:5} (below min)", f"status={r.status_code} detail={detail}")

    # ===== Test 5: GET /api/wallet/quote =====
    r = requests.get(f"{BASE_URL}/wallet/quote", headers=H, params={"address": "Sample address Mumbai 400001"}, timeout=60)
    ok = r.status_code == 200
    body = r.json() if ok else {}
    req_keys = {"plan", "wallet_balance", "total", "can_afford", "ai_complexity", "ai_credits", "shipment_credits", "ai_rates"}
    shape_ok = ok and req_keys.issubset(body.keys())
    log(shape_ok, "GET /wallet/quote?address=...", f"status={r.status_code} keys={sorted(list(body.keys()))[:10] if ok else r.text[:150]}")
    if ok:
        print(f"      → plan={body.get('plan')} ai_complexity={body.get('ai_complexity')} total={body.get('total')} can_afford={body.get('can_afford')}")

    # ===== Test 6: POST /api/wallet/razorpay/create-order amount=100 =====
    r = requests.post(f"{BASE_URL}/wallet/razorpay/create-order", headers=H, json={"amount_inr": 100}, timeout=30)
    razorpay_configured = None
    if r.status_code == 200:
        body = r.json()
        req_keys = {"key_id", "order_id", "amount_paise", "currency"}
        shape_ok = req_keys.issubset(body.keys())
        log(shape_ok, "POST /wallet/razorpay/create-order (configured path)",
            f"status=200 key_id={body.get('key_id','')[:15]}... order_id={body.get('order_id','')[:20]}...")
        razorpay_configured = True
    elif r.status_code == 503:
        detail = ""
        try:
            detail = r.json().get("detail", "")
        except Exception:
            detail = r.text
        shape_ok = "not configured" in detail.lower() or "razorpay" in detail.lower()
        log(shape_ok, "POST /wallet/razorpay/create-order (NOT configured path)",
            f"status=503 detail={detail}")
        razorpay_configured = False
    else:
        log(False, "POST /wallet/razorpay/create-order", f"UNEXPECTED status={r.status_code} body={r.text[:200]}")

    # ===== Test 7: POST /api/wallet/razorpay/verify with bogus data =====
    bogus = {"razorpay_order_id": "x", "razorpay_payment_id": "y", "razorpay_signature": "z"}
    r = requests.post(f"{BASE_URL}/wallet/razorpay/verify", headers=H, json=bogus, timeout=30)
    if razorpay_configured:
        acceptable = r.status_code in (404, 503)
        log(acceptable, "POST /wallet/razorpay/verify bogus (configured)",
            f"status={r.status_code} (expected 404 or 503) body={r.text[:150]}")
    else:
        acceptable = r.status_code == 503
        log(acceptable, "POST /wallet/razorpay/verify bogus (not configured)",
            f"status={r.status_code} (expected 503) body={r.text[:150]}")

    # ===== Test 8: POST /api/wallet/razorpay/webhook empty body no signature =====
    # NOTE: Global auth middleware requires Bearer token on ALL /api/* paths except
    # /api/auth/, /api/legal/, /api/team/login. This is pre-existing (not a refactor
    # regression). We include Bearer to reach the endpoint logic itself.
    r = requests.post(f"{BASE_URL}/wallet/razorpay/webhook", data="",
                      headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                      timeout=30)
    if r.status_code == 200:
        try:
            body = r.json()
        except Exception:
            body = {}
        shape_ok = body.get("ok") is True and body.get("skipped") == "webhook not configured"
        log(shape_ok, "POST /wallet/razorpay/webhook (no secret)",
            f"status=200 body={body}")
    elif r.status_code == 400:
        log(True, "POST /wallet/razorpay/webhook (secret configured → sig fail)",
            f"status=400 body={r.text[:150]}")
    else:
        log(False, "POST /wallet/razorpay/webhook",
            f"UNEXPECTED status={r.status_code} body={r.text[:200]}")

    # ===== Smoke: Phase-3 routers regression =====
    print("\n--- Phase-3 Smoke Regression ---")
    for path in ["/couriers", "/me/feature-flags", "/me/custom-fields", "/me/contact-settings", "/me/categories"]:
        r = requests.get(f"{BASE_URL}{path}", headers=H, timeout=30)
        ok = r.status_code == 200
        log(ok, f"GET {path}", f"status={r.status_code}")

    # ===== Summary =====
    print("\n" + "=" * 70)
    total = len(results)
    passed = sum(1 for ok, _, _ in results if ok)
    print(f"Result: {passed}/{total} passed")
    fails = [(n, d) for ok, n, d in results if not ok]
    if fails:
        print("\nFAILURES:")
        for n, d in fails:
            print(f"  - {n}: {d}")
    return 0 if passed == total else 1

if __name__ == "__main__":
    sys.exit(main())
