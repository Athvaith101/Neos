import unittest, subprocess, sys, os
from unittest import mock
from tests.common import *
from neos.config import NeighbourhoodConfig, ConfigError
from neos.world import build_neighbourhood
from neos.seeds import stable_seed, scenario_seed
from neos import service

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestSeeds(unittest.TestCase):
    def test_stable_across_processes_with_different_hash_seeds(self):
        code = "from neos.seeds import scenario_seed; print(scenario_seed('cloud', 2, 7))"
        outs = set()
        for hs in ('0', '1', '12345', 'random'):
            env = dict(os.environ, PYTHONHASHSEED=hs, PYTHONPATH=ROOT)
            outs.add(subprocess.check_output([sys.executable, '-c', code], env=env, cwd=ROOT).strip())
        self.assertEqual(len(outs), 1)

    def test_distinct_inputs_distinct_seeds(self):
        s = {scenario_seed(r, k) for r in ('normal', 'cloud', 'heat') for k in range(5)}
        self.assertEqual(len(s), 15)


class TestConfig(unittest.TestCase):
    def test_cross_field_validation(self):
        for bad in (dict(n_ev=500, n_homes=100), dict(n_bess=40, n_pv=10), dict(n_pv=999, n_homes=100),
                    dict(tx_kva=-1), dict(n_homes=3), dict(backend='x')):
            with self.assertRaises(ConfigError, msg=str(bad)):
                NeighbourhoodConfig(**bad)
        NeighbourhoodConfig()                              # defaults are valid

    def test_n_comm_and_tx_kva_are_honoured(self):
        nb = build_neighbourhood(n_comm=4, tx_kva=400.0)
        self.assertEqual(len(nb.commercials), 4); self.assertEqual(nb.tx_kva, 400.0)
        self.assertEqual(len(build_neighbourhood(n_comm=0).commercials), 0)

    def test_meta_reports_configured_rating(self):
        with mock.patch.object(service.F, 'fit_operational', return_value=None):
            m = service.meta(dict(SMALL, tx_kva=333.0))
        self.assertEqual(m['tx_kva'], 333.0); self.assertEqual(m['commercial'], SMALL['n_comm'])

    def test_world_cache_is_bounded(self):
        with mock.patch.object(service.F, 'fit_operational', return_value=None):
            for s in range(service.MAX_WORLDS + 3):
                service.get_world(dict(SMALL, seed=100 + s))
        self.assertLessEqual(len(service._cache), service.MAX_WORLDS)


class TestCalibration(unittest.TestCase):
    def test_recovers_impedance_and_closes_verification_gap(self):
        from neos.calibration import calibration_study
        from neos.grid import ReferenceGrid
        nb = small_nb()
        r = calibration_study(nb, lambda: ReferenceGrid(nb, 200.0), true_scale=1.4, n_fit=25, n_test=120)
        self.assertAlmostEqual(r['estimated_scale'], 1.4, delta=0.06)
        self.assertLessEqual(r['after']['v_rmse'], r['before']['v_rmse'] / 3)
        self.assertLessEqual(r['after']['false_safe_rate'], r['before']['false_safe_rate'])


if __name__ == '__main__':
    unittest.main()
