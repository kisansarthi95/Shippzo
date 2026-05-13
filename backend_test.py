"""
Phase-21 Backend Test — Pending Orders New/Repeat Markers, Mark-Viewed,
Pending-Count `new_count`, Feature Flags Registry.

Endpoints exercised:
  • POST /api/auth/login
  • GET  /api/orders/pending
  • GET  /api/orders/pending/{id}
  • POST /api/smart-paste
  • POST /api/orders/pending/{id}/mark-viewed   (NEW)
  • DELETE /api/orders/pending/{id}
  • POST /api/shipments
  • DELETE /api/shipments/{id}
  • GET  /api/orders/pending-count
  • GET  /api/me/feature-flags
  • GET  /api/me/feature-registry  (the request mentioned /api/admin/...
        but the actual endpoint shipped is /api/me/feature-registry +
        /api/admin/plan-features which embeds the registry payload —
        we test both.)
  • GET  /api/admin/plan-features  (admin-only — has the registry)
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

import requests

BASE_URL = "https://logistics-hub-740.preview.emergentagent.com/api"

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASSWORD = "Admin@12345"
USER2_EMAIL = "user2@test.com"
USER2_PASSWORD = "User@12345"


# ── tiny test harness ────────────────────────────────────────────────
RESULTS: List[Tuple[str, bool, str]] = []


def record(name: str, ok: bool, msg: str = "") -> None:
    icon = "✅" if ok else "❌"
    print(f"{icon} {name}{(' — ' + msg) if msg else ''}")
    RESULTS.append((name, ok, msg))


def assertTrue(name: str, cond: bool, msg: str = "") -> bool:
    record(name, bool(cond), msg)
    return bool(cond)


# ── helpers ──────────────────────────────────────────────────────────
def login(email: str, password: str) -> str:
    r = requests.post(
        f"{BASE_URL}/auth/login",
        json={"email": email, "password": password},
        timeout=20,
    )
    r.raise_for_status()
    data = r.json()
    return data["token"]


def H(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def smart_paste(token: str, text: str, skip_llm: bool = True) -> Dict[str, Any]:
    r = requests.post(
        f"{BASE_URL}/smart-paste",
        json={"text": text, "use_ai": False, "skip_llm": skip_llm},
        headers=H(token),
        timeout=60,
    )
    return {"status": r.status_code, "body": r.json() if r.status_code < 500 else r.text}


def canonical_paste_text(name: str, phone: str, pincode: str = "380015",
                         city: str = "Ahmedabad", state: str = "Gujarat",
                         items: str = "T-Shirt", amount: float = 599.0,
                         payment: str = "COD") -> str:
    """Build canonical NAME:/PHONE:/... block for regex parser."""
    return (
        f"NAME: {name}\n"
        f"PHONE: {phone}\n"
        f"PINCODE: {pincode}\n"
        f"CITY: {city}\n"
        f"STATE: {state}\n"
        f"ADDRESS_1: 1 Test Lane\n"
        f"AMOUNT: {amount}\n"
        f"PAYMENT_MODE: {payment}\n"
        f"ITEMS: {items}\n"
    )


def get_pending(token: str, source: Optional[str] = None) -> Tuple[int, Any]:
    params: Dict[str, Any] = {}
    if source:
        params["source"] = source
    r = requests.get(
        f"{BASE_URL}/orders/pending",
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=20,
    )
    return r.status_code, r.json() if r.status_code < 500 else r.text


def get_pending_one(token: str, oid: str) -> Tuple[int, Any]:
    r = requests.get(
        f"{BASE_URL}/orders/pending/{oid}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=20,
    )
    return r.status_code, r.json() if r.status_code < 500 else r.text


def mark_viewed(token: Optional[str], oid: str) -> Tuple[int, Any]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = requests.post(
        f"{BASE_URL}/orders/pending/{oid}/mark-viewed",
        headers=headers,
        timeout=20,
    )
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, r.text


def delete_pending(token: str, oid: str) -> Tuple[int, Any]:
    r = requests.delete(
        f"{BASE_URL}/orders/pending/{oid}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=20,
    )
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, r.text


def create_shipment(token: str, phone: str, name: str = "Phase21 Repeat Tester") -> Tuple[int, Any]:
    payload = {
        "tracking_id": f"PH21-{uuid.uuid4().hex[:6].upper()}",
        "courier_name": "Test Courier",
        "customer_name": name,
        "customer_phone": phone,
        "address_line1": "1 Test Lane",
        "city": "Ahmedabad",
        "state": "Gujarat",
        "pincode": "380015",
        "payment_mode": "Prepaid",
        "amount": 599.0,
        "items": ["T-Shirt"],
    }
    r = requests.post(
        f"{BASE_URL}/shipments",
        json=payload,
        headers=H(token),
        timeout=30,
    )
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, r.text


def delete_shipment(token: str, sid: str) -> int:
    r = requests.delete(
        f"{BASE_URL}/shipments/{sid}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=20,
    )
    return r.status_code


# ── tests ────────────────────────────────────────────────────────────
def test_backward_compat(token: str) -> None:
    print("\n── Test 1: Backward compatibility (viewed / is_repeat_customer) ──")
    code, body = get_pending(token)
    if not assertTrue("GET /orders/pending → 200", code == 200, f"got {code}"):
        return
    if not assertTrue("response is a list", isinstance(body, list)):
        return
    print(f"  pending row count: {len(body)}")
    if not body:
        record("at least one pending row to validate fields", False,
               "no rows; skipping field assertions")
        return
    sources_seen = set()
    bad = []
    for row in body:
        sources_seen.add(row.get("source"))
        if "viewed" not in row or not isinstance(row.get("viewed"), bool):
            bad.append((row.get("id"), "viewed missing/non-bool", row.get("source")))
        if "is_repeat_customer" not in row or not isinstance(row.get("is_repeat_customer"), bool):
            bad.append((row.get("id"), "is_repeat_customer missing/non-bool", row.get("source")))
    assertTrue("every row has viewed:bool", not bad,
               f"{len(bad)} bad rows {bad[:3]}" if bad else "")
    assertTrue("every row has is_repeat_customer:bool", not bad,
               f"{len(bad)} bad rows {bad[:3]}" if bad else "")
    print(f"  sources seen: {sources_seen}")


def test_mark_viewed(token: str) -> Optional[str]:
    print("\n── Test 2: Mark-Viewed Endpoint ──")
    sample_text = canonical_paste_text(
        "Riya Sharma", "9876543210", items="T-Shirt", amount=599.0, payment="COD",
    )
    res = smart_paste(token, sample_text)
    if not assertTrue("POST /smart-paste → 200", res["status"] == 200,
                      f"got {res['status']}: {str(res['body'])[:200]}"):
        return None
    body = res["body"]
    oid = body.get("id")
    if not assertTrue("smart-paste returns id", bool(oid)):
        return None

    # Test 2: viewed=false initially
    code, row = get_pending_one(token, oid)
    assertTrue("GET pending/{id} → 200", code == 200, f"got {code}")
    assertTrue("new order viewed=False", row.get("viewed") is False,
               f"got {row.get('viewed')!r}")

    # Test 3: mark-viewed → ok/viewed/matched all true
    code, payload = mark_viewed(token, oid)
    assertTrue("mark-viewed → 200", code == 200, f"got {code}")
    assertTrue("mark-viewed ok:true",
               isinstance(payload, dict) and payload.get("ok") is True,
               f"got {payload}")
    assertTrue("mark-viewed viewed:true",
               isinstance(payload, dict) and payload.get("viewed") is True,
               f"got {payload}")
    assertTrue("mark-viewed matched:true",
               isinstance(payload, dict) and payload.get("matched") is True,
               f"got {payload}")

    # Test 4: viewed is now True
    code, row2 = get_pending_one(token, oid)
    assertTrue("after mark-viewed GET → 200", code == 200)
    assertTrue("now viewed=True", row2.get("viewed") is True,
               f"got {row2.get('viewed')!r}")

    # Test 5: idempotent
    code2, payload2 = mark_viewed(token, oid)
    assertTrue("mark-viewed (again) → 200", code2 == 200)
    assertTrue("mark-viewed (again) ok:true",
               isinstance(payload2, dict) and payload2.get("ok") is True)
    assertTrue("mark-viewed (again) matched:true",
               isinstance(payload2, dict) and payload2.get("matched") is True,
               f"got {payload2}")

    # Test 6: invalid UUID → soft-404 (NOT HTTP 404)
    bogus_id = f"00000000-0000-0000-0000-{uuid.uuid4().hex[:12]}"
    code3, payload3 = mark_viewed(token, bogus_id)
    assertTrue("mark-viewed (invalid) → 200 (soft-404)",
               code3 == 200, f"got HTTP {code3}: {payload3}")
    if isinstance(payload3, dict):
        assertTrue("mark-viewed (invalid) ok:true", payload3.get("ok") is True,
                   f"got {payload3}")
        assertTrue("mark-viewed (invalid) viewed:false",
                   payload3.get("viewed") is False, f"got {payload3}")
        assertTrue("mark-viewed (invalid) matched:false",
                   payload3.get("matched") is False, f"got {payload3}")

    # Test 7: no auth → 401
    code4, _payload4 = mark_viewed(None, oid)
    assertTrue("mark-viewed (no auth) → 401",
               code4 == 401, f"got HTTP {code4}")

    return oid


def test_repeat_customer(token: str) -> Tuple[List[str], List[str]]:
    """
    Returns (created_pending_ids, created_shipment_ids) for cleanup.
    """
    print("\n── Test 3: Repeat-Customer Detection ──")
    # Use unique phones per run to avoid colliding with prior runs.
    salt = f"{int(time.time()) % 1000:03d}"
    repeat_phone = f"99990{salt}1"   # 8-digit base + salt = 10 digit; pad
    repeat_phone = ("9" + salt + uuid.uuid4().hex[:6])[:10]
    # ensure exactly 10 digits, all digits
    repeat_phone = "".join(ch for ch in repeat_phone if ch.isdigit())[:10].ljust(10, "0")
    fresh_phone = "".join(ch for ch in ("8" + salt + uuid.uuid4().hex[:6]) if ch.isdigit())[:10].ljust(10, "0")
    if repeat_phone == fresh_phone:
        fresh_phone = "8" + fresh_phone[1:]

    print(f"  repeat_phone (will get prior shipment): {repeat_phone}")
    print(f"  fresh_phone (no shipment): {fresh_phone}")

    created_pending: List[str] = []
    created_shipments: List[str] = []

    # Step A: create shipment with repeat_phone
    code, ship = create_shipment(token, repeat_phone, name="Ishita Phase21")
    if not assertTrue("create shipment for repeat phone → 200/201",
                      code in (200, 201), f"got {code}: {str(ship)[:200]}"):
        return created_pending, created_shipments
    sid = ship.get("id")
    if sid:
        created_shipments.append(sid)
    assertTrue("shipment has id", bool(sid))

    # Step B: smart-paste with same phone → is_repeat_customer:true
    text_repeat = canonical_paste_text("Ishita Desai", repeat_phone, items="T-Shirt",
                                       amount=499.0, payment="COD")
    res_b = smart_paste(token, text_repeat)
    if assertTrue("smart-paste (repeat phone) → 200",
                  res_b["status"] == 200, f"got {res_b['status']}"):
        body_b = res_b["body"]
        oid_b = body_b.get("id")
        if oid_b:
            created_pending.append(oid_b)
        assertTrue("repeat-phone pending has is_repeat_customer:True",
                   body_b.get("is_repeat_customer") is True,
                   f"got is_repeat_customer={body_b.get('is_repeat_customer')!r} "
                   f"phone_parsed={body_b.get('customer_phone')!r}")
        assertTrue("repeat-phone pending has viewed:False",
                   body_b.get("viewed") is False,
                   f"got viewed={body_b.get('viewed')!r}")

    # Step C: smart-paste with fresh_phone → is_repeat_customer:false
    text_fresh = canonical_paste_text("Aarav Mehta", fresh_phone, items="Bottle",
                                      amount=399.0, payment="COD")
    res_c = smart_paste(token, text_fresh)
    if assertTrue("smart-paste (fresh phone) → 200",
                  res_c["status"] == 200, f"got {res_c['status']}"):
        body_c = res_c["body"]
        oid_c = body_c.get("id")
        if oid_c:
            created_pending.append(oid_c)
        assertTrue("fresh-phone pending has is_repeat_customer:False",
                   body_c.get("is_repeat_customer") is False,
                   f"got is_repeat_customer={body_c.get('is_repeat_customer')!r}")

    # Step D: phone normalisation — +91 prefix + spaces
    formatted = f"+91 {repeat_phone[:5]} {repeat_phone[5:]}"
    text_norm = canonical_paste_text("Ishita Desai", formatted, items="T-Shirt",
                                     amount=499.0, payment="COD")
    res_d = smart_paste(token, text_norm)
    if assertTrue("smart-paste (+91 prefix) → 200",
                  res_d["status"] == 200, f"got {res_d['status']}: {str(res_d['body'])[:200]}"):
        body_d = res_d["body"]
        oid_d = body_d.get("id")
        if oid_d:
            created_pending.append(oid_d)
        # Note: parser strips +91 and stores last-10 digits.
        assertTrue("+91 prefix → still is_repeat_customer:True",
                   body_d.get("is_repeat_customer") is True,
                   f"got is_repeat_customer={body_d.get('is_repeat_customer')!r} "
                   f"phone_parsed={body_d.get('customer_phone')!r}")

    return created_pending, created_shipments


def test_pending_count(token: str) -> None:
    print("\n── Test 4: /orders/pending-count ──")
    r = requests.get(
        f"{BASE_URL}/orders/pending-count",
        headers={"Authorization": f"Bearer {token}"},
        timeout=20,
    )
    if not assertTrue("GET /orders/pending-count → 200", r.status_code == 200,
                      f"got {r.status_code}"):
        return
    body = r.json()
    assertTrue("response has 'count'", "count" in body)
    assertTrue("response has 'new_count' (new field)", "new_count" in body,
               f"keys: {list(body.keys())}")
    assertTrue("response has 'smart_paste_count'", "smart_paste_count" in body)
    assertTrue("response has 'sheet_count'", "sheet_count" in body)
    assertTrue("count == new_count",
               body.get("count") == body.get("new_count"),
               f"count={body.get('count')} new_count={body.get('new_count')}")
    assertTrue("count == smart_paste_count + sheet_count",
               body.get("count") == (body.get("smart_paste_count", 0)
                                     + body.get("sheet_count", 0)),
               f"got {body}")


def test_feature_flags(token: str, label: str, must_be_admin: bool) -> None:
    print(f"\n── Test 5{label}: /me/feature-flags ──")
    r = requests.get(
        f"{BASE_URL}/me/feature-flags",
        headers={"Authorization": f"Bearer {token}"},
        timeout=20,
    )
    if not assertTrue(f"{label}: feature-flags → 200", r.status_code == 200,
                      f"got {r.status_code}"):
        return
    body = r.json()
    feats = set(body.get("features") or [])
    plan = body.get("plan")
    print(f"  plan={plan} is_admin={body.get('is_admin')} feature_count={len(feats)}")
    for key in ("pending_orders_new_marker",
                "pending_orders_repeat_marker",
                "new_order_sound"):
        assertTrue(f"{label}: features contains {key}", key in feats,
                   "" if key in feats else f"missing — sample feats {list(feats)[:6]}")


def test_admin_feature_registry(token: str) -> None:
    print("\n── Test 5.B: feature registry payload ──")
    # The review asked for /api/admin/feature-registry. The actually-
    # shipped endpoints are:
    #   GET /api/me/feature-registry        (any logged-in user)
    #   GET /api/admin/plan-features        (admin — embeds the same
    #                                        registry payload)
    # We probe both.
    r = requests.get(
        f"{BASE_URL}/me/feature-registry",
        headers={"Authorization": f"Bearer {token}"},
        timeout=20,
    )
    if not assertTrue("GET /me/feature-registry → 200",
                      r.status_code == 200, f"got {r.status_code}"):
        return
    body = r.json()
    reg = body.get("registry") or {}
    feats_list = reg.get("features") or []
    feats_by_key = {f.get("key"): f for f in feats_list if isinstance(f, dict)}
    cat_order = reg.get("categories") or []
    print(f"  registry shape: features_count={len(feats_list)} categories_count={len(cat_order)}")
    assertTrue("'Customer Intelligence' appears in categories order list",
               "Customer Intelligence" in cat_order,
               f"categories: {cat_order}")
    for key in ("pending_orders_new_marker",
                "pending_orders_repeat_marker",
                "new_order_sound"):
        present = key in feats_by_key
        assertTrue(f"registry.features contains {key}", present,
                   "" if present else f"sample keys: {list(feats_by_key)[:6]}")
        if present:
            cat = feats_by_key[key].get("category")
            assertTrue(f"{key} category == 'Customer Intelligence'",
                       cat == "Customer Intelligence", f"got {cat!r}")

    # Also try /api/admin/plan-features (admin only)
    r2 = requests.get(
        f"{BASE_URL}/admin/plan-features",
        headers={"Authorization": f"Bearer {token}"},
        timeout=20,
    )
    if r2.status_code == 200:
        body2 = r2.json()
        reg2 = body2.get("registry") or {}
        feats2 = {f.get("key") for f in (reg2.get("features") or []) if isinstance(f, dict)}
        for key in ("pending_orders_new_marker",
                    "pending_orders_repeat_marker",
                    "new_order_sound"):
            assertTrue(f"admin/plan-features registry contains {key}",
                       key in feats2, "" if key in feats2 else "missing")
    else:
        print(f"  (admin/plan-features returned {r2.status_code} for current user — expected if not admin)")


# ── main ─────────────────────────────────────────────────────────────
def main() -> int:
    print(f"=== Phase-21 backend smoke test ===")
    print(f"BASE_URL = {BASE_URL}\n")

    # Login admin (preferred for full coverage)
    try:
        token_admin = login(ADMIN_EMAIL, ADMIN_PASSWORD)
        print(f"✓ admin login OK")
    except Exception as e:
        print(f"❌ admin login failed: {e}")
        return 2

    try:
        token_user2 = login(USER2_EMAIL, USER2_PASSWORD)
        print(f"✓ user2 login OK")
    except Exception as e:
        print(f"⚠ user2 login failed: {e}; continuing with admin only")
        token_user2 = None

    cleanup_pending: List[str] = []
    cleanup_shipments: List[str] = []

    try:
        # Test 1: backward compat
        test_backward_compat(token_admin)

        # Test 2: mark-viewed
        viewed_oid = test_mark_viewed(token_admin)
        if viewed_oid:
            cleanup_pending.append(viewed_oid)

        # Test 3: repeat customer detection (admin)
        pending_ids, shipment_ids = test_repeat_customer(token_admin)
        cleanup_pending.extend(pending_ids)
        cleanup_shipments.extend(shipment_ids)

        # Test 4: pending-count
        test_pending_count(token_admin)

        # Test 5: feature flags & registry
        test_feature_flags(token_admin, " (admin)", must_be_admin=True)
        if token_user2:
            test_feature_flags(token_user2, " (user2)", must_be_admin=False)

        test_admin_feature_registry(token_admin)
    finally:
        print(f"\n── Cleanup ── pending={len(cleanup_pending)} shipments={len(cleanup_shipments)}")
        for oid in cleanup_pending:
            try:
                code, _ = delete_pending(token_admin, oid)
                print(f"  DELETE pending/{oid[:8]} → {code}")
            except Exception as e:
                print(f"  DELETE pending/{oid[:8]} FAILED: {e}")
        for sid in cleanup_shipments:
            try:
                code = delete_shipment(token_admin, sid)
                print(f"  DELETE shipments/{sid[:8]} → {code}")
            except Exception as e:
                print(f"  DELETE shipments/{sid[:8]} FAILED: {e}")

    # Summary
    print("\n" + "=" * 60)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = [r for r in RESULTS if not r[1]]
    print(f"PASS {passed}/{len(RESULTS)}")
    if failed:
        print("\nFAILED:")
        for name, _ok, msg in failed:
            print(f"  ❌ {name}{(' — ' + msg) if msg else ''}")
        return 1
    print("All assertions green.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
