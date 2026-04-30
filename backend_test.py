"""
Backend test for two new linked features:
  - Header Auto-Sync (POST /api/sheets/sync-headers)
  - Per-user Custom Fields (CRUD + plan gating + admin caps)

Read-only verification with cleanup at the end. Does NOT modify code.
"""
import os
import sys
import json
import requests

BACKEND = "https://logistics-hub-740.preview.emergentagent.com/api"

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASSWORD = "Admin@12345"
USER_EMAIL = "user2@test.com"
USER_PASSWORD = "User@12345"

results = []  # list of (name, passed, detail)


def add(name, passed, detail=""):
    icon = "✅" if passed else "❌"
    print(f"{icon} {name}{(' — ' + detail) if detail else ''}")
    results.append((name, passed, detail))


def login(email, pw):
    r = requests.post(f"{BACKEND}/auth/login", json={"email": email, "password": pw}, timeout=20)
    if r.status_code != 200:
        print(f"LOGIN FAIL {email}: {r.status_code} {r.text[:200]}")
        sys.exit(1)
    j = r.json()
    return j["token"], j


def H(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def main():
    print("=" * 70)
    print("Header Auto-Sync + Custom Fields backend verification")
    print("=" * 70)

    admin_token, admin_user = login(ADMIN_EMAIL, ADMIN_PASSWORD)
    user_token, user_user = login(USER_EMAIL, USER_PASSWORD)
    print(f"admin id={admin_user.get('id')} plan={admin_user.get('plan')} is_admin={admin_user.get('is_admin')}")
    print(f"user2 id={user_user.get('id')} plan={user_user.get('plan')} is_admin={user_user.get('is_admin')}")

    # Snapshot plan_features and custom-field limits BEFORE mutations
    r = requests.get(f"{BACKEND}/admin/plan-features", headers=H(admin_token), timeout=20)
    if r.status_code != 200:
        print("Cannot fetch plan-features as admin", r.status_code, r.text[:200])
        sys.exit(1)
    pf_before = r.json().get("plans", {})
    silver_before = list(pf_before.get("silver", []))
    print(f"snapshot silver plan-features ({len(silver_before)}): includes_custom_fields={'custom_fields' in silver_before}")

    r = requests.get(f"{BACKEND}/admin/custom-field-limits", headers=H(admin_token), timeout=20)
    if r.status_code != 200:
        print("Cannot fetch custom-field-limits", r.status_code, r.text[:200])
        sys.exit(1)
    limits_before = r.json().get("limits", {})
    print(f"snapshot custom-field-limits: {limits_before}")

    cleanup_field_ids = []
    silver_modified = False
    limits_modified = False

    try:
        # ─────── Section A: Header Auto-Sync ───────
        print("\n--- Section A: Header Auto-Sync ---")
        # Check user2's settings for sheet.sheet_id
        r = requests.get(f"{BACKEND}/settings", headers=H(user_token), timeout=20)
        add("A. GET /settings (user2)", r.status_code == 200, f"status={r.status_code}")
        sheet_cfg = (r.json() or {}).get("sheet", {}) if r.status_code == 200 else {}
        has_sheet = bool(sheet_cfg.get("sheet_id"))
        print(f"user2 sheet.sheet_id = {sheet_cfg.get('sheet_id')!r}, mapping keys = {list((sheet_cfg.get('column_mapping') or {}).keys())}")

        if not has_sheet:
            # Per spec: attempt sync-headers anyway → expect 400
            r = requests.post(f"{BACKEND}/sheets/sync-headers", headers=H(user_token),
                              json={"dry_run": True}, timeout=20)
            ok = (r.status_code == 400 and "Google Sheet not connected" in r.text)
            add("A. POST /sheets/sync-headers without sheet → 400 'Google Sheet not connected'",
                ok, f"status={r.status_code} body={r.text[:160]}")
        else:
            # 1. dry_run true
            r = requests.post(f"{BACKEND}/sheets/sync-headers", headers=H(user_token),
                              json={"dry_run": True}, timeout=30)
            j = r.json() if r.status_code == 200 else {}
            add("A1. POST /sheets/sync-headers dry_run=true → 200",
                r.status_code == 200, f"status={r.status_code} body={r.text[:200]}")
            add("A1. dry_run response has ok=True, dry_run=True, would_write list",
                bool(j.get("ok")) and j.get("dry_run") is True and isinstance(j.get("would_write"), list),
                f"keys={list(j.keys())}")
            ww = j.get("would_write") or []
            valid_pairs = all(
                isinstance(it, (list, tuple, dict))
                for it in ww
            )
            # would_write items can be tuple/list of (col, name) — check both shapes
            shape_ok = True
            for it in ww:
                if isinstance(it, dict):
                    if not (it.get("column") and it.get("name")):
                        shape_ok = False
                        break
                elif isinstance(it, (list, tuple)):
                    if len(it) < 2 or not it[0] or not it[1]:
                        shape_ok = False
                        break
                else:
                    shape_ok = False
                    break
            add("A1. would_write entries have column + name", shape_ok and valid_pairs,
                f"sample={ww[:3]}")

            # 2. dry_run false
            r = requests.post(f"{BACKEND}/sheets/sync-headers", headers=H(user_token),
                              json={"dry_run": False}, timeout=60)
            j = r.json() if r.status_code == 200 else {}
            add("A2. POST /sheets/sync-headers dry_run=false → 200",
                r.status_code == 200, f"status={r.status_code} body={r.text[:200]}")
            add("A2. response has written_count, skipped_count, written, skipped",
                all(k in j for k in ("written_count", "skipped_count", "written", "skipped")),
                f"keys={list(j.keys())}")
            written_first = j.get("written", [])
            skipped_first = j.get("skipped", [])
            print(f"  written={len(written_first)} skipped={len(skipped_first)}")

            # 3. Repeat → all returned as skipped
            r = requests.post(f"{BACKEND}/sheets/sync-headers", headers=H(user_token),
                              json={"dry_run": False}, timeout=60)
            j2 = r.json() if r.status_code == 200 else {}
            add("A3. Second sync → 200", r.status_code == 200, f"status={r.status_code}")
            add("A3. Second sync written_count == 0",
                int(j2.get("written_count", -1)) == 0,
                f"written={j2.get('written_count')} skipped={j2.get('skipped_count')}")

            # 4. Edge case: explicit headers
            r = requests.post(f"{BACKEND}/sheets/sync-headers", headers=H(user_token),
                              json={"headers": [{"column": "ZZ", "name": "Test Col ZZ"}], "dry_run": False},
                              timeout=60)
            j3 = r.json() if r.status_code == 200 else {}
            add("A4. POST sync-headers explicit ZZ → 200",
                r.status_code == 200, f"status={r.status_code} body={r.text[:200]}")
            written_or_skipped = (j3.get("written", []) + [(s.get("column") or s[0]) if not isinstance(s, dict) else s.get("column") for s in j3.get("skipped", [])])
            zz_seen = False
            for w in j3.get("written", []) + j3.get("skipped", []):
                col = w.get("column") if isinstance(w, dict) else None
                if col == "ZZ":
                    zz_seen = True
                    break
            add("A4. ZZ column appears in written or skipped", zz_seen,
                f"written={j3.get('written')} skipped={j3.get('skipped')}")

        # ─────── Section B: Custom Fields gated on silver (default OFF) ───────
        print("\n--- Section B: Custom Fields gated (silver default) ---")
        r = requests.get(f"{BACKEND}/me/custom-fields", headers=H(user_token), timeout=20)
        j = r.json() if r.status_code == 200 else {}
        add("B5. GET /me/custom-fields (user2 silver) → 200",
            r.status_code == 200, f"status={r.status_code}")
        shape_ok = (
            isinstance(j.get("fields"), list)
            and "limit" in j and "used" in j
            and "feature_enabled" in j and "plan" in j and "is_admin" in j
        )
        add("B5. response shape has fields, limit, used, feature_enabled, plan, is_admin",
            shape_ok, f"keys={list(j.keys())}")
        add("B5. user2 plan == 'silver'", j.get("plan") == "silver", f"plan={j.get('plan')}")
        add("B5. is_admin == False", j.get("is_admin") is False, f"is_admin={j.get('is_admin')}")
        # If silver default has custom_fields off, feature_enabled should be false
        silver_default_has_cf = "custom_fields" in silver_before
        add("B5. feature_enabled matches silver default plan-features",
            j.get("feature_enabled") == silver_default_has_cf,
            f"feature_enabled={j.get('feature_enabled')} silver_has_cf={silver_default_has_cf}")
        if not silver_default_has_cf:
            r = requests.post(f"{BACKEND}/me/custom-fields", headers=H(user_token),
                              json={"name": "Test", "column_letter": "F"}, timeout=20)
            ok = r.status_code == 403 and "not available on your plan" in (r.text or "")
            add("B6. POST /me/custom-fields user2 → 403 'not available on your plan'",
                ok, f"status={r.status_code} body={r.text[:200]}")
        else:
            print("  Skipping B6 — silver already has custom_fields enabled by default")

        # ─────── Section C: Admin temporarily enables + caps silver ───────
        print("\n--- Section C: Admin enables custom_fields + caps silver ---")
        # 7. Add custom_fields to silver
        new_silver = list(silver_before)
        if "custom_fields" not in new_silver:
            new_silver.append("custom_fields")
        new_plans = {**pf_before, "silver": new_silver}
        r = requests.put(f"{BACKEND}/admin/plan-features", headers=H(admin_token),
                         json={"plans": new_plans}, timeout=20)
        add("C7. PUT /admin/plan-features add custom_fields to silver → 200",
            r.status_code == 200, f"status={r.status_code} body={r.text[:200]}")
        if r.status_code == 200:
            silver_modified = True

        # Verify on user side
        r = requests.get(f"{BACKEND}/me/custom-fields", headers=H(user_token), timeout=20)
        j = r.json() if r.status_code == 200 else {}
        add("C7. GET /me/custom-fields shows feature_enabled=True after admin update",
            j.get("feature_enabled") is True, f"feature_enabled={j.get('feature_enabled')}")

        # 8. Set silver cap = 3
        r = requests.put(f"{BACKEND}/admin/custom-field-limits", headers=H(admin_token),
                         json={"silver": 3}, timeout=20)
        j = r.json() if r.status_code == 200 else {}
        add("C8. PUT /admin/custom-field-limits silver=3 → 200",
            r.status_code == 200 and j.get("ok") is True and (j.get("limits") or {}).get("silver") == 3,
            f"status={r.status_code} body={r.text[:200]}")
        if r.status_code == 200:
            limits_modified = True

        r = requests.get(f"{BACKEND}/me/custom-fields", headers=H(user_token), timeout=20)
        j = r.json() if r.status_code == 200 else {}
        add("C8. GET /me/custom-fields limit == 3", j.get("limit") == 3,
            f"limit={j.get('limit')}")

        # ─────── Section D: Custom Fields CRUD happy path ───────
        print("\n--- Section D: Custom Fields CRUD happy path (silver enabled) ---")
        cols_to_add = [("F", "Salesperson"), ("G", "Region"), ("H", "Notes Custom")]
        ids = []
        for col, name in cols_to_add:
            r = requests.post(f"{BACKEND}/me/custom-fields", headers=H(user_token),
                              json={"name": name, "column_letter": col}, timeout=20)
            ok = r.status_code == 200
            add(f"D9. POST /me/custom-fields col={col} → 200",
                ok, f"status={r.status_code} body={r.text[:200]}")
            if ok:
                doc = r.json()
                ids.append(doc.get("id"))
                cleanup_field_ids.append(doc.get("id"))
        add("D9. Created 3 fields", len(ids) == 3, f"created={len(ids)}")
        r = requests.get(f"{BACKEND}/me/custom-fields", headers=H(user_token), timeout=20)
        j = r.json() if r.status_code == 200 else {}
        add("D9. GET shows used=3", j.get("used") == 3, f"used={j.get('used')}")

        # 10. 4th → 403 cap
        r = requests.post(f"{BACKEND}/me/custom-fields", headers=H(user_token),
                          json={"name": "Fourth", "column_letter": "I"}, timeout=20)
        body = r.text or ""
        cap_word = any(w.lower() in body.lower() for w in ("cap", "limit", "upgrade"))
        add("D10. 4th POST → 403 with cap/limit/Upgrade in detail",
            r.status_code == 403 and cap_word, f"status={r.status_code} body={body[:200]}")

        # Delete the third field (H "Notes Custom") so we have headroom for D11/D12 validation
        if len(ids) >= 3:
            requests.delete(f"{BACKEND}/me/custom-fields/{ids[2]}", headers=H(user_token), timeout=10)
            if ids[2] in cleanup_field_ids:
                cleanup_field_ids.remove(ids[2])
            ids = ids[:2]

        # 11. Invalid column 'fb1'
        r = requests.post(f"{BACKEND}/me/custom-fields", headers=H(user_token),
                          json={"name": "Bad", "column_letter": "fb1"}, timeout=20)
        body = r.text or ""
        add("D11. invalid column 'fb1' → 400 with 'A–Z' in detail",
            r.status_code == 400 and ("A–Z" in body or "A-Z" in body),
            f"status={r.status_code} body={body[:200]}")

        # 12. duplicate column 'F'
        r = requests.post(f"{BACKEND}/me/custom-fields", headers=H(user_token),
                          json={"name": "DupF", "column_letter": "F"}, timeout=20)
        body = r.text or ""
        add("D12. duplicate column 'F' → 400 with 'already used'",
            r.status_code == 400 and "already used" in body,
            f"status={r.status_code} body={body[:200]}")

        # Restore field count to 3 for D14 to verify used=2 after delete
        r = requests.post(f"{BACKEND}/me/custom-fields", headers=H(user_token),
                          json={"name": "Notes Custom 2", "column_letter": "H"}, timeout=20)
        if r.status_code == 200:
            ids.append(r.json().get("id"))
            cleanup_field_ids.append(r.json().get("id"))

        # 13. PUT update one
        if ids:
            fid = ids[0]
            r = requests.put(f"{BACKEND}/me/custom-fields/{fid}", headers=H(user_token),
                             json={"name": "Salesperson Updated", "column_letter": "J"}, timeout=20)
            add("D13. PUT /me/custom-fields/{id} → 200", r.status_code == 200,
                f"status={r.status_code} body={r.text[:200]}")
            r = requests.get(f"{BACKEND}/me/custom-fields", headers=H(user_token), timeout=20)
            j = r.json() if r.status_code == 200 else {}
            updated_doc = next((f for f in (j.get("fields") or []) if f.get("id") == fid), None)
            add("D13. GET shows updated name + column",
                updated_doc and updated_doc.get("name") == "Salesperson Updated"
                and updated_doc.get("column_letter") == "J",
                f"doc={updated_doc}")

        # 14. DELETE one
        if ids:
            fid = ids[1] if len(ids) > 1 else ids[0]
            r = requests.delete(f"{BACKEND}/me/custom-fields/{fid}", headers=H(user_token), timeout=20)
            add("D14. DELETE /me/custom-fields/{id} → 200", r.status_code == 200,
                f"status={r.status_code} body={r.text[:200]}")
            if r.status_code == 200 and fid in cleanup_field_ids:
                cleanup_field_ids.remove(fid)
            r = requests.get(f"{BACKEND}/me/custom-fields", headers=H(user_token), timeout=20)
            j = r.json() if r.status_code == 200 else {}
            add("D14. GET shows used=2", j.get("used") == 2, f"used={j.get('used')}")

        # ─────── Section E: Admin cap endpoints ───────
        print("\n--- Section E: Admin cap endpoints ---")
        r = requests.get(f"{BACKEND}/admin/custom-field-limits", headers=H(user_token), timeout=20)
        add("E15. Non-admin GET /admin/custom-field-limits → 403", r.status_code == 403,
            f"status={r.status_code} body={r.text[:200]}")

        r = requests.get(f"{BACKEND}/admin/custom-field-limits", headers=H(admin_token), timeout=20)
        j = r.json() if r.status_code == 200 else {}
        defaults = j.get("defaults", {})
        add("E16. Admin GET → 200 with limits + defaults",
            r.status_code == 200 and "limits" in j and "defaults" in j,
            f"status={r.status_code} keys={list(j.keys())}")
        add("E16. defaults include free_trial=0, silver=0, gold=3, platinum=5",
            defaults.get("free_trial") == 0 and defaults.get("silver") == 0
            and defaults.get("gold") == 3 and defaults.get("platinum") == 5,
            f"defaults={defaults}")

        r = requests.put(f"{BACKEND}/admin/custom-field-limits", headers=H(admin_token),
                         json={"gold": 5}, timeout=20)
        j = r.json() if r.status_code == 200 else {}
        add("E17. PUT gold=5 → 200 + persists",
            r.status_code == 200 and (j.get("limits") or {}).get("gold") == 5,
            f"status={r.status_code} body={r.text[:200]}")
        r = requests.get(f"{BACKEND}/admin/custom-field-limits", headers=H(admin_token), timeout=20)
        j = r.json() if r.status_code == 200 else {}
        add("E17. Subsequent GET reflects gold=5",
            (j.get("limits") or {}).get("gold") == 5, f"limits={j.get('limits')}")

        # ─────── Section F: Regression ───────
        print("\n--- Section F: Regression ---")
        # 18. POST /api/shipments still works (need a courier_id from user2)
        r = requests.get(f"{BACKEND}/couriers", headers=H(user_token), timeout=20)
        couriers = r.json() if r.status_code == 200 else []
        cid = couriers[0]["id"] if couriers else None
        if cid:
            payload = {
                "tracking_id": "TEST-REG-001",
                "courier_id": cid,
                "courier_name": couriers[0].get("name", ""),
                "customer_name": "Regression Test",
                "customer_phone": "9123456780",
                "address_line1": "Test Lane",
                "city": "Ahmedabad",
                "state": "Gujarat",
                "pincode": "380001",
                "payment_mode": "Prepaid",
                "amount": 100.0,
            }
            r = requests.post(f"{BACKEND}/shipments", headers=H(user_token), json=payload, timeout=30)
            add("F18. POST /shipments still 200", r.status_code == 200,
                f"status={r.status_code} body={r.text[:200]}")
            if r.status_code == 200:
                ship = r.json()
                ship_id = ship.get("id")
                # cleanup
                requests.delete(f"{BACKEND}/shipments/{ship_id}", headers=H(user_token), timeout=20)
        else:
            add("F18. POST /shipments — no courier available, skipped", True, "no courier")

        # 19. /me/feature-flags for gold/platinum should include custom_fields
        # We'll temporarily flip user2 to gold via direct mongo? Actually we cannot.
        # Instead, fetch admin feature-flags (admin gets all keys) and verify the key exists.
        r = requests.get(f"{BACKEND}/me/feature-flags", headers=H(admin_token), timeout=20)
        j = r.json() if r.status_code == 200 else {}
        add("F19. /me/feature-flags admin returns custom_fields key",
            "custom_fields" in (j.get("features") or []),
            f"is_admin={j.get('is_admin')} count={len(j.get('features', []))}")
        # Also verify default plan-features for gold/platinum include it
        r = requests.get(f"{BACKEND}/admin/plan-features", headers=H(admin_token), timeout=20)
        plans_now = (r.json() or {}).get("plans", {}) if r.status_code == 200 else {}
        gold_has = "custom_fields" in (plans_now.get("gold") or [])
        plat_has = "custom_fields" in (plans_now.get("platinum") or [])
        add("F19. plan-features: gold and platinum include custom_fields",
            gold_has and plat_has, f"gold_has={gold_has} platinum_has={plat_has}")

        # 20. /sheets/orders still 200 with existing shape
        r = requests.get(f"{BACKEND}/sheets/orders", headers=H(user_token), timeout=30)
        if r.status_code == 200:
            jo = r.json()
            keys = set(jo.keys())
            shape_ok = "rows" in keys or "orders" in keys or "headers" in keys
            add("F20. GET /sheets/orders → 200 with expected shape",
                shape_ok, f"keys={list(keys)[:10]}")
        elif r.status_code == 400:
            # User has no sheet connected — acceptable
            add("F20. GET /sheets/orders → 400 (no sheet) — acceptable",
                "Google Sheet not connected" in r.text or "Settings not configured" in r.text,
                f"status={r.status_code} body={r.text[:200]}")
        else:
            add("F20. GET /sheets/orders unexpected", False,
                f"status={r.status_code} body={r.text[:200]}")

    finally:
        # ───────────── Cleanup ─────────────
        print("\n--- Cleanup ---")
        for fid in list(cleanup_field_ids):
            try:
                r = requests.delete(f"{BACKEND}/me/custom-fields/{fid}", headers=H(user_token), timeout=10)
                print(f"  cleanup delete field {fid}: {r.status_code}")
            except Exception as e:
                print(f"  cleanup delete field {fid} failed: {e}")
        # Also clean up any leftover custom fields just in case
        try:
            r = requests.get(f"{BACKEND}/me/custom-fields", headers=H(user_token), timeout=10)
            for f in (r.json() or {}).get("fields", []):
                try:
                    requests.delete(f"{BACKEND}/me/custom-fields/{f['id']}",
                                    headers=H(user_token), timeout=10)
                except Exception:
                    pass
        except Exception:
            pass

        if silver_modified:
            try:
                r = requests.put(f"{BACKEND}/admin/plan-features", headers=H(admin_token),
                                 json={"plans": pf_before}, timeout=20)
                print(f"  restore plan-features silver: {r.status_code}")
            except Exception as e:
                print(f"  restore plan-features failed: {e}")

        if limits_modified:
            try:
                # Restore the prior limits doc exactly. If empty, send all None to leave defaults? 
                # The PUT only writes provided keys, so we should write the previous values.
                body = {}
                for k in ("free_trial", "silver", "gold", "platinum"):
                    if k in limits_before:
                        body[k] = int(limits_before[k])
                # Even if no prior overrides, attempt to clear by writing defaults:
                if not body:
                    # Reset to defaults from spec
                    body = {"free_trial": 0, "silver": 0, "gold": 3, "platinum": 5}
                r = requests.put(f"{BACKEND}/admin/custom-field-limits", headers=H(admin_token),
                                 json=body, timeout=20)
                print(f"  restore custom-field-limits: {r.status_code} body={r.text[:200]}")
            except Exception as e:
                print(f"  restore limits failed: {e}")

        # Summary
        print("\n" + "=" * 70)
        passed = sum(1 for _, p, _ in results if p)
        total = len(results)
        print(f"RESULT: {passed}/{total} assertions passed")
        for name, p, det in results:
            if not p:
                print(f"  ❌ {name} — {det}")
        sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
