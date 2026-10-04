"""
resources.py -- Resource abstraction.

Flexible resources are SERVICE CONSTRAINTS, not arbitrary negative load. Every
resource exposes the same contract so schedulers, the safety layer, the
flexibility envelope and the decision trace treat them uniformly:

    kind, tier (1 critical .. 5 discretionary), hard (service is a hard requirement)
    limits(dt_h)        -> (p_min_kw, p_max_kw)  DEVICE FEASIBILITY (power + energy bounds
                           + start/stop rules) evaluated BEFORE any power flow
    apply(p_kw, dt_h)   -> actual state change; raises if the request was infeasible
    service()           -> dict(state, requirement, satisfied)
    deadline_status(now_step) -> None | dict(deadline_step, need_kwh, max_deliverable_kwh,
                                               feasible, binding)

Infeasibility policy: a hard requirement that cannot be met is reported with the
binding constraint; it is never converted to a soft one.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field

DT = 0.25


class InfeasibleDispatch(Exception):
    pass


@dataclass
class Resource:
    name: str
    kind: str
    tier: int
    hard: bool = False
    node: str = ""
    phase: int = 0            # 0 = three-phase balanced

    def limits(self, dt_h=DT):
        raise NotImplementedError

    def apply(self, p_kw, dt_h=DT):
        lo, hi = self.limits(dt_h)
        if p_kw < lo - 1e-9 or p_kw > hi + 1e-9:
            raise InfeasibleDispatch(f"{self.name}: {p_kw:.3f} kW outside [{lo:.3f}, {hi:.3f}]")
        self._apply(p_kw, dt_h)

    def service(self):
        raise NotImplementedError

    def deadline_status(self, now_step):
        return None


# --------------------------------------------------------------- battery
@dataclass
class BatteryRes(Resource):
    cap_kwh: float = 20.0
    p_max_kw: float = 16.0
    e_kwh: float = 10.0
    eta: float = 0.95            # one-way
    soc_min: float = 0.10
    soc_max: float = 0.95
    soh: float = 1.0
    derate: float = 1.0
    available: bool = True

    def limits(self, dt_h=DT):
        """Positive = discharge (kW at terminals), negative = charge."""
        if not self.available:
            return 0.0, 0.0
        cap = self.cap_kwh * self.soh
        pmax = self.p_max_kw * self.derate
        dis = min(pmax, max(0.0, self.e_kwh - self.soc_min * cap) * self.eta / dt_h)
        chg = min(pmax, max(0.0, self.soc_max * cap - self.e_kwh) / (self.eta * dt_h))
        return -chg, dis

    def _apply(self, p, dt_h):
        self.e_kwh += (-p * self.eta if p < 0 else -p / self.eta) * dt_h

    @property
    def soc(self):
        return self.e_kwh / (self.cap_kwh * self.soh)

    def service(self):
        return dict(soc=self.soc, requirement=f"SOC in [{self.soc_min:.2f}, {self.soc_max:.2f}]",
                    satisfied=self.soc_min - 1e-9 <= self.soc <= self.soc_max + 1e-9)


# ------------------------------------------------------------------ pump
@dataclass
class TankSystem:
    """Water storage as an energy-equivalent buffer shared by the pumps."""
    cap: float = 160.0
    level: float = 100.0
    minimum: float = 30.0
    deadlines: tuple = ((24, 90.0), (68, 90.0))       # (step-of-day, required level kWh-eq)


@dataclass
class PumpRes(Resource):
    p_kw: float = 7.5
    min_run: int = 4
    min_rest: int = 2
    tank: TankSystem = field(default_factory=TankSystem)
    on: bool = False
    run_steps: int = 0
    rest_steps: int = 99
    started_this_step: bool = False

    def limits(self, dt_h=DT):
        room = (self.tank.cap - self.tank.level) / dt_h
        if self.on and self.run_steps < self.min_run:
            return min(self.p_kw, max(room, 0.0)), min(self.p_kw, max(room, 0.0))   # must keep running
        if not self.on and self.rest_steps < self.min_rest:
            return 0.0, 0.0                                                          # resting
        return 0.0, min(self.p_kw, max(room, 0.0))

    def can_start(self):
        return (not self.on) and self.rest_steps >= self.min_rest and self.tank.level < self.tank.cap - 1e-6

    def _apply(self, p, dt_h):
        if p > 1e-9:
            if not self.on:
                self.on, self.run_steps, self.started_this_step = True, 0, True
            self.run_steps += 1
            self.rest_steps = 0
            self.tank.level += p * dt_h
        else:
            if self.on:
                self.on, self.rest_steps = False, 0
            self.rest_steps += 1
            self.run_steps = 0

    def service(self):
        return dict(level=self.tank.level, requirement=f"tank >= {self.tank.minimum} kWh-eq always",
                    satisfied=self.tank.level >= self.tank.minimum - 1e-9)


# ------------------------------------------------------------- cold store
@dataclass
class ColdStoreRes(Resource):
    cap_th: float = 50.0
    s_th: float = 35.0
    s_min: float = 10.0
    p_el_kw: float = 5.0
    cop: float = 2.4
    min_run: int = 2
    on: bool = False
    run_steps: int = 0

    def limits(self, dt_h=DT):
        room = (self.cap_th - self.s_th) / (self.cop * dt_h)
        if self.on and self.run_steps < self.min_run:
            return min(self.p_el_kw, max(room, 0.0)), min(self.p_el_kw, max(room, 0.0))
        return 0.0, min(self.p_el_kw, max(room, 0.0))

    def leak(self, temp_c, dt_h=DT):
        return (2.0 + 0.15 * max(0.0, temp_c - 25.0)) * dt_h        # kWh_th lost per step

    def _apply(self, p, dt_h):
        self.s_th += p * self.cop * dt_h
        if p > 1e-9:
            self.on = True; self.run_steps += 1
        else:
            self.on = False; self.run_steps = 0

    def service(self):
        return dict(s_th=self.s_th, requirement=f"cold store stored cooling >= {self.s_min} kWh_th",
                    satisfied=self.s_th >= self.s_min - 1e-9)


# ---------------------------------------------------------- productive load
@dataclass
class ShiftableBlock(Resource):
    """Non-interruptible block of work (e.g. a mill): must run `steps_needed`
    contiguous steps inside [win_start, win_end) once started."""
    p_kw: float = 15.0
    kwh_total: float = 45.0
    win_start: int = 32
    win_end: int = 68
    remaining_kwh: float = 45.0
    running: bool = False

    def steps_left_needed(self):
        return math.ceil(self.remaining_kwh / (self.p_kw * DT) - 1e-9)

    def limits(self, dt_h=DT):
        if self.remaining_kwh <= 1e-9:
            return 0.0, 0.0
        if self.running:
            p = min(self.p_kw, self.remaining_kwh / dt_h)
            return p, p                                     # non-interruptible
        return 0.0, min(self.p_kw, self.remaining_kwh / dt_h)

    def _apply(self, p, dt_h):
        self.remaining_kwh -= p * dt_h
        self.running = p > 1e-9 and self.remaining_kwh > 1e-9

    def deadline_status(self, now_step):
        left = self.win_end - now_step
        need = self.steps_left_needed()
        return dict(deadline_step=self.win_end, need_steps=need, steps_left=left,
                    feasible=need <= left, binding='window length' if need > left else None)

    def service(self):
        return dict(remaining_kwh=self.remaining_kwh, requirement="complete daily work in window",
                    satisfied=self.remaining_kwh <= 1e-6)


# --------------------------------------------------------------------- EV
@dataclass
class EVRes(Resource):
    """EV as a deadline service: needs `need_kwh` by `deadline_step`."""
    p_max_kw: float = 7.2
    cap_kwh: float = 26.0
    soc: float = 0.4
    target_soc: float = 0.8
    deadline_step: int = 120
    eta: float = 0.92

    def limits(self, dt_h=DT):
        room = (1.0 - self.soc) * self.cap_kwh / (self.eta * dt_h)
        return 0.0, max(0.0, min(self.p_max_kw, room))

    def _apply(self, p, dt_h):
        self.soc += p * self.eta * dt_h / self.cap_kwh

    def deadline_status(self, now_step):
        need = max(0.0, (self.target_soc - self.soc) * self.cap_kwh)
        deliverable = self.p_max_kw * self.eta * DT * max(0, self.deadline_step - now_step)
        feasible = deliverable + 1e-9 >= need
        return dict(deadline_step=self.deadline_step, need_kwh=need, max_deliverable_kwh=deliverable,
                    feasible=feasible, binding=None if feasible else 'charger power x time to deadline')

    def service(self):
        return dict(soc=self.soc, requirement=f"SOC >= {self.target_soc:.2f} by step {self.deadline_step}",
                    satisfied=self.soc >= self.target_soc - 0.02)


def infeasibility_report(resources, now_step):
    """Hard requirements that can no longer be met, with the binding constraint.
    Never relaxes anything on its own."""
    out = []
    for r in resources:
        ds = r.deadline_status(now_step)
        if r.hard and ds is not None and not ds['feasible']:
            out.append(dict(resource=r.name, binding=ds['binding'], detail=ds))
    return out
