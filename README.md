# Neighbourhood Energy OS

A reproducible neighbourhood-energy **research simulator**: causal probabilistic forecasting, a physics-based
digital twin, a chance-constrained MPC benchmark, and a safety layer that re-solves every action it returns against an
unbalanced AC power flow and labels it `verified`, `infeasible` or `solver_failed`.

It commands no equipment. MARL, hardware-in-the-loop and measurement-calibrated twins are **not built**
(see `AUDIT-STATUS.md`).

    pip install -r requirements.txt
    python run_tests.py                           # full suite (pytest); writes test_status.json, the single source of the count
    python experiments.py                         # ~7 min with OpenDSS, writes results.json
    python build_static.py                        # offline console: static-demo.html
    npm i --no-save @babel/standalone && node build_tsx.js   # only if you edit web/src/*.tsx
    python build_web.py && uvicorn server:app --port 8000   # multi-page site at http://localhost:8000
                                                             # (Home, Console, Flexibility, Decision trace, DISCOM, Rural, Hub, Evidence)
    python validate_backends.py                   # cross-checks ReferenceGrid against OpenDSS

If your environment can't keep a long-running process alive across commands (true of some
sandboxes), `stage_runner.py` runs `experiments.py`'s protocol as resumable stages, appending
progress to `stage_progress.json` after each scenario/population so a hard per-command time limit
can't lose completed work: `python stage_runner.py scenario normal`, ... `heldout 21`, `extras`,
then `assemble results.json`.

**Backend status:** `results.json → meta.grid_backend` tells you which solver produced the numbers
you're looking at. The figures below are `"opendss"` -- the real unbalanced AC power-flow backend,
not the fast NumPy reference used for quick iteration. See `AUDIT-STATUS.md → "OpenDSS
verification"` for the cross-check between the two and a test-tolerance bug found and fixed while
running this for the first time on a real OpenDSS install.

## Headline result (630 kVA feeder, 300 homes, 60 EVs, 26 batteries, 781 kWp, OpenDSS backend)

Peak transformer loading, mean of 5 paired replicates per scenario. Δ is MPC minus no coordination, in percentage
points, with a 95 % t-interval on the paired difference. `rule` is a transparent time-of-use heuristic.

| Scenario | None | Rule | MPC | Δ MPC vs none (95 % CI) |
|---|---|---|---|---|
| Normal day | 98.9 | 98.2 | 70.5 | −28.4 [−30.5, −26.2] |
| Cloud passage | 92.2 | 96.6 | 66.7 | −25.5 [−28.7, −22.3] |
| Extreme heat | 116.8 | 99.8 | 87.2 | −29.6 [−31.5, −27.7] |
| EV surge | 105.1 | 99.2 | 69.9 | −35.2 [−37.9, −32.4] |
| Renewable shortage | 107.5 | 99.5 | 82.1 | −25.4 [−29.4, −21.4] |
| Battery outage | 100.2 | 96.4 | 74.9 | −25.3 [−27.7, −23.0] |
| Demand spike | 127.3 | 108.4 | 97.2 | −30.1 [−32.3, −27.8] |

The reduction replicates on two neighbourhood populations never used in development (seeds 21 and 22, also on
OpenDSS): normal-day peak 94-96 % uncoordinated → 62-64 % with MPC; extreme heat 109-112 % → 77-80 %; demand spike
119-124 % → 86-89 %. The optimiser keeps every scenario under 100 % on both held-out populations (all numbers from
`results.json`, regenerated on OpenDSS).

**Read the rule column carefully.** The rule policy sits near 100 % only because the safety layer clips it;
it needed far more safety interventions than the optimiser (normal day: 6.6 vs 0.2 steps per run; renewable shortage: 3.0 vs 0.0). A policy that is "safe" because it is constantly
overridden is not the same as one that plans well.

## What the optimiser costs (reported, not hidden)

* **It misses some EV requirements**: 1-6 of 270-290 sessions per scenario (about 1-2 %; EV satisfaction 99.3 % vs 100 % uncoordinated on a normal day).
  A P10 departure commitment accepts tail risk: some drivers leave earlier than the committed time.
* **It uses slightly less of the available solar** (97.2 % vs 99.3 % utilisation on a normal day, a 2.1-point loss, 95 % CI [0.8, 3.4]).
* **Explicit requirements cannot create energy.** In the override study a driver who says "I need my car now" with no notice gets only the SOC already banked (8.0 kWh short, reported as `applied_infeasible`). Part of that is energy the optimiser had deliberately deferred: the price of flexibility.
* **Demand spike is not fully solved**: 12 steps across the five replicates are `infeasible` (heat now has none on OpenDSS;
  uncoordinated has 54 and the rule policy 35 in the spike). The system reports them rather than certifying them.

## What is rigorous here, and what is not

Rigorous: paired replicates; causal forecasting enforced in code; every step has a constraint status (voltage per phase,
transformer kVA, per-line ampacity); energy accounting audited to ~1e-14 kWh with zero post-hoc clipping; four reintroduced
audit bugs are each caught by the tests; the full test suite (count: see test_status.json) and `validate_backends.py` have now been run against a real
OpenDSS install, not only the NumPy reference solver, and `results.json` above was generated on OpenDSS.

