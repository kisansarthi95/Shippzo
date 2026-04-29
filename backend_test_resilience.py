"""
Test the resilience layer for Master Sheet writes (Google Sheets 429 quota
handling) introduced 2026-04-29.

Coverage maps to the review request assertions A1-A9 (static) plus B10-B13
(runtime happy path) and D16-D18 (regression).

C14-C15 (transient simulation) are SKIPPED — would require gspread
monkey-patch on a deployed backend which we can't safely mutate.
"""
from __future__ import annotations

import os
import re
import sys
import json
import time
import requests
from typing import Dict, Any, List, Tuple

BASE = os.environ.get(
    "BACKEND_URL",
    "https://logistics-hub-740.preview.emergentagent.com",
).rstrip("/") + "/api"

ADMIN = ("admin@test.com", "Admin@12345")
USER2 = ("user2@test.com", "User@12345")

results: List[Tuple[str, bool, str]] = []


def chk(name: str, cond: bool, detail: str = "") -> bool:
    results.append((name, bool(cond), detail))
    flag = "PASS" if cond else "FAIL"
    print(f"  [{flag}] {name}" + (f"  — {detail}" if detail else ""))
    return bool(cond)


def login(email: str, password: str) -> Dict[str, Any]:
    r = requests.post(f"{BASE}/auth/login", json={"email": email, "password": password}, timeout=30)
    r.raise_for_status()
    return r.json()


