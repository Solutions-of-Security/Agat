#!/usr/bin/env python3
"""Reconstruct scheduled calls, actor ownership, recovery and idle resources."""
import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {'scripts/profile-embedding-session-endurance.py', 'scripts/verify-embedding-session-endurance.py',
           'scripts/lib/embedding_session.py', 'scripts/profile-embedding-transport.py',
           'workers/agat_worker.py', 'workers/embedding_http.py', 'workers/telemetry.py',
           'workers/web_tools.py', 'workers/local_decisions.py'}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return json.dumps(value, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def stats(values):
    values = sorted(values)
    return {'count': len(values), 'min': round(values[0], 3), 'p50': round(values[math.ceil(len(values) * .5) - 1], 3),
            'p95': round(values[math.ceil(len(values) * .95) - 1], 3), 'max': round(values[-1], 3)}


def verify(directory):
    if not __debug__:
        raise RuntimeError('Assertions must be enabled')
    directory = directory.resolve()
    assert directory.is_relative_to(ROOT / 'docs')
    plan = json.loads((directory / 'plan.json').read_text())
    result = json.loads((directory / 'result.json').read_text())
    assert plan['schema'] == 'agat.embedding.session-endurance.v1'
    assert result['status'] == 'observed' and result['failure'] is None and result['cleanup'] is True and result['fixtureErrors'] == []
    assert result['planSha256'] == sha((directory / 'plan.json').read_bytes())
    assert 120 <= result['elapsedSeconds'] <= 190
    assert (plan['rounds'], plan['actors'], plan['scheduleSeconds'], plan['roundPeriodSeconds'], plan['deadlineSeconds'], plan['controlActor']) == (120, 4, 120, 1, .25, 3)
    assert re.fullmatch('[0-9a-f]{40}', plan['implementationCommit']) and set(plan['sourceSha256']) == SOURCES
    for name, digest in plan['sourceSha256'].items():
        assert sha(subprocess.check_output(['git', 'show', f"{plan['implementationCommit']}:{name}"], cwd=ROOT, timeout=10)) == digest
    faults = {str(19 + 10 * index): {'actor': index % 3, 'kind': ('cancel-headers', 'cancel-body', 'deadline-body')[index % 3]} for index in range(10)}
    assert plan['faults'] == faults
    assert [round_['index'] for round_ in result['rounds']] == list(range(120))
    assert 120e9 <= result['scheduleFinishedNs'] - result['scheduleStartedNs'] <= 185e9
    server = {row['id']: row for row in result['server']}
    assert len(server) == len(result['server']) == 490
    children = {child['pid']: child for child in result['children']}
    assert len(children) == len(result['children']) == 14
    assert all(type(pid) is int and pid > 0 and type(child['returncode']) is int
               and child['stdinClosed'] is True and child['stdoutClosed'] is True for pid, child in children.items())
    assert [row['actor'] for row in result['readiness']] == list(range(4))
    current = {}
    for ready in result['readiness']:
        assert 0 < ready['startedNs'] < ready['finishedNs'] < result['ready']['atNs']
        child = children[ready['pid']]
        assert child['actor'] == ready['actor'] and child['createdFor'] == f"ready-{ready['actor']}"
        assert ready['startedNs'] < child['startedNs'] < ready['finishedNs']
        current[ready['actor']] = ready['pid']
    control_pid = current[3]
    seen_children = set(current.values())
    seen_requests = set()
    killed = set()
    last_finish = {actor: 0 for actor in range(4)}
    vectors = {}
    for batch in (1, 32):
        for dimensions in (768, 4096):
            values = [[((component % 19) + index + 1) / 37 for component in range(dimensions)] for index in range(batch)]
            response = encoded({'data': [{'index': i, 'embedding': values[i]} for i in reversed(range(batch))]})
            vectors[batch, dimensions] = (sha(encoded(values)), sha(response), len(response))
    latencies = defaultdict(list)
    cancelled = []
    deadlines = []
    overlap = []
    shots = [result['before'], result['ready']]
    for round_ in result['rounds']:
        index = round_['index']
        fault = faults.get(str(index))
        dimensions = 768 if index % 4 < 2 else 4096
        batch = 1 if index % 2 == 0 else 32
        expected = []
        for actor in range(4):
            if fault and fault['actor'] == actor:
                expected.append((f'r{index}-a{actor}-fault', actor, fault['kind'], 1, 768))
            expected.append((f'r{index}-a{actor}-ok', actor, 'success', batch, dimensions))
        assert [tuple(row[key] for key in ('id', 'actor', 'kind', 'batch', 'dimensions')) for row in round_['rows']] == expected
        intervals = []
        for row in round_['rows']:
            actor, identity, kind = row['actor'], row['id'], row['kind']
            assert result['scheduleStartedNs'] + index * 1e9 - 1e6 <= row['startedNs'] < row['finishedNs'] < round_['idle']['atNs']
            assert row['startedNs'] >= last_finish[actor]
            last_finish[actor] = row['finishedNs']
            child = children[row['helperPid']]
            assert child['actor'] == actor
            if current[actor] is None:
                assert row['helperPid'] not in seen_children and child['createdFor'] == identity
                assert row['startedNs'] < child['startedNs'] < row['finishedNs']
                current[actor] = row['helperPid']
                seen_children.add(row['helperPid'])
            assert row['helperPid'] == current[actor]
            contents = [f'{identity}/{kind}/{i}' for i in range(row['batch'])]
            payload = {'model': f"synthetic-{row['dimensions']}", 'input': contents}
            request_sha = sha(json.dumps(payload, ensure_ascii=False).encode())
            observed = server[identity]
            assert row['inputSha256'] == observed['inputSha256'] == request_sha
            assert (observed['kind'], observed['batch'], observed['dimensions']) == (kind, row['batch'], row['dimensions'])
            vector_sha, response_sha, response_bytes = vectors[row['batch'], row['dimensions']]
            assert observed['responseSha256'] == response_sha and observed['responseBytes'] == response_bytes
            assert row['startedNs'] <= observed['startedNs'] < row['finishedNs']
            assert observed['startedNs'] < observed['finishedNs'] < round_['idle']['atNs']
            seen_requests.add(identity)
            wall = (row['finishedNs'] - row['startedNs']) / 1e6
            if kind == 'success':
                assert row['outcome'] == 'completed' and row['vectorSha256'] == vector_sha and observed['outcome'] == 'completed'
                assert row['helperAfterPid'] == current[actor] and row['helperReturncode'] is None
                assert row['stdinClosed'] is False and row['stdoutClosed'] is False
                latencies['recovery' if child['createdFor'] == identity else f"d{row['dimensions']}-b{row['batch']}"].append(wall)
            else:
                assert observed['outcome'] == 'disconnected' and 'vectorSha256' not in row
                assert row['helperAfterPid'] is None and type(row['helperReturncode']) is int and row['helperReturncode'] < 0
                assert row['stdinClosed'] is True and row['stdoutClosed'] is True
                assert child['returncode'] == row['helperReturncode']
                killed.add(current[actor])
                current[actor] = None
                if kind.startswith('cancel-'):
                    assert row['outcome'] == 'cancelled'
                    assert observed['startedNs'] <= row['cancelledNs'] <= row['finishedNs']
                    latency = (row['finishedNs'] - row['cancelledNs']) / 1e6
                    assert latency <= 1000
                    cancelled.append(latency)
                else:
                    assert row['outcome'] == 'deadline' and 250 <= wall <= 3000
                    deadlines.append(wall)
            intervals.extend([(row['startedNs'], 1), (row['finishedNs'], -1)])
        active = peak = 0
        for _, change in sorted(intervals, key=lambda item: (item[0], item[1])):
            active += change
            assert 0 <= active <= 4
            peak = max(peak, active)
        assert active == 0
        overlap.append(peak)
        assert round_['idle']['actorPids'] == [current[actor] for actor in range(4)]
        assert current[3] == control_pid
        shots.append(round_['idle'])
    assert seen_requests == set(server) and seen_children == set(children) and len(killed) == 10
    assert all(child['returncode'] == 0 for pid, child in children.items() if pid not in killed)
    shots.append(result['afterClose'])
    assert len({shot['parentPid'] for shot in shots}) == 1
    assert result['before']['actorPids'] == result['afterClose']['actorPids'] == [None] * 4
    assert result['before']['helperRssBytes'] == result['afterClose']['helperRssBytes'] == {}
    assert result['before']['descriptors'] == result['afterClose']['descriptors']
    previous = 0
    for position, shot in enumerate(shots):
        assert shot['atNs'] > previous
        previous = shot['atNs']
        assert shot['parentRssBytes'] > 0 and shot['descriptors'] > 0 and shot['guardThreads'] == 0
        assert set(shot['helperRssBytes']) == {str(pid) for pid in shot['actorPids'] if pid is not None}
        assert all(type(value) is int and value > 0 for value in shot['helperRssBytes'].values())
        assert shot['descriptors'] == result['before']['descriptors'] + (8 if 0 < position < len(shots) - 1 else 0)
    assert result['ready']['atNs'] < result['scheduleStartedNs'] < result['scheduleFinishedNs'] < result['afterClose']['atNs']
    control = [round_['idle']['helperRssBytes'][str(control_pid)] for round_ in result['rounds']]
    return {'status': 'verified', 'successes': 480, 'cancelled': len(cancelled), 'deadlines': len(deadlines),
            'helpersReaped': 14, 'helperReplacements': 10, 'scheduleSeconds': (result['scheduleFinishedNs'] - result['scheduleStartedNs']) / 1e9,
            'latencyMs': {key: stats(values) for key, values in latencies.items()}, 'cancelReleaseMs': stats(cancelled), 'deadlineReturnMs': stats(deadlines),
            'roundConcurrency': overlap, 'controlPid': control_pid,
            'controlIdleRssBytes': {'first': control[0], 'afterFirstLargeBatch': control[3], 'last': control[-1], 'minAfterRound9': min(control[9:]), 'maxAfterRound9': max(control[9:])},
            'maxCombinedIdleHelperRssBytes': max(sum(shot['helperRssBytes'].values()) for shot in shots),
            'parentIdleRssBytes': {'first': shots[0]['parentRssBytes'], 'last': shots[-1]['parentRssBytes'], 'max': max(shot['parentRssBytes'] for shot in shots)},
            'descriptorsBeforeAndAfter': result['before']['descriptors'], 'descriptorsDuring': result['ready']['descriptors'],
            'planSha256': result['planSha256'], 'resultSha256': sha((directory / 'result.json').read_bytes())}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.directory), indent=2))
