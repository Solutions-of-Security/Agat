"""Portable replay, refusal controls and completion-marker write failures."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from decision_runtime.artifacts import sealed
from scripts.lib.decision_review_adjudication import encoded
from scripts.lib import decision_review_bundle as bundle
from scripts.lib.decision_review_finalization import prepare_finalization
from scripts.lib.decision_review_finalization_cli import REVIEW_INPUTS, ADJUDICATION_INPUTS
from scripts.lib.decision_review_pair import compare_pair
from scripts.test import test_decision_review_finalization as fixtures

ROOT = Path(__file__).resolve().parents[2]
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
reseal = lambda value: sealed({key: item for key, item in value.items() if key != 'sha256'})


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), ROOT/'scripts'/name)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


packager = load('package-decision-review-finalization.py')
verifier = load('verify-decision-review-bundle.py')


class ReviewBundleTest(unittest.TestCase):
    def setUp(self):
        self.helper = fixtures.ReviewFinalizationTest(); self.helper.setUp(); self.addCleanup(self.helper.doCleanups)
        self.root = self.helper.root; self.private = self.helper.private
        self.first, self.second = self.helper.first, self.helper.second
        self.comparison, self.adjudication = self.helper.comparison, self.helper.adjudication
        self.report, self.dataset = self.helper.written()

    def prepare(self):
        return bundle.prepare_bundle(self.first, self.second, self.comparison, sha(self.comparison),
            self.report, sha(self.report), self.dataset, sha(self.dataset), self.adjudication)

    def written(self):
        manifest, files = self.prepare()
        path = bundle.write_bundle(self.root, self.private/'bundle', manifest, files)/'bundle.json'
        return path, manifest, files

    def cli_args(self, output):
        args = ['--comparison', str(self.comparison), '--comparison-file-sha256', sha(self.comparison),
                '--finalization', str(self.report), '--finalization-file-sha256', sha(self.report),
                '--dataset', str(self.dataset), '--dataset-file-sha256', sha(self.dataset), '--output-dir', str(output)]
        for prefix, names, binding in (('first', REVIEW_INPUTS, self.first), ('second', REVIEW_INPUTS, self.second),
                                      ('adjudication', ADJUDICATION_INPUTS, self.adjudication)):
            if binding is not None:
                for offset, name in enumerate(names):
                    args.extend(['--'+prefix+'-'+name, str(binding[offset*2]),
                                 '--'+prefix+'-'+name+'-file-sha256', binding[offset*2+1]])
        return args

    def test_replays_every_source_and_adjudication_with_one_manifest_pin(self):
        path, manifest, files = self.written(); result = bundle.verify_bundle(path, sha(path))
        self.assertEqual(result['status'], 'pass'); self.assertEqual(result['artifactFilesVerified'], 15)
        self.assertEqual(result['caseCount'], 3); self.assertEqual(result['adjudicatedCases'], 2)
        self.assertTrue(result['sourceReviewsRevalidated']); self.assertTrue(result['handoffReconstructedFromSourceReviews'])
        self.assertEqual(result['datasetFileSha256'], sha(self.dataset))
        for flag in ('humanExecutionVerified', 'reviewerIdentityVerified', 'independentReviewVerified',
                     'expertQualificationsVerified', 'routingEnabled', 'classificationAccuracyMeasured'):
            self.assertIs(result[flag], False); self.assertIs(manifest[flag], False)
        for name, raw in files.items(): self.assertEqual((path.parent/name).read_bytes(), raw)

    def test_agreement_bundle_omits_adjudication_and_replays_nine_artifacts(self):
        self.second = self.helper.helper.helper.binding('agreeing', self.helper.helper.helper.b)
        self.adjudication = None; self.comparison.write_bytes(encoded(compare_pair(self.first, self.second)))
        outputs = prepare_finalization(self.first, self.second, self.comparison, sha(self.comparison))
        self.report, self.dataset = self.helper.written(outputs)
        path, manifest, _ = self.written()
        self.assertFalse(manifest['hasAdjudication'])
        self.assertEqual(bundle.verify_bundle(path, sha(path))['artifactFilesVerified'], 9)

    def test_moved_bundle_replays_after_original_source_files_are_removed(self):
        path, _, _ = self.written(); expected = bundle.verify_bundle(path, sha(path))
        relocated = self.private/'relocated'; shutil.move(path.parent, relocated)
        inputs = bundle.file_bindings(self.first, self.second, self.comparison, sha(self.comparison),
            self.report, sha(self.report), self.dataset, sha(self.dataset), self.adjudication)
        for source, _ in set(inputs.values()): source.unlink()
        moved = relocated/'bundle.json'; self.assertEqual(bundle.verify_bundle(moved, sha(moved)), expected)

    def test_changed_external_pin_or_each_artifact_is_rejected(self):
        path, _, files = self.written()
        with self.assertRaises(ValueError): bundle.verify_bundle(path, '0'*64)
        for name, raw in files.items():
            artifact = path.parent/name; artifact.write_bytes(raw+b' ')
            with self.subTest(name=name), self.assertRaises(ValueError): bundle.verify_bundle(path, sha(path))
            artifact.write_bytes(raw)

    def test_rehashed_manifest_cannot_invent_counts_authority_or_scalar_types(self):
        path, original, _ = self.written()
        for key, value in (('caseCount', 4), ('caseCount', 3.0), ('hasAdjudication', 1), ('modelCallsDuringPackaging', False),
                           ('humanExecutionVerified', True), ('qualification', 'qualified'),
                           ('classificationAccuracyMeasured', True), ('handoffReconstructedFromSourceReviews', False)):
            manifest = deepcopy(original); manifest[key] = value; path.write_bytes(encoded(reseal(manifest)))
            with self.subTest(key=key, value=value), self.assertRaises(ValueError): bundle.verify_bundle(path, sha(path))

    def test_duplicate_json_and_unknown_manifest_fields_rejected(self):
        path, original, _ = self.written()
        path.write_text('{"schemaVersion":"a","schemaVersion":"b"}')
        with self.assertRaises(ValueError): bundle.verify_bundle(path, sha(path))
        original['inputPath'] = '/untrusted'; path.write_bytes(encoded(reseal(original)))
        with self.assertRaises(ValueError): bundle.verify_bundle(path, sha(path))

    def test_manifest_cannot_choose_a_parent_or_absolute_artifact_path(self):
        path, original, _ = self.written()
        for name in ('../outside.json', '/outside.json', 'first/../dataset.json'):
            manifest = deepcopy(original); manifest['artifacts'][name] = manifest['artifacts'].pop('dataset.json')
            path.write_bytes(encoded(reseal(manifest)))
            with self.subTest(name=name), self.assertRaises(ValueError): bundle.verify_bundle(path, sha(path))

    def test_missing_extra_symlink_artifacts_and_symlink_manifest_rejected(self):
        path, _, files = self.written(); artifact = path.parent/'dataset.json'; raw = artifact.read_bytes(); artifact.unlink()
        with self.assertRaises(ValueError): bundle.verify_bundle(path, sha(path))
        artifact.write_bytes(raw); extra = path.parent/'unbound.json'; extra.write_text('{}')
        with self.assertRaises(ValueError): bundle.verify_bundle(path, sha(path))
        extra.unlink(); artifact.unlink(); artifact.symlink_to(self.dataset)
        with self.assertRaises(ValueError): bundle.verify_bundle(path, sha(path))
        artifact.unlink(); artifact.write_bytes(raw)
        link = self.private/'bundle.json'; link.symlink_to(path)
        with self.assertRaises(ValueError): bundle.verify_bundle(link, sha(path))

    def test_rehashed_artifact_binding_cannot_hide_a_changed_saved_review(self):
        path, manifest, _ = self.written(); artifact = path.parent/'first.output-review.json'
        value = json.loads(artifact.read_bytes()); value['labels'][0]['rationale'] = 'Changed after submitted receipt'
        artifact.write_bytes(encoded(value)); manifest['artifacts'][artifact.name] = {'sha256': sha(artifact), 'bytes': artifact.stat().st_size}
        path.write_bytes(encoded(reseal(manifest)))
        with self.assertRaises(ValueError): bundle.verify_bundle(path, sha(path))

    def test_declared_file_sizes_and_total_byte_bound_are_strict(self):
        path, original, _ = self.written()
        for size in (True, 0, -1, original['artifacts']['dataset.json']['bytes']+1):
            manifest = deepcopy(original); manifest['artifacts']['dataset.json']['bytes'] = size
            path.write_bytes(encoded(reseal(manifest)))
            with self.subTest(size=size), self.assertRaises(ValueError): bundle.verify_bundle(path, sha(path))
        path.write_bytes(encoded(original))
        with patch.object(bundle, 'MAX_BUNDLE_BYTES', 1), self.assertRaises(ValueError): bundle.verify_bundle(path, sha(path))
        with patch.object(bundle, 'MAX_BUNDLE_BYTES', 1), patch.object(bundle, 'verify_finalization') as replay:
            with self.assertRaises(ValueError): self.prepare()
            replay.assert_not_called()

    def test_partial_review_cannot_be_packaged_and_creates_no_output_directory(self):
        self.second = self.helper.helper.helper.binding('partial', self.helper.helper.helper.b, state='partial')
        output = self.private/'rejected'
        with patch.object(packager, 'ROOT', self.root): self.assertEqual(packager.main(self.cli_args(output)), 1)
        self.assertFalse(output.exists())

    def test_write_failure_leaves_no_completion_manifest(self):
        manifest, files = self.prepare(); output = self.private/'failed-write'; original = bundle.os.open; calls = 0
        def failing(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2: raise OSError('Synthetic bundle write failure')
            return original(*args, **kwargs)
        with patch.object(bundle.os, 'open', side_effect=failing), self.assertRaises(OSError):
            bundle.write_bundle(self.root, output, manifest, files)
        self.assertFalse((output/'bundle.json').exists())
        with self.assertRaises(ValueError): bundle.verify_bundle(output/'bundle.json', '0'*64)

    def test_writer_rejects_unsafe_names_or_changed_bytes_before_output(self):
        manifest, files = self.prepare(); output = self.private/'unsafe'
        files['../outside.json'] = files.pop('dataset.json')
        with self.assertRaises(ValueError): bundle.write_bundle(self.root, output, manifest, files)
        self.assertFalse(output.exists())
        manifest, files = self.prepare(); files['dataset.json'] += b' '
        with self.assertRaises(ValueError): bundle.write_bundle(self.root, output, manifest, files)
        self.assertFalse(output.exists())

    def test_cli_private_modes_reuse_and_readonly_verification(self):
        output = self.private/'cli-bundle'
        with patch.object(packager, 'ROOT', self.root):
            self.assertEqual(packager.main(self.cli_args(output)), 0)
            original = (output/'bundle.json').read_bytes()
            self.assertEqual(packager.main(self.cli_args(output)), 1)
            self.assertEqual((output/'bundle.json').read_bytes(), original)
            self.assertEqual(packager.main(self.cli_args(self.root/'outside')), 1)
        self.assertEqual(output.stat().st_mode & 0o777, 0o700)
        self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in output.iterdir()))
        args = ['--bundle', str(output/'bundle.json'), '--bundle-file-sha256', sha(output/'bundle.json'),
                '--output-dir', str(self.private/'verified')]
        with patch.object(verifier, 'ROOT', self.root):
            self.assertEqual(verifier.main(args), 0); self.assertEqual(verifier.main(args), 1)
            bad = args.copy(); bad[3] = '0'*64; bad[-1] = str(self.private/'refused')
            self.assertEqual(verifier.main(bad), 1); self.assertFalse((self.private/'refused').exists())
        self.assertEqual((output/'bundle.json').read_bytes(), original)


if __name__ == '__main__': unittest.main()
