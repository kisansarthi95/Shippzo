"""
Phase-3 server.py refactor regression test.

29 endpoints moved from server.py monolith into 3 new modular routers
(/app/backend/routers/{couriers,custom_fields,feature_flags}.py). Public
API surface is supposed to be 100% unchanged.

Verifies all 28 endpoints continue to work identically.
"""
from __future__ import annotations

import json
import os
import time
import traceback
from typing import Any, Dict, List, Optional

import requests

BASE = os.environ.get(
    "BACKEND_BASE",
    "https://logistics-hub-740.preview.emergentagent.com/api",
)

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASS = "Admin@12345"

_results: List[tuple] = []  # list[(label, ok, detail)]


def record(label: str, ok: bool, detail: str = "") -> None:
    _results.append((label, ok, detail))
    status = "PASS" if ok else "FAIL"
    extra = f" :: {detail}" if detail else ""
    print(f"[{status}] {label}{extra}")


def login(email: str, password: str) -> Dict[str, Any]:
    r = requests.post(
        f"{BASE}/auth/login",
        json={"email": email, "password": password},
        timeout=30,
    )
    r.raise_for_status()
    j = r.json()
    return j


def H(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# =====================================================================
#  Test runner
# =====================================================================

def main() -> int:
    print(f"== Phase-3 refactor regression test ==")
    print(f"BASE = {BASE}\n")

    # 1) login
    try:
        admin = login(ADMIN_EMAIL, ADMIN_PASS)
        token = admin["token"]
        record("auth/login admin", True, f"is_admin={admin.get('is_admin')}")
    except Exception as e:
        record("auth/login admin", False, repr(e))
        return _summary()

    headers = H(token)

    # ============================================================
    #  COURIERS (1-8)
    # ============================================================
    courier_id: Optional[str] = None
    try:
        # 1) GET /couriers
        r = requests.get(f"{BASE}/couriers", headers=headers, timeout=30)
        ok = r.status_code == 200 and isinstance(r.json(), list)
        record("1. GET /couriers", ok, f"status={r.status_code} count={len(r.json()) if ok else '-'}")

        # 2) GET /couriers/limits
        r = requests.get(f"{BASE}/couriers/limits", headers=headers, timeout=30)
        ok = r.status_code == 200
        if ok:
            j = r.json()
            keys_ok = all(k in j for k in ("plan", "is_admin", "limit", "current_count", "can_add", "is_unlimited"))
            ok = keys_ok
            detail = f"is_admin={j.get('is_admin')} limit={j.get('limit')} current_count={j.get('current_count')}"
        else:
            detail = f"status={r.status_code} body={r.text[:200]}"
        record("2. GET /couriers/limits", ok, detail)

        # 3) POST /couriers
        ts = int(time.time())
        payload = {
            "name": f"Test Courier {ts}",
            "series_prefix": "TST",
            "next_number": 1,
            "number_padding": 5,
            "contact_phone": "9999000011",
            "tracking_url_template": "https://example.com/track/{tracking_id}",
        }
        r = requests.post(f"{BASE}/couriers", headers=headers, json=payload, timeout=30)
        ok = r.status_code == 200
        if ok:
            courier = r.json()
            courier_id = courier.get("id")
            ok = bool(courier_id) and courier.get("name") == payload["name"]
            detail = f"id={courier_id} name={courier.get('name')}"
        else:
            detail = f"status={r.status_code} body={r.text[:300]}"
        record("3. POST /couriers", ok, detail)
        if not courier_id:
            print("Cannot continue couriers tests without courier_id")
            return _summary()

        # 4) PUT /couriers/{id}
        r = requests.put(
            f"{BASE}/couriers/{courier_id}",
            headers=headers,
            json={"contact_phone": "9999111122", "name": f"Test Courier {ts} Renamed"},
            timeout=30,
        )
        ok = r.status_code == 200
        if ok:
            j = r.json()
            ok = j.get("contact_phone") == "9999111122"
            detail = f"contact_phone={j.get('contact_phone')}"
        else:
            detail = f"status={r.status_code} body={r.text[:200]}"
        record("4. PUT /couriers/{id}", ok, detail)

        # 5) GET /couriers/{id}
        r = requests.get(f"{BASE}/couriers/{courier_id}", headers=headers, timeout=30)
        ok = r.status_code == 200 and r.json().get("id") == courier_id
        record("5. GET /couriers/{id}", ok, f"status={r.status_code}")

        # 6) GET /couriers/{id}/next-tracking
        r = requests.get(
            f"{BASE}/couriers/{courier_id}/next-tracking", headers=headers, timeout=30,
        )
        ok = r.status_code == 200
        if ok:
            j = r.json()
            ok = "tracking_id" in j and "next_number" in j
            detail = f"tracking_id={j.get('tracking_id')} next_number={j.get('next_number')}"
        else:
            detail = f"status={r.status_code} body={r.text[:200]}"
        record("6. GET /couriers/{id}/next-tracking", ok, detail)

        prev_next = r.json().get("next_number") if ok else None

        # 7) POST /couriers/{id}/consume-tracking
        r = requests.post(
            f"{BASE}/couriers/{courier_id}/consume-tracking", headers=headers, timeout=30,
        )
        ok = r.status_code == 200
        if ok:
            j = r.json()
            ok = "tracking_id" in j
            detail = f"tracking_id={j.get('tracking_id')}"
        else:
            detail = f"status={r.status_code} body={r.text[:200]}"
        record("7. POST /couriers/{id}/consume-tracking", ok, detail)

        # Verify next-tracking incremented
        if prev_next is not None:
            r2 = requests.get(
                f"{BASE}/couriers/{courier_id}/next-tracking", headers=headers, timeout=30,
            )
            if r2.status_code == 200:
                new_next = r2.json().get("next_number")
                if new_next == prev_next + 1:
                    print(f"   [OK] next_number incremented: {prev_next} -> {new_next}")
                else:
                    print(f"   [WARN] next_number not incremented: {prev_next} -> {new_next}")

        # ============================================================
        #  VARIANTS (9-13)
        # ============================================================
        variant_id: Optional[str] = None

        # 9) GET /couriers/{id}/variants
        r = requests.get(
            f"{BASE}/couriers/{courier_id}/variants", headers=headers, timeout=30,
        )
        ok = r.status_code == 200
        if ok:
            j = r.json()
            need = ("variants", "cap", "current_count", "package_types", "categories")
            ok = all(k in j for k in need)
            detail = (
                f"shape_ok={ok} cap={j.get('cap')} current_count={j.get('current_count')} "
                f"types={len(j.get('package_types',[]))} cats={len(j.get('categories',[]))}"
            )
        else:
            detail = f"status={r.status_code} body={r.text[:200]}"
        record("9. GET /couriers/{id}/variants", ok, detail)

        # 10) POST /couriers/{id}/variants
        v_payload = {
            "variant_name": f"Test Variant {ts}",
            "package_type": "Box",
            "category": "Apparel",
            "length_cm": 25,
            "width_cm": 18,
            "height_cm": 5,
            "weight_g": 250,
            "within_state_rate": 60,
            "outside_state_rate": 90,
            "active": True,
        }
        r = requests.post(
            f"{BASE}/couriers/{courier_id}/variants",
            headers=headers,
            json=v_payload,
            timeout=30,
        )
        ok = r.status_code == 200
        if ok:
            j = r.json()
            variant_id = j.get("id")
            ok = bool(variant_id) and j.get("variant_name") == v_payload["variant_name"]
            detail = f"id={variant_id} name={j.get('variant_name')} weight_g={j.get('weight_g')}"
        else:
            detail = f"status={r.status_code} body={r.text[:300]}"
        record("10. POST /couriers/{id}/variants", ok, detail)

        # 11) PUT variant
        if variant_id:
            r = requests.put(
                f"{BASE}/couriers/{courier_id}/variants/{variant_id}",
                headers=headers,
                json={"weight_g": 300, "within_state_rate": 70},
                timeout=30,
            )
            ok = r.status_code == 200
            if ok:
                j = r.json()
                ok = float(j.get("weight_g") or 0) == 300.0
                detail = f"weight_g={j.get('weight_g')} within={j.get('within_state_rate')}"
            else:
                detail = f"status={r.status_code} body={r.text[:200]}"
            record("11. PUT /couriers/{id}/variants/{vid}", ok, detail)

        # 13) GET /me/all-variants
        r = requests.get(f"{BASE}/me/all-variants", headers=headers, timeout=30)
        ok = r.status_code == 200
        if ok:
            j = r.json()
            need = ("variants", "by_courier", "package_types", "categories")
            ok = all(k in j for k in need)
            detail = f"variants={len(j.get('variants',[]))} couriers_with_variants={len(j.get('by_courier',{}))}"
        else:
            detail = f"status={r.status_code} body={r.text[:200]}"
        record("13. GET /me/all-variants", ok, detail)

        # 12) DELETE variant (cleanup)
        if variant_id:
            r = requests.delete(
                f"{BASE}/couriers/{courier_id}/variants/{variant_id}",
                headers=headers, timeout=30,
            )
            ok = r.status_code == 200
            record("12. DELETE /couriers/{id}/variants/{vid}", ok, f"status={r.status_code}")

    except Exception as e:
        record("Couriers/Variants block exception", False, repr(e))
        traceback.print_exc()

    # ============================================================
    #  CATEGORIES (14-16)
    # ============================================================
    test_cat = f"TestCat{int(time.time())}"
    try:
        # 14) GET /me/categories
        r = requests.get(f"{BASE}/me/categories", headers=headers, timeout=30)
        ok = r.status_code == 200
        if ok:
            j = r.json()
            ok = "presets" in j and "custom" in j
            detail = f"presets={len(j.get('presets',[]))} custom={len(j.get('custom',[]))}"
        else:
            detail = f"status={r.status_code} body={r.text[:200]}"
        record("14. GET /me/categories", ok, detail)

        # 15) POST /me/categories
        r = requests.post(
            f"{BASE}/me/categories",
            headers=headers,
            json={"name": test_cat},
            timeout=30,
        )
        ok = r.status_code == 200
        if ok:
            j = r.json()
            ok = test_cat in j.get("custom", [])
            detail = f"custom={j.get('custom',[])[:5]}"
        else:
            detail = f"status={r.status_code} body={r.text[:200]}"
        record("15. POST /me/categories", ok, detail)

        # 16) DELETE /me/categories/{name}
        r = requests.delete(
            f"{BASE}/me/categories/{test_cat}", headers=headers, timeout=30,
        )
        ok = r.status_code == 200
        if ok:
            j = r.json()
            ok = test_cat not in j.get("custom", [])
            detail = f"removed={ok}"
        else:
            detail = f"status={r.status_code} body={r.text[:200]}"
        record("16. DELETE /me/categories/{name}", ok, detail)
    except Exception as e:
        record("Categories block exception", False, repr(e))

    # ============================================================
    #  CUSTOM FIELDS (17-20)
    # ============================================================
    cf_id: Optional[str] = None
    try:
        # 17) GET /me/custom-fields
        r = requests.get(f"{BASE}/me/custom-fields", headers=headers, timeout=30)
        ok = r.status_code == 200
        if ok:
            j = r.json()
            need = ("fields", "limit", "used", "feature_enabled", "plan", "is_admin")
            ok = all(k in j for k in need)
            detail = (
                f"fields={len(j.get('fields',[]))} limit={j.get('limit')} "
                f"used={j.get('used')} feature_enabled={j.get('feature_enabled')} "
                f"plan={j.get('plan')} is_admin={j.get('is_admin')}"
            )
        else:
            detail = f"status={r.status_code} body={r.text[:200]}"
        record("17. GET /me/custom-fields", ok, detail)

        # 18) POST /me/custom-fields
        # Use unique column letter to avoid collision (Z is in use sometimes)
        # Try Z first; if dup or fails, try AA.
        col_letter = "Z"
        cf_payload = {
            "name": f"Test Field {int(time.time())}",
            "column_letter": col_letter,
            "field_type": "text",
        }
        r = requests.post(
            f"{BASE}/me/custom-fields", headers=headers, json=cf_payload, timeout=30,
        )
        if r.status_code == 400 and "already used" in r.text.lower():
            # try a random letter
            cf_payload["column_letter"] = "AB"
            r = requests.post(
                f"{BASE}/me/custom-fields", headers=headers, json=cf_payload, timeout=30,
            )
        ok = r.status_code == 200
        if ok:
            j = r.json()
            cf_id = j.get("id")
            ok = bool(cf_id) and j.get("column_letter") == cf_payload["column_letter"]
            detail = f"id={cf_id} column={j.get('column_letter')} name={j.get('name')}"
        else:
            detail = f"status={r.status_code} body={r.text[:300]}"
        record("18. POST /me/custom-fields", ok, detail)

        # 19) PUT /me/custom-fields/{id}
        if cf_id:
            r = requests.put(
                f"{BASE}/me/custom-fields/{cf_id}",
                headers=headers,
                json={"name": "Test Field RENAMED"},
                timeout=30,
            )
            ok = r.status_code == 200
            if ok:
                j = r.json()
                ok = j.get("name") == "Test Field RENAMED"
                detail = f"name={j.get('name')}"
            else:
                detail = f"status={r.status_code} body={r.text[:200]}"
            record("19. PUT /me/custom-fields/{id}", ok, detail)

        # 20) DELETE /me/custom-fields/{id}  (cleanup)
        if cf_id:
            r = requests.delete(
                f"{BASE}/me/custom-fields/{cf_id}", headers=headers, timeout=30,
            )
            ok = r.status_code == 200
            record("20. DELETE /me/custom-fields/{id}", ok, f"status={r.status_code}")
    except Exception as e:
        record("Custom Fields block exception", False, repr(e))
        traceback.print_exc()

    # ============================================================
    #  CONTACT SETTINGS + VCF (21-24)
    # ============================================================
    try:
        # 21) GET /me/contact-settings
        r = requests.get(f"{BASE}/me/contact-settings", headers=headers, timeout=30)
        ok = r.status_code == 200
        if ok:
            j = r.json()
            ok = isinstance(j, dict) and (
                "name_format" in j or "field_mapping" in j or "category" in j
            )
            detail = f"keys={list(j.keys())[:6]}"
        else:
            detail = f"status={r.status_code} body={r.text[:200]}"
        record("21. GET /me/contact-settings", ok, detail)

        # 22) PUT /me/contact-settings
        r = requests.put(
            f"{BASE}/me/contact-settings",
            headers=headers,
            json={"category": {"mode": "fixed", "value": "Customers"}},
            timeout=30,
        )
        ok = r.status_code == 200
        if ok:
            j = r.json()
            cat = j.get("category", {})
            ok = isinstance(cat, dict) and cat.get("value") == "Customers"
            detail = f"category={cat}"
        else:
            detail = f"status={r.status_code} body={r.text[:300]}"
        record("22. PUT /me/contact-settings", ok, detail)

        # 23) POST /contacts/build-one (inline shipment preview mode)
        r = requests.post(
            f"{BASE}/contacts/build-one",
            headers=headers,
            json={
                "shipment": {
                    "customer_name": "Aarav Patel",
                    "customer_phone": "9876543210",
                    "customer_city": "Ahmedabad",
                }
            },
            timeout=30,
        )
        ok = r.status_code == 200
        if ok:
            j = r.json()
            ok = isinstance(j, dict) and j.get("phone")
            detail = f"name={j.get('first_name') or j.get('full_name')} phone={j.get('phone')}"
        else:
            detail = f"status={r.status_code} body={r.text[:300]}"
        record("23. POST /contacts/build-one", ok, detail)

        # 24) POST /contacts/build-vcf - need an existing shipment id
        # find one from /shipments
        r = requests.get(f"{BASE}/shipments", headers=headers, timeout=30)
        ship_id: Optional[str] = None
        if r.status_code == 200:
            ships = r.json()
            for s in ships:
                if s.get("customer_phone"):
                    ship_id = s.get("id")
                    break
        if ship_id:
            r = requests.post(
                f"{BASE}/contacts/build-vcf",
                headers=headers,
                json={"shipment_ids": [ship_id]},
                timeout=30,
            )
            ok = r.status_code == 200
            if ok:
                j = r.json()
                ok = "vcf" in j and j.get("count", 0) >= 1
                detail = f"count={j.get('count')} skipped={j.get('skipped')} vcf_len={len(j.get('vcf',''))}"
            else:
                detail = f"status={r.status_code} body={r.text[:300]}"
            record("24. POST /contacts/build-vcf", ok, detail)
        else:
            record("24. POST /contacts/build-vcf", False, "No shipment with phone available to test")
    except Exception as e:
        record("Contact-settings block exception", False, repr(e))
        traceback.print_exc()

    # ============================================================
    #  ADMIN custom-field-limits (25-26)
    # ============================================================
    try:
        # Snapshot current limits to restore later
        r = requests.get(f"{BASE}/admin/custom-field-limits", headers=headers, timeout=30)
        prev_limits = None
        ok = r.status_code == 200
        if ok:
            j = r.json()
            ok = "limits" in j and "defaults" in j
            prev_limits = j.get("limits") or j.get("defaults")
            detail = f"limits={j.get('limits')} defaults={j.get('defaults')}"
        else:
            detail = f"status={r.status_code} body={r.text[:200]}"
        record("25. GET /admin/custom-field-limits", ok, detail)

        # 26) PUT - set then verify, then restore.
        new_limits = {"free_trial": 1, "silver": 5, "gold": 10, "platinum": 20}
        r = requests.put(
            f"{BASE}/admin/custom-field-limits",
            headers=headers,
            json=new_limits,
            timeout=30,
        )
        ok = r.status_code == 200
        if ok:
            j = r.json()
            persisted = j.get("limits") or {}
            ok = all(persisted.get(k) == v for k, v in new_limits.items())
            detail = f"persisted={persisted}"
        else:
            detail = f"status={r.status_code} body={r.text[:300]}"
        record("26. PUT /admin/custom-field-limits", ok, detail)

        # restore
        if prev_limits:
            try:
                requests.put(
                    f"{BASE}/admin/custom-field-limits",
                    headers=headers,
                    json=prev_limits,
                    timeout=30,
                )
            except Exception:
                pass
    except Exception as e:
        record("Admin custom-field-limits block exception", False, repr(e))

    # ============================================================
    #  FEATURE FLAGS (27-28)
    # ============================================================
    try:
        r = requests.get(f"{BASE}/me/feature-flags", headers=headers, timeout=30)
        ok = r.status_code == 200
        if ok:
            j = r.json()
            ok = (
                "plan" in j
                and isinstance(j.get("features"), list)
                and "is_admin" in j
            )
            detail = f"plan={j.get('plan')} feature_count={len(j.get('features',[]))} is_admin={j.get('is_admin')}"
        else:
            detail = f"status={r.status_code} body={r.text[:200]}"
        record("27. GET /me/feature-flags", ok, detail)

        r = requests.get(f"{BASE}/me/feature-registry", headers=headers, timeout=30)
        ok = r.status_code == 200
        if ok:
            j = r.json()
            ok = "registry" in j and "my_features" in j and "plan" in j
            reg = j.get("registry")
            detail = f"plan={j.get('plan')} my_features={len(j.get('my_features',[]))} registry_type={type(reg).__name__}"
        else:
            detail = f"status={r.status_code} body={r.text[:300]}"
        record("28. GET /me/feature-registry", ok, detail)
    except Exception as e:
        record("Feature flags block exception", False, repr(e))

    # ============================================================
    #  DELETE COURIER (cleanup) — endpoint 8
    # ============================================================
    if courier_id:
        try:
            r = requests.delete(
                f"{BASE}/couriers/{courier_id}", headers=headers, timeout=30,
            )
            ok = r.status_code == 200
            record("8. DELETE /couriers/{id} (cleanup)", ok, f"status={r.status_code}")
        except Exception as e:
            record("8. DELETE /couriers/{id}", False, repr(e))

    return _summary()


def _summary() -> int:
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    passed = sum(1 for _, ok, _ in _results if ok)
    failed = sum(1 for _, ok, _ in _results if not ok)
    for label, ok, detail in _results:
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {label}" + (f" :: {detail}" if detail else ""))
    print("-" * 60)
    print(f"  TOTAL: {passed} passed, {failed} failed (out of {len(_results)})")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
