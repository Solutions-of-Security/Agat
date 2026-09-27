#!/usr/bin/env python3
"""Independently replay before/after parent guard vectors, resources and ownership."""
import argparse
from collections import defaultdict
import gzip
import hashlib
import json
import io
import tarfile
import math
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
MODEL = 'embeddinggemma:latest'
DIGEST = '85462619ee721b466c5927d109d4cb765861907d5417b9109caebc4e614679f1'
BASELINE = '218ecb5f6b70289df0c89ebbc7275de57a202684'
SOURCES = {'scripts/profile-embedding-parent-guard.py', 'scripts/verify-embedding-parent-guard.py',
           'scripts/profile-embedding-model-transport.py', 'scripts/profile-embedding-transport.py',
           'scripts/profile-embedding-rag.py', 'workers/agat_worker.py', 'workers/embedding_http.py',
           'workers/embedding_transport.py', 'workers/telemetry.py', 'workers/web_tools.py', 'workers/local_decisions.py'}
TRANSPORTS = {'workers/embedding_http.py', 'workers/embedding_transport.py'}
COMMON = {'workers/agat_worker.py', 'workers/telemetry.py', 'workers/web_tools.py', 'workers/local_decisions.py'}


def source_hashes(commit, names):
    assert re.fullmatch('[0-9a-f]{40}', commit)
    raw = subprocess.check_output(['git', 'archive', commit, '--', *sorted(names)], cwd=ROOT, timeout=10)
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        return {entry.name: sha(archive.extractfile(entry).read()) for entry in archive if entry.isfile()}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def summary(values):
    ordered = sorted(values)
    return {'count': len(ordered), 'min': round(ordered[0], 3), 'p50': round(ordered[math.ceil(len(ordered) * .5) - 1], 3),
            'p95': round(ordered[math.ceil(len(ordered) * .95) - 1], 3), 'max': round(ordered[-1], 3)}