def auth_headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# =========================================================================
# A. Static / wiring assertions (read source files)
# =========================================================================
def section_A_static():
    print("\n[A] Static / wiring assertions")
    SW = "/app/backend/sheet_writer.py"
    SV = "/app/backend/server.py"

    def read(p):
        with open(p, "r", encoding="utf-8") as f:
            return f.read()

    sw_src = read(SW)
    sv_src = read(SV)

    # A1
    chk(
        "A1 sheet_writer: _with_retry / _is_transient / _RETRY_STATUSES exist",
        ("def _with_retry" in sw_src)
        and ("def _is_transient" in sw_src)
        and ("_RETRY_STATUSES = (429, 500, 502, 503, 504)" in sw_src),
    )

    # A2
    chk(
        "A2 sheet_writer: _MASTER_NEXT_ROW_CACHE + _master_cache_key exist",
        ("_MASTER_NEXT_ROW_CACHE" in sw_src) and ("def _master_cache_key" in sw_src),
    )

    # A3 — append_order_row uses _with_retry(ws.update,...) AND _MASTER_NEXT_ROW_CACHE
    # extract the function body.
    m = re.search(r"def append_order_row\([\s\S]+?(?=\ndef |\Z)", sw_src)
    body = m.group(0) if m else ""
    chk(
        "A3 append_order_row wraps ws.update in _with_retry",
        bool(re.search(r"_with_retry\(\s*ws\.update", body)),
    )
    chk(
        "A3 append_order_row uses _MASTER_NEXT_ROW_CACHE",
        "_MASTER_NEXT_ROW_CACHE" in body,
    )

    # A4 — _find_next_empty_row body wraps inner in _with_retry
    m = re.search(r"def _find_next_empty_row\([\s\S]+?(?=\ndef |\Z)", sw_src)
    body4 = m.group(0) if m else ""
    chk(
        "A4 _find_next_empty_row body wraps inner in _with_retry",
        ("def _inner" in body4) and ("_with_retry(_inner)" in body4),
    )

    # A5 — server.py defines sentinel + helper + worker
    chk(
        "A5 server: _BACKUP_DEFERRED_SENTINEL defined",
        "_BACKUP_DEFERRED_SENTINEL" in sv_src,
    )
    chk(
        "A5 server: _is_transient_sheet_error defined",
        re.search(r"def _is_transient_sheet_error\(", sv_src) is not None,
    )
    chk(
        "A5 server: _master_backup_retry_worker defined",
        re.search(r"async def _master_backup_retry_worker\(", sv_src) is not None,
    )

    # A6 — _backup_shipment_to_master_sheet returns the deferred sentinel on
    # transient errors and raises HTTPException only on permanent.
    m = re.search(
        r"async def _backup_shipment_to_master_sheet\([\s\S]+?(?=\nasync def |\ndef |\n@api_router|\Z)",
        sv_src,
    )
    body6 = m.group(0) if m else ""
    chk(
        "A6 _backup_shipment_to_master_sheet handles transient via sentinel",
        ("_is_transient_sheet_error" in body6)
        and ("_BACKUP_DEFERRED_SENTINEL" in body6)
        and ("_pending_payload" in body6),
    )
    chk(
        "A6 _backup_shipment_to_master_sheet raises HTTPException on permanent",
        ("HTTPException" in body6) and ("status_code=502" in body6),
    )

    # A7 — POST /shipments and POST /orders/pending/{id}/ship handle deferred
    # sentinel by setting master_backup_status='pending' and persisting payload.
    m_create = re.search(
        r"async def create_shipment\([\s\S]+?(?=\n@api_router|\nasync def |\ndef )",
        sv_src,
    )
    body7a = m_create.group(0) if m_create else ""
    chk(
        "A7 POST /shipments handles deferred + sets master_backup_status='pending'",
        ('sheet_meta.get("deferred")' in body7a)
        and ('master_backup_status' in body7a and '"pending"' in body7a)
        and ("master_backup_payload" in body7a),
    )

    m_ship = re.search(
        r"async def ship_pending_order\([\s\S]+?(?=\n@api_router|\nasync def |\ndef )",
        sv_src,
    )
    if not m_ship:
        # Try alt naming
        m_ship = re.search(
            r"async def \w+\([\s\S]+?ship_pending_order[\s\S]+?(?=\n@api_router|\nasync def |\ndef )",
            sv_src,
        )
    body7b = m_ship.group(0) if m_ship else ""
    if not body7b:
        # Search for the route handler that references /pending/{id}/ship
        m_ship2 = re.search(
            r"@api_router\.post\(\"/orders/pending/\{[^}]+\}/ship\"[\s\S]+?(?=\n@api_router|\Z)",
            sv_src,
        )
        body7b = m_ship2.group(0) if m_ship2 else ""
    chk(
        "A7 POST /orders/pending/{id}/ship handles deferred sentinel",
        ('sheet_meta.get("deferred")' in body7b)
        and ('"pending"' in body7b)
        and ("master_backup_payload" in body7b),
    )

    # A8 — _sync_user_sheet_to_master_bg: BATCH_LIMIT=20, SLEEP_BETWEEN_ROWS=1.2,
    # awaits asyncio.sleep(SLEEP_BETWEEN_ROWS)
    m_sync = re.search(
        r"async def _sync_user_sheet_to_master_bg\([\s\S]+?(?=\nasync def |\ndef |\n@api_router|\Z)",
        sv_src,
    )
    body8 = m_sync.group(0) if m_sync else ""
    chk(
        "A8 _sync_user_sheet_to_master_bg BATCH_LIMIT == 20",
        re.search(r"BATCH_LIMIT\s*=\s*20\b", body8) is not None,
    )
    chk(
        "A8 _sync_user_sheet_to_master_bg SLEEP_BETWEEN_ROWS == 1.2",
        re.search(r"SLEEP_BETWEEN_ROWS\s*=\s*1\.2\b", body8) is not None,
    )
    chk(
        "A8 _sync_user_sheet_to_master_bg awaits asyncio.sleep(SLEEP_BETWEEN_ROWS)",
        re.search(r"await\s+_?asyncio\.sleep\(SLEEP_BETWEEN_ROWS\)", body8) is not None,
    )

    # A9 — startup hook _master_backup_retry_worker is scheduled via asyncio.create_task in on_startup
    m_on = re.search(
        r"async def on_startup\([\s\S]+?(?=\nasync def |\ndef |\n@app\.|\Z)",
        sv_src,
    )
    body9 = m_on.group(0) if m_on else ""
    chk(
        "A9 on_startup schedules _master_backup_retry_worker via asyncio.create_task",
        re.search(r"_?asyncio\.create_task\(\s*_master_backup_retry_worker\(\)", body9) is not None,
    )


