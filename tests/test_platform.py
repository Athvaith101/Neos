"""
test_platform.py -- phase-aware transformer, DISCOM interface, rural kit / safe-island.

Pinned to the reference backend (see test_server.py for why OpenDSS contexts are not
mixed into the pytest process). OpenDSS behaviour of these same modules, and the
agreement of the two backends, is checked by validate_platform.py.
"""
import unittest, math
import numpy as np
from tests.common import *
from neos.grid import ReferenceGrid, phase_metrics, IMBALANCE_FLOOR
from neos.phase import phase_view, phase_status
from neos import discom, rural
from neos.discom import DiscomRequest, decide, ACCEPT, MODIFY, DEFER, REJECT


class TestPhase(unittest.TestCase):
    def setUp(self):
        self.nb = small_nb(); self.g = ReferenceGrid(self.nb, 200.0)

    def test_balanced_load_is_balanced(self):
        st = self.g.solve(spread(self.nb, 90))
        p = st['tx_phase_pct']
        self.assertLess(max(p) - min(p), 0.05 * np.mean(p))
        self.assertLess(st['imbalance'], 0.05)

    def test_single_phase_load_shows_on_that_phase_only(self):
        st = self.g.solve({(self.nb.nodes[0][0], 2): 60.0})
        self.assertAlmostEqual(st['tx_phase_pct'][0], 0.0, places=6)
        self.assertGreater(st['tx_phase_pct'][1], 85.0)
        self.assertAlmostEqual(st['imbalance'], 3.0, places=6)         # (max-min)/mean for one phase of three
        v = phase_view(st)
        self.assertEqual(v['worst_phase'], 'B')
        self.assertIn('Phase B', v['constraint'])

    def test_imbalance_undefined_near_zero_net_current(self):
        # spread/mean explodes when net current -> 0 (midday PV/load cancellation); below
        # the floor it is reported as 0, not as a huge meaningless number.
        pct, imb = phase_metrics([0.001, 0.0, 0.0002], 200.0)
        self.assertEqual(imb, 0.0)
        per = 200.0 / 3
        _, imb2 = phase_metrics([4 * IMBALANCE_FLOOR * per, 0.0, 0.0], 200.0)
        self.assertGreater(imb2, 0.0)

    def test_nan_state_is_never_reported_normal(self):
        self.assertEqual(phase_status(float('nan')), 'unknown')

    def test_phase_kva_carries_no_device_attribution(self):
        self.assertIn('cannot identify', phase_view(self.g.solve(spread(self.nb, 60)))['attribution'])

    def test_commercial_pv_is_three_phase(self):
        """Regression: commercial PV was once injected on ONE phase while the commercial load was
        spread over three, inflating phase imbalance in every urban run."""
        from neos import runner
        nb = small_nb(n_pv=6, n_bess=2)
        seen = []
        class Spy(ReferenceGrid):
            def solve(self, inj):
                seen.append(dict(inj)); return super().solve(inj)
        runner.export_limit(Spy(nb, 200.0), nb)
        inj = seen[-1]
        tot = sum(h.pv_kwp for h in nb.homes) + sum(c.pv_kwp for c in nb.commercials)
        exp = {}
        for h in nb.homes:
            if h.pv_kwp > 0:
                exp[(h.node, h.phase)] = exp.get((h.node, h.phase), 0.0) + h.pv_kwp / tot
        for c in nb.commercials:
            if c.pv_kwp > 0:
                for ph in (1, 2, 3):
                    exp[(c.node, ph)] = exp.get((c.node, ph), 0.0) + c.pv_kwp / tot / 3.0
        scale = -sum(inj.values()) / sum(exp.values())
        for k, w in exp.items():
            self.assertAlmostEqual(-inj[k], scale * w, places=6, msg=str(k))


