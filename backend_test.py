"""
Backend tests for Phase-4d auth/admin endpoints:
  * Test 1: /api/auth/signup phone validation + normalization
  * Test 2: /api/auth/me returns display_id + phone
  * Test 3: /api/auth/forgot-password (2-factor: email + phone)
  * Test 4: /api/admin/users/{id}/reset-password
  * Test 5: /api/admin/users returns display_id + phone
"""
import os
import sys
import re
import requests
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

BASE = "https://logistics-hub-740.preview.emergentagent.com/api"
MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]
ADMIN_EMAIL = "admin@test.com"
ADMIN_PASSWORD = "Admin@12345"

PASS = []
FAIL = []


def rec(ok, name, detail=""):
    if ok:
        PASS.append(name)
        print(f"  PASS - {name}")
    else:
        FAIL.append((name, detail))
        print(f"  FAIL - {name} :: {detail}")


def cleanup_users(emails):
    if not emails:
        return
    cli = MongoClient(MONGO_URL)
    db = cli[DB_NAME]
    user_ids = [u["id"] for u in db.users.find({"email": {"$in": emails}}, {"id": 1})]
    if user_ids:
        db.users.delete_many({"email": {"$in": emails}})
        for col in ("shipments", "couriers", "settings", "pending_orders",
                    "wallets", "wallet_history", "pwd_reset_attempts"):
            try:
                db[col].delete_many({"user_id": {"$in": user_ids}})
            except Exception:
                pass
    db.pwd_reset_attempts.delete_many({"email": {"$in": emails}})
    cli.close()


def admin_login():
    r = requests.post(
        f"{BASE}/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=20,
    )
    r.raise_for_status()
    return r.json()["token"]


def test1_signup_phone():
    print("\n=== TEST 1: Signup now requires phone ===")
    cleanup_users(["ptest1@example.com", "ptest1-2@example.com"])

    r = requests.post(f"{BASE}/auth/signup", json={
        "email": "ptest1@example.com", "password": "testpass123",
        "name": "Phone Tester", "shop_name": "PT Shop",
    })
    rec(
        r.status_code in (400, 422),
        "1a missing phone -> 422/400",
        f"got {r.status_code} body={r.text[:200]}",
    )

    r = requests.post(f"{BASE}/auth/signup", json={
        "email": "ptest1@example.com", "password": "testpass123",
        "name": "Phone Tester", "shop_name": "PT Shop", "phone": "abcxyz",
    })
    body = r.text.lower()
    rec(
        r.status_code in (400, 422) and ("mobile" in body or "phone" in body),
        "1b bad phone abcxyz -> 400/422 mentions mobile/phone",
        f"got {r.status_code} body={r.text[:200]}",
    )

    r = requests.post(f"{BASE}/auth/signup", json={
        "email": "ptest1@example.com", "password": "testpass123",
        "name": "Phone Tester", "shop_name": "PT Shop", "phone": "9876543210",
    })
    rec(r.status_code == 200, "1c valid phone signup -> 200",
        f"got {r.status_code} body={r.text[:200]}")
    if r.status_code == 200:
        b = r.json()
        rec(bool(b.get("token")), "1c response has token")
        rec(bool(b.get("id")), "1c response has id")
        did = b.get("display_id", "")
        rec(bool(re.fullmatch(r"USR-\d{5}", did)),
            "1c display_id matches USR-XXXXX",
            f"got display_id={did!r}")
        rec(b.get("phone") == "9876543210",
            "1c phone == '9876543210'",
            f"got phone={b.get('phone')!r}")

    r = requests.post(f"{BASE}/auth/signup", json={
        "email": "ptest1@example.com", "password": "testpass123",
        "name": "Phone Tester", "shop_name": "PT Shop", "phone": "9876543210",
    })
    rec(
        r.status_code == 400 and "already" in r.text.lower(),
        "1d duplicate email -> 400 'already registered'",
        f"got {r.status_code} body={r.text[:200]}",
    )

    r = requests.post(f"{BASE}/auth/signup", json={
        "email": "ptest1-2@example.com", "password": "testpass123",
        "name": "Phone Tester 2", "shop_name": "PT Shop 2",
        "phone": "+91 9876543211",
    })
    rec(r.status_code == 200, "1e +91 9876543211 -> 200",
        f"got {r.status_code} body={r.text[:200]}")
    if r.status_code == 200:
        rec(r.json().get("phone") == "9876543211",
            "1e phone normalised to last-10 digits ('9876543211')",
            f"got phone={r.json().get('phone')!r}")

    cleanup_users(["ptest1@example.com", "ptest1-2@example.com"])
    print("  CLEANUP - ptest1, ptest1-2 removed")


