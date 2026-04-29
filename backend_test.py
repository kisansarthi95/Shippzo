"""
Backend tests for Plan Features Registry +4 expansion (57 total) + Backend Gating.

Test plan:
1. Registry size = 57 with 4 new keys (correct labels & categories).
2. Per-plan defaults for the 4 new keys.
3. Admin /me/feature-flags returns is_admin=true, len=57.
4. PUT /admin/plan-features round-trip for csv_export_orders on gold.
5. Backend gate — Two-Way Status Sync on PUT /shipments/{id} (admin → must sync).
6. Backend gate — Soft-Delete tombstone on DELETE /orders/pending/{id} (admin).
7. Regression — settings, sheets/probe, PUT toggle for old key smart_paste_ai.
"""
import os
import sys
import time
import json
import uuid
import requests
from typing import Any, Dict, List, Optional, Tuple

BASE = "https://logistics-hub-740.preview.emergentagent.com/api"
ADMIN_EMAIL = "admin@test.com"
ADMIN_PASSWORD = "Admin@12345"
USER_EMAIL = "user2@test.com"
USER_PASSWORD = "User@12345"

NEW_FEATURE_KEYS = {
    "repeat_customer_banner": {
        "label": "Repeat customer banner (with Use button)",
        "category": "Customer Intelligence",
    },
    "csv_export_orders": {
        "label": "Export orders/shipments to CSV",
        "category": "Shipments List",
    },
    "shipments_bulk_select": {
        "label": "Bulk select / multi-pick mode",
        "category": "Shipments List",
    },
    "whatsapp_per_courier_template": {
        "label": "Per-courier WhatsApp templates",
        "category": "WhatsApp",
    },
}

passed: List[str] = []
failed: List[Tuple[str, str]] = []


def assert_true(cond: bool, msg: str, detail: str = ""):
    if cond:
        passed.append(msg)
        print(f"  ✅ {msg}")
    else:
        failed.append((msg, detail))
        print(f"  ❌ {msg}  {('— ' + detail) if detail else ''}")


def login(email: str, password: str) -> str:
    r = requests.post(f"{BASE}/auth/login", json={"email": email, "password": password}, timeout=20)
    r.raise_for_status()
    return r.json()["token"]


def H(tok: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}


