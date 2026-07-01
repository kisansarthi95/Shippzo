"""Universal Smart Search — backend test suite (Phase C).

Verifies the new `_search_blob`, /api/shipments?search, and
/api/shipments/product-suggestions endpoints against the acceptance
criteria in the review request (T1–T9).
"""
from __future__ import annotations

import os
import pytest
import requests

BASE_URL = (
    os.environ.get("EXPO_PUBLIC_BACKEND_URL")
    or os.environ.get("EXPO_BACKEND_URL")
    or ""
).rstrip("/")

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASSWORD = "Admin@12345"

# ── Fixtures ───────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def token() -> str:
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=15,
    )
    assert r.status_code == 200, f"login failed {r.status_code}: {r.text}"
    tok = r.json().get("token")
    assert tok, f"no token in login response: {r.json()}"
    return tok


@pytest.fixture(scope="session")
def api(token):
    s = requests.Session()
    s.headers.update({
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
    })
    return s


# ── Helpers ────────────────────────────────────────────────────────

def _get_shipments(api, **params):
    r = api.get(f"{BASE_URL}/api/shipments", params=params, timeout=30)
    assert r.status_code == 200, f"list_shipments {r.status_code}: {r.text[:200]}"
    return r.json()


# ── T1. _search_blob presence & correctness ─────────────────────────
def test_t1_search_blob_backfilled(api):
    # Trigger the read endpoint so the lazy backfill runs on anything
    # that missed the migration.
    docs = _get_shipments(api, limit=5)
    assert len(docs) > 0, "no shipments in admin workspace to test with"
    # Hit again to give the async backfill a chance.
    import time
    time.sleep(1.5)
    docs = _get_shipments(api, limit=5)
    # The Shipment model may not surface _search_blob (it's an internal
    # field). Verify via the search endpoint round-trip: pick a
    # customer_name substring from the first doc and ensure the
    # search-blob-driven regex returns that doc.
    sample = docs[0]
    cname = (sample.get("customer_name") or "").strip()
    if not cname:
        pytest.skip("first shipment has no customer_name to search on")
    token = cname.split()[0]  # first name/word
    if len(token) < 2:
        pytest.skip("first name too short to be a distinctive search term")
    found = _get_shipments(api, search=token, limit=1000)
    ids = {d["id"] for d in found}
    assert sample["id"] in ids, (
        f"search on customer_name token {token!r} did not return the "
        f"originating shipment — _search_blob likely missing/stale"
    )


# ── T2 & T3. Basic items[] search + word-order insensitivity ────────
def test_t2_t3_items_search(api):
    # Find a shipment with a non-empty items[] to derive a real search term.
    docs = _get_shipments(api, limit=500)
    with_items = [d for d in docs if d.get("items")]
    if not with_items:
        pytest.skip("no shipments with items[] in workspace")
    sample = next(
        (d for d in with_items if any(len(str(x)) >= 3 for x in d["items"])),
        None,
    )
    if not sample:
        pytest.skip("no items[] entries long enough to search")
    raw_item = next(str(x) for x in sample["items"] if len(str(x)) >= 3)
    # Take a single word from the item description as the search term.
    words = [w for w in raw_item.split() if len(w) >= 3]
    if not words:
        pytest.skip("no >=3 char word in items[] to search")
    term = words[0]

    # T2 — direct search finds >= 1 result including our sample.
    r1 = _get_shipments(api, search=term, limit=1000)
    assert len(r1) >= 1, f"search={term!r} returned 0 (expected >=1)"
    r1_ids = {d["id"] for d in r1}
    assert sample["id"] in r1_ids, (
        f"search={term!r} did not surface source shipment {sample['id']}"
    )

    # T3 — word-order insensitivity (if we have a 2-word phrase).
    if len(words) >= 2:
        w1, w2 = words[0], words[1]
        a = _get_shipments(api, search=f"{w1} {w2}", limit=1000)
        b = _get_shipments(api, search=f"{w2} {w1}", limit=1000)
        assert {d["id"] for d in a} == {d["id"] for d in b}, (
            "word-order-insensitive search returned different sets: "
            f"{w1} {w2} vs {w2} {w1}"
        )


