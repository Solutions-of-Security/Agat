#!/usr/bin/env python3
"""Paired loopback profile of disposable and explicitly prestarted sessions."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
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
spec = importlib.util.spec_from_file_location('session_profile_support', ROOT / 'scripts/profile-embedding-transport.py')
support = importlib.util.module_from_spec(spec)
spec.loader.exec_module(support)
sys.path.insert(0, str(ROOT / 'scripts/lib'))
from embedding_session import EmbeddingSession

SOURCES = ['scripts/profile-embedding-session.py', 'scripts/verify-embedding-session.py',
           'scripts/lib/embedding_session.py', 'scripts/profile-embedding-transport.py',
           'workers/agat_worker.py', 'workers/embedding_http.py', 'workers/telemetry.py',
           'workers/web_tools.py', 'workers/local_decisions.py']


def phases():
    return [{'id': f'd{d}-b{b}-c{c}-{i}', 'dimensions': d, 'batch': b, 'concurrency': c,
             'mode': mode, 'calls': 8} for d in (768, 4096) for b in (1, 32) for c in (1, 4)
            for i, mode in enumerate(('isolated', 'session', 'session', 'isolated'))]


def run_phase(item, fixture):
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
        if item['mode'] == 'session':
            return sessions[context.actor].request(*args, **kwargs)
        return original_transport(*args, **kwargs)

    def snapshot():
        pids = [child.pid for _, child in children if child.poll() is None]
        memory = support.rss([os.getpid(), *pids])
        return {'atNs': time.monotonic_ns(), 'parentPid': os.getpid(),
                'parentRssBytes': memory.pop(os.getpid()),
                'helperRssBytes': {str(pid): count for pid, count in memory.items()},
                'descriptors': support.descriptors()}

    def warmup(actor):
        context.owner = f'actor-{actor}'
        began = time.monotonic_ns()
        sessions[actor].warmup()
        return {'actor': actor, 'startedNs': began, 'finishedNs': time.monotonic_ns(), 'pid': sessions[actor].process_id}

    def actor_calls(actor):
        context.actor = actor
        rows = []
        barrier.wait(timeout=5)
        for index in range(actor, item['calls'], item['concurrency']):
            identity = f"{item['id']}-{index}"
            context.owner = identity
            inputs = [f'{identity}/success/{i}' for i in range(item['batch'])]
            model = f"synthetic-{item['dimensions']}"
            with fixture.lock:
                fixture.entered[identity] = threading.Event()
            client = support.agat_worker.LocalModelClient(fixture.url, '', embedding_timeout=5,
                                                          telemetry=support.WorkerTelemetry(enabled=False))
            row = {'id': identity, 'index': index, 'actor': actor,
                   'inputSha256': support.sha(json.dumps({'model': model, 'input': inputs}, ensure_ascii=False).encode()),
                   'startedNs': time.monotonic_ns()}
            vectors = client.embed(model, inputs)
            row['finishedNs'] = time.monotonic_ns()
            row['vectorSha256'] = support.sha(support.encoded(vectors))
            assert row['vectorSha256'] == fixture.expected[item['batch'], item['dimensions']]
            with lock:
                owned = [child for owner, child in children if owner == (f'actor-{actor}' if sessions else identity)]
            assert len(owned) == 1
            row['helperPid'] = owned[0].pid
            rows.append(row)
        return rows

    result = {'id': item['id'], 'before': snapshot(), 'rows': [], 'warmup': []}
    try:
        with patch('subprocess.Popen', side_effect=observe), patch.object(support.agat_worker, 'request_embedding_response', side_effect=transport):
            with ThreadPoolExecutor(max_workers=item['concurrency']) as pool:
                result['warmup'] = list(pool.map(warmup, range(item['concurrency']))) if sessions else []
                result['ready'] = snapshot()
                result['rows'] = sorted([row for rows in pool.map(actor_calls, range(item['concurrency'])) for row in rows], key=lambda row: row['index'])
                fixture.idle()
                result['afterCalls'] = snapshot()
    finally:
        for session in sessions:
            session.close()
        result['afterClose'] = snapshot()
    result['children'] = [{'owner': owner, 'pid': child.pid, 'returncode': child.returncode,
                           'stdinClosed': child.stdin.closed, 'stdoutClosed': child.stdout.closed} for owner, child in children]
    assert all(child.returncode == 0 and child.stdin.closed and child.stdout.closed for _, child in children)
    assert result['before']['descriptors'] == result['afterClose']['descriptors']
    return result


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
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    plan = {'schema': 'agat.embedding.session.v1', 'createdAt': datetime.now(timezone.utc).isoformat(),
            'implementationCommit': commit, 'sourceSha256': {}, 'phases': phases(),
            'platform': platform.system(), 'machine': platform.machine(), 'python': platform.python_version(),
            'timeoutSeconds': 5, 'sessionRequestBytes': 2097152, 'responseBytes': 8388608}
    for path in SOURCES:
        raw = (ROOT / path).read_bytes()
        assert raw == subprocess.check_output(['git', 'show', f'{commit}:{path}'], cwd=ROOT), path
        plan['sourceSha256'][path] = support.sha(raw)
    support.write(directory / 'plan.json', plan)
    result = {'status': 'incomplete', 'failure': None, 'planSha256': support.sha((directory / 'plan.json').read_bytes()), 'phases': []}
    started = time.monotonic()
    fixture = support.Fixture()
    try:
        with patch.dict(os.environ, {'NO_PROXY': '127.0.0.1', 'no_proxy': '127.0.0.1'}):
            for item in plan['phases']:
                if time.monotonic() - started > 120:
                    raise RuntimeError('Session profile exceeded 120 seconds')
                result['phases'].append(run_phase(item, fixture))
                print(f"{item['id']}: {item['mode']} completed", flush=True)
        result['status'] = 'observed'
    except Exception as error:
        result['failure'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        fixture.close()
        result['requests'] = fixture.requests
        result['fixtureErrors'] = fixture.errors
        result['elapsedSeconds'] = time.monotonic() - started
        support.write(directory / 'result.json', result)


if __name__ == '__main__':
    main()
