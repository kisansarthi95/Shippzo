"""
Backend test — Phases F3.9.8, F3.9.9, F3.9.10
  - Group A: Short-link service (POST/GET/idempotent/validation)
  - Group B: Auto cross-verify recovered carts (primary + fallback)
  - Group C: Upstream order_id preservation in pending_orders
"""
import os
import sys
import asyncio
import json
import uuid
from datetime import datetime, timezone, timedelta

import requests
from motor.motor_asyncio import AsyncIOMotorClient

BASE = "https://logistics-hub-740.preview.emergentagent.com/api"
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "test_database")

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASSWORD = "Admin@12345"

CART_TEST_1 = "cart-test-f398-1"
CART_TEST_2 = "cart-test-f398-2"
EXT_CART_A = "RECOVER-TEST-A"
EXT_CART_B_UNRELATED = "UNRELATED"           # cart-test-2 stored external_cart_id
EXT_ORDER_B_DIFFERENT = "ORD-DIFF-B"         # different uuid in webhook → forces fallback

CREATED_SHORT_CODES: list[str] = []
CREATED_WEBHOOK_IDS: list[str] = []
INGESTED_EXT_OIDS: list[str] = [EXT_CART_A, EXT_ORDER_B_DIFFERENT]

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def record(label: str, ok: bool, info: str = "") -> bool:
    if ok:
        PASSED.append(label)
        print(f"  ✅ {label} {info}")
    else:
        FAILED.append((label, info))
        print(f"  ❌ {label} {info}")
    return ok


def login(email: str, password: str):
    r = requests.post(f"{BASE}/auth/login",
                      json={"email": email, "password": password}, timeout=30)
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text}"
    data = r.json()
    return data["token"], data["id"]


# ──────────────────────────────────────────────────────────────────────
# DB helpers (motor)
# ──────────────────────────────────────────────────────────────────────
async def db_get():
    client = AsyncIOMotorClient(MONGO_URL)
    return client, client[DB_NAME]


async def seed_cart(user_id, cart_id, external_cart_id, phone, amount,
                    abandoned_at=None, customer_name="AR Test"):
    client, db = await db_get()
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        phone_norm = "".join(ch for ch in phone if ch.isdigit())[-10:]
        doc = {
            "id": cart_id,
            "user_id": user_id,
            "external_cart_id": external_cart_id,
            "customer_name": customer_name,
            "customer_phone": phone,
            "customer_phone_norm": phone_norm,
            "customer_email": "",
            "address": "L1",
            "city": "X",
            "state": "Y",
            "pincode": "110001",
            "cart_value": float(amount),
            "items_summary": "Test",
            "items_raw": [],
            "abandoned_at": abandoned_at or now_iso,
            "recovery_url": "",
            "status": "abandoned",
            "source_meta": {"source_app": "manual"},
            "created_at": now_iso,
            "updated_at": now_iso,
        }
        # idempotent
        await db.abandoned_carts.delete_one({"id": cart_id})
        await db.abandoned_carts.insert_one(doc)
        return doc
    finally:
        client.close()


async def fetch_cart(cart_id):
    client, db = await db_get()
    try:
        return await db.abandoned_carts.find_one({"id": cart_id}, {"_id": 0})
    finally:
        client.close()


async def fetch_pending_by_ext(user_id, ext_oid):
    client, db = await db_get()
    try:
        return await db.pending_orders.find_one(
            {"user_id": user_id, "external_order_id": ext_oid}, {"_id": 0}
        )
    finally:
        client.close()


async def fetch_shortlink(code):
    client, db = await db_get()
    try:
        return await db.short_links.find_one({"code": code}, {"_id": 0})
    finally:
        client.close()


