"""
Phase F3.2 (rev-2) — Order Status Update auto-detection tests.

Verifies that the public webhook ingest at /api/webhook/orders/{secret}
auto-detects order_id / status / timestamp from common payload key names
when the user has NOT configured an explicit field mapping for an
`order_status_update` webhook. Also verifies:

  • User-configured mapping STILL wins (auto-detect only fills gaps).
  • Auto-detect can't find order_id → 200 imported=0 with friendly error.
  • new_order event_type is unaffected (still REQUIRES a mapping).

Login: admin@test.com / Admin@12345.
"""
from __future__ import annotations

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

TEST_SHIP_ID = "shp_f32rev2"
TEST_SHIP_MOID = "OSU-AUTO-1"

PASSED = 0
FAILED = 0
FAIL_DETAILS: list[tuple[str, object]] = []


def check(cond: bool, msg: str, ctx: object = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  PASS  {msg}")
    else:
        FAILED += 1
        FAIL_DETAILS.append((msg, ctx))
        print(f"  FAIL  {msg}  ctx={ctx!r}")


# ── helpers ─────────────────────────────────────────────────────────
def login() -> tuple[str, str]:
    r = requests.post(
        f"{API}/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=20,
    )
    r.raise_for_status()
    j = r.json()
    return j["token"], j["id"]


async def reset_test_shipment(user_id: str, status: str = "Ready to Ship") -> None:
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    now = datetime.now(timezone.utc).isoformat()
    await db.shipments.delete_many({"id": TEST_SHIP_ID})
    await db.shipments.insert_one({
        "id":              TEST_SHIP_ID,
        "user_id":          user_id,
        "master_order_id": TEST_SHIP_MOID,
        "status":          status,
        "customer_name":   "Auto Test",
        "customer_phone":  "9000000000",
        "created_at":      now,
    })
    client.close()


async def fetch_shipment() -> dict:
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    doc = await db.shipments.find_one({"id": TEST_SHIP_ID}, {"_id": 0})
    client.close()
    return doc or {}


async def cleanup() -> None:
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    await db.shipments.delete_many({"id": TEST_SHIP_ID})
    client.close()


# ── webhook helpers ─────────────────────────────────────────────────
def create_webhook(token: str, name: str, event_type: str) -> dict:
    r = requests.post(
        f"{API}/me/webhooks",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name, "event_type": event_type},
        timeout=20,
    )
    r.raise_for_status()
    return r.json()


def update_mapping(token: str, wh_id: str, mapping: dict) -> dict:
    r = requests.put(
        f"{API}/me/webhooks/{wh_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"mapping": mapping},
        timeout=20,
    )
    r.raise_for_status()
    return r.json()


def delete_webhook(token: str, wh_id: str) -> None:
    requests.delete(
        f"{API}/me/webhooks/{wh_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=20,
    )


def post_to_webhook(url: str, body) -> requests.Response:
    return requests.post(url, json=body, timeout=20)


