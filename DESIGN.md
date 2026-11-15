# System Design Document — Snack Counter Automation

## 1. Problem & Goals

Framsikt operates two offices with independent monthly snack budgets:
- **Oslo, Norway:** 6,000 NOK / month (typical purchase: 300–1,200 NOK)
- **Bengaluru, India:** 15,000 INR / month (typical purchase: 500–3,000 INR)

Each office relies on employee volunteers to buy snacks out-of-pocket and submit receipts to HR for reimbursement (which takes 1–10 days). Previously, uncoordinated manual reporting caused:
1. **Empty counters for days** when nobody knew who was responsible.
2. **Duplicate requests and duplicate purchases** when multiple employees acted independently.
3. **Unfair volunteer burden**, with the same few employees doing most shopping trips.
4. **No real-time budget visibility**, risking overspending or mixing currencies.
5. **Coupled mental models** between physical counter state and slow HR reimbursement processing.

**Goal:** Build a reliable, explainable internal web application that coordinates the end-to-end snack refill and reimbursement lifecycle while simulating internal chat notifications and external HR claim processing.

---

## 2. Architecture & Separation of Concerns

The system is a modular monorepo with a **FastAPI (Python)** backend and a **React + TypeScript (Vite)** frontend:

```
Frontend (React + TypeScript)
   │  REST / JSON over HTTP (Decimal values serialized as strings)
   ▼
API Layer (backend/app/routes.py + schemas.py)
   │  Pydantic validation, HTTP status mapping (200/201/400/404/409/422)
   ▼
Service Layer (backend/app/services.py — SnackService)
   │  All domain rules, state transitions, fairness, budget math, RLock acquisition
   ├──► Repositories (backend/app/repositories.py) ──► Domain Models (models.py)
   └──► Mock Integrations (backend/app/integrations.py — MockChatService, MockHRService)
```

- **Domain Models (`models.py`):** Plain Python `@dataclass` entities and `Enum` state machines using `Decimal` for all monetary fields.
- **Repositories (`repositories.py`):** Thin in-memory dictionary stores encapsulated behind `AppRepositories`, which also owns a process-wide `threading.RLock`.
- **Business Logic (`services.py`):** `SnackService` enforces all domain invariants. No business logic resides in route handlers, and no HTTP concerns leak into the service layer.
- **Mock Integrations (`integrations.py`):** `MockChatService` and `MockHRService` record outbound notifications and simulated external HR claims (`HR-XXXXXX`) in memory for UI and test inspection.

---

## 3. Domain Models & State Transitions

### Core Entities & Relationships
- `Office (1) ──< (N) Employee`: Tracks `is_volunteer`, `role` (`EMPLOYEE`, `HR`, `ADMIN`), and `last_assigned_at`.
- `Office (1) ──< (1) Counter`: Tracks physical state (`FILLED` | `EMPTY`) and `last_updated_at`.
- `Counter (1) ──< (N) RefillRequest`: Tracks `reported_by` (any office employee) separately from `assigned_to` (an eligible volunteer), `estimated_amount`, `authorized_amount`, `purchase_amount`, and `reservation_active`.
- `RefillRequest (1) ── (1) ReimbursementClaim`: Created upon purchase completion with `receipt_reference` and `external_hr_claim_id`.

### Independent State Machines
1. **Physical Counter (`CounterStatus`):** `FILLED` ⇄ `EMPTY`
2. **Refill Request (`RefillStatus`):** `OPEN` → `ASSIGNED` → `COMPLETED` (or `CANCELLED`)
3. **Reimbursement Claim (`ClaimStatus`):** `PENDING` → `APPROVED` → `REIMBURSED` (or `PENDING` → `REJECTED`)

### Key Domain Decisions
- **Why Counter Status and Reimbursement Status are Separate:** Counter status answers *"Are there snacks on the shelf right now?"* while claim status answers *"Has finance paid the volunteer back yet?"* When a volunteer completes a purchase, snacks are physically on the counter (`FILLED`), even though HR reimbursement (`PENDING` → `APPROVED` → `REIMBURSED`) takes 1–10 days.
- **Why HR Approval Happens After Purchase:** Requiring HR pre-approval before buying snacks would leave the counter empty for days waiting on administrative review. Instead, budget availability is enforced automatically when a volunteer accepts the request, and HR verifies the receipt retroactively for reimbursement.
- **Why Manual Reporting Instead of Computer Vision / IoT:** Camera-based shelf detection or smart weight sensors introduce hardware cost, calibration failure modes, and privacy concerns for a ~20-person office. A one-click "Report Empty" button with duplicate prevention solves the coordination problem with zero hardware overhead.

---

