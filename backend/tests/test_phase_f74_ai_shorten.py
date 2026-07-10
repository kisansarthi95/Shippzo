"""
Phase F7.4 — India Post Complaint AI Shorten tests.

Endpoint under test:
    POST /api/shipments/complaint/ai-shorten
    { "description": "<any-language complaint text>" }

Behaviour spec (from review request):
  1. Happy path → 200, response contains ok, rewritten (English, ≤250 chars),
     credits_deducted=0.5, balance_after (number), chars (int == len(rewritten)).
  2. Empty description → 422; NO credit deducted.
  3. Insufficient credits (wallet < 0.5) → 402; NO credit deducted; NO LLM call.
  4. Missing auth → 401 or 403.
  5. Long input (>4000 chars) → 413; NO credit deducted.
  6. Multiple successful calls charge cumulatively (2 × 0.5 = 1.0) and each writes
     ONE credit_history row with ctype="ai_processing" credits=-0.5.

credit_history integrity: only success path writes a row.
"""
from __future__ import annotations

import asyncio
import os
from typing import List, Optional

import pytest
import requests

BASE_URL = (
    os.environ.get("EXPO_PUBLIC_BACKEND_URL")
    or os.environ.get("EXPO_BACKEND_URL")
    or ""
).rstrip("/")
assert BASE_URL, "EXPO_PUBLIC_BACKEND_URL / EXPO_BACKEND_URL not set"

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASS = "Admin@12345"

GUJARATI_COMPLAINT = (
    "મારો પાર્સલ 15 દિવસ થી ડિલિવર થયો નથી. "
    "ટ્રેકિંગ EA123456789IN. કૃપા કરી ચેક કરો."
)


# ─── Fixtures ────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def admin_headers():
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASS},
        timeout=15,
    )
    assert r.status_code == 200, f"Admin login failed: {r.status_code} {r.text}"
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture(scope="module")
def admin_user_id(admin_headers):
    r = requests.get(f"{BASE_URL}/api/auth/me", headers=admin_headers, timeout=15)
    assert r.status_code == 200
    return r.json()["id"]


@pytest.fixture(scope="module")
def mongo_db():
    """Direct Mongo access via pymongo (sync) — avoids event-loop lifetime
    issues that Motor has when re-used across asyncio.run() calls."""
    from pymongo import MongoClient
    mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    db_name = os.environ.get("DB_NAME", "test_database")
    cli = MongoClient(mongo_url)
    yield cli[db_name]
    cli.close()


# ─── Helpers (sync) ──────────────────────────────────────────────────
def _get_wallet_balance(db, uid: str) -> float:
    w = db.wallets.find_one({"user_id": uid}) or {}
    return float(w.get("remaining_credits", 0.0))


