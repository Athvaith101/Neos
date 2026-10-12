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
import asyncio, json, os, pathlib, queue, threading
from functools import lru_cache
_DISCOM_LOCK = threading.Lock()
from pathlib import Path

from fastapi import FastAPI, Query, Header, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from neos import service, rural, discom, hub as hubmod
from neos.phase import phase_view
from neos.config import NeighbourhoodConfig, ConfigError
from neos.grid import constraint_report
from neos.runner import headroom, export_limit
from neos.seeds import provenance

app = FastAPI(title="Neighbourhood Energy OS", version="1.0-research")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def simulation_only_header(request, call_next):
    resp = await call_next(request)
    resp.headers["X-Simulation-Only"] = "true"
    return resp


WEB = Path(__file__).parent / "web"
RESULTS = Path(__file__).parent / "results.json"
PLATFORM = Path(__file__).parent / "results_platform.json"
MODES = ("no_control", "uncoordinated", "rule_tou", "coordinated")


def cfg_from(n_homes, n_ev, n_pv, n_bess, tx_kva, backend="auto"):
    try:
        return NeighbourhoodConfig(n_homes=n_homes, n_ev=n_ev, n_pv=n_pv, n_bess=n_bess,
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


@lru_cache(maxsize=8)
def _cached_page_run(scenario, mode, rep, n_homes, n_ev, n_pv, n_bess, tx_kva, backend):
    """Share deterministic simulation output across the read-only analysis pages."""
    cfg = cfg_from(n_homes, n_ev, n_pv, n_bess, tx_kva, backend)
    return service.run(cfg, scenario, mode, rep=rep, use_ledger=False,
                       want_envelope=True, want_trace=True)


def _page_run(scenario, mode, rep, n_homes, n_ev, n_pv, n_bess, tx_kva, backend):
    cfg = cfg_from(n_homes, n_ev, n_pv, n_bess, tx_kva, backend)
    # A queued user override must be applied by a fresh run, never hidden by a cache hit.
    if service.LEDGER.pending(scenario, rep, cfg):
        _cached_page_run.cache_clear()
        return service.run(cfg, scenario, mode, rep=rep, want_envelope=True, want_trace=True)
    return _cached_page_run(scenario, mode, rep, n_homes, n_ev, n_pv, n_bess, tx_kva, backend)

@lru_cache(maxsize=8)
def _cached_discom(scenario, start_step, n_steps, kw, notice_steps, rep,
                   n_homes, n_ev, n_pv, n_bess, tx_kva, backend, margin):
    """Cache identical DISCOM studies; each study contains two complete simulations."""
    cfg = cfg_from(n_homes, n_ev, n_pv, n_bess, tx_kva, backend)

    def run_fn(cut=None, probe=None):
        m, series, _ = service.run(cfg, scenario, "coordinated", rep=rep, want_envelope=True,
                                   net_cut=cut, probe=probe, use_ledger=False)
        return m, series

    with _DISCOM_LOCK:
        previous_margin = discom.SAFETY_MARGIN
        discom.SAFETY_MARGIN = margin
        try:
            return discom.emulate(run_fn, discom.DiscomRequest(start_step, n_steps, kw, notice_steps))
        finally:
            discom.SAFETY_MARGIN = previous_margin


@app.get("/api/health")
def health():
    return dict(status="ok", simulation_only=True, auth_required=bool(os.environ.get("NEOS_API_KEY")),
                provenance=provenance())


@app.get("/api/meta")
def meta(n_homes: int = 300, n_ev: int = 60, n_pv: int = 150, n_bess: int = 26, tx_kva: float = 630.0,
        backend: str = "auto"):
    cfg = cfg_from(n_homes, n_ev, n_pv, n_bess, tx_kva, backend)
    m = service.meta(cfg)
    ts = pathlib.Path(__file__).parent / 'test_status.json'
    m['test_status'] = json.loads(ts.read_text()) if ts.exists() else None
    w = service.get_world(cfg)
    with w['grid'].lock:
        m['import_headroom_kw'] = round(headroom(w['grid'], w['nb']), 1)
        m['export_limit_kw'] = round(export_limit(w['grid'], w['nb']), 1)
    return m


@app.get("/api/scenarios")
def scenarios():
    return [dict(key=k, title=v[0], description=v[1]) for k, v in service.SCENARIOS.items()]


@app.get("/api/phase")
def phase(scenario: str = "normal", mode: str = "coordinated", rep: int = 0,
          n_homes: int = 300, n_ev: int = 60, n_pv: int = 150, n_bess: int = 26,
          tx_kva: float = 630.0, backend: str = "auto"):
    """Phase-aware transformer view (spec section 11): per-phase loading for every step.
    Phase totals only -- aggregate measurements cannot attribute load to a device."""
    check(scenario, mode)
    cfg = cfg_from(n_homes, n_ev, n_pv, n_bess, tx_kva, backend)
    m, s, _ = _page_run(scenario, mode, rep, n_homes, n_ev, n_pv, n_bess, tx_kva, backend)
    return dict(scenario=scenario, mode=mode, tx_loading=s["tx_loading"],
                phases=dict(A=s["phase_a"], B=s["phase_b"], C=s["phase_c"]),
                imbalance=s["imbalance"], peak_phase_loading=m["peak_phase_loading"],
                hours_phase_over_100=m["hours_phase_over_100"], imbalance_p95=m["imbalance_p95"],
                note="Phase loading = LV-side kVA per phase / (rating/3). No device attribution.")


@app.get("/api/discom")
def discom_request(scenario: str = "normal", start_step: int = Query(44, ge=0, le=95),
                   n_steps: int = Query(6, ge=1, le=24), kw: float = Query(30.0, gt=0, le=2000),
                   notice_steps: int = Query(2, ge=1, le=16), rep: int = 0,
                   n_homes: int = 300, n_ev: int = 60, n_pv: int = 150, n_bess: int = 26,
                   tx_kva: float = 630.0, backend: str = "auto"):
    """DISCOM flexibility interface (spec section 20). NEOS answers ACCEPT / MODIFY / DEFER / REJECT
    from an optimiser probe, then the response is MEASURED against an unrequested baseline run.
    Simulation only; no settlement is modelled. Takes tens of seconds on OpenDSS."""
    check(scenario)
    margin = 1.0
    if PLATFORM.exists():
        margin = json.loads(PLATFORM.read_text())["discom"]["margin"]
    out = dict(_cached_discom(scenario, start_step, n_steps, kw, notice_steps, rep,
                              n_homes, n_ev, n_pv, n_bess, tx_kva, backend, margin))
    out["margin_used"] = margin
    rel = None
    if PLATFORM.exists():
        S = json.loads(PLATFORM.read_text())["discom"]["summary"]
        rel = dict(promises_kept=S["promise_kept"], n_requests=S["n_requests"],
                   mean_ratio=S["mean_ratio_vs_promise"], min_ratio=S["min_ratio_vs_promise"],
                   firm_kw=(out["decision"]["accepted_kw"] * S["min_ratio_vs_promise"]) if out.get("decision") else None,
                   note="Held-out neighbourhoods, 12 requests: the promise is an estimate. 'firm_kw' is the promise times the "
                        "WORST delivery ratio seen in those 12 requests; with so few samples it is not a bound either.")
    out["reliability"] = rel
    out["disclaimer"] = ("Delivery is measured against an unrequested baseline run in simulation. "
                         "Settlement (baseline definition, metering, agreement, regulation) is not modelled; "
                         "nothing here is a guarantee.")
    return out


def _village_q(homes, pv_kwp, battery_kwh, battery_kw):
    try:
        return rural.validate_village(homes=homes, pv_kwp=pv_kwp, battery_kwh=battery_kwh, battery_kw=battery_kw)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))


