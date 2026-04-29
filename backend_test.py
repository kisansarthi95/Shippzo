"""
Backend tests for Courier Partner plan-cap logic.

Verifies:
- GET  /api/couriers/limits  (auth required, plan-aware response)
- POST /api/couriers         (403 with actionable detail at plan cap)
- Admin bypass + platinum bypass
- Plan-upgrade simulation (gold -> platinum) via direct Mongo mutation
- Route-order sanity: /couriers/{id} still resolves a real id
- Regression: /couriers list still returns user couriers

Cleans up everything it creates and restores user2.plan after mutation.
"""

import os
import sys
import asyncio
from typing import Any, Dict, List, Optional, Tuple

import requests
from motor.motor_asyncio import AsyncIOMotorClient

from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

FRONTEND_ENV = "/app/frontend/.env"
PUBLIC_URL: Optional[str] = None
with open(FRONTEND_ENV) as fh:
    for line in fh:
        line = line.strip()
        if line.startswith("EXPO_PUBLIC_BACKEND_URL="):
            PUBLIC_URL = line.split("=", 1)[1].strip().strip('"').strip("'")
            break

if not PUBLIC_URL:
    print("ERROR: EXPO_PUBLIC_BACKEND_URL not set"); sys.exit(2)

API = f"{PUBLIC_URL}/api"
print(f"[INFO] Using API base: {API}")

MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASS = "Admin@12345"
USER_EMAIL = "user2@test.com"
USER_PASS = "User@12345"

results: List[Tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> bool:
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name} :: {detail}")
    results.append((name, ok, detail))
    return ok


def login(email: str, password: str) -> Dict[str, Any]:
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=30)
    r.raise_for_status()
    return r.json()


