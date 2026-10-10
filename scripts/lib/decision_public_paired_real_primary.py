"""Counterbalanced bounded pairs with real primary and whole original inputs."""
from copy import deepcopy
import hashlib
import re
import sys
from urllib.parse import parse_qs, urlsplit

from decision_runtime.contracts import fields, Request, parse_json, number
from decision_runtime.metrics import outcome
from scripts.lib import decision_public_real_primary as common
from scripts.lib.decision_public_workflow_paired import busy_result
from scripts.lib.decision_performance import validate_result
from scripts.lib.decision_shadow_pilot import require, timestamp
from scripts.lib.decision_shadow_sli import same_json

PLAN_SCHEMA = 'agat.decision.public-paired-real-primary-plan.v1'
RESULT_SCHEMA = 'agat.decision.public-paired-real-primary-result.v1'
VERIFICATION_SCHEMA = 'agat.decision.public-paired-real-primary-verification.v1'
WORKFLOW_SCHEMA = 'agat.decision.public-paired-real-primary-workflow.v1'
DRIVER_PATH = 'scripts/run-public-support-paired-real-primary.mts'
PROTOCOL = {**common.PROTOCOL, 'kind': 'paired_counterbalanced_original_inventory', 'workerConcurrency': 2,
    'globalMaxConcurrency': 2, 'pairSize': 2, 'conditionOrder': 'alternate_by_original_pair', 'primaryNumParallel': 2,
    'busyWitness': 'actual_pending_peer_and_active_native_metrics', 'inputCreationSkewMaxMs': 250}
GENERATION = common.GENERATION
SETTINGS = {**common.SETTINGS, 'OLLAMA_NUM_PARALLEL': '2'}
WARMUP_REQUEST = common.WARMUP_REQUEST
SOURCE_PATHS = [*common.SOURCE_PATHS, 'scripts/run-public-support-paired-real-primary.py', DRIVER_PATH,
    'scripts/verify-public-support-paired-real-primary.py', 'scripts/test/test_decision_public_paired_real_primary.py']
ARTIFACTS = common.ARTIFACTS | {'paired-batches.jsonl', 'paired-primary-witnesses.jsonl', 'admission-metrics.jsonl', 'cohort-http.jsonl'}
now = common.now
journal = common.journal
raw_body = common.raw_body
shared_config = common.shared_config
primary = common.primary


class OwnedPrimary(common.OwnedPrimary):
    settings = SETTINGS


def prepare(root, binaries, models):
    value = common.prepare(root, binaries, models); value['settings'] = deepcopy(SETTINGS); return value


