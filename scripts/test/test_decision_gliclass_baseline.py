import copy
from collections import UserDict
from contextlib import nullcontext
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from decision_runtime.artifacts import sealed
from decision_runtime.contracts import DecisionError, Request, fingerprint
from decision_runtime.engine import DecisionEngine
from scripts.lib import decision_gliclass_baseline as baseline


def dataset():
    cases = []
    for index, kind in enumerate(('choice', 'boolean', 'score')):
        options = [{'id': 'first', 'description': 'First meaning'}, {'id': 'second', 'description': 'Second meaning'}]
        if kind == 'boolean':
            options[0]['value'], options[1]['value'] = True, False
        if kind == 'score':
            options[0]['value'], options[1]['value'] = 2, 0
        cases.append({'id': f'case-{index}', 'family': kind, 'groupId': f'group-{index}', 'split': 'development',
            'labelSource': 'synthetic-authored', 'expectedOptionId': 'second', 'annotationRationale': 'GOLD NEVER SENT',
            'request': {'schemaVersion': 'agat.decision.v1', 'id': f'case-{index}', 'kind': kind,
                        'question': 'Complete question', 'state': f'Complete state {index}', 'options': options}})
    return {'schemaVersion': 'agat.decision.dataset.v1', 'id': 'synthetic-gliclass-unit-fixture', 'cases': cases}


class FakeLogits:
    def __init__(self, values, shape=None):
        self.values, self.shape = values, shape or (1, len(values))
    def __getitem__(self, index): return self
    def detach(self): return self
    def to(self, **kwargs): return self
    def tolist(self): return self.values


class FakeTorch:
    long, float32 = 'long', 'float32'
    def tensor(self, values, **kwargs): return values
    def inference_mode(self): return nullcontext()


class FakeTokenizer:
    def __init__(self): self.calls, self.extra, self.wrong_marker = [], 0, False
    def __call__(self, text, **kwargs):
        self.calls.append((text, kwargs))
        ids = [2] + [baseline.CLASS_TOKEN, 12] * text.count('<<LABEL>>') + [baseline.TEXT_TOKEN, 13, 3]
        ids += [14] * self.extra
        if self.wrong_marker: ids[1] = 14
        # Hugging Face BatchEncoding is a UserDict, rather than a dict subclass.
        return UserDict({'input_ids': ids, 'attention_mask': [1] * len(ids)})


def backend():
    value = object.__new__(baseline.GLiClassBackend)
    value.torch, value.tokenizer, value.audit, value.load_ms = FakeTorch(), FakeTokenizer(), [], 1.0
    value.model_calls, value.model_values, value.model_shape = [], [0.0, 6.0], None
    def model(**kwargs):
        value.model_calls.append(kwargs)
        return type('Output', (), {'logits': FakeLogits(value.model_values, value.model_shape)})()
    value.model = model
    catalog = baseline.catalog()
    value.identity = {'repository': catalog['repository'], 'revision': catalog['revision'],
        'artifactSha256': fingerprint(catalog['files']), 'tokenizerSha256': catalog['files']['tokenizer.json']['sha256'],
        'backend': 'gliclass-uni-encoder-native-logits', 'profile': dict(baseline.PROFILE),
        'stack': {'versions': dict(baseline.PACKAGE_VERSIONS), 'gliclassSourceFiles': {'synthetic-only.py': '0' * 64}}}
    return value


def report():
    return baseline.measure(dataset(), backend(), {'sourceCommit': '1' * 40,
        'sourceFiles': {'scripts/lib/decision_gliclass_baseline.py': '2' * 64}})


def reseal(value): return sealed({key: item for key, item in value.items() if key != 'sha256'})


