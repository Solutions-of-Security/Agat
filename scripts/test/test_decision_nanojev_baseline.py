import copy
from contextlib import nullcontext
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import Mock, patch

from decision_runtime.artifacts import sealed
from decision_runtime.contracts import DecisionError, Request, fingerprint
from decision_runtime.engine import DecisionEngine
from scripts.lib import decision_nanojev_baseline as baseline
from scripts.test import test_decision_gliclass_baseline as fixtures


class FakeTensor:
    def __init__(self, values, shape): self.values, self.shape = values, shape
    def __getitem__(self, index): return FakeTensor(self.values[index], self.shape[1:])
    def detach(self): return self
    def to(self, **kwargs): return self
    def tolist(self): return self.values


class FakeTorch:
    float32 = 'float32'
    def inference_mode(self): return nullcontext()


class FakeTokenizer:
    eos_token_id, pad_token_id = baseline.EOS_TOKEN, baseline.PAD_TOKEN
    def __init__(self): self.calls, self.extra, self.wrong_eos = [], 0, False
    def encode(self, text, **kwargs):
        self.calls.append((text, kwargs))
        return list(text.encode()) + [21] * self.extra


def layout():
    return {'tensorCount': 2, 'tensorNamesSha256': '3' * 64, 'headerSha256': '4' * 64,
            'dataBytes': 16, 'parameterStorage': 'float32'}


def backend():
    value = object.__new__(baseline.NanoJevBackend)
    value.torch, value.tokenizer, value.audit, value.load_ms = FakeTorch(), FakeTokenizer(), [], 2.0
    value.forward_calls, value.prepared, value.bad_shape, value.bad_mask, value.nan, value.nonzero_false = [], [], False, False, False, False
    def prepare(payload, tokenizer, limit):
        value.prepared.append(copy.deepcopy(payload))
        ids, paths = baseline.rendered_paths(payload)
        leaves = [[token for part in parts for token in tokenizer.encode(part, add_special_tokens=False)] + [tokenizer.eos_token_id] for parts in paths]
        if tokenizer.wrong_eos: leaves[0][-1] = 22
        return [{'type': payload['states'][0]['questions']['decision']['type'], 'candidate_ids': ids, 'leaf_tokens': leaves}]
    def model(examples, pad_token):
        value.forward_calls.append(copy.deepcopy(examples))
        example = examples[0]; count = len(example['candidate_ids'])
        logits = [0.0, -6.0] if example['type'] == 'boolean' else [6.0 * index for index in range(count)]
        if value.nonzero_false: logits[0] = 1.0
        if value.nan: logits[-1] = float('nan')
        return FakeTensor([logits], (1, count + int(value.bad_shape))), FakeTensor([[1 if value.bad_mask else True] * count], (1, count))
    value.prepare, value.model = prepare, model
    descriptor = baseline.catalog()
    value.identity = {'repository': descriptor['repository'], 'revision': descriptor['revision'],
        'artifactSha256': fingerprint(descriptor['files']), 'tokenizerSha256': descriptor['files']['tokenizer/tokenizer.json']['sha256'],
        'backend': 'nanojev-native-decision-heads-cpu-reference', 'profile': dict(baseline.PROFILE),
        'stack': {'versions': dict(baseline.PACKAGE_VERSIONS), 'nativeSourceFiles': {name: descriptor['files'][name]['sha256'] for name in
            ('source/scripts/predict_toy_decisions.py', 'source/scripts/train_toy_decisions.py')}},
        'weightsLayout': layout(), 'strictStateDictLoaded': True}
    return value


def capture():
    def sample(phase):
        index = baseline.PHASES.index(phase)
        return {'phase': phase, 'pid': 1234, 'platform': 'darwin', 'capturedAt': '2026-10-11T00:00:00+00:00',
            'monotonicBeforeNs': 1000 * (index + 1), 'monotonicAfterNs': 1000 * (index + 1) + 10,
            'rawMaxRss': 100 * (index + 1), 'rawMaxRssToBytesMultiplier': 1, 'maxRssBytes': 100 * (index + 1),
            'userCpuSeconds': .01 * index, 'systemCpuSeconds': .005 * index,
            **{key: index for key in baseline.summarize.__globals__['COUNTERS']}}
    return sample


