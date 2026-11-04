import copy
import hashlib
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from decision_runtime import implementation_sha256
from decision_runtime.artifacts import sealed
from decision_runtime.contracts import Request, canonical_json, fingerprint
from decision_runtime.mlx_backend import MlxBackend, PROMPT_VERSION
from scripts.lib import decision_public_mlx_baseline as baseline
from scripts.test.test_decision_public_load import context_fixture
from scripts.test.test_decision_nanojev_baseline import capture
from scripts.test import test_decision_gliclass_baseline as typed_fixtures

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('public_mlx_cli', ROOT / 'scripts/evaluate-public-mlx-baseline.py')
cli = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(cli)
IDENTITY = {'sourceCommit': 'a' * 40, 'sourceFiles': {'fixture.py': hashlib.sha256(b'fixture').hexdigest()}}
ENV = {'python': '3.13.12', 'system': 'Darwin', 'machine': 'arm64', 'versions': baseline.package_pins()}
reseal = lambda value: sealed({key: item for key, item in value.items() if key != 'sha256'})


class Tokenizer:
    """Synthetic reversible chunks; no publisher tokenizer or model is imported."""
    def __init__(self): self.by_id = {}; self.by_text = {}; self.chunk_size = 10; self.bad_continuation = False
    def encode(self, text, **kwargs):
        if len(text) == 1 and text.isascii(): self.by_id[ord(text)] = text; return [ord(text)]
        ids = []
        for index in range(0, len(text), self.chunk_size):
            chunk = text[index:index + self.chunk_size]
            token = self.by_text.setdefault(chunk, 1000 + len(self.by_text)); self.by_id[token] = chunk; ids.append(token)
        return ids
    def decode(self, ids): return ''.join(self.by_id[token] for token in ids) + ('corrupt' if self.bad_continuation else '')


class Tensor:
    def __init__(self, values, core): self.values, self.core = values, core
    def __getitem__(self, index): return Tensor(index.values, self.core) if isinstance(index, Tensor) else self
    @property
    def T(self): return self
    def __matmul__(self, other):
        return Tensor(self.core.logits or [10.] + [0.] * (len(other.values) - 1), self.core)
    def astype(self, dtype):
        if dtype != self.core.float32: raise AssertionError('Original scorer lost its float32 output conversion')
        self.core.float32_conversions += 1; return self
    def tolist(self): return self.values


class Core:
    float32 = 'synthetic-float32'
    def __init__(self): self.logits = None; self.forward_calls = 0; self.eval_calls = 0; self.float32_conversions = 0
    def array(self, values): return Tensor(values, self)
    def eval(self, value): self.eval_calls += 1
    def get_active_memory(self): return 1024
    def get_cache_memory(self): return 32
    def get_peak_memory(self): return 2048
    def model(self, value): self.forward_calls += 1; return value


def backend(name='decider-2b'):
    # Execute the actual MlxBackend.score against a synthetic numeric interface.
    value = object.__new__(MlxBackend); entry = baseline.descriptor(name)
    value.tokenizer, value.mx, value.max_tokens, value.load_ms = Tokenizer(), Core(), 2048, 2.
    value.text_model = SimpleNamespace(model=value.mx.model); value.head = SimpleNamespace(weight=Tensor([], value.mx))
    value.identity = {key: entry[key] for key in ('repository', 'revision', 'artifactSha256', 'tokenizerSha256')}
    value.identity.update(promptVersion=PROMPT_VERSION, backend='mlx-qwen3.5-letter-logits',
        mlxVersion=baseline.package_pins()['mlx'], mlxLmVersion=baseline.package_pins()['mlx-lm'],
        implementationSha256=implementation_sha256(), **baseline.PROFILE)
    return value


def report(context=None, name='decider-2b', native=None):
    return baseline.measure(context or context_fixture(), name, Path('synthetic-only-manifest.json'), IDENTITY,
        backend_factory=lambda _: native or backend(name), capture=capture(), env=ENV)


