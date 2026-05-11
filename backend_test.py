"""Phase F3.8.1 — Webhook Mapping Auto-Suggest Deduplication test.

Tests POST /api/me/webhooks/{wh_id}/preview to ensure suggest_mapping()
does NOT emit duplicate target fields for customer_name / address etc.
"""
from __future__ import annotations

import sys
from collections import Counter
from typing import Any, Dict, List

import requests

BASE_URL = "https://logistics-hub-740.preview.emergentagent.com/api"
ADMIN_EMAIL = "admin@test.com"
ADMIN_PASSWORD = "Admin@12345"


def login() -> str:
    r = requests.post(
        f"{BASE_URL}/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["token"]


def create_webhook(token: str) -> str:
    r = requests.post(
        f"{BASE_URL}/me/webhooks",
        json={"name": "Test Dedup", "event_type": "new_order"},
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    r.raise_for_status()
    wh = r.json()
    print(f"  created webhook id={wh['id']}, url={wh.get('url','?')[:80]}")
    return wh["id"]


def delete_webhook(token: str, wh_id: str) -> None:
    r = requests.delete(
        f"{BASE_URL}/me/webhooks/{wh_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    r.raise_for_status()
    print(f"  deleted webhook id={wh_id}")


def call_preview(token: str, wh_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    r = requests.post(
        f"{BASE_URL}/me/webhooks/{wh_id}/preview",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def target_counts(suggested: Dict[str, str]) -> Counter:
    """Returns Counter of target_field -> count, ignoring blank values."""
    return Counter(v for v in suggested.values() if v)


def fmt_suggested(suggested: Dict[str, str]) -> str:
    return "\n      ".join(f"{k!r} -> {v!r}" for k, v in suggested.items())


def assert_scenario(label: str, suggested: Dict[str, str], expectations: Dict[str, int],
                    max_allowed: int = 2) -> bool:
    counts = target_counts(suggested)
    ok = True
    failures: List[str] = []
    for field, expected in expectations.items():
        actual = counts.get(field, 0)
        if actual != expected:
            ok = False
            failures.append(
                f"    FAIL: '{field}' expected {expected}, got {actual}"
            )
    for field, c in counts.items():
        if c > max_allowed:
            ok = False
            failures.append(
                f"    FAIL: target '{field}' appears {c} times (>{max_allowed})"
            )
    print(f"\n  [{label}]")
    print(f"    suggested mapping:\n      {fmt_suggested(suggested)}")
    print(f"    counts: {dict(counts)}")
    if failures:
        for f in failures:
            print(f)
        print(f"    => {label}: FAIL")
    else:
        print(f"    => {label}: PASS")
    return ok


def main() -> int:
    print("== Phase F3.8.1 webhook mapping dedup test ==")
    print(f"Base URL: {BASE_URL}")
    token = login()
    print(f"Logged in as {ADMIN_EMAIL}")
    wh_id = create_webhook(token)
    results: Dict[str, bool] = {}

    try:
        dukaan = {
            "order": {
                "uuid": "ORD-001",
                "buyer": {
                    "first_name": "John",
                    "last_name": "Doe",
                    "phone": "9999999999",
                    "email": "j@d.com",
                },
                "shipping_address": {
                    "address_1": "Flat 12",
                    "address_2": "Sec 9",
                    "city": "Mumbai",
                    "state": "MH",
                    "pincode": "400001",
                },
                "total_cost": 1200,
            }
        }
        resp2 = call_preview(token, wh_id, dukaan)
        print(f"\nScenario 2 keys: {resp2.get('keys')}")
        results["S2-Dukaan"] = assert_scenario(
            "Scenario 2 (Dukaan)",
            resp2.get("suggested", {}),
            {
                "customer_name":  2,
                "address":        1,
                "customer_phone": 1,
                "customer_email": 1,
                "city":           1,
                "state":          1,
                "pincode":        1,
                "amount":         1,
                "order_id":       1,
            },
            max_allowed=2,
        )

        shopify = {
            "customer": {
                "full_name":  "Jane Smith",
                "first_name": "Jane",
                "last_name":  "Smith",
                "phone":      "888",
                "email":      "j@s.com",
            },
            "shipping_address": {
                "address1": "L1",
                "address2": "L2",
                "line1":    "L1",
                "city":     "Delhi",
                "province": "DL",
                "zip":      "110001",
            },
            "total_price": 500,
        }
        resp3 = call_preview(token, wh_id, shopify)
        print(f"\nScenario 3 keys: {resp3.get('keys')}")
        results["S3-Shopify"] = assert_scenario(
            "Scenario 3 (Shopify w/ full_name + fragments)",
            resp3.get("suggested", {}),
            {
                "customer_name":  1,
                "address":        1,
                "customer_phone": 1,
                "customer_email": 1,
                "city":           1,
                "state":          1,
                "pincode":        1,
                "amount":         1,
            },
            max_allowed=1,
        )

        bare = {
            "first_name": "Bob",
            "last_name":  "Lee",
            "phone":      "777",
            "email":      "b@l.com",
            "address1":   "Plot 5",
            "city":       "Pune",
            "state":      "MH",
            "pincode":    "411001",
        }
        resp4 = call_preview(token, wh_id, bare)
        print(f"\nScenario 4 keys: {resp4.get('keys')}")
        results["S4-Bare"] = assert_scenario(
            "Scenario 4 (Bare leaf fragments)",
            resp4.get("suggested", {}),
            {
                "customer_name":  2,
                "address":        1,
                "customer_phone": 1,
                "customer_email": 1,
                "city":           1,
                "state":          1,
                "pincode":        1,
            },
            max_allowed=2,
        )
    finally:
        try:
            delete_webhook(token, wh_id)
            results["S5-Cleanup"] = True
        except Exception as e:
            print(f"  cleanup failed: {e}")
            results["S5-Cleanup"] = False

    print("\n========== SUMMARY ==========")
    for label, ok in results.items():
        print(f"  {'PASS' if ok else 'FAIL'}: {label}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
