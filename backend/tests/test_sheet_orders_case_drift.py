"""Iteration 33 — Defence-in-depth verification for the LIVE
`/api/sheets/orders` handler in `/app/backend/routers/sheets.py`.

Scenario reproduced:
  * The admin user's Google Sheet uses TITLE-CASE headers
    ("Order Id", "Name", "Phone", "Tracking Id", …).
  * We temporarily corrupt the stored `settings.sheet.column_mapping`
    to UPPERCASE variants ("ORDER ID", "NAME", "PHONE", "TRACKING ID").
  * With the iter-33 hardening ported into the LIVE router
    (`mapped_field` fast/slow path + `mapping_lc` + `_row_lc`),
    `GET /api/sheets/orders` MUST STILL return shipped >= 200
    (approximately unchanged from the correctly-cased baseline).
  * Before iter-33 hardening, shipped would collapse to 0.

The test always restores the mapping back to the migrated title-case
values in a finally-block (whether it passes or fails).
"""

from __future__ import annotations

import copy
import os
import time

import pytest
import requests

# We reach into MongoDB directly to mutate + restore the mapping.
import asyncio
import sys

sys.path.insert(0, "/app/backend")

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

BASE_URL = (
    os.environ.get("EXPO_PUBLIC_BACKEND_URL")
    or os.environ.get("EXPO_BACKEND_URL")
    or ""
).rstrip("/")

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASSWORD = "Admin@12345"

# ── canonical (post-migration) mapping values we MUST end with ────
CANONICAL_MAPPING = {
    "order_id":       "Order Id",
    "customer_name":  "Name",
    "phone":          "Phone",
    "tracking_id":    "Tracking Id",
    "master_order_id": "Master Order Id",
}

# ── drifted (uppercase) mapping values used for the reproduction ──
DRIFTED_MAPPING = {
    "order_id":       "ORDER ID",
    "customer_name":  "NAME",
    "phone":          "PHONE",
    "tracking_id":    "TRACKING ID",
}


MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://localhost:27017"
DB_NAME = os.environ.get("DB_NAME") or "test_database"


# ── helpers ───────────────────────────────────────────────────────

def _login() -> str:
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=60,
    )
    assert r.status_code == 200, r.text
    tok = r.json().get("token")
    assert tok
    return tok


def _api(token: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })
    return s


async def _get_admin_settings() -> dict:
    client = AsyncIOMotorClient(MONGO_URL)
    try:
        db = client[DB_NAME]
        # find the admin user id
        u = await db.users.find_one({"email": ADMIN_EMAIL}, {"_id": 0, "id": 1})
        assert u, "admin user not found"
        uid = u["id"]
        doc = await db.settings.find_one({"user_id": uid}, {"_id": 0})
        assert doc, "admin settings not found"
        return {"user_id": uid, "settings": doc}
    finally:
        client.close()


async def _set_column_mapping(user_id: str, new_mapping: dict) -> None:
    """Merge `new_mapping` into settings.sheet.column_mapping."""
    client = AsyncIOMotorClient(MONGO_URL)
    try:
        db = client[DB_NAME]
        # Build a $set per-key so we don't blow away other mapping entries.
        set_ops = {
            f"sheet.column_mapping.{k}": v for k, v in new_mapping.items()
        }
        await db.settings.update_one({"user_id": user_id}, {"$set": set_ops})
    finally:
        client.close()


def _shipped_split(orders_body: dict) -> tuple[int, int, int]:
    total = orders_body.get("total") or 0
    orders = orders_body.get("orders") or []
    shipped = sum(1 for o in orders if o.get("already_shipped"))
    not_shipped = total - shipped
    return total, shipped, not_shipped


# ── the two live checks ──────────────────────────────────────────

@pytest.fixture(scope="module")
def api():
    return _api(_login())


@pytest.fixture(scope="module")
def admin_ctx():
    return asyncio.run(_get_admin_settings())


class TestT1CaseDriftReproduction:
    """Corrupt the mapping to UPPERCASE, then verify the LIVE handler
    still de-duplicates correctly (defence-in-depth via mapping_lc)."""

    def test_case_drift_still_dedupes(self, api, admin_ctx):
        uid = admin_ctx["user_id"]
        original_mapping = copy.deepcopy(
            (admin_ctx["settings"].get("sheet") or {}).get("column_mapping") or {}
        )
        # Grab a canonical baseline first so we know what the shipped
        # count SHOULD be with correct casing.
        r0 = api.get(f"{BASE_URL}/api/sheets/orders", timeout=90)
        if r0.status_code in (400, 403):
            pytest.skip(f"sheet unreachable in this env: {r0.status_code}")
        assert r0.status_code == 200, r0.text
        t0, s0, ns0 = _shipped_split(r0.json())
        print(f"\n[baseline canonical] total={t0} shipped={s0} not_shipped={ns0}")

        try:
            # ── drift ────────────────────────────────────────────
            asyncio.run(_set_column_mapping(uid, DRIFTED_MAPPING))

            # Bust the settings cache via cache-buster query.
            time.sleep(1)
            r1 = api.get(
                f"{BASE_URL}/api/sheets/orders?_cb={int(time.time())}",
                timeout=90,
            )
            assert r1.status_code == 200, r1.text
            t1, s1, ns1 = _shipped_split(r1.json())
            print(f"[drifted uppercase]  total={t1} shipped={s1} not_shipped={ns1}")

            # Total row-count must be unchanged (still reading the
            # same physical sheet).
            assert t1 == t0, f"row count changed under drift {t0} → {t1}"

            # The defence-in-depth guarantee.
            assert s1 >= 200, (
                f"REGRESSION: with drifted uppercase mapping shipped={s1} "
                f"(expected >= 200; canonical baseline was {s0}). "
                "Case-insensitive fallback in mapped_field is not effective."
            )

            # Sanity: drifted vs canonical shipped counts should be
            # within a small margin (the fallback should recover ~all
            # dedupe hits — allow generous 20 slack for background
            # sheet syncs during the test).
            assert abs(s1 - s0) <= 20, (
                f"drifted shipped count drifted too far from baseline: "
                f"canonical={s0}, drifted={s1}"
            )
        finally:
            # ── restore ──────────────────────────────────────────
            asyncio.run(_set_column_mapping(uid, CANONICAL_MAPPING))
            # Also restore any additional keys that may have been
            # present in the pre-test mapping but not in CANONICAL.
            extras = {
                k: v for k, v in original_mapping.items()
                if k not in CANONICAL_MAPPING
            }
            if extras:
                asyncio.run(_set_column_mapping(uid, extras))


class TestT2RegressionCanonicalMapping:
    """After restoration the handler must still return the migrated
    baseline (~ shipped 311, not_shipped 18 on the admin's 329-row
    sheet)."""

    def test_canonical_mapping_still_ok(self, api):
        r = api.get(
            f"{BASE_URL}/api/sheets/orders?_cb={int(time.time())}",
            timeout=90,
        )
        if r.status_code in (400, 403):
            pytest.skip(f"sheet unreachable: {r.status_code}")
        assert r.status_code == 200, r.text
        total, shipped, not_shipped = _shipped_split(r.json())
        print(f"\n[canonical restore] total={total} shipped={shipped} not_shipped={not_shipped}")
        assert total >= 100, f"unexpectedly small sheet: total={total}"
        assert shipped >= total * 0.9, (
            f"canonical mapping regressed: shipped={shipped}/{total}"
        )


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(pytest.main([__file__, "-v", "-s"]))
