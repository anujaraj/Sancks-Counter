"""
Tests for budget logic:
- Reservation on accept
- Release on completion
- No double counting
- No double reservation release
- Insufficient budget
- Actual spend vs reservation separation
- Currency isolation (NOK and INR never mixed)
- Negative amounts
"""

from decimal import Decimal

import pytest

from app.models import RefillStatus
from tests.conftest import (
    make_blr_office,
    make_counter,
    make_employee,
    make_oslo_office,
)


def full_oslo_setup(repos):
    """Oslo with counter, reporter, and volunteer."""
    make_oslo_office(repos)
    make_counter(repos, "oslo")
    make_employee(repos, "emp-01", "oslo", is_volunteer=False)
    make_employee(repos, "vol-01", "oslo", is_volunteer=True)


class TestBudgetReservation:
    def test_reservation_on_accept(self, svc, app_repos):
        """
        When a volunteer accepts with estimated=500 NOK,
        active_reservations should increase by 500.
        
        Example:
          Budget: 6000 NOK
          After accept(500): reserved=500, available=5500
        """
        full_oslo_setup(app_repos)
        req = svc.report_empty_counter("oslo", "emp-01")
        svc.accept_refill_request(req.id, "vol-01", Decimal("500"))

        snapshot = svc.get_budget_snapshot("oslo")
        assert snapshot.active_reservations == Decimal("500")
        assert snapshot.actual_spent == Decimal("0")
        assert snapshot.available == Decimal("5500")

    def test_reservation_released_on_completion(self, svc, app_repos):
        """
        After completing the purchase:
        - reservation is released (goes back to 0)
        - actual_spent increases by purchase_amount
        - no double counting
        
        Example:
          Accept(500) → reserved=500, actual=0, available=5500
          Complete(490) → reserved=0, actual=490, available=5510
        """
        full_oslo_setup(app_repos)
        req = svc.report_empty_counter("oslo", "emp-01")
        svc.accept_refill_request(req.id, "vol-01", Decimal("500"))
        svc.complete_purchase(req.id, "vol-01", Decimal("490"), "RCPT-001")

        snapshot = svc.get_budget_snapshot("oslo")
        assert snapshot.active_reservations == Decimal("0"), "Reservation must be released"
        assert snapshot.actual_spent == Decimal("490"), "Actual spend must be recorded"
        assert snapshot.available == Decimal("5510"), "Available = 6000 - 490"

    def test_no_double_counting(self, svc, app_repos):
        """
        The sum of reservation + actual_spent should equal the total committed.
        At no point should both be non-zero for the same purchase.
        
        After completion: reservation=0, actual=purchase_amount
        Before completion: reservation=estimated, actual=0
        """
        full_oslo_setup(app_repos)
        req = svc.report_empty_counter("oslo", "emp-01")
        svc.accept_refill_request(req.id, "vol-01", Decimal("800"))

        # Before completion: only reservation
        snap_before = svc.get_budget_snapshot("oslo")
        assert snap_before.active_reservations == Decimal("800")
        assert snap_before.actual_spent == Decimal("0")

        svc.complete_purchase(req.id, "vol-01", Decimal("800"), "RCPT-002")

        # After completion: only actual
        snap_after = svc.get_budget_snapshot("oslo")
        assert snap_after.active_reservations == Decimal("0")
        assert snap_after.actual_spent == Decimal("800")
        # Total committed = 800, available = 6000 - 800 = 5200
        assert snap_after.available == Decimal("5200")

    def test_no_double_reservation_release(self, svc, app_repos):
        """
        Completing a request releases its reservation once.
        Attempting to complete it a second time must fail and must NOT
        decrement active_reservations belonging to other active requests.
        """
        full_oslo_setup(app_repos)
        make_employee(app_repos, "vol-02", "oslo", is_volunteer=True)

        # Request 1: accept 500, complete 450
        req1 = svc.report_empty_counter("oslo", "emp-01")
        svc.accept_refill_request(req1.id, "vol-01", Decimal("500"))
        svc.complete_purchase(req1.id, "vol-01", Decimal("450"), "RCPT-101")
        assert req1.reservation_active is False

        # Request 2: accept 600 (now active_reservations == 600)
        req2 = svc.report_empty_counter("oslo", "emp-01")
        svc.accept_refill_request(req2.id, "vol-02", Decimal("600"))
        assert svc.get_budget_snapshot("oslo").active_reservations == Decimal("600")

        # Attempting to complete req1 again must raise and leave req2's 600 reservation intact
        with pytest.raises(ValueError, match="not in ASSIGNED state"):
            svc.complete_purchase(req1.id, "vol-01", Decimal("450"), "RCPT-101-DUP")

        snap = svc.get_budget_snapshot("oslo")
        assert snap.active_reservations == Decimal("600")
        assert snap.actual_spent == Decimal("450")

    def test_multiple_concurrent_reservations(self, svc, app_repos):
        """
        Two simultaneous refill requests should each reserve independently.
        Available budget should decrease by both reservations.
        
        Example:
          Request 1 accepted: estimate=500, reserved=500
          Request 2 accepted: estimate=700, reserved=1200 total
          available = 6000 - 1200 = 4800
        """
        make_oslo_office(app_repos)
        # Need two separate counters to allow two active requests
        # (each counter can have only one active request)
        from app.models import Counter, CounterStatus
        c1 = Counter(id="c1", office_id="oslo", status=CounterStatus.FILLED)
        c2 = Counter(id="c2", office_id="oslo", status=CounterStatus.FILLED)
        app_repos.counters.add(c1)
        app_repos.counters.add(c2)

        make_employee(app_repos, "emp-01", "oslo")
        make_employee(app_repos, "emp-02", "oslo")
        make_employee(app_repos, "vol-01", "oslo", is_volunteer=True)
        make_employee(app_repos, "vol-02", "oslo", is_volunteer=True)

        # Create two refill requests on different counters
        from app.models import RefillRequest, RefillStatus, Currency, generate_id
        req1 = RefillRequest(
            id="req-1", counter_id="c1", office_id="oslo",
            reported_by="emp-01", assigned_to=None,
            status=RefillStatus.OPEN, currency=Currency.NOK
        )
        req2 = RefillRequest(
            id="req-2", counter_id="c2", office_id="oslo",
            reported_by="emp-02", assigned_to=None,
            status=RefillStatus.OPEN, currency=Currency.NOK
        )
        app_repos.counters.update_status("c1", CounterStatus.EMPTY)
        app_repos.counters.update_status("c2", CounterStatus.EMPTY)
        app_repos.refill_requests.add(req1)
        app_repos.refill_requests.add(req2)

        svc.accept_refill_request("req-1", "vol-01", Decimal("500"))
        svc.accept_refill_request("req-2", "vol-02", Decimal("700"))

        snap = svc.get_budget_snapshot("oslo")
        assert snap.active_reservations == Decimal("1200")
        assert snap.available == Decimal("4800")


