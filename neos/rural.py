"""
rural.py -- Rural energy portfolio, priority tiers and safe-island simulation
(master spec sections 21-25, 26C).

SYNTHETIC. Every load, tariff-free parameter and the 1.5x feeder impedance below is an
ASSUMED scenario-design choice, disclosed in results; none is measured data.

Topology (both backends, OpenDSS and the NumPy reference):

    11 kV -> 100 kVA Dyn11 -> 433 V bus -+-- feeder V : 30 nodes (1.2 km), 90 homes   (tier 5)
                                          +-- feeder C : 10 nodes (0.4 km), the COMMUNITY section
                                                 c00 community battery + grid-forming inverter (head)
                                                 c03 clinic (tier 1)          c04 community fridge, c05 lights (tier 2)
                                                 c06-c08 shops (tier 3)      c08 grain mill (tier 3, shiftable)
                                                 c09 cold storage (tier 3, thermal), pump (tier 4), 45 kWp PV (tail)
    Both feeders use 1.5x nameplate LV impedance (a weak rural line, by design).

Grid outage: the utility side opens. The community section (feeder C) is energised
ONLY by the community battery's grid-forming inverter, modelled as a stiff source
of the inverter's kVA rating behind the same LV network, so voltage, ampacity and
inverter loading are checked by the SAME solver (OpenDSS or reference). Homes on
feeder V are off in BOTH arms (they are not on the essential bus). Island dynamics,
black start, synchronised reconnection, protection coordination and neutral handling
are NOT modelled -- see the spec, section 22, and AUDIT-STATUS.md.

Two arms, same physics, same weather, same loads:
  naive  tier-blind. Local thermostat/tank/mill rules, battery self-consumption to empty, and in an
         outage supply is shared PROPORTIONALLY by everyone (the clinic is cut like a shop).
         It has the same local over-voltage PV curtailment the NEOS arm has.
  neos   priority tiers, confidence-aware reserve, deadline scheduling of productive loads,
         endurance-based service level with a fractional marginal tier.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import math
import numpy as np
from .world import T, DT, weather_ensemble, pv_power
from . import nwp
from .grid import make_grid, constraint_report, VLIM_LO, VLIM_HI
from .control import safety_project
from .seeds import stable_seed

WSTART, WLEN = 48, 96
PV_KWP = 45.0
BATT_KWH, BATT_KW, BATT_ETA = 120.0, 40.0, 0.95
SOC_MIN, SOC_MAX, SOC0 = 0.05, 1.00, 0.65
INVERTER_KVA = 50.0
TX_KVA = 100.0
Z_SCALE = 1.5
N_HOMES = 90
T1_ENDURANCE_H = 12.0          # CHARTER PARAMETER: hours of tier-1/2 service the reserve must cover
CLINIC_KW = dict(base=0.5, day=1.5, eve=1.0)
FRIDGE_KW, LIGHT_KW, SHOP_KW = 0.35, 1.2, 1.2
COLD_KW, COLD_BAND = 3.0, (2.0, 8.0)
PUMP_KW, TANK_KWH = 5.5, 40.0
MILL_KW, MILL_JOB_KWH = 7.5, 15.0
TIER_NAMES = {1: 'Critical (clinic)', 2: 'Community-critical (fridge, lights)',
              3: 'Productive (shops, cold storage, mill)', 4: 'Flexible (water pump)',
              5: 'Discretionary (homes)'}

SCENARIOS = {
    'normal': dict(regime='normal', outage=None, title='Normal day, grid up'),
    'evening_outage': dict(regime='normal', outage=(16, 64), title='12 h grid outage from 16:00'),
    'cloudy_outage': dict(regime='shortage', outage=(16, 64), title='12 h outage on a poor-sun day'),
    'long_outage': dict(regime='normal', outage=(8, 80), title='18 h outage from 14:00'),
}


class RuralNb:
    """Minimal neighbourhood: both grid backends read only `.nodes` = [(name, feeder, dist_km)]."""
    def __init__(self, community_only=False):
        self.nodes = []
        if not community_only:
            self.nodes += [(f'v{i:02d}', 'V', 0.04 * (i + 1)) for i in range(30)]
        self.nodes += [(f'c{i:02d}', 'C', 0.04 * (i + 1)) for i in range(10)]


import contextlib, threading
_VILLAGE_LOCK = threading.RLock()
DEFAULT_VILLAGE = dict(homes=90, pv_kwp=45.0, battery_kwh=120.0, battery_kw=40.0)
VILLAGE_BOUNDS = dict(homes=(10, 300), pv_kwp=(5.0, 150.0), battery_kwh=(20.0, 400.0), battery_kw=(5.0, 100.0))


def validate_village(**kw):
    out = dict(DEFAULT_VILLAGE)
    for k, v in kw.items():
        if v is None:
            continue
        if k not in VILLAGE_BOUNDS:
            raise ValueError(f'unknown village parameter {k}')
        lo, hi = VILLAGE_BOUNDS[k]
        if not (lo <= float(v) <= hi):
            raise ValueError(f'{k}={v} outside [{lo}, {hi}]')
        out[k] = int(v) if k == 'homes' else float(v)
    return out


@contextlib.contextmanager
def village(homes=None, pv_kwp=None, battery_kwh=None, battery_kw=None):
    """Temporarily resize the village. Module constants are swapped under a lock (runs are serialised).
    Derived, disclosed choices: inverter = 1.25 x battery kW; transformer = 100 kVA scaled with homes
    (rounded up to a 25 kVA step, never below 100). Charter-level results in results_platform.json
    exist ONLY for the default village."""
    global N_HOMES, PV_KWP, BATT_KWH, BATT_KW, INVERTER_KVA, TX_KVA
    v = validate_village(homes=homes, pv_kwp=pv_kwp, battery_kwh=battery_kwh, battery_kw=battery_kw)
    with _VILLAGE_LOCK:
        old = (N_HOMES, PV_KWP, BATT_KWH, BATT_KW, INVERTER_KVA, TX_KVA)
        N_HOMES, PV_KWP, BATT_KWH, BATT_KW = v['homes'], v['pv_kwp'], v['battery_kwh'], v['battery_kw']
        INVERTER_KVA = 1.25 * BATT_KW if BATT_KW != 40.0 else 50.0
        TX_KVA = max(100.0, 25.0 * math.ceil(100.0 * N_HOMES / 90.0 / 25.0))
        try:
            yield v
        finally:
            N_HOMES, PV_KWP, BATT_KWH, BATT_KW, INVERTER_KVA, TX_KVA = old


def _grid(nb, kva, backend):
    g = make_grid(nb, kva, backend)
    g.set_impedance_scale(Z_SCALE)
    return g


def hours(k):
    return ((WSTART + k) * DT) % 24.0


# ------------------------------------------------------------------ world
def build_world(seed=0, regime='normal'):
    rng = np.random.default_rng(stable_seed('rural', seed))
    w = weather_ensemble(n_days=2, day_of_year=140, seed=stable_seed('ruralwx', seed, regime), regime=regime)
    pv = pv_power(w['ghi'], w['temp'], PV_KWP)[WSTART:WSTART + WLEN]
    hh = np.array([hours(k) for k in range(WLEN)])
    mult = rng.lognormal(0.0, 0.3, N_HOMES)
    homes = np.zeros((N_HOMES, WLEN))
    for i in range(N_HOMES):
        eve = ((hh >= 18) & (hh < 22.5)).astype(float)
        morn = ((hh >= 5.5) & (hh < 8.0)).astype(float)
        noise = 1.0 + 0.15 * rng.standard_normal(WLEN)
        homes[i] = mult[i] * (0.08 + 0.33 * eve + 0.12 * morn + 0.05) * np.clip(noise, 0.5, 1.6)
    clinic = (CLINIC_KW['base'] + CLINIC_KW['day'] * ((hh >= 8) & (hh < 17))
              + CLINIC_KW['eve'] * ((hh >= 18) & (hh < 22)))
    open_ = ((hh >= 8) & (hh < 21)).astype(float)
    shops = SHOP_KW * open_[None, :] * (0.8 + 0.4 * rng.random((3, WLEN)))
    lights = LIGHT_KW * ((hh >= 18.5) | (hh < 5.5))
    water_use = np.where((hh >= 6) & (hh < 20), 1.5, 0.4)          # kWh-equivalent per hour
    return dict(w=w, pv=pv, homes=homes, clinic=clinic, shops=shops, lights=lights,
                fridge=np.full(WLEN, FRIDGE_KW), water_use=water_use, regime=regime, seed=seed)


class PVForecaster:
    """Issue-time forecast, re-issued every 2 h. Past is observed; future = skill-weighted
    blend of truth and climatology (nwp.py, SYNTHETIC skill assumption)."""
    def __init__(self, world):
        self.w = world
        hrs = np.arange(2 * T) * DT
        self.tclim = 29.5 - 4.5 * np.cos(2 * np.pi * (hrs[:T] - 3.5) / 24)
        self.cache = {}

    def at(self, k):
        origin = WSTART + (k // 8) * 8
        if origin not in self.cache:
            f = nwp.issue_forecast(self.w['w'], origin, self.w['w']['kt_base'], self.tclim,
                                   stable_seed('ruralnwp', self.w['seed'], origin), n_members=60)
            cs = self.w['w']['ghi_cs']
            q = lambda kt: pv_power(cs * kt, f['temp_fc'], PV_KWP)
            self.cache[origin] = dict(p50=q(f['kt_mean']), p10=q(np.percentile(f['kt_members'], 10, axis=0)))
        return self.cache[origin]

    def window(self, k, n, which='p10'):
        f = self.at(k)[which]
        a = WSTART + k
        seg = f[a:a + n]
        return np.r_[seg, np.zeros(n - len(seg))] if len(seg) < n else seg


# --------------------------------------------------------------- injection helpers
def _add(d, key, v):
    d[key] = d.get(key, 0.0) + v


def _three_phase(d, node, kw):
    for ph in (1, 2, 3):
        _add(d, (node, ph), kw / 3.0)


@dataclass
class State:
    soc: float = SOC0
    cold_c: float = 4.0
    tank: float = 0.7 * TANK_KWH
    mill_rem: float = MILL_JOB_KWH
    pump_on: bool = False
    cold_on: bool = False
    batt_flow: float = 0.0      # sum eta*c - d/eta [kWh], audit


def loads_at(world, k, st: State, req):
    """Nominal demand by device for step k. `req` carries the flexible loads' requested kW."""
    d = {}
    d['clinic'] = float(world['clinic'][k]); d['fridge'] = float(world['fridge'][k])
    d['lights'] = float(world['lights'][k])
    d['shops'] = [float(x) for x in world['shops'][:, k]]
    d['homes'] = world['homes'][:, k]
    d['cold'] = req.get('cold', 0.0); d['pump'] = req.get('pump', 0.0); d['mill'] = req.get('mill', 0.0)
    return d


