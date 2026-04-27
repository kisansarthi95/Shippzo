"""
Phase-4d backend tests:
  - GET /api/me/notification-prefs
  - PUT /api/me/notification-prefs
  - POST /api/me/cancel-subscription
  - GET /api/me/usage (new fields incl. plan-expiry)
  - POST /api/shipments  (plan-expiry enforcement)
  - GET  /api/customers/by-phone/{phone}  (last_items + last_amount)
"""

import os
import sys
import json
import time
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

import requests
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

# Use the public REACT_APP/EXPO backend URL
BASE_URL = "https://logistics-hub-740.preview.emergentagent.com/api"
ADMIN_EMAIL = "admin@test.com"
ADMIN_PASSWORD = "Admin@12345"

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "test_database")

PASSED = []
FAILED = []


def record(name: str, ok: bool, detail: str = ""):
    line = f"{'PASS' if ok else 'FAIL'} :: {name}"
    if detail:
        line += f"  ::  {detail}"
    if ok:
        PASSED.append(line)
        print(line)
    else:
        FAILED.append(line)
        print(line)


def assert_eq(name: str, got: Any, want: Any):
    record(name, got == want, f"got={got!r} want={want!r}")


# ---------------- Auth helpers ----------------

def login(email: str, password: str) -> str:
    r = requests.post(
        f"{BASE_URL}/auth/login",
        json={"email": email, "password": password},
        timeout=20,
    )
    assert r.status_code == 200, f"login failed {r.status_code} {r.text}"
    return r.json()["token"]


def auth_headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# ---------------- DB helpers ----------------

async def get_admin_user_doc():
    cli = AsyncIOMotorClient(MONGO_URL)
    try:
        db = cli[DB_NAME]
        u = await db.users.find_one({"email": ADMIN_EMAIL}, {"_id": 0})
        return u
    finally:
        cli.close()


async def set_admin_plan(plan: str, plan_expires_at: Optional[str] = None,
                        plan_billing_cycle: Optional[str] = None,
                        plan_mocked: bool = True,
                        unset: Optional[list] = None):
    cli = AsyncIOMotorClient(MONGO_URL)
    try:
        db = cli[DB_NAME]
        update_set = {"plan": plan, "plan_mocked": plan_mocked}
        if plan_expires_at is not None:
            update_set["plan_expires_at"] = plan_expires_at
        if plan_billing_cycle is not None:
            update_set["plan_billing_cycle"] = plan_billing_cycle
        ops: Dict[str, Any] = {"$set": update_set}
        if unset:
            ops["$unset"] = {k: "" for k in unset}
        await db.users.update_one({"email": ADMIN_EMAIL}, ops)
    finally:
        cli.close()


async def reset_admin_to_free_trial():
    """Restore admin to free_trial state, removing all paid-plan fields."""
    cli = AsyncIOMotorClient(MONGO_URL)
    try:
        db = cli[DB_NAME]
        # Set plan_expires_at back to a 7-day future trial window
        new_exp = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        await db.users.update_one(
            {"email": ADMIN_EMAIL},
            {
                "$set": {"plan": "free_trial", "plan_mocked": False,
                         "plan_expires_at": new_exp},
                "$unset": {
                    "plan_billing_cycle": "",
                    "auto_renew": "",
                    "cancelled_at": "",
                },
            },
        )
    finally:
        cli.close()


async def cleanup_test_shipments(phone_tail: str = "9999988888"):
    cli = AsyncIOMotorClient(MONGO_URL)
    try:
        db = cli[DB_NAME]
        r = await db.shipments.delete_many({
            "customer_phone": {"$regex": f"{phone_tail}$"}
        })
        return r.deleted_count
    finally:
        cli.close()


# ---------------- Tests ----------------

DEFAULT_PREFS_KEYS = {
    "channel_push": True,
    "channel_email": True,
    "trial_ending": True,
    "plan_expiring": True,
    "low_credits": True,
    "payment_success": True,
    "daily_summary": False,
}