class TestBudgetValidation:
    def test_insufficient_budget_on_accept(self, svc, app_repos):
        """
        Accepting a request that exceeds available budget should raise ValueError.
        """
        full_oslo_setup(app_repos)
        req = svc.report_empty_counter("oslo", "emp-01")

        with pytest.raises(ValueError, match="exceeds available budget"):
            svc.accept_refill_request(req.id, "vol-01", Decimal("7000"))  # > 6000

    def test_zero_amount_rejected(self, svc, app_repos):
        """Zero estimated amount should be rejected."""
        full_oslo_setup(app_repos)
        req = svc.report_empty_counter("oslo", "emp-01")

        with pytest.raises(ValueError, match="positive"):
            svc.accept_refill_request(req.id, "vol-01", Decimal("0"))

    def test_negative_amount_rejected(self, svc, app_repos):
        """Negative purchase amount should be rejected."""
        full_oslo_setup(app_repos)
        req = svc.report_empty_counter("oslo", "emp-01")
        svc.accept_refill_request(req.id, "vol-01", Decimal("500"))

        with pytest.raises(ValueError, match="positive"):
            svc.complete_purchase(req.id, "vol-01", Decimal("-100"), "RCPT-003")

    def test_purchase_exceeds_authorized_amount(self, svc, app_repos):
        """
        Purchase amount cannot exceed the authorized amount set at accept time.
        This prevents unauthorized overspend.
        """
        full_oslo_setup(app_repos)
        req = svc.report_empty_counter("oslo", "emp-01")
        svc.accept_refill_request(req.id, "vol-01", Decimal("500"))

        with pytest.raises(ValueError, match="exceeds authorized amount"):
            svc.complete_purchase(req.id, "vol-01", Decimal("600"), "RCPT-004")