def test2_auth_me():
    print("\n=== TEST 2: /api/auth/me returns display_id + phone ===")
    token = admin_login()
    r = requests.get(f"{BASE}/auth/me",
                     headers={"Authorization": f"Bearer {token}"})
    rec(r.status_code == 200, "2 admin /auth/me -> 200",
        f"got {r.status_code} body={r.text[:200]}")
    if r.status_code == 200:
        b = r.json()
        did = b.get("display_id", "")
        rec(isinstance(did, str) and bool(did) and did.startswith("USR-"),
            "2 admin display_id non-empty 'USR-XXXXX'",
            f"got display_id={did!r}")
        rec("phone" in b and isinstance(b.get("phone", ""), str),
            "2 admin response includes phone (string, may be '')",
            f"got phone={b.get('phone')!r}")


def test3_forgot_password():
    print("\n=== TEST 3: /api/auth/forgot-password ===")
    cleanup_users(["fptest@example.com"])

    r = requests.post(f"{BASE}/auth/signup", json={
        "email": "fptest@example.com", "password": "oldpass1",
        "name": "FP Tester", "shop_name": "FP Shop", "phone": "9999988888",
    })
    if r.status_code != 200:
        rec(False, "3 setup signup",
            f"got {r.status_code} body={r.text[:200]}")
        return
    rec(True, "3 setup signup -> 200")

    r = requests.post(f"{BASE}/auth/forgot-password", json={
        "email": "fptest@example.com", "phone": "wrong",
        "new_password": "newpass123",
    })
    rec(r.status_code in (400, 422),
        "3a phone too short -> 400/422 (no crash)",
        f"got {r.status_code} body={r.text[:200]}")

    r = requests.post(f"{BASE}/auth/forgot-password", json={
        "email": "fptest@example.com", "phone": "9111111111",
        "new_password": "newpass123",
    })
    bl = r.text.lower()
    rec(
        r.status_code == 400
        and ("don't match" in bl or "do not match" in bl
             or "double-check" in bl or "double check" in bl
             or "match" in bl),
        "3b wrong phone -> 400 mentions match/double-check",
        f"got {r.status_code} body={r.text[:200]}",
    )

    r = requests.post(f"{BASE}/auth/forgot-password", json={
        "email": "fptest@example.com", "phone": "9999988888",
        "new_password": "newpass123",
    })
    rec(r.status_code == 200, "3c correct phone reset -> 200",
        f"got {r.status_code} body={r.text[:200]}")
    if r.status_code == 200:
        b = r.json()
        rec(bool(b.get("token")), "3c response has token")
        did = b.get("display_id", "")
        rec(isinstance(did, str) and did.startswith("USR-"),
            "3c response has display_id",
            f"got {did!r}")
        rl = requests.post(f"{BASE}/auth/login", json={
            "email": "fptest@example.com", "password": "newpass123",
        })
        rec(rl.status_code == 200, "3c login with new pass -> 200",
            f"got {rl.status_code} body={rl.text[:200]}")

    rl = requests.post(f"{BASE}/auth/login", json={
        "email": "fptest@example.com", "password": "oldpass1",
    })
    rec(rl.status_code in (400, 401), "3d login with OLD pass -> 401/400",
        f"got {rl.status_code} body={rl.text[:200]}")

    # Rate limiting: clear failure history first so we have a known starting state
    cli = MongoClient(MONGO_URL)
    cli[DB_NAME].pwd_reset_attempts.delete_many({"email": "fptest@example.com"})
    cli.close()

    for i in range(3):
        requests.post(f"{BASE}/auth/forgot-password", json={
            "email": "fptest@example.com", "phone": "9111111111",
            "new_password": "newpass123",
        })
    r4 = requests.post(f"{BASE}/auth/forgot-password", json={
        "email": "fptest@example.com", "phone": "9111111111",
        "new_password": "newpass123",
    })
    body_l = r4.text.lower()
    rec(
        r4.status_code == 429 and "too many" in body_l,
        "3e 4th bad attempt -> 429 'Too many'",
        f"got {r4.status_code} body={r4.text[:200]}",
    )

    cleanup_users(["fptest@example.com"])
    print("  CLEANUP - fptest removed")


