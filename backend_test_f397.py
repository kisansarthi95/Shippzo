"""
Phase F3.9.7 — Abandoned-cart /recover endpoint RETEST.

Re-runs the 6 scenarios after the fix that moved _RecoverPayload to module
scope (so PEP-563 ForwardRefs resolve correctly).

Seeding strategy: create a temporary webhook with event_type=abandoned_order,
POST an abandoned-cart payload to /api/webhook/orders/{secret}, then look
up the cart by external_cart_id via /api/me/abandoned-carts?q=...
"""
import sys
import uuid
import json
from datetime import datetime, timezone

import requests

BASE = "https://logistics-hub-740.preview.emergentagent.com/api"
ADMIN = {"email": "admin@test.com", "password": "Admin@12345"}
USER2 = {"email": "user2@test.com", "password": "User@12345"}

results = []


def report(name, ok, detail=""):
    tag = "PASS" if ok else "FAIL"
    results.append((tag, name, detail))
    print(f"[{tag}] {name} — {detail}")


def login(creds):
    r = requests.post(f"{BASE}/auth/login", json=creds, timeout=20)
    r.raise_for_status()
    return r.json()["token"]


def auth(tok):
    return {"Authorization": f"Bearer {tok}"}


def create_test_webhook(tok, label):
    r = requests.post(
        f"{BASE}/me/webhooks",
        json={"name": f"F397-{label}", "event_type": "abandoned_order"},
        headers=auth(tok), timeout=15,
    )
    r.raise_for_status()
    d = r.json()
    return d["id"], d["secret"]


def delete_webhook(tok, wh_id):
    try:
        requests.delete(
            f"{BASE}/me/webhooks/{wh_id}", headers=auth(tok), timeout=15,
        )
    except Exception:
        pass


def seed_abandoned_cart(secret, label):
    """Seed via public webhook ingest. Returns external_cart_id."""
    ext = f"TEST-CART-F397-{label}-{uuid.uuid4().hex[:6]}"
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "cart_id":        ext,
        "customer_name":  f"Riya Patel F397 {label}",
        "customer_phone": "9876512340",
        "customer_email": "riya.f397@example.com",
        "address":        "12 Test Lane, Apt 4",
        "city":           "Ahmedabad",
        "state":          "Gujarat",
        "pincode":        "380015",
        "total_price":    1499.00,
        "items":          [{"name": "Honey 1kg", "qty": 2}],
        "abandoned_at":   now,
        "abandoned_checkout_url": "https://shop.example.com/recover/abc123",
    }
    r = requests.post(
        f"{BASE}/webhook/orders/{secret}",
        json=payload, timeout=20,
    )
    return ext, r


def find_cart_id_by_ext(tok, ext_id):
    r = requests.get(
        f"{BASE}/me/abandoned-carts?q={ext_id}",
        headers=auth(tok), timeout=15,
    )
    if r.status_code != 200:
        return None
    carts = r.json().get("carts") or []
    for c in carts:
        if c.get("external_cart_id") == ext_id:
            return c["id"]
    return carts[0]["id"] if carts else None


