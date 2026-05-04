"""
Phase 2.5 Excel Download Endpoint — Retest
Tests for /api/me/reports/courier-billing/excel after bug fixes.
"""
import sys
import requests

BASE = "https://logistics-hub-740.preview.emergentagent.com/api"
EMAIL = "user2@test.com"
PASSWORD = "User@12345"

XLSX_CT = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def login() -> str:
    r = requests.post(f"{BASE}/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=20)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    tok = r.json().get("token")
    assert tok, "no token in login response"
    return tok


def is_xlsx(body: bytes) -> bool:
    return body[:2] == b"PK"


def main() -> int:
    failures = []
    passed = []

    print("--- 1. LOGIN ---")
    token = login()
    print(f"  token len={len(token)}")
    passed.append("login as user2@test.com")

    # 2. Excel via Authorization header
    print("\n--- 2. Excel via Authorization header ---")
    r = requests.get(
        f"{BASE}/me/reports/courier-billing/excel",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    print(f"  status={r.status_code} ct={r.headers.get('content-type')} len={len(r.content)}")
    print(f"  first4={r.content[:4]!r}")
    if r.status_code == 200 and r.headers.get("content-type", "").startswith(XLSX_CT) and is_xlsx(r.content):
        passed.append("Excel via Authorization header → 200 + PK xlsx")
    else:
        failures.append(f"Excel via Authorization header: status={r.status_code} ct={r.headers.get('content-type')} body_head={r.content[:80]!r}")

    # 3. Excel via ?token= fallback (browser-style)
    print("\n--- 3. Excel via ?token= fallback ---")
    r = requests.get(
        f"{BASE}/me/reports/courier-billing/excel",
        params={"token": token},
        timeout=30,
    )
    print(f"  status={r.status_code} ct={r.headers.get('content-type')} len={len(r.content)}")
    print(f"  first4={r.content[:4]!r}")
    if r.status_code == 200 and r.headers.get("content-type", "").startswith(XLSX_CT) and is_xlsx(r.content):
        passed.append("Excel via ?token= fallback → 200 + PK xlsx")
    else:
        failures.append(f"Excel via ?token= fallback: status={r.status_code} ct={r.headers.get('content-type')} body_head={r.content[:80]!r}")

    # 4. No auth at all → 401
    print("\n--- 4. Excel with NO auth ---")
    r = requests.get(f"{BASE}/me/reports/courier-billing/excel", timeout=20)
    print(f"  status={r.status_code} body={r.text[:120]}")
    if r.status_code == 401:
        passed.append("No auth → 401")
    else:
        failures.append(f"No auth expected 401, got {r.status_code} body={r.text[:200]}")

    # 5. Invalid token → 401
    print("\n--- 5. Excel with invalid ?token=garbage ---")
    r = requests.get(f"{BASE}/me/reports/courier-billing/excel", params={"token": "garbage"}, timeout=20)
    print(f"  status={r.status_code} body={r.text[:120]}")
    if r.status_code == 401:
        passed.append("Invalid ?token=garbage → 401")
    else:
        failures.append(f"Invalid token expected 401, got {r.status_code} body={r.text[:200]}")

    # 6a. Range last_month
    print("\n--- 6a. Excel range=last_month ---")
    r = requests.get(
        f"{BASE}/me/reports/courier-billing/excel",
        params={"range": "last_month", "token": token},
        timeout=30,
    )
    print(f"  status={r.status_code} ct={r.headers.get('content-type')} len={len(r.content)}")
    if r.status_code == 200 and r.headers.get("content-type", "").startswith(XLSX_CT) and is_xlsx(r.content):
        passed.append("range=last_month → 200 + PK xlsx")
    else:
        failures.append(f"range=last_month: status={r.status_code} ct={r.headers.get('content-type')} body_head={r.content[:120]!r}")

    # 6b. Range custom
    print("\n--- 6b. Excel range=custom 2026-01-01 to 2026-12-31 ---")
    r = requests.get(
        f"{BASE}/me/reports/courier-billing/excel",
        params={"range": "custom", "from": "2026-01-01", "to": "2026-12-31", "token": token},
        timeout=30,
    )
    print(f"  status={r.status_code} ct={r.headers.get('content-type')} len={len(r.content)}")
    if r.status_code == 200 and r.headers.get("content-type", "").startswith(XLSX_CT) and is_xlsx(r.content):
        passed.append("range=custom → 200 + PK xlsx")
    else:
        failures.append(f"range=custom: status={r.status_code} ct={r.headers.get('content-type')} body_head={r.content[:120]!r}")

    # 7. courier_id filter — first fetch a valid courier_id
    print("\n--- 7. Excel with courier_id filter ---")
    rc = requests.get(f"{BASE}/couriers", headers={"Authorization": f"Bearer {token}"}, timeout=20)
    if rc.status_code == 200:
        couriers = rc.json() or []
        if couriers:
            cid = couriers[0]["id"]
            print(f"  using courier_id={cid} ({couriers[0].get('name')})")
            r = requests.get(
                f"{BASE}/me/reports/courier-billing/excel",
                params={"courier_id": cid, "token": token},
                timeout=30,
            )
            print(f"  status={r.status_code} ct={r.headers.get('content-type')} len={len(r.content)}")
            if r.status_code == 200 and r.headers.get("content-type", "").startswith(XLSX_CT) and is_xlsx(r.content):
                passed.append(f"courier_id filter → 200 + PK xlsx")
            else:
                failures.append(f"courier_id filter: status={r.status_code} ct={r.headers.get('content-type')} body_head={r.content[:120]!r}")
        else:
            failures.append("courier_id filter: no couriers found for user2 (cannot test)")
    else:
        failures.append(f"courier_id filter: GET /couriers failed status={rc.status_code}")

    # 8. JSON endpoint regression check
    print("\n--- 8. JSON endpoint regression check ---")
    r = requests.get(
        f"{BASE}/me/reports/courier-billing",
        headers={"Authorization": f"Bearer {token}"},
        timeout=20,
    )
    print(f"  status={r.status_code} ct={r.headers.get('content-type')}")
    if r.status_code == 200:
        try:
            j = r.json()
            assert "couriers" in j and "grand_total" in j and "period" in j
            print(f"  shape OK: period={j['period'].get('label')} grand={j['grand_total']}")
            passed.append("JSON endpoint /me/reports/courier-billing → 200 + valid shape")
        except Exception as e:
            failures.append(f"JSON endpoint: shape parse failed {e}")
    else:
        failures.append(f"JSON endpoint regression: status={r.status_code} body={r.text[:200]}")

    # Summary
    print("\n" + "=" * 60)
    print(f"PASSED: {len(passed)}")
    for p in passed:
        print(f"  ✓ {p}")
    print(f"\nFAILED: {len(failures)}")
    for f in failures:
        print(f"  ✗ {f}")
    print("=" * 60)
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
