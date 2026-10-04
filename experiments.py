"""
experiments.py -- reproduces every number in the demo.

Design (what makes this evidence rather than a demo):
  * PAIRED:  every arm sees the identical realised scenario (weather, loads, EV
             behaviour) per replicate, so differences are due to control alone.
  * MULTI-SEED: R replicates per scenario; we report mean, SD and a 95 % t-interval
             on the paired difference, never one favourable run.
  * HELD-OUT: the controller/forecaster are developed on neighbourhood seed 7 and
             evaluated again on populations (seeds 21, 22) never used in
             development. Every non-'normal' regime is also out-of-distribution
             for the forecaster, which is trained on normal days only.
  * ARMS:    uncoordinated | rule_tou (transparent heuristic) | coordinated (LP-MPC).
             A future MARL policy must beat the last two with the SAME safety layer.
  * HONEST METRICS: constraint status per step, safety interventions, solver
             latency, energy-accounting residuals, avoidable vs unavoidable
             override shortfall.

    python experiments.py                 # full study (about 10 min on 1 core)
    python experiments.py --quick         # 2 replicates, dev population only
"""
import argparse, json, time, sys
import numpy as np
from scipy import stats

from neos import service
from neos.config import NeighbourhoodConfig
from neos.seeds import provenance, stable_seed
from neos.runner import run_scenario, WSTART, WLEN
from neos.calibration import calibration_study
from neos.grid import ReferenceGrid, make_grid, have_opendss

ARMS = ('uncoordinated', 'rule_tou', 'coordinated')
SCEN_ORDER = ['normal', 'cloud', 'heat', 'evsurge', 'shortage', 'battfail', 'spike']
KEEP_SERIES = ('tx_loading', 'vmin', 'vmax', 'net', 'ev_charge', 'batt', 'curt', 'defer',
               'pv_avail', 'line_max', 'interv')
DEV_SEED, HELD_OUT_SEEDS = 7, (21, 22)

LIMITATIONS = [
    "All demand, PV, EV and weather inputs are SYNTHETIC. Absolute kW/INR figures are not predictions for a real feeder.",
    "Weather forecasts are synthetic issue-time forecasts with an assumed skill-vs-lead curve (nwp.py); no real NWP was used.",
    "Forecast skill on demand is optimistic: synthetic demand has far less idiosyncratic noise than metered homes.",
    "The PV ensemble under-covers persistent-haze days: the forecaster does not anticipate multi-hour regime persistence beyond its skill horizon.",
    "A P10 departure commitment accepts tail risk: the optimiser misses 1-2 % of EV requirements (uncoordinated: 0 %). The cost of coordination is measured here, not hidden.",
    "The optimiser uses LESS of the available solar than the uncoordinated baseline (about 92 % vs 95 % utilisation): the chance constraints are conservative and the PV ensemble is over-dispersed on clear days (about 99 % coverage for a nominal 80 % band). Known weakness; not tuned away.",
    "The 'rule_tou' arm looks safe at the transformer only because the safety layer clips it; compare its safety-intervention count, not just its peak.",
    "Safety verification is a simulated AC power flow on a nominal model. Stale telemetry, actuator faults, model error beyond the calibrated margin and real protection relays are out of scope.",
    "The twin-calibration study identifies one parameter (LV impedance) on a plant of the same structure; structural mismatch is not tested here.",
    "MARL is NOT implemented. The policy interface and two benchmark arms exist; any RL result must be reported as a delta against them.",
    "No hardware-in-the-loop. Simulation endpoints cannot control equipment.",
    "The reference grid solver is a backward/forward sweep, cross-checked against OpenDSS only where validate_backends.py has been run.",
]


def ci95(x):
    x = np.asarray(x, float)
    if len(x) < 2:
        return [float(x.mean()), float('nan'), float('nan')]
    se = x.std(ddof=1) / np.sqrt(len(x))
    h = stats.t.ppf(0.975, len(x) - 1) * se
    return [float(x.mean()), float(x.mean() - h), float(x.mean() + h)]


METRICS = [  # (key, label, lower_is_better)
    ('peak_tx_loading', 'Peak transformer loading (%)', True),
    ('hours_over_100', 'Hours above rating', True),
    ('min_voltage', 'Lowest voltage (pu)', False),
    ('total_cost_inr', 'Bill (INR)', True),
    ('renewable_utilisation', 'Renewable utilisation', False),
    ('ev_satisfaction', 'EV owners satisfied', False),
    ('steps_verified_pct', 'Steps fully AC-verified (%)', False),
    ('safety_interventions', 'Safety interventions', True),
    ('losses_kwh', 'Network losses (kWh)', True),
]