def verify(directory):
    if not __debug__:
        raise RuntimeError('Assertions must be enabled')
    directory = directory.resolve()
    assert directory.is_relative_to(ROOT / 'docs')
    plan = json.loads((directory / 'plan.json').read_text())
    result = json.loads((directory / 'result.json').read_text())
    assert result['status'] == 'observed' and result['failure'] is None
    assert 0 < result['elapsedSeconds'] <= 280 and result['planSha256'] == sha((directory / 'plan.json').read_bytes())
    assert (plan['schema'], plan['model'], plan['modelDigest'], plan['dimensions']) == (
        'agat.embedding.parent-guard.v1', MODEL, DIGEST, 768)
    endpoint = re.fullmatch(r'http://127\.0\.0\.1:([0-9]+)/v1', plan['endpoint'])
    assert endpoint and 1024 <= int(endpoint[1]) <= 65535 and int(endpoint[1]) != 11434
    assert plan['maxComponentDifference'] == 1e-6 and plan['maxCosineDistance'] == 1e-10
    assert plan['timeoutSeconds'] == 30 and plan['budgetSeconds'] == 240
    assert plan['platform'] == 'Darwin' and plan['machine'] == 'arm64' and plan['memoryBytes'] >= 24 * 1024**3
    assert plan['ollamaSettings'] == {'OLLAMA_NO_CLOUD': '1', 'OLLAMA_MAX_LOADED_MODELS': '1', 'OLLAMA_NUM_PARALLEL': '1',
                                      'OLLAMA_CONTEXT_LENGTH': '2048', 'OLLAMA_KEEP_ALIVE': '5m'}
    assert set(plan['sourceSha256']) == SOURCES
    assert plan['sourceSha256'] == source_hashes(plan['implementationCommit'], SOURCES)
    assert plan['baselineCommit'] == BASELINE and set(plan['baselineSha256']) == TRANSPORTS | COMMON
    assert plan['baselineSha256'] == source_hashes(BASELINE, TRANSPORTS | COMMON)
    assert all(plan['baselineSha256'][name] == plan['sourceSha256'][name] for name in COMMON)
    assert all(plan['baselineSha256'][name] != plan['sourceSha256'][name] for name in TRANSPORTS)
    assert result['cleanupErrors'] == result['remainingOwnedPids'] == []
    assert result['ollamaPid'] in result['ownedPids'] and result['ollamaExitCode'] in (0, -15)
    assert len(set(result['ownedPids'])) == len(result['ownedPids']) and all(type(pid) is int and pid > 0 for pid in result['ownedPids'])
    assert re.fullmatch('[0-9a-f]{64}', result['logSha256'])
    assert result['modelsAfterUnload'] == {'models': []}
    before, after = result['metadataBefore'], result['metadataAfter']
    assert before['version'] == after['version'] and before['model'] == after['model']
    assert before['model']['name'] == MODEL and before['model']['digest'] == DIGEST and before['model']['size'] > 0
    assert before['loaded'] == [] and len(after['loaded']) == 1
    assert after['loaded'][0]['name'] == MODEL and after['loaded'][0]['digest'] == DIGEST
    expected_cases = {}
    for batch in (1, 32):
        for case in range(4):
            expected_cases[f'b{batch}-{case}'] = [f'Учебный документ {case}-{index}. За неделю поступило {20 + case + index} обращений. '
                f'Сотрудник проверил источник и зарегистрировал результат. '
                f'Статус заявки: выполнено. Группа: поддержка. Период: сентябрь 2026.' for index in range(batch)]
    assert plan['cases'] == expected_cases
    expected = [(f'warm-b{b}-{m}-{g}', b, 1, m, g, 1, False) for b in (1, 32)
                for m in ('isolated', 'session') for g in (False, True)]
    expected += [(f'b{b}-c{c}-{m}-{i}', b, c, m, g, 8, True) for b in (1, 32) for c in (1, 4)
                 for m in ('isolated', 'session') for i, g in enumerate((False, True, True, False))]
    keys = ('id', 'batch', 'concurrency', 'mode', 'guard', 'calls', 'measured')
    assert all(type(item['guard']) is bool and type(item['measured']) is bool for item in plan['phases'])
    assert [tuple(item[key] for key in keys) for item in plan['phases']] == expected
    assert [phase['id'] for phase in result['phases']] == [item[0] for item in expected]
    blobs = {}
    for digest, metadata in result['vectorFiles'].items():
        assert re.fullmatch('[0-9a-f]{64}', digest)
        path = directory / 'vectors' / f'{digest}.json.gz'
        data = path.read_bytes()
        assert sha(data) == metadata['sha256'] and len(data) == metadata['bytes']
        # Each supported batch fits the existing 8 MiB response body budget.
        with gzip.open(path, 'rb') as stream:
            raw = stream.read(8 * 1024 * 1024 + 1)
        assert len(raw) <= 8 * 1024 * 1024 and len(raw) == metadata['decodedBytes'] and sha(raw) == digest
        blobs[digest] = json.loads(raw)
    assert {path.name for path in (directory / 'vectors').iterdir()} == {f'{digest}.json.gz' for digest in blobs}
    references = {}
    referenced = set()
    observed = defaultdict(list)
    warmup = {}
    concurrency = {}
    readiness = defaultdict(list)
    blocks = {}
    idle_memory = []
    exact = total = helpers = 0
    max_component = max_cosine = 0.0
    for item, phase in zip(plan['phases'], result['phases'], strict=True):
        assert [row['index'] for row in phase['rows']] == list(range(item['calls']))
        c = item['concurrency']
        reusable = item['mode'] == 'session'
        children = {child['pid']: child for child in phase['children']}
        assert len(children) == len(phase['children']) == (c if reusable else item['calls'])
        helpers += len(children)
        script = 'workers/embedding_transport.py' if reusable else 'workers/embedding_http.py'
        source = plan['sourceSha256' if item['guard'] else 'baselineSha256'][script]
        arguments = (['--serve'] if reusable else []) + (['--parent-pid', str(phase['before']['parentPid'])] if item['guard'] else [])
        assert all(child['arguments'] == arguments and child['scriptSha256'] == source for child in children.values())
        assert set(children) <= set(result['ownedPids'])
        assert all(type(pid) is int and pid > 0 and child['returncode'] == 0
                   and child['stdinClosed'] is True and child['stdoutClosed'] is True for pid, child in children.items())
        assert {child['owner'] for child in children.values()} == (
            {f'actor-{actor}' for actor in range(c)} if reusable else {f"{item['id']}-{i}" for i in range(item['calls'])})
        assert [warm['actor'] for warm in phase['readiness']] == (list(range(c)) if reusable else [])
        warm_by_pid = {}
        for warm in phase['readiness']:
            assert 0 < warm['startedNs'] < warm['finishedNs'] < phase['ready']['atNs']
            assert children[warm['pid']]['owner'] == f"actor-{warm['actor']}"
            warm_by_pid[warm['pid']] = warm
            readiness[str(item['guard'])].append((warm['finishedNs'] - warm['startedNs']) / 1e6)
        snapshots = [phase[key] for key in ('before', 'ready', 'afterCalls', 'afterClose')]
        assert 0 < snapshots[0]['atNs'] < snapshots[1]['atNs'] < snapshots[2]['atNs'] < snapshots[3]['atNs']
        assert len({shot['parentPid'] for shot in snapshots}) == 1
        assert snapshots[0]['descriptors'] == snapshots[3]['descriptors']
        assert snapshots[0]['helperRssBytes'] == snapshots[3]['helperRssBytes'] == {}
        for shot in snapshots:
            assert type(shot['parentRssBytes']) is int and shot['parentRssBytes'] > 0 and shot['descriptors'] > 0
            assert set(shot['helperRssBytes']) <= {str(pid) for pid in children}
            assert all(type(value) is int and value > 0 for value in shot['helperRssBytes'].values())
        for shot in snapshots[1:3]:
            assert set(shot['helperRssBytes']) == ({str(pid) for pid in children} if reusable else set())
            assert shot['descriptors'] == snapshots[0]['descriptors'] + (2 * c if reusable else 0)
        idle_memory.append({'phase': item['id'], 'mode': item['mode'], 'guard': item['guard'], 'helperReadyBytes': sum(phase['ready']['helperRssBytes'].values()),
                            'helperAfterCallsBytes': sum(phase['afterCalls']['helperRssBytes'].values()),
                            'parentAfterCallsBytes': phase['afterCalls']['parentRssBytes']})
        actor_last = {}
        intervals = []
        for row in phase['rows']:
            assert row['id'] == f"{item['id']}-{row['index']}"
            assert row['case'] == row['index'] % 4 and row['actor'] == row['index'] % c
            assert phase['ready']['atNs'] < row['startedNs'] < row['finishedNs'] < phase['afterCalls']['atNs']
            assert row['startedNs'] >= actor_last.get(row['actor'], 0)
            actor_last[row['actor']] = row['finishedNs']
            assert children[row['helperPid']]['owner'] == (f"actor-{row['actor']}" if reusable else row['id'])
            if reusable:
                assert warm_by_pid[row['helperPid']]['finishedNs'] < row['startedNs']
            payload = {'model': MODEL, 'input': expected_cases[f"b{item['batch']}-{row['case']}"]}
            assert row['inputSha256'] == sha(json.dumps(payload, ensure_ascii=False).encode())
            vectors = blobs[row['vectorSha256']]
            referenced.add(row['vectorSha256'])
            assert len(vectors) == item['batch'] and all(len(vector) == 768 for vector in vectors)
            for vector in vectors:
                assert all(type(value) in (int, float) and math.isfinite(value) for value in vector)
                assert any(value != 0 for value in vector)
            key = f"b{item['batch']}-{row['case']}"
            reference_sha, reference = references.setdefault(key, (row['vectorSha256'], vectors))
            exact += row['vectorSha256'] == reference_sha
            for vector, original in zip(vectors, reference, strict=True):
                difference = max(abs(a - b) for a, b in zip(vector, original, strict=True))
                dot = math.fsum(a * b for a, b in zip(vector, original, strict=True))
                norm = math.sqrt(math.fsum(a * a for a in vector) * math.fsum(b * b for b in original))
                assert norm > 0 and math.isfinite(norm)
                distance = max(0.0, 1 - dot / norm)
                assert difference <= plan['maxComponentDifference'] and distance <= plan['maxCosineDistance']
                max_component = max(max_component, difference)
                max_cosine = max(max_cosine, distance)
            intervals.extend([(row['startedNs'], 1), (row['finishedNs'], -1)])
            wall = (row['finishedNs'] - row['startedNs']) / 1e6
            if item['measured']:
                observed[f"b{item['batch']}-c{item['concurrency']}-{item['mode']}-{item['guard']}"].append(wall)
            else:
                warmup[row['id']] = round(wall, 3)
            total += 1
        active = peak = 0
        for _, change in sorted(intervals, key=lambda entry: (entry[0], entry[1])):
            active += change
            assert active >= 0
            peak = max(peak, active)
        assert active == 0 and peak == item['concurrency']
        concurrency[item['id']] = peak
        blocks[item['id']] = summary([(row['finishedNs'] - row['startedNs']) / 1e6 for row in phase['rows']])
    assert referenced == set(blobs) and len(references) == 8
    assert total == 264 and helpers == 176 and sum(map(len, readiness.values())) == 44
    return {'status': 'verified', 'calls': total, 'measuredCalls': sum(map(len, observed.values())), 'helpersReaped': helpers,
            'uniqueVectorBlobs': len(blobs), 'exactReferenceMatches': exact, 'maxComponentDifference': max_component,
            'maxCosineDistance': max_cosine, 'latencyMs': {key: summary(values) for key, values in observed.items()},
            'modelWarmupMs': warmup, 'readinessMs': {key: summary(values) for key, values in readiness.items()}, 'blocksMs': blocks, 'observedConcurrency': concurrency, 'idleMemory': idle_memory,
            'planSha256': result['planSha256'], 'resultSha256': sha((directory / 'result.json').read_bytes())}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.directory), indent=2, allow_nan=False))
