#!/usr/bin/env python3
"""Independently verify frozen sources, real RAG provenance and recovery evidence."""
import argparse
import base64
from collections import Counter, defaultdict
import hashlib
import importlib.util
import io
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_runtime.contracts import Request
from scripts.lib.decision_performance import validate_result
spec = importlib.util.spec_from_file_location('real_rag_launcher', ROOT / 'scripts/run-temporal-real-rag.py')
launcher_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher_module)
MODELS, SOURCES = launcher_module.shared.MODELS, launcher_module.SOURCES


def sha(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def digest(value):
    assert isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value)
    return value


def positive(value):
    assert type(value) in (int, float) and math.isfinite(value) and value > 0
    return value


def payload(value):
    assert len(value['payloads']) == 1
    item = value['payloads'][0]
    assert base64.b64decode(item['metadata']['encoding'], validate=True) == b'json/plain'
    return json.loads(base64.b64decode(item['data'], validate=True))


def verify(directory):
    if not __debug__:
        raise RuntimeError('Assertions must be enabled')
    directory = directory.resolve()
    assert directory.is_relative_to(ROOT / 'docs')
    plan, launcher = [json.loads((directory / f'{name}.json').read_text()) for name in ('plan', 'launcher')]
    shadow = plan['schema'] == 'agat.temporal.real-rag-plan.v2'
    version = 2 if shadow else 1
    assert plan['schema'] == f'agat.temporal.real-rag-plan.v{version}'
    assert re.fullmatch('[0-9a-f]{40}', plan['implementationCommit'])
    paths = [*SOURCES, *(launcher_module.SHADOW_SOURCES if shadow else [])]
    archived = subprocess.check_output(['git', 'archive', plan['implementationCommit'], '--', *paths], cwd=ROOT, timeout=15)
    with tarfile.open(fileobj=io.BytesIO(archived)) as archive:
        sources = {member.name: archive.extractfile(member).read() for member in archive if member.isfile()}
    assert plan['sourceSha256'] == {name: sha(raw) for name, raw in sources.items()}
    assert plan['fixturePath'] == launcher_module.shared.FIXTURE
    assert plan['fixture'] == json.loads(sources[plan['fixturePath']])
    assert plan['models'] == MODELS and plan['transports'] == ['isolated', 'session']
    assert plan['shadow'] is shadow and plan['routingEnabled'] is False and plan['qualification'] == 'not_assessed'
    profile = None
    if shadow:
        decision = plan['decision']
        assert decision['referencePath'] == launcher_module.SHADOW_REFERENCE and decision['policyPath'] == launcher_module.SHADOW_POLICY
        assert decision['profile'] == json.loads(sources[decision['referencePath']])['decision']
        assert decision['warmupCalls'] == 1
        assert sha(decision['profile']['profileJson']) == decision['profile']['profileSha256'] == '4bd6e0de2bfde982d8d5fbdfc4d5e7ef36ccd1d8bb69356cd558a33934cb7a2a'
        profile = json.loads(decision['profile']['profileJson'])
        assert decision['policy'] == json.loads(sources[decision['policyPath']])
        assert all(decision['manifest'][key] == profile['model'][key] for key in ['repository', 'revision', 'artifactSha256'])
        assert sha(json.dumps(decision['manifest']['files'], sort_keys=True, separators=(',', ':'))) == decision['manifest']['artifactSha256']
        assert decision['runtime']['packages'] == dict(line.split('==') for line in sources['decision_runtime/requirements-mlx.txt'].decode().splitlines()
                                                      if line and not line.startswith('#'))
    assert plan['concurrency'] == 1 and plan['workloadBudgetSeconds'] == 600
    assert plan['databaseIsolation'] == 'one_owned_server_per_transport'
    assert plan['ollamaSettings'] == {'OLLAMA_NO_CLOUD': '1', 'OLLAMA_MAX_LOADED_MODELS': '2', 'OLLAMA_NUM_PARALLEL': '1',
                                      'OLLAMA_CONTEXT_LENGTH': '8192', 'OLLAMA_KEEP_ALIVE': '5m'}
    assert plan['postgresImage'] == 'postgres:17.6-alpine' and plan['temporalImage'] == 'temporalio/temporal:1.8.1'
    assert launcher['schema'] == f'agat.temporal.real-rag-launcher.v{version}' and launcher['status'] == 'pass' and launcher['failure'] is None
    plan_sha = sha((directory / 'plan.json').read_bytes())
    assert launcher['planSha256'] == plan_sha
    assert launcher['cleanupErrors'] == launcher['remainingOwnedPids'] == []
    assert launcher['modelsBefore'] == launcher['modelsAfterUnload'] == {'models': []}
    assert launcher['workloadExitCode'] == launcher['ollamaExitCode'] == 0
    assert launcher['logSha256']['tests.log'] == sha((directory / 'tests.log').read_bytes())
    digest(launcher['logSha256']['ollama.log'])
    elapsed = positive(launcher['elapsedMs'])
    owned = set(launcher['ownedPids'])
    assert len(owned) == len(launcher['ownedPids']) and all(type(pid) is int and pid > 0 for pid in owned)
    assert launcher['ollamaPid'] in owned and launcher['workloadPid'] in owned
    if shadow:
        runtime = launcher['shadowRuntime']
        assert runtime['pid'] in owned and runtime['exitCode'] == 130
        assert runtime['before'] == runtime['after']
        assert runtime['before']['status'] == 'ready' and runtime['before']['mode'] == 'shadow'
        assert all(runtime['before'][key] == plan['decision']['profile'][key] for key in ['profileJson', 'profileSha256'])
        assert runtime['before']['model'] == profile['model']; digest(launcher['logSha256']['decision.log'])
        assert runtime['warmup']['httpStatus'] == 200 and runtime['warmup']['result']['status'] in ['ok', 'abstain']
        warmup_request = Request.from_dict({'schemaVersion': 'agat.decision.v1', 'id': 'temporal-shadow-warmup',
                                           'state': plan['fixture']['input'], **plan['fixture']['shadow']})
        validate_result(runtime['warmup']['result'], warmup_request, profile)
    assert [row['transport'] for row in launcher['workloads']] == plan['transports']
    assert all(row['exitCode'] == 0 and row['pid'] in owned for row in launcher['workloads'])
    assert len({row['pid'] for row in launcher['workloads']}) == 2
    assert len(launcher['containers']) == 4
    assert Counter(row['image'] for row in launcher['containers'].values()) == Counter({plan['postgresImage']: 2, plan['temporalImage']: 2})
    assert len({row['id'] for row in launcher['containers'].values()}) == 4
    for name, row in launcher['containers'].items():
        match = re.fullmatch(r'agat-temporal-(?:postgres-)?rag-(\d+)-\d+', name)
        assert match and int(match[1]) in owned
        digest(row['id']); assert row['imageId'].startswith('sha256:'); digest(row['imageId'][7:])
    assert {row['model'] for row in launcher['warmup']} == set(MODELS) and len(launcher['warmup']) == 2
    for row in launcher['warmup']:
        assert 0 <= row['nativeLoadMs'] <= positive(row['nativeTotalMs']) < elapsed
    assert launcher['loadedModelSamples'] and any({item['name'] for item in row['models']} == set(MODELS)
                                                for row in launcher['loadedModelSamples'])
    for row in launcher['loadedModelSamples']:
        assert 0 < row['elapsedMs'] < elapsed
        for model in row['models']:
            assert model['digest'] == MODELS[model['name']]
            assert model['context_length'] == (8192 if model['name'] == 'qwen3:8b' else 2048)
            positive(model['size']); positive(model['size_vram'])
    fixture = plan['fixture']
    source_by_id = {row['id']: row for row in fixture['rag']['sources']}
    source_hashes = {key: sha(row['content']) for key, row in source_by_id.items()}
    vectors, prompt_sets, output_sets, summaries = defaultdict(set), [], [], []
    assert set(launcher['phaseSha256']) == {'isolated.json', 'session.json'}
    for transport in plan['transports']:
        phase_path = directory / f'{transport}.json'
        assert launcher['phaseSha256'][phase_path.name] == sha(phase_path.read_bytes())
        phase = json.loads(phase_path.read_text())
        assert phase['schema'] == f'agat.temporal.real-rag.v{version}' and phase['status'] == 'pass' and phase['failure'] is None
        assert phase['transport'] == transport and phase['planSha256'] == plan_sha and phase['qualification'] == 'not_assessed'
        assert set(phase['checks']) == {'activityRetried', 'temporalWorkerKilledAndReplaced', 'sameWorkflowRun',
            'firstAcceptedStageUnchanged', 'realModelOutputsPreserved', 'durableTimerFired', 'nativeReplayWithoutSideEffects',
            'shadowObservationsPreserved' if shadow else 'shadowDisabled', 'childrenDrained'} and all(value is True for value in phase['checks'].values())
        phase_ms = positive(phase['elapsedMs']); assert phase_ms < elapsed
        for kind, name in [('primary', 'qwen3:8b'), ('embedding', 'embeddinggemma:latest')]:
            assert phase[kind]['name'] == name and phase[kind]['digest'] == MODELS[name]
            assert phase[kind]['version'] == launcher['ollamaVersion']['version']; digest(phase[kind]['descriptionSha256'])
        assert phase['primary']['generation'] == {'think': False, 'options': {'temperature': .2, 'seed': 0, 'num_ctx': 8192, 'num_predict': 384}}
        assert phase['embedding']['generation'] == {'truncate': False, 'keep_alive': '5m', 'options': {'num_ctx': 2048}}
        ingestion = phase['ingestion']
        assert ingestion['embeddingModel'] == fixture['rag']['embeddingModel'] and ingestion['dimensions'] == 768
        assert len(ingestion['sources']) == 2 and {row['id'] for row in ingestion['sources']} == set(source_by_id)
        by_chunk = {row['chunkId']: row for row in ingestion['sources']}; assert len(by_chunk) == 2
        for row in by_chunk.values():
            assert row['documentSha256'] == row['chunkSha256'] == source_hashes[row['id']]
            assert row['sourceUri'] == source_by_id[row['id']]['sourceUri'] and row['dimensions'] == 768
        embeddings = phase['embeddingCalls']; assert 4 <= len(embeddings) <= 5
        inputs, query_vectors = [], []
        for call in embeddings:
            assert call['status'] == 'completed' and call['model'] == 'embeddinggemma:latest' and call['dimensions'] == 768
            assert 0 < call['startedMs'] < call['finishedMs'] < phase_ms
            assert 0 <= call['nativeLoadMs'] <= positive(call['nativeTotalMs']) < phase_ms
            positive(call['inputTokens'])
            assert call['items'] == len(call['inputSha256']) == len(call['vectorSha256'])
            for key, vector in zip(call['inputSha256'], call['vectorSha256']):
                inputs.append(digest(key)); vectors[key].add(digest(vector))
                if key not in source_hashes.values(): query_vectors.append(vector)
        assert len(inputs) == 5 and sum(key in source_hashes.values() for key in inputs) == 2
        assert Counter(inputs) & Counter(source_hashes.values()) == Counter(source_hashes.values())
        trace = phase['trace']; assert trace['truncated'] is False and trace['run']['status'] == 'completed'
        assert len(trace['decisionObservations']) == (3 if shadow else 0)
        if shadow:
            assert phase['decision'] == plan['decision']['profile'] and len(phase['decisionCalls']) == 3
        stages = [row for row in trace['run']['stages'] if row['kind'] == 'agent']
        assert [row['processNodeId'] for row in stages] == [row['id'] for row in fixture['roles']]
        assert len({row['id'] for row in stages}) == 3
        calls = phase['primaryCalls']; assert len(calls) == len(phase['retrieval']) == 3
        known, queries, expected_inputs = {}, [], list(source_hashes.values())
        for index, (stage, call) in enumerate(zip(stages, calls)):
            assert stage['status'] == 'completed' and stage['attempt'] == 1
            assert stage['output'] == call['output'] and sha(call['output']) == call['outputSha256']
            assert call['status'] == 'completed' and call['doneReason'] == 'stop'
            assert call['retrievedSourceIds'] == list(source_by_id)
            assert 0 < call['startedMs'] < call['modelFinishedMs'] <= call['finishedMs'] < phase_ms
            positive(call['nativeTotalMs']); positive(call['inputTokens']); positive(call['outputTokens']); digest(call['messagesSha256'])
            assert stage['metrics']['model'] == 'qwen3:8b' and stage['metrics']['modelCalls'] == 1
            assert stage['metrics']['inputTokens'] == call['inputTokens'] and stage['metrics']['outputTokens'] == call['outputTokens']
            # Process edges forward the preceding output as the next lease's
            # run.input; the ordered completed-stage context is also present.
            assert stage['input'] == (stages[index - 1]['output'] if index else None)
            query_text = '\n\n'.join([stage['input'] if index else fixture['input'],
                                      *[row['output'] for row in stages[:index]]]).strip()[:50000]
            expected_inputs.append(sha(query_text))
            event = [row for row in trace['events'] if row['type'] == 'knowledge.retrieved' and row['stageId'] == stage['id']]
            assert len(event) == 1; data = event[0]['data']; assert len(data['queries']) == 1 and len(data['hits']) == 2
            query = data['queries'][0]
            assert query['embeddingModel'] == ingestion['embeddingModel'] and query['collectionIds'] == [ingestion['collectionId']]
            assert query['dimensions'] == 768 and 'vector' not in query
            queries.append(digest(query['vectorSha256']))
            assert queries[-1] in vectors[expected_inputs[-1]]
            hits = []
            for hit in data['hits']:
                provenance = hit['provenance']; source = by_chunk[provenance['chunkId']]
                assert provenance['collectionId'] == ingestion['collectionId'] and provenance['projectId'] == 'default'
                assert provenance['sourceUri'] == source['sourceUri']
                assert provenance['documentSha256'] == provenance['chunkSha256'] == sha(hit['content']) == source['chunkSha256']
                assert re.fullmatch('K[0-9]+', hit['marker']) and math.isfinite(hit['score'])
                value = {'sourceId': source['id'], 'marker': hit['marker'], 'chunkSha256': source['chunkSha256'], 'score': hit['score']}
                assert hit['marker'] not in known or known[hit['marker']] == value
                known[hit['marker']] = value; hits.append(value)
            assert {hit['sourceId'] for hit in hits} == set(source_by_id)
            markers = list(dict.fromkeys(re.findall(r'\[(K[0-9]+)\]', stage['output'])))
            assert all(marker in known for marker in markers)
            cited = list(dict.fromkeys(known[marker]['sourceId'] for marker in markers))
            assert set(cited) == set(source_by_id)
            assert phase['retrieval'][index] == {'queries': [{'vectorSha256': queries[-1], 'dimensions': 768}], 'hits': hits,
                'knownCitations': list(known.values()), 'outputMarkers': markers, 'unknownMarkers': [], 'citedSourceIds': cited, 'bothSourcesCited': True}
        assert len(known) == 6 and Counter(inputs) == Counter(expected_inputs) and Counter(queries) == Counter(query_vectors)
        recovery = phase['recovery']
        assert recovery['firstAcceptedStageSha256'] == sha(compact(stages[0]))
        assert recovery['primaryCallsBeforeRelease'] == 2
        assert calls[1]['modelFinishedMs'] <= recovery['heldResponseAtMs'] < recovery['killedAtMs'] < recovery['restoredAtMs'] <= recovery['releasedAtMs'] <= calls[1]['finishedMs'] < calls[2]['startedMs']
        if shadow:
            assert phase['shadowRecovery']['decisionCallsBeforeRelease'] == 1 and phase['shadowRecovery']['primaryFallbackPreserved'] is True
            observations = {row['stageId']: row for row in trace['decisionObservations']}
            decision_calls = {row['stageId']: row for row in phase['decisionCalls']}
            assert set(observations) == set(decision_calls) == {stage['id'] for stage in stages}
            assert len(observations) == len(decision_calls) == 3
            assert phase['shadowRecovery']['firstAcceptedObservationSha256'] == sha(compact(observations[stages[0]['id']]))
            for index, stage in enumerate(stages):
                call = decision_calls[stage['id']]; observation = observations[stage['id']]['observation']
                request = Request.from_dict({'schemaVersion': 'agat.decision.v1', 'id': stage['id'],
                                             'state': stage['input'] if index else fixture['input'], **fixture['shadow']})
                assert call['request'] == request.to_dict()
                assert call['httpStatus'] == 200 and calls[index]['finishedMs'] < call['startedMs'] < call['finishedMs'] < phase_ms
                if index < 2: assert call['finishedMs'] < calls[index + 1]['startedMs']
                validate_result(call['result'], request, profile)
                assert call['result']['status'] in ['ok', 'abstain']
                assert observation == {'mode': 'shadow', 'fallback': 'primary', 'status': call['result']['status'],
                                       'reason': call['result']['reason'], 'result': call['result']}
            assert decision_calls[stages[0]['id']]['finishedMs'] < recovery['heldResponseAtMs']
            assert decision_calls[stages[1]['id']]['startedMs'] > recovery['releasedAtMs']
        assert phase['database'] == {'driver': 'postgresql', 'runtimeRole': 'agat_system', 'tenantRole': 'agat_tenant',
            'visibility': {'own': [1, 3, 2], 'foreign': [0, 0, 0]}, 'releaseRegistryDenied': True, 'retrievalExecution': 'isolated'}
        assert len(phase['children']) == 4 and {row['pid'] for row in phase['children']} <= owned
        assert Counter((row['exitCode'], row['signal']) for row in phase['children']) == Counter({(0, None): 3, (None, 'SIGKILL'): 1})
        digest(phase['workflowBundleSha256'])
        events = phase['history']['events']
        assert [int(event['eventId']) for event in events] == list(range(1, len(events) + 1))
        first = events[0]['workflowExecutionStartedEventAttributes']
        assert first['workflowType']['name'] == 'agatProcessWorkflow' and first['workflowId'] == phase['workflowId'] == 'agat-process-' + phase['instanceId']
        assert first['originalExecutionRunId'] == first['firstExecutionRunId'] == phase['workflowRunId']
        assert payload(first['input'])['instanceId'] == phase['instanceId']
        assert payload(events[-1]['workflowExecutionCompletedEventAttributes']['result'])['status'] == 'completed'
        identities = {event.get('workflowTaskStartedEventAttributes', {}).get('identity') for event in events} - {None}
        assert identities == {recovery['firstIdentity'], recovery['secondIdentity']}
        assert recovery['firstIdentity'] == first['taskQueue']['name'] + '-before' and recovery['secondIdentity'] == first['taskQueue']['name'] + '-after'
        assert any(event.get('activityTaskStartedEventAttributes', {}).get('attempt', 0) >= 2 for event in events)
        assert any('timerFiredEventAttributes' in event for event in events)
        ticks = phase['ticks']; assert sum(row['dropped'] for row in ticks) == 1 and ticks[0]['dropped'] is True
        assert ticks[0]['response'] == ticks[1]['response']
        assert all(row['path'] == f"/api/v1/internal/processes/{phase['instanceId']}/tick" for row in ticks)
        assert Counter(compact(row['response']) for row in ticks[1:]) == Counter(compact(payload(event['activityTaskCompletedEventAttributes']['result']))
            for event in events if 'activityTaskCompletedEventAttributes' in event)
        prompt_sets.append([row['messagesSha256'] for row in calls]); output_sets.append([row['outputSha256'] for row in calls])
        summaries.append({'transport': transport, 'elapsedMs': phase_ms, 'primaryCalls': 3, 'embeddingItems': 5, 'historyEvents': len(events),
            'tickCalls': len(ticks), 'heldResponseMs': round(calls[1]['finishedMs'] - calls[1]['modelFinishedMs'], 3),
            'workflowRunId': phase['workflowRunId'], 'outputSha256': output_sets[-1]})
        if shadow:
            summaries[-1]['shadow'] = {'calls': 3, 'generatedTokens': 0,
                'outcomes': dict(Counter(f"{row['result']['status']}/{row['result']['reason']}" for row in phase['decisionCalls'])),
                'inputTokens': [row['result']['inputTokens'] for row in phase['decisionCalls']],
                'runtimeMs': [row['result']['durationMs'] for row in phase['decisionCalls']]}
    assert prompt_sets[0] == prompt_sets[1] and output_sets[0] == output_sets[1]
    assert len(vectors) == 5 and all(len(values) == 1 for values in vectors.values())
    assert len({row['workflowRunId'] for row in summaries}) == 2
    return {'schema': f'agat.temporal.real-rag-verification.v{version}', 'status': 'pass', 'implementationCommit': plan['implementationCommit'],
            'sourceFiles': len(sources), 'phases': summaries, 'equalPromptsAndOutputs': True, 'equalVectorsForFiveInputs': True,
            'primaryCalls': 6, 'embeddingItems': 10, 'sourceCitations': 12, 'nativeReplay': 'checked_by_live_harness',
            'ownedObservedPids': len(owned), 'ownedContainers': 4, 'qualification': 'not_assessed',
            **({'shadowCalls': 6, 'profileSha256': plan['decision']['profile']['profileSha256']} if shadow else {})}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = verify(args.directory)
    if args.output:
        assert args.output.resolve().is_relative_to(ROOT / 'docs')
        with args.output.open('x') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False); stream.write('\n')
    print(compact(result))
