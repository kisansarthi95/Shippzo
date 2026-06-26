"""
Prepaid Amount Preservation Hotfix — 2026-06-25.

Verifies that POST /api/shipments and PUT /api/shipments/{id} no longer
zero out Prepaid amounts by routing them through compute_order_amounts().

Bug pre-fix: For a Prepaid row where cod=0 and token=0 the helper
returned amount=0 and overwrote the entered value. After the hotfix,
Prepaid `amount` is preserved verbatim and `cod_amount` is forced to 0.

Also regression-checks that COD path still computes amount = cod+token
and that the COD>0 validator still fires.

Credentials per /app/memory/test_credentials.md:
    user2@test.com / User@12345
"""
import os
import uuid
from pathlib import Path

import pytest
import requests
from dotenv import load_dotenv

frontend_env = Path(__file__).parent.parent.parent / "frontend" / ".env"
load_dotenv(frontend_env)

BASE_URL = (
    os.environ.get("EXPO_PUBLIC_BACKEND_URL")
    or os.environ.get("EXPO_BACKEND_URL")
)
if not BASE_URL:
    raise RuntimeError("EXPO_PUBLIC_BACKEND_URL missing from frontend/.env")
BASE_URL = BASE_URL.rstrip("/")

USER_EMAIL = "user2@test.com"
USER_PASSWORD = "User@12345"


# ----------------------------------------------------- fixtures --
@pytest.fixture(scope="module")
def api_client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def user_token(api_client):
    r = api_client.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": USER_EMAIL, "password": USER_PASSWORD},
        timeout=20,
    )
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text[:200]}"
    return r.json()["token"]


@pytest.fixture(scope="module")
def user_headers(user_token):
    return {
        "Authorization": f"Bearer {user_token}",
        "Content-Type": "application/json",
    }


@pytest.fixture(scope="module")
def user_courier_id(api_client, user_headers):
    r = api_client.get(
        f"{BASE_URL}/api/couriers", headers=user_headers, timeout=20,
    )
    if r.status_code == 200:
        data = r.json()
        items = data if isinstance(data, list) else (
            data.get("couriers") or data.get("items") or []
        )
        if items:
            return items[0].get("id") or items[0].get("_id")
    pytest.skip("No courier available")


def _payload(courier_id, **overrides):
    unique = uuid.uuid4().hex[:6].upper()
    base = {
        "customer_name": f"TEST_PREPAID_HOTFIX_{unique}",
        "customer_phone": "9876543210",
        "address_line1": "1 Hotfix Lane",
        "city": "Ahmedabad",
        "state": "Gujarat",
        "pincode": "380001",
        "items": ["Widget"],
        "courier_id": courier_id,
        "tracking_id": f"TPREP{unique}",
    }
    base.update(overrides)
    return base


# ============================== A) POST Prepaid path =====================
class TestPostPrepaidPreserves:
    """Prepaid amount must NOT be re-computed via cod+token."""

    def test_A1_prepaid_amount_with_zero_cod_and_token(
        self, api_client, user_headers, user_courier_id,
    ):
        """The exact bug repro: Prepaid + amount=500 + cod=0 + token=0
        previously wrote amount=0. Hotfix preserves 500."""
        p = _payload(
            user_courier_id,
            payment_mode="Prepaid", amount=500,
            cod_amount=0, token_amount=0,
        )
        r = api_client.post(
            f"{BASE_URL}/api/shipments", headers=user_headers,
            json=p, timeout=60,
        )
        assert r.status_code == 200, f"{r.status_code} {r.text[:400]}"
        body = r.json()
        assert body["amount"] == 500.0, (
            f"REGRESSION: amount={body['amount']} expected 500.0 (Prepaid preserve)"
        )
        assert body["cod_amount"] == 0.0
        assert body["token_amount"] == 0.0
        # GET to verify persistence
        sid = body["id"]
        g = api_client.get(
            f"{BASE_URL}/api/shipments/{sid}", headers=user_headers, timeout=20,
        )
        assert g.status_code == 200
        gb = g.json()
        assert gb["amount"] == 500.0
        assert gb["cod_amount"] == 0.0

    def test_A2_prepaid_amount_with_token_carry_through(
        self, api_client, user_headers, user_courier_id,
    ):
        """Prepaid amount=1000 + token=200 → amount stays 1000;
        token is a passive carry-through, NOT subtracted."""
        p = _payload(
            user_courier_id,
            payment_mode="Prepaid", amount=1000, token_amount=200,
        )
        r = api_client.post(
            f"{BASE_URL}/api/shipments", headers=user_headers,
            json=p, timeout=60,
        )
        assert r.status_code == 200, f"{r.status_code} {r.text[:400]}"
        body = r.json()
        assert body["amount"] == 1000.0, (
            f"amount={body['amount']} expected 1000.0 (token must not subtract)"
        )
        assert body["cod_amount"] == 0.0
        assert body["token_amount"] == 200.0

    def test_A3_prepaid_legacy_total_in_cod_amount(
        self, api_client, user_headers, user_courier_id,
    ):
        """Legacy client sent total in cod_amount for a Prepaid row.
        Hotfix falls back to cod_amount when amount is 0/missing."""
        p = _payload(
            user_courier_id,
            payment_mode="Prepaid", amount=0, cod_amount=750,
        )
        r = api_client.post(
            f"{BASE_URL}/api/shipments", headers=user_headers,
            json=p, timeout=60,
        )
        assert r.status_code == 200, f"{r.status_code} {r.text[:400]}"
        body = r.json()
        assert body["amount"] == 750.0, (
            f"amount={body['amount']} expected 750.0 (legacy cod_amount fallback)"
        )
        assert body["cod_amount"] == 0.0

    def test_A4_payment_mode_missing_defaults_to_prepaid_preserve(
        self, api_client, user_headers, user_courier_id,
    ):
        """payment_mode empty/missing + amount=400 → amount preserved 400."""
        p = _payload(
            user_courier_id,
            amount=400,
            # Explicitly omit payment_mode — must default to Prepaid preserve.
        )
        # Make sure no payment_mode key
        p.pop("payment_mode", None)
        r = api_client.post(
            f"{BASE_URL}/api/shipments", headers=user_headers,
            json=p, timeout=60,
        )
        assert r.status_code == 200, f"{r.status_code} {r.text[:400]}"
        body = r.json()
        assert body["amount"] == 400.0, (
            f"amount={body['amount']} expected 400.0 (empty pm → preserve)"
        )
        assert body["cod_amount"] == 0.0


