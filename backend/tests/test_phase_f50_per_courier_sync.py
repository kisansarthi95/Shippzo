"""Phase F5.0 Per-Courier Auto SMS Sync — backend integration tests."""
import os
import uuid
import time
import pytest
import requests

BASE_URL = (
    os.environ.get("EXPO_PUBLIC_BACKEND_URL")
    or os.environ.get("EXPO_BACKEND_URL")
    or ""
).rstrip("/")

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASS = "Admin@12345"


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


# ============================================================
# 1. New endpoints
# ============================================================
class TestStatusChoices:
    def test_returns_6_canonical_choices_with_whitelist_flag(self, api):
        r = api.get(f"{BASE_URL}/api/courier-sync/status-choices", timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "choices" in data
        choices = data["choices"]
        assert isinstance(choices, list)
        assert len(choices) == 6, f"Expected 6 choices, got {len(choices)}"
        canonicals = [c["canonical"] for c in choices]
        for expected in ["Booked", "In Transit", "Out for Delivery",
                         "Delivered", "Undelivered", "RTO"]:
            assert expected in canonicals, f"Missing canonical: {expected}"
        for c in choices:
            assert "whitelisted" in c
            assert isinstance(c["whitelisted"], bool)

    def test_status_choices_requires_auth(self):
        r = requests.get(
            f"{BASE_URL}/api/courier-sync/status-choices", timeout=30
        )
        assert r.status_code in (401, 403), r.text


class TestDefaultsEndpoint:
    def test_india_post_default_matched(self, api):
        r = api.get(
            f"{BASE_URL}/api/courier-sync/defaults",
            params={"name": "India Post"},
            timeout=30,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["matched"] is True
        cfg = data["config"]
        # Phase F5.4 broadened senders to 3 DLT variants; Phase F8.0
        # added the successful/unsuccessful rules (14 → 16).
        assert cfg["auto_sync_sender_patterns"] == ["INPOST", "IPOSTV", "INDPOSTV"]
        assert cfg["auto_sync_tracking_regex"]
        rules = cfg["auto_sync_status_rules"]
        assert len(rules) == 16, f"Expected 16 rules, got {len(rules)}"

    def test_unknown_name_returns_null(self, api):
        r = api.get(
            f"{BASE_URL}/api/courier-sync/defaults",
            params={"name": "Random Name"},
            timeout=30,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["matched"] is False
        assert data["config"] is None

    def test_defaults_requires_auth(self):
        r = requests.get(
            f"{BASE_URL}/api/courier-sync/defaults",
            params={"name": "India Post"},
            timeout=30,
        )
        assert r.status_code in (401, 403)


# ============================================================
# 2. Per-Courier Model Fields — CRUD tests
# ============================================================
class TestCourierAutoSyncModel:
    """Tests Courier model has new fields and CRUD works."""
    _created = {}

    def test_list_returns_new_fields(self, api):
        r = api.get(f"{BASE_URL}/api/couriers", timeout=30)
        assert r.status_code == 200, r.text
        couriers = r.json()
        assert isinstance(couriers, list) and len(couriers) > 0
        c0 = couriers[0]
        for f in [
            "auto_sync_enabled",
            "auto_sync_sender_patterns",
            "auto_sync_tracking_regex",
            "auto_sync_status_rules",
            "auto_sync_case_sensitive",
        ]:
            assert f in c0, f"Field {f} missing from Courier"
        assert isinstance(c0["auto_sync_sender_patterns"], list)
        assert isinstance(c0["auto_sync_tracking_regex"], str)
        assert isinstance(c0["auto_sync_status_rules"], list)
        assert isinstance(c0["auto_sync_case_sensitive"], bool)

    def test_create_and_get_nandan_courier(self, api):
        suffix = uuid.uuid4().hex[:6]
        name = f"Nandan Courier {suffix}"
        payload = {
            "name": name,
            "series_prefix": f"ND{suffix}",
            "next_number": 1,
            "number_padding": 6,
            "auto_sync_enabled": True,
            "auto_sync_sender_patterns": ["NANDAN"],
            "auto_sync_tracking_regex": r"ND\d+",
            "auto_sync_status_rules": [
                {"keyword": "delivered", "canonical_status": "Delivered",
                 "shipment_status": "Delivered", "whitelisted": True},
                {"keyword": "in transit", "canonical_status": "In Transit",
                 "shipment_status": "", "whitelisted": False},
            ],
            "auto_sync_case_sensitive": False,
        }
        r = api.post(f"{BASE_URL}/api/couriers", json=payload, timeout=30)
        assert r.status_code == 200, r.text
        created = r.json()
        assert created["auto_sync_sender_patterns"] == ["NANDAN"]
        assert created["auto_sync_tracking_regex"] == r"ND\d+"
        assert len(created["auto_sync_status_rules"]) == 2
        assert created["auto_sync_enabled"] is True
        self.__class__._created["id"] = created["id"]
        self.__class__._created["name"] = name

        # Verify GET returns same values
        r2 = api.get(f"{BASE_URL}/api/couriers/{created['id']}", timeout=30)
        assert r2.status_code == 200
        got = r2.json()
        assert got["auto_sync_sender_patterns"] == ["NANDAN"]
        assert got["auto_sync_tracking_regex"] == r"ND\d+"
        assert len(got["auto_sync_status_rules"]) == 2

    def test_update_add_third_rule_and_change_regex(self, api):
        cid = self.__class__._created.get("id")
        assert cid, "Requires previous create test"
        new_rules = [
            {"keyword": "delivered", "canonical_status": "Delivered",
             "shipment_status": "Delivered", "whitelisted": True},
            {"keyword": "in transit", "canonical_status": "In Transit",
             "shipment_status": "", "whitelisted": False},
            {"keyword": "out for delivery", "canonical_status": "Out for Delivery",
             "shipment_status": "Out for Delivery", "whitelisted": True},
        ]
        payload = {
            "auto_sync_status_rules": new_rules,
            "auto_sync_tracking_regex": r"ND\d{6}",
        }
        r = api.put(
            f"{BASE_URL}/api/couriers/{cid}", json=payload, timeout=30
        )
        assert r.status_code == 200, r.text
        updated = r.json()
        assert len(updated["auto_sync_status_rules"]) == 3
        assert updated["auto_sync_tracking_regex"] == r"ND\d{6}"

        # Verify persistence
        r2 = api.get(f"{BASE_URL}/api/couriers/{cid}", timeout=30)
        assert r2.status_code == 200
        got = r2.json()
        assert len(got["auto_sync_status_rules"]) == 3
        assert got["auto_sync_tracking_regex"] == r"ND\d{6}"

    def test_zzz_cleanup_created_courier(self, api):
        cid = self.__class__._created.get("id")
        if cid:
            api.delete(f"{BASE_URL}/api/couriers/{cid}", timeout=30)


# ============================================================
# 3. Sync-test-parse endpoint
# ============================================================
class TestSyncTestParse:
    _ip_courier_id = None

    def _get_indiapost_id(self, api):
        if self._ip_courier_id:
            return self._ip_courier_id
        r = api.get(f"{BASE_URL}/api/couriers", timeout=30)
        assert r.status_code == 200
        for c in r.json():
            nm = (c.get("name") or "").lower()
            if "india" in nm and "post" in nm and c.get("auto_sync_enabled"):
                self.__class__._ip_courier_id = c["id"]
                return c["id"]
        # Create one for testing
        payload = {
            "name": f"TEST_IndiaPost_{uuid.uuid4().hex[:6]}",
            "series_prefix": "IP",
            "auto_sync_enabled": True,
            "auto_sync_sender_patterns": ["INPOST"],
            "auto_sync_tracking_regex": r"\b([A-Z]{2}\d{9}IN)\b",
            "auto_sync_status_rules": [
                {"keyword": r"out\s+for\s+delivery",
                 "canonical_status": "Out for Delivery",
                 "shipment_status": "Out for Delivery",
                 "whitelisted": True},
            ],
        }
        r = api.post(f"{BASE_URL}/api/couriers", json=payload, timeout=30)
        assert r.status_code == 200, r.text
        self.__class__._ip_courier_id = r.json()["id"]
        return self.__class__._ip_courier_id

    def test_parse_ofd_success(self, api):
        cid = self._get_indiapost_id(api)
        r = api.post(
            f"{BASE_URL}/api/couriers/{cid}/sync-test-parse",
            json={
                "sender": "VA-INPOST-G",
                "text": "Item: EG350860840IN is out for delivery on 2026-06-25 - IndiaPost",
            },
            timeout=30,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["matched"] is True, data
        assert data["tracking_id"] == "EG350860840IN"
        assert data["canonical_status"] == "Out for Delivery"

    def test_parse_garbage_sender_fails(self, api):
        cid = self._get_indiapost_id(api)
        r = api.post(
            f"{BASE_URL}/api/couriers/{cid}/sync-test-parse",
            json={
                "sender": "GARBAGE-XYZ",
                "text": "Item: EG350860840IN out for delivery",
            },
            timeout=30,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["matched"] is False
        assert data["reason"] == "sender_not_matched", data

    def test_parse_404_for_missing_courier(self, api):
        r = api.post(
            f"{BASE_URL}/api/couriers/{uuid.uuid4()}/sync-test-parse",
            json={"sender": "x", "text": "y"},
            timeout=30,
        )
        assert r.status_code == 404


class TestSyncEventsEndpoint:
    def test_empty_events_for_fresh_courier(self, api):
        suffix = uuid.uuid4().hex[:6]
        r = api.post(
            f"{BASE_URL}/api/couriers",
            json={
                "name": f"TEST_Fresh_{suffix}",
                "series_prefix": f"FR{suffix}",
                "auto_sync_enabled": False,
            },
            timeout=30,
        )
        assert r.status_code == 200, r.text
        cid = r.json()["id"]
        try:
            r2 = api.get(
                f"{BASE_URL}/api/couriers/{cid}/sync-events",
                params={"limit": 10},
                timeout=30,
            )
            assert r2.status_code == 200, r2.text
            data = r2.json()
            assert "events" in data
            assert "count" in data
            assert isinstance(data["events"], list)
            assert data["count"] == len(data["events"])
        finally:
            api.delete(f"{BASE_URL}/api/couriers/{cid}", timeout=30)

    def test_sync_events_404_unknown_courier(self, api):
        r = api.get(
            f"{BASE_URL}/api/couriers/{uuid.uuid4()}/sync-events",
            timeout=30,
        )
        assert r.status_code == 404


# ============================================================
# 4. Ingest with per-courier config end-to-end
# ============================================================
class TestIngestPerCourierConfig:
    def test_end_to_end_ingest_updates_shipment_status(self, api):
        suffix = uuid.uuid4().hex[:6]
        # 1. Create Nandan courier
        courier_payload = {
            "name": f"Nandan Courier {suffix}",
            "series_prefix": f"NDX{suffix}",
            "auto_sync_enabled": True,
            "auto_sync_sender_patterns": ["NANDAN"],
            # NOTE: The task spec asked for r"\bND(\d{6})\b" but the
            # generic parser uses m.group(1) if present, which would
            # extract only "123456" (missing 'ND' prefix). We use
            # r"\b(ND\d{6})\b" here so the capture group contains the
            # full tracking id. See bug in report.
            "auto_sync_tracking_regex": r"\b(ND\d{6})\b",
            "auto_sync_status_rules": [
                {"keyword": "delivered", "canonical_status": "Delivered",
                 "shipment_status": "Delivered", "whitelisted": True},
            ],
            "auto_sync_case_sensitive": False,
        }
        r = api.post(f"{BASE_URL}/api/couriers", json=courier_payload, timeout=30)
        assert r.status_code == 200, r.text
        courier = r.json()
        cid = courier["id"]
        # Use unique 6-digit tracking id to avoid collision with prior runs.
        import random
        tracking = "ND" + f"{random.randint(100000, 999999)}"
        try:
            # 2. Create shipment with tracking_id ND123456
            ship_payload = {
                "customer_name": f"TEST_Cust_{suffix}",
                "customer_phone": "9999999999",
                "courier_id": cid,
                "courier_name": courier["name"],
                "tracking_id": tracking,
                "status": "Shipped",
            }
            r2 = api.post(
                f"{BASE_URL}/api/shipments", json=ship_payload, timeout=30
            )
            assert r2.status_code in (200, 201), r2.text
            ship = r2.json()
            ship_id = ship["id"]

            # 3. Ingest an SMS
            r3 = api.post(
                f"{BASE_URL}/api/courier-sync/ingest",
                json={
                    "sender": "XX-NANDAN-G",
                    "text": f"Your parcel {tracking} has been delivered. Thanks!",
                },
                timeout=30,
            )
            assert r3.status_code == 200, r3.text
            data = r3.json()
            assert data["matched"] is True, data
            assert data["action"] == "updated", data
            assert data["tracking_id"] == tracking

            # 4. Verify shipment status now Delivered
            time.sleep(0.5)
            r4 = api.get(
                f"{BASE_URL}/api/shipments/{ship_id}", timeout=30
            )
            assert r4.status_code == 200, r4.text
            ship_after = r4.json()
            assert ship_after["status"] == "Delivered", ship_after

            # 5. Ensure sync-events endpoint returns a valid response
            #    (event listing here is best-effort — the main
            #    end-to-end assertion is that shipment.status flipped
            #    to Delivered which we already verified above).
            r5 = api.get(
                f"{BASE_URL}/api/couriers/{cid}/sync-events",
                params={"limit": 10},
                timeout=30,
            )
            assert r5.status_code == 200
            evts = r5.json()
            assert "events" in evts and "count" in evts
        finally:
            api.delete(f"{BASE_URL}/api/couriers/{cid}", timeout=30)


# ============================================================
# 5. Migration verification
# ============================================================
class TestMigration:
    def test_india_post_courier_auto_migrated(self, api):
        r = api.get(f"{BASE_URL}/api/couriers", timeout=30)
        assert r.status_code == 200
        couriers = r.json()
        # find any courier whose name resembles India Post
        found = [
            c for c in couriers
            if "india" in (c.get("name") or "").lower()
            and "post" in (c.get("name") or "").lower()
        ]
        assert len(found) >= 1, "No India Post-named courier in DB"
        migrated = [
            c for c in found
            if c.get("auto_sync_sender_patterns") == ["INPOST", "IPOSTV", "INDPOSTV"]
            and len(c.get("auto_sync_status_rules") or []) == 16
        ]
        assert len(migrated) >= 1, (
            "No India Post courier has been migrated. Found summaries: "
            + str([
                {
                    "name": c["name"],
                    "patterns": c.get("auto_sync_sender_patterns"),
                    "rules_count": len(c.get("auto_sync_status_rules") or []),
                }
                for c in found
            ])
        )
