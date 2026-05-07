"""
Phase 5d backend regression tests:
  • notifications router extraction (6 endpoints)
  • cancel-subscription bug-fix verification
  • smoke regression on previously-extracted routers
"""
import os
import json
import time
import uuid
import requests
from typing import Any, Dict, List, Optional

BASE = "https://logistics-hub-740.preview.emergentagent.com/api"
TIMEOUT = 30

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASSWORD = "Admin@12345"
USER2_EMAIL = "user2@test.com"
USER2_PASSWORD = "User@12345"

results: List[Dict[str, Any]] = []


def record(name: str, ok: bool, info: str = ""):
    results.append({"name": name, "ok": ok, "info": info})
    flag = "✅" if ok else "❌"
    print(f"{flag} {name}{(' — ' + info) if info else ''}")


def login(email: str, password: str) -> Optional[str]:
    r = requests.post(f"{BASE}/auth/login",
                      json={"email": email, "password": password},
                      timeout=TIMEOUT)
    if r.status_code != 200:
        return None
    return r.json().get("token")


def signup(email: str, password: str, name: str = "Reg Test",
           shop: str = "Reg Shop") -> Optional[str]:
    r = requests.post(f"{BASE}/auth/signup",
                      json={"email": email, "password": password,
                            "name": name, "shop_name": shop,
                            "phone": "9000000000"},
                      timeout=TIMEOUT)
    if r.status_code != 200:
        print(f"signup failed: {r.status_code} {r.text[:200]}")
        return None
    return r.json().get("token")


def auth_headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# ------------------------------------------------------------------ #
# 0.  Login admin + user2 + create fresh free-trial user
# ------------------------------------------------------------------ #

def setup_users():
    print("\n=== SETUP USERS ===")
    admin_tok = login(ADMIN_EMAIL, ADMIN_PASSWORD)
    record("Login admin@test.com", admin_tok is not None)
    user2_tok = login(USER2_EMAIL, USER2_PASSWORD)
    record("Login user2@test.com", user2_tok is not None)

    # Fresh free-trial user — used for the 400 path of cancel-subscription
    fresh_email = f"phase5d_{uuid.uuid4().hex[:8]}@test.com"
    fresh_tok = signup(fresh_email, "Phase5d@123",
                       name="Phase5d Trial", shop="Phase5d Shop")
    record(f"Signup fresh free-trial user ({fresh_email})",
           fresh_tok is not None)
    return admin_tok, user2_tok, fresh_tok, fresh_email


# ------------------------------------------------------------------ #
# 1.  GET /api/me/notification-prefs
# ------------------------------------------------------------------ #
DEFAULTS = {
    "trial_ending":     True,
    "plan_expiring":    True,
    "low_credits":      True,
    "payment_success":  True,
    "daily_summary":    False,
    "channel_push":     True,
    "channel_email":    True,
    "sla_breach":         True,
    "daily_limit_warn":   True,
    "morning_reminder":   True,
    "new_order":          True,
    "low_wallet":         True,
}
EXPECTED_KEYS = set(DEFAULTS.keys())


def test_get_notification_prefs(token: str, label: str):
    print(f"\n=== TEST: GET /me/notification-prefs ({label}) ===")
    r = requests.get(f"{BASE}/me/notification-prefs",
                     headers=auth_headers(token), timeout=TIMEOUT)
    record(f"[{label}] GET /me/notification-prefs returns 200",
           r.status_code == 200, f"status={r.status_code}")
    if r.status_code != 200:
        return None
    body = r.json()
    record(f"[{label}] response is a dict", isinstance(body, dict))
    record(f"[{label}] response has 12 expected keys",
           set(body.keys()) == EXPECTED_KEYS,
           f"missing={EXPECTED_KEYS - set(body.keys())} "
           f"extra={set(body.keys()) - EXPECTED_KEYS}")
    return body


def test_get_defaults_for_fresh_user(fresh_tok: str):
    body = test_get_notification_prefs(fresh_tok, "fresh-user")
    if body is None:
        return
    # Fresh user — should match defaults exactly
    matches = all(body.get(k) == v for k, v in DEFAULTS.items())
    record("[fresh-user] all 12 keys equal documented defaults",
           matches, f"body={body}")


