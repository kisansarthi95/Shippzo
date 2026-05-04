"""
Backend test for bulk-message endpoints Body parameter fix (2026-04-30)
Focus:
  - POST /api/me/bulk-message/mark-sent
  - POST /api/me/bulk-message/reset
The bug: payload: BulkMarkSentRequest (nested Pydantic class inside init())
was interpreted by FastAPI as a QUERY param → 422 "query.payload: Field required".
Fix: added `= Body(...)` marker on both endpoints.
"""
import json
import sys

import requests

BASE = "https://logistics-hub-740.preview.emergentagent.com/api"
USER_EMAIL = "user2@test.com"
USER_PASSWORD = "User@12345"


def _ok(label, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail else ""))
    return cond


results = {"passed": 0, "failed": 0, "failures": []}


def check(label, cond, detail=""):
    if _ok(label, cond, detail):
        results["passed"] += 1
    else:
        results["failed"] += 1
        results["failures"].append(f"{label} :: {detail}")


def login():
    r = requests.post(
        f"{BASE}/auth/login",
        json={"email": USER_EMAIL, "password": USER_PASSWORD},
        timeout=30,
    )
    assert r.status_code == 200, f"login failed {r.status_code}: {r.text}"
    tok = r.json().get("token")
    assert tok, "no token returned"
    return tok


def headers(tok):
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}


