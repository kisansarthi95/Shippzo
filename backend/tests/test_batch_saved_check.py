"""
Tests for POST /api/contacts/batch-saved-check
Phase: Contact-Saved Icon Rehydration (Shipments screen fix)
"""
import os
import time
import uuid
import pytest
import requests


BASE_URL = (
    os.environ.get("EXPO_PUBLIC_BACKEND_URL")
    or os.environ.get("EXPO_BACKEND_URL")
).rstrip("/")

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASSWORD = "Admin@12345"
USER2_EMAIL = "user2@test.com"
USER2_PASSWORD = "User@12345"


# ---------------------- Fixtures ----------------------

def _login(email: str, password: str) -> str:
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": email, "password": password},
        timeout=15,
    )
    assert r.status_code == 200, f"login {email} failed: {r.status_code} {r.text}"
    return r.json()["token"]


@pytest.fixture(scope="module")
def admin_token() -> str:
    return _login(ADMIN_EMAIL, ADMIN_PASSWORD)


@pytest.fixture(scope="module")
def user2_token() -> str:
    return _login(USER2_EMAIL, USER2_PASSWORD)


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# Utility: mark a contact as saved for a given token
def _mark_saved(token: str, phone: str, name: str = "", shipment_id: str = ""):
    r = requests.post(
        f"{BASE_URL}/api/contacts/mark-saved",
        headers=_headers(token),
        json={"phone": phone, "name": name, "shipment_id": shipment_id},
        timeout=15,
    )
    assert r.status_code == 200, f"mark-saved failed: {r.status_code} {r.text}"
    return r.json()


# ---------------------- Auth Guard ----------------------

class TestAuthGuard:
    def test_batch_without_token_returns_401(self):
        r = requests.post(
            f"{BASE_URL}/api/contacts/batch-saved-check",
            json={"phones": []},
            timeout=15,
        )
        # FastAPI/HTTPBearer usually returns 401 or 403
        assert r.status_code in (401, 403), f"got {r.status_code}"

    def test_batch_with_bad_token_returns_401(self):
        r = requests.post(
            f"{BASE_URL}/api/contacts/batch-saved-check",
            headers={"Authorization": "Bearer garbage", "Content-Type": "application/json"},
            json={"phones": []},
            timeout=15,
        )
        assert r.status_code in (401, 403)


# ---------------------- Empty / edge inputs ----------------------

class TestEmptyEdges:
    def test_empty_phones_list(self, admin_token):
        r = requests.post(
            f"{BASE_URL}/api/contacts/batch-saved-check",
            headers=_headers(admin_token),
            json={"phones": []},
            timeout=15,
        )
        assert r.status_code == 200
        assert r.json() == {"results": []}

    def test_all_empty_phone_values_return_saved_false(self, admin_token):
        payload = {
            "phones": [
                {"shipment_id": "sid-empty-1", "phone": ""},
                {"shipment_id": "sid-empty-2", "phone": "   "},
                {"shipment_id": "sid-empty-3", "phone": "abc-xyz"},  # no digits
            ]
        }
        r = requests.post(
            f"{BASE_URL}/api/contacts/batch-saved-check",
            headers=_headers(admin_token),
            json=payload,
            timeout=15,
        )
        assert r.status_code == 200
        results = r.json()["results"]
        assert len(results) == 3
        for row in results:
            assert row["saved"] is False
            assert row["shipment_id"].startswith("sid-empty-")


# ---------------------- Core behaviour ----------------------

