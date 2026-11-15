/**
 * Typed API client for all backend calls.
 *
 * Why a central API module?
 * - All fetch calls go through one place, so changing the base URL or adding
 *   auth headers only requires editing one file.
 * - Each function has a typed return value, so TypeScript catches misuse at
 *   compile time rather than at runtime in the browser.
 *
 * Error handling:
 * - We throw an Error with the backend's detail message on non-2xx responses.
 * - Components catch this and display it to the user.
 */

import type {
  BudgetSnapshot,
  ChatNotification,
  CompletePurchaseResponse,
  Employee,
  MonthlyReport,
  OfficeDashboard,
  Office,
  ReimbursementClaim,
  RefillRequest,
} from './types';

const BASE = 'http://localhost:8000/api';

async function request<T>(
  path: string,
  options?: RequestInit,
): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });

  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try {
      const body = await resp.json();
      detail = body.detail || JSON.stringify(body);
    } catch {
      // ignore parse errors
    }
    throw new Error(detail);
  }

  return resp.json() as Promise<T>;
}

// ── Offices ───────────────────────────────────────────────────────────────────

export const getOffices = (): Promise<Office[]> =>
  request('/offices');

export const getOfficeDashboard = (officeId: string): Promise<OfficeDashboard> =>
  request(`/offices/${officeId}/dashboard`);

// ── Employees ─────────────────────────────────────────────────────────────────

export const getEmployees = (officeId: string): Promise<Employee[]> =>
  request(`/offices/${officeId}/employees`);

export const getAllEmployees = (): Promise<Employee[]> =>
  request('/employees');

// ── Refill requests ───────────────────────────────────────────────────────────

export const reportEmpty = (officeId: string, reportedBy: string): Promise<RefillRequest> =>
  request(`/offices/${officeId}/report-empty`, {
    method: 'POST',
    body: JSON.stringify({ reported_by: reportedBy }),
  });

export const getRequests = (officeId: string, activeOnly = false): Promise<RefillRequest[]> =>
  request(`/offices/${officeId}/requests?active_only=${activeOnly}`);

export const acceptRequest = (
  requestId: string,
  volunteerId: string,
  estimatedAmount: string,
): Promise<RefillRequest> =>
  request(`/requests/${requestId}/accept`, {
    method: 'POST',
    body: JSON.stringify({ volunteer_id: volunteerId, estimated_amount: estimatedAmount }),
  });

export const rejectRequest = (
  requestId: string,
  volunteerId: string,
): Promise<RefillRequest> =>
  request(`/requests/${requestId}/reject`, {
    method: 'POST',
    body: JSON.stringify({ volunteer_id: volunteerId }),
  });

export const completePurchase = (
  requestId: string,
  volunteerId: string,
  purchaseAmount: string,
  receiptReference: string,
): Promise<CompletePurchaseResponse> =>
  request(`/requests/${requestId}/complete`, {
    method: 'POST',
    body: JSON.stringify({
      volunteer_id: volunteerId,
      purchase_amount: purchaseAmount,
      receipt_reference: receiptReference,
    }),
  });

// ── Claims ────────────────────────────────────────────────────────────────────

export const getClaims = (officeId: string): Promise<ReimbursementClaim[]> =>
  request(`/offices/${officeId}/claims`);

export const getAllClaims = (): Promise<ReimbursementClaim[]> =>
  request('/claims');

export const approveClaim = (claimId: string, reviewerId: string): Promise<ReimbursementClaim> =>
  request(`/claims/${claimId}/approve`, {
    method: 'POST',
    body: JSON.stringify({ reviewer_id: reviewerId }),
  });

export const rejectClaim = (
  claimId: string,
  reviewerId: string,
  reason: string,
): Promise<ReimbursementClaim> =>
  request(`/claims/${claimId}/reject`, {
    method: 'POST',
    body: JSON.stringify({ reviewer_id: reviewerId, reason }),
  });

export const confirmReimbursed = (
  claimId: string,
  confirmerId: string,
): Promise<ReimbursementClaim> =>
  request(`/claims/${claimId}/reimburse`, {
    method: 'POST',
    body: JSON.stringify({ confirmer_id: confirmerId }),
  });

// ── Budget ────────────────────────────────────────────────────────────────────

export const getBudget = (officeId: string): Promise<BudgetSnapshot> =>
  request(`/offices/${officeId}/budget`);

// ── Reports ───────────────────────────────────────────────────────────────────

export const getMonthlyReport = (
  officeId: string,
  year: number,
  month: number,
): Promise<MonthlyReport> =>
  request(`/offices/${officeId}/report?year=${year}&month=${month}`);

// ── Notifications (demo) ──────────────────────────────────────────────────────

export const getNotifications = (): Promise<{ notifications: ChatNotification[] }> =>
  request('/notifications');
