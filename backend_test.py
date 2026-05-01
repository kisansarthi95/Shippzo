"""
Phase-9 Scan-to-Dispatch backend regression.

Tests:
  1. GET /api/shipments/stats returns dispatch + shipped counts.
  2. POST /api/shipments creates Pending shipment.
  3. POST /api/shipments/scan-dispatch moves Pending -> Dispatch.
  4. Idempotent — second scan returns already_dispatch.
  5. Non-existent tracking returns not_found (200).
  6. Wrong-status shipment returns wrong_status:<status>.
  7. Empty tracking_id returns empty_tracking_id.
  8. Race: two concurrent scans on same Pending — exactly one "moved",
     other "already".
"""
import asyncio
import time
import uuid

import httpx

BASE = "https://logistics-hub-740.preview.emergentagent.com/api"
ADMIN_EMAIL = "admin@test.com"
ADMIN_PASSWORD = "Admin@12345"

created_shipment_ids: list[str] = []
borrowed_revert: list[tuple[str, str]] = []  # (id, original_status) — flip back at cleanup
_orig_admin_plan: dict = {}


def _bump_admin_to_platinum_temp():
    """Temporarily upgrade admin plan to platinum so the tests can
    create new shipments. The original plan is restored at cleanup."""
    import asyncio as _asyncio
    import os as _os
    from motor.motor_asyncio import AsyncIOMotorClient
    from dotenv import load_dotenv
    load_dotenv("/app/backend/.env")

    async def _run():
        c = AsyncIOMotorClient(_os.environ["MONGO_URL"])
        db = c[_os.environ["DB_NAME"]]
        u = await db.users.find_one({"email": ADMIN_EMAIL}, {"_id": 0, "plan": 1, "plan_expires_at": 1})
        if not u:
            raise SystemExit("admin user not found")
        _orig_admin_plan["plan"] = u.get("plan", "free_trial")
        _orig_admin_plan["plan_expires_at"] = u.get("plan_expires_at", "")
        from datetime import datetime, timezone, timedelta
        new_exp = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        await db.users.update_one(
            {"email": ADMIN_EMAIL},
            {"$set": {"plan": "platinum", "plan_expires_at": new_exp}},
        )
        c.close()
    _asyncio.run(_run())
    print(f"[setup] admin plan temporarily set to platinum (was {_orig_admin_plan.get('plan')})")


def _restore_admin_plan():
    if not _orig_admin_plan:
        return
    import asyncio as _asyncio
    import os as _os
    from motor.motor_asyncio import AsyncIOMotorClient
    from dotenv import load_dotenv
    load_dotenv("/app/backend/.env")

    async def _run():
        c = AsyncIOMotorClient(_os.environ["MONGO_URL"])
        db = c[_os.environ["DB_NAME"]]
        await db.users.update_one(
            {"email": ADMIN_EMAIL},
            {"$set": {
                "plan": _orig_admin_plan.get("plan", "free_trial"),
                "plan_expires_at": _orig_admin_plan.get("plan_expires_at", ""),
            }},
        )
        c.close()
    _asyncio.run(_run())
    print(f"[cleanup] admin plan restored to {_orig_admin_plan.get('plan')}")


def fail(msg: str):
    print(f"  FAIL: {msg}")
    raise SystemExit(1)


def ok(msg: str):
    print(f"  OK  : {msg}")


