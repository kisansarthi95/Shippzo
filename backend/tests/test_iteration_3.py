"""
Backend tests for Courier Label Manager - Iteration 3
Tests: Sample CSV template endpoint, Enhanced stats with COD/Prepaid breakdown
"""
import pytest
import requests
import os
from pathlib import Path
from dotenv import load_dotenv

# Load frontend .env to get EXPO_PUBLIC_BACKEND_URL
frontend_env = Path(__file__).parent.parent.parent / "frontend" / ".env"
load_dotenv(frontend_env)

BASE_URL = os.environ.get('EXPO_PUBLIC_BACKEND_URL')
if not BASE_URL:
    raise ValueError("EXPO_PUBLIC_BACKEND_URL not found in environment")
BASE_URL = BASE_URL.rstrip('/')


class TestSampleTemplate:
    """Test GET /api/sheets/sample-template endpoint"""

    def test_sample_template_returns_csv(self, api_client):
        """Sample template endpoint returns CSV with correct content-type"""
        response = api_client.get(f"{BASE_URL}/api/sheets/sample-template")
        assert response.status_code == 200
        assert "text/csv" in response.headers.get("content-type", "")
        print("✅ Sample template returns 200 with text/csv content-type")

    def test_sample_template_has_attachment_header(self, api_client):
        """Sample template has Content-Disposition attachment header"""
        response = api_client.get(f"{BASE_URL}/api/sheets/sample-template")
        assert response.status_code == 200
        disposition = response.headers.get("content-disposition", "")
        assert "attachment" in disposition.lower()
        assert "courier_sheet_template.csv" in disposition
        print(f"✅ Content-Disposition header present: {disposition}")

    def test_sample_template_has_correct_columns(self, api_client):
        """Sample template CSV has correct column headers"""
        response = api_client.get(f"{BASE_URL}/api/sheets/sample-template")
        assert response.status_code == 200
        lines = response.text.strip().split("\n")
        assert len(lines) >= 5  # header + 4 sample rows
        
        header = lines[0]
        expected_cols = [
            "Timestamp", "Order ID", "Name", "Phone", "Address",
            "City", "State", "Pincode", "Item", "Amount", "Payment Mode"
        ]
        for col in expected_cols:
            assert col in header, f"Missing column: {col}"
        print(f"✅ CSV header has all expected columns: {header}")

    def test_sample_template_has_4_sample_rows(self, api_client):
        """Sample template has 4 sample data rows (Gujarat businesses)"""
        response = api_client.get(f"{BASE_URL}/api/sheets/sample-template")
        assert response.status_code == 200
        lines = response.text.strip().split("\n")
        data_rows = lines[1:]  # skip header
        assert len(data_rows) == 4, f"Expected 4 sample rows, got {len(data_rows)}"
        
        # Check that rows contain Gujarat data
        full_text = response.text
        assert "Gujarat" in full_text
        assert "Ahmedabad" in full_text or "Rajkot" in full_text or "Surat" in full_text
        print(f"✅ CSV has 4 sample rows with Gujarat business data")