class TestCoreBehaviour:
    def test_batch_reflects_mark_saved(self, admin_token):
        uniq = uuid.uuid4().hex[:8]
        # Fake but valid Indian mobile number - unique to avoid collisions
        phone_a = f"98{uniq[:8]}"  # 10 digits
        phone_b = f"97{uniq[:8]}"
        sid_a = f"TEST_shp_{uniq}_a"
        sid_b = f"TEST_shp_{uniq}_b"
        sid_c = f"TEST_shp_{uniq}_c"  # will remain unsaved

        _mark_saved(admin_token, phone_a, name="TEST A", shipment_id=sid_a)

        payload = {
            "phones": [
                {"shipment_id": sid_a, "phone": phone_a},
                {"shipment_id": sid_b, "phone": phone_b},  # not saved
                {"shipment_id": sid_c, "phone": ""},       # empty → false
            ]
        }
        r = requests.post(
            f"{BASE_URL}/api/contacts/batch-saved-check",
            headers=_headers(admin_token),
            json=payload,
            timeout=15,
        )
        assert r.status_code == 200
        results = {row["shipment_id"]: row["saved"] for row in r.json()["results"]}
        assert results[sid_a] is True, f"phone_a should be saved: {r.json()}"
        assert results[sid_b] is False
        assert results[sid_c] is False

    def test_normalization_matches_single_saved_check(self, admin_token):
        """Phone normalization: strip non-digits, keep last 10 digits.
        Various formats of the SAME number should all resolve identically."""
        # Use only digits for the phone (uuid.hex includes a-f which would
        # be stripped and break the 10-digit assumption).
        uniq_digits = f"{uuid.uuid4().int}"[:8]
        base10 = f"96{uniq_digits}"
        _mark_saved(admin_token, base10, name="TEST N")

        variants = [
            base10,                      # 10 digits
            f"+91 {base10}",             # +91 prefix with space
            f"91{base10}",               # country code no plus
            f"+91-{base10[:5]}-{base10[5:]}",  # dashes
            f"0{base10}",                # leading 0 (still >=10 digits)
        ]
        payload_phones = [
            {"shipment_id": f"var-{i}", "phone": v} for i, v in enumerate(variants)
        ]

        batch = requests.post(
            f"{BASE_URL}/api/contacts/batch-saved-check",
            headers=_headers(admin_token),
            json={"phones": payload_phones},
            timeout=15,
        )
        assert batch.status_code == 200
        batch_results = {row["shipment_id"]: row["saved"] for row in batch.json()["results"]}

        # Cross-check with /contacts/saved-check per variant → results must agree
        for i, v in enumerate(variants):
            single = requests.get(
                f"{BASE_URL}/api/contacts/saved-check",
                headers=_headers(admin_token),
                params={"phone": v},
                timeout=15,
            )
            assert single.status_code == 200, single.text
            single_saved = bool(single.json().get("saved"))
            assert batch_results[f"var-{i}"] == single_saved, (
                f"mismatch for variant {v!r}: batch={batch_results[f'var-{i}']}, "
                f"single={single_saved}"
            )
            assert single_saved is True, f"variant {v!r} should resolve to same saved contact"

    def test_repeat_customer_multiple_shipments_same_phone(self, admin_token):
        """If two shipments share the same phone, BOTH must be flagged saved."""
        uniq = uuid.uuid4().hex[:8]
        phone = f"95{uniq[:8]}"
        sid1 = f"TEST_repeat_{uniq}_1"
        sid2 = f"TEST_repeat_{uniq}_2"
        sid3 = f"TEST_repeat_{uniq}_3"

        _mark_saved(admin_token, phone, name="TEST Repeat")

        r = requests.post(
            f"{BASE_URL}/api/contacts/batch-saved-check",
            headers=_headers(admin_token),
            json={
                "phones": [
                    {"shipment_id": sid1, "phone": phone},
                    {"shipment_id": sid2, "phone": phone},
                    {"shipment_id": sid3, "phone": phone},
                ]
            },
            timeout=15,
        )
        assert r.status_code == 200
        results = r.json()["results"]
        assert len(results) == 3
        for row in results:
            assert row["saved"] is True, f"repeat customer should be all saved: {results}"

    def test_result_preserves_input_order_and_duplicates(self, admin_token):
        """Response ordering + count must mirror input.phones exactly."""
        uniq = uuid.uuid4().hex[:8]
        phone = f"94{uniq[:8]}"
        _mark_saved(admin_token, phone)

        phones = [
            {"shipment_id": "X-1", "phone": phone},
            {"shipment_id": "X-2", "phone": ""},
            {"shipment_id": "X-1", "phone": phone},   # duplicate sid
            {"shipment_id": "X-3", "phone": "0000000000"},  # not saved
        ]
        r = requests.post(
            f"{BASE_URL}/api/contacts/batch-saved-check",
            headers=_headers(admin_token),
            json={"phones": phones},
            timeout=15,
        )
        assert r.status_code == 200
        results = r.json()["results"]
        assert len(results) == 4
        assert [row["shipment_id"] for row in results] == ["X-1", "X-2", "X-1", "X-3"]
        assert [row["saved"] for row in results] == [True, False, True, False]


