"""
Phase F10.2 — COD Payment import mismatch detection (bug fix regression).

Bug context (see review request):
  Article EG350860354IN has amount=719 (order total incl. advance token)
  and cod_amount=669 (what courier should collect).
  Uploading a cod_payment sheet with cod_collected_amount=719 previously
  produced a false matched_ok. After fix:
    (A) Cross-verify 'amount' baseline for cod_payment must be cod_amount.
    (B) cod_payment branch must explicitly compare cod_collected_amount
        vs stored cod_amount and append a `cod_amount` mismatch when they
        differ (unless the cross-verify loop already flagged one).

Test coverage:
 1. Discrepant remit (cod_collected=719, expected 669) → mismatch on
    field='cod_amount' with existing=669/imported=719, row_status
    matched_mismatch, batch tally matched_mismatch>=1.
 2. Exact remit (cod_collected=669) → matched_updated (payment stamped),
    no mismatch, batch tally matched_mismatch=0.
 3. Shipment Details endpoint surfaces the mismatch through the
    import_validation_alerts array (post-mismatch import).
 4. Regression: booking import still uses `amount` (order total 719)
    as baseline, so a booking upload of amount=719 for the same
    shipment reports MATCHED (no mismatch).
 5. Regression: booking import uploading amount=999 still flips to
    matched_mismatch (proves cross-verify path unchanged for booking).
"""
import os
import io
import csv
import json
import pytest
import requests

BASE_URL = os.environ.get(
    "EXPO_PUBLIC_BACKEND_URL",
    "https://logistics-hub-740.preview.emergentagent.com",
).rstrip("/")

ADMIN_EMAIL = "admin@test.com"
ADMIN_PASSWORD = "Admin@12345"
TARGET_TRACKING = "EG350860354IN"


