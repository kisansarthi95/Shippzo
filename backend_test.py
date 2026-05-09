"""Phase G2 (rev-2) — `needs_profile_completion` flag tests.

Covers 5 cases:
  1. Legacy user with no flag → /auth/context returns false even when
     shop_name/phone/category are empty.
  2. Email/password fresh signup → /auth/context returns false; mongo
     doc has no flag (or it's explicitly False).
  3. /auth/complete-profile flips the flag from True → False, both in
     /auth/context response and on the mongo doc.
  4. Sanity regressions: empty-shop_name signup → 422; business-categories
     returns 16; login + me work.
  5. Cleanup all pytest_phaseg2_rev2_* test users + seeded data.
"""
from __future__ import annotations

import os
import sys
import time
import uuid
import json
from typing import Any, Dict, List, Optional, Tuple

import requests
from pymongo import MongoClient

# ---- Config ---------------------------------------------------------

FRONTEND_ENV = "/app/frontend/.env"
BACKEND_ENV  = "/app/backend/.env"


def _read_env_file(path: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not os.path.exists(path):
        return out
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


_fe = _read_env_file(FRONTEND_ENV)
_be = _read_env_file(BACKEND_ENV)

BASE_URL = (
    _fe.get("EXPO_PUBLIC_BACKEND_URL")
    or _fe.get("REACT_APP_BACKEND_URL")
    or "http://localhost:8001"
).rstrip("/") + "/api"

MONGO_URL = _be.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME   = _be.get("DB_NAME",   "test_database")

mongo = MongoClient(MONGO_URL)
db    = mongo[DB_NAME]

ADMIN_EMAIL    = "admin@test.com"
ADMIN_PASSWORD = "Admin@12345"

# Marker for cleanup
TEST_PREFIX = "pytest_phaseg2_rev2_"

results: List[Tuple[str, bool, str]] = []


def record(name: str, ok: bool, msg: str = "") -> bool:
    results.append((name, ok, msg))
    print(f"{'PASS' if ok else 'FAIL'}  {name} {('— ' + msg) if msg else ''}")
    return ok


def http(method: str, path: str, *, token: Optional[str] = None, json_body: Any = None,
         expected_status: Optional[int] = None) -> Tuple[int, Any]:
    url = f"{BASE_URL}{path}"
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = requests.request(method, url, headers=headers, json=json_body, timeout=30)
    try:
        body = r.json()
    except Exception:
        body = r.text
    if expected_status is not None and r.status_code != expected_status:
        print(f"  [http] {method} {path} → {r.status_code} (expected {expected_status})")
        print(f"  body: {json.dumps(body)[:600]}")
    return r.status_code, body


# =====================================================================
# CASE 1 — Legacy user — no flag, no gate.
# =====================================================================

def case_1_legacy_user_no_flag() -> None:
    print("\n=== CASE 1: Legacy user (no flag, no gate) ===")

    # Step 1a: Login as admin@test.com (a pre-Phase G2 account).
    sc, body = http("POST", "/auth/login",
                    json_body={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    if not record("CASE1.login admin@test.com → 200",
                  sc == 200 and isinstance(body, dict) and body.get("token"),
                  f"status={sc}"):
        return
    token = body["token"]
    uid   = body.get("id")

    # Force the cleanest legacy state on the admin doc — unset needs_profile_completion
    # and blank shop_name/phone/category so we know the flag is the ONLY thing
    # gating /auth/context.
    db.users.update_one({"id": uid}, {"$unset": {"needs_profile_completion": ""}})

    # Snapshot original values so we can restore at the end of the case.
    orig = db.users.find_one({"id": uid}) or {}
    orig_shop  = orig.get("shop_name", "")
    orig_phone = orig.get("phone", "")
    orig_cat   = orig.get("primary_business_category", "")

    # Force the empty-fields edge case explicitly.
    db.users.update_one({"id": uid}, {"$set": {
        "shop_name": "",
        "phone": "",
        "primary_business_category": "",
    }, "$unset": {"needs_profile_completion": ""}})

    sc, ctx = http("GET", "/auth/context", token=token)
    record("CASE1.context status 200", sc == 200, f"status={sc}")
    record(
        "CASE1.needs_profile_completion is False even with empty shop/phone/category",
        isinstance(ctx, dict) and ctx.get("needs_profile_completion") is False,
        f"needs_profile_completion={ctx.get('needs_profile_completion')!r}",
    )

    # Verify the doc actually has no `needs_profile_completion` key.
    refreshed = db.users.find_one({"id": uid}) or {}
    record(
        "CASE1.user doc has no needs_profile_completion key",
        "needs_profile_completion" not in refreshed,
        f"keys present: needs_profile_completion={refreshed.get('needs_profile_completion')!r}",
    )

    # Restore original profile values so we don't break other tests.
    db.users.update_one({"id": uid}, {"$set": {
        "shop_name": orig_shop,
        "phone": orig_phone,
        "primary_business_category": orig_cat,
    }})


# =====================================================================
# CASE 2 — Email/password fresh signup — no gate, no flag.
# =====================================================================

case2_state: Dict[str, Any] = {}


def case_2_signup_no_flag() -> None:
    print("\n=== CASE 2: Email/password fresh signup ===")

    suffix = uuid.uuid4().hex[:10]
    email  = f"{TEST_PREFIX}signup_{suffix}@example.com"
    password = "TestPass#2026"
    payload = {
        "email": email,
        "password": password,
        "name": "Riya Patel",
        "shop_name": "Riya's Boutique",
        "phone": "9123456789",
        "primary_business_category": "fashion_apparel",
    }
    sc, body = http("POST", "/auth/signup", json_body=payload)
    if not record("CASE2.signup → 200",
                  sc == 200 and isinstance(body, dict) and body.get("token"),
                  f"status={sc}"):
        return
    token = body["token"]
    uid   = body.get("id")
    case2_state["email"]    = email
    case2_state["password"] = password
    case2_state["uid"]      = uid
    case2_state["token"]    = token

    sc, ctx = http("GET", "/auth/context", token=token)
    record("CASE2.context status 200", sc == 200, f"status={sc}")
    record(
        "CASE2.needs_profile_completion is False after fresh email/password signup",
        isinstance(ctx, dict) and ctx.get("needs_profile_completion") is False,
        f"needs_profile_completion={ctx.get('needs_profile_completion')!r}",
    )

    # On the mongo doc, the field must either be missing or explicitly False.
    doc = db.users.find_one({"id": uid}) or {}
    flag_val = doc.get("needs_profile_completion", "<missing>")
    record(
        "CASE2.mongo doc has no flag OR explicit False",
        flag_val == "<missing>" or flag_val is False,
        f"value={flag_val!r}",
    )


# =====================================================================
# CASE 3 — complete-profile clears the flag.
# =====================================================================

case3_state: Dict[str, Any] = {}


def case_3_complete_profile_flips_flag() -> None:
    print("\n=== CASE 3: /auth/complete-profile clears the flag ===")

    # Use a brand new dedicated user via direct signup, then set the flag in mongo
    # to simulate a Google-OAuth fresh-create branch.
    suffix = uuid.uuid4().hex[:10]
    email  = f"{TEST_PREFIX}gate_{suffix}@example.com"
    password = "Gated#Test2026"
    sc, body = http("POST", "/auth/signup", json_body={
        "email": email,
        "password": password,
        "name": "Aarav Mehta",
        "shop_name": "Aarav's Store",
        "phone": "9876512340",
        "primary_business_category": "books_stationery",
    })
    if not record("CASE3.bootstrap signup → 200",
                  sc == 200 and isinstance(body, dict) and body.get("token"),
                  f"status={sc}"):
        return
    token = body["token"]
    uid   = body["id"]
    case3_state["email"]    = email
    case3_state["password"] = password
    case3_state["uid"]      = uid

    # Force the gate ON to mimic a fresh Google user.
    db.users.update_one({"id": uid}, {"$set": {"needs_profile_completion": True}})

    sc, ctx = http("GET", "/auth/context", token=token)
    record("CASE3.context after flag forced True → 200", sc == 200, f"status={sc}")
    record(
        "CASE3.needs_profile_completion is True after manual flag set",
        isinstance(ctx, dict) and ctx.get("needs_profile_completion") is True,
        f"needs_profile_completion={ctx.get('needs_profile_completion')!r}",
    )

    # Submit complete-profile.
    sc, cp = http("POST", "/auth/complete-profile", token=token, json_body={
        "shop_name": "Aarav Books",
        "phone": "9876512340",
        "primary_business_category": "books_stationery",
    })
    record("CASE3.complete-profile → 200",
           sc == 200 and isinstance(cp, dict) and cp.get("ok") is True,
           f"status={sc} body={str(cp)[:200]}")

    # Re-check context.
    sc, ctx2 = http("GET", "/auth/context", token=token)
    record("CASE3.context after complete-profile → 200", sc == 200, f"status={sc}")
    record(
        "CASE3.needs_profile_completion is False after complete-profile",
        isinstance(ctx2, dict) and ctx2.get("needs_profile_completion") is False,
        f"needs_profile_completion={ctx2.get('needs_profile_completion')!r}",
    )

    # Mongo: flag must be literal False (not missing, not True).
    doc = db.users.find_one({"id": uid}) or {}
    record(
        "CASE3.mongo flag is literal False (present, not missing)",
        doc.get("needs_profile_completion") is False
        and "needs_profile_completion" in doc,
        f"value={doc.get('needs_profile_completion')!r}",
    )


# =====================================================================
# CASE 4 — Sanity regressions
# =====================================================================

def case_4_sanity_regressions() -> None:
    print("\n=== CASE 4: Sanity regressions ===")

    # 4a. POST /auth/signup with empty shop_name → 422.
    suffix = uuid.uuid4().hex[:10]
    email_x = f"{TEST_PREFIX}regr_{suffix}@example.com"
    sc, body = http("POST", "/auth/signup", json_body={
        "email": email_x,
        "password": "Whatever#123",
        "name": "Test Regression",
        "shop_name": "",
        "phone": "9012345678",
        "primary_business_category": "fashion_apparel",
    })
    record("CASE4.signup empty shop_name → 422", sc == 422, f"status={sc}")
    # If for any reason it created the user, still mark for cleanup
    # (TEST_PREFIX scoping handles that).

    # 4b. business-categories returns 16 entries.
    sc, body = http("GET", "/auth/business-categories")
    cats = (body or {}).get("categories") if isinstance(body, dict) else None
    record("CASE4.business-categories → 200", sc == 200, f"status={sc}")
    record(
        "CASE4.business-categories has 16 entries",
        isinstance(cats, list) and len(cats) == 16,
        f"len={(len(cats) if isinstance(cats, list) else 'N/A')}",
    )

    # 4c. login with case-2 credentials → 200.
    if case2_state.get("email"):
        sc, body = http("POST", "/auth/login", json_body={
            "email": case2_state["email"],
            "password": case2_state["password"],
        })
        ok = sc == 200 and isinstance(body, dict) and body.get("token")
        record("CASE4.login case-2 user → 200", bool(ok), f"status={sc}")

        # 4d. /auth/me → 200.
        if ok:
            sc2, me = http("GET", "/auth/me", token=body["token"])
            record(
                "CASE4./auth/me → 200",
                sc2 == 200 and isinstance(me, dict) and me.get("email") == case2_state["email"],
                f"status={sc2}",
            )


# =====================================================================
# CASE 5 — Cleanup
# =====================================================================

def case_5_cleanup() -> None:
    print("\n=== CASE 5: Cleanup ===")

    # Find all pytest_phaseg2_rev2_* users
    test_users = list(db.users.find({"email": {"$regex": f"^{TEST_PREFIX}"}}))
    user_ids = [u["id"] for u in test_users]
    print(f"  found {len(user_ids)} test users to clean")

    if user_ids:
        # Delete seeded data
        s_del = db.shipments.delete_many({"user_id": {"$in": user_ids}})
        c_del = db.couriers.delete_many({"user_id": {"$in": user_ids}})
        w_del = db.wallets.delete_many({"user_id": {"$in": user_ids}})
        wt_del = db.wallet_transactions.delete_many({"user_id": {"$in": user_ids}})
        st_del = db.settings.delete_many({"user_id": {"$in": user_ids}})
        po_del = db.pending_orders.delete_many({"user_id": {"$in": user_ids}})
        u_del = db.users.delete_many({"id": {"$in": user_ids}})

        print(f"  shipments={s_del.deleted_count} couriers={c_del.deleted_count} "
              f"wallets={w_del.deleted_count} wallet_tx={wt_del.deleted_count} "
              f"settings={st_del.deleted_count} pending={po_del.deleted_count} "
              f"users={u_del.deleted_count}")

    # Sanity: post-cleanup, no test users remain
    remaining = db.users.count_documents({"email": {"$regex": f"^{TEST_PREFIX}"}})
    record("CASE5.no pytest_phaseg2_rev2_* users remain", remaining == 0,
           f"remaining={remaining}")


def main() -> int:
    print(f"Backend URL: {BASE_URL}")
    print(f"Mongo:       {MONGO_URL}  /  DB: {DB_NAME}")

    try:
        case_1_legacy_user_no_flag()
        case_2_signup_no_flag()
        case_3_complete_profile_flips_flag()
        case_4_sanity_regressions()
    finally:
        case_5_cleanup()

    passed = sum(1 for _, ok, _ in results if ok)
    total  = len(results)
    print("\n" + "=" * 70)
    print(f"RESULT: {passed}/{total} assertions passed")
    print("=" * 70)
    if passed != total:
        for name, ok, msg in results:
            if not ok:
                print(f"  FAIL  {name} — {msg}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
