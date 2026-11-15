# Snack Counter Automation — Framsikt

An internal coordination and expense-tracking web application for managing office snack counter refills, volunteer fairness rotation, monthly budgets, and HR reimbursement claims across Framsikt's **Oslo (NOK)** and **Bengaluru (INR)** offices.

---

## 1. Features & Workflow

1. **Report Empty Counter:** Any employee in an office can report their snack counter as `EMPTY`, creating an `OPEN` refill request.
2. **Duplicate Prevention:** Only one active (`OPEN` or `ASSIGNED`) refill request can exist per counter at a time. Concurrent or duplicate reports return HTTP `409 Conflict` with a friendly message.
3. **Fair Volunteer Rotation & Mock Chat Notification:** Eligible office volunteers are notified via `MockChatService` ordered by fairness (**never-assigned first**, then **least-recently-assigned**).
4. **Thread-Safe Volunteer Acceptance & Budget Reservation:** When a volunteer accepts a request with an estimated amount, a process-wide `threading.RLock` guarantees only one volunteer wins even under concurrent requests. The estimated amount is immediately reserved from the office's monthly budget.
5. **Purchase Completion & Immediate Counter Refill:** When the volunteer records the actual purchase amount and receipt reference:
   - The reservation is released **exactly once** and actual spending is recorded **exactly once** (using Python `Decimal`, never `float`).
   - The physical counter immediately transitions to `FILLED` (without waiting for HR approval).
   - A reimbursement claim is submitted to `MockHRService` in `PENDING` state with an external HR reference (`HR-XXXXXX`).
6. **HR Reimbursement Lifecycle:** HR users can review claims (`PENDING` → `APPROVED` or `REJECTED` with a mandatory reason) and confirm payout (`APPROVED` → `REIMBURSED`).
7. **Currency-Isolated Monthly Reporting:** Oslo (`6,000 NOK/month`) and Bengaluru (`15,000 INR/month`) budgets and reports are tracked independently and never combined.

---

## 2. Project Structure

```text
Snacks_counter/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── models.py          # Dataclasses & Enums (Decimal money fields)
│   │   ├── repositories.py    # Thread-safe in-memory repositories + shared RLock
│   │   ├── integrations.py    # MockChatService and MockHRService
│   │   ├── services.py        # Core business logic (SnackService)
│   │   ├── schemas.py         # Pydantic request/response schemas
│   │   ├── routes.py          # Thin FastAPI REST endpoints
│   │   └── seed.py            # Demo seed data for Oslo & Bengaluru
│   ├── tests/
│   │   ├── conftest.py        # Isolated per-test fixtures
│   │   ├── test_refill.py     # Duplicate prevention, eligibility, concurrency, fairness
│   │   ├── test_budget.py     # Reservation, reconciliation, no double-release, currencies
│   │   ├── test_hr.py         # Claim lifecycle, receipt validation, monthly reports
│   │   └── test_api.py        # FastAPI TestClient endpoint integration tests
│   ├── main.py                # FastAPI application entry point + CORS + startup seed
│   ├── demo.py                # End-to-end CLI workflow demonstration script
│   └── requirements.txt       # Python dependencies
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── EmployeeDashboard.tsx  # Counter, budget, requests, claims, chat, volunteers
│   │   │   └── HRDashboard.tsx        # Cross-office claim review & monthly reports
│   │   ├── api.ts             # Typed REST API client
│   │   ├── types.ts           # TypeScript interfaces
│   │   ├── App.tsx            # Office, view mode, and demo persona switcher
│   │   ├── index.css          # Styling
│   │   └── main.tsx           # React entry point
│   ├── index.html
│   ├── package.json
│   └── vite.config.ts
├── DESIGN.md                  # Architecture & engineering design document
├── README.md                  # Setup and usage guide
└── .gitignore
```

---

## 3. Seeded Demo Personas

Authentication is simulated via the **"You are / HR User"** selector in the top navigation bar:

| Office | Name | ID | Role | Volunteer? | Initial Fairness State |
|---|---|---|---|---|---|
| **Oslo** (`NOK`) | Jonas Dahl | `oslo-v03` | `EMPLOYEE` | Yes | ⭐ Never assigned (1st in queue) |
| **Oslo** (`NOK`) | Erik Larsen | `oslo-v01` | `EMPLOYEE` | Yes | Assigned 10 days ago (2nd) |
| **Oslo** (`NOK`) | Astrid Berg | `oslo-v02` | `EMPLOYEE` | Yes | Assigned 3 days ago (3rd) |
| **Oslo** (`NOK`) | Marte Olsen | `oslo-e01` | `EMPLOYEE` | No | Regular employee (can report empty) |
| **Oslo** (`NOK`) | Ola Nordmann | `oslo-e02` | `EMPLOYEE` | No | Regular employee (can report empty) |
| **Oslo** (`NOK`) | Ingrid Hansen | `oslo-hr-01` | `HR` | No | HR reviewer (approves/rejects/reimburses) |
| **Bengaluru** (`INR`) | Divya Menon | `blr-v02` | `EMPLOYEE` | Yes | ⭐ Never assigned (1st in queue) |
| **Bengaluru** (`INR`) | Arjun Nair | `blr-v01` | `EMPLOYEE` | Yes | Assigned 5 days ago (2nd) |
| **Bengaluru** (`INR`) | Rahul Verma | `blr-v03` | `EMPLOYEE` | Yes | Assigned 1 day ago (3rd) |
| **Bengaluru** (`INR`) | Sneha Pillai | `blr-e01` | `EMPLOYEE` | No | Regular employee (can report empty) |
| **Bengaluru** (`INR`) | Kiran Reddy | `blr-e02` | `EMPLOYEE` | No | Regular employee (can report empty) |
| **Bengaluru** (`INR`) | Priya Sharma | `blr-hr-01` | `HR` | No | HR reviewer (approves/rejects/reimburses) |

---

## 4. Setup & Running Locally (Windows PowerShell)

### Backend Setup & Server
```powershell
cd backend

# Create virtual environment and install dependencies (if not already created)
python -m venv .venv
.\.venv\Scripts\pip.exe install -r requirements.txt

# Start the FastAPI server on http://localhost:8000
.\.venv\Scripts\uvicorn.exe main:app --reload --port 8000
```
- Interactive API docs (Swagger UI): http://localhost:8000/docs

### Run the End-to-End CLI Demo
```powershell
cd backend
.\.venv\Scripts\python.exe demo.py
```

### Run Automated Backend Tests
```powershell
cd backend
.\.venv\Scripts\pytest.exe -v
```

### Frontend Setup & Dev Server
Open a second PowerShell terminal:
```powershell
cd frontend

# Install dependencies (if not already installed)
npm install

# Start the Vite dev server on http://localhost:5173
npm run dev
```
*(Note: If Windows PowerShell script execution policy blocks `npm.ps1`, run `cmd /c "npm run dev"` or `cmd /c "npm run build"`.)*

---

## 5. Known Limitations (By Design for Take-Home Scope)

- **In-Memory Storage:** State is stored in Python dictionaries and resets when the backend server restarts.
- **Single-Process Lock:** `threading.RLock` protects concurrent requests within a single Python process. Multi-worker deployments in production would require PostgreSQL transactions, `SELECT ... FOR UPDATE` row locks, and partial unique indexes.
- **Authorized Estimate Ceiling:** Actual purchase amounts cannot exceed the `estimated_amount` reserved when accepting the request. Volunteers should estimate a comfortable upper bound.
- **Simulated Integrations & Auth:** Chat notifications (`MockChatService`), HR claim processing (`MockHRService`), and employee switching are simulated locally.
