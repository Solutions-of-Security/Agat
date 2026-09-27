#!/usr/bin/env python3
"""Replay model/lease provenance and helper ownership in the full RAG profile."""
import argparse
from collections import Counter, defaultdict
import hashlib
import io
import json
import math
from pathlib import Path
import re
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = 'docs/qualification/local-decisions/performance/rag-workflow.fixture.json'
PROFILE = 'docs/qualification/local-decisions/calibration/evidence/2026-09-26-frozen-profile/runtime-profile.json'
PATHS = ['apps/coordinator/src', 'workers', 'package.json', 'package-lock.json', 'apps/coordinator/package.json',
         'scripts/profile-embedding-rag.py', 'scripts/embedding-rag-worker-probe.py', 'scripts/benchmark-embedding-rag.ts',
         'scripts/lib/decision-primary-workflow.ts', 'scripts/lib/decision-rag.ts', 'scripts/lib/decision-shadow-proxy.ts', FIXTURE, PROFILE]
MODELS = {'qwen3:8b': '500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41',
          'embeddinggemma:latest': '85462619ee721b466c5927d109d4cb765861907d5417b9109caebc4e614679f1'}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def load(path):
    return json.loads(path.read_text())


def digest(value):
    assert isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value)
    return value


def positive(value):
    assert type(value) in (int, float) and math.isfinite(value) and value > 0
    return value


def stats(values):
    values = sorted(values)
    return {'count': len(values), 'min': round(values[0], 3), 'p50': round(values[math.ceil(len(values) * .5) - 1], 3),
            'p95': round(values[math.ceil(len(values) * .95) - 1], 3), 'max': round(values[-1], 3)}


def peak(intervals):
    active = highest = 0
    for _at, change in sorted([(start, 1) for start, _end in intervals] + [(end, -1) for _start, end in intervals]):
        active += change
        assert active >= 0
        highest = max(highest, active)
    assert active == 0
    return highest


