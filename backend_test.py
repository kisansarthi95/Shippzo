"""
Phase-1 incremental refactor verification.
Tests that extracting /admin/global-config GET into routers/admin.py
hasn't regressed any auth / admin / sheets / shipments flows.
"""
import os, time, requests, sys

# Use the same backend URL the frontend uses (the public preview URL).
with open("/app/frontend/.env") as f:
    env = dict(l.strip().split("=", 1) for l in f if "=" in l and not l.startswith("#"))
BACKEND = env["EXPO_PUBLIC_BACKEND_URL"].strip().strip('"') + "/api"
print(f"[INFO] Testing against: {BACKEND}\n")

results = []
def t(name, ok, detail=""):
    mark = "✓" if ok else "✗"
    print(f"  {mark} {name}{(' — ' + detail) if detail else ''}")
    results.append((name, ok, detail))

# --- helpers ---------------------------------------------------------
def login(email, pw):
    r = requests.post(f"{BACKEND}/auth/login", json={"email": email, "password": pw}, timeout=20)
    return r

# --- 0. Admin login --------------------------------------------------
print("[0] Admin login")
ar = login("admin@test.com", "Admin@12345")
t("admin login 200", ar.status_code == 200, f"got {ar.status_code}")
admin_tok = ar.json().get("token", "")
H_ADMIN = {"Authorization": f"Bearer {admin_tok}"}

# Regular user login (for non-admin checks)
print("\n[0b] Regular user login")
ur = login("user2@test.com", "User@12345")
t("regular user login 200", ur.status_code == 200, f"got {ur.status_code}")
user_tok = ur.json().get("token", "")
H_USER = {"Authorization": f"Bearer {user_tok}"}

# --- 1. Moved endpoint -----------------------------------------------
print("\n[1] /admin/global-config (extracted to routers/admin.py)")
r = requests.get(f"{BACKEND}/admin/global-config", headers=H_ADMIN, timeout=20)
t("admin GET 200", r.status_code == 200, f"got {r.status_code}")
body = r.json() if r.status_code == 200 else {}
t("has global_ai_rates", "global_ai_rates" in body)
t("has credit_packages", "credit_packages" in body)
t("has plan_pricing", "plan_pricing" in body)

r = requests.get(f"{BACKEND}/admin/global-config", timeout=20)
t("no-token → 401", r.status_code == 401, f"got {r.status_code}")

r = requests.get(f"{BACKEND}/admin/global-config", headers=H_USER, timeout=20)
t("non-admin → 403", r.status_code == 403, f"got {r.status_code}")

# --- 2. Adjacent admin endpoints (still in server.py) ----------------
print("\n[2] Adjacent admin endpoints (must not regress)")
r = requests.get(f"{BACKEND}/admin/users", headers=H_ADMIN, timeout=20)
t("GET /admin/users 200", r.status_code == 200, f"got {r.status_code}")
users_count = len(r.json().get("users", [])) if r.status_code == 200 else 0
t(f"users array populated ({users_count} users)", users_count >= 1)

r = requests.put(f"{BACKEND}/admin/global-config", json={}, headers=H_ADMIN, timeout=20)
t("PUT /admin/global-config 200", r.status_code == 200, f"got {r.status_code}")

r = requests.get(f"{BACKEND}/admin/plan-features", headers=H_ADMIN, timeout=20)
t("GET /admin/plan-features 200", r.status_code == 200, f"got {r.status_code}")
if r.status_code == 200:
    pf = r.json()
    t("plan-features has registry", "registry" in pf)
    t("plan-features has plans", "plans" in pf)

# --- 3. Auth flows ---------------------------------------------------
print("\n[3] Auth flows (bcrypt shim + signup)")
ts = int(time.time())
fp = f"refactor-test-fp-{ts}"
sig = requests.post(f"{BACKEND}/auth/signup", json={
    "email": f"refactor_a_{ts}@example.com", "password": "Pass1234!",
    "name": "Refactor A", "shop_name": "Shop A", "phone": "9988776655",
    "device_fingerprint": fp,
}, timeout=20)
t("signup 200", sig.status_code == 200, f"got {sig.status_code}")
sig_data = sig.json() if sig.status_code == 200 else {}
t("signup has token", bool(sig_data.get("token")))
t("display_id matches USR-####", sig_data.get("display_id", "").startswith("USR-"))
test_uid_a = sig_data.get("id")

