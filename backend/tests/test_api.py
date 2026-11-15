"""
API endpoint tests using FastAPI TestClient.

These tests verify that:
- HTTP status codes are correct
- Response shapes match the schemas
- Error messages are returned properly
- Routes correctly delegate to services

We override the FastAPI dependency to use isolated repos for each test.
This avoids touching the global singleton repos.

Why test the API separately from services?
- Service tests verify business logic.
- API tests verify HTTP contract: correct status codes, response shapes, error handling.
- Together they give full confidence end-to-end.
"""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.integrations import MockChatService, MockHRService
from app.repositories import AppRepositories
from app.routes import get_service, router
from app.services import SnackService
from tests.conftest import (
    make_counter,
    make_employee,
    make_hr_employee,
    make_oslo_office,
)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


def make_test_app(repos: AppRepositories, chat: MockChatService, hr: MockHRService):
    """
    Create a fresh FastAPI test app with isolated dependencies.
    
    We build a minimal app (no seed data) and override the get_service
    dependency to use test-local repos/mocks.
    """
    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")

    svc = SnackService(repos, chat, hr)
    test_app.dependency_overrides[get_service] = lambda: svc
    return test_app


@pytest.fixture
def client(app_repos, chat, hr):
    """TestClient with isolated dependencies."""
    test_app = make_test_app(app_repos, chat, hr)
    return TestClient(test_app)


@pytest.fixture
def oslo_client(app_repos, chat, hr):
    """TestClient with Oslo office, counter, and employees pre-seeded."""
    make_oslo_office(app_repos)
    make_counter(app_repos, "oslo")
    make_employee(app_repos, "emp-01", "oslo", is_volunteer=False)
    make_employee(app_repos, "vol-01", "oslo", is_volunteer=True)
    make_hr_employee(app_repos, "oslo", "hr-01")

    test_app = make_test_app(app_repos, chat, hr)
    return TestClient(test_app)


# ── Health ────────────────────────────────────────────────────────────────────

