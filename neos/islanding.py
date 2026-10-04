"""
islanding.py -- Essential-section islanding STATE MACHINE (concept / simulation).

CONCEPTUAL ONLY. Not a protection design, not certified, never to be applied to a
public feeder. Real islanding needs independent protection, grid-forming
conversion, anti-islanding verification, earthing/neutral handling, backfeed
prevention and controlled reconnection, all engineered and approved separately.
The 15-minute NEOS scheduler is not a protection system.

States:  GRID_CONNECTED -> OUTAGE_DETECTED -> ISOLATING -> ISLANDED
         ISLANDED -> RESYNC_WAIT -> RECONNECTING -> GRID_CONNECTED
         ISLANDED -> SHUTDOWN (SOC floor reached) -> BLACK_START -> RESYNC_WAIT/ISLANDED
Invariants enforced (and tested): never ISLANDED while the grid tie is closed
(backfeed prevention); never reconnect before the grid has been stable for
RESYNC_MIN_STEPS; load limit enforced while islanded; rooftop PV is assumed to
disconnect on outage (grid-following) unless a grid-forming source is present.
"""
from __future__ import annotations

GRID, DETECT, ISOLATING, ISLANDED, RESYNC, RECONNECT, SHUTDOWN, BLACKSTART = (
    'GRID_CONNECTED', 'OUTAGE_DETECTED', 'ISOLATING', 'ISLANDED', 'RESYNC_WAIT',
    'RECONNECTING', 'SHUTDOWN', 'BLACK_START')
CONCEPT_LABEL = "Conceptual/simulated architecture, not certified or approved field islanding."


class IslandController:
    def __init__(self, soc_floor=0.05, soc_restart=0.15, resync_min_steps=2, load_limit_kw=40.0):
        self.state = GRID
        self.tie_closed = True
        self.soc_floor, self.soc_restart = soc_floor, soc_restart
        self.resync_min_steps = resync_min_steps
        self.load_limit_kw = load_limit_kw
        self.stable = 0
        self.log = []

    def _go(self, new, t, why):
        self.log.append((t, self.state, new, why)); self.state = new

    def step(self, t, grid_ok, soc):
        """Advance one control step. Returns dict(state, tie_closed, serves_essential)."""
        s = self.state
        if s == GRID:
            if not grid_ok:
                self._go(DETECT, t, 'grid voltage lost')
        elif s == DETECT:
            self.tie_closed = False                       # open the tie FIRST
            self._go(ISOLATING, t, 'tie opened; verifying isolation')
        elif s == ISOLATING:
            assert not self.tie_closed, 'backfeed risk: tie closed while isolating'
            self._go(ISLANDED if soc > self.soc_floor else SHUTDOWN, t, 'isolation confirmed')
        elif s == ISLANDED:
            if soc <= self.soc_floor:
                self._go(SHUTDOWN, t, 'SOC floor')
            elif grid_ok:
                self.stable = 1
                self._go(RESYNC, t, 'grid returned; waiting for stability')
        elif s == RESYNC:
            if not grid_ok:
                self.stable = 0
                self._go(ISLANDED if soc > self.soc_floor else SHUTDOWN, t, 'grid lost again')
            else:
                self.stable += 1
                if self.stable >= self.resync_min_steps:
                    self._go(RECONNECT, t, 'grid stable: synchronise')
        elif s == RECONNECT:
            self.tie_closed = True                        # close only after sync check
            self._go(GRID, t, 'reconnected')
        elif s == SHUTDOWN:
            if grid_ok:
                self.stable = 1
                self._go(RESYNC, t, 'grid returned during shutdown')
            elif soc >= self.soc_restart:
                self._go(BLACKSTART, t, 'restart threshold reached')
        elif s == BLACKSTART:
            self._go(ISLANDED, t, 'grid-forming restart')
        assert not (self.state == ISLANDED and self.tie_closed), 'invariant: islanded with tie closed'
        return dict(state=self.state, tie_closed=self.tie_closed,
                    serves_essential=self.state == ISLANDED)
