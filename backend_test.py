"""
Backend test — Phase F3.9.7 Abandoned-cart recover endpoint workflow flag.
Tests POST /api/me/abandoned-carts/{cart_id}/recover with create_shipment param.
"""
import os
import sys
import asyncio
import json
import uuid
from datetime import datetime, timezone

import requests
from motor.motor_asyncio import AsyncIOMotorClient

BASE = "https://logistics-hub-740.preview.emergentagent.com/api"
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "test_database")

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASSWORD = "Admin@12345"
USER2_EMAIL = "user2@test.com"
USER2_PASSWORD = "User@12345"


def login(email, password):
    r = requests.post(
        f"{BASE}/auth/login",
        json={"email": email, "password": password},
        timeout=30,
    )
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text}"
    data = r.json()
    return data["token"], data["id"]


async def seed_abandoned_cart(user_id, external_cart_id, customer_name, customer_phone, cart_value):
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    cart_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "id": cart_id,
        "user_id": user_id,
        "external_cart_id": external_cart_id,
        "customer_name": customer_name,
        "customer_phone": customer_phone,
        "customer_email": "",
        "address": "123 Test Street",
        "city": "Mumbai",
        "state": "Maharashtra",
        "pincode": "400001",
        "cart_value": cart_value,
        "items_summary": "Test Item x1",
        "items_raw": [],
        "abandoned_at": now,
        "recovery_url": "",
        "status": "abandoned",
        "source_meta": {
            "source_app": "manual",
            "webhook_name": "Test F397",
            "webhook_id": "",
        },
        "created_at": now,
        "updated_at": now,
    }
    await db.abandoned_carts.insert_one(doc)
    client.close()
    return cart_id


async def get_cart_status(cart_id, user_id):
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    c = await db.abandoned_carts.find_one({"id": cart_id, "user_id": user_id}, {"_id": 0})
    client.close()
    return c


async def cleanup():
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    pend_a = await db.pending_orders.delete_many({"external_order_id": "TEST-CART-F397-A"})
    pend_b = await db.pending_orders.delete_many({"external_order_id": "TEST-CART-F397-B"})
    cart_res = await db.abandoned_carts.delete_many(
        {"external_cart_id": {"$in": ["TEST-CART-F397-A", "TEST-CART-F397-B"]}}
    )
    client.close()
    return {
        "pending_deleted_A": pend_a.deleted_count,
        "pending_deleted_B": pend_b.deleted_count,
        "carts_deleted": cart_res.deleted_count,
    }


