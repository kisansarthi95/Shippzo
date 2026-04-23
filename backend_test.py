"""
Backend test: Settings custom_fields capability
Tests PUT/GET /api/settings with the new custom_fields list
plus smoke checks on /api/shipments, /api/couriers, /api/shipments/stats.
"""
import os
import sys
import json
import requests
from pathlib import Path

# Read backend URL from frontend/.env
env_path = Path("/app/frontend/.env")
BASE = None
for line in env_path.read_text().splitlines():
    if line.startswith("EXPO_PUBLIC_BACKEND_URL="):
        BASE = line.split("=", 1)[1].strip().strip('"') + "/api"
        break
assert BASE, "Could not find EXPO_PUBLIC_BACKEND_URL"
print(f"Using BASE URL: {BASE}")

passed = 0
failed = 0
failures = []


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS: {name}")
    else:
        failed += 1
        failures.append(f"{name} :: {detail}")
        print(f"  FAIL: {name} :: {detail}")


# ------------------------------------------------------------------
# 1) GET /api/settings baseline
# ------------------------------------------------------------------
print("\n[1] GET /api/settings — baseline structure")
r = requests.get(f"{BASE}/settings", timeout=20)
check("GET /settings status 200", r.status_code == 200, f"status={r.status_code}, body={r.text[:300]}")
if r.status_code == 200:
    s = r.json()
    check("Has 'sender' key", "sender" in s)
    check("Has 'brand' key", "brand" in s)
    check("Has 'label_fields' key", "label_fields" in s)
    check("Has 'custom_fields' key", "custom_fields" in s)
    check("'custom_fields' is a list", isinstance(s.get("custom_fields"), list))
    print(f"     (current custom_fields length: {len(s.get('custom_fields', []))})")

# ------------------------------------------------------------------
# 2) PUT /api/settings with 2 custom_fields
# ------------------------------------------------------------------
print("\n[2] PUT /api/settings with 2 custom_fields")
body = {
    "custom_fields": [
        {"label": "GST:", "value": "24ABCDE1234F1Z5", "position": "footer_bottom", "enabled": True, "bold": True, "size": "sm"},
        {"label": "FSSAI:", "value": "10020099999999", "position": "header_top", "enabled": True, "bold": False, "size": "xs"},
    ]
}
r = requests.put(f"{BASE}/settings", json=body, timeout=20)
check("PUT /settings 2 items status 200", r.status_code == 200, f"status={r.status_code}, body={r.text[:300]}")
if r.status_code == 200:
    s = r.json()
    cf = s.get("custom_fields", [])
    check("custom_fields length == 2", len(cf) == 2, f"got {len(cf)}")
    if len(cf) == 2:
        gst = cf[0]
        fssai = cf[1]
        check("Item 0 label == 'GST:'", gst.get("label") == "GST:")
        check("Item 0 value == '24ABCDE1234F1Z5'", gst.get("value") == "24ABCDE1234F1Z5")
        check("Item 0 position == 'footer_bottom'", gst.get("position") == "footer_bottom")
        check("Item 0 enabled True", gst.get("enabled") is True)
        check("Item 0 bold True", gst.get("bold") is True)
        check("Item 0 size == 'sm'", gst.get("size") == "sm")
        check("Item 0 has generated id", bool(gst.get("id")) and isinstance(gst.get("id"), str))

        check("Item 1 label == 'FSSAI:'", fssai.get("label") == "FSSAI:")
        check("Item 1 value == '10020099999999'", fssai.get("value") == "10020099999999")
        check("Item 1 position == 'header_top'", fssai.get("position") == "header_top")
        check("Item 1 enabled True", fssai.get("enabled") is True)
        check("Item 1 bold False", fssai.get("bold") is False)
        check("Item 1 size == 'xs'", fssai.get("size") == "xs")
        check("Item 1 has generated id", bool(fssai.get("id")) and isinstance(fssai.get("id"), str))
        check("IDs are unique", gst.get("id") != fssai.get("id"))

# ------------------------------------------------------------------
# 3) PUT with 8 items — verify only 6 persisted
# ------------------------------------------------------------------
print("\n[3] PUT /api/settings with 8 custom_fields — expect cap at 6")
big = {
    "custom_fields": [
        {"label": f"F{i}:", "value": f"V{i}", "position": "meta_row", "enabled": True, "bold": False, "size": "sm"}
        for i in range(8)
    ]
}
r = requests.put(f"{BASE}/settings", json=big, timeout=20)
check("PUT /settings 8 items status 200", r.status_code == 200, f"status={r.status_code}")
if r.status_code == 200:
    cf = r.json().get("custom_fields", [])
    check("custom_fields capped at 6", len(cf) == 6, f"got {len(cf)}")
    if len(cf) == 6:
        labels = [c.get("label") for c in cf]
        check("First 6 items retained (F0..F5)", labels == [f"F{i}:" for i in range(6)], f"got {labels}")

