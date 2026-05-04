"""
Backend test for Phase 2D — Bulk-clone variants endpoint:
    POST /api/couriers/{courier_id}/variants/copy-from/{source_courier_id}

Tested against the public preview URL.
Auth: user2@test.com (silver plan; temporarily bumped to gold via Mongo
to allow creating a 2nd courier — reverted at the end).
"""

import os
import sys
import asyncio
from typing import Any, Dict, List, Tuple

import requests
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(ROOT, "backend", ".env"))

BASE = "https://logistics-hub-740.preview.emergentagent.com/api"
USER_EMAIL = "user2@test.com"
USER_PASSWORD = "User@12345"

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "test_database")

results: List[Tuple[str, bool, str]] = []


def record(name: str, ok: bool, msg: str = ""):
    results.append((name, ok, msg))
    icon = "PASS" if ok else "FAIL"
    print(f"[{icon}] {name}" + (f" — {msg}" if msg else ""))


def login(email: str, password: str) -> str:
    r = requests.post(f"{BASE}/auth/login", json={"email": email, "password": password}, timeout=15)
    r.raise_for_status()
    return r.json()["token"]


def H(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


async def set_plan(user_email: str, plan: str):
    cli = AsyncIOMotorClient(MONGO_URL)
    db = cli[DB_NAME]
    res = await db.users.update_one({"email": user_email}, {"$set": {"plan": plan}})
    cli.close()
    return res.modified_count


def cleanup_couriers(token: str, courier_ids: List[str]):
    for cid in courier_ids:
        try:
            r = requests.get(f"{BASE}/couriers/{cid}/variants", headers=H(token), timeout=10)
            if r.status_code == 200:
                for v in r.json().get("variants", []):
                    requests.delete(
                        f"{BASE}/couriers/{cid}/variants/{v['id']}",
                        headers=H(token), timeout=10,
                    )
            requests.delete(f"{BASE}/couriers/{cid}", headers=H(token), timeout=10)
        except Exception as e:
            print(f"  cleanup courier {cid}: {e}")


def main():
    print(f"\n=== Phase 2D Copy-Variants Endpoint Tests ===\nTarget: {BASE}\n")

    asyncio.run(set_plan(USER_EMAIL, "platinum"))

    created_courier_ids: List[str] = []
    token = None
    try:
        # 9. Auth check (no token).
        r = requests.post(
            f"{BASE}/couriers/00000000-0000-0000-0000-000000000000/variants/copy-from/11111111-1111-1111-1111-111111111111",
            timeout=15,
        )
        record(
            "9. Without token returns 401/403",
            r.status_code in (401, 403),
            f"got {r.status_code}",
        )

        token = login(USER_EMAIL, USER_PASSWORD)

        # 1. Setup.
        r = requests.get(f"{BASE}/couriers", headers=H(token), timeout=15)
        r.raise_for_status()
        couriers = r.json()
        record("1a. GET /couriers OK", isinstance(couriers, list), f"got {len(couriers)} couriers")

        if len(couriers) < 2:
            r = requests.post(
                f"{BASE}/couriers", headers=H(token),
                json={"name": "TARGET_COURIER_TEST", "series_prefix": "TGT", "number_padding": 5},
                timeout=15,
            )
            record(
                "1b. Created 2nd courier TARGET_COURIER_TEST",
                r.status_code == 200,
                f"status={r.status_code} body={r.text[:120]}",
            )
            if r.status_code == 200:
                created_courier_ids.append(r.json()["id"])
            else:
                print("Cannot proceed — couldn't create 2nd courier.")
                return
            couriers = requests.get(f"{BASE}/couriers", headers=H(token), timeout=15).json()

        source = next((c for c in couriers if c["name"] != "TARGET_COURIER_TEST"), couriers[0])
        target = next((c for c in couriers if c["id"] != source["id"]), None)
        if target is None:
            record("1c. Need 2 distinct couriers", False, "only 1 found")
            return
        SRC_ID = source["id"]
        TGT_ID = target["id"]
        print(f"  SOURCE: {source['name']} ({SRC_ID})")
        print(f"  TARGET: {target['name']} ({TGT_ID})")
        record("1c. Have 2 distinct couriers", True, f"src={source['name']}, tgt={target['name']}")

        # 2. Create source variants — clear first.
        for cid in (SRC_ID, TGT_ID):
            r = requests.get(f"{BASE}/couriers/{cid}/variants", headers=H(token), timeout=15)
            for v in (r.json().get("variants", []) if r.status_code == 200 else []):
                requests.delete(
                    f"{BASE}/couriers/{cid}/variants/{v['id']}",
                    headers=H(token), timeout=10,
                )

        v1 = {"variant_name": "ALPHA-100g", "weight_g": 100,
              "within_state_rate": 25, "outside_state_rate": 50}
        v2 = {"variant_name": "BETA-500g", "weight_g": 500,
              "within_state_rate": 60, "outside_state_rate": 120}
        for body, label in [(v1, "ALPHA-100g"), (v2, "BETA-500g")]:
            r = requests.post(
                f"{BASE}/couriers/{SRC_ID}/variants",
                headers=H(token), json=body, timeout=15,
            )
            record(
                f"2. Create source variant {label}",
                r.status_code == 200,
                f"status={r.status_code} {r.text[:100]}",
            )

        # 3. Self-copy.
        r = requests.post(
            f"{BASE}/couriers/{SRC_ID}/variants/copy-from/{SRC_ID}",
            headers=H(token), timeout=15,
        )
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        ok_self = r.status_code == 400 and "same" in (body.get("detail", "") or "").lower()
        record(
            "3. Self-copy returns 400 'Source and target courier are the same'",
            ok_self,
            f"status={r.status_code} detail={(body.get('detail',''))[:120]}",
        )

        # 4. 404 tests.
        bogus = "00000000-0000-0000-0000-000000000000"
        r = requests.post(
            f"{BASE}/couriers/{TGT_ID}/variants/copy-from/{bogus}",
            headers=H(token), timeout=15,
        )
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        record(
            "4a. Bogus source → 404 'Source courier not found'",
            r.status_code == 404 and "source" in (body.get("detail", "") or "").lower(),
            f"status={r.status_code} detail={body.get('detail','')[:120]}",
        )

        r = requests.post(
            f"{BASE}/couriers/{bogus}/variants/copy-from/{SRC_ID}",
            headers=H(token), timeout=15,
        )
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        record(
            "4b. Bogus target → 404 'Target courier not found'",
            r.status_code == 404 and "target" in (body.get("detail", "") or "").lower(),
            f"status={r.status_code} detail={body.get('detail','')[:120]}",
        )

        # 5. Successful copy.
        r = requests.post(
            f"{BASE}/couriers/{TGT_ID}/variants/copy-from/{SRC_ID}",
            headers=H(token), timeout=15,
        )
        record(
            "5a. Successful copy returns 200",
            r.status_code == 200,
            f"status={r.status_code} body={r.text[:200]}",
        )
        if r.status_code == 200:
            j = r.json()
            print(f"   response: {j}")
            record("5b. ok==True",                     j.get("ok") is True, str(j.get("ok")))
            record("5c. copied_count >= 1",            int(j.get("copied_count", 0)) >= 1, str(j.get("copied_count")))
            record("5d. skipped_duplicates is []",     j.get("skipped_duplicates") == [], str(j.get("skipped_duplicates")))
            record("5e. skipped_cap_full is list",     isinstance(j.get("skipped_cap_full"), list), str(j.get("skipped_cap_full")))
            record("5f. source_courier_name correct",  j.get("source_courier_name") == source["name"], str(j.get("source_courier_name")))
            record("5g. target_courier_name correct",  j.get("target_courier_name") == target["name"], str(j.get("target_courier_name")))

            r2 = requests.get(f"{BASE}/couriers/{TGT_ID}/variants", headers=H(token), timeout=15)
            tgt_vars = r2.json().get("variants", [])
            tgt_names = sorted([v["variant_name"] for v in tgt_vars])
            record(
                "5h. GET /target/variants contains copied",
                "ALPHA-100g" in tgt_names and "BETA-500g" in tgt_names,
                f"names={tgt_names}",
            )

        # 6. Duplicate prevention.
        r = requests.post(
            f"{BASE}/couriers/{TGT_ID}/variants/copy-from/{SRC_ID}",
            headers=H(token), timeout=15,
        )
        if r.status_code == 200:
            j = r.json()
            print(f"   re-run response: {j}")
            record("6a. Re-run returns 200", True, "")
            record(
                "6b. copied_count == 0",
                int(j.get("copied_count", -1)) == 0,
                str(j.get("copied_count")),
            )
            sd = sorted(j.get("skipped_duplicates", []))
            expected = sorted(["ALPHA-100g", "BETA-500g"])
            record(
                "6c. skipped_duplicates includes both names",
                sd == expected,
                f"got={sd}",
            )
        elif r.status_code == 402:
            record(
                "6a. Re-run returned 402 (target at cap; acceptable per spec step 8)",
                True,
                f"detail={r.json().get('detail','')[:120]}",
            )
        else:
            record(
                "6a. Re-run unexpected status",
                False,
                f"status={r.status_code} body={r.text[:200]}",
            )

        # 7. Empty source.
        r = requests.post(
            f"{BASE}/couriers", headers=H(token),
            json={"name": "EMPTY_SRC_TEST", "series_prefix": "EMP", "number_padding": 5},
            timeout=15,
        )
        if r.status_code == 200:
            empty_src = r.json()
            created_courier_ids.append(empty_src["id"])
            ESID = empty_src["id"]
            r2 = requests.post(
                f"{BASE}/couriers/{TGT_ID}/variants/copy-from/{ESID}",
                headers=H(token), timeout=15,
            )
            body = r2.json() if r2.headers.get("content-type", "").startswith("application/json") else {}
            record(
                "7. Empty source → 400 'Source courier has no variants to copy'",
                r2.status_code == 400 and "no variants" in (body.get("detail", "") or "").lower(),
                f"status={r2.status_code} detail={(body.get('detail',''))[:120]}",
            )
        else:
            record(
                "7. Could not create empty-source courier (plan-cap?)",
                False,
                f"status={r.status_code} body={r.text[:200]}",
            )

        # 8. Plan-cap behaviour.
        r = requests.post(
            f"{BASE}/couriers", headers=H(token),
            json={"name": "CAP_TEST_SRC", "series_prefix": "CTS", "number_padding": 5},
            timeout=15,
        )
        if r.status_code == 200:
            cts = r.json()
            created_courier_ids.append(cts["id"])
            CTS_ID = cts["id"]
            for nm, w in [("CAP-VAR-ONE", 100), ("CAP-VAR-TWO", 200)]:
                requests.post(
                    f"{BASE}/couriers/{CTS_ID}/variants",
                    headers=H(token), json={"variant_name": nm, "weight_g": w}, timeout=10,
                )
            r3 = requests.get(f"{BASE}/couriers/{TGT_ID}/variants", headers=H(token), timeout=15).json()
            cap = r3.get("cap")
            cur = r3.get("current_count", 0)
            needed = (int(cap) - int(cur)) if cap is not None else 0
            for i in range(needed):
                requests.post(
                    f"{BASE}/couriers/{TGT_ID}/variants",
                    headers=H(token),
                    json={"variant_name": f"FILLER-{i}", "weight_g": 50 + i},
                    timeout=10,
                )
            r4 = requests.get(f"{BASE}/couriers/{TGT_ID}/variants", headers=H(token), timeout=15).json()
            record(
                "8a. Target filled to cap",
                cap is None or int(r4.get("current_count", 0)) >= int(cap),
                f"current={r4.get('current_count')}/{cap}",
            )
            r5 = requests.post(
                f"{BASE}/couriers/{TGT_ID}/variants/copy-from/{CTS_ID}",
                headers=H(token), timeout=15,
            )
            print(f"   8b copy-when-at-cap status={r5.status_code} body={r5.text[:200]}")
            if r5.status_code == 402:
                record(
                    "8b. Copy when target at cap → 402 (no copy possible — per spec)",
                    True,
                    f"detail={r5.json().get('detail','')[:120]}",
                )
            elif r5.status_code == 200:
                j = r5.json()
                ok8 = (
                    int(j.get("copied_count", -1)) == 0 and
                    sorted(j.get("skipped_cap_full", [])) ==
                    sorted(["CAP-VAR-ONE", "CAP-VAR-TWO"])
                )
                record(
                    "8b. Copy when target at cap → 200 with copied_count=0 + skipped_cap_full=[both]",
                    ok8,
                    f"copied={j.get('copied_count')} skipped_cap={j.get('skipped_cap_full')}",
                )
            else:
                record(
                    "8b. Plan-cap test unexpected status",
                    False,
                    f"status={r5.status_code} body={r5.text[:200]}",
                )

    finally:
        try:
            tok = login(USER_EMAIL, USER_PASSWORD)
            r = requests.get(f"{BASE}/couriers", headers=H(tok), timeout=15)
            if r.status_code == 200:
                for c in r.json():
                    rv = requests.get(f"{BASE}/couriers/{c['id']}/variants", headers=H(tok), timeout=15)
                    if rv.status_code == 200:
                        for v in rv.json().get("variants", []):
                            requests.delete(
                                f"{BASE}/couriers/{c['id']}/variants/{v['id']}",
                                headers=H(tok), timeout=10,
                            )
            cleanup_couriers(tok, created_courier_ids)
        except Exception as e:
            print(f"  cleanup failed: {e}")
        try:
            asyncio.run(set_plan(USER_EMAIL, "silver"))
            print("  Reverted user2 plan to silver.")
        except Exception as e:
            print(f"  could not revert plan: {e}")

    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n=== {passed}/{len(results)} assertions passed ===\n")
    for n, ok, msg in results:
        if not ok:
            print(f"FAILED: {n} — {msg}")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