def main() -> int:
    print(f"\n=== Plan Features Registry +4 + Gating ({BASE}) ===\n")

    # ---- Login admin ----
    print("[A] Login admin")
    try:
        admin_tok = login(ADMIN_EMAIL, ADMIN_PASSWORD)
        assert_true(bool(admin_tok), "Admin login OK")
    except Exception as e:
        assert_true(False, "Admin login OK", f"{e}")
        return 1

    # ───────── 1. Registry size & new keys ─────────
    print("\n[1] GET /admin/plan-features (admin) — registry size + 4 new keys")
    r = requests.get(f"{BASE}/admin/plan-features", headers=H(admin_tok), timeout=20)
    assert_true(r.status_code == 200, "GET /admin/plan-features 200", f"status={r.status_code} body={r.text[:200]}")
    body = r.json() if r.status_code == 200 else {}
    registry = body.get("registry", {})
    features = registry.get("features", []) or []
    assert_true(len(features) == 57, f"registry.features length == 57", f"got {len(features)}")

    by_key = {f["key"]: f for f in features}
    for k, meta in NEW_FEATURE_KEYS.items():
        assert_true(k in by_key, f"new key '{k}' present in registry")
        if k in by_key:
            assert_true(
                by_key[k].get("label") == meta["label"],
                f"'{k}' label correct",
                f"got {by_key[k].get('label')!r}",
            )
            assert_true(
                by_key[k].get("category") == meta["category"],
                f"'{k}' category correct",
                f"got {by_key[k].get('category')!r}",
            )

    plans = body.get("plans", {})

    # ───────── 2. Per-plan defaults ─────────
    print("\n[2] Per-plan defaults for the 4 new keys")
    gold = set(plans.get("gold", []))
    silver = set(plans.get("silver", []))
    free = set(plans.get("free_trial", []))
    plat = set(plans.get("platinum", []))

    # gold should have all 4
    for k in NEW_FEATURE_KEYS:
        assert_true(k in gold, f"gold includes '{k}'", f"gold list missing it (got {len(gold)} keys)")

    # silver: csv_export_orders + repeat_customer_banner ONLY (excludes bulk_select & per_courier_template)
    assert_true("csv_export_orders" in silver, "silver includes csv_export_orders")
    assert_true("repeat_customer_banner" in silver, "silver includes repeat_customer_banner")
    assert_true("shipments_bulk_select" not in silver, "silver excludes shipments_bulk_select")
    assert_true(
        "whatsapp_per_courier_template" not in silver,
        "silver excludes whatsapp_per_courier_template",
    )

    # free_trial: none of the 4
    for k in NEW_FEATURE_KEYS:
        assert_true(k not in free, f"free_trial excludes '{k}'")

    # platinum: all 57 (auto via ALL_KEYS)
    assert_true(len(plat) >= 57, f"platinum has all 57 keys (got {len(plat)})")
    all_keys = set(by_key.keys())
    assert_true(all_keys.issubset(plat), "platinum contains ALL registry keys")

    # ───────── 3. Admin /me/feature-flags ─────────
    print("\n[3] Admin /me/feature-flags is_admin=true & len==57")
    r = requests.get(f"{BASE}/me/feature-flags", headers=H(admin_tok), timeout=20)
    assert_true(r.status_code == 200, "GET /me/feature-flags 200", f"{r.status_code}")
    flags = r.json() if r.status_code == 200 else {}
    assert_true(flags.get("is_admin") is True, "feature-flags is_admin=true")
    feats = flags.get("features", []) or []
    assert_true(len(feats) == 57, f"feature-flags features length == 57", f"got {len(feats)}")
    for k in NEW_FEATURE_KEYS:
        assert_true(k in feats, f"admin features include '{k}'")

    # ───────── 4. PUT /admin/plan-features round-trip ─────────
    print("\n[4] PUT /admin/plan-features round-trip — toggle csv_export_orders on 'gold'")
    # snapshot current state to restore at end
    snapshot = {p: list(plans.get(p, [])) for p in ("free_trial", "silver", "gold", "platinum")}

    # toggle OFF
    new_plans = {p: list(snapshot[p]) for p in snapshot}
    new_plans["gold"] = [k for k in new_plans["gold"] if k != "csv_export_orders"]
    r = requests.put(
        f"{BASE}/admin/plan-features",
        headers=H(admin_tok),
        json={"plans": new_plans},
        timeout=20,
    )
    assert_true(r.status_code == 200, "PUT /admin/plan-features (off) 200", f"{r.status_code} {r.text[:200]}")

    r = requests.get(f"{BASE}/admin/plan-features", headers=H(admin_tok), timeout=20)
    cur_gold = set(r.json().get("plans", {}).get("gold", []))
    assert_true("csv_export_orders" not in cur_gold, "gold no longer includes csv_export_orders after OFF")

    # toggle ON
    new_plans = {p: list(snapshot[p]) for p in snapshot}
    if "csv_export_orders" not in new_plans["gold"]:
        new_plans["gold"].append("csv_export_orders")
    r = requests.put(
        f"{BASE}/admin/plan-features",
        headers=H(admin_tok),
        json={"plans": new_plans},
        timeout=20,
    )
    assert_true(r.status_code == 200, "PUT /admin/plan-features (on) 200", f"{r.status_code}")
    r = requests.get(f"{BASE}/admin/plan-features", headers=H(admin_tok), timeout=20)
    cur_gold = set(r.json().get("plans", {}).get("gold", []))
    assert_true("csv_export_orders" in cur_gold, "gold includes csv_export_orders after ON")

    # ───────── 5. Backend gate — Two-Way Status Sync (admin) ─────────
    print("\n[5] Two-Way Status Sync via PUT /shipments/{id}")
    paste = (
        "Two-Way Sync Test\n"
        "9001234567\n"
        "12 MG Road, Ahmedabad, Gujarat 380001\n"
        "Order: 250\n"
        "COD\n"
    )
    r = requests.post(
        f"{BASE}/smart-paste",
        headers=H(admin_tok),
        json={"text": paste},
        timeout=60,
    )
    assert_true(r.status_code == 200, "POST /smart-paste 200", f"{r.status_code} {r.text[:200]}")
    pending = r.json() if r.status_code == 200 else {}
    sheet_row = pending.get("sheet_row_num")
    pending_id = pending.get("id")
    assert_true(isinstance(sheet_row, int) and sheet_row > 1, "PendingOrder.sheet_row_num > 1", f"got {sheet_row}")

    # Pick a courier (admin's first courier) for ship-now
    r = requests.get(f"{BASE}/couriers", headers=H(admin_tok), timeout=20)
    assert_true(r.status_code == 200, "GET /couriers 200")
    couriers = r.json() if r.status_code == 200 else []
    assert_true(len(couriers) > 0, "admin has at least 1 courier")
    courier_id = couriers[0]["id"] if couriers else None

    ship_id: Optional[str] = None
    if pending_id and courier_id:
        r = requests.post(
            f"{BASE}/orders/pending/{pending_id}/ship",
            headers=H(admin_tok),
            json={"courier_id": courier_id, "overrides": {}},
            timeout=30,
        )
        assert_true(r.status_code == 200, "Ship pending -> shipment 200", f"{r.status_code} {r.text[:200]}")
        ship = r.json() if r.status_code == 200 else {}
        ship_id = ship.get("id")
        assert_true(ship.get("sheet_row_num") == sheet_row, "shipment carries sheet_row_num forward")

    # PUT /shipments/{id} status=Delivered → triggers two-way sync
    if ship_id:
        r = requests.put(
            f"{BASE}/shipments/{ship_id}",
            headers=H(admin_tok),
            json={"status": "Delivered"},
            timeout=30,
        )
        assert_true(r.status_code == 200, "PUT shipment status=Delivered 200", f"{r.status_code} {r.text[:200]}")
        body = r.json() if r.status_code == 200 else {}
        assert_true(body.get("status") == "Delivered", "shipment status=Delivered")
        assert_true(bool(body.get("delivered_at")), "delivered_at set")

    # Inspect backend log for sync line
    time.sleep(1.0)
    log_check = os.popen("tail -n 200 /var/log/supervisor/backend.err.log /var/log/supervisor/backend.out.log 2>/dev/null").read()
    if sheet_row:
        marker = f"Sheet status sync OK: row={sheet_row}"
        assert_true(marker in log_check, f"backend log contains 'Sheet status sync OK: row={sheet_row}'",
                    f"checked tail of supervisor logs ({len(log_check)} chars)")

    # ───────── 6. Soft-delete tombstone (admin) ─────────
    print("\n[6] Soft-delete tombstone via DELETE /orders/pending/{id}")
    paste2 = (
        "Soft Delete Gate Test\n"
        "9007777777\n"
        "5 Park Road, Surat, Gujarat 395001\n"
        "Order: 100\n"
        "Prepaid\n"
    )
    r = requests.post(
        f"{BASE}/smart-paste",
        headers=H(admin_tok),
        json={"text": paste2},
        timeout=60,
    )
    assert_true(r.status_code == 200, "POST /smart-paste (soft-delete fixture) 200", f"{r.status_code}")
    p2 = r.json() if r.status_code == 200 else {}
    p2_id = p2.get("id")
    p2_row = p2.get("sheet_row_num")
    assert_true(isinstance(p2_row, int) and p2_row > 1, "fixture PendingOrder.sheet_row_num > 1", f"got {p2_row}")

    if p2_id:
        r = requests.delete(f"{BASE}/orders/pending/{p2_id}", headers=H(admin_tok), timeout=30)
        assert_true(r.status_code == 200, "DELETE pending 200", f"{r.status_code} {r.text[:200]}")
        body = r.json() if r.status_code == 200 else {}
        sheet = body.get("sheet", {})
        assert_true(body.get("ok") is True, "DELETE response ok=true")
        assert_true(sheet.get("attempted") is True, "sheet.attempted=true (gate passed for admin)",
                    f"got {sheet}")
        # Sheet writer is configured (admin has been writing rows successfully) — expect ok
        assert_true(sheet.get("ok") is True, "sheet.ok=true", f"got {sheet}")
        assert_true(sheet.get("row") == p2_row, "sheet.row matches sheet_row_num")

    # ───────── 7. Regression ─────────
    print("\n[7] Regression — existing 53 keys present, /settings, /sheets/probe, OLD-key toggle")
    # 53 of the original keys should still be present (i.e., 57 - 4 NEW)
    existing_count = sum(1 for k in by_key if k not in NEW_FEATURE_KEYS)
    assert_true(existing_count == 53, f"53 existing (pre-+4) keys still in registry", f"got {existing_count}")

    r = requests.get(f"{BASE}/settings", headers=H(admin_tok), timeout=20)
    assert_true(r.status_code == 200, "GET /settings 200")

    r = requests.get(f"{BASE}/sheets/probe", headers=H(admin_tok), timeout=30)
    assert_true(r.status_code == 200, "GET /sheets/probe 200", f"{r.status_code} {r.text[:200]}")

    # PUT toggle for OLD key (smart_paste_ai) on free_trial — toggle off then on
    r = requests.get(f"{BASE}/admin/plan-features", headers=H(admin_tok), timeout=20)
    cur = r.json().get("plans", {}) if r.status_code == 200 else {}
    cur_ft = list(cur.get("free_trial", []))
    new_payload = {p: list(cur.get(p, [])) for p in ("free_trial", "silver", "gold", "platinum")}
    new_payload["free_trial"] = [k for k in cur_ft if k != "smart_paste_ai"]
    r = requests.put(f"{BASE}/admin/plan-features", headers=H(admin_tok), json={"plans": new_payload}, timeout=20)
    assert_true(r.status_code == 200, "PUT /admin/plan-features OLD-key OFF 200", f"{r.status_code}")

    r = requests.get(f"{BASE}/admin/plan-features", headers=H(admin_tok), timeout=20)
    cur_ft2 = set(r.json().get("plans", {}).get("free_trial", []))
    assert_true("smart_paste_ai" not in cur_ft2, "free_trial smart_paste_ai removed (round-trip works)")

    # Restore
    new_payload["free_trial"] = list(cur_ft)
    r = requests.put(f"{BASE}/admin/plan-features", headers=H(admin_tok), json={"plans": new_payload}, timeout=20)
    assert_true(r.status_code == 200, "PUT /admin/plan-features OLD-key restored 200", f"{r.status_code}")

    # ───────── Summary ─────────
    print("\n=== Summary ===")
    print(f"  Passed: {len(passed)}")
    print(f"  Failed: {len(failed)}")
    if failed:
        print("\nFailures:")
        for m, d in failed:
            print(f"  ✗ {m}  {('— ' + d) if d else ''}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
