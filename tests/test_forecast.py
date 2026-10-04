import unittest
import numpy as np
from tests.common import *
from neos import forecast as F, nwp
from neos.world import T


def series(n=T * 8, seed=0):
    r = np.random.default_rng(seed)
    t = np.arange(n)
    y = 300 + 80 * np.sin(2 * np.pi * t / T) + r.normal(0, 5, n)
    temp = 30 + 5 * np.sin(2 * np.pi * (t - 20) / T)
    cs = np.maximum(0, np.sin(2 * np.pi * (t % T - 24) / T))
    return y, temp, cs, np.clip(0.8 + 0.1 * np.sin(t / 7), 0.05, 1)


class TestCausality(unittest.TestCase):
    def test_future_perturbation_does_not_change_features(self):
        y, temp, cs, kt = series()
        for O, H in [(4 * T + 10, 104), (5 * T, 24), (6 * T + 47, 96)]:
            X1, t1, l1 = F.features_for_origin(y, temp, temp, cs, kt, O, H)
            y2 = y.copy(); y2[O + 1:] += np.random.default_rng(1).normal(0, 1000, len(y) - O - 1)
            temp2 = temp.copy(); temp2[O + 1:] += 50          # realised future temperature
            X2, t2, l2 = F.features_for_origin(y2, temp2, temp, cs, kt, O, H)
            np.testing.assert_array_equal(X1, X2)

    def test_reading_past_origin_raises(self):
        y = np.arange(500.0)
        with self.assertRaises(AssertionError):
            F._Y(y, 100)[101]
        F._Y(y, 100)[100]                                    # equal to origin is fine

    def test_24h_lag_is_not_beyond_cutoff(self):
        """The old y[t-T+1] feature at horizon 96 pointed one step past the origin."""
        y, temp, cs, kt = series(); O = 5 * T
        X, t, lead = F.features_for_origin(y, temp, temp, cs, kt, O, 96)
        k = np.ceil(lead / T).astype(int)
        self.assertTrue(np.all(t - k * T <= O))

    def test_operational_window_has_no_nan_leak(self):
        y, temp, cs, kt = series(); O = 5 * T
        class M:                                              # model stub
            def predict(self, X, lead): return np.vstack([X[:, 2] - 1, X[:, 2], X[:, 2] + 1])
        P, tt = F.forecast_window(M(), y[:O + 1], temp, cs, kt, temp, O, 48)
        self.assertTrue(np.all(np.isfinite(P)))
        # prediction must not depend on realised y after O:
        y2 = y.copy(); y2[O + 1:] = 9999
        P2, _ = F.forecast_window(M(), y2[:O + 1], temp, cs, kt, temp, O, 48)
        np.testing.assert_array_equal(P, P2)

    def test_nwp_knows_past_not_future(self):
        w = dict(kt_true=np.linspace(0.3, 0.9, 400), temp=np.linspace(25, 40, 400))
        f = nwp.issue_forecast(w, 200, 0.7, np.full(T, 30.0), seed=1, n_members=20)
        np.testing.assert_array_equal(f['kt_mean'][:201], w['kt_true'][:201])
        self.assertFalse(np.allclose(f['kt_mean'][250:], w['kt_true'][250:]))
        spread_near = f['kt_members'][:, 205].std(); spread_far = f['kt_members'][:, 380].std()
        self.assertGreater(spread_far, spread_near)           # uncertainty grows with lead


class TestScores(unittest.TestCase):
    def test_mean_pinball_is_not_called_crps(self):
        self.assertFalse(hasattr(F, 'crps_approx'))
        y = np.array([1.0, 2.0]); q = [y - 1, y, y + 1]
        self.assertAlmostEqual(F.mean_pinball(y, q), np.mean([0.1, 0.0, 0.1 * 0 + 0.9 * 1 * 0 + 0.1]), places=1)

    def test_interval_score_penalises_misses(self):
        y = np.array([10.0]); self.assertEqual(F.interval_score(y, np.array([9.0]), np.array([11.0])), 2.0)
        self.assertGreater(F.interval_score(y, np.array([0.0]), np.array([5.0])), 5.0)

    def test_coverage_by_lead_buckets(self):
        y = np.zeros(300); lead = np.arange(300) % 96 + 1
        P = [y - 1, y, y + 1]
        c = F.coverage_by_lead(y, P, lead)
        self.assertEqual(set(c), set(F.LEAD_NAMES)); self.assertTrue(all(v == 1.0 for v in c.values()))


if __name__ == '__main__':
    unittest.main()
