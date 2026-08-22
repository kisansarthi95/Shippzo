"""Phase F11.K — Rule-First, AI-Fallback for Smart Paste.

Validates:
  1. Structured/labeled paste → regex short-circuits, LLM NOT called
     (ai.source='regex', ai.used=false).
  2. Unstructured/messy paste → LLM fallback triggers
     (ai.source='llm', ai.used=true) when LLM succeeds.
  3. Partial paste (missing pincode) → LLM path taken.
  4. use_ai=false payload forces regex-only even for unstructured input.
  5. Same behaviour on /smart-paste/check-duplicate.
  6. Auth guard preserved (401/403 on missing token).
  7. Response still contains full `fields` + `confidence` on regex path.
"""
import os
import pytest
import requests

BASE_URL = (
    os.environ.get("EXPO_PUBLIC_BACKEND_URL")
    or os.environ.get("EXPO_BACKEND_URL")
).rstrip("/")

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASSWORD = "Admin@12345"


# ────────────────────────── fixtures ──────────────────────────

@pytest.fixture(scope="module")
def api_client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def auth_token(api_client):
    r = api_client.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=15,
    )
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text}"
    tok = r.json().get("token")
    assert tok, "No token from /api/auth/login"
    return tok


@pytest.fixture(scope="module")
def auth_headers(auth_token):
    return {
        "Authorization": f"Bearer {auth_token}",
        "Content-Type": "application/json",
    }


# ─────────────────── canonical test payloads ───────────────────

STRUCTURED_FULL = (
    "Name: Ravi Patel\n"
    "Phone: 9876543210\n"
    "Address: 12 MG Road, Anand Vihar\n"
    "City: Ahmedabad\n"
    "Pincode: 380015\n"
)

STRUCTURED_MISSING_PINCODE = (
    "Name: Ravi Patel\n"
    "Phone: 9876543210\n"
    "Address: 12 MG Road, Anand Vihar\n"
    "City: Ahmedabad\n"
)

UNSTRUCTURED_MESSY = (
    "hey bhai kal courier bhejo Ravi ke ghar\n"
    "9876543210 milega uspe\n"
    "12 MG road anand vihar surat area\n"
    "cod 1500 rs saree 2 pcs\n"
)


# ────────── /smart-paste/parse — regex short-circuit ──────────

class TestSmartPasteParseRuleFirst:

    def test_structured_full_paste_skips_llm(self, api_client, auth_headers):
        """Fully labeled paste → ai.source='regex', ai.used=false."""
        r = api_client.post(
            f"{BASE_URL}/api/smart-paste/parse",
            json={"text": STRUCTURED_FULL},
            headers=auth_headers,
            timeout=30,
        )
        assert r.status_code == 200, f"HTTP {r.status_code}: {r.text}"
        data = r.json()
        ai = data.get("ai") or {}
        assert ai.get("source") == "regex", (
            f"Expected ai.source='regex' for fully-labeled paste, "
            f"got {ai!r}. fields={data.get('fields')}"
        )
        assert ai.get("used") is False, (
            f"Expected ai.used=False, got {ai!r}"
        )
        assert "AI skipped" in (ai.get("reason") or "") or \
               "regex" in (ai.get("reason") or "").lower(), (
            f"Expected reason to mention AI skipped, got {ai!r}"
        )

    def test_structured_paste_returns_fields_and_confidence(
        self, api_client, auth_headers,
    ):
        """Regex short-circuit still returns full `fields` + `confidence`."""
        r = api_client.post(
            f"{BASE_URL}/api/smart-paste/parse",
            json={"text": STRUCTURED_FULL},
            headers=auth_headers,
            timeout=30,
        )
        assert r.status_code == 200
        data = r.json()
        fields = data.get("fields") or {}
        assert fields.get("customer_name"), "customer_name missing"
        assert fields.get("customer_phone") == "9876543210"
        assert fields.get("address_line1"), "address_line1 missing"
        assert fields.get("city"), "city missing"
        assert fields.get("pincode") == "380015"
        # confidence dict must be present (may be empty for some keys)
        assert "confidence" in data, "confidence block missing"
        assert isinstance(data["confidence"], dict)

    def test_unstructured_paste_calls_llm(self, api_client, auth_headers):
        """Messy unlabeled paste → LLM fallback fires."""
        r = api_client.post(
            f"{BASE_URL}/api/smart-paste/parse",
            json={"text": UNSTRUCTURED_MESSY},
            headers=auth_headers,
            timeout=45,
        )
        assert r.status_code == 200, f"HTTP {r.status_code}: {r.text}"
        data = r.json()
        ai = data.get("ai") or {}
        # If LLM key is available, we expect source='llm'. If LLM
        # times out / fails, source stays 'regex' but the SHORT-CIRCUIT
        # reason must NOT be present (it should be an error path).
        if ai.get("source") == "llm":
            assert ai.get("used") is True, f"Expected used=True: {ai!r}"
        else:
            # LLM failed — that's a separate infrastructure issue, but
            # crucially the regex short-circuit reason must NOT appear
            # because regex did NOT extract all mandatory fields.
            assert "AI skipped" not in (ai.get("reason") or ""), (
                f"Regex short-circuit fired on unstructured paste! ai={ai!r}"
            )

    def test_partial_paste_missing_pincode_calls_llm(
        self, api_client, auth_headers,
    ):
        """Labeled paste missing pincode → must attempt LLM fallback."""
        r = api_client.post(
            f"{BASE_URL}/api/smart-paste/parse",
            json={"text": STRUCTURED_MISSING_PINCODE},
            headers=auth_headers,
            timeout=45,
        )
        assert r.status_code == 200, f"HTTP {r.status_code}: {r.text}"
        data = r.json()
        ai = data.get("ai") or {}
        # Regex-complete short-circuit MUST NOT fire — pincode is missing.
        assert "AI skipped" not in (ai.get("reason") or ""), (
            f"Regex short-circuit fired despite missing pincode! ai={ai!r}"
        )
        # ai.source can be 'llm' (success) or 'regex' (LLM failed);
        # what matters is that we DIDN'T assert "AI skipped".

    def test_use_ai_false_forces_regex(self, api_client, auth_headers):
        """use_ai=false → skip LLM even for messy paste."""
        r = api_client.post(
            f"{BASE_URL}/api/smart-paste/parse",
            json={"text": UNSTRUCTURED_MESSY, "use_ai": False},
            headers=auth_headers,
            timeout=15,
        )
        assert r.status_code == 200, f"HTTP {r.status_code}: {r.text}"
        data = r.json()
        ai = data.get("ai") or {}
        assert ai.get("source") == "regex", (
            f"use_ai=false must yield source='regex', got {ai!r}"
        )
        assert ai.get("used") is False, f"Expected used=False, got {ai!r}"


