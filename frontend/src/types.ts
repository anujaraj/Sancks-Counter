// TypeScript interfaces that mirror the backend Pydantic schemas.
// Keeping these in sync with the backend is the main maintenance burden
// of a monorepo without code generation. In production you'd use
// openapi-typescript or similar to auto-generate these.

export interface Office {
  id: string;
  name: string;
  currency: 'NOK' | 'INR';
  monthly_budget: string;
}

export interface Employee {
  id: string;
  name: string;
  email: string;
  office_id: string;
  is_volunteer: boolean;
  role: 'EMPLOYEE' | 'HR' | 'ADMIN';
  last_assigned_at: string | null;
}

export interface Counter {
  id: string;
  office_id: string;
  status: 'FILLED' | 'EMPTY';
  last_updated_at: string;
}

export interface RefillRequest {
  id: string;
  counter_id: string;
  office_id: string;
  reported_by: string;
  assigned_to: string | null;
  status: 'OPEN' | 'ASSIGNED' | 'COMPLETED' | 'CANCELLED';
  estimated_amount: string | null;
  authorized_amount: string | null;
  purchase_amount: string | null;
  currency: string | null;
  created_at: string;
  assigned_at: string | null;
  completed_at: string | null;
  declined_by?: string[];
}

export interface ReimbursementClaim {
  id: string;
  refill_request_id: string;
  employee_id: string;
  office_id: string;
  amount: string;
  currency: string;
  receipt_reference: string;
  description: string;
  status: 'PENDING' | 'APPROVED' | 'REJECTED' | 'REIMBURSED';
  external_hr_claim_id: string | null;
  submitted_at: string;
  reviewed_at: string | null;
  reimbursed_at: string | null;
  rejection_reason: string | null;
}

export interface BudgetSnapshot {
  office_id: string;
  currency: string;
  monthly_budget: string;
  actual_spent: string;
  active_reservations: string;
  available: string;
}

export interface OfficeDashboard {
  office: Office;
  counter: Counter;
  active_requests: RefillRequest[];
  budget: BudgetSnapshot;
  volunteers: Employee[];
}

export interface CompletePurchaseResponse {
  request: RefillRequest;
  claim: ReimbursementClaim;
}

export interface MonthlyReport {
  office_id: string;
  office_name: string;
  year: number;
  month: number;
  currency: string;
  monthly_budget: string;
  total_spent: string;
  actual_spent_ytd: string;
  active_reservations: string;
  available_budget: string;
  completed_refills: number;
  claims_pending: number;
  claims_approved: number;
  claims_rejected: number;
  claims_reimbursed: number;
  refill_details: {
    request_id: string;
    volunteer: string | null;
    amount: string;
    completed_at: string | null;
  }[];
}

export interface ChatNotification {
  timestamp: string;
  type: 'VOLUNTEER_NEEDED' | 'ASSIGNMENT_CONFIRMED';
  office: string;
  request_id: string;
  recipients?: [string, string][];
  volunteer_id?: string;
  volunteer_name?: string;
  message: string;
}
