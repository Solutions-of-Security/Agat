"""Actual two-slot worker deadline/HTTP sockets with synthetic model responses/epochs."""
import copy
import json
import shutil
import unittest
from scripts.lib import decision_two_slot_parallel_real_primary_deadline as diagnostic
from scripts.test import test_decision_two_slot_cancellation as shared


@unittest.skipUnless(shutil.which('node') and (shared.ROOT/'node_modules/tsx').exists(), 'Requires Node diagnostic dependencies')
class ParallelRealPrimaryDeadlineActorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls): shared.TwoSlotActorTest.setUpClass.__func__(cls, suite=diagnostic)

    alter_json = shared.TwoSlotActorTest.alter_json
    alter_journal = shared.TwoSlotActorTest.alter_journal

    def check(self, artifacts=None, protocol=None, recipe=None):
        return diagnostic.verify_actor(self.context, protocol or self.protocol, recipe or self.recipe, self.driver, artifacts or self.artifacts)

    def test_real_primary_deadline_preserves_all_four_outputs_and_known_timeout(self):
        e = self.check(); self.assertEqual(e['primaryRealCalls'], 4); self.assertEqual(e['durablePrimaryOutputs'], 4)
        self.assertEqual(e['completedWorkflows'], 4); self.assertEqual(e['cancelledWorkflows'], 0)
        self.assertEqual(e['knownCallerReturns'], 4); self.assertEqual(e['unknownCallerReturns'], 0)
        self.assertEqual(e['unavailableTimeoutReturns'], 1); self.assertEqual(e['healthyNativeOutcomes'], {'ok': 2, 'context_rejected': 1})
        self.assertGreater(e['actualPrimaryHttpOverlapMs'], 0); self.assertEqual(e['primaryNumParallel'], 2)
        self.assertTrue(e['actualPrimaryTwoProcessingSlotsObserved']);self.assertGreaterEqual(e['actualTargetDecodedAtPeerAdmission'],116)
        self.assertLess(e['actualTargetDecodedAtPeerAdmission'],128);self.assertNotEqual(e['actualTargetPrimaryTaskId'],e['actualPeerPrimaryTaskId'])
        self.assertGreaterEqual(e['localDeadlineCallerMs'], 250); self.assertLessEqual(e['localDeadlineCallerMs'], 500)
        self.assertTrue(e['peerResponseObservedWithoutArtificialHold']); self.assertTrue(e['peerLeasePreserved']); self.assertTrue(self.eof.is_set())
        self.assertEqual(self.result['physical']['knownCompletedPhysicalCalls'], 7); self.assertTrue(e['targetTerminalCounterUnknown'])

    def test_published_versions_differ_only_in_actual_caller_budget(self):
        artifacts = self.alter_json('workflow-target-graph.json', lambda g: g['nodes'][1]['config']['decisionShadow'].update(timeoutMs=251))
        recipe = copy.deepcopy(self.recipe); recipe['targetGraphFileSha256'] = shared.digest(artifacts['workflow-target-graph.json'])
        with self.assertRaises(ValueError): self.check(artifacts, recipe=recipe)

    def test_primary_raw_response_generation_and_durable_output_cannot_disagree(self):
        def corrupt(rows):
            row = next(r for r in rows if r['index'] == 1); raw = json.loads(row['nativeResponseBody']); raw['message']['content'] = 'replacement'
            row['nativeResponseBody'] = shared.encoded(raw).decode(); row['nativeResponseBodySha256'] = shared.digest(row['nativeResponseBody'].encode())
        with self.assertRaises(ValueError): self.check(self.alter_journal('primary-http.jsonl', corrupt))
        with self.assertRaises(ValueError): self.check(self.alter_json('trace-target.http.json', lambda t: next(s for s in t['run']['stages'] if s['processNodeId'] == 'agent').update(output=None)))
        def generate(rows):
            row = rows[0]; raw = json.loads(row['nativeRequestBody']); raw['options']['num_predict'] = 256
            row['nativeRequestBody'] = shared.encoded(raw).decode(); row['nativeRequestBodySha256'] = shared.digest(row['nativeRequestBody'].encode())
        with self.assertRaises(ValueError): self.check(self.alter_journal('primary-http.jsonl', generate))

    def test_deadline_must_accept_original_leases_and_all_durable_writes(self):
        for suffix, code in (('/renew', 404), ('/decision-shadow', 400), ('/complete', 400)):
            with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                self.check(self.alter_journal('coordinator-http.jsonl', lambda rows: next(r for r in rows if r['path'].endswith(suffix)).update(httpStatus=code)))

    def test_known_deadline_cannot_be_replaced_by_cancelled_or_fabricated_result(self):
        def corrupt(rows):
            row = next(r for r in rows if r['path'].endswith('/decision-shadow') and json.loads(r['requestBody']).get('status') == 'unavailable')
            raw = json.loads(row['requestBody']); raw['reason'] = 'cancelled'
            row['requestBody'] = shared.encoded(raw).decode(); row['requestBodySha256'] = shared.digest(row['requestBody'].encode())
        with self.assertRaises(ValueError): self.check(self.alter_journal('coordinator-http.jsonl', corrupt))

    def test_actual_peer_native_response_cannot_precede_native_recovery(self):
        early = json.loads(self.artifacts['peer-primary-held.json'])['heldAt']
        artifacts = self.alter_journal('primary-http.jsonl', lambda rows: next(r for r in rows if r['index'] == 2).update(nativeResponseReceivedAt=early))
        release = json.loads(artifacts['peer-primary-released.json']); release['nativeResponseReceivedAt'] = early
        artifacts['peer-primary-released.json'] = shared.encoded(release)
        with self.assertRaises(ValueError): self.check(artifacts)
        with self.assertRaises(ValueError): self.check(self.alter_json('peer-primary-released.json', lambda r: r.update(socketClosedBeforeRelease=True)))

    def test_peer_input_or_assignment_cannot_be_replaced_while_primary_pending(self):
        with self.assertRaises(ValueError): self.check(self.alter_json('trace-peer-after-deadline.http.json', lambda t: t['run'].update(input='short replacement')))
        with self.assertRaises(ValueError): self.check(self.alter_json('trace-peer-before-release.http.json', lambda t: next(s for s in t['run']['stages'] if s['processNodeId'] == 'agent').update(nodeId='another-worker')))

    def test_target_first_primary_original_long_peer_and_num_parallel_are_prospective(self):
        for key, value in (('startOrder', [0, 2, 1, 3]), ('primaryNumParallel', 1), ('selectedOriginalIndices', [0, 1, 2, 3])):
            protocol = copy.deepcopy(self.protocol); protocol[key] = value
            with self.assertRaises(ValueError): self.check(protocol=protocol)


    def test_late_decode_progress_cannot_be_early_completed_or_use_another_shape(self):
        for changed in (115,128,True):
            def corrupt(rows):
                row=rows[-1];value=json.loads(row['body']);value[0]['next_token'][0]['n_decoded']=changed
                row['body']=shared.encoded(value).decode();row['bodySha256']=shared.digest(row['body'].encode())
            with self.subTest(decoded=changed),self.assertRaises(ValueError):self.check(self.alter_journal('primary-progress.jsonl',corrupt))
        def old_shape(rows):
            row=rows[-1];value=json.loads(row['body']);value[0]['next_token']=value[0]['next_token'][0]
            row['body']=shared.encoded(value).decode();row['bodySha256']=shared.digest(row['body'].encode())
        with self.assertRaises(ValueError):self.check(self.alter_journal('primary-progress.jsonl',old_shape))

    def test_primary_progress_cannot_be_rebound_to_another_input_or_run(self):
        for field,value in (('runId','other-run'),('inputSha256','0'*64),('nativeRequestBodySha256','0'*64),('method','POST')):
            with self.subTest(field=field),self.assertRaises(ValueError):
                self.check(self.alter_journal('primary-progress.jsonl',lambda rows:rows[-1].update({field:value})))

    def test_slot_decode_cannot_exceed_the_delivered_raw_native_response(self):
        def change(rows):
            row=next(r for r in rows if r['index']==1)
            native=json.loads(row['nativeResponseBody']);native['eval_count']=100
            translated=json.loads(row['responseBody']);translated['usage']['completion_tokens']=100
            for field,value in (('nativeResponseBody',native),('responseBody',translated)):
                row[field]=shared.encoded(value).decode();row[field+'Sha256']=shared.digest(row[field].encode())
        with self.assertRaises(ValueError):self.check(self.alter_journal('primary-http.jsonl',change))
        with self.assertRaises(ValueError):self.check({**self.artifacts,'primary.log':self.artifacts['primary.log'].replace(b'task 1 |',b'task 2 |')})

    def test_progress_requires_owned_runner_and_fresh_prospective_boundary(self):
        for mutate in (lambda value:value.update(runnerPid=42),lambda value:value.update(url='http://127.0.0.1:11434'),
            lambda value:value.update(command=value['command'].replace('-np 2','-np 1'))):
            with self.assertRaises(ValueError):self.check(self.alter_json('primary-runner.json',mutate))
        protocol=copy.deepcopy(self.protocol);protocol['primaryProgress']['minDecoded']=1
        with self.assertRaises(ValueError):self.check(protocol=protocol)
        with self.assertRaises(ValueError):self.check(self.alter_journal('primary-progress.jsonl',lambda rows:rows[-1].update(receivedAt='2000-01-01T00:00:00Z')))

    def test_two_processing_slots_cannot_be_inferred_from_http_overlap(self):
        def change(rows, field, value):
            row=rows[-1];slots=json.loads(row['body']);slots[1][field]=value
            row['body']=shared.encoded(slots).decode();row['bodySha256']=shared.digest(row['body'].encode())
        for field,value in (('is_processing',False),('id_task',1),('id_task',777),('n_ctx',65536)):
            with self.subTest(field=field),self.assertRaises(ValueError):
                self.check(self.alter_journal('primary-parallel-progress.jsonl',lambda rows:change(rows,field,value)))

    def test_parallel_slot_reads_require_both_original_requests_and_pending_target(self):
        for field,value in (('targetRunId','other'),('peerRunId','other'),('targetInputSha256','0'*64),('peerInputSha256','0'*64),
            ('targetNativeRequestBodySha256','0'*64),('peerNativeRequestBodySha256','0'*64),('method','POST'),('receivedAt','2999-01-01T00:00:00Z')):
            with self.subTest(field=field),self.assertRaises(ValueError):
                self.check(self.alter_journal('primary-parallel-progress.jsonl',lambda rows:rows[-1].update({field:value})))