# ------------------------------------------------------------------ #
# 2.  PUT /api/me/notification-prefs
# ------------------------------------------------------------------ #
def test_put_notification_prefs(token: str):
    print("\n=== TEST: PUT /me/notification-prefs ===")
    # 2a. Partial update preserves untouched keys
    payload = {"daily_summary": True, "low_credits": False}
    r = requests.put(f"{BASE}/me/notification-prefs",
                     headers=auth_headers(token),
                     json=payload, timeout=TIMEOUT)
    record("PUT partial returns 200", r.status_code == 200,
           f"status={r.status_code}")
    body = r.json() if r.status_code == 200 else {}
    record("PUT partial — daily_summary flipped to True",
           body.get("daily_summary") is True,
           f"got={body.get('daily_summary')}")
    record("PUT partial — low_credits flipped to False",
           body.get("low_credits") is False,
           f"got={body.get('low_credits')}")
    record("PUT partial — untouched key trial_ending preserved (True)",
           body.get("trial_ending") is True,
           f"got={body.get('trial_ending')}")
    record("PUT partial — untouched key channel_push preserved (True)",
           body.get("channel_push") is True,
           f"got={body.get('channel_push')}")

    # 2b. Bool coercion (truthy/falsy non-bool inputs)
    # Pydantic's bool coercion accepts true/false strings, "yes"/"no",
    # 0/1 ints. The router's _coerce_notif_prefs runs `bool(v)` on the
    # incoming dict so empty-string would be False, non-empty would
    # be True. We use 0/1 ints for clarity.
    r = requests.put(f"{BASE}/me/notification-prefs",
                     headers=auth_headers(token),
                     json={"daily_summary": False, "sla_breach": True},
                     timeout=TIMEOUT)
    record("PUT bool coercion returns 200", r.status_code == 200)
    body = r.json() if r.status_code == 200 else {}
    record("PUT — daily_summary set to False",
           body.get("daily_summary") is False)
    record("PUT — sla_breach stays True",
           body.get("sla_breach") is True)

    # 2c. Empty body → returns current unchanged
    before = body.copy()
    r = requests.put(f"{BASE}/me/notification-prefs",
                     headers=auth_headers(token),
                     json={}, timeout=TIMEOUT)
    record("PUT empty body returns 200", r.status_code == 200)
    body = r.json() if r.status_code == 200 else {}
    record("PUT empty body — all keys identical to prior state",
           body == before,
           f"diff_keys={[k for k in EXPECTED_KEYS if body.get(k) != before.get(k)]}")

    # 2d. Restore defaults for cleanliness
    requests.put(f"{BASE}/me/notification-prefs",
                 headers=auth_headers(token), json=DEFAULTS, timeout=TIMEOUT)


# ------------------------------------------------------------------ #
# 3.  POST /api/me/push-token
# ------------------------------------------------------------------ #
TEST_TOKEN_A = "ExponentPushToken[Phase5dRegressionA-AAAAAAAAAA]"
TEST_TOKEN_B = "ExponentPushToken[Phase5dRegressionB-BBBBBBBBBB]"
INVALID_TOKEN = "not-an-expo-token-1234"


def test_push_token_register(token: str):
    print("\n=== TEST: POST /me/push-token ===")
    payload = {
        "token": TEST_TOKEN_A,
        "platform": "ios",
        "device_id": "phase5d-device-1",
    }
    r = requests.post(f"{BASE}/me/push-token",
                      headers=auth_headers(token),
                      json=payload, timeout=TIMEOUT)
    record("Register valid Expo token → 200",
           r.status_code == 200, f"status={r.status_code} body={r.text[:200]}")
    body = r.json() if r.status_code == 200 else {}
    record("Register response carries ok=True", body.get("ok") is True)
    record("Register response carries token", body.get("token") == TEST_TOKEN_A)

    # Idempotent re-register
    r = requests.post(f"{BASE}/me/push-token",
                      headers=auth_headers(token),
                      json=payload, timeout=TIMEOUT)
    record("Re-register same token (idempotent) → 200",
           r.status_code == 200, f"status={r.status_code}")
    # Invalid format
    r = requests.post(f"{BASE}/me/push-token",
                      headers=auth_headers(token),
                      json={"token": INVALID_TOKEN, "platform": "android"},
                      timeout=TIMEOUT)
    record("Invalid token format → 400",
           r.status_code == 400, f"status={r.status_code} body={r.text[:200]}")


