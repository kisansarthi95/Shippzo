"""
Backend API tests for Courier Label Manager - Iteration 2
Tests: New courier fields (contact info), GET /api/couriers/{id}, Google Sheets integration,
       Shipment enhancements (order_id, items array, amount for both modes), revenue_total stats, CSV export updates
"""
import pytest
import requests
import os
from pathlib import Path

# Read BASE_URL from frontend .env
frontend_env = Path("/app/frontend/.env")
BASE_URL = ""
if frontend_env.exists():
    for line in frontend_env.read_text().splitlines():
        if line.startswith("EXPO_PUBLIC_BACKEND_URL="):
            BASE_URL = line.split("=", 1)[1].strip().rstrip('/')
            break

if not BASE_URL:
    raise ValueError("EXPO_PUBLIC_BACKEND_URL not found in /app/frontend/.env")

@pytest.fixture
def api_client():
    """Shared requests session"""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    return session


class TestCourierEnhancements:
    """Test new courier fields: contact_phone, contact_email, website_url, tracking_url_template, notes"""

    def test_list_couriers_includes_new_fields(self, api_client):
        """GET /api/couriers returns couriers with new contact fields"""
        response = api_client.get(f"{BASE_URL}/api/couriers")
        assert response.status_code == 200
        
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 5, f"Expected at least 5 seeded couriers, got {len(data)}"
        
        # Verify new fields exist
        first = data[0]
        assert "contact_phone" in first
        assert "contact_email" in first
        assert "website_url" in first
        assert "tracking_url_template" in first
        assert "notes" in first
        
        # Verify all couriers have new fields (even if empty)
        for courier in data:
            assert "contact_phone" in courier
            assert "contact_email" in courier
            assert "website_url" in courier
            assert "tracking_url_template" in courier
            assert "notes" in courier

    def test_get_single_courier_by_id(self, api_client):
        """GET /api/couriers/{id} returns single courier with all fields"""
        # Get first courier
        couriers = api_client.get(f"{BASE_URL}/api/couriers").json()
        courier_id = couriers[0]["id"]
        
        response = api_client.get(f"{BASE_URL}/api/couriers/{courier_id}")
        assert response.status_code == 200
        
        data = response.json()
        assert data["id"] == courier_id
        assert "name" in data
        assert "contact_phone" in data
        assert "contact_email" in data
        assert "website_url" in data
        assert "tracking_url_template" in data
        assert "notes" in data

    def test_get_nonexistent_courier_returns_404(self, api_client):
        """GET /api/couriers/{invalid_id} returns 404"""
        response = api_client.get(f"{BASE_URL}/api/couriers/invalid-courier-999")
        assert response.status_code == 404

    def test_create_courier_with_contact_fields(self, api_client):
        """POST /api/couriers with contact fields persists correctly"""
        payload = {
            "name": "TEST_Iteration2_Courier",
            "series_prefix": "IT2",
            "next_number": 1,
            "number_padding": 5,
            "contact_phone": "1800 123 456",
            "contact_email": "support@test.com",
            "website_url": "https://test-courier.com",
            "tracking_url_template": "https://test-courier.com/track?id={tracking_id}",
            "notes": "Test courier for iteration 2"
        }
        
        response = api_client.post(f"{BASE_URL}/api/couriers", json=payload)
        assert response.status_code == 200
        
        data = response.json()
        assert data["name"] == payload["name"]
        assert data["contact_phone"] == payload["contact_phone"]
        assert data["contact_email"] == payload["contact_email"]
        assert data["website_url"] == payload["website_url"]
        assert data["tracking_url_template"] == payload["tracking_url_template"]
        assert data["notes"] == payload["notes"]
        
        # Verify persistence via GET
        courier_id = data["id"]
        get_response = api_client.get(f"{BASE_URL}/api/couriers/{courier_id}")
        assert get_response.status_code == 200
        persisted = get_response.json()
        assert persisted["contact_phone"] == payload["contact_phone"]
        assert persisted["tracking_url_template"] == payload["tracking_url_template"]

    def test_update_courier_contact_fields(self, api_client):
        """PUT /api/couriers/{id} updates contact_phone, tracking_url_template, website_url, notes"""
        # Create test courier
        create_resp = api_client.post(f"{BASE_URL}/api/couriers", json={
            "name": "TEST_Update_Contact",
            "series_prefix": "UC",
            "contact_phone": "1111111111"
        })
        courier_id = create_resp.json()["id"]
        
        # Update contact fields
        update_payload = {
            "contact_phone": "9999999999",
            "contact_email": "updated@test.com",
            "website_url": "https://updated.com",
            "tracking_url_template": "https://updated.com/track/{tracking_id}",
            "notes": "Updated notes"
        }
        response = api_client.put(f"{BASE_URL}/api/couriers/{courier_id}", json=update_payload)
        assert response.status_code == 200
        
        data = response.json()
        assert data["contact_phone"] == "9999999999"
        assert data["contact_email"] == "updated@test.com"
        assert data["website_url"] == "https://updated.com"
        assert data["tracking_url_template"] == "https://updated.com/track/{tracking_id}"
        assert data["notes"] == "Updated notes"
        
        # Verify persistence
        get_response = api_client.get(f"{BASE_URL}/api/couriers/{courier_id}")
        persisted = get_response.json()
        assert persisted["contact_phone"] == "9999999999"
        assert persisted["notes"] == "Updated notes"


