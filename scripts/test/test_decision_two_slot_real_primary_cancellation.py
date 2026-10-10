"""Actual two-slot worker/caller sockets; primary and retired epochs are synthetic."""
import copy
import json
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from decision_runtime.artifacts import sealed
from decision_runtime.metrics import OUTCOMES
from scripts.lib import decision_two_slot_real_primary_cancellation as diagnostic
from scripts.test import test_decision_two_slot_cancellation as shared


class PrimaryStartupFailureTest(unittest.TestCase):
    def test_partial_primary_start_retains_failure_and_cleans_both_owned_backends(self):
        context, _, cohort, _ = shared.fixture.fixture()
        with tempfile.TemporaryDirectory(dir=shared.ROOT/'docs/private', prefix='real-primary-startup-failure-') as temporary:
            root = Path(temporary); manifest = root/'manifest.json'; manifest.write_bytes(b'Synthetic manifest; no model.\n')
            context['manifestFileSha256'] = shared.digest(manifest.read_bytes()); context = sealed({k: v for k, v in context.items() if k != 'sha256'})
            context_path = root/'context.json'; context_path.write_bytes(shared.encoded(context)); directory = root/'run'
            native = Mock(pid=888888888, returncode=None); native.poll.side_effect = lambda: native.returncode
            primary = Mock(pid=888888889, returncode=None); owner = Mock(process=primary)
            calls = [0]; origin = time.time()
            def metrics(_port):
                return '\n'.join(f'agat_decision_requests_total{{outcome="{key}"}} {calls[0] if key == "ok" else 0}' for key in OUTCOMES)+f'\nagat_decision_backend_ready 1\nagat_decision_requests_in_progress 0\nagat_decision_server_start_time_seconds {origin}\n'
            def measure(*_args):
                calls[0] += 1
                result = cohort['traces'][0]['decisionObservations'][0]['observation']['result']
                return {'status': 'ok', 'reason': None, 'observation': {'result': result}, 'callerMs': 1, 'wallMs': 1}
            def owner_factory(_runtime, _root, _binaries, _models, _log, owned, _cancelled):
                def start(): owned.add(primary.pid); raise ValueError('Synthetic failure after primary process started')
                def close(_errors): primary.returncode = 0
                owner.start.side_effect = start; owner.close.side_effect = close; return owner
            def stop(process, _owned, _errors): process.returncode = 130
            with patch.object(shared.cli, 'historical_context_sources'), patch.object(shared.cli.launcher, 'frozen_sources', return_value=('0'*40, {})), \
                patch.object(shared.cli, 'verify_manifest', return_value=({}, None)), patch.object(shared.cli, 'verify_profile'), \
                patch.object(shared.cli.subprocess, 'check_output', return_value=shared.encoded(context['tokenizerEnvironment'])), \
                patch.object(shared.cli.subprocess, 'Popen', return_value=native), patch.object(diagnostic, 'prepare_primary', return_value={'synthetic': True}), \
                patch.object(diagnostic, 'OwnedPrimary', side_effect=owner_factory), patch.object(shared.cli, 'measure_one', side_effect=measure), \
                patch.object(shared.cli.active, 'bounded_metrics', side_effect=metrics), patch.object(shared.cli.runtime.shared, 'inventory', return_value=({native.pid}, {})), \
                patch.object(shared.cli.runtime, 'request', return_value={'status': 'ready', 'mode': 'shadow', 'profileSha256': context['profileSha256'], 'profileJson': json.dumps(context['profile'], sort_keys=True, separators=(',', ':'), ensure_ascii=False)}), \
                patch.object(shared.cli.runtime, 'stop_owned_process', side_effect=stop), patch.object(shared.cli.runtime, 'remaining_owned_processes', return_value=[]):
                status = shared.cli.main(['--context-profile', str(context_path), '--context-profile-file-sha256', shared.digest(context_path.read_bytes()),
                    '--runtime-python', sys.executable, '--manifest', str(manifest), '--evidence-dir', str(directory), '--cancel-at-original-index', '1',
                    '--primary-binaries', str(root), '--primary-models', str(root)], suite=diagnostic)
                self.assertEqual(status, 1); owner.close.assert_called_once(); self.assertEqual(native.returncode, 130)
            result = json.loads((directory/'result.json').read_bytes())
            self.assertEqual(result['failure']['reason'], 'Synthetic failure after primary process started')
            self.assertEqual(result['status'], 'failed'); self.assertEqual(result['primaryExitCode'], 0)
            self.assertEqual(result['ownedPids'], [native.pid, primary.pid]); self.assertEqual(result['remainingOwnedPids'], [])
            self.assertIn('primary.log', result['artifactSha256']); self.assertIsNone(result['physical'])


