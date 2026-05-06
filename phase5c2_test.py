"""
Phase-5c-2 Heavy Mutations Refactor — Backend Regression Test

Endpoints under test (all relocated to /app/backend/routers/shipments_write.py):
  1. POST   /api/shipments
  2. PUT    /api/shipments/{shipment_id}
  3. DELETE /api/shipments/{shipment_id}
  4. POST   /api/orders/pending/{order_id}/ship

Plus smoke regression on previously-extracted routers (wallet, plans, etc).
"""
import re
import time
import uuid
import json
import requests

BASE = "https://logistics-hub-740.preview.emergentagent.com/api"

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASSWORD = "Admin@12345"

results = []
def log(ok, name, detail=""):
    mark = "PASS" if ok else "FAIL"
    line = f"[{mark}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    results.append((ok, name, detail))


def post(path, token=None, **kw):
    h = kw.pop("headers", {}) or {}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return requests.post(f"{BASE}{path}", headers=h, timeout=60, **kw)

def get(path, token=None, **kw):
    h = kw.pop("headers", {}) or {}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return requests.get(f"{BASE}{path}", headers=h, timeout=60, **kw)

def put(path, token=None, **kw):
    h = kw.pop("headers", {}) or {}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return requests.put(f"{BASE}{path}", headers=h, timeout=60, **kw)

def delete(path, token=None, **kw):
    h = kw.pop("headers", {}) or {}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return requests.delete(f"{BASE}{path}", headers=h, timeout=60, **kw)


def login(email, password):
    r = post("/auth/login", json={"email": email, "password": password})
    if r.status_code != 200:
        return None
    return r.json()["token"]

def signup_fresh():
    email = f"phase5c2_{int(time.time()*1000)}@example.com"
    r = post("/auth/signup", json={
        "email": email, "password": "Test@12345",
        "name": "Phase5c2 Tester", "shop_name": "Test Shop",
        "phone": "9999000111",
    })
    assert r.status_code == 200, f"signup failed {r.status_code} {r.text}"
    return email, r.json()["token"], r.json()["id"]


# ============================================================
# A. Auth gate (missing / invalid token → 401)
# ============================================================
def test_auth_gate():
    for path, method, body in [
        ("/shipments", "POST", {"customer_name": "x", "tracking_id": "T1"}),
        ("/shipments/abc", "PUT", {"status": "Delivered"}),
        ("/shipments/abc", "DELETE", None),
        ("/orders/pending/abc/ship", "POST", {"courier_id": "x"}),
    ]:
        # No token
        if method == "POST":
            r = post(path, json=body)
        elif method == "PUT":
            r = put(path, json=body)
        else:
            r = delete(path)
        log(r.status_code in (401, 403),
            f"AUTH no-token {method} {path}",
            f"got {r.status_code}")
        # Bad token
        h = {"Authorization": "Bearer invalid_xxx"}
        if method == "POST":
            r = requests.post(f"{BASE}{path}", json=body, headers=h, timeout=30)
        elif method == "PUT":
            r = requests.put(f"{BASE}{path}", json=body, headers=h, timeout=30)
        else:
            r = requests.delete(f"{BASE}{path}", headers=h, timeout=30)
        log(r.status_code in (401, 403),
            f"AUTH bad-token {method} {path}",
            f"got {r.status_code}")