@lru_cache(maxsize=16)
def _cached_rural_arm(scenario, arm, seed, backend, endurance_h, homes, pv_kwp, battery_kwh, battery_kw):
    """Rural runs are deterministic; reuse each arm when users revisit the same setup."""
    with rural.village(homes=homes, pv_kwp=pv_kwp, battery_kwh=battery_kwh, battery_kw=battery_kw):
        return rural.run_rural(scenario, arm, seed=seed, backend=backend, t1_endurance_h=endurance_h)


@app.get("/api/rural")
def rural_run(scenario: str = "cloudy_outage", arm: str = "neos", seed: int = Query(0, ge=0, le=999),
              backend: str = "auto", t1_endurance_h: float = Query(rural.T1_ENDURANCE_H, ge=1, le=48),
              homes: int = None, pv_kwp: float = None, battery_kwh: float = None, battery_kw: float = None):
    if arm not in ("naive", "neos"):
        raise HTTPException(400, detail="arm must be naive or neos")
    if scenario not in rural.SCENARIOS:
        raise HTTPException(400, detail=f"unknown rural scenario '{scenario}'")
    v = _village_q(homes, pv_kwp, battery_kwh, battery_kw)
    with rural.village(**v):
        m, s = rural.run_rural(scenario, arm, seed=seed, backend=backend, t1_endurance_h=t1_endurance_h)
    return dict(metrics=m, series=s, tiers=rural.TIER_NAMES, village=v)


