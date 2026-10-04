"""
flexibility.py -- the Flexibility Envelope (V3 roadmap, section 5).

PURPOSE: expose "how much safely mobilisable capability does this
neighbourhood have right now", as a single estimate a DISCOM-facing view or
a future dispatcher could read, instead of forcing them to re-derive it from
raw per-device state.

DESIGN RULE (why this file is thin): every quantity here is read off state
the simulator ALREADY computed and already trusts -- the controller's own
adaptive import/export headroom, the already-calibrated forecast quantiles,
and the exact per-device physical bounds `runner.py` uses to limit actions
BEFORE they become electrical injections. This module does not re-run the
optimiser, re-solve the power flow, or introduce a second source of truth for
any of those numbers. It only combines them into the shape the roadmap asks
for, and is therefore only as trustworthy as the pieces it reads -- which are
the ones already covered by the 47-test suite and the OpenDSS verification.

THE GOVERNING RULE, STATED IN CODE: the returned `status` is always the
literal string "ESTIMATED". Nothing produced here is a dispatch guarantee.
See `envelope['note']` for why, and `test_flexibility.py` for the contract
that no caller is allowed to treat it as one (no field is named "guaranteed",
no field claims more precision than its heuristic actually supports).
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from .world import DT
from .control import EV_ETA

BATT_SOC_MIN_RESERVE = 0.15   # same floor solve_mpc's own LP already enforces
BATT_SOC_MAX = 0.95           # same ceiling solve_mpc's own LP already enforces


@dataclass
class Envelope:
    step: int
    hour: float
    tx_loading_pct: float
    tx_headroom_kw: float
    protected_reserve_floor_pct: float
    avg_battery_soc_pct: float
    batteries_available: int
    forecast_uncertainty_kw: float
    ev_flexibility_kw: float
    battery_flexibility_kw: float
    load_flexibility_kw: float
    safe_flexibility_kw: float
    duration_min: float
    duration_note: str
    confidence_pct: float
    confidence_note: str
    status: str = "ESTIMATED"
    note: str = ("An estimate of safely mobilisable capability under the SAME "
                "constraints the safety layer already enforces (battery "
                "reserve floor, EV charger/energy limits, transformer "
                "headroom, forecast uncertainty). It is not a contractual "
                "delivery commitment and can be revised at the next replan.")

    def to_dict(self):
        return asdict(self)


def _ev_flexibility(obs_ev):
    """Sheddable/deferrable EV charging capability right now: bounded by each
    plugged vehicle's charger rating and remaining battery room, EXCLUDING
    vehicles with a declared hard requirement -- those are the last thing the
    safety layer sheds (see control.IMPORT_ORDER), so counting them as
    'available flexibility' would overstate what can actually be mobilised
    without breaking a promise already made to a person."""
    total = 0.0
    for o in obs_ev.values():
        if o.get('hard'):
            continue
        room_kw = (1.0 - o['soc']) * o['cap'] / (DT * EV_ETA)
        total += max(0.0, min(o['p_max'], room_kw))
    return total


def _battery_flexibility(obs_batt, soc_min=BATT_SOC_MIN_RESERVE):
    """Discharge capability available ABOVE the protected reserve floor. This
    is deliberately the same soc_min the LP-MPC's own bounds already use
    (see controllers.MPCController._build), so the envelope never claims more
    battery capability than the optimiser itself is willing to draw on."""
    total, n_avail, soc_sum = 0.0, 0, 0.0
    for o in obs_batt.values():
        if not o.get('available'):
            continue
        n_avail += 1
        soc_sum += o['e'] / o['cap'] if o['cap'] > 0 else 0.0
        headroom_kwh = max(0.0, o['e'] - soc_min * o['cap'])
        p_kw = headroom_kwh * o['eta'] / DT
        total += max(0.0, min(o['p_max'], p_kw))
    avg_soc_pct = 100.0 * soc_sum / n_avail if n_avail else 0.0
    return total, n_avail, avg_soc_pct


def _load_flexibility(obs_defer):
    """Deferrable-appliance load currently inside its allowed window."""
    return sum(max(0.0, min(f['p_max'], f['rem'] / DT))
              for f in obs_defer.values() if f['rem'] > 1e-6)


def compute_envelope(k, t, obs, pst, P_head, forecast,
                     mpc_every=2, soc_min=BATT_SOC_MIN_RESERVE):
    """
    k, t        : control-step index and absolute simulation step
    obs         : the SAME observation dict the controller was given this step
                 (information barrier already applied -- no realised-future
                 leakage reaches this function either)
    pst         : the VERIFIED (post safety-projection) plant state for this
                 step, i.e. res.state from safety_project, or the equivalent
                 'pst' local in runner.py's loop
    P_head      : the controller's current import-headroom limit (kW). For an
                 MPCController this is its adaptively-tracked `.P_head`; for
                 rule/uncoordinated controllers, pass the twin-probed static
                 value computed once outside the loop (see runner.headroom).
    forecast    : the day-ahead forecast dict (same object every controller
                 already receives), used ONLY to read the P10/P50/P90 net
                 demand already computed for this exact step index.
    mpc_every   : the controller's replan cadence, if known, else leave at
                 the default -- used only to label `duration_note` honestly.
    """
    ev_flex = _ev_flexibility(obs['ev'])
    batt_flex, n_batt, avg_soc = _battery_flexibility(obs['batt'], soc_min)
    load_flex = _load_flexibility(obs['defer'])

    net_p10 = float(forecast['inflex']['p10'][k] - forecast['pv']['p90'][k])
    net_p50 = float(forecast['inflex']['p50'][k] - forecast['pv']['p50'][k])
    net_p90 = float(forecast['inflex']['p90'][k] - forecast['pv']['p10'][k])
    uncertainty_kw = max(0.0, 0.5 * (net_p90 - net_p10))

    tx_headroom_kw = max(0.0, P_head - max(pst['tx_kw'], 0.0))
    # Conservative by construction: bounded by BOTH what the transformer can
    # still take (after derating for forecast uncertainty, the same
    # chance-constraint philosophy solve_mpc's own headroom constraint uses)
    # AND by what flexible resource physically exists to mobilise. Taking the
    # larger of the two would mean claiming capability that either the grid
    # or the devices could not actually deliver.
    resource_total = ev_flex + batt_flex + load_flex
    grid_limited = max(0.0, tx_headroom_kw - uncertainty_kw)
    safe_flex = max(0.0, min(grid_limited, resource_total))

    duration_min = mpc_every * DT * 60.0
    duration_note = (f"Valid until the next scheduled replan (every {mpc_every} "
                     f"control step(s) = {duration_min:.0f} min), or sooner if a "
                     f"new event (arrival, departure, declared requirement) forces "
                     f"an earlier replan.")

    # HEURISTIC, not a calibrated probability: tighter forecast uncertainty
    # relative to the demand level this step reports higher confidence.
    # Floored at 50% (never claim a coin-flip is confident) and capped at 97%
    # (never claim near-certainty from a synthetic forecast). See
    # test_flexibility.py::test_confidence_is_bounded_and_monotonic.
    rel_unc = uncertainty_kw / max(abs(net_p50), 1.0)
    confidence_pct = float(max(50.0, min(97.0, 100.0 - 200.0 * rel_unc)))
    confidence_note = ("Heuristic from forecast-uncertainty-to-demand ratio at this "
                       "step (not a calibrated probability; see forecast_benchmark "
                       "in results.json for the forecaster's actual measured "
                       "coverage).")

    env = Envelope(
        step=k, hour=round(obs['hour'], 2),
        tx_loading_pct=round(float(pst['tx_loading']), 2),
        tx_headroom_kw=round(tx_headroom_kw, 1),
        protected_reserve_floor_pct=round(100.0 * soc_min, 1),
        avg_battery_soc_pct=round(avg_soc, 1),
        batteries_available=n_batt,
        forecast_uncertainty_kw=round(uncertainty_kw, 1),
        ev_flexibility_kw=round(ev_flex, 1),
        battery_flexibility_kw=round(batt_flex, 1),
        load_flexibility_kw=round(load_flex, 1),
        safe_flexibility_kw=round(safe_flex, 1),
        duration_min=round(duration_min, 1), duration_note=duration_note,
        confidence_pct=round(confidence_pct, 1), confidence_note=confidence_note,
    )
    return env