class TestDiscomDecision(unittest.TestCase):
    def req(self, kw=60.0, **kw2): return DiscomRequest(44, 6, kw, **kw2)

    def test_accept(self):
        d = decide(self.req(60), [100.0] + [100.0] * 8)
        self.assertEqual((d.action, d.accepted_kw, d.start_step), (ACCEPT, 60.0, 44))

    def test_modify_promises_less_than_asked(self):
        d = decide(self.req(100), [60.0, 0, 0, 0, 0, 0, 0, 0, 0])
        self.assertEqual(d.action, MODIFY)
        self.assertLess(d.accepted_kw, 100.0)
        self.assertLessEqual(d.accepted_kw, 60.0 * discom.SAFETY_MARGIN)

    def test_defer_shifts_start(self):
        d = decide(self.req(60), [0, 0, 0, 80, 80, 80, 80, 80, 80])
        self.assertEqual((d.action, d.start_step), (DEFER, 47))

    def test_reject_when_nothing_deliverable(self):
        self.assertEqual(decide(self.req(60), [2.0] * 9).action, REJECT)

    def test_reject_unsupported_or_malformed(self):
        self.assertEqual(decide(self.req(60, direction='absorb'), [99.0] * 9).action, REJECT)
        self.assertEqual(decide(self.req(0), [99.0] * 9).action, REJECT)
        self.assertEqual(decide(self.req(60), None).action, REJECT)
        self.assertEqual(decide(self.req(60), [None] * 9).action, REJECT)

    def test_never_promises_more_than_asked_or_probed(self):
        for cap in (10, 40, 80, 500):
            d = decide(self.req(60), [float(cap)] * 9)
            self.assertLessEqual(d.accepted_kw, 60.0)
            self.assertLessEqual(d.accepted_kw, cap + 1e-9)


class TestDiscomEmulation(unittest.TestCase):
    CFG = dict(n_homes=80, n_ev=18, n_pv=40, n_bess=8, n_comm=3, tx_kva=250.0, backend='reference')

    @classmethod
    def setUpClass(cls):
        from neos import service
        from neos.config import NeighbourhoodConfig
        cls.service = service
        cls.cfg = NeighbourhoodConfig(**cls.CFG)

    def run_(self, cut=None, probe=None):
        m, s, _ = self.service.run(self.cfg, 'normal', 'coordinated', want_envelope=True,
                                   net_cut=cut, probe=probe, use_ledger=False)
        return m, s

    def test_zero_cut_equals_no_request(self):
        """The 'requests=[] silently ran the demo commands' class of bug: no request is a no-op."""
        _, a = self.run_(None); _, b = self.run_(np.zeros(96))
        self.assertEqual(a['net'], b['net'])

    def test_probe_is_read_only(self):
        _, a = self.run_(None)
        m, b = self.run_(None, dict(k=36, start=44, n=6, max_shift=2))
        self.assertEqual(a['net'], b['net'])
        self.assertEqual(len(m['cut_capability']), 3)

    def test_rejected_request_equals_baseline_by_construction(self):
        out = discom.emulate(self.run_, DiscomRequest(60, 6, 5000.0))
        self.assertEqual(out['decision']['action'], REJECT)
        self.assertEqual(out['actual'], out['baseline'])

    def test_request_run_stays_verified(self):
        cut = np.zeros(96); cut[44:50] = 15.0
        m, _ = self.run_(cut)
        self.assertEqual(m['constraint_status_steps']['solver_failed'], 0)
        self.assertGreaterEqual(m['steps_verified_pct'], 99.0)

    def test_net_cut_needs_the_coordinated_controller(self):
        with self.assertRaises(ValueError):
            self.service.run(self.cfg, 'normal', 'uncoordinated', net_cut=np.zeros(96), use_ledger=False)


