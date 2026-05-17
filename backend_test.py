"""
Phase-23 — Manual Tracking Workflow (India Post / Anjani sticker support)
Verifies POST /api/orders/pending/{id}/ship with manual_tracking couriers.

Scenarios:
  A — Existing courier regression (auto-sequence still works)
  B — Manual tracking ON (errors, raw passthrough, no counter increment)
  C — Born-manual courier + edge cases (whitespace, cross-user 404)
"""
import os
import re
import sys
import json
import uuid
import requests
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from pymongo import MongoClient

BASE = (
    os.environ.get("BACKEND_URL")
    or "https://logistics-hub-740.preview.emergentagent.com"
).rstrip("/")
API = f"{BASE}/api"

USER_EMAIL = "user2@test.com"
USER_PASS  = "User@12345"

# Used only for Scenario C step 5 (cross-user 404 check)
ADMIN_EMAIL = "admin@test.com"
ADMIN_PASS  = "Admin@12345"

# Direct Mongo handle to insert PendingOrder fixtures. The /api/smart-paste
# create path is blocked on this preview workspace by an unrelated
# Google Sheet config issue ("All Master Data" tab missing); inserting
# the doc directly bypasses that side-effect and is functionally
# equivalent for the ship_pending_order flow under test.
_mongo = MongoClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
_dbname = os.environ.get("DB_NAME", "test_database")
_db = _mongo[_dbname]
USER_ID: Optional[str] = None   # filled in by main() after login


# ─── helpers ───────────────────────────────────────────────────────────

def login(email: str, password: str) -> str:
    r = requests.post(
        f"{API}/auth/login",
        json={"email": email, "password": password},
        timeout=20,
    )
    if r.status_code != 200:
        raise SystemExit(f"login {email} failed → {r.status_code} {r.text}")
    return r.json()["token"]


def hdr(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


_results = []  # (label, ok, detail)


def check(label: str, cond: bool, detail: str = "") -> bool:
    tag = "✅" if cond else "❌"
    print(f"   {tag} {label}" + (f"  [{detail}]" if detail else ""))
    _results.append((label, cond, detail))
    return cond


def make_pending(token: str, customer_phone: str, name: str = "Reg Test Buyer") -> str:
    """Insert a PendingOrder doc directly into Mongo so the ship endpoint
    has something to operate on. Side-stepping /api/smart-paste because
    this user2 workspace has a stale Master-Sheet tab config that makes
    the smart-paste path return 502 (unrelated to Phase-23 logic).
    Returns the new PendingOrder.id.
    """
    assert USER_ID, "USER_ID must be set after login"
    pid = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "id":               pid,
        "user_id":          USER_ID,
        "source":           "manual",
        "status":           "pending",
        "master_order_id":  "",
        "order_id":         "",
        "customer_name":    name,
        "customer_phone":   customer_phone,
        "customer_alt_phone": "",
        "customer_email":   "",
        "customer_gstin":   "",
        "address_line1":    "Test addr",
        "address_line2":    "",
        "city":             "Test",
        "state":            "Test",
        "pincode":          "560001",
        "items":            "Test x1",
        "amount":           100,
        "token_amount":     0,
        "payment_mode":     "COD",
        "courier_hint":     "",
        "order_id_hint":    "",
        "weight":           "",
        "notes":            "",
        "raw_text":         "",
        "confidence":       {},
        "warnings":         [],
        "custom_values":    {},
        "created_at":       now,
        "imported_status":  "",
        "imported_at":      "",
        "box_dimensions":   "",
        "box_length":       0.0,
        "box_width":        0.0,
        "box_height":       0.0,
        "category":         "",
    }
    _db.pending_orders.insert_one(doc)
    return pid


def create_courier(token: str, body: Dict[str, Any]) -> Dict[str, Any]:
    r = requests.post(f"{API}/couriers", headers=hdr(token), json=body, timeout=20)
    if r.status_code != 200:
        print(f"      [POST /couriers → {r.status_code} {r.text}]")
    r.raise_for_status()
    return r.json()


