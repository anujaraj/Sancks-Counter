"""
Tests for HR reimbursement workflow:
- Claim creation on purchase completion
- Approve PENDING claim
- Reject PENDING claim with reason
- Invalid transitions (approve approved, reject rejected, etc.)
- Reimbursement confirmation (APPROVED → REIMBURSED)
- Only HR/Admin can review claims
- Receipt validation
- Monthly reports
"""

from decimal import Decimal

import pytest

from app.models import ClaimStatus, EmployeeRole, RefillStatus
from tests.conftest import (
    make_counter,
    make_employee,
    make_hr_employee,
    make_oslo_office,
)


def full_workflow_to_claim(svc, repos):
    """
    Helper: runs the complete workflow up to claim creation.
    Returns (request, claim, hr_id).
    """
    make_oslo_office(repos)
    make_counter(repos, "oslo")
    make_employee(repos, "emp-01", "oslo", is_volunteer=False)
    make_employee(repos, "vol-01", "oslo", is_volunteer=True)
    hr = make_hr_employee(repos, "oslo", "hr-01")

    req = svc.report_empty_counter("oslo", "emp-01")
    svc.accept_refill_request(req.id, "vol-01", Decimal("800"))
    request, claim = svc.complete_purchase(req.id, "vol-01", Decimal("750"), "RCPT-TEST-01")
    return request, claim, hr


class TestClaimCreation:
    def test_claim_created_on_completion(self, svc, app_repos):
        """Completing a purchase should automatically create a PENDING claim."""
        _, claim, _ = full_workflow_to_claim(svc, app_repos)
        assert claim.status == ClaimStatus.PENDING
        assert claim.amount == Decimal("750")
        assert claim.receipt_reference == "RCPT-TEST-01"
        assert claim.external_hr_claim_id is not None
        assert claim.external_hr_claim_id.startswith("HR-")

    def test_claim_links_to_request(self, svc, app_repos):
        """The claim's refill_request_id must match the completed request."""
        request, claim, _ = full_workflow_to_claim(svc, app_repos)
        assert claim.refill_request_id == request.id

    def test_claim_currency_matches_office(self, svc, app_repos):
        """Oslo claims must be in NOK."""
        _, claim, _ = full_workflow_to_claim(svc, app_repos)
        assert claim.currency.value == "NOK"


class TestClaimApproval:
    def test_hr_can_approve_pending_claim(self, svc, app_repos):
        _, claim, hr = full_workflow_to_claim(svc, app_repos)
        approved = svc.approve_claim(claim.id, hr.id)
        assert approved.status == ClaimStatus.APPROVED
        assert approved.reviewed_at is not None

    def test_cannot_approve_already_approved(self, svc, app_repos):
        """Approving twice should raise."""
        _, claim, hr = full_workflow_to_claim(svc, app_repos)
        svc.approve_claim(claim.id, hr.id)

        with pytest.raises(ValueError, match="cannot be approved"):
            svc.approve_claim(claim.id, hr.id)

    def test_non_hr_cannot_approve(self, svc, app_repos):
        """Regular employees must not be able to approve claims."""
        _, claim, _ = full_workflow_to_claim(svc, app_repos)

        with pytest.raises(ValueError, match="Only HR"):
            svc.approve_claim(claim.id, "emp-01")

    def test_cannot_approve_rejected_claim(self, svc, app_repos):
        """A rejected claim cannot be re-approved."""
        _, claim, hr = full_workflow_to_claim(svc, app_repos)
        svc.reject_claim(claim.id, hr.id, "Missing receipt")

        with pytest.raises(ValueError, match="cannot be approved"):
            svc.approve_claim(claim.id, hr.id)


class TestClaimRejection:
    def test_hr_can_reject_pending_claim(self, svc, app_repos):
        _, claim, hr = full_workflow_to_claim(svc, app_repos)
        rejected = svc.reject_claim(claim.id, hr.id, "Receipt is illegible")
        assert rejected.status == ClaimStatus.REJECTED
        assert rejected.rejection_reason == "Receipt is illegible"

    def test_cannot_reject_without_reason(self, svc, app_repos):
        """Rejection must include a reason."""
        _, claim, hr = full_workflow_to_claim(svc, app_repos)

        with pytest.raises(ValueError, match="reason"):
            svc.reject_claim(claim.id, hr.id, "")

    def test_cannot_reject_approved_claim(self, svc, app_repos):
        """An already-approved claim cannot be rejected."""
        _, claim, hr = full_workflow_to_claim(svc, app_repos)
        svc.approve_claim(claim.id, hr.id)

        with pytest.raises(ValueError, match="cannot be rejected"):
            svc.reject_claim(claim.id, hr.id, "Changed my mind")

    def test_non_hr_cannot_reject(self, svc, app_repos):
        _, claim, _ = full_workflow_to_claim(svc, app_repos)

        with pytest.raises(ValueError, match="Only HR"):
            svc.reject_claim(claim.id, "emp-01", "Trying to reject my own claim")


