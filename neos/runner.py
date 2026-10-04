"""
runner.py -- Closed-loop co-simulation.

Per control step:
  1. exogenous events  (plug-in, DECLARED overrides, departures)   [simulator]
  2. observation       (what the controller is allowed to know)    [information barrier]
  3. policy proposal   (MPC | rule | uncoordinated | future MARL)
  4. DEVICE LIMITS     power AND remaining-energy bounds applied BEFORE any
                       electrical injection is created
  5. SAFETY PROJECTION direction-aware, AC-verified, explicit status
  6. STATE UPDATE      energy advances by the VERIFIED dispatch; nothing is
                       clipped after the fact (a clip counter proves it)
  7. TELEMETRY         constraint status for every step, never just 'peak'
"""
from __future__ import annotations
import time
import numpy as np
from .world import T, DT
from .control import (tariff_vector, EVEnvelope, safety_project, EV_ETA)
from .controllers import (UncoordinatedController, MPCController, RulePolicy, Proposal)
from .grid import VLIM_LO, VLIM_HI
from .flexibility import compute_envelope
from .decision_trace import build_trace

WSTART, WLEN = 48, 96          # 12:00 day-0 -> 12:00 day-1
BATT_PHYS_MIN, BATT_PHYS_MAX = 0.05, 1.00


def defer_window(h):
    return 0, 80


def defer_pref(h):
    return int(24 + (h.defer_window[0] - 20))


def headroom(grid, nb, v_target=0.945):
    """Voltage/thermal-limited import headroom (kW), bisected on the TWIN."""
    keys = [(h.node, h.phase) for h in nb.homes]
    lo, hi = 50.0, 900.0
    for _ in range(18):
        mid = (lo + hi) / 2
        inj = {}
        for k in keys:
            inj[k] = inj.get(k, 0.0) + mid / len(keys)
        st = grid.solve(inj)
        if st['vmin'] < v_target or st['line_loading_max'] > 100:
            hi = mid
        else:
            lo = mid
    return float(min(0.95 * grid.tx_kva * 0.95, lo))


def export_limit(grid, nb, v_target=1.055):
    tot = sum(h.pv_kwp for h in nb.homes) + sum(c.pv_kwp for c in nb.commercials)
    shape = {}
    for h in nb.homes:
        if h.pv_kwp > 0:
            shape[(h.node, h.phase)] = shape.get((h.node, h.phase), 0.0) + h.pv_kwp / tot
    for j, c in enumerate(nb.commercials):
        if c.pv_kwp > 0:
            k0 = (c.node, (j % 3) + 1)
            shape[k0] = shape.get(k0, 0.0) + c.pv_kwp / tot
    lo, hi = 20.0, 900.0
    for _ in range(18):
        mid = (lo + hi) / 2
        st = grid.solve({k: -mid * w for k, w in shape.items()})
        if st['vmax'] > v_target or st['line_loading_max'] > 100:
            hi = mid
        else:
            lo = mid
    return float(min(0.95 * grid.tx_kva, lo))


# ------------------------------------------------------------------ helpers
def _add(d, k, v):
    d[k] = d.get(k, 0.0) + v


def make_controller(name, nb, forecast, grid, flex_homes, mpc_every=2, limits=None):
    if name == 'uncoordinated':
        return UncoordinatedController(nb, {h.id: defer_pref(h) for h in flex_homes})
    if name == 'rule_tou':
        return RulePolicy(nb)
    if name in ('coordinated', 'coordinated_mpc'):
        ph, pe = limits if limits else (headroom(grid, nb), export_limit(grid, nb))
        return MPCController(nb, forecast, ph, pe, WLEN, mpc_every)
    raise ValueError(f'unknown controller {name}')


