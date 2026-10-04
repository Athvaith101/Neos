"""
world.py -- Synthetic neighbourhood generator for the Neighbourhood Energy OS.

Builds a heterogeneous population of prosumers on ONE 11kV/433V distribution
transformer with 4 LV feeders, plus a probabilistic weather / PV model.

Conventions:
  dt          = 0.25 h (15 min), T = 96 steps/day
  Power in kW, energy in kWh, positive = consumption.
Location assumptions: Coimbatore, Tamil Nadu (11.0 N), tropical, AC-dominated
summer peak, Indian LV voltage band +/-6% (CEA / IS 12360).
"""
from __future__ import annotations
import numpy as np
from dataclasses import dataclass, field

DT = 0.25
T = 96
LAT = 11.0


def tod(t):  # timestep -> hour of day (float)
    return (t % T) * DT


# ---------------------------------------------------------------- population

@dataclass
class Home:
    id: int
    node: str
    phase: int
    occupants: int
    has_ac: bool
    ac_kw: float
    base_kw: float
    # deferrable appliance (water heater / washing machine / pump)
    defer_kw: float
    defer_kwh: float
    defer_window: tuple  # (earliest_step, latest_step)
    flex_tolerance: float  # 0=never wants to be touched, 1=fully flexible
    pv_kwp: float = 0.0


@dataclass
class EV:
    id: int
    home_id: int
    node: str
    phase: int
    capacity_kwh: float
    charger_kw: float
    # behavioural distribution (learned in reality; sampled here as ground truth)
    dep_mu: float          # mean departure hour
    dep_sigma: float
    arr_mu: float
    arr_sigma: float
    daily_km: float
    req_soc: float         # SOC the user needs at departure
    flex_tolerance: float


@dataclass
class Battery:
    id: int
    home_id: int
    node: str
    phase: int
    capacity_kwh: float
    p_max: float
    eta: float = 0.95
    soc0: float = 0.5


@dataclass
class Commercial:
    id: int
    node: str
    peak_kw: float
    kind: str          # 'shop' | 'office' | 'coldstore'
    flex_kw: float     # curtailable portion
    pv_kwp: float


@dataclass
class Neighbourhood:
    homes: list
    evs: list
    batteries: list
    commercials: list
    nodes: list          # list of (node_name, feeder_idx, distance_m)
    tx_kva: float = 630.0

    @property
    def pv_kwp_total(self):
        return sum(h.pv_kwp for h in self.homes) + sum(c.pv_kwp for c in self.commercials)


