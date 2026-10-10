"""Source-bound descriptive latency accounting for paired and A/A workflows."""
from collections import Counter
import statistics

from decision_runtime.contracts import number, parse_json
from decision_runtime.artifacts import sealed, verify_seal
from scripts.lib import decision_public_paired_real_primary as paired
from scripts.lib import decision_public_primary_repeat_control as repeat
from scripts.lib.decision_public_load_verification import distribution, same, sources_at
from scripts.lib.decision_public_sources import pinned_input
from scripts.lib.decision_public_real_primary_verification import verify as verify_native
from scripts.lib.decision_shadow_pilot import require, timestamp

ANALYSIS_SCHEMA = 'agat.decision.public-workflow-latency-decomposition.v1'
VERIFICATION_SCHEMA = 'agat.decision.public-workflow-latency-decomposition-verification.v1'
CLOCK_TOLERANCE_MS = 20
SOURCE_PATHS = [*repeat.SOURCE_PATHS, 'scripts/analyze-public-support-latency.py',
    'scripts/verify-public-support-latency.py', 'scripts/test/test_decision_public_latency_decomposition.py']
SPEC = {'schemaVersion': 'agat.decision.workflow-latency-accounting-protocol.v1',
    'clockToleranceMs': CLOCK_TOLERANCE_MS, 'pairedConditions': ['control', 'shadow'],
    'repeatConditions': ['repeatA', 'repeatB'], 'denominator': 'all_original_cases_including_different_primary_outputs',
    'workflowBoundary': 'instance_creation_through_final_trace_response',
    'primaryBoundary': 'adapter_request_received_through_translated_response_prepared',
    'nativeForwardBoundary': 'adapter_forward_started_through_translated_response_prepared',
    'shadowRelayBoundary': 'adapter_request_received_through_native_response_prepared',
    'componentClock': 'wall_utc', 'httpDurationClock': 'monotonic', 'callerDurationClock': 'monotonic',
    'reportedPrimaryTimingUnit': 'nanoseconds_converted_to_milliseconds',
    'newModelCalls': 0, 'causalOverheadEstablished': False}


def milliseconds(end, begin):
    value = (timestamp(end, 'phase.end')-timestamp(begin, 'phase.begin')).total_seconds()*1000
    require(value >= 0, 'Negative phase boundary; do not clip or omit a case')
    return round(value, 3)


def stats(values):
    return {**distribution(values), 'min': min(values) if values else None,
        'mean': sum(values)/len(values) if values else None, 'median': statistics.median(values) if values else None}


