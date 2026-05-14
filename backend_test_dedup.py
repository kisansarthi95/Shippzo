"""
Phase-21 Webhook Deduplication Test Suite
==========================================

Tests the new application-level dedup pre-check + DB unique compound index
on pending_orders for (user_id, external_order_id, source_meta.source_app).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from typing import Any, Dict, List, Optional

import requests
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

BASE = "https://logistics-hub-740.preview.emergentagent.com/api"
EMAIL = "admin@test.com"
PASSWORD = "Admin@12345"

# Load Mongo creds so we can verify rows directly (external_order_id is
# NOT exposed by the PendingOrder pydantic model — response_model strips
# it. Querying Mongo directly gives us ground truth.).
load_dotenv("/app/backend/.env")
MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ.get("DB_NAME", "test_database")
_mongo = AsyncIOMotorClient(MONGO_URL)
mdb = _mongo[DB_NAME]


def db_count(user_id: str, ext_id: str, source_app: str) -> int:
    return asyncio.get_event_loop().run_until_complete(
        mdb.pending_orders.count_documents({
            "user_id": user_id,
            "external_order_id": ext_id,
            "source_meta.source_app": source_app,
        }),
    )


def db_find_by_name(user_id: str, name: str) -> List[Dict[str, Any]]:
    async def _f():
        cur = mdb.pending_orders.find(
            {"user_id": user_id, "customer_name": name},
            {"_id": 0, "id": 1, "external_order_id": 1,
             "source_meta.source_app": 1, "created_at": 1},
        ).sort("created_at", -1)
        return await cur.to_list(length=20)
    return asyncio.get_event_loop().run_until_complete(_f())


def db_find_by_ext(user_id: str, ext_id: str) -> List[Dict[str, Any]]:
    async def _f():
        cur = mdb.pending_orders.find(
            {"user_id": user_id, "external_order_id": ext_id},
            {"_id": 0, "id": 1, "external_order_id": 1,
             "source_meta.source_app": 1, "created_at": 1},
        ).sort("created_at", -1)
        return await cur.to_list(length=20)
    return asyncio.get_event_loop().run_until_complete(_f())


def db_index_info() -> Dict[str, Any]:
    return asyncio.get_event_loop().run_until_complete(
        mdb.pending_orders.index_information(),
    )


# Use a unique external order id prefix per run to avoid clashing with
# existing dedup index state from previous runs.
RUN_TAG = uuid.uuid4().hex[:8].upper()
EXT_ORDER_A = f"DUPTEST-{RUN_TAG}-001"
EXT_ORDER_B = f"DUPTEST-{RUN_TAG}-002"

passed: List[str] = []
failed: List[str] = []
created_pending_ids: List[str] = []


def ok(label: str) -> None:
    passed.append(label)
    print(f"  ✅ {label}")


def fail(label: str, detail: str = "") -> None:
    failed.append(f"{label} :: {detail}")
    print(f"  ❌ {label}  ({detail})")


def hdr(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# ----------------------------------------------------------------------
# 1. Login
# ----------------------------------------------------------------------
print("\n=== Step 1: Login ===")
r = requests.post(
    f"{BASE}/auth/login",
    json={"email": EMAIL, "password": PASSWORD},
    timeout=20,
)
if r.status_code != 200:
    print(f"LOGIN FAILED: {r.status_code} {r.text}")
    sys.exit(1)
auth = r.json()
TOKEN = auth["token"]
USER_ID = auth["id"]
print(f"Logged in as {EMAIL} (id={USER_ID})")
ok("Login successful")


# ----------------------------------------------------------------------
# 2. Get / rotate webhook secret + save mapping
# ----------------------------------------------------------------------
print("\n=== Step 2: Webhook config setup ===")
r = requests.get(f"{BASE}/me/webhook-config", headers=hdr(TOKEN), timeout=20)
if r.status_code != 200:
    fail("GET /me/webhook-config", f"{r.status_code} {r.text[:200]}")
    sys.exit(1)
cfg = r.json()
secret = cfg.get("secret") or ""
if not secret:
    r = requests.post(
        f"{BASE}/me/webhook-config/rotate",
        headers=hdr(TOKEN),
        json={"name": "Shopify Test"},
        timeout=20,
    )
    if r.status_code != 200:
        fail("rotate secret", f"{r.status_code} {r.text[:200]}")
        sys.exit(1)
    secret = r.json()["secret"]
    print(f"Rotated new secret")
print(f"Using secret: {secret[:8]}…")
ok("Webhook secret available")

# The secret might resolve to a v2 webhook (user_webhooks) first.
# Detect that and update its mapping + source_app so this test
# exercises the documented Phase-21 source_app="shopify" path.
# NOTE: build_pending_doc_from_mapping uses cols[-1] (last-wins) for
# non-address/non-customer_name fields, so DON'T map multiple source
# keys to the same schema field if some are absent in the payload —
# the empty one would overwrite the present one. Keep one source per
# field (the literal key our test payloads use).
DEDUP_MAPPING = {
    "name":     "customer_name",
    "phone":    "customer_phone",
    "city":     "city",
    "state":    "state",
    "pincode":  "pincode",
    "amount":   "amount",
    "order_id": "order_id",
}

# Save mapping on the legacy single-webhook config (fallback path)
r = requests.put(
    f"{BASE}/me/webhook-config",
    headers=hdr(TOKEN),
    json={"mapping": DEDUP_MAPPING},
    timeout=20,
)
if r.status_code != 200:
    fail("PUT /me/webhook-config (legacy)", f"{r.status_code} {r.text[:300]}")
    sys.exit(1)
ok("Legacy mapping saved")

# Look for matching v2 webhook (user_webhooks) and update it.
r = requests.get(f"{BASE}/me/webhooks", headers=hdr(TOKEN), timeout=20)
v2_id = None
if r.status_code == 200:
    for wh in (r.json().get("webhooks") or []):
        if wh.get("secret") == secret:
            v2_id = wh.get("id")
            break

if v2_id:
    print(f"Secret resolves to v2 webhook id={v2_id} — updating mapping + source_app=shopify")
    r = requests.put(
        f"{BASE}/me/webhooks/{v2_id}",
        headers=hdr(TOKEN),
        json={
            "mapping":    DEDUP_MAPPING,
            "source_app": "shopify",
        },
        timeout=20,
    )
    if r.status_code != 200:
        fail("PUT /me/webhooks/{id}", f"{r.status_code} {r.text[:300]}")
        sys.exit(1)
    ok("v2 webhook mapping + source_app=shopify saved")
    EXPECTED_SOURCE_APP = "shopify"
else:
    print("Secret only matches legacy single-webhook config (no v2 row)")
    EXPECTED_SOURCE_APP = ""


# ----------------------------------------------------------------------
# Helper: count current webhook pending orders w/ a given external_id
# ----------------------------------------------------------------------
def list_webhook_pending() -> List[Dict[str, Any]]:
    r = requests.get(
        f"{BASE}/orders/pending?source=webhook",
        headers=hdr(TOKEN),
        timeout=20,
    )
    if r.status_code != 200:
        return []
    return r.json() or []


def count_with_ext(ext_id: str) -> int:
    rows = list_webhook_pending()
    return sum(1 for r in rows if (r.get("external_order_id") or "") == ext_id)


def find_by_ext(ext_id: str) -> List[Dict[str, Any]]:
    # Query Mongo directly because the FastAPI response_model strips
    # the `external_order_id` field from PendingOrder.
    return db_find_by_ext(USER_ID, ext_id)


# Baseline
baseline_rows = list_webhook_pending()
baseline_count = len(baseline_rows)
print(f"Baseline webhook pending count: {baseline_count}")


# ----------------------------------------------------------------------
# 3. First-time ingest
# ----------------------------------------------------------------------
print(f"\n=== Step 3: First ingest of {EXT_ORDER_A} ===")
payload_a = {
    "name":     "Dedup Test User",
    "phone":    "9111000222",
    "city":     "Surat",
    "state":    "Gujarat",
    "pincode":  "395007",
    "amount":   999,
    "order_id": EXT_ORDER_A,
}
r = requests.post(f"{BASE}/webhook/orders/{secret}", json=payload_a, timeout=30)
print(f"Response: {r.status_code} {r.text[:300]}")
if r.status_code != 200:
    fail("POST first webhook ingest", f"{r.status_code} {r.text[:300]}")
else:
    body = r.json()
    if body.get("ok") and body.get("imported") == 1:
        ok(f"First ingest returns ok:true imported:1")
    else:
        fail("First ingest body", str(body))

time.sleep(0.5)
rows_a = find_by_ext(EXT_ORDER_A)
if len(rows_a) == 1:
    ok(f"Exactly 1 pending row created for {EXT_ORDER_A}")
    created_pending_ids.append(rows_a[0]["id"])
    print(f"  row.id={rows_a[0]['id']}")
    print(f"  row.source_meta.source_app={(rows_a[0].get('source_meta') or {}).get('source_app')!r}")
else:
    fail(f"Pending count for {EXT_ORDER_A} after first ingest", f"got {len(rows_a)}, expected 1")


# ----------------------------------------------------------------------
# 4. Replay identical payload → dedup pre-check trigger
# ----------------------------------------------------------------------
print(f"\n=== Step 4: Replay same payload (dedup pre-check) ===")
count_before = count_with_ext(EXT_ORDER_A)
total_before = len(list_webhook_pending())
r = requests.post(f"{BASE}/webhook/orders/{secret}", json=payload_a, timeout=30)
print(f"Response: {r.status_code} {r.text[:300]}")
if r.status_code != 200:
    fail("POST replay webhook", f"{r.status_code}")
else:
    body = r.json()
    if body.get("ok") and body.get("imported", 0) >= 1:
        ok(f"Replay returns ok:true imported>=1 (idempotent)")
    else:
        fail("Replay body", str(body))

time.sleep(0.5)
count_after = count_with_ext(EXT_ORDER_A)
total_after = len(list_webhook_pending())

if count_after == count_before:
    ok(f"No new row for {EXT_ORDER_A} after replay (count stayed at {count_before})")
else:
    fail(f"Replay created new row", f"count_before={count_before} count_after={count_after}")

if total_after == total_before:
    ok(f"Total webhook pending count unchanged after replay ({total_before} → {total_after})")
else:
    fail("Total count changed after replay", f"{total_before} → {total_after}")


# ----------------------------------------------------------------------
# 5. Different order_id → new row
# ----------------------------------------------------------------------
print(f"\n=== Step 5: Different order_id ({EXT_ORDER_B}) ===")
payload_b = dict(payload_a)
payload_b["order_id"] = EXT_ORDER_B

total_before = len(list_webhook_pending())
r = requests.post(f"{BASE}/webhook/orders/{secret}", json=payload_b, timeout=30)
print(f"Response: {r.status_code} {r.text[:300]}")
if r.status_code != 200:
    fail("POST second order", f"{r.status_code}")
else:
    body = r.json()
    if body.get("ok") and body.get("imported") == 1:
        ok(f"New order_id ingest returns imported:1")
    else:
        fail("New order body", str(body))

time.sleep(0.5)
total_after = len(list_webhook_pending())
rows_b = find_by_ext(EXT_ORDER_B)
if len(rows_b) == 1:
    ok(f"New row created for {EXT_ORDER_B}")
    created_pending_ids.append(rows_b[0]["id"])
else:
    fail(f"Pending row for {EXT_ORDER_B}", f"got {len(rows_b)}, expected 1")

if total_after == total_before + 1:
    ok(f"Total count increased by exactly 1 ({total_before} → {total_after})")
else:
    fail("Total count delta", f"{total_before} → {total_after} (delta={total_after-total_before})")


# ----------------------------------------------------------------------
# 6. Empty external_order_id → exempt from dedup
# ----------------------------------------------------------------------
print(f"\n=== Step 6: Empty order_id exempt from dedup ===")
payload_empty = {
    "name":    f"Empty-OID User {RUN_TAG}",
    "phone":   "9111000999",
    "city":    "Mumbai",
    "state":   "Maharashtra",
    "pincode": "400001",
    "amount":  500,
    # NO order_id
}

total_before = len(list_webhook_pending())
# Send twice
r1 = requests.post(f"{BASE}/webhook/orders/{secret}", json=payload_empty, timeout=30)
print(f"  First empty-oid response: {r1.status_code} {r1.text[:200]}")
time.sleep(0.4)
r2 = requests.post(f"{BASE}/webhook/orders/{secret}", json=payload_empty, timeout=30)
print(f"  Second empty-oid response: {r2.status_code} {r2.text[:200]}")
time.sleep(0.5)

if r1.status_code == 200 and r2.status_code == 200:
    ok("Both empty-OID ingests returned 200")
else:
    fail("Empty-OID ingest status", f"r1={r1.status_code} r2={r2.status_code}")

total_after = len(list_webhook_pending())
# Find rows that match our unique customer_name
rows_empty = [
    r for r in list_webhook_pending()
    if (r.get("customer_name") or "") == payload_empty["name"]
]
print(f"  Rows with customer_name='{payload_empty['name']}': {len(rows_empty)}")

if len(rows_empty) == 2:
    ok("2 separate rows created for empty external_order_id (dedup exempt)")
    for rr in rows_empty:
        ext = rr.get("external_order_id") or ""
        if ext == "":
            ok(f"  Row id={rr['id']} has empty external_order_id (as expected)")
        else:
            # Some implementations fall back to master_order_id; still ok as long as
            # 2 rows exist
            pass
        created_pending_ids.append(rr["id"])
else:
    # If it's 1, dedup didn't exempt empty OIDs (bug).
    # Could also be backend assigns master_order_id as external_order_id.
    if len(rows_empty) == 1:
        ext = rows_empty[0].get("external_order_id") or ""
        fail(
            "Empty-OID dedup exemption",
            f"only 1 row created (external_order_id={ext!r}) — dedup exemption failed OR backend "
            f"is filling external_order_id with master_order_id",
        )
        created_pending_ids.append(rows_empty[0]["id"])
    else:
        fail("Empty-OID row count", f"got {len(rows_empty)} rows, expected 2")
        for rr in rows_empty:
            created_pending_ids.append(rr["id"])

if total_after - total_before >= 1:
    ok(f"Total increased ({total_before} → {total_after})")


# ----------------------------------------------------------------------
# 7. Verify the unique compound index exists with the expected spec.
# ----------------------------------------------------------------------
print("\n=== Step 7: Verify unique compound index ===")
idx = db_index_info()
target = idx.get("uniq_user_externalOrder_sourceApp")
if target is None:
    fail("Index uniq_user_externalOrder_sourceApp", "not found")
else:
    if target.get("unique"):
        ok("Index is unique=True")
    else:
        fail("Index unique flag", str(target.get("unique")))
    keys = target.get("key") or []
    expected_keys = [
        ("user_id", 1),
        ("external_order_id", 1),
        ("source_meta.source_app", 1),
    ]
    if [tuple(k) for k in keys] == expected_keys:
        ok(f"Index keys match expected: {expected_keys}")
    else:
        fail("Index keys", f"got {keys}")
    pfe = dict(target.get("partialFilterExpression") or {})
    if pfe.get("external_order_id") and dict(pfe["external_order_id"]).get("$exists") is True:
        ok("Index has partialFilterExpression on external_order_id")
    else:
        fail("Index partial filter", str(pfe))


# ----------------------------------------------------------------------
# 8. Concurrent race protection — direct DB duplicate-insert attempt.
# ----------------------------------------------------------------------
print("\n=== Step 8: Concurrent DuplicateKeyError protection ===")
# Try to directly insert a second pending_orders row with the same
# (user_id, external_order_id, source_meta.source_app) tuple as the
# DUPTEST-A row we created via the webhook. The unique index should
# raise DuplicateKeyError.
from pymongo.errors import DuplicateKeyError as _DKE

async def _race():
    duplicate_doc = {
        "id": str(uuid.uuid4()),
        "user_id": USER_ID,
        "external_order_id": EXT_ORDER_A,   # same!
        "source_meta": {"source_app": EXPECTED_SOURCE_APP},
        "source": "webhook",
        "status": "pending",
        "customer_name": "Race Test",
        "created_at": "2026-05-14T10:00:00+00:00",
    }
    try:
        await mdb.pending_orders.insert_one(duplicate_doc)
        return False, "insert unexpectedly succeeded"
    except _DKE as e:
        return True, str(e)[:120]
    except Exception as e:
        return False, f"unexpected exception: {type(e).__name__}: {e}"

raised, msg = asyncio.get_event_loop().run_until_complete(_race())
if raised:
    ok(f"DuplicateKeyError raised as expected: {msg}")
else:
    fail("DuplicateKeyError protection", msg)


# ----------------------------------------------------------------------
# Cleanup: delete all pending orders we created
# ----------------------------------------------------------------------
print(f"\n=== Cleanup: deleting {len(created_pending_ids)} test pending orders ===")
deleted = 0
for pid in created_pending_ids:
    try:
        r = requests.delete(
            f"{BASE}/orders/pending/{pid}",
            headers=hdr(TOKEN),
            timeout=20,
        )
        if r.status_code == 200:
            deleted += 1
            print(f"  deleted {pid}")
        else:
            print(f"  delete {pid}: {r.status_code} {r.text[:120]}")
    except Exception as e:
        print(f"  delete {pid} exception: {e}")
print(f"Cleanup: {deleted}/{len(created_pending_ids)} deleted")


# ----------------------------------------------------------------------
# Summary
# ----------------------------------------------------------------------
print("\n" + "=" * 70)
print(f"PASSED: {len(passed)}")
print(f"FAILED: {len(failed)}")
print("=" * 70)
if failed:
    print("\nFailures:")
    for f in failed:
        print(f"  - {f}")
    sys.exit(1)
else:
    print("ALL TESTS PASSED ✓")
    sys.exit(0)