class TestEnhancedStats:
    """Test GET /api/shipments/stats with COD/Prepaid breakdown"""

    def test_stats_includes_new_fields(self, api_client):
        """Stats endpoint returns cod_count, prepaid_count, prepaid_total"""
        response = api_client.get(f"{BASE_URL}/api/shipments/stats")
        assert response.status_code == 200
        data = response.json()
        
        # Check all required fields exist
        required_fields = [
            "total", "delivered", "pending",
            "cod_total", "cod_count",
            "prepaid_total", "prepaid_count",
            "revenue_total"
        ]
        for field in required_fields:
            assert field in data, f"Missing field: {field}"
        
        print(f"✅ Stats includes all fields: {list(data.keys())}")
        print(f"   COD: count={data['cod_count']}, total=₹{data['cod_total']}")
        print(f"   Prepaid: count={data['prepaid_count']}, total=₹{data['prepaid_total']}")
        print(f"   Revenue total: ₹{data['revenue_total']}")

    def test_stats_aggregation_prepaid_and_cod(self, api_client):
        """Create Prepaid (500) + COD (1000) shipments, verify stats aggregation"""
        # Create Prepaid shipment
        prepaid_payload = {
            "tracking_id": "TEST_ITER3_PREPAID_001",
            "customer_name": "TEST Prepaid Customer",
            "payment_mode": "Prepaid",
            "amount": 500
        }
        prepaid_response = api_client.post(f"{BASE_URL}/api/shipments", json=prepaid_payload)
        assert prepaid_response.status_code == 200
        print(f"✅ Created Prepaid shipment: {prepaid_response.json()['tracking_id']}")

        # Create COD shipment
        cod_payload = {
            "tracking_id": "TEST_ITER3_COD_001",
            "customer_name": "TEST COD Customer",
            "payment_mode": "COD",
            "amount": 1000
        }
        cod_response = api_client.post(f"{BASE_URL}/api/shipments", json=cod_payload)
        assert cod_response.status_code == 200
        print(f"✅ Created COD shipment: {cod_response.json()['tracking_id']}")

        # Get stats
        stats_response = api_client.get(f"{BASE_URL}/api/shipments/stats")
        assert stats_response.status_code == 200
        stats = stats_response.json()

        # Verify aggregation (≥ because there might be existing shipments)
        assert stats["prepaid_total"] >= 500, f"prepaid_total should be ≥500, got {stats['prepaid_total']}"
        assert stats["cod_total"] >= 1000, f"cod_total should be ≥1000, got {stats['cod_total']}"
        assert stats["revenue_total"] >= 1500, f"revenue_total should be ≥1500, got {stats['revenue_total']}"
        assert stats["prepaid_count"] >= 1, f"prepaid_count should be ≥1, got {stats['prepaid_count']}"
        assert stats["cod_count"] >= 1, f"cod_count should be ≥1, got {stats['cod_count']}"

        print(f"✅ Stats aggregation correct:")
        print(f"   Prepaid: {stats['prepaid_count']} shipments, ₹{stats['prepaid_total']}")
        print(f"   COD: {stats['cod_count']} shipments, ₹{stats['cod_total']}")
        print(f"   Revenue total: ₹{stats['revenue_total']}")

    def test_stats_excludes_cancelled_shipments(self, api_client):
        """Cancelled shipments should not be included in revenue calculations"""
        # Get stats before creating shipment
        stats_initial = api_client.get(f"{BASE_URL}/api/shipments/stats").json()
        cod_total_initial = stats_initial["cod_total"]
        
        # Create a COD shipment
        test_payload = {
            "tracking_id": "TEST_ITER3_CANCELLED_002",
            "customer_name": "TEST Cancelled Customer",
            "payment_mode": "COD",
            "amount": 5000,
            "status": "Pending"
        }
        create_response = api_client.post(f"{BASE_URL}/api/shipments", json=test_payload)
        assert create_response.status_code == 200
        shipment_id = create_response.json()["id"]
        
        # Get stats after creation (should include the shipment)
        stats_after_create = api_client.get(f"{BASE_URL}/api/shipments/stats").json()
        cod_total_after_create = stats_after_create["cod_total"]
        assert cod_total_after_create == cod_total_initial + 5000, \
            f"COD total should increase by 5000. Initial: {cod_total_initial}, After create: {cod_total_after_create}"
        
        # Cancel the shipment
        update_response = api_client.put(
            f"{BASE_URL}/api/shipments/{shipment_id}",
            json={"status": "Cancelled"}
        )
        assert update_response.status_code == 200
        
        # Get stats after cancellation (should exclude the cancelled shipment)
        stats_after_cancel = api_client.get(f"{BASE_URL}/api/shipments/stats").json()
        cod_total_after_cancel = stats_after_cancel["cod_total"]
        
        # COD total should return to initial value (cancelled shipment excluded)
        assert cod_total_after_cancel == cod_total_initial, \
            f"Cancelled shipment should be excluded. Initial: {cod_total_initial}, After cancel: {cod_total_after_cancel}"
        
        print(f"✅ Cancelled shipments correctly excluded from stats")
        print(f"   Initial COD total: ₹{cod_total_initial}")
        print(f"   After creating ₹5000 COD: ₹{cod_total_after_create}")
        print(f"   After cancelling: ₹{cod_total_after_cancel} (back to initial)")


@pytest.fixture
def api_client():
    """Shared requests session"""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    return session