def inject_loads(d, scale):
    """Map device demands to node injections. `scale` = dict of per-device service fractions."""
    inj = {}
    s = lambda name: scale.get(name, 1.0)
    _three_phase(inj, 'c03', d['clinic'] * s('clinic'))
    _three_phase(inj, 'c04', d['fridge'] * s('fridge'))
    _three_phase(inj, 'c05', d['lights'] * s('lights'))
    for j, kw in enumerate(d['shops']):
        _add(inj, (f'c0{6 + j}', j % 3 + 1), kw * s(f'shop{j}'))
    _three_phase(inj, 'c08', d['mill'] * s('mill'))
    _three_phase(inj, 'c09', d['cold'] * s('cold') + d['pump'] * s('pump'))
    return inj


def inject_homes(d, on=True):
    inj = {}
    if on:
        for i, kw in enumerate(d['homes']):
            _add(inj, (f'v{2 + (i % 28):02d}', (i % 3) + 1), float(kw))
    return inj


def pv_inj(pv_kw):
    out = {}
    _three_phase(out, 'c09', -pv_kw)
    return out


# --------------------------------------------------------------- flexible state updates
def step_thermal(st, frac_on):
    gain = 0.9 * DT                      # degC per step at 35 C ambient, door openings included
    cool = 3.2 * DT
    st.cold_c += gain - cool * frac_on
    return st.cold_c


