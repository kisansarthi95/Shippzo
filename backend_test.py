"""
Phase-21 Support Tickets backend test.

Tests /api/support/* (user-side) and /api/admin/support/* (admin-side)
endpoints against the live preview backend.

Run: python3 /app/backend_test.py
"""
from __future__ import annotations

import os
import sys
import traceback
from typing import Any, Dict, Optional

import requests

BASE = "https://logistics-hub-740.preview.emergentagent.com/api"

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASS = "Admin@12345"
USER_EMAIL = "user2@test.com"
USER_PASS = "User@12345"

PASS_COUNT = 0
FAIL_COUNT = 0
FAILURES: list[str] = []


def _ok(label: str) -> None:
    global PASS_COUNT
    PASS_COUNT += 1
    print(f"  PASS  {label}")


def _fail(label: str, detail: str = "") -> None:
    global FAIL_COUNT
    FAIL_COUNT += 1
    FAILURES.append(f"{label} :: {detail}")
    print(f"  FAIL  {label}  ::  {detail}")


def _assert(cond: bool, label: str, detail: str = "") -> None:
    if cond:
        _ok(label)
    else:
        _fail(label, detail)


def login(email: str, password: str) -> Optional[str]:
    r = requests.post(
        f"{BASE}/auth/login",
        json={"email": email, "password": password},
        timeout=30,
    )
    if r.status_code != 200:
        print(f"LOGIN FAILED {email}: {r.status_code} {r.text[:200]}")
        return None
    return r.json().get("token")


def auth_headers(tok: Optional[str]) -> Dict[str, str]:
    if not tok:
        return {}
    return {"Authorization": f"Bearer {tok}"}


