import unittest
import numpy as np
from tests.common import *
from neos import service
from neos.runner import run_scenario
from neos.flexibility import compute_envelope, Envelope, BATT_SOC_MIN_RESERVE

CFG = dict(SMALL)
_CACHE = {}


def prep(regime='normal', rep=0):
    k = (regime, rep)
    if k not in _CACHE:
        _CACHE[k] = service.prepare(CFG, regime, rep)
    return _CACHE[k]


class TestEnvelopeContract(unittest.TestCase):
    """The roadmap's explicit rule: this is an ESTIMATE, never a guarantee."""

    def test_status_is_always_estimated(self):
        w, scen, fc = prep('normal')
        m, _ = run_scenario(w['nb'], w['grid'], scen, fc, mode='coordinated', seed=1,
                            want_envelope=True)
        self.assertTrue(len(m['envelopes']) > 0)
        for e in m['envelopes']:
            self.assertEqual(e['status'], 'ESTIMATED')

    def test_no_field_claims_to_be_a_guarantee(self):
        """Literal string check: nothing in the dict's keys or the note field
        may use the words 'guarantee' or 'guaranteed' as a claim about the
        envelope itself (the note is allowed to say what it is NOT)."""
        w, scen, fc = prep('normal')
        m, _ = run_scenario(w['nb'], w['grid'], scen, fc, mode='coordinated', seed=1,
                            want_envelope=True)
        e = m['envelopes'][10]
        for key in e:
            self.assertNotIn('guarantee', key.lower())
        self.assertIn('not a contractual delivery', e['note'])


