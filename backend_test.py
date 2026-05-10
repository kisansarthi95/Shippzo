"""
Phase F3.4 — Smart webhook field-mapping suggestion tests.

Pure-function tests against /app/backend/import_schema.py (cases A-G) +
live API smoke for /api/me/webhooks + preview endpoint (case H).

Run with:  cd /app && python backend_test.py
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from typing import Any, Dict, List

# Make backend importable
sys.path.insert(0, "/app/backend")

import requests  # noqa: E402

from import_schema import (  # noqa: E402
    HEADER_ALIASES,
    PREFIXES_TO_STRIP,
    build_pending_doc_from_mapping,
    suggest_mapping,
)


BASE = "https://logistics-hub-740.preview.emergentagent.com/api"
EMAIL = "admin@test.com"
PASSWORD = "Admin@12345"


# ── Tiny assert harness ─────────────────────────────────────────────
PASSED: List[str] = []
FAILED: List[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASSED.append(label)
        print(f"  ✅ {label}")
    else:
        FAILED.append(f"{label} :: {detail}")
        print(f"  ❌ {label} :: {detail}")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


# ── Case A — Shopify keys: 9-key mapping ────────────────────────────
def case_a() -> None:
    section("Case A — Shopify nested keys → all 9 fields auto-mapped")
    keys = [
        "order.customer.full_name",
        "order.billing_address.zip",
        "order.billing_address.city",
        "order.billing_address.state",
        "order.billing_address.address1",
        "order.line_items",
        "order.financial_status",
        "order.total_price",
        "order.email",
    ]
    expected: Dict[str, str] = {
        "order.customer.full_name":      "customer_name",
        "order.billing_address.zip":     "pincode",
        "order.billing_address.city":    "city",
        "order.billing_address.state":   "state",
        "order.billing_address.address1": "address",
        "order.line_items":              "items",
        "order.financial_status":        "status",
        "order.total_price":             "amount",
        "order.email":                   "customer_email",
    }
    out = suggest_mapping(keys)
    print(f"  suggest_mapping output: {json.dumps(out, indent=2)}")
    for k, v in expected.items():
        check(f"A: {k} → {v}", out.get(k) == v, f"got {out.get(k)!r}")


# ── Case B — first/last fragments only ──────────────────────────────
def case_b() -> None:
    section("Case B — first_name + last_name BOTH map to customer_name")
    keys = ["order.customer.first_name", "order.customer.last_name"]
    out = suggest_mapping(keys)
    print(f"  suggest_mapping output: {json.dumps(out, indent=2)}")
    check(
        "B: first_name → customer_name",
        out.get("order.customer.first_name") == "customer_name",
        f"got {out.get('order.customer.first_name')!r}",
    )
    check(
        "B: last_name → customer_name",
        out.get("order.customer.last_name") == "customer_name",
        f"got {out.get('order.customer.last_name')!r}",
    )
    # Now test build path joins them
    row = {
        "order.customer.first_name": "Nayan",
        "order.customer.last_name":  "Bhut",
    }
    doc = build_pending_doc_from_mapping(row, out)
    print(f"  pending doc: customer_name={doc.get('customer_name')!r}")
    check(
        "B: build_pending_doc joins first+last → 'Nayan Bhut'",
        doc.get("customer_name") == "Nayan Bhut",
        f"got {doc.get('customer_name')!r}",
    )


# ── Case C — clean wins, fragments suppressed ───────────────────────
def case_c() -> None:
    section("Case C — full_name + first_name + last_name: only full_name kept")
    keys = [
        "order.customer.full_name",
        "order.customer.first_name",
        "order.customer.last_name",
    ]
    out = suggest_mapping(keys)
    print(f"  suggest_mapping output: {json.dumps(out, indent=2)}")
    check(
        "C: full_name → customer_name",
        out.get("order.customer.full_name") == "customer_name",
        f"got {out.get('order.customer.full_name')!r}",
    )
    check(
        "C: first_name SKIPPED (clean source already won)",
        out.get("order.customer.first_name") is None
        or out.get("order.customer.first_name") == "",
        f"got {out.get('order.customer.first_name')!r}",
    )
    check(
        "C: last_name SKIPPED (clean source already won)",
        out.get("order.customer.last_name") is None
        or out.get("order.customer.last_name") == "",
        f"got {out.get('order.customer.last_name')!r}",
    )


# ── Case D — multiple address parts NEVER suppressed ───────────────
def case_d() -> None:
    section("Case D — address1 + area + landmark all map to address")
    keys = [
        "order.billing_address.address1",
        "order.billing_address.area",
        "order.billing_address.landmark",
    ]
    out = suggest_mapping(keys)
    print(f"  suggest_mapping output: {json.dumps(out, indent=2)}")
    for k in keys:
        check(
            f"D: {k} → address",
            out.get(k) == "address",
            f"got {out.get(k)!r}",
        )
    # build path joins the three address columns with " "
    row = {
        "order.billing_address.address1": "Veer Bhagatsinh Marg",
        "order.billing_address.area":     "Dhan Bay Chowk",
        "order.billing_address.landmark": "Talaja",
    }
    doc = build_pending_doc_from_mapping(row, out)
    print(f"  pending doc: address_line1={doc.get('address_line1')!r}")
    check(
        "D: build_pending_doc joins all three → 'Veer Bhagatsinh Marg Dhan Bay Chowk Talaja'",
        doc.get("address_line1") == "Veer Bhagatsinh Marg Dhan Bay Chowk Talaja",
        f"got {doc.get('address_line1')!r}",
    )


# ── Case E — phone duplicates: first wins ──────────────────────────
def case_e() -> None:
    section("Case E — phone duplicates: only first wins")
    keys = [
        "order.customer.phone",
        "order.billing_address.phone",
    ]
    out = suggest_mapping(keys)
    print(f"  suggest_mapping output: {json.dumps(out, indent=2)}")
    check(
        "E: order.customer.phone → customer_phone",
        out.get("order.customer.phone") == "customer_phone",
        f"got {out.get('order.customer.phone')!r}",
    )
    second = out.get("order.billing_address.phone")
    check(
        "E: order.billing_address.phone NOT mapped (duplicate suppressed)",
        second is None or second == "",
        f"got {second!r}",
    )


# ── Case F — items normalisation regression ─────────────────────────
def case_f() -> None:
    section("Case F — items normalisation: line_items → 'Title xQty, ...'")
    mapping = {"line_items": "items"}
    row = {
        "line_items": [
            {"title": "Shoes", "quantity": 2},
            {"title": "Belt",  "quantity": 1},
        ],
    }
    doc = build_pending_doc_from_mapping(row, mapping)
    print(f"  pending doc: items={doc.get('items')!r}")
    check(
        "F: line_items pretty-formatted",
        doc.get("items") == "Shoes x2, Belt x1",
        f"got {doc.get('items')!r}",
    )


# ── Case G — saved mapping precedence ───────────────────────────────
def case_g() -> None:
    section("Case G — saved mapping wins over auto-suggest")
    saved = {"order.customer.full_name": "address"}  # deliberately wrong override
    keys = ["order.customer.full_name"]
    out = suggest_mapping(keys, saved=saved)
    print(f"  suggest_mapping output: {json.dumps(out, indent=2)}")
    check(
        "G: saved choice 'address' wins (not customer_name)",
        out.get("order.customer.full_name") == "address",
        f"got {out.get('order.customer.full_name')!r}",
    )


# ── Smoke: import_schema constants ──────────────────────────────────
def case_constants() -> None:
    section("Smoke — PREFIXES_TO_STRIP + HEADER_ALIASES integrity")
    check(
        "PREFIXES_TO_STRIP has at least 28 entries",
        len(PREFIXES_TO_STRIP) >= 28,
        f"got {len(PREFIXES_TO_STRIP)}",
    )
    # Specificity check: more-specific paths (e.g. "order.customer.")
    # must appear BEFORE the generic root ("order.") so first-match wins.
    def _idx(p: str) -> int:
        return PREFIXES_TO_STRIP.index(p) if p in PREFIXES_TO_STRIP else -1
    check(
        "PREFIXES_TO_STRIP: order.customer. before order.",
        _idx("order.customer.") < _idx("order.") and _idx("order.") >= 0,
        f"order.customer.={_idx('order.customer.')} order.={_idx('order.')}",
    )
    check(
        "PREFIXES_TO_STRIP: order.billing_address. before order.",
        _idx("order.billing_address.") < _idx("order."),
        "ordering wrong",
    )
    # Spot-check key new aliases
    expected_aliases = {
        "first_name":             "customer_name",
        "last_name":              "customer_name",
        "full_name":              "customer_name",
        "billing_address.zip":    "pincode",
        "billing_address.area":   "address",
        "billing_address.landmark": "address",
        "total_price":            "amount",
        "line_items":             "items",
        "financial_status":       "status",
        "fulfillment_status":     "status",
        "shipping_address.address1": "address",
    }
    for k, v in expected_aliases.items():
        check(
            f"alias {k} → {v}",
            HEADER_ALIASES.get(k) == v,
            f"got {HEADER_ALIASES.get(k)!r}",
        )


# ── Case H — Live API regression + preview endpoint ─────────────────
def login() -> str:
    r = requests.post(
        f"{BASE}/auth/login",
        json={"email": EMAIL, "password": PASSWORD},
        timeout=20,
    )
    r.raise_for_status()
    body = r.json()
    return body["token"]


def case_h_live(token: str) -> None:
    section("Case H — Live /api/me/webhooks + preview regression")
    h = {"Authorization": f"Bearer {token}"}
    # 1. List existing webhooks (regression)
    r = requests.get(f"{BASE}/me/webhooks", headers=h, timeout=20)
    check("H: GET /me/webhooks 200", r.status_code == 200, f"status={r.status_code}")
    if r.status_code != 200:
        return
    list_body = r.json()
    pre_count = list_body.get("count", 0)
    print(f"  Pre-test webhook count: {pre_count}")

    # 2. Create webhook (event_type=new_order, source_app=shopify)
    r = requests.post(
        f"{BASE}/me/webhooks",
        headers=h,
        json={"name": "F34 Shopify Test", "event_type": "new_order", "source_app": "shopify"},
        timeout=20,
    )
    check(
        "H: POST /me/webhooks 200",
        r.status_code == 200,
        f"status={r.status_code} body={r.text[:200]}",
    )
    if r.status_code != 200:
        return
    wh = r.json()
    wh_id = wh.get("id")
    check("H: created webhook has id", bool(wh_id), f"got {wh}")
    check("H: source_app=shopify", wh.get("source_app") == "shopify", f"got {wh.get('source_app')}")
    check("H: event_type=new_order", wh.get("event_type") == "new_order", f"got {wh.get('event_type')}")
    check("H: secret is non-empty", bool(wh.get("secret")), "no secret")

    if not wh_id:
        return

    try:
        # 3. POST preview with sample Shopify-style order JSON
        sample = {
            "order": {
                "customer": {
                    "full_name": "Nayankumar Bhut",
                    "email":     "nayan@example.com",
                    "phone":     "9998887770",
                },
                "billing_address": {
                    "first_name": "Nayankumar",
                    "last_name":  "Bhut",
                    "address1":   "20, Dev Atelier",
                    "city":       "Bhavnagar",
                    "state":      "Gujarat",
                    "zip":        "364140",
                    "phone":      "9998887770",
                },
                "line_items": [
                    {"title": "Shoes", "quantity": 2},
                    {"title": "Belt",  "quantity": 1},
                ],
                "financial_status": "paid",
                "total_price":      "1750",
                "email":            "nayan@example.com",
            },
        }
        r = requests.post(
            f"{BASE}/me/webhooks/{wh_id}/preview",
            headers=h,
            json=sample,
            timeout=20,
        )
        check("H: POST preview 200", r.status_code == 200, f"status={r.status_code} body={r.text[:300]}")
        if r.status_code == 200:
            body = r.json()
            keys = body.get("keys", [])
            suggested = body.get("suggested", {})
            print(f"  preview keys ({len(keys)}): {keys}")
            print(f"  preview suggested ({len(suggested)}): {json.dumps(suggested, indent=2)}")
            # Verify several key auto-mappings landed in `suggested`
            expected_in_preview = {
                "order.customer.full_name":      "customer_name",
                "order.billing_address.zip":     "pincode",
                "order.billing_address.city":    "city",
                "order.billing_address.state":   "state",
                "order.billing_address.address1": "address",
                "order.financial_status":        "status",
                "order.total_price":             "amount",
            }
            for k, v in expected_in_preview.items():
                check(
                    f"H: preview suggested[{k}] = {v}",
                    suggested.get(k) == v,
                    f"got {suggested.get(k)!r}",
                )
            # In live preview, payload contained BOTH order.customer.email
            # AND order.email. Per duplicate-suppression spec, the FIRST
            # match wins (customer.email), so order.email may legitimately
            # remain unmapped. Just verify customer.email got mapped.
            check(
                "H: preview suggested[order.customer.email] = customer_email",
                suggested.get("order.customer.email") == "customer_email",
                f"got {suggested.get('order.customer.email')!r}",
            )
            check(
                "H: preview suggested[order.customer.phone] = customer_phone",
                suggested.get("order.customer.phone") == "customer_phone",
                f"got {suggested.get('order.customer.phone')!r}",
            )
            # line_items either flattens or stays as list, we accept both
            li_key = "order.line_items"
            if li_key in keys:
                check(
                    f"H: preview suggested[{li_key}] = items",
                    suggested.get(li_key) == "items",
                    f"got {suggested.get(li_key)!r}",
                )

        # 4. Regression: GET /me/customers + /me/abandoned-carts (Phase F3.3)
        r = requests.get(f"{BASE}/me/customers", headers=h, timeout=20)
        check(
            "H: GET /me/customers 200 (F3.3 regression)",
            r.status_code == 200,
            f"status={r.status_code}",
        )
        r = requests.get(f"{BASE}/me/abandoned-carts", headers=h, timeout=20)
        check(
            "H: GET /me/abandoned-carts 200 (F3.3 regression)",
            r.status_code == 200,
            f"status={r.status_code}",
        )
    finally:
        # 5. Cleanup — delete webhook
        r = requests.delete(f"{BASE}/me/webhooks/{wh_id}", headers=h, timeout=20)
        check("H: DELETE webhook 200", r.status_code == 200, f"status={r.status_code}")


# ── Main ────────────────────────────────────────────────────────────
def main() -> int:
    print("=" * 72)
    print("Phase F3.4 — Smart webhook field-mapping suggestion tests")
    print("=" * 72)

    # Pure-function tests
    case_constants()
    case_a()
    case_b()
    case_c()
    case_d()
    case_e()
    case_f()
    case_g()

    # Live API
    try:
        token = login()
        case_h_live(token)
    except Exception as e:
        FAILED.append(f"H_login: {e}")
        traceback.print_exc()

    # Summary
    print("\n" + "=" * 72)
    print(f"PASSED: {len(PASSED)}    FAILED: {len(FAILED)}")
    if FAILED:
        print("\nFAILURES:")
        for f in FAILED:
            print(f"  - {f}")
    print("=" * 72)
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
