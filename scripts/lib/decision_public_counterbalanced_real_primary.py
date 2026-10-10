"""Execute the previously sealed ABBA/BAAB design on two real primary slots."""
from collections import Counter
from copy import deepcopy
import hashlib
from pathlib import Path
import sys
from urllib.parse import parse_qs, urlsplit

from decision_runtime.artifacts import verify_seal
from decision_runtime.contracts import parse_json, number
from scripts.lib import decision_public_counterbalance as design
from scripts.lib import decision_public_latency_decomposition as latency
from scripts.lib import decision_public_paired_real_primary as paired
from scripts.lib import decision_public_real_primary as common
from scripts.lib.decision_public_load_verification import same
from scripts.lib.decision_public_sources import pinned_input
from scripts.lib.decision_shadow_pilot import require, timestamp

PLAN_SCHEMA = 'agat.decision.public-counterbalanced-real-primary-plan.v1'
RESULT_SCHEMA = 'agat.decision.public-counterbalanced-real-primary-result.v1'
VERIFICATION_SCHEMA = 'agat.decision.public-counterbalanced-real-primary-verification.v1'
WORKFLOW_SCHEMA = 'agat.decision.public-counterbalanced-real-primary-workflow.v1'
DRIVER_PATH = 'scripts/run-public-support-counterbalanced-real-primary.mts'
PROTOCOL = {**deepcopy(design.PROTOCOL), 'kind': 'counterbalanced_replication_original_inventory'}
GENERATION = paired.GENERATION
SETTINGS = paired.SETTINGS
WARMUP_REQUEST = paired.WARMUP_REQUEST
SOURCE_PATHS = [*design.SOURCE_PATHS, 'scripts/run-public-support-counterbalanced-real-primary.py', DRIVER_PATH,
    'scripts/verify-public-support-counterbalanced-real-primary.py', 'scripts/test/test_decision_public_counterbalanced_real_primary.py']
ARTIFACTS = paired.ARTIFACTS | {'replication-design.json'}
EXTRA_PLAN_FIELDS = {'replicationDesignFileSha256'}
OwnedPrimary = paired.OwnedPrimary
prepare = paired.prepare
now = paired.now
journal = paired.journal
raw_body = paired.raw_body
shared_config = paired.shared_config
primary = paired.primary
verify_decision = paired.verify_decision
verify_posted_return = paired.verify_posted_return
verify_native_origin = paired.verify_native_origin


def prepare_design(root, args, context):
    path = args.replication_design.resolve()
    require(path.is_relative_to((root/'docs/private').resolve()), 'Use an owned private prospective design')
    design.verify_plan(root, path, args.replication_design_file_sha256,
        args.context_profile.resolve(), args.context_profile_file_sha256)
    raw = pinned_input(path, args.replication_design_file_sha256, 64*1024*1024)
    same(parse_json(raw)['context'], context, 'Prospective design changed whole inputs')
    return {'plan': {'replicationDesignFileSha256': args.replication_design_file_sha256},
        'artifacts': {'replication-design.json': raw}}


def verify_design(root, directory, plan, context_path, context_sha):
    receipt = design.verify_plan(root, directory/'replication-design.json', plan['replicationDesignFileSha256'], context_path, context_sha)
    parent = parse_json((directory/'replication-design.json').read_bytes())
    require(timestamp(parent['createdAt'], 'design.createdAt') <= timestamp(plan['createdAt'], 'native.createdAt'),
        'Prospective design was created after the native plan')
    return receipt


