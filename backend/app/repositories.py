"""
In-memory repositories for all domain objects.

Why in-memory?
- This is a take-home assignment. No database setup is required.
- Repositories isolate storage from business logic, so swapping in PostgreSQL
  later only requires rewriting these classes, not services.py or routes.py.

Why threading.Lock?
- FastAPI runs in a single process with multiple threads (one per request by default).
- Without a lock, two threads could simultaneously read the "no active request"
  check, both pass, and both create duplicate requests — a classic TOCTOU race.
- The lock makes the check-then-act atomic within a single process.
- LIMITATION: If we ran multiple uvicorn workers (processes), each would have
  its own lock and its own memory — this would NOT be safe. Production requires
  database-level transactions and row locking (e.g. SELECT FOR UPDATE in Postgres).

Why RLock (reentrant)?
- Some service methods call other service methods that also acquire the lock.
  RLock allows the same thread to acquire the lock multiple times without deadlock.
  A plain Lock would deadlock in that scenario.

Repository pattern:
- Each repository owns one collection (dict keyed by id).
- Methods are thin: add, get, list, update, delete.
- No business logic lives here — that's services.py's job.
"""

from __future__ import annotations

import threading
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional

from app.models import (
    BudgetSnapshot,
    ClaimStatus,
    Counter,
    CounterStatus,
    Currency,
    Employee,
    Office,
    RefillRequest,
    RefillStatus,
    ReimbursementClaim,
)


class OfficeRepository:
    def __init__(self) -> None:
        self._data: Dict[str, Office] = {}

    def add(self, office: Office) -> Office:
        self._data[office.id] = office
        return office

    def get(self, office_id: str) -> Optional[Office]:
        return self._data.get(office_id)

    def list_all(self) -> List[Office]:
        return list(self._data.values())


class EmployeeRepository:
    def __init__(self) -> None:
        self._data: Dict[str, Employee] = {}

    def add(self, employee: Employee) -> Employee:
        self._data[employee.id] = employee
        return employee

    def get(self, employee_id: str) -> Optional[Employee]:
        return self._data.get(employee_id)

    def list_all(self) -> List[Employee]:
        return list(self._data.values())

    def list_by_office(self, office_id: str) -> List[Employee]:
        return [e for e in self._data.values() if e.office_id == office_id]

    def list_volunteers(self, office_id: str) -> List[Employee]:
        return [
            e for e in self._data.values()
            if e.office_id == office_id and e.is_volunteer
        ]


class CounterRepository:
    def __init__(self) -> None:
        self._data: Dict[str, Counter] = {}

    def add(self, counter: Counter) -> Counter:
        self._data[counter.id] = counter
        return counter

    def get(self, counter_id: str) -> Optional[Counter]:
        return self._data.get(counter_id)

    def get_by_office(self, office_id: str) -> Optional[Counter]:
        """Return the first counter for the given office (each office has one)."""
        for c in self._data.values():
            if c.office_id == office_id:
                return c
        return None

    def list_all(self) -> List[Counter]:
        return list(self._data.values())

    def update_status(self, counter_id: str, status: CounterStatus) -> Optional[Counter]:
        counter = self._data.get(counter_id)
        if counter:
            counter.status = status
            counter.last_updated_at = datetime.utcnow()
        return counter


class RefillRequestRepository:
    def __init__(self) -> None:
        self._data: Dict[str, RefillRequest] = {}

    def add(self, request: RefillRequest) -> RefillRequest:
        self._data[request.id] = request
        return request

    def get(self, request_id: str) -> Optional[RefillRequest]:
        return self._data.get(request_id)

    def list_all(self) -> List[RefillRequest]:
        return list(self._data.values())

    def list_by_office(self, office_id: str) -> List[RefillRequest]:
        return [r for r in self._data.values() if r.office_id == office_id]

    def find_active_for_counter(self, counter_id: str) -> Optional[RefillRequest]:
        """
        Find an OPEN or ASSIGNED request for this counter.
        Used to prevent duplicate active requests for the same counter.
        """
        active_statuses = {RefillStatus.OPEN, RefillStatus.ASSIGNED}
        for r in self._data.values():
            if r.counter_id == counter_id and r.status in active_statuses:
                return r
        return None

    def list_by_status(self, status: RefillStatus) -> List[RefillRequest]:
        return [r for r in self._data.values() if r.status == status]