def build_neighbourhood(seed=7, n_homes=300, n_ev=60, n_pv=150, n_bess=26,
                        n_comm=10, n_feeders=4, seg_per_feeder=10,
                        tx_kva=630.0) -> Neighbourhood:
    """Heterogeneity is the point: no two consumers share a parameter vector."""
    from .config import NeighbourhoodConfig
    NeighbourhoodConfig(n_homes=n_homes, n_ev=n_ev, n_pv=n_pv, n_bess=n_bess,
                        n_comm=n_comm, tx_kva=tx_kva, n_feeders=n_feeders,
                        seg_per_feeder=seg_per_feeder, seed=seed)   # validates
    rng = np.random.default_rng(seed)

    nodes = []
    for f in range(n_feeders):
        for s in range(1, seg_per_feeder + 1):
            nodes.append((f"f{f}_{s}", f, s * 40.0))   # 40 m segments -> 400 m feeder

    homes = []
    for i in range(n_homes):
        node, feeder, dist = nodes[rng.integers(len(nodes))]
        occ = int(rng.choice([1, 2, 3, 4, 5, 6], p=[.08, .22, .28, .24, .12, .06]))
        has_ac = rng.random() < 0.55
        homes.append(Home(
            id=i, node=node, phase=int(i % 3) + 1, occupants=occ,
            has_ac=has_ac,
            ac_kw=float(rng.uniform(0.85, 1.55) * (1 + 0.30 * (occ > 3))) if has_ac else 0.0,
            base_kw=float(0.12 + 0.055 * occ + rng.normal(0, 0.03)),
            defer_kw=float(rng.choice([1.5, 2.0, 3.0, 0.75], p=[.35, .3, .2, .15])),
            defer_kwh=float(rng.uniform(1.0, 4.5)),
            defer_window=(int(rng.integers(20, 34)), int(rng.integers(60, 88))),
            flex_tolerance=float(np.clip(rng.beta(2.2, 1.8), 0.02, 0.98)),
        ))

    # rooftop PV on a subset, sized to roof/consumption, not uniform
    pv_ids = rng.choice(n_homes, size=n_pv, replace=False)
    for i in pv_ids:
        homes[i].pv_kwp = float(np.round(rng.choice([2, 3, 5, 8], p=[.2, .35, .3, .15])
                                         + rng.normal(0, 0.25), 2))

    ev_ids = rng.choice(n_homes, size=n_ev, replace=False)
    evs = []
    for k, i in enumerate(ev_ids):
        h = homes[i]
        cap = float(rng.choice([18, 26, 30, 40, 55], p=[.2, .25, .25, .2, .1]))
        evs.append(EV(
            id=k, home_id=i, node=h.node, phase=h.phase,
            capacity_kwh=cap,
            charger_kw=float(rng.choice([3.3, 3.3, 7.2, 7.2, 11.0], p=[.3, .2, .25, .15, .1])),
            dep_mu=float(rng.normal(8.4, 1.1)), dep_sigma=float(rng.uniform(0.25, 0.9)),
            arr_mu=float(rng.normal(19.0, 1.4)), arr_sigma=float(rng.uniform(0.4, 1.3)),
            daily_km=float(np.clip(rng.gamma(4.2, 7.0), 8, 110)),
            req_soc=float(np.clip(rng.normal(0.72, 0.10), 0.45, 0.95)),
            flex_tolerance=float(np.clip(rng.beta(2.5, 1.5), 0.02, 0.98)),
        ))

    b_ids = rng.choice(pv_ids, size=n_bess, replace=False)   # batteries follow PV
    batteries = []
    for k, i in enumerate(b_ids):
        h = homes[i]
        cap = float(rng.choice([5, 7.5, 10, 13.5], p=[.3, .3, .25, .15]))
        batteries.append(Battery(id=k, home_id=i, node=h.node, phase=h.phase,
                                 capacity_kwh=cap, p_max=float(cap / 2.2),
                                 soc0=float(rng.uniform(0.35, 0.6))))

    commercials = []
    # n_comm is honoured: 50 % shops, 30 % offices, 20 % cold stores
    n_shop = int(round(0.5 * n_comm)); n_off = int(round(0.3 * n_comm))
    kinds = (['shop'] * n_shop + ['office'] * n_off
             + ['coldstore'] * max(0, n_comm - n_shop - n_off))[:n_comm]
    for j, kind in enumerate(kinds):
        node, feeder, dist = nodes[rng.integers(len(nodes))]
        peak = {'shop': rng.uniform(6, 14), 'office': rng.uniform(15, 35),
                'coldstore': rng.uniform(20, 45)}[kind]
        commercials.append(Commercial(
            id=j, node=node, peak_kw=float(peak), kind=kind,
            flex_kw=float(peak * {'shop': .15, 'office': .25, 'coldstore': .45}[kind]),
            pv_kwp=float(rng.choice([0, 10, 25, 50], p=[.2, .3, .3, .2])),
        ))

    return Neighbourhood(homes, evs, batteries, commercials, nodes, tx_kva=float(tx_kva))


# ------------------------------------------------------------------- weather

def clear_sky(day_of_year=140, n_days=1):
    """Simple Ineichen-style clear-sky GHI for the site (kW/m^2)."""
    out = []
    for d in range(n_days):
        doy = day_of_year + d
        decl = np.radians(23.45) * np.sin(2 * np.pi * (284 + doy) / 365)
        lat = np.radians(LAT)
        g = []
        for t in range(T):
            h = np.radians(15 * (tod(t) - 12))
            cz = np.sin(lat) * np.sin(decl) + np.cos(lat) * np.cos(decl) * np.cos(h)
            cz = max(cz, 0.0)
            g.append(1.09 * cz ** 1.18 if cz > 0 else 0.0)
        out.append(g)
    return np.array(out).ravel()


