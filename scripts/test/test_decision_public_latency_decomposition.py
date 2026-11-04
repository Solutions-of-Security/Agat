"""Clock accounting, full denominator and model-free replay regressions."""
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from decision_runtime.artifacts import sealed
from scripts.lib import decision_public_latency_decomposition as diagnostic
from scripts.test import test_decision_public_paired_real_primary as paired_fixture
from scripts.test import test_decision_public_real_primary as shared


def encoded(value): return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))+'\n').encode()
def digest(raw): return hashlib.sha256(raw).hexdigest()
BASE = datetime(2026, 10, 10, tzinfo=timezone.utc)
def moment(ms): return (BASE+timedelta(milliseconds=ms)).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def phase_fixture():
    native = {'model': 'qwen3:8b', 'done': True, 'done_reason': 'length', 'message': {'role': 'assistant', 'content': 'fixture'},
        'prompt_eval_count': 100, 'eval_count': 128, 'total_duration': 9_000_000, 'load_duration': 0, 'prompt_eval_duration': 3_000_000, 'eval_duration': 6_000_000}
    raw = encoded(native).decode()
    route = {'startedAt': moment(0), 'completedAt': moment(20), 'elapsedMs': 20}
    primary = {'startedAt': moment(2), 'nativeStartedAt': moment(3), 'completedAt': moment(12), 'elapsedMs': 10,
        'nativeResponseBody': raw, 'nativeResponseBodySha256': digest(raw.encode()), 'outputSha256': digest(b'fixture')}
    body = encoded({'status': 'ok'}).decode()
    decision = {'startedAt': moment(14), 'completedAt': moment(18), 'elapsedMs': 4, 'httpStatus': 200,
        'responseBody': body, 'responseBodySha256': digest(body.encode())}
    return route, primary, decision


class PhaseClockTest(unittest.TestCase):
    def test_primary_only_phases_and_mixed_clock_residual_close(self):
        r, p, _ = phase_fixture(); v = diagnostic.phases(r, p)
        self.assertEqual((v['prePrimaryWallMs'], v['primaryProxyMonotonicMs'], v['postPrimaryWallMs']), (2, 10, 8))
        self.assertEqual(v['clockClosureResidualMs'], 0); self.assertNotIn('shadow', v)
        p['elapsedMs'] = 9.75; self.assertEqual(diagnostic.phases(r, p)['clockClosureResidualMs'], .25)

    def test_shadow_tail_closes_and_records_local_caller_with_distinct_clock(self):
        r, p, d = phase_fixture(); v = diagnostic.phases(r, p, d, 4.5)['shadow']
        self.assertEqual((v['preDecisionWallMs'], v['decisionRelayWallMs'], v['postDecisionWallMs']), (2, 4, 2))
        self.assertEqual(v['callerMinusRelayMs'], .5); self.assertEqual(v['callerMonotonicMs'], 4.5)

    def test_negative_phases_are_rejected_without_clipping(self):
        for target, key, time in (('primary', 'startedAt', -1), ('primary', 'nativeStartedAt', 1),
            ('route', 'completedAt', 11), ('decision', 'startedAt', 11), ('decision', 'completedAt', 21)):
            r, p, d = phase_fixture(); {'route': r, 'primary': p, 'decision': d}[target][key] = moment(time)
            with self.assertRaises(ValueError): diagnostic.phases(r, p, d, 4)

    def test_clock_drift_nonfinite_duration_or_native_response_rehash_fails(self):
        for value in (30, float('nan'), float('inf'), True):
            r, p, _ = phase_fixture(); p['elapsedMs'] = value
            with self.assertRaises(ValueError): diagnostic.phases(r, p)
        r, p, _ = phase_fixture(); p['nativeResponseBody'] += ' '
        with self.assertRaises(ValueError): diagnostic.phases(r, p)

    def test_caller_and_relay_duration_cannot_escape_recorded_tail(self):
        r, p, d = phase_fixture()
        for caller in (None, 300, 40):
            with self.assertRaises(ValueError): diagnostic.phases(r, p, d, caller)
        d['elapsedMs'] = 20
        with self.assertRaises(ValueError): diagnostic.phases(r, p, d, 20)
        with self.assertRaises(ValueError): diagnostic.phases(r, p, caller_ms=1)

    def test_reported_model_subtimings_do_not_invent_queue_or_gpu_cost(self):
        r, p, _ = phase_fixture(); native = json.loads(p['nativeResponseBody'])
        native['load_duration'] = 20_000_000
        p['nativeResponseBody'] = encoded(native).decode(); p['nativeResponseBodySha256'] = digest(p['nativeResponseBody'].encode())
        v = diagnostic.phases(r, p); self.assertEqual(v['primaryReportedTimingMs']['load_duration'], 20)
        self.assertEqual(v['primaryProxyMonotonicMs'], 10); self.assertNotIn('gpuMs', v); self.assertNotIn('queueMs', v)

    def test_signed_stats_keep_negative_deltas_and_empty_shadow_is_unknown(self):
        v = diagnostic.stats([-10, -2, 1]); self.assertEqual(v['median'], -2); self.assertEqual(v['min'], -10)
        self.assertEqual(diagnostic.stats([])['count'], 0); self.assertIsNone(diagnostic.stats([])['mean'])

    def test_private_evidence_paths_cannot_escape_or_use_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); (root/'docs/private').mkdir(parents=True)
            (root/'docs/private/link').symlink_to(root/'docs/private')
            for name in ('../outside', '/tmp/evidence', 'docs/public', 'docs/private/link'):
                with self.assertRaises(ValueError): diagnostic.private_path(root, name)
            self.assertEqual(diagnostic.private_path(root, 'docs/private/evidence'), root/'docs/private/evidence')