# ============================== B) POST COD regression ===================
class TestPostCodRegression:
    def test_B1_cod_amount_equals_cod_plus_token(
        self, api_client, user_headers, user_courier_id,
    ):
        """COD: cod=500 + token=100 → amount=600 (canonical rule preserved)."""
        p = _payload(
            user_courier_id,
            payment_mode="COD",
            cod_amount=500, token_amount=100,
            amount=500,  # legacy mirrors cod
        )
        r = api_client.post(
            f"{BASE_URL}/api/shipments", headers=user_headers,
            json=p, timeout=60,
        )
        assert r.status_code == 200, f"{r.status_code} {r.text[:400]}"
        body = r.json()
        assert body["amount"] == 600.0
        assert body["cod_amount"] == 500.0
        assert body["token_amount"] == 100.0

    def test_B2_cod_with_zero_cod_amount_rejected_422(
        self, api_client, user_headers, user_courier_id,
    ):
        """COD + cod_amount=0 must still raise 422 (validator unchanged)."""
        p = _payload(
            user_courier_id,
            payment_mode="COD",
            cod_amount=0, token_amount=100, amount=0,
        )
        r = api_client.post(
            f"{BASE_URL}/api/shipments", headers=user_headers,
            json=p, timeout=60,
        )
        assert r.status_code == 422, (
            f"Expected 422 for COD with cod=0, got {r.status_code} {r.text[:300]}"
        )


# ============================== C) PUT Prepaid edits =====================
class TestPutPrepaidEdits:
    def _create_prepaid(self, api_client, user_headers, user_courier_id, amount):
        p = _payload(
            user_courier_id,
            payment_mode="Prepaid", amount=amount,
        )
        r = api_client.post(
            f"{BASE_URL}/api/shipments", headers=user_headers,
            json=p, timeout=60,
        )
        assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
        return r.json()

    def test_C1_put_amount_on_prepaid_row(
        self, api_client, user_headers, user_courier_id,
    ):
        ship = self._create_prepaid(
            api_client, user_headers, user_courier_id, 500,
        )
        sid = ship["id"]
        r = api_client.put(
            f"{BASE_URL}/api/shipments/{sid}",
            headers=user_headers, json={"amount": 700}, timeout=30,
        )
        assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
        body = r.json()
        assert body["amount"] == 700.0, (
            f"amount={body['amount']} expected 700.0 after PUT on Prepaid"
        )
        assert body["cod_amount"] == 0.0

    def test_C2_put_token_only_on_prepaid_row(
        self, api_client, user_headers, user_courier_id,
    ):
        """PUT only token_amount on a Prepaid row — amount unchanged."""
        ship = self._create_prepaid(
            api_client, user_headers, user_courier_id, 600,
        )
        sid = ship["id"]
        r = api_client.put(
            f"{BASE_URL}/api/shipments/{sid}",
            headers=user_headers, json={"token_amount": 50}, timeout=30,
        )
        assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
        body = r.json()
        # amount/cod block doesn't fire (no amount/cod_amount key in update).
        # token_amount is still on the row.
        assert body["amount"] == 600.0, (
            f"amount={body['amount']} expected 600.0 (unchanged on token-only PUT)"
        )
        assert body["token_amount"] == 50.0
        assert body["cod_amount"] == 0.0


