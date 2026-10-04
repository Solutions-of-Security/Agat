#!/usr/bin/env python3
"""Exercise one restart of a temporary, uniquely named user LaunchAgent, then boot it out."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import http.client
import os
import platform
import re
import runpy
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_runtime.artifacts import read_json, sealed, write_new
from decision_runtime.contracts import Request, canonical_json, fingerprint
from scripts.lib.decision_performance import profile_from_health
from scripts.lib.decision_service import launch_agent, write_launch_agent
from scripts.lib.decision_monitoring import PrometheusObservation

# Reuse the already-tested UTF-8 HTTP probe and owned-child identification.
recovery = runpy.run_path(str(ROOT/'scripts/check-decision-service-recovery.py'))
OwnedRuntime, child_processes, ensure, gone = (recovery[name] for name in ('OwnedRuntime', 'child_processes', 'ensure', 'gone'))


def launchctl(*arguments):
    return subprocess.run(['/bin/launchctl', *arguments], capture_output=True, text=True, timeout=10)


def service_info(target, diagnostic_path=None):
    state = launchctl('print', target)
    if diagnostic_path is not None:
        # Human-readable launchctl output can include symbolic sysexits names.
        # Keep the native output private so a parser failure is diagnosable.
        diagnostic_path.write_text(state.stdout+state.stderr)
        diagnostic_path.chmod(0o600)
    if state.returncode:
        label = target.rsplit('/', 1)[-1]
        if state.returncode == 113 and f'Could not find service "{label}"' in state.stderr:
            return None
        raise RuntimeError('Cannot inspect temporary LaunchAgent registration')
    result = {}
    for key in ('pid', 'runs', 'last exit code'):
        suffix = r'(?:: [A-Z][A-Z0-9_]*)?' if key == 'last exit code' else ''
        match = re.search(r'^\s*'+re.escape(key)+r' = (\d+)'+suffix+r'\s*$', state.stdout, re.MULTILINE)
        if match: result[key] = int(match[1])
    return result


def wait_for_exit(target, code, snapshots, diagnostic_path, timeout=5):
    # PID disappearance and launchd's publication of the exit status need not
    # be observed atomically. Wait only for that status, never restart the job.
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        state = service_info(target, diagnostic_path)
        if not snapshots or snapshots[-1] != state:
            snapshots.append(state)
        ensure(state is not None, 'Temporary LaunchAgent disappeared before its exit status')
        if 'last exit code' in state:
            ensure(state['last exit code'] == code, f'launchd recorded an unexpected exit code: {state["last exit code"]}')
            return state
        time.sleep(0.1)
    raise RuntimeError(f'launchd did not record runtime exit {code} within {timeout} seconds')


def wait_for_removal(target, diagnostic_path, timeout=8):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        if service_info(target, diagnostic_path) is None:
            return True
        time.sleep(0.1)
    return False


def harness_fingerprints():
    sources = ['scripts/check-decision-launchd.py', 'scripts/check-decision-service-recovery.py',
               'scripts/lib/decision_service.py', 'scripts/lib/decision_performance.py',
               'scripts/lib/decision_monitoring.py',
               'docs/qualification/local-decisions/shadow/observability/prometheus-3.13.4.json',
               'docs/qualification/local-decisions/shadow/observability/alerts.yml',
               'docs/qualification/local-decisions/shadow/observability/alerts.test.yml']
    return {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in sources}


def harness_identity():
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True, timeout=5).strip()
    sources = harness_fingerprints()
    for name, checksum in sources.items():
        raw = subprocess.check_output(['git', 'show', f'{commit}:{name}'], cwd=ROOT, timeout=5)
        ensure(hashlib.sha256(raw).hexdigest() == checksum, 'Commit native probe sources before measurement')
    return commit, sources


def expected_profile(path, deadline_ms):
    if path is None:
        return None
    profile = read_json(path)
    # Use the same canonical profile validation as the HTTP boundary.
    profile_from_health({'status': 'ready', 'mode': 'shadow', 'profileJson': canonical_json(profile),
                         'profileSha256': fingerprint(profile)})
    model = profile['model']
    execution = model.get('inferenceExecution', {})
    ensure(execution.get('kind') == 'isolated-process' and execution.get('startMethod') == 'spawn'
           and execution.get('deadlineMs') == deadline_ms
           and model.get('maxInputTokens') == 2048 and model.get('allocatorCacheLimitBytes') == 128*1024*1024
           and profile['calibration']['status'] == 'uncalibrated',
           'Probe configuration does not match the expected profile')
    return profile


def evidence_directory(args):
    if args.evidence_dir is None:
        return None
    directory = args.evidence_dir.absolute()
    private = (ROOT/'docs/private').resolve()
    ensure(directory.resolve() != private and directory.resolve().is_relative_to(private),
           'Persistent raw evidence must be under docs/private')
    ensure(args.output.absolute().parent == directory, 'Output must be directly inside the evidence directory')
    ensure(not directory.exists(), 'Evidence directory already exists')
    directory.parent.mkdir(parents=True, exist_ok=True)
    directory.mkdir(mode=0o700)
    return directory


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python', type=Path, default=ROOT/'.venv/decision/bin/python')
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--policy', type=Path, required=True)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--expected-profile', type=Path)
    parser.add_argument('--inference-timeout-ms', type=int, default=2000)
    parser.add_argument('--evidence-dir', type=Path, help='New private directory to retain plist and logs, including failed startup')
    parser.add_argument('--prometheus', type=Path, help='Pinned native Prometheus executable for optional real scrape/recovery observation')
    parser.add_argument('--promtool', type=Path, help='Matching pinned native promtool executable')
    args = parser.parse_args(argv)
    try:
        ensure(platform.system() == 'Darwin', 'This probe requires macOS launchd')
        ensure(not args.output.exists(), 'Output already exists')
        ensure(100 <= args.inference_timeout_ms <= 10000, 'Invalid inference deadline')
        expected = expected_profile(args.expected_profile, args.inference_timeout_ms)
        ensure(bool(args.prometheus) == bool(args.promtool), 'Supply both --prometheus and --promtool')
        ensure(not args.prometheus or (args.evidence_dir is not None and expected is not None),
               'Monitoring requires persistent private evidence and an expected profile')
        request = Request.from_dict(read_json(args.request))
        ensure(request.kind in ('choice', 'boolean'), 'Use a Choice/Boolean diagnostic request')
        commit, sources = harness_identity()
        persistent = evidence_directory(args)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as error:
        print(f'Cannot prepare native launchd probe: {error}', file=sys.stderr, flush=True)
        return 1
    label = 'org.agat.decision-shadow-probe-'+uuid.uuid4().hex
    domain = f'gui/{os.getuid()}';target = f'{domain}/{label}'
    try:
        ensure(service_info(target) is None, 'Refusing to touch an existing service')
    except (OSError, subprocess.SubprocessError, RuntimeError) as error:
        print(f'Cannot prepare native launchd probe: {error}', file=sys.stderr, flush=True)
        return 1
    started = time.monotonic();created = datetime.now(timezone.utc).isoformat()
    pids, snapshots, runs = [], [], []
    checks = {'nativePlistValidated': False, 'nativeRestartObserved': False, 'failureExit75': False,
              'sameProfileAfterRestart': False, 'sameDecisionAfterRestart': False,
              'temporaryServiceRemoved': False, 'allOwnedProcessesStopped': False,
              'harnessSourcesStable': False}
    if expected is not None:
        checks['expectedProfileMatched'] = False
    failure = cleanup_failure = None
    inventory_complete = True
    config = None
    monitor = None
    context = contextlib.nullcontext(persistent) if persistent is not None else tempfile.TemporaryDirectory(prefix='agat-launchd-probe-')
    with context as temporary:
        directory = Path(temporary)

        def ready(previous=None):
            nonlocal inventory_complete
            if expected is not None:
                checks['expectedProfileMatched'] = False
            deadline = time.monotonic()+90
            while time.monotonic() < deadline:
                state = service_info(target)
                if state and state.get('pid') and state['pid'] != previous:
                    pid = state['pid']
                    if pid not in pids: pids.append(pid)
                    # Inventory owned children before HTTP readiness: a hung
                    # startup must not hide inference/tracker PIDs from cleanup.
                    try:
                        children = child_processes(pid)
                    except (OSError, subprocess.SubprocessError, RuntimeError):
                        inventory_complete = False
                        raise
                    for row in children:
                        if row['pid'] not in pids: pids.append(row['pid'])
                    try:
                        probe = OwnedRuntime.__new__(OwnedRuntime);probe.port = port
                        status, health = probe.call('GET','/health')
                    except (OSError,http.client.HTTPException,ValueError):
                        time.sleep(0.2)
                        continue
                    if status == 200:
                        probe.profile = profile_from_health(health)
                        if expected is not None:
                            ensure(probe.profile == expected, 'Native service does not match the expected profile')
                            checks['expectedProfileMatched'] = True
                        ensure(sum(row['role']=='inference' for row in children)==1,'Expected one inference child')
                        record = {'pid':pid,'children':children,'service':state,'port':port,
                                  'profile':probe.profile,'profileSha256':fingerprint(probe.profile),
                                  'readyAtMs':round((time.monotonic()-started)*1000,3)}
                        runs.append(record)
                        return probe,record
                time.sleep(0.2)
            raise RuntimeError('LaunchAgent did not become ready within the bounded startup period')

        previous_sigterm = signal.getsignal(signal.SIGTERM)

        def interrupted(_signum, _frame):
            raise KeyboardInterrupt('Native launchd probe interrupted')

        signal.signal(signal.SIGTERM, interrupted)
        try:
            config = launch_agent(root=ROOT, python=args.python, manifest=args.manifest, policy=args.policy,
                                  log_dir=directory, label=label, inference_timeout_ms=args.inference_timeout_ms)
            # Reserve an ephemeral loopback port; both starts use that same port.
            import socket
            with socket.socket() as reservation:
                reservation.bind(('127.0.0.1',0));port = reservation.getsockname()[1]
            config['ProgramArguments'][config['ProgramArguments'].index('--port')+1] = str(port)
            plist = directory/f'{label}.plist';write_launch_agent(plist, config);plist.chmod(0o600)
            for key in ('StandardOutPath', 'StandardErrorPath'):
                descriptor = os.open(config[key], os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
                os.close(descriptor)
            lint = subprocess.run(['/usr/bin/plutil','-lint',str(plist)],capture_output=True,text=True,timeout=5)
            ensure(lint.returncode == 0, 'Generated LaunchAgent failed native plist validation')
            checks['nativePlistValidated'] = True
            if args.prometheus:
                monitor = PrometheusObservation(ROOT, directory, args.prometheus, args.promtool)
                monitor.start(port)
            bootstrap = launchctl('bootstrap',domain,str(plist))
            ensure(bootstrap.returncode == 0, 'Temporary LaunchAgent bootstrap failed')
            first, before = ready()
            if monitor: monitor.ready()
            before['decision'] = first.score(request)
            ensure(before['decision']['httpStatus'] == 200, 'Initial native service scoring failed')
            if monitor: monitor.scored()
            child = next(row for row in before['children'] if row['role']=='inference')
            ensure(child in child_processes(before['pid']), 'Owned child changed before signal')
            failed = time.monotonic();os.kill(child['pid'],signal.SIGKILL)
            ensure(gone([before['pid']]+[row['pid'] for row in before['children']],timeout=5), 'Failed process did not stop')
            before['failureToExitMs'] = round((time.monotonic()-failed)*1000,3)
            wait_for_exit(target, 75, snapshots, directory/'failure-service-state.txt')
            checks['failureExit75'] = True
            if monitor: monitor.failed()
            print('launchd recorded exit 75; waiting for its throttled restart',flush=True)
            second, after = ready(before['pid'])
            after['failureToReadyMs'] = round((time.monotonic()-failed)*1000,3)
            ensure(after['profile'] == before['profile'], 'Native restart changed the profile')
            checks['sameProfileAfterRestart'] = True
            ensure(after['service'].get('runs',0) >= 2,'launchd did not restart the service')
            checks['nativeRestartObserved'] = True
            if monitor: monitor.ready(second=True)
            after['decision'] = second.score(request)
            ensure(after['decision']['httpStatus'] == 200,'Native restart scoring failed')
            for key in ('status','reason','selectedOptionId','value','distribution'):
                ensure(after['decision']['result'][key] == before['decision']['result'][key],'Native restart changed the decision')
            checks['sameDecisionAfterRestart'] = True
            if monitor: monitor.scored(second=True)
        except (Exception, KeyboardInterrupt) as error:
            failure = {'type': type(error).__name__, 'message': str(error)[:1000]}
        finally:
            # Only this random per-run label is ever removed. No user/system job,
            # LaunchAgents directory, enable/disable override or login item is edited.
            try:
                launchctl('bootout',target)
                checks['temporaryServiceRemoved'] = wait_for_removal(target, directory/'cleanup-service-state.txt')
                checks['allOwnedProcessesStopped'] = gone(pids,timeout=8) and inventory_complete
                checks['harnessSourcesStable'] = harness_fingerprints() == sources
                ensure(checks['temporaryServiceRemoved'], 'Temporary LaunchAgent remains registered')
                ensure(checks['allOwnedProcessesStopped'], 'Temporary LaunchAgent left owned processes or an incomplete child inventory')
                ensure(checks['harnessSourcesStable'], 'Native probe sources changed during measurement')
            except (Exception, KeyboardInterrupt) as error:
                cleanup_failure = {'type': type(error).__name__, 'message': str(error)[:1000]}
            finally:
                if monitor:
                    try:
                        monitor.close()
                    except (Exception, KeyboardInterrupt) as error:
                        cleanup_failure = {'type':type(error).__name__, 'message':str(error)[:1000]}
                signal.signal(signal.SIGTERM, previous_sigterm)
    success = (failure is None and cleanup_failure is None and all(checks.values())
               and (monitor is None or all(monitor.checks.values())))
    report = sealed({'schemaVersion':'agat.decision.launchd-recovery.v2','createdAt':created,
                     'status':'observed' if success else 'failed','qualification':'not_assessed','routingEnabled':False,
                     'label':label,'domain':domain,'inputSha256':request.input_sha256,
                     'elapsedMs':round((time.monotonic()-started)*1000,3),
                     'implementationCommit':commit, 'harnessFiles':sources, 'ownedPids':pids,
                     'expectedProfileSha256':fingerprint(expected) if expected is not None else None,
                     'serviceConfig':config,'snapshots':snapshots,'runs':runs, 'checks':checks,
                     'failure':failure, 'cleanupFailure':cleanup_failure, 'logsRetained':persistent is not None,
                     'retainedFilesSha256':{path.name:hashlib.sha256(path.read_bytes()).hexdigest()
                                           for path in [directory/f'{label}.plist',
                                                        *[Path(config[key]) for key in ('StandardOutPath','StandardErrorPath')],
                                                        directory/'failure-service-state.txt', directory/'cleanup-service-state.txt',
                                                        directory/'prometheus.yml',directory/'prometheus.log']
                                           if path.is_file()} if persistent is not None and config is not None else {},
                     'limitations':['One restart in the current GUI login session; no boot/login or crash-loop test.',
                                    'Temporary job was booted out; no persistent service was installed.',
                                    'Recovery latency includes model loading and is not a production SLO.']})
    if monitor is not None:
        report = sealed({**{k:v for k,v in report.items() if k != 'sha256'},'monitoring':monitor.report()})
    write_new(args.output,report)
    args.output.chmod(0o600)
    print(f'Native launchd recovery {report["status"]}; evidence: {args.output}',flush=True)
    return 0 if success else 1


if __name__=='__main__': raise SystemExit(main())