def run_scenario(nb, grid, data, forecast, mode='coordinated', seed=0, mpc_every=2,
                 controller=None, plant=None, limits=None, safety_limits=None,
                 extra_overrides=None, on_step=None,
                 want_envelope=False, want_trace=False):
    """mode: 'uncoordinated' | 'rule_tou' | 'coordinated' (LP-MPC), or pass `controller`.
    plant : optional second grid that stands for REALITY (twin/plant mismatch).
            Safety verification still uses `grid` (the twin).
    extra_overrides : {ev_id: dict(notice, depart, kind)} user declarations to inject.

    want_envelope / want_trace : opt-in, ADDITIVE outputs (V3 roadmap sections 5
    and 6). Both are READ-ONLY annotations of state this function already
    computes for the control/safety pipeline -- enabling them changes nothing
    about dispatch, safety, or the returned numeric series `L`/`A`; they only
    add `m['envelopes']` / `m['traces']` (lists of plain dicts, one per step)
    to the returned metrics dict. Default False so every existing caller's
    behaviour, return shape and performance are unaffected.
    """
    rng = np.random.default_rng(seed)
    n_steps = WLEN
    tar = tariff_vector(n_steps + 12, offset=WSTART)
    flex_homes = [h for h in nb.homes if h.flex_tolerance > 0.35][:140]
    ctrl = controller or make_controller(mode, nb, forecast, grid, flex_homes,
                                         mpc_every, limits)
    use_groups = ctrl.uses_safety_groups
    evalgrid = plant or grid

    # Envelope needs an import-headroom figure for EVERY controller type, not
    # only MPC (which already tracks one for its own planning). Probed ONCE,
    # read-only, exactly like MPCController's own startup probe -- never
    # re-probed per step, and never fed back into control or safety.
    env_P_head = getattr(ctrl, 'P_head', None)
    if want_envelope and env_P_head is None:
        env_P_head = limits[0] if limits else headroom(grid, nb)
    envelopes, traces = [], []

    ev_soc = {e.id: 0.0 for e in nb.evs}
    ev_soc0 = dict(ev_soc)
    ev_plug = {e.id: False for e in nb.evs}
    ev_sess = {e.id: dict(data['ev'][e.id][0]) for e in nb.evs}
    for eid, ov in (extra_overrides or {}).items():
        ev_sess[eid]['override'] = ov
    ev_env, ev_energy_in = {}, {e.id: 0.0 for e in nb.evs}
    ev_out = {}                                     # departure outcomes
    batt_e = {b.id: b.soc0 * b.capacity_kwh for b in nb.batteries}
    batt_e0 = dict(batt_e)
    batt_flow = {b.id: 0.0 for b in nb.batteries}   # sum eta*c - d/eta  [kWh]
    batt_out = data.get('batt_out') or set()
    defer_rem = {h.id: h.defer_kwh for h in flex_homes}
    defer_rem0 = dict(defer_rem)
    defer_win = {h.id: defer_window(h) for h in flex_homes}
    fh = {h.id: h for h in flex_homes}
    evs = {e.id: e for e in nb.evs}
    bats = {b.id: b for b in nb.batteries}

    inflex, pvagg = data['agg_inflex'], data['agg_pv']
    L = {k: [] for k in ('tx_loading', 'vmin', 'vmax', 'vviol', 'net', 'losses', 'cost', 'curt',
                         'curt_plan', 'curt_prot', 'ev_charge', 'batt', 'defer', 'interv',
                         'pv_avail', 'safety_scale', 'export', 'line_max', 'status_ok',
                         'plant_tx', 'plant_vmin', 'plant_vmax', 'plant_ok', 'solve_ms')}
    status_count = {'verified': 0, 'infeasible': 0, 'solver_failed': 0}
    violation_count = {}
    ledger = []                                     # auditable override ledger
    device_limited_kw = 0.0
    clip_events = 0
    overrides_declared = 0
    t_wall = time.perf_counter()

    for k in range(n_steps):
        t = WSTART + k
        events = []

        # ---- 1. exogenous events -----------------------------------------
        for e in nb.evs:
            s = ev_sess[e.id]; ov = s['override']
            if t == s['arr']:
                ev_plug[e.id] = True
                ev_soc[e.id] = ev_soc0[e.id] = s['soc_arr']
                ev_env[e.id] = EVEnvelope(e, ev_soc[e.id], t, risk_q=0.10, learn_noise=0.08, rng=rng)
                events.append(('arrive', e.id))
            if ov and ev_plug[e.id] and t == ov['notice'] and ev_env[e.id].declared_at is None:
                env = ev_env[e.id]
                env.declare(t, ov['depart'], ov['kind'])
                fz = env.feasibility(ev_soc[e.id], t)
                overrides_declared += 1
                ledger.append(dict(step=k, ev=e.id, event='declared', kind=ov['kind'],
                                   ready_by_step=ov['depart'] - WSTART, feasible=fz['feasible'],
                                   kwh_needed=round(fz['kwh_needed'], 2),
                                   kwh_deliverable=round(fz['kwh_deliverable'], 2),
                                   unavoidable_shortfall_kwh=round(fz['shortfall_kwh'], 3),
                                   soc_reachable=round(fz['soc_reachable'], 3),
                                   status='applied' if fz['feasible'] else 'applied_infeasible'))
                events.append(('declare', e.id))
            dep_eff = ov['depart'] if ov else s['dep']
            if ev_plug[e.id] and t >= dep_eff:
                ev_plug[e.id] = False
                env = ev_env[e.id]
                gap = max(0.0, e.req_soc - ev_soc[e.id]) * e.capacity_kwh
                rec = dict(soc_final=ev_soc[e.id], req=e.req_soc, gap_kwh=gap,
                           met=bool(ev_soc[e.id] >= e.req_soc - 0.02),
                           override=bool(ov), kind=ov['kind'] if ov else None,
                           declared=env.declared_at is not None)
                if ov:
                    rec['available_soc'] = ev_soc[e.id]
                ev_out[e.id] = rec
                events.append(('depart', e.id))

        # ---- 2. observation (information barrier) -----------------------
        obs_ev = {}
        for e in nb.evs:
            if not ev_plug[e.id]:
                continue
            env = ev_env[e.id]
            obs_ev[e.id] = dict(
                soc=ev_soc[e.id], p_max=e.charger_kw, cap=e.capacity_kwh, priority=env.priority,
                need_kwh=env.energy_needed(ev_soc[e.id]),
                need_user_kwh=max(0.0, (e.req_soc - ev_soc[e.id]) * e.capacity_kwh),
                dl_p10_abs=int(env.dep_p10 / DT) + T, dl_p90_abs=int(env.dep_p90 / DT) + T,
                hard=env.hard, ready_by=env.ready_by)
        obs_b = {b.id: dict(e=batt_e[b.id], cap=b.capacity_kwh, p_max=b.p_max, eta=b.eta,
                            available=b.id not in batt_out) for b in nb.batteries}
        obs_f = {i: dict(rem=defer_rem[i], p_max=fh[i].defer_kw, window=defer_win[i],
                         flex=fh[i].flex_tolerance) for i in defer_rem}
        obs = dict(k=k, t=t, hour=((t * DT) % 24), events=events, ev=obs_ev, batt=obs_b,
                   defer=obs_f, tariff_now=float(tar[k]),
                   net_now_kw=float(inflex[t] - pvagg[t]),
                   net_fc_now_kw=float(forecast['inflex']['p50'][k] - forecast['pv']['p50'][k]))

        # ---- 3. proposal -------------------------------------------------
        t0 = time.perf_counter()
        prop: Proposal = ctrl.act(obs)
        L['solve_ms'].append(1000 * (time.perf_counter() - t0))

        # ---- 4. device limits BEFORE injections --------------------------
        ev_p, bc, bd, fl = {}, {}, {}, {}
        prot = {}
        for eid, o in obs_ev.items():
            room = (1.0 - ev_soc[eid]) * o['cap'] / (DT * EV_ETA)       # remaining-energy bound
            if o['hard'] and use_groups:
                want = o['p_max']                                       # explicit requirement
                tgt_room = max(0.0, (evs[eid].req_soc - ev_soc[eid]) * o['cap'] / (DT * EV_ETA))
                p = max(0.0, min(want, o['p_max'], room, tgt_room))
                prot[eid] = p
            else:
                want = prop.ev.get(eid, 0.0)
                p = max(0.0, min(want, o['p_max'], room))
                device_limited_kw += max(0.0, want - p)
                if p > 0:
                    ev_p[eid] = p
        for bid, b in bats.items():
            if bid in batt_out:
                continue
            c_w, d_w = prop.bc.get(bid, 0.0), prop.bd.get(bid, 0.0)
            c_max = max(0.0, (BATT_PHYS_MAX * b.capacity_kwh - batt_e[bid]) / (b.eta * DT))
            d_max = max(0.0, (batt_e[bid] - BATT_PHYS_MIN * b.capacity_kwh) * b.eta / DT)
            c, d = min(c_w, b.p_max, c_max), min(d_w, b.p_max, d_max)
            if c > 0 and d > 0:                                          # no simultaneous c/d
                net = c - d
                c, d = max(net, 0.0), max(-net, 0.0)
            device_limited_kw += max(0.0, c_w - c) + max(0.0, d_w - d)
            if c > 0: bc[bid] = c
            if d > 0: bd[bid] = d
        for fid, f in obs_f.items():
            want = prop.fl.get(fid, 0.0)
            p = max(0.0, min(want, f['p_max'], f['rem'] / DT))
            device_limited_kw += max(0.0, want - p)
            if p > 0:
                fl[fid] = p

        # ---- injections --------------------------------------------------
        base_inj, pv_i = {}, {}
        for i, h in enumerate(nb.homes):
            key = (h.node, h.phase)
            _add(base_inj, key, data['home_load'][i, t])
            if data['pv_home'][i, t] > 0:
                _add(pv_i, key, -data['pv_home'][i, t])
        for j, c in enumerate(nb.commercials):
            for off in range(3):
                _add(base_inj, (c.node, (j + off) % 3 + 1), data['comm_load'][j, t] / 3.0)
            if c.pv_kwp > 0:
                _add(pv_i, (c.node, (j % 3) + 1), -data['pv_comm'][j, t])
        pv_avail = float(-sum(pv_i.values()))
        plan_scale = 1.0
        if prop.curt_kw > 1e-6 and pv_avail > 1e-6:
            plan_scale = float(max(0.0, 1.0 - min(prop.curt_kw, pv_avail) / pv_avail))
        pv_planned = {kk: v * plan_scale for kk, v in pv_i.items()}

        g_ev, g_prot, g_bc, g_bd, g_ap = {}, {}, {}, {}, {}
        for eid, p in ev_p.items():
            _add(g_ev, (evs[eid].node, evs[eid].phase), p)
        for eid, p in prot.items():
            _add(g_prot, (evs[eid].node, evs[eid].phase), p)
        for bid, p in bc.items():
            _add(g_bc, (bats[bid].node, bats[bid].phase), p)
        for bid, p in bd.items():
            _add(g_bd, (bats[bid].node, bats[bid].phase), -p)
        for fid, p in fl.items():
            _add(g_ap, (fh[fid].node, fh[fid].phase), p)
        if use_groups:
            groups = {'ev': g_ev, 'protected_ev': g_prot, 'appl': g_ap,
                      'batt_chg': g_bc, 'batt_dis': g_bd}
        else:                                     # uncoordinated: nothing is controllable
            for g in (g_ev, g_prot, g_bc, g_bd, g_ap):
                for kk, v in g.items():
                    _add(base_inj, kk, v)
            groups = {}

        # ---- 5. safety projection ---------------------------------------
        res = safety_project(grid, base_inj, pv_planned, groups, limits=safety_limits)
        st = res.state
        sc = res.scales
        status_count[res.status] += 1
        for v in res.report['violations']:
            violation_count[v] = violation_count.get(v, 0) + 1
        final_inj = None
        if plant is not None:
            from .control import compose
            final_inj = compose(base_inj, pv_planned, groups, sc, res.pv_scale)
            pst = plant.solve(final_inj)
        else:
            pst = st

        # ---- 6. state update from the VERIFIED dispatch -----------------
        s_ev = sc.get('ev', 1.0) if use_groups else 1.0
        s_pr = sc.get('protected_ev', 1.0) if use_groups else 1.0
        s_bc, s_bd = sc.get('batt_chg', 1.0), sc.get('batt_dis', 1.0)
        s_ap = sc.get('appl', 1.0)
        ev_act = {eid: p * s_ev for eid, p in ev_p.items()}
        for eid, p in prot.items():
            ev_act[eid] = p * s_pr
        for eid, p in ev_act.items():
            dE = p * DT * EV_ETA / evs[eid].capacity_kwh
            if ev_soc[eid] + dE > 1.0 + 1e-9:
                clip_events += 1
            ev_soc[eid] += dE
            ev_energy_in[eid] += p * DT
        bc_act = {b: p * s_bc for b, p in bc.items()}
        bd_act = {b: p * s_bd for b, p in bd.items()}
        for bid in bats:
            dE = (bc_act.get(bid, 0.0) * bats[bid].eta - bd_act.get(bid, 0.0) / bats[bid].eta) * DT
            nxt = batt_e[bid] + dE
            if nxt < BATT_PHYS_MIN * bats[bid].capacity_kwh - 1e-6 or nxt > bats[bid].capacity_kwh + 1e-6:
                clip_events += 1
            batt_e[bid] = nxt
            batt_flow[bid] += dE
        fl_act = {f: p * s_ap for f, p in fl.items()}
        for fid, p in fl_act.items():
            if defer_rem[fid] - p * DT < -1e-9:
                clip_events += 1
            defer_rem[fid] -= p * DT

        ctrl.feedback(st, res)

        # ---- 7. telemetry ------------------------------------------------
        prot_kw = res.pv_scale * plan_scale * pv_avail
        L['tx_loading'].append(pst['tx_loading']); L['vmin'].append(pst['vmin'])
        L['vmax'].append(pst['vmax']); L['vviol'].append(pst['v_violations'])
        L['net'].append(pst['tx_kw']); L['losses'].append(pst['losses_kw'])
        L['line_max'].append(pst['line_loading_max'])
        L['cost'].append(max(pst['tx_kw'], 0) * DT * tar[k])
        L['curt_plan'].append((1 - plan_scale) * pv_avail)
        L['curt_prot'].append(plan_scale * (1 - res.pv_scale) * pv_avail)
        L['curt'].append(L['curt_plan'][-1] + L['curt_prot'][-1])
        L['pv_avail'].append(pv_avail)
        L['export'].append(max(-pst['tx_kw'], 0.0))
        L['ev_charge'].append(sum(ev_act.values()))
        L['batt'].append(sum(bc_act.values()) - sum(bd_act.values()))
        L['defer'].append(sum(fl_act.values()))
        L['interv'].append(res.n_interventions)
        L['safety_scale'].append(min(sc.values()) if sc else 1.0)
        L['status_ok'].append(1.0 if res.status == 'verified' else 0.0)
        L['plant_tx'].append(pst['tx_loading']); L['plant_vmin'].append(pst['vmin'])
        L['plant_vmax'].append(pst['vmax'])
        L['plant_ok'].append(1.0 if (pst['converged'] and pst['finite'] and pst['vmin'] >= VLIM_LO
                                     and pst['vmax'] <= VLIM_HI and pst['tx_loading'] <= 100
                                     and pst['line_loading_max'] <= 100) else 0.0)

        # ---- optional, additive: flexibility envelope + decision trace ---
        if want_envelope:
            ph_now = getattr(ctrl, 'P_head', env_P_head)
            envelopes.append(compute_envelope(
                k, t, obs, pst, ph_now, forecast, mpc_every=mpc_every).to_dict())
        if want_trace:
            fallback_used = bool(prop.info.get('fallback')) if hasattr(prop, 'info') else False
            traces.append(build_trace(
                k, t, obs, forecast, ev_p, prot, bc, bd, fl, res, pst,
                controller_name=ctrl.name, fallback_used=fallback_used).to_dict())

        if on_step is not None:
            on_step(dict(step=k, hour=round(((WSTART + k) * DT) % 24, 2),
                         tx_loading=round(pst['tx_loading'], 2), vmin=round(pst['vmin'], 4),
                         vmax=round(pst['vmax'], 4), net_kw=round(pst['tx_kw'], 1),
                         ev_kw=round(L['ev_charge'][-1], 1), batt_kw=round(L['batt'][-1], 1),
                         defer_kw=round(L['defer'][-1], 1), pv_kw=round(pv_avail, 1),
                         curtailed_kw=round(L['curt'][-1], 1), tariff=float(tar[k]),
                         safety_scale=round(L['safety_scale'][-1], 3),
                         interventions=res.n_interventions, status=res.status,
                         line_max=round(pst['line_loading_max'], 1),
                         headroom_kw=round(getattr(ctrl, 'P_head', 0.0), 1),
                         export_limit_kw=round(getattr(ctrl, 'P_exp', 0.0), 1),
                         evs_plugged=int(sum(ev_plug.values()))))

    # EVs still plugged at end of window: record the outcome
    for e in nb.evs:
        if ev_plug[e.id] and e.id not in ev_out:
            ev_out[e.id] = dict(soc_final=ev_soc[e.id], req=e.req_soc,
                                gap_kwh=max(0.0, e.req_soc - ev_soc[e.id]) * e.capacity_kwh,
                                met=bool(ev_soc[e.id] >= e.req_soc - 0.02), override=False,
                                kind=None, declared=False)
    m, A = _metrics(ctrl, mode if controller is None else ctrl.name, L, nb, evs, ev_out, ev_soc,
                    ev_soc0, ev_energy_in, batt_e, batt_e0, batt_flow, bats, defer_rem,
                    defer_rem0, status_count, violation_count, ledger, clip_events,
                    device_limited_kw, overrides_declared, time.perf_counter() - t_wall, plant)
    if want_envelope:
        m['envelopes'] = envelopes
    if want_trace:
        m['traces'] = traces
    return m, A


