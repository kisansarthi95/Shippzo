"""
Backend test suite for the unified Analytics endpoint:
  GET /api/analytics/overview

Runs against the public preview URL via REACT_APP_BACKEND_URL / EXPO_PUBLIC_BACKEND_URL.
"""
import os
import sys
from typing import Optional

import requests

BASE = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "https://logistics-hub-740.preview.emergentagent.com").rstrip("/")
API = f"{BASE}/api"

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASS = "Admin@12345"
USER_EMAIL = "user2@test.com"
USER_PASS = "User@12345"

PASSED: list = []
FAILED: list = []


def t(name: str, cond: bool, detail: str = ""):
    if cond:
        PASSED.append(name)
        print(f"  PASS  {name}")
    else:
        FAILED.append((name, detail))
        print(f"  FAIL  {name}  -- {detail}")


def login(email: str, password: str) -> str:
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"Login failed for {email}: HTTP {r.status_code}: {r.text[:200]}")
    return r.json()["token"]


def get_overview(token: Optional[str], **params) -> requests.Response:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return requests.get(f"{API}/analytics/overview", headers=headers, params=params, timeout=30)


def main():
    print(f"Target API: {API}")
    print("\n[Login]")
    admin_token = login(ADMIN_EMAIL, ADMIN_PASS)
    user_token = login(USER_EMAIL, USER_PASS)
    print(f"  Admin token: {admin_token[:24]}...")
    print(f"  User  token: {user_token[:24]}...")

    # Test 1
    print("\n[Test 1 — Auth required]")
    r = requests.get(f"{API}/analytics/overview", timeout=20)
    t("1.1 GET /analytics/overview without token returns 401",
      r.status_code == 401, f"got {r.status_code}: {r.text[:200]}")

    # Test 2
    print("\n[Test 2 — scope=mine (regular user)]")
    r = get_overview(user_token)
    t("2.1 user GET returns 200", r.status_code == 200, f"{r.status_code}: {r.text[:200]}")
    if r.status_code == 200:
        d = r.json()
        t("2.2 scope == 'mine'", d.get("scope") == "mine", f"got {d.get('scope')}")
        kpi = d.get("kpi") or {}
        t("2.3 kpi.total >= 0", isinstance(kpi.get("total"), int) and kpi["total"] >= 0,
          f"kpi.total={kpi.get('total')!r}")
        t("2.4 kpi.delivered + kpi.pending == kpi.total",
          int(kpi.get("delivered", -1)) + int(kpi.get("pending", -1)) == int(kpi.get("total", -1)),
          f"d={kpi.get('delivered')} p={kpi.get('pending')} t={kpi.get('total')}")
        ships = d.get("shipments") or {}
        t("2.5 shipments.by_status is dict", isinstance(ships.get("by_status"), dict))
        fo = d.get("filter_options") or {}
        t("2.6 filter_options.couriers is list", isinstance(fo.get("couriers"), list))
        t("2.7 filter_options.statuses is list", isinstance(fo.get("statuses"), list))
        t("2.8 filter_options.states is list", isinstance(fo.get("states"), list))
        t("2.9 admin field NOT present (regular user)", "admin" not in d, f"keys={list(d.keys())}")
        trend = d.get("trend_30d")
        t("2.10 trend_30d has exactly 30 entries",
          isinstance(trend, list) and len(trend) == 30,
          f"len={len(trend) if isinstance(trend, list) else 'NA'}")
        if isinstance(trend, list) and trend:
            sample = trend[0]
            t("2.11 trend_30d entries have date+count keys",
              isinstance(sample, dict) and "date" in sample and "count" in sample,
              f"sample={sample}")

    # Test 3
    print("\n[Test 3 — scope=platform as regular user]")
    r = get_overview(user_token, scope="platform")
    t("3.1 returns 403", r.status_code == 403, f"got {r.status_code}: {r.text[:200]}")
    if r.status_code == 403:
        try:
            detail = r.json().get("detail", "")
        except Exception:
            detail = r.text
        t("3.2 detail mentions admin", "admin" in detail.lower(), f"detail={detail!r}")

    # Test 4
    print("\n[Test 4 — scope=mine as admin]")
    r = get_overview(admin_token)
    t("4.1 admin GET (default mine) returns 200", r.status_code == 200, f"{r.status_code}: {r.text[:200]}")
    if r.status_code == 200:
        d = r.json()
        t("4.2 scope == 'mine'", d.get("scope") == "mine")
        t("4.3 admin field NOT present in mine scope", "admin" not in d, f"keys={list(d.keys())}")

    # Test 5
    print("\n[Test 5 — scope=platform as admin]")
    r = get_overview(admin_token, scope="platform")
    t("5.1 returns 200", r.status_code == 200, f"{r.status_code}: {r.text[:200]}")
    if r.status_code == 200:
        d = r.json()
        t("5.2 scope == 'platform'", d.get("scope") == "platform")
        adm = d.get("admin") or {}
        t("5.3 admin field exists", isinstance(d.get("admin"), dict))
        if adm:
            users = adm.get("users") or {}
            t("5.4 admin.users.total is int", isinstance(users.get("total"), int))
            t("5.5 admin.users.today is int", isinstance(users.get("today"), int))
            t("5.6 admin.top_users is list", isinstance(adm.get("top_users"), list))
            t("5.7 admin.sla_open is int", isinstance(adm.get("sla_open"), int))

    # Test 6
    print("\n[Test 6 — range filter]")
    for rng in ("today", "7d", "90d", "all"):
        r = get_overview(admin_token, range=rng)
        t(f"6.{rng} 200", r.status_code == 200, f"{r.status_code}: {r.text[:200]}")
        if r.status_code == 200:
            d = r.json()
            t(f"6.{rng} echoes range", d.get("range") == rng)
            since = d.get("since")
            if rng == "all":
                t(f"6.{rng} since is null", since is None, f"got {since!r}")
            else:
                t(f"6.{rng} since is ISO string",
                  isinstance(since, str) and ("T" in since or "-" in since),
                  f"got {since!r}")

    # Test 7
    print("\n[Test 7 — filter combinations]")
    r = get_overview(admin_token, range="all")
    if r.status_code != 200:
        t("7.0 baseline fetch", False, f"{r.status_code}: {r.text[:200]}")
    else:
        base = r.json()
        opts = base.get("filter_options") or {}
        couriers = opts.get("couriers") or []
        statuses = opts.get("statuses") or []
        states = opts.get("states") or []
        print(f"  baseline filter_options: {len(couriers)} couriers, {len(statuses)} statuses, {len(states)} states")

        # 7a courier
        if couriers:
            c0 = couriers[0]
            r2 = get_overview(admin_token, range="all", courier=c0)
            t(f"7a courier={c0!r} 200", r2.status_code == 200, f"{r2.status_code}: {r2.text[:200]}")
            if r2.status_code == 200:
                d2 = r2.json()
                bc = (d2.get("shipments") or {}).get("by_courier") or []
                names = [x.get("name") for x in bc]
                t(f"7a by_courier only contains {c0!r}",
                  all(n == c0 for n in names) if names else True, f"names={names}")
                t("7a filters.courier echoed", d2.get("filters", {}).get("courier") == c0)
        else:
            print("  (skipped 7a — no couriers)")

        # 7b status
        if statuses:
            s0 = statuses[0]
            r3 = get_overview(admin_token, range="all", status=s0)
            t(f"7b status={s0!r} 200", r3.status_code == 200, f"{r3.status_code}: {r3.text[:200]}")
            if r3.status_code == 200:
                d3 = r3.json()
                bs = (d3.get("shipments") or {}).get("by_status") or {}
                t(f"7b by_status only key is {s0!r}",
                  set(bs.keys()) <= {s0} if bs else True, f"keys={list(bs.keys())}")
        else:
            print("  (skipped 7b — no statuses)")

        # 7c payment COD
        r4 = get_overview(admin_token, range="all", payment_mode="COD")
        t("7c payment_mode=COD 200", r4.status_code == 200, f"{r4.status_code}: {r4.text[:200]}")
        if r4.status_code == 200:
            d4 = r4.json()
            bp = (d4.get("shipments") or {}).get("by_payment") or {}
            t("7c by_payment.PREPAID == 0", int(bp.get("PREPAID", -1)) == 0, f"by_payment={bp}")

        # 7d state
        if states:
            st0 = states[0]
            r5 = get_overview(admin_token, range="all", state=st0)
            t(f"7d state={st0!r} 200", r5.status_code == 200, f"{r5.status_code}: {r5.text[:200]}")
            if r5.status_code == 200:
                d5 = r5.json()
                bs_state = (d5.get("shipments") or {}).get("by_state") or []
                names = [x.get("name") for x in bs_state]
                t(f"7d by_state only contains {st0!r}",
                  all(n.lower() == st0.lower() for n in names) if names else True,
                  f"names={names}")
        else:
            print("  (skipped 7d — no states)")

    # Test 8
    print("\n[Test 8 — revenue field validity]")
    r = get_overview(admin_token, range="all")
    if r.status_code == 200:
        kpi = r.json().get("kpi") or {}
        rev = int(kpi.get("revenue") or 0)
        rev_cod = int(kpi.get("revenue_cod") or 0)
        rev_pre = int(kpi.get("revenue_prepaid") or 0)
        diff = rev - rev_cod - rev_pre
        t("8.1 revenue == cod + prepaid + Other (diff>=0)",
          diff >= 0, f"rev={rev} cod={rev_cod} pre={rev_pre} diff(Other)={diff}")
        for k in ("total", "delivered", "pending", "revenue", "revenue_cod", "revenue_prepaid"):
            v = kpi.get(k)
            t(f"8.2 kpi.{k} is int (not None)", isinstance(v, int),
              f"kpi.{k}={v!r} type={type(v).__name__}")

    # Test 9
    print("\n[Test 9 — legacy /admin/analytics/overview]")
    r = requests.get(f"{API}/admin/analytics/overview",
                     headers={"Authorization": f"Bearer {admin_token}"}, timeout=20)
    t("9.1 admin → 200", r.status_code == 200, f"{r.status_code}: {r.text[:200]}")
    r = requests.get(f"{API}/admin/analytics/overview",
                     headers={"Authorization": f"Bearer {user_token}"}, timeout=20)
    t("9.2 regular user → 403", r.status_code == 403, f"{r.status_code}: {r.text[:200]}")

    print("\n" + "=" * 60)
    print(f"PASSED: {len(PASSED)}")
    print(f"FAILED: {len(FAILED)}")
    if FAILED:
        print("\nFAILED CASES:")
        for n, d in FAILED:
            print(f"  - {n}: {d}")
        sys.exit(1)
    print("ALL OK")


if __name__ == "__main__":
    main()
