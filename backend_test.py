"""Backend test for POST /api/demo/clear endpoint — Phase-21 sweep upgrade.

Covers the review request:
  1. Fresh user signup auto-seeds 15 demo shipments + 1 Demo Courier (is_demo:True).
  2. GET /api/shipments and /api/couriers reflect the demo state before clear.
  3. POST /api/demo/clear returns per-collection breakdown + idempotency.
  4. After clear: shipments empty, Demo Courier deleted.
  5. Idempotent re-clear returns all zeros.
  6. Smart "in-use" check: real shipment using Demo Courier preserves the
     courier (couriers=0 deleted) and unsets the is_demo flag.
"""
import os
import sys
import time
import uuid
import requests

# ---------------------------------------------------------------------
# Resolve BASE_URL from frontend/.env (EXPO_PUBLIC_BACKEND_URL) — strict
# rule: never hardcode, never use localhost.
# ---------------------------------------------------------------------
def _resolve_base_url() -> str:
    env_path = "/app/frontend/.env"
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if k.strip() in ("EXPO_PUBLIC_BACKEND_URL", "REACT_APP_BACKEND_URL"):
                    return v.strip().strip('"').strip("'").rstrip("/")
    raise RuntimeError("EXPO_PUBLIC_BACKEND_URL not found in /app/frontend/.env")


BASE_URL = _resolve_base_url() + "/api"
print(f"[setup] BASE_URL = {BASE_URL}")


# ---------------------------------------------------------------------
# Assertion bookkeeping
# ---------------------------------------------------------------------
PASS = 0
FAIL = 0
FAILURES = []