def delete_courier(token: str, cid: str) -> int:
    r = requests.delete(f"{API}/couriers/{cid}", headers=hdr(token), timeout=20)
    return r.status_code


def free_courier_slot(token: str, keep_id: Optional[str] = None) -> None:
    """user2 is on silver plan (1-courier cap). Delete any existing
    couriers (except keep_id) so the test can create fresh test rows
    without hitting 403 'plan allows only 1 courier partner'."""
    r = requests.get(f"{API}/couriers", headers=hdr(token), timeout=20)
    r.raise_for_status()
    for c in r.json():
        if keep_id and c.get("id") == keep_id:
            continue
        sc = delete_courier(token, c["id"])
        print(f"      cleanup: deleted courier {c.get('name')!r} ({c.get('id')[:8]}…) → {sc}")


def patch_courier(token: str, cid: str, body: Dict[str, Any]) -> Dict[str, Any]:
    # CourierUpdate is exposed via PUT (server.py: update_courier @ PUT).
    r = requests.put(
        f"{API}/couriers/{cid}", headers=hdr(token), json=body, timeout=20,
    )
    if r.status_code != 200:
        print(f"      [PUT /couriers/{cid} → {r.status_code} {r.text}]")
    r.raise_for_status()
    return r.json()


def get_courier(token: str, cid: str) -> Tuple[int, Any]:
    r = requests.get(f"{API}/couriers/{cid}", headers=hdr(token), timeout=20)
    try:
        body = r.json()
    except Exception:
        body = r.text
    return r.status_code, body


def get_next_tracking(token: str, cid: str) -> Dict[str, Any]:
    r = requests.get(
        f"{API}/couriers/{cid}/next-tracking", headers=hdr(token), timeout=20
    )
    r.raise_for_status()
    return r.json()


def ship_pending(
    token: str,
    pid: str,
    courier_id: str,
    manual_tracking_id: Optional[str] = None,
) -> Tuple[int, Any]:
    body: Dict[str, Any] = {"courier_id": courier_id}
    if manual_tracking_id is not None:
        body["manual_tracking_id"] = manual_tracking_id
    r = requests.post(
        f"{API}/orders/pending/{pid}/ship",
        headers=hdr(token), json=body, timeout=30,
    )
    try:
        body_out = r.json()
    except Exception:
        body_out = r.text
    return r.status_code, body_out


# ─── scenarios ─────────────────────────────────────────────────────────

def scenario_A(token: str) -> str:
    print("\n────── SCENARIO A — Existing courier regression ──────")
    # 2) create courier
    c = create_courier(token, {
        "name":            "Phase23-RegTest",
        "series_prefix":   "RG",
        "next_number":     10,
        "number_padding":  4,
    })
    cid = c["id"]
    check("A2 created courier (manual_tracking default false)", c.get("manual_tracking") is False)

    # 3) next-tracking
    nt = get_next_tracking(token, cid)
    check(
        "A3 next-tracking == RG0010 / next_number=10 / manual_tracking=false",
        nt.get("tracking_id") == "RG0010"
        and nt.get("next_number") == 10
        and nt.get("manual_tracking") is False,
        f"got {nt}",
    )

    # 4) create pending order
    pid = make_pending(token, "9999000111")
    check("A4 pending order created", bool(pid), pid)

    # 5) ship
    sc, body = ship_pending(token, pid, cid)
    ship_ok = sc == 200 and (body.get("tracking_id") == "RG0010" if isinstance(body, dict) else False)
    check(
        "A5 ship 200 + tracking_id==RG0010",
        ship_ok,
        f"http {sc} body.tracking_id={body.get('tracking_id') if isinstance(body, dict) else body}",
    )
    if ship_ok:
        print(f"      → shipment.tracking_id = {body['tracking_id']}")

    # 6) GET courier → next_number == 11
    sc2, gc = get_courier(token, cid)
    check("A6 courier.next_number == 11 after ship", isinstance(gc, dict) and gc.get("next_number") == 11,
          f"next_number={gc.get('next_number') if isinstance(gc, dict) else gc}")

    # 7) next-tracking == RG0011
    nt2 = get_next_tracking(token, cid)
    check("A7 next-tracking == RG0011", nt2.get("tracking_id") == "RG0011", f"got {nt2}")

    return cid