def parent_design(context, recipe, artifacts):
    raw = artifacts['replication-design.json']; parent = verify_seal(parse_json(raw), design.PLAN_SCHEMA)
    require(parent['status'] == 'planned', 'Replication design is not prospective')
    same({k: parent[k] for k in design.FLAGS}, design.FLAGS, 'Design grants measured outcomes or authority')
    same(parent['context'], context, 'Replication omits or changes an original case')
    same(parent['protocol'], design.PROTOCOL, 'Native execution changed the frozen design')
    same(parent['generation'], GENERATION, 'Prospective primary generation differs')
    same(parent['settings'], SETTINGS, 'Prospective primary settings differ')
    require(recipe['replicationDesignFileSha256'] == hashlib.sha256(raw).hexdigest()
        and recipe['contextProfileFileSha256'] == parent['contextProfileFileSha256'], 'Recipe rebound to another prospective plan/context')
    expected = design.schedule(len(context['inputs']), parent['contextProfileFileSha256'])
    same(parent['schedule'], expected, 'Changed prospective seeded order or replica identities')
    same(parent['evidence'], design.evidence(context, expected), 'Prospective denominator differs')
    return parent


def route_order_for(context, recipe):
    return [(r['index'], r['condition'], r['replica']) for r in design.schedule(
        len(context['inputs']), recipe['contextProfileFileSha256'])['routes']]


def key(row): return row['index'], row['condition'], row['replica']
def trace_name(row): return f"trace-{row['index']:03d}-{row['condition']}-r{row['replica']}.http.json"
def trace_names(context):
    return {trace_name({'index': i, 'condition': c, 'replica': r}) for i in range(len(context['inputs']))
        for c in ('control', 'shadow') for r in (0, 1)}


def ordered_primary(rows, order):
    indexed = {key(r): r for r in rows}
    require(len(indexed) == len(rows) == len(order) and set(indexed) == set(order), 'Missing/repeated replicated primary request')
    return [indexed[k] for k in order]


