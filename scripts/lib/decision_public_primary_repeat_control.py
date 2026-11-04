"""Whole-inventory A/A primary repeat control; labels cannot change execution."""
from collections import Counter
import hashlib
import json
from urllib.parse import parse_qs, urlsplit

from decision_runtime.contracts import number, parse_json
from scripts.lib import decision_public_real_primary as common
from scripts.lib import decision_public_paired_real_primary as paired
from scripts.lib.decision_public_load_verification import counters, distribution, same
from scripts.lib.decision_shadow_pilot import require, timestamp

PLAN_SCHEMA = 'agat.decision.public-primary-repeat-control-plan.v1'
RESULT_SCHEMA = 'agat.decision.public-primary-repeat-control-result.v1'
VERIFICATION_SCHEMA = 'agat.decision.public-primary-repeat-control-verification.v1'
WORKFLOW_SCHEMA = 'agat.decision.public-primary-repeat-control-workflow.v1'
DRIVER_PATH = 'scripts/run-public-support-primary-repeat-control.mts'
PROTOCOL = {**paired.PROTOCOL, 'kind': 'paired_primary_only_repeat_control',
    'conditions': ['repeatA', 'repeatB'], 'processVersions': {'repeatA': 1, 'repeatB': 1},
    'busyWitness': 'no_native_case_calls', 'publishedVersionCount': 1,
    'backgroundNative': 'resident_after_two_warmups_without_case_scores', 'outputEqualityRequired': False}
NATIVE_CASE_CALLS = 0
GENERATION = paired.GENERATION
SETTINGS = paired.SETTINGS
WARMUP_REQUEST = paired.WARMUP_REQUEST
OwnedPrimary = paired.OwnedPrimary
prepare = paired.prepare
now = common.now
journal = common.journal
raw_body = common.raw_body
shared_config = common.shared_config
primary = common.primary
SOURCE_PATHS = [*paired.SOURCE_PATHS, 'scripts/run-public-support-primary-repeat-control.py', DRIVER_PATH,
    'scripts/verify-public-support-primary-repeat-control.py', 'scripts/test/test_decision_public_primary_repeat_control.py']
ARTIFACTS = (common.ARTIFACTS - {'graph-shadow.json', 'cohort-control.http.json', 'cohort-shadow.http.json'}) | {
    'paired-batches.jsonl', 'paired-primary-witnesses.jsonl', 'native-background-metrics.jsonl', 'cohort.http.json', 'cohort-http.jsonl'}


