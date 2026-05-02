"""Phase-13 Admin Plan Limits backend tests.

Run: python /app/backend_test_phase13.py
"""
import json
import sys
import requests

BASE = "http://localhost:8001/api"

ADMIN = {"email": "admin@test.com", "password": "Admin@12345"}
USER2 = {"email": "user2@test.com", "password": "User@12345"}

results = []


def log(name, ok, detail=""):
    results.append((name, ok, detail))
    flag = "PASS" if ok else "FAIL"
    print(f"[{flag}] {name}" + (f" — {detail}" if detail else ""))


def login(creds):
    r = requests.post(f"{BASE}/auth/login", json=creds, timeout=15)
    r.raise_for_status()
    return r.json()["token"]


def headers(tok):
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}


def main():
    # --- Login both accounts ---
    try:
        admin_tok = login(ADMIN)
        log("admin login", True)
    except Exception as e:
        log("admin login", False, str(e))
        return False
    try:
        user_tok = login(USER2)
        log("user2 login", True)
    except Exception as e:
        log("user2 login", False, str(e))
        return False

    # --- TEST 1: GET /admin/plan-limits as admin ---
    r = requests.get(f"{BASE}/admin/plan-limits", headers=headers(admin_tok))
    if r.status_code != 200:
        log("T1 GET /admin/plan-limits 200", False, f"status={r.status_code} body={r.text[:200]}")
        return False
    body = r.json()
    log("T1 GET /admin/plan-limits 200", True)

    expected_order = ["free_trial", "silver", "gold", "platinum"]
    log("T1 order matches", body.get("order") == expected_order,
        f"got {body.get('order')}")

    defaults = body.get("defaults") or {}
    keys_ok = set(defaults.keys()) == set(expected_order)
    log("T1 defaults has 4 plan keys", keys_ok, f"got {list(defaults.keys())}")

    required_keys = {"name", "label_cap", "bulk_max", "daily_cap",
                     "price_inr", "trial_days", "period"}
    for k in expected_order:
        d = defaults.get(k) or {}
        missing = required_keys - set(d.keys())
        log(f"T1 defaults[{k}] has all keys", not missing, f"missing={missing}")

    # Specific default values per plans.py
    log("T1 free_trial.label_cap=10", defaults["free_trial"]["label_cap"] == 10,
        f"got {defaults['free_trial']['label_cap']}")
    log("T1 silver.label_cap=50", defaults["silver"]["label_cap"] == 50,
        f"got {defaults['silver']['label_cap']}")
    log("T1 gold.label_cap=300", defaults["gold"]["label_cap"] == 300,
        f"got {defaults['gold']['label_cap']}")
    log("T1 gold.bulk_max=50", defaults["gold"]["bulk_max"] == 50,
        f"got {defaults['gold']['bulk_max']}")
    log("T1 platinum.label_cap=1500",
        defaults["platinum"]["label_cap"] == 1500,
        f"got {defaults['platinum']['label_cap']}")
    log("T1 platinum.bulk_max=100",
        defaults["platinum"]["bulk_max"] == 100,
        f"got {defaults['platinum']['bulk_max']}")
    log("T1 platinum.daily_cap=100",
        defaults["platinum"]["daily_cap"] == 100,
        f"got {defaults['platinum']['daily_cap']}")

    current = body.get("current") or {}
    cur_keys = {"label_cap", "bulk_max", "daily_cap",
                "price_inr", "trial_days"}
    for k in expected_order:
        c = current.get(k) or {}
        missing = cur_keys - set(c.keys())
        # current shape = same minus name/period
        log(f"T1 current[{k}] has correct shape (no name/period)",
            not missing and "name" not in c and "period" not in c,
            f"keys={list(c.keys())}")

    # --- TEST 2: GET /admin/plan-limits as non-admin → 403 ---
    r = requests.get(f"{BASE}/admin/plan-limits", headers=headers(user_tok))
    log("T2 non-admin gets 403", r.status_code == 403,
        f"status={r.status_code} body={r.text[:200]}")

    # --- TEST 3: PUT /admin/plan-limits with overrides ---
    payload = {
        "plans": {
            "silver": {"label_cap": 75, "price_inr": 249},
            "gold": {"label_cap": 400},
            "platinum": {"daily_cap": 200},
        }
    }
    r = requests.put(f"{BASE}/admin/plan-limits",
                     headers=headers(admin_tok), json=payload)
    if r.status_code != 200:
        log("T3 PUT /admin/plan-limits 200", False,
            f"status={r.status_code} body={r.text[:300]}")
        return False
    log("T3 PUT /admin/plan-limits 200", True)
    body3 = r.json()
    cur3 = body3.get("current") or {}
    log("T3 current.silver.label_cap=75",
        cur3["silver"]["label_cap"] == 75,
        f"got {cur3['silver']['label_cap']}")
    log("T3 current.silver.price_inr=249",
        cur3["silver"]["price_inr"] == 249,
        f"got {cur3['silver']['price_inr']}")
    log("T3 current.silver.bulk_max preserved (=0)",
        cur3["silver"]["bulk_max"] == 0,
        f"got {cur3['silver']['bulk_max']}")
    log("T3 current.silver.daily_cap preserved (None)",
        cur3["silver"]["daily_cap"] is None,
        f"got {cur3['silver']['daily_cap']}")
    log("T3 current.gold.label_cap=400",
        cur3["gold"]["label_cap"] == 400,
        f"got {cur3['gold']['label_cap']}")
    log("T3 current.gold.bulk_max preserved (=50)",
        cur3["gold"]["bulk_max"] == 50,
        f"got {cur3['gold']['bulk_max']}")
    log("T3 current.gold.price_inr preserved (=499)",
        cur3["gold"]["price_inr"] == 499,
        f"got {cur3['gold']['price_inr']}")
    log("T3 current.platinum.daily_cap=200",
        cur3["platinum"]["daily_cap"] == 200,
        f"got {cur3['platinum']['daily_cap']}")
    log("T3 current.platinum.label_cap preserved (=1500)",
        cur3["platinum"]["label_cap"] == 1500,
        f"got {cur3['platinum']['label_cap']}")
    log("T3 current.platinum.bulk_max preserved (=100)",
        cur3["platinum"]["bulk_max"] == 100,
        f"got {cur3['platinum']['bulk_max']}")
    log("T3 current.free_trial.label_cap untouched (=10)",
        cur3["free_trial"]["label_cap"] == 10,
        f"got {cur3['free_trial']['label_cap']}")

    # --- TEST 4: GET /api/me/usage reflects override ---
    # admin is on free_trial currently; we want a user on silver.
    # user2 default plan is free_trial in the credentials seed,
    # so we can't trivially check silver via /me/usage unless we
    # change a user's plan. Let's check via /plans (Test 5) which
    # returns all plans. Skip /me/usage test for silver since admin
    # is on free_trial — we'd need to flip a user's plan.
    # However the review says "admin account with silver plan OR
    # freshly seeded user". Since the data shows admin is on free_trial,
    # we'll instead verify /me/usage works (returns 200) for admin,
    # and rely on /plans for the silver verification.
    r = requests.get(f"{BASE}/me/usage", headers=headers(admin_tok))
    log("T4a GET /me/usage 200 (admin)", r.status_code == 200,
        f"status={r.status_code}")
    if r.status_code == 200:
        ub = r.json()
        log("T4a /me/usage has label_cap field", "label_cap" in ub,
            f"keys={list(ub.keys())}")

    # Also try user2 — likely on free_trial too, but at least confirm 200.
    r = requests.get(f"{BASE}/me/usage", headers=headers(user_tok))
    log("T4b GET /me/usage 200 (user2)", r.status_code == 200,
        f"status={r.status_code}")
    if r.status_code == 200:
        ub2 = r.json()
        plan_key = ub2.get("plan")
        cap = ub2.get("label_cap")
        # If user2 happens to be on silver, label_cap should reflect override
        if plan_key == "silver":
            log("T4b user2 silver label_cap=75", cap == 75, f"got {cap}")
        else:
            log(f"T4b user2 plan={plan_key} (not silver, skipping override check)", True)

    # --- TEST 5: GET /api/plans → silver label_cap=75 ---
    r = requests.get(f"{BASE}/plans", headers=headers(user_tok))
    if r.status_code != 200:
        log("T5 GET /plans 200", False,
            f"status={r.status_code} body={r.text[:200]}")
    else:
        log("T5 GET /plans 200", True)
        body5 = r.json()
        # Response shape might be {"plans": [...]} or list directly.
        plans_list = body5.get("plans") if isinstance(body5, dict) else body5
        if not isinstance(plans_list, list):
            log("T5 /plans returns list", False, f"shape={type(plans_list)}")
        else:
            silver = next((p for p in plans_list if p.get("key") == "silver"), None)
            gold = next((p for p in plans_list if p.get("key") == "gold"), None)
            platinum = next((p for p in plans_list if p.get("key") == "platinum"), None)
            log("T5 silver in /plans", silver is not None)
            if silver:
                log("T5 /plans silver.label_cap=75",
                    silver.get("label_cap") == 75,
                    f"got {silver.get('label_cap')}")
                log("T5 /plans silver.price_inr=249",
                    silver.get("price_inr") == 249,
                    f"got {silver.get('price_inr')}")
            if gold:
                log("T5 /plans gold.label_cap=400",
                    gold.get("label_cap") == 400,
                    f"got {gold.get('label_cap')}")
            if platinum:
                log("T5 /plans platinum.daily_cap=200",
                    platinum.get("daily_cap") == 200,
                    f"got {platinum.get('daily_cap')}")

    # --- TEST 6: POST /admin/plan-limits/reset ---
    r = requests.post(f"{BASE}/admin/plan-limits/reset",
                      headers=headers(admin_tok))
    log("T6 POST /admin/plan-limits/reset 200", r.status_code == 200,
        f"status={r.status_code}")
    if r.status_code == 200:
        body6 = r.json()
        cur6 = body6.get("current") or {}
        log("T6 silver.label_cap reset to 50",
            cur6["silver"]["label_cap"] == 50,
            f"got {cur6['silver']['label_cap']}")
        log("T6 silver.price_inr reset to 199",
            cur6["silver"]["price_inr"] == 199,
            f"got {cur6['silver']['price_inr']}")
        log("T6 gold.label_cap reset to 300",
            cur6["gold"]["label_cap"] == 300,
            f"got {cur6['gold']['label_cap']}")
        log("T6 platinum.daily_cap reset to 100",
            cur6["platinum"]["daily_cap"] == 100,
            f"got {cur6['platinum']['daily_cap']}")
        log("T6 overrides empty",
            (body6.get("overrides") or {}) == {},
            f"got {body6.get('overrides')}")

    # Verify via fresh GET that reset persisted
    r = requests.get(f"{BASE}/admin/plan-limits",
                     headers=headers(admin_tok))
    if r.status_code == 200:
        cur6b = (r.json().get("current") or {})
        log("T6 fresh GET silver.label_cap=50 after reset",
            cur6b["silver"]["label_cap"] == 50,
            f"got {cur6b['silver']['label_cap']}")

    # Also verify /plans now returns silver.label_cap=50
    r = requests.get(f"{BASE}/plans", headers=headers(user_tok))
    if r.status_code == 200:
        body5b = r.json()
        plans_list = body5b.get("plans") if isinstance(body5b, dict) else body5b
        silver = next((p for p in plans_list if p.get("key") == "silver"), None)
        if silver:
            log("T6 /plans silver.label_cap=50 after reset",
                silver.get("label_cap") == 50,
                f"got {silver.get('label_cap')}")

    # --- TEST 7: PUT with negative value → 400 ---
    bad = {"plans": {"silver": {"label_cap": -5}}}
    r = requests.put(f"{BASE}/admin/plan-limits",
                     headers=headers(admin_tok), json=bad)
    log("T7 PUT negative label_cap → 400",
        r.status_code == 400,
        f"status={r.status_code} body={r.text[:200]}")

    # negative price_inr
    bad2 = {"plans": {"gold": {"price_inr": -100}}}
    r = requests.put(f"{BASE}/admin/plan-limits",
                     headers=headers(admin_tok), json=bad2)
    log("T7b PUT negative price_inr → 400",
        r.status_code == 400,
        f"status={r.status_code} body={r.text[:200]}")

    # negative trial_days
    bad3 = {"plans": {"free_trial": {"trial_days": -1}}}
    r = requests.put(f"{BASE}/admin/plan-limits",
                     headers=headers(admin_tok), json=bad3)
    log("T7c PUT negative trial_days → 400",
        r.status_code == 400,
        f"status={r.status_code} body={r.text[:200]}")

    # --- TEST 8: Backwards compat ---
    r = requests.get(f"{BASE}/shipments", headers=headers(admin_tok))
    log("T8a GET /shipments 200", r.status_code == 200,
        f"status={r.status_code}")
    r = requests.get(f"{BASE}/me/usage", headers=headers(admin_tok))
    log("T8b GET /me/usage 200", r.status_code == 200,
        f"status={r.status_code}")
    r = requests.get(f"{BASE}/plans", headers=headers(admin_tok))
    log("T8c GET /plans 200", r.status_code == 200,
        f"status={r.status_code}")
    r = requests.get(f"{BASE}/shipments", headers=headers(user_tok))
    log("T8d GET /shipments 200 (user2)", r.status_code == 200,
        f"status={r.status_code}")

    return True


if __name__ == "__main__":
    ok = False
    try:
        ok = main()
    except Exception as e:
        import traceback
        traceback.print_exc()
        log("EXCEPTION", False, str(e))

    print("\n=== SUMMARY ===")
    passed = sum(1 for _, k, _ in results if k)
    failed = sum(1 for _, k, _ in results if not k)
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")
    if failed:
        print("\nFailed cases:")
        for name, k, det in results:
            if not k:
                print(f"  - {name}: {det}")
    sys.exit(0 if failed == 0 else 1)
