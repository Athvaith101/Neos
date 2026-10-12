interface RuralMetrics {
  scenario: string; arm: 'naive' | 'neos'; backend: string; outage_hours: number;
  clinic_unserved_kwh: number; tier2_unserved_kwh: number; shops_unserved_kwh: number;
  clinic_hours_full_power_in_outage: number; clinic_hours_degraded_in_outage: number;
  clinic_first_failure_h_after_outage: number | null;
  cold_chain_hours_above_8C: number; water_outage_hours: number; mill_job_done: boolean;
  min_voltage: number; min_soc: number; pv_utilisation_pct: number;
  island_steps_verified_pct: number | null; grid_steps_verified_pct: number | null;
  energy_audit: { battery_residual_kwh: number; clip_events: number };
  assumptions: { t1_endurance_h: number; island: string };
}
interface RuralSeries { soc: number[]; mode: number[]; cold_c: number[]; tank: number[]; clinic_frac: number[]; pv: number[];
                        tier_frac: Record<string, number[]> }
interface Arm { metrics: RuralMetrics; series: RuralSeries }
interface Village { homes: number; pv_kwp: number; battery_kwh: number; battery_kw: number }
interface RuralCompare { village: Village; is_default_village: boolean; scenario: string; title: string; tiers: Record<string, string>; naive: Arm; neos: Arm }
interface RuralScenario { key: string; title: string; outage: [number, number] | null }

function bandsOf(mode: number[]): Band[] {
  const out: Band[] = []; let a = -1;
  mode.forEach((m, i) => { if (m > 0.5 && a < 0) a = i; if ((m <= 0.5 || i === mode.length - 1) && a >= 0) { out.push({ from: a, to: m > 0.5 ? i : i - 1, label: out.length ? undefined : 'grid outage (island)', color: '#FBF1E3' }); a = -1; } });
  return out;
}

function ArmCard({ a, title, cls }: { a: Arm; title: string; cls: 'neos' | 'naive' }) {
  const m = a.metrics;
  const ok = m.clinic_unserved_kwh < 0.05;
  return (
    <div className="panel panel-pad">
      <div className="armhead"><span className={`chip-arm ${cls}`}>{cls === 'neos' ? 'NEOS' : 'TIER-BLIND'}</span>{title}</div>
      <div className="tiles" style={{ gridTemplateColumns: 'repeat(2,1fr)' }}>
        <Tile label="Clinic at full power" value={m.outage_hours ? `${m.clinic_hours_full_power_in_outage.toFixed(1)} of ${m.outage_hours.toFixed(0)} h` : 'no outage'} tone={ok ? 'good' : 'bad'}
              sub={m.clinic_first_failure_h_after_outage !== null ? `first failure ${m.clinic_first_failure_h_after_outage.toFixed(1)} h in` : undefined} />
        <Tile label="Clinic energy unserved" value={`${m.clinic_unserved_kwh.toFixed(1)} kWh`} tone={ok ? 'good' : 'bad'} />
        <Tile label="Fridge + lights unserved" value={`${m.tier2_unserved_kwh.toFixed(1)} kWh`} tone={m.tier2_unserved_kwh < 0.05 ? 'good' : 'bad'} />
        <Tile label="Shops unserved" value={`${m.shops_unserved_kwh.toFixed(1)} kWh`} tone={m.shops_unserved_kwh > 0.5 ? 'warn' : 'good'} sub="tier 3: shed before tiers 1–2" />
        <Tile label="Cold chain above 8 °C" value={`${m.cold_chain_hours_above_8C.toFixed(1)} h`} tone={m.cold_chain_hours_above_8C > 0 ? 'bad' : 'good'} />
        <Tile label="Water outage" value={`${m.water_outage_hours.toFixed(1)} h`} tone={m.water_outage_hours > 0 ? 'bad' : 'good'} />
      </div>
      <p className="hint" style={{ margin: 0 }}>Battery lowest {(100 * m.min_soc).toFixed(0)}% · solar used {m.pv_utilisation_pct.toFixed(0)}% · island steps AC-verified {m.island_steps_verified_pct === null ? 'n/a' : m.island_steps_verified_pct.toFixed(0) + '%'} · battery energy audit {m.energy_audit.battery_residual_kwh.toExponential(0)} kWh, {m.energy_audit.clip_events} clips · {m.backend}</p>
    </div>
  );
}

