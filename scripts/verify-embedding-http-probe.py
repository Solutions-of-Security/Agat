#!/usr/bin/env python3
"""Replay the pinned embedding profile, including durable index and control traffic."""
import argparse
from datetime import datetime
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('rag_verifier_support', ROOT / 'scripts/verify-rag-http-probe.py')
support = importlib.util.module_from_spec(spec)
spec.loader.exec_module(support)
sha, summary, intervals, timing = (getattr(support, name) for name in ('sha', 'summary', 'intervals', 'timing'))


def deadline(value):
    assert isinstance(value, str) and re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z', value)
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def verify(directory):
    if not __debug__:
        raise RuntimeError('Evidence verification requires Python assertions')
    directory = directory.resolve()
    assert directory.is_relative_to((ROOT / 'docs').resolve())
    def read(name):
        assert (directory / name).resolve().parent == directory
        return json.loads((directory / name).read_text())
    plan, launcher = read('plan.json'), read('launcher-result.json')
    assert launcher['status'] == 'observed' and launcher['failure'] is None
    assert launcher['containersRemoved'] and launcher['cleanupErrors'] == []
    assert len(launcher['children']) == 2 and all(child['exitCode'] == 0 for child in launcher['children'])
    layouts = ['shared', 'independent']
    expected_files = {'plan.json'} | {f'{prefix}-{layout}.{extension}' for layout in layouts
        for prefix, extension in [('client', 'json'), ('server', 'json'), ('server', 'log'), ('postgres-image', 'json')]}
    assert set(launcher['files']) == expected_files
    for name, digest in launcher['files'].items():
        assert (directory / name).resolve().parent == directory
        assert sha((directory / name).read_bytes()) == digest, name
    assert re.fullmatch('[0-9a-f]{40}', plan['implementationCommit'])
    required_sources = {'scripts/run-embedding-http-probe.py', 'scripts/serve-embedding-http-probe.ts',
                        'scripts/verify-embedding-http-probe.py', 'scripts/rag-main-coordinator.ts',
                        'scripts/observe-rag-coordinator-main.mjs', 'scripts/run-rag-http-probe.py',
                        'scripts/verify-rag-http-probe.py', 'apps/coordinator/src/database.ts',
                        'apps/coordinator/src/server.ts', 'apps/coordinator/src/config.ts', 'apps/coordinator/src/knowledge.ts',
                        'apps/coordinator/src/postgres-database.ts', 'apps/coordinator/src/postgres-worker.ts', 'package-lock.json',
                        'docs/qualification/local-decisions/performance/embedding-http.md'}
    assert required_sources <= set(plan['sourceSha256'])
    for name, digest in plan['sourceSha256'].items():
        assert not Path(name).is_absolute() and '..' not in Path(name).parts
        assert re.fullmatch('[0-9a-f]{64}', digest)
        original = subprocess.check_output(['git', 'show', f"{plan['implementationCommit']}:{name}"], cwd=ROOT, timeout=60)
        assert sha(original) == digest, name
    assert re.fullmatch(r'v24\.\d+\.\d+', plan['nodeVersion'])
    assert 'apps/coordinator/dist/server.js' in plan['compiledSha256']
    assert all(name.startswith('apps/coordinator/dist/') and '..' not in Path(name).parts and re.fullmatch('[0-9a-f]{64}', digest)
               for name, digest in plan['compiledSha256'].items())
    assert (plan['layouts'], plan['replicas'], plan['dimensions'], plan['chunksPerBatch'], plan['iterations']) == (layouts, 2, 768, 32, 10)
    assert (plan['healthIntervalMs'], plan['renewalIntervalMs'], plan['maxProbeInflightPerKind'], plan['requestTimeoutSeconds']) == (100, 250, 4, 15)
    assert plan['maxQueuedCompletions'] == 40 and plan['maintenanceIntervalMs'] == 1000
    assert plan['poolBudget'] == {'admittedReplicas': 3, 'admittedPoolMaxPerRole': 4, 'eachMainPoolMaxPerRole': 4, 'fixturePoolMaxPerRole': 1}
    expected_phases = [{'id': 'warmup', 'concurrency': 2}, {'id': 'idle', 'concurrency': 0}]
    expected_phases += [{'id': f'c{c}-r{r}', 'concurrency': c} for c in (1, 2, 4) for r in (1, 2, 3)]
    assert plan['phases'] == expected_phases
    plan_sha = sha((directory / 'plan.json').read_bytes())
    vector_sha = sha(json.dumps([1 / 3] * 768, separators=(',', ':')).encode())
    report, all_documents, all_pids, image_ids = {}, [], [], []
    for layout in layouts:
        client, server = read(f'client-{layout}.json'), read(f'server-{layout}.json')
        assert client['layout'] == server['layout'] == layout
        assert client['planSha256'] == server['planSha256'] == plan_sha
        assert len(server['mains']) == len(client['runtimePids']) == 2
        assert client['runtimePids'] == [main['pid'] for main in server['mains']]
        assert len(set(client['runtimePids'])) == 2
        for main in server['mains']:
            assert type(main['pid']) is int and main['pid'] > 0
            assert main['pid'] not in [child['pid'] for child in launcher['children']]
            assert main['code'] == 0 and main['signal'] is None and main['entry'] == 'apps/coordinator/dist/server.js'
            all_pids.append(main['pid'])
        image = read(f'postgres-image-{layout}.json')
        assert image['image'] == 'postgres:17.6-alpine' and re.fullmatch('sha256:[0-9a-f]{64}', image['imageId'])
        image_ids.append(image['imageId'])
        assert len(client['phases']) == len(server['phases']) == len(expected_phases)
        groups = {str(c): {'completion': [], 'health': [], 'renewal': [], 'healthSkipped': 0, 'renewalSkipped': 0,
                          'loopMax': [], 'maintenance': []} for c in (0, 1, 2, 4)}
        maintenance_by_replica, peak_rss, renewal_kinds = [0, 0], [0, 0], set()
        for expected, observed, actual in zip(expected_phases, client['phases'], server['phases'], strict=True):
            label, concurrency = expected['id'], expected['concurrency']
            count = 2 if label == 'warmup' else concurrency * 10
            assert observed['id'] == actual['id'] == label and actual['type'] == 'finished'
            assert observed['concurrency'] == observed['observedHttpConcurrency'] == concurrency
            assert intervals(observed['completions']) == concurrency
            assert len(observed['jobs']) == len(observed['completions']) == len(actual['jobs']) == actual['verifiedJobs'] == count
            collection_ids = [job['collection'] for job in observed['jobs']]
            assert len(set(collection_ids)) == (min(1, count) if layout == 'shared' else count)
            group = groups[str(concurrency)]
            for index, (job, response, durable) in enumerate(zip(observed['jobs'], observed['completions'], actual['jobs'], strict=True)):
                timing(response)
                assert response['kind'] == 'completion' and response['identity'] == job['document'] == durable['document']
                assert response['replica'] == job['replica'] == durable['replica'] == index % 2
                assert response['valid'] and response['error'] is None and response['status'] == 200
                assert response['completed'] is True and response['remainingChunks'] == 0
                assert durable['collection'] == job['collection']
                assert durable['contentSha256'] == job['contentSha256'] == sha(f'{label}-{index}'.ljust(12800, 'A').encode())
                assert len(job['chunkIds']) == len(set(job['chunkIds'])) == durable['chunks'] == 32
                assert durable['chunkIdsSha256'] == sha(json.dumps(job['chunkIds'], separators=(',', ':')).encode())
                assert durable['dimensions'] == 768 and durable['vectorSha256'] == vector_sha
                assert durable['documentStatus'] == 'ready' and durable['jobStatus'] == 'completed'
                assert durable['readyEvents'] == 1 and durable['failures'] == 0
                all_documents.append(job['document'])
                if label != 'warmup':
                    group['completion'].append(response['wallMs'])
            assert len(observed['renewalLeases']) == len(actual['renewals']) == 4
            lease_map = {item['lease']: item for item in observed['renewalLeases']}
            assert len(lease_map) == 4
            assert {(item['kind'], item['replica']) for item in lease_map.values()} == {('stage', 0), ('embedding', 0), ('stage', 1), ('embedding', 1)}
            for renewal in actual['renewals']:
                expected_lease = lease_map[renewal['lease']]
                assert renewal['kind'] == expected_lease['kind'] and renewal['replica'] == expected_lease['replica']
                assert renewal['status'] == 'running' and deadline(renewal['finalExpiry']) >= deadline(renewal['initialExpiry'])
            for field, kind, interval in [('healthProbes', 'health', 100), ('renewalProbes', 'renewal', 250)]:
                rows = observed[field]
                if label == 'warmup':
                    assert rows == []
                else:
                    assert rows and any(row['outcome'] == 'dispatched' for row in rows)
                responses = []
                for tick, row in enumerate(rows):
                    assert row['tick'] == tick and row['scheduledMs'] == tick * interval
                    assert math.isfinite(row['dispatchLatenessMs']) and row['dispatchLatenessMs'] >= 0
                    if row['outcome'] == 'inflight_limit':
                        assert 'response' not in row
                        group[f'{kind}Skipped'] += 1
                        continue
                    assert row['outcome'] == 'dispatched'
                    response = row['response']; timing(response)
                    assert response['valid'] and response['error'] is None and response['kind'] == kind
                    assert response['replica'] in (0, 1)
                    if kind == 'health':
                        assert response['status'] == 200 and response['healthStatus'] == 'ok' and response['replica'] == tick % 2
                    else:
                        lease = observed['renewalLeases'][tick % 4]
                        assert response['status'] == 204 and response['identity'] == lease['lease'] and response['replica'] == lease['replica']
                        if label not in ('warmup', 'idle'):
                            renewal_kinds.add((lease['kind'], lease['replica']))
                    responses.append(response); group[kind].append(response['wallMs'])
                assert intervals(responses) <= 4
            assert len(actual['metrics']) == 2
            for replica, metric in enumerate(actual['metrics']):
                assert metric['id'] == label and metric['type'] == 'probe-finished'
                for key in ('maxMs', 'p99Ms', 'samples'):
                    assert math.isfinite(metric['eventLoop'][key]) and metric['eventLoop'][key] >= 0
                assert metric['processLifetimePeakRssBytes'] > 0
                peak_rss[replica] = max(peak_rss[replica], metric['processLifetimePeakRssBytes'])
                for tick in metric['maintenance']:
                    assert tick['error'] is None and math.isfinite(tick['durationMs']) and tick['durationMs'] >= 0
                if label not in ('warmup', 'idle'):
                    maintenance_by_replica[replica] += len(metric['maintenance'])
                if label != 'warmup':
                    group['maintenance'].extend(tick['durationMs'] for tick in metric['maintenance'])
                    group['loopMax'].append(metric['eventLoop']['maxMs'])
        assert all(count > 0 for count in maintenance_by_replica)
        assert renewal_kinds == {('stage', 0), ('embedding', 0), ('stage', 1), ('embedding', 1)}
        report[layout] = {'measuredCompletions': 210, 'warmupCompletions': 2, 'verifiedChunks': 6784,
            'loadMaintenanceCallsByReplica': maintenance_by_replica, 'lifetimePeakRssBytesByReplica': peak_rss,
            'groups': {key: {**{field: summary(rows[field]) for field in ('completion', 'health', 'renewal', 'maintenance')},
                             'healthSkipped': rows['healthSkipped'], 'renewalSkipped': rows['renewalSkipped'],
                             'eventLoopMaxMs': max(rows['loopMax'], default=0)} for key, rows in groups.items()}}
    assert len(all_documents) == len(set(all_documents)) == 424
    assert len(set(all_pids)) == 4 and len(set(image_ids)) == 1
    return {'status': 'observed', 'implementationCommit': plan['implementationCommit'], 'nodeVersion': plan['nodeVersion'],
            'measuredCompletions': 420, 'warmupCompletions': 4, 'verifiedChunks': 13568, 'layouts': report,
            'boundary': 'Synthetic closed-loop SQL/HTTP profile on one host. No model inference, SLO or causal comparison to earlier commits.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.evidence_dir), indent=2, allow_nan=False))
