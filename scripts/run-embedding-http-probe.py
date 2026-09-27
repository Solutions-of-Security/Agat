#!/usr/bin/env python3
"""Bounded embedding HTTP batches with independent health and renewal probes."""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import http.client
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('rag_probe_support', ROOT / 'scripts/run-rag-http-probe.py')
support = importlib.util.module_from_spec(spec)
spec.loader.exec_module(support)
sha, write, command, stop, reply, control = (getattr(support, name) for name in ('sha', 'write', 'command', 'stop', 'reply', 'control'))


def request(port, route, begin, *, kind, replica, token=None, body=None, barrier=None, identity=None):
    if barrier:
        barrier.wait(timeout=10)
    started = time.monotonic()
    row = {'kind': kind, 'replica': replica, 'identity': identity,
           'startedMs': round((started - begin) * 1000, 3), 'status': None, 'valid': False, 'error': None}
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=15)
    try:
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        connection.request('POST' if body is not None else 'GET', route,
                           body=json.dumps(body, allow_nan=False) if body is not None else None, headers=headers)
        response = connection.getresponse()
        row['status'] = response.status
        raw = response.read(131073)
        if len(raw) > 131072:
            raise ValueError('Oversized probe response')
        if kind == 'renewal':
            row['valid'] = response.status == 204 and raw == b''
        else:
            value = json.loads(raw)
            if kind == 'completion':
                row.update(completed=value.get('completed'), remainingChunks=value.get('remainingChunks'))
                row['valid'] = response.status == 200 and value.get('completed') is True and value.get('remainingChunks') == 0
            else:
                row['healthStatus'] = value.get('status')
                row['valid'] = response.status == 200 and value.get('status') == 'ok'
    except Exception as exc:
        row['error'] = type(exc).__name__
    finally:
        connection.close()
        finished = time.monotonic()
        row.update(finishedMs=round((finished - begin) * 1000, 3), wallMs=round((finished - started) * 1000, 3))
    return row


def monitor(kind, ready, prepared, begin, stopped, results):
    interval = .1 if kind == 'health' else .25
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        pending, tick = [], 0
        while not stopped.is_set():
            scheduled = begin + tick * interval
            if stopped.wait(max(0, scheduled - time.monotonic())):
                break
            pending = [future for future in pending if not future.done()]
            entry = {'tick': tick, 'scheduledMs': round(tick * interval * 1000, 3),
                     'dispatchLatenessMs': round(max(0, time.monotonic() - scheduled) * 1000, 3)}
            results.append(entry)
            if kind == 'health':
                replica = tick % 2
                args = {'route': '/api/v1/health', 'kind': kind, 'replica': replica}
            else:
                item = prepared['renewals'][tick % len(prepared['renewals'])]
                replica = item['replica']
                route = 'leases' if item['kind'] == 'stage' else 'workers/knowledge/leases'
                args = {'route': f"/api/v1/{route}/{item['lease']}/renew", 'kind': kind, 'replica': replica,
                        'token': item['token'], 'body': {}, 'identity': item['lease']}
            tick += 1
            if len(pending) >= 4:
                entry['outcome'] = 'inflight_limit'
                continue
            entry['outcome'] = 'dispatched'
            def run(target=entry, options=args, port=ready['ports'][replica]):
                target['response'] = request(port, begin=begin, **options)
            pending.append(pool.submit(run))


def phase(process, ready, item):
    prepared = control(process, {'type': 'prepare', **item})
    assert prepared['type'] == 'prepared' and prepared['id'] == item['id']
    expected = 2 if item['id'] == 'warmup' else item['concurrency'] * 10
    assert len(prepared['jobs']) == expected and len(prepared['renewals']) == 4
    begin, stopped = time.monotonic(), threading.Event()
    health, renewals, completions = [], [], []
    monitors = [threading.Thread(target=monitor, args=(kind, ready, prepared, begin, stopped, rows))
                for kind, rows in [('health', health), ('renewal', renewals)]] if item['id'] != 'warmup' else []
    for thread in monitors:
        thread.start()
    try:
        if item['concurrency']:
            count = item['concurrency']
            barrier = threading.Barrier(count + 1)
            with concurrent.futures.ThreadPoolExecutor(max_workers=count) as pool:
                pending = []
                for index, job in enumerate(prepared['jobs']):
                    body = {'embeddings': [{'chunkId': chunk, 'embedding': ready['vector']} for chunk in job['chunkIds']]}
                    pending.append(pool.submit(request, ready['ports'][job['replica']],
                        f"/api/v1/workers/knowledge/leases/{job['lease']}/complete", begin,
                        kind='completion', replica=job['replica'], token=job['token'], body=body,
                        barrier=barrier if index < count else None, identity=job['document']))
                barrier.wait(timeout=10)
                completions = [future.result(timeout=30) for future in pending]
        else:
            stopped.wait(2)
    finally:
        stopped.set()
        for thread in monitors:
            thread.join(timeout=20)
            if thread.is_alive():
                raise TimeoutError('Control monitor did not stop')
    active = maximum = 0
    for _, delta in sorted([(row['startedMs'], 1) for row in completions] + [(row['finishedMs'], -1) for row in completions],
                           key=lambda entry: (entry[0], -entry[1])):
        active += delta
        maximum = max(maximum, active)
    return {'id': item['id'], 'concurrency': item['concurrency'], 'observedHttpConcurrency': maximum,
            'jobs': [{key: job[key] for key in ('document', 'collection', 'chunkIds', 'contentSha256', 'replica')}
                     for job in prepared['jobs']],
            'renewalLeases': [{key: lease[key] for key in ('kind', 'lease', 'replica')} for lease in prepared['renewals']],
            'completions': completions, 'healthProbes': health, 'renewalProbes': renewals,
            'elapsedMs': round((time.monotonic() - begin) * 1000, 3)}