def test4_admin_reset():
    print("\n=== TEST 4: Admin password reset ===")
    cleanup_users(["admr@example.com"])

    r = requests.post(f"{BASE}/auth/signup", json={
        "email": "admr@example.com", "password": "abc123",
        "name": "Admin Reset Tester", "shop_name": "AR Shop",
        "phone": "9000000001",
    })
    if r.status_code != 200:
        rec(False, "4a setup signup",
            f"got {r.status_code} body={r.text[:200]}")
        return
    new_uid = r.json()["id"]
    rec(True, "4a setup signup -> 200")

    admin_token = admin_login()

    r = requests.post(
        f"{BASE}/admin/users/{new_uid}/reset-password",
        json={"new_password": "resetme99"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    rec(r.status_code == 200, "4b admin reset -> 200",
        f"got {r.status_code} body={r.text[:200]}")
    if r.status_code == 200:
        b = r.json()
        rec(b.get("ok") is True, "4b ok=true",
            f"got ok={b.get('ok')!r}")
        rec(bool(b.get("display_id")), "4b response has display_id",
            f"got display_id={b.get('display_id')!r}")
        rec(b.get("email") == "admr@example.com",
            "4b response has email",
            f"got email={b.get('email')!r}")

    rl = requests.post(f"{BASE}/auth/login", json={
        "email": "admr@example.com", "password": "resetme99",
    })
    rec(rl.status_code == 200, "4c login with new pass -> 200",
        f"got {rl.status_code} body={rl.text[:200]}")
    rl_old = requests.post(f"{BASE}/auth/login", json={
        "email": "admr@example.com", "password": "abc123",
    })
    rec(rl_old.status_code in (400, 401),
        "4c login with old pass -> 401/400",
        f"got {rl_old.status_code} body={rl_old.text[:200]}")

    r = requests.post(
        f"{BASE}/admin/users/INVALID_ID/reset-password",
        json={"new_password": "doesntmatter"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    rec(r.status_code == 404 and "not found" in r.text.lower(),
        "4d invalid id -> 404 'User not found'",
        f"got {r.status_code} body={r.text[:200]}")

    r = requests.post(
        f"{BASE}/admin/users/{new_uid}/reset-password",
        json={"new_password": "doesntmatter"},
    )
    rec(r.status_code in (401, 403),
        "4e no auth -> 401/403",
        f"got {r.status_code} body={r.text[:200]}")

    if rl.status_code == 200:
        non_admin_token = rl.json()["token"]
        r = requests.post(
            f"{BASE}/admin/users/{new_uid}/reset-password",
            json={"new_password": "x123456"},
            headers={"Authorization": f"Bearer {non_admin_token}"},
        )
        rec(r.status_code == 403,
            "4e non-admin token -> 403",
            f"got {r.status_code} body={r.text[:200]}")

    cleanup_users(["admr@example.com"])
    print("  CLEANUP - admr removed")


def test5_admin_users_list():
    print("\n=== TEST 5: GET /api/admin/users includes display_id + phone ===")
    token = admin_login()
    r = requests.get(f"{BASE}/admin/users",
                     headers={"Authorization": f"Bearer {token}"})
    rec(r.status_code == 200, "5 GET /admin/users -> 200",
        f"got {r.status_code} body={r.text[:200]}")
    if r.status_code != 200:
        return
    users = r.json().get("users", [])
    rec(len(users) > 0, "5 at least 1 user in response")
    if users:
        first = users[0]
        did = first.get("display_id", "")
        rec(isinstance(did, str) and did.startswith("USR-"),
            "5 first row has display_id 'USR-XXXXX'",
            f"got display_id={did!r}")
        rec("phone" in first and isinstance(first.get("phone", ""), str),
            "5 first row has phone (string, may be '')",
            f"got phone={first.get('phone')!r}")


def main():
    try:
        test1_signup_phone()
        test2_auth_me()
        test3_forgot_password()
        test4_admin_reset()
        test5_admin_users_list()
    finally:
        cleanup_users([
            "ptest1@example.com", "ptest1-2@example.com",
            "fptest@example.com", "admr@example.com",
        ])

    print("\n" + "=" * 60)
    print(f"PASS: {len(PASS)}    FAIL: {len(FAIL)}")
    if FAIL:
        print("\nFAILED:")
        for n, d in FAIL:
            print(f"  - {n}\n      {d}")
        sys.exit(1)


if __name__ == "__main__":
    main()
