import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from neos.config import NeighbourhoodConfig
from neos.world import build_neighbourhood
from neos.grid import ReferenceGrid

SMALL = dict(n_homes=60, n_ev=14, n_pv=30, n_bess=6, n_comm=3, tx_kva=200.0, seed=3)


def small_nb(**kw):
    c = dict(SMALL); c.update(kw)
    c.pop('seed_', None)
    return build_neighbourhood(seed=c['seed'], n_homes=c['n_homes'], n_ev=c['n_ev'], n_pv=c['n_pv'],
                               n_bess=c['n_bess'], n_comm=c['n_comm'], tx_kva=c['tx_kva'])


def spread(nb, kw):
    inj = {}
    for h in nb.homes:
        inj[(h.node, h.phase)] = inj.get((h.node, h.phase), 0.0) + kw / len(nb.homes)
    return inj
