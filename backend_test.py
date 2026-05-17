"""
Phase-24 Field-Control System backend tests.

Targets:
  GET   /api/field-configs/modules
  GET   /api/field-configs/{module}
  GET   /api/admin/field-configs/{module}
  PATCH /api/admin/field-configs/{module}/{field_key}

Uses live preview backend per frontend/.env.
"""
from __future__ import annotations

import json
import sys
import time
from typing import Any, Dict, Optional

import requests

BASE = "https://logistics-hub-740.preview.emergentagent.com/api"
ADMIN_CREDS = {"email": "admin@test.com", "password": "Admin@12345"}
USER_CREDS = {"email": "user2@test.com", "password": "User@12345"}

LOCKED_EXPECTED = {
    "customer_name", "customer_phone", "address",
    "city", "state", "pincode", "order_id", "amount",
}

CONFIGURABLE_EXPECTED = {
    "tracking_id", "courier_id", "customer_alt_phone", "items",
    "item_description", "weight", "payment_mode", "eta_days",
    "sender_address_id", "notes",
}

results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    icon = "✅" if ok else "❌"
    print(f"{icon} {name}{(' — ' + detail) if detail else ''}")


def login(creds: Dict[str, str]) -> str:
    r = requests.post(f"{BASE}/auth/login", json=creds, timeout=20)
    r.raise_for_status()
    return r.json()["token"]


