#!/usr/bin/env python3
"""Independently verify frozen sources, real RAG provenance and recovery evidence."""
import argparse
import ast
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


def verify_shadow_profile(decision, sources):
    """Rebuild the selected identity from archived bytes, never today's runtime."""
    reference = launcher_module.SHADOW_REFERENCE
    historical = json.loads(sources[reference])['decision']
    if 'referenceFormat' not in decision:
        assert 'wiredLimitMiB' not in decision
        assert decision['referencePath'] == reference and decision['profile'] == historical
        assert historical['profileSha256'] == '4bd6e0de2bfde982d8d5fbdfc4d5e7ef36ccd1d8bb69356cd558a33934cb7a2a'
    else:
        assert decision['referenceFormat'] == 'runtime-profile-v1'
        selected = decision['referencePath']
        assert isinstance(selected, str) and Path(selected).as_posix() == selected and '..' not in Path(selected).parts
        assert Path(selected).is_relative_to('docs') and not Path(selected).is_relative_to('docs/private')
        expected = json.loads(historical['profileJson'])
        tree = ast.parse(sources['decision_runtime/__init__.py'])
        versions = [ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == 'VERSION' for target in node.targets)]
        assert len(versions) == 1 and isinstance(versions[0], str)
        expected['runtimeVersion'] = versions[0]
        runtime_sources = sorted(name for name in sources if re.fullmatch(r'decision_runtime/[^/]+\.py', name))
        assert runtime_sources
        implementation = b''.join(Path(name).name.encode() + b'\0' + sources[name] + b'\0' for name in runtime_sources)
        expected['model']['implementationSha256'] = sha(implementation)
        if 'wiredLimitMiB' in decision:
            wired = decision['wiredLimitMiB']
            assert type(wired) is int and 0 <= wired <= 65536
            expected['model']['allocatorWiredLimitBytes'] = wired * 1024**2
        raw = json.dumps(expected, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
        assert json.dumps(json.loads(sources[decision['referencePath']]), ensure_ascii=False,
                          sort_keys=True, separators=(',', ':'), allow_nan=False) == raw
        assert decision['profile'] == {'profileJson': raw, 'profileSha256': sha(raw)}
    assert sha(decision['profile']['profileJson']) == decision['profile']['profileSha256']
    return json.loads(decision['profile']['profileJson'])


def verify_runtime_control(plan, launcher, profile, warmup_request, owned, elapsed):
    control = launcher['shadowRecovery']
    assert control['schema'] == 'agat.shadow.control-result.v1'
    assert control['closed'] is True and control['failure'] is None
    observed = set(control['ownedPids'])
    assert len(observed) == len(control['ownedPids']) and observed <= owned
    states, events = control['runtimes'], control['events']
    assert len(states) == 3 and len({row['pid'] for row in states}) == 3
    assert [row['exitCode'] for row in states] == [-9, -9, 130]
    for state in states:
        assert state['pid'] in observed and 0 < state['startedMs'] < state['readyMs'] < elapsed
        assert state['health'] == launcher['shadowRuntime']['before']
        assert state['warmup']['httpStatus'] == 200 and state['warmup']['result']['status'] in ['ok', 'abstain']
        validate_result(state['warmup']['result'], warmup_request, profile)
    assert states[0]['warmup'] == launcher['shadowRuntime']['warmup']
    assert states[-1]['pid'] == launcher['shadowRuntime']['pid']
    assert [f"{row['transport']}/{row['action']}" for row in events] == plan['shadowRecovery']['actions']
    previous = states[0]['readyMs']
    for index, event in enumerate(events):
        assert event['status'] == 'pass' and 'failure' not in event
        assert previous < event['startedMs'] < event['finishedMs'] < elapsed
        previous = event['finishedMs']
        state = states[index // 2]
        assert event['oldPid'] == state['pid']
        if event['action'] == 'kill':
            before = set(event['ownedBeforeKill'])
            assert len(before) == len(event['ownedBeforeKill']) and len(before) >= 2
            assert state['pid'] in before and before <= observed
            assert event['exitCode'] == -9 and event['remainingAfterKill'] == []
        else:
            next_state = states[index // 2 + 1]
            assert event['newPid'] == next_state['pid'] and event['runtimeIndex'] == index // 2 + 1
            assert event['startedMs'] <= next_state['startedMs'] < next_state['readyMs'] <= event['finishedMs']
            assert event['profileSha256'] == plan['decision']['profile']['profileSha256']
            assert event['instanceId'] == events[index - 1]['instanceId']


def verify_runtime_phase(phase, launcher, stages, observations, decision_calls):
    runtime = phase['runtimeRecovery']
    controls = runtime['controlCalls']
    expected = [row for row in launcher['shadowRecovery']['events'] if row['transport'] == phase['transport']]
    assert [row['action'] for row in controls] == ['kill', 'restart']
    for row, event in zip(controls, expected, strict=True):
        assert row['response'] == event and event['instanceId'] == phase['instanceId']
        assert 0 < row['startedMs'] < row['finishedMs'] < phase['elapsedMs']
        # Launcher and integration clocks have different origins; compare
        # durations, then use each clock only within its own timeline.
        assert event['finishedMs'] - event['startedMs'] <= row['finishedMs'] - row['startedMs'] + 1
    kill, restart = controls
    calls, recovery = phase['primaryCalls'], phase['recovery']
    assert recovery['restoredAtMs'] <= kill['startedMs'] < kill['finishedMs'] <= recovery['releasedAtMs']
    snapshot = runtime['fallbackSnapshot']
    stage = stages[1]; observation = observations[stage['id']]
    assert snapshot['stageId'] == stage['id']
    assert snapshot['stageSha256'] == sha(compact(stage)) and snapshot['observationSha256'] == sha(compact(observation))
    assert decision_calls[stage['id']]['finishedMs'] < calls[2]['modelFinishedMs'] <= snapshot['recordedMs']
    assert snapshot['recordedMs'] <= restart['startedMs'] < restart['finishedMs'] <= snapshot['restartFinishedMs']
    assert snapshot['restartFinishedMs'] <= snapshot['thirdResponseReleasedMs'] <= calls[2]['finishedMs']


def verify_resources(plan, launcher, owned, elapsed):
    from scripts.lib.shadow_resource_sample import PLAN
    assert plan['resources'] == PLAN
    resources = launcher['resources']
    assert resources['schema'] == 'agat.shadow.resources.v1' and resources['errors'] == []
    assert set(resources['timebase']) == {'numer', 'denom'}
    assert all(type(value) is int and value > 0 for value in resources['timebase'].values())
    sampled = set(resources['ownedPids'])
    assert len(sampled) == len(resources['ownedPids']) and sampled <= owned
    rows = resources['samples']
    assert 10 <= len(rows) <= 1000
    assert [row['phase'] for row in rows[:3]] == ['before_models', 'after_shadow_warmup', 'after_ollama_warmup']
    assert [row['phase'] for row in rows[-2:]] == ['before_cleanup', 'after_cleanup']
    assert {row['phase'] for row in rows[3:-2]} == {'isolated', 'session'}
    assert rows[0]['groups'] == rows[-1]['groups'] == {}
    assert rows[0]['pressureDispatchLevel'] != 4
    last, all_sampled, previous = 0, set(), {}
    for row in rows:
        assert 'error' not in row and last < row['startedMs'] < row['finishedMs'] < elapsed
        last = row['finishedMs']
        groups = row['groups']; assert set(groups) <= {'shadow', 'ollama', 'workload'}
        joined = set()
        for name, values in groups.items():
            assert len(values) == len(set(values)) and not joined.intersection(values)
            joined.update(values)
            roots = {launcher['ollamaPid']} if name == 'ollama' else {r['pid'] for r in launcher['workloads']} if name == 'workload' else {
                r['pid'] for r in launcher['shadowRecovery']['runtimes']}
            assert not values or len(set(values) & roots) == 1
        assert joined <= sampled; all_sampled.update(joined)
        processes = row['processes']; ids = {process['pid'] for process in processes}
        exited = set(row['exitedDuringSample'])
        assert len(ids) == len(processes) and len(exited) == len(row['exitedDuringSample'])
        assert ids.isdisjoint(exited) and ids | exited == joined
        for process in processes:
            assert set(process) == {'pid', 'startTicks', 'userTicks', 'systemTicks', 'rssBytes', 'footprintBytes'}
            assert all(type(value) is int and value >= 0 for value in process.values())
            assert process['pid'] > 0 and process['startTicks'] > 0
            identity = process['pid'], process['startTicks']
            if identity in previous:
                assert all(process[key] >= previous[identity][key] for key in ('userTicks', 'systemTicks'))
            previous[identity] = process
        assert row['pressureDispatchLevel'] in (1, 2, 4)
        assert row['vm']['pageSizeBytes'] in (4096, 16384)
        assert set(row['vm']['pages']) == {'Pages free', 'Pages active', 'Pages inactive', 'Pages wired down', 'Pages occupied by compressor',
            'Pages stored in compressor', 'Pageins', 'Pageouts', 'Swapins', 'Swapouts', 'Compressions', 'Decompressions'}
        assert all(type(value) is int and value >= 0 for value in row['vm']['pages'].values())
        swap = row['swap']; assert set(swap) == {'totalBytes', 'usedBytes', 'freeBytes'}
        assert all(type(value) is int and value >= 0 for value in swap.values())
        assert abs(swap['totalBytes'] - swap['usedBytes'] - swap['freeBytes']) <= 20972
        assert max(swap['usedBytes'], swap['freeBytes']) <= swap['totalBytes']
    assert all_sampled == sampled
    return {'samples': len(rows), 'ownedSampledPids': len(sampled),
            'pressureDispatchLevels': sorted({row['pressureDispatchLevel'] for row in rows}),
            'swapUsedBytesRange': [min(row['swap']['usedBytes'] for row in rows), max(row['swap']['usedBytes'] for row in rows)],
            'maxObservedShadowRssBytes': max(sum(process['rssBytes'] for process in row['processes']
                if process['pid'] in row['groups'].get('shadow', [])) for row in rows),
            'measurement': 'sampled_not_peak', 'causalConclusion': 'not_established'}


def verify(directory):
    if not __debug__:
        raise RuntimeError('Assertions must be enabled')
    directory = directory.resolve()
    assert directory.is_relative_to(ROOT / 'docs')
    plan, launcher = [json.loads((directory / f'{name}.json').read_text()) for name in ('plan', 'launcher')]
    assert plan['schema'] in [f'agat.temporal.real-rag-plan.v{v}' for v in (1, 2, 3, 4)]
    version = int(plan['schema'][-1])
    shadow, runtime_recovery = version >= 2, version >= 3
    resource_sources = []
    if version == 4:
        from scripts.lib.shadow_resource_sample import SOURCE_PATHS
        resource_sources = SOURCE_PATHS
    assert plan['schema'] == f'agat.temporal.real-rag-plan.v{version}'
    assert re.fullmatch('[0-9a-f]{40}', plan['implementationCommit'])
    paths = [*SOURCES, *(launcher_module.SHADOW_SOURCES if shadow else []),
             *(launcher_module.SHADOW_RECOVERY_SOURCES if runtime_recovery else []), *resource_sources]
    if shadow and 'referenceFormat' in plan['decision']:
        reference = plan['decision']['referencePath']
        assert isinstance(reference, str) and reference.startswith('docs/')
        assert Path(reference).as_posix() == reference and '..' not in Path(reference).parts
        assert (ROOT / reference).resolve().is_relative_to(ROOT / 'docs')
        assert not (ROOT / reference).resolve().is_relative_to(ROOT / 'docs/private')
        paths.append(reference)
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
        assert decision['policyPath'] == launcher_module.SHADOW_POLICY
        profile = verify_shadow_profile(decision, sources)
        assert decision['warmupCalls'] == (3 if runtime_recovery else 1)
        assert decision['policy'] == json.loads(sources[decision['policyPath']])
        assert all(decision['manifest'][key] == profile['model'][key] for key in ['repository', 'revision', 'artifactSha256'])
        assert sha(json.dumps(decision['manifest']['files'], sort_keys=True, separators=(',', ':'))) == decision['manifest']['artifactSha256']
        assert decision['runtime']['packages'] == dict(line.split('==') for line in sources['decision_runtime/requirements-mlx.txt'].decode().splitlines()
                                                      if line and not line.startswith('#'))
    if runtime_recovery:
        assert plan['shadowRecovery'] == {'protocol': 'private-files-v1', 'signal': 'SIGKILL',
            'actions': ['isolated/kill', 'isolated/restart', 'session/kill', 'session/restart']}
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
        if runtime_recovery:
            verify_runtime_control(plan, launcher, profile, warmup_request, owned, elapsed)
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
    if runtime_recovery:
        assert set(launcher['shadowJournalSha256']) == {'isolated.shadow.jsonl', 'session.shadow.jsonl'}
    for transport in plan['transports']:
        phase_path = directory / f'{transport}.json'
        assert launcher['phaseSha256'][phase_path.name] == sha(phase_path.read_bytes())
        phase = json.loads(phase_path.read_text())
        if runtime_recovery:
            journal = directory / f'{transport}.shadow.jsonl'
            assert sha(journal.read_bytes()) == launcher['shadowJournalSha256'][journal.name]
            assert [json.loads(line) for line in journal.read_text().splitlines()] == phase['decisionCalls']
        assert phase['schema'] == f'agat.temporal.real-rag.v{version}' and phase['status'] == 'pass' and phase['failure'] is None
        assert phase['transport'] == transport and phase['planSha256'] == plan_sha and phase['qualification'] == 'not_assessed'
        assert set(phase['checks']) == {'activityRetried', 'temporalWorkerKilledAndReplaced', 'sameWorkflowRun',
            'firstAcceptedStageUnchanged', 'realModelOutputsPreserved', 'durableTimerFired', 'nativeReplayWithoutSideEffects',
            'shadowObservationsPreserved' if shadow else 'shadowDisabled', 'childrenDrained',
            *(['shadowRuntimeFailureAndRecovery'] if runtime_recovery else [])} and all(value is True for value in phase['checks'].values())
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
                assert calls[index]['finishedMs'] < call['startedMs'] < call['finishedMs'] < phase_ms
                if index < 2: assert call['finishedMs'] < calls[index + 1]['startedMs']
                if runtime_recovery and index == 1:
                    assert call['status'] == 'unavailable' and call['transportError'] == 'ECONNREFUSED'
                    assert 'httpStatus' not in call and 'result' not in call
                    assert observation == {'mode': 'shadow', 'fallback': 'primary', 'status': 'unavailable', 'reason': 'unreachable'}
                    continue
                assert call['httpStatus'] == 200
                validate_result(call['result'], request, profile)
                assert call['result']['status'] in ['ok', 'abstain']
                assert observation == {'mode': 'shadow', 'fallback': 'primary', 'status': call['result']['status'],
                                       'reason': call['result']['reason'], 'result': call['result']}
            assert decision_calls[stages[0]['id']]['finishedMs'] < recovery['heldResponseAtMs']
            assert decision_calls[stages[1]['id']]['startedMs'] > recovery['releasedAtMs']
            if runtime_recovery:
                verify_runtime_phase(phase, launcher, stages, observations, decision_calls)
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
            inferred = [row for row in phase['decisionCalls'] if 'result' in row]
            summaries[-1]['shadow'] = {'calls': 3, 'generatedTokens': 0,
                'outcomes': dict(Counter(f"{row['result']['status']}/{row['result']['reason']}" if 'result' in row else 'unavailable/unreachable'
                                         for row in phase['decisionCalls'])),
                'inputTokens': [row['result']['inputTokens'] for row in inferred],
                'runtimeMs': [row['result']['durationMs'] for row in inferred]}
            if runtime_recovery:
                summaries[-1]['shadow'].update(inferenceCalls=2, unavailableObservations=1)
                summaries[-1]['shadow']['restartMs'] = round(phase['runtimeRecovery']['controlCalls'][1]['finishedMs']
                                                            - phase['runtimeRecovery']['controlCalls'][1]['startedMs'], 3)
    assert prompt_sets[0] == prompt_sets[1] and output_sets[0] == output_sets[1]
    assert len(vectors) == 5 and all(len(values) == 1 for values in vectors.values())
    assert len({row['workflowRunId'] for row in summaries}) == 2
    return {'schema': f'agat.temporal.real-rag-verification.v{version}', 'status': 'pass', 'implementationCommit': plan['implementationCommit'],
            'sourceFiles': len(sources), 'phases': summaries, 'equalPromptsAndOutputs': True, 'equalVectorsForFiveInputs': True,
            'primaryCalls': 6, 'embeddingItems': 10, 'sourceCitations': 12, 'nativeReplay': 'checked_by_live_harness',
            'ownedObservedPids': len(owned), 'ownedContainers': 4, 'qualification': 'not_assessed',
            **({'shadowCalls': 6, 'profileSha256': plan['decision']['profile']['profileSha256']} if shadow else {}),
            **({'shadowInferenceCalls': 4, 'unavailableObservations': 2, 'separateWarmupCalls': 3, 'runtimeRestarts': 2}
               if runtime_recovery else {}),
            **({'resources': verify_resources(plan, launcher, owned, elapsed)} if version == 4 else {})}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = verify(args.directory)
    if args.output:
        assert args.output.resolve().is_relative_to(ROOT / 'docs')
        if result['schema'] == 'agat.temporal.real-rag-verification.v4':
            assert args.output.resolve().is_relative_to(ROOT / 'docs/private')
        with args.output.open('x') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False); stream.write('\n')
    print(compact(result))
