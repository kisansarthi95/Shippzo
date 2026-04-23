"""
Phase 1 Multi-Tenant Auth + user_id Data Isolation Test Suite
Tests the Courier Label Manager FastAPI backend for:
  1. Auth endpoints (login/signup/me)
  2. JWT middleware (auth_gate)
  3. Data isolation (cross-tenant) — CRITICAL
  4. Couriers isolation
  5. Per-user Settings
  6. Stats isolation
  7. Smart-Paste + pending isolation
  8. Demo clear

Base URL: EXPO_PUBLIC_BACKEND_URL + /api
"""
from __future__ import annotations

import sys
from typing import Any, Dict, List, Optional, Tuple

import requests

BASE = "https://logistics-hub-740.preview.emergentagent.com/api"

ADMIN_EMAIL = "admin@test.com"
ADMIN_PW = "Admin@12345"
USER2_EMAIL = "user2@test.com"
USER2_PW = "User@12345"

TIMEOUT = 30

_pass = 0
_fail = 0
_failures: List[str] = []
_section = "(setup)"


def _log(msg: str):
    print(msg, flush=True)


def section(name: str):
    global _section
    _section = name
    _log(f"\n========== {name} ==========")


def expect(cond: bool, label: str, extra: Any = None):
    global _pass, _fail
    if cond:
        _pass += 1
        _log(f"  PASS  {label}")
    else:
        _fail += 1
        msg = f"[{_section}] {label}"
        if extra is not None:
            msg += f" :: {extra}"
        _failures.append(msg)
        _log(f"  FAIL  {label} :: {extra}")


def H(tok: Optional[str]) -> Dict[str, str]:
    if tok:
        return {"Authorization": f"Bearer {tok}"}
    return {}


def _req(method: str, path: str, tok: Optional[str] = None, json_body: Any = None, **kwargs) -> requests.Response:
    url = BASE + path
    headers = kwargs.pop("headers", {}) or {}
    headers.update(H(tok))
    r = requests.request(method, url, headers=headers, json=json_body, timeout=TIMEOUT, **kwargs)
    return r


def test_auth():
    section("1. Auth Endpoints")

    r = _req("POST", "/auth/login", json_body={"email": ADMIN_EMAIL, "password": ADMIN_PW})
    expect(r.status_code == 200, "POST /auth/login admin -> 200", r.status_code)
    admin_body = r.json() if r.ok else {}
    admin_tok = admin_body.get("token")
    expect(bool(admin_tok), "admin token present")
    expect(admin_body.get("is_admin") is True, "admin.is_admin == True", admin_body.get("is_admin"))
    expect(admin_body.get("email") == ADMIN_EMAIL, "admin.email matches")
    for k in ("id", "email", "plan", "created_at", "shop_name", "name"):
        expect(k in admin_body, f"admin login body has '{k}'")
    expect("password_hash" not in admin_body, "admin login body has no password_hash")

    r = _req("POST", "/auth/login", json_body={"email": USER2_EMAIL, "password": USER2_PW})
    expect(r.status_code == 200, "POST /auth/login user2 -> 200", r.status_code)
    user2_body = r.json() if r.ok else {}
    user2_tok = user2_body.get("token")
    expect(bool(user2_tok), "user2 token present")
    expect(user2_body.get("is_admin") is False, "user2.is_admin == False", user2_body.get("is_admin"))

    r = _req("POST", "/auth/login", json_body={"email": ADMIN_EMAIL, "password": "WRONG!!!"})
    expect(r.status_code == 401, "Wrong password -> 401", r.status_code)
    try:
        expect(r.json().get("detail") == "Invalid email or password",
               "detail == 'Invalid email or password'", r.text)
    except Exception:
        expect(False, "wrong-pw response JSON parseable", r.text)

    r = _req("POST", "/auth/signup", json_body={
        "email": ADMIN_EMAIL, "password": "Anything@1", "name": "x", "shop_name": "y"
    })
    expect(r.status_code == 400, "Signup w/ existing email -> 400", r.status_code)
    try:
        expect(r.json().get("detail") == "Email already registered",
               "detail == 'Email already registered'", r.text)
    except Exception:
        expect(False, "existing-email response JSON", r.text)

    r = _req("GET", "/auth/me", tok=admin_tok)
    expect(r.status_code == 200, "GET /auth/me admin -> 200", r.status_code)
    me_a = r.json() if r.ok else {}
    expect(me_a.get("email") == ADMIN_EMAIL, "me.email == admin")
    expect(me_a.get("is_admin") is True, "me.is_admin True for admin")
    expect("password_hash" not in me_a, "me admin has no password_hash")

    r = _req("GET", "/auth/me", tok=user2_tok)
    expect(r.status_code == 200, "GET /auth/me user2 -> 200", r.status_code)
    me_b = r.json() if r.ok else {}
    expect(me_b.get("email") == USER2_EMAIL, "me.email == user2")
    expect("password_hash" not in me_b, "me user2 has no password_hash")

    return admin_tok, user2_tok


