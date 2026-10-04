# Specification traceability

Status of every item in the master specification's feature tiers (§46) and final checklist (Part II §31),
against what exists in this repository. **Built** = implemented and covered by an automated test. **Partial** = implemented
with a stated limitation. **Not built** = deliberately or otherwise absent. Nothing is marked built because the code *could*
be extended to do it.

All performance figures in the kits are **simulated**, from declared assumptions, and are not field validation.

## Tier 1 (must build)

| # | Feature | Status | Where | Limitation |
|---|---|---|---|---|
| 1 | Smart-meter / telemetry layer | Partial | `telemetry.py` (`RegisterStream`, `REGISTER_MAP`) | Simulated register stream with noise, quantisation, latency, dropouts and bad samples. No Modbus/HES/MDMS integration. |
| 2 | State estimation | Partial | `telemetry.StateEstimator` | Per-channel Kalman filter with staleness and bad-data rejection. Not a topology-aware network state estimator. |
| 3 | Probabilistic forecasting | Built (urban) | `forecast.py`, `nwp.py` | Causal, chronological hold-out, per-lead coverage. Synthetic demand and synthetic NWP. Hub has no forecast; rural reserve uses a clear-sky persistence forecast. |
| 4 | Confidence-aware reserve | Built (rural) | `reserve.py`, `reserve_experiment.py` | Result: statistically better, practically small (see README). Hub uses a fixed reserve with sensitivity runs. |
| 5 | Resource abstraction | Partial | `resources.py` | Used by the rural kit. The urban runner applies the same device rules inline rather than through these classes. |
| 6 | Deadline scheduling | Partial | `rural.py`, `control.py` | Rural pumps/mill use a transparent deadline heuristic with min-run/min-rest/stagger rules, not an optimal MILP. EV deadlines are in the LP. |
| 7 | Battery control | Built | `control.py`, `runner.py`, `rural.py`, `hub.py` | |
| 8 | EV flexibility | Built (urban) | `control.EVEnvelope`, `runner.py` | Declared requirements are hard; infeasible ones are reported. |
| 9 | Pump flexibility | Built (rural) | `resources.PumpRes`, `rural.py` | Pump power is an energy-equivalent model; motor inrush is not modelled. |
| 10 | OpenDSS digital twin | **Partial: not executed** | `grid.OpenDSSGrid` | Written against OpenDSSDirect.py ≥ 0.9; OpenDSS could not be installed where this was built, so it has **never been run**. All results use the NumPy `ReferenceGrid`. Run `validate_backends.py` first. |
| 11 | Device feasibility | Built | `resources.py`, `runner.py` | Power and energy bounds applied before any injection. |
| 12 | Physics safety projection | Built | `control.safety_project` | Re-solves the final action; `verified` / `infeasible` / `solver_failed`. Phase limits enforced where modelled. |
| 13 | Offline fallback | Partial | `controllers.CommsLossController`, `discom.py`, `offline.py` | Tested in the urban kit and the DISCOM emulation. Not exercised in the rural scheduler. |
| 14 | Flexibility envelope | Built (emulation) | `envelope.py`, `discom.py` | Demonstrated on the hub. Not wired to the urban fleet. Confidence is a stated heuristic formula, not a calibrated probability. |
| 15 | Decision trace | Built | `trace.py` | Urban (safety interventions, battery dispatch, declared requirements) and rural (pumps, mill, shedding, physics). Text is generated from live values. |
| 16 | Verification ledger | Built | `ledger.py` | Local SHA-256 hash chain; tamper, deletion and reorder detected; expired commands rejected. |
| 17 | Reproducible stress testing | Built | `experiments*.py`, `seeds.py`, `evidence.py` | SHA-256 seeds; config hash, source hash and versions in every result. |

## Tier 2 (full demo)

| # | Feature | Status | Notes |
|---|---|---|---|
| 18 | Phase-aware transformer monitoring | Built (urban) | Per-phase V/I/PF/loading and imbalance with a stated formula and light-load floor. Phase-aware shedding showed **no measurable benefit** over phase-agnostic shedding in the 40 % skew test. |
| 19 | Essential-service resilience | Built (hub, rural) | Hub does **not** reproduce the earlier hub figures (reported). |
| 20 | Safe-island simulation | Built (concept) | State machine with tested invariants. Not protection design. |
| 21 | Rural portfolio | Built | Sizes are assumptions. |
| 22 | Priority tiers | Built | Includes a fractional marginal tier; the tier-1 vs community split is an explicit charter parameter. |
| 23 | Cold-storage / thermal flexibility | Built | Hard minimum respected; used as flexibility above it. |
| 24 | Productive loads | Partial | One non-interruptible mill block and a fixed workshop. |
| 25 | Community gateway | Partial | Printed noticeboard and SMS generation only. No physical gateway. |
| 26 | DISCOM console | Built (emulation) | No DISCOM, tariff or contract is represented. |
| 27 | Communications-loss scenario | Built (urban, hub) | |
| 28 | Forecast-failure scenario | Built (urban, rural) | |

## Tier 3 (hold) and Tier 4 (do not make core)

Each of the following is not built, by design:

* Not built: second-life battery passport, solar-health monitoring, advanced forecasting models.
* Not built: any reinforcement learning (**no MARL**).
* Not built: cooperative-contract automation and Stackelberg pricing.
* Not built: hardware actuation; no equipment is commanded.
* Not built: P2P trading and blockchain trading (the ledger is a local hash chain only).
* Not built: black-box end-to-end AI; appliance identification from aggregate meter data; public-feeder islanding experiments.
* Not claimed: any guaranteed-flexibility statement. `claims.py` rejects wording that implies it.

## Validation gates (Part II §21)

| Gate | Status |
|---|---|
| 1 Software correctness | 97 automated tests; includes mutation checks of reintroduced audit bugs (see `AUDIT-STATUS.md`). |
| 2 Physical correctness | **Not met**: OpenDSS check has not been executed. The reference solver stands in and is unverified against OpenDSS here. |
| 3 Evidence correctness | `check_evidence.py` build gate (seed/config/version/staleness/kit-separation/historical-as-current/NaN) plus claim-language audit and secrets scan. |
| 4 Scenario fairness | Identical traces across arms; terminal-state caveats displayed; weak-feeder parameters disclosed as scenario design. |
| 5 Product demonstration | Console exercised against a stub DOM only; **not** rendered in a real browser. |

## Final engineering checklist

* **Done and tested:** forecast causality, hard EV requirements, impossible requests return infeasible, no unexplained NaN/Inf
  (non-finite outputs become `solver_failed`), deterministic seeding, transformer/phase/voltage limits, reserve protection,
  device feasibility, comms-loss fallback, expired-command rejection, kit selector, hub simulator, stress lab, envelope, ledger,
  decision trace, rural scheduler, phase panel, evidence tags, kit-separated evidence, no secrets in the repository (scanner).
* **Not done:** OpenDSS checks executed independently; "exposed keys rotated", "deployment environment checked" and "public demo
  tested in a private browser" are outside what code in this repository can establish and have **not** been done.
* **Pitch checklist:** the claim-language audit enforces no unqualified certified/approved/guaranteed/eliminated wording in the
  documents it scans. It cannot check what is said aloud.
