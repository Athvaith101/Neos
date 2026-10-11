interface HHRow { hh: string; pledge: { start: number; end: number; kw: number; seq: number } | null; reliability: number; points_kwh: number; online: boolean; queued: number }
interface LogRow { hh: string; seq: number; kind: string; start: number; end: number; kw: number; channel: string; outcome: string; reason: string }
interface HubState { households: HHRow[]; log: LogRow[]; queued: LogRow[]; committed: { raw_kw: number; expected_kw: number; households: number; note: string }; window: [number, number]; disclaimer: string }

const post = (u: string) => fetch(u, { method: 'POST' }).then(async r => ({ ok: r.ok, body: await r.json().catch(() => ({})) }));
const step2clock = (k: number) => fmtClock((k * 0.25) % 24);

function HubPage() {
  const [tick, setTick] = useState(0);
  const [hh, setHh] = useState('hh01');
  const [kw, setKw] = useState(1.5);
  const [start, setStart] = useState(72);
  const [end, setEnd] = useState(80);
  const [sms, setSms] = useState('PLEDGE 1.5 72 80');
  const [msg, setMsg] = useState('');
  const { data: d, loading, error } = useApi<HubState>(`/api/participation?start=${start}&end=${end}&_=${tick}`);
  const act = async (u: string, label: string) => {
    const r = await post(u);
    setMsg(r.ok ? `${label}: ${JSON.stringify(r.body)}` : `${label} failed: ${r.body.detail || 'error'}`);
    setTick(t => t + 1);
  };
  const me = d?.households.find(x => x.hh === hh);
  const q = `start=${start}&end=${end}`;
  return (<>
    <p className="scope">{d?.disclaimer ?? 'Simulated protocol.'} Steps are 15 minutes: step 72 is 18:00.</p>
    <div className="controls">
      <label htmlFor="hh">Household</label>
      <select id="hh" value={hh} onChange={e => setHh(e.target.value)}>{(d?.households ?? []).map(x => <option key={x.hh} value={x.hh}>{x.hh}</option>)}</select>
      <label htmlFor="kw">kW</label><input id="kw" type="number" min={0.1} max={10} step={0.1} value={kw} onChange={e => setKw(+e.target.value)} style={{ width: 70, padding: 8, borderRadius: 8, border: '1px solid var(--line-strong)' }} />
      <label htmlFor="st">From step</label><input id="st" type="number" min={0} value={start} onChange={e => setStart(+e.target.value)} style={{ width: 70, padding: 8, borderRadius: 8, border: '1px solid var(--line-strong)' }} />
      <label htmlFor="en">To step</label><input id="en" type="number" min={1} value={end} onChange={e => setEnd(+e.target.value)} style={{ width: 70, padding: 8, borderRadius: 8, border: '1px solid var(--line-strong)' }} />
      <button className="btn" onClick={() => act(`/api/participation/${hh}/pledge?kw=${kw}&start=${start}&end=${end}`, 'Pledge')}>Pledge</button>
      <button className="btn ghost" onClick={() => act(`/api/participation/${hh}/withdraw`, 'Withdraw')}>Withdraw</button>
    </div>
    <div className="controls">
      <span>{hh} phone is <b>{me?.online ? 'online' : 'OFFLINE'}</b>, {me?.queued ?? 0} queued</span>
      <button className="btn ghost" onClick={() => act(`/api/participation/${hh}/online?online=${!(me?.online ?? true)}`, 'Connectivity')}>{me?.online ? 'Go offline' : 'Come online'}</button>
      <button className="btn ghost" onClick={() => act(`/api/participation/${hh}/sync`, 'Sync')} disabled={!me?.online}>Sync queue</button>
      <label htmlFor="sms">SMS</label><input id="sms" value={sms} maxLength={80} onChange={e => setSms(e.target.value)} style={{ width: 190, padding: 8, borderRadius: 8, border: '1px solid var(--line-strong)' }} />
      <button className="btn ghost" onClick={() => act(`/api/participation/sms?sender=${hh}&text=${encodeURIComponent(sms)}`, 'SMS')}>Send</button>
    </div>
    {msg && <p className="hint" role="status">{msg}</p>}
    <Status loading={loading && !d} error={error} />
    {d && (<>
      <div className="tiles">
        <Tile label={`Confirmed pledges, ${step2clock(start)}–${step2clock(end)}`} value={`${d.committed.raw_kw.toFixed(1)} kW`} sub={`${d.committed.households} households`} />
        <Tile label="Expected after reliability discount" value={`${d.committed.expected_kw.toFixed(1)} kW`} tone="warn" sub="an estimate, not a guarantee" />
        <Tile label="Waiting on offline phones" value={`${d.queued.length}`} tone={d.queued.length ? 'warn' : 'good'} sub="not counted until synced" />
      </div>
      <div className="panel panel-pad" style={{ marginBottom: 16 }}>
        <h3 className="sec">Households</h3>
        <div style={{ overflowX: 'auto' }}><table className="data"><tbody>
          <tr><th>Household</th><th>Phone</th><th>Confirmed pledge</th><th>Past delivery</th><th>Points (kWh)</th></tr>
          {d.households.map(x => <tr key={x.hh}><td>{x.hh}</td><td>{x.online ? 'online' : `offline (${x.queued})`}</td>
            <td>{x.pledge ? `${x.pledge.kw} kW, ${step2clock(x.pledge.start)}–${step2clock(x.pledge.end)}` : '—'}</td>
            <td>{(100 * x.reliability).toFixed(0)}%</td><td>{x.points_kwh.toFixed(2)}</td></tr>)}
        </tbody></table></div>
        <div className="controls" style={{ marginTop: 12 }}>
          <button className="btn ghost" onClick={() => act(`/api/participation/settle?${q}&fraction=1`, 'Settle at 100%')}>Simulate delivery at 100%</button>
          <button className="btn ghost" onClick={() => act(`/api/participation/settle?${q}&fraction=0.5`, 'Settle at 50%')}>Simulate delivery at 50%</button>
          <button className="btn ghost" onClick={() => act('/api/participation/reset', 'Reset')}>Reset hub</button>
        </div>
        <p className="hint" style={{ margin: 0 }}>{d.committed.note}. Points are participation credit, never money.</p>
      </div>
      <div className="panel panel-pad">
        <h3 className="sec">Every event and what happened to it</h3>
        <div style={{ overflowX: 'auto' }}><table className="data"><tbody>
          <tr><th>Who</th><th>Seq</th><th>Event</th><th>Via</th><th>Outcome</th><th>Why</th></tr>
          {d.log.slice().reverse().map((e, i) => <tr key={i}><td>{e.hh}</td><td>{e.seq}</td>
            <td>{e.kind}{e.kind === 'pledge' ? ` ${e.kw} kW ${e.start}–${e.end}` : ''}</td><td>{e.channel}</td><td><b>{e.outcome}</b></td><td>{e.reason}</td></tr>)}
          {d.queued.map((e, i) => <tr key={'q' + i}><td>{e.hh}</td><td>{e.seq}</td><td>{e.kind}</td><td>app</td><td><b>queued</b></td><td>phone offline</td></tr>)}
        </tbody></table></div>
      </div>
    </>)}
  </>);
}
mount(<HubPage />);

