"""
Pydantic schemas for FastAPI request bodies and response models.

Why separate from domain models?
- Domain models (dataclasses) are plain Python objects used in business logic.
- Schemas (Pydantic) are for HTTP: they validate incoming JSON, coerce types,
  and produce clean JSON output.
- This separation means changing the API shape doesn't break domain logic.

Decimal handling:
- FastAPI/Pydantic accepts Decimal but serializes it to a JSON number by default.
- We use str for monetary amounts in responses to avoid precision loss in JSON.
  (JSON numbers can lose decimal precision in some parsers.)
- The frontend receives amounts as strings and uses parseFloat for display only.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, field_validator


# ──────────────────────────────────────────────
# Request bodies
# ──────────────────────────────────────────────

class ReportEmptyRequest(BaseModel):
    reported_by: str   # employee_id


class AcceptRequestBody(BaseModel):
    volunteer_id: str
    estimated_amount: Decimal

    @field_validator("estimated_amount")
    @classmethod
    def amount_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("estimated_amount must be positive")
        return v


class RejectRequestBody(BaseModel):
    volunteer_id: str


class CompletePurchaseBody(BaseModel):
    volunteer_id: str
    purchase_amount: Decimal
    receipt_reference: str

    @field_validator("purchase_amount")
    @classmethod
    def amount_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("purchase_amount must be positive")
        return v

    @field_validator("receipt_reference")
    @classmethod
    def receipt_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("receipt_reference cannot be empty")
        return v.strip()


class ClaimActionBody(BaseModel):
    reviewer_id: str
    reason: Optional[str] = None   # required for reject


class ConfirmReimbursedBody(BaseModel):
    confirmer_id: str


# ──────────────────────────────────────────────
# Response models
# ──────────────────────────────────────────────

class OfficeResponse(BaseModel):
    id: str
    name: str
    currency: str
    monthly_budget: str    # Decimal as string


class EmployeeResponse(BaseModel):
    id: str
    name: str
    email: str
    office_id: str
    is_volunteer: bool
    role: str
    last_assigned_at: Optional[datetime] = None


class CounterResponse(BaseModel):
    id: str
    office_id: str
    status: str
    last_updated_at: datetime


class RefillRequestResponse(BaseModel):
    id: str
    counter_id: str
    office_id: str
    reported_by: str
    assigned_to: Optional[str] = None
    status: str
    estimated_amount: Optional[str] = None   # Decimal as string
    authorized_amount: Optional[str] = None
    purchase_amount: Optional[str] = None
    currency: Optional[str] = None
    created_at: datetime
    assigned_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    declined_by: List[str] = []


class ReimbursementClaimResponse(BaseModel):
    id: str
    refill_request_id: str
    employee_id: str
    office_id: str
    amount: str                      # Decimal as string
    currency: str
    receipt_reference: str
    description: str
    status: str
    external_hr_claim_id: Optional[str] = None
    submitted_at: datetime
    reviewed_at: Optional[datetime] = None
    reimbursed_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None


class BudgetSnapshotResponse(BaseModel):
    office_id: str
    currency: str
    monthly_budget: str
    actual_spent: str
    active_reservations: str
    available: str


class OfficeDashboardResponse(BaseModel):
    office: OfficeResponse
    counter: CounterResponse
    active_requests: List[RefillRequestResponse]
    budget: BudgetSnapshotResponse
    volunteers: List[EmployeeResponse]


class CompletePurchaseResponse(BaseModel):
    request: RefillRequestResponse
    claim: ReimbursementClaimResponse


class NotificationsResponse(BaseModel):
    notifications: List[dict]


class MonthlyReportResponse(BaseModel):
    office_id: str
    office_name: str
    year: int
    month: int
    currency: str
    monthly_budget: str
    total_spent: str
    actual_spent_ytd: str
    active_reservations: str
    available_budget: str
    completed_refills: int
    claims_pending: int
    claims_approved: int
    claims_rejected: int
    claims_reimbursed: int
    refill_details: List[dict]