def phases(route, primary, decision=None, caller_ms=None):
    """Do not turn unmatched clocks or reported model timings into queue/GPU time."""
    total = number(route['elapsedMs'], 0, 210000); proxy = number(primary['elapsedMs'], 0, 180000)
    pre = milliseconds(primary['startedAt'], route['startedAt'])
    primary_wall = milliseconds(primary['completedAt'], primary['startedAt'])
    native_wall = milliseconds(primary['completedAt'], primary['nativeStartedAt'])
    milliseconds(primary['nativeStartedAt'], primary['startedAt'])
    post = milliseconds(route['completedAt'], primary['completedAt'])
    total_wall = milliseconds(route['completedAt'], route['startedAt'])
    require(abs(total-total_wall) <= 10 and abs(proxy-primary_wall) <= 10, 'Monotonic duration changed its recorded wall boundary')
    residual = round(total-(pre+proxy+post), 3)
    require(abs(residual) <= CLOCK_TOLERANCE_MS, 'Wall/monotonic accounting exceeds declared tolerance')
    native = paired.raw_body(primary, 'nativeResponseBody')
    timings = {key: round(number(native[key], 0, 3600*1_000_000_000)/1_000_000, 6)
        for key in ('total_duration', 'load_duration', 'prompt_eval_duration', 'eval_duration')}
    row = {'workflowObservedMs': total, 'prePrimaryWallMs': pre, 'primaryProxyMonotonicMs': proxy,
        'primaryProxyWallMs': primary_wall, 'primaryNativeForwardWallMs': native_wall, 'postPrimaryWallMs': post,
        'clockClosureResidualMs': residual, 'primaryReportedTimingMs': timings,
        'promptTokens': native['prompt_eval_count'], 'decodeTokens': native['eval_count'], 'doneReason': native['done_reason'],
        'outputSha256': primary['outputSha256']}
    if decision is not None:
        require(caller_ms is not None, 'Shadow caller duration missing')
        before = milliseconds(decision['startedAt'], primary['completedAt'])
        relay_wall = milliseconds(decision['completedAt'], decision['startedAt'])
        after = milliseconds(route['completedAt'], decision['completedAt'])
        require(abs(post-before-relay_wall-after) <= .001, 'Shadow tail does not close over actual HTTP boundaries')
        relay = number(decision['elapsedMs'], 0, 10000); caller = number(caller_ms, 0, 10000)
        require(abs(relay-relay_wall) <= 10 and abs(caller-relay) <= 250, 'Relay/caller clock boundaries differ from recorded source contract')
        require(caller <= post+CLOCK_TOLERANCE_MS, 'Caller duration cannot exceed the whole recorded post-primary interval')
        response = paired.raw_body(decision, 'responseBody')
        row['shadow'] = {'preDecisionWallMs': before, 'decisionRelayWallMs': relay_wall, 'decisionRelayMonotonicMs': relay,
            'postDecisionWallMs': after, 'callerMonotonicMs': caller, 'callerMinusRelayMs': round(caller-relay, 3),
            'httpStatus': decision['httpStatus'], 'outcome': 'busy' if decision['httpStatus'] == 503 else
                ('context_rejected' if decision['httpStatus'] == 422 else response['status'])}
    else: require(caller_ms is None, 'Primary-only control has a shadow timing')
    return row


def decompose(context, recipe, driver, artifacts, suite):
    require(suite in (paired, repeat), 'Unsupported native inventory for descriptive accounting')
    observed = suite.verify_inventory(context, suite.PROTOCOL, recipe, driver, artifacts)
    conditions = suite.PROTOCOL['conditions']; count = len(context['inputs'])
    primary = {(r['index'], r['condition']): r for r in suite.journal(artifacts['primary-http.jsonl'])}
    decisions = {r['index']: r for r in suite.journal(artifacts['decision-http.jsonl'])}
    cases = {}; component_names = ('workflowObservedMs', 'prePrimaryWallMs', 'primaryProxyMonotonicMs', 'postPrimaryWallMs', 'clockClosureResidualMs')
    for route in suite.journal(artifacts['workflow-routes.jsonl']):
        i, condition = route['index'], route['condition']; decision = decisions[i] if condition == 'shadow' else None
        trace = parse_json(artifacts[route['traceFile']]); caller = trace['decisionObservations'][0]['observation']['callerTiming']['durationMs'] if decision else None
        cases[i, condition] = phases(route, primary[i, condition], decision, caller)
    rows = []
    for i, case in enumerate(context['inputs']):
        first, second = (cases[i, c] for c in conditions)
        delta = {k: round(second[k]-first[k], 3) for k in component_names}
        closure = round(delta['workflowObservedMs']-sum(delta[k] for k in component_names[1:]), 3)
        require(abs(closure) <= .005, 'Matched deltas lost a phase or clock residual')
        rows.append({'index': i, 'caseId': case['id'], 'groupId': case['groupId'], 'inputSha256': case['inputSha256'],
            'samePrimaryOutput': first['outputSha256'] == second['outputSha256'],
            **{c: cases[i, c] for c in conditions}, 'secondMinusFirstMs': delta, 'deltaClosureResidualMs': closure})
    require(len(rows) == count and sum(observed['repeatCalls'].values() if suite is repeat else (observed['controlCalls'], observed['shadowCalls'])) == 2*count,
        'Accounting omitted or resampled original cases')
    components = {c: {k: stats([row[c][k] for row in rows]) for k in component_names} for c in conditions}
    deltas = {k: stats([row['secondMinusFirstMs'][k] for row in rows]) for k in component_names}
    shadow_rows = [row['shadow']['shadow'] for row in rows] if suite is paired else []
    shadow_keys = ('preDecisionWallMs', 'decisionRelayWallMs', 'decisionRelayMonotonicMs', 'postDecisionWallMs', 'callerMonotonicMs', 'callerMinusRelayMs')
    return {'conditions': conditions, 'originalCases': count, 'originalGroups': len({c['groupId'] for c in context['inputs']}),
        'accountedWorkflows': 2*count, 'nativeCaseCalls': len(decisions), 'allOriginalCasesAccounted': True,
        'matchedOutputPairs': sum(r['samePrimaryOutput'] for r in rows), 'differentOutputIndices': [r['index'] for r in rows if not r['samePrimaryOutput']],
        'componentMs': components, 'secondMinusFirstMs': deltas,
        'shadowTimingMs': {k: stats([r[k] for r in shadow_rows]) for k in shadow_keys},
        'shadowOutcomes': dict(sorted(Counter(r['outcome'] for r in shadow_rows).items())),
        'newModelCalls': 0, 'classificationAccuracyMeasured': False, 'referenceLabels': 0,
        'sloAccepted': False, 'routingEnabled': False, 'qualification': 'not_assessed', 'causalOverheadEstablished': False, 'cases': rows}


