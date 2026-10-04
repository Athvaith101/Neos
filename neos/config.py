"""config.py -- validated configuration with cross-field constraints."""
from __future__ import annotations
from dataclasses import dataclass, asdict


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class NeighbourhoodConfig:
    n_homes: int = 300
    n_ev: int = 60
    n_pv: int = 150
    n_bess: int = 26
    n_comm: int = 10
    tx_kva: float = 630.0
    seed: int = 7
    n_feeders: int = 4
    seg_per_feeder: int = 10
    backend: str = "auto"           # auto | opendss | reference

    def __post_init__(self):
        e = []
        if not 10 <= self.n_homes <= 2000:
            e.append("n_homes must be in [10, 2000]")
        for nm in ("n_ev", "n_pv", "n_bess", "n_comm"):
            if getattr(self, nm) < 0:
                e.append(f"{nm} must be >= 0")
        if self.n_ev > self.n_homes:
            e.append("n_ev cannot exceed n_homes (one EV per household at most)")
        if self.n_pv > self.n_homes:
            e.append("n_pv cannot exceed n_homes")
        if self.n_bess > self.n_pv:
            e.append("n_bess cannot exceed n_pv (storage is co-located with rooftop PV)")
        if self.n_comm > 60:
            e.append("n_comm must be <= 60")
        if not 100.0 <= self.tx_kva <= 5000.0:
            e.append("tx_kva must be in [100, 5000]")
        if self.n_feeders < 1 or self.seg_per_feeder < 2:
            e.append("need >=1 feeder and >=2 segments per feeder")
        if self.n_homes > 0 and (self.n_homes / (self.n_feeders * self.seg_per_feeder)) > 40:
            e.append("more than 40 homes per bus: network too coarse")
        if self.backend not in ("auto", "opendss", "reference"):
            e.append("backend must be auto|opendss|reference")
        if e:
            raise ConfigError("; ".join(e))

    def key(self):
        return tuple(sorted(asdict(self).items()))

    def to_dict(self):
        return asdict(self)
