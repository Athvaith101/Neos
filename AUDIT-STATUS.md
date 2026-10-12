# Audit status

Every finding from the code audit, what changed, and the test that would fail if it regressed.
"Verified" means the test passes in this repository's CI container.

**Update (second environment, OpenDSS available):** this package was re-run in a container that
*does* have `opendssdirect.py` installed -- the exact gap the previous pass flagged as untested.
(historical, v1 round) All 47 tests pass, including `test_opendss_context_isolation`, `python validate_backends.py` has
now actually been run, and `results.json` was regenerated end to end on the real OpenDSS backend
(`meta.grid_backend == "opendss"`), not the NumPy reference solver. Details below under
"OpenDSS verification (this pass)". Nothing here is claimed as field-tested on real hardware.

| Pri | Finding | Change | Evidence |
|---|---|---|---|
| P0 | Global OpenDSS engine shared by all `GridModel`s | `OpenDSSGrid` owns an independent `NewContext()`; refuses to fall back to the global circuit; per-model `RLock`. A NumPy `ReferenceGrid` backs fast iteration. | `tests/test_grid.py::TestIsolation` -- **verified on both backends** (see "OpenDSS verification" below for the reference-vs-opendss cross-check and a test-tolerance correction found along the way) |
| P0 | `safety_project` breaks before solving final action; stale telemetry | The final scales are **always** re-solved; the returned state is that solve. | `test_safety.py::test_returned_state_is_the_solve_of_the_returned_action`, `test_flex_scale_zero_still_solves_zero_action` |
| P0 | No convergence / ampacity check, no explicit status | `constraint_report` checks convergence, finiteness, phase-resolved voltage, transformer kVA and **every line's ampacity**. Status is `verified`, `infeasible` or `solver_failed`. | `test_grid.py::TestConstraintReport`, `test_line_ampacity_*`, `test_solver_failure_reported_explicitly` |
| P0 | EV SOC / battery / appliance energy clipped after the solve | Power **and** remaining-energy bounds applied before injections; energy advances from the verified, scaled dispatch; a clip counter and exact energy audit are reported every run. | `test_runner.py::TestEnergyBalance` (residuals ~1e-15 kWh, 0 clip events) |
| P0 | `/api/override` acknowledged without effect | Auditable ledger. Request is `queued`, never `applied` until a run consumes it; response says `applied:false, simulation_only:true`. | `TestOverrideLedger`; server untested, see below |
| P0 | "Hard" override still slack-penalised / scalable | Declared requirements leave the optimiser, charge at the physical maximum, and are the **last** thing the safety layer sheds. Infeasible requests are flagged `applied_infeasible` with unavoidable vs avoidable shortfall. Electrical protection keeps precedence. | `TestExplicitRequirements`, `test_protected_ev_sacrificed_last` |
| P1 | 96-step feature `y[t-T+1]` beyond cutoff | Direct fixed-origin features; reads past the origin **raise**. | `test_forecast.py::TestCausality` |
| P1 | Forecast built from realised weather | `nwp.py` issue-time forecasts: past observed, future = skill-weighted blend of truth and climatology, ensemble spread grows with lead. Labelled synthetic. | `test_nwp_knows_past_not_future` |
| P1 | EV planning uses sampled true departure | Controllers receive an `Obs` containing only plug-in state, learned departure *distribution* and declared requirements. | `TestInformationBarrier` (including shifting the true departure and showing earlier decisions are bit-identical) |
| P1 | Optimiser curtailment never applied | `Proposal.curt_kw` is applied before safety; planned vs protective curtailment accounted separately. | `test_planned_curtailment_is_applied_and_accounted_separately` |
| P1 | `mpc_every=2` repeats first command | `Plan.at(k)` indexes the full plan; replans on arrival, declaration, departure events. | `test_plan_is_indexed_not_repeated`, `test_mpc_uses_multistep_plan` |
| P1 | One scalar scales everything | Resource-level groups, direction-aware: import stress sheds loads (never discharge); export stress sheds discharge/PV (never loads). | `test_import_stress_never_throttles_discharge`, `test_export_stress_never_sheds_loads` |
| P1 | `hash()` seeds | SHA-256 seeds; provenance (versions, source hashes, config) in every results file. | `TestSeeds` (subprocess with different `PYTHONHASHSEED`) |
| P1 | "CRPS" is mean pinball; coverage assumed | Renamed; added 80 % interval score; coverage per lead bucket on a chronological hold-out; conformal widening per lead bucket. | `TestScores`; `results.json: forecast_benchmark` |
| P1 | Voltage filtered by pu magnitude | Buses selected by identity; abnormal voltages retained. | `test_severe_undervoltage_is_retained_not_filtered` |
| P2 | `n_comm` ignored, metadata mismatch, no validation | `NeighbourhoodConfig` with cross-field validation; `n_comm` and `tx_kva` honoured; bounded LRU world cache. | `TestConfig` |
| P2 | Irreproducible historical claims | Old numbers superseded; everything regenerated by `experiments.py`; limitations ship inside `results.json` and the console. | `results.json` |

