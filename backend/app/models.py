"""
Domain models for Snack Counter Automation.

Why dataclasses?
- They give us plain Python objects with no ORM overhead.
- Fields are explicit and type-annotated.
- In-memory repos store these directly — no serialization layer needed inside the app.

Why Decimal for money?
- float arithmetic is binary, not decimal. For example:
      0.1 + 0.2 == 0.30000000000000004 in Python float.
- Financial calculations must be exact. Decimal gives us base-10 precision.

Relationship summary:
  Office  1──n  Employee
  Office  1──n  Counter
  Counter 1──n  RefillRequest
  RefillRequest 1──1  ReimbursementClaim  (created when purchase is completed)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional



class Currency(str, Enum):
    NOK = "NOK"
    INR = "INR"


class CounterStatus(str, Enum):
    FILLED = "FILLED"
    EMPTY  = "EMPTY"


class RefillStatus(str, Enum):
    OPEN       = "OPEN"        # reported, waiting for a volunteer to accept
    ASSIGNED   = "ASSIGNED"    # a volunteer accepted and will purchase
    COMPLETED  = "COMPLETED"   # purchase done, counter filled
    CANCELLED  = "CANCELLED"   # e.g. duplicate prevention or admin action


class ClaimStatus(str, Enum):
    PENDING    = "PENDING"     # submitted to HR, awaiting decision
    APPROVED   = "APPROVED"    # HR approved the amount
    REJECTED   = "REJECTED"    # HR rejected (e.g. wrong amount / no receipt)
    REIMBURSED = "REIMBURSED"  # payment confirmed


class EmployeeRole(str, Enum):
    EMPLOYEE = "EMPLOYEE"
    HR       = "HR"
    ADMIN    = "ADMIN"


# ──────────────────────────────────────────────
# Domain models
# ──────────────────────────────────────────────

@dataclass
class Office:
    """
    Represents a physical office location.

    monthly_budget is the total NOK/INR allowed per calendar month.
    Currency is determined by the office (Oslo → NOK, Bengaluru → INR).
    We never mix currencies — each office tracks its own budget independently.
    """
    id: str
    name: str
    currency: Currency
    monthly_budget: Decimal


@dataclass
class Employee:
    """
    A person who works at Framsikt.

    is_volunteer: True if the person has opted in to purchasing snacks.
    last_assigned_at: timestamp of their last accepted refill request.
        Used for fairness rotation — we prefer volunteers who haven't been
        assigned recently. None means they have never been assigned.
    """
    id: str
    name: str
    email: str
    office_id: str
    is_volunteer: bool = False
    role: EmployeeRole = EmployeeRole.EMPLOYEE
    last_assigned_at: Optional[datetime] = None


@dataclass
class Counter:
    """
    Physical snack counter at an office.
    
    Status is FILLED or EMPTY — this is a physical state, completely independent
    of any reimbursement claim status. The counter becomes FILLED as soon as the
    volunteer completes the purchase, even though HR review may take days.

    Why separate? Because employees need to know if there are snacks NOW.
    Finance needs to know if the receipt has been approved. These are different
    concerns answered by different systems (physical counter vs HR system).
    """
    id: str
    office_id: str
    status: CounterStatus = CounterStatus.FILLED
    last_updated_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class RefillRequest:
    """
    Tracks the lifecycle of a single snack refill event.

    Why are counter status and claim status separate?
    - Counter status answers: "Can employees get snacks right now?"
    - Claim status answers: "Has the volunteer been paid back yet?"
    A volunteer buys snacks (counter fills) but waits days for HR to approve.
    Linking them would force employees to see "EMPTY" even after snacks arrive.

    Budget fields:
    - estimated_amount: reserved from the office budget when volunteer accepts.
    - purchase_amount: actual spend recorded when volunteer completes purchase.
    These are set at different points in time and must not be double-counted.

    authorized_amount: the ceiling approved when the volunteer accepts. We
    validate the actual purchase_amount against this to prevent overruns.
    """
    id: str
    counter_id: str
    office_id: str
    reported_by: str            # employee_id who reported it empty
    assigned_to: Optional[str]  # employee_id of the volunteer who accepted
    status: RefillStatus = RefillStatus.OPEN
    estimated_amount: Optional[Decimal] = None   # reserved on accept
    authorized_amount: Optional[Decimal] = None  # same as estimated at accept time
    purchase_amount: Optional[Decimal] = None    # actual spend on completion
    currency: Optional[Currency] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    assigned_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    # Whether the estimated_amount is currently reserved in the budget
    # Used to prevent double-release of reservation
    reservation_active: bool = False
    # Volunteers who have declined/rejected this request so fairness rotation skips them
    declined_by: list[str] = field(default_factory=list)


@dataclass
class ReimbursementClaim:
    """
    Represents a claim submitted to the HR system for reimbursement.

    Why does HR approval happen AFTER purchase?
    In the real workflow, employees buy snacks immediately (counter is empty now),
    then submit a receipt. HR reviews retroactively. Requiring pre-approval would
    leave the counter empty for days while waiting for bureaucracy.
    This mirrors how most companies handle petty cash and expense reimbursement.

    external_hr_claim_id: the ID returned by the mock HR system, simulating
    what a real integration (e.g. Tripletex, Visma) would return.
    """
    id: str
    refill_request_id: str
    employee_id: str
    office_id: str
    amount: Decimal
    currency: Currency
    receipt_reference: str
    description: str
    status: ClaimStatus = ClaimStatus.PENDING
    external_hr_claim_id: Optional[str] = None
    submitted_at: datetime = field(default_factory=datetime.utcnow)
    reviewed_at: Optional[datetime] = None
    reimbursed_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None


# ──────────────────────────────────────────────
# Budget snapshot (not stored, computed on demand)
# ──────────────────────────────────────────────

@dataclass
class BudgetSnapshot:
    """
    Computed view of an office's budget for the current month.
    
    available = monthly_budget - actual_spent - active_reservations

    We separate actual_spent and reservations so that:
    - actual_spent only increases when a purchase is COMPLETED (not when reserved)
    - reservations are released exactly once when purchase completes
    - available budget is always accurate whether or not purchases are in-flight
    """
    office_id: str
    currency: Currency
    monthly_budget: Decimal
    actual_spent: Decimal
    active_reservations: Decimal
    available: Decimal


def generate_id() -> str:
    """Generate a short, readable unique ID."""
    return str(uuid.uuid4())[:8]
