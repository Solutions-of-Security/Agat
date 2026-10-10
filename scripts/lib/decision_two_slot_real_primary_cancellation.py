"""Active native cancellation with an independently pending real-primary peer."""
import hashlib
from pathlib import Path
import sys

from decision_runtime.contracts import fields, number, parse_json
from scripts.lib import decision_two_slot_cancellation as common
from scripts.lib import decision_public_paired_real_primary as real
from scripts.lib import decision_public_workflow_active_cancellation as active
from scripts.lib.decision_public_load import validate_context
from scripts.lib.decision_shadow_pilot import require, timestamp
from scripts.lib.decision_shadow_sli import same_json

PLAN_SCHEMA = 'agat.decision.two-slot-real-primary-cancellation-plan.v2'
RESULT_SCHEMA = 'agat.decision.two-slot-real-primary-cancellation-result.v2'
VERIFICATION_SCHEMA = 'agat.decision.two-slot-real-primary-cancellation-verification.v2'
REAL_PRIMARY = True
DRIVER_PATH = 'scripts/run-two-slot-real-primary-cancellation.mts'
SOURCE_PATHS = [*common.SOURCE_PATHS, *real.primary.PRIMARY_SOURCES,
    'scripts/run-two-slot-real-primary-cancellation.py', DRIVER_PATH,
    'scripts/verify-two-slot-real-primary-cancellation.py', 'scripts/test/test_decision_two_slot_real_primary_cancellation.py']
ARTIFACTS = common.ARTIFACTS | {'primary.log', 'primary-before.json', 'primary-after.json', 'primary-warmup.json'}
AUTHORITY = common.AUTHORITY
PREPARED_FILE, READY_FILE, DRAINED_FILE = common.PREPARED_FILE, common.READY_FILE, common.DRAINED_FILE
GENERATION, WARMUP_REQUEST = real.GENERATION, real.WARMUP_REQUEST
SETTINGS = {**real.SETTINGS, 'OLLAMA_NUM_PARALLEL': '1'}
class OwnedPrimary(real.OwnedPrimary):
    settings = SETTINGS

def prepare_primary(root, binaries, models):
    value = real.prepare(root, binaries, models); value['settings'] = dict(SETTINGS); return value
journal, raw_body, shared_config = common.journal, common.raw_body, common.shared_config
make_proxy, recovery, verify_physical = common.make_proxy, common.recovery, common.verify_physical


def selected(context, target):
    validate_context(context)
    require(type(target) is int and 0 < target < len(context['inputs'])-2, 'Target needs an original prefix/suffix')
    peer = max(range(len(context['inputs'])), key=lambda i: context['inputs'][i]['inputTokens'])
    indices = [target-1, target, peer, target+2]
    require(len(set(indices)) == 4 and all(context['inputs'][i]['contextEligible'] for i in (target-1, target, target+2))
        and context['inputs'][peer]['contextEligible'] is False, 'Keep eligible target/prefix/suffix and whole longest original peer')
    return indices


def projection(context, target):
    return {'inputs': [context['inputs'][i] for i in selected(context, target)], 'profile': context['profile'], 'profileSha256': context['profileSha256']}


def protocol(context, target):
    view = projection(context, target)
    return {'kind': 'cancel_active_native_task_while_peer_primary_in_flight', 'selectedOriginalIndices': selected(context, target),
        'localTargetIndex': 1, 'localPeerIndex': 2, 'workerConcurrency': 2, 'schedulerMode': 'sequential', 'globalMaxConcurrency': 2,
        'retryCount': 0, 'primary': 'pinned_qwen3_8b_actual_chat', 'peerSelection': 'largest_original_decision_token_count',
        'peerHoldDeadlineMs': 180000, 'primaryTimeoutMs': 180000, 'driverDeadlineMs': 180000,
        'primaryNumParallel': 1, 'primaryContextLength': 32768, 'primaryDecodeLimit': 128,
        'peerStartBoundary': 'target_actual_primary_request_pending_before_peer_workflow_creation',
        'inputSource': real.PROTOCOL['inputSource'], 'processName': real.PROTOCOL['processName'], 'systemPrompt': real.PROTOCOL['systemPrompt'],
        'peerReleaseBoundary': 'actual_native_primary_response_after_old_native_pids_absent_and_new_epoch_two_warmups',
        'nativeFault': active.active_spec(view, 1)}