async def cleanup(user_id):
    client, db = await db_get()
    try:
        await db.abandoned_carts.delete_many(
            {"id": {"$in": [CART_TEST_1, CART_TEST_2]}}
        )
        await db.pending_orders.delete_many(
            {"user_id": user_id, "external_order_id": {"$in": INGESTED_EXT_OIDS}}
        )
        await db.user_webhooks.delete_many(
            {"user_id": user_id, "name": {"$regex": "^AR Test"}}
        )
        if CREATED_SHORT_CODES:
            await db.short_links.delete_many(
                {"user_id": user_id, "code": {"$in": CREATED_SHORT_CODES}}
            )
    finally:
        client.close()


# ──────────────────────────────────────────────────────────────────────
# GROUP A — Short links
# ──────────────────────────────────────────────────────────────────────
def group_a(token):
    print("\n══ GROUP A — Short links ══")
    h = {"Authorization": f"Bearer {token}"}

    # 1. Unauth POST → 401
    r = requests.post(f"{BASE}/short-links",
                      json={"target_url": "https://example.com/x"}, timeout=20)
    record("A1a unauth POST → 401/403", r.status_code in (401, 403),
           f"got {r.status_code}")

    # Unauth GET on non-existent code → 404 (NOT 401)
    r = requests.get(f"{BASE}/s/zzzzzz", allow_redirects=False, timeout=20)
    record("A1b unauth GET non-existent → 404", r.status_code == 404,
           f"got {r.status_code}")

    # 2. Auth POST happy
    target = "https://example.com/very/long/path?with=lots&of=query"
    r = requests.post(f"{BASE}/short-links",
                      json={"target_url": target}, headers=h, timeout=20)
    ok = r.status_code == 200
    record("A2a POST 200", ok, f"status={r.status_code} body={r.text[:200]}")
    if not ok:
        return None
    body = r.json()
    code = body.get("code", "")
    record("A2b code is 6 chars base62", len(code) == 6 and code.isalnum(),
           f"code={code!r}")
    record("A2c short_url present + ends with /api/s/<code>",
           isinstance(body.get("short_url"), str)
           and body["short_url"].endswith(f"/api/s/{code}")
           and body["short_url"].startswith(("http://", "https://")),
           f"short_url={body.get('short_url')!r}")
    record("A2d target_url echoed", body.get("target_url") == target,
           f"target_url={body.get('target_url')!r}")

    CREATED_SHORT_CODES.append(code)

    # 3. Idempotent POST
    r2 = requests.post(f"{BASE}/short-links",
                       json={"target_url": target}, headers=h, timeout=20)
    record("A3 idempotent same code",
           r2.status_code == 200 and r2.json().get("code") == code,
           f"status={r2.status_code} code={r2.json().get('code')!r}")

    # 4. Public GET → 302 with Location: target_url
    r3 = requests.get(f"{BASE}/s/{code}", allow_redirects=False, timeout=20)
    record("A4a 302 redirect", r3.status_code == 302,
           f"got {r3.status_code}")
    loc = r3.headers.get("Location") or r3.headers.get("location") or ""
    record("A4b Location header == target_url", loc == target,
           f"loc={loc!r}")

    # Verify hits >= 1
    doc = asyncio.run(fetch_shortlink(code))
    record("A4c hits ≥ 1 after redirect",
           bool(doc) and int(doc.get("hits") or 0) >= 1,
           f"hits={doc.get('hits') if doc else 'no doc'}")
    record("A4d last_clicked_at set",
           bool(doc) and bool(doc.get("last_clicked_at")),
           f"last_clicked_at={doc.get('last_clicked_at') if doc else None}")

    # 5. Auth inspect
    r5 = requests.get(f"{BASE}/short-links/{code}", headers=h, timeout=20)
    record("A5 inspect 200 + full doc",
           r5.status_code == 200
           and r5.json().get("code") == code
           and r5.json().get("target_url") == target,
           f"status={r5.status_code}")

    # 6. Validation — ftp:// and empty
    r6a = requests.post(f"{BASE}/short-links",
                        json={"target_url": "ftp://invalid"},
                        headers=h, timeout=20)
    record("A6a ftp:// rejected with 400", r6a.status_code == 400,
           f"got {r6a.status_code}")
    r6b = requests.post(f"{BASE}/short-links",
                        json={"target_url": ""}, headers=h, timeout=20)
    record("A6b empty rejected with 400", r6b.status_code == 400,
           f"got {r6b.status_code}")
    return code


