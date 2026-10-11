"""Compare submitted development labels with a complete pinned native observation."""
from collections import defaultdict

from decision_runtime.artifacts import sealed
from decision_runtime.contracts import canonical_json, fingerprint, parse_json
from decision_runtime.evaluation import metrics, validate_dataset
from scripts.lib.decision_public_development_review import review_outputs
from scripts.lib.decision_public_load import validate_context
from scripts.lib.decision_public_permutation_diagnostic import verify as verify_native, verify_phase
from scripts.lib.decision_public_permutations import variants
from scripts.lib.decision_public_sources import pinned_input
from scripts.lib.decision_review_bundle import verify_bundle
from scripts.lib.decision_review_session import AUTHORITY_FLAGS, MAX_REVIEW_BYTES
from scripts.lib.decision_shadow_pilot import require

SCHEMA = 'agat.decision.finalized-development-native-diagnostic.v1'


def _metrics(rows):
    value = metrics(rows)
    return {
        'caseCount': value['attempted'], 'computedCaseCount': value['scored'],
        'contextRejectedCaseCount': value['backendErrors'], 'abstainedCaseCount': value['abstained'],
        'acceptedCaseCount': value['accepted'], 'acceptedCoverageAllCases': value['coverage'],
        'argmaxMatchesSubmittedLabel': value['correct'],
        'argmaxAgreementAllCases': value['accuracyAllAttempts'],
        'argmaxAgreementComputedCases': value['accuracyScored'],
        'acceptedDisagreementCaseCount': value['acceptedErrors'],
        'acceptedDisagreementFraction': value['selectiveRisk'],
        'macroF1ComputedVsSubmittedLabels': value['macroF1Scored'],
        'nllComputedVsSubmittedLabels': value['nllScored'],
        'brierComputedVsSubmittedLabels': value['brierScored'],
        'ece10ComputedVsSubmittedLabels': value['ece10Scored'],
        'reliabilityVsSubmittedLabels': [
            {key: item for key, item in row.items() if key != 'accuracy'} |
            {'fractionMatchesSubmittedLabel': row['accuracy']} for row in value['reliability']],
        'classesVsSubmittedLabels': value['classes'],
    }


def analyse(dataset, context, inputs, phase):
    """Keep original cases as the unit; option variants are dependent diagnostics."""
    validate_dataset(dataset); validate_context(context)
    pool = review_outputs(context)['pool.json']
    require(dataset['annotation']['poolSha256'] == fingerprint(pool)
            and dataset['annotation']['splitSeed'] == context['splitSeed'],
            'Finalized review belongs to a different original context pool or split seed')
    cases = dataset['cases']
    require(len(cases) == len(context['inputs'])
            and all(case['split'] == 'development' for case in cases),
            'Use the whole development inventory; calibration/holdout labels are excluded')
    for case, original in zip(cases, pool['cases'], strict=True):
        require(canonical_json({key: case[key] for key in original}) == canonical_json(original),
                'Finalized case order, group, provenance or original request changed')
    expected_variants = variants(context)
    require(len(inputs) == len(expected_variants), 'Whole prospective option-order inventory is required')
    for actual, expected in zip(inputs, expected_variants, strict=True):
        require(canonical_json({key: actual[key] for key in expected}) == canonical_json(expected),
                'Native variant differs from the complete original request/order inventory')
    verify_phase(phase, inputs, context['profile'])
    expected_ids = [case['id'] for case in cases]
    by_order = defaultdict(dict)
    for row in phase['rows']:
        require(row['sourceCaseId'] in expected_ids and row['sourceCaseId'] not in by_order[row['orderId']],
                'Unexpected or repeated original case in an option order')
        by_order[row['orderId']][row['sourceCaseId']] = row
    require(by_order and all(set(rows) == set(expected_ids) for rows in by_order.values()),
            'Every option order must cover every original development case')
    orders = []; details = {case['id']: [] for case in cases}
    for order_id, observed in by_order.items():
        rows = []
        for case in cases:
            observation = observed[case['id']]
            require(observation['sourceGroupId'] == case['groupId'], 'Native group differs from reviewed source')
            result = observation['observation']['result']
            matched = result['selectedOptionId'] == case['expectedOptionId']
            rows.append({key: case[key] for key in ('id', 'family', 'groupId', 'expectedOptionId')} |
                        {'result': result, 'correct': matched})
            probabilities = {item['id']: item['probability'] for item in result['distribution']}
            details[case['id']].append({'orderId': order_id, 'status': result['status'], 'reason': result['reason'],
                'selectedOptionId': result['selectedOptionId'], 'matchesSubmittedLabel': matched,
                'submittedLabelProbability': probabilities.get(case['expectedOptionId'])})
        accepted_groups = {row['groupId'] for row in rows if row['result']['status'] == 'ok'}
        disagreements = {row['groupId'] for row in rows if row['result']['status'] == 'ok' and not row['correct']}
        orders.append({'orderId': order_id, 'metricsVsSubmittedLabels': _metrics(rows),
            'groupCount': len({case['groupId'] for case in cases}), 'acceptedGroupCount': len(accepted_groups),
            'groupsWithAcceptedDisagreement': sorted(disagreements)})
    return {'caseCount': len(cases), 'groupCount': len({case['groupId'] for case in cases}),
        'nativeVariantCount': len(phase['rows']), 'optionOrderCount': len(orders), 'orders': orders,
        'cases': [{key: case[key] for key in ('id', 'groupId', 'family', 'expectedOptionId')} |
                  {'orders': details[case['id']]} for case in cases]}


