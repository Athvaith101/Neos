"""validate_platform.py -- run the platform modules on OpenDSS and cross-check against the
independent NumPy reference solver. Exit code 1 on any disagreement.

  python validate_platform.py

Checks: (1) per-phase transformer kVA, unbalanced cases; (2) every rural scenario x arm:
OpenDSS-backed results satisfy the same invariants as the reference and agree with it on voltages,
service and energy; (3) every island step is verified."""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
from neos.grid import ReferenceGrid, OpenDSSGrid, have_opendss
from neos import rural
from tests.common import small_nb, spread

if not have_opendss():
    print('OpenDSSDirect.py not installed: nothing to validate'); sys.exit(2)
bad = []
def chk(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (' ' + detail if detail else ''))
    if not ok: bad.append(name)

nb = small_nb(); R = ReferenceGrid(nb, 200.0); O = OpenDSSGrid(nb, 200.0)
cases = {'phase A only': {(nb.nodes[0][0], 1): 60.0}, 'phase B only': {(nb.nodes[3][0], 2): 50.0},
         'mixed': {(nb.nodes[1][0], 1): 40.0, (nb.nodes[2][0], 2): 20.0, (nb.nodes[4][0], 3): 10.0},
         'balanced': spread(nb, 90), 'export (PV)': {(nb.nodes[2][0], 1): -30.0, (nb.nodes[5][0], 3): 10.0}}
worst = 0.0
for n, inj in cases.items():
    r, o = R.solve(inj), O.solve(inj)
    d = max(abs(a - b) for a, b in zip(r['tx_phase_kva'], o['tx_phase_kva']))
    worst = max(worst, d)
    chk(f'phase kVA agrees [{n}]', d < 0.05, f'max |diff| = {d:.4f} kVA')
chk('phase kVA overall', worst < 0.05, f'worst {worst:.4f} kVA')

rows = []
for sc in rural.SCENARIOS:
    for arm in ('naive', 'neos'):
        mo, so = rural.run_rural(sc, arm, backend='opendss'); mr, sr = rural.run_rural(sc, arm, backend='reference')
        assert mo['backend'] == 'opendss' and mr['backend'] == 'reference'
        dv = abs(mo['min_voltage'] - mr['min_voltage'])
        same = all(abs(mo[k] - mr[k]) < 0.5 for k in ('clinic_unserved_kwh', 'tier2_unserved_kwh', 'shops_unserved_kwh', 'cold_chain_hours_above_8C'))
        chk(f'{sc}/{arm} OpenDSS vs reference', dv < 5e-3 and same,
            f"dVmin={dv:.4f} clinic_uns {mo['clinic_unserved_kwh']:.2f}/{mr['clinic_unserved_kwh']:.2f}")
        chk(f'{sc}/{arm} island steps all electrically verified', mo['island_steps_verified_pct'] in (None, 100.0),
            str(mo['island_steps_verified_pct']))
        chk(f'{sc}/{arm} battery energy audit + no clipping', mo['energy_audit']['battery_residual_kwh'] < 1e-8 and mo['energy_audit']['clip_events'] == 0)
        rows.append(dict(scenario=sc, arm=arm, opendss=mo['clinic_unserved_kwh'], reference=mr['clinic_unserved_kwh'], dvmin=dv))
print('\n' + ('ALL PASS' if not bad else f'{len(bad)} FAILED: {bad}'))
sys.exit(1 if bad else 0)
