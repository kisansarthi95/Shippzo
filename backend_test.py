"""Backend test — Phase-21 order_id priority chain fix on ship_pending_order.

Tests POST /api/orders/pending/{order_id}/ship to verify the Shipment's
`order_id` is set per the new priority:
  1. PendingOrder.order_id (upstream-or-master fallback set at ingest)
  2. order_id_hint (regex paste hint)
  3. master_order_id (final fallback so order_id is NEVER blank)

Test cases:
  A. Webhook ingest with upstream order_id mapped → preserved on Shipment.
  B. Webhook ingest WITHOUT order_id mapping → falls back to master_order_id.
  C. Smart Paste with explicit "Order #ABC-001" in text → preserved.
  D. Smart Paste WITHOUT order_id in text → master_order_id fallback.
  E. File import with order_id column populated → preserved.
"""
import io
import os
import sys
import time
import uuid
import json
import requests


def _resolve_base_url() -> str:
    env_path = "/app/frontend/.env"
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if k.strip() in ("EXPO_PUBLIC_BACKEND_URL", "REACT_APP_BACKEND_URL"):
                    return v.strip().strip('"').strip("'").rstrip("/")
    raise RuntimeError("EXPO_PUBLIC_BACKEND_URL not in /app/frontend/.env")


BASE_URL = _resolve_base_url() + "/api"
print(f"[setup] BASE_URL = {BASE_URL}")


PASS = 0
FAIL = 0
FAILURES: list = []


