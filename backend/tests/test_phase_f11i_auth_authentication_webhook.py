"""
Phase F11.I — Authentication event webhook_url plumbing.

Verifies:
  1. GET /events returns 'auth_authentication' pre-populated with the
     default FlowConnect webhook URL (seed OR backfill).
  2. PUT /events/auth_authentication with { webhook_url } persists.
  3. PUT /events/auth_authentication with { label } persists (F11.H).
  4. GET /available-fields includes the 'otp_type' field (F11.G).
  5. dispatch_event() routes auth_authentication to the webhook_url —
     asserted indirectly via POST /api/auth/otp/request whose delivery
     block reports provider='event_trigger' rather than being skipped
     with 'provider not fully configured'.
  6. EventTriggerUpdate model accepts 'label' key in PATCH payloads
     (empty/whitespace label MUST be ignored, not wipe existing).
"""
from __future__ import annotations

import os
import time

import pytest
import requests

def _read_frontend_env_url() -> str:
    """Read EXPO_PUBLIC_BACKEND_URL from frontend/.env (or fall back to
    the env var if it happens to be exported)."""
    v = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "").strip()
    if v:
        return v
    try:
        with open("/app/frontend/.env", "r") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("EXPO_PUBLIC_BACKEND_URL="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    return val
    except Exception:
        pass
    return ""


BASE_URL = _read_frontend_env_url().rstrip("/")
assert BASE_URL, "EXPO_PUBLIC_BACKEND_URL missing from frontend/.env"

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASSWORD = "Admin@12345"

DEFAULT_AUTH_WEBHOOK = (
    "https://login.flowconnect.ai/api/automations/69ff6d211a1dc/execute"
)


# ────────────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def admin_token() -> str:
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=15,
    )
    if r.status_code != 200:
        pytest.skip(f"Admin login failed: {r.status_code} {r.text[:200]}")
    data = r.json()
    assert data.get("token"), "login response missing token"
    return data["token"]


@pytest.fixture(scope="module")
def admin_headers(admin_token: str) -> dict:
    return {"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"}


@pytest.fixture(scope="module")
def original_auth_event(admin_headers) -> dict:
    """Snapshot the current auth_authentication doc so we can restore."""
    r = requests.get(
        f"{BASE_URL}/api/admin/whatsapp-provider/events/auth_authentication",
        headers=admin_headers, timeout=15,
    )
    if r.status_code == 200:
        return r.json().get("item", {})
    return {}


# ────────────────────────────────────────────────────────────────────
# 1. GET /events → auth_authentication default webhook_url populated
# ────────────────────────────────────────────────────────────────────
class TestAuthEventSeed:
    def test_events_list_contains_auth_authentication(self, admin_headers):
        r = requests.get(
            f"{BASE_URL}/api/admin/whatsapp-provider/events",
            headers=admin_headers, timeout=15,
        )
        assert r.status_code == 200, r.text
        items = r.json().get("items") or []
        keys = [it.get("event_key") for it in items]
        assert "auth_authentication" in keys, f"missing auth_authentication in {keys}"

    def test_auth_authentication_has_default_webhook(self, admin_headers, original_auth_event):
        # If operator wiped webhook to blank previously, backfill should
        # have refilled it on startup. Assert canonical default OR
        # that an operator-set value is present.
        wh = (original_auth_event.get("webhook_url") or "").strip()
        # Requirement: seed OR backfill guarantees webhook_url is set.
        assert wh, (
            "auth_authentication.webhook_url is BLANK — seed/backfill "
            "did not populate the default FlowConnect URL"
        )
        # If it's the seed default we assert canonical value.
        # (If operator has overridden it we only require non-empty.)
        if "flowconnect.ai" in wh:
            assert wh == DEFAULT_AUTH_WEBHOOK, (
                f"expected default webhook={DEFAULT_AUTH_WEBHOOK!r}, got {wh!r}"
            )

    def test_auth_authentication_category_and_label(self, admin_headers):
        r = requests.get(
            f"{BASE_URL}/api/admin/whatsapp-provider/events/auth_authentication",
            headers=admin_headers, timeout=15,
        )
        assert r.status_code == 200, r.text
        item = r.json()["item"]
        assert item["category"] == "auth"
        assert item["label"], "label must be non-empty"