def check(cond: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        FAILURES.append(f"{label} :: {detail}")
        print(f"  ❌ {label} :: {detail}")


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def make_email(tag: str) -> str:
    return f"democlear_test_phase21_{tag}_{uuid.uuid4().hex[:6]}@test.com"


def signup_user(tag: str) -> dict:
    email = make_email(tag)
    payload = {
        "email": email,
        "password": "Test@1234",
        "name": f"Demo Clear Tester {tag}",
        "shop_name": f"Demo Shop {tag}",
        "phone": "9876543210",
        "primary_business_category": "fashion_apparel",
    }
    r = requests.post(f"{BASE_URL}/auth/signup", json=payload, timeout=60)
    assert r.status_code == 200, f"signup failed {r.status_code}: {r.text[:300]}"
    body = r.json()
    return {"email": email, "token": body["token"], "uid": body.get("id")}


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def get_shipments(token: str) -> list:
    r = requests.get(f"{BASE_URL}/shipments", headers=auth_headers(token), timeout=30)
    assert r.status_code == 200, f"GET /shipments failed {r.status_code}: {r.text[:300]}"
    return r.json()


def get_couriers(token: str) -> list:
    r = requests.get(f"{BASE_URL}/couriers", headers=auth_headers(token), timeout=30)
    assert r.status_code == 200, f"GET /couriers failed {r.status_code}: {r.text[:300]}"
    return r.json()


def post_demo_clear(token: str) -> dict:
    r = requests.post(f"{BASE_URL}/demo/clear", headers=auth_headers(token), timeout=60)
    assert r.status_code == 200, f"POST /demo/clear failed {r.status_code}: {r.text[:300]}"
    return r.json()


# ---------------------------------------------------------------------
# SCENARIO 1 — Happy path: clear demo data on a fresh user
# ---------------------------------------------------------------------
def scenario_1_happy_path():
    print("\n=== SCENARIO 1: Happy path — fresh user → clear ===")
    user = signup_user("happy")
    token = user["token"]
    print(f"[setup] signed up {user['email']}")

    # Tiny pause: seeding 15 shipments is async-free in this codebase but
    # mongo writes still need a beat to settle.
    time.sleep(0.5)

    # 2) Before clear
    ships = get_shipments(token)
    demo_ships = [s for s in ships if s.get("is_demo")]
    check(len(ships) == 15, "before clear: 15 shipments returned", f"got {len(ships)}")
    check(len(demo_ships) == 15, "before clear: all 15 are is_demo=True",
          f"got {len(demo_ships)}")

    couriers = get_couriers(token)
    demo_couriers = [c for c in couriers if c.get("name") == "Demo Courier"]
    check(len(demo_couriers) == 1, "before clear: Demo Courier present",
          f"couriers={[c.get('name') for c in couriers]}")
    if demo_couriers:
        check(bool(demo_couriers[0].get("is_demo")) is True,
              "before clear: Demo Courier has is_demo=True",
              f"is_demo={demo_couriers[0].get('is_demo')!r}")

    # 3) POST /demo/clear
    body = post_demo_clear(token)
    print(f"[clear] response = {body}")
    check(body.get("ok") is True, "clear response: ok=true",
          f"ok={body.get('ok')!r}")
    check(body.get("shipments") == 15, "clear response: shipments=15",
          f"got {body.get('shipments')}")
    check(body.get("pending_orders") == 0, "clear response: pending_orders=0",
          f"got {body.get('pending_orders')}")
    check(body.get("couriers") == 1, "clear response: couriers=1",
          f"got {body.get('couriers')}")
    expected_total = (body.get("shipments", 0) + body.get("pending_orders", 0)
                      + body.get("couriers", 0))
    check(body.get("deleted") == expected_total,
          "clear response: deleted == sum",
          f"deleted={body.get('deleted')} sum={expected_total}")
    check(body.get("deleted") == 16, "clear response: total deleted=16",
          f"got {body.get('deleted')}")

    # 4) After clear
    ships_after = get_shipments(token)
    check(len(ships_after) == 0, "after clear: shipments empty",
          f"got {len(ships_after)}")
    couriers_after = get_couriers(token)
    names_after = [c.get("name") for c in couriers_after]
    check("Demo Courier" not in names_after,
          "after clear: Demo Courier absent",
          f"couriers={names_after}")

    # 5) Idempotency
    body2 = post_demo_clear(token)
    print(f"[clear-again] response = {body2}")
    check(body2.get("ok") is True, "idempotent clear: ok=true",
          f"ok={body2.get('ok')!r}")
    check(body2.get("shipments") == 0, "idempotent clear: shipments=0",
          f"got {body2.get('shipments')}")
    check(body2.get("pending_orders") == 0, "idempotent clear: pending_orders=0",
          f"got {body2.get('pending_orders')}")
    check(body2.get("couriers") == 0, "idempotent clear: couriers=0",
          f"got {body2.get('couriers')}")
    check(body2.get("deleted") == 0, "idempotent clear: deleted=0",
          f"got {body2.get('deleted')}")


# ---------------------------------------------------------------------
# SCENARIO 2 — In-use check: real shipment pins the Demo Courier
# ---------------------------------------------------------------------
def scenario_2_in_use_preserves_courier():
    print("\n=== SCENARIO 2: In-use Demo Courier must be preserved ===")
    user = signup_user("inuse")
    token = user["token"]
    print(f"[setup] signed up {user['email']}")
    time.sleep(0.5)

    # Find the seeded Demo Courier id
    couriers = get_couriers(token)
    demo_courier = next((c for c in couriers if c.get("name") == "Demo Courier"), None)
    check(demo_courier is not None, "scenario-2 prep: Demo Courier exists",
          f"couriers={[c.get('name') for c in couriers]}")
    if not demo_courier:
        return
    courier_id = demo_courier["id"]
    check(bool(demo_courier.get("is_demo")) is True,
          "scenario-2 prep: Demo Courier flagged is_demo=True",
          f"is_demo={demo_courier.get('is_demo')!r}")

    # Create a REAL shipment using the Demo Courier
    real_payload = {
        "tracking_id": f"REAL{uuid.uuid4().hex[:6].upper()}",
        "courier_id": courier_id,
        "courier_name": "Demo Courier",
        "customer_name": "Priya Sharma",
        "customer_phone": "9123456789",
        "address_line1": "12 Marine Drive",
        "city": "Mumbai",
        "state": "Maharashtra",
        "pincode": "400020",
        "payment_mode": "Prepaid",
        "amount": 1499.0,
        "weight": "0.5",
        "items": ["Silk Saree"],
    }
    r = requests.post(f"{BASE_URL}/shipments", json=real_payload,
                      headers=auth_headers(token), timeout=60)
    check(r.status_code == 200,
          "scenario-2: POST /shipments (real) returned 200",
          f"status={r.status_code} body={r.text[:300]}")
    if r.status_code != 200:
        return
    real_ship = r.json()
    check(bool(real_ship.get("is_demo")) is False or real_ship.get("is_demo") is None,
          "scenario-2: real shipment is NOT flagged is_demo",
          f"is_demo={real_ship.get('is_demo')!r}")
    check(real_ship.get("courier_id") == courier_id,
          "scenario-2: real shipment linked to Demo Courier id",
          f"got {real_ship.get('courier_id')}")

    # Sanity — total shipments should now be 16 (15 demo + 1 real)
    ships_pre_clear = get_shipments(token)
    demo_count_pre = sum(1 for s in ships_pre_clear if s.get("is_demo"))
    real_count_pre = len(ships_pre_clear) - demo_count_pre
    check(demo_count_pre == 15,
          "scenario-2: 15 demo shipments before clear",
          f"got {demo_count_pre}")
    check(real_count_pre == 1,
          "scenario-2: 1 real shipment before clear",
          f"got {real_count_pre}")

    # POST /demo/clear — courier must be preserved
    body = post_demo_clear(token)
    print(f"[clear-inuse] response = {body}")
    check(body.get("ok") is True,
          "scenario-2 clear: ok=true",
          f"ok={body.get('ok')!r}")
    check(body.get("shipments") == 15,
          "scenario-2 clear: shipments=15 (only demo deleted)",
          f"got {body.get('shipments')}")
    check(body.get("pending_orders") == 0,
          "scenario-2 clear: pending_orders=0",
          f"got {body.get('pending_orders')}")
    check(body.get("couriers") == 0,
          "scenario-2 clear: couriers=0 (Demo Courier preserved due to in-use)",
          f"got {body.get('couriers')}")
    check(body.get("deleted") == 15,
          "scenario-2 clear: total deleted=15",
          f"got {body.get('deleted')}")

    # Verify after — real shipment survives, Demo Courier still present
    ships_after = get_shipments(token)
    check(len(ships_after) == 1,
          "scenario-2 after clear: 1 real shipment remains",
          f"got {len(ships_after)} : {[s.get('tracking_id') for s in ships_after]}")
    if ships_after:
        check(ships_after[0].get("tracking_id") == real_payload["tracking_id"],
              "scenario-2 after clear: surviving shipment is the real one",
              f"got tracking_id={ships_after[0].get('tracking_id')}")

    couriers_after = get_couriers(token)
    demo_after = next((c for c in couriers_after if c.get("name") == "Demo Courier"), None)
    check(demo_after is not None,
          "scenario-2 after clear: Demo Courier still present",
          f"couriers={[c.get('name') for c in couriers_after]}")
    if demo_after:
        # is_demo flag must be unset (so future clears won't target it)
        check(not demo_after.get("is_demo"),
              "scenario-2 after clear: is_demo flag UNSET on preserved courier",
              f"is_demo={demo_after.get('is_demo')!r}")

    # Second clear — should be a no-op (no demo data left, courier untagged)
    body2 = post_demo_clear(token)
    print(f"[clear-inuse-again] response = {body2}")
    check(body2.get("ok") is True and body2.get("deleted") == 0
          and body2.get("couriers") == 0 and body2.get("shipments") == 0,
          "scenario-2 idempotent: second clear returns all zeros",
          f"body={body2}")


# ---------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------
if __name__ == "__main__":
    try:
        scenario_1_happy_path()
        scenario_2_in_use_preserves_courier()
    except Exception as exc:
        print(f"\n[fatal] Test suite aborted: {exc}")
        import traceback
        traceback.print_exc()
        FAIL += 1
        FAILURES.append(f"FATAL: {exc}")

    print("\n========================================")
    print(f"PASSED: {PASS}")
    print(f"FAILED: {FAIL}")
    if FAILURES:
        print("\nFailures:")
        for f in FAILURES:
            print(f"  - {f}")
    print("========================================")
    sys.exit(0 if FAIL == 0 else 1)
