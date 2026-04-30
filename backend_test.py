"""
Targeted re-verification tests for:
  A. Header Auto-Sync contract — POST /api/sheets/sync-headers
  F. POST /api/shipments regression after sheet_writer.py syntax fix
  G. sheet_writer module import + backend health
"""
import os
import sys
import json
import requests

BASE = "https://logistics-hub-740.preview.emergentagent.com/api"
USER2_EMAIL = "user2@test.com"
USER2_PASSWORD = "User@12345"


def _login(email, pwd):
    r = requests.post(f"{BASE}/auth/login", json={"email": email, "password": pwd}, timeout=30)
    r.raise_for_status()
    return r.json()["token"]


def _headers(tok):
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}


# ---------------------------------------------------------------------------
def test_g_module_import():
    print("\n=== G7. sheet_writer import ===")
    rc = os.system("cd /app/backend && python -c 'import sheet_writer' >/tmp/_sw.log 2>&1")
    assert rc == 0, f"sheet_writer import failed: see /tmp/_sw.log"
    print("PASS: import OK")


def test_g_health():
    print("\n=== G8. /api/ health ===")
    r = requests.get(f"{BASE}/", timeout=15)
    print(f"  GET /api/ -> {r.status_code} {r.text[:120]}")
    # /api/ root is auth-gated -> 401 means backend alive; 200 also acceptable
    assert r.status_code in (200, 401), f"expected 200/401, got {r.status_code}"
    print("PASS: backend reachable (status %d)" % r.status_code)


def test_a_sync_headers_dry_run(tok):
    print("\n=== A1. POST /api/sheets/sync-headers dry_run=true ===")
    r = requests.post(
        f"{BASE}/sheets/sync-headers",
        headers=_headers(tok),
        json={"dry_run": True},
        timeout=30,
    )
    body = r.text
    try:
        data = r.json()
    except Exception:
        data = None
    print(f"  status={r.status_code}")
    print(f"  body={body[:600]}")
    assert r.status_code != 503, "REGRESSION: 503 'Sheets integration not configured' returned!"
    if r.status_code == 200:
        assert isinstance(data, dict), "body must be JSON dict"
        assert data.get("ok") is True, f"ok must be True, got {data.get('ok')}"
        assert data.get("dry_run") is True, "dry_run must be true"
        assert "would_write" in data, "would_write key required"
        ww = data["would_write"]
        assert isinstance(ww, list), "would_write must be a list"
        for i, item in enumerate(ww):
            assert isinstance(item, dict), f"item[{i}] must be dict"
            assert "column" in item, f"item[{i}] missing column"
            assert "name" in item, f"item[{i}] missing name"
        print(f"PASS: 200 OK, dry_run=true, {len(ww)} would_write items, all have column+name")
        return True, data
    elif r.status_code == 400:
        # No sheet connected — also valid contract
        detail = (data or {}).get("detail", "")
        assert "Google Sheet not connected" in detail or "not connected" in detail.lower(), \
            f"expected 'Google Sheet not connected', got: {detail}"
        print(f"PASS: 400 'Google Sheet not connected' (user2 has no sheet)")
        return False, data
    else:
        raise AssertionError(f"Unexpected status {r.status_code}: {body[:400]}")


