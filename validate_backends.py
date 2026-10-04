"""
validate_backends.py -- cross-check the NumPy reference solver against OpenDSS.

Run this on a machine with `pip install opendssdirect.py`. It solves identical
injections on both backends and prints the discrepancies. The reference solver
may only be used as evidence once these are small.
"""
import sys
import numpy as np
from neos.grid import ReferenceGrid, OpenDSSGrid, have_opendss
from neos.world import build_neighbourhood
from neos.calibration import random_operating_points

if not have_opendss():
    print("OpenDSSDirect.py is not installed: nothing to validate against.\n"
          "  pip install opendssdirect.py   then re-run.")
    sys.exit(0)

nb = build_neighbourhood()
ref, dss = ReferenceGrid(nb), OpenDSSGrid(nb)
pts = random_operating_points(nb, 60, np.random.default_rng(0), -600, 600)
rows = []
for p in pts:
    a, b = ref.solve(p), dss.solve(p)
    rows.append([abs(a['vmin'] - b['vmin']), abs(a['vmax'] - b['vmax']),
                 abs(a['tx_kw'] - b['tx_kw']) / max(1.0, abs(b['tx_kw'])),
                 abs(a['losses_kw'] - b['losses_kw']), abs(a['line_loading_max'] - b['line_loading_max'])])
R = np.array(rows)
print(f"{'':20s}{'mean':>10s}{'p95':>10s}{'max':>10s}")
for i, n in enumerate(['|dVmin| (pu)', '|dVmax| (pu)', 'rel d tx_kw', '|d losses| (kW)', '|d line %|']):
    print(f"{n:20s}{R[:, i].mean():10.5f}{np.percentile(R[:, i], 95):10.5f}{R[:, i].max():10.5f}")
