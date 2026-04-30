"""
Phase-8 Per-Field "Required" Toggles — Backend Regression
Targeted scope (per review request):
  1. GET /api/settings → field_requirements default presence+values
  2. PUT /api/settings field_requirements partial merge (single-key flip)
  3. PUT /api/settings unknown key in field_requirements is silently dropped
  4. POST/GET/PUT /api/me/custom-fields `required` field plumbing
  5. Backwards compat: PUT /settings without field_requirements does NOT erase it
"""
import os
import sys
import json
import requests

BASE = "https://logistics-hub-740.preview.emergentagent.com/api"
TIMEOUT = 30

ADMIN_EMAIL = "admin@test.com"
ADMIN_PWD = "Admin@12345"
USER2_EMAIL = "user2@test.com"
USER2_PWD = "User@12345"

DEFAULTS = {
    "customer_name": True,
    "customer_phone": True,
    "customer_alt_phone": False,
    "address_line1": True,
    "city": True,
    "state": True,
    "pincode": True,
    "items": False,
    "amount": True,
    "payment_mode": True,
    "token_amount": False,
    "courier_name": False,
    "order_id": False,
    "weight": True,
    "notes": False,
}


pass_count = 0
fail_count = 0
fail_lines = []


def check(label, cond, detail=""):
    global pass_count, fail_count
    if cond:
        pass_count += 1
        print(f"  PASS  {label}")
    else:
        fail_count += 1
        line = f"  FAIL  {label}{(' — ' + detail) if detail else ''}"
        print(line)
        fail_lines.append(line)


def login(email, pwd):
    r = requests.post(f"{BASE}/auth/login", json={"email": email, "password": pwd}, timeout=TIMEOUT)
    r.raise_for_status()
    j = r.json()
    return j["token"], j


