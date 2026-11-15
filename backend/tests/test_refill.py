"""
Tests for refill request workflow:
- Duplicate request prevention
- Volunteer eligibility
- Concurrent acceptance (single winner)
- Fairness rotation
- Counter status updates
"""

import threading
import time
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app.models import CounterStatus, RefillStatus
from tests.conftest import (
    make_blr_office,
    make_counter,
    make_employee,
    make_oslo_office,
)


# ── Setup helpers ──────────────────────────────────────────────────────────────

def setup_oslo(repos):
    """Minimal Oslo office with counter and one employee."""
    make_oslo_office(repos)
    make_counter(repos, "oslo")
    reporter = make_employee(repos, "emp-01", "oslo", is_volunteer=False)
    return reporter


def setup_with_volunteer(repos, vol_id="vol-01"):
    """Oslo office with a reporter and one volunteer."""
    setup_oslo(repos)
    vol = make_employee(repos, vol_id, "oslo", is_volunteer=True)
    return vol


# ── Duplicate prevention ───────────────────────────────────────────────────────

class TestDuplicatePrevention:
    def test_first_report_succeeds(self, svc, app_repos):
        setup_oslo(app_repos)
        req = svc.report_empty_counter("oslo", "emp-01")
        assert req.status == RefillStatus.OPEN

    def test_duplicate_report_raises(self, svc, app_repos):
        """Reporting empty twice should raise, not create two requests."""
        setup_oslo(app_repos)
        svc.report_empty_counter("oslo", "emp-01")

        with pytest.raises(ValueError, match="already active"):
            svc.report_empty_counter("oslo", "emp-01")

    def test_duplicate_after_assign_still_blocked(self, svc, app_repos):
        """Even when a request is ASSIGNED, another report should be blocked."""
        setup_with_volunteer(app_repos)
        req = svc.report_empty_counter("oslo", "emp-01")
        svc.accept_refill_request(req.id, "vol-01", Decimal("500"))

        with pytest.raises(ValueError, match="already active"):
            svc.report_empty_counter("oslo", "emp-01")

    def test_can_report_after_completion(self, svc, app_repos):
        """After a completed request, a new report should succeed."""
        setup_with_volunteer(app_repos)
        req = svc.report_empty_counter("oslo", "emp-01")
        svc.accept_refill_request(req.id, "vol-01", Decimal("500"))
        svc.complete_purchase(req.id, "vol-01", Decimal("490"), "RCPT-001")

        # Should succeed since previous request is COMPLETED
        new_req = svc.report_empty_counter("oslo", "emp-01")
        assert new_req.status == RefillStatus.OPEN
        assert new_req.id != req.id

    def test_counter_marked_empty_on_report(self, svc, app_repos):
        """Reporting empty should set the counter status to EMPTY."""
        setup_oslo(app_repos)
        svc.report_empty_counter("oslo", "emp-01")
        counter = svc.get_counter_for_office("oslo")
        assert counter.status == CounterStatus.EMPTY

    def test_counter_marked_filled_on_completion(self, svc, app_repos):
        """Completing a purchase should set the counter back to FILLED."""
        setup_with_volunteer(app_repos)
        req = svc.report_empty_counter("oslo", "emp-01")
        svc.accept_refill_request(req.id, "vol-01", Decimal("500"))
        svc.complete_purchase(req.id, "vol-01", Decimal("490"), "RCPT-001")
        counter = svc.get_counter_for_office("oslo")
        assert counter.status == CounterStatus.FILLED


# ── Volunteer eligibility ──────────────────────────────────────────────────────

