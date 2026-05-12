"""
Phase F3.9.5 — Abandoned-cart schema in webhook mapping screen.

Quick verification per review request:
1. Order webhook (new_order) returns ORDER schema (existing SCHEMA_FIELDS).
2. Abandoned webhook (abandoned_order) returns ABANDONED schema
   (ABANDONED_CART_SCHEMA_FIELDS).
3. Cleanup — DELETE both test webhooks.
"""
import os
import sys
import requests

BASE = "https://logistics-hub-740.preview.emergentagent.com/api"
EMAIL = "admin@test.com"
PASSWORD = "Admin@12345"


def _expect(cond: bool, msg: str, *, fatal: bool = False) -> bool:
    mark = "✅" if cond else "❌"
    print(f"  {mark} {msg}")
    if not cond and fatal:
        print("  FATAL — aborting")
        sys.exit(1)
    return cond


def main() -> int:
    print(f"\n=== Phase F3.9.5 Test — {BASE} ===\n")
    fails = 0

    # 1. Login
    print("[Auth] Login admin@test.com")
    r = requests.post(
        f"{BASE}/auth/login",
        json={"email": EMAIL, "password": PASSWORD},
        timeout=30,
    )
    _expect(r.status_code == 200, f"Login status 200 (got {r.status_code})", fatal=True)
    token = r.json().get("token")
    _expect(bool(token), "Token returned", fatal=True)
    h = {"Authorization": f"Bearer {token}"}

    # ── Scenario 1: Order webhook returns ORDER schema ─────────────
    print("\n[Scenario 1] Order webhook returns ORDER schema")
    r = requests.post(
        f"{BASE}/me/webhooks",
        json={"name": "Test Order WH", "event_type": "new_order"},
        headers=h, timeout=30,
    )
    if not _expect(r.status_code == 200, f"POST create order wh status 200 (got {r.status_code})"):
        print(r.text[:300]); return 1
    order_wh = r.json()
    order_id = order_wh.get("id")
    _expect(bool(order_id), f"Order webhook id captured: {order_id}", fatal=True)
    _expect(order_wh.get("event_type") == "new_order", "event_type == new_order")

    r = requests.get(f"{BASE}/me/webhooks/{order_id}", headers=h, timeout=30)
    if not _expect(r.status_code == 200, f"GET order wh status 200 (got {r.status_code})"):
        return 1
    body = r.json()
    order_schema = body.get("schema_fields") or []
    print(f"  Order schema_fields ({len(order_schema)}): {order_schema}")

    required_in_order = [
        "customer_name", "customer_phone", "amount", "payment_mode",
        "items", "pincode", "status", "created_at_override",
    ]
    forbidden_in_order = [
        "recovery_url", "cart_value", "external_cart_id",
        "abandoned_at", "items_summary",
    ]
    for f in required_in_order:
        if not _expect(f in order_schema, f"Order schema CONTAINS '{f}'"):
            fails += 1
    for f in forbidden_in_order:
        if not _expect(f not in order_schema, f"Order schema does NOT contain '{f}'"):
            fails += 1

    # ── Scenario 2: Abandoned webhook returns ABANDONED schema ─────
    print("\n[Scenario 2] Abandoned webhook returns ABANDONED schema")
    r = requests.post(
        f"{BASE}/me/webhooks",
        json={"name": "Test Abandoned WH", "event_type": "abandoned_order"},
        headers=h, timeout=30,
    )
    if not _expect(r.status_code == 200, f"POST create abandoned wh status 200 (got {r.status_code})"):
        print(r.text[:300]); return 1
    ab_wh = r.json()
    ab_id = ab_wh.get("id")
    _expect(bool(ab_id), f"Abandoned webhook id captured: {ab_id}", fatal=True)
    _expect(ab_wh.get("event_type") == "abandoned_order", "event_type == abandoned_order")

    r = requests.get(f"{BASE}/me/webhooks/{ab_id}", headers=h, timeout=30)
    if not _expect(r.status_code == 200, f"GET abandoned wh status 200 (got {r.status_code})"):
        return 1
    body = r.json()
    ab_schema = body.get("schema_fields") or []
    print(f"  Abandoned schema_fields ({len(ab_schema)}): {ab_schema}")

    required_in_ab_new = [
        "recovery_url", "cart_value", "external_cart_id",
        "abandoned_at", "items_summary",
    ]
    required_in_ab_basic = [
        "customer_name", "customer_phone", "customer_email",
        "address", "city", "state", "pincode",
    ]
    forbidden_in_ab = [
        "amount", "payment_mode", "items", "weight", "box_dimensions",
        "status", "created_at_override", "courier_hint",
    ]
    for f in required_in_ab_new:
        if not _expect(f in ab_schema, f"Abandoned schema CONTAINS new field '{f}'"):
            fails += 1
    for f in required_in_ab_basic:
        if not _expect(f in ab_schema, f"Abandoned schema CONTAINS basic field '{f}'"):
            fails += 1
    for f in forbidden_in_ab:
        if not _expect(f not in ab_schema, f"Abandoned schema does NOT contain order-only '{f}'"):
            fails += 1

    # Confirm count == 13 per review (13 items in ABANDONED_CART_SCHEMA_FIELDS)
    _expect(len(ab_schema) == 13, f"Abandoned schema has exactly 13 items (got {len(ab_schema)})")

    # ── Scenario 3: Cleanup ───────────────────────────────────────
    print("\n[Cleanup] DELETE both test webhooks")
    for wh_id, label in [(order_id, "order"), (ab_id, "abandoned")]:
        r = requests.delete(f"{BASE}/me/webhooks/{wh_id}", headers=h, timeout=30)
        if not _expect(r.status_code == 200, f"DELETE {label} wh status 200 (got {r.status_code})"):
            fails += 1
        else:
            j = r.json()
            _expect(j.get("ok") is True, f"DELETE {label} wh body ok=true")

    print(f"\n=== Result: {'PASS' if fails == 0 else f'FAIL ({fails} failures)'} ===")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
