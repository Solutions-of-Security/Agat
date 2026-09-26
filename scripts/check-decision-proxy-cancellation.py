#!/usr/bin/env python3
"""Verify actual worker cancellation reaches MLX through the observer proxy."""

import argparse
import hashlib
import http.client
import os
import selectors
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_runtime.artifacts import read_json, sealed, verify_seal, write_new
from decision_runtime.contracts import Policy, Request, fingerprint, parse_json
from decision_runtime.engine import DecisionEngine
from decision_runtime.isolated import IsolatedBackend, mlx_factory
from decision_runtime.server import make_server
from scripts.lib.decision_performance import validate_result
from workers.local_decisions import LocalDecisionClient


def require(value, message):
    if not value: raise ValueError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('manifest', 'policy', 'source-plan', 'plan-output', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    require(not args.plan_output.exists() and not args.output.exists() and args.plan_output.resolve() != args.output.resolve(), 'Use new paths')
    source = verify_seal(read_json(args.source_plan), 'agat.decision.synthetic-robustness-plan.v1')
    case = next(c for c in source['cases'] if c['request']['id'] == 'robust-single-2048-front')
    require(source['labelSource'] == 'synthetic-authored', 'Authored input required')
    request = Request.from_dict(case['request']);require(request.input_sha256 == case['inputSha256'], 'Input binding mismatch')
    started = time.monotonic();failure = None;stage = 'startup';plan = None;proxy = None
    baseline = outcome = retirement_ms = None
    with IsolatedBackend(mlx_factory, {'manifest': str(args.manifest.resolve()), 'max_tokens': 2048, 'cache_limit_mib': 128},
                         timeout_ms=5000) as backend:
        engine = DecisionEngine(backend, Policy.from_dict(read_json(args.policy)))
        with make_server(engine, 0) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
            try:
                proxy = subprocess.Popen(['node', '--import', 'tsx', 'scripts/probe-decision-shadow-proxy.ts',
                                          '--target', f'http://127.0.0.1:{server.server_port}'], cwd=ROOT,
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
                with selectors.DefaultSelector() as selector:
                    selector.register(proxy.stdout, selectors.EVENT_READ)
                    require(bool(selector.select(timeout=5)), 'Proxy startup timeout')
                    ready = parse_json(proxy.stdout.readline())
                require(ready['pid'] == proxy.pid and type(ready['port']) is int and 0 < ready['port'] < 65536, 'Invalid proxy readiness')
                files = ['scripts/check-decision-proxy-cancellation.py', 'scripts/probe-decision-shadow-proxy.ts',
                         'scripts/lib/decision-shadow-proxy.ts', 'workers/local_decisions.py']
                plan = sealed({'schemaVersion': 'agat.decision.proxy-cancellation-plan.v1', 'createdAt': datetime.now(timezone.utc).isoformat(),
                               'sourcePlanSha256': source['sha256'], 'request': request.to_dict(), 'inputSha256': request.input_sha256,
                               'inputTokens': 2048, 'profile': engine.profile(), 'profileSha256': fingerprint(engine.profile()),
                               'workerTimeoutMs': 100, 'retirementWaitMs': 1500, 'proxyLifetimeMs': 30000,
                               'routingEnabled': False, 'files': {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in files}})
                write_new(args.plan_output, plan)
                client = LocalDecisionClient(f'http://127.0.0.1:{ready["port"]}')
                shadow = {'profile': 'local_decision_shadow_v2', 'timeoutMs': 5000,
                          'profileSha256': plan['profileSha256'], 'request': request.to_dict()}
                stage = 'control';baseline = client.decide(shadow)
                require(set(baseline) == {'result'}, 'Control transport failed')
                validate_result(baseline['result'], request, engine.profile())
                require(baseline['result']['status'] == 'ok' and baseline['result']['inputTokens'] == 2048, 'Control failed or truncated')
                stage = 'cancel_through_proxy';begin = time.monotonic()
                outcome = client.decide({**shadow, 'timeoutMs': plan['workerTimeoutMs']})
                require(outcome == {'status': 'unavailable', 'reason': 'timeout'}, 'Expected a worker deadline')
                deadline = time.monotonic() + plan['retirementWaitMs'] / 1000
                while True:
                    diag = backend.diagnostics()
                    if not diag['available'] and diag['childExitCode'] is not None: break
                    require(time.monotonic() < deadline, 'Proxy did not propagate cancellation before server deadline')
                    time.sleep(.01)
                retirement_ms = round((time.monotonic() - begin) * 1000, 3)
                require(diag['stopReason'] == 'inference_cancelled', 'Wrong stop reason')
                stage = 'unavailable_health'
                conn = http.client.HTTPConnection('127.0.0.1', ready['port'], timeout=2)
                try:
                    conn.request('GET', '/health');response = conn.getresponse();body = parse_json(response.read(131072))
                    require(response.status == 503 and body['status'] == 'unavailable'
                            and body['profileSha256'] == plan['profileSha256'], 'Proxy lost readiness/profile')
                finally: conn.close()
            except Exception as exc: failure = {'stage': stage, 'type': type(exc).__name__}
            finally:
                if proxy:
                    if proxy.stdin: proxy.stdin.close()
                    try: proxy.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proxy.terminate()
                        try: proxy.wait(timeout=2)
                        except subprocess.TimeoutExpired: proxy.kill();proxy.wait(timeout=2)
                    if proxy.stdout: proxy.stdout.close()
                server.shutdown();thread.join(timeout=2);backend.close()
            diagnostics = backend.diagnostics()
    child_gone = False
    try: os.kill(diagnostics['childPid'], 0)
    except ProcessLookupError: child_gone = True
    clean = child_gone and proxy is not None and proxy.poll() is not None
    result = sealed({'schemaVersion': 'agat.decision.proxy-cancellation-result.v1', 'createdAt': datetime.now(timezone.utc).isoformat(),
                     'status': 'observed' if failure is None and clean else 'incomplete', 'failure': failure,
                     'planSha256': plan['sha256'] if plan else None, 'elapsedMs': round((time.monotonic() - started) * 1000, 3),
                     'baseline': baseline, 'workerOutcome': outcome, 'requestStartToObservedRetirementMs': retirement_ms,
                     'backendDiagnostics': diagnostics, 'managerPid': os.getpid(), 'proxyPid': proxy.pid if proxy else None,
                     'proxyExitCode': proxy.returncode if proxy else None, 'ownedChildrenStopped': clean,
                     'routingEnabled': False, 'qualifiedForRouting': False,
                     'limitations': ['One synthetic transport probe with real MLX; not an accuracy or latency-SLO estimate.',
                                     'The Node observer is diagnostic infrastructure, not a newly installed production proxy.']})
    write_new(args.output, result);print(result['status'], failure, retirement_ms)
    return 0 if result['status'] == 'observed' else 1


if __name__ == '__main__': raise SystemExit(main())