class TestVolunteerEligibility:
    def test_non_volunteer_cannot_accept(self, svc, app_repos):
        """An employee without is_volunteer=True should not be able to accept."""
        setup_oslo(app_repos)
        req = svc.report_empty_counter("oslo", "emp-01")

        with pytest.raises(ValueError, match="not a registered volunteer"):
            svc.accept_refill_request(req.id, "emp-01", Decimal("500"))

    def test_volunteer_from_wrong_office_cannot_accept(self, svc, app_repos):
        """A volunteer from Bengaluru cannot accept an Oslo request."""
        make_oslo_office(app_repos)
        make_counter(app_repos, "oslo")
        make_blr_office(app_repos)
        make_counter(app_repos, "bengaluru", "counter-blr")

        make_employee(app_repos, "oslo-emp", "oslo", is_volunteer=False)
        make_employee(app_repos, "blr-vol", "bengaluru", is_volunteer=True)

        req = svc.report_empty_counter("oslo", "oslo-emp")

        with pytest.raises(ValueError, match="different office"):
            svc.accept_refill_request(req.id, "blr-vol", Decimal("500"))

    def test_volunteer_can_accept(self, svc, app_repos):
        setup_with_volunteer(app_repos)
        req = svc.report_empty_counter("oslo", "emp-01")
        assigned = svc.accept_refill_request(req.id, "vol-01", Decimal("500"))
        assert assigned.status == RefillStatus.ASSIGNED
        assert assigned.assigned_to == "vol-01"


# ── Concurrent acceptance ──────────────────────────────────────────────────────

