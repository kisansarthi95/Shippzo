"""
Phase-16 Contact Save — Re-test of 2 previously-failing cases for
POST /api/contacts/build-vcf

Tested against the public preview backend.
"""
import os
import sys
import json
import requests

BASE_URL = "https://logistics-hub-740.preview.emergentagent.com/api"

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASSWORD = "Admin@12345"


def log(msg):
    print(msg, flush=True)


def login(email, password):
    r = requests.post(f"{BASE_URL}/auth/login",
                      json={"email": email, "password": password},
                      timeout=30)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    data = r.json()
    return data["token"]


def auth_headers(token):
    return {"Authorization": f"Bearer {token}",
            "Content-Type": "application/json"}


def create_shipment(token, *, name, phone, items_list, tracking_id):
    payload = {
        "tracking_id": tracking_id,
        "customer_name": name,
        "customer_phone": phone,
        "address_line1": "20 Dev Atelier",
        "city": "Ahmedabad",
        "state": "Gujarat",
        "pincode": "380015",
        "payment_mode": "Prepaid",
        "amount": 750.0,
        "items": items_list,
        "weight": "1",
    }
    r = requests.post(f"{BASE_URL}/shipments",
                      json=payload, headers=auth_headers(token), timeout=60)
    if r.status_code != 200:
        log(f"  POST /shipments → {r.status_code} body={r.text[:400]}")
    assert r.status_code == 200, "create_shipment failed"
    return r.json()


def delete_shipment(token, ship_id):
    try:
        requests.delete(f"{BASE_URL}/shipments/{ship_id}",
                        headers=auth_headers(token), timeout=30)
    except Exception as e:
        log(f"  cleanup delete failed: {e}")


def main():
    failures = []
    log(f"BASE_URL = {BASE_URL}")

    log("\n[Setup] Logging in as admin…")
    token = login(ADMIN_EMAIL, ADMIN_PASSWORD)
    log("  ✓ admin token acquired")

    created_ids = []

    # ----------------------------------------------------------------
    # CASE 1 — Build VCF for 3 shipments with valid phones
    # ----------------------------------------------------------------
    log("\n[CASE 1] Build VCF for 3 shipments with phones")
    try:
        import time
        ts = int(time.time())
        ship_ids_with_phone = []
        # Use Indian-looking customer + 10-digit numbers
        cases = [
            ("Ramesh Patel",  "9812345601", ["Garlic"],          f"VCFTST-{ts}-A"),
            ("Sunita Sharma", "9812345602", ["Honey", "Ghee"],   f"VCFTST-{ts}-B"),
            ("Amit Mehta",    "9812345603", ["Turmeric"],        f"VCFTST-{ts}-C"),
        ]
        for nm, ph, itms, trk in cases:
            sh = create_shipment(token, name=nm, phone=ph,
                                 items_list=itms, tracking_id=trk)
            ship_ids_with_phone.append(sh["id"])
            created_ids.append(sh["id"])
            log(f"  ✓ created shipment id={sh['id'][:8]}… "
                f"items={sh.get('items')!r}")

        # POST /api/contacts/build-vcf
        r = requests.post(f"{BASE_URL}/contacts/build-vcf",
                          json={"shipment_ids": ship_ids_with_phone},
                          headers=auth_headers(token), timeout=60)
        log(f"  build-vcf status={r.status_code}")
        if r.status_code != 200:
            log(f"  body={r.text[:600]}")
            failures.append(f"CASE1 expected 200, got {r.status_code}")
        else:
            body = r.json()
            vcf = body.get("vcf", "")
            count = body.get("count")
            n = len(ship_ids_with_phone)
            begin_count = vcf.count("BEGIN:VCARD")
            end_count = vcf.count("END:VCARD")
            log(f"  count={count} BEGIN:VCARD x {begin_count} "
                f"END:VCARD x {end_count} skipped={body.get('skipped')}")
            log(f"  vcf head:\n{vcf[:300]}")
            if begin_count != n:
                failures.append(
                    f"CASE1 BEGIN:VCARD expected {n} got {begin_count}")
            if end_count != n:
                failures.append(
                    f"CASE1 END:VCARD expected {n} got {end_count}")
            if count != n:
                failures.append(f"CASE1 count expected {n} got {count}")
            if not failures:
                log("  ✓ CASE 1 PASS")
    except AssertionError as ae:
        failures.append(f"CASE1 assertion: {ae}")
    except Exception as e:
        failures.append(f"CASE1 unexpected: {e}")

    # ----------------------------------------------------------------
    # CASE 2 — All shipments lack customer_phone → expect 400 (not 500)
    # ----------------------------------------------------------------
    log("\n[CASE 2] Build VCF where all shipments lack customer_phone")
    try:
        import time
        ts = int(time.time())
        nophone_ids = []
        for i in range(2):
            payload = {
                "tracking_id": f"VCFTST-NOPH-{ts}-{i}",
                "customer_name": f"Nophone Customer {i}",
                "customer_phone": "",      # explicitly empty
                "address_line1": "X",
                "city": "Mumbai",
                "state": "Maharashtra",
                "pincode": "400001",
                "items": ["Garlic"],
                "weight": "1",
            }
            r = requests.post(f"{BASE_URL}/shipments", json=payload,
                              headers=auth_headers(token), timeout=60)
            if r.status_code != 200:
                log(f"  POST /shipments (no phone) → {r.status_code} {r.text[:400]}")
            assert r.status_code == 200
            sh = r.json()
            nophone_ids.append(sh["id"])
            created_ids.append(sh["id"])
            log(f"  ✓ created no-phone shipment id={sh['id'][:8]}… "
                f"phone={sh.get('customer_phone')!r}")

        r = requests.post(f"{BASE_URL}/contacts/build-vcf",
                          json={"shipment_ids": nophone_ids},
                          headers=auth_headers(token), timeout=60)
        log(f"  build-vcf status={r.status_code} body={r.text[:300]}")
        if r.status_code == 500:
            failures.append(
                "CASE2 returned 500 (regression — bug NOT fixed)")
        elif r.status_code != 400:
            failures.append(
                f"CASE2 expected 400, got {r.status_code}")
        else:
            log("  ✓ CASE 2 PASS (400 returned)")
    except AssertionError as ae:
        failures.append(f"CASE2 assertion: {ae}")
    except Exception as e:
        failures.append(f"CASE2 unexpected: {e}")

    # ----------------------------------------------------------------
    # Cleanup
    # ----------------------------------------------------------------
    log("\n[Cleanup] Deleting test shipments…")
    for sid in created_ids:
        delete_shipment(token, sid)
    log(f"  cleaned {len(created_ids)} shipments")

    # ----------------------------------------------------------------
    # Summary
    # ----------------------------------------------------------------
    log("\n" + "=" * 60)
    if failures:
        log(f"FAILED ({len(failures)}):")
        for f in failures:
            log(f"  ✗ {f}")
        sys.exit(1)
    else:
        log("ALL 2 CASES PASS")
        sys.exit(0)


if __name__ == "__main__":
    main()