def step_tank(st, w_use, pump_kw):
    st.tank = float(np.clip(st.tank + pump_kw * DT * 0.9 - w_use * DT, 0.0, TANK_KWH))
    return st.tank


# --------------------------------------------------------------- the run
def run_rural(scenario='normal', arm='neos', seed=0, backend='auto', world=None,
              t1_endurance_h=T1_ENDURANCE_H, charter_tiers=(1, 2)):
    """Returns (metrics, series). arm in {'naive','neos'}."""
    sc = SCENARIOS[scenario]
    world = world or build_world(seed, sc['regime'])
    nb_full, nb_island = RuralNb(), RuralNb(community_only=True)
    g_main = _grid(nb_full, TX_KVA, backend)
    g_isl = _grid(nb_island, INVERTER_KVA, backend)
    fcst = PVForecaster(world)
    st = State()
    out = dict(soc=[], cold_c=[], tank=[], tx=[], vmin=[], vmax=[], ok=[], mode=[], pv=[], pv_used=[],
               curt=[], clinic_frac=[], tier_frac={t: [] for t in (1, 2, 3, 4)}, batt_kw=[],
               phase_pct=[], net=[], mill_kw=[], pump_kw=[], cold_kw=[], inv_loading=[], status=[])
    e0 = st.soc * BATT_KWH
    served = {'clinic': 0.0, 'fridge': 0.0, 'lights': 0.0, 'shops': 0.0}
    demand = {'clinic': 0.0, 'fridge': 0.0, 'lights': 0.0, 'shops': 0.0}
    clip_events = 0
    out_a, out_b = sc['outage'] if sc['outage'] else (WLEN + 1, WLEN + 1)
    statuses = {'verified': 0, 'infeasible': 0, 'solver_failed': 0}
    island_statuses = {'verified': 0, 'infeasible': 0, 'solver_failed': 0}

    for k in range(WLEN):
        hr = hours(k)
        pv_av = float(world['pv'][k])
        island = out_a <= k < out_b
        e = st.soc * BATT_KWH
        # ---------------- flexible-load requests ------------------------------------
        req = _requests(arm, k, hr, st, world, pv_av, island)
        d = loads_at(world, k, st, req)
        inflex_comm = d['clinic'] + d['fridge'] + d['lights'] + sum(d['shops'])
        homes_kw = float(d['homes'].sum())
        d_all = dict(d)

        if not island:
            res = _grid_step(arm, k, hr, st, e, d, world, pv_av, g_main, fcst, t1_endurance_h)
            (cs, bc, bd, pv_scale_total, state, status, scales, pv_used) = res
            batt_flow_kwh = (bc * BATT_ETA - bd / BATT_ETA) * DT
            st.soc = (e + batt_flow_kwh) / BATT_KWH
            st.batt_flow += batt_flow_kwh
            frac = {nm: scales.get(nm, 1.0) for nm in ('cold', 'pump', 'mill')}
            out['mode'].append(0)
            out['clinic_frac'].append(1.0)
            for t in (1, 2, 3, 4):
                out['tier_frac'][t].append(1.0)
            served['clinic'] += d['clinic'] * DT; served['fridge'] += d['fridge'] * DT
            served['lights'] += d['lights'] * DT; served['shops'] += sum(d['shops']) * DT
            statuses[status] += 1
            vm, vx, txl, okf = state['vmin'], state['vmax'], state['tx_loading'], status == 'verified'
            net = state['tx_kw']
            ph = max(state['tx_phase_pct'])
            inv_l = float('nan')
            pv_cur = pv_av - pv_used
            bkw = bc - bd
        else:
            res = _island_step(arm, k, hr, st, e, d, world, pv_av, g_isl, fcst, t1_endurance_h)
            (frac_dev, bkw, pv_used, state, status, tiers) = res
            batt_flow_kwh = (max(-bkw, 0.0) * BATT_ETA * 0 + 0.0)
            # battery energy from the verified island solve: discharge = source power (incl. losses)
            src = state['tx_kw']
            if src >= 0:
                dE = -src * DT / BATT_ETA
            else:
                dE = (-src) * DT * BATT_ETA
            if e + dE < SOC_MIN * BATT_KWH - 1e-6 or e + dE > SOC_MAX * BATT_KWH + 1e-6:
                clip_events += 1
            st.soc = (e + dE) / BATT_KWH
            st.batt_flow += dE
            frac = {nm: frac_dev.get(nm, 1.0) for nm in ('cold', 'pump', 'mill')}
            served['clinic'] += d['clinic'] * frac_dev.get('clinic', 1.0) * DT
            served['fridge'] += d['fridge'] * frac_dev.get('fridge', 1.0) * DT
            served['lights'] += d['lights'] * frac_dev.get('lights', 1.0) * DT
            served['shops'] += sum(d['shops'][j] * frac_dev.get(f'shop{j}', 1.0) for j in range(3)) * DT
            out['clinic_frac'].append(frac_dev.get('clinic', 1.0))
            for t in (1, 2, 3, 4):
                out['tier_frac'][t].append(tiers.get(t, 1.0))
            out['mode'].append(1)
            island_statuses[status] += 1
            vm, vx, txl, okf = state['vmin'], state['vmax'], state['tx_loading'], status == 'verified'
            net = -src
            ph = max(state['tx_phase_pct'])
            inv_l = state['tx_loading']
            pv_cur = pv_av - pv_used
            bkw = src if src > 0 else src
        demand['clinic'] += d['clinic'] * DT; demand['fridge'] += d['fridge'] * DT
        demand['lights'] += d['lights'] * DT; demand['shops'] += sum(d['shops']) * DT

        # ---------------- advance flexible state from the VERIFIED dispatch ---------
        on_cold = frac.get('cold', 0.0) * (1.0 if req.get('cold', 0) > 0 else 0.0)
        step_thermal(st, on_cold)
        pump_kw_act = req.get('pump', 0.0) * frac.get('pump', 0.0)
        step_tank(st, float(world['water_use'][k]), pump_kw_act)
        mill_kw_act = req.get('mill', 0.0) * frac.get('mill', 0.0)
        st.mill_rem = max(0.0, st.mill_rem - mill_kw_act * DT)
        st.pump_on = bool(req.get('pump', 0.0) > 0 and frac.get('pump', 0.0) > 0.01)

        out['soc'].append(st.soc); out['cold_c'].append(st.cold_c); out['tank'].append(st.tank)
        out['tx'].append(txl); out['vmin'].append(vm); out['vmax'].append(vx); out['ok'].append(1.0 if okf else 0.0)
        out['pv'].append(pv_av); out['pv_used'].append(pv_used); out['curt'].append(pv_cur)
        out['batt_kw'].append(bkw); out['phase_pct'].append(ph); out['net'].append(net)
        out['mill_kw'].append(mill_kw_act); out['pump_kw'].append(pump_kw_act)
        out['cold_kw'].append(req.get('cold', 0.0) * frac.get('cold', 0.0))
        out['inv_loading'].append(inv_l); out['status'].append(status)

    A = {k_: np.array(v, float) for k_, v in out.items() if k_ not in ('tier_frac', 'status')}
    tf = {t: np.array(v, float) for t, v in out['tier_frac'].items()}
    isl = A['mode'] > 0.5
    unserved = {n: max(0.0, demand[n] - served[n]) for n in served}
    cold_hot_h = float(((A['cold_c'] > COLD_BAND[1])).sum() * DT)
    water_out_h = float(((A['tank'] < 0.10 * TANK_KWH)).sum() * DT)
    clinic_full = (A['clinic_frac'] >= 0.999)
    m = dict(
        scenario=scenario, arm=arm, backend=g_main.backend, seed=seed, regime=sc['regime'],
        outage_steps=int(isl.sum()), outage_hours=float(isl.sum() * DT),
        clinic_unserved_kwh=unserved['clinic'], tier2_unserved_kwh=unserved['fridge'] + unserved['lights'],
        shops_unserved_kwh=unserved['shops'],
        tier1_unserved_kwh=unserved['clinic'],
        clinic_hours_full_power_in_outage=float((clinic_full & isl).sum() * DT),
        clinic_hours_degraded_in_outage=float(((~clinic_full) & isl).sum() * DT),
        clinic_first_failure_h_after_outage=(float(np.argmax((~clinic_full) & isl) * DT - out_a * DT)
                                             if ((~clinic_full) & isl).any() else None),
        cold_chain_hours_above_8C=cold_hot_h, water_outage_hours=water_out_h,
        mill_job_done=bool(st.mill_rem < 1e-6), mill_remaining_kwh=float(st.mill_rem),
        min_voltage=float(np.nanmin(A['vmin'])), max_voltage=float(np.nanmax(A['vmax'])),
        peak_tx_loading=float(np.nanmax(np.where(isl, np.nan, A['tx'])) if (~isl).any() else float('nan')),
        peak_inverter_loading=float(np.nanmax(np.where(isl, A['inv_loading'], np.nan))) if isl.any() else None,
        peak_phase_loading=float(np.nanmax(A['phase_pct'])),
        pv_available_kwh=float(A['pv'].sum() * DT), pv_curtailed_kwh=float(A['curt'].sum() * DT),
        pv_utilisation_pct=float(100 * A['pv_used'].sum() / max(A['pv'].sum(), 1e-9)),
        final_soc=float(st.soc), min_soc=float(A['soc'].min()),
        grid_steps_verified_pct=100.0 * statuses['verified'] / max(1, sum(statuses.values())) if sum(statuses.values()) else None,
        island_steps_verified_pct=100.0 * island_statuses['verified'] / max(1, sum(island_statuses.values())) if sum(island_statuses.values()) else None,
        constraint_status_grid=statuses, constraint_status_island=island_statuses,
        energy_audit=dict(battery_residual_kwh=float(abs(st.soc * BATT_KWH - e0 - st.batt_flow)), clip_events=int(clip_events)),
        assumptions=dict(pv_kwp=PV_KWP, battery_kwh=BATT_KWH, battery_kw=BATT_KW, inverter_kva=INVERTER_KVA,
                         tx_kva=TX_KVA, z_scale=Z_SCALE, homes=N_HOMES, t1_endurance_h=t1_endurance_h,
                         island='grid-forming source behind the same LV network; dynamics not modelled'),
    )
    series = {k_: [round(float(x), 4) for x in v] for k_, v in A.items()}
    series['tier_frac'] = {str(t): [round(float(x), 3) for x in v] for t, v in tf.items()}
    return _json_safe(m), _json_safe(series)


