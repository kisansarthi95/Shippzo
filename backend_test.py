"""
Backend re-verification test — Courier partner limit for Platinum plan
(spec change: Platinum now capped at 5, no longer unlimited).

Review contract:
  1. Admin — GET /api/couriers/limits → is_admin=true, is_unlimited=true,
     limit=null, suggested_upgrade=null.
  2. Platinum simulation on user2 (direct Mongo set user2.plan="platinum"):
     - limits: plan=platinum, plan_label=Platinum, is_admin=false,
               is_unlimited=false, limit=5, suggested_upgrade=null,
               can_add=true (<5 couriers).
     - Create couriers until 5 total — each POST → 200.
     - limits now: current_count=5, can_add=false, suggested_upgrade=null.
     - 6th POST → 403, detail contains "Your Platinum plan allows only
       5 courier partners" AND "contact support" (case-insensitive),
       MUST NOT contain "Upgrade to".
     - Cleanup all created couriers + restore user2.plan.
  3. Gold regression: user2.plan="gold" — limit=2, suggested="Platinum",
     3rd courier POST → 403 detail contains "Upgrade to Platinum".
  4. Silver regression: user2.plan="silver" — limit=1, can_add=false,
     suggested="Gold".
"""
import os
import sys
import json
import asyncio

import requests
from motor.motor_asyncio import AsyncIOMotorClient

BASE = "https://logistics-hub-740.preview.emergentagent.com/api"

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASS = "Admin@12345"
USER2_EMAIL = "user2@test.com"
USER2_PASS = "User@12345"

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "test_database")


class Result:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.failures = []

    def check(self, cond, label, extra=""):
        if cond:
            self.passed += 1
            print(f"  PASS  {label}")
        else:
            self.failed += 1
            msg = f"{label}" + (f"  [{extra}]" if extra else "")
            self.failures.append(msg)
            print(f"  FAIL  {msg}")


def login(email, password):
    r = requests.post(f"{BASE}/auth/login", json={"email": email, "password": password}, timeout=20)
    r.raise_for_status()
    body = r.json()
    return body["token"], body


def auth_hdr(tok):
    return {"Authorization": f"Bearer {tok}"}


def get_limits(tok):
    r = requests.get(f"{BASE}/couriers/limits", headers=auth_hdr(tok), timeout=20)
    r.raise_for_status()
    return r.json()


def list_couriers(tok):
    r = requests.get(f"{BASE}/couriers", headers=auth_hdr(tok), timeout=20)
    r.raise_for_status()
    return r.json()


def create_courier(tok, name, prefix):
    payload = {
        "name": name,
        "series_prefix": prefix,
        "next_number": 1,
        "number_padding": 5,
        "contact_phone": "",
        "tracking_url_template": "",
    }
    return requests.post(f"{BASE}/couriers", headers=auth_hdr(tok), json=payload, timeout=20)


def delete_courier(tok, cid):
    return requests.delete(f"{BASE}/couriers/{cid}", headers=auth_hdr(tok), timeout=20)


async def set_user_plan(email, plan_value):
    """Directly flip user.plan in Mongo (bypasses billing flow). Returns
    previous plan value so caller can restore it."""
    cli = AsyncIOMotorClient(MONGO_URL)
    db = cli[DB_NAME]
    doc = await db.users.find_one({"email": email}, {"plan": 1})
    prev = (doc or {}).get("plan", "")
    await db.users.update_one({"email": email}, {"$set": {"plan": plan_value}})
    cli.close()
    return prev


async def get_user_plan(email):
    cli = AsyncIOMotorClient(MONGO_URL)
    db = cli[DB_NAME]
    doc = await db.users.find_one({"email": email}, {"plan": 1})
    cli.close()
    return (doc or {}).get("plan", "")