def main():
    print("=" * 78)
    print("TEST 1: Auth — POST both endpoints without token → 401")
    print("=" * 78)
    r1 = requests.post(
        f"{BASE}/me/bulk-message/mark-sent",
        json={"ttype": "shipment_sent", "shipment_ids": ["x"]},
        timeout=30,
    )
    check(
        "mark-sent without token → 401/403",
        r1.status_code in (401, 403),
        f"got {r1.status_code}: {r1.text[:200]}",
    )
    r2 = requests.post(
        f"{BASE}/me/bulk-message/reset",
        json={"ttype": "shipment_sent", "shipment_ids": ["x"]},
        timeout=30,
    )
    check(
        "reset without token → 401/403",
        r2.status_code in (401, 403),
        f"got {r2.status_code}: {r2.text[:200]}",
    )

    print()
    print("=" * 78)
    print("TEST 2: Login user2@test.com")
    print("=" * 78)
    tok = login()
    H = headers(tok)
    check("login ok (token obtained)", bool(tok), f"token_len={len(tok)}")

    print()
    print("=" * 78)
    print("TEST 3: GET /me/bulk-message/eligible?ttype=shipment_sent")
    print("=" * 78)
    r3 = requests.get(
        f"{BASE}/me/bulk-message/eligible?ttype=shipment_sent",
        headers=H,
        timeout=30,
    )
    check("eligible 200", r3.status_code == 200, f"{r3.status_code}: {r3.text[:200]}")
    j3 = r3.json() if r3.status_code == 200 else {}
    ships = j3.get("shipments") or []
    check("eligible returns 'shipments' list", isinstance(ships, list),
          f"type={type(ships).__name__}, count={len(ships)}")
    print(f"  → {len(ships)} eligible shipment(s) returned, counts={j3.get('counts')}")

    target_id = None
    if ships:
        target_id = ships[0].get("id")
        print(f"  → target shipment id: {target_id}")

    print()
    print("=" * 78)
    print("TEST 4: CRITICAL — mark-sent happy path (was returning 422)")
    print("=" * 78)
    if not target_id:
        # Fall back to the first shipment of any kind
        rfallback = requests.get(f"{BASE}/shipments", headers=H, timeout=30)
        if rfallback.status_code == 200:
            items = rfallback.json()
            if isinstance(items, list) and items:
                target_id = items[0]["id"]
                print(f"  ⚠ no eligible shipment_sent rows; using fallback shipment id={target_id}")

    if not target_id:
        check("mark-sent 200 (body param fix)", False,
              "no shipment id available to test with")
    else:
        r4 = requests.post(
            f"{BASE}/me/bulk-message/mark-sent",
            headers=H,
            json={"ttype": "shipment_sent", "shipment_ids": [target_id]},
            timeout=30,
        )
        print(f"  raw: status={r4.status_code}, body={r4.text[:300]}")
        check(
            "mark-sent NOT 422 query.payload error (Body fix verified)",
            r4.status_code != 422,
            f"{r4.status_code}: {r4.text[:300]}",
        )
        check(
            "mark-sent 200",
            r4.status_code == 200,
            f"{r4.status_code}: {r4.text[:300]}",
        )
        if r4.status_code == 200:
            j4 = r4.json()
            keys_ok = all(k in j4 for k in ("ttype", "updated", "skipped",
                                            "updated_ids", "skipped_ids"))
            check("mark-sent response has ttype/updated/skipped/updated_ids/skipped_ids",
                  keys_ok, f"keys={list(j4.keys())}")
            check("ttype echoed as shipment_sent",
                  j4.get("ttype") == "shipment_sent",
                  f"got {j4.get('ttype')}")
            updated_first = j4.get("updated", -1)
            skipped_first = j4.get("skipped", -1)
            print(f"  → first call: updated={updated_first}, skipped={skipped_first}")

            print()
            print("=" * 78)
            print("TEST 5: Idempotent repeat — same body → updated=0, skipped=1")
            print("=" * 78)
            r5 = requests.post(
                f"{BASE}/me/bulk-message/mark-sent",
                headers=H,
                json={"ttype": "shipment_sent", "shipment_ids": [target_id]},
                timeout=30,
            )
            check("repeat 200", r5.status_code == 200,
                  f"{r5.status_code}: {r5.text[:200]}")
            if r5.status_code == 200:
                j5 = r5.json()
                print(f"  → repeat: updated={j5.get('updated')}, skipped={j5.get('skipped')}")
                # If first call itself had 0 updated (because no matching doc), skipped will also be 0
                if updated_first >= 1:
                    check("repeat updated=0", j5.get("updated") == 0,
                          f"got updated={j5.get('updated')}")
                    check("repeat skipped>=1 (idempotent same-day block)",
                          j5.get("skipped") >= 1,
                          f"got skipped={j5.get('skipped')}")
                else:
                    print("  ⚠ first call updated=0 (target not owned by user2 or wrong status); idempotency check SKIPPED")

    print()
    print("=" * 78)
    print("TEST 6: Bad ttype → 400 'Unknown bulk template type'")
    print("=" * 78)
    r6 = requests.post(
        f"{BASE}/me/bulk-message/mark-sent",
        headers=H,
        json={"ttype": "wrong_type", "shipment_ids": ["x"]},
        timeout=30,
    )
    check("bad ttype 400", r6.status_code == 400,
          f"{r6.status_code}: {r6.text[:200]}")
    if r6.status_code == 400:
        detail = r6.json().get("detail") or ""
        check("detail mentions 'Unknown bulk template type'",
              "Unknown bulk template type" in detail,
              f"detail={detail!r}")

    print()
    print("=" * 78)
    print("TEST 7: Empty body {} → 422 validation error on ttype")
    print("=" * 78)
    r7 = requests.post(
        f"{BASE}/me/bulk-message/mark-sent",
        headers=H,
        json={},
        timeout=30,
    )
    check("empty body → 422", r7.status_code == 422,
          f"{r7.status_code}: {r7.text[:200]}")
    if r7.status_code == 422:
        body = r7.json()
        # Confirm error is on BODY not QUERY (the whole point of the fix)
        loc_entries = []
        for err in body.get("detail") or []:
            loc = err.get("loc") or []
            loc_entries.append(loc)
        print(f"  → validation loc entries: {loc_entries}")
        all_body = all(
            (len(loc) > 0 and loc[0] == "body") for loc in loc_entries
        )
        check("422 error loc starts with 'body' (NOT 'query')",
              all_body,
              f"locs={loc_entries}")

    print()
    print("=" * 78)
    print("TEST 8: /reset endpoint — body accepted (not 422)")
    print("=" * 78)
    reset_id = target_id or "nonexistent-id"
    r8 = requests.post(
        f"{BASE}/me/bulk-message/reset",
        headers=H,
        json={"ttype": "shipment_sent", "shipment_ids": [reset_id]},
        timeout=30,
    )
    print(f"  raw: status={r8.status_code}, body={r8.text[:300]}")
    check("reset NOT 422 query.payload error (Body fix verified)",
          r8.status_code != 422,
          f"{r8.status_code}: {r8.text[:300]}")
    check("reset 200", r8.status_code == 200,
          f"{r8.status_code}: {r8.text[:300]}")
    if r8.status_code == 200:
        j8 = r8.json()
        check("reset response has 'updated'", "updated" in j8,
              f"keys={list(j8.keys())}")

    print()
    print("=" * 78)
    print("TEST 9: All 5 ttypes accepted by mark-sent (no 422)")
    print("=" * 78)
    sample_id = target_id or "nonexistent-id"
    ttypes = ["shipment_sent", "dispatch_confirmation",
              "delivery_confirmation", "delivery_done", "feedback_request"]
    for tt in ttypes:
        r = requests.post(
            f"{BASE}/me/bulk-message/mark-sent",
            headers=H,
            json={"ttype": tt, "shipment_ids": [sample_id]},
            timeout=30,
        )
        ok = r.status_code == 200
        check(f"ttype={tt!r} → 200 (no 422)", ok,
              f"{r.status_code}: {r.text[:150]}")
        if ok:
            j = r.json()
            check(f"ttype={tt!r} response echoes ttype",
                  j.get("ttype") == tt,
                  f"got {j.get('ttype')}")

    print()
    print("=" * 78)
    print("TEST 10: Regression — eligible endpoint still works")
    print("=" * 78)
    r10 = requests.get(
        f"{BASE}/me/bulk-message/eligible?ttype=shipment_sent",
        headers=H,
        timeout=30,
    )
    check("eligible regression 200", r10.status_code == 200,
          f"{r10.status_code}: {r10.text[:200]}")
    if r10.status_code == 200:
        j10 = r10.json()
        check("eligible shape keys present",
              all(k in j10 for k in ("shipments", "counts", "ttype")),
              f"keys={list(j10.keys())}")

    print()
    print("=" * 78)
    print(f"RESULT: {results['passed']} passed, {results['failed']} failed")
    print("=" * 78)
    if results["failures"]:
        print("FAILURES:")
        for f in results["failures"]:
            print(f"  - {f}")
    return 0 if results["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
