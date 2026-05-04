"""
Phase 2 final verification — feature registry expansion + variant rotation.

Tests:
1. GET /api/admin/plan-features as admin → 200, new categories+keys present.
2. GET /api/admin/plan-features as user2 → 403.
3. PUT /api/admin/plan-features adds whatsapp_ai_variants to silver.
4. CRITICAL — Variant rotation: save 3 variants and call resolve-template 4 times,
   confirm round-robin advancing each call.
5. GET /api/me/feature-flags as user2.
"""
from __future__ import annotations
import os
import sys
import json
import requests

BASE_URL = os.environ.get(
    "BACKEND_URL", "https://logistics-hub-740.preview.emergentagent.com"
).rstrip("/")
API = f"{BASE_URL}/api"

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASS  = "Admin@12345"
USER2_EMAIL = "user2@test.com"
USER2_PASS  = "User@12345"

results = []

def assert_eq(label, got, want):
    ok = (got == want)
    results.append((ok, label, f"got={got!r} want={want!r}" if not ok else ""))
    if not ok:
        print(f"  ❌ {label}: got={got!r} want={want!r}")
    return ok

def assert_true(label, cond, info=""):
    ok = bool(cond)
    results.append((ok, label, info if not ok else ""))
    if not ok:
        print(f"  ❌ {label}: {info}")
    return ok

def login(email, password):
    r = requests.post(f"{API}/auth/login",
                      json={"email": email, "password": password},
                      timeout=20)
    r.raise_for_status()
    j = r.json()
    return j["token"], j

def H(token):
    return {"Authorization": f"Bearer {token}"}

