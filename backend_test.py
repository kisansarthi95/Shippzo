"""
Master Sheet Backup Verification — ensures every shipment-creation path
writes to the central Master Sheet before committing to Mongo.

Covers review contract assertions A, C, D, E. Assertion B (forced 502
when master_sheet_id is cleared) is SKIPPED — admin_config state is
production-shared and mutating it would affect other concurrent tests.
"""

import json
import os
import sys
import uuid
from typing import Any, Dict, List, Optional, Tuple

import requests

BASE = os.environ.get(
    "BACKEND_URL",
    "https://logistics-hub-740.preview.emergentagent.com",
).rstrip("/") + "/api"

TIMEOUT = 60

USER2_EMAIL = "user2@test.com"
USER2_PASS = "User@12345"
ADMIN_EMAIL = "admin@test.com"
ADMIN_PASS = "Admin@12345"

# Results registry
results: List[Tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    tag = "PASS" if ok else "FAIL"
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""))


def auth_headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def login(email: str, password: str) -> Optional[str]:
    r = requests.post(
        f"{BASE}/auth/login", json={"email": email, "password": password}, timeout=TIMEOUT
    )
    if r.status_code != 200:
        print(f"  login failed ({email}): {r.status_code} {r.text[:200]}")
        return None
    return r.json().get("token")


