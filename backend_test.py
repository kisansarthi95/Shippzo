"""
Phase F3.2 — Order Status Update event-type webhook flow tests.

Tests the public ingest endpoint at /api/webhook/orders/{secret} when the
configured webhook has event_type="order_status_update". Validates:
  • Status normalisation (Shipped, Delivered, Returned, free-text fallback).
  • Mirror onto well-known timestamp fields (dispatched_at / delivered_at /
    returned_at / status_updated_at).
  • Order-id miss → imported=0, not_found=1, friendly error.
  • Pending order branch (matched in db.pending_orders).
  • Mapping incomplete (no order_id mapping) → imported=0 + error.
  • Event timestamp override via mapped `created_at_override` cell.
  • Bulk array body.
  • Regression: new_order still creates pending_orders, abandoned_order
    returns 200 imported=0 with friendly placeholder.
"""
from __future__ import annotations

import os
import sys
import uuid
import asyncio
from datetime import datetime, timezone

import requests
from motor.motor_asyncio import AsyncIOMotorClient

BACKEND_URL = "https://logistics-hub-740.preview.emergentagent.com"
API = f"{BACKEND_URL}/api"

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASSWORD = "Admin@12345"

MONGO_URL = "mongodb://localhost:27017"
DB_NAME = "test_database"

# --- helper assert with counter ---
PASSED = 0
FAILED = 0
FAIL_DETAILS = []