def _metrics(ctrl, mode, L, nb, evs, ev_out, ev_soc, ev_soc0, ev_in, batt_e, batt_e0,
             batt_flow, bats, defer_rem, defer_rem0, status_count, violation_count,
             ledger, clip_events, device_limited_kw, n_over, wall_s, plant):
    A = {k: np.array(v, dtype=float) for k, v in L.items()}
    peak = float(A['net'].max())
    served = [r for r in ev_out.values()]
    normal = [r for r in served if not r['override']]
    over = [r for r in served if r['override']]
    # energy audit: SOC change must equal verified energy x efficiency EXACTLY
    ev_resid = max([abs((ev_soc[i] - ev_soc0[i]) * evs[i].capacity_kwh - ev_in[i] * EV_ETA)
                    for i in ev_in if i in ev_out] or [0.0])
    b_resid = max([abs(batt_e[i] - batt_e0[i] - batt_flow[i]) for i in bats] or [0.0])
    m = dict(
        mode=mode,
        peak_tx_loading=float(A['tx_loading'].max()), peak_kw=peak,
        hours_over_100=float((A['tx_loading'] > 100).sum() * DT),
        min_voltage=float(A['vmin'].min()), max_voltage=float(A['vmax'].max()),
        node_hours_v_violation=float(A['vviol'].sum() * DT),
        max_line_loading=float(A['line_max'].max()),
        hours_line_over_100=float((A['line_max'] > 100).sum() * DT),
        energy_cost_inr=float(A['cost'].sum()), demand_charge_inr=peak * 11.7,
        losses_kwh=float(A['losses'].sum() * DT),
        curtailed_kwh=float(A['curt'].sum() * DT),
        curtailed_planned_kwh=float(A['curt_plan'].sum() * DT),
        curtailed_protective_kwh=float(A['curt_prot'].sum() * DT),
        pv_available_kwh=float(A['pv_avail'].sum() * DT),
        export_kwh=float(A['export'].sum() * DT),
        load_factor=float(A['net'].mean() / peak) if peak > 0 else 0.0,
        ev_satisfaction=float(np.mean([r['met'] for r in normal])) if normal else 1.0,
        ev_unserved_kwh=float(sum(r['gap_kwh'] for r in normal)),
        n_ev_sessions=len(normal),
        user_overrides=n_over,
        override_met=int(sum(r['met'] for r in over)),
        override_sessions=len(over),
        override_shortfall_kwh=float(sum(r['gap_kwh'] for r in over)),
        override_unavoidable_kwh=float(sum(x.get('unavoidable_shortfall_kwh', 0.0) for x in ledger)),
        override_avoidable_kwh=float(max(0.0, sum(r['gap_kwh'] for r in over)
                                         - sum(x.get('unavoidable_shortfall_kwh', 0.0) for x in ledger))),
        ev_missed=[dict(ev=i, gap_kwh=round(r['gap_kwh'], 2), soc=round(r['soc_final'], 3),
                        req=round(r['req'], 3), override=r['override']) for i, r in ev_out.items() if not r['met']],
        override_ledger=ledger,
        safety_interventions=int(A['interv'].sum()),
        constraint_status_steps=status_count,
        steps_verified_pct=100.0 * status_count['verified'] / max(1, sum(status_count.values())),
        violation_counts=violation_count,
        device_limited_kw_total=float(device_limited_kw),
        energy_clip_events=int(clip_events),
        energy_audit=dict(ev_residual_kwh=float(ev_resid), battery_residual_kwh=float(b_resid)),
        ev_final_soc=[round(float(v), 3) for v in ev_soc.values()],
        wall_s=round(wall_s, 2),
    )
    if hasattr(ctrl, 'solve_times') and ctrl.solve_times:
        st = np.array(ctrl.solve_times)
        m['mpc'] = dict(n_solves=int(len(st)), mean_ms=float(1000 * st.mean()),
                        p95_ms=float(1000 * np.percentile(st, 95)),
                        max_ms=float(1000 * st.max()), status=ctrl.plan_status,
                        fallback_steps=int(ctrl.n_fallback_steps))
    if plant is not None:
        m['plant_unsafe_steps'] = int((A['plant_ok'] == 0).sum())
    m['total_cost_inr'] = m['energy_cost_inr'] + m['demand_charge_inr']
    m['renewable_utilisation'] = 1 - m['curtailed_kwh'] / max(1e-6, m['pv_available_kwh'])
    m['pv_self_consumption'] = 1 - (m['export_kwh'] + m['curtailed_kwh']) / max(1e-6, m['pv_available_kwh'])
    return m, A
