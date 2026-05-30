"""
Phase-29 regression test — server.py router extraction.
Tests auth, sheet-sync, analytics routers + smoke endpoints.
"""
from __future__ import annotations
import sys
import uuid
import requests

BASE = "https://logistics-hub-740.preview.emergentagent.com/api"

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASS  = "Admin@12345"
USER_EMAIL  = "user2@test.com"
USER_PASS   = "User@12345"

passes: list[str] = []
fails: list[str] = []


def _ok(label: str):
    print(f"  PASS  {label}")
    passes.append(label)


def _fail(label: str, detail: str = ""):
    print(f"  FAIL  {label}  {detail}")
    fails.append(f"{label} :: {detail}")


def req(method: str, path: str, *, token: str = "", **kw) -> requests.Response:
    headers = kw.pop("headers", {}) or {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return requests.request(method, BASE + path, headers=headers, timeout=30, **kw)


# ════════════════════════════════════════════════════════════════
# A) AUTH ROUTER
# ════════════════════════════════════════════════════════════════
print("\n========== A) AUTH ROUTER ==========")
admin_token = ""
user_token  = ""

# 1. admin login
r = req("POST", "/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASS})
if r.status_code == 200 and r.json().get("token"):
    admin_token = r.json()["token"]
    _ok(f"A01 POST /auth/login (admin) → 200, token len={len(admin_token)}")
else:
    _fail("A01 POST /auth/login (admin)", f"got {r.status_code} {r.text[:200]}")

# user2 login (for negative tests)
r = req("POST", "/auth/login", json={"email": USER_EMAIL, "password": USER_PASS})
if r.status_code == 200 and r.json().get("token"):
    user_token = r.json()["token"]
    _ok("A01b POST /auth/login (user2) → 200")
else:
    _fail("A01b POST /auth/login (user2)", f"got {r.status_code} {r.text[:200]}")

# 2. bad password
r = req("POST", "/auth/login", json={"email": ADMIN_EMAIL, "password": "WrongPass123"})
if r.status_code == 401:
    _ok("A02 POST /auth/login (bad pass) → 401")
else:
    _fail("A02 POST /auth/login (bad pass)", f"got {r.status_code} {r.text[:200]}")

# 3. /auth/me
r = req("GET", "/auth/me", token=admin_token)
if r.status_code == 200:
    body = r.json()
    if "id" in body and "email" in body and body.get("email") == ADMIN_EMAIL:
        _ok(f"A03 GET /auth/me → 200, email={body['email']}")
    else:
        _fail("A03 GET /auth/me shape", str(body)[:200])
else:
    _fail("A03 GET /auth/me", f"got {r.status_code}")

# 4. /auth/context
r = req("GET", "/auth/context", token=admin_token)
if r.status_code == 200:
    body = r.json()
    needed = {"user", "is_team_member", "needs_profile_completion"}
    missing = needed - set(body.keys())
    if not missing:
        _ok(f"A04 GET /auth/context → 200 (is_team_member={body['is_team_member']})")
    else:
        _fail("A04 GET /auth/context shape", f"missing keys: {missing}")
else:
    _fail("A04 GET /auth/context", f"got {r.status_code}")

# 5. signup with valid business_category + 10-digit phone
new_email = f"phase29regress+{uuid.uuid4().hex[:8]}@test.com"
signup_body_valid = {
    "email": new_email,
    "password": "NewUser@12345",
    "name": "Phase29 Tester",
    "shop_name": "Phase29 Shop",
    "phone": "9988776655",
    "primary_business_category": "fashion_apparel",
}
r = req("POST", "/auth/signup", json=signup_body_valid)
if r.status_code == 200 and r.json().get("token"):
    _ok(f"A05 POST /auth/signup (valid) → 200, token, email={new_email}")
else:
    _fail("A05 POST /auth/signup (valid)", f"got {r.status_code} {r.text[:300]}")

# 6. signup with invalid category
r = req("POST", "/auth/signup", json={
    "email": f"phase29bad+{uuid.uuid4().hex[:6]}@test.com",
    "password": "NewUser@12345",
    "name": "Bad Cat",
    "shop_name": "X",
    "phone": "9988776655",
    "primary_business_category": "bogus_xyz",
})
if r.status_code == 400 and "valid business category" in r.text.lower():
    _ok("A06 POST /auth/signup (invalid cat) → 400 valid-business-category msg")
else:
    _fail("A06 POST /auth/signup (invalid cat)", f"got {r.status_code} {r.text[:200]}")

# 7. signup phone too short
r = req("POST", "/auth/signup", json={
    "email": f"phase29short+{uuid.uuid4().hex[:6]}@test.com",
    "password": "NewUser@12345",
    "name": "Short Phone",
    "shop_name": "Y",
    "phone": "123",
    "primary_business_category": "fashion_apparel",
})
if r.status_code == 400 and "10-digit mobile number" in r.text:
    _ok("A07 POST /auth/signup (phone too short) → 400 valid-phone msg")
else:
    _fail("A07 POST /auth/signup (phone short)", f"got {r.status_code} {r.text[:200]}")

# 8. signup same email twice
dup_email = f"phase29dup+{uuid.uuid4().hex[:8]}@test.com"
dup_body = {
    "email": dup_email,
    "password": "NewUser@12345",
    "name": "Dup",
    "shop_name": "Z",
    "phone": "9988776655",
    "primary_business_category": "fashion_apparel",
}
r1 = req("POST", "/auth/signup", json=dup_body)
r2 = req("POST", "/auth/signup", json=dup_body)
if r1.status_code == 200 and r2.status_code == 400 and "already registered" in r2.text.lower():
    _ok("A08 POST /auth/signup (dup email) → 400 already-registered")
else:
    _fail("A08 POST /auth/signup (dup email)", f"first={r1.status_code} second={r2.status_code} text={r2.text[:200]}")

# 9. /business-categories (no auth)
r = req("GET", "/auth/business-categories")
if r.status_code == 200:
    cats = r.json().get("categories") or []
    if isinstance(cats, list) and len(cats) > 0:
        _ok(f"A09 GET /auth/business-categories → 200, {len(cats)} cats")
    else:
        _fail("A09 GET /auth/business-categories", f"empty cats: {r.text[:200]}")
else:
    _fail("A09 GET /auth/business-categories", f"got {r.status_code}")

# 10. /logout
r = req("POST", "/auth/logout", token=admin_token)
if r.status_code == 200 and r.json().get("ok") is True:
    _ok("A10 POST /auth/logout → 200 ok=true")
else:
    _fail("A10 POST /auth/logout", f"got {r.status_code} {r.text[:200]}")

# 11. forgot-password wrong phone
r = req("POST", "/auth/forgot-password", json={
    "email": ADMIN_EMAIL,
    "phone": "1111111111",
    "new_password": "NewPass@12345",
})
if r.status_code == 400 and ("don't match" in r.text or "doesn't match" in r.text or "details don" in r.text):
    _ok("A11 POST /auth/forgot-password (wrong phone) → 400 mismatch")
else:
    _fail("A11 POST /auth/forgot-password (wrong phone)", f"got {r.status_code} {r.text[:200]}")

# 12. forgot-password missing phone
r = req("POST", "/auth/forgot-password", json={
    "email": ADMIN_EMAIL,
    "phone": "",
    "new_password": "NewPass@12345",
})
if r.status_code in (400, 422):
    _ok(f"A12 POST /auth/forgot-password (missing phone) → {r.status_code}")
else:
    _fail("A12 POST /auth/forgot-password (missing phone)", f"got {r.status_code} {r.text[:200]}")

# 13. forgot-password VALID flow — get admin's phone first
admin_phone = ""
r = req("GET", "/auth/context", token=admin_token)
if r.status_code == 200:
    admin_phone = (r.json().get("user") or {}).get("phone") or ""

if not admin_phone:
    _fail("A13-prep admin phone unavailable", "skipping A13a/b (cannot proceed without admin phone)")
else:
    tmp_pass = "TempPass@99999"
    r = req("POST", "/auth/forgot-password", json={
        "email": ADMIN_EMAIL,
        "phone": admin_phone,
        "new_password": tmp_pass,
    })
    if r.status_code == 200 and r.json().get("token"):
        _ok(f"A13a POST /auth/forgot-password (valid) → 200, new token (phone={admin_phone[:4]}***)")
        r2 = req("POST", "/auth/forgot-password", json={
            "email": ADMIN_EMAIL,
            "phone": admin_phone,
            "new_password": ADMIN_PASS,
        })
        if r2.status_code == 200:
            _ok("A13b restore Admin@12345 → 200")
        else:
            _fail("A13b restore admin password", f"got {r2.status_code} {r2.text[:300]} — ADMIN PASSWORD MAY NEED MANUAL RESET")
    else:
        _fail("A13a POST /auth/forgot-password (valid)", f"got {r.status_code} {r.text[:300]}")

# Re-login to confirm admin password restored & rotate token
r = req("POST", "/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASS})
if r.status_code == 200 and r.json().get("token"):
    admin_token = r.json()["token"]
    _ok("A13c re-login admin after restore → 200")
else:
    _fail("A13c re-login admin after restore", f"got {r.status_code} {r.text[:300]} — ADMIN MAY BE LOCKED OUT")

# 14. google/session empty
r = req("POST", "/auth/google/session", json={"session_id": ""})
if r.status_code == 400:
    _ok("A14 POST /auth/google/session (empty) → 400")
else:
    _fail("A14 POST /auth/google/session (empty)", f"got {r.status_code} {r.text[:200]}")

# 15. google/session bogus
r = req("POST", "/auth/google/session", json={"session_id": "abcdefghij"})
if r.status_code in (401, 502):
    _ok(f"A15 POST /auth/google/session (bogus) → {r.status_code}")
else:
    _fail("A15 POST /auth/google/session (bogus)", f"got {r.status_code} {r.text[:200]}")

# 16. business-category (admin)
prev_cat = ""
r = req("GET", "/auth/context", token=admin_token)
if r.status_code == 200:
    prev_cat = (r.json().get("user") or {}).get("primary_business_category") or ""

r = req("POST", "/auth/business-category", token=admin_token, json={"category": "organic_herbal"})
if r.status_code == 200 and r.json().get("ok"):
    r2 = req("GET", "/auth/context", token=admin_token)
    if r2.status_code == 200 and (r2.json().get("user") or {}).get("primary_business_category") == "organic_herbal":
        _ok("A16 POST /auth/business-category → 200, /context reflects organic_herbal")
    else:
        _fail("A16 /context did not reflect category change", str(r2.json())[:200])
    if prev_cat:
        req("POST", "/auth/business-category", token=admin_token, json={"category": prev_cat})
else:
    _fail("A16 POST /auth/business-category", f"got {r.status_code} {r.text[:200]}")


# ════════════════════════════════════════════════════════════════
# B) SHEET-SYNC ROUTER
# ════════════════════════════════════════════════════════════════
print("\n========== B) SHEET-SYNC ROUTER ==========")

orig_toggles = {}
r = req("GET", "/me/sheet-sync/status", token=admin_token)
if r.status_code == 200:
    body = r.json()
    needed = {"connected", "sheet_id", "auto_sync_create", "auto_sync_status",
              "auto_sync_delete", "shipment_counts", "queue_pending"}
    missing = needed - set(body.keys())
    if not missing:
        _ok(f"B17 GET /me/sheet-sync/status → 200 (connected={body['connected']}, pending={body['queue_pending']})")
        orig_toggles = {
            "auto_sync_create": bool(body.get("auto_sync_create")),
            "auto_sync_status": bool(body.get("auto_sync_status")),
            "auto_sync_delete": bool(body.get("auto_sync_delete")),
        }
    else:
        _fail("B17 GET /me/sheet-sync/status shape", f"missing: {missing}")
else:
    _fail("B17 GET /me/sheet-sync/status", f"got {r.status_code} {r.text[:200]}")

# 18. PUT toggles
r = req("PUT", "/me/sheet-sync/toggles", token=admin_token, json={"auto_sync_create": True})
if r.status_code == 200 and r.json().get("auto_sync_create") is True:
    _ok("B18 PUT /me/sheet-sync/toggles {auto_sync_create:true} → 200")
else:
    _fail("B18 PUT /me/sheet-sync/toggles", f"got {r.status_code} {r.text[:200]}")

if orig_toggles:
    req("PUT", "/me/sheet-sync/toggles", token=admin_token, json=orig_toggles)

# 19. run-now
r = req("POST", "/me/sheet-sync/run-now", token=admin_token)
if r.status_code == 200:
    body = r.json()
    keys = {"drained", "backfilled", "errored"}
    if keys.issubset(set(body.keys())):
        _ok(f"B19 POST /me/sheet-sync/run-now → 200 (drained={body['drained']}, backfilled={body['backfilled']}, errored={body['errored']})")
    else:
        _fail("B19 /run-now shape", str(body)[:200])
else:
    _fail("B19 POST /me/sheet-sync/run-now", f"got {r.status_code} {r.text[:300]}")

# 20. nonexistent shipment
r = req("POST", "/me/sheet-sync/shipment/NONEXISTENT", token=admin_token)
if r.status_code == 404:
    _ok("B20 POST /me/sheet-sync/shipment/NONEXISTENT → 404")
else:
    _fail("B20 POST /me/sheet-sync/shipment/NONEXISTENT", f"got {r.status_code} {r.text[:200]}")


# ════════════════════════════════════════════════════════════════
# C) ANALYTICS ROUTER
# ════════════════════════════════════════════════════════════════
print("\n========== C) ANALYTICS ROUTER ==========")

# 21. /analytics/overview scope=mine
r = req("GET", "/analytics/overview?range=30d&scope=mine", token=admin_token)
if r.status_code == 200:
    body = r.json()
    needed = {"kpi", "shipments", "trend_30d"}
    missing = needed - set(body.keys())
    if not missing:
        _ok(f"C21 GET /analytics/overview?scope=mine → 200 (kpi.total={body['kpi'].get('total')})")
    else:
        _fail("C21 /analytics/overview shape", f"missing: {missing}")
else:
    _fail("C21 GET /analytics/overview?scope=mine", f"got {r.status_code} {r.text[:200]}")

# 22. scope=platform (admin)
r = req("GET", "/analytics/overview?range=30d&scope=platform", token=admin_token)
if r.status_code == 200:
    body = r.json()
    if "admin" in body and "kpi" in body and "shipments" in body and "trend_30d" in body:
        _ok(f"C22 GET /analytics/overview?scope=platform (admin) → 200 (admin.users={body['admin']['users']})")
    else:
        _fail("C22 /analytics/overview platform shape", str(list(body.keys())))
else:
    _fail("C22 GET /analytics/overview?scope=platform admin", f"got {r.status_code} {r.text[:200]}")

# 23. scope=platform (non-admin) → 403
r = req("GET", "/analytics/overview?range=30d&scope=platform", token=user_token)
if r.status_code == 403:
    _ok("C23 GET /analytics/overview?scope=platform (user2) → 403")
else:
    _fail("C23 GET /analytics/overview?scope=platform (user2)", f"got {r.status_code} {r.text[:200]}")

# 24. /admin/analytics/overview (admin)
r = req("GET", "/admin/analytics/overview?range=7d", token=admin_token)
if r.status_code == 200:
    body = r.json()
    needed = {"users", "shipments", "trend_30d", "sla"}
    missing = needed - set(body.keys())
    if not missing:
        _ok(f"C24 GET /admin/analytics/overview?range=7d → 200 (users.total={body['users']['total']})")
    else:
        _fail("C24 /admin/analytics/overview shape", f"missing: {missing}")
else:
    _fail("C24 GET /admin/analytics/overview", f"got {r.status_code} {r.text[:200]}")

# 25. /admin/analytics/overview (user2) → 403
r = req("GET", "/admin/analytics/overview", token=user_token)
if r.status_code == 403:
    _ok("C25 GET /admin/analytics/overview (user2) → 403")
else:
    _fail("C25 GET /admin/analytics/overview (user2)", f"got {r.status_code} {r.text[:200]}")


# ════════════════════════════════════════════════════════════════
# D) SMOKE
# ════════════════════════════════════════════════════════════════
print("\n========== D) SMOKE ==========")

r = req("GET", "/shipments", token=admin_token)
if r.status_code == 200 and isinstance(r.json(), list):
    _ok(f"D26 GET /shipments (admin) → 200 ({len(r.json())} items)")
else:
    _fail("D26 GET /shipments", f"got {r.status_code} {r.text[:200]}")

r = req("GET", "/articles")
if r.status_code == 200:
    body = r.json()
    items = body if isinstance(body, list) else body.get("articles") or body.get("items") or []
    if len(items) >= 6:
        _ok(f"D27 GET /articles → 200 ({len(items)} items)")
    else:
        _fail("D27 GET /articles count<6", f"got {len(items)}; body keys={list(body.keys()) if isinstance(body, dict) else 'list'}")
else:
    _fail("D27 GET /articles", f"got {r.status_code}")

r = req("GET", "/faq")
if r.status_code == 200:
    _ok("D28 GET /faq → 200")
else:
    _fail("D28 GET /faq", f"got {r.status_code} {r.text[:200]}")

r = req("GET", "/admin/whatsapp-provider/config", token=admin_token)
if r.status_code == 200:
    _ok("D29 GET /admin/whatsapp-provider/config (admin) → 200")
else:
    _fail("D29 GET /admin/whatsapp-provider/config", f"got {r.status_code} {r.text[:200]}")


# ════════════════════════════════════════════════════════════════
# SUMMARY
# ════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print(f"PASSED: {len(passes)}")
print(f"FAILED: {len(fails)}")
if fails:
    print("\nFAILURES:")
    for f in fails:
        print(f"  - {f}")
sys.exit(0 if not fails else 1)
