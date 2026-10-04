"""
telemetry.py -- Smart-meter / gateway telemetry layer and state estimation.

Data architecture rules (from the specification):
  * One three-phase transformer meter measures all three phases; a single-phase
    service uses a single-phase meter. NEOS stays useful WITHOUT household-meter data.
  * Only AGGREGATE telemetry and REGISTERED-device data are used. Appliances are
    never identified from aggregate meter data.
  * Measurement does not actuate: control needs an approved controllable circuit,
    limiter, charger/inverter or voluntary participation.
  * In a pilot the gateway could read Modbus/HES/MDMS registers; here the stream is
    SIMULATED from the twin with noise, quantisation, latency and dropouts.

StateEstimator: scalar Kalman filter per channel with staleness handling. When no
fresh sample arrives, variance grows and the estimate is flagged stale instead of
being silently held.
"""
from __future__ import annotations
import numpy as np

REGISTER_MAP = {
    "tx_kw_a": "Phase A active power (kW)", "tx_kw_b": "Phase B active power (kW)",
    "tx_kw_c": "Phase C active power (kW)", "v_a": "Phase A voltage (pu)",
    "v_b": "Phase B voltage (pu)", "v_c": "Phase C voltage (pu)",
    "i_a": "Phase A current (A)", "i_b": "Phase B current (A)", "i_c": "Phase C current (A)",
    "pf_a": "Phase A power factor", "pf_b": "Phase B power factor", "pf_c": "Phase C power factor",
}


class RegisterStream:
    def __init__(self, seed=0, noise_frac=0.01, quant=0.1, latency_steps=0, p_drop=0.0, bad_p=0.0):
        self.rng = np.random.default_rng(seed)
        self.noise, self.quant, self.lat = noise_frac, quant, latency_steps
        self.p_drop, self.bad_p = p_drop, bad_p
        self._buf = []

    def sample(self, t, truth: dict):
        """truth: {channel: value}. Returns {channel: (value, quality)} or None (dropped)."""
        row = {}
        for k, v in truth.items():
            x = v * (1 + self.rng.normal(0, self.noise)) if v else v
            x = round(x / self.quant) * self.quant if self.quant else x
            q = 'good'
            if self.rng.random() < self.bad_p:
                x, q = x * self.rng.choice([0.0, 10.0]), 'bad'      # stuck-at-zero / spike
            row[k] = (float(x), q)
        self._buf.append((t, row))
        if len(self._buf) <= self.lat:
            return None
        t0, r0 = self._buf.pop(0)
        if self.rng.random() < self.p_drop:
            return None
        return r0


class StateEstimator:
    def __init__(self, q=0.5, r=1.0, stale_after=3, plausible=None):
        self.q, self.r, self.stale_after = q, r, stale_after
        self.x, self.p, self.age = {}, {}, {}
        self.plausible = plausible or {}           # channel -> (lo, hi)

    def update(self, row):
        """row: None (no data) or {channel: (value, quality)}. Returns estimates dict."""
        for k in list(self.x):
            self.age[k] = self.age.get(k, 0) + 1
            self.p[k] += self.q                         # uncertainty grows without data
        if row is not None:
            for k, (v, qual) in row.items():
                lo, hi = self.plausible.get(k, (-np.inf, np.inf))
                if qual != 'good' or not (lo <= v <= hi):
                    continue                            # bad-data rejection
                if k not in self.x:
                    self.x[k], self.p[k] = v, self.r
                else:
                    K = self.p[k] / (self.p[k] + self.r)
                    self.x[k] += K * (v - self.x[k]); self.p[k] *= (1 - K)
                self.age[k] = 0
        return {k: dict(value=self.x[k], sd=float(np.sqrt(self.p[k])), age=self.age[k],
                        stale=self.age[k] >= self.stale_after) for k in self.x}
