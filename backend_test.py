"""
Backend tests — Phase F3.5 Bulk Abandoned Cart Recovery messaging.

Verifies the new abandoned_recovery template type in
/app/backend/routers/messaging.py.
"""
from __future__ import annotations

import sys
import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List

import requests
from motor.motor_asyncio import AsyncIOMotorClient

BASE = "https://logistics-hub-740.preview.emergentagent.com/api"
EMAIL = "admin@test.com"
PASSWORD = "Admin@12345"

MONGO_URL = "mongodb://localhost:27017"
DB_NAME = "test_database"

TEST_CART_IDS = ["c1", "c2"]


def login() -> Dict[str, Any]:
    r = requests.post(
        f"{BASE}/auth/login",
        json={"email": EMAIL, "password": PASSWORD},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def hdrs(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


async def insert_carts(owner_uid: str) -> None:
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    await db.abandoned_carts.delete_many(
        {"user_id": owner_uid, "id": {"$in": TEST_CART_IDS}}
    )
    docs = [
        {
            "id": "c1",
            "user_id": owner_uid,
            "status": "abandoned",
            "external_cart_id": "COL-1",
            "customer_name": "Test A",
            "customer_phone": "9999900001",
            "customer_email": "a@x.com",
            "address": "Addr1",
            "city": "Bhavnagar",
            "state": "Gujarat",
            "pincode": "364140",
            "cart_value": 599.0,
            "items_summary": "Shoes x1",
            "source_meta": {"source_app": "shopify"},
            "abandoned_at": now_iso(),
            "created_at": now_iso(),
        },
        {
            "id": "c2",
            "user_id": owner_uid,
            "status": "abandoned",
            "external_cart_id": "COL-2",
            "customer_name": "Test B",
            "customer_phone": "9999900002",
            "customer_email": "b@x.com",
            "address": "Addr2",
            "city": "Bhavnagar",
            "state": "Gujarat",
            "pincode": "364140",
            "cart_value": 1299.0,
            "items_summary": "Shirts x2",
            "source_meta": {"source_app": "shopify"},
            "abandoned_at": now_iso(),
            "created_at": now_iso(),
        },
    ]
    await db.abandoned_carts.insert_many(docs)
    client.close()


async def cleanup_carts(owner_uid: str) -> None:
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    res = await db.abandoned_carts.delete_many(
        {"user_id": owner_uid, "id": {"$in": TEST_CART_IDS}}
    )
    client.close()
    print(f"  cleanup: removed {res.deleted_count} abandoned_carts test docs")


def section(title: str) -> None:
    print(f"\n{'='*70}\n{title}\n{'='*70}")


PASS = 0
FAIL = 0
ERRORS: List[str] = []


def check(label: str, cond: bool, info: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        msg = f"  FAIL  {label}" + (f"  [{info}]" if info else "")
        print(msg)
        ERRORS.append(label + (f": {info}" if info else ""))


def main() -> None:
    section("LOGIN")
    j = login()
    token = j["token"]
    owner_uid = j["id"]
    print(f"  user_id={owner_uid}  email={j.get('email')}  is_admin={j.get('is_admin')}")
    H = hdrs(token)

    # ----- (A) GET /me/whatsapp-templates  -----
    section("(A) GET /me/whatsapp-templates → abandoned_recovery has gu/hi/en")
    r = requests.get(f"{BASE}/me/whatsapp-templates", headers=H, timeout=30)
    check("/me/whatsapp-templates 200", r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")
    body = r.json() if r.status_code == 200 else {}
    types = body.get("types") or []
    check("'abandoned_recovery' in types[]", "abandoned_recovery" in types, f"types={types}")
    admin_tpl = (body.get("admin_templates") or {}).get("abandoned_recovery") or {}
    for L in ("gu", "hi", "en"):
        check(
            f"admin_templates.abandoned_recovery.{L} non-empty",
            isinstance(admin_tpl.get(L), str) and len(admin_tpl[L].strip()) > 0,
            f"value={admin_tpl.get(L)!r}",
        )
    defaults = (body.get("defaults") or {}).get("abandoned_recovery") or {}
    for L in ("gu", "hi", "en"):
        check(
            f"defaults.abandoned_recovery.{L} non-empty",
            isinstance(defaults.get(L), str) and len(defaults[L].strip()) > 0,
        )

    # Pre-clean
    asyncio.run(cleanup_carts(owner_uid))

    # ----- (B) GET eligible (clean state) -----
    section("(B) GET /me/bulk-message/eligible?ttype=abandoned_recovery (clean)")
    r = requests.get(
        f"{BASE}/me/bulk-message/eligible",
        params={"ttype": "abandoned_recovery"},
        headers=H,
        timeout=30,
    )
    check("eligible (clean) 200", r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")
    if r.status_code == 200:
        b = r.json()
        check("ttype == abandoned_recovery", b.get("ttype") == "abandoned_recovery")
        check(
            "label == 'Abandoned Cart Recovery'",
            b.get("label") == "Abandoned Cart Recovery",
            f"got {b.get('label')!r}",
        )
        check("icon == cart emoji", b.get("icon") == "🛒", f"got {b.get('icon')!r}")
        check("min_days == 0", b.get("min_days") == 0, f"got {b.get('min_days')!r}")
        check(
            "statuses == ['abandoned']",
            b.get("statuses") == ["abandoned"],
            f"got {b.get('statuses')!r}",
        )
        check("shipments == []", b.get("shipments") == [], f"got {b.get('shipments')!r}")
        c = b.get("counts") or {}
        check("counts.list == 0", c.get("list") == 0, f"got {c}")
        check("counts.sent_today == 0", c.get("sent_today") == 0, f"got {c}")
        check("counts.pending == 0", c.get("pending") == 0, f"got {c}")

    # Capture baseline counts for delivery_confirmation regression
    section("REGRESSION BASELINE — delivery_confirmation BEFORE inserts")
    r0 = requests.get(
        f"{BASE}/me/bulk-message/eligible",
        params={"ttype": "delivery_confirmation"},
        headers=H,
        timeout=30,
    )
    base_dc = r0.json() if r0.status_code == 200 else {}
    base_counts_dc = base_dc.get("counts") or {}
    print(f"  baseline delivery_confirmation counts: {base_counts_dc}")
    check("delivery_confirmation baseline 200", r0.status_code == 200)

    # Capture baseline dashboard counts
    rdb = requests.get(f"{BASE}/me/bulk-message/dashboard-counts", headers=H, timeout=30)
    base_dash = rdb.json() if rdb.status_code == 200 else {}
    print(f"  baseline dashboard keys: {sorted(base_dash.keys()) if isinstance(base_dash, dict) else 'N/A'}")

    # ----- (C) Insert carts then GET eligible -----
    section("(C) Insert 2 abandoned_carts; GET eligible should return 2 shaped rows")
    asyncio.run(insert_carts(owner_uid))
    r = requests.get(
        f"{BASE}/me/bulk-message/eligible",
        params={"ttype": "abandoned_recovery"},
        headers=H,
        timeout=30,
    )
    check("eligible (with 2 carts) 200", r.status_code == 200, f"got {r.status_code}")
    if r.status_code == 200:
        b = r.json()
        ships = b.get("shipments") or []
        check("shipments has 2 rows", len(ships) == 2, f"got {len(ships)}")
        c = b.get("counts") or {}
        check("counts.list == 2", c.get("list") == 2, f"got {c}")
        check("counts.sent_today == 0", c.get("sent_today") == 0, f"got {c}")
        check("counts.pending == 2", c.get("pending") == 2, f"got {c}")
        c1 = next((s for s in ships if s.get("id") == "c1"), None)
        check("c1 row exists", c1 is not None)
        if c1:
            check("c1.customer_name == 'Test A'", c1.get("customer_name") == "Test A", f"got {c1.get('customer_name')!r}")
            check("c1.customer_phone == '9999900001'", c1.get("customer_phone") == "9999900001")
            check("c1.customer_email == 'a@x.com'", c1.get("customer_email") == "a@x.com")
            check("c1.address_line1 == 'Addr1'", c1.get("address_line1") == "Addr1")
            check("c1.city == 'Bhavnagar'", c1.get("city") == "Bhavnagar")
            check("c1.state == 'Gujarat'", c1.get("state") == "Gujarat")
            check("c1.pincode == '364140'", c1.get("pincode") == "364140")
            check("c1.amount == 599.0", float(c1.get("amount") or -1) == 599.0, f"got {c1.get('amount')!r}")
            check("c1.items == 'Shoes x1'", c1.get("items") == "Shoes x1")
            check("c1.order_id == 'COL-1'", c1.get("order_id") == "COL-1")
            check("c1.tracking_id == ''", c1.get("tracking_id") == "")
            check("c1.courier == ''", c1.get("courier") == "")
            check("c1._msg_sent_today == False", c1.get("_msg_sent_today") is False)
            check("c1._days_since == 0", c1.get("_days_since") == 0)

        c2 = next((s for s in ships if s.get("id") == "c2"), None)
        check("c2 row exists", c2 is not None)
        if c2:
            check("c2.amount == 1299.0", float(c2.get("amount") or -1) == 1299.0)
            check("c2.customer_phone == '9999900002'", c2.get("customer_phone") == "9999900002")

    # ----- (D) mark-sent first time -----
    section("(D) POST /me/bulk-message/mark-sent (c1, c2) → updated=2")
    r = requests.post(
        f"{BASE}/me/bulk-message/mark-sent",
        headers=H,
        json={"ttype": "abandoned_recovery", "shipment_ids": ["c1", "c2"]},
        timeout=30,
    )
    check("mark-sent 200", r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")
    if r.status_code == 200:
        b = r.json()
        check("mark-sent updated == 2", b.get("updated") == 2, f"got {b}")
        check("mark-sent skipped == 0", b.get("skipped") == 0, f"got {b}")
        check(
            "mark-sent updated_ids contains c1+c2",
            set(b.get("updated_ids") or []) == {"c1", "c2"},
        )

    # GET eligible after mark-sent
    r = requests.get(
        f"{BASE}/me/bulk-message/eligible",
        params={"ttype": "abandoned_recovery"},
        headers=H,
        timeout=30,
    )
    check("eligible after mark-sent 200", r.status_code == 200)
    if r.status_code == 200:
        b = r.json()
        c = b.get("counts") or {}
        check("counts.sent_today == 2", c.get("sent_today") == 2, f"got {c}")
        check("counts.pending == 0", c.get("pending") == 0, f"got {c}")
        ships = b.get("shipments") or []
        check(
            "all rows have _msg_sent_today=True",
            len(ships) == 2 and all(s.get("_msg_sent_today") is True for s in ships),
        )

    # ----- (E) mark-sent again → skipped=2 -----
    section("(E) POST /me/bulk-message/mark-sent same body → skipped=2")
    r = requests.post(
        f"{BASE}/me/bulk-message/mark-sent",
        headers=H,
        json={"ttype": "abandoned_recovery", "shipment_ids": ["c1", "c2"]},
        timeout=30,
    )
    check("mark-sent 2nd 200", r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")
    if r.status_code == 200:
        b = r.json()
        check("2nd mark-sent updated == 0", b.get("updated") == 0, f"got {b}")
        check("2nd mark-sent skipped == 2", b.get("skipped") == 2, f"got {b}")
        check(
            "2nd mark-sent skipped_ids set == {c1,c2}",
            set(b.get("skipped_ids") or []) == {"c1", "c2"},
        )

    # ----- (F) reset → updated=2; eligible.pending=2 again -----
    section("(F) POST /me/bulk-message/reset → updated=2")
    r = requests.post(
        f"{BASE}/me/bulk-message/reset",
        headers=H,
        json={"ttype": "abandoned_recovery", "shipment_ids": ["c1", "c2"]},
        timeout=30,
    )
    check("reset 200", r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")
    if r.status_code == 200:
        b = r.json()
        check("reset updated == 2", b.get("updated") == 2, f"got {b}")
    r = requests.get(
        f"{BASE}/me/bulk-message/eligible",
        params={"ttype": "abandoned_recovery"},
        headers=H,
        timeout=30,
    )
    if r.status_code == 200:
        c = r.json().get("counts") or {}
        check("after reset counts.pending == 2", c.get("pending") == 2, f"got {c}")
        check("after reset counts.sent_today == 0", c.get("sent_today") == 0, f"got {c}")

    # ----- (G) Dashboard counts include abandoned_recovery + others unchanged -----
    section("(G) GET /me/bulk-message/dashboard-counts")
    r = requests.get(f"{BASE}/me/bulk-message/dashboard-counts", headers=H, timeout=30)
    check("dashboard-counts 200", r.status_code == 200)
    if r.status_code == 200:
        d = r.json()
        keys_required = {
            "shipment_sent",
            "dispatch_confirmation",
            "delivery_confirmation",
            "delivery_done",
            "feedback_request",
            "abandoned_recovery",
        }
        check(
            "dashboard has all 6 keys",
            keys_required.issubset(set(d.keys())),
            f"got {sorted(d.keys())}",
        )
        ar = d.get("abandoned_recovery") or {}
        check("abandoned_recovery.list == 2", ar.get("list") == 2, f"got {ar}")
        check("abandoned_recovery.pending == 2", ar.get("pending") == 2, f"got {ar}")
        check(
            "abandoned_recovery.label == 'Abandoned Cart Recovery'",
            ar.get("label") == "Abandoned Cart Recovery",
        )
        check("abandoned_recovery.icon == cart emoji", ar.get("icon") == "🛒")

        for k in [
            "shipment_sent",
            "dispatch_confirmation",
            "delivery_confirmation",
            "delivery_done",
            "feedback_request",
        ]:
            base = base_dash.get(k) or {}
            now = d.get(k) or {}
            check(
                f"dashboard '{k}' list unchanged",
                base.get("list") == now.get("list"),
                f"baseline={base.get('list')} now={now.get('list')}",
            )
            check(
                f"dashboard '{k}' pending unchanged",
                base.get("pending") == now.get("pending"),
                f"baseline={base.get('pending')} now={now.get('pending')}",
            )

    # ----- (H) Regression: shipment-based ttype unaffected -----
    section("(H) REGRESSION — delivery_confirmation eligible AFTER abandoned ops")
    r = requests.get(
        f"{BASE}/me/bulk-message/eligible",
        params={"ttype": "delivery_confirmation"},
        headers=H,
        timeout=30,
    )
    check("delivery_confirmation eligible 200", r.status_code == 200)
    if r.status_code == 200:
        b = r.json()
        c = b.get("counts") or {}
        check(
            "delivery_confirmation counts.list unchanged",
            c.get("list") == base_counts_dc.get("list"),
            f"baseline={base_counts_dc.get('list')} now={c.get('list')}",
        )
        check(
            "delivery_confirmation counts.pending unchanged",
            c.get("pending") == base_counts_dc.get("pending"),
            f"baseline={base_counts_dc.get('pending')} now={c.get('pending')}",
        )
        check(
            "delivery_confirmation counts.sent_today unchanged",
            c.get("sent_today") == base_counts_dc.get("sent_today"),
            f"baseline={base_counts_dc.get('sent_today')} now={c.get('sent_today')}",
        )
        ids = {s.get("id") for s in (b.get("shipments") or [])}
        check(
            "delivery_confirmation has NO c1/c2 ids (no cross-collection contamination)",
            "c1" not in ids and "c2" not in ids,
            f"intersection={ids & set(TEST_CART_IDS)}",
        )

    section("(H2) REGRESSION — mark-sent for delivery_confirmation with fake IDs")
    r = requests.post(
        f"{BASE}/me/bulk-message/mark-sent",
        headers=H,
        json={"ttype": "delivery_confirmation", "shipment_ids": ["c1", "c2"]},
        timeout=30,
    )
    check("mark-sent (delivery_confirmation, fake IDs) 200", r.status_code == 200)
    if r.status_code == 200:
        b = r.json()
        check("mark-sent d_c updated == 0", b.get("updated") == 0, f"got {b}")
        check("mark-sent d_c skipped == 0", b.get("skipped") == 0, f"got {b}")

    # CLEANUP
    section("CLEANUP")
    asyncio.run(cleanup_carts(owner_uid))

    section(f"RESULTS: {PASS} passed, {FAIL} failed")
    if FAIL:
        print("\nFAILED:")
        for e in ERRORS:
            print(f"  - {e}")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