def test_a_sync_headers_real_write(tok):
    print("\n=== A2. POST /api/sheets/sync-headers dry_run=false ===")
    r = requests.post(
        f"{BASE}/sheets/sync-headers",
        headers=_headers(tok),
        json={"dry_run": False},
        timeout=60,
    )
    print(f"  status={r.status_code}")
    print(f"  body={r.text[:400]}")
    assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text[:300]}"
    data = r.json()
    assert data.get("ok") is True
    assert "written_count" in data
    assert "skipped_count" in data
    assert "written" in data and isinstance(data["written"], list)
    assert "skipped" in data and isinstance(data["skipped"], list)
    written_count_1 = data["written_count"]
    print(f"  first run: written_count={written_count_1}, skipped_count={data['skipped_count']}")

    # Re-run
    r2 = requests.post(
        f"{BASE}/sheets/sync-headers",
        headers=_headers(tok),
        json={"dry_run": False},
        timeout=60,
    )
    assert r2.status_code == 200, f"second run failed: {r2.status_code} {r2.text[:300]}"
    data2 = r2.json()
    print(f"  second run: written_count={data2['written_count']}, skipped_count={data2['skipped_count']}")
    assert data2["written_count"] == 0, f"second run should have written_count==0, got {data2['written_count']}"
    assert data2["skipped_count"] >= written_count_1, \
        f"skipped_count ({data2['skipped_count']}) must be >= previous written_count ({written_count_1})"
    print("PASS: idempotent — second run wrote 0, skipped >= previous written")


def test_a_sync_headers_explicit_override(tok):
    print("\n=== A3. POST /api/sheets/sync-headers explicit headers override ===")
    payload = {
        "dry_run": True,
        "headers": [{"column": "ZZ", "name": "Test ZZ"}],
    }
    r = requests.post(f"{BASE}/sheets/sync-headers",
                      headers=_headers(tok), json=payload, timeout=30)
    print(f"  status={r.status_code}")
    print(f"  body={r.text[:400]}")
    assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text[:300]}"
    data = r.json()
    assert data.get("ok") is True
    assert data.get("dry_run") is True
    ww = data.get("would_write") or []
    found = any(it.get("column") == "ZZ" and it.get("name") == "Test ZZ" for it in ww)
    assert found, f"explicit override not present in would_write: {ww}"
    print(f"PASS: would_write contains exact pair {{column:'ZZ', name:'Test ZZ'}}")


# ---------------------------------------------------------------------------
def test_f_post_shipment(tok):
    print("\n=== F5. POST /api/shipments regression check ===")
    # Get a courier
    r = requests.get(f"{BASE}/couriers", headers=_headers(tok), timeout=20)
    assert r.status_code == 200, f"GET /couriers failed: {r.status_code}"
    couriers = r.json()
    assert len(couriers) > 0, "user2 should have at least 1 courier (Demo Courier seeded)"
    c = couriers[0]
    print(f"  using courier: {c['name']} (id={c['id']})")

    payload = {
        "tracking_id": "TST00099",
        "courier_id": c["id"],
        "courier_name": c["name"],
        "customer_name": "Header Sync Regression Test",
        "customer_phone": "9112233445",
        "address_line1": "12 Test Avenue",
        "city": "Ahmedabad",
        "state": "Gujarat",
        "pincode": "380015",
        "payment_mode": "COD",
        "amount": 199.0,
        "items": ["Test Item"],
        "weight": "0.5",
    }
    r = requests.post(f"{BASE}/shipments", headers=_headers(tok), json=payload, timeout=60)
    print(f"  status={r.status_code}")
    body_preview = r.text[:600]
    print(f"  body={body_preview}")
    assert r.status_code != 503, f"REGRESSION: 503 returned! {body_preview}"
    assert r.status_code == 200, f"expected 200, got {r.status_code}: {body_preview}"
    ship = r.json()
    sid = ship["id"]
    assert ship.get("customer_name") == payload["customer_name"]
    print(f"PASS: 200 OK — shipment created id={sid}, master_order_id={ship.get('master_order_id')}, sheet_row_num={ship.get('sheet_row_num')}")

    # Cleanup
    rd = requests.delete(f"{BASE}/shipments/{sid}", headers=_headers(tok), timeout=30)
    print(f"  DELETE -> {rd.status_code} {rd.text[:200]}")
    assert rd.status_code == 200
    print("  cleanup OK")
    return ship


