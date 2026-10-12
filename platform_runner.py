"""
platform_runner.py -- resumable experiments for the platform modules (rural kit, DISCOM interface).

    python platform_runner.py rural                 # 4 scenarios x 2 arms x (5 dev + 3 held-out) worlds, OpenDSS
    python platform_runner.py discom_cal <regime>   # calibration set: dev neighbourhood (seed 7)
    python platform_runner.py discom_test <seed>    # held-out neighbourhoods (21, 22) with the calibrated margin
    python platform_runner.py assemble results_platform.json

DISCOM protocol (why the margin is MEASURED, not chosen):
  calibration : for each (regime, window) the request is oversized so the decision is MODIFY at margin 1.0;
                ratio = delivered mean / probed capability. margin = 10th percentile of ratios, clipped to [0.3, 1].
  test        : held-out populations never used to pick the margin; promise-kept = delivered mean >= 90 % of promise.
"""
import json, os, sys, time
import numpy as np
from scipy import stats
from neos import rural, service, discom
from neos.config import NeighbourhoodConfig
from neos.discom import DiscomRequest, emulate
from neos.seeds import provenance

PROGRESS = 'platform_progress.json'
DEV_SEEDS, HELD_SEEDS = (0, 1, 2, 3, 4), (10, 11, 12)
WINDOWS = (40, 44, 48)
TEST_KW = 30.0     # a realistic request for this 300-home feeder (probed capability is ~45-60 kW)
REGIMES_CAL = ('normal', 'heat', 'cloud')
REGIMES_TEST = ('normal', 'heat')
RURAL_KEYS = ['clinic_unserved_kwh', 'tier2_unserved_kwh', 'shops_unserved_kwh', 'clinic_hours_full_power_in_outage',
              'cold_chain_hours_above_8C', 'water_outage_hours', 'min_voltage', 'pv_utilisation_pct', 'min_soc',
              'mill_remaining_kwh', 'island_steps_verified_pct', 'grid_steps_verified_pct']


def load():
    return json.load(open(PROGRESS)) if os.path.exists(PROGRESS) else dict(rural=None, cal={}, test={})


def save(d):
    json.dump(d, open(PROGRESS, 'w'), default=float)


def ci(x):
    x = np.asarray(x, float)
    if len(x) < 2:
        return [float(x.mean()), None, None]
    h = stats.t.ppf(0.975, len(x) - 1) * x.std(ddof=1) / np.sqrt(len(x))
    return [float(x.mean()), float(x.mean() - h), float(x.mean() + h)]


def do_rural():
    d = load(); t0 = time.time()
    out = {}
    for pop, seeds in (('dev', DEV_SEEDS), ('held_out', HELD_SEEDS)):
        out[pop] = {}
        for sc in rural.SCENARIOS:
            cells = {a: [rural.run_rural(sc, a, seed=s, backend='opendss')[0] for s in seeds] for a in ('naive', 'neos')}
            S = {}
            for k in RURAL_KEYS:
                vn = [m[k] for m in cells['naive'] if m[k] is not None]
                vo = [m[k] for m in cells['neos'] if m[k] is not None]
                if not vn:
                    continue
                S[k] = dict(naive=dict(mean=float(np.mean(vn))), neos=dict(mean=float(np.mean(vo))),
                            delta=dict(zip(('mean', 'lo', 'hi'), ci(np.array(vo) - np.array(vn)))))
            S['mill_job_done'] = {a: int(sum(m['mill_job_done'] for m in cells[a])) for a in cells}
            S['clip_events'] = int(sum(m['energy_audit']['clip_events'] for a in cells for m in cells[a]))
            S['max_battery_residual_kwh'] = float(max(m['energy_audit']['battery_residual_kwh'] for a in cells for m in cells[a]))
            S['n'] = len(seeds); S['title'] = rural.SCENARIOS[sc]['title']; S['backend'] = cells['neos'][0]['backend']
            out[pop][sc] = S
            print(f'{pop:8s} {sc:15s} clinic unserved naive {S["clinic_unserved_kwh"]["naive"]["mean"]:.2f} '
                  f'neos {S["clinic_unserved_kwh"]["neos"]["mean"]:.2f} kWh ({time.time()-t0:.0f}s)', flush=True)
    rep_m, rep_s = rural.run_rural('cloudy_outage', 'neos', seed=0, backend='opendss')
    rep_n, rep_sn = rural.run_rural('cloudy_outage', 'naive', seed=0, backend='opendss')
    out['example'] = dict(scenario='cloudy_outage', neos=dict(metrics=rep_m, series=rep_s), naive=dict(metrics=rep_n, series=rep_sn))
    d['rural'] = out; save(d)


def _emulate(cfg, regime, start, kw, margin):
    def run_fn(cut=None, probe=None):
        m, s, _ = service.run(cfg, regime, 'coordinated', want_envelope=True, net_cut=cut, probe=probe, use_ledger=False)
        return m, s
    discom.SAFETY_MARGIN = margin
    return emulate(run_fn, DiscomRequest(start, 6, kw, notice_steps=2))


