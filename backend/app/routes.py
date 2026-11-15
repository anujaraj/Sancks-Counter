"""
FastAPI route handlers.

Design principle: routes are thin. They:
1. Parse and validate HTTP input (Pydantic does this automatically).
2. Call a service method.
3. Convert the result to a response schema.
4. Handle exceptions → HTTP status codes.

No business logic lives here. All rules are in services.py.

Error mapping:
- ValueError from services → 400 Bad Request or 409 Conflict
- Not found → 404
- Concurrency conflicts → 409

We use a single router per concern and include all in main.py.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from app.integrations import MockChatService, MockHRService, chat_service, hr_service
from app.models import (
    Counter,
    Employee,
    Office,
    RefillRequest,
    ReimbursementClaim,
)
from app.repositories import AppRepositories, repos as global_repos
from app.schemas import (
    AcceptRequestBody,
    BudgetSnapshotResponse,
    ClaimActionBody,
    CompletePurchaseBody,
    CompletePurchaseResponse,
    ConfirmReimbursedBody,
    CounterResponse,
    EmployeeResponse,
    MonthlyReportResponse,
    NotificationsResponse,
    OfficeDashboardResponse,
    OfficeResponse,
    RefillRequestResponse,
    ReimbursementClaimResponse,
    RejectRequestBody,
    ReportEmptyRequest,
)
from app.services import SnackService


# ── Dependency injection ──────────────────────────────────────────────────────

def get_service() -> SnackService:
    """
    FastAPI dependency that provides a SnackService instance.
    Uses the global singletons (repos, chat, hr) for the running server.
    Tests override this with isolated instances.
    """
    return SnackService(global_repos, chat_service, hr_service)


# ── Converters: domain model → response schema ────────────────────────────────

def office_to_response(o: Office) -> OfficeResponse:
    return OfficeResponse(
        id=o.id,
        name=o.name,
        currency=o.currency.value,
        monthly_budget=str(o.monthly_budget),
    )


def employee_to_response(e: Employee) -> EmployeeResponse:
    return EmployeeResponse(
        id=e.id,
        name=e.name,
        email=e.email,
        office_id=e.office_id,
        is_volunteer=e.is_volunteer,
        role=e.role.value,
        last_assigned_at=e.last_assigned_at,
    )


def counter_to_response(c: Counter) -> CounterResponse:
    return CounterResponse(
        id=c.id,
        office_id=c.office_id,
        status=c.status.value,
        last_updated_at=c.last_updated_at,
    )


def request_to_response(r: RefillRequest) -> RefillRequestResponse:
    return RefillRequestResponse(
        id=r.id,
        counter_id=r.counter_id,
        office_id=r.office_id,
        reported_by=r.reported_by,
        assigned_to=r.assigned_to,
        status=r.status.value,
        estimated_amount=str(r.estimated_amount) if r.estimated_amount is not None else None,
        authorized_amount=str(r.authorized_amount) if r.authorized_amount is not None else None,
        purchase_amount=str(r.purchase_amount) if r.purchase_amount is not None else None,
        currency=r.currency.value if r.currency else None,
        created_at=r.created_at,
        assigned_at=r.assigned_at,
        completed_at=r.completed_at,
        declined_by=list(r.declined_by),
    )


def claim_to_response(c: ReimbursementClaim) -> ReimbursementClaimResponse:
    return ReimbursementClaimResponse(
        id=c.id,
        refill_request_id=c.refill_request_id,
        employee_id=c.employee_id,
        office_id=c.office_id,
        amount=str(c.amount),
        currency=c.currency.value,
        receipt_reference=c.receipt_reference,
        description=c.description,
        status=c.status.value,
        external_hr_claim_id=c.external_hr_claim_id,
        submitted_at=c.submitted_at,
        reviewed_at=c.reviewed_at,
        reimbursed_at=c.reimbursed_at,
        rejection_reason=c.rejection_reason,
    )


# ── Routers ───────────────────────────────────────────────────────────────────

router = APIRouter()


# Health check
@router.get("/health", tags=["System"])
def health_check():
    return {"status": "ok", "service": "Snack Counter Automation"}


# ── Offices ───────────────────────────────────────────────────────────────────

@router.get("/offices", response_model=List[OfficeResponse], tags=["Offices"])
def list_offices(svc: SnackService = Depends(get_service)):
    return [office_to_response(o) for o in svc.get_offices()]


@router.get("/offices/{office_id}", response_model=OfficeResponse, tags=["Offices"])
def get_office(office_id: str, svc: SnackService = Depends(get_service)):
    try:
        return office_to_response(svc.get_office(office_id))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── Office dashboard (aggregated view) ────────────────────────────────────────

@router.get(
    "/offices/{office_id}/dashboard",
    response_model=OfficeDashboardResponse,
    tags=["Offices"],
)
def office_dashboard(office_id: str, svc: SnackService = Depends(get_service)):
    """
    Single endpoint that returns everything the employee dashboard needs:
    office info, counter status, active requests, budget, and volunteers.
    Reduces the number of round-trips from the frontend.
    """
    try:
        office = svc.get_office(office_id)
        counter = svc.get_counter_for_office(office_id)
        active_requests = svc.get_active_requests_for_office(office_id)
        budget = svc.get_budget_snapshot(office_id)
        volunteers = [e for e in svc.get_employees_for_office(office_id) if e.is_volunteer]

        return OfficeDashboardResponse(
            office=office_to_response(office),
            counter=counter_to_response(counter),
            active_requests=[request_to_response(r) for r in active_requests],
            budget=BudgetSnapshotResponse(
                office_id=budget.office_id,
                currency=budget.currency.value,
                monthly_budget=str(budget.monthly_budget),
                actual_spent=str(budget.actual_spent),
                active_reservations=str(budget.active_reservations),
                available=str(budget.available),
            ),
            volunteers=[employee_to_response(v) for v in volunteers],
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── Employees ─────────────────────────────────────────────────────────────────

@router.get(
    "/offices/{office_id}/employees",
    response_model=List[EmployeeResponse],
    tags=["Employees"],
)
def list_employees(office_id: str, svc: SnackService = Depends(get_service)):
    try:
        svc.get_office(office_id)
        return [employee_to_response(e) for e in svc.get_employees_for_office(office_id)]
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/employees", response_model=List[EmployeeResponse], tags=["Employees"])
def list_all_employees(svc: SnackService = Depends(get_service)):
    return [employee_to_response(e) for e in svc.get_all_employees()]


# ── Counter ───────────────────────────────────────────────────────────────────

@router.get(
    "/offices/{office_id}/counter",
    response_model=CounterResponse,
    tags=["Counter"],
)
def get_counter(office_id: str, svc: SnackService = Depends(get_service)):
    try:
        return counter_to_response(svc.get_counter_for_office(office_id))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── Refill Requests ───────────────────────────────────────────────────────────

@router.post(
    "/offices/{office_id}/report-empty",
    response_model=RefillRequestResponse,
    status_code=201,
    tags=["Refill Requests"],
)
def report_empty(
    office_id: str,
    body: ReportEmptyRequest,
    svc: SnackService = Depends(get_service),
):
    """
    Step 1: Report the snack counter as empty.
    Creates a RefillRequest in OPEN state.
    Returns 409 if an active request already exists for this counter.
    """
    try:
        request = svc.report_empty_counter(office_id, body.reported_by)
        return request_to_response(request)
    except ValueError as e:
        msg = str(e)
        # Duplicate active request → 409 Conflict
        if "already active" in msg:
            raise HTTPException(status_code=409, detail=msg)
        raise HTTPException(status_code=400, detail=msg)


@router.get(
    "/offices/{office_id}/requests",
    response_model=List[RefillRequestResponse],
    tags=["Refill Requests"],
)
def list_requests(
    office_id: str,
    active_only: bool = Query(False, description="If true, only return OPEN/ASSIGNED requests"),
    svc: SnackService = Depends(get_service),
):
    try:
        if active_only:
            requests = svc.get_active_requests_for_office(office_id)
        else:
            requests = svc.get_all_requests_for_office(office_id)
        return [request_to_response(r) for r in requests]
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post(
    "/requests/{request_id}/accept",
    response_model=RefillRequestResponse,
    tags=["Refill Requests"],
)
def accept_request(
    request_id: str,
    body: AcceptRequestBody,
    svc: SnackService = Depends(get_service),
):
    """
    Step 2: A volunteer accepts the refill request.
    Returns 409 if the request was already accepted by another volunteer.
    """
    try:
        request = svc.accept_refill_request(
            request_id=request_id,
            volunteer_id=body.volunteer_id,
            estimated_amount=body.estimated_amount,
        )
        return request_to_response(request)
    except ValueError as e:
        msg = str(e)
        if "cannot be accepted" in msg or "Another volunteer" in msg:
            raise HTTPException(status_code=409, detail=msg)
        raise HTTPException(status_code=400, detail=msg)


@router.post(
    "/requests/{request_id}/reject",
    response_model=RefillRequestResponse,
    tags=["Refill Requests"],
)
def reject_request(
    request_id: str,
    body: RejectRequestBody,
    svc: SnackService = Depends(get_service),
):
    """
    Volunteer declines/rejects a refill request, rotating assignment to the
    next volunteer in the office's fairness queue.
    """
    try:
        request = svc.reject_refill_request(
            request_id=request_id,
            volunteer_id=body.volunteer_id,
        )
        return request_to_response(request)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post(
    "/requests/{request_id}/complete",
    response_model=CompletePurchaseResponse,
    tags=["Refill Requests"],
)
def complete_purchase(
    request_id: str,
    body: CompletePurchaseBody,
    svc: SnackService = Depends(get_service),
):
    """
    Step 3: Volunteer records actual purchase and submits receipt.
    - Marks counter as FILLED.
    - Creates a reimbursement claim (PENDING).
    - Submits claim to mock HR system.
    """
    try:
        request, claim = svc.complete_purchase(
            request_id=request_id,
            volunteer_id=body.volunteer_id,
            purchase_amount=body.purchase_amount,
            receipt_reference=body.receipt_reference,
        )
        return CompletePurchaseResponse(
            request=request_to_response(request),
            claim=claim_to_response(claim),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Reimbursement Claims ──────────────────────────────────────────────────────

@router.get(
    "/offices/{office_id}/claims",
    response_model=List[ReimbursementClaimResponse],
    tags=["Claims"],
)
def list_claims(office_id: str, svc: SnackService = Depends(get_service)):
    try:
        svc.get_office(office_id)
        return [claim_to_response(c) for c in svc.get_claims_for_office(office_id)]
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get(
    "/claims",
    response_model=List[ReimbursementClaimResponse],
    tags=["Claims"],
)
def list_all_claims(svc: SnackService = Depends(get_service)):
    return [claim_to_response(c) for c in svc.get_all_claims()]


@router.post(
    "/claims/{claim_id}/approve",
    response_model=ReimbursementClaimResponse,
    tags=["Claims"],
)
def approve_claim(
    claim_id: str,
    body: ClaimActionBody,
    svc: SnackService = Depends(get_service),
):
    try:
        claim = svc.approve_claim(claim_id, body.reviewer_id)
        return claim_to_response(claim)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post(
    "/claims/{claim_id}/reject",
    response_model=ReimbursementClaimResponse,
    tags=["Claims"],
)
def reject_claim(
    claim_id: str,
    body: ClaimActionBody,
    svc: SnackService = Depends(get_service),
):
    if not body.reason:
        raise HTTPException(status_code=400, detail="A rejection reason is required")
    try:
        claim = svc.reject_claim(claim_id, body.reviewer_id, body.reason)
        return claim_to_response(claim)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post(
    "/claims/{claim_id}/reimburse",
    response_model=ReimbursementClaimResponse,
    tags=["Claims"],
)
def confirm_reimbursed(
    claim_id: str,
    body: ConfirmReimbursedBody,
    svc: SnackService = Depends(get_service),
):
    try:
        claim = svc.confirm_reimbursed(claim_id, body.confirmer_id)
        return claim_to_response(claim)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Budget ────────────────────────────────────────────────────────────────────

@router.get(
    "/offices/{office_id}/budget",
    response_model=BudgetSnapshotResponse,
    tags=["Budget"],
)
def get_budget(office_id: str, svc: SnackService = Depends(get_service)):
    try:
        budget = svc.get_budget_snapshot(office_id)
        return BudgetSnapshotResponse(
            office_id=budget.office_id,
            currency=budget.currency.value,
            monthly_budget=str(budget.monthly_budget),
            actual_spent=str(budget.actual_spent),
            active_reservations=str(budget.active_reservations),
            available=str(budget.available),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── Reports ───────────────────────────────────────────────────────────────────

@router.get(
    "/offices/{office_id}/report",
    tags=["Reports"],
)
def monthly_report(
    office_id: str,
    year: int = Query(default=datetime.utcnow().year),
    month: int = Query(default=datetime.utcnow().month),
    svc: SnackService = Depends(get_service),
):
    try:
        return svc.get_monthly_report(office_id, year, month)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── Chat notifications (demo inspection) ─────────────────────────────────────

@router.get(
    "/notifications",
    response_model=NotificationsResponse,
    tags=["Demo"],
)
def get_notifications(svc: SnackService = Depends(get_service)):
    """Returns all mock chat notifications — useful for the demo."""
    return NotificationsResponse(notifications=svc.chat.get_notifications())


# ── HR system state (demo inspection) ────────────────────────────────────────

@router.get("/hr/claims", tags=["Demo"])
def get_hr_claims(svc: SnackService = Depends(get_service)):
    """Returns all claims as seen by the mock HR system."""
    return {"claims": svc.hr.get_all_claims()}