function RuralPage() {
  const [list, setList] = useState<RuralScenario[]>([]);
  const [sc, setSc] = useState('cloudy_outage');
  const [seed, setSeed] = useState(0);
  const [hrs, setHrs] = useState(12);
  const [go, setGo] = useState(0);
  const [vil, setVil] = useState<Village>({ homes: 90, pv_kwp: 45, battery_kwh: 120, battery_kw: 40 });
  useEffect(() => { fetch('/api/rural/scenarios').then(r => r.json()).then(setList).catch(() => setList([])); }, []);
  const url = `/api/rural/compare?scenario=${sc}&seed=${seed}&t1_endurance_h=${hrs}&homes=${vil.homes}&pv_kwp=${vil.pv_kwp}&battery_kwh=${vil.battery_kwh}&battery_kw=${vil.battery_kw}&_=${go}`;
  const { data, loading, error } = useApi<RuralCompare>(url);
  const d = data;
  const lines = (key: 'soc' | 'cold_c' | 'tank', scale = 1): Line[] => d ? [
    { name: 'NEOS', color: '#1B7A4B', values: d.neos.series[key].map(v => v * scale) },
    { name: 'Tier-blind', color: '#B07A2A', values: d.naive.series[key].map(v => v * scale), dash: '5 3' }] : [];
  return (<>
    <p className="scope">Synthetic village (default 90 homes, 45 kWp solar, 120 kWh battery; editable below) on a 1.2 km weak feeder (1.5× impedance, a scenario-design choice) and a community section with a clinic, fridge, lights, three shops, grain mill, cold store, water pump, solar and a battery. In an outage the community section is islanded on a grid-forming inverter and checked by the same AC solver. Island dynamics, black start, protection and reconnection are <b>not</b> modelled. Homes are off in both arms.</p>
    <div className="controls">
      <label htmlFor="rs">Scenario</label>
      <select id="rs" value={sc} onChange={e => setSc(e.target.value)}>{list.map(s => <option key={s.key} value={s.key}>{s.title}</option>)}</select>
      <label htmlFor="rd">Village seed</label>
      <input id="rd" type="number" min={0} max={99} value={seed} onChange={e => setSeed(+e.target.value)} style={{ width: 70, padding: 8, borderRadius: 8, border: '1px solid var(--line-strong)' }} />
      <label htmlFor="rh">Tier-1/2 endurance (h)</label>
      <input id="rh" type="range" min={2} max={24} value={hrs} onChange={e => setHrs(+e.target.value)} />
      <b style={{ fontFamily: 'var(--font-mono)' }}>{hrs} h</b>
      <button className="btn ghost" onClick={() => setGo(go + 1)}>Re-run</button>
    </div>
    <div className="controls">
      {([['homes', 'Homes', 10, 300, 1], ['pv_kwp', 'Solar kWp', 5, 150, 1], ['battery_kwh', 'Battery kWh', 20, 400, 5], ['battery_kw', 'Battery kW', 5, 100, 1]] as const).map(([k, lab, lo, hi, st]) => (
        <span key={k} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <label htmlFor={'v_' + k}>{lab}</label>
          <input id={'v_' + k} type="number" min={lo} max={hi} step={st} value={vil[k]}
            onChange={e => { const n = +e.target.value; setVil({ ...vil, [k]: Math.min(hi, Math.max(lo, isNaN(n) ? lo : n)) }); }}
            style={{ width: 80, padding: 8, borderRadius: 8, border: '1px solid var(--line-strong)' }} />
        </span>))}
      <button className="btn ghost" onClick={() => setVil({ homes: 90, pv_kwp: 45, battery_kwh: 120, battery_kw: 40 })}>Reset to default</button>
    </div>
    {d && !d.is_default_village && <p className="hint" style={{ marginTop: -10 }}><b>Custom village.</b> Inverter = 1.25 × battery kW and the transformer scales with homes (100 kVA at 90 homes). The archived held-out study on the Evidence page covers the default village only; this run is a single seed.</p>}
    <p className="hint" style={{ marginTop: -10 }}>The endurance is a charter parameter: how long the battery must be able to carry the clinic, fridge and lights. A longer charter protects them longer and sheds shops sooner.</p>
    <Status loading={loading} error={error} />
    {d && !loading && (<>
      <div className="two"><ArmCard a={d.neos} cls="neos" title="Priority tiers, confidence-aware reserve" /><ArmCard a={d.naive} cls="naive" title="Everyone shares equally" /></div>
      <div className="panel panel-pad" style={{ marginBottom: 16 }}>
        <h3 className="sec">Battery state of charge</h3>
        <LineChart label="Battery state of charge" unit="%" yMin={0} yMax={100} bands={bandsOf(d.neos.series.mode)} lines={lines('soc', 100)} />
      </div>
      <div className="panel panel-pad" style={{ marginBottom: 16 }}>
        <h3 className="sec">Clinic supply (1 = full power)</h3>
        <LineChart label="Clinic supply fraction" yMin={0} yMax={1.1} bands={bandsOf(d.neos.series.mode)}
          lines={[{ name: 'NEOS', color: '#1B7A4B', values: d.neos.series.clinic_frac }, { name: 'Tier-blind', color: '#B07A2A', values: d.naive.series.clinic_frac, dash: '5 3' }]} />
      </div>
      <div className="two">
        <div className="panel panel-pad"><h3 className="sec">Cold store temperature (limit 8 °C)</h3>
          <LineChart label="Cold store temperature" unit=" °C" yMin={0} yMax={14} bands={bandsOf(d.neos.series.mode)} lines={lines('cold_c')} /></div>
        <div className="panel panel-pad"><h3 className="sec">Water tank (% full)</h3>
          <LineChart label="Water tank level" unit="%" yMin={0} yMax={100} bands={bandsOf(d.neos.series.mode)} lines={lines('tank', 100 / 40)} /></div>
      </div>
      <table className="data"><tbody>
        {Object.entries(d.tiers).map(([t, n]) => <tr key={t}><th>Tier {t}</th><td>{n}</td></tr>)}
      </tbody></table>
      <p className="disclaimer">Protecting tiers 1–2 on a poor-sun day means shedding tier 3 earlier; the shops column shows that cost, it is not hidden. All loads, weather and tariffs are synthetic.</p>
    </>)}
  </>);
}
mount(<RuralPage />);
