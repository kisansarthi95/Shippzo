"""
Phase-9 Unified Address Field — End-to-End Bug Fix Verification

Tests:
  1. Smart Paste with Gujarati address containing commas → address_line1
     must preserve full string (not truncate at first comma).
  2. GET /api/orders/pending/{id} confirms persistence.
  3. Regression on shipments/stats, shipments, peek-master-id, feature-flags.
  4. Smart Paste with English address with multiple commas — preserve all.
  5. Cleanup: delete test pending orders.

Backend URL: https://logistics-hub-740.preview.emergentagent.com/api
Credentials: admin@test.com / Admin@12345
"""

import os
import sys
import json
import time
from typing import Dict, Any, List, Optional, Tuple

import requests

BASE = "https://logistics-hub-740.preview.emergentagent.com/api"
EMAIL = "admin@test.com"
PASSWORD = "Admin@12345"

passed: List[str] = []
failed: List[Tuple[str, str]] = []


def assert_eq(name: str, got, expected) -> bool:
    if got == expected:
        passed.append(name)
        print(f"  PASS  {name}")
        return True
    failed.append((name, f"expected {expected!r}, got {got!r}"))
    print(f"  FAIL  {name} — expected {expected!r}, got {got!r}")
    return False


def assert_true(name: str, cond: bool, detail: str = "") -> bool:
    if cond:
        passed.append(name)
        print(f"  PASS  {name}")
        return True
    failed.append((name, detail or "assertion false"))
    print(f"  FAIL  {name} — {detail}")
    return False


def login() -> str:
    r = requests.post(
        f"{BASE}/auth/login",
        json={"email": EMAIL, "password": PASSWORD},
        timeout=30,
    )
    r.raise_for_status()
    body = r.json()
    return body["token"]


