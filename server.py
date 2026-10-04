"""
server.py -- HTTP + SSE API over the Neighbourhood Energy OS.

    uvicorn server:app --port 8000

THIS IS A SIMULATION API. No endpoint here can command a physical device, and
every response carries `X-Simulation-Only: true`. Specifically:

  POST /api/override   registers an auditable override EVENT in a ledger. It is
                       'queued' and affects ONLY the next simulation run of the
                       named scope; it becomes 'applied' (with its feasibility
                       outcome) only once a run has consumed it. It never claims
                       a device was commanded.
  GET  /api/overrides  the ledger.

Optional authentication: set NEOS_API_KEY and mutating endpoints require
`X-API-Key`. Telemetry-freshness checks and actuator acknowledgement do not
exist because there are no actuators; they are required before any field trial.

Endpoints
    GET  /api/health                         backend, versions, simulation-only flag
    GET  /api/meta                           network nameplate and probed limits
    GET  /api/scenarios                      scenario catalogue
    GET  /api/forecast/benchmark             chronological hold-out scores + per-lead coverage
    GET  /api/run?scenario=&mode=            one arm: metrics + series
    GET  /api/compare?scenario=              all arms side by side
    GET  /api/stream?scenario=&mode=         SSE, one frame per control step
    GET  /api/twin/powerflow?load_kw=        single AC solve with full constraint report
    GET  /api/evidence                       the archived multi-seed study (results.json)
    POST /api/override | GET /api/overrides  see above
    GET  /                                   operator console
"""
from __future__ import annotations
import asyncio, json, os, queue, threading
from pathlib import Path

from fastapi import FastAPI, Query, Header, HTTPException
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from neos import service
from neos.config import NeighbourhoodConfig, ConfigError
from neos.grid import constraint_report
from neos.runner import headroom, export_limit
from neos.seeds import provenance
from neos.platform import platform_manifest
from neos import hub, rural, discom, offline, phase, islanding

