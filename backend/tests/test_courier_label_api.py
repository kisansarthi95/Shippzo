"""
Backend API tests for Courier Label Manager
Tests: Couriers, Settings, Shipments, Stats, CSV Export, Tracking Series
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


class TestCouriers:
    """Courier CRUD and tracking series tests"""

    def test_list_couriers_returns_5_seeded(self, api_client):
        """GET /api/couriers should return 5 seeded couriers"""
        response = api_client.get(f"{BASE_URL}/api/couriers")
        assert response.status_code == 200
        
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 5, f"Expected at least 5 seeded couriers, got {len(data)}"
        
        # Verify seeded courier names
        names = [c["name"] for c in data]
        expected = ["Nandan Courier", "DTDC", "ST Courier", "Trackon", "Other"]
        for name in expected:
            assert name in names, f"Expected seeded courier '{name}' not found"
        
        # Verify structure
        first = data[0]
        assert "id" in first
        assert "name" in first
        assert "series_prefix" in first
        assert "next_number" in first
        assert "number_padding" in first
        assert "created_at" in first

    def test_peek_next_tracking(self, api_client):
        """GET /api/couriers/{id}/next-tracking returns predicted tracking_id"""
        # Get first courier
        couriers = api_client.get(f"{BASE_URL}/api/couriers").json()
        courier = couriers[0]
        
        response = api_client.get(f"{BASE_URL}/api/couriers/{courier['id']}/next-tracking")
        assert response.status_code == 200
        
        data = response.json()
        assert "tracking_id" in data
        assert "next_number" in data
        
        # Verify format: prefix + padded number
        expected = f"{courier['series_prefix']}{str(courier['next_number']).zfill(courier['number_padding'])}"
        assert data["tracking_id"] == expected

    def test_consume_tracking_increments(self, api_client):
        """POST /api/couriers/{id}/consume-tracking increments next_number"""
        # Get courier
        couriers = api_client.get(f"{BASE_URL}/api/couriers").json()
        courier = couriers[0]
        initial_next = courier["next_number"]
        
        # Consume tracking
        response = api_client.post(f"{BASE_URL}/api/couriers/{courier['id']}/consume-tracking")
        assert response.status_code == 200
        
        data = response.json()
        assert "tracking_id" in data
        
        # Verify next_number incremented
        updated = api_client.get(f"{BASE_URL}/api/couriers/{courier['id']}/next-tracking").json()
        assert updated["next_number"] == initial_next + 1

    def test_create_courier(self, api_client):
        """POST /api/couriers creates custom courier"""
        payload = {
            "name": "TEST_Speed Post",
            "series_prefix": "SP",
            "next_number": 100,
            "number_padding": 4
        }
        
        response = api_client.post(f"{BASE_URL}/api/couriers", json=payload)
        assert response.status_code == 200
        
        data = response.json()
        assert data["name"] == payload["name"]
        assert data["series_prefix"] == payload["series_prefix"]
        assert data["next_number"] == payload["next_number"]
        assert "id" in data
        
        # Verify persistence
        courier_id = data["id"]
        get_response = api_client.get(f"{BASE_URL}/api/couriers")
        all_couriers = get_response.json()
        assert any(c["id"] == courier_id for c in all_couriers)

    def test_update_courier(self, api_client):
        """PUT /api/couriers/{id} updates courier fields"""
        # Create test courier
        create_resp = api_client.post(f"{BASE_URL}/api/couriers", json={
            "name": "TEST_Update Courier",
            "series_prefix": "UC",
            "next_number": 1
        })
        courier_id = create_resp.json()["id"]
        
        # Update
        update_payload = {"next_number": 500}
        response = api_client.put(f"{BASE_URL}/api/couriers/{courier_id}", json=update_payload)
        assert response.status_code == 200
        
        data = response.json()
        assert data["next_number"] == 500
        
        # Verify persistence
        peek = api_client.get(f"{BASE_URL}/api/couriers/{courier_id}/next-tracking").json()
        assert peek["next_number"] == 500

    def test_delete_courier(self, api_client):
        """DELETE /api/couriers/{id} removes courier"""
        # Create test courier
        create_resp = api_client.post(f"{BASE_URL}/api/couriers", json={
            "name": "TEST_Delete Courier",
            "series_prefix": "DC"
        })
        courier_id = create_resp.json()["id"]
        
        # Delete
        response = api_client.delete(f"{BASE_URL}/api/couriers/{courier_id}")
        assert response.status_code == 200
        
        # Verify deleted
        get_response = api_client.get(f"{BASE_URL}/api/couriers")
        all_couriers = get_response.json()
        assert not any(c["id"] == courier_id for c in all_couriers)


class TestSettings:
    """Settings CRUD tests"""

    def test_get_settings_returns_defaults(self, api_client):
        """GET /api/settings returns default settings with WhatsApp template"""
        response = api_client.get(f"{BASE_URL}/api/settings")
        assert response.status_code == 200
        
        data = response.json()
        assert "id" in data
        assert data["id"] == "default"
        assert "sender" in data
        assert "whatsapp_template" in data
        assert "default_eta_days" in data
        
        # Verify sender structure
        sender = data["sender"]
        assert "name" in sender
        assert "phone" in sender
        assert "address_line1" in sender
        assert "show_contact" in sender
        
        # Verify WhatsApp template has placeholders
        template = data["whatsapp_template"]
        assert "{customer_name}" in template or "{tracking_id}" in template

    def test_update_settings(self, api_client):
        """PUT /api/settings updates sender address and template"""
        payload = {
            "sender": {
                "name": "TEST_Shop Name",
                "phone": "9876543210",
                "address_line1": "123 Test Street",
                "address_line2": "Test Area",
                "city": "Rajkot",
                "state": "Gujarat",
                "pincode": "360001",
                "show_contact": True
            },
            "whatsapp_template": "TEST: {customer_name} - {tracking_id}",
            "default_eta_days": 5
        }
        
        response = api_client.put(f"{BASE_URL}/api/settings", json=payload)
        assert response.status_code == 200
        
        data = response.json()
        assert data["sender"]["name"] == payload["sender"]["name"]
        assert data["whatsapp_template"] == payload["whatsapp_template"]
        assert data["default_eta_days"] == 5
        
        # Verify persistence
        get_response = api_client.get(f"{BASE_URL}/api/settings")
        persisted = get_response.json()
        assert persisted["sender"]["name"] == payload["sender"]["name"]


class TestShipments:
    """Shipment CRUD tests"""

    def test_create_shipment(self, api_client):
        """POST /api/shipments creates shipment with all fields"""
        # Get a courier
        couriers = api_client.get(f"{BASE_URL}/api/couriers").json()
        courier = couriers[0]
        
        payload = {
            "tracking_id": "TEST_ND00001",
            "courier_id": courier["id"],
            "customer_name": "Test Customer",
            "customer_phone": "9876543210",
            "address_line1": "Test Street",
            "address_line2": "Test Area",
            "city": "Rajkot",
            "state": "Gujarat",
            "pincode": "360001",
            "payment_mode": "COD",
            "cod_amount": 500,
            "weight": "0.5 kg",
            "item_description": "Clothes"
        }
        
        response = api_client.post(f"{BASE_URL}/api/shipments", json=payload)
        assert response.status_code == 200
        
        data = response.json()
        assert data["tracking_id"] == payload["tracking_id"]
        assert data["customer_name"] == payload["customer_name"]
        assert data["payment_mode"] == "COD"
        assert data["cod_amount"] == 500
        assert data["status"] == "Pending"
        assert "id" in data
        assert "created_at" in data
        
        # Verify courier_name resolved
        assert data["courier_name"] == courier["name"]
        
        # Verify persistence
        shipment_id = data["id"]
        get_response = api_client.get(f"{BASE_URL}/api/shipments/{shipment_id}")
        assert get_response.status_code == 200
        persisted = get_response.json()
        assert persisted["tracking_id"] == payload["tracking_id"]

    def test_list_shipments(self, api_client):
        """GET /api/shipments lists all shipments"""
        response = api_client.get(f"{BASE_URL}/api/shipments")
        assert response.status_code == 200
        
        data = response.json()
        assert isinstance(data, list)

    def test_list_shipments_with_status_filter(self, api_client):
        """GET /api/shipments?status=Pending filters by status"""
        # Create a pending shipment
        couriers = api_client.get(f"{BASE_URL}/api/couriers").json()
        api_client.post(f"{BASE_URL}/api/shipments", json={
            "tracking_id": "TEST_PENDING001",
            "courier_id": couriers[0]["id"],
            "customer_name": "Pending Test",
            "payment_mode": "Prepaid"
        })
        
        response = api_client.get(f"{BASE_URL}/api/shipments", params={"status": "Pending"})
        assert response.status_code == 200
        
        data = response.json()
        for shipment in data:
            assert shipment["status"] == "Pending"

    def test_list_shipments_with_search(self, api_client):
        """GET /api/shipments?search=query searches tracking/name/city"""
        # Create shipment with unique tracking
        couriers = api_client.get(f"{BASE_URL}/api/couriers").json()
        unique_tracking = "TEST_SEARCH999"
        api_client.post(f"{BASE_URL}/api/shipments", json={
            "tracking_id": unique_tracking,
            "courier_id": couriers[0]["id"],
            "customer_name": "Search Test User",
            "payment_mode": "Prepaid"
        })
        
        response = api_client.get(f"{BASE_URL}/api/shipments", params={"search": unique_tracking})
        assert response.status_code == 200
        
        data = response.json()
        assert len(data) >= 1
        assert any(s["tracking_id"] == unique_tracking for s in data)

    def test_update_shipment_to_delivered(self, api_client):
        """PUT /api/shipments/{id} updates status to Delivered and sets delivered_at"""
        # Create shipment
        couriers = api_client.get(f"{BASE_URL}/api/couriers").json()
        create_resp = api_client.post(f"{BASE_URL}/api/shipments", json={
            "tracking_id": "TEST_DELIVER001",
            "courier_id": couriers[0]["id"],
            "customer_name": "Deliver Test",
            "payment_mode": "Prepaid"
        })
        shipment_id = create_resp.json()["id"]
        
        # Update to delivered
        response = api_client.put(f"{BASE_URL}/api/shipments/{shipment_id}", json={
            "status": "Delivered"
        })
        assert response.status_code == 200
        
        data = response.json()
        assert data["status"] == "Delivered"
        assert data["delivered_at"] is not None
        
        # Verify persistence
        get_response = api_client.get(f"{BASE_URL}/api/shipments/{shipment_id}")
        persisted = get_response.json()
        assert persisted["status"] == "Delivered"
        assert persisted["delivered_at"] is not None

    def test_delete_shipment(self, api_client):
        """DELETE /api/shipments/{id} removes shipment"""
        # Create shipment
        couriers = api_client.get(f"{BASE_URL}/api/couriers").json()
        create_resp = api_client.post(f"{BASE_URL}/api/shipments", json={
            "tracking_id": "TEST_DELETE001",
            "courier_id": couriers[0]["id"],
            "customer_name": "Delete Test",
            "payment_mode": "Prepaid"
        })
        shipment_id = create_resp.json()["id"]
        
        # Delete
        response = api_client.delete(f"{BASE_URL}/api/shipments/{shipment_id}")
        assert response.status_code == 200
        
        # Verify deleted
        get_response = api_client.get(f"{BASE_URL}/api/shipments/{shipment_id}")
        assert get_response.status_code == 404


class TestStats:
    """Shipment statistics tests"""

    def test_get_stats(self, api_client):
        """GET /api/shipments/stats returns total, delivered, pending, cod_total"""
        response = api_client.get(f"{BASE_URL}/api/shipments/stats")
        assert response.status_code == 200
        
        data = response.json()
        assert "total" in data
        assert "delivered" in data
        assert "pending" in data
        assert "cod_total" in data
        
        assert isinstance(data["total"], int)
        assert isinstance(data["delivered"], int)
        assert isinstance(data["pending"], int)
        assert isinstance(data["cod_total"], (int, float))

    def test_stats_cod_total_calculation(self, api_client):
        """Stats should sum COD amounts correctly"""
        # Get initial stats
        initial = api_client.get(f"{BASE_URL}/api/shipments/stats").json()
        initial_cod = initial["cod_total"]
        
        # Create COD shipment
        couriers = api_client.get(f"{BASE_URL}/api/couriers").json()
        api_client.post(f"{BASE_URL}/api/shipments", json={
            "tracking_id": "TEST_COD_STAT001",
            "courier_id": couriers[0]["id"],
            "customer_name": "COD Stats Test",
            "payment_mode": "COD",
            "cod_amount": 1000
        })
        
        # Check updated stats
        updated = api_client.get(f"{BASE_URL}/api/shipments/stats").json()
        assert updated["cod_total"] >= initial_cod + 1000


class TestCSVExport:
    """CSV export tests"""

    def test_export_csv_returns_csv(self, api_client):
        """GET /api/shipments/export/csv returns CSV with proper headers"""
        response = api_client.get(f"{BASE_URL}/api/shipments/export/csv")
        assert response.status_code == 200
        assert "text/csv" in response.headers.get("content-type", "")
        
        csv_content = response.text
        lines = csv_content.strip().split("\n")
        assert len(lines) >= 1, "CSV should have at least header row"
        
        # Verify headers
        header = lines[0]
        expected_headers = [
            "Tracking ID", "Courier", "Customer", "Phone",
            "Address Line 1", "Address Line 2", "City", "State", "Pincode",
            "Payment Mode", "COD Amount", "Weight", "Item",
            "Status", "Created At", "Delivered At"
        ]
        for h in expected_headers:
            assert h in header, f"Expected header '{h}' not found in CSV"


class TestErrorHandling:
    """Error handling tests"""

    def test_get_nonexistent_courier_returns_404(self, api_client):
        """GET /api/couriers/{invalid_id}/next-tracking returns 404"""
        response = api_client.get(f"{BASE_URL}/api/couriers/invalid-id-999/next-tracking")
        assert response.status_code == 404

    def test_get_nonexistent_shipment_returns_404(self, api_client):
        """GET /api/shipments/{invalid_id} returns 404"""
        response = api_client.get(f"{BASE_URL}/api/shipments/invalid-id-999")
        assert response.status_code == 404

    def test_update_nonexistent_courier_returns_404(self, api_client):
        """PUT /api/couriers/{invalid_id} returns 404"""
        response = api_client.put(f"{BASE_URL}/api/couriers/invalid-id-999", json={"next_number": 100})
        assert response.status_code == 404

    def test_delete_nonexistent_courier_returns_404(self, api_client):
        """DELETE /api/couriers/{invalid_id} returns 404"""
        response = api_client.delete(f"{BASE_URL}/api/couriers/invalid-id-999")
        assert response.status_code == 404