def scenario_B(token: str, cid: str) -> None:
    print("\n────── SCENARIO B — Manual tracking ON ──────")

    # 1) PATCH manual_tracking=true (PUT in this codebase)
    patched = patch_courier(token, cid, {"manual_tracking": True})
    check("B1 PUT manual_tracking=true → courier.manual_tracking is True",
          patched.get("manual_tracking") is True)
    sc, gc = get_courier(token, cid)
    check("B1b GET shows manual_tracking=true", isinstance(gc, dict) and gc.get("manual_tracking") is True)

    # 2) next-tracking under manual mode
    nt = get_next_tracking(token, cid)
    check("B2 next-tracking → tracking_id=='' / next_number==11 / manual_tracking==true",
          nt.get("tracking_id") == "" and nt.get("next_number") == 11 and nt.get("manual_tracking") is True,
          f"got {nt}")

    # 3) Create another pending order
    pid2 = make_pending(token, "9999000222", name="Phase23 Manual Buyer")
    check("B3 pending order pid2 created", bool(pid2), pid2)

    # 4) Ship WITHOUT manual_tracking_id → 400 with detail mentioning manual tracking + sticker
    sc, body = ship_pending(token, pid2, cid)
    detail = (body.get("detail") if isinstance(body, dict) else str(body)) or ""
    cond = (sc == 400 and "manual tracking" in detail.lower() and "sticker" in detail.lower())
    check(
        "B4 ship w/o manual_tracking_id → 400 + 'manual tracking' & 'sticker' in detail",
        cond, f"http {sc} detail={detail!r}",
    )

    # 5) Ship with manual_tracking_id → 200 + raw tracking
    raw_tid = "EM987654321IN"
    sc, body = ship_pending(token, pid2, cid, manual_tracking_id=raw_tid)
    ok = sc == 200 and isinstance(body, dict) and body.get("tracking_id") == raw_tid
    check("B5 ship w/ manual_tracking_id=EM987654321IN → 200 + tracking_id passthrough",
          ok, f"http {sc} tracking_id={body.get('tracking_id') if isinstance(body, dict) else body}")
    if ok:
        print(f"      → shipment.tracking_id = {body['tracking_id']}")

    # 6) GET courier → next_number STILL 11 (NO increment)
    sc2, gc = get_courier(token, cid)
    nn = gc.get("next_number") if isinstance(gc, dict) else None
    check(
        "B6 courier.next_number STILL == 11 (manual mode does NOT increment)",
        nn == 11, f"next_number={nn}",
    )

    # 7) PATCH manual_tracking=false
    patched = patch_courier(token, cid, {"manual_tracking": False})
    check("B7 PUT manual_tracking=false → courier.manual_tracking is False",
          patched.get("manual_tracking") is False)

    # 8) Another pending order
    pid3 = make_pending(token, "9999000333", name="Phase23 Resume Buyer")
    check("B8 pending order pid3 created", bool(pid3), pid3)

    # 9) Ship without manual_tracking_id → tracking == RG0011 (counter resumed)
    sc, body = ship_pending(token, pid3, cid)
    ok = sc == 200 and isinstance(body, dict) and body.get("tracking_id") == "RG0011"
    check("B9 ship resumes sequential → tracking_id==RG0011",
          ok, f"http {sc} tracking_id={body.get('tracking_id') if isinstance(body, dict) else body}")
    if ok:
        print(f"      → shipment.tracking_id = {body['tracking_id']}")

    # 10) Counter now 12
    sc2, gc = get_courier(token, cid)
    nn = gc.get("next_number") if isinstance(gc, dict) else None
    check("B10 courier.next_number == 12 after resumed auto-ship",
          nn == 12, f"next_number={nn}")


