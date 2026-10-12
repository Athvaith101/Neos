"""
controllers.py -- Policies behind one interface.

    controller.act(obs) -> Proposal        (what the policy WANTS)
    controller.feedback(state, result)     (post-hoc telemetry, optional)

A controller sees only `obs`: observed connection state, measured SOC, learned
departure DISTRIBUTIONS, declared user requirements, issue-time forecasts and
the tariff. It never sees realised departures, realised weather or the
simulator's overrides before the user declares them. A MARL policy plugs in
here, goes through the identical device limits and safety layer, and is judged
on the identical metrics (including safety interventions).
"""
from __future__ import annotations
from dataclasses import dataclass, field
import numpy as np
from .world import T, DT
from .control import solve_mpc, EV_ETA, tariff_vector


@dataclass
class Proposal:
    ev: dict = field(default_factory=dict)       # id -> kW charge
    bc: dict = field(default_factory=dict)       # id -> kW charge
    bd: dict = field(default_factory=dict)       # id -> kW discharge
    fl: dict = field(default_factory=dict)       # id -> kW appliance
    curt_kw: float = 0.0                         # planned PV curtailment (total kW)
    info: dict = field(default_factory=dict)


# ----------------------------------------------------------- uncoordinated
class UncoordinatedController:
    """Today's de-facto behaviour: EVs charge on plug-in, batteries self-consume,
    appliances run when the user starts them. The honest counterfactual."""
    name = 'uncoordinated'
    uses_safety_groups = False

    def __init__(self, nb, defer_pref):
        self.nb, self.defer_pref = nb, defer_pref

    def act(self, obs):
        p = Proposal()
        for i, e in obs['ev'].items():
            p.ev[i] = e['p_max'] if e['need_user_kwh'] > 1e-3 else 0.0
        net = obs['net_now_kw']
        for i, b in obs['batt'].items():
            if not b['available']:
                continue
            soc = b['e'] / b['cap']
            if net < 0 and soc < 0.95:
                p.bc[i] = b['p_max']
            elif net > 0 and soc > 0.20:
                p.bd[i] = b['p_max']
        for i, f in obs['defer'].items():
            if f['rem'] > 1e-3 and obs['k'] >= self.defer_pref[i]:
                p.fl[i] = f['p_max']
        return p

    def feedback(self, st, res):
        pass


# ------------------------------------------------------- no control (Arm A)
class NoControlController:
    """V3 roadmap, section 7, Arm A -- 'normal uncontrolled behaviour'.

    This is DELIBERATELY a separate class from UncoordinatedController, even
    though the two look similar, because they answer different questions.
    UncoordinatedController (mode='uncoordinated') already runs battery
    self-consumption logic -- charge on PV surplus, discharge on net demand --
    which is exactly the roadmap's Arm B ('battery optimisation without
    broader flexibility coordination'), not Arm A. Collapsing the two would
    make it impossible to separate "does having a battery with even the
    simplest control help at all" (Arm A -> Arm B) from "does full NEOS
    coordination help beyond that" (Arm B -> Arm C) -- which is the entire
    point of the roadmap's three-arm design (section 7, "Fair-comparison
    rule"). EVs and appliances behave identically to UncoordinatedController
    (immediate charge on plug-in, immediate use) in both arms, because the
    roadmap's Arm A/B distinction is specifically about the BATTERY.

    EXISTING UncoordinatedController, and every test or result built on
    mode='uncoordinated', is left completely unchanged: this is a new,
    additive mode ('no_control'), not a redefinition of an existing one."""
    name = 'no_control'
    uses_safety_groups = False

    def __init__(self, nb, defer_pref):
        self.nb, self.defer_pref = nb, defer_pref

    def act(self, obs):
        p = Proposal()
        for i, e in obs['ev'].items():
            p.ev[i] = e['p_max'] if e['need_user_kwh'] > 1e-3 else 0.0
        # Battery deliberately issues NO charge/discharge commands at all --
        # it is physically present but grid-inert, the Arm A baseline every
        # other arm's benefit is measured against.
        for i, f in obs['defer'].items():
            if f['rem'] > 1e-3 and obs['k'] >= self.defer_pref[i]:
                p.fl[i] = f['p_max']
        return p

    def feedback(self, st, res):
        pass