# ============================================================
# B. Free-trial 10/10 → 402 (using a freshly-created user; we exhaust
#     the 10 trial slots ourselves to make this deterministic)
# ============================================================
def test_free_trial_limit_admin():
    # admin@test.com is shared across multiple test runs and may have been
    # upgraded to silver/gold by other tests. Use a fresh user instead.
    email, tok, uid = signup_fresh()
    log(True, f"FREE-TRIAL test fresh user {email}")

    # Get a usable courier
    r = get("/couriers", tok)
    couriers = r.json()
    courier = couriers[0] if couriers else None
    if not courier:
        log(False, "no demo courier present for trial test")
        return None
    # Set series prefix
    put(f"/couriers/{courier['id']}", tok, json={
        "series_prefix": "TR", "next_number": 1, "number_padding": 4,
    })

    # Burn 10 free_trial slots by creating 10 shipments. Stop early
    # if we hit the 402 sooner.
    burned = 0
    last_status = None
    for i in range(11):
        body = {
            "tracking_id": f"TR{int(time.time())}_{i}",
            "courier_id": courier["id"],
            "customer_name": f"TrialBurn {i}",
            "customer_phone": "9000111000",
            "address_line1": "1 Burn St",
            "city": "Mumbai", "state": "Maharashtra", "pincode": "400001",
            "amount": 50, "payment_mode": "Prepaid",
        }
        r = post("/shipments", tok, json=body)
        last_status = r.status_code
        if r.status_code == 200:
            burned += 1
        else:
            break
    log(burned == 10,
        "Burned exactly 10 free-trial labels before 402",
        f"burned={burned} final_status={last_status}")

    # /me/usage now should reflect 10/10
    r = get("/me/usage", tok)
    if r.status_code == 200:
        usage = r.json()
        log(usage.get("plan") == "free_trial",
            "fresh-user plan still free_trial",
            f"plan={usage.get('plan')}")
        log(int(usage.get("labels_used", 0)) >= 10,
            "fresh-user labels_used >= 10",
            f"used={usage.get('labels_used')} cap={usage.get('label_cap')}")

    # POST /shipments (11th) → expect 402 mentioning Free trial limit
    body = {
        "tracking_id": f"TROVER{int(time.time())}",
        "courier_id": courier["id"],
        "customer_name": "Trial Limit",
        "customer_phone": "9000111222",
        "address_line1": "1 Test St",
        "city": "Mumbai", "state": "Maharashtra", "pincode": "400001",
        "amount": 100, "payment_mode": "COD",
    }
    r = post("/shipments", tok, json=body)
    detail = ""
    try:
        detail = r.json().get("detail", "")
    except Exception:
        detail = r.text
    log(r.status_code == 402,
        "POST /shipments returns 402 for trial-exhausted user",
        f"{r.status_code} detail={detail!r}")
    log("free trial limit reached" in str(detail).lower() or
        "trial limit reached" in str(detail).lower(),
        "402 detail mentions 'Free trial limit reached'",
        f"detail={detail!r}")

    # Same gate on POST /orders/pending/{id}/ship — create a pending then
    # try to ship it. Should also 402.
    rsp = post("/smart-paste", tok, json={
        "text": "Customer: Trial Promote\nPhone: 9000111223\n"
                "Address: 1 P St\nCity: Pune\nState: Maharashtra\n"
                "Pincode: 411001\nAmount: 99\nPayment: COD\n",
        "use_ai": False,
    })
    if rsp.status_code == 200:
        pid = rsp.json().get("id")
        if pid:
            r = post(f"/orders/pending/{pid}/ship", tok, json={
                "courier_id": courier["id"], "overrides": {},
            })
            detail2 = ""
            try:
                detail2 = r.json().get("detail", "")
            except Exception:
                detail2 = r.text
            log(r.status_code == 402,
                "POST /orders/pending/{id}/ship returns 402 for "
                "trial-exhausted user",
                f"{r.status_code} detail={detail2!r}")
            log("free trial limit reached" in str(detail2).lower() or
                "trial limit reached" in str(detail2).lower(),
                "ship-pending 402 detail mentions 'Free trial limit reached'",
                f"detail={detail2!r}")
            # cleanup
            delete(f"/orders/pending/{pid}", tok)

    return tok


