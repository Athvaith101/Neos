"""
stage_runner.py -- runs experiments.py's protocol in resumable stages.

Each invocation does ONE unit of work (one scenario's replicates, one
held-out population, or the calibration/override studies) and appends its
result to a progress file, so a hard wall-clock limit on any single process
cannot lose completed work. Usage:

    python stage_runner.py scenario normal
    python stage_runner.py scenario cloud
    ...
    python stage_runner.py heldout 21
    python stage_runner.py heldout 22
    python stage_runner.py extras
    python stage_runner.py assemble results.json
"""
import json, sys, time, os
import numpy as np
from neos import service
from neos.config import NeighbourhoodConfig
from neos.seeds import provenance
from neos.grid import ReferenceGrid
import experiments as E

PROGRESS = 'stage_progress.json'


def load():
    if os.path.exists(PROGRESS):
        return json.load(open(PROGRESS))
    return dict(scenarios={}, study={}, held_out={}, extras=None, bench=None,
               pvcal=None, meta=None)


def save(d):
    json.dump(d, open(PROGRESS, 'w'), default=float)


def do_scenario(regime, reps=5):
    d = load()
    t0 = time.time()
    dev = NeighbourhoodConfig(seed=E.DEV_SEED)
    if d['meta'] is None:
        world = service.get_world(dev)
        nb = world['nb']
        d['meta'] = dict(homes=len(nb.homes), evs=len(nb.evs), batteries=len(nb.batteries),
                         commercial=len(nb.commercials), pv_kwp=round(nb.pv_kwp_total, 1),
                         tx_kva=nb.tx_kva, nodes=len(nb.nodes), lv_loads=len(nb.nodes) * 3,
                         grid_backend=world['grid'].backend, replicates=reps)
        print('backend:', world['grid'].backend, flush=True)
    if d['bench'] is None:
        d['bench'] = service.forecast_benchmark(dev)
        print('forecast benchmark done', flush=True)
    if d['pvcal'] is None:
        d['pvcal'] = service.pv_ensemble_calibration(dev, reps=3)
        print('pv calibration done', flush=True)

    cells = [E.run_cell(dev, regime, r) for r in range(reps)]
    d['study'][regime] = E.summarize(cells)
    c0 = cells[0]
    title, desc = service.SCENARIOS[regime]
    d['scenarios'][regime] = dict(
        title=title, desc=desc,
        uncoordinated=c0['uncoordinated'][0], coordinated=c0['coordinated'][0],
        rule_tou=c0['rule_tou'][0],
        uncoordinated_series={k: c0['uncoordinated'][1][k] for k in E.KEEP_SERIES},
        coordinated_series={k: c0['coordinated'][1][k] for k in E.KEEP_SERIES},
        forecast=service.flat_fc(c0['coordinated'][2]))
    s = d['study'][regime]['peak_tx_loading']
    print(f"{regime:9s} peak: un {s['uncoordinated']['mean']:6.1f}  "
         f"rule {s['rule_tou']['mean']:6.1f}  MPC {s['coordinated']['mean']:6.1f}  "
         f"d_vs_un {s['delta_vs_uncoordinated']['mean']:+.1f} "
         f"[{s['delta_vs_uncoordinated']['ci95'][0]:+.1f},{s['delta_vs_uncoordinated']['ci95'][1]:+.1f}]  "
         f"({time.time()-t0:.0f}s)", flush=True)
    save(d)


def do_heldout(seed, reps=3):
    d = load()
    cfg = NeighbourhoodConfig(seed=seed)
    t0 = time.time()
    d['held_out'].setdefault(str(seed), {})
    for regime in E.SCEN_ORDER:
        if regime in d['held_out'][str(seed)]:
            continue
        cells = [E.run_cell(cfg, regime, r) for r in range(reps)]
        d['held_out'][str(seed)][regime] = E.summarize(cells)
        save(d)
    p = [d['held_out'][str(seed)][r]['peak_tx_loading'] for r in E.SCEN_ORDER]
    print(f"held-out nbhd {seed}: mean peak un "
         f"{np.mean([x['uncoordinated']['mean'] for x in p]):.1f} -> "
         f"MPC {np.mean([x['coordinated']['mean'] for x in p]):.1f}  "
         f"({time.time()-t0:.0f}s)", flush=True)


def do_extras():
    d = load()
    dev = NeighbourhoodConfig(seed=E.DEV_SEED)
    world = service.get_world(dev)
    nb = world['nb']
    t0 = time.time()
    cal = E.calibration_study(nb, lambda: ReferenceGrid(nb))
    ovr = E.override_study(dev)
    d['extras'] = dict(calibration=cal, override_study=ovr)
    save(d)
    print(f'calibration + override studies done ({time.time()-t0:.0f}s)', flush=True)


def do_assemble(out, reps=5):
    d = load()
    dev = NeighbourhoodConfig(seed=E.DEV_SEED)
    missing_s = [r for r in E.SCEN_ORDER if r not in d['scenarios']]
    missing_h = [s for s in E.HELD_OUT_SEEDS if str(s) not in d['held_out']]
    if missing_s or missing_h or d['extras'] is None:
        print('NOT READY -- missing scenarios:', missing_s, 'missing held-out:', missing_h,
             'extras done:', d['extras'] is not None)
        sys.exit(1)
    payload = dict(
        meta=dict(**d['meta'], runtime_s=None),
        forecast_benchmark=d['bench'], pv_calibration=d['pvcal'],
        scenarios=d['scenarios'], study=d['study'], held_out=d['held_out'],
        calibration=d['extras']['calibration'], override_study=d['extras']['override_study'],
        limitations=E.LIMITATIONS,
        provenance=provenance(dev.to_dict(), dict(reps=reps, dev_seed=E.DEV_SEED,
                                                  held_out_seeds=list(E.HELD_OUT_SEEDS))))
    json.dump(payload, open(out, 'w'), default=float)
    print(f'ASSEMBLED -> {out}')


if __name__ == '__main__':
    cmd = sys.argv[1]
    if cmd == 'scenario':
        do_scenario(sys.argv[2])
    elif cmd == 'heldout':
        do_heldout(int(sys.argv[2]))
    elif cmd == 'extras':
        do_extras()
    elif cmd == 'assemble':
        do_assemble(sys.argv[2])
    else:
        raise SystemExit(f'unknown command {cmd}')
