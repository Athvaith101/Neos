"""
envelope.py -- the Flexibility Envelope: a DISCOM-facing, physics- and
reserve-constrained estimate of what a transformer-level fleet could add or shed.

    ESTIMATED AVAILABLE FLEXIBILITY -- NOT GUARANTEED DELIVERY.

Available discharge power (kW) is the minimum of
    converter/device limit, energy limit over the stated duration, and the amount
    that does not eat into the PROTECTED RESILIENCE RESERVE,
then (optionally) clipped by a physics check on the twin. Confidence is a
transparent formula, not a calibrated probability:

    confidence = Phi( margin_kw / sigma_kw ) clipped to [0.05, 0.99]
      margin_kw = available_kw - expected_net_demand_stress_kw
      sigma_kw  = forecast uncertainty (P90-P10)/2.563 of the aggregate net load

A real utility use would require a baseline, measurement and contract framework;
none exists here.
"""
from __future__ import annotations
import math

NOTICE = "Estimated available flexibility \u2014 not guaranteed delivery."


def _phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def battery_fleet_envelope(batteries, reserve_frac, now_h, ttl_h=0.5, duration_h=1.0,
                           sigma_kw=0.0, stress_kw=0.0, config_id="unspecified",
                           transformer_headroom_kw=None, phase_note=None):
    """batteries: iterable of dict(e_kwh, cap_kwh, soh, p_max_kw, eta, soc_min, available).
    reserve_frac: SOC fraction protected for resilience (fixed, or from reserve.py)."""
    dis_kw = chg_kw = e_avail = e_room = 0.0
    n = 0
    for b in batteries:
        if not b.get('available', True):
            continue
        n += 1
        usable_floor = max(b['soc_min'], reserve_frac) * b['cap_kwh'] * b.get('soh', 1.0)
        e_above = max(0.0, b['e_kwh'] - usable_floor)
        e_below = max(0.0, 0.95 * b['cap_kwh'] * b.get('soh', 1.0) - b['e_kwh'])
        dis_kw += min(b['p_max_kw'], e_above * b['eta'] / duration_h)
        chg_kw += min(b['p_max_kw'], e_below / (b['eta'] * duration_h))
        e_avail += e_above
        e_room += e_below
    if transformer_headroom_kw is not None:
        chg_kw = min(chg_kw, max(0.0, transformer_headroom_kw))
    sig = max(sigma_kw, 1e-6)
    conf_d = min(0.99, max(0.05, _phi((dis_kw - stress_kw) / sig))) if sigma_kw > 0 else None
    return dict(
        notice=NOTICE, config_id=config_id, issued_at_h=now_h, expires_at_h=now_h + ttl_h,
        n_resources=n, reserve_protected_frac=reserve_frac,
        discharge=dict(direction='discharge', kw=round(dis_kw, 2), duration_h=duration_h,
                       confidence=None if conf_d is None else round(conf_d, 3),
                       energy_above_reserve_kwh=round(e_avail, 2)),
        charge=dict(direction='charge', kw=round(chg_kw, 2), duration_h=duration_h,
                    confidence=None, headroom_kwh=round(e_room, 2)),
        constraint=dict(transformer_headroom_kw=transformer_headroom_kw, phase=phase_note),
        confidence_formula='Phi((available_kw - stress_kw)/sigma_kw); heuristic, not calibrated')


def physics_clip(grid, base_inj, node_phase_share, kw, sign=-1, step=0.5, limits=None):
    """Largest offered kW (<= kw) whose dispatch the AC twin still verifies. `sign`
    -1 = discharge/export-like, +1 = charge/import-like. node_phase_share is
    {(node,phase): weight} summing to 1."""
    from .grid import constraint_report
    lo, hi = 0.0, float(kw)
    def ok(x):
        inj = dict(base_inj)
        for k, w in node_phase_share.items():
            inj[k] = inj.get(k, 0.0) + sign * x * w
        return constraint_report(grid.solve(inj), **(limits or {}))['ok']
    if not ok(0.0):
        return 0.0            # the base state is already violating: offer nothing
    if ok(hi):
        return hi
    for _ in range(14):
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if ok(mid) else (lo, mid)
    return lo