def run():
    r = Result()

    # --- Login both users -------------------------------------------------
    admin_tok, admin_info = login(ADMIN_EMAIL, ADMIN_PASS)
    user2_tok, user2_info = login(USER2_EMAIL, USER2_PASS)
    print(f"Admin id={admin_info.get('id')} is_admin={admin_info.get('is_admin')}")
    print(f"User2 id={user2_info.get('id')} plan={user2_info.get('plan')}")

    # =====================================================================
    # 1) Admin → unlimited
    # =====================================================================
    print("\n[1] Admin limits (unlimited)")
    lim = get_limits(admin_tok)
    print("  ", json.dumps(lim))
    r.check(lim.get("is_admin") is True, "admin.is_admin == true")
    r.check(lim.get("is_unlimited") is True, "admin.is_unlimited == true")
    r.check(lim.get("limit") is None, "admin.limit is null", str(lim.get("limit")))
    r.check(lim.get("suggested_upgrade") is None,
            "admin.suggested_upgrade is null", str(lim.get("suggested_upgrade")))

    # =====================================================================
    # 2) Platinum simulation on user2
    # =====================================================================
    print("\n[2] Platinum simulation on user2")
    prev_plan = asyncio.run(set_user_plan(USER2_EMAIL, "platinum"))
    print(f"  user2.plan pre-test={prev_plan!r} → now 'platinum'")

    created_ids = []
    try:
        # Clean baseline: count existing couriers
        existing = list_couriers(user2_tok)
        initial_count = len(existing)
        print(f"  user2 existing couriers: {initial_count}")

        # Limits snapshot BEFORE we create anything
        lim = get_limits(user2_tok)
        print("  platinum.limits (before):", json.dumps(lim))
        r.check(lim.get("plan") == "platinum", "platinum.plan == 'platinum'", str(lim.get("plan")))
        r.check(lim.get("plan_label") == "Platinum",
                "platinum.plan_label == 'Platinum'", str(lim.get("plan_label")))
        r.check(lim.get("is_admin") is False, "platinum.is_admin == false")
        r.check(lim.get("is_unlimited") is False, "platinum.is_unlimited == false")
        r.check(lim.get("limit") == 5, "platinum.limit == 5", str(lim.get("limit")))
        r.check(lim.get("suggested_upgrade") is None,
                "platinum.suggested_upgrade is null",
                str(lim.get("suggested_upgrade")))
        if initial_count < 5:
            r.check(lim.get("can_add") is True,
                    f"platinum.can_add == true (since count={initial_count} < 5)")

        # Fill to 5 total
        to_create = max(0, 5 - initial_count)
        print(f"  Creating {to_create} couriers to reach cap of 5…")
        for i in range(to_create):
            resp = create_courier(user2_tok, f"PlatTest{i+1}", f"PT{i+1}")
            r.check(resp.status_code == 200,
                    f"POST courier #{initial_count + i + 1} → 200",
                    f"status={resp.status_code} body={resp.text[:200]}")
            if resp.status_code == 200:
                created_ids.append(resp.json()["id"])

        # Limits after hitting cap
        lim2 = get_limits(user2_tok)
        print("  platinum.limits (at cap):", json.dumps(lim2))
        r.check(lim2.get("current_count") == 5,
                "platinum.current_count == 5", str(lim2.get("current_count")))
        r.check(lim2.get("can_add") is False,
                "platinum.can_add == false at cap")
        r.check(lim2.get("suggested_upgrade") is None,
                "platinum.suggested_upgrade is null at cap",
                str(lim2.get("suggested_upgrade")))

        # 6th courier should 403 with specific message
        resp6 = create_courier(user2_tok, "PlatTest6", "PT6")
        print(f"  6th POST status={resp6.status_code} body={resp6.text[:300]}")
        r.check(resp6.status_code == 403, "6th POST → 403",
                f"got {resp6.status_code}")
        detail = ""
        try:
            detail = (resp6.json() or {}).get("detail", "")
        except Exception:
            pass
        detail_low = detail.lower()
        r.check("your platinum plan allows only 5 courier partners" in detail_low,
                "detail contains 'Your Platinum plan allows only 5 courier partners'",
                detail)
        r.check("contact support" in detail_low,
                "detail contains 'contact support' (case-insensitive)", detail)
        r.check("upgrade to" not in detail_low,
                "detail does NOT contain 'Upgrade to'", detail)
        # 6th request should not have created a courier
        if resp6.status_code == 200:
            try:
                created_ids.append(resp6.json()["id"])
            except Exception:
                pass

        # =================================================================
        # 3) Gold regression (on same user2)
        # =================================================================
        print("\n[3] Gold regression")
        # Cleanup created couriers first so we can test gold cap.
        print(f"  Cleaning {len(created_ids)} couriers created during platinum test")
        for cid in list(created_ids):
            dr = delete_courier(user2_tok, cid)
            if dr.status_code == 200:
                created_ids.remove(cid)
            else:
                print(f"   delete {cid} → {dr.status_code} {dr.text[:120]}")

        asyncio.run(set_user_plan(USER2_EMAIL, "gold"))
        # refresh to confirm
        after_gold = list_couriers(user2_tok)
        print(f"  user2 courier count after cleanup: {len(after_gold)}")
        lim_g = get_limits(user2_tok)
        print("  gold.limits:", json.dumps(lim_g))
        r.check(lim_g.get("plan") == "gold", "gold.plan == 'gold'", str(lim_g.get("plan")))
        r.check(lim_g.get("limit") == 2, "gold.limit == 2", str(lim_g.get("limit")))
        r.check(lim_g.get("suggested_upgrade") == "Platinum",
                "gold.suggested_upgrade == 'Platinum'",
                str(lim_g.get("suggested_upgrade")))

        # If user2 already has less than 2, fill up to 2
        gold_created = []
        cur_count = lim_g.get("current_count", 0)
        while cur_count < 2:
            resp = create_courier(user2_tok, f"GoldTest{cur_count+1}", f"GT{cur_count+1}")
            r.check(resp.status_code == 200,
                    f"Gold POST up to cap #{cur_count+1} → 200",
                    f"status={resp.status_code} body={resp.text[:200]}")
            if resp.status_code == 200:
                gold_created.append(resp.json()["id"])
                cur_count += 1
            else:
                break

        # 3rd courier on gold → 403 with 'Upgrade to Platinum'
        resp3g = create_courier(user2_tok, "GoldTest3", "GT3")
        print(f"  Gold 3rd POST status={resp3g.status_code} body={resp3g.text[:300]}")
        r.check(resp3g.status_code == 403,
                "gold 3rd POST → 403", f"got {resp3g.status_code}")
        detail_g = ""
        try:
            detail_g = (resp3g.json() or {}).get("detail", "")
        except Exception:
            pass
        r.check("Upgrade to Platinum" in detail_g,
                "gold 403 detail contains 'Upgrade to Platinum'", detail_g)
        if resp3g.status_code == 200:
            try:
                gold_created.append(resp3g.json()["id"])
            except Exception:
                pass

        # Clean up gold-created couriers
        for cid in gold_created:
            delete_courier(user2_tok, cid)

        # =================================================================
        # 4) Silver regression
        # =================================================================
        print("\n[4] Silver regression")
        asyncio.run(set_user_plan(USER2_EMAIL, "silver"))
        lim_s = get_limits(user2_tok)
        print("  silver.limits:", json.dumps(lim_s))
        r.check(lim_s.get("plan") == "silver",
                "silver.plan == 'silver'", str(lim_s.get("plan")))
        r.check(lim_s.get("limit") == 1, "silver.limit == 1", str(lim_s.get("limit")))
        r.check(lim_s.get("suggested_upgrade") == "Gold",
                "silver.suggested_upgrade == 'Gold'",
                str(lim_s.get("suggested_upgrade")))
        # With the 1 seeded default courier, can_add should be False
        if lim_s.get("current_count", 0) >= 1:
            r.check(lim_s.get("can_add") is False,
                    "silver.can_add == false (already at 1)",
                    str(lim_s.get("can_add")))

    finally:
        # Final cleanup — restore plan + delete any stragglers
        print("\n[cleanup] restoring user2.plan and deleting stragglers")
        # Clean leftover created couriers
        try:
            all_c = list_couriers(user2_tok)
            for c in all_c:
                if c.get("name", "").startswith(("PlatTest", "GoldTest")):
                    dr = delete_courier(user2_tok, c["id"])
                    print(f"  cleanup delete {c['name']} → {dr.status_code}")
        except Exception as e:
            print(f"  cleanup list/delete error: {e}")
        restored = prev_plan if prev_plan else "silver"
        asyncio.run(set_user_plan(USER2_EMAIL, restored))
        print(f"  user2.plan restored to {restored!r}")

    # =====================================================================
    print("\n" + "=" * 60)
    print(f"RESULT  passed={r.passed}  failed={r.failed}")
    if r.failures:
        print("FAILURES:")
        for f in r.failures:
            print(f"  - {f}")
    print("=" * 60)
    return 0 if r.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(run())
