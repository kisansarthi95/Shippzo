"""
Phase-7f Master Order ID counter customization tests.
Base URL: ${EXPO_PUBLIC_BACKEND_URL}/api
"""
import sys
from datetime import datetime, timedelta
import requests

BASE_URL = "https://logistics-hub-740.preview.emergentagent.com/api"
ADMIN_EMAIL = "admin@test.com"
ADMIN_PASSWORD = "Admin@12345"

results = []


def record(name, passed, detail=""):
    sym = "PASS" if passed else "FAIL"
    print(f"[{sym}] {name}{(' — ' + detail) if detail else ''}")
    results.append((name, passed, detail))


def login():
    r = requests.post(
        f"{BASE_URL}/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=30,
    )
    r.raise_for_status()
    body = r.json()
    return body["token"]


def ist_yymmdd():
    ist_now = datetime.utcnow() + timedelta(hours=5, minutes=30)
    return ist_now.strftime("%y%m%d")


def main():
    token = login()
    H = {"Authorization": f"Bearer {token}"}
    expected_yymmdd = ist_yymmdd()
    print(f"Expected IST YYMMDD: {expected_yymmdd}")

    # ---- Test 1: peek-master-id IST timezone fix ----
    print("\n=== TEST 1: GET /api/orders/peek-master-id (IST date prefix) ===")
    r = requests.get(f"{BASE_URL}/orders/peek-master-id", headers=H, timeout=30)
    record("T1 peek-master-id 200", r.status_code == 200, f"status={r.status_code}")
    body1 = r.json() if r.status_code == 200 else {}
    print("  body:", body1)
    moid = body1.get("master_order_id", "")
    auto_gen = body1.get("auto_generate", False)
    if not auto_gen:
        # Enable auto_generate first
        print("  auto_generate=false → enabling")
        rr = requests.put(
            f"{BASE_URL}/settings",
            headers=H,
            json={"order_id_auto_generate": True},
            timeout=30,
        )
        record("T1 enable auto_generate", rr.status_code == 200, f"status={rr.status_code}")
        r = requests.get(f"{BASE_URL}/orders/peek-master-id", headers=H, timeout=30)
        body1 = r.json()
        moid = body1.get("master_order_id", "")
        print("  re-fetched body:", body1)
    record(
        "T1 master_order_id starts with IST YYMMDD",
        isinstance(moid, str) and moid.startswith(expected_yymmdd),
        f"got={moid!r} expected_prefix={expected_yymmdd}",
    )

    # ---- Test 2: GET counter ----
    print("\n=== TEST 2: GET /api/orders/master-id-counter ===")
    r = requests.get(f"{BASE_URL}/orders/master-id-counter", headers=H, timeout=30)
    record("T2 GET counter 200", r.status_code == 200, f"status={r.status_code}")
    body2 = r.json() if r.status_code == 200 else {}
    print("  body:", body2)
    cur_seq = body2.get("current_seq")
    next_seq = body2.get("next_seq")
    next_moid = body2.get("next_master_order_id", "")
    record(
        "T2 current_seq is int ≥ 0",
        isinstance(cur_seq, int) and cur_seq >= 0,
        f"current_seq={cur_seq}",
    )
    record(
        "T2 next_seq == current_seq + 1",
        next_seq == (cur_seq or 0) + 1,
        f"next_seq={next_seq}",
    )
    record(
        "T2 next_master_order_id starts with IST YYMMDD",
        isinstance(next_moid, str) and next_moid.startswith(expected_yymmdd),
        f"got={next_moid!r}",
    )

    cur = int(cur_seq or 0)
    print(f"  Starting cur={cur}")

    # ---- Test 3: Set counter to higher value ----
    target = cur + 100
    print(f"\n=== TEST 3: Set counter to {target} ===")
    r = requests.post(
        f"{BASE_URL}/orders/master-id-counter",
        headers=H,
        json={"seq": target},
        timeout=30,
    )
    record(
        "T3 POST set higher 200",
        r.status_code == 200,
        f"status={r.status_code} body={r.text[:200]}",
    )
    body3 = r.json() if r.status_code == 200 else {}
    print("  body:", body3)
    record(
        f"T3 response current_seq == {target}",
        body3.get("current_seq") == target,
        f"got={body3.get('current_seq')}",
    )

    r = requests.get(f"{BASE_URL}/orders/master-id-counter", headers=H, timeout=30)
    body3b = r.json()
    print("  GET back:", body3b)
    record(
        f"T3 GET current_seq == {target}",
        body3b.get("current_seq") == target,
        f"got={body3b.get('current_seq')}",
    )

    # POST /api/smart-paste with skip_llm=true and minimal valid order
    paste_text = (
        "CUSTOMER_NAME: Phase7f Test User\n"
        "PHONE: 9876543210\n"
        "ADDRESS_1: 12 MG Road\n"
        "CITY: Surat\n"
        "STATE: Gujarat\n"
        "PINCODE: 395001\n"
        "ITEMS: Test Item\n"
        "AMOUNT: 199\n"
        "PAYMENT: COD"
    )
    r = requests.post(
        f"{BASE_URL}/smart-paste",
        headers=H,
        json={"text": paste_text, "skip_llm": True},
        timeout=60,
    )
    record(
        "T3 POST /smart-paste with skip_llm 200",
        r.status_code == 200,
        f"status={r.status_code} body={r.text[:300]}",
    )
    pending = r.json() if r.status_code == 200 else {}
    pending_id = pending.get("id")
    moid_created = pending.get("master_order_id", "")
    print(f"  pending.id={pending_id} master_order_id={moid_created!r}")
    expected_moid_suffix = str(target + 1).zfill(5)
    record(
        f"T3 master_order_id ends with {expected_moid_suffix}",
        isinstance(moid_created, str) and moid_created.endswith(expected_moid_suffix),
        f"got={moid_created!r} expected_suffix={expected_moid_suffix}",
    )
    record(
        f"T3 master_order_id starts with IST YYMMDD {expected_yymmdd}",
        isinstance(moid_created, str) and moid_created.startswith(expected_yymmdd),
        f"got={moid_created!r}",
    )

    # Cleanup pending order
    if pending_id:
        try:
            rd = requests.delete(
                f"{BASE_URL}/orders/pending/{pending_id}", headers=H, timeout=30
            )
            print(f"  cleanup delete pending: status={rd.status_code}")
        except Exception as e:
            print(f"  cleanup error: {e}")

    # Counter should now be at target+1 since smart-paste consumed one.
    after_t3 = target + 1

    # ---- Test 4: Set lower without force ----
    print("\n=== TEST 4: Set counter to 1 without force ===")
    r = requests.post(
        f"{BASE_URL}/orders/master-id-counter",
        headers=H,
        json={"seq": 1},
        timeout=30,
    )
    record(
        "T4 lower w/o force returns 409",
        r.status_code == 409,
        f"status={r.status_code}",
    )
    detail4 = ""
    try:
        detail4 = r.json().get("detail", "")
    except Exception:
        pass
    print(f"  detail: {detail4}")
    record(
        "T4 detail mentions 'Lowering' and 'duplicate'",
        ("Lowering" in detail4) and ("duplicate" in detail4),
        f"detail={detail4!r}",
    )

    # ---- Test 5: Set lower WITH force ----
    print("\n=== TEST 5: Set counter to 2200 with force=true ===")
    r = requests.post(
        f"{BASE_URL}/orders/master-id-counter",
        headers=H,
        json={"seq": 2200, "force": True},
        timeout=30,
    )
    record(
        "T5 force lower 200",
        r.status_code == 200,
        f"status={r.status_code} body={r.text[:200]}",
    )
    body5 = r.json() if r.status_code == 200 else {}
    record(
        "T5 response current_seq == 2200",
        body5.get("current_seq") == 2200,
        f"got={body5.get('current_seq')}",
    )
    r = requests.get(f"{BASE_URL}/orders/master-id-counter", headers=H, timeout=30)
    body5b = r.json()
    record(
        "T5 GET current_seq == 2200",
        body5b.get("current_seq") == 2200,
        f"got={body5b.get('current_seq')}",
    )

    # Restore counter close to original
    print(f"\n  Restoring counter to {after_t3} via force...")
    r = requests.post(
        f"{BASE_URL}/orders/master-id-counter",
        headers=H,
        json={"seq": after_t3, "force": True},
        timeout=30,
    )
    record(
        f"T5 cleanup restore counter to {after_t3}",
        r.status_code == 200 and r.json().get("current_seq") == after_t3,
        f"status={r.status_code} body={r.text[:200]}",
    )

    # ---- Test 6: Validation ----
    print("\n=== TEST 6: Validation ===")
    r = requests.post(
        f"{BASE_URL}/orders/master-id-counter",
        headers=H,
        json={"seq": -5},
        timeout=30,
    )
    record(
        "T6 negative seq returns 422",
        r.status_code == 422,
        f"status={r.status_code} body={r.text[:200]}",
    )

    r = requests.post(
        f"{BASE_URL}/orders/master-id-counter",
        headers=H,
        json={"seq": 99999999},
        timeout=30,
    )
    record(
        "T6 oversized seq (99999999) returns 422",
        r.status_code == 422,
        f"status={r.status_code} body={r.text[:200]}",
    )

    # ---- Summary ----
    print("\n" + "=" * 60)
    passed = sum(1 for _, p, _ in results if p)
    total = len(results)
    print(f"RESULTS: {passed}/{total} passed")
    failures = [(n, d) for n, p, d in results if not p]
    if failures:
        print("\nFAILURES:")
        for n, d in failures:
            print(f"  - {n}: {d}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