r = requests.get(f"{BASE}/settings", timeout=20)
if r.status_code == 200:
    cf = r.json().get("custom_fields", [])
    check("GET after 8-item PUT still has 6 items", len(cf) == 6, f"got {len(cf)}")

# ------------------------------------------------------------------
# 4) PUT with empty list — verify list cleared
# ------------------------------------------------------------------
print("\n[4] PUT /api/settings with custom_fields=[] — clear")
r = requests.put(f"{BASE}/settings", json={"custom_fields": []}, timeout=20)
check("PUT /settings empty list status 200", r.status_code == 200, f"status={r.status_code}, body={r.text[:300]}")
if r.status_code == 200:
    cf = r.json().get("custom_fields", None)
    check("custom_fields cleared (empty list)", cf == [], f"got {cf!r}")

r = requests.get(f"{BASE}/settings", timeout=20)
if r.status_code == 200:
    check("GET after empty PUT returns []", r.json().get("custom_fields") == [])

# ------------------------------------------------------------------
# 5) Re-populate custom_fields, then PUT without the key — verify preserved
# ------------------------------------------------------------------
print("\n[5] PUT updating only sender must preserve custom_fields")
seed_body = {
    "custom_fields": [
        {"label": "PAN:", "value": "ABCDE1234F", "position": "from_block", "enabled": True, "bold": True, "size": "md"},
    ]
}
r = requests.put(f"{BASE}/settings", json=seed_body, timeout=20)
check("Seed custom_fields PUT 200", r.status_code == 200)
seeded_id = None
if r.status_code == 200:
    cf = r.json().get("custom_fields", [])
    check("Seed length == 1", len(cf) == 1)
    if cf:
        seeded_id = cf[0].get("id")

sender_body = {
    "sender": {
        "name": "Mahek Creations",
        "phone": "9876543210",
        "address_line1": "Shop 5, Market Road",
        "address_line2": "Opp Post Office",
        "city": "Ahmedabad",
        "state": "Gujarat",
        "pincode": "380001",
        "show_contact": True,
    }
}
r = requests.put(f"{BASE}/settings", json=sender_body, timeout=20)
check("PUT /settings sender-only 200", r.status_code == 200, f"status={r.status_code}, body={r.text[:300]}")
if r.status_code == 200:
    s = r.json()
    check("Sender name updated", s.get("sender", {}).get("name") == "Mahek Creations")
    cf = s.get("custom_fields", [])
    check("custom_fields preserved after sender-only PUT (length 1)", len(cf) == 1, f"got {len(cf)}")
    if cf:
        check("Preserved item label == 'PAN:'", cf[0].get("label") == "PAN:")
        check("Preserved item id unchanged", cf[0].get("id") == seeded_id, f"got {cf[0].get('id')} vs {seeded_id}")

# ------------------------------------------------------------------
# 6) Final GET reflects latest persisted state
# ------------------------------------------------------------------
print("\n[6] Final GET /api/settings")
r = requests.get(f"{BASE}/settings", timeout=20)
check("Final GET 200", r.status_code == 200)
if r.status_code == 200:
    s = r.json()
    cf = s.get("custom_fields", [])
    check("Final custom_fields length 1", len(cf) == 1)
    if cf:
        check("Final item is PAN entry", cf[0].get("label") == "PAN:" and cf[0].get("value") == "ABCDE1234F")
    check("Sender preserved in final GET", s.get("sender", {}).get("name") == "Mahek Creations")

# ------------------------------------------------------------------
# 7) Smoke check existing endpoints (regression)
# ------------------------------------------------------------------
print("\n[7] Regression smoke: /shipments, /couriers, /shipments/stats")
r = requests.get(f"{BASE}/shipments", timeout=20)
check("GET /shipments 200", r.status_code == 200, f"status={r.status_code}")
if r.status_code == 200:
    check("/shipments returns list", isinstance(r.json(), list))

r = requests.get(f"{BASE}/couriers", timeout=20)
check("GET /couriers 200", r.status_code == 200, f"status={r.status_code}")
if r.status_code == 200:
    body = r.json()
    check("/couriers returns non-empty list", isinstance(body, list) and len(body) > 0)
    if body:
        c = body[0]
        check("Courier has id+name", "id" in c and "name" in c)
        check("Courier has customer_id field", "customer_id" in c)

r = requests.get(f"{BASE}/shipments/stats", timeout=20)
check("GET /shipments/stats 200", r.status_code == 200, f"status={r.status_code}")
if r.status_code == 200:
    st = r.json()
    for k in ("total", "delivered", "pending", "cod_total", "prepaid_total", "revenue_total"):
        check(f"stats has '{k}'", k in st)

# ------------------------------------------------------------------
print("\n" + "=" * 60)
print(f"RESULTS: {passed} passed, {failed} failed")
if failures:
    print("\nFAILURES:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("ALL CHECKS PASSED")
