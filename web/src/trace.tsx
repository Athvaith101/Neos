type Filter = 'ALL' | SafetyStatus | 'ACTIONS';
const FILTERS: [Filter, string][] = [
  ['ALL', 'All steps'], ['VERIFIED', 'Verified'], ['INFEASIBLE', 'Infeasible'],
  ['SOLVER_FAILED', 'Solver failed'], ['ACTIONS', 'Only steps with an action'],
];

function TraceCard({ t }: { t: Trace }) {
  const actions = Object.values(t.actions);
  const load = Number.isFinite(t.result_tx_loading_pct) ? `${t.result_tx_loading_pct.toFixed(1)}% loading` : '—';
  return (
    <div className="panel trace-card">
      <div className="row1">
        <span className="step">step {t.step} · {fmtClock(t.hour)} · {t.controller}</span>
        <span className={`status-badge ${t.safety_status}`}>{t.safety_status}</span>
      </div>
      <div className="reason">{t.reason}</div>
      {actions.length > 0 && <div className="actions">{actions.join(' · ')}</div>}
      <div className="meta">
        <span>detected: {t.detected}</span>
        <span>result: {load}</span>
        <span>forecast P10–P90: {t.forecast_p10_kw.toFixed(0)} to {t.forecast_p90_kw.toFixed(0)} kW</span>
      </div>
    </div>
  );
}

function TracePage() {
  const scenarios = useScenarios();
  const [scenario, setScenario] = useState('normal');
  const [mode, setMode] = useState<Mode>('coordinated');
  const [filter, setFilter] = useState<Filter>('ALL');
  const { data, loading, error } = useApi<{ traces: Trace[] }>(`/api/decision_trace?scenario=${scenario}&mode=${mode}`);
  const all = data?.traces ?? [];
  const counts = useMemo(() => {
    const c: Record<SafetyStatus, number> = { VERIFIED: 0, INFEASIBLE: 0, SOLVER_FAILED: 0 };
    all.forEach(t => { c[t.safety_status]++; });
    return c;
  }, [all]);
  const shown = all.filter(t => filter === 'ALL' ? true : filter === 'ACTIONS' ? Object.keys(t.actions).length > 0 : t.safety_status === filter);
  return (
    <>
      <Controls scenarios={scenarios} scenario={scenario} mode={mode} onScenario={setScenario} onMode={setMode} />
      <div className="filters" role="group" aria-label="Filter steps">
        {FILTERS.map(([k, label]) => <button key={k} aria-pressed={filter === k} onClick={() => setFilter(k)}>{label}</button>)}
      </div>
      <Status loading={loading} error={error} />
      {!loading && !error && (<>
        <div className="summary-row">
          <span><b>{all.length}</b> steps total</span><span><b>{counts.VERIFIED}</b> verified</span>
          <span><b>{counts.INFEASIBLE}</b> infeasible</span><span><b>{counts.SOLVER_FAILED}</b> solver-failed</span>
          <span>showing <b>{shown.length}</b></span>
        </div>
        <div className="trace-list">
          {shown.length === 0 ? <p className="loading">No steps match this filter.</p> : shown.map(t => <TraceCard key={t.step} t={t} />)}
        </div>
      </>)}
    </>
  );
}
mount(<TracePage />);
