"""Backend tests for Phase-C sync-from-master endpoint."""
import os
import json
import requests

BASE_URL = "https://logistics-hub-740.preview.emergentagent.com/api"
ADMIN_EMAIL = "admin@test.com"
ADMIN_PASSWORD = "Admin@12345"

PASS = []
FAIL = []


def assert_eq(label, got, expected):
    ok = got == expected
    rec = (label, got, expected, ok)
    (PASS if ok else FAIL).append(rec)
    icon = "OK" if ok else "FAIL"
    print(f"  [{icon}] {label}: got={got!r}, expected={expected!r}")
    return ok


def assert_true(label, cond, detail=""):
    rec = (label, detail, "truthy", bool(cond))
    (PASS if cond else FAIL).append(rec)
    icon = "OK" if cond else "FAIL"
    print(f"  [{icon}] {label}{(' → ' + detail) if detail else ''}")
    return cond


def login(email, password):
    r = requests.post(f"{BASE_URL}/auth/login", json={"email": email, "password": password}, timeout=15)
    r.raise_for_status()
    return r.json()["token"]


def auth(tok):
    return {"Authorization": f"Bearer {tok}"}


def main():
    print("=" * 70)
    print("Phase-C Sync-From-Master Endpoint Tests")
    print("=" * 70)

    # Login as admin
    print("\n[Setup] Login admin@test.com")
    token = login(ADMIN_EMAIL, ADMIN_PASSWORD)
    h = auth(token)
    print(f"  token (first 16): {token[:16]}…")

    # ---- Test 4 first: backend health ----
    print("\n[Test 4] Backend health — GET /api/auth/me")
    r = requests.get(f"{BASE_URL}/auth/me", headers=h, timeout=15)
    assert_eq("auth/me status", r.status_code, 200)
    me = r.json()
    assert_true("auth/me has email", me.get("email") == ADMIN_EMAIL,
                detail=f"email={me.get('email')}")
    assert_true("admin is_admin == True", bool(me.get("is_admin")))

    # Read original sheet config so we can restore at the end.
    r0 = requests.get(f"{BASE_URL}/settings", headers=h, timeout=15)
    r0.raise_for_status()
    original_sheet_cfg = (r0.json() or {}).get("sheet") or {}
    print(f"  original sheet cfg: {json.dumps(original_sheet_cfg)}")

    # Read master_sheet_id from admin/global-config
    print("\n[Setup] GET /api/admin/global-config — read master_sheet_id")
    r = requests.get(f"{BASE_URL}/admin/global-config", headers=h, timeout=15)
    print(f"  status={r.status_code}, body keys: {list(r.json().keys()) if r.status_code == 200 else r.text[:200]}")
    assert_eq("global-config status", r.status_code, 200)
    gc = r.json()
    master_sheet_id = (gc.get("master_sheet_id") or "").strip()
    master_sheet_tab = (gc.get("master_sheet_tab") or "Sheet1").strip()
    print(f"  master_sheet_id={master_sheet_id!r}, master_sheet_tab={master_sheet_tab!r}")
    assert_true("master_sheet_id is non-empty", bool(master_sheet_id),
                detail=f"value={master_sheet_id!r}")

    try:
        # ============================================================
        # TEST 1 — sync without user sheet linked
        # ============================================================
        print("\n[Test 1] Sync without user sheet linked")
        # Step 1.1: PUT /api/settings to clear any linked sheet
        clear_payload = {"sheet": {"sheet_id": "", "gid": "0"}}
        r = requests.put(f"{BASE_URL}/settings", headers=h, json=clear_payload, timeout=15)
        assert_eq("PUT /settings clear sheet status", r.status_code, 200)
        cleared = (r.json() or {}).get("sheet") or {}
        assert_eq("sheet.sheet_id cleared", (cleared.get("sheet_id") or ""), "")

        # Step 1.2: POST /api/sheets/sync-from-master expect 422
        r = requests.post(
            f"{BASE_URL}/sheets/sync-from-master",
            headers=h, json={"overwrite": True}, timeout=20,
        )
        assert_eq("Test1 sync status (no sheet linked)", r.status_code, 422)
        try:
            body = r.json()
        except Exception:
            body = {"raw": r.text[:200]}
        detail = body.get("detail", "")
        print(f"  body: {json.dumps(body)[:300]}")
        assert_true(
            "Test1 detail mentions 'Link your Google Sheet'",
            "Link your Google Sheet" in str(detail),
            detail=f"detail={detail!r}",
        )

        # ============================================================
        # TEST 2 — Sync with sheet linked (overwrite mode)
        # ============================================================
        print("\n[Test 2] Sync with sheet linked (overwrite mode)")
        link_payload = {"sheet": {"sheet_id": master_sheet_id, "gid": "0"}}
        r = requests.put(f"{BASE_URL}/settings", headers=h, json=link_payload, timeout=15)
        assert_eq("PUT /settings link master sheet status", r.status_code, 200)
        linked = (r.json() or {}).get("sheet") or {}
        assert_eq("sheet.sheet_id == master_sheet_id", linked.get("sheet_id"), master_sheet_id)

        r = requests.post(
            f"{BASE_URL}/sheets/sync-from-master",
            headers=h, json={"overwrite": True}, timeout=120,
        )
        print(f"  Test2 sync status={r.status_code}, body[:400]={r.text[:400]}")
        assert_eq("Test2 sync status", r.status_code, 200)
        if r.status_code == 200:
            body = r.json()
            assert_eq("Test2 ok", body.get("ok"), True)
            rows_synced_t2 = body.get("rows_synced")
            assert_true(
                "Test2 rows_synced is int >= 0",
                isinstance(rows_synced_t2, int) and rows_synced_t2 >= 0,
                detail=f"rows_synced={rows_synced_t2!r}",
            )
            assert_true(
                "Test2 tab is non-empty string",
                isinstance(body.get("tab"), str) and len(body.get("tab")) > 0,
                detail=f"tab={body.get('tab')!r}",
            )
            assert_eq("Test2 mode == 'overwrite'", body.get("mode"), "overwrite")

            # Idempotent re-call — should produce same row count.
            print("\n  [Test 2.b] Idempotency — re-call sync (overwrite)")
            r2 = requests.post(
                f"{BASE_URL}/sheets/sync-from-master",
                headers=h, json={"overwrite": True}, timeout=120,
            )
            print(f"    re-call status={r2.status_code}, body[:300]={r2.text[:300]}")
            if r2.status_code == 200:
                rows2 = r2.json().get("rows_synced")
                assert_eq("Test2 idempotent rows_synced match", rows2, rows_synced_t2)
            else:
                FAIL.append(("Test2 idempotent re-call status==200", r2.status_code, 200, False))

            # ============================================================
            # TEST 3 — Sync in append mode (dedup)
            # ============================================================
            print("\n[Test 3] Sync in append mode (dedup by master_order_id)")
            r3 = requests.post(
                f"{BASE_URL}/sheets/sync-from-master",
                headers=h, json={"overwrite": False}, timeout=120,
            )
            print(f"  Test3 status={r3.status_code}, body[:400]={r3.text[:400]}")
            assert_eq("Test3 sync status", r3.status_code, 200)
            if r3.status_code == 200:
                b3 = r3.json()
                assert_eq("Test3 ok", b3.get("ok"), True)
                assert_eq("Test3 mode == 'append'", b3.get("mode"), "append")
                assert_eq("Test3 rows_synced == 0 (dedup)", b3.get("rows_synced"), 0)

    finally:
        # Restore original sheet config
        print("\n[Cleanup] Restore original sheet config")
        try:
            restore_payload = {
                "sheet": {
                    "sheet_id": original_sheet_cfg.get("sheet_id", ""),
                    "gid": original_sheet_cfg.get("gid", "0"),
                }
            }
            r = requests.put(f"{BASE_URL}/settings", headers=h, json=restore_payload, timeout=15)
            print(f"  restore status={r.status_code}")
        except Exception as e:
            print(f"  restore failed: {e}")

    # ---- Summary ----
    print("\n" + "=" * 70)
    print(f"PASS: {len(PASS)}    FAIL: {len(FAIL)}")
    print("=" * 70)
    if FAIL:
        print("\nFailed assertions:")
        for label, got, exp, _ in FAIL:
            print(f"  - {label}: got={got!r}, expected={exp!r}")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
