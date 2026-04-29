"""
Phase-C Sync-From-Master — RETEST Test 3 (append-mode dedup).

Bug fix applied: sync_master_to_user_sheet now falls back to canonical
column index when the "Master Order ID" header cell is blank.

Setup (per review request):
1) Reuse admin's existing user-sheet config (already set in last test run).
2) POST /api/sheets/sync-from-master {"overwrite": true} → BASELINE.

Test:
1) POST /api/sheets/sync-from-master {"overwrite": false} → expect rows_synced == 0.
2) Repeat {"overwrite": false} → expect rows_synced == 0 (idempotent).
3) {"overwrite": true} → expect rows_synced == BASELINE again (no duplicates).
"""
import json
import os
import sys
import requests

BASE = os.getenv("BACKEND_BASE", "http://localhost:8001/api")
EMAIL = "admin@test.com"
PASSWORD = "Admin@12345"


def main() -> int:
    s = requests.Session()
    print(f"[*] Base: {BASE}")

    # Auth
    r = s.post(f"{BASE}/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=30)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    tok = r.json()["token"]
    h = {"Authorization": f"Bearer {tok}"}

    # Confirm a user-sheet config is currently set
    r = s.get(f"{BASE}/settings", headers=h, timeout=30)
    assert r.status_code == 200, f"GET /settings failed: {r.status_code} {r.text}"
    settings = r.json() or {}
    sh = (settings.get("sheet") or {})
    sid = (sh.get("sheet_id") or "").strip()
    gid = (sh.get("gid") or "").strip()
    print(f"[*] User-sheet config: sheet_id={sid!r}, gid={gid!r}")
    if not sid:
        print("[!] No user sheet linked. Cannot run Phase-C dedup test.")
        return 2

    # Baseline: overwrite=true
    print("\n[1/4] BASELINE — POST /sheets/sync-from-master {overwrite:true}")
    r = s.post(f"{BASE}/sheets/sync-from-master", headers=h,
               json={"overwrite": True}, timeout=120)
    print(f"     status={r.status_code}")
    print(f"     body={json.dumps(r.json(), indent=2)}")
    assert r.status_code == 200, f"baseline overwrite failed: {r.status_code} {r.text}"
    base_body = r.json()
    assert base_body.get("ok") is True
    assert base_body.get("mode") == "overwrite"
    BASELINE = int(base_body.get("rows_synced", -1))
    assert BASELINE >= 0, f"unexpected BASELINE rows_synced: {BASELINE}"
    print(f"     BASELINE rows_synced = {BASELINE}")

    # Test 1: append once → rows_synced == 0
    print("\n[2/4] TEST 1 — POST /sheets/sync-from-master {overwrite:false} (1st)")
    r = s.post(f"{BASE}/sheets/sync-from-master", headers=h,
               json={"overwrite": False}, timeout=120)
    print(f"     status={r.status_code}")
    body1 = r.json()
    print(f"     body={json.dumps(body1, indent=2)}")
    assert r.status_code == 200, f"append #1 failed: {r.status_code}"
    assert body1.get("mode") == "append", f"expected mode=append, got {body1.get('mode')!r}"
    rs1 = int(body1.get("rows_synced", -1))
    print(f"     rows_synced = {rs1}  (expected 0)")
    test1_pass = (rs1 == 0)

    # Test 2: append again → rows_synced == 0
    print("\n[3/4] TEST 2 — POST /sheets/sync-from-master {overwrite:false} (2nd, idempotency)")
    r = s.post(f"{BASE}/sheets/sync-from-master", headers=h,
               json={"overwrite": False}, timeout=120)
    print(f"     status={r.status_code}")
    body2 = r.json()
    print(f"     body={json.dumps(body2, indent=2)}")
    assert r.status_code == 200
    assert body2.get("mode") == "append"
    rs2 = int(body2.get("rows_synced", -1))
    print(f"     rows_synced = {rs2}  (expected 0)")
    test2_pass = (rs2 == 0)

    # Test 3: overwrite again → rows_synced == BASELINE
    print("\n[4/4] TEST 3 — POST /sheets/sync-from-master {overwrite:true} (restore)")
    r = s.post(f"{BASE}/sheets/sync-from-master", headers=h,
               json={"overwrite": True}, timeout=120)
    print(f"     status={r.status_code}")
    body3 = r.json()
    print(f"     body={json.dumps(body3, indent=2)}")
    assert r.status_code == 200
    assert body3.get("mode") == "overwrite"
    rs3 = int(body3.get("rows_synced", -1))
    print(f"     rows_synced = {rs3}  (expected == BASELINE = {BASELINE})")
    test3_pass = (rs3 == BASELINE)

    # Final report
    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    print(f"  BASELINE (overwrite #1): rows_synced={BASELINE}")
    print(f"  Test 1 (append #1):      rows_synced={rs1}  expected=0   "
          f"{'PASS' if test1_pass else 'FAIL'}")
    print(f"  Test 2 (append #2):      rows_synced={rs2}  expected=0   "
          f"{'PASS' if test2_pass else 'FAIL'}")
    print(f"  Test 3 (overwrite #2):   rows_synced={rs3}  expected={BASELINE} "
          f"{'PASS' if test3_pass else 'FAIL'}")

    overall = test1_pass and test2_pass and test3_pass
    print("\nOVERALL:", "PASS" if overall else "FAIL")
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
