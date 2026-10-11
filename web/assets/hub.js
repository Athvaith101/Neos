/* generated from web/src/hub.tsx by build_tsx.js -- do not edit */
(function(){
// Shared types, hooks and components for the React + TypeScript pages.
// Concatenated in front of each page at build time (see build_tsx.js).
const {
  useState,
  useEffect,
  useMemo,
  useCallback
} = React;
const fmtClock = h => `${String(Math.floor(h)).padStart(2, '0')}:${String(Math.round(h % 1 * 60)).padStart(2, '0')}`;
function useScenarios() {
  const [list, setList] = useState([]);
  useEffect(() => {
    fetch('/api/scenarios').then(r => r.json()).then(setList).catch(() => setList([]));
  }, []);
  return list;
}

// Fetch with loading / error state; refetches when `url` changes.
function useApi(url) {
  const [state, set] = useState({
    data: null,
    loading: !!url,
    error: null
  });
  useEffect(() => {
    if (!url) return;
    let live = true;
    set(s => ({
      ...s,
      loading: true,
      error: null
    }));
    fetch(url).then(r => {
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    }).then(d => {
      if (live) set({
        data: d,
        loading: false,
        error: null
      });
    }).catch(e => {
      if (live) set({
        data: null,
        loading: false,
        error: String(e.message || e)
      });
    });
    return () => {
      live = false;
    };
  }, [url]);
  return state;
}
function Controls(p) {
  return /*#__PURE__*/React.createElement("div", {
    className: "controls"
  }, /*#__PURE__*/React.createElement("label", {
    htmlFor: "scenario"
  }, "Scenario"), /*#__PURE__*/React.createElement("select", {
    id: "scenario",
    value: p.scenario,
    onChange: e => p.onScenario(e.target.value)
  }, p.scenarios.map(s => /*#__PURE__*/React.createElement("option", {
    key: s.key,
    value: s.key
  }, s.title))), /*#__PURE__*/React.createElement("label", {
    htmlFor: "mode"
  }, "Control mode"), /*#__PURE__*/React.createElement("select", {
    id: "mode",
    value: p.mode,
    onChange: e => p.onMode(e.target.value)
  }, /*#__PURE__*/React.createElement("option", {
    value: "coordinated"
  }, "Full NEOS coordination"), /*#__PURE__*/React.createElement("option", {
    value: "uncoordinated"
  }, "Battery only"), /*#__PURE__*/React.createElement("option", {
    value: "no_control"
  }, "No coordination")));
}
function Status({
  loading,
  error
}) {
  if (error) return /*#__PURE__*/React.createElement("p", {
    className: "loading",
    role: "alert"
  }, "Could not load: ", error);
  if (loading) return /*#__PURE__*/React.createElement("p", {
    className: "loading"
  }, "Running the simulation\u2026");
  return null;
}
function mount(node) {
  // The UMD global exposes createRoot at runtime; @types only declares it under react-dom/client.
  ReactDOM.createRoot(document.getElementById('app')).render(node);
}

// ---- shared chart + tiles for the platform pages ---------------------------------------------
const clockOf = k => fmtClock(((48 + k) * 0.25 % 24 + 24) % 24); // step 0 = 12:00

function LineChart(p) {
  const w = 900,
    h = p.height ?? 230,
    ml = 46,
    mr = 12,
    mt = 14,
    mb = 28,
    pw = w - ml - mr,
    ph = h - mt - mb;
  const n = Math.max(...p.lines.map(l => l.values.length));
  const all = p.lines.flatMap(l => l.values.filter(v => v !== null && Number.isFinite(v)));
  const rawLo = Math.min(0, ...all);
  const lo = p.yMin ?? (rawLo < -1.5 ? Math.floor(rawLo / 10) * 10 : rawLo),
    hi0 = p.yMax ?? Math.max(1, ...all);
  const hi = p.yMax ?? (hi0 <= 1.5 ? Math.ceil(hi0 * 10) / 10 : Math.ceil(hi0 / 10) * 10);
  const x = i => ml + pw * i / Math.max(1, n - 1);
  const y = v => mt + ph * (1 - (v - lo) / Math.max(1e-9, hi - lo));
  const path = vals => {
    let d = '',
      pen = false;
    vals.forEach((v, i) => {
      if (v === null || !Number.isFinite(v)) {
        pen = false;
        return;
      }
      d += `${pen ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
      pen = true;
    });
    return d;
  };
  const ticks = [0, 0.25, 0.5, 0.75, 1].map(f => Math.round(f * (n - 1)));
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("svg", {
    viewBox: `0 0 ${w} ${h}`,
    style: {
      width: '100%',
      height: h
    },
    role: "img",
    "aria-label": p.label
  }, (p.bands ?? []).map((b, i) => /*#__PURE__*/React.createElement("g", {
    key: i
  }, /*#__PURE__*/React.createElement("rect", {
    x: x(b.from),
    y: mt,
    width: Math.max(0, x(b.to) - x(b.from)),
    height: ph,
    fill: b.color ?? '#F2F8F4'
  }), b.label && /*#__PURE__*/React.createElement("text", {
    x: x(b.from) + 6,
    y: mt + 12,
    fontSize: "10",
    fill: "#4B6356"
  }, b.label))), [0, 0.5, 1].map(f => /*#__PURE__*/React.createElement("line", {
    key: f,
    x1: ml,
    x2: w - mr,
    y1: mt + ph * f,
    y2: mt + ph * f,
    stroke: "#DCEAE1"
  })), /*#__PURE__*/React.createElement("text", {
    x: ml - 8,
    y: mt + 4,
    fontSize: "10",
    fill: "#80988A",
    textAnchor: "end"
  }, hi, p.unit ?? ''), /*#__PURE__*/React.createElement("text", {
    x: ml - 8,
    y: mt + ph + 4,
    fontSize: "10",
    fill: "#80988A",
    textAnchor: "end"
  }, lo, p.unit ?? ''), ticks.map(i => /*#__PURE__*/React.createElement("text", {
    key: i,
    x: x(i),
    y: h - 8,
    fontSize: "10",
    fill: "#80988A",
    textAnchor: "middle"
  }, clockOf(i))), p.lines.map(l => /*#__PURE__*/React.createElement("path", {
    key: l.name,
    d: path(l.values),
    fill: "none",
    stroke: l.color,
    strokeWidth: "2.2",
    strokeDasharray: l.dash
  })), (p.marks ?? []).map(m => /*#__PURE__*/React.createElement("g", {
    key: m.label
  }, /*#__PURE__*/React.createElement("line", {
    x1: x(m.at),
    x2: x(m.at),
    y1: mt,
    y2: mt + ph,
    stroke: "#0F4A2C",
    strokeDasharray: "3 3"
  }), /*#__PURE__*/React.createElement("text", {
    x: x(m.at) + 4,
    y: mt + ph - 6,
    fontSize: "10",
    fill: "#0F4A2C"
  }, m.label)))), /*#__PURE__*/React.createElement("div", {
    className: "legend"
  }, p.lines.map(l => /*#__PURE__*/React.createElement("span", {
    key: l.name
  }, /*#__PURE__*/React.createElement("i", {
    style: {
      background: l.color
    }
  }), l.name))));
}
function Tile(p) {
  const color = p.tone === 'good' ? '#1B7A4B' : p.tone === 'bad' ? '#AD3B2C' : p.tone === 'warn' ? '#B8781E' : undefined;
  return /*#__PURE__*/React.createElement("div", {
    className: "tile"
  }, /*#__PURE__*/React.createElement("span", null, p.label), /*#__PURE__*/React.createElement("b", {
    style: color ? {
      color
    } : undefined
  }, p.value), p.sub && /*#__PURE__*/React.createElement("em", null, p.sub));
}
const post = u => fetch(u, {
  method: 'POST'
}).then(async r => ({
  ok: r.ok,
  body: await r.json().catch(() => ({}))
}));
const step2clock = k => fmtClock(k * 0.25 % 24);
function HubPage() {
  const [tick, setTick] = useState(0);
  const [hh, setHh] = useState('hh01');
  const [kw, setKw] = useState(1.5);
  const [start, setStart] = useState(72);
  const [end, setEnd] = useState(80);
  const [sms, setSms] = useState('PLEDGE 1.5 72 80');
  const [msg, setMsg] = useState('');
  const {
    data: d,
    loading,
    error
  } = useApi(`/api/participation?start=${start}&end=${end}&_=${tick}`);
  const act = async (u, label) => {
    const r = await post(u);
    setMsg(r.ok ? `${label}: ${JSON.stringify(r.body)}` : `${label} failed: ${r.body.detail || 'error'}`);
    setTick(t => t + 1);
  };
  const me = d?.households.find(x => x.hh === hh);
  const q = `start=${start}&end=${end}`;
  return /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("p", {
    className: "scope"
  }, d?.disclaimer ?? 'Simulated protocol.', " Steps are 15 minutes: step 72 is 18:00."), /*#__PURE__*/React.createElement("div", {
    className: "controls"
  }, /*#__PURE__*/React.createElement("label", {
    htmlFor: "hh"
  }, "Household"), /*#__PURE__*/React.createElement("select", {
    id: "hh",
    value: hh,
    onChange: e => setHh(e.target.value)
  }, (d?.households ?? []).map(x => /*#__PURE__*/React.createElement("option", {
    key: x.hh,
    value: x.hh
  }, x.hh))), /*#__PURE__*/React.createElement("label", {
    htmlFor: "kw"
  }, "kW"), /*#__PURE__*/React.createElement("input", {
    id: "kw",
    type: "number",
    min: 0.1,
    max: 10,
    step: 0.1,
    value: kw,
    onChange: e => setKw(+e.target.value),
    style: {
      width: 70,
      padding: 8,
      borderRadius: 8,
      border: '1px solid var(--line-strong)'
    }
  }), /*#__PURE__*/React.createElement("label", {
    htmlFor: "st"
  }, "From step"), /*#__PURE__*/React.createElement("input", {
    id: "st",
    type: "number",
    min: 0,
    value: start,
    onChange: e => setStart(+e.target.value),
    style: {
      width: 70,
      padding: 8,
      borderRadius: 8,
      border: '1px solid var(--line-strong)'
    }
  }), /*#__PURE__*/React.createElement("label", {
    htmlFor: "en"
  }, "To step"), /*#__PURE__*/React.createElement("input", {
    id: "en",
    type: "number",
    min: 1,
    value: end,
    onChange: e => setEnd(+e.target.value),
    style: {
      width: 70,
      padding: 8,
      borderRadius: 8,
      border: '1px solid var(--line-strong)'
    }
  }), /*#__PURE__*/React.createElement("button", {
    className: "btn",
    onClick: () => act(`/api/participation/${hh}/pledge?kw=${kw}&start=${start}&end=${end}`, 'Pledge')
  }, "Pledge"), /*#__PURE__*/React.createElement("button", {
    className: "btn ghost",
    onClick: () => act(`/api/participation/${hh}/withdraw`, 'Withdraw')
  }, "Withdraw")), /*#__PURE__*/React.createElement("div", {
    className: "controls"
  }, /*#__PURE__*/React.createElement("span", null, hh, " phone is ", /*#__PURE__*/React.createElement("b", null, me?.online ? 'online' : 'OFFLINE'), ", ", me?.queued ?? 0, " queued"), /*#__PURE__*/React.createElement("button", {
    className: "btn ghost",
    onClick: () => act(`/api/participation/${hh}/online?online=${!(me?.online ?? true)}`, 'Connectivity')
  }, me?.online ? 'Go offline' : 'Come online'), /*#__PURE__*/React.createElement("button", {
    className: "btn ghost",
    onClick: () => act(`/api/participation/${hh}/sync`, 'Sync'),
    disabled: !me?.online
  }, "Sync queue"), /*#__PURE__*/React.createElement("label", {
    htmlFor: "sms"
  }, "SMS"), /*#__PURE__*/React.createElement("input", {
    id: "sms",
    value: sms,
    maxLength: 80,
    onChange: e => setSms(e.target.value),
    style: {
      width: 190,
      padding: 8,
      borderRadius: 8,
      border: '1px solid var(--line-strong)'
    }
  }), /*#__PURE__*/React.createElement("button", {
    className: "btn ghost",
    onClick: () => act(`/api/participation/sms?sender=${hh}&text=${encodeURIComponent(sms)}`, 'SMS')
  }, "Send")), msg && /*#__PURE__*/React.createElement("p", {
    className: "hint",
    role: "status"
  }, msg), /*#__PURE__*/React.createElement(Status, {
    loading: loading && !d,
    error: error
  }), d && /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "tiles"
  }, /*#__PURE__*/React.createElement(Tile, {
    label: `Confirmed pledges, ${step2clock(start)}–${step2clock(end)}`,
    value: `${d.committed.raw_kw.toFixed(1)} kW`,
    sub: `${d.committed.households} households`
  }), /*#__PURE__*/React.createElement(Tile, {
    label: "Expected after reliability discount",
    value: `${d.committed.expected_kw.toFixed(1)} kW`,
    tone: "warn",
    sub: "an estimate, not a guarantee"
  }), /*#__PURE__*/React.createElement(Tile, {
    label: "Waiting on offline phones",
    value: `${d.queued.length}`,
    tone: d.queued.length ? 'warn' : 'good',
    sub: "not counted until synced"
  })), /*#__PURE__*/React.createElement("div", {
    className: "panel panel-pad",
    style: {
      marginBottom: 16
    }
  }, /*#__PURE__*/React.createElement("h3", {
    className: "sec"
  }, "Households"), /*#__PURE__*/React.createElement("div", {
    style: {
      overflowX: 'auto'
    }
  }, /*#__PURE__*/React.createElement("table", {
    className: "data"
  }, /*#__PURE__*/React.createElement("tbody", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Household"), /*#__PURE__*/React.createElement("th", null, "Phone"), /*#__PURE__*/React.createElement("th", null, "Confirmed pledge"), /*#__PURE__*/React.createElement("th", null, "Past delivery"), /*#__PURE__*/React.createElement("th", null, "Points (kWh)")), d.households.map(x => /*#__PURE__*/React.createElement("tr", {
    key: x.hh
  }, /*#__PURE__*/React.createElement("td", null, x.hh), /*#__PURE__*/React.createElement("td", null, x.online ? 'online' : `offline (${x.queued})`), /*#__PURE__*/React.createElement("td", null, x.pledge ? `${x.pledge.kw} kW, ${step2clock(x.pledge.start)}–${step2clock(x.pledge.end)}` : '—'), /*#__PURE__*/React.createElement("td", null, (100 * x.reliability).toFixed(0), "%"), /*#__PURE__*/React.createElement("td", null, x.points_kwh.toFixed(2))))))), /*#__PURE__*/React.createElement("div", {
    className: "controls",
    style: {
      marginTop: 12
    }
  }, /*#__PURE__*/React.createElement("button", {
    className: "btn ghost",
    onClick: () => act(`/api/participation/settle?${q}&fraction=1`, 'Settle at 100%')
  }, "Simulate delivery at 100%"), /*#__PURE__*/React.createElement("button", {
    className: "btn ghost",
    onClick: () => act(`/api/participation/settle?${q}&fraction=0.5`, 'Settle at 50%')
  }, "Simulate delivery at 50%"), /*#__PURE__*/React.createElement("button", {
    className: "btn ghost",
    onClick: () => act('/api/participation/reset', 'Reset')
  }, "Reset hub")), /*#__PURE__*/React.createElement("p", {
    className: "hint",
    style: {
      margin: 0
    }
  }, d.committed.note, ". Points are participation credit, never money.")), /*#__PURE__*/React.createElement("div", {
    className: "panel panel-pad"
  }, /*#__PURE__*/React.createElement("h3", {
    className: "sec"
  }, "Every event and what happened to it"), /*#__PURE__*/React.createElement("div", {
    style: {
      overflowX: 'auto'
    }
  }, /*#__PURE__*/React.createElement("table", {
    className: "data"
  }, /*#__PURE__*/React.createElement("tbody", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Who"), /*#__PURE__*/React.createElement("th", null, "Seq"), /*#__PURE__*/React.createElement("th", null, "Event"), /*#__PURE__*/React.createElement("th", null, "Via"), /*#__PURE__*/React.createElement("th", null, "Outcome"), /*#__PURE__*/React.createElement("th", null, "Why")), d.log.slice().reverse().map((e, i) => /*#__PURE__*/React.createElement("tr", {
    key: i
  }, /*#__PURE__*/React.createElement("td", null, e.hh), /*#__PURE__*/React.createElement("td", null, e.seq), /*#__PURE__*/React.createElement("td", null, e.kind, e.kind === 'pledge' ? ` ${e.kw} kW ${e.start}–${e.end}` : ''), /*#__PURE__*/React.createElement("td", null, e.channel), /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("b", null, e.outcome)), /*#__PURE__*/React.createElement("td", null, e.reason))), d.queued.map((e, i) => /*#__PURE__*/React.createElement("tr", {
    key: 'q' + i
  }, /*#__PURE__*/React.createElement("td", null, e.hh), /*#__PURE__*/React.createElement("td", null, e.seq), /*#__PURE__*/React.createElement("td", null, e.kind), /*#__PURE__*/React.createElement("td", null, "app"), /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("b", null, "queued")), /*#__PURE__*/React.createElement("td", null, "phone offline")))))))));
}
mount(/*#__PURE__*/React.createElement(HubPage, null));
})();