class TestCurrencyIsolation:
    def test_nok_and_inr_are_separate(self, svc, app_repos):
        """
        Each office tracks its own budget independently.
        Spending in Oslo should not affect Bengaluru's budget.
        """
        make_oslo_office(app_repos)
        make_blr_office(app_repos)
        make_counter(app_repos, "oslo")
        make_counter(app_repos, "bengaluru", "counter-blr")
        make_employee(app_repos, "oslo-emp", "oslo")
        make_employee(app_repos, "blr-emp", "bengaluru")
        make_employee(app_repos, "oslo-vol", "oslo", is_volunteer=True)
        make_employee(app_repos, "blr-vol", "bengaluru", is_volunteer=True)

        req_oslo = svc.report_empty_counter("oslo", "oslo-emp")
        svc.accept_refill_request(req_oslo.id, "oslo-vol", Decimal("1000"))
        svc.complete_purchase(req_oslo.id, "oslo-vol", Decimal("950"), "RCPT-NO-01")

        oslo_snap = svc.get_budget_snapshot("oslo")
        blr_snap = svc.get_budget_snapshot("bengaluru")

        assert oslo_snap.actual_spent == Decimal("950")
        assert blr_snap.actual_spent == Decimal("0"), "INR budget must be unaffected"
        assert oslo_snap.currency.value == "NOK"
        assert blr_snap.currency.value == "INR"

        # Now run a Bengaluru purchase in INR and verify combined monthly report
        req_blr = svc.report_empty_counter("bengaluru", "blr-emp")
        svc.accept_refill_request(req_blr.id, "blr-vol", Decimal("2000"))
        svc.complete_purchase(req_blr.id, "blr-vol", Decimal("1750.50"), "RCPT-IN-01")

        blr_snap_after = svc.get_budget_snapshot("bengaluru")
        assert blr_snap_after.actual_spent == Decimal("1750.50")
        assert blr_snap_after.available == Decimal("13249.50")
        assert svc.get_budget_snapshot("oslo").actual_spent == Decimal("950")

        from datetime import datetime
        now = datetime.utcnow()
        combined = svc.get_combined_monthly_report(now.year, now.month)
        assert combined["totals_by_currency"] == {"NOK": "950", "INR": "1750.50"}
        assert combined["offices"]["oslo"]["currency"] == "NOK"
        assert combined["offices"]["bengaluru"]["currency"] == "INR"

    def test_budget_full_cycle_numbers(self, svc, app_repos):
        """
        Numerical example matching the assignment specification:
          Oslo monthly: 6,000 NOK
          Accept estimated: 1,200 NOK  → available: 4,800
          Purchase actual: 1,100 NOK   → actual_spent: 1,100, available: 4,900
        """
        full_oslo_setup(app_repos)
        req = svc.report_empty_counter("oslo", "emp-01")
        svc.accept_refill_request(req.id, "vol-01", Decimal("1200"))

        snap = svc.get_budget_snapshot("oslo")
        assert snap.available == Decimal("4800")

        svc.complete_purchase(req.id, "vol-01", Decimal("1100"), "RCPT-005")

        snap2 = svc.get_budget_snapshot("oslo")
        assert snap2.actual_spent == Decimal("1100")
        assert snap2.active_reservations == Decimal("0")
        assert snap2.available == Decimal("4900")  # 6000 - 1100
