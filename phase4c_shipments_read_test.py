"""Phase-4c regression tests — Read-only Shipments + Lookup endpoints
relocated to /app/backend/routers/shipments_read.py.

Tests 15 shipments/lookup assertions + 8 smoke regression tests.
"""
import json
import os
import sys
import time

import requests

BASE = os.environ.get(
    "TEST_BASE", "https://logistics-hub-740.preview.emergentagent.com/api"
)
EMAIL = "admin@test.com"
PASSWORD = "Admin@12345"

results = []  # (label, ok, detail)


def record(label, ok, detail=""):
    results.append((label, ok, detail))
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {label} {('— ' + detail) if detail else ''}")


def login():
    r = requests.post(
        f"{BASE}/auth/login",
        json={"email": EMAIL, "password": PASSWORD},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    return data["token"]


def main():
    token = login()
    H = {"Authorization": f"Bearer {token}"}

    # ===== TEST 1: GET /api/shipments =====
    r = requests.get(f"{BASE}/shipments", headers=H, timeout=30)
    record(
        "T1: GET /shipments → 200",
        r.status_code == 200,
        f"status={r.status_code}",
    )
    if r.status_code == 200:
        data = r.json()
        record(
            "T1: response is array",
            isinstance(data, list),
            f"type={type(data).__name__} len={len(data) if isinstance(data, list) else 'n/a'}",
        )

    # ===== TEST 2: GET /shipments?status=Delivered =====
    r = requests.get(
        f"{BASE}/shipments", headers=H, params={"status": "Delivered"}, timeout=30
    )
    record(
        "T2: GET /shipments?status=Delivered → 200",
        r.status_code == 200,
        f"status={r.status_code}",
    )
    if r.status_code == 200:
        data = r.json()
        all_match = all(d.get("status") == "Delivered" for d in data) if data else True
        record(
            "T2: all returned have status=Delivered",
            all_match,
            f"count={len(data)} all_match={all_match}",
        )

    # ===== TEST 3: GET /shipments?search=test&limit=5 =====
    r = requests.get(
        f"{BASE}/shipments",
        headers=H,
        params={"search": "test", "limit": 5},
        timeout=30,
    )
    record(
        "T3: GET /shipments?search=test&limit=5 → 200",
        r.status_code == 200,
        f"status={r.status_code}",
    )
    if r.status_code == 200:
        data = r.json()
        record(
            "T3: response is array, len ≤ 5",
            isinstance(data, list) and len(data) <= 5,
            f"len={len(data)}",
        )

    # ===== TEST 4: GET /shipments/stats =====
    r = requests.get(f"{BASE}/shipments/stats", headers=H, timeout=30)
    record(
        "T4: GET /shipments/stats → 200",
        r.status_code == 200,
        f"status={r.status_code}",
    )
    expected_keys = {
        "total", "delivered", "pending", "dispatch", "shipped",
        "cod_total", "cod_count", "prepaid_total", "prepaid_count",
        "revenue_total",
    }
    if r.status_code == 200:
        data = r.json()
        missing = expected_keys - set(data.keys())
        record(
            "T4: response has all expected keys",
            not missing,
            f"missing={missing}" if missing else "all present",
        )
        all_numeric = all(
            isinstance(data.get(k), (int, float)) for k in expected_keys
        )
        record(
            "T4: all values numeric",
            all_numeric,
            f"values={ {k: data.get(k) for k in expected_keys} }",
        )

    # ===== TEST 5: GET /shipments/export/csv =====
    r = requests.get(f"{BASE}/shipments/export/csv", headers=H, timeout=30)
    record(
        "T5: GET /shipments/export/csv → 200",
        r.status_code == 200,
        f"status={r.status_code}",
    )
    if r.status_code == 200:
        ct = r.headers.get("content-type", "")
        record(
            "T5: Content-Type starts with text/csv",
            ct.startswith("text/csv"),
            f"content-type={ct}",
        )
        body = r.text
        header_ok = (
            "Tracking ID" in body and "Courier" in body
            and "Order ID" in body and "Customer" in body and "Phone" in body
        )
        record(
            "T5: CSV header row present",
            header_ok,
            f"first_60={body[:60]!r}",
        )

    # ===== TEST 6: GET /shipments/by-tracking/NONEXISTENT_TRACKING_99999 =====
    r = requests.get(
        f"{BASE}/shipments/by-tracking/NONEXISTENT_TRACKING_99999",
        headers=H, timeout=30,
    )
    record(
        "T6: GET /shipments/by-tracking/NONEXISTENT → 404",
        r.status_code == 404,
        f"status={r.status_code}",
    )

    # ===== TEST 7: POST /shipments/bulk-fetch with empty ids =====
    r = requests.post(
        f"{BASE}/shipments/bulk-fetch", headers=H, json={"ids": []}, timeout=30
    )
    record(
        "T7: POST /shipments/bulk-fetch (empty ids) → 200",
        r.status_code == 200,
        f"status={r.status_code}",
    )
    if r.status_code == 200:
        data = r.json()
        record(
            "T7: returns empty array",
            isinstance(data, list) and len(data) == 0,
            f"data={data!r}",
        )

    # ===== TEST 8: POST /shipments/bulk-fetch with nonexistent id =====
    r = requests.post(
        f"{BASE}/shipments/bulk-fetch",
        headers=H,
        json={"ids": ["nonexistent-id-12345"]},
        timeout=30,
    )
    record(
        "T8: POST /shipments/bulk-fetch (nonexistent id) → 200",
        r.status_code == 200,
        f"status={r.status_code}",
    )
    if r.status_code == 200:
        data = r.json()
        record(
            "T8: returns empty array",
            isinstance(data, list) and len(data) == 0,
            f"data={data!r}",
        )

    # ===== TEST 9: GET /customers/by-phone/9999999999 =====
    r = requests.get(f"{BASE}/customers/by-phone/9999999999", headers=H, timeout=30)
    record(
        "T9: GET /customers/by-phone/9999999999 → 200",
        r.status_code == 200,
        f"status={r.status_code}",
    )
    if r.status_code == 200:
        data = r.json()
        keys_ok = (
            "found" in data and isinstance(data["found"], bool)
            and "customer" in data
            and "count" in data and isinstance(data["count"], int)
        )
        record(
            "T9: response has found/customer/count",
            keys_ok,
            f"data={data!r}",
        )

    # ===== TEST 10: GET /customers/by-phone/123 (less than 10 digits) =====
    r = requests.get(f"{BASE}/customers/by-phone/123", headers=H, timeout=30)
    record(
        "T10: GET /customers/by-phone/123 → 200",
        r.status_code == 200,
        f"status={r.status_code}",
    )
    if r.status_code == 200:
        data = r.json()
        ok = (
            data.get("found") is False and data.get("customer") is None
            and data.get("count") == 0
        )
        record(
            "T10: {found: false, customer: null, count: 0}",
            ok,
            f"data={data!r}",
        )

    # ===== TEST 11: GET /lookup/by-city?q=Surat =====
    r = requests.get(
        f"{BASE}/lookup/by-city", headers=H, params={"q": "Surat"}, timeout=30
    )
    record(
        "T11: GET /lookup/by-city?q=Surat → 200",
        r.status_code == 200,
        f"status={r.status_code}",
    )
    if r.status_code == 200:
        data = r.json()
        keys_ok = all(
            k in data for k in ("ok", "city", "state", "suggestions", "count")
        )
        record(
            "T11: response has ok/city/state/suggestions/count",
            keys_ok,
            f"keys={list(data.keys())}",
        )
        # State should be Gujarat if data is loaded; not strictly required
        state = data.get("state", "")
        if state:
            record(
                "T11: state for Surat is Gujarat (data loaded)",
                state == "Gujarat",
                f"state={state!r}",
            )
        else:
            print(f"[INFO] T11: Surat returned empty state (India Post data may not be loaded). Response: {json.dumps(data)[:200]}")

    # ===== TEST 12: GET /lookup/by-city?q=Su (less than 3 chars) =====
    r = requests.get(
        f"{BASE}/lookup/by-city", headers=H, params={"q": "Su"}, timeout=30
    )
    record(
        "T12: GET /lookup/by-city?q=Su → 200",
        r.status_code == 200,
        f"status={r.status_code}",
    )
    if r.status_code == 200:
        data = r.json()
        ok = (
            data.get("ok") is True and data.get("suggestions") == []
            and data.get("count") == 0
        )
        record(
            "T12: {ok: true, suggestions: [], count: 0}",
            ok,
            f"data={data!r}",
        )

    # ===== TEST 13: GET /lookup/by-pincode/395003 =====
    r = requests.get(f"{BASE}/lookup/by-pincode/395003", headers=H, timeout=30)
    record(
        "T13: GET /lookup/by-pincode/395003 → 200 OR 404",
        r.status_code in (200, 404),
        f"status={r.status_code}",
    )
    if r.status_code == 200:
        data = r.json()
        record(
            "T13: response includes state info",
            "state" in data,
            f"data={data!r}",
        )

    # ===== TEST 14: GET /lookup/by-pincode/INVALID =====
    r = requests.get(f"{BASE}/lookup/by-pincode/INVALID", headers=H, timeout=30)
    record(
        "T14: GET /lookup/by-pincode/INVALID → 404",
        r.status_code == 404,
        f"status={r.status_code}",
    )

    # ===== TEST 15: GET /shipments/nonexistent-id-99 =====
    r = requests.get(f"{BASE}/shipments/nonexistent-id-99", headers=H, timeout=30)
    record(
        "T15: GET /shipments/nonexistent-id-99 → 404",
        r.status_code == 404,
        f"status={r.status_code}",
    )

    # ===== SMOKE REGRESSION TESTS =====
    print("\n--- Smoke regression on previously-extracted routers ---")
    smoke_endpoints = [
        ("/wallet", "S1"),
        ("/plans", "S2"),
        ("/smart-paste/default-prompt", "S3"),
        ("/orders/master-id-counter", "S4"),
        ("/couriers", "S5"),
        ("/admin/coupons", "S6"),
        ("/me/feature-flags", "S7"),
        ("/me/custom-fields", "S8"),
    ]
    for path, label in smoke_endpoints:
        r = requests.get(f"{BASE}{path}", headers=H, timeout=30)
        record(
            f"{label}: GET {path} → 200",
            r.status_code == 200,
            f"status={r.status_code}"
            + (f" body={r.text[:120]}" if r.status_code != 200 else ""),
        )

    # ===== Summary =====
    total = len(results)
    failed = [r for r in results if not r[1]]
    print(f"\n{'=' * 60}")
    print(f"TOTAL: {total} assertions  PASS: {total - len(failed)}  FAIL: {len(failed)}")
    if failed:
        print("\nFAILED:")
        for label, _, detail in failed:
            print(f"  - {label} | {detail}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