def test_notification_prefs_get_default(token: str):
    print("\n--- 1) GET /api/me/notification-prefs ---")
    # Reset notification_prefs in DB so we test the "fresh" default.
    async def _reset_prefs():
        cli = AsyncIOMotorClient(MONGO_URL)
        try:
            db = cli[DB_NAME]
            await db.users.update_one(
                {"email": ADMIN_EMAIL}, {"$unset": {"notification_prefs": ""}}
            )
        finally:
            cli.close()
    asyncio.get_event_loop().run_until_complete(_reset_prefs())

    r = requests.get(
        f"{BASE_URL}/me/notification-prefs",
        headers=auth_headers(token), timeout=20,
    )
    record("GET notification-prefs status 200", r.status_code == 200, str(r.status_code))
    if r.status_code != 200:
        record("GET notification-prefs body", False, r.text[:300])
        return
    body = r.json()
    for k, want in DEFAULT_PREFS_KEYS.items():
        record(f"default prefs[{k}] == {want}", body.get(k) is want, f"got={body.get(k)!r}")
        record(f"default prefs[{k}] is bool", isinstance(body.get(k), bool), f"type={type(body.get(k)).__name__}")
    # All expected keys present
    missing = [k for k in DEFAULT_PREFS_KEYS if k not in body]
    record("default prefs has all expected keys", not missing, f"missing={missing}")


def test_notification_prefs_put_partial(token: str):
    print("\n--- 2) PUT /api/me/notification-prefs (partial update) ---")
    payload = {"low_credits": False, "daily_summary": True}
    r = requests.put(
        f"{BASE_URL}/me/notification-prefs",
        headers=auth_headers(token),
        json=payload, timeout=20,
    )
    record("PUT status 200", r.status_code == 200, str(r.status_code))
    if r.status_code != 200:
        record("PUT body", False, r.text[:300])
        return
    body = r.json()
    record("PUT response low_credits=False", body.get("low_credits") is False, f"got={body.get('low_credits')!r}")
    record("PUT response daily_summary=True", body.get("daily_summary") is True, f"got={body.get('daily_summary')!r}")
    # Other defaults preserved
    for k in ("channel_push", "channel_email", "trial_ending", "plan_expiring", "payment_success"):
        record(f"PUT preserves {k}=True", body.get(k) is True, f"got={body.get(k)!r}")

    # GET again — confirm persistence
    r2 = requests.get(f"{BASE_URL}/me/notification-prefs",
                      headers=auth_headers(token), timeout=20)
    record("GET-after-PUT status 200", r2.status_code == 200, str(r2.status_code))
    body2 = r2.json()
    record("persistence low_credits=False", body2.get("low_credits") is False, f"got={body2.get('low_credits')!r}")
    record("persistence daily_summary=True", body2.get("daily_summary") is True, f"got={body2.get('daily_summary')!r}")

    # Reset back to defaults
    reset_payload = {"low_credits": True, "daily_summary": False}
    rr = requests.put(f"{BASE_URL}/me/notification-prefs",
                      headers=auth_headers(token),
                      json=reset_payload, timeout=20)
    record("reset-to-defaults status 200", rr.status_code == 200, str(rr.status_code))


def test_cancel_subscription_on_free_trial(token: str):
    print("\n--- 3a) POST /api/me/cancel-subscription on free_trial → 400 ---")
    r = requests.post(
        f"{BASE_URL}/me/cancel-subscription",
        headers=auth_headers(token), timeout=20,
    )
    record("cancel-subscription on trial status 400", r.status_code == 400, f"got={r.status_code} body={r.text[:200]}")
    if r.status_code == 400:
        try:
            detail = (r.json().get("detail") or "").lower()
            ok = ("free trial" in detail) or ("nothing to cancel" in detail)
            record("cancel-subscription detail mentions 'free trial'/'nothing to cancel'",
                   ok, f"detail={detail!r}")
        except Exception as e:
            record("cancel-subscription detail parse", False, str(e))


