"""
Phase-4b Smart Paste Router Refactor — Backend Regression Test
Tests 7 endpoints relocated from server.py to routers/smart_paste.py:
  GET  /smart-paste/default-prompt
  POST /smart-paste/parse
  POST /smart-paste/check-duplicate
  GET  /orders/master-id-counter
  POST /orders/master-id-counter
  GET  /orders/peek-master-id
  POST /sheets/sync-from-master

Plus smoke regression on previously-extracted routers.
"""
import json
import sys
import requests

BASE_URL = "https://logistics-hub-740.preview.emergentagent.com/api"
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


def login():
    r = requests.post(
        f"{BASE_URL}/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=30,
    )
    assert r.status_code == 200, f"login failed {r.status_code} {r.text}"
    return r.json()["token"]


def main():
    token = login()
    H = {"Authorization": f"Bearer {token}"}
    print(f"Authenticated as {ADMIN_EMAIL}\n")

    # === 1. GET /smart-paste/default-prompt ===
    print("--- TEST 1: GET /smart-paste/default-prompt ---")
    try:
        r = requests.get(f"{BASE_URL}/smart-paste/default-prompt",
                         headers=H, timeout=30)
        log(r.status_code == 200,
            "T1 status 200", f"got {r.status_code}: {r.text[:200]}")
        if r.status_code == 200:
            j = r.json()
            log("default_prompt" in j, "T1 has default_prompt key",
                f"keys={list(j.keys())}")
            log("user_instructions" in j, "T1 has user_instructions key")
            log("ai_enabled" in j, "T1 has ai_enabled key")
            log(isinstance(j.get("default_prompt"), str)
                and "\n" in j.get("default_prompt", ""),
                "T1 default_prompt is multi-line string",
                f"len={len(j.get('default_prompt', ''))}")
            log(isinstance(j.get("ai_enabled"), bool),
                "T1 ai_enabled is bool",
                f"type={type(j.get('ai_enabled')).__name__}")
    except Exception as e:
        log(False, "T1 exception", str(e))

    # === 2. POST /smart-paste/parse (with AI) ===
    print("\n--- TEST 2: POST /smart-paste/parse (use_ai default) ---")
    paste_text = "Test User\n9876543210\n12 Park Lane, Mumbai 400001\n₹500 COD"
    try:
        r = requests.post(f"{BASE_URL}/smart-paste/parse",
                          headers=H,
                          json={"text": paste_text},
                          timeout=60)
        log(r.status_code == 200,
            "T2 status 200", f"got {r.status_code}: {r.text[:300]}")
        if r.status_code == 200:
            j = r.json()
            log("fields" in j and isinstance(j["fields"], dict),
                "T2 has fields dict",
                f"fields keys={list(j.get('fields', {}).keys())[:8]}")
            f = j.get("fields", {})
            log(bool(f.get("customer_name")),
                "T2 fields.customer_name parsed",
                f"got={f.get('customer_name')!r}")
            log(bool(f.get("customer_phone")),
                "T2 fields.customer_phone parsed",
                f"got={f.get('customer_phone')!r}")
            log(bool(f.get("address_line1") or f.get("address")
                     or f.get("city") or f.get("pincode")),
                "T2 some address-ish field parsed",
                f"addr1={f.get('address_line1')!r}, "
                f"city={f.get('city')!r}, pin={f.get('pincode')!r}")
            log("ai" in j and isinstance(j["ai"], dict),
                "T2 has ai block",
                f"ai={j.get('ai')}")
    except Exception as e:
        log(False, "T2 exception", str(e))

    # === 3. POST /smart-paste/parse with use_ai: false ===
    print("\n--- TEST 3: POST /smart-paste/parse (use_ai=false) ---")
    try:
        r = requests.post(f"{BASE_URL}/smart-paste/parse",
                          headers=H,
                          json={"text": paste_text, "use_ai": False},
                          timeout=30)
        log(r.status_code == 200,
            "T3 status 200", f"got {r.status_code}: {r.text[:200]}")
        if r.status_code == 200:
            j = r.json()
            ai = j.get("ai", {})
            log(ai.get("used") is False,
                "T3 ai.used is False (regex-only path)",
                f"ai.used={ai.get('used')}, ai.source={ai.get('source')}")
            log(ai.get("source") == "regex",
                "T3 ai.source is 'regex'", f"ai={ai}")
    except Exception as e:
        log(False, "T3 exception", str(e))

    # === 4. POST /smart-paste/check-duplicate ===
    print("\n--- TEST 4: POST /smart-paste/check-duplicate ---")
    dup_text = "Test User\n9999999999\n12 Park Lane, Mumbai 400001"
    try:
        r = requests.post(f"{BASE_URL}/smart-paste/check-duplicate",
                          headers=H,
                          json={"text": dup_text},
                          timeout=60)
        log(r.status_code == 200,
            "T4 status 200", f"got {r.status_code}: {r.text[:300]}")
        if r.status_code == 200:
            j = r.json()
            log("fields" in j and isinstance(j["fields"], dict),
                "T4 has fields dict")
            log("confidence" in j,
                "T4 has confidence key",
                f"confidence={j.get('confidence')}")
            log("warnings" in j and isinstance(j["warnings"], list),
                "T4 has warnings list")
            log("duplicates" in j and isinstance(j["duplicates"], list),
                "T4 has duplicates array",
                f"dup count={len(j.get('duplicates', []))}")
            log("ai" in j and isinstance(j["ai"], dict),
                "T4 has ai block",
                f"ai={j.get('ai')}")
    except Exception as e:
        log(False, "T4 exception", str(e))

    # === 5. GET /orders/master-id-counter ===
    print("\n--- TEST 5: GET /orders/master-id-counter ---")
    current_seq = None
    next_master_id = None
    try:
        r = requests.get(f"{BASE_URL}/orders/master-id-counter",
                         headers=H, timeout=30)
        log(r.status_code == 200,
            "T5 status 200", f"got {r.status_code}: {r.text[:200]}")
        if r.status_code == 200:
            j = r.json()
            log("current_seq" in j and isinstance(j["current_seq"], int),
                "T5 current_seq is int",
                f"current_seq={j.get('current_seq')}")
            log("next_seq" in j and isinstance(j["next_seq"], int),
                "T5 next_seq is int",
                f"next_seq={j.get('next_seq')}")
            log("next_master_order_id" in j
                and isinstance(j["next_master_order_id"], str),
                "T5 next_master_order_id is string",
                f"next_master_order_id={j.get('next_master_order_id')}")
            nmoid = j.get("next_master_order_id", "")
            log(len(nmoid) >= 5 and nmoid[-5:].isdigit(),
                "T5 next_master_order_id ends with 5 digits",
                f"got={nmoid}")
            current_seq = j.get("current_seq")
            next_master_id = nmoid
    except Exception as e:
        log(False, "T5 exception", str(e))

    # === 6. GET /orders/peek-master-id ===
    print("\n--- TEST 6: GET /orders/peek-master-id ---")
    try:
        r = requests.get(f"{BASE_URL}/orders/peek-master-id",
                         headers=H, timeout=30)
        log(r.status_code == 200,
            "T6 status 200", f"got {r.status_code}: {r.text[:200]}")
        if r.status_code == 200:
            j = r.json()
            log("master_order_id" in j,
                "T6 has master_order_id key",
                f"master_order_id={j.get('master_order_id')!r}")
            log("auto_generate" in j and isinstance(j["auto_generate"], bool),
                "T6 auto_generate is bool",
                f"auto_generate={j.get('auto_generate')}")
            log("autofill_in_new_shipment" in j
                and isinstance(j["autofill_in_new_shipment"], bool),
                "T6 autofill_in_new_shipment is bool",
                f"autofill_in_new_shipment={j.get('autofill_in_new_shipment')}")
    except Exception as e:
        log(False, "T6 exception", str(e))

    # === 7. POST /orders/master-id-counter (safe no-op) ===
    print("\n--- TEST 7: POST /orders/master-id-counter (safe no-op) ---")
    if current_seq is None:
        log(False, "T7 skipped — no current_seq from T5")
    else:
        # Use current_seq value to be a no-op equivalent (no force needed).
        try:
            r = requests.post(f"{BASE_URL}/orders/master-id-counter",
                              headers=H,
                              json={"seq": current_seq, "force": False},
                              timeout=30)
            log(r.status_code == 200,
                f"T7 POST seq={current_seq} (no-op) status 200",
                f"got {r.status_code}: {r.text[:200]}")
            if r.status_code == 200:
                j = r.json()
                log(j.get("current_seq") == current_seq,
                    "T7 response current_seq unchanged",
                    f"got={j.get('current_seq')}, expected={current_seq}")
                log(j.get("next_seq") == current_seq + 1,
                    "T7 response next_seq is current_seq+1",
                    f"got={j.get('next_seq')}")
                log(isinstance(j.get("next_master_order_id"), str)
                    and len(j.get("next_master_order_id", "")) >= 5,
                    "T7 response has next_master_order_id",
                    f"got={j.get('next_master_order_id')}")
        except Exception as e:
            log(False, "T7 exception", str(e))

        # Bonus: verify the lowering-blocked guard (409) without force
        try:
            r2 = requests.post(f"{BASE_URL}/orders/master-id-counter",
                               headers=H,
                               json={"seq": max(current_seq - 1, 0),
                                     "force": False},
                               timeout=30)
            # If current_seq == 0, this won't be lower; expect 200 instead
            if current_seq > 0:
                log(r2.status_code == 409,
                    "T7b lowering without force returns 409",
                    f"got {r2.status_code}: {r2.text[:200]}")
            else:
                log(True, "T7b skipped — current_seq is 0, can't lower")
        except Exception as e:
            log(False, "T7b exception", str(e))

        # Confirm counter unchanged after the 409 attempt
        try:
            r3 = requests.get(f"{BASE_URL}/orders/master-id-counter",
                              headers=H, timeout=30)
            if r3.status_code == 200:
                log(r3.json().get("current_seq") == current_seq,
                    "T7c counter still at original current_seq",
                    f"got={r3.json().get('current_seq')}, "
                    f"expected={current_seq}")
        except Exception as e:
            log(False, "T7c exception", str(e))

    # === 8. POST /sheets/sync-from-master ===
    print("\n--- TEST 8: POST /sheets/sync-from-master ---")
    try:
        r = requests.post(f"{BASE_URL}/sheets/sync-from-master",
                          headers=H,
                          json={"overwrite": True},
                          timeout=60)
        # Either 422 (no sheet linked) OR 200/502 if linked
        ok = r.status_code in (200, 422, 502)
        log(ok, "T8 status acceptable (200/422/502)",
            f"got {r.status_code}: {r.text[:300]}")
        if r.status_code == 422:
            try:
                detail = r.json().get("detail", "")
            except Exception:
                detail = r.text
            log("Link your Google Sheet" in detail,
                "T8 422 message mentions 'Link your Google Sheet'",
                f"detail={detail!r}")
        elif r.status_code == 200:
            j = r.json()
            log(isinstance(j, dict),
                "T8 200 response is dict",
                f"keys={list(j.keys()) if isinstance(j, dict) else 'na'}")
        elif r.status_code == 502:
            log(True, "T8 502 acceptable (sheet config error)",
                f"detail={r.text[:200]}")
    except Exception as e:
        log(False, "T8 exception", str(e))

    # === SMOKE REGRESSION ===
    print("\n--- SMOKE REGRESSION on previously-extracted routers ---")
    smoke_endpoints = [
        ("/wallet", "wallet"),
        ("/plans", "plans"),
        ("/admin/coupons", "admin coupons"),
        ("/couriers", "couriers"),
        ("/me/feature-flags", "me/feature-flags"),
        ("/me/custom-fields", "me/custom-fields"),
    ]
    for path, label in smoke_endpoints:
        try:
            r = requests.get(f"{BASE_URL}{path}", headers=H, timeout=30)
            log(r.status_code == 200,
                f"SMOKE GET {path} ({label}) status 200",
                f"got {r.status_code}: {r.text[:150]}")
        except Exception as e:
            log(False, f"SMOKE GET {path} exception", str(e))

    # === SUMMARY ===
    print("\n" + "=" * 70)
    passed = sum(1 for ok, _, _ in results if ok)
    failed = sum(1 for ok, _, _ in results if not ok)
    print(f"TOTAL: {passed}/{len(results)} PASS, {failed} FAIL")
    if failed:
        print("\nFAILED:")
        for ok, name, detail in results:
            if not ok:
                print(f"  [FAIL] {name} — {detail}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