class TestHealthEndpoint:
    def test_health_returns_200(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


# ── Offices ───────────────────────────────────────────────────────────────────

class TestOfficeEndpoints:
    def test_list_offices_empty(self, client):
        resp = client.get("/api/offices")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_offices_with_data(self, oslo_client):
        resp = oslo_client.get("/api/offices")
        assert resp.status_code == 200
        offices = resp.json()
        assert len(offices) == 1
        assert offices[0]["id"] == "oslo"
        assert offices[0]["currency"] == "NOK"

    def test_get_office_not_found(self, client):
        resp = client.get("/api/offices/nonexistent")
        assert resp.status_code == 404

    def test_get_office_exists(self, oslo_client):
        resp = oslo_client.get("/api/offices/oslo")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Oslo"


# ── Dashboard ─────────────────────────────────────────────────────────────────

class TestDashboardEndpoint:
    def test_dashboard_returns_all_fields(self, oslo_client):
        resp = oslo_client.get("/api/offices/oslo/dashboard")
        assert resp.status_code == 200
        data = resp.json()
        assert "office" in data
        assert "counter" in data
        assert "active_requests" in data
        assert "budget" in data
        assert "volunteers" in data

    def test_dashboard_budget_in_nok(self, oslo_client):
        resp = oslo_client.get("/api/offices/oslo/dashboard")
        budget = resp.json()["budget"]
        assert budget["currency"] == "NOK"
        assert budget["monthly_budget"] == "6000"


# ── Report empty ──────────────────────────────────────────────────────────────

class TestReportEmptyEndpoint:
    def test_report_empty_creates_request(self, oslo_client):
        resp = oslo_client.post(
            "/api/offices/oslo/report-empty",
            json={"reported_by": "emp-01"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "OPEN"
        assert data["office_id"] == "oslo"

    def test_duplicate_report_returns_409(self, oslo_client):
        oslo_client.post("/api/offices/oslo/report-empty", json={"reported_by": "emp-01"})
        resp = oslo_client.post(
            "/api/offices/oslo/report-empty",
            json={"reported_by": "emp-01"},
        )
        assert resp.status_code == 409
        assert "already active" in resp.json()["detail"]

    def test_unknown_employee_returns_400(self, oslo_client):
        resp = oslo_client.post(
            "/api/offices/oslo/report-empty",
            json={"reported_by": "nobody"},
        )
        assert resp.status_code == 400


# ── Accept request ────────────────────────────────────────────────────────────

class TestAcceptRequestEndpoint:
    def test_accept_returns_assigned(self, oslo_client):
        # First create a request
        create_resp = oslo_client.post(
            "/api/offices/oslo/report-empty", json={"reported_by": "emp-01"}
        )
        req_id = create_resp.json()["id"]

        resp = oslo_client.post(
            f"/api/requests/{req_id}/accept",
            json={"volunteer_id": "vol-01", "estimated_amount": "500"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ASSIGNED"
        assert resp.json()["assigned_to"] == "vol-01"

    def test_accept_twice_returns_409(self, oslo_client, app_repos):
        make_employee(app_repos, "vol-02", "oslo", is_volunteer=True)
        
        create_resp = oslo_client.post(
            "/api/offices/oslo/report-empty", json={"reported_by": "emp-01"}
        )
        req_id = create_resp.json()["id"]
        oslo_client.post(
            f"/api/requests/{req_id}/accept",
            json={"volunteer_id": "vol-01", "estimated_amount": "500"},
        )

        resp = oslo_client.post(
            f"/api/requests/{req_id}/accept",
            json={"volunteer_id": "vol-02", "estimated_amount": "500"},
        )
        assert resp.status_code == 409

    def test_accept_with_zero_amount_returns_400(self, oslo_client):
        create_resp = oslo_client.post(
            "/api/offices/oslo/report-empty", json={"reported_by": "emp-01"}
        )
        req_id = create_resp.json()["id"]

        resp = oslo_client.post(
            f"/api/requests/{req_id}/accept",
            json={"volunteer_id": "vol-01", "estimated_amount": "0"},
        )
        assert resp.status_code == 422  # Pydantic validation


# ── Complete purchase ─────────────────────────────────────────────────────────

class TestCompletePurchaseEndpoint:
    def _create_and_accept(self, client):
        create = client.post(
            "/api/offices/oslo/report-empty", json={"reported_by": "emp-01"}
        )
        req_id = create.json()["id"]
        client.post(
            f"/api/requests/{req_id}/accept",
            json={"volunteer_id": "vol-01", "estimated_amount": "800"},
        )
        return req_id

    def test_complete_purchase_success(self, oslo_client):
        req_id = self._create_and_accept(oslo_client)
        resp = oslo_client.post(
            f"/api/requests/{req_id}/complete",
            json={
                "volunteer_id": "vol-01",
                "purchase_amount": "750",
                "receipt_reference": "RCPT-API-01",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["request"]["status"] == "COMPLETED"
        assert data["claim"]["status"] == "PENDING"
        assert data["claim"]["external_hr_claim_id"].startswith("HR-")

    def test_counter_filled_after_complete(self, oslo_client):
        req_id = self._create_and_accept(oslo_client)
        oslo_client.post(
            f"/api/requests/{req_id}/complete",
            json={"volunteer_id": "vol-01", "purchase_amount": "750", "receipt_reference": "RCPT-002"},
        )
        counter = oslo_client.get("/api/offices/oslo/counter").json()
        assert counter["status"] == "FILLED"


# ── Claims ────────────────────────────────────────────────────────────────────

class TestClaimsEndpoints:
    def _full_workflow(self, client):
        create = client.post(
            "/api/offices/oslo/report-empty", json={"reported_by": "emp-01"}
        )
        req_id = create.json()["id"]
        client.post(
            f"/api/requests/{req_id}/accept",
            json={"volunteer_id": "vol-01", "estimated_amount": "800"},
        )
        complete = client.post(
            f"/api/requests/{req_id}/complete",
            json={"volunteer_id": "vol-01", "purchase_amount": "750", "receipt_reference": "RCPT-003"},
        )
        return complete.json()["claim"]["id"]

    def test_list_claims_for_office(self, oslo_client):
        self._full_workflow(oslo_client)
        resp = oslo_client.get("/api/offices/oslo/claims")
        assert resp.status_code == 200
        claims = resp.json()
        assert len(claims) == 1
        assert claims[0]["status"] == "PENDING"

    def test_approve_claim(self, oslo_client):
        claim_id = self._full_workflow(oslo_client)
        resp = oslo_client.post(
            f"/api/claims/{claim_id}/approve",
            json={"reviewer_id": "hr-01"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "APPROVED"

    def test_reject_claim(self, oslo_client):
        claim_id = self._full_workflow(oslo_client)
        resp = oslo_client.post(
            f"/api/claims/{claim_id}/reject",
            json={"reviewer_id": "hr-01", "reason": "Missing receipt"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "REJECTED"

    def test_reject_without_reason_returns_400(self, oslo_client):
        claim_id = self._full_workflow(oslo_client)
        resp = oslo_client.post(
            f"/api/claims/{claim_id}/reject",
            json={"reviewer_id": "hr-01"},
        )
        assert resp.status_code == 400

    def test_reimburse_after_approve(self, oslo_client):
        claim_id = self._full_workflow(oslo_client)
        oslo_client.post(f"/api/claims/{claim_id}/approve", json={"reviewer_id": "hr-01"})
        resp = oslo_client.post(
            f"/api/claims/{claim_id}/reimburse",
            json={"confirmer_id": "hr-01"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "REIMBURSED"

    def test_reimburse_pending_returns_400(self, oslo_client):
        claim_id = self._full_workflow(oslo_client)
        resp = oslo_client.post(
            f"/api/claims/{claim_id}/reimburse",
            json={"confirmer_id": "hr-01"},
        )
        assert resp.status_code == 400


# ── Budget ────────────────────────────────────────────────────────────────────

class TestBudgetEndpoint:
    def test_budget_initial_state(self, oslo_client):
        resp = oslo_client.get("/api/offices/oslo/budget")
        assert resp.status_code == 200
        data = resp.json()
        assert data["monthly_budget"] == "6000"
        assert data["actual_spent"] == "0"
        assert data["active_reservations"] == "0"
        assert data["available"] == "6000"
        assert data["currency"] == "NOK"


# ── Monthly report ────────────────────────────────────────────────────────────

class TestMonthlyReportEndpoint:
    def test_monthly_report_returns_data(self, oslo_client):
        from datetime import datetime
        now = datetime.utcnow()
        resp = oslo_client.get(
            f"/api/offices/oslo/report?year={now.year}&month={now.month}"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["currency"] == "NOK"
        assert data["office_name"] == "Oslo"

        # Also verify the combined management monthly report endpoint
        combined_resp = oslo_client.get(
            f"/api/reports/monthly?year={now.year}&month={now.month}"
        )
        assert combined_resp.status_code == 200
        combined_data = combined_resp.json()
        assert "offices" in combined_data
        assert "oslo" in combined_data["offices"]
        assert combined_data["totals_by_currency"]["NOK"] == "0"
