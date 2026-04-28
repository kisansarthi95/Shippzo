"""
Phase-7d Master Order ID backend tests.

Tests the server-side Master Order ID auto-generation for the SAAS
backend. Covers the full toggle behaviour:
  - Auto-generate ON (default): master_order_id is YYMMDD+5+ digits;
    blank user order_id falls back to master.
  - User-provided order_id is preserved when auto-gen is ON.
  - Sequence increments globally and atomically.
  - Auto-generate OFF: blank order_id 422s, user-provided one is
    persisted as order_id, master_order_id is empty.
  - Settings GET exposes the order_id_auto_generate boolean (default
    true).

The endpoint URL is derived from frontend/.env:EXPO_PUBLIC_BACKEND_URL
to match how the deployed app talks to the backend.
"""

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

# Resolve the public backend URL the way the frontend does.
FRONTEND_ENV = Path("/app/frontend/.env")
BASE = ""
if FRONTEND_ENV.exists():
    for line in FRONTEND_ENV.read_text().splitlines():
        if line.startswith("EXPO_PUBLIC_BACKEND_URL="):
            BASE = line.split("=", 1)[1].strip().strip('"').rstrip("/")
            break
if not BASE:
    BASE = "https://logistics-hub-740.preview.emergentagent.com"
API = f"{BASE}/api"

EMAIL = "admin@test.com"
PASSWORD = "Admin@12345"

PASSED = []
FAILED = []


def _record(ok: bool, name: str, info: str = ""):
    if ok:
        PASSED.append(name)
        print(f"  PASS {name}")
    else:
        FAILED.append(f"{name} :: {info}")
        print(f"  FAIL {name} -- {info}")


def login() -> str:
    r = requests.post(
        f"{API}/auth/login",
        json={"email": EMAIL, "password": PASSWORD},
        timeout=20,
    )
    print(f"login -> {r.status_code}")
    r.raise_for_status()
    body = r.json()
    tok = body.get("token")
    assert tok, f"no token in login response: {body}"
    return tok


def hdr(token: str):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def put_settings(token: str, payload: dict):
    return requests.put(f"{API}/settings", json=payload, headers=hdr(token), timeout=20)


def get_settings(token: str):
    return requests.get(f"{API}/settings", headers=hdr(token), timeout=20)


def smart_paste(token: str, payload: dict):
    return requests.post(
        f"{API}/smart-paste",
        json=payload,
        headers=hdr(token),
        timeout=60,
    )


def cleanup_pending(token: str, pid: str):
    try:
        requests.delete(
            f"{API}/orders/pending/{pid}", headers=hdr(token), timeout=30
        )
    except Exception:
        pass