def _json_safe(o):
    """NaN/inf are not JSON: report 'not applicable' (e.g. inverter loading while the grid is up) as null."""
    if isinstance(o, dict):
        return {k: _json_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_json_safe(v) for v in o]
    if isinstance(o, float) and not math.isfinite(o):
        return None
    return o


# ---------------------------------------------------------------- requests (flexible loads)
def _requests(arm, k, hr, st, world, pv_av, island):
    """What each flexible load asks for this step (kW), BEFORE any supply constraint."""
    req = dict(cold=0.0, pump=0.0, mill=0.0)
    inflex = (world['clinic'][k] + world['fridge'][k] + world['lights'][k] + world['shops'][:, k].sum())
    surplus = pv_av - inflex
    lo, hi = COLD_BAND
    if arm == 'naive':
        # local rules, blind to supply: thermostat 3.0-7.5 C, tank 35 -> 80 %, mill at the user's usual 15:30
        st.cold_on = (st.cold_c > 7.5) or (st.cold_on and st.cold_c > 3.0)
        req['cold'] = COLD_KW if st.cold_on else 0.0
        low, high = 0.35 * TANK_KWH, 0.80 * TANK_KWH
        want = (st.tank < low) or (st.pump_on and st.tank < high)
        req['pump'] = PUMP_KW if want else 0.0
        if k >= 14 and st.mill_rem > 1e-6:
            req['mill'] = min(MILL_KW, st.mill_rem / DT)
        return req
    # ---- neos: thermal/tank/job INERTIA used, supply-aware ----
    must_cold = st.cold_c > 7.0
    precool = surplus > COLD_KW and st.cold_c > 3.2
    req['cold'] = COLD_KW if (must_cold or precool or (st.cold_on and st.cold_c > 3.0 and surplus > 0)) else 0.0
    st.cold_on = req['cold'] > 0
    must_pump = st.tank < 0.30 * TANK_KWH
    solar_pump = surplus > PUMP_KW and st.tank < 0.95 * TANK_KWH
    req['pump'] = PUMP_KW if (must_pump or solar_pump or (st.pump_on and surplus > 0 and st.tank < 0.98 * TANK_KWH)) else 0.0
    if st.mill_rem > 1e-6:
        steps_needed = int(np.ceil(st.mill_rem / (MILL_KW * DT)))
        latest_start = 24 - steps_needed                      # job must finish by 18:00 (step 24)
        if surplus > MILL_KW or k >= latest_start:
            req['mill'] = min(MILL_KW, st.mill_rem / DT)
    return req