def scenario_C(token: str) -> None:
    print("\n────── SCENARIO C — Defence-in-depth + edge cases ──────")

    # 1) Born-manual courier
    c = create_courier(token, {
        "name":            "Phase23-BornManual",
        "series_prefix":   "BM",
        "next_number":     1,
        "number_padding":  5,
        "manual_tracking": True,
    })
    cid_b = c["id"]
    check("C1 created courier with manual_tracking=true",
          c.get("manual_tracking") is True)

    # 2) next-tracking on born-manual courier
    nt = get_next_tracking(token, cid_b)
    check("C2 next-tracking → manual_tracking=true / tracking_id==''",
          nt.get("manual_tracking") is True and nt.get("tracking_id") == "",
          f"got {nt}")

    # 3) Ship with valid manual_tracking_id
    pid = make_pending(token, "9988770011", name="Phase23 BornManual Buyer")
    sc, body = ship_pending(token, pid, cid_b, manual_tracking_id="IPMANUAL12345IN")
    ok = sc == 200 and isinstance(body, dict) and body.get("tracking_id") == "IPMANUAL12345IN"
    check("C3 ship with valid manual_tracking_id → 200 + raw passthrough",
          ok, f"http {sc} tracking_id={body.get('tracking_id') if isinstance(body, dict) else body}")
    if ok:
        print(f"      → shipment.tracking_id = {body['tracking_id']}")

    # 4) Whitespace-only manual_tracking_id → 400
    pid2 = make_pending(token, "9988770022", name="Phase23 BornManual Buyer 2")
    sc, body = ship_pending(token, pid2, cid_b, manual_tracking_id="   ")
    detail = (body.get("detail") if isinstance(body, dict) else str(body)) or ""
    cond = sc == 400
    check(
        "C4 ship with whitespace-only manual_tracking_id → 400",
        cond, f"http {sc} detail={detail!r}",
    )

    # 5) Cross-user isolation: log in as admin, try to GET cid_b → 404
    admin_token = login(ADMIN_EMAIL, ADMIN_PASS)
    sc, body = get_courier(admin_token, cid_b)
    detail = body.get("detail") if isinstance(body, dict) else str(body)
    check("C5 admin GET /couriers/{user2_courier_id} → 404",
          sc == 404, f"http {sc} detail={detail!r}")


# ─── main ───────────────────────────────────────────────────────────────

def main() -> int:
    print(f"BASE = {BASE}")
    # Load /app/backend/.env so MONGO_URL / DB_NAME for direct Mongo
    # access match the backend's connection (only used to seed
    # PendingOrder fixtures, since the public /api/smart-paste path is
    # blocked on this preview workspace by a stale Sheet config).
    try:
        from dotenv import load_dotenv as _ld
        _ld("/app/backend/.env")
    except Exception:
        pass

    token = login(USER_EMAIL, USER_PASS)
    global USER_ID
    USER_ID = requests.get(
        f"{API}/auth/me",
        headers={"Authorization": f"Bearer {token}"},
        timeout=20,
    ).json()["id"]
    print(f"logged in as {USER_EMAIL} (user_id={USER_ID})")

    failed = 0

    try:
        # Clear any existing couriers to free up slot (user2 is silver plan
        # with limit=1 courier). The seeded "Demo Courier" is the only one
        # present in a fresh workspace; deleting it lets us create our
        # named test couriers without hitting the cap.
        free_courier_slot(token)

        cid = scenario_A(token)
        scenario_B(token, cid)

        # Free up the slot before scenario C creates a second courier.
        scA_cleanup = delete_courier(token, cid)
        print(f"      cleanup: deleted RegTest courier → {scA_cleanup}")

        scenario_C(token)
    finally:
        print("\n══════════════════ SUMMARY ══════════════════")
        total = len(_results)
        passed = sum(1 for _, ok, _ in _results if ok)
        failed = total - passed
        print(f"   {passed}/{total} assertions PASSED   ({failed} failed)")
        if failed:
            print("   FAILED:")
            for label, ok, detail in _results:
                if not ok:
                    print(f"     ✗ {label}  [{detail}]")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