def test_cancel_subscription_on_paid(token: str):
    print("\n--- 3b) POST /api/me/cancel-subscription on silver paid → 200 ---")
    future_iso = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    asyncio.get_event_loop().run_until_complete(
        set_admin_plan("silver", plan_expires_at=future_iso,
                       plan_billing_cycle="monthly")
    )

    r = requests.post(f"{BASE_URL}/me/cancel-subscription",
                      headers=auth_headers(token), timeout=20)
    record("cancel-subscription paid status 200", r.status_code == 200, f"got={r.status_code} body={r.text[:200]}")
    if r.status_code == 200:
        body = r.json()
        record("cancel-subscription ok=true", body.get("ok") is True, f"got={body.get('ok')!r}")
        record("cancel-subscription plan='silver'", body.get("plan") == "silver", f"got={body.get('plan')!r}")
        record("cancel-subscription plan_expires_at echoed",
               body.get("plan_expires_at") == future_iso,
               f"got={body.get('plan_expires_at')!r}")

    # Verify auto_renew=false on user via DB
    user = asyncio.get_event_loop().run_until_complete(get_admin_user_doc())
    record("auto_renew=False after cancel",
           user.get("auto_renew") is False, f"auto_renew={user.get('auto_renew')!r}")
    record("cancelled_at set after cancel",
           bool(user.get("cancelled_at")), f"cancelled_at={user.get('cancelled_at')!r}")


def test_usage_for_free_trial(token: str):
    print("\n--- 4a) GET /api/me/usage on free_trial ---")
    asyncio.get_event_loop().run_until_complete(reset_admin_to_free_trial())
    r = requests.get(f"{BASE_URL}/me/usage", headers=auth_headers(token), timeout=20)
    record("usage status 200", r.status_code == 200, str(r.status_code))
    if r.status_code != 200:
        record("usage body", False, r.text[:300])
        return
    body = r.json()
    print("free_trial usage body:", json.dumps(body, indent=2, default=str))

    record("usage period == 'trial'", body.get("period") == "trial", f"got={body.get('period')!r}")
    # NEW fields the review explicitly asks for on the trial response:
    record("usage trial plan_expires_at is None",
           body.get("plan_expires_at") is None, f"got={body.get('plan_expires_at')!r}")
    record("usage trial plan_days_left is None",
           body.get("plan_days_left") is None, f"got={body.get('plan_days_left')!r}")
    record("usage trial plan_expired is False",
           body.get("plan_expired") is False, f"got={body.get('plan_expired')!r}")
    record("usage trial plan_billing_cycle is None",
           body.get("plan_billing_cycle") is None, f"got={body.get('plan_billing_cycle')!r}")
    # Existing fields still present
    for k in ("label_cap", "labels_used", "can_create_label", "trial_days_left"):
        record(f"usage trial has {k}", k in body, f"keys_present={k in body}")


def test_usage_for_paid_active(token: str):
    print("\n--- 4b) GET /api/me/usage on silver active (future expiry) ---")
    future_iso = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    asyncio.get_event_loop().run_until_complete(
        set_admin_plan("silver", plan_expires_at=future_iso,
                       plan_billing_cycle="monthly")
    )
    r = requests.get(f"{BASE_URL}/me/usage", headers=auth_headers(token), timeout=20)
    record("usage paid active status 200", r.status_code == 200, str(r.status_code))
    if r.status_code != 200:
        record("usage paid active body", False, r.text[:300])
        return
    body = r.json()
    print("paid active usage body:", json.dumps(body, indent=2, default=str))

    record("usage paid period == 'month'", body.get("period") == "month", f"got={body.get('period')!r}")
    record("usage paid plan_expires_at == future_iso",
           body.get("plan_expires_at") == future_iso, f"got={body.get('plan_expires_at')!r}")
    days_left = body.get("plan_days_left")
    record("usage paid plan_days_left >= 1 int",
           isinstance(days_left, int) and days_left >= 1, f"got={days_left!r}")
    record("usage paid plan_expired False",
           body.get("plan_expired") is False, f"got={body.get('plan_expired')!r}")
    record("usage paid plan_billing_cycle 'monthly' or None",
           body.get("plan_billing_cycle") in ("monthly", None),
           f"got={body.get('plan_billing_cycle')!r}")
    record("usage paid can_create_label True",
           body.get("can_create_label") is True, f"got={body.get('can_create_label')!r}")


