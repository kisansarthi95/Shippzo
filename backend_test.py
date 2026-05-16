"""
Phase-21 Support Tickets — comprehensive backend test suite.

Targets:
  USER routes  : /api/support/tickets, /reply, /close
  ADMIN routes : /api/admin/support/tickets, /status, /priority
  Auth-gated: admin@test.com (Admin@12345) + user2@test.com (User@12345)
"""

import os
import sys
import json
import requests

BASE = "https://logistics-hub-740.preview.emergentagent.com/api"

ADMIN = {"email": "admin@test.com", "password": "Admin@12345"}
USER  = {"email": "user2@test.com", "password": "User@12345"}

PASS = []
FAIL = []


def _log(ok: bool, label: str, info: str = ""):
    rec = f"[{'PASS' if ok else 'FAIL'}] {label}"
    if info:
        rec += f" — {info}"
    (PASS if ok else FAIL).append(rec)
    print(rec)


def login(creds):
    r = requests.post(f"{BASE}/auth/login", json=creds, timeout=30)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    body = r.json()
    return body["token"], body


def H(tok):
    return {"Authorization": f"Bearer {tok}"}


def main():
    # ── Setup ────────────────────────────────────────────────────
    try:
        admin_tok, admin_me = login(ADMIN)
        user_tok,  user_me  = login(USER)
        _log(True, "Login admin + user", f"admin_id={admin_me.get('id')[:8]}… user_id={user_me.get('id')[:8]}…")
        assert admin_me.get("is_admin"), "admin account is not is_admin=true"
        assert not user_me.get("is_admin"), "user2 must not be is_admin"
        _log(True, "Role check (admin=true, user2=false)")
    except Exception as e:
        _log(False, "Login phase", str(e))
        return _summary()

    # =====================================================================
    # 1. POST /api/support/tickets — create
    # =====================================================================
    # 1a — valid
    payload = {
        "title": "Cannot print shipping label",
        "description": "Hi support, the PDF generation throws a blank page when I try to print a label for a long-address shipment. Please help.",
        "category": "technical",
    }
    r = requests.post(f"{BASE}/support/tickets", json=payload, headers=H(user_tok), timeout=20)
    ok = r.status_code == 200 and r.json().get("id") and r.json().get("status") == "open"
    _log(ok, "1a POST /support/tickets valid", f"status={r.status_code} body={r.text[:200]}")
    ticket = r.json() if ok else {}
    user_ticket_id = ticket.get("id")
    assert user_ticket_id, "no ticket id returned"
    # check denormalised fields
    _log(ticket.get("user_id") == user_me["id"], "1a-1 ticket.user_id == current user id")
    _log(ticket.get("user_email") == USER["email"], "1a-2 ticket.user_email denormalised")
    _log(ticket.get("category") == "technical", "1a-3 ticket.category=technical")
    _log(len(ticket.get("messages", [])) == 1 and ticket["messages"][0]["author_role"] == "user",
         "1a-4 ticket.messages[0] is user message")

    # 1b — invalid category → 400
    bad = {**payload, "category": "spaceship"}
    r = requests.post(f"{BASE}/support/tickets", json=bad, headers=H(user_tok), timeout=20)
    _log(r.status_code == 400, "1b POST invalid category → 400", f"got {r.status_code}")

    # 1c — short title → 422 (Pydantic min_length=2)
    short = {"title": "x", "description": "Body text here.", "category": "general"}
    r = requests.post(f"{BASE}/support/tickets", json=short, headers=H(user_tok), timeout=20)
    _log(r.status_code == 422, "1c POST short title → 422", f"got {r.status_code}")

    # 1d — no auth → 401/403
    r = requests.post(f"{BASE}/support/tickets", json=payload, timeout=20)
    _log(r.status_code in (401, 403), "1d POST no auth → 401/403", f"got {r.status_code}")

    # Create a 2nd ticket so list filter has data
    payload2 = {
        "title": "Billing question about Silver plan",
        "description": "What is included in the Silver plan team-member quota?",
        "category": "billing",
    }
    r = requests.post(f"{BASE}/support/tickets", json=payload2, headers=H(user_tok), timeout=20)
    second_ticket_id = r.json().get("id") if r.status_code == 200 else None
    _log(r.status_code == 200, "1e Create 2nd ticket for filter tests")

    # =====================================================================
    # 2. GET /api/support/tickets — list mine
    # =====================================================================
    r = requests.get(f"{BASE}/support/tickets", headers=H(user_tok), timeout=20)
    ok = r.status_code == 200 and "items" in r.json()
    _log(ok, "2a GET list mine")
    items = r.json().get("items", []) if ok else []
    own = [t for t in items if t["id"] in (user_ticket_id, second_ticket_id)]
    _log(len(own) >= 2, "2a-1 contains both my tickets", f"found {len(own)}")
    # Verify list response excludes messages array but provides preview/count
    if items:
        first = items[0]
        _log("messages" not in first, "2a-2 list response strips messages array")
        _log("message_count" in first and "last_message_preview" in first, "2a-3 has message_count + preview")

    # 2b — status filter "open" returns it
    r = requests.get(f"{BASE}/support/tickets?status=open", headers=H(user_tok), timeout=20)
    ok = r.status_code == 200
    items_open = r.json().get("items", []) if ok else []
    found = any(t["id"] == user_ticket_id for t in items_open)
    _log(ok and found, "2b GET ?status=open returns my open ticket")

    # 2c — status=closed returns empty (for this user, none closed yet)
    r = requests.get(f"{BASE}/support/tickets?status=closed", headers=H(user_tok), timeout=20)
    closed_items = r.json().get("items", []) if r.status_code == 200 else []
    # We haven't closed any user2 ticket yet — should be 0 belonging to user_ticket_id
    _log(not any(t["id"] == user_ticket_id for t in closed_items),
         "2c GET ?status=closed → my open ticket NOT in list")

    # 2d — invalid status → 400
    r = requests.get(f"{BASE}/support/tickets?status=banana", headers=H(user_tok), timeout=20)
    _log(r.status_code == 400, "2d GET ?status=banana → 400", f"got {r.status_code}")

    # =====================================================================
    # 3. GET /api/support/tickets/{id}
    # =====================================================================
    # 3a — own → 200
    r = requests.get(f"{BASE}/support/tickets/{user_ticket_id}", headers=H(user_tok), timeout=20)
    ok = r.status_code == 200 and r.json().get("id") == user_ticket_id
    _log(ok, "3a GET own ticket → 200")
    _log(ok and isinstance(r.json().get("messages"), list),
         "3a-1 detail response includes messages array")

    # 3b — unknown id → 404
    r = requests.get(f"{BASE}/support/tickets/does-not-exist-uuid", headers=H(user_tok), timeout=20)
    _log(r.status_code == 404, "3b GET unknown → 404", f"got {r.status_code}")

    # 3c — someone else's ticket → 403
    # Admin creates a ticket; then user2 tries to view → admin's user_id is different
    admin_payload = {
        "title": "Admin testing visibility",
        "description": "Ticket created by admin for cross-tenant test.",
        "category": "general",
    }
    r = requests.post(f"{BASE}/support/tickets", json=admin_payload, headers=H(admin_tok), timeout=20)
    admin_ticket_id = r.json().get("id") if r.status_code == 200 else None
    _log(r.status_code == 200, "3c-prep admin creates ticket")

    r = requests.get(f"{BASE}/support/tickets/{admin_ticket_id}", headers=H(user_tok), timeout=20)
    _log(r.status_code == 403, "3c GET other's ticket as regular user → 403", f"got {r.status_code}")
    # And admin reading user2's ticket should succeed (admin bypass)
    r = requests.get(f"{BASE}/support/tickets/{user_ticket_id}", headers=H(admin_tok), timeout=20)
    _log(r.status_code == 200, "3c-1 admin can read any user's ticket → 200")

    # =====================================================================
    # 4. POST /api/support/tickets/{id}/reply
    # =====================================================================
    # 4a — user reply on own ticket → 200, length+1
    before = requests.get(f"{BASE}/support/tickets/{user_ticket_id}", headers=H(user_tok)).json()
    before_count = len(before.get("messages", []))
    r = requests.post(
        f"{BASE}/support/tickets/{user_ticket_id}/reply",
        json={"body": "Adding more context: it happens only on Android."},
        headers=H(user_tok),
        timeout=20,
    )
    ok = r.status_code == 200 and r.json().get("ok") is True
    _log(ok, "4a User reply → 200", f"got {r.status_code} {r.text[:120]}")
    after = requests.get(f"{BASE}/support/tickets/{user_ticket_id}", headers=H(user_tok)).json()
    after_count = len(after.get("messages", []))
    _log(after_count == before_count + 1,
         "4a-1 messages length grew by 1", f"{before_count} → {after_count}")
    _log(after["messages"][-1]["author_role"] == "user",
         "4a-2 last message author_role=user")
    _log(after["last_reply_by"] == "user", "4a-3 last_reply_by=user")

    # 4b — empty body → 422
    r = requests.post(
        f"{BASE}/support/tickets/{user_ticket_id}/reply",
        json={"body": ""},
        headers=H(user_tok),
        timeout=20,
    )
    _log(r.status_code == 422, "4b Reply empty body → 422", f"got {r.status_code}")

    # 4c — reply on someone else's ticket → 403
    r = requests.post(
        f"{BASE}/support/tickets/{admin_ticket_id}/reply",
        json={"body": "I shouldn't be able to reply here."},
        headers=H(user_tok),
        timeout=20,
    )
    _log(r.status_code == 403, "4c Reply on other's ticket → 403", f"got {r.status_code}")

    # 4d — reply on closed ticket → 409
    # First close `second_ticket_id` (user owns it)
    rc = requests.post(f"{BASE}/support/tickets/{second_ticket_id}/close",
                       headers=H(user_tok), timeout=20)
    _log(rc.status_code == 200, "4d-prep close 2nd ticket", f"got {rc.status_code}")
    r = requests.post(
        f"{BASE}/support/tickets/{second_ticket_id}/reply",
        json={"body": "trying to reply after close"},
        headers=H(user_tok),
        timeout=20,
    )
    _log(r.status_code == 409, "4d Reply on closed ticket → 409", f"got {r.status_code}")

    # =====================================================================
    # 5. POST /api/support/tickets/{id}/close
    # =====================================================================
    # 5a — own close (already done for second_ticket — verify status flipped)
    detail = requests.get(f"{BASE}/support/tickets/{second_ticket_id}",
                          headers=H(user_tok), timeout=20).json()
    _log(detail.get("status") == "closed", "5a Own close → status=closed",
         f"status={detail.get('status')}")

    # 5b — reply after close → 409 (already verified at 4d)

    # 5c — others' close → 403
    r = requests.post(f"{BASE}/support/tickets/{admin_ticket_id}/close",
                      headers=H(user_tok), timeout=20)
    _log(r.status_code == 403, "5c Close other's ticket → 403", f"got {r.status_code}")

    # =====================================================================
    # 6. GET /api/admin/support/tickets
    # =====================================================================
    # 6a — admin 200, returns >= our 3 tickets
    r = requests.get(f"{BASE}/admin/support/tickets", headers=H(admin_tok), timeout=20)
    ok = r.status_code == 200
    admin_items = r.json().get("items", []) if ok else []
    _log(ok, "6a Admin list all → 200")
    ids = {t["id"] for t in admin_items}
    _log({user_ticket_id, second_ticket_id, admin_ticket_id} <= ids,
         "6a-1 admin list contains all 3 created tickets")

    # 6b — regular user → 403
    r = requests.get(f"{BASE}/admin/support/tickets", headers=H(user_tok), timeout=20)
    _log(r.status_code == 403, "6b Regular user → 403", f"got {r.status_code}")

    # =====================================================================
    # 7. PATCH /api/admin/support/tickets/{id}/status
    # =====================================================================
    # 7a — admin sets status=resolved on user_ticket_id (it's currently in_progress or open)
    r = requests.patch(
        f"{BASE}/admin/support/tickets/{user_ticket_id}/status",
        json={"status": "resolved"},
        headers=H(admin_tok),
        timeout=20,
    )
    _log(r.status_code == 200 and r.json().get("status") == "resolved",
         "7a Admin set status=resolved → 200", f"body={r.text[:120]}")
    # Verify via GET
    detail = requests.get(f"{BASE}/support/tickets/{user_ticket_id}",
                          headers=H(admin_tok), timeout=20).json()
    _log(detail.get("status") == "resolved", "7a-1 persisted status=resolved")

    # 7b — invalid status → 400
    r = requests.patch(
        f"{BASE}/admin/support/tickets/{user_ticket_id}/status",
        json={"status": "donezo"},
        headers=H(admin_tok),
        timeout=20,
    )
    _log(r.status_code == 400, "7b Invalid status → 400", f"got {r.status_code}")

    # 7c — regular user → 403
    r = requests.patch(
        f"{BASE}/admin/support/tickets/{user_ticket_id}/status",
        json={"status": "open"},
        headers=H(user_tok),
        timeout=20,
    )
    _log(r.status_code == 403, "7c Regular user PATCH status → 403", f"got {r.status_code}")

    # =====================================================================
    # 8. PATCH /api/admin/support/tickets/{id}/priority
    # =====================================================================
    r = requests.patch(
        f"{BASE}/admin/support/tickets/{user_ticket_id}/priority",
        json={"priority": "high"},
        headers=H(admin_tok),
        timeout=20,
    )
    _log(r.status_code == 200 and r.json().get("priority") == "high",
         "8a Admin set priority=high → 200")
    detail = requests.get(f"{BASE}/support/tickets/{user_ticket_id}",
                          headers=H(admin_tok), timeout=20).json()
    _log(detail.get("priority") == "high", "8a-1 persisted priority=high")

    r = requests.patch(
        f"{BASE}/admin/support/tickets/{user_ticket_id}/priority",
        json={"priority": "extreme"},
        headers=H(admin_tok),
        timeout=20,
    )
    _log(r.status_code == 400, "8b Invalid priority → 400", f"got {r.status_code}")

    # =====================================================================
    # 9. Admin reply auto-status-change: fresh ticket open → admin reply → in_progress
    # =====================================================================
    fresh = {
        "title": "Fresh ticket for status-flip test",
        "description": "Testing admin auto-status-change to in_progress.",
        "category": "general",
    }
    r = requests.post(f"{BASE}/support/tickets", json=fresh, headers=H(user_tok), timeout=20)
    fresh_id = r.json().get("id") if r.status_code == 200 else None
    _log(r.status_code == 200 and r.json().get("status") == "open",
         "9-prep create fresh ticket status=open")

    r = requests.post(
        f"{BASE}/support/tickets/{fresh_id}/reply",
        json={"body": "Hello, support here. Investigating now."},
        headers=H(admin_tok),
        timeout=20,
    )
    _log(r.status_code == 200, "9-1 Admin reply on fresh open ticket → 200",
         f"got {r.status_code}")

    detail = requests.get(f"{BASE}/support/tickets/{fresh_id}",
                          headers=H(admin_tok), timeout=20).json()
    _log(detail.get("status") == "in_progress",
         "9-2 status flipped to in_progress", f"status={detail.get('status')}")
    last_msg = (detail.get("messages") or [])[-1]
    _log(last_msg.get("author_role") == "admin", "9-3 last message author_role=admin")
    _log(detail.get("last_reply_by") == "admin", "9-4 last_reply_by=admin")

    # Confirm in_progress doesn't downgrade to open on subsequent admin replies
    r = requests.post(
        f"{BASE}/support/tickets/{fresh_id}/reply",
        json={"body": "Following up — please retry."},
        headers=H(admin_tok),
        timeout=20,
    )
    detail = requests.get(f"{BASE}/support/tickets/{fresh_id}",
                          headers=H(admin_tok), timeout=20).json()
    _log(detail.get("status") == "in_progress",
         "9-5 status stays in_progress on subsequent admin reply")

    # ── Cleanup ──────────────────────────────────────────────────
    # Soft-delete via close (no DELETE endpoint exists for tickets).
    for tid in (user_ticket_id, second_ticket_id, fresh_id, admin_ticket_id):
        if not tid:
            continue
        # admin can close any via PATCH status=closed
        try:
            requests.patch(
                f"{BASE}/admin/support/tickets/{tid}/status",
                json={"status": "closed"},
                headers=H(admin_tok),
                timeout=10,
            )
        except Exception:
            pass

    return _summary()


def _summary():
    print("\n" + "=" * 70)
    print(f"PASSED: {len(PASS)}    FAILED: {len(FAIL)}")
    print("=" * 70)
    if FAIL:
        print("\n--- Failures ---")
        for f in FAIL:
            print(f)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
