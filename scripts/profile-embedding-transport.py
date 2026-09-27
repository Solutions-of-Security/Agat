#!/usr/bin/env python3
"""Pinned loopback comparison and cancellation resource probe; no real model calls."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
import gc
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import platform
import select
import socket
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'workers'))
import agat_worker
import embedding_http
from telemetry import WorkerTelemetry

BASELINE = 'f9a16ea289845b9d7db142e5ee2144aad0d553ff'
SOURCES = ['scripts/profile-embedding-transport.py', 'scripts/verify-embedding-transport.py',
           'workers/agat_worker.py', 'workers/embedding_http.py', 'workers/telemetry.py', 'workers/web_tools.py', 'workers/local_decisions.py']


def sha(value):
    return hashlib.sha256(value).hexdigest()


def encoded(value):
    return json.dumps(value, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def write(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n')


def phases():
    result = []
    for dimensions in (768, 4096):
        for batch in (1, 32):
            for concurrency in (1, 4):
                for number, mode in enumerate(('direct', 'isolated', 'isolated', 'direct')):
                    result.append({'id': f'd{dimensions}-b{batch}-c{concurrency}-{number}', 'mode': mode,
                                   'kind': 'success', 'batch': batch, 'dimensions': dimensions,
                                   'concurrency': concurrency, 'calls': 8, 'measured': True})
    for concurrency in (1, 4):
        for kind in ('cancel-headers', 'cancel-body', 'deadline-body'):
            result.append({'id': f'{kind}-c{concurrency}', 'mode': 'isolated', 'kind': kind,
                           'batch': 1, 'dimensions': 768, 'concurrency': concurrency, 'calls': 8, 'measured': True})
    # Explicitly exclude one request per shape/mode from measured comparisons.
    warm = [{'id': f'warm-d{d}-b{b}-{m}', 'mode': m, 'kind': 'success', 'batch': b, 'dimensions': d,
             'concurrency': 1, 'calls': 1, 'measured': False} for d in (768, 4096) for b in (1, 32) for m in ('direct', 'isolated')]
    return warm + result


class Fixture:
    def __init__(self):
        self.lock = threading.Lock()
        self.entered = {}
        self.requests = []
        self.active = set()
        self.errors = []
        self.stopped = threading.Event()
        self.responses = {}
        self.expected = {}
        for dimensions in (768, 4096):
            for batch in (1, 32):
                vectors = [[((component % 19) + index + 1) / 37 for component in range(dimensions)] for index in range(batch)]
                self.expected[batch, dimensions] = sha(encoded(vectors))
                self.responses[batch, dimensions] = encoded({'data': [{'index': index, 'embedding': vectors[index]} for index in reversed(range(batch))]})
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *_args):
                pass

            def do_POST(self):
                identity = None
                row = None
                try:
                    assert self.path == '/v1/embeddings'
                    raw = self.rfile.read(int(self.headers['Content-Length']))
                    body = json.loads(raw)
                    identity, kind, _ = body['input'][0].split('/')
                    dimensions = int(body['model'].split('-')[-1])
                    batch = len(body['input'])
                    assert body['input'] == [f'{identity}/{kind}/{index}' for index in range(batch)]
                    response = fixture.responses[batch, dimensions]
                    row = {'id': identity, 'kind': kind, 'batch': batch, 'dimensions': dimensions,
                           'inputSha256': sha(raw), 'responseSha256': sha(response), 'responseBytes': len(response),
                           'startedNs': time.monotonic_ns()}
                    with fixture.lock:
                        assert identity not in fixture.active
                        fixture.requests.append(row)
                        fixture.active.add(identity)
                        fixture.entered[identity].set()
                    if kind == 'cancel-headers':
                        while not fixture.stopped.is_set():
                            readable, _, _ = select.select([self.connection], [], [], .03)
                            if readable and not self.connection.recv(1, socket.MSG_PEEK):
                                row['outcome'] = 'disconnected'
                                return
                    elif kind != 'success':
                        self.send_response(200)
                        self.send_header('Transfer-Encoding', 'chunked')
                        self.end_headers()
                        self.wfile.write(b'1\r\n{\r\n')
                        self.wfile.flush()
                        while not fixture.stopped.wait(.02):
                            self.wfile.write(b'1\r\n \r\n')
                            self.wfile.flush()
                    else:
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/json')
                        self.send_header('Content-Length', str(len(response)))
                        self.end_headers()
                        self.wfile.write(response)
                        self.wfile.flush()
                        row['outcome'] = 'completed'
                except (BrokenPipeError, ConnectionResetError):
                    if row is not None:
                        row['outcome'] = 'disconnected'
                except Exception as error:
                    with fixture.lock:
                        fixture.errors.append(f'{type(error).__name__}: {error}')
                finally:
                    if row is not None:
                        row['finishedNs'] = time.monotonic_ns()
                    with fixture.lock:
                        fixture.active.discard(identity)
                    self.close_connection = True

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=.02), daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}/v1'

    def idle(self):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            with self.lock:
                if not self.active:
                    assert not self.errors, self.errors
                    return
            time.sleep(.01)
        raise RuntimeError('Fixture handlers did not become idle')

    def close(self):
        self.stopped.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.idle()
        assert not self.thread.is_alive()


def rss(pids):
    raw = subprocess.check_output(['ps', '-o', 'pid=,rss=', '-p', ','.join(map(str, pids))], timeout=2, text=True)
    return {int(parts[0]): int(parts[1]) * 1024 for line in raw.splitlines() if len(parts := line.split()) == 2}


def descriptors():
    return len(os.listdir('/dev/fd'))


@contextmanager
def legacy_client():
    code = subprocess.check_output(['git', 'show', f'{BASELINE}:workers/agat_worker.py'], cwd=ROOT, timeout=10)
    # Shared imported support modules must be byte-identical to the old commit.
    for name in ('telemetry.py', 'web_tools.py', 'local_decisions.py'):
        original = subprocess.check_output(['git', 'show', f'{BASELINE}:workers/{name}'], cwd=ROOT, timeout=10)
        assert sha(original) == sha((ROOT / 'workers' / name).read_bytes())
    with tempfile.TemporaryDirectory(prefix='agat-embedding-baseline-') as folder:
        path = Path(folder) / 'baseline.py'
        path.write_bytes(code)
        spec = importlib.util.spec_from_file_location('agat_embedding_baseline', path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        yield module.LocalModelClient, sha(code)
        del sys.modules[spec.name]


def phase(item, fixture, client_class):
    children = []
    rows = []
    samples = []
    errors = []
    lock = threading.Lock()
    context = threading.local()
    process_create = subprocess.Popen
    helper_path = str(Path(embedding_http.__file__).resolve())
    done = threading.Event()
    started = time.monotonic_ns()

    def spawn(*args, **kwargs):
        child = process_create(*args, **kwargs)
        if isinstance(args[0], list) and args[0][-1] == helper_path:
            with lock:
                children.append((context.identity, child))
        return child

    def sample():
        while not done.is_set():
            with lock:
                pids = [child.pid for _, child in children if child.poll() is None]
            try:
                memory = rss([os.getpid(), *pids])
                samples.append({'atNs': time.monotonic_ns(), 'parentRssBytes': memory.pop(os.getpid()),
                                'helperRssBytes': {str(pid): value for pid, value in memory.items()}})
            except Exception as error:
                errors.append(f'{type(error).__name__}: {error}')
            done.wait(.05)

    def one(index):
        identity = f"{item['id']}-{index}"
        context.identity = identity
        entered = threading.Event()
        with fixture.lock:
            fixture.entered[identity] = entered
        cancelled = threading.Event()
        row = {'id': identity, 'index': index}
        canceller = None
        if item['kind'].startswith('cancel-'):
            def cancel():
                if entered.wait(3):
                    row['cancelledNs'] = time.monotonic_ns()
                    cancelled.set()
            canceller = threading.Thread(target=cancel)
            canceller.start()
        options = {'embedding_timeout': .3 if item['kind'] == 'deadline-body' else 5} if item['mode'] == 'isolated' else {}
        client = client_class(fixture.url, '', telemetry=WorkerTelemetry(enabled=False), **options)
        inputs = [f"{identity}/{item['kind']}/{index}" for index in range(item['batch'])]
        payload = {'model': f"synthetic-{item['dimensions']}", 'input': inputs}
        row['inputSha256'] = sha(json.dumps(payload, ensure_ascii=False).encode())
        row['startedNs'] = time.monotonic_ns()
        try:
            extra = {'cancelled': cancelled} if item['mode'] == 'isolated' else {}
            vectors = client.embed(payload['model'], inputs, **extra)
            row['finishedNs'] = time.monotonic_ns()
            row.update(outcome='completed', vectorSha256=sha(encoded(vectors)))
            assert item['kind'] == 'success'
            assert row['vectorSha256'] == fixture.expected[item['batch'], item['dimensions']]
        except RuntimeError as error:
            row['finishedNs'] = time.monotonic_ns()
            row.update(outcome='cancelled' if 'cancelled' in str(error) else 'deadline' if 'deadline' in str(error) else 'error', error=str(error))
            assert row['outcome'] == ('deadline' if item['kind'] == 'deadline-body' else 'cancelled'), row
        finally:
            if canceller:
                canceller.join(3)
                assert not canceller.is_alive()
        with lock:
            owned = [child for owner, child in children if owner == identity]
            row['children'] = [{'pid': child.pid, 'returncode': child.returncode,
                                'stdinClosed': child.stdin.closed, 'stdoutClosed': child.stdout.closed} for child in owned]
            rows.append(row)
        assert len(owned) == (1 if item['mode'] == 'isolated' else 0)
        assert all(child.poll() is not None and child.stdin.closed and child.stdout.closed for child in owned)

    before_fds = descriptors()
    with patch('subprocess.Popen', side_effect=spawn):
        sampler = threading.Thread(target=sample)
        sampler.start()
        try:
            with ThreadPoolExecutor(max_workers=item['concurrency']) as executor:
                list(executor.map(one, range(item['calls'])))
            fixture.idle()
        finally:
            done.set()
            sampler.join(3)
            for _, child in children:
                if child.poll() is None:
                    child.kill()
                    child.communicate()
                    errors.append('A helper required emergency cleanup')
    assert not sampler.is_alive() and not errors, errors
    gc.collect()
    after_fds = descriptors()
    assert before_fds == after_fds, (before_fds, after_fds)
    return {'id': item['id'], 'startedNs': started, 'finishedNs': time.monotonic_ns(),
            'fdBefore': before_fds, 'fdAfter': after_fds, 'rows': sorted(rows, key=lambda row: row['index']),
            'samples': samples, 'idleRssBytes': rss([os.getpid()])[os.getpid()]}


def main():
    if not __debug__:
        raise RuntimeError('Assertions must be enabled')
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / 'docs'):
        parser.error('Evidence must be stored under /docs')
    output.mkdir(parents=True, exist_ok=False)
    source_commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    plan = {'schema': 'agat.embedding.transport-profile.v1', 'createdAt': datetime.now(timezone.utc).isoformat(),
            'implementationCommit': source_commit, 'baselineCommit': BASELINE, 'sourceSha256': {},
            'platform': platform.system(), 'machine': platform.machine(), 'python': platform.python_version(),
            'cpuCount': os.cpu_count(), 'vectorFormula': '((component % 19) + index + 1) / 37', 'sampleIntervalMs': 50, 'deadlineSeconds': .3, 'phases': phases()}
    for name in SOURCES:
        code = (ROOT / name).read_bytes()
        assert code == subprocess.check_output(['git', 'show', f'{source_commit}:{name}'], cwd=ROOT), name
        plan['sourceSha256'][name] = sha(code)
    result = {'status': 'incomplete', 'failure': None, 'phases': [], 'server': [], 'cleanup': False}
    fixture = None
    overall = time.monotonic()
    try:
        with legacy_client() as (direct, baseline_sha), patch.dict(os.environ, {'NO_PROXY': '127.0.0.1', 'no_proxy': '127.0.0.1'}):
            plan['baselineWorkerSha256'] = baseline_sha
            write(output / 'plan.json', plan)
            result['planSha256'] = sha((output / 'plan.json').read_bytes())
            fixture = Fixture()
            result['fdBefore'] = descriptors()
            for item in plan['phases']:
                if time.monotonic() - overall > 180:
                    raise RuntimeError('Profile exceeded its 180s budget')
                value = phase(item, fixture, direct if item['mode'] == 'direct' else agat_worker.LocalModelClient)
                result['phases'].append(value)
                print(f"{item['id']}: {len(value['rows'])} calls, fd {value['fdBefore']}->{value['fdAfter']}", flush=True)
            result['fdAfter'] = descriptors()
            assert result['fdBefore'] == result['fdAfter']
            result['status'] = 'observed'
    except Exception as error:
        result['failure'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        if fixture:
            try:
                fixture.close()
                result['cleanup'] = True
            except Exception as error:
                result['status'] = 'incomplete'
                result['failure'] = f'Cleanup: {type(error).__name__}: {error}'
            result['server'] = fixture.requests
        result['elapsedSeconds'] = time.monotonic() - overall
        write(output / 'result.json', result)
    assert result['cleanup'] and result['status'] == 'observed'


if __name__ == '__main__':
    main()
