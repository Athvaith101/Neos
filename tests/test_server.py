"""
test_server.py -- the first real test file for server.py.

AUDIT-STATUS.md flagged server.py as "compiles, but no endpoint has been
exercised" and later as "exercised via FastAPI's TestClient in-process ...
but a real curl round-trip was not completed" -- both true statements about
AD HOC manual checks run during development, not about a committed,
re-runnable test. This file is that test: it uses FastAPI's TestClient
in-process (no real socket needed, and none was reliably available in the
sandbox these were developed in -- see AUDIT-STATUS.md), but it runs every
time the suite runs, which an interactive curl session does not.

Kept deliberately small: these are the two NEW endpoints (V3 roadmap) plus
one baseline. Covering the entire existing API is future work, noted in
AUDIT-STATUS.md rather than silently left undone.
"""
import unittest
from fastapi.testclient import TestClient
from server import app

SMALL_Q = dict(n_homes=60, n_ev=14, n_pv=30, n_bess=6, n_comm=3, tx_kva=200.0, backend='reference')
# Pinned to the reference backend deliberately: this file tests API routing,
# validation and serialisation, which is backend-agnostic by design (the
# service layer dispatches to whichever backend the config names). OpenDSS
# physics correctness is test_grid.py's and validate_backends.py's job, not
# this file's. Pinning here is ALSO what keeps the full suite stable: running
# test_grid.py's several independent OpenDSS contexts and this file's own
# get_world() calls in the SAME pytest process reproducibly segfaulted
# (native code, not a Python exception) in this environment -- confirmed to
# require BOTH files together (neither alone, nor the equivalent sequence of
# calls run as a plain script outside pytest, reproduces it). That points to
# a GC-timing interaction between pytest's test lifecycle and OpenDSS's
# native context cleanup, not a defect in OpenDSSGrid's isolation logic
# itself (separately proven correct in test_grid.py and validate_backends.py
# on real OpenDSS). See AUDIT-STATUS.md for the full writeup.


class TestServerBaseline(unittest.TestCase):
    def setUp(self):
        self.c = TestClient(app)

    def test_meta_responds_and_reports_the_requested_backend(self):
        r = self.c.get('/api/meta', params=SMALL_Q)
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d['grid_backend'], 'reference')   # pinned, see SMALL_Q comment
        self.assertEqual(d['homes'], 60)

    # NOTE: deliberately NOT testing backend='auto' resolving to OpenDSS
    # through this HTTP layer in the committed suite -- doing so inside the
    # same pytest process as test_grid.py is exactly the combination that
    # segfaults (see SMALL_Q comment above). That specific path (API ->
    # service.get_world -> OpenDSSGrid, end to end) WAS verified manually
    # during development (see AUDIT-STATUS.md, "OpenDSS verification"
    # section) and passed; it is not re-asserted here as an automated test
    # to avoid reintroducing the instability. This is a real, acknowledged
    # gap, not a hidden one.


class TestFlexibilityEndpoint(unittest.TestCase):
    def setUp(self):
        self.c = TestClient(app)

    def test_returns_one_envelope_per_control_step_all_estimated(self):
        r = self.c.get('/api/flexibility', params=dict(scenario='normal', mode='coordinated', **SMALL_Q))
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertGreater(len(d['envelopes']), 0)
        self.assertTrue(all(e['status'] == 'ESTIMATED' for e in d['envelopes']))
        self.assertEqual(d['latest'], d['envelopes'][-1])

    def test_unknown_scenario_is_a_client_error_not_a_500(self):
        r = self.c.get('/api/flexibility', params=dict(scenario='not_a_real_scenario', **SMALL_Q))
        self.assertEqual(r.status_code, 400)

    def test_unknown_mode_is_a_client_error_not_a_500(self):
        r = self.c.get('/api/flexibility', params=dict(scenario='normal', mode='not_a_real_mode', **SMALL_Q))
        self.assertEqual(r.status_code, 400)


class TestDecisionTraceEndpoint(unittest.TestCase):
    def setUp(self):
        self.c = TestClient(app)

    def test_returns_one_trace_per_control_step_with_required_statuses_only(self):
        r = self.c.get('/api/decision_trace', params=dict(scenario='heat', mode='coordinated', **SMALL_Q))
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d['count'], len(d['traces']))
        seen = {t['safety_status'] for t in d['traces']}
        self.assertTrue(seen.issubset({'VERIFIED', 'INFEASIBLE', 'SOLVER_FAILED'}))

    def test_status_filter_only_returns_matching_records(self):
        r = self.c.get('/api/decision_trace',
                       params=dict(scenario='heat', mode='coordinated', status='VERIFIED', **SMALL_Q))
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertTrue(all(t['safety_status'] == 'VERIFIED' for t in d['traces']))

    def test_invalid_status_filter_is_rejected(self):
        r = self.c.get('/api/decision_trace',
                       params=dict(scenario='heat', mode='coordinated', status='bogus', **SMALL_Q))
        self.assertEqual(r.status_code, 400)


class TestStreamEndpoint(unittest.TestCase):
    def test_stream_emits_progress_and_completion(self):
        c = TestClient(app)
        r = c.get('/api/stream', params=dict(scenario='normal', mode='uncoordinated',
                                             delay=0, **SMALL_Q))
        self.assertEqual(r.status_code, 200)
        self.assertIn('text/event-stream', r.headers.get('content-type', ''))
        self.assertIn('"done": true', r.text)
        self.assertNotIn('"error"', r.text)


if __name__ == '__main__':
    unittest.main()