def verify_pairs(context, recipe, driver, artifacts, parent):
    count = len(context['inputs']); expected = parent['schedule']; routes = journal(artifacts['workflow-routes.jsonl'])
    require(driver['workerConcurrency'] == 2 and driver['primaryMaximumActive'] == (2 if count >= 2 else 1)
        and 1 <= driver['decisionMaximumActive'] <= 2 and len({c['inputSha256'] for c in context['inputs']}) == count,
        'Worker slots or original input association changed')
    require(len(routes) == len(expected['routes']), 'Missing or repeated replicated route')
    for row, prescribed in zip(routes, expected['routes']):
        same({k: row[k] for k in prescribed}, prescribed, 'Route period, original order or replica changed')
    route_by_key = {key(r): r for r in routes}; primaries = {key(r): r for r in journal(artifacts['primary-http.jsonl'])}
    for row in primaries.values():
        route = route_by_key[key(row)]
        same({k: row[k] for k in ('pair', 'period', 'replica', 'index', 'condition', 'runId')},
            {k: route[k] for k in ('pair', 'period', 'replica', 'index', 'condition', 'runId')}, 'Primary replica metadata rebound')
    batches = journal(artifacts['paired-batches.jsonl']); witnesses = journal(artifacts['paired-primary-witnesses.jsonl'])
    require(len(batches) == len(expected['batches']) and len(witnesses) == 4*(count//2), 'Missing batch or actual primary witness')
    by_batch = {(w['pair'], w['period']): w for w in witnesses}
    require(len(by_batch) == len(witnesses), 'Repeated primary overlap witness')
    previous = timestamp(recipe['startAt'], 'window.startAt'); overlaps = []
    for row, prescribed in zip(batches, expected['batches']):
        same({k: row[k] for k in prescribed}, prescribed, 'Batch differs from the sealed prospective order')
        pair, period, condition, replica, indices = (row[k] for k in ('pair', 'period', 'condition', 'replica', 'indices'))
        batch_routes = [route_by_key[i, condition, replica] for i in indices]
        require(row['runIds'] == [r['runId'] for r in batch_routes], 'Batch run identities rebound')
        begin = timestamp(row['startedAt'], 'batch.start'); end = timestamp(row['completedAt'], 'batch.end')
        require(previous <= begin <= end and abs((end-begin).total_seconds()*1000-number(row['elapsedMs'], 0, PROTOCOL['instanceDeadlineMs'])) <= 10,
            'Batches overlap or crossed their duration budget')
        previous = end; starts = [timestamp(r['startedAt'], 'route.start') for r in batch_routes]
        require((max(starts)-min(starts)).total_seconds()*1000 <= PROTOCOL['inputCreationSkewMaxMs']
            and all(begin <= s <= end for s in starts) and all(timestamp(r['completedAt'], 'route.end') <= end for r in batch_routes),
            'Inputs were serial or outside their prospective batch')
        if len(indices) == 1: continue
        primary_rows = [primaries[i, condition, replica] for i in indices]
        overlap = (min(timestamp(r['completedAt'], 'primary.end') for r in primary_rows)
            -max(timestamp(r['nativeStartedAt'], 'primary.nativeStart') for r in primary_rows)).total_seconds()*1000
        require(overlap > 0, 'Native primary HTTP calls never overlapped'); overlaps.append(overlap)
        witness = by_batch[pair, period]
        same({k: witness[k] for k in ('pair', 'period', 'condition', 'replica', 'indices')},
            {k: row[k] for k in ('pair', 'period', 'condition', 'replica', 'indices')}, 'Primary witness crossed a period')
        require(witness['runIds'] == row['runIds'] and len(witness['traces']) == 2, 'Primary witness rebound')
        observed = timestamp(witness['capturedAt'], 'witness.capturedAt'); nodes = []; assignments = []
        for index, item in zip(indices, witness['traces']):
            route = route_by_key[index, condition, replica]; trace = raw_body(item, 'body'); stage = paired.stage(trace, context['inputs'][index])
            require(item['httpStatus'] == 200 and item['path'] == '/api/v1/runs/'+route['runId']+'/trace'
                and trace['run']['id'] == route['runId'] and trace['run']['status'] == 'running'
                and stage['status'] in ('assigned', 'running') and stage['output'] is None
                and stage['id'] == route['stageId'] and stage['worker']['nodeId'] == stage['nodeId']
                and bool(stage['nodeId']) and trace['decisionObservations'] == [], 'Two-slot pending primary witness missing')
            final = paired.stage(parse_json(artifacts[route['traceFile']]), context['inputs'][index]); nodes.append(stage['nodeId'])
            require(final['nodeId'] == stage['nodeId'] and final['worker'] == stage['worker']
                and primaries[index, condition, replica]['stageId'] == stage['id'], 'Primary pair was reassigned')
            require(timestamp(primaries[index, condition, replica]['startedAt'], 'primary.start') <= observed
                <= timestamp(primaries[index, condition, replica]['completedAt'], 'primary.end'), 'Primary witness captured after response')
            if condition == 'shadow':
                assignment = paired.assignment(trace)
                require(assignment['negotiated'] is True and assignment['intent'] is False and assignment['returned'] is None
                    and assignment['assignmentId'] == paired.assignment(parse_json(artifacts[route['traceFile']]))['assignmentId'],
                    'Held primary lost its original shadow assignment')
                assignments.append(assignment['assignmentId'])
            else: require(trace['decisionCallerAccounting']['stages'] == [] and trace['decisionStageInventory']['stages'] == [], 'Control called shadow')
        require(len(set(nodes)) == 1 and (condition != 'shadow' or len(set(assignments)) == 2), 'Pair did not occupy two distinct leases on one worker')
    require(previous <= timestamp(driver['actualWindow']['endAt'], 'window.endAt'), 'Census closed before the last period')
    by_run = {r['runId']: r for r in routes}
    for row in journal(artifacts['coordinator-http.jsonl']):
        require(row['runId'] in by_run and row['leaseId'] == row['path'].split('/')[4], 'Lease path lacks owned run binding')
        route = by_run[row['runId']]; final = paired.stage(parse_json(artifacts[route['traceFile']]), context['inputs'][route['index']])
        same({k: row[k] for k in ('pair', 'period', 'replica', 'index', 'condition', 'stageId')},
            {k: route[k] for k in ('pair', 'period', 'replica', 'index', 'condition', 'stageId')}, 'Lease crossed a period or replica')
        require(row['nodeId'] == final['nodeId'], 'Lease node changed')
    return overlaps


def verify_admission(context, artifacts):
    decisions = {(d['index'], d['replica']): d for d in journal(artifacts['decision-http.jsonl'])}
    samples = journal(artifacts['admission-metrics.jsonl']); count = len(context['inputs'])
    require(len(decisions) == 2*count and len(samples) >= 2*count
        and {(s['index'], s['replica']) for s in samples} == set(decisions), 'Missing replicated native admission snapshots')
    origins = set(); busy = []; busy_ineligible = 0
    for identity, decision in decisions.items():
        rows = [s for s in samples if (s['index'], s['replica']) == identity]
        require(sum(s['source'] == 'before_forward' for s in rows) == 1
            and sum(s['source'] == 'after_busy_response' for s in rows) == int(decision['httpStatus'] == 503), 'Missing/repeated admission boundary')
        observed_peers = []
        for sample in rows:
            same({k: sample[k] for k in ('pair', 'period', 'replica', 'index', 'runId', 'stageId')},
                {k: decision[k] for k in ('pair', 'period', 'replica', 'index', 'runId', 'stageId')}, 'Admission sample crossed a replica')
            require(sample['metricsSha256'] == hashlib.sha256(sample['metricsRaw'].encode()).hexdigest(), 'Raw admission metrics pin differs')
            origin, active = paired.metrics(sample['metricsRaw']); origins.add(origin)
            captured = timestamp(sample['capturedAt'], 'admission.capturedAt')
            require(timestamp(decision['startedAt'], 'decision.start') <= captured <= timestamp(decision['completedAt'], 'decision.end'), 'Admission outside native attempt')
            for peer in sample['pendingPeers']:
                require(peer['index'] != decision['index'] and peer['pair'] == decision['pair'] and peer['period'] == decision['period']
                    and peer['replica'] == decision['replica'] and (peer['index'], peer['replica']) in decisions, 'Foreign native peer period')
                partner = decisions[peer['index'], peer['replica']]
                require(partner['runId'] == peer['runId'] and partner['stageId'] == peer['stageId'], 'Native peer identity rebound')
                if active == 1 and timestamp(partner['startedAt'], 'peer.start') <= captured <= timestamp(partner['completedAt'], 'peer.end'):
                    observed_peers.append(peer['runId'])
        if decision['httpStatus'] == 503:
            require(observed_peers, 'Busy has no actual active native peer witness'); busy.append(identity)
            busy_ineligible += int(not context['inputs'][decision['index']]['contextEligible'])
    require(len(origins) == 1, 'Native epoch changed during replication')
    return {'nativeBusyWitnesses': len(busy), 'busyBeforeContextCheckCases': busy_ineligible}


def verify_census(recipe, driver, artifacts):
    rows = journal(artifacts['cohort-http.jsonl'])
    require(len(rows) == 2 and [r['condition'] for r in rows] == ['control', 'shadow'], 'Missing authenticated census responses')
    for row in rows:
        condition = row['condition']; scope = recipe['processes'][condition]; url = urlsplit(row['path'])
        require(row['httpStatus'] == 200 and row['unauthenticatedStatus'] == 401
            and url.path == '/api/v1/processes/'+scope['processId']+'/decision-shadow-cohort'
            and parse_qs(url.query) == {'processVersion': [str(scope['version'])], 'startAt': [recipe['startAt']], 'endAt': [driver['actualWindow']['endAt']]}
            and row['bodySha256'] == hashlib.sha256(artifacts['cohort-'+condition+'.http.json']).hexdigest(), 'Census response/authentication rebound')


def case_ledger(context, artifacts, parent):
    routes = {key(r): r for r in journal(artifacts['workflow-routes.jsonl'])}
    primaries = {key(r): r for r in journal(artifacts['primary-http.jsonl'])}
    decisions = {(r['index'], r['replica']): r for r in journal(artifacts['decision-http.jsonl'])}
    components = ('workflowObservedMs', 'prePrimaryWallMs', 'primaryProxyMonotonicMs', 'postPrimaryWallMs', 'clockClosureResidualMs')
    rows = []
    for index, case in enumerate(context['inputs']):
        replicas = []; requests = []; outputs = []
        for replica in (0, 1):
            phases = {}
            for condition in ('control', 'shadow'):
                identity = index, condition, replica; route = routes[identity]; primary_row = primaries[identity]
                decision = decisions[index, replica] if condition == 'shadow' else None
                caller_ms = None
                if condition == 'shadow':
                    trace = parse_json(artifacts[route['traceFile']]); caller_ms = trace['decisionObservations'][0]['observation']['callerTiming']['durationMs']
                phases[condition] = latency.phases(route, primary_row, decision, caller_ms)
                phases[condition]['period'] = route['period']; requests.append(raw_body(primary_row, 'nativeRequestBody'))
                outputs.append(phases[condition]['outputSha256'])
            delta = {k: round(phases['shadow'][k]-phases['control'][k], 3) for k in components}
            require(abs(delta['workflowObservedMs']-sum(delta[k] for k in components[1:])) <= .005, 'Replicated component delta does not close')
            replicas.append({'replica': replica, **phases, 'shadowMinusControlMs': delta})
        require(all(r == requests[0] for r in requests), 'Primary input/settings changed between the four periods')
        contrast = {k: round(sum(r['shadowMinusControlMs'][k] for r in replicas)/2, 6) for k in components}
        require(abs(contrast['workflowObservedMs']-sum(contrast[k] for k in components[1:])) <= .005, 'Case mean contrast does not close')
        rows.append({'index': index, 'caseId': case['id'], 'groupId': case['groupId'],
            'orientation': parent['schedule']['blocks'][index//2]['orientation'], 'replicas': replicas,
            'meanShadowMinusControlMs': contrast, 'allFourPrimaryRequestsEqual': True, 'allFourPrimaryOutputsEqual': len(set(outputs)) == 1})
    return {'cases': rows, 'originalGroups': len({c['groupId'] for c in context['inputs']}),
        'caseMeanComponentDeltaMs': {k: latency.stats([r['meanShadowMinusControlMs'][k] for r in rows]) for k in components},
        'allFourPrimaryOutputsEqualCases': sum(r['allFourPrimaryOutputsEqual'] for r in rows),
        'differentOutputIndices': [r['index'] for r in rows if not r['allFourPrimaryOutputsEqual']],
        'allCasesFourPeriods': True, 'differentOutputsRetained': True, 'observationsPerCondition': 2,
        'independentCasesAssumed': False, 'elapsedWallTimeTrendRemoved': False, 'cacheCarryoverRemoved': False,
        'causalOverheadEstablished': False}


def verify_inventory(context, protocol, recipe, driver, artifacts):
    parent = parent_design(context, recipe, artifacts)
    evidence = common.verify_inventory(context, protocol, recipe, driver, artifacts, suite=sys.modules[__name__])
    overlaps = verify_pairs(context, recipe, driver, artifacts, parent); verify_census(recipe, driver, artifacts)
    ledger = case_ledger(context, artifacts, parent)
    require(abs(ledger['caseMeanComponentDeltaMs']['workflowObservedMs']['mean']-evidence['matchedWorkflowDeltaMs']['mean']) <= .005,
        'Replica and case contrasts have different denominators')
    return {**evidence, **ledger, **verify_admission(context, artifacts),
        'schemaVersion': 'agat.decision.public-counterbalanced-real-primary-inventory.v1',
        'pairedBatches': len(parent['schedule']['batches']), 'actualTwoSlotPrimaryWitnesses': len(overlaps),
        'nativePrimaryHttpOverlapMs': {**common.distribution(overlaps), 'min': round(min(overlaps), 3) if overlaps else None},
        'prospectiveDesignFileSha256': recipe['replicationDesignFileSha256'], 'designSourceCommit': parent['sourceCommit'],
        'designSourceFiles': len(parent['sourceFiles']), 'workerConcurrency': 2, 'primaryNumParallel': 2,
        'primaryConcurrencyCapacityQualified': False}
