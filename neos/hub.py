"""
hub.py -- Community Energy Hub / low-income resilience simulator (15-min, one year).

ALL parameters below are SIMULATION ASSUMPTIONS, not deployment specifications.
Demand and outage traces are synthetic and seeded; the historical outage model
behind the old numbers is not available, so agreement with those numbers is a
regression check, not validation.

Arms (identical demand, outage trace, start SOC, device availability):
    no_hub        grid only; outage = no supply
    battery_only  hub battery: peak shaving above a protected reserve; serves the
                  essential section in outages (grid-forming converter, CONCEPT)
    battery_flex  as battery_only + (a) voluntary evening load shifting
                  (noticeboard/SMS participation) that reduces battery discharge,
                  (b) tiered service in outages: below a SOC threshold only tier-1
                  (critical lighting/fan/phone) is served, to stretch endurance.

Metrics follow the spec definitions. Hours "without essential supply" count outage
hours where the essential demand is not FULLY met (so tier-1-only hours count as
unmet; critical-tier hours are reported separately). Customer-hours avoided =
connections x restored fully-met outage hours.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict, replace
import hashlib, json
import numpy as np
from .reserve import hours_remaining

DT = 0.25
STEPS_YEAR = 365 * 96


@dataclass(frozen=True)
class HubConfig:
    n_homes: int = 150
    n_shops: int = 10
    tx_kva: float = 100.0
    batt_kwh: float = 100.0
    conv_kw: float = 40.0
    home_limiter_kw: float = 0.200
    shop_limiter_kw: float = 0.500
    import_cap_kw: float = 95.0
    soc_min: float = 0.15
    soc_max: float = 0.95
    soh: float = 0.95
    eta_batt: float = 0.98            # one-way cell efficiency
    eta_conv: float = 0.97            # one-way converter efficiency
    aux_kw: float = 0.30
    hot_derate: float = 0.90          # converter derating in hot months (Apr-May)
    derate_override: float = 1.0      # stress: extra converter power derating
    reserve_soc: float = 0.50         # protected SOC fraction for outages (fixed policy)
    shave_kw: float = 62.0            # peak-shaving import target
    dr_cut: float = 0.05              # voluntary evening shift (flex arm)
    tier1_frac: float = 0.60          # critical share of essential demand
    degrade_soc: float = 0.30         # flex arm drops to tier-1 below this SOC
    outages_per_year: float = 55.0
    outage_mean_h: float = 3.2
    start_soc: float = 0.90

    @property
    def connections(self):
        return self.n_homes + self.n_shops

    def config_hash(self):
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()[:16]


HIST = dict(hours_no_hub=174.0, hours_hub=24.0, ens_no_hub=3527.0, ens_hub=430.0,
            served=0.878, ridden=0.862, customer_hours=24000.0,
            peak_before=78.8, peak_after=61.8)


# ----------------------------------------------------------------- traces
def make_traces(cfg: HubConfig, seed: int):
    rng = np.random.default_rng(seed)
    N = STEPS_YEAR
    t = np.arange(N)
    h = (t % 96) * DT
    month = np.minimum(11, ((t // 96) * 12) // 365)
    season = np.where((month >= 3) & (month <= 5), 1.25, np.where(month >= 10, 0.85, np.where(month <= 1, 0.85, 1.0)))
    g = lambda x, m, s: np.exp(-((x - m) / s) ** 2)
    shape_h = 0.35 + 0.30 * g(h, 7, 1.5) + 1.15 * g(h, 20, 2.0) + 0.15 * g(h, 13, 2.5)
    scale = rng.lognormal(np.log(0.26), 0.35, cfg.n_homes)[:, None]
    noise = rng.lognormal(0.0, 0.30, (cfg.n_homes, N))
    homes = scale * shape_h[None, :] * season[None, :] * noise
    open_ = ((h >= 9) & (h < 21)).astype(float)
    shape_s = 0.25 + 1.2 * open_
    sscale = rng.lognormal(np.log(0.8), 0.3, cfg.n_shops)[:, None]
    shops = sscale * shape_s[None, :] * season[None, :] * rng.lognormal(0.0, 0.25, (cfg.n_shops, N))
    D = homes.sum(0) + shops.sum(0)
    E = (np.minimum(homes, cfg.home_limiter_kw).sum(0)
         + np.minimum(shops, cfg.shop_limiter_kw).sum(0))
    # outages: Poisson count, lognormal durations, 50 % starting in the evening
    n_ev = rng.poisson(cfg.outages_per_year)
    out = np.zeros(N, bool); ev_id = -np.ones(N, int)
    sigma = 0.9; mu = np.log(cfg.outage_mean_h) - sigma ** 2 / 2
    starts = []
    for k in range(n_ev):
        day = rng.integers(0, 365)
        hr = rng.uniform(15, 23) if rng.random() < 0.5 else rng.uniform(0, 24)
        s0 = int(day * 96 + hr / DT)
        dur = int(np.clip(rng.lognormal(mu, sigma), 0.25, 24) / DT)
        if s0 + dur >= N or out[max(0, s0 - 4):s0 + dur + 4].any():
            continue
        out[s0:s0 + dur] = True; ev_id[s0:s0 + dur] = len(starts); starts.append((s0, dur))
    derate = np.where((month == 3) | (month == 4), cfg.hot_derate, 1.0) * cfg.derate_override
    return dict(D=D, E=E, out=out, ev_id=ev_id, events=starts, derate=derate, N=N)


# ------------------------------------------------------------------- arms
def simulate(cfg: HubConfig, tr, arm: str):
    N = tr['N']; D, E, out, evid, derate = tr['D'], tr['E'], tr['out'], tr['ev_id'], tr['derate']
    cap = cfg.batt_kwh * cfg.soh
    e = cfg.start_soc * cap
    lo, hi = cfg.soc_min * cap, cfg.soc_max * cap
    ec, eb = cfg.eta_conv, cfg.eta_batt
    n_ev = len(tr['events'])
    ev_full = np.ones(max(1, n_ev), bool)
    m = dict(ens=0.0, ens_critical=0.0, out_h=float(out.sum() * DT), h_unmet=0.0, h_unmet_crit=0.0,
             h_partial=0.0, ess_out=0.0, served_out=0.0, conv_loss=0.0, batt_loss=0.0, aux=0.0,
             throughput=0.0, dis_shave=0.0, cap_unserved=0.0, reserve_violations=0)
    peak_eve = {}; carry = 0.0
    imp_max = 0.0
    soc_lo = 1.0
    for t in range(N):
        d, ess = D[t], E[t]
        sod = t % 96
        conv = cfg.conv_kw * derate[t]
        if arm == 'no_hub':
            if out[t]:
                m['ens'] += ess * DT; m['ens_critical'] += cfg.tier1_frac * ess * DT
                m['h_unmet'] += DT; m['h_unmet_crit'] += DT; m['ess_out'] += ess * DT
                if evid[t] >= 0: ev_full[evid[t]] = False
            else:
                imp = d
                imp_max = max(imp_max, imp)
                if 72 <= sod < 88: peak_eve[t // 96] = max(peak_eve.get(t // 96, 0.0), imp)
            continue
        flex = arm == 'battery_flex'
        if not out[t]:
            dd = d
            if flex:
                if 72 <= sod < 88:
                    cut = cfg.dr_cut * (d - 0.0) ; dd = d - cut; carry += cut
                elif 88 <= sod < 92 and carry > 0:
                    add = carry / ((92 - sod) * 1.0); dd = d + add; carry -= add
                elif sod == 0:
                    carry = 0.0
            imp = dd + cfg.aux_kw
            m['aux'] += cfg.aux_kw * DT
            P = 0.0
            if imp > cfg.shave_kw and e > cfg.reserve_soc * cap:
                avail = (e - cfg.reserve_soc * cap) * ec * eb / DT
                P = min(imp - cfg.shave_kw, conv, avail)          # discharge, AC kW
                dc_bus = P / ec; cells = dc_bus / eb
                e -= cells * DT; imp -= P
                m['conv_loss'] += (dc_bus - P) * DT; m['batt_loss'] += (cells - dc_bus) * DT
                m['throughput'] += cells * DT; m['dis_shave'] += P * DT
            elif imp > cfg.import_cap_kw and e > lo:
                avail = (e - lo) * ec * eb / DT
                P = min(imp - cfg.import_cap_kw, conv, avail)
                dc_bus = P / ec; cells = dc_bus / eb
                e -= cells * DT; imp -= P
                m['conv_loss'] += (dc_bus - P) * DT; m['batt_loss'] += (cells - dc_bus) * DT
                m['throughput'] += cells * DT
            elif imp < cfg.shave_kw and e < hi:
                room = (hi - e) / (DT * ec * eb)
                P = min(cfg.shave_kw - imp, conv, room)           # charge, AC kW from grid
                dc_bus = P * ec; cells = dc_bus * eb
                e += cells * DT; imp += P
                m['conv_loss'] += (P - dc_bus) * DT; m['batt_loss'] += (dc_bus - cells) * DT
                m['throughput'] += cells * DT
            if imp > cfg.import_cap_kw:
                m['cap_unserved'] += (imp - cfg.import_cap_kw) * DT
            imp_max = max(imp_max, imp)
            if 72 <= sod < 88: peak_eve[t // 96] = max(peak_eve.get(t // 96, 0.0), imp)
            if e < cfg.reserve_soc * cap - 1e-9 and imp <= cfg.import_cap_kw and P == 0.0 and e > lo and False:
                m['reserve_violations'] += 1
        else:
            soc_use = (e - lo) / max(hi - lo, 1e-9)
            tier1_only = flex and (e / cap) < cfg.degrade_soc
            target = cfg.tier1_frac * ess if tier1_only else ess
            aux_dc = cfg.aux_kw * DT
            avail_ac = max(0.0, (e - lo - aux_dc / eb) * ec * eb / DT)
            P = min(target, conv, avail_ac)
            dc_bus = P / ec; cells = dc_bus / eb
            e -= cells * DT + aux_dc / eb
            m['aux'] += aux_dc
            m['conv_loss'] += (dc_bus - P) * DT; m['batt_loss'] += (cells - dc_bus) * DT
            m['throughput'] += cells * DT
            m['ess_out'] += ess * DT; m['served_out'] += P * DT
            m['ens'] += (ess - P) * DT
            crit_need = cfg.tier1_frac * ess
            if P < crit_need - 1e-9:
                m['ens_critical'] += (crit_need - P) * DT; m['h_unmet_crit'] += DT
            if P < ess - 1e-9:
                m['h_unmet'] += DT
                if evid[t] >= 0: ev_full[evid[t]] = False
                if P >= crit_need - 1e-9: m['h_partial'] += DT
        soc_lo = min(soc_lo, e / cap)
        assert -1e-6 <= e <= cap + 1e-6, 'SOC out of physical range'
    m['terminal_soc'] = e / cap
    m['min_soc'] = soc_lo
    m['events'] = n_ev
    m['events_ridden'] = int(ev_full[:n_ev].sum()) if n_ev else 0
    m['evening_peak_mean_kw'] = float(np.mean(list(peak_eve.values()))) if peak_eve else 0.0
    m['import_peak_kw'] = float(imp_max)
    m['outage_demand_served'] = m['served_out'] / m['ess_out'] if m['ess_out'] > 0 else 1.0
    m['events_ridden_frac'] = m['events_ridden'] / n_ev if n_ev else 1.0
    m['customer_hours_avoided'] = cfg.connections * (m['out_h'] - m['h_unmet'])
    m['hours_without_essential_supply'] = m['h_unmet']
    m['hours_without_critical_supply'] = m['h_unmet_crit']
    return m


ARMS = ('no_hub', 'battery_only', 'battery_flex')


def run_seed(cfg, seed):
    tr = make_traces(cfg, seed)
    return {a: simulate(cfg, tr, a) for a in ARMS}, tr


def study(cfg=HubConfig(), seeds=range(10)):
    rows = []
    for s in seeds:
        r, _ = run_seed(cfg, s)
        rows.append(r)
    keys = ['hours_without_essential_supply', 'hours_without_critical_supply', 'ens', 'ens_critical',
            'outage_demand_served', 'events_ridden_frac', 'customer_hours_avoided',
            'evening_peak_mean_kw', 'import_peak_kw', 'terminal_soc', 'conv_loss', 'batt_loss',
            'cap_unserved', 'dis_shave', 'out_h', 'events']
    agg = {}
    for a in ARMS:
        agg[a] = {}
        for k in keys:
            v = np.array([r[a][k] for r in rows], float)
            agg[a][k] = dict(mean=float(v.mean()), sd=float(v.std(ddof=1)) if len(v) > 1 else 0.0,
                             min=float(v.min()), max=float(v.max()))
    tsoc = {a: agg[a]['terminal_soc']['mean'] for a in ARMS}
    fair = max(tsoc['battery_only'], tsoc['battery_flex']) - min(tsoc['battery_only'], tsoc['battery_flex']) <= 0.05
    return dict(config=asdict(cfg), config_hash=cfg.config_hash(), seeds=list(seeds), arms=agg,
                per_seed=[{a: {k: r[a][k] for k in keys} for a in ARMS} for r in rows],
                terminal_soc_comparable=bool(fair), terminal_soc_mean=tsoc,
                endurance_indicator_h=hours_remaining(cfg.soc_max - cfg.soc_min, cfg.soh,
                                                       cfg.eta_conv * cfg.eta_batt, cfg.batt_kwh,
                                                       agg['no_hub']['ens']['mean'] / max(agg['no_hub']['out_h']['mean'], 1e-9)))


def regression_check(st, rel_tol=0.15):
    """Compare current outputs with the HISTORICAL numbers. Per the spec: if the
    model does not reproduce them within tolerance the NEW values become current
    and the old ones leave the active claim set. Both outcomes are reported."""
    A = st['arms']
    cur = dict(hours_no_hub=A['no_hub']['hours_without_essential_supply']['mean'],
               hours_hub=A['battery_only']['hours_without_essential_supply']['mean'],
               ens_no_hub=A['no_hub']['ens']['mean'], ens_hub=A['battery_only']['ens']['mean'],
               served=A['battery_only']['outage_demand_served']['mean'],
               ridden=A['battery_only']['events_ridden_frac']['mean'],
               customer_hours=A['battery_only']['customer_hours_avoided']['mean'],
               peak_before=A['no_hub']['evening_peak_mean_kw']['mean'],
               peak_after=A['battery_only']['evening_peak_mean_kw']['mean'])
    out = {}
    for k, hv in HIST.items():
        rel = (cur[k] - hv) / hv
        out[k] = dict(historical=hv, current=cur[k], rel_diff=rel, within_tolerance=abs(rel) <= rel_tol)
    return dict(tolerance=rel_tol, metrics=out,
                all_within=all(v['within_tolerance'] for v in out.values()),
                note='Demand, outage and arm parameters are assumptions made in this repo; the historical model is not '
                     'available. A match would not be validation, and a mismatch means the current value replaces the old.')
