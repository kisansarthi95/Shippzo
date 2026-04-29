"""
Phase-D backend verification — User Sheet Read-Only Mode.

Live backend: https://logistics-hub-740.preview.emergentagent.com/api
Credentials: admin@test.com / Admin@12345
"""
import json
import re
import sys
import time
from typing import Any, Dict, List, Optional

import requests

BASE = "https://logistics-hub-740.preview.emergentagent.com/api"
EMAIL = "admin@test.com"
PASSWORD = "Admin@12345"

PASS: List[str] = []
FAIL: List[str] = []


def ok(label: str, cond: bool, extra: str = "") -> None:
    if cond:
        PASS.append(label)
        print(f"  ✅ {label}")
    else:
        FAIL.append(f"{label} :: {extra}")
        print(f"  ❌ {label} :: {extra}")


def login() -> str:
    r = requests.post(
        f"{BASE}/auth/login",
        json={"email": EMAIL, "password": PASSWORD},
        timeout=30,
    )
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    return r.json()["token"]


def H(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def tail_backend_log(n: int = 400) -> str:
    """Read the latest n lines from /var/log/supervisor/backend.err.log"""
    try:
        with open("/var/log/supervisor/backend.err.log", "r", errors="ignore") as f:
            lines = f.readlines()
        return "".join(lines[-n:])
    except Exception as e:
        return f"<unable to read backend log: {e}>"


def main() -> int:
    print(f"\n=== Phase-D Verification :: {BASE} ===\n")

    print("[1] Login admin@test.com")
    token = login()
    print(f"  token len={len(token)}")
    ok("login → token issued", len(token) > 20, "no token")

    # Mark log position by reading current size to slice afterwards
    log_before = tail_backend_log(2000)
    log_before_len = len(log_before)

    print("\n[2] Smart Paste create")
    paste_text = (
        "Name: Phase D Test\n"
        "Mobile: 9112233445\n"
        "Address: Sample Street 12\n"
        "City: Surat\n"
        "State: Gujarat\n"
        "Pincode: 395001\n"
        "COD ₹ 199\n"
    )
    r = requests.post(
        f"{BASE}/smart-paste",
        headers=H(token),
        json={"text": paste_text},
        timeout=60,
    )
    ok("POST /smart-paste → 200", r.status_code == 200, f"{r.status_code} {r.text[:300]}")
    body = r.json() if r.status_code == 200 else {}
    print(f"  body keys: {list(body.keys())}")

    pending_id = body.get("id")
    moid = body.get("master_order_id", "")
    ok("response has id", bool(pending_id), f"got {pending_id!r}")
    ok(
        "master_order_id is 11+ digits and positive",
        isinstance(moid, str) and moid.isdigit() and len(moid) >= 11 and int(moid) > 0,
        f"got {moid!r}",
    )
    ok(
        "customer_name == 'Phase D Test'",
        body.get("customer_name") == "Phase D Test",
        f"got {body.get('customer_name')!r}",
    )
    ok(
        "customer_phone == '9112233445'",
        body.get("customer_phone") == "9112233445",
        f"got {body.get('customer_phone')!r}",
    )
    ok(
        "pincode == '395001'",
        str(body.get("pincode")) == "395001",
        f"got {body.get('pincode')!r}",
    )
    ok(
        "payment_mode == 'COD'",
        body.get("payment_mode") == "COD",
        f"got {body.get('payment_mode')!r}",
    )
    ok(
        "amount == 199.0",
        float(body.get("amount") or 0) == 199.0,
        f"got {body.get('amount')!r}",
    )
    sheet_row_num = body.get("sheet_row_num")
    ok(
        "sheet_row_num is positive int",
        isinstance(sheet_row_num, int) and sheet_row_num > 1,
        f"got {sheet_row_num!r}",
    )

    print("\n[3] Verify Mongo persistence")
    if pending_id:
        r = requests.get(
            f"{BASE}/orders/pending/{pending_id}",
            headers=H(token),
            timeout=30,
        )
        ok(
            "GET /orders/pending/{id} → 200",
            r.status_code == 200,
            f"{r.status_code} {r.text[:200]}",
        )
        if r.status_code == 200:
            doc = r.json()
            ok(
                "doc round-trip: customer_name preserved",
                doc.get("customer_name") == "Phase D Test",
                f"got {doc.get('customer_name')}",
            )
            ok(
                "doc round-trip: master_order_id preserved",
                doc.get("master_order_id") == moid,
                f"got {doc.get('master_order_id')}",
            )

    print("\n[4] Verify Master Sheet probe")
    r = requests.get(f"{BASE}/sheets/probe", headers=H(token), timeout=30)
    ok(
        "GET /sheets/probe → 200",
        r.status_code == 200,
        f"{r.status_code} {r.text[:200]}",
    )
    probe = r.json() if r.status_code == 200 else {}
    ok(
        "sheets probe ok=true",
        probe.get("ok") is True,
        f"got {probe!r}",
    )

    print("\n[5] CRITICAL — Verify NO user-sheet write happened")
    # Wait briefly to allow any async log flush
    time.sleep(1.5)
    # Anchor: find the line that contains our master append (A{sheet_row_num}:S{sheet_row_num}
    # or A{sheet_row_num}:N{sheet_row_num}). Inspect everything AFTER that anchor.
    log_full = tail_backend_log(4000)
    anchor_pat = re.compile(
        rf"Sheet append OK:.*?A{sheet_row_num}:[NS]{sheet_row_num}"
    ) if isinstance(sheet_row_num, int) else None
    new_log = log_full
    if anchor_pat is not None:
        m = None
        for m in anchor_pat.finditer(log_full):
            pass
        if m is not None:
            new_log = log_full[m.start():]
    print(f"  scanning {len(new_log)} chars AFTER our master-append anchor")

    ok(
        "backend log contains 'Sheet append OK' (master sheet)",
        "Sheet append OK" in new_log and (
            f"A{sheet_row_num}" in new_log if isinstance(sheet_row_num, int) else True
        ),
        "did not find master append marker in recent log",
    )

    # CRITICAL: NO user-sheet append OK lines AFTER our master append
    user_append_present = "User-sheet append OK" in new_log
    ok(
        "NO 'User-sheet append OK' line after our master append",
        not user_append_present,
        "User-sheet append OK was emitted AFTER our master append — auto-write should be DISABLED",
    )

    # Also expect NO "User-sheet write skipped" because the block doesn't run
    user_skip_present = "User-sheet write skipped" in new_log
    ok(
        "NO 'User-sheet write skipped' line after our master append (gate skips block)",
        not user_skip_present,
        "User-sheet write skipped emitted — should not even attempt the block",
    )

    print("\n[6] Phase-C regression — POST /sheets/sync-from-master append mode")
    r = requests.post(
        f"{BASE}/sheets/sync-from-master",
        headers=H(token),
        json={"overwrite": False},
        timeout=120,
    )
    print(f"  status={r.status_code} body={r.text[:300]}")
    if r.status_code == 422:
        # Admin's sheet might not be linked — fetch settings to investigate
        rs = requests.get(f"{BASE}/settings", headers=H(token), timeout=30)
        print(f"  settings.sheet={rs.json().get('sheet') if rs.status_code == 200 else 'err'}")
        ok(
            "sync-from-master 200 (admin has linked sheet)",
            False,
            "Got 422 — admin doesn't have personal sheet linked",
        )
    else:
        ok(
            "POST /sheets/sync-from-master → 200",
            r.status_code == 200,
            f"{r.status_code} {r.text[:300]}",
        )
        if r.status_code == 200:
            sync_body = r.json()
            ok(
                "sync ok=true",
                sync_body.get("ok") is True,
                f"got {sync_body}",
            )
            ok(
                "sync mode == 'append'",
                sync_body.get("mode") == "append",
                f"got mode={sync_body.get('mode')!r}",
            )
            ok(
                "sync rows_synced is int",
                isinstance(sync_body.get("rows_synced"), int),
                f"got {sync_body.get('rows_synced')!r}",
            )
            ok(
                "sync master_total_rows is int",
                isinstance(sync_body.get("master_total_rows"), int),
                f"got {sync_body.get('master_total_rows')!r}",
            )

    print("\n[7] Master Order ID counter regression")
    r = requests.get(f"{BASE}/orders/peek-master-id", headers=H(token), timeout=30)
    ok(
        "GET /orders/peek-master-id → 200",
        r.status_code == 200,
        f"{r.status_code} {r.text[:200]}",
    )
    if r.status_code == 200:
        peek = r.json()
        ok(
            "peek has master_order_id key",
            "master_order_id" in peek,
            f"got keys={list(peek.keys())}",
        )
        ok(
            "peek has auto_generate key",
            "auto_generate" in peek,
            f"got keys={list(peek.keys())}",
        )
        ok(
            "peek has autofill_in_new_shipment key",
            "autofill_in_new_shipment" in peek,
            f"got keys={list(peek.keys())}",
        )

    print("\n[8] Settings regression")
    r = requests.get(f"{BASE}/settings", headers=H(token), timeout=30)
    ok(
        "GET /settings → 200",
        r.status_code == 200,
        f"{r.status_code} {r.text[:200]}",
    )
    if r.status_code == 200:
        s = r.json()
        for key in ("sheet", "order_id_auto_generate", "order_id_autofill_in_new_shipment", "custom_fields"):
            ok(
                f"settings has '{key}'",
                key in s,
                f"missing — got keys={list(s.keys())[:20]}",
            )

    print("\n[9] Cleanup — DELETE pending order (soft-delete tombstones master row)")
    if pending_id:
        r = requests.delete(
            f"{BASE}/orders/pending/{pending_id}",
            headers=H(token),
            timeout=60,
        )
        ok(
            "DELETE /orders/pending/{id} → 200",
            r.status_code == 200,
            f"{r.status_code} {r.text[:200]}",
        )
        if r.status_code == 200:
            d = r.json()
            ok(
                "delete response ok=true",
                d.get("ok") is True,
                f"got {d}",
            )
            sh = d.get("sheet") or {}
            ok(
                "delete response sheet.attempted == true",
                sh.get("attempted") is True,
                f"got {sh}",
            )
            ok(
                "delete response sheet.ok == true (row tombstoned)",
                sh.get("ok") is True,
                f"got {sh}",
            )

    print(f"\n=== RESULT: {len(PASS)} pass / {len(FAIL)} fail ===")
    if FAIL:
        print("\nFailing assertions:")
        for f in FAIL:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
