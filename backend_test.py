"""
Phase-21 Support Tickets backend test (post field-additions).
"""
from __future__ import annotations

import re
import sys
import json
import requests

BASE = "https://logistics-hub-740.preview.emergentagent.com/api"
USER_EMAIL = "user2@test.com"
USER_PASSWORD = "User@12345"

PASS = 0
FAIL = 0
FAILURES = []


def _ok(msg: str):
    global PASS
    PASS += 1
    print(f"  PASS  {msg}")


def _fail(msg: str, detail: str = ""):
    global FAIL
    FAIL += 1
    print(f"  FAIL  {msg}")
    if detail:
        print(f"        {detail}")
    FAILURES.append(f"{msg} :: {detail}")


def _hdr(token: str):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def login() -> str:
    r = requests.post(
        f"{BASE}/auth/login",
        json={"email": USER_EMAIL, "password": USER_PASSWORD},
        timeout=30,
    )
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    return r.json()["token"]


def create_ticket(token: str, body: dict, expect_status: int = 200) -> dict:
    r = requests.post(
        f"{BASE}/support/tickets",
        headers=_hdr(token),
        json=body,
        timeout=30,
    )
    if r.status_code != expect_status:
        raise RuntimeError(
            f"POST /support/tickets expected {expect_status} got {r.status_code}: {r.text}"
        )
    return r.json() if r.text else {}


SHP_RE = re.compile(r"^SHP-\d{4,}$")


def test_new_categories(token: str):
    print("\n[1] POST /support/tickets with new + legacy category keys")
    new_cats = [
        "label_print", "account_login", "whatsapp",
        "plan_wallet", "app_bug", "feature_request",
        "order_input", "other",
    ]
    for cat in new_cats:
        try:
            t = create_ticket(token, {
                "title": f"Test ticket — {cat}",
                "description": f"Body for {cat} category test.",
                "category": cat,
            })
            if t.get("category") == cat:
                _ok(f"category={cat} accepted (200)")
            else:
                _fail(f"category={cat} echoed mismatch", f"got {t.get('category')}")
            tn = t.get("ticket_number") or ""
            if SHP_RE.match(tn):
                _ok(f"ticket_number={tn} matches /^SHP-\\d{{4,}}$/")
            else:
                _fail(f"ticket_number malformed for {cat}", f"value={tn!r}")
        except Exception as e:
            _fail(f"category={cat} POST failed", str(e))

    # Invalid category should yield 400
    try:
        r = requests.post(
            f"{BASE}/support/tickets",
            headers=_hdr(token),
            json={
                "title": "Invalid cat test",
                "description": "bogus category",
                "category": "xxx_bogus",
            },
            timeout=30,
        )
        if r.status_code == 400:
            _ok("invalid category 'xxx_bogus' → 400")
        else:
            _fail(
                "invalid category should return 400",
                f"got {r.status_code}: {r.text[:160]}",
            )
    except Exception as e:
        _fail("invalid category request failed", str(e))


def _seq_num(ticket_number: str) -> int:
    m = re.match(r"^SHP-(\d+)$", ticket_number)
    return int(m.group(1)) if m else -1


def test_monotonicity(token: str):
    print("\n[2] SHP-XXXX monotonic increment across 3 back-to-back creates")
    seqs = []
    for i in range(3):
        t = create_ticket(token, {
            "title": f"Mono test {i}",
            "description": f"mono body {i}",
            "category": "general",
        })
        tn = t.get("ticket_number") or ""
        n = _seq_num(tn)
        if n < 0:
            _fail("monotonicity create yielded bad ticket_number", f"value={tn!r}")
            return
        seqs.append(n)
        print(f"        ticket {i}: {tn} (seq={n})")
    if seqs[1] > seqs[0] and seqs[2] > seqs[1]:
        _ok(f"sequential: {seqs[0]} < {seqs[1]} < {seqs[2]}")
        if seqs[1] == seqs[0] + 1 and seqs[2] == seqs[1] + 1:
            _ok("sequential by exactly +1 each (atomic counter behaviour)")
    else:
        _fail("monotonicity broken", f"seqs={seqs}")


def test_supplementary_fields(token: str) -> str:
    print("\n[3] POST with supplementary fields (courier_name / order_id / device_info / issue_started)")
    body = {
        "title": "Courier label not printing",
        "description": "Detail: tried twice, no PDF generated.",
        "category": "label_print",
        "courier_name": "Delhivery",
        "order_id": "ORD-987",
        "issue_started": "yesterday",
        "device_info": {
            "app_version": "1.2.3",
            "platform": "ios",
            "os_version": "17.4",
        },
    }
    t = create_ticket(token, body)
    checks = [
        ("courier_name", "Delhivery"),
        ("order_id_ref", "ORD-987"),
        ("issue_started", "yesterday"),
    ]
    for k, expected in checks:
        if t.get(k) == expected:
            _ok(f"{k}={expected!r} round-trips")
        else:
            _fail(f"{k} mismatch", f"got={t.get(k)!r} expected={expected!r}")
    di = t.get("device_info") or {}
    if (di.get("app_version") == "1.2.3"
            and di.get("platform") == "ios"
            and di.get("os_version") == "17.4"):
        _ok("device_info round-trips with app_version/platform/os_version")
    else:
        _fail("device_info mismatch", f"got={di!r}")
    return t["id"]


