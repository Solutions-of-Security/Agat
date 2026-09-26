#!/usr/bin/env python3
"""Recompute offline batching evidence without loading a model or the probe helpers."""

import argparse
import hashlib
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_runtime.artifacts import read_json, sealed, verify_seal, write_new
from decision_runtime.contracts import Request


def require(condition, message):
    if not condition:
        raise ValueError(message)


def check_outcome(row, case, policy):
    request = Request.from_dict(case['request'])
    require(row['inputTokens'] == case['targetTokens'], 'Token count mismatch')
    logits = row['logits']
    require(len(logits) == len(request.options) and all(math.isfinite(x) for x in logits), 'Invalid logits')
    exp = [math.exp(z - max(logits)) for z in logits]
    ps = [x / sum(exp) for x in exp]
    require(len(ps) == len(row['probabilities']) and all(abs(a-b) <= 1e-15 for a,b in zip(ps, row['probabilities'])),
            'Softmax mismatch')
    ranked = sorted(range(len(ps)), key=lambda i: ps[i], reverse=True)
    selected = request.options[ranked[0]]
    reason = ('abstain_option' if selected.abstain else 'below_threshold'
              if ps[ranked[0]] < policy['minProbability'] or ps[ranked[0]] - ps[ranked[1]] < policy['minMargin']
              else 'accepted')
    require(row['selectedOptionId'] == selected.id and row['reason'] == reason
            and row['status'] == ('ok' if reason == 'accepted' else 'abstain'), 'Outcome mismatch')


def comparison(left, right, tolerance):
    logit = max(abs(a-b) for a,b in zip(left['logits'], right['logits']))
    prob = max(abs(a-b) for a,b in zip(left['probabilities'], right['probabilities']))
    same = all(left[k] == right[k] for k in ('selectedOptionId', 'reason', 'status'))
    within = logit <= tolerance and prob <= tolerance
    return {'maxAbsoluteLogitDelta': logit, 'maxAbsoluteProbabilityDelta': prob,
            'sameOutcome': same, 'withinTolerance': within, 'equivalentUnderCriterion': same and within}


