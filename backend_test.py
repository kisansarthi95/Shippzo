"""
Phase-7e — New Shipment Auto-fill Backend Test
Tests the peek-master-id endpoint and POST /shipments behaviour with regards
to master_order_id and order_id auto-generation flags.
"""
import os
import re
import sys
import json
import requests

BASE = os.environ.get("BASE_URL", "http://localhost:8001/api")
EMAIL = "admin@test.com"
PASSWORD = "Admin@12345"

results = []


def log(name, ok, msg=""):
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f" — {msg}" if msg else ""))
    results.append((name, ok, msg))


def login():
    r = requests.post(f"{BASE}/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=15)
    r.raise_for_status()
    return r.json()["token"]


def main():
    token = login()
    H = {"Authorization": f"Bearer {token}"}

    created_shipment_ids = []

    # ── Test 1 ─────────────────────────────────────────────────────────
    print("\n=== Test 1: peek-master-id with auto-gen ON ===")
    r = requests.put(f"{BASE}/settings", headers=H, json={
        "order_id_auto_generate": True,
        "order_id_autofill_in_new_shipment": True,
    }, timeout=10)
    log("T1.1 PUT settings auto-gen=ON, autofill=ON returns 200", r.status_code == 200, f"status={r.status_code} body={r.text[:200]}")

    r = requests.get(f"{BASE}/orders/peek-master-id", headers=H, timeout=10)
    log("T1.2 GET peek-master-id returns 200", r.status_code == 200, f"status={r.status_code}")
    body = {}
    if r.ok:
        body = r.json()
        moid = body.get("master_order_id", "")
        log("T1.3 master_order_id matches ^\\d{6}\\d{5,}$", bool(re.match(r"^\d{6}\d{5,}$", moid)), f"moid={moid!r}")
        log("T1.4 auto_generate == True", body.get("auto_generate") is True, f"got={body.get('auto_generate')!r}")
        log("T1.5 autofill_in_new_shipment == True", body.get("autofill_in_new_shipment") is True, f"got={body.get('autofill_in_new_shipment')!r}")
        first_peek = moid

        # Call peek again — must return SAME id (counter not incremented)
        r2 = requests.get(f"{BASE}/orders/peek-master-id", headers=H, timeout=10)
        body2 = r2.json() if r2.ok else {}
        log("T1.6 Second peek returns same master_order_id (counter not incremented)",
            r2.ok and body2.get("master_order_id") == first_peek,
            f"first={first_peek!r} second={body2.get('master_order_id')!r}")

    # ── Test 2 ─────────────────────────────────────────────────────────
    print("\n=== Test 2: peek with auto-gen OFF ===")
    r = requests.put(f"{BASE}/settings", headers=H, json={"order_id_auto_generate": False}, timeout=10)
    log("T2.1 PUT auto-gen=OFF returns 200", r.status_code == 200, f"status={r.status_code}")

    r = requests.get(f"{BASE}/orders/peek-master-id", headers=H, timeout=10)
    log("T2.2 GET peek returns 200", r.status_code == 200)
    if r.ok:
        body = r.json()
        log("T2.3 master_order_id == ''", body.get("master_order_id") == "", f"got={body.get('master_order_id')!r}")
        log("T2.4 auto_generate == False", body.get("auto_generate") is False, f"got={body.get('auto_generate')!r}")

    # ── Test 3 ─────────────────────────────────────────────────────────
    print("\n=== Test 3: POST /shipments with master_order_id provided (auto-gen ON) ===")
    r = requests.put(f"{BASE}/settings", headers=H, json={"order_id_auto_generate": True}, timeout=10)
    log("T3.1 PUT auto-gen=ON returns 200", r.status_code == 200)

    r = requests.get(f"{BASE}/orders/peek-master-id", headers=H, timeout=10)
    preview_id = r.json()["master_order_id"] if r.ok else ""
    log("T3.2 Got previewId from peek", bool(preview_id), f"previewId={preview_id!r}")

    # Get a courier
    r = requests.get(f"{BASE}/couriers", headers=H, timeout=10)
    couriers = r.json() if r.ok else []
    if not couriers:
        rc = requests.post(f"{BASE}/couriers", headers=H, json={"name": "Test Courier", "code": "TC"}, timeout=10)
        log("T3.3 POST courier (none existed)", rc.status_code in (200, 201), f"status={rc.status_code}")
        courier = rc.json()
    else:
        courier = couriers[0]
        log("T3.3 GET couriers returned existing courier", True, f"using={courier.get('name')!r}")

    courier_id = courier["id"]
    courier_name = courier.get("name", "")

    body3 = {
        "tracking_id": "TST-001",
        "courier_id": courier_id,
        "courier_name": courier_name,
        "master_order_id": preview_id,
        "order_id": "ABC-PHASE7E",
        "customer_name": "Phase7e Test",
        "customer_phone": "9777777777",
        "address_line1": "Test",
        "address_line2": "",
        "city": "Ahmedabad",
        "state": "Gujarat",
        "pincode": "380001",
        "items": [],
        "amount": 100,
        "weight": "500",
        "payment_mode": "COD",
    }
    r = requests.post(f"{BASE}/shipments", headers=H, json=body3, timeout=15)
    log("T3.4 POST /shipments returns 200/201", r.status_code in (200, 201), f"status={r.status_code} body={r.text[:300]}")
    if r.ok:
        ship = r.json()
        created_shipment_ids.append(ship["id"])
        log("T3.5 shipment.master_order_id == previewId (frontend-supplied honoured)",
            ship.get("master_order_id") == preview_id,
            f"got={ship.get('master_order_id')!r} expected={preview_id!r}")
        log("T3.6 shipment.order_id == 'ABC-PHASE7E'",
            ship.get("order_id") == "ABC-PHASE7E",
            f"got={ship.get('order_id')!r}")

    # ── Test 4 ─────────────────────────────────────────────────────────
    print("\n=== Test 4: POST /shipments WITHOUT master_order_id (auto-gen ON) ===")
    body4 = dict(body3)
    body4.pop("master_order_id", None)
    body4["tracking_id"] = "TST-002"
    body4["customer_phone"] = "9888888888"
    r = requests.post(f"{BASE}/shipments", headers=H, json=body4, timeout=15)
    log("T4.1 POST /shipments returns 200/201", r.status_code in (200, 201), f"status={r.status_code} body={r.text[:300]}")
    if r.ok:
        ship = r.json()
        created_shipment_ids.append(ship["id"])
        moid = ship.get("master_order_id", "")
        log("T4.2 master_order_id matches pattern", bool(re.match(r"^\d{6}\d{5,}$", moid)), f"got={moid!r}")
        log("T4.3 master_order_id != previewId (server allocated fresh)",
            moid != preview_id, f"got={moid!r} previewId={preview_id!r}")
        log("T4.4 order_id stays 'ABC-PHASE7E'",
            ship.get("order_id") == "ABC-PHASE7E",
            f"got={ship.get('order_id')!r}")

    # ── Test 5 ─────────────────────────────────────────────────────────
    print("\n=== Test 5: POST /shipments WITHOUT order_id (auto-gen OFF) ===")
    r = requests.put(f"{BASE}/settings", headers=H, json={"order_id_auto_generate": False}, timeout=10)
    log("T5.1 PUT auto-gen=OFF returns 200", r.status_code == 200)

    body5 = dict(body3)
    body5.pop("master_order_id", None)
    body5.pop("order_id", None)
    body5["tracking_id"] = "TST-003"
    body5["customer_phone"] = "9999999999"
    r = requests.post(f"{BASE}/shipments", headers=H, json=body5, timeout=15)
    log("T5.2 POST returns 422", r.status_code == 422, f"status={r.status_code} body={r.text[:300]}")
    if r.status_code == 422:
        try:
            detail = r.json().get("detail", "")
        except Exception:
            detail = r.text
        if isinstance(detail, list):
            detail_str = json.dumps(detail)
        else:
            detail_str = str(detail)
        log("T5.3 422 detail mentions 'Order ID is required when Auto-Generate is OFF'",
            "Order ID is required when Auto-Generate is OFF" in detail_str,
            f"detail={detail_str[:300]}")
    else:
        # Capture for cleanup if it slipped through
        try:
            ship = r.json()
            if "id" in ship:
                created_shipment_ids.append(ship["id"])
        except Exception:
            pass

    # ── Cleanup ────────────────────────────────────────────────────────
    print("\n=== Cleanup ===")
    requests.put(f"{BASE}/settings", headers=H, json={
        "order_id_auto_generate": True,
        "order_id_autofill_in_new_shipment": True,
    }, timeout=10)
    print(f"  Reset settings to auto-gen=ON, autofill=ON")

    for sid in created_shipment_ids:
        try:
            rd = requests.delete(f"{BASE}/shipments/{sid}", headers=H, timeout=15)
            print(f"  DELETE /shipments/{sid} → {rd.status_code}")
        except Exception as e:
            print(f"  DELETE /shipments/{sid} failed: {e}")

    # ── Summary ────────────────────────────────────────────────────────
    passed = sum(1 for _, ok, _ in results if ok)
    failed = [r for r in results if not r[1]]
    print(f"\n===== SUMMARY: {passed}/{len(results)} PASSED =====")
    for name, ok, msg in failed:
        print(f"  FAIL: {name} — {msg}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
