#!/usr/bin/env python3
"""Independent reconstruction of vectors, helper ownership and paired timing."""
import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {'scripts/profile-embedding-session.py', 'scripts/verify-embedding-session.py',
           'scripts/lib/embedding_session.py', 'scripts/profile-embedding-transport.py',
           'workers/agat_worker.py', 'workers/embedding_http.py', 'workers/telemetry.py',
           'workers/web_tools.py', 'workers/local_decisions.py'}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return json.dumps(value, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def stats(values):
    values = sorted(values)
    return {key: round(value, 3) for key, value in {
        'min': values[0], 'p50': values[math.ceil(.5 * len(values)) - 1],
        'p95': values[math.ceil(.95 * len(values)) - 1], 'max': values[-1]}.items()} | {'count': len(values)}


def verify(directory):
    if not __debug__:
        raise RuntimeError('Assertions must be enabled')
    directory = directory.resolve()
    assert directory.is_relative_to(ROOT / 'docs')
    plan = json.loads((directory / 'plan.json').read_text())
    result = json.loads((directory / 'result.json').read_text())
    assert plan['schema'] == 'agat.embedding.session.v1'
    assert result['status'] == 'observed' and result['failure'] is None and result['fixtureErrors'] == []
    assert 0 < result['elapsedSeconds'] <= 150
    assert result['planSha256'] == sha((directory / 'plan.json').read_bytes())
    assert (plan['timeoutSeconds'], plan['sessionRequestBytes'], plan['responseBytes']) == (5, 2097152, 8388608)
    assert re.fullmatch('[0-9a-f]{40}', plan['implementationCommit'])
    assert set(plan['sourceSha256']) == SOURCES
    for name, digest in plan['sourceSha256'].items():
        assert sha(subprocess.check_output(['git', 'show', f"{plan['implementationCommit']}:{name}"], cwd=ROOT, timeout=10)) == digest
    expected = [{'id': f'd{d}-b{b}-c{c}-{i}', 'dimensions': d, 'batch': b, 'concurrency': c, 'mode': mode, 'calls': 8}
                for d in (768, 4096) for b in (1, 32) for c in (1, 4)
                for i, mode in enumerate(('isolated', 'session', 'session', 'isolated'))]
    assert plan['phases'] == expected
    assert [phase['id'] for phase in result['phases']] == [item['id'] for item in expected]
    requests = {row['id']: row for row in result['requests']}
    assert len(requests) == len(result['requests']) == 256
    seen_requests = set()
    grouped = defaultdict(list)
    warmups = []
    concurrency = {}
    memory = []
    helper_count = 0
    for item, phase in zip(expected, result['phases'], strict=True):
        b, d, c = item['batch'], item['dimensions'], item['concurrency']
        reusable = item['mode'] == 'session'
        vectors = [[((component % 19) + index + 1) / 37 for component in range(d)] for index in range(b)]
        vector_sha = sha(encoded(vectors))
        response = encoded({'data': [{'index': i, 'embedding': vectors[i]} for i in reversed(range(b))]})
        children = {child['pid']: child for child in phase['children']}
        assert len(children) == len(phase['children']) == (c if reusable else 8)
        helper_count += len(children)
        assert all(type(pid) is int and pid > 0 and child['returncode'] == 0
                   and child['stdinClosed'] is True and child['stdoutClosed'] is True for pid, child in children.items())
        assert {child['owner'] for child in children.values()} == (
            {f'actor-{actor}' for actor in range(c)} if reusable else {f"{item['id']}-{i}" for i in range(8)})
        assert [row['actor'] for row in phase['warmup']] == (list(range(c)) if reusable else [])
        warm_by_pid = {}
        for warm in phase['warmup']:
            assert 0 < warm['startedNs'] < warm['finishedNs'] < phase['ready']['atNs']
            assert children[warm['pid']]['owner'] == f"actor-{warm['actor']}"
            warm_by_pid[warm['pid']] = warm
            warmups.append((warm['finishedNs'] - warm['startedNs']) / 1e6)
        snapshots = [phase[name] for name in ('before', 'ready', 'afterCalls', 'afterClose')]
        assert len({shot['parentPid'] for shot in snapshots}) == 1
        assert 0 < snapshots[0]['atNs'] < snapshots[1]['atNs'] < snapshots[2]['atNs'] < snapshots[3]['atNs']
        assert snapshots[0]['descriptors'] == snapshots[3]['descriptors']
        assert snapshots[0]['helperRssBytes'] == snapshots[3]['helperRssBytes'] == {}
        for shot in snapshots:
            assert type(shot['parentRssBytes']) is int and shot['parentRssBytes'] > 0 and shot['descriptors'] > 0
            assert set(shot['helperRssBytes']) <= {str(pid) for pid in children}
            assert all(type(value) is int and value > 0 for value in shot['helperRssBytes'].values())
        for shot in snapshots[1:3]:
            assert set(shot['helperRssBytes']) == ({str(pid) for pid in children} if reusable else set())
            assert shot['descriptors'] == snapshots[0]['descriptors'] + (2 * c if reusable else 0)
        memory.append({'phase': item['id'], 'helperReadyBytes': sum(phase['ready']['helperRssBytes'].values()),
                       'helperAfterCallsBytes': sum(phase['afterCalls']['helperRssBytes'].values()),
                       'parentAfterCallsBytes': phase['afterCalls']['parentRssBytes']})
        assert [row['index'] for row in phase['rows']] == list(range(8))
        intervals = []
        actor_last = {}
        for row in phase['rows']:
            index = row['index']
            identity = f"{item['id']}-{index}"
            assert row['id'] == identity and row['actor'] == index % c
            assert phase['ready']['atNs'] < row['startedNs'] < row['finishedNs'] < phase['afterCalls']['atNs']
            assert row['startedNs'] >= actor_last.get(row['actor'], 0)
            actor_last[row['actor']] = row['finishedNs']
            assert row['vectorSha256'] == vector_sha
            payload = {'model': f'synthetic-{d}', 'input': [f'{identity}/success/{i}' for i in range(b)]}
            request_sha = sha(json.dumps(payload, ensure_ascii=False).encode())
            server = requests[identity]
            assert row['inputSha256'] == server['inputSha256'] == request_sha
            assert server['kind'] == 'success' and server['batch'] == b and server['dimensions'] == d
            assert server['outcome'] == 'completed' and server['responseSha256'] == sha(response) and server['responseBytes'] == len(response)
            assert row['startedNs'] <= server['startedNs'] < server['finishedNs'] < phase['afterCalls']['atNs']
            assert server['startedNs'] < row['finishedNs']
            seen_requests.add(identity)
            assert children[row['helperPid']]['owner'] == (f"actor-{row['actor']}" if reusable else identity)
            if reusable:
                assert warm_by_pid[row['helperPid']]['finishedNs'] < row['startedNs']
            intervals.extend([(row['startedNs'], 1), (row['finishedNs'], -1)])
            grouped[f'd{d}-b{b}-c{c}-{item["mode"]}'].append((row['finishedNs'] - row['startedNs']) / 1e6)
        active = peak = 0
        for _, change in sorted(intervals, key=lambda event: (event[0], event[1])):
            active += change
            assert 0 <= active <= c
            peak = max(peak, active)
        assert active == 0
        concurrency[item['id']] = peak
    assert seen_requests == set(requests)
    return {'status': 'verified', 'calls': 256, 'helpersReaped': helper_count,
            'latencyMs': {key: stats(value) for key, value in grouped.items()},
            'readinessMs': stats(warmups), 'observedConcurrency': concurrency, 'idleMemory': memory,
            'planSha256': result['planSha256'], 'resultSha256': sha((directory / 'result.json').read_bytes())}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.directory), indent=2))
