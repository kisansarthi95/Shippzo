"""
Phase F2.2 — Webhook Ingest pipeline + Dispatch→Ready-to-Ship cleanup.
Backend regression test against the public preview URL.

Scenarios A–H per /app/test_result.md → "Phase F2.2" task. Uses requests.
"""
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
created_pending_ids: List[str] = []
created_shipment_ids: List[str] = []
created_custom_field_ids: List[str] = []
legacy_dispatch_ids: List[str] = []   # direct DB inserts to clean up via DELETE
created_courier_id: Optional[str] = None
saved_webhook_mapping: Optional[Dict[str, str]] = None


def record(name: str, ok: bool, info: str = ""):
    results.append({"name": name, "ok": ok, "info": info})
    flag = "PASS" if ok else "FAIL"
    msg = f"[{flag}] {name}"
    if info:
        msg += f" — {info}"
    print(msg, flush=True)


def login(email: str, password: str) -> Optional[str]:
    try:
        r = requests.post(
            f"{BASE}/auth/login",
            json={"email": email, "password": password},
            timeout=TIMEOUT,
        )
        if r.status_code != 200:
            print(f"login failed: {r.status_code} {r.text[:300]}")
            return None
        return r.json().get("token")
    except Exception as e:
        print(f"login error: {e}")
        return None


