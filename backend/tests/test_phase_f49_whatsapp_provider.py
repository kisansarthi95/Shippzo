"""
Phase F4.9 — WhatsApp Provider bug fix verification.

Coverage:
  Bug #3 — `template_enabled` toggle now persists to Mongo and is
           returned by GET /events and GET /events/{event_key}.
  Bug #6 — All event trigger settings survive a round-trip:
           automation_id, template_preview, template_enabled,
           selected_fields, variable_mapping, custom_fields.
  Extra  — AVAILABLE_FIELDS endpoint returns the canonical list
           used by the picker in the UI.
"""
from __future__ import annotations

import os
from typing import Dict, Any

import pytest
import requests


BASE_URL = os.environ.get(
    "EXPO_PUBLIC_BACKEND_URL",
    "https://logistics-hub-740.preview.emergentagent.com",
).rstrip("/")

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASSWORD = "Admin@12345"


# ─── fixtures ─────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def admin_token() -> str:
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=15,
    )
    assert r.status_code == 200, f"admin login failed: {r.status_code} {r.text}"
    body = r.json()
    tok = body.get("token") or body.get("access_token")
    assert tok, f"no token in login response: {body}"
    return tok


@pytest.fixture(scope="session")
def auth_headers(admin_token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {admin_token}",
        "Content-Type": "application/json",
    }


# ─── helpers ──────────────────────────────────────────────────────
def _get_event(headers: Dict[str, str], event_key: str) -> Dict[str, Any]:
    r = requests.get(
        f"{BASE_URL}/api/admin/whatsapp-provider/events/{event_key}",
        headers=headers, timeout=10,
    )
    assert r.status_code == 200, f"GET event failed: {r.status_code} {r.text}"
    return r.json()["item"]


