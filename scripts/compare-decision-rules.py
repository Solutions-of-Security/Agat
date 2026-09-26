#!/usr/bin/env python3
"""Validate and compare title heuristics with the existing development model runs."""

import argparse
import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_runtime.artifacts import read_json, sealed, verify_seal, write_new
from decision_runtime.contracts import Request, fields, fingerprint, number
from decision_runtime.evaluation import load_dataset
from scripts.lib.decision_baselines import compare_baselines, development_cases, label_metrics
from scripts.lib.decision_rules import PLAN_SCHEMA, RESULT_SCHEMA, identity, predict


def require(value, message):
    if not value:
        raise ValueError(message)


def validate_rules(dataset, plan, report):
    cases = development_cases(dataset)
    verify_seal(plan, PLAN_SCHEMA); verify_seal(report, RESULT_SCHEMA)
    binding = {'id': dataset['id'], 'sha256': fingerprint(dataset), 'split': 'development'}
    require(plan['dataset'] == report['dataset'] == binding and report['planSha256'] == plan['sha256'], 'Dataset/plan mismatch')
    require(plan['rules'] == report['rules'] == identity(), 'Rule implementation changed')
    require(report['status'] == 'diagnostic_only' and report['routingEnabled'] is False and report['qualifiedForRouting'] is False
            and report['probabilities'] == 'unavailable_not_estimated', 'Invalid qualification/probability claim')
    require(plan['orders'] == ['original', 'reversed'] and plan['caseIds'] == [c['id'] for c in cases], 'Experiment scope changed')
    for name, digest in plan['harnessFiles'].items():
        path = (ROOT / name).resolve()
        require(path.is_relative_to(ROOT) and hashlib.sha256(path.read_bytes()).hexdigest() == digest, 'Frozen harness changed')
    rows = report['cases']
    require(len(rows) == len(cases), 'Missing/extra case')
    for case, row in zip(cases, rows, strict=True):
        require(all(case[k] == row[k] for k in ('id', 'family', 'groupId', 'expectedOptionId', 'labelSource')), 'Case metadata changed')
        for order in plan['orders']:
            request = Request.from_dict(case['request'] if order == 'original' else {
                **case['request'], 'options': list(reversed(case['request']['options']))})
            answer = fields(row[order], {'selectedOptionId', 'ruleReason', 'inputSha256', 'durationMs', 'status', 'reason', 'value'})
            require(answer['inputSha256'] == request.input_sha256, 'Input/order binding mismatch')
            expected = predict(request)
            require(all(answer[k] == v for k, v in expected.items()), 'Prediction cannot be replayed from frozen rules')
            option = next((o for o in request.options if o.id == expected['selectedOptionId']), None)
            accepted = option is not None and not option.abstain
            require(answer['status'] == ('ok' if accepted else 'abstain') and answer['value'] == (option.id if accepted else None)
                    and answer['reason'] == ('rule_match' if accepted else 'abstain_option' if option else 'no_selection'),
                    'Invalid typed outcome')
            number(answer['durationMs'], 0, 3_600_000)
    # Recompute aggregate scores separately from the evaluator's stored summary.
    summaries = {order: label_metrics(cases, rows, order) for order in plan['orders']}
    families = {family: {order: label_metrics([c for c in cases if c['family'] == family],
                                            [r for r in rows if r['family'] == family], order) for order in plan['orders']}
                for family in sorted({c['family'] for c in cases})}
    changed = [r['id'] for r in rows if r['original']['selectedOptionId'] != r['reversed']['selectedOptionId']]
    require(summaries == report['summary'] and families == report['families'] and changed == report['changedCaseIds'],
            'Stored summary does not match the rows')
    return {'method': 'fixed_title_rules', 'model': report['rules'], 'reportSha256': fingerprint(report),
            **summaries, 'families': families, 'changedCaseIds': changed,
            'predictions': [{'id': r['id'], 'expectedOptionId': r['expectedOptionId'],
                             **{order: {k: r[order][k] for k in ('status', 'reason', 'selectedOptionId', 'ruleReason')}
                                for order in plan['orders']}} for r in rows]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('dataset', 'plan', 'rules', 'generative', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--logits', type=Path, nargs='+', required=True)
    args = parser.parse_args()
    if args.output.exists(): parser.error('Choose a new output path')
    dataset = load_dataset(args.dataset)
    plan, rules = read_json(args.plan), read_json(args.rules)
    candidate = validate_rules(dataset, plan, rules)
    models = compare_baselines(dataset, read_json(args.generative), [read_json(p) for p in args.logits])
    result = sealed({'schemaVersion': 'agat.decision.rule-baseline-comparison.v1',
                     'createdAt': datetime.now(timezone.utc).isoformat(), 'status': 'diagnostic_only',
                     'routingEnabled': False, 'qualifiedForRouting': False, 'dataset': models['dataset'],
                     'cases': models['cases'], 'groups': models['groups'], 'rulesPlanSha256': plan['sha256'],
                     'analysisImplementationSha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                     'models': [candidate, *models['models']],
                     'limitations': models['limitations'] + rules['limitations'] + [
                         'Existing model predictions are revalidated and reused; no new model inference was run.',
                         'Rule replay checks reproducibility and summary integrity, not independent semantic correctness.']})
    write_new(args.output, result)
    for candidate in result['models']:
        print(candidate['model'].get('name'), candidate['method'],
              {order: {k: candidate[order][k] for k in ('correct', 'accepted', 'acceptedErrors')} for order in ('original', 'reversed')})


if __name__ == '__main__': main()