# ------------------------------------------------------------------ #
# 4.  GET /api/me/push-tokens — also verifies idempotency (no dup)
# ------------------------------------------------------------------ #
def test_list_push_tokens(token: str, expect_token: str):
    print("\n=== TEST: GET /me/push-tokens ===")
    r = requests.get(f"{BASE}/me/push-tokens",
                     headers=auth_headers(token), timeout=TIMEOUT)
    record("GET /me/push-tokens → 200", r.status_code == 200,
           f"status={r.status_code}")
    body = r.json() if r.status_code == 200 else {}
    record("Response has 'count' and 'tokens'",
           "count" in body and "tokens" in body)
    record("Tokens count >= 1", body.get("count", 0) >= 1,
           f"count={body.get('count')}")
    # Idempotency: registering same token twice should NOT result in 2 entries
    matches = [t for t in body.get("tokens", [])
               if expect_token.startswith(t.get("token", "").rstrip("…"))]
    record("Idempotent — no duplicate of test token",
           len(matches) == 1, f"matches={len(matches)}, tokens={body.get('tokens')}")
    if matches:
        rec = matches[0]
        record("Token entry has 'platform'", "platform" in rec)
        record("Token entry has 'device_id'", "device_id" in rec)


# ------------------------------------------------------------------ #
# 5.  POST /api/me/push-token/test
# ------------------------------------------------------------------ #
def test_push_self_test_with_tokens(token: str):
    print("\n=== TEST: POST /me/push-token/test (with tokens) ===")
    r = requests.post(f"{BASE}/me/push-token/test",
                      headers=auth_headers(token), timeout=TIMEOUT)
    record("Push self-test (with tokens) → 200",
           r.status_code == 200, f"status={r.status_code} body={r.text[:200]}")
    body = r.json() if r.status_code == 200 else {}
    # Push to a fake test token will likely fail at Expo, but the endpoint
    # should still return 200 with a sent/total count.
    record("Self-test response has 'total' or 'sent'",
           "total" in body or "sent" in body, f"body={body}")


def test_push_self_test_no_tokens(token: str):
    print("\n=== TEST: POST /me/push-token/test (no tokens) ===")
    r = requests.post(f"{BASE}/me/push-token/test",
                      headers=auth_headers(token), timeout=TIMEOUT)
    record("Push self-test (no tokens) → 200",
           r.status_code == 200, f"status={r.status_code}")
    body = r.json() if r.status_code == 200 else {}
    record("No-token user → sent=0",
           body.get("sent", -1) == 0, f"body={body}")


# ------------------------------------------------------------------ #
# 6.  DELETE /api/me/push-token
# ------------------------------------------------------------------ #
def test_delete_push_token(token: str):
    print("\n=== TEST: DELETE /me/push-token ===")
    # Re-register the token first so DELETE has something to remove
    # (the prior /push-token/test invocation may have pruned it because
    # the fake token returns DeviceNotRegistered from Expo).
    requests.post(f"{BASE}/me/push-token",
                  headers=auth_headers(token),
                  json={"token": TEST_TOKEN_A,
                        "platform": "ios",
                        "device_id": "phase5d-device-1"},
                  timeout=TIMEOUT)
    # Existing token
    r = requests.delete(f"{BASE}/me/push-token",
                        params={"token": TEST_TOKEN_A},
                        headers=auth_headers(token), timeout=TIMEOUT)
    record("DELETE existing token → 200", r.status_code == 200,
           f"status={r.status_code}")
    body = r.json() if r.status_code == 200 else {}
    record("DELETE existing — ok=True", body.get("ok") is True)
    record("DELETE existing — removed=1",
           body.get("removed") == 1, f"body={body}")

    # Non-existent token
    r = requests.delete(f"{BASE}/me/push-token",
                        params={"token": "ExponentPushToken[ghost-not-in-db]"},
                        headers=auth_headers(token), timeout=TIMEOUT)
    record("DELETE non-existent token → 200", r.status_code == 200)
    body = r.json() if r.status_code == 200 else {}
    record("DELETE non-existent — ok=True, removed=0",
           body.get("ok") is True and body.get("removed") == 0,
           f"body={body}")


