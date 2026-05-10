"""
Phase F3.3 — Abandoned Carts + Customers webhook event processing tests.

Verifies the full backend flow for the two new event-types:
  • abandoned_order → db.abandoned_carts (+ recover/dismiss/list/stats endpoints)
  • customer_created / customer_updated → db.customers (+ list/stats endpoints)

Login: admin@test.com / Admin@12345.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

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

CREATED_WEBHOOK_IDS: set[str] = set()
CREATED_PENDING_ORDER_IDS: set[str] = set()


def check(cond: bool, msg: str, ctx: object = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  PASS  {msg}")
    else:
        FAILED += 1
        FAIL_DETAILS.append((msg, ctx))
        print(f"  FAIL  {msg}  ctx={ctx!r}")


def login() -> tuple[str, str]:
    r = requests.post(
        f"{API}/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=20,
    )
    r.raise_for_status()
    j = r.json()
    return j["token"], j["id"]


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def create_webhook(token: str, name: str, event_type: str, source_app: str = "") -> dict:
    r = requests.post(
        f"{API}/me/webhooks",
        headers=auth(token),
        json={"name": name, "event_type": event_type, "source_app": source_app},
        timeout=20,
    )
    r.raise_for_status()
    j = r.json()
    if j.get("id"):
        CREATED_WEBHOOK_IDS.add(j["id"])
    return j


def delete_webhook(token: str, wh_id: str) -> None:
    try:
        requests.delete(
            f"{API}/me/webhooks/{wh_id}",
            headers=auth(token),
            timeout=20,
        )
    except Exception:
        pass


def post_to_webhook(url: str, body) -> requests.Response:
    return requests.post(url, json=body, timeout=20)


# ── abandoned-cart endpoint helpers ─────────────────────────────────
def list_carts(token: str, **params) -> dict:
    r = requests.get(
        f"{API}/me/abandoned-carts",
        headers=auth(token),
        params=params,
        timeout=20,
    )
    r.raise_for_status()
    return r.json()


def cart_stats(token: str) -> dict:
    r = requests.get(
        f"{API}/me/abandoned-carts/stats",
        headers=auth(token),
        timeout=20,
    )
    r.raise_for_status()
    return r.json()


def recover_cart(token: str, cart_id: str) -> requests.Response:
    return requests.post(
        f"{API}/me/abandoned-carts/{cart_id}/recover",
        headers=auth(token),
        timeout=20,
    )


def dismiss_cart(token: str, cart_id: str) -> requests.Response:
    return requests.post(
        f"{API}/me/abandoned-carts/{cart_id}/dismiss",
        headers=auth(token),
        timeout=20,
    )


def delete_cart(token: str, cart_id: str) -> None:
    try:
        requests.delete(
            f"{API}/me/abandoned-carts/{cart_id}",
            headers=auth(token),
            timeout=20,
        )
    except Exception:
        pass


# ── customer endpoint helpers ───────────────────────────────────────
def list_customers(token: str, **params) -> dict:
    r = requests.get(
        f"{API}/me/customers",
        headers=auth(token),
        params=params,
        timeout=20,
    )
    r.raise_for_status()
    return r.json()


def customer_stats(token: str) -> dict:
    r = requests.get(
        f"{API}/me/customers/stats",
        headers=auth(token),
        timeout=20,
    )
    r.raise_for_status()
    return r.json()


def get_customer(token: str, cid: str) -> requests.Response:
    return requests.get(
        f"{API}/me/customers/{cid}",
        headers=auth(token),
        timeout=20,
    )


def delete_customer(token: str, cid: str) -> None:
    try:
        requests.delete(
            f"{API}/me/customers/{cid}",
            headers=auth(token),
            timeout=20,
        )
    except Exception:
        pass


# ── DB cleanup helpers ──────────────────────────────────────────────
async def db_cleanup_abandoned(user_id: str, ids: list[str]) -> int:
    cli = AsyncIOMotorClient(MONGO_URL)
    db = cli[DB_NAME]
    n = 0
    if ids:
        res = await db.abandoned_carts.delete_many({"user_id": user_id, "id": {"$in": ids}})
        n = res.deleted_count
    cli.close()
    return n


async def db_cleanup_customers(user_id: str, ids: list[str]) -> int:
    cli = AsyncIOMotorClient(MONGO_URL)
    db = cli[DB_NAME]
    n = 0
    if ids:
        res = await db.customers.delete_many({"user_id": user_id, "id": {"$in": ids}})
        n = res.deleted_count
    cli.close()
    return n


async def db_cleanup_pending_orders(user_id: str, ids: list[str]) -> int:
    cli = AsyncIOMotorClient(MONGO_URL)
    db = cli[DB_NAME]
    n = 0
    if ids:
        res = await db.pending_orders.delete_many({"user_id": user_id, "id": {"$in": ids}})
        n = res.deleted_count
    cli.close()
    return n


async def db_check_pending_order(user_id: str, pending_id: str) -> Optional[Dict[str, Any]]:
    cli = AsyncIOMotorClient(MONGO_URL)
    db = cli[DB_NAME]
    doc = await db.pending_orders.find_one({"id": pending_id, "user_id": user_id}, {"_id": 0})
    cli.close()
    return doc


# ────────────────────────────────────────────────────────────────────
# ABANDONED CARTS — A–H
# ────────────────────────────────────────────────────────────────────
def run_abandoned_cart_tests(token: str, user_id: str) -> None:
    print("\n=== ABANDONED CARTS — Phase F3.3 ===")

    created_cart_ids: list[str] = []

    # Pre-clean: remove any existing abandoned_cart rows for this user from
    # earlier failed runs so totals/counts are predictable.
    async def _preclean():
        cli = AsyncIOMotorClient(MONGO_URL)
        db = cli[DB_NAME]
        await db.abandoned_carts.delete_many({"user_id": user_id})
        cli.close()
    asyncio.run(_preclean())

    # ── (A) Create webhook + ingest Shopify-style payload ──
    print("\n--- CASE A: Create webhook abandoned_order/shopify and ingest ---")
    wh_shop = create_webhook(token, "F33-AC-Shopify", "abandoned_order", "shopify")
    wh_shop_id = wh_shop["id"]
    wh_shop_url = wh_shop["url"]
    check(bool(wh_shop_url), "CASE A: webhook URL returned", wh_shop)
    check(wh_shop.get("source_app") == "shopify", "CASE A: source_app=shopify",
          wh_shop.get("source_app"))

    EXT_CART_ID = f"ac-shop-{uuid.uuid4().hex[:8]}"
    shopify_payload = {
        "id": EXT_CART_ID,
        "abandoned_checkout_url": "https://shop.example.com/checkout/abc123/recover",
        "total_price": "1499.00",
        "subtotal_price": "1399.00",
        "currency": "INR",
        "abandoned_checkout_at": "2026-05-09T14:25:33Z",
        "customer": {
            "id": "cust-9988",
            "first_name": "Riya",
            "last_name": "Sharma",
            "email": "riya.sharma@example.com",
            "phone": "+919812345670",
        },
        "shipping_address": {
            "address1": "12 MG Road",
            "city": "Pune",
            "province": "Maharashtra",
            "zip": "411001",
            "phone": "+919812345670",
        },
        "billing_address": {
            "address1": "12 MG Road",
            "city": "Pune",
            "province": "Maharashtra",
            "zip": "411001",
        },
        "line_items": [
            {"title": "Saree — Indigo Cotton", "quantity": 1, "price": "1299"},
            {"title": "Bag — Jute Tote", "quantity": 1, "price": "200"},
        ],
    }
    r = post_to_webhook(wh_shop_url, shopify_payload)
    check(r.status_code == 200, "CASE A: ingest returns 200", r.status_code)
    j = r.json()
    check(j.get("imported") == 1, "CASE A: imported=1", j)
    check(j.get("event_type") == "abandoned_order", "CASE A: event_type=abandoned_order", j)
    check(j.get("skipped", 0) == 0, "CASE A: skipped=0", j)

    lst = list_carts(token)
    check(lst.get("total") == 1, "CASE A: list total=1", lst)
    check(len(lst.get("carts", [])) == 1, "CASE A: 1 cart in list", lst)
    if lst.get("carts"):
        c = lst["carts"][0]
        created_cart_ids.append(c["id"])
        check(c.get("source_app") == "shopify", "CASE A: cart.source_app=shopify",
              c.get("source_app"))
        check(abs(float(c.get("cart_value", 0)) - 1499.0) < 0.01,
              "CASE A: cart_value=1499", c.get("cart_value"))
        check("Riya" in (c.get("customer_name") or ""),
              "CASE A: customer_name has 'Riya'", c.get("customer_name"))
        check("Sharma" in (c.get("customer_name") or ""),
              "CASE A: customer_name has 'Sharma'", c.get("customer_name"))
        check(c.get("customer_email") == "riya.sharma@example.com",
              "CASE A: customer_email", c.get("customer_email"))
        check("9812345670" in (c.get("customer_phone") or ""),
              "CASE A: customer_phone present", c.get("customer_phone"))
        check(bool(c.get("items_summary")),
              "CASE A: items_summary populated", c.get("items_summary"))
        check(bool(c.get("abandoned_at")),
              "CASE A: abandoned_at populated", c.get("abandoned_at"))
        check(bool(c.get("recovery_url")),
              "CASE A: recovery_url populated", c.get("recovery_url"))
        check(c.get("external_cart_id") == EXT_CART_ID,
              "CASE A: external_cart_id matches", c.get("external_cart_id"))
        check(c.get("status") == "abandoned",
              "CASE A: status=abandoned", c.get("status"))

    # ── (B) Same payload again → upsert (count remains 1) ──
    print("\n--- CASE B: Repeat payload → upsert (count stays 1) ---")
    r2 = post_to_webhook(wh_shop_url, shopify_payload)
    j2 = r2.json()
    check(j2.get("imported") == 1, "CASE B: imported=1 (upsert)", j2)
    lst2 = list_carts(token)
    check(lst2.get("total") == 1, "CASE B: total still 1 (upsert worked)", lst2)

    # ── (C) Stats endpoint ──
    print("\n--- CASE C: Stats endpoint ---")
    s = cart_stats(token)
    check(s.get("abandoned") == 1, "CASE C: stats.abandoned=1", s)
    check(s.get("recovered") == 0, "CASE C: stats.recovered=0", s)
    check(abs(float(s.get("total_value", 0)) - 1499.0) < 0.01,
          "CASE C: total_value=1499", s.get("total_value"))

    # ── (D) Recover ──
    print("\n--- CASE D: Recover abandoned cart ---")
    if not created_cart_ids:
        check(False, "CASE D: no cart available to recover", lst)
        return
    cart_id = created_cart_ids[0]
    rec = recover_cart(token, cart_id)
    check(rec.status_code == 200, "CASE D: recover returns 200", rec.status_code)
    rj = rec.json()
    check(rj.get("ok") is True, "CASE D: ok=True", rj)
    pending_id = rj.get("pending_order_id")
    master_oid = rj.get("master_order_id")
    check(bool(pending_id), "CASE D: pending_order_id returned", rj)
    check(bool(master_oid), "CASE D: master_order_id returned", rj)
    if pending_id:
        CREATED_PENDING_ORDER_IDS.add(pending_id)
        # Verify pending_orders doc exists with cart's customer + amount
        pdoc = asyncio.run(db_check_pending_order(user_id, pending_id))
        check(pdoc is not None, "CASE D: pending_orders doc created", pdoc)
        if pdoc:
            check("Riya" in (pdoc.get("customer_name") or ""),
                  "CASE D: pending has customer_name", pdoc.get("customer_name"))
            check(abs(float(pdoc.get("amount") or 0) - 1499.0) < 0.01,
                  "CASE D: pending amount=1499", pdoc.get("amount"))
            check(pdoc.get("source") == "abandoned_cart",
                  "CASE D: pending source=abandoned_cart", pdoc.get("source"))

    # Verify cart now status=recovered
    lst3 = list_carts(token)
    cart_after = next((c for c in lst3.get("carts", []) if c["id"] == cart_id), None)
    check(cart_after and cart_after.get("status") == "recovered",
          "CASE D: cart.status=recovered after recover",
          cart_after.get("status") if cart_after else None)
    check(cart_after and cart_after.get("pending_order_id") == pending_id,
          "CASE D: cart.pending_order_id matches",
          cart_after.get("pending_order_id") if cart_after else None)

    # ── (E) Recover again → already_recovered=true, same pending_order_id ──
    print("\n--- CASE E: Recover again → already_recovered=true ---")
    rec2 = recover_cart(token, cart_id)
    check(rec2.status_code == 200, "CASE E: 2nd recover returns 200", rec2.status_code)
    rj2 = rec2.json()
    check(rj2.get("ok") is True, "CASE E: ok=True", rj2)
    check(rj2.get("already_recovered") is True,
          "CASE E: already_recovered=true", rj2)
    check(rj2.get("pending_order_id") == pending_id,
          "CASE E: same pending_order_id returned", rj2)

    # ── (F) Filter by source_app ──
    print("\n--- CASE F: Source filter ---")
    lst_dukaan = list_carts(token, source_app="dukaan")
    check(lst_dukaan.get("total") == 0,
          "CASE F: source_app=dukaan returns 0", lst_dukaan)
    lst_shop = list_carts(token, source_app="shopify")
    check(lst_shop.get("total") == 1,
          "CASE F: source_app=shopify returns 1", lst_shop)

    # ── (G) Dismiss on a fresh cart ──
    print("\n--- CASE G: Dismiss fresh cart ---")
    EXT_CART_2 = f"ac-shop-dismiss-{uuid.uuid4().hex[:8]}"
    payload_g = dict(shopify_payload)
    payload_g["id"] = EXT_CART_2
    payload_g["customer"] = {
        "id": "cust-1010",
        "first_name": "Aman",
        "last_name": "Verma",
        "email": "aman.verma@example.com",
        "phone": "+919811112222",
    }
    payload_g["total_price"] = "599.00"
    rg = post_to_webhook(wh_shop_url, payload_g)
    check(rg.json().get("imported") == 1, "CASE G: imported new cart", rg.json())
    lst_g = list_carts(token, status="abandoned")
    cart2 = next((c for c in lst_g.get("carts", []) if c.get("external_cart_id") == EXT_CART_2), None)
    check(cart2 is not None, "CASE G: 2nd cart found", lst_g)
    if cart2:
        cart2_id = cart2["id"]
        created_cart_ids.append(cart2_id)
        dr = dismiss_cart(token, cart2_id)
        check(dr.status_code == 200, "CASE G: dismiss returns 200", dr.status_code)
        check(dr.json().get("ok") is True, "CASE G: dismiss ok=True", dr.json())
        # Verify status=dismissed
        lst_g2 = list_carts(token)
        cart2_after = next((c for c in lst_g2.get("carts", []) if c["id"] == cart2_id), None)
        check(cart2_after and cart2_after.get("status") == "dismissed",
              "CASE G: cart.status=dismissed",
              cart2_after.get("status") if cart2_after else None)
        check(cart2_after and bool(cart2_after.get("dismissed_at")),
              "CASE G: dismissed_at set",
              cart2_after.get("dismissed_at") if cart2_after else None)

    # ── (H) Empty payload — no identifying field ──
    print("\n--- CASE H: Webhook payload missing all identifying fields ---")
    bad_payload = {
        "currency": "INR",
        "total_price": "100",
        "abandoned_checkout_at": "2026-05-09T15:00:00Z",
    }
    rh = post_to_webhook(wh_shop_url, bad_payload)
    jh = rh.json()
    check(jh.get("skipped") == 1, "CASE H: skipped=1", jh)
    check(jh.get("imported") == 0, "CASE H: imported=0", jh)
    errs = jh.get("errors") or []
    has_msg = any("couldn't locate cart_id" in str(e) for e in errs)
    check(has_msg, "CASE H: error contains 'couldn't locate cart_id'", errs)

    # ── Cleanup created carts + pending orders + webhook ──
    print("\n--- ABANDONED CART cleanup ---")
    n_carts = asyncio.run(db_cleanup_abandoned(user_id, created_cart_ids))
    print(f"  cleanup: deleted {n_carts} abandoned_carts")
    n_pending = asyncio.run(db_cleanup_pending_orders(
        user_id, list(CREATED_PENDING_ORDER_IDS),
    ))
    print(f"  cleanup: deleted {n_pending} pending_orders")
    delete_webhook(token, wh_shop_id)
    print(f"  cleanup: deleted webhook {wh_shop_id}")


# ────────────────────────────────────────────────────────────────────
# CUSTOMERS — A–H
# ────────────────────────────────────────────────────────────────────
def run_customer_tests(token: str, user_id: str) -> None:
    print("\n=== CUSTOMERS — Phase F3.3 ===")

    # Pre-clean
    async def _preclean():
        cli = AsyncIOMotorClient(MONGO_URL)
        db = cli[DB_NAME]
        await db.customers.delete_many({"user_id": user_id})
        cli.close()
    asyncio.run(_preclean())

    customer_ids: list[str] = []

    # ── (A) customer_created webhook (shopify) ──
    print("\n--- CASE A: customer_created shopify ingest ---")
    wh_cust_shop = create_webhook(token, "F33-Cust-Shopify", "customer_created", "shopify")
    wh_cust_shop_id = wh_cust_shop["id"]
    wh_cust_shop_url = wh_cust_shop["url"]
    check(bool(wh_cust_shop_url), "CASE A: customer webhook URL", wh_cust_shop)
    check(wh_cust_shop.get("source_app") == "shopify",
          "CASE A: customer wh source_app=shopify", wh_cust_shop.get("source_app"))

    EXT_CUST_ID = f"cust-{uuid.uuid4().hex[:8]}"
    cust_payload = {
        "id": EXT_CUST_ID,
        "first_name": "Priya",
        "last_name": "Patel",
        "email": "priya.patel@example.com",
        "phone": "+919876543210",
        "orders_count": 3,
        "total_spent": "5499.00",
        "default_address": {
            "address1": "44 Park Street",
            "city": "Mumbai",
            "province": "Maharashtra",
            "zip": "400001",
        },
        "created_at": "2025-12-01T10:00:00Z",
        "updated_at": "2026-04-15T11:11:11Z",
    }
    r = post_to_webhook(wh_cust_shop_url, cust_payload)
    check(r.status_code == 200, "CASE A: customer ingest 200", r.status_code)
    j = r.json()
    check(j.get("imported") == 1, "CASE A: imported=1", j)
    check(j.get("event_type") == "customer_created",
          "CASE A: event_type=customer_created", j)

    lst = list_customers(token)
    check(lst.get("total") == 1, "CASE A: customers total=1", lst)
    if lst.get("customers"):
        c = lst["customers"][0]
        customer_ids.append(c["id"])
        check(c.get("source_app") == "shopify", "CASE A: source_app=shopify",
              c.get("source_app"))
        check(c.get("external_customer_id") == EXT_CUST_ID,
              "CASE A: external_customer_id", c.get("external_customer_id"))
        check("Priya" in (c.get("customer_name") or ""),
              "CASE A: customer_name has Priya", c.get("customer_name"))
        check("Patel" in (c.get("customer_name") or ""),
              "CASE A: customer_name has Patel", c.get("customer_name"))
        check(c.get("customer_email") == "priya.patel@example.com",
              "CASE A: email", c.get("customer_email"))
        check("9876543210" in (c.get("customer_phone") or ""),
              "CASE A: phone", c.get("customer_phone"))
        check(int(c.get("orders_count") or 0) == 3,
              "CASE A: orders_count=3", c.get("orders_count"))
        check(abs(float(c.get("total_spent") or 0) - 5499.0) < 0.01,
              "CASE A: total_spent=5499", c.get("total_spent"))
        check(c.get("last_event") == "customer_created",
              "CASE A: last_event=customer_created", c.get("last_event"))

    # ── (B) customer_updated for SAME ext_cust_id ──
    print("\n--- CASE B: customer_updated upsert for same ext_cust_id ---")
    wh_cust_shop_upd = create_webhook(
        token, "F33-Cust-Shopify-Upd", "customer_updated", "shopify",
    )
    wh_upd_id = wh_cust_shop_upd["id"]
    wh_upd_url = wh_cust_shop_upd["url"]
    cust_updated_payload = dict(cust_payload)
    cust_updated_payload["orders_count"] = 5
    cust_updated_payload["total_spent"] = "8999.00"
    cust_updated_payload["updated_at"] = "2026-05-10T09:00:00Z"
    rb = post_to_webhook(wh_upd_url, cust_updated_payload)
    jb = rb.json()
    check(jb.get("imported") == 1, "CASE B: imported=1 (upsert)", jb)
    check(jb.get("event_type") == "customer_updated",
          "CASE B: event_type=customer_updated", jb)
    lst_b = list_customers(token)
    check(lst_b.get("total") == 1,
          "CASE B: total still 1 after update (upsert)", lst_b)
    if lst_b.get("customers"):
        cb = lst_b["customers"][0]
        check(cb.get("last_event") == "customer_updated",
              "CASE B: last_event=customer_updated", cb.get("last_event"))
        check(int(cb.get("orders_count") or 0) == 5,
              "CASE B: orders_count=5", cb.get("orders_count"))
        check(abs(float(cb.get("total_spent") or 0) - 8999.0) < 0.01,
              "CASE B: total_spent=8999", cb.get("total_spent"))

    # ── (C) Same external customer ID with source_app=dukaan → SEPARATE row ──
    print("\n--- CASE C: same ext_cust_id with source_app=dukaan → separate row ---")
    wh_cust_dukaan = create_webhook(
        token, "F33-Cust-Dukaan", "customer_created", "dukaan",
    )
    wh_dukaan_id = wh_cust_dukaan["id"]
    wh_dukaan_url = wh_cust_dukaan["url"]
    cust_dukaan_payload = dict(cust_payload)
    cust_dukaan_payload["phone"] = "+919555000111"
    rc = post_to_webhook(wh_dukaan_url, cust_dukaan_payload)
    jc = rc.json()
    check(jc.get("imported") == 1, "CASE C: imported=1", jc)
    lst_c = list_customers(token)
    check(lst_c.get("total") == 2,
          "CASE C: total=2 (source-strict isolation)", lst_c)
    # Capture the dukaan one
    dukaan_row = None
    for c in lst_c.get("customers", []):
        if c.get("source_app") == "dukaan":
            dukaan_row = c
            if c["id"] not in customer_ids:
                customer_ids.append(c["id"])
            break
    check(dukaan_row is not None, "CASE C: dukaan row exists", lst_c)
    if dukaan_row:
        check(dukaan_row.get("external_customer_id") == EXT_CUST_ID,
              "CASE C: dukaan row has same ext_cust_id",
              dukaan_row.get("external_customer_id"))

    # ── (D) Stats — total=2, by_source breakdown ──
    print("\n--- CASE D: stats with by_source ---")
    s = customer_stats(token)
    check(s.get("total") == 2, "CASE D: stats.total=2", s)
    by_src = s.get("by_source") or []
    sources_set = {x.get("source_app") for x in by_src}
    check("shopify" in sources_set, "CASE D: by_source has 'shopify'", by_src)
    check("dukaan" in sources_set, "CASE D: by_source has 'dukaan'", by_src)
    shop_e = next((x for x in by_src if x.get("source_app") == "shopify"), None)
    duk_e  = next((x for x in by_src if x.get("source_app") == "dukaan"), None)
    check(shop_e and shop_e.get("count") == 1,
          "CASE D: shopify count=1", shop_e)
    check(duk_e and duk_e.get("count") == 1,
          "CASE D: dukaan count=1", duk_e)
    check(shop_e and abs(float(shop_e.get("total_spent") or 0) - 8999.0) < 0.01,
          "CASE D: shopify total_spent=8999", shop_e)
    check(duk_e and abs(float(duk_e.get("total_spent") or 0) - 5499.0) < 0.01,
          "CASE D: dukaan total_spent=5499", duk_e)
    check(abs(float(s.get("total_spent") or 0) - (8999.0 + 5499.0)) < 0.01,
          "CASE D: stats.total_spent=14498", s.get("total_spent"))

    # ── (E) Filter by source_app=shopify returns 1 ──
    print("\n--- CASE E: filter source_app=shopify ---")
    le = list_customers(token, source_app="shopify")
    check(le.get("total") == 1, "CASE E: source_app=shopify returns 1", le)

    # ── (F) Search q=<phone> ──
    print("\n--- CASE F: search q=<phone> ---")
    lf = list_customers(token, q="9876543210")
    check(lf.get("total") >= 1,
          "CASE F: phone search returns ≥1 row", lf)
    found = False
    for x in lf.get("customers", []):
        if "9876543210" in (x.get("customer_phone") or ""):
            found = True
            break
    check(found, "CASE F: row with matching phone present", lf)

    # ── (G) DELETE customer ──
    print("\n--- CASE G: DELETE customer ---")
    if customer_ids:
        target = customer_ids[0]
        delete_customer(token, target)
        rg = get_customer(token, target)
        check(rg.status_code == 404, "CASE G: GET deleted customer 404",
              rg.status_code)
        s2 = customer_stats(token)
        check(s2.get("total") == 1, "CASE G: stats.total drops to 1", s2)
        # Remove from cleanup list since already deleted
        customer_ids = [c for c in customer_ids if c != target]

    # ── (H) customer_created with no id/phone/email → skipped=1 ──
    print("\n--- CASE H: missing id+phone+email → skipped ---")
    bad = {
        "first_name": "NoId",
        "last_name":  "NoPhone",
        "orders_count": 0,
    }
    rh = post_to_webhook(wh_cust_shop_url, bad)
    jh = rh.json()
    check(jh.get("skipped") == 1, "CASE H: skipped=1", jh)
    check(jh.get("imported") == 0, "CASE H: imported=0", jh)
    errs = jh.get("errors") or []
    has_msg = any(
        "couldn't locate customer_id" in str(e) and "phone" in str(e) and "email" in str(e)
        for e in errs
    )
    check(has_msg, "CASE H: error mentions 'customer_id / phone / email'", errs)

    # ── Cleanup ──
    print("\n--- CUSTOMER cleanup ---")
    # Refresh customer ids by listing again (in case dukaan id wasn't captured)
    final = list_customers(token)
    all_ids = [c["id"] for c in final.get("customers", [])]
    n_cust = asyncio.run(db_cleanup_customers(user_id, all_ids))
    print(f"  cleanup: deleted {n_cust} customers")
    delete_webhook(token, wh_cust_shop_id)
    delete_webhook(token, wh_upd_id)
    delete_webhook(token, wh_dukaan_id)
    print(f"  cleanup: deleted 3 webhooks (cust-shop, cust-shop-upd, cust-dukaan)")


# ────────────────────────────────────────────────────────────────────
def main() -> int:
    print(f"Backend URL: {BACKEND_URL}")
    print(f"Login: {ADMIN_EMAIL}")
    token, user_id = login()
    print(f"Logged in: user_id={user_id}, token_len={len(token)}")

    try:
        run_abandoned_cart_tests(token, user_id)
    except Exception as e:
        import traceback
        traceback.print_exc()
        check(False, f"Abandoned-cart suite raised: {e}", e)

    try:
        run_customer_tests(token, user_id)
    except Exception as e:
        import traceback
        traceback.print_exc()
        check(False, f"Customer suite raised: {e}", e)

    print("\n══════════════════════════════════════")
    print(f"   PASSED: {PASSED}   FAILED: {FAILED}")
    print("══════════════════════════════════════")
    if FAILED:
        for msg, ctx in FAIL_DETAILS:
            print(f"  ❌ {msg} ctx={ctx!r}")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
