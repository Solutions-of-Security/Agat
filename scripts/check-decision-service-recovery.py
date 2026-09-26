#!/usr/bin/env python3
"""Bounded fault injection into three owned local runtime processes; no installed service."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import os
import re
import selectors
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_runtime.artifacts import read_json, sealed, write_new
from decision_runtime.contracts import Request, canonical_json, fingerprint, parse_json
from scripts.lib.decision_performance import hardware, profile_from_health, validate_result


def ensure(condition, message):
    if not condition: raise RuntimeError(message)


def child_processes(parent):
    found = subprocess.run(['/usr/bin/pgrep', '-P', str(parent)], capture_output=True, text=True, timeout=2)
    ensure(found.returncode in (0, 1), 'Cannot inspect owned children')
    pids = [int(value) for value in found.stdout.split()]
    if not pids: return []
    rows = subprocess.check_output(['/bin/ps', '-p', ','.join(map(str, pids)), '-o', 'pid=,ppid=,command='],
                                   text=True, timeout=2).splitlines()
    result = []
    for row in rows:
        pid, ppid, command = row.strip().split(maxsplit=2)
        ensure(int(ppid) == parent, 'Owned child parent changed')
        role = ('inference' if 'multiprocessing.spawn' in command and 'spawn_main' in command
                else 'resource_tracker' if 'multiprocessing.resource_tracker' in command else 'unknown')
        result.append({'pid': int(pid), 'parentPid': int(ppid), 'role': role})
    ensure(all(row['role'] != 'unknown' for row in result), 'Unexpected child process; refusing fault injection')
    return result


def gone(pids, timeout=3):
    def exists(pid):
        try: os.kill(pid, 0);return True
        except ProcessLookupError: return False
    deadline = time.monotonic()+timeout
    while any(exists(pid) for pid in pids) and time.monotonic() < deadline:
        time.sleep(0.05)
    return not any(exists(pid) for pid in pids)


class OwnedRuntime:
    def __init__(self, args, directory, name, deadline_ms, port=0):
        # absolute(), not resolve(): preserve the virtualenv interpreter path.
        command = [str(args.python.absolute()), '-m', 'decision_runtime', 'serve', '--manifest', str(args.manifest.resolve()),
                   '--policy', str(args.policy.resolve()), '--cache-limit-mib', '128', '--max-tokens', '2048',
                   '--inference-timeout-ms', str(deadline_ms), '--exit-on-backend-unavailable', '--port', str(port)]
        self.log = (directory/f'{name}.stderr').open('wb')
        self.process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=self.log, env={**os.environ, 'PYTHONUNBUFFERED': '1'})
        self.children = []
        self.record = {'name': name, 'parentPid': self.process.pid, 'inferenceDeadlineMs': deadline_ms}
        started = time.monotonic()
        try:
            selector = selectors.DefaultSelector();selector.register(self.process.stdout, selectors.EVENT_READ)
            with selector:
                pending = b''
                while time.monotonic()-started < 75:
                    ensure(self.process.poll() is None, 'Runtime exited before readiness')
                    for key, _ in selector.select(timeout=0.2):
                        pending += os.read(key.fd, 4096)
                        ensure(len(pending) <= 65536, 'Unexpected startup output')
                        match = re.search(rb'Agat decision runtime: http://127\.0\.0\.1:(\d+) \(shadow\)', pending)
                        if match:
                            self.port = int(match[1]);break
                    if hasattr(self, 'port'): break
                ensure(hasattr(self, 'port'), 'Runtime startup timeout')
            self.children = child_processes(self.process.pid)
            ensure(sum(row['role'] == 'inference' for row in self.children) == 1, 'Expected one owned inference process')
            status, health = self.call('GET', '/health')
            ensure(status == 200, 'Runtime was not healthy after startup')
            self.profile = profile_from_health(health)
            self.record.update(port=self.port, startupMs=round((time.monotonic()-started)*1000, 3),
                               children=self.children, profile=self.profile, profileSha256=fingerprint(self.profile))
        except BaseException:
            self.close();raise

    def call(self, method, path, body=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        try:
            headers = {'Content-Type': 'application/json'}
            if hasattr(self, 'profile'): headers['X-Agat-Decision-Profile'] = fingerprint(self.profile)
            connection.request(method, path, body=None if body is None else canonical_json(body).encode('utf-8'), headers=headers)
            response = connection.getresponse();raw = response.read(131073)
            ensure(len(raw) <= 131072, 'Response exceeded bound')
            ensure(response.getheader('Content-Length') == str(len(raw)), 'Incomplete HTTP response')
            return response.status, parse_json(raw)
        finally: connection.close()

    def score(self, request):
        started = time.monotonic()
        status, result = self.call('POST', '/v1/decisions', request.to_dict())
        validate_result(result, request, self.profile)
        return {'httpStatus': status, 'wallMs': round((time.monotonic()-started)*1000, 3),
                'completeResponse': True, 'result': result}

    def failed_exit(self):
        code = self.process.wait(timeout=5)
        ensure(code == 75, 'Failed backend did not exit with code 75')
        ensure(gone([row['pid'] for row in self.children]), 'Failed runtime left a child process')
        self.record.update(exitCode=code, ownedChildrenGone=True)

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try: self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill();self.process.wait(timeout=3)
        if self.process.stdout: self.process.stdout.close()
        self.log.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python', type=Path, default=ROOT/'.venv/decision/bin/python')
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--policy', type=Path, required=True)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    ensure(not args.output.exists(), 'Output already exists')
    request = Request.from_dict(read_json(args.request))
    ensure(request.kind in ('choice', 'boolean'), 'Recovery probe accepts Choice/Boolean diagnostics only')
    created = datetime.now(timezone.utc).isoformat();started = time.monotonic()
    runs, owned = [], []
    with tempfile.TemporaryDirectory(prefix='agat-decision-recovery-') as temporary:
        try:
            first = OwnedRuntime(args, Path(temporary), 'idle_failure', 2000);owned.append(first)
            runs.append(first.record)
            baseline = first.score(request);ensure(baseline['httpStatus'] == 200, 'Initial scoring failed')
            first.record['beforeFailure'] = baseline
            child = next(row for row in first.children if row['role'] == 'inference')
            ensure(first.process.poll() is None and child in child_processes(first.process.pid), 'Owned child changed before signal')
            failed = time.monotonic();os.kill(child['pid'], signal.SIGKILL)
            # Do not send health/score after SIGKILL: prove detection while idle.
            first.failed_exit();first.record['failureToExitMs'] = round((time.monotonic()-failed)*1000, 3)
            print('Idle backend loss: exit 75, children gone; starting a fresh owned process', flush=True)
            second = OwnedRuntime(args, Path(temporary), 'explicit_restart', 2000, first.port);owned.append(second)
            runs.append(second.record)
            ensure(second.profile == first.profile, 'Restart changed the pinned runtime profile')
            after = second.score(request);ensure(after['httpStatus'] == 200, 'Scoring after restart failed')
            stable_fields = ('status', 'reason', 'selectedOptionId', 'value', 'distribution')
            ensure(all(after['result'][key] == baseline['result'][key] for key in stable_fields), 'Decision changed after restart')
            second.record['afterRestart'] = after
            second.close();ensure(gone([row['pid'] for row in second.children]), 'Normal shutdown left children')
            second.record.update(exitCode=second.process.returncode, ownedChildrenGone=True)
            print('Restart: same endpoint/profile/distribution; testing bounded inference timeout', flush=True)
            third = OwnedRuntime(args, Path(temporary), 'inference_timeout', 100, first.port);owned.append(third)
            runs.append(third.record)
            error = third.score(request)
            ensure(error['httpStatus'] == 504 and error['result']['reason'] == 'inference_timeout', 'Expected controlled inference timeout')
            third.record['failedRequest'] = error;third.failed_exit()
        finally:
            for runtime in owned: runtime.close()
            ensure(gone([row['pid'] for runtime in owned for row in runtime.children]), 'Owned child cleanup failed')
    body = {'schemaVersion': 'agat.decision.service-recovery.v1', 'createdAt': created,
            'status': 'observed', 'qualification': 'not_assessed', 'routingEnabled': False,
            'restartManager': 'bounded-explicit-test-harness', 'nativeServiceInstalled': False,
            'host': hardware(), 'harnessSha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'inputSha256': request.input_sha256, 'elapsedMs': round((time.monotonic()-started)*1000, 3),
            'runs': runs, 'checks': {'idleFailureDetectedWithoutHttp': True, 'failedProcessExit75': True,
            'completeTimeoutResponse': True, 'sameEndpointAfterRestart': True, 'sameProfileAfterRestart': True,
            'sameDecisionAfterRestart': True, 'allOwnedProcessesStopped': True},
            'limitations': ['Three owned local processes; launchd restart itself was not exercised.',
                            'Explicit subsequent test request; failed requests are never retried by the runtime.',
                            'No sustained crash loop, boot/login recovery, real workflow or SLO measurement.']}
    write_new(args.output, sealed(body))
    print(f'Observed service recovery; all owned processes stopped; {args.output}', flush=True)
    return 0


if __name__ == '__main__': raise SystemExit(main())