class ReimbursementClaimRepository:
    def __init__(self) -> None:
        self._data: Dict[str, ReimbursementClaim] = {}

    def add(self, claim: ReimbursementClaim) -> ReimbursementClaim:
        self._data[claim.id] = claim
        return claim

    def get(self, claim_id: str) -> Optional[ReimbursementClaim]:
        return self._data.get(claim_id)

    def list_all(self) -> List[ReimbursementClaim]:
        return list(self._data.values())

    def list_by_office(self, office_id: str) -> List[ReimbursementClaim]:
        return [c for c in self._data.values() if c.office_id == office_id]

    def list_by_employee(self, employee_id: str) -> List[ReimbursementClaim]:
        return [c for c in self._data.values() if c.employee_id == employee_id]

    def find_by_refill_request(self, refill_request_id: str) -> Optional[ReimbursementClaim]:
        for c in self._data.values():
            if c.refill_request_id == refill_request_id:
                return c
        return None


class BudgetRepository:
    """
    Tracks per-office monthly budget state.
    
    Separation of concerns:
    - actual_spent: sum of purchase_amounts for COMPLETED requests this month.
    - active_reservations: sum of estimated_amounts for ASSIGNED requests
                           (the volunteer accepted but hasn't bought yet).

    When a volunteer accepts:   reservation += estimated_amount
    When purchase completes:    reservation -= estimated_amount  (released exactly once)
                                actual_spent += purchase_amount   (recorded exactly once)

    This prevents double-counting: we never add both reservation and actual
    to the "spent" total at the same time.

    available = monthly_budget - actual_spent - active_reservations
    """

    def __init__(self) -> None:
        # {office_id: {"monthly_budget": Decimal, "actual_spent": Decimal,
        #               "active_reservations": Decimal, "currency": Currency}}
        self._data: Dict[str, dict] = {}

    def initialize(self, office_id: str, monthly_budget: Decimal, currency: Currency) -> None:
        self._data[office_id] = {
            "monthly_budget": monthly_budget,
            "actual_spent": Decimal("0"),
            "active_reservations": Decimal("0"),
            "currency": currency,
        }

    def _get(self, office_id: str) -> dict:
        d = self._data.get(office_id)
        if d is None:
            raise ValueError(f"No budget initialized for office {office_id}")
        return d

    def reserve(self, office_id: str, amount: Decimal) -> None:
        d = self._get(office_id)
        d["active_reservations"] += amount

    def release_reservation(self, office_id: str, amount: Decimal) -> None:
        d = self._get(office_id)
        d["active_reservations"] -= amount
        # Safety: never go negative (guard against double-release)
        if d["active_reservations"] < Decimal("0"):
            d["active_reservations"] = Decimal("0")

    def record_actual_spend(self, office_id: str, amount: Decimal) -> None:
        d = self._get(office_id)
        d["actual_spent"] += amount

    def get_snapshot(self, office_id: str) -> BudgetSnapshot:
        d = self._get(office_id)
        monthly = d["monthly_budget"]
        spent = d["actual_spent"]
        reserved = d["active_reservations"]
        available = monthly - spent - reserved
        return BudgetSnapshot(
            office_id=office_id,
            currency=d["currency"],
            monthly_budget=monthly,
            actual_spent=spent,
            active_reservations=reserved,
            available=available,
        )

    def available_budget(self, office_id: str) -> Decimal:
        return self.get_snapshot(office_id).available


class AppRepositories:
    """
    Single container that holds all repos and ONE shared RLock.

    Why one shared lock?
    - Cross-repo operations (e.g. reserve budget AND assign request) must be atomic.
    - Using one lock ensures no partial state is visible between threads.
    - Downside: coarse granularity (all writes block each other). Acceptable for
      a low-traffic internal tool; production would use row-level DB locking.
    """
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.offices = OfficeRepository()
        self.employees = EmployeeRepository()
        self.counters = CounterRepository()
        self.refill_requests = RefillRequestRepository()
        self.claims = ReimbursementClaimRepository()
        self.budgets = BudgetRepository()


# Global singleton — shared across all requests in the process
repos = AppRepositories()
