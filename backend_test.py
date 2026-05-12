"""
Phase 16.1 — Save Contact duplicate-detection endpoints tests.

Targets:
  GET  /api/contacts/saved-check?phone=<raw>
  POST /api/contacts/mark-saved
"""
import os
import sys
import requests
from datetime import datetime
from pymongo import MongoClient

BASE = "https://logistics-hub-740.preview.emergentagent.com/api"

USER_A = {"email": "admin@test.com", "password": "Admin@12345"}
USER_B = {"email": "user2@test.com", "password": "User@12345"}

TEST_PHONE_NORM = "9876543210"

results = []
fails = []


def record(name, ok, detail=""):
    results.append((name, ok, detail))
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}: {detail}")
    if not ok:
        fails.append((name, detail))


def login(creds):
    r = requests.post(f"{BASE}/auth/login", json=creds, timeout=30)
    if r.status_code != 200:
        print(f"!! Login failed for {creds['email']}: {r.status_code} {r.text}")
        return None
    return r.json()["token"]


def auth_headers(tok):
    return {"Authorization": f"Bearer {tok}"}


def parse_iso(s):
    try:
        s2 = s.replace("Z", "+00:00") if s.endswith("Z") else s
        datetime.fromisoformat(s2)
        return True
    except Exception:
        return False


def cleanup_mongo():
    try:
        mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
        db_name = os.environ.get("DB_NAME", "test_database")
        c = MongoClient(mongo_url)
        c[db_name].saved_contacts.delete_many({"phone_norm": TEST_PHONE_NORM})
        c.close()
        return True
    except Exception as e:
        print(f"Cleanup error: {e}")
        return False


def count_rows(user_id):
    mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    db_name = os.environ.get("DB_NAME", "test_database")
    c = MongoClient(mongo_url)
    n = c[db_name].saved_contacts.count_documents(
        {"user_id": user_id, "phone_norm": TEST_PHONE_NORM}
    )
    c.close()
    return n