class TestGoogleSheetsIntegration:
    """Test Google Sheets integration endpoints"""

    def test_sheets_preview_with_invalid_url_returns_400(self, api_client):
        """POST /api/sheets/preview with invalid URL returns 400 with helpful message"""
        invalid_urls = [
            "not-a-url",
            "https://google.com",
            "https://docs.google.com/document/d/123",
        ]
        
        for url in invalid_urls:
            response = api_client.post(f"{BASE_URL}/api/sheets/preview", json={"url": url})
            assert response.status_code == 400, f"Expected 400 for invalid URL: {url}"
            
            data = response.json()
            assert "detail" in data
            assert "Invalid Google Sheet URL" in data["detail"] or "Paste the full URL" in data["detail"]

    def test_sheets_preview_with_valid_public_url(self, api_client):
        """POST /api/sheets/preview with valid PUBLIC Google Sheet URL returns headers + sample_rows + auto_mapping"""
        # Using official Google Sheets sample (publicly viewable)
        test_url = "https://docs.google.com/spreadsheets/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms/edit#gid=0"
        
        response = api_client.post(f"{BASE_URL}/api/sheets/preview", json={"url": test_url})
        
        # Note: This may fail if the sheet is not accessible or sharing settings changed
        # In that case, we verify error handling works
        if response.status_code == 400:
            data = response.json()
            assert "detail" in data
            # Acceptable error messages
            assert any(msg in data["detail"] for msg in [
                "not public",
                "Could not fetch sheet",
                "Sign in"
            ])
            print(f"Sheet preview failed (expected if sheet not public): {data['detail']}")
        else:
            # If successful, verify response structure
            assert response.status_code == 200
            
            data = response.json()
            assert "sheet_id" in data
            assert "gid" in data
            assert "headers" in data
            assert "sample_rows" in data
            assert "total_rows" in data
            assert "auto_mapping" in data
            
            assert isinstance(data["headers"], list)
            assert isinstance(data["sample_rows"], list)
            assert isinstance(data["auto_mapping"], dict)
            assert data["sheet_id"] == "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms"

    def test_settings_includes_sheet_config(self, api_client):
        """GET /api/settings now includes sheet {url, sheet_id, gid, headers, column_mapping} and copy_template fields"""
        response = api_client.get(f"{BASE_URL}/api/settings")
        assert response.status_code == 200
        
        data = response.json()
        assert "sheet" in data
        assert "copy_template" in data
        
        # Verify sheet structure
        sheet = data["sheet"]
        assert "url" in sheet
        assert "sheet_id" in sheet
        assert "gid" in sheet
        assert "headers" in sheet
        assert "column_mapping" in sheet
        
        # Verify copy_template has placeholders
        copy_tpl = data["copy_template"]
        assert "{customer_name}" in copy_tpl or "{tracking_id}" in copy_tpl

    def test_update_settings_with_sheet_config(self, api_client):
        """PUT /api/settings with sheet payload persists column_mapping"""
        payload = {
            "sheet": {
                "url": "https://docs.google.com/spreadsheets/d/TEST123/edit",
                "sheet_id": "TEST123",
                "gid": "0",
                "tab_name": "Orders",
                "headers": ["Name", "Phone", "Address", "Amount"],
                "column_mapping": {
                    "customer_name": "Name",
                    "phone": "Phone",
                    "address": "Address",
                    "amount": "Amount"
                }
            }
        }
        
        response = api_client.put(f"{BASE_URL}/api/settings", json=payload)
        assert response.status_code == 200
        
        data = response.json()
        assert data["sheet"]["sheet_id"] == "TEST123"
        assert data["sheet"]["column_mapping"]["customer_name"] == "Name"
        
        # Verify persistence
        get_response = api_client.get(f"{BASE_URL}/api/settings")
        persisted = get_response.json()
        assert persisted["sheet"]["sheet_id"] == "TEST123"
        assert persisted["sheet"]["column_mapping"]["phone"] == "Phone"