@unittest.skipUnless(shutil.which('node') and (shared.ROOT/'node_modules/tsx').exists(), 'Requires Node diagnostic dependencies')
class RealPrimaryCancellationActorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls): shared.TwoSlotActorTest.setUpClass.__func__(cls, suite=diagnostic)

    alter_json = shared.TwoSlotActorTest.alter_json
    alter_journal = shared.TwoSlotActorTest.alter_journal

    def check(self, artifacts=None, protocol=None):
        return diagnostic.verify_actor(self.context, protocol or self.protocol, self.recipe, self.driver, artifacts or self.artifacts)

    def test_actual_http_primary_peer_keeps_lease_and_context_rejects_after_recovery(self):
        result = self.check()
        self.assertEqual(result['selectedOriginalIndices'], [0, 1, 5, 3])
        self.assertEqual(result['primaryRealCalls'], 4); self.assertEqual(result['durablePrimaryOutputs'], 3)
        self.assertEqual(result['unknownCallerReturns'], 1); self.assertEqual(result['knownCallerReturns'], 3)
        self.assertEqual(result['healthyNativeOutcomes'], {'ok': 2, 'context_rejected': 1})
        self.assertGreater(result['actualPrimaryHttpOverlapMs'], 0)
        self.assertTrue(result['peerResponseObservedWithoutArtificialHold']); self.assertTrue(self.eof.is_set())
        self.assertEqual(self.result['physical']['knownCompletedPhysicalCalls'], 7)
        self.assertFalse(result['primaryConcurrencyCapacityQualified']); self.assertFalse(result['classificationAccuracyMeasured'])

    def test_full_primary_raw_response_and_translated_output_must_agree_after_rehash(self):
        def corrupt(rows):
            row = next(r for r in rows if r['index'] == 3); value = json.loads(row['nativeResponseBody'])
            value['message']['content'] = 'Different response'
            row['nativeResponseBody'] = shared.encoded(value).decode(); row['nativeResponseBodySha256'] = shared.digest(row['nativeResponseBody'].encode())
        with self.assertRaises(ValueError): self.check(self.alter_journal('primary-http.jsonl', corrupt))

    def test_whole_input_and_primary_generation_cannot_change_after_raw_rehash(self):
        for key in ('num_ctx', 'num_predict', 'seed'):
            def change(rows):
                row = rows[0]; value = json.loads(row['nativeRequestBody']); value['options'][key] += 1
                row['nativeRequestBody'] = shared.encoded(value).decode(); row['nativeRequestBodySha256'] = shared.digest(row['nativeRequestBody'].encode())
            with self.subTest(key=key), self.assertRaises(ValueError): self.check(self.alter_journal('primary-http.jsonl', change))
        with self.assertRaises(ValueError): self.check(self.alter_journal('primary-http.jsonl', lambda rows: rows[0].update(outputSha256='0'*64)))

    def test_native_peer_response_cannot_precede_recovery_even_after_row_rehash(self):
        early = json.loads(self.artifacts['peer-primary-held.json'])['heldAt']
        artifacts = self.alter_journal('primary-http.jsonl', lambda rows: next(r for r in rows if r['index'] == 2).update(nativeResponseReceivedAt=early))
        release = json.loads(artifacts['peer-primary-released.json']); release['nativeResponseReceivedAt'] = early
        artifacts['peer-primary-released.json'] = shared.encoded(release)
        with self.assertRaises(ValueError): self.check(artifacts)

    def test_actual_primary_cannot_be_replaced_by_fixture_or_foreign_native_url(self):
        for name, mutate in (('peer-primary-held.json', lambda r: r.update(kind='held_fixture')),
            ('peer-primary-released.json', lambda r: r.update(kind='fixture_release'))):
            with self.assertRaises(ValueError): self.check(self.alter_json(name, mutate))
        with self.assertRaises(ValueError): self.check(self.alter_journal('primary-http.jsonl', lambda rows: rows[0].update(nativeUrl='http://127.0.0.1:11434')))

    def test_peer_and_target_durable_outputs_and_revoked_late_writes_stay_bound(self):
        with self.assertRaises(ValueError): self.check(self.alter_json('trace-target.http.json', lambda t: next(s for s in t['run']['stages'] if s['processNodeId'] == 'agent').update(output='PRIMARY_OUTPUT')))
        with self.assertRaises(ValueError): self.check(self.alter_json('trace-peer-after-cancel.http.json', lambda t: t['run'].update(status='cancelled')))
        with self.assertRaises(ValueError): self.check(self.alter_journal('coordinator-http.jsonl', lambda rows: next(r for r in rows if r['path'].endswith('/complete') and r['httpStatus'] == 400).update(httpStatus=200)))

    def test_peer_selection_and_two_slots_cannot_be_changed_posthoc(self):
        for key, value in (('selectedOriginalIndices', [0, 1, 2, 3]), ('workerConcurrency', 1), ('primaryNumParallel', 2), ('primaryDecodeLimit', 256)):
            protocol = copy.deepcopy(self.protocol); protocol[key] = value
            with self.assertRaises(ValueError): self.check(protocol=protocol)


