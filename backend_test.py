"""
Phase-12 Step-2 Backend Tests — Scanner Flow + Mark-Processing
Tests:
  1. POST /api/shipments/bulk-mark-processing  (NEW)
  2. POST /api/shipments/scan-dispatch         (UPDATED)
  3. POST /api/shipments/scan-ship             (UPDATED)

Auth: admin@test.com / Admin@12345
"""
import os
import sys
import json
import uuid
import requests

BASE = "https://logistics-hub-740.preview.emergentagent.com/api"
EMAIL = "admin@test.com"
PASSWORD = "Admin@12345"

results = []
def log(name, ok, detail=""):
    results.append((name, ok, detail))
    sym = "PASS" if ok else "FAIL"
    print(f"[{sym}] {name}" + (f"  -- {detail}" if detail else ""))

# ── login ───────────────────────────────────────────────────────────
print(f"== Logging in as {EMAIL} ==")
r = requests.post(f"{BASE}/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=15)
if r.status_code != 200:
    print(f"LOGIN FAILED: {r.status_code} {r.text}")
    sys.exit(1)
token = r.json()["token"]
H = {"Authorization": f"Bearer {token}"}
print("Logged in OK\n")

# ── Helper: get an existing courier id (we won't create one) ────────
r = requests.get(f"{BASE}/couriers", headers=H, timeout=15)
courier_id = None
courier_name = ""
if r.status_code == 200 and r.json():
    courier_id = r.json()[0]["id"]
    courier_name = r.json()[0]["name"]
    print(f"Using courier: {courier_name} ({courier_id})")
else:
    print("WARN: No couriers found; will create shipment without courier_id")

# ── Setup: create 4 Pending test shipments ──────────────────────────
suffix = uuid.uuid4().hex[:6].upper()
created_shipments = []
print(f"\n== Setup: Creating 4 test shipments (suffix {suffix}) ==")
for i in range(4):
    payload = {
        "tracking_id": f"TST{suffix}{i:02d}",
        "courier_id": courier_id,
        "courier_name": courier_name,
        "customer_name": f"Test Customer {i+1}",
        "customer_phone": f"99999{suffix[:5]}",
        "address_line1": "12 Test Lane",
        "city": "Ahmedabad",
        "state": "Gujarat",
        "pincode": "380001",
        "amount": 100.0 + i,
        "payment_mode": "Prepaid",
    }
    r = requests.post(f"{BASE}/shipments", headers=H, json=payload, timeout=15)
    if r.status_code != 200:
        print(f"  Failed to create shipment {i}: {r.status_code} {r.text[:200]}")
        sys.exit(1)
    s = r.json()
    created_shipments.append(s)
    print(f"  Created id={s['id'][:8]} tracking={s['tracking_id']} status={s['status']}")

# Sanity: all should be Pending
for s in created_shipments:
    log(f"setup status==Pending for {s['tracking_id']}", s["status"] == "Pending", f"got {s['status']}")

# Shorthand
S0, S1, S2, S3 = created_shipments  # all Pending

# ════════════════════════════════════════════════════════════════════
# 1. POST /api/shipments/bulk-mark-processing  (NEW)
# ════════════════════════════════════════════════════════════════════
print("\n══ TEST GROUP 1: bulk-mark-processing ══")

# 1a. Empty list
r = requests.post(f"{BASE}/shipments/bulk-mark-processing", headers=H, json={"shipment_ids": []}, timeout=15)
ok = r.status_code == 200 and r.json() == {
    "updated": 0, "skipped": 0, "not_found": 0,
    "updated_ids": [], "skipped_ids": [], "not_found_ids": []
}
log("1a empty list returns zeros", ok, f"status={r.status_code} body={r.text[:200]}")

# 1b. 2 valid Pending IDs (S0, S1)
ids = [S0["id"], S1["id"]]
r = requests.post(f"{BASE}/shipments/bulk-mark-processing", headers=H, json={"shipment_ids": ids}, timeout=15)
body = r.json() if r.status_code == 200 else {}
ok = r.status_code == 200 and body.get("updated") == 2 and body.get("skipped") == 0 and body.get("not_found") == 0
log("1b 2 Pending → updated=2,skipped=0,not_found=0", ok, json.dumps(body))
ok = set(body.get("updated_ids", [])) == set(ids)
log("1b updated_ids match input", ok, f"got={body.get('updated_ids')}")

# Verify S0 status == Processing AND processing_started_at is set
r = requests.get(f"{BASE}/shipments/{S0['id']}", headers=H, timeout=15)
sd = r.json() if r.status_code == 200 else {}
ok = sd.get("status") == "Processing"
log("1b S0 status==Processing after bulk", ok, f"got status={sd.get('status')}")
ok = bool(sd.get("processing_started_at"))
log("1b S0 processing_started_at set", ok, f"got={sd.get('processing_started_at')}")

# 1c. Mix: 1 Pending (S2) + 1 Shipped (we'll set S3 to Shipped via PUT)
r = requests.put(f"{BASE}/shipments/{S3['id']}", headers=H, json={"status": "Shipped"}, timeout=15)
log("1c setup PUT S3→Shipped", r.status_code == 200, f"status={r.status_code}")

ids = [S2["id"], S3["id"]]
r = requests.post(f"{BASE}/shipments/bulk-mark-processing", headers=H, json={"shipment_ids": ids}, timeout=15)
body = r.json() if r.status_code == 200 else {}
ok = r.status_code == 200 and body.get("updated") == 1 and body.get("skipped") == 1 and body.get("not_found") == 0
log("1c 1Pending+1Shipped → updated=1,skipped=1", ok, json.dumps(body))
ok = body.get("updated_ids") == [S2["id"]] and body.get("skipped_ids") == [S3["id"]]
log("1c id buckets correct", ok, f"updated={body.get('updated_ids')} skipped={body.get('skipped_ids')}")

# 1d. Bad id
bad_id = "non-existent-id-" + uuid.uuid4().hex[:8]
r = requests.post(f"{BASE}/shipments/bulk-mark-processing", headers=H, json={"shipment_ids": [bad_id]}, timeout=15)
body = r.json() if r.status_code == 200 else {}
ok = r.status_code == 200 and body.get("updated") == 0 and body.get("skipped") == 0 and body.get("not_found") == 1
log("1d bad id → not_found=1", ok, json.dumps(body))
ok = body.get("not_found_ids") == [bad_id]
log("1d not_found_ids correct", ok, f"got={body.get('not_found_ids')}")

# State after this group:
#   S0=Processing, S1=Processing, S2=Processing, S3=Shipped

# ════════════════════════════════════════════════════════════════════
# 2. POST /api/shipments/scan-dispatch  (UPDATED)
# ════════════════════════════════════════════════════════════════════
print("\n══ TEST GROUP 2: scan-dispatch ══")

# 2a. Scan a Processing shipment → outcome:moved, no hint
# Use S0 (Processing)
r = requests.post(f"{BASE}/shipments/scan-dispatch", headers=H, json={"tracking_id": S0["tracking_id"]}, timeout=15)
body = r.json() if r.status_code == 200 else {}
ok = r.status_code == 200 and body.get("outcome") == "moved"
log("2a Processing→Dispatch outcome=moved", ok, json.dumps({"outcome": body.get("outcome"), "reason": body.get("reason")}))
ok = "hint" not in body
log("2a Processing→Dispatch NO hint", ok, f"hint={body.get('hint')}")
ok = (body.get("shipment") or {}).get("status") in ("Dispatch", "Dispatched", "Ready to Ship", "ReadyToShip")
log("2a shipment.status now Dispatch", ok, f"status={(body.get('shipment') or {}).get('status')}")

# 2b. Scan a Pending shipment → outcome:moved, hint=skipped_processing
# We need a fresh Pending shipment. Create one more.
r = requests.post(f"{BASE}/shipments", headers=H, json={
    "tracking_id": f"TST{suffix}P1",
    "courier_id": courier_id,
    "courier_name": courier_name,
    "customer_name": "Pending Skip Test",
    "customer_phone": "9000000001",
    "address_line1": "1 Pending St",
    "city": "Ahmedabad", "state": "Gujarat", "pincode": "380001",
    "amount": 50.0, "payment_mode": "Prepaid",
}, timeout=15)
SP = r.json()
log("2b setup created Pending shipment", r.status_code == 200 and SP.get("status") == "Pending", f"status={SP.get('status')}")

r = requests.post(f"{BASE}/shipments/scan-dispatch", headers=H, json={"tracking_id": SP["tracking_id"]}, timeout=15)
body = r.json() if r.status_code == 200 else {}
ok = r.status_code == 200 and body.get("outcome") == "moved"
log("2b Pending→Dispatch outcome=moved", ok, json.dumps({"outcome": body.get("outcome")}))
ok = body.get("hint") == "skipped_processing"
log("2b Pending→Dispatch hint=skipped_processing", ok, f"hint={body.get('hint')}")

# 2c. Scan an already-Dispatched shipment → outcome:already
# S0 is now Dispatch; rescan it.
r = requests.post(f"{BASE}/shipments/scan-dispatch", headers=H, json={"tracking_id": S0["tracking_id"]}, timeout=15)
body = r.json() if r.status_code == 200 else {}
ok = r.status_code == 200 and body.get("outcome") == "already"
log("2c Dispatch→Dispatch outcome=already", ok, f"outcome={body.get('outcome')} reason={body.get('reason')}")

# 2d. Scan a Shipped shipment → outcome:failed, reason starts with "wrong_status"
# S3 is Shipped
r = requests.post(f"{BASE}/shipments/scan-dispatch", headers=H, json={"tracking_id": S3["tracking_id"]}, timeout=15)
body = r.json() if r.status_code == 200 else {}
ok = r.status_code == 200 and body.get("outcome") == "failed"
log("2d Shipped→Dispatch outcome=failed", ok, f"outcome={body.get('outcome')} reason={body.get('reason')}")
ok = isinstance(body.get("reason"), str) and body.get("reason", "").startswith("wrong_status")
log("2d Shipped→Dispatch reason starts with wrong_status", ok, f"reason={body.get('reason')}")

# 2e. Unknown tracking_id → outcome:failed, reason=not_found
r = requests.post(f"{BASE}/shipments/scan-dispatch", headers=H, json={"tracking_id": "NOPENOPENOPE-XYZ-12345"}, timeout=15)
body = r.json() if r.status_code == 200 else {}
ok = r.status_code == 200 and body.get("outcome") == "failed" and body.get("reason") == "not_found"
log("2e unknown tid → failed+not_found", ok, f"outcome={body.get('outcome')} reason={body.get('reason')}")

# ════════════════════════════════════════════════════════════════════
# 3. POST /api/shipments/scan-ship  (UPDATED)
# ════════════════════════════════════════════════════════════════════
print("\n══ TEST GROUP 3: scan-ship ══")

# 3a. Scan a Dispatch shipment → outcome:moved
# S0 is now in Dispatch. Use it.
r = requests.post(f"{BASE}/shipments/scan-ship", headers=H, json={"tracking_id": S0["tracking_id"]}, timeout=15)
body = r.json() if r.status_code == 200 else {}
ok = r.status_code == 200 and body.get("outcome") == "moved"
log("3a Dispatch→Shipped outcome=moved", ok, f"outcome={body.get('outcome')} reason={body.get('reason')}")
ok = (body.get("shipment") or {}).get("status") == "Shipped"
log("3a S0 now Shipped", ok, f"status={(body.get('shipment') or {}).get('status')}")

# 3b. Scan a Pending shipment → outcome:failed, message contains "scan to Ready to Ship first"
# S1 is in Processing — need a Pending one. Create another.
r = requests.post(f"{BASE}/shipments", headers=H, json={
    "tracking_id": f"TST{suffix}P2",
    "courier_id": courier_id, "courier_name": courier_name,
    "customer_name": "Pending Ship Test",
    "customer_phone": "9000000002",
    "address_line1": "2 Pending St",
    "city": "Ahmedabad", "state": "Gujarat", "pincode": "380001",
    "amount": 75.0, "payment_mode": "Prepaid",
}, timeout=15)
SP2 = r.json()
log("3b setup Pending shipment", r.status_code == 200 and SP2.get("status") == "Pending", f"status={SP2.get('status')}")

r = requests.post(f"{BASE}/shipments/scan-ship", headers=H, json={"tracking_id": SP2["tracking_id"]}, timeout=15)
body = r.json() if r.status_code == 200 else {}
ok = r.status_code == 200 and body.get("outcome") == "failed"
log("3b Pending→Ship outcome=failed", ok, f"outcome={body.get('outcome')} reason={body.get('reason')}")
ok = "scan to Ready to Ship first" in (body.get("message") or "")
log("3b message contains 'scan to Ready to Ship first'", ok, f"msg={body.get('message')}")

# 3c. Scan an already-Shipped → outcome:already
r = requests.post(f"{BASE}/shipments/scan-ship", headers=H, json={"tracking_id": S0["tracking_id"]}, timeout=15)
body = r.json() if r.status_code == 200 else {}
ok = r.status_code == 200 and body.get("outcome") == "already"
log("3c Shipped→Ship outcome=already", ok, f"outcome={body.get('outcome')} reason={body.get('reason')}")

# ── Cleanup ─────────────────────────────────────────────────────────
print("\n══ Cleanup ══")
cleanup_ids = [s["id"] for s in created_shipments] + [SP["id"], SP2["id"]]
for sid in cleanup_ids:
    try:
        rr = requests.delete(f"{BASE}/shipments/{sid}", headers=H, timeout=15)
        print(f"  DELETE {sid[:8]} → {rr.status_code}")
    except Exception as e:
        print(f"  DELETE {sid[:8]} failed: {e}")

# ── Summary ─────────────────────────────────────────────────────────
print("\n" + "═" * 60)
passed = sum(1 for _, ok, _ in results if ok)
total = len(results)
print(f"RESULT: {passed}/{total} passed")
fails = [(n, d) for n, ok, d in results if not ok]
if fails:
    print("\nFAILURES:")
    for n, d in fails:
        print(f"  - {n}: {d}")
    sys.exit(1)
print("ALL PASS")
