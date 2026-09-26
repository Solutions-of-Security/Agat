import json
import subprocess
import unittest
from unittest.mock import patch

from decision_runtime.artifacts import sealed, verify_seal
from scripts.lib.decision_endurance import ProcessMemory, SCHEMA, endurance
from scripts.test.test_decision_baselines import dataset
from scripts.test.test_decision_performance import Fixture


class ClockFixture(Fixture):
    def __init__(self):
        super().__init__(); self.now = 0
    def decide(self, shadow):
        self.now += 1
        return super().decide(shadow)
    def clock(self): return self.now


def run(fixture, **kwargs):
    return endurance(dataset(), 'http://127.0.0.1:1', duration_s=3, window_s=1, warmup=0,
                     health_transport=fixture.health, client=fixture, clock=fixture.clock, **kwargs)


class EnduranceTest(unittest.TestCase):
    def test_duration_windows_signature_and_source_privacy(self):
        fixture = ClockFixture(); report = run(fixture)
        verify_seal(report, SCHEMA)
        self.assertEqual((report['status'], report['summary']['attempts']), ('observed', 3))
        self.assertEqual(len(report['windows']), 3)
        self.assertEqual(len(report['samples']), 5)
        self.assertEqual(len({r['decisionSha256'] for r in report['rows']}), 1)
        self.assertNotIn('Input only', json.dumps(report))
        self.assertNotIn('expectedOptionId', json.dumps(fixture.requests))
        self.assertFalse(report['qualifiedForRouting'])

    def test_request_cap_failure_and_profile_change_stop_without_retry(self):
        fixture = ClockFixture(); self.assertEqual(run(fixture, max_requests=1)['stoppedReason'], 'request_cap')
        fixture = ClockFixture(); fixture.busy = True
        report = run(fixture)
        self.assertEqual(report['stoppedReason'], 'request_failed'); self.assertEqual(fixture.calls, 2)
        self.assertEqual(report['summary']['scoredWallMs']['count'], 1)
        fixture = ClockFixture(); fixture.changed = True
        report = run(fixture)
        self.assertEqual(report['status'], 'degraded'); self.assertEqual(fixture.calls, 0)

    def test_invalid_result_changed_decision_and_process_loss_stop(self):
        fixture = ClockFixture(); fixture.invalid = True
        self.assertEqual(run(fixture)['summary']['failures'], {'invalid_response': 1})
        fixture = ClockFixture()
        original = fixture.decide
        def changing(shadow):
            if fixture.calls: fixture.engine.backend.logits = [0, 3]
            return original(shadow)
        fixture.decide = changing
        report = run(fixture)
        self.assertEqual(report['stoppedReason'], 'decision_changed')
        self.assertEqual(len(report['repeatDecisionChanges']), 1)
        fixture = ClockFixture()
        report = run(fixture, memory_sampler=lambda: {'available': False, 'processes': []})
        self.assertEqual(report['status'], 'degraded'); self.assertEqual(fixture.calls, 0)

    def test_non_development_and_excessive_limits_rejected_before_network(self):
        fixture = ClockFixture()
        data = dataset(); data.pop('sha256'); data['cases'][0]['split'] = 'holdout'
        with self.assertRaises(ValueError): endurance(sealed(data), 'http://127.0.0.1:1', health_transport=fixture.health)
        for config in ({'duration_s': 1801}, {'max_requests': 10001}, {'warmup': 21}, {'timeout_ms': 99}, {'window_s': 301}):
            with self.assertRaises(ValueError): endurance(dataset(), 'http://127.0.0.1:1', health_transport=fixture.health, **config)
        self.assertEqual(fixture.health_calls, 0)

    def test_process_memory_records_rss_separately_and_detects_pid_reuse(self):
        sampler = ProcessMemory([123])
        with patch('subprocess.check_output', side_effect=['123 1 1024 Sat Sep 26 12:00:00 2026',
                                                         '123 1 2048 Sat Sep 26 12:00:01 2026']):
            first, reused = sampler(), sampler()
        self.assertEqual(first['processes'][0]['rssBytes'], 1048576)
        self.assertFalse(reused['available'])
        with patch('subprocess.check_output', side_effect=subprocess.TimeoutExpired('ps', 2)):
            self.assertFalse(sampler()['available'])
        for pids in ([True], [0], [1, 1], [1, 2, 3, 4, 5]):
            with self.assertRaises(ValueError): ProcessMemory(pids)

    def test_failed_warmup_is_saved_and_prevents_measured_requests(self):
        fixture = ClockFixture(); fixture.invalid = True
        report = endurance(dataset(), 'http://127.0.0.1:1', duration_s=3, window_s=1, warmup=3,
                           health_transport=fixture.health, client=fixture, clock=fixture.clock)
        self.assertEqual(report['stoppedReason'], 'warmup_failed')
        self.assertEqual(len(report['warmup']), 1)
        self.assertEqual(report['summary']['attempts'], 0)
        self.assertEqual(fixture.calls, 1)


if __name__ == '__main__': unittest.main()