def check(cond: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS {label}")
    else:
        FAIL += 1
        FAILURES.append(label + (f"  -- {detail}" if detail else ""))
        print(f"  FAIL {label}" + (f"  -- {detail}" if detail else ""))


def login(email: str, pwd: str) -> str:
    r = requests.post(
        f"{BASE_URL}/auth/login",
        json={"email": email, "password": pwd},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["token"]


TOKEN = login("admin@test.com", "Admin@12345")
H = {"Authorization": f"Bearer {TOKEN}"}
print("[setup] Logged in as admin@test.com")


created_pending_ids: set = set()
created_shipment_ids: set = set()
created_courier_id: str = ""


def cleanup():
    print("\n[cleanup]")
    for pid in list(created_pending_ids):
        try:
            r = requests.delete(
                f"{BASE_URL}/orders/pending/{pid}", headers=H, timeout=20,
            )
            print(f"  DELETE /orders/pending/{pid} -> {r.status_code}")
        except Exception as e:
            print(f"  DELETE pending {pid} failed: {e}")
    for sid in list(created_shipment_ids):
        try:
            r = requests.delete(
                f"{BASE_URL}/shipments/{sid}", headers=H, timeout=30,
            )
            print(f"  DELETE /shipments/{sid} -> {r.status_code}")
        except Exception as e:
            print(f"  DELETE shipment {sid} failed: {e}")
    if created_courier_id:
        try:
            r = requests.delete(
                f"{BASE_URL}/couriers/{created_courier_id}", headers=H, timeout=20,
            )
            print(f"  DELETE /couriers/{created_courier_id} -> {r.status_code}")
        except Exception as e:
            print(f"  DELETE courier failed: {e}")


# ----- Ensure auto-generate is ON -----------------------------------------
print("\n[pre-req] Ensure order_id_auto_generate is ON in /settings")
r = requests.put(
    f"{BASE_URL}/settings",
    headers=H,
    json={"order_id_auto_generate": True},
    timeout=30,
)
print(f"  PUT /settings -> {r.status_code}")

# ----- Ensure courier exists ----------------------------------------------
print("\n[pre-req] Ensure a courier exists for shipping tests")
r = requests.get(f"{BASE_URL}/couriers", headers=H, timeout=20)
r.raise_for_status()
couriers = r.json()
COURIER_ID = ""
if couriers:
    real = [c for c in couriers if not c.get("is_demo")]
    COURIER_ID = (real[0] if real else couriers[0])["id"]
    print(f"  Reusing courier id={COURIER_ID}")
else:
    payload = {
        "name": f"Test Courier {uuid.uuid4().hex[:6]}",
        "series_prefix": "TC",
        "next_number": 1,
        "number_padding": 4,
        "tracking_url_template": "https://example.com/track/{tracking}",
    }
    r = requests.post(f"{BASE_URL}/couriers", headers=H, json=payload, timeout=20)
    r.raise_for_status()
    COURIER_ID = r.json()["id"]
    created_courier_id = COURIER_ID
    print(f"  Created courier id={COURIER_ID}")


def ensure_webhook_with_order_id_mapping() -> str:
    r = requests.get(f"{BASE_URL}/me/webhook-config", headers=H, timeout=20)
    r.raise_for_status()
    cfg = r.json()
    secret = cfg.get("secret") or ""
    if not secret:
        r2 = requests.post(
            f"{BASE_URL}/me/webhook-config/rotate",
            headers=H, json={"name": "Test Webhook"}, timeout=20,
        )
        r2.raise_for_status()
        secret = r2.json()["secret"]
        print(f"  Rotated to create webhook secret={secret[:8]}...")

    mapping = {
        "order_id":     "order_id",
        "name":         "customer_name",
        "phone":        "customer_phone",
        "address":      "address",
        "city":         "city",
        "state":        "state",
        "pincode":      "pincode",
        "amount":       "amount",
    }
    r3 = requests.put(
        f"{BASE_URL}/me/webhook-config",
        headers=H, json={"mapping": mapping}, timeout=20,
    )
    r3.raise_for_status()
    return secret


WH_SECRET = ensure_webhook_with_order_id_mapping()
print(f"\n[setup] Webhook secret prepared ({WH_SECRET[:8]}...)")


def find_pending_by_phone(phone: str) -> dict | None:
    r = requests.get(f"{BASE_URL}/orders/pending", headers=H, timeout=30)
    r.raise_for_status()
    for o in r.json():
        if (o.get("customer_phone") or "").strip().endswith(phone[-10:]):
            return o
    return None


def ship(pending_id: str) -> dict:
    r = requests.post(
        f"{BASE_URL}/orders/pending/{pending_id}/ship",
        headers=H, json={"courier_id": COURIER_ID}, timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(f"ship failed {r.status_code} {r.text[:200]}")
    return r.json()


# ===== TEST A — Webhook with upstream order_id ===========================
print("\n" + "=" * 72)
print("TEST A: Webhook ingest with upstream order_id")
print("=" * 72)
phone_a = "9112000111"
upstream_a = f"UPSTREAM-XYZ-{uuid.uuid4().hex[:6].upper()}"
payload_a = {
    "order_id": upstream_a,
    "name":     "Rohit Sharma",
    "phone":    phone_a,
    "address":  "1, MG Road",
    "city":     "Ahmedabad",
    "state":    "Gujarat",
    "pincode":  "380015",
    "amount":   799,
}
r = requests.post(
    f"{BASE_URL}/webhook/orders/{WH_SECRET}", json=payload_a, timeout=30,
)
print(f"  POST webhook -> {r.status_code} {r.text[:160]}")
check(r.status_code == 200, "Test A: webhook accepts payload")
body = r.json()
check(body.get("imported") == 1, "Test A: imported=1", f"got {body}")

time.sleep(0.5)
po_a = find_pending_by_phone(phone_a)
check(po_a is not None, "Test A: pending order created and discoverable")
if po_a:
    created_pending_ids.add(po_a["id"])
    print(f"  pending.order_id          = {po_a.get('order_id')!r}")
    print(f"  pending.master_order_id   = {po_a.get('master_order_id')!r}")
    print(f"  pending.external_order_id = {po_a.get('external_order_id')!r}")
    check(
        po_a.get("order_id") == upstream_a,
        "Test A: PendingOrder.order_id preserves upstream value",
        f"expected {upstream_a!r}, got {po_a.get('order_id')!r}",
    )
    try:
        ship_a = ship(po_a["id"])
        created_shipment_ids.add(ship_a["id"])
        created_pending_ids.discard(po_a["id"])
        print(f"  ship.order_id        = {ship_a.get('order_id')!r}")
        print(f"  ship.master_order_id = {ship_a.get('master_order_id')!r}")
        check(
            ship_a.get("order_id") == upstream_a,
            "Test A: Shipment.order_id == upstream order_id",
            f"expected {upstream_a!r}, got {ship_a.get('order_id')!r}",
        )
        check(
            ship_a.get("order_id") != ship_a.get("master_order_id"),
            "Test A: Shipment.order_id is NOT the master_order_id",
        )
    except Exception as e:
        check(False, "Test A: ship POST 200", str(e))


# ===== TEST B — Webhook without order_id ================================
print("\n" + "=" * 72)
print("TEST B: Webhook ingest WITHOUT order_id -> master_order_id fallback")
print("=" * 72)
phone_b = "9111000333"
payload_b = {
    "name":    "Test User B",
    "phone":   phone_b,
    "address": "55 Some Street",
    "city":    "X",
    "state":   "Y",
    "pincode": "380015",
    "amount":  499,
}
r = requests.post(
    f"{BASE_URL}/webhook/orders/{WH_SECRET}", json=payload_b, timeout=30,
)
print(f"  POST webhook -> {r.status_code} {r.text[:160]}")
check(r.status_code == 200, "Test B: webhook accepts payload")
check(r.json().get("imported") == 1, "Test B: imported=1")

time.sleep(0.5)
po_b = find_pending_by_phone(phone_b)
check(po_b is not None, "Test B: pending order created and discoverable")
if po_b:
    created_pending_ids.add(po_b["id"])
    moid_b = po_b.get("master_order_id") or ""
    oid_b  = po_b.get("order_id") or ""
    print(f"  pending.master_order_id = {moid_b!r}")
    print(f"  pending.order_id        = {oid_b!r}")
    check(
        bool(moid_b) and moid_b.isdigit() and len(moid_b) >= 8,
        "Test B: PendingOrder.master_order_id is allocated (YYMMDD+seq)",
        f"got {moid_b!r}",
    )
    check(
        oid_b == moid_b,
        "Test B: PendingOrder.order_id == master_order_id (ingest fallback)",
        f"order_id={oid_b!r} master={moid_b!r}",
    )
    try:
        ship_b = ship(po_b["id"])
        created_shipment_ids.add(ship_b["id"])
        created_pending_ids.discard(po_b["id"])
        print(f"  ship.order_id        = {ship_b.get('order_id')!r}")
        print(f"  ship.master_order_id = {ship_b.get('master_order_id')!r}")
        check(
            ship_b.get("order_id") == moid_b,
            "Test B: Shipment.order_id == master_order_id",
            f"expected {moid_b!r}, got {ship_b.get('order_id')!r}",
        )
        check(
            bool(ship_b.get("order_id")),
            "Test B: Shipment.order_id is NOT empty",
        )
    except Exception as e:
        check(False, "Test B: ship POST 200", str(e))


# ===== TEST C — Smart-paste with explicit Order ID =====================
print("\n" + "=" * 72)
print("TEST C: Smart-paste with explicit Order #ABC-001 -> preserved")
print("=" * 72)
phone_c = "9999000444"
# Use canonical "Order ID:" form (parser pre-normalises space → underscore).
text_c = (
    "Order ID: ABC-001\n"
    "NAME: Riya Singh\n"
    f"PHONE: {phone_c}\n"
    "ADDRESS: 22 Park Lane\n"
    "CITY: Ahmedabad\n"
    "STATE: Gujarat\n"
    "PINCODE: 380015\n"
    "AMOUNT: 599\n"
    "PAYMENT: COD\n"
    "ITEMS: T-Shirt\n"
)
r = requests.post(
    f"{BASE_URL}/smart-paste", headers=H,
    json={"text": text_c, "skip_llm": True}, timeout=60,
)
print(f"  POST /smart-paste -> {r.status_code} {r.text[:240]}")
check(r.status_code == 200, "Test C: smart-paste 200", r.text[:200])
if r.status_code == 200:
    po_c = r.json()
    created_pending_ids.add(po_c["id"])
    oid_c = po_c.get("order_id") or ""
    moid_c = po_c.get("master_order_id") or ""
    print(f"  pending.order_id        = {oid_c!r}")
    print(f"  pending.master_order_id = {moid_c!r}")
    check(
        oid_c == "ABC-001",
        "Test C: PendingOrder.order_id == 'ABC-001' (parsed)",
        f"got {oid_c!r}",
    )
    check(
        bool(moid_c) and moid_c != oid_c,
        "Test C: master_order_id allocated and distinct from order_id",
        f"master={moid_c!r}",
    )
    try:
        ship_c = ship(po_c["id"])
        created_shipment_ids.add(ship_c["id"])
        created_pending_ids.discard(po_c["id"])
        print(f"  ship.order_id        = {ship_c.get('order_id')!r}")
        print(f"  ship.master_order_id = {ship_c.get('master_order_id')!r}")
        check(
            ship_c.get("order_id") == "ABC-001",
            "Test C: Shipment.order_id == 'ABC-001'",
            f"got {ship_c.get('order_id')!r}",
        )
    except Exception as e:
        check(False, "Test C: ship POST 200", str(e))


# ===== TEST D — Smart-paste without order_id ===========================
print("\n" + "=" * 72)
print("TEST D: Smart-paste WITHOUT order_id -> master fallback")
print("=" * 72)
phone_d = "9999000555"
text_d = (
    "NAME: Riya Patel\n"
    f"PHONE: {phone_d}\n"
    "ADDRESS: 33 Lake View\n"
    "CITY: Ahmedabad\n"
    "STATE: Gujarat\n"
    "PINCODE: 380015\n"
    "AMOUNT: 599\n"
    "PAYMENT: COD\n"
    "ITEMS: T-Shirt\n"
)
r = requests.post(
    f"{BASE_URL}/smart-paste", headers=H,
    json={"text": text_d, "skip_llm": True}, timeout=60,
)
print(f"  POST /smart-paste -> {r.status_code} {r.text[:240]}")
check(r.status_code == 200, "Test D: smart-paste 200", r.text[:200])
if r.status_code == 200:
    po_d = r.json()
    created_pending_ids.add(po_d["id"])
    oid_d  = po_d.get("order_id") or ""
    moid_d = po_d.get("master_order_id") or ""
    print(f"  pending.order_id        = {oid_d!r}")
    print(f"  pending.master_order_id = {moid_d!r}")
    check(bool(moid_d), "Test D: master_order_id allocated", f"got {moid_d!r}")
    check(
        oid_d == moid_d,
        "Test D: PendingOrder.order_id == master_order_id (auto fallback)",
        f"order_id={oid_d!r} master={moid_d!r}",
    )
    try:
        ship_d = ship(po_d["id"])
        created_shipment_ids.add(ship_d["id"])
        created_pending_ids.discard(po_d["id"])
        print(f"  ship.order_id        = {ship_d.get('order_id')!r}")
        print(f"  ship.master_order_id = {ship_d.get('master_order_id')!r}")
        check(
            ship_d.get("order_id") == moid_d,
            "Test D: Shipment.order_id == master_order_id",
            f"expected {moid_d!r}, got {ship_d.get('order_id')!r}",
        )
        check(
            bool(ship_d.get("order_id")),
            "Test D: Shipment.order_id is NOT empty",
        )
    except Exception as e:
        check(False, "Test D: ship POST 200", str(e))


# ===== TEST E — File import with order_id column ========================
print("\n" + "=" * 72)
print("TEST E: File import CSV with order_id -> preserved")
print("=" * 72)
file_oid = f"FILE-ORD-{uuid.uuid4().hex[:6].upper()}"
phone_e = "9999000666"
csv_text = (
    "customer_name,phone,pincode,city,state,item,amount,order_id\n"
    f"Test User E,{phone_e},380015,Ahmedabad,Gujarat,Item E,499,{file_oid}\n"
)
mapping_e = {
    "customer_name": "customer_name",
    "phone":         "customer_phone",
    "pincode":       "pincode",
    "city":          "city",
    "state":         "state",
    "item":          "items",
    "amount":        "amount",
    "order_id":      "order_id",
}
files = {
    "file": ("orders.csv", io.BytesIO(csv_text.encode("utf-8")), "text/csv"),
}
data = {
    "mapping": json.dumps(mapping_e),
    "save_default": "false",
}
r = requests.post(
    f"{BASE_URL}/orders/import/commit",
    headers=H, files=files, data=data, timeout=60,
)
print(f"  POST /orders/import/commit -> {r.status_code} {r.text[:240]}")
check(r.status_code == 200, "Test E: file-import 200", r.text[:200])
if r.status_code == 200:
    res = r.json()
    check(res.get("imported") == 1, "Test E: imported=1", f"got {res}")
    time.sleep(0.5)
    po_e = find_pending_by_phone(phone_e)
    check(po_e is not None, "Test E: pending order created and discoverable")
    if po_e:
        created_pending_ids.add(po_e["id"])
        oid_e  = po_e.get("order_id") or ""
        moid_e = po_e.get("master_order_id") or ""
        print(f"  pending.order_id        = {oid_e!r}")
        print(f"  pending.master_order_id = {moid_e!r}")
        check(
            oid_e == file_oid,
            "Test E: PendingOrder.order_id preserves file value",
            f"expected {file_oid!r}, got {oid_e!r}",
        )
        try:
            ship_e = ship(po_e["id"])
            created_shipment_ids.add(ship_e["id"])
            created_pending_ids.discard(po_e["id"])
            print(f"  ship.order_id        = {ship_e.get('order_id')!r}")
            print(f"  ship.master_order_id = {ship_e.get('master_order_id')!r}")
            check(
                ship_e.get("order_id") == file_oid,
                "Test E: Shipment.order_id == file order_id",
                f"expected {file_oid!r}, got {ship_e.get('order_id')!r}",
            )
        except Exception as e:
            check(False, "Test E: ship POST 200", str(e))


cleanup()

print("\n" + "=" * 72)
print(f"RESULT: PASS={PASS}  FAIL={FAIL}")
print("=" * 72)
if FAIL:
    print("\nFailures:")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
sys.exit(0)