# ---------------------------------------------------------------- grid-connected step
def _grid_step(arm, k, hr, st, e, d, world, pv_av, grid, fcst, t1h):
    cap = BATT_KWH
    inflex_kw = d['clinic'] + d['fridge'] + d['lights'] + sum(d['shops']) + float(d['homes'].sum())
    flex_req = d['cold'] + d['pump'] + d['mill']
    p_chg_max = max(0.0, min(BATT_KW, (SOC_MAX * cap - e) / (BATT_ETA * DT)))
    p_dis_max = max(0.0, min(BATT_KW, (e - SOC_MIN * cap) * BATT_ETA / DT))
    bc = bd = 0.0
    if arm == 'naive':
        net0 = inflex_kw + flex_req - pv_av
        if net0 < 0:
            bc = min(-net0, p_chg_max)
        else:
            bd = min(net0, p_dis_max)
        groups = {}
        base = {}
        for dd in (inject_loads(d, {}), inject_homes(d)):
            for kk, v in dd.items():
                _add(base, kk, v)
        if bc > 0: _three_phase(base, 'c00', bc)
        if bd > 0: _three_phase(base, 'c00', -bd)
        pv_i = pv_inj(pv_av)
        r = safety_project(grid, base, pv_i, groups)
        return (None, bc, bd, r.pv_scale, r.state, r.status, {}, pv_av * r.pv_scale)
    # NEOS: reserve-aware battery, flexible loads in groups the safety layer may scale
    reserve = _reserve_soc(k, d, world, fcst, t1h) * cap
    net_pre = inflex_kw + flex_req - pv_av
    if net_pre < 0:
        bc = min(-net_pre, p_chg_max)
    else:
        peak = (18.0 <= hr < 22.5) or net_pre > 0.55 * TX_KVA
        if peak and e > reserve:
            bd = min(net_pre, p_dis_max, (e - reserve) * BATT_ETA / DT)
    base = {}
    for dd in (inject_loads(dict(d, cold=0.0, pump=0.0, mill=0.0), {}), inject_homes(d)):
        for kk, v in dd.items():
            _add(base, kk, v)
    g_app = {}
    _three_phase(g_app, 'c09', d['cold'] + d['pump'])
    _three_phase(g_app, 'c08', d['mill'])
    g_bc, g_bd = {}, {}
    _three_phase(g_bc, 'c00', bc)
    _three_phase(g_bd, 'c00', -bd)
    groups = {'appl': g_app, 'batt_chg': g_bc, 'batt_dis': g_bd}
    r = safety_project(grid, base, pv_inj(pv_av), groups)
    s_ap, s_bc, s_bd = r.scales.get('appl', 1.0), r.scales.get('batt_chg', 1.0), r.scales.get('batt_dis', 1.0)
    scales = {'cold': s_ap, 'pump': s_ap, 'mill': s_ap}
    return (None, bc * s_bc, bd * s_bd, r.pv_scale, r.state, r.status, scales, pv_av * r.pv_scale)


