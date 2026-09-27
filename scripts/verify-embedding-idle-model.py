#!/usr/bin/env python3
"""Replay full vectors, cold admission, idle retirement and per-helper ownership."""
import argparse
from collections import defaultdict
import gzip
import importlib.util
import json
import math
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('idle_model_replay_support', ROOT / 'scripts/verify-embedding-parent-guard.py')
support = importlib.util.module_from_spec(spec)
spec.loader.exec_module(support)
SOURCES = support.SOURCES | {'scripts/lib/embedding_idle_pool.py', 'scripts/profile-embedding-idle-model.py',
                             'scripts/verify-embedding-idle-model.py'}


def verify(directory):
    if not __debug__:
        raise RuntimeError('Assertions must be enabled')
    directory = directory.resolve()
    assert directory.is_relative_to(ROOT / 'docs')
    plan = json.loads((directory / 'plan.json').read_text())
    result = json.loads((directory / 'result.json').read_text())
    assert result['status'] == 'observed' and result['failure'] is None
    assert result['planSha256'] == support.sha((directory / 'plan.json').read_bytes())
    assert plan['schema'] == 'agat.embedding.idle-model.v1' and plan['model'] == support.MODEL and plan['modelDigest'] == support.DIGEST
    assert plan['dimensions'] == 768 and plan['timeoutSeconds'] == 30 and plan['budgetSeconds'] == 240
    assert plan['platform'] == 'Darwin' and plan['machine'] == 'arm64' and plan['memoryBytes'] >= 24 * 1024**3
    assert 64 <= result['elapsedSeconds'] <= 280
    endpoint = re.fullmatch(r'http://127\.0\.0\.1:([0-9]+)/v1', plan['endpoint'])
    assert endpoint and 1024 <= int(endpoint[1]) <= 65535 and int(endpoint[1]) != 11434
    assert plan['maxComponentDifference'] == 1e-6 and plan['maxCosineDistance'] == 1e-10
    assert plan['ollamaSettings'] == {'OLLAMA_NO_CLOUD': '1', 'OLLAMA_MAX_LOADED_MODELS': '1', 'OLLAMA_NUM_PARALLEL': '1',
                                      'OLLAMA_CONTEXT_LENGTH': '2048', 'OLLAMA_KEEP_ALIVE': '5m'}
    assert set(plan['sourceSha256']) == SOURCES and plan['sourceSha256'] == support.source_hashes(plan['implementationCommit'], SOURCES)
    assert result['remainingOwnedPids'] == result['cleanupErrors'] == [] and result['modelsAfterUnload'] == {'models': []}
    assert result['ollamaPid'] in result['ownedPids'] and result['ollamaExitCode'] in (0, -15)
    assert re.fullmatch('[0-9a-f]{64}', result['logSha256'])
    before, after = result['metadataBefore'], result['metadataAfter']
    assert before['version'] == after['version'] and before['model'] == after['model']
    assert before['model']['name'] == support.MODEL and before['model']['digest'] == support.DIGEST and before['model']['size'] > 0
    assert before['loaded'] == [] and len(after['loaded']) == 1
    assert after['loaded'][0]['name'] == support.MODEL and after['loaded'][0]['digest'] == support.DIGEST
    expected_cases = {f'b{b}-{case}': [f'Учебный документ {case}-{index}. За неделю поступило {20 + case + index} обращений. '
        f'Сотрудник проверил источник и зарегистрировал результат. '
        f'Статус заявки: выполнено. Группа: поддержка. Период: сентябрь 2026.' for index in range(b)]
        for b in (1, 32) for case in range(4)}
    assert plan['cases'] == expected_cases
    expected = [{'id': f'b{b}-c{c}-{i}', 'batch': b, 'concurrency': c, 'mode': mode, 'callsPerBurst': 8,
                 'idleTimeoutSeconds': 3, 'idleSeconds': 4} for b in (1, 32) for c in (1, 4)
                for i, mode in enumerate(('keep', 'retire', 'retire', 'keep'))]
    warmups = [{'id': f'warm-b{b}', 'batch': b, 'concurrency': 1, 'mode': 'isolated', 'calls': 1} for b in (1, 32)]
    assert plan['phases'] == expected and plan['warmups'] == warmups
    assert [phase['id'] for phase in result['phases']] == [item['id'] for item in expected]
    assert [phase['id'] for phase in result['warmups']] == [item['id'] for item in warmups]
    blobs = {}
    for digest, metadata in result['vectorFiles'].items():
        assert re.fullmatch('[0-9a-f]{64}', digest)
        path = directory / 'vectors' / f'{digest}.json.gz'
        data = path.read_bytes()
        assert support.sha(data) == metadata['sha256'] and len(data) == metadata['bytes']
        with gzip.open(path, 'rb') as stream:
            raw = stream.read(8 * 1024 * 1024 + 1)
        assert len(raw) <= 8 * 1024 * 1024 and len(raw) == metadata['decodedBytes'] and support.sha(raw) == digest
        blobs[digest] = json.loads(raw)
    assert {path.name for path in (directory / 'vectors').iterdir()} == {f'{digest}.json.gz' for digest in blobs}
    references, referenced, exact, total = {}, set(), 0, 0
    max_component = max_cosine = 0.0

    def vector(row, batch, case):
        nonlocal exact, total, max_component, max_cosine
        payload = {'model': support.MODEL, 'input': expected_cases[f'b{batch}-{case}']}
        assert row['inputSha256'] == support.sha(json.dumps(payload, ensure_ascii=False).encode())
        digest = row['vectorSha256']
        values = blobs[digest]
        referenced.add(digest)
        assert len(values) == batch and all(len(v) == 768 for v in values)
        assert all(all(type(x) in (int, float) and math.isfinite(x) for x in v) and any(x != 0 for x in v) for v in values)
        original_sha, originals = references.setdefault(f'b{batch}-{case}', (digest, values))
        exact += digest == original_sha
        total += 1
        for v, reference in zip(values, originals, strict=True):
            difference = max(abs(a - b) for a, b in zip(v, reference, strict=True))
            dot = math.fsum(a * b for a, b in zip(v, reference, strict=True))
            norm = math.sqrt(math.fsum(x * x for x in v) * math.fsum(x * x for x in reference))
            distance = max(0.0, 1 - dot / norm)
            assert difference <= 1e-6 and distance <= 1e-10
            max_component, max_cosine = max(max_component, difference), max(max_cosine, distance)

    def children(phase, script, count):
        found = {row['pid']: row for row in phase['children']}
        assert len(found) == len(phase['children']) == count and set(found) <= set(result['ownedPids'])
        for pid, row in found.items():
            assert type(pid) is int and pid > 0 and row['returncode'] == 0
            assert row['stdinClosed'] is True and row['stdoutClosed'] is True
            assert row['scriptSha256'] == plan['sourceSha256'][script]
            assert row['arguments'] == (['--serve'] if script.endswith('embedding_transport.py') else []) + ['--parent-pid', str(phase['before']['parentPid'])]
        return found

    def snapshots(phase, keys):
        shots = [phase[key] for key in keys]
        assert 0 < shots[0]['atNs'] and all(a['atNs'] < b['atNs'] for a, b in zip(shots, shots[1:]))
        assert len({shot['parentPid'] for shot in shots}) == 1
        assert shots[0]['descriptors'] == shots[-1]['descriptors'] and shots[0]['helperRssBytes'] == shots[-1]['helperRssBytes'] == {}
        for shot in shots:
            assert type(shot['parentRssBytes']) is int and shot['parentRssBytes'] > 0
            assert type(shot['descriptors']) is int and shot['descriptors'] > 0
            assert all(type(value) is int and value > 0 for value in shot['helperRssBytes'].values())
        return shots

    helpers, observed, cold, memory, warm_results, burst_results = 0, defaultdict(list), [], [], {}, []
    for item, phase in zip(warmups, result['warmups'], strict=True):
        owned = children(phase, 'workers/embedding_http.py', 1)
        shots = snapshots(phase, ('before', 'ready', 'afterCalls', 'afterClose'))
        assert phase['readiness'] == [] and all(shot['helperRssBytes'] == {} and shot['descriptors'] == shots[0]['descriptors'] for shot in shots)
        assert len(phase['rows']) == 1
        row = phase['rows'][0]
        assert row['id'] == item['id'] + '-0' and row['index'] == row['case'] == row['actor'] == 0
        assert shots[1]['atNs'] < row['startedNs'] < row['finishedNs'] < shots[2]['atNs']
        assert owned[row['helperPid']]['owner'] == row['id']
        vector(row, item['batch'], 0)
        warm_results[item['id']] = round((row['finishedNs'] - row['startedNs']) / 1e6, 3)
        helpers += 1
    for item, phase in zip(expected, result['phases'], strict=True):
        c, retire = item['concurrency'], item['mode'] == 'retire'
        owned = children(phase, 'workers/embedding_transport.py', c * (2 if retire else 1))
        helpers += len(owned)
        assert phase['maintenanceFailure'] is None and phase['remainingThreads'] == [] and phase['idleReaps'] == (c if retire else 0)
        shots = snapshots(phase, ('before', 'afterFirst', 'afterIdle', 'afterSecond', 'afterClose'))
        assert (shots[2]['atNs'] - shots[1]['atNs']) / 1e9 >= 4
        assert len(phase['rows']) == 16
        by_id = {row['id']: row for row in phase['rows']}
        assert len(by_id) == 16
        expected_owners = {f"{item['id']}-r{r}-{i}" for r in ((0, 1) if retire else (0,)) for i in range(c)}
        assert {child['owner'] for child in owned.values()} == expected_owners
        pid_rounds = []
        transport_intervals = defaultdict(list)
        for round_id in (0, 1):
            rows = [row for row in phase['rows'] if row['round'] == round_id]
            assert [row['index'] for row in rows] == list(range(8))
            pids = {row['helperPid'] for row in rows}
            assert len(pids) == c and pids <= set(owned)
            pid_rounds.append(pids)
            start_shot, finish_shot = (shots[0], shots[1]) if round_id == 0 else (shots[2], shots[3])
            intervals = []
            for row in rows:
                assert row['id'] == f"{item['id']}-r{round_id}-{row['index']}"
                assert row['actor'] == row['index'] % c and row['case'] == row['index'] % 4
                assert start_shot['atNs'] < row['startedNs'] < row['transportStartedNs'] < row['transportFinishedNs'] < row['finishedNs'] < finish_shot['atNs']
                child = owned[row['helperPid']]
                creator = by_id[child['owner']]
                assert creator['transportStartedNs'] < child['bornNs'] < creator['transportFinishedNs']
                assert child['bornNs'] < row['transportFinishedNs']
                vector(row, item['batch'], row['case'])
                duration = (row['finishedNs'] - row['startedNs']) / 1e6
                position = 'admission' if row['index'] < c else 'following'
                observed[f"b{item['batch']}-c{c}-{item['mode']}-r{round_id}-{position}"].append(duration)
                if child['owner'] == row['id']:
                    cold.append({'id': row['id'], 'ms': round(duration, 3)})
                transport_intervals[row['helperPid']].append((row['transportStartedNs'], row['transportFinishedNs']))
                intervals.extend([(row['startedNs'], 1), (row['finishedNs'], -1)])
            active = peak = 0
            for _, change in sorted(intervals):
                active += change
                assert active >= 0
                peak = max(peak, active)
            assert active == 0 and peak == c
            burst_results.append({'phase': item['id'], 'round': round_id,
                                  'wallMs': round((max(row['finishedNs'] for row in rows) - min(row['startedNs'] for row in rows)) / 1e6, 3)})
        assert (pid_rounds[0].isdisjoint(pid_rounds[1]) if retire else pid_rounds[0] == pid_rounds[1])
        for intervals in transport_intervals.values():
            ordered = sorted(intervals)
            assert all(before[1] < after[0] for before, after in zip(ordered, ordered[1:]))
        live_sets = [set(), pid_rounds[0], set() if retire else pid_rounds[0], pid_rounds[1], set()]
        reaped_sets = [set(), set(), pid_rounds[0] if retire else set(), pid_rounds[0] if retire else set(), set(owned)]
        for shot, live, reaped in zip(shots, live_sets, reaped_sets, strict=True):
            assert set(shot['helperRssBytes']) == {str(pid) for pid in live}
            assert set(shot['reapedPids']) == reaped and len(shot['reapedPids']) == len(reaped)
            assert shot['descriptors'] == shots[0]['descriptors'] + 2 * len(live)
        memory.append({'phase': item['id'], 'mode': item['mode'],
                       **{key: sum(phase[key]['helperRssBytes'].values()) for key in ('afterFirst', 'afterIdle', 'afterSecond')}})
    assert total == 258 and helpers == 62 and len(cold) == 60
    assert len(references) == 8 and referenced == set(blobs)
    return {'status': 'verified', 'calls': total, 'measuredCalls': total - 2, 'helpersReaped': helpers,
            'exactReferenceMatches': exact, 'uniqueVectorBlobs': len(blobs), 'maxComponentDifference': max_component,
            'maxCosineDistance': max_cosine, 'latencyMs': {key: support.summary(values) for key, values in observed.items()},
            'modelWarmupMs': warm_results, 'coldAdmissions': cold, 'bursts': burst_results, 'helperMemory': memory,
            'planSha256': result['planSha256'], 'resultSha256': support.sha((directory / 'result.json').read_bytes())}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    print(json.dumps(verify(parser.parse_args().directory), indent=2, allow_nan=False))
