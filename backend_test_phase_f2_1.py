"""
Phase F2.1 — CSV/XLSX Import: Status + Timestamp + Custom-Field Mapping.
Backend regression test against the public preview URL.

Scenarios A/B/C/D/E/F/G as per the review request in test_result.md.
"""
import io
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

BASE = "https://logistics-hub-740.preview.emergentagent.com/api"
TIMEOUT = 30

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASSWORD = "Admin@12345"

results: List[Dict[str, Any]] = []
# Cleanup trackers
created_pending_ids: List[str] = []
created_shipment_ids: List[str] = []
created_custom_field_ids: List[str] = []
created_courier_ids: List[str] = []


def record(name: str, ok: bool, info: str = ""):
    results.append({"name": name, "ok": ok, "info": info})
    flag = "✅" if ok else "❌"
    msg = f"{flag} {name}"
    if info:
        msg += f" — {info}"
    print(msg, flush=True)


def login(email: str, password: str) -> Optional[str]:
    try:
        r = requests.post(f"{BASE}/auth/login",
                          json={"email": email, "password": password},
                          timeout=TIMEOUT)
        if r.status_code != 200:
            print(f"login failed: {r.status_code} {r.text[:300]}")
            return None
        return r.json().get("token")
    except Exception as e:
        print(f"login error: {e}")
        return None


