#!/usr/bin/env python3
"""Bounded HTTP bursts and separately scheduled health probes against a disposable coordinator."""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ['scripts/run-rag-http-probe.py', 'scripts/serve-rag-http-probe.ts', 'scripts/verify-rag-http-probe.py', 'package-lock.json',
           'apps/coordinator/src/server.ts', 'apps/coordinator/src/config.ts', 'apps/coordinator/src/database.ts',
           'apps/coordinator/src/knowledge.ts', 'apps/coordinator/src/knowledge-ranking.ts',
           'apps/coordinator/src/postgres-database.ts', 'apps/coordinator/src/postgres-worker.ts',
           'apps/coordinator/src/postgres-response-buffer.ts', 'apps/coordinator/src/sync-database.ts']
SOURCES += ['apps/coordinator/src/knowledge-search-executor.ts', 'apps/coordinator/src/knowledge-search-worker.ts']
SOURCES += ['scripts/rag-main-coordinator.ts', 'scripts/observe-rag-coordinator-main.mjs']


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write(path, data):
    with path.open('x') as stream:
        json.dump(data, stream, indent=2, allow_nan=False)
        stream.write('\n')


def command(argv):
    return subprocess.check_output(argv, cwd=ROOT, text=True, stderr=subprocess.PIPE, timeout=60).strip()


def stop(process):
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)


def reply(process, timeout=60):
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        if not selector.select(timeout):
            raise TimeoutError('Coordinator control reply')
    line = process.stdout.readline()
    if not line:
        raise RuntimeError('Coordinator exited before its control reply')
    return json.loads(line)


def control(process, value):
    process.stdin.write(json.dumps(value) + '\n')
    process.stdin.flush()
    return reply(process)


def request(port, route, begin, body=None, token=None, target=None, barrier=None):
    if barrier:
        barrier.wait(timeout=10)
    started = time.monotonic()
    row = {'startedMs': round((started - begin) * 1000, 3), 'status': None, 'valid': False, 'error': None}
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=15)
    try:
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        connection.request('POST' if body else 'GET', route, body=json.dumps(body) if body else None, headers=headers)
        response = connection.getresponse()
        row['status'] = response.status
        raw = response.read(131073)
        if len(raw) > 131072:
            raise ValueError('Response exceeded probe limit')
        value = json.loads(raw)
        if body:
            hits = value.get('hits', [])
            row['hits'] = [{'score': hit.get('score'), 'marker': hit.get('marker'),
                            'documentId': hit.get('provenance', {}).get('documentId'),
                            'sourceUri': hit.get('provenance', {}).get('sourceUri'),
                            'documentSha256': hit.get('provenance', {}).get('documentSha256'),
                            'chunkSha256': hit.get('provenance', {}).get('chunkSha256')} for hit in hits]
            row['valid'] = (response.status == 200 and len(hits) == 1 and hits[0]['score'] == 1
                            and hits[0]['marker'] == 'K1' and hits[0]['provenance']['documentId'] == target)
        else:
            row['healthStatus'] = value.get('status')
            row['knowledgeSearch'] = value.get('knowledgeSearch')
            row['valid'] = response.status == 200 and value.get('status') == 'ok'
    except Exception as exc:
        row['error'] = type(exc).__name__
    finally:
        connection.close()
        finished = time.monotonic()
        row.update(finishedMs=round((finished - begin) * 1000, 3), wallMs=round((finished - started) * 1000, 3))
    return row


def health_loop(port, begin, stopped, result):
    pending = []
    tick = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        while not stopped.is_set():
            scheduled = begin + tick * .1
            if stopped.wait(max(0, scheduled - time.monotonic())):
                break
            pending = [future for future in pending if not future.done()]
            entry = {'scheduledMs': round(tick * 100, 3),
                     'dispatchLatenessMs': round(max(0, time.monotonic() - scheduled) * 1000, 3)}
            tick += 1
            result.append(entry)
            if len(pending) >= 4:
                entry['outcome'] = 'inflight_limit'
                continue
            entry['outcome'] = 'dispatched'
            def run(item=entry):
                item['response'] = request(port, '/api/v1/health', begin)
            pending.append(pool.submit(run))


