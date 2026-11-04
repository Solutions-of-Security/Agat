import copy
import importlib.util
from pathlib import Path
import threading
import time
import unittest

from decision_runtime.contracts import Request
from decision_runtime.engine import DecisionEngine, Scores
from decision_runtime.tests.test_decisions import Backend, request
from scripts.lib.decision_arrival_rate import drive_arrivals, run_phase, summarize, validate_schedule
from workers.local_decisions import CALLER_TIMING_VERSION

spec = importlib.util.spec_from_file_location('arrival_verifier', Path(__file__).resolve().parents[1] / 'verify-decision-arrival-rate.py')
verifier = importlib.util.module_from_spec(spec); spec.loader.exec_module(verifier)


class Clock:
    def __init__(self): self.now = 50.0
    def clock(self): return self.now
    def sleep(self, duration): self.now += duration


class TokensBackend(Backend):
    def __init__(self):
        super().__init__()
        self.identity = {**Backend.identity, 'maxInputTokens': 2048}
    def score(self, raw):
        return Scores(self.logits, 256)


class Client:
    def __init__(self, delay=0):
        self.engine = DecisionEngine(TokensBackend()); self.delay = delay; self.calls = []
        self.active = 0; self.max_active = 0; self.lock = threading.Lock(); self.mutate = lambda row: None
    def decide(self, shadow):
        began = time.monotonic()
        with self.lock:
            self.calls.append(copy.deepcopy(shadow)); self.active += 1; self.max_active = max(self.max_active, self.active)
        time.sleep(self.delay)
        result = self.engine.decide(shadow['request'])
        with self.lock: self.active -= 1
        row = {'result': result, 'callerTiming': {'schemaVersion': CALLER_TIMING_VERSION,
               'clock': 'monotonic', 'boundary': 'local_http_call',
               'durationMs': round((time.monotonic() - began) * 1000, 3)}}
        self.mutate(row)
        return row


def case():
    raw = request(); parsed = Request.from_dict(raw)
    return {'request': raw, 'inputSha256': parsed.input_sha256, 'targetTokens': 256}


