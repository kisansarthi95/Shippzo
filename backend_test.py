"""
Phase D — Razorpay real integration for extra team-member slots.
Backend regression test.

Covers:
  1. POST /api/me/team-members/pay-extra (method=razorpay)
  2. POST /api/me/team-members/pay-extra (method=wallet) — insufficient/sufficient
  3. POST /api/me/team-members/razorpay/verify  (NEW)
     - bogus signature, wrong purpose, wrong user, idempotency, valid signature
  4. POST /api/me/team-members/with-extra (HARDENED — paid/unpaid/consumed/invalid)
  5. Smoke regression on existing endpoints

Uses real Razorpay TEST credentials from /app/backend/.env. The "valid
signature" happy-path test uses the same HMAC-SHA256 algorithm that
`razorpay.client.utility.generate_signature` uses internally — so the
backend's `verify_payment_signature` accepts it without a real Razorpay
Checkout round-trip.

Direct Mongo access used to seed plans / wallet balances and to clean up.
"""
from __future__ import annotations

import hmac
import hashlib
import os
import sys
import time
import uuid
from datetime import datetime, timezone

import requests
from pymongo import MongoClient

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────
BASE_URL = "https://logistics-hub-740.preview.emergentagent.com/api"

# Read backend .env for MONGO_URL / DB_NAME / Razorpay test creds
_env: dict[str, str] = {}
with open("/app/backend/.env") as fh:
    for ln in fh:
        ln = ln.strip()
        if not ln or ln.startswith("#") or "=" not in ln:
            continue
        k, _, v = ln.partition("=")
        _env[k.strip()] = v.strip().strip('"').strip("'")

MONGO_URL = _env.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = _env.get("DB_NAME", "test_database")
RZP_KEY_ID = _env.get("RAZORPAY_KEY_ID", "")
RZP_KEY_SECRET = _env.get("RAZORPAY_KEY_SECRET", "")

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASSWORD = "Admin@12345"

results: list[tuple[bool, str, str]] = []