# ────────────────────────────────────────────────────────────────────
# 2 + 3. PUT persists webhook_url + label (Phase F11.H)
# ────────────────────────────────────────────────────────────────────
class TestAuthEventUpdate:
    def test_put_webhook_url_persists(self, admin_headers, original_auth_event):
        new_url = "https://example.com/f11i-webhook-test"
        r = requests.put(
            f"{BASE_URL}/api/admin/whatsapp-provider/events/auth_authentication",
            headers=admin_headers, json={"webhook_url": new_url}, timeout=15,
        )
        assert r.status_code == 200, r.text
        assert r.json().get("ok") is True
        item = r.json()["item"]
        assert item["webhook_url"] == new_url

        # GET to verify persistence
        r2 = requests.get(
            f"{BASE_URL}/api/admin/whatsapp-provider/events/auth_authentication",
            headers=admin_headers, timeout=15,
        )
        assert r2.status_code == 200
        assert r2.json()["item"]["webhook_url"] == new_url

        # Restore to original (or canonical default)
        restore_url = (original_auth_event.get("webhook_url") or DEFAULT_AUTH_WEBHOOK)
        requests.put(
            f"{BASE_URL}/api/admin/whatsapp-provider/events/auth_authentication",
            headers=admin_headers, json={"webhook_url": restore_url}, timeout=15,
        )

    def test_put_label_persists(self, admin_headers, original_auth_event):
        new_label = "My Auth Block TEST_F11H"
        r = requests.put(
            f"{BASE_URL}/api/admin/whatsapp-provider/events/auth_authentication",
            headers=admin_headers, json={"label": new_label}, timeout=15,
        )
        assert r.status_code == 200, r.text
        item = r.json()["item"]
        assert item["label"] == new_label, (
            f"label not persisted; got {item.get('label')!r}"
        )

        # GET verify
        r2 = requests.get(
            f"{BASE_URL}/api/admin/whatsapp-provider/events/auth_authentication",
            headers=admin_headers, timeout=15,
        )
        assert r2.json()["item"]["label"] == new_label

        # Restore original label
        orig_label = original_auth_event.get("label") or "Authentication"
        requests.put(
            f"{BASE_URL}/api/admin/whatsapp-provider/events/auth_authentication",
            headers=admin_headers, json={"label": orig_label}, timeout=15,
        )

    def test_put_blank_label_ignored(self, admin_headers):
        """Blank/whitespace label MUST NOT wipe existing label."""
        # First capture current label
        cur = requests.get(
            f"{BASE_URL}/api/admin/whatsapp-provider/events/auth_authentication",
            headers=admin_headers, timeout=15,
        ).json()["item"]["label"]
        r = requests.put(
            f"{BASE_URL}/api/admin/whatsapp-provider/events/auth_authentication",
            headers=admin_headers, json={"label": "   ", "enabled": True}, timeout=15,
        )
        assert r.status_code == 200, r.text
        # Label should be unchanged
        after = requests.get(
            f"{BASE_URL}/api/admin/whatsapp-provider/events/auth_authentication",
            headers=admin_headers, timeout=15,
        ).json()["item"]["label"]
        assert after == cur, f"blank label wiped existing: {cur!r} → {after!r}"

    def test_put_accepts_label_only_payload(self, admin_headers):
        """Regression: EventTriggerUpdate must accept a payload with
        ONLY the label key (nothing else) — F11.H inline rename UX."""
        r = requests.put(
            f"{BASE_URL}/api/admin/whatsapp-provider/events/auth_authentication",
            headers=admin_headers, json={"label": "F11H Label Only"}, timeout=15,
        )
        assert r.status_code == 200, r.text


# ────────────────────────────────────────────────────────────────────
# 4. GET /available-fields includes otp_type (Phase F11.G)
# ────────────────────────────────────────────────────────────────────
class TestAvailableFields:
    def test_otp_type_in_available_fields(self, admin_headers):
        r = requests.get(
            f"{BASE_URL}/api/admin/whatsapp-provider/available-fields",
            headers=admin_headers, timeout=15,
        )
        assert r.status_code == 200, r.text
        fields = r.json().get("fields") or []
        keys = [f.get("key") for f in fields]
        assert "otp_type" in keys, (
            f"otp_type missing from available-fields. Keys sample: {keys[:20]}"
        )
        # Verify friendly section/label
        otp_type_entry = next((f for f in fields if f["key"] == "otp_type"), None)
        assert otp_type_entry
        assert otp_type_entry.get("section") == "Auth"