@app.get("/api/rural/compare")
def rural_compare(scenario: str = "cloudy_outage", seed: int = Query(0, ge=0, le=999), backend: str = "auto",
                  t1_endurance_h: float = Query(rural.T1_ENDURANCE_H, ge=1, le=48),
                  homes: int = None, pv_kwp: float = None, battery_kwh: float = None, battery_kw: float = None):
    if scenario not in rural.SCENARIOS:
        raise HTTPException(400, detail=f"unknown rural scenario '{scenario}'")
    v = _village_q(homes, pv_kwp, battery_kwh, battery_kw)
    out = {}
    for a in ("naive", "neos"):
        metrics, series = _cached_rural_arm(scenario, a, seed, backend, t1_endurance_h,
                                             v["homes"], v["pv_kwp"], v["battery_kwh"], v["battery_kw"])
        out[a] = dict(metrics=metrics, series=series)
    return dict(scenario=scenario, title=rural.SCENARIOS[scenario]["title"], tiers=rural.TIER_NAMES,
                village=v, is_default_village=(v == rural.DEFAULT_VILLAGE),
                village_bounds=rural.VILLAGE_BOUNDS, **out)


import re as _re, tempfile as _tmp
_HUBS: dict = {}
_HUB_HH = [f"hh{i:02d}" for i in range(1, 9)]
_HUB_DIR = pathlib.Path(os.environ.get("NEOS_HUB_DIR", pathlib.Path(__file__).parent / "data" / "hub"))
_HUB_MAX_LIVE = 200
_HUB_LOCK = threading.Lock()
_SID = _re.compile(r"[A-Za-z0-9_-]{4,40}")


def _hub(sid):
    """One hub per session id, saved to JSON after every change. The id is a label, NOT authentication."""
    if not _SID.fullmatch(sid or ""):
        raise HTTPException(400, detail="sid must be 4-40 characters of letters, digits, _ or -")
    with _HUB_LOCK:
        h = _HUBS.pop(sid, None)
        if h is None:
            f = _HUB_DIR / f"{sid}.json"
            try:
                h = hubmod.Hub.from_dict(json.loads(f.read_text())) if f.exists() else hubmod.Hub(_HUB_HH, now=0)
            except (OSError, ValueError, KeyError):
                h = hubmod.Hub(_HUB_HH, now=0)
        _HUBS[sid] = h
        while len(_HUBS) > _HUB_MAX_LIVE:
            _HUBS.pop(next(iter(_HUBS)))
        return h


