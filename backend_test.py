"""Phase 2B Backend Test — Shipment Create with Packing Variant snapshot fields.

Tests the NEW optional fields on Shipment/ShipmentCreate:
  variant_id, variant_name, package_type, category, rate_applied, rate_basis
and the /api/me/all-variants endpoint + ShipmentUpdate behavior.
"""

import os
import sys
import json
import uuid
import requests

BACKEND_URL = "https://logistics-hub-740.preview.emergentagent.com"
API = f"{BACKEND_URL}/api"

USER_EMAIL = "user2@test.com"
USER_PASSWORD = "User@12345"
ADMIN_EMAIL = "admin@test.com"
ADMIN_PASSWORD = "Admin@12345"

passed = []
failed = []


def p(ok, label, details=""):
    if ok:
        passed.append(label)
        print(f"  PASS  {label}")
    else:
        failed.append((label, details))
        print(f"  FAIL  {label}  | {details}")


def login(email, password):
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    return r.json()["token"]


def auth_hdr(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def main():
    print(f"=== Phase 2B Test against {API} ===")

    # Try user2 first; fall back to admin if user2 is over cap for couriers.
    token = login(USER_EMAIL, USER_PASSWORD)
    who = "user2"

    # Step 1: fetch couriers, create if none
    r = requests.get(f"{API}/couriers", headers=auth_hdr(token), timeout=15)
    p(r.status_code == 200, "1. GET /api/couriers returns 200", f"status={r.status_code} body={r.text[:200]}")
    couriers = r.json() if r.status_code == 200 else []
    print(f"  user2 has {len(couriers)} couriers")

    courier = None
    if couriers:
        courier = couriers[0]
    else:
        # Create one
        r2 = requests.post(f"{API}/couriers",
                           headers=auth_hdr(token),
                           json={"name": "TEST_COURIER_PV"},
                           timeout=15)
        if r2.status_code != 200:
            # Might be plan cap — switch to admin
            print(f"  user2 POST /couriers failed ({r2.status_code}: {r2.text[:120]}), switching to admin")
            token = login(ADMIN_EMAIL, ADMIN_PASSWORD)
            who = "admin"
            r = requests.get(f"{API}/couriers", headers=auth_hdr(token), timeout=15)
            couriers = r.json()
            if couriers:
                courier = couriers[0]
            else:
                r2 = requests.post(f"{API}/couriers",
                                   headers=auth_hdr(token),
                                   json={"name": "TEST_COURIER_PV"},
                                   timeout=15)
                p(r2.status_code == 200, "1a. POST /api/couriers (admin) 200", r2.text[:200])
                courier = r2.json()
        else:
            p(True, "1a. POST /api/couriers created new courier")
            courier = r2.json()

    courier_id = courier["id"]
    courier_name = courier["name"]
    print(f"  Using courier: {courier_name} ({courier_id}) as {who}")

    # Step 2: Create a packing variant
    variant_body = {
        "variant_name": f"ODC 320gm PV-{uuid.uuid4().hex[:4]}",
        "package_type": "Cover",
        "category": "Documents",
        "length_cm": 25,
        "width_cm": 18,
        "height_cm": 2,
        "weight_g": 320,
        "within_state_rate": 30,
        "outside_state_rate": 60,
    }
    r = requests.post(f"{API}/couriers/{courier_id}/variants",
                      headers=auth_hdr(token),
                      json=variant_body,
                      timeout=15)
    if r.status_code == 402:
        # plan cap — fallback: list existing variants and reuse/create on admin
        print(f"  POST variant hit plan cap: {r.text[:160]}")
        # try listing
        rl = requests.get(f"{API}/couriers/{courier_id}/variants", headers=auth_hdr(token), timeout=15)
        existing = (rl.json() or {}).get("variants") or []
        if existing:
            variant = existing[0]
            print(f"  Reusing existing variant on same plan: {variant['id']}")
            # But test rate values may not match; patch them via PUT to satisfy assertions
            upd = requests.put(f"{API}/couriers/{courier_id}/variants/{variant['id']}",
                               headers=auth_hdr(token),
                               json={"within_state_rate": 30, "outside_state_rate": 60,
                                     "package_type": "Cover", "category": "Documents"},
                               timeout=15)
            if upd.status_code == 200:
                variant = upd.json()
            p(True, "2. variant available (existing, plan-capped)")
        else:
            # escalate to admin
            print("  No existing variants — switching to admin to create one")
            token = login(ADMIN_EMAIL, ADMIN_PASSWORD)
            who = "admin"
            # Need an admin courier
            rc = requests.get(f"{API}/couriers", headers=auth_hdr(token), timeout=15)
            adm_couriers = rc.json() or []
            if not adm_couriers:
                rc2 = requests.post(f"{API}/couriers", headers=auth_hdr(token),
                                    json={"name": "TEST_COURIER_PV"}, timeout=15)
                courier = rc2.json()
            else:
                courier = adm_couriers[0]
            courier_id = courier["id"]
            courier_name = courier["name"]
            r = requests.post(f"{API}/couriers/{courier_id}/variants",
                              headers=auth_hdr(token),
                              json=variant_body, timeout=15)
            p(r.status_code == 200, "2. POST /api/couriers/{id}/variants (admin) 200",
              f"status={r.status_code} body={r.text[:200]}")
            variant = r.json()
    else:
        p(r.status_code == 200, "2. POST /api/couriers/{id}/variants 200",
          f"status={r.status_code} body={r.text[:200]}")
        if r.status_code != 200:
            print("Aborting — cannot create variant.")
            print_summary()
            sys.exit(1)
        variant = r.json()

    variant_id = variant["id"]
    print(f"  Created variant id={variant_id} name={variant['variant_name']}")

    # validate variant fields
    p(variant.get("package_type") == "Cover", "2a. variant.package_type = 'Cover'", str(variant.get("package_type")))
    p(variant.get("category") == "Documents", "2b. variant.category = 'Documents'", str(variant.get("category")))
    p(abs(float(variant.get("within_state_rate") or 0) - 30) < 0.01, "2c. variant.within_state_rate = 30")
    p(abs(float(variant.get("outside_state_rate") or 0) - 60) < 0.01, "2d. variant.outside_state_rate = 60")

    # Step 3: Create shipment WITH variant fields
    tracking_id = f"TST-PV-{uuid.uuid4().hex[:8].upper()}"
    ship_body_with = {
        "tracking_id": tracking_id,
        "courier_id": courier_id,
        "courier_name": courier_name,
        "order_id": f"ORD-PV-{uuid.uuid4().hex[:6]}",
        "customer_name": "Test Customer",
        "customer_phone": "9876543210",
        "address_line1": "Test address",
        "city": "Ahmedabad",
        "state": "Gujarat",
        "pincode": "380001",
        "payment_mode": "COD",
        "amount": 60,
        "variant_id": variant_id,
        "variant_name": variant["variant_name"],
        "package_type": "Cover",
        "category": "Documents",
        "rate_applied": 60,
        "rate_basis": "outside_state",
    }
    r = requests.post(f"{API}/shipments", headers=auth_hdr(token), json=ship_body_with, timeout=30)
    p(r.status_code == 200, "3. POST /api/shipments WITH variant fields 200",
      f"status={r.status_code} body={r.text[:300]}")
    if r.status_code != 200:
        print_summary()
        sys.exit(1)
    ship_with = r.json()
    print(f"  Created shipment id={ship_with['id']} tracking={ship_with['tracking_id']}")

    # Verify returned shipment has the snapshot fields
    p(ship_with.get("variant_id") == variant_id, "3a. response.variant_id matches",
      f"got={ship_with.get('variant_id')!r} expected={variant_id!r}")
    p(ship_with.get("variant_name") == variant["variant_name"], "3b. response.variant_name matches",
      f"got={ship_with.get('variant_name')!r}")
    p(ship_with.get("package_type") == "Cover", "3c. response.package_type == 'Cover'",
      f"got={ship_with.get('package_type')!r}")
    p(ship_with.get("category") == "Documents", "3d. response.category == 'Documents'",
      f"got={ship_with.get('category')!r}")
    p(abs(float(ship_with.get("rate_applied") or 0) - 60) < 0.01,
      "3e. response.rate_applied == 60",
      f"got={ship_with.get('rate_applied')!r}")
    p(ship_with.get("rate_basis") == "outside_state", "3f. response.rate_basis == 'outside_state'",
      f"got={ship_with.get('rate_basis')!r}")

    # Step 4: Read back via GET /api/shipments
    r = requests.get(f"{API}/shipments", headers=auth_hdr(token), timeout=30)
    p(r.status_code == 200, "4. GET /api/shipments 200", f"status={r.status_code}")
    shipments = r.json() if r.status_code == 200 else []
    found = next((s for s in shipments if s.get("id") == ship_with["id"]), None)
    p(found is not None, "4a. Created shipment found in list")
    if found:
        p(found.get("variant_id") == variant_id, "4b. list.variant_id matches",
          f"got={found.get('variant_id')!r}")
        p(found.get("variant_name") == variant["variant_name"], "4c. list.variant_name matches")
        p(found.get("package_type") == "Cover", "4d. list.package_type == 'Cover'")
        p(found.get("category") == "Documents", "4e. list.category == 'Documents'")
        p(abs(float(found.get("rate_applied") or 0) - 60) < 0.01, "4f. list.rate_applied == 60")
        p(found.get("rate_basis") == "outside_state", "4g. list.rate_basis == 'outside_state'")

    # Step 5: Create shipment WITHOUT variant fields
    tracking_id2 = f"TST-NV-{uuid.uuid4().hex[:8].upper()}"
    ship_body_without = {
        "tracking_id": tracking_id2,
        "courier_id": courier_id,
        "courier_name": courier_name,
        "order_id": f"ORD-NV-{uuid.uuid4().hex[:6]}",
        "customer_name": "Test NoVariant",
        "customer_phone": "9876543211",
        "address_line1": "Test address 2",
        "city": "Ahmedabad",
        "state": "Gujarat",
        "pincode": "380001",
        "payment_mode": "Prepaid",
        "amount": 100,
    }
    r = requests.post(f"{API}/shipments", headers=auth_hdr(token), json=ship_body_without, timeout=30)
    p(r.status_code == 200, "5. POST /api/shipments WITHOUT variant fields 200",
      f"status={r.status_code} body={r.text[:300]}")
    if r.status_code == 200:
        ship_without = r.json()
        p(ship_without.get("variant_id") == "", "5a. default variant_id == ''",
          f"got={ship_without.get('variant_id')!r}")
        p(ship_without.get("variant_name") == "", "5b. default variant_name == ''",
          f"got={ship_without.get('variant_name')!r}")
        p(float(ship_without.get("rate_applied") or 0) == 0.0, "5c. default rate_applied == 0",
          f"got={ship_without.get('rate_applied')!r}")
        p(ship_without.get("rate_basis") == "", "5d. default rate_basis == ''",
          f"got={ship_without.get('rate_basis')!r}")
    else:
        ship_without = None

    # Step 6: Update shipment with rate_applied/rate_basis
    # ShipmentUpdate does NOT list these fields — report accordingly.
    upd_body = {"rate_applied": 45, "rate_basis": "within_state"}
    r = requests.put(f"{API}/shipments/{ship_with['id']}", headers=auth_hdr(token),
                     json=upd_body, timeout=15)
    print(f"  6. PUT /api/shipments/{{id}} with rate_applied/rate_basis → status={r.status_code}")
    if r.status_code == 400 and "No fields to update" in r.text:
        # Model silently drops unknown fields; Pydantic extras == ignore.
        p(False, "6. PUT update accepts rate_applied/rate_basis",
          "ShipmentUpdate does NOT list variant/rate fields — server responded 'No fields to update' "
          "(fields silently ignored). Report: main agent should add rate_applied/rate_basis/variant_* "
          "to ShipmentUpdate model if post-save edits are expected.")
    elif r.status_code == 200:
        body = r.json()
        got_rate = float(body.get("rate_applied") or 0)
        got_basis = body.get("rate_basis")
        if abs(got_rate - 45) < 0.01 and got_basis == "within_state":
            p(True, "6. PUT update accepts rate_applied/rate_basis and applies them")
        else:
            p(False, "6. PUT update applied rate fields",
              f"values NOT updated — got rate_applied={got_rate} rate_basis={got_basis!r}. "
              f"ShipmentUpdate model likely ignores unknown fields → Pydantic didn't set them.")
    else:
        p(False, "6. PUT update accepts rate_applied/rate_basis",
          f"unexpected status={r.status_code} body={r.text[:200]}")

    # Step 7: GET /api/me/all-variants
    r = requests.get(f"{API}/me/all-variants", headers=auth_hdr(token), timeout=15)
    p(r.status_code == 200, "7. GET /api/me/all-variants 200", f"status={r.status_code}")
    if r.status_code == 200:
        body = r.json()
        by_courier = body.get("by_courier") or {}
        p(courier_id in by_courier, "7a. by_courier[courier_id] present",
          f"keys={list(by_courier.keys())[:5]}")
        vlist = by_courier.get(courier_id) or []
        variant_ids = [v.get("id") for v in vlist]
        p(variant_id in variant_ids, "7b. created variant_id present in by_courier list",
          f"got_ids={variant_ids}")

    # cleanup attempt (non-fatal)
    try:
        if ship_with:
            requests.delete(f"{API}/shipments/{ship_with['id']}", headers=auth_hdr(token), timeout=10)
        if ship_without:
            requests.delete(f"{API}/shipments/{ship_without['id']}", headers=auth_hdr(token), timeout=10)
    except Exception:
        pass

    print_summary()


def print_summary():
    print("\n=== SUMMARY ===")
    print(f"PASSED: {len(passed)}")
    print(f"FAILED: {len(failed)}")
    if failed:
        print("\nFailed assertions:")
        for lbl, det in failed:
            print(f"  - {lbl}")
            if det:
                print(f"      {det}")
    print(f"\nTotal: {len(passed)}/{len(passed)+len(failed)}")


if __name__ == "__main__":
    main()
    sys.exit(0 if not failed else 1)
