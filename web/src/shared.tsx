// Shared types, hooks and components for the React + TypeScript pages.
// Concatenated in front of each page at build time (see build_tsx.js).
const { useState, useEffect, useMemo, useCallback } = React;

type Scenario = { key: string; title: string };
type Mode = 'coordinated' | 'uncoordinated' | 'no_control';

interface Envelope {
  step: number; hour: number; status: 'ESTIMATED'; note: string;
  safe_flexibility_kw: number; ev_flexibility_kw: number; battery_flexibility_kw: number;
  load_flexibility_kw: number; tx_loading_pct: number; tx_headroom_kw: number;
  confidence_pct: number; confidence_note: string; forecast_uncertainty_kw: number;
  protected_reserve_floor_pct: number; batteries_available: number;
  avg_battery_soc_pct: number; duration_note: string;
}

type SafetyStatus = 'VERIFIED' | 'INFEASIBLE' | 'SOLVER_FAILED';
interface Trace {
  step: number; hour: number; controller: string; safety_status: SafetyStatus;
  reason: string; detected: string; actions: Record<string, string>;
  result_tx_loading_pct: number; forecast_p10_kw: number; forecast_p90_kw: number;
}

const fmtClock = (h: number): string =>
  `${String(Math.floor(h)).padStart(2, '0')}:${String(Math.round((h % 1) * 60)).padStart(2, '0')}`;

function useScenarios(): Scenario[] {
  const [list, setList] = useState<Scenario[]>([]);
  useEffect(() => { fetch('/api/scenarios').then(r => r.json()).then(setList).catch(() => setList([])); }, []);
  return list;
}

// Fetch with loading / error state; refetches when `url` changes.
function useApi<T>(url: string | null): { data: T | null; loading: boolean; error: string | null } {
  const [state, set] = useState<{ data: T | null; loading: boolean; error: string | null }>(
    { data: null, loading: !!url, error: null });
  useEffect(() => {
    if (!url) return;
    let live = true;
    set(s => ({ ...s, loading: true, error: null }));
    fetch(url)
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then(d => { if (live) set({ data: d as T, loading: false, error: null }); })
      .catch(e => { if (live) set({ data: null, loading: false, error: String(e.message || e) }); });
    return () => { live = false; };
  }, [url]);
  return state;
}

interface ControlsProps {
  scenarios: Scenario[]; scenario: string; mode: Mode;
  onScenario: (s: string) => void; onMode: (m: Mode) => void;
}
function Controls(p: ControlsProps) {
  return (
    <div className="controls">
      <label htmlFor="scenario">Scenario</label>
      <select id="scenario" value={p.scenario} onChange={e => p.onScenario(e.target.value)}>
        {p.scenarios.map(s => <option key={s.key} value={s.key}>{s.title}</option>)}
      </select>
      <label htmlFor="mode">Control mode</label>
      <select id="mode" value={p.mode} onChange={e => p.onMode(e.target.value as Mode)}>
        <option value="coordinated">Full NEOS coordination</option>
        <option value="uncoordinated">Battery only</option>
        <option value="no_control">No coordination</option>
      </select>
    </div>
  );
}

function Status({ loading, error }: { loading: boolean; error: string | null }) {
  if (error) return <p className="loading" role="alert">Could not load: {error}</p>;
  if (loading) return <p className="loading">Running the simulation…</p>;
  return null;
}

function mount(node: React.ReactElement) {
  // The UMD global exposes createRoot at runtime; @types only declares it under react-dom/client.
  (ReactDOM as unknown as { createRoot(el: HTMLElement): { render(n: React.ReactElement): void } })
    .createRoot(document.getElementById('app') as HTMLElement).render(node);
}

// ---- shared chart + tiles for the platform pages ---------------------------------------------
const clockOf = (k: number): string => fmtClock((((48 + k) * 0.25) % 24 + 24) % 24);   // step 0 = 12:00

interface Line { name: string; color: string; values: (number | null)[]; dash?: string }
interface Band { from: number; to: number; label?: string; color?: string }

