"""
service.py -- the layer between the HTTP API / experiments and the simulation.

  * bounded LRU cache of worlds (no unbounded growth)
  * one lock PER GRID (not a process-global one): independent OpenDSS contexts
    mean different neighbourhoods can be solved without disturbing each other
  * forecasts are built at an explicit ISSUE TIME from issue-time weather
  * override requests go through an auditable ledger and are never reported as
    applied until a simulation run has actually consumed them
"""
from __future__ import annotations
import threading, time, itertools
from collections import OrderedDict
import numpy as np

from .config import NeighbourhoodConfig
from .world import build_neighbourhood, T, pv_power
from .grid import make_grid
from .dataset import simulate_period
from . import forecast as F
from . import nwp
from .runner import run_scenario, WSTART, WLEN
from .seeds import scenario_seed, stable_seed

SCENARIOS = {
    'normal':   ('Normal day', 'Clear sky, typical occupancy and EV arrivals'),
    'cloud':    ('Cloud passage', '2.5 h opaque cloud front across the midday solar peak'),
    'heat':     ('Extreme heat', '42 C day, coincident air-conditioning across the feeder'),
    'evsurge':  ('EV surge', 'Holiday return: EVs plug in together at 18:00, 45 % deeper discharge'),
    'shortage': ('Renewable shortage', 'Persistent haze, solar output down ~55 % all afternoon'),
    'battfail': ('Battery outage', '60 % of neighbourhood storage offline'),
    'spike':    ('Demand spike', 'Unmodelled coincident evening demand event, +30 % for 2.5 h'),
}
N_HIST = 40
TRAIN_DAYS, CAL_DAYS, TEST_DAYS = 26, 6, (32, 39)
H_FC = WLEN + 8
MAX_WORLDS = 3

_cache: "OrderedDict" = OrderedDict()
_cache_lock = threading.Lock()


def as_cfg(cfg):
    if isinstance(cfg, NeighbourhoodConfig):
        return cfg
    return NeighbourhoodConfig(**cfg)


def get_world(cfg):
    cfg = as_cfg(cfg)
    k = cfg.key()
    with _cache_lock:
        if k in _cache:
            _cache.move_to_end(k)
            return _cache[k]
    nb = build_neighbourhood(seed=cfg.seed, n_homes=cfg.n_homes, n_ev=cfg.n_ev, n_pv=cfg.n_pv,
                             n_bess=cfg.n_bess, n_comm=cfg.n_comm, n_feeders=cfg.n_feeders,
                             seg_per_feeder=cfg.seg_per_feeder, tx_kva=cfg.tx_kva)
    grid = make_grid(nb, cfg.tx_kva, cfg.backend)
    hist = simulate_period(nb, n_days=N_HIST, seed=stable_seed('hist', cfg.seed), regime='normal')
    w = hist['weather']
    temp_clim = np.array([w['temp'][i::T].mean() for i in range(T)])
    kt_clim = float(w['kt_true'].mean())
    model = F.fit_operational(hist['agg_inflex'], w['temp'], w['ghi_cs'], w['kt_true'],
                              temp_clim, kt_clim, TRAIN_DAYS, CAL_DAYS, H_FC,
                              seed=stable_seed('gbm', cfg.seed))
    world = dict(nb=nb, grid=grid, hist=hist, cfg=cfg, model=model,
                 temp_clim=temp_clim, kt_clim=kt_clim)
    with _cache_lock:
        _cache[k] = world
        while len(_cache) > MAX_WORLDS:
            _cache.popitem(last=False)
    return world


def build_forecast(world, scen, seed):
    """Fixed-origin day-ahead forecast issued at the step BEFORE control begins."""
    hist = world['hist']; off = N_HIST * T
    O = off + WSTART - 1
    cat = lambda a, b: np.concatenate([a, b])
    wh, ws = hist['weather'], scen['weather']
    wcat = dict(kt_true=cat(wh['kt_true'], ws['kt_true']), temp=cat(wh['temp'], ws['temp']),
                ghi_cs=cat(wh['ghi_cs'], ws['ghi_cs']))
    y = cat(hist['agg_inflex'], scen['agg_inflex'])
    f = nwp.issue_forecast(wcat, O, world['kt_clim'], world['temp_clim'], seed)
    P, tt = F.forecast_window(world['model'], y[:O + 1], wcat['temp'], wcat['ghi_cs'],
                              f['kt_mean'], f['temp_fc'], O, H_FC)
    kwp = world['nb'].pv_kwp_total
    ens = np.stack([pv_power(wcat['ghi_cs'][tt] * m[tt], f['temp_fc'][tt], kwp)
                    for m in f['kt_members']])
    return dict(
        inflex=dict(p10=P[0], p50=P[1], p90=P[2]),
        pv=dict(p10=np.percentile(ens, 10, 0), p50=np.percentile(ens, 50, 0),
                p90=np.percentile(ens, 90, 0)),
        issue_step=int(O), assumptions='synthetic NWP skill: see nwp.py')


def prepare(cfg, regime, rep=0):
    world = get_world(cfg)
    seed = scenario_seed(regime, rep, world['cfg'].seed)
    scen = simulate_period(world['nb'], n_days=2, seed=seed, regime=regime)
    if regime == 'battfail':
        n = len(world['nb'].batteries)
        scen['batt_out'] = set(range(0, int(n * 0.6)))
    fc = build_forecast(world, scen, stable_seed('nwp', regime, rep, world['cfg'].seed))
    return world, scen, fc