def diagnose(root, bundle_path, bundle_sha, directory, context_path, permutation_path, *,
             context_sha, permutation_sha, plan_sha, result_sha):
    reviews = verify_bundle(bundle_path, bundle_sha)
    dataset_raw = pinned_input(bundle_path.parent/'dataset.json', reviews['datasetFileSha256'], MAX_REVIEW_BYTES)
    context_raw = pinned_input(context_path, context_sha, 32*1024*1024)
    context = parse_json(context_raw)
    dataset = parse_json(dataset_raw)
    # Reject unrelated or partial labels before replaying the larger native history.
    pool = review_outputs(context)['pool.json']
    require(dataset['annotation']['poolSha256'] == fingerprint(pool),
            'Finalized labels do not belong to the native development inventory')
    pins = {'context_sha': context_sha, 'permutation_sha': permutation_sha,
            'plan_sha': plan_sha, 'result_sha': result_sha}
    native = verify_native(root, directory, context_path, permutation_path, **pins)
    plan_raw = pinned_input(directory/'plan.json', plan_sha, 32*1024*1024)
    result_raw = pinned_input(directory/'result.json', result_sha, 64*1024*1024)
    plan, result = parse_json(plan_raw), parse_json(result_raw)
    comparison = analyse(dataset, context, plan['inputs'], result['phase'])
    require(canonical_json(verify_bundle(bundle_path, bundle_sha)) == canonical_json(reviews)
            and canonical_json(verify_native(root, directory, context_path, permutation_path, **pins)) == canonical_json(native),
            'Source reviews or native receipts changed during diagnostics')
    for path, pin, raw in ((bundle_path.parent/'dataset.json', reviews['datasetFileSha256'], dataset_raw),
                           (context_path, context_sha, context_raw), (directory/'plan.json', plan_sha, plan_raw),
                           (directory/'result.json', result_sha, result_raw)):
        require(pinned_input(path, pin, 64*1024*1024) == raw, 'Consumed source artifact changed')
    return sealed({'schemaVersion': SCHEMA, 'status': 'development_diagnostic_only',
        'bundleFileSha256': bundle_sha, 'datasetFileSha256': reviews['datasetFileSha256'],
        'contextProfileFileSha256': context_sha, 'permutationContextFileSha256': permutation_sha,
        'planFileSha256': plan_sha, 'resultFileSha256': result_sha,
        'reviewVerificationSha256': reviews['sha256'], 'nativeVerificationSha256': native['sha256'],
        'modelProfileSha256': context['profileSha256'], 'comparison': comparison,
        'sourceReviewsRevalidated': True, 'nativePhysicalHttpAccounting': native['physicalHttpAccounting'],
        'submittedLabelMetricsComputed': True, 'referenceLabelsVerified': 0,
        'optionVariantsStatisticallyIndependent': False, 'calibrationPerformed': False,
        'holdoutEvaluated': False, 'thresholdsTuned': False,
        **{flag: False for flag in AUTHORITY_FLAGS}, 'classificationAccuracyMeasured': False,
        'modelCallsDuringDiagnostics': 0, 'qualification': 'not_assessed'})


def verify_diagnostic(root, report_path, report_sha, *args, **kwargs):
    raw = pinned_input(report_path, report_sha, 32*1024*1024)
    parse_json(raw)
    expected = diagnose(root, *args, **kwargs)
    require(raw == (canonical_json(expected)+'\n').encode('utf8'),
            'Saved diagnostic differs from its complete source reviews and native history')
    return sealed({'schemaVersion': 'agat.decision.finalized-development-native-verification.v1',
        'status': 'pass', 'diagnosticFileSha256': report_sha, 'diagnosticSha256': expected['sha256'],
        'caseCount': expected['comparison']['caseCount'], 'groupCount': expected['comparison']['groupCount'],
        'nativeVariantCount': expected['comparison']['nativeVariantCount'],
        **{flag: False for flag in AUTHORITY_FLAGS}, 'classificationAccuracyMeasured': False,
        'modelCallsDuringVerification': 0, 'qualification': 'not_assessed'})