function LineChart(p: { lines: Line[]; bands?: Band[]; height?: number; unit?: string; yMax?: number; yMin?: number;
                        marks?: { at: number; label: string }[]; label: string }) {
  const w = 900, h = p.height ?? 230, ml = 46, mr = 12, mt = 14, mb = 28, pw = w - ml - mr, ph = h - mt - mb;
  const n = Math.max(...p.lines.map(l => l.values.length));
  const all = p.lines.flatMap(l => l.values.filter((v): v is number => v !== null && Number.isFinite(v)));
  const rawLo = Math.min(0, ...all);
  const lo = p.yMin ?? (rawLo < -1.5 ? Math.floor(rawLo / 10) * 10 : rawLo), hi0 = p.yMax ?? Math.max(1, ...all);
  const hi = p.yMax ?? (hi0 <= 1.5 ? Math.ceil(hi0 * 10) / 10 : Math.ceil(hi0 / 10) * 10);
  const x = (i: number) => ml + (pw * i) / Math.max(1, n - 1);
  const y = (v: number) => mt + ph * (1 - (v - lo) / Math.max(1e-9, hi - lo));
  const path = (vals: (number | null)[]) => {
    let d = '', pen = false;
    vals.forEach((v, i) => {
      if (v === null || !Number.isFinite(v)) { pen = false; return; }
      d += `${pen ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`; pen = true;
    });
    return d;
  };
  const ticks = [0, 0.25, 0.5, 0.75, 1].map(f => Math.round(f * (n - 1)));
  return (
    <div>
      <svg viewBox={`0 0 ${w} ${h}`} style={{ width: '100%', height: h }} role="img" aria-label={p.label}>
        {(p.bands ?? []).map((b, i) => (
          <g key={i}><rect x={x(b.from)} y={mt} width={Math.max(0, x(b.to) - x(b.from))} height={ph} fill={b.color ?? '#F2F8F4'} />
            {b.label && <text x={x(b.from) + 6} y={mt + 12} fontSize="10" fill="#4B6356">{b.label}</text>}</g>))}
        {[0, 0.5, 1].map(f => <line key={f} x1={ml} x2={w - mr} y1={mt + ph * f} y2={mt + ph * f} stroke="#DCEAE1" />)}
        <text x={ml - 8} y={mt + 4} fontSize="10" fill="#80988A" textAnchor="end">{hi}{p.unit ?? ''}</text>
        <text x={ml - 8} y={mt + ph + 4} fontSize="10" fill="#80988A" textAnchor="end">{lo}{p.unit ?? ''}</text>
        {ticks.map(i => <text key={i} x={x(i)} y={h - 8} fontSize="10" fill="#80988A" textAnchor="middle">{clockOf(i)}</text>)}
        {p.lines.map(l => <path key={l.name} d={path(l.values)} fill="none" stroke={l.color} strokeWidth="2.2" strokeDasharray={l.dash} />)}
        {(p.marks ?? []).map(m => <g key={m.label}><line x1={x(m.at)} x2={x(m.at)} y1={mt} y2={mt + ph} stroke="#0F4A2C" strokeDasharray="3 3" />
          <text x={x(m.at) + 4} y={mt + ph - 6} fontSize="10" fill="#0F4A2C">{m.label}</text></g>)}
      </svg>
      <div className="legend">{p.lines.map(l => <span key={l.name}><i style={{ background: l.color }} />{l.name}</span>)}</div>
    </div>
  );
}

function Tile(p: { label: string; value: string; sub?: string; tone?: 'good' | 'bad' | 'warn' }) {
  const color = p.tone === 'good' ? '#1B7A4B' : p.tone === 'bad' ? '#AD3B2C' : p.tone === 'warn' ? '#B8781E' : undefined;
  return <div className="tile"><span>{p.label}</span><b style={color ? { color } : undefined}>{p.value}</b>{p.sub && <em>{p.sub}</em>}</div>;
}