# =========================================================================
# B. Runtime — happy path
# =========================================================================
def section_B_runtime():
    print("\n[B] Runtime — happy path (no quota issues)")
    user2 = login(*USER2)
    token = user2["token"]
    h = auth_headers(token)

    # Get a courier for user2 (Demo Courier seeded)
    r = requests.get(f"{BASE}/couriers", headers=h, timeout=30)
    couriers = r.json() if r.ok else []
    courier = next((c for c in couriers if c.get("name")), None)
    if not chk("B pre: user2 has at least one courier", courier is not None):
        return

    # B10 — POST /api/shipments → 200 with sheet_row_num populated
    payload = {
        "customer_name": "Resilience Test Alpha",
        "customer_phone": "9876511101",
        "address_line1": "12 Resilience Lane",
        "address_line2": "Sector 5",
        "city": "Ahmedabad",
        "state": "Gujarat",
        "pincode": "380001",
        "items": ["Test Box 1kg"],
        "amount": 499.0,
        "payment_mode": "COD",
        "courier_id": courier["id"],
        "tracking_id": "RESIL-A-001",
    }
    r = requests.post(f"{BASE}/shipments", headers=h, json=payload, timeout=60)
    ok10 = chk(
        "B10 POST /api/shipments returns 200",
        r.status_code == 200,
        f"status={r.status_code} body={r.text[:200]}",
    )
    ship_id = None
    row_num_1 = None
    if ok10:
        data = r.json()
        ship_id = data.get("id")
        row_num_1 = data.get("sheet_row_num")
        chk(
            "B10 response shipment has sheet_row_num (positive int)",
            isinstance(row_num_1, int) and row_num_1 > 1,
            f"sheet_row_num={row_num_1}",
        )

    # B13 — second consecutive POST should land on row_num_1 + 1 (cache hit)
    payload2 = dict(payload)
    payload2["customer_name"] = "Resilience Test Bravo"
    payload2["customer_phone"] = "9876511102"
    payload2["tracking_id"] = "RESIL-A-002"
    r2 = requests.post(f"{BASE}/shipments", headers=h, json=payload2, timeout=60)
    ok13 = chk(
        "B13 second POST /api/shipments returns 200",
        r2.status_code == 200,
        f"status={r2.status_code}",
    )
    ship_id_2 = None
    row_num_2 = None
    if ok13:
        d2 = r2.json()
        ship_id_2 = d2.get("id")
        row_num_2 = d2.get("sheet_row_num")
        chk(
            "B13 second shipment sheet_row_num is consecutive (cache populated)",
            isinstance(row_num_2, int) and isinstance(row_num_1, int) and row_num_2 == row_num_1 + 1,
            f"row1={row_num_1} row2={row_num_2}",
        )

    # B11 — POST /smart-paste then POST /orders/pending/{id}/ship → no duplicate row
    paste_text = (
        "Name: Resilience SmartPaste\n"
        "Mobile: 9876511103\n"
        "Address: 99 SmartPaste Rd, Ahmedabad, Gujarat 380002\n"
        "Item: Test Box 2kg\n"
        "Amount: 999\n"
        "Payment: COD\n"
    )
    r3 = requests.post(f"{BASE}/smart-paste", headers=h, json={"text": paste_text, "skip_llm": True}, timeout=120)
    ok_sp = chk(
        "B11 POST /api/smart-paste returns 200",
        r3.status_code == 200,
        f"status={r3.status_code} body={r3.text[:200]}",
    )
    pending_id = None
    sp_row = None
    if ok_sp:
        sp = r3.json()
        # Smart paste returns either {fields, ...} or PendingOrder. Look for id.
        pending_id = sp.get("id")
        sp_row = sp.get("sheet_row_num")
        chk(
            "B11 smart-paste returned pending order with sheet_row_num",
            isinstance(sp_row, int) and sp_row > 1,
            f"sheet_row_num={sp_row}",
        )

    ship_id_3 = None
    if pending_id:
        # Ship it
        r4 = requests.post(
            f"{BASE}/orders/pending/{pending_id}/ship",
            headers=h,
            json={"courier_id": courier["id"], "overrides": {}},
            timeout=60,
        )
        ok_sh = chk(
            "B11 POST /orders/pending/{id}/ship returns 200",
            r4.status_code == 200,
            f"status={r4.status_code} body={r4.text[:200]}",
        )
        if ok_sh:
            sh = r4.json()
            ship_id_3 = sh.get("id")
            chk(
                "B11 ship doc carries forwarded sheet_row_num (no duplicate row)",
                sh.get("sheet_row_num") == sp_row,
                f"sheet_row_num={sh.get('sheet_row_num')} expected={sp_row}",
            )

    # B12 — GET /api/sheets/orders. user2 may not have a sheet linked
    # (per prior runs). 400 means "Google Sheet not connected" which is
    # not a regression — we accept 200 (with shape check) or 400.
    r5 = requests.get(f"{BASE}/sheets/orders", headers=h, timeout=60)
    if r5.status_code == 400:
        chk(
            "B12 GET /api/sheets/orders 400 (no sheet linked) — N/A",
            True,
            f"status=400 (user2 has no sheet linked, regression-safe)",
        )
    else:
        ok_so = chk("B12 GET /api/sheets/orders 200", r5.status_code == 200)
        if ok_so:
            body = r5.json()
            chk(
                "B12 /sheets/orders payload shape unchanged",
                isinstance(body, dict)
                and all(k in body for k in ("orders", "headers", "total")),
                f"keys={list(body.keys())[:8]}",
            )

    # Cleanup all created shipments
    print("\n[B] Cleanup")
    for sid in (ship_id, ship_id_2, ship_id_3):
        if sid:
            try:
                rd = requests.delete(f"{BASE}/shipments/{sid}", headers=h, timeout=30)
                print(f"  DELETE /shipments/{sid} → {rd.status_code}")
            except Exception as e:
                print(f"  cleanup error for {sid}: {e}")