def main():
    # ───────────────────────── Login ─────────────────────────
    print("\n— LOGIN —")
    admin_token, admin_user = login(ADMIN_EMAIL, ADMIN_PASS)
    user2_token, user2_user = login(USER2_EMAIL, USER2_PASS)
    assert_true("admin token present", bool(admin_token))
    assert_true("user2 token present", bool(user2_token))
    assert_eq("admin is_admin=true", admin_user.get("is_admin"), True)
    assert_eq("user2 is_admin=false", user2_user.get("is_admin", False), False)

    # ─────────────────── 1. GET plan-features (admin) ───────────────────
    print("\n— TEST 1: GET /api/admin/plan-features as admin —")
    r = requests.get(f"{API}/admin/plan-features", headers=H(admin_token), timeout=20)
    assert_eq("admin /plan-features status", r.status_code, 200)
    data = r.json()
    registry = data.get("registry") or {}
    plans = data.get("plans") or {}

    cats = registry.get("categories") or []
    print("  Categories:", cats)
    for nc in ("Packing Variants", "Analytics & SLA", "Notifications"):
        assert_true(f"category '{nc}' present", nc in cats, info=f"cats={cats}")
    if "Form Fields" in cats:
        ff_idx = cats.index("Form Fields")
        for nc in ("Packing Variants", "Analytics & SLA", "Notifications"):
            if nc in cats:
                assert_true(
                    f"'{nc}' index > Form Fields index",
                    cats.index(nc) > ff_idx,
                    info=f"FF idx={ff_idx} {nc} idx={cats.index(nc)}",
                )

    new_keys = [
        "whatsapp_ai_variants", "whatsapp_variant_rotation",
        "packing_variants_manage", "packing_variants_picker",
        "packing_variants_flexible", "packing_variants_copy",
        "packing_variants_custom_categories",
        "analytics_dashboard", "analytics_filters",
        "analytics_revenue_breakdown",
        "sla_engine", "sla_alerts_dashboard", "stage_rules_editor",
        "push_notifications", "bulk_messaging_stages",
        "bulk_message_select_filter",
    ]
    feat_keys = {f["key"] for f in (registry.get("features") or [])}
    for k in new_keys:
        assert_true(f"feature key present: {k}", k in feat_keys)

    feat_by_key = {f["key"]: f for f in (registry.get("features") or [])}
    for k in new_keys:
        if k in feat_by_key:
            label = feat_by_key[k].get("label") or ""
            assert_true(f"{k} has non-empty label", bool(label.strip()),
                        info=f"label={label!r}")

    # Platinum has all 16 new keys
    plat = set(plans.get("platinum") or [])
    for k in new_keys:
        assert_true(f"platinum has {k}", k in plat)

    # Free/silver/gold do NOT have these new keys
    for plan_name in ("free_trial", "silver", "gold"):
        s = set(plans.get(plan_name) or [])
        for k in new_keys:
            assert_true(
                f"{plan_name} does NOT have {k} by default",
                k not in s,
                info=f"{plan_name} unexpectedly has {k}",
            )

    # ─────────────────── 2. GET plan-features (regular user) ───────────────────
    print("\n— TEST 2: GET /api/admin/plan-features as user2 —")
    r = requests.get(f"{API}/admin/plan-features", headers=H(user2_token), timeout=20)
    assert_eq("user2 /plan-features status (forbidden)", r.status_code, 403)

    # ─────────────────── 3. PUT plan-features (admin) ───────────────────
    print("\n— TEST 3: PUT /api/admin/plan-features (silver = [whatsapp_ai_variants]) —")
    original_silver = list(plans.get("silver") or [])
    payload_plans = {
        "free_trial": list(plans.get("free_trial") or []),
        "silver":     ["whatsapp_ai_variants"],
        "gold":       list(plans.get("gold") or []),
        "platinum":   list(plans.get("platinum") or []),
    }
    r = requests.put(
        f"{API}/admin/plan-features",
        headers=H(admin_token),
        json={"plans": payload_plans},
        timeout=20,
    )
    assert_eq("PUT /admin/plan-features status", r.status_code, 200)
    body = r.json()
    new_silver = body.get("plans", {}).get("silver") or []
    assert_eq("PUT response: silver = [whatsapp_ai_variants]",
              new_silver, ["whatsapp_ai_variants"])

    r = requests.get(f"{API}/admin/plan-features", headers=H(admin_token), timeout=20)
    after = r.json()
    silver_after = set(after.get("plans", {}).get("silver") or [])
    assert_true("after GET: silver has whatsapp_ai_variants",
                "whatsapp_ai_variants" in silver_after)

    # Restore original silver
    payload_plans["silver"] = original_silver
    rrest = requests.put(
        f"{API}/admin/plan-features",
        headers=H(admin_token),
        json={"plans": payload_plans},
        timeout=20,
    )
    print(f"  restored silver to original ({len(original_silver)} keys); status={rrest.status_code}")

    # ─────────────────── 4. CRITICAL — Variant Rotation ───────────────────
    print("\n— TEST 4: VARIANT ROTATION (user2) —")
    ttype = "shipment_sent"
    variants_payload = {
        "template_type": ttype,
        "variants": {
            "gu": [
                "VARIANT_A_TEXT_GU rotation_test_AAA",
                "VARIANT_B_TEXT_GU rotation_test_BBB",
                "VARIANT_C_TEXT_GU rotation_test_CCC",
            ],
        },
    }
    r = requests.post(
        f"{API}/me/whatsapp-templates/save-variants",
        headers=H(user2_token),
        json=variants_payload,
        timeout=20,
    )
    assert_eq("save-variants status", r.status_code, 200)
    sv_body = r.json()
    saved = sv_body.get("variants", {}).get("gu") or []
    assert_eq("saved variants count", len(saved), 3)

    # Call 4 times; rotation advances on each call (mod 3).
    sources, bodies = [], []
    for i in range(4):
        r = requests.get(
            f"{API}/me/resolve-template",
            headers=H(user2_token),
            params={"ttype": ttype, "lang": "gu"},
            timeout=20,
        )
        assert_eq(f"resolve-template call {i+1} status", r.status_code, 200)
        j = r.json()
        sources.append(j.get("source"))
        bodies.append(j.get("template"))
        print(f"  call {i+1}: source={j.get('source')!r}  body={(j.get('template') or '')[:80]!r}")

    for i, s in enumerate(sources):
        assert_true(
            f"call {i+1} source starts with 'user_variant_'",
            isinstance(s, str) and s.startswith("user_variant_"),
            info=f"got source={s!r}",
        )

    def parse_idx(s):
        try:
            return int(s.split("_")[-1])
        except Exception:
            return -1

    idxs = [parse_idx(s) for s in sources]
    print(f"  rotation 1-based indices: {idxs}")

    # Each call must advance by exactly 1 (mod 3).
    for i in range(1, 4):
        diff = (idxs[i] - idxs[i-1]) % 3
        assert_eq(
            f"call {i+1} advanced by 1 vs prev ({idxs[i-1]}→{idxs[i]})",
            diff, 1,
        )

    distinct_bodies = set(bodies[:3])
    assert_eq("first 3 calls produce 3 distinct templates",
              len(distinct_bodies), 3)

    assert_eq("call 4 wraps round-robin (body == call 1 body)",
              bodies[3], bodies[0])
    assert_eq("call 4 wraps round-robin (source == call 1 source)",
              sources[3], sources[0])

    # ─────────────────── 5. GET feature-flags ───────────────────
    print("\n— TEST 5: GET /api/me/feature-flags —")
    r = requests.get(f"{API}/me/feature-flags", headers=H(user2_token), timeout=20)
    assert_eq("user2 /me/feature-flags status", r.status_code, 200)
    j = r.json()
    assert_true("response has 'plan'", "plan" in j)
    assert_true("response 'features' is list", isinstance(j.get("features"), list))
    assert_eq("user2 is_admin=false", j.get("is_admin"), False)
    print(f"  user2 plan={j.get('plan')!r} feature_count={len(j.get('features') or [])}")

    r = requests.get(f"{API}/me/feature-flags", headers=H(admin_token), timeout=20)
    assert_eq("admin /me/feature-flags status", r.status_code, 200)
    j2 = r.json()
    assert_eq("admin is_admin=true in feature-flags", j2.get("is_admin"), True)
    feat_admin = set(j2.get("features") or [])
    for k in new_keys:
        assert_true(f"admin feature-flags has {k}", k in feat_admin)

    # ─────────────────────── REPORT ───────────────────────
    print("\n" + "=" * 70)
    passed = sum(1 for ok, *_ in results if ok)
    total  = len(results)
    fails = [(lbl, info) for ok, lbl, info in results if not ok]
    print(f"RESULT: {passed}/{total} assertions passed")
    if fails:
        print(f"\n— {len(fails)} FAILURE(S) —")
        for lbl, info in fails:
            print(f"  ❌ {lbl}: {info}")
        sys.exit(1)
    else:
        print("ALL PASS ✅")
        sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as e:
        print(f"HTTP error: {e}\nResponse: {e.response.text if e.response else ''}")
        sys.exit(2)
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(3)
