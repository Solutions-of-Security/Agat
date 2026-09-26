"""Plan group counts from declared criteria without reading labels or running a model."""

import hashlib
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from decision_runtime.annotations import group_split, prepare_review
from decision_runtime.artifacts import sealed
from decision_runtime.calibration_workflow import validate_criteria
from decision_runtime.contracts import fingerprint, number
from decision_runtime.qualification import binomial_upper

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = 'agat.decision.sampling-plan.v1'


def minimum_zero_error_groups(max_risk, confidence):
    number(max_risk, 0, 1); number(confidence, .5, .9999999999)
    if max_risk == 0:
        return {'status': 'no_finite_sample', 'count': None}
    if max_risk == 1:
        return {'status': 'finite', 'count': 1, 'upperBound': binomial_upper(0, 1, confidence), 'previousUpperBound': None}
    estimate = math.log1p(-confidence) / math.log1p(-max_risk)
    if not math.isfinite(estimate) or estimate > 1_000_000_000:
        return {'status': 'above_calculation_limit', 'count': None, 'calculationLimit': 1_000_000_000}
    n = max(1, math.ceil(estimate))
    # Certify the minimal integer against the same bound used by qualification.
    while binomial_upper(0, n, confidence) > max_risk: n += 1
    while n > 1 and binomial_upper(0, n-1, confidence) <= max_risk: n -= 1
    return {'status': 'finite', 'count': n, 'upperBound': binomial_upper(0, n, confidence),
            'previousUpperBound': binomial_upper(0, n-1, confidence) if n > 1 else None}


def sampling_plan(blank_review, criteria):
    validate_criteria(criteria)
    if not isinstance(blank_review, dict) or 'pool' not in blank_review or 'splitSeed' not in blank_review:
        raise ValueError('Expected the original blank review package')
    expected = prepare_review(blank_review['pool'], blank_review['splitSeed'])
    if blank_review != expected:
        raise ValueError('Sampling planner requires an intact blank review, without labels or predictions')
    pool, seed = blank_review['pool'], blank_review['splitSeed']
    families = sorted({c['family'] for c in pool['cases']})
    confidence = 1-(1-criteria['confidence'])/(len(families)+1)
    required = minimum_zero_error_groups(criteria['maxGroupRisk'], confidence)
    count = required['count']
    global_min = max(count, criteria['minAcceptedGroups']) if count is not None else None
    family_min = max(count, criteria['minAcceptedGroupsPerFamily']) if count is not None else None
    splits = {name: [c for c in pool['cases'] if group_split(seed,c['groupId']) == name]
              for name in ('development','calibration','holdout')}
    split_counts = {name: {'cases': len(cases), 'groups': len({c['groupId'] for c in cases}),
                          'sources': len({c['provenance']['sourceId'] for c in cases})}
                    for name, cases in splits.items()}
    groups = {c['groupId'] for c in splits['holdout']}
    by_family = {}
    for family in families:
        cases = [c for c in splits['holdout'] if c['family'] == family]
        held = {c['groupId'] for c in cases}
        by_family[family] = {'prospectiveHoldoutCases': len(cases), 'prospectiveHoldoutGroups': len(held),
                             'requiredAcceptedGroupsZeroErrors': family_min,
                             'minimumAdditionalGroupsEvenIfAllAcceptedAndCorrect': max(0,family_min-len(held)) if family_min is not None else None,
                             'optimisticRiskUpperBound': binomial_upper(0,len(held),confidence),
                             'calibrationGroups': len({c['groupId'] for c in splits['calibration'] if c['family'] == family})}
    sources = {}
    for split, cases in splits.items():
        for case in cases: sources.setdefault(case['provenance']['sourceId'],set()).add(split)
    cross_split = sorted(source for source, assigned in sources.items() if len(assigned)>1)
    issues = []
    if not splits['calibration']: issues.append('no_calibration_groups')
    if not splits['holdout']: issues.append('no_holdout_groups')
    if cross_split: issues.append('source_crosses_splits')
    if count is None: issues.append(required['status'])
    elif len(groups) < global_min or any(v['prospectiveHoldoutGroups'] < family_min for v in by_family.values()):
        issues.append('insufficient_holdout_group_capacity_even_at_zero_errors')
    paths = ['scripts/plan-decision-sampling.py','scripts/lib/decision_sampling.py',
             'decision_runtime/annotations.py','decision_runtime/qualification.py','decision_runtime/calibration_workflow.py']
    return sealed({'schemaVersion':SCHEMA,'createdAt':datetime.now(timezone.utc).isoformat(),
                   'status':'insufficient_capacity' if issues else 'capacity_only_requires_review_and_measurement',
                   'qualifiedForRouting':False,'routingEnabled':False,'modelCalls':0,
                   'poolSha256':fingerprint(pool),'blankReviewSha256':fingerprint(blank_review),
                   'criteria':criteria,'criteriaSha256':fingerprint(criteria),'criteriaApproval':'not_asserted',
                   'splitSeed':seed,'splits':split_counts,'families':by_family,'blockingCapacityIssues':issues,
                   'sourceIdsAcrossSplits':cross_split,'sourceKindCaseCounts':dict(Counter(c['provenance']['kind'] for c in pool['cases'])),
                   'method':{'name':'zero-error one-sided Clopper-Pearson; Bonferroni overall and families',
                             'simultaneousConfidence':criteria['confidence'],'comparisons':len(families)+1,
                             'boundConfidence':confidence,'independentAcceptedGroupsRequired':required,
                             'globalMinimumWithCountGate':global_min,'perFamilyMinimumWithCountGate':family_min},
                   'overall':{'prospectiveHoldoutGroups':len(groups),
                              'optimisticRiskUpperBound':binomial_upper(0,len(groups),confidence),
                              'minimumAdditionalGroupsEvenIfAllAcceptedAndCorrect':max(0,global_min-len(groups)) if global_min is not None else None},
                   'implementation':{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in paths},
                   'limits':['Planning only, not observed acceptance, correctness, confidence, or qualification.',
                             'Zero errors and acceptance of every available group are optimistic assumptions.',
                             'Groups must be independent and representative; distinct IDs do not prove that.',
                             'Per-family group requirements are not summed: one group can contain several families.',
                             'Case coverage cannot be converted directly into accepted-group coverage.',
                             'This bounds holdout capacity only; calibration sample adequacy is not established.',
                             'Seed, source groups, criteria and human labels are not changed by this tool.']})
