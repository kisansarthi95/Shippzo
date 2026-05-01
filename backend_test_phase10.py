"""Phase-10 Scan-to-Shipped focused regression.

Verifies POST /api/shipments/scan-ship for all atomic outcomes.
"""
import os
import sys
import uuid
import requests

BASE = "https://logistics-hub-740.preview.emergentagent.com/api"
EMAIL = "admin@test.com"
PASSWORD = "Admin@12345"

results = []
created_ids = []


def log(name, ok, detail=""):
    tag = "PASS" if ok else "FAIL"
    print(f"[{tag}] {name} :: {detail}")
    results.append((name, ok, detail))


def login():
    r = requests.post(f"{BASE}/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=20)
    r.raise_for_status()
    return r.json()["token"]


def main():
    token = login()
    H = {"Authorization": f"Bearer {token}"}

    # Find a courier so we can create shipments cleanly.
    r = requests.get(f"{BASE}/couriers", headers=H, timeout=20)
    r.raise_for_status()
    couriers = r.json()
    if not couriers:
        # Create a temp courier
        r = requests.post(f"{BASE}/couriers", headers=H, json={
            "name": "Phase10 Test Courier", "series_prefix": "P10", "next_number": 1, "number_padding": 5,
        }, timeout=20)
        r.raise_for_status()
        courier = r.json()
    else:
        courier = couriers[0]

    # ---- Test 1: empty tracking_id ----
    r = requests.post(f"{BASE}/shipments/scan-ship", headers=H, json={"tracking_id": ""}, timeout=20)
    j = r.json()
    log("T1 empty tracking_id status 200", r.status_code == 200, f"got {r.status_code}")
    log("T1 outcome=failed", j.get("outcome") == "failed", str(j))
    log("T1 reason=empty_tracking_id", j.get("reason") == "empty_tracking_id", str(j))
    log("T1 shipment is null", j.get("shipment") is None, str(j))

    # ---- Test 2: not_found ----
    rand_tid = f"NOPE{uuid.uuid4().hex[:8].upper()}"
    r = requests.post(f"{BASE}/shipments/scan-ship", headers=H, json={"tracking_id": rand_tid}, timeout=20)
    j = r.json()
    log("T2 outcome=failed", j.get("outcome") == "failed", str(j))
    log("T2 reason=not_found", j.get("reason") == "not_found", str(j))
    log("T2 shipment is null", j.get("shipment") is None, str(j))

    # ---- Test 3: Pending → wrong_status:Pending ----
    tid_a = f"P10A{uuid.uuid4().hex[:8].upper()}"
    payload_a = {
        "tracking_id": tid_a,
        "courier_id": courier["id"],
        "courier_name": courier["name"],
        "customer_name": "Phase10 Pending Test",
        "customer_phone": "9988776655",
        "address_line1": "10 Test Street",
        "city": "Ahmedabad",
        "state": "Gujarat",
        "pincode": "380001",
        "payment_mode": "Prepaid",
        "amount": 100.0,
    }
    r = requests.post(f"{BASE}/shipments", headers=H, json=payload_a, timeout=20)
    if r.status_code != 200:
        log("T3 create shipment", False, f"{r.status_code} {r.text[:200]}")
        return summary()
    ship_a = r.json()
    created_ids.append(ship_a["id"])
    log("T3 created Pending shipment", ship_a.get("status") == "Pending", f"status={ship_a.get('status')}")

    r = requests.post(f"{BASE}/shipments/scan-ship", headers=H, json={"tracking_id": tid_a}, timeout=20)
    j = r.json()
    log("T3 scan-ship Pending → outcome=failed", j.get("outcome") == "failed", str(j)[:300])
    log("T3 reason=wrong_status:Pending", j.get("reason") == "wrong_status:Pending", str(j)[:300])
    log("T3 shipment non-null", j.get("shipment") is not None, "")

    # ---- Test 4: scan-dispatch then scan-ship → moved ----
    r = requests.post(f"{BASE}/shipments/scan-dispatch", headers=H, json={"tracking_id": tid_a}, timeout=20)
    jd = r.json()
    log("T4 scan-dispatch → moved", jd.get("outcome") == "moved", str(jd)[:200])

    r = requests.post(f"{BASE}/shipments/scan-ship", headers=H, json={"tracking_id": tid_a}, timeout=20)
    j = r.json()
    log("T4 scan-ship → moved", j.get("outcome") == "moved", str(j)[:300])
    log("T4 reason=ok", j.get("reason") == "ok", str(j)[:300])
    log("T4 message contains 'moved to Shipped'", "moved to Shipped" in (j.get("message") or ""), str(j.get("message")))
    sh = j.get("shipment") or {}
    log("T4 shipment.status=Shipped", sh.get("status") == "Shipped", f"got {sh.get('status')}")
    log("T4 shipped_at set", bool(sh.get("shipped_at")), f"shipped_at={sh.get('shipped_at')}")

    # Verify via GET /api/shipments
    r = requests.get(f"{BASE}/shipments?limit=200", headers=H, timeout=20)
    arr = r.json()
    found = next((s for s in arr if s.get("id") == ship_a["id"]), None)
    log("T4 GET /shipments finds shipment", found is not None, "")
    if found:
        log("T4 GET status=Shipped", found.get("status") == "Shipped", f"status={found.get('status')}")
        log("T4 GET shipped_at set", bool(found.get("shipped_at")), f"shipped_at={found.get('shipped_at')}")

    # ---- Test 5: scan-ship again on Shipped → already ----
    r = requests.post(f"{BASE}/shipments/scan-ship", headers=H, json={"tracking_id": tid_a}, timeout=20)
    j = r.json()
    log("T5 outcome=already", j.get("outcome") == "already", str(j)[:300])
    log("T5 reason=already_shipped", j.get("reason") == "already_shipped", str(j)[:300])
    sh5 = j.get("shipment") or {}
    log("T5 shipment.status=Shipped", sh5.get("status") == "Shipped", f"got {sh5.get('status')}")

    # ---- Test 6: legacy 'Dispatched' → moved ----
    tid_b = f"P10B{uuid.uuid4().hex[:8].upper()}"
    payload_b = dict(payload_a)
    payload_b["tracking_id"] = tid_b
    payload_b["customer_name"] = "Phase10 Legacy Test"
    r = requests.post(f"{BASE}/shipments", headers=H, json=payload_b, timeout=20)
    if r.status_code != 200:
        log("T6 create shipment", False, f"{r.status_code} {r.text[:200]}")
    else:
        ship_b = r.json()
        created_ids.append(ship_b["id"])
        # Set status to legacy 'Dispatched' via PUT
        r = requests.put(f"{BASE}/shipments/{ship_b['id']}", headers=H, json={"status": "Dispatched"}, timeout=20)
        log("T6 PUT status=Dispatched", r.status_code == 200, f"{r.status_code} {r.text[:200]}")
        if r.status_code == 200:
            updated = r.json()
            log("T6 status persisted as Dispatched", updated.get("status") == "Dispatched", f"got {updated.get('status')}")

            r = requests.post(f"{BASE}/shipments/scan-ship", headers=H, json={"tracking_id": tid_b}, timeout=20)
            j = r.json()
            log("T6 scan-ship legacy Dispatched → moved", j.get("outcome") == "moved", str(j)[:300])
            log("T6 reason=ok", j.get("reason") == "ok", str(j)[:300])
            sh6 = j.get("shipment") or {}
            log("T6 shipment.status=Shipped", sh6.get("status") == "Shipped", f"got {sh6.get('status')}")
            log("T6 shipped_at set", bool(sh6.get("shipped_at")), f"shipped_at={sh6.get('shipped_at')}")

    # ---- Test 7: GET /shipments/stats includes shipped count ----
    r = requests.get(f"{BASE}/shipments/stats", headers=H, timeout=20)
    stats = r.json()
    log("T7 stats has 'shipped' key", "shipped" in stats, str(stats))
    log("T7 stats.shipped is int", isinstance(stats.get("shipped"), int), f"shipped={stats.get('shipped')}")
    log("T7 stats.shipped >= 1", int(stats.get("shipped", 0)) >= 1, f"shipped={stats.get('shipped')}")

    return summary()


def summary():
    # Cleanup
    print("\n--- Cleanup ---")
    token = login()
    H = {"Authorization": f"Bearer {token}"}
    for sid in created_ids:
        try:
            r = requests.delete(f"{BASE}/shipments/{sid}", headers=H, timeout=20)
            print(f"  DELETE {sid} → {r.status_code}")
        except Exception as e:
            print(f"  DELETE {sid} failed: {e}")

    print("\n--- SUMMARY ---")
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"PASSED {passed}/{total}")
    fails = [(n, d) for n, ok, d in results if not ok]
    if fails:
        print("\nFAILURES:")
        for n, d in fails:
            print(f"  - {n}: {d}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