# ---------------------- Scope Isolation ----------------------

class TestScopeIsolation:
    def test_user_isolation(self, admin_token, user2_token):
        """A contact saved by admin must NOT show as saved for user2."""
        uniq = uuid.uuid4().hex[:8]
        phone = f"93{uniq[:8]}"
        sid = f"TEST_iso_{uniq}"

        # admin saves the contact
        _mark_saved(admin_token, phone, name="TEST admin's contact")

        # admin sees it
        admin_res = requests.post(
            f"{BASE_URL}/api/contacts/batch-saved-check",
            headers=_headers(admin_token),
            json={"phones": [{"shipment_id": sid, "phone": phone}]},
            timeout=15,
        )
        assert admin_res.status_code == 200
        assert admin_res.json()["results"][0]["saved"] is True

        # user2 does NOT see it
        u2_res = requests.post(
            f"{BASE_URL}/api/contacts/batch-saved-check",
            headers=_headers(user2_token),
            json={"phones": [{"shipment_id": sid, "phone": phone}]},
            timeout=15,
        )
        assert u2_res.status_code == 200
        assert u2_res.json()["results"][0]["saved"] is False, (
            f"user2 should NOT see admin's saved contact! got: {u2_res.json()}"
        )


# ---------------------- Perf: Single-query optimisation ----------------------

class TestPerformanceSingleQuery:
    def test_large_batch_response_time_reasonable(self, admin_token):
        """20+ pairs should complete in < 2s (single MongoDB $in query).
        This isn't a strict perf test — it just proves we're not doing
        N sequential queries. Anything >5s flags a regression."""
        uniq = uuid.uuid4().hex[:8]
        # Save 10 contacts, leave 15 unsaved → total 25 pairs
        saved_phones = [f"90{uniq[:6]}{i:02d}" for i in range(10)]
        for p in saved_phones:
            _mark_saved(admin_token, p)

        unsaved_phones = [f"80{uniq[:6]}{i:02d}" for i in range(15)]

        phones_payload = []
        for i, p in enumerate(saved_phones):
            phones_payload.append({"shipment_id": f"S-{i}", "phone": p})
        for i, p in enumerate(unsaved_phones):
            phones_payload.append({"shipment_id": f"U-{i}", "phone": p})

        t0 = time.time()
        r = requests.post(
            f"{BASE_URL}/api/contacts/batch-saved-check",
            headers=_headers(admin_token),
            json={"phones": phones_payload},
            timeout=30,
        )
        elapsed = time.time() - t0
        assert r.status_code == 200
        results = r.json()["results"]
        assert len(results) == 25
        saved_count = sum(1 for row in results if row["saved"])
        assert saved_count == 10, (
            f"expected 10 saved, got {saved_count}: {results}"
        )
        # generous perf guard: batch of 25 should stay under 5s round-trip
        assert elapsed < 5.0, f"batch took too long: {elapsed:.2f}s (should be <5s)"
        print(f"[perf] 25-pair batch completed in {elapsed*1000:.0f} ms")