# =========================================================================
# D. Regression
# =========================================================================
def section_D_regression():
    print("\n[D] Regression")
    user2 = login(*USER2)
    h = auth_headers(user2["token"])

    # D16 — couriers/limits, me/usage, sheets/orders all 200
    r1 = requests.get(f"{BASE}/couriers/limits", headers=h, timeout=30)
    chk("D16a GET /couriers/limits → 200", r1.status_code == 200, f"status={r1.status_code}")
    r2 = requests.get(f"{BASE}/me/usage", headers=h, timeout=30)
    chk("D16b GET /me/usage → 200", r2.status_code == 200, f"status={r2.status_code}")
    r3 = requests.get(f"{BASE}/sheets/orders", headers=h, timeout=60)
    chk("D16c GET /sheets/orders → 200 or 400 (no-sheet)", r3.status_code in (200, 400), f"status={r3.status_code}")

    # D17 — user_sheet_master_backups index intact
    try:
        from pymongo import MongoClient
        # Use the backend's MONGO_URL
        with open("/app/backend/.env") as f:
            env_lines = f.read().splitlines()
        mongo_url = next((l.split("=", 1)[1].strip().strip('"') for l in env_lines if l.startswith("MONGO_URL=")), "mongodb://localhost:27017")
        db_name = next((l.split("=", 1)[1].strip().strip('"') for l in env_lines if l.startswith("DB_NAME=")), "test_database")
        client = MongoClient(mongo_url)
        db = client[db_name]
        idx = db.user_sheet_master_backups.index_information()
        chk(
            "D17 user_sheet_master_backups has uid_rowkey_unique index",
            "uid_rowkey_unique" in idx and idx["uid_rowkey_unique"].get("unique") is True,
            f"indexes={list(idx.keys())}",
        )
    except Exception as e:
        chk("D17 user_sheet_master_backups index check", False, f"error={e}")

    # D18 — POST /coupons/validate (regression): try a clearly-invalid coupon to get 200/400.
    r4 = requests.post(
        f"{BASE}/coupons/validate",
        headers=h,
        json={"code": "BOGUS-DOES-NOT-EXIST", "plan_key": "silver", "billing_cycle": "monthly"},
        timeout=30,
    )
    chk(
        "D18 POST /coupons/validate returns 200 or 400 (not 5xx)",
        r4.status_code in (200, 400, 404, 422),
        f"status={r4.status_code} body={r4.text[:160]}",
    )


# =========================================================================
# Run
# =========================================================================
if __name__ == "__main__":
    section_A_static()
    section_B_runtime()
    print("\n[C] SKIPPED — would require gspread monkey-patch on a deployed backend.")
    section_D_regression()

    # Summary
    passes = sum(1 for _, ok, _ in results if ok)
    fails = sum(1 for _, ok, _ in results if not ok)
    total = len(results)
    print("\n" + "=" * 70)
    print(f"TOTAL: {passes}/{total} passed, {fails} failed")
    print("=" * 70)
    if fails:
        print("\nFailures:")
        for name, ok, detail in results:
            if not ok:
                print(f"  - {name}: {detail}")
        sys.exit(1)
    sys.exit(0)
