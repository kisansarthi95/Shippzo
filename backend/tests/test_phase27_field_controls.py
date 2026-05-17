"""
Phase-27 backend test — Per-tenant Field-Control System.

Verifies the refactor from the global super-admin endpoints into
per-user endpoints, gated by the `field_controls` feature flag.

Run:  python /app/backend_test.py
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any, Dict, List, Optional

import requests

BASE_URL = "https://logistics-hub-740.preview.emergentagent.com/api"

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASS  = "Admin@12345"
USER2_EMAIL = "user2@test.com"
USER2_PASS  = "User@12345"

LOCKED_KEYS = {
    "customer_name", "customer_phone", "address", "city", "state",
    "pincode", "order_id", "amount",
}
CONFIG_KEYS = {
    "tracking_id", "courier_id", "customer_alt_phone", "items",
    "item_description", "weight", "payment_mode", "eta_days",
    "sender_address_id", "notes",
}

PASS: List[str] = []
FAIL: List[str] = []


def ok(name: str, cond: bool, extra: str = "") -> bool:
    """Record an assertion."""
    if cond:
        PASS.append(name)
        print(f"  ✅ {name}")
    else:
        FAIL.append(f"{name}  {extra}")
        print(f"  ❌ {name}  {extra}")
    return cond


def login(email: str, password: str) -> str:
    r = requests.post(
        f"{BASE_URL}/auth/login",
        json={"email": email, "password": password},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["token"]


def hdr(tok: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}


# ─────────────────────────────────────────────────────────────────────
def scenario_a(user2_tok: str):
    """Read paths work for everyone (no feature flag)."""
    print("\n=== SCENARIO A — Read paths ===")

    # A1 — GET /api/field-configs/new_shipment as user2
    r = requests.get(f"{BASE_URL}/field-configs/new_shipment", headers=hdr(user2_tok), timeout=15)
    ok("A1.status_200", r.status_code == 200, f"status={r.status_code} body={r.text[:200]}")
    if r.status_code == 200:
        body = r.json()
        ok("A1.has_locked_array", isinstance(body.get("locked"), list))
        ok("A1.has_configurable_array", isinstance(body.get("configurable"), list))
        ok("A1.locked_count_eq_8", len(body["locked"]) == 8, f"got {len(body['locked'])}")
        ok("A1.configurable_count_eq_10", len(body["configurable"]) == 10, f"got {len(body['configurable'])}")
        lk = {e["field_key"] for e in body["locked"]}
        ck = {e["field_key"] for e in body["configurable"]}
        ok("A1.locked_keys_match", lk == LOCKED_KEYS, f"diff={lk ^ LOCKED_KEYS}")
        ok("A1.configurable_keys_match", ck == CONFIG_KEYS, f"diff={ck ^ CONFIG_KEYS}")
        all_locked_props = all(
            e.get("locked") is True and e.get("enabled") is True and e.get("required") is True
            for e in body["locked"]
        )
        ok("A1.locked_props_locked_enabled_required_True", all_locked_props)

    # A2 — GET /api/me/field-configs/new_shipment as user2 — includes locked_keys
    r = requests.get(f"{BASE_URL}/me/field-configs/new_shipment", headers=hdr(user2_tok), timeout=15)
    ok("A2.status_200", r.status_code == 200, f"status={r.status_code}")
    if r.status_code == 200:
        body = r.json()
        ok("A2.has_locked_keys_array", isinstance(body.get("locked_keys"), list))
        ok("A2.locked_keys_len_8", len(body.get("locked_keys", [])) == 8)
        ok("A2.locked_keys_match", set(body.get("locked_keys", [])) == LOCKED_KEYS)
        ok("A2.has_locked_array", isinstance(body.get("locked"), list) and len(body["locked"]) == 8)
        ok("A2.has_configurable_array", isinstance(body.get("configurable"), list) and len(body["configurable"]) == 10)

    # A3 — GET /api/field-configs/modules as user2
    r = requests.get(f"{BASE_URL}/field-configs/modules", headers=hdr(user2_tok), timeout=15)
    ok("A3.status_200", r.status_code == 200)
    if r.status_code == 200:
        ok("A3.has_modules", isinstance(r.json().get("modules"), list) and "new_shipment" in r.json()["modules"])

    # A4 — GET unknown module
    r = requests.get(f"{BASE_URL}/field-configs/bogus_module", headers=hdr(user2_tok), timeout=15)
    ok("A4.status_404", r.status_code == 404, f"status={r.status_code}")


# ─────────────────────────────────────────────────────────────────────
def scenario_b(user2_tok: str, admin_tok: str):
    """PATCH gated by feature flag."""
    print("\n=== SCENARIO B — PATCH gated by feature flag ===")

    # B1 — user2 (free_trial, no field_controls) → 403
    r = requests.patch(
        f"{BASE_URL}/me/field-configs/new_shipment/tracking_id",
        headers=hdr(user2_tok), data=json.dumps({"required": True}), timeout=15,
    )
    ok("B1.status_403", r.status_code == 403, f"status={r.status_code} body={r.text[:200]}")
    if r.status_code == 403:
        detail = (r.json().get("detail") or "").lower()
        ok("B1.detail_mentions_field_controls", "field_controls" in detail or "plan does not include" in detail,
           f"detail={detail}")

    # B2 — admin → 200, required=True
    r = requests.patch(
        f"{BASE_URL}/me/field-configs/new_shipment/tracking_id",
        headers=hdr(admin_tok), data=json.dumps({"required": True}), timeout=15,
    )
    ok("B2.status_200", r.status_code == 200, f"status={r.status_code} body={r.text[:200]}")
    if r.status_code == 200:
        cfg = r.json()
        tr = next((e for e in cfg["configurable"] if e["field_key"] == "tracking_id"), None)
        ok("B2.tracking_id_required_True", tr and tr["required"] is True, f"tr={tr}")

    # B2b — read-back as admin
    r = requests.get(f"{BASE_URL}/field-configs/new_shipment", headers=hdr(admin_tok), timeout=15)
    if r.status_code == 200:
        tr = next((e for e in r.json()["configurable"] if e["field_key"] == "tracking_id"), None)
        ok("B2b.readback_admin_tracking_id_required_True", tr and tr["required"] is True)

    # B3 — admin sets required=False
    r = requests.patch(
        f"{BASE_URL}/me/field-configs/new_shipment/tracking_id",
        headers=hdr(admin_tok), data=json.dumps({"required": False}), timeout=15,
    )
    ok("B3.status_200", r.status_code == 200, f"status={r.status_code}")
    if r.status_code == 200:
        tr = next((e for e in r.json()["configurable"] if e["field_key"] == "tracking_id"), None)
        ok("B3.tracking_id_required_False", tr and tr["required"] is False)


# ─────────────────────────────────────────────────────────────────────
def scenario_c(user2_tok: str, admin_tok: str):
    """Per-user isolation."""
    print("\n=== SCENARIO C — Per-user isolation ===")

    # C1 — admin PATCH notes {enabled:true, required:true}
    r = requests.patch(
        f"{BASE_URL}/me/field-configs/new_shipment/notes",
        headers=hdr(admin_tok), data=json.dumps({"enabled": True, "required": True}), timeout=15,
    )
    ok("C1.admin_PATCH_notes_status_200", r.status_code == 200, f"status={r.status_code}")

    # C2 — user2 GET — notes should STILL be at default (enabled=False, required=False)
    r = requests.get(f"{BASE_URL}/field-configs/new_shipment", headers=hdr(user2_tok), timeout=15)
    if r.status_code == 200:
        n = next((e for e in r.json()["configurable"] if e["field_key"] == "notes"), None)
        ok("C2.user2_notes_enabled_default_False", n and n["enabled"] is False, f"n={n}")
        ok("C2.user2_notes_required_default_False", n and n["required"] is False, f"n={n}")

    # C3 — admin GET — notes shows enabled=True, required=True
    r = requests.get(f"{BASE_URL}/field-configs/new_shipment", headers=hdr(admin_tok), timeout=15)
    if r.status_code == 200:
        n = next((e for e in r.json()["configurable"] if e["field_key"] == "notes"), None)
        ok("C3.admin_notes_enabled_True", n and n["enabled"] is True, f"n={n}")
        ok("C3.admin_notes_required_True", n and n["required"] is True, f"n={n}")


# ─────────────────────────────────────────────────────────────────────
def scenario_d(admin_tok: str):
    """Locked-field defence."""
    print("\n=== SCENARIO D — Locked-field defence ===")

    for fk in ("address", "amount"):
        r = requests.patch(
            f"{BASE_URL}/me/field-configs/new_shipment/{fk}",
            headers=hdr(admin_tok), data=json.dumps({"required": False}), timeout=15,
        )
        ok(f"D.{fk}_PATCH_status_400", r.status_code == 400, f"status={r.status_code} body={r.text[:200]}")
        if r.status_code == 400:
            detail = (r.json().get("detail") or "").lower()
            ok(f"D.{fk}_detail_mentions_locked", "locked" in detail, f"detail={detail}")


# ─────────────────────────────────────────────────────────────────────
def scenario_e():
    """Auth / 401."""
    print("\n=== SCENARIO E — Auth/401 ===")

    r = requests.get(f"{BASE_URL}/field-configs/new_shipment", timeout=15)
    ok("E1.GET_no_bearer_401_or_403", r.status_code in (401, 403), f"status={r.status_code}")

    r = requests.patch(
        f"{BASE_URL}/me/field-configs/new_shipment/tracking_id",
        headers={"Content-Type": "application/json"},
        data=json.dumps({"required": True}), timeout=15,
    )
    ok("E2.PATCH_no_bearer_401_or_403", r.status_code in (401, 403), f"status={r.status_code}")


# ─────────────────────────────────────────────────────────────────────
def scenario_f(admin_tok: str):
    """Legacy admin endpoints removed."""
    print("\n=== SCENARIO F — Legacy admin endpoints removed ===")

    r = requests.get(f"{BASE_URL}/admin/field-configs/new_shipment", headers=hdr(admin_tok), timeout=15)
    ok("F1.legacy_GET_404_or_405", r.status_code in (404, 405), f"status={r.status_code} body={r.text[:200]}")

    r = requests.patch(
        f"{BASE_URL}/admin/field-configs/new_shipment/tracking_id",
        headers=hdr(admin_tok), data=json.dumps({"required": True}), timeout=15,
    )
    ok("F2.legacy_PATCH_404_or_405", r.status_code in (404, 405), f"status={r.status_code} body={r.text[:200]}")


# ─────────────────────────────────────────────────────────────────────
def scenario_g(admin_tok: str):
    """Bad inputs."""
    print("\n=== SCENARIO G — Bad inputs ===")

    # G1 — empty body
    r = requests.patch(
        f"{BASE_URL}/me/field-configs/new_shipment/tracking_id",
        headers=hdr(admin_tok), data=json.dumps({}), timeout=15,
    )
    ok("G1.empty_body_422", r.status_code == 422, f"status={r.status_code} body={r.text[:200]}")

    # G2 — unknown field_key
    r = requests.patch(
        f"{BASE_URL}/me/field-configs/new_shipment/blah",
        headers=hdr(admin_tok), data=json.dumps({"required": True}), timeout=15,
    )
    ok("G2.unknown_field_200", r.status_code == 200, f"status={r.status_code} body={r.text[:200]}")

    # G2b — GET should NOT include 'blah' in configurable
    r = requests.get(f"{BASE_URL}/field-configs/new_shipment", headers=hdr(admin_tok), timeout=15)
    if r.status_code == 200:
        body = r.json()
        all_ck = {e["field_key"] for e in body["configurable"]}
        all_lk = {e["field_key"] for e in body["locked"]}
        ok("G2b.blah_absent_from_configurable", "blah" not in all_ck, f"ck={all_ck}")
        ok("G2b.blah_absent_from_locked", "blah" not in all_lk)
        ok("G2b.configurable_count_still_10", len(body["configurable"]) == 10, f"got {len(body['configurable'])}")


# ─────────────────────────────────────────────────────────────────────
def cleanup(admin_tok: str):
    """Reset admin's per-user notes back to default."""
    print("\n=== CLEANUP ===")
    r = requests.patch(
        f"{BASE_URL}/me/field-configs/new_shipment/notes",
        headers=hdr(admin_tok), data=json.dumps({"enabled": False, "required": False}), timeout=15,
    )
    ok("cleanup.notes_reset_200", r.status_code == 200, f"status={r.status_code}")
    if r.status_code == 200:
        n = next((e for e in r.json()["configurable"] if e["field_key"] == "notes"), None)
        ok("cleanup.notes_enabled_False", n and n["enabled"] is False)
        ok("cleanup.notes_required_False", n and n["required"] is False)
    # Reset tracking_id required=False (in case B left it True)
    requests.patch(
        f"{BASE_URL}/me/field-configs/new_shipment/tracking_id",
        headers=hdr(admin_tok), data=json.dumps({"required": False}), timeout=15,
    )


# ─────────────────────────────────────────────────────────────────────
def main():
    print(f"Running Phase-27 field-control tests against {BASE_URL}")
    print(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    try:
        admin_tok = login(ADMIN_EMAIL, ADMIN_PASS)
        user2_tok = login(USER2_EMAIL, USER2_PASS)
        ok("login.admin", bool(admin_tok))
        ok("login.user2", bool(user2_tok))
    except Exception as e:
        print(f"❌ Login failed: {e}")
        sys.exit(2)

    scenario_a(user2_tok)
    scenario_b(user2_tok, admin_tok)
    scenario_c(user2_tok, admin_tok)
    scenario_d(admin_tok)
    scenario_e()
    scenario_f(admin_tok)
    scenario_g(admin_tok)
    cleanup(admin_tok)

    print("\n" + "=" * 60)
    print(f"PASSED: {len(PASS)}    FAILED: {len(FAIL)}")
    if FAIL:
        print("\nFailures:")
        for f in FAIL:
            print(f"  • {f}")
        sys.exit(1)
    print("All assertions passed.")


if __name__ == "__main__":
    main()