def main():
    if not __debug__:
        raise RuntimeError('Do not run the probe with Python optimization')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    args = parser.parse_args()
    evidence = args.evidence_dir.resolve()
    if evidence.exists() or not evidence.is_relative_to((ROOT / 'docs').resolve()) or evidence == (ROOT / 'docs').resolve():
        raise ValueError('Use a new evidence directory under docs')
    node = command(['node', '--version'])
    assert re.fullmatch(r'v24\.\d+\.\d+', node)
    implementation = command(['git', 'rev-parse', 'HEAD'])
    names = ['scripts/run-embedding-http-probe.py', 'scripts/serve-embedding-http-probe.ts',
             'scripts/verify-embedding-http-probe.py', 'scripts/run-rag-http-probe.py', 'scripts/verify-rag-http-probe.py',
             'scripts/rag-main-coordinator.ts', 'scripts/observe-rag-coordinator-main.mjs',
             'package-lock.json', 'apps/coordinator/package.json', 'apps/coordinator/tsconfig.json',
             'docs/qualification/local-decisions/performance/embedding-http.md']
    names.extend(file.relative_to(ROOT).as_posix() for file in (ROOT / 'apps/coordinator/src').rglob('*.ts'))
    sources = {}
    for name in sorted(set(names)):
        data = (ROOT / name).read_bytes()
        assert data == subprocess.check_output(['git', 'show', f'{implementation}:{name}'], cwd=ROOT, timeout=60), name
        sources[name] = sha(data)
    command(['npm', 'run', 'build', '--workspace', '@agat/coordinator'])
    compiled = {file.relative_to(ROOT).as_posix(): sha(file.read_bytes()) for file in sorted((ROOT / 'apps/coordinator/dist').rglob('*.js'))}
    assert 'apps/coordinator/dist/server.js' in compiled
    evidence.mkdir(parents=True)
    plan = {'implementationCommit': implementation, 'sourceSha256': sources, 'compiledSha256': compiled, 'nodeVersion': node,
            'layouts': ['shared', 'independent'], 'replicas': 2, 'dimensions': 768, 'chunksPerBatch': 32, 'iterations': 10,
            'phases': [{'id': 'warmup', 'concurrency': 2}, {'id': 'idle', 'concurrency': 0}]
                      + [{'id': f'c{c}-r{r}', 'concurrency': c} for c in (1, 2, 4) for r in (1, 2, 3)],
            'healthIntervalMs': 100, 'renewalIntervalMs': 250, 'maxProbeInflightPerKind': 4, 'requestTimeoutSeconds': 15,
            'maxQueuedCompletions': 40, 'maintenanceIntervalMs': 1000,
            'poolBudget': {'admittedReplicas': 3, 'admittedPoolMaxPerRole': 4, 'eachMainPoolMaxPerRole': 4, 'fixturePoolMaxPerRole': 1},
            'hostLoadAtStart': os.getloadavg(), 'fixture': 'Synthetic vectors of 768 values 1/3; no model inference or quality claim',
            'timing': 'External client wall time includes JSON serialization; closed-loop batches, independent bounded control probes.'}
    write(evidence / 'plan.json', plan)
    children, containers, cleanup_errors, failure = [], [], [], None
    started = time.monotonic()
    def interrupted(_number, _frame):
        raise KeyboardInterrupt('Probe interrupted')
    signal.signal(signal.SIGTERM, interrupted)
    try:
        for layout in plan['layouts']:
            container = f'agat-embedding-http-{uuid.uuid4().hex[:12]}'
            containers.append(container)
            command(['docker', 'run', '--detach', '--name', container, '--publish', '127.0.0.1::5432',
                     '--env', 'POSTGRES_DB=agat_rag_http', '--env', 'POSTGRES_USER=postgres', '--env', 'POSTGRES_PASSWORD=http-admin-test',
                     '--env', 'AGAT_POSTGRES_MIGRATION_PASSWORD=http-migration-test', '--env', 'AGAT_POSTGRES_SYSTEM_PASSWORD=http-system-test',
                     '--env', 'AGAT_POSTGRES_TENANT_PASSWORD=http-tenant-test',
                     '--volume', f'{ROOT}/deploy/k8s/docker-desktop/postgres-init.sh:/docker-entrypoint-initdb.d/10-agat.sh:ro', 'postgres:17.6-alpine'])
            address = command(['docker', 'port', container, '5432/tcp'])
            assert re.fullmatch(r'127\.0\.0\.1:[0-9]+', address)
            deadline = time.monotonic() + 60
            while True:
                try:
                    command(['docker', 'exec', container, 'pg_isready', '--host', '127.0.0.1', '--username', 'postgres', '--dbname', 'agat_rag_http'])
                    break
                except subprocess.CalledProcessError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Disposable PostgreSQL startup')
                    time.sleep(.25)
            write(evidence / f'postgres-image-{layout}.json', {'image': 'postgres:17.6-alpine', 'imageId': command(['docker', 'inspect', '--format', '{{.Image}}', container])})
            env = {key: value for key, value in os.environ.items() if not key.startswith('AGAT_')}
            env.update(AGAT_RAG_HTTP_DISPOSABLE='1', AGAT_POSTGRES_MIGRATION_URL=f'postgresql://agat_migrator:http-migration-test@{address}/agat_rag_http',
                       AGAT_POSTGRES_URL=f'postgresql://agat_system:http-system-test@{address}/agat_rag_http',
                       AGAT_POSTGRES_TENANT_URL=f'postgresql://agat_tenant:http-tenant-test@{address}/agat_rag_http',
                       AGAT_POSTGRES_SSL_MODE='disable', AGAT_REGION='eu-test-1', AGAT_RESIDENCY_DOMAIN='eu-test',
                       AGAT_POSTGRES_EXPECTED_REPLICAS='3', AGAT_POSTGRES_POOL_MAX='4', AGAT_POSTGRES_ADMISSION_CONCURRENCY='2',
                       AGAT_POSTGRES_ADMISSION_DURATION_MS='500', AGAT_POSTGRES_ADMISSION_MIN_OPERATIONS='10', AGAT_POSTGRES_ADMISSION_P99_MS='1000')
            phases, ready = [], None
            with tempfile.TemporaryDirectory(prefix='agat-embedding-http-') as folder:
                with (Path(folder) / 'stderr.log').open('wb') as log:
                    process = subprocess.Popen(['node', '--import', 'tsx', 'scripts/serve-embedding-http-probe.ts', folder, str(evidence), layout],
                        cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True, bufsize=1, start_new_session=True)
                    children.append(process)
                    try:
                        ready = reply(process, timeout=120)
                        assert ready['type'] == 'ready' and len(set(ready['runtimePids'])) == 2
                        for item in plan['phases']:
                            print(f"{layout}: {item['id']}", flush=True)
                            measured = phase(process, ready, item)
                            phases.append(measured)
                            finished = control(process, {'type': 'finish', 'id': item['id']})
                            assert finished['type'] == 'finished' and finished['verifiedJobs'] == len(measured['completions'])
                            assert measured['observedHttpConcurrency'] == item['concurrency']
                            assert all(row['valid'] for row in measured['completions'])
                            for field in ('healthProbes', 'renewalProbes'):
                                assert all(entry.get('response', {}).get('valid') for entry in measured[field] if entry['outcome'] == 'dispatched')
                        process.stdin.write('{"type":"stop"}\n'); process.stdin.flush()
                        assert process.wait(timeout=10) == 0
                    finally:
                        stop(process)
                        write(evidence / f'client-{layout}.json', {'planSha256': sha((evidence / 'plan.json').read_bytes()), 'layout': layout,
                            'runtimePids': ready['runtimePids'] if ready else [], 'phases': phases})
                        log.flush()
                        diagnostic = (Path(folder) / 'stderr.log').read_text(errors='replace')[-16384:]
                        diagnostic = re.sub(r'postgres(?:ql)?://[^\s\"\']+', '<disposable-postgres-url>', diagnostic)
                        (evidence / f'server-{layout}.log').write_text(diagnostic.replace(folder, '<temporary>').replace(str(ROOT), '<repo>').rstrip() + '\n')
            command(['docker', 'rm', '--force', '--volumes', container]); containers.remove(container)
    except (Exception, KeyboardInterrupt) as exc:
        failure = {'type': type(exc).__name__, 'message': str(exc)[:300]}
    finally:
        for process in children:
            try:
                stop(process)
            except Exception as exc:
                cleanup_errors.append(type(exc).__name__)
        for container in containers[:]:
            try:
                command(['docker', 'rm', '--force', '--volumes', container]); containers.remove(container)
            except Exception as exc:
                cleanup_errors.append(type(exc).__name__)
        result = {'status': 'observed' if failure is None and not cleanup_errors else 'incomplete', 'failure': failure,
                  'cleanupErrors': cleanup_errors, 'containersRemoved': not containers,
                  'children': [{'pid': child.pid, 'exitCode': child.returncode} for child in children],
                  'elapsedMs': round((time.monotonic() - started) * 1000, 3), 'hostLoadAtFinish': os.getloadavg(),
                  'files': {file.name: sha(file.read_bytes()) for file in evidence.iterdir() if file.is_file()}}
        write(evidence / 'launcher-result.json', result)
    print(result['status'], failure, flush=True)
    return 0 if result['status'] == 'observed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