class PublicMlxBaselineTest(unittest.TestCase):
    def test_original_scorer_is_delegated_and_actual_backbone_eval_head_conversion_recorded(self):
        context = context_fixture(); native = backend(); value = report(context, native=native)
        baseline.verify_report(context, value)
        self.assertEqual(native.mx.forward_calls, 2 * len(context['inputs']))
        self.assertEqual(native.mx.eval_calls, native.mx.forward_calls)
        self.assertEqual(native.mx.float32_conversions, native.mx.forward_calls)
        self.assertEqual(value['summary']['nativeInferenceAttempts'], native.mx.forward_calls)
        self.assertTrue(all(row['nativeForwardAttempts'] == 1 and row['nativeInferenceMs'] is not None for row in value['nativeCalls']))

    def test_both_candidates_keep_whole_inventory_including_foreign_context_exclusions(self):
        context = context_fixture(); self.assertEqual(sum(not row['contextEligible'] for row in context['inputs']), 1)
        for name in baseline.NAMES:
            value = report(context, name); baseline.verify_report(context, value)
            self.assertEqual(len(value['cases']), len(context['inputs']))
            self.assertEqual(value['summary']['nativeInferenceAttempts'], 2 * len(context['inputs']))
            self.assertIs(value['classificationAccuracyMeasured'], False); self.assertNotIn('accuracy', value['summary'])

    def test_oversize_requests_preserve_full_two_parts_and_never_enter_backbone_or_eval(self):
        context = context_fixture()
        for row in context['inputs']:
            row['request']['state'] = 'x' * 22000
            row['inputSha256'] = Request.from_dict(row['request']).input_sha256
        native = backend(); value = report(reseal(context), native=native)
        self.assertEqual(native.mx.forward_calls, 0); self.assertEqual(native.mx.eval_calls, 0)
        self.assertEqual(value['summary']['nativeInferenceAttempts'], 0)
        for row in value['nativeCalls']:
            self.assertEqual(len(row['tokenizerEvents']), 2); self.assertEqual(row['continuationDecodedSha256'], [])
            self.assertGreater(sum(len(event['tokenIds']) for event in row['tokenizerEvents']), 2048)
        baseline.verify_report(reseal(context), value)

    def test_scope_labels_calibration_holdout_and_authority_fail_before_factory(self):
        factory = Mock(); context = context_fixture()
        for mutation in (lambda d: d.update(selectedSplit='calibration'), lambda d: d.update(selectedSplit='holdout'),
            lambda d: d.update(referenceLabels=1), lambda d: d.update(referenceLabels=False),
            lambda d: d.update(routingEnabled=True), lambda d: d['inputs'][0].update(expectedOptionId='fake-gold')):
            bad = copy.deepcopy(context); mutation(bad)
            with self.assertRaises(ValueError): baseline.measure(reseal(bad), baseline.NAMES[0], Path('unused'), IDENTITY, backend_factory=factory, env=ENV)
        factory.assert_not_called()

    def test_source_paths_and_dependency_environment_fail_before_factory(self):
        factory = Mock()
        for identity, env in (({'sourceCommit': 'a' * 40, 'sourceFiles': {'../escape': 'b' * 64}}, ENV),
            (IDENTITY, {**ENV, 'versions': {**ENV['versions'], 'mlx': 'wrong'}}), (IDENTITY, {**ENV, 'machine': 'x86_64'})):
            with self.assertRaises(ValueError): baseline.measure(context_fixture(), baseline.NAMES[0], Path('unused'), identity, backend_factory=factory, env=env)
        factory.assert_not_called()

    def test_local_manifest_cannot_override_publisher_file_inventory_or_grant_extra_fields(self):
        name = baseline.NAMES[0]; expected = baseline.descriptor(name)
        original = {key: expected[key] for key in ('name', 'repository', 'revision', 'license', 'role', 'artifactSha256')}
        original.update(schemaVersion=1, snapshot='/synthetic-only-unused', files={key: value['sha256'] for key, value in expected['files'].items()})
        for mutation in (lambda d: d.update(schemaVersion=True), lambda d: d.update(name=baseline.NAMES[1]),
            lambda d: d.update(revision='0' * 40), lambda d: d.update(routingEnabled=True),
            lambda d: d['files'].update(extra='a' * 64), lambda d: d['files'].update(**{'config.json': '0' * 64})):
            bad = copy.deepcopy(original); mutation(bad)
            with patch.object(baseline, 'verify_manifest', return_value=(bad, Path('/never-accessed'))):
                with self.assertRaises(ValueError): baseline.local_manifest(name, Path('unused'))

    def test_source_snapshot_copy_imports_actual_cli_without_model_packages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            names = set(subprocess.check_output(['git', 'ls-files', '--', *baseline.SOURCE_PATHS], cwd=ROOT, text=True).splitlines())
            names.update({'scripts/lib/decision_public_mlx_baseline.py', 'scripts/evaluate-public-mlx-baseline.py',
                'scripts/test/test_decision_public_mlx_baseline.py', 'docs/qualification/local-decisions/baselines/public-mlx-candidates.json'})
            for name in names:
                target = root / name; target.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(ROOT / name, target)
            program = """import runpy, sys
sys.argv = [sys.argv[1], '--help']
try: runpy.run_path(sys.argv[0], run_name='__main__')
except SystemExit as error: assert error.code == 0
else: raise AssertionError('CLI did not exit')
assert not any(name.split('.')[0] in {'mlx','mlx_lm','transformers','tokenizers','numpy','safetensors','torch','gliclass'} for name in sys.modules)
print('modelPackagesImported=0')
"""
            value = subprocess.run([sys.executable, '-I', '-B', '-c', program, str(root / 'scripts/evaluate-public-mlx-baseline.py')],
                cwd=root, capture_output=True, text=True, timeout=30)
            self.assertEqual(value.returncode, 0, value.stderr); self.assertIn('modelPackagesImported=0', value.stdout)

    def test_case_group_order_and_input_bindings_fail_closed(self):
        context = context_fixture(); original = report(context)
        for mutation in (lambda d: d['cases'].pop(), lambda d: d['nativeCalls'].pop(), lambda d: d['cases'].reverse(),
            lambda d: d['cases'][0].update(groupId='changed'), lambda d: d['nativeCalls'][0].update(inputSha256='0' * 64),
            lambda d: d['nativeCalls'][0].update(ordinal=True)):
            bad = copy.deepcopy(original); mutation(bad)
            with self.assertRaises(ValueError): baseline.verify_report(context, reseal(bad))

    def test_full_token_part_hash_labels_continuations_and_token_domain_are_verified(self):
        context = context_fixture(); original = report(context)
        for mutation in (lambda d: d['nativeCalls'][0]['tokenizerEvents'].pop(),
            lambda d: d['nativeCalls'][0]['tokenizerEvents'][0].update(textSha256='0' * 64),
            lambda d: d['nativeCalls'][0]['tokenizerEvents'][0]['tokenIds'].__setitem__(0, True),
            lambda d: d['nativeCalls'][0]['tokenizerEvents'][0]['tokenIds'].__setitem__(0, baseline.VOCAB_SIZE),
            lambda d: d['nativeCalls'][0]['tokenizerEvents'][2].update(tokenIds=[22, 23]),
            lambda d: d['nativeCalls'][0]['continuationDecodedSha256'].__setitem__(0, '0' * 64)):
            bad = copy.deepcopy(original); mutation(bad)
            with self.assertRaises(ValueError): baseline.verify_report(context, reseal(bad))

    def test_real_scorer_refuses_bad_continuation_before_any_gpu_access(self):
        context = context_fixture(); native = backend(); native.tokenizer.bad_continuation = True
        value = report(context, native=native); baseline.verify_report(context, value)
        self.assertEqual(native.mx.forward_calls, 0); self.assertEqual(native.mx.eval_calls, 0)
        self.assertTrue(all(row['reason'] == 'unsupported_tokenizer' for case in value['cases'] for row in (case['result'], case['reversedResult'])))

    def test_logits_policy_cached_counts_and_native_timings_are_recomputed(self):
        context = context_fixture(); original = report(context)
        for mutation in (lambda d: d['nativeCalls'][0]['requestLogits'].__setitem__(0, -10.),
            lambda d: d['cases'][0]['result']['policy'].update(minProbability=.1),
            lambda d: d['summary'].update(nativeInferenceAttempts=0),
            lambda d: d['nativeCalls'][0].update(nativeForwardAttempts=True),
            lambda d: d['nativeCalls'][0].update(nativeInferenceMs=1e8),
            lambda d: d['nativeCalls'][0].update(nativeBackendScoreMs=1e8)):
            bad = copy.deepcopy(original); mutation(bad)
            with self.assertRaises(ValueError): baseline.verify_report(context, reseal(bad))

    def test_typed_false_and_binary64_numeric_score_are_preserved_by_existing_engine(self):
        for example in typed_fixtures.dataset()['cases'][1:]:
            context = context_fixture(); native = backend(); source = context['inputs'][0]
            request = copy.deepcopy(example['request']); request['id'] = source['id']
            if request['kind'] == 'boolean':
                false_index = next(i for i, option in enumerate(request['options']) if option['value'] is False)
                native.mx.logits = [10. if i == false_index else 0. for i in range(len(request['options']))]
            else:
                request['inputFingerprintVersion'] = 'binary64-v1'; native.mx.logits = [10.] + [0.] * (len(request['options']) - 1)
            source['request'] = request; source['inputSha256'] = Request.from_dict(request).input_sha256
            value = report(reseal(context), native=native); result = value['cases'][0]['result']
            baseline.verify_report(reseal(context), value); self.assertEqual(result['status'], 'ok')
            if request['kind'] == 'boolean': self.assertIs(result['value'], False)
            else:
                expected = sum(row['probability'] * option['value'] for row, option in zip(result['distribution'], request['options'], strict=True))
                self.assertEqual(result['value'], expected)

    def test_authority_model_profile_resources_and_allocator_samples_fail_closed(self):
        context = context_fixture(); original = report(context)
        for mutation in (lambda d: d.update(routingEnabled=True), lambda d: d.update(classificationAccuracyMeasured=True),
            lambda d: d.update(referenceLabelsVerified=False), lambda d: d['model'].update(maxInputTokens=True),
            lambda d: d['model'].update(allocatorWiredLimitBytes=0), lambda d: d['model'].update(routingEnabled=True),
            lambda d: d['environment']['versions'].update(tokenizers='wrong'), lambda d: d['resourceSamples'][1].update(pid=999),
            lambda d: d['mlxMemorySamples'][1].update(peakBytes=True), lambda d: d['mlxMemorySamples'][1].update(peakBytes=1)):
            bad = copy.deepcopy(original); mutation(bad)
            with self.assertRaises(ValueError): baseline.verify_report(context, reseal(bad))

    def test_actual_cli_stdlib_replay_external_byte_pins_and_no_partial_outputs(self): self.run_cli_case()
    def test_actual_cli_publication_bound_includes_canonical_json_newline(self): self.run_cli_case(byte_bound=True)

    def run_cli_case(self, byte_bound=False):
        context = context_fixture(); value = report(context)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); private = root / 'docs/private'; private.mkdir(parents=True)
            context_path = private / 'context.json'; context_raw = (canonical_json(context) + '\n').encode(); context_path.write_bytes(context_raw)
            report_path = private / 'report.json'; report_raw = (canonical_json(value) + '\n').encode(); report_path.write_bytes(report_raw)
            manifest_path = private / 'manifest.json'; manifest_raw = b'{}\n'; manifest_path.write_bytes(manifest_raw)
            output = private / 'verified'; sha = lambda raw: hashlib.sha256(raw).hexdigest()
            args = ['verify', '--candidate', baseline.NAMES[0], '--context-profile', str(context_path), '--context-file-sha256', sha(context_raw),
                '--manifest', str(manifest_path), '--manifest-file-sha256', sha(manifest_raw), '--report', str(report_path),
                '--report-file-sha256', sha(report_raw), '--output-dir', str(output)]
            with patch.object(cli, 'ROOT', root), patch.object(cli, 'frozen_sources', return_value=IDENTITY), patch.object(cli, 'historical_context_sources'), \
                 patch.object(cli, 'local_manifest', return_value=({'artifactSha256': value['model']['artifactSha256']}, private)), \
                 patch.object(cli.subprocess, 'check_output', return_value=b'fixture'), patch.object(cli, 'measure') as measure:
                if byte_bound:
                    args = args[:args.index('--report')] + ['--output-dir', str(output)]; args[0] = 'measure'; measure.return_value = value
                    with patch.object(cli, 'MAX_REPORT_BYTES', len(report_raw) - 1): self.assertEqual(cli.main(args), 1)
                    self.assertFalse(output.exists()); measure.assert_called_once()
                else:
                    self.assertEqual(cli.main(args), 0); self.assertEqual((output / 'verified-report.json').read_bytes(), report_raw)
                    args[-1] = str(private / 'wrong-pin'); args[args.index('--manifest-file-sha256') + 1] = '0' * 64
                    self.assertEqual(cli.main(args), 1); self.assertFalse((private / 'wrong-pin').exists()); measure.assert_not_called()


if __name__ == '__main__': unittest.main()
