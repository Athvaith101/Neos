"""dataset.py -- rolls the world model forward to produce per-asset time series."""
from __future__ import annotations
import numpy as np
from .world import (T, DT, weather_ensemble, pv_power, home_profile,
                    commercial_profile, ev_sessions)


def simulate_period(nb, n_days=1, seed=0, regime='normal', day0=140):
    w = weather_ensemble(n_days=n_days, day_of_year=day0, seed=seed, regime=regime)
    rng = np.random.default_rng(seed + 991)
    n = T * n_days

    home_load = np.stack([home_profile(h, w['temp'], rng, n_days) for h in nb.homes])
    comm_load = np.stack([commercial_profile(c, w['temp'], rng, n_days) for c in nb.commercials])
    if regime == 'spike':                      # unplanned coincident demand event
        home_load[:, 76:86] *= 1.30
        comm_load[:, 76:86] *= 1.20

    pv_home = np.stack([pv_power(w['ghi'], w['temp'], h.pv_kwp) for h in nb.homes])
    pv_comm = np.stack([pv_power(w['ghi'], w['temp'], c.pv_kwp) for c in nb.commercials])

    evs = {e.id: ev_sessions(e, rng, n_days, regime) for e in nb.evs}

    return dict(weather=w, home_load=home_load, comm_load=comm_load,
                pv_home=pv_home, pv_comm=pv_comm, ev=evs,
                n=n, n_days=n_days, regime=regime,
                agg_inflex=home_load.sum(0) + comm_load.sum(0),
                agg_pv=pv_home.sum(0) + pv_comm.sum(0))
