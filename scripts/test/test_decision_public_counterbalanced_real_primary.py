"""Real coordinator/worker replication; primary/native inference remain fixtures."""
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from decision_runtime.artifacts import sealed
from scripts.lib import decision_public_counterbalance as design
from scripts.lib import decision_public_counterbalanced_real_primary as diagnostic
from scripts.test import test_decision_public_real_primary as shared


def fixture_design(context, plan):
    pin = hashlib.sha256(shared.encoded(context)).hexdigest()
    schedule = design.schedule(len(context['inputs']), pin)
    parent = sealed({'schemaVersion': design.PLAN_SCHEMA, 'status': 'planned',
        'createdAt': (datetime.fromisoformat(plan['createdAt'])-timedelta(seconds=1)).isoformat(),
        'sourceCommit': 'd'*40, 'sourceFiles': {'fixture-only.py': 'e'*64},
        'contextProfileFileSha256': pin, 'context': context, 'protocol': design.PROTOCOL,
        'generation': diagnostic.GENERATION, 'settings': diagnostic.SETTINGS, 'schedule': schedule,
        'evidence': design.evidence(context, schedule), **design.FLAGS})
    raw = shared.encoded(parent)
    return {'plan': {'replicationDesignFileSha256': hashlib.sha256(raw).hexdigest(), 'contextProfileFileSha256': pin},
        'artifacts': {'replication-design.json': raw}}


@unittest.skipUnless(shutil.which('node') and (shared.ROOT/'node_modules/tsx').exists(), 'Requires Node diagnostic dependencies')
class CounterbalancedRealWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        shared.MatchedWorkflowTest.setUpClass.__func__(cls, suite=diagnostic, primary_variation=True, initial_artifacts=fixture_design)

    mutate_rows = shared.MatchedWorkflowTest.mutate_rows
    def check(self, artifacts=None, recipe=None, driver=None, protocol=None):
        return diagnostic.verify_inventory(self.context, protocol or diagnostic.PROTOCOL,
            recipe or self.recipe, driver or self.driver, artifacts or self.artifacts)

    def test_actual_replicas_keep_all_workflows_distinct_and_all_different_outputs(self):
        evidence = self.check()
        self.assertEqual(evidence['actualWorkflows'], 24); self.assertEqual(evidence['primaryOutputsPreserved'], 24)
        self.assertEqual(evidence['durableShadowReturns'], 12); self.assertEqual(len(self.native_calls), 12)
        self.assertEqual(evidence['actualTwoSlotPrimaryWitnesses'], 12)
        self.assertGreater(evidence['nativePrimaryHttpOverlapMs']['min'], 0)
        self.assertEqual(evidence['allFourPrimaryOutputsEqualCases'], 0)
        self.assertEqual(evidence['differentOutputIndices'], list(range(6)))
        self.assertTrue(evidence['differentOutputsRetained']); self.assertFalse(evidence['causalOverheadEstablished'])
        self.assertEqual(len(evidence['cases']), 6)

    def test_primary_completion_order_can_differ_from_the_prescribed_creation_order(self):
        evidence = self.check(self.mutate_rows('primary-http.jsonl', lambda rows: rows.reverse()))
        self.assertEqual(evidence['actualWorkflows'], 24)

    def test_duplicate_native_replica_fails_after_rehash(self):
        with self.assertRaises(ValueError):
            self.check(self.mutate_rows('decision-http.jsonl', lambda rows: rows.append(copy.deepcopy(rows[0]))))

    def test_primary_rebound_to_other_replica_fails_after_rehash(self):
        with self.assertRaises(ValueError):
            self.check(self.mutate_rows('primary-http.jsonl', lambda rows: rows[0].__setitem__('replica', 1-rows[0]['replica'])))

    def test_route_period_boolean_is_not_a_valid_numeric_period(self):
        data = self.mutate_rows('workflow-routes.jsonl', lambda rows: rows[2].__setitem__('period', True))
        driver = copy.deepcopy(self.driver); driver['routes'] = diagnostic.journal(data['workflow-routes.jsonl'])
        with self.assertRaises(ValueError): self.check(data, driver=driver)

    def test_completion_metadata_cannot_cross_replicas(self):
        def change(rows):
            row = next(r for r in rows if r['path'].endswith('/complete')); row['replica'] = 1-row['replica']
        with self.assertRaises(ValueError): self.check(self.mutate_rows('coordinator-http.jsonl', change))

    def test_repeated_primary_witness_period_fails(self):
        def change(rows): rows[1]['period'] = rows[0]['period']
        with self.assertRaises(ValueError): self.check(self.mutate_rows('paired-primary-witnesses.jsonl', change))

    def test_admission_peer_cannot_belong_to_a_previous_replica(self):
        def change(rows):
            row = next(r for r in rows if r['pendingPeers']); row['pendingPeers'][0]['replica'] = 1-row['pendingPeers'][0]['replica']
        with self.assertRaises(ValueError): self.check(self.mutate_rows('admission-metrics.jsonl', change))

    def test_prospective_raw_bytes_are_bound_to_the_recipe(self):
        data = dict(self.artifacts); data['replication-design.json'] += b' '
        with self.assertRaises(ValueError): self.check(data)

    def test_case_contrast_equals_all_replica_differences_with_same_denominator(self):
        evidence = self.check(); values = [r['meanShadowMinusControlMs']['workflowObservedMs'] for r in evidence['cases']]
        self.assertAlmostEqual(sum(values)/6, evidence['matchedWorkflowDeltaMs']['mean'], places=3)
        for case in evidence['cases']:
            delta = case['meanShadowMinusControlMs']
            self.assertLessEqual(abs(delta['workflowObservedMs']-sum(v for k, v in delta.items() if k != 'workflowObservedMs')), .005)

    def parent_mutation(self, mutate):
        data = dict(self.artifacts); parent = json.loads(data['replication-design.json']); mutate(parent)
        parent = sealed({k: v for k, v in parent.items() if k != 'sha256'})
        data['replication-design.json'] = shared.encoded(parent); recipe = copy.deepcopy(self.recipe)
        recipe['replicationDesignFileSha256'] = hashlib.sha256(data['replication-design.json']).hexdigest()
        return data, recipe

    def test_resealed_prospective_order_cannot_change_after_execution(self):
        data, recipe = self.parent_mutation(lambda parent: parent['schedule']['batches'].reverse())
        with self.assertRaises(ValueError): self.check(data, recipe=recipe)

    def test_resealed_design_cannot_claim_measured_outcomes(self):
        data, recipe = self.parent_mutation(lambda parent: parent.__setitem__('modelCalls', 196))
        with self.assertRaises(ValueError): self.check(data, recipe=recipe)

    def test_rebound_census_version_fails_after_body_journal_rehash(self):
        with self.assertRaises(ValueError):
            self.check(self.mutate_rows('cohort-http.jsonl', lambda rows: rows[0].__setitem__('path', rows[0]['path'].replace('processVersion=1', 'processVersion=2'))))

    def test_native_primary_with_no_actual_overlap_fails(self):
        def change(rows):
            first = rows[0]; second = next(r for r in rows if r['pair'] == first['pair'] and r['period'] == first['period'] and r['index'] != first['index'])
            second['nativeStartedAt'] = first['completedAt']
        with self.assertRaises(ValueError): self.check(self.mutate_rows('primary-http.jsonl', change))


class ProspectiveDesignBindingTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve(); self.directory = self.root/'docs/private/run'; self.directory.mkdir(parents=True)
        self.path = self.directory/'replication-design.json'
        self.parent = {'createdAt': '2026-10-10T00:00:00+00:00', 'context': {'fixture': True}}
        self.raw = shared.encoded(self.parent); self.path.write_bytes(self.raw)
        self.pin = hashlib.sha256(self.raw).hexdigest()

    def test_prepare_verifies_and_copies_exact_prospective_bytes_before_owned_startup(self):
        args = SimpleNamespace(replication_design=self.path, replication_design_file_sha256=self.pin,
            context_profile=self.directory/'context.json', context_profile_file_sha256='b'*64)
        with patch.object(design, 'verify_plan', return_value={'status': 'pass'}) as verifier:
            value = diagnostic.prepare_design(self.root, args, {'fixture': True})
        self.assertEqual(value['artifacts']['replication-design.json'], self.raw)
        self.assertEqual(value['plan']['replicationDesignFileSha256'], self.pin)
        verifier.assert_called_once_with(self.root, self.path, self.pin, args.context_profile, 'b'*64)

    def test_nonprivate_design_is_rejected_before_source_or_model_access(self):
        args = SimpleNamespace(replication_design=self.root/'public.json')
        with patch.object(design, 'verify_plan') as verifier:
            with self.assertRaises(ValueError): diagnostic.prepare_design(self.root, args, {'fixture': True})
        verifier.assert_not_called()

    def test_full_source_bound_design_failure_cannot_be_bypassed_by_native_startup(self):
        args = SimpleNamespace(replication_design=self.path, replication_design_file_sha256=self.pin,
            context_profile=self.directory/'context.json', context_profile_file_sha256='b'*64)
        with patch.object(design, 'verify_plan', side_effect=ValueError('changed historical source')):
            with self.assertRaises(ValueError): diagnostic.prepare_design(self.root, args, {'fixture': True})

    def test_design_timestamp_after_native_plan_is_rejected_after_source_replay(self):
        plan = {'replicationDesignFileSha256': self.pin, 'createdAt': '2026-10-09T23:59:59+00:00'}
        with patch.object(design, 'verify_plan', return_value={'status': 'pass'}):
            with self.assertRaises(ValueError): diagnostic.verify_design(self.root, self.directory, plan, self.directory/'context.json', 'b'*64)


if __name__ == '__main__': unittest.main()
