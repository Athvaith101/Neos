"""
trace.py -- Decision Trace: every important dispatch decision is explainable.

Each entry answers: what changed, why, which forecast drove it, what uncertainty
existed, which resource was chosen, which constraints were binding, what reserve
was protected, did the physics check pass, and what actually happened.
The text is generated from live simulation values, never hand-written.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict


@dataclass
class TraceEntry:
    step: int
    hour: float
    kit: str
    action: str                      # e.g. "start pump P1"
    why: str
    resource: str = ""
    forecast: dict = field(default_factory=dict)       # which forecast drove it
    uncertainty: dict = field(default_factory=dict)    # e.g. {'pv_p10_kw':..,'pv_p90_kw':..}
    binding: list = field(default_factory=list)        # binding constraints
    reserve_protected: str = ""                        # e.g. "battery SOC >= 40 % (fixed)"
    alternative: str = ""
    physics: str = "not_checked"                       # PASS | FAIL:<why> | not_modelled | not_checked
    expected: str = ""
    actual: str = "pending"
    kind: str = "dispatch"           # dispatch | shed | protect | reject | island | envelope

    def text(self):
        hh = int(self.hour) % 24; mm = int(round((self.hour % 1) * 60)) % 60
        lines = [f"WHY DID NEOS DO THIS?   [{self.kit}]  step {self.step}  {hh:02d}:{mm:02d}",
                 f"  Action:       {self.action}",
                 f"  Reason:       {self.why}"]
        if self.forecast:
            lines.append("  Forecast:     " + ", ".join(f"{k}={v}" for k, v in self.forecast.items()))
        if self.uncertainty:
            lines.append("  Uncertainty:  " + ", ".join(f"{k}={v}" for k, v in self.uncertainty.items()))
        if self.resource:
            lines.append(f"  Resource:     {self.resource}")
        if self.binding:
            lines.append("  Binding:      " + "; ".join(self.binding))
        if self.reserve_protected:
            lines.append(f"  Reserve:      {self.reserve_protected}")
        if self.alternative:
            lines.append(f"  Alternative:  {self.alternative}")
        lines.append(f"  Physics:      {self.physics}")
        lines.append(f"  Expected:     {self.expected or 'n/a'}")
        lines.append(f"  Actual:       {self.actual}")
        return "\n".join(lines)


class DecisionTrace:
    def __init__(self, kit, max_entries=4000):
        self.kit, self.entries, self.max = kit, [], max_entries
        self.dropped = 0

    def add(self, step, hour, action, why, **kw):
        if len(self.entries) >= self.max:
            self.dropped += 1
            return None
        e = TraceEntry(step=step, hour=hour, kit=self.kit, action=action, why=why, **kw)
        self.entries.append(e)
        return e

    def completeness(self):
        """Fraction of dispatch/shed entries with a recorded outcome and a physics status."""
        d = [e for e in self.entries if e.kind in ('dispatch', 'shed', 'protect')]
        if not d:
            return 1.0
        return sum(1 for e in d if e.actual != 'pending' and e.physics != 'not_checked') / len(d)

    def to_json(self, limit=None):
        es = self.entries if limit is None else self.entries[:limit]
        return [asdict(e) | {"text": e.text()} for e in es]

    def by_kind(self):
        out = {}
        for e in self.entries:
            out[e.kind] = out.get(e.kind, 0) + 1
        return out
