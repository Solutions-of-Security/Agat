#!/usr/bin/env python3
"""Exercise one restart of a temporary, uniquely named user LaunchAgent, then boot it out."""

from __future__ import annotations

import argparse
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
from decision_runtime.contracts import Request, fingerprint
from scripts.lib.decision_performance import profile_from_health
from scripts.lib.decision_service import launch_agent, write_launch_agent

# Reuse the already-tested UTF-8 HTTP probe and owned-child identification.
recovery = runpy.run_path(str(ROOT/'scripts/check-decision-service-recovery.py'))
OwnedRuntime, child_processes, ensure, gone = (recovery[name] for name in ('OwnedRuntime', 'child_processes', 'ensure', 'gone'))


def launchctl(*arguments):
    return subprocess.run(['/bin/launchctl', *arguments], capture_output=True, text=True, timeout=10)


def service_info(target):
    state = launchctl('print', target)
    if state.returncode: return None
    result = {}
    for key in ('pid', 'runs', 'last exit code'):
        match = re.search(r'^\s*'+re.escape(key)+r' = (\d+)\s*$', state.stdout, re.MULTILINE)
        if match: result[key] = int(match[1])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python', type=Path, default=ROOT/'.venv/decision/bin/python')
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--policy', type=Path, required=True)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    ensure(platform.system() == 'Darwin', 'This probe requires macOS launchd')
    ensure(not args.output.exists(), 'Output already exists')
    request = Request.from_dict(read_json(args.request))
    ensure(request.kind in ('choice', 'boolean'), 'Use a Choice/Boolean diagnostic request')
    label = 'org.agat.decision-shadow-probe-'+uuid.uuid4().hex
    domain = f'gui/{os.getuid()}';target = f'{domain}/{label}'
    ensure(service_info(target) is None, 'Refusing to touch an existing service')
    started = time.monotonic();created = datetime.now(timezone.utc).isoformat()
    pids, snapshots, runs = [], [], []
    with tempfile.TemporaryDirectory(prefix='agat-launchd-probe-') as temporary:
        directory = Path(temporary)
        config = launch_agent(root=ROOT, python=args.python, manifest=args.manifest, policy=args.policy,
                              log_dir=directory, label=label)
        # Port 0 is intentionally limited to this private probe. Ordinary service
        # configuration requires a fixed port; both starts must receive that port.
        import socket
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1',0));port = reservation.getsockname()[1]
        config['ProgramArguments'][config['ProgramArguments'].index('--port')+1] = str(port)
        plist = directory/f'{label}.plist';write_launch_agent(plist, config)
        lint = subprocess.run(['/usr/bin/plutil','-lint',str(plist)],capture_output=True,text=True,timeout=5)
        ensure(lint.returncode == 0, 'Generated LaunchAgent failed native plist validation')

        def ready(previous=None):
            deadline = time.monotonic()+90
            while time.monotonic() < deadline:
                state = service_info(target)
                if state and state.get('pid') and state['pid'] != previous:
                    pid = state['pid']
                    if pid not in pids: pids.append(pid)
                    try:
                        probe = OwnedRuntime.__new__(OwnedRuntime);probe.port = port
                        status, health = probe.call('GET','/health')
                        if status == 200:
                            probe.profile = profile_from_health(health)
                            children = child_processes(pid)
                            ensure(sum(row['role']=='inference' for row in children)==1,'Expected one inference child')
                            pids.extend(row['pid'] for row in children)
                            record = {'pid':pid,'children':children,'service':state,'port':port,
                                      'profile':probe.profile,'profileSha256':fingerprint(probe.profile),
                                      'readyAtMs':round((time.monotonic()-started)*1000,3)}
                            runs.append(record)
                            return probe,record
                    except (OSError,http.client.HTTPException,ValueError): pass
                time.sleep(0.2)
            raise RuntimeError('LaunchAgent did not become ready within the bounded startup period')

        try:
            bootstrap = launchctl('bootstrap',domain,str(plist))
            ensure(bootstrap.returncode == 0, 'Temporary LaunchAgent bootstrap failed')
            first, before = ready()
            before['decision'] = first.score(request)
            ensure(before['decision']['httpStatus'] == 200, 'Initial native service scoring failed')
            child = next(row for row in before['children'] if row['role']=='inference')
            ensure(child in child_processes(before['pid']), 'Owned child changed before signal')
            failed = time.monotonic();os.kill(child['pid'],signal.SIGKILL)
            ensure(gone([before['pid']]+[row['pid'] for row in before['children']],timeout=5), 'Failed process did not stop')
            before['failureToExitMs'] = round((time.monotonic()-failed)*1000,3)
            observed = service_info(target);snapshots.append(observed)
            ensure(observed and observed.get('last exit code') == 75, 'launchd did not record runtime exit 75')
            print('launchd recorded exit 75; waiting for its throttled restart',flush=True)
            second, after = ready(before['pid'])
            after['failureToReadyMs'] = round((time.monotonic()-failed)*1000,3)
            ensure(after['profile'] == before['profile'], 'Native restart changed the profile')
            ensure(after['service'].get('runs',0) >= 2,'launchd did not restart the service')
            after['decision'] = second.score(request)
            ensure(after['decision']['httpStatus'] == 200,'Native restart scoring failed')
            for key in ('status','reason','selectedOptionId','value','distribution'):
                ensure(after['decision']['result'][key] == before['decision']['result'][key],'Native restart changed the decision')
        finally:
            # Only this random per-run label is ever removed. No user/system job,
            # LaunchAgents directory, enable/disable override or login item is edited.
            launchctl('bootout',target)
            ensure(service_info(target) is None,'Temporary LaunchAgent remains registered')
            ensure(gone(pids,timeout=8),'Temporary LaunchAgent left owned processes')
    sources = ['scripts/check-decision-launchd.py','scripts/check-decision-service-recovery.py','scripts/lib/decision_service.py']
    report = sealed({'schemaVersion':'agat.decision.launchd-recovery.v1','createdAt':created,
                     'status':'observed','qualification':'not_assessed','routingEnabled':False,
                     'label':label,'domain':domain,'inputSha256':request.input_sha256,
                     'elapsedMs':round((time.monotonic()-started)*1000,3),
                     'harnessFiles':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in sources},
                     'serviceConfig':config,'snapshots':snapshots,'runs':runs,
                     'checks':{'nativePlistValidated':True,'nativeRestartObserved':True,'failureExit75':True,
                               'sameProfileAfterRestart':True,'sameDecisionAfterRestart':True,
                               'temporaryServiceRemoved':True,'allOwnedProcessesStopped':True},
                     'limitations':['One restart in the current GUI login session; no boot/login or crash-loop test.',
                                    'Temporary job was booted out; no persistent service was installed.',
                                    'Recovery latency includes model loading and is not a production SLO.']})
    write_new(args.output,report)
    print(f'Native launchd recovery observed; temporary service removed; {args.output}',flush=True)
    return 0


if __name__=='__main__': raise SystemExit(main())
