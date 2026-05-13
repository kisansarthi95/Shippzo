"""
Phase-21 Re-test: detect_repeat_customer fix verification.

Re-tests ONLY the repeat-customer detector after the field name fix
in /app/backend/server.py.
"""
import os
import requests
import sys

BASE = "https://logistics-hub-740.preview.emergentagent.com/api"

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASSWORD = "Admin@12345"

passed = 0
failed = 0
issues = []

def chk(cond, name):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ✅ {name}")
    else:
        failed += 1
        issues.append(name)
        print(f"  ❌ {name}")

def main():
    # 1) Login
    print("\n[1] Login admin@test.com")
    r = requests.post(f"{BASE}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=30)
    chk(r.status_code == 200, f"login returns 200 (got {r.status_code})")
    if r.status_code != 200:
        print(r.text[:500])
        return
    token = r.json()["token"]
    H = {"Authorization": f"Bearer {token}"}

    # 2) Get a courier_id
    print("\n[2] Fetch a courier id")
    rc = requests.get(f"{BASE}/couriers", headers=H, timeout=30)
    chk(rc.status_code == 200, f"GET /couriers returns 200 (got {rc.status_code})")
    couriers = rc.json() if rc.status_code == 200 else []
    chk(len(couriers) > 0, "at least one courier exists")
    if not couriers:
        return
    courier_id = couriers[0]["id"]
    print(f"  -> using courier_id={courier_id} ({couriers[0].get('name')})")

    # Pre-clean: delete any existing pending order matching these phones (best effort)
    target_phones = {"9999000222", "7777666555"}
    try:
        rp = requests.get(f"{BASE}/orders/pending", headers=H, timeout=30)
        if rp.status_code == 200:
            data = rp.json()
            rows = data if isinstance(data, list) else data.get("items", [])
            for row in rows:
                cp = (row.get("customer_phone") or "").replace(" ", "").replace("+91", "").strip()
                cp_last10 = "".join(ch for ch in cp if ch.isdigit())[-10:]
                if cp_last10 in target_phones and row.get("source") in (None, "paste", "smart_paste"):
                    pid = row.get("id")
                    if pid:
                        requests.delete(f"{BASE}/orders/pending/{pid}", headers=H, timeout=20)
    except Exception as e:
        print(f"  (pre-clean skipped: {e})")

    created_pending_ids = []
    created_shipment_ids = []

    try:
        # 3) Create a fresh shipment with phone 9999000222
        print("\n[3] POST /api/shipments with phone 9999000222")
        import time as _t
        ship_payload = {
            "tracking_id": f"TEST{int(_t.time())}",
            "customer_name": "Repeat Customer One",
            "customer_phone": "9999000222",
            "address_line1": "12 Some Lane",
            "city": "Ahmedabad",
            "state": "Gujarat",
            "pincode": "380015",
            "amount": 599,
            "payment_mode": "COD",
            "courier_id": courier_id,
        }
        rs = requests.post(f"{BASE}/shipments", json=ship_payload, headers=H, timeout=30)
        chk(rs.status_code == 200, f"POST /shipments returns 200 (got {rs.status_code})")
        if rs.status_code != 200:
            print(rs.text[:500])
            return
        ship_doc = rs.json()
        ship_id = ship_doc.get("id")
        created_shipment_ids.append(ship_id)
        chk(ship_doc.get("customer_phone") == "9999000222", "shipment.customer_phone == 9999000222")
        print(f"  -> shipment_id={ship_id}, tracking={ship_doc.get('tracking_id')}")

        # 4) POST /api/smart-paste with bare phone 9999000222
        print("\n[4] POST /api/smart-paste with bare phone '9999000222'")
        block_a = "Test User, 9999000222, 380015, Ahmedabad, COD 599, Test Item"
        rp = requests.post(f"{BASE}/smart-paste", json={"text": block_a}, headers=H, timeout=30)
        chk(rp.status_code == 200, f"smart-paste returns 200 (got {rp.status_code})")
        if rp.status_code == 200:
            po_a = rp.json()
            print(f"  -> pending_id={po_a.get('id')}, customer_phone={po_a.get('customer_phone')}, is_repeat_customer={po_a.get('is_repeat_customer')}")
            created_pending_ids.append(po_a.get("id"))
            chk(po_a.get("customer_phone") in ("9999000222",), f"parsed customer_phone matches (got {po_a.get('customer_phone')})")
            chk(po_a.get("is_repeat_customer") is True, "is_repeat_customer == True for bare phone match")
        else:
            print(rp.text[:500])

        # 5) Phone normalisation: +91 99990 00222
        print("\n[5] POST /api/smart-paste with normalised phone '+91 99990 00222'")
        block_b = "Test User Two, +91 99990 00222, 380015, Ahmedabad, COD 599, Test Item Two"
        rp2 = requests.post(f"{BASE}/smart-paste", json={"text": block_b}, headers=H, timeout=30)
        chk(rp2.status_code == 200, f"smart-paste returns 200 (got {rp2.status_code})")
        if rp2.status_code == 200:
            po_b = rp2.json()
            print(f"  -> pending_id={po_b.get('id')}, customer_phone={po_b.get('customer_phone')}, is_repeat_customer={po_b.get('is_repeat_customer')}")
            created_pending_ids.append(po_b.get("id"))
            chk(po_b.get("is_repeat_customer") is True, "is_repeat_customer == True for +91 99990 00222 (normalised)")
        else:
            print(rp2.text[:500])

        # 6) Fresh phone -> should be False
        print("\n[6] POST /api/smart-paste with fresh phone '7777666555' (no prior shipment)")
        block_c = "Fresh Lead, 7777666555, 380015, Ahmedabad, COD 599, Test Item Fresh"
        rp3 = requests.post(f"{BASE}/smart-paste", json={"text": block_c}, headers=H, timeout=30)
        chk(rp3.status_code == 200, f"smart-paste returns 200 (got {rp3.status_code})")
        if rp3.status_code == 200:
            po_c = rp3.json()
            print(f"  -> pending_id={po_c.get('id')}, customer_phone={po_c.get('customer_phone')}, is_repeat_customer={po_c.get('is_repeat_customer')}")
            created_pending_ids.append(po_c.get("id"))
            chk(po_c.get("is_repeat_customer") is False, "is_repeat_customer == False for fresh phone 7777666555")
        else:
            print(rp3.text[:500])

    finally:
        # Cleanup
        print("\n[CLEANUP]")
        for pid in created_pending_ids:
            if not pid:
                continue
            try:
                rd = requests.delete(f"{BASE}/orders/pending/{pid}", headers=H, timeout=20)
                print(f"  DELETE pending {pid} -> {rd.status_code}")
            except Exception as e:
                print(f"  DELETE pending {pid} failed: {e}")
        for sid in created_shipment_ids:
            if not sid:
                continue
            try:
                rd = requests.delete(f"{BASE}/shipments/{sid}", headers=H, timeout=20)
                print(f"  DELETE shipment {sid} -> {rd.status_code}")
            except Exception as e:
                print(f"  DELETE shipment {sid} failed: {e}")

    print(f"\n=== RESULT: {passed} passed, {failed} failed ===")
    if issues:
        print("\nFAILED:")
        for i in issues:
            print(f"  - {i}")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
