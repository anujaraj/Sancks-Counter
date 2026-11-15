# System Design Document — Snack Counter Automation

## 1. How a Refill Moves from "Empty" to "Reimbursed" (End-to-End Lifecycle)

Framsikt operates two offices with independent monthly snack budgets:
- **Oslo, Norway:** `6,000 NOK` / month (typical purchase: `300–1,200 NOK`)
- **Bengaluru, India:** `15,000 INR` / month (typical purchase: `500–3,000 INR`)

Physical counter state (`CounterStatus`: `FILLED` ⇄ `EMPTY`), refill workflow state (`RefillStatus`: `OPEN` → `ASSIGNED` → `COMPLETED`), and HR reimbursement state (`ClaimStatus`: `PENDING` → `APPROVED` / `REJECTED` → `REIMBURSED`) are modeled as **three decoupled state machines**:

1. **Report Empty (`FILLED` → `EMPTY`, Request `OPEN`):**
   - Any office employee clicks **Report Empty** (`POST /api/offices/{id}/report-empty`).
   - Inside `repos.lock` (`threading.RLock`), `SnackService.report_empty_counter` verifies no `OPEN` or `ASSIGNED` request already exists for that counter (returning `409 Conflict` if one is active), marks the physical counter `EMPTY`, creates an `OPEN` `RefillRequest`, and notifies eligible office volunteers via `MockChatService` ordered by fairness (never-assigned first, then oldest `last_assigned_at`).
2. **Volunteer Acceptance & Budget Reservation (`OPEN` → `ASSIGNED`) — or Handoff (`/reject`):**
   - An eligible office volunteer accepts (`POST /api/requests/{id}/accept`) with an `estimated_amount`.
   - Under `repos.lock`, the service verifies the request is still `OPEN` and `estimated_amount <= available_budget`, transitions the request to `ASSIGNED`, reserves `estimated_amount` immediately (`reservation_active = True`, `authorized_amount = estimated_amount`), and updates the volunteer's `last_assigned_at`.
   - **Volunteer Handoff (`POST /api/requests/{id}/reject`):** If a volunteer is unavailable or cannot complete an accepted refill, calling `reject_refill_request` releases any active budget reservation, records the volunteer in `declined_by` (preventing them from re-accepting or completing it after handoff), and assigns the request to the next volunteer in the office's fairness queue.
3. **Purchase Completion & Immediate Counter Refill (`ASSIGNED` → `COMPLETED`, Counter `FILLED`, Claim `PENDING`):**
   - After buying snacks, the assigned volunteer submits the actual `purchase_amount` and `receipt_reference` (`POST /api/requests/{id}/complete`).
   - The service validates `0 < purchase_amount <= authorized_amount`, submits the claim to `MockHRService` (receiving an external ID `HR-XXXXXX`), releases the budget reservation **exactly once** (`reservation_active = False`), records `actual_spent` **exactly once**, marks the request `COMPLETED`, and immediately marks the physical counter **`FILLED`** so employees know snacks are available right away without waiting 1–10 days for HR.
4. **HR Review & Reimbursement (`PENDING` → `APPROVED` / `REJECTED` → `REIMBURSED`):**
   - An HR/Admin user approves (`POST /api/claims/{id}/approve`) or rejects with a mandatory reason (`POST /api/claims/{id}/reject`).
   - Once finance transfers the payout in the employee's payroll currency, HR confirms reimbursement (`POST /api/claims/{id}/reimburse`), transitioning the claim from `APPROVED` to `REIMBURSED`.

---

## 2. Main Solution Components & the Data Each Part Keeps

```text
React + TypeScript UI (EmployeeDashboard.tsx, HRDashboard.tsx)
   │  REST / JSON over HTTP (all monetary Decimals serialized as strings)
   ▼
API & Schemas Layer (backend/app/routes.py, schemas.py)
   │  Pydantic validation; maps domain ValueError to HTTP 400 / 404 / 409
   ▼
Service Layer (backend/app/services.py — SnackService)
   │  Enforces all business rules, fairness rotation, budget math & RLock
   ├──► In-Memory Repositories (repositories.py) + Domain Models (models.py)
   └──► Simulated Stand-Ins (integrations.py — MockChatService, MockHRService)
```

| Component | Source Files | Data Kept & Responsibility |
|---|---|---|
| **Domain Models** | `backend/app/models.py` | Typed `@dataclass` entities and `Enum` statuses using `decimal.Decimal` for all money fields: `Office` (`id`, `name`, `currency`, `monthly_budget`), `Employee` (`id`, `office_id`, `is_volunteer`, `role`, `last_assigned_at`), `Counter` (`id`, `office_id`, `status`), `RefillRequest` (`reported_by`, `assigned_to`, `declined_by`, `status`, `estimated_amount`, `authorized_amount`, `purchase_amount`, `reservation_active`), `ReimbursementClaim` (`amount`, `currency`, `receipt_reference`, `status`, `external_hr_claim_id`), and `BudgetSnapshot`. |
| **Repositories & Lock** | `backend/app/repositories.py` | `AppRepositories` holds isolated per-entity dicts (`offices`, `employees`, `counters`, `refill_requests`, `claims`), `BudgetRepository` (`_monthly_budgets`, `_actual_spent`, `_active_reservations` per office), and a shared `threading.RLock` (`repos.lock`). |
| **Core Service Layer** | `backend/app/services.py` | Stateless `SnackService` orchestrating the refill lifecycle, single-winner locking, volunteer handoff, two-phase budget reconciliation, HR claim transitions, and currency-isolated monthly reports (`get_monthly_report` and `get_combined_monthly_report`). |
| **Mock Integrations** | `backend/app/integrations.py` | `MockChatService` keeps `_notifications` (`VOLUNTEER_NEEDED`, `ASSIGNMENT_CONFIRMED` with recipient order). `MockHRService` keeps `_claims` keyed by external ID (`HR-XXXXXX`) with status (`SUBMITTED`, `APPROVED`, `REJECTED`, `REIMBURSED`). |
| **Frontend UI** | `frontend/src/*` | Interactive Oslo (`NOK`) and Bengaluru (`INR`) dashboard, persona switcher, live counter & budget cards, refill/handoff actions, HR review table, and cross-office monthly spending report. |

