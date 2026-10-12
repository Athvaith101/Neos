function Timeline({ envs, idx, onPick }: { envs: Envelope[]; idx: number; onPick: (i: number) => void }) {
  const w = 900, h = 230, ml = 44, mr = 12, mt = 14, mb = 28;
  const pw = w - ml - mr, ph = h - mt - mb;
  const vals = envs.map(e => e.safe_flexibility_kw);
  const hi = Math.max(10, Math.ceil(Math.max(...vals) / 20) * 20);
  const x = (i: number) => ml + (pw * i) / Math.max(1, vals.length - 1);
  const y = (v: number) => mt + ph * (1 - v / hi);
  const line = vals.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join('');
  const area = `${line}L${x(vals.length - 1).toFixed(1)},${mt + ph}L${ml},${mt + ph}Z`;
  const n = vals.length;
  const tickIdx = [0, 0.25, 0.5, 0.75, 1].map(f => Math.round(f * (n - 1)));
  const pick = (ev: React.MouseEvent<SVGSVGElement>) => {
    const r = ev.currentTarget.getBoundingClientRect();
    const px = ((ev.clientX - r.left) / r.width) * w;
    onPick(Math.max(0, Math.min(vals.length - 1, Math.round(((px - ml) / pw) * (vals.length - 1)))));
  };
  return (
    <div className="panel panel-pad" style={{ marginBottom: 24 }}>
      <h3 className="sec">Safe flexibility across the day</h3>
      <svg viewBox={`0 0 ${w} ${h}`} style={{ width: '100%', height: 230, cursor: 'crosshair' }} onClick={pick}
           role="img" aria-label="Safe flexibility in kW across the day">
        <defs><linearGradient id="fa" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#39C26D" stopOpacity=".28" /><stop offset="1" stopColor="#39C26D" stopOpacity="0" />
        </linearGradient></defs>
        {[0, .5, 1].map(f => <line key={f} x1={ml} x2={w - mr} y1={mt + ph * f} y2={mt + ph * f} stroke="#DCEAE1" />)}
        <text x={ml - 8} y={mt + 4} fontSize="10" fill="#80988A" textAnchor="end">{hi}</text>
        <text x={ml - 8} y={mt + ph + 4} fontSize="10" fill="#80988A" textAnchor="end">0</text>
        {tickIdx.map(i => <text key={i} x={x(i)} y={h - 8} fontSize="10" fill="#80988A" textAnchor="middle">{fmtClock(envs[i].hour % 24)}</text>)}
        <path d={area} fill="url(#fa)" />
        <path d={line} fill="none" stroke="#1B7A4B" strokeWidth="2.2" />
        <line x1={x(idx)} x2={x(idx)} y1={mt} y2={mt + ph} stroke="#0F4A2C" strokeDasharray="3 3" />
        <circle cx={x(idx)} cy={y(vals[idx])} r="5" fill="#fff" stroke="#1B7A4B" strokeWidth="2.4" />
      </svg>
      <p className="hint">kW of safely mobilisable capability, one point per control step. Click the chart or drag the slider.</p>
    </div>
  );
}

function Bars({ e }: { e: Envelope }) {
  const total = e.ev_flexibility_kw + e.battery_flexibility_kw + e.load_flexibility_kw;
  const rows: [string, number, string][] = [
    ['EV charging', e.ev_flexibility_kw, '#2E8B57'], ['Battery', e.battery_flexibility_kw, '#1B7A4B'],
    ['Appliances', e.load_flexibility_kw, '#8CCB9E'],
  ];
  return (
    <div className="bars">
      {rows.map(([lab, v, c]) => (
        <div className="bar-row" key={lab}>
          <div className="lab">{lab}</div>
          <div className="bar-track"><div className="bar-fill" style={{ width: `${total > 0 ? Math.min(100, (100 * v) / total) : 0}%`, background: c }} /></div>
          <div className="val">{v.toFixed(1)} kW</div>
        </div>
      ))}
    </div>
  );
}

function Cell({ label, value, headline, color }: { label: string; value: string; headline?: boolean; color?: string }) {
  return <div className={'now-cell' + (headline ? ' headline' : '')}><span>{label}</span><b style={color ? { color } : undefined}>{value}</b></div>;
}

function FlexPage() {
  const scenarios = useScenarios();
  const [scenario, setScenario] = useState('normal');
  const [mode, setMode] = useState<Mode>('coordinated');
  const { data, loading, error } = useApi<{ envelopes: Envelope[] }>(`/api/flexibility?scenario=${scenario}&mode=${mode}`);
  const envs = data?.envelopes ?? [];
  const [idx, setIdx] = useState(0);
  useEffect(() => { setIdx(Math.floor(envs.length / 2)); }, [envs.length, scenario, mode]);
  const e = envs[Math.min(idx, envs.length - 1)];
  const conf = e ? (e.confidence_pct > 60 ? '#1B7A4B' : e.confidence_pct > 30 ? '#B8781E' : '#AD3B2C') : undefined;
  return (
    <>
      <Controls scenarios={scenarios} scenario={scenario} mode={mode} onScenario={setScenario} onMode={setMode} />
      <Status loading={loading} error={error} />
      {e && !loading && (<>
        <Timeline envs={envs} idx={idx} onPick={setIdx} />
        <div className="scrub">
          <label htmlFor="scrub">Time of day</label>
          <input id="scrub" type="range" min={0} max={envs.length - 1} value={idx} onChange={ev => setIdx(+ev.target.value)} />
          <b>{fmtClock(e.hour)}</b>
        </div>
        <div className="now-grid">
          <Cell headline label="Safe flexibility, this step" value={`${e.safe_flexibility_kw.toFixed(1)} kW`} />
          <Cell label="Transformer loading" value={`${e.tx_loading_pct.toFixed(1)}%`} />
          <Cell label="Transformer headroom" value={`${e.tx_headroom_kw.toFixed(0)} kW`} />
          <Cell label="Confidence (heuristic)" value={`${e.confidence_pct.toFixed(0)}%`} color={conf} />
        </div>
        <h3 className="sec">Where the flexibility would come from</h3>
        <Bars e={e} />
        <div className="panel panel-pad">
          <table className="data"><tbody>
            <tr><th>Forecast uncertainty</th><td>±{e.forecast_uncertainty_kw.toFixed(1)} kW</td></tr>
            <tr><th>Protected reserve floor</th><td>{e.protected_reserve_floor_pct.toFixed(0)}% of battery capacity</td></tr>
            <tr><th>Batteries available</th><td>{e.batteries_available} (avg SOC {e.avg_battery_soc_pct.toFixed(0)}%)</td></tr>
            <tr><th>Valid for</th><td>{e.duration_note}</td></tr>
            <tr><th>Confidence basis</th><td>{e.confidence_note}</td></tr>
            <tr><th>Status</th><td><span className="tag">{e.status}</span> {e.note}</td></tr>
          </tbody></table>
        </div>
      </>)}
    </>
  );
}
mount(<FlexPage />);