class TestEnvelopeInvariants(unittest.TestCase):
    """Numeric sanity that would catch a double-counting or sign bug."""

    def setUp(self):
        self.nb = small_nb()

    def _obs(self, ev=None, batt=None, defer=None, hour=12.0):
        return dict(hour=hour, ev=ev or {}, batt=batt or {}, defer=defer or {})

    def _forecast(self, n=8, p10=90.0, p50=100.0, p90=120.0, pv10=0.0, pv50=0.0, pv90=0.0):
        import numpy as np
        return dict(inflex=dict(p10=np.full(n, p10), p50=np.full(n, p50), p90=np.full(n, p90)),
                   pv=dict(p10=np.full(n, pv10), p50=np.full(n, pv50), p90=np.full(n, pv90)))

    def test_safe_flexibility_never_exceeds_resource_sum(self):
        obs = self._obs(
            ev={0: dict(soc=0.3, cap=40.0, p_max=11.0, hard=False)},
            batt={0: dict(e=10.0, cap=20.0, p_max=7.0, eta=0.95, available=True)},
            defer={0: dict(rem=3.0, p_max=2.0)})
        pst = dict(tx_kw=50.0, tx_loading=20.0)
        fc = self._forecast()
        env = compute_envelope(0, 48, obs, pst, P_head=500.0, forecast=fc)
        resource_sum = env.ev_flexibility_kw + env.battery_flexibility_kw + env.load_flexibility_kw
        self.assertLessEqual(env.safe_flexibility_kw, resource_sum + 1e-9)
        self.assertGreaterEqual(env.safe_flexibility_kw, 0.0)

    def test_safe_flexibility_bounded_by_transformer_headroom(self):
        """Plenty of battery/EV/load resource, but almost no transformer
        headroom left -- safe flexibility must respect the GRID limit, not
        just the sum of device capability."""
        obs = self._obs(
            ev={0: dict(soc=0.1, cap=60.0, p_max=11.0, hard=False)},
            batt={0: dict(e=19.0, cap=20.0, p_max=10.0, eta=0.95, available=True)},
            defer={0: dict(rem=10.0, p_max=5.0)})
        pst = dict(tx_kw=498.0, tx_loading=99.6)             # almost fully loaded against a 500 kW headroom
        fc = self._forecast(p10=90, p50=100, p90=110)   # modest uncertainty
        env = compute_envelope(0, 48, obs, pst, P_head=500.0, forecast=fc)
        self.assertLess(env.safe_flexibility_kw, 10.0)   # only ~2 kW of real headroom left

    def test_hard_ev_requirement_excluded_from_flexibility(self):
        """An EV with a declared hard requirement is the LAST thing the
        safety layer sheds (control.IMPORT_ORDER) -- counting it as available
        flexibility would overstate what can actually be mobilised."""
        obs_soft = self._obs(ev={0: dict(soc=0.3, cap=40.0, p_max=11.0, hard=False)})
        obs_hard = self._obs(ev={0: dict(soc=0.3, cap=40.0, p_max=11.0, hard=True)})
        pst = dict(tx_kw=50.0, tx_loading=20.0)
        fc = self._forecast()
        e_soft = compute_envelope(0, 48, obs_soft, pst, P_head=500.0, forecast=fc)
        e_hard = compute_envelope(0, 48, obs_hard, pst, P_head=500.0, forecast=fc)
        self.assertGreater(e_soft.ev_flexibility_kw, 0.0)
        self.assertEqual(e_hard.ev_flexibility_kw, 0.0)

    def test_battery_flexibility_respects_reserve_floor_not_just_physical_floor(self):
        """A battery sitting exactly AT the reserve floor (15%) must report
        zero flexibility, even though the physical floor is lower (5%, per
        runner.BATT_PHYS_MIN) -- the envelope must match what the LP-MPC's
        OWN bounds actually protect, not a looser number."""
        cap = 20.0
        obs_at_floor = self._obs(batt={0: dict(e=BATT_SOC_MIN_RESERVE * cap, cap=cap,
                                               p_max=10.0, eta=0.95, available=True)})
        obs_above = self._obs(batt={0: dict(e=0.5 * cap, cap=cap, p_max=10.0,
                                            eta=0.95, available=True)})
        pst = dict(tx_kw=50.0, tx_loading=20.0)
        fc = self._forecast()
        e_floor = compute_envelope(0, 48, obs_at_floor, pst, P_head=500.0, forecast=fc)
        e_above = compute_envelope(0, 48, obs_above, pst, P_head=500.0, forecast=fc)
        self.assertAlmostEqual(e_floor.battery_flexibility_kw, 0.0, places=6)
        self.assertGreater(e_above.battery_flexibility_kw, 0.0)

    def test_unavailable_battery_contributes_nothing(self):
        obs = self._obs(batt={0: dict(e=15.0, cap=20.0, p_max=10.0, eta=0.95, available=False)})
        pst = dict(tx_kw=50.0, tx_loading=20.0)
        env = compute_envelope(0, 48, obs, pst, P_head=500.0, forecast=self._forecast())
        self.assertEqual(env.battery_flexibility_kw, 0.0)
        self.assertEqual(env.batteries_available, 0)

    def test_confidence_is_bounded_and_monotonic(self):
        """Tighter forecast uncertainty (relative to demand) must never
        report LOWER confidence than a wider one, and confidence must stay
        inside the documented [50, 97] band regardless of input."""
        obs = self._obs()
        pst = dict(tx_kw=50.0, tx_loading=20.0)
        tight = compute_envelope(0, 48, obs, pst, 500.0, self._forecast(p10=98, p50=100, p90=102))
        wide = compute_envelope(0, 48, obs, pst, 500.0, self._forecast(p10=20, p50=100, p90=180))
        extreme = compute_envelope(0, 48, obs, pst, 500.0, self._forecast(p10=-500, p50=100, p90=700))
        self.assertGreaterEqual(tight.confidence_pct, wide.confidence_pct)
        for e in (tight, wide, extreme):
            self.assertGreaterEqual(e.confidence_pct, 50.0)
            self.assertLessEqual(e.confidence_pct, 97.0)

    def test_duration_scales_with_replan_cadence(self):
        obs, pst, fc = self._obs(), dict(tx_kw=50.0, tx_loading=20.0), self._forecast()
        e1 = compute_envelope(0, 48, obs, pst, 500.0, fc, mpc_every=1)
        e4 = compute_envelope(0, 48, obs, pst, 500.0, fc, mpc_every=4)
        self.assertAlmostEqual(e4.duration_min, 4 * e1.duration_min, places=6)


class TestEnvelopeDoesNotChangeControl(unittest.TestCase):
    """want_envelope must be a pure side-channel: enabling it cannot change
    the numeric dispatch, safety, or metrics any existing caller depends on."""

    def test_identical_metrics_with_and_without_envelope(self):
        w, scen, fc = prep('normal')
        m0, A0 = run_scenario(w['nb'], w['grid'], scen, fc, mode='coordinated', seed=1,
                              want_envelope=False)
        m1, A1 = run_scenario(w['nb'], w['grid'], scen, fc, mode='coordinated', seed=1,
                              want_envelope=True)
        for key in ('peak_tx_loading', 'energy_cost_inr', 'ev_satisfaction'):
            self.assertAlmostEqual(m0[key], m1[key], places=9, msg=key)
        for key in A0:
            if key == 'solve_ms':
                continue   # wall-clock timing noise, not a computed result -- expected to differ
            self.assertTrue(np.allclose(A0[key], A1[key], equal_nan=True), key)


if __name__ == '__main__':
    unittest.main()
