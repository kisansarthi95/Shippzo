"""
Focused regression test for Smart Paste + Custom Fields wiring.

Tests:
1. BACKWARD COMPAT: POST /api/smart-paste WITHOUT custom_values returns 200
   with custom_values: {} default.
2. NEW HAPPY PATH: POST /api/smart-paste with custom_values: {<real_id>:"..."}
   returns 200 and echoes the map back.
3. HELPER SAFETY: POST /api/smart-paste with custom_values referencing
   fake/unknown ids never raises; request still 200.
4. /api/me/custom-fields GET shape; POST creates a new field whose id is
   then usable as a custom_values key.
5. PendingOrder doc includes top-level custom_values field — re-fetched
   via GET /api/orders/pending.
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from typing import Any, Dict, List, Optional

import requests

BASE = os.environ.get(
    "BACKEND_URL",
    "https://logistics-hub-740.preview.emergentagent.com",
).rstrip("/")
API = f"{BASE}/api"

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASS = "Admin@12345"
USER_EMAIL = "user2@test.com"
USER_PASS = "User@12345"

assertions: List[tuple] = []   # (label, ok, detail)


def check(label: str, ok: bool, detail: str = "") -> bool:
    assertions.append((label, ok, detail))
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {label}" + (f"  -- {detail}" if detail else ""))
    return ok


def login(email: str, password: str) -> str:
    r = requests.post(
        f"{API}/auth/login",
        json={"email": email, "password": password},
        timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(f"login {email} failed: {r.status_code} {r.text}")
    body = r.json()
    return body["token"]


def auth_h(tok: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {tok}"}


def smart_paste_payload(name: str, phone: str) -> Dict[str, Any]:
    text = (
        f"NAME: {name}\n"
        f"PHONE: {phone}\n"
        "ADDRESS: 12 Test Lane, Lal Darwaja\n"
        "CITY: Ahmedabad\n"
        "STATE: Gujarat\n"
        "PINCODE: 380001\n"
        "AMOUNT: 499\n"
        "PAYMENT: COD\n"
        "WEIGHT: 500\n"
    )
    return {"text": text, "skip_llm": True, "use_ai": False}


def main() -> int:
    print(f"BASE = {BASE}")

    # ------------------------------------------------------------------
    # Login admin (admin gets effectively unlimited custom-field cap)
    # ------------------------------------------------------------------
    print("\n[STEP] Login admin + user2")
    admin_tok = login(ADMIN_EMAIL, ADMIN_PASS)
    user_tok = login(USER_EMAIL, USER_PASS)
    check("admin login OK", bool(admin_tok))
    check("user2 login OK", bool(user_tok))

    # ------------------------------------------------------------------
    # 1. /api/me/custom-fields GET baseline shape
    # ------------------------------------------------------------------
    print("\n[STEP] GET /api/me/custom-fields (admin)")
    r = requests.get(f"{API}/me/custom-fields", headers=auth_h(admin_tok), timeout=30)
    check("GET /me/custom-fields admin 200", r.status_code == 200,
          f"status={r.status_code} body={r.text[:300]}")
    body = r.json() if r.ok else {}
    fields_list = body.get("fields", [])
    check("response has fields list", isinstance(fields_list, list))
    check("response has limit/used/feature_enabled keys",
          all(k in body for k in ("limit", "used", "feature_enabled", "plan", "is_admin")),
          f"keys={list(body.keys())}")

    # If the admin already has fields, validate shape on first one.
    if fields_list:
        f0 = fields_list[0]
        required_keys = {"id", "name", "column_letter", "field_type",
                         "active", "show_in_smart_paste", "sort_order"}
        missing = required_keys - set(f0.keys())
        check("custom field shape contains expected keys",
              not missing, f"missing={missing} sample={f0}")

    # ------------------------------------------------------------------
    # 2. BACKWARD COMPAT — Smart Paste WITHOUT custom_values key
    # ------------------------------------------------------------------
    print("\n[STEP] BACKWARD COMPAT: POST /smart-paste WITHOUT custom_values key")
    bc_phone = f"99000{uuid.uuid4().int % 100000:05d}"[:10]
    bc_payload = smart_paste_payload("Backward Compat User", bc_phone)
    # Explicitly NOT including custom_values key.
    r = requests.post(f"{API}/smart-paste", json=bc_payload,
                      headers=auth_h(admin_tok), timeout=60)
    check("smart-paste no-cv 200", r.status_code == 200,
          f"status={r.status_code} body={r.text[:400]}")
    bc_doc: Dict[str, Any] = r.json() if r.ok else {}
    bc_pending_id = bc_doc.get("id")
    check("smart-paste no-cv response contains 'custom_values' key",
          "custom_values" in bc_doc,
          f"keys={list(bc_doc.keys())}")
    check("smart-paste no-cv custom_values == {}",
          bc_doc.get("custom_values") == {},
          f"got={bc_doc.get('custom_values')!r}")
    check("smart-paste no-cv id present",
          bool(bc_pending_id), f"doc={bc_doc.get('id')!r}")
    check("smart-paste no-cv customer_phone parsed",
          bc_doc.get("customer_phone") == bc_phone,
          f"phone={bc_doc.get('customer_phone')!r} expected={bc_phone}")

    # ------------------------------------------------------------------
    # Cleanup helper - keep ids and remove at end.
    # ------------------------------------------------------------------
    pending_to_cleanup: List[tuple] = []  # (token, pending_id)
    if bc_pending_id:
        pending_to_cleanup.append((admin_tok, bc_pending_id))

    # ------------------------------------------------------------------
    # 3. POST /api/me/custom-fields creates a new field on admin
    #    (admin gets unlimited via _get_custom_field_limit)
    # ------------------------------------------------------------------
    print("\n[STEP] POST /api/me/custom-fields (admin) — create new field")
    # Pick a column letter that's unlikely to clash. Use Z+random bias.
    used_cols = {f.get("column_letter") for f in fields_list}
    candidate_cols = ["Z", "Y", "X", "W", "V", "AA", "AB", "AC", "AD", "AE"]
    col_letter = next((c for c in candidate_cols if c not in used_cols), "AF")
    new_field_payload = {
        "name": f"QA_TestField_{uuid.uuid4().hex[:6]}",
        "column_letter": col_letter,
        "field_type": "text",
        "show_in_form": True,
        "show_in_smart_paste": True,
        "sort_order": 99,
    }
    r = requests.post(f"{API}/me/custom-fields", json=new_field_payload,
                      headers=auth_h(admin_tok), timeout=30)
    new_field_id: Optional[str] = None
    if r.status_code == 200:
        cf = r.json()
        new_field_id = cf.get("id")
        check("POST /me/custom-fields 200", True)
        check("new custom field has id", bool(new_field_id), f"cf={cf}")
        check("new custom field name preserved",
              cf.get("name") == new_field_payload["name"])
        check("new custom field column_letter preserved",
              cf.get("column_letter") == col_letter)
        check("new custom field active=True", cf.get("active") is True)
    else:
        check("POST /me/custom-fields 200", False,
              f"status={r.status_code} body={r.text[:300]}")

    # ------------------------------------------------------------------
    # 4. NEW HAPPY PATH — Smart Paste WITH custom_values referencing real id
    # ------------------------------------------------------------------
    print("\n[STEP] HAPPY PATH: POST /smart-paste WITH custom_values "
          "{real_field_id: 'Hello'}")
    if new_field_id:
        hp_phone = f"99001{uuid.uuid4().int % 100000:05d}"[:10]
        hp_payload = smart_paste_payload("Happy Path User", hp_phone)
        hp_payload["custom_values"] = {new_field_id: "Hello"}
        r = requests.post(f"{API}/smart-paste", json=hp_payload,
                          headers=auth_h(admin_tok), timeout=60)
        check("smart-paste happy-path 200", r.status_code == 200,
              f"status={r.status_code} body={r.text[:400]}")
        hp_doc = r.json() if r.ok else {}
        hp_pid = hp_doc.get("id")
        if hp_pid:
            pending_to_cleanup.append((admin_tok, hp_pid))
        check("happy-path response.custom_values matches request",
              hp_doc.get("custom_values") == {new_field_id: "Hello"},
              f"got={hp_doc.get('custom_values')!r} expected={{{new_field_id!r}: 'Hello'}}")
        check("happy-path response.id present", bool(hp_pid))

        # Re-fetch via GET /api/orders/pending (list) to confirm Mongo doc
        r = requests.get(f"{API}/orders/pending",
                         headers=auth_h(admin_tok), timeout=30)
        check("GET /orders/pending 200", r.status_code == 200,
              f"status={r.status_code}")
        if r.ok:
            arr = r.json()
            match = next((d for d in arr if d.get("id") == hp_pid), None)
            check("happy-path doc found in GET /orders/pending",
                  match is not None)
            if match:
                check("Mongo PendingOrder doc has top-level 'custom_values' key",
                      "custom_values" in match,
                      f"keys={list(match.keys())[:30]}")
                check("Mongo PendingOrder.custom_values matches submission",
                      match.get("custom_values") == {new_field_id: "Hello"},
                      f"got={match.get('custom_values')!r}")
    else:
        # Use a fake id since field creation failed
        check("HAPPY PATH skipped (no new_field_id created)", False,
              "POST /me/custom-fields didn't return an id")

    # ------------------------------------------------------------------
    # 5. HELPER SAFETY — fake/unknown id; should still 200
    # ------------------------------------------------------------------
    print("\n[STEP] HELPER SAFETY: POST /smart-paste with custom_values "
          "referencing UNKNOWN id")
    fake_id = "fake-id-" + uuid.uuid4().hex[:8]
    safe_phone = f"99002{uuid.uuid4().int % 100000:05d}"[:10]
    safe_payload = smart_paste_payload("Helper Safety User", safe_phone)
    safe_payload["custom_values"] = {fake_id: "Hello-fake"}
    r = requests.post(f"{API}/smart-paste", json=safe_payload,
                      headers=auth_h(admin_tok), timeout=60)
    check("smart-paste fake-id 200 (helper never raised)",
          r.status_code == 200,
          f"status={r.status_code} body={r.text[:400]}")
    safe_doc = r.json() if r.ok else {}
    safe_pid = safe_doc.get("id")
    if safe_pid:
        pending_to_cleanup.append((admin_tok, safe_pid))
    check("fake-id custom_values echoed (string-coerced)",
          safe_doc.get("custom_values") == {fake_id: "Hello-fake"},
          f"got={safe_doc.get('custom_values')!r}")

    # ------------------------------------------------------------------
    # 6. HELPER SAFETY (No sheet connected) — user2 has no sheet linked.
    #    user2 plan=silver so cannot create custom_fields, but smart-paste
    #    must still accept arbitrary custom_values map (helper no-ops since
    #    no sheet connected).
    # ------------------------------------------------------------------
    print("\n[STEP] HELPER SAFETY: user2 (no custom fields, possibly no sheet) "
          "+ unknown id custom_values")
    u2_phone = f"99003{uuid.uuid4().int % 100000:05d}"[:10]
    u2_payload = smart_paste_payload("User2 Helper Safety", u2_phone)
    u2_payload["custom_values"] = {"some-random-id": "X"}
    r = requests.post(f"{API}/smart-paste", json=u2_payload,
                      headers=auth_h(user_tok), timeout=60)
    check("user2 smart-paste with random custom_values 200",
          r.status_code == 200,
          f"status={r.status_code} body={r.text[:400]}")
    u2_doc = r.json() if r.ok else {}
    u2_pid = u2_doc.get("id")
    if u2_pid:
        pending_to_cleanup.append((user_tok, u2_pid))
    check("user2 response.custom_values echoed",
          u2_doc.get("custom_values") == {"some-random-id": "X"},
          f"got={u2_doc.get('custom_values')!r}")

    # Also test user2 no-cv → custom_values:{}
    print("\n[STEP] BACKWARD COMPAT (user2): POST /smart-paste no custom_values")
    u2b_phone = f"99004{uuid.uuid4().int % 100000:05d}"[:10]
    u2b_payload = smart_paste_payload("User2 BC", u2b_phone)
    r = requests.post(f"{API}/smart-paste", json=u2b_payload,
                      headers=auth_h(user_tok), timeout=60)
    check("user2 smart-paste no-cv 200", r.status_code == 200,
          f"status={r.status_code} body={r.text[:400]}")
    u2b_doc = r.json() if r.ok else {}
    u2b_pid = u2b_doc.get("id")
    if u2b_pid:
        pending_to_cleanup.append((user_tok, u2b_pid))
    check("user2 no-cv custom_values == {}",
          u2b_doc.get("custom_values") == {},
          f"got={u2b_doc.get('custom_values')!r}")

    # ------------------------------------------------------------------
    # 7. Empty-value trim verification: empty strings are dropped per
    #    server logic `if v not in (None, "")`.
    # ------------------------------------------------------------------
    if new_field_id:
        print("\n[STEP] EMPTY-VALUE TRIM: custom_values with empty string is dropped")
        et_phone = f"99005{uuid.uuid4().int % 100000:05d}"[:10]
        et_payload = smart_paste_payload("Empty Trim User", et_phone)
        et_payload["custom_values"] = {new_field_id: ""}
        r = requests.post(f"{API}/smart-paste", json=et_payload,
                          headers=auth_h(admin_tok), timeout=60)
        check("empty-value trim 200", r.status_code == 200)
        et_doc = r.json() if r.ok else {}
        et_pid = et_doc.get("id")
        if et_pid:
            pending_to_cleanup.append((admin_tok, et_pid))
        check("empty-value trim drops blank → custom_values == {}",
              et_doc.get("custom_values") == {},
              f"got={et_doc.get('custom_values')!r}")

    # ------------------------------------------------------------------
    # CLEANUP
    # ------------------------------------------------------------------
    print("\n[CLEANUP]")
    for tok, pid in pending_to_cleanup:
        try:
            r = requests.delete(f"{API}/orders/pending/{pid}",
                                headers=auth_h(tok), timeout=30)
            print(f"  delete pending {pid[:8]}… → {r.status_code}")
        except Exception as e:
            print(f"  delete pending {pid[:8]}… → ERROR {e}")

    if new_field_id:
        try:
            r = requests.delete(f"{API}/me/custom-fields/{new_field_id}",
                                headers=auth_h(admin_tok), timeout=30)
            print(f"  delete custom field {new_field_id[:8]}… → {r.status_code}")
        except Exception as e:
            print(f"  delete custom field → ERROR {e}")

    # ------------------------------------------------------------------
    # SUMMARY
    # ------------------------------------------------------------------
    total = len(assertions)
    failed = [a for a in assertions if not a[1]]
    passed = total - len(failed)
    print(f"\n=== RESULT: {passed}/{total} assertions passed ===")
    if failed:
        print("\nFAILED:")
        for label, _, detail in failed:
            print(f"  - {label}\n      {detail}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