def _reserve_soc(k, d, world, fcst, t1h):
    """Confidence-aware battery reserve: energy to carry tier 1-2 for the charter horizon, net of
    the PESSIMISTIC (p10) solar expected in it, plus a margin proportional to forecast spread."""
    n = int(round(t1h / DT))
    a = k
    core = (world['clinic'] + world['fridge'] + world['lights'])
    seg = np.r_[core[a:a + n], np.full(max(0, n - len(core[a:a + n])), core[-1])]
    p10 = fcst.window(k, n, 'p10'); p50 = fcst.window(k, n, 'p50')
    direct = np.minimum(p10, seg)
    need = float(np.sum(seg - direct) * DT) / BATT_ETA
    spread = float(np.sum(np.maximum(p50 - p10, 0.0)) * DT) * 0.1
    soc = SOC_MIN + (need + min(spread, 0.1 * BATT_KWH)) / BATT_KWH
    return float(np.clip(soc, 0.15, 0.80))


# ------------------------------------------------------------------ island step
DEV_TIER = {'clinic': 1, 'fridge': 2, 'lights': 2, 'shop0': 3, 'shop1': 3, 'shop2': 3,
            'cold': 3, 'mill': 3, 'pump': 4}


def _island_step(arm, k, hr, st, e, d, world, pv_av, grid, fcst, t1h):
    cap = BATT_KWH
    p_avail = max(0.0, min(BATT_KW, (e - SOC_MIN * cap) * BATT_ETA / DT))
    p_absorb = max(0.0, min(BATT_KW, (SOC_MAX * cap - e) / (BATT_ETA * DT)))
    dev_kw = dict(clinic=d['clinic'], fridge=d['fridge'], lights=d['lights'],
                  shop0=d['shops'][0], shop1=d['shops'][1], shop2=d['shops'][2],
                  cold=d['cold'], mill=d['mill'], pump=d['pump'])
    if arm == 'naive':
        order = [list(dev_kw)]                   # one block: everyone shares equally
    else:
        # endurance-based service level: admit tier t only if the energy for tiers 1-2 over the
        # charter horizon is still covered afterwards; marginal tier is admitted fractionally.
        order = [[n for n in dev_kw if DEV_TIER[n] == t] for t in (1, 2, 3, 4)]
    admit = {n: 1.0 for n in dev_kw}
    if arm == 'neos':
        extra_kw = _extra_allowed_kw(k, d, world, fcst, e, t1h)
        run = 0.0
        for t in (3, 4):
            tier_dev = [n for n in dev_kw if DEV_TIER[n] == t]
            tier_kw = sum(dev_kw[n] for n in tier_dev)
            if tier_kw <= 1e-9:
                continue
            room = max(0.0, extra_kw - run)
            f = float(np.clip(room / tier_kw, 0.0, 1.0))
            for n in tier_dev:
                admit[n] = f
            run += tier_kw * f

    pvu = [pv_av]                       # PV actually injected for the LAST solve (protective curtailment)

    def solve(scales):
        # Surplus the battery cannot absorb is PV that must be CURTAILED -- never a reason to shed
        # loads (that would enlarge the surplus). Applied inside every solve so that load shedding
        # and curtailment cannot fight each other. Identical protection for both arms.
        pv = pv_av
        for _ in range(8):
            inj = inject_loads(d, scales)
            for kk, v in pv_inj(pv).items():
                _add(inj, kk, v)
            stt = grid.solve(inj)
            excess = (-stt['tx_kw']) - p_absorb * 0.999 if stt['finite'] else 0.0
            if excess <= 1e-6 or pv <= 0.0:
                break
            pv = max(0.0, pv - excess - 1e-6)
        pvu[0] = pv
        return stt, inj

    def feasible(stt):
        rep = constraint_report(stt)
        src = stt['tx_kw']
        return rep['ok'] and src <= p_avail + 1e-6 and (-src) <= p_absorb * 0.999 + 1e-6, rep

    scales = dict(admit)
    state, _ = solve(scales)
    ok, rep = feasible(state)
    if not ok:
        scales = _shed_to_feasible(arm, dev_kw, admit, order, solve, feasible)
        state, _ = solve(scales)
        ok, rep = feasible(state)
    pv_used = pvu[0]
    status = ('verified' if ok else ('solver_failed' if not rep['solver_ok'] else 'infeasible'))
    tiers = {t: _tier_service(dev_kw, scales, t) for t in (1, 2, 3, 4)}
    return (scales, state['tx_kw'], pv_used, state, status, tiers)


