"""
Iteration 35 — WhatsApp Provider "Out for Delivery" event tests.

Verifies:
  T1 — Event catalogue exposes stage_out_for_delivery in correct position.
  T2 — STAGE_TO_EVENT_KEY mapping includes Out for Delivery and preserves prior keys.
  T3 — Seeded event trigger row exists in Mongo with correct default fields.
  T4 — Regression: all 8 pre-existing events still present.
"""
from __future__ import annotations

import os
import sys
import asyncio
import pytest
import requests

# Ensure backend importable for direct-import tests.
BACKEND_DIR = "/app/backend"
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    raise RuntimeError("EXPO_PUBLIC_BACKEND_URL not set")

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASSWORD = "Admin@12345"

PRE_EXISTING_KEYS = [
    "otp_login",
    "otp_signup",
    "stage_pending",
    "stage_processing",
    "stage_ready_to_ship",
    "stage_shipped",
    "stage_delivered",
    "stage_feedback",
]

NEW_EVENT_KEY = "stage_out_for_delivery"
EXPECTED_DEFAULT_FIELDS = [
    "customer_name",
    "customer_phone",
    "order_id",
    "tracking_id",
    "courier_name",
    "business_name",
]


@pytest.fixture(scope="module")
def admin_token() -> str:
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=15,
    )
    assert r.status_code == 200, f"Admin login failed: {r.status_code} {r.text}"
    body = r.json()
    tok = body.get("token")
    assert tok, f"No token in login response: {body}"
    return tok


@pytest.fixture(scope="module")
def auth_headers(admin_token: str) -> dict:
    return {"Authorization": f"Bearer {admin_token}"}


# ─── T1 — Event catalogue exposure ────────────────────────────────────
class TestEventCatalogueExposure:
    def test_list_events_contains_out_for_delivery_between_shipped_and_delivered(
        self, auth_headers: dict
    ) -> None:
        r = requests.get(
            f"{BASE_URL}/api/admin/whatsapp-provider/events",
            headers=auth_headers,
            timeout=15,
        )
        assert r.status_code == 200, f"list events failed: {r.status_code} {r.text}"
        data = r.json()
        assert "items" in data, f"missing items key: {data}"
        items = data["items"]
        keys = [it.get("event_key") for it in items]

        assert NEW_EVENT_KEY in keys, f"stage_out_for_delivery missing from {keys}"

        target = next(it for it in items if it["event_key"] == NEW_EVENT_KEY)
        assert target["category"] == "stage", f"wrong category: {target}"
        assert target["label"] == "Stage: Out for Delivery", (
            f"wrong label: {target['label']!r}"
        )

        idx_shipped = keys.index("stage_shipped")
        idx_ofd = keys.index(NEW_EVENT_KEY)
        idx_delivered = keys.index("stage_delivered")
        assert idx_shipped < idx_ofd < idx_delivered, (
            f"ordering wrong: shipped={idx_shipped} "
            f"ofd={idx_ofd} delivered={idx_delivered} keys={keys}"
        )

    def test_default_fields_match_spec(self, auth_headers: dict) -> None:
        r = requests.get(
            f"{BASE_URL}/api/admin/whatsapp-provider/events/{NEW_EVENT_KEY}",
            headers=auth_headers,
            timeout=15,
        )
        assert r.status_code == 200, f"get event failed: {r.status_code} {r.text}"
        item = r.json().get("item") or {}
        assert item.get("event_key") == NEW_EVENT_KEY
        selected = list(item.get("selected_fields") or [])
        # Allow order to match spec; assert as list equal.
        assert selected == EXPECTED_DEFAULT_FIELDS, (
            f"selected_fields mismatch: got={selected} "
            f"expected={EXPECTED_DEFAULT_FIELDS}"
        )
        assert item.get("category") == "stage"
        assert item.get("label") == "Stage: Out for Delivery"


# ─── T2 — STAGE_TO_EVENT_KEY mapping (direct import) ──────────────────
class TestStageMapping:
    def test_out_for_delivery_mapped(self) -> None:
        from routers.whatsapp_provider import STAGE_TO_EVENT_KEY

        assert STAGE_TO_EVENT_KEY["Out for Delivery"] == "stage_out_for_delivery"

    def test_prior_status_keys_preserved(self) -> None:
        from routers.whatsapp_provider import STAGE_TO_EVENT_KEY

        expected = {
            "Pending": "stage_pending",
            "Processing": "stage_processing",
            "Ready to Ship": "stage_ready_to_ship",
            "Shipped": "stage_shipped",
            "Delivered": "stage_delivered",
            "Feedback": "stage_feedback",
        }
        for stage, key in expected.items():
            assert STAGE_TO_EVENT_KEY.get(stage) == key, (
                f"regression: STAGE_TO_EVENT_KEY[{stage!r}] "
                f"= {STAGE_TO_EVENT_KEY.get(stage)!r}, want {key!r}"
            )


# ─── T3 — Seeded event trigger row in Mongo ───────────────────────────
class TestSeededMongoRow:
    def test_seed_row_present_with_correct_fields(self) -> None:
        # Force fresh dotenv load — pytest runs outside supervisor.
        from dotenv import load_dotenv

        load_dotenv("/app/backend/.env")
        mongo_url = os.environ.get("MONGO_URL")
        db_name = os.environ.get("DB_NAME")
        assert mongo_url and db_name, "MONGO_URL / DB_NAME missing"

        from motor.motor_asyncio import AsyncIOMotorClient

        async def _fetch() -> dict:
            client = AsyncIOMotorClient(mongo_url)
            db = client[db_name]
            doc = await db["whatsapp_event_triggers"].find_one(
                {"event_key": NEW_EVENT_KEY}
            )
            client.close()
            return doc

        doc = asyncio.get_event_loop().run_until_complete(_fetch())
        assert doc is not None, (
            "no whatsapp_event_triggers row seeded for stage_out_for_delivery"
        )
        assert doc.get("event_key") == NEW_EVENT_KEY
        assert doc.get("category") == "stage"
        assert doc.get("label") == "Stage: Out for Delivery"
        selected = list(doc.get("selected_fields") or [])
        assert selected == EXPECTED_DEFAULT_FIELDS, (
            f"seeded selected_fields mismatch: {selected}"
        )


# ─── T4 — Regression: all 8 pre-existing events present ──────────────
class TestExistingEventsRegression:
    def test_all_prior_events_still_present_in_catalog(self) -> None:
        from routers.whatsapp_provider import EVENT_CATALOG

        keys = [c["event_key"] for c in EVENT_CATALOG]
        for k in PRE_EXISTING_KEYS:
            assert k in keys, f"regression: {k} missing from EVENT_CATALOG"
        # And new event exists.
        assert NEW_EVENT_KEY in keys

    def test_all_prior_events_returned_by_api(self, auth_headers: dict) -> None:
        r = requests.get(
            f"{BASE_URL}/api/admin/whatsapp-provider/events",
            headers=auth_headers,
            timeout=15,
        )
        assert r.status_code == 200
        keys = [it.get("event_key") for it in r.json().get("items", [])]
        for k in PRE_EXISTING_KEYS:
            assert k in keys, f"regression: {k} missing from /events response"
