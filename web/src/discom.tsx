interface PhaseResp { tx_loading: number[]; phases: { A: number[]; B: number[]; C: number[] }; imbalance: number[];
                      peak_phase_loading: number; hours_phase_over_100: number; imbalance_p95: number; note: string }
interface DiscomResp {
  request: { start_step: number };
  decision: { action: 'ACCEPT' | 'MODIFY' | 'DEFER' | 'REJECT'; requested_kw: number; accepted_kw: number; start_step: number;
              n_steps: number; probed_capability_kw: number; reason: string };
  capability_by_shift_kw: (number | null)[]; margin_used: number; disclaimer: string; reliability: { promises_kept: number; n_requests: number; mean_ratio: number; min_ratio: number; firm_kw: number | null; note: string } | null; window: [number, number] | null;
  baseline: { peak_kw: number; ev_unserved_kwh: number; ev_satisfaction: number; steps_verified_pct: number };
  actual: { peak_kw: number; ev_unserved_kwh: number; ev_satisfaction: number; steps_verified_pct: number; peak_phase_loading: number };
  delivered: { mean_kw: number; min_kw: number; delivery_ratio: number; shortfall_kwh: number; energy_shifted_kwh: number;
               steps_meeting_promise: number; n_steps: number } | null;
  rebound?: { post_window_peak_change_kw: number; overall_peak_change_kw: number };
  net_baseline_kw?: number[]; net_actual_kw?: number[]; note?: string;
}

const phaseTone = (p: number) => (p > 100 ? 'over' : p >= 85 ? 'stressed' : 'normal');
const toneColor: Record<string, string> = { normal: '#1B7A4B', stressed: '#B8781E', over: '#AD3B2C' };

function PhasePanel() {
  const scen = 'normal';
  const [mode, setMode] = useState<Mode>('uncoordinated');
  const { data, loading, error } = useApi<PhaseResp>(`/api/phase?scenario=${scen}&mode=${mode}`);
  const [idx, setIdx] = useState(36);
  const d = data;
  return (
    <div className="panel panel-pad" style={{ marginBottom: 20 }}>
      <h3 className="sec">Transformer, phase by phase</h3>
      <div className="controls" style={{ margin: '0 0 14px' }}>
        <label htmlFor="pm">View</label>
        <select id="pm" value={mode} onChange={e => setMode(e.target.value as Mode)}>
          <option value="uncoordinated">Without coordination</option>
          <option value="coordinated">With NEOS</option>
        </select>
      </div>
      <Status loading={loading} error={error} />
      {d && !loading && (<>
        <div className="scrub"><label htmlFor="ps">Time</label>
          <input id="ps" type="range" min={0} max={95} value={idx} onChange={e => setIdx(+e.target.value)} /><b>{clockOf(idx)}</b></div>
        <div className="tiles">
          <Tile label="Transformer (total)" value={`${d.tx_loading[idx].toFixed(0)}%`} />
          <Tile label="Worst single phase" value={`${Math.max(d.phases.A[idx], d.phases.B[idx], d.phases.C[idx]).toFixed(0)}%`}
                tone={Math.max(d.phases.A[idx], d.phases.B[idx], d.phases.C[idx]) > 100 ? 'bad' : undefined} sub="of that phase's rating" />
          <Tile label="Peak phase, whole day" value={`${d.peak_phase_loading.toFixed(0)}%`} sub={`${d.hours_phase_over_100.toFixed(1)} h above 100%`} />
          <Tile label="Imbalance (p95)" value={d.imbalance_p95.toFixed(2)} sub="(max−min)/mean kVA" />
        </div>
        {(['A', 'B', 'C'] as const).map(ph => {
          const v = d.phases[ph][idx]; const t = phaseTone(v);
          return (<div className="phase-row" key={ph}><span className="lab">Phase {ph}</span>
            <div className="bar-track" style={{ flex: 1 }}><div className="bar-fill" style={{ width: `${Math.min(100, v)}%`, background: toneColor[t] }} /></div>
            <span className="val" style={{ width: 54, textAlign: 'right', fontFamily: 'var(--font-mono)', fontSize: 12.5 }}>{v.toFixed(0)}%</span>
            <span className="st" style={{ color: toneColor[t] }}>{t}</span></div>);
        })}
        <LineChart label="Loading by phase across the day" unit="%" yMin={0} yMax={Math.max(120, Math.ceil(d.peak_phase_loading / 10) * 10)}
          marks={[{ at: idx, label: clockOf(idx) }]}
          lines={[{ name: 'Phase A', color: '#1B7A4B', values: d.phases.A }, { name: 'Phase B', color: '#2E7D9E', values: d.phases.B },
                  { name: 'Phase C', color: '#B8781E', values: d.phases.C }, { name: 'Transformer total', color: '#0E2A1D', values: d.tx_loading, dash: '4 3' }]} />
        <p className="hint">{d.note} NEOS lowers load on all phases; it does not rebalance phases (no household phase switching). Aggregate meters cannot say which appliance caused a phase's load.</p>
      </>)}
    </div>
  );
}

