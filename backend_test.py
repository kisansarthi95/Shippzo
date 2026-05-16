"""
Phase-22 — Verify Resend email notification triggers on Support Center endpoints.

Tests:
 1. POST /api/support/tickets (user)        → triggers admin alert email
 2. POST /api/support/tickets/{id}/reply (admin) → triggers user alert email
 3. POST /api/support/tickets/{id}/reply (user)  → NO email
 4. Smoke checks: admin list (200), admin list w/ user auth (403), user list (200, contains ticket)
"""

import os
import re
import sys
import time
import subprocess
import requests

BASE = "https://logistics-hub-740.preview.emergentagent.com/api"
ADMIN_EMAIL = "admin@test.com"
ADMIN_PASS = "Admin@12345"
USER_EMAIL = "user2@test.com"
USER_PASS = "User@12345"

LOG_PATHS = [
    "/var/log/supervisor/backend.err.log",
    "/var/log/supervisor/backend.out.log",
]


def login(email: str, password: str) -> str:
    r = requests.post(f"{BASE}/auth/login", json={"email": email, "password": password}, timeout=20)
    r.raise_for_status()
    return r.json()["token"]


def hdr(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


def get_log_offsets() -> dict:
    offsets = {}
    for p in LOG_PATHS:
        try:
            offsets[p] = os.path.getsize(p) if os.path.exists(p) else 0
        except Exception:
            offsets[p] = 0
    return offsets


def get_new_log_lines(prev_offsets: dict) -> list:
    new = []
    for p, off in prev_offsets.items():
        if not os.path.exists(p):
            continue
        try:
            with open(p, "r", errors="replace") as f:
                f.seek(off)
                new.extend(f.read().splitlines())
        except Exception:
            pass
    return new


def wait_for_log(prev_offsets: dict, pattern: str, timeout: float = 10.0):
    rx = re.compile(pattern)
    deadline = time.time() + timeout
    while time.time() < deadline:
        lines = get_new_log_lines(prev_offsets)
        for ln in lines:
            if rx.search(ln):
                return ln
        time.sleep(0.4)
    return None


def main():
    results = []

    def report(name, ok, detail=""):
        status = "PASS" if ok else "FAIL"
        results.append((name, ok, detail))
        print(f"[{status}] {name} :: {detail}")

    try:
        user_token = login(USER_EMAIL, USER_PASS)
        admin_token = login(ADMIN_EMAIL, ADMIN_PASS)
        report("auth.login.user", True, "user2 logged in")
        report("auth.login.admin", True, "admin logged in")
    except Exception as e:
        report("auth.login", False, f"failed: {e}")
        return summarize(results)

    # SCENARIO 1 — create ticket
    offsets = get_log_offsets()
    body = {
        "title":          "Phase-22 backend email test",
        "description":    "Auto-generated test ticket to validate admin alert email.",
        "category":       "label_print",
        "courier_name":   "DTDC",
        "order_id":       "TEST-001",
        "issue_started":  "today",
        "screenshot_b64": "",
        "recording_b64":  "",
        "device_info":    {"platform": "test", "app_version": "1.0.0"},
    }
    t0 = time.time()
    r = requests.post(f"{BASE}/support/tickets", json=body, headers=hdr(user_token), timeout=30)
    latency = time.time() - t0
    if r.status_code != 200:
        report("1.create_ticket", False, f"HTTP {r.status_code}: {r.text[:300]}")
        return summarize(results)
    ticket = r.json()
    ticket_id = ticket.get("id")
    ticket_number = ticket.get("ticket_number", "")
    ok = bool(ticket_id) and bool(re.match(r"^SHP-\d{4,}$", ticket_number or ""))
    report(
        "1.create_ticket",
        ok,
        f"200 OK in {latency:.2f}s — id={ticket_id} ticket_number={ticket_number}",
    )

    line = wait_for_log(
        offsets,
        r"\[email\] sent to=shippzo\.support@gmail\.com subject=.*New support request.*",
        timeout=15.0,
    )
    if line:
        report("1.admin_email_log", True, f"matched: {line.strip()[:250]}")
    else:
        new = get_new_log_lines(offsets)
        email_lines = [ln for ln in new if "[email]" in ln]
        report(
            "1.admin_email_log",
            False,
            f"no '[email] sent ...' line within 15s. recent [email] lines: {email_lines[-5:]}",
        )

    # SCENARIO 2 — admin reply
    offsets = get_log_offsets()
    r = requests.post(
        f"{BASE}/support/tickets/{ticket_id}/reply",
        json={"body": "Phase-22 admin reply test — please confirm receipt."},
        headers=hdr(admin_token),
        timeout=30,
    )
    if r.status_code != 200 or not r.json().get("ok"):
        report("2.admin_reply", False, f"HTTP {r.status_code}: {r.text[:300]}")
    else:
        report("2.admin_reply", True, f"200 OK — message id={r.json().get('message',{}).get('id')}")
        line = wait_for_log(
            offsets,
            r"\[email\] sent to=user2@test\.com subject=.*Shippzo Support replied.*",
            timeout=15.0,
        )
        if line:
            report("2.user_email_log", True, f"matched: {line.strip()[:250]}")
        else:
            new = get_new_log_lines(offsets)
            email_lines = [ln for ln in new if "[email]" in ln]
            report(
                "2.user_email_log",
                False,
                f"no user-alert email line. recent [email] lines: {email_lines[-5:]}",
            )

    # SCENARIO 3 — user reply → NO email
    offsets = get_log_offsets()
    r = requests.post(
        f"{BASE}/support/tickets/{ticket_id}/reply",
        json={"body": "Phase-22 user reply test — should NOT trigger any email."},
        headers=hdr(user_token),
        timeout=30,
    )
    if r.status_code != 200:
        report("3.user_reply", False, f"HTTP {r.status_code}: {r.text[:300]}")
    else:
        report("3.user_reply", True, "200 OK")
        time.sleep(4.0)
        new = get_new_log_lines(offsets)
        sent_lines = [ln for ln in new if "[email] sent" in ln]
        if sent_lines:
            report("3.no_email_on_user_reply", False, f"unexpected: {sent_lines}")
        else:
            report("3.no_email_on_user_reply", True, "no '[email] sent' lines (correct)")

    # SMOKE
    r = requests.get(
        f"{BASE}/admin/support/tickets?status=open&limit=200",
        headers=hdr(admin_token),
        timeout=20,
    )
    if r.status_code == 200 and isinstance(r.json().get("items"), list):
        report("smoke.admin_list_open", True, f"200 OK — {r.json().get('count')} items")
    else:
        report("smoke.admin_list_open", False, f"HTTP {r.status_code}: {r.text[:200]}")

    r = requests.get(f"{BASE}/admin/support/tickets", headers=hdr(user_token), timeout=20)
    report(
        "smoke.admin_list_forbidden_for_user",
        r.status_code == 403,
        f"HTTP {r.status_code} (want 403)",
    )

    r = requests.get(f"{BASE}/support/tickets", headers=hdr(user_token), timeout=20)
    if r.status_code != 200:
        report("smoke.user_list", False, f"HTTP {r.status_code}: {r.text[:200]}")
    else:
        items = r.json().get("items", [])
        found = any(it.get("id") == ticket_id for it in items)
        report(
            "smoke.user_list_contains_ticket",
            found,
            f"200 OK — {len(items)} items, ticket present={found}",
        )

    return summarize(results)


def summarize(results):
    print("\n========= SUMMARY =========")
    passes = sum(1 for _, ok, _ in results if ok)
    fails = sum(1 for _, ok, _ in results if not ok)
    print(f"Total: {len(results)}  Pass: {passes}  Fail: {fails}")
    for name, ok, detail in results:
        print(f"  {'OK' if ok else 'FAIL'}  {name}  — {detail}")
    return fails == 0


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