@unittest.skipUnless(shutil.which('node') and (shared.ROOT/'node_modules/tsx').exists(), 'Requires Node diagnostic dependencies')
class ParallelRealPrimaryDeadlineReplayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls): shared.ReplayTest.setUpClass.__func__(cls, suite=diagnostic, actor=ParallelRealPrimaryDeadlineActorTest)

    @classmethod
    def tearDownClass(cls): cls.temporary.cleanup()
    replay = shared.ReplayTest.replay

    def test_full_historical_source_replay_makes_no_model_or_network_calls(self):
        receipt = self.replay(); self.assertEqual(receipt['status'], 'pass')
        self.assertEqual(receipt['inventory']['modelCallsDuringVerification'], 0)
        self.assertFalse(receipt['inventory']['liveCleanupVerified']); self.assertFalse(receipt['inventory']['gpuKernelPreemptionEstablished'])

    def test_missing_primary_receipt_or_unknown_owned_cleanup_fails(self):
        artifacts = copy.deepcopy(self.artifacts); artifacts.pop('primary-parallel-progress.jsonl')
        with self.assertRaises(ValueError): self.replay(artifacts=artifacts)
        result = copy.deepcopy(self.result); result['remainingOwnedPids'] = None
        with self.assertRaises(ValueError): self.replay(result=result)
        result = copy.deepcopy(self.result); result['primaryExitCode'] = None
        with self.assertRaises(ValueError): self.replay(result=result)

    def test_primary_profile_and_extra_physical_call_fail_after_reseal(self):
        plan = copy.deepcopy(self.plan); plan['primary']['settings']['OLLAMA_NUM_PARALLEL'] = '1'
        with self.assertRaises(ValueError): self.replay(plan=plan)
        result = copy.deepcopy(self.result); result['samples'][-1]['counters']['ok'] += 1
        with self.assertRaises(ValueError): self.replay(result=result)

    def test_missing_source_or_versioned_authentication_cannot_be_omitted(self):
        plan = copy.deepcopy(self.plan); plan['sourceFiles'].pop('scripts/run-two-slot-parallel-real-primary-deadline.mts')
        with self.assertRaises(ValueError): self.replay(plan=plan)
        artifacts = copy.deepcopy(self.artifacts); rows = diagnostic.journal(artifacts['cohort-http.jsonl']); rows[1]['unauthenticatedStatus'] = 200
        artifacts['cohort-http.jsonl'] = b''.join(shared.encoded(row) for row in rows)
        with self.assertRaises(ValueError): self.replay(artifacts=artifacts)


if __name__ == '__main__': unittest.main()
