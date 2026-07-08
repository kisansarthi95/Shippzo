"""Phase F5.6 — Add Auto-Sync courier presets for Blue Dart, DTDC, and ShipRocket.

Extends the India Post-only prefill system to cover the 4 largest Indian
couriers. Also validates canonical slug resolution (partner_slug_for_name).

Backend surface under test:
  GET  /api/courier-sync/defaults?name=<courier>
  POST /api/couriers/{cid}/sync-test-parse
  courier_sync.generic_parser.partner_slug_for_name  (unit test)
"""
import os
import sys
import uuid
import pytest
import requests

# Allow importing the backend module for the partner_slug_for_name unit test.
sys.path.insert(0, "/app/backend")

BASE_URL = (
    os.environ.get("EXPO_PUBLIC_BACKEND_URL")
    or os.environ.get("EXPO_BACKEND_URL")
    or ""
).rstrip("/")

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASS = "Admin@12345"


# ---------------------------------------------------------------- fixtures
@pytest.fixture(scope="module")
def token():
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASS},
        timeout=30,
    )
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def api(token):
    s = requests.Session()
    s.headers.update({
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })
    return s


def _make_courier(api, name: str) -> str:
    """Create a test courier and return its id. Cleaned up in fixture."""
    payload = {
        "name":           f"TEST_{name}_{uuid.uuid4().hex[:6]}",
        "series_prefix":  "TST",
        "number_padding": 4,
        "next_number":    1,
    }
    r = api.post(f"{BASE_URL}/api/couriers", json=payload, timeout=30)
    assert r.status_code == 200, r.text
    return r.json()["id"]


@pytest.fixture(scope="module")
def created_couriers():
    return {"ids": []}


@pytest.fixture(scope="module", autouse=True)
def cleanup(api, created_couriers):
    yield
    for cid in created_couriers["ids"]:
        try:
            api.delete(f"{BASE_URL}/api/couriers/{cid}", timeout=15)
        except Exception:  # noqa: BLE001
            pass