def weather_ensemble(n_days=1, day_of_year=140, seed=0, n_members=0,
                     regime='normal'):
    """Returns dict with the realised weather and an ENSEMBLE of plausible
    cloud trajectories -> this is what turns a point PV forecast into a
    distribution (P10/P50/P90). Cloud opacity is an OU process so that
    cloud events are temporally correlated, not white noise."""
    rng = np.random.default_rng(seed)
    n = T * n_days
    cs = clear_sky(day_of_year, n_days)

    def ou(rs, theta=0.12, sigma=0.22, x0=None):
        x = np.zeros(n)
        x[0] = x0 if x0 is not None else rs.normal(0, 0.3)
        for i in range(1, n):
            x[i] = x[i - 1] + theta * (0 - x[i - 1]) + sigma * rs.normal()
        return x

    def to_kt(x, base):  # clearness index in (0.05, 1.0)
        return np.clip(base - 0.42 / (1 + np.exp(-x)) + 0.21, 0.05, 1.0)

    base = {'normal': 0.94, 'cloud': 0.94, 'heat': 0.99,
            'shortage': 0.62, 'evsurge': 0.94, 'battfail': 0.94,
            'spike': 0.94}[regime]

    truth_kt = to_kt(ou(rng), base)
    if regime == 'cloud':          # a hard 2.5 h cloud passage from 12:30
        s, e = 50, 60
        truth_kt[s:e] *= np.clip(np.r_[np.linspace(1, .18, 4),
                                       np.full(3, .15),
                                       np.linspace(.18, 1, 3)], 0, 1)
    if regime == 'shortage':
        truth_kt[36:76] *= 0.45

    # NOTE: no forecast ensemble is generated here any more. Ensembles are
    # ISSUE-TIME objects built in nwp.py; the realised weather below is hidden
    # from the controller.
    members = np.zeros((0, n))

    # temperature: diurnal + regime offset
    peak_c = {'normal': 34.0, 'cloud': 31.5, 'heat': 42.0, 'shortage': 35.0,
              'evsurge': 34.0, 'battfail': 34.0, 'spike': 36.0}[regime]
    hrs = np.array([tod(t) for t in range(n)])
    temp = (peak_c + 25.0) / 2 - (peak_c - 25.0) / 2 * np.cos(
        2 * np.pi * (hrs - 3.5) / 24) + rng.normal(0, 0.35, n)
    temp += 3.0 * (1 - truth_kt) * (truth_kt < 0.5)  # clouds cool slightly

    return dict(ghi_cs=cs, kt_true=truth_kt, kt_base=base,
                ghi=cs * truth_kt, temp=temp, regime=regime)


def pv_power(ghi, temp, kwp):
    """PV DC->AC with NOCT cell-temperature derating (-0.38 %/K above 25 C)."""
    tcell = temp + 28.0 * ghi
    return np.maximum(kwp * ghi * (1 - 0.0038 * (tcell - 25.0)) * 0.96, 0.0)


# ------------------------------------------------------- behavioural loads