def flat_fc(fc, n=96):
    return {f"{g}_{q}": [round(float(x), 2) for x in fc[g][q][:n]]
            for g in ('inflex', 'pv') for q in ('p10', 'p50', 'p90')}


# ---------------------------------------------------------------- override ledger
class OverrideLedger:
    """Scoped, auditable override events. A request is only 'applied' once a
    simulation run consumed it; until then it is honestly 'queued'."""

    def __init__(self):
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self.entries = []

    def submit(self, scenario, ev_id, kind, lead_steps, notice_k, rep=0, cfg=None):
        if kind not in ('need_now', 'depart_early'):
            raise ValueError("kind must be need_now|depart_early")
        if not 0 <= notice_k < WLEN - 1:
            raise ValueError("notice_k out of the control window")
        e = dict(id=next(self._ids), created=time.time(), scenario=scenario, rep=rep,
                 ev_id=int(ev_id), kind=kind, notice_k=int(notice_k),
                 lead_steps=max(0, int(lead_steps)), cfg=as_cfg(cfg or {}).to_dict(),
                 status='queued', outcome=None)
        with self._lock:
            self.entries.append(e)
        return e

    def pending(self, scenario, rep, cfg):
        c = as_cfg(cfg).to_dict()
        with self._lock:
            return [e for e in self.entries if e['status'] == 'queued'
                    and e['scenario'] == scenario and e['rep'] == rep and e['cfg'] == c]

    def resolve(self, entry, outcome, status):
        with self._lock:
            entry['status'], entry['outcome'] = status, outcome

    def list(self):
        with self._lock:
            return [dict(e) for e in self.entries]


LEDGER = OverrideLedger()


def run(cfg, regime, mode, rep=0, on_step=None, use_ledger=True,
       want_envelope=False, want_trace=False):
    world, scen, fc = prepare(cfg, regime, rep)
    nb = world['nb']
    extra, consumed = {}, []
    if use_ledger and mode != 'uncoordinated_noledger':
        for e in LEDGER.pending(regime, rep, cfg):
            sess = scen['ev'].get(e['ev_id'])
            if not sess:
                LEDGER.resolve(e, dict(error='unknown ev'), 'rejected'); continue
            arr = sess[0]['arr']
            notice = max(arr + 1, WSTART + e['notice_k'])
            extra[e['ev_id']] = dict(notice=notice, depart=notice + e['lead_steps'], kind=e['kind'])
            consumed.append(e)
    with world['grid'].lock:
        m, L = run_scenario(nb, world['grid'], scen, fc, mode=mode,
                            seed=stable_seed('run', regime, rep), extra_overrides=extra or None,
                            on_step=on_step, want_envelope=want_envelope, want_trace=want_trace)
    for e in consumed:
        mine = [x for x in m['override_ledger'] if x['ev'] == e['ev_id']]
        LEDGER.resolve(e, dict(declared=mine, mode=mode), 'applied' if mine else 'not_applied_ev_not_plugged')
    series = {k: [round(float(x), 3) for x in v] for k, v in L.items()}
    return m, series, fc


def forecast_benchmark(cfg):
    world = get_world(cfg)
    hist, w = world['hist'], world['hist']['weather']
    out = F.evaluate_benchmark(world['model'], hist['agg_inflex'], w['temp'], w['ghi_cs'],
                               w['kt_true'], world['temp_clim'], world['kt_clim'],
                               TEST_DAYS, H_FC, seed=stable_seed('bench', world['cfg'].seed))
    out['_protocol'] = dict(
        split='chronological: train days 3-26, conformal calibration days 26-32, '
              'TEST days 32-39 (never seen), 4 forecast origins/day, 26 h lead',
        weather='issue-time synthetic NWP (see nwp.py)',
        metric_note='mean_pinball is the average pinball loss over q=0.1/0.5/0.9, not CRPS',
        data='synthetic demand: absolute errors are optimistic vs real meters')
    return out


def pv_ensemble_calibration(cfg, regimes=('normal', 'cloud', 'heat', 'shortage'), reps=3):
    """Empirical P10-P90 coverage of the PV ensemble, per scenario (nominal 80 %)."""
    world = get_world(cfg)
    res = {}
    for r in regimes:
        cov = []
        for rep in range(reps):
            _, scen, fc = prepare(cfg, r, rep)
            pv = scen['agg_pv'][WSTART:WSTART + H_FC]
            d = pv > 1.0
            cov.append(float(np.mean((pv[d] >= fc['pv']['p10'][d]) & (pv[d] <= fc['pv']['p90'][d]))))
        res[r] = dict(mean=float(np.mean(cov)), per_rep=cov)
    return res


def meta(cfg):
    w = get_world(cfg)
    nb = w['nb']
    return dict(homes=len(nb.homes), evs=len(nb.evs), batteries=len(nb.batteries),
                commercial=len(nb.commercials), pv_kwp=round(nb.pv_kwp_total, 1),
                tx_kva=nb.tx_kva, nodes=len(nb.nodes), lv_loads=len(nb.nodes) * 3,
                grid_backend=w['grid'].backend)
