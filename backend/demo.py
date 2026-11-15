"""
End-to-end CLI demo for Snack Counter Automation.

Run from the `backend` directory:
    .venv\\Scripts\\python.exe demo.py

Demonstrates:
1. Seeded offices (Oslo in NOK, Bengaluru in INR) and volunteer fairness order.
2. Employee reporting an empty counter (and mock chat notification).
3. Duplicate active refill request prevention.
4. Concurrent volunteer acceptance protected by threading.RLock.
5. Budget reservation on accept and exact single-release reconciliation on purchase completion.
6. Physical counter immediately becoming FILLED while HR claim starts as PENDING.
7. HR claim approval, rejection, and reimbursement confirmation.
8. Currency-isolated monthly spending reports (NOK and INR never combined).
"""

from __future__ import annotations

import threading
from datetime import datetime
from decimal import Decimal

from app.integrations import MockChatService, MockHRService
from app.repositories import AppRepositories
from app.seed import seed_data
from app.services import SnackService


def print_header(title: str) -> None:
    print(f"\n{'=' * 72}")
    print(f"  {title}")
    print(f"{'=' * 72}")


def run_demo() -> None:
    repos = AppRepositories()
    chat = MockChatService()
    hr = MockHRService()
    seed_data(repos, chat, hr)
    svc = SnackService(repos, chat, hr)

    print_header("1. INITIAL SEEDED STATE & VOLUNTEER FAIRNESS ORDER")
    for office in svc.get_offices():
        counter = svc.get_counter_for_office(office.id)
        budget = svc.get_budget_snapshot(office.id)
        vols = svc._get_eligible_volunteers(office.id)
        print(
            f"Office: {office.name:10s} | Counter: {counter.status.value:6s} | "
            f"Budget: {budget.available}/{budget.monthly_budget} {budget.currency.value}"
        )
        print("  Fairness queue: " + " -> ".join(
            f"{v.name} ({'never' if not v.last_assigned_at else v.last_assigned_at.strftime('%Y-%m-%d')})"
            for v in vols
        ))

    print_header("2. REPORT EMPTY COUNTER & DUPLICATE PREVENTION (OSLO)")
    req_oslo = svc.report_empty_counter("oslo", "oslo-e01")  # Marte Olsen (non-volunteer)
    counter_after_report = svc.get_counter_for_office("oslo")
    print(f"[OK] Marte Olsen reported Oslo counter empty -> Request #{req_oslo.id} ({req_oslo.status.value})")
    print(f"[OK] Oslo physical counter status is now: {counter_after_report.status.value}")

    last_notif = chat.get_notifications()[-1]
    print(f"[CHAT] Notified volunteers in fairness order: {[name for _, name in last_notif['recipients']]}")

    try:
        svc.report_empty_counter("oslo", "oslo-e02")  # Ola Nordmann tries duplicate report
    except ValueError as exc:
        print(f"[BLOCKED DUPLICATE] Second empty report prevented: {exc}")

    print_header("3. CONCURRENT VOLUNTEER ACCEPTANCE & BUDGET RESERVATION (OSLO)")
    winners: list[str] = []
    rejections: list[str] = []

    def attempt_accept(vol_id: str, estimate: Decimal) -> None:
        try:
            svc.accept_refill_request(req_oslo.id, vol_id, estimate)
            winners.append(vol_id)
        except ValueError as exc:
            rejections.append(f"{vol_id}: {exc}")

    t1 = threading.Thread(target=attempt_accept, args=("oslo-v03", Decimal("1000")))
    t2 = threading.Thread(target=attempt_accept, args=("oslo-v01", Decimal("900")))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    winner_id = winners[0]
    winner_emp = repos.employees.get(winner_id)
    oslo_budget_reserved = svc.get_budget_snapshot("oslo")
    print(f"[CONCURRENCY] Winner: {winner_emp.name} ({winner_id})")
    print(f"[CONCURRENCY] Rejected concurrent attempt -> {rejections[0]}")
    print(
        f"[BUDGET] Oslo after accept -> Monthly: {oslo_budget_reserved.monthly_budget} NOK, "
        f"Reserved: {oslo_budget_reserved.active_reservations} NOK, "
        f"Actual Spent: {oslo_budget_reserved.actual_spent} NOK, "
        f"Available: {oslo_budget_reserved.available} NOK"
    )

    print_header("4. COMPLETE PURCHASE — COUNTER FILLED vs CLAIM PENDING (OSLO)")
    req_oslo, claim_oslo = svc.complete_purchase(
        request_id=req_oslo.id,
        volunteer_id=winner_id,
        purchase_amount=Decimal("850"),
        receipt_reference="RECEIPT-OSLO-2026-001",
    )
    oslo_counter_filled = svc.get_counter_for_office("oslo")
    oslo_budget_reconciled = svc.get_budget_snapshot("oslo")
    print(f"[OK] Purchase completed by {winner_emp.name} for {req_oslo.purchase_amount} NOK")
    print(f"[STATE SEPARATION] Physical Counter: {oslo_counter_filled.status.value} | HR Claim #{claim_oslo.id}: {claim_oslo.status.value} (Ext Ref: {claim_oslo.external_hr_claim_id})")
    print(
        f"[BUDGET RECONCILED] Reserved: {oslo_budget_reconciled.active_reservations} NOK | "
        f"Actual Spent: {oslo_budget_reconciled.actual_spent} NOK | "
        f"Available: {oslo_budget_reconciled.available} NOK"
    )

    print_header("5. BENGALURU WORKFLOW (INR CURRENCY ISOLATION)")
    req_blr = svc.report_empty_counter("bengaluru", "blr-e01")  # Sneha Pillai
    svc.accept_refill_request(req_blr.id, "blr-v02", Decimal("2500"))  # Divya Menon
    req_blr, claim_blr = svc.complete_purchase(
        request_id=req_blr.id,
        volunteer_id="blr-v02",
        purchase_amount=Decimal("2200"),
        receipt_reference="RECEIPT-BLR-2026-001",
    )
    blr_budget = svc.get_budget_snapshot("bengaluru")
    print(f"[OK] Bengaluru purchase completed: {req_blr.purchase_amount} INR (Claim #{claim_blr.id}, Ext Ref: {claim_blr.external_hr_claim_id})")
    print(
        f"[BUDGET] Bengaluru -> Monthly: {blr_budget.monthly_budget} INR | "
        f"Reserved: {blr_budget.active_reservations} INR | "
        f"Actual Spent: {blr_budget.actual_spent} INR | "
        f"Available: {blr_budget.available} INR"
    )

    print_header("6. HR APPROVAL & REIMBURSEMENT LIFECYCLE")
    svc.approve_claim(claim_oslo.id, "oslo-hr-01")
    print(f"[HR] Ingrid Hansen approved Oslo claim #{claim_oslo.id} -> Status: {claim_oslo.status.value}")
    svc.confirm_reimbursed(claim_oslo.id, "oslo-hr-01")
    print(f"[HR] Ingrid Hansen confirmed reimbursement for #{claim_oslo.id} -> Status: {claim_oslo.status.value}")

    svc.approve_claim(claim_blr.id, "blr-hr-01")
    print(f"[HR] Priya Sharma approved Bengaluru claim #{claim_blr.id} -> Status: {claim_blr.status.value}")

    print_header("7. MONTHLY SPENDING REPORTS (SEPARATE CURRENCIES)")
    now = datetime.utcnow()
    for office_id in ("oslo", "bengaluru"):
        report = svc.get_monthly_report(office_id, now.year, now.month)
        print(
            f"Report [{report['office_name']} - {report['currency']}]: "
            f"Spent={report['total_spent']} {report['currency']}, "
            f"Available={report['available_budget']}/{report['monthly_budget']} {report['currency']}, "
            f"Refills={report['completed_refills']}, "
            f"Claims(Pending={report['claims_pending']}, Approved={report['claims_approved']}, Reimbursed={report['claims_reimbursed']})"
        )

    print("\n✅ Demo workflow completed successfully!")


if __name__ == "__main__":
    run_demo()