class ReplayAccountingTest(unittest.TestCase):
    def setUp(self):
        self.inputs = {'fixture': True}; self.evidence = {'newModelCalls': 0, 'causalOverheadEstablished': False, 'cases': [1, 2]}
        self.receipts = {k: sealed({'schemaVersion': 'fixture.replay.v1', 'verifiedAt': moment(0), 'status': 'pass', 'modelCallsDuringVerification': 0})
            for k in ('paired', 'repeatControl')}
        with patch.object(diagnostic, 'sources_at', return_value={'fixture': b'bytes'}), patch.object(diagnostic, 'analyze_receipts', return_value=(self.evidence, self.receipts)):
            self.analysis = diagnostic.create_analysis(Path('/fixture'), self.inputs, 'a'*40, {'fixture': 'b'*64})

    def replay(self, mutate=None, receipts=None, raw_pin=None):
        value = copy.deepcopy(self.analysis)
        if mutate: mutate(value)
        value = sealed({k: v for k, v in value.items() if k != 'sha256'}); raw = encoded(value)
        with tempfile.TemporaryDirectory() as temporary:
            p = Path(temporary)/'analysis.json'; p.write_bytes(raw)
            with patch.object(diagnostic, 'sources_at', return_value={'fixture': b'bytes'}), patch.object(diagnostic, 'analyze_receipts', return_value=(self.evidence, receipts or self.receipts)):
                return diagnostic.verify_analysis(Path(temporary), p, raw_pin or digest(raw))

    def test_replay_recalculates_receipts_and_forbids_model_process_or_network(self):
        with patch('socket.create_connection', side_effect=AssertionError('Network')), patch('subprocess.Popen', side_effect=AssertionError('Process')):
            v = self.replay()
        self.assertEqual(v['status'], 'pass'); self.assertEqual(v['modelCallsDuringVerification'], 0)
        self.assertFalse(v['evidence']['causalOverheadEstablished'])

    def test_evidence_changed_after_resealing_cannot_pass(self):
        with self.assertRaises(ValueError): self.replay(lambda v: v['evidence'].update(cases=[1]))

    def test_posthoc_clock_protocol_or_causal_qualification_fails(self):
        for mutate in (lambda v: v['protocol'].update(clockToleranceMs=1000), lambda v: v.update(sloAccepted=True),
            lambda v: v.update(newModelCalls=1), lambda v: v.update(referenceLabels=1), lambda v: v.update(qualification='qualified')):
            with self.assertRaises(ValueError): self.replay(mutate)

    def test_replay_timestamp_can_change_but_parent_receipt_evidence_cannot(self):
        changed = {k: sealed({**{f: x for f, x in v.items() if f != 'sha256'}, 'verifiedAt': moment(2)}) for k, v in self.receipts.items()}
        self.assertEqual(self.replay(receipts=changed)['status'], 'pass')
        with self.assertRaises(ValueError): self.replay(lambda v: v['datasetReplays']['paired'].update(status='failed'))

    def test_wrong_raw_pin_unsealed_parent_or_extra_schema_field_fails(self):
        with self.assertRaises(ValueError): self.replay(raw_pin='0'*64)
        with self.assertRaises(ValueError): self.replay(lambda v: v['datasetReplays']['paired'].update(sha256='0'*64))
        with self.assertRaises(ValueError): self.replay(lambda v: v.update(extra=True))

    def test_unknown_creation_time_or_extra_parent_replay_rejected(self):
        for mutate in (lambda v: v.update(createdAt='unknown'), lambda v: v.update(createdAt=moment(-1)),
            lambda v: v['datasetReplays'].update(extra=self.receipts['paired'])):
            with self.assertRaises(ValueError): self.replay(mutate)