lg = login(f"refactor_a_{ts}@example.com", "Pass1234!")
t("login correct 200", lg.status_code == 200, f"got {lg.status_code}")

lg = login(f"refactor_a_{ts}@example.com", "wrong-password")
t("login wrong 401", lg.status_code == 401, f"got {lg.status_code}")

# --- 4. Sheets endpoints ---------------------------------------------
print("\n[4] Sheets endpoints (Phase-5 SA-share)")
r = requests.get(f"{BACKEND}/sheets/service-account", headers=H_ADMIN, timeout=20)
t("GET /sheets/service-account 200", r.status_code == 200, f"got {r.status_code}")
sa = r.json() if r.status_code == 200 else {}
t("SA email returned", "@" in sa.get("email", ""), f"email='{sa.get('email','')[:40]}'")

# Master sheet should be SA-accessible
master_url = "https://docs.google.com/spreadsheets/d/1troW3K7P_uaE_7moo6_CioPczUosSiZyoPmCBBcekxA/edit#gid=0"
r = requests.post(f"{BACKEND}/sheets/preview", json={"url": master_url}, headers=H_ADMIN, timeout=30)
t("POST /sheets/preview master 200", r.status_code == 200, f"got {r.status_code}")
if r.status_code == 200:
    pv = r.json()
    t("access_method=service_account", pv.get("access_method") == "service_account",
      f"got '{pv.get('access_method')}'")
    t(f"total_rows>=1 ({pv.get('total_rows')})", (pv.get("total_rows", 0) or 0) >= 1)

# --- 5. Device fingerprint regression check -------------------------
print("\n[5] Phase-2b device fingerprint anti-abuse")
sig2 = requests.post(f"{BACKEND}/auth/signup", json={
    "email": f"refactor_b_{ts}@example.com", "password": "Pass1234!",
    "name": "Refactor B", "shop_name": "Shop B", "phone": "9988776656",
    "device_fingerprint": fp,  # SAME fingerprint
}, timeout=20)
t("2nd signup same-fp 200", sig2.status_code == 200, f"got {sig2.status_code}")
sig2_data = sig2.json() if sig2.status_code == 200 else {}
t("2nd signup trial_denied=True", sig2_data.get("trial_denied") is True,
  f"got {sig2_data.get('trial_denied')}")
t("2nd signup plan='' (no trial)", sig2_data.get("plan", "<missing>") == "",
  f"got '{sig2_data.get('plan')}'")
test_uid_b = sig2_data.get("id")

# --- Cleanup ---------------------------------------------------------
print("\n[Cleanup]")
import asyncio
mongo_url = open("/app/backend/.env").read()
mongo_url = mongo_url.split("MONGO_URL=")[1].split("\n")[0].strip().strip('"')
async def cleanup():
    from motor.motor_asyncio import AsyncIOMotorClient
    cli = AsyncIOMotorClient(mongo_url)
    db = cli["test_database"]
    n = 0
    for u in [test_uid_a, test_uid_b]:
        if not u: continue
        rd = await db.users.delete_one({"id": u})
        n += rd.deleted_count
        await db.shipments.delete_many({"user_id": u})
        await db.couriers.delete_many({"user_id": u})
        await db.wallets.delete_many({"user_id": u})
        await db.credit_transactions.delete_many({"user_id": u})
        await db.settings.delete_many({"user_id": u})
        await db.pwd_reset_attempts.delete_many({"email": {"$regex": f"refactor_._{ts}"}})
    cli.close()
    return n
deleted = asyncio.run(cleanup())
print(f"  ✓ Removed {deleted} test users + their seed data")

# --- Summary ---------------------------------------------------------
passed = sum(1 for _, ok, _ in results if ok)
total = len(results)
print(f"\n{'='*60}")
print(f"  RESULT: {passed}/{total} {'✅ PASS' if passed == total else '❌ FAIL'}")
print(f"{'='*60}")
if passed != total:
    print("\nFailed tests:")
    for n, ok, d in results:
        if not ok: print(f"  - {n}: {d}")
    sys.exit(1)