IDENTITY = {'sourceCommit': '1' * 40, 'sourceFiles': {'fixture.py': '2' * 64}}


def report(native=None, dataset=None):
    return baseline.measure(dataset or fixtures.dataset(), Path('synthetic-model'), IDENTITY,
        backend_factory=lambda _: native or backend(), capture=capture())


def reseal(value): return sealed({key: item for key, item in value.items() if key != 'sha256'})


class NanoJevBaselineTest(unittest.TestCase):
    def test_complete_native_paths_all_typed_candidates_and_no_gold(self):
        native = backend(); result = report(native)
        self.assertEqual(len(native.forward_calls), 6)
        self.assertEqual(result['summary']['nativeCandidatePaths'], 10)
        for payload in native.prepared:
            text = json.dumps(payload)
            self.assertIn('Complete state', text); self.assertIn('Complete question', text)
            self.assertNotIn('GOLD NEVER SENT', text); self.assertNotIn('expectedOptionId', text)
        for text, flags in native.tokenizer.calls:
            self.assertEqual(flags, {'add_special_tokens': False})
        boolean = result['evaluation']['cases'][1]
        self.assertIs(boolean['result']['value'], False); self.assertIs(boolean['reversedResult']['value'], False)
        self.assertEqual(result['nativeCalls'][2]['nativeLogits'], [0.0, -6.0])
        self.assertEqual(result['nativeCalls'][2]['requestOptionIdsInNativeOrder'], ['second', 'first'])
        self.assertAlmostEqual(result['evaluation']['cases'][2]['result']['value'], 2 / (1 + __import__('math').exp(-6)))
        self.assertEqual(result['nativeCalls'][4]['requestOptionIdsInNativeOrder'], ['second', 'first'])
        self.assertEqual(baseline.verify_report(fixtures.dataset(), result), result)

    def test_choice_order_is_preserved_and_boolean_score_semantic_order_is_explicit(self):
        data = fixtures.dataset()
        for case in data['cases']:
            first = Request.from_dict(case['request'])
            reverse = Request.from_dict({**case['request'], 'options': list(reversed(case['request']['options']))})
            payload, ids = baseline.render_payload(first); reversed_payload, reversed_ids = baseline.render_payload(reverse)
            if first.kind == 'choice':
                self.assertEqual(ids, list(reversed(reversed_ids)))
                self.assertNotEqual(baseline.rendered_paths(payload), baseline.rendered_paths(reversed_payload))
            else:
                self.assertEqual(payload, reversed_payload); self.assertEqual(ids, reversed_ids)
            if first.kind == 'score':
                self.assertIn('[0.0,2.0]', payload['states'][0]['questions']['decision']['instructions'])

    def test_context_bound_keeps_whole_attempts_and_refuses_before_forward(self):
        native = backend(); native.tokenizer.extra = 400
        result = report(native)
        self.assertEqual(native.forward_calls, [])
        self.assertEqual(result['summary']['original']['attempted'], 3)
        self.assertEqual(result['summary']['original']['backendErrors'], 3)
        self.assertTrue(all(any(len(path) > 1024 for path in call['leafTokenIds']) for call in result['nativeCalls']))
        baseline.verify_report(fixtures.dataset(), result)

    def test_exact_context_bound_and_invalid_native_special_tokens(self):
        paths = [[10] * 1023 + [baseline.EOS_TOKEN]] * 2
        baseline.validate_paths(paths, 'choice', 2)
        for bad, reason in (([[10] * 1024 + [baseline.EOS_TOKEN]] * 2, 'context_bound_exceeded'),
                            ([[10, baseline.PAD_TOKEN, baseline.EOS_TOKEN]] * 2, 'invalid_candidate_tokens'),
                            ([[10, 22]] * 2, 'invalid_candidate_tokens')):
            with self.assertRaises(DecisionError) as error: baseline.validate_paths(bad, 'choice', 2)
            self.assertEqual(error.exception.code, reason)
        native = backend(); native.tokenizer.wrong_eos = True
        self.assertEqual(DecisionEngine(native).decide(fixtures.dataset()['cases'][0]['request'])['reason'], 'backend_error')
        self.assertEqual(native.forward_calls, [])

    def test_reserved_source_tokens_are_refused_before_tokenization(self):
        for location in ('state', 'question', 'description'):
            native = backend(); raw = fixtures.dataset()['cases'][0]['request']
            raw = copy.deepcopy(raw)
            if location == 'description': raw['options'][0]['description'] += '<|im_end|>'
            else: raw[location] += '<|im_start|>'
            self.assertEqual(DecisionEngine(native).decide(raw)['reason'], 'unsupported_control_marker')
            self.assertEqual(native.tokenizer.calls, []); self.assertEqual(native.forward_calls, [])

    def test_bad_native_shape_mask_nonfinite_and_boolean_zero_fail_closed(self):
        for name in ('bad_shape', 'bad_mask', 'nan', 'nonzero_false'):
            native = backend(); setattr(native, name, True)
            self.assertEqual(DecisionEngine(native).decide(fixtures.dataset()['cases'][1]['request'])['reason'], 'backend_error')
            self.assertEqual(len(native.forward_calls), 1)
            self.assertTrue(native.audit[0]['inferenceAttempted']); self.assertIsNotNone(native.audit[0]['inferenceMs'])
            self.assertEqual(native.audit[0]['nativeLogits'], [])

    def test_wrong_split_or_human_source_never_constructs_model(self):
        for field, replacement in (('split', 'calibration'), ('split', 'holdout'), ('split', 'train'), ('labelSource', 'expert-reviewed')):
            data = fixtures.dataset(); data['cases'][0][field] = replacement
            factory = Mock()
            with self.assertRaises(ValueError): baseline.measure(data, Path('missing'), IDENTITY, backend_factory=factory, capture=capture())
            factory.assert_not_called()

    def test_resealed_typed_logits_tokens_metrics_and_authority_are_recomputed(self):
        original = report()
        changes = [lambda d: d.update(routingEnabled=True), lambda d: d.update(humanLabelsVerified=0),
            lambda d: d['summary']['original'].update(accepted=d['summary']['original']['accepted'] + 1),
            lambda d: d['nativeCalls'][2]['nativeLogits'].__setitem__(0, 1),
            lambda d: d['nativeCalls'][0]['requestOptionIdsInNativeOrder'].reverse(),
            lambda d: d['nativeCalls'][0]['leafTokenIds'].pop(),
            lambda d: d['evaluation']['cases'][1]['result'].update(value=True),
            lambda d: d['evaluation']['model'].update(strictStateDictLoaded=False),
            lambda d: d['evaluation']['model']['profile'].update(dtype='bfloat16')]
        for index, change in enumerate(changes):
            with self.subTest(index=index):
                damaged = copy.deepcopy(original); change(damaged)
                self.assertNotEqual(baseline.canonical_json(damaged), baseline.canonical_json(original))
                with self.assertRaises(ValueError): baseline.verify_report(fixtures.dataset(), reseal(damaged))

    def test_resources_reject_resealed_counter_unit_pid_and_cached_summary(self):
        original = report()
        for change in (lambda d: d['resourceSamples'][1].update(rawMaxRssToBytesMultiplier=1024),
                       lambda d: d['resourceSamples'][1].update(pid=5678),
                       lambda d: d['resourceSamples'][2].update(userCpuSeconds=0),
                       lambda d: d['resourceSummary'].update(maxRssBytesAfterWholeScoring=1)):
            damaged = copy.deepcopy(original); change(damaged)
            with self.assertRaises(ValueError): baseline.verify_report(fixtures.dataset(), reseal(damaged))

    def test_safetensors_full_layout_without_importing_model(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'weights.safetensors'
            def write(header, payload=b'0' * 12):
                raw = json.dumps(header).encode(); path.write_bytes(struct.pack('<Q', len(raw)) + raw + payload)
            header = {'a': {'dtype': 'F32', 'shape': [2], 'data_offsets': [0, 8]},
                      'b': {'dtype': 'F32', 'shape': [1], 'data_offsets': [8, 12]}}
            write(header); self.assertEqual(baseline.weights_header(path)['tensorCount'], 2)
            for modify in (lambda h: h['a'].update(dtype='BF16'), lambda h: h['b'].update(data_offsets=[4, 8]),
                           lambda h: h['a'].update(shape=[True]), lambda h: h['a'].update(shape=[3])):
                bad = copy.deepcopy(header); modify(bad); write(bad)
                with self.assertRaises(ValueError): baseline.weights_header(path)
            write(header, b'0' * 13)
            with self.assertRaises(ValueError): baseline.weights_header(path)

    def test_model_inventory_uses_external_committed_pins_and_refuses_symlinks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); descriptor = copy.deepcopy(baseline.catalog())
            for name in baseline.MODEL_FILES:
                path = root / name; path.parent.mkdir(parents=True, exist_ok=True)
                raw = ('synthetic-' + name).encode(); path.write_bytes(raw)
                descriptor['files'][name] = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
            (root / 'artifact.json').write_text(json.dumps(baseline.artifact(descriptor)))
            baseline.verify_local(root, descriptor)
            path = root / 'README.md'; raw = path.read_bytes(); path.write_bytes(b'tampered')
            descriptor['files']['README.md'] = {'bytes': 8, 'sha256': hashlib.sha256(b'tampered').hexdigest()}
            (root / 'artifact.json').write_text(json.dumps(baseline.artifact(descriptor)))
            # A self-rehashed manifest cannot replace the separately supplied original pins.
            original = copy.deepcopy(descriptor); original['files']['README.md'] = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
            with self.assertRaises(ValueError): baseline.verify_local(root, original)
            path.write_bytes(raw); descriptor = original
            (root / 'artifact.json').write_text(json.dumps(baseline.artifact(descriptor)))
            path.unlink(); path.symlink_to(root / 'config.json')
            with self.assertRaises(ValueError): baseline.verify_local(root, descriptor)

    def test_explicit_download_never_publishes_manifest_after_oversized_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / 'model'; descriptor = baseline.catalog()
            first = sorted(descriptor['files'])[0]
            descriptor['files'][first] = {'bytes': 1, 'sha256': hashlib.sha256(b'x').hexdigest()}
            with patch.object(baseline, 'catalog', return_value=descriptor), \
                 patch.object(baseline.urllib.request, 'urlopen', return_value=io.BytesIO(b'xx')):
                with self.assertRaises(ValueError): baseline.download(target)
            self.assertFalse((target / 'artifact.json').exists())

    def test_real_cli_decodes_pinned_json_and_stdlib_replays_with_no_model(self):
        spec = importlib.util.spec_from_file_location('nanojev_cli_fixture', baseline.ROOT / 'scripts/evaluate-nanojev-development.py')
        cli = importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = b'synthetic measured fixture'
            identity = {'sourceCommit': '1' * 40, 'sourceFiles': {'fixture.py': hashlib.sha256(source).hexdigest()}}
            data = root / 'dataset.json'; raw = json.dumps(fixtures.dataset()).encode(); data.write_bytes(raw)
            output, verified = root / 'docs/private/measure', root / 'docs/private/verify'
            args = ['--model-dir', str(root / 'synthetic-model'), '--dataset', str(data), '--dataset-file-sha256', hashlib.sha256(raw).hexdigest()]
            def synthetic_measure(dataset, directory, source_identity):
                return baseline.measure(dataset, directory, source_identity, backend_factory=lambda _: backend(), capture=capture())
            with patch.object(cli, 'ROOT', root), patch.object(cli, 'frozen_sources', return_value=identity), \
                 patch.object(cli, 'verify_local', return_value={'sha256': '3' * 64}), patch.object(cli, 'weights_header', return_value=layout()), \
                 patch.object(cli, 'measure', side_effect=synthetic_measure):
                self.assertEqual(cli.main(['measure', *args, '--output-dir', str(output)]), 0)
                report_path = output / 'report.json'; report_raw = report_path.read_bytes()
                with patch.object(cli.subprocess, 'check_output', return_value=source):
                    self.assertEqual(cli.main(['verify', *args, '--report', str(report_path),
                        '--report-file-sha256', hashlib.sha256(report_raw).hexdigest(), '--output-dir', str(verified)]), 0)
                self.assertEqual(report_raw, (verified / 'verified-report.json').read_bytes())
                self.assertEqual(json.loads((verified / 'execution.json').read_bytes())['modelCalls'], 0)
