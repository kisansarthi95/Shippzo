"""
Backend tests — Courier Packing Variants endpoints (Phase 2).

Covers:
  - GET    /api/couriers/{courier_id}/variants
  - POST   /api/couriers/{courier_id}/variants
  - PUT    /api/couriers/{courier_id}/variants/{variant_id}
  - DELETE /api/couriers/{courier_id}/variants/{variant_id}
  - GET    /api/me/all-variants
  - GET/PUT /api/admin/plan-limits  (packing_variant_cap exposure)

Run:  python /app/backend_test.py
"""
import os
import sys
import uuid
import json
import requests
from typing import Any, Dict, List, Optional, Tuple

BASE_URL = "https://logistics-hub-740.preview.emergentagent.com/api"

ADMIN = {"email": "admin@test.com", "password": "Admin@12345"}
USER2 = {"email": "user2@test.com", "password": "User@12345"}

PASS_COUNT = 0
FAIL_COUNT = 0
FAILS: List[str] = []


def _check(cond: bool, msg: str):
    global PASS_COUNT, FAIL_COUNT
    if cond:
        PASS_COUNT += 1
        print(f"  ✅ {msg}")
    else:
        FAIL_COUNT += 1
        FAILS.append(msg)
        print(f"  ❌ {msg}")


def login(creds: Dict[str, str]) -> str:
    r = requests.post(f"{BASE_URL}/auth/login", json=creds, timeout=20)
    r.raise_for_status()
    return r.json()["token"]