def verify(directory):
    if not __debug__:
        raise RuntimeError('Assertions must be enabled')
    directory = directory.resolve()
    assert directory.is_relative_to(ROOT / 'docs')
    plan, result, launcher = [load(directory / name) for name in ('plan.json', 'workflow.json', 'launcher.json')]
    assert plan['schema'] == 'agat.embedding.rag-plan.v1'
    assert re.fullmatch('[0-9a-f]{40}', plan['implementationCommit'])
    snapshot = subprocess.check_output(['git', 'archive', plan['implementationCommit'], '--', *PATHS], cwd=ROOT, timeout=15)
    with tarfile.open(fileobj=io.BytesIO(snapshot)) as archive:
        source = {item.name: archive.extractfile(item).read() for item in archive if item.isfile()}
    assert plan['sourceSha256'] == {name: sha(raw) for name, raw in source.items()}
    assert plan['fixturePath'] == FIXTURE and plan['profilePath'] == PROFILE
    assert plan['fixture'] == json.loads(source[FIXTURE])
    assert plan['models'] == MODELS
    assert plan['ollamaSettings'] == {'OLLAMA_NO_CLOUD': '1', 'OLLAMA_MAX_LOADED_MODELS': '2', 'OLLAMA_NUM_PARALLEL': '1',
                                     'OLLAMA_CONTEXT_LENGTH': '8192', 'OLLAMA_KEEP_ALIVE': '5m'}
    assert plan['metadataId'] == 'embedding_transport_rag' and plan['samplePeriodMs'] == 100 and plan['nodeBudgetSeconds'] == 660
    assert plan['host']['system'] == 'Darwin' and plan['host']['memoryBytes'] >= 24 * 1024**3
    blocks = [{'transport': mode, 'phase': {'id': f'{index}_{mode}', 'concurrency': 2, 'runs': 3, 'shadow': False}}
              for index, mode in enumerate(('isolated', 'session', 'session', 'isolated'))]
    assert plan['blocks'] == blocks
    assert result['schema'] == 'agat.embedding.rag-workflow.v1' and launcher['schema'] == 'agat.embedding.rag-launcher.v1'
    for item in (result, launcher):
        assert item['status'] == 'observed' and item['failure'] is None
        assert item['planSha256'] == sha((directory / 'plan.json').read_bytes())
    for item in (plan, result):
        assert item['qualification'] == 'not_assessed' and item['routingEnabled'] is False
    assert result['failureStep'] is None and 0 < result['elapsedMs'] <= 600_000
    assert result['elapsedMs'] <= launcher['elapsedMs'] <= 720_000
    assert launcher['workflowSha256'] == sha((directory / 'workflow.json').read_bytes())
    assert launcher['nodeExitCode'] == 0 and type(launcher['ollamaExitCode']) is int
    assert launcher['cleanupErrors'] == [] and launcher['remainingOwnedPids'] == []
    assert launcher['modelsBefore']['models'] == [] and launcher['modelsAfterUnload']['models'] == []
    owned = set(launcher['ownedPids'])
    assert len(owned) == len(launcher['ownedPids']) and all(type(pid) is int and pid > 0 for pid in owned)
    assert launcher['nodePid'] in owned and launcher['ollamaPid'] in owned
    loaded = launcher['loadedModelSamples']
    assert loaded and any({item['name'] for item in sample['models']} == set(MODELS) for sample in loaded)
    for sample in loaded:
        assert 0 < sample['elapsedMs'] < launcher['elapsedMs']
        assert len({item['name'] for item in sample['models']}) == len(sample['models'])
        for item in sample['models']:
            assert item['name'] in MODELS and item['digest'] == MODELS[item['name']]
            assert item['context_length'] == (8192 if item['name'] == 'qwen3:8b' else 2048)
            positive(item['size']);positive(item['size_vram'])
    assert set(launcher['probeSha256']) == {f"{block['phase']['id']}.worker.json" for block in blocks}
    for kind, name in (('primary', 'qwen3:8b'), ('embedding', 'embeddinggemma:latest')):
        identity = result[kind]
        assert identity['name'] == name and identity['digest'] == MODELS[name]
        assert identity['version'] == launcher['ollamaVersion']['version']
        digest(identity['descriptionSha256'])
    assert result['primary']['generation'] == {'think': False, 'options': {'temperature': .2, 'seed': 0, 'num_ctx': 8192, 'num_predict': 384}}
    assert result['embedding']['generation'] == {'truncate': False, 'keep_alive': '5m', 'options': {'num_ctx': 2048}}
    fixture = plan['fixture']
    sources = {item['id']: item for item in fixture['rag']['sources']}
    source_hashes = {identity: sha(item['content'].encode()) for identity, item in sources.items()}
    warm = result['warmup']
    assert 0 < warm['primaryWallMs'] < warm['totalMs'] < result['elapsedMs']
    positive(warm['inputTokens']);positive(warm['outputTokens']);digest(warm['outputSha256'])
    assert warm['embedding']['inputSha256'] == list(source_hashes.values())
    assert warm['embedding']['items'] == 2 and warm['embedding']['dimensions'] == 768
    assert len(warm['embedding']['vectorSha256']) == 2
    vectors_by_input = defaultdict(set)
    for key, vector in zip(warm['embedding']['inputSha256'], warm['embedding']['vectorSha256']):
        vectors_by_input[key].add(digest(vector))
    assert len(result['blocks']) == 4
    reports, prompt_sets, output_sets, first_prompts = [], [], [], []
    all_workers, all_helpers = set(), set()
    for expected, block in zip(blocks, result['blocks']):
        mode, workflow = block['transport'], block['workflow']
        assert mode == expected['transport'] and workflow['phase'] == expected['phase']
        assert workflow['status'] == 'observed' and workflow['failure'] is None
        assert workflow['scheduler'] == {'mode': 'sequential', 'globalMaxConcurrency': 2}
        assert workflow['decisionCalls'] == [] and workflow['shadowCallsOverlappingPrimaryHttp'] == 0
        assert set(workflow['checks']) == {'allWorkflowsCompleted', 'everyStageExecutedOnce', 'allPrimaryOutputsPreserved',
                                          'expectedShadowObservationCount', 'configuredConcurrencyObserved'}
        assert all(value is True for value in workflow['checks'].values())
        assert workflow['maxPrimaryRequestsInFlight'] == 2
        assert 1 <= workflow['maxEmbeddingRequestsInFlight'] <= 2
        elapsed = positive(workflow['elapsedMs'])
        rag = workflow['rag']
        assert rag['embeddingModel'] == 'embeddinggemma:latest' and rag['dimensions'] == 768
        assert len(rag['sources']) == 2 and 0 < rag['ingestionMs'] < elapsed
        assert {item['id'] for item in rag['sources']} == set(sources)
        for item in rag['sources']:
            assert item['documentSha256'] == item['chunkSha256'] == source_hashes[item['id']]
            assert item['sourceUri'] == sources[item['id']]['sourceUri'] and item['dimensions'] == 768
        assert len({item['chunkId'] for item in rag['sources']}) == 2
        embedding = workflow['embeddingCalls']
        assert 10 <= len(embedding) <= 11 and sum(call['items'] for call in embedding) == 11
        inputs, query_vectors = [], []
        for call in embedding:
            assert call['status'] == 'completed' and call['model'] == rag['embeddingModel'] and call['dimensions'] == 768
            assert 0 < call['startedMs'] < call['finishedMs'] < elapsed
            assert 0 <= call['nativeLoadMs'] <= call['nativeTotalMs'] < elapsed
            positive(call['inputTokens'])
            assert call['items'] == len(call['inputSha256']) == len(call['vectorSha256'])
            for key, vector in zip(call['inputSha256'], call['vectorSha256']):
                inputs.append(digest(key));vectors_by_input[key].add(digest(vector))
                if key not in source_hashes.values():
                    query_vectors.append(vector)
        assert Counter(inputs) & Counter(source_hashes.values()) == Counter(source_hashes.values())
        assert sum(key in source_hashes.values() for key in inputs) == 2 and len(query_vectors) == 9
        assert peak([(call['startedMs'], call['finishedMs']) for call in embedding]) == workflow['maxEmbeddingRequestsInFlight']
        primary = workflow['primaryCalls']
        assert len(primary) == 9
        for call in primary:
            assert call['status'] == 'completed' and call['doneReason'] == 'stop'
            assert 0 < call['startedMs'] < call['finishedMs'] < elapsed
            assert call['retrievedSourceIds'] == list(sources)
            assert call['outputSha256'] == sha(call['output'].encode())
            positive(call['inputTokens']);positive(call['outputTokens']);positive(call['nativeTotalMs'])
            digest(call['messagesSha256'])
        assert peak([(call['startedMs'], call['finishedMs']) for call in primary]) == 2
        first_prompts.append(primary[0]['messagesSha256'])
        prompt_sets.append(Counter(call['messagesSha256'] for call in primary))
        output_sets.append(Counter(call['outputSha256'] for call in primary))
        workflows = workflow['workflows']
        assert len(workflows) == 3 and len({run['runId'] for run in workflows}) == 3
        persisted_outputs, persisted_queries, cited, unknown = [], [], 0, 0
        stage_ids = set()
        for run in workflows:
            assert rag['ingestionMs'] <= run['startedMs'] < run['finishedMs'] < elapsed
            assert abs(run['wallMs'] - (run['finishedMs'] - run['startedMs'])) <= .0011
            stages = run['stages']
            assert [stage['nodeId'] for stage in stages] == [role['id'] for role in fixture['roles']]
            assert [stage['position'] for stage in stages] == sorted(set(stage['position'] for stage in stages))
            known = {}
            for stage in stages:
                assert stage['id'] not in stage_ids
                stage_ids.add(stage['id'])
                assert stage['metrics']['model'] == 'qwen3:8b' and stage['metrics']['modelCalls'] == 1
                assert stage['outputSha256'] == sha(stage['output'].encode()) and 'observation' not in stage
                persisted_outputs.append(stage['outputSha256'])
                retrieval = stage['retrieval']
                assert len(retrieval['queries']) == 1 and retrieval['queries'][0]['dimensions'] == 768
                persisted_queries.append(digest(retrieval['queries'][0]['vectorSha256']))
                hits = retrieval['hits']
                assert len(hits) == 2 and {hit['sourceId'] for hit in hits} == set(sources)
                assert len({hit['marker'] for hit in hits}) == 2
                for hit in hits:
                    assert re.fullmatch('K[0-9]+', hit['marker']) and hit['chunkSha256'] == source_hashes[hit['sourceId']]
                    assert type(hit['score']) in (float, int) and math.isfinite(hit['score'])
                    assert hit['marker'] not in known or known[hit['marker']] == hit
                    known[hit['marker']] = hit
                assert {hit['marker']: hit for hit in retrieval['knownCitations']} == known
                markers = list(dict.fromkeys(re.findall(r'\[(K[0-9]+)\]', stage['output'])))
                assert retrieval['outputMarkers'] == markers
                assert retrieval['unknownMarkers'] == [marker for marker in markers if marker not in known]
                source_ids = list(dict.fromkeys(known[marker]['sourceId'] for marker in markers if marker in known))
                assert retrieval['citedSourceIds'] == source_ids
                assert retrieval['bothSourcesCited'] is (set(source_ids) == set(sources))
                cited += retrieval['bothSourcesCited']
                unknown += len(retrieval['unknownMarkers'])
        assert Counter(persisted_queries) == Counter(query_vectors)
        assert Counter(persisted_outputs) == output_sets[-1]
        probe_name = expected['phase']['id'] + '.worker.json'
        probe_path = directory / probe_name
        assert block['probeSha256'] == launcher['probeSha256'][probe_name] == sha(probe_path.read_bytes())
        probe = load(probe_path)
        assert probe['schema'] == 'agat.embedding.rag-worker.v1' and probe['transport'] == mode and probe['concurrency'] == 2
        assert probe['python'] == plan['host']['python'] and probe['pid'] in owned and probe['pid'] not in all_workers
        all_workers.add(probe['pid'])
        assert probe['exitCode'] == 0 and probe['failure'] is None and probe['sampleErrors'] == []
        assert probe['activeCalls'] == 0 and probe['liveThreads'] == [] and probe['fdBefore'] == probe['fdAfter']
        assert type(probe['fdBefore']) is int and probe['fdBefore'] > 0
        calls = {call['startedNs']: call for call in probe['calls']}
        assert len(calls) == len(probe['calls']) == len(embedding)
        assert Counter(tuple(call['inputSha256']) for call in calls.values()) == Counter(tuple(call['inputSha256']) for call in embedding)
        for start, call in calls.items():
            assert probe['startedNs'] < start < call['finishedNs'] < probe['finishedNs']
            assert call['completed'] is True and call['items'] == len(call['inputSha256'])
        assert 1 <= peak([(start, call['finishedNs']) for start, call in calls.items()]) <= 2
        children = {child['pid']: child for child in probe['children']}
        assert len(children) == len(probe['children'])
        assert not (set(children) & (all_helpers | all_workers)) and set(children) <= owned
        all_helpers.update(children)
        for child in children.values():
            assert child['returncode'] == 0 and child['stdinClosed'] is True and child['stdoutClosed'] is True
            call = calls[child['callerStartedNs']]
            assert call['startedNs'] < child['startedNs'] < call['finishedNs']
        requests = probe['requests']
        assert len(requests) == len(calls) and Counter(row['callerStartedNs'] for row in requests) == Counter(calls.keys())
        per_child = defaultdict(list)
        deaths = {}
        for row in requests:
            call, child = calls[row['callerStartedNs']], children[row['pid']]
            assert call['startedNs'] < row['atNs'] <= call['finishedNs'] and child['startedNs'] < row['atNs']
            assert row['returncode'] == (0 if mode == 'isolated' else None)
            assert row['stdinClosed'] is (mode == 'isolated') and row['stdoutClosed'] is (mode == 'isolated')
            per_child[row['pid']].append((call['startedNs'], call['finishedNs']))
            if mode == 'isolated':
                deaths[row['pid']] = row['atNs']
        assert set(per_child) == set(children)
        assert all(peak(intervals) == 1 for intervals in per_child.values())
        if mode == 'isolated':
            assert len(children) == len(calls) and probe['retired'] == []
        else:
            assert 1 <= len(children) <= 2 and len(probe['retired']) == len(children)
            assert Counter(row['pid'] for row in probe['retired']) == Counter(children.keys())
            for row in probe['retired']:
                assert row['returncode'] == 0 and row['stdinClosed'] is True and row['stdoutClosed'] is True
                assert row['callerStartedNs'] is None and max(call['finishedNs'] for call in calls.values()) < row['atNs'] < probe['finishedNs']
                deaths[row['pid']] = row['atNs']
        assert peak([(child['startedNs'], deaths[pid]) for pid, child in children.items()]) <= 2
        samples = probe['samples']
        assert len(samples) >= 5
        assert [sample['atNs'] for sample in samples] == sorted(set(sample['atNs'] for sample in samples))
        for sample in samples:
            assert probe['startedNs'] < sample['atNs'] < probe['finishedNs']
            positive(sample['workerRssBytes'])
            for pid, memory in sample['helperRssBytes'].items():
                assert type(memory) is int and memory >= 0
                # ps is not atomic with process exit. The sample starts before
                # its timestamp; accept the bounded two-second observer window.
                assert int(pid) in children
                assert children[int(pid)]['startedNs'] < sample['atNs'] <= deaths[int(pid)] + 2_000_000_000
        assert any(sample['helperRssBytes'] for sample in samples)
        reports.append({'id': expected['phase']['id'], 'transport': mode, 'phaseWallMs': round(elapsed, 3),
                        'ingestionMs': rag['ingestionMs'], 'workflowsMs': stats([run['wallMs'] for run in workflows]),
                        'workerEmbeddingMs': stats([(call['finishedNs'] - call['startedNs']) / 1e6 for call in calls.values()]),
                        'proxyEmbeddingMs': stats([call['finishedMs'] - call['startedMs'] for call in embedding]),
                        'primaryHttpMs': stats([call['finishedMs'] - call['startedMs'] for call in primary]),
                        'workerRssMiB': stats([sample['workerRssBytes'] / 1024**2 for sample in samples]),
                        'helperTotalRssMiB': stats([sum(sample['helperRssBytes'].values()) / 1024**2 for sample in samples]),
                        'sampleCount': len(samples), 'helperCount': len(children), 'embeddingCalls': len(calls),
                        'embeddingItems': sum(call['items'] for call in calls.values()),
                        'bothSourcesCitedStages': cited, 'unknownCitationCount': unknown,
                        'inputTokens': sum(call['inputTokens'] for call in primary), 'outputTokens': sum(call['outputTokens'] for call in primary)})
    assert len(set(first_prompts)) == 1
    assert all(len(values) == 1 for values in vectors_by_input.values())
    comparison = {'firstPrimaryPromptsIdentical': True, 'allPromptMultisetsIdentical': all(values == prompt_sets[0] for values in prompt_sets),
                  'allOutputMultisetsIdentical': all(values == output_sets[0] for values in output_sets),
                  'uniqueEmbeddingInputs': len(vectors_by_input),
                  'inputsWithDifferingVectors': sum(len(values) > 1 for values in vectors_by_input.values())}
    return {'status': 'pass', 'blocks': reports, 'comparison': comparison, 'workers': len(all_workers),
            'helpers': len(all_helpers), 'sourceFiles': len(source), 'qualification': 'not_assessed', 'routingEnabled': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = verify(args.directory)
    if args.output:
        assert args.output.resolve().is_relative_to(ROOT / 'docs')
        with args.output.open('x') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))
