# NEOS Final Candidate — Integration Status

## What this build is

This package combines the **OpenDSS-verified Stage-1 research engine** with the broader NEOS operating-system modules from the final prototype architecture.

The quantitative evidence remains anchored to the OpenDSS-generated `results.json` from the verified core. Broader modules are integrated as simulation/operator layers and are not allowed to silently replace or relabel the verified evidence.

## Integrated core

- Unbalanced OpenDSS grid verification
- Reference NumPy grid for fast iteration
- LP/MPC optimisation
- Probabilistic demand/PV forecasting
- Forecast causality and issue-time weather logic
- EV information barrier and hard requirements
- Resource-level safety projection
- Transformer, phase-voltage and line-ampacity checks
- Explicit `verified`, `infeasible`, `solver_failed`
- Exact energy accounting
- Paired multi-seed experimental protocol
- Held-out neighbourhood populations
- Flexibility Envelope
- Decision Trace
- FastAPI operator API

## Integrated operating-system modules

- Urban/suburban kit metadata
- Low-income/peri-urban Community Energy Hub simulator
- Rural portfolio and scheduling modules
- Phase-aware transformer sensing model
- Battery reserve utilities
- Generic battery/EV/pump/tank/cold-store/shiftable resource models
- Offline participation/continuity utilities
- DISCOM request + flexibility/ledger utilities
- Hash-chained verification ledger
- Islanding state machine for simulation/concept use
- Claims/evidence/provenance helpers
- Final platform manifest and `/api/platform`

## Evidence boundary

### Quantitatively verified

Use the OpenDSS-generated `results.json` for quantitative NEOS feeder claims. Do not substitute older reference-grid or pre-correctness numbers.

### Simulation modules

The hub and rural modules are software simulations. Their outputs are not field measurements and must be labelled simulated/assumed where shown.

### Concept only

Islanding is a state-machine/engineering concept in this build. It is not a protection design, certification, approval, or field-safety claim.

### Not implemented

- Physical actuator control
- Hardware-in-the-loop
- Real feeder deployment
- Certified public-feeder islanding
- Appliance identification from aggregate meter data
- Blockchain/P2P energy trading
- MARL as a production controller

## Core loop

Observe → Forecast → Protect Reserve → Build Flexibility Envelope → Optimise → Device Feasibility → OpenDSS Physics Check → Dispatch/Fallback → Measure → Verify → Explain → Repeat

## Recommended demo boundary

Present the OpenDSS-verified urban feeder results as the quantitative research evidence. Use the low-income and rural modules to demonstrate how the same NEOS brain extends to resilience and productive-energy use cases, while clearly marking those results as simulation/design evidence unless regenerated and validated.
