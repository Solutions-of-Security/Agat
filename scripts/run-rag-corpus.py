#!/usr/bin/env python3
"""Measure exact retrieval on pinned public repository documents and local embeddings."""
from __future__ import annotations

import argparse
import http.client
import json
import os
import platform
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.lib.rag_corpus import file_sha, make_oracle, verify_results, write_new

MODEL = 'embeddinggemma:latest'
DIGEST = '85462619ee721b466c5927d109d4cb765861907d5417b9109caebc4e614679f1'


def control(port, route, body=None):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    try:
        connection.request('GET' if body is None else 'POST', route,
                           body=None if body is None else json.dumps(body), headers={'Content-Type': 'application/json'})
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError(f'Local model control HTTP {response.status}')
        raw = response.read(524289)
        if len(raw) > 524288:
            raise ValueError('Control response too large')
        return json.loads(raw)
    finally:
        connection.close()


def stop(process):
    if process is None or getattr(process, '_agat_stopped', False):
        return
    # Every owned process starts a separate session; never signal other servers.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    # The server may exit before its runner. Stop the whole owned group too.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=5)
    process._agat_stopped = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    parser.add_argument('--source-ref', required=True, help='Full commit SHA containing public top-level docs/*.md')
    parser.add_argument('--copies', type=int, choices=(1, 4), default=1,
                        help='Four named copies create a declared synthetic scale profile (up to 10000 candidates)')
    args = parser.parse_args()
    evidence = args.evidence_dir.resolve()
    if not evidence.is_relative_to(ROOT / 'docs') or evidence == ROOT / 'docs' or evidence.exists():
        raise ValueError('Use a new directory under docs')
    installed = Path(os.environ.get('OLLAMA_MODELS', str(Path.home() / '.ollama/models'))) / 'manifests/registry.ollama.ai/library/embeddinggemma/latest'
    if file_sha(installed) != DIGEST:
        raise ValueError('Pinned embedding model must already be installed; no download is performed')
    evidence.mkdir(parents=True)
    settings = {'OLLAMA_NO_CLOUD': '1', 'OLLAMA_MAX_LOADED_MODELS': '1', 'OLLAMA_NUM_PARALLEL': '1', 'OLLAMA_CONTEXT_LENGTH': '2048'}
    container = f'agat-rag-corpus-{os.getpid()}-{int(time.time())}'
    children = []
    created_container = False
    errors = []
    failure = None
    exit_codes = {}
    begin = time.monotonic()
    def command(argv, **kwargs):
        return subprocess.run(argv, cwd=ROOT, check=True, text=True, capture_output=True, timeout=60, **kwargs).stdout.strip()
    env = {key: value for key, value in os.environ.items() if not key.startswith('AGAT_')}
    env['AGAT_RAG_CORPUS_COPIES'] = str(args.copies)
    write_new(evidence / 'launcher-plan.json', {'sourceRef': args.source_ref, 'modelDigest': DIGEST, 'settings': settings,
               'machine': {'system': platform.system(), 'release': platform.release(), 'architecture': platform.machine()},
               'maximumPhaseSeconds': 600, 'retrievalOrder': ['sqlite', 'postgresql'], 'modelDisabledDuringRetrieval': True,
               'copies': args.copies, 'candidateLimit': 5000 if args.copies == 1 else 10000})
    def interrupted(_number, _frame):
        raise KeyboardInterrupt('Corpus probe interrupted')
    signal.signal(signal.SIGTERM, interrupted)
    try:
        with tempfile.TemporaryDirectory(prefix='agat-rag-corpus-') as folder:
            data = Path(folder)
            def phase(name, option, phase_env=None):
                print(f'start: {name} {option.split(":", 1)[0]}', flush=True)
                log_path = data / f'{name}-{option.split(":", 1)[0]}.log'
                with log_path.open('wb') as log:
                    process = subprocess.Popen(['node', '--import', 'tsx', 'scripts/benchmark-rag-corpus.ts', name, str(data), str(evidence), option],
                                               cwd=ROOT, env=phase_env or env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                    children.append(process)
                    try:
                        code = process.wait(timeout=600)
                    finally:
                        stop(process)
                    exit_codes[f'{name}-{option.split(":", 1)[0]}'] = process.returncode
                lines = log_path.read_text(errors='replace').splitlines()
                diagnostic = '\n'.join(lines)[-16384:]
                diagnostic = re.sub(r'postgres(?:ql)?://[^\s\"\']+', '<disposable-postgres-url>', diagnostic)
                diagnostic = diagnostic.replace(str(data), '<temporary>').replace(str(ROOT), '<repo>')
                (evidence / f'{name}-{option.split(":", 1)[0]}.log').write_text(diagnostic.rstrip() + '\n')
                safe = [line for line in lines if line.startswith(('plan:', 'embedding:', 'ingest ', 'search '))]
                for line in safe:
                    print(line, flush=True)
                if code:
                    # Diagnostic stack only. Connection strings and startup environment are never archived.
                    raise RuntimeError(f'Phase {name} failed; exit {code}; log {log_path}')
            phase('plan', args.source_ref)
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            with (data / 'ollama.log').open('wb') as log:
                ollama = subprocess.Popen(['ollama', 'serve'], cwd=ROOT, env={**env, **settings, 'OLLAMA_HOST': f'127.0.0.1:{port}'},
                                          stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                children.append(ollama)
                deadline = time.monotonic() + 30
                while True:
                    if ollama.poll() is not None:
                        raise RuntimeError('Owned Ollama exited')
                    try:
                        version = control(port, '/api/version'); break
                    except (OSError, ValueError, http.client.HTTPException):
                        if time.monotonic() >= deadline:
                            raise TimeoutError('Ollama startup')
                        time.sleep(.1)
                phase('embed', f'http://127.0.0.1:{port}')
                loaded = control(port, '/api/ps')
                control(port, '/api/generate', {'model': MODEL, 'stream': False, 'keep_alive': 0})
                unloaded = control(port, '/api/ps')
                if unloaded.get('models') != []:
                    raise ValueError('Model remains loaded')
                stop(ollama)
                write_new(evidence / 'model-service.json', {'version': version, 'loadedBeforeUnload': loaded, 'afterUnload': unloaded,
                                                          'pid': ollama.pid, 'exitCode': ollama.returncode})
            make_oracle(data, evidence)
            phase('ingest', 'sqlite')
            phase('search', 'sqlite')
            pg_image = 'postgres:17.6-alpine'
            created_container = True
            command(['docker', 'run', '--detach', '--name', container, '--publish', '127.0.0.1::5432',
                     '--env', 'POSTGRES_DB=agat_rag_corpus', '--env', 'POSTGRES_USER=postgres', '--env', 'POSTGRES_PASSWORD=corpus-admin-test',
                     '--env', 'AGAT_POSTGRES_MIGRATION_PASSWORD=corpus-migration-test', '--env', 'AGAT_POSTGRES_SYSTEM_PASSWORD=corpus-system-test',
                     '--env', 'AGAT_POSTGRES_TENANT_PASSWORD=corpus-tenant-test',
                     '--volume', f'{ROOT}/deploy/k8s/docker-desktop/postgres-init.sh:/docker-entrypoint-initdb.d/10-agat.sh:ro', pg_image])
            address = command(['docker', 'port', container, '5432/tcp'])
            if not address.startswith('127.0.0.1:') or not address.split(':')[1].isdigit():
                raise ValueError('Non-loopback database')
            deadline = time.monotonic() + 60
            while True:
                try:
                    command(['docker', 'exec', container, 'pg_isready', '--host', '127.0.0.1', '--username', 'postgres', '--dbname', 'agat_rag_corpus']); break
                except subprocess.CalledProcessError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('PostgreSQL startup')
                    time.sleep(.25)
            pg_env = {**env, 'AGAT_RAG_CORPUS_DISPOSABLE': '1',
                      'AGAT_POSTGRES_MIGRATION_URL': f'postgresql://agat_migrator:corpus-migration-test@{address}/agat_rag_corpus',
                      'AGAT_POSTGRES_URL': f'postgresql://agat_system:corpus-system-test@{address}/agat_rag_corpus',
                      'AGAT_POSTGRES_TENANT_URL': f'postgresql://agat_tenant:corpus-tenant-test@{address}/agat_rag_corpus',
                      'AGAT_POSTGRES_SSL_MODE': 'disable', 'AGAT_REGION': 'eu-test-1', 'AGAT_RESIDENCY_DOMAIN': 'eu-test',
                      'AGAT_POSTGRES_EXPECTED_REPLICAS': '1', 'AGAT_POSTGRES_POOL_MAX': '1', 'AGAT_POSTGRES_ADMISSION_CONCURRENCY': '2',
                      'AGAT_POSTGRES_ADMISSION_DURATION_MS': '500', 'AGAT_POSTGRES_ADMISSION_MIN_OPERATIONS': '10', 'AGAT_POSTGRES_ADMISSION_P99_MS': '1000'}
            write_new(evidence / 'postgres-image.json', {'image': pg_image,
                       'imageId': command(['docker', 'inspect', '--format', '{{.Image}}', container])})
            phase('ingest', 'postgresql', pg_env)
            phase('search', 'postgresql', pg_env)
            write_new(evidence / 'verification.json', verify_results(evidence))
    except (Exception, KeyboardInterrupt) as exc:
        failure = {'type': type(exc).__name__, 'message': str(exc)[:300]}
    finally:
        for process in reversed(children):
            try:
                stop(process)
            except Exception as exc:
                errors.append(type(exc).__name__)
        if created_container:
            try:
                command(['docker', 'rm', '--force', '--volumes', container])
            except Exception as exc:
                errors.append(type(exc).__name__)
        report = {'status': 'observed' if failure is None and not errors else 'incomplete', 'failure': failure,
                  'elapsedMs': round((time.monotonic() - begin)*1000, 3), 'phaseExitCodes': exit_codes,
                  'children': [{'pid': process.pid, 'exitCode': process.returncode} for process in children], 'cleanupErrors': errors,
                  'containerRemoved': created_container and not errors,
                  'files': {file.name: file_sha(file) for file in sorted(evidence.iterdir()) if file.is_file()}}
        write_new(evidence / 'launcher-result.json', report)
    print(report['status'], failure, flush=True)
    return 0 if report['status'] == 'observed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
