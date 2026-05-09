"""
Phase G — Business Category Signup Integration Backend Tests.

Tests the 5 cases from the review request:
  1. GET /api/auth/business-categories — public, returns 16 categories
  2. POST /api/auth/signup happy path with valid category
  3. POST /api/auth/signup with invalid category → 400
  4. POST /api/auth/signup with empty category (backward compat) → 200
  5. Regression sanity: webhook + login + me + bad-token 401

Cleans up any pytest_phaseg_* users + their related rows at the end.
"""
from __future__ import annotations

import sys
import time
import asyncio
import requests
from pathlib import Path

# --- Locate frontend/.env to grab the public backend URL ----------------
FRONTEND_ENV = Path("/app/frontend/.env")
PUBLIC_BACKEND_URL = None
for line in FRONTEND_ENV.read_text().splitlines():
    if line.strip().startswith("EXPO_PUBLIC_BACKEND_URL="):
        PUBLIC_BACKEND_URL = line.split("=", 1)[1].strip().strip('"').strip("'")
        break
    if line.strip().startswith("REACT_APP_BACKEND_URL="):
        PUBLIC_BACKEND_URL = line.split("=", 1)[1].strip().strip('"').strip("'")
        break

if not PUBLIC_BACKEND_URL:
    print("FATAL: no EXPO_PUBLIC_BACKEND_URL or REACT_APP_BACKEND_URL in /app/frontend/.env")
    sys.exit(1)

API = PUBLIC_BACKEND_URL.rstrip("/") + "/api"
print(f"Testing against: {API}\n")

# --- Mongo connection (for direct DB verification + cleanup) -----------
MONGO_URL = None
DB_NAME = None
for line in Path("/app/backend/.env").read_text().splitlines():
    if line.strip().startswith("MONGO_URL="):
        MONGO_URL = line.split("=", 1)[1].strip().strip('"').strip("'")
    elif line.strip().startswith("DB_NAME="):
        DB_NAME = line.split("=", 1)[1].strip().strip('"').strip("'")

assert MONGO_URL, "MONGO_URL missing from backend/.env"
assert DB_NAME, "DB_NAME missing from backend/.env"

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

mongo_client = AsyncIOMotorClient(MONGO_URL)
db = mongo_client[DB_NAME]

results = []  # list[(case, ok, msg)]


def record(case: str, ok: bool, msg: str = ""):
    results.append((case, ok, msg))
    flag = "PASS" if ok else "FAIL"
    print(f"[{flag}] {case}: {msg}")