def auth_headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def get_settings(token):
    r = requests.get(f"{BASE}/settings", headers=auth_headers(token), timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def put_settings(token, body):
    r = requests.put(
        f"{BASE}/settings",
        headers=auth_headers(token),
        data=json.dumps(body),
        timeout=TIMEOUT,
    )
    return r


def main():
    print("=" * 72)
    print("Phase-8 Field Requirements Regression")
    print("=" * 72)

    print("\n--- Logging in as user2 (fresh user) ---")
    u2_tok, u2_user = login(USER2_EMAIL, USER2_PWD)
    print(f"  user2 id={u2_user.get('id')} plan={u2_user.get('plan')} admin={u2_user.get('is_admin')}")

    print("\n--- Logging in as admin (for additional coverage) ---")
    ad_tok, ad_user = login(ADMIN_EMAIL, ADMIN_PWD)
    print(f"  admin id={ad_user.get('id')} plan={ad_user.get('plan')} admin={ad_user.get('is_admin')}")

    # Pre-test cleanup: ensure user2's field_requirements is at defaults so
    # scenario [1] reflects the documented baseline. The merge-not-replace
    # logic means prior test runs may have toggled a flag — reset them all.
    put_settings(u2_tok, {"field_requirements": dict(DEFAULTS)})

    # ──────────────────────────────────────────────────────────────
    # SCENARIO 1: Default field_requirements present on GET /settings
    # ──────────────────────────────────────────────────────────────
    print("\n[1] GET /api/settings → default field_requirements")
    s = get_settings(u2_tok)
    fr = s.get("field_requirements")
    check("response contains 'field_requirements' key", isinstance(fr, dict),
          detail=f"got type={type(fr).__name__} value={fr!r}")

    if isinstance(fr, dict):
        for k, v in DEFAULTS.items():
            check(f"key {k!r} present", k in fr)
            if k in fr:
                check(f"key {k!r} default value == {v}", fr.get(k) == v,
                      detail=f"got {fr.get(k)!r}")
        extras = [k for k in fr.keys() if k not in DEFAULTS]
        check(f"no unknown keys present (extras={extras})", len(extras) == 0)

    # ──────────────────────────────────────────────────────────────
    # SCENARIO 2: Partial PUT merges, doesn't replace
    # ──────────────────────────────────────────────────────────────
    print("\n[2] PUT /api/settings {field_requirements:{customer_alt_phone:true}} merges")
    r = put_settings(u2_tok, {"field_requirements": {"customer_alt_phone": True}})
    check("PUT 200", r.status_code == 200, detail=f"status={r.status_code} body={r.text[:200]}")
    if r.status_code == 200:
        fr2 = r.json().get("field_requirements") or {}
        check("customer_alt_phone == True after toggle ON",
              fr2.get("customer_alt_phone") is True,
              detail=f"got {fr2.get('customer_alt_phone')!r}")
        for k, v in DEFAULTS.items():
            if k == "customer_alt_phone":
                continue
            check(f"  preserved {k!r} == {v}", fr2.get(k) == v,
                  detail=f"got {fr2.get(k)!r}")
        extras = [k for k in fr2.keys() if k not in DEFAULTS]
        check(f"  no extra keys (extras={extras})", len(extras) == 0)

    print("\n[2b] PUT /api/settings {field_requirements:{customer_alt_phone:false}} flips back")
    r = put_settings(u2_tok, {"field_requirements": {"customer_alt_phone": False}})
    check("PUT 200", r.status_code == 200, detail=f"status={r.status_code}")
    if r.status_code == 200:
        fr3 = r.json().get("field_requirements") or {}
        check("customer_alt_phone == False after toggle OFF",
              fr3.get("customer_alt_phone") is False,
              detail=f"got {fr3.get('customer_alt_phone')!r}")
        for k, v in DEFAULTS.items():
            if k == "customer_alt_phone":
                continue
            check(f"  preserved {k!r} == {v}", fr3.get(k) == v,
                  detail=f"got {fr3.get(k)!r}")

    # ──────────────────────────────────────────────────────────────
    # SCENARIO 3: Unknown keys silently dropped
    # ──────────────────────────────────────────────────────────────
    print("\n[3] PUT /api/settings with unknown key {foo_bar:true} is silently dropped")
    r = put_settings(u2_tok, {"field_requirements": {"foo_bar": True, "weight": False}})
    check("PUT 200 (unknown key tolerated)", r.status_code == 200,
          detail=f"status={r.status_code} body={r.text[:200]}")
    if r.status_code == 200:
        fr4 = r.json().get("field_requirements") or {}
        check("'foo_bar' NOT in response.field_requirements",
              "foo_bar" not in fr4,
              detail=f"got keys={sorted(fr4.keys())}")
        check("known key 'weight' was applied (False)",
              fr4.get("weight") is False,
              detail=f"got {fr4.get('weight')!r}")
        rr = put_settings(u2_tok, {"field_requirements": {"weight": True}})
        check("restore weight=True OK", rr.status_code == 200)

    # ──────────────────────────────────────────────────────────────
    # SCENARIO 5: Backwards compat — PUT without field_requirements
    #            preserves the persisted dict.
    # ──────────────────────────────────────────────────────────────
    print("\n[5] Backwards compat: PUT without field_requirements preserves dict")
    rseed = put_settings(u2_tok, {"field_requirements": {"customer_alt_phone": True, "notes": True}})
    check("seed PUT 200", rseed.status_code == 200)
    fr_pre = rseed.json().get("field_requirements") or {}
    r = put_settings(u2_tok, {"shipment_tagline": "regression-tag-phase8"})
    check("unrelated PUT 200", r.status_code == 200, detail=f"status={r.status_code} body={r.text[:200]}")
    if r.status_code == 200:
        fr_post = r.json().get("field_requirements") or {}
        check("field_requirements unchanged after unrelated PUT",
              fr_post == fr_pre,
              detail=f"pre={fr_pre} post={fr_post}")

    print("\n  cleanup: reset toggled flags to defaults")
    put_settings(u2_tok, {"field_requirements": dict(DEFAULTS)})

    # ──────────────────────────────────────────────────────────────
    # SCENARIO 4: Custom field `required` plumbing
    # ──────────────────────────────────────────────────────────────
    print("\n[4] POST /api/me/custom-fields with required:true persists & toggles")

    created_field_ids = []
    worker_tok = u2_tok

    listr = requests.get(f"{BASE}/me/custom-fields", headers=auth_headers(u2_tok), timeout=TIMEOUT)
    used_cols = set()
    if listr.status_code == 200:
        for f in (listr.json() or {}).get("fields", []):
            if f.get("active", True):
                used_cols.add((f.get("column_letter") or "").upper())
        print(f"  user2 existing custom-field columns: {sorted(used_cols)}")

    # We'll prefer columns that are unlikely to collide with shipment columns A..S (1..19)
    candidate_cols = [c for c in ["T", "U", "V", "W", "X", "Y", "Z"] if c not in used_cols]
    if len(candidate_cols) < 2:
        # Fallback: try double-letters
        candidate_cols.extend([c for c in ["AA", "AB", "AC", "AD", "AE"] if c not in used_cols])
    col_a = candidate_cols[0]
    col_b = candidate_cols[1]
    print(f"  using columns col_a={col_a} col_b={col_b}")

    payload_a = {
        "name": "GST Number (Phase8 test)",
        "column_letter": col_a,
        "field_type": "text",
        "show_in_form": True,
        "show_in_smart_paste": True,
        "required": True,
        "sort_order": 99,
    }
    rc = requests.post(
        f"{BASE}/me/custom-fields",
        headers=auth_headers(u2_tok),
        data=json.dumps(payload_a),
        timeout=TIMEOUT,
    )
    if rc.status_code == 403 and "plan" in rc.text.lower():
        print(f"  user2 plan rejected custom-fields: {rc.text[:200]}")
        print("  Retrying with admin token (admin bypass).")
        rc = requests.post(
            f"{BASE}/me/custom-fields",
            headers=auth_headers(ad_tok),
            data=json.dumps(payload_a),
            timeout=TIMEOUT,
        )
        worker_tok = ad_tok

    check(
        f"POST /me/custom-fields (required=true) → 200, got {rc.status_code}",
        rc.status_code == 200,
        detail=f"body={rc.text[:400]}",
    )
    if rc.status_code == 200:
        body = rc.json()
        fid_a = body.get("id")
        if fid_a:
            created_field_ids.append(fid_a)
        check("(4a) response.required is True", body.get("required") is True,
              detail=f"got {body.get('required')!r} full={body}")

        # 4b. GET list reflects required=true
        rg = requests.get(f"{BASE}/me/custom-fields", headers=auth_headers(worker_tok), timeout=TIMEOUT)
        check("GET /me/custom-fields 200", rg.status_code == 200)
        if rg.status_code == 200:
            entry = next((f for f in rg.json().get("fields", []) if f.get("id") == fid_a), None)
            check("(4b) created field appears in GET list", entry is not None)
            if entry:
                check("(4b) GET list entry.required == True", entry.get("required") is True,
                      detail=f"got {entry.get('required')!r}")

        # 4c. PUT toggle required → false
        rp = requests.put(
            f"{BASE}/me/custom-fields/{fid_a}",
            headers=auth_headers(worker_tok),
            data=json.dumps({"required": False}),
            timeout=TIMEOUT,
        )
        check(f"PUT toggle required→false 200, got {rp.status_code}",
              rp.status_code == 200, detail=f"body={rp.text[:200]}")
        if rp.status_code == 200:
            check("(4c) PUT response.required == False",
                  rp.json().get("required") is False,
                  detail=f"got {rp.json().get('required')!r}")

        # 4d. Create without required key — defaults to false
        payload_b = {
            "name": "Optional Note (Phase8 test)",
            "column_letter": col_b,
            "field_type": "text",
            "show_in_form": True,
            "show_in_smart_paste": True,
        }
        rc2 = requests.post(
            f"{BASE}/me/custom-fields",
            headers=auth_headers(worker_tok),
            data=json.dumps(payload_b),
            timeout=TIMEOUT,
        )
        check(
            f"POST /me/custom-fields (required omitted) → 200, got {rc2.status_code}",
            rc2.status_code == 200,
            detail=f"body={rc2.text[:300]}",
        )
        if rc2.status_code == 200:
            b2 = rc2.json()
            fid_b = b2.get("id")
            if fid_b:
                created_field_ids.append(fid_b)
            check("(4d) response.required defaults to False",
                  b2.get("required") is False,
                  detail=f"got {b2.get('required')!r}")
            rg2 = requests.get(f"{BASE}/me/custom-fields", headers=auth_headers(worker_tok), timeout=TIMEOUT)
            if rg2.status_code == 200:
                e2 = next((f for f in rg2.json().get("fields", []) if f.get("id") == fid_b), None)
                if e2:
                    check("(4d) GET list entry.required == False",
                          e2.get("required") is False,
                          detail=f"got {e2.get('required')!r}")

        # cleanup
        for fid in list(created_field_ids):
            try:
                rd = requests.delete(
                    f"{BASE}/me/custom-fields/{fid}",
                    headers=auth_headers(worker_tok),
                    timeout=TIMEOUT,
                )
                if rd.status_code == 200:
                    print(f"  cleanup: deleted custom field {fid}")
                else:
                    print(f"  cleanup: delete {fid} got {rd.status_code} {rd.text[:120]}")
            except Exception as e:
                print(f"  cleanup error: {e}")

    print("\n" + "=" * 72)
    print(f"RESULT: {pass_count} passed, {fail_count} failed")
    print("=" * 72)
    if fail_lines:
        print("\nFailed assertions:")
        for ln in fail_lines:
            print(ln)
        sys.exit(1)


if __name__ == "__main__":
    main()
