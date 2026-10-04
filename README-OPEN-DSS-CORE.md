# Neighbourhood Energy OS

A reproducible neighbourhood-energy **research simulator**: causal probabilistic forecasting, a physics-based
digital twin, a chance-constrained MPC benchmark, and a safety layer that re-solves every action it returns against an
unbalanced AC power flow and labels it `verified`, `infeasible` or `solver_failed`.

It commands no equipment. MARL, hardware-in-the-loop and measurement-calibrated twins are **not built**
(see `AUDIT-STATUS.md`).

    pip install -r requirements.txt
    python -m unittest discover -s tests -t .     # 47 tests, all pass with OpenDSS installed
    python experiments.py                         # ~7 min with OpenDSS, writes results.json
    python build_static.py                        # offline console: static-demo.html
    python build_web.py && uvicorn server:app --port 8000   # live operator console
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
| Normal day | 98.9 | 98.2 | 72.5 | −26.4 [−31.8, −21.0] |
| Cloud passage | 92.2 | 96.6 | 66.4 | −25.8 [−29.6, −22.0] |
| Extreme heat | 116.8 | 99.8 | 96.7 | −20.1 [−28.2, −11.9] |
| EV surge | 105.1 | 99.2 | 71.6 | −33.5 [−40.9, −26.1] |
| Renewable shortage | 107.5 | 99.5 | 82.8 | −24.8 [−27.9, −21.6] |
| Battery outage | 100.2 | 96.4 | 75.0 | −25.2 [−27.9, −22.5] |
| Demand spike | 127.3 | 108.4 | 97.4 | −29.9 [−32.5, −27.4] |

The reduction replicates on two neighbourhood populations never used in development (seeds 21 and 22, also on
OpenDSS): mean peak about 100-102 % → 70-72 % (heat: 112-120 % → 77-80 %). The optimiser keeps every scenario
under 100 % on both held-out populations.

**Read the rule column carefully.** The rule policy sits near 100 % only because the safety layer clips it;
it needed roughly 3-11× more safety interventions than the optimiser (renewable shortage: 3 vs 0). A policy that is "safe" because it is constantly
overridden is not the same as one that plans well.

## What the optimiser costs (reported, not hidden)

* **It misses some EV requirements**: 1-6 of 270-290 sessions per scenario (about 1-2 %). Uncoordinated misses none.
  A P10 departure commitment accepts tail risk: some drivers leave earlier than the committed time.
* **It uses less of the available solar** (about 92 % vs 95 % utilisation on a normal day). The chance constraints are
  conservative and the PV ensemble is over-dispersed on clear days.
* **Explicit requirements cannot create energy.** In the override study a driver who says "I need my car now" with no notice gets only the SOC already banked (8.0 kWh short, reported as `applied_infeasible`). Part of that is energy the optimiser had deliberately deferred: the price of flexibility.
* **Extreme heat and demand spike are not solved**: heat leaves 12 infeasible steps and the spike 5, across replicates.
  The system reports them as `infeasible` rather than certifying them.

## What is rigorous here, and what is not

Rigorous: paired replicates; causal forecasting enforced in code; every step has a constraint status (voltage per phase,
transformer kVA, per-line ampacity); energy accounting audited to ~1e-14 kWh with zero post-hoc clipping; four reintroduced
audit bugs are each caught by the tests; the full 47-test suite and `validate_backends.py` have now been run against a real
OpenDSS install, not only the NumPy reference solver, and `results.json` above was generated on OpenDSS.

Not rigorous: all data and weather forecasts are synthetic (absolute numbers are not predictions for a real feeder);
forecast skill on synthetic demand is optimistic; the twin-calibration study identifies one parameter on a plant of the
same structure; `server.py`'s routing/validation/serialisation was exercised in-process (FastAPI `TestClient`) rather than
over a real listening socket, because this particular sandbox could not keep a background server process alive between
commands; the console JavaScript regenerates without error but has not been opened in an actual browser. Full list:
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
    neos/service.py       bounded world cache, forecasts, override ledger
    server.py             API; POST /api/override queues an auditable event and commands nothing;
                          GET /api/flexibility, GET /api/decision_trace (opt-in, additive, see AUDIT-STATUS.md)
    tests/                regression tests, one group per audit finding
    research/             literature-matrix scaffold (to be filled by reading the papers)

## V3 roadmap

A separate roadmap (`NEOS_Next_Steps_V3_Roadmap.md`) lists 35 sections across 15 build stages beyond this
repository -- a stress-test lab, a community-facing hub, a rural deployment kit, MARL, hardware-in-the-loop,
and more. Only Stage 1 ("merge the operating layer": the Flexibility Envelope and Decision Trace above) is
implemented here, because the roadmap itself names it the highest-priority starting point and because
claiming the rest was done without doing it would repeat exactly the failure mode this audit trail exists
to catch. See `AUDIT-STATUS.md -> "V3 roadmap, Stage 1"` for exactly what changed, what it's tested against,
and a real segfault that was found and root-caused (not just silenced) while wiring the new API tests in.

## MARL

Not built. The optimiser is the benchmark any RL policy must beat. Implement `act(obs) -> Proposal`
(`neos/controllers.py`); it will pass through the same device limits and safety layer and be scored by the same
metrics. Report RL as a delta against the `rule_tou` and `coordinated` rows in `results.json`, with safety
interventions as a first-class metric.