def log(ok: bool, name: str, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    line = f"[{mark}] {name}"
    if detail:
        line += f" — {detail}"
    print(line, flush=True)
    results.append((ok, name, detail))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _post(path, json=None, headers=None, timeout=30):
    return requests.post(f"{BASE_URL}{path}", json=json, headers=headers or {}, timeout=timeout)


def _get(path, headers=None, timeout=30):
    return requests.get(f"{BASE_URL}{path}", headers=headers or {}, timeout=timeout)


def _put(path, json=None, headers=None, timeout=30):
    return requests.put(f"{BASE_URL}{path}", json=json, headers=headers or {}, timeout=timeout)


def _del(path, headers=None, timeout=30):
    return requests.delete(f"{BASE_URL}{path}", headers=headers or {}, timeout=timeout)


def login(email, password):
    r = _post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, f"login {email} → {r.status_code}: {r.text[:200]}"
    body = r.json()
    return body["token"], body


def signup(email, password, name, phone):
    r = _post(
        "/auth/signup",
        json={
            "email": email, "password": password,
            "name": name, "shop_name": f"{name}'s Shop", "phone": phone,
        },
    )
    assert r.status_code in (200, 201), f"signup {email} → {r.status_code}: {r.text[:200]}"
    body = r.json()
    return body["token"], body


def hmac_signature(order_id, payment_id, secret):
    """Replicates `razorpay.client.utility.generate_signature(f'{order}|{payment}', secret)`."""
    msg = f"{order_id}|{payment_id}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def main() -> int:
    print(f"BASE_URL={BASE_URL}")
    print(f"DB={DB_NAME}  RZP_KEY_ID={RZP_KEY_ID[:14]}…")

    mongo = MongoClient(MONGO_URL)
    db = mongo[DB_NAME]

    # ── Login admin (used for cross-user 404 verify test + smoke) ─────────
    admin_token, admin_user = login(ADMIN_EMAIL, ADMIN_PASSWORD)
    admin_uid = admin_user["id"]
    admin_H = {"Authorization": f"Bearer {admin_token}"}
    log(True, "Login admin@test.com", f"id={admin_uid[:8]}…")

    # ── Create a fresh user for clean wallet/quota baseline ───────────────
    test_email = f"phased_{uuid.uuid4().hex[:8]}@test.com"
    test_password = "PhaseD@12345"
    test_name = "Phase D Tester"
    test_phone = "9000088888"
    user_token, user_doc = signup(test_email, test_password, test_name, test_phone)
    user_uid = user_doc["id"]
    H = {"Authorization": f"Bearer {user_token}"}
    log(True, "Signup fresh user", f"email={test_email} id={user_uid[:8]}…")

    # Promote test user to gold plan (extra_member_price_inr=200, team_member_cap=1)
    db.users.update_one(
        {"id": user_uid},
        {"$set": {
            "plan": "gold", "is_admin": False, "trial_consumed": False,
            "plan_expires_at": "2099-12-31T23:59:59+00:00",
        }},
    )
    log(True, "Seed: user.plan=gold", "team_member_cap=1, extra_price=₹200")

    # ── 1. GET /me/team-members baseline ──────────────────────────────────
    r = _get("/me/team-members", headers=H)
    ok = r.status_code == 200
    body = r.json() if ok else {}
    expected_keys = {"members", "free_cap", "free_used", "extra_used",
                     "extra_member_price", "plan_key", "plan_name",
                     "can_add_free", "can_buy_extra"}
    shape_ok = ok and expected_keys.issubset(body.keys())
    log(shape_ok, "GET /me/team-members baseline",
        f"status={r.status_code} plan={body.get('plan_key')} cap={body.get('free_cap')} price={body.get('extra_member_price')}")

    if not (body.get("plan_key") == "gold" and body.get("extra_member_price") == 200 and body.get("free_cap") == 1):
        log(False, "Plan seed verification",
            f"plan={body.get('plan_key')} price={body.get('extra_member_price')} cap={body.get('free_cap')}")
        # Continue but findings won't match expected.

    # ── 2. POST /me/team-members (free quota — happy path) ────────────────
    r = _post("/me/team-members", headers=H, json={
        "name": "Riya Sharma", "phone": "9988776655",
        "role": "Logistics Assistant", "permissions": ["shipments.read"],
    })
    ok = r.status_code == 200
    free_member = r.json() if ok else {}
    log(ok and free_member.get("paid_extra") is False,
        "POST /me/team-members (free)",
        f"status={r.status_code} id={(free_member.get('id') or '')[:8]} paid_extra={free_member.get('paid_extra')}")
    free_member_id = free_member.get("id")

    # ── 3. POST /me/team-members again (cap reached → 402) ────────────────
    r = _post("/me/team-members", headers=H, json={
        "name": "Mehul Patel", "phone": "9911223344", "role": "Manager", "permissions": [],
    })
    ok = r.status_code == 402
    detail = r.json().get("detail") if r.status_code == 402 else r.text
    log(ok, "POST /me/team-members (cap reached → 402)",
        f"status={r.status_code} code={(detail or {}).get('code') if isinstance(detail, dict) else ''}")

    # ── 4. pay-extra wallet INSUFFICIENT → 402 ────────────────────────────
    r = _post("/me/team-members/pay-extra", headers=H, json={"method": "wallet"})
    ok = r.status_code == 402
    log(ok, "pay-extra wallet INSUFFICIENT → 402",
        f"status={r.status_code} body={r.text[:140]}")

    # Seed wallet balance via Mongo (the team_members.py wallet path reads
    # `wallets.balance` field directly — distinct from the credits ledger).
    db.wallets.update_one(
        {"user_id": user_uid},
        {"$set": {"balance": 500, "updated_at": _now_iso()},
         "$setOnInsert": {"user_id": user_uid, "created_at": _now_iso()}},
        upsert=True,
    )
    log(True, "Seed: wallet.balance=₹500", "via Mongo (covers ₹200 extra-member price)")

    # ── 5. pay-extra wallet SUFFICIENT → 200 ──────────────────────────────
    r = _post("/me/team-members/pay-extra", headers=H, json={"method": "wallet"})
    ok = r.status_code == 200
    body = r.json() if ok else {}
    wallet_slot = body.get("slot_token")
    log(ok and wallet_slot and body.get("method") == "wallet" and body.get("amount") == 200,
        "pay-extra wallet SUFFICIENT → 200",
        f"status={r.status_code} slot={str(wallet_slot)[:8]} amount={body.get('amount')} method={body.get('method')}")

    wal = db.wallets.find_one({"user_id": user_uid})
    log((wal or {}).get("balance") == 300,
        "Wallet decremented after wallet pay-extra",
        f"balance={(wal or {}).get('balance')} (expected 300)")

    tok = db.team_extra_tokens.find_one({"id": wallet_slot}) if wallet_slot else None
    log(tok and tok.get("paid") is True and tok.get("method") == "wallet" and tok.get("consumed") is False,
        "team_extra_tokens row (wallet) paid=True consumed=False",
        f"paid={tok and tok.get('paid')} method={tok and tok.get('method')} consumed={tok and tok.get('consumed')}")

    # ── 6. pay-extra razorpay → 200 with real Razorpay Checkout payload ──
    r = _post("/me/team-members/pay-extra", headers=H, json={"method": "razorpay"})
    ok = r.status_code == 200
    rzp_pay = r.json() if ok else {}
    rzp_required_keys = {"razorpay_order_id", "key_id", "amount_paise", "currency",
                         "receipt", "user_email", "user_name", "slot_token", "amount", "method"}
    shape_ok = ok and rzp_required_keys.issubset(rzp_pay.keys()) and rzp_pay.get("method") == "razorpay"
    real_order = isinstance(rzp_pay.get("razorpay_order_id"), str) and rzp_pay["razorpay_order_id"].startswith("order_")
    log(shape_ok and real_order,
        "pay-extra razorpay → 200 with REAL Razorpay payload (not mock)",
        f"status={r.status_code} order_id={rzp_pay.get('razorpay_order_id')} amount_paise={rzp_pay.get('amount_paise')} currency={rzp_pay.get('currency')} key_id_prefix={(rzp_pay.get('key_id') or '')[:14]}")
    rzp_slot = rzp_pay.get("slot_token")
    rzp_order_id = rzp_pay.get("razorpay_order_id")

    tok2 = db.team_extra_tokens.find_one({"id": rzp_slot}) if rzp_slot else None
    log(tok2 and tok2.get("paid") is False and tok2.get("method") == "razorpay"
        and tok2.get("razorpay_order_id") == rzp_order_id,
        "team_extra_tokens row (razorpay) paid=False",
        f"paid={tok2 and tok2.get('paid')} method={tok2 and tok2.get('method')} order_id_match={tok2 and tok2.get('razorpay_order_id')==rzp_order_id}")

    rzp_o = db.razorpay_orders.find_one({"razorpay_order_id": rzp_order_id}) if rzp_order_id else None
    log(rzp_o and rzp_o.get("purpose") == "team_extra_member" and rzp_o.get("status") == "created"
        and rzp_o.get("user_id") == user_uid and rzp_o.get("slot_token") == rzp_slot,
        "razorpay_orders ledger row created",
        f"purpose={rzp_o and rzp_o.get('purpose')} status={rzp_o and rzp_o.get('status')} slot_match={rzp_o and rzp_o.get('slot_token')==rzp_slot}")

    # ── 7. verify BOGUS signature → 400 ───────────────────────────────────
    fake_payment_id = f"pay_FAKE{uuid.uuid4().hex[:14]}"
    r = _post("/me/team-members/razorpay/verify", headers=H, json={
        "razorpay_order_id":   rzp_order_id,
        "razorpay_payment_id": fake_payment_id,
        "razorpay_signature":  "bogus_sig_" + uuid.uuid4().hex,
    })
    ok = r.status_code == 400
    log(ok, "verify BOGUS sig → 400 (NOT 500)",
        f"status={r.status_code} detail={r.text[:200]}")

    tok2b = db.team_extra_tokens.find_one({"id": rzp_slot})
    log((tok2b or {}).get("paid") is False,
        "Token still paid=False after bogus sig (no leak)",
        f"paid={(tok2b or {}).get('paid')}")

    # ── 8. verify wrong-USER order → 404 ──────────────────────────────────
    r = _post("/me/team-members/razorpay/verify", headers=admin_H, json={
        "razorpay_order_id":   rzp_order_id,
        "razorpay_payment_id": fake_payment_id,
        "razorpay_signature":  "any",
    })
    log(r.status_code == 404, "verify wrong-user order → 404",
        f"status={r.status_code} detail={r.text[:160]}")

    # ── 9. verify wrong-PURPOSE order (use a wallet/razorpay/create-order) ─
    r = _post("/wallet/razorpay/create-order", headers=H, json={"amount_inr": 100})
    ok = r.status_code == 200
    wallet_order_payload = r.json() if ok else {}
    wallet_rzp_order_id = wallet_order_payload.get("order_id")
    log(ok and wallet_rzp_order_id and wallet_rzp_order_id.startswith("order_"),
        "POST /wallet/razorpay/create-order (smoke + setup)",
        f"status={r.status_code} order_id={wallet_rzp_order_id}")

    if wallet_rzp_order_id:
        r = _post("/me/team-members/razorpay/verify", headers=H, json={
            "razorpay_order_id":   wallet_rzp_order_id,
            "razorpay_payment_id": fake_payment_id,
            "razorpay_signature":  "any",
        })
        ok = r.status_code == 400 and "team-member slot" in r.text.lower()
        log(ok, "verify wrong-PURPOSE order (wallet topup) → 400",
            f"status={r.status_code} detail={r.text[:200]}")

    # ── 10. with-extra UNPAID rzp token → 402 ─────────────────────────────
    r = _post("/me/team-members/with-extra", headers=H, json={
        "name": "Aniket Bose", "phone": "9111122222",
        "role": "Dispatch", "permissions": [],
        "slot_token": rzp_slot,
    })
    ok = r.status_code == 402 and "not been paid" in r.text.lower()
    log(ok, "with-extra UNPAID rzp token → 402 (security gap closed)",
        f"status={r.status_code} detail={r.text[:200]}")

    # ── 11. with-extra INVALID slot_token (random uuid) → 400 ─────────────
    r = _post("/me/team-members/with-extra", headers=H, json={
        "name": "Random User", "phone": "9333344444",
        "role": "X", "permissions": [],
        "slot_token": str(uuid.uuid4()),
    })
    log(r.status_code == 400, "with-extra INVALID slot_token → 400",
        f"status={r.status_code} detail={r.text[:160]}")

    # ── 12. verify VALID signature → 200 (HMAC = razorpay.utility.generate_signature) ─
    valid_payment_id = f"pay_TEST{uuid.uuid4().hex[:14]}"
    valid_sig = hmac_signature(rzp_order_id, valid_payment_id, RZP_KEY_SECRET)
    r = _post("/me/team-members/razorpay/verify", headers=H, json={
        "razorpay_order_id":   rzp_order_id,
        "razorpay_payment_id": valid_payment_id,
        "razorpay_signature":  valid_sig,
    })
    ok = r.status_code == 200
    body = r.json() if ok else {}
    happy_ok = ok and body.get("ok") is True and body.get("already_credited") is False \
        and body.get("slot_token") == rzp_slot and body.get("amount") == 200
    log(happy_ok, "verify VALID sig → 200, already_credited=false",
        f"status={r.status_code} body={body if ok else r.text[:200]}")

    tok2c = db.team_extra_tokens.find_one({"id": rzp_slot})
    log((tok2c or {}).get("paid") is True
        and (tok2c or {}).get("razorpay_payment_id") == valid_payment_id,
        "team_extra_tokens.paid → True after valid verify",
        f"paid={(tok2c or {}).get('paid')} payment_id_match={(tok2c or {}).get('razorpay_payment_id')==valid_payment_id}")

    rzp_o2 = db.razorpay_orders.find_one({"razorpay_order_id": rzp_order_id})
    log((rzp_o2 or {}).get("status") == "paid"
        and (rzp_o2 or {}).get("razorpay_payment_id") == valid_payment_id,
        "razorpay_orders.status → 'paid'",
        f"status={(rzp_o2 or {}).get('status')} payment_id_match={(rzp_o2 or {}).get('razorpay_payment_id')==valid_payment_id}")

    # ── 13. verify already-paid order → 200 already_credited=true ─────────
    r = _post("/me/team-members/razorpay/verify", headers=H, json={
        "razorpay_order_id":   rzp_order_id,
        "razorpay_payment_id": valid_payment_id,
        "razorpay_signature":  valid_sig,
    })
    ok = r.status_code == 200
    body = r.json() if ok else {}
    log(ok and body.get("already_credited") is True and body.get("slot_token") == rzp_slot,
        "verify already-paid → 200 already_credited=true (idempotent, no double-flip)",
        f"status={r.status_code} body={body if ok else r.text[:200]}")

    # ── 14. with-extra wallet-paid token → 200 ────────────────────────────
    r = _post("/me/team-members/with-extra", headers=H, json={
        "name": "Wallet Member", "phone": "9444455555",
        "role": "Packer", "permissions": ["shipments.read"],
        "slot_token": wallet_slot,
    })
    ok = r.status_code == 200
    body = r.json() if ok else {}
    wallet_member_id = body.get("id") if ok else None
    log(ok and body.get("paid_extra") is True and body.get("extra_token") == wallet_slot,
        "with-extra WALLET-paid token → 200, member created",
        f"status={r.status_code} id={(wallet_member_id or '')[:8]} paid_extra={body.get('paid_extra')}")

    # ── 15. with-extra razorpay-paid token → 200 ─────────────────────────
    r = _post("/me/team-members/with-extra", headers=H, json={
        "name": "Razorpay Member", "phone": "9555566666",
        "role": "Sales", "permissions": [],
        "slot_token": rzp_slot,
    })
    ok = r.status_code == 200
    body = r.json() if ok else {}
    rzp_member_id = body.get("id") if ok else None
    log(ok and body.get("paid_extra") is True and body.get("extra_token") == rzp_slot,
        "with-extra RAZORPAY-paid token → 200, member created",
        f"status={r.status_code} id={(rzp_member_id or '')[:8]} paid_extra={body.get('paid_extra')}")

    tok2d = db.team_extra_tokens.find_one({"id": rzp_slot})
    log((tok2d or {}).get("consumed") is True,
        "Razorpay slot token marked consumed=True",
        f"consumed={(tok2d or {}).get('consumed')}")

    # ── 16. with-extra ALREADY-consumed token → 400 ───────────────────────
    r = _post("/me/team-members/with-extra", headers=H, json={
        "name": "Dup Attempt", "phone": "9666677777",
        "role": "X", "permissions": [],
        "slot_token": rzp_slot,
    })
    ok = r.status_code == 400 and "already" in r.text.lower()
    log(ok, "with-extra ALREADY-consumed token → 400",
        f"status={r.status_code} detail={r.text[:160]}")

    # ── 17. PUT /me/team-members/{id} ─────────────────────────────────────
    r = _put(f"/me/team-members/{free_member_id}", headers=H,
             json={"role": "Updated Role", "permissions": ["shipments.read", "shipments.write"]})
    ok = r.status_code == 200 and r.json().get("role") == "Updated Role"
    log(ok, "PUT /me/team-members/{id}", f"status={r.status_code}")

    # ── 18. DELETE /me/team-members/{id} ──────────────────────────────────
    r = _del(f"/me/team-members/{wallet_member_id}", headers=H)
    log(r.status_code == 200, "DELETE /me/team-members/{id}",
        f"status={r.status_code}")

    # ── 19. POST /api/team/login (Phase B+C) ──────────────────────────────
    # Bump plan to platinum (cap=2) so we can add a NEW free member with email/pwd.
    db.users.update_one({"id": user_uid}, {"$set": {"plan": "platinum"}})
    team_email = f"teamlogin_{uuid.uuid4().hex[:6]}@test.com"
    team_pwd = "Teammate@12345"
    r = _post("/me/team-members", headers=H, json={
        "name": "Login Tester", "phone": "9777788888", "role": "Login",
        "permissions": ["shipments.read"], "email": team_email, "password": team_pwd,
    })
    member_for_login_ok = r.status_code == 200
    log(member_for_login_ok, "Setup: create team member with email+password",
        f"status={r.status_code} email={team_email}")

    if member_for_login_ok:
        r = _post("/team/login", json={"email": team_email, "password": team_pwd})
        ok = r.status_code == 200
        body = r.json() if ok else {}
        log(ok and body.get("kind") == "team" and body.get("token")
            and (body.get("team_member") or {}).get("permissions") == ["shipments.read"],
            "POST /api/team/login (Phase B+C)",
            f"status={r.status_code} kind={body.get('kind')} parent={body.get('parent_business')}")

        r = _post("/team/login", json={"email": team_email, "password": "WrongPwd@12345"})
        log(r.status_code == 401, "POST /api/team/login bad password → 401",
            f"status={r.status_code}")

    # ── 20. Smoke: POST /wallet/razorpay/verify (bogus sig) ───────────────
    if wallet_rzp_order_id:
        r = _post("/wallet/razorpay/verify", headers=H, json={
            "razorpay_order_id":   wallet_rzp_order_id,
            "razorpay_payment_id": fake_payment_id,
            "razorpay_signature":  "bogus_" + uuid.uuid4().hex,
        })
        log(r.status_code == 400, "POST /wallet/razorpay/verify (bogus sig) → 400 (regression)",
            f"status={r.status_code}")

    # ── 21. Smoke: POST /plans/razorpay/create-order ──────────────────────
    r = _post("/plans/razorpay/create-order", headers=H, json={
        "plan_key": "gold", "billing_cycle": "monthly",
    })
    ok = r.status_code == 200
    plan_payload = r.json() if ok else {}
    plan_order_id = plan_payload.get("order_id") or plan_payload.get("razorpay_order_id")
    log(ok and isinstance(plan_order_id, str) and plan_order_id.startswith("order_"),
        "POST /plans/razorpay/create-order (smoke regression)",
        f"status={r.status_code} order_id={plan_order_id}")

    # ── 22. Smoke: POST /plans/razorpay/verify (bogus sig) ────────────────
    if plan_order_id:
        r = _post("/plans/razorpay/verify", headers=H, json={
            "razorpay_order_id":   plan_order_id,
            "razorpay_payment_id": fake_payment_id,
            "razorpay_signature":  "bogus_" + uuid.uuid4().hex,
        })
        log(r.status_code == 400, "POST /plans/razorpay/verify (bogus sig) → 400 (regression)",
            f"status={r.status_code}")

    # ── 23. Final GET /me/team-members ────────────────────────────────────
    r = _get("/me/team-members", headers=H)
    ok = r.status_code == 200
    body = r.json() if ok else {}
    log(ok and isinstance(body.get("members"), list),
        "GET /me/team-members final state",
        f"status={r.status_code} members={len(body.get('members') or [])} free_used={body.get('free_used')} extra_used={body.get('extra_used')}")

    # ── Cleanup ───────────────────────────────────────────────────────────
    try:
        db.users.delete_one({"id": user_uid})
        db.team_members.delete_many({"user_id": user_uid})
        db.team_extra_tokens.delete_many({"user_id": user_uid})
        db.razorpay_orders.delete_many({"user_id": user_uid})
        db.wallets.delete_many({"user_id": user_uid})
        db.credit_history.delete_many({"user_id": user_uid})
        log(True, "Cleanup", f"removed all docs for fresh user {user_uid[:8]}…")
    except Exception as e:
        log(False, "Cleanup", str(e))

    # ── Summary ───────────────────────────────────────────────────────────
    passed = sum(1 for ok, _, _ in results if ok)
    failed = sum(1 for ok, _, _ in results if not ok)
    print(f"\n=== {passed} passed, {failed} failed of {len(results)} ===")
    if failed:
        print("\n--- FAILED ---")
        for ok, name, detail in results:
            if not ok:
                print(f"  - {name} :: {detail}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