# ────────────────────────────────────────────────────────────────────
# 5. OTP dispatch → not skipped with 'provider not fully configured'
# ────────────────────────────────────────────────────────────────────
class TestOtpDispatchNotSkipped:
    """Fire /api/auth/otp/request and verify the delivery is attempted
    via the event_trigger path (i.e. the webhook_url wiring works)."""

    def test_ensure_auth_webhook_set(self, admin_headers):
        # Precondition: reset webhook to canonical default in case a
        # prior test left it blank.
        requests.put(
            f"{BASE_URL}/api/admin/whatsapp-provider/events/auth_authentication",
            headers=admin_headers,
            json={"webhook_url": DEFAULT_AUTH_WEBHOOK, "enabled": True},
            timeout=15,
        )
        r = requests.get(
            f"{BASE_URL}/api/admin/whatsapp-provider/events/auth_authentication",
            headers=admin_headers, timeout=15,
        )
        assert r.json()["item"]["webhook_url"] == DEFAULT_AUTH_WEBHOOK

    def test_otp_request_login_dispatch(self):
        # Use a fresh-looking phone to sidestep the 60s cooldown /
        # 30-min lockout limits on this shared test env.
        phone = f"9{int(time.time()) % 1_000_000_000:09d}"
        r = requests.post(
            f"{BASE_URL}/api/auth/otp/request",
            json={"phone": phone, "event_type": "login"},
            timeout=20,
        )
        # 200 = accepted; 429 = rate-limit hit on shared env.
        if r.status_code == 429:
            pytest.skip(f"OTP endpoint rate-limited: {r.text[:120]}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("ok") is True
        delivery = body.get("delivery") or {}
        # Critical assertion — provider must be the event_trigger path
        # (indicates dispatch_event fired and routed to webhook_url).
        # 'unknown' would indicate the auth path was skipped and fell
        # all the way through to no-provider.
        assert delivery.get("channel") == "whatsapp"
        assert delivery.get("provider") in {
            "event_trigger", "flowconnect", "flowconnect_hosted",
        }, (
            f"Auth OTP dispatch was NOT routed via event_trigger — "
            f"delivery={delivery!r} — this is the F11.I bug: the "
            f"webhook_url on auth_authentication was ignored / skipped."
        )

    def test_otp_request_signup_dispatch(self):
        phone = f"8{int(time.time() * 1000) % 1_000_000_000:09d}"
        r = requests.post(
            f"{BASE_URL}/api/auth/otp/request",
            json={"phone": phone, "event_type": "signup"},
            timeout=20,
        )
        if r.status_code == 429:
            pytest.skip(f"OTP endpoint rate-limited: {r.text[:120]}")
        assert r.status_code == 200, r.text
        delivery = r.json().get("delivery") or {}
        assert delivery.get("provider") in {
            "event_trigger", "flowconnect", "flowconnect_hosted",
        }, f"signup OTP delivery not routed via event_trigger: {delivery!r}"

    def test_otp_request_password_reset_dispatch(self):
        phone = f"7{int(time.time() * 7) % 1_000_000_000:09d}"
        r = requests.post(
            f"{BASE_URL}/api/auth/otp/request",
            json={"phone": phone, "event_type": "password_reset"},
            timeout=20,
        )
        if r.status_code == 429:
            pytest.skip(f"OTP endpoint rate-limited: {r.text[:120]}")
        assert r.status_code == 200, r.text
        delivery = r.json().get("delivery") or {}
        assert delivery.get("provider") in {
            "event_trigger", "flowconnect", "flowconnect_hosted",
        }, f"reset OTP delivery not routed via event_trigger: {delivery!r}"


# ────────────────────────────────────────────────────────────────────
# 6. Negative: with webhook_url wiped, auth STILL dispatches (fallback
#    to global base_url per Phase F11.I priority order).
# ────────────────────────────────────────────────────────────────────
class TestAuthWebhookFallback:
    def test_wipe_webhook_then_dispatch_falls_back_to_base_url(self, admin_headers, original_auth_event):
        # Clear the webhook_url to force fallback path.
        r = requests.put(
            f"{BASE_URL}/api/admin/whatsapp-provider/events/auth_authentication",
            headers=admin_headers, json={"webhook_url": ""}, timeout=15,
        )
        assert r.status_code == 200, r.text
        assert r.json()["item"]["webhook_url"] == ""

        try:
            phone = f"6{int(time.time() * 13) % 1_000_000_000:09d}"
            r = requests.post(
                f"{BASE_URL}/api/auth/otp/request",
                json={"phone": phone, "event_type": "login"},
                timeout=20,
            )
            if r.status_code == 429:
                pytest.skip(f"OTP endpoint rate-limited: {r.text[:120]}")
            assert r.status_code == 200, r.text
            delivery = r.json().get("delivery") or {}
            # With webhook_url blank, dispatcher must fall back to
            # base_url (event_trigger) — NOT skip with 'provider not
            # fully configured'.
            assert delivery.get("provider") in {
                "event_trigger", "flowconnect", "flowconnect_hosted",
            }, (
                f"Fallback to global base_url broken — delivery={delivery!r}"
            )
        finally:
            # Restore canonical default so future runs / users are OK.
            requests.put(
                f"{BASE_URL}/api/admin/whatsapp-provider/events/auth_authentication",
                headers=admin_headers,
                json={"webhook_url": DEFAULT_AUTH_WEBHOOK},
                timeout=15,
            )