app = FastAPI(title="Neighbourhood Energy OS", version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def simulation_only_header(request, call_next):
    resp = await call_next(request)
    resp.headers["X-Simulation-Only"] = "true"
    return resp


WEB = Path(__file__).parent / "web"
RESULTS = Path(__file__).parent / "results.json"
MODES = ("uncoordinated", "rule_tou", "coordinated")


def cfg_from(n_homes, n_ev, n_pv, n_bess, n_comm, tx_kva, backend="auto"):
    try:
        return NeighbourhoodConfig(n_homes=n_homes, n_ev=n_ev, n_pv=n_pv, n_bess=n_bess, n_comm=n_comm,
                                   tx_kva=tx_kva, backend=backend)
    except ConfigError as e:
        raise HTTPException(422, detail=str(e))


def require_key(x_api_key):
    key = os.environ.get("NEOS_API_KEY")
    if key and x_api_key != key:
        raise HTTPException(401, detail="missing or invalid X-API-Key")


def check(scenario, mode=None):
    if scenario not in service.SCENARIOS:
        raise HTTPException(400, detail=f"unknown scenario '{scenario}'")
    if mode is not None and mode not in MODES:
        raise HTTPException(400, detail=f"mode must be one of {MODES}")


@app.get("/api/health")
def health():
    return dict(status="ok", simulation_only=True, auth_required=bool(os.environ.get("NEOS_API_KEY")),
                provenance=provenance())


@app.get("/api/platform")
def platform():
    return platform_manifest()


@app.get("/api/meta")
def meta(n_homes: int = 300, n_ev: int = 60, n_pv: int = 150, n_bess: int = 26, n_comm: int = 10, tx_kva: float = 630.0,
        backend: str = "auto"):
    cfg = cfg_from(n_homes, n_ev, n_pv, n_bess, n_comm, tx_kva, backend)
    m = service.meta(cfg)
    w = service.get_world(cfg)
    with w['grid'].lock:
        m['import_headroom_kw'] = round(headroom(w['grid'], w['nb']), 1)
        m['export_limit_kw'] = round(export_limit(w['grid'], w['nb']), 1)
    return m


@app.get("/api/scenarios")
def scenarios():
    return [dict(key=k, title=v[0], description=v[1]) for k, v in service.SCENARIOS.items()]


@app.get("/api/forecast/benchmark")
def benchmark(n_homes: int = 300, n_ev: int = 60, n_pv: int = 150, n_bess: int = 26, n_comm: int = 10, tx_kva: float = 630.0):
    cfg = cfg_from(n_homes, n_ev, n_pv, n_bess, n_comm, tx_kva)
    w = service.get_world(cfg)
    if 'bench' not in w:
        w['bench'] = service.forecast_benchmark(cfg)
        w['pvcal'] = service.pv_ensemble_calibration(cfg, reps=2)
    return dict(models=w['bench'], pv_ensemble=w['pvcal'])


@app.get("/api/run")
def run(scenario: str = Query("normal"), mode: str = Query("coordinated"), rep: int = 0,
        n_homes: int = 300, n_ev: int = 60, n_pv: int = 150, n_bess: int = 26, n_comm: int = 10, tx_kva: float = 630.0):
    check(scenario, mode)
    cfg = cfg_from(n_homes, n_ev, n_pv, n_bess, n_comm, tx_kva)
    m, series, fc = service.run(cfg, scenario, mode, rep=rep)
    return dict(metrics=m, series=series, forecast=service.flat_fc(fc))


@app.get("/api/compare")
def compare(scenario: str = "normal", rep: int = 0, n_homes: int = 300, n_ev: int = 60,
            n_pv: int = 150, n_bess: int = 26, n_comm: int = 10, tx_kva: float = 630.0):
    check(scenario)
    cfg = cfg_from(n_homes, n_ev, n_pv, n_bess, n_comm, tx_kva)
    out = {}
    for mode in MODES:
        m, series, fc = service.run(cfg, scenario, mode, rep=rep)
        out[mode] = m
        out[mode + "_series"] = series
        out["forecast"] = service.flat_fc(fc)
    out["title"], out["desc"] = service.SCENARIOS[scenario]
    return out


@app.get("/api/stream")
async def stream(scenario: str = "normal", mode: str = "coordinated", rep: int = 0,
                 n_homes: int = 300, n_ev: int = 60, n_pv: int = 150, n_bess: int = 26,
                 tx_kva: float = 630.0, delay: float = 0.06):
    check(scenario, mode)
    cfg = cfg_from(n_homes, n_ev, n_pv, n_bess, n_comm, tx_kva)
    q: "queue.Queue" = queue.Queue()

    def worker():
        try:
            m, _s, _f = service.run(cfg, scenario, mode, rep=rep, on_step=q.put)
            q.put({"done": True, "metrics": m})
        except Exception as exc:                      # surface, never swallow
            q.put({"error": str(exc)})
        q.put(None)

    threading.Thread(target=worker, daemon=True).start()

    async def gen():
        loop = asyncio.get_running_loop()
        while True:
            item = await loop.run_in_executor(None, q.get)
            if item is None:
                break
            yield f"data: {json.dumps(item, default=float)}\n\n"
            if not item.get("done") and delay:
                await asyncio.sleep(delay)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/flexibility")
def flexibility(scenario: str = "normal", mode: str = "coordinated", rep: int = 0,
                n_homes: int = 300, n_ev: int = 60, n_pv: int = 150, n_bess: int = 26,
                tx_kva: float = 630.0, backend: str = "auto"):
    """The Flexibility Envelope, one record per control step (V3 roadmap
    section 5). Every record's `status` field is the literal string
    "ESTIMATED" -- see neos/flexibility.py for why this is enforced in code,
    not just in this docstring. `latest` is the convenience field a live
    dashboard widget would actually poll."""
    check(scenario, mode)
    cfg = cfg_from(n_homes, n_ev, n_pv, n_bess, n_comm, tx_kva, backend)
    m, _series, _fc = service.run(cfg, scenario, mode, rep=rep, want_envelope=True)
    envs = m.get('envelopes', [])
    return dict(scenario=scenario, mode=mode, envelopes=envs,
               latest=envs[-1] if envs else None)


@app.get("/api/decision_trace")
def decision_trace(scenario: str = "normal", mode: str = "coordinated", rep: int = 0,
                   n_homes: int = 300, n_ev: int = 60, n_pv: int = 150, n_bess: int = 26,
                   tx_kva: float = 630.0, backend: str = "auto", status: str = None):
    """The Decision Trace, one record per control step (V3 roadmap section
    6). `status` (optional) filters to VERIFIED / INFEASIBLE / SOLVER_FAILED,
    exactly as reported -- never collapsed into a generic ok/not-ok flag, in
    this endpoint or anywhere upstream of it. Pass status=INFEASIBLE to see
    only the steps where no electrically-safe match for the controller's
    plan existed."""
    check(scenario, mode)
    cfg = cfg_from(n_homes, n_ev, n_pv, n_bess, n_comm, tx_kva, backend)
    m, _series, _fc = service.run(cfg, scenario, mode, rep=rep, want_trace=True)
    traces = m.get('traces', [])
    if status is not None:
        want = status.upper()
        if want not in ('VERIFIED', 'INFEASIBLE', 'SOLVER_FAILED'):
            raise HTTPException(400, detail="status must be one of VERIFIED, INFEASIBLE, SOLVER_FAILED")
        traces = [t for t in traces if t['safety_status'] == want]
    return dict(scenario=scenario, mode=mode, count=len(traces), traces=traces)


@app.get("/api/twin/powerflow")
def powerflow(load_kw: float = 400.0, n_homes: int = 300, n_ev: int = 60, n_pv: int = 150,
              n_bess: int = 26, n_comm: int = 10, tx_kva: float = 630.0):
    cfg = cfg_from(n_homes, n_ev, n_pv, n_bess, n_comm, tx_kva)
    w = service.get_world(cfg)
    inj = {}
    for h in w['nb'].homes:
        k = (h.node, h.phase)
        inj[k] = inj.get(k, 0.0) + load_kw / len(w['nb'].homes)
    with w['grid'].lock:
        st = w['grid'].solve(inj)
    return dict(state=st, constraints=constraint_report(st))


@app.post("/api/override", status_code=202)
def override(ev_id: int, kind: str = "need_now", scenario: str = "normal", rep: int = 0,
             notice_step: int = 40, lead_steps: int = 0, n_homes: int = 300, n_ev: int = 60,
             n_pv: int = 150, n_bess: int = 26, n_comm: int = 10, tx_kva: float = 630.0,
             x_api_key: str | None = Header(default=None)):
    """Register a user-declared requirement. See module docstring: this is queued
    for the next simulation run of this scope and commands NO device."""
    require_key(x_api_key)
    check(scenario)
    cfg = cfg_from(n_homes, n_ev, n_pv, n_bess, n_comm, tx_kva)
    if not 0 <= ev_id < n_ev:
        raise HTTPException(422, detail=f"ev_id must be in [0, {n_ev})")
    try:
        e = service.LEDGER.submit(scenario, ev_id, kind, lead_steps, notice_step, rep=rep, cfg=cfg)
    except ValueError as ex:
        raise HTTPException(422, detail=str(ex))
    return dict(id=e['id'], status=e['status'], applied=False, simulation_only=True,
                scope=dict(scenario=scenario, rep=rep, cfg=e['cfg']),
                message="Queued for the next simulation run of this scope. No physical device "
                        "was commanded. Feasibility is determined when the run executes; "
                        "see GET /api/overrides.")


@app.get("/api/overrides")
def overrides():
    return service.LEDGER.list()


@app.get("/api/operating-system")
def operating_system():
    """Expose the broader NEOS operating-system layer in one browser-friendly manifest."""
    p = platform_manifest()
    p["modules"] = {
        "telemetry_state_estimation": "implemented_module",
        "resources": "implemented_module",
        "reserve": "implemented_module",
        "flexibility_envelope": "implemented_api",
        "decision_trace": "implemented_api",
        "phase_aware_gateway": "simulation_module",
        "community_energy_hub": "simulation_module",
        "rural_scheduler": "simulation_module",
        "rural_surplus_router": "simulation_module",
        "offline_participation": "implemented_module",
        "verification_ledger": "simulation_module",
        "discom_interface": "simulation_module",
        "islanding_state_machine": "concept_simulation_only",
        "affordability": "designed_module_not_integrated",
        "service_charter": "designed_module_not_integrated",
    }
    return p


@app.get("/api/hub")
def hub_simulation(seed: int = 3, n_homes: int = 150, n_shops: int = 10,
                   batt_kwh: float = 100.0, outage_mean_h: float = 3.2):
    """Low-income/peri-urban community-energy-hub simulation."""
    cfg = hub.HubConfig(n_homes=n_homes, n_shops=n_shops, batt_kwh=batt_kwh,
                        outage_mean_h=outage_mean_h)
    r, _ = hub.run_seed(cfg, seed)
    return {"config": cfg.__dict__, "seed": seed, "arms": r,
            "status": "SIMULATED", "boundary": "community hub simulation; not field validation"}


@app.get("/api/rural")
def rural_simulation(seed: int = 3, regime: str = "normal", outage_start: float = 18.0,
                     outage_end: float = 21.0, pv_kwp: float = 100.0, batt_kwh: float = 20.0,
                     batt_kw: float = 16.0, n_homes: int = 120, tx_kva: float = 100.0):
    """One-day rural scheduler / surplus-router simulation for the current code module."""
    if regime not in service.SCENARIOS:
        raise HTTPException(400, detail=f"unknown regime '{regime}'")
    cfg = rural.RuralConfig(pv_kwp=pv_kwp, batt_kwh=batt_kwh, batt_kw=batt_kw,
                            n_homes=n_homes, tx_kva=tx_kva)
    outage = (outage_start, outage_end) if outage_end > outage_start else None
    rows = {}
    for arm in ("naive", "neos"):
        m = rural.simulate_day(cfg, arm=arm, seed=seed, regime=regime, outage=outage)
        rows[arm] = m
    return {"config": cfg.__dict__, "seed": seed, "regime": regime, "outage": outage,
            "arms": rows, "status": "SIMULATED",
            "boundary": "rural research simulation; benchmark validation pending"}


@app.get("/api/discom")
def discom_simulation(seed: int = 3, reserve_frac: float = 0.50,
                      link_start: float = 15.0, link_end: float = 19.0):
    cfg = hub.HubConfig()
    out = discom.simulate(cfg=cfg, seed=seed, reserve_frac=reserve_frac,
                          link_down=(link_start, link_end))
    return out


@app.get("/api/islanding")
def islanding_simulation(outage_start: int = 72, outage_steps: int = 12,
                         soc_start: float = 0.75):
    ctl = islanding.IslandController()
    rows = []
    soc = float(soc_start)
    for t in range(96):
        grid_ok = not (outage_start <= t < outage_start + outage_steps)
        if not grid_ok:
            soc = max(0.0, soc - 0.75 / 96.0)
        r = ctl.step(t, grid_ok, soc)
        rows.append({"step": t, "hour": round(t * 0.25, 2), "grid_ok": grid_ok,
                     "soc": round(soc, 4), **r})
    return {"steps": rows, "final_state": ctl.state,
            "transition_log": ctl.log, "status": "SIMULATED_CONCEPT",
            "boundary": islanding.CONCEPT_LABEL}


@app.get("/api/phase")
def phase_gateway(load_kw: float = 400.0, n_homes: int = 300, n_ev: int = 60,
                  n_pv: int = 150, n_bess: int = 26, n_comm: int = 10,
                  tx_kva: float = 630.0):
    cfg = cfg_from(n_homes, n_ev, n_pv, n_bess, n_comm, tx_kva)
    w = service.get_world(cfg)
    inj = {}
    for h in w['nb'].homes:
        k = (h.node, h.phase)
        inj[k] = inj.get(k, 0.0) + load_kw / len(w['nb'].homes)
    with w['grid'].lock:
        st = w['grid'].solve(inj)
    gateway = phase.PhaseGateway(tx_kva=tx_kva)
    panel = gateway.update(st)
    rec = gateway.recommend(panel, {}) if panel else None
    return {"panel": panel, "recommendation": rec, "formula": phase.FORMULA,
            "not_claimed": phase.NOT_CLAIMED, "status": "SIMULATED"}


@app.get("/api/offline")
def offline_demo():
    schedule = [
        {"start_h": 6.0, "end_h": 7.0, "what": "water pumping", "tier": 2},
        {"start_h": 18.0, "end_h": 20.0, "what": "flexible EV charging", "tier": 4},
        {"start_h": 20.0, "end_h": 22.0, "what": "cold storage", "tier": 3},
    ]
    return {"schedule": schedule,
            "noticeboard": offline.noticeboard(schedule, community="NEOS neighbourhood"),
            "sms": [offline.sms(x) for x in schedule],
            "shortage": [offline.shortage_notice(i, 0.65 - i * 0.12, 5.0 - i) for i in range(3)],
            "status": "SIMULATED"}


@app.get("/api/evidence")
def evidence():
    if not RESULTS.exists():
        raise HTTPException(404, detail="results.json not found: run `python experiments.py`")
    return JSONResponse(json.loads(RESULTS.read_text()))


if WEB.exists():
    app.mount("/static", StaticFiles(directory=WEB), name="static")

    @app.get("/")
    def index():
        return FileResponse(WEB / "index.html")