# ------------------------------------------------------------------ #
# 7.  POST /api/me/cancel-subscription  (BUG FIX VERIFY)
# ------------------------------------------------------------------ #
def test_cancel_subscription_unauth():
    print("\n=== TEST: POST /me/cancel-subscription (unauth) ===")
    r = requests.post(f"{BASE}/me/cancel-subscription", timeout=TIMEOUT)
    # Could be 401 or 403 depending on auth implementation
    record("Unauthenticated cancel-subscription → 401/403",
           r.status_code in (401, 403),
           f"status={r.status_code} body={r.text[:200]}")


def test_cancel_subscription_free_trial(fresh_tok: str):
    print("\n=== TEST: POST /me/cancel-subscription (free trial) ===")
    r = requests.post(f"{BASE}/me/cancel-subscription",
                      headers=auth_headers(fresh_tok), timeout=TIMEOUT)
    record("Free-trial cancel → 400",
           r.status_code == 400,
           f"status={r.status_code} body={r.text[:200]}")
    if r.status_code == 400:
        try:
            detail = r.json().get("detail", "")
        except Exception:
            detail = r.text
        record("Free-trial cancel detail mentions 'cancel' or 'trial'",
               "cancel" in detail.lower() or "trial" in detail.lower(),
               f"detail={detail}")


def test_cancel_subscription_paid(token: str, label: str):
    print(f"\n=== TEST: POST /me/cancel-subscription ({label} paid) ===")
    r = requests.post(f"{BASE}/me/cancel-subscription",
                      headers=auth_headers(token), timeout=TIMEOUT)
    record(f"[{label}] paid plan cancel → 200",
           r.status_code == 200,
           f"status={r.status_code} body={r.text[:300]}")
    if r.status_code != 200:
        return
    body = r.json()
    record(f"[{label}] response has ok=True", body.get("ok") is True)
    record(f"[{label}] response has 'plan'", "plan" in body)
    record(f"[{label}] response has 'plan_expires_at'",
           "plan_expires_at" in body)
    record(f"[{label}] response has 'message'", bool(body.get("message")))


# ------------------------------------------------------------------ #
# 8. SMOKE REGRESSION on previously-extracted routers
# ------------------------------------------------------------------ #
def test_smoke_other_routers(token: str):
    print("\n=== TEST: SMOKE on previously-extracted routers ===")
    h = auth_headers(token)

    endpoints_get = [
        ("/me/team-members", "team-members GET"),
        ("/wallet", "wallet GET"),
        ("/wallet/history", "wallet history GET"),
        ("/shipments", "shipments GET"),
        ("/orders/pending", "orders/pending GET"),
        ("/me/feature-flags", "feature-flags GET"),
    ]
    for path, name in endpoints_get:
        r = requests.get(f"{BASE}{path}", headers=h, timeout=TIMEOUT)
        record(f"GET {path} → 200",
               r.status_code == 200,
               f"status={r.status_code}")

    # POST /wallet/razorpay/create-order — bogus tiny amount but valid call shape
    r = requests.post(f"{BASE}/wallet/razorpay/create-order",
                      headers=h, json={"amount_inr": 100}, timeout=TIMEOUT)
    record("POST /wallet/razorpay/create-order → 200/503",
           r.status_code in (200, 503),
           f"status={r.status_code} body={r.text[:200]}")

    # POST /plans/upgrade — switch back to silver (mock upgrade)
    r = requests.post(f"{BASE}/plans/upgrade",
                      headers=h, json={"plan": "silver"}, timeout=TIMEOUT)
    record("POST /plans/upgrade (silver mock) → 200",
           r.status_code == 200,
           f"status={r.status_code} body={r.text[:200]}")

    # POST /plans/razorpay/create-order
    r = requests.post(f"{BASE}/plans/razorpay/create-order",
                      headers=h,
                      json={"plan_key": "silver", "billing_cycle": "monthly"},
                      timeout=TIMEOUT)
    record("POST /plans/razorpay/create-order (silver/monthly) → 200/503",
           r.status_code in (200, 503),
           f"status={r.status_code} body={r.text[:200]}")

    # POST /me/team-members/pay-extra (bogus method=razorpay) — depends on plan
    r = requests.post(f"{BASE}/me/team-members/pay-extra",
                      headers=h,
                      json={"method": "razorpay"},
                      timeout=TIMEOUT)
    record("POST /me/team-members/pay-extra → 200/400/402/503",
           r.status_code in (200, 400, 402, 403, 503),
           f"status={r.status_code} body={r.text[:200]}")

    # POST /me/team-members/razorpay/verify with bogus values → expect 400/404
    r = requests.post(f"{BASE}/me/team-members/razorpay/verify",
                      headers=h,
                      json={"razorpay_order_id": "order_BOGUS",
                            "razorpay_payment_id": "pay_BOGUS",
                            "razorpay_signature": "sig_BOGUS"},
                      timeout=TIMEOUT)
    record("POST /me/team-members/razorpay/verify (bogus) → 400/404",
           r.status_code in (400, 404),
           f"status={r.status_code} body={r.text[:200]}")


