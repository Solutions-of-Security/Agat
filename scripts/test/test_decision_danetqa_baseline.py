"""Native scorer interfaces and external-reference replay contracts; no real model inference."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from decision_runtime.artifacts import sealed
from decision_runtime.contracts import canonical_json
from scripts.lib import decision_danetqa_corpus as corpus, decision_danetqa_baseline as baseline
from scripts.test import test_decision_danetqa_corpus as corpus_fixtures
from scripts.test import test_decision_gliclass_baseline as gl_fixtures, test_decision_nanojev_baseline as nano_fixtures
from scripts.test import test_decision_public_mlx_baseline as mlx_fixtures

SPEC = importlib.util.spec_from_file_location('danetqa_baseline_cli', ROOT / 'scripts/evaluate-danetqa-baseline.py')
cli = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(cli)
reseal = lambda value: sealed({key: item for key, item in value.items() if key != 'sha256'})
sha = lambda raw: hashlib.sha256(raw).hexdigest()
IDENTITY = mlx_fixtures.IDENTITY


def backend(name):
    return gl_fixtures.backend() if name == 'gliclass' else nano_fixtures.backend() if name == 'nanojev' else mlx_fixtures.backend(name)


def report(name='gliclass', native=None):
    raw, license, source = corpus_fixtures.fixture()
    with patch.object(corpus, 'descriptor', return_value=source):
        dataset = corpus.build_dataset(raw, license)
        value = baseline.measure(dataset, raw, license, name, Path('synthetic-only-model'), IDENTITY,
            backend_factory=lambda _: native or backend(name), capture=nano_fixtures.capture(), env=mlx_fixtures.ENV)
        baseline.verify_report(dataset, raw, license, value)
    return dataset, raw, license, source, value


class DaNetQABaselineTests(unittest.TestCase):
    def test_all_four_actual_scorer_interfaces_receive_whole_cases_and_both_orders(self):
        for name in baseline.NAMES:
            native = backend(name); dataset, raw, license, source, value = report(name, native)
            self.assertEqual(value['summary']['caseCount'], 2)
            self.assertEqual(value['summary']['dependentOrderVariants'], 4)
            self.assertEqual(value['summary']['nativeInferenceAttempts'], 4)
            self.assertEqual(value['summary']['publishedReferenceLabels'], 2)
            self.assertTrue(value['classificationAccuracyMeasured']); self.assertFalse(value['classificationAccuracyQualified'])
            self.assertEqual(value['localIndependentHumanReviews'], 0); self.assertFalse(value['humanLabelsVerified'])
            if name in baseline.NAMES[2:]:
                self.assertEqual(native.mx.forward_calls, 4); self.assertEqual(native.mx.eval_calls, 4)
                self.assertEqual(native.mx.float32_conversions, 4)

    def test_typed_false_true_values_and_native_boolean_head_order_survive_both_orders(self):
        for name in baseline.NAMES:
            dataset, packed, license_raw, source, value = report(name)
            for row in value['evaluation']['cases']:
                for key in ('result', 'reversedResult'):
                    result = row[key]
                    if result['status'] == 'ok':
                        self.assertIs(result['value'], result['selectedOptionId'] == 'true')
                        self.assertIs(type(result['value']), bool)
                    else: self.assertIsNone(result['value'])
            if name == 'nanojev':
                self.assertEqual(value['summary']['changedArgmaxBothComputed'], 0)
                for call in value['nativeCalls']:
                    self.assertEqual(call['nativeCandidateIds'], ['false', 'true'])
                    self.assertEqual(call['requestOptionIdsInNativeOrder'], ['false', 'true'])
                    self.assertEqual(call['nativeLogits'], [0., -6.])
                    self.assertEqual(len(call['leafTokenIds']), 1)
                self.assertEqual(value['nativeCalls'][0]['requestLogits'], [0., -6.])
                self.assertEqual(value['nativeCalls'][1]['requestLogits'], [-6., 0.])

    def test_resealed_numeric_boolean_aliases_fail_before_factory_and_in_native_replay(self):
        dataset, packed, license_raw, source, original = report('nanojev')
        factory = Mock()
        for mutate in (lambda v:v['cases'][0].update(publishedBooleanReference=0),
                lambda v:v['cases'][0]['request']['options'][0].update(value=0)):
            bad = copy.deepcopy(dataset); mutate(bad)
            with patch.object(corpus, 'descriptor', return_value=source), self.assertRaises(ValueError):
                baseline.measure(reseal(bad), packed, license_raw, 'nanojev', Path('unused'), IDENTITY, backend_factory=factory)
        factory.assert_not_called()
        for key in ('result', 'reversedResult'):
            bad = copy.deepcopy(original); bad['evaluation']['cases'][0][key]['value'] = 0
            with patch.object(corpus, 'descriptor', return_value=source), self.assertRaises(ValueError):
                baseline.verify_report(dataset, packed, license_raw, reseal(bad))

    def test_source_gold_scope_and_authority_mutations_refuse_before_factory(self):
        dataset, raw, license, source, value = report(); factory = Mock()
        for mutate in (lambda v:v['cases'][0].update(expectedOptionId='true'),
                lambda v:v['cases'][0].update(split='holdout'), lambda v:v.update(localIndependentHumanReviews=True),
                lambda v:v.update(routingEnabled=True), lambda v:v['cases'].pop()):
            bad = copy.deepcopy(dataset); mutate(bad)
            with patch.object(corpus, 'descriptor', return_value=source), self.assertRaises(ValueError):
                baseline.measure(reseal(bad), raw, license, 'gliclass', Path('unused'), IDENTITY, backend_factory=factory)
        factory.assert_not_called()

    def test_unexpected_model_source_or_mlx_stack_refuses_before_factory(self):
        dataset, raw, license, source, value = report(); factory = Mock()
        with patch.object(corpus, 'descriptor', return_value=source):
            for identity in ({}, {'sourceCommit': 'bad', 'sourceFiles': {'fixture': 'a'*64}}):
                with self.assertRaises(ValueError): baseline.measure(dataset, raw, license, 'gliclass', Path('unused'), identity, backend_factory=factory)
            bad = copy.deepcopy(mlx_fixtures.ENV); bad['versions']['mlx'] = 'unlocked'
            with self.assertRaises(ValueError): baseline.measure(dataset, raw, license, 'decider-2b', Path('unused'), IDENTITY,
                backend_factory=factory, capture=nano_fixtures.capture(), env=bad)
        factory.assert_not_called()

    def test_all_context_refusals_remain_in_accuracy_denominator_and_have_zero_forwards(self):
        for name in baseline.NAMES:
            native = backend(name)
            if name in baseline.NAMES[:2]: native.tokenizer.extra = 1100
            else: native.max_tokens = 1  # Real score refusal; profile requires original 2048-token recorded bound.
            if name in baseline.NAMES[2:]:
                raw, license, source = corpus_fixtures.fixture(); source['development']['cases'] = 2
                # Force full token arrays over the real declared profile using reversible single-character chunks.
                rows = [{'passage': 'А' * 2200, 'question': 'Вопрос?', 'label': False, 'idx': 0},
                        {'passage': 'Б' * 2200, 'question': 'Вопрос?', 'label': True, 'idx': 1}]
                raw, license, source = corpus_fixtures.fixture(rows); native.max_tokens = 2048; native.tokenizer.chunk_size = 1
                with patch.object(corpus, 'descriptor', return_value=source):
                    dataset = corpus.build_dataset(raw, license)
                    value = baseline.measure(dataset, raw, license, name, Path('unused'), IDENTITY,
                        backend_factory=lambda _:native, capture=nano_fixtures.capture(), env=mlx_fixtures.ENV)
            else: dataset, raw, license, source, value = report(name, native)
            self.assertEqual(value['summary']['nativeInferenceAttempts'], 0)
            self.assertEqual(value['summary']['original']['attempted'], 2)
            self.assertEqual(value['summary']['original']['accuracyAllAttempts'], 0)
            self.assertEqual(value['summary']['reversed']['backendErrors'], 2)

    def test_cached_metrics_gold_correctness_and_policy_are_rebuilt(self):
        dataset, raw, license, source, original = report('nanojev')
        changes = [lambda v:v['summary']['original'].update(correct=99), lambda v:v['summary']['reversed'].update(nllScored=0),
            lambda v:v['evaluation']['metrics'].update(ece10Scored=0),
            lambda v:v['evaluation']['cases'][0].update(correct=not v['evaluation']['cases'][0]['correct']),
            lambda v:v['evaluation']['cases'][0].update(expectedOptionId='true'),
            lambda v:v['evaluation']['policy'].update(minProbability=.1),
            lambda v:v['evaluation']['cases'][0]['result']['distribution'][0].update(probability=.5),
            lambda v:v['nativeCalls'][0]['requestLogits'].__setitem__(0, 123.)]
        for change in changes:
            bad = copy.deepcopy(original); change(bad)
            self.assertNotEqual(canonical_json(bad), canonical_json(original))
            with patch.object(corpus, 'descriptor', return_value=source), self.assertRaises(ValueError): baseline.verify_report(dataset, raw, license, reseal(bad))

    def test_native_inventory_order_groups_and_token_arrays_are_closed(self):
        for name in ('gliclass', 'decider-2b'):
            dataset, raw, license, source, original = report(name)
            changes = [lambda v:v['nativeCalls'].pop(), lambda v:v['evaluation']['cases'].reverse(),
                lambda v:v['evaluation']['cases'][0].update(groupId='fake'), lambda v:v['nativeCalls'][0].update(ordinal=True),
                lambda v:v['nativeCalls'][0].update(inputSha256='0'*64)]
            changes += [lambda v:v['nativeCalls'][0]['tokenIds'].__setitem__(0, True)] if name=='gliclass' else [
                lambda v:v['nativeCalls'][0]['tokenizerEvents'][0]['tokenIds'].__setitem__(0, baseline.mlx.VOCAB_SIZE),
                lambda v:v['nativeCalls'][0]['continuationDecodedSha256'].__setitem__(0, '0'*64)]
            for change in changes:
                bad = copy.deepcopy(original); change(bad)
                with patch.object(corpus, 'descriptor', return_value=source), self.assertRaises(ValueError): baseline.verify_report(dataset, raw, license, reseal(bad))

    def test_resources_allocator_scope_and_local_authority_cannot_be_fabricated(self):
        for name in ('gliclass', 'qwen3.5-2b-base'):
            dataset, raw, license, source, original = report(name)
            changes = [lambda v:v.update(localIndependentHumanReviews=False), lambda v:v.update(humanLabelsVerified=True),
                lambda v:v.update(qualifiedForRouting=True), lambda v:v.update(classificationAccuracyQualified=True),
                lambda v:v['resourceSamples'][1].update(pid=9876), lambda v:v['resourceSummary'].update(maxRssBytesAfterBackend=1),
                lambda v:v.update(modelLoadMs=True), lambda v:v['evaluation'].update(createdAt='2026-10-11T00:00:00')]
            changes += [lambda v:v.update(mlxMemorySamples=[{}])] if name=='gliclass' else [
                lambda v:v['mlxMemorySamples'][0].update(activeBytes=True), lambda v:v['mlxMemorySamples'].pop(),
                lambda v:v['environment']['versions'].update(mlx='unlocked')]
            for change in changes:
                bad = copy.deepcopy(original); change(bad)
                with patch.object(corpus, 'descriptor', return_value=source), self.assertRaises(ValueError): baseline.verify_report(dataset, raw, license, reseal(bad))

    def test_actual_isolated_cli_help_imports_no_model_packages(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in baseline.SOURCE_PATHS:
                incoming = ROOT / name; target = root / name; target.parent.mkdir(parents=True, exist_ok=True)
                if incoming.is_dir(): shutil.copytree(incoming, target, ignore=shutil.ignore_patterns('__pycache__'), dirs_exist_ok=True)
                else: shutil.copy2(incoming, target)
            program = "import runpy,sys;sys.argv=[sys.argv[1],'--help'];\ntry:runpy.run_path(sys.argv[0],run_name='__main__')\nexcept SystemExit as e:assert e.code==0\nassert not {'mlx','mlx_lm','torch','transformers','numpy','gliclass'} & sys.modules.keys()"
            result = subprocess.run([sys.executable,'-I','-B','-c',program,str(root/'scripts/evaluate-danetqa-baseline.py')],cwd=root,capture_output=True,text=True,timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_metrics_match_independent_two_class_math_and_orders_are_not_pooled(self):
        import math
        _, _, _, _, value = report('nanojev')
        probability = 1 / (1 + math.exp(-6))
        for order in ('original', 'reversed'):
            actual = value['summary'][order]
            self.assertEqual(actual['attempted'], 2); self.assertEqual(actual['correct'], 1)
            self.assertEqual(actual['accuracyAllAttempts'], .5)
            self.assertEqual(actual['accepted'], 2); self.assertEqual(actual['acceptedErrors'], 1)
            self.assertEqual(actual['coverage'], 1); self.assertEqual(actual['selectiveRisk'], .5)
            self.assertAlmostEqual(actual['macroF1Scored'], 1/3)
            self.assertAlmostEqual(actual['nllScored'], -(math.log(probability) + math.log(1-probability))/2)
            self.assertAlmostEqual(actual['brierScored'], probability**2 + (1-probability)**2)
            self.assertAlmostEqual(actual['ece10Scored'], probability - .5)
        self.assertEqual(value['summary']['changedArgmaxBothComputed'], 0)
        self.assertEqual(value['summary']['bothOrdersComputed'], 2)
        self.assertEqual(value['summary']['dependentOrderVariants'], 4)
        self.assertFalse(value['statisticalIndependenceVerified'])

    def test_cli_positive_replay_is_byte_identical_and_source_and_pin_failures_leave_no_output(self):
        dataset, packed, license_raw, source, value = report()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); private = root / 'docs/private'; private.mkdir(parents=True)
            inputs = {'archive': packed, 'license': license_raw, 'dataset': (canonical_json(dataset) + '\n').encode(),
                      'report': (canonical_json(value) + '\n').encode()}
            args = ['verify', '--candidate', 'gliclass', '--model-dir', str(private/'fake-model')]
            for name, raw in inputs.items():
                path = private/name; path.write_bytes(raw); args += ['--'+name, str(path), '--'+name+'-file-sha256', sha(raw)]
            with patch.object(corpus, 'descriptor', return_value=source), patch.object(cli, 'ROOT', root), \
                    patch.object(cli, 'frozen_sources', return_value=IDENTITY), \
                    patch.object(cli, 'model_input', return_value=(private/'fake-model', {}, None)), \
                    patch.object(cli.subprocess, 'check_output', return_value=b'measured-source'):
                # The independent source hash is part of the recorded identity and is checked through git show.
                pinned = copy.deepcopy(value); pinned['sourceIdentity']['sourceFiles'] = {'fixture.py':sha(b'measured-source')}
                current = {**IDENTITY, 'sourceFiles':pinned['sourceIdentity']['sourceFiles']}
                raw = (canonical_json(reseal(pinned))+'\n').encode(); (private/'report').write_bytes(raw)
                args[args.index('--report-file-sha256')+1] = sha(raw)
                with patch.object(cli, 'frozen_sources', return_value=current):
                    out=private/'verified'; self.assertEqual(cli.main([*args,'--output-dir',str(out)]),0)
                    self.assertEqual((out/'verified-report.json').read_bytes(),raw)
                    self.assertEqual(json.loads((out/'execution.json').read_bytes())['modelCalls'],0)
                    self.assertEqual(out.stat().st_mode & 0o777,0o700)
                    self.assertEqual((out/'verified-report.json').stat().st_mode & 0o777,0o600)
                    bad=list(args); bad[bad.index('--dataset-file-sha256')+1]='0'*64
                    denied=private/'wrong-dataset-pin'; self.assertEqual(cli.main([*bad,'--output-dir',str(denied)]),1)
                    self.assertFalse(denied.exists())
                    with patch.object(cli.subprocess,'check_output',return_value=b'changed-source'):
                        denied=private/'changed-source'; self.assertEqual(cli.main([*args,'--output-dir',str(denied)]),1)
                        self.assertFalse(denied.exists())

    def test_cli_publication_byte_limit_includes_newline_before_creating_output(self):
        dataset, packed, license_raw, source, value = report()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); private=root/'docs/private'; private.mkdir(parents=True)
            args=['measure','--candidate','gliclass','--model-dir',str(private/'fake-model')]
            for name,raw in {'archive':packed,'license':license_raw,'dataset':(canonical_json(dataset)+'\n').encode()}.items():
                path=private/name; path.write_bytes(raw); args += ['--'+name,str(path),'--'+name+'-file-sha256',sha(raw)]
            exact=len((canonical_json(value)+'\n').encode())
            with patch.object(corpus,'descriptor',return_value=source), patch.object(cli,'ROOT',root), \
                    patch.object(cli,'frozen_sources',return_value=IDENTITY), \
                    patch.object(cli,'model_input',return_value=(private/'fake-model',{},None)), \
                    patch.object(cli,'measure',return_value=value):
                denied=private/'one-byte-too-small'
                with patch.object(cli,'MAX_REPORT_BYTES',exact-1):
                    self.assertEqual(cli.main([*args,'--output-dir',str(denied)]),1); self.assertFalse(denied.exists())
                allowed=private/'exact-bound'
                with patch.object(cli,'MAX_REPORT_BYTES',exact):
                    self.assertEqual(cli.main([*args,'--output-dir',str(allowed)]),0)
                    self.assertEqual((allowed/'report.json').stat().st_size,exact)

    def test_model_input_flags_are_candidate_specific_and_manifests_have_external_pins(self):
        from types import SimpleNamespace
        for args in (SimpleNamespace(candidate='gliclass',model_dir=None,manifest=Path('unused'),manifest_file_sha256='a'*64),
                SimpleNamespace(candidate='decider-2b',model_dir=Path('unused'),manifest=None,manifest_file_sha256=None),
                SimpleNamespace(candidate='decider-2b',model_dir=None,manifest=Path('unused'),manifest_file_sha256=None)):
            with self.assertRaises(ValueError): cli.model_input(args)


if __name__ == '__main__': unittest.main()
