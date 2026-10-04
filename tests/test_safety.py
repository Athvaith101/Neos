import unittest
from tests.common import *
from neos.grid import ReferenceGrid, constraint_report
from neos.control import safety_project, compose


class Mock:
    """A grid whose solve() can be scripted -- for failure-mode tests."""
    backend = 'mock'
    def __init__(self, states): self.states, self.i = states, 0
    def solve(self, inj):
        s = self.states[min(self.i, len(self.states) - 1)]; self.i += 1; return dict(s)


def ok_state(**kw):
    s = dict(converged=True, finite=True, vmin=1.0, vmax=1.02, tx_loading=50.0, line_loading_max=30.0,
             tx_kw=100.0, v_violations=0, losses_kw=1.0)
    s.update(kw); return s


class TestSafety(unittest.TestCase):
    def setUp(self):
        self.nb = small_nb(); self.g = ReferenceGrid(self.nb, 200.0)
        hs = self.nb.homes
        self.keys = sorted({(h.node, h.phase) for h in hs})

    def groups(self, ev=40.0, batt_dis=0.0, appl=0.0, prot=0.0):
        d = lambda tot, sign=1: {k: sign * tot / len(self.keys) for k in self.keys} if tot else {}
        return {'ev': d(ev), 'appl': d(appl), 'protected_ev': d(prot), 'batt_chg': {}, 'batt_dis': d(batt_dis, -1)}

    def test_returned_state_is_the_solve_of_the_returned_action(self):
        """P0: with a_flex driven to zero the old code returned a STALE state."""
        base = spread(self.nb, 190)                    # inflexible load alone ~ the rating
        g = self.groups(ev=80.0)
        res = safety_project(self.g, base, {}, g)
        fresh = self.g.solve(compose(base, {}, g, res.scales, res.pv_scale))
        for k in ('vmin', 'vmax', 'tx_loading', 'tx_kw', 'line_loading_max'):
            self.assertAlmostEqual(res.state[k], fresh[k], places=9, msg=k)

    def test_flex_scale_zero_still_solves_zero_action(self):
        base = spread(self.nb, 260)                    # overload even with zero flex
        g = self.groups(ev=60.0)
        res = safety_project(self.g, base, {}, g)
        self.assertEqual(res.scales['ev'], 0.0)
        self.assertEqual(res.status, 'infeasible')     # honest, not 'verified'
        fresh = self.g.solve(compose(base, {}, g, res.scales, res.pv_scale))
        self.assertAlmostEqual(res.state['tx_loading'], fresh['tx_loading'], places=9)
        self.assertIn('transformer_overload', res.report['violations'])

    def test_verified_when_clear_and_untouched(self):
        res = safety_project(self.g, spread(self.nb, 40), {}, self.groups(ev=10.0))
        self.assertEqual(res.status, 'verified'); self.assertEqual(res.scales['ev'], 1.0)
        self.assertEqual(res.n_interventions, 0)

    def test_solver_failure_reported_explicitly(self):
        bad = ok_state(converged=False)
        res = safety_project(Mock([bad, bad, bad]), {}, {}, {'ev': {('f0_1', 1): 5.0}})
        self.assertEqual(res.status, 'solver_failed')
        self.assertFalse(res.report['ok'])

    def test_import_stress_never_throttles_discharge(self):
        """P1: reducing helpful discharge worsens an import overload."""
        base = spread(self.nb, 150)
        g = self.groups(ev=60.0, batt_dis=20.0)
        res = safety_project(self.g, base, {}, g)
        self.assertLess(res.scales['ev'], 1.0)          # load was shed
        self.assertEqual(res.scales['batt_dis'], 1.0)   # discharge was NOT

    def test_export_stress_never_sheds_loads(self):
        pv = {k: -80.0 / len(self.keys) for k in self.keys}
        pv = {k: v * 6 for k, v in pv.items()}         # strong overvoltage / reverse flow
        g = self.groups(ev=15.0)
        res = safety_project(self.g, spread(self.nb, 5), pv, g)
        self.assertEqual(res.scales['ev'], 1.0)         # loads help export stress
        self.assertLess(res.pv_scale, 1.0)              # PV curtailed instead

    def test_protected_ev_sacrificed_last(self):
        base = spread(self.nb, 120)
        g = self.groups(ev=40.0, prot=10.0)
        res = safety_project(self.g, base, {}, g)
        if res.scales['ev'] > 0:
            self.assertEqual(res.scales['protected_ev'], 1.0)

    def test_line_ampacity_triggers_projection(self):
        nb = small_nb(); g = ReferenceGrid(nb, 5000.0)
        far = max(nb.nodes, key=lambda n: n[2])[0]
        grp = {'ev': {(far, 1): 100.0, (far, 2): 100.0, (far, 3): 100.0}}
        res = safety_project(g, {}, {}, grp)
        self.assertLess(res.scales['ev'], 1.0)
        self.assertLessEqual(res.state['line_loading_max'], 100.0 + 1e-6)
        self.assertEqual(res.status, 'verified')


if __name__ == '__main__':
    unittest.main()
