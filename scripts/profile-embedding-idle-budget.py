#!/usr/bin/env python3
"""Measure owned idle pool CPU/RSS before/after guard with Darwin native counters."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import ctypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import platform
import subprocess
from socketserver import TCPServer
import sys
import tempfile
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('idle_guard_support', ROOT / 'scripts/profile-embedding-parent-guard.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
SOURCES = sorted(set(guard.SOURCES) | {'scripts/profile-embedding-idle-budget.py', 'scripts/verify-embedding-idle-budget.py'})


class Usage(ctypes.Structure):
    _fields_ = [('uuid', ctypes.c_uint8 * 16)] + [(name, ctypes.c_uint64) for name in (
        'user', 'system', 'packageWakeups', 'interruptWakeups', 'pageins', 'wired', 'rss', 'footprint', 'start', 'exit')]


class Timebase(ctypes.Structure):
    _fields_ = [('numer', ctypes.c_uint32), ('denom', ctypes.c_uint32)]


class DarwinCounters:
    def __init__(self):
        if sys.platform != 'darwin':
            raise RuntimeError('Native counters require macOS')
        self.lib = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
        self.lib.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
        self.lib.proc_pid_rusage.restype = ctypes.c_int
        system = ctypes.CDLL('/usr/lib/libSystem.B.dylib')
        system.mach_timebase_info.argtypes = [ctypes.POINTER(Timebase)]
        system.mach_timebase_info.restype = ctypes.c_int
        timebase = Timebase()
        assert system.mach_timebase_info(ctypes.byref(timebase)) == 0 and timebase.numer > 0 and timebase.denom > 0
        self.timebase = {'numer': timebase.numer, 'denom': timebase.denom}
        assert ctypes.sizeof(Usage) == 96

    def sample(self, pid):
        usage = Usage()
        if self.lib.proc_pid_rusage(pid, 0, ctypes.byref(usage)) != 0:
            raise OSError(ctypes.get_errno(), 'Owned process resource sampling failed')
        return {'pid': pid, 'atNs': time.monotonic_ns(), 'uuid': bytes(usage.uuid).hex(), 'startTicks': usage.start,
                'exitTicks': usage.exit, 'userTicks': usage.user, 'systemTicks': usage.system,
                'rssBytes': usage.rss, 'footprintBytes': usage.footprint,
                'packageWakeups': usage.packageWakeups, 'interruptWakeups': usage.interruptWakeups}

    def calibration(self):
        before = self.sample(os.getpid())
        cpu_before = time.process_time_ns()
        while time.process_time_ns() - cpu_before < 200_000_000:
            pass
        cpu_after = time.process_time_ns()
        after = self.sample(os.getpid())
        ticks = after['userTicks'] + after['systemTicks'] - before['userTicks'] - before['systemTicks']
        native = ticks * self.timebase['numer'] / self.timebase['denom']
        assert abs(native - (cpu_after - cpu_before)) <= 1_000_000 + .02 * (cpu_after - cpu_before)
        return {'before': before, 'after': after, 'processTimeBeforeNs': cpu_before, 'processTimeAfterNs': cpu_after}


def phases():
    return [{'id': f'c{c}-{index}', 'capacity': c, 'guard': enabled, 'idleSeconds': 10}
            for c in (1, 4, 32) for index, enabled in enumerate((False, True, True, False))]


class Fixture:
    def __init__(self, capacity):
        self.barriers = [threading.Barrier(capacity) for _ in range(2)]
        self.rows, self.errors = [], []
        lock, fixture = threading.Lock(), self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *_args):
                pass

            def do_POST(self):
                try:
                    assert self.path == '/v1/embeddings'
                    body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                    assert body['model'] == 'idle-fixture' and len(body['input']) == 1
                    round_id, actor = map(int, body['input'][0].split('/'))
                    assert round_id in (0, 1) and 0 <= actor < capacity
                    row = {'round': round_id, 'actor': actor, 'enteredNs': time.monotonic_ns()}
                    fixture.barriers[round_id].wait(timeout=10)
                    raw = json.dumps({'data': [{'index': 0, 'embedding': [round_id + 1, actor + 1]}]}).encode()
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                    self.wfile.flush()
                    row['finishedNs'] = time.monotonic_ns()
                    with lock:
                        fixture.rows.append(row)
                except Exception as error:
                    with lock:
                        fixture.errors.append(type(error).__name__)
                finally:
                    self.close_connection = True

        class Server(ThreadingHTTPServer):
            request_queue_size = 128

            def server_bind(self):
                # Literal loopback fixture: avoid HTTPServer's getfqdn(), which
                # opens a cached Darwin resolver socket on first use.
                TCPServer.server_bind(self)
                self.server_name, self.server_port = self.server_address[:2]

        self.server = Server(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=.01), name='idle-fixture')
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}/v1'

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        assert not self.thread.is_alive()


def run_phase(item, module, counters):
    capacity, children, context = item['capacity'], [], threading.local()
    create, lock = subprocess.Popen, threading.Lock()
    helper = str(Path(module.__file__).resolve())
    pool = module.EmbeddingSessionPool(capacity)
    before_threads = {thread.ident for thread in threading.enumerate()}
    phase = {'id': item['id'], 'fdBefore': guard.support.descriptors(), 'parentPid': os.getpid()}
    fixture = Fixture(capacity)

    def observe(*args, **kwargs):
        process = create(*args, **kwargs)
        if isinstance(args[0], list) and helper in args[0]:
            with lock:
                children.append((context.actor, process, args[0][args[0].index(helper) + 1:]))
        return process

    def call(round_id, actor):
        context.actor = actor
        client = guard.support.agat_worker.LocalModelClient(fixture.url, '', embedding_timeout=15,
            telemetry=guard.support.WorkerTelemetry(enabled=False), embedding_request=pool.request)
        row = {'round': round_id, 'actor': actor, 'startedNs': time.monotonic_ns()}
        row['vectors'] = client.embed('idle-fixture', [f'{round_id}/{actor}'])
        row['finishedNs'] = time.monotonic_ns()
        assert row['vectors'] == [[round_id + 1, actor + 1]]
        return row

    try:
        with patch('subprocess.Popen', side_effect=observe):
            with ThreadPoolExecutor(max_workers=capacity) as executor:
                phase['beforeCalls'] = list(executor.map(lambda actor: call(0, actor), range(capacity)))
            assert len(children) == capacity and all(p.poll() is None for _, p, _ in children)
            time.sleep(.1)  # Let server handler and deadline threads finish before idle counters.
            phase['fdIdle'] = guard.support.descriptors()
            phase['idleBefore'] = [counters.sample(p.pid) for _, p, _ in children]
            time.sleep(item['idleSeconds'])
            phase['idleAfter'] = [counters.sample(p.pid) for _, p, _ in children]
            with ThreadPoolExecutor(max_workers=capacity) as executor:
                phase['afterCalls'] = list(executor.map(lambda actor: call(1, actor), range(capacity)))
            assert len(children) == capacity, 'Idle helpers must be reused without replacement'
    finally:
        phase['closeStartedNs'] = time.monotonic_ns()
        pool.close()
        phase['closeFinishedNs'] = time.monotonic_ns()
        fixture.close()
        phase['fdAfterClose'] = guard.support.descriptors()
        phase['remainingThreads'] = sorted(thread.name for thread in threading.enumerate() if thread.ident not in before_threads)
    phase['children'] = [{'pid': p.pid, 'actor': actor, 'arguments': args, 'scriptSha256': guard.support.sha(Path(helper).read_bytes()),
                          'returncode': p.returncode, 'stdinClosed': p.stdin.closed, 'stdoutClosed': p.stdout.closed}
                         for actor, p, args in children]
    phase['serverRows'], phase['serverErrors'] = fixture.rows, fixture.errors
    assert phase['fdBefore'] == phase['fdAfterClose'] and phase['remainingThreads'] == [], (phase['fdBefore'], phase['fdAfterClose'], phase['remainingThreads'])
    assert all(p.returncode == 0 and p.stdin.closed and p.stdout.closed for _, p, _ in children)
    assert len(fixture.rows) == capacity * 2 and not fixture.errors
    return phase


def main():
    if not __debug__:
        raise RuntimeError('Assertions must be enabled')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    directory = parser.parse_args().output.resolve()
    assert directory.is_relative_to(ROOT / 'docs') and not directory.exists()
    counters = DarwinCounters()
    commit = guard.owned.command(['git', 'rev-parse', 'HEAD'])
    sources = {}
    for name in SOURCES:
        raw = subprocess.check_output(['git', 'show', f'{commit}:{name}'], cwd=ROOT)
        assert raw == (ROOT / name).read_bytes(), 'Commit all measured sources first'
        sources[name] = guard.support.sha(raw)
    baseline = {name: subprocess.check_output(['git', 'show', f'{guard.BASELINE}:{name}'], cwd=ROOT) for name in guard.TRANSPORTS}
    # The existing model qualification pins the common client for this baseline.
    for name in guard.COMMON:
        assert (ROOT / name).read_bytes() == subprocess.check_output(['git', 'show', f'{guard.BASELINE}:{name}'], cwd=ROOT)
    plan = {'schema': 'agat.embedding.idle-budget.v1', 'implementationCommit': commit, 'sourceSha256': sources,
            'baselineCommit': guard.BASELINE, 'baselineSha256': {name: guard.support.sha(raw) for name, raw in baseline.items()},
            'phases': phases(), 'timebase': counters.timebase, 'platform': platform.system(), 'release': platform.release(),
            'machine': platform.machine(), 'python': platform.python_version(), 'cpu': guard.owned.command(['/usr/sbin/sysctl', '-n', 'machdep.cpu.brand_string']),
            'memoryBytes': int(guard.owned.command(['/usr/sbin/sysctl', '-n', 'hw.memsize'])), 'budgetSeconds': 180,
            'counter': 'proc_pid_rusage/RUSAGE_INFO_V0', 'abiBytes': ctypes.sizeof(Usage), 'model': 'synthetic-no-inference'}
    directory.mkdir(parents=True)
    guard.support.write(directory / 'plan.json', plan)
    result = {'status': 'incomplete', 'failure': None, 'phases': [], 'planSha256': guard.support.sha((directory / 'plan.json').read_bytes())}
    began = time.monotonic()
    try:
        result['calibration'] = counters.calibration()
        with tempfile.TemporaryDirectory(prefix='agat-idle-before-') as folder:
            folder = Path(folder)
            for name, raw in baseline.items():
                (folder / Path(name).name).write_bytes(raw)
            old_http = guard.load('idle_before_http', folder / 'embedding_http.py')
            with patch.dict(sys.modules, {'embedding_http': old_http}):
                old_session = guard.load('idle_before_session', folder / 'embedding_transport.py')
            with patch.dict(os.environ, {'NO_PROXY': '127.0.0.1', 'no_proxy': '127.0.0.1'}):
                for item in plan['phases']:
                    assert time.monotonic() - began < plan['budgetSeconds']
                    phase = run_phase(item, guard.embedding_transport if item['guard'] else old_session, counters)
                    result['phases'].append(phase)
                    print(item['id'], 'guard', item['guard'], 'helpers', len(phase['children']), 'closed', flush=True)
        result['status'] = 'observed'
    except Exception as error:
        result['failure'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        result['elapsedSeconds'] = time.monotonic() - began
        guard.support.write(directory / 'result.json', result)


if __name__ == '__main__':
    main()
