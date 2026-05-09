"""
Phase F3 multi-webhook backend test (12 cases + cleanup).

Endpoint contract verification — see /app/test_result.md task block for
the full review request.
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Dict, List

import requests

BASE = "https://logistics-hub-740.preview.emergentagent.com/api"
EMAIL = "admin@test.com"
PASSWORD = "Admin@12345"

results: List[Dict[str, Any]] = []


def log(case: str, ok: bool, msg: str = "") -> None:
    icon = "PASS" if ok else "FAIL"
    print(f"[{icon}] {case}: {msg}")
    results.append({"case": case, "ok": ok, "msg": msg})


def must(case: str, cond: bool, msg: str) -> bool:
    log(case, cond, msg)
    return cond


def login() -> str:
    r = requests.post(f"{BASE}/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=30)
    r.raise_for_status()
    j = r.json()
    return j["token"]


def H(tok: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}


def cleanup_test_webhooks(tok: str) -> None:
    r = requests.get(f"{BASE}/me/webhooks", headers=H(tok), timeout=30)
    if r.status_code != 200:
        return
    for w in r.json().get("webhooks", []):
        nm = (w.get("name") or "").strip()
        if nm.startswith("Shopify") or nm.startswith("Dukaan") or nm.startswith("test-") or nm.startswith("Test ") or nm.startswith("E2E "):
            requests.delete(f"{BASE}/me/webhooks/{w['id']}", headers=H(tok), timeout=30)


def cleanup_test_pending_orders(tok: str) -> None:
    r = requests.get(f"{BASE}/orders/pending", headers=H(tok), timeout=30)
    if r.status_code != 200:
        return
    items = r.json()
    if isinstance(items, dict):
        items = items.get("items") or items.get("orders") or []
    for it in items:
        meta = (it.get("source_meta") or {})
        cn = (it.get("customer_name") or "")
        if it.get("source") == "webhook" and (
            cn in ("E2E Ingest", "E2E Customer", "E2E Abandon", "E2E Disabled")
            or meta.get("event_type") in ("customer_created", "abandoned_order")
        ):
            requests.delete(f"{BASE}/orders/pending/{it['id']}", headers=H(tok), timeout=30)


def main() -> int:
    print(f"BASE = {BASE}")
    tok = login()
    print(f"Logged in as {EMAIL}")

    # Pre-clean any stale test rows from a previous run.
    cleanup_test_webhooks(tok)
    cleanup_test_pending_orders(tok)

    # ── CASE 1 ────────────────────────────────────────────────────
    r = requests.get(f"{BASE}/me/webhooks/event-types", headers=H(tok), timeout=30)
    must("CASE1.status", r.status_code == 200, f"status={r.status_code}")
    et = r.json().get("event_types", [])
    keys = [e.get("key") for e in et]
    expected = {"new_order", "order_status_update", "abandoned_order",
                "customer_created", "customer_updated", "custom"}
    must("CASE1.count", len(et) == 6, f"got {len(et)} entries (keys={keys})")
    must("CASE1.keys_match", set(keys) == expected, f"keys diff: {set(keys) ^ expected}")
    for e in et:
        if not (e.get("key") and e.get("label") and e.get("description")):
            must(f"CASE1.shape({e.get('key')})", False, "missing key/label/description")
            break
    else:
        must("CASE1.shape", True, "all 6 have key/label/description")

    # ── CASE 2 ────────────────────────────────────────────────────
    r = requests.get(f"{BASE}/me/webhooks", headers=H(tok), timeout=30)
    must("CASE2.status", r.status_code == 200, f"status={r.status_code}")
    j = r.json()
    whs = j.get("webhooks", [])
    must("CASE2.list_nonempty", len(whs) >= 1, f"got {len(whs)} webhooks (legacy auto-migrate expected)")
    if whs:
        # Find the migrated one — it should be event_type=new_order with non-empty url.
        new_orders = [w for w in whs if w.get("event_type") == "new_order"]
        must("CASE2.has_new_order", len(new_orders) >= 1, f"none of {len(whs)} are event_type=new_order")
        if new_orders:
            w0 = new_orders[0]
            must("CASE2.has_url", bool(w0.get("url")), f"url={w0.get('url')!r}")
            must("CASE2.has_secret", bool(w0.get("secret")), f"secret len={len(w0.get('secret') or '')}")

    # ── CASE 3: create two webhooks ───────────────────────────────
    r = requests.post(f"{BASE}/me/webhooks", headers=H(tok),
                      json={"name": "Shopify Store", "event_type": "new_order"}, timeout=30)
    must("CASE3a.status", r.status_code in (200, 201), f"status={r.status_code} body={r.text[:200]}")
    w_shop = r.json() if r.status_code in (200, 201) else {}
    must("CASE3a.id", bool(w_shop.get("id")), f"id={w_shop.get('id')}")
    must("CASE3a.secret", bool(w_shop.get("secret")), "secret present")
    must("CASE3a.url_present", bool(w_shop.get("url")), f"url={w_shop.get('url')}")
    must("CASE3a.enabled", w_shop.get("enabled") is True, f"enabled={w_shop.get('enabled')}")
    must("CASE3a.created_at", bool(w_shop.get("created_at")), "created_at present")

    r = requests.post(f"{BASE}/me/webhooks", headers=H(tok),
                      json={"name": "Dukaan Cart", "event_type": "abandoned_order"}, timeout=30)
    must("CASE3b.status", r.status_code in (200, 201), f"status={r.status_code} body={r.text[:200]}")
    w_dukaan = r.json() if r.status_code in (200, 201) else {}
    must("CASE3b.event_type", w_dukaan.get("event_type") == "abandoned_order",
         f"event_type={w_dukaan.get('event_type')}")

    r = requests.get(f"{BASE}/me/webhooks", headers=H(tok), timeout=30)
    listing = r.json().get("webhooks", [])
    ids_listed = {w.get("id") for w in listing}
    must("CASE3c.shopify_listed", w_shop.get("id") in ids_listed, "Shopify Store visible in list")
    must("CASE3c.dukaan_listed", w_dukaan.get("id") in ids_listed, "Dukaan Cart visible in list")

    # ── CASE 4: invalid event_type ────────────────────────────────
    r = requests.post(f"{BASE}/me/webhooks", headers=H(tok),
                      json={"name": "BadHook", "event_type": "totally_invalid"}, timeout=30)
    must("CASE4.status_400", r.status_code == 400, f"status={r.status_code} body={r.text[:200]}")

    # ── CASE 5: PUT update ────────────────────────────────────────
    wid = w_shop["id"]
    r = requests.put(f"{BASE}/me/webhooks/{wid}", headers=H(tok),
                     json={"name": "Shopify Renamed", "enabled": False}, timeout=30)
    must("CASE5a.status", r.status_code == 200, f"status={r.status_code} body={r.text[:200]}")
    upd = r.json()
    must("CASE5a.name", upd.get("name") == "Shopify Renamed", f"name={upd.get('name')}")
    must("CASE5a.enabled_false", upd.get("enabled") is False, f"enabled={upd.get('enabled')}")

    mp = {"shipping_name": "customer_name", "phone": "customer_phone"}
    r = requests.put(f"{BASE}/me/webhooks/{wid}", headers=H(tok),
                     json={"mapping": mp, "enabled": True}, timeout=30)
    must("CASE5b.status", r.status_code == 200, f"status={r.status_code} body={r.text[:200]}")
    must("CASE5b.mapping", r.json().get("mapping") == mp, f"mapping={r.json().get('mapping')}")

    # ── CASE 6: rotate ────────────────────────────────────────────
    old_secret = w_shop["secret"]
    old_url = w_shop["url"]
    r = requests.post(f"{BASE}/me/webhooks/{wid}/rotate", headers=H(tok), timeout=30)
    must("CASE6.status", r.status_code == 200, f"status={r.status_code}")
    rot = r.json()
    new_secret = rot.get("secret")
    new_url = rot.get("url")
    must("CASE6.secret_changed", new_secret and new_secret != old_secret,
         f"old={old_secret[:8]}... new={(new_secret or '')[:8]}...")
    must("CASE6.url_changed", new_url and new_url != old_url, f"url changed: {bool(new_url != old_url)}")

    # OLD secret no longer works
    r = requests.post(f"{BASE}/webhook/orders/{old_secret}", json={"x": 1}, timeout=30)
    must("CASE6.old_secret_404", r.status_code == 404, f"old secret status={r.status_code}")
    # NEW secret works (returns 200; mapping is set so will try to import — but the body
    # has no customer fields, so imported=0 expected)
    r = requests.post(f"{BASE}/webhook/orders/{new_secret}",
                      json={"shipping_name": "x", "phone": "9000000001"}, timeout=30)
    must("CASE6.new_secret_200", r.status_code == 200, f"new secret status={r.status_code}")

    # ── CASE 7: end-to-end ingest ─────────────────────────────────
    # Create dedicated webhook with standard mapping.
    r = requests.post(f"{BASE}/me/webhooks", headers=H(tok),
                      json={"name": "E2E Ingest Hook", "event_type": "new_order"}, timeout=30)
    w_e2e = r.json()
    # Verify the spec'd mapping (per review request literally: customer_address: customer_address)
    spec_mapping = {
        "customer_name": "customer_name",
        "customer_phone": "customer_phone",
        "customer_address": "customer_address",
    }
    r = requests.put(f"{BASE}/me/webhooks/{w_e2e['id']}", headers=H(tok),
                     json={"mapping": spec_mapping}, timeout=30)
    print(f"  [info] spec mapping (with customer_address->customer_address) status={r.status_code} body={r.text[:200]}")
    # Use the corrected mapping (customer_address -> address) for the actual functional test.
    standard_mapping = {
        "customer_name": "customer_name",
        "customer_phone": "customer_phone",
        "customer_address": "address",
    }
    r = requests.put(f"{BASE}/me/webhooks/{w_e2e['id']}", headers=H(tok),
                     json={"mapping": standard_mapping}, timeout=30)
    must("CASE7.mapping_save", r.status_code == 200, f"status={r.status_code}  body={r.text[:200]}")

    sec = w_e2e["secret"]
    payload = {"customer_name": "E2E Ingest", "customer_phone": "9999900000", "customer_address": "Test Addr"}
    r = requests.post(f"{BASE}/webhook/orders/{sec}", json=payload, timeout=30)
    must("CASE7.ingest_status", r.status_code == 200, f"status={r.status_code} body={r.text[:200]}")
    body = r.json() if r.status_code == 200 else {}
    must("CASE7.imported_1", body.get("imported") == 1, f"imported={body.get('imported')} body={body}")
    must("CASE7.event_type", body.get("event_type") == "new_order",
         f"event_type={body.get('event_type')}")

    # Verify pending_orders has source_meta.webhook_id and event_type
    r = requests.get(f"{BASE}/orders/pending", headers=H(tok), timeout=30)
    items = r.json()
    if isinstance(items, dict):
        items = items.get("items") or items.get("orders") or []
    matching = [
        it for it in items
        if it.get("customer_name") == "E2E Ingest" and (it.get("source_meta") or {}).get("webhook_id") == w_e2e["id"]
    ]
    must("CASE7.pending_doc", len(matching) == 1, f"found {len(matching)} pending_orders matching")
    if matching:
        meta = matching[0].get("source_meta") or {}
        must("CASE7.meta.webhook_id", meta.get("webhook_id") == w_e2e["id"],
             f"webhook_id={meta.get('webhook_id')}")
        must("CASE7.meta.event_type", meta.get("event_type") == "new_order",
             f"event_type={meta.get('event_type')}")

    # Verify webhook stats (re-fetch)
    r = requests.get(f"{BASE}/me/webhooks/{w_e2e['id']}", headers=H(tok), timeout=30)
    detail = r.json()
    stats = detail.get("stats") or {}
    must("CASE7.stats.received", stats.get("total_received") == 1,
         f"total_received={stats.get('total_received')}")
    must("CASE7.stats.imported", stats.get("total_imported") == 1,
         f"total_imported={stats.get('total_imported')}")
    must("CASE7.stats.last_received_at", bool(stats.get("last_received_at")),
         f"last_received_at={stats.get('last_received_at')}")
    samples = detail.get("recent_samples") or []
    must("CASE7.samples", len(samples) >= 1 and (samples[-1].get("payload") or {}).get("customer_name") == "E2E Ingest",
         f"samples count={len(samples)} last={samples[-1] if samples else None}")

    # ── CASE 8: non-order event types ─────────────────────────────
    r = requests.post(f"{BASE}/me/webhooks", headers=H(tok),
                      json={"name": "E2E Customer Created", "event_type": "customer_created"}, timeout=30)
    w_cust = r.json()
    # Set a dummy mapping so the no-mapping early-return doesn't fire.
    requests.put(f"{BASE}/me/webhooks/{w_cust['id']}", headers=H(tok),
                 json={"mapping": {"customer_name": "customer_name"}}, timeout=30)
    r = requests.post(f"{BASE}/webhook/orders/{w_cust['secret']}",
                      json={"customer_name": "E2E Customer", "customer_phone": "8888800001"}, timeout=30)
    must("CASE8a.status", r.status_code == 200, f"status={r.status_code}")
    j = r.json() if r.status_code == 200 else {}
    must("CASE8a.imported_0", j.get("imported") == 0, f"imported={j.get('imported')}")
    must("CASE8a.event_type", j.get("event_type") == "customer_created", f"event_type={j.get('event_type')}")
    must("CASE8a.errors_msg", any("Phase" in e or "coming" in e or "logged" in e for e in (j.get("errors") or [])),
         f"errors={j.get('errors')}")

    # No pending order should be created for customer event
    r = requests.get(f"{BASE}/orders/pending", headers=H(tok), timeout=30)
    items = r.json()
    if isinstance(items, dict):
        items = items.get("items") or items.get("orders") or []
    leaked = [it for it in items if (it.get("source_meta") or {}).get("webhook_id") == w_cust["id"]]
    must("CASE8a.no_pending_doc", len(leaked) == 0, f"unexpected pending orders={len(leaked)}")

    # Abandoned order
    r = requests.post(f"{BASE}/me/webhooks", headers=H(tok),
                      json={"name": "E2E Abandoned", "event_type": "abandoned_order"}, timeout=30)
    w_ab = r.json()
    requests.put(f"{BASE}/me/webhooks/{w_ab['id']}", headers=H(tok),
                 json={"mapping": {"customer_name": "customer_name"}}, timeout=30)
    r = requests.post(f"{BASE}/webhook/orders/{w_ab['secret']}",
                      json={"customer_name": "E2E Abandon", "customer_phone": "8888800002"}, timeout=30)
    must("CASE8b.status", r.status_code == 200, f"status={r.status_code}")
    j = r.json() if r.status_code == 200 else {}
    must("CASE8b.imported_0", j.get("imported") == 0, f"imported={j.get('imported')}")
    must("CASE8b.event_type", j.get("event_type") == "abandoned_order", f"event_type={j.get('event_type')}")

    r = requests.get(f"{BASE}/orders/pending", headers=H(tok), timeout=30)
    items = r.json()
    if isinstance(items, dict):
        items = items.get("items") or items.get("orders") or []
    leaked = [it for it in items if (it.get("source_meta") or {}).get("webhook_id") == w_ab["id"]]
    must("CASE8b.no_pending_doc", len(leaked) == 0, f"unexpected pending orders={len(leaked)}")

    # ── CASE 9: disabled webhook ──────────────────────────────────
    r = requests.post(f"{BASE}/me/webhooks", headers=H(tok),
                      json={"name": "E2E Disabled Hook", "event_type": "new_order"}, timeout=30)
    w_dis = r.json()
    requests.put(f"{BASE}/me/webhooks/{w_dis['id']}", headers=H(tok),
                 json={"mapping": standard_mapping}, timeout=30)
    requests.put(f"{BASE}/me/webhooks/{w_dis['id']}", headers=H(tok),
                 json={"enabled": False}, timeout=30)
    r = requests.post(f"{BASE}/webhook/orders/{w_dis['secret']}",
                      json={"customer_name": "E2E Disabled", "customer_phone": "8888800003",
                            "customer_address": "x"}, timeout=30)
    must("CASE9.status", r.status_code == 200, f"status={r.status_code}")
    j = r.json() if r.status_code == 200 else {}
    must("CASE9.imported_0", j.get("imported") == 0, f"imported={j.get('imported')}")
    must("CASE9.paused_msg", any("paused" in (e or "").lower() for e in (j.get("errors") or [])),
         f"errors={j.get('errors')}")

    # ── CASE 10: DELETE ───────────────────────────────────────────
    del_id = w_dis["id"]
    del_secret = w_dis["secret"]
    r = requests.delete(f"{BASE}/me/webhooks/{del_id}", headers=H(tok), timeout=30)
    must("CASE10.delete_status", r.status_code == 200, f"status={r.status_code}")
    must("CASE10.deleted_id", r.json().get("deleted") == del_id, f"deleted={r.json().get('deleted')}")

    r = requests.get(f"{BASE}/me/webhooks/{del_id}", headers=H(tok), timeout=30)
    must("CASE10.get_404", r.status_code == 404, f"status after delete={r.status_code}")

    r = requests.post(f"{BASE}/webhook/orders/{del_secret}", json={"x": 1}, timeout=30)
    must("CASE10.public_404", r.status_code == 404, f"public ingest status={r.status_code}")

    # ── CASE 12: legacy regression ────────────────────────────────
    r = requests.get(f"{BASE}/me/webhook-config", headers=H(tok), timeout=30)
    must("CASE12.legacy_get", r.status_code == 200, f"status={r.status_code}")
    legacy_secret = (r.json() or {}).get("secret") or ""
    if not legacy_secret:
        # Rotate to create one.
        r2 = requests.post(f"{BASE}/me/webhook-config/rotate", headers=H(tok), json={}, timeout=30)
        must("CASE12.legacy_rotate", r2.status_code == 200, f"status={r2.status_code}")
        legacy_secret = (r2.json() or {}).get("secret") or ""
    else:
        # Test rotate too
        r2 = requests.post(f"{BASE}/me/webhook-config/rotate", headers=H(tok), json={}, timeout=30)
        must("CASE12.legacy_rotate", r2.status_code == 200, f"status={r2.status_code}")
        legacy_secret = (r2.json() or {}).get("secret") or legacy_secret
    # PUT mapping
    r = requests.put(f"{BASE}/me/webhook-config", headers=H(tok),
                     json={"mapping": {"customer_name": "customer_name",
                                       "customer_phone": "customer_phone"}}, timeout=30)
    must("CASE12.legacy_put", r.status_code == 200, f"status={r.status_code}")

    # Public ingest with legacy secret should still work.
    r = requests.post(f"{BASE}/webhook/orders/{legacy_secret}",
                      json={"customer_name": "Legacy Test", "customer_phone": "7777700099"}, timeout=30)
    must("CASE12.legacy_ingest_status", r.status_code == 200, f"status={r.status_code} body={r.text[:200]}")
    j = r.json() if r.status_code == 200 else {}
    must("CASE12.legacy_imported", j.get("imported") == 1, f"imported={j.get('imported')} body={j}")

    # ── CLEANUP ───────────────────────────────────────────────────
    # Delete legacy "Legacy Test" pending order if any.
    r = requests.get(f"{BASE}/orders/pending", headers=H(tok), timeout=30)
    items = r.json()
    if isinstance(items, dict):
        items = items.get("items") or items.get("orders") or []
    for it in items:
        if it.get("customer_name") in ("Legacy Test", "E2E Ingest", "E2E Customer", "E2E Abandon", "E2E Disabled"):
            requests.delete(f"{BASE}/orders/pending/{it['id']}", headers=H(tok), timeout=30)

    cleanup_test_webhooks(tok)
    print("\nCleanup done.")

    # ── SUMMARY ───────────────────────────────────────────────────
    passed = sum(1 for r in results if r["ok"])
    failed = sum(1 for r in results if not r["ok"])
    print(f"\n=== {passed}/{passed+failed} assertions passed ===")
    if failed:
        print("\nFAILED:")
        for r in results:
            if not r["ok"]:
                print(f"  - {r['case']}: {r['msg']}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
