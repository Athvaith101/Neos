"""
claims.py -- Claim register and language audit.

CLAIMS lists what the product may say and the evidence ids that must exist (current,
validated) for it to be said. FORBIDDEN lists wording the project must not use unless
the same line negates it ("not guaranteed", "no payment", ...).  audit_text() flags every
un-negated occurrence so marketing language cannot drift past the evidence.
"""
from __future__ import annotations
import re

FORBIDDEN = [
    r"\bguarantee[sd]?\b", r"\bcertified\b", r"\bapproved (for|by)\b", r"\bblockchain\b",
    r"\benerg(y)? trading\b", r"\bP2P trading\b", r"\bsettlement\b", r"\bpayment[s]?\b",
    r"\beliminat(e|es|ed|ing) (all )?(outages|blackouts|load.?shedding)\b",
    r"\bprevent(s|ed)? (all )?(outages|blackouts)\b", r"\bzero outages\b", r"\bproven\b",
    r"\bfield[- ]validated\b", r"\bproduction[- ]ready\b", r"\bautonomous(ly)? control(s|led)? (the )?grid\b",
]
NEGATIONS = re.compile(r"\b(not|no|never|nor|without|cannot|can't|isn't|aren't|nothing|none|neither|"
                       r"does not|do not|don't|doesn't|unless|must not|never be|ban|banned|forbidden|"
                       r"avoid|rather than|instead of|exclude[sd]?|out of scope|prohibit|"
                       r"not claimed|non-claim|disclaim|no claim)\b", re.I)

CLAIMS = [
    dict(id='C1', text='In simulation, coordinated dispatch reduced peak transformer loading versus no coordination in the urban kit.',
         needs=['urban.peak.normal.delta'], allowed='SIMULATED only'),
    dict(id='C2', text='Every dispatch step carries an explicit constraint status (verified / infeasible / solver_failed).',
         needs=[], allowed='DESIGNED, tested'),
    dict(id='C3', text='In simulation, the community hub reduced hours without essential supply versus no hub.',
         needs=['low_income.hours_unmet.battery_only'], allowed='SIMULATED only'),
    dict(id='C4', text='In simulation, tiered service protected critical load longer than tier-blind shedding in the rural kit.',
         needs=['rural.crit_hours.outage_evening.neos'], allowed='SIMULATED only'),
    dict(id='C5', text='Delivery records are tamper-evident through a hash chain; expired commands are rejected.',
         needs=[], allowed='DESIGNED, tested'),
    dict(id='C6', text='The flexibility envelope is an estimate of available flexibility, not guaranteed delivery.',
         needs=[], allowed='DESIGNED'),
]
NON_CLAIMS = [
    'real-world performance', 'field validation', 'certified islanding or protection',
    'guaranteed outage elimination', 'blockchain or energy trading', 'payments or settlement',
    'appliance identification from aggregate meter data', 'a MARL policy (not built)',
]


def audit_text(name, text):
    """Return a list of (file, line_no, phrase, line) for un-negated forbidden wording."""
    hits = []
    for i, line in enumerate(text.splitlines(), 1):
        for pat in FORBIDDEN:
            m = re.search(pat, line, re.I)
            if m and not NEGATIONS.search(line):
                hits.append((name, i, m.group(0), line.strip()[:160]))
    return hits