class TestRural(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.R = {(sc, arm): rural.run_rural(sc, arm, backend='reference')
                 for sc in rural.SCENARIOS for arm in ('naive', 'neos')}

    def test_energy_audit_is_exact_and_nothing_is_clipped(self):
        for k, (m, _) in self.R.items():
            self.assertLess(m['energy_audit']['battery_residual_kwh'], 1e-8, k)
            self.assertEqual(m['energy_audit']['clip_events'], 0, k)
            s = self.R[k][1]
            self.assertLessEqual(max(s['soc']), 1.0 + 1e-9, k)
            self.assertGreaterEqual(min(s['soc']), rural.SOC_MIN - 1e-9, k)

    def test_every_island_step_is_electrically_checked(self):
        for k, (m, _) in self.R.items():
            n = sum(m['constraint_status_island'].values())
            self.assertEqual(n, m['outage_steps'], k)
            self.assertGreaterEqual(m['island_steps_verified_pct'] or 100.0, 99.0, k)

    def test_grid_up_has_no_island_steps(self):
        for arm in ('naive', 'neos'):
            m, _ = self.R[('normal', arm)]
            self.assertEqual(m['outage_steps'], 0)
            self.assertEqual(m['clinic_unserved_kwh'], 0.0)

    def test_priority_tiers_protect_the_clinic_where_tier_blind_sharing_fails(self):
        n, _ = self.R[('cloudy_outage', 'naive')]; o, _ = self.R[('cloudy_outage', 'neos')]
        self.assertGreater(n['clinic_unserved_kwh'], 1.0)                 # tier-blind loses the clinic
        self.assertEqual(o['clinic_unserved_kwh'], 0.0)
        self.assertGreater(o['clinic_hours_full_power_in_outage'], n['clinic_hours_full_power_in_outage'])
        self.assertLess(o['cold_chain_hours_above_8C'], n['cold_chain_hours_above_8C'])

    def test_the_cost_of_protection_is_disclosed_not_hidden(self):
        # protecting tiers 1-2 on a poor-sun day means shedding tier 3 (shops): the trade-off is real
        n, _ = self.R[('cloudy_outage', 'naive')]; o, _ = self.R[('cloudy_outage', 'neos')]
        self.assertGreater(o['shops_unserved_kwh'], n['shops_unserved_kwh'])

    def test_naive_sharing_is_proportional(self):
        _, s = self.R[('cloudy_outage', 'naive')]
        tf = s['tier_frac']
        for k in range(rural.WLEN):
            if s['mode'][k] > 0.5:
                vals = [tf[t][k] for t in ('1', '2', '3', '4')]
                shed = [v for v in vals if v < 0.999]                  # tiers with no demand read 1.0
                if shed:
                    self.assertLess(abs(vals[0] - min(vals)), 1e-6, k)  # the clinic is cut as deeply ...
                    self.assertLess(max(shed) - min(shed), 1e-6, k)     # ... as every other tier

    def test_pv_is_never_invented(self):
        for k, (m, s) in self.R.items():
            for u, a in zip(s['pv_used'], s['pv']):
                self.assertLessEqual(u, a + 1e-9, k)
            self.assertGreaterEqual(m['pv_curtailed_kwh'], -1e-9, k)

    def test_charter_parameter_changes_behaviour(self):
        a, _ = rural.run_rural('cloudy_outage', 'neos', backend='reference', t1_endurance_h=2.0)
        b, _ = self.R[('cloudy_outage', 'neos')]
        self.assertNotEqual((round(a['shops_unserved_kwh'], 3), round(a['min_soc'], 3)),
                            (round(b['shops_unserved_kwh'], 3), round(b['min_soc'], 3)))

    def test_deterministic(self):
        a, _ = rural.run_rural('evening_outage', 'neos', backend='reference')
        b, _ = self.R[('evening_outage', 'neos')]
        self.assertEqual(a['final_soc'], b['final_soc'])


if __name__ == '__main__':
    unittest.main()


class TestEditableVillage(unittest.TestCase):
    def test_default_override_identical_and_restored(self):
        from neos import rural
        a, _ = rural.run_rural('evening_outage', 'neos', seed=1, backend='reference')
        with rural.village():
            b, _ = rural.run_rural('evening_outage', 'neos', seed=1, backend='reference')
        self.assertEqual(a['shops_unserved_kwh'], b['shops_unserved_kwh'])
        self.assertEqual((rural.N_HOMES, rural.PV_KWP, rural.BATT_KWH, rural.TX_KVA), (90, 45.0, 120.0, 100.0))

    def test_bigger_battery_never_worse_for_clinic(self):
        from neos import rural
        out = []
        for kwh, kw in ((60, 20), (120, 40), (300, 80)):
            with rural.village(battery_kwh=kwh, battery_kw=kw):
                m, _ = rural.run_rural('cloudy_outage', 'neos', seed=0, backend='reference')
            out.append(m['clinic_unserved_kwh'])
            self.assertEqual(m['energy_audit']['clip_events'], 0)
        self.assertTrue(out[0] >= out[1] >= out[2], out)

    def test_restored_after_exception_and_bounds(self):
        from neos import rural
        with self.assertRaises(ValueError):
            with rural.village(homes=5):
                pass
        with self.assertRaises(RuntimeError):
            with rural.village(homes=120):
                raise RuntimeError
        self.assertEqual(rural.N_HOMES, 90)

    def test_transformer_scales_with_homes_and_inverter_with_battery(self):
        from neos import rural
        with rural.village(homes=200, battery_kw=80):
            self.assertEqual(rural.TX_KVA, 225.0); self.assertEqual(rural.INVERTER_KVA, 100.0)
        with rural.village(homes=90):
            self.assertEqual(rural.TX_KVA, 100.0)


class TestHub(unittest.TestCase):
    def setUp(self):
        from neos.hub import Hub
        self.hub = Hub(['a', 'b', 'c'], now=10)

    def test_offline_queue_then_sync_applies_in_order(self):
        d = self.hub.devices['a']; d.online = False
        d.submit(self.hub, 'pledge', 20, 30, 2.0)
        d.submit(self.hub, 'pledge', 20, 30, 3.0)         # later pledge supersedes
        self.assertIsNone(self.hub.h['a'].pledge)         # nothing confirmed while offline
        self.assertEqual(self.hub.committed_kw(20, 30)['raw_kw'], 0.0)
        d.online = True; evs = d.sync(self.hub)
        self.assertEqual([e.outcome for e in evs], ['applied', 'applied'])
        self.assertEqual(self.hub.h['a'].pledge['kw'], 3.0)

    def test_out_of_order_arrival_last_sequence_wins_and_is_reported(self):
        from neos.hub import Event
        self.hub.receive([Event('b', 2, 'pledge', 20, 30, 4.0)])
        self.hub.receive([Event('b', 1, 'pledge', 20, 30, 1.0)])
        self.assertEqual(self.hub.h['b'].pledge['kw'], 4.0)
        self.assertEqual(self.hub.log[-1].outcome, 'duplicate')

    def test_resend_is_idempotent(self):
        from neos.hub import Event
        e = Event('c', 1, 'pledge', 20, 30, 2.0)
        self.hub.receive([e]); e2 = Event('c', 1, 'pledge', 20, 30, 2.0); self.hub.receive([e2])
        self.assertEqual(e2.outcome, 'duplicate')

    def test_expired_and_invalid_are_explicit_not_silent(self):
        d = self.hub.devices['a']; d.online = False
        d.submit(self.hub, 'pledge', 2, 8, 1.0)           # over before sync
        d.submit(self.hub, 'pledge', 20, 30, 99.0)        # absurd
        d.online = True; evs = d.sync(self.hub)
        self.assertEqual([e.outcome for e in evs], ['expired', 'rejected'])
        self.assertIsNone(self.hub.h['a'].pledge)

    def test_sms_path_and_errors(self):
        r, ev = self.hub.sms('a', 'pledge 1.5 20 30')
        self.assertTrue(r.startswith('APPLIED')); self.assertEqual(self.hub.h['a'].pledge['kw'], 1.5)
        self.assertTrue(self.hub.sms('a', 'hello')[0].startswith('ERR'))
        self.assertTrue(self.hub.sms('zz', 'WITHDRAW')[0].startswith('ERR'))
        self.assertEqual(self.hub.sms('a', 'WITHDRAW')[1].outcome, 'applied')
        self.assertIsNone(self.hub.h['a'].pledge)

    def test_reliability_discounts_commitment_and_points(self):
        self.hub.sms('a', 'PLEDGE 2 20 30'); self.hub.sms('b', 'PLEDGE 2 20 30')
        c0 = self.hub.committed_kw(20, 30)
        self.assertEqual(c0['raw_kw'], 4.0); self.assertAlmostEqual(c0['expected_kw'], 2.0)   # prior 0.5
        self.hub.settle(20, 30, {'a': 2.0, 'b': 0.0})
        self.assertAlmostEqual(self.hub.reliability('a'), 1.0); self.assertAlmostEqual(self.hub.reliability('b'), 0.0)
        self.assertAlmostEqual(self.hub.h['a'].points, 5.0)
        self.assertAlmostEqual(self.hub.committed_kw(20, 30)['expected_kw'], 2.0)

    def test_over_delivery_is_not_credited(self):
        self.hub.sms('a', 'PLEDGE 1 20 24'); r = self.hub.settle(20, 24, {'a': 9.0})
        self.assertAlmostEqual(r['a']['delivered_kwh'], 1.0)


class TestHubPersistence(unittest.TestCase):
    def test_roundtrip_preserves_queue_pledges_and_history(self):
        import json
        from neos.hub import Hub
        h = Hub(['a', 'b'], now=5)
        h.sms('a', 'PLEDGE 2 20 30'); h.devices['b'].online = False
        h.devices['b'].submit(h, 'pledge', 20, 30, 1.0); h.settle(20, 30, {'a': 1.0})
        h2 = Hub.from_dict(json.loads(json.dumps(h.to_dict())))
        self.assertEqual(h2.snapshot(), h.snapshot())
        self.assertEqual(len(h2.devices['b'].queue), 1)
        h2.devices['b'].online = True; h2.devices['b'].sync(h2)
        self.assertEqual(h2.h['b'].pledge['kw'], 1.0)
        self.assertEqual(h2.devices['b'].seq, 1)
