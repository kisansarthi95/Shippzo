"""
Auth regression test — verifies bcrypt 4.x → passlib compatibility shim
in /app/backend/auth.py does not break:

  1. POST /api/auth/signup   (token + display_id USR-##### + phone)
  2. POST /api/auth/login    (correct password)
  3. POST /api/auth/login    (wrong password → 401)
  4. GET  /api/auth/me       (display_id + phone)
  5. POST /api/auth/forgot-password  (token, then login old/new pwds)

Cleanup: removes the test user + its wallet/seed artifacts.
"""
from __future__ import annotations

import os
import re
import sys
import time
import json
import uuid
import asyncio
from pathlib import Path

import requests


# ----- locate backend URL ---------------------------------------------------

def _read_backend_url() -> str:
    env_path = Path("/app/frontend/.env")
    text = env_path.read_text()
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("EXPO_PUBLIC_BACKEND_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("EXPO_PUBLIC_BACKEND_URL not found in /app/frontend/.env")


BASE = _read_backend_url().rstrip("/") + "/api"
print(f"[INFO] Testing against: {BASE}")


# ----- assertion helpers ---------------------------------------------------

PASS = 0
FAIL = 0
FAIL_DETAILS: list[str] = []


def check(label: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {label}")
    else:
        FAIL += 1
        FAIL_DETAILS.append(f"{label}: {detail}")
        print(f"  ✗ {label}  {detail}")


# ----- test data -----------------------------------------------------------

TS = int(time.time())
EMAIL = f"bcrypt_shim_test_{TS}@example.com"
PASSWORD = "OldP@ssw0rd123"
NEW_PASSWORD = "FreshP@ss456!"
NAME = "Bcrypt Shim Tester"
SHOP = "Shim QA Shop"
PHONE = "9876512340"   # 10 digits


def main():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    user_id = None
    token = None

    # ---- 1) SIGNUP ---------------------------------------------------------
    print("\n=== 1) POST /api/auth/signup ===")
    r = s.post(f"{BASE}/auth/signup", json={
        "email": EMAIL,
        "password": PASSWORD,
        "name": NAME,
        "shop_name": SHOP,
        "phone": PHONE,
    }, timeout=60)
    print(f"  HTTP {r.status_code}")
    check("signup HTTP 200", r.status_code == 200, f"body={r.text[:300]}")
    if r.status_code == 200:
        body = r.json()
        token = body.get("token")
        user_id = body.get("id")
        display_id = body.get("display_id", "")
        check("signup returns non-empty token", bool(token), f"got={token!r}")
        check("signup returns user id (uuid)", bool(user_id))
        check(
            "signup display_id matches USR-#####",
            bool(re.fullmatch(r"USR-\d{5}", display_id or "")),
            f"display_id={display_id!r}",
        )
        check("signup returns phone field", "phone" in body, f"keys={list(body.keys())}")
        check(
            "signup phone is the 10-digit value submitted",
            body.get("phone") == PHONE,
            f"phone={body.get('phone')!r}",
        )
        check("signup does not leak password_hash", "password_hash" not in body)
        check("signup email matches", body.get("email") == EMAIL)

    if not token:
        print("\n[FATAL] No token after signup — aborting downstream auth tests.")
        return _finish(s, user_id)

    # ---- 2) LOGIN OK -------------------------------------------------------
    print("\n=== 2) POST /api/auth/login (correct password) ===")
    r = s.post(f"{BASE}/auth/login", json={
        "email": EMAIL, "password": PASSWORD,
    }, timeout=30)
    print(f"  HTTP {r.status_code}")
    check("login (correct) HTTP 200", r.status_code == 200, f"body={r.text[:300]}")
    if r.status_code == 200:
        body = r.json()
        check("login returns non-empty token", bool(body.get("token")))
        check("login email matches", body.get("email") == EMAIL)
        # Refresh the session token for safety
        token = body.get("token") or token

    # ---- 3) LOGIN WRONG PASSWORD → 401 ------------------------------------
    print("\n=== 3) POST /api/auth/login (wrong password → 401) ===")
    r = s.post(f"{BASE}/auth/login", json={
        "email": EMAIL, "password": "ThisIsNotMyPassword!!"
    }, timeout=30)
    print(f"  HTTP {r.status_code}")
    check("login (wrong) HTTP 401", r.status_code == 401, f"body={r.text[:200]}")
    if r.status_code == 401:
        try:
            detail = r.json().get("detail", "")
        except Exception:
            detail = ""
        check(
            "wrong-pwd detail == 'Invalid email or password'",
            detail == "Invalid email or password",
            f"detail={detail!r}",
        )

    # ---- 4) GET /auth/me ---------------------------------------------------
    print("\n=== 4) GET /api/auth/me ===")
    r = s.get(f"{BASE}/auth/me", headers={"Authorization": f"Bearer {token}"}, timeout=30)
    print(f"  HTTP {r.status_code}")
    check("me HTTP 200", r.status_code == 200, f"body={r.text[:300]}")
    if r.status_code == 200:
        body = r.json()
        check("me has display_id field", "display_id" in body)
        check(
            "me display_id matches USR-#####",
            bool(re.fullmatch(r"USR-\d{5}", body.get("display_id") or "")),
            f"display_id={body.get('display_id')!r}",
        )
        check("me has phone field", "phone" in body)
        check("me phone matches signup phone", body.get("phone") == PHONE,
              f"phone={body.get('phone')!r}")
        check("me email matches", body.get("email") == EMAIL)
        check("me does not leak password_hash", "password_hash" not in body)

    # ---- 5) FORGOT-PASSWORD -----------------------------------------------
    print("\n=== 5) POST /api/auth/forgot-password ===")
    r = s.post(f"{BASE}/auth/forgot-password", json={
        "email": EMAIL,
        "phone": PHONE,
        "new_password": NEW_PASSWORD,
    }, timeout=30)
    print(f"  HTTP {r.status_code}")
    check("forgot-password HTTP 200", r.status_code == 200, f"body={r.text[:300]}")
    if r.status_code == 200:
        body = r.json()
        check("forgot-password returns fresh token", bool(body.get("token")))
        check("forgot-password email matches", body.get("email") == EMAIL)
        token = body.get("token") or token

    # 5b) login with NEW password → 200
    print("\n=== 5b) Login with NEW password (should be 200) ===")
    r = s.post(f"{BASE}/auth/login", json={
        "email": EMAIL, "password": NEW_PASSWORD
    }, timeout=30)
    print(f"  HTTP {r.status_code}")
    check("login w/ NEW password HTTP 200", r.status_code == 200, f"body={r.text[:200]}")

    # 5c) login with OLD password → 401
    print("\n=== 5c) Login with OLD password (should be 401) ===")
    r = s.post(f"{BASE}/auth/login", json={
        "email": EMAIL, "password": PASSWORD
    }, timeout=30)
    print(f"  HTTP {r.status_code}")
    check("login w/ OLD password HTTP 401", r.status_code == 401, f"body={r.text[:200]}")

    _finish(s, user_id)


# ----- cleanup -------------------------------------------------------------

def _finish(_s: requests.Session, user_id: str | None):
    print("\n=== Cleanup ===")
    try:
        # Use motor directly because the test user shouldn't linger.
        from motor.motor_asyncio import AsyncIOMotorClient
        from dotenv import load_dotenv
        load_dotenv("/app/backend/.env")
        mongo = os.environ.get("MONGO_URL")
        db_name = os.environ.get("DB_NAME", "test_database")
        if not mongo:
            print("  [WARN] MONGO_URL not set; skipping DB cleanup")
        else:
            async def _clean():
                client = AsyncIOMotorClient(mongo)
                db = client[db_name]
                # Remove user + any associated isolated data
                u = await db.users.find_one({"email": EMAIL})
                if not u:
                    print(f"  [WARN] no user found for {EMAIL}; nothing to clean")
                    return
                uid = u.get("id")
                r1 = await db.users.delete_many({"email": EMAIL})
                r2 = await db.shipments.delete_many({"user_id": uid})
                r3 = await db.couriers.delete_many({"user_id": uid})
                r4 = await db.settings.delete_many({"user_id": uid})
                r5 = await db.pending_orders.delete_many({"user_id": uid})
                r6 = await db.wallet_transactions.delete_many({"user_id": uid})
                r7 = await db.wallets.delete_many({"user_id": uid})
                r8 = await db.pwd_reset_attempts.delete_many({"email": EMAIL})
                client.close()
                print(
                    f"  Cleaned: users={r1.deleted_count} "
                    f"shipments={r2.deleted_count} couriers={r3.deleted_count} "
                    f"settings={r4.deleted_count} pending={r5.deleted_count} "
                    f"wallet_tx={r6.deleted_count} wallets={r7.deleted_count} "
                    f"pwd_attempts={r8.deleted_count}"
                )
            asyncio.run(_clean())
    except Exception as e:
        print(f"  [WARN] cleanup failed: {e!r}")

    # ---- Final summary -----------------------------------------------------
    print("\n" + "=" * 60)
    print(f"RESULT: {PASS} passed, {FAIL} failed")
    if FAIL:
        print("Failures:")
        for d in FAIL_DETAILS:
            print(f"  - {d}")
    print("=" * 60)
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
