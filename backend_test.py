"""
Phase F3.9.2 — Plan-gated Pending Order Edit + Delete

Backend tests:
  1. Feature registry contains both pending_orders_edit and pending_orders_delete
     under category 'Customer Intelligence' with the correct labels.
  2. User-facing /me/feature-flags includes both flags for user2 (silver plan).
  3. Happy path: PUT and DELETE pending order succeeds.
  4. Negative path: with flags removed, PUT and DELETE return 403 with the
     exact detail strings, then flags are restored.
  5. Sanity: GET /api/orders/pending stays 200 regardless of flags.
"""
import os
import sys
import copy
import requests

BASE = (
    os.environ.get("BACKEND_URL")
    or "https://logistics-hub-740.preview.emergentagent.com"
).rstrip("/")
API = f"{BASE}/api"

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASS = "Admin@12345"
USER_EMAIL = "user2@test.com"
USER_PASS = "User@12345"

PASS, FAIL = [], []


def _record(ok, msg):
    (PASS if ok else FAIL).append(msg)
    print(("PASS" if ok else "FAIL") + ":", msg)


def login(email, password):
    r = requests.post(
        f"{API}/auth/login",
        json={"email": email, "password": password},
        timeout=20,
    )
    r.raise_for_status()
    body = r.json()
    return body["token"], body


def h(tok):
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}


# ─── SETUP ──────────────────────────────────────────────────────────
print("==> Logging in")
admin_tok, admin_me = login(ADMIN_EMAIL, ADMIN_PASS)
user_tok, user_me = login(USER_EMAIL, USER_PASS)
print(
    f"admin: id={admin_me['id']} is_admin={admin_me['is_admin']} plan={admin_me['plan']}"
)
print(
    f"user2: id={user_me['id']} is_admin={user_me['is_admin']} plan={user_me['plan']}"
)


# ─── SCENARIO 1 — Registry presence ──────────────────────────────────
print("\n==> Scenario 1: Registry presence")
r = requests.get(f"{API}/admin/plan-features", headers=h(admin_tok), timeout=20)
_record(r.status_code == 200, f"admin/plan-features HTTP {r.status_code}")
data = r.json() if r.status_code == 200 else {}
registry = (data.get("registry") or {})
features = registry.get("features") or []
by_key = {f["key"]: f for f in features}

edit_row = by_key.get("pending_orders_edit")
del_row = by_key.get("pending_orders_delete")

_record(edit_row is not None, "registry contains key 'pending_orders_edit'")
_record(del_row is not None, "registry contains key 'pending_orders_delete'")
if edit_row:
    _record(
        edit_row.get("category") == "Customer Intelligence",
        f"pending_orders_edit category = 'Customer Intelligence' (got {edit_row.get('category')!r})",
    )
    _record(
        edit_row.get("label") == "Edit pending order before saving",
        f"pending_orders_edit label correct (got {edit_row.get('label')!r})",
    )
if del_row:
    _record(
        del_row.get("category") == "Customer Intelligence",
        f"pending_orders_delete category = 'Customer Intelligence' (got {del_row.get('category')!r})",
    )
    _record(
        del_row.get("label") == "Delete pending order",
        f"pending_orders_delete label correct (got {del_row.get('label')!r})",
    )

# Verify plan_features assignment for silver/gold/free_trial
plans = data.get("plans") or {}
for tier in ("free_trial", "silver", "gold", "platinum"):
    keys = set(plans.get(tier, []))
    _record(
        "pending_orders_edit" in keys,
        f"plans.{tier} contains 'pending_orders_edit'",
    )
    _record(
        "pending_orders_delete" in keys,
        f"plans.{tier} contains 'pending_orders_delete'",
    )

# User-facing endpoint
r = requests.get(f"{API}/me/feature-flags", headers=h(user_tok), timeout=20)
_record(r.status_code == 200, f"me/feature-flags HTTP {r.status_code}")
flags = set((r.json() or {}).get("features", []))
_record(
    "pending_orders_edit" in flags,
    "user2 effective features contains 'pending_orders_edit' (plan=silver)",
)
_record(
    "pending_orders_delete" in flags,
    "user2 effective features contains 'pending_orders_delete'",
)


# ─── helper — create a pending order via smart-paste ────────────────
def create_pending_order(tok, name_seed="Aarav Mehta"):
    text = (
        f"CUSTOMER_NAME: {name_seed}\n"
        "PHONE: 9812345678\n"
        "ADDRESS_1: 23 Shanti Niwas\n"
        "ADDRESS_2: Near Bus Stand\n"
        "CITY: Surat\n"
        "STATE: Gujarat\n"
        "PINCODE: 395003\n"
        "AMOUNT: 850\n"
        "PAYMENT: COD\n"
        "ITEMS: 1 silk dupatta\n"
        "WEIGHT: 350\n"
    )
    r = requests.post(
        f"{API}/smart-paste",
        headers=h(tok),
        json={"text": text, "skip_llm": True},
        timeout=30,
    )
    if r.status_code != 200:
        print("smart-paste body:", r.status_code, r.text[:300])
    r.raise_for_status()
    return r.json()


# ─── SCENARIO 2 — Happy path ────────────────────────────────────────
print("\n==> Scenario 2: Happy path with flags ON")
po = create_pending_order(user_tok, "Aarav Mehta")
po_id = po.get("id")
_record(bool(po_id), f"created pending order id={po_id} master_order_id={po.get('master_order_id')}")