class TestReimbursementConfirmation:
    def test_can_confirm_approved_claim(self, svc, app_repos):
        """APPROVED → REIMBURSED should succeed."""
        _, claim, hr = full_workflow_to_claim(svc, app_repos)
        svc.approve_claim(claim.id, hr.id)
        confirmed = svc.confirm_reimbursed(claim.id, hr.id)
        assert confirmed.status == ClaimStatus.REIMBURSED
        assert confirmed.reimbursed_at is not None

    def test_cannot_reimburse_pending_claim(self, svc, app_repos):
        """Cannot mark PENDING as REIMBURSED — must approve first."""
        _, claim, hr = full_workflow_to_claim(svc, app_repos)

        with pytest.raises(ValueError, match="must be APPROVED"):
            svc.confirm_reimbursed(claim.id, hr.id)

    def test_cannot_reimburse_rejected_claim(self, svc, app_repos):
        _, claim, hr = full_workflow_to_claim(svc, app_repos)
        svc.reject_claim(claim.id, hr.id, "No receipt")

        with pytest.raises(ValueError, match="must be APPROVED"):
            svc.confirm_reimbursed(claim.id, hr.id)

    def test_non_hr_cannot_confirm_reimbursement(self, svc, app_repos):
        _, claim, hr = full_workflow_to_claim(svc, app_repos)
        svc.approve_claim(claim.id, hr.id)

        with pytest.raises(ValueError, match="Only HR"):
            svc.confirm_reimbursed(claim.id, "emp-01")


class TestReceiptValidation:
    def test_empty_receipt_reference_rejected(self, svc, app_repos):
        """An empty receipt reference must be rejected at the service level."""
        make_oslo_office(app_repos)
        make_counter(app_repos, "oslo")
        make_employee(app_repos, "emp-01", "oslo")
        make_employee(app_repos, "vol-01", "oslo", is_volunteer=True)

        req = svc.report_empty_counter("oslo", "emp-01")
        svc.accept_refill_request(req.id, "vol-01", Decimal("500"))

        # Empty string receipt reference
        with pytest.raises((ValueError, Exception)):
            svc.complete_purchase(req.id, "vol-01", Decimal("490"), "")

    def test_wrong_volunteer_cannot_complete(self, svc, app_repos):
        """Only the assigned volunteer can complete the purchase."""
        make_oslo_office(app_repos)
        make_counter(app_repos, "oslo")
        make_employee(app_repos, "emp-01", "oslo")
        make_employee(app_repos, "vol-01", "oslo", is_volunteer=True)
        make_employee(app_repos, "vol-02", "oslo", is_volunteer=True)

        req = svc.report_empty_counter("oslo", "emp-01")
        svc.accept_refill_request(req.id, "vol-01", Decimal("500"))

        with pytest.raises(ValueError, match="not assigned"):
            svc.complete_purchase(req.id, "vol-02", Decimal("490"), "RCPT-999")


class TestMonthlyReport:
    def test_monthly_report_counts_completed_requests(self, svc, app_repos):
        """The monthly report should count completed purchases for the month."""
        from datetime import datetime
        _, claim, _ = full_workflow_to_claim(svc, app_repos)

        now = datetime.utcnow()
        report = svc.get_monthly_report("oslo", now.year, now.month)

        assert report["completed_refills"] == 1
        assert report["claims_pending"] == 1
        assert report["currency"] == "NOK"

    def test_monthly_report_after_approval(self, svc, app_repos):
        """After HR approves, the report should show 1 approved, 0 pending."""
        from datetime import datetime
        _, claim, hr = full_workflow_to_claim(svc, app_repos)
        svc.approve_claim(claim.id, hr.id)

        now = datetime.utcnow()
        report = svc.get_monthly_report("oslo", now.year, now.month)

        assert report["claims_pending"] == 0
        assert report["claims_approved"] == 1

    def test_monthly_report_amounts(self, svc, app_repos):
        """The report's total_spent should match the actual purchase amount."""
        from datetime import datetime
        _, _, _ = full_workflow_to_claim(svc, app_repos)

        now = datetime.utcnow()
        report = svc.get_monthly_report("oslo", now.year, now.month)
        assert report["total_spent"] == "750"  # from full_workflow_to_claim

    def test_monthly_report_budget_fields(self, svc, app_repos):
        """The report should include accurate budget figures."""
        from datetime import datetime
        full_workflow_to_claim(svc, app_repos)

        now = datetime.utcnow()
        report = svc.get_monthly_report("oslo", now.year, now.month)
        assert report["monthly_budget"] == "6000"
        assert report["currency"] == "NOK"
