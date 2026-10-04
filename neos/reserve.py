"""
reserve.py -- Confidence-aware battery reserve.

NEOS distinguishes AVAILABLE energy from energy that should be RESERVED for
resilience. Endurance indicator (conservative, NOT a full battery model):

    hours_remaining = usable_range * SoH * eta_discharge * nominal_kWh / essential_kW

It ignores auxiliaries, temperature, converter limits and dynamic load; the hub
simulator models those explicitly.

Reserve policies compared in the reserve experiment:
    FixedReserve(r)                 constant protected SOC fraction
    AdaptiveReserve(base, k, max)   r = clip(base + k * recent_forecast_error_ratio)
The experiment reports whichever result it produces (better / not better / inconclusive).
"""
from __future__ import annotations


def hours_remaining(usable_range, soh, eta_dis, nominal_kwh, essential_kw):
    return usable_range * soh * eta_dis * nominal_kwh / essential_kw


def nominal_for(essential_kw, hours, usable_range, soh, eta_dis):
    """Nominal kWh needed to carry `essential_kw` for `hours` (before auxiliaries/margin)."""
    return essential_kw * hours / (usable_range * soh * eta_dis)


class FixedReserve:
    name = 'fixed'

    def __init__(self, r=0.40):
        self.r = r

    def update(self, forecast_kw, actual_kw):
        pass

    def value(self):
        return self.r


class AdaptiveReserve:
    """Raises the protected reserve after recent over-forecasting of supply."""
    name = 'adaptive'

    def __init__(self, base=0.40, k=0.60, r_max=0.75, alpha=0.25):
        self.base, self.k, self.r_max, self.alpha = base, k, r_max, alpha
        self.err = 0.0                      # EWMA of relative shortfall of actual vs forecast

    def update(self, forecast_kw, actual_kw):
        if forecast_kw > 1e-6:
            short = max(0.0, (forecast_kw - actual_kw) / forecast_kw)
            self.err = (1 - self.alpha) * self.err + self.alpha * short

    def value(self):
        return min(self.r_max, self.base + self.k * self.err)