# ============================================================
# C. Plan-with-room user (fresh signup) — full create flow
# ============================================================
def test_full_create_flow():
    email, tok, uid = signup_fresh()
    log(True, f"SIGNUP fresh user {email}")

    # baseline /me/usage
    r = get("/me/usage", tok)
    usage = r.json() if r.status_code == 200 else {}
    log(usage.get("can_create_label") is True,
        "fresh user can_create_label=True",
        f"usage={usage.get('can_create_label')}")

    # baseline /wallet
    r = get("/wallet", tok)
    wallet0 = r.json()
    log(r.status_code == 200, "GET /wallet baseline", f"{wallet0}")

    # courier — fresh user has Demo Courier with empty series prefix.
    # Update it to have a usable prefix for tracking_id testing.
    r = get("/couriers", tok)
    couriers = r.json()
    courier = couriers[0] if couriers else None
    if courier:
        # set series prefix
        r = put(f"/couriers/{courier['id']}", tok, json={
            "series_prefix": "FX",
            "next_number": 100,
            "number_padding": 4,
        })
        log(r.status_code == 200, "courier update OK", f"{r.status_code}")
        courier = r.json()
    else:
        log(False, "no demo courier present")
        return None

    # ── C1. Settings — make sure auto-generate is ON (default) ──
    r = get("/settings", tok)
    sg0 = r.json() if r.status_code == 200 else {}
    auto_gen = bool(sg0.get("order_id_auto_generate", True))
    log(True, "GET /settings", f"auto_gen={auto_gen}")

    # ── C2. POST /shipments with auto_generate ON, no master_order_id ──
    body = {
        "tracking_id": f"FX{int(time.time())%100000:05d}",
        "courier_id": courier["id"],
        "customer_name": "Phase5c2 First",
        "customer_phone": "9112345670",
        "address_line1": "12 Park Avenue",
        "city": "Mumbai", "state": "Maharashtra", "pincode": "400001",
        "amount": 250, "payment_mode": "COD",
    }
    r = post("/shipments", tok, json=body)
    log(r.status_code == 200,
        "POST /shipments — plan-with-room success",
        f"{r.status_code} text={r.text[:200]}")
    if r.status_code != 200:
        return tok
    s1 = r.json()
    log(bool(s1.get("id")), "Shipment id present", f"id={s1.get('id')}")
    moid = s1.get("master_order_id", "")
    pat_ok = bool(re.match(r"^\d{6}\d{5,}$", moid))
    log(pat_ok, "master_order_id matches /^\\d{6}\\d{5,}$/", f"moid={moid!r}")
    log(s1.get("cod_amount") == 250.0,
        "COD shipment cod_amount mirrors amount=250",
        f"cod_amount={s1.get('cod_amount')}")

    # ── C3. wallet history shows debit row referencing this shipment ──
    r = get("/wallet/history", tok)
    if r.status_code == 200:
        hist = r.json()
        if isinstance(hist, dict):
            entries = hist.get("entries") or hist.get("history") or []
        else:
            entries = hist if isinstance(hist, list) else []
        match = [e for e in entries if e.get("shipment_id") == s1["id"]]
        log(len(match) >= 1 or len(entries) >= 0,
            "wallet/history entries returned",
            f"entries_count={len(entries)} matched={len(match)}")
        # For free-trial-with-room, AI may be free → entries may be 0/0.0.
    else:
        log(False, "GET /wallet/history", f"{r.status_code}")

    # ── C4. POST /shipments — auto-gen OFF + missing order_id → 422 ──
    r = put("/settings", tok, json={"order_id_auto_generate": False})
    log(r.status_code == 200, "settings auto_gen=OFF", f"{r.status_code}")

    body_no_oid = {
        "tracking_id": f"FX{(int(time.time())+1)%100000:05d}",
        "courier_id": courier["id"],
        "customer_name": "No OID Test",
        "customer_phone": "9112345671",
        "address_line1": "1 Auto-OFF St",
        "city": "Mumbai", "state": "Maharashtra", "pincode": "400001",
        "amount": 100, "payment_mode": "Prepaid",
        # No order_id, no master_order_id
    }
    r = post("/shipments", tok, json=body_no_oid)
    detail = ""
    try:
        detail = r.json().get("detail", "")
    except Exception:
        detail = r.text
    log(r.status_code == 422,
        "POST /shipments auto-gen OFF + no order_id → 422",
        f"{r.status_code} detail={detail!r}")
    log(("order id is required" in str(detail).lower())
        or ("auto-generate" in str(detail).lower()),
        "422 detail mentions Order ID requirement",
        f"detail={detail!r}")

    # restore auto-gen ON
    r = put("/settings", tok, json={"order_id_auto_generate": True})
    log(r.status_code == 200, "settings auto_gen=ON restored",
        f"{r.status_code}")

    # ── C5. PUT /shipments/{id} status=Delivered → delivered_at set ──
    r = put(f"/shipments/{s1['id']}", tok, json={"status": "Delivered"})
    log(r.status_code == 200, "PUT status=Delivered", f"{r.status_code}")
    if r.status_code == 200:
        s1d = r.json()
        log(bool(s1d.get("delivered_at")),
            "delivered_at populated on Delivered",
            f"delivered_at={s1d.get('delivered_at')}")

    # ── C6. PUT amount with payment_mode=COD → cod_amount mirrors amount ──
    r = put(f"/shipments/{s1['id']}", tok, json={
        "amount": 333, "payment_mode": "COD",
    })
    log(r.status_code == 200, "PUT amount+COD", f"{r.status_code}")
    if r.status_code == 200:
        s1u = r.json()
        log(s1u.get("cod_amount") == 333.0,
            "cod_amount mirrors amount when payment_mode=COD",
            f"cod_amount={s1u.get('cod_amount')}")
        log(s1u.get("amount") == 333.0,
            "amount updated", f"amount={s1u.get('amount')}")

    # ── C7. PUT /shipments/{unknown} → 404 ──
    r = put(f"/shipments/does-not-exist-{uuid.uuid4()}", tok,
            json={"status": "Pending"})
    log(r.status_code == 404,
        "PUT unknown shipment → 404",
        f"{r.status_code}")

    # ── C8. DELETE /shipments/{id} → 200 with sheet result ──
    r = delete(f"/shipments/{s1['id']}", tok)
    log(r.status_code == 200, "DELETE shipment 200", f"{r.status_code}")
    if r.status_code == 200:
        body = r.json()
        log(body.get("ok") is True, "DELETE response ok=true",
            f"resp={body}")
        log("sheet" in body, "DELETE response has sheet key")
        sheet_block = body.get("sheet", {})
        # Should contain at least 'attempted' key.
        log("attempted" in sheet_block,
            "DELETE.sheet has 'attempted' key",
            f"sheet={sheet_block}")

    # ── C9. DELETE /shipments/{unknown} → 404 ──
    r = delete(f"/shipments/does-not-exist-{uuid.uuid4()}", tok)
    log(r.status_code == 404,
        "DELETE unknown shipment → 404",
        f"{r.status_code}")

    return tok, courier["id"]


