"""
WhatsApp Manual Messaging Pricing — Admin Endpoints Test (Phase-14)

Covers:
  GET    /api/admin/whatsapp-pricing
  PUT    /api/admin/whatsapp-pricing
  GET    /api/me/whatsapp-pricing

Target: https://logistics-hub-740.preview.emergentagent.com/api
Creds:  admin@test.com / Admin@12345 (admin)
        user2@test.com / User@12345 (regular user)
"""
import os
import sys
import json
from typing import Any, Dict, List, Tuple

import requests

BASE = os.environ.get(
    "BACKEND_URL",
    "https://logistics-hub-740.preview.emergentagent.com",
).rstrip("/") + "/api"

ADMIN_EMAIL = "admin@test.com"
ADMIN_PWD   = "Admin@12345"
USER_EMAIL  = "user2@test.com"
USER_PWD    = "User@12345"

EXPECTED_ORDER = ["free_trial", "silver", "gold", "platinum"]

PASS: List[str] = []
FAIL: List[str] = []


def rec(ok: bool, label: str, detail: str = "") -> None:
    if ok:
        PASS.append(label)
        print(f"  ✅ {label}")
    else:
        FAIL.append(f"{label} :: {detail}")
        print(f"  ❌ {label}  —  {detail}")


def login(email: str, pwd: str) -> str:
    r = requests.post(
        f"{BASE}/auth/login",
        json={"email": email, "password": pwd},
        timeout=30,
    )
    r.raise_for_status()
    tok = r.json().get("token")
    if not tok:
        raise RuntimeError(f"Login for {email} returned no token: {r.text}")
    return tok


