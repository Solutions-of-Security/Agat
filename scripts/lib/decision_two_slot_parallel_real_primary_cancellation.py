"""Prospective native cancellation with two worker and two actual primary slots."""
from pathlib import Path
import re
import shlex
import sys
from urllib.parse import urlparse
from decision_runtime.contracts import parse_json
from scripts.lib.decision_shadow_pilot import require, timestamp
from scripts.lib import decision_two_slot_cancellation as common
from scripts.lib import decision_two_slot_real_primary_cancellation as primary

PLAN_SCHEMA = 'agat.decision.two-slot-parallel-real-primary-cancellation-plan.v2'
RESULT_SCHEMA = 'agat.decision.two-slot-parallel-real-primary-cancellation-result.v2'
VERIFICATION_SCHEMA = 'agat.decision.two-slot-parallel-real-primary-cancellation-verification.v2'
REAL_PRIMARY = True
PRIMARY_RUNNER_PROGRESS = True
real = primary.real
DRIVER_PATH = 'scripts/run-two-slot-parallel-real-primary-cancellation.mts'
SOURCE_PATHS = [*primary.SOURCE_PATHS, 'scripts/run-two-slot-parallel-real-primary-cancellation.py', DRIVER_PATH,
    'scripts/verify-two-slot-parallel-real-primary-cancellation.py', 'scripts/test/test_decision_two_slot_parallel_real_primary_cancellation.py']
ARTIFACTS, AUTHORITY = primary.ARTIFACTS | {'primary-runner.json', 'primary-progress.jsonl', 'primary-parallel-progress.jsonl'}, primary.AUTHORITY
PREPARED_FILE, READY_FILE, DRAINED_FILE = primary.PREPARED_FILE, primary.READY_FILE, primary.DRAINED_FILE
GENERATION, SETTINGS, WARMUP_REQUEST = real.GENERATION, real.SETTINGS, real.WARMUP_REQUEST
prepare_primary = real.prepare
projection, selected = primary.projection, primary.selected
make_proxy, recovery, verify_physical = primary.make_proxy, primary.recovery, primary.verify_physical
journal, raw_body, shared_config = primary.journal, primary.raw_body, primary.shared_config
agent_stage, validate_preparation = primary.agent_stage, primary.validate_preparation
verify_primary_response = primary.verify_primary_response


class OwnedPrimary(real.OwnedPrimary):
    def start(self):
        super().start()
        owned = set(self.sample()['ownedPids']); children = owned - {self.process.pid}
        require(len(children) == 1, 'Expected one owned native primary runner')
        runner_pid = next(iter(children))
        command = self.runtime.shared.command(['ps', '-p', str(runner_pid), '-o', 'command=']).strip()
        argv = shlex.split(command)
        require(argv and Path(argv[0]).resolve() == (self.binaries/'llama-server').resolve(), 'Primary descendant is not the pinned native runner')
        port = int(argv[argv.index('--port')+1])
        require(argv[argv.index('--host')+1] == '127.0.0.1' and port not in (self.port,8766,9095,11434)
            and argv[argv.index('-np')+1] == '2' and argv[argv.index('-c')+1] == '65536', 'Primary runner endpoint/slots/context changed')
        self.runner = {'url': 'http://127.0.0.1:'+str(port), 'runnerPid': runner_pid, 'parentPid': self.process.pid,
            'command': command, 'capturedAt': real.now()}


def protocol(context, target):
    return {**primary.protocol(context, target), 'primaryNumParallel': 2,
        'peerStartBoundary': 'target_actual_primary_decode_progress_before_peer_workflow_creation',
        'primaryProgress': {'path': '/slots', 'minDecoded': 116, 'maxDecoded': 127, 'pollMs': 10,
            'httpTimeoutMs': 5000, 'sampleToPeerCreationMaxMs': 250, 'requireTwoProcessingSlots': True}}


def verify_actor(context, spec, recipe, driver, artifacts):
    evidence = common.verify_actor(context, spec, recipe, driver, artifacts, suite=sys.modules[__name__])
    return {**evidence, **primary.verify_primary_peer(context, spec, recipe, artifacts, parallel=2),
        **verify_progress(context, spec, recipe, artifacts)}


