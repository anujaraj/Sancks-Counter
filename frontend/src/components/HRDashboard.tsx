import { useState, useEffect, useCallback } from 'react';
import * as api from '../api';
import type { ReimbursementClaim, Employee, MonthlyReport } from '../types';

interface Props {
  currentEmployee: Employee | null;
  employees: Employee[];
}

function StatusBadge({ status }: { status: string }) {
  return <span className={`badge badge-${status.toLowerCase()}`}>{status}</span>;
}

function formatDateTime(dt: string | null): string {
  if (!dt) return '—';
  return new Date(dt).toLocaleString('en-GB', {
    day: '2-digit', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  });
}

export default function HRDashboard({ currentEmployee, employees }: Props) {
  const [claims, setClaims] = useState<ReimbursementClaim[]>([]);
  const [osloReport, setOsloReport] = useState<MonthlyReport | null>(null);
  const [blrReport, setBlrReport] = useState<MonthlyReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [msg, setMsg] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const [rejectReason, setRejectReason] = useState<Record<string, string>>({});
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [filterOffice, setFilterOffice] = useState<'all' | 'oslo' | 'bengaluru'>('all');

  const empName = (id: string | null) => {
    if (!id) return '—';
    const found = employees.find(e => e.id === id);
    return found ? `${found.name} (${id})` : id;
  };

  const showMsg = (type: 'success' | 'error', text: string) => {
    setMsg({ type, text });
    setTimeout(() => setMsg(null), 5000);
  };

  const refresh = useCallback(async () => {
    try {
      const now = new Date();
      const year = now.getUTCFullYear();
      const month = now.getUTCMonth() + 1;
      const [claimsData, osloRep, blrRep] = await Promise.all([
        api.getAllClaims(),
        api.getMonthlyReport('oslo', year, month),
        api.getMonthlyReport('bengaluru', year, month),
      ]);
      setClaims(claimsData);
      setOsloReport(osloRep);
      setBlrReport(blrRep);
    } catch (e: unknown) {
      showMsg('error', e instanceof Error ? e.message : 'Failed to load claims');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    setLoading(true);
    refresh();
    const interval = setInterval(refresh, 10000);
    return () => clearInterval(interval);
  }, [refresh]);

  const handleApprove = async (claimId: string) => {
    if (!currentEmployee) return showMsg('error', 'Please select an HR employee');
    setActionLoading(`approve-${claimId}`);
    try {
      await api.approveClaim(claimId, currentEmployee.id);
      showMsg('success', `Claim #${claimId} approved.`);
      await refresh();
    } catch (e: unknown) {
      showMsg('error', e instanceof Error ? e.message : 'Failed to approve claim');
    } finally {
      setActionLoading(null);
    }
  };

  const handleReject = async (claimId: string) => {
    if (!currentEmployee) return showMsg('error', 'Please select an HR employee');
    const reason = rejectReason[claimId];
    if (!reason?.trim()) return showMsg('error', 'Please enter a rejection reason');
    setActionLoading(`reject-${claimId}`);
    try {
      await api.rejectClaim(claimId, currentEmployee.id, reason.trim());
      showMsg('success', `Claim #${claimId} rejected.`);
      setRejectReason(prev => ({ ...prev, [claimId]: '' }));
      await refresh();
    } catch (e: unknown) {
      showMsg('error', e instanceof Error ? e.message : 'Failed to reject claim');
    } finally {
      setActionLoading(null);
    }
  };

  const handleReimburse = async (claimId: string) => {
    if (!currentEmployee) return showMsg('error', 'Please select an HR employee');
    setActionLoading(`reimburse-${claimId}`);
    try {
      await api.confirmReimbursed(claimId, currentEmployee.id);
      showMsg('success', `Claim #${claimId} marked as reimbursed.`);
      await refresh();
    } catch (e: unknown) {
      showMsg('error', e instanceof Error ? e.message : 'Failed to mark reimbursed');
    } finally {
      setActionLoading(null);
    }
  };

  const filteredClaims = filterOffice === 'all'
    ? claims
    : claims.filter(c => c.office_id === filterOffice);

  // Summary stats
  const pending = claims.filter(c => c.status === 'PENDING');
  const approved = claims.filter(c => c.status === 'APPROVED');
  const rejected = claims.filter(c => c.status === 'REJECTED');
  const reimbursed = claims.filter(c => c.status === 'REIMBURSED');

  if (loading) {
    return (
      <div className="loading-center">
        <div className="spinner" />
        <span>Loading HR claims...</span>
      </div>
    );
  }

  return (
    <div>
      {msg && (
        <div className={`message message-${msg.type}`} style={{ marginBottom: 20 }}>
          {msg.type === 'success' ? '✓' : '✗'} {msg.text}
        </div>
      )}

      <div className="demo-notice" style={{ marginBottom: 20 }}>
        🏢 HR View — All offices. This simulates the internal HR reimbursement portal.
        You are acting as: <strong>{currentEmployee?.name ?? '(no HR user selected)'}</strong>
      </div>

      {/* ── Summary Stats ── */}
      <div className="budget-stats" style={{ marginBottom: 24 }}>
        <div className="stat-box">
          <div className="stat-label">Pending Review</div>
          <div className="stat-value" style={{ color: 'var(--yellow)' }}>{pending.length}</div>
          <div className="stat-currency">claims</div>
        </div>
        <div className="stat-box">
          <div className="stat-label">Approved</div>
          <div className="stat-value" style={{ color: 'var(--green)' }}>{approved.length}</div>
          <div className="stat-currency">claims</div>
        </div>
        <div className="stat-box">
          <div className="stat-label">Rejected</div>
          <div className="stat-value" style={{ color: 'var(--red)' }}>{rejected.length}</div>
          <div className="stat-currency">claims</div>
        </div>
        <div className="stat-box">
          <div className="stat-label">Reimbursed</div>
          <div className="stat-value" style={{ color: 'var(--purple)' }}>{reimbursed.length}</div>
          <div className="stat-currency">claims</div>
        </div>
      </div>

      {/* ── Monthly Spending Summary (Strictly Separate Currencies) ── */}
      <div className="card" style={{ marginBottom: 24 }}>
        <div className="card-title"><span className="icon">📊</span> Monthly Office Spending Summary — Currencies Never Combined</div>
        <div className="grid-2">
          {osloReport && (
            <div className="stat-box">
              <div className="stat-label">🇳🇴 Oslo ({osloReport.currency}) — {osloReport.year}-{String(osloReport.month).padStart(2, '0')}</div>
              <div className="stat-value">{Number(osloReport.total_spent).toLocaleString()} {osloReport.currency}</div>
              <div className="stat-currency">
                Monthly Budget: {Number(osloReport.monthly_budget).toLocaleString()} {osloReport.currency} ·
                Available: {Number(osloReport.available_budget).toLocaleString()} {osloReport.currency} ·
                Reserved: {Number(osloReport.active_reservations).toLocaleString()} {osloReport.currency} ·
                Completed Refills: {osloReport.completed_refills}
              </div>
            </div>
          )}
          {blrReport && (
            <div className="stat-box">
              <div className="stat-label">🇮🇳 Bengaluru ({blrReport.currency}) — {blrReport.year}-{String(blrReport.month).padStart(2, '0')}</div>
              <div className="stat-value">{Number(blrReport.total_spent).toLocaleString()} {blrReport.currency}</div>
              <div className="stat-currency">
                Monthly Budget: {Number(blrReport.monthly_budget).toLocaleString()} {blrReport.currency} ·
                Available: {Number(blrReport.available_budget).toLocaleString()} {blrReport.currency} ·
                Reserved: {Number(blrReport.active_reservations).toLocaleString()} {blrReport.currency} ·
                Completed Refills: {blrReport.completed_refills}
              </div>
            </div>
          )}
        </div>
        <div className="demo-notice" style={{ marginTop: 12 }}>
          ⚠️ NOK and INR are tracked in separate office ledgers and never summed together.
        </div>
      </div>

      {/* ── Claims Table ── */}
      <div className="card">
        <div className="section-header">
          <div>
            <div className="card-title" style={{ marginBottom: 0 }}>
              <span className="icon">🧾</span> Reimbursement Claims
            </div>
          </div>
          <div className="office-tabs">
            {(['all', 'oslo', 'bengaluru'] as const).map(o => (
              <button
                key={o}
                className={`tab-btn ${filterOffice === o ? 'active' : ''}`}
                onClick={() => setFilterOffice(o)}
              >
                {o === 'all' ? 'All' : o === 'oslo' ? '🇳🇴 Oslo' : '🇮🇳 Bengaluru'}
              </button>
            ))}
          </div>
        </div>

        {filteredClaims.length === 0 ? (
          <div className="empty-state">
            <div className="empty-icon">🧾</div>
            No claims found
          </div>
        ) : (
          <div className="claims-table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Claim ID</th>
                  <th>Office</th>
                  <th>Volunteer</th>
                  <th>Amount</th>
                  <th>Receipt</th>
                  <th>HR Ref</th>
                  <th>Submitted</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {filteredClaims.map(claim => (
                  <tr key={claim.id}>
                    <td>
                      <span style={{ fontFamily: 'monospace', fontSize: 11, color: 'var(--text-secondary)' }}>
                        #{claim.id}
                      </span>
                    </td>
                    <td>
                      <span style={{ fontSize: 13 }}>
                        {claim.office_id === 'oslo' ? '🇳🇴 Oslo' : '🇮🇳 Bengaluru'}
                      </span>
                    </td>
                    <td style={{ fontSize: 12 }}>{empName(claim.employee_id)}</td>
                    <td className="amount-cell">
                      {Number(claim.amount).toLocaleString()} {claim.currency}
                    </td>
                    <td style={{ fontFamily: 'monospace', fontSize: 11 }}>{claim.receipt_reference}</td>
                    <td style={{ fontFamily: 'monospace', fontSize: 11, color: 'var(--accent)' }}>
                      {claim.external_hr_claim_id || '—'}
                    </td>
                    <td style={{ fontSize: 12 }}>{formatDateTime(claim.submitted_at)}</td>
                    <td>
                      <StatusBadge status={claim.status} />
                      {claim.rejection_reason && (
                        <div style={{ fontSize: 11, color: 'var(--red)', marginTop: 4 }}>
                          {claim.rejection_reason}
                        </div>
                      )}
                    </td>
                    <td>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 6, minWidth: 160 }}>
                        {claim.status === 'PENDING' && (
                          <>
                            <button
                              id={`btn-approve-${claim.id}`}
                              className="btn btn-success btn-sm"
                              onClick={() => handleApprove(claim.id)}
                              disabled={actionLoading === `approve-${claim.id}`}
                            >
                              {actionLoading === `approve-${claim.id}` ? <span className="spinner" style={{width:12,height:12}} /> : '✓'} Approve
                            </button>
                            <input
                              id={`input-reason-${claim.id}`}
                              className="input"
                              style={{ fontSize: 11, padding: '4px 8px' }}
                              placeholder="Rejection reason…"
                              value={rejectReason[claim.id] || ''}
                              onChange={e => setRejectReason(prev => ({ ...prev, [claim.id]: e.target.value }))}
                            />
                            <button
                              id={`btn-reject-${claim.id}`}
                              className="btn btn-danger btn-sm"
                              onClick={() => handleReject(claim.id)}
                              disabled={actionLoading === `reject-${claim.id}`}
                            >
                              {actionLoading === `reject-${claim.id}` ? <span className="spinner" style={{width:12,height:12}} /> : '✗'} Reject
                            </button>
                          </>
                        )}
                        {claim.status === 'APPROVED' && (
                          <button
                            id={`btn-reimburse-${claim.id}`}
                            className="btn btn-primary btn-sm"
                            onClick={() => handleReimburse(claim.id)}
                            disabled={actionLoading === `reimburse-${claim.id}`}
                          >
                            {actionLoading === `reimburse-${claim.id}` ? <span className="spinner" style={{width:12,height:12}} /> : '💸'} Confirm Reimbursed
                          </button>
                        )}
                        {(claim.status === 'REJECTED' || claim.status === 'REIMBURSED') && (
                          <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                            {claim.status === 'REIMBURSED'
                              ? `Paid ${formatDateTime(claim.reimbursed_at)}`
                              : `Reviewed ${formatDateTime(claim.reviewed_at)}`}
                          </span>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