def test_auth_gate(admin_tok: str):
    section("2. JWT Middleware (auth_gate)")

    r = _req("GET", "/shipments")
    expect(r.status_code == 401, "GET /shipments w/o token -> 401", r.status_code)
    www = r.headers.get("www-authenticate") or r.headers.get("WWW-Authenticate") or ""
    expect(www.lower().startswith("bearer"), "WWW-Authenticate: Bearer header present", www)

    r = _req("GET", "/couriers")
    expect(r.status_code == 401, "GET /couriers w/o token -> 401", r.status_code)

    r = _req("GET", "/settings")
    expect(r.status_code == 401, "GET /settings w/o token -> 401", r.status_code)

    r = _req("POST", "/shipments/bulk-fetch", json_body={"ids": []})
    expect(r.status_code == 401, "POST /shipments/bulk-fetch w/o token -> 401", r.status_code)

    r = _req("GET", "/shipments", tok="notavalidjwt.garbage.token")
    expect(r.status_code == 401, "Garbage token -> 401", r.status_code)
    detail = ""
    try:
        detail = r.json().get("detail", "")
    except Exception:
        pass
    expect(detail.startswith("Invalid token") or detail == "Token expired",
           "Garbage token detail says 'Invalid token...' or 'Token expired'", detail)

    r = _req("GET", "/shipments", tok=admin_tok)
    expect(r.status_code == 200, "Valid token on /shipments -> 200", r.status_code)


