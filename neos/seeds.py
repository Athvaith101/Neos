"""seeds.py -- stable, process-independent seeding and run provenance.

Python's built-in hash() of a str is randomised per process (PYTHONHASHSEED),
so `hash(regime) % 9999` produced DIFFERENT scenarios on every launch. All
seeds now derive from SHA-256 of an explicit tuple.
"""
from __future__ import annotations
import hashlib, json, platform, sys
from pathlib import Path


def stable_seed(*parts, mod=2**31 - 1) -> int:
    s = "|".join(str(p) for p in parts).encode()
    return int.from_bytes(hashlib.sha256(s).digest()[:8], "big") % mod


def scenario_seed(regime: str, replicate: int = 0, base: int = 0) -> int:
    return stable_seed("scenario", regime, replicate, base)


def source_hashes(root: Path | None = None) -> dict:
    root = root or Path(__file__).parent
    out = {}
    for p in sorted(root.glob("*.py")):
        out[p.name] = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
    return out


def provenance(cfg=None, extra=None) -> dict:
    import numpy, scipy, sklearn
    try:
        import opendssdirect
        dssv = getattr(opendssdirect, "__version__", "installed")
    except Exception:
        dssv = None
    d = dict(python=sys.version.split()[0], platform=platform.platform(),
             numpy=numpy.__version__, scipy=scipy.__version__,
             sklearn=sklearn.__version__, opendssdirect=dssv,
             source_sha256_16=source_hashes())
    if cfg is not None:
        d["config"] = cfg if isinstance(cfg, dict) else cfg.__dict__
    if extra:
        d.update(extra)
    return d