def _hub_save(sid):
    try:
        _HUB_DIR.mkdir(parents=True, exist_ok=True)
        h = _HUBS[sid]
        fd, tmp = _tmp.mkstemp(dir=_HUB_DIR, suffix=".tmp")
        with os.fdopen(fd, "w") as fh:
            json.dump(h.to_dict(), fh)
        os.replace(tmp, _HUB_DIR / f"{sid}.json")
    except OSError:
        pass          # persistence is best-effort; the in-memory state is still correct


def _hh(sid, h):
    if h not in _HUB_HH:
        raise HTTPException(404, detail=f"unknown household '{h}'")
    return _hub(sid).devices[h]


@app.get("/api/hub")
def hub_state(sid: str, start: int = Query(72, ge=0, le=200), end: int = Query(80, ge=1, le=200)):
    if end <= start:
        raise HTTPException(400, detail="end must be after start")
    h = _hub(sid)
    return dict(**h.snapshot(), committed=h.committed_kw(start, end), window=[start, end])


@app.post("/api/hub/{hh}/pledge")
def hub_pledge(hh: str, sid: str, kw: float = Query(..., gt=0, le=10), start: int = Query(..., ge=0), end: int = Query(..., ge=1)):
    ev = _hh(sid, hh).submit(_hub(sid), "pledge", start, end, kw); _hub_save(sid)
    return dict(seq=ev.seq, outcome=ev.outcome, reason=ev.reason)


@app.post("/api/hub/{hh}/withdraw")
def hub_withdraw(hh: str, sid: str):
    ev = _hh(sid, hh).submit(_hub(sid), "withdraw"); _hub_save(sid)
    return dict(seq=ev.seq, outcome=ev.outcome, reason=ev.reason)


@app.post("/api/hub/{hh}/online")
def hub_online(hh: str, sid: str, online: bool):
    _hh(sid, hh).online = online; _hub_save(sid)
    return dict(hh=hh, online=online)


@app.post("/api/hub/{hh}/sync")
def hub_sync(hh: str, sid: str):
    d = _hh(sid, hh)
    if not d.online:
        raise HTTPException(409, detail="device is offline; nothing to sync")
    evs = d.sync(_hub(sid)); _hub_save(sid)
    return dict(synced=[dict(seq=e.seq, outcome=e.outcome, reason=e.reason) for e in evs])


@app.post("/api/hub/sms")
def hub_sms(sid: str, sender: str, text: str = Query(..., max_length=80)):
    if sender not in _HUB_HH:
        raise HTTPException(404, detail="unknown number")
    reply, _ = _hub(sid).sms(sender, text); _hub_save(sid)
    return dict(reply=reply)


@app.post("/api/hub/settle")
def hub_settle(sid: str, start: int = Query(72, ge=0), end: int = Query(80, ge=1), fraction: float = Query(1.0, ge=0, le=1.5)):
    """SIMULATED delivery: every pledging household achieves `fraction` of its pledge."""
    h = _hub(sid)
    ach = {k: (x.pledge["kw"] * fraction if x.pledge else 0.0) for k, x in h.h.items()}
    out = h.settle(start, end, ach); _hub_save(sid)
    return out


@app.post("/api/hub/reset")
def hub_reset(sid: str):
    _hub(sid)
    with _HUB_LOCK:
        _HUBS[sid] = hubmod.Hub(_HUB_HH, now=0)
    _hub_save(sid)
    return dict(ok=True)


@app.get("/api/rural/scenarios")
def rural_scenarios():
    return [dict(key=k, title=v["title"], outage=v["outage"]) for k, v in rural.SCENARIOS.items()]


@app.get("/api/platform/evidence")
def platform_evidence():
    """Archived rural + DISCOM studies (results_platform.json), regenerated by platform_runner.py."""
    if not PLATFORM.exists():
        raise HTTPException(404, detail="results_platform.json not found; run platform_runner.py")
    d = json.loads(PLATFORM.read_text())
    for pop in ("dev", "held_out"):
        pass
    d["rural"].pop("example", None)
    return d


