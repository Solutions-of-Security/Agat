import copy
import json
import tempfile
import unittest
from pathlib import Path

from decision_runtime.artifacts import read_json, sealed, verify_seal, write_new
from decision_runtime.engine import DecisionEngine
from decision_runtime.tests.test_decisions import Backend
from scripts.lib.decision_shared_load import benchmark_shared, summarize
from scripts.lib.decision_shared_soak import SCHEMA, shared_soak
from scripts.test.test_decision_baselines import dataset
from scripts.test.test_decision_performance import Fixture
from scripts.test.test_decision_shared_load import Primary as FixturePrimary


class SyntheticClock:
    def __init__(self): self.now = 0
    def __call__(self): return self.now


class TimedBlocks:
    """Explicit fixture blocks: 1500 ms measured plus four 1-ms warmup calls."""
    def __init__(self, clock, alter=None):
        self.clock = clock; self.alter = alter; self.calls = 0

    def __call__(self, data, url, primary_factory, **kwargs):
        self.calls += 1
        fixture = Fixture(); primary = FixturePrimary()
        if self.alter: self.alter(self.calls, fixture, primary)
        report = benchmark_shared(data, url, lambda: primary, rounds=2, warmup=2,
                                  client=fixture, health_transport=fixture.health,
                                  expected_profile=kwargs['expected_profile'])
        body = {k: copy.deepcopy(v) for k, v in report.items() if k != 'sha256'}
        elapsed = 0
        for row in body['warmup']:
            row.update(startedMs=elapsed, finishedMs=elapsed + 1, wallMs=1); elapsed += 1
        for phase in body['phases']:
            paired = phase['name'] in ('sequential_pair', 'overlapping_pair')
            groups = [phase['rows'][i:i + (2 if paired else 1)]
                      for i in range(0, len(phase['rows']), 2 if paired else 1)]
            phase['pairs'] = []
            for rows in groups:
                begin = elapsed
                if phase['name'] == 'overlapping_pair':
                    rows[0].update(startedMs=begin, finishedMs=begin + 150, wallMs=150)
                    rows[1].update(startedMs=begin, finishedMs=begin + 100, wallMs=100)
                    elapsed += 150
                else:
                    for row in rows:
                        row.update(startedMs=elapsed, finishedMs=elapsed + 100, wallMs=100); elapsed += 100
                if paired:
                    phase['pairs'].append({'caseId': rows[0]['caseId'], 'elapsedMs': elapsed - begin,
                                           'requestOverlapMs': 100 if phase['name'] == 'overlapping_pair' else 0})
            phase['summary'] = {kind: summarize([r for r in phase['rows'] if r['kind'] == kind])
                                for kind in ('decision', 'primary')}
        body['elapsedMs'] = elapsed
        body['plan'].update(stopOnFailure=True, pairObservation=True,
                            cooperativeCancellation=True, expectedProfilePinned=True)
        observer = kwargs['pair_observer']
        for index in (0, 2): observer('warmup', index // 2 + 1, body['warmup'][index:index + 2])
        for phase in body['phases']:
            step = 2 if phase['pairs'] else 1
            for index in range(0, len(phase['rows']), step):
                observer(phase['name'], index // step + 1, phase['rows'][index:index + step])
        self.clock.now += elapsed / 1000
        return sealed(body)


class SharedSoakTest(unittest.TestCase):
    def run_probe(self, directory, *, duration=3, cap=4, alter=None, **kwargs):
        clock = SyntheticClock(); probe = TimedBlocks(clock, alter)
        result = shared_soak(dataset(), 'http://127.0.0.1:1', lambda: None, directory,
                             Fixture().engine.profile(), duration_s=duration, max_blocks=cap,
                             probe=probe, clock=clock, **kwargs)
        verify_seal(result, SCHEMA)
        return result, probe

    def test_duration_excludes_warmup_and_journal_matches_saved_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            seen = []; result, probe = self.run_probe(Path(tmp), journal=seen.append)
            self.assertEqual((result['status'], result['stoppedReason']), ('observed', 'duration_complete'))
            self.assertEqual((probe.calls, result['measuredMs'], result['separateWarmupCalls'], result['measuredAttempts']), (2, 3000, 8, 32))
            self.assertEqual(result['wallMs'], 3008)
            for index, entry in enumerate(result['blocks']):
                report = read_json(Path(tmp) / entry['path'])
                self.assertEqual(report['sha256'], entry['sha256'])
                self.assertEqual([r for event in seen if event['block'] == index for r in event['rows']],
                                 report['warmup'] + [r for p in report['phases'] for r in p['rows']])
                self.assertEqual((Path(tmp) / entry['path']).stat().st_mode & 0o777, 0o600)

    def test_block_cap_cannot_become_success_by_lowering_duration_afterward(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, probe = self.run_probe(Path(tmp), duration=10, cap=1)
            self.assertEqual((result['status'], result['stoppedReason'], probe.calls), ('limited', 'block_cap', 1))
            self.assertEqual(result['plan']['durationSeconds'], 10)

    def test_changed_decision_retains_second_block_and_starts_no_third(self):
        def alter(index, fixture, _primary):
            if index == 2: fixture.engine = DecisionEngine(Backend([0, 3]))
        with tempfile.TemporaryDirectory() as tmp:
            result, probe = self.run_probe(Path(tmp), duration=10, alter=alter)
            self.assertEqual((probe.calls, len(result['blocks'])), (2, 2))
            self.assertEqual(result['stoppedReason'], 'decision_changed_across_blocks')
            self.assertTrue(result['crossBlockDecisionChanges'])

    def test_primary_identity_change_is_failure_even_if_individual_blocks_are_stable(self):
        def alter(index, _fixture, primary):
            if index == 2: primary.identity['digest'] = '2' * 64
        with tempfile.TemporaryDirectory() as tmp:
            result, probe = self.run_probe(Path(tmp), duration=10, alter=alter)
            self.assertEqual(probe.calls, 2); self.assertEqual(result['stoppedReason'], 'primary_changed_or_unloaded')

    def test_second_checkpoint_io_failure_keeps_first_and_never_calls_third_probe(self):
        writes = []
        def writer(path, value):
            writes.append(path)
            if len(writes) == 2: raise OSError('PRIVATE ENOSPC detail')
            write_new(path, value)
        with tempfile.TemporaryDirectory() as tmp:
            result, probe = self.run_probe(Path(tmp), duration=10, writer=writer)
            self.assertEqual((probe.calls, len(result['blocks'])), (2, 1))
            self.assertEqual(result['stoppedReason'], 'evidence_write_failed')
            self.assertTrue((Path(tmp) / 'block-001.json').exists())
            self.assertFalse((Path(tmp) / 'block-002.json').exists())
            self.assertNotIn('PRIVATE', json.dumps(result))

    def test_progress_failure_retains_checkpoint_and_stops(self):
        def failed_progress(*_): raise RuntimeError('SECRET')
        with tempfile.TemporaryDirectory() as tmp:
            result, probe = self.run_probe(Path(tmp), duration=10, progress=failed_progress)
            self.assertEqual((probe.calls, len(result['blocks'])), (1, 1))
            self.assertEqual(result['stoppedReason'], 'observation_failed')
            self.assertNotIn('SECRET', json.dumps(result))

    def test_cancel_before_first_probe_and_cancel_observer_failure_do_not_make_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, probe = self.run_probe(Path(tmp), cancel_requested=lambda: True)
            self.assertEqual((probe.calls, result['blocks'], result['stoppedReason']), (0, [], 'cancelled'))
            def broken(): raise ValueError('SECRET')
            result, probe = self.run_probe(Path(tmp), cancel_requested=broken)
            self.assertEqual((probe.calls, result['stoppedReason']), (0, 'observation_failed'))

    def test_probe_exception_retains_prior_checkpoints_and_is_not_a_success(self):
        clock = SyntheticClock(); delegate = TimedBlocks(clock); count = []
        def probe(*args, **kwargs):
            count.append(True)
            if len(count) == 2: raise ValueError('PRIVATE PROFILE')
            return delegate(*args, **kwargs)
        with tempfile.TemporaryDirectory() as tmp:
            result = shared_soak(dataset(), 'http://127.0.0.1:1', lambda: None, Path(tmp), Fixture().engine.profile(),
                                 duration_s=10, max_blocks=4, probe=probe, clock=clock)
            self.assertEqual((len(count), len(result['blocks']), result['stoppedReason']), (2, 1, 'block_failed'))
            self.assertNotIn('PRIVATE', json.dumps(result))

    def test_real_fixture_busy_cannot_be_accepted_as_a_duration_boundary(self):
        fixture = Fixture(); fixture.busy = True
        def probe(data, url, _factory, **kwargs):
            return benchmark_shared(data, url, lambda: FixturePrimary(), client=fixture,
                                    health_transport=fixture.health, **kwargs)
        with tempfile.TemporaryDirectory() as tmp:
            result = shared_soak(dataset(), 'http://127.0.0.1:1', lambda: None, Path(tmp), fixture.engine.profile(),
                                 duration_s=1, max_blocks=3, probe=probe)
            self.assertEqual(result['status'], 'degraded')
            self.assertEqual(result['stoppedReason'], 'request_failed')
            self.assertEqual(len(result['blocks']), 1)
            self.assertEqual(fixture.calls, 2)

    def test_invalid_dataset_and_bounds_fail_before_probe(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            for options in ({'duration_s': 0}, {'duration_s': True}, {'duration_s': 7201}, {'max_blocks': 0}, {'max_blocks': 129}, {'journal': 1}):
                with self.assertRaises(ValueError):
                    shared_soak(dataset(), 'http://127.0.0.1:1', lambda: None, Path(tmp), Fixture().engine.profile(),
                                probe=lambda *_args, **_kw: calls.append(True), **options)
            data = dataset(); data.pop('sha256'); data['cases'][0]['split'] = 'holdout'
            with self.assertRaises(ValueError):
                shared_soak(sealed(data), 'http://127.0.0.1:1', lambda: None, Path(tmp), Fixture().engine.profile(),
                            probe=lambda *_args, **_kw: calls.append(True))
        self.assertEqual(calls, [])


if __name__ == '__main__': unittest.main()
