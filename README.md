# NEOS — Final Candidate 1.0-research

NEOS is an offline-first, uncertainty-aware neighbourhood energy operating system. One control architecture supports three deployment kits: urban/suburban flexibility coordination, low-income/peri-urban essential-service resilience, and rural productive-energy scheduling.

## Architecture

1. Observe telemetry and estimate state
2. Forecast demand/PV/EV conditions probabilistically
3. Protect an explicit resilience reserve
4. Estimate the Flexibility Envelope
5. Optimise with LP/MILP/MPC
6. Check device feasibility
7. Verify the proposed action with OpenDSS
8. Dispatch locally or fall back safely
9. Measure the actual outcome
10. Write a Decision Trace and verification record
11. Recalculate on the next cycle

## Technology

Python, NumPy, Pandas, SciPy, scikit-learn/LightGBM-compatible forecasting layer, conformal prediction, LP/MILP/MPC, OpenDSS, FastAPI, React/Plotly-compatible web layer, JSON/SQLite-style audit records, Git and Docker.

## Run

```bash
python -m unittest discover -s tests -t .
python experiments.py
python validate_backends.py
python build_static.py
python build_web.py
uvicorn server:app --port 8000
```

The `/api/platform` endpoint exposes the final candidate capability manifest.

## Important

This is a research/simulation prototype, not a hardware controller. `results.json` is the quantitative evidence source for the OpenDSS-verified core. No field deployment, actuator control, certified islanding, or DISCOM approval is claimed.
