"""
Backend tests for Phase 2.5 Courier Billing Report endpoints.

Routes under test:
  - GET /api/me/reports/courier-billing
  - GET /api/me/reports/courier-billing/excel
"""
from __future__ import annotations

import json
import os
import sys
import traceback

import requests

BASE = os.environ.get(
    "BACKEND_BASE",
    "https://logistics-hub-740.preview.emergentagent.com/api",
)

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASS  = "Admin@12345"
USER2_EMAIL = "user2@test.com"
USER2_PASS  = "User@12345"

_results = []  # list[(label, ok, detail)]


def record(label: str, ok: bool, detail: str = "") -> None:
    _results.append((label, ok, detail))
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {label}" + (f" :: {detail}" if detail else ""))


def login(email: str, password: str) -> str:
    r = requests.post(
        f"{BASE}/auth/login",
        json={"email": email, "password": password},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["token"]


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ──────────────────────────────────────────────────────────────────
# TEST 1 — Auth required
# ──────────────────────────────────────────────────────────────────

def t1_auth_required() -> None:
    for path in [
        "/me/reports/courier-billing",
        "/me/reports/courier-billing/excel",
    ]:
        r = requests.get(f"{BASE}{path}", timeout=30)
        record(
            f"T1 no-token {path} → 401",
            r.status_code == 401,
            f"got {r.status_code}",
        )


# ──────────────────────────────────────────────────────────────────
# TEST 2 — default (this_month) response shape
# ──────────────────────────────────────────────────────────────────

REQ_TOP = {"period", "couriers", "grand_total", "rows_without_rate"}
REQ_PERIOD = {"from", "to", "label"}
REQ_COURIER = {
    "courier_id", "courier_name", "total_shipments", "total_charges",
    "cod", "prepaid", "other", "by_package_type", "by_state", "shipments",
}
REQ_GRAND = {"shipments", "charges"}


def t2_default_shape(user_token: str) -> dict:
    r = requests.get(
        f"{BASE}/me/reports/courier-billing",
        headers=auth_headers(user_token),
        timeout=30,
    )
    record("T2 default this_month 200", r.status_code == 200, f"got {r.status_code}")
    if r.status_code != 200:
        return {}

    data = r.json()
    record(
        "T2 top-level keys present",
        REQ_TOP.issubset(data.keys()),
        f"missing={REQ_TOP - set(data.keys())}",
    )

    period = data.get("period") or {}
    record(
        "T2 period keys present",
        REQ_PERIOD.issubset(period.keys()),
        f"missing={REQ_PERIOD - set(period.keys())}",
    )
    record(
        "T2 period.label non-empty string",
        isinstance(period.get("label"), str) and len(period["label"]) > 0,
        f"label={period.get('label')!r}",
    )

    grand = data.get("grand_total") or {}
    record(
        "T2 grand_total keys present",
        REQ_GRAND.issubset(grand.keys()),
        f"missing={REQ_GRAND - set(grand.keys())}",
    )

    record(
        "T2 rows_without_rate is int >=0",
        isinstance(data.get("rows_without_rate"), int) and data["rows_without_rate"] >= 0,
        f"value={data.get('rows_without_rate')!r}",
    )

    couriers = data.get("couriers") or []
    record(
        "T2 couriers is list",
        isinstance(couriers, list),
        f"type={type(couriers).__name__}",
    )

    if couriers:
        c0 = couriers[0]
        missing = REQ_COURIER - set(c0.keys())
        record(
            "T2 courier[0] has all required keys",
            not missing,
            f"missing={missing}",
        )
        record(
            "T2 courier[0].cod has count+amount",
            {"count", "amount"}.issubset(c0.get("cod", {}).keys()),
            f"cod={c0.get('cod')}",
        )
        record(
            "T2 courier[0].prepaid has count+amount",
            {"count", "amount"}.issubset(c0.get("prepaid", {}).keys()),
            "",
        )
        record(
            "T2 courier[0].other has count+amount",
            {"count", "amount"}.issubset(c0.get("other", {}).keys()),
            "",
        )
        record(
            "T2 courier[0].by_package_type is list",
            isinstance(c0.get("by_package_type"), list),
            "",
        )
        record(
            "T2 courier[0].by_state is list",
            isinstance(c0.get("by_state"), list),
            "",
        )
        record(
            "T2 courier[0].shipments is list",
            isinstance(c0.get("shipments"), list),
            "",
        )
        if c0.get("shipments"):
            s0 = c0["shipments"][0]
            req_ship = {
                "id", "tracking_id", "order_id", "date", "customer_name",
                "city", "state", "weight", "rate", "payment_mode", "status",
                "package_type", "variant_name",
            }
            missing_s = req_ship - set(s0.keys())
            record(
                "T2 shipment row has all required keys",
                not missing_s,
                f"missing={missing_s}",
            )
    else:
        print("       [info] user2 has no shipments in this_month window — skipping courier-shape assertions")

    return data


# ──────────────────────────────────────────────────────────────────
# TEST 3 — all range chips
# ──────────────────────────────────────────────────────────────────

def t3_range_chips(user_token: str) -> None:
    for rng in ["this_week", "last_week", "this_month", "last_month", "last_30"]:
        r = requests.get(
            f"{BASE}/me/reports/courier-billing",
            params={"range": rng},
            headers=auth_headers(user_token),
            timeout=30,
        )
        ok = r.status_code == 200
        record(f"T3 range={rng} → 200", ok, f"got {r.status_code}")
        if ok:
            label = (r.json().get("period") or {}).get("label")
            record(
                f"T3 range={rng} period.label present",
                bool(label),
                f"label={label!r}",
            )


# ──────────────────────────────────────────────────────────────────
# TEST 4 — custom range
# ──────────────────────────────────────────────────────────────────

def t4_custom_range(user_token: str) -> None:
    r = requests.get(
        f"{BASE}/me/reports/courier-billing",
        params={
            "range": "custom",
            "from":  "2026-01-01",
            "to":    "2026-12-31",
        },
        headers=auth_headers(user_token),
        timeout=30,
    )
    record("T4 custom range 200", r.status_code == 200, f"got {r.status_code}")


# ──────────────────────────────────────────────────────────────────
# TEST 5 — bad custom range → 400
# ──────────────────────────────────────────────────────────────────

def t5_bad_custom_range(user_token: str) -> None:
    r = requests.get(
        f"{BASE}/me/reports/courier-billing",
        params={"range": "custom", "from": "garbage"},
        headers=auth_headers(user_token),
        timeout=30,
    )
    # Missing `to` means the custom branch won't trigger (falls through to default),
    # so we also try with bad `from` + valid `to` to force the ISO parse failure.
    r2 = requests.get(
        f"{BASE}/me/reports/courier-billing",
        params={"range": "custom", "from": "garbage", "to": "2026-12-31"},
        headers=auth_headers(user_token),
        timeout=30,
    )
    record(
        "T5 bad custom range → 400",
        r2.status_code == 400,
        f"got {r2.status_code} body={r2.text[:200]}",
    )


# ──────────────────────────────────────────────────────────────────
# TEST 6 — filter by courier_id
# ──────────────────────────────────────────────────────────────────

def t6_courier_filter(user_token: str, default_data: dict) -> None:
    couriers = default_data.get("couriers") or []
    if not couriers:
        record("T6 courier_id filter (skipped — no couriers)", True, "no shipments to filter")
        return

    # Prefer a courier_id that is non-empty
    cid = None
    target_name = None
    for c in couriers:
        if c.get("courier_id"):
            cid = c["courier_id"]
            target_name = c["courier_name"]
            break
    if not cid:
        record("T6 courier_id filter (skipped — no courier_id values)", True, "")
        return

    r = requests.get(
        f"{BASE}/me/reports/courier-billing",
        params={"courier_id": cid},
        headers=auth_headers(user_token),
        timeout=30,
    )
    ok = r.status_code == 200
    record("T6 filter 200", ok, f"got {r.status_code}")
    if not ok:
        return
    data = r.json()
    cs = data.get("couriers") or []
    record(
        "T6 filter returns <=1 courier",
        len(cs) <= 1,
        f"count={len(cs)}",
    )
    if cs:
        record(
            "T6 filter returns the requested courier",
            cs[0].get("courier_id") == cid,
            f"got {cs[0].get('courier_id')} vs {cid}",
        )


# ──────────────────────────────────────────────────────────────────
# TEST 7 — rows_without_rate counter
# ──────────────────────────────────────────────────────────────────

def t7_rows_without_rate(default_data: dict) -> None:
    val = default_data.get("rows_without_rate")
    record(
        "T7 rows_without_rate is non-negative int",
        isinstance(val, int) and val >= 0,
        f"value={val}",
    )


# ──────────────────────────────────────────────────────────────────
# TEST 8 — Excel via Authorization header
# ──────────────────────────────────────────────────────────────────

def t8_excel_header(user_token: str) -> None:
    r = requests.get(
        f"{BASE}/me/reports/courier-billing/excel",
        headers=auth_headers(user_token),
        timeout=60,
    )
    ok = r.status_code == 200
    record("T8 Excel header 200", ok, f"got {r.status_code}")
    if not ok:
        return
    ct = r.headers.get("content-type", "")
    record(
        "T8 Excel content-type xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" in ct,
        f"ct={ct!r}",
    )
    body = r.content
    record(
        "T8 Excel body starts with PK",
        body[:2] == b"PK",
        f"first bytes={body[:4]!r}",
    )
    record(
        "T8 Excel body non-trivial size",
        len(body) > 500,
        f"len={len(body)}",
    )


# ──────────────────────────────────────────────────────────────────
# TEST 9 — Excel via ?token= fallback
# ──────────────────────────────────────────────────────────────────

def t9_excel_query_token(user_token: str) -> None:
    r = requests.get(
        f"{BASE}/me/reports/courier-billing/excel",
        params={"token": user_token},
        timeout=60,
    )
    ok = r.status_code == 200
    record("T9 Excel ?token= 200", ok, f"got {r.status_code} body={r.text[:200] if not ok else ''}")
    if not ok:
        return
    ct = r.headers.get("content-type", "")
    record(
        "T9 Excel ?token= content-type xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" in ct,
        f"ct={ct!r}",
    )
    body = r.content
    record(
        "T9 Excel ?token= body starts with PK",
        body[:2] == b"PK",
        "",
    )


# ──────────────────────────────────────────────────────────────────
# TEST 10 — Excel invalid token
# ──────────────────────────────────────────────────────────────────

def t10_excel_invalid_token() -> None:
    r = requests.get(
        f"{BASE}/me/reports/courier-billing/excel",
        params={"token": "garbage"},
        timeout=30,
    )
    ok = r.status_code == 401
    record("T10 Excel invalid token → 401", ok, f"got {r.status_code}")
    if ok:
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        record(
            "T10 Excel invalid token detail 'Auth required'",
            body.get("detail") == "Auth required",
            f"detail={body.get('detail')!r}",
        )


# ──────────────────────────────────────────────────────────────────
# TEST 11 — grand total math
# ──────────────────────────────────────────────────────────────────

def t11_grand_total(default_data: dict) -> None:
    couriers = default_data.get("couriers") or []
    sum_ships = sum((c.get("total_shipments") or 0) for c in couriers)
    sum_charges = sum((c.get("total_charges") or 0) for c in couriers)
    grand = default_data.get("grand_total") or {}
    record(
        "T11 sum(couriers.total_shipments) == grand_total.shipments",
        sum_ships == grand.get("shipments"),
        f"sum={sum_ships} vs {grand.get('shipments')}",
    )
    record(
        "T11 sum(couriers.total_charges) == grand_total.charges (±0.01)",
        abs(sum_charges - (grand.get("charges") or 0)) < 0.01,
        f"sum={sum_charges} vs {grand.get('charges')}",
    )


# ──────────────────────────────────────────────────────────────────
# Runner
# ──────────────────────────────────────────────────────────────────

def main() -> int:
    print(f"=== Running courier-billing report tests against {BASE} ===")
    try:
        user2_token = login(USER2_EMAIL, USER2_PASS)
        print(f"[info] logged in as {USER2_EMAIL}")
    except Exception as exc:
        print(f"FATAL: login failed: {exc}")
        traceback.print_exc()
        return 1

    t1_auth_required()
    default_data = t2_default_shape(user2_token)
    t3_range_chips(user2_token)
    t4_custom_range(user2_token)
    t5_bad_custom_range(user2_token)
    t6_courier_filter(user2_token, default_data)
    t7_rows_without_rate(default_data)
    t8_excel_header(user2_token)
    t9_excel_query_token(user2_token)
    t10_excel_invalid_token()
    t11_grand_total(default_data)

    # Also try with admin since admin has ~50 legacy shipments — good for
    # rows_without_rate coverage.
    try:
        admin_token = login(ADMIN_EMAIL, ADMIN_PASS)
        r = requests.get(
            f"{BASE}/me/reports/courier-billing",
            params={"range": "last_30"},
            headers=auth_headers(admin_token),
            timeout=30,
        )
        record("Extra admin last_30 200", r.status_code == 200, f"got {r.status_code}")
        if r.status_code == 200:
            d = r.json()
            record(
                "Extra admin rows_without_rate int",
                isinstance(d.get("rows_without_rate"), int),
                f"value={d.get('rows_without_rate')}",
            )
            print(f"       [info] admin couriers={len(d.get('couriers') or [])} "
                  f"rows_without_rate={d.get('rows_without_rate')} "
                  f"grand_total={d.get('grand_total')}")
    except Exception as exc:
        print(f"[info] admin cross-check skipped: {exc}")

    # Summary
    print()
    print("=" * 60)
    total = len(_results)
    passed = sum(1 for _, ok, _ in _results if ok)
    print(f"SUMMARY: {passed}/{total} passed")
    failures = [x for x in _results if not x[1]]
    if failures:
        print(f"\n{len(failures)} FAILURE(S):")
        for label, _, detail in failures:
            print(f"  ✗ {label} :: {detail}")
    return 0 if passed == total else 2


if __name__ == "__main__":
    sys.exit(main())
