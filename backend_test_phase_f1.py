"""
Phase F1 — CSV / XLSX Bulk Import to pending_orders.
Backend regression test against:
  https://logistics-hub-740.preview.emergentagent.com/api

Endpoints under test:
  POST /api/orders/import/preview
  POST /api/orders/import/commit
  GET  /api/me/file-import-mapping
  PUT  /api/me/file-import-mapping

Plus smoke regression on prior phases.
"""
import io
import json
import os
import sys
import time
import uuid
from typing import Any, Dict, List, Optional

import requests

BASE = "https://logistics-hub-740.preview.emergentagent.com/api"
TIMEOUT = 30

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASSWORD = "Admin@12345"
STAFF_EMAIL = "staff@test.com"
STAFF_PASSWORD = "Staff@12345"

results: List[Dict[str, Any]] = []
created_pending_ids: List[str] = []


def record(name: str, ok: bool, info: str = ""):
    results.append({"name": name, "ok": ok, "info": info})
    flag = "✅" if ok else "❌"
    print(f"{flag} {name}{(' — ' + info) if info else ''}", flush=True)


def login(email: str, password: str) -> Optional[str]:
    try:
        r = requests.post(
            f"{BASE}/auth/login",
            json={"email": email, "password": password},
            timeout=TIMEOUT,
        )
    except Exception as e:
        print(f"login error: {e}")
        return None
    if r.status_code != 200:
        print(f"login failed: {r.status_code} {r.text[:300]}")
        return None
    return r.json().get("token")