function RequestPanel() {
  const [f, setF] = useState({ start_step: 44, n_steps: 6, kw: 30, notice_steps: 2 });
  const [url, setUrl] = useState<string | null>(null);
  const { data, loading, error } = useApi<DiscomResp>(url);
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: +e.target.value });
  const send = () => setUrl(`/api/discom?scenario=normal&start_step=${f.start_step}&n_steps=${f.n_steps}&kw=${f.kw}&notice_steps=${f.notice_steps}&_=${Date.now()}`);
  const d = data;
  return (
    <div className="panel panel-pad">
      <h3 className="sec">Send the neighbourhood a flexibility request</h3>
      <p className="hint" style={{ marginTop: -6, marginBottom: 14 }}>The utility asks for a reduction in net import. NEOS asks its own optimiser what it can sustain, answers, and the response is then measured against an identical run without the request.</p>
      <div className="form">
        <div><label htmlFor="f1">Start (step; 0 = 12:00, 44 = 23:00)</label><input id="f1" type="number" min={0} max={95} value={f.start_step} onChange={set('start_step')} /></div>
        <div><label htmlFor="f2">Duration (15-min steps)</label><input id="f2" type="number" min={1} max={24} value={f.n_steps} onChange={set('n_steps')} /></div>
        <div><label htmlFor="f3">Requested reduction (kW)</label><input id="f3" type="number" min={1} max={2000} value={f.kw} onChange={set('kw')} /></div>
        <div><label htmlFor="f4">Notice (15-min steps)</label><input id="f4" type="number" min={1} max={16} value={f.notice_steps} onChange={set('notice_steps')} /></div>
      </div>
      <button className="btn" onClick={send} disabled={loading}>{loading ? 'Simulating…' : 'Send request'}</button>
      <span className="hint" style={{ marginLeft: 12 }}>Two full simulated days plus optimiser probes; allow up to a minute on OpenDSS.</span>
      {error && <p className="loading" role="alert">Could not load: {error}</p>}
      {d && !loading && (<div style={{ marginTop: 22 }}>
        <div className={`decision ${d.decision.action}`}>
          <h3>{d.decision.action}{d.decision.action !== 'REJECT' && ` · ${d.decision.accepted_kw.toFixed(0)} kW from ${clockOf(d.decision.start_step)}`}</h3>
          <p>{d.decision.reason}</p>
        </div>
        {d.reliability && d.decision.action !== 'REJECT' && (
          <p className="hint" role="note" style={{ borderLeft: '3px solid var(--amber)', paddingLeft: 12 }}>
            <b>Treat the promise as an estimate.</b> On held-out neighbourhoods only {d.reliability.promises_kept} of {d.reliability.n_requests} promises were fully kept;
            delivery averaged {(100 * d.reliability.mean_ratio).toFixed(0)}% and fell to {(100 * d.reliability.min_ratio).toFixed(0)}% of the promise.
            Plan on about {d.reliability.firm_kw === null ? '—' : d.reliability.firm_kw.toFixed(0)} kW, and even that is the worst of only {d.reliability.n_requests} samples, not a bound.</p>)}
        <div className="tiles">
          <Tile label="Requested" value={`${d.decision.requested_kw.toFixed(0)} kW`} />
          <Tile label="Promised" value={`${d.decision.accepted_kw.toFixed(0)} kW`} sub={`optimiser probe ${d.decision.probed_capability_kw.toFixed(0)} kW × margin ${d.margin_used.toFixed(2)}`} />
          <Tile label="Measured delivery" value={d.delivered ? `${d.delivered.mean_kw.toFixed(0)} kW` : '—'}
                tone={d.delivered ? (d.delivered.delivery_ratio >= 0.9 ? 'good' : 'warn') : undefined}
                sub={d.delivered ? `${(100 * d.delivered.delivery_ratio).toFixed(0)}% of promise` : 'nothing dispatched'} />
          <Tile label="Rebound after window" value={d.rebound ? `${d.rebound.post_window_peak_change_kw >= 0 ? '+' : ''}${d.rebound.post_window_peak_change_kw.toFixed(0)} kW` : '—'} sub="change in the next 2 h peak" />
        </div>
        {d.net_baseline_kw && d.net_actual_kw && d.window && (
          <LineChart label="Net import with and without the request" unit=" kW" bands={[{ from: d.window[0], to: d.window[1], label: 'request window' }]}
            lines={[{ name: 'Without request', color: '#80988A', values: d.net_baseline_kw, dash: '4 3' }, { name: 'With request', color: '#1B7A4B', values: d.net_actual_kw }]} />)}
        <table className="data" style={{ marginTop: 14 }}><tbody>
          <tr><th>Probed sustained capability, by start time</th><td>{d.capability_by_shift_kw.map((c, i) => c === null ? null : `${clockOf(d.request.start_step + i)}: ${c.toFixed(0)} kW`).filter(Boolean).join(' · ')}</td></tr>
          <tr><th>EV owners affected</th><td>{d.actual.ev_unserved_kwh.toFixed(2)} kWh unserved (baseline {d.baseline.ev_unserved_kwh.toFixed(2)} kWh)</td></tr>
          <tr><th>Physics check</th><td>{d.actual.steps_verified_pct.toFixed(1)}% of steps AC-verified · peak phase {d.actual.peak_phase_loading.toFixed(0)}%</td></tr>
        </tbody></table>
        <p className="disclaimer">{d.disclaimer}</p>
      </div>)}
    </div>
  );
}

function DiscomPage() {
  return (<>
    <p className="scope">Simulation only. Nothing here commands equipment. NEOS never promises flexibility it cannot show: across held-out neighbourhoods, delivery against promise was uneven (see Evidence), which is why this interface reports <b>measured</b> delivery, not a guarantee.</p>
    <PhasePanel />
    <RequestPanel />
  </>);
}
mount(<DiscomPage />);
