"""Actual paired coordinator/worker requests; primary/native/model residence are fixtures."""
import copy
import hashlib
import json
import shutil
import subprocess
import unittest
from unittest.mock import patch
from scripts.lib import decision_public_paired_real_primary as diagnostic
from scripts.test import test_decision_public_real_primary as shared


@unittest.skipUnless(shutil.which('node') and (shared.ROOT/'node_modules/tsx').exists(), 'Requires Node diagnostic dependencies')
class PairedWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls): shared.MatchedWorkflowTest.setUpClass.__func__(cls, suite=diagnostic)

    mutate_rows = shared.MatchedWorkflowTest.mutate_rows
    def check(self, artifacts=None, recipe=None, driver=None, protocol=None):
        return diagnostic.verify_inventory(self.context, protocol or diagnostic.PROTOCOL, recipe or self.recipe, driver or self.driver, artifacts or self.artifacts)

    def test_actual_two_slot_primary_overlap_and_known_busy_preserve_all_outputs(self):
        evidence = self.check(); self.assertEqual(evidence['actualWorkflows'], 12); self.assertEqual(evidence['primaryOutputsPreserved'], 12)
        self.assertEqual(evidence['durableShadowReturns'], 6); self.assertEqual(evidence['matchedPrimaryRequests'], 6)
        self.assertEqual(evidence['actualTwoSlotPrimaryWitnesses'], 6); self.assertGreater(evidence['nativeBusyWitnesses'], 0)
        self.assertGreater(evidence['nativePrimaryHttpOverlapMs']['min'], 0); self.assertEqual(len(self.native_calls), 6)
        self.assertFalse(evidence['classificationAccuracyMeasured']); self.assertFalse(evidence['causalOverheadEstablished'])

    def test_async_completion_order_is_allowed_but_duplicated_native_identity_fails(self):
        data = self.mutate_rows('primary-http.jsonl', lambda rows: rows.reverse()); self.assertEqual(self.check(data)['actualWorkflows'], 12)
        with self.assertRaises(ValueError): self.check(self.mutate_rows('primary-http.jsonl', lambda rows: rows.append(copy.deepcopy(rows[0]))))

    def test_primary_without_actual_native_http_overlap_fails_after_rehash(self):
        def serial(rows):
            rows[1]['nativeStartedAt'] = rows[0]['completedAt']
        with self.assertRaises(ValueError): self.check(self.mutate_rows('primary-http.jsonl', serial))

    def test_wrong_actual_readonly_lease_run_stage_binding_fails(self):
        for key, value in (('stageId', 'foreign-stage'), ('nodeId', 'foreign-worker'), ('runId', 'foreign-run'), ('leaseId', 'foreign-lease')):
            with self.subTest(key=key), self.assertRaises(ValueError): self.check(self.mutate_rows('coordinator-http.jsonl', lambda rows: rows[0].update({key: value})))

    def test_busy_needs_live_native_peer_in_same_original_pair(self):
        def idle(rows):
            for row in rows:
                row['metricsRaw'] = row['metricsRaw'].replace('agat_decision_requests_in_progress 1', 'agat_decision_requests_in_progress 0')
                row['metricsSha256'] = hashlib.sha256(row['metricsRaw'].encode()).hexdigest()
        with self.assertRaises(ValueError): self.check(self.mutate_rows('admission-metrics.jsonl', idle))
        with self.assertRaises(ValueError): self.check(self.mutate_rows('admission-metrics.jsonl', lambda rows: [row.update(pendingPeers=[]) for row in rows]))

    def test_reassigned_or_answered_primary_witness_fails_after_raw_rehash(self):
        def change(rows, mutate):
            item = rows[0]['traces'][0]; trace = json.loads(item['body']); mutate(trace)
            item['body'] = shared.encoded(trace).decode(); item['bodySha256'] = hashlib.sha256(item['body'].encode()).hexdigest()
        for mutate in (lambda t: t['run'].update(status='completed'), lambda t: next(s for s in t['run']['stages'] if s['processNodeId']=='agent').update(nodeId='foreign')):
            with self.assertRaises(ValueError): self.check(self.mutate_rows('paired-primary-witnesses.jsonl', lambda rows: change(rows, mutate)))

    def test_lost_whole_input_context_or_actual_primary_output_fails(self):
        def shorten(rows):
            value=json.loads(rows[0]['nativeRequestBody']);value['messages'][1]['content']='replacement'
            rows[0]['nativeRequestBody']=shared.encoded(value).decode();rows[0]['nativeRequestBodySha256']=hashlib.sha256(rows[0]['nativeRequestBody'].encode()).hexdigest()
        with self.assertRaises(ValueError): self.check(self.mutate_rows('primary-http.jsonl', shorten))
        def disclose_condition(rows):
            row = rows[0]; request = json.loads(row['requestBody']); native = json.loads(row['nativeRequestBody'])
            request['messages'][1]['content'] = 'Diagnostic phase: '+row['condition']+'\n'+request['messages'][1]['content']
            native['messages'] = request['messages']; row['messagesSha256'] = hashlib.sha256(json.dumps(request['messages'], ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
            for key, value in (('requestBody', request), ('nativeRequestBody', native)):
                row[key] = shared.encoded(value).decode(); row[key+'Sha256'] = hashlib.sha256(row[key].encode()).hexdigest()
        with self.assertRaises(ValueError): self.check(self.mutate_rows('primary-http.jsonl', disclose_condition))
        with self.assertRaises(ValueError): self.check(self.mutate_rows('coordinator-http.jsonl', lambda rows: next(r for r in rows if r['path'].endswith('/complete')).update(httpStatus=400)))

    def test_missing_versioned_auth_response_or_prospective_budget_fails(self):
        with self.assertRaises(ValueError): self.check(self.mutate_rows('cohort-http.jsonl', lambda rows: rows[1].update(unauthenticatedStatus=200)))
        for key, value in (('workerConcurrency', 1), ('primaryNumParallel', 1), ('primaryContextLength', 8192), ('retryCount', 1)):
            protocol = copy.deepcopy(diagnostic.PROTOCOL); protocol[key] = value
            with self.assertRaises(ValueError): self.check(protocol=protocol)


@unittest.skipUnless(shutil.which('node') and (shared.ROOT/'node_modules/tsx').exists(), 'Requires Node diagnostic dependencies')
class PairedReplayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls): shared.ReplayVerificationTest.setUpClass.__func__(cls, suite=diagnostic, actor=PairedWorkflowTest)
    @classmethod
    def tearDownClass(cls): cls.temporary.cleanup()
    replay = shared.ReplayVerificationTest.replay

    def test_source_bound_replay_forbids_model_process_and_network_calls(self):
        original = subprocess.Popen
        def git_only(args, *positional, **kwargs):
            if args[0] != 'git': raise AssertionError('Replay attempted model/worker')
            return original(args, *positional, **kwargs)
        with patch('subprocess.Popen', side_effect=git_only), patch('socket.create_connection', side_effect=AssertionError('Replay attempted network')):
            receipt = self.replay()
        self.assertEqual(receipt['status'], 'pass'); self.assertEqual(receipt['modelCallsDuringVerification'], 0)

    def test_missing_source_unknown_cleanup_hidden_native_call_and_epoch_drift_fail(self):
        plan=copy.deepcopy(self.plan);plan['sourceFiles'].pop('scripts/run-public-support-paired-real-primary.mts')
        with self.assertRaises(ValueError): self.replay(plan=plan)
        for mutate in (lambda r:r.update(remainingOwnedPids=None), lambda r:r['samples'][-1]['counters'].update(ok=99)):
            result=copy.deepcopy(self.result);mutate(result)
            with self.assertRaises(ValueError): self.replay(result=result)

    def test_requested_parallelism_must_match_actual_runner_log(self):
        artifacts=copy.deepcopy(self.artifacts);artifacts['primary.log']=artifacts['primary.log'].replace(b'n_seq_max = 2',b'n_seq_max = 1')
        result=copy.deepcopy(self.result);result['artifactSha256']['primary.log']=hashlib.sha256(artifacts['primary.log']).hexdigest()
        with self.assertRaises(ValueError): self.replay(result=result,artifacts=artifacts)


if __name__ == '__main__': unittest.main()