def auth_hdr(tok: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {tok}"}


def assert_status(resp: requests.Response, expected: int, label: str) -> bool:
    ok = resp.status_code == expected
    rec(ok, label, f"got {resp.status_code} body={resp.text[:180]}")
    return ok


# ---------------------------------------------------------------------------
def test_case1_admin_get_baseline(admin_tok: str) -> None:
    print("\n=== CASE 1 — GET /admin/whatsapp-pricing (admin) baseline ===")
    r = requests.get(
        f"{BASE}/admin/whatsapp-pricing",
        headers=auth_hdr(admin_tok),
        timeout=30,
    )
    if not assert_status(r, 200, "GET /admin/whatsapp-pricing 200"):
        return
    data = r.json()
    rec(
        list(data.get("order", [])) == EXPECTED_ORDER,
        "order == [free_trial,silver,gold,platinum]",
        f"got {data.get('order')}",
    )
    defaults = data.get("defaults", {})
    current = data.get("current", {})
    rec(defaults.get("enabled") is False,
        "defaults.enabled == false",
        f"got {defaults.get('enabled')}")
    rec(current.get("enabled") is False,
        "current.enabled initially false (or after reset)",
        f"got {current.get('enabled')}")

    for plan in EXPECTED_ORDER:
        rec(plan in defaults.get("rates", {}),
            f"defaults.rates contains '{plan}'",
            f"rates={defaults.get('rates')}")
        rec(float(defaults.get("rates", {}).get(plan, -1)) == 0.0,
            f"defaults.rates.{plan} == 0.0",
            f"got {defaults.get('rates', {}).get(plan)}")
        rec(plan in current.get("rates", {}),
            f"current.rates contains '{plan}'",
            f"rates={current.get('rates')}")


def test_case2_non_admin_forbidden(user_tok: str) -> None:
    print("\n=== CASE 2 — GET /admin/whatsapp-pricing (non-admin) → 403 ===")
    r = requests.get(
        f"{BASE}/admin/whatsapp-pricing",
        headers=auth_hdr(user_tok),
        timeout=30,
    )
    assert_status(r, 403, "non-admin GET returns 403")


def test_case3_admin_put_enable(admin_tok: str) -> None:
    print("\n=== CASE 3 — PUT enable + set silver=0.5 gold=1 platinum=1.5 ===")
    payload = {
        "enabled": True,
        "plans": {
            "silver":   {"per_message_credits": 0.5},
            "gold":     {"per_message_credits": 1},
            "platinum": {"per_message_credits": 1.5},
        },
    }
    r = requests.put(
        f"{BASE}/admin/whatsapp-pricing",
        headers=auth_hdr(admin_tok),
        json=payload,
        timeout=30,
    )
    if not assert_status(r, 200, "PUT enable+rates returns 200"):
        return
    data = r.json()
    cur = data.get("current", {})
    rec(cur.get("enabled") is True,
        "current.enabled == true after PUT",
        f"got {cur.get('enabled')}")
    rec(float(cur.get("rates", {}).get("silver", -1)) == 0.5,
        "current.rates.silver == 0.5",
        f"got {cur.get('rates', {}).get('silver')}")
    rec(float(cur.get("rates", {}).get("gold", -1)) == 1.0,
        "current.rates.gold == 1.0",
        f"got {cur.get('rates', {}).get('gold')}")
    rec(float(cur.get("rates", {}).get("platinum", -1)) == 1.5,
        "current.rates.platinum == 1.5",
        f"got {cur.get('rates', {}).get('platinum')}")
    rec(float(cur.get("rates", {}).get("free_trial", -1)) == 0.0,
        "current.rates.free_trial remains 0.0",
        f"got {cur.get('rates', {}).get('free_trial')}")


def test_case4_negative_value_rejected(admin_tok: str) -> None:
    print("\n=== CASE 4 — PUT with negative value → 400 ===")
    payload = {
        "plans": {
            "silver": {"per_message_credits": -1},
        },
    }
    r = requests.put(
        f"{BASE}/admin/whatsapp-pricing",
        headers=auth_hdr(admin_tok),
        json=payload,
        timeout=30,
    )
    assert_status(r, 400, "PUT negative value returns 400")


def test_case5_put_without_enabled_preserves(admin_tok: str) -> None:
    print("\n=== CASE 5 — PUT without enabled field (should not flip) ===")
    # Precondition: enabled should currently be true after CASE 3.
    r0 = requests.get(
        f"{BASE}/admin/whatsapp-pricing",
        headers=auth_hdr(admin_tok),
        timeout=30,
    )
    pre_enabled = r0.json().get("current", {}).get("enabled")
    rec(pre_enabled is True,
        "precondition: enabled is currently true",
        f"got {pre_enabled}")

    payload = {
        "plans": {
            "gold": {"per_message_credits": 2},
        },
    }
    r = requests.put(
        f"{BASE}/admin/whatsapp-pricing",
        headers=auth_hdr(admin_tok),
        json=payload,
        timeout=30,
    )
    if not assert_status(r, 200, "PUT without enabled returns 200"):
        return
    data = r.json()
    cur = data.get("current", {})
    rec(cur.get("enabled") is True,
        "enabled remains true (no implicit flip)",
        f"got {cur.get('enabled')}")
    rec(float(cur.get("rates", {}).get("gold", -1)) == 2.0,
        "gold rate updated to 2.0",
        f"got {cur.get('rates', {}).get('gold')}")
    # silver should still be 0.5, platinum should still be 1.5 (merge preserves)
    rec(float(cur.get("rates", {}).get("silver", -1)) == 0.5,
        "silver rate preserved (0.5) across partial PUT",
        f"got {cur.get('rates', {}).get('silver')}")
    rec(float(cur.get("rates", {}).get("platinum", -1)) == 1.5,
        "platinum rate preserved (1.5) across partial PUT",
        f"got {cur.get('rates', {}).get('platinum')}")


def test_case6_me_endpoint(user_tok: str) -> Dict[str, Any]:
    print("\n=== CASE 6 — GET /me/whatsapp-pricing (regular user) ===")
    r = requests.get(
        f"{BASE}/me/whatsapp-pricing",
        headers=auth_hdr(user_tok),
        timeout=30,
    )
    if not assert_status(r, 200, "GET /me/whatsapp-pricing 200"):
        return {}
    data = r.json()
    rec("enabled" in data, "response has 'enabled' key",
        f"keys={list(data.keys())}")
    rec("plan" in data, "response has 'plan' key",
        f"keys={list(data.keys())}")
    rec("per_message_credits" in data,
        "response has 'per_message_credits' key",
        f"keys={list(data.keys())}")
    rec(data.get("enabled") is True,
        "me.enabled == true (matches global flag after CASE 3)",
        f"got {data.get('enabled')}")

    # Fetch user's plan from /auth/me and verify rate mapping
    r2 = requests.get(f"{BASE}/auth/me",
                      headers=auth_hdr(user_tok), timeout=30)
    me = r2.json() if r2.status_code == 200 else {}
    user_plan = (me.get("plan") or "free_trial")
    rec(data.get("plan") == user_plan,
        f"me.plan == user's actual plan ({user_plan})",
        f"got {data.get('plan')}")

    # Expected rate after CASE 5: {free_trial:0, silver:0.5, gold:2, platinum:1.5}
    expected_rate_map = {
        "free_trial": 0.0,
        "silver":     0.5,
        "gold":       2.0,
        "platinum":   1.5,
    }
    expected_rate = expected_rate_map.get(user_plan, 0.0)
    rec(float(data.get("per_message_credits", -999)) == expected_rate,
        f"me.per_message_credits matches admin rate for '{user_plan}' ({expected_rate})",
        f"got {data.get('per_message_credits')}")
    return data


def test_case7_reset_all(admin_tok: str) -> None:
    print("\n=== CASE 7 — PUT reset (enabled=false, all rates 0) ===")
    payload = {
        "enabled": False,
        "plans": {
            "free_trial": {"per_message_credits": 0},
            "silver":     {"per_message_credits": 0},
            "gold":       {"per_message_credits": 0},
            "platinum":   {"per_message_credits": 0},
        },
    }
    r = requests.put(
        f"{BASE}/admin/whatsapp-pricing",
        headers=auth_hdr(admin_tok),
        json=payload,
        timeout=30,
    )
    if not assert_status(r, 200, "PUT reset returns 200"):
        return

    r2 = requests.get(
        f"{BASE}/admin/whatsapp-pricing",
        headers=auth_hdr(admin_tok),
        timeout=30,
    )
    if not assert_status(r2, 200, "GET after reset 200"):
        return
    data = r2.json()
    cur = data.get("current", {})
    rec(cur.get("enabled") is False,
        "current.enabled == false after reset",
        f"got {cur.get('enabled')}")
    for plan in EXPECTED_ORDER:
        rec(float(cur.get("rates", {}).get(plan, -1)) == 0.0,
            f"current.rates.{plan} == 0.0 after reset",
            f"got {cur.get('rates', {}).get(plan)}")


def test_case8_idempotent_get(admin_tok: str) -> None:
    print("\n=== CASE 8 — GET idempotency (two identical calls) ===")
    r1 = requests.get(
        f"{BASE}/admin/whatsapp-pricing",
        headers=auth_hdr(admin_tok),
        timeout=30,
    )
    r2 = requests.get(
        f"{BASE}/admin/whatsapp-pricing",
        headers=auth_hdr(admin_tok),
        timeout=30,
    )
    ok1 = r1.status_code == 200 and r2.status_code == 200
    rec(ok1, "both GETs returned 200",
        f"s1={r1.status_code} s2={r2.status_code}")
    if ok1:
        rec(r1.json() == r2.json(),
            "two GETs returned identical JSON",
            f"diff observed between first and second GET")


# ---------------------------------------------------------------------------
def main() -> int:
    print(f"\n=== WhatsApp Pricing Admin Test against {BASE} ===\n")
    try:
        admin_tok = login(ADMIN_EMAIL, ADMIN_PWD)
        print(f"  ✅ admin login OK")
        user_tok = login(USER_EMAIL, USER_PWD)
        print(f"  ✅ user login OK")
    except Exception as exc:
        print(f"  ❌ login failed: {exc}")
        return 1

    # First, reset to known baseline so initial-state assertions hold.
    print("\n--- baseline reset (to enabled=false / all rates 0) ---")
    requests.put(
        f"{BASE}/admin/whatsapp-pricing",
        headers=auth_hdr(admin_tok),
        json={
            "enabled": False,
            "plans": {p: {"per_message_credits": 0} for p in EXPECTED_ORDER},
        },
        timeout=30,
    )

    test_case1_admin_get_baseline(admin_tok)
    test_case2_non_admin_forbidden(user_tok)
    test_case3_admin_put_enable(admin_tok)
    test_case4_negative_value_rejected(admin_tok)
    test_case5_put_without_enabled_preserves(admin_tok)
    test_case6_me_endpoint(user_tok)
    test_case7_reset_all(admin_tok)
    test_case8_idempotent_get(admin_tok)

    print("\n" + "=" * 60)
    print(f"  TOTAL PASS: {len(PASS)}")
    print(f"  TOTAL FAIL: {len(FAIL)}")
    if FAIL:
        print("  FAILED ASSERTIONS:")
        for f in FAIL:
            print(f"   - {f}")
    return 0 if not FAIL else 2


if __name__ == "__main__":
    sys.exit(main())