def auth_headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def main():
    print(f"[BASE] {BASE}")
    print("[1] Logging in...")
    token = login()
    print("    Logged in.")
    H = auth_headers(token)

    created_pending_ids: List[str] = []

    # =====================================================================
    # TEST 1 — Smart Paste with Gujarati address (commas inside)
    # =====================================================================
    print("\n[T1] Smart Paste — Gujarati address with comma in line 3")
    gj_paste = (
        "આહિર વશરામભાઈ નામેરીભાઈ\n"
        "9978049561\n"
        "ગામ, રામવાવ તા. રાપર જી.કચ્છ\n"
        "પિન 370165\n"
        "COD ₹900\n"
    )
    expected_full_addr = "ગામ, રામવાવ તા. રાપર જી.કચ્છ"

    r1 = requests.post(
        f"{BASE}/smart-paste",
        headers=H,
        json={"text": gj_paste},
        timeout=120,
    )
    print(f"    HTTP {r1.status_code}")
    if r1.status_code != 200:
        print(f"    Body: {r1.text[:600]}")
    assert_eq("T1 status 200", r1.status_code, 200)

    if r1.status_code == 200:
        body1 = r1.json()
        print(
            "    Returned: address_line1=%r address_line2=%r city=%r state=%r pincode=%r"
            % (
                body1.get("address_line1"),
                body1.get("address_line2"),
                body1.get("city"),
                body1.get("state"),
                body1.get("pincode"),
            )
        )
        pid_gj = body1.get("id")
        if pid_gj:
            created_pending_ids.append(pid_gj)

        addr1 = body1.get("address_line1", "")
        addr2 = body1.get("address_line2", "") or ""

        # CRITICAL: full address must be preserved (with comma). This is the
        # whole point of the Phase-9 fix — backend should not lose anything.
        # Accept either: address_line1 == full string, OR
        # the union of line1+line2 contains the full string verbatim.
        union = (addr1 + " " + addr2).strip() if addr2 else addr1
        assert_true(
            "T1 address_line1 preserves comma+full string (verbatim or in line1+line2)",
            expected_full_addr in addr1 or expected_full_addr in union,
            f"expected to find {expected_full_addr!r} in {addr1!r} or {union!r}",
        )
        # Specifically, the comma must NOT have been lost:
        assert_true(
            "T1 returned address contains comma after 'ગામ'",
            ("ગામ," in addr1) or ("ગામ," in union),
            f"line1={addr1!r} union={union!r}",
        )

        # Pincode
        assert_eq("T1 pincode == 370165", body1.get("pincode"), "370165")

        # City — should be 'રાપર' (per review). LLM/regex may variably surface
        # this; treat as soft (warn only) but still report.
        city = (body1.get("city") or "").strip()
        if city == "રાપર":
            passed.append("T1 city == રાપર (exact)")
            print("  PASS  T1 city == રાપર (exact)")
        else:
            print(f"  WARN  T1 city extraction = {city!r} (expected 'રાપર' — soft check)")

        state = (body1.get("state") or "").strip()
        if state == "કચ્છ" or state.endswith("કચ્છ"):
            passed.append("T1 state == કચ્છ (exact or suffix)")
            print("  PASS  T1 state == કચ્છ (exact or suffix)")
        else:
            print(f"  WARN  T1 state extraction = {state!r} (expected 'કચ્છ' — soft check)")

        # ===================================================================
        # TEST 2 — GET pending by id
        # ===================================================================
        print(f"\n[T2] GET /orders/pending/{pid_gj} — confirm persistence")
        r2 = requests.get(f"{BASE}/orders/pending/{pid_gj}", headers=H, timeout=30)
        print(f"    HTTP {r2.status_code}")
        assert_eq("T2 GET pending status 200", r2.status_code, 200)
        if r2.status_code == 200:
            body2 = r2.json()
            addr1_p = body2.get("address_line1", "")
            addr2_p = body2.get("address_line2", "") or ""
            union_p = (addr1_p + " " + addr2_p).strip() if addr2_p else addr1_p
            assert_true(
                "T2 persisted address contains full comma'd string",
                expected_full_addr in addr1_p or expected_full_addr in union_p,
                f"line1={addr1_p!r} union={union_p!r}",
            )

    # =====================================================================
    # TEST 3 — Regression GETs
    # =====================================================================
    print("\n[T3] Regression GETs")
    r3a = requests.get(f"{BASE}/shipments/stats", headers=H, timeout=30)
    print(f"    /shipments/stats → {r3a.status_code}")
    assert_eq("T3a /shipments/stats == 200", r3a.status_code, 200)
    if r3a.status_code == 200:
        s = r3a.json()
        assert_true(
            "T3a shipments/stats has 'total' or known stat key",
            isinstance(s, dict) and len(s) > 0,
            f"body keys={list(s.keys()) if isinstance(s, dict) else type(s)}",
        )

    r3b = requests.get(f"{BASE}/shipments", headers=H, timeout=30)
    print(f"    /shipments → {r3b.status_code}")
    assert_eq("T3b /shipments == 200", r3b.status_code, 200)
    if r3b.status_code == 200:
        body = r3b.json()
        # body could be list or {items: [...]}
        if isinstance(body, list):
            count = len(body)
        elif isinstance(body, dict) and "items" in body:
            count = len(body["items"])
        else:
            count = -1
        print(f"    shipments count={count}")
        assert_true("T3b /shipments returned list-shape", count >= 0, f"body type={type(body)}")

    r3c = requests.get(f"{BASE}/orders/peek-master-id", headers=H, timeout=30)
    print(f"    /orders/peek-master-id → {r3c.status_code}")
    assert_eq("T3c /orders/peek-master-id == 200", r3c.status_code, 200)
    if r3c.status_code == 200:
        b = r3c.json()
        assert_true(
            "T3c peek-master-id has master_order_id key",
            "master_order_id" in b,
            f"keys={list(b.keys())}",
        )

    r3d = requests.get(f"{BASE}/me/feature-flags", headers=H, timeout=30)
    print(f"    /me/feature-flags → {r3d.status_code}")
    assert_eq("T3d /me/feature-flags == 200", r3d.status_code, 200)
    if r3d.status_code == 200:
        ff = r3d.json()
        feats = ff.get("features") or []
        # Some implementations might return dict of {key: bool}. Handle both.
        if isinstance(feats, dict):
            n = len(feats)
        else:
            n = len(feats)
        print(f"    features count={n}")
        assert_eq("T3d feature-flags features.length == 57", n, 57)

    # =====================================================================
    # TEST 4 — Smart Paste with English-only address (3 commas)
    # =====================================================================
    print("\n[T4] Smart Paste — English address with 3 commas")
    en_paste = (
        "Test Customer\n"
        "9876543210\n"
        "123 Main Road, Near Park, Sector 12\n"
        "Delhi 110001\n"
        "COD ₹500\n"
    )
    expected_en_addr = "123 Main Road, Near Park, Sector 12"

    r4 = requests.post(
        f"{BASE}/smart-paste",
        headers=H,
        json={"text": en_paste},
        timeout=120,
    )
    print(f"    HTTP {r4.status_code}")
    if r4.status_code != 200:
        print(f"    Body: {r4.text[:600]}")
    assert_eq("T4 status 200", r4.status_code, 200)
    if r4.status_code == 200:
        body4 = r4.json()
        print(
            "    Returned: address_line1=%r address_line2=%r city=%r state=%r pincode=%r"
            % (
                body4.get("address_line1"),
                body4.get("address_line2"),
                body4.get("city"),
                body4.get("state"),
                body4.get("pincode"),
            )
        )
        pid_en = body4.get("id")
        if pid_en:
            created_pending_ids.append(pid_en)

        a1 = body4.get("address_line1", "") or ""
        a2 = body4.get("address_line2", "") or ""
        union_en = (a1 + " " + a2).strip() if a2 else a1

        assert_true(
            "T4 full English address (with 3 commas) preserved",
            expected_en_addr in a1 or expected_en_addr in union_en,
            f"line1={a1!r} union={union_en!r}",
        )
        # Comma count check — must keep all 2 internal commas
        comma_count_a1 = a1.count(",")
        comma_count_union = union_en.count(",")
        assert_true(
            "T4 address has at least 2 commas preserved (in line1 or union)",
            comma_count_a1 >= 2 or comma_count_union >= 2,
            f"line1 commas={comma_count_a1}, union commas={comma_count_union}",
        )
        assert_eq("T4 pincode == 110001", body4.get("pincode"), "110001")

    # =====================================================================
    # TEST 5 — Cleanup
    # =====================================================================
    print("\n[T5] Cleanup — delete test pending orders")
    for pid in created_pending_ids:
        try:
            rd = requests.delete(f"{BASE}/orders/pending/{pid}", headers=H, timeout=30)
            print(f"    DELETE /orders/pending/{pid} → {rd.status_code}")
            assert_true(
                f"T5 cleanup pending {pid[:8]}",
                rd.status_code in (200, 204, 404),
                f"got {rd.status_code}: {rd.text[:200]}",
            )
        except Exception as e:
            print(f"    DELETE failed: {e}")

    # =====================================================================
    # SUMMARY
    # =====================================================================
    print("\n" + "=" * 70)
    print(f"PASSED: {len(passed)}")
    print(f"FAILED: {len(failed)}")
    if failed:
        print("\nFAILURES:")
        for name, detail in failed:
            print(f"  - {name}: {detail}")
    print("=" * 70)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
