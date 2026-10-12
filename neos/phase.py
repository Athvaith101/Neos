"""
phase.py -- phase-aware transformer view (master spec section 11).

A three-phase transformer is NOT one percentage. This module turns the
per-phase kVA that BOTH grid backends now report into the operator-facing
reading: per-phase loading, which phase is the constraint, and an imbalance
indicator.

Boundary stated in the spec and kept here: aggregate transformer measurements
do NOT identify which household or appliance created a load. Nothing in this
module attributes load to a device, and automatic household phase switching is
out of scope.
"""
from __future__ import annotations

PHASE_NAMES = ('A', 'B', 'C')
WARN_PCT = 85.0          # 'approaching threshold'
LIMIT_PCT = 100.0        # per-phase rating = one third of the transformer kVA


def phase_status(pct):
    if pct != pct:                       # NaN: solver failed, never report 'normal'
        return 'unknown'
    if pct > LIMIT_PCT:
        return 'over'
    return 'stressed' if pct >= WARN_PCT else 'normal'


def phase_view(st):
    """Operator reading of one solved state (either backend)."""
    pct = list(st['tx_phase_pct'])
    phases = [dict(phase=PHASE_NAMES[i], loading_pct=pct[i], kva=st['tx_phase_kva'][i],
                   status=phase_status(pct[i])) for i in range(3)]
    finite = [p for p in pct if p == p]
    worst = max(range(3), key=lambda i: pct[i] if pct[i] == pct[i] else -1.0) if finite else None
    if worst is None:
        note = 'Solver did not converge: phase loading unknown.'
    elif phases[worst]['status'] == 'normal':
        note = 'All phases within normal range.'
    else:
        note = f"Phase {PHASE_NAMES[worst]} {phases[worst]['status']} ({pct[worst]:.0f}% of its rating)."
    return dict(phases=phases, worst_phase=None if worst is None else PHASE_NAMES[worst],
                imbalance=st['imbalance'], constraint=note,
                attribution='Phase totals only. Aggregate measurements cannot identify which '
                            'household or appliance caused the load.')
