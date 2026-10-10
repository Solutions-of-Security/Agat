"""Actual worker's 250 ms deadline with a held peer; native epochs are synthetic here."""
import copy
import json
import shutil
import unittest
from scripts.lib import decision_two_slot_deadline as diagnostic
from scripts.test import test_decision_two_slot_cancellation as shared


@unittest.skipUnless(shutil.which('node') and (shared.ROOT/'node_modules/tsx').exists(), 'Requires Node diagnostic dependencies')
class TwoSlotDeadlineActorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls): shared.TwoSlotActorTest.setUpClass.__func__(cls, suite=diagnostic)

    alter_json = shared.TwoSlotActorTest.alter_json
    alter_journal = shared.TwoSlotActorTest.alter_journal

    def check(self, artifacts=None, protocol=None, recipe=None):
        return diagnostic.verify_actor(self.context, protocol or self.protocol, recipe or self.recipe, self.driver, artifacts or self.artifacts)

    def test_actual_worker_deadline_preserves_four_primary_outputs_and_known_return(self):
        evidence = self.check()
        self.assertEqual(evidence['completedWorkflows'], 4); self.assertEqual(evidence['cancelledWorkflows'], 0)
        self.assertEqual(evidence['durablePrimaryOutputs'], 4); self.assertEqual(evidence['knownCallerReturns'], 4)
        self.assertEqual(evidence['unknownCallerReturns'], 0); self.assertEqual(evidence['unavailableTimeoutReturns'], 1)
        self.assertGreaterEqual(evidence['localDeadlineCallerMs'], 250); self.assertLessEqual(evidence['localDeadlineCallerMs'], 500)
        self.assertTrue(evidence['peerPrimaryConnectionPreserved']); self.assertTrue(evidence['peerLeasePreserved']); self.assertTrue(self.eof.is_set())
        self.assertEqual(self.result['physical']['knownCompletedPhysicalCalls'], 7); self.assertEqual(len(self.native_calls), 4)
        self.assertTrue(evidence['targetTerminalCounterUnknown']); self.assertFalse(evidence['classificationAccuracyMeasured'])

    def test_published_same_process_versions_differ_only_in_caller_budget(self):
        self.check()
        artifacts = self.alter_json('workflow-target-graph.json', lambda r: r['nodes'][1]['config']['decisionShadow'].update(timeoutMs=251))
        recipe = copy.deepcopy(self.recipe); recipe['targetGraphFileSha256'] = shared.digest(artifacts['workflow-target-graph.json'])
        with self.assertRaises(ValueError): self.check(artifacts, recipe=recipe)

    def test_original_lease_cannot_be_revoked_or_write_rejected(self):
        for suffix, code in (('/renew', 404), ('/decision-shadow', 400), ('/complete', 400)):
            with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                self.check(self.alter_journal('coordinator-http.jsonl', lambda rows: next(r for r in rows if r['path'].endswith(suffix)).update(httpStatus=code)))

    def test_cancelled_reason_and_missing_monotonic_deadline_fail_after_request_rehash(self):
        def change(rows, mutate):
            row = next(r for r in rows if r['path'].endswith('/decision-shadow') and json.loads(r['requestBody']).get('status') == 'unavailable')
            value = json.loads(row['requestBody']); mutate(value); row['requestBody'] = shared.encoded(value).decode(); row['requestBodySha256'] = shared.digest(row['requestBody'].encode())
        for mutate in (lambda r: r.update(reason='cancelled'), lambda r: r['callerTiming'].update(durationMs=249), lambda r: r['callerTiming'].update(clock='wall')):
            with self.assertRaises(ValueError): self.check(self.alter_journal('coordinator-http.jsonl', lambda rows: change(rows, mutate)))

    def test_closed_answered_or_early_released_peer_fails(self):
        for key, value in (('socketClosedBeforeRelease', True), ('socketOpenAtRelease', False), ('responseBytesWrittenBeforeRelease', 1),
            ('releasedAt', json.loads(self.artifacts['peer-primary-held.json'])['heldAt'])):
            with self.subTest(key=key), self.assertRaises(ValueError): self.check(self.alter_json('peer-primary-released.json', lambda r: r.update({key: value})))

    def test_wrong_census_version_or_missing_actual_auth_response_fails(self):
        with self.assertRaises(ValueError): self.check(self.alter_json('cohort-target.http.json', lambda r: r['scope'].update(processVersion=1)))
        with self.assertRaises(ValueError): self.check(self.alter_journal('cohort-http.jsonl', lambda rows: rows[1].update(unauthenticatedStatus=200)))
        with self.assertRaises(ValueError): self.check(self.alter_journal('cohort-http.jsonl', lambda rows: rows.pop()))

    def test_preparation_requires_two_original_same_worker_distinct_assignments(self):
        projected = diagnostic.projection(self.context, 1); prepared = json.loads(self.artifacts[diagnostic.PREPARED_FILE])
        diagnostic.validate_preparation(projected, prepared, self.artifacts)
        def reassign(trace): next(s for s in trace['run']['stages'] if s['processNodeId'] == 'agent')['worker']['nodeId'] = 'other-worker'
        artifacts = self.alter_json('trace-peer-before.http.json', reassign)
        prepared['peerTraceFileSha256'] = shared.digest(artifacts['trace-peer-before.http.json'])
        with self.assertRaises(ValueError): diagnostic.validate_preparation(projected, prepared, artifacts)

    def test_primary_whole_input_cannot_be_shortened_after_request_rehash(self):
        def shorten(rows):
            row = next(r for r in rows if r['index'] == 2); value = json.loads(row['requestBody']); value['messages'][1]['content'] = 'short replacement'
            row['requestBody'] = shared.encoded(value).decode(); row['requestBodySha256'] = shared.digest(row['requestBody'].encode())
        with self.assertRaises(ValueError): self.check(self.alter_journal('primary-http.jsonl', shorten))

    def test_extra_native_request_and_reassigned_peer_snapshot_fail(self):
        with self.assertRaises(ValueError): self.check(self.alter_json('active-transport.json', lambda r: r['rows'].append(copy.deepcopy(r['rows'][-1]))))
        with self.assertRaises(ValueError): self.check(self.alter_json('trace-peer-after-deadline.http.json', lambda r: r['run'].update(status='cancelled')))

    def test_shortened_protocol_and_wrong_versions_fail(self):
        for key, value in (('workerConcurrency', 1), ('startOrder', [0, 1, 2, 3]), ('caseVersions', [1, 1, 1, 1])):
            protocol = copy.deepcopy(self.protocol); protocol[key] = value
            with self.assertRaises(ValueError): self.check(protocol=protocol)


