import copy
import hashlib
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from decision_runtime.artifacts import sealed
from decision_runtime.contracts import canonical_json, fingerprint
from scripts.lib import decision_public_native_baseline as baseline
from scripts.test.test_decision_public_load import context_fixture
from scripts.test import test_decision_gliclass_baseline as gl_fixtures
from scripts.test import test_decision_nanojev_baseline as nano_fixtures

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('public_native_cli', ROOT / 'scripts/evaluate-public-native-baseline.py')
cli = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(cli)
IDENTITY = nano_fixtures.IDENTITY
reseal = lambda value: sealed({key: item for key, item in value.items() if key != 'sha256'})


def backend(kind):
    native = gl_fixtures.backend() if kind == 'gliclass' else nano_fixtures.backend()
    if kind == 'gliclass': native.model_values = [0., 1., 2., 3., 6.]
    return native


def report(kind='nanojev', context=None, native=None):
    return baseline.measure(context or context_fixture(), kind, Path('synthetic-only-model'), IDENTITY,
        backend_factory=lambda _: native or backend(kind), capture=nano_fixtures.capture())


class PublicNativeBaselineTest(unittest.TestCase):
    def test_frozen_source_inventory_imports_cli_in_isolation_without_model_packages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            names = subprocess.check_output(['git', 'ls-files', '--', *baseline.SOURCE_PATHS], cwd=ROOT, text=True).splitlines()
            for name in names:
                target = root / name; target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / name, target)
            program = """import runpy, sys
sys.argv = [sys.argv[1], '--help']
try:
    runpy.run_path(sys.argv[0], run_name='__main__')
except SystemExit as error:
    assert error.code == 0
else:
    raise AssertionError('CLI help did not exit')
assert not any(name.split('.')[0] in {'torch','transformers','gliclass','tokenizers','numpy','safetensors'} for name in sys.modules)
print('closed-source-cli modelPackagesImported=0')
"""
            result = subprocess.run([sys.executable, '-I', '-B', '-c', program,
                str(root / 'scripts/evaluate-public-native-baseline.py')], cwd=root, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('closed-source-cli modelPackagesImported=0', result.stdout)

    def test_whole_unlabelled_inventory_not_filtered_by_other_model_context(self):
        context = context_fixture()
        self.assertEqual(sum(not row['contextEligible'] for row in context['inputs']), 1)
        for kind in ('gliclass', 'nanojev'):
            with self.subTest(kind=kind):
                value = report(kind, context)
                baseline.verify_report(context, value)
                self.assertEqual(value['summary']['nativeInferenceAttempts'], 2 * len(context['inputs']))
                self.assertEqual([row['id'] for row in value['cases']], [row['id'] for row in context['inputs']])
                self.assertEqual(value['referenceLabelsVerified'], 0)
                self.assertIs(value['classificationAccuracyMeasured'], False)
                self.assertNotIn('accuracy', value['summary'])

    def test_context_refusals_preserve_whole_denominator_and_never_infer(self):
        context = context_fixture()
        for kind in ('gliclass', 'nanojev'):
            native = backend(kind); native.tokenizer.extra = 1100
            value = report(kind, context, native)
            self.assertEqual(value['summary']['nativeInferenceAttempts'], 0)
            self.assertEqual(value['summary']['wholeVariantCount'], 2 * len(context['inputs']))
            for order in value['summary']['orders'].values():
                self.assertEqual(order['wholeCases'], len(context['inputs']))
                self.assertEqual(order['refusals'], {'context_bound_exceeded': len(context['inputs'])})
                self.assertEqual(order['computedCases'], 0)
            self.assertTrue(all(not row['inferenceAttempted'] and row['requestLogits'] == [] for row in value['nativeCalls']))

    def test_calibration_holdout_labels_and_scope_authority_rejected_before_model_factory(self):
        context = context_fixture(); factory = Mock()
        for mutation in (lambda d: d.update(selectedSplit='holdout'), lambda d: d.update(selectedSplit='calibration'),
                         lambda d: d.update(referenceLabels=1), lambda d: d.update(referenceLabels=False),
                         lambda d: d.update(routingEnabled=True), lambda d: d.update(representativeAgatTraffic=True),
                         lambda d: d['inputs'][0].update(expectedOptionId='invented-label')):
            with self.subTest(mutation=mutation):
                bad = copy.deepcopy(context); mutation(bad)
                with self.assertRaises(ValueError): baseline.measure(reseal(bad), 'nanojev', Path('unused'), IDENTITY, backend_factory=factory)
        factory.assert_not_called()

    def test_missing_reordered_or_wrong_group_inputs_fail_closed(self):
        context = context_fixture(); original = report(context=context)
        for mutation in (lambda d: d['cases'].pop(), lambda d: d['nativeCalls'].pop(), lambda d: d['cases'].reverse(),
                         lambda d: d['cases'][0].update(groupId='different-group'),
                         lambda d: d['nativeCalls'][0].update(inputSha256='0' * 64),
                         lambda d: d['nativeCalls'][0].update(ordinal=True)):
            with self.subTest(mutation=mutation):
                bad = copy.deepcopy(original); mutation(bad)
                with self.assertRaises(ValueError): baseline.verify_report(context, reseal(bad))

    def test_logits_policy_and_cached_summary_are_recomputed(self):
        context = context_fixture()
        for kind in ('gliclass', 'nanojev'):
            original = report(kind, context)
            for mutation in (lambda d: d['nativeCalls'][0]['requestLogits'].__setitem__(0, 123.),
                             lambda d: d['cases'][0]['result']['distribution'][0].update(probability=.5),
                             lambda d: d['cases'][0]['result']['policy'].update(minProbability=.1),
                             lambda d: d['summary'].update(nativeInferenceAttempts=0),
                             lambda d: d['summary']['orders']['original'].update(acceptedCases=123)):
                with self.subTest(kind=kind, mutation=mutation):
                    bad = copy.deepcopy(original); mutation(bad)
                    with self.assertRaises(ValueError): baseline.verify_report(context, reseal(bad))

    def test_reserved_source_control_syntax_refused_before_tokenization(self):
        for kind, marker in (('gliclass', '<<LABEL>>'), ('nanojev', '<|im_start|>')):
            context = context_fixture(); row = context['inputs'][0]
            row['request']['state'] += marker
            from decision_runtime.contracts import Request
            row['inputSha256'] = Request.from_dict(row['request']).input_sha256
            value = report(kind, reseal(context))
            self.assertEqual(value['cases'][0]['result']['reason'], 'unsupported_control_marker')
            self.assertFalse(value['nativeCalls'][0]['inferenceAttempted'])
            self.assertEqual(value['nativeCalls'][0]['requestLogits'], [])

    def test_resealed_context_tokens_cannot_claim_refusal_after_forward(self):
        context = context_fixture()
        for kind in ('gliclass', 'nanojev'):
            native = backend(kind); native.tokenizer.extra = 1100
            value = report(kind, context, native)
            value['nativeCalls'][0].update(inferenceAttempted=True, inferenceMs=1.)
            with self.assertRaises(ValueError): baseline.verify_report(context, reseal(value))

    def test_semantic_native_mapping_and_complete_eos_are_required(self):
        context = context_fixture(); original = report(context=context)
        for mutation in (lambda d: d['nativeCalls'][0]['requestOptionIdsInNativeOrder'].reverse(),
                         lambda d: d['nativeCalls'][0]['leafTokenIds'][0].__setitem__(-1, 22),
                         lambda d: d['nativeCalls'][0]['nativeLogits'].__setitem__(0, 99.)):
            bad = copy.deepcopy(original); mutation(bad)
            with self.assertRaises(ValueError): baseline.verify_report(context, reseal(bad))

    def test_label_free_native_result_preserves_typed_false_and_numeric_score_mean(self):
        from decision_runtime.contracts import Request
        context = context_fixture()
        for index, example in enumerate(gl_fixtures.dataset()['cases'][1:]):
            request = copy.deepcopy(example['request']); request['id'] = context['inputs'][index]['id']
            if request['kind'] == 'score': request['inputFingerprintVersion'] = 'binary64-v1'
            context['inputs'][index]['request'] = request
            context['inputs'][index]['inputSha256'] = Request.from_dict(request).input_sha256
        context = reseal(context); value = report(context=context)
        baseline.verify_report(context, value)
        self.assertIs(value['cases'][0]['result']['value'], False)
        score = value['cases'][1]['result']
        self.assertEqual(score['status'], 'ok')
        self.assertAlmostEqual(score['value'], 2 * score['distribution'][0]['probability'])
        bad = copy.deepcopy(value); bad['cases'][0]['result']['value'] = 0
        with self.assertRaises(ValueError): baseline.verify_report(context, reseal(bad))

    def test_resources_wrong_pid_units_and_cached_totals_rejected(self):
        context = context_fixture(); original = report(context=context)
        for mutation in (lambda d: d['resourceSamples'][1].update(pid=5678),
                         lambda d: d['resourceSamples'][1].update(rawMaxRssToBytesMultiplier=1024),
                         lambda d: d['resourceSummary'].update(maxRssBytesAfterWholeScoring=1)):
            bad = copy.deepcopy(original); mutation(bad)
            with self.assertRaises(ValueError): baseline.verify_report(context, reseal(bad))

    def test_no_human_accuracy_slo_or_routing_authority_can_be_resealed(self):
        context = context_fixture(); original = report(context=context)
        for key, value in baseline.AUTHORITY.items():
            for wrong in ((True, 0) if value is False else (True, 'qualified') if isinstance(value, str) else (True, False, 1)):
                with self.subTest(key=key, wrong=wrong):
                    bad = copy.deepcopy(original); bad[key] = wrong
                    with self.assertRaises(ValueError): baseline.verify_report(context, reseal(bad))

    def test_model_profile_weights_and_native_layout_identity_fail_closed(self):
        context = context_fixture(); original = report(context=context)
        for mutation in (lambda d: d['model']['profile'].update(device='cuda:0'),
                         lambda d: d['model'].update(strictStateDictLoaded=False),
                         lambda d: d['model'].update(qualifiedForRouting=True),
                         lambda d: d['model'].update(artifactSha256='0' * 64),
                         lambda d: d['model']['weightsLayout'].update(tensorCount=True)):
            bad = copy.deepcopy(original); mutation(bad)
            with self.assertRaises(ValueError): baseline.verify_report(context, reseal(bad))

    def test_actual_cli_byte_pins_replay_without_measuring_and_no_partial_outputs(self):
        self.run_cli_case()

    def test_actual_cli_complete_publication_byte_bound_includes_newline(self):
        self.run_cli_case(byte_bound=True)

    def run_cli_case(self, byte_bound=False):
        context = context_fixture(); value = report(context=context)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); private = root / 'docs/private'; private.mkdir(parents=True)
            context_path = private / 'context.json'; context_raw = (canonical_json(context) + '\n').encode(); context_path.write_bytes(context_raw)
            report_path = private / 'report.json'; report_raw = (canonical_json(value) + '\n').encode(); report_path.write_bytes(report_raw)
            output = private / 'verified'; sha = lambda raw: hashlib.sha256(raw).hexdigest()
            args = ['verify', '--candidate', 'nanojev', '--context-profile', str(context_path), '--context-file-sha256', sha(context_raw),
                '--model-dir', str(private / 'model'), '--report', str(report_path), '--report-file-sha256', sha(report_raw), '--output-dir', str(output)]
            with patch.object(cli, 'ROOT', root), patch.object(cli, 'frozen_sources', return_value=IDENTITY), \
                 patch.object(cli, 'historical_context_sources'), patch.object(cli, 'measure') as measure, \
                 patch.object(cli.subprocess, 'check_output', return_value=b'fixture'), \
                 patch.object(baseline.nano, 'verify_local', return_value={'sha256': '1' * 64}), \
                 patch.object(baseline.nano, 'weights_header', return_value=nano_fixtures.layout()):
                # Keep the external source byte proof independent from the synthetic fixed identity.
                identity = {'sourceCommit': IDENTITY['sourceCommit'], 'sourceFiles': {'fixture.py': sha(b'fixture')}}
                value['sourceIdentity'] = identity; value = reseal(value); report_raw = (canonical_json(value) + '\n').encode(); report_path.write_bytes(report_raw)
                args[args.index('--report-file-sha256') + 1] = sha(report_raw)
                with patch.object(cli, 'frozen_sources', return_value=identity):
                    if byte_bound:
                        args = ['measure', '--candidate', 'nanojev', '--context-profile', str(context_path),
                            '--context-file-sha256', sha(context_raw), '--model-dir', str(private / 'model'), '--output-dir', str(output)]
                        measure.return_value = value
                        with patch.object(cli, 'MAX_REPORT_BYTES', len(report_raw) - 1): self.assertEqual(cli.main(args), 1)
                        self.assertFalse(output.exists())
                        measure.assert_called_once()
                    else:
                        self.assertEqual(cli.main(args), 0)
                        self.assertEqual((output / 'verified-report.json').read_bytes(), report_raw)
                        bad_output = private / 'wrong-pin-output'; args[-1] = str(bad_output)
                        args[args.index('--report-file-sha256') + 1] = '0' * 64
                        self.assertEqual(cli.main(args), 1); self.assertFalse(bad_output.exists())
                if not byte_bound: measure.assert_not_called()


if __name__ == '__main__': unittest.main()
