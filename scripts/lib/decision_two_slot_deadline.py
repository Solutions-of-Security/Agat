"""Published worker deadline with a still-running primary peer; model-free receipt replay."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import sys
from urllib.parse import urlparse

from decision_runtime.artifacts import verify_seal
from decision_runtime.contracts import Request, fields, number, parse_json
from decision_runtime.metrics import outcome
from scripts.lib import decision_two_slot_cancellation as common
from scripts.lib import decision_public_workflow_active_cancellation as active
from scripts.lib import decision_public_workflow_active_deadline as established
from scripts.lib import decision_public_workflow_cancellation as cancellation
from scripts.lib.decision_performance import validate_result
from scripts.lib.decision_public_load import validate_context
from scripts.lib.decision_shadow_pilot import require, timestamp
from scripts.lib.decision_shadow_sli import same_json

PLAN_SCHEMA = 'agat.decision.two-slot-deadline-plan.v1'
RESULT_SCHEMA = 'agat.decision.two-slot-deadline-result.v1'
VERIFICATION_SCHEMA = 'agat.decision.two-slot-deadline-verification.v1'
PROCESS_NAME = 'Two worker slots native deadline isolation'
DRIVER_PATH = 'scripts/run-two-slot-native-deadline.mts'
PREPARED_FILE = 'native-target-prepared.json'
READY_FILE = 'active-native-ready.json'
DRAINED_FILE = 'active-native-drained.json'
SOURCE_PATHS = [*common.SOURCE_PATHS, 'scripts/run-two-slot-native-deadline.py', DRIVER_PATH,
    'scripts/verify-two-slot-native-deadline.py', 'scripts/test/test_decision_two_slot_deadline.py']
ARTIFACTS = common.ARTIFACTS-{'coordinator-cancellation-prepared.json', 'coordinator-cancellation-ready.json',
    'coordinator-cancellation-drained.json', 'coordinator-cancellation-applied.json', 'trace-peer-after-cancel.http.json'} | {
    PREPARED_FILE, READY_FILE, DRAINED_FILE, 'trace-peer-after-deadline.http.json', 'workflow-target-graph.json', 'cohort-target.http.json', 'cohort-http.jsonl'}
AUTHORITY = common.AUTHORITY
projection = common.projection
shared_config = common.shared_config
journal = common.journal
raw_body = common.raw_body
verify_physical = common.verify_physical
recovery = common.recovery


def native_fault(projected, index):
    base = active.active_spec(projected, index)
    return {**{k: v for k, v in base.items() if k not in ('sampleToCancelMaxMs', 'cancelToEofDeadlineMs')},
        'kind': 'local_caller_deadline_during_active_native_http_handler', 'trigger': 'worker_monotonic_deadline_from_published_shadow_config',
        'callerTimeoutMs': 250, 'healthyCallerTimeoutMs': 10000, 'deadlineOverrunMaxMs': 250,
        'relayCallerTimingDifferenceMaxMs': 50, 'acceptedDurableReturn': 'unavailable/timeout', 'coordinatorRunCancelled': False}


def protocol(context, index):
    value = common.protocol(context, index)
    return {**value, 'kind': 'worker_deadline_while_peer_primary_in_flight', 'processName': PROCESS_NAME,
        'startOrder': [0, 2, 1, 3], 'caseVersions': [1, 2, 1, 1], 'targetCallerTimeoutMs': 250, 'healthyCallerTimeoutMs': 10000,
        'nativeFault': native_fault(projection(context, index), 1)}


def agent_stage(trace, case): return common.agent_stage(trace, case, PROCESS_NAME)


def make_proxy(port, projected, fault):
    from scripts.lib.decision_public_workflow_active_integration import PreparedActiveCancellationProxy
    return PreparedActiveCancellationProxy(port, projected, fault, spec_factory=native_fault)


def validate_preparation(projected, prepared, artifacts, *, process_name=PROCESS_NAME):
    fields(prepared, {'targetIndex', 'caseId', 'stageId', 'runId', 'inputSha256', 'profileSha256', 'peerRunId', 'peerStageId',
        'workerNodeId', 'beforeTraceFileSha256', 'peerTraceFileSha256', 'peerHeldFileSha256', 'preparedAt'})
    require(type(prepared['targetIndex']) is int and prepared['targetIndex'] == 1 and prepared['caseId'] == projected['inputs'][1]['id']
        and prepared['inputSha256'] == projected['inputs'][1]['inputSha256'] and prepared['profileSha256'] == projected['profileSha256'], 'Deadline preparation rebound')
    for name, key in (('trace-target-before.http.json', 'beforeTraceFileSha256'), ('trace-peer-before.http.json', 'peerTraceFileSha256'), ('peer-primary-held.json', 'peerHeldFileSha256')):
        require(isinstance(artifacts[name], bytes) and 0 < len(artifacts[name]) <= 16*1024*1024
            and hashlib.sha256(artifacts[name]).hexdigest() == prepared[key], 'Prepared deadline raw snapshot changed')
    target = parse_json(artifacts['trace-target-before.http.json']); peer = parse_json(artifacts['trace-peer-before.http.json'])
    t = common.agent_stage(target, projected['inputs'][1], process_name); p = common.agent_stage(peer, projected['inputs'][2], process_name)
    require(target['run']['id'] == prepared['runId'] and peer['run']['id'] == prepared['peerRunId']
        and target['run']['status'] == peer['run']['status'] == 'running' and t['id'] == prepared['stageId'] and p['id'] == prepared['peerStageId']
        and t['nodeId'] == p['nodeId'] == t['worker']['nodeId'] == p['worker']['nodeId'] == prepared['workerNodeId'] and bool(t['nodeId'])
        and t['status'] in ('assigned', 'running') and p['status'] in ('assigned', 'running') and t['output'] is p['output'] is None
        and target['decisionObservations'] == peer['decisionObservations'] == [], 'Two actual in-flight worker slots missing')
    a = cancellation._single_assignment(target, t['id']); b = cancellation._single_assignment(peer, p['id'])
    require(a['assignmentId'] != b['assignmentId'] and a['negotiated'] is True and a['intent'] is True and a['returned'] is None and a['outcome'] == 'intent_pending'
        and b['negotiated'] is True and b['intent'] is False and b['returned'] is None, 'Deadline and held peer assignments changed')
    held = parse_json(artifacts['peer-primary-held.json'])
    require(held['runId'] == peer['run']['id'] and held['index'] == 2 and held['responseBytesWritten'] == 0
        and timestamp(held['heldAt'], 'heldAt') <= timestamp(prepared['preparedAt'], 'preparedAt'), 'Peer was not held before deadline')
    return prepared


def verify_actor(context, spec, recipe, driver, artifacts, *, suite=None):
    create_protocol = protocol if suite is None else suite.protocol
    project = projection if suite is None else suite.projection
    stage_for = lambda trace, case: common.agent_stage(trace, case, spec['processName'])
    require(same_json(spec, create_protocol(context, spec['selectedOriginalIndices'][1])) and same_json(recipe['protocol'], spec), 'Posthoc deadline protocol')
    cases = project(context, spec['selectedOriginalIndices'][1])['inputs']
    require(driver['status'] == 'observed' and driver['failure'] is None and driver['workerExitCode'] == 0 and driver['primaryCalls'] == 4
        and driver['workerConcurrency'] == driver['globalMaxConcurrency'] == 2 and driver['schedulerMode'] == 'sequential'
        and driver['authenticatedStatus'] == 200 and driver['unauthenticatedStatus'] == 401, 'Actual two-slot deadline driver failed')
    routes = journal(artifacts['workflow-routes.jsonl']); primary = journal(artifacts['primary-http.jsonl'])
    require(routes == driver['routes'] and len(routes) == len(primary) == 4 and [r['index'] for r in routes] == list(range(4)), 'Missing/retried deadline workflow or primary')
    healthy_graph = parse_json(artifacts['workflow-graph.json']); target_graph = parse_json(artifacts['workflow-target-graph.json'])
    require(recipe['caseVersions'] == [1, 2, 1, 1] and hashlib.sha256(artifacts['workflow-graph.json']).hexdigest() == recipe['graphFileSha256']
        and hashlib.sha256(artifacts['workflow-target-graph.json']).hexdigest() == recipe['targetGraphFileSha256'], 'Deadline versions/graphs rebound')
    normalized = shared_config(context); normalized['options'] = [{**o, 'abstain': o.get('abstain', False)} for o in normalized['options']]
    for graph, budget in ((healthy_graph, 10000), (target_graph, 250)):
        require([n['id'] for n in graph['nodes']] == ['start', 'agent', 'end'] and [n['type'] for n in graph['nodes']] == ['start', 'agent', 'end'], 'Deadline graph introduced another input path')
        c = graph['nodes'][1]['config']['decisionShadow']
        require(c['mode'] == 'shadow' and type(c['timeoutMs']) is int and c['timeoutMs'] == budget
            and same_json({k: c[k] for k in normalized}, normalized) and same_json(parse_json(c['profileJson']), context['profile']), 'Published caller deadline/profile/question changed')
    target_graph['nodes'][1]['config']['decisionShadow']['timeoutMs'] = 10000
    require(same_json(healthy_graph, target_graph) and healthy_graph['edges'] == [{'id': 'a', 'source': 'start', 'target': 'agent', 'branch': 'default'},
        {'id': 'b', 'source': 'agent', 'target': 'end', 'branch': 'default'}], 'Matched direct graphs differ beyond published deadline')
    full_names = ('trace-prefix.http.json', 'trace-target.http.json', 'trace-peer.http.json', 'trace-suffix.http.json')
    traces = {parse_json(artifacts[name])['run']['id']: parse_json(artifacts[name]) for name in full_names}; metadata = {}; instances = {}
    start = timestamp(recipe['startAt'], 'startAt'); end = timestamp(driver['endAt'], 'endAt')
    require(start < end and (end-start).total_seconds()*1000 <= spec['driverDeadlineMs'], 'Deadline creation window changed')
    census_http = journal(artifacts['cohort-http.jsonl'])
    require(len(census_http) == 2 and [r['version'] for r in census_http] == [1, 2], 'Missing/repeated versioned authenticated census')
    for version, filename, indices in ((1, 'cohort.http.json', (0, 2, 3)), (2, 'cohort-target.http.json', (1,))):
        cohort = parse_json(artifacts[filename]); scope = cohort['scope']; expected = {routes[i]['runId'] for i in indices}
        require(cohort['schemaVersion'] == 'agat.decision.shadow-cohort.v1' and cohort['snapshot']['storedCohortComplete'] is True and cohort['snapshot']['truncated'] is False
            and cohort['snapshot']['consistency'] == 'single_database_snapshot' and cohort['counts']['instances'] == cohort['counts']['runs'] == cohort['counts']['storedShadowStages'] == len(indices)
            and expected == {t['run']['id'] for t in cohort['traces']} == {i['runId'] for i in cohort['instances']}, 'Versioned deadline census missing actual workflow')
        require(scope['processId'] == recipe['processId'] and scope['processVersion'] == version and scope['projectId'] == 'default'
            and scope['startAt'] == recipe['startAt'] and scope['endAt'] == driver['endAt'] and scope['boundary'] == 'process_instance_created_at_half_open'
            and end <= timestamp(cohort['observedAt'], 'observedAt') and cohort['sloAccepted'] is False and cohort['routingEnabled'] is False and cohort['qualification'] == 'not_assessed', 'Wrong actual version census scope/authority')
        census_row = fields(census_http[version-1], {'version', 'path', 'httpStatus', 'unauthenticatedStatus', 'bodySha256', 'capturedAt'})
        endpoint = urlparse(census_row['path'])
        from urllib.parse import parse_qs
        require(endpoint.path == '/api/v1/processes/'+recipe['processId']+'/decision-shadow-cohort'
            and parse_qs(endpoint.query) == {'processVersion': [str(version)], 'startAt': [recipe['startAt']], 'endAt': [driver['endAt']]}
            and census_row['httpStatus'] == 200 and census_row['unauthenticatedStatus'] == 401
            and census_row['bodySha256'] == hashlib.sha256(artifacts[filename]).hexdigest()
            and timestamp(cohort['observedAt'], 'censusAt') <= timestamp(census_row['capturedAt'], 'capturedAt'), 'Actual census response/authentication rebound')
        metadata.update({t['run']['id']: t for t in cohort['traces']}); instances.update({i['runId']: i for i in cohort['instances']})
    require(set(traces) == set(metadata) == set(instances) == {r['runId'] for r in routes} and len(traces) == 4, 'Full deadline cohort denominator changed')
    trace_indices = {full_names[i]: i for i in range(4)}
    trace_indices.update({'trace-target-before.http.json': 1, 'trace-peer-before.http.json': 2, 'trace-peer-after-deadline.http.json': 2, 'trace-peer-before-release.http.json': 2})
    get_rows = journal(artifacts['trace-http.jsonl']); require(len(get_rows) == 8 and {r['file'] for r in get_rows} == set(trace_indices), 'Missing/retried actual trace GET')
    for row in get_rows:
        index = trace_indices[row['file']]
        require(row['index'] == index and row['path'] == '/api/v1/runs/'+routes[index]['runId']+'/trace' and row['httpStatus'] == 200
            and row['bodySha256'] == hashlib.sha256(artifacts[row['file']]).hexdigest()
            and timestamp(instances[routes[index]['runId']]['createdAt'], 'createdAt') <= timestamp(row['capturedAt'], 'capturedAt'), 'Authenticated raw trace rebound')
    primary_outputs = {}
    for index, (route, case) in enumerate(zip(routes, cases)):
        trace = traces[route['runId']]; stage = stage_for(trace, case); instance = instances[route['runId']]
        matches = [p for p in primary if p['index'] == index]; require(len(matches) == 1 and matches[0]['runId'] == route['runId'], 'Primary request repeated/rebound')
        output = 'PRIMARY_OUTPUT' if suite is None else suite.verify_primary_response(matches[0], case, spec)
        primary_outputs[route['runId']] = output
        require(route['originalIndex'] == spec['selectedOriginalIndices'][index] and route['caseId'] == case['id'] and route['inputSha256'] == case['inputSha256']
            and route['stageId'] == stage['id'] and trace['run']['status'] == route['runStatus'] == instance['status'] == 'completed'
            and stage['output'] == output and stage['status'] == 'completed' and instance['instanceId'] == route['instanceId']
            and instance['processVersion'] == spec['caseVersions'][index] and instance['replayOfInstanceId'] is None and start <= timestamp(instance['createdAt'], 'createdAt') < end, 'Primary lost, replayed or wrong published version')
        for key in ('decisionObservations', 'decisionCallerAccounting', 'decisionAssignmentHistory', 'decisionStageInventory'):
            value = trace[key]
            if key == 'decisionObservations':
                require(len(value) == 1 and same_json(value[0]['context'], normalized)
                    and value[0]['callerTimeoutMs'] == (250 if index == 1 else 10000)
                    and value[0]['stageId'] == stage['id'] and value[0]['inputSha256'] == case['inputSha256']
                    and value[0]['profileSha256'] == context['profileSha256'], 'Deadline caller observation, original input or published timeout missing')
                value = [{k: v for k, v in o.items() if k != 'context'} for o in value]
            require(same_json(value, metadata[route['runId']][key]), 'Full deadline trace and metadata differ')
        assignment = cancellation._single_assignment(trace, stage['id'])
        require(assignment['negotiated'] is True and assignment['intent'] is True and assignment['outcome'] == 'returned'
            and assignment['returned'] is not None and stage['worker']['nodeId'] == stage['nodeId'], 'Known deadline caller return or assigned worker lost')
        if suite is None:
            request = raw_body(matches[0], 'requestBody'); response = raw_body(matches[0], 'responseBody')
            require(request['model'] == 'fixture-primary' and request['temperature'] == .2 and request['stream'] is False and request['messages'] == [
                {'role': 'system', 'content': spec['systemPrompt']}, {'role': 'user', 'content': 'Задача: '+spec['processName']+'\n\nВходные данные:\n'+case['request']['state']}]
                and matches[0]['httpStatus'] == 200 and response['choices'][0]['message']['content'] == 'PRIMARY_OUTPUT', 'Full fixture primary input/output changed')
        require(timestamp(instance['createdAt'], 'createdAt') <= timestamp(matches[0]['startedAt'], 'primaryStartedAt')
            <= timestamp(matches[0]['completedAt'], 'primaryCompletedAt') <= timestamp(stage['completedAt'], 'stageCompletedAt'), 'Full primary input once or original response changed')
    created_order = [timestamp(instances[routes[i]['runId']]['createdAt'], 'createdAt') for i in spec['startOrder']]
    require(created_order == sorted(created_order), 'Actual workflow creation order differs from prospective protocol')
    prepared = validate_preparation(project(context, spec['selectedOriginalIndices'][1]), parse_json(artifacts[PREPARED_FILE]), artifacts, process_name=spec['processName'])
    peer_before = parse_json(artifacts['trace-peer-before.http.json']); peer_stage = stage_for(peer_before, cases[2])
    target_before = parse_json(artifacts['trace-target-before.http.json']); target_stage = stage_for(target_before, cases[1])
    for index, before_stage, before_trace in ((1, target_stage, target_before), (2, peer_stage, peer_before)):
        final_stage = stage_for(traces[routes[index]['runId']], cases[index])
        require(final_stage['nodeId'] == before_stage['nodeId'] and final_stage['worker'] == before_stage['worker']
            and cancellation._single_assignment(before_trace, before_stage['id'])['assignmentId']
            == cancellation._single_assignment(traces[routes[index]['runId']], final_stage['id'])['assignmentId'], 'Deadline restarted or reassigned an existing lease')
    for name in ('trace-peer-before.http.json', 'trace-peer-after-deadline.http.json', 'trace-peer-before-release.http.json'):
        trace = parse_json(artifacts[name]); stage = stage_for(trace, cases[2]); assignment = cancellation._single_assignment(trace, stage['id'])
        require(trace['run']['id'] == routes[2]['runId'] and trace['run']['status'] == 'running' and stage['id'] == routes[2]['stageId'] and stage['nodeId'] == peer_stage['nodeId']
            and stage['worker'] == peer_stage['worker'] and stage['status'] in ('assigned', 'running') and stage['output'] is None
            and trace['decisionObservations'] == [] and assignment['negotiated'] is True and assignment['intent'] is False and assignment['returned'] is None
            and assignment['assignmentId'] == cancellation._single_assignment(traces[routes[2]['runId']], routes[2]['stageId'])['assignmentId'], 'Deadline revoked/reassigned or completed peer primary early')
    held = parse_json(artifacts['peer-primary-held.json']); released = parse_json(artifacts['peer-primary-released.json'])
    require(held['runId'] == released['runId'] == routes[2]['runId'] and held['index'] == released['index'] == 2
        and held['responseBytesWritten'] == released['responseBytesWrittenBeforeRelease'] == 0
        and released['socketClosedBeforeRelease'] is False and released['socketOpenAtRelease'] is True and held['requestBodySha256'] == next(p for p in primary if p['index'] == 2)['requestBodySha256']
        and released['heldFileSha256'] == hashlib.sha256(artifacts['peer-primary-held.json']).hexdigest() and released['recoveredFileSha256'] == hashlib.sha256(artifacts['native-recovered.json']).hexdigest(), 'Peer primary connection did not survive deadline/recovery')
    require(0 <= (timestamp(released['releasedAt'], 'releasedAt')-timestamp(held['heldAt'], 'heldAt')).total_seconds()*1000 <= spec['peerHoldDeadlineMs'], 'Peer hold budget exceeded')
    transport = verify_seal(parse_json(artifacts['active-transport.json']), active.TRANSPORT_SCHEMA)
    require(same_json(transport['spec'], spec['nativeFault']) and transport['closed'] is True and transport['errors'] == [] and transport['activeHandlers'] == 0
        and transport['acceptedPosts'] == 4 and transport['completedUpstreamPosts'] == 3 and transport['interruptedActiveUpstreamPosts'] == 1
        and len(transport['rows']) == 4 and [r['index'] for r in transport['rows']] == list(range(4)), 'Missing/retried native deadline attempt')
    ready = verify_seal(parse_json(artifacts[READY_FILE]), active.READY_SCHEMA); drained = verify_seal(parse_json(artifacts[DRAINED_FILE]), active.DRAIN_SCHEMA)
    wire = transport['rows'][1]; require(same_json(ready, active.ready_receipt(wire, {'capturedAt': ready['activeObservedAt'], 'metricsRaw': ready['activeMetricsRaw']}))
        and same_json(drained, active.drain_receipt(wire)), 'Deadline active/EOF barrier rebound')
    require(timestamp(wire['acceptedAt'], 'acceptedAt') <= timestamp(wire['upstreamRequestSentAt'], 'sentAt')
        <= timestamp(ready['activeObservedAt'], 'activeAt') < timestamp(wire['clientEofObservedAt'], 'eofAt')
        <= timestamp(wire['upstreamShutdownAt'], 'shutdownAt') <= timestamp(wire['finishedAt'], 'finishedAt')
        and timestamp(prepared['preparedAt'], 'preparedAt') <= timestamp(ready['activeObservedAt'], 'activeAt'), 'Worker deadline closed before active native witness')
    http = journal(artifacts['coordinator-http.jsonl']); posted = established.verify_http(project(context, spec['selectedOriginalIndices'][1]), spec['nativeFault'], {'traces': list(metadata.values())}, routes, {'http': http}, primary_outputs=None if suite is None else primary_outputs)
    local = parse_json(posted['requestBody']); timing = fields(local['callerTiming'], {'schemaVersion', 'clock', 'boundary', 'durationMs'})
    require(timing['schemaVersion'] == 'agat.decision.caller-timing.v1' and timing['clock'] == 'monotonic' and timing['boundary'] == 'local_http_call', 'Actual monotonic worker deadline missing')
    caller_ms = number(timing['durationMs'], 250, 500)
    require(200 <= wire['elapsedMs'] <= 500 and abs(caller_ms-wire['elapsedMs']) <= 50
        and timestamp(wire['clientEofObservedAt'], 'eofAt') <= timestamp(posted['startedAt'], 'returnAt'), 'Local deadline omitted socket work or durable return preceded EOF')
    healthy = Counter(); last = timestamp(transport['startedAt'], 'relayAt')
    for index, row in enumerate(transport['rows']):
        require(row['index'] == index and raw_body(row, 'requestBody') == Request.from_dict({**cases[index]['request'], 'id': routes[index]['stageId']}).to_dict()
            and row['caseId'] == cases[index]['id'] and row['stageId'] == routes[index]['stageId'] and row['inputSha256'] == cases[index]['inputSha256']
            and row['profileSha256'] == context['profileSha256'] and row['cancelOnDisconnect'] is True
            and last <= timestamp(row['acceptedAt'], 'acceptedAt') <= timestamp(row['finishedAt'], 'finishedAt'), 'Original native request/order changed')
        last = timestamp(row['finishedAt'], 'finishedAt')
        if index == 1:
            require(row['upstreamResponseBytesObserved'] == row['responseBytesWritten'] == 0 and row['upstreamCompletedNormally'] is False
                and row['clientEofObserved'] is True and row['upstreamShutdownApplied'] is True and row['downstreamWriteCompleted'] is False, 'Timed-out target has a fabricated native result')
            continue
        result = raw_body(row, 'responseBody'); validate_result(result, Request.from_dict({**cases[index]['request'], 'id': routes[index]['stageId']}), context['profile'])
        token_result = (result.get('inputTokens') == cases[index]['inputTokens'] and result.get('generatedTokens') == 0) if cases[index]['contextEligible'] else (
            result['status'] == 'error' and result['reason'] == 'context_too_long')
        require(token_result and row['upstreamStatus'] == (200 if cases[index]['contextEligible'] else 422)
            and row['downstreamWriteCompleted'] is True and same_json(result, traces[routes[index]['runId']]['decisionObservations'][0]['observation']['result']), 'Unaffected typed result changed')
        healthy[outcome(result)] += 1
        if index >= 2: require(timestamp(released['releasedAt'], 'releasedAt') <= timestamp(row['acceptedAt'], 'acceptedAt'), 'Peer native work started before recovery release')
    retired = verify_seal(parse_json(artifacts['native-retired.json']), active.RETIREMENT_SCHEMA)
    require(same_json(retired, active.retired_receipt(spec['nativeFault'], ready, drained, runtime_pid=retired['runtimePid'], native_pids=retired['nativePids'],
        exit_code=75, remaining=[], log_raw=artifacts['runtime.log'], observed_at=retired['observedExitedAt'])), 'Native deadline retirement changed')
    recovered = verify_seal(parse_json(artifacts['native-recovered.json']), active.RECOVERED_SCHEMA)
    require(same_json(recovered, active.recovered_receipt(spec['nativeFault'], retired, runtime_pid=recovered['runtimePid'], profile_sha=context['profileSha256'],
        server_start=float(recovered['readyServerStartText']), warmup_file_sha=hashlib.sha256(artifacts['recovery-warmup.json']).hexdigest(), applied_at=recovered['appliedAt'])), 'Native deadline recovery changed')
    eof = timestamp(wire['clientEofObservedAt'], 'eofAt'); retirement = timestamp(retired['observedExitedAt'], 'retiredAt'); recovery = timestamp(recovered['appliedAt'], 'recoveredAt')
    require(0 <= (retirement-eof).total_seconds()*1000 <= 10000 and 0 <= (recovery-retirement).total_seconds()*1000 <= 90000
        and timestamp(held['heldAt'], 'heldAt') <= timestamp(ready['activeObservedAt'], 'activeAt') <= eof <= recovery <= timestamp(released['releasedAt'], 'releasedAt'), 'Peer hold or native recovery lifecycle reordered')
    captured = {r['file']: timestamp(r['capturedAt'], 'capturedAt') for r in get_rows}
    require(timestamp(posted['finishedAt'], 'targetReturnAt') <= captured['trace-peer-after-deadline.http.json']
        and recovery <= captured['trace-peer-before-release.http.json'] <= timestamp(released['releasedAt'], 'releasedAt')
        <= timestamp(next(p for p in primary if p['index'] == 2)['completedAt'], 'peerPrimaryCompletedAt')
        and recovery <= timestamp(instances[routes[3]['runId']]['createdAt'], 'suffixCreatedAt'), 'Peer snapshots or primary completion bypassed deadline/recovery')
    return {'selectedOriginalIndices': spec['selectedOriginalIndices'], 'actualWorkflows': 4, 'completedWorkflows': 4, 'cancelledWorkflows': 0,
        ('primaryFixtureCalls' if suite is None else 'primaryRealCalls'): 4, 'durablePrimaryOutputs': 4, 'knownCallerReturns': 4, 'unknownCallerReturns': 0, 'unavailableTimeoutReturns': 1,
        'healthyNativeOutcomes': dict(healthy), 'localDeadlineCallerMs': caller_ms, 'targetTerminalCounterUnknown': True,
        'sameWorkerTwoAssignedStages': True, 'peerPrimaryConnectionPreserved': True, 'peerLeasePreserved': True, 'workerConcurrency': 2,
        'retryCount': 0, 'incompleteRenewalRequestBodies': sum(r['requestBodyComplete'] is False for r in http), **AUTHORITY}


def inventory(context, plan, result, artifacts, *, suite=None):
    validate_context(context); verify_seal(plan, PLAN_SCHEMA if suite is None else suite.PLAN_SCHEMA); verify_seal(result, RESULT_SCHEMA if suite is None else suite.RESULT_SCHEMA)
    fields(plan, {'schemaVersion', 'sha256', 'createdAt', 'sourceCommit', 'sourceFiles', 'contextProfileFileSha256', 'context',
        'config', 'runtime', 'profileFileSha256', 'manifestFileSha256', 'protocol', *AUTHORITY} | (set() if suite is None else {'primary'}))
    fields(result, {'schemaVersion', 'sha256', 'status', 'planSha256', 'evidence', 'physical', 'warmup', 'samples', 'failure',
        'ownedPids', 'remainingOwnedPids', 'cleanupErrors', 'runtimeExitCodes', 'driverExitCode', 'artifactSha256', 'elapsedMs', *AUTHORITY} | (set() if suite is None else {'primaryExitCode'}))
    require(set(artifacts) == set(result['artifactSha256']) == (ARTIFACTS if suite is None else suite.ARTIFACTS) and result['status'] == 'observed' and result['failure'] is None
        and result['remainingOwnedPids'] == result['cleanupErrors'] == [] and result['runtimeExitCodes'] == [75, 130] and result['driverExitCode'] == 0
        and result['planSha256'] == plan['sha256'] and same_json(plan['context'], context) and same_json(plan['config'], shared_config(context))
        and same_json(plan['runtime'], context['tokenizerEnvironment']) and plan['manifestFileSha256'] == context['manifestFileSha256']
        and plan['profileFileSha256'] == context['profileFileSha256'], 'Deadline run/context/source or known cleanup failed')
    for value in (plan, result): require(all(type(value[k]) is type(v) and value[k] == v for k, v in AUTHORITY.items()), 'Deadline invented authority/qualification')
    for name, raw in artifacts.items():
        require(isinstance(raw, bytes) and (len(raw) > 0 or name.endswith('.log')) and len(raw) <= 32*1024*1024
            and hashlib.sha256(raw).hexdigest() == result['artifactSha256'][name], 'Deadline raw artifact changed')
    owned = result['ownedPids']; require(isinstance(owned, list) and owned == sorted(set(owned)) and owned
        and all(type(p) is int and 0 < p < 2**31 for p in owned), 'Invalid deadline owned PID inventory')
    ledger = journal(artifacts['owned-pids.jsonl']); previous = set()
    for row in ledger:
        fields(row, {'recordedAt', 'ownedPids'}); timestamp(row['recordedAt'], 'recordedAt')
        require(row['ownedPids'] == sorted(set(row['ownedPids'])) and previous <= set(row['ownedPids']) <= set(owned), 'Deadline PID ledger lost/added foreign process')
        previous = set(row['ownedPids'])
    require(previous == set(owned), 'Final deadline PID ledger missing'); number(result['elapsedMs'], 0, 260000)
    recipe = parse_json(artifacts['workflow-plan.json']); driver = parse_json(artifacts['workflow-driver.json']); transport = parse_json(artifacts['active-transport.json'])
    require(len(driver['ownedPids']) == len(set(driver['ownedPids'])) == 2 and set(driver['ownedPids']) <= set(owned), 'Actual deadline worker/driver unowned')
    fields(recipe['endpoints'], {'primaryUrl', 'coordinatorUrl', 'decisionUrl'} | (set() if suite is None else {'primaryNativeUrl'}))
    ports = [transport['proxyPort'], transport['upstreamPort']]
    for name, value in recipe['endpoints'].items():
        url = urlparse(value); require(url.scheme == 'http' and url.hostname == '127.0.0.1' and url.path in ('', '/') and not url.username and not url.password and not url.query and not url.fragment, 'Deadline non-owned endpoint')
        if name == 'decisionUrl': require(url.port == transport['proxyPort'], 'Deadline relay rebound')
        else: ports.append(url.port)
    require(len(ports) == len(set(ports)) == (4 if suite is None else 5) and all(type(p) is int and 0 < p < 65536 and p not in (8766, 9095, 11434) for p in ports), 'Deadline addresses protected/shared port')
    evidence = verify_actor(context, plan['protocol'], recipe, driver, artifacts) if suite is None else suite.verify_actor(context, plan['protocol'], recipe, driver, artifacts)
    require(same_json(evidence, result['evidence']), 'Reported deadline evidence differs')
    ready = parse_json(artifacts[READY_FILE]); retired = parse_json(artifacts['native-retired.json']); recovered = parse_json(artifacts['native-recovered.json'])
    require(set(retired['nativePids']) <= set(owned) and recovered['runtimePid'] in owned and set(driver['ownedPids']) <= set(owned)
        and recovered['runtimePid'] not in driver['ownedPids'] and not set(retired['nativePids']) & set(driver['ownedPids']), 'Deadline native ownership crossed worker')
    physical = verify_physical((projection if suite is None else suite.projection)(context, plan['protocol']['selectedOriginalIndices'][1]), result, transport, ready, retired, recovered, artifacts)
    require(physical['knownCompletedPhysicalCalls'] == 7 and same_json(physical, result['physical']), 'Deadline physical accounting changed')
    require(timestamp(context['createdAt'], 'contextAt') <= timestamp(plan['createdAt'], 'planAt') < datetime.fromtimestamp(result['samples'][0]['serverStart'], timezone.utc)
        <= timestamp(recipe['startAt'], 'startAt'), 'Deadline plan not fixed before origin/scoring')
    prefix = parse_json(artifacts['native-prefix-ready.json']); armed = parse_json(artifacts['native-prefix-armed.json'])
    require(armed['prefixFileSha256'] == hashlib.sha256(artifacts['native-prefix-ready.json']).hexdigest()
        and prefix['traceFileSha256'] == hashlib.sha256(artifacts['trace-prefix.http.json']).hexdigest()
        and prefix['runId'] == driver['routes'][0]['runId'] and prefix['stageId'] == driver['routes'][0]['stageId']
        and armed['runtimePid'] == retired['runtimePid'] and armed['nativePids'] == retired['nativePids']
        and armed['serverStartText'] == retired['retiredServerStartText'] and timestamp(prefix['requestedAt'], 'prefixAt')
        <= timestamp(result['samples'][2]['capturedAt'], 'sampleAt') <= timestamp(armed['armedAt'], 'armedAt')
        <= timestamp(instances_at(artifacts, driver['routes'][2]['runId']), 'peerCreatedAt')
        <= timestamp(transport['rows'][1]['acceptedAt'], 'targetAt'), 'Native deadline prefix accounting crossed active target boundary')
    if suite is not None: suite.verify_primary_inventory(plan, result, recipe, artifacts)
    return {'evidence': evidence, 'physical': physical, 'reportedCleanupComplete': True, 'liveCleanupVerified': False,
        'gpuKernelPreemptionEstablished': False, 'modelCallsDuringVerification': 0, **AUTHORITY}


def verify(root, directory, context_path, **kwargs): return common.verify(root, directory, context_path, suite=sys.modules[__name__], **kwargs)


def instances_at(artifacts, run_id):
    return next(i['createdAt'] for name in ('cohort.http.json', 'cohort-target.http.json')
        for i in parse_json(artifacts[name])['instances'] if i['runId'] == run_id)