r = requests.put(
    f"{API}/orders/pending/{po_id}",
    headers=h(user_tok),
    json={"customer_name": "TEST EDIT 200"},
    timeout=20,
)
_record(r.status_code == 200, f"PUT pending HTTP {r.status_code} (body: {r.text[:160]})")
if r.status_code == 200:
    _record(
        r.json().get("customer_name") == "TEST EDIT 200",
        f"PUT response.customer_name == 'TEST EDIT 200' (got {r.json().get('customer_name')!r})",
    )

r = requests.delete(f"{API}/orders/pending/{po_id}", headers=h(user_tok), timeout=20)
_record(r.status_code == 200, f"DELETE pending HTTP {r.status_code} (body: {r.text[:160]})")
if r.status_code == 200:
    _record(
        bool(r.json().get("ok")),
        f"DELETE response.ok == true (got {r.json().get('ok')!r})",
    )


# ─── SCENARIO 3 — Negative path: flip flags OFF on silver via admin ──
print("\n==> Scenario 3: Negative path (flags OFF)")

plans_snapshot = copy.deepcopy(plans)
silver_full = set(plans.get("silver", []))

silver_no_edit = sorted(silver_full - {"pending_orders_edit"})
new_plans = copy.deepcopy(plans)
new_plans["silver"] = silver_no_edit

r = requests.put(
    f"{API}/admin/plan-features",
    headers=h(admin_tok),
    json={"plans": new_plans},
    timeout=20,
)
_record(r.status_code == 200, f"admin PUT plans (no edit) HTTP {r.status_code}")
saved = (r.json() or {}).get("plans", {}).get("silver", [])
_record(
    "pending_orders_edit" not in set(saved),
    "after admin PUT, silver no longer has 'pending_orders_edit'",
)

po2 = create_pending_order(user_tok, "Negative Edit Test")
po2_id = po2["id"]

r = requests.put(
    f"{API}/orders/pending/{po2_id}",
    headers=h(user_tok),
    json={"customer_name": "SHOULD NOT WORK"},
    timeout=20,
)
_record(
    r.status_code == 403,
    f"PUT with flag OFF returns 403 (got {r.status_code}, body={r.text[:200]})",
)
if r.status_code == 403:
    detail = (r.json() or {}).get("detail")
    expected = "Your plan doesn't include editing pending orders."
    _record(
        detail == expected,
        f"403 detail matches expected (got {detail!r})",
    )

silver_no_both = sorted(set(silver_no_edit) - {"pending_orders_delete"})
new_plans["silver"] = silver_no_both
r = requests.put(
    f"{API}/admin/plan-features",
    headers=h(admin_tok),
    json={"plans": new_plans},
    timeout=20,
)
_record(r.status_code == 200, f"admin PUT plans (no edit, no delete) HTTP {r.status_code}")
saved = (r.json() or {}).get("plans", {}).get("silver", [])
_record(
    "pending_orders_delete" not in set(saved),
    "after admin PUT, silver no longer has 'pending_orders_delete'",
)

r = requests.delete(
    f"{API}/orders/pending/{po2_id}", headers=h(user_tok), timeout=20
)
_record(
    r.status_code == 403,
    f"DELETE with flag OFF returns 403 (got {r.status_code}, body={r.text[:200]})",
)
if r.status_code == 403:
    detail = (r.json() or {}).get("detail")
    expected_del = "Your plan doesn't include deleting pending orders."
    _record(
        detail == expected_del,
        f"403 detail matches expected (got {detail!r})",
    )


# ─── SCENARIO 4 — Sanity: GET /api/orders/pending still 200 ─────────
print("\n==> Scenario 4: Sanity — GET pending list is not gated")
r = requests.get(f"{API}/orders/pending", headers=h(user_tok), timeout=20)
_record(r.status_code == 200, f"GET orders/pending HTTP {r.status_code}")
_record(
    isinstance(r.json(), list),
    "GET orders/pending returns a list (read access not gated)",
)


# ─── RESTORE flags ──────────────────────────────────────────────────
print("\n==> Restoring plan_features to original")
r = requests.put(
    f"{API}/admin/plan-features",
    headers=h(admin_tok),
    json={"plans": plans_snapshot},
    timeout=20,
)
_record(r.status_code == 200, f"restore plan_features HTTP {r.status_code}")
restored = (r.json() or {}).get("plans", {}).get("silver", [])
_record(
    {"pending_orders_edit", "pending_orders_delete"}.issubset(set(restored)),
    "after restore, silver has both pending_orders_edit + pending_orders_delete",
)

# Best-effort cleanup of the leftover pending order created in negative path
r = requests.delete(f"{API}/orders/pending/{po2_id}", headers=h(user_tok), timeout=20)
print(f"cleanup DELETE po2 -> {r.status_code} {r.text[:100]}")


# ─── SUMMARY ────────────────────────────────────────────────────────
print("\n========= SUMMARY =========")
print(f"PASS: {len(PASS)}    FAIL: {len(FAIL)}")
if FAIL:
    print("\nFailures:")
    for f in FAIL:
        print(" -", f)
    sys.exit(1)
print("All assertions passed.")
sys.exit(0)