@unittest.skipUnless(shutil.which('node') and (shared.ROOT/'node_modules/tsx').exists(), 'Requires Node diagnostic dependencies')
class DeadlineReplayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls): shared.ReplayTest.setUpClass.__func__(cls, suite=diagnostic, actor=TwoSlotDeadlineActorTest)

    @classmethod
    def tearDownClass(cls): cls.temporary.cleanup()
    replay = shared.ReplayTest.replay

    def test_full_historical_source_replay_forbids_model_and_network_calls(self):
        receipt = self.replay(); self.assertEqual(receipt['status'], 'pass')
        self.assertEqual(receipt['inventory']['modelCallsDuringVerification'], 0)
        self.assertFalse(receipt['inventory']['liveCleanupVerified']); self.assertFalse(receipt['inventory']['gpuKernelPreemptionEstablished'])

    def test_unknown_cleanup_hidden_physical_start_or_missing_warmup_fails(self):
        for mutate in (lambda r: r.update(remainingOwnedPids=None), lambda r: r['samples'][-1]['counters'].update(ok=99),
            lambda r: r['warmup'].pop(), lambda r: r.update(runtimeExitCodes=[0, 130])):
            result = copy.deepcopy(self.result); mutate(result)
            with self.assertRaises(ValueError): self.replay(result=result)

    def test_omitted_source_or_raw_version_census_fails_after_resealing(self):
        plan = copy.deepcopy(self.plan); plan['sourceFiles'].pop('scripts/run-two-slot-native-deadline.mts')
        with self.assertRaises(ValueError): self.replay(plan=plan)
        artifacts = copy.deepcopy(self.artifacts); artifacts.pop('cohort-target.http.json')
        with self.assertRaises(ValueError): self.replay(artifacts=artifacts)

    def test_false_prefix_ownership_and_extra_result_fields_fail_after_resealing(self):
        artifacts = copy.deepcopy(self.artifacts); armed = json.loads(artifacts['native-prefix-armed.json']); armed['runtimePid'] = 999
        artifacts['native-prefix-armed.json'] = shared.encoded(armed)
        with self.assertRaises(ValueError): self.replay(artifacts=artifacts)
        result = copy.deepcopy(self.result); result['fabricatedQualification'] = True
        with self.assertRaises(ValueError): self.replay(result=result)


if __name__ == '__main__': unittest.main()