def _tier_service(dev_kw, scales, t):
    num = sum(dev_kw[n] * scales.get(n, 1.0) for n in dev_kw if DEV_TIER[n] == t)
    den = sum(dev_kw[n] for n in dev_kw if DEV_TIER[n] == t)
    return float(num / den) if den > 1e-9 else 1.0


def _shed_to_feasible(arm, dev_kw, admit, order, solve, feasible):
    scales = dict(admit)
    if arm == 'naive':
        lo, hi = 0.0, 1.0                        # one common scale for ALL devices (tier-blind)
        for _ in range(14):
            mid = 0.5 * (lo + hi)
            ok, _r = feasible(solve({n: admit[n] * mid for n in dev_kw})[0])
            lo, hi = (mid, hi) if ok else (lo, mid)
        return {n: admit[n] * lo for n in dev_kw}
    for block in reversed(order):                # lowest tier first, tier 1 last
        lo, hi = 0.0, 1.0
        base = dict(scales)
        ok, _r = feasible(solve(base)[0])
        if ok:
            return scales
        for _ in range(14):
            mid = 0.5 * (lo + hi)
            trial = dict(base)
            for n in block:
                trial[n] = base[n] * mid
            ok, _r = feasible(solve(trial)[0])
            lo, hi = (mid, hi) if ok else (lo, mid)
        for n in block:
            scales[n] = base[n] * lo
        if feasible(solve(scales)[0])[0]:
            return scales
    return scales


def _extra_allowed_kw(k, d, world, fcst, e, t1h):
    """Average kW (above tier-1/2 demand) that tiers 3-4 may draw while the reserve still
    covers tiers 1-2 for the charter horizon, counting only PESSIMISTIC (p10) solar."""
    n = int(round(t1h / DT))
    core = (world['clinic'] + world['fridge'] + world['lights'])
    seg = np.r_[core[k:k + n], np.full(max(0, n - len(core[k:k + n])), core[-1])]
    p10 = fcst.window(k, n, 'p10')
    e_usable = max(0.0, (e - SOC_MIN * BATT_KWH)) * BATT_ETA
    e_pv = float(p10.sum() * DT) * 0.9
    e_core = float(seg.sum() * DT)
    spare = e_usable + e_pv - e_core
    cur_core = d['clinic'] + d['fridge'] + d['lights']
    pv_now = float(world['pv'][k])
    direct = max(0.0, pv_now - cur_core)             # PV beyond the core load right now is free
    return max(0.0, spare / (n * DT)) + direct
