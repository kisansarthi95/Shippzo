"""Backend tests for Phase 2D-update — User Custom Categories endpoints.

Endpoints under test:
  GET    /api/me/categories
  POST   /api/me/categories
  DELETE /api/me/categories/{name}
"""
import sys
import urllib.parse
import requests

BASE = "https://logistics-hub-740.preview.emergentagent.com/api"
EMAIL = "user2@test.com"
PASSWORD = "User@12345"

PRESETS = ["Electronics", "Clothing", "Medical", "Documents", "Home Goods", "Other"]

passed = 0
failed = 0
failures = []


def assert_eq(label, actual, expected):
    global passed, failed
    if actual == expected:
        passed += 1
        print(f"  PASS: {label}")
    else:
        failed += 1
        msg = f"  FAIL: {label} — expected {expected!r}, got {actual!r}"
        print(msg)
        failures.append(msg)


def assert_true(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS: {label}")
    else:
        failed += 1
        msg = f"  FAIL: {label} {detail}"
        print(msg)
        failures.append(msg)


def login():
    r = requests.post(f"{BASE}/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=20)
    r.raise_for_status()
    return r.json()["token"]


def main():
    print("=" * 70)
    print("Phase 2D-update — Custom Categories Tests")
    print(f"BASE={BASE}  user={EMAIL}")
    print("=" * 70)

    token = login()
    h = {"Authorization": f"Bearer {token}"}
    print(f"\n[1] Logged in OK (token len={len(token)})")

    # ----- TEST 1: Auth required ---------------------------------------
    print("\n[TEST 1] Auth: missing token → 401/403")
    r = requests.get(f"{BASE}/me/categories", timeout=20)
    assert_true("GET without token rejects", r.status_code in (401, 403), f"status={r.status_code}")
    r = requests.post(f"{BASE}/me/categories", json={"name": "X"}, timeout=20)
    assert_true("POST without token rejects", r.status_code in (401, 403), f"status={r.status_code}")
    r = requests.delete(f"{BASE}/me/categories/X", timeout=20)
    assert_true("DELETE without token rejects", r.status_code in (401, 403), f"status={r.status_code}")

    # ----- Cleanup any leftover test categories from previous runs -----
    print("\n[CLEANUP] Removing leftover test categories if any")
    r = requests.get(f"{BASE}/me/categories", headers=h, timeout=20)
    if r.status_code == 200:
        existing_custom = r.json().get("custom") or []
        for nm in ("Toys", "Home Decor", "Kids", "Y" * 40):
            if nm in existing_custom:
                requests.delete(f"{BASE}/me/categories/{urllib.parse.quote(nm)}", headers=h, timeout=20)
                print(f"  - removed '{nm[:20]}...'")

    # ----- TEST 2: Initial state ---------------------------------------
    print("\n[TEST 2] GET /me/categories initial state")
    r = requests.get(f"{BASE}/me/categories", headers=h, timeout=20)
    assert_eq("GET status 200", r.status_code, 200)
    data = r.json()
    assert_true("Response has 'presets' key", "presets" in data)
    assert_true("Response has 'custom' key", "custom" in data)
    assert_true("'presets' is a list", isinstance(data.get("presets"), list))
    assert_true("'custom' is a list", isinstance(data.get("custom"), list))
    presets = data.get("presets") or []
    for p in PRESETS:
        assert_true(f"Preset '{p}' present", p in presets)
    print(f"  initial custom={data.get('custom')}")

    # ----- TEST 3: Add custom ------------------------------------------
    print("\n[TEST 3] POST /me/categories {'name':'Toys'}")
    r = requests.post(f"{BASE}/me/categories", headers=h, json={"name": "Toys"}, timeout=20)
    assert_eq("POST Toys status 200", r.status_code, 200)
    data = r.json()
    assert_true("'Toys' in custom", "Toys" in (data.get("custom") or []))
    assert_true("Presets still has 6 entries", len(data.get("presets") or []) == 6)

    # ----- TEST 4: Idempotent add --------------------------------------
    print("\n[TEST 4] POST Toys again → idempotent")
    r = requests.post(f"{BASE}/me/categories", headers=h, json={"name": "Toys"}, timeout=20)
    assert_eq("2nd POST Toys status 200", r.status_code, 200)
    data = r.json()
    custom = data.get("custom") or []
    toys_count = sum(1 for c in custom if c == "Toys")
    assert_eq("Exactly one 'Toys' in custom", toys_count, 1)

    # ----- TEST 5: Empty name ------------------------------------------
    print("\n[TEST 5] POST empty/whitespace name → 400")
    r = requests.post(f"{BASE}/me/categories", headers=h, json={"name": "   "}, timeout=20)
    assert_eq("Empty name status 400", r.status_code, 400)
    body = r.json()
    assert_true("detail mentions 'required'",
                "required" in (body.get("detail") or "").lower(),
                f"detail={body.get('detail')!r}")

    # ----- TEST 6: Too long --------------------------------------------
    print("\n[TEST 6] POST name > 40 chars → 400")
    long_name = "X" * 41
    r = requests.post(f"{BASE}/me/categories", headers=h, json={"name": long_name}, timeout=20)
    assert_eq("Too-long name status 400", r.status_code, 400)
    body = r.json()
    assert_true("detail mentions 'too long' or '40'",
                "too long" in (body.get("detail") or "").lower()
                or "40" in (body.get("detail") or ""),
                f"detail={body.get('detail')!r}")

    # Boundary: exactly 40 chars should pass
    print("\n[TEST 6b] POST name = 40 chars → 200 (boundary)")
    boundary = "Y" * 40
    r = requests.post(f"{BASE}/me/categories", headers=h, json={"name": boundary}, timeout=20)
    assert_eq("40-char name status 200", r.status_code, 200)
    if r.status_code == 200:
        requests.delete(f"{BASE}/me/categories/{urllib.parse.quote(boundary)}", headers=h, timeout=20)

    # ----- TEST 7: Built-in collision ----------------------------------
    print("\n[TEST 7] POST 'Electronics' (built-in) → 400")
    r = requests.post(f"{BASE}/me/categories", headers=h, json={"name": "Electronics"}, timeout=20)
    assert_eq("Electronics status 400", r.status_code, 400)
    body = r.json()
    assert_true("detail mentions 'built-in'",
                "built-in" in (body.get("detail") or "").lower(),
                f"detail={body.get('detail')!r}")

    print("\n[TEST 7b] POST 'electronics' (lowercase) → 400 case-insensitive")
    r = requests.post(f"{BASE}/me/categories", headers=h, json={"name": "electronics"}, timeout=20)
    assert_eq("electronics status 400", r.status_code, 400)
    body = r.json()
    assert_true("detail mentions 'built-in' (lowercase)",
                "built-in" in (body.get("detail") or "").lower(),
                f"detail={body.get('detail')!r}")

    print("\n[TEST 7c] POST 'HOME GOODS' (upper multi-word) → 400")
    r = requests.post(f"{BASE}/me/categories", headers=h, json={"name": "HOME GOODS"}, timeout=20)
    assert_eq("HOME GOODS status 400", r.status_code, 400)

    # ----- TEST 8: Delete custom ---------------------------------------
    print("\n[TEST 8] DELETE /me/categories/Toys")
    r = requests.delete(f"{BASE}/me/categories/Toys", headers=h, timeout=20)
    assert_eq("DELETE Toys status 200", r.status_code, 200)
    data = r.json()
    assert_true("'Toys' removed from custom",
                "Toys" not in (data.get("custom") or []),
                f"custom={data.get('custom')}")
    assert_true("Presets still intact after delete",
                all(p in (data.get("presets") or []) for p in PRESETS))

    # ----- TEST 9: Delete non-existent (idempotent) --------------------
    print("\n[TEST 9] DELETE /me/categories/DoesNotExist")
    r = requests.delete(f"{BASE}/me/categories/DoesNotExist", headers=h, timeout=20)
    assert_eq("DELETE non-existent status 200", r.status_code, 200)
    data = r.json()
    assert_true("Response has 'presets'", "presets" in data)
    assert_true("Response has 'custom'", "custom" in data)

    # ----- TEST 10: Multiple + URL-encoded delete ----------------------
    print("\n[TEST 10] Multiple categories + URL-encoded delete")
    r = requests.post(f"{BASE}/me/categories", headers=h, json={"name": "Home Decor"}, timeout=20)
    assert_eq("POST 'Home Decor' status 200", r.status_code, 200)
    r = requests.post(f"{BASE}/me/categories", headers=h, json={"name": "Kids"}, timeout=20)
    assert_eq("POST 'Kids' status 200", r.status_code, 200)

    r = requests.get(f"{BASE}/me/categories", headers=h, timeout=20)
    assert_eq("GET after 2 adds status 200", r.status_code, 200)
    custom = r.json().get("custom") or []
    assert_true("'Home Decor' present", "Home Decor" in custom, f"custom={custom}")
    assert_true("'Kids' present", "Kids" in custom, f"custom={custom}")

    url_encoded = "Home%20Decor"
    r = requests.delete(f"{BASE}/me/categories/{url_encoded}", headers=h, timeout=20)
    assert_eq(f"DELETE {url_encoded} status 200", r.status_code, 200)
    data = r.json()
    custom = data.get("custom") or []
    assert_true("'Home Decor' removed",
                "Home Decor" not in custom, f"custom={custom}")
    assert_true("'Kids' still present",
                "Kids" in custom, f"custom={custom}")

    # Final GET to verify persistence
    r = requests.get(f"{BASE}/me/categories", headers=h, timeout=20)
    data = r.json()
    custom = data.get("custom") or []
    assert_true("Final GET: 'Home Decor' absent",
                "Home Decor" not in custom, f"custom={custom}")
    assert_true("Final GET: 'Kids' present",
                "Kids" in custom, f"custom={custom}")

    # Cleanup "Kids"
    print("\n[CLEANUP] Removing 'Kids'")
    r = requests.delete(f"{BASE}/me/categories/Kids", headers=h, timeout=20)
    assert_eq("DELETE Kids cleanup status 200", r.status_code, 200)

    total = passed + failed
    print("\n" + "=" * 70)
    print(f"RESULT: {passed}/{total} assertions passed  ({failed} failed)")
    print("=" * 70)
    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f)
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