def main():
    artifact_pids = []
    artifact_cart_ids = []
    artifact_webhooks = []

    # ---------- AUTH ----------
    try:
        tok_admin = login(ADMIN)
        report("login admin@test.com", True, "got token")
    except Exception as e:
        report("login admin@test.com", False, str(e))
        return summarise()

    try:
        tok_user2 = login(USER2)
        report("login user2@test.com", True, "got token")
    except Exception as e:
        report("login user2@test.com", False, str(e))
        tok_user2 = None

    # ---------- PRE-CHECK: bare /recover no-auth → 401 ----------
    r = requests.post(
        f"{BASE}/me/abandoned-carts/nonexistent/recover", timeout=15,
    )
    report(
        "PRE-CHECK — bare /recover (no auth) returns 401 (no 500)",
        r.status_code in (401, 403),
        f"HTTP {r.status_code}",
    )

    # ---------- CREATE TEST WEBHOOK ----------
    try:
        wh_id, wh_secret = create_test_webhook(tok_admin, "main")
        artifact_webhooks.append(wh_id)
        report("create test abandoned_order webhook", True, f"wh={wh_id[:8]}...")
    except Exception as e:
        report("create test abandoned_order webhook", False, str(e))
        return summarise()

    # ---------- SCENARIO 1: SEED CART ----------
    ext1, ingest_r = seed_abandoned_cart(wh_secret, "S1")
    seed_ok = ingest_r.status_code == 200
    report(
        "SCENARIO 1 — seed test cart via webhook ingest",
        seed_ok,
        f"HTTP {ingest_r.status_code} ext={ext1}",
    )
    if not seed_ok:
        print("ingest body:", ingest_r.text[:400])

    cid1 = find_cart_id_by_ext(tok_admin, ext1)
    if cid1:
        artifact_cart_ids.append(cid1)
    report(
        "SCENARIO 1 — cart visible in list",
        bool(cid1),
        f"id={cid1[:8] if cid1 else 'NONE'}",
    )
    if not cid1:
        # cleanup webhook + bail
        for wid in artifact_webhooks:
            delete_webhook(tok_admin, wid)
        return summarise()

    # ---------- SCENARIO 2: POST /recover {} → 200, NO pending_order ----------
    r = requests.post(
        f"{BASE}/me/abandoned-carts/{cid1}/recover",
        json={}, headers=auth(tok_admin), timeout=20,
    )
    s2_ok = r.status_code == 200
    s2_body = {}
    try:
        s2_body = r.json()
    except Exception:
        pass
    report(
        "SCENARIO 2 — POST /recover {} returns 200",
        s2_ok,
        f"HTTP {r.status_code} body={json.dumps(s2_body)[:250]}",
    )
    report(
        "SCENARIO 2 — response has NO 'pending_order' key",
        s2_ok and "pending_order" not in s2_body,
        f"pending_order present? {'pending_order' in s2_body}",
    )
    report(
        "SCENARIO 2 — response has ok=true + pending_order_id + master_order_id",
        s2_ok
        and s2_body.get("ok") is True
        and bool(s2_body.get("pending_order_id"))
        and bool(s2_body.get("master_order_id")),
        f"ok={s2_body.get('ok')} pid={s2_body.get('pending_order_id')} moid={s2_body.get('master_order_id')}",
    )
    s2_pending_id = s2_body.get("pending_order_id")
    if s2_pending_id:
        artifact_pids.append(s2_pending_id)

    # ---------- SCENARIO 3: idempotent re-recover w/ create_shipment=true ----------
    r = requests.post(
        f"{BASE}/me/abandoned-carts/{cid1}/recover",
        json={"create_shipment": True},
        headers=auth(tok_admin), timeout=20,
    )
    s3_ok = r.status_code == 200
    s3_body = {}
    try:
        s3_body = r.json()
    except Exception:
        pass
    report(
        "SCENARIO 3 — re-recover create_shipment=true returns 200",
        s3_ok,
        f"HTTP {r.status_code} keys={list(s3_body.keys())}",
    )
    report(
        "SCENARIO 3 — already_recovered=true",
        s3_ok and s3_body.get("already_recovered") is True,
        f"already_recovered={s3_body.get('already_recovered')}",
    )
    po = s3_body.get("pending_order")
    s3_po_ok = isinstance(po, dict) and bool(po)
    report(
        "SCENARIO 3 — pending_order is a full dict",
        s3_ok and s3_po_ok,
        f"type={type(po).__name__} "
        f"len_keys={len(po) if isinstance(po, dict) else 0}",
    )
    if isinstance(po, dict):
        report(
            "SCENARIO 3 — pending_order.source == 'abandoned_cart'",
            po.get("source") == "abandoned_cart",
            f"source={po.get('source')}",
        )
        report(
            "SCENARIO 3 — pending_order.id matches scenario-2 pending_order_id",
            po.get("id") == s2_pending_id,
            f"po.id={po.get('id')} s2.pid={s2_pending_id}",
        )

    # ---------- SCENARIO 4: fresh cart with create_shipment=true ----------
    ext2, ingest_r2 = seed_abandoned_cart(wh_secret, "S4")
    seed2_ok = ingest_r2.status_code == 200
    cid2 = find_cart_id_by_ext(tok_admin, ext2) if seed2_ok else None
    if cid2:
        artifact_cart_ids.append(cid2)
    report(
        "SCENARIO 4 — seed FRESH cart",
        bool(cid2),
        f"ext={ext2} id={cid2[:8] if cid2 else 'NONE'}",
    )

    s4_pending_id = None
    if cid2:
        r = requests.post(
            f"{BASE}/me/abandoned-carts/{cid2}/recover",
            json={"create_shipment": True},
            headers=auth(tok_admin), timeout=20,
        )
        s4_ok = r.status_code == 200
        s4_body = {}
        try:
            s4_body = r.json()
        except Exception:
            pass
        report(
            "SCENARIO 4 — POST /recover create_shipment=true returns 200",
            s4_ok,
            f"HTTP {r.status_code} keys={list(s4_body.keys())}",
        )
        po4 = s4_body.get("pending_order")
        report(
            "SCENARIO 4 — pending_order populated (full dict)",
            s4_ok and isinstance(po4, dict) and bool(po4),
            f"type={type(po4).__name__} "
            f"id={po4.get('id') if isinstance(po4, dict) else None} "
            f"source={po4.get('source') if isinstance(po4, dict) else None}",
        )
        report(
            "SCENARIO 4 — already_recovered NOT true (fresh)",
            s4_ok and not s4_body.get("already_recovered"),
            f"already_recovered={s4_body.get('already_recovered')}",
        )
        if isinstance(po4, dict):
            s4_pending_id = po4.get("id")
            if s4_pending_id:
                artifact_pids.append(s4_pending_id)
            report(
                "SCENARIO 4 — pending_order.source == 'abandoned_cart'",
                po4.get("source") == "abandoned_cart",
                f"source={po4.get('source')}",
            )

        # Verify cart status flipped in DB
        r2 = requests.get(
            f"{BASE}/me/abandoned-carts/{cid2}",
            headers=auth(tok_admin), timeout=15,
        )
        if r2.status_code == 200:
            cart_status = r2.json().get("status")
            cart_pid    = r2.json().get("pending_order_id")
            report(
                "SCENARIO 4 — cart.status == 'recovered' in DB",
                cart_status == "recovered",
                f"status={cart_status}",
            )
            report(
                "SCENARIO 4 — cart.pending_order_id matches po.id",
                cart_pid == s4_pending_id,
                f"cart.pid={cart_pid} po.id={s4_pending_id}",
            )
        else:
            report(
                "SCENARIO 4 — fetch cart after recover",
                False,
                f"HTTP {r2.status_code}",
            )

    # ---------- SCENARIO 5: Multi-tenant isolation ----------
    if tok_user2 and cid1:
        r = requests.post(
            f"{BASE}/me/abandoned-carts/{cid1}/recover",
            json={}, headers=auth(tok_user2), timeout=15,
        )
        report(
            "SCENARIO 5 — cross-tenant POST /recover returns 404",
            r.status_code == 404,
            f"HTTP {r.status_code}",
        )
        r2 = requests.get(
            f"{BASE}/me/abandoned-carts/{cid1}",
            headers=auth(tok_user2), timeout=15,
        )
        report(
            "SCENARIO 5 — cross-tenant GET cart returns 404",
            r2.status_code == 404,
            f"HTTP {r2.status_code}",
        )
        # Also: user2 should not see admin's pending order in their list
        if s2_pending_id:
            r3 = requests.get(
                f"{BASE}/orders/pending/{s2_pending_id}",
                headers=auth(tok_user2), timeout=15,
            )
            report(
                "SCENARIO 5 — cross-tenant pending order isolated",
                r3.status_code in (403, 404),
                f"HTTP {r3.status_code}",
            )
    else:
        report(
            "SCENARIO 5 — multi-tenant isolation",
            True,
            "SKIPPED (user2 unavailable)",
        )

    # ---------- SCENARIO 6: Cleanup ----------
    deleted = 0
    for pid in artifact_pids:
        r = requests.delete(
            f"{BASE}/orders/pending/{pid}",
            headers=auth(tok_admin), timeout=15,
        )
        if r.status_code in (200, 404):
            deleted += 1
    for cid in artifact_cart_ids:
        r = requests.delete(
            f"{BASE}/me/abandoned-carts/{cid}",
            headers=auth(tok_admin), timeout=15,
        )
        if r.status_code in (200, 404):
            deleted += 1
    for wid in artifact_webhooks:
        delete_webhook(tok_admin, wid)
        deleted += 1
    report(
        "SCENARIO 6 — cleanup test artifacts",
        True,
        f"deleted {deleted} (pending={len(artifact_pids)}, carts={len(artifact_cart_ids)}, webhooks={len(artifact_webhooks)})",
    )

    return summarise()


def summarise():
    passes = sum(1 for r in results if r[0] == "PASS")
    fails  = sum(1 for r in results if r[0] == "FAIL")
    print("\n========== SUMMARY ==========")
    print(f"PASS: {passes}  FAIL: {fails}")
    for tag, name, detail in results:
        print(f"  [{tag}] {name}")
    return fails


if __name__ == "__main__":
    sys.exit(main() or 0)