def batches(count):
    return [(pair, condition, list(range(2*pair, min(2*pair+2, count)))) for pair in range((count+1)//2)
        for condition in (('repeatA', 'repeatB') if pair % 2 == 0 else ('repeatB', 'repeatA'))]


def route_order(count): return [(i, condition) for _, condition, indices in batches(count) for i in indices]


def native_counters(raw):
    return counters({'label': 'background', 'elapsedMs': 0, 'health': {}, 'metricsRaw': raw, 'ownedPids': [], 'processRaw': ''})


def verify_native_origin(result, artifacts):
    expected = result['samples'][1]
    for row in journal(artifacts['native-background-metrics.jsonl']):
        values, origin = native_counters(row['metricsRaw'])
        same(values, expected['counters'], 'Native background scored a case or reset counters')
        require(origin == expected['serverStart'], 'Background native restarted during A/A inventory')
    paired.verify_primary_runner(artifacts['primary.log'])


def verify_batches(context, recipe, driver, artifacts, routes, primaries):
    count = len(context['inputs']); expected = batches(count)
    rows = journal(artifacts['paired-batches.jsonl']); witnesses = journal(artifacts['paired-primary-witnesses.jsonl'])
    background = journal(artifacts['native-background-metrics.jsonl'])
    require(len(rows) == len(background) == len(expected) and len(witnesses) == 2*(count//2), 'Missing batch, background snapshot or two-slot witness')
    by_witness = {(w['pair'], w['condition']): w for w in witnesses}
    require(len(by_witness) == len(witnesses), 'Repeated primary overlap witness')
    by_route = {(r['index'], r['condition']): r for r in routes}; by_primary = {(r['index'], r['condition']): r for r in primaries}
    previous = timestamp(recipe['startAt'], 'window.startAt'); overlap = []; origins = set(); counts_seen = []
    for row, snapshot, (pair, condition, indices) in zip(rows, background, expected):
        require(row['pair'] == snapshot['pair'] == pair and row['condition'] == snapshot['condition'] == condition
            and row['indices'] == indices and row['runIds'] == [by_route[i, condition]['runId'] for i in indices], 'Batch labels/order/whole-input denominator changed')
        begin = timestamp(row['startedAt'], 'batch.start'); end = timestamp(row['completedAt'], 'batch.end')
        captured = timestamp(snapshot['capturedAt'], 'background.capturedAt')
        require(previous <= begin <= end <= captured and abs((end-begin).total_seconds()*1000-number(row['elapsedMs'], 0, PROTOCOL['instanceDeadlineMs'])) <= 10,
            'A/A batches overlap or snapshot is outside its boundary')
        require(snapshot['httpStatus'] == 200 and snapshot['path'] == '/metrics'
            and snapshot['metricsSha256'] == hashlib.sha256(snapshot['metricsRaw'].encode()).hexdigest(), 'Background raw metrics pin changed')
        values, origin = native_counters(snapshot['metricsRaw']); origins.add(origin); counts_seen.append(values)
        require(sum(values.values()) == 2, 'A/A control made a native case call')
        previous = captured
        started = [timestamp(by_route[i, condition]['startedAt'], 'route.start') for i in indices]
        require((max(started)-min(started)).total_seconds()*1000 <= PROTOCOL['inputCreationSkewMaxMs']
            and all(begin <= value <= end for value in started)
            and all(timestamp(by_route[i, condition]['completedAt'], 'route.end') <= end for i in indices), 'Original pair was serial or outside batch')
        if len(indices) == 1: continue
        p = [by_primary[i, condition] for i in indices]
        intersection = (min(timestamp(r['completedAt'], 'primary.end') for r in p)-max(timestamp(r['nativeStartedAt'], 'primary.nativeStart') for r in p)).total_seconds()*1000
        require(intersection > 0, 'Native primary HTTP requests never overlapped'); overlap.append(intersection)
        witness = by_witness[pair, condition]
        require(witness['indices'] == indices and witness['runIds'] == row['runIds'] and len(witness['traces']) == 2, 'Primary witness rebound')
        observed = timestamp(witness['capturedAt'], 'witness.capturedAt'); nodes = []
        for index, item in zip(indices, witness['traces']):
            route = by_route[index, condition]; trace = raw_body(item, 'body'); stage = paired.stage(trace, context['inputs'][index])
            require(item['httpStatus'] == 200 and item['path'] == '/api/v1/runs/'+route['runId']+'/trace'
                and trace['run']['id'] == route['runId'] and trace['run']['status'] == 'running'
                and stage['status'] in ('assigned', 'running') and stage['output'] is None and stage['id'] == route['stageId']
                and stage['worker']['nodeId'] == stage['nodeId'] and bool(stage['nodeId']), 'Actual two-slot pending primary witness missing')
            require(trace['decisionObservations'] == [] and all(trace[k]['stages'] == [] for k in
                ('decisionStageInventory', 'decisionCallerAccounting', 'decisionAssignmentHistory')), 'A/A witness enabled shadow')
            final = paired.stage(parse_json(artifacts[route['traceFile']]), context['inputs'][index]); nodes.append(stage['nodeId'])
            require(final['nodeId'] == stage['nodeId'] and final['worker'] == stage['worker']
                and by_primary[index, condition]['stageId'] == stage['id'], 'Pair reassigned or primary rebound')
            require(timestamp(by_primary[index, condition]['startedAt'], 'primary.start') <= observed <= timestamp(by_primary[index, condition]['completedAt'], 'primary.end'),
                'Pending primary witness captured after response')
        require(len(set(nodes)) == 1, 'Pair did not occupy two actual leases on one worker')
    require(len(origins) == 1 and all(c == counts_seen[0] for c in counts_seen)
        and previous <= timestamp(driver['actualWindow']['endAt'], 'window.endAt'), 'Background epoch/outcomes changed or census closed early')
    return {'pairedBatches': len(rows), 'actualTwoSlotPrimaryWitnesses': len(witnesses), 'nativeBackgroundSnapshots': len(background),
        'nativePrimaryHttpOverlapMs': {**distribution(overlap), 'min': round(min(overlap), 3)},
        'workerConcurrency': 2, 'primaryNumParallel': 2, 'primaryConcurrencyCapacityQualified': False, 'causalOverheadEstablished': False}


def verify_inventory(context, protocol, recipe, driver, artifacts):
    common.validate_context(context); same(protocol, PROTOCOL, 'Posthoc A/A protocol'); same(recipe['protocol'], protocol, 'Workflow/plan protocol differs')
    count = len(context['inputs']); expected_order = route_order(count)
    require(recipe['schemaVersion'] == WORKFLOW_SCHEMA and recipe['primaryModel'] == primary.MODEL and recipe['publishedVersionCount'] == 1,
        'Unsupported or multiply published A/A graph')
    require(driver['status'] == 'observed' and driver['failure'] is None and driver['workerExitCode'] == 0
        and driver['primaryCalls'] == 2*count and driver['decisionCalls'] == driver['decisionMaximumActive'] == 0
        and driver['authenticatedStatus'] == 200 and driver['unauthenticatedStatus'] == 401 and driver['schedulerMode'] == 'sequential'
        and driver['globalMaxConcurrency'] == driver['workerConcurrency'] == driver['primaryMaximumActive'] == 2, 'Driver or A/A denominator failed')
    require(len({c['inputSha256'] for c in context['inputs']}) == count, 'Ambiguous original input association')
    routes = journal(artifacts['workflow-routes.jsonl']); same(driver['routes'], routes, 'Route journal differs')
    rows = paired.ordered_primary(journal(artifacts['primary-http.jsonl']), expected_order)
    leases = journal(artifacts['coordinator-http.jsonl'])
    require(len(routes) == len(rows) == 2*count and driver['leaseCalls'] == len(leases)
        and journal(artifacts['decision-http.jsonl']) == [], 'Missing/retried primary or hidden native case call')
    require([(r['index'], r['condition']) for r in routes] == expected_order, 'Counterbalanced original inventory changed')
    process_a, process_b = (recipe['processes'][c] for c in protocol['conditions'])
    same(process_a, process_b, 'A/A labels changed process identity/version/graph')
    require(process_a['version'] == 1 and process_a['graphFileSha256'] == hashlib.sha256(artifacts['graph-control.json']).hexdigest(), 'Published graph SHA/version differs')
    graph = parse_json(artifacts['graph-control.json']); require([n['id'] for n in graph['nodes']] == protocol['graphPath'], 'Different primary path')
    require([n['type'] for n in graph['nodes']] == ['start', 'agent', 'end']
        and graph['nodes'][0]['config'] == graph['nodes'][2]['config'] == {}
        and set(graph['nodes'][1]['config']) == {'agentId', 'approvalRequired'}
        and graph['nodes'][1]['config']['approvalRequired'] is False, 'A/A graph added behavior beyond primary execution')
    same(graph['edges'], [{'id': 'a', 'source': 'start', 'target': 'agent', 'branch': 'default'},
        {'id': 'b', 'source': 'agent', 'target': 'end', 'branch': 'default'}], 'Different downstream path')
    require('decisionShadow' not in graph['nodes'][1]['config'], 'A/A graph enabled shadow')
    cohort = parse_json(artifacts['cohort.http.json']); scope = cohort['scope']
    require(cohort['schemaVersion'] == 'agat.decision.shadow-cohort.v1' and cohort['snapshot']['storedCohortComplete'] is True
        and cohort['snapshot']['truncated'] is False and cohort['snapshot']['consistency'] == 'single_database_snapshot'
        and cohort['counts'] == {'instances': 2*count, 'runs': 2*count, 'storedStages': 2*count, 'storedShadowStages': 0}, 'Incomplete A/A authenticated census')
    require(scope == {'projectId': 'default', 'processId': process_a['processId'], 'processVersion': 1, 'startAt': recipe['startAt'],
        'endAt': driver['actualWindow']['endAt'], 'boundary': 'process_instance_created_at_half_open'}, 'Wrong A/A census scope')
    run_ids = {r['runId'] for r in routes}
    require(len(run_ids) == len(cohort['instances']) == len(cohort['traces']) == 2*count
        and len({r['stageId'] for r in routes}) == 2*count
        and run_ids == {i['runId'] for i in cohort['instances']} == {t['run']['id'] for t in cohort['traces']}
        and all(i['status'] == 'completed' and i['processVersion'] == 1 and i['replayOfInstanceId'] is None for i in cohort['instances']), 'Census lost/replayed/rebound actual runs')
    require(cohort['sloAccepted'] is False and cohort['routingEnabled'] is False and cohort['qualification'] == 'not_assessed', 'Census grants customer qualification')
    census_rows = journal(artifacts['cohort-http.jsonl']); require(len(census_rows) == 1, 'Missing/repeated actual census response')
    c = census_rows[0]; url = urlsplit(c['path'])
    require(c['httpStatus'] == 200 and c['unauthenticatedStatus'] == 401 and url.path == '/api/v1/processes/'+process_a['processId']+'/decision-shadow-cohort'
        and parse_qs(url.query) == {'processVersion': ['1'], 'startAt': [recipe['startAt']], 'endAt': [driver['actualWindow']['endAt']]}
        and c['bodySha256'] == hashlib.sha256(artifacts['cohort.http.json']).hexdigest(), 'Actual raw census/authentication rebound')
    seen = set(); outputs = {}; requests = {}; lengths = Counter(); prompts = []; case_rows = {}
    primary_latency = {c: [] for c in protocol['conditions']}; workflow_latency = {c: [] for c in protocol['conditions']}
    for ordinal, (route, row) in enumerate(zip(routes, rows)):
        index, condition = expected_order[ordinal]; case = context['inputs'][index]
        require(route['ordinal'] == ordinal and route['caseId'] == case['id'] and route['inputSha256'] == case['inputSha256']
            and row['index'] == index and row['condition'] == condition and route['runId'] == row['runId'] and route['runId'] not in seen, 'A/A route missing/repeated/rebound')
        seen.add(route['runId']); trace_raw = artifacts[route['traceFile']]
        require(route['traceFile'] == f'trace-{index:03d}-{condition}.http.json' and hashlib.sha256(trace_raw).hexdigest() == route['traceFileSha256'], 'Raw trace SHA differs')
        trace = parse_json(trace_raw); run = trace['run']; stage = paired.stage(trace, case)
        require(run['id'] == route['runId'] and run['status'] == 'completed' and stage['id'] == row['stageId'] == route['stageId']
            and stage['status'] == 'completed' and row['nodeId'] == stage['nodeId'], 'Wrong completed primary run/stage/worker')
        require(trace['decisionObservations'] == [] and all(trace[k]['stages'] == [] for k in
            ('decisionStageInventory', 'decisionCallerAccounting', 'decisionAssignmentHistory')), 'A/A enabled a shadow stage/intent/return')
        request = raw_body(row, 'requestBody'); native_request = raw_body(row, 'nativeRequestBody')
        native = raw_body(row, 'nativeResponseBody'); translated = raw_body(row, 'responseBody')
        require(set(request) == {'model', 'messages', 'temperature', 'stream'} and request['model'] == primary.MODEL
            and request['temperature'] == .2 and request['stream'] is False, 'Worker primary settings differ')
        same(native_request, {'model': primary.MODEL, 'messages': request['messages'], 'stream': False, 'keep_alive': '5m', **GENERATION}, 'Native primary settings/input changed')
        messages = request['messages']
        same(messages, [{'role': 'system', 'content': protocol['systemPrompt']},
            {'role': 'user', 'content': f"Задача: {protocol['processName']}\n\nВходные данные:\n{case['request']['state']}"}], 'Whole original input or condition-blind prompt changed')
        require(messages[1]['content'].count(case['request']['state']) == 1 and row['messagesSha256'] == hashlib.sha256(
            json.dumps(messages, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest(), 'Original input duplicated or message pin differs')
        primary.validate_response(native)
        require(native['prompt_eval_count']+128 < 32768 and not native['message'].get('tool_calls'), 'Primary exceeded whole-input/generation contract')
        same(translated, {'choices': [{'message': {'role': 'assistant', 'content': native['message']['content']}, 'finish_reason': native['done_reason']}],
            'usage': {'prompt_tokens': native['prompt_eval_count'], 'completion_tokens': native['eval_count']}}, 'Adapter changed actual output/usage')
        output_sha = hashlib.sha256(native['message']['content'].strip().encode()).hexdigest()
        require(output_sha == row['outputSha256'] == route['outputSha256'] == hashlib.sha256(stage['output'].encode()).hexdigest()
            and row['httpStatus'] == row['nativeHttpStatus'] == 200, 'Actual output not preserved durably')
        census_trace = next(t for t in cohort['traces'] if t['run']['id'] == run['id'])
        projection_keys = {'run', 'truncated', 'decisionCallerAccounting', 'decisionAssignmentHistory', 'decisionStageInventory', 'decisionObservations'}
        require(set(census_trace) == projection_keys, 'Authenticated census trace projection is incomplete')
        same(census_trace, {k: {'id': run['id']} if k == 'run' else trace[k] for k in projection_keys}, 'Authenticated census differs from individual trace')
        begin = timestamp(route['startedAt'], 'route.start'); end = timestamp(route['completedAt'], 'route.end')
        pb = timestamp(row['startedAt'], 'primary.start'); nb = timestamp(row['nativeStartedAt'], 'native.start'); pe = timestamp(row['completedAt'], 'primary.end')
        require(timestamp(recipe['startAt'], 'window.start') <= begin <= pb <= nb <= pe <= end <= timestamp(driver['actualWindow']['endAt'], 'window.end'), 'Primary/workflow boundaries differ')
        require(abs((pe-pb).total_seconds()*1000-number(row['elapsedMs'], 0, protocol['primaryTimeoutMs'])) <= 10
            and abs((end-begin).total_seconds()*1000-number(route['elapsedMs'], 0, protocol['instanceDeadlineMs'])) <= 10, 'Wall/monotonic primary boundaries differ')
        primary_latency[condition].append(row['elapsedMs']); workflow_latency[condition].append(route['elapsedMs']); prompts.append(native['prompt_eval_count'])
        lengths[condition] += int(native['done_reason'] == 'length'); outputs[index, condition] = output_sha; requests[index, condition] = row['nativeRequestBody']
        case_rows[index, condition] = {'primaryMs': row['elapsedMs'], 'workflowMs': route['elapsedMs'], 'outputSha256': output_sha,
            'nativeRequestBodySha256': row['nativeRequestBodySha256'], 'promptTokens': native['prompt_eval_count'], 'decodeTokens': native['eval_count'], 'doneReason': native['done_reason']}
    require(all(requests[i, 'repeatA'] == requests[i, 'repeatB'] for i in range(count)), 'A/A native request bytes differ')
    by_run = {r['runId']: r for r in routes}; completed = []
    for row in leases:
        require(row['runId'] in by_run and row['path'] == '/api/v1/leases/'+row['leaseId']+('/renew' if row['path'].endswith('/renew') else '/complete'), 'A/A wrote an intent/return/fail or changed lease path')
        route = by_run[row['runId']]; stage = paired.stage(parse_json(artifacts[route['traceFile']]), context['inputs'][route['index']])
        require(row['index'] == route['index'] and row['condition'] == route['condition'] and row['stageId'] == route['stageId']
            and row['nodeId'] == stage['nodeId'] and row['pair'] == route['index']//2, 'Actual lease/run/stage ownership changed')
        require(row['httpStatus'] == (204 if row['path'].endswith('/renew') else 200)
            and row['requestBodySha256'] == hashlib.sha256(row['requestBody'].encode()).hexdigest()
            and (row['requestBodyComplete'] is True or row['path'].endswith('/renew')), 'Failed/incomplete actual lease mutation')
        if row['path'].endswith('/complete'):
            value = parse_json(row['requestBody']); require(hashlib.sha256(value['output'].encode()).hexdigest() == route['outputSha256'], 'Completion changed primary output')
            completed.append(row['runId'])
    require(len(completed) == len(set(completed)) == 2*count and set(completed) == run_ids, 'Lost/retried durable primary completion')
    require('truncating input prompt' not in artifacts['primary.log'].decode(errors='replace').lower(), 'Ollama truncated original input')
    pairs = [{'index': i, 'caseId': context['inputs'][i]['id'], 'inputSha256': context['inputs'][i]['inputSha256'],
        'sameOutput': outputs[i, 'repeatA'] == outputs[i, 'repeatB'], **{c: case_rows[i, c] for c in protocol['conditions']},
        'workflowDeltaMs': round(case_rows[i, 'repeatB']['workflowMs']-case_rows[i, 'repeatA']['workflowMs'], 3)} for i in range(count)]
    deltas = [p['workflowDeltaMs'] for p in pairs]; differences = [p['index'] for p in pairs if not p['sameOutput']]
    return {'schemaVersion': 'agat.decision.public-primary-repeat-control-inventory.v1', 'originalCases': count,
        'originalGroups': len({c['groupId'] for c in context['inputs']}), 'actualWorkflows': 2*count, 'primaryCalls': 2*count,
        'repeatCalls': {c: count for c in protocol['conditions']}, 'nativeCaseCalls': 0, 'durableShadowReturns': 0, 'callerIntents': 0,
        'primaryOutputsPreserved': 2*count, 'matchedPrimaryRequests': count, 'matchedOutputPairs': count-len(differences),
        'differentOutputIndices': differences, 'primaryPromptTokens': {'min': min(prompts), 'max': max(prompts)},
        'primaryLengthReturns': {c: lengths[c] for c in protocol['conditions']},
        'primaryLatencyMs': {c: distribution(v) for c, v in primary_latency.items()}, 'workflowLatencyMs': {c: distribution(v) for c, v in workflow_latency.items()},
        'repeatWorkflowDeltaMs': {**distribution(deltas), 'min': min(deltas), 'mean': sum(deltas)/count}, 'pairs': pairs,
        'retryCount': 0, 'classificationAccuracyMeasured': False, 'referenceLabels': 0, 'sloAccepted': False,
        'routingEnabled': False, 'qualification': 'not_assessed', **verify_batches(context, recipe, driver, artifacts, routes, rows)}
