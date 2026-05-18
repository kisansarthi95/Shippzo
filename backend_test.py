"""
Backend test for:

 Phase-5i refactor: utility router extraction (GET /api/, POST /api/demo/clear)
 Phase-33: Terminal-state lock on shipments + pending orders

Runs against the public preview backend defined by EXPO_PUBLIC_BACKEND_URL
in /app/frontend/.env.
"""
import os
import sys
import json
import time
import uuid
import requests
from typing import Any, Dict, Optional

BASE = "https://logistics-hub-740.preview.emergentagent.com/api"

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASSWORD = "Admin@12345"

session = requests.Session()
session.headers["Accept"] = "application/json"

results = []  # list of (label, passed, info)


def record(label: str, passed: bool, info: str = ""):
    flag = "PASS" if passed else "FAIL"
    results.append((label, passed, info))
    print(f"[{flag}] {label}  {info}")


def auth_login(email: str, password: str) -> Optional[str]:
    r = session.post(f"{BASE}/auth/login", json={"email": email, "password": password}, timeout=20)
    if r.status_code != 200:
        record("login admin", False, f"HTTP {r.status_code} {r.text[:200]}")
        return None
    tok = r.json().get("token")
    record("login admin", bool(tok), f"token len={len(tok or '')}")
    return tok


