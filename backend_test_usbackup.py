"""
User-Sheet → Master-Sheet Auto-Backup verification.

Static checks (always run):
  S1. BackgroundTasks imported in server.py
  S2. _ensure_user_sheet_backup_index, _row_to_master_payload,
      _sync_user_sheet_to_master_bg defined.
  S3. Helper passes notice='auto-backup from user sheet'.
  S4. /sheets/orders endpoint takes BackgroundTasks parameter and calls
      background_tasks.add_task(_sync_user_sheet_to_master_bg, ...).

Runtime checks (skipped — no fixture sheet for user2):
  R1. GET /api/sheets/orders 200 with expected shape.
  R2. After ~10s, user_sheet_master_backups has docs for user2 with
      backed_up_at + row_key.
  R3. Second call → no new docs.
  R4. Unique compound index uid_rowkey_unique exists.

Regression:
  REG1. GET /api/shipments 200.
  REG2. POST /api/shipments still creates with sheet_row_num.
  REG3. POST /api/smart-paste still works.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

import requests

BASE = os.environ.get(
    "BACKEND_URL",
    "https://logistics-hub-740.preview.emergentagent.com",
).rstrip("/") + "/api"
SERVER_FILE = "/app/backend/server.py"
TIMEOUT = 60

USER2_EMAIL = "user2@test.com"
USER2_PASS = "User@12345"
ADMIN_EMAIL = "admin@test.com"
ADMIN_PASS = "Admin@12345"

results: List[Tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    tag = "PASS" if ok else "FAIL"
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""))


def auth_headers(t: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {t}", "Content-Type": "application/json"}


def login(email: str, password: str) -> Optional[str]:
    r = requests.post(
        f"{BASE}/auth/login",
        json={"email": email, "password": password},
        timeout=TIMEOUT,
    )
    return r.json().get("token") if r.status_code == 200 else None


# ----------- Static checks -----------
def static_checks() -> None:
    print("\n=== Static Code Checks ===")
    src = open(SERVER_FILE).read()

    record(
        "S1 BackgroundTasks imported from fastapi",
        bool(re.search(r"^from fastapi import .*\bBackgroundTasks\b", src, re.M)),
    )
    record(
        "S2a _ensure_user_sheet_backup_index defined",
        "async def _ensure_user_sheet_backup_index" in src,
    )
    record(
        "S2b _row_to_master_payload defined",
        "def _row_to_master_payload" in src,
    )
    record(
        "S2c _sync_user_sheet_to_master_bg defined",
        "async def _sync_user_sheet_to_master_bg" in src,
    )
    record(
        "S3 helper passes notice='auto-backup from user sheet'",
        'notice="auto-backup from user sheet"' in src,
    )
    # S4: endpoint signature + add_task call
    sig_re = re.search(
        r'@api_router\.get\("/sheets/orders"\)\s*async def sheets_orders\(([^)]+)\)',
        src,
        re.S,
    )
    record(
        "S4a /sheets/orders has BackgroundTasks parameter",
        bool(sig_re and "background_tasks: BackgroundTasks" in sig_re.group(1)),
        f"sig={sig_re.group(1).strip() if sig_re else 'NOT FOUND'}",
    )
    record(
        "S4b endpoint calls background_tasks.add_task(_sync_user_sheet_to_master_bg, ...)",
        "background_tasks.add_task(" in src
        and "_sync_user_sheet_to_master_bg" in src.split("background_tasks.add_task(", 1)[1][:200],
    )
    record(
        "S5 user_sheet_master_backups collection referenced",
        "user_sheet_master_backups" in src,
    )
    record(
        "S6 unique index name 'uid_rowkey_unique' present",
        "uid_rowkey_unique" in src,
    )
    record(
        "S7 BATCH_LIMIT = 50 enforced",
        "BATCH_LIMIT = 50" in src,
    )


# ----------- Runtime: index + collection state -----------
def runtime_index_and_state(user2_token: str, admin_token: str) -> None:
    print("\n=== Runtime — index + collection state ===")
    # Trigger /sheets/orders for admin (has a sheet linked) so the bg task
    # runs at least once. user2 has no sheet so its call would 400 and bg
    # task never fires.
    r = requests.get(
        f"{BASE}/sheets/orders", headers=auth_headers(admin_token), timeout=TIMEOUT
    )
    record(
        "R0 admin GET /api/sheets/orders 200 (triggers bg index creation)",
        r.status_code == 200,
        f"status={r.status_code}",
    )
    if r.status_code == 200:
        body = r.json()
        expected_keys = {"headers", "headers_changed", "orders", "total", "access_method"}
        record(
            "R1 response shape contains all expected keys",
            expected_keys.issubset(set(body.keys())),
            f"keys={sorted(body.keys())}",
        )

    time.sleep(6)

    # Inspect Mongo index directly via Motor
    async def _inspect():
        from motor.motor_asyncio import AsyncIOMotorClient
        cli = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
        db = cli[os.environ.get("DB_NAME", "test_database")]
        info = await db.user_sheet_master_backups.index_information()
        return info

    try:
        info = asyncio.get_event_loop().run_until_complete(_inspect())
    except RuntimeError:
        info = asyncio.new_event_loop().run_until_complete(_inspect())

    expected_index_name = "uid_rowkey_unique"
    has_idx = expected_index_name in info
    correct_keys = False
    if has_idx:
        keys = info[expected_index_name].get("key")
        # PyMongo returns SON; convert to list of (k,v) tuples for safe compare
        keys_list = [tuple(k) if isinstance(k, list) else k for k in keys]
        correct_keys = keys_list == [("user_id", 1), ("row_key", 1)]
    record(
        "R4a unique index 'uid_rowkey_unique' exists",
        has_idx,
        f"indexes={list(info.keys())}",
    )
    record(
        "R4b index keys are [('user_id', 1), ('row_key', 1)]",
        correct_keys,
        f"keys={info.get(expected_index_name, {}).get('key')}",
    )
    record(
        "R4c index is unique",
        has_idx and bool(info[expected_index_name].get("unique")),
        f"unique={info.get(expected_index_name, {}).get('unique')}",
    )

    # Note: user2 has no sheet → cannot test runtime backup count here.
    print("  Note: R2/R3 (count-after-call) skipped — user2@test.com has no fixture sheet.")
    print("  Note: admin has sheet but empty column_mapping → empty-row guard kicks in.")


# ----------- Regression: existing backend flows -----------
def regression(user2_token: str) -> None:
    print("\n=== Regression — existing flows ===")

    r = requests.get(
        f"{BASE}/shipments", headers=auth_headers(user2_token), timeout=TIMEOUT
    )
    record(
        "REG1 GET /api/shipments returns 200",
        r.status_code == 200,
        f"status={r.status_code} count={len(r.json()) if r.status_code == 200 else 'n/a'}",
    )

    # POST /api/shipments — pick first courier
    rc = requests.get(
        f"{BASE}/couriers", headers=auth_headers(user2_token), timeout=TIMEOUT
    )
    couriers = rc.json() if rc.status_code == 200 else []
    if not couriers:
        record("REG2 POST /api/shipments — has courier", False, "no couriers")
        return
    courier_id = couriers[0]["id"]

    payload = {
        "courier_id": courier_id,
        "tracking_id": f"REGSHIP{uuid.uuid4().hex[:6].upper()}",
        "customer_name": "Regression Test Backup",
        "customer_phone": "9123456780",
        "address_line1": "1 Regression Way",
        "city": "Mumbai",
        "state": "MH",
        "pincode": "400001",
        "amount": 199,
        "payment_mode": "COD",
        "items": ["Sample"],
    }
    r = requests.post(
        f"{BASE}/shipments",
        headers=auth_headers(user2_token),
        json=payload,
        timeout=TIMEOUT,
    )
    record(
        "REG2a POST /api/shipments returns 200",
        r.status_code == 200,
        f"status={r.status_code} body={r.text[:200]}",
    )
    ship_id = None
    if r.status_code == 200:
        ship = r.json()
        ship_id = ship.get("id")
        # Re-fetch from Mongo to get authoritative sheet_row_num
        rg = requests.get(
            f"{BASE}/shipments/{ship_id}",
            headers=auth_headers(user2_token),
            timeout=TIMEOUT,
        )
        stored = rg.json().get("sheet_row_num") if rg.status_code == 200 else None
        record(
            "REG2b shipment has sheet_row_num (master backup wired)",
            isinstance(stored, int) and stored > 1,
            f"sheet_row_num={stored!r}",
        )

    # POST /api/smart-paste
    sp_payload = {
        "text": (
            "RegSmart Paste\n"
            "9123456781\n"
            "11 Smart Paste Lane\n"
            "Mumbai 400001\n"
            "COD 250\n"
            "T-Shirt"
        )
    }
    rsp = requests.post(
        f"{BASE}/smart-paste",
        headers=auth_headers(user2_token),
        json=sp_payload,
        timeout=TIMEOUT,
    )
    record(
        "REG3 POST /api/smart-paste returns 200",
        rsp.status_code == 200,
        f"status={rsp.status_code}",
    )
    pending_id = rsp.json().get("id") if rsp.status_code == 200 else None

    # Cleanup
    if ship_id:
        rd = requests.delete(
            f"{BASE}/shipments/{ship_id}",
            headers=auth_headers(user2_token),
            timeout=TIMEOUT,
        )
        record(
            "REG4 DELETE cleanup shipment 200",
            rd.status_code == 200,
            f"status={rd.status_code}",
        )
    if pending_id:
        rdp = requests.delete(
            f"{BASE}/orders/pending/{pending_id}",
            headers=auth_headers(user2_token),
            timeout=TIMEOUT,
        )
        record(
            "REG5 DELETE cleanup pending 200",
            rdp.status_code == 200,
            f"status={rdp.status_code}",
        )


def main() -> int:
    print(f"Testing User-Sheet → Master-Sheet auto-backup at {BASE}\n")

    static_checks()

    u2 = login(USER2_EMAIL, USER2_PASS)
    ad = login(ADMIN_EMAIL, ADMIN_PASS)
    record("Login user2", u2 is not None)
    record("Login admin", ad is not None)
    if not u2 or not ad:
        return summary(force_fail=True)

    runtime_index_and_state(u2, ad)
    regression(u2)

    return summary()


def summary(force_fail: bool = False) -> int:
    print("\n" + "=" * 60)
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"SUMMARY: {passed}/{total} assertions passed")
    fails = [(n, d) for n, ok, d in results if not ok]
    if fails:
        print("\nFAILURES:")
        for n, d in fails:
            print(f"  - {n}: {d}")
    return 1 if (force_fail or fails) else 0


if __name__ == "__main__":
    sys.exit(main())