def run():
    results = []

    def record(name, ok, detail=""):
        status = "PASS" if ok else "FAIL"
        results.append((status, name, detail))
        print(f"[{status}] {name}: {detail}")

    asyncio.run(cleanup())

    token_a, user_a_id = login(ADMIN_EMAIL, ADMIN_PASSWORD)
    headers_a = {"Authorization": f"Bearer {token_a}"}
    print(f"\nLogged in as admin: id={user_a_id}")

    # Seed cart A
    cart_a_id = asyncio.run(
        seed_abandoned_cart(
            user_a_id, "TEST-CART-F397-A", "Test Cart Owner A", "9876500001", 999.99,
        )
    )
    print(f"Seeded cart A: {cart_a_id}")
    record("Scenario 1 - Seed cart A", True, f"cart_id={cart_a_id}")

    # Scenario 2 — create_shipment=false (empty body)
    r2 = requests.post(
        f"{BASE}/me/abandoned-carts/{cart_a_id}/recover",
        json={}, headers=headers_a, timeout=30,
    )
    print(f"\nScenario 2 status: {r2.status_code}")
    try:
        b2 = r2.json()
    except Exception:
        b2 = {}
    print(f"Scenario 2 body: {json.dumps(b2, indent=2)[:500]}")
    s2_ok = True; s2_detail = []
    if r2.status_code != 200:
        s2_ok = False; s2_detail.append(f"HTTP {r2.status_code}")
    else:
        if b2.get("ok") is not True: s2_ok = False; s2_detail.append("ok != true")
        if not b2.get("pending_order_id"): s2_ok = False; s2_detail.append("missing pending_order_id")
        if not b2.get("master_order_id"): s2_ok = False; s2_detail.append("missing master_order_id")
        if "pending_order" in b2: s2_ok = False; s2_detail.append("pending_order key should be absent")
        s2_detail.append(
            f"HTTP=200, ok=true, pending_order_id={b2.get('pending_order_id')}, master_order_id={b2.get('master_order_id')}, pending_order absent={'pending_order' not in b2}"
        )
    record("Scenario 2 - Recover create_shipment=false", s2_ok, " | ".join(s2_detail))
    s2_pending_id = b2.get("pending_order_id") if r2.status_code == 200 else None

    # Scenario 3 — Idempotent re-recover with create_shipment=true
    r3 = requests.post(
        f"{BASE}/me/abandoned-carts/{cart_a_id}/recover",
        json={"create_shipment": True}, headers=headers_a, timeout=30,
    )
    print(f"\nScenario 3 status: {r3.status_code}")
    try:
        b3 = r3.json()
    except Exception:
        b3 = {}
    print(f"Scenario 3 body: {json.dumps(b3, indent=2)[:800]}")
    s3_ok = True; s3_detail = []
    if r3.status_code != 200:
        s3_ok = False; s3_detail.append(f"HTTP {r3.status_code}")
    else:
        if b3.get("already_recovered") is not True: s3_ok = False; s3_detail.append("already_recovered != true")
        if b3.get("pending_order_id") != s2_pending_id:
            s3_ok = False; s3_detail.append(f"pending_order_id mismatch: {b3.get('pending_order_id')} vs {s2_pending_id}")
        po = b3.get("pending_order")
        if not isinstance(po, dict):
            s3_ok = False; s3_detail.append("pending_order not a dict")
        else:
            required = ["id", "customer_name", "customer_phone", "amount", "items", "source"]
            miss = [k for k in required if k not in po]
            if miss: s3_ok = False; s3_detail.append(f"missing keys: {miss}")
            if po.get("source") != "abandoned_cart":
                s3_ok = False; s3_detail.append(f"source != abandoned_cart (got {po.get('source')})")
            if po.get("id") != s2_pending_id:
                s3_ok = False; s3_detail.append("pending_order.id != original pending_order_id")
        s3_detail.append(f"already_recovered=true, pending_order present={isinstance(po, dict)}")
    record("Scenario 3 - Idempotent re-recover create_shipment=true", s3_ok, " | ".join(s3_detail))

    # Scenario 4 — Fresh cart B + create_shipment=true
    cart_b_id = asyncio.run(
        seed_abandoned_cart(
            user_a_id, "TEST-CART-F397-B", "Test Cart Owner B", "9876500002", 1499.50,
        )
    )
    print(f"\nSeeded cart B: {cart_b_id}")
    r4 = requests.post(
        f"{BASE}/me/abandoned-carts/{cart_b_id}/recover",
        json={"create_shipment": True}, headers=headers_a, timeout=30,
    )
    print(f"Scenario 4 status: {r4.status_code}")
    try:
        b4 = r4.json()
    except Exception:
        b4 = {}
    print(f"Scenario 4 body: {json.dumps(b4, indent=2)[:800]}")
    s4_ok = True; s4_detail = []
    if r4.status_code != 200:
        s4_ok = False; s4_detail.append(f"HTTP {r4.status_code}")
    else:
        if b4.get("ok") is not True: s4_ok = False; s4_detail.append("ok != true")
        po = b4.get("pending_order")
        if not isinstance(po, dict):
            s4_ok = False; s4_detail.append("pending_order missing/not dict")
        else:
            required = ["id", "customer_name", "customer_phone", "amount", "items", "source"]
            for k in required:
                if k not in po:
                    s4_ok = False; s4_detail.append(f"missing key: {k}")
            if po.get("source") != "abandoned_cart":
                s4_ok = False; s4_detail.append("source != abandoned_cart")
            if not isinstance(po.get("amount"), (int, float)):
                s4_ok = False; s4_detail.append(f"amount not numeric: {type(po.get('amount')).__name__}")
            if po.get("id") == cart_b_id:
                s4_ok = False; s4_detail.append("pending_order.id == cart_id (should be fresh uuid)")
            try:
                uuid.UUID(str(po.get("id")))
            except Exception:
                s4_ok = False; s4_detail.append(f"pending_order.id not valid uuid: {po.get('id')}")
        s4_detail.append(
            f"pending_order.id={po.get('id') if isinstance(po, dict) else 'N/A'}, "
            f"source={po.get('source') if isinstance(po, dict) else 'N/A'}, "
            f"amount={po.get('amount') if isinstance(po, dict) else 'N/A'}"
        )
    record("Scenario 4 - Fresh recover create_shipment=true", s4_ok, " | ".join(s4_detail))

    cart_b_after = asyncio.run(get_cart_status(cart_b_id, user_a_id))
    if cart_b_after and cart_b_after.get("status") == "recovered":
        record("Scenario 4b - Cart B status=='recovered' in DB", True,
               f"status={cart_b_after.get('status')}, pending_order_id={cart_b_after.get('pending_order_id')}")
    else:
        record("Scenario 4b - Cart B status=='recovered' in DB", False,
               f"status={cart_b_after.get('status') if cart_b_after else 'cart not found'}")

    # Scenario 5 — multi-tenant isolation
    try:
        token_b, user_b_id = login(USER2_EMAIL, USER2_PASSWORD)
        headers_b = {"Authorization": f"Bearer {token_b}"}
        r5 = requests.post(
            f"{BASE}/me/abandoned-carts/{cart_a_id}/recover",
            json={"create_shipment": True}, headers=headers_b, timeout=30,
        )
        print(f"\nScenario 5 status: {r5.status_code}")
        if r5.status_code == 404:
            record("Scenario 5 - Multi-tenant isolation (404 expected)", True, "HTTP=404")
        else:
            record("Scenario 5 - Multi-tenant isolation (404 expected)", False,
                   f"HTTP={r5.status_code}, body={r5.text[:200]}")
    except Exception as e:
        record("Scenario 5 - Multi-tenant isolation", False, f"Error: {e}")

    cleanup_result = asyncio.run(cleanup())
    record("Scenario 6 - Cleanup", True, str(cleanup_result))

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    fails = [r for r in results if r[0] == "FAIL"]
    for status, name, detail in results:
        print(f"{status}  {name}")
    print(f"\nTotal: {len(results)}, Passed: {len(results)-len(fails)}, Failed: {len(fails)}")
    return len(fails) == 0


if __name__ == "__main__":
    ok = run()
    sys.exit(0 if ok else 1)