Not rigorous: all data and weather forecasts are synthetic (absolute numbers are not predictions for a real feeder);
forecast skill on synthetic demand is optimistic; the twin-calibration study identifies one parameter on a plant of the
same structure; `server.py`'s routing/validation/serialisation was exercised in-process (FastAPI `TestClient`) in-process, and separately over a live socket in a single-command browser test; the pages were checked in headless Chromium against a real server (see AUDIT-STATUS.md), not on a range of real devices. Full list:
`results.json → limitations`, and see `AUDIT-STATUS.md` for exactly what "verified" means in each case.

## Layout

    neos/grid.py          OpenDSSGrid (independent contexts) + ReferenceGrid (NumPy); constraint_report
    neos/forecast.py      causal features, quantile GBM, lead-bucketed conformal, honest scores
    neos/nwp.py           issue-time synthetic weather forecasts (labelled assumption)
    neos/control.py       flexibility envelopes, sparse LP-MPC, direction-aware safety projection
    neos/controllers.py   policy interface: uncoordinated | rule_tou | MPC  (a MARL policy plugs in here)
    neos/runner.py        closed loop: device limits -> safety -> energy update from verified dispatch
    neos/calibration.py   physical twin parameter estimation, verification gap, voltage margin
    neos/flexibility.py   Flexibility Envelope: safe mobilisable capability, status always ESTIMATED
    neos/decision_trace.py  per-step human-readable trace; VERIFIED/INFEASIBLE/SOLVER_FAILED, never collapsed
    neos/phase.py         per-phase transformer loading and imbalance view
    neos/discom.py        DISCOM request decisions (ACCEPT/MODIFY/DEFER/REJECT) + measured delivery protocol
    neos/rural.py         rural kit: priority tiers, island mode, editable village (SIMULATED)
    neos/hub.py           community hub: offline-first pledge protocol (SIMULATED)
    platform_runner.py    staged, resumable rural + DISCOM studies -> results_platform.json
    validate_platform.py  OpenDSS vs reference cross-check for the platform features
    run_tests.py          runs the suite, writes test_status.json
    neos/service.py       bounded world cache, forecasts, override ledger
    server.py             API; POST /api/override queues an auditable event and commands nothing;
                          GET /api/flexibility, GET /api/decision_trace (opt-in, additive, see AUDIT-STATUS.md)
    tests/                regression tests, one group per audit finding
    research/             literature-matrix scaffold (to be filled by reading the papers)

## Roadmap status

The roadmap (`NEOS_Next_Steps_V3_Roadmap.md`) has 35 sections across 15 stages. Built here: Stage 1 (Flexibility Envelope, Decision Trace),
a per-phase view, a DISCOM request interface, a rural kit with island mode and an offline-pledging hub. The rural, DISCOM and hub
parts are simulations on synthetic data, not deployments. Not built: the stress-test lab (partly), MARL, hardware-in-the-loop, real data,
settlement, island dynamics. `SPEC-TRACEABILITY.md` gives the item-by-item status; `AUDIT-STATUS.md` records what each "verified" means.

## MARL

Not built. The optimiser is the benchmark any RL policy must beat. Implement `act(obs) -> Proposal`
(`neos/controllers.py`); it will pass through the same device limits and safety layer and be scored by the same
metrics. Report RL as a delta against the `rule_tou` and `coordinated` rows in `results.json`, with safety
interventions as a first-class metric.


## v4 platform additions

Pages: `/discom.html` (phase view + DISCOM requests), `/rural.html` (rural kit, tiers, island, editable village), `/hub.html` (offline pledging).
Studies: `python platform_runner.py` (staged, resumable) writes `results_platform.json`.

**Test count.** No document should hard-code it: `python run_tests.py` writes `test_status.json`, which the Evidence page and `/api/meta` read.
Counts in `AUDIT-STATUS.md` from before the v4 section are historical.

**Editable village.** Rural page inputs: homes, solar kWp, battery kWh, battery kW. The archived held-out study covers the **default village only**
(90 homes, 45 kWp, 120 kWh / 40 kW); any other setting is a single-seed simulation with no study behind it, and the page says so.

**Community hub.** A demo of an offline-first pledging protocol, not the shared-battery energy hub of the wider NEOS idea. State is held per browser
session id (not a login) and saved to JSON under `data/hub/` (override with `NEOS_HUB_DIR`); there is no authentication, so anyone with a session id can act as that session.

## Render and Vercel deployment

Render runs the FastAPI service defined in `render.yaml` and serves the pages plus API. For the Vercel frontend, use `web` as the static output/root directory and leave the build command empty; the committed React bundles are already built. `web/assets/api.js` sends `/api/*` and event-stream requests to `https://neos-arpj.onrender.com`. For a different API URL, set `localStorage.neosApiBase` to that API origin. Localhost development stays same-origin. The API must allow the Vercel origin through CORS; the current FastAPI app allows cross-origin requests.