## V3 roadmap, Stage 1 ("merge the operating layer")

The V3 roadmap (`NEOS_Next_Steps_V3_Roadmap.md`) lists 35 sections across 15 build stages. Implementing
all of it in one pass was not attempted -- that is genuinely weeks of work, and claiming otherwise would
repeat exactly the mistake this audit trail exists to catch. Stage 1, which the roadmap itself names as
the highest-priority starting point, is implemented, tested and documented below. Everything else in the
roadmap (stress-test lab, community hub, rural kit, MARL, hardware-in-the-loop, and the rest) remains
**not built**.

| Section | What it is | Change | Evidence |
|---|---|---|---|
| 5 | Flexibility Envelope | `neos/flexibility.py`: one record per control step, reading ONLY state the control/safety pipeline already computed (controller's own adaptive import headroom, the already-calibrated forecast quantiles, the exact per-device physical bounds `runner.py` uses to limit actions). Adds no second source of truth for any of those numbers. `status` is the literal string `"ESTIMATED"` on every record, enforced in code. | `tests/test_flexibility.py` (10 tests): bounds/invariants (safe flexibility never exceeds the resource sum, never negative, bounded by transformer headroom even when device capability is abundant), the hard-override exclusion rule, the reserve-floor-vs-physical-floor distinction, confidence boundedness, and a pin proving `want_envelope=True` changes nothing about dispatch, safety or the existing numeric series. |
| 6 | Decision Trace | `neos/decision_trace.py`: one deterministic, template-built record per step from the SAME `obs`/`prop`/`res`/`pst` objects the control loop already holds. The three-way safety status (`VERIFIED` / `INFEASIBLE` / `SOLVER_FAILED`) is read from `res.status` directly; an unrecognised status **raises** rather than being silently coerced. | `tests/test_decision_trace.py` (7 tests), including a **regression test for a real bug found while building this**: the `reason` sentence originally named the final state's constraint-violation TYPES (empty, almost by definition, since an intervention's entire point is that the reduced action verifies clean) instead of which safety GROUP was actually throttled -- it said "reduced one or more resource groups" without ever naming one. Fixed to read `res.scales` instead of `res.report['violations']`; `test_names_the_group_and_its_scale` pins the correct wording. Also covers determinism (identical seed -> byte-identical trace) and that `solver_failed` numeric fields stay NaN rather than being backfilled with a stale number. |
| API | `/api/flexibility`, `/api/decision_trace` | Thin, opt-in wiring: `run_scenario(..., want_envelope=False, want_trace=False)` -- both default off, so no existing caller's behaviour, return shape or performance changes. `service.run()` and two new `server.py` endpoints expose them, following the same `cfg_from`/`check()` conventions as every other endpoint. `/api/decision_trace` supports `?status=` filtering to exactly the three required values. | `tests/test_server.py` (7 tests) -- **the first committed test file for `server.py`**; AUDIT-STATUS.md previously recorded this endpoint only as manually checked via an ad hoc `TestClient` session, never as a re-runnable test. |

### A segfault, found and fixed, while wiring the API tests in

Running the complete suite with the new `tests/test_server.py` included reproducibly crashed the Python
process with a native segmentation fault -- not a test failure, a hard crash with no Python traceback.
Root-caused as follows, not just patched until it stopped happening:

1. Bisected which file combination triggered it: `test_grid.py` + `test_server.py` together crashes;
  either file alone, or `test_server.py` combined with any OTHER test file, does not.
