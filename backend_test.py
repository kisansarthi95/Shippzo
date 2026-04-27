"""
Targeted retest for:
  Test 1: Plan-expiry enforcement on POST /api/shipments
  Test 2: GET /api/me/usage trial branch returns plan_expired=false
"""
import os
import sys
import json

import requests
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")

BASE = os.environ.get("EXPO_PUBLIC_BACKEND_URL",
                     "https://logistics-hub-740.preview.emergentagent.com").rstrip("/")
API = f"{BASE}/api"

MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASSWORD = "Admin@12345"


def login(email: str, password: str) -> dict:
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=30)
    r.raise_for_status()
    return r.json()


def headers(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}


def restore_clean_free_trial(users_col, admin_id):
    users_col.update_one(
        {"id": admin_id},
        {
            "$set": {"plan": "free_trial", "plan_mocked": False},
            "$unset": {
                "plan_expires_at": "",
                "plan_billing_cycle": "",
                "auto_renew": "",
                "cancelled_at": "",
            },
        },
    )


def main():
    print(f"API = {API}")
    print(f"MONGO_URL = {MONGO_URL}")
    print(f"DB_NAME = {DB_NAME}")

    client = MongoClient(MONGO_URL)
    db = client[DB_NAME]
    users = db.users
    shipments = db.shipments

    admin_login = login(ADMIN_EMAIL, ADMIN_PASSWORD)
    admin_tok = admin_login["token"]
    admin_id = admin_login["id"]
    print(f"Admin id = {admin_id}")

    results = {"test1": {}, "test2": {}}

    # ==== TEST 1 ====
    print("\n=== TEST 1: Plan-expiry enforcement on POST /api/shipments ===")
    try:
        before_count = shipments.count_documents({"user_id": admin_id})
        print(f"Shipment count before = {before_count}")

        past_iso = "2024-01-01T00:00:00+00:00"
        started_iso = "2023-12-01T00:00:00+00:00"
        users.update_one(
            {"id": admin_id},
            {"$set": {
                "plan": "silver",
                "plan_expires_at": past_iso,
                "plan_started_at": started_iso,
            }},
        )
        fresh = users.find_one({"id": admin_id}, {"_id": 0, "plan": 1, "plan_expires_at": 1, "plan_started_at": 1})
        print(f"Admin set to: {fresh}")

        payload = {
            "customer_name": "Retest Expiry User",
            "customer_phone": "9000001111",
            "address": "12 MG Road, Surat, Gujarat 395001",
            "courier": "Nandan Courier",
            "tracking_id": "RTEST-EXP-0001",
            "amount": 199.0,
            "payment_mode": "COD",
        }
        r = requests.post(f"{API}/shipments", headers=headers(admin_tok), json=payload, timeout=30)
        print(f"POST /shipments status = {r.status_code}")
        try:
            body = r.json()
        except Exception:
            body = {"raw": r.text}
        print(f"POST /shipments body = {json.dumps(body, indent=2, default=str)}")

        results["test1"]["status_code"] = r.status_code
        results["test1"]["body"] = body

        after_count = shipments.count_documents({"user_id": admin_id})
        print(f"Shipment count after = {after_count}")

        detail = str(body.get("detail", "")).lower()
        c1 = r.status_code == 402
        c2 = "expired" in detail
        c3 = ("2024" in detail) or ("jan" in detail)
        c4 = after_count == before_count

        print(f"  [{'PASS' if c1 else 'FAIL'}] HTTP 402 returned (got {r.status_code})")
        print(f"  [{'PASS' if c2 else 'FAIL'}] detail contains 'expired'")
        print(f"  [{'PASS' if c3 else 'FAIL'}] detail mentions the expiry date (2024/Jan)")
        print(f"  [{'PASS' if c4 else 'FAIL'}] shipment NOT created (count unchanged)")
        results["test1"]["pass"] = all([c1, c2, c3, c4])
    finally:
        shipments.delete_many({
            "user_id": admin_id,
            "tracking_id": "RTEST-EXP-0001",
        })

    # ==== TEST 2 ====
    print("\n=== TEST 2: GET /api/me/usage trial branch ===")
    users.update_one(
        {"id": admin_id},
        {
            "$set": {"plan": "free_trial", "plan_mocked": False},
            "$unset": {
                "plan_expires_at": "",
                "plan_billing_cycle": "",
                "auto_renew": "",
                "cancelled_at": "",
            },
        },
    )
    fresh = users.find_one({"id": admin_id}, {"_id": 0})
    print("After restore — relevant fields:")
    for k in ("plan", "plan_mocked", "plan_expires_at", "plan_billing_cycle",
              "auto_renew", "cancelled_at", "plan_started_at", "trial_expires_at"):
        print(f"  {k!r} = {fresh.get(k)!r}")

    r = requests.get(f"{API}/me/usage", headers=headers(admin_tok), timeout=30)
    print(f"GET /me/usage status = {r.status_code}")
    body = r.json()
    print(f"GET /me/usage body = {json.dumps(body, indent=2, default=str)}")

    results["test2"]["status_code"] = r.status_code
    results["test2"]["body"] = body

    checks = [
        ("period == 'trial'", body.get("period") == "trial"),
        ("plan_expired is False", body.get("plan_expired") is False),
        ("plan_expires_at is None", body.get("plan_expires_at") is None),
        ("plan_days_left is None", body.get("plan_days_left") is None),
        ("plan_billing_cycle is None", body.get("plan_billing_cycle") is None),
        ("trial_expired is False", body.get("trial_expired") is False),
        ("can_create_label is True", body.get("can_create_label") is True),
    ]
    all_pass = True
    for label, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        all_pass = all_pass and ok
    results["test2"]["pass"] = all_pass

    # ==== CLEANUP ====
    print("\n=== CLEANUP ===")
    restore_clean_free_trial(users, admin_id)
    cleaned = users.find_one({"id": admin_id}, {"_id": 0})
    print(f"After cleanup — plan={cleaned.get('plan')}, "
          f"plan_mocked={cleaned.get('plan_mocked')}, "
          f"plan_expires_at={cleaned.get('plan_expires_at')}, "
          f"plan_billing_cycle={cleaned.get('plan_billing_cycle')}, "
          f"auto_renew={cleaned.get('auto_renew')}, "
          f"cancelled_at={cleaned.get('cancelled_at')}")

    print("\n======== SUMMARY ========")
    print(f"  TEST 1 (plan-expiry 402): {'PASS' if results['test1'].get('pass') else 'FAIL'}")
    print(f"  TEST 2 (/me/usage trial): {'PASS' if results['test2'].get('pass') else 'FAIL'}")

    sys.exit(0 if (results['test1'].get('pass') and results['test2'].get('pass')) else 1)


if __name__ == "__main__":
    main()