def run_async(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("loop closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


# ----- CASE 1: GET /api/auth/business-categories -------------------------

def case1_business_categories():
    print("\n=== CASE 1: GET /api/auth/business-categories ===")
    try:
        # No auth header at all — must be publicly reachable.
        r = requests.get(f"{API}/auth/business-categories", timeout=15)
        record("CASE1 status 200 (no auth header)", r.status_code == 200, f"got {r.status_code}")
        if r.status_code != 200:
            print("body:", r.text[:500])
            return
        body = r.json()
        record(
            "CASE1 response shape has 'categories' key",
            isinstance(body, dict) and "categories" in body,
            f"keys={list(body.keys())[:5]}",
        )
        cats = body.get("categories", [])
        record("CASE1 categories is list", isinstance(cats, list), f"type={type(cats).__name__}")
        record("CASE1 exactly 16 entries", len(cats) == 16, f"got {len(cats)}")
        bad = [c for c in cats if not (
            isinstance(c, dict) and "slug" in c and "label" in c and "icon" in c
        )]
        record("CASE1 every entry has slug+label+icon", not bad, f"missing keys in {len(bad)}")
        slugs = {c.get("slug") for c in cats if isinstance(c, dict)}
        record(
            "CASE1 'fashion_apparel' slug present",
            "fashion_apparel" in slugs,
            f"first 3 slugs={sorted(slugs)[:3]}",
        )
    except Exception as e:
        record("CASE1 unexpected exception", False, repr(e))


# ----- CASE 2: signup happy path -----------------------------------------

EMAIL_PREFIX = f"pytest_phaseg_{int(time.time())}"


def case2_signup_valid():
    print("\n=== CASE 2: POST /api/auth/signup VALID category ===")
    email = f"{EMAIL_PREFIX}_valid@test.com"
    body = {
        "email": email,
        "password": "Test@1234",
        "name": "PG Test",
        "shop_name": "PG Shop",
        "phone": "9999900001",
        "primary_business_category": "fashion_apparel",
        "device_fingerprint": "",
    }
    token = None
    try:
        r = requests.post(f"{API}/auth/signup", json=body, timeout=20)
        record("CASE2 signup status 200", r.status_code == 200, f"got {r.status_code}; body={r.text[:300]}")
        if r.status_code != 200:
            return None
        data = r.json()
        token = data.get("token")
        record("CASE2 response has 'token'", bool(token), f"token_len={len(token or '')}")
        record("CASE2 response has user 'id'", bool(data.get("id")), f"id={data.get('id')}")
        record("CASE2 response email matches", data.get("email") == email, f"got {data.get('email')}")
    except Exception as e:
        record("CASE2 signup exception", False, repr(e))
        return None

    # GET /api/auth/me with the token
    try:
        r = requests.get(
            f"{API}/auth/me",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        record("CASE2 GET /auth/me status 200", r.status_code == 200, f"got {r.status_code}")
        me = r.json() if r.status_code == 200 else {}
        if r.status_code == 200:
            has_pbc = "primary_business_category" in me
            record(
                "CASE2 /auth/me surfaces 'primary_business_category'",
                has_pbc,
                (
                    f"value={me.get('primary_business_category')!r}"
                    if has_pbc else
                    "key MISSING from /auth/me — UserPublic model lacks the field. "
                    "Currently only /auth/context.user.primary_business_category surfaces it."
                ),
            )
            if has_pbc:
                record(
                    "CASE2 /auth/me primary_business_category == 'fashion_apparel'",
                    me.get("primary_business_category") == "fashion_apparel",
                    f"value={me.get('primary_business_category')!r}",
                )
    except Exception as e:
        record("CASE2 GET /auth/me exception", False, repr(e))

    # Backup verification via /auth/context (which DOES surface it)
    try:
        r = requests.get(
            f"{API}/auth/context",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        if r.status_code == 200:
            ctx = r.json()
            ctx_pbc = (ctx.get("user") or {}).get("primary_business_category")
            record(
                "CASE2 /auth/context user.primary_business_category == 'fashion_apparel'",
                ctx_pbc == "fashion_apparel",
                f"got {ctx_pbc!r}",
            )
    except Exception as e:
        record("CASE2 /auth/context exception", False, repr(e))

    # Direct Mongo verification
    async def _check_db():
        return await db.users.find_one({"email": email})

    try:
        u = run_async(_check_db())
        record(
            "CASE2 Mongo: users doc exists",
            u is not None,
            f"id={u.get('id') if u else None}",
        )
        if u:
            record(
                "CASE2 Mongo: primary_business_category == 'fashion_apparel'",
                u.get("primary_business_category") == "fashion_apparel",
                f"got {u.get('primary_business_category')!r}",
            )
    except Exception as e:
        record("CASE2 Mongo check exception", False, repr(e))

    return token


# ----- CASE 2b: spot-check a different slug -----------------------------

def case2b_signup_electronics():
    print("\n=== CASE 2b: spot-check signup with 'electronics_gadgets' ===")
    email = f"{EMAIL_PREFIX}_electronics@test.com"
    body = {
        "email": email,
        "password": "Test@1234",
        "name": "PG Test E",
        "shop_name": "PG Electronics Shop",
        "phone": "9999900005",
        "primary_business_category": "electronics_gadgets",
        "device_fingerprint": "",
    }
    try:
        r = requests.post(f"{API}/auth/signup", json=body, timeout=20)
        record(
            "CASE2b signup status 200 with electronics_gadgets",
            r.status_code == 200,
            f"got {r.status_code}; body={r.text[:200]}",
        )
        if r.status_code != 200:
            return
        token = r.json().get("token")

        # /auth/me check
        r = requests.get(
            f"{API}/auth/me",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        if r.status_code == 200:
            me = r.json()
            record(
                "CASE2b /auth/me primary_business_category == 'electronics_gadgets'",
                me.get("primary_business_category") == "electronics_gadgets",
                f"got {me.get('primary_business_category')!r}",
            )

        # Mongo check
        async def _check():
            return await db.users.find_one({"email": email})
        u = run_async(_check())
        if u:
            record(
                "CASE2b Mongo: primary_business_category == 'electronics_gadgets'",
                u.get("primary_business_category") == "electronics_gadgets",
                f"got {u.get('primary_business_category')!r}",
            )
            record(
                "CASE2b Mongo: primary_business_category_at non-empty",
                bool(u.get("primary_business_category_at")),
                f"got {u.get('primary_business_category_at')!r}",
            )
    except Exception as e:
        record("CASE2b exception", False, repr(e))


# ----- CASE 3: invalid category -----------------------------------------

def case3_signup_invalid():
    print("\n=== CASE 3: POST /api/auth/signup INVALID category ===")
    email = f"{EMAIL_PREFIX}_invalid@test.com"
    body = {
        "email": email,
        "password": "Test@1234",
        "name": "PG Test",
        "shop_name": "PG Shop",
        "phone": "9999900002",
        "primary_business_category": "totally_not_real_slug",
        "device_fingerprint": "",
    }
    try:
        r = requests.post(f"{API}/auth/signup", json=body, timeout=20)
        record("CASE3 status 400", r.status_code == 400, f"got {r.status_code}; body={r.text[:200]}")
        if r.status_code == 400:
            try:
                detail = r.json().get("detail", "")
            except Exception:
                detail = r.text
            record(
                "CASE3 detail mentions 'valid business category' (case-insensitive)",
                "valid business category" in str(detail).lower(),
                f"detail={detail!r}",
            )
        # Also confirm the bogus user wasn't actually inserted
        async def _check():
            return await db.users.find_one({"email": email})
        u = run_async(_check())
        record(
            "CASE3 invalid signup did NOT create a user document",
            u is None,
            "user doc found despite 400" if u else "no doc, as expected",
        )
    except Exception as e:
        record("CASE3 exception", False, repr(e))


# ----- CASE 4: empty category, backward-compat --------------------------

def case4_signup_empty():
    print("\n=== CASE 4: POST /api/auth/signup EMPTY category (backward-compat) ===")
    # 4a: empty string
    email_a = f"{EMAIL_PREFIX}_empty@test.com"
    body_a = {
        "email": email_a,
        "password": "Test@1234",
        "name": "PG Test",
        "shop_name": "PG Shop",
        "phone": "9999900003",
        "primary_business_category": "",
        "device_fingerprint": "",
    }
    try:
        r = requests.post(f"{API}/auth/signup", json=body_a, timeout=20)
        record(
            "CASE4a empty-string category → status 200",
            r.status_code == 200,
            f"got {r.status_code}; body={r.text[:300]}",
        )
        if r.status_code == 200:
            d = r.json()
            record("CASE4a response has token", bool(d.get("token")), f"id={d.get('id')}")
    except Exception as e:
        record("CASE4a exception", False, repr(e))

    # 4b: field omitted entirely
    email_b = f"{EMAIL_PREFIX}_omitted@test.com"
    body_b = {
        "email": email_b,
        "password": "Test@1234",
        "name": "PG Test",
        "shop_name": "PG Shop",
        "phone": "9999900004",
        "device_fingerprint": "",
    }
    try:
        r = requests.post(f"{API}/auth/signup", json=body_b, timeout=20)
        record(
            "CASE4b omitted category → status 200",
            r.status_code == 200,
            f"got {r.status_code}; body={r.text[:300]}",
        )
    except Exception as e:
        record("CASE4b exception", False, repr(e))


# ----- CASE 5: regression sanity ----------------------------------------

def case5_regression():
    print("\n=== CASE 5: Regression sanity (login + me + bad token + webhook) ===")
    email = f"{EMAIL_PREFIX}_valid@test.com"

    # 5a — login with the valid-category test user
    tok = None
    try:
        r = requests.post(
            f"{API}/auth/login",
            json={"email": email, "password": "Test@1234"},
            timeout=15,
        )
        record("CASE5a /auth/login status 200", r.status_code == 200, f"got {r.status_code}")
        if r.status_code == 200:
            tok = r.json().get("token")
            record("CASE5a login returns token", bool(tok), "")
    except Exception as e:
        record("CASE5a login exception", False, repr(e))

    # 5b — /auth/me with valid token
    if tok:
        try:
            r = requests.get(
                f"{API}/auth/me",
                headers={"Authorization": f"Bearer {tok}"},
                timeout=15,
            )
            record("CASE5b /auth/me with valid token = 200", r.status_code == 200, f"got {r.status_code}")
        except Exception as e:
            record("CASE5b /auth/me exception", False, repr(e))

    # 5c — /auth/me with bad token = 401
    try:
        r = requests.get(
            f"{API}/auth/me",
            headers={"Authorization": "Bearer this.is.not.a.real.jwt"},
            timeout=15,
        )
        record(
            "CASE5c /auth/me bad token returns 401",
            r.status_code == 401,
            f"got {r.status_code}",
        )
    except Exception as e:
        record("CASE5c bad-token exception", False, repr(e))

    # 5d — webhook reachability spot-check.
    # Review request mentioned /api/webhooks/orders/<secret> (plural) but
    # the actual mount is /api/webhook/orders/<secret> (singular). We try
    # both — at least one must respond with a 4xx (route mounted, secret
    # rejected, no 5xx).
    for path in ("webhook", "webhooks"):
        try:
            r = requests.post(
                f"{API}/{path}/orders/__not_a_real_secret__",
                json={"customer_name": "Test", "customer_phone": "9999900099"},
                timeout=15,
            )
            ok_4xx = 400 <= r.status_code < 500
            record(
                f"CASE5d /{path}/orders/<bad-secret> returns 4xx (mounted, no 5xx)",
                ok_4xx,
                f"got {r.status_code}; body={r.text[:160]}",
            )
        except Exception as e:
            record(f"CASE5d /{path} exception", False, repr(e))


# ----- CLEANUP -----------------------------------------------------------

async def cleanup():
    print("\n=== CLEANUP: removing pytest_phaseg_* test artefacts ===")
    pattern = {"email": {"$regex": r"^pytest_phaseg_"}}
    users = await db.users.find(pattern, {"_id": 0, "id": 1, "email": 1}).to_list(50)
    user_ids = [u["id"] for u in users]
    print(f"Found {len(user_ids)} test users to clean up")
    if user_ids:
        cond = {"user_id": {"$in": user_ids}}
        for coll in (
            "pending_orders", "shipments", "couriers", "settings",
            "wallet_transactions", "wallets", "team_members", "wallet",
        ):
            try:
                res = await db[coll].delete_many(cond)
                if res.deleted_count:
                    print(f"  - {coll}: deleted {res.deleted_count}")
            except Exception as e:
                print(f"  - {coll}: cleanup failed: {e!r}")
    res = await db.users.delete_many(pattern)
    print(f"  - users: deleted {res.deleted_count}")


# ----- RUNNER ------------------------------------------------------------

def main():
    case1_business_categories()
    case2_signup_valid()
    case2b_signup_electronics()
    case3_signup_invalid()
    case4_signup_empty()
    case5_regression()

    try:
        run_async(cleanup())
    except Exception as e:
        print(f"Cleanup failed: {e!r}")

    print("\n" + "=" * 60)
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"RESULTS: {passed}/{total} assertions passed")
    print("=" * 60)
    failures = [(c, m) for c, ok, m in results if not ok]
    if failures:
        print("\nFAILURES:")
        for c, m in failures:
            print(f"  - {c} :: {m}")
        sys.exit(1)
    print("\nALL TESTS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
