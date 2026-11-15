"""
Shared test fixtures.

Design: each test gets a FRESH set of repositories, services, and integrations.
This means tests are fully isolated — no state bleeds between them.

Why isolated fixtures?
- If Test A creates a refill request and Test B checks "no active requests",
  B would fail without isolation.
- With isolated repos, every test starts from a known, clean state.
- This makes tests deterministic and order-independent.

How it works:
- The `app_repos` fixture creates a new AppRepositories instance.
- The `svc` fixture creates a new SnackService with that repos instance.
- Each fixture is function-scoped (default), so it's re-created for every test.
"""

import pytest
from decimal import Decimal
from datetime import datetime, timedelta

from app.integrations import MockChatService, MockHRService
from app.models import (
    Counter,
    CounterStatus,
    Currency,
    Employee,
    EmployeeRole,
    Office,
)
from app.repositories import AppRepositories
from app.services import SnackService


@pytest.fixture
def app_repos():
    """Fresh in-memory repositories for each test."""
    return AppRepositories()


@pytest.fixture
def chat():
    """Fresh mock chat service."""
    return MockChatService()


@pytest.fixture
def hr():
    """Fresh mock HR service."""
    return MockHRService()


@pytest.fixture
def svc(app_repos, chat, hr):
    """SnackService wired to isolated repos and mocks."""
    return SnackService(app_repos, chat, hr)


# ── Helpers to set up minimal test data ──────────────────────────────────────

def make_oslo_office(repos: AppRepositories) -> Office:
    office = Office(
        id="oslo",
        name="Oslo",
        currency=Currency.NOK,
        monthly_budget=Decimal("6000"),
    )
    repos.offices.add(office)
    repos.budgets.initialize("oslo", Decimal("6000"), Currency.NOK)
    return office


def make_blr_office(repos: AppRepositories) -> Office:
    office = Office(
        id="bengaluru",
        name="Bengaluru",
        currency=Currency.INR,
        monthly_budget=Decimal("15000"),
    )
    repos.offices.add(office)
    repos.budgets.initialize("bengaluru", Decimal("15000"), Currency.INR)
    return office


def make_counter(repos: AppRepositories, office_id: str, counter_id: str = None) -> Counter:
    counter = Counter(
        id=counter_id or f"counter-{office_id}",
        office_id=office_id,
        status=CounterStatus.FILLED,
    )
    repos.counters.add(counter)
    return counter


def make_employee(
    repos: AppRepositories,
    employee_id: str,
    office_id: str,
    is_volunteer: bool = False,
    role: EmployeeRole = EmployeeRole.EMPLOYEE,
    last_assigned_at: datetime = None,
) -> Employee:
    emp = Employee(
        id=employee_id,
        name=f"Test Employee {employee_id}",
        email=f"{employee_id}@test.com",
        office_id=office_id,
        is_volunteer=is_volunteer,
        role=role,
        last_assigned_at=last_assigned_at,
    )
    repos.employees.add(emp)
    return emp


def make_hr_employee(repos: AppRepositories, office_id: str, emp_id: str = "hr-01") -> Employee:
    return make_employee(repos, emp_id, office_id, is_volunteer=False, role=EmployeeRole.HR)
