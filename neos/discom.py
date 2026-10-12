"""
discom.py -- DISCOM flexibility interface (master spec section 20).

The utility asks for flexibility; NEOS answers ACCEPT / MODIFY / DEFER / REJECT
from what it can actually deliver, never from what was asked for.

  request    : reduce net import by `kw` for `n_steps` control steps from
               `start_step` (the evening-peak-relief case), notified `notice_steps`
               before it begins.
  capability : at the moment the request ARRIVES, the optimiser itself is asked
               for the largest SUSTAINED cut it can deliver with zero unmet slack
               (MPCController.cut_capability) for the requested window and for
               windows up to 2 h later. This is state-, forecast-, EV-requirement-
               and reserve-aware.

FINDING that motivated this design (recorded in AUDIT-STATUS.md): the Flexibility
Envelope reported ~99 kW of "safe flexibility" in an evening window in which the
plan was already discharging every battery and charging no EV. Envelope flexibility
is capacity to MOVE load; import can only be reduced by what is currently drawn or
by extra discharge. So the envelope is shown on the console but is NOT used to
decide a request.

Delivery is MEASURED, not assumed: `emulate()` runs an unrequested baseline and a
request run through the same controller, safety layer and AC power flow, and
reports delivered = baseline net import - request net import per step, plus any
rebound after the window. Settlement is explicitly NOT modelled: the spec lists
baseline definition, metering, utility agreement and regulatory approval as later
steps.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
import numpy as np

ACCEPT, MODIFY, DEFER, REJECT = 'ACCEPT', 'MODIFY', 'DEFER', 'REJECT'
MIN_USEFUL_KW = 5.0           # below this a response is not worth dispatching
MODIFY_FLOOR = 0.25           # accept a reduced amount if >= 25 % of what was asked
MAX_DEFER_STEPS = 8           # look at most 2 h ahead for a better window
SAFETY_MARGIN = 0.90          # promise 90 % of the probed capability (forecast error)


@dataclass
class DiscomRequest:
    start_step: int           # control step (0..95) the response must begin
    n_steps: int              # duration in control steps (15 min each)
    kw: float                 # requested reduction of net import, kW
    notice_steps: int = 8     # how long before start_step the request arrives (default 2 h)
    direction: str = 'reduce_import'
    id: str = 'req-1'

    @property
    def arrival_step(self):
        return max(0, self.start_step - self.notice_steps)


@dataclass
class Decision:
    action: str
    requested_kw: float
    accepted_kw: float
    start_step: int           # may differ from the request when DEFER
    n_steps: int
    probed_capability_kw: float   # optimiser-probed sustained cut for the decided window
    reason: str

    def to_dict(self):
        return asdict(self)


def decide(req: DiscomRequest, capability):
    """capability[s] = probed sustained cut (kW) for a window starting s steps after
    the requested start (None if beyond the horizon). Index 0 = requested window."""
    def rej(msg, cap0=0.0):
        return Decision(REJECT, req.kw, 0.0, req.start_step, req.n_steps, cap0, msg)
    if req.direction != 'reduce_import':
        return rej(f"direction '{req.direction}' is not supported by this interface")
    if req.kw <= 0 or req.n_steps <= 0 or not capability or capability[0] is None:
        return rej('request is empty, malformed or outside the planning horizon')
    c0 = float(capability[0]) * SAFETY_MARGIN
    if c0 >= req.kw:
        return Decision(ACCEPT, req.kw, req.kw, req.start_step, req.n_steps, float(capability[0]),
                        f'The optimiser can sustain {capability[0]:.0f} kW over the window; '
                        f'{req.kw:.0f} kW is within the {100 * SAFETY_MARGIN:.0f}% of that NEOS is willing to promise.')
    if c0 >= max(MIN_USEFUL_KW, MODIFY_FLOOR * req.kw):
        amt = float(np.floor(c0))
        return Decision(MODIFY, req.kw, amt, req.start_step, req.n_steps, float(capability[0]),
                        f'Only {amt:.0f} kW can be sustained over the requested window '
                        f'(probed capability {capability[0]:.0f} kW, {100 * SAFETY_MARGIN:.0f}% promised).')
    for sh in range(1, min(len(capability), MAX_DEFER_STEPS + 1)):
        cs = capability[sh]
        if cs is not None and cs * SAFETY_MARGIN >= req.kw:
            return Decision(DEFER, req.kw, req.kw, req.start_step + sh, req.n_steps, float(cs),
                            f'Not enough at the requested time ({capability[0]:.0f} kW probed); '
                            f'{req.kw:.0f} kW is deliverable {sh * 15} min later.')
    return rej(f'Probed sustained capability is {capability[0]:.0f} kW; nothing useful can be '
               f'promised without breaching a constraint, an EV requirement or the battery reserve.',
               float(capability[0]))


def emulate(run_fn, req: DiscomRequest):
    """Baseline run (+ read-only capability probe at the request's arrival) ->
    decision -> request run -> MEASURED delivery.

    run_fn(net_cut=None, probe=None) -> (metrics, series); series['net'] is the
    transformer net import (kW) per step.
    """
    probe = dict(k=req.arrival_step, start=req.start_step, n=req.n_steps, max_shift=MAX_DEFER_STEPS)
    base_m, base_s = run_fn(None, probe)
    net0 = np.asarray(base_s['net'], float)
    dec = decide(req, base_m.get('cut_capability'))
    out = dict(request=asdict(req), decision=dec.to_dict(), baseline=_summ(base_m, net0),
               capability_by_shift_kw=base_m.get('cut_capability'))
    if dec.action == REJECT or dec.accepted_kw <= 0:
        out.update(delivered=None, actual=out['baseline'], window=None,
                   note='Request rejected: the actual run equals the baseline by construction.')
        return out
    a, b = dec.start_step, dec.start_step + dec.n_steps
    cut = np.zeros(len(net0))
    cut[a:b] = dec.accepted_kw                         # the promised reduction, vs the unrequested plan
    act_m, act_s = run_fn(cut, None)
    net1 = np.asarray(act_s['net'], float)
    delivered = net0[a:b] - net1[a:b]
    post = slice(b, min(b + MAX_DEFER_STEPS, len(net0)))     # the 2 h after the window
    rebound_kw = float(np.max(net1[post]) - np.max(net0[post])) if len(net0[post]) else 0.0
    out.update(
        actual=_summ(act_m, net1),
        delivered=dict(mean_kw=float(np.mean(delivered)), min_kw=float(np.min(delivered)),
                       per_step_kw=[float(x) for x in delivered],
                       delivery_ratio=float(np.mean(delivered) / dec.accepted_kw),
                       steps_meeting_promise=int(np.sum(delivered >= 0.9 * dec.accepted_kw)),
                       n_steps=int(b - a),
                       shortfall_kwh=float(np.sum(np.maximum(dec.accepted_kw - delivered, 0.0)) * 0.25),
                       energy_shifted_kwh=float(np.sum(delivered) * 0.25)),
        net_baseline_kw=[float(x) for x in net0], net_actual_kw=[float(x) for x in net1],
        window=[int(a), int(b)],
        rebound=dict(post_window_peak_change_kw=rebound_kw,
                     overall_peak_change_kw=float(np.max(net1) - np.max(net0))))
    return out


def _summ(m, net):
    return dict(peak_kw=float(np.max(net)), peak_tx_loading=m['peak_tx_loading'],
                peak_phase_loading=m['peak_phase_loading'], min_voltage=m['min_voltage'],
                ev_satisfaction=m['ev_satisfaction'], ev_unserved_kwh=m['ev_unserved_kwh'],
                energy_cost_inr=m['energy_cost_inr'], steps_verified_pct=m['steps_verified_pct'],
                safety_interventions=m['safety_interventions'])