@unittest.skipUnless(shutil.which('node') and (shared.ROOT/'node_modules/tsx').exists(), 'Requires Node diagnostic dependencies')
class RealPrimaryCancellationReplayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls): shared.ReplayTest.setUpClass.__func__(cls, suite=diagnostic, actor=RealPrimaryCancellationActorTest)

    @classmethod
    def tearDownClass(cls): cls.temporary.cleanup()
    replay = shared.ReplayTest.replay

    def test_historical_full_context_raw_replay_forbids_models_and_network(self):
        receipt = self.replay(); self.assertEqual(receipt['status'], 'pass')
        self.assertEqual(receipt['inventory']['modelCallsDuringVerification'], 0)
        self.assertFalse(receipt['inventory']['liveCleanupVerified']); self.assertFalse(receipt['inventory']['gpuKernelPreemptionEstablished'])

    def test_missing_real_primary_artifact_or_hidden_physical_call_fails(self):
        artifacts = copy.deepcopy(self.artifacts); artifacts.pop('primary.log')
        with self.assertRaises(ValueError): self.replay(artifacts=artifacts)
        result = copy.deepcopy(self.result); result['samples'][-1]['counters']['ok'] += 1
        with self.assertRaises(ValueError): self.replay(result=result)

    def test_primary_settings_cleanup_or_actual_runner_shape_cannot_be_faked(self):
        plan = copy.deepcopy(self.plan); plan['primary']['settings']['OLLAMA_NUM_PARALLEL'] = '2'
        with self.assertRaises(ValueError): self.replay(plan=plan)
        result = copy.deepcopy(self.result); result['primaryExitCode'] = None
        with self.assertRaises(ValueError): self.replay(result=result)
        artifacts = copy.deepcopy(self.artifacts); artifacts['primary.log'] = b'-np 2\nn_seq_max = 2\nn_ctx = 65536\nn_ctx_seq = 32768\n'
        with self.assertRaises(ValueError): self.replay(artifacts=artifacts)

    def test_unknown_cleanup_missing_source_and_shortened_context_fail(self):
        result = copy.deepcopy(self.result); result['remainingOwnedPids'] = None
        with self.assertRaises(ValueError): self.replay(result=result)
        plan = copy.deepcopy(self.plan); plan['sourceFiles'].pop('scripts/run-two-slot-real-primary-cancellation.mts')
        with self.assertRaises(ValueError): self.replay(plan=plan)
        plan = copy.deepcopy(self.plan); plan['context']['inputs'].pop()
        with self.assertRaises(ValueError): self.replay(plan=plan)


if __name__ == '__main__': unittest.main()