2. Reproduced the exact sequence (several `OpenDSSGrid` context creations, then several
  `service.get_world()` calls through a FastAPI `TestClient`) as a **plain Python script with no
  pytest involved at all** -- it did **not** crash. This rules out a logic bug in `OpenDSSGrid`'s context
  isolation itself (already independently proven correct by `test_grid.py`'s isolation test and
  `validate_backends.py`'s cross-check) and implicates a GC-timing interaction between pytest's test
  lifecycle and the native OpenDSS context's cleanup, specific to this combination of files in one
  process.
3. **Fix, scoped correctly rather than papered over:** `test_server.py` exists to verify the API's
  routing, validation and serialisation logic, which is backend-agnostic by construction (the service
  layer dispatches to whichever backend a config names). It does not need to create ADDITIONAL native
  OpenDSS contexts to do that job -- OpenDSS-specific correctness is already `test_grid.py`'s and
  `validate_backends.py`'s responsibility. `test_server.py` is now pinned to `backend='reference'` via a
  newly-exposed `backend` query parameter on `cfg_from` and the three endpoints it touches (`/api/meta`,
  `/api/flexibility`, `/api/decision_trace`), defaulting to `"auto"` everywhere else so no existing
  endpoint's behaviour changes.
4. Verified fixed, not just "didn't crash once": (historical) the full 71-test suite was run four consecutive times
  after the fix with zero crashes.
5. **Acknowledged, not hidden, remaining gap:** `backend='auto'` resolving to OpenDSS through the full
  HTTP layer (API -> `service.get_world` -> `OpenDSSGrid`, end to end) was verified manually during
  development (see "OpenDSS verification" above) and passed, but is deliberately **not** re-asserted as
  an automated test in the committed suite, specifically to avoid reintroducing this instability. See the
  comment in `tests/test_server.py` above `SMALL_Q`.

## Research-tier workstreams

| Workstream | Status |
|---|---|
| Regression testing | Done (47 tests at that round). |
| Electrical validation | Done (phase-resolved V, tx kVA, per-line ampacity, status per step). |
| Deterministic benchmark | Done: sparse LP with solver status, latency, energy audit. |
| Experimental design | Done: paired replicates, 95 % CIs, two held-out populations. Forecaster trained on normal days only, so every other regime is out-of-distribution. |
| Human requirements | Done in simulation: early departure, insufficient time, unannounced disconnect, 12 competing requirements. |
| Digital-twin calibration | Done for one physical parameter (LV impedance), with false-safe rate before/after and a derived voltage margin. Structural mismatch not tested. |
| Research library | Scaffolded in `research/literature-matrix.md`; **the team must populate it by reading the papers**. |
| MARL | **Not implemented.** Policy interface, identical safety layer, identical metrics and two benchmark arms exist. |
| Deployment boundary | API key option, simulation-only header, honest override semantics. No actuators exist, so freshness/acknowledgement handling is not built. |
| Hardware-in-the-loop | Not started. |

## OpenDSS verification (this pass)

* **(historical) Full suite on the real backend: 47/47 pass.** `test_opendss_context_isolation` initially
  **failed** at the original `places=9` (sub-micro-pu) tolerance -- not because contexts leak
  into each other, but because the tolerance was unrealistic for ANY iterative nonlinear AC
  solve. Proof, not assertion: re-solving the SAME `OpenDSSGrid` instance on the IDENTICAL
  injection, with a second context *never created*, already drifts `vmin` by ~2.4e-6 between
  the first two calls (Newton-Raphson warm-starting from the previous converged voltage as its
  initial guess) and `tx_loading` by ~2.4e-3 (~3.6e-5 relative) over the same two calls. A real
  context leak would show the SECOND neighbourhood's topology bleeding into the first model's
  result -- a double-digit-percent effect, four to five orders of magnitude larger than what was
  observed. The test now asserts a 1e-4 *relative* tolerance (dimensionally correct across pu
  voltage, percentage loading and kW, unlike the original fixed-decimal-places check), which
  clears the measured same-instance settling noise with ~3x margin and would still fail hard on
  an actual isolation bug. The production code (`OpenDSSGrid`, `dss.NewContext()` per instance)
  was **not changed** -- only the test's expectation of solver determinism was corrected, and the
  reasoning is left in the test file so this isn't a silent loosening.
* **`validate_backends.py` has now been run.** `ReferenceGrid` (the fast NumPy backward/forward
  sweep used for the rest of the test suite and for quick iteration) cross-checked against
  `OpenDSSGrid` on 60 random operating points: `|dVmin|` mean 0.00094 pu / max 0.00218 pu (under
  4% of the 6% statutory voltage band), relative `tx_kw` error mean 0.017% / max 0.049%, `losses`
  discrepancy up to 0.31 kW, line-loading discrepancy up to 0.22 percentage points. The reference
  solver is a legitimate stand-in for fast iteration; anything reported as a specific percentage
  or kW figure in `results.json` was generated with `grid_backend: "opendss"`, not the reference.
* **`results.json` regenerated on OpenDSS, full protocol** (5 paired replicates per scenario, 2
  held-out neighbourhood populations at seeds 21/22, calibration and override studies) --
  `meta.grid_backend == "opendss"`. Headline finding holds on the real solver: coordinated peak
  transformer loading is 20-34 percentage points lower than uncoordinated across all seven
  scenarios (95% CI on the paired delta excludes zero in 6 of 7; `heat` is the exception, CI
  [-28.2, -11.9]), and the same pattern reproduces on both held-out populations never used during
  development. The previous reference-grid run is kept as `results.reference-grid.json.bak` for
  comparison, not presented as current evidence.
* **`server.py` exercised via FastAPI's `TestClient` in-process** (no real HTTP socket needed in
  this sandbox): `/api/meta` returns `grid_backend: "opendss"` live, `/api/scenarios`,
  `/api/compare`, `/api/forecast/benchmark`, `/api/stream` (SSE) and `/api/twin/powerflow` all
  return correctly-shaped, OpenDSS-backed responses. A real browser against a real socket was
  still not exercised here -- see "Not verified" below.