def combine(context, paired_recipe, paired_driver, paired_artifacts, repeat_recipe, repeat_driver, repeat_artifacts):
    a = decompose(context, paired_recipe, paired_driver, paired_artifacts, paired)
    b = decompose(context, repeat_recipe, repeat_driver, repeat_artifacts, repeat)
    return {'paired': a, 'repeatControl': b, 'newModelCalls': 0, 'allCasesIncluded': True,
        'primaryOnlyOutputVariationObserved': bool(b['differentOutputIndices']), 'causalOverheadEstablished': False,
        'wholeWorkflowDeltaIsShadowCostEstimate': False, 'liveCleanupEstablishedByAnalysis': False,
        'referenceLabels': 0, 'classificationAccuracyMeasured': False, 'ownersAppointed': False,
        'sloAccepted': False, 'routingEnabled': False, 'qualification': 'not_assessed'}


def private_path(root, relative):
    from pathlib import Path
    require(isinstance(relative, str) and not Path(relative).is_absolute() and '..' not in Path(relative).parts,
        'Use an owned relative private evidence path')
    path = root/relative
    require(path.resolve().is_relative_to((root/'docs/private').resolve()) and not path.is_symlink(), 'Evidence path escapes private directory')
    return path


def analyze_receipts(root, inputs):
    require(set(inputs) == {'context', 'paired', 'repeatControl'} and set(inputs['context']) == {'path', 'sha256'}, 'Incomplete pinned analysis inputs')
    context_path = private_path(root, inputs['context']['path']); context_sha = inputs['context']['sha256']
    context = parse_json(pinned_input(context_path, context_sha, 32*1024*1024)); raw = []; receipts = {}
    for key, suite in (('paired', paired), ('repeatControl', repeat)):
        value = inputs[key]; require(set(value) == {'evidenceDir', 'planFileSha256', 'resultFileSha256'}, 'Unpinned dataset')
        directory = private_path(root, value['evidenceDir'])
        receipt = verify_native(root, directory, context_path, context_sha=context_sha,
            plan_sha=value['planFileSha256'], result_sha=value['resultFileSha256'], suite=suite)
        require(receipt['status'] == 'pass' and receipt['modelCallsDuringVerification'] == 0, 'Source dataset replay failed')
        result = parse_json(pinned_input(directory/'result.json', value['resultFileSha256'], 64*1024*1024))
        artifacts = {name: pinned_input(directory/name, pin, 32*1024*1024) for name, pin in result['artifactSha256'].items()}
        raw.append((parse_json(artifacts['workflow-plan.json']), parse_json(artifacts['workflow-driver.json']), artifacts))
        receipts[key] = receipt
    return combine(context, *raw[0], *raw[1]), receipts