def auth(tok: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {tok}"}


def ensure_courier(tok: str) -> Optional[str]:
    global created_courier_id
    r = requests.get(f"{BASE}/couriers", headers=auth(tok), timeout=TIMEOUT)
    if r.status_code == 200 and r.json():
        return r.json()[0]["id"]
    body = {
        "name": "Phase F2.2 Test Courier",
        "series_prefix": "F22T",
        "next_number": 1,
        "number_padding": 4,
        "contact_phone": "9999999999",
        "tracking_url_template": "",
    }
    r = requests.post(
        f"{BASE}/couriers", headers=auth(tok), json=body, timeout=TIMEOUT,
    )
    if r.status_code == 200:
        cid = r.json().get("id")
        if cid:
            created_courier_id = cid
        return cid
    return None


# ════════════════════════════ A — Config CRUD ═══════════════════════════
def test_a_config_crud(tok: str) -> Optional[str]:
    print("\n========= A) WEBHOOK CONFIG CRUD =========", flush=True)
    secret_v1: Optional[str] = None

    # 1. Initial GET
    r = requests.get(
        f"{BASE}/me/webhook-config", headers=auth(tok), timeout=TIMEOUT,
    )
    record("A1 GET /me/webhook-config 200",
           r.status_code == 200, f"got {r.status_code} {r.text[:200]}")
    if r.status_code == 200:
        j = r.json()
        # NOTE: webhook may already be configured if we ran a previous round
        record("A1 response has 'configured' key",
               "configured" in j, f"keys={list(j.keys())}")
        record("A1 response.mapping is dict",
               isinstance(j.get("mapping"), dict),
               f"mapping={type(j.get('mapping')).__name__}")
        sf = j.get("schema_fields") or []
        record("A1 schema_fields includes 'status'",
               "status" in sf, f"len={len(sf)}")
        record("A1 schema_fields includes 'created_at_override'",
               "created_at_override" in sf, "")
        record("A1 custom_fields is list",
               isinstance(j.get("custom_fields"), list),
               f"type={type(j.get('custom_fields')).__name__}")

    # 2. Rotate
    r = requests.post(
        f"{BASE}/me/webhook-config/rotate",
        headers=auth(tok), timeout=TIMEOUT,
    )
    record("A2 POST rotate 200",
           r.status_code == 200, f"got {r.status_code} {r.text[:200]}")
    if r.status_code == 200:
        j = r.json()
        secret_v1 = j.get("secret") or ""
        url = j.get("url") or ""
        record("A2 secret length >= 22",
               len(secret_v1) >= 22, f"len={len(secret_v1)}")
        record("A2 url ends with /api/webhook/orders/<secret>",
               url.endswith(f"/api/webhook/orders/{secret_v1}"),
               f"url={url}")

    # 3. GET again — configured=true
    r = requests.get(
        f"{BASE}/me/webhook-config", headers=auth(tok), timeout=TIMEOUT,
    )
    if r.status_code == 200:
        j = r.json()
        record("A3 GET after rotate → configured=true",
               j.get("configured") is True, f"configured={j.get('configured')}")
        record("A3 secret matches v1",
               j.get("secret") == secret_v1, f"got={j.get('secret')[:10]}...")
    else:
        record("A3 GET after rotate", False, f"{r.status_code}")

    # 4. Rotate again — secret CHANGES
    r = requests.post(
        f"{BASE}/me/webhook-config/rotate",
        headers=auth(tok), timeout=TIMEOUT,
    )
    if r.status_code == 200:
        secret_v2 = r.json().get("secret") or ""
        record("A4 second rotate 200", True)
        record("A4 secret CHANGED on rotate",
               secret_v2 and secret_v2 != secret_v1,
               f"v1={secret_v1[:8]}... v2={secret_v2[:8]}...")
        # Old URL must 404 on POST
        r_old = requests.post(
            f"{BASE}/webhook/orders/{secret_v1}",
            json={"x": "y"}, timeout=TIMEOUT,
        )
        record("A4 POST to old URL → 404",
               r_old.status_code == 404, f"got {r_old.status_code}")
        return secret_v2
    record("A4 second rotate", False, f"got {r.status_code}")
    return secret_v1


# ════════════════════════════ B — Mapping save ═══════════════════════════
def test_b_mapping_save(tok: str) -> Optional[str]:
    """Save a valid mapping; also create a custom field and validate
    custom: pointer accepted; garbage rejected. Returns the cf_id."""
    global saved_webhook_mapping
    print("\n========= B) MAPPING SAVE =========", flush=True)

    valid = {
        "customer.name":   "customer_name",
        "customer.phone":  "customer_phone",
        "shipping.line1":  "address",
        "shipping.line2":  "address",
        "order.amount":    "amount",
        "order.status":    "status",
        "order.placed_at": "created_at_override",
    }
    r = requests.put(
        f"{BASE}/me/webhook-config",
        headers=auth(tok), json={"mapping": valid}, timeout=TIMEOUT,
    )
    record("B1 PUT valid mapping 200",
           r.status_code == 200, f"got {r.status_code} {r.text[:200]}")
    if r.status_code == 200:
        record("B1 ok=true", r.json().get("ok") is True,
               f"resp={r.json()}")
        saved_webhook_mapping = valid

    # B2 — unknown schema field
    bad = dict(valid)
    bad["customer.name"] = "foo"
    r = requests.put(
        f"{BASE}/me/webhook-config",
        headers=auth(tok), json={"mapping": bad}, timeout=TIMEOUT,
    )
    record("B2 PUT unknown schema field → 400",
           r.status_code == 400, f"got {r.status_code} {r.text[:200]}")

    # B3 — create a custom field, then map a key to it
    short = str(int(time.time()))[-6:]
    used_letters = set()
    g = requests.get(
        f"{BASE}/me/custom-fields", headers=auth(tok), timeout=TIMEOUT,
    )
    if g.status_code == 200:
        body = g.json()
        fields = body.get("fields", []) if isinstance(body, dict) else body
        for f in fields:
            cl = (f.get("column_letter") or "").upper()
            if cl:
                used_letters.add(cl)
    chosen_col = next(
        (l for l in ["W", "Y", "Z", "X", "V", "U", "AA", "AB", "AC", "AD"]
         if l not in used_letters), "AE",
    )
    cf_payload = {
        "name": f"Webhook Tag {short}",
        "column_letter": chosen_col,
        "field_type": "text",
        "show_in_form": True,
        "show_in_smart_paste": True,
    }
    r = requests.post(
        f"{BASE}/me/custom-fields",
        headers=auth(tok), json=cf_payload, timeout=TIMEOUT,
    )
    cf_id: Optional[str] = None
    if r.status_code == 200:
        cf_id = r.json().get("id")
        if cf_id:
            created_custom_field_ids.append(cf_id)
    record("B3 created custom field",
           bool(cf_id), f"id={cf_id}")

    if cf_id:
        valid_with_cf = dict(valid)
        valid_with_cf["meta.tag"] = f"custom:{cf_id}"
        r = requests.put(
            f"{BASE}/me/webhook-config",
            headers=auth(tok),
            json={"mapping": valid_with_cf}, timeout=TIMEOUT,
        )
        record("B3 PUT mapping with custom:<id> → 200",
               r.status_code == 200,
               f"got {r.status_code} {r.text[:200]}")
        if r.status_code == 200:
            saved_webhook_mapping = valid_with_cf

    # B4 — garbage custom id
    garb_map = dict(valid)
    garb_map["meta.tag"] = "custom:garbage-id-does-not-exist"
    r = requests.put(
        f"{BASE}/me/webhook-config",
        headers=auth(tok), json={"mapping": garb_map}, timeout=TIMEOUT,
    )
    record("B4 PUT mapping with custom:<garbage> → 400",
           r.status_code == 400, f"got {r.status_code} {r.text[:200]}")

    # Restore the saved mapping (without garbage) for D/E/F tests.
    if saved_webhook_mapping:
        requests.put(
            f"{BASE}/me/webhook-config",
            headers=auth(tok),
            json={"mapping": saved_webhook_mapping}, timeout=TIMEOUT,
        )
    return cf_id


# ════════════════════════════ C — Preview ═══════════════════════════
def test_c_preview(tok: str):
    print("\n========= C) PREVIEW =========", flush=True)
    sample = {
        "customer": {"name": "Riya", "phone": "9876543210"},
        "shipping": {"line1": "123 MG Road", "line2": "Surat",
                     "pincode": "395001"},
        "order": {"amount": 1499, "status": "Shipped",
                  "placed_at": "2026-04-29T14:30:00"},
    }
    r = requests.post(
        f"{BASE}/me/webhook-config/preview",
        headers=auth(tok), json=sample, timeout=TIMEOUT,
    )
    record("C1 POST preview 200",
           r.status_code == 200, f"got {r.status_code} {r.text[:200]}")
    if r.status_code != 200:
        return
    j = r.json()
    keys = j.get("keys") or []
    record("C1 keys[] includes 'customer.name'",
           "customer.name" in keys, f"keys={keys[:8]}")
    record("C1 keys[] includes 'order.placed_at'",
           "order.placed_at" in keys, "")
    sv = j.get("sample_values") or {}
    record("C1 sample_values has 'customer.name'",
           sv.get("customer.name") == "Riya", f"sv['customer.name']={sv.get('customer.name')!r}")
    sug = j.get("suggested") or {}
    record("C1 suggested[customer.name] == 'customer_name'",
           sug.get("customer.name") == "customer_name",
           f"got={sug.get('customer.name')!r}")
    sf = j.get("schema_fields") or []
    record("C1 schema_fields present (>=20)",
           len(sf) >= 20, f"len={len(sf)}")
    record("C1 custom_fields list present",
           isinstance(j.get("custom_fields"), list), "")


# ════════════════════════════ D — Public ingest happy path ═════════════
def test_d_public_ingest_happy(tok: str, secret: str):
    print("\n========= D) PUBLIC INGEST happy path =========", flush=True)
    short = str(int(time.time()))[-6:]
    phone = f"98{short}10"[:10]
    payload = {
        "customer": {"name": "Riya", "phone": phone},
        "shipping": {"line1": "123 MG", "line2": "Surat"},
        "order": {"amount": 1499, "status": "Shipped",
                  "placed_at": "2026-04-29T14:30:00"},
    }
    # Note: NO Authorization header for public ingest
    r = requests.post(
        f"{BASE}/webhook/orders/{secret}", json=payload, timeout=TIMEOUT,
    )
    record("D1 POST public ingest 200",
           r.status_code == 200, f"got {r.status_code} {r.text[:200]}")
    if r.status_code != 200:
        return
    j = r.json()
    record("D1 imported == 1",
           j.get("imported") == 1, f"imported={j.get('imported')} skipped={j.get('skipped')} errors={j.get('errors')}")

    # Fetch the pending row
    r = requests.get(
        f"{BASE}/orders/pending?source=webhook",
        headers=auth(tok), timeout=TIMEOUT,
    )
    if r.status_code != 200:
        record("D1 GET pending source=webhook 200", False,
               f"got {r.status_code}")
        return
    matches = [p for p in r.json() if (p.get("customer_phone") or "") == phone]
    record("D1 pending row found by phone", len(matches) == 1,
           f"matches={len(matches)}")
    if not matches:
        return
    po = matches[0]
    created_pending_ids.append(po["id"])
    record("D1 customer_name == 'Riya'",
           po.get("customer_name") == "Riya",
           f"got={po.get('customer_name')!r}")
    record("D1 customer_phone matches",
           po.get("customer_phone") == phone, f"got={po.get('customer_phone')!r}")
    record("D1 address auto-merged (line1+line2)",
           "123 MG" in (po.get("address_line1") or "")
           and "Surat" in (po.get("address_line1") or ""),
           f"address_line1={po.get('address_line1')!r}")
    record("D1 amount == 1499",
           float(po.get("amount") or 0) == 1499.0,
           f"got={po.get('amount')!r}")
    record("D1 imported_status == 'Shipped'",
           po.get("imported_status") == "Shipped",
           f"got={po.get('imported_status')!r}")
    ia = po.get("imported_at") or ""
    record("D1 imported_at contains '2026-04-29T14:30'",
           "2026-04-29T14:30" in ia, f"imported_at={ia!r}")
    record("D1 status (pipeline) == 'pending'",
           po.get("status") == "pending", f"got={po.get('status')!r}")
    record("D1 source == 'webhook'",
           po.get("source") == "webhook", f"got={po.get('source')!r}")
    record("D1 source_meta.received_at present",
           bool((po.get("source_meta") or {}).get("received_at")),
           f"source_meta={po.get('source_meta')}")

    # Now ship it
    courier_id = ensure_courier(tok)
    record("D2 courier exists", bool(courier_id), str(courier_id))
    if not courier_id:
        return
    r = requests.post(
        f"{BASE}/orders/pending/{po['id']}/ship",
        headers=auth(tok),
        json={"courier_id": courier_id, "overrides": {}},
        timeout=TIMEOUT,
    )
    record("D2 POST /orders/pending/{id}/ship 200",
           r.status_code == 200, f"got {r.status_code} {r.text[:200]}")
    if r.status_code == 200:
        ship = r.json()
        sid = ship.get("id")
        if sid:
            created_shipment_ids.append(sid)
            try:
                created_pending_ids.remove(po["id"])
            except ValueError:
                pass
        record("D2 Shipment.status == 'Shipped' (NOT 'Pending')",
               ship.get("status") == "Shipped",
               f"got={ship.get('status')!r}")
        record("D2 Shipment.created_at == imported_at",
               ship.get("created_at") == ia,
               f"got={ship.get('created_at')!r} expected={ia!r}")


# ════════════════════════════ E — Public ingest batch ═════════════
def test_e_batch(tok: str, secret: str):
    print("\n========= E) PUBLIC INGEST batch =========", flush=True)
    short = str(int(time.time()))[-6:]

    def _row(n: int) -> Dict[str, Any]:
        return {
            "customer": {"name": f"Batch{n}", "phone": f"7{short}{n:03d}"[:10]},
            "shipping": {"line1": f"{n} Main St", "line2": "Mumbai"},
            "order": {"amount": 100 + n, "status": "Pending",
                      "placed_at": "2026-04-30T10:00:00"},
        }

    # E1 — {"orders": [r1, r2, r3]}
    body = {"orders": [_row(i) for i in range(1, 4)]}
    r = requests.post(
        f"{BASE}/webhook/orders/{secret}", json=body, timeout=TIMEOUT,
    )
    record("E1 batch via {orders:[...]} 200",
           r.status_code == 200, f"got {r.status_code} {r.text[:200]}")
    if r.status_code == 200:
        j = r.json()
        record("E1 imported == 3",
               j.get("imported") == 3, f"j={j}")
        # Cleanup pending IDs
        for n in range(1, 4):
            phone = f"7{short}{n:03d}"[:10]
            g = requests.get(
                f"{BASE}/orders/pending?source=webhook",
                headers=auth(tok), timeout=TIMEOUT,
            )
            if g.status_code == 200:
                for p in g.json():
                    if p.get("customer_phone") == phone:
                        created_pending_ids.append(p["id"])

    # E2 — top-level array
    arr_body = [_row(50), _row(51)]
    r = requests.post(
        f"{BASE}/webhook/orders/{secret}", json=arr_body, timeout=TIMEOUT,
    )
    record("E2 top-level array 200",
           r.status_code == 200, f"got {r.status_code} {r.text[:200]}")
    if r.status_code == 200:
        j = r.json()
        record("E2 imported == 2",
               j.get("imported") == 2, f"j={j}")
        for n in (50, 51):
            phone = f"7{short}{n:03d}"[:10]
            g = requests.get(
                f"{BASE}/orders/pending?source=webhook",
                headers=auth(tok), timeout=TIMEOUT,
            )
            if g.status_code == 200:
                for p in g.json():
                    if p.get("customer_phone") == phone:
                        created_pending_ids.append(p["id"])

    # E3 — 201 rows → 413
    huge = {"orders": [_row(100 + i) for i in range(201)]}
    r = requests.post(
        f"{BASE}/webhook/orders/{secret}", json=huge, timeout=TIMEOUT,
    )
    record("E3 201-row batch → 413",
           r.status_code == 413, f"got {r.status_code} {r.text[:200]}")


# ════════════════════════════ F — Errors ═════════════
def test_f_errors(tok: str, secret: str):
    print("\n========= F) PUBLIC INGEST errors =========", flush=True)

    # F1 — bogus secret
    r = requests.post(
        f"{BASE}/webhook/orders/totally-bogus-secret-aaaaaaaaaaa",
        json={"x": "y"}, timeout=TIMEOUT,
    )
    record("F1 bogus secret → 404",
           r.status_code == 404, f"got {r.status_code} {r.text[:200]}")

    # F2 — user with NO mapping. We need to wipe mapping then restore.
    # PUT empty mapping — ingest should yield 409.
    r = requests.put(
        f"{BASE}/me/webhook-config",
        headers=auth(tok), json={"mapping": {}}, timeout=TIMEOUT,
    )
    if r.status_code == 200:
        r = requests.post(
            f"{BASE}/webhook/orders/{secret}",
            json={"customer": {"name": "X"}}, timeout=TIMEOUT,
        )
        record("F2 ingest with no mapping → 409",
               r.status_code == 409, f"got {r.status_code} {r.text[:200]}")
    else:
        record("F2 wipe mapping for test → 200",
               False, f"got {r.status_code} {r.text[:200]}")

    # Restore the saved mapping
    if saved_webhook_mapping:
        requests.put(
            f"{BASE}/me/webhook-config",
            headers=auth(tok),
            json={"mapping": saved_webhook_mapping}, timeout=TIMEOUT,
        )

    # F3 — row missing both name and phone → errors[], imported=0
    bad_row = {"shipping": {"line1": "Address only"},
               "order": {"amount": 100}}
    r = requests.post(
        f"{BASE}/webhook/orders/{secret}", json=bad_row, timeout=TIMEOUT,
    )
    record("F3 missing name+phone → 200 with imported=0",
           r.status_code == 200, f"got {r.status_code} {r.text[:200]}")
    if r.status_code == 200:
        j = r.json()
        record("F3 imported == 0", j.get("imported") == 0,
               f"j={j}")
        record("F3 errors[] non-empty",
               bool(j.get("errors")), f"errors={j.get('errors')}")

    # F4 — non-dict body (raw string)
    r = requests.post(
        f"{BASE}/webhook/orders/{secret}",
        data='"this is a string"',
        headers={"Content-Type": "application/json"},
        timeout=TIMEOUT,
    )
    record("F4 non-dict body → 400",
           r.status_code == 400, f"got {r.status_code} {r.text[:200]}")


# ════════════════════════════ G — Dispatch cleanup ═════════════
def test_g_dispatch_cleanup(tok: str):
    print("\n========= G) DISPATCH CLEANUP =========", flush=True)
    courier_id = ensure_courier(tok)
    if not courier_id:
        record("G courier exists", False, "no courier")
        return

    # Create a Pending shipment via POST /shipments
    short = str(int(time.time()))[-6:]
    payload = {
        "courier_id":     courier_id,
        "customer_name":  "Dispatch CleanUp Tester",
        "customer_phone": f"96{short}55"[:10],
        "address_line1":  "12 Test Street",
        "city":           "Surat",
        "state":          "Gujarat",
        "pincode":        "395001",
        "amount":         500,
        "payment_mode":   "COD",
        "items":          ["box A"],
        "weight":         "0.5",
        "status":         "Pending",
    }
    r = requests.post(
        f"{BASE}/shipments", headers=auth(tok), json=payload, timeout=TIMEOUT,
    )
    record("G1 POST /shipments (Pending) 200",
           r.status_code == 200, f"got {r.status_code} {r.text[:300]}")
    if r.status_code != 200:
        return
    ship = r.json()
    sid = ship.get("id")
    tid = ship.get("tracking_id")
    if sid:
        created_shipment_ids.append(sid)

    # Confirm initial status is Pending
    record("G1 initial status == 'Pending'",
           ship.get("status") == "Pending", f"got={ship.get('status')!r}")

    # POST /shipments/scan-dispatch with tracking_id
    r = requests.post(
        f"{BASE}/shipments/scan-dispatch",
        headers=auth(tok),
        json={"tracking_id": tid}, timeout=TIMEOUT,
    )
    record("G1 POST scan-dispatch 200",
           r.status_code == 200, f"got {r.status_code} {r.text[:300]}")
    if r.status_code == 200:
        j = r.json()
        new_status = (j.get("shipment") or {}).get("status")
        record("G1 scan-dispatch result.status == 'Ready to Ship'",
               new_status == "Ready to Ship", f"got={new_status!r}")
        record("G1 scan-dispatch result.status NOT 'Dispatch'",
               new_status != "Dispatch", f"got={new_status!r}")
        record("G1 scan-dispatch result.status NOT 'Dispatched'",
               new_status != "Dispatched", f"got={new_status!r}")

    # Re-fetch via GET /shipments/{id} to confirm DB state
    r = requests.get(
        f"{BASE}/shipments/{sid}", headers=auth(tok), timeout=TIMEOUT,
    )
    if r.status_code == 200:
        st = r.json().get("status")
        record("G1 GET /shipments/{id} status == 'Ready to Ship'",
               st == "Ready to Ship", f"got={st!r}")

    # G2 — direct DB insert of a legacy "Dispatch" shipment (cannot do
    # mongo direct via API; simulate by POSTing a Shipment with status
    # explicitly set to "Dispatch"). Verify GET ?status=Ready%20to%20Ship
    # surfaces it.
    legacy_payload = dict(payload)
    legacy_payload["customer_name"] = "Legacy Dispatch Row"
    legacy_payload["customer_phone"] = f"96{short}77"[:10]
    legacy_payload["status"] = "Dispatch"
    r = requests.post(
        f"{BASE}/shipments", headers=auth(tok), json=legacy_payload,
        timeout=TIMEOUT,
    )
    record("G2 POST /shipments (status='Dispatch') 200",
           r.status_code == 200, f"got {r.status_code} {r.text[:300]}")
    if r.status_code == 200:
        legacy_doc = r.json()
        legacy_id = legacy_doc.get("id")
        if legacy_id:
            legacy_dispatch_ids.append(legacy_id)
            created_shipment_ids.append(legacy_id)
        legacy_status = legacy_doc.get("status")
        record("G2 POST persisted status=='Dispatch' (or normalised?)",
               legacy_status in ("Dispatch", "Ready to Ship", "Dispatched"),
               f"persisted={legacy_status!r}")

        # GET ?status=Ready%20to%20Ship
        r = requests.get(
            f"{BASE}/shipments",
            headers=auth(tok),
            params={"status": "Ready to Ship"},
            timeout=TIMEOUT,
        )
        if r.status_code == 200:
            ids = [s.get("id") for s in r.json()]
            record("G2 GET ?status=Ready%20to%20Ship surfaces legacy row",
                   legacy_id in ids,
                   f"legacy_id in {len(ids)} returned ids: "
                   f"{'YES' if legacy_id in ids else 'NO'}")
        else:
            record("G2 GET shipments?status=Ready to Ship",
                   False, f"got {r.status_code}")


# ════════════════════════════ H — F1+F2.1 smoke ═════════════
def test_h_smoke_f2_1(tok: str):
    """Light smoke test - just verify the import endpoint preview/commit
    still work. The full suite is in /app/backend_test_phase_f2_1.py
    which the user can run separately if needed."""
    print("\n========= H) F1+F2.1 SMOKE =========", flush=True)
    import io
    import csv as _csv

    short = str(int(time.time()))[-6:]
    sio = io.StringIO()
    w = _csv.writer(sio)
    w.writerow(["Customer Name", "Phone", "Status", "Timestamp"])
    phone = f"95{short}11"[:10]
    w.writerow(["Smoke F21", phone, "Shipped", "29/04/2026 10:00:00"])
    blob = sio.getvalue().encode("utf-8")
    files = {"file": ("smoke.csv", blob, "text/csv")}

    # Preview
    r = requests.post(
        f"{BASE}/orders/import/preview",
        headers=auth(tok), files=files, timeout=TIMEOUT,
    )
    record("H1 preview 200",
           r.status_code == 200, f"got {r.status_code} {r.text[:200]}")
    if r.status_code == 200:
        sug = r.json().get("suggested", {})
        record("H1 suggested[Status] == 'status'",
               sug.get("Status") == "status", f"got={sug.get('Status')!r}")
        record("H1 suggested[Timestamp] == 'created_at_override'",
               sug.get("Timestamp") == "created_at_override",
               f"got={sug.get('Timestamp')!r}")

    # Commit
    mapping = {
        "Customer Name": "customer_name",
        "Phone": "customer_phone",
        "Status": "status",
        "Timestamp": "created_at_override",
    }
    files = {"file": ("smoke.csv", blob, "text/csv")}
    data = {"mapping": json.dumps(mapping), "save_default": "false"}
    r = requests.post(
        f"{BASE}/orders/import/commit",
        headers=auth(tok), files=files, data=data, timeout=TIMEOUT,
    )
    record("H1 commit 200", r.status_code == 200,
           f"got {r.status_code} {r.text[:200]}")
    if r.status_code == 200:
        record("H1 imported == 1",
               r.json().get("imported") == 1, f"j={r.json()}")
        # Cleanup the new pending row
        g = requests.get(
            f"{BASE}/orders/pending?source=file",
            headers=auth(tok), timeout=TIMEOUT,
        )
        if g.status_code == 200:
            for p in g.json():
                if p.get("customer_phone") == phone:
                    created_pending_ids.append(p["id"])

    # /me/file-import-mapping returns custom_fields
    r = requests.get(
        f"{BASE}/me/file-import-mapping",
        headers=auth(tok), timeout=TIMEOUT,
    )
    record("H2 GET /me/file-import-mapping 200", r.status_code == 200,
           f"got {r.status_code}")
    if r.status_code == 200:
        record("H2 response has 'custom_fields' key",
               "custom_fields" in r.json(), f"keys={list(r.json().keys())}")


# ════════════════════════════ Cleanup ═════════════
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
    if created_courier_id:
        try:
            requests.delete(f"{BASE}/couriers/{created_courier_id}",
                            headers=auth(tok), timeout=TIMEOUT)
        except Exception:
            pass
    print("Cleanup done.", flush=True)


def main() -> int:
    tok = login(ADMIN_EMAIL, ADMIN_PASSWORD)
    if not tok:
        print("FATAL: could not log in admin@test.com")
        return 2
    print(f"Logged in. Token len={len(tok)}", flush=True)

    secret: Optional[str] = None
    try:
        secret = test_a_config_crud(tok)
        test_b_mapping_save(tok)
        test_c_preview(tok)
        if secret:
            test_d_public_ingest_happy(tok, secret)
            test_e_batch(tok, secret)
            test_f_errors(tok, secret)
        else:
            record("D/E/F skipped — no secret", False, "rotate failed")
        test_g_dispatch_cleanup(tok)
        test_h_smoke_f2_1(tok)
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
                print(f"  FAIL {r['name']} — {r['info']}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
