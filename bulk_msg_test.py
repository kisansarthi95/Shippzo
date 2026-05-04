"""Focused retest for /api/me/bulk-message/* endpoints.

Verifies the fix where BulkMarkSentRequest was moved to module scope
in /app/backend/routers/messaging.py to avoid Pydantic v2 TypeAdapter
500 errors that previously blocked the bulk message flow.
"""
import json
import os
import sys
import requests

BASE = "https://logistics-hub-740.preview.emergentagent.com/api"
EMAIL = "user2@test.com"
PASSWORD = "User@12345"

TTYPES = [
    "shipment_sent",
    "dispatch_confirmation",
    "delivery_confirmation",
    "delivery_done",
    "feedback_request",
]

results = []


def rec(name, ok, detail=""):
    results.append((name, ok, detail))
    icon = "✅" if ok else "❌"
    print(f"{icon} {name}{(' — ' + detail) if detail else ''}")


def login() -> str:
    r = requests.post(f"{BASE}/auth/login",
                      json={"email": EMAIL, "password": PASSWORD},
                      timeout=30)
    r.raise_for_status()
    return r.json()["token"]


def main() -> int:
    try:
        tok = login()
        rec("login user2@test.com", True)
    except Exception as e:
        rec("login user2@test.com", False, str(e))
        return 1
    H = {"Authorization": f"Bearer {tok}"}

    # 2. GET eligible (shipment_sent) ------------------------------------
    r = requests.get(f"{BASE}/me/bulk-message/eligible",
                     params={"ttype": "shipment_sent"},
                     headers=H, timeout=30)
    rec(f"GET eligible (shipment_sent) status=200", r.status_code == 200,
        f"got {r.status_code}: {r.text[:200]}")
    if r.status_code != 200:
        return 1
    body = r.json()
    rec("eligible response has 'shipments' list",
        isinstance(body.get("shipments"), list),
        f"keys={list(body.keys())}")
    rec("eligible response has 'counts' dict",
        isinstance(body.get("counts"), dict))
    ships = body.get("shipments") or []
    print(f"   Found {len(ships)} eligible shipments for shipment_sent")
    if not ships:
        rec("at least one eligible shipment exists for shipment_sent",
            False, "user2 fixture should have demo shipments in Pending status")
        # We can still continue with later tests using a fake id to verify endpoint contract.
    first_id = ships[0]["id"] if ships else None

    # 3. POST mark-sent (CRITICAL — was 500 before fix) ------------------
    if first_id:
        body3 = {"ttype": "shipment_sent", "shipment_ids": [first_id]}
        r = requests.post(f"{BASE}/me/bulk-message/mark-sent",
                          json=body3, headers=H, timeout=30)
        rec("POST mark-sent — NOT 500", r.status_code != 500,
            f"got {r.status_code}: {r.text[:300]}")
        rec("POST mark-sent — NOT 422", r.status_code != 422,
            f"got {r.status_code}: {r.text[:300]}")
        rec("POST mark-sent status=200", r.status_code == 200,
            f"got {r.status_code}: {r.text[:300]}")
        if r.status_code == 200:
            jb = r.json()
            for k in ("ttype", "updated", "skipped", "updated_ids", "skipped_ids"):
                rec(f"mark-sent response has '{k}'", k in jb,
                    f"keys={list(jb.keys())}")
            print(f"   First call: updated={jb.get('updated')} skipped={jb.get('skipped')}")

            # 4. Idempotency — same request again ------------------------
            r2 = requests.post(f"{BASE}/me/bulk-message/mark-sent",
                               json=body3, headers=H, timeout=30)
            rec("POST mark-sent (2nd time) status=200",
                r2.status_code == 200, f"got {r2.status_code}: {r2.text[:200]}")
            if r2.status_code == 200:
                jb2 = r2.json()
                rec("2nd call skipped >= 1 (idempotent)",
                    int(jb2.get("skipped") or 0) >= 1,
                    f"updated={jb2.get('updated')} skipped={jb2.get('skipped')} skipped_ids={jb2.get('skipped_ids')}")

            # 5. RESET ---------------------------------------------------
            r3 = requests.post(f"{BASE}/me/bulk-message/reset",
                               json=body3, headers=H, timeout=30)
            rec("POST reset status=200", r3.status_code == 200,
                f"got {r3.status_code}: {r3.text[:200]}")
            if r3.status_code == 200:
                rec("reset response contains 'updated'",
                    "updated" in r3.json(), f"body={r3.text[:200]}")
    else:
        rec("SKIP step 3-5 (no eligible shipment id)", True,
            "but endpoint contract test still valid via empty-id call below")
        # Try with empty list to confirm endpoint accepts the model
        body3e = {"ttype": "shipment_sent", "shipment_ids": []}
        re_ = requests.post(f"{BASE}/me/bulk-message/mark-sent",
                            json=body3e, headers=H, timeout=30)
        rec("POST mark-sent (empty ids) — NOT 500",
            re_.status_code != 500, f"got {re_.status_code}: {re_.text[:200]}")

    # 6. Validation: missing ttype field ---------------------------------
    r6 = requests.post(f"{BASE}/me/bulk-message/mark-sent",
                       json={}, headers=H, timeout=30)
    rec("POST mark-sent body={} → 422", r6.status_code == 422,
        f"got {r6.status_code}: {r6.text[:200]}")

    # 7. Bad ttype → 400 -------------------------------------------------
    r7 = requests.post(f"{BASE}/me/bulk-message/mark-sent",
                       json={"ttype": "bad", "shipment_ids": []},
                       headers=H, timeout=30)
    rec("POST mark-sent ttype=bad → 400", r7.status_code == 400,
        f"got {r7.status_code}: {r7.text[:200]}")
    if r7.status_code == 400:
        msg = (r7.json().get("detail") or "").lower()
        rec("error message mentions 'unknown bulk template type'",
            "unknown bulk template type" in msg, f"detail={msg}")

    # 8. All 5 ttypes work without 500 -----------------------------------
    print("\n--- Testing all 5 ttypes ---")
    for tt in TTYPES:
        # GET eligible
        rg = requests.get(f"{BASE}/me/bulk-message/eligible",
                          params={"ttype": tt}, headers=H, timeout=30)
        rec(f"GET eligible ttype={tt} — NOT 500",
            rg.status_code != 500,
            f"got {rg.status_code}: {rg.text[:150]}")
        rec(f"GET eligible ttype={tt} status=200",
            rg.status_code == 200,
            f"got {rg.status_code}")

        # POST mark-sent with empty ids — should 200 and not 500
        rp = requests.post(f"{BASE}/me/bulk-message/mark-sent",
                           json={"ttype": tt, "shipment_ids": []},
                           headers=H, timeout=30)
        rec(f"POST mark-sent ttype={tt} (empty ids) — NOT 500",
            rp.status_code != 500,
            f"got {rp.status_code}: {rp.text[:150]}")
        rec(f"POST mark-sent ttype={tt} status=200",
            rp.status_code == 200,
            f"got {rp.status_code}: {rp.text[:200]}")

        # POST reset with empty ids — should 200 and not 500
        rr = requests.post(f"{BASE}/me/bulk-message/reset",
                           json={"ttype": tt, "shipment_ids": []},
                           headers=H, timeout=30)
        rec(f"POST reset ttype={tt} — NOT 500",
            rr.status_code != 500,
            f"got {rr.status_code}: {rr.text[:150]}")

    # Summary
    print("\n=========================================")
    failed = [r for r in results if not r[1]]
    passed = [r for r in results if r[1]]
    print(f"PASSED: {len(passed)}/{len(results)}")
    if failed:
        print(f"FAILED ({len(failed)}):")
        for n, _, d in failed:
            print(f"  - {n}: {d}")
        return 1
    print("ALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
