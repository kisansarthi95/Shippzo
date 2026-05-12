#!/usr/bin/env python3
"""
Phase-19 — Two-button stage flow + Modified flag backend tests.

Scenarios:
 1. Model fields surfaced (is_modified + modified_at on every shipment)
 2. Pure status flip — NO tag set
 3. Status + content edit — TAG SET
 4. Status flip after tagged — TAG STAYS
 5. Modified virtual filter (?status=Modified)
 6. Other status filters unaffected
 7. Reset for cleanliness (status flip after tagged still keeps tag)
 8. Multi-tenant isolation
"""
import os
import re
import sys
import json
import uuid
import requests
from datetime import datetime

BACKEND = "https://logistics-hub-740.preview.emergentagent.com"
API = f"{BACKEND}/api"

USER_A = {"email": "admin@test.com",  "password": "Admin@12345"}
USER_B = {"email": "user2@test.com",  "password": "User@12345"}


def login(creds):
    r = requests.post(f"{API}/auth/login", json=creds, timeout=30)
    r.raise_for_status()
    body = r.json()
    return body["token"], body.get("id")


def auth_h(token):
    return {"Authorization": f"Bearer {token}"}


def pretty(obj, max_len=600):
    s = json.dumps(obj, default=str)
    return s if len(s) <= max_len else s[:max_len] + "…(truncated)"


# ──────────────────────────────────────────────────────────────────
# Scenario runners
# ──────────────────────────────────────────────────────────────────


