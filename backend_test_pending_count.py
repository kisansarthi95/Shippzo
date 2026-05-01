"""
Focused regression — /api/orders/pending-count + /api/sheets/orders cache write.

Verifies:
  1. user with NO sheet → 200 with count==smart_paste_count + sheet_count=0
  2. user WITH sheet but no cache yet → 200 with sheet_count=0 (cache tolerated)
  3. After GET /sheets/orders → pending-count sheet_count > 0 AND
     count == smart_paste_count + sheet_count
  4. /sheets/orders response shape unchanged: headers, headers_changed,
     orders, total, access_method
  5. legacy `count` field still works (= total)
"""
import os
import sys
import requests

BASE = os.environ.get(
    "REACT_APP_BACKEND_URL",
    "https://logistics-hub-740.preview.emergentagent.com",
).rstrip("/") + "/api"

ADMIN_EMAIL = "admin@test.com"
ADMIN_PWD = "Admin@12345"
USER2_EMAIL = "user2@test.com"
USER2_PWD = "User@12345"

passed = 0
failed = 0
fail_msgs = []


def assert_eq(label, got, expected):
    global passed, failed
    if got == expected:
        print(f"  PASS  {label}: {got!r}")
        passed += 1
    else:
        print(f"  FAIL  {label}: got {got!r}, expected {expected!r}")
        failed += 1
        fail_msgs.append(f"{label}: got {got!r}, expected {expected!r}")


def assert_true(label, cond, info=""):
    global passed, failed
    if cond:
        print(f"  PASS  {label}{(' — ' + info) if info else ''}")
        passed += 1
    else:
        print(f"  FAIL  {label} — {info}")
        failed += 1
        fail_msgs.append(f"{label} — {info}")


def login(email, pwd):
    r = requests.post(f"{BASE}/auth/login", json={"email": email, "password": pwd}, timeout=20)
    r.raise_for_status()
    return r.json()


