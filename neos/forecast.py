"""
forecast.py -- Probabilistic, CAUSAL demand forecasting.

Design rules (each one is covered by tests/test_forecast.py):

 1. Every forecast has an explicit ISSUE TIME `O` (an index). Features for target
    t > O may read the realised series only at indices <= O. This is enforced
    in code (`_Y.__getitem__` raises) and by a perturbation test.
 2. Weather inputs are ISSUE-TIME forecasts from nwp.py, never realised weather.
 3. Seasonal lags are chosen per target as the most recent same-time-of-day
    sample that is already observed at O (lag day k = ceil((t-O)/T)); the old
    `y[t-T+1]` feature, which peeked past the cutoff at 24 h, is gone.
 4. Scores are labelled for what they are: MEAN PINBALL LOSS over the three
    forecast quantiles (not CRPS), an 80 % INTERVAL SCORE, and empirical
    COVERAGE reported per lead-time bucket.
 5. Conformal widening is applied per lead bucket on a CHRONOLOGICALLY LATER
    calibration block. Split-conformal guarantees assume exchangeability, which
    time series do not satisfy; coverage is therefore *measured*, never assumed.
"""
from __future__ import annotations
import math
import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from .world import T, DT
from . import nwp

QUANTILES = (0.1, 0.5, 0.9)
LEAD_EDGES = (0, 24, 48, 10**9)            # 0-6 h, 6-12 h, >12 h
LEAD_NAMES = ('0-6h', '6-12h', '12h+')


# ------------------------------------------------------------------ metrics
def pinball(y, q, tau):
    d = np.asarray(y) - np.asarray(q)
    return float(np.mean(np.maximum(tau * d, (tau - 1) * d)))


def mean_pinball(y, qs, taus=QUANTILES):
    """Average pinball loss over the supplied quantile levels. This is a
    quantile-score summary, NOT a CRPS estimate (which needs the full
    predictive distribution or many levels)."""
    return float(np.mean([pinball(y, qs[i], t) for i, t in enumerate(taus)]))


def coverage(y, lo, hi):
    y = np.asarray(y)
    return float(np.mean((y >= lo) & (y <= hi)))


def interval_score(y, lo, hi, alpha=0.2):
    """Gneiting & Raftery interval score for a central (1-alpha) interval."""
    y = np.asarray(y)
    w = hi - lo
    s = w + (2 / alpha) * (lo - y) * (y < lo) + (2 / alpha) * (y - hi) * (y > hi)
    return float(np.mean(s))


def lead_bucket(lead_steps):
    lead_steps = np.asarray(lead_steps)
    b = np.zeros(lead_steps.shape, int)
    for i in range(1, len(LEAD_EDGES) - 1):
        b += lead_steps > LEAD_EDGES[i]
    return b


# ----------------------------------------------------------- causal features
class _Y:
    """Wrapper that refuses to be read past the forecast origin."""

    def __init__(self, arr, origin):
        self.a, self.O = np.asarray(arr, float), int(origin)

    def __getitem__(self, idx):
        i = np.asarray(idx)
        if i.size and int(i.max()) > self.O:
            raise AssertionError(f"causality violation: read index {int(i.max())} > origin {self.O}")
        return self.a[idx]


