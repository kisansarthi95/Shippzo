"""
Backend tests — Plan Features Registry verification.
Tests the 14 NEW feature additions and auto-migration for the admin
plan-features endpoints.

Live backend: https://logistics-hub-740.preview.emergentagent.com/api
"""
import json
import os
import sys
import time
import uuid
import requests

BASE_URL = "https://logistics-hub-740.preview.emergentagent.com/api"

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASSWORD = "Admin@12345"

PASS = 0
FAIL = 0
FAILURES = []


def assert_(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {msg}")
    else:
        FAIL += 1
        FAILURES.append(msg)
        print(f"  ❌ {msg}")


def section(title):
    print(f"\n{'='*78}\n{title}\n{'='*78}")


# Expected 14 new keys per review
NEW_KEYS = {
    "sheet_restore_my_orders": "Google Sheets",
    "sheet_two_way_status_sync": "Google Sheets",
    "sheet_soft_delete_tombstone": "Google Sheets",
    "master_order_id_counter_custom": "Master Order ID",
    "master_order_id_autofill_new": "Master Order ID",
    "smart_paste_duplicate_check": "Smart Paste",
    "scanner_sound_feedback": "Scanner",
    "scanner_double_confirm": "Scanner",
    "scanner_manual_entry": "Scanner",
    "offline_mode": "Offline Mode",
    "offline_create_shipment": "Offline Mode",
    "offline_sync_queue_view": "Offline Mode",
    "label_customer_id": "Label Design",
    "label_content_budget": "Label Design",
}

# Some existing ("legacy") keys that must remain (sample of 39)
EXISTING_KEYS_SAMPLE = [
    "smart_paste_ai", "smart_paste_voice", "smart_paste_image_ocr",
    "shipment_copy_btn", "shipment_whatsapp_btn", "shipment_print_btn",
    "label_brand_logo", "label_brand_name", "label_custom_fields",
    "sheet_import", "sheet_two_way_sync", "sheet_column_mapping",
    "multiple_couriers", "auto_tracking", "manual_tracking_scan",
    "repeat_customer_detect", "pending_orders_inbox",
    "bulk_print", "pdf_download", "print_preview",
    "whatsapp_template_editor", "whatsapp_eta_customization",
    "whatsapp_copy_template", "ai_rate_customization", "wallet_topup",
    "form_alt_phone", "form_box_dimensions",
    "form_token_amount", "form_shipment_notes",
]


def login(email, password):
    r = requests.post(f"{BASE_URL}/auth/login", json={"email": email, "password": password})
    if r.status_code != 200:
        print(f"LOGIN FAILED for {email}: {r.status_code} {r.text}")
        return None
    return r.json()


def signup(email, password, name="Feat Tester", shop="Feat Shop", phone=None):
    if phone is None:
        # 10-digit unique phone
        phone = "9" + str(int(time.time()))[-9:]
    r = requests.post(f"{BASE_URL}/auth/signup", json={
        "email": email, "password": password, "name": name,
        "shop_name": shop, "phone": phone,
    })
    return r


def main():
    section("STEP 1: Login admin@test.com")
    admin = login(ADMIN_EMAIL, ADMIN_PASSWORD)
    if not admin:
        print("FATAL: cannot continue without admin token")
        sys.exit(1)
    admin_token = admin["token"]
    admin_hdr = {"Authorization": f"Bearer {admin_token}"}
    print(f"  Admin token acquired (is_admin={admin.get('is_admin')})")
    assert_(admin.get("is_admin") is True, "Admin login returns is_admin=true")

    # ---------------------------------------------------------------
    section("STEP 2: GET /admin/plan-features")
    r = requests.get(f"{BASE_URL}/admin/plan-features", headers=admin_hdr)
    assert_(r.status_code == 200, f"GET /admin/plan-features returns 200 (got {r.status_code})")
    if r.status_code != 200:
        print(r.text[:500])
        sys.exit(1)

    body = r.json()
    registry = body.get("registry", {})
    plans = body.get("plans", {})
    features = registry.get("features", [])
    categories = registry.get("categories", [])

    # 53 features
    assert_(len(features) == 53, f"registry.features length == 53 (got {len(features)})")

    # Build map key → feature
    feat_map = {f["key"]: f for f in features}

    # All 14 new keys present with right category
    for key, expected_cat in NEW_KEYS.items():
        f = feat_map.get(key)
        assert_(f is not None, f"NEW key '{key}' present in registry")
        if f:
            assert_(f.get("category") == expected_cat,
                    f"NEW key '{key}' has category '{expected_cat}' (got '{f.get('category')}')")
            assert_(bool(f.get("label")) and isinstance(f.get("label"), str),
                    f"NEW key '{key}' has a non-empty label")

    # Categories includes 3 new ones
    for cat in ["Master Order ID", "Scanner", "Offline Mode"]:
        assert_(cat in categories, f"categories includes '{cat}'")

    # Plans dict has 4 keys
    for plan_key in ["free_trial", "silver", "gold", "platinum"]:
        assert_(plan_key in plans, f"plans has key '{plan_key}'")

    # Platinum is superset of all 53 keys
    plat_set = set(plans.get("platinum", []))
    all_keys = set(feat_map.keys())
    missing_in_plat = all_keys - plat_set
    assert_(len(missing_in_plat) == 0,
            f"plans.platinum is superset of all 53 keys (missing: {missing_in_plat})")
    assert_(len(plat_set) >= 53, f"plans.platinum has >=53 keys (got {len(plat_set)})")

    # Gold defaults
    gold_set = set(plans.get("gold", []))
    for k in ["scanner_sound_feedback", "offline_mode", "label_customer_id",
              "master_order_id_counter_custom", "scanner_double_confirm",
              "offline_sync_queue_view", "label_content_budget",
              "sheet_two_way_status_sync"]:
        assert_(k in gold_set, f"plans.gold includes NEW key '{k}'")

    # Silver tier
    silver_set = set(plans.get("silver", []))
    assert_("sheet_restore_my_orders" in silver_set,
            "plans.silver includes 'sheet_restore_my_orders'")
    assert_("scanner_sound_feedback" in silver_set,
            "plans.silver includes 'scanner_sound_feedback'")
    assert_("offline_mode" not in silver_set,
            "plans.silver does NOT include 'offline_mode'")

    # Free trial — only basic
    ft_set = set(plans.get("free_trial", []))
    assert_("smart_paste_duplicate_check" in ft_set,
            "plans.free_trial includes 'smart_paste_duplicate_check'")
    assert_("scanner_manual_entry" in ft_set,
            "plans.free_trial includes 'scanner_manual_entry'")
    assert_("master_order_id_autofill_new" in ft_set,
            "plans.free_trial includes 'master_order_id_autofill_new'")
    assert_("offline_mode" not in ft_set,
            "plans.free_trial does NOT include 'offline_mode'")
    assert_("master_order_id_counter_custom" not in ft_set,
            "plans.free_trial does NOT include 'master_order_id_counter_custom'")
    assert_("sheet_two_way_status_sync" not in ft_set,
            "plans.free_trial does NOT include 'sheet_two_way_status_sync'")

    # ---------------------------------------------------------------
    section("STEP 3: GET /me/feature-flags (admin)")
    r = requests.get(f"{BASE_URL}/me/feature-flags", headers=admin_hdr)
    assert_(r.status_code == 200, f"GET /me/feature-flags returns 200 (got {r.status_code})")
    body3 = r.json()
    assert_(body3.get("is_admin") is True, "/me/feature-flags is_admin=true for admin")
    feats = body3.get("features", [])
    assert_(len(feats) == 53, f"admin /me/feature-flags has 53 features (got {len(feats)})")
    assert_(set(feats) == all_keys,
            "admin features list == ALL_KEYS (set equality)")

    # ---------------------------------------------------------------
    section("STEP 4: PUT /admin/plan-features round-trip")
    # Take current plans, remove scanner_sound_feedback from silver
    new_silver = [k for k in plans["silver"] if k != "scanner_sound_feedback"]
    assert_("scanner_sound_feedback" not in new_silver,
            "Local mutation: scanner_sound_feedback removed from silver list")

    put_payload = {
        "plans": {
            "free_trial": list(plans["free_trial"]),
            "silver": new_silver,
            "gold": list(plans["gold"]),
            "platinum": list(plans["platinum"]),
        }
    }
    r = requests.put(f"{BASE_URL}/admin/plan-features",
                     json=put_payload, headers=admin_hdr)
    assert_(r.status_code == 200, f"PUT (remove) returns 200 (got {r.status_code}): {r.text[:200]}")

    # GET after PUT
    r = requests.get(f"{BASE_URL}/admin/plan-features", headers=admin_hdr)
    after_remove = r.json().get("plans", {})
    assert_("scanner_sound_feedback" not in set(after_remove.get("silver", [])),
            "After PUT remove: silver does NOT contain 'scanner_sound_feedback'")

    # Put it back
    new_silver_back = list(set(after_remove["silver"]) | {"scanner_sound_feedback"})
    put_payload2 = {
        "plans": {
            "free_trial": list(after_remove.get("free_trial", [])),
            "silver": new_silver_back,
            "gold": list(after_remove.get("gold", [])),
            "platinum": list(after_remove.get("platinum", [])),
        }
    }
    r = requests.put(f"{BASE_URL}/admin/plan-features",
                     json=put_payload2, headers=admin_hdr)
    assert_(r.status_code == 200, f"PUT (add back) returns 200 (got {r.status_code})")

    r = requests.get(f"{BASE_URL}/admin/plan-features", headers=admin_hdr)
    after_add = r.json().get("plans", {})
    assert_("scanner_sound_feedback" in set(after_add.get("silver", [])),
            "After PUT add-back: silver contains 'scanner_sound_feedback'")

    # ---------------------------------------------------------------
    section("STEP 5: New free_trial user feature-flags")
    ts = int(time.time())
    new_email = f"feature_test_{ts}_{uuid.uuid4().hex[:6]}@example.com"
    new_password = "FeatTest@123"
    # Use a plausible 10-digit phone
    sr = signup(new_email, new_password, name="Feature Tester",
                shop="Feature Test Shop", phone="9" + str(ts)[-9:])
    if sr.status_code != 200:
        # try alternate phone
        sr = signup(new_email, new_password, name="Feature Tester",
                    shop="Feature Test Shop",
                    phone="9" + uuid.uuid4().hex[:9].translate(str.maketrans("abcdef", "012345")))
    assert_(sr.status_code == 200,
            f"New user signup returns 200 (got {sr.status_code}): {sr.text[:300]}")
    new_user_token = None
    if sr.status_code == 200:
        new_user_token = sr.json().get("token")
        new_user_plan = sr.json().get("plan", "")
        print(f"  New user plan: '{new_user_plan}'")
    else:
        # try login fallback
        l = login(new_email, new_password)
        if l:
            new_user_token = l.get("token")

    if new_user_token:
        new_user_hdr = {"Authorization": f"Bearer {new_user_token}"}
        r = requests.get(f"{BASE_URL}/me/feature-flags", headers=new_user_hdr)
        assert_(r.status_code == 200, f"new user /me/feature-flags 200 (got {r.status_code})")
        b = r.json()
        assert_(b.get("is_admin") is False, "New user is_admin=false")
        feats = set(b.get("features", []))
        # Should be a subset of admin's all_keys
        assert_(feats.issubset(all_keys),
                f"New user features ⊆ ALL_KEYS (extras: {feats - all_keys})")
        # Plan-specific assertions (free_trial OR could be paywall if device dup)
        plan_for = b.get("plan", "")
        print(f"  /me/feature-flags reports plan='{plan_for}', features count={len(feats)}")
        # If plan is free_trial → assertions per review
        if plan_for == "free_trial":
            assert_("offline_mode" not in feats,
                    "free_trial user does NOT have 'offline_mode'")
            assert_("smart_paste_duplicate_check" in feats,
                    "free_trial user HAS 'smart_paste_duplicate_check'")
            # Subset of plans.free_trial
            ft_feats_after = set(after_add.get("free_trial", []))
            assert_(feats == ft_feats_after,
                    f"free_trial user features == plans.free_trial list "
                    f"(diff: extra={feats-ft_feats_after}, missing={ft_feats_after-feats})")
        else:
            print(f"  ⚠️  New user got plan='{plan_for}' (not free_trial). "
                  f"This can happen if device-fingerprint denied trial. "
                  f"Skipping free_trial-specific subset assertion.")
            # Still verify NOT offline_mode (free_trial wouldn't have it; "" plan also wouldn't)
            assert_("offline_mode" not in feats or plan_for == "platinum",
                    f"non-platinum user does NOT have 'offline_mode' (plan={plan_for})")

    # ---------------------------------------------------------------
    section("STEP 6: Regression — existing 39 features")
    r = requests.get(f"{BASE_URL}/admin/plan-features", headers=admin_hdr)
    body6 = r.json()
    keys_now = {f["key"] for f in body6["registry"]["features"]}
    for k in EXISTING_KEYS_SAMPLE:
        assert_(k in keys_now, f"existing key '{k}' still in registry")

    # PUT round-trip on smart_paste_ai for gold
    plans_now = body6["plans"]
    gold_list = list(plans_now["gold"])
    had_smart = "smart_paste_ai" in gold_list
    # Remove it
    new_gold = [k for k in gold_list if k != "smart_paste_ai"]
    pp = {"plans": {p: plans_now[p] if p != "gold" else new_gold
                    for p in ["free_trial","silver","gold","platinum"]}}
    r = requests.put(f"{BASE_URL}/admin/plan-features", json=pp, headers=admin_hdr)
    assert_(r.status_code == 200, "PUT smart_paste_ai removal from gold → 200")
    r = requests.get(f"{BASE_URL}/admin/plan-features", headers=admin_hdr)
    assert_("smart_paste_ai" not in set(r.json()["plans"]["gold"]),
            "After PUT: gold no longer has smart_paste_ai")
    # Restore
    pp2 = {"plans": {p: plans_now[p] if p != "gold" else gold_list
                     for p in ["free_trial","silver","gold","platinum"]}}
    r = requests.put(f"{BASE_URL}/admin/plan-features", json=pp2, headers=admin_hdr)
    assert_(r.status_code == 200, "PUT smart_paste_ai restore on gold → 200")
    r = requests.get(f"{BASE_URL}/admin/plan-features", headers=admin_hdr)
    final_gold = set(r.json()["plans"]["gold"])
    if had_smart:
        assert_("smart_paste_ai" in final_gold,
                "After restore PUT: gold has smart_paste_ai")
    else:
        print("  (smart_paste_ai was not in gold originally — skipping restore check)")

    # ---------------------------------------------------------------
    section("STEP 7: Regression — core endpoints")
    r = requests.get(f"{BASE_URL}/sheets/probe", headers=admin_hdr)
    assert_(r.status_code == 200, f"GET /sheets/probe → 200 (got {r.status_code})")
    if r.status_code == 200:
        assert_(r.json().get("ok") is True, f"sheets/probe ok=true (got {r.json()})")

    r = requests.get(f"{BASE_URL}/orders/peek-master-id", headers=admin_hdr)
    assert_(r.status_code == 200, f"GET /orders/peek-master-id → 200 (got {r.status_code})")
    if r.status_code == 200:
        b = r.json()
        # Must have valid keys
        for k in ["master_order_id", "auto_generate", "autofill_in_new_shipment"]:
            assert_(k in b, f"peek-master-id has key '{k}'")

    r = requests.get(f"{BASE_URL}/settings", headers=admin_hdr)
    assert_(r.status_code == 200, f"GET /settings → 200 (got {r.status_code})")

    # ---------------------------------------------------------------
    section("FINAL RESULTS")
    total = PASS + FAIL
    print(f"\n  PASSED: {PASS}/{total}")
    print(f"  FAILED: {FAIL}/{total}")
    if FAILURES:
        print("\n  Failed assertions:")
        for f in FAILURES:
            print(f"    - {f}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