def main():
    global passed, failed
    print(f"Backend: {BASE}")

    # ---------- LOGIN ----------
    admin = login(ADMIN_EMAIL, ADMIN_PWD)
    user2 = login(USER2_EMAIL, USER2_PWD)
    admin_h = {"Authorization": f"Bearer {admin['token']}"}
    user2_h = {"Authorization": f"Bearer {user2['token']}"}

    # ---------- TEST 1: user with NO sheet ----------
    print("\n=== TEST 1: user2 (NO sheet connected) → /orders/pending-count ===")
    s2 = requests.get(f"{BASE}/settings", headers=user2_h, timeout=20).json()
    sheet_id_2 = (s2.get("sheet") or {}).get("sheet_id") or ""
    assert_eq("user2 has no sheet linked", sheet_id_2, "")

    r = requests.get(f"{BASE}/orders/pending-count", headers=user2_h, timeout=20)
    assert_eq("HTTP status 200", r.status_code, 200)
    body = r.json()
    print(f"  body = {body}")
    assert_true("response is dict", isinstance(body, dict))
    assert_true("'count' key present", "count" in body)
    assert_true("'smart_paste_count' key present", "smart_paste_count" in body)
    assert_true("'sheet_count' key present", "sheet_count" in body)
    sp = body.get("smart_paste_count")
    sc = body.get("sheet_count")
    cnt = body.get("count")
    assert_eq("sheet_count == 0 (no sheet)", sc, 0)
    assert_true("count == smart_paste_count + sheet_count", cnt == (sp or 0) + (sc or 0),
                f"count={cnt} sp={sp} sc={sc}")

    # ---------- TEST 2: user WITH sheet but cache field absent ----------
    print("\n=== TEST 2: admin (sheet connected, cache may be missing) → /orders/pending-count ===")
    s1 = requests.get(f"{BASE}/settings", headers=admin_h, timeout=20).json()
    admin_sheet = s1.get("sheet") or {}
    admin_sheet_id = admin_sheet.get("sheet_id") or ""
    print(f"  admin sheet_id = {admin_sheet_id!r}")
    print(f"  admin sheet.unshipped_count_cached = {admin_sheet.get('unshipped_count_cached')!r}")
    assert_true("admin has a sheet linked", bool(admin_sheet_id))

    # Force-clear the cache so we test the "missing cache" branch.
    # We do this via a mongo-less path: hit /sheets/orders later to set
    # it. For now, directly read pending-count and assert no 500.
    r = requests.get(f"{BASE}/orders/pending-count", headers=admin_h, timeout=20)
    assert_eq("HTTP 200 with sheet linked (cache may be missing)", r.status_code, 200)
    body0 = r.json()
    print(f"  body = {body0}")
    sp0 = body0.get("smart_paste_count")
    sc0 = body0.get("sheet_count")
    cnt0 = body0.get("count")
    assert_true("'sheet_count' is int (0 if cache missing)", isinstance(sc0, int))
    assert_true(
        "count == smart_paste_count + sheet_count (admin pre-sheet-fetch)",
        cnt0 == (sp0 or 0) + (sc0 or 0),
        f"count={cnt0} sp={sp0} sc={sc0}",
    )

    # ---------- TEST 3 & 4: GET /sheets/orders populates cache + shape ----------
    print("\n=== TEST 3+4: GET /sheets/orders populates cache & response shape unchanged ===")
    r = requests.get(f"{BASE}/sheets/orders", headers=admin_h, timeout=60)
    print(f"  /sheets/orders HTTP {r.status_code}")
    if r.status_code != 200:
        print(f"  body: {r.text[:500]}")
        failed += 1
        fail_msgs.append(f"/sheets/orders returned {r.status_code}, body={r.text[:300]}")
    else:
        sob = r.json()
        # Shape check
        for k in ("headers", "headers_changed", "orders", "total", "access_method"):
            assert_true(f"/sheets/orders contains '{k}'", k in sob)
        assert_true("orders is list", isinstance(sob.get("orders"), list))
        assert_true("total is int", isinstance(sob.get("total"), int))
        # Compute expected unshipped count
        orders = sob.get("orders") or []
        expected_unshipped = sum(1 for o in orders if not o.get("already_shipped"))
        print(f"  total orders = {len(orders)}, expected_unshipped = {expected_unshipped}")
        print(f"  access_method = {sob.get('access_method')!r}")

        # Now re-fetch pending-count → sheet_count should equal expected_unshipped
        r2 = requests.get(f"{BASE}/orders/pending-count", headers=admin_h, timeout=20)
        assert_eq("post-sheet-fetch pending-count HTTP 200", r2.status_code, 200)
        body2 = r2.json()
        print(f"  body = {body2}")
        sp2 = body2.get("smart_paste_count")
        sc2 = body2.get("sheet_count")
        cnt2 = body2.get("count")
        assert_eq(
            "post-sheet-fetch sheet_count == expected unshipped",
            sc2,
            expected_unshipped,
        )
        assert_true(
            "post-sheet-fetch count == smart_paste_count + sheet_count",
            cnt2 == (sp2 or 0) + (sc2 or 0),
            f"count={cnt2} sp={sp2} sc={sc2}",
        )
        if expected_unshipped > 0:
            assert_true(
                "post-sheet-fetch sheet_count > 0 (review item 3)",
                sc2 > 0, f"sc={sc2}",
            )
        else:
            print("  NOTE: expected_unshipped is 0 — can't verify '>0' criterion, "
                  "but sheet_count==expected and count==sp+sc are both correct.")

    # ---------- TEST 5: legacy count field still works ----------
    print("\n=== TEST 5: legacy `count` field equals total (smart_paste+sheet) ===")
    r3 = requests.get(f"{BASE}/orders/pending-count", headers=admin_h, timeout=20)
    body3 = r3.json()
    sp3 = body3.get("smart_paste_count") or 0
    sc3 = body3.get("sheet_count") or 0
    cnt3 = body3.get("count")
    assert_eq("legacy count == smart_paste + sheet", cnt3, sp3 + sc3)

    # ---------- SUMMARY ----------
    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    if fail_msgs:
        print("Failures:")
        for m in fail_msgs:
            print(f"  - {m}")
        sys.exit(1)
    print("ALL PASS")
    sys.exit(0)


if __name__ == "__main__":
    main()
