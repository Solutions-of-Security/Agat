"""Clock intervals, forged elapsed values, legacy compatibility and the actual Node sampler."""
import copy
import json
from pathlib import Path
import subprocess
import unittest

from scripts.lib.decision_workflow_batch_clock import BATCH_CLOCK_SCHEMA, verify_batch_clock
from scripts.lib import decision_public_workflow as workflow
from scripts.test import test_decision_public_workflow_paired as paired_fixture

ROOT = Path(__file__).resolve().parents[2]


def batch():
    return {'schemaVersion': BATCH_CLOCK_SCHEMA, 'index': 0, 'inputIndices': [0, 1], 'runIds': ['a', 'b'],
            'startedAt': '2026-10-11T00:00:00.000Z', 'completedAt': '2026-10-11T00:00:00.200Z', 'elapsedMs': 150.0,
            'clockSamples': {
                'start': {'wallAt': '2026-10-11T00:00:00.000Z', 'monotonicBeforeMs': 100, 'monotonicAfterMs': 100},
                'end': {'wallAt': '2026-10-11T00:00:00.200Z', 'monotonicBeforeMs': 250, 'monotonicAfterMs': 300}}}


class BatchClockTest(unittest.TestCase):
    def test_recorded_sampling_delay_bounds_wall_difference_without_changing_elapsed(self):
        end, result = verify_batch_clock(batch())
        self.assertEqual(end, 300)
        self.assertEqual(result, {'samplingMs': [0, 50], 'wallDurationMs': 200.0,
                                  'monotonicLowerMs': 150, 'monotonicUpperMs': 200})

    def test_forged_elapsed_rejected_even_when_wall_duration_fits_sampling_interval(self):
        value = batch(); value['elapsedMs'] = 200.0
        with self.assertRaisesRegex(ValueError, 'elapsed time differs'):
            verify_batch_clock(value)

    def test_unrecorded_clock_difference_is_rejected(self):
        value = batch(); value['clockSamples']['end']['monotonicAfterMs'] = 251
        with self.assertRaisesRegex(ValueError, 'correlation is unverified'):
            verify_batch_clock(value)

    def test_monotonic_deadline_includes_sampling_and_cannot_use_a_shorter_wall_clock(self):
        value = batch(); value['clockSamples']['end']['monotonicAfterMs'] = 20101
        with self.assertRaisesRegex(ValueError, 'completion deadline'):
            verify_batch_clock(value)

    def test_rounding_is_limited_to_half_a_microsecond(self):
        value = batch(); value['clockSamples']['end']['monotonicBeforeMs'] = 250.00049
        verify_batch_clock(value)
        value['clockSamples']['end']['monotonicBeforeMs'] = 250.0006
        with self.assertRaisesRegex(ValueError, 'elapsed time differs'):
            verify_batch_clock(value)

    def test_unknown_missing_scalar_or_reversed_samples_rejected(self):
        for change in ('schema', 'missing', 'extra', 'wall', 'bool', 'nan', 'negative', 'sample_backwards', 'work_backwards'):
            value = batch(); end = value['clockSamples']['end']
            if change == 'schema': value['schemaVersion'] = 'unknown'
            elif change == 'missing': del end['monotonicAfterMs']
            elif change == 'extra': end['unverifiedAllowanceMs'] = 1000
            elif change == 'wall': end['wallAt'] = value['startedAt']
            elif change == 'bool': end['monotonicBeforeMs'] = True
            elif change == 'nan': end['monotonicBeforeMs'] = float('nan')
            elif change == 'negative': end['monotonicBeforeMs'] = -1
            elif change == 'sample_backwards': end['monotonicAfterMs'] = 249
            else: end['monotonicBeforeMs'] = 99
            with self.subTest(change=change), self.assertRaises(ValueError): verify_batch_clock(value)

    def test_previous_batch_barrier_uses_the_last_monotonic_sample(self):
        verify_batch_clock(batch(), 100)
        with self.assertRaisesRegex(ValueError, 'overlap'):
            verify_batch_clock(batch(), 100.001)

    def test_actual_node_sampler_retains_a_delay_inside_the_wall_read(self):
        script = '''
import {sampleBatchClock,batchElapsedMs,BATCH_CLOCK_SCHEMA} from './scripts/lib/decision_workflow_batch_clock.mts';
const start=sampleBatchClock();
const end=sampleBatchClock(undefined,()=>{
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)),0,0,40);
  return Date.now();
});
console.log(JSON.stringify({schemaVersion:BATCH_CLOCK_SCHEMA,index:0,inputIndices:[0,1],runIds:['a','b'],
  startedAt:start.wallAt,completedAt:end.wallAt,elapsedMs:batchElapsedMs(start,end),clockSamples:{start,end}}));
'''
        run = subprocess.run(['node', '--import', 'tsx', '--input-type=module', '-e', script], cwd=ROOT,
                             capture_output=True, text=True, timeout=10, check=True)
        value = json.loads(run.stdout); _, result = verify_batch_clock(value)
        self.assertGreaterEqual(result['samplingMs'][1], 30)
        self.assertGreater(result['wallDurationMs']-value['elapsedMs'], 20)

    def test_paired_inventory_accepts_recorded_brackets_and_keeps_legacy_output(self):
        context, recipe, cohort, routes, transport, bundle, _ = paired_fixture.fixture()
        legacy = workflow.verify_inventory(context, recipe, cohort, routes, transport, bundle)
        self.assertNotIn('batchClockSchemaVersion', legacy)
        self.assertNotIn('batchClockSamplesVerified', legacy)
        for index, row in enumerate(bundle['batches']):
            start = index*250+10
            row.update(schemaVersion=BATCH_CLOCK_SCHEMA, elapsedMs=150.0, clockSamples={
                'start': {'wallAt': row['startedAt'], 'monotonicBeforeMs': start, 'monotonicAfterMs': start},
                'end': {'wallAt': row['completedAt'], 'monotonicBeforeMs': start+150, 'monotonicAfterMs': start+200}})
        value = workflow.verify_inventory(context, recipe, cohort, routes, transport, bundle)
        self.assertEqual(value['batchClockSchemaVersion'], BATCH_CLOCK_SCHEMA)
        self.assertTrue(value['batchClockSamplesVerified'])
        self.assertEqual(value['maximumClockSamplingMs'], 50)
        self.assertEqual(value['scheduled'], legacy['scheduled'])
        self.assertEqual(value['nativeBusyRefusals'], legacy['nativeBusyRefusals'])

    def test_legacy_discrepancy_is_not_reclassified_as_valid_new_evidence(self):
        context, recipe, cohort, routes, transport, bundle, _ = paired_fixture.fixture()
        bundle['batches'][0]['elapsedMs'] = 462.513
        with self.assertRaisesRegex(ValueError, 'Pair crossed completion barrier'):
            workflow.verify_inventory(context, recipe, cohort, routes, transport, bundle)

    def test_mixed_batch_versions_and_reversed_monotonic_pairs_rejected(self):
        context, recipe, cohort, routes, transport, bundle, _ = paired_fixture.fixture()
        for index, row in enumerate(bundle['batches']):
            start = index*250+10
            row.update(schemaVersion=BATCH_CLOCK_SCHEMA, clockSamples={
                'start': {'wallAt': row['startedAt'], 'monotonicBeforeMs': start, 'monotonicAfterMs': start},
                'end': {'wallAt': row['completedAt'], 'monotonicBeforeMs': start+200, 'monotonicAfterMs': start+200}})
        broken = copy.deepcopy(bundle); del broken['batches'][0]['schemaVersion']
        with self.assertRaisesRegex(ValueError, 'Mixed or unknown'):
            workflow.verify_inventory(context, recipe, cohort, routes, transport, broken)
        second = bundle['batches'][1]; second['clockSamples']['start']['monotonicBeforeMs'] = 209
        with self.assertRaisesRegex(ValueError, 'overlap'):
            workflow.verify_inventory(context, recipe, cohort, routes, transport, bundle)


if __name__ == '__main__': unittest.main()
