import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import time
import sys
import unittest
from unittest.mock import patch

from scripts.lib import decision_arrival_primary as primary
from scripts.lib.decision_arrival_rate import run_phase
from scripts.test.test_decision_arrival_rate import Client, case, verifier


def response():
    return {'model': primary.MODEL, 'done': True, 'done_reason': 'length',
            'message': {'role': 'assistant', 'content': 'Fixture primary output.'},
            'prompt_eval_count': 20, 'eval_count': 128, 'total_duration': 500_000_000,
            'load_duration': 0, 'prompt_eval_duration': 100_000_000, 'eval_duration': 400_000_000}


def active_phase():
    rows = [{'index': index, 'scheduledMs': index * 2000, 'dispatchMs': index * 2000 + 1,
             'startedMs': index * 2000 + 2, 'finishedMs': index * 2000 + 502,
             'status': 'returned', 'wallMs': 500, 'response': response()} for index in range(2)]
    phase = {'schemaVersion': primary.PHASE_SCHEMA, 'condition': 'primary_active',
             'phaseOriginMonotonicMs': 100000, 'elapsedMs': 4000, 'primaryRows': rows,
             'rows': [{'status': 'ok', 'startedMs': 0, 'finishedMs': 1000}]}
    return phase, {'condition': 'primary_active'}, {'phaseSeconds': 4}