def home_profile(h: Home, temp, rng, n_days=1):
    """Occupancy-driven stochastic appliance model. Produces the INFLEXIBLE
    part of a household's demand (the deferrable appliance is handled by the
    controller, not baked in here)."""
    n = T * n_days
    hrs = np.array([tod(t) for t in range(n)])
    # occupancy probability: home at night, partly out mid-day
    occ = 0.55 + 0.45 * np.cos(2 * np.pi * (hrs - 3) / 24)
    occ = np.clip(occ + rng.normal(0, 0.04, n), 0, 1)
    p = h.base_kw * (0.45 + 0.9 * occ)
    # cooking / evening peaks
    for centre, width, amp in [(7.5, 0.9, 0.40), (13.0, 1.0, 0.26), (20.0, 1.2, 0.62)]:
        c = centre + rng.normal(0, 0.55)
        p += amp * h.occupants / 3.2 * np.exp(-0.5 * ((hrs - c) / width) ** 2)
    # air conditioning: thermostatic, temperature-driven duty cycle
    if h.has_ac:
        duty = np.clip((temp[:n] - 27.5) / 10.5, 0, 1) * (0.35 + 0.65 * occ)
        p += h.ac_kw * duty * (1 + rng.normal(0, 0.05, n))
    # stochastic appliance bursts
    for _ in range(rng.poisson(3.0 * n_days)):
        s = rng.integers(0, n - 4)
        p[s:s + int(rng.integers(1, 5))] += rng.uniform(0.4, 1.8)
    # day-level random effects: household routines are not identical day to day
    for d in range(n_days):
        sl = slice(d * T, (d + 1) * T)
        weekend = ((d % 7) in (5, 6))
        p[sl] *= (1.0 + rng.normal(0, 0.09)) * (1.10 if weekend else 1.0)
    return np.maximum(p, 0.05)


def commercial_profile(c: Commercial, temp, rng, n_days=1):
    n = T * n_days
    hrs = np.array([tod(t) for t in range(n)])
    if c.kind == 'coldstore':
        duty = 0.55 + 0.35 * np.clip((temp[:n] - 28) / 12, 0, 1)
        p = c.peak_kw * duty
    elif c.kind == 'office':
        p = c.peak_kw * (0.18 + 0.82 * ((hrs > 9) & (hrs < 19)))
        p = p * (1 + 0.25 * np.clip((temp[:n] - 30) / 10, 0, 1))
    else:
        p = c.peak_kw * (0.15 + 0.85 * ((hrs > 9.5) & (hrs < 21.5)))
    return np.maximum(p * (1 + rng.normal(0, 0.03, n)), 0.1)


def ev_sessions(ev: EV, rng, n_days=1, regime='normal'):
    """Sample ground-truth EV behaviour. This lives INSIDE THE SIMULATOR: the
    controller never reads `dep` or `override`; it sees only the observed
    plug-in event, the learned departure distribution, and any override the
    user has actually DECLARED (at `notice`).

    override (rare, ~6 % of sessions) is an early-departure event:
        notice  : step at which the user tells the system ("I need the car at X")
        depart  : the actual early departure step (> = notice)
        lead_steps = depart - notice may be 0 (unannounced disconnection)
    """
    out = []
    for d in range(n_days):
        arr_h = float(np.clip(rng.normal(ev.arr_mu, ev.arr_sigma), 15.5, 23.5))
        dep_h = float(np.clip(rng.normal(ev.dep_mu, ev.dep_sigma), 5.0, 11.5))
        km = float(np.clip(rng.normal(ev.daily_km, ev.daily_km * 0.28), 3, 160))
        if regime == 'evsurge':
            arr_h = float(np.clip(rng.normal(18.2, 0.6), 16.0, 20.0))  # everyone home together
            km *= 1.45
        soc_arr = float(np.clip(0.92 - km * 0.155 / ev.capacity_kwh, 0.05, 0.9))
        arr = int(arr_h / DT)
        dep = int(dep_h / DT) + T
        ov = None
        if rng.random() < 0.06:
            lead = int(rng.choice([0, 1, 2, 4, 8], p=[.15, .25, .25, .2, .15]))
            depart = int(np.clip(arr + rng.integers(4, 30), arr + 2, dep - 2))
            ov = dict(notice=max(arr + 1, depart - lead), depart=depart,
                      kind='need_now' if lead <= 1 else 'depart_early')
        if ov is not None:
            ov = dict(notice=ov['notice'] + d * T, depart=ov['depart'] + d * T, kind=ov['kind'])
        out.append(dict(day=d, arr=arr + d * T, dep=dep + d * T, soc_arr=soc_arr,
                        req=ev.req_soc, override=ov))
    return out