def _row(r):
    d = r['decision']; x = r['delivered']
    row = dict(action=d['action'], requested=d['requested_kw'], accepted=d['accepted_kw'], probed=r['capability_by_shift_kw'][0],
               start=d['start_step'])
    if x:
        row.update(delivered_mean=x['mean_kw'], ratio_vs_promise=x['delivery_ratio'], promise_kept=bool(x['mean_kw'] >= 0.9 * d['accepted_kw']),
                   ev_unserved_delta_kwh=r['actual']['ev_unserved_kwh'] - r['baseline']['ev_unserved_kwh'],
                   rebound_kw=r['rebound']['post_window_peak_change_kw'], overall_peak_change_kw=r['rebound']['overall_peak_change_kw'],
                   verified_pct=r['actual']['steps_verified_pct'], peak_phase=r['actual']['peak_phase_loading'])
    return row


def do_cal(regime):
    d = load(); cfg = NeighbourhoodConfig(seed=7); t0 = time.time()
    d['cal'].setdefault(regime, {})
    for w in WINDOWS:
        if str(w) in d['cal'][regime]:
            continue
        r = _emulate(cfg, regime, w, 100.0, 1.0)
        row = _row(r)
        if row.get('probed'):
            row['ratio_vs_probed'] = row['delivered_mean'] / row['probed'] if 'delivered_mean' in row else None
        d['cal'][regime][str(w)] = row; save(d)
        print(regime, w, {k: (round(v, 2) if isinstance(v, float) else v) for k, v in row.items()}, f'({time.time()-t0:.0f}s)', flush=True)


def margin_from_cal(d):
    ratios = [r['ratio_vs_probed'] for g in d['cal'].values() for r in g.values() if r.get('ratio_vs_probed') is not None]
    if not ratios:
        return None, []
    return float(np.clip(np.percentile(ratios, 10), 0.3, 1.0)), ratios


def do_test(seed):
    d = load(); m, ratios = margin_from_cal(d)
    if m is None:
        print('run discom_cal first'); sys.exit(1)
    d['margin'] = m; d['cal_ratios'] = ratios
    cfg = NeighbourhoodConfig(seed=seed); t0 = time.time()
    d['test'].setdefault(str(seed), {})
    for regime in REGIMES_TEST:
        for w in WINDOWS:
            key = f'{regime}/{w}'
            if key in d['test'][str(seed)]:
                continue
            r = _emulate(cfg, regime, w, TEST_KW, m)
            d['test'][str(seed)][key] = _row(r); save(d)
            print(seed, key, {k: (round(v, 2) if isinstance(v, float) else v) for k, v in d['test'][str(seed)][key].items()}, f'({time.time()-t0:.0f}s)', flush=True)


def do_assemble(out):
    d = load()
    if d['rural'] is None or not d['cal'] or len(d['test']) < 2:
        print('NOT READY', dict(rural=d['rural'] is not None, cal=list(d['cal']), test=list(d['test']))); sys.exit(1)
    rows = [r for s in d['test'].values() for r in s.values() if 'delivered_mean' in r]
    summ = dict(n_requests=sum(len(s) for s in d['test'].values()), n_dispatched=len(rows),
                action_counts={a: sum(1 for s in d['test'].values() for r in s.values() if r['action'] == a)
                               for a in ('ACCEPT', 'MODIFY', 'DEFER', 'REJECT')},
                promise_kept=int(sum(r['promise_kept'] for r in rows)),
                mean_ratio_vs_promise=float(np.mean([r['ratio_vs_promise'] for r in rows])) if rows else None,
                min_ratio_vs_promise=float(np.min([r['ratio_vs_promise'] for r in rows])) if rows else None,
                mean_ev_unserved_delta_kwh=float(np.mean([r['ev_unserved_delta_kwh'] for r in rows])) if rows else None,
                max_ev_unserved_delta_kwh=float(np.max([r['ev_unserved_delta_kwh'] for r in rows])) if rows else None,
                mean_rebound_kw=float(np.mean([r['rebound_kw'] for r in rows])) if rows else None,
                min_verified_pct=float(np.min([r['verified_pct'] for r in rows])) if rows else None)
    payload = dict(rural=d['rural'], discom=dict(calibration=d['cal'], margin=d['margin'], calibration_ratios=d['cal_ratios'],
                                                 held_out=d['test'], oversize_100kw_requests_seed21=d.get('oversize_rejections'), test_request_kw=TEST_KW, summary=summ,
                                                 protocol='margin = 10th percentile of delivered/probed on the calibration set (dev neighbourhood, seed 7); applied unchanged to held-out seeds 21, 22'),
                   limitations=["SYNTHETIC loads, weather and tariffs; the rural feeder is a scenario-design choice (1.5x impedance).",
                                "Island operation is a grid-forming source behind the same LV network, verified for voltage, ampacity and inverter loading by the same solver; dynamics, black start, protection coordination and synchronised reconnection are NOT modelled.",
                                "DISCOM settlement (baseline definition, metering, agreement, regulation) is NOT modelled; delivery is measured against an unrequested baseline run.",
                                "Request notice is 30 min; reliability at longer notice is lower because the baseline plan drifts as it is re-solved."],
                   provenance=provenance(NeighbourhoodConfig().to_dict(), dict(dev_seeds=list(DEV_SEEDS), held_out_seeds=list(HELD_SEEDS), windows=list(WINDOWS))))
    json.dump(payload, open(out, 'w'), default=float); print('ASSEMBLED ->', out); print(json.dumps(summ, indent=1))


if __name__ == '__main__':
    c = sys.argv[1]
    {'rural': lambda: do_rural(), 'discom_cal': lambda: do_cal(sys.argv[2]), 'discom_test': lambda: do_test(int(sys.argv[2])),
     'assemble': lambda: do_assemble(sys.argv[2])}[c]()
