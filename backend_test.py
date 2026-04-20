"""Backend API tests for Courier CRUD endpoints with customer_id field."""
import os
import sys
import requests
from pathlib import Path

# Load backend URL from frontend .env
ENV_PATH = Path(__file__).parent / "frontend" / ".env"
BASE_URL = None
if ENV_PATH.exists():
    for line in ENV_PATH.read_text().splitlines():
        if line.startswith("EXPO_PUBLIC_BACKEND_URL="):
            BASE_URL = line.split("=", 1)[1].strip().strip('"')
            break

if not BASE_URL:
    BASE_URL = "http://localhost:8001"

API = f"{BASE_URL}/api"
print(f"Using API base URL: {API}\n")

results = []
created_courier_id = None


def record(name, ok, detail=""):
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}{': ' + detail if detail else ''}")
    results.append((name, ok, detail))


def _req(method, path, **kwargs):
    url = f"{API}{path}"
    return requests.request(method, url, timeout=30, **kwargs)


# -----------------------------------------------------------------------------
# TEST 1: GET /api/couriers includes customer_id field
# -----------------------------------------------------------------------------
try:
    r = _req("GET", "/couriers")
    if r.status_code != 200:
        record("GET /couriers returns 200", False, f"status={r.status_code}, body={r.text[:300]}")
    else:
        data = r.json()
        if not isinstance(data, list):
            record("GET /couriers returns list", False, f"type={type(data)}")
        else:
            record("GET /couriers returns 200 list", True, f"{len(data)} couriers")
            # Every courier must have customer_id field (empty string default)
            missing = [c.get("name") for c in data if "customer_id" not in c]
            if missing:
                record("All couriers include customer_id", False, f"Missing in: {missing}")
            else:
                record("All couriers include customer_id", True, "field present on all")
            # Default value should be string (empty "" for ones that never had it)
            non_string = [c.get("name") for c in data if not isinstance(c.get("customer_id"), str)]
            if non_string:
                record("customer_id is string type", False, f"non-string in: {non_string}")
            else:
                record("customer_id is string type (empty string default ok)", True)
except Exception as e:
    record("GET /couriers", False, f"exception: {e}")


# -----------------------------------------------------------------------------
# TEST 2: POST /api/couriers with name + customer_id creates courier
# -----------------------------------------------------------------------------
try:
    payload = {
        "name": "India Post (Test)",
        "customer_id": "1000057527",
        "series_prefix": "IPT",
        "next_number": 101,
        "number_padding": 5,
        "contact_phone": "1800-266-6868",
        "tracking_url_template": "https://www.indiapost.gov.in/track/{tracking_id}",
    }
    r = _req("POST", "/couriers", json=payload)
    if r.status_code != 200:
        record("POST /couriers creates courier", False, f"status={r.status_code}, body={r.text[:300]}")
    else:
        data = r.json()
        created_courier_id = data.get("id")
        if not created_courier_id:
            record("POST /couriers returns id", False, f"body={data}")
        else:
            record("POST /couriers creates courier", True, f"id={created_courier_id}")
            if data.get("customer_id") != "1000057527":
                record("POST persists customer_id", False, f"got customer_id={data.get('customer_id')!r}")
            else:
                record("POST persists customer_id", True, "customer_id=1000057527")
            # Verify other fields
            for k, v in payload.items():
                if data.get(k) != v:
                    record(f"POST persists {k}", False, f"expected={v!r}, got={data.get(k)!r}")
                    break
            else:
                record("POST persists all other fields", True)
except Exception as e:
    record("POST /couriers", False, f"exception: {e}")


# -----------------------------------------------------------------------------
# TEST 3: PUT /api/couriers/{id} with only customer_id preserves other fields
# -----------------------------------------------------------------------------
if created_courier_id:
    try:
        # First: capture current courier snapshot
        r_get = _req("GET", f"/couriers/{created_courier_id}")
        before = r_get.json() if r_get.status_code == 200 else {}

        r = _req("PUT", f"/couriers/{created_courier_id}", json={"customer_id": "1000057527"})
        if r.status_code != 200:
            record("PUT customer_id only", False, f"status={r.status_code}, body={r.text[:300]}")
        else:
            data = r.json()
            if data.get("customer_id") != "1000057527":
                record("PUT updates customer_id", False, f"got={data.get('customer_id')!r}")
            else:
                record("PUT updates customer_id", True)
            # Verify ALL other fields preserved
            preserved_keys = [
                "name", "series_prefix", "next_number", "number_padding",
                "contact_phone", "contact_email", "website_url",
                "tracking_url_template", "notes", "created_at",
            ]
            mismatches = []
            for k in preserved_keys:
                if before.get(k) != data.get(k):
                    mismatches.append(f"{k}: before={before.get(k)!r}, after={data.get(k)!r}")
            if mismatches:
                record("PUT preserves all other fields", False, "; ".join(mismatches))
            else:
                record("PUT preserves all other fields", True)
    except Exception as e:
        record("PUT customer_id only", False, f"exception: {e}")