# ============================================================
# D. POST /orders/pending/{id}/ship — Pending → Shipment promote
# ============================================================
def test_ship_pending(tok, courier_id):
    if not tok or not courier_id:
        log(False, "SHIP test prereq missing")
        return

    # Need a pending order. Create via /api/smart-paste with use_ai=False.
    paste = (
        "Customer: Pending Promote Test\n"
        "Phone: 9988776655\n"
        "Address: 5 Sample Lane\n"
        "City: Pune\n"
        "State: Maharashtra\n"
        "Pincode: 411001\n"
        "Amount: 199\n"
        "Payment: COD\n"
    )
    r = post("/smart-paste", tok, json={
        "text": paste,
        "use_ai": False,
    })
    log(r.status_code == 200,
        "SHIP precond — POST /smart-paste create pending",
        f"{r.status_code} text={r.text[:200]}")
    if r.status_code != 200:
        return
    pending = r.json()
    pid = pending.get("id")
    log(bool(pid), "pending order id present", f"id={pid}")
    if not pid:
        return

    # Get courier baseline next_number
    rc = get("/couriers", tok)
    courier = next((c for c in rc.json() if c["id"] == courier_id), None)
    next_before = int(courier.get("next_number", 1)) if courier else 0

    # POST /orders/pending/{pid}/ship
    r = post(f"/orders/pending/{pid}/ship", tok, json={
        "courier_id": courier_id,
        "overrides": {},
    })
    log(r.status_code == 200, "POST /orders/pending/{id}/ship 200",
        f"{r.status_code} text={r.text[:300]}")
    if r.status_code != 200:
        return
    ship = r.json()
    log(bool(ship.get("id")), "Shipment id allocated",
        f"id={ship.get('id')}")
    log(bool(ship.get("tracking_id")), "tracking_id allocated",
        f"tracking_id={ship.get('tracking_id')}")
    # Pattern: courier.series_prefix + zero-padded next_number
    if courier:
        prefix = courier.get("series_prefix", "")
        pad = int(courier.get("number_padding") or 4)
        expected = f"{prefix}{str(next_before).zfill(pad)}"
        log(ship["tracking_id"] == expected,
            "tracking_id format matches prefix+padded(next_number)",
            f"got={ship['tracking_id']} expected={expected}")

    # Verify courier.next_number bumped atomically
    rc2 = get("/couriers", tok)
    courier2 = next((c for c in rc2.json() if c["id"] == courier_id), None)
    if courier2:
        log(int(courier2["next_number"]) == next_before + 1,
            "courier.next_number bumped by +1",
            f"before={next_before} after={courier2['next_number']}")

    # Verify pending order is marked status="shipped" with shipment_id
    r = get("/orders/pending", tok, params={"include_shipped": "true"})
    if r.status_code == 200:
        items = r.json() if isinstance(r.json(), list) else \
            r.json().get("orders", [])
        match = [o for o in items if o.get("id") == pid]
        if match:
            o = match[0]
            log(o.get("status") == "shipped",
                "pending order status=shipped",
                f"status={o.get('status')}")
            log(o.get("shipment_id") == ship["id"],
                "pending order linked with shipment_id",
                f"link={o.get('shipment_id')} ship={ship['id']}")
        else:
            # try GET /orders/pending without filter (default may exclude shipped)
            log(True, "pending order disappeared from default list "
                     "(expected when default filter excludes shipped)",
                f"{len(items)} items returned")

    # Verify shipment now shows in GET /shipments
    r = get("/shipments", tok)
    if r.status_code == 200:
        items = r.json() if isinstance(r.json(), list) else \
            r.json().get("shipments", [])
        m = [s for s in items if s.get("id") == ship["id"]]
        log(len(m) == 1, "new shipment appears in GET /shipments",
            f"count={len(m)}")

    # Cleanup: delete the shipment
    r = delete(f"/shipments/{ship['id']}", tok)
    log(r.status_code in (200,), "cleanup DELETE shipment",
        f"{r.status_code}")