# ============================== D) PUT COD regression ====================
class TestPutCodRegression:
    def _create_cod(self, api_client, user_headers, user_courier_id, cod, tok=0):
        p = _payload(
            user_courier_id,
            payment_mode="COD",
            cod_amount=cod, token_amount=tok, amount=cod,
        )
        r = api_client.post(
            f"{BASE_URL}/api/shipments", headers=user_headers,
            json=p, timeout=60,
        )
        assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
        return r.json()

    def test_D1_put_cod_and_token(
        self, api_client, user_headers, user_courier_id,
    ):
        ship = self._create_cod(api_client, user_headers, user_courier_id, 400, 0)
        sid = ship["id"]
        r = api_client.put(
            f"{BASE_URL}/api/shipments/{sid}",
            headers=user_headers,
            json={"cod_amount": 600, "token_amount": 100}, timeout=30,
        )
        assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
        body = r.json()
        assert body["amount"] == 700.0
        assert body["cod_amount"] == 600.0
        assert body["token_amount"] == 100.0

    def test_D2_put_amount_only_on_cod_row_fallback(
        self, api_client, user_headers, user_courier_id,
    ):
        """PUT amount=800 on COD row with token=100; cod_amount missing in
        update → fallback uses update.amount as cod → amount = 800+100=900."""
        ship = self._create_cod(api_client, user_headers, user_courier_id, 400, 100)
        sid = ship["id"]
        r = api_client.put(
            f"{BASE_URL}/api/shipments/{sid}",
            headers=user_headers, json={"amount": 800}, timeout=30,
        )
        assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
        body = r.json()
        assert body["amount"] == 900.0, (
            f"amount={body['amount']} expected 900.0 (cod=800 fallback + tok=100)"
        )
        assert body["cod_amount"] == 800.0
        assert body["token_amount"] == 100.0


# ============================== E) Repair script idempotency =============
class TestRepairScriptIdempotency:
    """Re-running the one-off repair script must be a no-op now that the
    two real Prepaid zero-amount rows were already fixed in production."""

    def _run(self, dry_run: bool):
        import subprocess
        cmd = ["python", "-m", "scripts.repair_prepaid_zero_amount"]
        if dry_run:
            cmd.append("--dry-run")
        r = subprocess.run(
            cmd, cwd="/app/backend",
            capture_output=True, text=True, timeout=60,
        )
        return r

    def test_E1_dry_run_repairs_zero(self):
        r = self._run(dry_run=True)
        assert r.returncode == 0, f"script failed: {r.stderr[-400:]}"
        # Parse `repaired: N` from the log output (stderr or stdout).
        out = (r.stdout or "") + (r.stderr or "")
        assert "repaired: 0" in out, (
            f"Expected 'repaired: 0' on dry-run re-run, got:\n{out[-800:]}"
        )

    def test_E2_live_run_repairs_zero(self):
        r = self._run(dry_run=False)
        assert r.returncode == 0, f"script failed: {r.stderr[-400:]}"
        out = (r.stdout or "") + (r.stderr or "")
        assert "repaired: 0" in out, (
            f"Expected 'repaired: 0' on live re-run, got:\n{out[-800:]}"
        )

    def test_E3_previously_repaired_shipments_still_correct(
        self, api_client,
    ):
        """Verify the two prod-fixed shipment ids still hold the
        restored amounts (669.0 and 300.0). Direct Mongo check — we
        don't know which user owns them, so use motor."""
        import asyncio
        from motor.motor_asyncio import AsyncIOMotorClient
        from dotenv import load_dotenv
        load_dotenv("/app/backend/.env")
        mongo = os.environ["MONGO_URL"]
        dbn   = os.environ["DB_NAME"]

        async def _check():
            cli = AsyncIOMotorClient(mongo)
            db = cli[dbn]
            r1 = await db.shipments.find_one(
                {"id": "0260c63b-6af7-4dd8-8bb6-a9f938391a23"},
                {"_id": 0, "amount": 1},
            )
            r2 = await db.shipments.find_one(
                {"id": "0b74e543-6d13-4267-97c6-9c51215c6105"},
                {"_id": 0, "amount": 1},
            )
            cli.close()
            return r1, r2

        r1, r2 = asyncio.run(_check())
        if r1 is None and r2 is None:
            pytest.skip(
                "Neither prod-fixed shipment exists in this DB — likely a "
                "fresh sandbox. Skipping idempotency value check."
            )
        if r1 is not None:
            assert r1["amount"] == 669.0, (
                f"Shipment 0260c63b amount={r1['amount']} expected 669.0"
            )
        if r2 is not None:
            assert r2["amount"] == 300.0, (
                f"Shipment 0b74e543 amount={r2['amount']} expected 300.0"
            )
