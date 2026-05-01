"""
Phase-11 Delivery Confirmation backend tests.

Covers:
  1. GET  /api/shipments/delivery-confirmation (default + threshold=0)
  2. POST /api/shipments/delivery-confirmation/mark-sent (new + same-day-dup)
  3. POST /api/shipments/delivery-confirmation/mark-delivered (Shipped-only filter)
  4. Empty shipment_ids contract on both POSTs.

Setup helper: creates shipments directly via POST /api/shipments (admin
bypasses plan caps) and then force-sets status="Shipped" + shipped_at
in Mongo so we can control days_since_shipped precisely.
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

import requests
from pymongo import MongoClient

BASE = "https://logistics-hub-740.preview.emergentagent.com/api"
ADMIN_EMAIL = "admin@test.com"
ADMIN_PASSWORD = "Admin@12345"

MONGO_URL = "mongodb://localhost:27017"
DB_NAME = "test_database"

PASSED: List[str] = []
FAILED: List[str] = []


def p(ok: bool, label: str, extra: str = ""):
    line = f"{'PASS' if ok else 'FAIL'} | {label}"
    if extra:
        line += f" -- {extra}"
    (PASSED if ok else FAILED).append(line)
    print(line)


def login(email: str, password: str) -> str:
    r = requests.post(f"{BASE}/auth/login", json={"email": email, "password": password}, timeout=20)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    return r.json()["token"]


def auth(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def get_user_id(email: str) -> str:
    mc = MongoClient(MONGO_URL)
    try:
        u = mc[DB_NAME].users.find_one({"email": email}, {"id": 1})
        assert u, f"user not found: {email}"
        return u["id"]
    finally:
        mc.close()


def create_shipment(token: str, tracking_id: str, name: str, user_id: str = "") -> Dict[str, Any]:
    """Insert a shipment directly into Mongo (bypasses plan-limit gate on
    POST /shipments for the admin account, which is on free_trial in this
    environment). The created doc mirrors the Shipment model so the
    delivery-confirmation endpoints still see the correct shape.
    """
    mc = MongoClient(MONGO_URL)
    try:
        sid = str(uuid.uuid4())
        doc = {
            "id": sid,
            "user_id": user_id,
            "tracking_id": tracking_id,
            "courier_id": None,
            "courier_name": "",
            "master_order_id": "",
            "order_id": "",
            "customer_name": name,
            "customer_phone": "9123400001",
            "customer_alt_phone": "",
            "address_line1": "12, MG Road",
            "address_line2": "",
            "city": "Ahmedabad",
            "state": "Gujarat",
            "pincode": "380001",
            "payment_mode": "Prepaid",
            "amount": 499.0,
            "cod_amount": 0.0,
            "items": ["Test Item"],
            "item_description": "",
            "weight": "500",
            "token_amount": 0.0,
            "box_dimensions": "",
            "shipment_notes": "",
            "custom_values": {},
            "status": "Pending",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "delivered_at": None,
            "dispatched_at": None,
            "shipped_at": None,
            "confirmation_status": "pending",
            "last_confirmation_sent_at": None,
            "last_confirmation_reply": None,
            "sheet_row_key": "",
            "sheet_row_num": None,
        }
        mc[DB_NAME].shipments.insert_one(doc)
        doc.pop("_id", None)
        return doc
    finally:
        mc.close()


def set_shipped_in_mongo(shipment_id: str, days_ago: int):
    """Directly force status='Shipped' + shipped_at = now - days_ago.
    Bypasses the scan flow so we can control the threshold precisely.
    """
    mc = MongoClient(MONGO_URL)
    try:
        db = mc[DB_NAME]
        shipped_at = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
        res = db.shipments.update_one(
            {"id": shipment_id},
            {"$set": {
                "status": "Shipped",
                "shipped_at": shipped_at,
                "confirmation_status": "pending",
                "last_confirmation_sent_at": None,
            }},
        )
        assert res.matched_count == 1, f"mongo matched 0 for {shipment_id}"
    finally:
        mc.close()


def mongo_force_sent_today(shipment_id: str):
    """Force last_confirmation_sent_at=now so mark-sent will skip it."""
    mc = MongoClient(MONGO_URL)
    try:
        db = mc[DB_NAME]
        now_iso = datetime.now(timezone.utc).isoformat()
        db.shipments.update_one(
            {"id": shipment_id},
            {"$set": {
                "confirmation_status": "sent",
                "last_confirmation_sent_at": now_iso,
            }},
        )
    finally:
        mc.close()


def mongo_set_status(shipment_id: str, status: str):
    mc = MongoClient(MONGO_URL)
    try:
        mc[DB_NAME].shipments.update_one({"id": shipment_id}, {"$set": {"status": status}})
    finally:
        mc.close()


def delete_shipment_direct(shipment_id: str):
    """Hard-delete from Mongo (avoid triggering Sheet tombstone)."""
    mc = MongoClient(MONGO_URL)
    try:
        mc[DB_NAME].shipments.delete_one({"id": shipment_id})
    finally:
        mc.close()


def get_shipment_by_id(shipment_id: str) -> Optional[Dict[str, Any]]:
    mc = MongoClient(MONGO_URL)
    try:
        return mc[DB_NAME].shipments.find_one({"id": shipment_id}, {"_id": 0})
    finally:
        mc.close()


# ────────────────────────────────────────────────────────────
# Tests
# ────────────────────────────────────────────────────────────

def test_1_default_threshold_shape(token: str):
    print("\n=== Test 1: GET default threshold=5 returns expected shape ===")
    r = requests.get(f"{BASE}/shipments/delivery-confirmation", headers=auth(token), timeout=20)
    p(r.status_code == 200, "GET default threshold returns 200", f"status={r.status_code}")
    if r.status_code != 200:
        print(r.text[:400])
        return
    body = r.json()
    p("threshold_days" in body, "response has threshold_days")
    p(body.get("threshold_days") == 5, "threshold_days default = 5", f"got {body.get('threshold_days')}")
    p("counts" in body and isinstance(body["counts"], dict), "response has counts dict")
    counts = body.get("counts") or {}
    for k in ("list", "sent", "replied", "pending"):
        p(k in counts, f"counts has '{k}'")
    p("shipments" in body and isinstance(body["shipments"], list), "response has shipments list")


def test_2_and_3_thresholds(token: str, user_id: str) -> Dict[str, str]:
    print("\n=== Test 2+3+4: threshold filtering ===")
    tid_a = f"DC7-{uuid.uuid4().hex[:6].upper()}"
    tid_b = f"DC0-{uuid.uuid4().hex[:6].upper()}"
    sa = create_shipment(token, tid_a, "Rahul Verma", user_id=user_id)
    sb = create_shipment(token, tid_b, "Priya Nair", user_id=user_id)
    sid_a, sid_b = sa["id"], sb["id"]

    set_shipped_in_mongo(sid_a, days_ago=7)
    set_shipped_in_mongo(sid_b, days_ago=0)

    # threshold=5 (default): A in, B out
    r = requests.get(f"{BASE}/shipments/delivery-confirmation", headers=auth(token), timeout=20)
    p(r.status_code == 200, "GET threshold=5 returns 200")
    shipments = r.json().get("shipments", [])
    ids_5 = {s["id"] for s in shipments}
    p(sid_a in ids_5, "[Test 2] shipment A (days=7) appears in threshold=5")
    p(sid_b not in ids_5, "[Test 3] shipment B (days=0) NOT in threshold=5")

    entry_a = next((s for s in shipments if s["id"] == sid_a), None)
    if entry_a:
        p("days_since_shipped" in entry_a, "entry has days_since_shipped field")
        p(isinstance(entry_a.get("days_since_shipped"), int), "days_since_shipped is int")
        p(entry_a["days_since_shipped"] >= 5, f"shipment A days_since_shipped >= 5 (got {entry_a['days_since_shipped']})")
        p(entry_a.get("status") == "Shipped", "entry A status=Shipped")
        p(entry_a.get("confirmation_status") != "confirmed", "entry A not confirmed")

    # threshold=0: both in
    r0 = requests.get(f"{BASE}/shipments/delivery-confirmation?threshold_days=0", headers=auth(token), timeout=20)
    p(r0.status_code == 200, "GET threshold=0 returns 200")
    body0 = r0.json()
    ids_0 = {s["id"] for s in body0.get("shipments", [])}
    p(sid_b in ids_0, "[Test 4] shipment B appears when threshold=0")
    p(sid_a in ids_0, "[Test 4] shipment A still appears when threshold=0")
    p(body0.get("threshold_days") == 0, f"threshold_days echoed as 0 (got {body0.get('threshold_days')})")

    return {"sid_a": sid_a, "sid_b": sid_b}


def test_5_mark_sent(token: str, sid_a: str, sid_b: str):
    print("\n=== Test 5: mark-sent new + same-day-skip ===")
    # Pre-flag sid_b as already sent today.
    mongo_force_sent_today(sid_b)

    r = requests.post(
        f"{BASE}/shipments/delivery-confirmation/mark-sent",
        json={"shipment_ids": [sid_a, sid_b]},
        headers=auth(token), timeout=20,
    )
    p(r.status_code == 200, f"mark-sent returns 200", f"got {r.status_code}")
    body = r.json()
    for k in ("updated", "skipped", "updated_ids", "skipped_ids"):
        p(k in body, f"response has '{k}'")
    p(body.get("updated") == 1, f"updated count == 1 (got {body.get('updated')})")
    p(body.get("skipped") == 1, f"skipped count == 1 (got {body.get('skipped')})")
    p(sid_a in (body.get("updated_ids") or []), "sid_a in updated_ids")
    p(sid_b in (body.get("skipped_ids") or []), "sid_b in skipped_ids (already sent today)")

    # Verify mongo state: sid_a now 'sent' with today prefix
    doc_a = get_shipment_by_id(sid_a)
    today = datetime.now(timezone.utc).isoformat()[:10]
    p(doc_a.get("confirmation_status") == "sent", f"sid_a confirmation_status='sent' (got {doc_a.get('confirmation_status')})")
    last_sent = (doc_a.get("last_confirmation_sent_at") or "")
    p(last_sent.startswith(today), f"sid_a last_confirmation_sent_at starts with {today} (got {last_sent[:20]})")

    # Second call with sid_a same day → should skip
    r2 = requests.post(
        f"{BASE}/shipments/delivery-confirmation/mark-sent",
        json={"shipment_ids": [sid_a]},
        headers=auth(token), timeout=20,
    )
    p(r2.status_code == 200, "second mark-sent returns 200")
    body2 = r2.json()
    p(body2.get("updated") == 0, f"second call: updated=0 (got {body2.get('updated')})")
    p(body2.get("skipped") == 1, f"second call: skipped=1 (got {body2.get('skipped')})")
    p(sid_a in (body2.get("skipped_ids") or []), "second call: sid_a in skipped_ids")


def test_6_mark_delivered(token: str, user_id: str) -> List[str]:
    print("\n=== Test 6: mark-delivered only flips Shipped ===")
    s1 = create_shipment(token, f"MD1-{uuid.uuid4().hex[:6].upper()}", "Sunita Iyer", user_id=user_id)
    s2 = create_shipment(token, f"MD2-{uuid.uuid4().hex[:6].upper()}", "Anil Kumar", user_id=user_id)
    s3 = create_shipment(token, f"MD3-{uuid.uuid4().hex[:6].upper()}", "Meera Joshi", user_id=user_id)
    id1, id2, id3 = s1["id"], s2["id"], s3["id"]

    set_shipped_in_mongo(id1, days_ago=0)     # Shipped
    mongo_set_status(id2, "Dispatch")          # not Shipped
    mongo_set_status(id3, "Delivered")         # already Delivered

    r = requests.post(
        f"{BASE}/shipments/delivery-confirmation/mark-delivered",
        json={"shipment_ids": [id1, id2, id3]},
        headers=auth(token), timeout=20,
    )
    p(r.status_code == 200, "mark-delivered returns 200")
    body = r.json()
    p("updated" in body and "requested" in body, "response has updated + requested")
    p(body.get("requested") == 3, f"requested == 3 (got {body.get('requested')})")
    p(body.get("updated") == 1, f"updated == 1 (Shipped-only filter) (got {body.get('updated')})")

    doc1 = get_shipment_by_id(id1)
    p(doc1.get("status") == "Delivered", f"id1 status=Delivered (got {doc1.get('status')})")
    p(doc1.get("confirmation_status") == "confirmed", f"id1 confirmation_status=confirmed (got {doc1.get('confirmation_status')})")
    p(bool(doc1.get("delivered_at")), f"id1 delivered_at set (got {doc1.get('delivered_at')})")

    doc2 = get_shipment_by_id(id2)
    p(doc2.get("status") == "Dispatch", f"id2 (Dispatch) untouched (got {doc2.get('status')})")
    p(doc2.get("confirmation_status") != "confirmed", f"id2 confirmation_status not confirmed (got {doc2.get('confirmation_status')})")

    doc3 = get_shipment_by_id(id3)
    p(doc3.get("status") == "Delivered", "id3 still Delivered")
    p(doc3.get("confirmation_status") != "confirmed",
      f"id3 confirmation_status NOT flipped to 'confirmed' since current status != Shipped (got {doc3.get('confirmation_status')})")

    return [id1, id2, id3]


def test_7_empty_ids(token: str):
    print("\n=== Test 7: Empty shipment_ids contracts ===")
    r1 = requests.post(f"{BASE}/shipments/delivery-confirmation/mark-sent",
                       json={"shipment_ids": []}, headers=auth(token), timeout=20)
    p(r1.status_code == 200, "mark-sent empty list returns 200")
    b1 = r1.json()
    p(b1.get("updated") == 0 and b1.get("skipped") == 0,
      f"mark-sent empty: updated=0, skipped=0 (got updated={b1.get('updated')}, skipped={b1.get('skipped')})")
    p(b1.get("updated_ids") == [] and b1.get("skipped_ids") == [], "mark-sent empty ids lists are []")

    r2 = requests.post(f"{BASE}/shipments/delivery-confirmation/mark-delivered",
                       json={"shipment_ids": []}, headers=auth(token), timeout=20)
    p(r2.status_code == 200, "mark-delivered empty list returns 200")
    b2 = r2.json()
    p(b2.get("updated") == 0, f"mark-delivered empty: updated=0 (got {b2.get('updated')})")


def main():
    print(f"BASE={BASE}")
    token = login(ADMIN_EMAIL, ADMIN_PASSWORD)
    user_id = get_user_id(ADMIN_EMAIL)
    print(f"Logged in admin (user_id={user_id}), token=***{token[-10:]}")

    created_ids: List[str] = []
    try:
        test_1_default_threshold_shape(token)

        ids = test_2_and_3_thresholds(token, user_id)
        created_ids.extend([ids["sid_a"], ids["sid_b"]])

        test_5_mark_sent(token, ids["sid_a"], ids["sid_b"])

        md_ids = test_6_mark_delivered(token, user_id)
        created_ids.extend(md_ids)

        test_7_empty_ids(token)
    finally:
        for sid in created_ids:
            try:
                delete_shipment_direct(sid)
            except Exception as e:
                print(f"cleanup failed for {sid}: {e}")

    print("\n========== SUMMARY ==========")
    print(f"PASSED: {len(PASSED)}")
    print(f"FAILED: {len(FAILED)}")
    if FAILED:
        print("\n--- FAILURES ---")
        for f in FAILED:
            print(f)
        sys.exit(1)
    print("All assertions passed.")


if __name__ == "__main__":
    main()