class ArrivalRateTest(unittest.TestCase):
    def test_fixed_offsets_do_not_wait_for_previous_completion(self):
        clock = Clock(); rows = []
        drive_arrivals(4, 5, 1, 100, lambda *row: rows.append(row), clock=clock.clock, sleep=clock.sleep)
        self.assertEqual([r[1] for r in rows], [0, .25, .5, .75, 1])
        self.assertTrue(all(r[3] is None and abs(r[2] - r[1]) < 1e-9 for r in rows))

    def test_late_arrivals_are_counted_and_not_sent_in_catch_up_burst(self):
        clock = Clock(); rows = []
        def dispatch(*row):
            rows.append(row)
            if row[0] == 0: clock.now += .65
        drive_arrivals(4, 4, 1, 100, dispatch, clock=clock.clock, sleep=clock.sleep)
        self.assertEqual([r[3] for r in rows], [None, 'scheduler_lag', 'scheduler_lag', None])
        self.assertEqual([r[1] for r in rows], [0, .25, .5, .75])

    def test_cancelled_schedule_preserves_remaining_denominator(self):
        clock = Clock(); rows = []
        drive_arrivals(1, 3, 1, 100, lambda *r: rows.append(r), clock=clock.clock,
                       sleep=clock.sleep, cancelled=lambda: True)
        self.assertEqual([r[3] for r in rows], ['cancelled'] * 3)

    def test_excessive_invalid_schedule_has_no_effects(self):
        for args in ((True, 1, 1, 100), (float('nan'), 1, 1, 100), (4, 241, 1, 100),
                     (1, 1, 9, 100), (1, 1, 1, False), (.1, 13, 1, 100)):
            with self.subTest(args=args), self.assertRaises(ValueError): validate_schedule(*args)

    def phase(self, client=None, count=1, slots=1):
        client = client or Client()
        phase = run_phase(client, case(), client.engine.profile(), rate=4, count=count, slots=slots)
        schedule = {'caseIndex': 0, 'ratePerSecond': 4, 'count': count, 'clientSlots': slots, 'maxSchedulerLagMs': 100}
        plan = {'profile': client.engine.profile(), 'callerTimeoutMs': 10000, 'thresholdMs': 5000}
        return client, phase, schedule, plan

    def test_one_busy_client_drops_arrivals_without_hiding_them_in_ratio(self):
        client, phase, schedule, plan = self.phase(Client(.6), count=3)
        self.assertEqual(client.max_active, 1)
        self.assertEqual((phase['summary']['scheduled'], phase['summary']['admitted'], phase['summary']['scored']), (3, 1, 1))
        self.assertEqual(phase['summary']['dropped'], {'client_capacity': 2})
        self.assertEqual(phase['summary']['goodPerScheduled'], 1 / 3)
        self.assertEqual(phase['summary']['goodPerAdmitted'], 1)
        verifier.verify_phase(phase, schedule, case(), plan)

    def test_negotiates_actual_timing_and_binds_worker_descriptor(self):
        client, phase, schedule, plan = self.phase()
        sent = client.calls[0]
        self.assertEqual(sent['callerTimingVersion'], CALLER_TIMING_VERSION)
        self.assertEqual(sent['request'], Request.from_dict(case()['request']).to_dict())
        self.assertNotIn('expectedOptionId', sent)
        self.assertEqual(verifier.verify_phase(phase, schedule, case(), plan), phase['summary'])

    def test_unavailable_is_failure_and_retains_measured_timing(self):
        client = Client()
        client.mutate = lambda row: (row.pop('result'), row.update(status='unavailable', reason='busy'))
        _, phase, schedule, plan = self.phase(client)
        self.assertEqual(phase['summary']['failures'], {'busy': 1})
        self.assertEqual(phase['summary']['goodPerScheduled'], 0)
        self.assertEqual(phase['summary']['callerMsAllMeasured']['count'], 1)
        verifier.verify_phase(phase, schedule, case(), plan)

    def test_bad_probability_missing_timing_or_wrong_tokens_fail_measurement(self):
        for mutate in (lambda r: r.pop('callerTiming'), lambda r: r['result'].update(inputTokens=2048),
                       lambda r: r['result']['distribution'][0].update(probability=.1)):
            client = Client(); client.mutate = mutate
            _, phase, schedule, plan = self.phase(client)
            self.assertEqual(phase['rows'][0]['status'], 'measurement_error')
            self.assertEqual(phase['summary']['goodPerScheduled'], 0)
            with self.assertRaises(ValueError): verifier.verify_phase(phase, schedule, case(), plan)

    def test_independent_verifier_rejects_missing_arrivals_shifted_schedule_and_false_ratio(self):
        _, phase, schedule, plan = self.phase()
        for mutate in (lambda p: p['rows'].clear(), lambda p: p['rows'][0].update(scheduledMs=10),
                       lambda p: p['summary'].update(goodPerScheduled=.5),
                       lambda p: p['rows'][0].update(callerMs=0),
                       lambda p: p['rows'][0].update(finishedMs=-1)):
            changed = copy.deepcopy(phase); mutate(changed)
            with self.subTest(mutate=mutate), self.assertRaises((ValueError, KeyError)):
                verifier.verify_phase(changed, schedule, case(), plan)

    def test_drops_never_gain_http_latency_and_all_dropped_has_no_admitted_ratio(self):
        rows = [{'index': 0, 'scheduledMs': 0, 'dispatchMs': 200, 'status': 'dropped', 'reason': 'scheduler_lag'}]
        summary = summarize(rows, 5000)
        self.assertEqual(summary['goodPerScheduled'], 0)
        self.assertIsNone(summary['goodPerAdmitted'])
        self.assertEqual(summary['callerMsAllMeasured']['count'], 0)

    def test_client_exception_or_malformed_result_keeps_scheduled_failure(self):
        class RaisingClient(Client):
            def decide(self, shadow): raise OSError('fixture')
        for client in (RaisingClient(), Client()):
            client.mutate = lambda row: row.update(result='malformed')
            _, phase, _, _ = self.phase(client)
            self.assertEqual(phase['rows'][0]['status'], 'measurement_error')
            self.assertEqual(phase['summary']['scheduled'], 1)
            self.assertEqual(phase['summary']['goodPerScheduled'], 0)

    def test_two_client_slots_allow_actual_overlap_and_still_bound_it(self):
        client, phase, schedule, plan = self.phase(Client(.6), count=2, slots=2)
        self.assertEqual(client.max_active, 2)
        self.assertEqual(phase['summary']['admitted'], 2)
        self.assertGreater(phase['rows'][0]['finishedMs'], phase['rows'][1]['startedMs'])
        verifier.verify_phase(phase, schedule, case(), plan)


if __name__ == '__main__': unittest.main()