def verify_progress(context, spec, recipe, artifacts):
    info = parse_json(artifacts['primary-runner.json']); url = urlparse(info['url'])
    require(url.scheme == 'http' and url.hostname == '127.0.0.1' and url.port and url.port not in (8766,9095,11434)
        and url.port not in (urlparse(recipe['endpoints'][name]).port for name in recipe['endpoints'])
        and url.path == '' and not url.username and not url.password and not url.query and not url.fragment, 'Primary progress used a foreign endpoint')
    argv = shlex.split(info['command'])
    require(Path(argv[0]).name == 'llama-server' and argv[argv.index('--port')+1] == str(url.port)
        and argv[argv.index('--host')+1] == '127.0.0.1' and argv[argv.index('-np')+1] == '2' and argv[argv.index('-c')+1] == '65536', 'Owned runner argv disagrees with progress endpoint/profile')
    before = parse_json(artifacts['primary-before.json']); after = parse_json(artifacts['primary-after.json'])
    require(type(info['runnerPid']) is int and type(info['parentPid']) is int and info['runnerPid'] != info['parentPid']
        and {info['runnerPid'],info['parentPid']} <= set(before['ownedPids']) & set(after['ownedPids'])
        and timestamp(info['capturedAt'], 'runner.capturedAt') < timestamp(recipe['startAt'], 'workflow.startAt'), 'Progress runner ownership or lifecycle changed')
    primary_rows = {r['index']: r for r in journal(artifacts['primary-http.jsonl'])}; target = primary_rows[1]; peer = primary_rows[2]
    cohort = parse_json(artifacts['cohort.http.json']); instance = next(i for i in cohort['instances'] if i['runId'] == peer['runId'])
    rows = journal(artifacts['primary-progress.jsonl']); require(0 < len(rows) <= 1000, 'Missing/excessive primary progress reads')
    previous = None; selected = None; task = slot_id = None; decoded = 0
    for row in rows:
        require(row['method'] == 'GET' and row['url'] == info['url']+'/slots' and row['httpStatus'] == 200
            and row['runId'] == target['runId'] and row['inputSha256'] == context['inputs'][spec['selectedOriginalIndices'][1]]['inputSha256']
            and row['nativeRequestBodySha256'] == target['nativeRequestBodySha256'], 'Raw slot read not bound to pending target/native input')
        begin = timestamp(row['startedAt'], 'progress.startedAt'); end = timestamp(row['receivedAt'], 'progress.receivedAt')
        require(timestamp(target['nativeStartedAt'], 'target.startedAt') <= begin <= end
            < timestamp(instance['createdAt'], 'peer.createdAt') < timestamp(target['nativeResponseReceivedAt'], 'target.responseAt')
            and (end-begin).total_seconds()*1000 <= spec['primaryProgress']['httpTimeoutMs']
            and (previous is None or previous <= begin), 'Progress is stale, after peer creation, or after completed primary response')
        previous = end; slots = raw_body(row,'body')
        require(isinstance(slots,list) and len(slots)==2 and {s['id'] for s in slots}=={0,1}
            and all(type(s['id']) is int and type(s['n_ctx']) is int and s['n_ctx']==32768 and type(s['is_processing']) is bool for s in slots), 'Actual primary slots/context omitted')
        active = [s for s in slots if s['is_processing']]
        require(len(active)<=1, 'Unexpected undeclared primary request before peer creation')
        if not active:
            require(task is None, 'Target primary became idle before late-decode admission')
            continue
        s=active[0]; tokens=s['next_token']
        require(isinstance(tokens,list) and len(tokens)==1 and type(tokens[0]['n_decoded']) is int
            and decoded<=tokens[0]['n_decoded']<128 and type(s['params']['n_predict']) is int and s['params']['n_predict']==128
            and type(s['params']['seed']) is int and s['params']['seed']==0 and type(s['params']['temperature']) in (int,float)
            and s['params']['temperature']==0 and type(s['id_task']) is int and s['id_task']>=0, 'Pinned native progress shape/generation changed')
        require((task is None or task==s['id_task']) and (slot_id is None or slot_id==s['id']), 'Pending target moved to another native task/slot')
        task,slot_id=s['id_task'],s['id']
        decoded=tokens[0]['n_decoded']
        if 116<=tokens[0]['n_decoded']<=127:
            require(selected is None and tokens[0]['has_next_token'] is True, 'Primary late-decode witness repeated or already ended')
            selected=(row,tokens[0]['n_decoded'])
    require(selected is not None and selected[0] is rows[-1], 'Peer not created immediately after the first observed late-decode boundary')
    target_response = raw_body(target,'nativeResponseBody')
    require(selected[1] < target_response['eval_count'] <= 128, 'Slot progress exceeds the delivered target decode')
    late_slot = next(s for s in raw_body(selected[0],'body') if s['id']==slot_id)
    require(all(type(late_slot[key]) is int and late_slot[key]>=0 for key in ('n_prompt_tokens','n_prompt_tokens_processed','n_prompt_tokens_cache'))
        and late_slot['n_prompt_tokens_processed']+late_slot['n_prompt_tokens_cache']==target_response['prompt_eval_count']
        and late_slot['n_prompt_tokens']==target_response['prompt_eval_count']+selected[1], 'Late-decode native prompt/progress accounting differs from actual response')
    require(re.search(r'slot launch_slot_:\s+id\s+'+str(slot_id)+r'\s+\|\s+task\s+'+str(task)+r'\s+\|\s+processing task',
        artifacts['primary.log'].decode()), 'Late-decode task/slot absent from actual native runner log')
    require((timestamp(instance['createdAt'],'peer.createdAt')-timestamp(selected[0]['receivedAt'],'progress.receivedAt')).total_seconds()*1000
        <= spec['primaryProgress']['sampleToPeerCreationMaxMs'], 'Late-decode admission snapshot was stale')
    parallel_rows = journal(artifacts['primary-parallel-progress.jsonl'])
    require(0 < len(parallel_rows) <= 1000, 'Missing/excessive parallel slot reads')
    witnessed = None; previous = None; peer_task = None
    peer_response = raw_body(peer, 'nativeResponseBody')
    for row in parallel_rows:
        require(row['method'] == 'GET' and row['url'] == info['url']+'/slots' and row['httpStatus'] == 200
            and row['targetRunId'] == target['runId'] and row['peerRunId'] == peer['runId']
            and row['targetInputSha256'] == context['inputs'][spec['selectedOriginalIndices'][1]]['inputSha256']
            and row['peerInputSha256'] == context['inputs'][spec['selectedOriginalIndices'][2]]['inputSha256']
            and row['targetNativeRequestBodySha256'] == target['nativeRequestBodySha256']
            and row['peerNativeRequestBodySha256'] == peer['nativeRequestBodySha256'], 'Parallel slot read not bound to original target/peer requests')
        begin = timestamp(row['startedAt'], 'parallel.startedAt'); end = timestamp(row['receivedAt'], 'parallel.receivedAt')
        require(timestamp(peer['nativeStartedAt'], 'peer.startedAt') <= begin <= end < timestamp(target['nativeResponseReceivedAt'], 'target.responseAt')
            and (end-begin).total_seconds()*1000 <= spec['primaryProgress']['httpTimeoutMs']
            and (previous is None or previous <= begin), 'Parallel slot read stale or after completed target')
        previous = end; slots = raw_body(row, 'body')
        require(isinstance(slots,list) and len(slots)==2 and {s['id'] for s in slots}=={0,1}
            and all(type(s['id']) is int and type(s['n_ctx']) is int and s['n_ctx']==32768 and type(s['is_processing']) is bool for s in slots), 'Parallel native slots/context omitted')
        target_slot = next(s for s in slots if s['id']==slot_id)
        require(target_slot['is_processing'] and target_slot['id_task']==task, 'Original target task absent from parallel witness')
        active = [s for s in slots if s['is_processing']]
        for s in active:
            tokens = s['next_token']
            require(type(s['id_task']) is int and s['id_task']>=0 and isinstance(tokens,list) and len(tokens)==1
                and type(tokens[0]['n_decoded']) is int and 0<=tokens[0]['n_decoded']<128
                and type(tokens[0]['has_next_token']) is bool and type(s['params']['n_predict']) is int and s['params']['n_predict']==128
                and type(s['params']['seed']) is int and s['params']['seed']==0 and type(s['params']['temperature']) in (int,float)
                and s['params']['temperature']==0, 'Parallel slot task/generation/progress changed')
        target_decoded = target_slot['next_token'][0]['n_decoded']
        require(selected[1]<=target_decoded<target_response['eval_count'] and target_slot['next_token'][0]['has_next_token'] is True
            and all(type(target_slot[key]) is int and target_slot[key]>=0 for key in ('n_prompt_tokens','n_prompt_tokens_processed','n_prompt_tokens_cache'))
            and target_slot['n_prompt_tokens_processed']+target_slot['n_prompt_tokens_cache']==target_response['prompt_eval_count']
            and target_slot['n_prompt_tokens']==target_response['prompt_eval_count']+target_decoded, 'Parallel target progress differs from delivered response')
        if len(active)==2:
            require(witnessed is None, 'Parallel proof continued after first two-active-slots witness')
            peer_slot = next(s for s in active if s['id']!=slot_id); peer_task = peer_slot['id_task']
            require(peer_task!=task and peer_slot['next_token'][0]['n_decoded']<=peer_response['eval_count']
                and all(type(peer_slot[key]) is int and peer_slot[key]>=0 for key in ('n_prompt_tokens','n_prompt_tokens_processed','n_prompt_tokens_cache'))
                and peer_slot['n_prompt_tokens_processed']+peer_slot['n_prompt_tokens_cache']<=peer_response['prompt_eval_count'], 'Peer task/progress not consistent with delivered original input')
            require(re.search(r'slot launch_slot_:\s+id\s+'+str(peer_slot['id'])+r'\s+\|\s+task\s+'+str(peer_task)+r'\s+\|\s+processing task',
                artifacts['primary.log'].decode()), 'Parallel peer task/slot absent from native runner log')
            witnessed = row
    require(witnessed is not None and witnessed is parallel_rows[-1], 'Two actual processing slots were never observed before target response')
    return {'actualTargetDecodedAtPeerAdmission': selected[1], 'actualTargetPrimarySlotId': slot_id,
        'actualTargetPrimaryTaskId': task, 'actualPeerPrimaryTaskId': peer_task, 'actualPrimaryTwoProcessingSlotsObserved': True,
        'actualPrimaryProgressReads': len(rows), 'actualPrimaryParallelProgressReads': len(parallel_rows), 'primaryProgressEndpointReadOnly': True}


def verify_primary_inventory(plan, result, recipe, artifacts):
    return primary.verify_primary_inventory(plan, result, recipe, artifacts, settings=SETTINGS, parallel=2)


def inventory(context, plan, result, artifacts): return common.inventory(context, plan, result, artifacts, suite=sys.modules[__name__])


def verify(root, directory, context_path, **pins): return common.verify(root, directory, context_path, suite=sys.modules[__name__], **pins)
