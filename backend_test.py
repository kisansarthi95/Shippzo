"""
Phase-B Master Sheet extension backend tests.
Run: python /app/backend_test.py
"""
import os
import re
import sys
import json
import time
import requests

BASE = "https://logistics-hub-740.preview.emergentagent.com/api"

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASS = "Admin@12345"

results = []

def check(label, cond, info=""):
    status = "PASS" if cond else "FAIL"
    results.append((status, label, info))
    print(f"[{status}] {label}" + (f"  --  {info}" if info else ""))


def login():
    r = requests.post(f"{BASE}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASS}, timeout=30)
    r.raise_for_status()
    return r.json()["token"]


def main():
    token = login()
    H = {"Authorization": f"Bearer {token}"}

    # ----- Test 1: Smart Paste with extended schema -----
    print("\n=== Test 1: Smart Paste extended Master Sheet ===")
    s = requests.put(f"{BASE}/settings", headers=H, json={"order_id_auto_generate": True}, timeout=30)
    check("PUT /settings (auto_generate=true) returns 200", s.status_code == 200, f"status={s.status_code} body={s.text[:200]}")

    paste_text = (
        "NAME: Phase-B Test\n"
        "PHONE: 9123412345\n"
        "ALT_PHONE: 9999912345\n"
        "ADDRESS_1: Test addr line\n"
        "CITY: Ahmedabad\n"
        "STATE: Gujarat\n"
        "PINCODE: 380001\n"
        "AMOUNT: 500\n"
        "TOKEN: 50\n"
        "PAYMENT: COD\n"
        "WEIGHT: 750\n"
        "ORDER_ID: PHB-001"
    )
    sp = requests.post(f"{BASE}/smart-paste", headers=H, json={"text": paste_text, "skip_llm": True}, timeout=60)
    check("POST /smart-paste returns 200", sp.status_code == 200, f"status={sp.status_code} body={sp.text[:600]}")
    sp_json = sp.json() if sp.status_code == 200 else {}
    print("smart-paste response keys:", list(sp_json.keys()))
    if sp.status_code == 200:
        moid = sp_json.get("master_order_id", "")
        oid = sp_json.get("order_id", "")
        alt = sp_json.get("customer_alt_phone", "")
        tok = sp_json.get("token_amount")
        wt = sp_json.get("weight", "")
        sheet_row = sp_json.get("sheet_row_num")
        check("master_order_id matches ^\\d{6}\\d{5,}$", bool(re.match(r"^\d{6}\d{5,}$", moid or "")), f"moid={moid!r}")
        check("order_id == 'PHB-001'", oid == "PHB-001", f"order_id={oid!r}")
        check("customer_alt_phone == '9999912345'", alt == "9999912345", f"alt_phone={alt!r}")
        check("token_amount == 50", float(tok or 0) == 50.0, f"token_amount={tok!r}")
        check("weight == '750'", str(wt) == "750", f"weight={wt!r}")
        check("sheet_row_num is positive int (sheet append worked, no 502)", isinstance(sheet_row, int) and sheet_row > 1, f"sheet_row_num={sheet_row!r}")
        # Save for cleanup
        sp_json["_id"] = sp_json.get("id")
    else:
        sp_json = {}

    # ----- Verify backend logs show 'Sheet append OK' -----
    print("\n=== Test 1b: Verify backend logs ===")
    try:
        with open("/var/log/supervisor/backend.err.log", "r") as f:
            log_tail = f.read()[-12000:]
    except Exception as e:
        log_tail = ""
        print(f"Could not read backend.err.log: {e}")
    check("backend logs contain 'Sheet append OK'", "Sheet append OK" in log_tail, "")
    if "User-sheet" in log_tail:
        # Just informational
        for line in log_tail.splitlines()[-50:]:
            if "User-sheet" in line:
                print("  log:", line[-200:])

    # ----- Test 2: POST /shipments extended payload -----
    print("\n=== Test 2: POST /shipments with extended payload ===")
    cr = requests.get(f"{BASE}/couriers", headers=H, timeout=30)
    check("GET /couriers returns 200", cr.status_code == 200, f"status={cr.status_code}")
    couriers = cr.json() if cr.status_code == 200 else []
    if not couriers:
        nc = requests.post(f"{BASE}/couriers", headers=H, json={"name": "PhaseB Courier", "code": "PB"}, timeout=30)
        check("POST /couriers fallback create 200", nc.status_code == 200, nc.text[:200])
        courier = nc.json()
    else:
        courier = couriers[0]
    courier_id = courier.get("id")
    courier_name = courier.get("name")
    print(f"Using courier id={courier_id} name={courier_name}")

    ship_payload = {
        "tracking_id": f"PB-2604-{int(time.time())%100000}",
        "courier_id": courier_id,
        "courier_name": courier_name,
        "order_id": f"PB-MAN-{int(time.time())%100000}",
        "customer_name": "Phase-B Manual",
        "customer_phone": "9000010001",
        "customer_alt_phone": "9000020001",
        "address_line1": "Manual addr",
        "address_line2": "",
        "city": "Ahmedabad",
        "state": "Gujarat",
        "pincode": "380001",
        "items": [],
        "amount": 1500,
        "token_amount": 200,
        "weight": "1200",
        "payment_mode": "COD",
    }
    ps = requests.post(f"{BASE}/shipments", headers=H, json=ship_payload, timeout=60)
    check("POST /shipments returns 200/201", ps.status_code in (200, 201), f"status={ps.status_code} body={ps.text[:400]}")
    ps_json = ps.json() if ps.status_code in (200, 201) else {}
    print("shipment response keys:", list(ps_json.keys())[:30])
    if ps_json:
        moid2 = ps_json.get("master_order_id", "")
        alt2 = ps_json.get("customer_alt_phone", "")
        tok2 = ps_json.get("token_amount")
        wt2 = ps_json.get("weight", "")
        check("Shipment master_order_id matches ^\\d{6}\\d{5,}$", bool(re.match(r"^\d{6}\d{5,}$", moid2 or "")), f"moid={moid2!r}")
        check("Shipment customer_alt_phone == '9000020001'", alt2 == "9000020001", f"alt={alt2!r}")
        check("Shipment token_amount == 200", float(tok2 or 0) == 200.0, f"token_amount={tok2!r}")
        check("Shipment weight == '1200'", str(wt2) == "1200", f"weight={wt2!r}")

    # ----- Test 3: sheets/probe -----
    print("\n=== Test 3: GET /sheets/probe ===")
    sp_probe = requests.get(f"{BASE}/sheets/probe", headers=H, timeout=30)
    if sp_probe.status_code == 200:
        check("GET /sheets/probe returns 200", True, "")
        body = sp_probe.json()
        if body.get("ok"):
            check("sheets/probe ok=true", True, f"tab={body.get('tab')!r}")
        else:
            check("sheets/probe ok=true", False, f"body={body}")
    else:
        # If MASTER_SHEET_ID is set but probe fails, that's a fail. Else SKIP.
        master_id_set = bool(os.environ.get("MASTER_SHEET_ID"))
        if master_id_set:
            check("GET /sheets/probe returns 200 (MASTER_SHEET_ID configured)", False, f"status={sp_probe.status_code} body={sp_probe.text[:200]}")
        else:
            print(f"[SKIP] sheets/probe not configured (status={sp_probe.status_code})")

    # ----- Test 4: Master Sheet header backward compatibility -----
    print("\n=== Test 4: Master Sheet header backward compatibility ===")
    # If Test 1 returned 200 (not 502), this passes.
    test1_returned_200 = sp.status_code == 200 if sp_json else False
    check("Smart Paste returned 200 (no 502 from sheet_writer with extended schema)", test1_returned_200, f"sp.status_code={sp.status_code}")

    # ----- Cleanup: delete the test shipment + pending order -----
    print("\n=== Cleanup ===")
    if sp_json and sp_json.get("id"):
        # pending order delete
        try:
            d = requests.delete(f"{BASE}/orders/pending/{sp_json['id']}", headers=H, timeout=30)
            print(f"DELETE pending order -> {d.status_code} {d.text[:200]}")
        except Exception as e:
            print(f"pending delete failed: {e}")
    if ps_json and ps_json.get("id"):
        try:
            d2 = requests.delete(f"{BASE}/shipments/{ps_json['id']}", headers=H, timeout=30)
            print(f"DELETE shipment -> {d2.status_code} {d2.text[:200]}")
        except Exception as e:
            print(f"shipment delete failed: {e}")

    # ----- Summary -----
    print("\n=== SUMMARY ===")
    passed = sum(1 for r in results if r[0] == "PASS")
    failed = sum(1 for r in results if r[0] == "FAIL")
    for s, l, i in results:
        print(f" [{s}] {l}")
    print(f"\n{passed} pass, {failed} fail (total {len(results)})")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