def batches(count):
    return [(pair, condition, list(range(2*pair, min(2*pair+2, count)))) for pair in range((count+1)//2)
        for condition in (('control', 'shadow') if pair % 2 == 0 else ('shadow', 'control'))]


def route_order(count): return [(i, condition) for _, condition, indices in batches(count) for i in indices]


def ordered_primary(rows, order):
    indexed = {(r['index'], r['condition']): r for r in rows}
    require(len(indexed) == len(rows) == len(order) and set(indexed) == set(order), 'Missing/repeated paired primary request')
    return [indexed[key] for key in order]


def verify_decision(context, case, request, row, response, observation):
    if row['httpStatus'] == 503:
        busy_result(response, context['profile'])
        require(observation['status'] == 'unavailable' and observation['reason'] == 'busy' and 'result' not in observation,
            'Busy became a typed model result or unknown caller')
    else:
        validate_result(response, request, context['profile'])
        require(row['httpStatus'] == (200 if case['contextEligible'] else 422)
            and same_json(observation['result'], response) and observation['status'] == response['status'], 'Typed native result lost or context changed')
        if case['contextEligible']: require(response['status'] in ('ok', 'abstain') and response['inputTokens'] == case['inputTokens'], 'Eligible input failed')
        else: require(response['status'] == 'error' and response['reason'] == 'context_too_long', 'Overlong whole input was hidden')
    timing = fields(observation['callerTiming'], {'schemaVersion', 'clock', 'boundary', 'durationMs'})
    require(timing['schemaVersion'] == 'agat.decision.caller-timing.v1' and timing['clock'] == 'monotonic' and timing['boundary'] == 'local_http_call'
        and abs(number(timing['durationMs'], 0, 10000)-number(row['elapsedMs'], 0, 10000)) <= 250, 'Paired caller timing omits HTTP work')
    return outcome(response)


def verify_posted_return(value, observation):
    expected = {'result', 'callerTiming'} if 'result' in observation else {'status', 'reason', 'callerTiming'}
    require(set(value) == expected and same_json(value, {k: observation[k] for k in expected}), 'Accepted caller return differs from durable result/fallback')


def stage(trace, case):
    require(trace['truncated'] is False and trace['run']['input'] == case['request']['state'] and trace['run']['name'] == PROTOCOL['processName']
        and trace['run']['replayOfRunId'] is None, 'Truncated/transformed/replayed paired trace')
    candidates = [s for s in trace['run']['stages'] if s['processNodeId'] == 'agent']
    require(len(candidates) == 1 and candidates[0]['input'] is None and candidates[0]['attempt'] == 1, 'Duplicated original input or retry')
    return candidates[0]


def assignment(trace):
    stages = trace['decisionCallerAccounting']['stages']
    require(len(stages) == 1 and len(stages[0]['assignments']) == 1 and stages[0]['coverage'] == 'complete', 'Incomplete/repeated actual assignment')
    return stages[0]['assignments'][0]


def metrics(raw):
    require(isinstance(raw, str) and 0 < len(raw.encode()) <= 1048576, 'Missing native admission metrics')
    origins = re.findall(r'^agat_decision_server_start_time_seconds ([0-9.]+)$', raw, re.M)
    active = re.findall(r'^agat_decision_requests_in_progress ([0-9.]+)$', raw, re.M)
    ready = re.findall(r'^agat_decision_backend_ready ([0-9.]+)$', raw, re.M)
    require(len(origins) == len(active) == len(ready) == 1 and float(ready[0]) == 1 and float(active[0]) in (0, 1), 'Wrong native readiness/active gauge')
    return float(origins[0]), int(float(active[0]))


def verify_pairs(context, recipe, driver, artifacts):
    count = len(context['inputs']); routes = common.journal(artifacts['workflow-routes.jsonl'])
    require(driver['workerConcurrency'] == 2 and driver['primaryMaximumActive'] == 2 and 1 <= driver['decisionMaximumActive'] <= 2
        and len({c['inputSha256'] for c in context['inputs']}) == count, 'Serial worker or ambiguous original input association')
    route_by_key = {(r['index'], r['condition']): r for r in routes}; primaries = {(r['index'], r['condition']): r for r in journal(artifacts['primary-http.jsonl'])}
    batch_rows = journal(artifacts['paired-batches.jsonl']); witnesses = journal(artifacts['paired-primary-witnesses.jsonl']); expected = batches(count)
    require(len(batch_rows) == len(expected) and len(witnesses) == 2*(count//2), 'Missing actual paired batch or primary witness')
    by_batch = {(w['pair'], w['condition']): w for w in witnesses}; require(len(by_batch) == len(witnesses), 'Repeated primary overlap witness')
    previous = timestamp(recipe['startAt'], 'window.startAt'); overlap = []; original_assignments = {}
    for row, (pair, condition, indices) in zip(batch_rows, expected):
        require(row['pair'] == pair and row['condition'] == condition and row['indices'] == indices
            and row['runIds'] == [route_by_key[i, condition]['runId'] for i in indices], 'Pair changed original order/condition/denominator')
        begin = timestamp(row['startedAt'], 'batch.startedAt'); end = timestamp(row['completedAt'], 'batch.completedAt')
        require(previous <= begin <= end and abs((end-begin).total_seconds()*1000-number(row['elapsedMs'], 0, PROTOCOL['instanceDeadlineMs'])) <= 10, 'Paired batches overlap or crossed budget')
        previous = end; started = [timestamp(route_by_key[i, condition]['startedAt'], 'route.startedAt') for i in indices]
        require((max(started)-min(started)).total_seconds()*1000 <= 250 and all(begin <= s <= end for s in started)
            and all(timestamp(route_by_key[i, condition]['completedAt'], 'route.completedAt') <= end for i in indices), 'Actual pair was serial or outside batch')
        if len(indices) == 1: continue
        p = [primaries[i, condition] for i in indices]
        intersection = (min(timestamp(r['completedAt'], 'primary.end') for r in p)-max(timestamp(r['nativeStartedAt'], 'primary.nativeStart') for r in p)).total_seconds()*1000
        require(intersection > 0, 'Native primary HTTP requests never overlapped'); overlap.append(intersection)
        witness = by_batch[pair, condition]; require(witness['indices'] == indices and witness['runIds'] == row['runIds'] and len(witness['traces']) == 2, 'Primary witness rebound')
        observed = timestamp(witness['capturedAt'], 'witness.capturedAt'); nodes = []
        for index, item in zip(indices, witness['traces']):
            route = route_by_key[index, condition]; trace = raw_body(item, 'body'); s = stage(trace, context['inputs'][index])
            require(item['httpStatus'] == 200 and item['path'] == '/api/v1/runs/'+route['runId']+'/trace' and trace['run']['id'] == route['runId']
                and trace['run']['status'] == 'running' and s['status'] in ('assigned', 'running') and s['output'] is None
                and s['id'] == route['stageId'] and s['worker']['nodeId'] == s['nodeId'] and bool(s['nodeId'])
                and trace['decisionObservations'] == [], 'Actual two-slot pending primary witness missing')
            final = stage(parse_json(artifacts[route['traceFile']]), context['inputs'][index]); nodes.append(s['nodeId'])
            require(final['nodeId'] == s['nodeId'] and final['worker'] == s['worker'] and primaries[index, condition]['stageId'] == s['id'], 'Actual pair reassigned or primary rebound')
            require(timestamp(primaries[index, condition]['startedAt'], 'primary.start') <= observed <= timestamp(primaries[index, condition]['completedAt'], 'primary.end'), 'Primary witness captured after response')
            if condition == 'shadow':
                a = assignment(trace); require(a['negotiated'] is True and a['intent'] is False and a['returned'] is None
                    and a['assignmentId'] == assignment(parse_json(artifacts[route['traceFile']]))['assignmentId'], 'Held primary lost original assignment')
                original_assignments[index] = a['assignmentId']
            else: require(trace['decisionCallerAccounting']['stages'] == [] and trace['decisionStageInventory']['stages'] == [], 'Control requested shadow')
        require(len(set(nodes)) == 1 and (condition != 'shadow' or len({original_assignments[i] for i in indices}) == 2), 'Pair did not occupy two distinct leases on one actual worker')
    require(previous <= timestamp(driver['actualWindow']['endAt'], 'window.endAt'), 'Census closed before final pair')
    routes_by_run = {r['runId']: r for r in routes}
    for row in journal(artifacts['coordinator-http.jsonl']):
        require(row['runId'] in routes_by_run and row['leaseId'] == row['path'].split('/')[4], 'Actual lease path has no owned run binding')
        route = routes_by_run[row['runId']]; final = stage(parse_json(artifacts[route['traceFile']]), context['inputs'][route['index']])
        require(row['index'] == route['index'] and row['condition'] == route['condition'] and row['stageId'] == route['stageId']
            and row['nodeId'] == final['nodeId'] and row['pair'] == route['index']//2, 'Readonly actual store lease/run/stage binding changed')
    census_rows = journal(artifacts['cohort-http.jsonl']); require(len(census_rows) == 2 and [r['condition'] for r in census_rows] == ['control', 'shadow'], 'Missing actual authenticated census responses')
    for row in census_rows:
        c = row['condition']; scope = recipe['processes'][c]; url = urlsplit(row['path'])
        require(row['httpStatus'] == 200 and row['unauthenticatedStatus'] == 401 and url.path == '/api/v1/processes/'+scope['processId']+'/decision-shadow-cohort'
            and parse_qs(url.query) == {'processVersion': [str(scope['version'])], 'startAt': [recipe['startAt']], 'endAt': [driver['actualWindow']['endAt']]}
            and row['bodySha256'] == hashlib.sha256(artifacts['cohort-'+c+'.http.json']).hexdigest(), 'Actual census raw response/authentication rebound')
    decisions = {d['index']: d for d in journal(artifacts['decision-http.jsonl'])}; samples = journal(artifacts['admission-metrics.jsonl'])
    require(len(samples) >= count and {s['index'] for s in samples} == set(decisions), 'Missing native admission snapshots')
    origins = set(); busy_witnesses = []; busy_ineligible = 0
    for index, d in decisions.items():
        rows = [s for s in samples if s['index'] == index]; require(sum(s['source'] == 'before_forward' for s in rows) == 1, 'Repeated or missing admission start sample')
        observed_peers = []
        for sample in rows:
            require(sample['runId'] == d['runId'] and sample['stageId'] == d['stageId'] and sample['source'] in ('before_forward', 'after_busy_response')
                and sample['metricsSha256'] == hashlib.sha256(sample['metricsRaw'].encode()).hexdigest(), 'Raw admission metrics rebound')
            origin, active = metrics(sample['metricsRaw']); origins.add(origin); captured = timestamp(sample['capturedAt'], 'sample.capturedAt')
            require(timestamp(d['startedAt'], 'decision.startedAt') <= captured <= timestamp(d['completedAt'], 'decision.completedAt'), 'Admission metrics outside native HTTP attempt')
            for peer in sample['pendingPeers']:
                require(peer['index'] != index and peer['index']//2 == index//2 and peer['index'] in decisions, 'Foreign native peer')
                partner = decisions[peer['index']]
                require(partner['runId'] == peer['runId'] and partner['stageId'] == peer['stageId'], 'Native pending peer identity rebound')
                if active == 1 and timestamp(partner['startedAt'], 'peer.startedAt') <= captured <= timestamp(partner['completedAt'], 'peer.completedAt'):
                    observed_peers.append(peer['index'])
        if d['httpStatus'] == 503:
            require(observed_peers, 'Busy has no actual active native peer witness'); busy_witnesses.append(index)
            busy_ineligible += int(not context['inputs'][index]['contextEligible'])
    require(len(origins) == 1, 'Native restarted during paired inventory')
    return {'pairedBatches': len(batch_rows), 'actualTwoSlotPrimaryWitnesses': len(witnesses),
        'nativePrimaryHttpOverlapMs': {**common.distribution(overlap), 'min': round(min(overlap), 3)},
        'workerConcurrency': 2, 'primaryNumParallel': 2, 'nativeBusyWitnesses': len(busy_witnesses), 'busyBeforeContextCheckCases': busy_ineligible,
        'primaryConcurrencyCapacityQualified': False, 'causalOverheadEstablished': False}


def verify_native_origin(result, artifacts):
    expected = result['samples'][0]['serverStart']
    require(all(metrics(s['metricsRaw'])[0] == expected for s in journal(artifacts['admission-metrics.jsonl'])), 'Admission metrics crossed native epoch')
    verify_primary_runner(artifacts['primary.log'])


def verify_primary_runner(raw):
    log = raw.decode(errors='strict')
    require(re.search(r'n_seq_max\s*=\s*2\b', log) and re.search(r'n_ctx_seq\s*=\s*32768\b', log)
        and re.search(r'n_ctx\s*=\s*65536\b', log) and '-np 2' in log, 'Requested primary parallelism differs from actual runner slots/context')


def verify_inventory(context, protocol, recipe, driver, artifacts):
    evidence = common.verify_inventory(context, protocol, recipe, driver, artifacts, suite=sys.modules[__name__])
    return {**evidence, 'schemaVersion': 'agat.decision.public-paired-real-primary-inventory.v1', **verify_pairs(context, recipe, driver, artifacts)}
