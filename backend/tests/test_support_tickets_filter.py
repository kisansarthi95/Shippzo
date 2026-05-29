"""
Integration test — confirms the /api/support/tickets endpoint honours
the `status` query parameter exactly the way the redesigned
my-tickets.tsx screen needs it to.

The test:
  1. Signs up a brand-new throw-away user.
  2. Creates 4 support tickets and moves each into a different status
     (open, in_progress, resolved, closed) by writing the non-default
     statuses directly to Mongo. (The default status is `open`.)
  3. Hits GET /api/support/tickets five times — once per status filter
     plus once with no filter — and asserts the returned ticket set is
     exactly the expected one.
  4. Cleans up: drops the seeded tickets and the throw-away user.

Standalone-runnable (no pytest plugin dependency):

    python3 -m backend.tests.test_support_tickets_filter
    # OR (from /app/backend)
    python3 tests/test_support_tickets_filter.py

Exit code 0 = all 5 assertions passed. Non-zero = a regression.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from typing import Dict, List

import httpx
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient


load_dotenv("/app/backend/.env")

BASE_URL  = os.environ.get("BACKEND_BASE_URL", "http://localhost:8001")
MONGO_URL = os.environ["MONGO_URL"]
DB_NAME   = os.environ.get("DB_NAME", "test_database")

STATUSES = ("open", "in_progress", "resolved", "closed")


# ─── Helpers ────────────────────────────────────────────────────────────────
async def signup_throwaway_user(cli: httpx.AsyncClient) -> Dict[str, str]:
    """Create a unique test user and return {id, token, email}."""
    unique = uuid.uuid4().hex[:8]
    email  = f"qa-tickets-{unique}@example.com"
    pwd    = "qaTestP@ss1!"
    r = await cli.post("/api/auth/signup", json={
        "email":     email,
        "password":  pwd,
        "name":      "QA Tickets",
        "shop_name": "QA Tickets Shop",
        "phone":     f"+91999{unique[:7]}",
    })
    assert r.status_code in (200, 201), f"signup failed: {r.status_code} {r.text}"
    body  = r.json()
    # Backend returns a flat user record + a top-level `token` string.
    token = body.get("token") or body.get("access_token") or body.get("jwt")
    uid   = body.get("id") or (body.get("user") or {}).get("id") or body.get("user_id")
    assert token and uid, f"sign-up did not return token/user_id: {body}"
    return {"id": uid, "email": email, "token": token}


async def seed_four_tickets(
    cli: httpx.AsyncClient,
    db,
    user_token: str,
) -> Dict[str, str]:
    """Create one ticket per status and return {status -> ticket_id}."""
    headers  = {"Authorization": f"Bearer {user_token}"}
    created: Dict[str, str] = {}
    for st in STATUSES:
        r = await cli.post(
            "/api/support/tickets",
            json={
                "category":    "general",
                "title":       f"QA filter test — {st}",
                "description": f"Seeded for {st} filter assertion.",
            },
            headers=headers,
        )
        assert r.status_code in (200, 201), f"ticket create failed: {r.text}"
        created[st] = r.json()["id"]

    # Patch non-default statuses directly in Mongo to avoid going through
    # admin endpoints (this test stays a pure user-side check).
    for st, tid in created.items():
        if st == "open":
            continue
        await db.support_tickets.update_one({"id": tid}, {"$set": {"status": st}})
    return created


async def list_tickets(
    cli: httpx.AsyncClient,
    token: str,
    status: str | None,
) -> List[dict]:
    headers = {"Authorization": f"Bearer {token}"}
    params  = {"status": status} if status else None
    r = await cli.get("/api/support/tickets", headers=headers, params=params)
    assert r.status_code == 200, f"list failed (status={status}): {r.text}"
    return r.json().get("items", [])


# ─── Assertions ────────────────────────────────────────────────────────────
def assert_filter(items: List[dict], expected_id: str, expected_status: str,
                  forbidden_ids: List[str]) -> None:
    ids = {t["id"] for t in items}
    assert expected_id in ids, (
        f"filter={expected_status!r} did NOT return its own ticket {expected_id!r}"
    )
    for fid in forbidden_ids:
        assert fid not in ids, (
            f"filter={expected_status!r} LEAKED foreign ticket {fid!r}"
        )
    for t in items:
        # Every row returned by the filter must literally match the
        # requested status — no exceptions, no fall-throughs.
        assert t["status"] == expected_status, (
            f"filter={expected_status!r} returned ticket with status={t['status']!r}"
        )


# ─── Main test orchestrator ────────────────────────────────────────────────
async def run_all() -> None:
    client_db = AsyncIOMotorClient(MONGO_URL)
    db        = client_db[DB_NAME]
    test_user = None

    try:
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=30) as cli:
            test_user = await signup_throwaway_user(cli)
            print(f"  ✓ Throw-away user created: {test_user['email']}")

            seeded = await seed_four_tickets(cli, db, test_user["token"])
            print(f"  ✓ Seeded 4 tickets — {list(seeded.values())[:1]}… (+3 more)")

            # 1. open filter
            items = await list_tickets(cli, test_user["token"], "open")
            assert_filter(
                items, seeded["open"], "open",
                [seeded["in_progress"], seeded["resolved"], seeded["closed"]],
            )
            print(f"  ✓ open filter         — returned {len(items)} ticket(s), all open")

            # 2. in_progress filter
            items = await list_tickets(cli, test_user["token"], "in_progress")
            assert_filter(
                items, seeded["in_progress"], "in_progress",
                [seeded["open"], seeded["resolved"], seeded["closed"]],
            )
            print(f"  ✓ in_progress filter  — returned {len(items)} ticket(s), all in_progress")

            # 3. resolved filter
            items = await list_tickets(cli, test_user["token"], "resolved")
            assert_filter(
                items, seeded["resolved"], "resolved",
                [seeded["open"], seeded["in_progress"], seeded["closed"]],
            )
            print(f"  ✓ resolved filter     — returned {len(items)} ticket(s), all resolved")

            # 4. closed filter
            items = await list_tickets(cli, test_user["token"], "closed")
            assert_filter(
                items, seeded["closed"], "closed",
                [seeded["open"], seeded["in_progress"], seeded["resolved"]],
            )
            print(f"  ✓ closed filter       — returned {len(items)} ticket(s), all closed")

            # 5. no filter (== "all" in the tab map) must include every status
            items = await list_tickets(cli, test_user["token"], None)
            ids   = {t["id"] for t in items}
            for st in STATUSES:
                assert seeded[st] in ids, f"no-filter call missed the {st!r} ticket"
            print(f"  ✓ no filter (== all)  — returned {len(items)} ticket(s), covers every status")

    finally:
        if test_user:
            await db.support_tickets.delete_many({"user_id": test_user["id"]})
            await db.users.delete_one({"id": test_user["id"]})
            print(f"  ✓ Cleanup — purged seeded tickets + throw-away user")
        client_db.close()


def main() -> int:
    print("Running test_support_tickets_filter …")
    try:
        asyncio.run(run_all())
    except AssertionError as e:
        print(f"\n  ✗ FAIL: {e}")
        return 1
    except Exception as e:
        print(f"\n  ✗ ERROR: {e}")
        return 2
    print("\nALL 5 FILTER CASES PASSED ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
