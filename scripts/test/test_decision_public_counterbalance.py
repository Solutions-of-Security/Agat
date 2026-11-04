"""Replication identity, drift balance and prospective source/seal regressions."""
from collections import Counter
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from decision_runtime.artifacts import sealed
from scripts.lib import decision_public_counterbalance as design


def context(count=49):
    return {'sourceCommit': 'a'*40, 'sourceFiles': {'context.py': 'b'*64},
        'inputs': [{'groupId': str(min(i, 43))} for i in range(count)],
        'contextEligibleCases': max(0, count-3), 'contextTooLongCases': min(3, count)}


class CounterbalancedScheduleTest(unittest.TestCase):
    def test_full_odd_inventory_has_196_distinct_routes_and_100_batches(self):
        value = design.schedule(49, 'b'*64); e = design.evidence(context(), value)
        self.assertEqual(e['plannedWorkflows'], 196); self.assertEqual(e['plannedBatches'], 100)
        self.assertEqual(e['plannedDecisionScoringCalls'], 98); self.assertEqual(e['originalGroups'], 44)
        self.assertEqual((e['plannedTwoInputBatches'], e['plannedSingletonBatches']), (96, 4))
        self.assertEqual(e['completeBlockOrientations'], {'ABBA': 12, 'BAAB': 12})
        self.assertEqual(Counter(r['index'] for r in value['routes']), {i: 4 for i in range(49)})
        self.assertEqual([b['indices'] for b in value['blocks']], [list(range(i, min(i+2, 49))) for i in range(0, 49, 2)])
        self.assertFalse(e['actualTwoSlotOverlapEstablished']); self.assertFalse(e['causalOverheadEstablished'])

    def test_each_condition_has_two_distinct_replica_identities_for_each_input(self):
        rows = design.schedule(49, 'b'*64)['routes']
        for index in range(49):
            self.assertEqual({(r['condition'], r['replica']) for r in rows if r['index'] == index},
                {('control', 0), ('control', 1), ('shadow', 0), ('shadow', 1)})
        self.assertEqual(len({r['routeKey'] for r in rows}), 196)

    def test_order_is_reproducible_and_context_seed_is_domain_separated(self):
        a = design.schedule(49, 'b'*64); self.assertEqual(a, design.schedule(49, 'b'*64))
        self.assertNotEqual(a['orderSeedSha256'], design.schedule(49, 'c'*64)['orderSeedSha256'])
        expected = hashlib.sha256((design.SEED_DOMAIN+'\0'+'b'*64).encode()).hexdigest()
        self.assertEqual(a['orderSeedSha256'], expected)

    def test_complete_block_balance_and_denominator_hold_for_every_supported_size(self):
        for count in range(1, 61):
            for pin in ('0'*64, 'f'*64):
                with self.subTest(count=count, pin=pin):
                    value = design.schedule(count, pin); e = design.evidence(context(count), value)
                    self.assertEqual(e['plannedWorkflows'], 4*count)
                    self.assertEqual(e['uniqueRouteKeys'], 4*count)
                    self.assertEqual(len(value['blocks']), (count+1)//2)

    def test_case_contrast_recovers_treatment_under_arbitrary_linear_period_trend(self):
        for block in design.schedule(49, 'b'*64)['blocks']:
            for trend in (-500, 0, 17.5, 999):
                values = [(condition, 5000+trend*period+(31.25 if condition == 'shadow' else 0))
                    for period, condition in enumerate(block['conditions'])]
                difference = sum(v for c, v in values if c == 'shadow')/2-sum(v for c, v in values if c == 'control')/2
                self.assertEqual(difference, 31.25)

    def test_nonlinear_period_drift_is_not_removed_by_case_contrast(self):
        biases = set()
        for block in design.schedule(49, 'b'*64)['blocks']:
            values = [(c, p*p) for p, c in enumerate(block['conditions'])]
            biases.add(sum(v for c, v in values if c == 'shadow')/2-sum(v for c, v in values if c == 'control')/2)
        self.assertEqual(biases, {-2, 2})
        self.assertFalse(design.evidence(context(), design.schedule(49, 'b'*64))['nonlinearDriftRemoved'])

    def test_unequal_elapsed_time_spacing_does_not_balance_wall_time_drift(self):
        times = [0, 1, 3, 10]; biases = set()
        for block in design.schedule(49, 'b'*64)['blocks']:
            values = [(c, times[p]) for p, c in enumerate(block['conditions'])]
            biases.add(sum(v for c, v in values if c == 'shadow')/2-sum(v for c, v in values if c == 'control')/2)
        self.assertEqual(biases, {-3, 3})
        self.assertFalse(design.evidence(context(), design.schedule(49, 'b'*64))['elapsedWallTimeTrendRemoved'])

    def test_invalid_size_and_context_pins_fail_without_clipping(self):
        for count in (0, 61, -1, True, 49.0):
            with self.subTest(count=count), self.assertRaises(ValueError): design.schedule(count, 'b'*64)
        for pin in ('b'*63, 'B'*64, None, '../x'):
            with self.subTest(pin=pin), self.assertRaises(ValueError): design.schedule(49, pin)


class CounterbalancedPlanReplayTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(); self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name); self.path = self.root/'plan.json'
        self.context = context(); self.sources = {'planner.py': 'c'*64}
        self.patches = [patch.object(design, 'context_file', return_value=self.context),
            patch.object(design, 'sources_at', return_value={'planner.py': b'committed fixture'}),
            patch('socket.create_connection', side_effect=AssertionError('Prospective design must not connect'))]
        for item in self.patches: item.start(); self.addCleanup(item.stop)
        self.plan = design.create_plan(self.root, self.root/'context.json', 'b'*64, 'd'*40, self.sources)

    def verify(self, mutate=None, reseal=True):
        value = copy.deepcopy(self.plan)
        if mutate: mutate(value)
        if reseal: value = sealed({k: v for k, v in value.items() if k != 'sha256'})
        raw = (json.dumps(value, sort_keys=True)+'\n').encode(); self.path.write_bytes(raw)
        return design.verify_plan(self.root, self.path, hashlib.sha256(raw).hexdigest(), self.root/'context.json', 'b'*64)

    def test_prospective_replay_reports_planned_counts_and_zero_measured_calls(self):
        receipt = self.verify(); self.assertEqual(receipt['status'], 'pass')
        self.assertEqual(receipt['evidence']['plannedWorkflows'], 196)
        self.assertEqual(receipt['modelCallsDuringVerification'], 0); self.assertEqual(receipt['measuredWorkflows'], 0)
        self.assertFalse(receipt['causalOverheadEstablished'])

    def test_changed_order_fails_even_after_valid_reseal(self):
        with self.assertRaises(ValueError): self.verify(lambda v: v['schedule']['batches'].reverse())

    def test_duplicate_replica_identity_fails_even_after_valid_reseal(self):
        with self.assertRaises(ValueError): self.verify(lambda v: v['schedule']['routes'].__setitem__(1, v['schedule']['routes'][0]))

    def test_missing_original_case_fails_even_after_valid_reseal(self):
        with self.assertRaises(ValueError): self.verify(lambda v: v['schedule']['routes'].pop())

    def test_rebound_context_file_fails_after_valid_reseal(self):
        with self.assertRaises(ValueError): self.verify(lambda v: v.__setitem__('contextProfileFileSha256', 'f'*64))

    def test_changed_primary_generation_and_cache_policy_fail_after_reseal(self):
        for section, key, value in (('generation', 'think', True), ('settings', 'OLLAMA_NUM_PARALLEL', '1'),
                ('protocol', 'primaryCachePolicy', 'reset_after_each_period')):
            with self.subTest(section=section), self.assertRaises(ValueError): self.verify(lambda v: v[section].__setitem__(key, value))

    def test_measured_outcome_or_authority_cannot_be_granted_by_a_design(self):
        for key, value in (('status', 'observed'), ('modelCalls', 196), ('measuredWorkflows', 196),
                ('ownersAppointed', True), ('referenceLabels', 49), ('causalOverheadEstablished', True), ('routingEnabled', True)):
            with self.subTest(key=key), self.assertRaises(ValueError): self.verify(lambda v: v.__setitem__(key, value))

    def test_extra_schema_field_and_broken_seal_fail(self):
        with self.assertRaises(ValueError): self.verify(lambda v: v.__setitem__('chosenAfterResults', True))
        with self.assertRaises(ValueError): self.verify(lambda v: v.__setitem__('status', 'observed'), reseal=False)

    def test_missing_or_mismatched_historical_sources_fail_closed(self):
        with patch.object(design, 'sources_at', side_effect=ValueError('historical source missing')):
            with self.assertRaises(ValueError): self.verify()


if __name__ == '__main__': unittest.main()
