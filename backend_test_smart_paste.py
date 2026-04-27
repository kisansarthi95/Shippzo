"""Smart Paste address-completeness + pincode validation tests.

Targets review request:
  Test 1 — Address completeness on the user's exact failing case
  Test 2 — Pincode mismatch warning (state mismatch)
  Test 3 — Pincode match (no false-positive warning)
  Test 4 — Bad pincode (unresolvable)

Base URL uses the public preview URL (via EXPO_PUBLIC_BACKEND_URL) since
per environment rules we must not hardcode localhost. The backend reached
is the same container as localhost:8001.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict

import requests

BASE = os.environ.get(
    "BACKEND_BASE",
    "https://logistics-hub-740.preview.emergentagent.com",
).rstrip("/")
API = f"{BASE}/api"

ADMIN_EMAIL = "admin@test.com"
ADMIN_PW = "Admin@12345"


def login() -> str:
    r = requests.post(
        f"{API}/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PW},
        timeout=30,
    )
    r.raise_for_status()
    tok = r.json().get("token")
    assert tok, f"No token in login response: {r.text}"
    return tok


def check_dup(token: str, text: str) -> Dict[str, Any]:
    r = requests.post(
        f"{API}/smart-paste/check-duplicate",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": text},
        timeout=90,
    )
    assert r.status_code == 200, f"HTTP {r.status_code} body={r.text}"
    return r.json()


RESULTS: list = []


def record(name: str, ok: bool, detail: str = ""):
    RESULTS.append((name, ok, detail))
    flag = "PASS" if ok else "FAIL"
    print(f"  [{flag}] {name}{' — ' + detail if detail else ''}")


def contains_ci(hay: str, needle: str) -> bool:
    return needle.lower() in (hay or "").lower()


def test_1(tok: str):
    print("\n=== TEST 1 — Address completeness on user's failing case ===")
    text = (
        "💰 Payment:\n"
        "Prepaid ₹1750\n\n"
        "To Ahmedabad 380015\n\n"
        "GREY GENTS\n"
        "7575848410 / 7777978550\n\n"
        "Shipping Address:\n"
        "20 \"Dev Atelier\", Nr RK Enterprise, Hiran Circle, "
        "Ramdevnagar Road, Prahladnagar, Ahmedabad, 380015 Gujarat\n\n"
        "તમારો ઓર્ડર:\n3 Kg Natural Honey"
    )
    resp = check_dup(tok, text)
    fields = resp.get("fields", {}) or {}
    warnings = resp.get("warnings", []) or []
    print("  --- fields ---")
    print(json.dumps(fields, indent=2, ensure_ascii=False))
    print(f"  --- warnings ({len(warnings)}) ---")
    for w in warnings:
        print(f"    • {w}")

    a1 = fields.get("address_line1") or ""
    a2 = fields.get("address_line2") or ""
    print(f"\n  >>> address_line1 = {a1!r}")
    print(f"  >>> address_line2 = {a2!r}")

    record("T1 customer_name == 'GREY GENTS'",
           (fields.get("customer_name") or "").strip().upper() == "GREY GENTS",
           f"got={fields.get('customer_name')!r}")
    record("T1 customer_phone == '7575848410'",
           fields.get("customer_phone") == "7575848410",
           f"got={fields.get('customer_phone')!r}")
    record("T1 customer_alt_phone == '7777978550'",
           fields.get("customer_alt_phone") == "7777978550",
           f"got={fields.get('customer_alt_phone')!r}")
    record("T1 city == 'Ahmedabad'",
           (fields.get("city") or "").strip().lower() == "ahmedabad",
           f"got={fields.get('city')!r}")
    record("T1 pincode == '380015'",
           str(fields.get("pincode") or "").strip() == "380015",
           f"got={fields.get('pincode')!r}")
    record("T1 state == 'Gujarat'",
           (fields.get("state") or "").strip().lower() == "gujarat",
           f"got={fields.get('state')!r}")

    amt = fields.get("amount")
    amt_ok = False
    try:
        amt_ok = float(amt) == 1750.0
    except Exception:
        amt_ok = False
    record("T1 amount == 1750", amt_ok, f"got={amt!r}")

    items = (fields.get("items") or "")
    record("T1 items mentions Honey",
           contains_ci(items, "honey"),
           f"got={items!r}")

    # KEY assertion: every address fragment must be present
    combined = f"{a1}\n{a2}"
    fragments = [
        "20", "Dev Atelier", "Nr RK Enterprise",
        "Hiran Circle", "Ramdevnagar Road", "Prahladnagar",
    ]
    all_found = True
    missing_frags = []
    for frag in fragments:
        if not contains_ci(combined, frag):
            all_found = False
            missing_frags.append(frag)
    record(
        "T1 KEY: all address fragments present in a1+a2",
        all_found,
        f"missing={missing_frags}" if missing_frags else "",
    )

    record("T1 warnings field is a list",
           isinstance(resp.get("warnings"), list))


def test_2(tok: str):
    print("\n=== TEST 2 — Pincode mismatch (Mumbai address, 380015 pincode) ===")
    text = (
        "Ramesh Patel\n9999991111\n"
        "123 Park Lane, Andheri West, Mumbai, 380015 Gujarat\n"
        "Items: Phone case ₹500"
    )
    resp = check_dup(tok, text)
    warnings = resp.get("warnings", []) or []
    print(f"  --- warnings ({len(warnings)}) ---")
    for w in warnings:
        print(f"    • {w}")
    fields = resp.get("fields", {}) or {}
    print(f"  city={fields.get('city')!r} state={fields.get('state')!r} pincode={fields.get('pincode')!r}")

    has_pincode_warning = any(
        isinstance(w, str) and ("pincode" in w.lower() and "380015" in w)
        for w in warnings
    )
    record("T2 warnings contain mention of 'Pincode' + '380015'",
           has_pincode_warning,
           f"warnings={warnings}")


def test_3(tok: str):
    print("\n=== TEST 3 — Pincode match (Ahmedabad 380015 — no false positive) ===")
    text = (
        "Test Customer\n9999992222\n"
        "Tagore Road, Vejalpur, Ahmedabad, 380015 Gujarat\n"
        "Items: book ₹100"
    )
    resp = check_dup(tok, text)
    warnings = resp.get("warnings", []) or []
    print(f"  --- warnings ({len(warnings)}) ---")
    for w in warnings:
        print(f"    • {w}")
    fields = resp.get("fields", {}) or {}
    print(f"  city={fields.get('city')!r} state={fields.get('state')!r} pincode={fields.get('pincode')!r}")

    has_pincode_warning = any(
        isinstance(w, str) and "pincode" in w.lower()
        for w in warnings
    )
    record("T3 no 'Pincode' warning when city=Ahmedabad matches 380015",
           not has_pincode_warning,
           f"unexpected pincode warnings: {[w for w in warnings if 'pincode' in (w or '').lower()]}")


def test_4(tok: str):
    print("\n=== TEST 4 — Unresolvable pincode (999999) ===")
    text = (
        "X\n9999993333\n"
        "Some Street, Mumbai, 999999 Maharashtra\n"
        "Items: book ₹100"
    )
    try:
        resp = check_dup(tok, text)
    except AssertionError as e:
        record("T4 HTTP 200 — no crash", False, str(e))
        return
    warnings = resp.get("warnings", []) or []
    fields = resp.get("fields", {}) or {}
    print(f"  --- warnings ({len(warnings)}) ---")
    for w in warnings:
        print(f"    • {w}")
    print(f"  pincode={fields.get('pincode')!r}")
    record("T4 HTTP 200 (no crash)", True)
    # Pincode validation should silently pass
    pincode_warns = [w for w in warnings if isinstance(w, str) and "pincode" in w.lower() and "999999" in w]
    record("T4 no 'Pincode 999999 belongs to…' warning (silent)",
           len(pincode_warns) == 0,
           f"unexpected: {pincode_warns}")


def main():
    print(f"BASE = {BASE}")
    tok = login()
    print("Login OK")
    test_1(tok)
    test_2(tok)
    test_3(tok)
    test_4(tok)

    print("\n=== SUMMARY ===")
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    for name, ok, detail in RESULTS:
        flag = "PASS" if ok else "FAIL"
        print(f"  {flag}  {name}" + (f" :: {detail}" if detail and not ok else ""))
    print(f"\n{passed}/{total} passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