def run_cell(cfg, regime, rep, arms=ARMS):
    out = {}
    for arm in arms:
        m, series, fc = service.run(cfg, regime, arm, rep=rep, use_ledger=False)
        out[arm] = (m, series, fc)
    return out


def summarize(cells):
    """cells: list over replicates of {arm: (m, series, fc)} -> stats dict."""
    S = {}
    for key, label, lower in METRICS:
        S[key] = dict(label=label, lower_is_better=lower)
        for arm in ARMS:
            v = [c[arm][0][key] for c in cells]
            S[key][arm] = dict(mean=float(np.mean(v)), sd=float(np.std(v, ddof=1)) if len(v) > 1 else 0.0)
        for ref in ('uncoordinated', 'rule_tou'):
            d = [c['coordinated'][0][key] - c[ref][0][key] for c in cells]
            m_, lo, hi = ci95(d)
            S[key][f'delta_vs_{ref}'] = dict(mean=m_, ci95=[lo, hi], n=len(d))
    S['ev_missed_total'] = {a: int(sum(len(c[a][0]['ev_missed']) for c in cells)) for a in ARMS}
    S['ev_sessions_total'] = int(sum(cells[0][a][0]['n_ev_sessions'] for a in ('coordinated',)) * len(cells))
    S['override'] = {a: dict(
        sessions=int(sum(c[a][0]['override_sessions'] for c in cells)),
        met=int(sum(c[a][0]['override_met'] for c in cells)),
        unavoidable_kwh=float(sum(c[a][0]['override_unavoidable_kwh'] for c in cells)),
        avoidable_kwh=float(sum(c[a][0]['override_avoidable_kwh'] for c in cells))) for a in ARMS}
    S['infeasible_steps'] = {a: int(sum(c[a][0]['constraint_status_steps']['infeasible'] for c in cells)) for a in ARMS}
    S['solver_failed_steps'] = {a: int(sum(c[a][0]['constraint_status_steps']['solver_failed'] for c in cells)) for a in ARMS}
    S['energy_clip_events'] = int(sum(c[a][0]['energy_clip_events'] for c in cells for a in ARMS))
    S['max_energy_residual_kwh'] = float(max(max(c[a][0]['energy_audit'].values()) for c in cells for a in ARMS))
    mp = [c['coordinated'][0].get('mpc') for c in cells if c['coordinated'][0].get('mpc')]
    if mp:
        S['mpc_latency_ms'] = dict(mean=float(np.mean([x['mean_ms'] for x in mp])),
                                   p95=float(np.max([x['p95_ms'] for x in mp])),
                                   max=float(np.max([x['max_ms'] for x in mp])),
                                   fallback_steps=int(sum(x['fallback_steps'] for x in mp)))
    return S