def agent_stage(trace, case): return common.agent_stage(trace, case, real.PROTOCOL['processName'])


def validate_preparation(projected, prepared, artifacts):
    return common.validate_preparation(projected, prepared, artifacts, process_name=real.PROTOCOL['processName'])


def verify_primary_response(row, case, spec):
    request = raw_body(row, 'requestBody'); native_request = raw_body(row, 'nativeRequestBody')
    native = raw_body(row, 'nativeResponseBody'); response = raw_body(row, 'responseBody')
    messages = [{'role': 'system', 'content': spec['systemPrompt']}, {'role': 'user',
        'content': 'Задача: '+spec['processName']+'\n\nВходные данные:\n'+case['request']['state']}]
    require(same_json(request, {'model': real.primary.MODEL, 'messages': messages, 'stream': False, 'temperature': .2})
        and messages[1]['content'].count(case['request']['state']) == 1, 'Primary whole input or condition-blind prompt changed')
    require(same_json(native_request, {'model': real.primary.MODEL, 'messages': messages, 'stream': False, 'keep_alive': '5m', **GENERATION}), 'Actual native primary generation/input changed')
    real.primary.validate_response(native)
    require(native['done_reason'] in ('stop', 'length') and not native['message'].get('thinking') and not native['message'].get('tool_calls')
        and type(native['prompt_eval_count']) is int and 0 < native['prompt_eval_count'] < 32768-128
        and type(native['eval_count']) is int and 0 < native['eval_count'] <= 128, 'Native primary truncated input or exceeded pinned decode profile')
    expected = {'choices': [{'message': {'role': 'assistant', 'content': native['message']['content']}, 'finish_reason': native['done_reason']}],
        'usage': {'prompt_tokens': native['prompt_eval_count'], 'completion_tokens': native['eval_count']}}
    require(row['httpStatus'] == row['nativeHttpStatus'] == 200 and same_json(response, expected), 'Actual primary response transformed or failed')
    output = native['message']['content'].strip()
    require(row['outputSha256'] == hashlib.sha256(output.encode()).hexdigest() and output, 'Actual primary output SHA differs')
    begin = timestamp(row['startedAt'], 'primary.startedAt'); end = timestamp(row['completedAt'], 'primary.completedAt')
    require(begin <= timestamp(row['nativeStartedAt'], 'primary.nativeStartedAt') <= timestamp(row['nativeResponseReceivedAt'], 'primary.responseAt') <= end
        and abs((end-begin).total_seconds()*1000-number(row['elapsedMs'], 0, 180000)) <= 10, 'Actual primary timing omitted or outside budget')
    return output


def verify_actor(context, spec, recipe, driver, artifacts):
    evidence = common.verify_actor(context, spec, recipe, driver, artifacts, suite=sys.modules[__name__])
    rows = journal(artifacts['primary-http.jsonl']); by_index = {r['index']: r for r in rows}
    require(len(by_index) == 4, 'Real primary request repeated')
    held = parse_json(artifacts['peer-primary-held.json']); released = parse_json(artifacts['peer-primary-released.json'])
    peer = by_index[2]; target = by_index[1]
    require(held['kind'] == 'actual_pending_native_primary' and released['kind'] == 'actual_native_primary_response'
        and held['nativeRequestBodySha256'] == peer['nativeRequestBodySha256'] and held['nativeUrl'] == recipe['endpoints']['primaryNativeUrl']
        and held['nativeStartedAt'] == peer['nativeStartedAt'] and released['nativeResponseReceivedAt'] == peer['nativeResponseReceivedAt']
        and released['nativeResponseBodySha256'] == peer['nativeResponseBodySha256']
        and all(row['nativeUrl'] == recipe['endpoints']['primaryNativeUrl'] for row in rows), 'Peer was a held fixture or native request/response was rebound')
    recovered = parse_json(artifacts['native-recovered.json']); ready = parse_json(artifacts['coordinator-cancellation-ready.json'])
    require(timestamp(peer['nativeStartedAt'], 'peer.nativeStart') <= timestamp(ready['activeObservedAt'], 'native.activeAt')
        < timestamp(recovered['appliedAt'], 'native.recoveredAt') <= timestamp(peer['nativeResponseReceivedAt'], 'peer.responseAt'), 'Actual primary peer responded before cancellation/recovery')
    overlap = (min(timestamp(r['nativeResponseReceivedAt'], 'primary.responseAt') for r in (peer, target))
        -max(timestamp(r['nativeStartedAt'], 'primary.nativeStart') for r in (peer, target))).total_seconds()*1000
    require(overlap > 0 and timestamp(target['completedAt'], 'target.primaryDone') <= timestamp(ready['activeObservedAt'], 'native.activeAt'), 'Actual two-slot primary overlap or completed target primary missing')
    cohort = parse_json(artifacts['cohort.http.json']); instance = next(i for i in cohort['instances'] if i['runId'] == peer['runId'])
    require(timestamp(target['nativeStartedAt'], 'target.primaryStart') <= timestamp(instance['createdAt'], 'peer.createdAt')
        <= timestamp(peer['nativeStartedAt'], 'peer.primaryStart') < timestamp(target['nativeResponseReceivedAt'], 'target.responseAt'), 'Actual pending target primary was not admitted before peer creation')
    return {**evidence, 'actualPrimaryHttpOverlapMs': round(overlap, 3), 'primaryModel': real.primary.MODEL,
        'primaryNumParallel': 1, 'peerResponseObservedWithoutArtificialHold': True, 'primaryConcurrencyCapacityQualified': False}


