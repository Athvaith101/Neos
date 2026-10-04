import unittest
from unittest import mock
import numpy as np
from tests.common import *
from neos import service
from neos.runner import run_scenario, WSTART, WLEN
from neos.controllers import Proposal, UncoordinatedController
from neos.control import Plan, EVEnvelope, EV_ETA
from neos.world import DT

CFG = dict(SMALL)
_CACHE = {}


def prep(regime='normal', rep=0):
    k = (regime, rep)
    if k not in _CACHE:
        _CACHE[k] = service.prepare(CFG, regime, rep)
    return _CACHE[k]


class TestEnergyBalance(unittest.TestCase):
    """P0: no clipping after the electrical solve; energy follows the verified dispatch."""

    def test_no_clipping_and_exact_energy_accounting(self):
        for mode in ('uncoordinated', 'rule_tou', 'coordinated'):
            w, scen, fc = prep('normal')
            m, L = run_scenario(w['nb'], w['grid'], scen, fc, mode=mode, seed=1)
            self.assertEqual(m['energy_clip_events'], 0, mode)
            self.assertLess(m['energy_audit']['ev_residual_kwh'], 1e-9, mode)
            self.assertLess(m['energy_audit']['battery_residual_kwh'], 1e-9, mode)
            self.assertTrue(all(0.0 <= s <= 1.0 + 1e-9 for s in m['ev_final_soc']))

    def test_impossible_requests_are_limited_before_injection(self):
        """A policy that asks for absurd power must be bounded by device physics first."""
        class Greedy(UncoordinatedController):
            name = 'greedy'; uses_safety_groups = True
            def act(self, obs):
                p = Proposal()
                for i in obs['ev']: p.ev[i] = 1e4
                for i in obs['batt']: p.bc[i] = 1e4; p.bd[i] = 1e4
                for i in obs['defer']: p.fl[i] = 1e4
                return p
        w, scen, fc = prep('normal')
        m, L = run_scenario(w['nb'], w['grid'], scen, fc, controller=Greedy(w['nb'], {}), seed=1)
        self.assertGreater(m['device_limited_kw_total'], 0)
        self.assertEqual(m['energy_clip_events'], 0)
        self.assertLess(m['energy_audit']['ev_residual_kwh'], 1e-9)
        self.assertTrue(all(s <= 1.0 + 1e-9 for s in m['ev_final_soc']))


class TestCurtailmentAndPlan(unittest.TestCase):
    def test_planned_curtailment_is_applied_and_accounted_separately(self):
        class Curt(UncoordinatedController):
            name = 'curt'; uses_safety_groups = True
            def act(self, obs):
                p = Proposal(); p.curt_kw = 25.0 if 8 <= obs['hour'] <= 14 else 0.0
                return p
        w, scen, fc = prep('normal')
        m, L = run_scenario(w['nb'], w['grid'], scen, fc, controller=Curt(w['nb'], {}), seed=1)
        self.assertGreater(m['curtailed_planned_kwh'], 10.0)

    def test_plan_is_indexed_not_repeated(self):
        p = Plan(status='optimal', H=10, k0=5)
        self.assertEqual([p.at(k) for k in (5, 6, 9, 14, 15, 4)], [0, 1, 4, 9, None, None])
        self.assertIsNone(Plan(status='infeasible', H=10, k0=0).at(0))

    def test_mpc_uses_multistep_plan(self):
        w, scen, fc = prep('normal')
        m1, _ = run_scenario(w['nb'], w['grid'], scen, fc, mode='coordinated', seed=1, mpc_every=1)
        m4, _ = run_scenario(w['nb'], w['grid'], scen, fc, mode='coordinated', seed=1, mpc_every=4)
        self.assertLess(m4['mpc']['n_solves'], m1['mpc']['n_solves'])
        self.assertEqual(m4['mpc']['fallback_steps'], 0)
        # indexing the plan keeps the result close to re-solving every step
        self.assertLess(abs(m4['peak_tx_loading'] - m1['peak_tx_loading']), 12.0)