def _set_wallet_balance(db, uid: str, target: float) -> None:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    db.wallets.update_one(
        {"user_id": uid},
        {
            "$set": {
                "user_id": uid,
                "total_credits": float(target),
                "used_credits": 0.0,
                "remaining_credits": float(target),
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )


def _count_ai_shorten_history(db, uid: str) -> int:
    return db.credit_history.count_documents({
        "user_id": uid,
        "type": "ai_processing",
        "credits": -0.5,
        "description": {"$regex": "AI Shorten"},
    })


@pytest.fixture()
def restore_wallet(admin_user_id, mongo_db):
    """Snapshot wallet + shorten-history count before test, restore after."""
    snapshot = mongo_db.wallets.find_one({"user_id": admin_user_id}) or {}
    hist_before = _count_ai_shorten_history(mongo_db, admin_user_id)
    yield {"before_balance": float(snapshot.get("remaining_credits", 0.0)),
           "history_before": hist_before}

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    mongo_db.wallets.update_one(
        {"user_id": admin_user_id},
        {"$set": {
            "total_credits": float(snapshot.get("total_credits", 0.0)),
            "used_credits":  float(snapshot.get("used_credits", 0.0)),
            "remaining_credits": float(snapshot.get("remaining_credits", 0.0)),
            "updated_at": now,
        }},
        upsert=True,
    )
    mongo_db.credit_history.delete_many({
        "user_id": admin_user_id,
        "type": "ai_processing",
        "description": {"$regex": "AI Shorten"},
        "credits": -0.5,
    })


# ═════════════════════ Auth ══════════════════════════════════════════
class TestAuth:
    def test_no_auth_rejected(self):
        r = requests.post(
            f"{BASE_URL}/api/shipments/complaint/ai-shorten",
            json={"description": GUJARATI_COMPLAINT}, timeout=15,
        )
        assert r.status_code in (401, 403), f"expected 401/403, got {r.status_code}"


# ═════════════════════ Input validation (no LLM, no charge) ══════════
class TestInputValidation:
    def test_empty_description_422(self, admin_headers, restore_wallet, mongo_db, admin_user_id):
        r = requests.post(
            f"{BASE_URL}/api/shipments/complaint/ai-shorten",
            headers=admin_headers,
            json={"description": ""}, timeout=15,
        )
        assert r.status_code == 422, r.text
        # No credit history row added
        after = _count_ai_shorten_history(mongo_db, admin_user_id)
        assert after == restore_wallet["history_before"]
        # Wallet unchanged
        bal_after = _get_wallet_balance(mongo_db, admin_user_id)
        assert abs(bal_after - restore_wallet["before_balance"]) < 1e-6

    def test_whitespace_only_description_422(self, admin_headers):
        r = requests.post(
            f"{BASE_URL}/api/shipments/complaint/ai-shorten",
            headers=admin_headers,
            json={"description": "     \n\t  "}, timeout=15,
        )
        assert r.status_code == 422, r.text

    def test_long_input_413(self, admin_headers, restore_wallet, mongo_db, admin_user_id):
        big = "x" * 5000
        r = requests.post(
            f"{BASE_URL}/api/shipments/complaint/ai-shorten",
            headers=admin_headers,
            json={"description": big}, timeout=15,
        )
        assert r.status_code == 413, f"expected 413, got {r.status_code}: {r.text[:200]}"
        after = _count_ai_shorten_history(mongo_db, admin_user_id)
        assert after == restore_wallet["history_before"]
        bal_after = _get_wallet_balance(mongo_db, admin_user_id)
        assert abs(bal_after - restore_wallet["before_balance"]) < 1e-6


# ═════════════════════ Insufficient credits ══════════════════════════
class TestInsufficientCredits:
    def test_402_when_wallet_below_half_credit(
        self, admin_headers, mongo_db, admin_user_id, restore_wallet,
    ):
        # Force wallet to 0.1
        _set_wallet_balance(mongo_db, admin_user_id, 0.1)
        hist_before = _count_ai_shorten_history(mongo_db, admin_user_id)

        r = requests.post(
            f"{BASE_URL}/api/shipments/complaint/ai-shorten",
            headers=admin_headers,
            json={"description": GUJARATI_COMPLAINT}, timeout=30,
        )
        assert r.status_code == 402, f"expected 402, got {r.status_code}: {r.text}"
        assert "Insufficient" in r.text or "insufficient" in r.text.lower()

        # Wallet remained at 0.1 (no deduction)
        bal = _get_wallet_balance(mongo_db, admin_user_id)
        assert abs(bal - 0.1) < 1e-6, f"wallet was touched: {bal}"
        # No new AI-shorten history row
        hist_after = _count_ai_shorten_history(mongo_db, admin_user_id)
        assert hist_after == hist_before, "history row created despite 402"


# ═════════════════════ Happy path (real LLM call) ════════════════════
class TestHappyPath:
    def test_gujarati_complaint_shortens_and_charges_half(
        self, admin_headers, mongo_db, admin_user_id, restore_wallet,
    ):
        # Ensure enough balance
        _set_wallet_balance(mongo_db, admin_user_id, 5.0)
        hist_before = _count_ai_shorten_history(mongo_db, admin_user_id)

        r = requests.post(
            f"{BASE_URL}/api/shipments/complaint/ai-shorten",
            headers=admin_headers,
            json={"description": GUJARATI_COMPLAINT}, timeout=45,
        )
        assert r.status_code == 200, f"got {r.status_code}: {r.text[:400]}"
        body = r.json()

        # Response shape
        assert body.get("ok") is True
        rewritten = body.get("rewritten") or ""
        assert isinstance(rewritten, str) and rewritten, "rewritten missing/empty"
        assert len(rewritten) <= 250, f"rewritten > 250 chars: {len(rewritten)}"
        assert body.get("credits_deducted") == 0.5
        assert isinstance(body.get("balance_after"), (int, float))
        assert body.get("chars") == len(rewritten)
        # Balance decreased by 0.5
        assert abs(body["balance_after"] - (5.0 - 0.5)) < 1e-3, body

        # Best-effort English sanity check: contains ASCII letters, no Devanagari/Gujarati.
        import re
        assert re.search(r"[A-Za-z]", rewritten), "no ASCII letters in output"
        assert not re.search(r"[\u0A80-\u0AFF\u0900-\u097F]", rewritten), \
            "rewritten still contains Gujarati/Devanagari characters"

        # Wallet reflected
        bal = _get_wallet_balance(mongo_db, admin_user_id)
        assert abs(bal - 4.5) < 1e-3, bal

        # credit_history: exactly one new AI-shorten row
        hist_after = _count_ai_shorten_history(mongo_db, admin_user_id)
        assert hist_after == hist_before + 1

        # Fetch and inspect the latest entry
        docs = await_history_entries(mongo_db, admin_user_id)
        assert docs, "no credit_history docs"
        latest = docs[0]
        assert latest.get("type") == "ai_processing"
        assert latest.get("credits") == -0.5
        assert "AI Shorten" in (latest.get("description") or "")

    def test_two_successful_calls_charge_cumulatively(
        self, admin_headers, mongo_db, admin_user_id, restore_wallet,
    ):
        _set_wallet_balance(mongo_db, admin_user_id, 5.0)
        hist_before = _count_ai_shorten_history(mongo_db, admin_user_id)

        # Call twice
        outs = []
        for i in range(2):
            r = requests.post(
                f"{BASE_URL}/api/shipments/complaint/ai-shorten",
                headers=admin_headers,
                json={"description": GUJARATI_COMPLAINT + f" (call {i+1})"},
                timeout=45,
            )
            assert r.status_code == 200, f"call {i+1} failed: {r.status_code} {r.text[:200]}"
            outs.append(r.json())

        # Cumulative deduction 1.0
        assert abs(outs[-1]["balance_after"] - (5.0 - 1.0)) < 1e-3

        bal = _get_wallet_balance(mongo_db, admin_user_id)
        assert abs(bal - 4.0) < 1e-3, bal

        # Exactly 2 new history rows
        hist_after = _count_ai_shorten_history(mongo_db, admin_user_id)
        assert hist_after == hist_before + 2, (
            f"expected +2 history rows, got +{hist_after - hist_before}"
        )


# ─── Small helper ────────────────────────────────────────────────────
def await_history_entries(db, uid: str, limit: int = 5):
    return list(db.credit_history.find(
        {"user_id": uid, "type": "ai_processing",
         "description": {"$regex": "AI Shorten"}, "credits": -0.5},
    ).sort("created_at", -1).limit(limit))