def auth_h(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def get_limits(token: str) -> Tuple[int, Dict[str, Any]]:
    r = requests.get(f"{API}/couriers/limits", headers=auth_h(token), timeout=30)
    try:
        body = r.json()
    except Exception:
        body = {"_raw": r.text}
    return r.status_code, body


def list_couriers(token: str) -> Tuple[int, List[Dict[str, Any]]]:
    r = requests.get(f"{API}/couriers", headers=auth_h(token), timeout=30)
    try:
        body = r.json()
    except Exception:
        body = []
    return r.status_code, body


def create_courier(token: str, name: str, prefix: str = "T") -> Tuple[int, Dict[str, Any]]:
    payload = {
        "name": name,
        "series_prefix": prefix,
        "next_number": 1,
        "number_padding": 4,
    }
    r = requests.post(f"{API}/couriers", headers=auth_h(token), json=payload, timeout=30)
    try:
        body = r.json()
    except Exception:
        body = {"_raw": r.text}
    return r.status_code, body


def delete_courier(token: str, cid: str) -> int:
    r = requests.delete(f"{API}/couriers/{cid}", headers=auth_h(token), timeout=30)
    return r.status_code


def get_courier(token: str, cid: str) -> Tuple[int, Dict[str, Any]]:
    r = requests.get(f"{API}/couriers/{cid}", headers=auth_h(token), timeout=30)
    try:
        body = r.json()
    except Exception:
        body = {"_raw": r.text}
    return r.status_code, body


async def _set_plan(user_id: str, plan: str) -> Dict[str, Any]:
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    res = await db.users.find_one_and_update(
        {"id": user_id}, {"$set": {"plan": plan}}, return_document=True
    )
    client.close()
    return res or {}


def set_plan(user_id: str, plan: str) -> Dict[str, Any]:
    return asyncio.get_event_loop().run_until_complete(_set_plan(user_id, plan))


def main() -> int:
    try:
        admin = login(ADMIN_EMAIL, ADMIN_PASS)
        record("admin login", True, f"id={admin.get('id','')[:8]}")
    except Exception as e:
        record("admin login", False, str(e)); return 1
    try:
        user = login(USER_EMAIL, USER_PASS)
        record("user2 login", True, f"id={user.get('id','')[:8]} plan={user.get('plan')}")
    except Exception as e:
        record("user2 login", False, str(e)); return 1

    admin_tok = admin["token"]
    user_tok = user["token"]
    user_id = user["id"]
    original_plan = user.get("plan") or "free_trial"

    r = requests.get(f"{API}/couriers/limits", timeout=30)
    record("/couriers/limits requires auth", r.status_code in (401, 403), f"got {r.status_code}")

    sc, body = get_limits(user_tok)
    print(f"[INFO] user2 limits initial: {body}")
    record("user2 GET /couriers/limits 200", sc == 200, f"status={sc}")
    user2_plan_key = body.get("plan")
    record("user2 plan in {free_trial, silver}", user2_plan_key in ("free_trial", "silver"), f"plan={user2_plan_key}")
    record("user2 limit == 1", body.get("limit") == 1, f"limit={body.get('limit')}")
    record("user2 current_count == 1 (default seeded courier)", body.get("current_count") == 1, f"current_count={body.get('current_count')}")
    record("user2 can_add == False", body.get("can_add") is False, f"can_add={body.get('can_add')}")
    record("user2 is_unlimited == False", body.get("is_unlimited") is False, f"is_unlimited={body.get('is_unlimited')}")
    record("user2 is_admin == False", body.get("is_admin") is False, f"is_admin={body.get('is_admin')}")
    record("user2 suggested_upgrade in {Silver, Gold}", body.get("suggested_upgrade") in ("Silver", "Gold"), f"suggested_upgrade={body.get('suggested_upgrade')}")

    sc, body = create_courier(user_tok, "Test Cap")
    record("user2 POST /couriers 403 at cap", sc == 403, f"status={sc} body={body}")
    detail = (body.get("detail") if isinstance(body, dict) else "") or ""
    record("403 detail mentions plan label (Silver or Free Trial)", ("Silver plan" in detail) or ("Free Trial plan" in detail), f"detail='{detail}'")
    record("403 detail says 'Upgrade to <NextTier>'", ("Upgrade to Gold" in detail) or ("Upgrade to Silver" in detail), f"detail='{detail}'")

    sc, body = get_limits(admin_tok)
    print(f"[INFO] admin limits: {body}")
    record("admin GET /couriers/limits 200", sc == 200, f"status={sc}")
    record("admin is_admin == True", body.get("is_admin") is True, f"is_admin={body.get('is_admin')}")
    record("admin is_unlimited == True", body.get("is_unlimited") is True, f"is_unlimited={body.get('is_unlimited')}")
    record("admin limit == None", body.get("limit") is None, f"limit={body.get('limit')}")
    record("admin can_add == True", body.get("can_add") is True, f"can_add={body.get('can_add')}")

    sc, body = create_courier(admin_tok, "PlanCap Admin Test")
    admin_courier_id = body.get("id") if sc == 200 else None
    record("admin POST /couriers succeeds (any count)", sc == 200, f"status={sc} id={admin_courier_id}")
    if admin_courier_id:
        del_sc = delete_courier(admin_tok, admin_courier_id)
        record("admin cleanup DELETE /couriers/{id}", del_sc == 200, f"status={del_sc}")

    sc, ad_couriers = list_couriers(admin_tok)
    record("admin GET /couriers list 200", sc == 200, f"count={len(ad_couriers) if isinstance(ad_couriers, list) else 'n/a'}")
    if isinstance(ad_couriers, list) and ad_couriers:
        sample_id = ad_couriers[0]["id"]
        sc, sample = get_courier(admin_tok, sample_id)
        record("GET /couriers/{valid_id} returns specific courier", sc == 200 and sample.get("id") == sample_id, f"status={sc} id={sample.get('id')}")
    else:
        record("admin had at least 1 seeded courier for sanity check", False, "list empty")

    sc, u_couriers = list_couriers(user_tok)
    record("user2 GET /couriers list 200", sc == 200, f"status={sc}")
    record("user2 list contains exactly 1 courier", isinstance(u_couriers, list) and len(u_couriers) == 1, f"count={len(u_couriers) if isinstance(u_couriers, list) else 'n/a'}")

    created_in_gold: List[str] = []
    try:
        upd = set_plan(user_id, "gold")
        record("Mongo: set user2 plan = gold", upd.get("plan") == "gold", f"plan={upd.get('plan')}")

        sc, body = get_limits(user_tok)
        print(f"[INFO] user2 limits as GOLD (existing token): {body}")
        if body.get("plan") != "gold":
            user2 = login(USER_EMAIL, USER_PASS)
            user_tok = user2["token"]
            sc, body = get_limits(user_tok)
            print(f"[INFO] user2 limits after re-login (GOLD): {body}")

        record("GOLD: GET /couriers/limits plan=gold", body.get("plan") == "gold", f"plan={body.get('plan')}")
        record("GOLD: limit == 2", body.get("limit") == 2, f"limit={body.get('limit')}")
        record("GOLD: current_count == 1", body.get("current_count") == 1, f"current_count={body.get('current_count')}")
        record("GOLD: can_add == True", body.get("can_add") is True, f"can_add={body.get('can_add')}")
        record("GOLD: suggested_upgrade == Platinum", body.get("suggested_upgrade") == "Platinum", f"suggested_upgrade={body.get('suggested_upgrade')}")

        sc, body = create_courier(user_tok, "Gold Slot 2", prefix="G")
        if sc == 200 and body.get("id"):
            created_in_gold.append(body["id"])
        record("GOLD: POST /couriers (2nd) 200", sc == 200, f"status={sc} body={body if sc!=200 else 'ok'}")

        sc, body = get_limits(user_tok)
        record("GOLD: after add - current_count == 2", body.get("current_count") == 2, f"current_count={body.get('current_count')}")
        record("GOLD: after add - can_add == False", body.get("can_add") is False, f"can_add={body.get('can_add')}")

        sc, body = create_courier(user_tok, "Gold Slot 3 should fail", prefix="X")
        record("GOLD: POST /couriers (3rd) 403", sc == 403, f"status={sc}")
        detail = (body.get("detail") if isinstance(body, dict) else "") or ""
        record("GOLD: 403 detail mentions 'Gold plan'", "Gold plan" in detail, f"detail='{detail}'")
        record("GOLD: 403 detail says 'Upgrade to Platinum'", "Upgrade to Platinum" in detail, f"detail='{detail}'")
    except Exception as e:
        record("GOLD plan simulation block", False, f"exception: {e}")
    finally:
        for cid in created_in_gold:
            delete_courier(user_tok, cid)

    created_in_plat: List[str] = []
    try:
        upd = set_plan(user_id, "platinum")
        record("Mongo: set user2 plan = platinum", upd.get("plan") == "platinum")

        user2 = login(USER_EMAIL, USER_PASS)
        user_tok = user2["token"]

        sc, body = get_limits(user_tok)
        print(f"[INFO] user2 limits as PLATINUM: {body}")
        record("PLATINUM: plan=platinum", body.get("plan") == "platinum", f"plan={body.get('plan')}")
        record("PLATINUM: is_unlimited == True", body.get("is_unlimited") is True, f"is_unlimited={body.get('is_unlimited')}")
        record("PLATINUM: limit == None", body.get("limit") is None, f"limit={body.get('limit')}")
        record("PLATINUM: can_add == True", body.get("can_add") is True, f"can_add={body.get('can_add')}")

        for label in ("Platinum 1", "Platinum 2", "Platinum 3"):
            sc, body = create_courier(user_tok, label, prefix="P")
            if sc == 200 and body.get("id"):
                created_in_plat.append(body["id"])
            record(f"PLATINUM: POST /couriers '{label}' succeeds", sc == 200, f"status={sc}")
    except Exception as e:
        record("PLATINUM plan simulation block", False, f"exception: {e}")
    finally:
        for cid in created_in_plat:
            delete_courier(user_tok, cid)

    try:
        upd = set_plan(user_id, original_plan)
        record(f"Mongo: restored user2 plan = {original_plan}", (upd.get("plan") or "") == original_plan, f"plan={upd.get('plan')}")
    except Exception as e:
        record("Restore user2 original plan", False, str(e))

    user2 = login(USER_EMAIL, USER_PASS)
    user_tok = user2["token"]
    sc, u_couriers = list_couriers(user_tok)
    record("Final regression: user2 list still works", sc == 200, f"status={sc}")
    record("Final regression: user2 has exactly 1 courier (cleanup ok)", isinstance(u_couriers, list) and len(u_couriers) == 1, f"count={len(u_couriers) if isinstance(u_couriers, list) else 'n/a'}")
    sc, body = get_limits(user_tok)
    record("Final regression: limit reverted to 1", body.get("limit") == 1 and body.get("current_count") == 1, f"body={body}")

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print("\n=========================")
    print(f"PASSED: {passed}/{total}")
    print(f"FAILED: {total - passed}")
    if total - passed:
        print("Failures:")
        for name, ok, detail in results:
            if not ok:
                print(f"  - {name} :: {detail}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