def test_shipment_isolation(admin_tok: str, user2_tok: str) -> Tuple[List[dict], List[dict]]:
    section("3. Shipment Data Isolation")

    r = _req("GET", "/shipments", tok=admin_tok)
    expect(r.status_code == 200, "admin GET /shipments -> 200", r.status_code)
    admin_ships = r.json() if r.ok else []
    expect(isinstance(admin_ships, list), "admin shipments is a list")
    expect(len(admin_ships) >= 50, f"admin has >=50 shipments (got {len(admin_ships)})")
    demo_in_admin = [s for s in admin_ships if s.get("is_demo") is True]
    expect(len(demo_in_admin) == 0,
           f"no admin shipment has is_demo=True (found {len(demo_in_admin)})")

    r = _req("GET", "/shipments", tok=user2_tok)
    expect(r.status_code == 200, "user2 GET /shipments -> 200", r.status_code)
    user2_ships = r.json() if r.ok else []
    expect(len(user2_ships) in (15, 0),
           f"user2 has 15 shipments (or 0 if already demo-cleared) got {len(user2_ships)}")

    if user2_ships:
        one = user2_ships[0]
        expect(one.get("is_demo") is True,
               "at least one user2 shipment flagged is_demo=True", one.get("is_demo"))

    if admin_ships:
        a_ship = admin_ships[0]
        a_id = a_ship.get("id")
        a_tracking = a_ship.get("tracking_id")

        r = _req("GET", f"/shipments/{a_id}", tok=user2_tok)
        expect(r.status_code == 404,
               f"user2 GET /shipments/{{admin_id}} -> 404 (got {r.status_code})",
               r.text[:200])

        r = _req("PUT", f"/shipments/{a_id}", tok=user2_tok,
                 json_body={"status": "Delivered"})
        expect(r.status_code == 404,
               f"user2 PUT /shipments/{{admin_id}} -> 404 (got {r.status_code})",
               r.text[:200])

        r = _req("DELETE", f"/shipments/{a_id}", tok=user2_tok)
        expect(r.status_code == 404,
               f"user2 DELETE /shipments/{{admin_id}} -> 404 (got {r.status_code})",
               r.text[:200])

        r = _req("GET", f"/shipments/{a_id}", tok=admin_tok)
        expect(r.status_code == 200,
               "admin GET /shipments/{admin_id} still 200 after user2 DELETE attempt",
               r.status_code)

        if a_tracking:
            r = _req("GET", f"/shipments/by-tracking/{a_tracking}", tok=user2_tok)
            expect(r.status_code == 404,
                   f"user2 GET /shipments/by-tracking/{{admin}} -> 404 (got {r.status_code})",
                   r.text[:200])
            r = _req("GET", f"/shipments/by-tracking/{a_tracking}", tok=admin_tok)
            expect(r.status_code == 200, "admin by-tracking on own tracking -> 200", r.status_code)

        ids = [s["id"] for s in admin_ships[:5] if s.get("id")]
        r = _req("POST", "/shipments/bulk-fetch", tok=user2_tok, json_body={"ids": ids})
        expect(r.status_code == 200, "user2 bulk-fetch admin ids -> 200", r.status_code)
        body = r.json() if r.ok else None
        expect(isinstance(body, list), "bulk-fetch returns list", type(body).__name__)
        expect(len(body or []) == 0,
               f"bulk-fetch with admin ids on user2 returns empty (got {len(body or [])})")
    else:
        _log("  (no admin shipments -> skipping cross-tenant id checks)")

    return admin_ships, user2_ships


def test_couriers(admin_tok: str, user2_tok: str):
    section("4. Couriers Isolation")

    r = _req("GET", "/couriers", tok=admin_tok)
    expect(r.status_code == 200, "admin GET /couriers -> 200", r.status_code)
    admin_couriers = r.json() if r.ok else []
    admin_names = [c.get("name", "") for c in admin_couriers]
    expect(len(admin_couriers) >= 1, f"admin has >=1 courier (got {len(admin_couriers)})")
    has_nandan = any("nandan" in n.lower() for n in admin_names)
    expect(has_nandan, f"admin couriers include seeded default; got {admin_names}")

    r = _req("GET", "/couriers", tok=user2_tok)
    expect(r.status_code == 200, "user2 GET /couriers -> 200", r.status_code)
    u2_couriers = r.json() if r.ok else []
    u2_names = [c.get("name", "") for c in u2_couriers]
    has_demo = any("demo" in n.lower() for n in u2_names)
    expect(has_demo, f"user2 couriers include 'Demo Courier'; got {u2_names}")
    admin_exclusive = [n for n in u2_names if n in ("DTDC", "India Post", "Delhivery", "Nandan Courier")]
    expect(len(admin_exclusive) == 0,
           f"user2 does not have admin's seeded couriers (found {admin_exclusive})")

    tag_name = "__ISOTEST__User2Courier"
    r = _req("POST", "/couriers", tok=user2_tok, json_body={
        "name": tag_name, "series_prefix": "U2", "next_number": 1,
        "number_padding": 4, "contact_phone": "9999999999",
    })
    expect(r.status_code == 200, "user2 POST /couriers -> 200", r.status_code)
    new_cid = (r.json() or {}).get("id") if r.ok else None
    expect(bool(new_cid), "new courier id returned", r.text[:200])

    if new_cid:
        r = _req("GET", "/couriers", tok=user2_tok)
        names_now = [c.get("name") for c in (r.json() if r.ok else [])]
        expect(tag_name in names_now, f"user2 sees own new courier; got {names_now}")

        r = _req("GET", "/couriers", tok=admin_tok)
        admin_names_now = [c.get("name") for c in (r.json() if r.ok else [])]
        expect(tag_name not in admin_names_now,
               f"admin does NOT see user2's courier ({tag_name} not in admin list)")

        r = _req("GET", f"/couriers/{new_cid}", tok=admin_tok)
        expect(r.status_code == 404,
               f"admin GET /couriers/{{user2_cid}} -> 404 (got {r.status_code})", r.text[:200])

        r = _req("DELETE", f"/couriers/{new_cid}", tok=user2_tok)
        expect(r.status_code in (200, 204), f"user2 DELETE own courier -> OK (got {r.status_code})")


