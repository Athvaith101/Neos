"""build_web.py -- generates web/index.html (API-driven) from dash_template.html."""
from pathlib import Path

root = Path(__file__).parent
tpl = (root / "dash_template.html").read_text()

LIVE_PANEL = """
  <section class="panel" id="livePanel">
    <div class="ptitle"><h2>Live control loop</h2><span id="liveClock">idle</span></div>
    <p class="note">96 control steps, one every 15 simulated minutes. Each step is a fresh
    forecast window, a fresh linear programme, and a fresh unbalanced AC power flow before
    anything is allowed to reach a device.</p>
    <div class="switchbar">
      <button class="chip" id="runCo" type="button">Run coordinated</button>
      <button class="chip" id="runUn" type="button">Run without coordination</button>
      <button class="chip" id="runStop" type="button" disabled>Stop</button>
      <span class="legend"><span id="liveMode" style="color:var(--ink-3)"></span></span>
    </div>
    <div id="liveReadout" class="readout"></div>
    <div id="liveChart"></div>
  </section>

  <section class="panel">
    <div class="ptitle"><h2>Change the neighbourhood</h2><span>re-runs the whole study</span></div>
    <p class="note">The interesting question is not whether this works on one feeder. It is where the
    benefit disappears. Move the sliders and every number on this page is recomputed from the physics.</p>
    <div class="knobs" id="knobs"></div>
    <div class="switchbar"><button class="chip" id="applyCfg" type="button">Apply and re-run</button>
      <span id="cfgState" style="color:var(--ink-3);font-size:13px"></span></div>
  </section>
"""

EXTRA_CSS = """
.readout{display:grid;grid-template-columns:repeat(2,1fr);gap:1px;background:var(--rule);
  border:1px solid var(--rule);margin-bottom:14px}
@media(min-width:700px){.readout{grid-template-columns:repeat(4,1fr)}}
.readout div{background:var(--panel);padding:9px 11px}
.readout span{display:block;color:var(--ink-3);font-size:11px}
.readout b{font-size:19px;font-weight:600;letter-spacing:-.01em}
.knobs{display:grid;gap:14px;grid-template-columns:1fr}
@media(min-width:700px){.knobs{grid-template-columns:repeat(2,1fr)}}
.knob label{display:flex;justify-content:space-between;font-size:13px;color:var(--ink-2);margin-bottom:4px}
.knob label b{color:var(--ink);font-weight:600}
.knob input[type=range]{width:100%;accent-color:var(--ok)}
.chip[disabled]{opacity:.45;cursor:default}
.loading{color:var(--ink-3);font-size:13px}
"""

