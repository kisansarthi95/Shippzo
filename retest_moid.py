#!/usr/bin/env python3
"""
Retest Phase-7d Master Order ID fixes - Test 2 and Test 4 only.
"""
import os
import re
import sys
import json
import requests

BASE = "https://logistics-hub-740.preview.emergentagent.com/api"
ADMIN_EMAIL = "admin@test.com"
ADMIN_PASS = "Admin@12345"

results = []

def log(label, passed, detail=""):
    mark = "PASS" if passed else "FAIL"
    print(f"[{mark}] {label} :: {detail}")
    results.append((label, passed, detail))


def login(email, password):
    r = requests.post(f"{BASE}/auth/login", json={"email": email, "password": password}, timeout=30)
    r.raise_for_status()
    return r.json()["token"]


def main():
    token = login(ADMIN_EMAIL, ADMIN_PASS)
    H = {"Authorization": f"Bearer {token}"}
    print(f"Logged in as {ADMIN_EMAIL}")

    cleanup_ids = []

    # ============ Test 2 ============
    print("\n=== TEST 2: User-supplied Order ID NOT overwritten ===")
    # Step 1: Ensure auto-gen ON
    r = requests.put(f"{BASE}/settings", json={"order_id_auto_generate": True}, headers=H, timeout=30)
    log("T2 PUT /settings {auto_generate: true} -> 200", r.status_code == 200,
        f"status={r.status_code} body={r.text[:200]}")
    if r.status_code == 200:
        body = r.json()
        log("T2 settings.order_id_auto_generate == true",
            body.get("order_id_auto_generate") is True,
            f"got={body.get('order_id_auto_generate')}")

    # Step 2: POST /smart-paste with user-supplied ORDER_ID
    paste_text = (
        "NAME: Order ID Test\n"
        "PHONE: 9222222222\n"
        "ADDRESS_1: addr\n"
        "CITY: Ahmedabad\n"
        "STATE: Gujarat\n"
        "PINCODE: 380001\n"
        "AMOUNT: 200\n"
        "PAYMENT: COD\n"
        "WEIGHT: 500\n"
        "ORDER_ID: ABC-001"
    )
    r = requests.post(f"{BASE}/smart-paste", json={"text": paste_text, "skip_llm": True},
                      headers=H, timeout=60)
    log("T2 POST /smart-paste -> 200", r.status_code == 200,
        f"status={r.status_code} body={r.text[:300]}")
    if r.status_code == 200:
        body = r.json()
        cleanup_ids.append(body.get("id"))
        moid = body.get("master_order_id", "")
        oid = body.get("order_id", "")
        print(f"   master_order_id = {moid!r}")
        print(f"   order_id        = {oid!r}")

        # master_order_id should NOT equal "ABC-001" - should be YYMMDD+seq
        log("T2 master_order_id != 'ABC-001'",
            moid != "ABC-001",
            f"moid={moid!r}")
        # MOID should match YYMMDD + sequence pattern
        log("T2 master_order_id matches YYMMDD+digits format",
            bool(re.match(r"^\d{6}\d{1,}$", moid)) and len(moid) >= 7,
            f"moid={moid!r}")
        # order_id should be exactly "ABC-001"
        log("T2 order_id == 'ABC-001' (user value preserved)",
            oid == "ABC-001",
            f"order_id={oid!r}")

    # ============ Test 4 ============
    print("\n=== TEST 4: Auto-gen OFF blocks save when no order_id ===")
    # Step 1: PUT auto=false
    r = requests.put(f"{BASE}/settings", json={"order_id_auto_generate": False}, headers=H, timeout=30)
    log("T4 PUT /settings {auto_generate: false} -> 200", r.status_code == 200,
        f"status={r.status_code} body={r.text[:200]}")
    if r.status_code == 200:
        body = r.json()
        log("T4 settings.order_id_auto_generate == false",
            body.get("order_id_auto_generate") is False,
            f"got={body.get('order_id_auto_generate')}")

    # Step 2: POST without ORDER_ID -> expect 422
    paste_no_oid = (
        "NAME: No Order Test\n"
        "PHONE: 9333333333\n"
        "ADDRESS_1: addr\n"
        "CITY: Ahmedabad\n"
        "STATE: Gujarat\n"
        "PINCODE: 380001\n"
        "AMOUNT: 200\n"
        "PAYMENT: COD\n"
        "WEIGHT: 500"
    )
    r = requests.post(f"{BASE}/smart-paste", json={"text": paste_no_oid, "skip_llm": True},
                      headers=H, timeout=60)
    log("T4 POST without ORDER_ID -> 422",
        r.status_code == 422,
        f"status={r.status_code} body={r.text[:300]}")
    if r.status_code == 422:
        try:
            detail = r.json().get("detail", "")
            detail_str = str(detail)
            log("T4 422 detail mentions 'Order ID is required'",
                "Order ID is required" in detail_str,
                f"detail={detail_str[:200]}")
        except Exception as e:
            log("T4 422 detail mentions 'Order ID is required'", False, f"parse error: {e}")
    elif r.status_code == 200:
        # if it accidentally succeeded, capture for cleanup
        try:
            cleanup_ids.append(r.json().get("id"))
        except Exception:
            pass

    # Step 3: POST with ORDER_ID: MY-555 -> expect 200
    paste_with_oid = (
        "NAME: With Order Test\n"
        "PHONE: 9444444444\n"
        "ADDRESS_1: addr\n"
        "CITY: Ahmedabad\n"
        "STATE: Gujarat\n"
        "PINCODE: 380001\n"
        "AMOUNT: 200\n"
        "PAYMENT: COD\n"
        "WEIGHT: 500\n"
        "ORDER_ID: MY-555"
    )
    r = requests.post(f"{BASE}/smart-paste", json={"text": paste_with_oid, "skip_llm": True},
                      headers=H, timeout=60)
    log("T4 POST with ORDER_ID -> 200", r.status_code == 200,
        f"status={r.status_code} body={r.text[:300]}")
    if r.status_code == 200:
        body = r.json()
        cleanup_ids.append(body.get("id"))
        moid = body.get("master_order_id", "")
        oid = body.get("order_id", "")
        print(f"   master_order_id = {moid!r}")
        print(f"   order_id        = {oid!r}")
        log("T4 master_order_id == '' (empty when auto-gen off)",
            moid == "",
            f"moid={moid!r}")
        log("T4 order_id == 'MY-555'",
            oid == "MY-555",
            f"order_id={oid!r}")

    # Step 4: Reset PUT auto=true
    r = requests.put(f"{BASE}/settings", json={"order_id_auto_generate": True}, headers=H, timeout=30)
    log("T4 reset PUT /settings {auto_generate: true} -> 200", r.status_code == 200,
        f"status={r.status_code} body={r.text[:200]}")

    # ============ Cleanup ============
    print("\n=== CLEANUP ===")
    for pid in cleanup_ids:
        if not pid:
            continue
        try:
            r = requests.delete(f"{BASE}/orders/pending/{pid}", headers=H, timeout=30)
            print(f"   DELETE pending/{pid} -> {r.status_code}")
        except Exception as e:
            print(f"   DELETE pending/{pid} failed: {e}")

    # Try to also delete any other test pending orders left from previous run
    try:
        r = requests.get(f"{BASE}/orders/pending", headers=H, timeout=30)
        if r.status_code == 200:
            pendings = r.json()
            print(f"\nRemaining pending orders: {len(pendings)}")
            for p in pendings:
                name = p.get("customer_name", "")
                if name in ("Order ID Test", "No Order Test", "With Order Test"):
                    pid = p.get("id")
                    rd = requests.delete(f"{BASE}/orders/pending/{pid}", headers=H, timeout=30)
                    print(f"   Auto-cleanup DELETE pending/{pid} ({name}) -> {rd.status_code}")
    except Exception as e:
        print(f"   Cleanup scan failed: {e}")

    # ============ Summary ============
    print("\n=== SUMMARY ===")
    passed = sum(1 for _, p, _ in results if p)
    total = len(results)
    print(f"{passed}/{total} assertions passed")
    fails = [(l, d) for l, p, d in results if not p]
    if fails:
        print("\nFAILED:")
        for label, detail in fails:
            print(f"  - {label}")
            print(f"    {detail}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
