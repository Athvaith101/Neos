"""
control.py -- Decision layer.

  1. FLEXIBILITY ENVELOPES  -- what the controller may know about a user: a
     DISTRIBUTION over departure, plus any requirement the user has actually
     DECLARED. Predicted preferences are soft; declared requirements are hard.
  2. CHANCE-CONSTRAINED LP-MPC -- the deterministic benchmark any RL policy must
     beat. Returns the FULL multi-step plan, and a solver status.
  3. SAFETY PROJECTION -- a policy-agnostic, resource-level, direction-aware
     filter verified by an AC power flow. It always solves the FINAL action it
     returns and reports an explicit status: verified | infeasible | solver_failed.

Semantics worth stating plainly
  * "I need my car now" cannot create energy. It (a) removes the EV from
    discretionary optimisation, (b) charges at the maximum the charger and
    battery physically allow, and (c) reports honestly what SOC is available.
  * Electrical protection retains precedence over every request, including
    explicit ones; if protection must override a user it is logged as such.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import time
import numpy as np
from scipy.optimize import linprog
from scipy import sparse
from .world import T, DT
from .grid import constraint_report, VLIM_LO, VLIM_HI

EV_ETA = 0.92          # charger+battery round-trip efficiency used EVERYWHERE


# ------------------------------------------------------------------ tariff
def tariff_vector(n, offset=0):
    """Illustrative Indian-style ToD tariff, INR/kWh. Replace with the TNERC order."""
    p = np.empty(n)
    for k in range(n):
        h = ((k + offset) % T) * DT
        if 22 <= h or h < 6:
            p[k] = 4.5
        elif 6 <= h < 9 or 18 <= h < 22:
            p[k] = 9.0
        else:
            p[k] = 6.5
    return p


LAMBDA_PEAK = 12.0
LAMBDA_DEG = 1.8
LAMBDA_CURT = 7.0


# ------------------------------------------------------- flexibility envelope
class EVEnvelope:
    """Everything the controller may know about one plugged-in EV.

    `dep_p10/p50/p90` come from a LEARNED distribution (population parameter +
    estimation noise), never from the realised departure. `hard` is True only
    after the user DECLARES a requirement (`declare`)."""

    def __init__(self, ev, soc, plug_in, risk_q=0.10, learn_noise=0.0, rng=None):
        from scipy.stats import norm
        rng = rng or np.random.default_rng(0)
        mu = ev.dep_mu + (rng.normal(0, learn_noise) if learn_noise else 0.0)
        sg = ev.dep_sigma * (1 + (rng.normal(0, learn_noise) if learn_noise else 0.0))
        self.ev, self.soc, self.plug_in = ev, soc, plug_in
        self.dep_p10 = mu + norm.ppf(risk_q) * sg
        self.dep_p50 = mu
        self.dep_p90 = mu + norm.ppf(0.90) * sg
        self.req_soc = min(0.95, ev.req_soc + 0.03)        # small comfort buffer
        self.p_max = ev.charger_kw
        self.priority = 1.0 - ev.flex_tolerance
        self.hard = False
        self.declared_at = None
        self.ready_by = None          # absolute step the user said they will leave
        self.kind = None

    def declare(self, step, ready_by, kind):
        self.hard, self.declared_at, self.ready_by, self.kind = True, step, ready_by, kind

    def energy_needed(self, soc):
        return max(0.0, (self.req_soc - soc) * self.ev.capacity_kwh)

    def feasibility(self, soc, now, target_soc=None):
        """Can the declared requirement be met? Honest physics, not a promise."""
        tgt = self.req_soc if target_soc is None else target_soc
        need = max(0.0, (tgt - soc) * self.ev.capacity_kwh)
        room = max(0.0, (1.0 - soc) * self.ev.capacity_kwh)
        need = min(need, room)
        steps_left = max(0, (self.ready_by if self.ready_by is not None else now) - now)
        deliverable = self.p_max * EV_ETA * DT * steps_left
        return dict(kwh_needed=need, kwh_deliverable=min(deliverable, room),
                    feasible=bool(deliverable + 1e-9 >= need),
                    shortfall_kwh=max(0.0, need - deliverable),
                    soc_reachable=min(1.0, soc + deliverable / self.ev.capacity_kwh))


# ----------------------------------------------------------------- LP-MPC
@dataclass
class Plan:
    status: str                       # optimal | infeasible | failed
    H: int = 0
    ev_ids: list = field(default_factory=list)
    b_ids: list = field(default_factory=list)
    f_ids: list = field(default_factory=list)
    ev: np.ndarray = None             # [nE,H] kW
    bc: np.ndarray = None
    bd: np.ndarray = None
    fl: np.ndarray = None
    curt: np.ndarray = None           # [H] kW planned PV curtailment
    plan_net: np.ndarray = None
    shortfall: np.ndarray = None
    shortfall2: np.ndarray = None
    obj: float = float('nan')
    solve_s: float = 0.0
    k0: int = 0                       # control step the plan starts at
    n_vars: int = 0
    n_rows: int = 0

    def at(self, k):
        """Setpoints for absolute control step k (index INTO the plan)."""
        i = k - self.k0
        if self.status != 'optimal' or i < 0 or i >= self.H:
            return None
        return i


def solve_mpc(H, inflex, pv, ev_items, batt_items, defer_items, p_headroom, p_export,
              tariff, fixed=None, curt_cap=None):
    """
    inflex/pv : dict with 'p10','p50','p90' arrays (length >= H)
    fixed     : optional kW series of PROTECTED load (explicit user requirements)
    Variables: xev, pc, pd, xfl, cur, ep (import epigraph), z (peak), slacks,
    and battery energy STATE variables eB (sparse dynamics instead of dense
    triangular sums -- this is what makes the LP fast enough to re-solve often).
    """
    t0 = time.perf_counter()
    nE, nB, nF = len(ev_items), len(batt_items), len(defer_items)
    fx = np.zeros(H) if fixed is None else np.asarray(fixed, float)[:H]
    i50, i90, i10 = (np.asarray(inflex[q], float)[:H] + fx for q in ('p50', 'p90', 'p10'))
    v50, v10, v90 = (np.asarray(pv[q], float)[:H] for q in ('p50', 'p10', 'p90'))

    oE = 0; oC = oE + nE * H; oD = oC + nB * H; oF = oD + nB * H
    oCur = oF + nF * H; oEp = oCur + H; oZ = oEp + H
    oS = oZ + 1; oS2 = oS + nE; oSF = oS2 + nE; oSB = oSF + nF; oEB = oSB + nB
    N = oEB + nB * H

    ub = np.full(N, np.inf); lb = np.zeros(N)
    for i, e in enumerate(ev_items):
        ub[oE + i * H:oE + (i + 1) * H] = np.where(e['avail'][:H], e['p_max'], 0.0)
    for b, B in enumerate(batt_items):
        ub[oC + b * H:oC + (b + 1) * H] = B['p_max_c']
        ub[oD + b * H:oD + (b + 1) * H] = B['p_max_d']
        lo_e = min(B['cap'] * B['soc_min'], B['e0'])
        hi_e = max(B['cap'] * B['soc_max'], B['e0'])
        lb[oEB + b * H:oEB + (b + 1) * H] = lo_e
        ub[oEB + b * H:oEB + (b + 1) * H] = hi_e
    for j, F in enumerate(defer_items):
        ub[oF + j * H:oF + (j + 1) * H] = np.where(F['avail'][:H], F['p_max'], 0.0)
    ub[oCur:oCur + H] = np.maximum(v90 if curt_cap is None else curt_cap[:H], 0.0)

    c = np.zeros(N)
    c[oEp:oEp + H] = tariff[:H] * DT
    c[oZ] = LAMBDA_PEAK
    for b in range(nB):
        c[oC + b * H:oC + (b + 1) * H] = LAMBDA_DEG * DT / 2
        c[oD + b * H:oD + (b + 1) * H] = LAMBDA_DEG * DT / 2
        c[oSB + b] = 6.0
    c[oCur:oCur + H] = LAMBDA_CURT * DT
    for j, F in enumerate(defer_items):
        c[oSF + j] = 45.0 * (1.2 - F['flex'])
        c[oF + j * H:oF + (j + 1) * H] += 0.05 * np.arange(H) * (1 - F['flex'])
    for i, e in enumerate(ev_items):
        c[oS + i] = 260.0 * (0.4 + e['priority'])
        c[oS2 + i] = 60.0 * (0.4 + e['priority'])
        # anti-procrastination tie-break (a receding-horizon controller that is
        # indifferent between equal-price slots would defer forever)
        c[oE + i * H:oE + (i + 1) * H] += 0.045 * (0.5 + e['priority']) * np.arange(H) * DT

    R, C, V, bub = [], [], [], []
    Re, Ce, Ve, beq = [], [], [], []
    r = re_ = 0

    def add(entries, rhs):
        nonlocal r
        for cc, vv in entries:
            R.append(r); C.append(cc); V.append(vv)
        bub.append(rhs); r += 1

    def add_eq(entries, rhs):
        nonlocal re_
        for cc, vv in entries:
            Re.append(re_); Ce.append(cc); Ve.append(vv)
        beq.append(rhs); re_ += 1

    ev_cols = lambda h: [(oE + i * H + h, 1.0) for i in range(nE)]
    for h in range(H):
        ctrl = (ev_cols(h) + [(oC + b * H + h, 1.0) for b in range(nB)]
                + [(oD + b * H + h, -1.0) for b in range(nB)]
                + [(oF + j * H + h, 1.0) for j in range(nF)] + [(oCur + h, 1.0)])
        add(ctrl + [(oZ, -1.0)], -(i50[h] - v50[h]))                     # peak epigraph
        add(ctrl + [(oEp + h, -1.0)], -(i50[h] - v50[h]))                # import epigraph
        add(ctrl, p_headroom[h] - (i90[h] - v10[h]))                     # chance: import headroom
        add([(cc, -vv) for cc, vv in ctrl], p_export[h] + (i10[h] - v90[h]))  # chance: export limit

    for i, e in enumerate(ev_items):                                     # two-tier delivery
        dl = min(e['deadline'], H); dl2 = min(e.get('deadline2', dl), H)
        add([(oE + i * H + h, -DT * EV_ETA) for h in range(dl)] + [(oS + i, -1.0)], -e['kwh_need'])
        add([(oE + i * H + h, -DT * EV_ETA) for h in range(dl2)] + [(oS2 + i, -1.0)], -e['kwh_need'])

    for b, B in enumerate(batt_items):                                   # SOC dynamics (sparse)
        for h in range(H):
            ent = [(oEB + b * H + h, 1.0), (oC + b * H + h, -B['eta'] * DT),
                   (oD + b * H + h, DT / B['eta'])]
            if h > 0:
                ent.append((oEB + b * H + h - 1, -1.0))
                add_eq(ent, 0.0)
            else:
                add_eq(ent, B['e0'])
        add([(oEB + b * H + H - 1, -1.0), (oSB + b, -1.0)], -0.30 * B['cap'])   # terminal reserve

    for j, F in enumerate(defer_items):                                  # appliance energy
        add([(oF + j * H + h, -DT) for h in range(H)] + [(oSF + j, -1.0)], -F['kwh'])

    A = sparse.csr_matrix((V, (R, C)), shape=(r, N))
    Aeq = sparse.csr_matrix((Ve, (Re, Ce)), shape=(max(re_, 0), N)) if re_ else None
    res = linprog(c, A_ub=A, b_ub=np.array(bub), A_eq=Aeq,
                  b_eq=np.array(beq) if re_ else None,
                  bounds=np.column_stack([lb, ub]), method='highs')
    dt = time.perf_counter() - t0
    if not res.success:
        return Plan(status='infeasible' if res.status == 2 else 'failed', H=H,
                    solve_s=dt, n_vars=N, n_rows=r + re_)
    x = res.x
    M = lambda o, n: x[o:o + n * H].reshape(n, H) if n else np.zeros((0, H))
    ev, bc, bd, fl = M(oE, nE), M(oC, nB), M(oD, nB), M(oF, nF)
    cur = x[oCur:oCur + H]
    net = i50 - v50 + ev.sum(0) + bc.sum(0) - bd.sum(0) + fl.sum(0) + cur
    return Plan(status='optimal', H=H, ev=ev, bc=bc, bd=bd, fl=fl, curt=cur, plan_net=net,
                shortfall=x[oS:oS + nE], shortfall2=x[oS2:oS2 + nE], obj=float(res.fun),
                solve_s=dt, n_vars=N, n_rows=r + re_)


# -------------------------------------------------------------- safety layer
@dataclass
class SafetyResult:
    status: str                  # verified | infeasible | solver_failed
    state: dict                  # the AC state of EXACTLY the returned action
    scales: dict                 # group -> scale in [0,1] actually applied
    report: dict                 # constraint_report of `state`
    n_interventions: int = 0
    n_solves: int = 0
    pv_scale: float = 1.0
    log: list = field(default_factory=list)


IMPORT_ORDER = ('appl', 'batt_chg', 'ev', 'protected_ev')   # sacrifice order under import stress
EXPORT_ORDER = ('batt_dis', 'pv')                           # sacrifice order under export stress
LOAD_GROUPS = ('appl', 'batt_chg', 'ev', 'protected_ev')


def compose(base_inj, pv_inj, groups, scales, pv_scale):
    inj = dict(base_inj)
    for k, v in pv_inj.items():
        inj[k] = inj.get(k, 0.0) + pv_scale * v
    for g, gi in groups.items():
        s = scales.get(g, 1.0)
        for k, v in gi.items():
            inj[k] = inj.get(k, 0.0) + s * v
    return inj


def _violated(rep):
    return bool(rep['violations'])


def _stress(st, rep):
    """Direction-aware classification of what is wrong."""
    imp = rep['undervoltage'] or ((rep['tx_overload'] or rep['line_overload']) and st['tx_kw'] >= 0)
    exp = rep['overvoltage'] or ((rep['tx_overload'] or rep['line_overload']) and st['tx_kw'] < 0)
    return imp, exp


def safety_project(grid, base_inj, pv_inj, groups, limits=None, max_rounds=8, bisect=7):
    """
    groups : {name: {(node,phase): kW}} controllable injections. Positive = consumes.
             'appl','batt_chg','ev','protected_ev' consume; 'batt_dis' is NEGATIVE kW.
    pv_inj : {(node,phase): -kW} available PV (after planned curtailment).

    Direction-aware: under IMPORT stress (undervoltage / import overload) only
    consuming groups are reduced -- discharge is never throttled, because that
    would make the overload worse. Under EXPORT stress (overvoltage / reverse
    overload) only discharge and PV are reduced -- loads are never shed.
    Within a direction, groups are sacrificed in priority order (see *_ORDER).
    The state returned is ALWAYS the solve of the final action.
    """
    lim = dict(vlo=VLIM_LO, vhi=VLIM_HI, tx_limit_pct=100.0, line_limit_pct=100.0)
    lim.update(limits or {})
    scales = {g: 1.0 for g in groups}
    pvs = 1.0
    n_solves = 0
    log = []

    def solve(sc, pv):
        nonlocal n_solves
        n_solves += 1
        st = grid.solve(compose(base_inj, pv_inj, groups, sc, pv))
        return st, constraint_report(st, **lim)

    st, rep = solve(scales, pvs)
    touched = set()
    for rnd in range(max_rounds):
        if not rep['solver_ok']:
            break
        if not _violated(rep):
            break
        imp, exp = _stress(st, rep)
        order = []
        if imp:
            order += [g for g in IMPORT_ORDER if g in groups and scales[g] > 0]
        if exp:
            order += [g for g in EXPORT_ORDER if (g == 'pv' and pvs > 0) or (g in groups and scales.get(g, 0) > 0)]
        if not order:
            break
        g = order[0]
        cur = pvs if g == 'pv' else scales[g]

        def with_scale(v):
            if g == 'pv':
                return solve(scales, v)
            sc = dict(scales); sc[g] = v
            return solve(sc, pvs)

        st0, rep0 = with_scale(0.0)
        # largest scale in [0,cur] for which the targeted stress has cleared
        def cleared(rp, s_):
            i2, e2 = _stress(s_, rp) if rp['solver_ok'] else (True, True)
            return rp['solver_ok'] and not ((imp and i2) or (exp and e2))
        if not cleared(rep0, st0):
            newv = 0.0                    # even full removal of this group is not enough
        else:
            lo_v, hi_v = 0.0, cur
            for _ in range(bisect):
                mid = 0.5 * (lo_v + hi_v)
                stm, repm = with_scale(mid)
                if cleared(repm, stm):
                    lo_v = mid
                else:
                    hi_v = mid
            newv = lo_v
        if g == 'pv':
            pvs = newv
        else:
            scales[g] = newv
        touched.add(g)
        log.append(f"{g}->{newv:.3f} ({'import' if imp else ''}{'export' if exp else ''} stress)")
        st, rep = solve(scales, pvs)       # ALWAYS re-solve the exact action we now hold

    if not rep['solver_ok']:
        # fall back to the minimal-risk action: no discretionary flex, no battery
        fb = {g: 0.0 for g in groups}
        st, rep = solve(fb, pvs)
        scales = fb
        touched |= set(groups)
        log.append('solver_failed: fell back to zero flexible dispatch')
        status = 'solver_failed'
    elif _violated(rep):
        status = 'infeasible'
        log.append('infeasible: ' + ','.join(rep['violations']))
    else:
        status = 'verified'
    return SafetyResult(status=status, state=st, scales=scales, report=rep,
                        n_interventions=len(touched), n_solves=n_solves,
                        pv_scale=pvs, log=log)