# --------------------------------------------------------------- rule policy
class RulePolicy:
    """A transparent time-of-use heuristic. Exists to prove the policy interface
    and to give any future RL agent a SECOND benchmark between 'do nothing' and
    'solve the LP'."""
    name = 'rule_tou'
    uses_safety_groups = True

    def __init__(self, nb):
        self.nb = nb

    def act(self, obs):
        p = Proposal()
        k, tar = obs['k'], obs['tariff_now']
        cheap = tar <= 6.5
        for i, e in obs['ev'].items():
            if e['need_kwh'] <= 1e-3:
                continue
            steps_left = max(1, e['dl_p10_abs'] - obs['t'])
            must = e['need_kwh'] >= e['p_max'] * EV_ETA * DT * (steps_left - 1)
            if cheap or must:
                p.ev[i] = e['p_max']
        net = obs['net_fc_now_kw']
        for i, b in obs['batt'].items():
            if not b['available']:
                continue
            soc = b['e'] / b['cap']
            if net < 0 and soc < 0.95:
                p.bc[i] = b['p_max']
            elif tar >= 9.0 and soc > 0.30:
                p.bd[i] = b['p_max']
        hr = obs['hour']
        for i, f in obs['defer'].items():
            if f['rem'] > 1e-3 and (hr >= 22 or hr < 5.5):
                p.fl[i] = f['p_max']
        return p

    def feedback(self, st, res):
        pass


# ------------------------------------------------------------------- MPC
def _protect_ev(items):
    """A DISCOM request must never be paid for with a late EV: in the capped solve both
    delivery tiers use the EARLY (p10) departure deadline, so deferral can only use slack
    that exists even if the user leaves early."""
    return [dict(e, deadline2=e['deadline']) for e in items]