class TestShipmentEnhancements:
    """Test new shipment fields: order_id, items (array), amount (both prepaid and COD)"""

    def test_create_shipment_with_order_id_and_items(self, api_client):
        """POST /api/shipments now accepts order_id, items (array), amount (prepaid); verify items array persists"""
        couriers = api_client.get(f"{BASE_URL}/api/couriers").json()
        courier = couriers[0]
        
        payload = {
            "tracking_id": "TEST_IT2_001",
            "courier_id": courier["id"],
            "order_id": "ORD-2024-001",
            "customer_name": "Test Customer Iteration 2",
            "customer_phone": "9876543210",
            "address_line1": "123 Test St",
            "city": "Rajkot",
            "state": "Gujarat",
            "pincode": "360001",
            "payment_mode": "Prepaid",
            "amount": 750,
            "items": ["T-Shirt", "Jeans", "Shoes"],
            "weight": "1.2 kg"
        }
        
        response = api_client.post(f"{BASE_URL}/api/shipments", json=payload)
        assert response.status_code == 200
        
        data = response.json()
        assert data["order_id"] == "ORD-2024-001"
        assert data["items"] == ["T-Shirt", "Jeans", "Shoes"]
        assert data["amount"] == 750
        assert data["payment_mode"] == "Prepaid"
        assert data["cod_amount"] == 0  # Prepaid should have cod_amount = 0
        
        # Verify persistence
        shipment_id = data["id"]
        get_response = api_client.get(f"{BASE_URL}/api/shipments/{shipment_id}")
        assert get_response.status_code == 200
        persisted = get_response.json()
        assert persisted["order_id"] == "ORD-2024-001"
        assert persisted["items"] == ["T-Shirt", "Jeans", "Shoes"]
        assert persisted["amount"] == 750

    def test_create_shipment_cod_with_amount(self, api_client):
        """POST /api/shipments with COD mode uses amount field (not only cod_amount)"""
        couriers = api_client.get(f"{BASE_URL}/api/couriers").json()
        courier = couriers[0]
        
        payload = {
            "tracking_id": "TEST_IT2_COD_001",
            "courier_id": courier["id"],
            "order_id": "ORD-COD-001",
            "customer_name": "COD Test Customer",
            "payment_mode": "COD",
            "amount": 1200,
            "items": ["Product A", "Product B"]
        }
        
        response = api_client.post(f"{BASE_URL}/api/shipments", json=payload)
        assert response.status_code == 200
        
        data = response.json()
        assert data["payment_mode"] == "COD"
        assert data["amount"] == 1200
        assert data["cod_amount"] == 1200  # COD should sync amount to cod_amount
        assert data["items"] == ["Product A", "Product B"]

    def test_shipment_items_empty_array_when_not_provided(self, api_client):
        """POST /api/shipments without items field defaults to empty array"""
        couriers = api_client.get(f"{BASE_URL}/api/couriers").json()
        courier = couriers[0]
        
        payload = {
            "tracking_id": "TEST_IT2_NO_ITEMS",
            "courier_id": courier["id"],
            "customer_name": "No Items Test",
            "payment_mode": "Prepaid"
        }
        
        response = api_client.post(f"{BASE_URL}/api/shipments", json=payload)
        assert response.status_code == 200
        
        data = response.json()
        assert "items" in data
        assert isinstance(data["items"], list)
        assert len(data["items"]) == 0