def main():
    print(f"BASE = {BASE}")
    cleanup_mongo()

    tokA = login(USER_A)
    if not tokA:
        record("login user A", False, "could not authenticate admin@test.com")
        return
    record("login user A", True, "ok")

    me_a = requests.get(f"{BASE}/auth/me", headers=auth_headers(tokA), timeout=30).json()
    user_a_id = me_a["id"]

    tokB = login(USER_B)
    have_b = bool(tokB)
    user_b_id = None
    if have_b:
        record("login user B", True, "ok")
        me_b = requests.get(f"{BASE}/auth/me", headers=auth_headers(tokB), timeout=30).json()
        user_b_id = me_b["id"]
    else:
        record("login user B", False, "user2 unavailable — multi-tenant test will be skipped")

    # ----------- Scenario 1: Fresh phone returns saved=false -----------
    r = requests.get(
        f"{BASE}/contacts/saved-check",
        params={"phone": "9876543210"},
        headers=auth_headers(tokA), timeout=30,
    )
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
    ok = r.status_code == 200 and isinstance(body, dict) and body.get("saved") is False
    record("S1: fresh phone -> saved=false", ok, f"HTTP={r.status_code} body={body}")

    # ----------- Scenario 2: mark-saved + re-check -----------
    payload = {
        "phone": "+91 9876543210",
        "name": "Test Customer",
        "shipment_id": "sh-test-1",
    }
    r = requests.post(f"{BASE}/contacts/mark-saved", json=payload,
                      headers=auth_headers(tokA), timeout=30)
    body = r.json() if r.ok else r.text
    saved_at_initial = body.get("saved_at") if isinstance(body, dict) else ""
    ok = (
        r.status_code == 200
        and isinstance(body, dict)
        and body.get("ok") is True
        and parse_iso(saved_at_initial)
    )
    record("S2a: mark-saved 200 + ok=true + ISO saved_at", ok,
           f"HTTP={r.status_code} body={body}")

    r = requests.get(f"{BASE}/contacts/saved-check",
                     params={"phone": "9876543210"},
                     headers=auth_headers(tokA), timeout=30)
    body = r.json() if r.ok else r.text
    ok = (
        r.status_code == 200
        and isinstance(body, dict)
        and body.get("saved") is True
        and body.get("name") == "Test Customer"
        and body.get("saved_at") == saved_at_initial
        and body.get("shipment_id") == "sh-test-1"
    )
    record("S2b: saved-check matches inserted record", ok,
           f"HTTP={r.status_code} body={body}")

    # ----------- Scenario 3: phone normalization variants -----------
    variants = [
        ("09876543210",     "leading 0"),
        ("919876543210",    "country code 91"),
        ("+91 9876-543210", "URL-encoded +91 9876-543210"),
    ]
    for raw, label in variants:
        r = requests.get(
            f"{BASE}/contacts/saved-check",
            params={"phone": raw},
            headers=auth_headers(tokA), timeout=30,
        )
        body = r.json() if r.ok else r.text
        ok = (r.status_code == 200 and isinstance(body, dict)
              and body.get("saved") is True)
        record(f"S3: normalize ({label}) -> saved=true", ok,
               f"raw={raw!r} HTTP={r.status_code} body={body}")

    # ----------- Scenario 4: Upsert (no duplicate row) -----------
    # tiny sleep so ISO timestamp advances measurably
    import time
    time.sleep(0.01)
    r = requests.post(f"{BASE}/contacts/mark-saved",
                      json={"phone": "9876543210", "name": "Updated Name"},
                      headers=auth_headers(tokA), timeout=30)
    body = r.json() if r.ok else r.text
    new_saved_at = body.get("saved_at") if isinstance(body, dict) else ""
    ok = r.status_code == 200 and isinstance(body, dict) and body.get("ok") is True
    record("S4a: second mark-saved 200 ok", ok, f"HTTP={r.status_code} body={body}")

    r = requests.get(f"{BASE}/contacts/saved-check",
                     params={"phone": "9876543210"},
                     headers=auth_headers(tokA), timeout=30)
    body = r.json() if r.ok else r.text
    ok_name = isinstance(body, dict) and body.get("name") == "Updated Name"
    ok_advanced = (isinstance(body, dict) and body.get("saved_at") and
                   body.get("saved_at") > saved_at_initial)
    record("S4b: name now 'Updated Name' + saved_at advanced",
           ok_name and ok_advanced,
           f"body={body} prev_saved_at={saved_at_initial} new={new_saved_at}")

    n = count_rows(user_a_id)
    record("S4c: exactly ONE row in db.saved_contacts for user A", n == 1, f"count={n}")

    # ----------- Scenario 5: Validation -----------
    r = requests.post(f"{BASE}/contacts/mark-saved", json={"phone": ""},
                      headers=auth_headers(tokA), timeout=30)
    record("S5a: empty phone -> 400", r.status_code == 400,
           f"HTTP={r.status_code} body={r.text}")

    r = requests.post(f"{BASE}/contacts/mark-saved", json={"phone": "abc"},
                      headers=auth_headers(tokA), timeout=30)
    record("S5b: 'abc' (no digits) -> 400", r.status_code == 400,
           f"HTTP={r.status_code} body={r.text}")

    # ----------- Scenario 6: Multi-tenant isolation -----------
    if have_b:
        r = requests.get(f"{BASE}/contacts/saved-check",
                         params={"phone": "9876543210"},
                         headers=auth_headers(tokB), timeout=30)
        body = r.json() if r.ok else r.text
        ok = (r.status_code == 200 and isinstance(body, dict)
              and body.get("saved") is False)
        record("S6a: User B saved-check for User A's phone -> saved=false",
               ok, f"HTTP={r.status_code} body={body}")

        r = requests.post(f"{BASE}/contacts/mark-saved",
                          json={"phone": "9876543210", "name": "User B Customer"},
                          headers=auth_headers(tokB), timeout=30)
        ok = r.status_code == 200
        record("S6b: User B mark-saved 200", ok,
               f"HTTP={r.status_code} body={r.text}")

        r = requests.get(f"{BASE}/contacts/saved-check",
                         params={"phone": "9876543210"},
                         headers=auth_headers(tokA), timeout=30)
        body = r.json() if r.ok else r.text
        ok = (isinstance(body, dict) and body.get("name") == "Updated Name")
        record("S6c: User A's record unchanged after User B write",
               ok, f"User A body={body}")

        r = requests.get(f"{BASE}/contacts/saved-check",
                         params={"phone": "9876543210"},
                         headers=auth_headers(tokB), timeout=30)
        body = r.json() if r.ok else r.text
        ok = (isinstance(body, dict) and body.get("saved") is True
              and body.get("name") == "User B Customer")
        record("S6d: User B sees its own record", ok, f"body={body}")
    else:
        record("S6: multi-tenant", True, "SKIPPED — only one user available")

    # ----------- Scenario 7: Cleanup -----------
    cleaned = cleanup_mongo()
    record("S7: cleanup db.saved_contacts", cleaned, "delete_many issued")

    print("\n=========================")
    print(f"Total: {len(results)}  Failed: {len(fails)}")
    for name, detail in fails:
        print(f"  FAIL: {name} :: {detail}")
    print("=========================")
    sys.exit(0 if not fails else 1)


if __name__ == "__main__":
    main()