def phase(process, ready, label, concurrency):
    prepared = control(process, {'type': 'prepare', 'id': label, 'concurrency': concurrency})
    if prepared['type'] != 'prepared' or prepared['id'] != label or len(prepared['leases']) != concurrency:
        raise ValueError('Wrong phase preparation')
    begin = time.monotonic()
    probes, searches = [], []
    stopped = threading.Event()
    monitor = threading.Thread(target=health_loop, args=(ready['port'], begin, stopped, probes))
    if label != 'warmup':
        monitor.start()
    try:
        if concurrency:
            barrier = threading.Barrier(concurrency + 1)
            with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
                pending = [pool.submit(request, ready['port'], f"/api/v1/leases/{item['lease']}/knowledge/search",
                                       begin, ready['query'], item['token'], ready['target'], barrier)
                           for item in prepared['leases']]
                barrier.wait(timeout=10)
                searches = [future.result(timeout=30) for future in pending]
        else:
            stopped.wait(2)
    finally:
        stopped.set()
        if monitor.ident is not None:
            monitor.join(timeout=20)
            if monitor.is_alive():
                raise TimeoutError('Health monitor did not stop')
    active = maximum = 0
    for _, delta in sorted([(row['startedMs'], 1) for row in searches]
                           + [(row['finishedMs'], -1) for row in searches], key=lambda item: (item[0], -item[1])):
        active += delta
        maximum = max(maximum, active)
    return {'id': label, 'concurrency': concurrency, 'observedHttpConcurrency': maximum,
            'searches': searches, 'healthProbes': probes, 'elapsedMs': round((time.monotonic() - begin) * 1000, 3)}


