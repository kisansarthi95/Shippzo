"""
Phase-28 Dynamic WhatsApp Provider — Step 7 re-test (CustomField bug fix verification).

Bug fix: CustomField.name min_length=1 constraint removed so the router's
"drop blank-name entries" filter at line ~708 can run as designed.

Also re-verifies sanity Steps 1, 2, 4, 10.
"""
import json
import sys
import requests

BASE = "https://logistics-hub-740.preview.emergentagent.com/api"
ADMIN_EMAIL = "admin@test.com"
ADMIN_PASSWORD = "Admin@12345"
USER_EMAIL = "user2@test.com"
USER_PASSWORD = "User@12345"


def login(email: str, password: str) -> str:
    r = requests.post(
        f"{BASE}/auth/login",
        json={"email": email, "password": password},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["token"]


def H(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def main() -> int:
    fail = 0
    print("=" * 70)
    print("Phase-28 WhatsApp Provider — Step 7 re-test + sanity")
    print("=" * 70)

    admin_tok = login(ADMIN_EMAIL, ADMIN_PASSWORD)
    user_tok  = login(USER_EMAIL,  USER_PASSWORD)
    print(f"[auth] admin login OK; user2 login OK")

    # ── Step 1: GET /config (admin) ───────────────────────────────
    print("\n--- Step 1 — GET /config (admin) ---")
    r = requests.get(f"{BASE}/admin/whatsapp-provider/config", headers=H(admin_tok), timeout=15)
    if r.status_code == 200 and "config" in r.json():
        print(f"  ✅ 200 OK; provider={r.json()['config'].get('provider')!r}")
    else:
        print(f"  ❌ status={r.status_code} body={r.text[:200]}")
        fail += 1

    # ── Step 2: GET /config (non-admin → 403) ─────────────────────
    print("\n--- Step 2 — GET /config (user2 → 403) ---")
    r = requests.get(f"{BASE}/admin/whatsapp-provider/config", headers=H(user_tok), timeout=15)
    if r.status_code == 403:
        print(f"  ✅ 403 Forbidden as expected")
    else:
        print(f"  ❌ status={r.status_code} (expected 403) body={r.text[:200]}")
        fail += 1

    # ── Step 4: GET /events returns 8 items ──────────────────────
    print("\n--- Step 4 — GET /events (admin) → 8 items ---")
    r = requests.get(f"{BASE}/admin/whatsapp-provider/events", headers=H(admin_tok), timeout=15)
    if r.status_code == 200:
        items = r.json().get("items") or []
        keys = [i["event_key"] for i in items]
        if len(items) == 8:
            print(f"  ✅ 200 OK; count=8; keys={keys}")
        else:
            print(f"  ❌ count={len(items)} (expected 8); keys={keys}")
            fail += 1
    else:
        print(f"  ❌ status={r.status_code} body={r.text[:200]}")
        fail += 1

    # ── Step 7 — PUT /events/stage_shipped (THE BUG FIX) ─────────
    print("\n--- Step 7 — PUT /events/stage_shipped (bug-fix verification) ---")
    body = {
        "automation_id": "test-auto-12345",
        "selected_fields": [
            "customer_name", "customer_phone", "order_id",
            "tracking_id", "customer_name",
        ],
        "custom_fields": [
            {"name": "template_lang", "value": "gu"},
            {"name": "", "value": "should_be_dropped"},
        ],
        "variable_mapping": {"customer_name": "name"},
        "enabled": True,
    }
    r = requests.put(
        f"{BASE}/admin/whatsapp-provider/events/stage_shipped",
        headers=H(admin_tok),
        data=json.dumps(body),
        timeout=15,
    )
    print(f"  HTTP {r.status_code}")
    if r.status_code != 200:
        print(f"  ❌ Expected 200 (not 422). Body: {r.text[:400]}")
        fail += 1
    else:
        item = r.json().get("item") or {}
        print(f"  response.item:\n{json.dumps(item, indent=2)[:1200]}")

        # automation_id check
        if item.get("automation_id") == "test-auto-12345":
            print("  ✅ automation_id == 'test-auto-12345'")
        else:
            print(f"  ❌ automation_id={item.get('automation_id')!r}")
            fail += 1

        # selected_fields dedup -> length 4
        sf = item.get("selected_fields") or []
        if len(sf) == 4 and sf == ["customer_name", "customer_phone", "order_id", "tracking_id"]:
            print(f"  ✅ selected_fields length=4, order preserved, dup removed → {sf}")
        else:
            print(f"  ❌ selected_fields={sf} (expected 4 items, no dup)")
            fail += 1

        # custom_fields → only 1 entry (blank-name dropped)
        cf = item.get("custom_fields") or []
        if (len(cf) == 1
                and isinstance(cf[0], dict)
                and cf[0].get("name") == "template_lang"
                and cf[0].get("value") == "gu"):
            print(f"  ✅ custom_fields length=1; blank-name entry dropped → {cf}")
        else:
            print(f"  ❌ custom_fields={cf} (expected [{{name:template_lang,value:gu}}])")
            fail += 1

        # variable_mapping
        vm = item.get("variable_mapping") or {}
        if vm.get("customer_name") == "name":
            print(f"  ✅ variable_mapping has customer_name→name")
        else:
            print(f"  ❌ variable_mapping={vm}")
            fail += 1

    # ── Step 10 — POST /test (sanity) ─────────────────────────────
    print("\n--- Step 10 — POST /test (sanity, stage_shipped) ---")
    test_body = {
        "event_key": "stage_shipped",
        "phone": "9999999999",
        "sample_context": {
            "customer_name": "Test Customer",
            "order_id": "TEST-001",
            "tracking_id": "TRACK-12345",
            "courier_name": "Delhivery",
        },
    }
    r = requests.post(
        f"{BASE}/admin/whatsapp-provider/test",
        headers=H(admin_tok),
        data=json.dumps(test_body),
        timeout=20,
    )
    if r.status_code == 200:
        j = r.json()
        res = j.get("result") or {}
        # Endpoint contract: returns 200 with {ok, result:{...}} regardless
        # of provider success — the call must not raise. ok is informational.
        print(f"  ✅ 200 OK; ok={j.get('ok')} result.skipped={res.get('skipped')} reason={res.get('reason')!r} status_code={res.get('status_code')}")
    else:
        print(f"  ❌ status={r.status_code} body={r.text[:300]}")
        fail += 1

    print("\n" + "=" * 70)
    if fail == 0:
        print("✅ ALL ASSERTIONS PASSED")
    else:
        print(f"❌ {fail} assertion(s) failed")
    print("=" * 70)
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