class TestInformationBarrier(unittest.TestCase):
    def test_controller_never_sees_true_departure_or_future_weather(self):
        seen = []
        class Spy(UncoordinatedController):
            name = 'spy'; uses_safety_groups = True
            def act(self, obs):
                seen.append(obs); return Proposal()
        w, scen, fc = prep('normal')
        run_scenario(w['nb'], w['grid'], scen, fc, controller=Spy(w['nb'], {}), seed=1)
        allowed = {'soc', 'p_max', 'cap', 'priority', 'need_kwh', 'need_user_kwh', 'dl_p10_abs',
                   'dl_p90_abs', 'hard', 'ready_by'}
        for o in seen:
            for e in o['ev'].values():
                self.assertEqual(set(e), allowed)
            self.assertNotIn('weather', o)

    def test_changing_true_departure_does_not_change_earlier_decisions(self):
        w, scen, fc = prep('normal')
        nb = w['nb']
        eid = max(scen['ev'], key=lambda i: scen['ev'][i][0]['dep'] if scen['ev'][i][0]['override'] is None else -1)
        m0, L0 = run_scenario(nb, w['grid'], scen, fc, mode='coordinated', seed=1)
        import copy
        scen2 = dict(scen); scen2['ev'] = copy.deepcopy(scen['ev'])
        s = scen2['ev'][eid][0]; orig = s['dep']; s['dep'] = orig + 3
        m1, L1 = run_scenario(nb, w['grid'], scen2, fc, mode='coordinated', seed=1)
        cut = orig - WSTART - 1
        np.testing.assert_allclose(L0['net'][:cut], L1['net'][:cut], atol=1e-9)


class TestExplicitRequirements(unittest.TestCase):
    def _case(self, notice_off, depart_off):
        w, scen, fc = prep('normal')
        nb = w['nb']
        import copy
        scen2 = dict(scen); scen2['ev'] = copy.deepcopy(scen['ev'])
        for i in scen2['ev']:
            scen2['ev'][i][0]['override'] = None
        need = lambda i: (nb.evs[i].req_soc - scen2['ev'][i][0]['soc_arr']) * nb.evs[i].capacity_kwh
        eid = max(scen2['ev'], key=need)             # an EV that really needs energy
        self.assertGreater(need(eid), 3.0)
        arr = scen2['ev'][eid][0]['arr']
        ov = {eid: dict(notice=arr + notice_off, depart=arr + depart_off, kind='need_now')}
        m, L = run_scenario(nb, w['grid'], scen2, fc, mode='coordinated', seed=1, extra_overrides=ov)
        return m, eid

    def test_infeasible_request_is_reported_honestly_and_max_soc_exposed(self):
        m, eid = self._case(4, 5)                  # one step of notice
        led = [x for x in m['override_ledger'] if x['ev'] == eid][0]
        self.assertFalse(led['feasible'])                      # physics forbids the target
        self.assertEqual(led['status'], 'applied_infeasible')
        self.assertGreater(led['unavoidable_shortfall_kwh'], 0)
        self.assertEqual(m['override_met'], 0)                 # and we do not pretend otherwise
        self.assertLessEqual(m['override_avoidable_kwh'], 0.5)   # system took all deliverable energy

    def test_unannounced_disconnection_is_handled(self):
        m, eid = self._case(4, 4)                   # notice == departure
        self.assertEqual(m['override_sessions'], 1)
        led = [x for x in m['override_ledger'] if x['ev'] == eid][0]
        self.assertEqual(led['kwh_deliverable'], 0.0)          # nothing can be added after the fact
        self.assertLessEqual(m['override_avoidable_kwh'], 0.5)

    def test_ample_notice_meets_requirement(self):
        m, eid = self._case(6, 40)
        self.assertEqual(m['override_met'], 1)

    def test_electrical_protection_keeps_precedence(self):
        w, scen, fc = prep('normal')
        m, L = run_scenario(w['nb'], w['grid'], scen, fc, mode='coordinated', seed=1)
        self.assertEqual(m['constraint_status_steps']['solver_failed'], 0)
        self.assertGreaterEqual(m['steps_verified_pct'], 90.0)


class TestOverrideLedger(unittest.TestCase):
    def test_queued_until_a_run_consumes_it(self):
        led = service.OverrideLedger()
        e = led.submit('normal', 0, 'need_now', 1, 10, cfg=CFG)
        self.assertEqual(e['status'], 'queued')            # never claimed as applied
        self.assertEqual(len(led.pending('normal', 0, CFG)), 1)
        self.assertEqual(len(led.pending('cloud', 0, CFG)), 0)   # scoped
        with self.assertRaises(ValueError):
            led.submit('normal', 0, 'bogus', 1, 10, cfg=CFG)
        with self.assertRaises(ValueError):
            led.submit('normal', 0, 'need_now', 1, 9999, cfg=CFG)

    def test_run_consumes_ledger_and_records_outcome(self):
        old = service.LEDGER
        service.LEDGER = service.OverrideLedger()
        try:
            w, scen, fc = prep('normal')
            eid = min(scen['ev'], key=lambda i: scen['ev'][i][0]['arr'])
            arr_k = scen['ev'][eid][0]['arr'] - WSTART
            e = service.LEDGER.submit('normal', eid, 'depart_early', 12, arr_k + 4, cfg=CFG)
            m, s, f = service.run(CFG, 'normal', 'coordinated', use_ledger=True)
            self.assertIn(e['status'], ('applied', 'not_applied_ev_not_plugged'))
            self.assertIsNotNone(e['outcome'])
        finally:
            service.LEDGER = old


if __name__ == '__main__':
    unittest.main()