def main() -> int:
    print("=" * 72)
    print("Phase-21 Support Tickets backend test")
    print("=" * 72)

    admin_tok = login(ADMIN_EMAIL, ADMIN_PASS)
    user_tok = login(USER_EMAIL, USER_PASS)
    if not admin_tok or not user_tok:
        print("Cannot proceed: login failed.")
        return 1

    print(f"\nadmin_tok={admin_tok[:24]}...   user_tok={user_tok[:24]}...\n")

    Hu = auth_headers(user_tok)
    Ha = auth_headers(admin_tok)

    created_ticket_id: Optional[str] = None
    second_ticket_id: Optional[str] = None

    # Provision a third user for cross-user 403 tests
    other_tok: Optional[str] = None
    other_email = f"phase21user_{os.getpid()}@test.com"
    try:
        r = requests.post(
            f"{BASE}/auth/signup",
            json={
                "email": other_email,
                "password": "Other@12345",
                "name": "Phase21 OtherUser",
                "shop_name": "Phase21 Shop",
            },
            timeout=30,
        )
        if r.status_code in (200, 201):
            other_tok = r.json().get("token")
            print(f"Provisioned other user={other_email}, tok={(other_tok or '')[:24]}...")
        else:
            rl = requests.post(
                f"{BASE}/auth/login",
                json={"email": other_email, "password": "Other@12345"},
                timeout=30,
            )
            if rl.status_code == 200:
                other_tok = rl.json().get("token")
                print(f"Logged in pre-existing other user={other_email}")
            else:
                print(f"Signup status={r.status_code} body={r.text[:200]}")
    except Exception as e:
        print(f"Could not provision other user via signup: {e}")

    if not other_tok:
        print("No third user available — cross-user 403 tests will be skipped.")

    Ho = auth_headers(other_tok) if other_tok else {}

    # ───────────────────────────────────────────────────────────────
    # 1) POST /api/support/tickets
    # ───────────────────────────────────────────────────────────────
    print("\n[1] POST /api/support/tickets (create)")

    r = requests.post(
        f"{BASE}/support/tickets",
        headers=Hu,
        json={
            "title": "Label PDF prints blank on iPhone 12",
            "description": "Hi team, when I tap Print on a shipment "
                           "label the resulting PDF is completely "
                           "blank. iOS 17.4, app version 2026.05.",
            "category": "technical",
        },
        timeout=30,
    )
    _assert(r.status_code == 200, "create valid ticket -> 200",
            f"status={r.status_code} body={r.text[:200]}")
    if r.status_code == 200:
        t = r.json()
        created_ticket_id = t.get("id")
        _assert(t.get("status") == "open", "new ticket status=open",
                f"got={t.get('status')}")
        _assert(t.get("priority") == "medium", "new ticket priority=medium",
                f"got={t.get('priority')}")
        _assert(t.get("category") == "technical", "category preserved",
                f"got={t.get('category')}")
        _assert(t.get("user_id") and t.get("user_email") == USER_EMAIL,
                "user_id + user_email stamped",
                f"user_id={t.get('user_id')} email={t.get('user_email')}")
        msgs = t.get("messages") or []
        _assert(len(msgs) == 1, "messages[] has 1 entry from description",
                f"len={len(msgs)}")
        if msgs:
            _assert(msgs[0].get("author_role") == "user",
                    "first message author_role=user",
                    f"got={msgs[0].get('author_role')}")
            _assert("blank" in (msgs[0].get("body") or ""),
                    "first message body == description",
                    f"body={msgs[0].get('body','')[:80]}")
        _assert(bool(t.get("created_at")) and bool(t.get("updated_at"))
                and bool(t.get("last_reply_at")),
                "timestamps present")

    r = requests.post(
        f"{BASE}/support/tickets",
        headers=Hu,
        json={
            "title": "Random",
            "description": "Random description for invalid cat test.",
            "category": "BOGUS_CAT",
        },
        timeout=30,
    )
    _assert(r.status_code == 400, "invalid category -> 400",
            f"status={r.status_code} body={r.text[:200]}")

    r = requests.post(
        f"{BASE}/support/tickets",
        headers=Hu,
        json={"title": "A", "description": "Hello there"},
        timeout=30,
    )
    _assert(r.status_code in (400, 422), "title < 2 -> 400/422",
            f"status={r.status_code}")

    r = requests.post(
        f"{BASE}/support/tickets",
        json={"title": "NoAuth", "description": "Should fail without token"},
        timeout=30,
    )
    _assert(r.status_code == 401, "no auth -> 401",
            f"status={r.status_code} body={r.text[:200]}")

    # second ticket for list filter
    r = requests.post(
        f"{BASE}/support/tickets",
        headers=Hu,
        json={
            "title": "Billing question about Silver plan renewal",
            "description": "I was charged twice for my renewal - "
                           "can you check invoice INV-2026-0517?",
            "category": "billing",
        },
        timeout=30,
    )
    if r.status_code == 200:
        second_ticket_id = r.json().get("id")
        _ok("second ticket created for list tests")
    else:
        _fail("second ticket create", f"status={r.status_code}")

    # ───────────────────────────────────────────────────────────────
    # 2) GET /api/support/tickets
    # ───────────────────────────────────────────────────────────────
    print("\n[2] GET /api/support/tickets (list mine)")
    r = requests.get(f"{BASE}/support/tickets", headers=Hu, timeout=30)
    _assert(r.status_code == 200, "list mine -> 200",
            f"status={r.status_code}")
    if r.status_code == 200:
        body = r.json()
        items = body.get("items") or []
        _assert("count" in body and "items" in body,
                "response has items + count")
        ids = [t.get("id") for t in items]
        _assert(created_ticket_id in ids,
                "newly-created ticket appears in list")
        sample = next((t for t in items if t.get("id") == created_ticket_id),
                      None)
        if sample:
            _assert("message_count" in sample
                    and sample["message_count"] >= 1,
                    "list item has message_count >= 1",
                    f"got={sample.get('message_count')}")
            _assert("last_message_preview" in sample
                    and isinstance(sample["last_message_preview"], str),
                    "list item has last_message_preview string")
            _assert("messages" not in sample,
                    "list items strip full messages[] array",
                    f"keys={list(sample.keys())}")

    r = requests.get(f"{BASE}/support/tickets?status=open",
                     headers=Hu, timeout=30)
    _assert(r.status_code == 200, "list status=open -> 200")
    if r.status_code == 200:
        ids = [t.get("id") for t in (r.json().get("items") or [])]
        _assert(created_ticket_id in ids,
                "status=open includes the new ticket")

    r = requests.get(f"{BASE}/support/tickets?status=closed",
                     headers=Hu, timeout=30)
    _assert(r.status_code == 200, "list status=closed -> 200")
    if r.status_code == 200:
        ids = [t.get("id") for t in (r.json().get("items") or [])]
        _assert(created_ticket_id not in ids,
                "status=closed does NOT include the open ticket")

    r = requests.get(f"{BASE}/support/tickets?status=bogus",
                     headers=Hu, timeout=30)
    _assert(r.status_code == 400, "invalid status filter -> 400",
            f"status={r.status_code}")

    # ───────────────────────────────────────────────────────────────
    # 3) GET /api/support/tickets/{id}
    # ───────────────────────────────────────────────────────────────
    print("\n[3] GET /api/support/tickets/{id} (detail)")
    if created_ticket_id:
        r = requests.get(f"{BASE}/support/tickets/{created_ticket_id}",
                         headers=Hu, timeout=30)
        _assert(r.status_code == 200, "own ticket detail -> 200",
                f"status={r.status_code}")
        if r.status_code == 200:
            t = r.json()
            _assert(t.get("id") == created_ticket_id, "id matches")
            _assert(isinstance(t.get("messages"), list)
                    and len(t["messages"]) >= 1,
                    "detail returns full messages[] thread",
                    f"len={len(t.get('messages') or [])}")

    r = requests.get(f"{BASE}/support/tickets/does-not-exist-12345",
                     headers=Hu, timeout=30)
    _assert(r.status_code == 404, "nonexistent ticket -> 404",
            f"status={r.status_code}")

    if other_tok and created_ticket_id:
        r = requests.get(f"{BASE}/support/tickets/{created_ticket_id}",
                         headers=Ho, timeout=30)
        _assert(r.status_code == 403, "other user reading my ticket -> 403",
                f"status={r.status_code} body={r.text[:160]}")
    else:
        print("  SKIP  3c — no other user")

    # ───────────────────────────────────────────────────────────────
    # 4) reply
    # ───────────────────────────────────────────────────────────────
    print("\n[4] POST /api/support/tickets/{id}/reply")
    if created_ticket_id:
        before = requests.get(f"{BASE}/support/tickets/{created_ticket_id}",
                              headers=Hu, timeout=30).json()
        before_len = len(before.get("messages") or [])

        r = requests.post(
            f"{BASE}/support/tickets/{created_ticket_id}/reply",
            headers=Hu,
            json={"body": "Following up - I cleared cache, still blank PDF."},
            timeout=30,
        )
        _assert(r.status_code == 200, "user reply -> 200",
                f"status={r.status_code} body={r.text[:200]}")

        after = requests.get(f"{BASE}/support/tickets/{created_ticket_id}",
                             headers=Hu, timeout=30).json()
        after_len = len(after.get("messages") or [])
        _assert(after_len == before_len + 1,
                "messages length increased by 1",
                f"before={before_len} after={after_len}")
        _assert(after.get("status") == "open",
                "user reply does NOT auto-change status",
                f"got={after.get('status')}")

    if created_ticket_id:
        r = requests.post(
            f"{BASE}/support/tickets/{created_ticket_id}/reply",
            headers=Hu,
            json={"body": ""},
            timeout=30,
        )
        _assert(r.status_code in (400, 422),
                "empty body reply -> 422 (or 400)",
                f"status={r.status_code}")

    if other_tok and created_ticket_id:
        r = requests.post(
            f"{BASE}/support/tickets/{created_ticket_id}/reply",
            headers=Ho,
            json={"body": "Hi I'm a stranger trying to reply"},
            timeout=30,
        )
        _assert(r.status_code == 403,
                "reply on other user's ticket -> 403",
                f"status={r.status_code}")
    else:
        print("  SKIP  4c — no other user")

    # ───────────────────────────────────────────────────────────────
    # 5) close
    # ───────────────────────────────────────────────────────────────
    print("\n[5] POST /api/support/tickets/{id}/close")
    close_test_id = second_ticket_id
    if close_test_id:
        if other_tok:
            r = requests.post(
                f"{BASE}/support/tickets/{close_test_id}/close",
                headers=Ho, timeout=30,
            )
            _assert(r.status_code == 403,
                    "other user closes my ticket -> 403",
                    f"status={r.status_code}")
        else:
            print("  SKIP  5c — no other user")

        r = requests.post(
            f"{BASE}/support/tickets/{close_test_id}/close",
            headers=Hu, timeout=30,
        )
        _assert(r.status_code == 200, "owner close -> 200",
                f"status={r.status_code} body={r.text[:200]}")

        after = requests.get(f"{BASE}/support/tickets/{close_test_id}",
                             headers=Hu, timeout=30).json()
        _assert(after.get("status") == "closed",
                "after close: status=closed",
                f"got={after.get('status')}")

        r = requests.post(
            f"{BASE}/support/tickets/{close_test_id}/reply",
            headers=Hu,
            json={"body": "Hello after close"},
            timeout=30,
        )
        _assert(r.status_code == 409,
                "reply on closed ticket -> 409",
                f"status={r.status_code} body={r.text[:200]}")

    # ───────────────────────────────────────────────────────────────
    # 6) admin list
    # ───────────────────────────────────────────────────────────────
    print("\n[6] GET /api/admin/support/tickets")
    r = requests.get(f"{BASE}/admin/support/tickets", headers=Ha, timeout=30)
    _assert(r.status_code == 200, "admin list all -> 200",
            f"status={r.status_code} body={r.text[:200]}")
    if r.status_code == 200:
        body = r.json()
        items = body.get("items") or []
        ids = [t.get("id") for t in items]
        _assert(created_ticket_id in ids,
                "admin list includes user2's ticket (cross-user)",
                f"created_ticket_id={created_ticket_id}")
        if second_ticket_id:
            _assert(second_ticket_id in ids,
                    "admin list includes user2's closed ticket")

    r = requests.get(f"{BASE}/admin/support/tickets", headers=Hu, timeout=30)
    _assert(r.status_code == 403,
            "regular user accessing admin list -> 403",
            f"status={r.status_code}")

    # ───────────────────────────────────────────────────────────────
    # 7) admin status patch
    # ───────────────────────────────────────────────────────────────
    print("\n[7] PATCH admin status")
    if created_ticket_id:
        r = requests.patch(
            f"{BASE}/admin/support/tickets/{created_ticket_id}/status",
            headers=Ha, json={"status": "resolved"}, timeout=30,
        )
        _assert(r.status_code == 200, "admin set status=resolved -> 200",
                f"status={r.status_code} body={r.text[:200]}")
        if r.status_code == 200:
            _assert(r.json().get("status") == "resolved",
                    "response confirms status=resolved")
            t = requests.get(f"{BASE}/support/tickets/{created_ticket_id}",
                             headers=Hu, timeout=30).json()
            _assert(t.get("status") == "resolved",
                    "GET confirms ticket status=resolved",
                    f"got={t.get('status')}")

        r = requests.patch(
            f"{BASE}/admin/support/tickets/{created_ticket_id}/status",
            headers=Ha, json={"status": "wibble"}, timeout=30,
        )
        _assert(r.status_code == 400, "invalid admin status -> 400",
                f"status={r.status_code}")

        r = requests.patch(
            f"{BASE}/admin/support/tickets/{created_ticket_id}/status",
            headers=Hu, json={"status": "resolved"}, timeout=30,
        )
        _assert(r.status_code == 403,
                "regular user PATCH status -> 403",
                f"status={r.status_code}")

    # ───────────────────────────────────────────────────────────────
    # 8) admin priority patch
    # ───────────────────────────────────────────────────────────────
    print("\n[8] PATCH admin priority")
    if created_ticket_id:
        r = requests.patch(
            f"{BASE}/admin/support/tickets/{created_ticket_id}/priority",
            headers=Ha, json={"priority": "high"}, timeout=30,
        )
        _assert(r.status_code == 200, "admin set priority=high -> 200",
                f"status={r.status_code}")
        if r.status_code == 200:
            _assert(r.json().get("priority") == "high",
                    "response confirms priority=high")
            t = requests.get(f"{BASE}/support/tickets/{created_ticket_id}",
                             headers=Hu, timeout=30).json()
            _assert(t.get("priority") == "high",
                    "GET confirms priority=high")

        r = requests.patch(
            f"{BASE}/admin/support/tickets/{created_ticket_id}/priority",
            headers=Ha, json={"priority": "ultra"}, timeout=30,
        )
        _assert(r.status_code == 400, "invalid priority -> 400",
                f"status={r.status_code}")

        r = requests.patch(
            f"{BASE}/admin/support/tickets/{created_ticket_id}/priority",
            headers=Hu, json={"priority": "low"}, timeout=30,
        )
        _assert(r.status_code == 403, "regular user PATCH priority -> 403",
                f"status={r.status_code}")

    # ───────────────────────────────────────────────────────────────
    # 9) Admin reply flips status
    # ───────────────────────────────────────────────────────────────
    print("\n[9] Admin reply via user endpoint flips status")
    r = requests.post(
        f"{BASE}/support/tickets",
        headers=Hu,
        json={
            "title": "Need help with bulk import CSV",
            "description": "My 200-row CSV fails on import - error says "
                           "'pincode missing' but col is filled.",
            "category": "general",
        },
        timeout=30,
    )
    _assert(r.status_code == 200, "create fresh ticket for admin-reply test",
            f"status={r.status_code}")
    fresh_id = None
    if r.status_code == 200:
        fresh_id = r.json().get("id")
        _assert(r.json().get("status") == "open",
                "fresh ticket starts at status=open")

    if fresh_id:
        r = requests.post(
            f"{BASE}/support/tickets/{fresh_id}/reply",
            headers=Ha,
            json={"body": "Hi - please email the CSV to support@kisan, "
                          "we'll diagnose."},
            timeout=30,
        )
        _assert(r.status_code == 200, "admin reply via user endpoint -> 200",
                f"status={r.status_code} body={r.text[:200]}")

        t = requests.get(f"{BASE}/support/tickets/{fresh_id}",
                         headers=Hu, timeout=30).json()
        _assert(t.get("status") == "in_progress",
                "after admin reply, status flipped to in_progress",
                f"got={t.get('status')}")

        msgs = t.get("messages") or []
        if msgs:
            _assert(msgs[-1].get("author_role") == "admin",
                    "last message author_role=admin",
                    f"got={msgs[-1].get('author_role')}")

    total = PASS_COUNT + FAIL_COUNT
    print("\n" + "=" * 72)
    print(f"RESULT: {PASS_COUNT}/{total} assertions passed "
          f"({FAIL_COUNT} failed)")
    print("=" * 72)
    if FAILURES:
        print("\nFailures:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    except Exception:
        traceback.print_exc()
        rc = 1
    sys.exit(rc)
