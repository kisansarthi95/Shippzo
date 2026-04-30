"""Regression check — Custom Field values wiring into shipment pipelines."""
import re
import subprocess
import sys

import requests

BASE = "https://logistics-hub-740.preview.emergentagent.com/api"
USER2 = {"email": "user2@test.com", "password": "User@12345"}
ADMIN = {"email": "admin@test.com", "password": "Admin@12345"}

results = []


def record(ok: bool, label: str, detail: str = ""):
    results.append((ok, label, detail))
    marker = "PASS" if ok else "FAIL"
    print(f"[{marker}] {label}" + (f"  ({detail})" if detail else ""))


def login(creds):
    r = requests.post(f"{BASE}/auth/login", json=creds, timeout=20)
    r.raise_for_status()
    return r.json()["token"], r.json()["id"]


def h(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def test_module_health():
    r = subprocess.run(
        [sys.executable, "-c", "import server; import sheet_writer"],
        cwd="/app/backend",
        capture_output=True,
        text=True,
        timeout=30,
    )
    ok = r.returncode == 0
    detail = (r.stderr or r.stdout).strip().splitlines()
    record(ok, "A1: import server + sheet_writer (exit 0)",
           f"rc={r.returncode} err={detail[-3:] if detail else []}")


def test_backend_up():
    try:
        t, _ = login(USER2)
        r = requests.get(f"{BASE}/auth/me", headers={"Authorization": f"Bearer {t}"}, timeout=20)
        record(r.status_code == 200, "A2: backend up /api/auth/me 200", f"code={r.status_code}")
    except Exception as e:
        record(False, "A2: backend up", str(e))


def test_shipments_no_custom(token):
    payload = {
        "tracking_id": "REG-TEST-A3-001",
        "customer_name": "Neha Gupta",
        "customer_phone": "9123456701",
        "address_line1": "27, Shanti Niwas, Navrangpura",
        "city": "Ahmedabad",
        "state": "Gujarat",
        "pincode": "380009",
        "weight": "0.5",
        "courier_id": "",
        "courier_name": "",
        "payment_mode": "COD",
        "amount": 499.0,
    }
    r = requests.post(f"{BASE}/shipments", headers=h(token), json=payload, timeout=30)
    ok = r.status_code == 200
    sid = r.json().get("id") if ok else None
    record(ok, "A3: POST /shipments (no custom_values) 200",
           f"code={r.status_code} body={r.text[:200] if not ok else ''}")
    if sid:
        rd = requests.delete(f"{BASE}/shipments/{sid}", headers=h(token), timeout=30)
        record(rd.status_code == 200, "A3: cleanup DELETE /shipments/{id}", f"code={rd.status_code}")


def test_shipments_spurious_custom(token):
    payload = {
        "tracking_id": "REG-TEST-A4-001",
        "customer_name": "Ravi Patel",
        "customer_phone": "9887776655",
        "address_line1": "12, Silver Oak Apt, Vastrapur",
        "city": "Ahmedabad",
        "state": "Gujarat",
        "pincode": "380015",
        "weight": "0.7",
        "courier_id": "",
        "courier_name": "",
        "payment_mode": "Prepaid",
        "amount": 899.0,
        "custom_values": {"11111111-2222-3333-4444-555555555555": "X"},
    }
    r = requests.post(f"{BASE}/shipments", headers=h(token), json=payload, timeout=30)
    ok = r.status_code == 200
    sid = r.json().get("id") if ok else None
    record(ok, "A4: POST /shipments (spurious custom_values) 200 (no 500)",
           f"code={r.status_code} body={r.text[:250] if not ok else ''}")
    if sid:
        rd = requests.delete(f"{BASE}/shipments/{sid}", headers=h(token), timeout=30)
        record(rd.status_code == 200, "A4: cleanup DELETE /shipments/{id}", f"code={rd.status_code}")


def test_smart_paste_and_ship(token):
    paste_text = (
        "CUSTOMER_NAME: Priya Sharma\n"
        "PHONE: 9090909091\n"
        "ADDRESS_1: 9, Palm Residency, Satellite Road\n"
        "CITY: Ahmedabad\n"
        "STATE: Gujarat\n"
        "PINCODE: 380015\n"
        "AMOUNT: 450\n"
        "PAYMENT_MODE: COD\n"
        "WEIGHT: 0.4\n"
    )
    r = requests.post(f"{BASE}/smart-paste", headers=h(token),
                      json={"text": paste_text, "skip_llm": True}, timeout=40)
    if r.status_code != 200:
        record(False, "A5a: POST /smart-paste 200",
               f"code={r.status_code} body={r.text[:200]}")
        return
    record(True, "A5a: POST /smart-paste 200", "")
    pending = r.json()
    pid = pending.get("id")
    cr = requests.get(f"{BASE}/couriers", headers=h(token), timeout=20)
    couriers = cr.json() if cr.status_code == 200 else []
    if not couriers:
        record(False, "A5b: courier lookup for ship", "no courier available")
        requests.delete(f"{BASE}/orders/pending/{pid}", headers=h(token), timeout=20)
        return
    courier_id = couriers[0]["id"]
    rs = requests.post(f"{BASE}/orders/pending/{pid}/ship", headers=h(token),
                       json={"courier_id": courier_id, "overrides": {}}, timeout=40)
    ok = rs.status_code == 200
    sid = rs.json().get("id") if ok else None
    record(ok, "A5b: POST /orders/pending/{id}/ship 200 (no 500, no ghost)",
           f"code={rs.status_code} body={rs.text[:250] if not ok else ''}")
    if sid:
        rd = requests.delete(f"{BASE}/shipments/{sid}", headers=h(token), timeout=30)
        record(rd.status_code == 200, "A5c: cleanup DELETE /shipments/{id}",
               f"code={rd.status_code}")


def test_me_custom_fields(token):
    r = requests.get(f"{BASE}/me/custom-fields", headers=h(token), timeout=20)
    ok = r.status_code == 200
    body_ok = False
    if ok:
        try:
            b = r.json()
            body_ok = isinstance(b, (list, dict))
        except Exception:
            body_ok = False
    record(ok and body_ok, "A6: GET /me/custom-fields 200 + valid shape",
           f"code={r.status_code} body={r.text[:200] if not ok else ''}")


def test_sync_headers(token):
    r = requests.post(f"{BASE}/sheets/sync-headers", headers=h(token),
                      json={"dry_run": True}, timeout=20)
    ok = r.status_code in (200, 400, 422)
    record(ok, "A7: POST /sheets/sync-headers non-500 (200/400/422)",
           f"code={r.status_code} body={r.text[:200]}")


def test_admin_limits(admin_token):
    r = requests.get(f"{BASE}/admin/custom-field-limits", headers=h(admin_token), timeout=20)
    ok = r.status_code == 200
    record(ok, "A8: GET /admin/custom-field-limits (admin) 200",
           f"code={r.status_code} body={r.text[:200] if not ok else ''}")


def test_wiring_contract():
    with open("/app/backend/server.py") as f:
        src = f.read()
    insert_positions = [m.start() for m in re.finditer(r"db\.shipments\.insert_one\(", src)]
    record(len(insert_positions) >= 2,
           "A9a: Found >=2 db.shipments.insert_one(...) calls",
           f"count={len(insert_positions)}")
    awaited_after = 0
    for pos in insert_positions:
        window = src[pos:pos + 1500]
        if "await _write_custom_values_to_user_sheet_bg" in window:
            awaited_after += 1
    record(awaited_after == len(insert_positions),
           "A9b: Every insert_one is followed by await _write_custom_values_to_user_sheet_bg",
           f"awaited/total = {awaited_after}/{len(insert_positions)}")
    helper_def = re.search(
        r"async def _write_custom_values_to_user_sheet_bg\(\s*current_user[^)]*custom_values[^)]*\)",
        src, re.DOTALL)
    record(bool(helper_def),
           "A9c: Helper signature (current_user, custom_values)")
    helper_body_match = re.search(
        r"async def _write_custom_values_to_user_sheet_bg\b.*?(?=\nasync def |\n@api_router|\n@app\.|\Z)",
        src, re.DOTALL)
    if helper_body_match:
        body = helper_body_match.group(0)
        has_try = "try:" in body
        has_except = re.search(r"except\s+Exception", body) is not None
        has_logger = "logger.warning" in body or "logger.exception" in body
        record(has_try and has_except and has_logger,
               "A9d: Helper try/except + logger.warning",
               f"try={has_try} except_Exception={has_except} logger={has_logger}")
    else:
        record(False, "A9d: helper body not extractable", "")


def main():
    test_module_health()
    test_backend_up()
    u2_token, _ = login(USER2)
    adm_token, _ = login(ADMIN)
    test_shipments_no_custom(u2_token)
    test_shipments_spurious_custom(u2_token)
    test_smart_paste_and_ship(u2_token)
    test_me_custom_fields(u2_token)
    test_sync_headers(u2_token)
    test_admin_limits(adm_token)
    test_wiring_contract()
    passed = sum(1 for ok, *_ in results if ok)
    total = len(results)
    print(f"\n==== {passed}/{total} assertions passed ====")
    failed = [(label, det) for ok, label, det in results if not ok]
    if failed:
        print("\nFailures:")
        for label, det in failed:
            print(f"  - {label}: {det}")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