# ──────────────────────────────────────────────────────────────────────
# GROUP B + C — Auto recover + Upstream order_id
# ──────────────────────────────────────────────────────────────────────
def create_webhook(token, name):
    h = {"Authorization": f"Bearer {token}"}
    r = requests.post(f"{BASE}/me/webhooks",
                      json={"name": name, "event_type": "new_order"},
                      headers=h, timeout=20)
    assert r.status_code == 200, f"create webhook: {r.status_code} {r.text}"
    body = r.json()
    CREATED_WEBHOOK_IDS.append(body["id"])
    # Configure a mapping so the new_order ingest actually creates a
    # pending_orders row (otherwise ingest returns imported=0 with the
    # "no field mapping configured yet" friendly skip).
    mapping = {
        "order.uuid":                       "order_id",
        "order.buyer.first_name":           "customer_name",
        "order.buyer.phone":                "customer_phone",
        "order.shipping_address.address_1": "address",
        "order.shipping_address.city":      "city",
        "order.shipping_address.state":     "state",
        "order.shipping_address.pincode":   "pincode",
        "order.total_cost":                 "amount",
    }
    r2 = requests.put(f"{BASE}/me/webhooks/{body['id']}",
                      json={"mapping": mapping}, headers=h, timeout=20)
    assert r2.status_code == 200, f"set mapping: {r2.status_code} {r2.text}"
    return body["id"], body["secret"]