def bearer(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def get_cfg_map(payload: Dict[str, Any], section: str) -> Dict[str, Dict]:
    return {f["field_key"]: f for f in payload.get(section, [])}


def main() -> int:
    print(f"BASE = {BASE}")
    try:
        admin_t = login(ADMIN_CREDS)
        user_t = login(USER_CREDS)
        record("login admin + user2", True)
    except Exception as exc:
        record("login admin + user2", False, str(exc))
        return 1

    # ─────────────────────────────────────────────────────────────
    # SCENARIO A — read paths
    # ─────────────────────────────────────────────────────────────
    print("\n--- Scenario A — read paths ---")

    # A.1 GET /api/field-configs/new_shipment as user2 → 200
    r = requests.get(f"{BASE}/field-configs/new_shipment",
                     headers=bearer(user_t), timeout=15)
    record("A.1 GET /field-configs/new_shipment (user2) returns 200",
           r.status_code == 200, f"status={r.status_code}")
    if r.status_code == 200:
        payload = r.json()
        locked = payload.get("locked", [])
        configurable = payload.get("configurable", [])

        record("A.1 locked array has 8 entries",
               len(locked) == 8, f"got {len(locked)}")
        locked_keys = {f["field_key"] for f in locked}
        record("A.1 locked keys match expected set",
               locked_keys == LOCKED_EXPECTED,
               f"diff={LOCKED_EXPECTED ^ locked_keys}")
        record("A.1 every locked entry has locked=True, enabled=True, required=True",
               all(f.get("locked") is True and f.get("enabled") is True
                   and f.get("required") is True for f in locked))

        record("A.1 configurable array has 10 entries",
               len(configurable) == 10, f"got {len(configurable)}")
        cfg_keys = {f["field_key"] for f in configurable}
        record("A.1 configurable keys match expected set",
               cfg_keys == CONFIGURABLE_EXPECTED,
               f"diff={CONFIGURABLE_EXPECTED ^ cfg_keys}")

    # A.2 GET /api/field-configs/modules
    r = requests.get(f"{BASE}/field-configs/modules",
                     headers=bearer(user_t), timeout=15)
    record("A.2 GET /field-configs/modules returns 200",
           r.status_code == 200, f"status={r.status_code}")
    if r.status_code == 200:
        mods = r.json().get("modules", [])
        record("A.2 modules list contains 'new_shipment'",
               "new_shipment" in mods, f"modules={mods}")

    # A.3 GET unknown module → 404
    r = requests.get(f"{BASE}/field-configs/bogus_module",
                     headers=bearer(user_t), timeout=15)
    record("A.3 GET /field-configs/bogus_module returns 404",
           r.status_code == 404, f"status={r.status_code}")

    # ─────────────────────────────────────────────────────────────
    # SCENARIO B — admin happy-path PATCH
    # ─────────────────────────────────────────────────────────────
    print("\n--- Scenario B — admin happy-path PATCH ---")

    # B.1 PATCH tracking_id {required: true}
    r = requests.patch(f"{BASE}/admin/field-configs/new_shipment/tracking_id",
                       headers=bearer(admin_t),
                       json={"required": True}, timeout=15)
    record("B.1 PATCH tracking_id {required:true} returns 200",
           r.status_code == 200, f"status={r.status_code} body={r.text[:160]}")
    if r.status_code == 200:
        cfg = get_cfg_map(r.json(), "configurable")
        record("B.1 configurable[tracking_id].required === true",
               cfg.get("tracking_id", {}).get("required") is True,
               f"got={cfg.get('tracking_id')}")

    # B.2 PATCH tracking_id {required: false}
    r = requests.patch(f"{BASE}/admin/field-configs/new_shipment/tracking_id",
                       headers=bearer(admin_t),
                       json={"required": False}, timeout=15)
    record("B.2 PATCH tracking_id {required:false} returns 200",
           r.status_code == 200, f"status={r.status_code}")
    if r.status_code == 200:
        cfg = get_cfg_map(r.json(), "configurable")
        record("B.2 tracking_id.required === false",
               cfg.get("tracking_id", {}).get("required") is False)

    # B.3 PATCH notes {enabled:true, required:true}
    r = requests.patch(f"{BASE}/admin/field-configs/new_shipment/notes",
                       headers=bearer(admin_t),
                       json={"enabled": True, "required": True}, timeout=15)
    record("B.3 PATCH notes {enabled:true,required:true} returns 200",
           r.status_code == 200, f"status={r.status_code}")
    if r.status_code == 200:
        cfg = get_cfg_map(r.json(), "configurable")
        record("B.3 notes shows enabled=True, required=True",
               cfg.get("notes", {}).get("enabled") is True
               and cfg.get("notes", {}).get("required") is True,
               f"got={cfg.get('notes')}")

    # B.4 GET as user2 reflects the changes (global config)
    r = requests.get(f"{BASE}/field-configs/new_shipment",
                     headers=bearer(user_t), timeout=15)
    if r.status_code == 200:
        cfg = get_cfg_map(r.json(), "configurable")
        record("B.4 user2 sees tracking_id.required=False after PATCH",
               cfg.get("tracking_id", {}).get("required") is False)
        record("B.4 user2 sees notes.enabled=True, required=True",
               cfg.get("notes", {}).get("enabled") is True
               and cfg.get("notes", {}).get("required") is True)
    else:
        record("B.4 GET as user2", False, f"status={r.status_code}")

    # ─────────────────────────────────────────────────────────────
    # SCENARIO C — locked-field defence
    # ─────────────────────────────────────────────────────────────
    print("\n--- Scenario C — locked-field defence ---")

    # C.1 PATCH address with payload
    r = requests.patch(f"{BASE}/admin/field-configs/new_shipment/address",
                       headers=bearer(admin_t),
                       json={"required": False}, timeout=15)
    ok = r.status_code == 400 and "locked" in (r.text or "").lower()
    record("C.1 PATCH address → 400 with detail containing 'locked'",
           ok, f"status={r.status_code} body={r.text[:160]}")

    # C.2 PATCH amount {enabled:false}
    r = requests.patch(f"{BASE}/admin/field-configs/new_shipment/amount",
                       headers=bearer(admin_t),
                       json={"enabled": False}, timeout=15)
    ok = r.status_code == 400 and "locked" in (r.text or "").lower()
    record("C.2 PATCH amount {enabled:false} → 400 (locked)",
           ok, f"status={r.status_code} body={r.text[:160]}")

    # C.3 PATCH customer_name {required:false}
    r = requests.patch(f"{BASE}/admin/field-configs/new_shipment/customer_name",
                       headers=bearer(admin_t),
                       json={"required": False}, timeout=15)
    ok = r.status_code == 400 and "locked" in (r.text or "").lower()
    record("C.3 PATCH customer_name {required:false} → 400 (locked)",
           ok, f"status={r.status_code} body={r.text[:160]}")

    # ─────────────────────────────────────────────────────────────
    # SCENARIO D — auth / admin guard
    # ─────────────────────────────────────────────────────────────
    print("\n--- Scenario D — auth/admin guard ---")

    # D.1 PATCH as regular user → 403
    r = requests.patch(f"{BASE}/admin/field-configs/new_shipment/tracking_id",
                       headers=bearer(user_t),
                       json={"required": True}, timeout=15)
    record("D.1 PATCH as user2 → 403",
           r.status_code == 403, f"status={r.status_code} body={r.text[:160]}")

    # D.2 GET /admin/field-configs/new_shipment as user2 → 403
    r = requests.get(f"{BASE}/admin/field-configs/new_shipment",
                     headers=bearer(user_t), timeout=15)
    record("D.2 GET /admin/field-configs/new_shipment as user2 → 403",
           r.status_code == 403, f"status={r.status_code}")

    # D.3 GET /field-configs/new_shipment WITHOUT bearer → 401
    r = requests.get(f"{BASE}/field-configs/new_shipment", timeout=15)
    record("D.3 GET /field-configs/new_shipment without Bearer → 401",
           r.status_code in (401, 403),
           f"status={r.status_code} (accepting 401 or 403 since FastAPI may emit either)")

    # D.4 PATCH on non-existent module → 404
    r = requests.patch(f"{BASE}/admin/field-configs/foo/tracking_id",
                       headers=bearer(admin_t),
                       json={"required": True}, timeout=15)
    record("D.4 PATCH unknown module → 404",
           r.status_code == 404, f"status={r.status_code} body={r.text[:160]}")

    # ─────────────────────────────────────────────────────────────
    # SCENARIO E — bad inputs
    # ─────────────────────────────────────────────────────────────
    print("\n--- Scenario E — bad inputs ---")

    # E.1 PATCH with empty body
    r = requests.patch(f"{BASE}/admin/field-configs/new_shipment/tracking_id",
                       headers=bearer(admin_t),
                       json={}, timeout=15)
    ok = r.status_code == 422 and "nothing_to_update" in (r.text or "")
    record("E.1 PATCH with {} → 422 with detail 'nothing_to_update'",
           ok, f"status={r.status_code} body={r.text[:160]}")

    # E.2 PATCH on unregistered field_key → upserts in DB but read does not surface it
    r = requests.patch(f"{BASE}/admin/field-configs/new_shipment/blah",
                       headers=bearer(admin_t),
                       json={"required": True}, timeout=15)
    record("E.2 PATCH unregistered field_key 'blah' returns 200",
           r.status_code == 200, f"status={r.status_code} body={r.text[:160]}")

    r = requests.get(f"{BASE}/field-configs/new_shipment",
                     headers=bearer(user_t), timeout=15)
    if r.status_code == 200:
        payload = r.json()
        cfg_keys = {f["field_key"] for f in payload.get("configurable", [])}
        locked_keys = {f["field_key"] for f in payload.get("locked", [])}
        record("E.2 'blah' does NOT appear in configurable or locked",
               "blah" not in cfg_keys and "blah" not in locked_keys,
               f"cfg_keys has blah? {'blah' in cfg_keys}")
        record("E.2 configurable still has exactly 10 registered keys",
               cfg_keys == CONFIGURABLE_EXPECTED,
               f"got {len(cfg_keys)} keys")

    # ─────────────────────────────────────────────────────────────
    # CLEANUP — leave tracking_id required=false
    # ─────────────────────────────────────────────────────────────
    print("\n--- Cleanup ---")
    r = requests.patch(f"{BASE}/admin/field-configs/new_shipment/tracking_id",
                       headers=bearer(admin_t),
                       json={"required": False}, timeout=15)
    record("Cleanup: tracking_id.required reset to false",
           r.status_code == 200, f"status={r.status_code}")
    # Reset notes to defaults (enabled=False, required=False)
    r = requests.patch(f"{BASE}/admin/field-configs/new_shipment/notes",
                       headers=bearer(admin_t),
                       json={"enabled": False, "required": False}, timeout=15)
    record("Cleanup: notes reset to enabled=false, required=false",
           r.status_code == 200, f"status={r.status_code}")

    # Summary
    print("\n" + "=" * 60)
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"RESULT: {passed}/{total} assertions passed")
    for name, ok, detail in results:
        if not ok:
            print(f"  FAIL — {name}: {detail}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
