"""
nwp.py -- ISSUE-TIME weather forecasts (synthetic, and labelled as such).

The controller must never see realised weather. A forecast issued at step
`origin` knows the observed past exactly, and for lead times l > 0 blends the
(hidden) truth with climatology using a skill weight that decays with lead:

    w(l)  = exp(-l / tau)            tau_kt = 8 h, tau_temp = 36 h
    kt_fc = w*kt_true + (1-w)*kt_clim + e(l)        e ~ OU noise, sd grows with l

This is an ASSUMPTION about NWP skill, not a measurement. Replace with archived
NWP / satellite-nowcast data (see TOOLS-AND-FRAMEWORK.md, Tier 2) before making
any claim about forecast accuracy on real weather.
"""
from __future__ import annotations
import numpy as np
from .world import DT, T

TAU_KT_H = 8.0
TAU_TEMP_H = 36.0


def skill(lead_steps, tau_h):
    return np.exp(-np.maximum(lead_steps, 0) * DT / tau_h)


def sigma_kt(lead_steps):
    l = np.maximum(lead_steps, 0) * DT
    return 0.04 + 0.20 * (1 - np.exp(-l / 6.0))


def temp_forecast(temp_true, temp_clim, lead_steps, rng):
    w = skill(lead_steps, TAU_TEMP_H)
    sd = 0.3 + 0.9 * (1 - w)
    f = w * temp_true + (1 - w) * temp_clim + rng.normal(0, 1, np.shape(temp_true)) * sd
    return np.where(np.asarray(lead_steps) <= 0, temp_true, f)


def kt_forecast_point(kt_true, kt_clim, lead_steps, rng):
    w = skill(lead_steps, TAU_KT_H)
    f = w * kt_true + (1 - w) * kt_clim + rng.normal(0, 1, np.shape(kt_true)) * 0.5 * sigma_kt(lead_steps)
    f = np.clip(f, 0.05, 1.0)
    return np.where(np.asarray(lead_steps) <= 0, kt_true, f)


def ou_unit(n_members, n, rng, theta=0.12):
    """Unit-variance stationary OU paths (vectorised over members)."""
    sd_inc = np.sqrt(1 - (1 - theta) ** 2)
    x = np.zeros((n_members, n))
    x[:, 0] = rng.normal(0, 1, n_members)
    z = rng.normal(0, 1, (n_members, n))
    for i in range(1, n):
        x[:, i] = (1 - theta) * x[:, i - 1] + sd_inc * z[:, i]
    return x


def issue_forecast(weather, origin, kt_clim, temp_clim_day, seed, n_members=100):
    """Forecast issued at step `origin` (index into the weather arrays).
    Returns dict(kt_mean, kt_members, temp_fc). Entries <= origin are OBSERVED."""
    rng = np.random.default_rng(seed)
    n = len(weather['kt_true'])
    lead = np.arange(n) - origin
    tclim = np.array([temp_clim_day[i % T] for i in range(n)])
    temp_fc = temp_forecast(weather['temp'], tclim, lead, rng)
    kt_mean = kt_forecast_point(weather['kt_true'], kt_clim, lead, rng)
    sig = sigma_kt(lead)
    noise = ou_unit(n_members, n, rng)
    members = np.clip(kt_mean[None, :] + sig[None, :] * noise, 0.05, 1.0)
    members[:, lead <= 0] = weather['kt_true'][lead <= 0]
    return dict(kt_mean=kt_mean, kt_members=members, temp_fc=temp_fc)