# ─────────── /smart-paste/check-duplicate mirror suite ──────────

class TestSmartPasteCheckDuplicateRuleFirst:

    def test_structured_paste_skips_llm(self, api_client, auth_headers):
        r = api_client.post(
            f"{BASE_URL}/api/smart-paste/check-duplicate",
            json={"text": STRUCTURED_FULL},
            headers=auth_headers,
            timeout=30,
        )
        assert r.status_code == 200, f"HTTP {r.status_code}: {r.text}"
        data = r.json()
        ai = data.get("ai") or {}
        assert ai.get("source") == "regex", (
            f"check-duplicate: expected source='regex' for full paste, "
            f"got {ai!r}"
        )
        assert ai.get("used") is False, f"Expected used=False: {ai!r}"

    def test_unstructured_paste_invokes_llm(self, api_client, auth_headers):
        r = api_client.post(
            f"{BASE_URL}/api/smart-paste/check-duplicate",
            json={"text": UNSTRUCTURED_MESSY},
            headers=auth_headers,
            timeout=45,
        )
        assert r.status_code == 200, f"HTTP {r.status_code}: {r.text}"
        data = r.json()
        ai = data.get("ai") or {}
        # Same tolerance as above — LLM must at least be attempted.
        if ai.get("source") == "llm":
            assert ai.get("used") is True
        else:
            # If LLM path failed, the "AI skipped" reason must NOT be set,
            # because regex did NOT hit the short-circuit.
            assert "AI skipped" not in (ai.get("reason") or ""), (
                f"Regex short-circuit fired on unstructured paste in "
                f"check-duplicate! ai={ai!r}"
            )

    def test_response_shape(self, api_client, auth_headers):
        """Ensure the endpoint returns the documented top-level keys."""
        r = api_client.post(
            f"{BASE_URL}/api/smart-paste/check-duplicate",
            json={"text": STRUCTURED_FULL},
            headers=auth_headers,
            timeout=30,
        )
        assert r.status_code == 200
        data = r.json()
        for k in ("fields", "confidence", "warnings", "duplicates", "ai"):
            assert k in data, f"Response missing key '{k}'"


# ──────────────────────── auth guard ────────────────────────

class TestAuthGuard:

    def test_parse_requires_auth(self, api_client):
        r = api_client.post(
            f"{BASE_URL}/api/smart-paste/parse",
            json={"text": STRUCTURED_FULL},
            timeout=15,
        )
        assert r.status_code in (401, 403), (
            f"Expected 401/403 without token, got {r.status_code}: {r.text}"
        )

    def test_check_duplicate_requires_auth(self, api_client):
        r = api_client.post(
            f"{BASE_URL}/api/smart-paste/check-duplicate",
            json={"text": STRUCTURED_FULL},
            timeout=15,
        )
        assert r.status_code in (401, 403), (
            f"Expected 401/403 without token, got {r.status_code}: {r.text}"
        )