def verify_primary_inventory(plan, result, recipe, artifacts):
    value = fields(plan['primary'], {'model', 'manifestSha256', 'blobCount', 'blobBytes', 'release', 'releaseFileSha256', 'generation', 'settings', 'warmupRequest'})
    pin = (Path(__file__).resolve().parents[2]/real.primary.PRIMARY_SOURCES[2]).read_bytes()
    require(value['model'] == real.primary.MODEL and value['manifestSha256'] == real.primary.DIGEST and value['blobCount'] == 5
        and value['blobBytes'] == 5225388164 and value['release'] == parse_json(pin) and value['releaseFileSha256'] == hashlib.sha256(pin).hexdigest()
        and same_json(value['generation'], GENERATION) and same_json(value['settings'], SETTINGS) and same_json(value['warmupRequest'], WARMUP_REQUEST), 'Pinned primary model/release/residency profile changed')
    require(type(result['primaryExitCode']) is int and result['primaryExitCode'] == 0, 'Primary exit or cleanup unknown/failed')
    warmup = parse_json(artifacts['primary-warmup.json']); require(same_json(warmup['request'], WARMUP_REQUEST), 'Raw primary warmup changed')
    real.primary.validate_response(warmup['response']); number(warmup['wallMs'], 0, 90000)
    for name in ('primary-before.json', 'primary-after.json'):
        snapshot = parse_json(artifacts[name]); entries = [m for m in snapshot['tags']['models'] if m.get('name') == real.primary.MODEL]
        resident = snapshot['residence']['models']
        require(snapshot['version'] == {'version': '0.35.1'} and len(entries) == len(resident) == 1
            and entries[0]['digest'] == resident[0]['digest'] == real.primary.DIGEST and resident[0]['context_length'] == 32768
            and not entries[0].get('remote_model') and not entries[0].get('remote_host') and snapshot['ownedPids']
            and set(snapshot['ownedPids']) <= set(result['ownedPids']), 'Actual primary release/residence/context/ownership changed')
        require(not set(snapshot['ownedPids']) & set(parse_json(artifacts['native-retired.json'])['nativePids']), 'Primary process was retired with native decider')
    before = parse_json(artifacts['primary-before.json']); after = parse_json(artifacts['primary-after.json']); driver = parse_json(artifacts['workflow-driver.json'])
    require(timestamp(before['capturedAt'], 'primary.before') < timestamp(recipe['startAt'], 'workflow.startAt')
        and timestamp(after['capturedAt'], 'primary.after') >= timestamp(driver['endAt'], 'workflow.endAt'), 'Actual primary snapshots do not enclose scoring/recovery')
    real.verify_primary_runner(artifacts['primary.log'], parallel=1)
    require('truncating input prompt' not in artifacts['primary.log'].decode().lower(), 'Ollama truncated a whole original input')


def inventory(context, plan, result, artifacts): return common.inventory(context, plan, result, artifacts, suite=sys.modules[__name__])


def verify(root, directory, context_path, **pins): return common.verify(root, directory, context_path, suite=sys.modules[__name__], **pins)