def main():
    results = []

    def record(name, ok, details):
        results.append({"name": name, "ok": ok, "details": details})
        flag = "PASS" if ok else "FAIL"
        print(f"\n[{flag}] {name}\n   {details}")

    # Login user A
    try:
        token_a, uid_a = login(USER_A)
        print(f"User A logged in: id={uid_a}")
    except Exception as e:
        record("Login user A", False, f"login failed: {e}")
        return finalize(results)

    headers_a = auth_h(token_a)

    # ── Scenario 1: model fields surfaced ──
    try:
        r = requests.get(f"{API}/shipments", headers=headers_a, timeout=30)
        ok = r.status_code == 200
        items = r.json() if ok else []
        if not isinstance(items, list) or not items:
            record("S1 Model fields surfaced", False,
                   f"HTTP {r.status_code}, list empty or wrong type, sample={pretty(items)[:200]}")
            return finalize(results)
        # Every item must have is_modified (bool) + modified_at (str|null)
        missing = []
        for s in items[:50]:
            if "is_modified" not in s or "modified_at" not in s:
                missing.append(s.get("id"))
            elif not isinstance(s["is_modified"], bool):
                missing.append(s.get("id"))
        if missing:
            record("S1 Model fields surfaced", False,
                   f"HTTP 200 but {len(missing)} items missing/wrong-type fields, sample_ids={missing[:5]}")
            return finalize(results)
        # Find a shipment we can experiment on — prefer one with is_modified=false
        cand = next((s for s in items if s.get("is_modified") is False), items[0])
        record("S1 Model fields surfaced", True,
               f"HTTP 200 · {len(items)} shipments returned · all have is_modified+modified_at · "
               f"chosen id={cand['id'][:8]}…  current is_modified={cand['is_modified']}, "
               f"modified_at={cand.get('modified_at')}, status={cand.get('status')}")
        target_id = cand["id"]
        target_initial_status = cand.get("status")
    except Exception as e:
        record("S1 Model fields surfaced", False, f"exception: {e}")
        return finalize(results)

    # If chosen shipment is already modified, find a fresher candidate via create
    chosen_was_modified = cand.get("is_modified") is True

    # ── Scenario 2: pure status flip — NO tag ──
    # We need a clean shipment with is_modified=false. If our target already
    # has the flag, skip the negative check; if not, proceed.
    if chosen_was_modified:
        # find another untouched one
        clean = next((s for s in items if s.get("is_modified") is False), None)
        if clean is None:
            record("S2 Pure status flip — NO tag", False,
                   "Could not find any shipment with is_modified=false (entire workspace is already tagged)")
        else:
            target_id = clean["id"]
            target_initial_status = clean.get("status")
            chosen_was_modified = False

    if not chosen_was_modified:
        try:
            r = requests.put(f"{API}/shipments/{target_id}",
                             headers=headers_a,
                             json={"status": "Processing"}, timeout=30)
            put_ok = r.status_code == 200
            put_body = r.json() if put_ok else r.text
            # Verify via GET
            g = requests.get(f"{API}/shipments/{target_id}", headers=headers_a, timeout=30)
            gbody = g.json() if g.status_code == 200 else {}
            still_unmod = (gbody.get("is_modified") is False and gbody.get("modified_at") in (None, ""))
            ok = put_ok and still_unmod
            record("S2 Pure status flip — NO tag", ok,
                   f"PUT→{r.status_code} ({pretty(put_body)[:200]})  ·  GET→{g.status_code} "
                   f"is_modified={gbody.get('is_modified')}, modified_at={gbody.get('modified_at')}, "
                   f"status={gbody.get('status')}")
        except Exception as e:
            record("S2 Pure status flip — NO tag", False, f"exception: {e}")
    else:
        record("S2 Pure status flip — NO tag", False,
               "Skipped — couldn't locate untouched shipment; cannot verify clean→stays-clean")

    # ── Scenario 3: status + content edit — TAG SET ──
    try:
        r = requests.put(f"{API}/shipments/{target_id}", headers=headers_a,
                         json={"status": "Ready to Ship",
                               "customer_name": "Phase19 Edit Test"},
                         timeout=30)
        put_ok = r.status_code == 200
        put_body = r.json() if put_ok else r.text
        g = requests.get(f"{API}/shipments/{target_id}", headers=headers_a, timeout=30)
        gbody = g.json() if g.status_code == 200 else {}
        is_mod = gbody.get("is_modified")
        mod_at = gbody.get("modified_at")
        status = gbody.get("status")
        # Validate ISO timestamp
        iso_ok = False
        if isinstance(mod_at, str) and mod_at:
            try:
                datetime.fromisoformat(mod_at.replace("Z", "+00:00"))
                iso_ok = True
            except Exception:
                iso_ok = False
        ok = put_ok and is_mod is True and iso_ok and status == "Ready to Ship"
        record("S3 Status + content edit — TAG SET", ok,
               f"PUT→{r.status_code} · GET status={status} is_modified={is_mod} modified_at={mod_at} "
               f"customer_name={gbody.get('customer_name')!r} · iso_valid={iso_ok}")
    except Exception as e:
        record("S3 Status + content edit — TAG SET", False, f"exception: {e}")

    # ── Scenario 4: status flip after tagged — TAG STAYS ──
    try:
        r = requests.put(f"{API}/shipments/{target_id}", headers=headers_a,
                         json={"status": "Shipped"}, timeout=30)
        put_ok = r.status_code == 200
        g = requests.get(f"{API}/shipments/{target_id}", headers=headers_a, timeout=30)
        gbody = g.json() if g.status_code == 200 else {}
        is_mod = gbody.get("is_modified")
        status = gbody.get("status")
        ok = put_ok and is_mod is True and status == "Shipped"
        record("S4 Status flip after tagged — TAG STAYS", ok,
               f"PUT→{r.status_code} · GET status={status} is_modified={is_mod}")
    except Exception as e:
        record("S4 Status flip after tagged — TAG STAYS", False, f"exception: {e}")

    # ── Scenario 5: Modified virtual filter ──
    try:
        r = requests.get(f"{API}/shipments?status=Modified", headers=headers_a, timeout=30)
        ok_http = r.status_code == 200
        items = r.json() if ok_http else []
        ids = [s["id"] for s in items if isinstance(s, dict)]
        target_in_mod = target_id in ids
        all_modified = ok_http and items and all(s.get("is_modified") is True for s in items)

        # Also fetch ?status=Shipped — must include same id
        r2 = requests.get(f"{API}/shipments?status=Shipped", headers=headers_a, timeout=30)
        ok_http2 = r2.status_code == 200
        items2 = r2.json() if ok_http2 else []
        ids2 = [s["id"] for s in items2 if isinstance(s, dict)]
        target_in_shipped = target_id in ids2
        all_shipped = ok_http2 and all(s.get("status") == "Shipped" for s in items2)

        ok = ok_http and target_in_mod and all_modified and ok_http2 and target_in_shipped and all_shipped
        record("S5 Modified virtual filter", ok,
               f"?status=Modified → HTTP {r.status_code}, count={len(items)}, target_present={target_in_mod}, "
               f"all_is_modified=True={all_modified}  ·  "
               f"?status=Shipped → HTTP {r2.status_code}, count={len(items2)}, target_present={target_in_shipped}, "
               f"all_status=Shipped={all_shipped}")
    except Exception as e:
        record("S5 Modified virtual filter", False, f"exception: {e}")

    # ── Scenario 6: other status filters unaffected ──
    try:
        r = requests.get(f"{API}/shipments?status=Pending", headers=headers_a, timeout=30)
        items_p = r.json() if r.status_code == 200 else []
        all_pending = r.status_code == 200 and all(s.get("status") == "Pending" for s in items_p)

        r2 = requests.get(f"{API}/shipments", headers=headers_a, timeout=30)
        items_all = r2.json() if r2.status_code == 200 else []

        ok = all_pending and r2.status_code == 200 and len(items_all) >= len(items_p)
        record("S6 Other status filters unaffected", ok,
               f"?status=Pending → HTTP {r.status_code}, count={len(items_p)}, all_status=Pending={all_pending}  ·  "
               f"GET /shipments (no status) → HTTP {r2.status_code}, count={len(items_all)}")
    except Exception as e:
        record("S6 Other status filters unaffected", False, f"exception: {e}")

    # ── Scenario 7: reset for cleanliness ──
    try:
        r = requests.put(f"{API}/shipments/{target_id}", headers=headers_a,
                         json={"status": "Pending"}, timeout=30)
        put_ok = r.status_code == 200
        g = requests.get(f"{API}/shipments/{target_id}", headers=headers_a, timeout=30)
        gbody = g.json() if g.status_code == 200 else {}
        is_mod = gbody.get("is_modified")
        status = gbody.get("status")
        ok = put_ok and is_mod is True and status == "Pending"
        record("S7 Reset for cleanliness — tag stays sticky", ok,
               f"PUT→{r.status_code} (status=Pending) · GET status={status} is_modified={is_mod} "
               f"(must be True — sticky audit flag)")
    except Exception as e:
        record("S7 Reset for cleanliness — tag stays sticky", False, f"exception: {e}")

    # ── Scenario 8: Multi-tenant isolation ──
    try:
        token_b, uid_b = login(USER_B)
        headers_b = auth_h(token_b)

        # User B's modified list MUST NOT contain target_id
        r = requests.get(f"{API}/shipments?status=Modified", headers=headers_b, timeout=30)
        ok_http = r.status_code == 200
        items_b = r.json() if ok_http else []
        ids_b = [s.get("id") for s in items_b if isinstance(s, dict)]
        leakage = target_id in ids_b

        # User B PUT on target_id → MUST NOT be 200
        r2 = requests.put(f"{API}/shipments/{target_id}", headers=headers_b,
                          json={"customer_name": "Cross-tenant Hack"}, timeout=30)
        put_blocked = r2.status_code in (403, 404)

        ok = ok_http and (not leakage) and put_blocked
        record("S8 Multi-tenant isolation", ok,
               f"User B id={uid_b}  ·  ?status=Modified → HTTP {r.status_code}, count={len(items_b)}, "
               f"contains_A_target={leakage}  ·  cross-tenant PUT → HTTP {r2.status_code} "
               f"(must be 403/404, got blocked={put_blocked})")
    except requests.HTTPError as e:
        record("S8 Multi-tenant isolation", False,
               f"User B login failed: {e}. SKIP if user2 not provisioned.")
    except Exception as e:
        record("S8 Multi-tenant isolation", False, f"exception: {e}")

    return finalize(results)


def finalize(results):
    print("\n" + "=" * 70)
    print("PHASE-19 BACKEND TEST SUMMARY")
    print("=" * 70)
    passed = sum(1 for r in results if r["ok"])
    failed = len(results) - passed
    for r in results:
        flag = "✅" if r["ok"] else "❌"
        print(f"{flag} {r['name']}")
    print(f"\nTOTAL: {passed}/{len(results)} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
