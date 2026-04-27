"""
Backend tests for Razorpay Plan-Subscription endpoints.
Targets the new /api/plans/razorpay/* endpoints in /app/backend/server.py.
"""
import os
import sys
import json
import requests
from pymongo import MongoClient

BASE = os.environ.get("TEST_BASE", "http://localhost:8001")
API = f"{BASE}/api"

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASS = "Admin@12345"

results = []  # list of (name, passed, info)


def record(name, passed, info=""):
    status = "PASS" if passed else "FAIL"
    results.append((name, passed, info))
    suffix = f"  {info}" if not passed and info else ""
    print(f"[{status}] {name}{suffix}")


def login(email, pwd):
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": pwd}, timeout=20)
    r.raise_for_status()
    return r.json()["token"]


def main():
    try:
        tok = login(ADMIN_EMAIL, ADMIN_PASS)
        record("auth.login admin", True)
    except Exception as e:
        record("auth.login admin", False, f"login failed: {e}")
        print_summary()
        sys.exit(1)
    H = {"Authorization": f"Bearer {tok}"}

    mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    db_name = os.environ.get("DB_NAME", "test_database")
    client = MongoClient(mongo_url)
    coll = client[db_name].razorpay_orders
    users_coll = client[db_name].users

    # ── 1.a silver/monthly ─────────────────────────────────────────────
    silver_order_id = None
    try:
        r = requests.post(f"{API}/plans/razorpay/create-order", headers=H,
                          json={"plan_key": "silver", "billing_cycle": "monthly"}, timeout=30)
        record("1.a status==200", r.status_code == 200, f"got {r.status_code}: {r.text[:300]}")
        if r.status_code == 200:
            j = r.json()
            silver_order_id = j.get("order_id")
            for n, ok in [
                ("key_id present", bool(j.get("key_id"))),
                ("order_id starts with order_", str(j.get("order_id", "")).startswith("order_")),
                ("amount_paise==19900", j.get("amount_paise") == 19900),
                ("amount_inr==199", j.get("amount_inr") == 199),
                ("plan_key==silver", j.get("plan_key") == "silver"),
                ("plan_name==Silver", j.get("plan_name") == "Silver"),
                ("billing_cycle==monthly", j.get("billing_cycle") == "monthly"),
                ("months==1", j.get("months") == 1),
                ("bonus_months==0", j.get("bonus_months") == 0),
                ("purpose==plan_subscription", j.get("purpose") == "plan_subscription"),
            ]:
                record(f"1.a {n}", ok, f"resp={json.dumps(j)[:300]}")
    except Exception as e:
        record("1.a silver/monthly", False, str(e))

    # ── 1.b gold/yearly ────────────────────────────────────────────────
    try:
        r = requests.post(f"{API}/plans/razorpay/create-order", headers=H,
                          json={"plan_key": "gold", "billing_cycle": "yearly"}, timeout=30)
        record("1.b status==200", r.status_code == 200, f"got {r.status_code}: {r.text[:300]}")
        if r.status_code == 200:
            j = r.json()
            for n, ok in [
                ("amount_paise==449100", j.get("amount_paise") == 449100),
                ("amount_inr==4491", j.get("amount_inr") == 4491),
                ("months==12", j.get("months") == 12),
                ("bonus_months==1", j.get("bonus_months") == 1),
                ("plan_key==gold", j.get("plan_key") == "gold"),
                ("billing_cycle==yearly", j.get("billing_cycle") == "yearly"),
            ]:
                record(f"1.b {n}", ok, f"resp={json.dumps(j)[:300]}")
    except Exception as e:
        record("1.b gold/yearly", False, str(e))

    # ── 1.c platinum/monthly ───────────────────────────────────────────
    try:
        r = requests.post(f"{API}/plans/razorpay/create-order", headers=H,
                          json={"plan_key": "platinum", "billing_cycle": "monthly"}, timeout=30)
        record("1.c status==200", r.status_code == 200, f"got {r.status_code}: {r.text[:300]}")
        if r.status_code == 200:
            j = r.json()
            record("1.c amount_paise==99900", j.get("amount_paise") == 99900,
                   f"resp={json.dumps(j)[:300]}")
    except Exception as e:
        record("1.c platinum/monthly", False, str(e))

    # ── 1.d invalid billing_cycle ──────────────────────────────────────
    try:
        r = requests.post(f"{API}/plans/razorpay/create-order", headers=H,
                          json={"plan_key": "silver", "billing_cycle": "weekly"}, timeout=30)
        ok = r.status_code == 400 and ("monthly" in r.text.lower() or "yearly" in r.text.lower())
        record("1.d weekly→400 mentions monthly/yearly", ok,
               f"status={r.status_code} body={r.text[:300]}")
    except Exception as e:
        record("1.d invalid billing_cycle", False, str(e))

    # ── 1.e free_trial ─────────────────────────────────────────────────
    try:
        r = requests.post(f"{API}/plans/razorpay/create-order", headers=H,
                          json={"plan_key": "free_trial", "billing_cycle": "monthly"}, timeout=30)
        ok = r.status_code == 400 and "Cannot subscribe to plan 'free_trial'" in r.text
        record("1.e free_trial→400 with proper detail", ok,
               f"status={r.status_code} body={r.text[:300]}")
    except Exception as e:
        record("1.e free_trial", False, str(e))

    # ── 1.f diamond ────────────────────────────────────────────────────
    try:
        r = requests.post(f"{API}/plans/razorpay/create-order", headers=H,
                          json={"plan_key": "diamond", "billing_cycle": "monthly"}, timeout=30)
        ok = r.status_code == 400 and (
            "Cannot subscribe to plan 'diamond'" in r.text or
            "Unknown plan 'diamond'" in r.text)
        record("1.f diamond→400 with proper detail", ok,
               f"status={r.status_code} body={r.text[:300]}")
    except Exception as e:
        record("1.f diamond", False, str(e))

    # ── 1.g missing token ──────────────────────────────────────────────
    try:
        r = requests.post(f"{API}/plans/razorpay/create-order",
                          json={"plan_key": "silver", "billing_cycle": "monthly"}, timeout=30)
        record("1.g missing token→401", r.status_code == 401,
               f"status={r.status_code} body={r.text[:300]}")
    except Exception as e:
        record("1.g no auth", False, str(e))

    # ── 1.h DB has the order ───────────────────────────────────────────
    if silver_order_id:
        try:
            doc = coll.find_one({"razorpay_order_id": silver_order_id})
            ok = (doc is not None
                  and doc.get("purpose") == "plan_subscription"
                  and doc.get("plan_key") == "silver"
                  and doc.get("billing_cycle") == "monthly"
                  and doc.get("status") == "created")
            record("1.h db.razorpay_orders has plan_subscription doc", ok,
                   f"doc={doc}")
        except Exception as e:
            record("1.h db.razorpay_orders verification", False, str(e))
    else:
        record("1.h db.razorpay_orders verification", False, "skipped — silver order missing")

    # ── 2.a bogus verify on real plan order ────────────────────────────
    if silver_order_id:
        try:
            r = requests.post(f"{API}/plans/razorpay/verify", headers=H,
                              json={"razorpay_order_id": silver_order_id,
                                    "razorpay_payment_id": "pay_FAKE",
                                    "razorpay_signature": "xxx"}, timeout=30)
            ok = r.status_code == 400 and "Payment verification failed" in r.text
            record("2.a bogus verify→400 'Payment verification failed: …'", ok,
                   f"status={r.status_code} body={r.text[:300]}")
            doc = coll.find_one({"razorpay_order_id": silver_order_id})
            ok2 = doc is not None and doc.get("status") == "verify_failed"
            record("2.a db row status==verify_failed", ok2,
                   f"status={doc.get('status') if doc else None}")
        except Exception as e:
            record("2.a bogus verify", False, str(e))

    # ── 2.b unknown order ──────────────────────────────────────────────
    try:
        r = requests.post(f"{API}/plans/razorpay/verify", headers=H,
                          json={"razorpay_order_id": "order_DOESNOTEXIST_xxxxx",
                                "razorpay_payment_id": "pay_x",
                                "razorpay_signature": "y"}, timeout=30)
        ok = r.status_code == 404 and "Order not found for this user" in r.text
        record("2.b unknown order→404", ok,
               f"status={r.status_code} body={r.text[:300]}")
    except Exception as e:
        record("2.b unknown order", False, str(e))

    # ── 2.c wallet topup order via plan /verify → 400 ──────────────────
    wallet_order_id = None
    try:
        r = requests.post(f"{API}/wallet/razorpay/create-order", headers=H,
                          json={"amount_inr": 100}, timeout=30)
        if r.status_code == 200:
            wallet_order_id = r.json().get("order_id")
            record("2.c precondition wallet topup created", True)
        else:
            record("2.c precondition wallet topup created", False,
                   f"status={r.status_code} body={r.text[:300]}")
    except Exception as e:
        record("2.c precondition wallet topup created", False, str(e))

    if wallet_order_id:
        try:
            r = requests.post(f"{API}/plans/razorpay/verify", headers=H,
                              json={"razorpay_order_id": wallet_order_id,
                                    "razorpay_payment_id": "pay_x",
                                    "razorpay_signature": "y"}, timeout=30)
            ok = r.status_code == 400 and "isn't a plan subscription" in r.text
            record("2.c wallet topup via plan/verify→400 'isn't a plan subscription'", ok,
                   f"status={r.status_code} body={r.text[:300]}")
        except Exception as e:
            record("2.c wallet via plan verify", False, str(e))

    # ── 2.d Idempotency ────────────────────────────────────────────────
    idem_order_id = None
    try:
        r = requests.post(f"{API}/plans/razorpay/create-order", headers=H,
                          json={"plan_key": "silver", "billing_cycle": "monthly"}, timeout=30)
        if r.status_code == 200:
            idem_order_id = r.json().get("order_id")
            record("2.d precondition new plan order created", True)
        else:
            record("2.d precondition new plan order created", False,
                   f"status={r.status_code} body={r.text[:300]}")
    except Exception as e:
        record("2.d precondition new plan order created", False, str(e))

    if idem_order_id:
        try:
            from datetime import datetime, timezone, timedelta
            future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
            coll.update_one(
                {"razorpay_order_id": idem_order_id},
                {"$set": {"status": "paid"}}
            )
            users_coll.update_one(
                {"email": ADMIN_EMAIL},
                {"$set": {"plan": "silver", "plan_expires_at": future}}
            )

            r = requests.post(f"{API}/plans/razorpay/verify", headers=H,
                              json={"razorpay_order_id": idem_order_id,
                                    "razorpay_payment_id": "pay_idem",
                                    "razorpay_signature": "sig_idem"}, timeout=30)
            record("2.d idempotency status==200", r.status_code == 200,
                   f"status={r.status_code} body={r.text[:300]}")
            if r.status_code == 200:
                j = r.json()
                record("2.d already_credited==true", j.get("already_credited") is True,
                       f"resp={j}")
                record("2.d plan_expires_at populated", bool(j.get("plan_expires_at")),
                       f"resp={j}")
        except Exception as e:
            record("2.d idempotency", False, str(e))

    # ── 3. Soft check: wallet/verify on plan order should NOT credit ───
    plan_order_for_wallet_test = None
    try:
        r = requests.post(f"{API}/plans/razorpay/create-order", headers=H,
                          json={"plan_key": "silver", "billing_cycle": "monthly"}, timeout=30)
        if r.status_code == 200:
            plan_order_for_wallet_test = r.json().get("order_id")
    except Exception:
        pass

    if plan_order_for_wallet_test:
        try:
            r = requests.post(f"{API}/wallet/razorpay/verify", headers=H,
                              json={"razorpay_order_id": plan_order_for_wallet_test,
                                    "razorpay_payment_id": "pay_x",
                                    "razorpay_signature": "y"}, timeout=30)
            credited = False
            try:
                if r.status_code == 200:
                    j = r.json()
                    if float(j.get("credits_added", 0)) > 0 and not j.get("already_credited"):
                        credited = True
            except Exception:
                pass
            info = f"status={r.status_code} body={r.text[:300]} credited={credited}"
            # We want non-credit. Pass if not credited.
            record("3. wallet verify on plan order does NOT credit (soft)", not credited, info)
        except Exception as e:
            record("3. wallet verify on plan order", False, str(e))

    # ── 4. User state after idempotency call ───────────────────────────
    try:
        u = users_coll.find_one({"email": ADMIN_EMAIL}, {"_id": 0})
        # The idempotency path doesn't mutate user. We just confirm the
        # user doc still has the values we set in 2.d (plan, plan_expires_at).
        ok = bool(u and u.get("plan_expires_at"))
        info = f"plan={u.get('plan')} plan_expires_at={u.get('plan_expires_at')} plan_billing_cycle={u.get('plan_billing_cycle')} plan_mocked={u.get('plan_mocked')} last_paid_payment_id={u.get('last_paid_payment_id')}" if u else "user not found"
        record("4. user has plan_expires_at set", ok, info)
    except Exception as e:
        record("4. user state check", False, str(e))

    print_summary()


def print_summary():
    print("\n" + "=" * 70)
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"TOTAL: {passed}/{total} passed")
    print("=" * 70)
    fails = [(n, info) for n, ok, info in results if not ok]
    if fails:
        print("\nFAILED:")
        for n, info in fails:
            print(f"  - {n}\n      {info}")


if __name__ == "__main__":
    main()
