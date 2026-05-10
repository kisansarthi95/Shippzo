"""
Phase F3.2 (rev-3) — Source-Aware Webhook Matching tests.

Verifies:
  (A) Webhooks accept and persist `source_app` on POST /api/me/webhooks
      and PUT /api/me/webhooks/{id}.
  (B) Source-strict matching for status updates — Shopify webhook does
      NOT touch a Dukaan-tagged pending order with the same external
      order_id.
  (C) Pending-order auto-update: status update on pending order applies
      the new status AND raises needs_review with reason + at fields.
  (D) Legacy fallback: when the strict-match misses on both shipments
      AND pending_orders, retry once without source filter so docs
      created before rev-3 (source_app missing) still get updated.
  (E) PUT /api/me/webhooks/{id} with {"source_app":"meesho"} updates,
      and {"source_app":""} clears.
  (F) Regression: the rev-2 auto-detect suite still passes when the
      webhook has source_app="".

Login: admin@test.com / Admin@12345.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import requests
from motor.motor_asyncio import AsyncIOMotorClient


BACKEND_URL = "https://logistics-hub-740.preview.emergentagent.com"
API = f"{BACKEND_URL}/api"

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASSWORD = "Admin@12345"

MONGO_URL = "mongodb://localhost:27017"
DB_NAME = "test_database"

PASSED = 0
FAILED = 0
FAIL_DETAILS: list[tuple[str, object]] = []

# IDs / tags we use across cases — easy to clean up at the end.
COL_ORDER_ID = "COL-123"
LEGACY_ORDER_ID = "LEG-456"
PEND_AUTO_ORDER_ID = "PND-789"
REGR_SHIP_ID = "shp_f33_regr"
REGR_SHIP_MOID = "OSU-REGR-1"


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


def create_webhook(token: str, name: str, event_type: str, source_app: str = "") -> dict:
    r = requests.post(
        f"{API}/me/webhooks",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name, "event_type": event_type, "source_app": source_app},
        timeout=20,
    )
    r.raise_for_status()
    return r.json()


def list_webhooks(token: str) -> dict:
    r = requests.get(
        f"{API}/me/webhooks",
        headers={"Authorization": f"Bearer {token}"},
        timeout=20,
    )
    r.raise_for_status()
    return r.json()


def update_webhook(token: str, wh_id: str, body: dict) -> dict:
    r = requests.put(
        f"{API}/me/webhooks/{wh_id}",
        headers={"Authorization": f"Bearer {token}"},
        json=body,
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


# ── DB helpers ─────────────────────────────────────────────────────
async def insert_pending_order(user_id: str, *, order_id: str, source_app: str | None,
                                status: str = "Pending",
                                customer_name: str = "Test Customer",
                                customer_phone: str = "9000000000") -> str:
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    pid = f"pend_test_{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).isoformat()
    source_meta: dict = {
        "received_at": now,
        "webhook_name": "test",
        "event_type": "new_order",
    }
    if source_app is not None:
        source_meta["source_app"] = source_app
    doc = {
        "id":                pid,
        "user_id":           user_id,
        "source":            "webhook",
        "status":            status,
        "customer_name":     customer_name,
        "customer_phone":    customer_phone,
        "external_order_id": order_id,
        "order_id":          order_id,
        "master_order_id":   "",
        "created_at":        now,
        "source_meta":       source_meta,
    }
    await db.pending_orders.insert_one(doc)
    client.close()
    return pid


async def fetch_pending(pid: str) -> dict:
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    doc = await db.pending_orders.find_one({"id": pid}, {"_id": 0})
    client.close()
    return doc or {}


async def cleanup_pending(*pids: str) -> None:
    if not pids:
        return
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    await db.pending_orders.delete_many({"id": {"$in": list(pids)}})
    client.close()


# regression shipment helpers (case F)
async def reset_regression_shipment(user_id: str, status: str = "Ready to Ship") -> None:
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    now = datetime.now(timezone.utc).isoformat()
    await db.shipments.delete_many({"id": REGR_SHIP_ID})
    await db.shipments.insert_one({
        "id":              REGR_SHIP_ID,
        "user_id":         user_id,
        "master_order_id": REGR_SHIP_MOID,
        "status":          status,
        "customer_name":   "Regression",
        "customer_phone":  "9000000000",
        "created_at":      now,
    })
    client.close()


async def fetch_regression_shipment() -> dict:
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    doc = await db.shipments.find_one({"id": REGR_SHIP_ID}, {"_id": 0})
    client.close()
    return doc or {}


async def cleanup_regression_shipment() -> None:
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    await db.shipments.delete_many({"id": REGR_SHIP_ID})
    client.close()


# ── main test runner ───────────────────────────────────────────────
async def run_async(token: str, user_id: str) -> None:
    wh_shopify = None
    wh_dukaan = None
    wh_regr = None
    pending_ids: list[str] = []

    try:
        # ─────────────────────────────────────────────────────────────
        # CASE A — create two webhooks with source_app
        # ─────────────────────────────────────────────────────────────
        print("\n[CASE A] POST /api/me/webhooks with source_app=shopify and source_app=dukaan")
        wh_shopify = create_webhook(token, "WH-Shopify", "order_status_update", "shopify")
        check(wh_shopify.get("source_app") == "shopify",
              "A: WH-Shopify created with source_app='shopify'", wh_shopify.get("source_app"))
        check(wh_shopify.get("event_type") == "order_status_update",
              "A: WH-Shopify event_type=order_status_update", wh_shopify.get("event_type"))
        check(bool(wh_shopify.get("url")), "A: WH-Shopify has URL", wh_shopify.get("url"))

        wh_dukaan = create_webhook(token, "WH-Dukaan", "order_status_update", "dukaan")
        check(wh_dukaan.get("source_app") == "dukaan",
              "A: WH-Dukaan created with source_app='dukaan'", wh_dukaan.get("source_app"))

        # GET /api/me/webhooks — both must be returned with source_app
        listing = list_webhooks(token)
        whs = listing.get("webhooks") or []
        ids = {w["id"] for w in whs}
        check(wh_shopify["id"] in ids, "A: GET listing contains WH-Shopify", ids)
        check(wh_dukaan["id"] in ids, "A: GET listing contains WH-Dukaan", ids)
        sa_map = {w["id"]: w.get("source_app") for w in whs}
        check(sa_map.get(wh_shopify["id"]) == "shopify",
              "A: listing reflects source_app='shopify' for WH-Shopify",
              sa_map.get(wh_shopify["id"]))
        check(sa_map.get(wh_dukaan["id"]) == "dukaan",
              "A: listing reflects source_app='dukaan' for WH-Dukaan",
              sa_map.get(wh_dukaan["id"]))

        # ─────────────────────────────────────────────────────────────
        # CASE B — Collision test
        # Two pending_orders with the same external_order_id but
        # different source_app values. Shopify webhook update must
        # touch ONLY the Shopify one.
        # ─────────────────────────────────────────────────────────────
        print(f"\n[CASE B] Insert two pending_orders external_order_id={COL_ORDER_ID} (shopify + dukaan); "
              "POST status update via WH-Shopify")
        pid_sh = await insert_pending_order(user_id, order_id=COL_ORDER_ID, source_app="shopify")
        pid_dk = await insert_pending_order(user_id, order_id=COL_ORDER_ID, source_app="dukaan")
        pending_ids.extend([pid_sh, pid_dk])

        r = post_to_webhook(wh_shopify["url"],
                            {"order_id": COL_ORDER_ID, "status": "shipped"})
        check(r.status_code == 200, "B: HTTP 200", (r.status_code, r.text[:200]))
        j = r.json()
        check(j.get("imported") == 1, "B: imported=1 (only Shopify pending matched)", j)
        check(j.get("event_type") == "order_status_update",
              "B: event_type echoed", j.get("event_type"))

        sh_doc = await fetch_pending(pid_sh)
        dk_doc = await fetch_pending(pid_dk)
        check(sh_doc.get("status") == "Shipped",
              "B: Shopify pending updated to 'Shipped'", sh_doc.get("status"))
        check(sh_doc.get("needs_review") is True,
              "B: Shopify pending got needs_review=true", sh_doc.get("needs_review"))
        check(bool(sh_doc.get("needs_review_reason")),
              "B: Shopify pending has needs_review_reason", sh_doc.get("needs_review_reason"))
        check(bool(sh_doc.get("needs_review_at")),
              "B: Shopify pending has needs_review_at", sh_doc.get("needs_review_at"))
        check(bool(sh_doc.get("dispatched_at")),
              "B: Shopify pending has dispatched_at on Shipped", sh_doc.get("dispatched_at"))

        check(dk_doc.get("status") == "Pending",
              "B: Dukaan pending UNCHANGED (still 'Pending')", dk_doc.get("status"))
        check(not dk_doc.get("needs_review"),
              "B: Dukaan pending has NO needs_review flag", dk_doc.get("needs_review"))
        check(not dk_doc.get("dispatched_at"),
              "B: Dukaan pending has NO dispatched_at", dk_doc.get("dispatched_at"))

        # ─────────────────────────────────────────────────────────────
        # CASE C — Pending auto-update with needs_review flag
        # ─────────────────────────────────────────────────────────────
        print(f"\n[CASE C] Insert pending source_app=shopify external_order_id={PEND_AUTO_ORDER_ID}; "
              "POST status='delivered' via WH-Shopify")
        pid_c = await insert_pending_order(user_id, order_id=PEND_AUTO_ORDER_ID, source_app="shopify")
        pending_ids.append(pid_c)
        r = post_to_webhook(wh_shopify["url"],
                            {"order_id": PEND_AUTO_ORDER_ID, "status": "delivered"})
        check(r.status_code == 200, "C: HTTP 200", (r.status_code, r.text[:200]))
        j = r.json()
        check(j.get("imported") == 1, "C: imported=1", j)
        check(j.get("event_type") == "order_status_update",
              "C: event_type=order_status_update", j)

        c_doc = await fetch_pending(pid_c)
        check(c_doc.get("status") == "Delivered",
              "C: pending status → 'Delivered'", c_doc.get("status"))
        check(c_doc.get("needs_review") is True,
              "C: pending needs_review=true", c_doc.get("needs_review"))
        check(bool(c_doc.get("needs_review_reason")),
              "C: pending needs_review_reason populated", c_doc.get("needs_review_reason"))
        check(bool(c_doc.get("needs_review_at")),
              "C: pending needs_review_at populated", c_doc.get("needs_review_at"))
        check(bool(c_doc.get("delivered_at")),
              "C: pending delivered_at set", c_doc.get("delivered_at"))

        # ─────────────────────────────────────────────────────────────
        # CASE D — Legacy fallback
        # Pending row with source_meta.source_app missing → strict
        # match misses; loose fallback should still update it.
        # ─────────────────────────────────────────────────────────────
        print(f"\n[CASE D] Insert pending with NO source_app on source_meta (legacy doc); "
              "POST status='shipped' via WH-Shopify")
        # Insert without source_app at all
        pid_d = await insert_pending_order(user_id, order_id=LEGACY_ORDER_ID, source_app=None)
        pending_ids.append(pid_d)

        # Sanity check: source_meta.source_app must be missing
        d_pre = await fetch_pending(pid_d)
        check("source_app" not in (d_pre.get("source_meta") or {}),
              "D: legacy pending has no source_meta.source_app",
              d_pre.get("source_meta"))

        r = post_to_webhook(wh_shopify["url"],
                            {"order_id": LEGACY_ORDER_ID, "status": "shipped"})
        check(r.status_code == 200, "D: HTTP 200", (r.status_code, r.text[:200]))
        j = r.json()
        check(j.get("imported") == 1,
              "D: imported=1 (legacy fallback found the row)", j)

        d_doc = await fetch_pending(pid_d)
        check(d_doc.get("status") == "Shipped",
              "D: legacy pending updated to 'Shipped'", d_doc.get("status"))
        check(d_doc.get("needs_review") is True,
              "D: legacy pending got needs_review=true", d_doc.get("needs_review"))

        # ─────────────────────────────────────────────────────────────
        # CASE E — PUT updates source_app, then clears
        # ─────────────────────────────────────────────────────────────
        print("\n[CASE E] PUT WH-Shopify {source_app:'meesho'} then {source_app:''}")
        upd1 = update_webhook(token, wh_shopify["id"], {"source_app": "meesho"})
        check(upd1.get("source_app") == "meesho",
              "E: PUT source_app=meesho persisted", upd1.get("source_app"))

        # Verify via list as well
        listing2 = list_webhooks(token)
        sa2 = {w["id"]: w.get("source_app") for w in (listing2.get("webhooks") or [])}
        check(sa2.get(wh_shopify["id"]) == "meesho",
              "E: GET listing now reflects source_app='meesho'",
              sa2.get(wh_shopify["id"]))

        upd2 = update_webhook(token, wh_shopify["id"], {"source_app": ""})
        check(upd2.get("source_app") == "",
              "E: PUT source_app='' clears the value", upd2.get("source_app"))

        # Restore original "shopify" so following invariants hold
        update_webhook(token, wh_shopify["id"], {"source_app": "shopify"})

        # ─────────────────────────────────────────────────────────────
        # CASE F — Regression: rev-2 auto-detect suite with source_app=""
        # Use a NEW webhook with source_app="" so legacy matching is in
        # effect. Insert a regression shipment and run a quick subset
        # of the rev-2 cases.
        # ─────────────────────────────────────────────────────────────
        print("\n[CASE F] Regression: legacy webhook (source_app='') still auto-detects status updates")
        wh_regr = create_webhook(token, "F33-Regression-WH", "order_status_update", "")
        check(wh_regr.get("source_app") == "",
              "F: regression WH created with source_app=''", wh_regr.get("source_app"))

        await reset_regression_shipment(user_id, "Ready to Ship")

        # F1: order_id + status:'shipped'
        r = post_to_webhook(wh_regr["url"], {"order_id": REGR_SHIP_MOID, "status": "shipped"})
        check(r.status_code == 200 and r.json().get("imported") == 1,
              "F1: legacy order_id+status auto-detect → imported=1",
              (r.status_code, r.text[:200]))
        ship = await fetch_regression_shipment()
        check(ship.get("status") == "Shipped",
              "F1: shipment status='Shipped'", ship.get("status"))
        check(bool(ship.get("dispatched_at")),
              "F1: dispatched_at set", ship.get("dispatched_at"))

        # F2: alternate id + state:'delivered'
        r = post_to_webhook(wh_regr["url"], {"id": REGR_SHIP_MOID, "state": "delivered"})
        check(r.status_code == 200 and r.json().get("imported") == 1,
              "F2: id+state auto-detect → imported=1",
              (r.status_code, r.text[:200]))
        ship = await fetch_regression_shipment()
        check(ship.get("status") == "Delivered",
              "F2: shipment status='Delivered'", ship.get("status"))
        check(bool(ship.get("delivered_at")),
              "F2: delivered_at set", ship.get("delivered_at"))

        # F3: orderID + fulfillment_status:'returned' + event_at
        r = post_to_webhook(wh_regr["url"], {
            "orderID": REGR_SHIP_MOID,
            "fulfillment_status": "returned",
            "event_at": "2026-02-01T10:00:00Z",
        })
        check(r.status_code == 200 and r.json().get("imported") == 1,
              "F3: orderID+fulfillment_status+event_at → imported=1",
              (r.status_code, r.text[:200]))
        ship = await fetch_regression_shipment()
        check(ship.get("status") == "Returned",
              "F3: shipment status='Returned'", ship.get("status"))
        ret_at = ship.get("returned_at") or ""
        check(ret_at.startswith("2026-02-01"),
              "F3: returned_at honours event_at",
              ret_at)

    finally:
        # ─────────────────────────────────────────────────────────────
        # Cleanup
        # ─────────────────────────────────────────────────────────────
        print("\n[Cleanup] Deleting test webhooks + pending orders + shipment")
        for wh in (wh_shopify, wh_dukaan, wh_regr):
            if wh:
                try:
                    delete_webhook(token, wh["id"])
                    print(f"  - deleted webhook {wh['id']} ({wh.get('name')})")
                except Exception as e:
                    print(f"  - failed to delete webhook {wh.get('id')}: {e}")
        try:
            await cleanup_pending(*pending_ids)
            print(f"  - deleted {len(pending_ids)} pending order(s)")
        except Exception as e:
            print(f"  - cleanup_pending error: {e}")
        try:
            await cleanup_regression_shipment()
            print(f"  - deleted regression shipment {REGR_SHIP_ID}")
        except Exception as e:
            print(f"  - cleanup regression shipment error: {e}")


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