def group_b_c(token, user_id):
    print("\n══ GROUP B — Auto-recover (primary) + GROUP C — Order ID preservation ══")

    # 7. Seed cart-test-1
    asyncio.run(seed_cart(
        user_id=user_id, cart_id=CART_TEST_1,
        external_cart_id=EXT_CART_A,
        phone="9876500099", amount=500.0,
    ))
    record("B7 seed cart-test-1", True, f"id={CART_TEST_1}")

    # 8. Create webhook
    wh_id, secret = create_webhook(token, "AR Test WH P")
    record("B8 webhook created", bool(secret), f"id={wh_id}")

    # 9. POST webhook payload — primary match path (order.uuid == EXT_CART_A)
    payload = {
        "order": {
            "uuid": EXT_CART_A,
            "buyer": {"first_name": "AR", "last_name": "Test",
                      "phone": "9876500099"},
            "shipping_address": {
                "address_1": "L1", "city": "X",
                "state": "Y", "pincode": "110001",
            },
            "total_cost": 500.0,
        }
    }
    r = requests.post(f"{BASE}/webhook/orders/{secret}",
                      json=payload, timeout=30)
    record("B9 webhook ingest 200",
           r.status_code == 200,
           f"status={r.status_code} body={r.text[:300]}")

    # 10. Cart should now be recovered_auto (primary match)
    cart1 = asyncio.run(fetch_cart(CART_TEST_1))
    record("B10a cart status=recovered_auto",
           bool(cart1) and cart1.get("status") == "recovered_auto",
           f"status={cart1.get('status') if cart1 else 'no doc'}")
    record("B10b recovered_at set",
           bool(cart1) and bool(cart1.get("recovered_at")),
           f"recovered_at={cart1.get('recovered_at') if cart1 else None}")
    record("B10c recovered_pending_order_id populated",
           bool(cart1) and bool(cart1.get("recovered_pending_order_id")),
           f"rpoid={cart1.get('recovered_pending_order_id') if cart1 else None}")
    record("B10d recovered_external_order_id == EXT_CART_A",
           bool(cart1) and cart1.get("recovered_external_order_id") == EXT_CART_A,
           f"reoid={cart1.get('recovered_external_order_id') if cart1 else None}")

    # 11. Pending doc external_order_id == EXT_CART_A
    pending = asyncio.run(fetch_pending_by_ext(user_id, EXT_CART_A))
    record("B11a pending order exists for EXT_CART_A",
           bool(pending), f"found={bool(pending)}")
    if pending:
        record("B11b pending.external_order_id == EXT_CART_A",
               pending.get("external_order_id") == EXT_CART_A,
               f"ext_oid={pending.get('external_order_id')!r}")

    # ── GROUP C — order_id preservation ───────────────────────────────
    print("\n══ GROUP C — Order ID preservation ══")
    if pending:
        record("C13a pending.order_id == EXT_CART_A (NOT master_order_id)",
               pending.get("order_id") == EXT_CART_A,
               f"order_id={pending.get('order_id')!r}")
        moid = pending.get("master_order_id") or ""
        record("C13b master_order_id is separate (non-empty, != order_id)",
               bool(moid) and moid != EXT_CART_A,
               f"master_order_id={moid!r}")

    # 12. Fallback match — second cart
    print("\n══ GROUP B (fallback) ══")
    now_iso = datetime.now(timezone.utc).isoformat()
    asyncio.run(seed_cart(
        user_id=user_id, cart_id=CART_TEST_2,
        external_cart_id=EXT_CART_B_UNRELATED,
        phone="9876500088", amount=1000.0,
        abandoned_at=now_iso,
        customer_name="AR Test 2",
    ))
    record("B12-seed cart-test-2", True, f"id={CART_TEST_2}")

    # Send order whose order.uuid is unrelated, but phone + amount match
    payload2 = {
        "order": {
            "uuid": EXT_ORDER_B_DIFFERENT,  # different uuid → primary fails
            "buyer": {"first_name": "AR", "last_name": "Test2",
                      "phone": "9876500088"},
            "shipping_address": {
                "address_1": "L1", "city": "X",
                "state": "Y", "pincode": "110001",
            },
            "total_cost": 1000.0,
        }
    }
    r = requests.post(f"{BASE}/webhook/orders/{secret}",
                      json=payload2, timeout=30)
    record("B12 webhook ingest 200 (fallback)",
           r.status_code == 200,
           f"status={r.status_code}")

    cart2 = asyncio.run(fetch_cart(CART_TEST_2))
    record("B12a cart-test-2 status=recovered_auto (fallback path)",
           bool(cart2) and cart2.get("status") == "recovered_auto",
           f"status={cart2.get('status') if cart2 else 'no doc'}")
    record("B12b cart-test-2 recovered_at set",
           bool(cart2) and bool(cart2.get("recovered_at")),
           f"recovered_at={cart2.get('recovered_at') if cart2 else None}")

    # Sanity — pending row for fallback also has external_order_id preserved
    pending_b = asyncio.run(fetch_pending_by_ext(user_id, EXT_ORDER_B_DIFFERENT))
    record("B12c pending for fallback has external_order_id preserved",
           bool(pending_b)
           and pending_b.get("external_order_id") == EXT_ORDER_B_DIFFERENT
           and pending_b.get("order_id") == EXT_ORDER_B_DIFFERENT,
           f"ext_oid={pending_b.get('external_order_id') if pending_b else None} "
           f"order_id={pending_b.get('order_id') if pending_b else None}")


# ──────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────
def main():
    print(f"Testing against {BASE}")
    token, user_id = login(ADMIN_EMAIL, ADMIN_PASSWORD)
    print(f"Logged in as {ADMIN_EMAIL} (user_id={user_id})")

    try:
        group_a(token)
        group_b_c(token, user_id)
    finally:
        print("\n══ CLEANUP ══")
        asyncio.run(cleanup(user_id))
        print("  cleanup done")

    print("\n" + "═" * 60)
    print(f"PASSED: {len(PASSED)}")
    print(f"FAILED: {len(FAILED)}")
    for label, info in FAILED:
        print(f"  ❌ {label}  ::  {info}")
    print("═" * 60)
    if FAILED:
        sys.exit(1)


if __name__ == "__main__":
    main()