def create_analysis(root, inputs, commit, source_files):
    sources_at(root, commit, source_files, SOURCE_PATHS)
    evidence, receipts = analyze_receipts(root, inputs)
    return sealed({'schemaVersion': ANALYSIS_SCHEMA, 'createdAt': paired.now(), 'sourceCommit': commit, 'sourceFiles': source_files,
        'protocol': SPEC, 'inputs': inputs, 'datasetReplays': receipts, 'evidence': evidence,
        'newModelCalls': 0, 'referenceLabels': 0, 'classificationAccuracyMeasured': False, 'ownersAppointed': False,
        'sloAccepted': False, 'routingEnabled': False, 'qualification': 'not_assessed'})


def verify_analysis(root, path, file_sha):
    analysis = verify_seal(parse_json(pinned_input(path, file_sha, 64*1024*1024)), ANALYSIS_SCHEMA)
    require(set(analysis) == {'schemaVersion', 'sha256', 'createdAt', 'sourceCommit', 'sourceFiles', 'protocol', 'inputs', 'datasetReplays',
        'evidence', 'newModelCalls', 'referenceLabels', 'classificationAccuracyMeasured', 'ownersAppointed', 'sloAccepted', 'routingEnabled', 'qualification'}, 'Analysis schema changed')
    same(analysis['protocol'], SPEC, 'Posthoc accounting protocol')
    created = timestamp(analysis['createdAt'], 'analysis.createdAt')
    require(set(analysis['datasetReplays']) == {'paired', 'repeatControl'}, 'Incomplete/extra source dataset replay')
    require(all(timestamp(v['verifiedAt'], 'dataset.verifiedAt') <= created for v in analysis['datasetReplays'].values()),
        'Analysis timestamp precedes its source replay')
    require(type(analysis['newModelCalls']) is int and analysis['newModelCalls'] == 0 and type(analysis['referenceLabels']) is int
        and analysis['referenceLabels'] == 0 and analysis['qualification'] == 'not_assessed'
        and all(analysis[k] is False for k in ('classificationAccuracyMeasured', 'ownersAppointed', 'sloAccepted', 'routingEnabled')), 'Analysis grants unsupported authority')
    source_files = sources_at(root, analysis['sourceCommit'], analysis['sourceFiles'], SOURCE_PATHS)
    evidence, receipts = analyze_receipts(root, analysis['inputs'])
    same(evidence, analysis['evidence'], 'Latency decomposition differs from raw receipts')
    for key, value in receipts.items():
        old = analysis['datasetReplays'][key]
        same({k: v for k, v in value.items() if k not in ('sha256', 'verifiedAt')},
            {k: v for k, v in old.items() if k not in ('sha256', 'verifiedAt')}, 'Embedded source dataset replay changed')
        verify_seal(old, value['schemaVersion'])
    return sealed({'schemaVersion': VERIFICATION_SCHEMA, 'verifiedAt': paired.now(), 'status': 'pass', 'analysisFileSha256': file_sha,
        'sourceCommit': analysis['sourceCommit'], 'verifiedSourceFiles': len(source_files), 'evidence': evidence,
        'newModelCalls': 0, 'modelCallsDuringVerification': 0, 'referenceLabels': 0, 'classificationAccuracyMeasured': False,
        'ownersAppointed': False, 'sloAccepted': False, 'routingEnabled': False, 'qualification': 'not_assessed'})
