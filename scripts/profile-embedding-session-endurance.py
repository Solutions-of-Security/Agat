#!/usr/bin/env python3
"""Two-minute owned-session control with repeated faults and idle resources."""
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
spec = importlib.util.spec_from_file_location('session_endurance_support', ROOT / 'scripts/profile-embedding-transport.py')
support = importlib.util.module_from_spec(spec)
spec.loader.exec_module(support)
sys.path.insert(0, str(ROOT / 'scripts/lib'))
from embedding_session import EmbeddingSession

SOURCES = ['scripts/profile-embedding-session-endurance.py', 'scripts/verify-embedding-session-endurance.py',
           'scripts/lib/embedding_session.py', 'scripts/profile-embedding-transport.py',
           'workers/agat_worker.py', 'workers/embedding_http.py', 'workers/telemetry.py',
           'workers/web_tools.py', 'workers/local_decisions.py']


def fault_for(round_index):
    if round_index in range(19, 110, 10):
        index = (round_index - 19) // 10
        return {'actor': index % 3, 'kind': ('cancel-headers', 'cancel-body', 'deadline-body')[index % 3]}
    return None


def run(result, fixture):
    sessions = [EmbeddingSession() for _ in range(4)]
    children = []
    lock = threading.Lock()
    context = threading.local()
    spawn = subprocess.Popen
    helper = str(ROOT / 'scripts/lib/embedding_session.py')

    def observe(*args, **kwargs):
        process = spawn(*args, **kwargs)
        if isinstance(args[0], list) and helper in args[0]:
            with lock:
                children.append((context.actor, context.owner, time.monotonic_ns(), process))
        return process

    def snapshot():
        memory = support.rss([os.getpid(), *[child.pid for _, _, _, child in children if child.poll() is None]])
        return {'atNs': time.monotonic_ns(), 'parentPid': os.getpid(), 'parentRssBytes': memory.pop(os.getpid()),
                'helperRssBytes': {str(pid): value for pid, value in memory.items()},
                'actorPids': [session.process_id for session in sessions], 'descriptors': support.descriptors(),
                'guardThreads': sum(thread.name == 'embedding-session-deadline' for thread in threading.enumerate())}

    def ready(actor):
        context.actor = actor
        context.owner = f'ready-{actor}'
        began = time.monotonic_ns()
        sessions[actor].warmup()
        return {'actor': actor, 'startedNs': began, 'finishedNs': time.monotonic_ns(), 'pid': sessions[actor].process_id}

    def call(actor, identity, kind, batch, dimensions):
        context.actor = actor
        context.owner = identity
        session = sessions[actor]
        row = {'id': identity, 'actor': actor, 'kind': kind, 'batch': batch, 'dimensions': dimensions}
        previous_pid = session.process_id
        entered, cancelled = threading.Event(), threading.Event()
        with fixture.lock:
            fixture.entered[identity] = entered
        canceller = None
        if kind.startswith('cancel-'):
            def cancel():
                if entered.wait(3):
                    row['cancelledNs'] = time.monotonic_ns()
                    cancelled.set()
            canceller = threading.Thread(target=cancel, name='endurance-canceller')
            canceller.start()
        payload = {'model': f'synthetic-{dimensions}', 'input': [f'{identity}/{kind}/{i}' for i in range(batch)]}
        row['inputSha256'] = support.sha(json.dumps(payload, ensure_ascii=False).encode())
        client = support.agat_worker.LocalModelClient(fixture.url, '', embedding_timeout=.25 if kind == 'deadline-body' else 5,
                                                      telemetry=support.WorkerTelemetry(enabled=False))
        row['startedNs'] = time.monotonic_ns()
        try:
            vectors = client.embed(payload['model'], payload['input'], cancelled=cancelled)
            row['finishedNs'] = time.monotonic_ns()
            row.update(outcome='completed', vectorSha256=support.sha(support.encoded(vectors)))
            assert kind == 'success' and row['vectorSha256'] == fixture.expected[batch, dimensions]
        except RuntimeError as error:
            row['finishedNs'] = time.monotonic_ns()
            outcome = 'cancelled' if 'cancelled' in str(error) else 'deadline' if 'deadline' in str(error) else 'error'
            row.update(outcome=outcome, error=str(error))
            assert outcome == ('deadline' if kind == 'deadline-body' else 'cancelled'), row
        finally:
            if canceller:
                canceller.join(3)
                assert not canceller.is_alive()
        with lock:
            owned = [child for owner, created_for, _, child in children
                     if child.pid == previous_pid or (previous_pid is None and owner == actor and created_for == identity)]
        assert len(owned) == 1
        child = owned[0]
        row.update(helperPid=child.pid, helperAfterPid=session.process_id, helperReturncode=child.poll(),
                   stdinClosed=child.stdin.closed, stdoutClosed=child.stdout.closed)
        if kind == 'success':
            assert session.process_id == child.pid and child.poll() is None
        else:
            assert session.process_id is None and child.poll() is not None and child.stdin.closed and child.stdout.closed
        return row

    def actor_round(args):
        actor, round_index, barrier = args
        barrier.wait(timeout=5)
        rows = []
        fault = fault_for(round_index)
        if fault and fault['actor'] == actor:
            rows.append(call(actor, f'r{round_index}-a{actor}-fault', fault['kind'], 1, 768))
        dimensions = 768 if round_index % 4 < 2 else 4096
        batch = 1 if round_index % 2 == 0 else 32
        rows.append(call(actor, f'r{round_index}-a{actor}-ok', 'success', batch, dimensions))
        return rows

    result['before'] = snapshot()
    try:
        with patch('subprocess.Popen', side_effect=observe), patch.object(support.agat_worker, 'request_embedding_response',
                side_effect=lambda *args, **kwargs: sessions[context.actor].request(*args, **kwargs)):
            with ThreadPoolExecutor(max_workers=4) as pool:
                result['readiness'] = list(pool.map(ready, range(4)))
                result['ready'] = snapshot()
                start = time.monotonic()
                result['scheduleStartedNs'] = time.monotonic_ns()
                for round_index in range(120):
                    if time.monotonic() - start > 180:
                        raise RuntimeError('Endurance exceeded its 180s run budget')
                    delay = start + round_index - time.monotonic()
                    if delay > 0:
                        time.sleep(delay)
                    barrier = threading.Barrier(4)
                    rows = [row for observed in pool.map(actor_round, [(actor, round_index, barrier) for actor in range(4)]) for row in observed]
                    fixture.idle()
                    shot = snapshot()
                    assert shot['descriptors'] == result['before']['descriptors'] + 8, shot
                    assert shot['guardThreads'] == 0
                    result['rounds'].append({'index': round_index, 'rows': rows, 'idle': shot})
                    if (round_index + 1) % 10 == 0:
                        print(f'{round_index + 1}/120 rounds: {len(children)} helpers started, four live, FD={shot["descriptors"]}', flush=True)
                delay = start + 120 - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                result['scheduleFinishedNs'] = time.monotonic_ns()
    finally:
        for session in sessions:
            session.close()
        result['afterClose'] = snapshot()
        result['children'] = [{'actor': actor, 'createdFor': owner, 'startedNs': began, 'pid': child.pid,
                               'returncode': child.returncode, 'stdinClosed': child.stdin.closed, 'stdoutClosed': child.stdout.closed}
                              for actor, owner, began, child in children]
        assert all(child.poll() is not None and child.stdin.closed and child.stdout.closed for _, _, _, child in children)
        assert result['before']['descriptors'] == result['afterClose']['descriptors']


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
    plan = {'schema': 'agat.embedding.session-endurance.v1', 'createdAt': datetime.now(timezone.utc).isoformat(),
            'implementationCommit': commit, 'sourceSha256': {}, 'platform': platform.system(),
            'machine': platform.machine(), 'python': platform.python_version(), 'rounds': 120, 'actors': 4,
            'scheduleSeconds': 120, 'roundPeriodSeconds': 1, 'deadlineSeconds': .25,
            'controlActor': 3, 'faults': {str(i): fault_for(i) for i in range(120) if fault_for(i)}}
    for name in SOURCES:
        raw = (ROOT / name).read_bytes()
        assert raw == subprocess.check_output(['git', 'show', f'{commit}:{name}'], cwd=ROOT), name
        plan['sourceSha256'][name] = support.sha(raw)
    support.write(directory / 'plan.json', plan)
    result = {'status': 'incomplete', 'failure': None, 'planSha256': support.sha((directory / 'plan.json').read_bytes()),
              'rounds': [], 'cleanup': False}
    fixture = support.Fixture()
    began = time.monotonic()
    try:
        with patch.dict(os.environ, {'NO_PROXY': '127.0.0.1', 'no_proxy': '127.0.0.1'}):
            run(result, fixture)
        result['status'] = 'observed'
    except Exception as error:
        result['failure'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        try:
            fixture.close()
            result['cleanup'] = True
        finally:
            result['server'] = fixture.requests
            result['fixtureErrors'] = fixture.errors
            result['elapsedSeconds'] = time.monotonic() - began
            support.write(directory / 'result.json', result)


if __name__ == '__main__':
    main()
