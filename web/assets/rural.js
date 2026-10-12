/* generated from web/src/rural.tsx by build_tsx.js -- do not edit */
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
function bandsOf(mode) {
  const out = [];
  let a = -1;
  mode.forEach((m, i) => {
    if (m > 0.5 && a < 0) a = i;
    if ((m <= 0.5 || i === mode.length - 1) && a >= 0) {
      out.push({
        from: a,
        to: m > 0.5 ? i : i - 1,
        label: out.length ? undefined : 'grid outage (island)',
        color: '#FBF1E3'
      });
      a = -1;
    }
  });
  return out;
}
function ArmCard({
  a,
  title,
  cls
}) {
  const m = a.metrics;
  const ok = m.clinic_unserved_kwh < 0.05;
  return /*#__PURE__*/React.createElement("div", {
    className: "panel panel-pad"
  }, /*#__PURE__*/React.createElement("div", {
    className: "armhead"
  }, /*#__PURE__*/React.createElement("span", {
    className: `chip-arm ${cls}`
  }, cls === 'neos' ? 'NEOS' : 'TIER-BLIND'), title), /*#__PURE__*/React.createElement("div", {
    className: "tiles",
    style: {
      gridTemplateColumns: 'repeat(2,1fr)'
    }
  }, /*#__PURE__*/React.createElement(Tile, {
    label: "Clinic at full power",
    value: m.outage_hours ? `${m.clinic_hours_full_power_in_outage.toFixed(1)} of ${m.outage_hours.toFixed(0)} h` : 'no outage',
    tone: ok ? 'good' : 'bad',
    sub: m.clinic_first_failure_h_after_outage !== null ? `first failure ${m.clinic_first_failure_h_after_outage.toFixed(1)} h in` : undefined
  }), /*#__PURE__*/React.createElement(Tile, {
    label: "Clinic energy unserved",
    value: `${m.clinic_unserved_kwh.toFixed(1)} kWh`,
    tone: ok ? 'good' : 'bad'
  }), /*#__PURE__*/React.createElement(Tile, {
    label: "Fridge + lights unserved",
    value: `${m.tier2_unserved_kwh.toFixed(1)} kWh`,
    tone: m.tier2_unserved_kwh < 0.05 ? 'good' : 'bad'
  }), /*#__PURE__*/React.createElement(Tile, {
    label: "Shops unserved",
    value: `${m.shops_unserved_kwh.toFixed(1)} kWh`,
    tone: m.shops_unserved_kwh > 0.5 ? 'warn' : 'good',
    sub: "tier 3: shed before tiers 1\u20132"
  }), /*#__PURE__*/React.createElement(Tile, {
    label: "Cold chain above 8 \xB0C",
    value: `${m.cold_chain_hours_above_8C.toFixed(1)} h`,
    tone: m.cold_chain_hours_above_8C > 0 ? 'bad' : 'good'
  }), /*#__PURE__*/React.createElement(Tile, {
    label: "Water outage",
    value: `${m.water_outage_hours.toFixed(1)} h`,
    tone: m.water_outage_hours > 0 ? 'bad' : 'good'
  })), /*#__PURE__*/React.createElement("p", {
    className: "hint",
    style: {
      margin: 0
    }
  }, "Battery lowest ", (100 * m.min_soc).toFixed(0), "% \xB7 solar used ", m.pv_utilisation_pct.toFixed(0), "% \xB7 island steps AC-verified ", m.island_steps_verified_pct === null ? 'n/a' : m.island_steps_verified_pct.toFixed(0) + '%', " \xB7 battery energy audit ", m.energy_audit.battery_residual_kwh.toExponential(0), " kWh, ", m.energy_audit.clip_events, " clips \xB7 ", m.backend));
}
function RuralPage() {
  const [list, setList] = useState([]);
  const [sc, setSc] = useState('cloudy_outage');
  const [seed, setSeed] = useState(0);
  const [hrs, setHrs] = useState(12);
  const [go, setGo] = useState(0);
  const [vil, setVil] = useState({
    homes: 90,
    pv_kwp: 45,
    battery_kwh: 120,
    battery_kw: 40
  });
  useEffect(() => {
    fetch('/api/rural/scenarios').then(r => r.json()).then(setList).catch(() => setList([]));
  }, []);
  const url = `/api/rural/compare?scenario=${sc}&seed=${seed}&t1_endurance_h=${hrs}&homes=${vil.homes}&pv_kwp=${vil.pv_kwp}&battery_kwh=${vil.battery_kwh}&battery_kw=${vil.battery_kw}&_=${go}`;
  const {
    data,
    loading,
    error
  } = useApi(url);
  const d = data;
  const lines = (key, scale = 1) => d ? [{
    name: 'NEOS',
    color: '#1B7A4B',
    values: d.neos.series[key].map(v => v * scale)
  }, {
    name: 'Tier-blind',
    color: '#B07A2A',
    values: d.naive.series[key].map(v => v * scale),
    dash: '5 3'
  }] : [];
  return /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("p", {
    className: "scope"
  }, "Synthetic village (default 90 homes, 45 kWp solar, 120 kWh battery; editable below) on a 1.2 km weak feeder (1.5\xD7 impedance, a scenario-design choice) and a community section with a clinic, fridge, lights, three shops, grain mill, cold store, water pump, solar and a battery. In an outage the community section is islanded on a grid-forming inverter and checked by the same AC solver. Island dynamics, black start, protection and reconnection are ", /*#__PURE__*/React.createElement("b", null, "not"), " modelled. Homes are off in both arms."), /*#__PURE__*/React.createElement("div", {
    className: "controls"
  }, /*#__PURE__*/React.createElement("label", {
    htmlFor: "rs"
  }, "Scenario"), /*#__PURE__*/React.createElement("select", {
    id: "rs",
    value: sc,
    onChange: e => setSc(e.target.value)
  }, list.map(s => /*#__PURE__*/React.createElement("option", {
    key: s.key,
    value: s.key
  }, s.title))), /*#__PURE__*/React.createElement("label", {
    htmlFor: "rd"
  }, "Village seed"), /*#__PURE__*/React.createElement("input", {
    id: "rd",
    type: "number",
    min: 0,
    max: 99,
    value: seed,
    onChange: e => setSeed(+e.target.value),
    style: {
      width: 70,
      padding: 8,
      borderRadius: 8,
      border: '1px solid var(--line-strong)'
    }
  }), /*#__PURE__*/React.createElement("label", {
    htmlFor: "rh"
  }, "Tier-1/2 endurance (h)"), /*#__PURE__*/React.createElement("input", {
    id: "rh",
    type: "range",
    min: 2,
    max: 24,
    value: hrs,
    onChange: e => setHrs(+e.target.value)
  }), /*#__PURE__*/React.createElement("b", {
    style: {
      fontFamily: 'var(--font-mono)'
    }
  }, hrs, " h"), /*#__PURE__*/React.createElement("button", {
    className: "btn ghost",
    onClick: () => setGo(go + 1)
  }, "Re-run")), /*#__PURE__*/React.createElement("div", {
    className: "controls"
  }, [['homes', 'Homes', 10, 300, 1], ['pv_kwp', 'Solar kWp', 5, 150, 1], ['battery_kwh', 'Battery kWh', 20, 400, 5], ['battery_kw', 'Battery kW', 5, 100, 1]].map(([k, lab, lo, hi, st]) => /*#__PURE__*/React.createElement("span", {
    key: k,
    style: {
      display: 'inline-flex',
      alignItems: 'center',
      gap: 6
    }
  }, /*#__PURE__*/React.createElement("label", {
    htmlFor: 'v_' + k
  }, lab), /*#__PURE__*/React.createElement("input", {
    id: 'v_' + k,
    type: "number",
    min: lo,
    max: hi,
    step: st,
    value: vil[k],
    onChange: e => {
      const n = +e.target.value;
      setVil({
        ...vil,
        [k]: Math.min(hi, Math.max(lo, isNaN(n) ? lo : n))
      });
    },
    style: {
      width: 80,
      padding: 8,
      borderRadius: 8,
      border: '1px solid var(--line-strong)'
    }
  }))), /*#__PURE__*/React.createElement("button", {
    className: "btn ghost",
    onClick: () => setVil({
      homes: 90,
      pv_kwp: 45,
      battery_kwh: 120,
      battery_kw: 40
    })
  }, "Reset to default")), d && !d.is_default_village && /*#__PURE__*/React.createElement("p", {
    className: "hint",
    style: {
      marginTop: -10
    }
  }, /*#__PURE__*/React.createElement("b", null, "Custom village."), " Inverter = 1.25 \xD7 battery kW and the transformer scales with homes (100 kVA at 90 homes). The archived held-out study on the Evidence page covers the default village only; this run is a single seed."), /*#__PURE__*/React.createElement("p", {
    className: "hint",
    style: {
      marginTop: -10
    }
  }, "The endurance is a charter parameter: how long the battery must be able to carry the clinic, fridge and lights. A longer charter protects them longer and sheds shops sooner."), /*#__PURE__*/React.createElement(Status, {
    loading: loading,
    error: error
  }), d && !loading && /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "two"
  }, /*#__PURE__*/React.createElement(ArmCard, {
    a: d.neos,
    cls: "neos",
    title: "Priority tiers, confidence-aware reserve"
  }), /*#__PURE__*/React.createElement(ArmCard, {
    a: d.naive,
    cls: "naive",
    title: "Everyone shares equally"
  })), /*#__PURE__*/React.createElement("div", {
    className: "panel panel-pad",
    style: {
      marginBottom: 16
    }
  }, /*#__PURE__*/React.createElement("h3", {
    className: "sec"
  }, "Battery state of charge"), /*#__PURE__*/React.createElement(LineChart, {
    label: "Battery state of charge",
    unit: "%",
    yMin: 0,
    yMax: 100,
    bands: bandsOf(d.neos.series.mode),
    lines: lines('soc', 100)
  })), /*#__PURE__*/React.createElement("div", {
    className: "panel panel-pad",
    style: {
      marginBottom: 16
    }
  }, /*#__PURE__*/React.createElement("h3", {
    className: "sec"
  }, "Clinic supply (1 = full power)"), /*#__PURE__*/React.createElement(LineChart, {
    label: "Clinic supply fraction",
    yMin: 0,
    yMax: 1.1,
    bands: bandsOf(d.neos.series.mode),
    lines: [{
      name: 'NEOS',
      color: '#1B7A4B',
      values: d.neos.series.clinic_frac
    }, {
      name: 'Tier-blind',
      color: '#B07A2A',
      values: d.naive.series.clinic_frac,
      dash: '5 3'
    }]
  })), /*#__PURE__*/React.createElement("div", {
    className: "two"
  }, /*#__PURE__*/React.createElement("div", {
    className: "panel panel-pad"
  }, /*#__PURE__*/React.createElement("h3", {
    className: "sec"
  }, "Cold store temperature (limit 8 \xB0C)"), /*#__PURE__*/React.createElement(LineChart, {
    label: "Cold store temperature",
    unit: " \xB0C",
    yMin: 0,
    yMax: 14,
    bands: bandsOf(d.neos.series.mode),
    lines: lines('cold_c')
  })), /*#__PURE__*/React.createElement("div", {
    className: "panel panel-pad"
  }, /*#__PURE__*/React.createElement("h3", {
    className: "sec"
  }, "Water tank (% full)"), /*#__PURE__*/React.createElement(LineChart, {
    label: "Water tank level",
    unit: "%",
    yMin: 0,
    yMax: 100,
    bands: bandsOf(d.neos.series.mode),
    lines: lines('tank', 100 / 40)
  }))), /*#__PURE__*/React.createElement("table", {
    className: "data"
  }, /*#__PURE__*/React.createElement("tbody", null, Object.entries(d.tiers).map(([t, n]) => /*#__PURE__*/React.createElement("tr", {
    key: t
  }, /*#__PURE__*/React.createElement("th", null, "Tier ", t), /*#__PURE__*/React.createElement("td", null, n))))), /*#__PURE__*/React.createElement("p", {
    className: "disclaimer"
  }, "Protecting tiers 1\u20132 on a poor-sun day means shedding tier 3 earlier; the shops column shows that cost, it is not hidden. All loads, weather and tariffs are synthetic.")));
}
mount(/*#__PURE__*/React.createElement(RuralPage, null));
})();