class TestStatsEnhancements:
    """Test stats endpoint now returns revenue_total"""

    def test_stats_includes_revenue_total(self, api_client):
        """GET /api/shipments/stats now returns revenue_total in addition to cod_total"""
        response = api_client.get(f"{BASE_URL}/api/shipments/stats")
        assert response.status_code == 200
        
        data = response.json()
        assert "total" in data
        assert "delivered" in data
        assert "pending" in data
        assert "cod_total" in data
        assert "revenue_total" in data
        
        assert isinstance(data["revenue_total"], (int, float))
        # revenue_total should be >= cod_total (includes prepaid + COD)
        assert data["revenue_total"] >= data["cod_total"]

    def test_revenue_total_calculation(self, api_client):
        """revenue_total should sum all shipment amounts (COD + Prepaid)"""
        # Get initial stats
        initial = api_client.get(f"{BASE_URL}/api/shipments/stats").json()
        initial_revenue = initial["revenue_total"]
        
        # Create Prepaid shipment
        couriers = api_client.get(f"{BASE_URL}/api/couriers").json()
        api_client.post(f"{BASE_URL}/api/shipments", json={
            "tracking_id": "TEST_REVENUE_PREPAID",
            "courier_id": couriers[0]["id"],
            "customer_name": "Revenue Test Prepaid",
            "payment_mode": "Prepaid",
            "amount": 500
        })
        
        # Create COD shipment
        api_client.post(f"{BASE_URL}/api/shipments", json={
            "tracking_id": "TEST_REVENUE_COD",
            "courier_id": couriers[0]["id"],
            "customer_name": "Revenue Test COD",
            "payment_mode": "COD",
            "amount": 300
        })
        
        # Check updated stats
        updated = api_client.get(f"{BASE_URL}/api/shipments/stats").json()
        assert updated["revenue_total"] >= initial_revenue + 800  # 500 + 300


class TestCSVExportEnhancements:
    """Test CSV export includes new columns: Order ID, Items, Amount"""

    def test_csv_export_includes_new_columns(self, api_client):
        """GET /api/shipments/export/csv includes new columns (Order ID, Items, Amount)"""
        # Create shipment with new fields
        couriers = api_client.get(f"{BASE_URL}/api/couriers").json()
        api_client.post(f"{BASE_URL}/api/shipments", json={
            "tracking_id": "TEST_CSV_EXPORT",
            "courier_id": couriers[0]["id"],
            "order_id": "CSV-ORDER-001",
            "customer_name": "CSV Test",
            "payment_mode": "Prepaid",
            "amount": 999,
            "items": ["Item1", "Item2"]
        })
        
        response = api_client.get(f"{BASE_URL}/api/shipments/export/csv")
        assert response.status_code == 200
        assert "text/csv" in response.headers.get("content-type", "")
        
        csv_content = response.text
        lines = csv_content.strip().split("\n")
        assert len(lines) >= 1, "CSV should have at least header row"
        
        # Verify headers include new columns
        header = lines[0]
        assert "Order ID" in header
        assert "Items" in header
        assert "Amount" in header
        
        # Verify data row contains our test shipment
        csv_rows = "\n".join(lines)
        assert "TEST_CSV_EXPORT" in csv_rows
        assert "CSV-ORDER-001" in csv_rows
        assert "999" in csv_rows


class TestSearchEnhancements:
    """Test search now includes order_id"""

    def test_search_by_order_id(self, api_client):
        """GET /api/shipments?search=order_id searches order_id field"""
        # Create shipment with unique order_id
        couriers = api_client.get(f"{BASE_URL}/api/couriers").json()
        unique_order = "SEARCH-ORDER-999"
        api_client.post(f"{BASE_URL}/api/shipments", json={
            "tracking_id": "TEST_SEARCH_ORDER",
            "courier_id": couriers[0]["id"],
            "order_id": unique_order,
            "customer_name": "Search Order Test",
            "payment_mode": "Prepaid"
        })
        
        response = api_client.get(f"{BASE_URL}/api/shipments", params={"search": unique_order})
        assert response.status_code == 200
        
        data = response.json()
        assert len(data) >= 1
        assert any(s["order_id"] == unique_order for s in data)
