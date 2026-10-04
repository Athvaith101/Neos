import unittest
from dataclasses import dataclass, field
from tests.common import *
from neos import service
from neos.runner import run_scenario
from neos.decision_trace import build_trace, REQUIRED_STATUSES

CFG = dict(SMALL)
_CACHE = {}


def prep(regime='normal', rep=0):
    k = (regime, rep)
    if k not in _CACHE:
        _CACHE[k] = service.prepare(CFG, regime, rep)
    return _CACHE[k]


@dataclass
class FakeResult:
    """Mirrors control.SafetyResult's shape without needing a real solve, so
    the status/wording contract can be tested in isolation."""
    status: str
    state: dict = field(default_factory=dict)
    scales: dict = field(default_factory=dict)
    report: dict = field(default_factory=dict)
    n_interventions: int = 0


def _obs():
    return dict(hour=12.0)


def _fc(n=8):
    import numpy as np
    return dict(inflex=dict(p10=np.full(n, 90.0), p50=np.full(n, 100.0), p90=np.full(n, 120.0)),
               pv=dict(p10=np.full(n, 0.0), p50=np.full(n, 0.0), p90=np.full(n, 0.0)))


class TestRequiredStatusContract(unittest.TestCase):
    """Roadmap section 6: VERIFIED / INFEASIBLE / SOLVER_FAILED must never be
    collapsed into a generic success/failure flag."""

    def test_all_three_statuses_pass_through_uppercase_unmodified(self):
        pst = dict(tx_loading=50.0, vmin=1.0, vmax=1.0, finite=True)
        for status in REQUIRED_STATUSES:
            res = FakeResult(status=status, scales={}, report={'violations': []})
            tr = build_trace(0, 48, _obs(), _fc(), {}, {}, {}, {}, {}, res, pst, 'test_ctrl')
            self.assertEqual(tr.safety_status, status.upper())

    def test_unknown_status_raises_rather_than_silently_coercing(self):
        pst = dict(tx_loading=50.0, vmin=1.0, vmax=1.0, finite=True)
        res = FakeResult(status='mostly_fine', scales={}, report={'violations': []})
        with self.assertRaises(ValueError):
            build_trace(0, 48, _obs(), _fc(), {}, {}, {}, {}, {}, res, pst, 'test_ctrl')

    def test_solver_failed_result_fields_are_nan_not_fabricated(self):
        """A solver_failed state's numeric fields are NaN upstream (grid.py);
        the trace must carry that through, not substitute a stale number."""
        pst = dict(tx_loading=float('nan'), vmin=float('nan'), vmax=float('nan'), finite=False)
        res = FakeResult(status='solver_failed', scales={}, report={'violations': ['solver_failed']})
        tr = build_trace(0, 48, _obs(), _fc(), {}, {}, {}, {}, {}, res, pst, 'test_ctrl')
        self.assertTrue(tr.result_tx_loading_pct != tr.result_tx_loading_pct)  # NaN != NaN
        self.assertIn('must NOT be treated as confirmed safe', tr.reason)


class TestReasonNamesTheActualThrottledGroup(unittest.TestCase):
    """Regression test for a real bug found while building this feature: the
    reason text originally read the FINAL state's constraint-violation TYPES
    (near-always empty, since an intervention's whole point is that the
    reduced action verifies clean) instead of WHICH safety group was actually
    scaled down. It said 'reduced one or more resource groups' without ever
    naming one."""

    def test_names_the_group_and_its_scale(self):
        pst = dict(tx_loading=61.4, vmin=0.99, vmax=1.01, finite=True)
        res = FakeResult(status='verified', scales={'ev': 1.0, 'appl': 0.977, 'batt_chg': 1.0},
                         report={'violations': []}, n_interventions=1)
        tr = build_trace(0, 48, _obs(), _fc(), {}, {}, {}, {}, {}, res, pst, 'test_ctrl')
        self.assertIn('appl', tr.reason)
        self.assertIn('98%', tr.reason)          # 0.977 -> "98% of requested"
        self.assertNotIn('ev to', tr.reason)     # ev was NOT throttled (scale 1.0)

    def test_zero_interventions_gives_clean_acceptance_wording(self):
        pst = dict(tx_loading=40.0, vmin=0.99, vmax=1.01, finite=True)
        res = FakeResult(status='verified', scales={'ev': 1.0}, report={'violations': []},
                         n_interventions=0)
        tr = build_trace(0, 48, _obs(), _fc(), {}, {}, {}, {}, {}, res, pst, 'test_ctrl')
        self.assertIn('accepted without modification', tr.reason)


class TestDeterminism(unittest.TestCase):
    """Same seed, same config -> byte-identical traces. This is what makes a
    trace reproducible evidence rather than a one-off log line."""

    def test_two_runs_produce_identical_traces(self):
        w, scen, fc = prep('normal')
        m0, _ = run_scenario(w['nb'], w['grid'], scen, fc, mode='coordinated', seed=1, want_trace=True)
        m1, _ = run_scenario(w['nb'], w['grid'], scen, fc, mode='coordinated', seed=1, want_trace=True)
        self.assertEqual(m0['traces'], m1['traces'])


class TestTraceDoesNotChangeControl(unittest.TestCase):
    def test_identical_metrics_with_and_without_trace(self):
        import numpy as np
        w, scen, fc = prep('normal')
        m0, A0 = run_scenario(w['nb'], w['grid'], scen, fc, mode='coordinated', seed=1, want_trace=False)
        m1, A1 = run_scenario(w['nb'], w['grid'], scen, fc, mode='coordinated', seed=1, want_trace=True)
        for key in A0:
            if key == 'solve_ms':
                continue   # wall-clock timing noise, not a computed result -- expected to differ
            self.assertTrue(np.allclose(A0[key], A1[key], equal_nan=True), key)


if __name__ == '__main__':
    unittest.main()