---

## 3. How the Original Problems Are Prevented

1. **Counters Sitting Empty for Days → One-Click Reporting & Immediate Volunteer Notification:**
   - Any employee can report an empty counter, immediately broadcasting a `VOLUNTEER_NEEDED` alert to eligible volunteers in that office. Because HR approval happens *after* purchase (retroactively for reimbursement), volunteers can buy snacks immediately.
2. **Two People Buying Snacks on the Same Day → Atomic Single-Request & Single-Winner Locking:**
   - `report_empty_counter` blocks duplicate reports whenever a request is `OPEN` or `ASSIGNED` (`409 Conflict`).
   - `accept_refill_request` acquires `self.repos.lock` (`threading.RLock`) and verifies `request.status == RefillStatus.OPEN`. If two volunteers click Accept concurrently, exactly one thread transitions the request to `ASSIGNED`; the second receives `409 Conflict`.
3. **A Few People Doing Most Refills (or Going on Holiday) → Fairness Queue & Handoff:**
   - `_get_eligible_volunteers` sorts office volunteers by `(last_assigned_at is not None, last_assigned_at)` so never-assigned volunteers come first, followed by those assigned longest ago.
   - If a volunteer is working from home, on holiday, or unable to complete an accepted refill, `reject_refill_request` releases any reserved budget, records them in `declined_by`, blocks them from completing the handed-over request, and reassigns the next volunteer in the fairness queue.
   - Visiting employees from another office are blocked from accepting requests outside their home office (`volunteer.office_id != request.office_id`), ensuring employees are always reimbursed in their own payroll currency.
4. **No Visibility into Monthly Spending → Two-Phase `Decimal` Budget & Multi-Currency Reporting:**
   - All money calculations use Python `Decimal` (never `float`) and serialize as strings in JSON.
   - Available budget is tracked in real time as:
     $$\text{Available Budget} = \text{Monthly Budget} - \text{Actual Spent} - \text{Active Reservations}$$
   - Accepting a request reserves `estimated_amount`; completing a purchase checks `purchase_amount <= authorized_amount`, releases the reservation once (`reservation_active` guard), and increments `actual_spent` once.
   - `get_combined_monthly_report` (`GET /api/reports/monthly`) and the HR Dashboard provide management with one unified monthly report across both offices while keeping `NOK` and `INR` totals strictly separate (`totals_by_currency: {"NOK": ..., "INR": ...}`).

---

## 4. Trade-Offs Made & What We Deliberately Did Not Build

### Key Engineering Trade-Offs
- **In-Memory Repositories vs. Relational Database:** Using in-memory Python dictionaries seeded at startup gives zero-config execution and sub-second test runs (70 tests in ~2.6s), at the cost of state resetting when the FastAPI process restarts. In production, this would be backed by PostgreSQL.
- **Process-Wide `threading.RLock` vs. Distributed DB Locks:** `threading.RLock` guarantees thread safety within a single Python process, which is ideal for a single-instance server and multi-threaded tests, but does not coordinate across multiple Gunicorn/Uvicorn worker processes. In production, we would use PostgreSQL `SELECT ... FOR UPDATE` row locks and a partial unique index `WHERE status IN ('OPEN', 'ASSIGNED')`.
- **Hard Authorized Ceiling (`purchase_amount <= authorized_amount`):** Rejecting purchases above the estimated ceiling prevents silent budget overruns when multiple requests are active, though it requires volunteers to estimate a comfortable upper bound.
- **Receipt Reference String vs. Binary File Storage:** Accepting a non-empty receipt reference (invoice code or document URL) validates the audit trail without coupling the core transactional ledger to S3/multipart file upload plumbing.

### What We Deliberately Did Not Build
1. **Computer Vision / Smart Shelf Weight Sensors:** Automatic empty detection via cameras or IoT scales adds hardware cost, calibration failure modes, and employee privacy concerns for a ~15–20 person counter. A one-click "Report Empty" button with duplicate protection solves the coordination problem cleanly.
2. **Pre-Purchase HR Approval Gate:** Requiring HR to approve every refill *before* a volunteer can buy snacks would leave the counter empty for 1–10 days. Instead, the system automatically checks budget availability upon volunteer acceptance and routes receipts to HR after the purchase.
3. **Cross-Currency FX Conversion:** We deliberately never convert or sum `NOK` and `INR` into a single number, because daily FX fluctuations distort local office budget tracking and employees are reimbursed strictly in their local payroll currency.
4. **Item-Level Inventory / Barcode Tracking ("Which Snacks Get Eaten"):** Tracking individual chocolate bars or fruit SKUs would force employees to log every snack they eat, creating high friction and low compliance. Tracking refill frequency and receipt totals per office gives management spending visibility without policing snack consumption.
5. **Automated Grocery Ordering / Delivery API Integration:** Snack variety preferences change week to week; coordinating human volunteers is more flexible than hardcoding vendor integrations with local Norwegian and Indian grocers.
