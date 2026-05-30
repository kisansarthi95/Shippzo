"""
Phase-29 Support Articles backend test.

Tests the public + admin CRUD endpoints for support articles per
the review request (2026-05-30).
"""
import os
import sys
import json
import requests
from typing import Any, Dict, Optional

BASE_URL = "https://logistics-hub-740.preview.emergentagent.com/api"
ADMIN_EMAIL = "admin@test.com"
ADMIN_PASS  = "Admin@12345"
USER_EMAIL  = "user2@test.com"
USER_PASS   = "User@12345"

PASS = []
FAIL = []


def _log(step: str, ok: bool, detail: str = "") -> None:
    tag = "PASS" if ok else "FAIL"
    print(f"[{tag}] {step}{(' — ' + detail) if detail else ''}")
    (PASS if ok else FAIL).append((step, detail))


def login(email: str, password: str) -> str:
    r = requests.post(
        f"{BASE_URL}/auth/login",
        json={"email": email, "password": password},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["token"]


def H(tok: Optional[str]) -> Dict[str, str]:
    return {"Authorization": f"Bearer {tok}"} if tok else {}


def main() -> int:
    admin_tok = login(ADMIN_EMAIL, ADMIN_PASS)
    user_tok  = login(USER_EMAIL,  USER_PASS)
    print(f"Admin token len={len(admin_tok)} | User token len={len(user_tok)}")

    # ── 1) GET /api/articles (no auth)
    r = requests.get(f"{BASE_URL}/articles", timeout=15)
    ok = r.status_code == 200
    _log("1.status==200 GET /api/articles (no auth)", ok,
         f"status={r.status_code} body={r.text[:200]}")
    items = []
    if ok:
        body = r.json()
        items = body.get("items") or []
        _log("1.items.length==6", len(items) == 6,
             f"got {len(items)}")
        first = items[0] if items else {}
        required = {"id", "title", "summary", "icon", "category",
                    "sort_order", "is_visible"}
        missing = required - set(first.keys())
        _log("1.required fields present", not missing,
             f"missing={missing}")
        _log("1.sort_order is int",
             isinstance(first.get("sort_order"), int),
             f"type={type(first.get('sort_order')).__name__}")
        _log("1.is_visible==True for all",
             all(it.get("is_visible") is True for it in items),
             f"vals={[it.get('is_visible') for it in items]}")
        _log("1.body NOT in list payload",
             all("body" not in it for it in items),
             f"sample keys={list(first.keys())}")
        orders = [it.get("sort_order") for it in items]
        _log("1.ordered by sort_order asc",
             orders == sorted(orders), f"orders={orders}")

    # ── 2) GET /api/articles/label
    r = requests.get(f"{BASE_URL}/articles/label", timeout=15)
    ok = r.status_code == 200
    _log("2.status==200 GET /api/articles/label", ok,
         f"status={r.status_code} body={r.text[:200]}")
    if ok:
        item = r.json().get("item") or {}
        required = {"id", "title", "summary", "body", "icon", "category"}
        missing = required - set(item.keys())
        _log("2.required fields present (incl. body)", not missing,
             f"missing={missing}")
        body_text = item.get("body") or ""
        _log("2.body non-empty", bool(body_text), f"len={len(body_text)}")
        _log("2.body length > 100 chars", len(body_text) > 100,
             f"len={len(body_text)}")

    # ── 3) GET /api/articles/INVALID_ID
    r = requests.get(f"{BASE_URL}/articles/INVALID_ID", timeout=15)
    _log("3.status==404 GET /api/articles/INVALID_ID",
         r.status_code == 404, f"status={r.status_code}")

    # ── 4) GET /api/admin/articles (non-admin token)
    r = requests.get(f"{BASE_URL}/admin/articles", headers=H(user_tok), timeout=15)
    _log("4.status==403 GET /api/admin/articles (non-admin)",
         r.status_code == 403, f"status={r.status_code} body={r.text[:200]}")

    # ── 5) GET /api/admin/articles (admin token)
    r = requests.get(f"{BASE_URL}/admin/articles", headers=H(admin_tok), timeout=15)
    ok = r.status_code == 200
    _log("5.status==200 GET /api/admin/articles (admin)", ok,
         f"status={r.status_code} body={r.text[:200]}")
    if ok:
        body = r.json()
        admin_items = body.get("items") or []
        _log("5.items.length==6", len(admin_items) == 6,
             f"got {len(admin_items)}")
        _log("5.visible==6", body.get("visible") == 6,
             f"visible={body.get('visible')}")
        _log("5.hidden==0", body.get("hidden") == 0,
             f"hidden={body.get('hidden')}")

    # ── 6) POST /api/admin/articles (admin)
    payload = {
        "title": "Test article from agent",
        "body": "This is a test article body. Should be long enough.",
        "category": "Testing",
    }
    r = requests.post(f"{BASE_URL}/admin/articles", headers=H(admin_tok),
                      json=payload, timeout=15)
    ok = r.status_code == 200
    _log("6.status==200 POST /api/admin/articles", ok,
         f"status={r.status_code} body={r.text[:300]}")
    NEW_ID = ""
    if ok:
        item = r.json().get("item") or {}
        NEW_ID = item.get("id") or ""
        _log("6.id non-empty", bool(NEW_ID), f"id={NEW_ID}")
        _log("6.id starts with 'admin-'", NEW_ID.startswith("admin-"),
             f"id={NEW_ID}")
        _log("6.title matches", item.get("title") == "Test article from agent",
             f"title={item.get('title')}")
        _log("6.is_visible==True (default)",
             item.get("is_visible") is True,
             f"is_visible={item.get('is_visible')}")

    if not NEW_ID:
        print("\n>> Cannot continue — no NEW_ID available.")
        _print_summary()
        return 1

    # ── 7) PATCH NEW_ID is_visible=False
    r = requests.patch(f"{BASE_URL}/admin/articles/{NEW_ID}",
                       headers=H(admin_tok),
                       json={"is_visible": False}, timeout=15)
    ok = r.status_code == 200
    _log("7.status==200 PATCH is_visible=false", ok,
         f"status={r.status_code} body={r.text[:200]}")
    if ok:
        item = r.json().get("item") or {}
        _log("7.is_visible==False",
             item.get("is_visible") is False,
             f"is_visible={item.get('is_visible')}")

    # ── 8) GET public /api/articles/{NEW_ID} → 404 (hidden)
    r = requests.get(f"{BASE_URL}/articles/{NEW_ID}", timeout=15)
    _log("8.status==404 GET public hidden article",
         r.status_code == 404, f"status={r.status_code}")

    # ── 9) PATCH is_visible=true + title
    r = requests.patch(f"{BASE_URL}/admin/articles/{NEW_ID}",
                       headers=H(admin_tok),
                       json={"is_visible": True,
                             "title": "Updated test article"},
                       timeout=15)
    ok = r.status_code == 200
    _log("9.status==200 PATCH is_visible+title", ok,
         f"status={r.status_code} body={r.text[:200]}")
    if ok:
        item = r.json().get("item") or {}
        _log("9.title=='Updated test article'",
             item.get("title") == "Updated test article",
             f"title={item.get('title')}")

    # ── 10) GET public /api/articles/{NEW_ID} → 200
    r = requests.get(f"{BASE_URL}/articles/{NEW_ID}", timeout=15)
    ok = r.status_code == 200
    _log("10.status==200 GET public after re-show", ok,
         f"status={r.status_code} body={r.text[:200]}")
    if ok:
        item = r.json().get("item") or {}
        _log("10.title=='Updated test article'",
             item.get("title") == "Updated test article",
             f"title={item.get('title')}")

    # ── 11) GET /api/admin/articles/{NEW_ID}
    r = requests.get(f"{BASE_URL}/admin/articles/{NEW_ID}",
                     headers=H(admin_tok), timeout=15)
    ok = r.status_code == 200
    _log("11.status==200 admin detail", ok,
         f"status={r.status_code} body={r.text[:200]}")
    if ok:
        item = r.json().get("item") or {}
        _log("11.body present (non-empty)", bool(item.get("body")),
             f"body len={len(item.get('body') or '')}")

    # ── 12) POST /api/admin/articles (non-admin) → 403
    r = requests.post(f"{BASE_URL}/admin/articles", headers=H(user_tok),
                      json=payload, timeout=15)
    _log("12.status==403 POST as non-admin",
         r.status_code == 403, f"status={r.status_code}")

    # ── 13) PATCH (non-admin) → 403
    r = requests.patch(f"{BASE_URL}/admin/articles/{NEW_ID}",
                       headers=H(user_tok),
                       json={"is_visible": False}, timeout=15)
    _log("13.status==403 PATCH as non-admin",
         r.status_code == 403, f"status={r.status_code}")

    # ── 14) DELETE (non-admin) → 403
    r = requests.delete(f"{BASE_URL}/admin/articles/{NEW_ID}",
                        headers=H(user_tok), timeout=15)
    _log("14.status==403 DELETE as non-admin",
         r.status_code == 403, f"status={r.status_code}")

    # ── 15) DELETE admin → 200 + deleted:1
    r = requests.delete(f"{BASE_URL}/admin/articles/{NEW_ID}",
                        headers=H(admin_tok), timeout=15)
    ok = r.status_code == 200
    _log("15.status==200 DELETE admin", ok,
         f"status={r.status_code} body={r.text[:200]}")
    if ok:
        body = r.json()
        _log("15.deleted==1", body.get("deleted") == 1,
             f"deleted={body.get('deleted')}")
    # confirm 404 after delete
    r = requests.get(f"{BASE_URL}/articles/{NEW_ID}", timeout=15)
    _log("15.GET after delete → 404", r.status_code == 404,
         f"status={r.status_code}")

    # ── 16) DELETE non-existent (admin) → 404
    r = requests.delete(f"{BASE_URL}/admin/articles/NON_EXISTENT_ID_XYZ",
                        headers=H(admin_tok), timeout=15)
    _log("16.status==404 DELETE non-existent",
         r.status_code == 404, f"status={r.status_code}")

    # ── 17) POST empty body → 422
    r = requests.post(f"{BASE_URL}/admin/articles", headers=H(admin_tok),
                      json={}, timeout=15)
    _log("17.status==422 POST empty body",
         r.status_code == 422, f"status={r.status_code} body={r.text[:200]}")

    # ── 18) Sanity: GET /api/articles still has 6
    r = requests.get(f"{BASE_URL}/articles", timeout=15)
    ok = r.status_code == 200
    _log("18.status==200 sanity GET /api/articles", ok,
         f"status={r.status_code}")
    if ok:
        items = r.json().get("items") or []
        _log("18.items.length==6 after cleanup",
             len(items) == 6, f"got {len(items)}")

    _print_summary()
    return 0 if not FAIL else 1


def _print_summary():
    print("\n" + "=" * 70)
    print(f"SUMMARY: {len(PASS)} passed, {len(FAIL)} failed")
    print("=" * 70)
    if FAIL:
        print("\nFAILED ASSERTIONS:")
        for step, detail in FAIL:
            print(f"  ❌ {step} — {detail}")


if __name__ == "__main__":
    sys.exit(main())
