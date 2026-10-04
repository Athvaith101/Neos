"""
phase.py -- Phase-aware transformer gateway.

Consumes per-phase telemetry (from the twin or, in a pilot, Modbus/HES/MDMS
registers) and reports voltage, current, PF, per-phase loading, imbalance and
how long the imbalance has persisted.

Imbalance indicator (shown in evidence metadata):
        imb = max_p |I_p - mean(I)| / mean(I)
Persistence: consecutive steps with imb > IMB_THRESHOLD (assumed 0.10).

Allowed response: RECOMMEND delaying/shifting controllable load on the stressed
phase. NOT allowed / not claimed: appliance identification from aggregate data,
automatic household phase switching, protection certification.
"""
from __future__ import annotations
import numpy as np

IMB_THRESHOLD = 0.10
MIN_LOAD_FRAC = 0.05
FORMULA = ("imbalance = max_p |I_p - mean(I)| / mean(I), evaluated only when mean(I) >= 5 % of the "
           "transformer's rated current (otherwise reported as 0: the ratio is meaningless at light/cancelling load)")
NOT_CLAIMED = ["identification of individual appliances from aggregate transformer data",
               "automatic household phase switching", "protection certification"]


def imbalance(i_phase, i_rated=None):
    i = np.asarray(i_phase, float)
    m = i.mean()
    if m <= 1e-9 or (i_rated is not None and m < MIN_LOAD_FRAC * i_rated):
        return 0.0
    return float(np.max(np.abs(i - m)) / m)


class PhaseGateway:
    def __init__(self, threshold=IMB_THRESHOLD, dt_h=0.25, tx_kva=630.0):
        self.threshold, self.dt_h = threshold, dt_h
        self.i_rated = tx_kva * 1000.0 / (3 ** 0.5 * 433.0)
        self.run = 0
        self.max_run = 0
        self.history = []

    def update(self, st):
        """st: solved state with ph_* fields. Returns the gateway panel dict."""
        if 'ph_i' not in st:
            return None
        imb = imbalance(st['ph_i'], self.i_rated)
        self.run = self.run + 1 if imb > self.threshold else 0
        self.max_run = max(self.max_run, self.run)
        panel = dict(
            voltage_pu=dict(zip('ABC', np.round(st['ph_v'], 4).tolist())),
            current_a=dict(zip('ABC', np.round(st['ph_i'], 1).tolist())),
            power_factor=dict(zip('ABC', np.round(st['ph_pf'], 3).tolist())),
            loading_pct=dict(zip('ABC', np.round(st['ph_loading'], 1).tolist())),
            transformer_kva=round(st['tx_kva'], 1), imbalance=round(imb, 4),
            imbalance_persistence_h=round(self.run * self.dt_h, 2),
            stressed_phase='ABC'[int(np.argmax(st['ph_loading']))])
        self.history.append(panel)
        return panel

    def recommend(self, panel, controllable_by_phase):
        """controllable_by_phase: {phase(1..3): kW of controllable load/charging}.
        Returns a recommendation (never an actuation)."""
        if panel is None or panel['imbalance'] <= self.threshold:
            return dict(action='none', reason='phase currents within the imbalance threshold')
        ph = 'ABC'.index(panel['stressed_phase']) + 1
        kw = controllable_by_phase.get(ph, 0.0)
        if kw <= 0:
            return dict(action='alert_only', phase=panel['stressed_phase'],
                        reason='phase stressed but no controllable load is registered on it')
        return dict(action='recommend_delay_or_shift', phase=panel['stressed_phase'],
                    controllable_kw=round(kw, 1),
                    reason=f"phase {panel['stressed_phase']} loads {panel['loading_pct'][panel['stressed_phase']]:.0f} % "
                           f"of its share; imbalance {panel['imbalance']:.2f}",
                    note='recommendation only; no household switching is implied')
