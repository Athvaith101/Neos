"""
evidence.py -- Evidence contract and build gate.

Every metric the product may display is an EvidenceRecord:

    id, kit, tag, label, value, unit, source, seeds, config_hash, version, source_hash, status, note

tag     SOURCED     taken from a cited external source (none are used in this build)
        SIMULATED   produced by a seeded simulation in this repo (needs seeds + config hash)
        ASSUMED     a declared modelling assumption (never presented as a finding)
        DESIGNED    a design rule / architecture statement with no measured number
        UNKNOWN     explicitly unknown (value must be None)
        HISTORICAL  an old number kept for comparison ONLY (never current, never a claim)

validate() returns a list of errors; `check_evidence.py` exits non-zero if any exist, so a
missing seed, a stale result, a cross-kit reuse or a historical number shown as current
BREAKS THE BUILD instead of reaching the interface.
"""
from __future__ import annotations
import math, json
from dataclasses import dataclass, asdict, field

TAGS = ('SOURCED', 'SIMULATED', 'ASSUMED', 'DESIGNED', 'UNKNOWN', 'HISTORICAL')
KITS = ('urban', 'low_income', 'rural', 'platform')
STATUSES = ('current', 'historical', 'superseded')

# which kit each source module may legitimately feed
SOURCE_KIT = {
    'hub.py': 'low_income', 'discom.py': 'low_income',
    'rural.py': 'rural', 'reserve_experiment.py': 'rural',
    'runner.py': 'urban', 'service.py': 'urban', 'calibration.py': 'urban', 'experiments.py': 'urban',
    'control.py': 'urban', 'forecast.py': 'urban',
    # shared platform pieces carry only 'platform' (DESIGNED/ASSUMED) records
    'ledger.py': 'platform', 'envelope.py': 'platform', 'phase.py': 'platform', 'islanding.py': 'platform',
    'kits.py': 'platform', 'trace.py': 'platform', 'evidence.py': 'platform',
}
# files whose change makes a kit's published results stale
KIT_SOURCES = {
    'urban': ['runner.py', 'control.py', 'controllers.py', 'grid.py', 'forecast.py', 'nwp.py', 'service.py',
              'world.py', 'dataset.py', 'config.py', 'phase.py', 'calibration.py', 'telemetry.py'],
    'low_income': ['hub.py', 'reserve.py', 'ledger.py', 'envelope.py', 'discom.py'],
    'rural': ['rural.py', 'resources.py', 'islanding.py', 'reserve.py', 'reserve_experiment.py', 'trace.py',
              'grid.py', 'control.py'],
    'platform': [],
}
KIT_SCRIPTS = {'urban': ['experiments.py'], 'low_income': ['experiments_resilience.py'],
               'rural': ['experiments_resilience.py'], 'platform': []}


@dataclass
class EvidenceRecord:
    id: str
    kit: str
    tag: str
    label: str
    value: object = None
    unit: str = ''
    source: str = ''               # module that produced it (e.g. 'hub.py')
    seeds: list | None = None
    config_hash: str = ''
    version: str = ''
    source_hash: str = ''          # hash of the kit's source set when the result was generated
    status: str = 'current'
    note: str = ''
    shown_in_kits: list = field(default_factory=list)   # where the UI will display it


def kit_source_hash(hashes: dict, kit: str) -> str:
    """Combine recorded per-file hashes for the kit's source set into one id."""
    import hashlib
    h = hashlib.sha256()
    src = hashes.get('source_sha256_16', {})
    scr = hashes.get('script_sha256_16', {})
    for f in KIT_SOURCES.get(kit, []):
        h.update((f + ':' + src.get(f, 'MISSING')).encode())
    for f in KIT_SCRIPTS.get(kit, []):
        h.update((f + ':' + scr.get(f, 'MISSING')).encode())
    return h.hexdigest()[:16]


def validate(records, current_hashes=None, today_version=None):
    """Returns a list of human-readable errors (empty list = gate passes)."""
    errs, seen = [], set()
    for r in records:
        d = r if isinstance(r, dict) else asdict(r)
        rid = d.get('id', '<no id>')
        if rid in seen:
            errs.append(f"{rid}: duplicate id")
        seen.add(rid)
        for req in ('id', 'kit', 'tag', 'label', 'source', 'version'):
            if not d.get(req):
                errs.append(f"{rid}: missing required field '{req}'")
        if d.get('kit') not in KITS:
            errs.append(f"{rid}: unknown kit '{d.get('kit')}'")
        if d.get('tag') not in TAGS:
            errs.append(f"{rid}: unknown tag '{d.get('tag')}'")
        if d.get('status') not in STATUSES:
            errs.append(f"{rid}: unknown status '{d.get('status')}'")
        v = d.get('value')
        if isinstance(v, float) and not math.isfinite(v):
            errs.append(f"{rid}: non-finite value")
        if isinstance(v, (list, tuple)) and any(isinstance(x, float) and not math.isfinite(x) for x in v):
            errs.append(f"{rid}: non-finite value in list")
        tag = d.get('tag')
        if tag == 'SIMULATED':
            if not d.get('seeds'):
                errs.append(f"{rid}: SIMULATED metric has no seeds")
            if not d.get('config_hash'):
                errs.append(f"{rid}: SIMULATED metric has no config hash")
        if tag == 'HISTORICAL' and d.get('status') == 'current':
            errs.append(f"{rid}: HISTORICAL number marked current (it may be displayed only as historical)")
        if d.get('status') == 'current' and tag == 'HISTORICAL':
            pass
        if tag == 'UNKNOWN' and v is not None:
            errs.append(f"{rid}: UNKNOWN metric carries a value")
        if tag in ('SIMULATED', 'ASSUMED', 'HISTORICAL') and v is None and tag != 'UNKNOWN':
            errs.append(f"{rid}: {tag} metric has no value")
        src, kit = d.get('source'), d.get('kit')
        want = SOURCE_KIT.get(src)
        if src in ('experiments_resilience.py',):
            want = None
        if want is not None and kit != want and want != 'platform':
            errs.append(f"{rid}: kit '{kit}' differs from the kit of its source '{src}' ('{want}'): cross-kit reuse")
        for k in d.get('shown_in_kits') or []:
            if kit != 'platform' and k != kit:
                errs.append(f"{rid}: {kit} evidence is displayed in the {k} kit")
        if current_hashes is not None and d.get('status') == 'current' and kit in KIT_SOURCES and tag == 'SIMULATED':
            want_h = current_hashes.get(kit)
            if want_h and d.get('source_hash') != want_h:
                errs.append(f"{rid}: STALE: source changed since this result was generated (regenerate)")
    return errs


def summarise(records):
    out = {}
    for r in records:
        d = r if isinstance(r, dict) else asdict(r)
        k = out.setdefault(d['kit'], {})
        k[d['tag']] = k.get(d['tag'], 0) + 1
    return out
