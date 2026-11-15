"""
Seed data for the demo.

This populates the in-memory repositories with realistic starting data
covering both offices (Oslo and Bengaluru) so the full workflow can be
demonstrated without manual setup.

Why manual seed data instead of fixtures?
- The app runs with no database, so there's no migration system.
- Seed data is loaded once at startup via main.py.
- Tests use their own isolated repos (see conftest.py) and do NOT use this data.

Demo credentials (all simulated, no real auth):
- Oslo employees: 3 volunteers, 2 non-volunteers, 1 HR
- Bengaluru employees: 3 volunteers, 2 non-volunteers, 1 HR
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from app.integrations import MockHRService, MockChatService
from app.models import (
    Counter,
    CounterStatus,
    Currency,
    Employee,
    EmployeeRole,
    Office,
    generate_id,
)
from app.repositories import AppRepositories


def seed_data(repos: AppRepositories, chat: MockChatService, hr: MockHRService) -> dict:
    """
    Populate repos with demo data. Returns IDs for reference.
    Called once at application startup.
    """
    ids = {}

    # ── Offices ──────────────────────────────────
    oslo = Office(
        id="oslo",
        name="Oslo",
        currency=Currency.NOK,
        monthly_budget=Decimal("6000"),
    )
    bengaluru = Office(
        id="bengaluru",
        name="Bengaluru",
        currency=Currency.INR,
        monthly_budget=Decimal("15000"),
    )
    repos.offices.add(oslo)
    repos.offices.add(bengaluru)

    # ── Budgets ───────────────────────────────────
    repos.budgets.initialize("oslo", Decimal("6000"), Currency.NOK)
    repos.budgets.initialize("bengaluru", Decimal("15000"), Currency.INR)

    # ── Oslo Counters ─────────────────────────────
    oslo_counter = Counter(id="counter-oslo", office_id="oslo", status=CounterStatus.FILLED)
    repos.counters.add(oslo_counter)
    ids["oslo_counter"] = oslo_counter.id

    # ── Bengaluru Counter ─────────────────────────
    blr_counter = Counter(id="counter-blr", office_id="bengaluru", status=CounterStatus.FILLED)
    repos.counters.add(blr_counter)
    ids["blr_counter"] = blr_counter.id

    # ── Oslo Employees ────────────────────────────
    oslo_hr = Employee(
        id="oslo-hr-01",
        name="Ingrid Hansen",
        email="ingrid.hansen@framsikt.no",
        office_id="oslo",
        is_volunteer=False,
        role=EmployeeRole.HR,
    )

    oslo_vol1 = Employee(
        id="oslo-v01",
        name="Erik Larsen",
        email="erik.larsen@framsikt.no",
        office_id="oslo",
        is_volunteer=True,
        role=EmployeeRole.EMPLOYEE,
        # Assigned 10 days ago — not the most recently assigned
        last_assigned_at=datetime.utcnow() - timedelta(days=10),
    )

    oslo_vol2 = Employee(
        id="oslo-v02",
        name="Astrid Berg",
        email="astrid.berg@framsikt.no",
        office_id="oslo",
        is_volunteer=True,
        role=EmployeeRole.EMPLOYEE,
        # Assigned 3 days ago — most recently assigned (fairness penalizes this)
        last_assigned_at=datetime.utcnow() - timedelta(days=3),
    )

    oslo_vol3 = Employee(
        id="oslo-v03",
        name="Jonas Dahl",
        email="jonas.dahl@framsikt.no",
        office_id="oslo",
        is_volunteer=True,
        role=EmployeeRole.EMPLOYEE,
        last_assigned_at=None,  # Never assigned — should be preferred
    )

    oslo_emp1 = Employee(
        id="oslo-e01",
        name="Marte Olsen",
        email="marte.olsen@framsikt.no",
        office_id="oslo",
        is_volunteer=False,
        role=EmployeeRole.EMPLOYEE,
    )

    oslo_emp2 = Employee(
        id="oslo-e02",
        name="Ola Nordmann",
        email="ola.nordmann@framsikt.no",
        office_id="oslo",
        is_volunteer=False,
        role=EmployeeRole.EMPLOYEE,
    )

    for emp in [oslo_hr, oslo_vol1, oslo_vol2, oslo_vol3, oslo_emp1, oslo_emp2]:
        repos.employees.add(emp)

    ids["oslo_hr"] = oslo_hr.id
    ids["oslo_vol1"] = oslo_vol1.id
    ids["oslo_vol2"] = oslo_vol2.id
    ids["oslo_vol3"] = oslo_vol3.id

    # ── Bengaluru Employees ───────────────────────
    blr_hr = Employee(
        id="blr-hr-01",
        name="Priya Sharma",
        email="priya.sharma@framsikt.in",
        office_id="bengaluru",
        is_volunteer=False,
        role=EmployeeRole.HR,
    )

    blr_vol1 = Employee(
        id="blr-v01",
        name="Arjun Nair",
        email="arjun.nair@framsikt.in",
        office_id="bengaluru",
        is_volunteer=True,
        role=EmployeeRole.EMPLOYEE,
        last_assigned_at=datetime.utcnow() - timedelta(days=5),
    )

    blr_vol2 = Employee(
        id="blr-v02",
        name="Divya Menon",
        email="divya.menon@framsikt.in",
        office_id="bengaluru",
        is_volunteer=True,
        role=EmployeeRole.EMPLOYEE,
        last_assigned_at=None,  # Never assigned — preferred
    )

    blr_vol3 = Employee(
        id="blr-v03",
        name="Rahul Verma",
        email="rahul.verma@framsikt.in",
        office_id="bengaluru",
        is_volunteer=True,
        role=EmployeeRole.EMPLOYEE,
        last_assigned_at=datetime.utcnow() - timedelta(days=1),
    )

    blr_emp1 = Employee(
        id="blr-e01",
        name="Sneha Pillai",
        email="sneha.pillai@framsikt.in",
        office_id="bengaluru",
        is_volunteer=False,
        role=EmployeeRole.EMPLOYEE,
    )

    blr_emp2 = Employee(
        id="blr-e02",
        name="Kiran Reddy",
        email="kiran.reddy@framsikt.in",
        office_id="bengaluru",
        is_volunteer=False,
        role=EmployeeRole.EMPLOYEE,
    )

    for emp in [blr_hr, blr_vol1, blr_vol2, blr_vol3, blr_emp1, blr_emp2]:
        repos.employees.add(emp)

    ids["blr_hr"] = blr_hr.id
    ids["blr_vol1"] = blr_vol1.id
    ids["blr_vol2"] = blr_vol2.id
    ids["blr_vol3"] = blr_vol3.id

    return ids
