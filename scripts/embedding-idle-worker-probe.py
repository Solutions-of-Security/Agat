#!/usr/bin/env python3
"""Observe ordinary worker calls and explicit snapshots over a private control pipe."""
from __future__ import annotations

import gzip
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'workers'))
import agat_worker
import embedding_transport


def main():
    target = Path(os.environ['AGAT_IDLE_PROBE_OUTPUT']).resolve()
    if not target.is_relative_to(ROOT / 'docs') or target.exists():
        raise ValueError('Use a new probe output under docs')
    config = agat_worker.parse_args()
    if config.embedding_transport != 'session' or config.concurrency != 1:
        raise ValueError('This experiment requires one session worker slot')
    for address in (config.coordinator_url, config.model_base_url):
        parsed = urlsplit(address)
        if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port or parsed.username or parsed.password:
            raise ValueError('Owned loopback endpoints required')
    lock = threading.Lock()
    calls, children, retired, snapshots, leases, completions = [], [], [], {}, [], []
    active = {}
    embed_code = agat_worker.LocalModelClient.embed.__code__
    session_code = embedding_transport.EmbeddingSession._exchange.__code__
    retire_code = embedding_transport.EmbeddingSession._retire.__code__
    spawn_code = subprocess.Popen.__init__.__code__
    execute_code = agat_worker.execute_knowledge_lease.__code__
    request_code = agat_worker.CoordinatorClient.request.__code__
    codes = {embed_code, session_code, retire_code, spawn_code, execute_code, request_code}
    helper_path = str(ROOT / 'workers/embedding_transport.py')

    def emit(kind, value):
        print('AGAT_IDLE_PROBE ' + json.dumps({'kind': kind, **value}), flush=True)

    def observe(frame, event, result):
        if event not in {'call', 'return'} or frame.f_code not in codes:
            return
        code, now, tid = frame.f_code, time.monotonic_ns(), threading.get_native_id()
        with lock:
            if code is embed_code:
                if event == 'call':
                    row = {'startedNs': now, 'model': frame.f_locals['model'], 'inputs': frame.f_locals['inputs']}
                    active[tid] = row; calls.append(row)
                else:
                    row = active.pop(tid)
                    row.update(finishedNs=now, vectors=result)
            elif code is spawn_code and event == 'return':
                command, process = frame.f_locals.get('args'), frame.f_locals['self']
                if isinstance(command, list) and helper_path in command:
                    children.append({'pid': process.pid, 'bornNs': now, 'callerStartedNs': active[tid]['startedNs'], 'process': process})
            elif code is session_code and event == 'return' and tid in active:
                process = frame.f_locals.get('process')
                if process:
                    active[tid].update(helperPid=process.pid, transportFinishedNs=now)
            elif code is retire_code and event == 'return':
                process = frame.f_locals.get('process')
                if process:
                    retired.append({'pid': process.pid, 'atNs': now, 'returncode': process.returncode,
                                    'stdinClosed': process.stdin.closed, 'stdoutClosed': process.stdout.closed})
            elif code is execute_code:
                row = {'event': event, 'atNs': now, 'leaseId': frame.f_locals['lease']['leaseId']}
                leases.append(row)
                emit('lease', row)
            elif code is request_code and event == 'return':
                route = frame.f_locals['path']
                if route.startswith('/api/v1/workers/knowledge/') and route.endswith(('/complete', '/fail')):
                    response = frame.f_locals.get('response')
                    completions.append({'path': route, 'atNs': now, 'status': response.status if response else None,
                                        'body': frame.f_locals['body']})

    def snapshot(name):
        if name not in {'before', 'afterFirst', 'afterIdle', 'afterSecond'} or name in snapshots:
            raise ValueError('Unexpected snapshot identity')
        with lock:
            live = [row['pid'] for row in children if row['process'].poll() is None]
        raw = subprocess.check_output(['ps', '-o', 'pid=,rss=', '-p', ','.join(map(str, [os.getpid(), *live]))], text=True, timeout=2)
        memory = {int(parts[0]): int(parts[1]) * 1024 for line in raw.splitlines() if len(parts := line.split()) == 2}
        with lock:
            value = {'atNs': time.monotonic_ns(), 'workerRssBytes': memory.pop(os.getpid()),
                     'helperRssBytes': {str(pid): size for pid, size in memory.items()},
                     'reaped': [row['pid'] for row in retired], 'descriptors': len(os.listdir('/dev/fd'))}
            snapshots[name] = value
        emit('snapshot', {'name': name, **value})

    control_errors = []
    def control():
        try:
            for line in sys.stdin:
                item = json.loads(line)
                if set(item) != {'snapshot'}:
                    raise ValueError('Invalid control command')
                snapshot(item['snapshot'])
        except Exception as error:
            control_errors.append(type(error).__name__)
            emit('control_error', {'error': type(error).__name__})

    before = len(os.listdir('/dev/fd'))
    controller = threading.Thread(target=control, name='embedding-probe-control', daemon=True)
    began, exit_code, failure = time.monotonic_ns(), None, None
    try:
        sys.setprofile(observe); threading.setprofile(observe)
        controller.start()
        exit_code = agat_worker.worker_loop(config)
    except BaseException as error:
        failure = type(error).__name__
        raise
    finally:
        sys.setprofile(None); threading.setprofile(None)
        # The owner closes its command pipe before requesting graceful shutdown.
        controller.join(2)
        final = [{k: v for k, v in row.items() if k != 'process'} | {
            'returncode': row['process'].poll(), 'stdinClosed': row['process'].stdin.closed,
            'stdoutClosed': row['process'].stdout.closed} for row in children]
        report = {'schema': 'agat.embedding.idle-worker-probe.v1', 'pid': os.getpid(), 'python': sys.version.split()[0],
                  'idleTimeout': config.embedding_idle_timeout, 'concurrency': config.concurrency,
                  'startedNs': began, 'finishedNs': time.monotonic_ns(), 'exitCode': exit_code, 'failure': failure,
                  'calls': calls, 'children': final, 'retired': retired, 'snapshots': snapshots,
                  'leases': leases, 'completions': completions, 'controlErrors': control_errors,
                  'activeCalls': len(active), 'fdBefore': before, 'fdAfter': len(os.listdir('/dev/fd')),
                  'liveThreads': [t.name for t in threading.enumerate() if t is not threading.main_thread()]}
        target.write_bytes(gzip.compress(json.dumps(report, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode(), mtime=0))
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