def test_master_order_id():
    print(f"\n=== Master Order ID Tests against {API} ===\n")
    token = login()

    today_yymmdd = datetime.now(timezone.utc).strftime("%y%m%d")
    print(f"Today's UTC YYMMDD = {today_yymmdd}")

    pending_to_cleanup: list = []

    r = put_settings(token, {"order_id_auto_generate": True})
    _record(r.status_code == 200, "Setup: PUT /settings auto=true", str(r.status_code))

    # ---------- TEST 1 ----------
    print("\n--- Test 1: Auto-generate ON (default) ---")
    text1 = (
        "NAME: Master Test 1\n"
        "PHONE: 9111111111\n"
        "ADDRESS_1: Test addr\n"
        "CITY: Ahmedabad\n"
        "STATE: Gujarat\n"
        "PINCODE: 380001\n"
        "AMOUNT: 100\n"
        "PAYMENT: COD\n"
        "WEIGHT: 500"
    )
    r1 = smart_paste(token, {"text": text1, "skip_llm": True})
    _record(r1.status_code == 200, "T1 POST /smart-paste 200",
            f"got {r1.status_code} body={r1.text[:300]}")
    if r1.status_code != 200:
        return
    body1 = r1.json()
    pending_to_cleanup.append(body1.get("id", ""))

    moid1 = body1.get("master_order_id", "")
    oid1 = body1.get("order_id", "")
    print(f"  master_order_id={moid1!r}  order_id={oid1!r}")

    pat = re.compile(r"^\d{6}\d{5,}$")
    _record(bool(pat.match(moid1)), "T1 master_order_id matches YYMMDD+5+digits",
            f"got {moid1!r}")
    _record(moid1.startswith(today_yymmdd),
            "T1 master_order_id starts with today's UTC YYMMDD",
            f"got {moid1!r} expected prefix {today_yymmdd}")
    _record(oid1 == moid1,
            "T1 order_id falls back to master_order_id when user didn't provide",
            f"order_id={oid1!r} master={moid1!r}")
    _record(body1.get("customer_name") == "Master Test 1",
            "T1 customer_name parsed",
            f"got {body1.get('customer_name')!r}")

    # ---------- TEST 2 ----------
    print("\n--- Test 2: Auto-generate ON, user-provided ORDER_ID ---")
    text2 = (
        "NAME: Master Test 2\n"
        "PHONE: 9222222222\n"
        "ADDRESS_1: Test addr 2\n"
        "CITY: Surat\n"
        "STATE: Gujarat\n"
        "PINCODE: 395001\n"
        "AMOUNT: 250\n"
        "PAYMENT: COD\n"
        "WEIGHT: 600\n"
        "ORDER_ID: ABC-001"
    )
    r2 = smart_paste(token, {"text": text2, "skip_llm": True})
    _record(r2.status_code == 200, "T2 POST /smart-paste 200",
            f"got {r2.status_code} body={r2.text[:300]}")
    if r2.status_code != 200:
        return
    body2 = r2.json()
    pending_to_cleanup.append(body2.get("id", ""))

    moid2 = body2.get("master_order_id", "")
    oid2 = body2.get("order_id", "")
    print(f"  master_order_id={moid2!r}  order_id={oid2!r}")

    _record(bool(pat.match(moid2)), "T2 master_order_id is well-formed",
            f"got {moid2!r}")
    _record(moid2 != moid1, "T2 master_order_id is fresh (different from T1)",
            f"T1={moid1!r} T2={moid2!r}")
    _record(oid2 == "ABC-001",
            "T2 order_id preserves user's ABC-001",
            f"got {oid2!r}")

    # ---------- TEST 3 ----------
    print("\n--- Test 3: Sequence increments globally ---")
    moids = [moid1, moid2]
    for i in range(3):
        text_i = (
            f"NAME: Master Test Seq {i+3}\n"
            f"PHONE: 933333333{i}\n"
            "ADDRESS_1: Seq Addr\n"
            "CITY: Ahmedabad\n"
            "STATE: Gujarat\n"
            "PINCODE: 380001\n"
            "AMOUNT: 50\n"
            "PAYMENT: COD\n"
            "WEIGHT: 100"
        )
        r = smart_paste(token, {"text": text_i, "skip_llm": True})
        _record(r.status_code == 200, f"T3 POST seq #{i+1} 200",
                f"got {r.status_code} body={r.text[:200]}")
        if r.status_code != 200:
            return
        b = r.json()
        pending_to_cleanup.append(b.get("id", ""))
        moids.append(b.get("master_order_id", ""))

    print(f"  collected moids: {moids}")
    for i, m in enumerate(moids):
        _record(m.startswith(today_yymmdd),
                f"T3 moid[{i}] starts with today's YYMMDD",
                f"got {m!r}")

    def _seq(m: str) -> int:
        return int(m[6:])

    seqs = [_seq(m) for m in moids]
    print(f"  seqs={seqs}")
    diffs = [seqs[i + 1] - seqs[i] for i in range(len(seqs) - 1)]
    _record(all(d == 1 for d in diffs),
            "T3 sequence increments by exactly +1 each call",
            f"diffs={diffs}")

    # ---------- TEST 4 ----------
    print("\n--- Test 4: Auto-generate OFF ---")
    r = put_settings(token, {"order_id_auto_generate": False})
    _record(r.status_code == 200, "T4 PUT /settings auto=false",
            f"got {r.status_code}")
    if r.status_code == 200:
        body = r.json()
        _record(body.get("order_id_auto_generate") is False,
                "T4 settings response reflects order_id_auto_generate=false",
                f"got {body.get('order_id_auto_generate')!r}")

    text4a = (
        "NAME: NoID Test\n"
        "PHONE: 9444444444\n"
        "ADDRESS_1: Off Addr\n"
        "CITY: Ahmedabad\n"
        "STATE: Gujarat\n"
        "PINCODE: 380001\n"
        "AMOUNT: 70\n"
        "PAYMENT: COD\n"
        "WEIGHT: 100"
    )
    r4a = smart_paste(token, {"text": text4a, "skip_llm": True})
    _record(r4a.status_code == 422, "T4a Without order_id -> 422",
            f"got {r4a.status_code} body={r4a.text[:300]}")
    try:
        detail = (r4a.json() or {}).get("detail", "")
    except Exception:
        detail = r4a.text
    _record("Order ID is required" in str(detail),
            "T4a 422 detail mentions 'Order ID is required'",
            f"got {detail!r}")

    text4b = text4a + "\nORDER_ID: MY-555"
    r4b = smart_paste(token, {"text": text4b, "skip_llm": True})
    _record(r4b.status_code == 200, "T4b With ORDER_ID -> 200",
            f"got {r4b.status_code} body={r4b.text[:300]}")
    if r4b.status_code == 200:
        b = r4b.json()
        pending_to_cleanup.append(b.get("id", ""))
        _record(b.get("master_order_id", "<missing>") == "",
                "T4b master_order_id is empty string",
                f"got {b.get('master_order_id')!r}")
        _record(b.get("order_id") == "MY-555",
                "T4b order_id == 'MY-555'",
                f"got {b.get('order_id')!r}")

    r = put_settings(token, {"order_id_auto_generate": True})
    _record(r.status_code == 200, "T4 reset PUT /settings auto=true",
            f"got {r.status_code}")

    # ---------- TEST 5 ----------
    print("\n--- Test 5: Settings persistence ---")
    r5 = get_settings(token)
    _record(r5.status_code == 200, "T5 GET /settings 200",
            f"got {r5.status_code}")
    if r5.status_code == 200:
        body = r5.json()
        _record("order_id_auto_generate" in body,
                "T5 GET /settings response includes 'order_id_auto_generate'",
                f"keys={sorted(body.keys())[:25]}")
        _record(isinstance(body.get("order_id_auto_generate"), bool),
                "T5 order_id_auto_generate is a bool",
                f"got type={type(body.get('order_id_auto_generate')).__name__}")
        _record(body.get("order_id_auto_generate") is True,
                "T5 order_id_auto_generate currently True (after reset)",
                f"got {body.get('order_id_auto_generate')!r}")

    # ---------- Cleanup ----------
    print("\n--- Cleanup: deleting test pending orders ---")
    for pid in pending_to_cleanup:
        if pid:
            cleanup_pending(token, pid)


if __name__ == "__main__":
    try:
        test_master_order_id()
    except Exception as e:
        print(f"\nFATAL: test driver crashed: {e!r}")
        FAILED.append(f"driver crash: {e!r}")

    print("\n========== SUMMARY ==========")
    print(f"PASSED: {len(PASSED)}")
    print(f"FAILED: {len(FAILED)}")
    if FAILED:
        print("\nFAILURES:")
        for f in FAILED:
            print(f"  - {f}")
        sys.exit(1)
    print("All assertions passed.")
