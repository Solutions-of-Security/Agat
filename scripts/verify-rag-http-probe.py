#!/usr/bin/env python3
"""Replay bounded HTTP probe evidence without starting any service."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def summary(values):
    ordered = sorted(values)
    if not ordered:
        return {'count': 0, 'p50Ms': None, 'p95Ms': None, 'maxMs': None}
    def percentile(fraction):
        index = (len(ordered) - 1) * fraction
        lo, hi = math.floor(index), math.ceil(index)
        return round(ordered[lo] + (ordered[hi] - ordered[lo]) * (index - lo), 3)
    return {'count': len(values), 'p50Ms': percentile(.5), 'p95Ms': percentile(.95), 'maxMs': max(values)}


def intervals(rows):
    active = maximum = 0
    for _, change in sorted([(row['startedMs'], 1) for row in rows] + [(row['finishedMs'], -1) for row in rows],
                            key=lambda item: (item[0], -item[1])):
        active += change
        maximum = max(maximum, active)
    return maximum


def timing(row):
    for key in ('startedMs', 'finishedMs', 'wallMs'):
        assert isinstance(row[key], (int, float)) and math.isfinite(row[key]) and row[key] >= 0
    assert abs(row['finishedMs'] - row['startedMs'] - row['wallMs']) <= .002


def verify(directory):
    directory = directory.resolve()
    assert directory.is_relative_to(ROOT / 'docs')
    def read(name):
        return json.loads((directory / name).read_text())
    launcher, plan = read('launcher-result.json'), read('plan.json')
    assert launcher['status'] == 'observed' and launcher['failure'] is None
    assert launcher['containerRemoved'] and not launcher['cleanupErrors']
    assert len(launcher['children']) == 2 and all(child['exitCode'] == 0 for child in launcher['children'])
    for name, expected in launcher['files'].items():
        assert (directory / name).resolve().parent == directory
        assert sha((directory / name).read_bytes()) == expected, f'Changed evidence: {name}'
    assert re.fullmatch('[0-9a-f]{40}', plan['implementationCommit'])
    for name, expected in plan['sourceSha256'].items():
        original = subprocess.check_output(['git', 'show', f"{plan['implementationCommit']}:{name}"], cwd=ROOT, timeout=60)
        assert sha(original) == expected, f'Changed implementation: {name}'
    expected_phases = [{'id': 'warmup', 'concurrency': 1}, {'id': 'idle', 'concurrency': 0}]
    expected_phases += [{'id': f'c{c}-r{r}', 'concurrency': c} for c in (1, 2, 4) for r in (1, 2, 3)]
    assert plan['phases'] == expected_phases
    assert (plan['candidates'], plan['dimensions'], plan['candidateLimit']) == (9716, 768, 10000)
    assert (plan['healthIntervalMs'], plan['maxHealthInflight']) == (100, 4)
    plan_sha = sha((directory / 'plan.json').read_bytes())
    expected_text = sha(b'Synthetic exact-match fixture.')
    results = {}
    for backend in ('sqlite', 'postgresql'):
        client, server = read(f'client-{backend}.json'), read(f'server-{backend}.json')
        assert client['planSha256'] == server['planSha256'] == plan_sha
        assert client['backend'] == server['backend'] == backend
        assert client['target'] == server['target'] and isinstance(client['target'], str)
        assert server['indexed'] == plan['candidates'] and server['nodeVersion'] == plan['nodeVersion']
        if plan.get('verifyDatabaseCandidateCount'):
            assert client['databaseCandidates'] == server['databaseCandidates'] == plan['candidates']
        assert len(client['phases']) == len(server['phases']) == len(expected_phases)
        groups = {str(c): {'searches': [], 'health': [], 'skipped': 0, 'loopMax': []} for c in (0, 1, 2, 4)}
        run_ids = []
        for expected, observed, actual in zip(expected_phases, client['phases'], server['phases'], strict=True):
            count, label = expected['concurrency'], expected['id']
            assert observed['id'] == actual['id'] == label
            assert observed['concurrency'] == actual['verifiedRetrievals'] == count
            assert len(observed['searches']) == len(actual['retrievals']) == count
            assert observed['observedHttpConcurrency'] == intervals(observed['searches']) == count
            for row in observed['searches']:
                timing(row)
                assert row['status'] == 200 and row['error'] is None and row['valid']
                assert row['hits'] == [{'score': 1, 'marker': 'K1', 'documentId': client['target'],
                    'sourceUri': 'agat://rag-http/old-winner', 'documentSha256': expected_text, 'chunkSha256': expected_text}]
            for row in actual['retrievals']:
                assert (row['events'], row['score'], row['marker'], row['candidateLimit']) == (1, 1, 'K1', 10000)
                assert row['documentId'] == client['target']
                run_ids.append(row['runId'])
            probes = observed['healthProbes']
            assert [row['scheduledMs'] for row in probes] == list(range(0, len(probes) * 100, 100))
            dispatched, skipped = [], 0
            for entry in probes:
                assert math.isfinite(entry['dispatchLatenessMs']) and entry['dispatchLatenessMs'] >= 0
                if entry['outcome'] == 'inflight_limit':
                    assert 'response' not in entry
                    skipped += 1
                else:
                    assert entry['outcome'] == 'dispatched'
                    row = entry['response']; timing(row)
                    assert row['status'] == 200 and row['healthStatus'] == 'ok' and row['error'] is None and row['valid']
                    assert row['startedMs'] + .002 >= entry['scheduledMs'] + entry['dispatchLatenessMs']
                    dispatched.append(row)
            assert intervals(dispatched) <= 4
            if label == 'warmup':
                assert not probes
                continue
            assert dispatched, 'Every measured phase must contain health observations'
            loop = actual['eventLoop']
            assert loop['samples'] > 0 and 0 <= loop['p99Ms'] <= loop['maxMs']
            group = groups[str(count)]
            group['searches'].extend(row['wallMs'] for row in observed['searches'])
            group['health'].extend(row['wallMs'] for row in dispatched)
            group['skipped'] += skipped; group['loopMax'].append(loop['maxMs'])
        assert len(run_ids) == len(set(run_ids)) == 22
        results[backend] = {key: {'search': summary(value['searches']), 'healthDispatched': summary(value['health']),
            'healthNotDispatched': value['skipped'], 'maxEventLoopDelayMs': max(value['loopMax'])} for key, value in groups.items()}
    return {'status': 'verified', 'implementationCommit': plan['implementationCommit'], 'planSha256': plan_sha,
            **({'databaseCandidatesVerifiedPerBackend': plan['candidates']} if plan.get('verifyDatabaseCandidateCount') else {}),
            'launcherSha256': sha((directory / 'launcher-result.json').read_bytes()), 'measuredSearches': 42,
            'warmupSearches': 2, 'summary': results,
            'limitations': ['Health latency covers dispatched probes only; skipped arrivals remain separate.',
                            'Three closed-loop bursts per level, one coordinator per backend, shared host, no production SLO.',
                            'Synthetic vectors with an analytically known winner; no model or semantic qualification.']}


def main():
    if not __debug__:
        raise RuntimeError('Evidence verification requires Python assertions')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.evidence_dir), indent=2))


if __name__ == '__main__':
    main()
