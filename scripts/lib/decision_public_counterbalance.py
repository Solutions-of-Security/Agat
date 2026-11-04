"""Prospective, source-bound ABBA/BAAB replication of the whole public inventory."""
from collections import Counter
from copy import deepcopy
import hashlib
import re

from decision_runtime.artifacts import sealed, verify_seal
from decision_runtime.contracts import parse_json
from scripts.lib import decision_public_paired_real_primary as paired
from scripts.lib.decision_public_load import validate_context
from scripts.lib.decision_public_load_verification import CONTEXT_PATHS, same, sources_at
from scripts.lib.decision_public_sources import pinned_input
from scripts.lib.decision_shadow_pilot import require, timestamp

PLAN_SCHEMA = 'agat.decision.public-counterbalanced-design.v1'
VERIFICATION_SCHEMA = 'agat.decision.public-counterbalanced-design-verification.v1'
SCHEDULE_SCHEMA = 'agat.decision.public-counterbalanced-schedule.v1'
SEED_DOMAIN = 'agat.public-workflow-counterbalanced-replication.v1'
PROTOCOL = {**deepcopy(paired.PROTOCOL), 'kind': 'prospective_counterbalanced_replication',
    'conditionOrder': 'sha256_ranked_abba_baab_by_original_pair', 'observationsPerCondition': 2,
    'periodsPerBlock': 4, 'blockOrder': 'original_adjacent_input_pairs',
    'orderSeed': 'fixed_domain_and_context_file_sha256', 'completeBlockOrientationBalance': 'difference_at_most_one',
    'singletonOrientation': 'separate_seeded_bit', 'primaryCachePolicy': 'unchanged_owned_runner_default',
    'outputEqualityRequired': False, 'denominator': 'all_original_cases_and_all_four_periods',
    'failedAttemptPolicy': 'preserve_and_report_no_outcome_based_order_changes',
    'caseContrast': 'mean_two_shadow_minus_mean_two_control',
    'linearTrendBalanceCoordinate': 'ordinal_period_not_elapsed_wall_time', 'independentCasesAssumed': False,
    'causalOverheadEstablished': False}
SOURCE_PATHS = [*paired.SOURCE_PATHS, 'scripts/plan-public-support-counterbalanced.py',
    'scripts/verify-public-support-counterbalanced-design.py', 'scripts/test/test_decision_public_counterbalance.py']
FLAGS = {'modelCalls': 0, 'measuredWorkflows': 0, 'referenceLabels': 0,
    'classificationAccuracyMeasured': False, 'ownersAppointed': False, 'sloAccepted': False,
    'routingEnabled': False, 'qualification': 'not_assessed', 'causalOverheadEstablished': False}