def features_for_origin(y, temp_obs, temp_fc, ghi_cs, kt_fc, O, H):
    """Rows for targets t = O+1 .. O+H. Returns X, t_idx, lead_steps."""
    t = np.arange(O + 1, O + H + 1)
    lead = t - O
    k = np.ceil(lead / T).astype(int)
    lag1 = t - k * T
    lag2 = lag1 - T
    if lag2.min() < 0 or O < 8:
        raise ValueError("origin too early for the lag structure")
    Y = _Y(y, O)
    TO = _Y(temp_obs, O)
    h = (t % T) * DT
    yO = np.full(H, float(Y[O]))
    m8 = np.full(H, float(np.mean(Y[np.arange(O - 7, O + 1)])))
    X = np.column_stack([
        yO, m8, Y[lag1], Y[lag2], 0.5 * (Y[lag1] + Y[lag2]),
        np.sin(2 * np.pi * h / 24), np.cos(2 * np.pi * h / 24),
        temp_fc[t], temp_fc[t] - TO[lag1],
        ghi_cs[t], ghi_cs[t] * kt_fc[t],
        ((t // T) % 7).astype(float), lead * DT,
    ])
    return X, t, lead


def build_training_set(y, temp, ghi_cs, kt_true, temp_clim, kt_clim, origins, H, seed=0):
    """Training rows from many origins; weather features are ISSUE-TIME forecasts."""
    Xs, Ys, Ls, Os = [], [], [], []
    n = len(y)
    for O in origins:
        if O + H >= n:
            continue
        rng = np.random.default_rng(nwp_seed(seed, O))
        lead_all = np.arange(n) - O
        tclim = np.array([temp_clim[i % T] for i in range(n)])
        tf = nwp.temp_forecast(temp, tclim, lead_all, rng)
        kf = nwp.kt_forecast_point(kt_true, kt_clim, lead_all, rng)
        X, t, lead = features_for_origin(y, temp, tf, ghi_cs, kf, O, H)
        Xs.append(X); Ys.append(np.asarray(y)[t]); Ls.append(lead); Os.append(np.full(len(t), O))
    return np.vstack(Xs), np.concatenate(Ys), np.concatenate(Ls), np.concatenate(Os)


def nwp_seed(seed, O):
    from .seeds import stable_seed
    return stable_seed('nwp-train', seed, int(O))


def daily_origins(first_day, last_day, per_day=4):
    step = T // per_day
    return [d * T + j * step for d in range(first_day, last_day) for j in range(per_day)]


# ------------------------------------------------------------------- model
class QuantileGBM:
    """Quantile gradient boosting + lead-bucketed split-conformal widening.
    Deliberately not a Transformer: see TOOLS-AND-FRAMEWORK.md for the evidence."""

    def __init__(self, quantiles=QUANTILES, max_iter=200):
        self.quantiles = quantiles
        self.models = {q: HistGradientBoostingRegressor(
            loss='quantile', quantile=q, max_iter=max_iter, learning_rate=0.06,
            max_depth=6, min_samples_leaf=15, l2_regularization=1.0, random_state=0)
            for q in quantiles}
        self.delta = np.zeros(len(LEAD_EDGES) - 1)

    def fit(self, X, y, Xcal=None, ycal=None, leadcal=None, alpha=0.2):
        for m in self.models.values():
            m.fit(X, y)
        if Xcal is not None:
            raw = self._raw(Xcal)
            score = np.maximum(raw[0] - ycal, ycal - raw[-1])
            b = lead_bucket(leadcal)
            for i in range(len(self.delta)):
                s = np.sort(score[b == i])
                if len(s) < 20:
                    continue
                kk = int(np.ceil((len(s) + 1) * (1 - alpha))) - 1
                self.delta[i] = float(s[min(kk, len(s) - 1)])
        return self

    def _raw(self, X):
        return np.sort(np.stack([self.models[q].predict(X) for q in self.quantiles]), 0)

    def predict(self, X, lead_steps=None, conformal=True):
        p = self._raw(X)
        if conformal and lead_steps is not None:
            d = self.delta[lead_bucket(lead_steps)]
            p[0] = p[0] - d
            p[-1] = p[-1] + d
        return p


# ---------------------------------------------------------------- baselines
def baseline_preds(y, t, lead):
    """Causal baselines, evaluated for the same (origin,t) rows as the model."""
    y = np.asarray(y)
    O = t - lead
    k = np.ceil(lead / T).astype(int)
    lag1 = t - k * T
    return dict(persistence=y[O], day_persistence=y[lag1],
                seasonal_mean_2d=0.5 * (y[lag1] + y[lag1 - T]))


def score_block(y, P=None, point=None, alpha=0.2):
    if P is not None:
        return dict(mae=float(np.mean(np.abs(y - P[1]))),
                    rmse=float(np.sqrt(np.mean((y - P[1]) ** 2))),
                    mean_pinball=mean_pinball(y, P),
                    interval_score_80=interval_score(y, P[0], P[2], alpha),
                    coverage_80=coverage(y, P[0], P[2]),
                    sharpness=float(np.mean(P[2] - P[0])))
    return dict(mae=float(np.mean(np.abs(y - point))),
                rmse=float(np.sqrt(np.mean((y - point) ** 2))),
                mean_pinball=mean_pinball(y, [point, point, point]),
                interval_score_80=None, coverage_80=None, sharpness=0.0)


def coverage_by_lead(y, P, lead):
    b = lead_bucket(lead)
    return {LEAD_NAMES[i]: (coverage(y[b == i], P[0][b == i], P[2][b == i])
                            if (b == i).any() else None)
            for i in range(len(LEAD_NAMES))}


def fit_operational(series, temp, ghi_cs, kt_true, temp_clim, kt_clim, n_train_days,
                    n_cal_days, H, seed=0):
    """Train on days [3, n_train), calibrate on the chronologically later block."""
    tr = daily_origins(3, n_train_days)
    ca = daily_origins(n_train_days, n_train_days + n_cal_days)
    X, Y, L, _ = build_training_set(series, temp, ghi_cs, kt_true, temp_clim, kt_clim, tr, H, seed)
    Xc, Yc, Lc, _ = build_training_set(series, temp, ghi_cs, kt_true, temp_clim, kt_clim, ca, H, seed + 1)
    return QuantileGBM().fit(X, Y, Xc, Yc, Lc)


def evaluate_benchmark(model, series, temp, ghi_cs, kt_true, temp_clim, kt_clim,
                       test_days, H, seed=0):
    """Chronological hold-out evaluation on days the model never saw."""
    org = daily_origins(test_days[0], test_days[1])
    X, Y, L, _ = build_training_set(series, temp, ghi_cs, kt_true, temp_clim, kt_clim, org, H, seed + 2)
    t = np.concatenate([np.arange(O + 1, O + H + 1) for O in org if O + H < len(series)])
    out = {}
    base = baseline_preds(series, t, L)
    for name, p in base.items():
        out[name] = score_block(Y, point=p)
    Praw = model.predict(X, L, conformal=False)
    Pc = model.predict(X, L, conformal=True)
    out['quantile_gbm_raw'] = score_block(Y, Praw)
    out['quantile_gbm_raw']['coverage_by_lead'] = coverage_by_lead(Y, Praw, L)
    out['quantile_gbm+conformal'] = score_block(Y, Pc)
    out['quantile_gbm+conformal']['coverage_by_lead'] = coverage_by_lead(Y, Pc, L)
    out['_n_rows'] = int(len(Y))
    return out


def forecast_window(model, y_obs_upto_O, temp_all, ghi_cs_all, kt_fc_all, temp_fc_all, O, H):
    """Operational fixed-origin forecast. `y_obs_upto_O` is sliced HERE so the
    realised future cannot leak in even by accident."""
    y = np.full(len(ghi_cs_all), np.nan)
    y[:O + 1] = np.asarray(y_obs_upto_O)[:O + 1]
    X, t, lead = features_for_origin(y, temp_all, temp_fc_all, ghi_cs_all, kt_fc_all, O, H)
    if not np.all(np.isfinite(X)):
        raise AssertionError("non-finite feature: a future value was read")
    return model.predict(X, lead), t
