"""Development submitted labels stay out of calibration and independent holdout."""
from copy import deepcopy
import contextlib
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from decision_runtime.__main__ import main as runtime_main
from decision_runtime.annotations import finalize_reviews, group_split, prepare_review
from decision_runtime.artifacts import sealed, verify_seal
from decision_runtime.calibration_workflow import freeze_plan, validate_plan
from decision_runtime.contracts import Policy, fingerprint, parse_json
from decision_runtime.engine import DecisionEngine
from decision_runtime.evaluation import evaluate, load_dataset
from decision_runtime.tests.test_calibration import FixtureBackend, criteria, pipeline
from scripts.lib.decision_review_adjudication import encoded
from scripts.lib.decision_review_bundle import prepare_bundle, verify_bundle, write_bundle
from scripts.lib.decision_review_finalization import prepare_finalization, verify_finalization
from scripts.lib.decision_review_pair import compare_pair
from scripts.lib.decision_review_development_scope import for_native_diagnostics, scope_development_reviews
from scripts.lib.decision_review_session import AUTHORITY_FLAGS
from scripts.test import test_decision_review_pair as pair_fixtures

sha = pair_fixtures.hashlib.sha256
file_sha = lambda path: sha(path.read_bytes()).hexdigest()
reseal = lambda value: sealed({key: item for key, item in value.items() if key != 'sha256'})