BOOT = r"""
const API = "";
let P = {n_homes:300, n_ev:60, n_pv:150, n_bess:26, tx_kva:630};
const D = {meta:null, forecast_benchmark:null, pv_calibration:null, scenarios:{}, study:null};
const qs = () => new URLSearchParams(P).toString();
async function j(u){const r=await fetch(API+u); if(!r.ok) throw new Error(await r.text()); return r.json();}

const SCENARIO_ICON = {normal:"\u2600\uFE0F",cloud:"\u2601\uFE0F",heat:"\uD83D\uDD25",
  evsurge:"\uD83D\uDE97",shortage:"\u26A1",battfail:"\uD83D\uDD0B",spike:"\uD83D\uDCC8"};

/* ---------- bootstrap ---------- */
async function boot(){
  D.meta = await j("/api/meta?"+qs());
  renderPlate();
  const cat = await j("/api/scenarios");
  cat.forEach(s => D.scenarios[s.key] = {title:(SCENARIO_ICON[s.key]||"")+" "+s.title, desc:s.description});
  renderRail(); renderKnobs();
  const b = await j("/api/forecast/benchmark?"+qs());
  D.forecast_benchmark = b.models; D.pv_calibration = b.pv_ensemble;
  renderForecastTable(); drawPipe();
  try{ const E = await j("/api/evidence"); Object.assign(D,{study:E.study,held_out:E.held_out,calibration:E.calibration,override_study:E.override_study,limitations:E.limitations,provenance:E.provenance}); D.meta.replicates=E.meta.replicates; }catch(e){}
  D.meta.grid_backend = D.meta.grid_backend||"reference"; renderEvidence();
  await select(Object.keys(D.scenarios)[0], true);
}

async function ensure(k){
  const sc = D.scenarios[k];
  if(sc.coordinated) return sc;
  Object.assign(sc, await j("/api/compare?scenario="+k+"&"+qs()));
  sc.title = (SCENARIO_ICON[k]||"")+" "+sc.title;
  return sc;
}

function renderRail(){
  const r = $("#rail"); r.innerHTML="";
  for(const k in D.scenarios){
    const b=document.createElement("button");
    b.className="chip"; b.type="button"; b.textContent=D.scenarios[k].title;
    b.setAttribute("aria-pressed", k===current);
    b.onclick=()=>select(k);
    r.appendChild(b);
  }
}

async function select(k, animate=true){
  current=k;
  [...$("#rail").children].forEach((b,i)=>
    b.setAttribute("aria-pressed", Object.keys(D.scenarios)[i]===k));
  $("#scTitle").textContent="Loading…"; $("#scDesc").textContent="";
  const sc = await ensure(k);
  $("#scTitle").textContent=sc.title.replace(/^\S+\s/,"");
  $("#scDesc").textContent=sc.desc;
  drawHero(sc,animate); drawFlex(sc); drawVolt(sc); drawForecast(sc);
  renderMetrics(sc); renderVerdict(sc);
}

/* ---------- live stream ---------- */
let es=null, live=[];
function liveStop(){ if(es){es.close(); es=null;} $("#runStop").disabled=true;
  $("#runCo").disabled=false; $("#runUn").disabled=false; }

function liveStart(mode){
  liveStop(); live=[]; $("#liveMode").textContent = mode==="coordinated"
    ? "optimiser active, safety layer armed" : "no coordination, autonomous inverter protection only";
  $("#runStop").disabled=false; $("#runCo").disabled=true; $("#runUn").disabled=true;
  es = new EventSource(API+"/api/stream?scenario="+current+"&mode="+mode+"&"+qs());
  es.onmessage = ev => {
    const d = JSON.parse(ev.data);
    if(d.error){ $("#liveClock").textContent = "failed: "+d.error; liveStop(); return; }
    if(d.done){ $("#liveClock").textContent = "run complete — peak "+d.metrics.peak_tx_loading.toFixed(1)+" %";
      liveStop(); return; }
    live.push(d); drawLive(d);
  };
  es.onerror = () => { $("#liveClock").textContent="connection lost"; liveStop(); };
}

function drawLive(d){
  const hh=String(Math.floor(d.hour)).padStart(2,"0"), mm=String(Math.round((d.hour%1)*60)).padStart(2,"0");
  $("#liveClock").textContent = hh+":"+mm+"  ·  step "+(d.step+1)+" of 96";
  const state = d.tx_loading>100 ? ["var(--bad)","over rating"]
    : d.tx_loading>90 ? ["var(--warn)","approaching rating"] : ["var(--ok)","within limits"];
  $("#liveReadout").innerHTML =
    `<div><span>Transformer</span><b style="color:${state[0]}">${d.tx_loading.toFixed(1)} %</b></div>`+
    `<div><span>Worst node voltage</span><b style="color:${d.vmin<0.94?"var(--bad)":"var(--ink)"}">${d.vmin.toFixed(3)} pu</b></div>`+
    `<div><span>EV charging</span><b>${d.ev_kw.toFixed(0)} kW</b></div>`+
    `<div><span>Solar</span><b style="color:var(--solar)">${d.pv_kw.toFixed(0)} kW</b></div>`+
    `<div><span>Storage</span><b style="color:var(--store)">${d.batt_kw.toFixed(0)} kW</b></div>`+
    `<div><span>Deferrable load</span><b>${d.defer_kw.toFixed(0)} kW</b></div>`+
    `<div><span>Tariff now</span><b>\u20B9${d.tariff.toFixed(2)}</b></div>`+
    `<div><span>Safety overrides</span><b style="color:${d.interventions?"var(--warn)":"var(--ink)"}">${d.interventions}</b></div>`;
  const arr = live.map(x=>x.tx_loading);
  const hi = Math.max(130, Math.ceil(Math.max(...arr)/20)*20+10);
  const F = frame($("#liveChart"),{h:190});
  yAxis(F,0,hi,[0,50,100].filter(v=>v<=hi),"%"); xAxis(F,96,{every:12});
  const y100=sy(F,100,0,hi);
  F.s.appendChild(svgEl("line",{x1:F.ml,y1:y100,x2:F.w-F.mr,y2:y100,stroke:"var(--bad)",
    "stroke-width":1.3,"stroke-dasharray":"5 4","stroke-opacity":.8}));
  const n=96, pts=arr.map((v,i)=>`${i?"L":"M"}${sx(F,i,n).toFixed(1)},${sy(F,v,0,hi).toFixed(1)}`).join("");
  F.s.appendChild(svgEl("path",{d:pts,fill:"none",stroke:state[0],"stroke-width":2.2}));
  const cx=sx(F,arr.length-1,n), cy=sy(F,arr[arr.length-1],0,hi);
  F.s.appendChild(svgEl("circle",{cx,cy,r:3.5,fill:state[0]}));
  label(F,F.ml,F.h-2,"transformer loading, live","var(--ink-3)","start",10.5);
}

/* ---------- what-if knobs ---------- */
const KNOBS=[["n_ev","Electric vehicles",0,180,10],["n_pv","Rooftop solar installations",0,300,10],
  ["n_bess","Home batteries",0,100,2],["tx_kva","Transformer rating (kVA)",315,1000,5]];
function renderKnobs(){
  $("#knobs").innerHTML = KNOBS.map(([k,lab,mn,mx,st])=>
    `<div class="knob"><label for="k_${k}">${lab}<b id="v_${k}">${P[k]}</b></label>
     <input id="k_${k}" type="range" min="${mn}" max="${mx}" step="${st}" value="${P[k]}"></div>`).join("");
  KNOBS.forEach(([k])=>{
    const el=$("#k_"+k);
    el.oninput=()=>{ $("#v_"+k).textContent=el.value; };
  });
  $("#applyCfg").onclick=async()=>{
    liveStop();
    KNOBS.forEach(([k])=>P[k]=Number($("#k_"+k).value));
    $("#cfgState").textContent="rebuilding the feeder and refitting the forecaster…";
    $("#applyCfg").disabled=true;
    try{
      for(const k in D.scenarios){ const t=D.scenarios[k].title, d=D.scenarios[k].desc;
        D.scenarios[k]={title:t,desc:d}; }
      D.meta = await j("/api/meta?"+qs()); renderPlate();
      const b = await j("/api/forecast/benchmark?"+qs());
      D.forecast_benchmark=b.models; D.pv_calibration=b.pv_ensemble; renderForecastTable();
      await select(current,true);
      $("#cfgState").textContent="done";
    }catch(e){ $("#cfgState").textContent="failed: "+e.message; }
    $("#applyCfg").disabled=false;
  };
}

$("#runCo").onclick=()=>liveStart("coordinated");
$("#runUn").onclick=()=>liveStart("uncoordinated");
$("#runStop").onclick=liveStop;

function renderPlate(){
  const m=D.meta;
  $("#plate").innerHTML=
    `<div><span>Homes</span><b>${m.homes}</b></div>`+
    `<div><span>Rooftop solar</span><b>${fmt(m.pv_kwp,0)} kWp</b></div>`+
    `<div><span>Electric vehicles</span><b>${m.evs}</b></div>`+
    `<div><span>Home batteries</span><b>${m.batteries}</b></div>`+
    `<div><span>Commercial sites</span><b>${m.commercial}</b></div>`+
    `<div><span>Transformer</span><b>${fmt(m.tx_kva,0)} kVA</b></div>`;
  $("#foot").innerHTML=`Nothing on this page is a stored screenshot. Every trace is computed on request by `+
    `the backend: a linear programme per control step, each one verified by an unbalanced three-phase `+
    `load flow (${m.grid_backend}) across ${m.lv_loads} metered LV connection points. Import headroom `+
    `${fmt(m.import_headroom_kw,0)} kW, reverse-flow limit ${fmt(m.export_limit_kw,0)} kW, both probed `+
    `from the twin itself. The uncoordinated arm still curtails autonomously on local overvoltage, as a `+
    `compliant installation does today.`;
}

let rt; addEventListener("resize",()=>{clearTimeout(rt);rt=setTimeout(()=>select(current,false),200)});
boot().catch(e=>{ document.querySelector(".wrap").insertAdjacentHTML("afterbegin",
  `<p class="loading">Backend not reachable (${e.message}). Start it with <code>uvicorn server:app --port 8000</code>, or open static-demo.html.</p>`); });
"""