def check(cond: bool, msg: str, ctx: object = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ✅ {msg}")
    else:
        FAILED += 1
        FAIL_DETAILS.append((msg, ctx))
        print(f"  ❌ {msg}  ctx={ctx!r}")


# ── 1. Login ────────────────────────────────────────────────────────
def login() -> tuple[str, str]:
    r = requests.post(
        f"{API}/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=20,
    )
    r.raise_for_status()
    j = r.json()
    return j["token"], j["id"]


# ── 2. Mongo helpers (run async) ────────────────────────────────────
async def insert_test_shipment(user_id: str) -> None:
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    now = datetime.now(timezone.utc).isoformat()
    await db.shipments.delete_many({"id": "shp_test_phaseF32_1"})
    await db.shipments.insert_one({
        "id":              "shp_test_phaseF32_1",
        "user_id":          user_id,
        "master_order_id": "ORD-9999",
        "status":          "Ready to Ship",
        "customer_name":   "F32 Test",
        "customer_phone":  "9000000000",
        "created_at":      now,
    })
    client.close()


async def insert_test_pending(user_id: str) -> None:
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    now = datetime.now(timezone.utc).isoformat()
    await db.pending_orders.delete_many({"id": "pend_test_phaseF32_1"})
    await db.pending_orders.insert_one({
        "id":              "pend_test_phaseF32_1",
        "user_id":          user_id,
        "master_order_id": "PND-1234",
        "status":          "pending",
        "customer_name":   "F32 Pending",
        "customer_phone":  "9000000001",
        "created_at":      now,
    })
    client.close()


async def fetch_shipment(shp_id: str) -> dict | None:
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    doc = await db.shipments.find_one({"id": shp_id}, {"_id": 0})
    client.close()
    return doc


async def fetch_pending(pend_id: str) -> dict | None:
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    doc = await db.pending_orders.find_one({"id": pend_id}, {"_id": 0})
    client.close()
    return doc


async def cleanup_db(extra_pending_ids: list[str]) -> None:
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    await db.shipments.delete_many({"id": "shp_test_phaseF32_1"})
    await db.pending_orders.delete_many({"id": "pend_test_phaseF32_1"})
    if extra_pending_ids:
        await db.pending_orders.delete_many({"id": {"$in": extra_pending_ids}})
    client.close()


# ── 3. Webhook helpers ──────────────────────────────────────────────
def create_webhook(token: str, name: str, event_type: str) -> dict:
    r = requests.post(
        f"{API}/me/webhooks",
        json={"name": name, "event_type": event_type},
        headers={"Authorization": f"Bearer {token}"},
        timeout=20,
    )
    r.raise_for_status()
    return r.json()


def update_webhook_mapping(token: str, wh_id: str, mapping: dict) -> dict:
    r = requests.put(
        f"{API}/me/webhooks/{wh_id}",
        json={"mapping": mapping},
        headers={"Authorization": f"Bearer {token}"},
        timeout=20,
    )
    r.raise_for_status()
    return r.json()


def delete_webhook(token: str, wh_id: str) -> None:
    r = requests.delete(
        f"{API}/me/webhooks/{wh_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=20,
    )
    r.raise_for_status()


def post_webhook(url: str, body) -> dict:
    r = requests.post(url, json=body, timeout=20)
    return {"status": r.status_code, "body": r.json() if r.status_code != 500 else r.text}


# ── 4. Test orchestration ───────────────────────────────────────────
def main() -> int:
    print("\n=== Phase F3.2 Order Status Update Webhook Tests ===\n")
    print(f"Backend: {API}")
    print(f"Admin:   {ADMIN_EMAIL}")
    print()

    print("[step 0] Logging in as admin…")
    token, admin_id = login()
    print(f"  → admin_id={admin_id}\n")

    # ───────────────────────────────────────────────────────────
    # CASE 1 — Create webhook with event_type="order_status_update"
    # ───────────────────────────────────────────────────────────
    print("[case 1] Create OSU webhook + set mapping {order_id, status}")
    wh = create_webhook(token, "F32 Status WH", "order_status_update")
    wh_id  = wh["id"]
    wh_url = wh["url"]
    check(bool(wh_id), "webhook id returned", wh)
    check(bool(wh_url), "webhook url returned", wh_url)
    check(wh["event_type"] == "order_status_update", "event_type stored", wh.get("event_type"))

    # Set mapping
    upd = update_webhook_mapping(
        token, wh_id, {"order_id": "order_id", "status": "status"},
    )
    check(upd.get("mapping") == {"order_id": "order_id", "status": "status"},
          "mapping persisted", upd.get("mapping"))
    print()

    # ───────────────────────────────────────────────────────────
    # Insert test shipment + pending
    # ───────────────────────────────────────────────────────────
    print("[setup] Inserting shipment shp_test_phaseF32_1 (ORD-9999) & "
          "pending pend_test_phaseF32_1 (PND-1234)…")
    asyncio.run(insert_test_shipment(admin_id))
    asyncio.run(insert_test_pending(admin_id))
    print()

    # ───────────────────────────────────────────────────────────
    # CASE 2 — POST status="shipped" → imported=1, dispatched_at set
    # ───────────────────────────────────────────────────────────
    print("[case 2] POST {order_id:'ORD-9999', status:'shipped'}")
    res2 = post_webhook(wh_url, {"order_id": "ORD-9999", "status": "shipped"})
    check(res2["status"] == 200, "HTTP 200", res2)
    body2 = res2["body"]
    check(body2.get("imported") == 1, "imported=1", body2)
    check(body2.get("event_type") == "order_status_update", "event_type echoed", body2)
    check(body2.get("not_found", -1) == 0, "not_found=0", body2)

    shp = asyncio.run(fetch_shipment("shp_test_phaseF32_1"))
    check(shp is not None, "shipment exists", None)
    check(shp.get("status") == "Shipped", "status=Shipped", shp.get("status"))
    check(bool(shp.get("dispatched_at")), "dispatched_at set", shp.get("dispatched_at"))
    check(bool(shp.get("status_updated_at")), "status_updated_at set",
          shp.get("status_updated_at"))
    print()

    # ───────────────────────────────────────────────────────────
    # CASE 3 — Delivered
    # ───────────────────────────────────────────────────────────
    print("[case 3] POST {order_id:'ORD-9999', status:'delivered'}")
    res3 = post_webhook(wh_url, {"order_id": "ORD-9999", "status": "delivered"})
    check(res3["status"] == 200, "HTTP 200", res3)
    check(res3["body"].get("imported") == 1, "imported=1", res3["body"])
    shp = asyncio.run(fetch_shipment("shp_test_phaseF32_1"))
    check(shp.get("status") == "Delivered", "status=Delivered", shp.get("status"))
    check(bool(shp.get("delivered_at")), "delivered_at set", shp.get("delivered_at"))
    print()

    # ───────────────────────────────────────────────────────────
    # CASE 4 — Returned
    # ───────────────────────────────────────────────────────────
    print("[case 4] POST {order_id:'ORD-9999', status:'returned'}")
    res4 = post_webhook(wh_url, {"order_id": "ORD-9999", "status": "returned"})
    check(res4["status"] == 200, "HTTP 200", res4)
    check(res4["body"].get("imported") == 1, "imported=1", res4["body"])
    shp = asyncio.run(fetch_shipment("shp_test_phaseF32_1"))
    check(shp.get("status") == "Returned", "status=Returned", shp.get("status"))
    check(bool(shp.get("returned_at")), "returned_at set", shp.get("returned_at"))
    print()

    # ───────────────────────────────────────────────────────────
    # CASE 5 — Free-text status normalisation
    # ───────────────────────────────────────────────────────────
    print("[case 5] POST {order_id:'ORD-9999', status:'OUT FOR DELIVERY'}")
    res5 = post_webhook(wh_url, {"order_id": "ORD-9999", "status": "OUT FOR DELIVERY"})
    check(res5["status"] == 200, "HTTP 200", res5)
    check(res5["body"].get("imported") == 1, "imported=1", res5["body"])
    shp = asyncio.run(fetch_shipment("shp_test_phaseF32_1"))
    check(bool(shp.get("status")), "status non-empty", shp.get("status"))
    # Note: 'OUT FOR DELIVERY' maps to 'Shipped' per import_schema STATUS_ALIASES
    print(f"  ℹ status normalised to: '{shp.get('status')}'")
    print()

    # ───────────────────────────────────────────────────────────
    # CASE 6 — Order_id miss
    # ───────────────────────────────────────────────────────────
    print("[case 6] POST {order_id:'ORD-NOT-IN-DB', status:'shipped'} → not_found=1")
    res6 = post_webhook(wh_url, {"order_id": "ORD-NOT-IN-DB", "status": "shipped"})
    check(res6["status"] == 200, "HTTP 200", res6)
    body6 = res6["body"]
    check(body6.get("imported") == 0, "imported=0", body6)
    check(body6.get("not_found") == 1, "not_found=1", body6)
    errs = body6.get("errors") or []
    check(len(errs) >= 1 and "no shipment / pending order found" in errs[0],
          "error mentions 'no shipment / pending order found'", errs)
    print()

    # ───────────────────────────────────────────────────────────
    # CASE 7 — Pending order match
    # ───────────────────────────────────────────────────────────
    print("[case 7] POST {order_id:'PND-1234', status:'processing'}")
    res7 = post_webhook(wh_url, {"order_id": "PND-1234", "status": "processing"})
    check(res7["status"] == 200, "HTTP 200", res7)
    check(res7["body"].get("imported") == 1, "imported=1", res7["body"])
    pend = asyncio.run(fetch_pending("pend_test_phaseF32_1"))
    check(pend.get("status") == "Processing", "pending status=Processing",
          pend.get("status"))
    check(bool(pend.get("status_updated_at")), "status_updated_at set",
          pend.get("status_updated_at"))
    print()

    # ───────────────────────────────────────────────────────────
    # CASE 8 — Mapping incomplete (no order_id key)
    # ───────────────────────────────────────────────────────────
    print("[case 8] PUT mapping={status: status} (no order_id) + POST")
    update_webhook_mapping(token, wh_id, {"status": "status"})
    res8 = post_webhook(wh_url, {"order_id": "ORD-9999", "status": "delivered"})
    check(res8["status"] == 200, "HTTP 200", res8)
    body8 = res8["body"]
    check(body8.get("imported") == 0, "imported=0", body8)
    errs8 = body8.get("errors") or []
    check(len(errs8) >= 1 and "order_id" in errs8[0].lower(),
          "error mentions order_id missing", errs8)
    print()

    # ───────────────────────────────────────────────────────────
    # CASE 9 — Event timestamp override
    # ───────────────────────────────────────────────────────────
    print("[case 9] mapping incl. shipped_at→created_at_override; POST with shipped_at")
    update_webhook_mapping(token, wh_id, {
        "order_id":   "order_id",
        "status":     "status",
        "shipped_at": "created_at_override",
    })
    res9 = post_webhook(wh_url, {
        "order_id":   "ORD-9999",
        "status":     "shipped",
        "shipped_at": "2026-01-15T10:30:00Z",
    })
    check(res9["status"] == 200, "HTTP 200", res9)
    check(res9["body"].get("imported") == 1, "imported=1", res9["body"])
    shp = asyncio.run(fetch_shipment("shp_test_phaseF32_1"))
    disp = shp.get("dispatched_at") or ""
    check(disp.startswith("2026-01-15"), "dispatched_at = 2026-01-15…",
          disp)
    su = shp.get("status_updated_at") or ""
    check(su.startswith("2026-01-15"), "status_updated_at = 2026-01-15…", su)
    print()

    # ───────────────────────────────────────────────────────────
    # CASE 10 — Bulk update (array body)
    # ───────────────────────────────────────────────────────────
    print("[case 10] POST array of 2 → imported=2")
    res10 = post_webhook(wh_url, [
        {"order_id": "ORD-9999",  "status": "delivered"},
        {"order_id": "PND-1234",  "status": "delivered"},
    ])
    check(res10["status"] == 200, "HTTP 200", res10)
    check(res10["body"].get("imported") == 2, "imported=2", res10["body"])
    shp = asyncio.run(fetch_shipment("shp_test_phaseF32_1"))
    pend = asyncio.run(fetch_pending("pend_test_phaseF32_1"))
    check(shp.get("status") == "Delivered", "shipment now Delivered",
          shp.get("status"))
    check(pend.get("status") == "Delivered", "pending now Delivered",
          pend.get("status"))
    print()

    # ───────────────────────────────────────────────────────────
    # CASE 11 — Regression: new_order still creates pending_orders
    # ───────────────────────────────────────────────────────────
    print("[case 11] regression — new_order webhook smoke test")
    wh_no = create_webhook(token, "F32 New Order WH", "new_order")
    update_webhook_mapping(token, wh_no["id"], {
        "name":     "customer_name",
        "phone":    "customer_phone",
        "address":  "address",
        "city":     "city",
        "state":    "state",
        "pincode":  "pincode",
    })
    res11 = post_webhook(wh_no["url"], {
        "name":    "Regression Test User",
        "phone":   "9876500011",
        "address": "Test Address Line 1",
        "city":    "Mumbai",
        "state":   "MH",
        "pincode": "400001",
    })
    check(res11["status"] == 200, "HTTP 200", res11)
    check(res11["body"].get("imported") == 1, "imported=1", res11["body"])
    check(res11["body"].get("event_type") == "new_order", "event_type=new_order",
          res11["body"].get("event_type"))

    # Find the new pending order to clean up later
    async def _find_pend():
        client2 = AsyncIOMotorClient(MONGO_URL)
        db2 = client2[DB_NAME]
        d = await db2.pending_orders.find_one(
            {"user_id": admin_id, "customer_phone": "9876500011"}, {"_id": 0}
        )
        client2.close()
        return d
    new_pend = asyncio.run(_find_pend())
    extra_pending_ids = []
    if new_pend:
        extra_pending_ids.append(new_pend["id"])
        check(new_pend.get("customer_name") == "Regression Test User",
              "pending order created with correct name", new_pend.get("customer_name"))
    else:
        check(False, "pending order created (lookup failed)", None)
    print()

    # ───────────────────────────────────────────────────────────
    # CASE 12 — Regression: abandoned_order returns 200 imported=0
    # ───────────────────────────────────────────────────────────
    print("[case 12] regression — abandoned_order returns 200 imported=0")
    wh_ab = create_webhook(token, "F32 Abandoned WH", "abandoned_order")
    update_webhook_mapping(token, wh_ab["id"], {
        "name":  "customer_name",
        "phone": "customer_phone",
    })
    res12 = post_webhook(wh_ab["url"], {
        "name":  "Abandoned Test",
        "phone": "9876500022",
    })
    check(res12["status"] == 200, "HTTP 200", res12)
    body12 = res12["body"]
    check(body12.get("imported") == 0, "imported=0", body12)
    check(body12.get("event_type") == "abandoned_order", "event_type echoed", body12)
    errs12 = body12.get("errors") or []
    check(len(errs12) >= 1, "errors present (placeholder msg)", errs12)
    if errs12:
        check("upcoming release" in errs12[0].lower() or
              "coming" in errs12[0].lower(),
              "errors hint at future release", errs12[0])

    # Confirm no shipment was created for abandoned_order
    async def _count_ab():
        client3 = AsyncIOMotorClient(MONGO_URL)
        db3 = client3[DB_NAME]
        n = await db3.shipments.count_documents(
            {"user_id": admin_id, "customer_phone": "9876500022"}
        )
        client3.close()
        return n
    ab_ship = asyncio.run(_count_ab())
    check(ab_ship == 0, "abandoned_order did NOT create a shipment", ab_ship)
    print()

    # ───────────────────────────────────────────────────────────
    # CLEANUP
    # ───────────────────────────────────────────────────────────
    print("[cleanup] Deleting test webhooks + test docs…")
    try:
        delete_webhook(token, wh_id)
        delete_webhook(token, wh_no["id"])
        delete_webhook(token, wh_ab["id"])
    except Exception as e:
        print(f"  ⚠ webhook cleanup error: {e}")
    asyncio.run(cleanup_db(extra_pending_ids))
    print("  → cleanup done")
    print()

    # ───────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print(f"RESULT: {PASSED} passed, {FAILED} failed")
    print("=" * 60)
    if FAIL_DETAILS:
        print("\nFAILED ASSERTIONS:")
        for msg, ctx in FAIL_DETAILS:
            print(f"  ❌ {msg}\n      ctx={ctx!r}")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