def auth(tok: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {tok}"}


# ════════════════════════════════════════════════════════════════════
# Helpers — build CSV/XLSX bytes
# ════════════════════════════════════════════════════════════════════
def csv_bytes(rows: List[List[str]], with_bom: bool = False) -> bytes:
    sio = io.StringIO()
    import csv
    w = csv.writer(sio)
    for r in rows:
        w.writerow(r)
    out = sio.getvalue().encode("utf-8")
    if with_bom:
        out = b"\xef\xbb\xbf" + out
    return out


def xlsx_bytes(rows: List[List[Any]]) -> bytes:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


# ════════════════════════════════════════════════════════════════════
# Test cases
# ════════════════════════════════════════════════════════════════════

def test_preview(tok: str):
    print("\n── 1. POST /api/orders/import/preview ──")

    # 1a. CSV with UTF-8 BOM (Excel-saved)
    rows = [
        ["Customer Name", "Customer Phone", "Pincode", "Amount", "Payment Mode", "City", "State"],
        ["Anjali Mehta", "9812345678", "395002", "1250.50", "COD", "Surat", "Gujarat"],
        ["Rohit Sharma", "9098765432", "110001", "999", "Paid", "Delhi", "Delhi"],
    ]
    files = {"file": ("excel_saved.csv", csv_bytes(rows, with_bom=True), "text/csv")}
    r = requests.post(f"{BASE}/orders/import/preview", headers=auth(tok), files=files, timeout=TIMEOUT)
    ok = r.status_code == 200
    record("1a CSV with UTF-8 BOM → 200", ok, f"status={r.status_code}")
    if ok:
        body = r.json()
        cols = body.get("columns", [])
        record("1a BOM stripped from columns[0]",
               cols and not cols[0].startswith("\ufeff") and cols[0] == "Customer Name",
               f"columns={cols}")
        record("1a format=csv", body.get("format") == "csv", f"format={body.get('format')}")
        record("1a total_rows=2", body.get("total_rows") == 2, f"total_rows={body.get('total_rows')}")
        # naive header match: "Customer Name" → "customer_name"
        sug = body.get("suggested", {})
        record("1a suggested has customer_name", sug.get("Customer Name") == "customer_name",
               f"suggested={sug}")
        record("1a suggested has customer_phone", sug.get("Customer Phone") == "customer_phone")
        record("1a suggested has pincode", sug.get("Pincode") == "pincode")
        record("1a suggested has amount", sug.get("Amount") == "amount")
        record("1a suggested has payment_mode (Payment Mode)",
               sug.get("Payment Mode") == "payment_mode")
        record("1a response shape complete",
               all(k in body for k in
                   ["format", "filename", "columns", "sample_rows", "total_rows",
                    "schema_fields", "suggested"]),
               f"keys={list(body.keys())}")
        record("1a schema_fields contains 18 entries",
               isinstance(body.get("schema_fields"), list) and len(body["schema_fields"]) == 18,
               f"len={len(body.get('schema_fields', []))}")

    # 1b. Plain CSV (no BOM)
    files = {"file": ("plain.csv", csv_bytes(rows, with_bom=False), "text/csv")}
    r = requests.post(f"{BASE}/orders/import/preview", headers=auth(tok), files=files, timeout=TIMEOUT)
    record("1b Plain CSV → 200", r.status_code == 200, f"status={r.status_code}")
    if r.status_code == 200:
        cols = r.json().get("columns", [])
        record("1b plain CSV first column == 'Customer Name'",
               cols and cols[0] == "Customer Name", f"cols={cols}")

    # 1c. XLSX (openpyxl path)
    files = {"file": ("orders.xlsx", xlsx_bytes(rows),
                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    r = requests.post(f"{BASE}/orders/import/preview", headers=auth(tok), files=files, timeout=TIMEOUT)
    ok = r.status_code == 200
    record("1c XLSX → 200", ok, f"status={r.status_code}")
    if ok:
        body = r.json()
        record("1c xlsx format=xlsx", body.get("format") == "xlsx")
        record("1c xlsx columns parsed",
               body.get("columns", [])[:2] == ["Customer Name", "Customer Phone"])
        record("1c xlsx sample_rows non-empty", len(body.get("sample_rows", [])) == 2)
        sample = body.get("sample_rows", [])
        if sample:
            record("1c xlsx sample row data",
                   sample[0].get("Customer Name") == "Anjali Mehta",
                   f"row[0]={sample[0]}")

    # 1d. Empty CSV
    files = {"file": ("empty.csv", b"", "text/csv")}
    r = requests.post(f"{BASE}/orders/import/preview", headers=auth(tok), files=files, timeout=TIMEOUT)
    record("1d Empty file → 400", r.status_code == 400, f"status={r.status_code} body={r.text[:120]}")
    if r.status_code == 400:
        det = (r.json().get("detail") or "").lower()
        record("1d detail mentions 'empty'", "empty" in det, f"detail={det}")

    # 1e. Unsupported extension
    for ext, ct in [(".txt", "text/plain"), (".pdf", "application/pdf"), (".docx", "application/octet-stream")]:
        files = {"file": (f"foo{ext}", b"some content here", ct)}
        r = requests.post(f"{BASE}/orders/import/preview", headers=auth(tok), files=files, timeout=TIMEOUT)
        record(f"1e Unsupported {ext} → 400", r.status_code == 400, f"status={r.status_code}")
        if r.status_code == 400:
            det = (r.json().get("detail") or "").lower()
            record(f"1e {ext} detail mentions supported formats",
                   ".csv" in det and ".xlsx" in det,
                   f"detail={det}")

    # 1f. File > 10 MB → 413
    big = b"a,b,c\n" + (b"x,y,z\n" * (11 * 1024 * 1024 // 6))  # ~11 MB
    files = {"file": ("big.csv", big, "text/csv")}
    r = requests.post(f"{BASE}/orders/import/preview", headers=auth(tok), files=files, timeout=60)
    record("1f File > 10 MB → 413", r.status_code == 413, f"status={r.status_code} size={len(big)}")


def test_mapping_get_put(tok: str):
    print("\n── 3+4. GET / PUT /api/me/file-import-mapping ──")

    # 3a. Baseline GET (clear any existing first via PUT empty)
    r0 = requests.put(f"{BASE}/me/file-import-mapping",
                      headers={**auth(tok), "Content-Type": "application/json"},
                      data=json.dumps({"mapping": {}}), timeout=TIMEOUT)
    record("3a setup PUT empty mapping → 200", r0.status_code == 200, f"status={r0.status_code}")

    r = requests.get(f"{BASE}/me/file-import-mapping", headers=auth(tok), timeout=TIMEOUT)
    ok = r.status_code == 200
    record("3a GET after empty PUT → 200", ok, f"status={r.status_code}")
    if ok:
        body = r.json()
        record("3a empty mapping returned",
               isinstance(body.get("mapping"), dict) and body.get("mapping") == {},
               f"mapping={body.get('mapping')}")
        record("3a schema_fields list returned",
               isinstance(body.get("schema_fields"), list) and len(body["schema_fields"]) == 18,
               f"len={len(body.get('schema_fields', []))}")

    # 4a. PUT valid mapping
    valid = {
        "Customer Name":  "customer_name",
        "Customer Phone": "customer_phone",
        "Pincode":        "pincode",
        "Amount":         "amount",
    }
    r = requests.put(f"{BASE}/me/file-import-mapping",
                     headers={**auth(tok), "Content-Type": "application/json"},
                     data=json.dumps({"mapping": valid}), timeout=TIMEOUT)
    record("4a PUT valid mapping → 200", r.status_code == 200, f"status={r.status_code}")

    # 3b. GET after PUT — returns mapping
    r = requests.get(f"{BASE}/me/file-import-mapping", headers=auth(tok), timeout=TIMEOUT)
    if r.status_code == 200:
        body = r.json()
        record("3b GET returns saved mapping",
               body.get("mapping") == valid,
               f"mapping={body.get('mapping')}")

    # 4b. Unknown schema field → 400
    bad = {"Foo": "not_a_real_field"}
    r = requests.put(f"{BASE}/me/file-import-mapping",
                     headers={**auth(tok), "Content-Type": "application/json"},
                     data=json.dumps({"mapping": bad}), timeout=TIMEOUT)
    record("4b PUT unknown schema field → 400",
           r.status_code == 400, f"status={r.status_code} body={r.text[:200]}")

    # 1g. Re-run preview to verify saved-default suggestion takes priority
    rows = [
        ["Customer Name", "Customer Phone", "Pincode", "Amount", "RandomCol"],
        ["Test User",     "9990001112",     "395001",  "100",    "ignored"],
    ]
    files = {"file": ("after_save.csv", csv_bytes(rows), "text/csv")}
    r = requests.post(f"{BASE}/orders/import/preview", headers=auth(tok), files=files, timeout=TIMEOUT)
    if r.status_code == 200:
        sug = r.json().get("suggested", {})
        record("1g Saved-default mapping wins on preview suggested",
               sug.get("Customer Name") == "customer_name"
               and sug.get("Customer Phone") == "customer_phone"
               and sug.get("Pincode") == "pincode"
               and sug.get("Amount") == "amount",
               f"suggested={sug}")


def test_commit_happy_path(tok: str):
    print("\n── 2. POST /api/orders/import/commit ──")

    rows = [
        ["Cust Name", "Phone", "Pin",     "Amt",        "PayMode", "City",   "State",   "Items"],
        ["Suresh Patel",  "9811112222", "395 002", "₹1,250.50", "cod",  "Surat",  "Gujarat", "1 Saree"],
        ["Megha Iyer",    "9844455555", "560-001", "999",        "Paid", "Bangalore", "KA", "2 Tops"],
        ["Vikram Singh",  "+91 7012345678", "682001", "750",     "online", "Kochi", "Kerala", "Soap pack"],
        ["",              "",           "",        "",            "",     "",       "",        ""],   # blank → skipped
        ["Solo Phone Only", "9000011111", "",  "",            "upi",  "",       "",        ""],
    ]
    csv_blob = csv_bytes(rows)
    mapping = {
        "Cust Name": "customer_name",
        "Phone":     "customer_phone",
        "Pin":       "pincode",
        "Amt":       "amount",
        "PayMode":   "payment_mode",
        "City":      "city",
        "State":     "state",
        "Items":     "items",
    }
    files = {"file": ("commit_test.csv", csv_blob, "text/csv")}
    data = {"mapping": json.dumps(mapping), "save_default": "false"}
    r = requests.post(f"{BASE}/orders/import/commit", headers=auth(tok),
                      files=files, data=data, timeout=TIMEOUT)
    record("2a Commit valid mapping → 200", r.status_code == 200,
           f"status={r.status_code} body={r.text[:300]}")
    if r.status_code == 200:
        body = r.json()
        record("2a imported=4", body.get("imported") == 4, f"imported={body.get('imported')}")
        record("2a skipped=1 (blank row)", body.get("skipped") == 1, f"skipped={body.get('skipped')}")
        record("2a total=5 (rows in file)", body.get("total") == 5, f"total={body.get('total')}")
        record("2a format=csv", body.get("format") == "csv")

    # Now fetch them via /orders/pending?source=file and verify shape + coercions
    r = requests.get(f"{BASE}/orders/pending?source=file", headers=auth(tok), timeout=TIMEOUT)
    record("2j GET /orders/pending?source=file → 200", r.status_code == 200, f"status={r.status_code}")
    docs_check_done = False
    if r.status_code == 200:
        docs = r.json()
        record("2j at least 4 file-source pending orders returned",
               isinstance(docs, list) and len(docs) >= 4,
               f"count={len(docs) if isinstance(docs, list) else 'NA'}")
        # Track for cleanup
        for d in docs:
            if d.get("source") == "file" and d.get("id"):
                created_pending_ids.append(d["id"])
        # Find one of our committed rows
        suresh = next((d for d in docs if d.get("customer_name") == "Suresh Patel"), None)
        if suresh:
            docs_check_done = True
            record("2g Numeric coercion ₹1,250.50 → 1250.5",
                   suresh.get("amount") == 1250.5, f"amount={suresh.get('amount')}")
            record("2h payment_mode 'cod' → 'COD'",
                   suresh.get("payment_mode") == "COD", f"payment_mode={suresh.get('payment_mode')}")
            record("2i pincode '395 002' → '395002'",
                   suresh.get("pincode") == "395002", f"pincode={suresh.get('pincode')}")
            record("2k source=file", suresh.get("source") == "file")
            sm = suresh.get("source_meta") or {}
            record("2k source_meta has filename",
                   sm.get("filename") == "commit_test.csv", f"sm={sm}")
            record("2k source_meta has format=csv", sm.get("format") == "csv")
            record("2k source_meta has imported_at", bool(sm.get("imported_at")))
            record("2k source_meta has row_index (>=2)",
                   isinstance(sm.get("row_index"), int) and sm.get("row_index") >= 2,
                   f"row_index={sm.get('row_index')}")
        else:
            record("2g/h/i/k Could not locate Suresh Patel doc", False, "missing")

        # Check Megha Iyer (Paid → PAID, '560-001' → '560001')
        megha = next((d for d in docs if d.get("customer_name") == "Megha Iyer"), None)
        if megha:
            record("2h-2 'Paid' → 'PAID'", megha.get("payment_mode") == "PAID",
                   f"payment_mode={megha.get('payment_mode')}")
            record("2i-2 '560-001' → '560001'", megha.get("pincode") == "560001",
                   f"pincode={megha.get('pincode')}")
        # Vikram (online → PAID)
        vikram = next((d for d in docs if d.get("customer_name") == "Vikram Singh"), None)
        if vikram:
            record("2h-3 'online' → 'PAID'", vikram.get("payment_mode") == "PAID",
                   f"payment_mode={vikram.get('payment_mode')}")
        # Solo Phone Only — phone-only row imported (skipped only if BOTH blank)
        solo = next((d for d in docs if d.get("customer_phone") == "9000011111"), None)
        record("2f Phone-only row imported (name blank but phone present)",
               solo is not None and solo.get("source") == "file")
        if solo:
            record("2h-4 'upi' → 'PAID'", solo.get("payment_mode") == "PAID",
                   f"payment_mode={solo.get('payment_mode')}")


def test_commit_validation_errors(tok: str):
    print("\n── 2 negative cases ──")

    rows = [
        ["Foo", "Bar"],
        ["a",   "b"],
    ]
    csv_blob = csv_bytes(rows)

    # 2b. Mapping without name AND phone → 400
    mapping = {"Foo": "city", "Bar": "state"}
    files = {"file": ("nocn.csv", csv_blob, "text/csv")}
    data = {"mapping": json.dumps(mapping), "save_default": "false"}
    r = requests.post(f"{BASE}/orders/import/commit", headers=auth(tok),
                      files=files, data=data, timeout=TIMEOUT)
    record("2b Mapping without name+phone → 400", r.status_code == 400,
           f"status={r.status_code}")
    if r.status_code == 400:
        det = (r.json().get("detail") or "").lower()
        record("2b detail mentions 'customer_name' or 'customer_phone'",
               "customer_name" in det or "customer_phone" in det
               or ("name" in det and "phone" in det),
               f"detail={det}")

    # 2c. Mapping references unknown schema field → 400
    mapping = {"Foo": "definitely_not_a_field", "Bar": "customer_name"}
    files = {"file": ("unkfield.csv", csv_bytes(rows), "text/csv")}
    data = {"mapping": json.dumps(mapping), "save_default": "false"}
    r = requests.post(f"{BASE}/orders/import/commit", headers=auth(tok),
                      files=files, data=data, timeout=TIMEOUT)
    record("2c Mapping with unknown schema field → 400", r.status_code == 400,
           f"status={r.status_code}")
    if r.status_code == 400:
        det = (r.json().get("detail") or "").lower()
        record("2c detail mentions 'unknown schema field'",
               "unknown schema field" in det, f"detail={det}")

    # 2c-2. Invalid JSON mapping
    files = {"file": ("invjson.csv", csv_bytes(rows), "text/csv")}
    data = {"mapping": "not json at all", "save_default": "false"}
    r = requests.post(f"{BASE}/orders/import/commit", headers=auth(tok),
                      files=files, data=data, timeout=TIMEOUT)
    record("2c-2 Invalid mapping JSON → 400", r.status_code == 400,
           f"status={r.status_code}")
    if r.status_code == 400:
        det = (r.json().get("detail") or "").lower()
        record("2c-2 detail mentions 'invalid mapping json'",
               "invalid mapping json" in det, f"detail={det}")


def test_commit_save_default(tok: str):
    print("\n── 2d. save_default round-trip ──")
    rows = [
        ["Cust Name 9", "Phone 9", "Pin 9", "Amt 9", "PayMode 9"],
        ["Saver",       "9001112233", "400001", "10",  "cod"],
    ]
    mapping = {
        "Cust Name 9":  "customer_name",
        "Phone 9":      "customer_phone",
        "Pin 9":        "pincode",
        "Amt 9":        "amount",
        "PayMode 9":    "payment_mode",
    }
    files = {"file": ("save_def.csv", csv_bytes(rows), "text/csv")}
    data = {"mapping": json.dumps(mapping), "save_default": "true"}
    r = requests.post(f"{BASE}/orders/import/commit", headers=auth(tok),
                      files=files, data=data, timeout=TIMEOUT)
    record("2d save_default=true commit → 200", r.status_code == 200,
           f"status={r.status_code}")

    # GET should return the new mapping
    r = requests.get(f"{BASE}/me/file-import-mapping", headers=auth(tok), timeout=TIMEOUT)
    if r.status_code == 200:
        m = r.json().get("mapping", {})
        record("2d GET /me/file-import-mapping returns saved mapping",
               m == mapping, f"mapping={m}")
    # Cleanup imported pending orders for this user
    rl = requests.get(f"{BASE}/orders/pending?source=file", headers=auth(tok), timeout=TIMEOUT)
    if rl.status_code == 200:
        for d in rl.json():
            if d.get("id"):
                created_pending_ids.append(d["id"])


def test_commit_too_many_rows(tok: str):
    print("\n── 2e. > 5000 rows → 413 ──")
    headers = ["Customer Name", "Customer Phone"]
    big_rows = [headers] + [[f"User {i}", f"90000{i:05d}"] for i in range(5005)]
    csv_blob = csv_bytes(big_rows)
    mapping = {"Customer Name": "customer_name", "Customer Phone": "customer_phone"}
    files = {"file": ("too_many.csv", csv_blob, "text/csv")}
    data = {"mapping": json.dumps(mapping), "save_default": "false"}
    r = requests.post(f"{BASE}/orders/import/commit", headers=auth(tok),
                      files=files, data=data, timeout=60)
    record("2e > 5000 rows → 413", r.status_code == 413,
           f"status={r.status_code}")
    if r.status_code == 413:
        det = (r.json().get("detail") or "").lower()
        record("2e detail mentions 'too many'",
               "too many" in det or "max" in det, f"detail={det}")


def cleanup_pending(tok: str):
    print("\n── Cleanup imported pending orders ──")
    # Final fetch + delete
    rl = requests.get(f"{BASE}/orders/pending?source=file", headers=auth(tok), timeout=TIMEOUT)
    if rl.status_code == 200:
        for d in rl.json():
            if d.get("id"):
                created_pending_ids.append(d["id"])
    deleted = 0
    for oid in set(created_pending_ids):
        try:
            r = requests.delete(f"{BASE}/orders/pending/{oid}",
                                headers=auth(tok), timeout=TIMEOUT)
            if r.status_code == 200:
                deleted += 1
        except Exception:
            pass
    print(f"   deleted {deleted} pending rows")


# ════════════════════════════════════════════════════════════════════
# Smoke regression
# ════════════════════════════════════════════════════════════════════
def test_smoke_regression(tok: str):
    print("\n── 5. SMOKE REGRESSION ──")

    # 5.1 GET /orders/pending (no filter)
    r = requests.get(f"{BASE}/orders/pending", headers=auth(tok), timeout=TIMEOUT)
    record("5.1 GET /orders/pending (no filter) → 200",
           r.status_code == 200, f"status={r.status_code}")

    for src in ("paste", "sheet", "file"):
        r = requests.get(f"{BASE}/orders/pending?source={src}", headers=auth(tok), timeout=TIMEOUT)
        record(f"5.1 GET /orders/pending?source={src} → 200",
               r.status_code == 200, f"status={r.status_code}")

    # 5.2 GET /orders/pending-count
    r = requests.get(f"{BASE}/orders/pending-count", headers=auth(tok), timeout=TIMEOUT)
    record("5.2 GET /orders/pending-count → 200",
           r.status_code == 200, f"status={r.status_code}")

    # 5.3 POST /orders/pending/{id}/ship — create a paste pending order, ship it, delete the shipment
    paste_text = (
        "Smoke Reg Cust\n"
        "9999000011\n"
        "Test Address Lane\n"
        "Surat\n"
        "Gujarat\n"
        "395001\n"
        "AMOUNT: 100\n"
        "PAYMENT_MODE: COD"
    )
    r = requests.post(f"{BASE}/smart-paste",
                      headers={**auth(tok), "Content-Type": "application/json"},
                      data=json.dumps({"raw_text": paste_text}), timeout=TIMEOUT)
    pending_id_for_ship = None
    if r.status_code == 200:
        pending_id_for_ship = r.json().get("id")
        record("5.3 setup POST /smart-paste → 200 with id",
               bool(pending_id_for_ship), f"id={pending_id_for_ship}")
    else:
        record("5.3 setup POST /smart-paste → 200", False,
               f"status={r.status_code} body={r.text[:200]}")

    # Need a courier id
    courier_id = None
    rc = requests.get(f"{BASE}/couriers", headers=auth(tok), timeout=TIMEOUT)
    if rc.status_code == 200 and rc.json():
        courier_id = rc.json()[0].get("id")

    if pending_id_for_ship and courier_id:
        r = requests.post(
            f"{BASE}/orders/pending/{pending_id_for_ship}/ship",
            headers={**auth(tok), "Content-Type": "application/json"},
            data=json.dumps({"courier_id": courier_id}),
            timeout=TIMEOUT,
        )
        ok = r.status_code == 200
        record("5.3 POST /orders/pending/{id}/ship → 200", ok, f"status={r.status_code} body={r.text[:200]}")
        if ok:
            ship_id = r.json().get("id")
            # cleanup the resulting shipment
            if ship_id:
                rd = requests.delete(f"{BASE}/shipments/{ship_id}",
                                     headers=auth(tok), timeout=TIMEOUT)
                record("5.3 cleanup DELETE /shipments/{id} → 200",
                       rd.status_code == 200, f"status={rd.status_code}")

    # 5.4 DELETE /orders/pending/{id} — already exercised in cleanup. Quick fresh-order test:
    r = requests.post(f"{BASE}/smart-paste",
                      headers={**auth(tok), "Content-Type": "application/json"},
                      data=json.dumps({"raw_text":
                          "Del Test\n9999111100\nAddr\nCity\nState\n395001\nAMOUNT:50"}),
                      timeout=TIMEOUT)
    if r.status_code == 200:
        oid = r.json().get("id")
        rd = requests.delete(f"{BASE}/orders/pending/{oid}",
                             headers=auth(tok), timeout=TIMEOUT)
        record("5.4 DELETE /orders/pending/{id} → 200",
               rd.status_code == 200, f"status={rd.status_code}")

    # 5.5 GET /me/team-members
    r = requests.get(f"{BASE}/me/team-members", headers=auth(tok), timeout=TIMEOUT)
    record("5.5 GET /me/team-members → 200",
           r.status_code == 200, f"status={r.status_code}")

    # 5.6 POST /me/team-members/pay-extra (wallet — likely 402 insufficient or 200; both ok)
    r = requests.post(f"{BASE}/me/team-members/pay-extra",
                      headers={**auth(tok), "Content-Type": "application/json"},
                      data=json.dumps({"method": "wallet"}),
                      timeout=TIMEOUT)
    # 200 ok, 400 (extra disabled), 402 (insufficient balance) all acceptable as endpoint reachability
    ok = r.status_code in (200, 400, 402)
    record("5.6 POST /me/team-members/pay-extra reachable",
           ok, f"status={r.status_code} body={r.text[:200]}")

    # 5.7 GET /me/notification-prefs
    r = requests.get(f"{BASE}/me/notification-prefs", headers=auth(tok), timeout=TIMEOUT)
    record("5.7 GET /me/notification-prefs → 200",
           r.status_code == 200, f"status={r.status_code}")

    # 5.8 POST /me/cancel-subscription
    r = requests.post(f"{BASE}/me/cancel-subscription",
                      headers={**auth(tok), "Content-Type": "application/json"},
                      data=json.dumps({"reason": "regression test"}),
                      timeout=TIMEOUT)
    ok = r.status_code in (200, 400, 409)  # plan-state dependent; reachable is enough
    record("5.8 POST /me/cancel-subscription reachable",
           ok, f"status={r.status_code} body={r.text[:200]}")


# ════════════════════════════════════════════════════════════════════
# main
# ════════════════════════════════════════════════════════════════════
def main():
    tok = login(ADMIN_EMAIL, ADMIN_PASSWORD)
    if not tok:
        print("Cannot login as admin — aborting"); sys.exit(1)
    print(f"Logged in as {ADMIN_EMAIL}")

    # Pre-cleanup any stale file-source pending orders to keep things sane
    rl = requests.get(f"{BASE}/orders/pending?source=file", headers=auth(tok), timeout=TIMEOUT)
    if rl.status_code == 200:
        for d in rl.json():
            if d.get("id"):
                requests.delete(f"{BASE}/orders/pending/{d['id']}",
                                headers=auth(tok), timeout=TIMEOUT)
        print(f"Pre-cleanup removed {len(rl.json())} prior file-source pending rows")

    # Tests
    test_preview(tok)
    test_mapping_get_put(tok)
    test_commit_happy_path(tok)
    test_commit_validation_errors(tok)
    test_commit_save_default(tok)
    test_commit_too_many_rows(tok)
    test_smoke_regression(tok)
    cleanup_pending(tok)

    # Reset the file-import mapping to {} so we don't pollute the user
    requests.put(f"{BASE}/me/file-import-mapping",
                 headers={**auth(tok), "Content-Type": "application/json"},
                 data=json.dumps({"mapping": {}}), timeout=TIMEOUT)

    # Summary
    total = len(results)
    passed = sum(1 for r in results if r["ok"])
    failed = total - passed
    print("\n" + "=" * 60)
    print(f"PASSED: {passed} / {total}")
    print(f"FAILED: {failed}")
    if failed:
        print("\nFailures:")
        for r in results:
            if not r["ok"]:
                print(f"  ❌ {r['name']} — {r['info']}")
    print("=" * 60)
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