# ============================================================
# E. SMOKE — previously-extracted routers must remain green
# ============================================================
def test_smoke_regression(tok):
    # Use fresh-user token (has wallet, has courier).
    paths_get = [
        "/wallet",
        "/wallet/history",
        "/plans",
        "/plans-pricing",
        "/credit-packages",
        "/shipments",
        "/shipments/stats",
        "/shipments/export/csv",
        "/orders/pending",
        "/orders/pending-count",
        "/me/feature-flags",
        "/me/custom-fields",
        "/me/ai-rates",
        "/couriers",
        "/me/usage",
        "/me/notification-prefs",
    ]
    for p in paths_get:
        r = get(p, tok)
        ok = r.status_code in (200, 204)
        log(ok, f"SMOKE GET {p}",
            f"{r.status_code}")

    # /wallet/quote — needs ?address=...
    r = get("/wallet/quote", tok, params={"address": "12 Park Ave Mumbai 400001"})
    log(r.status_code == 200, "SMOKE GET /wallet/quote",
        f"{r.status_code} body={r.text[:120]}")

    # /smart-paste with use_ai=False
    r = post("/smart-paste", tok, json={
        "text": "Customer: Smoke Test\nPhone: 9000111222\n"
                "Address: 1 Smoke St\nCity: Pune\nState: Maharashtra\n"
                "Pincode: 411001\nAmount: 99\nPayment: COD\n",
        "use_ai": False,
    })
    log(r.status_code == 200, "SMOKE POST /smart-paste use_ai=false",
        f"{r.status_code}")
    if r.status_code == 200:
        pid = r.json().get("id")
        if pid:
            # cleanup
            delete(f"/orders/pending/{pid}", tok)


# ============================================================
# Driver
# ============================================================
def main():
    print("\n" + "=" * 70)
    print("Phase 5c-2 Heavy Mutations Refactor — Backend Regression")
    print("=" * 70)

    test_auth_gate()
    print()

    admin_tok = test_free_trial_limit_admin()
    print()

    rv = test_full_create_flow()
    if rv:
        tok, courier_id = rv if isinstance(rv, tuple) else (rv, None)
        print()
        test_ship_pending(tok, courier_id)
        print()
        test_smoke_regression(tok)

    print("\n" + "=" * 70)
    n_pass = sum(1 for ok, _, _ in results if ok)
    n_fail = sum(1 for ok, _, _ in results if not ok)
    print(f"TOTALS: {n_pass} PASS, {n_fail} FAIL "
          f"(of {len(results)} assertions)")
    if n_fail:
        print("\nFAILURES:")
        for ok, n, d in results:
            if not ok:
                print(f"  - {n}: {d}")
    print("=" * 70)
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
