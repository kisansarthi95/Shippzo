"""
Phase-16 Contact Save Settings + Build Endpoints Backend Test.

Covers:
  GET  /api/me/contact-settings
  PUT  /api/me/contact-settings
  POST /api/contacts/build-one
  POST /api/contacts/build-vcf

Target: https://logistics-hub-740.preview.emergentagent.com/api
Creds:  admin@test.com / Admin@12345
"""
import os
import sys
import json
import uuid
from typing import Any, Dict, List, Tuple

import requests

BASE = os.environ.get(
    "BACKEND_BASE",
    "https://logistics-hub-740.preview.emergentagent.com/api",
)
ADMIN_EMAIL = "admin@test.com"
ADMIN_PASS = "Admin@12345"

PASS: List[str] = []
FAIL: List[Tuple[str, str]] = []


def expect(cond: bool, label: str, detail: str = ""):
    if cond:
        PASS.append(label)
        print(f"  PASS  {label}")
    else:
        FAIL.append((label, detail))
        print(f"  FAIL  {label}  --  {detail}")


def login(email: str, password: str) -> str:
    r = requests.post(f"{BASE}/auth/login", json={"email": email, "password": password}, timeout=20)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    return r.json()["token"]


def H(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def pretty(obj: Any) -> str:
    try:
        return json.dumps(obj, indent=2, default=str, ensure_ascii=False)
    except Exception:
        return str(obj)


# ───────────────── Test 1: GET default shape ──────────────────────────
def test_get_default_shape(token: str):
    print("\n[Test 1] GET /api/me/contact-settings (defaults)")
    r = requests.get(f"{BASE}/me/contact-settings", headers=H(token), timeout=20)
    expect(r.status_code == 200, "T1.status=200", f"got {r.status_code} body={r.text}")
    if r.status_code != 200:
        return
    data = r.json()
    print(pretty(data))
    nf = data.get("name_format", {})
    expect(nf.get("prefix_enabled") is True, "T1.name_format.prefix_enabled=true")
    expect(nf.get("prefix_position") == "start", "T1.name_format.prefix_position=start")
    expect(nf.get("name_type") == "full", "T1.name_format.name_type=full")
    expect(nf.get("product_placement") == "after_name", "T1.name_format.product_placement=after_name")
    expect(nf.get("location") == "city", "T1.name_format.location=city")
    fm = data.get("field_mapping", {})
    expect(fm.get("address_target") == "address", "T1.field_mapping.address_target=address")
    expect(fm.get("product_target") == "notes", "T1.field_mapping.product_target=notes")
    ni = fm.get("notes_include", {})
    expect(ni.get("order_id") is False, "T1.notes_include.order_id=false")
    cat = data.get("category", {})
    expect(cat.get("auto_assign") is True, "T1.category.auto_assign=true")
    expect(cat.get("manual_popup") is False, "T1.category.manual_popup=false")


# ───────────────── Test 2: PUT with category merge-save ───────────────
def test_put_category_merge(token: str):
    print("\n[Test 2] PUT /api/me/contact-settings (merge-save category only)")
    payload = {
        "category": {
            "categories": ["KSS", "KOC"],
            "default_category": "KOC",
            "auto_assign": True,
            "manual_popup": False,
            "product_mapping": [
                {"keyword": "garlic", "category": "KSS"},
                {"keyword": "soap", "category": "KOC"},
            ],
        },
    }
    r = requests.put(f"{BASE}/me/contact-settings", headers=H(token), json=payload, timeout=20)
    expect(r.status_code == 200, "T2.status=200", f"got {r.status_code} body={r.text}")
    if r.status_code != 200:
        return
    data = r.json()
    print(pretty(data))
    cat = data.get("category", {})
    expect(cat.get("categories") == ["KSS", "KOC"], "T2.category.categories preserved")
    expect(cat.get("default_category") == "KOC", "T2.default_category=KOC")
    pm = cat.get("product_mapping", [])
    expect(len(pm) == 2, f"T2.product_mapping length=2 (got {len(pm)})")
    expect(
        any(p.get("keyword") == "garlic" and p.get("category") == "KSS" for p in pm),
        "T2.product_mapping has garlic->KSS",
    )
    nf = data.get("name_format", {})
    expect(nf.get("prefix_enabled") is True, "T2.name_format.prefix_enabled still true")
    expect(nf.get("name_type") == "full", "T2.name_format.name_type still full")
    expect(nf.get("location") == "city", "T2.name_format.location still city")
    fm = data.get("field_mapping", {})
    expect(fm.get("address_target") == "address", "T2.field_mapping.address_target still address")
    expect(fm.get("product_target") == "notes", "T2.field_mapping.product_target still notes")


# ───────────────── Test 3: build-one inline shipment ──────────────────
SHIPMENT_PAYLOAD = {
    "customer_name": "Ramesh Patel",
    "customer_phone": "9876543210",
    "items": "Garlic",
    "address_line1": "Shop 12, Main Bazaar",
    "city": "Surat",
    "state": "Gujarat",
    "pincode": "395003",
    "order_id": "ORD-1024",
    "quantity": "2kg",
    "payment_mode": "COD",
}


def test_build_one_default(token: str):
    print("\n[Test 3] POST /api/contacts/build-one (inline, defaults)")
    r = requests.post(
        f"{BASE}/contacts/build-one",
        headers=H(token),
        json={"shipment": SHIPMENT_PAYLOAD},
        timeout=20,
    )
    expect(r.status_code == 200, "T3.status=200", f"got {r.status_code} body={r.text}")
    if r.status_code != 200:
        return
    data = r.json()
    print(pretty(data))
    expect(data.get("name") == "[KSS] Ramesh Patel | Garlic | Surat",
           f"T3.name='[KSS] Ramesh Patel | Garlic | Surat' (got {data.get('name')!r})")
    expect(data.get("phone") == "9876543210", f"T3.phone=9876543210 (got {data.get('phone')!r})")
    postal = data.get("postal", "")
    expect("Shop 12" in postal, f"T3.postal contains 'Shop 12' (got {postal!r})")
    expect("395003" in postal, f"T3.postal contains '395003' (got {postal!r})")
    expect(data.get("notes") == "Ordered: Garlic",
           f"T3.notes='Ordered: Garlic' (got {data.get('notes')!r})")
    expect(data.get("category") == "KSS",
           f"T3.category='KSS' (got {data.get('category')!r})")


# ───────────────── Test 4: build-one override category ────────────────
def test_build_one_override(token: str):
    print("\n[Test 4] POST /api/contacts/build-one (override_category='KOC')")
    r = requests.post(
        f"{BASE}/contacts/build-one",
        headers=H(token),
        json={"shipment": SHIPMENT_PAYLOAD, "override_category": "KOC"},
        timeout=20,
    )
    expect(r.status_code == 200, "T4.status=200", f"got {r.status_code} body={r.text}")
    if r.status_code != 200:
        return
    data = r.json()
    print(pretty(data))
    name = data.get("name", "")
    expect(name.startswith("[KOC] Ramesh"), f"T4.name starts with '[KOC] Ramesh' (got {name!r})")
    expect(data.get("category") == "KOC", f"T4.category='KOC' (got {data.get('category')!r})")


# ───────────────── Test 5: PUT custom name_format + build ─────────────
def test_put_and_build_firstname_noprefix(token: str):
    print("\n[Test 5] PUT name_format (first, no location, no prefix) + build-one")
    payload = {
        "name_format": {
            "prefix_enabled": False,
            "prefix_position": "start",
            "name_type": "first",
            "product_placement": "after_name",
            "location": "none",
        },
    }
    r = requests.put(f"{BASE}/me/contact-settings", headers=H(token), json=payload, timeout=20)
    expect(r.status_code == 200, "T5.PUT status=200", f"got {r.status_code} body={r.text}")
    if r.status_code != 200:
        return
    data = r.json()
    nf = data.get("name_format", {})
    expect(nf.get("prefix_enabled") is False, "T5.name_format.prefix_enabled=false")
    expect(nf.get("name_type") == "first", "T5.name_format.name_type=first")
    expect(nf.get("location") == "none", "T5.name_format.location=none")
    cat = data.get("category", {})
    expect(cat.get("categories") == ["KSS", "KOC"], "T5.category.categories preserved from T2")

    r2 = requests.post(
        f"{BASE}/contacts/build-one",
        headers=H(token),
        json={"shipment": SHIPMENT_PAYLOAD},
        timeout=20,
    )
    expect(r2.status_code == 200, "T5.build status=200", f"got {r2.status_code} body={r2.text}")
    if r2.status_code != 200:
        return
    built = r2.json()
    print(pretty(built))
    expect(built.get("name") == "Ramesh | Garlic",
           f"T5.name='Ramesh | Garlic' (got {built.get('name')!r})")


# ───────────────── Test 6: build-vcf with real shipments ──────────────
def _get_default_courier(token: str) -> str:
    rs = requests.get(f"{BASE}/couriers", headers=H(token), timeout=20).json()
    if isinstance(rs, list) and rs:
        return rs[0]["id"]
    r = requests.post(
        f"{BASE}/couriers", headers=H(token),
        json={"name": "Test Courier", "series_prefix": "TC", "next_number": 1, "number_padding": 4},
        timeout=20,
    )
    return r.json()["id"]


def _create_shipment(token: str, name: str, phone: str, courier_id: str) -> str:
    tid = f"TESTVCF-{uuid.uuid4().hex[:6].upper()}"
    payload = {
        "tracking_id": tid,
        "courier_id": courier_id,
        "courier_name": "Test Courier",
        "customer_name": name,
        "customer_phone": phone,
        "address_line1": "Block A, Flat 101",
        "city": "Ahmedabad",
        "state": "Gujarat",
        "pincode": "380001",
        "items": ["garlic"],
        "amount": 250.0,
        "payment_mode": "COD",
    }
    r = requests.post(f"{BASE}/shipments", headers=H(token), json=payload, timeout=20)
    assert r.status_code == 200, f"create shipment failed: {r.status_code} {r.text}"
    return r.json()["id"]


def _create_shipment_no_phone(token: str, name: str, courier_id: str) -> str:
    tid = f"TESTVCFNP-{uuid.uuid4().hex[:6].upper()}"
    payload = {
        "tracking_id": tid,
        "courier_id": courier_id,
        "courier_name": "Test Courier",
        "customer_name": name,
        "customer_phone": "",
        "address_line1": "Some addr",
        "city": "Rajkot",
        "state": "Gujarat",
        "pincode": "360001",
        "items": ["soap"],
        "amount": 100.0,
        "payment_mode": "Prepaid",
    }
    r = requests.post(f"{BASE}/shipments", headers=H(token), json=payload, timeout=20)
    assert r.status_code == 200, f"create shipment failed: {r.status_code} {r.text}"
    return r.json()["id"]


def test_build_vcf(token: str):
    print("\n[Test 6] POST /api/contacts/build-vcf (3 shipments w/ phone)")
    courier_id = _get_default_courier(token)
    sids = [
        _create_shipment(token, "Meena Shah", "9111111111", courier_id),
        _create_shipment(token, "Rakesh Joshi", "9222222222", courier_id),
        _create_shipment(token, "Priya Mehta", "9333333333", courier_id),
    ]
    r = requests.post(
        f"{BASE}/contacts/build-vcf",
        headers=H(token),
        json={"shipment_ids": sids},
        timeout=20,
    )
    expect(r.status_code == 200, "T6.status=200", f"got {r.status_code} body={r.text}")
    if r.status_code != 200:
        return sids, courier_id
    data = r.json()
    vcf = data.get("vcf", "")
    count = data.get("count", 0)
    begin_n = vcf.count("BEGIN:VCARD")
    end_n = vcf.count("END:VCARD")
    fn_n = vcf.count("FN:")
    tel_n = vcf.count("TEL")
    expect(begin_n == 3, f"T6.BEGIN:VCARD x3 (got {begin_n})")
    expect(end_n == 3, f"T6.END:VCARD x3 (got {end_n})")
    expect(fn_n == 3, f"T6.FN: x3 (got {fn_n})")
    expect(tel_n >= 3, f"T6.TEL line for each vcard (got {tel_n})")
    expect(count == 3, f"T6.count=3 (got {count})")
    print(vcf[:600])
    return sids, courier_id


def test_build_vcf_no_phone(token: str, courier_id: str):
    print("\n[Test 7] POST /api/contacts/build-vcf (all no-phone -> 400)")
    sids = [
        _create_shipment_no_phone(token, "NoPhone One", courier_id),
        _create_shipment_no_phone(token, "NoPhone Two", courier_id),
    ]
    try:
        r = requests.post(
            f"{BASE}/contacts/build-vcf",
            headers=H(token),
            json={"shipment_ids": sids},
            timeout=20,
        )
        expect(r.status_code == 400, f"T7.status=400 (got {r.status_code})",
               f"body={r.text[:200]}")
    finally:
        for sid in sids:
            try:
                requests.delete(f"{BASE}/shipments/{sid}", headers=H(token), timeout=10)
            except Exception:
                pass


def test_build_one_empty(token: str):
    print("\n[Test 8] POST /api/contacts/build-one (empty body -> 400)")
    r = requests.post(
        f"{BASE}/contacts/build-one",
        headers=H(token),
        json={},
        timeout=20,
    )
    expect(r.status_code == 400, f"T8.status=400 (got {r.status_code})", f"body={r.text[:200]}")


def test_get_no_auth():
    print("\n[Test 9] GET /api/me/contact-settings (no auth -> 401)")
    r = requests.get(f"{BASE}/me/contact-settings", timeout=20)
    expect(r.status_code in (401, 403), f"T9.status in 401/403 (got {r.status_code})")


def test_persistence(token: str):
    print("\n[Test 10] Persistence round-trip")
    payload = {
        "name_format": {
            "prefix_enabled": True,
            "prefix_position": "end",
            "name_type": "first",
            "product_placement": "end",
            "location": "taluka",
        },
        "field_mapping": {
            "address_target": "notes",
            "product_target": "both",
            "notes_include": {
                "order_id": True,
                "quantity": True,
                "payment_mode": False,
            },
        },
        "category": {
            "categories": ["A1", "B2", "C3"],
            "default_category": "A1",
            "auto_assign": False,
            "manual_popup": True,
            "product_mapping": [
                {"keyword": "honey", "category": "A1"},
            ],
        },
    }
    r = requests.put(f"{BASE}/me/contact-settings", headers=H(token), json=payload, timeout=20)
    expect(r.status_code == 200, "T10.PUT status=200", f"got {r.status_code} body={r.text}")
    if r.status_code != 200:
        return
    r2 = requests.get(f"{BASE}/me/contact-settings", headers=H(token), timeout=20)
    expect(r2.status_code == 200, "T10.GET status=200")
    got = r2.json()
    print(pretty(got))
    expect(got.get("name_format", {}).get("prefix_position") == "end", "T10.name_format.prefix_position=end")
    expect(got.get("name_format", {}).get("location") == "taluka", "T10.name_format.location=taluka")
    expect(got.get("field_mapping", {}).get("address_target") == "notes", "T10.field_mapping.address_target=notes")
    expect(got.get("field_mapping", {}).get("product_target") == "both", "T10.field_mapping.product_target=both")
    expect(got.get("field_mapping", {}).get("notes_include", {}).get("order_id") is True,
           "T10.notes_include.order_id=true")
    expect(got.get("field_mapping", {}).get("notes_include", {}).get("quantity") is True,
           "T10.notes_include.quantity=true")
    expect(got.get("category", {}).get("categories") == ["A1", "B2", "C3"],
           "T10.category.categories=[A1,B2,C3]")
    expect(got.get("category", {}).get("default_category") == "A1", "T10.default_category=A1")
    expect(got.get("category", {}).get("auto_assign") is False, "T10.auto_assign=false")
    expect(got.get("category", {}).get("manual_popup") is True, "T10.manual_popup=true")
    pm = got.get("category", {}).get("product_mapping", [])
    expect(len(pm) == 1 and pm[0].get("keyword") == "honey" and pm[0].get("category") == "A1",
           "T10.product_mapping preserved")


def cleanup_shipments(token: str, sids: List[str]):
    for sid in sids:
        try:
            requests.delete(f"{BASE}/shipments/{sid}", headers=H(token), timeout=10)
        except Exception:
            pass


def restore_defaults(token: str):
    """Reset contact settings so we don't leave test state polluting the admin account."""
    payload = {
        "name_format": {
            "prefix_enabled": True,
            "prefix_position": "start",
            "name_type": "full",
            "product_placement": "after_name",
            "location": "city",
        },
        "field_mapping": {
            "address_target": "address",
            "product_target": "notes",
            "notes_include": {"order_id": False, "quantity": False, "payment_mode": False},
        },
        "category": {
            "categories": [],
            "default_category": "",
            "auto_assign": True,
            "manual_popup": False,
            "product_mapping": [],
        },
    }
    try:
        requests.put(f"{BASE}/me/contact-settings", headers=H(token), json=payload, timeout=10)
    except Exception:
        pass


def main():
    print("=== Phase-16 Contact Save Settings backend tests ===")
    print(f"BASE={BASE}")
    token = login(ADMIN_EMAIL, ADMIN_PASS)
    print("Logged in as admin.")

    test_get_default_shape(token)
    test_put_category_merge(token)
    test_build_one_default(token)
    test_build_one_override(token)
    test_put_and_build_firstname_noprefix(token)

    sids: List[str] = []
    courier_id = None
    try:
        sids, courier_id = test_build_vcf(token)
    except Exception as e:
        FAIL.append(("T6.run", str(e)))
        print(f"  FAIL  T6.run  --  {e}")
    try:
        if courier_id:
            test_build_vcf_no_phone(token, courier_id)
    except Exception as e:
        FAIL.append(("T7.run", str(e)))
        print(f"  FAIL  T7.run  --  {e}")

    test_build_one_empty(token)
    test_get_no_auth()
    test_persistence(token)

    if sids:
        cleanup_shipments(token, sids)
    restore_defaults(token)

    print("\n--- SUMMARY ---")
    print(f"PASS: {len(PASS)}")
    print(f"FAIL: {len(FAIL)}")
    for label, detail in FAIL:
        print(f"  x {label}  :: {detail}")
    sys.exit(0 if not FAIL else 1)


if __name__ == "__main__":
    main()