# ── T4. Case + hyphen insensitivity ─────────────────────────────────
def test_t4_case_hyphen_insensitive(api):
    docs = _get_shipments(api, limit=500)
    with_items = [d for d in docs if d.get("items")]
    if not with_items:
        pytest.skip("no shipments with items[] in workspace")
    sample = next(
        (d for d in with_items if any(len(str(x)) >= 4 for x in d["items"])),
        None,
    )
    if not sample:
        pytest.skip("no long enough item")
    raw_item = next(str(x) for x in sample["items"] if len(str(x)) >= 4)
    words = [w for w in raw_item.split() if len(w) >= 4 and w.isalpha()]
    if not words:
        pytest.skip("no alpha word for case test")
    w = words[0]
    lower = _get_shipments(api, search=w.lower(), limit=1000)
    upper = _get_shipments(api, search=w.upper(), limit=1000)
    hyphen = _get_shipments(
        api,
        search=f"{w[:len(w)//2]}-{w[len(w)//2:]}",
        limit=1000,
    )
    l_ids = {d["id"] for d in lower}
    u_ids = {d["id"] for d in upper}
    h_ids = {d["id"] for d in hyphen}
    assert l_ids == u_ids, f"case-sensitivity leak: {w}"
    assert l_ids == h_ids, f"hyphen-sensitivity leak: {w}"


# ── T5. Weight-unit collapse ────────────────────────────────────────
def test_t5_weight_unit_collapse(api):
    # Best-effort: search on a common weight literal seen in the data.
    for probe in ("100gm", "100 gram", "100g", "500g", "250gm", "1kg"):
        r = _get_shipments(api, search=probe, limit=1000)
        if r:
            # If any weight variant matches, the collapsed forms
            # ("100 gram" ↔ "100g" ↔ "100gm") should return the SAME
            # id set.  Pick the number+unit pair from `probe` and
            # verify equivalence with two alternate spellings.
            digits = "".join(ch for ch in probe if ch.isdigit())
            if not digits:
                continue
            a = _get_shipments(api, search=f"{digits}gm", limit=1000)
            b = _get_shipments(api, search=f"{digits} gram", limit=1000)
            c = _get_shipments(api, search=f"{digits}g", limit=1000)
            a_ids = {d["id"] for d in a}
            b_ids = {d["id"] for d in b}
            c_ids = {d["id"] for d in c}
            assert a_ids == b_ids == c_ids, (
                f"weight-unit collapse mismatch for {digits}: "
                f"gm={len(a_ids)} gram={len(b_ids)} g={len(c_ids)}"
            )
            return
    pytest.skip("no weight-suffix items in workspace to probe")


# ── T6. Suggestions endpoint parity (THE CRITICAL BUG) ──────────────
def test_t6_suggestions_parity(api):
    r = api.get(
        f"{BASE_URL}/api/shipments/product-suggestions",
        params={"limit": 20, "min_count": 2},
        timeout=30,
    )
    assert r.status_code == 200, (
        f"product-suggestions {r.status_code}: {r.text[:200]}"
    )
    payload = r.json()
    suggestions = payload.get("suggestions") or []
    if not suggestions:
        pytest.skip(
            "no suggestions returned — dataset lacks recurring items "
            "(min_count=2). T6 parity trivially holds."
        )
    mismatches = []
    for s in suggestions:
        display = s["display"]
        expected = int(s["count"])
        r2 = _get_shipments(api, search=display, limit=10000)
        actual = len(r2)
        if actual != expected:
            mismatches.append({
                "display":  display,
                "norm":     s.get("norm"),
                "expected": expected,
                "actual":   actual,
            })
    assert not mismatches, (
        "Suggested Filters count ≠ search result length — this is the "
        f"exact bug the user reported.  Mismatches: {mismatches[:5]}"
    )


# ── T8. Regression — combined filters still respected ───────────────
def test_t8_combined_filters(api):
    docs = _get_shipments(api, limit=500)
    # Find any status the workspace actually carries.
    from collections import Counter
    statuses = Counter(d.get("status") for d in docs if d.get("status"))
    if not statuses:
        pytest.skip("no statuses in workspace")
    stat, _ = statuses.most_common(1)[0]
    only_stat = _get_shipments(api, status=stat, limit=1000)
    for d in only_stat:
        assert d.get("status") == stat or (
            stat == "Ready to Ship"
            and d.get("status") in {"Dispatch", "Dispatched",
                                    "ReadyToShip", "READY_TO_SHIP"}
        ), f"status filter leaked: {d.get('status')} vs {stat}"
    # And status + a broad search term shouldn't crash.
    combo = _get_shipments(api, status=stat, search="a", limit=1000)
    assert isinstance(combo, list)