def main():
    if not __debug__:
        raise RuntimeError('Do not run the evidence probe with Python optimization')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    parser.add_argument('--execution-mode', choices=['sync', 'isolated'], default='sync')
    parser.add_argument('--maintenance-interval-ms', type=int, choices=[0, 1000], default=0)
    parser.add_argument('--coordinator-entry', choices=['handler', 'main'], default='handler')
    args = parser.parse_args()
    if args.coordinator_entry == 'main' and args.maintenance_interval_ms != 1000:
        parser.error('The real main always runs its 1000 ms maintenance timer; use --maintenance-interval-ms 1000')
    evidence = args.evidence_dir.resolve()
    if evidence.exists() or not evidence.is_relative_to(ROOT / 'docs') or evidence == ROOT / 'docs':
        raise ValueError('Use a new evidence directory under docs')
    node_version = command(['node', '--version'])
    if not re.fullmatch(r'v24\.\d+\.\d+', node_version):
        raise ValueError('Use the repository Node 24 runtime for this protocol')
    implementation = command(['git', 'rev-parse', 'HEAD'])
    sources = {}
    source_names = set(SOURCES)
    if args.coordinator_entry == 'main':
        source_names.update(path.relative_to(ROOT).as_posix() for path in (ROOT / 'apps/coordinator/src').rglob('*.ts'))
        source_names.update(['apps/coordinator/tsconfig.json', 'apps/coordinator/package.json'])
    for name in sorted(source_names):
        data = (ROOT / name).read_bytes()
        committed = subprocess.check_output(['git', 'show', f'{implementation}:{name}'], cwd=ROOT, timeout=60)
        if data != committed:
            raise ValueError(f'Commit the implementation before measuring: {name}')
        sources[name] = sha(data)
    compiled = None
    if args.coordinator_entry == 'main':
        command(['npm', 'run', 'build', '--workspace', '@agat/coordinator'])
        compiled = {path.relative_to(ROOT).as_posix(): sha(path.read_bytes())
                    for path in sorted((ROOT / 'apps/coordinator/dist').rglob('*.js'))}
        if not compiled or 'apps/coordinator/dist/server.js' not in compiled:
            raise ValueError('No compiled main output')
    evidence.mkdir(parents=True)
    plan = {'implementationCommit': implementation, 'sourceSha256': sources,
            'nodeVersion': node_version, 'candidates': 9716, 'dimensions': 768, 'candidateLimit': 10000,
            'verifyDatabaseCandidateCount': True,
            'executionMode': args.execution_mode, 'maintenanceIntervalMs': args.maintenance_interval_ms,
            'coordinatorEntry': args.coordinator_entry,
            'phases': [{'id': 'warmup', 'concurrency': 1}, {'id': 'idle', 'concurrency': 0}]
                      + [{'id': f'c{c}-r{r}', 'concurrency': c} for c in (1, 2, 4) for r in (1, 2, 3)],
            'healthIntervalMs': 100, 'maxHealthInflight': 4, 'searchRequestTimeoutSeconds': 15,
            'fixture': 'One old exact vector, all others orthogonal; values +/-1/3; synthetic, no semantic quality claim',
            'hostLoadAtStart': os.getloadavg(), 'backendOrder': ['postgresql'] if compiled else ['sqlite', 'postgresql']}
    if compiled:
        plan.update(compiledSha256=compiled,
                    poolBudget={'admittedReplicas': 2, 'admittedPoolMaxPerRole': 4, 'mainPoolMaxPerRole': 4, 'fixturePoolMaxPerRole': 1},
                    observer='Preload records actual main maintenance calls and process metrics; it does not start maintenance or serve requests.')
    write(evidence / 'plan.json', plan)
    env = {key: value for key, value in os.environ.items() if not key.startswith('AGAT_')}
    env['AGAT_KNOWLEDGE_SEARCH_MAX_CANDIDATES'] = '10000'
    children, cleanup_errors = [], []
    container = f'agat-rag-http-{os.getpid()}-{int(time.time())}'
    created = False
    failure = None
    started = time.monotonic()
    def interrupted(_number, _frame):
        raise KeyboardInterrupt('Probe interrupted')
    signal.signal(signal.SIGTERM, interrupted)
    try:
        for backend in plan['backendOrder']:
            phase_env = dict(env)
            if backend == 'postgresql':
                created = True
                image = 'postgres:17.6-alpine'
                command(['docker', 'run', '--detach', '--name', container, '--publish', '127.0.0.1::5432',
                         '--env', 'POSTGRES_DB=agat_rag_http', '--env', 'POSTGRES_USER=postgres',
                         '--env', 'POSTGRES_PASSWORD=http-admin-test', '--env', 'AGAT_POSTGRES_MIGRATION_PASSWORD=http-migration-test',
                         '--env', 'AGAT_POSTGRES_SYSTEM_PASSWORD=http-system-test', '--env', 'AGAT_POSTGRES_TENANT_PASSWORD=http-tenant-test',
                         '--volume', f'{ROOT}/deploy/k8s/docker-desktop/postgres-init.sh:/docker-entrypoint-initdb.d/10-agat.sh:ro', image])
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
                phase_env.update(AGAT_RAG_HTTP_DISPOSABLE='1',
                    AGAT_POSTGRES_MIGRATION_URL=f'postgresql://agat_migrator:http-migration-test@{address}/agat_rag_http',
                    AGAT_POSTGRES_URL=f'postgresql://agat_system:http-system-test@{address}/agat_rag_http',
                    AGAT_POSTGRES_TENANT_URL=f'postgresql://agat_tenant:http-tenant-test@{address}/agat_rag_http',
                    AGAT_POSTGRES_SSL_MODE='disable', AGAT_REGION='eu-test-1', AGAT_RESIDENCY_DOMAIN='eu-test',
                    AGAT_POSTGRES_EXPECTED_REPLICAS='2' if compiled else '1', AGAT_POSTGRES_POOL_MAX='4' if compiled else '1',
                    AGAT_POSTGRES_ADMISSION_CONCURRENCY='2',
                    AGAT_POSTGRES_ADMISSION_DURATION_MS='500', AGAT_POSTGRES_ADMISSION_MIN_OPERATIONS='10', AGAT_POSTGRES_ADMISSION_P99_MS='1000')
                write(evidence / 'postgres-image.json', {'image': image, 'imageId': command(['docker', 'inspect', '--format', '{{.Image}}', container])})
            phases, ready = [], None
            with tempfile.TemporaryDirectory(prefix='agat-rag-http-') as folder:
                with (Path(folder) / 'stderr.log').open('wb') as log:
                    process = subprocess.Popen(['node', '--import', 'tsx', 'scripts/serve-rag-http-probe.ts', folder, str(evidence), backend],
                        cwd=ROOT, env=phase_env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log,
                        text=True, bufsize=1, start_new_session=True)
                    children.append(process)
                    try:
                        ready = reply(process, timeout=300)
                        assert ready['type'] == 'ready' and ready['indexed'] == plan['candidates']
                        assert ready['databaseCandidates'] == plan['candidates']
                        if compiled:
                            assert isinstance(ready['runtimePid'], int) and ready['runtimePid'] != process.pid
                        for item in plan['phases']:
                            print(f"{backend}: {item['id']}", flush=True)
                            result = phase(process, ready, item['id'], item['concurrency'])
                            phases.append(result)
                            finished = control(process, {'type': 'finish', 'id': item['id']})
                            assert finished['type'] == 'finished' and finished['verifiedRetrievals'] == item['concurrency']
                            assert result['observedHttpConcurrency'] == item['concurrency']
                            assert all(row['valid'] for row in result['searches'])
                            assert all(entry.get('response', {}).get('valid') for entry in result['healthProbes'] if entry['outcome'] == 'dispatched')
                        process.stdin.write('{"type":"stop"}\n'); process.stdin.flush()
                        assert process.wait(timeout=10) == 0
                    finally:
                        stop(process)
                        write(evidence / f'client-{backend}.json', {'planSha256': sha((evidence / 'plan.json').read_bytes()),
                            'backend': backend, 'target': ready['target'] if ready else None,
                            'databaseCandidates': ready['databaseCandidates'] if ready else None, 'phases': phases,
                            **({'runtimePid': ready['runtimePid']} if compiled and ready else {})})
                        log.flush()
                        diagnostic = (Path(folder) / 'stderr.log').read_text(errors='replace')[-16384:]
                        diagnostic = re.sub(r'postgres(?:ql)?://[^\s\"\']+', '<disposable-postgres-url>', diagnostic)
                        diagnostic = diagnostic.replace(folder, '<temporary>').replace(str(ROOT), '<repo>')
                        (evidence / f'server-{backend}.log').write_text(diagnostic.rstrip() + '\n')
    except (Exception, KeyboardInterrupt) as exc:
        failure = {'type': type(exc).__name__, 'message': str(exc)[:300]}
    finally:
        for process in children:
            try:
                stop(process)
            except Exception as exc:
                cleanup_errors.append(type(exc).__name__)
        if created:
            try:
                command(['docker', 'rm', '--force', '--volumes', container])
            except Exception as exc:
                cleanup_errors.append(type(exc).__name__)
        result = {'status': 'observed' if failure is None and not cleanup_errors else 'incomplete',
                  'failure': failure, 'cleanupErrors': cleanup_errors, 'containerRemoved': created and not cleanup_errors,
                  'children': [{'pid': child.pid, 'exitCode': child.returncode} for child in children],
                  'elapsedMs': round((time.monotonic() - started) * 1000, 3), 'hostLoadAtFinish': os.getloadavg(),
                  'files': {path.name: sha(path.read_bytes()) for path in evidence.iterdir() if path.is_file()}}
        write(evidence / 'launcher-result.json', result)
    print(result['status'], failure, flush=True)
    return 0 if result['status'] == 'observed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