def schedule(count, context_sha):
    require(type(count) is int and 1 <= count <= 60, 'Use the whole bounded input inventory')
    require(isinstance(context_sha, str) and re.fullmatch(r'[a-f0-9]{64}', context_sha), 'Invalid context file SHA')
    seed = hashlib.sha256((SEED_DOMAIN+'\0'+context_sha).encode()).hexdigest()
    rank = lambda pair: hashlib.sha256((seed+'\0pair\0'+str(pair)).encode()).hexdigest()
    complete = sorted(range(count//2), key=lambda pair: (rank(pair), pair))
    baab = set(complete[:len(complete)//2])
    if count % 2 and int(hashlib.sha256((seed+'\0singleton').encode()).hexdigest(), 16) % 2:
        baab.add(count//2)
    blocks = []; batches = []; routes = []
    for pair in range((count+1)//2):
        indices = list(range(2*pair, min(2*pair+2, count)))
        conditions = ['shadow', 'control', 'control', 'shadow'] if pair in baab else ['control', 'shadow', 'shadow', 'control']
        blocks.append({'pair': pair, 'indices': indices, 'orientation': 'BAAB' if pair in baab else 'ABBA',
            'rankSha256': rank(pair), 'conditions': conditions})
        observed = Counter()
        for period, condition in enumerate(conditions):
            replica = observed[condition]; observed[condition] += 1
            batches.append({'batch': len(batches), 'pair': pair, 'period': period,
                'condition': condition, 'replica': replica, 'indices': indices})
            for index in indices:
                routes.append({'ordinal': len(routes), 'pair': pair, 'period': period,
                    'index': index, 'condition': condition, 'replica': replica,
                    'routeKey': f'{pair}:{period}:{index}:{condition}'})
    return {'schemaVersion': SCHEDULE_SCHEMA, 'orderSeedSha256': seed,
        'blocks': blocks, 'batches': batches, 'routes': routes}


def evidence(context, value):
    count = len(context['inputs']); blocks = value['blocks']; routes = value['routes']
    identities = [(r['index'], r['condition'], r['replica']) for r in routes]
    require(len(identities) == len(set(identities)) == 4*count, 'Repeated or missing replication identity')
    require(Counter(r['condition'] for r in routes) == {'control': 2*count, 'shadow': 2*count}, 'Incomplete condition denominator')
    complete = [b for b in blocks if len(b['indices']) == 2]
    counts = Counter(b['orientation'] for b in complete)
    require(abs(counts['ABBA']-counts['BAAB']) <= 1, 'Complete blocks have unbalanced orientation')
    for block in blocks:
        conditions = block['conditions']
        require(Counter(conditions) == {'control': 2, 'shadow': 2}
            and sum(i for i, c in enumerate(conditions) if c == 'control') == 3
            and sum(i for i, c in enumerate(conditions) if c == 'shadow') == 3,
            'Case contrast does not balance counts and a linear period trend')
    return {'originalCases': count, 'originalGroups': len({c['groupId'] for c in context['inputs']}),
        'contextEligibleCases': context['contextEligibleCases'], 'contextTooLongCases': context['contextTooLongCases'],
        'plannedWorkflows': 4*count, 'plannedPrimaryScoringCalls': 4*count, 'plannedDecisionScoringCalls': 2*count,
        'plannedControlWorkflows': 2*count, 'plannedShadowWorkflows': 2*count,
        'plannedBatches': len(value['batches']), 'plannedTwoInputBatches': 4*(count//2),
        'plannedSingletonBatches': 4*(count % 2), 'completeBlockOrientations': dict(sorted(counts.items())),
        'uniqueRouteKeys': len({r['routeKey'] for r in routes}), 'allCasesFourPeriods': True,
        'linearPeriodTrendBalancedAlgebraically': True, 'elapsedWallTimeTrendRemoved': False, 'nonlinearDriftRemoved': False,
        'cacheCarryoverRemoved': False, 'groupDependenceRemoved': False,
        'differentOutputsWillBeRetained': True, 'actualTwoSlotOverlapEstablished': False,
        'newModelCalls': 0, 'causalOverheadEstablished': False}


def context_file(path, context_sha):
    return validate_context(parse_json(pinned_input(path, context_sha, 32*1024*1024)))


def create_plan(root, context_path, context_sha, commit, source_files):
    context = context_file(context_path, context_sha)
    sources_at(root, context['sourceCommit'], context['sourceFiles'], CONTEXT_PATHS)
    sources_at(root, commit, source_files, SOURCE_PATHS)
    value = schedule(len(context['inputs']), context_sha)
    return sealed({'schemaVersion': PLAN_SCHEMA, 'status': 'planned', 'createdAt': paired.now(),
        'sourceCommit': commit, 'sourceFiles': source_files, 'contextProfileFileSha256': context_sha,
        'context': context, 'protocol': deepcopy(PROTOCOL), 'generation': deepcopy(paired.GENERATION),
        'settings': deepcopy(paired.SETTINGS), 'schedule': value, 'evidence': evidence(context, value), **FLAGS})


def verify_plan(root, path, file_sha, context_path, context_sha):
    plan = verify_seal(parse_json(pinned_input(path, file_sha, 64*1024*1024)), PLAN_SCHEMA)
    require(set(plan) == {'schemaVersion', 'sha256', 'status', 'createdAt', 'sourceCommit', 'sourceFiles',
        'contextProfileFileSha256', 'context', 'protocol', 'generation', 'settings', 'schedule', 'evidence', *FLAGS}, 'Design schema changed')
    require(plan['status'] == 'planned', 'A design is not a measured result')
    timestamp(plan['createdAt'], 'design.createdAt')
    same({k: plan[k] for k in FLAGS}, FLAGS, 'Design grants measured outcomes or authority')
    context = context_file(context_path, context_sha)
    same(plan['contextProfileFileSha256'], context_sha, 'Design rebound to another context file')
    same(plan['context'], context, 'Whole development inputs changed')
    same(plan['protocol'], PROTOCOL, 'Posthoc replication protocol')
    same(plan['generation'], paired.GENERATION, 'Primary generation changed')
    same(plan['settings'], paired.SETTINGS, 'Primary runner settings changed')
    sources_at(root, context['sourceCommit'], context['sourceFiles'], CONTEXT_PATHS)
    source_files = sources_at(root, plan['sourceCommit'], plan['sourceFiles'], SOURCE_PATHS)
    expected = schedule(len(context['inputs']), context_sha)
    same(plan['schedule'], expected, 'Prospective order, identity or whole denominator changed')
    same(plan['evidence'], evidence(context, expected), 'Design evidence differs from the scheduled inventory')
    return sealed({'schemaVersion': VERIFICATION_SCHEMA, 'status': 'pass', 'verifiedAt': paired.now(),
        'planFileSha256': file_sha, 'contextProfileFileSha256': context_sha, 'sourceCommit': plan['sourceCommit'],
        'verifiedSourceFiles': len(source_files), 'verifiedContextSourceFiles': len(context['sourceFiles']),
        'evidence': plan['evidence'], 'modelCallsDuringVerification': 0, **FLAGS})