def test_settings(admin_tok: str, user2_tok: str):
    section("5. Per-User Settings")

    r = _req("PUT", "/settings", tok=admin_tok, json_body={"default_eta_days": 10})
    expect(r.status_code == 200, "admin PUT /settings (eta=10) -> 200", r.status_code)

    r = _req("PUT", "/settings", tok=user2_tok, json_body={"default_eta_days": 3})
    expect(r.status_code == 200, "user2 PUT /settings (eta=3) -> 200", r.status_code)

    r = _req("GET", "/settings", tok=admin_tok)
    expect(r.status_code == 200, "admin GET /settings -> 200", r.status_code)
    a = r.json() if r.ok else {}
    expect(a.get("default_eta_days") == 10,
           f"admin settings.default_eta_days == 10 (got {a.get('default_eta_days')})")

    r = _req("GET", "/settings", tok=user2_tok)
    expect(r.status_code == 200, "user2 GET /settings -> 200", r.status_code)
    b = r.json() if r.ok else {}
    expect(b.get("default_eta_days") == 3,
           f"user2 settings.default_eta_days == 3 (got {b.get('default_eta_days')})")

    expect(a.get("default_eta_days") != b.get("default_eta_days"),
           "admin and user2 settings are independent")


def test_stats(admin_tok: str, user2_tok: str):
    section("6. Stats Isolation")

    r = _req("GET", "/shipments/stats", tok=admin_tok)
    expect(r.status_code == 200, "admin GET /shipments/stats -> 200", r.status_code)
    s_a = r.json() if r.ok else {}
    expect(s_a.get("total", 0) >= 50, f"admin stats.total >= 50 (got {s_a.get('total')})")

    r = _req("GET", "/shipments/stats", tok=user2_tok)
    expect(r.status_code == 200, "user2 GET /shipments/stats -> 200", r.status_code)
    s_b = r.json() if r.ok else {}
    expect(s_b.get("total", -1) in (15, 0),
           f"user2 stats.total in (15, 0) (got {s_b.get('total')})")

    cod = float(s_b.get("cod_total", 0) or 0)
    prepaid = float(s_b.get("prepaid_total", 0) or 0)
    rev = float(s_b.get("revenue_total", -1) or -1)
    expect(abs((cod + prepaid) - rev) < 0.001,
           f"user2 cod_total+prepaid_total == revenue_total ({cod}+{prepaid} vs {rev})")