def test_usage_for_paid_expired(token: str):
    print("\n--- 4c) GET /api/me/usage on silver EXPIRED ---")
    past_iso = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    asyncio.get_event_loop().run_until_complete(
        set_admin_plan("silver", plan_expires_at=past_iso,
                       plan_billing_cycle="monthly")
    )
    r = requests.get(f"{BASE_URL}/me/usage", headers=auth_headers(token), timeout=20)
    record("usage paid expired status 200", r.status_code == 200, str(r.status_code))
    if r.status_code != 200:
        record("usage paid expired body", False, r.text[:300])
        return
    body = r.json()
    print("paid expired usage body:", json.dumps(body, indent=2, default=str))

    record("usage expired plan_expired True",
           body.get("plan_expired") is True, f"got={body.get('plan_expired')!r}")
    record("usage expired plan_days_left == 0",
           body.get("plan_days_left") == 0, f"got={body.get('plan_days_left')!r}")
    record("usage expired can_create_label False ← KEY CHECK",
           body.get("can_create_label") is False, f"got={body.get('can_create_label')!r}")


def test_create_shipment_blocks_when_plan_expired(token: str):
    print("\n--- 5) POST /api/shipments on expired silver → 402 ---")
    past_iso = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    asyncio.get_event_loop().run_until_complete(
        set_admin_plan("silver", plan_expires_at=past_iso,
                       plan_billing_cycle="monthly")
    )
    payload = {
        "tracking_id": "EXP-TEST-001",
        "customer_name": "Expired Plan Test",
        "customer_phone": "9000000000",
        "address_line1": "1, Test Lane",
        "city": "Surat", "state": "Gujarat", "pincode": "395001",
        "items": ["TestItem x 1"],
        "amount": 10.0, "payment_mode": "Prepaid",
    }
    r = requests.post(f"{BASE_URL}/shipments",
                      headers=auth_headers(token),
                      json=payload, timeout=30)
    record("create shipment on expired plan returns 402",
           r.status_code == 402, f"got={r.status_code} body={r.text[:300]}")
    if r.status_code == 402:
        try:
            detail = (r.json().get("detail") or "").lower()
            record("402 detail mentions 'expired'", "expired" in detail, f"detail={detail!r}")
            # Date check is best-effort: the date string format used in the
            # endpoint is "%d %b %Y" — so look for a digit pattern.
            import re
            has_date = bool(re.search(r"\d{1,2}\s+\w+\s+\d{4}", detail))
            record("402 detail mentions a date", has_date, f"detail={detail!r}")
        except Exception as e:
            record("402 detail parse", False, str(e))

    # Verify shipment was NOT created
    cli = AsyncIOMotorClient(MONGO_URL)
    try:
        db = cli[DB_NAME]
        async def _check():
            return await db.shipments.find_one({"tracking_id": "EXP-TEST-001"})
        leftover = asyncio.get_event_loop().run_until_complete(_check())
        record("shipment NOT created on expired plan",
               leftover is None, f"leftover={'present' if leftover else 'none'}")
    finally:
        cli.close()


