import { useState, useEffect, useCallback } from 'react';
import * as api from '../api';
import type {
  OfficeDashboard,
  Employee,
  RefillRequest,
  ReimbursementClaim,
  ChatNotification,
} from '../types';

interface Props {
  officeId: string;
  currentEmployee: Employee | null;
  employees: Employee[];
}

function StatusMessage({ msg }: { msg: { type: 'success' | 'error'; text: string } | null }) {
  if (!msg) return null;
  return (
    <div className={`message message-${msg.type}`}>
      {msg.type === 'success' ? '✓' : '✗'} {msg.text}
    </div>
  );
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

export default function EmployeeDashboard({ officeId, currentEmployee, employees }: Props) {
  const [dashboard, setDashboard] = useState<OfficeDashboard | null>(null);
  const [claims, setClaims] = useState<ReimbursementClaim[]>([]);
  const [notifications, setNotifications] = useState<ChatNotification[]>([]);
  const [loading, setLoading] = useState(true);
  const [msg, setMsg] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  // Per-request form state
  const [estimatedAmount, setEstimatedAmount] = useState('');
  const [purchaseAmount, setPurchaseAmount] = useState('');
  const [receiptRef, setReceiptRef] = useState('');
  const [actionLoading, setActionLoading] = useState<string | null>(null);

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
      const [dashData, claimsData, notifData] = await Promise.all([
        api.getOfficeDashboard(officeId),
        api.getClaims(officeId),
        api.getNotifications(),
      ]);
      setDashboard(dashData);
      setClaims(claimsData);
      setNotifications(
        (notifData.notifications || []).filter(
          n => n.office.toLowerCase() === dashData.office.name.toLowerCase()
        )
      );
    } catch (e: unknown) {
      showMsg('error', e instanceof Error ? e.message : 'Failed to load dashboard');
    } finally {
      setLoading(false);
    }
  }, [officeId]);

  useEffect(() => {
    setLoading(true);
    refresh();
    const interval = setInterval(refresh, 10000);
    return () => clearInterval(interval);
  }, [refresh]);

  const handleReportEmpty = async () => {
    if (!currentEmployee) return showMsg('error', 'Please select an employee first');
    setActionLoading('report');
    try {
      await api.reportEmpty(officeId, currentEmployee.id);
      showMsg('success', 'Counter reported as empty. Eligible volunteers have been notified via chat.');
      await refresh();
    } catch (e: unknown) {
      showMsg('error', e instanceof Error ? e.message : 'Failed to report empty');
    } finally {
      setActionLoading(null);
    }
  };

  const handleAccept = async (requestId: string) => {
    if (!currentEmployee) return showMsg('error', 'Please select a volunteer');
    if (!estimatedAmount || Number(estimatedAmount) <= 0) {
      return showMsg('error', 'Enter a valid positive estimated amount');
    }
    setActionLoading(`accept-${requestId}`);
    try {
      await api.acceptRequest(requestId, currentEmployee.id, estimatedAmount);
      showMsg('success', `Request accepted! Reserved ${estimatedAmount} ${dashboard?.budget.currency} from office budget.`);
      setEstimatedAmount('');
      await refresh();
    } catch (e: unknown) {
      showMsg('error', e instanceof Error ? e.message : 'Failed to accept');
    } finally {
      setActionLoading(null);
    }
  };

  const handleRejectRequest = async (requestId: string) => {
    if (!currentEmployee) return showMsg('error', 'Please select a volunteer');
    setActionLoading(`reject-req-${requestId}`);
    try {
      const updated = await api.rejectRequest(requestId, currentEmployee.id);
      showMsg(
        'success',
        `Request declined. Reassigned to next volunteer in fairness queue: ${empName(updated.assigned_to)}!`
      );
      await refresh();
    } catch (e: unknown) {
      showMsg('error', e instanceof Error ? e.message : 'Failed to reject request');
    } finally {
      setActionLoading(null);
    }
  };

  const handleComplete = async (requestId: string) => {
    if (!currentEmployee) return showMsg('error', 'Please select the assigned volunteer');
    if (!purchaseAmount || Number(purchaseAmount) <= 0) {
      return showMsg('error', 'Enter a valid positive purchase amount');
    }
    if (!receiptRef.trim()) {
      return showMsg('error', 'Enter a receipt reference');
    }
    setActionLoading(`complete-${requestId}`);
    try {
      const result = await api.completePurchase(requestId, currentEmployee.id, purchaseAmount, receiptRef.trim());
      showMsg(
        'success',
        `Counter marked FILLED! Recorded ${result.claim.amount} ${result.claim.currency}. HR Claim ${result.claim.external_hr_claim_id} is now PENDING.`
      );
      setPurchaseAmount('');
      setReceiptRef('');
      await refresh();
    } catch (e: unknown) {
      showMsg('error', e instanceof Error ? e.message : 'Failed to complete purchase');
    } finally {
      setActionLoading(null);
    }
  };

  if (loading) {
    return (
      <div className="loading-center">
        <div className="spinner" />
        <span>Loading dashboard...</span>
      </div>
    );
  }

  if (!dashboard) return null;

  const { office, counter, active_requests, budget, volunteers } = dashboard;
  const currency = office.currency;
  const isMyRequest = (r: RefillRequest) => r.assigned_to === currentEmployee?.id;
  const canAccept = currentEmployee?.is_volunteer && currentEmployee.office_id === officeId;

  return (
    <div className={`office-${officeId}`} style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      <StatusMessage msg={msg} />

      {/* ── Counter Status ── */}
      <div className="card">
        <div className="card-title"><span className="icon">🧃</span> Snack Counter Status — {office.name}</div>
        <div className="counter-display">
          <div className={`counter-badge ${counter.status.toLowerCase()}`}>
            <span className="counter-icon">
              {counter.status === 'FILLED' ? '🟢' : '🔴'}
            </span>
            {counter.status}
          </div>
          <div className="counter-info">
            <div className="counter-label">Physical Counter State (independent of HR claim approval)</div>
            <div className="counter-time">Last updated: {formatDateTime(counter.last_updated_at)}</div>
          </div>
          <button
            id={`btn-report-empty-${officeId}`}
            className="btn btn-danger"
            onClick={handleReportEmpty}
            disabled={actionLoading === 'report' || active_requests.length > 0}
          >
            {actionLoading === 'report' ? <span className="spinner" /> : '🚨'}
            {active_requests.length > 0 ? 'Request Already Active' : 'Report Empty Counter'}
          </button>
        </div>
        {active_requests.length > 0 && (
          <div className="demo-notice" style={{ marginTop: 12 }}>
            ℹ️ An active refill request (#{active_requests[0].id}) is in progress — duplicate reports are prevented.
          </div>
        )}
      </div>

      {/* ── Budget Summary ── */}
      <div className="card">
        <div className="card-title"><span className="icon">💰</span> Monthly Budget — {office.name} ({currency})</div>
        <div className="budget-stats">
          <div className="stat-box">
            <div className="stat-label">Monthly Budget</div>
            <div className="stat-value">{Number(budget.monthly_budget).toLocaleString()}</div>
            <div className="stat-currency">{currency}</div>
          </div>
          <div className="stat-box available">
            <div className="stat-label">Available</div>
            <div className="stat-value">{Number(budget.available).toLocaleString()}</div>
            <div className="stat-currency">{currency} (Budget − Spent − Reserved)</div>
          </div>
          <div className="stat-box reserved">
            <div className="stat-label">Active Reservations</div>
            <div className="stat-value">{Number(budget.active_reservations).toLocaleString()}</div>
            <div className="stat-currency">{currency} (In-flight estimates)</div>
          </div>
          <div className="stat-box spent">
            <div className="stat-label">Actual Spent</div>
            <div className="stat-value">{Number(budget.actual_spent).toLocaleString()}</div>
            <div className="stat-currency">{currency} (Completed refills)</div>
          </div>
        </div>
      </div>

      {/* ── Active Refill Requests ── */}
      <div className="card">
        <div className="card-title"><span className="icon">📋</span> Active Refill Requests</div>
        {active_requests.length === 0 ? (
          <div className="empty-state" style={{ padding: 24 }}>
            No active refill requests. Click <strong>Report Empty Counter</strong> above when snacks run out.
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            {active_requests.map(r => (
              <div key={r.id} className="request-card">
                <div className="request-header">
                  <div>
                    <div className="request-id">Request #{r.id}</div>
                    <div style={{ marginTop: 6 }}><StatusBadge status={r.status} /></div>
                  </div>
                  <div style={{ fontSize: 12, color: 'var(--text-secondary)', textAlign: 'right' }}>
                    Created {formatDateTime(r.created_at)}
                  </div>
                </div>

                <div className="request-meta">
                  <span>Reported by: <strong>{empName(r.reported_by)}</strong></span>
                  <span>Assigned volunteer: <strong>{r.assigned_to ? empName(r.assigned_to) : 'Unassigned'}</strong></span>
                  {r.estimated_amount && (
                    <span>Reserved estimate (ceiling): <strong>{r.estimated_amount} {currency}</strong></span>
                  )}
                  {r.assigned_at && <span>Accepted: <strong>{formatDateTime(r.assigned_at)}</strong></span>}
                </div>

                {/* Accept form — show for OPEN requests when user is a volunteer */}
                {r.status === 'OPEN' && canAccept && (
                  <div className="request-actions">
                    <div className="form-row">
                      <div className="form-group">
                        <label>Estimated Purchase Amount ({currency})</label>
                        <input
                          id={`input-estimate-${r.id}`}
                          className="input"
                          type="number"
                          min="1"
                          placeholder={`Typical: ${currency === 'NOK' ? '300–1200' : '500–3000'} ${currency}`}
                          value={estimatedAmount}
                          onChange={e => setEstimatedAmount(e.target.value)}
                        />
                      </div>
                      <button
                        id={`btn-accept-${r.id}`}
                        className="btn btn-primary"
                        onClick={() => handleAccept(r.id)}
                        disabled={actionLoading === `accept-${r.id}`}
                      >
                        {actionLoading === `accept-${r.id}` ? <span className="spinner" /> : '✋'}
                        Accept Request
                      </button>
                      <button
                        id={`btn-reject-req-${r.id}`}
                        className="btn btn-ghost"
                        onClick={() => handleRejectRequest(r.id)}
                        disabled={actionLoading === `reject-req-${r.id}`}
                      >
                        {actionLoading === `reject-req-${r.id}` ? <span className="spinner" /> : '⏭️'}
                        Reject (Pass to Next)
                      </button>
                    </div>
                  </div>
                )}

                {r.status === 'OPEN' && !canAccept && (
                  <div className="demo-notice">
                    ℹ️ Switch to a <strong>(volunteer)</strong> employee in the top bar (e.g. Jonas Dahl or Divya Menon) to accept this request.
                  </div>
                )}

                {/* Complete purchase form — show for ASSIGNED requests assigned to current user */}
                {r.status === 'ASSIGNED' && isMyRequest(r) && (
                  <div className="request-actions">
                    <div className="form-row">
                      <div className="form-group">
                        <label>Actual Purchase Amount ({currency}, max {r.authorized_amount})</label>
                        <input
                          id={`input-purchase-${r.id}`}
                          className="input"
                          type="number"
                          min="1"
                          max={r.authorized_amount || undefined}
                          placeholder={`<= ${r.authorized_amount} ${currency}`}
                          value={purchaseAmount}
                          onChange={e => setPurchaseAmount(e.target.value)}
                        />
                      </div>
                      <div className="form-group">
                        <label>Receipt Reference</label>
                        <input
                          id={`input-receipt-${r.id}`}
                          className="input"
                          type="text"
                          placeholder="e.g. RECEIPT-2026-001"
                          value={receiptRef}
                          onChange={e => setReceiptRef(e.target.value)}
                        />
                      </div>
                      <button
                        id={`btn-complete-${r.id}`}
                        className="btn btn-success"
                        onClick={() => handleComplete(r.id)}
                        disabled={actionLoading === `complete-${r.id}`}
                      >
                        {actionLoading === `complete-${r.id}` ? <span className="spinner" /> : '✅'}
                        Complete Purchase
                      </button>
                      <button
                        id={`btn-reject-assigned-${r.id}`}
                        className="btn btn-ghost"
                        onClick={() => handleRejectRequest(r.id)}
                        disabled={actionLoading === `reject-req-${r.id}`}
                      >
                        {actionLoading === `reject-req-${r.id}` ? <span className="spinner" /> : '⏭️'}
                        Reject (Pass to Next)
                      </button>
                    </div>
                  </div>
                )}

                {r.status === 'ASSIGNED' && !isMyRequest(r) && (
                  <div className="demo-notice">
                    ℹ️ Assigned to <strong>{empName(r.assigned_to)}</strong> — select that volunteer in the top bar to record the purchase.
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ── Office Reimbursement Claims (Separate from Physical Counter Status) ── */}
      <div className="card">
        <div className="card-title"><span className="icon">🧾</span> Reimbursement Claims — {office.name}</div>
        {claims.length === 0 ? (
          <div className="empty-state" style={{ padding: 20 }}>
            No reimbursement claims submitted for {office.name} yet.
          </div>
        ) : (
          <div className="claims-table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Claim ID</th>
                  <th>Volunteer</th>
                  <th>Amount</th>
                  <th>Receipt Ref</th>
                  <th>External HR ID</th>
                  <th>Submitted</th>
                  <th>Claim Status</th>
                </tr>
              </thead>
              <tbody>
                {claims.map(c => (
                  <tr key={c.id}>
                    <td style={{ fontFamily: 'monospace', fontSize: 12 }}>#{c.id}</td>
                    <td>{empName(c.employee_id)}</td>
                    <td className="amount-cell">{Number(c.amount).toLocaleString()} {c.currency}</td>
                    <td style={{ fontFamily: 'monospace', fontSize: 12 }}>{c.receipt_reference}</td>
                    <td style={{ fontFamily: 'monospace', fontSize: 12, color: 'var(--accent)' }}>
                      {c.external_hr_claim_id || '—'}
                    </td>
                    <td style={{ fontSize: 12 }}>{formatDateTime(c.submitted_at)}</td>
                    <td>
                      <StatusBadge status={c.status} />
                      {c.rejection_reason && (
                        <div style={{ fontSize: 11, color: 'var(--red)', marginTop: 4 }}>
                          Reason: {c.rejection_reason}
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="demo-notice" style={{ marginTop: 12 }}>
          ℹ️ Physical counter status becomes <strong>FILLED</strong> immediately on purchase, even while the HR reimbursement claim is <strong>PENDING</strong>. Switch to the <strong>🏢 HR</strong> view in the header to approve, reject, or mark claims reimbursed.
        </div>
      </div>

      <div className="grid-2">
        {/* ── Volunteers ── */}
        <div className="card">
          <div className="card-title"><span className="icon">🙋</span> Volunteers — Fairness Rotation</div>
          {volunteers.length === 0 ? (
            <div className="empty-state">
              <div className="empty-icon">👥</div>
              No volunteers registered for this office
            </div>
          ) : (
            <div className="volunteer-list">
              {[...volunteers]
                .sort((a, b) => {
                  if (!a.last_assigned_at && !b.last_assigned_at) return 0;
                  if (!a.last_assigned_at) return -1;
                  if (!b.last_assigned_at) return 1;
                  return new Date(a.last_assigned_at).getTime() - new Date(b.last_assigned_at).getTime();
                })
                .map((v, i) => (
                  <div key={v.id} className="volunteer-item">
                    <div>
                      <div className="volunteer-name">
                        {i === 0 ? '⭐ ' : ''}{v.name}
                        {currentEmployee?.id === v.id && ' (you)'}
                      </div>
                      <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>{v.email}</div>
                    </div>
                    <div className="volunteer-fairness">
                      {v.last_assigned_at
                        ? `Last assigned: ${formatDateTime(v.last_assigned_at)}`
                        : '✨ Never assigned (preferred)'}
                    </div>
                  </div>
                ))}
            </div>
          )}
          <div className="demo-notice" style={{ marginTop: 12 }}>
            ⭐ = Next in fairness rotation (never assigned first, then least recently assigned)
          </div>
        </div>

        {/* ── Mock Chat Notifications ── */}
        <div className="card">
          <div className="card-title"><span className="icon">💬</span> Simulated Internal Chat Notifications</div>
          {notifications.length === 0 ? (
            <div className="empty-state" style={{ padding: 24 }}>
              No chat notifications sent for {office.name} yet. Report an empty counter to trigger volunteer alerts.
            </div>
          ) : (
            <div className="volunteer-list">
              {[...notifications].reverse().map((n, idx) => (
                <div key={idx} className="volunteer-item" style={{ flexDirection: 'column', alignItems: 'flex-start', gap: 6 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', width: '100%', fontSize: 11, color: 'var(--text-secondary)' }}>
                    <strong>{n.type}</strong>
                    <span>{formatDateTime(n.timestamp)}</span>
                  </div>
                  <div style={{ fontSize: 13 }}>{n.message}</div>
                  {n.recipients && n.recipients.length > 0 && (
                    <div style={{ fontSize: 11, color: 'var(--accent)' }}>
                      Notified in fairness order: {n.recipients.map(r => r[1]).join(' → ')}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
