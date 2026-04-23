"""
Backend test for Two-Way Status Sync feature.

Scenarios:
1. POST /api/smart-paste → verify sheet_row_num is a positive int > 1
2. POST /api/orders/pending/{id}/ship → verify Shipment has same sheet_row_num,
   non-empty tracking_id, status="Pending"
3. PUT /api/shipments/{id} {"status":"Delivered"} → 200, status=Delivered,
   delivered_at non-empty ISO timestamp
4. PUT /api/shipments/{id} {"status":"Delivered"} AGAIN → 200, no-op for sheet
5. Audit backend logs for expected sync messages
6. DELETE /api/shipments/{id} → {"ok":true,"sheet":{"attempted":true,"ok":true,...}}
7. Regression: create a legacy shipment (no sheet_row_num) → DELETE returns
   {"ok":true,"sheet":{"attempted":false}}
"""

import json
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / "frontend" / ".env")

BASE = (
    os.getenv("REACT_APP_BACKEND_URL")
    or os.getenv("EXPO_PUBLIC_BACKEND_URL")
    or "https://logistics-hub-740.preview.emergentagent.com"
).rstrip("/")
API = f"{BASE}/api"

PASS = 0
FAIL = 0
FAILURES = []


def assert_true(cond, msg, detail=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS: {msg}")
    else:
        FAIL += 1
        FAILURES.append(f"{msg} | detail={detail}")
        print(f"  FAIL: {msg} | detail={detail}")


def req(method, path, **kwargs):
    url = f"{API}{path}"
    r = requests.request(method, url, timeout=30, **kwargs)
    return r


def main():
    print(f"Testing against: {API}")
    print("=" * 70)

    print("\n[0] Sheet probe (pre-flight)")
    r = req("GET", "/sheets/probe")
    print(f"  GET /sheets/probe -> {r.status_code}")
    try:
        probe = r.json()
        print(f"  probe: {probe}")
        assert_true(r.status_code == 200 and probe.get("ok"),
                    "Sheet probe ok", probe)
    except Exception as e:
        assert_true(False, "Sheet probe parse", str(e))
        return

    # ----- Step 1: Smart Paste -----
    print("\n[1] POST /smart-paste")
    payload = {
        "text": (
            "Name: Sync Test\n"
            "Phone: 9112223344\n"
            "Address: 11 Test Blvd\n"
            "City: Ahmedabad\n"
            "State: Gujarat\n"
            "Pincode: 380001\n"
            "Item: Widget\n"
            "Amount: 299\n"
            "Payment: COD"
        )
    }
    r = req("POST", "/smart-paste", json=payload)
    print(f"  -> {r.status_code}")
    assert_true(r.status_code == 200, "smart-paste 200", r.text[:300])
    if r.status_code != 200:
        return
    po = r.json()
    print(f"  body: {json.dumps(po, indent=2)[:900]}")
    pending_id = po.get("id")
    sheet_row_num = po.get("sheet_row_num")
    assert_true(isinstance(sheet_row_num, int) and sheet_row_num > 1,
                "sheet_row_num is positive int > 1",
                f"got={sheet_row_num}")
    assert_true(po.get("customer_name") == "Sync Test", "customer_name parsed")
    assert_true(po.get("customer_phone") == "9112223344", "phone parsed")
    assert_true(po.get("pincode") == "380001", "pincode parsed")
    assert_true(po.get("payment_mode") == "COD", "payment_mode parsed")

    # ----- Step 2: Ship pending order -----
    print("\n[2] POST /orders/pending/{id}/ship")
    r = req("GET", "/couriers")
    couriers = r.json() if r.status_code == 200 else []
    assert_true(len(couriers) > 0, "have couriers", f"count={len(couriers)}")
    courier_id = couriers[0]["id"]
    courier_name = couriers[0]["name"]
    print(f"  using courier: {courier_name} ({courier_id})")

    r = req("POST", f"/orders/pending/{pending_id}/ship",
            json={"courier_id": courier_id, "overrides": {}})
    print(f"  -> {r.status_code}")
    assert_true(r.status_code == 200, "ship 200", r.text[:300])
    if r.status_code != 200:
        return
    ship = r.json()
    print(f"  body: {json.dumps(ship, indent=2)[:900]}")
    ship_id = ship.get("id")
    assert_true(ship.get("sheet_row_num") == sheet_row_num,
                "Shipment.sheet_row_num == PendingOrder.sheet_row_num",
                f"ship={ship.get('sheet_row_num')} po={sheet_row_num}")
    assert_true(bool(ship.get("tracking_id")), "tracking_id non-empty",
                ship.get("tracking_id"))
    assert_true(ship.get("status") == "Pending", "status=Pending",
                ship.get("status"))

    time.sleep(1.0)

    # ----- Step 3: PUT to Delivered -----
    print("\n[3] PUT /shipments/{id} status=Delivered")
    r = req("PUT", f"/shipments/{ship_id}", json={"status": "Delivered"})
    print(f"  -> {r.status_code}")
    assert_true(r.status_code == 200, "PUT Delivered 200", r.text[:300])
    delivered_at_1 = None
    if r.status_code == 200:
        body = r.json()
        print(f"  body: {json.dumps(body, indent=2)[:700]}")
        assert_true(body.get("status") == "Delivered",
                    "returned status==Delivered", body.get("status"))
        delivered_at_1 = body.get("delivered_at") or ""
        assert_true(
            isinstance(delivered_at_1, str)
            and len(delivered_at_1) > 10
            and ("T" in delivered_at_1),
            "delivered_at non-empty ISO",
            delivered_at_1,
        )

    time.sleep(1.0)

    # ----- Step 4: PUT to Delivered AGAIN (no-op) -----
    print("\n[4] PUT /shipments/{id} status=Delivered AGAIN (no-op)")
    r = req("PUT", f"/shipments/{ship_id}", json={"status": "Delivered"})
    print(f"  -> {r.status_code}")
    assert_true(r.status_code == 200, "PUT Delivered (repeat) 200", r.text[:300])
    if r.status_code == 200:
        body2 = r.json()
        assert_true(body2.get("status") == "Delivered",
                    "status still Delivered", body2.get("status"))

    # ----- Step 5: Backend log audit (best-effort) -----
    print("\n[5] Backend log audit")
    log_paths = [
        "/var/log/supervisor/backend.err.log",
        "/var/log/supervisor/backend.out.log",
    ]
    combined_log = ""
    for p in log_paths:
        try:
            if os.path.exists(p):
                with open(p, "r") as f:
                    data = f.read()
                combined_log += data[-200000:]
        except Exception:
            pass
    markers = {
        "append": "Sheet append OK:",
        "dispatched": f"Sheet status sync OK: row={sheet_row_num} Pending → Dispatched",
        "delivered": f"Sheet status sync OK: row={sheet_row_num} → Delivered",
    }
    for name, substr in markers.items():
        found = substr in combined_log
        print(f"  log marker [{name}] present: {found}  ({substr!r})")
        assert_true(found, f"log marker [{name}] found",
                    "not found in backend logs")

    # ----- Step 6: DELETE shipment (soft-delete path) -----
    print("\n[6] DELETE /shipments/{id}")
    r = req("DELETE", f"/shipments/{ship_id}")
    print(f"  -> {r.status_code}")
    assert_true(r.status_code == 200, "DELETE shipment 200", r.text[:300])
    if r.status_code == 200:
        body = r.json()
        print(f"  body: {json.dumps(body, indent=2)}")
        assert_true(body.get("ok") is True, "ok=true", body)
        sheet = body.get("sheet") or {}
        assert_true(sheet.get("attempted") is True,
                    "sheet.attempted=true", sheet)
        assert_true(sheet.get("ok") is True, "sheet.ok=true", sheet)
        assert_true(sheet.get("row") == sheet_row_num,
                    f"sheet.row=={sheet_row_num}", sheet)

    r = req("GET", f"/shipments/{ship_id}")
    assert_true(r.status_code == 404, "shipment 404 post-delete",
                f"status={r.status_code}")

    # ----- Step 7: Regression — legacy shipment (no sheet_row_num) -----
    print("\n[7] Regression: legacy shipment (no sheet_row_num) DELETE")
    legacy_payload = {
        "tracking_id": f"LEG-TEST-{int(time.time())}",
        "courier_id": courier_id,
        "customer_name": "Legacy Regression Test",
        "customer_phone": "9000000001",
        "address_line1": "1 Legacy Lane",
        "city": "Mumbai",
        "state": "Maharashtra",
        "pincode": "400001",
        "payment_mode": "Prepaid",
        "amount": 10.0,
    }
    r = req("POST", "/shipments", json=legacy_payload)
    print(f"  POST /shipments -> {r.status_code}")
    assert_true(r.status_code == 200, "create legacy shipment 200",
                r.text[:300])
    if r.status_code != 200:
        return
    legacy = r.json()
    legacy_id = legacy.get("id")
    assert_true(legacy.get("sheet_row_num") in (None, 0, ""),
                "legacy sheet_row_num missing/null",
                legacy.get("sheet_row_num"))

    r = req("DELETE", f"/shipments/{legacy_id}")
    print(f"  DELETE /shipments/{legacy_id} -> {r.status_code}")
    assert_true(r.status_code == 200, "DELETE legacy 200", r.text[:300])
    if r.status_code == 200:
        body = r.json()
        print(f"  body: {json.dumps(body, indent=2)}")
        assert_true(body.get("ok") is True, "legacy ok=true")
        assert_true(body.get("sheet", {}).get("attempted") is False,
                    "legacy sheet.attempted=false", body.get("sheet"))

    print("\n" + "=" * 70)
    print(f"RESULT: {PASS} passed, {FAIL} failed")
    if FAILURES:
        print("\nFAILURES:")
        for f in FAILURES:
            print(f"  - {f}")
    return FAIL == 0


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