def H(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# -----------------------------------------------------------------------
# Phase-5i tests
# -----------------------------------------------------------------------
def test_phase_5i(token: str):
    print("\n=== Phase-5i: Utility router & cross-router smoke ===")

    # 1) GET /api/  → 200 with message
    r = session.get(f"{BASE}/", headers=H(token), timeout=15)
    ok = r.status_code == 200 and "message" in (r.json() if r.headers.get("content-type", "").startswith("application/json") else {})
    record("GET /api/ health-check", ok, f"HTTP {r.status_code} body={r.text[:120]}")

    # 2) POST /api/demo/clear without auth → expect 401/403
    r = session.post(f"{BASE}/demo/clear", timeout=15)
    record(
        "POST /api/demo/clear requires auth",
        r.status_code in (401, 403),
        f"HTTP {r.status_code}",
    )

    # 3) POST /api/demo/clear with auth → 200 with the expected shape
    r = session.post(f"{BASE}/demo/clear", headers=H(token), timeout=30)
    body = {}
    try:
        body = r.json()
    except Exception:
        pass
    keys_present = (
        r.status_code == 200
        and body.get("ok") is True
        and "deleted" in body
        and "shipments" in body
        and "pending_orders" in body
        and "couriers" in body
    )
    record(
        "POST /api/demo/clear shape",
        keys_present,
        f"HTTP {r.status_code} body={body}",
    )

    # 4) Spot-check critical endpoints from other extracted routers
    spot = [
        ("GET /api/settings", "/settings"),
        ("GET /api/me/usage", "/me/usage"),
        ("GET /api/smart-paste/default-prompt", "/smart-paste/default-prompt"),
        ("GET /api/shipments", "/shipments"),
        ("GET /api/orders/pending", "/orders/pending"),
        ("GET /api/sheets/orders", "/sheets/orders"),
        ("GET /api/wallet", "/wallet"),
        ("GET /api/admin/plan-features", "/admin/plan-features"),
    ]
    for label, path in spot:
        r = session.get(f"{BASE}{path}", headers=H(token), timeout=20)
        record(label, r.status_code == 200, f"HTTP {r.status_code}")


# -----------------------------------------------------------------------
# Phase-33 tests
# -----------------------------------------------------------------------
def create_shipment(token: str, suffix: str = "") -> Optional[Dict[str, Any]]:
    """Create a fresh active shipment for testing."""
    body = {
        "customer_name": f"Phase33 Test {suffix}",
        "customer_phone": "9090909090",
        "address": "201 Test Lane, Sector 5",
        "city": "Ahmedabad",
        "state": "Gujarat",
        "pincode": "380015",
        "amount": 599,
        "payment_mode": "COD",
        "status": "Pending",
        "items": ["Test product"],
        "tracking_id": "PH33-" + uuid.uuid4().hex[:8].upper(),
        "courier_id": "f48dc9c4-19cb-4014-a05b-1f3de3002796",  # Nandan Courier
        "courier_name": "Nandan Courier",
    }
    r = session.post(f"{BASE}/shipments", headers=H(token), json=body, timeout=30)
    if r.status_code != 200:
        record(f"create shipment {suffix}", False, f"HTTP {r.status_code} {r.text[:200]}")
        return None
    record(f"create shipment {suffix}", True, f"id={r.json().get('id')}")
    return r.json()


def test_phase_33_shipments(token: str):
    print("\n=== Phase-33: Shipment terminal-state lock ===")

    # ---- Scenario A: PUT to Cancelled stamps audit fields, then PUT Shipped → 423
    s1 = create_shipment(token, "A")
    if not s1:
        return
    sid = s1["id"]

    # PUT status=Cancelled
    r = session.put(f"{BASE}/shipments/{sid}", headers=H(token), json={"status": "Cancelled"}, timeout=20)
    body = r.json() if r.status_code == 200 else {}
    record(
        "PUT shipment status=Cancelled returns 200",
        r.status_code == 200,
        f"HTTP {r.status_code}",
    )

    # The response_model `Shipment` strips unknown fields, so verify
    # `cancelled_at` / `cancel_reason` directly via Mongo (source of
    # truth). They are guaranteed by /app/backend/routers/shipments_write.py:314.
    import asyncio, re
    async def _check_audit_fields():
        from motor.motor_asyncio import AsyncIOMotorClient
        env = open("/app/backend/.env").read()
        mongo_url = re.search(r'MONGO_URL\s*=\s*"?([^"\n]+)"?', env).group(1).strip().strip('"')
        db_name = re.search(r'DB_NAME\s*=\s*"?([^"\n]+)"?', env).group(1).strip().strip('"')
        client = AsyncIOMotorClient(mongo_url)
        d = await client[db_name].shipments.find_one(
            {"id": sid}, {"_id": 0, "status": 1, "cancelled_at": 1, "cancel_reason": 1}
        )
        client.close()
        return d or {}
    db_row = asyncio.run(_check_audit_fields())
    record(
        "Cancelled shipment has cancelled_at stamped (mongo)",
        bool(db_row.get("cancelled_at")),
        f"db_row={db_row}",
    )
    record(
        "Cancelled shipment has cancel_reason=user_action (mongo)",
        db_row.get("cancel_reason") == "user_action",
        f"cancel_reason={db_row.get('cancel_reason')}",
    )

    # Subsequent PUT to a different non-terminal status → 423
    r = session.put(f"{BASE}/shipments/{sid}", headers=H(token), json={"status": "Shipped"}, timeout=20)
    record(
        "PUT to a terminal-locked shipment → 423",
        r.status_code == 423,
        f"HTTP {r.status_code} detail={r.text[:200]}",
    )

    # DELETE on already-cancelled shipment → 200 + already_cancelled=True
    r = session.delete(f"{BASE}/shipments/{sid}", headers=H(token), timeout=20)
    body = {}
    try:
        body = r.json()
    except Exception:
        pass
    record(
        "DELETE already-cancelled shipment is idempotent",
        r.status_code == 200 and body.get("ok") is True and body.get("already_cancelled") is True,
        f"HTTP {r.status_code} body={body}",
    )

    # ---- Scenario B: DELETE active shipment flips to Cancelled (no hard-delete)
    s2 = create_shipment(token, "B")
    if not s2:
        return
    sid2 = s2["id"]
    r = session.delete(f"{BASE}/shipments/{sid2}", headers=H(token), timeout=20)
    body = {}
    try:
        body = r.json()
    except Exception:
        pass
    record(
        "DELETE active shipment → 200 + status flipped",
        r.status_code == 200 and body.get("ok") is True and body.get("status") == "Cancelled",
        f"HTTP {r.status_code} body={body}",
    )

    # GET that shipment → it should still exist with status="Cancelled"
    r = session.get(f"{BASE}/shipments/{sid2}", headers=H(token), timeout=15)
    if r.status_code == 200:
        record(
            "GET shipment after DELETE still exists with status=Cancelled",
            r.json().get("status") == "Cancelled",
            f"status={r.json().get('status')}",
        )
    else:
        # Fallback: lookup via list (in case there's no GET-by-id route)
        r2 = session.get(f"{BASE}/shipments", headers=H(token), timeout=20)
        if r2.status_code == 200:
            found = next((d for d in r2.json() if d.get("id") == sid2), None)
            record(
                "GET shipment after DELETE still exists with status=Cancelled (via list)",
                bool(found) and found.get("status") == "Cancelled",
                f"found={bool(found)} status={(found or {}).get('status')}",
            )
        else:
            record("GET shipment after DELETE", False, f"HTTP {r.status_code}")


# -----------------------------------------------------------------------
# Phase-33: Pending orders
# -----------------------------------------------------------------------
def create_pending_order_via_smart_paste(token: str) -> Optional[Dict[str, Any]]:
    """Create a pending order via Smart Paste (non-AI)."""
    text = (
        "Name: Phase33 Pending Test\n"
        "Phone: 9091929394\n"
        "Address: 12 Test Street, Sector 7\n"
        "City: Ahmedabad\n"
        "State: Gujarat\n"
        "Pincode: 380015\n"
        "Amount: 499\n"
        "Payment: COD\n"
        "Order ID: PHASE33-PND-" + uuid.uuid4().hex[:6].upper() + "\n"
        "Items: Test Item\n"
    )
    r = session.post(
        f"{BASE}/smart-paste",
        headers=H(token),
        json={"text": text, "source": "paste", "ai_enabled": False},
        timeout=60,
    )
    if r.status_code != 200:
        record("create pending order via smart-paste", False, f"HTTP {r.status_code} {r.text[:200]}")
        return None
    body = r.json()
    # Smart paste may return either a pending_order or a record. Pull the id.
    po = body.get("pending_order") or body.get("order") or body
    if "id" not in po:
        # Some implementations return wrapper {ok:..., id:...}
        if "id" in body:
            po = body
    record("create pending order via smart-paste", "id" in po, f"id={po.get('id')}")
    return po


def get_pending_order(token: str, oid: str) -> Optional[Dict[str, Any]]:
    """Fetch a single pending order by id — this endpoint returns
    cancelled rows too (unlike the list endpoint which defaults to
    status='pending')."""
    r = session.get(f"{BASE}/orders/pending/{oid}", headers=H(token), timeout=20)
    if r.status_code == 200:
        return r.json()
    return None


def test_phase_33_pending(token: str):
    print("\n=== Phase-33: Pending order terminal-state lock ===")

    po = create_pending_order_via_smart_paste(token)
    if not po:
        # Fallback — list existing pending orders and pick first non-cancelled
        r = session.get(f"{BASE}/orders/pending", headers=H(token), timeout=20)
        if r.status_code == 200 and isinstance(r.json(), list):
            for d in r.json():
                if (d.get("status") or "").lower() != "cancelled":
                    po = d
                    break
    if not po or not po.get("id"):
        record("locate a pending order", False, "no pending order available")
        return
    oid = po["id"]

    # DELETE pending order → flips to cancelled (NOT hard delete)
    r = session.delete(f"{BASE}/orders/pending/{oid}", headers=H(token), timeout=20)
    body = {}
    try:
        body = r.json()
    except Exception:
        pass
    record(
        "DELETE pending order → 200 (soft-cancel)",
        r.status_code == 200 and body.get("ok") is True,
        f"HTTP {r.status_code} body={body}",
    )

    # GET that pending order → status=cancelled
    found = get_pending_order(token, oid)
    record(
        "Pending order persists with status=cancelled",
        bool(found) and (found.get("status") or "").lower() == "cancelled",
        f"found={bool(found)} status={(found or {}).get('status')}",
    )

    # PUT { status: pending } on cancelled pending → 423
    r = session.put(
        f"{BASE}/orders/pending/{oid}",
        headers=H(token),
        json={"status": "pending"},
        timeout=20,
    )
    record(
        "PUT a cancelled pending order → 423",
        r.status_code == 423,
        f"HTTP {r.status_code} detail={r.text[:200]}",
    )

    # POST .../ship on cancelled pending → 423 (must pass courier_id to clear pydantic validation)
    r = session.post(
        f"{BASE}/orders/pending/{oid}/ship",
        headers=H(token),
        json={"courier_id": "f48dc9c4-19cb-4014-a05b-1f3de3002796"},
        timeout=20,
    )
    record(
        "POST ship on cancelled pending → 423",
        r.status_code == 423,
        f"HTTP {r.status_code} detail={r.text[:200]}",
    )


# -----------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------
def main() -> int:
    print(f"Backend under test: {BASE}")
    token = auth_login(ADMIN_EMAIL, ADMIN_PASSWORD)
    if not token:
        print("Cannot continue without auth token")
        return 1

    test_phase_5i(token)
    test_phase_33_shipments(token)
    test_phase_33_pending(token)

    print("\n=========== SUMMARY ===========")
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    for label, ok, info in results:
        flag = "PASS" if ok else "FAIL"
        print(f"  [{flag}] {label}")
    print(f"\n{passed}/{total} assertions passed")
    return 0 if passed == total else 2


if __name__ == "__main__":
    sys.exit(main())
