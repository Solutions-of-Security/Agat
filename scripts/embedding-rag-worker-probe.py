#!/usr/bin/env python3
"""Observe the ordinary worker entry point without changing its execution path."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'workers'))
import agat_worker
import embedding_http
import embedding_transport


def main():
    if len(sys.argv) < 2 or Path(sys.argv[1]).resolve() != ROOT / 'workers/agat_worker.py':
        raise ValueError('This probe only observes the ordinary worker entry point')
    sys.argv = sys.argv[1:]
    target = Path(os.environ['AGAT_EMBEDDING_PROBE_OUTPUT']).resolve()
    if not target.is_relative_to(ROOT / 'docs') or target.exists():
        raise ValueError('Use a new probe output under docs')
    config = agat_worker.parse_args()
    if config.embedding_transport not in {'isolated', 'session'} or config.concurrency != 2:
        raise ValueError('This profile requires an explicit transport and two worker slots')
    # The fixture fixes both endpoints to its own ephemeral loopback proxies.
    from urllib.parse import urlsplit
    for endpoint in (config.coordinator_url, config.model_base_url):
        url = urlsplit(endpoint)
        if url.scheme != 'http' or url.hostname != '127.0.0.1' or not url.port:
            raise ValueError('Probe endpoints must be explicit loopback URLs')
    lock = threading.Lock()
    done = threading.Event()
    calls, requests, retired, children, samples, errors = [], [], [], [], [], []
    active = {}
    embed_code = agat_worker.LocalModelClient.embed.__code__
    isolated_code = embedding_http._request_response.__code__
    session_code = embedding_transport.EmbeddingSession._exchange.__code__
    retire_code = embedding_transport.EmbeddingSession._retire.__code__
    spawn_code = subprocess.Popen.__init__.__code__
    codes = {embed_code, isolated_code, session_code, retire_code, spawn_code}
    paths = {str(ROOT / 'workers/embedding_http.py'), str(ROOT / 'workers/embedding_transport.py')}

    def observe(frame, event, _result):
        if event not in {'call', 'return'} or frame.f_code not in codes:
            return
        code = frame.f_code
        if code is isolated_code and frame.f_locals.get("endpoint_name") != "Embedding":
            return
        thread = threading.get_native_id()
        now = time.monotonic_ns()
        with lock:
            if code is embed_code:
                if event == 'call':
                    payload = {'model': frame.f_locals['model'], 'input': frame.f_locals['inputs']}
                    row = {'startedNs': now, 'threadId': thread, 'items': len(payload['input']),
                           'inputSha256': [hashlib.sha256(value.encode()).hexdigest() for value in payload['input']]}
                    active[thread] = row
                    calls.append(row)
                else:
                    row = active.pop(thread)
                    row['finishedNs'] = now
                    row['completed'] = isinstance(_result, list) and len(_result) == row['items']
            elif code is spawn_code and event == 'return':
                child = frame.f_locals['self']
                command = frame.f_locals.get('args')
                if thread in active and isinstance(command, list) and any(arg in paths for arg in command):
                    children.append({'pid': child.pid, 'startedNs': now, 'process': child,
                                     'callerStartedNs': active.get(thread, {}).get('startedNs')})
            elif code in {isolated_code, session_code, retire_code} and event == 'return':
                child = frame.f_locals.get('process')
                if child is not None:
                    row = {'atNs': now, 'pid': child.pid, 'returncode': child.returncode,
                           'stdinClosed': child.stdin.closed, 'stdoutClosed': child.stdout.closed,
                           'callerStartedNs': active.get(thread, {}).get('startedNs')}
                    (retired if code is retire_code else requests).append(row)

    def sample():
        while not done.is_set():
            with lock:
                live = [row['pid'] for row in children if row['process'].poll() is None]
            try:
                raw = subprocess.check_output(['ps', '-o', 'pid=,rss=', '-p', ','.join(map(str, [os.getpid(), *live]))], text=True, timeout=2)
                memory = {int(parts[0]): int(parts[1]) * 1024 for line in raw.splitlines() if len(parts := line.split()) == 2}
                samples.append({'atNs': time.monotonic_ns(), 'workerRssBytes': memory.pop(os.getpid()),
                                'helperRssBytes': {str(pid): value for pid, value in memory.items()}})
            except Exception as error:
                errors.append(type(error).__name__)
            done.wait(.1)

    # Sampling uses its own short-lived ps child; those are excluded from the
    # helper inventory. Embedding helpers are observed at Popen return inside
    # their embed call; primary HTTP helpers are outside embedding resource totals.
    before = len(os.listdir('/dev/fd'))
    sampler = threading.Thread(target=sample, name='embedding-rag-rss')
    began = time.monotonic_ns()
    exit_code = None
    failure = None
    try:
        sys.setprofile(observe)
        threading.setprofile(observe)
        sampler.start()
        exit_code = agat_worker.worker_loop(config)
    except BaseException as error:
        failure = type(error).__name__
        raise
    finally:
        sys.setprofile(None)
        threading.setprofile(None)
        done.set()
        sampler.join(3)
        final_children = [{'pid': row['pid'], 'startedNs': row['startedNs'], 'callerStartedNs': row['callerStartedNs'],
                           'returncode': row['process'].poll(), 'stdinClosed': row['process'].stdin.closed,
                           'stdoutClosed': row['process'].stdout.closed} for row in children]
        after = len(os.listdir('/dev/fd'))
        report = {'schema': 'agat.embedding.rag-worker.v1', 'transport': config.embedding_transport,
                  'pid': os.getpid(), 'python': sys.version.split()[0], 'concurrency': config.concurrency,
                  'startedNs': began, 'finishedNs': time.monotonic_ns(), 'exitCode': exit_code, 'failure': failure,
                  'calls': calls, 'requests': requests, 'retired': retired, 'children': final_children,
                  'samples': samples, 'sampleErrors': errors, 'activeCalls': len(active),
                  'fdBefore': before, 'fdAfter': after,
                  'liveThreads': [thread.name for thread in threading.enumerate() if thread is not threading.main_thread()]}
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('x') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
