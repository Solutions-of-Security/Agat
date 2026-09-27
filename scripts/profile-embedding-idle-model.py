#!/usr/bin/env python3
"""Pinned production/prototype model bursts separated by an explicit idle window."""
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
spec = importlib.util.spec_from_file_location('idle_model_guard', ROOT / 'scripts/profile-embedding-parent-guard.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
sys.path.insert(0, str(ROOT / 'scripts/lib'))
from embedding_idle_pool import IdleEmbeddingSessionPool

SOURCES = sorted(set(guard.SOURCES) | {'scripts/lib/embedding_idle_pool.py', 'scripts/profile-embedding-idle-model.py',
                                     'scripts/verify-embedding-idle-model.py'})


def phases():
    return [{'id': f'b{b}-c{c}-{i}', 'batch': b, 'concurrency': c, 'mode': mode, 'callsPerBurst': 8,
             'idleTimeoutSeconds': 3, 'idleSeconds': 4} for b in (1, 32) for c in (1, 4)
            for i, mode in enumerate(('keep', 'retire', 'retire', 'keep'))]


def run_phase(item):
    context, lock, children = threading.local(), threading.Lock(), []
    capacity = item['concurrency']
    pool = (IdleEmbeddingSessionPool(capacity, idle_timeout=item['idleTimeoutSeconds']) if item['mode'] == 'retire'
            else guard.embedding_transport.EmbeddingSessionPool(capacity))
    helper = str(Path(guard.embedding_transport.__file__).resolve())
    create = subprocess.Popen
    exchange = guard.embedding_transport.EmbeddingSession.request
    before_threads = {thread.ident for thread in threading.enumerate() if thread is not getattr(pool, '_maintenance', None)}

    def observe(*args, **kwargs):
        child = create(*args, **kwargs)
        if isinstance(args[0], list) and helper in args[0]:
            with lock:
                children.append((context.identity, child, args[0][args[0].index(helper) + 1:], time.monotonic_ns()))
        return child

    def request(session, *args, **kwargs):
        context.transport_started = time.monotonic_ns()
        raw = exchange(session, *args, **kwargs)
        context.transport_finished = time.monotonic_ns()
        context.helper = session.process_id
        return raw

    def snapshot():
        live = [child.pid for _, child, _, _ in children if child.poll() is None]
        memory = guard.support.rss([os.getpid(), *live])
        return {'atNs': time.monotonic_ns(), 'parentPid': os.getpid(), 'parentRssBytes': memory.pop(os.getpid()),
                'helperRssBytes': {str(pid): value for pid, value in memory.items()},
                'reapedPids': [p.pid for _, p, _, _ in children if p.returncode is not None and p.stdin.closed and p.stdout.closed],
                'descriptors': guard.support.descriptors()}

    def burst(round_id):
        barrier = threading.Barrier(capacity)

        def calls(actor):
            client = guard.support.agat_worker.LocalModelClient(guard.model.BASE + '/v1', '', embedding_timeout=30,
                embedding_request=pool.request, telemetry=guard.support.WorkerTelemetry(enabled=False))
            observed = []
            barrier.wait(timeout=5)
            for index in range(actor, item['callsPerBurst'], capacity):
                identity = f"{item['id']}-r{round_id}-{index}"
                context.identity = identity
                case = index % 4
                payload = {'model': guard.model.MODEL, 'input': guard.model.inputs(item['batch'], case)}
                row = {'id': identity, 'round': round_id, 'actor': actor, 'index': index, 'case': case,
                       'inputSha256': guard.support.sha(json.dumps(payload, ensure_ascii=False).encode()),
                       'startedNs': time.monotonic_ns()}
                vectors = client.embed(payload['model'], payload['input'])
                row.update(finishedNs=time.monotonic_ns(), helperPid=context.helper,
                           transportStartedNs=context.transport_started, transportFinishedNs=context.transport_finished)
                observed.append((row, vectors))
            return observed

        with ThreadPoolExecutor(max_workers=capacity) as executor:
            return sorted([entry for rows in executor.map(calls, range(capacity)) for entry in rows], key=lambda pair: pair[0]['index'])

    phase = {'id': item['id'], 'before': snapshot()}
    try:
        with patch('subprocess.Popen', side_effect=observe), patch.object(guard.embedding_transport.EmbeddingSession, 'request', request):
            first = burst(0)
            phase['afterFirst'] = snapshot()
            time.sleep(item['idleSeconds'])
            phase['afterIdle'] = snapshot()
            second = burst(1)
            phase['afterSecond'] = snapshot()
    finally:
        pool.close()
        phase['afterClose'] = snapshot()
    phase['maintenanceFailure'] = getattr(pool, 'maintenance_failure', None)
    phase['idleReaps'] = sum(getattr(session, 'idle_reaps', 0) for session in pool._sessions)
    phase['remainingThreads'] = sorted(thread.name for thread in threading.enumerate() if thread.ident not in before_threads)
    phase['children'] = [{'owner': owner, 'pid': child.pid, 'bornNs': born, 'arguments': arguments,
                          'returncode': child.returncode, 'stdinClosed': child.stdin.closed, 'stdoutClosed': child.stdout.closed,
                          'scriptSha256': guard.support.sha(Path(helper).read_bytes())} for owner, child, arguments, born in children]
    assert phase['before']['descriptors'] == phase['afterClose']['descriptors']
    assert phase['maintenanceFailure'] is None and phase['remainingThreads'] == []
    assert all(p.returncode == 0 and p.stdin.closed and p.stdout.closed for _, p, _, _ in children)
    return phase, first + second


def main():
    if not __debug__:
        raise RuntimeError('Assertions must be enabled')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    directory = parser.parse_args().output.resolve()
    assert directory.is_relative_to(ROOT / 'docs') and not directory.exists() and platform.system() == 'Darwin'
    commit = guard.owned.command(['git', 'rev-parse', 'HEAD'])
    sources = {}
    with tarfile.open(fileobj=io.BytesIO(subprocess.check_output(['git', 'archive', commit, '--', *SOURCES], cwd=ROOT))) as archive:
        for entry in archive:
            if entry.isfile():
                raw = archive.extractfile(entry).read()
                assert raw == (ROOT / entry.name).read_bytes(), 'Commit measured sources first'
                sources[entry.name] = guard.support.sha(raw)
    assert set(sources) == set(SOURCES)
    model_root = Path(os.environ.get('OLLAMA_MODELS', str(Path.home() / '.ollama/models')))
    assert guard.support.sha((model_root / 'manifests/registry.ollama.ai/library/embeddinggemma/latest').read_bytes()) == guard.model.DIGEST
    plan = {'schema': 'agat.embedding.idle-model.v1', 'implementationCommit': commit, 'sourceSha256': sources,
            'phases': phases(), 'warmups': [{'id': f'warm-b{b}', 'batch': b, 'concurrency': 1, 'mode': 'isolated', 'calls': 1} for b in (1, 32)],
            'platform': platform.system(), 'machine': platform.machine(), 'python': platform.python_version(),
            'cpu': guard.owned.command(['/usr/sbin/sysctl', '-n', 'machdep.cpu.brand_string']),
            'memoryBytes': int(guard.owned.command(['/usr/sbin/sysctl', '-n', 'hw.memsize'])), 'ollamaSettings': guard.SETTINGS,
            'model': guard.model.MODEL, 'modelDigest': guard.model.DIGEST, 'dimensions': 768, 'timeoutSeconds': 30,
            'budgetSeconds': 240, 'maxComponentDifference': 1e-6, 'maxCosineDistance': 1e-10,
            'cases': {f'b{b}-{case}': guard.model.inputs(b, case) for b in (1, 32) for case in range(4)}}
    directory.mkdir(parents=True)
    (directory / 'vectors').mkdir()
    result = {'status': 'incomplete', 'failure': None, 'phases': [], 'warmups': [], 'vectorFiles': {}, 'cleanupErrors': []}
    started, server, owned_pids = time.monotonic(), None, set()
    environment = {key: value for key, value in os.environ.items() if not key.startswith(('AGAT_', 'OTEL_', 'OLLAMA_'))}
    environment.update(guard.SETTINGS, OLLAMA_MODELS=str(model_root), AGAT_OTEL_ENABLED='false', OTEL_SDK_DISABLED='true',
                       NO_PROXY='127.0.0.1', no_proxy='127.0.0.1')

    def record(phase, observed, target):
        owned_pids.update(guard.owned.inventory(server.pid)[0])
        owned_pids.update(child['pid'] for child in phase['children'])
        phase['rows'] = []
        for row, vectors in observed:
            raw = guard.support.encoded(vectors)
            digest = guard.support.sha(raw)
            row['vectorSha256'] = digest
            if digest not in result['vectorFiles']:
                data = gzip.compress(raw, mtime=0)
                (directory / 'vectors' / f'{digest}.json.gz').write_bytes(data)
                result['vectorFiles'][digest] = {'sha256': guard.support.sha(data), 'bytes': len(data), 'decodedBytes': len(raw)}
            phase['rows'].append(row)
        result[target].append(phase)
        print(phase['id'], len(observed), 'completed', flush=True)

    with tempfile.TemporaryDirectory(prefix='agat-idle-model-') as folder:
        log_path = Path(folder) / 'ollama.log'
        with log_path.open('wb') as log, patch.dict(os.environ, environment, clear=True):
            with socket.socket() as bound:
                bound.bind(('127.0.0.1', 0))
                port = bound.getsockname()[1]
            guard.model.BASE = f'http://127.0.0.1:{port}'
            plan['endpoint'] = guard.model.BASE + '/v1'
            guard.support.write(directory / 'plan.json', plan)
            result['planSha256'] = guard.support.sha((directory / 'plan.json').read_bytes())
            try:
                server = subprocess.Popen(['ollama', 'serve'], stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                                          env={**environment, 'OLLAMA_HOST': f'127.0.0.1:{port}'})
                owned_pids.add(server.pid)
                deadline = time.monotonic() + 30
                while True:
                    assert server.poll() is None
                    try:
                        result['metadataBefore'] = guard.model.metadata()
                        break
                    except (OSError, ValueError):
                        assert time.monotonic() < deadline
                        time.sleep(.1)
                assert result['metadataBefore']['loaded'] == []
                for item in plan['warmups']:
                    phase, observed = guard.run_phase(item, guard.embedding_http, guard.embedding_transport)
                    record(phase, observed, 'warmups')
                for item in plan['phases']:
                    assert time.monotonic() - started < plan['budgetSeconds']
                    phase, observed = run_phase(item)
                    record(phase, observed, 'phases')
                result['metadataAfter'] = guard.model.metadata()
                assert result['metadataAfter']['model'] == result['metadataBefore']['model']
                assert result['metadataAfter']['version'] == result['metadataBefore']['version']
                guard.owned.request(port, '/api/generate', {'model': guard.model.MODEL, 'stream': False, 'keep_alive': 0})
                result['modelsAfterUnload'] = guard.owned.request(port, '/api/ps')
                assert result['modelsAfterUnload']['models'] == []
            except Exception as error:
                result['failure'] = f'{type(error).__name__}: {error}'
            finally:
                try:
                    if server is not None:
                        owned_pids.update(guard.owned.inventory(server.pid)[0])
                    guard.owned.stop(server)
                except Exception as error:
                    result['cleanupErrors'].append(type(error).__name__)
        result['logSha256'] = guard.support.sha(log_path.read_bytes())
    result.update(elapsedSeconds=time.monotonic() - started, ownedPids=sorted(owned_pids),
                  remainingOwnedPids=sorted(owned_pids & guard.owned.inventory(os.getpid())[1]),
                  ollamaPid=server.pid if server else None, ollamaExitCode=server.returncode if server else None)
    if result['cleanupErrors'] or result['remainingOwnedPids']:
        result['failure'] = result['failure'] or 'Owned process cleanup failed'
    if result['failure'] is None:
        result['status'] = 'observed'
    guard.support.write(directory / 'result.json', result)
    print(result['status'], result['failure'], flush=True)
    return int(result['status'] != 'observed')


if __name__ == '__main__':
    raise SystemExit(main())