def override_study(cfg):
    """Targeted tests of explicit-requirement semantics."""
    world, scen, fc = service.prepare(cfg, 'normal', 0)
    nb = world['nb']
    sess = {e.id: scen['ev'][e.id][0] for e in nb.evs}
    order = sorted(sess, key=lambda i: sess[i]['arr'])
    clean = [i for i in order if sess[i]['override'] is None]
    for s in sess.values():
        s['override'] = None
    cases = {}

    def go(name, extra, desc):
        with world['grid'].lock:
            m, L = run_scenario(nb, world['grid'], scen, fc, mode='coordinated', seed=5,
                                extra_overrides=extra)
        cases[name] = dict(description=desc, ledger=m['override_ledger'],
                           met=m['override_met'], sessions=m['override_sessions'],
                           shortfall_kwh=m['override_shortfall_kwh'],
                           unavoidable_kwh=m['override_unavoidable_kwh'],
                           avoidable_kwh=m['override_avoidable_kwh'],
                           peak_tx_loading=m['peak_tx_loading'],
                           steps_verified_pct=m['steps_verified_pct'],
                           ev_satisfaction=m['ev_satisfaction'])

    need = lambda i: (nb.evs[i].req_soc - sess[i]['soc_arr']) * nb.evs[i].capacity_kwh
    a = max(clean[:30], key=need)              # an EV that genuinely needs energy
    assert need(a) > 5.0, 'override study needs an EV with a real charging need'
    arr = sess[a]['arr']
    go('early_departure_ample_notice', {a: dict(notice=arr + 6, depart=arr + 6 + 24, kind='depart_early')},
       'User announces departure 6 h ahead of an early leave: feasible, target should be met.')
    go('need_now_no_notice', {a: dict(notice=arr + 4, depart=arr + 4, kind='need_now')},
       'Unannounced disconnection (notice = departure). Controller can only expose the SOC it already banked.')
    go('need_now_insufficient_time', {a: dict(notice=arr + 4, depart=arr + 5, kind='need_now')},
       'One step of notice: physics forbids the target; the ledger must say so, not promise it.')
    ids = [i for i in clean[:40] if i != a][:12]
    go('competing_requirements', {i: dict(notice=sess[i]['arr'] + 3, depart=sess[i]['arr'] + 3 + 3, kind='need_now')
                                  for i in ids},
       '12 users declare need-now at once on top of the evening peak: electrical protection keeps precedence.')
    return cases


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--quick', action='store_true')
    ap.add_argument('--reps', type=int, default=5)
    ap.add_argument('--out', default='results.json')
    a = ap.parse_args()
    reps = 2 if a.quick else a.reps
    t0 = time.time()
    dev = NeighbourhoodConfig(seed=DEV_SEED)
    world = service.get_world(dev)
    nb = world['nb']
    print(f"backend={world['grid'].backend}  {len(nb.homes)} homes {len(nb.evs)} EVs "
          f"{len(nb.batteries)} batteries {nb.pv_kwp_total:.0f} kWp", flush=True)

    bench = service.forecast_benchmark(dev)
    pvcal = service.pv_ensemble_calibration(dev, reps=3)
    print('forecast benchmark done', flush=True)

    scenarios, study = {}, {}
    for regime in SCEN_ORDER:
        cells = [run_cell(dev, regime, r) for r in range(reps)]
        study[regime] = summarize(cells)
        c0 = cells[0]
        title, desc = service.SCENARIOS[regime]
        scenarios[regime] = dict(
            title=title, desc=desc,
            uncoordinated=c0['uncoordinated'][0], coordinated=c0['coordinated'][0],
            rule_tou=c0['rule_tou'][0],
            uncoordinated_series={k: c0['uncoordinated'][1][k] for k in KEEP_SERIES},
            coordinated_series={k: c0['coordinated'][1][k] for k in KEEP_SERIES},
            forecast=service.flat_fc(c0['coordinated'][2]))
        s = study[regime]['peak_tx_loading']
        print(f"{regime:9s} peak: un {s['uncoordinated']['mean']:6.1f}  rule {s['rule_tou']['mean']:6.1f}  "
              f"MPC {s['coordinated']['mean']:6.1f}  d_vs_un {s['delta_vs_uncoordinated']['mean']:+.1f} "
              f"[{s['delta_vs_uncoordinated']['ci95'][0]:+.1f},{s['delta_vs_uncoordinated']['ci95'][1]:+.1f}]", flush=True)

    held = {}
    for hs in ([] if a.quick else HELD_OUT_SEEDS):
        cfg = NeighbourhoodConfig(seed=hs)
        held[str(hs)] = {}
        for regime in SCEN_ORDER:
            cells = [run_cell(cfg, regime, r) for r in range(3)]
            held[str(hs)][regime] = summarize(cells)
        p = [held[str(hs)][r]['peak_tx_loading'] for r in SCEN_ORDER]
        print(f"held-out nbhd {hs}: mean peak un {np.mean([x['uncoordinated']['mean'] for x in p]):.1f} -> "
              f"MPC {np.mean([x['coordinated']['mean'] for x in p]):.1f}", flush=True)

    cal = calibration_study(nb, lambda: ReferenceGrid(nb))
    ovr = override_study(dev)
    print('calibration + override studies done', flush=True)

    payload = dict(
        meta=dict(homes=len(nb.homes), evs=len(nb.evs), batteries=len(nb.batteries),
                  commercial=len(nb.commercials), pv_kwp=round(nb.pv_kwp_total, 1),
                  tx_kva=nb.tx_kva, nodes=len(nb.nodes), lv_loads=len(nb.nodes) * 3,
                  grid_backend=world['grid'].backend, replicates=reps,
                  runtime_s=round(time.time() - t0, 1)),
        forecast_benchmark=bench, pv_calibration=pvcal, scenarios=scenarios, study=study,
        held_out=held, calibration=cal, override_study=ovr, limitations=LIMITATIONS,
        provenance=provenance(dev.to_dict(), dict(reps=reps, dev_seed=DEV_SEED,
                                                  held_out_seeds=list(HELD_OUT_SEEDS))))
    with open(a.out, 'w') as f:
        json.dump(payload, f, default=float)
    print(f"\ndone in {time.time() - t0:.0f}s -> {a.out}")


if __name__ == '__main__':
    main()