def get_couriers(token: str) -> List[Dict[str, Any]]:
    r = requests.get(f"{BASE}/couriers", headers=auth_headers(token), timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def probe(token: str) -> Dict[str, Any]:
    try:
        r = requests.get(
            f"{BASE}/sheets/probe", headers=auth_headers(token), timeout=TIMEOUT
        )
        return r.json() if r.status_code == 200 else {"ok": False, "status": r.status_code}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------- Assertion A: POST /api/shipments writes to Master Sheet ----------
def test_A_manual_shipment_writes_to_master(user2_token: str, courier_id: str) -> None:
    print("\n=== Assertion A — POST /api/shipments writes to Master Sheet ===")

    probe_before = probe(user2_token)
    record(
        "A.0 sheets probe OK (pre)",
        bool(probe_before.get("ok")),
        f"tab={probe_before.get('tab')!r} row_count={probe_before.get('row_count')}",
    )

    # Make tracking_id unique so we can find the shipment even across retries.
    tracking = f"TESTSHIP{uuid.uuid4().hex[:6].upper()}"
    payload = {
        "courier_id": courier_id,
        "tracking_id": tracking,
        "customer_name": "Test Master Backup",
        "customer_phone": "9999999999",
        "address_line1": "123 Test Street",
        "address_line2": "",
        "city": "Mumbai",
        "state": "MH",
        "pincode": "400001",
        "amount": 250,
        "payment_mode": "COD",
        "items": ["Test Item"],
    }
    r = requests.post(
        f"{BASE}/shipments", headers=auth_headers(user2_token), json=payload, timeout=TIMEOUT
    )
    record(
        "A.1 POST /api/shipments returns 200",
        r.status_code == 200,
        f"status={r.status_code} body={r.text[:300]}",
    )
    if r.status_code != 200:
        return

    ship = r.json()
    ship_id = ship.get("id")
    sheet_row_num = ship.get("sheet_row_num")

    # Re-fetch from Mongo via GET /api/shipments/{id}
    r2 = requests.get(
        f"{BASE}/shipments/{ship_id}", headers=auth_headers(user2_token), timeout=TIMEOUT
    )
    record(
        "A.3 GET /api/shipments/{id} returns 200",
        r2.status_code == 200,
        f"status={r2.status_code}",
    )
    stored_row = None
    if r2.status_code == 200:
        stored_row = r2.json().get("sheet_row_num")

    # Review allows either: response OR Mongo doc has non-empty sheet_row_num.
    effective_row = sheet_row_num if isinstance(sheet_row_num, int) and sheet_row_num > 1 else stored_row
    record(
        "A.2 Mongo OR response carries sheet_row_num (non-empty int > 1)",
        isinstance(effective_row, int) and effective_row > 1,
        f"response_sheet_row_num={sheet_row_num!r} mongo_stored={stored_row!r}",
    )
    record(
        "A.4 Mongo doc has sheet_row_num populated (source of truth)",
        isinstance(stored_row, int) and stored_row > 1,
        f"stored={stored_row!r}",
    )

    probe_after = probe(user2_token)
    record(
        "A.5 sheets probe OK (post)",
        bool(probe_after.get("ok")),
        f"row_count={probe_after.get('row_count')}",
    )

    # Cleanup
    rd = requests.delete(
        f"{BASE}/shipments/{ship_id}", headers=auth_headers(user2_token), timeout=TIMEOUT
    )
    sheet_info = {}
    try:
        sheet_info = rd.json().get("sheet", {})
    except Exception:
        pass
    record(
        "A.6 DELETE /api/shipments/{id} returns 200 (cleanup)",
        rd.status_code == 200,
        f"status={rd.status_code} sheet={sheet_info}",
    )
    record(
        "A.7 tombstone attempted on delete (sheet.attempted=true)",
        bool(sheet_info.get("attempted")),
        f"sheet={sheet_info}",
    )


# ---------- Assertion C: Smart Paste → Ship pipeline does NOT duplicate ----------
def test_C_smart_paste_ship_no_duplicate(user2_token: str, courier_id: str) -> None:
    print("\n=== Assertion C — Smart Paste → Ship does NOT duplicate master row ===")

    payload = {
        "text": (
            "Test User SmartPaste\n"
            "9999999998\n"
            "123 Sample Rd\n"
            "Mumbai 400001\n"
            "COD 200\n"
            "T-Shirt"
        )
    }
    r = requests.post(
        f"{BASE}/smart-paste",
        headers=auth_headers(user2_token),
        json=payload,
        timeout=TIMEOUT,
    )
    record(
        "C.1 POST /api/smart-paste returns 200",
        r.status_code == 200,
        f"status={r.status_code} body={r.text[:300]}",
    )
    if r.status_code != 200:
        return

    pending = r.json()
    pending_id = pending.get("id")
    pending_row = pending.get("sheet_row_num")
    pending_moid = pending.get("master_order_id") or ""
    record(
        "C.2 pending has sheet_row_num (non-empty int > 1)",
        isinstance(pending_row, int) and pending_row > 1,
        f"sheet_row_num={pending_row!r}",
    )
    record(
        "C.3 pending has master_order_id populated",
        bool(pending_moid),
        f"master_order_id={pending_moid!r}",
    )

    # Ship it
    rs = requests.post(
        f"{BASE}/orders/pending/{pending_id}/ship",
        headers=auth_headers(user2_token),
        json={"courier_id": courier_id},
        timeout=TIMEOUT,
    )
    record(
        "C.4 POST /api/orders/pending/{id}/ship returns 200",
        rs.status_code == 200,
        f"status={rs.status_code} body={rs.text[:300]}",
    )
    if rs.status_code != 200:
        # Try cleanup: delete the pending order
        requests.delete(
            f"{BASE}/orders/pending/{pending_id}",
            headers=auth_headers(user2_token),
            timeout=TIMEOUT,
        )
        return

    ship = rs.json()
    ship_id = ship.get("id")
    ship_row = ship.get("sheet_row_num")
    ship_moid = ship.get("master_order_id") or ""

    # THE critical check — master-sheet row was NOT duplicated.
    record(
        "C.5 shipment.sheet_row_num == pending.sheet_row_num (no duplicate Master row)",
        ship_row == pending_row and isinstance(ship_row, int),
        f"pending_row={pending_row!r} ship_row={ship_row!r}",
    )
    record(
        "C.6 master_order_id forwarded from pending → shipment",
        ship_moid == pending_moid and bool(ship_moid),
        f"pending_moid={pending_moid!r} ship_moid={ship_moid!r}",
    )

    # Cleanup
    rd = requests.delete(
        f"{BASE}/shipments/{ship_id}",
        headers=auth_headers(user2_token),
        timeout=TIMEOUT,
    )
    record(
        "C.7 DELETE shipment cleanup returns 200",
        rd.status_code == 200,
        f"status={rd.status_code}",
    )


# ---------- Assertion D: Plan independence (admin path) ----------
def test_D_admin_plan_independence(admin_token: str) -> None:
    print("\n=== Assertion D — Plan independence (admin POST /api/shipments) ===")
    # Admin needs a courier — pick first from admin's own courier list
    couriers = get_couriers(admin_token)
    if not couriers:
        record("D.0 admin has at least 1 courier", False, "no couriers returned")
        return
    courier_id = couriers[0]["id"]
    record("D.0 admin has at least 1 courier", True, f"courier_id={courier_id}")

    payload = {
        "courier_id": courier_id,
        "tracking_id": f"ADMSHIP{uuid.uuid4().hex[:6].upper()}",
        "customer_name": "Admin Master Backup Test",
        "customer_phone": "9988776655",
        "address_line1": "45 Admin Lane",
        "address_line2": "",
        "city": "Ahmedabad",
        "state": "GJ",
        "pincode": "380001",
        "amount": 399,
        "payment_mode": "COD",
        "items": ["Admin Sample"],
    }
    r = requests.post(
        f"{BASE}/shipments", headers=auth_headers(admin_token), json=payload, timeout=TIMEOUT
    )
    record(
        "D.1 admin POST /api/shipments returns 200",
        r.status_code == 200,
        f"status={r.status_code} body={r.text[:300]}",
    )
    if r.status_code != 200:
        return

    ship = r.json()
    ship_id = ship.get("id")
    sheet_row_num = ship.get("sheet_row_num")

    # Re-fetch via GET and check Mongo-stored sheet_row_num (per review spec).
    r2 = requests.get(
        f"{BASE}/shipments/{ship_id}", headers=auth_headers(admin_token), timeout=TIMEOUT
    )
    stored_row = r2.json().get("sheet_row_num") if r2.status_code == 200 else None
    effective_row = sheet_row_num if isinstance(sheet_row_num, int) and sheet_row_num > 1 else stored_row
    record(
        "D.2 admin shipment has sheet_row_num (response OR Mongo) > 1",
        isinstance(effective_row, int) and effective_row > 1,
        f"response={sheet_row_num!r} mongo={stored_row!r}",
    )

    rd = requests.delete(
        f"{BASE}/shipments/{ship_id}", headers=auth_headers(admin_token), timeout=TIMEOUT
    )
    record(
        "D.3 admin DELETE cleanup returns 200",
        rd.status_code == 200,
        f"status={rd.status_code}",
    )


# ---------- Assertion E: Regression smoke ----------
def test_E_regressions(user2_token: str) -> None:
    print("\n=== Assertion E — Regression smoke ===")

    r = requests.get(
        f"{BASE}/couriers/limits", headers=auth_headers(user2_token), timeout=TIMEOUT
    )
    ok = r.status_code == 200
    body = r.json() if ok else {}
    # expected shape keys
    expected_keys = {"limit", "can_add", "is_unlimited", "is_admin",
                     "plan_label", "current_count"}
    shape_ok = ok and expected_keys.issubset(set(body.keys()))
    record(
        "E.1 GET /api/couriers/limits returns 200 + expected shape",
        shape_ok,
        f"status={r.status_code} keys={sorted(body.keys())}",
    )

    r = requests.get(
        f"{BASE}/me/usage", headers=auth_headers(user2_token), timeout=TIMEOUT
    )
    record(
        "E.2 GET /api/me/usage returns 200",
        r.status_code == 200,
        f"status={r.status_code}",
    )

    r = requests.get(
        f"{BASE}/shipments", headers=auth_headers(user2_token), timeout=TIMEOUT
    )
    record(
        "E.3 GET /api/shipments returns 200",
        r.status_code == 200,
        f"status={r.status_code} count={len(r.json()) if r.status_code == 200 else 'n/a'}",
    )

    r = requests.get(
        f"{BASE}/sheets/orders", headers=auth_headers(user2_token), timeout=TIMEOUT
    )
    # 200 = sheet connected; 400 = sheet not linked (valid for user2 demo data).
    record(
        "E.4 GET /api/sheets/orders returns 200 or 400 (non-regression)",
        r.status_code in (200, 400),
        f"status={r.status_code} detail={(r.json().get('detail') if r.headers.get('content-type','').startswith('application/json') else '')[:120]}",
    )


def main() -> int:
    print(f"Testing Master Sheet Backup at {BASE}\n")

    user2_token = login(USER2_EMAIL, USER2_PASS)
    admin_token = login(ADMIN_EMAIL, ADMIN_PASS)
    record("Login user2@test.com", user2_token is not None)
    record("Login admin@test.com", admin_token is not None)
    if not user2_token or not admin_token:
        summary(failure=True)
        return 1

    couriers = get_couriers(user2_token)
    record("user2 has at least 1 courier", len(couriers) > 0, f"count={len(couriers)}")
    if not couriers:
        summary(failure=True)
        return 1
    courier_id = couriers[0]["id"]

    test_A_manual_shipment_writes_to_master(user2_token, courier_id)
    test_C_smart_paste_ship_no_duplicate(user2_token, courier_id)
    test_D_admin_plan_independence(admin_token)
    test_E_regressions(user2_token)

    return 0 if summary() == 0 else 1


def summary(failure: bool = False) -> int:
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print("\n" + "=" * 60)
    print(f"SUMMARY: {passed}/{total} assertions passed")
    fails = [(n, d) for n, ok, d in results if not ok]
    if fails:
        print("\nFAILURES:")
        for n, d in fails:
            print(f"  - {n}: {d}")
    return len(fails)


if __name__ == "__main__":
    sys.exit(main())
