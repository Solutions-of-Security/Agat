#!/usr/bin/env python3
"""Replay model vectors, durable chunks and ownership after worker idle retirement."""
import argparse
from collections import defaultdict
import gzip
import importlib.util
import json
import math
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('worker_idle_replay_support', ROOT / 'scripts/verify-embedding-parent-guard.py')
support = importlib.util.module_from_spec(spec); spec.loader.exec_module(support)
SOURCE_ROOTS = sorted(support.SOURCES | {'apps/coordinator/src', 'workers', 'package.json', 'package-lock.json', 'apps/coordinator/package.json',
    'scripts/profile-embedding-worker-idle.py', 'scripts/benchmark-embedding-worker-idle.ts',
    'scripts/embedding-idle-worker-probe.py', 'scripts/verify-embedding-worker-idle.py'})


def verify(directory):
    if not __debug__:
        raise RuntimeError('Assertions must be enabled')
    directory = directory.resolve(); assert directory.is_relative_to(ROOT / 'docs')
    plan = json.loads((directory / 'plan.json').read_text()); result = json.loads((directory / 'result.json').read_text())
    assert plan['schema'] == 'agat.embedding.worker-idle-model.v1'
    assert plan['model'] == support.MODEL and plan['modelDigest'] == support.DIGEST and plan['dimensions'] == 768
    assert plan['concurrency'] == 1 and plan['callsPerBurst'] == 2 and plan['timeoutSeconds'] == 30 and plan['budgetSeconds'] == 240
    assert plan['maxComponentDifference'] == 1e-6 and plan['maxCosineDistance'] == 1e-10
    assert plan['platform'] == 'Darwin' and plan['machine'] == 'arm64' and plan['memoryBytes'] >= 24 * 1024**3
    assert plan['warmupInput'] == 'Проверка готовности локальной embedding модели.'
    endpoint = re.fullmatch(r'http://127\.0\.0\.1:([0-9]+)/v1', plan['endpoint']); assert endpoint and int(endpoint[1]) != 11434
    assert plan['ollamaSettings'] == {'OLLAMA_NO_CLOUD': '1', 'OLLAMA_MAX_LOADED_MODELS': '1', 'OLLAMA_NUM_PARALLEL': '1',
                                    'OLLAMA_CONTEXT_LENGTH': '2048', 'OLLAMA_KEEP_ALIVE': '5m'}
    commit = plan['implementationCommit']; assert re.fullmatch('[a-f0-9]{40}', commit)
    files = set(subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', commit, '--', *SOURCE_ROOTS], cwd=ROOT, text=True).splitlines())
    assert files == set(plan['sourceSha256']) and plan['sourceSha256'] == support.source_hashes(commit, files)
    expected = [{'id': f'b{batch}-{i}', 'batch': batch, 'idleTimeout': idle, 'idleSeconds': 4}
                for batch in (1, 32) for i, idle in enumerate((0, 3, 3, 0))]
    assert plan['phases'] == expected
    assert result['status'] == 'observed' and result['failure'] is None
    assert result['planSha256'] == support.sha((directory / 'plan.json').read_bytes())
    assert result['remainingOwnedPids'] == result['cleanupErrors'] == [] and result['modelsAfterUnload'] == {'models': []}
    assert result['ollamaExitCode'] in (0, -15) and result['nodeExitCode'] == 0
    assert result['ollamaPid'] in result['ownedPids'] and result['nodePid'] in result['ownedPids']
    assert 32 <= result['elapsedSeconds'] <= 290
    before, after = result['metadataBefore'], result['metadataAfter']
    assert before['version'] == after['version'] and before['model'] == after['model']
    assert before['model']['name'] == support.MODEL and before['model']['digest'] == support.DIGEST
    assert before['loaded'] == [] and len(after['loaded']) == 1
    assert after['loaded'][0]['name'] == support.MODEL and after['loaded'][0]['digest'] == support.DIGEST
    assert set(result['logSha256']) == {'ollama.log', 'worker.log'} and all(re.fullmatch('[a-f0-9]{64}', v) for v in result['logSha256'].values())
    assert result['progress'] == [f"phase {phase['id']}: {status}" for phase in expected for status in ('started', 'completed')]
    expected_files = {f"{phase['id']}.{kind}.json.gz" for phase in expected for kind in ('worker', 'store')}
    assert set(result['files']) == expected_files == {p.name for p in directory.glob('*.json.gz')}
    def read(name):
        path = directory / name; meta = result['files'][name]; data = path.read_bytes()
        assert len(data) == meta['bytes'] and support.sha(data) == meta['sha256']
        with gzip.open(path, 'rb') as stream: raw = stream.read(8 * 1024 * 1024 + 1)
        assert len(raw) == meta['decodedBytes'] <= 8 * 1024 * 1024
        return json.loads(raw)
    def vectors(values, count):
        assert len(values) == count and all(isinstance(v, list) and len(v) == 768 for v in values)
        for vector in values:
            assert all(type(x) in (int, float) and math.isfinite(x) and abs(x) <= 1_000_000 for x in vector)
            norm = math.fsum(x * x for x in vector); assert 0 < norm < math.inf
    assert result['warmup']['model'] == support.MODEL
    vectors(result['warmup']['embeddings'], 1)
    references, latency, memory = {}, defaultdict(list), []
    total = stored = helpers = exact = 0
    max_difference = max_cosine = 0.0
    for phase in expected:
        probe, saved = read(phase['id'] + '.worker.json.gz'), read(phase['id'] + '.store.json.gz')
        assert probe['schema'] == 'agat.embedding.idle-worker-probe.v1' and saved['schema'] == 'agat.embedding.idle-worker-store.v1'
        assert probe['exitCode'] == saved['workerExitCode'] == 0 and probe['failure'] is saved['failure'] is None
        assert saved['status'] == 'observed' and saved['workerSignal'] is None and saved['phase'] == phase
        assert probe['pid'] in result['ownedPids'] and probe['concurrency'] == 1 and probe['idleTimeout'] == phase['idleTimeout']
        assert probe['activeCalls'] == 0 and probe['liveThreads'] == probe['controlErrors'] == []
        assert probe['fdBefore'] == probe['fdAfter'] and probe['fdBefore'] > 0
        children = {item['pid']: item for item in probe['children']}; reaps = {item['pid']: item for item in probe['retired']}
        assert len(children) == len(probe['children']) == (2 if phase['idleTimeout'] else 1)
        assert set(children) == set(reaps) and len(reaps) == len(probe['retired'])
        assert set(children) <= set(result['ownedPids'])
        for child in [*children.values(), *reaps.values()]:
            assert child['returncode'] == 0 and child['stdinClosed'] is child['stdoutClosed'] is True
        helpers += len(children)
        shots = probe['snapshots']; assert set(shots) == {'before', 'afterFirst', 'afterIdle', 'afterSecond'}
        times = [probe['startedNs'], *(shots[key]['atNs'] for key in ('before', 'afterFirst', 'afterIdle', 'afterSecond')), probe['finishedNs']]
        assert all(a < b for a, b in zip(times, times[1:])) and shots['afterIdle']['atNs'] - shots['afterFirst']['atNs'] >= 4e9
        assert all(shot['workerRssBytes'] > 0 and all(value > 0 for value in shot['helperRssBytes'].values()) for shot in shots.values())
        calls = probe['calls']; assert len(calls) == 4 and len(saved['rounds']) == 2 and all(len(r) == 2 for r in saved['rounds'])
        assert all(a['finishedNs'] < b['startedNs'] for a, b in zip(calls, calls[1:]))
        assert len(probe['leases']) == 8 and len(probe['completions']) == 4
        pids = []
        document_ids, lease_ids = [], []
        for round_id, documents in enumerate(saved['rounds']):
            seen = set()
            pids.append({call['helperPid'] for call in calls[round_id * 2:round_id * 2 + 2]})
            assert len(pids[-1]) == 1
            for position, call in enumerate(calls[round_id * 2:round_id * 2 + 2]):
                index = round_id * 2 + position
                start_key, end_key = ('before', 'afterFirst') if round_id == 0 else ('afterIdle', 'afterSecond')
                assert shots[start_key]['atNs'] < call['startedNs'] < call['transportFinishedNs'] < call['finishedNs'] < shots[end_key]['atNs']
                assert call['model'] == support.MODEL
                child = children[call['helperPid']]
                assert child['bornNs'] < call['transportFinishedNs']
                creator = next(row for row in calls if row['startedNs'] == child['callerStartedNs'])
                assert creator['startedNs'] < child['bornNs'] < creator['transportFinishedNs']
                vectors(call['vectors'], phase['batch'])
                document = next(d for d in documents if [c['content'] for c in d['chunks']] == call['inputs'])
                item = documents.index(document); assert item not in seen; seen.add(item)
                content = (f'Учебный документ {item}. Статус заявки: выполнено. Сотрудник проверил источник.' if phase['batch'] == 1 else
                           ''.join(f'Учебная запись {item}-{i}. Статус: выполнено. '.ljust(400, 'я') for i in range(32)))
                doc = document['document']; document_ids.append(doc['id'])
                assert doc['content'] == content and doc['content_sha256'] == support.sha(content.encode()) and doc['status'] == 'ready'
                assert document['job'] == {'status': 'completed', 'failures': 0, 'lease_id': None}
                assert len(document['chunks']) == phase['batch']
                chunks = document['chunks']
                for ordinal, (chunk, vector) in enumerate(zip(chunks, call['vectors'], strict=True)):
                    assert chunk['ordinal'] == ordinal and chunk['embedding_model'] == support.MODEL and chunk['embedding_dimensions'] == 768
                    assert chunk['content'] == content[chunk['char_start']:chunk['char_end']]
                    assert chunk['char_start'] == ordinal * 400 and chunk['char_end'] == min((ordinal + 1) * 400, len(content))
                    assert chunk['content_sha256'] == support.sha(chunk['content'].encode())
                    assert json.loads(chunk['embedding_json']) == vector
                    stored += 1
                lease_start, lease_end = probe['leases'][index * 2:index * 2 + 2]
                assert lease_start['event'] == 'call' and lease_end['event'] == 'return'
                assert lease_start['leaseId'] == lease_end['leaseId']; lease_ids.append(lease_start['leaseId'])
                completion = probe['completions'][index]
                assert completion['path'] == f"/api/v1/workers/knowledge/leases/{lease_start['leaseId']}/complete" and completion['status'] == 200
                assert completion['body'] == {'embeddings': [{'chunkId': c['id'], 'embedding': v} for c, v in zip(chunks, call['vectors'], strict=True)]}
                assert lease_start['atNs'] < call['startedNs'] < call['finishedNs'] < completion['atNs'] < lease_end['atNs']
                original = references.setdefault((phase['batch'], item), call['vectors'])
                exact += original == call['vectors']; total += 1
                for vector, reference in zip(call['vectors'], original, strict=True):
                    difference = max(abs(a - b) for a, b in zip(vector, reference, strict=True))
                    norm = math.sqrt(math.fsum(x*x for x in vector) * math.fsum(x*x for x in reference))
                    assert math.isfinite(norm) and norm > 0
                    cosine = max(0.0, 1 - math.fsum(a*b for a,b in zip(vector, reference, strict=True)) / norm)
                    assert difference <= 1e-6 and cosine <= 1e-10
                    max_difference, max_cosine = max(max_difference, difference), max(max_cosine, cosine)
                latency[f"b{phase['batch']}-{'retire' if phase['idleTimeout'] else 'keep'}-r{round_id}-{'admission' if position == 0 else 'following'}"].append((call['finishedNs']-call['startedNs'])/1e6)
        assert len(set(document_ids)) == len(set(lease_ids)) == 4
        ready = [json.loads(e['data_json'])['documentId'] for e in saved['events'] if e['type'] == 'knowledge.document.ready']
        assert len(ready) == 4 and set(ready) == set(document_ids)
        assert not any(e['type'] in {'knowledge.embedding.retrying', 'knowledge.embedding.failed'} for e in saved['events'])
        assert (pids[0].isdisjoint(pids[1]) if phase['idleTimeout'] else pids[0] == pids[1])
        for key, live, reaped in [('before', set(), set()), ('afterFirst', pids[0], set()),
                                 ('afterIdle', set() if phase['idleTimeout'] else pids[0], pids[0] if phase['idleTimeout'] else set()),
                                 ('afterSecond', pids[1], pids[0] if phase['idleTimeout'] else set())]:
            assert set(shots[key]['helperRssBytes']) == {str(pid) for pid in live}
            assert set(shots[key]['reaped']) == reaped and len(shots[key]['reaped']) == len(reaped)
        for pid in pids[0]:
            if phase['idleTimeout']: assert shots['afterFirst']['atNs'] < reaps[pid]['atNs'] < shots['afterIdle']['atNs']
        memory.append({'phase': phase['id'], 'idleHelperRssBytes': sum(shots['afterIdle']['helperRssBytes'].values())})
    assert total == 32 and stored == 528 and helpers == 12 and len(references) == 4
    return {'status': 'verified', 'workerCalls': total, 'modelWarmups': 1, 'persistedVectors': stored, 'helpersReaped': helpers,
            'workersClosed': 8, 'exactReferenceMatches': exact, 'maxComponentDifference': max_difference, 'maxCosineDistance': max_cosine,
            'latencyMs': {k: support.summary(v) for k, v in latency.items()}, 'idleMemory': memory,
            'planSha256': result['planSha256'], 'resultSha256': support.sha((directory / 'result.json').read_bytes())}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('directory', type=Path)
    print(json.dumps(verify(parser.parse_args().directory), indent=2, allow_nan=False))