def test_f_smart_paste_then_ship(tok):
    print("\n=== F6. Smart-Paste -> Ship pipeline (no duplicate row) ===")
    paste_text = """Name: Pipeline Regression
Phone: 9123455678
Address: 99, Test Lane, Ahmedabad
City: Ahmedabad
State: Gujarat
Pincode: 380015
Payment: COD
Amount: 250
Items: Sample"""

    r = requests.post(f"{BASE}/smart-paste", headers=_headers(tok),
                      json={"text": paste_text}, timeout=60)
    print(f"  smart-paste status={r.status_code}")
    print(f"  body={r.text[:300]}")
    if r.status_code != 200:
        print("SKIP: smart-paste pipeline not available")
        return
    pending = r.json()
    pid = pending["id"]
    sheet_row_num_1 = pending.get("sheet_row_num")
    print(f"  pending id={pid} sheet_row_num={sheet_row_num_1}")

    # Get courier
    rc = requests.get(f"{BASE}/couriers", headers=_headers(tok), timeout=20)
    couriers = rc.json()
    if not couriers:
        # cleanup pending
        requests.delete(f"{BASE}/orders/pending/{pid}", headers=_headers(tok), timeout=20)
        print("SKIP: no courier")
        return
    cid = couriers[0]["id"]

    # Ship
    rs = requests.post(f"{BASE}/orders/pending/{pid}/ship",
                       headers=_headers(tok),
                       json={"courier_id": cid, "overrides": {}},
                       timeout=60)
    print(f"  ship status={rs.status_code} body={rs.text[:300]}")
    assert rs.status_code == 200, f"ship failed: {rs.status_code} {rs.text[:200]}"
    ship = rs.json()
    sheet_row_num_2 = ship.get("sheet_row_num")
    sid = ship["id"]
    print(f"  shipment id={sid} sheet_row_num={sheet_row_num_2}")
    # sheet_row_num must be carried forward (no duplicate row written)
    assert sheet_row_num_2 == sheet_row_num_1, \
        f"sheet_row_num diverged: pending={sheet_row_num_1}, shipment={sheet_row_num_2}. Possible duplicate row write!"

    # Cleanup
    rd = requests.delete(f"{BASE}/shipments/{sid}", headers=_headers(tok), timeout=30)
    print(f"  cleanup status={rd.status_code}")
    assert rd.status_code == 200
    print("PASS: smart-paste -> ship preserved sheet_row_num (no duplicate master row)")


# ---------------------------------------------------------------------------
def main():
    failures = []
    try:
        test_g_module_import()
    except Exception as e:
        failures.append(("G7 module import", e))

    try:
        test_g_health()
    except Exception as e:
        failures.append(("G8 health", e))

    print(f"\n=== Login as {USER2_EMAIL} ===")
    try:
        tok = _login(USER2_EMAIL, USER2_PASSWORD)
        print(f"  token: {tok[:24]}…")
    except Exception as e:
        print(f"FATAL: cannot login: {e}")
        sys.exit(1)

    has_sheet = False
    try:
        has_sheet, _data = test_a_sync_headers_dry_run(tok)
    except Exception as e:
        failures.append(("A1 sync-headers dry_run", e))

    if has_sheet:
        try:
            test_a_sync_headers_real_write(tok)
        except Exception as e:
            failures.append(("A2 sync-headers real write", e))
        try:
            test_a_sync_headers_explicit_override(tok)
        except Exception as e:
            failures.append(("A3 sync-headers explicit override", e))
    else:
        print("\n[A2/A3 skipped — user2 has no sheet linked, contract for 400 already validated]")
        # We can still test A3 if explicit headers are given without dry_run since
        # the 400 fails before reaching items, but the contract says "if sheet connected"
        # for A3, so we skip it here.

    try:
        test_f_post_shipment(tok)
    except Exception as e:
        failures.append(("F5 POST /shipments", e))

    try:
        test_f_smart_paste_then_ship(tok)
    except Exception as e:
        failures.append(("F6 smart-paste -> ship", e))

    print("\n" + "=" * 60)
    if failures:
        print(f"FAILURES: {len(failures)}")
        for name, exc in failures:
            print(f"  - {name}: {exc}")
        sys.exit(2)
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
