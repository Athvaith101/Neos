/* generated from web/src/discom.tsx by build_tsx.js -- do not edit */
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
const phaseTone = p => p > 100 ? 'over' : p >= 85 ? 'stressed' : 'normal';
const toneColor = {
  normal: '#1B7A4B',
  stressed: '#B8781E',
  over: '#AD3B2C'
};
function PhasePanel() {
  const scen = 'normal';
  const [mode, setMode] = useState('uncoordinated');
  const {
    data,
    loading,
    error
  } = useApi(`/api/phase?scenario=${scen}&mode=${mode}`);
  const [idx, setIdx] = useState(36);
  const d = data;
  return /*#__PURE__*/React.createElement("div", {
    className: "panel panel-pad",
    style: {
      marginBottom: 20
    }
  }, /*#__PURE__*/React.createElement("h3", {
    className: "sec"
  }, "Transformer, phase by phase"), /*#__PURE__*/React.createElement("div", {
    className: "controls",
    style: {
      margin: '0 0 14px'
    }
  }, /*#__PURE__*/React.createElement("label", {
    htmlFor: "pm"
  }, "View"), /*#__PURE__*/React.createElement("select", {
    id: "pm",
    value: mode,
    onChange: e => setMode(e.target.value)
  }, /*#__PURE__*/React.createElement("option", {
    value: "uncoordinated"
  }, "Without coordination"), /*#__PURE__*/React.createElement("option", {
    value: "coordinated"
  }, "With NEOS"))), /*#__PURE__*/React.createElement(Status, {
    loading: loading,
    error: error
  }), d && !loading && /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "scrub"
  }, /*#__PURE__*/React.createElement("label", {
    htmlFor: "ps"
  }, "Time"), /*#__PURE__*/React.createElement("input", {
    id: "ps",
    type: "range",
    min: 0,
    max: 95,
    value: idx,
    onChange: e => setIdx(+e.target.value)
  }), /*#__PURE__*/React.createElement("b", null, clockOf(idx))), /*#__PURE__*/React.createElement("div", {
    className: "tiles"
  }, /*#__PURE__*/React.createElement(Tile, {
    label: "Transformer (total)",
    value: `${d.tx_loading[idx].toFixed(0)}%`
  }), /*#__PURE__*/React.createElement(Tile, {
    label: "Worst single phase",
    value: `${Math.max(d.phases.A[idx], d.phases.B[idx], d.phases.C[idx]).toFixed(0)}%`,
    tone: Math.max(d.phases.A[idx], d.phases.B[idx], d.phases.C[idx]) > 100 ? 'bad' : undefined,
    sub: "of that phase's rating"
  }), /*#__PURE__*/React.createElement(Tile, {
    label: "Peak phase, whole day",
    value: `${d.peak_phase_loading.toFixed(0)}%`,
    sub: `${d.hours_phase_over_100.toFixed(1)} h above 100%`
  }), /*#__PURE__*/React.createElement(Tile, {
    label: "Imbalance (p95)",
    value: d.imbalance_p95.toFixed(2),
    sub: "(max\u2212min)/mean kVA"
  })), ['A', 'B', 'C'].map(ph => {
    const v = d.phases[ph][idx];
    const t = phaseTone(v);
    return /*#__PURE__*/React.createElement("div", {
      className: "phase-row",
      key: ph
    }, /*#__PURE__*/React.createElement("span", {
      className: "lab"
    }, "Phase ", ph), /*#__PURE__*/React.createElement("div", {
      className: "bar-track",
      style: {
        flex: 1
      }
    }, /*#__PURE__*/React.createElement("div", {
      className: "bar-fill",
      style: {
        width: `${Math.min(100, v)}%`,
        background: toneColor[t]
      }
    })), /*#__PURE__*/React.createElement("span", {
      className: "val",
      style: {
        width: 54,
        textAlign: 'right',
        fontFamily: 'var(--font-mono)',
        fontSize: 12.5
      }
    }, v.toFixed(0), "%"), /*#__PURE__*/React.createElement("span", {
      className: "st",
      style: {
        color: toneColor[t]
      }
    }, t));
  }), /*#__PURE__*/React.createElement(LineChart, {
    label: "Loading by phase across the day",
    unit: "%",
    yMin: 0,
    yMax: Math.max(120, Math.ceil(d.peak_phase_loading / 10) * 10),
    marks: [{
      at: idx,
      label: clockOf(idx)
    }],
    lines: [{
      name: 'Phase A',
      color: '#1B7A4B',
      values: d.phases.A
    }, {
      name: 'Phase B',
      color: '#2E7D9E',
      values: d.phases.B
    }, {
      name: 'Phase C',
      color: '#B8781E',
      values: d.phases.C
    }, {
      name: 'Transformer total',
      color: '#0E2A1D',
      values: d.tx_loading,
      dash: '4 3'
    }]
  }), /*#__PURE__*/React.createElement("p", {
    className: "hint"
  }, d.note, " NEOS lowers load on all phases; it does not rebalance phases (no household phase switching). Aggregate meters cannot say which appliance caused a phase's load.")));
}
function RequestPanel() {
  const [f, setF] = useState({
    start_step: 44,
    n_steps: 6,
    kw: 30,
    notice_steps: 2
  });
  const [url, setUrl] = useState(null);
  const {
    data,
    loading,
    error
  } = useApi(url);
  const set = k => e => setF({
    ...f,
    [k]: +e.target.value
  });
  const send = () => setUrl(`/api/discom?scenario=normal&start_step=${f.start_step}&n_steps=${f.n_steps}&kw=${f.kw}&notice_steps=${f.notice_steps}&_=${Date.now()}`);
  const d = data;
  return /*#__PURE__*/React.createElement("div", {
    className: "panel panel-pad"
  }, /*#__PURE__*/React.createElement("h3", {
    className: "sec"
  }, "Send the neighbourhood a flexibility request"), /*#__PURE__*/React.createElement("p", {
    className: "hint",
    style: {
      marginTop: -6,
      marginBottom: 14
    }
  }, "The utility asks for a reduction in net import. NEOS asks its own optimiser what it can sustain, answers, and the response is then measured against an identical run without the request."), /*#__PURE__*/React.createElement("div", {
    className: "form"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("label", {
    htmlFor: "f1"
  }, "Start (step; 0 = 12:00, 44 = 23:00)"), /*#__PURE__*/React.createElement("input", {
    id: "f1",
    type: "number",
    min: 0,
    max: 95,
    value: f.start_step,
    onChange: set('start_step')
  })), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("label", {
    htmlFor: "f2"
  }, "Duration (15-min steps)"), /*#__PURE__*/React.createElement("input", {
    id: "f2",
    type: "number",
    min: 1,
    max: 24,
    value: f.n_steps,
    onChange: set('n_steps')
  })), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("label", {
    htmlFor: "f3"
  }, "Requested reduction (kW)"), /*#__PURE__*/React.createElement("input", {
    id: "f3",
    type: "number",
    min: 1,
    max: 2000,
    value: f.kw,
    onChange: set('kw')
  })), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("label", {
    htmlFor: "f4"
  }, "Notice (15-min steps)"), /*#__PURE__*/React.createElement("input", {
    id: "f4",
    type: "number",
    min: 1,
    max: 16,
    value: f.notice_steps,
    onChange: set('notice_steps')
  }))), /*#__PURE__*/React.createElement("button", {
    className: "btn",
    onClick: send,
    disabled: loading
  }, loading ? 'Simulating…' : 'Send request'), /*#__PURE__*/React.createElement("span", {
    className: "hint",
    style: {
      marginLeft: 12
    }
  }, "Two full simulated days plus optimiser probes; allow up to a minute on OpenDSS."), error && /*#__PURE__*/React.createElement("p", {
    className: "loading",
    role: "alert"
  }, "Could not load: ", error), d && !loading && /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 22
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: `decision ${d.decision.action}`
  }, /*#__PURE__*/React.createElement("h3", null, d.decision.action, d.decision.action !== 'REJECT' && ` · ${d.decision.accepted_kw.toFixed(0)} kW from ${clockOf(d.decision.start_step)}`), /*#__PURE__*/React.createElement("p", null, d.decision.reason)), d.reliability && d.decision.action !== 'REJECT' && /*#__PURE__*/React.createElement("p", {
    className: "hint",
    role: "note",
    style: {
      borderLeft: '3px solid var(--amber)',
      paddingLeft: 12
    }
  }, /*#__PURE__*/React.createElement("b", null, "Treat the promise as an estimate."), " On held-out neighbourhoods only ", d.reliability.promises_kept, " of ", d.reliability.n_requests, " promises were fully kept; delivery averaged ", (100 * d.reliability.mean_ratio).toFixed(0), "% and fell to ", (100 * d.reliability.min_ratio).toFixed(0), "% of the promise. Plan on about ", d.reliability.firm_kw === null ? '—' : d.reliability.firm_kw.toFixed(0), " kW, and even that is the worst of only ", d.reliability.n_requests, " samples, not a bound."), /*#__PURE__*/React.createElement("div", {
    className: "tiles"
  }, /*#__PURE__*/React.createElement(Tile, {
    label: "Requested",
    value: `${d.decision.requested_kw.toFixed(0)} kW`
  }), /*#__PURE__*/React.createElement(Tile, {
    label: "Promised",
    value: `${d.decision.accepted_kw.toFixed(0)} kW`,
    sub: `optimiser probe ${d.decision.probed_capability_kw.toFixed(0)} kW × margin ${d.margin_used.toFixed(2)}`
  }), /*#__PURE__*/React.createElement(Tile, {
    label: "Measured delivery",
    value: d.delivered ? `${d.delivered.mean_kw.toFixed(0)} kW` : '—',
    tone: d.delivered ? d.delivered.delivery_ratio >= 0.9 ? 'good' : 'warn' : undefined,
    sub: d.delivered ? `${(100 * d.delivered.delivery_ratio).toFixed(0)}% of promise` : 'nothing dispatched'
  }), /*#__PURE__*/React.createElement(Tile, {
    label: "Rebound after window",
    value: d.rebound ? `${d.rebound.post_window_peak_change_kw >= 0 ? '+' : ''}${d.rebound.post_window_peak_change_kw.toFixed(0)} kW` : '—',
    sub: "change in the next 2 h peak"
  })), d.net_baseline_kw && d.net_actual_kw && d.window && /*#__PURE__*/React.createElement(LineChart, {
    label: "Net import with and without the request",
    unit: " kW",
    bands: [{
      from: d.window[0],
      to: d.window[1],
      label: 'request window'
    }],
    lines: [{
      name: 'Without request',
      color: '#80988A',
      values: d.net_baseline_kw,
      dash: '4 3'
    }, {
      name: 'With request',
      color: '#1B7A4B',
      values: d.net_actual_kw
    }]
  }), /*#__PURE__*/React.createElement("table", {
    className: "data",
    style: {
      marginTop: 14
    }
  }, /*#__PURE__*/React.createElement("tbody", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Probed sustained capability, by start time"), /*#__PURE__*/React.createElement("td", null, d.capability_by_shift_kw.map((c, i) => c === null ? null : `${clockOf(d.request.start_step + i)}: ${c.toFixed(0)} kW`).filter(Boolean).join(' · '))), /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "EV owners affected"), /*#__PURE__*/React.createElement("td", null, d.actual.ev_unserved_kwh.toFixed(2), " kWh unserved (baseline ", d.baseline.ev_unserved_kwh.toFixed(2), " kWh)")), /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Physics check"), /*#__PURE__*/React.createElement("td", null, d.actual.steps_verified_pct.toFixed(1), "% of steps AC-verified \xB7 peak phase ", d.actual.peak_phase_loading.toFixed(0), "%")))), /*#__PURE__*/React.createElement("p", {
    className: "disclaimer"
  }, d.disclaimer)));
}
function DiscomPage() {
  return /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("p", {
    className: "scope"
  }, "Simulation only. Nothing here commands equipment. NEOS never promises flexibility it cannot show: across held-out neighbourhoods, delivery against promise was uneven (see Evidence), which is why this interface reports ", /*#__PURE__*/React.createElement("b", null, "measured"), " delivery, not a guarantee."), /*#__PURE__*/React.createElement(PhasePanel, null), /*#__PURE__*/React.createElement(RequestPanel, null));
}
mount(/*#__PURE__*/React.createElement(DiscomPage, null));
})();