class DevelopmentReviewScopeTest(unittest.TestCase):
    def setUp(self):
        self.helper = pair_fixtures.ReviewPairTest(); self.helper.setUp(); self.addCleanup(self.helper.doCleanups)
        self.root = self.helper.root; self.seed = 'synthetic-development-scope'
        pool = deepcopy(self.helper.a['pool'])
        groups = [f'synthetic-group-{i}' for i in range(50)
                  if group_split(self.seed, f'synthetic-group-{i}') == 'development'][:3]
        self.assertEqual(len(groups), 3)
        for case, group in zip(pool['cases'], groups, strict=True): case['groupId'] = group
        blank = prepare_review(pool, self.seed)
        self.first = self.helper.binding('first', blank); self.second = self.helper.binding('second', blank)
        self.comparison = self.root/'comparison.json'
        self.comparison.write_bytes(encoded(compare_pair(self.first, self.second)))

    def prepare(self, version=2):
        return prepare_finalization(self.first, self.second, self.comparison, file_sha(self.comparison), version=version)

    def written(self, outputs):
        report, dataset = self.root/'finalization.json', self.root/'dataset.json'
        report.write_bytes(encoded(outputs['finalization.json'])); dataset.write_bytes(encoded(outputs['dataset.json']))
        return report, dataset

    def verify(self, report, dataset):
        return verify_finalization(self.first, self.second, self.comparison, file_sha(self.comparison),
                                   report, file_sha(report), dataset, file_sha(dataset))

    def test_source_bound_finalization_marks_and_seals_development_labels(self):
        output = self.prepare(); data = output['dataset.json']; report = output['finalization.json']
        verify_seal(data, 'agat.decision.dataset.v1')
        self.assertEqual(data['usage'], 'development-only'); self.assertEqual(data['developmentLabelKind'], 'submitted-review')
        self.assertTrue(report['developmentOnly']); self.assertEqual(report['datasetUsage'], data['usage'])
        self.assertEqual(report['schemaVersion'], 'agat.decision.review-finalization.v2')
        self.assertEqual(data['cases'], self.prepare(1)['dataset.json']['cases'])
        self.assertEqual(data['annotation'], self.prepare(1)['dataset.json']['annotation'])
        for flag in (*AUTHORITY_FLAGS, 'qualifiedForRouting', 'classificationAccuracyMeasured'):
            self.assertIs(data[flag], False)
        verified = self.verify(*self.written(output)); self.assertTrue(verified['developmentOnly'])
        self.assertEqual(verified['schemaVersion'], 'agat.decision.review-finalization-verification.v2')

    def test_standalone_copy_validates_for_native_diagnostics_and_keeps_all_cases(self):
        _, path = self.written(self.prepare()); copied = self.root/'standalone.json'; copied.write_bytes(path.read_bytes())
        data = for_native_diagnostics(parse_json(copied.read_bytes())); backend = FixtureBackend()
        report = evaluate(DecisionEngine(backend), data, 'development', reverse_options=True)
        self.assertEqual(backend.calls, 6); self.assertEqual(len(report['cases']), 3)
        self.assertEqual({case['id'] for case in report['cases']}, {case['id'] for case in data['cases']})
        with self.assertRaises(ValueError): load_dataset(copied)

    def test_resealed_split_changes_remain_forbidden(self):
        data = self.prepare()['dataset.json']
        for split in ('calibration', 'holdout', 'train'):
            changed = deepcopy(data); changed['cases'][0]['split'] = split
            with self.subTest(split=split), self.assertRaisesRegex(ValueError, 'Development-only'):
                for_native_diagnostics(reseal(changed))

    def test_standalone_scope_refuses_freezing_and_rebound_experiment_plan(self):
        data = self.prepare()['dataset.json']
        with self.assertRaisesRegex(ValueError, 'Development-only'):
            freeze_plan(data, Policy(), criteria(), FixtureBackend.identity)
        _, plan, *_ = pipeline()
        changed = deepcopy(plan); changed['datasetSha256'] = fingerprint(data)
        with self.assertRaisesRegex(ValueError, 'Development-only'):
            validate_plan(reseal(changed), data)

    def test_runtime_cli_refuses_calibration_holdout_before_loading_weights(self):
        _, data = self.written(self.prepare())
        for split in ('calibration', 'holdout'):
            args = ['decision_runtime', 'evaluate', '--manifest', str(self.root/'absent-weights.json'),
                    '--dataset', str(data), '--split', split, '--output', str(self.root/(split+'.json'))]
            with self.subTest(split=split), patch.object(sys, 'argv', args), \
                 patch('decision_runtime.mlx_backend.MlxBackend') as backend, \
                 contextlib.redirect_stderr(io.StringIO()) as error:
                self.assertEqual(runtime_main(), 1)
            backend.assert_not_called(); self.assertTrue(error.getvalue())
            self.assertFalse((self.root/(split+'.json')).exists())
            self.assertIn('Development-only', error.getvalue())

    def test_scoping_is_idempotent_without_a_nested_checksum(self):
        data = self.prepare()['dataset.json']; self.assertEqual(scope_development_reviews(data), data)
        verify_seal(scope_development_reviews(data), 'agat.decision.dataset.v1')

    def test_missing_records_or_resealed_authority_are_not_development_review_data(self):
        original = self.prepare()['dataset.json']
        mutations = [lambda d: d['cases'][0].update(review={}),
                     lambda d: d['cases'][0]['review']['records'].pop(),
                     lambda d: d['cases'][0]['review']['records'][0].update(inputSha256='0'*64),
                     lambda d: d['cases'][0]['review']['records'][1].update(reviewerId=d['cases'][0]['review']['records'][0]['reviewerId']),
                     lambda d: d.update(developmentLabelKind='unknown'),
                     lambda d: d.update(developmentLabelKind='assistant-reviewed'),
                     lambda d: d.pop('annotation')]
        mutations += [lambda d, key=key: d.update({key: True}) for key in
                      (*AUTHORITY_FLAGS, 'qualifiedForRouting', 'classificationAccuracyMeasured')]
        mutations += [lambda d: d.update(humanExecutionVerified=0), lambda d: d.update(qualification='pass')]
        for mutate in mutations:
            changed = deepcopy(original); mutate(changed)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError): for_native_diagnostics(reseal(changed))

    def test_v2_receipt_scope_and_dataset_cannot_be_removed_with_new_pins(self):
        output = self.prepare(); report, dataset = self.written(output)
        changed = deepcopy(output['dataset.json']); changed.pop('usage')
        dataset.write_bytes(encoded(reseal(changed)))
        with self.assertRaisesRegex(ValueError, 'differs'): self.verify(report, dataset)
        report, dataset = self.written(output)
        changed = deepcopy(output['finalization.json']); changed.update(developmentOnly=False, datasetUsage='group-split-review')
        report.write_bytes(encoded(reseal(changed)))
        with self.assertRaisesRegex(ValueError, 'differs'): self.verify(report, dataset)

    def test_unknown_or_changed_finalization_version_cannot_reuse_receipts(self):
        output = self.prepare(); report, dataset = self.written(output)
        for version in ('agat.decision.review-finalization.v1', 'agat.decision.review-finalization.v99'):
            changed = deepcopy(output['finalization.json']); changed['schemaVersion'] = version
            report.write_bytes(encoded(reseal(changed)))
            with self.subTest(version=version), self.assertRaises(ValueError): self.verify(report, dataset)
        for version in (True, 0, 3, '2'):
            with self.subTest(version=version), self.assertRaises(ValueError): self.prepare(version)

    def test_v1_finalization_replay_and_bundle_keep_exact_legacy_bytes(self):
        output = self.prepare(1); report, dataset = self.written(output); raw = dataset.read_bytes()
        original_reviews = [parse_json(binding[4].read_bytes()) for binding in (self.first, self.second)]
        self.assertEqual(raw, encoded(finalize_reviews(*original_reviews)))
        verification = self.verify(report, dataset)
        self.assertEqual(verification['schemaVersion'], 'agat.decision.review-finalization-verification.v1')
        self.assertNotIn('developmentOnly', verification)
        manifest, files = prepare_bundle(self.first, self.second, self.comparison, file_sha(self.comparison),
                                        report, file_sha(report), dataset, file_sha(dataset))
        path = write_bundle(self.root, self.root/'docs/private/legacy-bundle', manifest, files)/'bundle.json'
        self.assertEqual(verify_bundle(path, file_sha(path))['status'], 'pass')
        self.assertEqual((path.parent/'dataset.json').read_bytes(), raw)

    def test_v2_bundle_keeps_the_scoped_standalone_dataset(self):
        report, dataset = self.written(self.prepare())
        manifest, files = prepare_bundle(self.first, self.second, self.comparison, file_sha(self.comparison),
                                        report, file_sha(report), dataset, file_sha(dataset))
        path = write_bundle(self.root, self.root/'docs/private/scoped-bundle', manifest, files)/'bundle.json'
        self.assertEqual(verify_bundle(path, file_sha(path))['status'], 'pass')
        data = parse_json((path.parent/'dataset.json').read_bytes())
        self.assertEqual(data['usage'], 'development-only')
        self.assertEqual(len(for_native_diagnostics(data)['cases']), 3)

    def test_mixed_split_review_keeps_original_calibration_holdout_assignments(self):
        first = self.helper.binding('mixed-first', self.helper.a); second = self.helper.binding('mixed-second', self.helper.b)
        path = self.root/'mixed-comparison.json'; path.write_bytes(encoded(compare_pair(first, second)))
        output = prepare_finalization(first, second, path, file_sha(path))
        self.assertFalse(output['finalization.json']['developmentOnly'])
        self.assertEqual(output['finalization.json']['datasetUsage'], 'group-split-review')
        original = finalize_reviews(*(parse_json(b[4].read_bytes()) for b in (first, second)))
        self.assertEqual(output['dataset.json'], original); self.assertNotIn('usage', original)
        with self.assertRaisesRegex(ValueError, 'Scope only'): scope_development_reviews(original)


if __name__ == '__main__': unittest.main()