def test_smart_paste(admin_tok: str, user2_tok: str, admin_ships: List[dict]):
    section("7. Smart-Paste Duplicate & Pending Isolation")

    admin_phone = ""
    for s in admin_ships:
        p = (s.get("customer_phone") or "").strip()
        if p and len(p) >= 10:
            admin_phone = p
            break

    if admin_phone:
        paste_text = (
            f"Name: Cross Tenant Probe\nPhone: {admin_phone}\n"
            "Address: 1 Test Lane\nCity: Ahmedabad\nState: Gujarat\n"
            "Pincode: 380001\nItem: Probe\nAmount: 10\nPayment: Prepaid"
        )
        r = _req("POST", "/smart-paste/check-duplicate", tok=user2_tok,
                 json_body={"text": paste_text})
        expect(r.status_code == 200,
               "user2 /smart-paste/check-duplicate w/ admin's phone -> 200",
               r.status_code)
        body = r.json() if r.ok else {}
        dups = body.get("duplicates") or []
        admin_ids = {s.get("id") for s in admin_ships}
        leaked = [d for d in dups if d.get("id") in admin_ids]
        expect(len(leaked) == 0,
               f"no admin shipment id leaks into user2 duplicates (found {len(leaked)})")
        parsed_phone = (body.get("fields") or {}).get("customer_phone", "")
        expect(parsed_phone.endswith(admin_phone[-10:]) or parsed_phone == admin_phone,
               f"parsed phone matches input ({parsed_phone})")
    else:
        _log("  (no admin phone available -> skipping cross-tenant dup check)")

    r = _req("GET", "/orders/pending-count", tok=admin_tok)
    expect(r.status_code == 200, "admin /orders/pending-count -> 200", r.status_code)
    r = _req("GET", "/orders/pending-count", tok=user2_tok)
    expect(r.status_code == 200, "user2 /orders/pending-count -> 200", r.status_code)

    r = _req("GET", "/orders/pending", tok=user2_tok)
    expect(r.status_code == 200, "user2 /orders/pending -> 200", r.status_code)


def test_demo_clear(admin_tok: str, user2_tok: str, admin_ships_before: List[dict]):
    section("8. Demo Clear (user2)")

    r = _req("POST", "/demo/clear", tok=user2_tok)
    expect(r.status_code == 200, "user2 POST /demo/clear -> 200", r.status_code)
    body = r.json() if r.ok else {}
    expect(body.get("ok") is True, f"demo/clear returns ok=true (got {body})")
    deleted = body.get("deleted", -1)
    expect(deleted in (15, 0),
           f"demo/clear deleted in (15, 0) (got {deleted})")

    r = _req("GET", "/shipments", tok=user2_tok)
    u2_now = r.json() if r.ok else []
    expect(len(u2_now) == 0, f"user2 shipments == 0 after demo/clear (got {len(u2_now)})")

    r = _req("GET", "/shipments", tok=admin_tok)
    admin_now = r.json() if r.ok else []
    expect(len(admin_now) == len(admin_ships_before),
           f"admin shipment count unchanged (before={len(admin_ships_before)}, after={len(admin_now)})")

    r = _req("POST", "/demo/clear", tok=user2_tok)
    expect(r.status_code == 200, "second user2 /demo/clear -> 200", r.status_code)
    body2 = r.json() if r.ok else {}
    expect(body2.get("deleted") == 0,
           f"idempotent /demo/clear deleted==0 (got {body2.get('deleted')})")


def main():
    _log(f"Base URL: {BASE}")
    admin_tok, user2_tok = test_auth()
    if not (admin_tok and user2_tok):
        _log("FATAL: cannot login - aborting")
        sys.exit(2)

    test_auth_gate(admin_tok)
    admin_ships, user2_ships = test_shipment_isolation(admin_tok, user2_tok)
    test_couriers(admin_tok, user2_tok)
    test_settings(admin_tok, user2_tok)
    test_stats(admin_tok, user2_tok)
    test_smart_paste(admin_tok, user2_tok, admin_ships)
    test_demo_clear(admin_tok, user2_tok, admin_ships)

    _log("\n" + "=" * 60)
    _log(f"TOTAL: {_pass + _fail} assertions | PASS: {_pass} | FAIL: {_fail}")
    if _failures:
        _log("\nFAILURES:")
        for f in _failures:
            _log(f"  - {f}")
    sys.exit(0 if _fail == 0 else 1)


if __name__ == "__main__":
    main()