def auth(tok: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {tok}"}


def csv_bytes(rows: List[List[str]], with_bom: bool = False) -> bytes:
    sio = io.StringIO()
    import csv as _csv
    w = _csv.writer(sio)
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


def upload_preview(tok: str, fname: str, blob: bytes) -> requests.Response:
    files = {"file": (fname, blob,
                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                      if fname.endswith(".xlsx") else "text/csv")}
    return requests.post(f"{BASE}/orders/import/preview", headers=auth(tok),
                         files=files, timeout=TIMEOUT)


def upload_commit(tok: str, fname: str, blob: bytes,
                  mapping: Dict[str, str], save_default: bool = False) -> requests.Response:
    files = {"file": (fname, blob,
                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                      if fname.endswith(".xlsx") else "text/csv")}
    data = {
        "mapping": json.dumps(mapping),
        "save_default": "true" if save_default else "false",
    }
    return requests.post(f"{BASE}/orders/import/commit", headers=auth(tok),
                         files=files, data=data, timeout=TIMEOUT)


def fetch_pending_by_phone(tok: str, phone: str) -> List[Dict[str, Any]]:
    r = requests.get(f"{BASE}/orders/pending?source=file",
                     headers=auth(tok), timeout=TIMEOUT)
    if r.status_code != 200:
        return []
    return [p for p in r.json() if (p.get("customer_phone") or "") == phone]


def ensure_courier(tok: str) -> Optional[str]:
    """Make sure the user has at least one courier; return its id."""
    r = requests.get(f"{BASE}/couriers", headers=auth(tok), timeout=TIMEOUT)
    if r.status_code == 200 and r.json():
        return r.json()[0]["id"]
    # Create a fresh courier
    body = {
        "name": "Phase F2.1 Test Courier",
        "series_prefix": "F21T",
        "next_number": 1,
        "number_padding": 4,
        "contact_phone": "9999999999",
        "tracking_url_template": "",
    }
    r = requests.post(f"{BASE}/couriers", headers=auth(tok),
                      json=body, timeout=TIMEOUT)
    if r.status_code == 200:
        cid = r.json().get("id")
        if cid:
            created_courier_ids.append(cid)
        return cid
    return None


def ship_pending(tok: str, pending_id: str, courier_id: str) -> requests.Response:
    return requests.post(f"{BASE}/orders/pending/{pending_id}/ship",
                         headers=auth(tok),
                         json={"courier_id": courier_id, "overrides": {}},
                         timeout=TIMEOUT)


def cleanup(tok: str):
    print("\n--- CLEANUP ---", flush=True)
    for pid in created_pending_ids:
        try:
            requests.delete(f"{BASE}/orders/pending/{pid}",
                            headers=auth(tok), timeout=TIMEOUT)
        except Exception:
            pass
    for sid in created_shipment_ids:
        try:
            requests.delete(f"{BASE}/shipments/{sid}",
                            headers=auth(tok), timeout=TIMEOUT)
        except Exception:
            pass
    for cf in created_custom_field_ids:
        try:
            requests.delete(f"{BASE}/me/custom-fields/{cf}",
                            headers=auth(tok), timeout=TIMEOUT)
        except Exception:
            pass
    for cid in created_courier_ids:
        try:
            requests.delete(f"{BASE}/couriers/{cid}",
                            headers=auth(tok), timeout=TIMEOUT)
        except Exception:
            pass
    print("Cleanup done", flush=True)


# ═════════════════════ Scenario A — Status mapping ═════════════════════════
def test_status_mapping(tok: str):
    print("\n========= A) STATUS MAPPING =========", flush=True)
    # Use unique tag to avoid collisions
    tag = f"F21A{int(time.time())}"
    headers = ["Customer Name", "Phone", "Status"]
    rows_data = [
        ["Aarav Sharma",   f"9{tag}1", "Shipped"],
        ["Priya Verma",    f"9{tag}2", "delivered"],
        ["Rohit Singh",    f"9{tag}3", "DISPATCHED"],
        ["Neha Gupta",     f"9{tag}4", "ready to ship"],
        ["Sanjay Mehta",   f"9{tag}5", "rto"],
        ["Anita Desai",    f"9{tag}6", "Cancelled"],
        ["Kunal Patel",    f"9{tag}7", "Out for delivery"],
        ["Vikram Joshi",   f"9{tag}8", "garbage_x"],
    ]
    blob = csv_bytes([headers] + rows_data)
    # Truncate phone numbers - they may exceed 10 digits via the tag.
    # Reduce to last 10 digits but keep uniqueness — re-shape:
    short_tag = str(int(time.time()))[-6:]   # 6 digits
    rows_data = [
        [r[0], f"9{short_tag}{i+1}"[:10], r[2]] for i, r in enumerate(rows_data)
    ]
    blob = csv_bytes([headers] + rows_data)

    # Preview
    r = upload_preview(tok, "statusA.csv", blob)
    record("A1 preview status returns 200", r.status_code == 200,
           f"got {r.status_code} {r.text[:120]}")
    if r.status_code != 200:
        return
    j = r.json()
    record("A1 suggested[Status] == 'status'",
           j.get("suggested", {}).get("Status") == "status",
           f"suggested={j.get('suggested')}")

    # Commit
    mapping = {
        "Status":        "status",
        "Customer Name": "customer_name",
        "Phone":         "customer_phone",
    }
    r = upload_commit(tok, "statusA.csv", blob, mapping)
    record("A2 commit returns 200", r.status_code == 200,
           f"got {r.status_code} {r.text[:200]}")
    if r.status_code != 200:
        return
    j = r.json()
    record("A2 imported == 8", j.get("imported") == 8,
           f"imported={j.get('imported')} skipped={j.get('skipped')}")

    # Fetch pending and verify imported_status mapping
    expected_status_by_phone = {
        rows_data[0][1]: "Shipped",
        rows_data[1][1]: "Delivered",
        rows_data[2][1]: "Ready to Ship",
        rows_data[3][1]: "Ready to Ship",
        rows_data[4][1]: "Returned",
        rows_data[5][1]: "Cancelled",
        rows_data[6][1]: "Shipped",
        rows_data[7][1]: "Garbage_X",
    }
    r = requests.get(f"{BASE}/orders/pending?source=file",
                     headers=auth(tok), timeout=TIMEOUT)
    pendings = r.json() if r.status_code == 200 else []
    by_phone = {p.get("customer_phone"): p for p in pendings}
    for phone, want in expected_status_by_phone.items():
        po = by_phone.get(phone)
        if po:
            created_pending_ids.append(po["id"])
        got = (po or {}).get("imported_status")
        record(f"A3 imported_status for phone {phone} == {want!r}",
               got == want, f"got={got!r}")

    # Critical guard — none stored as "Dispatch"
    bad = [p for p in pendings
           if p.get("customer_phone") in expected_status_by_phone
           and (p.get("imported_status") or "").lower() == "dispatch"]
    record("A3.guard no row stored as literal 'Dispatch'",
           len(bad) == 0, f"bad rows={len(bad)}")

    # Pick the "Shipped" row and ship it
    ship_phone = rows_data[0][1]   # "Shipped"
    po = by_phone.get(ship_phone)
    if po is None:
        record("A4 found 'Shipped' pending row", False, "missing")
        return
    courier_id = ensure_courier(tok)
    record("A4 courier exists", bool(courier_id), str(courier_id))
    if not courier_id:
        return
    r = ship_pending(tok, po["id"], courier_id)
    record("A4 ship pending → 200", r.status_code == 200,
           f"got {r.status_code} {r.text[:200]}")
    if r.status_code == 200:
        ship = r.json()
        sid = ship.get("id")
        if sid:
            created_shipment_ids.append(sid)
            # Once shipped, the pending row is marked shipped — drop from cleanup
            try:
                created_pending_ids.remove(po["id"])
            except ValueError:
                pass
        record("A4 Shipment.status == 'Shipped'",
               ship.get("status") == "Shipped",
               f"got status={ship.get('status')!r}")
        record("A4 Shipment.status NOT 'Pending'",
               ship.get("status") != "Pending",
               f"got status={ship.get('status')!r}")


# ═════════════════════ Scenario B — Timestamp mapping ═════════════════════════
def test_timestamp_mapping(tok: str):
    print("\n========= B) TIMESTAMP MAPPING =========", flush=True)

    # B1 — CSV with DD/MM/YYYY HH:MM:SS
    short_tag = str(int(time.time()))[-6:]
    csv_phone = f"81{short_tag}"[:10]
    headers = ["Name", "Phone", "Timestamp"]
    rows_data = [["Bharti Modi", csv_phone, "29/04/2026 14:30:00"]]
    blob = csv_bytes([headers] + rows_data)

    r = upload_preview(tok, "tsB.csv", blob)
    record("B1 preview returns 200", r.status_code == 200)
    if r.status_code == 200:
        sug = r.json().get("suggested", {})
        record("B1 suggested[Timestamp] == 'created_at_override'",
               sug.get("Timestamp") == "created_at_override",
               f"suggested={sug}")

    mapping = {
        "Name":      "customer_name",
        "Phone":     "customer_phone",
        "Timestamp": "created_at_override",
    }
    r = upload_commit(tok, "tsB.csv", blob, mapping)
    record("B1 commit returns 200", r.status_code == 200,
           f"got {r.status_code} {r.text[:200]}")
    pos = fetch_pending_by_phone(tok, csv_phone)
    if not pos:
        record("B1 found pending row", False)
        return
    po = pos[0]
    created_pending_ids.append(po["id"])
    record("B1 imported_at parsed (non-empty)",
           bool(po.get("imported_at")),
           f"imported_at={po.get('imported_at')!r}")
    # Should parse 29/04/2026 14:30:00 as ISO that contains 2026-04-29T14:30
    iso = po.get("imported_at") or ""
    record("B1 imported_at contains '2026-04-29T14:30'",
           "2026-04-29T14:30" in iso, f"iso={iso!r}")

    # Ship it & confirm Shipment.created_at == imported_at
    courier_id = ensure_courier(tok)
    if courier_id:
        r = ship_pending(tok, po["id"], courier_id)
        if r.status_code == 200:
            ship = r.json()
            sid = ship.get("id")
            if sid:
                created_shipment_ids.append(sid)
                try:
                    created_pending_ids.remove(po["id"])
                except ValueError:
                    pass
            record("B1 Shipment.created_at == imported_at",
                   ship.get("created_at") == po.get("imported_at"),
                   f"shipment.created_at={ship.get('created_at')!r} expected={po.get('imported_at')!r}")
        else:
            record("B1 ship returned 200", False, f"{r.status_code} {r.text[:200]}")

    # B2 — XLSX with native datetime cell
    xlsx_phone = f"82{short_tag}"[:10]
    real_dt = datetime(2026, 3, 15, 9, 45, 0)
    rows_x = [["Name", "Phone", "Timestamp"],
              ["Charu Iyer", xlsx_phone, real_dt]]
    xblob = xlsx_bytes(rows_x)
    mapping_x = {"Name": "customer_name",
                 "Phone": "customer_phone",
                 "Timestamp": "created_at_override"}
    r = upload_commit(tok, "tsB.xlsx", xblob, mapping_x)
    record("B2 xlsx commit returns 200", r.status_code == 200,
           f"got {r.status_code} {r.text[:200]}")
    pos = fetch_pending_by_phone(tok, xlsx_phone)
    if pos:
        po2 = pos[0]
        created_pending_ids.append(po2["id"])
        ia = po2.get("imported_at") or ""
        record("B2 xlsx imported_at contains '2026-03-15T09:45'",
               "2026-03-15T09:45" in ia, f"imported_at={ia!r}")
        # Ship + verify created_at
        if courier_id:
            r2 = ship_pending(tok, po2["id"], courier_id)
            if r2.status_code == 200:
                ship2 = r2.json()
                if ship2.get("id"):
                    created_shipment_ids.append(ship2["id"])
                    try:
                        created_pending_ids.remove(po2["id"])
                    except ValueError:
                        pass
                record("B2 Shipment.created_at == imported_at",
                       ship2.get("created_at") == ia,
                       f"got={ship2.get('created_at')!r} want={ia!r}")
    else:
        record("B2 found xlsx pending", False)

    # B3 — bad timestamp → import OK, imported_at = "", Shipment.created_at = now()
    bad_phone = f"83{short_tag}"[:10]
    rows_b = [["Name", "Phone", "Timestamp"], ["Deepa Rao", bad_phone, "not a date"]]
    blob_b = csv_bytes(rows_b)
    r = upload_commit(tok, "tsB3.csv", blob_b, mapping)
    record("B3 bad timestamp commit 200", r.status_code == 200,
           f"got {r.status_code} {r.text[:200]}")
    pos = fetch_pending_by_phone(tok, bad_phone)
    if pos:
        po3 = pos[0]
        created_pending_ids.append(po3["id"])
        record("B3 imported_at == '' for bad timestamp",
               (po3.get("imported_at") or "") == "",
               f"imported_at={po3.get('imported_at')!r}")
        if courier_id:
            now_before = datetime.now(timezone.utc)
            r3 = ship_pending(tok, po3["id"], courier_id)
            if r3.status_code == 200:
                ship3 = r3.json()
                if ship3.get("id"):
                    created_shipment_ids.append(ship3["id"])
                    try:
                        created_pending_ids.remove(po3["id"])
                    except ValueError:
                        pass
                # created_at must be parseable & close to now (within 5min)
                ca = ship3.get("created_at") or ""
                ok_now = False
                try:
                    cdt = datetime.fromisoformat(ca.replace("Z", "+00:00"))
                    if cdt.tzinfo is None:
                        cdt = cdt.replace(tzinfo=timezone.utc)
                    delta = abs((cdt - now_before).total_seconds())
                    ok_now = delta < 300
                except Exception:
                    pass
                record("B3 Shipment.created_at ≈ now() (within 5min)",
                       ok_now, f"created_at={ca!r}")


# ═════════════════════ Scenario C — Custom Field mapping ═════════════════════
def test_custom_field_mapping(tok: str):
    print("\n========= C) CUSTOM FIELD MAPPING =========", flush=True)

    # First, fetch existing custom fields and find a free column letter
    r = requests.get(f"{BASE}/me/custom-fields", headers=auth(tok), timeout=TIMEOUT)
    used_cols = set()
    if r.status_code == 200:
        # response shape may be a dict {fields: [...]} or list — check both
        body = r.json()
        if isinstance(body, dict):
            fields = body.get("fields", [])
        else:
            fields = body
        for f in fields:
            cl = (f.get("column_letter") or "").upper()
            if cl:
                used_cols.add(cl)
    chosen_col = None
    for letter in ["W", "Y", "Z", "X", "V", "U", "AA", "AB"]:
        if letter not in used_cols:
            chosen_col = letter
            break
    if not chosen_col:
        chosen_col = "AC"

    # Create custom field
    payload = {
        "name": "Pet Type",
        "column_letter": chosen_col,
        "field_type": "text",
        "show_in_form": True,
        "show_in_smart_paste": True,
    }
    r = requests.post(f"{BASE}/me/custom-fields", headers=auth(tok),
                      json=payload, timeout=TIMEOUT)
    record("C1 create custom-field 'Pet Type' returns 200",
           r.status_code == 200, f"got {r.status_code} {r.text[:300]}")
    if r.status_code != 200:
        return
    cf = r.json()
    cf_id = cf.get("id")
    if cf_id:
        created_custom_field_ids.append(cf_id)
    record("C1 custom field has id", bool(cf_id), f"id={cf_id}")

    # CSV with column "Pet Type"
    short_tag = str(int(time.time()))[-6:]
    phone = f"84{short_tag}"[:10]
    headers = ["Name", "Phone", "Pet Type"]
    rows_data = [["Esha Khan", phone, "Golden Retriever"]]
    blob = csv_bytes([headers] + rows_data)

    # Preview — should auto-suggest custom:<id>
    r = upload_preview(tok, "petC.csv", blob)
    record("C2 preview 200", r.status_code == 200,
           f"got {r.status_code} {r.text[:120]}")
    if r.status_code == 200:
        sug = r.json().get("suggested", {})
        expected = f"custom:{cf_id}"
        record("C2 suggested[Pet Type] auto = custom:<id>",
               sug.get("Pet Type") == expected,
               f"got={sug.get('Pet Type')!r} expected={expected!r}")
        # Note: this may FAIL because import_schema reads cf['label'] but the model uses 'name'

    # Commit explicitly with custom mapping
    mapping = {
        "Name":      "customer_name",
        "Phone":     "customer_phone",
        "Pet Type":  f"custom:{cf_id}",
    }
    r = upload_commit(tok, "petC.csv", blob, mapping)
    record("C3 commit 200", r.status_code == 200,
           f"got {r.status_code} {r.text[:300]}")
    if r.status_code != 200:
        return
    pos = fetch_pending_by_phone(tok, phone)
    if not pos:
        record("C3 pending row found", False)
        return
    po = pos[0]
    created_pending_ids.append(po["id"])
    cv = po.get("custom_values") or {}
    record("C3 PendingOrder.custom_values has entry under cf_id",
           cv.get(cf_id) == "Golden Retriever",
           f"custom_values={cv}")

    # Ship — verify shipment.custom_values
    # Note: ship_pending_order copies order.custom_values via _write_custom_values_to_user_sheet_bg
    # but does it persist on shipment? Check.
    courier_id = ensure_courier(tok)
    if courier_id:
        r = ship_pending(tok, po["id"], courier_id)
        if r.status_code == 200:
            ship = r.json()
            if ship.get("id"):
                created_shipment_ids.append(ship["id"])
                try:
                    created_pending_ids.remove(po["id"])
                except ValueError:
                    pass
            scv = ship.get("custom_values") or {}
            record("C4 Shipment.custom_values has same entry",
                   scv.get(cf_id) == "Golden Retriever",
                   f"shipment.custom_values={scv}")
        else:
            record("C4 ship 200", False, f"{r.status_code} {r.text[:200]}")


# ═════════════════════ Scenario D — Alias regression ═════════════════════════
def test_alias_regression(tok: str):
    print("\n========= D) ALIAS REGRESSION =========", flush=True)
    headers = ["status", "stage", "order_status", "shipment_status",
               "timestamp", "date", "order_date", "created_at",
               "Customer Name", "Phone"]
    short_tag = str(int(time.time()))[-6:]
    rows_data = [["Shipped", "Pending", "Delivered", "Cancelled",
                  "2026-04-01", "2026-04-02", "2026-04-03", "2026-04-04",
                  "Alias Tester", f"85{short_tag}"[:10]]]
    blob = csv_bytes([headers] + rows_data)
    r = upload_preview(tok, "aliasD.csv", blob)
    record("D preview 200", r.status_code == 200,
           f"got {r.status_code} {r.text[:120]}")
    if r.status_code != 200:
        return
    sug = r.json().get("suggested", {})
    for col in ["status", "stage", "order_status", "shipment_status"]:
        record(f"D auto-map {col!r} → 'status'", sug.get(col) == "status",
               f"got={sug.get(col)!r}")
    for col in ["timestamp", "date", "order_date", "created_at"]:
        record(f"D auto-map {col!r} → 'created_at_override'",
               sug.get(col) == "created_at_override", f"got={sug.get(col)!r}")


# ═════════════════════ Scenario E — Dispatch cleanup guard ═════════════════════
def test_dispatch_cleanup(tok: str):
    print("\n========= E) DISPATCH CLEANUP GUARD =========", flush=True)
    short_tag = str(int(time.time()))[-6:]
    headers = ["Customer Name", "Phone", "Status"]
    p1 = f"86{short_tag}1"[:10]
    p2 = f"86{short_tag}2"[:10]
    rows_data = [
        ["Disp Tester1", p1, "Dispatch"],
        ["Disp Tester2", p2, "Dispatched"],
    ]
    blob = csv_bytes([headers] + rows_data)
    mapping = {"Customer Name": "customer_name",
               "Phone": "customer_phone",
               "Status": "status"}
    r = upload_commit(tok, "dispE.csv", blob, mapping)
    record("E commit 200", r.status_code == 200,
           f"got {r.status_code} {r.text[:200]}")
    if r.status_code != 200:
        return
    courier_id = ensure_courier(tok)
    for phone in [p1, p2]:
        pos = fetch_pending_by_phone(tok, phone)
        if not pos:
            record(f"E pending row for {phone}", False)
            continue
        po = pos[0]
        created_pending_ids.append(po["id"])
        record(f"E imported_status for phone {phone} == 'Ready to Ship'",
               (po.get("imported_status") or "") == "Ready to Ship",
               f"got={po.get('imported_status')!r}")
        if courier_id:
            r2 = ship_pending(tok, po["id"], courier_id)
            if r2.status_code == 200:
                ship = r2.json()
                if ship.get("id"):
                    created_shipment_ids.append(ship["id"])
                    try:
                        created_pending_ids.remove(po["id"])
                    except ValueError:
                        pass
                st = ship.get("status")
                record(f"E Shipment.status == 'Ready to Ship' (NOT 'Dispatch') for {phone}",
                       st == "Ready to Ship", f"got={st!r}")
            else:
                record(f"E ship pending 200 for {phone}", False,
                       f"{r2.status_code} {r2.text[:200]}")


# ═════════════════════ Scenario F — Phase F1 regression ═════════════════════════
def test_f1_regression(tok: str):
    print("\n========= F) PHASE F1 REGRESSION =========", flush=True)

    # F1 — preview/commit happy path with BOM CSV, payment mode column
    short_tag = str(int(time.time()))[-6:]
    headers = ["customer_name", "customer_phone", "address", "city", "state",
               "pincode", "amount", "payment_mode"]
    rows = [
        ["Faisal Ahmed", f"87{short_tag}1"[:10], "12 MG Rd",
         "Mumbai", "Maharashtra", "400001", "1500", "cod"],
        ["Geeta Patel", f"87{short_tag}2"[:10], "5 Park Lane",
         "Delhi", "Delhi", "110001", "2500", "PAID"],
        ["Hari Krishnan", f"87{short_tag}3"[:10], "9 Nehru Pl",
         "Chennai", "Tamil Nadu", "600001", "999", "online"],
    ]
    blob = csv_bytes([headers] + rows, with_bom=True)
    r = upload_preview(tok, "f1.csv", blob)
    record("F1 preview (BOM CSV) 200", r.status_code == 200,
           f"got {r.status_code}")
    if r.status_code == 200:
        sug = r.json().get("suggested", {})
        record("F1 suggested[customer_name] == 'customer_name'",
               sug.get("customer_name") == "customer_name")
        record("F1 suggested[payment_mode] == 'payment_mode'",
               sug.get("payment_mode") == "payment_mode")

    # Commit happy path
    mapping = {h: h for h in headers}
    mapping["address"] = "address"
    r = upload_commit(tok, "f1.csv", blob, mapping)
    record("F1 commit (BOM CSV) 200", r.status_code == 200,
           f"got {r.status_code} {r.text[:200]}")
    if r.status_code == 200:
        j = r.json()
        record("F1 imported == 3", j.get("imported") == 3,
               f"j={j}")
    # Verify payment_mode coercion + cleanup pendings
    for r_ in rows:
        pos = fetch_pending_by_phone(tok, r_[1])
        if pos:
            created_pending_ids.append(pos[0]["id"])
    pmodes = {}
    for r_ in rows:
        pos = fetch_pending_by_phone(tok, r_[1])
        if pos:
            pmodes[r_[1]] = pos[0].get("payment_mode")
    record("F1 payment_mode 'cod' → 'COD'",
           pmodes.get(rows[0][1]) == "COD", f"got={pmodes}")
    record("F1 payment_mode 'PAID' → 'PAID'",
           pmodes.get(rows[1][1]) == "PAID", f"got={pmodes}")
    record("F1 payment_mode 'online' → 'PAID'",
           pmodes.get(rows[2][1]) == "PAID", f"got={pmodes}")

    # source_meta presence
    if pos:
        record("F1 source_meta present in pending response",
               isinstance(pos[0].get("source_meta"), dict),
               f"source_meta={pos[0].get('source_meta')}")

    # Mapping validation: 400 unknown schema field
    bad_mapping = {"customer_name": "customer_name",
                   "customer_phone": "customer_phone",
                   "address": "totally_unknown_field"}
    r = upload_commit(tok, "f1bad.csv", blob, bad_mapping)
    record("F1 unknown schema field → 400",
           r.status_code == 400, f"got {r.status_code} {r.text[:200]}")

    # Mapping validation: 400 missing both name+phone
    short_blob = csv_bytes([["address"], ["12 MG Road"]])
    r = upload_commit(tok, "f1empty.csv", short_blob, {"address": "address"})
    record("F1 missing name+phone → 400",
           r.status_code == 400, f"got {r.status_code} {r.text[:200]}")

    # save_default round-trip
    sd_mapping = {"customer_name": "customer_name",
                  "customer_phone": "customer_phone"}
    blob_sd = csv_bytes([["customer_name", "customer_phone"],
                         ["Saved Default", f"88{short_tag}9"[:10]]])
    r = upload_commit(tok, "f1sd.csv", blob_sd, sd_mapping, save_default=True)
    record("F1 commit with save_default 200", r.status_code == 200,
           f"got {r.status_code} {r.text[:200]}")
    pos = fetch_pending_by_phone(tok, f"88{short_tag}9"[:10])
    if pos:
        created_pending_ids.append(pos[0]["id"])
    r = requests.get(f"{BASE}/me/file-import-mapping",
                     headers=auth(tok), timeout=TIMEOUT)
    record("F1 GET /me/file-import-mapping 200", r.status_code == 200)
    if r.status_code == 200:
        body = r.json()
        record("F1 saved mapping persisted",
               body.get("mapping", {}).get("customer_name") == "customer_name",
               f"mapping={body.get('mapping')}")


# ═════════════════════ Scenario G — file-import-mapping returns custom_fields ══
def test_mapping_endpoint_custom_fields(tok: str):
    print("\n========= G) /me/file-import-mapping → custom_fields ==========",
          flush=True)
    r = requests.get(f"{BASE}/me/file-import-mapping",
                     headers=auth(tok), timeout=TIMEOUT)
    record("G GET /me/file-import-mapping 200", r.status_code == 200)
    if r.status_code != 200:
        return
    body = r.json()
    record("G response has 'custom_fields' key",
           "custom_fields" in body, f"keys={list(body.keys())}")
    cf = body.get("custom_fields") or []
    record("G custom_fields is a list",
           isinstance(cf, list), f"type={type(cf).__name__}")
    if cf:
        record("G custom_fields entries have 'id' and 'label'",
               all(("id" in c and "label" in c) for c in cf),
               f"first={cf[0] if cf else None}")
        # Check whether any custom field's label is non-empty
        # (this surfaces the name vs label bug if any)
        any_nonempty = any((c.get("label") or "") for c in cf)
        record("G at least one custom field has non-empty 'label'",
               any_nonempty,
               f"all_labels={[c.get('label') for c in cf]}")


# ═════════════════════ Run ═════════════════════════
def main():
    tok = login(ADMIN_EMAIL, ADMIN_PASSWORD)
    if not tok:
        print("FATAL: could not log in admin@test.com")
        sys.exit(1)
    print(f"Logged in. Token len={len(tok)}", flush=True)

    try:
        test_status_mapping(tok)
        test_timestamp_mapping(tok)
        test_custom_field_mapping(tok)
        test_alias_regression(tok)
        test_dispatch_cleanup(tok)
        test_f1_regression(tok)
        test_mapping_endpoint_custom_fields(tok)
    finally:
        cleanup(tok)

    passed = sum(1 for r in results if r["ok"])
    failed = sum(1 for r in results if not r["ok"])
    print(f"\n=========== SUMMARY: {passed} passed / {failed} failed / "
          f"{len(results)} total ===========")
    if failed:
        print("\nFailures:")
        for r in results:
            if not r["ok"]:
                print(f"  ❌ {r['name']} — {r['info']}")
    return failed == 0


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