class TestConcurrentAcceptance:
    def test_only_one_volunteer_wins(self, svc, app_repos):
        """
        Simulate two volunteers accepting simultaneously.
        Exactly one should succeed; the other gets a ValueError.
        
        How the test works:
        - We create two volunteers and one open request.
        - Two threads call accept_refill_request at the same time.
        - The threading.Lock in the repos ensures only one wins.
        - We collect results and assert exactly one success.
        """
        make_oslo_office(app_repos)
        make_counter(app_repos, "oslo")
        make_employee(app_repos, "emp-01", "oslo", is_volunteer=False)
        vol1 = make_employee(app_repos, "vol-01", "oslo", is_volunteer=True)
        vol2 = make_employee(app_repos, "vol-02", "oslo", is_volunteer=True)

        req = svc.report_empty_counter("oslo", "emp-01")

        successes = []
        errors = []

        def try_accept(volunteer_id):
            try:
                result = svc.accept_refill_request(req.id, volunteer_id, Decimal("500"))
                successes.append(volunteer_id)
            except ValueError as e:
                errors.append((volunteer_id, str(e)))

        t1 = threading.Thread(target=try_accept, args=("vol-01",))
        t2 = threading.Thread(target=try_accept, args=("vol-02",))

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert len(successes) == 1, f"Expected exactly 1 success, got {successes}"
        assert len(errors) == 1, f"Expected exactly 1 error, got {errors}"
        # Verify the final state is ASSIGNED to the winner
        final = svc.repos.refill_requests.get(req.id)
        assert final.status == RefillStatus.ASSIGNED
        assert final.assigned_to == successes[0]

    def test_concurrent_reports_only_one_created(self, svc, app_repos):
        """
        Two employees reporting simultaneously should produce exactly one request.
        """
        make_oslo_office(app_repos)
        make_counter(app_repos, "oslo")
        make_employee(app_repos, "emp-01", "oslo")
        make_employee(app_repos, "emp-02", "oslo")

        successes = []
        errors = []

        def try_report(emp_id):
            try:
                r = svc.report_empty_counter("oslo", emp_id)
                successes.append(r.id)
            except ValueError:
                errors.append(emp_id)

        t1 = threading.Thread(target=try_report, args=("emp-01",))
        t2 = threading.Thread(target=try_report, args=("emp-02",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert len(successes) == 1, f"Expected 1 success, got {successes}"
        total_requests = len(svc.repos.refill_requests.list_all())
        assert total_requests == 1


# ── Fairness rotation ──────────────────────────────────────────────────────────

class TestFairnessRotation:
    def test_never_assigned_preferred(self, svc, app_repos):
        """
        Volunteer who has never been assigned (last_assigned_at=None)
        should appear first in the eligible list.
        """
        make_oslo_office(app_repos)
        # vol-A was assigned recently
        make_employee(
            app_repos, "vol-A", "oslo", is_volunteer=True,
            last_assigned_at=datetime.utcnow() - timedelta(days=1)
        )
        # vol-B was never assigned
        make_employee(app_repos, "vol-B", "oslo", is_volunteer=True, last_assigned_at=None)

        eligible = svc._get_eligible_volunteers("oslo")
        assert eligible[0].id == "vol-B", "Never-assigned volunteer should come first"

    def test_least_recently_assigned_preferred(self, svc, app_repos):
        """
        Among volunteers who have been assigned before, the one assigned
        LONGEST AGO should come first.
        """
        make_oslo_office(app_repos)
        make_employee(
            app_repos, "vol-recent", "oslo", is_volunteer=True,
            last_assigned_at=datetime.utcnow() - timedelta(days=2)
        )
        make_employee(
            app_repos, "vol-old", "oslo", is_volunteer=True,
            last_assigned_at=datetime.utcnow() - timedelta(days=10)
        )

        eligible = svc._get_eligible_volunteers("oslo")
        assert eligible[0].id == "vol-old", "Longest-ago-assigned should be first"

    def test_last_assigned_at_updated_on_accept(self, svc, app_repos):
        """
        After accepting a request, the volunteer's last_assigned_at should be updated.
        """
        setup_with_volunteer(app_repos)
        before = datetime.utcnow()
        req = svc.report_empty_counter("oslo", "emp-01")
        svc.accept_refill_request(req.id, "vol-01", Decimal("500"))
        after = datetime.utcnow()

        vol = app_repos.employees.get("vol-01")
        assert vol.last_assigned_at is not None
        assert before <= vol.last_assigned_at <= after

    def test_reject_request_assigns_next_in_fairness_queue(self, svc, app_repos):
        """
        When a volunteer rejects or hands over an uncompleted request:
        - It releases any active budget reservation.
        - It assigns the request to the next volunteer in the fairness queue.
        - The original volunteer cannot re-accept or complete the assignment after handoff.
        """
        make_oslo_office(app_repos)
        make_counter(app_repos, "oslo")
        make_employee(app_repos, "emp-01", "oslo", is_volunteer=False)
        # Queue order: vol-never (None) -> vol-old (10d ago) -> vol-recent (2d ago)
        make_employee(app_repos, "vol-never", "oslo", is_volunteer=True, last_assigned_at=None)
        make_employee(
            app_repos, "vol-old", "oslo", is_volunteer=True,
            last_assigned_at=datetime.utcnow() - timedelta(days=10),
        )
        make_employee(
            app_repos, "vol-recent", "oslo", is_volunteer=True,
            last_assigned_at=datetime.utcnow() - timedelta(days=2),
        )

        req = svc.report_empty_counter("oslo", "emp-01")
        # 1. First volunteer (vol-never) rejects while OPEN -> assigns to vol-old
        updated = svc.reject_refill_request(req.id, "vol-never")
        assert updated.assigned_to == "vol-old"
        assert "vol-never" in updated.declined_by

        # 2. vol-old accepts the request (reserving 800 NOK), then cannot complete it and hands it off
        svc.accept_refill_request(req.id, "vol-old", Decimal("800"))
        assert svc.get_budget_snapshot("oslo").active_reservations == Decimal("800")

        updated2 = svc.reject_refill_request(req.id, "vol-old")
        assert updated2.assigned_to == "vol-recent"
        assert "vol-old" in updated2.declined_by
        # Reservation from vol-old must be released upon handoff
        assert svc.get_budget_snapshot("oslo").active_reservations == Decimal("0")
        assert svc.get_budget_snapshot("oslo").available == Decimal("6000")

        # 3. Original volunteer (vol-old) cannot re-accept or complete after handoff
        with pytest.raises(ValueError, match="already handed over"):
            svc.accept_refill_request(req.id, "vol-old", Decimal("500"))
        with pytest.raises(ValueError):
            svc.complete_purchase(req.id, "vol-old", Decimal("500"), "RCPT-OLD")

        # 4. New volunteer (vol-recent) accepts; original volunteer still cannot complete
        svc.accept_refill_request(req.id, "vol-recent", Decimal("700"))
        with pytest.raises(ValueError, match="not assigned"):
            svc.complete_purchase(req.id, "vol-old", Decimal("650"), "RCPT-OLD")

        # 5. New volunteer completes the purchase
        completed_req, claim = svc.complete_purchase(
            req.id, "vol-recent", Decimal("650"), "RCPT-RECENT-01"
        )
        assert completed_req.status == RefillStatus.COMPLETED
        assert claim.employee_id == "vol-recent"