@app.get("/api/forecast/benchmark")
def benchmark(n_homes: int = 300, n_ev: int = 60, n_pv: int = 150, n_bess: int = 26, tx_kva: float = 630.0):
    cfg = cfg_from(n_homes, n_ev, n_pv, n_bess, tx_kva)
    w = service.get_world(cfg)
    if 'bench' not in w:
        w['bench'] = service.forecast_benchmark(cfg)
        w['pvcal'] = service.pv_ensemble_calibration(cfg, reps=2)
    return dict(models=w['bench'], pv_ensemble=w['pvcal'])


@app.get("/api/run")
def run(scenario: str = Query("normal"), mode: str = Query("coordinated"), rep: int = 0,
        n_homes: int = 300, n_ev: int = 60, n_pv: int = 150, n_bess: int = 26, tx_kva: float = 630.0):
    check(scenario, mode)
    cfg = cfg_from(n_homes, n_ev, n_pv, n_bess, tx_kva)
    m, series, fc = service.run(cfg, scenario, mode, rep=rep)
    return dict(metrics=m, series=series, forecast=service.flat_fc(fc))


@app.get("/api/compare")
def compare(scenario: str = "normal", rep: int = 0, n_homes: int = 300, n_ev: int = 60,
            n_pv: int = 150, n_bess: int = 26, tx_kva: float = 630.0):
    check(scenario)
    cfg = cfg_from(n_homes, n_ev, n_pv, n_bess, tx_kva)
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
    cfg = cfg_from(n_homes, n_ev, n_pv, n_bess, tx_kva)
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
    cfg = cfg_from(n_homes, n_ev, n_pv, n_bess, tx_kva, backend)
    m, _series, _fc = _page_run(scenario, mode, rep, n_homes, n_ev, n_pv, n_bess, tx_kva, backend)
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
    cfg = cfg_from(n_homes, n_ev, n_pv, n_bess, tx_kva, backend)
    m, _series, _fc = _page_run(scenario, mode, rep, n_homes, n_ev, n_pv, n_bess, tx_kva, backend)
    traces = m.get('traces', [])
    if status is not None:
        want = status.upper()
        if want not in ('VERIFIED', 'INFEASIBLE', 'SOLVER_FAILED'):
            raise HTTPException(400, detail="status must be one of VERIFIED, INFEASIBLE, SOLVER_FAILED")
        traces = [t for t in traces if t['safety_status'] == want]
    return dict(scenario=scenario, mode=mode, count=len(traces), traces=traces)


@app.get("/api/twin/powerflow")
def powerflow(load_kw: float = 400.0, n_homes: int = 300, n_ev: int = 60, n_pv: int = 150,
              n_bess: int = 26, tx_kva: float = 630.0):
    cfg = cfg_from(n_homes, n_ev, n_pv, n_bess, tx_kva)
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
             n_pv: int = 150, n_bess: int = 26, tx_kva: float = 630.0,
             x_api_key: str | None = Header(default=None)):
    """Register a user-declared requirement. See module docstring: this is queued
    for the next simulation run of this scope and commands NO device."""
    require_key(x_api_key)
    check(scenario)
    cfg = cfg_from(n_homes, n_ev, n_pv, n_bess, tx_kva)
    if not 0 <= ev_id < n_ev:
        raise HTTPException(422, detail=f"ev_id must be in [0, {n_ev})")
    try:
        e = service.LEDGER.submit(scenario, ev_id, kind, lead_steps, notice_step, rep=rep, cfg=cfg)
        _cached_page_run.cache_clear()
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


@app.get("/api/evidence")
def evidence():
    if not RESULTS.exists():
        raise HTTPException(404, detail="results.json not found: run `python experiments.py`")
    return JSONResponse(json.loads(RESULTS.read_text()))


if WEB.exists():
    # html=True serves index.html at "/" and every sibling file (console.html,
    # flexibility.html, trace.html, evidence.html, assets/*) at its own clean
    # path, with the pages' own relative ("./assets/...", "./console.html")
    # references resolving correctly. Registered LAST and matched by prefix,
    # so it never shadows the /api/* routes above -- Starlette tries routes
    # in registration order and every /api/* path is registered first.
    app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
