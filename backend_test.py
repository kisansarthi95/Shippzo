"""
Phase-12 Messaging Backend Tests
=================================
Tests ONLY the new endpoints in /app/backend/routers/messaging.py
"""
from __future__ import annotations

import sys
from typing import Any, Dict, Optional, List

import requests

BASE_URL = "https://logistics-hub-740.preview.emergentagent.com/api"

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASSWORD = "Admin@12345"
USER_EMAIL = "user2@test.com"
USER_PASSWORD = "User@12345"


passes = 0
failures = 0
fail_msgs: List[str] = []


def t(label: str, cond: bool, extra: str = ""):
    global passes, failures
    if cond:
        passes += 1
        print(f"  PASS: {label}")
    else:
        failures += 1
        msg = f"  FAIL: {label}" + (f" — {extra}" if extra else "")
        fail_msgs.append(msg)
        print(msg)


def login(email: str, password: str) -> Optional[str]:
    r = requests.post(
        f"{BASE_URL}/auth/login",
        json={"email": email, "password": password},
        timeout=20,
    )
    if r.status_code != 200:
        print(f"!! login failed for {email}: {r.status_code} {r.text[:300]}")
        return None
    return r.json().get("token")


def auth_h(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def section(title: str):
    print(f"\n=== {title} ===")


def test_courier_rules_admin(admin_tok: str, user_tok: str):
    section("1. Courier Rules — Admin")

    r = requests.get(f"{BASE_URL}/admin/courier-rules", headers=auth_h(admin_tok), timeout=20)
    t("GET /admin/courier-rules → 200 (admin)", r.status_code == 200, f"got {r.status_code} {r.text[:200]}")
    if r.status_code == 200:
        data = r.json()
        t("response has 'rules' dict", isinstance(data.get("rules"), dict))
        t("response has 'default_eta_days' int", isinstance(data.get("default_eta_days"), int))

    payload = {"rules": {
        "Demo Courier": {"delivery_eta_days": 5},
        "Indian Post": {"delivery_eta_days": 8},
        "Quick Delivery": {"delivery_eta_days": 1},
    }}
    r = requests.put(f"{BASE_URL}/admin/courier-rules", json=payload, headers=auth_h(admin_tok), timeout=20)
    t("PUT /admin/courier-rules → 200", r.status_code == 200, f"got {r.status_code} {r.text[:200]}")
    if r.status_code == 200:
        rules = r.json().get("rules", {})
        t("PUT echoed Demo Courier eta=5", rules.get("Demo Courier", {}).get("delivery_eta_days") == 5)
        t("PUT echoed Indian Post eta=8", rules.get("Indian Post", {}).get("delivery_eta_days") == 8)
        t("PUT echoed Quick Delivery eta=1", rules.get("Quick Delivery", {}).get("delivery_eta_days") == 1)

    r = requests.get(f"{BASE_URL}/admin/courier-rules", headers=auth_h(admin_tok), timeout=20)
    if r.status_code == 200:
        rules = r.json().get("rules", {})
        t("Persisted: Demo Courier eta=5", rules.get("Demo Courier", {}).get("delivery_eta_days") == 5)
        t("Persisted: Indian Post eta=8", rules.get("Indian Post", {}).get("delivery_eta_days") == 8)

    r = requests.get(f"{BASE_URL}/admin/courier-rules", headers=auth_h(user_tok), timeout=20)
    t("Non-admin GET /admin/courier-rules → 403", r.status_code == 403, f"got {r.status_code}")

    r = requests.put(
        f"{BASE_URL}/admin/courier-rules",
        json={"rules": {"X": {"delivery_eta_days": 1}}},
        headers=auth_h(user_tok), timeout=20,
    )
    t("Non-admin PUT /admin/courier-rules → 403", r.status_code == 403, f"got {r.status_code}")


def test_courier_rules_user(user_tok: str):
    section("2. Courier Rules — User")

    r = requests.get(f"{BASE_URL}/me/courier-rules", headers=auth_h(user_tok), timeout=20)
    t("GET /me/courier-rules → 200", r.status_code == 200, f"got {r.status_code}")
    if r.status_code != 200:
        return
    data = r.json()
    t("has admin_rules", isinstance(data.get("admin_rules"), dict))
    t("has user_rules", isinstance(data.get("user_rules"), dict))
    t("has courier_names list", isinstance(data.get("courier_names"), list))
    t("has default_eta_days", isinstance(data.get("default_eta_days"), int))
    admin_rules = data.get("admin_rules", {})
    t("admin layer shows Demo Courier=5", admin_rules.get("Demo Courier", {}).get("delivery_eta_days") == 5)

    r = requests.put(
        f"{BASE_URL}/me/courier-rules",
        json={"rules": {"Demo Courier": {"delivery_eta_days": 3}}},
        headers=auth_h(user_tok), timeout=20,
    )
    t("PUT /me/courier-rules override → 200", r.status_code == 200, f"got {r.status_code} {r.text[:200]}")
    if r.status_code == 200:
        u = r.json().get("user_rules", {})
        t("user override Demo Courier=3 echoed", u.get("Demo Courier", {}).get("delivery_eta_days") == 3)

    r = requests.get(f"{BASE_URL}/me/courier-rules", headers=auth_h(user_tok), timeout=20)
    if r.status_code == 200:
        u = r.json().get("user_rules", {})
        t("Persisted user override Demo Courier=3", u.get("Demo Courier", {}).get("delivery_eta_days") == 3)

    bad_payload = {"rules": {
        "BadNeg": {"delivery_eta_days": -5},
        "BadHigh": {"delivery_eta_days": 100},
        "BadStr": {"delivery_eta_days": "foo"},
        "BadMissing": {},
        "GoodKeep": {"delivery_eta_days": 4},
    }}
    r = requests.put(
        f"{BASE_URL}/me/courier-rules", json=bad_payload, headers=auth_h(user_tok), timeout=20
    )
    t("PUT invalid values → 200 (silently dropped)", r.status_code == 200, f"got {r.status_code} {r.text[:200]}")
    if r.status_code == 200:
        u = r.json().get("user_rules", {})
        t("BadNeg dropped", "BadNeg" not in u)
        t("BadHigh (>60) dropped", "BadHigh" not in u)
        t("BadStr dropped", "BadStr" not in u)
        t("BadMissing dropped", "BadMissing" not in u)
        t("GoodKeep retained=4", u.get("GoodKeep", {}).get("delivery_eta_days") == 4)

    requests.put(
        f"{BASE_URL}/me/courier-rules",
        json={"rules": {"Demo Courier": {"delivery_eta_days": 3}}},
        headers=auth_h(user_tok), timeout=20,
    )


def test_whatsapp_templates_admin(admin_tok: str, user_tok: str):
    section("3. WhatsApp Templates — Admin")

    expected_types = {"shipment_sent", "dispatch_confirmation", "delivery_confirmation", "delivery_done"}
    expected_langs = {"gu", "hi", "en"}

    r = requests.get(f"{BASE_URL}/admin/whatsapp-templates", headers=auth_h(admin_tok), timeout=20)
    t("GET /admin/whatsapp-templates → 200 (admin)", r.status_code == 200, f"got {r.status_code}")
    if r.status_code == 200:
        data = r.json()
        t("has templates", isinstance(data.get("templates"), dict))
        t("has saved_overrides", isinstance(data.get("saved_overrides"), dict))
        t("has defaults", isinstance(data.get("defaults"), dict))
        t("types == expected", set(data.get("types") or []) == expected_types, f"got {data.get('types')}")
        t("languages == expected", set(data.get("languages") or []) == expected_langs, f"got {data.get('languages')}")
        merged = data.get("templates", {})
        for tp in expected_types:
            t(f"merged has type {tp}", tp in merged)
            for lang in expected_langs:
                if tp in merged:
                    val = merged[tp].get(lang)
                    t(f"merged[{tp}][{lang}] non-empty str", isinstance(val, str) and len(val) > 0)

    r = requests.get(f"{BASE_URL}/admin/whatsapp-templates", headers=auth_h(user_tok), timeout=20)
    t("Non-admin GET /admin/whatsapp-templates → 403", r.status_code == 403, f"got {r.status_code}")

    payload = {"templates": {"shipment_sent": {
        "gu": "Custom Gujarati admin template",
        "en": "Custom English admin template",
    }}}
    r = requests.put(
        f"{BASE_URL}/admin/whatsapp-templates", json=payload, headers=auth_h(admin_tok), timeout=20
    )
    t("PUT /admin/whatsapp-templates → 200", r.status_code == 200, f"got {r.status_code} {r.text[:200]}")
    if r.status_code == 200:
        data = r.json()
        saved = data.get("saved_overrides", {})
        t("admin override gu saved", saved.get("shipment_sent", {}).get("gu") == "Custom Gujarati admin template")
        t("admin override en saved", saved.get("shipment_sent", {}).get("en") == "Custom English admin template")
        t("admin override hi NOT in saved (partial)", "hi" not in saved.get("shipment_sent", {}))
        merged = data.get("templates", {})
        defaults = data.get("defaults", {})
        t("merged[shipment_sent][hi] == bundled default",
          merged.get("shipment_sent", {}).get("hi") == defaults.get("shipment_sent", {}).get("hi"))
        t("merged[shipment_sent][gu] == new override",
          merged.get("shipment_sent", {}).get("gu") == "Custom Gujarati admin template")
        t("merged[delivery_confirmation][gu] == bundled default",
          merged.get("delivery_confirmation", {}).get("gu") == defaults.get("delivery_confirmation", {}).get("gu"))


def test_whatsapp_templates_user(user_tok: str):
    section("4. WhatsApp Templates — User")

    r = requests.get(f"{BASE_URL}/me/whatsapp-templates", headers=auth_h(user_tok), timeout=20)
    t("GET /me/whatsapp-templates → 200", r.status_code == 200, f"got {r.status_code}")
    if r.status_code != 200:
        return
    data = r.json()
    t("has admin_templates", isinstance(data.get("admin_templates"), dict))
    t("has user_templates", isinstance(data.get("user_templates"), dict))
    t("has default_language", isinstance(data.get("default_language"), str))
    t("has types", isinstance(data.get("types"), list))
    t("has languages", isinstance(data.get("languages"), list))
    t("has defaults", isinstance(data.get("defaults"), dict))
    at = data.get("admin_templates", {})
    t("user sees admin's shipment_sent.gu override",
      at.get("shipment_sent", {}).get("gu") == "Custom Gujarati admin template")

    custom_gu = "મારો કસ્ટમ ગુજરાતી મેસેજ"
    payload = {
        "templates": {"delivery_confirmation": {"gu": custom_gu}},
        "default_language": "hi",
    }
    r = requests.put(
        f"{BASE_URL}/me/whatsapp-templates", json=payload, headers=auth_h(user_tok), timeout=20
    )
    t("PUT /me/whatsapp-templates → 200", r.status_code == 200, f"got {r.status_code} {r.text[:200]}")
    if r.status_code == 200:
        d = r.json()
        ut = d.get("user_templates", {})
        t("user override delivery_confirmation.gu saved",
          ut.get("delivery_confirmation", {}).get("gu") == custom_gu)
        t("default_language now 'hi'", d.get("default_language") == "hi")

    r = requests.get(f"{BASE_URL}/me/whatsapp-templates", headers=auth_h(user_tok), timeout=20)
    if r.status_code == 200:
        d = r.json()
        ut = d.get("user_templates", {})
        t("Persisted user override delivery_confirmation.gu",
          ut.get("delivery_confirmation", {}).get("gu") == custom_gu)
        t("Persisted default_language=hi", d.get("default_language") == "hi")

    r = requests.get(
        f"{BASE_URL}/me/resolve-template",
        params={"ttype": "delivery_confirmation", "lang": "gu"},
        headers=auth_h(user_tok), timeout=20,
    )
    t("GET /me/resolve-template gu → 200", r.status_code == 200, f"got {r.status_code} {r.text[:200]}")
    if r.status_code == 200:
        d = r.json()
        t("resolve gu returns user override", d.get("template") == custom_gu)
        t("resolve gu source=user", d.get("source") == "user")
        t("resolve gu language=gu", d.get("language") == "gu")

    r = requests.get(
        f"{BASE_URL}/me/resolve-template",
        params={"ttype": "delivery_confirmation", "lang": "en"},
        headers=auth_h(user_tok), timeout=20,
    )
    t("GET /me/resolve-template en → 200", r.status_code == 200, f"got {r.status_code}")
    if r.status_code == 200:
        d = r.json()
        t("resolve en source != user (admin/bundled)",
          d.get("source") in ("admin", "bundled"), f"got source={d.get('source')}")
        t("resolve en template non-empty",
          isinstance(d.get("template"), str) and len(d.get("template", "")) > 0)
        t("resolve en language=en", d.get("language") == "en")

    r = requests.get(
        f"{BASE_URL}/me/resolve-template",
        params={"ttype": "invalid_type"},
        headers=auth_h(user_tok), timeout=20,
    )
    t("GET /me/resolve-template invalid_type → 400", r.status_code == 400, f"got {r.status_code}")


def _ensure_shipped_shipments(user_tok: str, n_needed: int = 2) -> List[str]:
    r = requests.get(
        f"{BASE_URL}/shipments/dispatch-confirmation",
        headers=auth_h(user_tok), timeout=20,
    )
    if r.status_code == 200:
        rows = r.json().get("shipments", [])
        ids = [r0["id"] for r0 in rows if r0.get("id")]
        if len(ids) >= n_needed:
            return ids[:n_needed]

    r = requests.get(f"{BASE_URL}/shipments", headers=auth_h(user_tok), timeout=20)
    if r.status_code != 200:
        return []
    body = r.json()
    all_ships = body if isinstance(body, list) else body.get("shipments", [])
    candidates = [s for s in all_ships
                  if s.get("status") not in ("Shipped", "Returned", "Cancelled", "Delivered")]
    chosen_ids: List[str] = []
    for s in candidates:
        if len(chosen_ids) >= n_needed:
            break
        sid = s.get("id")
        rr = requests.put(
            f"{BASE_URL}/shipments/{sid}",
            json={"status": "Shipped"},
            headers=auth_h(user_tok), timeout=20,
        )
        if rr.status_code == 200:
            chosen_ids.append(sid)
    return chosen_ids


def test_dispatch_confirmation(user_tok: str):
    section("5. Dispatch Confirmation")

    r = requests.get(
        f"{BASE_URL}/shipments/dispatch-confirmation",
        headers=auth_h(user_tok), timeout=20,
    )
    t("GET /shipments/dispatch-confirmation → 200", r.status_code == 200, f"got {r.status_code} {r.text[:200]}")
    if r.status_code != 200:
        return
    data = r.json()
    t("has shipments list", isinstance(data.get("shipments"), list))
    t("has counts dict", isinstance(data.get("counts"), dict))
    if isinstance(data.get("counts"), dict):
        c = data["counts"]
        t("counts has list/sent/pending keys",
          all(k in c for k in ("list", "sent", "pending")))
        t("counts.list == len(shipments)", c.get("list") == len(data.get("shipments") or []))
    if data.get("shipments"):
        all_shipped = all(s.get("status") == "Shipped" for s in data["shipments"])
        t("all returned shipments have status=Shipped", all_shipped)

    shipped_ids = _ensure_shipped_shipments(user_tok, 2)
    if not shipped_ids:
        t("at least 1 Shipped shipment available", False, "none found/created")
        return
    print(f"  Using shipped IDs: {shipped_ids}")

    requests.post(
        f"{BASE_URL}/shipments/dispatch-confirmation/reset",
        json={"shipment_ids": shipped_ids},
        headers=auth_h(user_tok), timeout=20,
    )

    r = requests.post(
        f"{BASE_URL}/shipments/dispatch-confirmation/mark-sent",
        json={"shipment_ids": shipped_ids},
        headers=auth_h(user_tok), timeout=20,
    )
    t("POST mark-sent → 200", r.status_code == 200, f"got {r.status_code} {r.text[:200]}")
    if r.status_code == 200:
        d = r.json()
        t("response keys updated/skipped/updated_ids/skipped_ids",
          all(k in d for k in ("updated", "skipped", "updated_ids", "skipped_ids")))
        t("first call: updated == len(ids)", d.get("updated") == len(shipped_ids), f"got {d.get('updated')}")
        t("first call: skipped == 0", d.get("skipped") == 0, f"got {d.get('skipped')}")
        t("first call: updated_ids matches input",
          set(d.get("updated_ids") or []) == set(shipped_ids))

    r = requests.post(
        f"{BASE_URL}/shipments/dispatch-confirmation/mark-sent",
        json={"shipment_ids": shipped_ids},
        headers=auth_h(user_tok), timeout=20,
    )
    t("POST mark-sent (repeat same-day) → 200", r.status_code == 200, f"got {r.status_code}")
    if r.status_code == 200:
        d = r.json()
        t("repeat call: updated == 0", d.get("updated") == 0, f"got {d.get('updated')}")
        t("repeat call: skipped == len(ids)", d.get("skipped") == len(shipped_ids), f"got {d.get('skipped')}")
        t("repeat call: skipped_ids matches input",
          set(d.get("skipped_ids") or []) == set(shipped_ids))

    r = requests.get(
        f"{BASE_URL}/shipments/dispatch-confirmation",
        headers=auth_h(user_tok), timeout=20,
    )
    if r.status_code == 200:
        c = r.json().get("counts", {})
        t("counts.sent >= len(marked)",
          (c.get("sent") or 0) >= len(shipped_ids), f"got sent={c.get('sent')}")

    r = requests.post(
        f"{BASE_URL}/shipments/dispatch-confirmation/reset",
        json={"shipment_ids": shipped_ids},
        headers=auth_h(user_tok), timeout=20,
    )
    t("POST dispatch-confirmation/reset → 200", r.status_code == 200, f"got {r.status_code} {r.text[:200]}")
    if r.status_code == 200:
        d = r.json()
        t("reset response has 'updated' key", "updated" in d)
        t("reset.updated == len(ids)", d.get("updated") == len(shipped_ids), f"got {d.get('updated')}")

    r = requests.get(
        f"{BASE_URL}/shipments/dispatch-confirmation",
        headers=auth_h(user_tok), timeout=20,
    )
    if r.status_code == 200:
        rows = {s.get("id"): s for s in r.json().get("shipments", [])}
        all_pending = all(
            (rows.get(sid, {}).get("dispatch_msg_status") or "pending") == "pending"
            for sid in shipped_ids
        )
        t("after reset: marked shipments back to pending status", all_pending)


def test_delivery_confirmation_v2(user_tok: str):
    section("6. Delivery Confirmation v2")

    r = requests.get(
        f"{BASE_URL}/shipments/delivery-confirmation-v2",
        headers=auth_h(user_tok), timeout=20,
    )
    t("GET /shipments/delivery-confirmation-v2 → 200", r.status_code == 200, f"got {r.status_code} {r.text[:200]}")
    if r.status_code != 200:
        return
    d = r.json()
    t("has shipments list", isinstance(d.get("shipments"), list))
    t("has counts dict", isinstance(d.get("counts"), dict))
    t("has eta_min int", isinstance(d.get("eta_min"), int))
    t("has eta_max int", isinstance(d.get("eta_max"), int))
    t("has threshold_override key", "threshold_override" in d)
    t("threshold_override is None by default", d.get("threshold_override") is None)
    if d.get("shipments"):
        all_eta = all("courier_eta_days" in s for s in d["shipments"])
        t("each shipment has courier_eta_days", all_eta)

    r = requests.get(
        f"{BASE_URL}/shipments/delivery-confirmation-v2",
        params={"threshold_days": 0},
        headers=auth_h(user_tok), timeout=20,
    )
    t("GET threshold_days=0 → 200", r.status_code == 200, f"got {r.status_code}")
    if r.status_code == 200:
        d2 = r.json()
        t("threshold_override == 0", d2.get("threshold_override") == 0)
        t("threshold=0 list ≥ default list",
          len(d2.get("shipments", [])) >= len(d.get("shipments", [])),
          f"got threshold=0:{len(d2.get('shipments', []))} default:{len(d.get('shipments', []))}")
        if d2.get("shipments"):
            all_eta_v2 = all("courier_eta_days" in s for s in d2["shipments"])
            t("threshold=0: each shipment has courier_eta_days", all_eta_v2)


def main():
    print(f"Backend URL: {BASE_URL}")

    print("\n--- Authenticating ---")
    admin_tok = login(ADMIN_EMAIL, ADMIN_PASSWORD)
    user_tok = login(USER_EMAIL, USER_PASSWORD)
    if not admin_tok:
        print("FATAL: admin login failed; aborting")
        sys.exit(1)
    if not user_tok:
        print("FATAL: user login failed; aborting")
        sys.exit(1)
    print(f"  admin token: {admin_tok[:20]}...")
    print(f"  user token:  {user_tok[:20]}...")

    test_courier_rules_admin(admin_tok, user_tok)
    test_courier_rules_user(user_tok)
    test_whatsapp_templates_admin(admin_tok, user_tok)
    test_whatsapp_templates_user(user_tok)
    test_dispatch_confirmation(user_tok)
    test_delivery_confirmation_v2(user_tok)

    print("\n" + "=" * 60)
    print(f"RESULT: {passes} passed, {failures} failed (total {passes + failures})")
    if failures:
        print("\nFailures:")
        for m in fail_msgs:
            print(m)
        sys.exit(1)


if __name__ == "__main__":
    main()