# ────────────────── Fixtures ──────────────────
@pytest.fixture(scope="module")
def admin_headers():
    resp = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=20,
    )
    assert resp.status_code == 200, f"login failed: {resp.status_code} {resp.text}"
    tok = resp.json().get("token")
    assert tok
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture(scope="module")
def target_shipment(admin_headers):
    """Locate EG350860354IN (or synthesize a fallback with amount!=cod_amount)."""
    resp = requests.get(
        f"{BASE_URL}/api/shipments?limit=1000&search={TARGET_TRACKING}",
        headers=admin_headers, timeout=20,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    shps = data if isinstance(data, list) else data.get("shipments") or data.get("items") or []
    match = [s for s in shps if (s.get("tracking_id") or "").strip() == TARGET_TRACKING]
    assert match, f"Target article {TARGET_TRACKING} not found in admin account"
    s = match[0]
    assert float(s.get("amount") or 0) != float(s.get("cod_amount") or 0), (
        f"Fixture precondition violated — amount ({s.get('amount')}) must differ from "
        f"cod_amount ({s.get('cod_amount')}) for this test to be meaningful."
    )
    return s


# ────────────────── Helpers ──────────────────
def _csv_bytes(headers, rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(headers)
    for r in rows:
        w.writerow(r)
    return buf.getvalue().encode("utf-8")


def _commit(admin_headers, import_type, headers, rows, mapping, filename):
    files = {"file": (filename, _csv_bytes(headers, rows), "text/csv")}
    data = {
        "import_type": import_type,
        "mapping":     json.dumps(mapping),
        "save_default": "false",
        "header_row":   "1",
        "data_start_row": "2",
        "header_col":   "1",
    }
    resp = requests.post(
        f"{BASE_URL}/api/shipments/import/commit",
        headers=admin_headers,
        files=files, data=data, timeout=30,
    )
    return resp


def _fetch_batch(admin_headers, batch_id):
    r = requests.get(
        f"{BASE_URL}/api/shipments/import/batches/{batch_id}",
        headers=admin_headers, timeout=20,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _row_for_tracking(batch, tracking):
    for row in batch.get("rows", []):
        if (row.get("tracking_id") or "").strip() == tracking:
            return row
    return None


# ────────────────── Tests ──────────────────
class TestCodPaymentMismatch:
    """Phase F10.2 — cod_payment import must catch discrepancies against cod_amount."""

    def test_cod_payment_discrepant_upload_flags_mismatch(self, admin_headers, target_shipment):
        """cod_collected_amount=719 vs stored cod_amount=669 → matched_mismatch."""
        headers = ["Tracking", "COD Collected"]
        rows = [[TARGET_TRACKING, "719"]]
        mapping = {"Tracking": "tracking_id", "COD Collected": "cod_collected_amount"}
        resp = _commit(admin_headers, "cod_payment", headers, rows, mapping,
                       "cod_discrepant.csv")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        assert body["matched_mismatch"] >= 1, (
            f"Expected matched_mismatch>=1 for discrepant COD, got {body}"
        )
        # Fetch the full batch to inspect the row's mismatches[]
        batch = _fetch_batch(admin_headers, body["batch_id"])
        row = _row_for_tracking(batch, TARGET_TRACKING)
        assert row is not None, f"row not found in batch: {batch}"
        assert row["status"] == "matched_mismatch", (
            f"row status expected matched_mismatch, got {row['status']}. row={row}"
        )
        mm_fields = {m["field"]: m for m in row.get("mismatches", [])}
        assert "cod_amount" in mm_fields, (
            f"Expected 'cod_amount' mismatch entry. mismatches={row.get('mismatches')}"
        )
        entry = mm_fields["cod_amount"]
        assert float(entry["existing"]) == float(target_shipment["cod_amount"]), entry
        assert float(entry["imported"]) == 719.0, entry

    def test_shipment_details_surfaces_import_alert(self, admin_headers, target_shipment):
        """After the discrepant import, GET /api/shipments/{id} must show the alert."""
        sid = target_shipment["id"]
        r = requests.get(
            f"{BASE_URL}/api/shipments/{sid}",
            headers=admin_headers, timeout=20,
        )
        assert r.status_code == 200, r.text
        doc = r.json()
        alerts = doc.get("import_validation_alerts") or []
        # Bug fix promises this alert list surfaces the cod_amount mismatch.
        cod_alerts = [a for a in alerts if a.get("field") == "cod_amount"]
        assert cod_alerts, (
            "Shipment Details endpoint did not surface a cod_amount alert via "
            f"import_validation_alerts. Got: {alerts}"
        )
        a = cod_alerts[0]
        assert float(a.get("existing")) == float(target_shipment["cod_amount"])
        assert float(a.get("imported")) == 719.0

    def test_cod_payment_matching_upload_no_mismatch(self, admin_headers, target_shipment):
        """cod_collected_amount=669 → matched_updated, no mismatch, tally clean."""
        expected_cod = float(target_shipment["cod_amount"])
        headers = ["Tracking", "COD Collected"]
        rows = [[TARGET_TRACKING, str(expected_cod)]]
        mapping = {"Tracking": "tracking_id", "COD Collected": "cod_collected_amount"}
        resp = _commit(admin_headers, "cod_payment", headers, rows, mapping,
                       "cod_match.csv")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["matched_mismatch"] == 0, (
            f"Expected zero mismatches for exact-match remit, got {body}"
        )
        batch = _fetch_batch(admin_headers, body["batch_id"])
        row = _row_for_tracking(batch, TARGET_TRACKING)
        assert row is not None
        assert row["status"] in ("matched_updated", "matched_no_change"), row
        assert not row.get("mismatches"), (
            f"Expected empty mismatches on exact match, got {row.get('mismatches')}"
        )


class TestBookingImportRegression:
    """Booking / delivery imports must continue to use `amount` (order total)
    as the cross-verify baseline. Uploading the order total (719) MUST NOT
    trigger a mismatch; only a truly different value should."""

    def test_booking_import_matching_amount_no_mismatch(self, admin_headers, target_shipment):
        order_total = float(target_shipment["amount"])
        headers = ["Tracking", "Amount"]
        rows = [[TARGET_TRACKING, str(order_total)]]
        mapping = {"Tracking": "tracking_id", "Amount": "amount"}
        resp = _commit(admin_headers, "booking", headers, rows, mapping,
                       "booking_match.csv")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # order total upload → no mismatch (baseline is `amount` for booking)
        assert body["matched_mismatch"] == 0, (
            f"Booking import with matching order total should not mismatch. body={body}"
        )
        batch = _fetch_batch(admin_headers, body["batch_id"])
        row = _row_for_tracking(batch, TARGET_TRACKING)
        assert row is not None
        assert row["status"] in ("matched_updated", "matched_no_change"), row

    def test_booking_import_diverging_amount_flags_mismatch(self, admin_headers, target_shipment):
        """Sanity-check the booking cross-verify path is still wired up."""
        headers = ["Tracking", "Amount"]
        rows = [[TARGET_TRACKING, "999"]]
        mapping = {"Tracking": "tracking_id", "Amount": "amount"}
        resp = _commit(admin_headers, "booking", headers, rows, mapping,
                       "booking_diverge.csv")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["matched_mismatch"] >= 1, body
        batch = _fetch_batch(admin_headers, body["batch_id"])
        row = _row_for_tracking(batch, TARGET_TRACKING)
        assert row is not None
        assert row["status"] == "matched_mismatch"
        mm_fields = {m["field"] for m in row.get("mismatches", [])}
        assert "amount" in mm_fields, row.get("mismatches")