## 4. Concurrency, Duplicate Prevention & Volunteer Fairness

### Duplicate Active Request Prevention
A counter can have many historical `COMPLETED` requests, but at most **one** active (`OPEN` or `ASSIGNED`) request at any time. Inside `report_empty_counter`, the service acquires `repos.lock`, checks `find_active_for_counter(counter_id)`, and raises a conflict (mapped to HTTP `409 Conflict`) if one exists.

### Concurrent Volunteer Acceptance
If two volunteers click "Accept" at the same millisecond:
1. Both requests enter `accept_refill_request` and contend for `self.repos.lock` (`threading.RLock`).
2. Thread A acquires the lock first, verifies `request.status == RefillStatus.OPEN`, transitions it to `ASSIGNED`, reserves the budget, and releases the lock.
3. Thread B acquires the lock next, sees `request.status == RefillStatus.ASSIGNED`, and fails fast with a `409 Conflict` message.
- **Timestamps vs. Locks:** Timestamps (`created_at`, `assigned_at`, `last_assigned_at`) are used strictly for audit trails and fairness sorting, never as a substitute for mutual exclusion.

### Fair Volunteer Rotation & Reject-to-Next Queue
When a counter is reported empty, `_get_eligible_volunteers(office_id)` filters for office employees with `is_volunteer=True` and sorts them by:
1. **Never assigned first** (`last_assigned_at is None`).
2. **Least recently assigned next** (oldest `last_assigned_at` timestamp).
When a volunteer accepts a request, their `last_assigned_at` is updated to `datetime.utcnow()`, rotating them to the back of the queue. If a volunteer chooses to **Reject / Pass** (`POST /api/requests/{id}/reject`), the service records them in `request.declined_by`, releases any active budget reservation, and automatically assigns the request to the **next volunteer in the fairness queue** (`eligible[0]` excluding `declined_by`).

---

## 5. Budget Reservation, Reconciliation & Currency Isolation

### Exact Decimal Math & Reservation Formula
All monetary values use `decimal.Decimal` (never `float`) and serialize as strings in JSON to avoid IEEE-754 precision issues. For each office:
$$\text{Available Budget} = \text{Monthly Budget} - \text{Actual Spent} - \text{Active Reservations}$$

1. **On Accept (`OPEN` → `ASSIGNED`):** We verify `estimated_amount <= available_budget`. We add `estimated_amount` to `active_reservations`, set `authorized_amount = estimated_amount`, and set `request.reservation_active = True`.
2. **On Purchase Completion (`ASSIGNED` → `COMPLETED`):**
   - We validate `0 < purchase_amount <= authorized_amount`. (The estimate acts as an authorized spending ceiling for that trip; if snacks cost more than estimated, the volunteer must request a higher authorization rather than silently overspending.)
   - **Single Release Guard:** If `request.reservation_active` is `True`, we subtract `estimated_amount` from `active_reservations` and flip `reservation_active = False`.
   - **Single Spend Record:** We add `purchase_amount` to `actual_spent` exactly once.
   - Reservation and actual spend are never double-counted.

### Strict Currency Separation
Oslo (`NOK`) and Bengaluru (`INR`) budgets, requests, claims, and monthly reports are keyed by `office_id` and tagged with their `Currency` enum. No endpoint or UI component ever adds `NOK` and `INR` together.

---

## 6. Trade-offs & Production Improvements

| Area | Take-Home Implementation | Production Recommendation |
|---|---|---|
| **Persistence** | In-memory Python dicts (`repositories.py`) seeded on startup. Fast and zero-dependency, but resets on server restart. | **PostgreSQL** with SQLAlchemy/SQLModel and Alembic migrations. |
| **Concurrency** | `threading.RLock` protects threads within a **single Python process**. Would not protect across multiple Gunicorn/Uvicorn worker processes. | PostgreSQL transactions with `SELECT ... FOR UPDATE` row locking and a partial unique index: `CREATE UNIQUE INDEX one_active_req_per_counter ON refill_requests(counter_id) WHERE status IN ('OPEN', 'ASSIGNED');`. |
| **Authorized Ceiling** | Rejects `purchase_amount > authorized_amount` to prevent silent budget overruns when multiple requests are active. | Allow a small configurable tolerance (e.g., +10%) if `available_budget` permits, or a Slack approval button to bump the ceiling. |
| **Integrations** | `MockChatService` & `MockHRService` in memory. | Slack/Teams webhook + interactive buttons (`/accept`), and Tripletex/Visma API with idempotency keys and webhook callbacks. |
| **Auth** | Demo user selector in the UI header. | Company SSO (OIDC / Entra ID / Google Workspace) with JWT role claims. |