def test_list_strips_heavy(token: str) -> str:
    print("\n[4] GET /support/tickets — list view excludes heavy fields")
    body = {
        "title": "Screenshot round-trip ticket",
        "description": "with attached image",
        "category": "app_bug",
        "courier_name": "DTDC",
        "order_id": "ORD-555",
        "issue_started": "today",
        "screenshot_b64": "iVBORw0KGgo=",
        "device_info": {"app_version": "1.0", "platform": "android", "os_version": "14"},
    }
    created = create_ticket(token, body)
    cid = created["id"]

    r = requests.get(f"{BASE}/support/tickets", headers=_hdr(token), timeout=30)
    if r.status_code != 200:
        _fail("GET /support/tickets failed", f"{r.status_code} {r.text[:200]}")
        return cid
    items = r.json().get("items", [])
    if not items:
        _fail("GET /support/tickets returned no items", "")
        return cid
    _ok(f"GET /support/tickets returned {len(items)} items")

    me = next((i for i in items if i.get("id") == cid), None)
    if not me:
        _fail("freshly created ticket not in list", "")
        return cid

    if not me.get("screenshot_b64"):
        _ok("list item has no screenshot_b64 (projection stripped)")
    else:
        _fail(
            "list item still includes screenshot_b64",
            f"len={len(me.get('screenshot_b64',''))}",
        )
    if not me.get("recording_b64"):
        _ok("list item has no recording_b64 (projection stripped)")
    else:
        _fail("list item still includes recording_b64", "")

    light_keys = [
        "id", "ticket_number", "user_id", "title", "category", "status",
        "priority", "courier_name", "order_id_ref", "issue_started",
        "message_count", "last_message_preview", "created_at", "updated_at",
    ]
    missing = [k for k in light_keys if k not in me]
    if not missing:
        _ok("list item includes all expected light fields")
    else:
        _fail("list item missing fields", f"missing={missing} item_keys={list(me.keys())}")

    if me.get("message_count") == 1 and (me.get("last_message_preview") or "").startswith("with attached"):
        _ok(f"message_count=1, preview correct")
    else:
        _fail(
            "message_count/preview mismatch",
            f"mc={me.get('message_count')} preview={me.get('last_message_preview')!r}",
        )
    return cid


def test_detail_has_heavy(token: str, ticket_id: str):
    print("\n[5] GET /support/tickets/{id} — detail view returns full thread + heavy fields")
    r = requests.get(
        f"{BASE}/support/tickets/{ticket_id}",
        headers=_hdr(token),
        timeout=30,
    )
    if r.status_code != 200:
        _fail("GET /support/tickets/{id} failed", f"{r.status_code} {r.text[:200]}")
        return
    t = r.json()
    if t.get("screenshot_b64") == "iVBORw0KGgo=":
        _ok("screenshot_b64 round-trips on detail endpoint")
    else:
        _fail(
            "screenshot_b64 missing or mutated on detail",
            f"value={t.get('screenshot_b64')!r}",
        )
    msgs = t.get("messages") or []
    if isinstance(msgs, list) and len(msgs) >= 1:
        _ok(f"detail returns messages thread (count={len(msgs)})")
        m0 = msgs[0]
        if m0.get("author_role") == "user" and m0.get("body"):
            _ok(f"first message authored by user")
        else:
            _fail("first message bad shape", f"m0={m0!r}")
    else:
        _fail("detail missing messages array", f"value={msgs!r}")
    tn = t.get("ticket_number") or ""
    if SHP_RE.match(tn):
        _ok(f"detail still includes ticket_number={tn}")
    else:
        _fail("detail ticket_number malformed", f"value={tn!r}")


def test_legacy_general_works(token: str):
    print("\n[6] Legacy 'general' category and full list integrity")
    try:
        t = create_ticket(token, {
            "title": "Legacy category test",
            "description": "general should still work",
            "category": "general",
        })
        if t.get("category") == "general" and SHP_RE.match(t.get("ticket_number", "")):
            _ok(f"legacy 'general' accepted, ticket_number={t['ticket_number']}")
        else:
            _fail("legacy 'general' shape unexpected", f"t={json.dumps(t)[:200]}")
    except Exception as e:
        _fail("legacy 'general' create failed", str(e))

    r = requests.get(f"{BASE}/support/tickets", headers=_hdr(token), timeout=30)
    if r.status_code != 200:
        _fail("GET /support/tickets failed after legacy create", f"{r.status_code}")
        return
    items = r.json().get("items", [])
    _ok(f"list endpoint serves {len(items)} tickets without validation breaks")


def main():
    print(f"Target: {BASE}")
    print(f"User:   {USER_EMAIL}")
    try:
        token = login()
    except Exception as e:
        print(f"LOGIN FAILED: {e}")
        sys.exit(2)
    print("login OK")

    test_new_categories(token)
    test_monotonicity(token)
    test_supplementary_fields(token)
    list_ticket_id = test_list_strips_heavy(token)
    test_detail_has_heavy(token, list_ticket_id)
    test_legacy_general_works(token)

    print(f"\n=== {PASS} PASS / {FAIL} FAIL ===")
    if FAIL:
        print("Failures:")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)


if __name__ == "__main__":
    main()
