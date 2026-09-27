#!/usr/bin/env python3
"""Compare pinned before/after parent guards against an owned local model server."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import gzip
import importlib.util
import io
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BASELINE = '218ecb5f6b70289df0c89ebbc7275de57a202684'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


model = load('guard_model_support', ROOT / 'scripts/profile-embedding-model-transport.py')
owned = load('guard_owned_support', ROOT / 'scripts/profile-embedding-rag.py')
support = model.support
import embedding_http
import embedding_transport

SOURCES = ['scripts/profile-embedding-parent-guard.py', 'scripts/verify-embedding-parent-guard.py',
           'scripts/profile-embedding-model-transport.py', 'scripts/profile-embedding-transport.py',
           'scripts/profile-embedding-rag.py', 'workers/agat_worker.py', 'workers/embedding_http.py',
           'workers/embedding_transport.py', 'workers/telemetry.py', 'workers/web_tools.py', 'workers/local_decisions.py']
TRANSPORTS = ['workers/embedding_http.py', 'workers/embedding_transport.py']
COMMON = ['workers/agat_worker.py', 'workers/telemetry.py', 'workers/web_tools.py', 'workers/local_decisions.py']
SETTINGS = {'OLLAMA_NO_CLOUD': '1', 'OLLAMA_MAX_LOADED_MODELS': '1', 'OLLAMA_NUM_PARALLEL': '1',
            'OLLAMA_CONTEXT_LENGTH': '2048', 'OLLAMA_KEEP_ALIVE': '5m'}


def phases():
    warm = [{'id': f'warm-b{b}-{m}-{g}', 'batch': b, 'concurrency': 1, 'mode': m, 'guard': g,
             'calls': 1, 'measured': False} for b in (1, 32) for m in ('isolated', 'session') for g in (False, True)]
    return warm + [{'id': f'b{b}-c{c}-{m}-{i}', 'batch': b, 'concurrency': c, 'mode': m, 'guard': g,
                    'calls': 8, 'measured': True} for b in (1, 32) for c in (1, 4) for m in ('isolated', 'session')
                   for i, g in enumerate((False, True, True, False))]


def run_phase(item, http_module, session_module):
    context, lock, children = threading.local(), threading.Lock(), []
    spawn = subprocess.Popen
    helper = Path(session_module.__file__ if item['mode'] == 'session' else http_module.__file__).resolve()
    helper_sha = support.sha(helper.read_bytes())
    sessions = [session_module.EmbeddingSession() for _ in range(item['concurrency'])] if item['mode'] == 'session' else []
    barrier = threading.Barrier(item['concurrency'])

    def observe(*args, **kwargs):
        child = spawn(*args, **kwargs)
        if isinstance(args[0], list) and str(helper) in args[0]:
            with lock:
                children.append((context.owner, child, list(args[0][args[0].index(str(helper)) + 1:])))
        return child

    def snapshot():
        memory = support.rss([os.getpid(), *[child.pid for _, child, _ in children if child.poll() is None]])
        return {'atNs': time.monotonic_ns(), 'parentPid': os.getpid(), 'parentRssBytes': memory.pop(os.getpid()),
                'helperRssBytes': {str(pid): value for pid, value in memory.items()}, 'descriptors': support.descriptors()}

    def ready(actor):
        context.owner = f'actor-{actor}'
        began = time.monotonic_ns()
        sessions[actor].warmup()
        return {'actor': actor, 'startedNs': began, 'finishedNs': time.monotonic_ns(), 'pid': sessions[actor].process_id}

    def calls(actor):
        observed = []
        client = support.agat_worker.LocalModelClient(model.BASE + '/v1', '', embedding_timeout=30,
            telemetry=support.WorkerTelemetry(enabled=False),
            embedding_request=sessions[actor].request if sessions else http_module.request_embedding_response)
        barrier.wait(timeout=5)
        for index in range(actor, item['calls'], item['concurrency']):
            identity = f"{item['id']}-{index}"
            context.owner = identity
            case = index % 4
            contents = model.inputs(item['batch'], case)
            row = {'id': identity, 'index': index, 'case': case, 'actor': actor,
                   'inputSha256': support.sha(json.dumps({'model': model.MODEL, 'input': contents}, ensure_ascii=False).encode()),
                   'startedNs': time.monotonic_ns()}
            vectors = client.embed(model.MODEL, contents)
            row['finishedNs'] = time.monotonic_ns()
            with lock:
                matching = [child for owner, child, _ in children if owner == (f'actor-{actor}' if sessions else identity)]
            assert len(matching) == 1
            row['helperPid'] = matching[0].pid
            observed.append((row, vectors))
        return observed

    phase = {'id': item['id'], 'before': snapshot(), 'readiness': []}
    try:
        with patch('subprocess.Popen', side_effect=observe):
            with ThreadPoolExecutor(max_workers=item['concurrency']) as pool:
                phase['readiness'] = list(pool.map(ready, range(item['concurrency']))) if sessions else []
                phase['ready'] = snapshot()
                observed = sorted([row for rows in pool.map(calls, range(item['concurrency'])) for row in rows], key=lambda row: row[0]['index'])
                phase['afterCalls'] = snapshot()
    finally:
        for session in sessions:
            session.close()
        phase['afterClose'] = snapshot()
    phase['children'] = [{'owner': owner, 'pid': child.pid, 'returncode': child.returncode,
                          'arguments': arguments, 'scriptSha256': helper_sha,
                          'stdinClosed': child.stdin.closed, 'stdoutClosed': child.stdout.closed}
                         for owner, child, arguments in children]
    assert all(child.returncode == 0 and child.stdin.closed and child.stdout.closed for _, child, _ in children)
    assert phase['before']['descriptors'] == phase['afterClose']['descriptors']
    return phase, observed


def main():
    if not __debug__:
        raise RuntimeError('Assertions must be enabled')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    directory = parser.parse_args().output.resolve()
    assert directory.is_relative_to(ROOT / 'docs') and not directory.exists()
    assert platform.system() == 'Darwin', 'Scope: installed-model macOS experiment'
    commit = owned.command(['git', 'rev-parse', 'HEAD'])
    sources = {}
    with tarfile.open(fileobj=io.BytesIO(subprocess.check_output(['git', 'archive', commit, '--', *SOURCES], cwd=ROOT))) as archive:
        for entry in archive:
            if entry.isfile():
                raw = archive.extractfile(entry).read()
                assert raw == (ROOT / entry.name).read_bytes(), 'Commit measured sources first'
                sources[entry.name] = support.sha(raw)
    assert set(sources) == set(SOURCES)
    baseline = {name: subprocess.check_output(['git', 'show', f'{BASELINE}:{name}'], cwd=ROOT) for name in TRANSPORTS + COMMON}
    assert all(baseline[name] == (ROOT / name).read_bytes() for name in COMMON), 'Only transport may change'
    model_root = Path(os.environ.get('OLLAMA_MODELS', str(Path.home() / '.ollama/models')))
    assert support.sha((model_root / 'manifests/registry.ollama.ai/library/embeddinggemma/latest').read_bytes()) == model.DIGEST
    plan = {'schema': 'agat.embedding.parent-guard.v1', 'implementationCommit': commit, 'sourceSha256': sources,
            'baselineCommit': BASELINE, 'baselineSha256': {name: support.sha(raw) for name, raw in baseline.items()},
            'phases': phases(), 'platform': platform.system(), 'machine': platform.machine(), 'python': platform.python_version(),
            'cpu': owned.command(['/usr/sbin/sysctl', '-n', 'machdep.cpu.brand_string']),
            'memoryBytes': int(owned.command(['/usr/sbin/sysctl', '-n', 'hw.memsize'])), 'ollamaSettings': SETTINGS,
            'model': model.MODEL, 'modelDigest': model.DIGEST, 'dimensions': 768, 'timeoutSeconds': 30,
            'budgetSeconds': 240, 'maxComponentDifference': 1e-6, 'maxCosineDistance': 1e-10,
            'cases': {f'b{b}-{case}': model.inputs(b, case) for b in (1, 32) for case in range(4)}}
    directory.mkdir(parents=True)
    (directory / 'vectors').mkdir()
    result = {'status': 'incomplete', 'failure': None, 'phases': [], 'vectorFiles': {}, 'cleanupErrors': []}
    started, server, owned_pids = time.monotonic(), None, set()
    # Avoid ambient application/telemetry and Ollama options influencing this fixture.
    environment = {key: value for key, value in os.environ.items() if not key.startswith(('AGAT_', 'OTEL_', 'OLLAMA_'))}
    environment.update(SETTINGS, OLLAMA_MODELS=str(model_root), AGAT_OTEL_ENABLED='false', OTEL_SDK_DISABLED='true',
                       NO_PROXY='127.0.0.1', no_proxy='127.0.0.1')
    with tempfile.TemporaryDirectory(prefix='agat-embedding-guard-') as folder:
        folder = Path(folder)
        for name in TRANSPORTS:
            (folder / Path(name).name).write_bytes(baseline[name])
        old_http = load('before_guard_http', folder / 'embedding_http.py')
        with patch.dict(sys.modules, {'embedding_http': old_http}):
            old_session = load('before_guard_session', folder / 'embedding_transport.py')
        with (folder / 'ollama.log').open('wb') as log, patch.dict(os.environ, environment, clear=True):
            with socket.socket() as bound:
                bound.bind(('127.0.0.1', 0))
                port = bound.getsockname()[1]
            model.BASE = f'http://127.0.0.1:{port}'
            plan['endpoint'] = model.BASE + '/v1'
            # Freeze the complete design before starting or contacting the server.
            support.write(directory / 'plan.json', plan)
            result['planSha256'] = support.sha((directory / 'plan.json').read_bytes())
            try:
                server = subprocess.Popen(['ollama', 'serve'], stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                                          env={**environment, 'OLLAMA_HOST': f'127.0.0.1:{port}'})
                owned_pids.add(server.pid)
                deadline = time.monotonic() + 30
                while True:
                    assert server.poll() is None, 'Owned Ollama exited'
                    try:
                        result['metadataBefore'] = model.metadata()
                        break
                    except (OSError, ValueError):
                        assert time.monotonic() < deadline, 'Owned server startup timed out'
                        time.sleep(.1)
                assert result['metadataBefore']['loaded'] == []
                for item in plan['phases']:
                    assert time.monotonic() - started < plan['budgetSeconds'], 'Profile budget exceeded'
                    modules = (embedding_http, embedding_transport) if item['guard'] else (old_http, old_session)
                    phase, observed = run_phase(item, *modules)
                    owned_pids.update(owned.inventory(server.pid)[0])
                    owned_pids.update(child['pid'] for child in phase['children'])
                    phase['rows'] = []
                    # Persist full vectors outside timed requests and outside concurrency.
                    for row, vectors in observed:
                        raw = support.encoded(vectors)
                        digest = support.sha(raw)
                        row['vectorSha256'] = digest
                        if digest not in result['vectorFiles']:
                            data = gzip.compress(raw, mtime=0)
                            (directory / 'vectors' / f'{digest}.json.gz').write_bytes(data)
                            result['vectorFiles'][digest] = {'sha256': support.sha(data), 'bytes': len(data), 'decodedBytes': len(raw)}
                        phase['rows'].append(row)
                    result['phases'].append(phase)
                    print(f"{item['id']}: {len(observed)} completed", flush=True)
                result['metadataAfter'] = model.metadata()
                assert result['metadataAfter']['model'] == result['metadataBefore']['model']
                assert result['metadataAfter']['version'] == result['metadataBefore']['version']
                owned.request(port, '/api/generate', {'model': model.MODEL, 'stream': False, 'keep_alive': 0})
                result['modelsAfterUnload'] = owned.request(port, '/api/ps')
                assert result['modelsAfterUnload']['models'] == []
            except Exception as error:
                result['failure'] = f'{type(error).__name__}: {error}'
            finally:
                try:
                    if server is not None:
                        owned_pids.update(owned.inventory(server.pid)[0])
                    owned.stop(server)
                except Exception as error:
                    result['cleanupErrors'].append(type(error).__name__)
        result['logSha256'] = support.sha((folder / 'ollama.log').read_bytes())
    result.update(elapsedSeconds=time.monotonic() - started, ownedPids=sorted(owned_pids),
                  remainingOwnedPids=sorted(owned_pids & owned.inventory(os.getpid())[1]),
                  ollamaPid=server.pid if server else None, ollamaExitCode=server.returncode if server else None)
    if result['cleanupErrors'] or result['remainingOwnedPids']:
        result['failure'] = result['failure'] or 'Owned process cleanup failed'
    if result['failure'] is None:
        result['status'] = 'observed'
    support.write(directory / 'result.json', result)
    print(result['status'], result['failure'], flush=True)
    return int(result['status'] != 'observed')


if __name__ == '__main__':
    raise SystemExit(main())
