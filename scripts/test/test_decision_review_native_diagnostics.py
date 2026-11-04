"""Whole-inventory label comparisons and refusal before publishing diagnostics."""
from copy import deepcopy
import hashlib
import http.client
import importlib.util
import json
import math
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from decision_runtime.annotations import finalize_reviews, prepare_review
from decision_runtime.artifacts import sealed
from decision_runtime.contracts import Policy
from decision_runtime.engine import DecisionEngine, Scores
from scripts.lib.decision_public_development_review import review_outputs
from scripts.lib.decision_review_adjudication import encoded
from scripts.lib import decision_review_bundle as bundles
from scripts.lib import decision_review_native_diagnostics as diagnostics
from scripts.lib.decision_review_finalization import prepare_finalization
from scripts.lib.decision_review_pair import compare_pair
from scripts.lib.decision_review_session import AUTHORITY_FLAGS
from scripts.test import test_decision_public_permutation_diagnostic as native
from workers.local_decisions import LocalDecisionClient

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('finalized_native_diagnostic_cli', ROOT/'scripts/diagnose-finalized-decision-development.py')
cli = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(cli)
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
reseal = lambda value: sealed({key: item for key, item in value.items() if key != 'sha256'})


def reviews(context, labels=None, pool=None):
    blank = (prepare_review(pool, context['splitSeed']) if pool is not None
             else review_outputs(context)['review.first.blank.json'])
    answers = labels or ['incident'] * len(blank['labels'])
    result = []
    for name in ('synthetic-first', 'synthetic-second'):
        review = deepcopy(blank); review.update(reviewerId=name, reviewedAt='2026-10-10T10:00:01.000Z')
        for label, answer in zip(review['labels'], answers, strict=True):
            label.update(expectedOptionId=answer, rationale='Synthetic test answer; no human review performed.')
        result.append(review)
    return blank, result


def bundle_fixture(root, context, labels=None, pool=None):
    private = root/'docs/private'; private.mkdir(parents=True, exist_ok=True)
    blank, completed = reviews(context, labels, pool)
    bindings = []
    for index, saved in enumerate(completed):
        count = len(saved['labels']); initial_raw, saved_raw = encoded(blank), encoded(saved)
        session = sealed({'schemaVersion': 'agat.decision.blind-review-session.v2', 'status': 'completed',
            'startedAt': '2026-10-10T10:00:00.000Z', 'finishedAt': saved['reviewedAt'], 'endReason': 'submitted',
            'sourceCommit': 'a'*40, 'sourceFiles': {'scripts/review-decision-pool.py': 'b'*64},
            'inputReviewFileSha256': hashlib.sha256(initial_raw).hexdigest(),
            'outputReviewFileSha256': hashlib.sha256(saved_raw).hexdigest(),
            'poolSha256': blank['poolSha256'], 'reviewerId': saved['reviewerId'], 'existingAnswers': 0,
            'newAnswers': count, 'revisedAnswers': 0, 'clearedAnswers': 0, 'remainingAnswers': 0,
            'submissionConfirmed': True, 'failureType': None, 'modelCalls': 0, 'qualification': 'not_assessed',
            **{flag: False for flag in AUTHORITY_FLAGS}})
        binding = []
        for name, raw in (('session', encoded(session)), ('input', initial_raw), ('output', saved_raw)):
            path = private/f'{index}-{name}.json'; path.write_bytes(raw); binding.extend((path, sha(path)))
        bindings.append(tuple(binding))
    comparison = private/'comparison.json'; comparison.write_bytes(encoded(compare_pair(*bindings)))
    finalized = prepare_finalization(*bindings, comparison, sha(comparison))
    report, dataset = private/'finalization.json', private/'dataset.json'
    report.write_bytes(encoded(finalized['finalization.json'])); dataset.write_bytes(encoded(finalized['dataset.json']))
    manifest, files = bundles.prepare_bundle(*bindings, comparison, sha(comparison), report, sha(report), dataset, sha(dataset))
    return bundles.write_bundle(root, private/'bundle', manifest, files)/'bundle.json'