# -----------------------------------------------------------------------------
# TEST 4: PUT with customer_id="" (empty string) should clear it
# -----------------------------------------------------------------------------
if created_courier_id:
    try:
        r = _req("PUT", f"/couriers/{created_courier_id}", json={"customer_id": ""})
        if r.status_code != 200:
            record("PUT customer_id='' accepted", False, f"status={r.status_code}, body={r.text[:300]}")
        else:
            data = r.json()
            if data.get("customer_id") != "":
                record("PUT customer_id='' clears value", False, f"got customer_id={data.get('customer_id')!r}")
            else:
                record("PUT customer_id='' clears value", True, "customer_id is now ''")
    except Exception as e:
        record("PUT customer_id=''", False, f"exception: {e}")


# -----------------------------------------------------------------------------
# TEST 5: GET /api/couriers/{id} returns updated customer_id
# -----------------------------------------------------------------------------
if created_courier_id:
    try:
        # Set to a new value, then GET and verify
        r_put = _req("PUT", f"/couriers/{created_courier_id}", json={"customer_id": "9988776655"})
        r = _req("GET", f"/couriers/{created_courier_id}")
        if r.status_code != 200:
            record("GET /couriers/{id} after update", False, f"status={r.status_code}, body={r.text[:300]}")
        else:
            data = r.json()
            if data.get("customer_id") != "9988776655":
                record("GET returns updated customer_id", False, f"got={data.get('customer_id')!r}")
            else:
                record("GET returns updated customer_id", True, "customer_id=9988776655")
    except Exception as e:
        record("GET after update", False, f"exception: {e}")


# -----------------------------------------------------------------------------
# TEST 6a: POST without customer_id still works (defaults to "")
# -----------------------------------------------------------------------------
regression_id = None
try:
    payload = {
        "name": "Regression Test Courier",
        "series_prefix": "RGT",
        "next_number": 1,
        "number_padding": 4,
        "contact_phone": "9123456789",
        "tracking_url_template": "https://example.com/track/{tracking_id}",
    }
    r = _req("POST", "/couriers", json=payload)
    if r.status_code != 200:
        record("POST without customer_id", False, f"status={r.status_code}, body={r.text[:300]}")
    else:
        data = r.json()
        regression_id = data.get("id")
        if data.get("customer_id") != "":
            record("POST w/o customer_id defaults to ''", False, f"got={data.get('customer_id')!r}")
        else:
            record("POST w/o customer_id defaults to ''", True)
        # Check all other fields round-tripped
        ok = all(data.get(k) == v for k, v in payload.items())
        record("POST preserves fields w/o customer_id", ok)
except Exception as e:
    record("POST regression", False, f"exception: {e}")


# -----------------------------------------------------------------------------
# TEST 6b: PUT existing fields (no customer_id in body) still works
# -----------------------------------------------------------------------------
if regression_id:
    try:
        payload = {
            "name": "Regression Test Courier (Updated)",
            "tracking_url_template": "https://newexample.com/t/{tracking_id}",
            "contact_phone": "9000000000",
            "series_prefix": "RGX",
        }
        r = _req("PUT", f"/couriers/{regression_id}", json=payload)
        if r.status_code != 200:
            record("PUT other fields w/o customer_id", False, f"status={r.status_code}, body={r.text[:300]}")
        else:
            data = r.json()
            ok = all(data.get(k) == v for k, v in payload.items())
            if not ok:
                mismatches = {k: (v, data.get(k)) for k, v in payload.items() if data.get(k) != v}
                record("PUT other fields w/o customer_id", False, f"mismatches={mismatches}")
            else:
                record("PUT other fields w/o customer_id", True)
            # customer_id unchanged ("" from create)
            if data.get("customer_id") != "":
                record("PUT w/o customer_id preserves previous", False, f"got={data.get('customer_id')!r}")
            else:
                record("PUT w/o customer_id preserves previous", True)
    except Exception as e:
        record("PUT regression", False, f"exception: {e}")


# -----------------------------------------------------------------------------
# TEST 7: Cleanup - delete test couriers
# -----------------------------------------------------------------------------
for cid, label in [(created_courier_id, "primary"), (regression_id, "regression")]:
    if cid:
        try:
            r = _req("DELETE", f"/couriers/{cid}")
            if r.status_code == 200 and r.json().get("ok"):
                record(f"DELETE cleanup ({label})", True, f"id={cid}")
            else:
                record(f"DELETE cleanup ({label})", False, f"status={r.status_code}, body={r.text[:200]}")
        except Exception as e:
            record(f"DELETE cleanup ({label})", False, f"exception: {e}")


# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------
print("\n" + "=" * 70)
passed = sum(1 for _, ok, _ in results if ok)
failed = sum(1 for _, ok, _ in results if not ok)
print(f"RESULTS: {passed} passed, {failed} failed (of {len(results)})")
if failed:
    print("\nFAILURES:")
    for name, ok, detail in results:
        if not ok:
            print(f"  - {name}: {detail}")
sys.exit(0 if failed == 0 else 1)
