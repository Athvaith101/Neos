"""
calibration.py -- Physical digital-twin calibration.

This replaces "the twin learns" by something that can be checked. The plant (a
stand-in for the real feeder) has a feeder-impedance multiplier the twin does not
know. We:

  1. collect noisy telemetry (min/max LV voltage, transformer kW) at varied
     operating points from the PLANT,
  2. identify the twin's impedance multiplier by nonlinear least squares and
     bootstrap a confidence interval,
  3. measure the VERIFICATION GAP before/after: of the dispatches the twin
     certifies as safe, what fraction does the plant violate?
  4. derive a voltage safety margin from the residual distribution, so the
     remaining model error is priced into the constraint instead of ignored.

It is deliberately separate from the heuristic headroom adaptation inside
MPCController.feedback, which is NOT parameter estimation.
"""
from __future__ import annotations
import numpy as np
from scipy.optimize import minimize_scalar
from .grid import constraint_report, VLIM_LO, VLIM_HI


def random_operating_points(nb, n, rng, lo=-800.0, hi=800.0):
    """Heterogeneous injections spanning export and import stress."""
    keys = sorted({(h.node, h.phase) for h in nb.homes})
    pts = []
    for _ in range(n):
        tot = rng.uniform(lo, hi)
        w = rng.gamma(2.0, 1.0, len(keys)); w /= w.sum()
        pts.append({k: tot * wi for k, wi in zip(keys, w)})
    return pts


def measure(grid, pts, rng, noise_v=0.002, noise_p=0.5):
    rows = []
    for inj in pts:
        st = grid.solve(inj)
        rows.append((st['vmin'] + rng.normal(0, noise_v), st['vmax'] + rng.normal(0, noise_v),
                     st['tx_kw'] + rng.normal(0, noise_p)))
    return np.array(rows)


def _loss(scale, twin, pts, obs):
    twin.set_impedance_scale(scale)
    pred = np.array([[(s := twin.solve(i))['vmin'], s['vmax']] for i in pts])
    return float(np.mean((pred - obs[:, :2]) ** 2))


def fit_impedance_scale(twin, pts, obs, bounds=(0.4, 3.0)):
    r = minimize_scalar(lambda z: _loss(z, twin, pts, obs), bounds=bounds, method='bounded',
                        options=dict(xatol=1e-4))
    twin.set_impedance_scale(r.x)
    return float(r.x), float(r.fun)


def bootstrap_ci(twin, pts, obs, n_boot=30, rng=None):
    rng = rng or np.random.default_rng(0)
    est = []
    for _ in range(n_boot):
        idx = rng.integers(0, len(pts), len(pts))
        z, _ = fit_impedance_scale(twin, [pts[i] for i in idx], obs[idx])
        est.append(z)
    return float(np.percentile(est, 2.5)), float(np.percentile(est, 97.5))


def verification_gap(twin, plant, pts, margin=0.0):
    """Among points the twin calls SAFE (with `margin` pu tightening), how many
    does the plant violate?  Also returns the voltage prediction RMSE."""
    certified = unsafe = 0
    err = []
    for inj in pts:
        st = twin.solve(inj); ps = plant.solve(inj)
        rep = constraint_report(st, vlo=VLIM_LO + margin, vhi=VLIM_HI - margin)
        prep = constraint_report(ps)
        err.append(ps['vmin'] - st['vmin']); err.append(ps['vmax'] - st['vmax'])
        if rep['ok']:
            certified += 1
            unsafe += (not prep['ok'])
    err = np.array(err)
    return dict(certified=certified, plant_unsafe_among_certified=unsafe,
                false_safe_rate=unsafe / max(1, certified),
                v_rmse=float(np.sqrt(np.mean(err ** 2))), v_bias=float(err.mean()),
                v_abs_p95=float(np.percentile(np.abs(err), 95)))


def calibration_study(nb, make_twin, true_scale=1.30, n_fit=40, n_test=400, seed=0):
    rng = np.random.default_rng(seed)
    plant = make_twin(); plant.set_impedance_scale(true_scale)
    twin = make_twin()
    fit_pts = random_operating_points(nb, n_fit, rng)
    obs = measure(plant, fit_pts, rng)
    test_pts = random_operating_points(nb, n_test, np.random.default_rng(seed + 1), -700, 700)
    before = verification_gap(twin, plant, test_pts)
    z, rmse = fit_impedance_scale(twin, fit_pts, obs)
    lo, hi = bootstrap_ci(twin, fit_pts, obs, rng=rng)
    twin.set_impedance_scale(z)
    after = verification_gap(twin, plant, test_pts)
    margin = after['v_abs_p95']
    after_margin = verification_gap(twin, plant, test_pts, margin=margin)
    return dict(true_scale=true_scale, estimated_scale=z, ci95=[lo, hi],
                fit_rmse_pu=float(np.sqrt(rmse)), n_fit=n_fit, n_test=n_test,
                before=before, after=after, after_with_margin=after_margin,
                recommended_voltage_margin_pu=margin,
                note='parameter = LV feeder impedance multiplier; telemetry = noisy '
                     'Vmin/Vmax. The bootstrap resamples operating points only, not '
                     'measurement-noise realisations, so the CI understates uncertainty '
                     '(here it narrowly excludes the true value). A real deployment would '
                     'also identify load and PV models, not only network impedance.')