# ── T9. Write path — create + update refresh the blob ────────────────
def test_t9_write_path_updates_blob(api):
    magic_customer = "TESTBLOB_XYZ_9k7"
    magic_item     = "TESTPROD_ABC_2v"
    magic_item_2   = "TESTPROD_CHANGED_4b"

    # 1. Create the shipment.
    import uuid as _uuid
    r = api.post(
        f"{BASE_URL}/api/shipments",
        json={
            "tracking_id":    f"TEST-{_uuid.uuid4().hex[:8]}",
            "customer_name":  magic_customer,
            "customer_phone": "9998887771",
            "address_line1":  "1 Test Rd",
            "city":           "Testville",
            "state":          "Gujarat",
            "pincode":        "395003",
            "items":          [magic_item],
            "weight":         "500 g",
            "payment_mode":   "Prepaid",
            "amount":         100.0,
            "cod_amount":     0.0,
            "token_amount":   0.0,
        },
        timeout=30,
    )
    if r.status_code == 402:
        pytest.skip(f"plan blocked create: {r.text[:200]}")
    assert r.status_code == 200, (
        f"POST /shipments {r.status_code}: {r.text[:300]}"
    )
    created = r.json()
    ship_id = created["id"]

    try:
        # 2. Search on the magic customer name.
        r1 = _get_shipments(api, search=magic_customer, limit=1000)
        assert any(d["id"] == ship_id for d in r1), (
            f"search on customer_name {magic_customer!r} did not "
            f"return the freshly-created shipment {ship_id}"
        )

        # 3. Search on the item.
        r2 = _get_shipments(api, search=magic_item, limit=1000)
        assert any(d["id"] == ship_id for d in r2), (
            f"search on item {magic_item!r} did not return {ship_id} "
            f"— _search_blob missing item on create"
        )

        # 4. Update items -> new value should be searchable, old should NOT.
        r3 = api.put(
            f"{BASE_URL}/api/shipments/{ship_id}",
            json={"items": [magic_item_2]},
            timeout=30,
        )
        assert r3.status_code == 200, (
            f"PUT /shipments {r3.status_code}: {r3.text[:300]}"
        )
        # Give the async blob refresh a beat.
        import time as _t
        _t.sleep(0.5)

        r4 = _get_shipments(api, search=magic_item_2, limit=1000)
        assert any(d["id"] == ship_id for d in r4), (
            f"search on changed item {magic_item_2!r} missed {ship_id} "
            f"— PUT did not refresh _search_blob"
        )
        r5 = _get_shipments(api, search=magic_item, limit=1000)
        assert not any(d["id"] == ship_id for d in r5), (
            f"search on OLD item {magic_item!r} STILL returns {ship_id} "
            f"— blob refresh did not drop old tokens"
        )
    finally:
        # Cleanup (soft delete — Phase-33 flips to Cancelled).
        try:
            api.delete(
                f"{BASE_URL}/api/shipments/{ship_id}",
                timeout=15,
            )
        except Exception:
            pass


# ── T7. Multilingual (best-effort) ──────────────────────────────────
def test_t7_multilingual_best_effort(api):
    # Create a shipment with a Gujarati city, then search in English.
    r = api.post(
        f"{BASE_URL}/api/shipments",
        json={
            "tracking_id":    f"TESTMULTI-{__import__('uuid').uuid4().hex[:8]}",
            "customer_name":  "TESTBLOB_MULTI",
            "customer_phone": "9998887772",
            "address_line1":  "1 Multi Rd",
            "city":           "ભાવનગર",   # Gujarati "Bhavnagar"
            "state":          "Gujarat",
            "pincode":        "364001",
            "items":          ["Widget"],
            "weight":         "100 g",
            "payment_mode":   "Prepaid",
            "amount":         50.0,
            "cod_amount":     0.0,
            "token_amount":   0.0,
        },
        timeout=30,
    )
    if r.status_code == 402:
        pytest.skip(f"plan blocked create: {r.text[:200]}")
    assert r.status_code == 200, (
        f"POST /shipments {r.status_code}: {r.text[:300]}"
    )
    ship_id = r.json()["id"]
    try:
        # Search in Latin — schwa-compact skeleton match should hit it.
        for probe in ("Bhavnagar", "bhavnagar", "BHAVNAGAR"):
            r2 = _get_shipments(api, search=probe, limit=1000)
            if any(d["id"] == ship_id for d in r2):
                return
        pytest.fail(
            "Gujarati city ભાવનગર did not match English 'Bhavnagar' via "
            "the schwa-compact skeleton — multilingual fold failed."
        )
    finally:
        try:
            api.delete(f"{BASE_URL}/api/shipments/{ship_id}", timeout=15)
        except Exception:
            pass