class VariableRepeatFixture:
    @classmethod
    def setUpClass(cls): shared.MatchedWorkflowTest.setUpClass.__func__(cls, suite=diagnostic.repeat, primary_variation=True)


@unittest.skipUnless(shutil.which('node') and (shared.ROOT/'node_modules/tsx').exists(), 'Requires Node fixture dependencies')
class FullInventoryAccountingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        paired_fixture.PairedWorkflowTest.setUpClass(); VariableRepeatFixture.setUpClass()
        cls.paired = paired_fixture.PairedWorkflowTest; cls.repeat = VariableRepeatFixture

    def compute(self):
        p, r = self.paired, self.repeat
        return diagnostic.combine(p.context, p.recipe, p.driver, p.artifacts, r.recipe, r.driver, r.artifacts)

    def test_actual_fixture_inventory_unequal_primary_outputs_are_all_retained(self):
        v = self.compute(); self.assertEqual(v['paired']['originalCases'], 6); self.assertEqual(v['repeatControl']['originalCases'], 6)
        self.assertEqual(v['repeatControl']['matchedOutputPairs'], 0); self.assertEqual(v['repeatControl']['differentOutputIndices'], list(range(6)))
        self.assertEqual(len(v['repeatControl']['cases']), 6); self.assertEqual(v['repeatControl']['accountedWorkflows'], 12)
        self.assertTrue(v['primaryOnlyOutputVariationObserved']); self.assertFalse(v['wholeWorkflowDeltaIsShadowCostEstimate'])
        self.assertFalse(v['causalOverheadEstablished']); self.assertEqual(len(self.repeat.native_calls), 0)

    def test_every_matched_delta_preserves_phase_sum_and_native_outcome(self):
        v = self.compute()
        for group in ('paired', 'repeatControl'):
            for row in v[group]['cases']: self.assertLessEqual(abs(row['deltaClosureResidualMs']), .005)
        self.assertEqual(sum(v['paired']['shadowOutcomes'].values()), 6); self.assertEqual(v['repeatControl']['nativeCaseCalls'], 0)

    def test_analysis_of_actual_receipts_uses_no_model_or_network(self):
        with patch('socket.create_connection', side_effect=AssertionError('Network')), patch('subprocess.Popen', side_effect=AssertionError('Process')):
            self.assertEqual(self.compute()['newModelCalls'], 0)

    def test_missing_original_trace_and_changed_source_generation_fail(self):
        r = self.repeat; artifacts = copy.deepcopy(r.artifacts); artifacts.pop(r.driver['routes'][0]['traceFile'])
        with self.assertRaises(KeyError): diagnostic.decompose(r.context, r.recipe, r.driver, artifacts, diagnostic.repeat)
        recipe = copy.deepcopy(r.recipe); recipe['protocol']['primarySeed'] = 1
        with self.assertRaises(ValueError): diagnostic.decompose(r.context, recipe, r.driver, r.artifacts, diagnostic.repeat)


if __name__ == '__main__': unittest.main()
