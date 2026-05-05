"""
Phase-3 Smart Paste enhancements verification
=============================================
Tests customer_email + customer_gstin extraction, persistence, and
regression of Phase-3 routers.

Target: https://logistics-hub-740.preview.emergentagent.com/api
Auth  : admin@test.com / Admin@12345
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

import requests

BASE_URL = "https://logistics-hub-740.preview.emergentagent.com/api"
ADMIN_EMAIL = "admin@test.com"
ADMIN_PASSWORD = "Admin@12345"

PASS: List[str] = []
FAIL: List[str] = []


def _log(tag: str, msg: str, *, ok: bool) -> None:
    marker = "✅" if ok else "❌"
    print(f"{marker} [{tag}] {msg}")
    (PASS if ok else FAIL).append(f"[{tag}] {msg}")


def ok(tag: str, msg: str) -> None:
    _log(tag, msg, ok=True)


def fail(tag: str, msg: str, resp: Optional[requests.Response] = None) -> None:
    if resp is not None:
        body = ""
        try:
            body = json.dumps(resp.json(), indent=2)[:800]
        except Exception:
            body = (resp.text or "")[:800]
        msg = f"{msg}\n  HTTP {resp.status_code}\n  body: {body}"
    _log(tag, msg, ok=False)


def assert_true(tag: str, cond: bool, msg: str, resp: Optional[requests.Response] = None) -> bool:
    if cond:
        ok(tag, msg)
        return True
    fail(tag, msg, resp)
    return False


def login() -> str:
    r = requests.post(
        f"{BASE_URL}/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=30,
    )
    if r.status_code != 200:
        print(f"LOGIN FAILED: HTTP {r.status_code} — {r.text[:400]}")
        sys.exit(2)
    tok = (r.json() or {}).get("token", "")
    if not tok:
        print(f"LOGIN missing token: {r.text[:400]}")
        sys.exit(2)
    print(f"✅ Logged in as {ADMIN_EMAIL}")
    return tok


def auth_headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# ─────────────────────────── TEST 1 ───────────────────────────
def test_1_regex_explicit_labels(tok: str) -> None:
    tag = "TEST1 regex explicit labels"
    payload = {
        "text": (
            "NAME: Test User\n"
            "PHONE: 9876543210\n"
            "ADDRESS_1: 12 Test Lane, Mumbai 400001\n"
            "EMAIL: foo@bar.com\n"
            "GST: 24ABCDE1234F1Z5"
        )
    }
    r = requests.post(
        f"{BASE_URL}/smart-paste/parse",
        headers=auth_headers(tok),
        json=payload,
        timeout=60,
    )
    if not assert_true(tag, r.status_code == 200, f"HTTP 200 (got {r.status_code})", r):
        return
    data = r.json() or {}
    fields = data.get("fields") or {}
    conf = data.get("confidence") or {}

    assert_true(
        tag,
        fields.get("customer_email") == "foo@bar.com",
        f"fields.customer_email == 'foo@bar.com' (got {fields.get('customer_email')!r})",
        r,
    )
    assert_true(
        tag,
        fields.get("customer_gstin") == "24ABCDE1234F1Z5",
        f"fields.customer_gstin == '24ABCDE1234F1Z5' (got {fields.get('customer_gstin')!r})",
        r,
    )
    assert_true(
        tag,
        conf.get("customer_email") == "high",
        f"confidence.customer_email == 'high' (got {conf.get('customer_email')!r})",
        r,
    )
    assert_true(
        tag,
        conf.get("customer_gstin") == "high",
        f"confidence.customer_gstin == 'high' (got {conf.get('customer_gstin')!r})",
        r,
    )


# ─────────────────────────── TEST 2 ───────────────────────────
def test_2_regex_opportunistic_freetext(tok: str) -> None:
    tag = "TEST2 regex opportunistic free-text"
    payload = {
        "text": (
            "Hi, please ship to John Smith, 99 Park Road, Pune 411001, "
            "contact us at sales@acme.com or our GSTIN 27AAACI1681G1ZN "
            "if needed. Cash 1500."
        )
    }
    r = requests.post(
        f"{BASE_URL}/smart-paste/parse",
        headers=auth_headers(tok),
        json=payload,
        timeout=60,
    )
    if not assert_true(tag, r.status_code == 200, f"HTTP 200 (got {r.status_code})", r):
        return
    data = r.json() or {}
    fields = data.get("fields") or {}
    conf = data.get("confidence") or {}

    em = fields.get("customer_email") or ""
    assert_true(
        tag,
        em == "sales@acme.com" or "sales@acme.com" in em,
        f"fields.customer_email contains 'sales@acme.com' (got {em!r})",
        r,
    )
    assert_true(
        tag,
        fields.get("customer_gstin") == "27AAACI1681G1ZN",
        f"fields.customer_gstin == '27AAACI1681G1ZN' (got {fields.get('customer_gstin')!r})",
        r,
    )
    assert_true(
        tag,
        conf.get("customer_email") == "high",
        f"confidence.customer_email == 'high' (got {conf.get('customer_email')!r})",
        r,
    )
    assert_true(
        tag,
        conf.get("customer_gstin") == "high",
        f"confidence.customer_gstin == 'high' (got {conf.get('customer_gstin')!r})",
        r,
    )


# ─────────────────────────── TEST 3 ───────────────────────────
def test_3_invalid_gstin(tok: str) -> None:
    tag = "TEST3 invalid GSTIN"
    payload = {"text": "GST: NOT-A-VALID-GST-NUM\nEMAIL: bad@@email"}
    r = requests.post(
        f"{BASE_URL}/smart-paste/parse",
        headers=auth_headers(tok),
        json=payload,
        timeout=60,
    )
    if not assert_true(tag, r.status_code == 200, f"HTTP 200 (got {r.status_code})", r):
        return
    data = r.json() or {}
    fields = data.get("fields") or {}
    conf = data.get("confidence") or {}
    warnings = data.get("warnings") or []

    assert_true(
        tag,
        bool(fields.get("customer_gstin")),
        f"fields.customer_gstin populated (got {fields.get('customer_gstin')!r})",
        r,
    )
    assert_true(
        tag,
        conf.get("customer_gstin") == "low",
        f"confidence.customer_gstin == 'low' (got {conf.get('customer_gstin')!r})",
        r,
    )
    warning_hit = any(
        "GSTIN doesn't match the standard 15-character format" in (w or "")
        for w in warnings
    )
    assert_true(
        tag,
        warning_hit,
        f"warnings contains standard GSTIN format msg (warnings={warnings})",
        r,
    )


# ─────────────────────────── TEST 4 ───────────────────────────
def test_4_direct_shipment_b2b(tok: str) -> None:
    tag = "TEST4 direct shipment B2B fields"

    # Get or create a courier.
    r = requests.get(f"{BASE_URL}/couriers", headers=auth_headers(tok), timeout=30)
    if not assert_true(tag, r.status_code == 200, f"GET /couriers 200 (got {r.status_code})", r):
        return
    couriers = r.json() or []
    courier_id: Optional[str] = None
    if isinstance(couriers, list) and couriers:
        courier_id = couriers[0].get("id")
    if not courier_id:
        cr = requests.post(
            f"{BASE_URL}/couriers",
            headers=auth_headers(tok),
            json={
                "name": "Phase3 Test Courier",
                "series_prefix": "P3",
                "next_number": 1,
                "number_padding": 5,
            },
            timeout=30,
        )
        if cr.status_code not in (200, 201):
            fail(tag, "Could not create courier", cr)
            return
        courier_id = (cr.json() or {}).get("id")
    assert_true(tag, bool(courier_id), f"Have courier_id={courier_id}")

    # Create shipment with B2B fields.
    ship_payload = {
        "tracking_id": f"P3TEST{int(time.time())}",
        "courier_id": courier_id,
        "customer_name": "B2B Buyer Ltd",
        "customer_phone": "9988776655",
        "customer_email": "buyer@business.com",
        "customer_gstin": "29ABCDE1234F1Z5",
        "address_line1": "Warehouse 7, Industrial Area",
        "city": "Bengaluru",
        "state": "Karnataka",
        "pincode": "560001",
        "payment_mode": "Prepaid",
        "amount": 12500,
    }
    cr = requests.post(
        f"{BASE_URL}/shipments",
        headers=auth_headers(tok),
        json=ship_payload,
        timeout=60,
    )
    if not assert_true(
        tag, cr.status_code in (200, 201), f"POST /shipments 200/201 (got {cr.status_code})", cr,
    ):
        return
    created = cr.json() or {}
    ship_id = created.get("id")
    assert_true(tag, bool(ship_id), f"created shipment has id (got {ship_id})", cr)
    assert_true(
        tag,
        created.get("customer_email") == "buyer@business.com",
        f"POST response customer_email (got {created.get('customer_email')!r})",
        cr,
    )
    assert_true(
        tag,
        created.get("customer_gstin") == "29ABCDE1234F1Z5",
        f"POST response customer_gstin (got {created.get('customer_gstin')!r})",
        cr,
    )

    # GET the shipment and verify fields persist.
    gr = requests.get(
        f"{BASE_URL}/shipments/{ship_id}", headers=auth_headers(tok), timeout=30,
    )
    if not assert_true(tag, gr.status_code == 200, f"GET /shipments/{{id}} 200", gr):
        return
    got = gr.json() or {}
    assert_true(
        tag,
        got.get("customer_email") == "buyer@business.com",
        f"GET customer_email (got {got.get('customer_email')!r})",
        gr,
    )
    assert_true(
        tag,
        got.get("customer_gstin") == "29ABCDE1234F1Z5",
        f"GET customer_gstin (got {got.get('customer_gstin')!r})",
        gr,
    )

    # Cleanup
    dr = requests.delete(
        f"{BASE_URL}/shipments/{ship_id}", headers=auth_headers(tok), timeout=30,
    )
    assert_true(
        tag, dr.status_code in (200, 204), f"DELETE /shipments/{{id}} (got {dr.status_code})", dr,
    )


# ─────────────────────────── TEST 5 ───────────────────────────
def test_5_pending_to_shipment_promotion(tok: str) -> None:
    tag = "TEST5 pending→shipment carries email+gstin"

    sp_payload = {
        "text": (
            "NAME: Promotion Tester\n"
            "PHONE: 9123450011\n"
            "ADDRESS_1: 5 Commerce Plaza, Mumbai\n"
            "CITY: Mumbai\n"
            "STATE: Maharashtra\n"
            "PINCODE: 400013\n"
            "AMOUNT: 4500\n"
            "PAYMENT: COD\n"
            "EMAIL: promo@company.in\n"
            "GST: 27AAACI1681G1ZN\n"
        ),
        "use_ai": False,
        "skip_llm": True,
    }
    r = requests.post(
        f"{BASE_URL}/smart-paste",
        headers=auth_headers(tok),
        json=sp_payload,
        timeout=60,
    )
    if not assert_true(
        tag, r.status_code == 200, f"POST /smart-paste 200 (got {r.status_code})", r,
    ):
        return
    pending = r.json() or {}
    pending_id = pending.get("id")
    assert_true(tag, bool(pending_id), f"pending has id (got {pending_id})", r)
    assert_true(
        tag,
        pending.get("customer_email") == "promo@company.in",
        f"PendingOrder.customer_email set (got {pending.get('customer_email')!r})",
        r,
    )
    assert_true(
        tag,
        pending.get("customer_gstin") == "27AAACI1681G1ZN",
        f"PendingOrder.customer_gstin set (got {pending.get('customer_gstin')!r})",
        r,
    )

    # Resolve a courier_id
    cr = requests.get(f"{BASE_URL}/couriers", headers=auth_headers(tok), timeout=30)
    couriers = cr.json() if cr.status_code == 200 else []
    courier_id = couriers[0]["id"] if couriers else None
    if not courier_id:
        fail(tag, "No courier available for shipping test")
        return

    # Promote
    pr = requests.post(
        f"{BASE_URL}/orders/pending/{pending_id}/ship",
        headers=auth_headers(tok),
        json={"courier_id": courier_id},
        timeout=60,
    )
    if not assert_true(
        tag,
        pr.status_code in (200, 201),
        f"POST /orders/pending/{{id}}/ship 200/201 (got {pr.status_code})",
        pr,
    ):
        # Try to cleanup pending
        requests.delete(
            f"{BASE_URL}/orders/pending/{pending_id}",
            headers=auth_headers(tok),
            timeout=30,
        )
        return
    shipment = pr.json() or {}
    ship_id = shipment.get("id")
    assert_true(
        tag,
        shipment.get("customer_email") == "promo@company.in",
        f"Shipment.customer_email after ship (got {shipment.get('customer_email')!r})",
        pr,
    )
    assert_true(
        tag,
        shipment.get("customer_gstin") == "27AAACI1681G1ZN",
        f"Shipment.customer_gstin after ship (got {shipment.get('customer_gstin')!r})",
        pr,
    )

    # Cleanup
    if ship_id:
        dr = requests.delete(
            f"{BASE_URL}/shipments/{ship_id}",
            headers=auth_headers(tok),
            timeout=30,
        )
        assert_true(
            tag,
            dr.status_code in (200, 204),
            f"cleanup DELETE shipment (got {dr.status_code})",
            dr,
        )


# ─────────────────────────── TEST 6 ───────────────────────────
def test_6_smoke_regression(tok: str) -> None:
    tag = "TEST6 smoke regression"
    for path in (
        "/couriers",
        "/me/feature-flags",
        "/me/custom-fields",
        "/me/contact-settings",
    ):
        r = requests.get(f"{BASE_URL}{path}", headers=auth_headers(tok), timeout=30)
        assert_true(tag, r.status_code == 200, f"GET {path} 200 (got {r.status_code})", r)


# ─────────────────────────── MAIN ───────────────────────────
def main() -> int:
    print("=" * 72)
    print("Phase-3 Smart Paste enhancements — backend verification")
    print("=" * 72)
    tok = login()
    test_1_regex_explicit_labels(tok)
    test_2_regex_opportunistic_freetext(tok)
    test_3_invalid_gstin(tok)
    test_4_direct_shipment_b2b(tok)
    test_5_pending_to_shipment_promotion(tok)
    test_6_smoke_regression(tok)

    print("\n" + "=" * 72)
    print(f"RESULTS: {len(PASS)} passed, {len(FAIL)} failed")
    print("=" * 72)
    if FAIL:
        print("\nFAILURES:")
        for f in FAIL:
            print(f"  - {f}")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