## Web layer, verified in a real browser (this pass)

Run against a real `uvicorn` socket with Playwright/Chromium, OpenDSS backend:

* **Flexibility and Decision-trace pages** are React 18 + TypeScript (`web/src/*.tsx`). They pass
  `tsc --strict` with no errors, are compiled at build time (`node build_tsx.js`) and load React from
  `web/assets/vendor/` -- no CDN, no in-browser Babel. In the browser: both render real API data, the
  time slider changes the headline value (290 kW -> 118 kW in the check), the status filter works, the
  console has no JavaScript errors, and a 390 px-wide viewport has no horizontal scroll.
* **Console** is white by default and stays light under an OS dark-mode preference (checked). First load
  on the OpenDSS backend is slow while the first comparison runs (tens of seconds), then cached.
* Bug found and fixed: the flexibility chart's x-axis assumed a midnight start, but the simulated day
  runs 12:00 -> 12:00, so the labels were wrong. Labels now come from each record's own hour.
* Bug found and fixed: the trace page's filter row had a duplicated "Infeasible" button; the React
  rewrite removes it. Server-generated text is now rendered through React (escaped), not `innerHTML`.
* Test suite after this pass (historical): **75 passed**.

Not covered by browser tests: the Evidence and Home pages were not re-opened this pass, the live SSE
stream in the console was not exercised, and Google Fonts cannot load in this sandbox, so typography was
checked with fallback fonts.

## Not verified in this environment

* **Stage 2 stress lab** is half-built: `no_control` arm and tests exist; flexibility requested/delivered
  and battery-reserve-violation metrics are declared but not yet populated, and there is no stress-lab page.
* **A "v2.0 master-specification" round** (rural kit, DISCOM emulation, phase-imbalance metric,
  `SPEC-TRACEABILITY.md`) is described in a separate AUDIT-STATUS.md but its code is **not in this
  repository** and is not claimed here.
* **Field deployment, real feeder data, real hardware.** Everything above is a verified
  simulation. It is evidence the code does what it claims to do internally, not evidence about a
  real neighbourhood.


## Platform round (v4)
- The current test count is whatever `test_status.json` says (written by `run_tests.py`); earlier counts in this file are historical. At the time of writing the suite passes in full; `validate_platform.py` ALL PASS (OpenDSS vs reference, rural scenarios).
- Added: per-phase view, DISCOM request interface, rural kit with island mode. See `SPEC-TRACEABILITY.md`.
- Honest findings: DISCOM promises kept 3/12 on held-out neighbourhoods (calibration did not transfer); NEOS does not fix phase imbalance; island mode is simulation-only.
- Not verified: island dynamics, black start, protection, settlement, hardware, real data.
- Bug disclosed: OpenDSS impedance scaling was silently ignored until fixed (urban study unaffected, used reference grid).

### Follow-up round (user review points)
1. Rural village now editable (homes, solar, battery kWh/kW; derived inverter 1.25x battery kW and transformer scaled with homes). Default reproduces the archived results exactly (tested). The held-out study is default-village only.
2. Community hub built as a **simulated** offline-first protocol (`neos/hub.py`, `/hub.html`): queue, sequence numbers, explicit outcomes, SMS parsing, reliability-discounted commitments, participation points. No phone app, SMS gateway or network exists. State is keyed by a browser-generated session id (a label, not a login) and saved to JSON (`data/hub/`, `NEOS_HUB_DIR`); visitors no longer share one hub. Still not built: authentication, identity, anti-fraud, settlement, concurrency beyond a single server process. It is an offline-pledging demo, not the shared-battery energy hub.
3. Test count: single source `test_status.json`.
4. DISCOM: the UI now states 3/12 kept, mean and worst delivery beside every promise, and a "plan on" figure (promise x worst held-out ratio). The reliability itself was **not** improved; it is still weak.
5. Islanding: unchanged and still simulation only. Dynamics, protection, black start and resynchronisation need an EMT/dynamic model and hardware tests, which this repo does not contain.

### Review follow-up (round 2)
- `pytest` and `httpx` added to `requirements.txt` (run_tests.py and the suite need them); regenerating `test_status.json` from a fresh install works.
- README rewritten against the regenerated `results.json`: the headline table, held-out ranges, solar utilisation, EV misses and infeasible-step claims were stale and are corrected (heat now has 0 infeasible steps; the spike has 12).
- Hub: per-session state with JSON persistence; wording says it is not the energy hub.
- Rural: the held-out study still covers the default village only; the page says so for every custom setting.