def bindings(root):
    commit = native.repository(root); directory, pins = native.write_fixture(root, commit)
    context = json.loads((directory/'context.json').read_bytes())
    bundle = bundle_fixture(root, context)
    return (bundle, sha(bundle), directory, directory/'context.json', directory/'permutation.json'), pins


def arguments(binding, pins, output):
    return ['--bundle', str(binding[0]), '--bundle-file-sha256', binding[1], '--evidence-dir', str(binding[2]),
        '--context-profile', str(binding[3]), '--permutation-context', str(binding[4]),
        '--context-profile-file-sha256', pins['context_sha'], '--permutation-context-file-sha256', pins['permutation_sha'],
        '--plan-file-sha256', pins['plan_sha'], '--result-file-sha256', pins['result_sha'], '--output-dir', str(output)]


class ReviewNativeDiagnosticTest(unittest.TestCase):
    def setUp(self):
        self.context, self.permutation = native.corpus()
        self.inputs = self.permutation['inputs']; self.clock = native.Clock()
        self.phase = native.diagnostic.run_phase(None, self.inputs, self.context['profile'], clock=self.clock,
            measure=native.measurements(self.inputs, self.context['profile'], self.clock))
        _, pair = reviews(self.context); self.dataset = finalize_reviews(*pair)

    def analyse(self, dataset=None, inputs=None, phase=None):
        return diagnostics.analyse(dataset or self.dataset, self.context, inputs or self.inputs, phase or self.phase)

    def test_all_orders_preserve_original_cases_and_context_rejection_denominators(self):
        result = self.analyse(); count = len(self.context['inputs']); scored = self.context['contextEligibleCases']
        self.assertEqual(result['caseCount'], count); self.assertEqual(result['nativeVariantCount'], count*7)
        self.assertEqual(result['optionOrderCount'], 7); self.assertEqual(len(result['cases']), count)
        for order in result['orders']:
            value = order['metricsVsSubmittedLabels']; self.assertEqual(value['caseCount'], count)
            self.assertEqual(value['computedCaseCount'], scored)
            self.assertEqual(value['contextRejectedCaseCount'], count-scored)
            self.assertEqual(value['argmaxMatchesSubmittedLabel'], scored)
            self.assertEqual(value['argmaxAgreementAllCases'], scored/count)
            self.assertEqual(value['argmaxAgreementComputedCases'], 1)
            self.assertEqual(value['acceptedCoverageAllCases'], scored/count)
            self.assertEqual(value['acceptedDisagreementFraction'], 0)
        for case in result['cases']:
            self.assertEqual(len(case['orders']), 7)
            if case['id'] == self.context['inputs'][-1]['id']:
                self.assertTrue(all(row['submittedLabelProbability'] is None and not row['matchesSubmittedLabel'] for row in case['orders']))

    def test_confusion_and_proper_scores_use_submitted_labels_not_model_pseudo_labels(self):
        labels = ['access'] + ['incident']*(len(self.context['inputs'])-1)
        _, pair = reviews(self.context, labels); result = self.analyse(finalize_reviews(*pair))
        value = result['orders'][0]['metricsVsSubmittedLabels']; first = self.phase['rows'][0]['observation']['result']
        probability = next(row['probability'] for row in first['distribution'] if row['id'] == 'access')
        self.assertGreater(value['nllComputedVsSubmittedLabels'], 1)
        self.assertGreater(value['brierComputedVsSubmittedLabels'], .3)
        originals = [row for row in self.phase['rows'] if row['orderId'] == 'rotation_0' and row['status'] != 'error']
        targets = {case['id']: target for case, target in zip(self.context['inputs'], labels, strict=True)}
        nll = brier = 0.
        for row in originals:
            probabilities = {item['id']: item['probability'] for item in row['observation']['result']['distribution']}
            target = targets[row['sourceCaseId']]
            nll -= math.log(probabilities[target])
            brier += sum((value-int(key == target))**2 for key, value in probabilities.items())
        self.assertAlmostEqual(value['nllComputedVsSubmittedLabels'], nll/len(originals))
        self.assertAlmostEqual(value['brierComputedVsSubmittedLabels'], brier/len(originals))
        self.assertEqual(value['acceptedDisagreementCaseCount'], 1)
        self.assertEqual(value['acceptedDisagreementFraction'], 1/self.context['contextEligibleCases'])
        self.assertEqual(result['cases'][0]['expectedOptionId'], 'access')
        self.assertEqual(result['cases'][0]['orders'][0]['submittedLabelProbability'], probability)
        self.assertEqual(result['orders'][0]['groupsWithAcceptedDisagreement'], [self.dataset['cases'][0]['groupId']])

    def test_abstained_disagreement_stays_visible_without_becoming_an_accepted_error(self):
        context = self.context; cases = {row['id']: row for row in self.inputs}
        first_id = context['inputs'][0]['id']; clock = native.Clock()
        class SyntheticBackend:
            identity = context['profile']['model']
            def score(self, request):
                variant = cases[request.id]
                high = 2. if variant['sourceCaseId'] == first_id else 8.
                return Scores([high if option.id == 'incident' else 0. for option in request.options], variant['inputTokens'])
        engine = DecisionEngine(SyntheticBackend(), Policy.from_dict({key: value for key, value in context['profile']['policy'].items() if key != 'sha256'}))
        measured = native.measurements(self.inputs, context['profile'], clock)
        def measure(*args):
            row = measured(*args); request = args[1]
            if cases[request.id]['contextEligible']:
                result = engine.decide(request.to_dict()); result['durationMs'] = 5.
                row.update(status=result['status'], reason=result['reason']); row['observation']['result'] = result
            return row
        phase = native.diagnostic.run_phase(None, self.inputs, context['profile'], clock=clock, measure=measure)
        _, pair = reviews(context, ['access']+['incident']*(len(context['inputs'])-1))
        result = self.analyse(finalize_reviews(*pair), phase=phase)
        for order in result['orders']:
            value = order['metricsVsSubmittedLabels']
            self.assertEqual(value['abstainedCaseCount'], 1)
            self.assertEqual(value['argmaxMatchesSubmittedLabel'], context['contextEligibleCases']-1)
            self.assertEqual(value['acceptedDisagreementCaseCount'], 0)
            self.assertEqual(value['acceptedCaseCount'], context['contextEligibleCases']-1)

    def test_group_denominator_counts_shared_sources_once(self):
        context = deepcopy(self.context); context['inputs'][1]['groupId'] = context['inputs'][0]['groupId']
        context['developmentGroups'] -= 1; context = reseal(context)
        permutation = native.variants(context); original = {row['id']: row for row in self.context['inputs']}
        rows = [{'id': row['id'], 'inputSha256': row['inputSha256'], 'partTokens': original[row['sourceCaseId']]['partTokens'],
                 'inputTokens': original[row['sourceCaseId']]['inputTokens'], 'labelContinuationsVerified': True} for row in permutation]
        permutation = native.receipt(context, rows); clock = native.Clock()
        phase = native.diagnostic.run_phase(None, permutation['inputs'], context['profile'], clock=clock,
            measure=native.measurements(permutation['inputs'], context['profile'], clock))
        _, pair = reviews(context); result = diagnostics.analyse(finalize_reviews(*pair), context, permutation['inputs'], phase)
        self.assertEqual(result['groupCount'], len(context['inputs'])-1)
        self.assertEqual(result['orders'][0]['acceptedGroupCount'], context['contextEligibleCases']-1)

    def test_variant_specific_context_rejection_keeps_each_order_whole(self):
        inputs = deepcopy(self.inputs); target = next(row for row in inputs if row['orderId'] == 'rotation_1')
        target.update(inputTokens=2049, partTokens=[1949, 100], contextEligible=False, contextExclusionReason='context_too_long')
        clock = native.Clock()
        phase = native.diagnostic.run_phase(None, inputs, self.context['profile'], clock=clock,
            measure=native.measurements(inputs, self.context['profile'], clock))
        result = self.analyse(inputs=inputs, phase=phase)
        counts = {row['orderId']: row['metricsVsSubmittedLabels'] for row in result['orders']}
        self.assertEqual(counts['rotation_1']['caseCount'], len(self.context['inputs']))
        self.assertEqual(counts['rotation_1']['computedCaseCount'], counts['rotation_0']['computedCaseCount']-1)
        self.assertEqual(counts['rotation_1']['contextRejectedCaseCount'], counts['rotation_0']['contextRejectedCaseCount']+1)

    def test_partial_extra_reordered_or_rebound_dataset_is_refused(self):
        for change in (lambda d: d['cases'].pop(), lambda d: d['cases'].reverse(),
                       lambda d: d['cases'][0].update(split='holdout'),
                       lambda d: d['annotation'].update(splitSeed='different'),
                       lambda d: d['cases'][0]['request'].update(state='Changed source'),
                       lambda d: d['cases'][0]['provenance'].update(reference='Changed provenance')):
            dataset = deepcopy(self.dataset); change(dataset)
            with self.subTest(change=change), self.assertRaises(ValueError): self.analyse(dataset)

    def test_missing_whole_order_partial_phase_or_rehashed_bad_result_is_refused(self):
        shortened = [row for row in self.inputs if row['orderId'] != 'original_repeat']
        with self.assertRaises(ValueError): self.analyse(inputs=shortened)
        for change in (lambda p: p['rows'].pop(), lambda p: p.update(complete=False),
                       lambda p: p['rows'][0]['observation']['result']['distribution'][0].update(probability=.5)):
            phase = deepcopy(self.phase); change(phase)
            with self.subTest(change=change), self.assertRaises(ValueError): self.analyse(phase=phase)

    def test_actual_complete_pinned_native_and_bundle_replay_never_call_model_or_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); binding, pins = bindings(root)
            before = {path: path.read_bytes() for path in root.glob('docs/private/**/*.json')}
            actual_popen = subprocess.Popen
            def only_git(command, **kwargs):
                self.assertEqual(command[0], 'git'); self.assertIn(command[1], ('ls-tree', 'archive'))
                self.assertFalse(kwargs.get('shell', False)); return actual_popen(command, **kwargs)
            with (patch.object(http.client, 'HTTPConnection') as network, patch.object(LocalDecisionClient, 'decide') as model,
                  patch.object(subprocess, 'Popen', side_effect=only_git)):
                result = diagnostics.diagnose(root, *binding, **pins); network.assert_not_called(); model.assert_not_called()
            self.assertEqual(result['status'], 'development_diagnostic_only'); self.assertEqual(result['comparison']['caseCount'], 6)
            self.assertTrue(result['submittedLabelMetricsComputed']); self.assertEqual(result['referenceLabelsVerified'], 0)
            for flag in (*AUTHORITY_FLAGS, 'classificationAccuracyMeasured', 'calibrationPerformed', 'holdoutEvaluated', 'thresholdsTuned'):
                self.assertIs(result[flag], False)
            self.assertEqual(before, {path: path.read_bytes() for path in before})

    def test_wrong_raw_pins_and_source_review_native_mutations_refuse_publication(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); binding, pins = bindings(root)
            for key in pins:
                bad = {**pins, key: '0'*64}
                with self.subTest(pin=key), self.assertRaises(ValueError): diagnostics.diagnose(root, *binding, **bad)
            bad = list(binding); bad[1] = '0'*64
            with self.assertRaises(ValueError): diagnostics.diagnose(root, *bad, **pins)
            (binding[2]/'requests.jsonl').write_bytes(b'rewritten')
            with self.assertRaises(ValueError): diagnostics.diagnose(root, *binding, **pins)

    def test_valid_foreign_review_pool_with_same_case_ids_is_refused_before_native_replay(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); binding, pins = bindings(root)
            context = json.loads(binding[3].read_bytes()); pool = review_outputs(context)['pool.json']
            pool['cases'][0]['request']['state'] = 'Different source text for a synthetic refusal control.'
            foreign = bundle_fixture(root/'foreign', context, pool=pool)
            changed = (foreign, sha(foreign), *binding[2:])
            bundles.verify_bundle(foreign, sha(foreign))
            with patch.object(diagnostics, 'verify_native') as replay, self.assertRaises(ValueError):
                diagnostics.diagnose(root, *changed, **pins)
            replay.assert_not_called()

    def test_artifact_drift_between_label_comparison_and_final_replay_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); binding, pins = bindings(root); original = diagnostics.analyse
            def changed(*args):
                result = original(*args); (binding[2]/'requests.jsonl').write_bytes(b'changed after first replay'); return result
            with patch.object(diagnostics, 'analyse', side_effect=changed), self.assertRaises(ValueError):
                diagnostics.diagnose(root, *binding, **pins)

    def test_saved_diagnostic_exact_rebuild_and_rehashed_count_or_authority_refusals(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); binding, pins = bindings(root); report = diagnostics.diagnose(root, *binding, **pins)
            path = root/'docs/private/diagnostic.json'; path.write_bytes(encoded(report))
            self.assertEqual(diagnostics.verify_diagnostic(root, path, sha(path), *binding, **pins)['status'], 'pass')
            for change in (lambda r: r.update(humanExecutionVerified=True),
                           lambda r: r['comparison'].update(caseCount=5),
                           lambda r: r['comparison']['orders'][0]['metricsVsSubmittedLabels'].update(argmaxMatchesSubmittedLabel=True)):
                value = deepcopy(report); change(value); path.write_bytes(encoded(reseal(value)))
                with self.subTest(change=change), self.assertRaises(ValueError):
                    diagnostics.verify_diagnostic(root, path, sha(path), *binding, **pins)

    def test_cli_produces_and_reverifies_private_output_and_binds_committed_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); binding, pins = bindings(root); output = root/'docs/private/diagnostic-output'
            identity = ('a'*40, {'synthetic-fixture.py': 'b'*64})
            with patch.object(cli, 'ROOT', root), patch.object(cli.launcher, 'frozen_sources', return_value=identity) as source:
                self.assertEqual(cli.main(arguments(binding, pins, output)), 0); self.assertEqual(source.call_count, 2)
                path = output/'diagnostic.json'; replay = root/'docs/private/reverified'
                args = arguments(binding, pins, replay) + ['--diagnostic-report', str(path), '--diagnostic-report-file-sha256', sha(path)]
                self.assertEqual(cli.main(args), 0)
                self.assertEqual(json.loads((replay/'verification.json').read_bytes())['status'], 'pass')
                self.assertEqual(cli.main(args), 1)
            self.assertEqual(output.stat().st_mode&0o777, 0o700)
            self.assertTrue(all(path.stat().st_mode&0o777 == 0o600 for path in output.iterdir()))

    def test_cli_bad_pair_nonprivate_or_existing_output_and_source_drift_create_no_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); binding, pins = bindings(root); output = root/'docs/private/refused'
            identity = ('a'*40, {'synthetic-fixture.py': 'b'*64})
            with patch.object(cli, 'ROOT', root), patch.object(cli.launcher, 'frozen_sources', return_value=identity):
                bad = arguments(binding, pins, output) + ['--diagnostic-report-file-sha256', '0'*64]
                self.assertEqual(cli.main(bad), 1); self.assertFalse(output.exists())
                self.assertEqual(cli.main(arguments(binding, pins, root/'public')), 1); self.assertFalse((root/'public').exists())
                bad_pins = {**pins, 'context_sha': '0'*64}
                self.assertEqual(cli.main(arguments(binding, bad_pins, output)), 1); self.assertFalse(output.exists())
            with patch.object(cli, 'ROOT', root), patch.object(cli.launcher, 'frozen_sources', side_effect=[identity, ('c'*40, identity[1])]):
                self.assertEqual(cli.main(arguments(binding, pins, output)), 1); self.assertFalse(output.exists())


if __name__ == '__main__': unittest.main()