class PrimaryArrivalTest(unittest.TestCase):
    def test_configurable_schedule_is_bounded_and_legacy_default_is_unchanged(self):
        legacy = primary.schedules(12)
        lower = primary.schedules(12, .5)
        self.assertEqual([(r['ratePerSecond'], r['count']) for r in legacy], [(1, 12)] * 6)
        self.assertEqual([(r['ratePerSecond'], r['count']) for r in lower], [(.5, 6)] * 6)
        for seconds, rate in ((True, .5), (31, .5), (12, True), (12, float('nan')), (12, 2)):
            with self.subTest(seconds=seconds, rate=rate), self.assertRaises(ValueError): primary.schedules(seconds, rate)

    def test_decision_rate_requires_primary_before_any_measurement_effect(self):
        entry = Path(__file__).resolve().parents[1] / 'run-decision-arrival-rate.py'
        spec = importlib.util.spec_from_file_location('lower_arrival_cli', entry)
        cli = importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)
        args = ['probe', '--evidence-dir', 'docs/private/not-created-lower-fixture', '--runtime-python', sys.executable,
                '--manifest', 'missing-manifest', '--profile', 'missing-profile', '--decision-rate', '.5']
        with patch.object(sys, 'argv', args), patch.object(cli, 'frozen_sources') as sources, \
                self.assertRaisesRegex(ValueError, 'requires both primary paths'):
            cli.main()
        sources.assert_not_called()

    def test_bad_negotiated_rate_and_legacy_rate_override_fail_before_source_io(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            for schema, rate in ((primary.CONFIGURABLE_SCHEMAS[0], True),
                                 (primary.CONFIGURABLE_SCHEMAS[0], 2), (primary.PLAN_SCHEMA, .5)):
                (directory / 'plan.json').write_text(json.dumps({'schemaVersion': schema, 'decisionRatePerSecond': rate}))
                with self.subTest(schema=schema, rate=rate), patch.object(verifier.subprocess, 'check_output') as source_io, \
                        self.assertRaises(ValueError): verifier.verify(directory)
                source_io.assert_not_called()

    def test_v3_real_half_rate_intervals_verify_against_fixed_schedule(self):
        def transport(*_):
            time.sleep(.1)
            value = response(); value['total_duration'] = 1_000_000
            return value
        origin = time.monotonic()
        companion = primary.PrimaryArrivals(transport, origin, 4, lambda: False); companion.start()
        client = Client(.15)
        phase = run_phase(client, case(), client.engine.profile(), rate=.5, count=2, origin=origin)
        primary_rows = companion.finish()
        phase.update(schemaVersion=primary.CONFIGURABLE_SCHEMAS[2], condition='primary_active',
                     phaseOriginMonotonicMs=origin * 1000, primaryRows=primary_rows,
                     elapsedMs=(time.monotonic() - origin) * 1000)
        plan = {'schemaVersion': primary.CONFIGURABLE_SCHEMAS[0], 'phaseSeconds': 4,
                'decisionRatePerSecond': .5, 'profile': client.engine.profile(),
                'callerTimeoutMs': 10000, 'thresholdMs': 5000}
        schedule = primary.schedules(4, .5)[1]
        verifier.verify_phase(phase, schedule, case(), plan)
        result = verifier.verify_primary_phase(phase, schedule, plan, primary)
        self.assertEqual(result['overlapPairs'], 2)
        changed = copy.deepcopy(schedule); changed['ratePerSecond'] = 1
        with self.assertRaises(ValueError): verifier.verify_phase(phase, changed, case(), plan)

    def test_primary_response_requires_complete_local_model_and_exact_budget(self):
        primary.validate_response(response())
        for mutate in (lambda r: r.update(model='foreign'), lambda r: r.update(done=False),
                       lambda r: r.update(eval_count=129), lambda r: r.update(eval_count=True),
                       lambda r: r.update(total_duration=-1), lambda r: r.update(load_duration=False),
                       lambda r: r['message'].update(content=''), lambda r: r['message'].update(thinking='hidden')):
            raw = response(); mutate(raw)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError): primary.validate_response(raw)

    def test_failed_primary_call_is_not_fabricated_as_returned_or_http_200(self):
        def fail(*_): raise OSError('fixture')
        row = primary.measure_primary(fail)
        self.assertEqual(row['status'], 'measurement_error')
        self.assertNotIn('response', row); self.assertNotIn('httpStatus', row)

    def test_pair_requires_real_overlap_and_complete_primary_denominator(self):
        phase, schedule, plan = active_phase()
        self.assertEqual(verifier.verify_primary_phase(phase, schedule, plan, primary),
                         {'scheduled': 2, 'returned': 2, 'overlapPairs': 1})
        for mutate in (lambda p: p['primaryRows'].pop(), lambda p: p['primaryRows'][0].update(scheduledMs=1),
                       lambda p: p['rows'][0].update(startedMs=3000, finishedMs=3500),
                       lambda p: p['primaryRows'][1].update(startedMs=400),
                       lambda p: p['primaryRows'][0].update(dispatchMs=200),
                       lambda p: p['primaryRows'][0].update(finishedMs=5000)):
            changed = copy.deepcopy(phase); mutate(changed)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                verifier.verify_primary_phase(changed, schedule, plan, primary)

    def test_idle_phase_has_no_primary_attempts(self):
        phase, _, plan = active_phase(); phase['condition'] = 'primary_idle_after'; phase['primaryRows'] = []
        verifier.verify_primary_phase(phase, {'condition': 'primary_idle_after'}, plan, primary)
        phase['primaryRows'] = active_phase()[0]['primaryRows']
        with self.assertRaises(ValueError): verifier.verify_primary_phase(phase, {'condition': 'primary_idle_after'}, plan, primary)

    def test_corrupt_native_binary_rejected_before_loading_model_or_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); pin = root / primary.PRIMARY_SOURCES[2]; pin.parent.mkdir(parents=True)
            pin.write_text(json.dumps({'schemaVersion': 'agat.ollama-native-release.v1', 'version': '0.35.1', 'files': {'ollama': '0' * 64}}))
            binaries = root / 'bin'; binaries.mkdir(); (binaries / 'ollama').write_bytes(b'foreign')
            with self.assertRaisesRegex(ValueError, 'dependency SHA'):
                primary.prepare(root, binaries, root / 'missing-models')

    def test_primary_window_is_bounded_before_creating_threads(self):
        for seconds in (True, 0, 3, 31, 4.5):
            with self.subTest(seconds=seconds), self.assertRaises(ValueError):
                primary.PrimaryArrivals(lambda *_: response(), time.monotonic(), seconds, lambda: False)

    def test_primary_paths_must_be_paired_before_any_measurement_effect(self):
        entry = Path(__file__).resolve().parents[1] / 'run-decision-arrival-rate.py'
        spec = importlib.util.spec_from_file_location('primary_arrival_cli', entry)
        cli = importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)
        args = ['probe', '--evidence-dir', 'docs/private/not-created-primary-fixture', '--runtime-python', sys.executable,
                '--manifest', 'missing-manifest', '--profile', 'missing-profile', '--primary-binaries', 'missing-binaries']
        with patch.object(sys, 'argv', args), patch.object(cli, 'frozen_sources') as sources, \
                self.assertRaisesRegex(ValueError, 'Both primary paths'):
            cli.main()
        sources.assert_not_called()

    def test_shared_origin_records_real_thread_intervals_and_drains_primary(self):
        def transport(*_):
            time.sleep(.01)
            value = response(); value['total_duration'] = 1_000_000
            return value
        origin = time.monotonic()
        companion = primary.PrimaryArrivals(transport, origin, 4, lambda: False); companion.start()
        client = Client(.6)
        phase = run_phase(client, case(), client.engine.profile(), rate=4, count=4, slots=1, origin=origin)
        primary_rows = companion.finish()
        phase.update(schemaVersion=primary.PHASE_SCHEMA, condition='primary_active',
                     phaseOriginMonotonicMs=origin * 1000, primaryRows=primary_rows,
                     elapsedMs=(time.monotonic() - origin) * 1000)
        self.assertFalse(companion.thread.is_alive())
        summary = verifier.verify_primary_phase(phase, {'condition': 'primary_active'}, {'phaseSeconds': 4}, primary)
        self.assertEqual(summary['scheduled'], 2); self.assertEqual(summary['returned'], 2)
        self.assertGreater(summary['overlapPairs'], 0)

    def test_primary_configuration_and_release_are_bound_to_source_bytes(self):
        from decision_runtime.contracts import fingerprint
        import hashlib
        pin = b'{"version":"fixture"}'
        config = {'model': primary.MODEL, 'manifestSha256': primary.DIGEST, 'request': primary.REQUEST,
                  'requestSha256': fingerprint(primary.REQUEST), 'settings': primary.SETTINGS,
                  'ratePerSecond': .5, 'clientSlots': 1, 'timeoutSeconds': 30, 'blobCount': 1, 'blobBytes': 1,
                  'release': json.loads(pin), 'releaseFileSha256': hashlib.sha256(pin).hexdigest()}
        verifier.verify_primary_plan({'primary': config}, {primary.PRIMARY_SOURCES[2]: pin}, primary)
        config = copy.deepcopy(config); config['settings']['OLLAMA_NUM_PARALLEL'] = '2'
        with self.assertRaises(ValueError): verifier.verify_primary_plan({'primary': config}, {primary.PRIMARY_SOURCES[2]: pin}, primary)
