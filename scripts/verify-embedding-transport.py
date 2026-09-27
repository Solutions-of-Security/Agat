#!/usr/bin/env python3
"""Independently replay timing, vectors, process cleanup and resource observations."""
import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
BASELINE = 'f9a16ea289845b9d7db142e5ee2144aad0d553ff'
SOURCES = {'scripts/profile-embedding-transport.py', 'scripts/verify-embedding-transport.py',
           'workers/agat_worker.py', 'workers/embedding_http.py', 'workers/telemetry.py',
           'workers/web_tools.py', 'workers/local_decisions.py'}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def compact(data):
    return json.dumps(data, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def distribution(values):
    values = sorted(values)
    assert values and all(math.isfinite(value) and value >= 0 for value in values)
    return {'count': len(values), 'min': round(values[0], 3),
            'p50': round(values[math.ceil(len(values) * .5) - 1], 3),
            'p95': round(values[math.ceil(len(values) * .95) - 1], 3), 'max': round(values[-1], 3)}


def verify(directory):
    if not __debug__:
        raise RuntimeError('Assertions must be enabled')
    directory = directory.resolve()
    assert directory.is_relative_to(ROOT / 'docs')
    plan = json.loads((directory / 'plan.json').read_text())
    result = json.loads((directory / 'result.json').read_text())
    assert result['planSha256'] == digest((directory / 'plan.json').read_bytes())
    assert result['status'] == 'observed' and result['failure'] is None and result['cleanup'] is True
    assert result['fdBefore'] == result['fdAfter'] and 0 < result['elapsedSeconds'] <= 200
    assert plan['schema'] == 'agat.embedding.transport-profile.v1' and plan['baselineCommit'] == BASELINE
    assert plan['platform'] in ('Darwin', 'Linux') and isinstance(plan['cpuCount'], int) and plan['cpuCount'] > 0
    assert plan['sampleIntervalMs'] == 50 and plan['deadlineSeconds'] == .3
    assert plan['vectorFormula'] == '((component % 19) + index + 1) / 37'
    assert re.fullmatch('[0-9a-f]{40}', plan['implementationCommit'])
    assert set(plan['sourceSha256']) == SOURCES
    for name, sha in plan['sourceSha256'].items():
        assert digest(subprocess.check_output(['git', 'show', f"{plan['implementationCommit']}:{name}"], cwd=ROOT, timeout=10)) == sha, name
    assert digest(subprocess.check_output(['git', 'show', f'{BASELINE}:workers/agat_worker.py'], cwd=ROOT, timeout=10)) == plan['baselineWorkerSha256']
    expected = []
    for d in (768, 4096):
        for b in (1, 32):
            for mode in ('direct', 'isolated'):
                expected.append((f'warm-d{d}-b{b}-{mode}', mode, 'success', b, d, 1, 1, False))
    for d in (768, 4096):
        for b in (1, 32):
            for c in (1, 4):
                for index, mode in enumerate(('direct', 'isolated', 'isolated', 'direct')):
                    expected.append((f'd{d}-b{b}-c{c}-{index}', mode, 'success', b, d, c, 8, True))
    for c in (1, 4):
        for kind in ('cancel-headers', 'cancel-body', 'deadline-body'):
            expected.append((f'{kind}-c{c}', 'isolated', kind, 1, 768, c, 8, True))
    keys = ('id', 'mode', 'kind', 'batch', 'dimensions', 'concurrency', 'calls', 'measured')
    assert [tuple(item[key] for key in keys) for item in plan['phases']] == expected
    assert [phase['id'] for phase in result['phases']] == [item[0] for item in expected]
    server = {row['id']: row for row in result['server']}
    assert len(server) == len(result['server']) == sum(item[6] for item in expected)
    seen = set()
    latencies = defaultdict(list)
    resources = defaultdict(list)
    cancellation = defaultdict(list)
    helpers = 0
    for item, phase in zip(plan['phases'], result['phases'], strict=True):
        assert phase['fdBefore'] == phase['fdAfter'] == result['fdBefore']
        assert 0 < phase['startedNs'] < phase['finishedNs'] and phase['idleRssBytes'] > 0
        assert [row['index'] for row in phase['rows']] == list(range(item['calls']))
        assert phase['samples']
        vectors = [[(component % 19 + index + 1) / 37 for component in range(item['dimensions'])] for index in range(item['batch'])]
        vector_sha = digest(compact(vectors))
        response = compact({'data': [{'index': i, 'embedding': vectors[i]} for i in reversed(range(item['batch']))]})
        intervals = []
        pids = set()
        for row in phase['rows']:
            identity = f"{item['id']}-{row['index']}"
            assert row['id'] == identity and identity not in seen
            seen.add(identity)
            peer = server[identity]
            assert (peer['kind'], peer['batch'], peer['dimensions']) == (item['kind'], item['batch'], item['dimensions'])
            payload = {'model': f"synthetic-{item['dimensions']}", 'input': [f"{identity}/{item['kind']}/{i}" for i in range(item['batch'])]}
            expected_input = digest(json.dumps(payload, ensure_ascii=False).encode())
            assert row['inputSha256'] == peer['inputSha256'] == expected_input
            assert peer['responseSha256'] == digest(response) and peer['responseBytes'] == len(response)
            assert phase['startedNs'] <= row['startedNs'] < peer['startedNs'] < row['finishedNs'] <= phase['finishedNs']
            assert peer['startedNs'] <= peer['finishedNs'] <= phase['finishedNs']
            intervals.extend([(row['startedNs'], 1), (row['finishedNs'], -1)])
            expected_outcome = 'completed' if item['kind'] == 'success' else 'deadline' if item['kind'] == 'deadline-body' else 'cancelled'
            assert row['outcome'] == expected_outcome
            assert peer['outcome'] == ('completed' if expected_outcome == 'completed' else 'disconnected')
            assert len(row['children']) == (1 if item['mode'] == 'isolated' else 0)
            for child in row['children']:
                assert type(child['pid']) is int and child['pid'] > 0 and type(child['returncode']) is int
                assert child['stdinClosed'] is True and child['stdoutClosed'] is True
                assert (child['returncode'] == 0) == (expected_outcome == 'completed')
                pids.add(str(child['pid']))
                helpers += 1
            elapsed = (row['finishedNs'] - row['startedNs']) / 1e6
            if expected_outcome == 'completed':
                assert row['vectorSha256'] == vector_sha
                if item['measured']:
                    key = f"d{item['dimensions']}-b{item['batch']}-c{item['concurrency']}-{item['mode']}"
                    latencies[key].append(elapsed)
            else:
                assert 'vectorSha256' not in row
                if expected_outcome == 'cancelled':
                    assert peer['startedNs'] <= row['cancelledNs'] <= row['finishedNs']
                    elapsed = (row['finishedNs'] - row['cancelledNs']) / 1e6
                assert 0 <= elapsed <= 2000
                assert expected_outcome in row['error']
                cancellation[item['id']].append(elapsed)
        active = peak = 0
        for _, change in sorted(intervals, key=lambda value: (value[0], value[1])):
            active += change
            assert active >= 0
            peak = max(peak, active)
        assert active == 0 and 1 <= peak <= item['concurrency']
        for sample in phase['samples']:
            assert phase['startedNs'] <= sample['atNs'] <= phase['finishedNs']
            assert sample['parentRssBytes'] > 0 and set(sample['helperRssBytes']) <= pids
            assert len(sample['helperRssBytes']) <= item['concurrency']
            assert all(type(value) is int and value >= 0 for value in sample['helperRssBytes'].values())
        if item['measured']:
            key = f"d{item['dimensions']}-b{item['batch']}-c{item['concurrency']}-{item['mode']}" if item['kind'] == 'success' else item['id']
            resources[key].append({'observedConcurrency': peak, 'idleRssBytes': phase['idleRssBytes'],
                                   'sampledParentMaxBytes': max(s['parentRssBytes'] for s in phase['samples']),
                                   'sampledHelpersMaxBytes': max(sum(s['helperRssBytes'].values()) for s in phase['samples']),
                                   'sampledCombinedMaxBytes': max(s['parentRssBytes'] + sum(s['helperRssBytes'].values()) for s in phase['samples'])})
    assert seen == set(server)
    return {'status': 'verified', 'requests': len(seen), 'measuredSuccess': sum(map(len, latencies.values())),
            'measuredCancelledOrTimedOut': sum(map(len, cancellation.values())), 'helpersReaped': helpers,
            'fdBefore': result['fdBefore'], 'fdAfter': result['fdAfter'],
            'latencyMs': {key: distribution(values) for key, values in latencies.items()},
            'cancellationOrDeadlineMs': {key: distribution(values) for key, values in cancellation.items()},
            'resources': dict(resources), 'planSha256': result['planSha256'],
            'resultSha256': digest((directory / 'result.json').read_bytes())}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.directory), indent=2, allow_nan=False))
