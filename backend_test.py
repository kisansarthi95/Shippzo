"""
Backend test — Google Sheets Soft-Delete (Tombstone) feature
===========================================================

Scope (per /app/test_result.md, 2026-04-23 iteration):
  1. POST /api/smart-paste → PendingOrder.sheet_row_num is a positive int
     captured from the Google Sheets updatedRange.
  2. DELETE /api/orders/pending/{id} returns
     {"ok": true, "sheet": {"attempted": true, "ok": true, ...}}.
  3. After POST /api/orders/pending/{id}/ship, the returned Shipment
     carries the same sheet_row_num as the pending order.
  4. DELETE /api/shipments/{id} on that shipment → soft-delete attempted+ok.
  5. DELETE /api/shipments/{id} on a legacy shipment without sheet_row_num
     returns {"ok": true, "sheet": {"attempted": false}} and deletes locally.
  6. GET /api/sheets/probe still succeeds afterwards (no regression).
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests

# --- Resolve public base URL from frontend/.env ------------------------------
ENV_PATH = Path("/app/frontend/.env")
BASE_URL = None
if ENV_PATH.exists():
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if line.startswith("EXPO_PUBLIC_BACKEND_URL"):
            _, v = line.split("=", 1)
            BASE_URL = v.strip().strip('"').strip("'")
            break

if not BASE_URL:
    print("FATAL: EXPO_PUBLIC_BACKEND_URL not found in /app/frontend/.env",
          file=sys.stderr)
    sys.exit(2)

API = BASE_URL.rstrip("/") + "/api"
print(f"Testing against: {API}")
print("=" * 72)

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []
created_pending_ids: list[str] = []
created_shipment_ids: list[str] = []
created_legacy_shipment_id: str | None = None


def ok(name: str, detail: str = "") -> None:
    PASS.append(name)
    print(f"  PASS  {name}" + (f" — {detail}" if detail else ""))


def fail(name: str, detail: str) -> None:
    FAIL.append((name, detail))
    print(f"  FAIL  {name} — {detail}")


def pretty(obj) -> str:
    try:
        return json.dumps(obj, indent=2, ensure_ascii=False, default=str)
    except Exception:
        return repr(obj)


# --- Step 0 — probe ----------------------------------------------------------
print("\n[Step 0] GET /api/sheets/probe (baseline)")
try:
    r = requests.get(f"{API}/sheets/probe", timeout=30)
    probe_baseline = r.json()
    print("  response:", pretty(probe_baseline))
    if r.status_code == 200 and probe_baseline.get("ok") is True:
        ok("baseline sheets/probe", f"tab={probe_baseline.get('tab')}")
    else:
        fail("baseline sheets/probe",
             f"status={r.status_code} body={probe_baseline}")
except Exception as e:
    fail("baseline sheets/probe", f"exception: {e}")
    sys.exit(1)

# --- Step 1 — create a pending order via smart-paste -------------------------
print("\n[Step 1] POST /api/smart-paste (Soft Delete Test #1)")
payload_1 = {
    "text": (
        "Name: Soft Delete Test\n"
        "Phone: 9998887770\n"
        "Address: 5 MG Road\n"
        "City: Surat\n"
        "State: Gujarat\n"
        "Pincode: 395001\n"
        "Item: TestItem\n"
        "Amount: 100\n"
        "Payment: COD"
    )
}
pending_id_1 = None
pending_row_1 = None
try:
    r = requests.post(f"{API}/smart-paste", json=payload_1, timeout=45)
    print(f"  status: {r.status_code}")
    body = r.json()
    print("  body:", pretty(body))
    if r.status_code != 200:
        fail("smart-paste #1 create", f"status={r.status_code}")
    else:
        pending_id_1 = body.get("id")
        pending_row_1 = body.get("sheet_row_num")
        if not pending_id_1:
            fail("smart-paste #1 create", "missing id")
        else:
            created_pending_ids.append(pending_id_1)
            ok("smart-paste #1 returns PendingOrder", f"id={pending_id_1}")
        if isinstance(pending_row_1, int) and pending_row_1 > 1:
            ok("smart-paste #1 sheet_row_num", f"row={pending_row_1}")
        else:
            fail("smart-paste #1 sheet_row_num",
                 f"expected positive int > 1, got {pending_row_1!r}")
        if body.get("customer_name", "").lower().startswith("soft delete"):
            ok("smart-paste #1 parsed name", body.get("customer_name"))
        else:
            fail("smart-paste #1 parsed name",
                 f"got {body.get('customer_name')!r}")
        if body.get("customer_phone") == "9998887770":
            ok("smart-paste #1 parsed phone")
        else:
            fail("smart-paste #1 parsed phone",
                 f"got {body.get('customer_phone')!r}")
except Exception as e:
    fail("smart-paste #1 create", f"exception: {e}")

# --- Step 2 — DELETE pending order -------------------------------------------
print("\n[Step 2] DELETE /api/orders/pending/{id}")
if not pending_id_1:
    fail("pending DELETE", "no pending id from step 1")
else:
    try:
        r = requests.delete(f"{API}/orders/pending/{pending_id_1}", timeout=60)
        print(f"  status: {r.status_code}")
        body = r.json()
        print("  body:", pretty(body))
        if r.status_code != 200:
            fail("pending DELETE status", f"status={r.status_code}")
        else:
            ok("pending DELETE 200")
        if body.get("ok") is True:
            ok("pending DELETE ok=true")
        else:
            fail("pending DELETE ok", f"ok={body.get('ok')!r}")
        sheet = body.get("sheet") or {}
        if sheet.get("attempted") is True and sheet.get("ok") is True:
            ok("pending DELETE sheet.attempted+ok",
               f"row={sheet.get('row')} tab={sheet.get('tab')}")
            if pending_id_1 in created_pending_ids:
                created_pending_ids.remove(pending_id_1)
        else:
            fail("pending DELETE sheet",
                 f"attempted/ok mismatch: sheet={pretty(sheet)}")
        g = requests.get(f"{API}/orders/pending/{pending_id_1}", timeout=15)
        if g.status_code == 404:
            ok("pending DELETE local purge (404)")
        else:
            fail("pending DELETE local purge",
                 f"expected 404, got {g.status_code}")
    except Exception as e:
        fail("pending DELETE", f"exception: {e}")

# --- Step 3 — smart-paste again, ship, delete shipment -----------------------
print("\n[Step 3] Smart-paste #2 → ship → delete shipment")
payload_2 = {
    "text": (
        "Name: Soft Delete Ship Flow\n"
        "Phone: 9112233445\n"
        "Address: 27 Laxmi Nagar\n"
        "City: Ahmedabad\n"
        "State: Gujarat\n"
        "Pincode: 380015\n"
        "Item: Cotton Kurta\n"
        "Amount: 550\n"
        "Payment: Prepaid"
    )
}
pending_id_2 = None
pending_row_2 = None
try:
    r = requests.post(f"{API}/smart-paste", json=payload_2, timeout=45)
    print(f"  smart-paste#2 status: {r.status_code}")
    body = r.json()
    if r.status_code == 200 and body.get("id"):
        pending_id_2 = body["id"]
        pending_row_2 = body.get("sheet_row_num")
        created_pending_ids.append(pending_id_2)
        print(f"  pending_id_2={pending_id_2} sheet_row_num={pending_row_2}")
        if isinstance(pending_row_2, int) and pending_row_2 > 1:
            ok("smart-paste #2 sheet_row_num", f"row={pending_row_2}")
        else:
            fail("smart-paste #2 sheet_row_num", f"got {pending_row_2!r}")
    else:
        fail("smart-paste #2 create", f"status={r.status_code} body={body}")
except Exception as e:
    fail("smart-paste #2 create", f"exception: {e}")

courier_id = None
try:
    r = requests.get(f"{API}/couriers", timeout=30)
    if r.status_code == 200:
        couriers = r.json()
        if couriers:
            courier_id = couriers[0]["id"]
            print(f"  using courier_id={courier_id} ({couriers[0].get('name')})")
        else:
            fail("ship flow — couriers list", "empty list")
    else:
        fail("ship flow — couriers list", f"status={r.status_code}")
except Exception as e:
    fail("ship flow — couriers list", f"exception: {e}")

ship_id = None
ship_row = None
if pending_id_2 and courier_id:
    try:
        r = requests.post(
            f"{API}/orders/pending/{pending_id_2}/ship",
            json={"courier_id": courier_id},
            timeout=45,
        )
        print(f"  ship status: {r.status_code}")
        body = r.json()
        print("  ship body:", pretty(body))
        if r.status_code == 200:
            ship_id = body.get("id")
            ship_row = body.get("sheet_row_num")
            if ship_id:
                created_shipment_ids.append(ship_id)
            if isinstance(ship_row, int) and ship_row == pending_row_2:
                ok("ship forwards sheet_row_num",
                   f"pending={pending_row_2} shipment={ship_row}")
            else:
                fail("ship forwards sheet_row_num",
                     f"pending={pending_row_2!r} shipment={ship_row!r}")
        else:
            fail("ship POST", f"status={r.status_code} body={body}")
    except Exception as e:
        fail("ship POST", f"exception: {e}")

if ship_id:
    try:
        r = requests.delete(f"{API}/shipments/{ship_id}", timeout=60)
        print(f"  shipment DELETE status: {r.status_code}")
        body = r.json()
        print("  shipment DELETE body:", pretty(body))
        if r.status_code != 200:
            fail("shipment DELETE status", f"status={r.status_code}")
        else:
            ok("shipment DELETE 200")
        if body.get("ok") is True:
            ok("shipment DELETE ok=true")
        else:
            fail("shipment DELETE ok", f"ok={body.get('ok')!r}")
        sheet = body.get("sheet") or {}
        if sheet.get("attempted") is True and sheet.get("ok") is True:
            ok("shipment DELETE sheet.attempted+ok", f"row={sheet.get('row')}")
            if ship_id in created_shipment_ids:
                created_shipment_ids.remove(ship_id)
        else:
            fail("shipment DELETE sheet",
                 f"attempted/ok mismatch: sheet={pretty(sheet)}")
        g = requests.get(f"{API}/shipments/{ship_id}", timeout=15)
        if g.status_code == 404:
            ok("shipment DELETE local purge (404)")
        else:
            fail("shipment DELETE local purge",
                 f"expected 404, got {g.status_code}")
    except Exception as e:
        fail("shipment DELETE", f"exception: {e}")

# --- Step 4 — legacy shipment (no sheet_row_num) -----------------------------
print("\n[Step 4] DELETE legacy shipment (no sheet_row_num) → attempted=false")
try:
    payload_legacy = {
        "tracking_id": f"LEGACY-TEST-{int(time.time())}",
        "customer_name": "Legacy Soft Delete",
        "customer_phone": "9000011122",
        "address_line1": "1 Legacy Street",
        "city": "Mumbai",
        "state": "Maharashtra",
        "pincode": "400001",
        "payment_mode": "Prepaid",
        "amount": 1.0,
        "item_description": "legacy test",
    }
    r = requests.post(f"{API}/shipments", json=payload_legacy, timeout=30)
    print(f"  create legacy status: {r.status_code}")
    body = r.json()
    if r.status_code == 200 and body.get("id"):
        created_legacy_shipment_id = body["id"]
        created_shipment_ids.append(created_legacy_shipment_id)
        row = body.get("sheet_row_num")
        if row in (None, 0):
            ok("legacy shipment created (no sheet_row_num)")
        else:
            fail("legacy shipment created",
                 f"unexpectedly has sheet_row_num={row}")
    else:
        fail("legacy shipment create", f"status={r.status_code} body={body}")
except Exception as e:
    fail("legacy shipment create", f"exception: {e}")

if created_legacy_shipment_id:
    try:
        r = requests.delete(
            f"{API}/shipments/{created_legacy_shipment_id}", timeout=30
        )
        print(f"  legacy DELETE status: {r.status_code}")
        body = r.json()
        print("  legacy DELETE body:", pretty(body))
        if r.status_code != 200:
            fail("legacy shipment DELETE status",
                 f"status={r.status_code}")
        else:
            ok("legacy shipment DELETE 200")
        if body.get("ok") is True:
            ok("legacy shipment DELETE ok=true")
        else:
            fail("legacy shipment DELETE ok", f"ok={body.get('ok')!r}")
        sheet = body.get("sheet") or {}
        if sheet.get("attempted") is False:
            ok("legacy shipment DELETE sheet.attempted=false")
            if created_legacy_shipment_id in created_shipment_ids:
                created_shipment_ids.remove(created_legacy_shipment_id)
        else:
            fail("legacy shipment DELETE sheet.attempted",
                 f"sheet={pretty(sheet)}")
        g = requests.get(
            f"{API}/shipments/{created_legacy_shipment_id}", timeout=15
        )
        if g.status_code == 404:
            ok("legacy shipment DELETE local purge (404)")
        else:
            fail("legacy shipment DELETE local purge",
                 f"expected 404, got {g.status_code}")
    except Exception as e:
        fail("legacy shipment DELETE", f"exception: {e}")

# --- Step 5 — probe still healthy -------------------------------------------
print("\n[Step 5] GET /api/sheets/probe (post-delete)")
try:
    r = requests.get(f"{API}/sheets/probe", timeout=30)
    body = r.json()
    print("  response:", pretty(body))
    if r.status_code == 200 and body.get("ok") is True:
        ok("post-test sheets/probe")
    else:
        fail("post-test sheets/probe",
             f"status={r.status_code} body={body}")
except Exception as e:
    fail("post-test sheets/probe", f"exception: {e}")

# --- Cleanup -----------------------------------------------------------------
print("\n[Cleanup] Removing any leftover test artifacts")
for pid in list(created_pending_ids):
    try:
        r = requests.delete(f"{API}/orders/pending/{pid}", timeout=30)
        print(f"  cleanup pending {pid}: {r.status_code}")
    except Exception as e:
        print(f"  cleanup pending {pid}: error {e}")
for sid in list(created_shipment_ids):
    try:
        r = requests.delete(f"{API}/shipments/{sid}", timeout=30)
        print(f"  cleanup shipment {sid}: {r.status_code}")
    except Exception as e:
        print(f"  cleanup shipment {sid}: error {e}")

# --- Summary -----------------------------------------------------------------
print("\n" + "=" * 72)
print(f"PASS: {len(PASS)}")
print(f"FAIL: {len(FAIL)}")
for t in PASS:
    print(f"  ✓ {t}")
for name, detail in FAIL:
    print(f"  ✗ {name} — {detail}")

sys.exit(0 if not FAIL else 1)
