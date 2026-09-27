#!/usr/bin/env python3
"""Pinned real-model comparison of disposable and prestarted embedding helpers."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import gzip
import importlib.util
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('session_model_support', ROOT / 'scripts/profile-embedding-model-transport.py')
model_support = importlib.util.module_from_spec(spec)
spec.loader.exec_module(model_support)
support = model_support.support
sys.path.insert(0, str(ROOT / 'scripts/lib'))
from embedding_session import EmbeddingSession

MODEL, DIGEST, BASE = model_support.MODEL, model_support.DIGEST, model_support.BASE
SOURCES = ['scripts/profile-embedding-session-model.py', 'scripts/verify-embedding-session-model.py',
           'scripts/lib/embedding_session.py', 'scripts/profile-embedding-model-transport.py',
           'scripts/profile-embedding-transport.py', 'workers/agat_worker.py', 'workers/embedding_http.py',
           'workers/telemetry.py', 'workers/web_tools.py', 'workers/local_decisions.py']


def phases():
    warm = [{'id': f'warm-b{b}-{m}', 'batch': b, 'concurrency': 1, 'mode': m, 'calls': 1, 'measured': False}
            for b in (1, 32) for m in ('isolated', 'session')]
    return warm + [{'id': f'b{b}-c{c}-{i}', 'batch': b, 'concurrency': c, 'mode': m, 'calls': 8, 'measured': True}
                   for b in (1, 32) for c in (1, 4) for i, m in enumerate(('isolated', 'session', 'session', 'isolated'))]


def run_phase(item):
    context = threading.local()
    children = []
    lock = threading.Lock()
    spawn = subprocess.Popen
    original_transport = support.agat_worker.request_embedding_response
    helpers = {str(ROOT / 'workers/embedding_http.py'), str(ROOT / 'scripts/lib/embedding_session.py')}
    sessions = [EmbeddingSession() for _ in range(item['concurrency'])] if item['mode'] == 'session' else []
    barrier = threading.Barrier(item['concurrency'])

    def observe(*args, **kwargs):
        child = spawn(*args, **kwargs)
        if isinstance(args[0], list) and any(arg in helpers for arg in args[0]):
            with lock:
                children.append((context.owner, child))
        return child

    def transport(*args, **kwargs):
        if sessions:
            return sessions[context.actor].request(*args, **kwargs)
        return original_transport(*args, **kwargs)

    def snapshot():
        memory = support.rss([os.getpid(), *[child.pid for _, child in children if child.poll() is None]])
        return {'atNs': time.monotonic_ns(), 'parentPid': os.getpid(), 'parentRssBytes': memory.pop(os.getpid()),
                'helperRssBytes': {str(pid): value for pid, value in memory.items()}, 'descriptors': support.descriptors()}

    def ready(actor):
        context.owner = f'actor-{actor}'
        began = time.monotonic_ns()
        sessions[actor].warmup()
        return {'actor': actor, 'startedNs': began, 'finishedNs': time.monotonic_ns(), 'pid': sessions[actor].process_id}

    def calls(actor):
        context.actor = actor
        observed = []
        client = support.agat_worker.LocalModelClient(BASE + '/v1', '', embedding_timeout=30,
                                                      telemetry=support.WorkerTelemetry(enabled=False))
        barrier.wait(timeout=5)
        for index in range(actor, item['calls'], item['concurrency']):
            identity = f"{item['id']}-{index}"
            context.owner = identity
            case = index % 4
            contents = model_support.inputs(item['batch'], case)
            row = {'id': identity, 'index': index, 'case': case, 'actor': actor,
                   'inputSha256': support.sha(json.dumps({'model': MODEL, 'input': contents}, ensure_ascii=False).encode()),
                   'startedNs': time.monotonic_ns()}
            vectors = client.embed(MODEL, contents)
            row['finishedNs'] = time.monotonic_ns()
            with lock:
                owned = [child for owner, child in children if owner == (f'actor-{actor}' if sessions else identity)]
            assert len(owned) == 1
            row['helperPid'] = owned[0].pid
            observed.append((row, vectors))
        return observed

    phase = {'id': item['id'], 'before': snapshot(), 'readiness': []}
    try:
        with patch('subprocess.Popen', side_effect=observe), patch.object(support.agat_worker, 'request_embedding_response', side_effect=transport):
            with ThreadPoolExecutor(max_workers=item['concurrency']) as pool:
                phase['readiness'] = list(pool.map(ready, range(item['concurrency']))) if sessions else []
                phase['ready'] = snapshot()
                observed = sorted([row for rows in pool.map(calls, range(item['concurrency'])) for row in rows], key=lambda item: item[0]['index'])
                phase['afterCalls'] = snapshot()
    finally:
        for session in sessions:
            session.close()
        phase['afterClose'] = snapshot()
    phase['children'] = [{'owner': owner, 'pid': child.pid, 'returncode': child.returncode,
                          'stdinClosed': child.stdin.closed, 'stdoutClosed': child.stdout.closed} for owner, child in children]
    assert all(child.returncode == 0 and child.stdin.closed and child.stdout.closed for _, child in children)
    assert phase['before']['descriptors'] == phase['afterClose']['descriptors']
    return phase, observed


def main():
    if not __debug__:
        raise RuntimeError('Assertions must be enabled')
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    directory = args.output.resolve()
    if not directory.is_relative_to(ROOT / 'docs'):
        parser.error('Evidence must be under /docs')
    directory.mkdir(parents=True, exist_ok=False)
    (directory / 'vectors').mkdir()
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    plan = {'schema': 'agat.embedding.session-model.v1', 'createdAt': datetime.now(timezone.utc).isoformat(),
            'implementationCommit': commit, 'sourceSha256': {}, 'phases': phases(),
            'platform': platform.system(), 'machine': platform.machine(), 'python': platform.python_version(),
            'model': MODEL, 'modelDigest': DIGEST, 'endpoint': BASE + '/v1', 'dimensions': 768,
            'timeoutSeconds': 30, 'maxComponentDifference': 1e-6, 'maxCosineDistance': 1e-10,
            'cases': {f'b{b}-{case}': model_support.inputs(b, case) for b in (1, 32) for case in range(4)}}
    for name in SOURCES:
        raw = (ROOT / name).read_bytes()
        assert raw == subprocess.check_output(['git', 'show', f'{commit}:{name}'], cwd=ROOT), name
        plan['sourceSha256'][name] = support.sha(raw)
    result = {'status': 'incomplete', 'failure': None, 'phases': [], 'vectorFiles': {}}
    started = time.monotonic()
    try:
        with patch.dict(os.environ, {'NO_PROXY': '127.0.0.1', 'no_proxy': '127.0.0.1'}):
            plan['metadataBefore'] = model_support.metadata()
            support.write(directory / 'plan.json', plan)
            result['planSha256'] = support.sha((directory / 'plan.json').read_bytes())
            for item in plan['phases']:
                if time.monotonic() - started > 180:
                    raise RuntimeError('Real-model session profile exceeded 180 seconds')
                phase, observed = run_phase(item)
                phase['rows'] = []
                # Hashing, compression and persistence are outside all timed calls.
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
            result['metadataAfter'] = model_support.metadata()
            assert result['metadataAfter']['model'] == plan['metadataBefore']['model']
            assert result['metadataAfter']['version'] == plan['metadataBefore']['version']
            result['status'] = 'observed'
    except Exception as error:
        result['failure'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        result['elapsedSeconds'] = time.monotonic() - started
        support.write(directory / 'result.json', result)


if __name__ == '__main__':
    main()