class GLiClassBaselineTest(unittest.TestCase):
    def test_real_cli_decodes_sha_pinned_json_and_replays_complete_report(self):
        spec = importlib.util.spec_from_file_location('gliclass_cli_unit_fixture', baseline.ROOT / 'scripts/evaluate-gliclass-development.py')
        cli = importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source_raw = b'synthetic CLI source fixture'
            identity = {'sourceCommit': '1' * 40, 'sourceFiles': {'fixture.py': hashlib.sha256(source_raw).hexdigest()}}
            raw = json.dumps(dataset()).encode(); data_path = root / 'dataset.json'; data_path.write_bytes(raw)
            output, verified = root / 'docs/private/measured', root / 'docs/private/verified'
            args = ['--model-dir', str(root / 'fake-model'), '--dataset', str(data_path),
                    '--dataset-file-sha256', hashlib.sha256(raw).hexdigest()]
            with patch.object(cli, 'ROOT', root), patch.object(cli, 'frozen_sources', return_value=identity), \
                 patch.object(cli, 'verify_local', return_value={'sha256': '3' * 64}), \
                 patch.object(cli, 'GLiClassBackend', return_value=backend()):
                self.assertEqual(cli.main(['measure', *args, '--output-dir', str(output)]), 0)
                report_path = output / 'report.json'; report_raw = report_path.read_bytes()
                with patch.object(cli.subprocess, 'check_output', return_value=source_raw):
                    self.assertEqual(cli.main(['verify', *args, '--report', str(report_path),
                        '--report-file-sha256', hashlib.sha256(report_raw).hexdigest(), '--output-dir', str(verified)]), 0)
                self.assertEqual((verified / 'verified-report.json').read_bytes(), report_raw)
                self.assertEqual(json.loads((verified / 'execution.json').read_bytes())['modelCalls'], 0)

    def test_complete_request_candidate_order_and_no_gold_reaches_native_forward(self):
        native = backend(); result = baseline.measure(dataset(), native, {'sourceCommit': '1' * 40,
            'sourceFiles': {'fixture.py': '2' * 64}})
        self.assertEqual(len(native.model_calls), 6)
        for text, flags in native.tokenizer.calls:
            self.assertIn('Complete question', text); self.assertIn('Complete state', text)
            self.assertNotIn('GOLD NEVER SENT', text); self.assertNotIn('expectedOptionId', text)
            self.assertEqual(flags, {'truncation': False, 'padding': False,
                                     'return_attention_mask': True, 'return_token_type_ids': False})
        self.assertLess(native.tokenizer.calls[0][0].index('First meaning'), native.tokenizer.calls[0][0].index('Second meaning'))
        self.assertGreater(native.tokenizer.calls[1][0].index('First meaning'), native.tokenizer.calls[1][0].index('Second meaning'))
        self.assertIs(result['evaluation']['cases'][1]['result']['value'], False)
        self.assertAlmostEqual(result['evaluation']['cases'][2]['result']['value'], 2 / (1 + __import__('math').exp(6)))
        self.assertEqual(result['summary']['original']['correct'], 3)
        self.assertEqual(result['summary']['reversed']['correct'], 0)
        self.assertEqual(baseline.verify_report(dataset(), result), result)

    def test_full_context_boundary_refuses_before_model_and_retains_all_attempts(self):
        native = backend(); native.tokenizer.extra = 1100
        result = baseline.measure(dataset(), native, {'sourceCommit': '1' * 40, 'sourceFiles': {'fixture.py': '2' * 64}})
        self.assertEqual(native.model_calls, [])
        self.assertEqual(result['summary']['original']['attempted'], 3)
        self.assertEqual(result['summary']['original']['backendErrors'], 3)
        self.assertEqual(result['summary']['original']['coverage'], 0)
        self.assertTrue(all(len(call['tokenIds']) > 1024 for call in result['nativeCalls']))
        baseline.verify_report(dataset(), result)

    def test_exact_prospective_token_limit_and_candidate_markers(self):
        encoded = {'input_ids': [2, baseline.CLASS_TOKEN, baseline.CLASS_TOKEN, baseline.TEXT_TOKEN] + [3] * 1020,
                   'attention_mask': [1] * 1024}
        baseline.validate_encoding(encoded, 2)
        encoded['input_ids'].append(4); encoded['attention_mask'].append(1)
        with self.assertRaises(DecisionError) as error: baseline.validate_encoding(encoded, 2)
        self.assertEqual(error.exception.code, 'context_bound_exceeded')
        native = backend(); native.tokenizer.wrong_marker = True
        result = DecisionEngine(native).decide(dataset()['cases'][0]['request'])
        self.assertEqual(result['reason'], 'invalid_candidate_tokens'); self.assertEqual(native.model_calls, [])

    def test_reserved_marker_in_state_question_or_label_is_never_silently_removed(self):
        for location in ('state', 'question', 'option'):
            native = backend(); request = copy.deepcopy(dataset()['cases'][0]['request'])
            if location == 'option': request['options'][0]['description'] += '<<LABEL>>'
            else: request[location] += '<<SEP>>'
            result = DecisionEngine(native).decide(request)
            self.assertEqual(result['reason'], 'unsupported_control_marker')
            self.assertEqual(native.tokenizer.calls, []); self.assertEqual(native.model_calls, [])

    def test_nan_and_native_shape_failures_never_create_a_distribution(self):
        for values, shape in (([0.0, float('nan')], None), ([0.0, 6.0], (1, 3))):
            native = backend(); native.model_values, native.model_shape = values, shape
            result = DecisionEngine(native).decide(dataset()['cases'][0]['request'])
            self.assertEqual(result['status'], 'error'); self.assertEqual(result['distribution'], [])
            self.assertEqual(native.audit[0]['rejection'], 'backend_error')

    def test_calibration_holdout_and_train_are_refused_before_backend_construction(self):
        for split in ('train', 'calibration', 'holdout'):
            data = dataset(); data['cases'][0]['split'] = split
            with self.assertRaises(ValueError): baseline.development_dataset(data)

    def test_resealed_reports_cannot_change_policy_logits_typed_values_counts_or_authority(self):
        original = report()
        mutations = [lambda r: r.update(routingEnabled=True), lambda r: r.update(humanLabelsVerified=0),
            lambda r: r['summary']['original'].update(correct=99), lambda r: r['evaluation']['metrics'].update(correct=99),
            lambda r: r['evaluation']['cases'].pop(), lambda r: r['nativeCalls'].pop(),
            lambda r: r['evaluation']['policy'].update(minProbability=.1),
            lambda r: r['evaluation']['cases'][1]['result'].update(value=0),
            lambda r: r['nativeCalls'][0].update(inferenceAttempted=1),
            lambda r: r['nativeCalls'][0].update(renderedInputSha256='0' * 64),
            lambda r: r['evaluation']['model']['profile'].update(maxInputTokens=8192),
            lambda r: r['evaluation']['cases'][0]['result']['distribution'].reverse(),
            lambda r: r['evaluation']['cases'][0]['result']['distribution'][0].update(probability=.99)]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                changed = copy.deepcopy(original); mutation(changed)
                with self.assertRaises(ValueError): baseline.verify_report(dataset(), reseal(changed))

    def test_a_resealed_context_refusal_cannot_claim_inference_or_clipping(self):
        native = backend(); native.tokenizer.extra = 1100
        original = baseline.measure(dataset(), native, {'sourceCommit': '1' * 40, 'sourceFiles': {'fixture.py': '2' * 64}})
        for mutation in (lambda r: r['nativeCalls'][0].update(inferenceAttempted=True),
                         lambda r: r['nativeCalls'][0].update(tokenIds=r['nativeCalls'][0]['tokenIds'][:1024])):
            changed = copy.deepcopy(original); mutation(changed)
            with self.assertRaises(ValueError): baseline.verify_report(dataset(), reseal(changed))

    def test_reserved_marker_refusal_is_replayable_without_inference(self):
        data = dataset(); data['cases'][0]['request']['state'] += '<<LABEL>>'
        result = baseline.measure(data, backend(), {'sourceCommit': '1' * 40, 'sourceFiles': {'fixture.py': '2' * 64}})
        self.assertEqual(result['summary']['original']['backendErrors'], 1)
        baseline.verify_report(data, result)

    def test_pinned_model_requires_exact_files_bytes_sha_and_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); descriptor = copy.deepcopy(baseline.catalog())
            for name in baseline.MODEL_FILES:
                raw = ('synthetic-only-' + name).encode(); (root / name).write_bytes(raw)
                descriptor['files'][name] = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
            manifest = sealed({'schemaVersion': baseline.ARTIFACT_SCHEMA, 'modelDescriptorSha256': fingerprint(descriptor),
                'repository': descriptor['repository'], 'revision': descriptor['revision'], 'files': descriptor['files']})
            baseline.write_json_new(root / 'artifact.json', manifest); baseline.verify_local(root, descriptor)
            (root / 'remote.py').write_text('unexpected')
            with self.assertRaises(ValueError): baseline.verify_local(root, descriptor)
            (root / 'remote.py').unlink(); path = root / 'model.safetensors'; saved = path.read_bytes()
            path.write_bytes(b'x' + saved[1:])
            with self.assertRaises(ValueError): baseline.verify_local(root, descriptor)
            path.write_bytes(saved); actual = root / 'actual'; path.rename(actual); path.symlink_to(actual)
            with self.assertRaises(ValueError): baseline.verify_local(root, descriptor)

    def test_download_is_explicit_and_checks_expected_size_before_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / 'new'; descriptor = copy.deepcopy(baseline.catalog())
            descriptor['files'] = {name: {'bytes': 1, 'sha256': hashlib.sha256(b'x').hexdigest()} for name in baseline.MODEL_FILES}
            import io
            with patch.object(baseline, 'catalog', return_value=descriptor), patch.object(baseline.urllib.request, 'urlopen', return_value=io.BytesIO(b'xx')):
                with self.assertRaises(ValueError): baseline.download(target)
            self.assertFalse((target / 'artifact.json').exists())


if __name__ == '__main__': unittest.main()
