"""
kits.py -- Feeder fingerprint and kit selector.

One platform, three deployment kits. The selector maps a measured/simulated feeder
fingerprint to a RECOMMENDED kit with transparent rules; every threshold below is an
ASSUMPTION (not calibrated on real feeders) and is displayed with the result.

    urban      affluent / high-DER: PV + EV + home batteries, evening peaks, phase stress
    low_income community-hub resilience: frequent outages, tiny discretionary load, no PV/EV
    rural      weak tail-end feeder, motor loads (pumps/mills), long outages, water/cold storage

Evidence for one kit is NEVER reused for another: every record carries its kit and
the evidence gate (evidence.py) rejects mismatches.
"""
from __future__ import annotations

KITS = ('urban', 'low_income', 'rural')
KIT_TITLES = {'urban': 'Urban / affluent (high-DER)', 'low_income': 'Low-income resilience hub',
              'rural': 'Rural mixed-use microgrid'}

THRESHOLDS = {  # ASSUMED, displayed with every recommendation
    'rural_motor_share': 0.25,           # pumps + mills as a share of peak demand
    'rural_tail_v_pu': 0.97,             # tail-end voltage under normal peak at/below this
    'lowincome_outages_per_year': 30,    # frequent outages
    'lowincome_der_per_home': 0.05,      # (PV + EV + battery count)/homes at or below this
    'urban_peak_pu': 0.85,               # evening peak at/above this fraction of rating
    'urban_ev_per_home': 0.10,
}


def fingerprint(**kw):
    """Normalise a fingerprint dict. Unknown/missing -> None (selector treats as unknown)."""
    keys = ['midday_reverse_flow_pu', 'evening_peak_pu', 'motor_load_share', 'outages_per_year',
            'outage_mean_h', 'tail_end_voltage_pu', 'phase_imbalance', 'ev_per_home',
            'der_per_home', 'controllable_resources']
    return {k: kw.get(k) for k in keys}


def select_kit(fp):
    """Returns dict(recommended, scores, reasons, thresholds, caveat). Rules, not a trained model."""
    T = THRESHOLDS
    sc = {k: 0.0 for k in KITS}
    why = {k: [] for k in KITS}

    def add(kit, pts, text):
        sc[kit] += pts; why[kit].append(text)

    g = lambda k: fp.get(k)
    if g('motor_load_share') is not None and g('motor_load_share') >= T['rural_motor_share']:
        add('rural', 2, f"motor loads are {g('motor_load_share'):.0%} of peak (>= {T['rural_motor_share']:.0%})")
    if g('tail_end_voltage_pu') is not None and g('tail_end_voltage_pu') <= T['rural_tail_v_pu']:
        add('rural', 1.5, f"tail-end voltage {g('tail_end_voltage_pu'):.3f} pu <= {T['rural_tail_v_pu']} (weak feeder)")
    if g('outage_mean_h') is not None and g('outage_mean_h') >= 5:
        add('rural', 1, f"long outages (mean {g('outage_mean_h'):.1f} h)")
    if g('outages_per_year') is not None and g('outages_per_year') >= T['lowincome_outages_per_year']:
        add('low_income', 2, f"{g('outages_per_year'):.0f} outages/year (>= {T['lowincome_outages_per_year']})")
    if g('der_per_home') is not None and g('der_per_home') <= T['lowincome_der_per_home']:
        add('low_income', 1.5, f"almost no home DER ({g('der_per_home'):.2f} per home)")
    if g('motor_load_share') is not None and g('motor_load_share') < 0.05:
        add('low_income', 0.5, 'negligible motor load')
    if g('evening_peak_pu') is not None and g('evening_peak_pu') >= T['urban_peak_pu']:
        add('urban', 1.5, f"evening peak {g('evening_peak_pu'):.0%} of rating (>= {T['urban_peak_pu']:.0%})")
    if g('ev_per_home') is not None and g('ev_per_home') >= T['urban_ev_per_home']:
        add('urban', 1.5, f"EV penetration {g('ev_per_home'):.0%} of homes")
    if g('midday_reverse_flow_pu') is not None and g('midday_reverse_flow_pu') >= 0.10:
        add('urban', 1, f"midday reverse flow {g('midday_reverse_flow_pu'):.0%} of rating")
    if g('phase_imbalance') is not None and g('phase_imbalance') >= 0.10:
        add('urban', 0.5, f"phase imbalance {g('phase_imbalance'):.2f}")
    best = max(sc, key=lambda k: sc[k])
    ranked = sorted(sc.items(), key=lambda kv: -kv[1])
    tie = len(ranked) > 1 and ranked[0][1] == ranked[1][1]
    return dict(recommended=None if tie or sc[best] == 0 else best, scores=sc, reasons=why,
                thresholds=T, tie=tie,
                caveat='Rule-based recommendation from assumed thresholds. A utility engineer must confirm '
                       'the kit from real feeder data; the selector does not replace a feeder study.')