class MPCController:
    name = 'coordinated_mpc'
    uses_safety_groups = True

    def __init__(self, nb, forecast, p_head, p_exp, wlen, mpc_every=2):
        self.nb, self.fc = nb, forecast
        self.P_head0, self.P_exp0 = p_head, p_exp
        self.P_head, self.P_exp = p_head, p_exp
        self.wlen, self.mpc_every = wlen, mpc_every
        self.plan = None
        self.last_solve_k = -10**9
        self.n_solves = 0
        self.solve_times = []
        self.plan_status = {}
        self.fallback = RulePolicy(nb)
        self.n_fallback_steps = 0
        self.net_cut = None            # optional absolute-step array: requested reduction of net import (kW)

    # -- heuristic (NOT physical) adaptation of the aggregate limits from telemetry
    def feedback(self, st, res):
        if st is None or not st.get('finite', False):
            return
        exp_now = max(-st['tx_kw'], 0.0)
        if res.pv_scale < 1.0 or st['vmax'] > 1.055:
            self.P_exp = max(60.0, min(self.P_exp, 0.88 * max(exp_now, 60.0)))
        elif st['vmax'] < 1.045:
            self.P_exp = min(self.P_exp0, self.P_exp * 1.03 + 2.0)
        if st['vmin'] < 0.944 or st['tx_loading'] > 96:
            self.P_head = max(120.0, min(self.P_head, 0.94 * max(st['tx_kw'], 120.0)))
        elif st['vmin'] > 0.96 and st['tx_loading'] < 88:
            self.P_head = min(self.P_head0, self.P_head * 1.02 + 2.0)

    def _build(self, obs):
        k, t = obs['k'], obs['t']
        H = min(self.wlen + 8 - k, 96)
        ev_items, ev_ids = [], []
        fixed = np.zeros(H)
        for i, e in obs['ev'].items():
            if e['hard']:
                n = int(np.ceil(e['need_kwh'] / max(e['p_max'] * EV_ETA * DT, 1e-6)))
                if e['ready_by'] is not None:
                    n = min(n, max(0, e['ready_by'] - t))
                fixed[:min(n, H)] += e['p_max']
                continue
            if e['need_kwh'] <= 1e-3:
                continue
            last = max(e['dl_p90_abs'], t + 2)
            avail = np.array([(t + hh) < last for hh in range(H)])
            dl = int(np.clip(e['dl_p10_abs'] - t, 1, H))
            dl2 = int(np.clip(e['dl_p90_abs'] - t, dl, H))
            ev_items.append(dict(p_max=e['p_max'], kwh_need=e['need_kwh'], deadline=dl,
                                 deadline2=dl2, avail=avail, priority=e['priority']))
            ev_ids.append(i)
        b_items, b_ids = [], []
        for i, b in obs['batt'].items():
            if not b['available']:
                continue
            b_items.append(dict(p_max_c=b['p_max'], p_max_d=b['p_max'], cap=b['cap'], e0=b['e'],
                                eta=b['eta'], soc_min=0.15, soc_max=0.95))
            b_ids.append(i)
        f_items, f_ids = [], []
        for i, f in obs['defer'].items():
            if f['rem'] <= 1e-3:
                continue
            e0, l0 = f['window']
            avail = np.array([e0 <= (k + hh) <= l0 for hh in range(H)])
            if not avail.any():
                continue
            f_items.append(dict(p_max=f['p_max'], kwh=f['rem'], avail=avail, flex=f['flex']))
            f_ids.append(i)
        sl = slice(k, k + H)
        infl = {q: self.fc['inflex'][q][sl] for q in ('p10', 'p50', 'p90')}
        pv = {q: self.fc['pv'][q][sl] for q in ('p10', 'p50', 'p90')}
        return H, ev_items, ev_ids, b_items, b_ids, f_items, f_ids, infl, pv, fixed

    def cut_capability(self, obs, start_abs, n, x_hi=450.0, tol_kwsteps=0.5, iters=11):
        """Largest SUSTAINED reduction of net import (kW) over [start_abs, start_abs+n)
        that the optimiser can deliver with ZERO unmet slack, given the state NOW,
        the forecast, device limits, EV requirements and the battery reserve.
        READ-ONLY: solves LPs from the current observation; changes nothing.
        Returns None if the window is outside the planning horizon."""
        k = obs['k']
        H, evi, evid, bi, bid, fi, fid, infl, pv, fixed = self._build(obs)
        a, b = start_abs - k, start_abs - k + n
        if a < 0 or b > H:
            return None
        tar = tariff_vector(H, offset=obs['t'])
        kw = dict(fixed=fixed)
        args = (H, infl, pv, evi, bi, fi, np.full(H, self.P_head), np.full(H, self.P_exp), tar)
        p0 = solve_mpc(*args, **kw)
        if p0.status != 'optimal':
            return 0.0
        z0 = float(p0.plan_net.max())
        evp = _protect_ev(evi)

        def feasible(x):
            cap = np.full(H, z0)
            cap[a:b] = p0.plan_net[a:b] - x
            pl = solve_mpc(H, infl, pv, evp, bi, fi, np.full(H, self.P_head),
                           np.full(H, self.P_exp), tar, fixed=fixed, net_cap=cap)
            return pl.status == 'optimal' and pl.req_slack <= tol_kwsteps
        lo, hi = 0.0, x_hi
        if feasible(hi):
            return hi
        for _ in range(iters):
            mid = 0.5 * (lo + hi)
            if feasible(mid):
                lo = mid
            else:
                hi = mid
        return float(lo)

    def act(self, obs):
        k = obs['k']
        replan = (self.plan is None or k - self.plan.k0 >= self.mpc_every
                  or bool(obs['events']) or self.plan.at(k) is None)
        if replan:
            H, evi, evid, bi, bid, fi, fid, infl, pv, fixed = self._build(obs)
            tar = tariff_vector(H, offset=obs['t'])
            cap = None
            if self.net_cut is not None:
                cut = np.zeros(H)
                seg = np.asarray(self.net_cut, float)[k:k + H]
                cut[:len(seg)] = seg
                if cut.max() > 0:
                    # The request is defined against the optimiser's OWN unrequested plan
                    # (forecast space, same information), and the response may not create a
                    # higher peak than that plan: no rebound above the baseline peak.
                    p0 = solve_mpc(H, infl, pv, evi, bi, fi, np.full(H, self.P_head),
                                   np.full(H, self.P_exp), tar, fixed=fixed)
                    if p0.status == 'optimal':
                        z0 = float(p0.plan_net.max())
                        cap = np.where(cut > 0, p0.plan_net - cut, z0)
            plan = solve_mpc(H, infl, pv, evi if cap is None else _protect_ev(evi), bi, fi,
                             np.full(H, self.P_head), np.full(H, self.P_exp), tar,
                             fixed=fixed, net_cap=cap)
            plan.ev_ids, plan.b_ids, plan.f_ids, plan.k0 = evid, bid, fid, k
            self.plan = plan
            self.n_solves += 1
            self.solve_times.append(plan.solve_s)
            self.plan_status[plan.status] = self.plan_status.get(plan.status, 0) + 1
        plan = self.plan
        i = plan.at(k)
        if i is None:
            # LP infeasible/failed: say so, and fall back to a transparent rule
            # policy instead of silently outputting zeros.
            self.n_fallback_steps += 1
            p = self.fallback.act(obs)
            p.info['fallback'] = plan.status
            return p
        p = Proposal()
        for j, eid in enumerate(plan.ev_ids):
            if eid in obs['ev']:
                p.ev[eid] = float(plan.ev[j, i])
        for j, bid in enumerate(plan.b_ids):
            p.bc[bid], p.bd[bid] = float(plan.bc[j, i]), float(plan.bd[j, i])
        for j, fid in enumerate(plan.f_ids):
            p.fl[fid] = float(plan.fl[j, i])
        p.curt_kw = float(plan.curt[i])                 # planned curtailment is APPLIED
        p.info['plan_age'] = k - plan.k0
        return p