def test_customer_by_phone_new_fields(token: str):
    print("\n--- 6) GET /api/customers/by-phone/9999988888 — new fields ---")

    # Make sure admin is back on free_trial so create_shipment passes.
    asyncio.get_event_loop().run_until_complete(reset_admin_to_free_trial())

    # Pick a courier owned by admin so courier_id resolves cleanly.
    rcour = requests.get(f"{BASE_URL}/couriers", headers=auth_headers(token), timeout=20)
    courier_id = None
    if rcour.status_code == 200 and rcour.json():
        courier_id = rcour.json()[0]["id"]

    payload = {
        "tracking_id": f"CST-TEST-{int(time.time())}",
        "courier_id": courier_id,
        "customer_name": "Customer Phone Test",
        "customer_phone": "9999988888",
        "address_line1": "12 Test Street",
        "city": "Surat", "state": "Gujarat", "pincode": "395001",
        "items": ["TestItem x 2"],
        "amount": 199.0, "payment_mode": "Prepaid",
    }
    rs = requests.post(f"{BASE_URL}/shipments",
                       headers=auth_headers(token),
                       json=payload, timeout=30)
    record("create test shipment for by-phone", rs.status_code == 200,
           f"got={rs.status_code} body={rs.text[:200]}")
    if rs.status_code != 200:
        return

    rg = requests.get(f"{BASE_URL}/customers/by-phone/9999988888",
                      headers=auth_headers(token), timeout=20)
    record("by-phone status 200", rg.status_code == 200, f"got={rg.status_code} body={rg.text[:200]}")
    if rg.status_code != 200:
        return
    body = rg.json()
    print("by-phone body:", json.dumps(body, indent=2, default=str))
    record("by-phone found=True", body.get("found") is True, f"got={body.get('found')!r}")
    cust = body.get("customer") or {}
    # New fields
    last_items = cust.get("last_items")
    record("by-phone customer.last_items is list",
           isinstance(last_items, list), f"got={type(last_items).__name__}")
    record("by-phone customer.last_items contains 'TestItem x 2'",
           last_items and any("TestItem" in x for x in last_items),
           f"got={last_items!r}")
    last_amount = cust.get("last_amount")
    record("by-phone customer.last_amount == 199",
           float(last_amount or 0) == 199.0, f"got={last_amount!r}")
    # Existing fields
    for k in ("customer_name", "customer_phone", "address_line1", "city", "state", "pincode"):
        record(f"by-phone customer.{k} present", k in cust, f"keys={list(cust.keys())}")
    record("by-phone customer.customer_name correct",
           cust.get("customer_name") == "Customer Phone Test",
           f"got={cust.get('customer_name')!r}")

    # cleanup
    n = asyncio.get_event_loop().run_until_complete(cleanup_test_shipments("9999988888"))
    print(f"cleanup deleted {n} test shipments for 9999988888")


# ---------------- Main ----------------

def main():
    print(f"Base URL: {BASE_URL}")
    token = login(ADMIN_EMAIL, ADMIN_PASSWORD)
    print(f"Got admin token (len={len(token)})")

    try:
        test_notification_prefs_get_default(token)
        test_notification_prefs_put_partial(token)

        # cancel-subscription
        # Make sure admin starts on free_trial for the 400 path
        asyncio.get_event_loop().run_until_complete(reset_admin_to_free_trial())
        test_cancel_subscription_on_free_trial(token)
        test_cancel_subscription_on_paid(token)

        # usage tests
        test_usage_for_free_trial(token)
        test_usage_for_paid_active(token)
        test_usage_for_paid_expired(token)

        # plan-expiry on shipment create
        test_create_shipment_blocks_when_plan_expired(token)

        # customer by-phone new fields
        test_customer_by_phone_new_fields(token)

    finally:
        # Always restore admin to clean state
        asyncio.get_event_loop().run_until_complete(reset_admin_to_free_trial())
        print("\n[cleanup] admin restored to free_trial state")

    print("\n========== SUMMARY ==========")
    print(f"PASSED: {len(PASSED)}")
    print(f"FAILED: {len(FAILED)}")
    if FAILED:
        print("\nFAILURES:")
        for f in FAILED:
            print(" -", f)
    print("=============================")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
