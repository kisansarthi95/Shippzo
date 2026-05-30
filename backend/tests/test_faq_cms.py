"""
Backend integration test — FAQ CMS (Phase-27)

Verifies:
  1. Public  /api/faq                  → 200, returns only visible items.
  2. Non-admin /api/admin/faq          → 403 (or 401).
  3. Admin   /api/admin/faq            → 200, returns ALL items.
  4. Admin   POST  /api/admin/faq      → 200, creates new row.
  5. Admin   PATCH /api/admin/faq/{id} → 200, updates row.
  6. Public  /api/faq excludes hidden  → flipping `is_visible` removes
     the row from the public payload.
  7. Admin   DELETE /api/admin/faq/{id}→ 200, row gone from both lists.

Cleans up after itself even on partial failure (best-effort).
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid

import httpx
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(_HERE)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)
load_dotenv(os.path.join(_BACKEND, ".env"))

BASE = os.getenv("TEST_BASE_URL", "http://localhost:8001")
MONGO_URL = os.getenv("MONGO_URL")
DB_NAME = os.getenv("DB_NAME") or "test_database"


def step(msg: str) -> None:
    print(f"  ▸ {msg}")


async def make_admin_token() -> str:
    """Sign up a fresh user and promote them to admin via the DB.
    Returns a JWT for that user.
    """
    cli = httpx.AsyncClient(base_url=BASE, timeout=30)
    try:
        suffix = uuid.uuid4().int % 9_000_000
        email  = f"qa-faq-{suffix}@example.com"
        pwd    = "qaFaqP@ss1!"
        phone  = f"99{suffix:08d}"[:10]
        r = await cli.post("/api/auth/signup", json={
            "email":     email,
            "password":  pwd,
            "name":      "QA FAQ",
            "shop_name": "QA FAQ Shop",
            "phone":     phone,
        })
        assert r.status_code == 200, f"signup failed: {r.status_code} {r.text}"
        token = r.json()["token"]
        # Promote to admin via Mongo direct.
        mc = AsyncIOMotorClient(MONGO_URL)
        try:
            await mc[DB_NAME]["users"].update_one(
                {"email": email}, {"$set": {"is_admin": True}}
            )
        finally:
            mc.close()
        return token
    finally:
        await cli.aclose()


async def make_user_token() -> str:
    """Sign up a non-admin user."""
    cli = httpx.AsyncClient(base_url=BASE, timeout=30)
    try:
        suffix = (uuid.uuid4().int % 9_000_000) + 1
        email  = f"qa-faq-user-{suffix}@example.com"
        pwd    = "qaFaqP@ss1!"
        phone  = f"98{suffix:08d}"[:10]
        r = await cli.post("/api/auth/signup", json={
            "email":     email,
            "password":  pwd,
            "name":      "QA FAQ User",
            "shop_name": "QA FAQ User Shop",
            "phone":     phone,
        })
        assert r.status_code == 200, f"non-admin signup failed: {r.status_code} {r.text}"
        return r.json()["token"]
    finally:
        await cli.aclose()


async def main() -> int:
    failures = 0
    admin_token = await make_admin_token()
    user_token  = await make_user_token()

    created_id: str | None = None
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as cli:
        # ── 1. Public endpoint returns only visible rows.
        step("GET /api/faq (public)")
        r = await cli.get("/api/faq")
        if r.status_code != 200:
            print(f"    ✗ expected 200, got {r.status_code}: {r.text[:200]}")
            failures += 1
        else:
            data = r.json()
            print(f"    ✓ HTTP 200 — {data.get('count')} visible items")
            for it in data.get("items", []):
                if not it.get("is_visible", True):
                    print("    ✗ hidden item leaked into public list!")
                    failures += 1
                    break

        # ── 2. Non-admin cannot access admin list.
        step("GET /api/admin/faq with non-admin token")
        r = await cli.get(
            "/api/admin/faq",
            headers={"Authorization": f"Bearer {user_token}"},
        )
        if r.status_code in (401, 403):
            print(f"    ✓ HTTP {r.status_code} — non-admin properly blocked")
        else:
            print(f"    ✗ expected 401/403, got {r.status_code}: {r.text[:200]}")
            failures += 1

        # ── 3. Admin can list all.
        step("GET /api/admin/faq with admin token")
        r = await cli.get(
            "/api/admin/faq",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        if r.status_code != 200:
            print(f"    ✗ expected 200, got {r.status_code}: {r.text[:200]}")
            failures += 1
        else:
            data = r.json()
            print(f"    ✓ HTTP 200 — total={data.get('count')} "
                  f"visible={data.get('visible')} hidden={data.get('hidden')}")

        # ── 4. Admin can create a new FAQ.
        step("POST /api/admin/faq (create)")
        new_id = f"qa-test-{uuid.uuid4().hex[:8]}"
        r = await cli.post(
            "/api/admin/faq",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "id":       new_id,
                "category": "Test Category",
                "q":        "Why is the sky blue?",
                "a":        "Rayleigh scattering. Short answer for testing.",
                "sort_order": 9999,
                "is_visible": True,
            },
        )
        if r.status_code != 200:
            print(f"    ✗ expected 200, got {r.status_code}: {r.text[:200]}")
            failures += 1
        else:
            print("    ✓ HTTP 200 — FAQ created")
            created_id = new_id

        # ── 5. Public endpoint includes the new row when visible.
        step("GET /api/faq — new row visible")
        r = await cli.get("/api/faq")
        data = r.json()
        ids = [it["id"] for it in data.get("items", [])]
        if created_id in ids:
            print("    ✓ new row appears in public list")
        else:
            print("    ✗ new row missing from public list")
            failures += 1

        # ── 6. Hide the row, then verify public list excludes it.
        step("PATCH /api/admin/faq/{id} — set is_visible=False")
        r = await cli.patch(
            f"/api/admin/faq/{created_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"is_visible": False},
        )
        if r.status_code != 200:
            print(f"    ✗ expected 200, got {r.status_code}: {r.text[:200]}")
            failures += 1

        r = await cli.get("/api/faq")
        data = r.json()
        ids = [it["id"] for it in data.get("items", [])]
        if created_id not in ids:
            print("    ✓ hidden row no longer in public list")
        else:
            print("    ✗ hidden row STILL in public list")
            failures += 1

        # ── 6b. Admin list still shows it (with is_visible=False).
        r = await cli.get(
            "/api/admin/faq",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        data = r.json()
        match = next((i for i in data.get("items", []) if i["id"] == created_id), None)
        if match and match.get("is_visible") is False:
            print("    ✓ admin list keeps the hidden row")
        else:
            print(f"    ✗ admin list dropped the hidden row: {match}")
            failures += 1

        # ── 7. Update the question text.
        step("PATCH /api/admin/faq/{id} — change question text")
        r = await cli.patch(
            f"/api/admin/faq/{created_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"q": "Updated question text", "is_visible": True},
        )
        if r.status_code != 200:
            print(f"    ✗ expected 200, got {r.status_code}: {r.text[:200]}")
            failures += 1
        else:
            updated = r.json().get("item", {})
            if updated.get("q") == "Updated question text" and updated.get("is_visible") is True:
                print("    ✓ row updated + re-visible")
            else:
                print(f"    ✗ unexpected updated row: {updated}")
                failures += 1

        # ── 8. Non-admin cannot PATCH.
        step("PATCH with non-admin token")
        r = await cli.patch(
            f"/api/admin/faq/{created_id}",
            headers={"Authorization": f"Bearer {user_token}"},
            json={"q": "Should be blocked"},
        )
        if r.status_code in (401, 403):
            print(f"    ✓ HTTP {r.status_code} — non-admin patch blocked")
        else:
            print(f"    ✗ expected 401/403, got {r.status_code}: {r.text[:200]}")
            failures += 1

        # ── 9. Admin can delete.
        step("DELETE /api/admin/faq/{id}")
        r = await cli.delete(
            f"/api/admin/faq/{created_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        if r.status_code == 200 and r.json().get("deleted") == 1:
            print("    ✓ row deleted")
        else:
            print(f"    ✗ delete failed: HTTP {r.status_code} {r.text[:200]}")
            failures += 1

        # ── 10. After delete, row gone from admin list too.
        r = await cli.get(
            "/api/admin/faq",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        data = r.json()
        ids = [it["id"] for it in data.get("items", [])]
        if created_id not in ids:
            print("    ✓ row gone from admin list after delete")
        else:
            print("    ✗ row STILL present after delete")
            failures += 1

    # ── Cleanup user accounts created during this run.
    if MONGO_URL:
        mc = AsyncIOMotorClient(MONGO_URL)
        try:
            await mc[DB_NAME]["users"].delete_many({"email": {"$regex": r"^qa-faq-(user-)?\d+@example.com$"}})
            await mc[DB_NAME]["faq_items"].delete_many({"id": {"$regex": r"^qa-test-"}})
        finally:
            mc.close()

    print()
    if failures == 0:
        print("ALL FAQ CMS CHECKS PASSED ✅")
        return 0
    print(f"✗ {failures} check(s) failed")
    return 1


if __name__ == "__main__":
    print("Running test_faq_cms …")
    sys.exit(asyncio.run(main()))