# =========================================================================
# 1. GET /api/courier-sync/defaults — new presets
# =========================================================================
class TestDefaultsBlueDart:
    def test_blue_dart_returns_matched_true_with_expected_config(self, api):
        r = api.get(
            f"{BASE_URL}/api/courier-sync/defaults",
            params={"name": "Blue Dart"}, timeout=30,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("matched") is True
        cfg = data.get("config")
        assert cfg is not None
        senders = cfg.get("auto_sync_sender_patterns") or []
        assert "BLUDRT" in senders, senders
        assert "BLUEDART" in senders, senders
        assert cfg.get("auto_sync_tracking_regex") == r"\b(\d{11})\b"
        rules = cfg.get("auto_sync_status_rules") or []
        # ~13 rules (common set). Allow ±2 buffer.
        assert 11 <= len(rules) <= 15, f"expected ~13 rules, got {len(rules)}"


class TestDefaultsDTDC:
    def test_dtdc_returns_matched_true_with_dtdc_senders(self, api):
        r = api.get(
            f"{BASE_URL}/api/courier-sync/defaults",
            params={"name": "DTDC"}, timeout=30,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("matched") is True
        cfg = data["config"]
        senders = cfg.get("auto_sync_sender_patterns") or []
        # At least one DTDC-family sender must appear.
        assert any(s.startswith("DTDC") for s in senders), senders
        assert cfg.get("auto_sync_tracking_regex")
        assert len(cfg.get("auto_sync_status_rules") or []) >= 10


class TestDefaultsShipRocket:
    def test_shiprocket_returns_matched_true_with_shprkt_family(self, api):
        r = api.get(
            f"{BASE_URL}/api/courier-sync/defaults",
            params={"name": "Shiprocket"}, timeout=30,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("matched") is True
        cfg = data["config"]
        senders = cfg.get("auto_sync_sender_patterns") or []
        assert any(s.startswith("SHPR") or s.startswith("SHIPR") or s.startswith("SHPROK")
                   or s.startswith("SHROC") for s in senders), senders


class TestDefaultsIndiaPostRegression:
    def test_india_post_still_returns_original_config(self, api):
        r = api.get(
            f"{BASE_URL}/api/courier-sync/defaults",
            params={"name": "India Post"}, timeout=30,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("matched") is True
        cfg = data["config"]
        senders = cfg.get("auto_sync_sender_patterns") or []
        assert "INPOST" in senders
        # India Post tracking regex should still contain 'IN' suffix pattern.
        assert "IN" in (cfg.get("auto_sync_tracking_regex") or "")


class TestDefaultsUnknown:
    def test_unknown_courier_returns_matched_false(self, api):
        r = api.get(
            f"{BASE_URL}/api/courier-sync/defaults",
            params={"name": "Foo Bar"}, timeout=30,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("matched") is False
        assert data.get("config") in (None, {}, False)


class TestDefaultsCaseInsensitive:
    @pytest.mark.parametrize("name", ["bluedart", "blue dart", "BLUE DART", "BlUe DaRt"])
    def test_case_insensitive_variants_all_match(self, api, name):
        r = api.get(
            f"{BASE_URL}/api/courier-sync/defaults",
            params={"name": name}, timeout=30,
        )
        assert r.status_code == 200, r.text
        assert r.json().get("matched") is True, name


class TestDefaultsAlias:
    def test_ship_rocket_alias_resolves_to_shiprocket(self, api):
        r = api.get(
            f"{BASE_URL}/api/courier-sync/defaults",
            params={"name": "Ship Rocket"}, timeout=30,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("matched") is True
        senders = (data.get("config") or {}).get("auto_sync_sender_patterns") or []
        assert any("SH" in s for s in senders), senders


class TestDefaultsAuth:
    def test_defaults_requires_bearer_token(self):
        r = requests.get(
            f"{BASE_URL}/api/courier-sync/defaults",
            params={"name": "Blue Dart"}, timeout=30,
        )
        # 401 (missing bearer) or 403 (auth dep) — both = protected.
        assert r.status_code in (401, 403), r.status_code


# =========================================================================
# 2. POST /api/couriers/{cid}/sync-test-parse — end-to-end parser
# =========================================================================
def _apply_defaults(api, cid: str, courier_display_name: str) -> dict:
    """Fetch defaults for `courier_display_name` and PUT them onto courier `cid`."""
    r = api.get(
        f"{BASE_URL}/api/courier-sync/defaults",
        params={"name": courier_display_name}, timeout=30,
    )
    assert r.status_code == 200, r.text
    cfg = r.json()["config"]
    assert cfg, f"no defaults for {courier_display_name}"
    put = api.put(f"{BASE_URL}/api/couriers/{cid}", json=cfg, timeout=30)
    assert put.status_code == 200, put.text
    return cfg


class TestParseBlueDart:
    def test_blue_dart_delivered_sms_parses_correctly(self, api, created_couriers):
        cid = _make_courier(api, "BlueDart")
        created_couriers["ids"].append(cid)
        _apply_defaults(api, cid, "Blue Dart")

        payload = {
            "sender": "VM-BLUDRT",
            "title":  "",
            "text":   "Your shipment 12345678901 has been delivered. Thanks - Blue Dart",
        }
        r = api.post(
            f"{BASE_URL}/api/couriers/{cid}/sync-test-parse",
            json=payload, timeout=30,
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("matched") is True, d
        assert d.get("canonical_status") == "Delivered"
        assert d.get("shipment_status") == "Delivered"
        assert d.get("whitelisted") is True
        assert "12345678901" in (d.get("tracking_ids") or [])


class TestParseDTDC:
    def test_dtdc_awb_letter_plus_digits_extracts(self, api, created_couriers):
        cid = _make_courier(api, "DTDC")
        created_couriers["ids"].append(cid)
        _apply_defaults(api, cid, "DTDC")

        payload = {
            "sender": "VM-DTDCTX",
            "title":  "",
            "text":   "Dear customer, your DTDC parcel X12345678 has been delivered.",
        }
        r = api.post(
            f"{BASE_URL}/api/couriers/{cid}/sync-test-parse",
            json=payload, timeout=30,
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("matched") is True, d
        # AWB should extract — comparison is uppercase (parser upper-cases haystack).
        assert any("X12345678" in awb.upper() for awb in (d.get("tracking_ids") or [])), d
        assert d.get("canonical_status") == "Delivered"


class TestParseShipRocket:
    def test_shiprocket_10_digit_awb_extracts(self, api, created_couriers):
        cid = _make_courier(api, "ShipRocket")
        created_couriers["ids"].append(cid)
        _apply_defaults(api, cid, "Shiprocket")

        payload = {
            "sender": "VM-SHPRKT",
            "title":  "",
            "text":   "Shiprocket update: AWB 1234567890 has been delivered.",
        }
        r = api.post(
            f"{BASE_URL}/api/couriers/{cid}/sync-test-parse",
            json=payload, timeout=30,
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("matched") is True, d
        assert "1234567890" in (d.get("tracking_ids") or []), d
        assert d.get("canonical_status") == "Delivered"


class TestParseIndiaPostRegression:
    def test_india_post_still_parses(self, api, created_couriers):
        cid = _make_courier(api, "IndiaPost")
        created_couriers["ids"].append(cid)
        _apply_defaults(api, cid, "India Post")

        payload = {
            "sender": "VM-INPOST",
            "title":  "",
            "text":   "Consignment EM123456789IN has been delivered on 2026-01-05.",
        }
        r = api.post(
            f"{BASE_URL}/api/couriers/{cid}/sync-test-parse",
            json=payload, timeout=30,
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("matched") is True, d
        assert "EM123456789IN" in (d.get("tracking_ids") or []), d
        assert d.get("canonical_status") == "Delivered"


# =========================================================================
# 3. partner_slug_for_name — unit test (Python import)
# =========================================================================
class TestPartnerSlugForName:
    def test_blue_dart_variants_resolve(self):
        from courier_sync.generic_parser import partner_slug_for_name
        assert partner_slug_for_name("Blue Dart Express") == "blue_dart"
        assert partner_slug_for_name("BLUEDART") == "blue_dart"
        assert partner_slug_for_name("Blue-Dart") == "blue_dart"

    def test_dtdc_variants_resolve(self):
        from courier_sync.generic_parser import partner_slug_for_name
        assert partner_slug_for_name("DTDC Plus") == "dtdc"
        assert partner_slug_for_name("DTDC Express") == "dtdc"

    def test_shiprocket_variants_resolve(self):
        from courier_sync.generic_parser import partner_slug_for_name
        assert partner_slug_for_name("Shiprocket X") == "shiprocket"
        assert partner_slug_for_name("Ship Rocket") == "shiprocket"

    def test_india_post_still_resolves(self):
        from courier_sync.generic_parser import partner_slug_for_name
        assert partner_slug_for_name("India Post") == "india_post"
        assert partner_slug_for_name("Indian Post") == "india_post"

    def test_unknown_falls_back_to_snake_case(self):
        from courier_sync.generic_parser import partner_slug_for_name
        assert partner_slug_for_name("Foo Bar") == "foo_bar"
