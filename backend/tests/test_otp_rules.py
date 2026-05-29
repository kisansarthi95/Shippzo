"""
Test the 2026-05-29 OTP authentication rule update:

  • OTP Expiry Time:                    10 Minutes  (= 600s)
  • OTP Resend Cooldown:                60 Seconds
  • Maximum OTP Resend Attempts:        5
  • Lockout Duration After Max:         30 Minutes  (= 1800s)

This is a hermetic test — uses a throw-away phone number, talks to the
local HTTP API directly, and cleans up its own rows after.

Run:  python -m backend.tests.test_otp_rules
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from typing import Any

import httpx

# Add backend to sys.path so service imports resolve from this script.
_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(_HERE)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(_BACKEND, ".env"))

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from services import otp_service  # noqa: E402  # for constant introspection

BASE_URL = os.getenv("TEST_BASE_URL", "http://localhost:8001")
TEST_PHONE = os.getenv("TEST_PHONE", "+918111100099")  # not real / not seeded


def banner(title: str) -> None:
    print("\n" + "─" * 64)
    print(f"  {title}")
    print("─" * 64)


async def reset_phone(db: Any, phone: str) -> None:
    """Delete every OTP row + lockout counter for the test phone so each
    run starts from a clean slate. Idempotent."""
    await db[otp_service._COLLECTION].delete_many({"phone": phone})
    await db[otp_service._LOCK_COLLECTION].delete_many({"phone": phone})


async def main() -> int:
    failures = 0
    mongo_url = os.getenv("MONGO_URL")
    db_name = os.getenv("DB_NAME") or "test_database"
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]

    banner("Phase 0 — Configured constants")
    print(f"  OTP_TTL_SECONDS          = {otp_service.OTP_TTL_SECONDS}")
    print(f"  OTP_RESEND_COOLDOWN_S    = {otp_service.OTP_RESEND_COOLDOWN_S}")
    print(f"  OTP_MAX_RESEND_ATTEMPTS  = {otp_service.OTP_MAX_RESEND_ATTEMPTS}")
    print(f"  OTP_LOCKOUT_DURATION_S   = {otp_service.OTP_LOCKOUT_DURATION_S}")

    expected = {
        "OTP_TTL_SECONDS":          600,
        "OTP_RESEND_COOLDOWN_S":    60,
        "OTP_MAX_RESEND_ATTEMPTS":  5,
        "OTP_LOCKOUT_DURATION_S":   1800,
    }
    for k, v in expected.items():
        actual = getattr(otp_service, k)
        ok = actual == v
        print(f"   {'✓' if ok else '✗'} {k} == {v}  (got {actual})")
        if not ok:
            failures += 1

    await reset_phone(db, TEST_PHONE)

    # ── Phase 1 — TTL surfaces correctly on the HTTP response ──────
    banner("Phase 1 — /api/auth/otp/request reports 600-second TTL")
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30) as c:
        r = await c.post("/api/auth/otp/request", json={
            "phone": TEST_PHONE, "event_type": "login",
        })
        print(f"  HTTP {r.status_code}  → expires_in = {r.json().get('expires_in')}")
        ok = r.status_code == 200 and r.json().get("expires_in") == 600
        print(f"   {'✓' if ok else '✗'} expires_in == 600")
        if not ok:
            failures += 1

    # ── Phase 2 — cooldown blocks an immediate re-request ──────────
    banner("Phase 2 — 60-second cooldown between resends")
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30) as c:
        r = await c.post("/api/auth/otp/request", json={
            "phone": TEST_PHONE, "event_type": "login",
        })
        ok = r.status_code == 429 and "wait" in r.json().get("detail", "").lower()
        print(f"  HTTP {r.status_code}  detail={r.json().get('detail')}")
        print(f"   {'✓' if ok else '✗'} second request within cooldown is 429")
        if not ok:
            failures += 1

    # ── Phase 3 — bypass cooldown in the test (DB direct) to count
    # resends. Patch each row's last_resend_at to be > cooldown ago.
    banner("Phase 3 — 5 resends allowed, 6th locks out")
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30) as c:
        # First request already happened above (count=1). Fire 4 more
        # by rewinding the OTP row's last_resend_at each time so the
        # cooldown path always passes.
        for i in range(2, 6):  # requests 2,3,4,5
            # Rewind last_resend_at to 70s ago on the most recent row.
            await db[otp_service._COLLECTION].update_many(
                {"phone": TEST_PHONE},
                {"$set": {"last_resend_at": "1970-01-01T00:00:00+00:00"}},
            )
            r = await c.post("/api/auth/otp/request", json={
                "phone": TEST_PHONE, "event_type": "login",
            })
            print(f"  Resend #{i}: HTTP {r.status_code} "
                  f"expires_in={r.json().get('expires_in')}")
            ok = r.status_code == 200
            if not ok:
                failures += 1
        # 6th — should be locked.
        await db[otp_service._COLLECTION].update_many(
            {"phone": TEST_PHONE},
            {"$set": {"last_resend_at": "1970-01-01T00:00:00+00:00"}},
        )
        r = await c.post("/api/auth/otp/request", json={
            "phone": TEST_PHONE, "event_type": "login",
        })
        ok = r.status_code == 429 and "minute" in r.json().get("detail", "").lower()
        print(f"  Resend #6: HTTP {r.status_code} detail={r.json().get('detail')}")
        print(f"   {'✓' if ok else '✗'} 6th resend within window is 429 with minutes")
        if not ok:
            failures += 1

    # ── Phase 4 — lockout row state ────────────────────────────────
    banner("Phase 4 — lockout row written to otp_resend_locks")
    row = await db[otp_service._LOCK_COLLECTION].find_one(
        {"phone": TEST_PHONE, "event_type": "login"}
    )
    print(f"  row = {row}")
    has_lock = row and row.get("locked_until")
    print(f"   {'✓' if has_lock else '✗'} locked_until is set on the row")
    if not has_lock:
        failures += 1
    else:
        print(f"   ✓ Lockout in effect until {row.get('locked_until')}")
        # ~30 minutes from now (allow ±2 minute drift).
        from datetime import datetime, timezone
        lu = datetime.fromisoformat(row["locked_until"].replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta_min = (lu - now).total_seconds() / 60
        ok = 28 <= delta_min <= 31
        print(f"   {'✓' if ok else '✗'} lockout duration ≈ 30 min "
              f"(got {delta_min:.1f} min)")
        if not ok:
            failures += 1

    # ── Phase 5 — while locked, even after cooldown bypass we 429 ──
    banner("Phase 5 — locked → all subsequent requests 429")
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30) as c:
        await db[otp_service._COLLECTION].update_many(
            {"phone": TEST_PHONE},
            {"$set": {"last_resend_at": "1970-01-01T00:00:00+00:00"}},
        )
        r = await c.post("/api/auth/otp/request", json={
            "phone": TEST_PHONE, "event_type": "login",
        })
        ok = r.status_code == 429
        print(f"  HTTP {r.status_code}  detail={r.json().get('detail')}")
        print(f"   {'✓' if ok else '✗'} request during lock is 429")
        if not ok:
            failures += 1

    # ── Cleanup ────────────────────────────────────────────────────
    await reset_phone(db, TEST_PHONE)
    client.close()

    banner("Result")
    if failures == 0:
        print("  ✅ ALL OTP RULE CHECKS PASSED")
    else:
        print(f"  ❌ {failures} check(s) failed")
    return failures


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