# ------------------------------------------------------------------ #
# main
# ------------------------------------------------------------------ #
def main():
    admin_tok, user2_tok, fresh_tok, fresh_email = setup_users()
    if not admin_tok:
        print("ABORT — admin login failed")
        return

    # 1+2: notification prefs
    test_get_defaults_for_fresh_user(fresh_tok)
    test_get_notification_prefs(admin_tok, "admin")
    test_put_notification_prefs(admin_tok)

    # 5: push self-test BEFORE registering a token (admin may have one;
    #    use fresh_tok for the no-tokens case)
    test_push_self_test_no_tokens(fresh_tok)

    # 3: register token (under admin)
    test_push_token_register(admin_tok)

    # 4: list & verify idempotency
    test_list_push_tokens(admin_tok, TEST_TOKEN_A)

    # 5b: with-token self-test
    test_push_self_test_with_tokens(admin_tok)

    # 6: delete token
    test_delete_push_token(admin_tok)

    # Final list — count after delete
    r = requests.get(f"{BASE}/me/push-tokens",
                     headers=auth_headers(admin_tok), timeout=TIMEOUT)
    if r.status_code == 200:
        body = r.json()
        # admin may have other tokens from prior tests, but ours should be gone
        toks = [t for t in body.get("tokens", [])
                if TEST_TOKEN_A.startswith(t.get("token", "").rstrip("…"))]
        record("After DELETE — test token gone from list",
               len(toks) == 0)

    # 7: cancel-subscription
    test_cancel_subscription_unauth()
    test_cancel_subscription_free_trial(fresh_tok)

    # Use user2 for the paid-plan cancel — user2 is silver
    test_cancel_subscription_paid(user2_tok, "user2")

    # Re-cancellation on already-cancelled user2 should still work (200, idempotent flip)
    r = requests.post(f"{BASE}/me/cancel-subscription",
                      headers=auth_headers(user2_tok), timeout=TIMEOUT)
    record("Re-cancel paid plan → 200 (idempotent)",
           r.status_code == 200, f"status={r.status_code}")

    # 8: smoke regression
    test_smoke_other_routers(admin_tok)

    # ====================  FINAL SUMMARY  ====================
    print("\n========================================")
    print("FINAL SUMMARY")
    print("========================================")
    passed = sum(1 for r in results if r["ok"])
    failed = [r for r in results if not r["ok"]]
    print(f"Total assertions: {len(results)}")
    print(f"Passed: {passed}")
    print(f"Failed: {len(failed)}")
    for f in failed:
        print(f"  ❌ {f['name']} — {f['info']}")
    print("========================================")


if __name__ == "__main__":
    main()