def auth_headers(tok: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {tok}"}


def get_user_plan(tok: str) -> str:
    r = requests.get(f"{BASE_URL}/auth/me", headers=auth_headers(tok), timeout=20)
    r.raise_for_status()
    return r.json().get("plan", "free_trial")


def get_first_courier_id(tok: str) -> Optional[str]:
    r = requests.get(f"{BASE_URL}/couriers", headers=auth_headers(tok), timeout=20)
    r.raise_for_status()
    arr = r.json()
    return arr[0]["id"] if arr else None


# ─────────────────────── TESTS ───────────────────────

def test_auth_required():
    print("\n[1] Auth required (no token → 401)")
    r = requests.get(f"{BASE_URL}/couriers/{uuid.uuid4()}/variants", timeout=20)
    _check(r.status_code in (401, 403),
           f"GET variants without token → {r.status_code} (expected 401/403)")


def cleanup_variants(tok: str, courier_id: str):
    """Wipe all variants for given courier (so we start from zero)."""
    r = requests.get(f"{BASE_URL}/couriers/{courier_id}/variants",
                     headers=auth_headers(tok), timeout=20)
    if r.status_code != 200:
        return
    for v in r.json().get("variants", []):
        requests.delete(
            f"{BASE_URL}/couriers/{courier_id}/variants/{v['id']}",
            headers=auth_headers(tok), timeout=20,
        )


def test_user2_flow():
    print("\n[2..7] User2 flow (login, list empty, create, cap, list, update, delete)")
    tok = login(USER2)
    plan = get_user_plan(tok)
    print(f"  • user2 plan = {plan!r}")

    courier_id = get_first_courier_id(tok)
    _check(courier_id is not None, "user2 has at least one courier (Demo Courier or seeded)")
    if not courier_id:
        return

    cleanup_variants(tok, courier_id)

    # --- Empty list ---
    r = requests.get(f"{BASE_URL}/couriers/{courier_id}/variants",
                     headers=auth_headers(tok), timeout=20)
    _check(r.status_code == 200, f"GET empty variants → 200 (got {r.status_code})")
    j = r.json()
    _check("variants" in j and "cap" in j and "current_count" in j and "remaining" in j,
           "GET response has variants/cap/current_count/remaining keys")
    _check(j.get("current_count") == 0, f"current_count == 0 (got {j.get('current_count')})")
    _check(j.get("variants") == [], "variants is []")
    _check("package_types" in j and isinstance(j["package_types"], list),
           "package_types list present")
    _check("categories" in j and isinstance(j["categories"], list),
           "categories list present")
    expected_caps = {"free_trial": 1, "silver": 2, "gold": 5, "platinum": 8}
    expected = expected_caps.get(plan, 1)
    cap_returned = j.get("cap")
    _check(cap_returned == expected,
           f"cap = {cap_returned} matches plan {plan!r} expected {expected}")
    print(f"  • cap={cap_returned}  current_count={j.get('current_count')}  remaining={j.get('remaining')}")

    # --- Create one variant ---
    payload = {
        "variant_name": "ODC 320gm",
        "package_type": "Cover",
        "category": "Documents",
        "length_cm": 25, "width_cm": 18, "height_cm": 2,
        "weight_g": 320,
        "within_state_rate": 30,
        "outside_state_rate": 60,
    }
    r = requests.post(f"{BASE_URL}/couriers/{courier_id}/variants",
                      headers=auth_headers(tok), json=payload, timeout=20)
    _check(r.status_code == 200, f"POST first variant → 200 (got {r.status_code} body={r.text[:200]})")
    if r.status_code != 200:
        return
    v1 = r.json()
    _check(v1.get("variant_name") == "ODC 320gm", "variant_name persisted")
    _check(v1.get("package_type") == "Cover", "package_type persisted")
    _check(v1.get("category") == "Documents", "category persisted")
    _check(float(v1.get("length_cm", 0)) == 25, "length_cm persisted")
    _check(float(v1.get("weight_g", 0)) == 320, "weight_g persisted")
    _check(float(v1.get("within_state_rate", 0)) == 30, "within_state_rate persisted")
    _check(float(v1.get("outside_state_rate", 0)) == 60, "outside_state_rate persisted")
    _check(v1.get("user_id") and v1.get("courier_id") == courier_id, "user_id+courier_id stamped")
    _check(bool(v1.get("active", False)), "active=true by default")

    # --- Plan cap enforcement ---
    cap = cap_returned
    if cap is not None:
        # already created 1; create up to cap, then expect 402 on next
        created_extra = []
        for i in range(2, cap + 1):
            extra = dict(payload, variant_name=f"Cap fill #{i}")
            r2 = requests.post(f"{BASE_URL}/couriers/{courier_id}/variants",
                               headers=auth_headers(tok), json=extra, timeout=20)
            _check(r2.status_code == 200, f"POST fill #{i}/{cap} → 200 (got {r2.status_code})")
            if r2.status_code == 200:
                created_extra.append(r2.json().get("id"))
        # Now exceed cap
        r3 = requests.post(f"{BASE_URL}/couriers/{courier_id}/variants",
                           headers=auth_headers(tok),
                           json=dict(payload, variant_name="Should be blocked"),
                           timeout=20)
        _check(r3.status_code == 402, f"POST beyond cap → 402 (got {r3.status_code})")
        body = r3.text
        _check("Packing variant limit reached" in body or "limit" in body.lower(),
               f"402 body mentions 'Packing variant limit reached' (got: {body[:160]})")

    # --- List populated ---
    r = requests.get(f"{BASE_URL}/couriers/{courier_id}/variants",
                     headers=auth_headers(tok), timeout=20)
    _check(r.status_code == 200, "GET after fill → 200")
    j2 = r.json()
    _check(j2.get("current_count") == cap, f"current_count == cap ({cap}) (got {j2.get('current_count')})")
    _check(j2.get("remaining") == 0, f"remaining == 0 (got {j2.get('remaining')})")
    names = [v["variant_name"] for v in j2.get("variants", [])]
    _check("ODC 320gm" in names, "Original ODC 320gm appears in list")

    # --- Update first variant ---
    vid = v1["id"]
    r = requests.put(f"{BASE_URL}/couriers/{courier_id}/variants/{vid}",
                     headers=auth_headers(tok),
                     json={"within_state_rate": 45}, timeout=20)
    _check(r.status_code == 200, f"PUT update rate → 200 (got {r.status_code})")
    if r.status_code == 200:
        _check(float(r.json().get("within_state_rate", 0)) == 45,
               f"within_state_rate updated to 45 (got {r.json().get('within_state_rate')})")
        # Other fields preserved
        _check(r.json().get("variant_name") == "ODC 320gm", "variant_name preserved on PUT")

    # --- Delete first variant ---
    r = requests.delete(f"{BASE_URL}/couriers/{courier_id}/variants/{vid}",
                        headers=auth_headers(tok), timeout=20)
    _check(r.status_code == 200, f"DELETE variant → 200 (got {r.status_code})")
    r = requests.get(f"{BASE_URL}/couriers/{courier_id}/variants",
                     headers=auth_headers(tok), timeout=20)
    after_delete_names = [v["variant_name"] for v in r.json().get("variants", [])]
    _check("ODC 320gm" not in after_delete_names, "Deleted variant gone from list")
    _check(r.json().get("current_count") == cap - 1,
           f"current_count decreased by 1 (got {r.json().get('current_count')})")
    _check(r.json().get("remaining") == 1,
           f"remaining == 1 (got {r.json().get('remaining')})")

    # cleanup remaining test variants
    cleanup_variants(tok, courier_id)


def test_bad_courier_id():
    print("\n[9] Bad courier_id")
    tok = login(USER2)
    bogus = str(uuid.uuid4())
    r = requests.get(f"{BASE_URL}/couriers/{bogus}/variants",
                     headers=auth_headers(tok), timeout=20)
    _check(r.status_code == 404, f"GET bogus courier → 404 (got {r.status_code})")
    _check("Courier not found" in r.text, "GET 404 body mentions 'Courier not found'")
    r = requests.post(f"{BASE_URL}/couriers/{bogus}/variants",
                      headers=auth_headers(tok),
                      json={"variant_name": "x"}, timeout=20)
    _check(r.status_code == 404, f"POST bogus courier → 404 (got {r.status_code})")
    _check("Courier not found" in r.text, "POST 404 body mentions 'Courier not found'")


def test_admin_bypass():
    print("\n[10] Admin bypass — cap=null + can create > plan cap")
    tok = login(ADMIN)
    courier_id = get_first_courier_id(tok)
    _check(courier_id is not None, "admin has at least one courier")
    if not courier_id:
        return
    cleanup_variants(tok, courier_id)

    r = requests.get(f"{BASE_URL}/couriers/{courier_id}/variants",
                     headers=auth_headers(tok), timeout=20)
    _check(r.status_code == 200, "Admin GET variants → 200")
    j = r.json()
    _check(j.get("cap") is None, f"Admin cap == null (got {j.get('cap')!r})")
    _check(j.get("remaining") is None, f"Admin remaining == null (got {j.get('remaining')!r})")

    # Create 9 variants — exceeds platinum cap of 8 — should still succeed for admin
    created_ids = []
    for i in range(9):
        r = requests.post(f"{BASE_URL}/couriers/{courier_id}/variants",
                          headers=auth_headers(tok),
                          json={"variant_name": f"Admin V{i}",
                                "package_type": "Cover", "category": "Other",
                                "within_state_rate": 10, "outside_state_rate": 20},
                          timeout=20)
        if r.status_code == 200:
            created_ids.append(r.json()["id"])
    _check(len(created_ids) == 9, f"Admin created 9 variants beyond top plan cap (got {len(created_ids)})")

    # cleanup
    for vid in created_ids:
        requests.delete(f"{BASE_URL}/couriers/{courier_id}/variants/{vid}",
                        headers=auth_headers(tok), timeout=20)


def test_all_variants_endpoint():
    print("\n[11] /me/all-variants scope + shape")
    tok = login(USER2)
    courier_id = get_first_courier_id(tok)
    cleanup_variants(tok, courier_id)
    # Create 2 variants for user2
    for i in range(2):
        requests.post(f"{BASE_URL}/couriers/{courier_id}/variants",
                      headers=auth_headers(tok),
                      json={"variant_name": f"AVar {i}", "package_type": "Cover",
                            "within_state_rate": 5, "outside_state_rate": 10},
                      timeout=20)
    r = requests.get(f"{BASE_URL}/me/all-variants", headers=auth_headers(tok), timeout=20)
    _check(r.status_code == 200, f"GET /me/all-variants → 200 (got {r.status_code})")
    j = r.json()
    _check("variants" in j and "by_courier" in j and "package_types" in j and "categories" in j,
           "Response keys: variants, by_courier, package_types, categories")
    arr = j.get("variants", [])
    _check(len(arr) >= 2, f"Returns user's variants (got {len(arr)})")
    _check(all(v.get("user_id") for v in arr), "All variants belong to current user (user_id stamped)")
    by = j.get("by_courier", {})
    _check(courier_id in by, f"by_courier has courier_id={courier_id}")
    _check(len(by.get(courier_id, [])) >= 2, "by_courier[courier_id] has 2+ entries")
    cleanup_variants(tok, courier_id)


def test_validation_missing_name():
    print("\n[12] Validation: missing/empty variant_name → 400")
    tok = login(USER2)
    courier_id = get_first_courier_id(tok)
    # Empty string
    r = requests.post(f"{BASE_URL}/couriers/{courier_id}/variants",
                      headers=auth_headers(tok), json={"variant_name": ""}, timeout=20)
    _check(r.status_code == 400, f"POST empty variant_name → 400 (got {r.status_code} body={r.text[:160]})")
    # Whitespace only
    r = requests.post(f"{BASE_URL}/couriers/{courier_id}/variants",
                      headers=auth_headers(tok), json={"variant_name": "   "}, timeout=20)
    _check(r.status_code == 400, f"POST whitespace variant_name → 400 (got {r.status_code})")
    # Missing field — Pydantic should 422 (treat as a validation error category)
    r = requests.post(f"{BASE_URL}/couriers/{courier_id}/variants",
                      headers=auth_headers(tok), json={}, timeout=20)
    _check(r.status_code in (400, 422), f"POST missing variant_name → 400/422 (got {r.status_code})")


def test_plan_limits_exposure():
    print("\n[13] /admin/plan-limits exposes packing_variant_cap (and PUT round-trip)")
    tok = login(ADMIN)
    r = requests.get(f"{BASE_URL}/admin/plan-limits", headers=auth_headers(tok), timeout=20)
    _check(r.status_code == 200, f"GET /admin/plan-limits → 200 (got {r.status_code})")
    if r.status_code != 200:
        return
    j = r.json()
    _check("defaults" in j and "current" in j, "Response has defaults+current")
    expected_defaults = {"free_trial": 1, "silver": 2, "gold": 5, "platinum": 8}
    for key, val in expected_defaults.items():
        d = j.get("defaults", {}).get(key, {})
        c = j.get("current", {}).get(key, {})
        _check(d.get("packing_variant_cap") == val,
               f"defaults.{key}.packing_variant_cap == {val} (got {d.get('packing_variant_cap')})")
        _check("packing_variant_cap" in c,
               f"current.{key} contains packing_variant_cap key")

    # PUT round-trip — set silver to 3
    put_body = {"plans": {"silver": {"packing_variant_cap": 3}}}
    r = requests.put(f"{BASE_URL}/admin/plan-limits",
                     headers=auth_headers(tok), json=put_body, timeout=20)
    _check(r.status_code == 200, f"PUT plan-limits silver=3 → 200 (got {r.status_code} body={r.text[:200]})")
    if r.status_code == 200:
        cur = r.json().get("current", {}).get("silver", {})
        _check(cur.get("packing_variant_cap") == 3,
               f"current.silver.packing_variant_cap == 3 (got {cur.get('packing_variant_cap')})")
    # Confirm via GET
    r = requests.get(f"{BASE_URL}/admin/plan-limits", headers=auth_headers(tok), timeout=20)
    _check(r.status_code == 200 and r.json().get("current", {}).get("silver", {}).get("packing_variant_cap") == 3,
           "GET reflects silver packing_variant_cap=3 after PUT")

    # Restore default (delete override)
    r_reset = requests.put(f"{BASE_URL}/admin/plan-limits",
                           headers=auth_headers(tok),
                           json={"plans": {"silver": {"packing_variant_cap": 2}}},
                           timeout=20)
    print(f"  • restore silver to default (status {r_reset.status_code})")


def main():
    print(f"=== Courier Packing Variants Backend Tests ===")
    print(f"Base URL: {BASE_URL}")
    try:
        test_auth_required()
        test_user2_flow()
        test_bad_courier_id()
        test_admin_bypass()
        test_all_variants_endpoint()
        test_validation_missing_name()
        test_plan_limits_exposure()
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"\nFATAL: {e}")
        sys.exit(2)
    print(f"\n=== RESULTS: {PASS_COUNT} passed, {FAIL_COUNT} failed ===")
    if FAIL_COUNT:
        print("\nFailures:")
        for f in FAILS:
            print(f"  - {f}")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
