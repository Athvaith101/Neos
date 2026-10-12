# SPEC-TRACEABILITY — status against the master specification

Legend: **VERIFIED** = implemented and tested against OpenDSS (and the NumPy reference);
**SIMULATED** = implemented, but only in simulation with synthetic data; **NOT BUILT**.
Source: `NEOS_Master_Architecture_Prototype_Validation__1_.md`, Tiers 1–2 (the only spec text available).
The "other zip" referred to in the brief was not supplied, so these features were rebuilt from the spec and the v2.0 audit; they are not a copy of any earlier implementation.

| Item | Status | Where | Evidence / caveat |
|---|---|---|---|
| Unbalanced 3-phase power flow | VERIFIED | `neos/grid.py` | `validate_backends.py`, OpenDSS vs reference |
| Per-phase transformer loading + imbalance | VERIFIED | `neos/grid.py`, `neos/phase.py` | `validate_platform.py`; tests in `tests/test_platform.py`. Imbalance is defined only above 5% of per-phase rating. NEOS does not reduce imbalance (p95 ≈ 0.4 either way) |
| Safety layer (VERIFIED/INFEASIBLE/SOLVER_FAILED) | VERIFIED | `neos/runner.py` | tests |
| LP-MPC with chance constraints | VERIFIED | `neos/control.py` | tests, `results.json` |
| Flexibility Envelope | SIMULATED | `neos/controllers.py` | Estimate only; the envelope overstated what was deliverable, so a read-only probe (`cut_capability`) is used for DISCOM decisions |
| Decision Trace | VERIFIED | `neos/decision_trace.py` | tests |
| DISCOM request emulation (ACCEPT/MODIFY/DEFER/REJECT) | SIMULATED | `neos/discom.py` | Measured baseline-vs-request protocol. **Held-out: 3 of 12 promises kept; mean delivery 77% of promise, minimum 54%.** The margin calibrated on dev seeds did not transfer. Not a guarantee. 100 kW requests were rejected (capability ≈ 45 kW) |
| Rural kit: 5 priority tiers, village feeders | SIMULATED | `neos/rural.py` | 4 scenarios × 2 arms × 8 villages on OpenDSS; synthetic data |
| Island mode | SIMULATED | `neos/rural.py` | Grid-forming source behind the same LV network, voltage/ampacity/inverter verified per step; energy audit ≈ 1e-13 kWh, no clipping. **Not modelled:** dynamics, black start, protection, resynchronisation |
| Outage tier protection | SIMULATED | `neos/rural.py` | Poor-sun 12 h outage: tier-blind loses the clinic after 4.5 h; NEOS keeps tiers 1–2 and sheds all shops |
| Community hub, offline participation | SIMULATED | `neos/hub.py` | Protocol logic tested (queue, idempotency, expiry, SMS parse, reliability discount). Per-session JSON persistence; no client app, gateway, authentication or settlement. Not the shared-battery energy hub |
| Editable rural village | VERIFIED | `neos/rural.py` `village()` | Default reproduces archived results; larger battery never worse for the clinic; held-out study covers default only |
| DISCOM settlement / baseline regulation | NOT BUILT | — | |
| Hardware-in-the-loop, field pilots | NOT BUILT | — | |
| Multi-agent RL | NOT BUILT | — | |
| Real feeder / meter data | NOT BUILT | — | All data synthetic |
| Stage-2 stress lab (`flex_req`, `flex_del`, `batt_reserve_viol`) | NOT BUILT | `neos/runner.py` | Fields exist, unpopulated |
| Structural digital-twin calibration | NOT BUILT | — | |

## Bugs found and fixed during verification (disclosed)
1. **OpenDSS `set_impedance_scale` silently ignored** (lines not re-bound). Found by cross-validating rural voltages (0.013 pu discrepancy). Affected OpenDSS only; the urban calibration study used the reference grid. Fixed with regression tests, mutation-verified.
2. **Commercial PV injected on one phase**; now three-phase. Urban results regenerated on OpenDSS.
3. **Intervention counting** now counts steps with an intervention, plus a separate group-action count.
4. **Island logic shed load when the battery was full**; now curtails PV inside the solve.
5. **DISCOM first delivery metric compared realised with forecast quantities**; replaced by the paired baseline protocol.