def verify(source, plan, result, head_plan, head_result):
    for value, schema in [(source, 'synthetic-robustness-plan'), (plan, 'batching-plan'),
                          (result, 'batching-diagnostic'), (head_plan, 'batch-head-plan'),
                          (head_result, 'batch-head-diagnostic')]:
        verify_seal(value, f'agat.decision.{schema}.v1')
    require(plan['sourcePlanSha256'] == head_plan['sourcePlanSha256'] == source['sha256'], 'Wrong source plan')
    require(result['planSha256'] == plan['sha256'] and head_result['planSha256'] == head_plan['sha256'], 'Wrong plan binding')
    require(plan['serialProfile'] == head_plan['serialProfile'], 'Profile mismatch')
    require(plan['serialProfile']['calibration']['temperature'] == 1, 'Only the predeclared raw profile is supported')
    for frozen in (plan, head_plan):
        for name, digest in frozen['harnessFiles'].items():
            path = (ROOT / name).resolve()
            require(path.is_relative_to(ROOT) and hashlib.sha256(path.read_bytes()).hexdigest() == digest,
                    f'Frozen harness changed: {name}')
    require(plan['tolerance'] == {'absoluteLogit': 0.0001, 'absoluteProbability': 0.0001, 'sameOutcomeRequired': True}
            and head_plan['absoluteTolerance'] == 0.0001 and head_plan['sameOutcomeRequired'] is True,
            'Predeclared criterion changed')
    require(plan['batchExecution'] == {'kind': 'offline-equal-length-prototype', 'sizes': [2,4],
            'rowOrders': ['forward','reversed'], 'padding': False, 'sharedCache': False}, 'Different experiment')
    groups = defaultdict(list)
    for case in source['cases']:
        request = Request.from_dict(case['request'])
        require(request.input_sha256 == case['inputSha256'], 'Invalid source input binding')
        groups[case['targetTokens']].append({'request': request.to_dict(), 'inputSha256': request.input_sha256,
                                           'targetTokens': case['targetTokens']})
    groups = dict(sorted(groups.items()))
    expected_cases = [case for cases in groups.values() for case in cases]
    require(plan['cases'] == expected_cases, 'Source selection changed')
    require(head_plan['cases'] == [case for cases in groups.values() for case in cases[:4]], 'Head selection changed')
    lookup = {case['request']['id']: case for case in expected_cases}
    require(len(lookup) == len(expected_cases), 'Duplicate case IDs')
    policy = plan['serialProfile']['policy']

    def check_call(call, variant, order, cases):
        require((call['variant'], call['rowOrder'], call['batchSize']) == (variant, order, len(cases)), 'Wrong call shape')
        require([r['caseId'] for r in call['rows']] == [c['request']['id'] for c in cases], 'Wrong row binding/order')
        require(math.isfinite(call['wallMs']) and call['wallMs'] > 0, 'Invalid duration')
        for row, case in zip(call['rows'], cases):
            require(row['inputSha256'] == case['inputSha256'], 'Wrong input binding')
            check_outcome(row, case, policy)

    require(len(result['warmup']) == 3, 'Incomplete warmup')
    first = next(iter(groups.values()))
    for call, size in zip(result['warmup'], [1,2,4]):
        check_call(call, 'warmup', 'forward', first[:size])
    expected_calls = [('serial','forward',[case]) for case in expected_cases]
    for size in (2,4):
        for cases in groups.values():
            for offset in range(0, len(cases), size):
                batch = cases[offset:offset+size]
                expected_calls += [(f'batch_{size}', 'forward', batch), (f'batch_{size}', 'reversed', list(reversed(batch)))]
    require(len(result['calls']) == len(expected_calls), 'Incomplete measurement ledger')
    serial = {}; forward = {}; comparisons = []; timing = defaultdict(list)
    for call, expected in zip(result['calls'], expected_calls):
        check_call(call, *expected)
        timing[(call['variant'], call['rows'][0]['inputTokens'])].append(call)
        for row in call['rows']:
            case_id = row['caseId']; size = call['batchSize']
            if call['variant'] == 'serial':
                serial[case_id] = row
                continue
            comparisons.append({'kind': 'versus_serial', 'caseId': case_id, 'batchSize': size,
                                'rowOrder': call['rowOrder'], **comparison(serial[case_id], row, 0.0001)})
            if call['rowOrder'] == 'forward':
                forward[(size, case_id)] = row
            else:
                comparisons.append({'kind': 'row_permutation', 'caseId': case_id, 'batchSize': size,
                                    **comparison(forward[(size, case_id)], row, 0.0001)})
    require(comparisons == result['comparisons'], 'Comparison ledger mismatch')
    expected_summary = {'measuredForwardCalls': len(expected_calls),
                        'measuredRows': sum(len(c['rows']) for c in result['calls']),
                        'comparisonViolations': sum(not c['equivalentUnderCriterion'] for c in comparisons),
                        'changedOutcomes': sum(not c['sameOutcome'] for c in comparisons)}
    require(result['summary'] == expected_summary and result['profileStable'] is True and result['stoppedReason'] is None,
            'Summary or completeness mismatch')
    require(result['equivalentUnderCriterion'] == all(c['equivalentUnderCriterion'] for c in comparisons), 'Invalid conclusion')
    require([r['caseId'] for r in head_result['rows']] == [c['request']['id'] for c in head_plan['cases']], 'Head rows changed')
    for row in head_result['rows']:
        case = lookup[row['caseId']]
        require(row['inputSha256'] == case['inputSha256'], 'Head binding mismatch')
        require(math.isfinite(row['hiddenMaxAbsoluteDelta']) and row['hiddenMaxAbsoluteDelta'] >= 0, 'Invalid hidden delta')
        for key in ('serial', 'batchMatrixHead', 'batchRowwiseHead'):
            check_outcome(row[key], case, policy)
        for key, left, right in [('matrixVersusSerial','serial','batchMatrixHead'),
                                 ('rowwiseVersusSerial','serial','batchRowwiseHead'),
                                 ('matrixVersusRowwise','batchRowwiseHead','batchMatrixHead')]:
            require(row[key] == comparison(row[left], row[right], 0.0001), 'Head comparison mismatch')
        for key, other in [('serial',serial[row['caseId']]), ('batchMatrixHead',forward[(4,row['caseId'])])]:
            require(all(row[key][field] == other[field] for field in row[key]), 'Separate processes gave different logits')
    require(head_result['summary'] == {'cases': len(head_result['rows']),
            'backboneDifferent': sum(r['hiddenMaxAbsoluteDelta'] != 0 for r in head_result['rows']),
            'matrixViolations': sum(not r['matrixVersusSerial']['equivalentUnderCriterion'] for r in head_result['rows']),
            'rowwiseViolations': sum(not r['rowwiseVersusSerial']['equivalentUnderCriterion'] for r in head_result['rows'])},
            'Head summary mismatch')
    for report in (result, head_result):
        require(report['status'] == 'diagnostic_only' and report['qualifiedForRouting'] is False
                and report['batchingEnabled'] is False, 'Incorrect qualification claim')
    timings = []
    for (variant, tokens), calls in timing.items():
        elapsed = sum(c['wallMs'] for c in calls); count = sum(len(c['rows']) for c in calls)
        timings.append({'variant':variant, 'inputTokens':tokens, 'calls':len(calls), 'rows':count,
                        'sumWallMs':round(elapsed,3), 'meanAmortizedMsPerRow':round(elapsed/count,3)})
    deltas = []
    for kind in ('versus_serial', 'row_permutation'):
        for size in (2,4):
            checks = [c for c in comparisons if c['kind'] == kind and c['batchSize'] == size]
            deltas.append({'kind':kind, 'batchSize':size, 'comparisons':len(checks),
                           'violations':sum(not c['equivalentUnderCriterion'] for c in checks),
                           'changedOutcomes':sum(not c['sameOutcome'] for c in checks),
                           'maxAbsoluteLogitDelta':max(c['maxAbsoluteLogitDelta'] for c in checks),
                           'maxAbsoluteProbabilityDelta':max(c['maxAbsoluteProbabilityDelta'] for c in checks)})
    return {'summary':expected_summary, 'timing':timings, 'deltas':deltas,
            'headSummary':head_result['summary'], 'headProjectionIdenticalOnSameHidden':all(
                r['matrixVersusRowwise']['maxAbsoluteLogitDelta'] == 0 for r in head_result['rows']),
            'headMaxHiddenDelta':max(r['hiddenMaxAbsoluteDelta'] for r in head_result['rows'])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    names = ['robustness-plan', 'batching-plan', 'batching-result', 'batch-head-plan', 'batch-head-result']
    artifacts = [read_json(args.evidence_dir / f'{name}.json') for name in names]
    checked = verify(*artifacts)
    report = sealed({'schemaVersion':'agat.decision.batching-verification.v1',
                     'createdAt':datetime.now(timezone.utc).isoformat(), 'status':'verified',
                     'artifacts':{name:artifact['sha256'] for name,artifact in zip(names,artifacts)},
                     'verifierSha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), **checked,
                     'limitations':['Verification recomputes persisted logits, probabilities, bindings, order and summaries without model inference.',
                                    'Full hidden vectors are not persisted; their reported maximum difference is not independently recomputed.',
                                    'Allocator peak is cumulative within a process, not a per-variant peak or process RSS limit.',
                                    'Verified integrity is not qualification or acceptance of batching.']})
    write_new(args.output, report)
    print(checked)


if __name__ == '__main__':
    main()