def login(client: httpx.Client) -> str:
    r = client.post(f"{BASE}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    if r.status_code != 200:
        fail(f"login failed {r.status_code}: {r.text[:200]}")
    tok = r.json().get("token")
    if not tok:
        fail("login missing token")
    return tok


def get_courier_id(client: httpx.Client, headers: dict) -> str:
    r = client.get(f"{BASE}/couriers", headers=headers)
    if r.status_code != 200:
        fail(f"GET /couriers failed {r.status_code}")
    couriers = r.json()
    if not couriers:
        fail("no couriers available for admin")
    return couriers[0]["id"]


def create_shipment(client, headers, courier_id, tracking_id, name="Rahul Sharma") -> dict:
    payload = {
        "tracking_id": tracking_id,
        "courier_id": courier_id,
        "customer_name": name,
        "customer_phone": "9876543210",
        "address_line1": "12 Marine Drive",
        "city": "Mumbai",
        "state": "Maharashtra",
        "pincode": "400001",
        "payment_mode": "Prepaid",
        "amount": 599.0,
        "weight": "1",
    }
    r = client.post(f"{BASE}/shipments", json=payload, headers=headers)
    if r.status_code != 200:
        fail(f"POST /shipments failed {r.status_code}: {r.text[:300]}")
    doc = r.json()
    created_shipment_ids.append(doc["id"])
    return doc


def test_stats_fields(client, headers):
    print("\n[1] GET /api/shipments/stats — includes dispatch + shipped")
    r = client.get(f"{BASE}/shipments/stats", headers=headers)
    if r.status_code != 200:
        fail(f"status {r.status_code}: {r.text[:200]}")
    j = r.json()
    for k in ("total", "delivered", "pending", "dispatch", "shipped",
              "cod_total", "cod_count", "prepaid_total", "prepaid_count", "revenue_total"):
        if k not in j:
            fail(f"stats missing key: {k}")
    for k in ("dispatch", "shipped"):
        if not isinstance(j[k], int):
            fail(f"stats[{k}] not int: {j[k]!r}")
    ok(f"stats OK — total={j['total']} pending={j['pending']} dispatch={j['dispatch']} shipped={j['shipped']} delivered={j['delivered']}")


def test_scan_dispatch_happy(client, headers, courier_id):
    print("\n[2+3] Create Pending -> scan-dispatch -> moved")
    tid = f"ZTST{int(time.time())}{uuid.uuid4().hex[:4].upper()}"
    doc = create_shipment(client, headers, courier_id, tid)
    if doc.get("status") != "Pending":
        fail(f"new shipment status expected 'Pending' got {doc.get('status')!r}")
    ok(f"created shipment tid={tid} status=Pending id={doc['id']}")

    r = client.post(f"{BASE}/shipments/scan-dispatch", json={"tracking_id": tid}, headers=headers)
    if r.status_code != 200:
        fail(f"scan-dispatch status {r.status_code}: {r.text[:300]}")
    res = r.json()
    if res.get("outcome") != "moved":
        fail(f"expected outcome moved, got {res}")
    if res.get("reason") != "ok":
        fail(f"expected reason ok, got {res.get('reason')}")
    expected_msg = f"{tid} moved to Dispatch"
    if res.get("message") != expected_msg:
        fail(f"message mismatch: expected {expected_msg!r} got {res.get('message')!r}")
    sh = res.get("shipment") or {}
    if sh.get("status") != "Dispatch":
        fail(f"response.shipment.status expected Dispatch, got {sh.get('status')}")
    if not sh.get("dispatched_at"):
        fail("response.shipment.dispatched_at not set")
    ok(f"moved: status=Dispatch dispatched_at={sh.get('dispatched_at')}")

    r2 = client.get(f"{BASE}/shipments", headers=headers, params={"limit": 500})
    if r2.status_code != 200:
        fail(f"GET /shipments {r2.status_code}")
    listing = r2.json()
    found = next((s for s in listing if s["tracking_id"] == tid), None)
    if not found:
        fail("shipment not in listing after scan")
    if found["status"] != "Dispatch":
        fail(f"persisted status={found['status']}, expected Dispatch")
    ok("GET /shipments confirms status=Dispatch")
    return tid


def test_scan_dispatch_already(client, headers, tid):
    print("\n[4] Second scan on same tracking -> already")
    r = client.post(f"{BASE}/shipments/scan-dispatch", json={"tracking_id": tid}, headers=headers)
    if r.status_code != 200:
        fail(f"status {r.status_code}: {r.text[:300]}")
    res = r.json()
    if res.get("outcome") != "already":
        fail(f"expected outcome already, got {res}")
    if res.get("reason") != "already_dispatch":
        fail(f"expected reason already_dispatch, got {res.get('reason')}")
    if not res.get("shipment"):
        fail("shipment should be non-null on already")
    if (res.get("shipment") or {}).get("status") != "Dispatch":
        fail("shipment.status should still be Dispatch")
    ok("idempotent — second scan returns already_dispatch")


def test_scan_dispatch_not_found(client, headers):
    print("\n[5] Random tracking -> failed not_found (HTTP 200)")
    fake = "ZZZNOPE123"
    r = client.post(f"{BASE}/shipments/scan-dispatch", json={"tracking_id": fake}, headers=headers)
    if r.status_code != 200:
        fail(f"expected HTTP 200 for not-found, got {r.status_code}")
    res = r.json()
    if res.get("outcome") != "failed":
        fail(f"expected failed, got {res}")
    if res.get("reason") != "not_found":
        fail(f"expected reason not_found, got {res.get('reason')}")
    if res.get("shipment") is not None:
        fail(f"shipment should be null, got {res.get('shipment')!r}")
    ok("not_found handled with HTTP 200")


def borrow_pending(client, headers, exclude_tids=None) -> dict:
    """Find an existing Pending shipment for tests that can't create
    new ones (admin is on free_trial with label cap reached)."""
    exclude = set(exclude_tids or [])
    r = client.get(f"{BASE}/shipments", headers=headers, params={"status": "Pending", "limit": 500})
    if r.status_code != 200:
        fail(f"GET /shipments failed {r.status_code}")
    for s in r.json():
        if s["tracking_id"] not in exclude:
            return s
    fail("no available Pending shipment to borrow")


def test_scan_dispatch_wrong_status(client, headers):
    print("\n[6] Wrong-status shipment (Shipped) -> failed wrong_status:Shipped")
    sh = borrow_pending(client, headers)
    sid = sh["id"]
    tid = sh["tracking_id"]
    original_status = sh.get("status", "Pending")
    borrowed_revert.append((sid, original_status))
    r = client.put(f"{BASE}/shipments/{sid}", json={"status": "Shipped"}, headers=headers)
    if r.status_code != 200:
        fail(f"PUT /shipments failed {r.status_code}: {r.text[:300]}")
    if r.json().get("status") != "Shipped":
        fail(f"PUT response status not Shipped: {r.json().get('status')}")
    ok(f"flipped existing shipment to Shipped: tid={tid}")

    r = client.post(f"{BASE}/shipments/scan-dispatch", json={"tracking_id": tid}, headers=headers)
    if r.status_code != 200:
        fail(f"scan-dispatch status {r.status_code}")
    res = r.json()
    if res.get("outcome") != "failed":
        fail(f"expected failed, got {res}")
    if res.get("reason") != "wrong_status:Shipped":
        fail(f"expected reason wrong_status:Shipped, got {res.get('reason')!r}")
    if not res.get("shipment"):
        fail("shipment should be non-null on wrong_status")
    ok("wrong_status:Shipped returned correctly")


def test_scan_dispatch_empty(client, headers):
    print("\n[7] Empty tracking_id -> failed empty_tracking_id")
    r = client.post(f"{BASE}/shipments/scan-dispatch", json={"tracking_id": ""}, headers=headers)
    if r.status_code != 200:
        fail(f"status {r.status_code}")
    res = r.json()
    if res.get("outcome") != "failed":
        fail(f"expected failed, got {res}")
    if res.get("reason") != "empty_tracking_id":
        fail(f"expected reason empty_tracking_id, got {res.get('reason')!r}")
    ok("empty_tracking_id handled")


async def test_scan_dispatch_race(token, client, headers):
    print("\n[8] Race — 2 concurrent scans on same Pending")
    sh = borrow_pending(client, headers)
    sid = sh["id"]
    tid = sh["tracking_id"]
    original_status = sh.get("status", "Pending")
    borrowed_revert.append((sid, original_status))
    async with httpx.AsyncClient(timeout=30) as c:
        ah = {"Authorization": f"Bearer {token}"}
        body = {"tracking_id": tid}
        results = await asyncio.gather(
            c.post(f"{BASE}/shipments/scan-dispatch", json=body, headers=ah),
            c.post(f"{BASE}/shipments/scan-dispatch", json=body, headers=ah),
        )
        outcomes = [r.json() for r in results if r.status_code == 200]
        if len(outcomes) != 2:
            fail(f"some requests did not return 200: {[(r.status_code, r.text[:120]) for r in results]}")
        print(f"  outcomes: {[o.get('outcome') for o in outcomes]}, reasons: {[o.get('reason') for o in outcomes]}")
        moved_count = sum(1 for o in outcomes if o.get("outcome") == "moved")
        already_count = sum(1 for o in outcomes if o.get("outcome") == "already")
        if moved_count != 1 or already_count != 1:
            fail(f"expected exactly 1 moved and 1 already, got moved={moved_count} already={already_count}")
        r = await c.get(f"{BASE}/shipments", headers=ah, params={"limit": 500})
        listing = r.json()
        found = next((s for s in listing if s["tracking_id"] == tid), None)
        if not found or found.get("status") != "Dispatch":
            fail(f"final status not Dispatch: {found.get('status') if found else 'not-found'}")
    ok("race handled correctly — 1 moved, 1 already, final status Dispatch")


def cleanup(client, headers):
    print("\n[cleanup] Reverting borrowed shipments and deleting created ones...")
    for sid, orig_status in borrowed_revert:
        try:
            r = client.put(f"{BASE}/shipments/{sid}", json={"status": orig_status}, headers=headers)
            if r.status_code == 200:
                print(f"  reverted {sid} -> {orig_status}")
            else:
                print(f"  revert {sid} returned {r.status_code}: {r.text[:120]}")
        except Exception as e:
            print(f"  revert {sid} error: {e}")
    for sid in created_shipment_ids:
        try:
            r = client.delete(f"{BASE}/shipments/{sid}", headers=headers)
            if r.status_code == 200:
                print(f"  deleted {sid}")
            else:
                print(f"  delete {sid} returned {r.status_code}")
        except Exception as e:
            print(f"  delete {sid} error: {e}")


def main():
    _bump_admin_to_platinum_temp()
    with httpx.Client(timeout=30) as client:
        token = login(client)
        headers = {"Authorization": f"Bearer {token}"}
        courier_id = get_courier_id(client, headers)
        print(f"Logged in as {ADMIN_EMAIL}, courier_id={courier_id}")

        try:
            test_stats_fields(client, headers)
            moved_tid = test_scan_dispatch_happy(client, headers, courier_id)
            test_scan_dispatch_already(client, headers, moved_tid)
            test_scan_dispatch_not_found(client, headers)
            test_scan_dispatch_wrong_status(client, headers)
            test_scan_dispatch_empty(client, headers)
            asyncio.run(test_scan_dispatch_race(token, client, headers))
        finally:
            cleanup(client, headers)
            _restore_admin_plan()

    print("\n ALL PHASE-9 SCAN-TO-DISPATCH TESTS PASSED ")


if __name__ == "__main__":
    main()