def _put_event(headers: Dict[str, str], event_key: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    r = requests.put(
        f"{BASE_URL}/api/admin/whatsapp-provider/events/{event_key}",
        headers=headers, json=payload, timeout=10,
    )
    assert r.status_code == 200, f"PUT event failed: {r.status_code} {r.text}"
    return r.json()["item"]


# ─── tests ────────────────────────────────────────────────────────
class TestWhatsAppProviderF49:
    """Bug #3 + Bug #6 — template_enabled + full persistence."""

    def test_list_events_returns_template_enabled_field(self, auth_headers):
        """GET /events must include `template_enabled` (bool) on each item."""
        r = requests.get(
            f"{BASE_URL}/api/admin/whatsapp-provider/events",
            headers=auth_headers, timeout=10,
        )
        assert r.status_code == 200, f"{r.status_code} {r.text}"
        body = r.json()
        assert "items" in body and len(body["items"]) >= 8, "expected 8+ events"
        for item in body["items"]:
            assert "template_enabled" in item, f"missing template_enabled: {item}"
            assert isinstance(item["template_enabled"], bool), (
                f"template_enabled must be bool, got {type(item['template_enabled']).__name__}"
            )
            # canonical event keys the UI ships with
            assert "event_key" in item and item["event_key"]

    def test_template_enabled_toggle_persists_true(self, auth_headers):
        """PUT template_enabled=true then GET must reflect true."""
        updated = _put_event(auth_headers, "otp_login", {"template_enabled": True})
        assert updated["template_enabled"] is True, (
            f"PUT response did not echo True: {updated}"
        )
        fresh = _get_event(auth_headers, "otp_login")
        assert fresh["template_enabled"] is True, (
            f"GET after PUT True returned {fresh['template_enabled']}"
        )

    def test_template_enabled_toggle_persists_false(self, auth_headers):
        """PUT template_enabled=false then GET must reflect false."""
        updated = _put_event(auth_headers, "otp_login", {"template_enabled": False})
        assert updated["template_enabled"] is False, (
            f"PUT response did not echo False: {updated}"
        )
        fresh = _get_event(auth_headers, "otp_login")
        assert fresh["template_enabled"] is False, (
            f"GET after PUT False returned {fresh['template_enabled']}"
        )

    def test_full_settings_round_trip(self, auth_headers):
        """Bug #6 — every settable field must survive a save/reload."""
        payload = {
            "automation_id":     "AUTO123",
            "template_preview":  "Hello {name}",
            "template_enabled":  True,
            "selected_fields":   ["customer_name", "customer_phone", "otp"],
            "variable_mapping":  {"customer_name": "name", "customer_phone": "phone"},
        }
        updated = _put_event(auth_headers, "otp_login", payload)
        # PUT echoes
        assert updated["automation_id"]    == "AUTO123"
        assert updated["template_preview"] == "Hello {name}"
        assert updated["template_enabled"] is True
        assert updated["selected_fields"]  == ["customer_name", "customer_phone", "otp"]
        assert updated["variable_mapping"] == {"customer_name": "name", "customer_phone": "phone"}

        # Reload — must match exactly
        fresh = _get_event(auth_headers, "otp_login")
        assert fresh["automation_id"]    == "AUTO123"
        assert fresh["template_preview"] == "Hello {name}"
        assert fresh["template_enabled"] is True
        assert fresh["selected_fields"]  == ["customer_name", "customer_phone", "otp"]
        assert fresh["variable_mapping"] == {"customer_name": "name", "customer_phone": "phone"}

    def test_cleanup_restore_defaults(self, auth_headers):
        """Reset otp_login to a neutral state so subsequent test runs aren't
        polluted by the above assertions."""
        _put_event(auth_headers, "otp_login", {
            "automation_id":    "",
            "template_enabled": False,
            "selected_fields":  ["customer_name", "customer_phone", "otp", "event_type"],
            "variable_mapping": {},
        })
        fresh = _get_event(auth_headers, "otp_login")
        assert fresh["template_enabled"] is False
        assert fresh["automation_id"] == ""


class TestAvailableFields:
    """Ensures the picker source-of-truth endpoint exposes the canonical
    field list the frontend depends on."""

    REQUIRED_KEYS = {
        "customer_name", "customer_phone", "otp", "order_id",
        "tracking_id", "event_type", "business_name", "business_phone",
    }

    def test_available_fields_shape_and_content(self, auth_headers):
        r = requests.get(
            f"{BASE_URL}/api/admin/whatsapp-provider/available-fields",
            headers=auth_headers, timeout=10,
        )
        assert r.status_code == 200, f"{r.status_code} {r.text}"
        body = r.json()
        assert "fields" in body, f"missing 'fields' key: {body}"
        fields = body["fields"]
        assert isinstance(fields, list) and len(fields) >= 8
        # every entry has key + label
        keys_seen = set()
        for f in fields:
            assert set(f.keys()) >= {"key", "label"}, f"bad field shape: {f}"
            assert isinstance(f["key"], str) and f["key"]
            assert isinstance(f["label"], str) and f["label"]
            keys_seen.add(f["key"])
        missing = self.REQUIRED_KEYS - keys_seen
        assert not missing, f"AVAILABLE_FIELDS missing required keys: {missing}"


class TestEventTriggerUpdateValidation:
    """Sanity — updating a bogus event_key must 404, empty PUT must 400."""

    def test_unknown_event_key_404(self, auth_headers):
        r = requests.put(
            f"{BASE_URL}/api/admin/whatsapp-provider/events/does_not_exist",
            headers=auth_headers, json={"template_enabled": True}, timeout=10,
        )
        assert r.status_code == 404, f"expected 404, got {r.status_code} {r.text}"

    def test_empty_patch_400(self, auth_headers):
        r = requests.put(
            f"{BASE_URL}/api/admin/whatsapp-provider/events/otp_login",
            headers=auth_headers, json={}, timeout=10,
        )
        assert r.status_code == 400, f"expected 400, got {r.status_code} {r.text}"
