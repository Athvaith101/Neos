# Neighbourhood Energy OS — stack, framework and calibration plan

> **Status note (v1.1-research).** This document describes the architecture. Several claims in the first version were
> wrong or unverifiable and have been corrected; see `AUDIT-STATUS.md` for the full list. In particular: the OpenDSS
> backend now uses independent contexts and is *optional* (a NumPy `ReferenceGrid` is used for fast iteration when
> OpenDSS isn't installed); forecast scores are "mean pinball", not CRPS; the old "online twin calibration" was a
> heuristic headroom nudge, and real parameter estimation now lives in `neos/calibration.py`; numbers quoted below are
> superseded by `results.json`. **Update:** the reference solver HAS now been cross-checked against real OpenDSS
> (`validate_backends.py`, run in a container with `opendssdirect.py` installed) -- voltage agreement within 0.0022 pu,
> power accounting within 0.05% relative -- and `results.json` was regenerated with `grid_backend: "opendss"`, not the
> reference. See `AUDIT-STATUS.md → "OpenDSS verification"` for the full numbers and a test-tolerance bug this exposed
> and fixed (the isolation test's original tolerance was tighter than any iterative AC solver can reproduce even
> against itself; the production isolation code was correct, the test's precision expectation was not).


## 1. Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python experiments.py                       # batch study -> results.json  (~90 s)
python build_web.py                         # regenerate the console from the template
uvicorn server:app --reload --port 8000     # open http://localhost:8000
```

First request builds the OpenDSS circuit, simulates 32 days of history and fits the
forecaster (~15 s), then everything is cached. Changing the neighbourhood from the
console's sliders rebuilds that cache under a new key.

---

## 2. Every tool used, and why

### Power system / digital twin

| Tool | Version used | Role in this project | Why this one |
|---|---|---|---|
| **OpenDSS** (EPRI) | engine 0.14.5 | The physics. Unbalanced three-phase load flow on an 11 kV / 433 V network: 630 kVA Dyn11 transformer, 4 LV feeders, 40 buses, 120 single-phase load points. Solves in ~1 ms. Each model owns an independent OpenDSS context (`dss.NewContext()`), verified by alternating two models thousands of times with no cross-contamination. When OpenDSS is not installed the NumPy `ReferenceGrid` (same network, backward/forward sweep, cross-checked against OpenDSS to within 0.0022 pu voltage and 0.05% power) is used instead and the console says so. `results.json` in this repository was generated with `grid_backend: "opendss"`. | Industry-standard distribution solver, free, used by utilities and by EPRI itself. Writing our own power-flow solver would have been the single fastest way to lose credibility. |
| **OpenDSSDirect.py** | 0.9.4 | Python binding to the OpenDSS engine (`dss-python` 0.15.7 underneath). | Direct in-process API — no COM, works on Linux, fast enough to sit inside a control loop. |

The alternative, **GridLAB-D**, was considered and dropped: it is stronger on
building thermal models and weaker on the unbalanced LV power flow we actually
needed, and it adds a co-simulation bus for no benefit at this scale. If the project
later needs detailed building physics, GridLAB-D (or EnergyPlus) is the right
second simulator, coupled over **HELICS**.

### Optimisation

| Tool | Role | Why |
|---|---|---|
| **HiGHS**, via `scipy.optimize.linprog` | Solves the model-predictive control problem: ~13,000 variables, ~9,000 constraints, per control step, in roughly 30 ms | Open source, no licence, ships inside SciPy, fast enough to re-solve every 30 simulated minutes. No Gurobi/CPLEX dependency means the judges can run it. |
| **`scipy.sparse`** | Constraint matrix assembly | The constraint matrix is ~0.1 % dense; building it dense would be 100× slower and would not fit comfortably in memory. |

`cvxpy` and `Pyomo` were deliberately avoided: they add a modelling layer and a
translation step for a problem we can write directly, and both make the hot loop
slower.

### Forecasting

| Tool | Role |
|---|---|
| **scikit-learn `HistGradientBoostingRegressor`** with `loss="quantile"` | The demand forecaster. One model per quantile (P10/P50/P90). |
| **Split-conformal calibration** (Romano, Patterson & Candès, CQR, 2019) — implemented directly in `neos/forecast.py` | Repairs the coverage of the raw quantile model. On the chronological hold-out the nominal 80 % band covers ~72 % raw and ~83 % after widening, in every lead bucket (`results.json`). Conformal guarantees assume exchangeability, which time series violate, so coverage is measured, never assumed. |
| **NumPy** | The weather ensemble: 100 Ornstein–Uhlenbeck cloud-opacity trajectories, so cloud events are temporally correlated rather than white noise. |

No PyTorch, no Temporal Fusion Transformer, no PatchTST — on 32 days of
neighbourhood-aggregate data a deep sequence model has nothing to learn that
gradient boosting with weather features does not already capture. The benchmark
table in the console is the evidence for that claim, and it is the honest form of
your plan's "the model must earn its place".

### Backend

| Tool | Role |
|---|---|
| **FastAPI** 0.141 | HTTP API. Typed query parameters, automatic OpenAPI docs at `/docs`. |
| **Uvicorn** 0.53 | ASGI server. |
| **Server-Sent Events** (via `StreamingResponse`) | Streams one telemetry frame per control step to the console. Chosen over WebSockets because the flow is strictly one-way and SSE reconnects by itself. |
| **`threading` + `queue`** | The simulation is synchronous and CPU-bound; it runs on a worker thread and feeds the async generator through a queue. A process-wide lock serialises it, because OpenDSS holds one global circuit per process. |

### Frontend

| Tool | Role |
|---|---|
| **Vanilla JS + hand-written inline SVG** | Every chart. About 200 lines of chart kit, no dependency. |
| **IBM Plex Sans / Mono** (Google Fonts) | Typeface designed for technical interfaces; tabular figures so numbers do not jitter as they stream. |
| **CSS custom properties** | Light/dark theming from one token block. |

No React, no D3, no Chart.js: the whole console is one static HTML file the judges
can open from a USB stick if your Wi-Fi fails. That is a deliberate demo-risk
decision, not laziness.

---

## 3. The framework

```
                    ┌──────────────── every 15 min ───────────────┐
                    │                                             │
   ┌────────────────▼─────────────────┐                           │
   │ 1. SENSE                         │  telemetry: 120 LV points │
   │    neos/dataset.py               │  weather, EV plug events  │
   └────────────────┬─────────────────┘                           │
                    │                                             │
   ┌────────────────▼─────────────────┐                           │
   │ 2. FORECAST     neos/forecast.py │                           │
   │    demand  -> P10 / P50 / P90    │  conformal-calibrated     │
   │    solar   -> 100-member ensemble│  physics-based ensemble   │
   └────────────────┬─────────────────┘                           │
                    │                                             │
   ┌────────────────▼─────────────────┐                           │
   │ 3. ENVELOPE     neos/control.py  │  per user, not per device │
   │    departure P10 / P50 / P90     │                           │
   │    energy need, power limit      │                           │
   │    priority, hard-override flag  │                           │
   └────────────────┬─────────────────┘                           │
                    │                                             │
   ┌────────────────▼─────────────────┐                           │
   │ 4. OPTIMISE     neos/control.py  │  LP, 24 h horizon         │
   │    min  energy + peak + wear     │                           │
   │         + curtailment + comfort  │                           │
   │    s.t. import headroom  (P90 demand, P10 solar)             │
   │         export limit     (P10 demand, P90 solar)             │
   │         SOC dynamics, delivery deadlines                     │
   │    every soft constraint carries a priced slack              │
   └────────────────┬─────────────────┘                           │
                    │ proposed setpoints                          │
   ┌────────────────▼─────────────────┐                           │
   │ 5. VERIFY       neos/grid.py     │  unbalanced AC load flow  │
   │    voltage band 0.94–1.06 pu     │  OpenDSS                  │
   │    transformer thermal limit     │                           │
   │    infeasible -> project, never reject silently              │
   │    sacrifice order: flexible load first, renewables last     │
   └────────────────┬─────────────────┘                           │
                    │ safe setpoints                              │
   ┌────────────────▼─────────────────┐                           │
   │ 6. ACT / MEASURE  neos/runner.py │                           │
   └────────────────┬─────────────────┘                           │
                    │                                             │
   ┌────────────────▼─────────────────┐                           │
   │ 7. CALIBRATE THE TWIN            │  measured outcome shrinks │
   │    aggregate limits move toward  │  or relaxes the headroom  │
   │    what the feeder actually did  │  and export limits        │
   └──────────────────────────────────┴─────────────────────────►─┘
```

Three properties are worth stating out loud when you present:

1. **Layer 5 is policy-agnostic.** It does not know or care whether the setpoints
   came from the LP or from a reinforcement-learning policy. That is what makes it
   possible to swap the controller later without re-auditing the safety argument.
2. **Layer 2 feeds layer 4 as a constraint, not as a number.** The width of the
   forecast band is a safety input. An over-confident interval is a physical risk,
   which is why calibration is in the pipeline rather than in an appendix.
3. **Layer 7 closes the loop on the model itself**, not just on the plant. The
   aggregate headroom the optimiser uses is corrected by what the AC solve actually
   reported, because no single aggregate number can capture where PV and load
   really sit on the feeder.

### Repository layout

```
neos/
  world.py       synthetic population, weather ensemble, behavioural load models
  grid.py        OpenDSS circuit, power flow, voltage sensitivities
  dataset.py     rolls the world forward into per-asset time series
  forecast.py    baselines, quantile GBM, conformal calibration, scoring rules
  control.py     flexibility envelopes, LP-MPC, safety projection layer
  runner.py      closed-loop co-simulation, device limits, metrics (headroom adaptation is a heuristic, not calibration)
  service.py     caching layer between the API and the simulation
server.py        FastAPI app + SSE stream
build_web.py     generates web/index.html from dash_template.html
web/index.html   operator console
experiments.py   batch study -> results.json
```

### API surface

| Endpoint | Returns |
|---|---|
| `GET /api/meta` | nameplate, plus import headroom and reverse-flow limit probed from the twin |
| `GET /api/scenarios` | scenario catalogue |
| `GET /api/forecast/benchmark` | baselines vs quantile GBM vs conformal, both horizons |
| `GET /api/run?scenario=&mode=` | one arm: metrics + full series |
| `GET /api/compare?scenario=` | both arms side by side |
| `GET /api/stream?scenario=&mode=` | SSE, one frame per control step |
| `GET /api/twin/powerflow?load_kw=` | single AC solve — probe the twin directly |
| `POST /api/override` | a user declaring a hard constraint |

---

## 4. Calibrating without a Schneider dataset

Assume there is no Schneider feeder dataset. That is the normal case, and it does
not block you — it just changes what you calibrate against. Three tiers, in the
order you should attempt them.

### Tier 1 — network topology: use a published test feeder

Replace the synthetic 40-bus network with the **IEEE European Low Voltage Test
Feeder** (906 buses, 55 single-phase customers, 416 V, 50 Hz, one-minute load
shapes). It ships as an OpenDSS model in the `IEEETestCases/LVTestCase` folder of
the OpenDSS distribution, and is published by the IEEE PES Distribution Systems
Analysis Subcommittee at `cmte.ieee.org/pes-testfeeders/resources/`. Swapping
`neos/grid.py` to load that circuit instead of building one is roughly a day of
work, and it converts "our synthetic feeder" into "the standard LV benchmark
feeder", which is a much harder result to argue with.

Caveat to state honestly: it is a UK feeder, not an Indian one. Keep your own
synthetic Indian feeder as the primary case and report the IEEE feeder as a
cross-check that the method is not tuned to one topology.

### Tier 2 — behaviour: public measured data

| Quantity | Source | Notes |
|---|---|---|
| EV charging sessions | **ACN-Data**, Caltech / JPL, `ev.caltech.edu/dataset` — 30,000+ workplace sessions, arrival time, departure, energy delivered | Fit the departure-time and energy distributions that define your flexibility envelopes. Workplace, US — so refit the *shape*, not the absolute levels. A static 2018–2020 snapshot also exists on GitHub if the live API is awkward. |
| Household load | Public smart-meter releases (Ausgrid solar-home data, UK-DALE, Pecan Street academic access) | Use these to validate the *diversity* and load-factor statistics of your synthetic population, which is the thing reviewers actually challenge. |
| Solar irradiance | NASA POWER and NSRDB give free hourly GHI/temperature by latitude/longitude; for Coimbatore specifically, NIWE and IMD publish station data | Replaces the analytic clear-sky model with measured irradiance, and lets you validate ensemble coverage against reality. |
| Tariffs | Your state regulator's published tariff order (TNERC for Tamil Nadu) | Replace the illustrative ToD prices with the real schedule. This is a half-hour change with an outsized effect on credibility. |

### Tier 3 — the calibration that costs ₹15,000 and wins the room

Instrument **one real building** — a hostel block, a department, a friend's home —
with a clamp-meter energy monitor logging at one-minute resolution for two weeks.
Then show a single chart: your synthetic household model against that measured
profile, with the error quantified.

You are not claiming the feeder is real. You are claiming the *behavioural model
that drives it* is grounded in measurement. That is a claim you can defend, it is
achievable before the deadline, and it answers the only question that matters
about a simulation-based entry: "how do you know your inputs are right?"

### What to say if asked outright

> We do not have a measured feeder dataset. What we have is a standard benchmark
> topology, behaviour distributions fitted to published measured data, and one
> building we metered ourselves. The uncertainty in those inputs is quantified,
> and the calibration loop in the system is designed to correct exactly this kind
> of error from telemetry once it is connected to a real network.

That answer is stronger than a dataset you cannot explain.

---

## 5. Claim discipline

Print this and keep it in front of you.

**What we claim**
- On a modelled 630 kVA Indian LV feeder with 300 homes, 60 EVs and 781 kWp of
  rooftop solar, coordination removes transformer overload in six of seven stress
  scenarios, cuts the neighbourhood bill by 8–19 %, and does so without a single
  EV owner failing to get the charge they asked for.
- Every action is verified against an unbalanced AC power flow before it is issued.
- The forecast intervals the controller relies on are calibrated, and we measure it.

**What we do not claim**
- That these numbers transfer unchanged to a real feeder.
- That reinforcement learning currently beats the optimiser — we have not built it.
- That the load and EV behaviour models are validated against Indian measurements
  yet. (Tier 3 above is how that stops being true.)
