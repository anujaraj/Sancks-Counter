"""
Business logic layer — all domain rules live here.

This is the most important file. The key principle:
- Routes validate HTTP input and call service methods.
- Services enforce business rules and call repositories.
- Repositories store/retrieve data.
- No business logic in routes. No I/O in services.

Thread-safety:
- Every write operation acquires repos.lock before reading + modifying state.
- This makes check-then-act atomic (e.g. checking for active request, then creating).
- The lock is an RLock so the same thread can acquire it multiple times.

Exceptions:
- ValueError for domain rule violations (bad input, wrong state).
- These bubble up to the API layer which converts them to HTTP 400/409 responses.
"""

from __future__ import annotations

import calendar
from datetime import datetime
from decimal import Decimal
from typing import List, Optional, Tuple

from app.integrations import MockChatService, MockHRService
from app.models import (
    BudgetSnapshot,
    ClaimStatus,
    Counter,
    CounterStatus,
    Currency,
    Employee,
    EmployeeRole,
    Office,
    RefillRequest,
    RefillStatus,
    ReimbursementClaim,
    generate_id,
)
from app.repositories import AppRepositories


class SnackService:
    """
    Central service for the Snack Counter Automation workflow.

    Injecting repos, chat and hr at construction makes it easy to pass
    test doubles (isolated repos) in tests without touching global state.
    """

    def __init__(
        self,
        repos: AppRepositories,
        chat: MockChatService,
        hr: MockHRService,
    ) -> None:
        self.repos = repos
        self.chat = chat
        self.hr = hr

    # ──────────────────────────────────────────────
    # Office & Counter
    # ──────────────────────────────────────────────

    def get_offices(self) -> List[Office]:
        return self.repos.offices.list_all()

    def get_office(self, office_id: str) -> Office:
        office = self.repos.offices.get(office_id)
        if not office:
            raise ValueError(f"Office not found: {office_id}")
        return office

    def get_counter_for_office(self, office_id: str) -> Counter:
        counter = self.repos.counters.get_by_office(office_id)
        if not counter:
            raise ValueError(f"No counter found for office: {office_id}")
        return counter

    # ──────────────────────────────────────────────
    # Step 1: Report empty counter
    # ──────────────────────────────────────────────

    def report_empty_counter(self, office_id: str, reported_by_id: str) -> RefillRequest:
        """
        An employee reports the snack counter is empty.

        Duplicate prevention:
        - We acquire the lock before checking for active requests.
        - This makes "check for existing + create new" atomic.
        - Without the lock, two concurrent reports could both pass the check
          and create two OPEN requests for the same counter.

        Returns the new RefillRequest, or raises ValueError if one already exists.
        """
        with self.repos.lock:
            # Validate employee and office
            employee = self.repos.employees.get(reported_by_id)
            if not employee:
                raise ValueError(f"Employee not found: {reported_by_id}")
            if employee.office_id != office_id:
                raise ValueError(
                    f"Employee {reported_by_id} does not belong to office {office_id}"
                )

            counter = self.get_counter_for_office(office_id)
            office = self.get_office(office_id)

            # Duplicate check: prevent two active requests for same counter
            existing = self.repos.refill_requests.find_active_for_counter(counter.id)
            if existing:
                raise ValueError(
                    f"A refill request (#{existing.id}) is already active for this counter. "
                    f"Status: {existing.status.value}. "
                    f"Please wait for it to be completed."
                )

            # Mark counter as EMPTY
            self.repos.counters.update_status(counter.id, CounterStatus.EMPTY)

            # Create the refill request
            request = RefillRequest(
                id=generate_id(),
                counter_id=counter.id,
                office_id=office_id,
                reported_by=reported_by_id,
                assigned_to=None,
                status=RefillStatus.OPEN,
                currency=office.currency,
            )
            self.repos.refill_requests.add(request)

            # Notify eligible volunteers via mock chat
            volunteers = self._get_eligible_volunteers(office_id)
            if volunteers:
                self.chat.notify_volunteers(
                    volunteer_ids=[v.id for v in volunteers],
                    volunteer_names=[v.name for v in volunteers],
                    office_name=office.name,
                    request_id=request.id,
                    message=(
                        f"The snack counter at {office.name} is EMPTY! "
                        f"Can you pick up snacks? Request #{request.id}. "
                        f"Reply with /accept {request.id} to volunteer."
                    ),
                )

            return request

    # ──────────────────────────────────────────────
    # Step 2: Accept refill request (volunteer assignment)
    # ──────────────────────────────────────────────

    def accept_refill_request(
        self,
        request_id: str,
        volunteer_id: str,
        estimated_amount: Decimal,
    ) -> RefillRequest:
        """
        A volunteer accepts an open refill request.

        Concurrency safety:
        - We acquire the lock and re-check request.status == OPEN inside the lock.
        - If two volunteers try to accept the same request simultaneously:
          Thread A: acquires lock → sees OPEN → changes to ASSIGNED → releases lock
          Thread B: acquires lock → sees ASSIGNED → raises ValueError → releases lock
        - Only Thread A succeeds. Thread B gets a friendly error.

        Budget reservation:
        - We reserve estimated_amount from the office budget immediately.
        - This prevents over-committing the budget if multiple requests are
          in-flight at the same time.

        Fairness:
        - _get_eligible_volunteers returns volunteers sorted by last_assigned_at,
          preferring those who haven't been assigned recently (or ever).
        - The assignment happens regardless of sort order — the volunteer who clicks
          "Accept" first wins — but the notification order reflects fairness preference.
        """
        with self.repos.lock:
            volunteer = self.repos.employees.get(volunteer_id)
            if not volunteer:
                raise ValueError(f"Employee not found: {volunteer_id}")
            if not volunteer.is_volunteer:
                raise ValueError(f"Employee {volunteer_id} is not a registered volunteer")

            request = self.repos.refill_requests.get(request_id)
            if not request:
                raise ValueError(f"Refill request not found: {request_id}")

            # Must be OPEN to accept — this is the concurrency gate
            if request.status != RefillStatus.OPEN:
                raise ValueError(
                    f"Refill request #{request_id} cannot be accepted. "
                    f"Current status: {request.status.value}. "
                    f"Another volunteer may have already accepted it."
                )

            # Volunteer must belong to the same office (or be visiting)
            # For this assignment, we allow volunteers from the same office only
            if volunteer.office_id != request.office_id:
                raise ValueError(
                    f"Volunteer {volunteer_id} belongs to a different office "
                    f"({volunteer.office_id}), not {request.office_id}."
                )

            # Validate estimated amount
            if estimated_amount <= Decimal("0"):
                raise ValueError("Estimated amount must be positive")

            office = self.get_office(request.office_id)

            # Currency must match the office
            if office.currency != request.currency:
                raise ValueError(
                    f"Currency mismatch: office uses {office.currency.value}, "
                    f"request is in {request.currency.value}"
                )

            # Budget check: estimated amount must fit in available budget
            available = self.repos.budgets.available_budget(request.office_id)
            if estimated_amount > available:
                raise ValueError(
                    f"Estimated amount {estimated_amount} {office.currency.value} "
                    f"exceeds available budget {available} {office.currency.value}"
                )

            # All checks passed — assign the request (atomic under lock)
            request.status = RefillStatus.ASSIGNED
            request.assigned_to = volunteer_id
            request.assigned_at = datetime.utcnow()
            request.estimated_amount = estimated_amount
            request.authorized_amount = estimated_amount
            request.reservation_active = True

            # Reserve the amount from the budget
            self.repos.budgets.reserve(request.office_id, estimated_amount)

            # Update volunteer's last_assigned_at for fairness tracking
            volunteer.last_assigned_at = datetime.utcnow()

            # Confirm assignment via mock chat
            self.chat.notify_assignment(
                volunteer_id=volunteer_id,
                volunteer_name=volunteer.name,
                office_name=office.name,
                request_id=request_id,
            )

            return request

    def reject_refill_request(
        self,
        request_id: str,
        volunteer_id: str,
    ) -> RefillRequest:
        """
        A volunteer rejects/declines a refill request, passing it to the next
        volunteer in the fairness queue (never assigned first, then least-recently assigned).

        If the request was already ASSIGNED to this volunteer, any active budget
        reservation is released first so office funds are not locked.
        """
        with self.repos.lock:
            volunteer = self.repos.employees.get(volunteer_id)
            if not volunteer:
                raise ValueError(f"Employee not found: {volunteer_id}")
            if not volunteer.is_volunteer:
                raise ValueError(f"Employee {volunteer_id} is not a registered volunteer")

            request = self.repos.refill_requests.get(request_id)
            if not request:
                raise ValueError(f"Refill request not found: {request_id}")

            if request.status not in (RefillStatus.OPEN, RefillStatus.ASSIGNED):
                raise ValueError(
                    f"Refill request #{request_id} cannot be rejected in {request.status.value} state"
                )

            if volunteer.office_id != request.office_id:
                raise ValueError(
                    f"Volunteer {volunteer_id} belongs to a different office ({volunteer.office_id})"
                )

            if request.status == RefillStatus.ASSIGNED and request.assigned_to not in (None, volunteer_id):
                raise ValueError(
                    f"Request #{request_id} is currently assigned to {request.assigned_to}, not {volunteer_id}"
                )

            # Release budget reservation if this volunteer had already accepted it
            if request.reservation_active and request.estimated_amount:
                self.repos.budgets.release_reservation(request.office_id, request.estimated_amount)
                request.reservation_active = False
                request.estimated_amount = None
                request.authorized_amount = None

            if volunteer_id not in request.declined_by:
                request.declined_by.append(volunteer_id)

            # Select the next eligible volunteer in fairness order excluding those who declined
            eligible = [
                v for v in self._get_eligible_volunteers(request.office_id)
                if v.id not in request.declined_by
            ]
            if not eligible:
                request.assigned_to = None
                request.status = RefillStatus.OPEN
                raise ValueError("No other eligible volunteers available in this office to reassign")

            next_volunteer = eligible[0]
            request.assigned_to = next_volunteer.id
            request.status = RefillStatus.OPEN

            office = self.get_office(request.office_id)
            self.chat.notify_assignment(
                volunteer_id=next_volunteer.id,
                volunteer_name=next_volunteer.name,
                office_name=office.name,
                request_id=request_id,
            )

            return request

    # ──────────────────────────────────────────────
    # Step 3: Complete purchase
    # ──────────────────────────────────────────────

    def complete_purchase(
        self,
        request_id: str,
        volunteer_id: str,
        purchase_amount: Decimal,
        receipt_reference: str,
    ) -> Tuple[RefillRequest, ReimbursementClaim]:
        """
        The volunteer has purchased snacks and is recording the actual spend.

        What happens here (order matters):
        1. Validate the request and volunteer.
        2. Validate the purchase_amount (positive, within authorized limit, within budget).
        3. Release the reservation ONCE (using reservation_active flag).
        4. Record actual spend ONCE.
        5. Mark counter FILLED.
        6. Mark request COMPLETED.
        7. Create a ReimbursementClaim and submit to HR.

        No double-counting:
        - reservation_active prevents releasing the reservation twice.
        - We only call record_actual_spend once per request.
        - The budget formula is: available = monthly - actual_spent - reservations
          After this method: actual_spent increases by purchase_amount,
          reservations decrease by estimated_amount.
          If purchase_amount < estimated_amount, available_budget increases slightly.
          If purchase_amount == estimated_amount, it stays the same.

        Why is HR approval AFTER purchase?
        - The counter is empty NOW. The volunteer should buy immediately.
        - Requiring pre-approval would leave the counter empty for days.
        - This matches how petty cash / expense reimbursement works in real companies.
        """
        with self.repos.lock:
            request = self.repos.refill_requests.get(request_id)
            if not request:
                raise ValueError(f"Refill request not found: {request_id}")

            if request.status != RefillStatus.ASSIGNED:
                raise ValueError(
                    f"Request #{request_id} is not in ASSIGNED state. "
                    f"Current: {request.status.value}"
                )

            if request.assigned_to != volunteer_id:
                raise ValueError(
                    f"Volunteer {volunteer_id} is not assigned to request #{request_id}. "
                    f"Assigned to: {request.assigned_to}"
                )

            if purchase_amount <= Decimal("0"):
                raise ValueError("Purchase amount must be positive")

            if not receipt_reference or not receipt_reference.strip():
                raise ValueError("Receipt reference cannot be empty")

            # Validate against authorized amount (ceiling set at accept time)
            if request.authorized_amount and purchase_amount > request.authorized_amount:
                raise ValueError(
                    f"Purchase amount {purchase_amount} exceeds authorized amount "
                    f"{request.authorized_amount}. Contact your manager for a larger budget."
                )

            office = self.get_office(request.office_id)
            volunteer = self.repos.employees.get(volunteer_id)
            if not volunteer:
                raise ValueError(f"Volunteer not found: {volunteer_id}")

            # Release reservation ONCE (guard against double-release)
            if request.reservation_active:
                self.repos.budgets.release_reservation(
                    request.office_id, request.estimated_amount
                )
                request.reservation_active = False

            # Validate purchase amount fits in available budget
            # (after releasing the reservation, so we see the true remaining budget)
            available_after_release = self.repos.budgets.available_budget(request.office_id)
            if purchase_amount > available_after_release:
                # Re-reserve to restore consistent state
                self.repos.budgets.reserve(request.office_id, request.estimated_amount)
                request.reservation_active = True
                raise ValueError(
                    f"Purchase amount {purchase_amount} {office.currency.value} "
                    f"exceeds remaining budget {available_after_release} {office.currency.value}"
                )

            # Record actual spend ONCE
            self.repos.budgets.record_actual_spend(request.office_id, purchase_amount)

            # Update request
            request.purchase_amount = purchase_amount
            request.status = RefillStatus.COMPLETED
            request.completed_at = datetime.utcnow()

            # Mark counter FILLED immediately
            self.repos.counters.update_status(request.counter_id, CounterStatus.FILLED)

            # Create reimbursement claim and submit to HR
            external_hr_id = self.hr.submit_claim(
                employee_name=volunteer.name,
                office_name=office.name,
                amount=str(purchase_amount),
                currency=office.currency.value,
                receipt_reference=receipt_reference,
                description=f"Snack refill for {office.name} office (request #{request_id})",
            )

            claim = ReimbursementClaim(
                id=generate_id(),
                refill_request_id=request_id,
                employee_id=volunteer_id,
                office_id=request.office_id,
                amount=purchase_amount,
                currency=office.currency,
                receipt_reference=receipt_reference,
                description=f"Snack refill for {office.name} office",
                status=ClaimStatus.PENDING,
                external_hr_claim_id=external_hr_id,
            )
            self.repos.claims.add(claim)

            return request, claim

    # ──────────────────────────────────────────────
    # HR workflow
    # ──────────────────────────────────────────────

    def approve_claim(self, claim_id: str, reviewer_id: str) -> ReimbursementClaim:
        """
        HR approves a reimbursement claim.
        Can only approve PENDING claims.
        """
        with self.repos.lock:
            claim = self.repos.claims.get(claim_id)
            if not claim:
                raise ValueError(f"Claim not found: {claim_id}")

            reviewer = self.repos.employees.get(reviewer_id)
            if not reviewer or reviewer.role not in (EmployeeRole.HR, EmployeeRole.ADMIN):
                raise ValueError(f"Only HR or Admin employees can approve claims")

            if claim.status != ClaimStatus.PENDING:
                raise ValueError(
                    f"Claim #{claim_id} cannot be approved. "
                    f"Current status: {claim.status.value}"
                )

            claim.status = ClaimStatus.APPROVED
            claim.reviewed_at = datetime.utcnow()

            # Also update the HR system record
            if claim.external_hr_claim_id:
                self.hr.approve(claim.external_hr_claim_id)

            return claim

    def reject_claim(
        self, claim_id: str, reviewer_id: str, reason: str
    ) -> ReimbursementClaim:
        """
        HR rejects a reimbursement claim.
        Can only reject PENDING claims.
        """
        with self.repos.lock:
            claim = self.repos.claims.get(claim_id)
            if not claim:
                raise ValueError(f"Claim not found: {claim_id}")

            reviewer = self.repos.employees.get(reviewer_id)
            if not reviewer or reviewer.role not in (EmployeeRole.HR, EmployeeRole.ADMIN):
                raise ValueError(f"Only HR or Admin employees can reject claims")

            if claim.status != ClaimStatus.PENDING:
                raise ValueError(
                    f"Claim #{claim_id} cannot be rejected. "
                    f"Current status: {claim.status.value}"
                )

            if not reason or not reason.strip():
                raise ValueError("A rejection reason is required")

            claim.status = ClaimStatus.REJECTED
            claim.reviewed_at = datetime.utcnow()
            claim.rejection_reason = reason.strip()

            if claim.external_hr_claim_id:
                self.hr.reject(claim.external_hr_claim_id, reason.strip())

            return claim

    def confirm_reimbursed(self, claim_id: str, confirmer_id: str) -> ReimbursementClaim:
        """
        Finance confirms that payment was actually transferred to the volunteer.
        Can only transition APPROVED → REIMBURSED.
        """
        with self.repos.lock:
            claim = self.repos.claims.get(claim_id)
            if not claim:
                raise ValueError(f"Claim not found: {claim_id}")

            confirmer = self.repos.employees.get(confirmer_id)
            if not confirmer or confirmer.role not in (EmployeeRole.HR, EmployeeRole.ADMIN):
                raise ValueError(f"Only HR or Admin employees can confirm reimbursement")

            if claim.status != ClaimStatus.APPROVED:
                raise ValueError(
                    f"Claim #{claim_id} must be APPROVED before marking as REIMBURSED. "
                    f"Current status: {claim.status.value}"
                )

            claim.status = ClaimStatus.REIMBURSED
            claim.reimbursed_at = datetime.utcnow()
            return claim

    # ──────────────────────────────────────────────
    # Queries
    # ──────────────────────────────────────────────

    def get_active_requests_for_office(self, office_id: str) -> List[RefillRequest]:
        active = {RefillStatus.OPEN, RefillStatus.ASSIGNED}
        return [
            r for r in self.repos.refill_requests.list_by_office(office_id)
            if r.status in active
        ]

    def get_all_requests_for_office(self, office_id: str) -> List[RefillRequest]:
        return self.repos.refill_requests.list_by_office(office_id)

    def get_claims_for_office(self, office_id: str) -> List[ReimbursementClaim]:
        return self.repos.claims.list_by_office(office_id)

    def get_all_claims(self) -> List[ReimbursementClaim]:
        return self.repos.claims.list_all()

    def get_budget_snapshot(self, office_id: str) -> BudgetSnapshot:
        self.get_office(office_id)  # validate office exists
        return self.repos.budgets.get_snapshot(office_id)

    def get_employees_for_office(self, office_id: str) -> List[Employee]:
        return self.repos.employees.list_by_office(office_id)

    def get_all_employees(self) -> List[Employee]:
        return self.repos.employees.list_all()

    # ──────────────────────────────────────────────
    # Monthly report
    # ──────────────────────────────────────────────

    def get_monthly_report(self, office_id: str, year: int, month: int) -> dict:
        """
        Monthly spending report for a single office.

        Why NOK and INR are always separate:
        - Exchange rates fluctuate daily.
        - Combining them would require a conversion rate that may be inaccurate.
        - Each office has its own budget in its own currency.
        - Reports are generated per-office so the number is always in one currency.

        Why manual reporting instead of computer vision?
        - Computer vision for receipt scanning is a complex ML problem requiring
          training data, model hosting, and error handling.
        - For this assignment, human input is simpler, faster, and more reliable.
        - In production, you could add OCR (e.g. Google Document AI) as an enhancement
          after the core workflow is proven to work.
        """
        self.get_office(office_id)
        requests = self.repos.refill_requests.list_by_office(office_id)
        claims = self.repos.claims.list_by_office(office_id)

        # Filter to the requested month
        def in_month(dt: Optional[datetime]) -> bool:
            return dt is not None and dt.year == year and dt.month == month

        completed_requests = [
            r for r in requests
            if r.status == RefillStatus.COMPLETED and in_month(r.completed_at)
        ]
        month_claims = [
            c for c in claims
            if in_month(c.submitted_at)
        ]

        total_spent = sum(
            (r.purchase_amount for r in completed_requests if r.purchase_amount),
            Decimal("0"),
        )

        claims_by_status = {}
        for c in month_claims:
            claims_by_status.setdefault(c.status.value, []).append(c)

        budget = self.repos.budgets.get_snapshot(office_id)
        office = self.get_office(office_id)

        return {
            "office_id": office_id,
            "office_name": office.name,
            "year": year,
            "month": month,
            "currency": office.currency.value,
            "monthly_budget": str(budget.monthly_budget),
            "total_spent": str(total_spent),
            "actual_spent_ytd": str(budget.actual_spent),
            "active_reservations": str(budget.active_reservations),
            "available_budget": str(budget.available),
            "completed_refills": len(completed_requests),
            "claims_pending": len(claims_by_status.get("PENDING", [])),
            "claims_approved": len(claims_by_status.get("APPROVED", [])),
            "claims_rejected": len(claims_by_status.get("REJECTED", [])),
            "claims_reimbursed": len(claims_by_status.get("REIMBURSED", [])),
            "refill_details": [
                {
                    "request_id": r.id,
                    "volunteer": self._get_employee_name(r.assigned_to),
                    "amount": str(r.purchase_amount),
                    "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                }
                for r in completed_requests
            ],
        }

    # ──────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────

    def _get_eligible_volunteers(self, office_id: str) -> List[Employee]:
        """
        Return volunteers for this office, sorted for fairness.

        Fairness algorithm:
        - Volunteers who have NEVER been assigned come first (last_assigned_at is None).
        - Among previously assigned volunteers, those assigned LEAST RECENTLY come first.
        - This is a simple "least recently used" rotation — not strictly round-robin,
          but ensures the same volunteer doesn't repeatedly carry the load.

        In production: you might want a more sophisticated system (e.g. weighted by
        how often they've been assigned, respecting leave schedules, etc.)
        """
        volunteers = self.repos.employees.list_volunteers(office_id)
        # Sort: None last_assigned_at first (never assigned), then by oldest assignment
        return sorted(
            volunteers,
            key=lambda v: (
                v.last_assigned_at is not None,  # False (None) sorts before True
                v.last_assigned_at or datetime.min,
            ),
        )

    def _get_employee_name(self, employee_id: Optional[str]) -> Optional[str]:
        if not employee_id:
            return None
        emp = self.repos.employees.get(employee_id)
        return emp.name if emp else employee_id