# ── main test runner ───────────────────────────────────────────────
async def run_async(token: str, user_id: str) -> None:
    osu_wh = None
    new_order_wh = None
    try:
        # CASE 1: insert shipment
        print("\n[Setup] Insert test shipment shp_f32rev2 (status='Ready to Ship')")
        await reset_test_shipment(user_id, "Ready to Ship")
        doc = await fetch_shipment()
        check(doc.get("master_order_id") == TEST_SHIP_MOID,
              "Setup: shipment exists with master_order_id=OSU-AUTO-1", doc.get("master_order_id"))

        # CASE 2: Create webhook with event_type=order_status_update, NO mapping
        print("\n[CASE 2] Create OSU webhook with no mapping; POST {order_id, status:'shipped'}")
        osu_wh = create_webhook(token, "F32rev2 OSU WH", "order_status_update")
        check(osu_wh.get("event_type") == "order_status_update",
              "CASE 2: webhook created with event_type=order_status_update", osu_wh.get("event_type"))
        check(osu_wh.get("mapping") == {},
              "CASE 2: webhook has empty mapping by default", osu_wh.get("mapping"))
        check(bool(osu_wh.get("url")),
              "CASE 2: public URL returned", osu_wh.get("url"))

        wh_url = osu_wh["url"]
        wh_id = osu_wh["id"]

        # POST baseline payload — order_id+status keys
        r = post_to_webhook(wh_url, {"order_id": TEST_SHIP_MOID, "status": "shipped"})
        check(r.status_code == 200, "CASE 2: HTTP 200", (r.status_code, r.text[:200]))
        j = r.json()
        check(j.get("imported") == 1, "CASE 2: imported=1 (auto-detect order_id/status)", j)
        check(j.get("not_found") == 0, "CASE 2: not_found=0", j)
        check(j.get("event_type") == "order_status_update", "CASE 2: event_type echoed", j)

        doc = await fetch_shipment()
        check(doc.get("status") == "Shipped",
              "CASE 2: shipment.status normalised to 'Shipped'", doc.get("status"))
        check(bool(doc.get("dispatched_at")),
              "CASE 2: dispatched_at set on Shipped", doc.get("dispatched_at"))

        # CASE 3: alternate field names — id + state
        print("\n[CASE 3] POST {id:'OSU-AUTO-1', state:'delivered'}")
        r = post_to_webhook(wh_url, {"id": TEST_SHIP_MOID, "state": "delivered"})
        check(r.status_code == 200, "CASE 3: HTTP 200", (r.status_code, r.text[:200]))
        j = r.json()
        check(j.get("imported") == 1, "CASE 3: imported=1 (id+state auto-detected)", j)
        doc = await fetch_shipment()
        check(doc.get("status") == "Delivered",
              "CASE 3: shipment.status='Delivered'", doc.get("status"))
        check(bool(doc.get("delivered_at")),
              "CASE 3: delivered_at set", doc.get("delivered_at"))

        # CASE 4: Shopify-style — orderID + fulfillment_status + event_at
        print("\n[CASE 4] POST {orderID, fulfillment_status:'returned', event_at:'2026-02-01T...'}")
        r = post_to_webhook(wh_url, {
            "orderID": TEST_SHIP_MOID,
            "fulfillment_status": "returned",
            "event_at": "2026-02-01T10:00:00Z",
        })
        check(r.status_code == 200, "CASE 4: HTTP 200", (r.status_code, r.text[:200]))
        j = r.json()
        check(j.get("imported") == 1, "CASE 4: imported=1 (camelCase + fulfillment_status + event_at)", j)
        doc = await fetch_shipment()
        check(doc.get("status") == "Returned",
              "CASE 4: shipment.status='Returned'", doc.get("status"))
        ret_at = doc.get("returned_at") or ""
        check(ret_at.startswith("2026-02-01"),
              "CASE 4: returned_at starts with '2026-02-01' (event_at honoured)", ret_at)
        sua = doc.get("status_updated_at") or ""
        check(sua.startswith("2026-02-01"),
              "CASE 4: status_updated_at also reflects event_at", sua)

        # CASE 5: partial user mapping — only order_id, status auto-detected
        print("\n[CASE 5] PUT mapping={'order_id':'order_id'} (no status); POST shipped")
        # Reset status so we can verify the change clearly
        await reset_test_shipment(user_id, "Ready to Ship")
        update_mapping(token, wh_id, {"order_id": "order_id"})
        r = post_to_webhook(wh_url, {"order_id": TEST_SHIP_MOID, "status": "shipped"})
        check(r.status_code == 200, "CASE 5: HTTP 200", (r.status_code, r.text[:200]))
        j = r.json()
        check(j.get("imported") == 1,
              "CASE 5: imported=1 (status auto-detected even though mapping omits it)", j)
        doc = await fetch_shipment()
        check(doc.get("status") == "Shipped",
              "CASE 5: shipment.status='Shipped' (auto-detected)", doc.get("status"))

        # CASE 6: Power-user override — custom dotted-path mapping
        print("\n[CASE 6] PUT mapping={'event.payload.order.id':'order_id', 'event.payload.status':'status'}; POST nested")
        update_mapping(token, wh_id, {
            "event.payload.order.id": "order_id",
            "event.payload.status":   "status",
        })
        r = post_to_webhook(wh_url, {
            "event": {"payload": {"order": {"id": TEST_SHIP_MOID}, "status": "delivered"}}
        })
        check(r.status_code == 200, "CASE 6: HTTP 200", (r.status_code, r.text[:200]))
        j = r.json()
        check(j.get("imported") == 1,
              "CASE 6: imported=1 (user-mapped dotted paths win over auto)", j)
        doc = await fetch_shipment()
        check(doc.get("status") == "Delivered",
              "CASE 6: shipment.status='Delivered' (via user mapping)", doc.get("status"))

        # CASE 7: Auto-detect can't find order_id at all
        print("\n[CASE 7] Reset mapping to empty; POST {random_key, another}")
        update_mapping(token, wh_id, {})
        r = post_to_webhook(wh_url, {"random_key": "X", "another": "Y"})
        check(r.status_code == 200, "CASE 7: HTTP 200", (r.status_code, r.text[:200]))
        j = r.json()
        check(j.get("imported") == 0, "CASE 7: imported=0", j)
        errs = j.get("errors") or []
        check(len(errs) >= 1, "CASE 7: at least one error returned", errs)
        joined = " | ".join(errs).lower()
        check("couldn't find an order id" in joined or "couldn’t find an order id" in joined or "find an order id" in joined,
              "CASE 7: error mentions \"couldn't find an order id\"", errs)

        # CASE 8: Regression — new_order webhook still REQUIRES mapping
        print("\n[CASE 8] new_order webhook with empty mapping; POST any payload")
        new_order_wh = create_webhook(token, "F32rev2 NewOrder WH", "new_order")
        check(new_order_wh.get("mapping") == {},
              "CASE 8: new_order webhook starts with empty mapping", new_order_wh.get("mapping"))
        r = post_to_webhook(new_order_wh["url"], {
            "name": "Auto Detect Should Not Apply",
            "phone": "9000000099",
            "address": "Some Address",
            "city": "Somewhere",
            "state": "MH",
            "pincode": "400001",
        })
        check(r.status_code == 200, "CASE 8: HTTP 200", (r.status_code, r.text[:200]))
        j = r.json()
        check(j.get("imported") == 0,
              "CASE 8: imported=0 (auto-detect MUST NOT apply for new_order)", j)
        errs = j.get("errors") or []
        joined = " | ".join(errs).lower()
        check("no field mapping is configured" in joined or "field mapping" in joined,
              "CASE 8: error message about \"no field mapping is configured\"", errs)

    finally:
        # Cleanup
        print("\n[Cleanup] Deleting test webhooks + shipment")
        if osu_wh:
            try:
                delete_webhook(token, osu_wh["id"])
                print(f"  - deleted OSU webhook {osu_wh['id']}")
            except Exception as e:
                print(f"  - failed to delete OSU webhook: {e}")
        if new_order_wh:
            try:
                delete_webhook(token, new_order_wh["id"])
                print(f"  - deleted new_order webhook {new_order_wh['id']}")
            except Exception as e:
                print(f"  - failed to delete new_order webhook: {e}")
        try:
            await cleanup()
            print(f"  - deleted test shipment {TEST_SHIP_ID}")
        except Exception as e:
            print(f"  - cleanup error: {e}")


def main() -> None:
    print(f"Backend: {BACKEND_URL}")
    print(f"Login as: {ADMIN_EMAIL}")
    token, user_id = login()
    print(f"Logged in. user_id={user_id}")

    asyncio.run(run_async(token, user_id))

    print("\n" + "=" * 70)
    print(f"RESULT: {PASSED} passed, {FAILED} failed")
    if FAIL_DETAILS:
        print("\nFailures:")
        for msg, ctx in FAIL_DETAILS:
            print(f"  - {msg}  ctx={ctx!r}")
    print("=" * 70)
    if FAILED:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