html = tpl
html = html.replace('<div class="rail" id="rail" role="group" aria-label="Scenario"></div>',
                    '<div class="rail" id="rail" role="group" aria-label="Scenario"></div>' + LIVE_PANEL)
html = html.replace('@media (prefers-reduced-motion:reduce)', EXTRA_CSS + '\n@media (prefers-reduced-motion:reduce)')

# swap the static bootstrap for the API-driven one
start = html.index('const D = __DATA__;')
end = html.index('renderPlate(); renderRail(); renderForecastTable(); renderEvidence(); drawPipe(); select("normal",true);')
tail_end = html.index('</script>', end)
core = html[start:end]
# keep the chart kit + renderers, drop the old static loaders we override
for fn in ['function renderPlate(){', 'function renderRail(){', 'function select(k, animate=true){']:
    i = core.index(fn)
    depth, jx = 0, i
    while True:
        if core[jx] == '{': depth += 1
        elif core[jx] == '}':
            depth -= 1
            if depth == 0: break
        jx += 1
    core = core[:i] + core[jx + 1:]
core = core.replace('const D = __DATA__;', '')

html = html[:start] + core + BOOT + html[tail_end:]
html = html.replace('<title>Neighbourhood Energy OS — feeder coordination study</title>',
                    '<title>Neighbourhood Energy OS — operator console</title>')

(root / "web").mkdir(exist_ok=True)
(root / "web" / "index.html").write_text(html)
print("web/index.html", len(html) // 1024, "KB")
