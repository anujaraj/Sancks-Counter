import { useState, useEffect } from 'react';
import './index.css';
import EmployeeDashboard from './components/EmployeeDashboard';
import HRDashboard from './components/HRDashboard';
import * as api from './api';
import type { Employee, Office } from './types';

type ViewMode = 'employee' | 'hr';

export default function App() {
  const [offices, setOffices] = useState<Office[]>([]);
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [selectedOffice, setSelectedOffice] = useState<string>('oslo');
  const [viewMode, setViewMode] = useState<ViewMode>('employee');
  const [currentEmployeeId, setCurrentEmployeeId] = useState<string>('oslo-e01');
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.getOffices(), api.getAllEmployees()])
      .then(([offices, emps]) => {
        setOffices(offices);
        setEmployees(emps);
      })
      .catch(e => setLoadError(e instanceof Error ? e.message : 'Could not connect to backend'));
  }, []);

  // When switching to HR view, auto-select an HR employee
  const handleViewModeChange = (mode: ViewMode) => {
    setViewMode(mode);
    if (mode === 'hr') {
      const hrEmp = employees.find(e => e.role === 'HR');
      if (hrEmp) setCurrentEmployeeId(hrEmp.id);
    } else {
      // Switch back to an employee in the selected office
      const emp = employees.find(e => e.office_id === selectedOffice && e.role === 'EMPLOYEE');
      if (emp) setCurrentEmployeeId(emp.id);
    }
  };

  const handleOfficeChange = (officeId: string) => {
    setSelectedOffice(officeId);
    // Switch current employee to one from that office
    const emp = employees.find(e => e.office_id === officeId && e.role === 'EMPLOYEE');
    if (emp) setCurrentEmployeeId(emp.id);
  };

  const currentEmployee = employees.find(e => e.id === currentEmployeeId) ?? null;

  // Filter employees for the selector based on view mode
  const selectableEmployees = viewMode === 'hr'
    ? employees.filter(e => e.role === 'HR' || e.role === 'ADMIN')
    : employees.filter(e => e.office_id === selectedOffice);

  if (loadError) {
    return (
      <div style={{
        display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
        minHeight: '100vh', gap: 16, padding: 32, textAlign: 'center',
      }}>
        <div style={{ fontSize: 48 }}>⚠️</div>
        <h2 style={{ color: 'var(--red)', fontSize: 20 }}>Cannot connect to backend</h2>
        <p style={{ color: 'var(--text-secondary)', maxWidth: 400 }}>
          {loadError}
        </p>
        <div className="demo-notice">
          Make sure the backend is running:<br />
          <code style={{ fontFamily: 'monospace', marginTop: 6, display: 'block' }}>
            cd backend && .venv\Scripts\uvicorn main:app --reload
          </code>
        </div>
      </div>
    );
  }

  return (
    <div className="app">
      {/* ── Header ── */}
      <header className="app-header">
        <div className="header-inner">
          <div className="app-logo">
            <span className="logo-icon">🍿</span>
            <span>Snack Counter Automation</span>
          </div>

          <div className="header-controls">
            {/* Office selector — hidden in HR view since HR sees all offices */}
            {viewMode === 'employee' && (
              <div className="control-group">
                <span className="control-label">Office</span>
                <div className="office-tabs">
                  {offices.map(o => (
                    <button
                      key={o.id}
                      id={`tab-office-${o.id}`}
                      className={`tab-btn ${selectedOffice === o.id ? `active${o.id === 'bengaluru' ? ' blr' : ''}` : ''}`}
                      onClick={() => handleOfficeChange(o.id)}
                    >
                      {o.id === 'oslo' ? '🇳🇴' : '🇮🇳'} {o.name}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* View mode selector */}
            <div className="control-group">
              <span className="control-label">View</span>
              <div className="view-tabs">
                <button
                  id="tab-view-employee"
                  className={`tab-btn ${viewMode === 'employee' ? 'active' : ''}`}
                  onClick={() => handleViewModeChange('employee')}
                >
                  👤 Employee
                </button>
                <button
                  id="tab-view-hr"
                  className={`tab-btn ${viewMode === 'hr' ? 'active' : ''}`}
                  onClick={() => handleViewModeChange('hr')}
                >
                  🏢 HR
                </button>
              </div>
            </div>

            {/* Person selector */}
            <div className="control-group">
              <span className="control-label">
                {viewMode === 'hr' ? 'HR User' : 'You are'}
              </span>
              <select
                id="select-employee"
                className="emp-select"
                value={currentEmployeeId}
                onChange={e => setCurrentEmployeeId(e.target.value)}
              >
                {selectableEmployees.map(e => (
                  <option key={e.id} value={e.id}>
                    {e.name} {e.is_volunteer ? '(volunteer)' : ''}{e.role === 'HR' ? '(HR)' : ''}
                  </option>
                ))}
              </select>
            </div>
          </div>
        </div>
      </header>

      {/* ── Main ── */}
      <main className="main-content">
        {/* Demo notice */}
        <div className="demo-notice">
          🎭 <strong>Demo Mode</strong> — Employee login and HR integration are simulated.
          No real authentication or external services are used.
          {currentEmployee && (
            <span style={{ marginLeft: 8 }}>
              Acting as: <strong>{currentEmployee.name}</strong>
              {' '}({currentEmployee.role}{currentEmployee.is_volunteer ? ', Volunteer' : ''})
              {' '}— {offices.find(o => o.id === currentEmployee.office_id)?.name}
            </span>
          )}
        </div>

        {viewMode === 'employee' ? (
          <EmployeeDashboard
            officeId={selectedOffice}
            currentEmployee={currentEmployee}
            employees={employees}
          />
        ) : (
          <HRDashboard
            currentEmployee={currentEmployee}
            employees={employees}
          />
        )}
      </main>
    </div>
  );
}
