#!/usr/bin/env python3
"""Install, inspect or stop only the verified resident shadow jobs owned by Agat."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import math
import os
import platform
import plistlib
import re
import runpy
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from decision_runtime.artifacts import read_json,sealed,verify_seal,write_new
from decision_runtime.contracts import Request,canonical_json,fingerprint
from decision_runtime.model_store import sha256_file,verify_manifest
from scripts.lib.decision_performance import profile_from_health,validate_result
from scripts.lib.decision_service import launch_agent

native = runpy.run_path(str(ROOT/'scripts/check-decision-launchd.py'))
prepare = runpy.run_path(str(ROOT/'scripts/prepare-decision-resident-deployment.py'))
launchctl,service_info,wait_for_removal = (native[k] for k in ('launchctl','service_info','wait_for_removal'))
OwnedRuntime,child_processes,gone = (native[k] for k in ('OwnedRuntime','child_processes','gone'))
LABELS = ('org.agat.decision-shadow','org.agat.decision-prometheus')
PACKAGE_CHECKS = {'allOwnedPidsAbsent','committedHarness','jobAbsent','nativeExitAndRestart',
                  'officialArchiveAndBinaries','offlineEnvironmentPins','preparedBundleFiles',
                  'rawPendingAndClearedAlert','rawScrapeCountersAndReset','rawTargetFailure',
                  'rawUpSequence','residentModel','retainedFiles','runtimeProfileAndResults','sealedEvidence'}


def require(condition,message):
    if not condition: raise RuntimeError(message)


def source_identity():
    commit,files = prepare['source_identity']()
    files.update(native['harness_fingerprints']())
    for name in ('scripts/manage-decision-resident-deployment.py','docs/qualification/local-decisions/request.example.json'):
        files[name] = sha256_file(ROOT/name)
    for name,checksum in files.items():
        raw = subprocess.check_output(['git','show',f'{commit}:{name}'],cwd=ROOT,timeout=5)
        require(hashlib.sha256(raw).hexdigest() == checksum,'Commit management sources before execution')
    return commit,files


def checked_path(root,relative):
    path = Path(relative)
    require(not path.is_absolute() and '..' not in path.parts and (root/path).resolve().is_relative_to(root.resolve()),
            'Bundle path escapes the owned release')
    target = root/path
    require(not target.is_symlink() and target.is_file(),'Bundle input is not a regular resident file')
    return target


def validate_bundle(root,expected_seal):
    require(re.fullmatch(r'[a-f0-9]{64}',expected_seal) is not None,'Invalid expected bundle seal')
    root = root.absolute()
    base = (Path.home()/'Library/Application Support/Agat/decision-shadow/releases').resolve()
    require(not root.is_symlink() and root.resolve().parent == base and root.stat().st_uid == os.getuid(),
            'Use the owned resident release in Application Support')
    bundle = verify_seal(read_json(root/'deployment.json'),'agat.decision.resident-bundle.v1')
    require(bundle['sha256'] == expected_seal and bundle['destination'] == str(root)
            and bundle['status'] == 'prepared' and bundle['routingEnabled'] is False and bundle['qualification'] == 'not_assessed',
            'Unexpected resident bundle or routing state')
    profile = read_json(ROOT/'docs/qualification/local-decisions/performance/profiles/runtime-0.12.2.json')
    require(bundle['profile'] == profile and bundle['profileSha256'] == fingerprint(profile),'Bundle/profile mismatch')
    for name,checksum in {**bundle['copiedFiles'],**bundle['generatedFiles']}.items():
        require(sha256_file(checked_path(root,name)) == checksum,'Resident bundle file changed')
    configs = bundle['serviceConfigs']
    require(len(configs) == 2 and [c['Label'] for c in configs] == list(LABELS),'Unexpected service labels')
    runtime_args = configs[0]['ProgramArguments'];port = int(runtime_args[runtime_args.index('--port')+1])
    monitor_arg = next(a for a in configs[1]['ProgramArguments'] if a.startswith('--web.listen-address='))
    require(monitor_arg.startswith('--web.listen-address=127.0.0.1:'),'Prometheus must bind loopback')
    monitor_port = int(monitor_arg.rsplit(':',1)[1]);require(port != monitor_port,'Duplicate service ports')
    expected = launch_agent(root=root/'runtime',python=root/'venv/bin/python',manifest=root/'config/decider-2b.json',
                            policy=root/'config/policy.json',log_dir=root/'logs',port=port,inference_timeout_ms=5000)
    expected['EnvironmentVariables'].update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1')
    require(configs == [expected,prepare['prometheus_agent'](root,root/'bin/prometheus',monitor_port)],'Service configuration differs from the owned recipe')
    for config in configs:
        path = root/'launchd'/f'{config["Label"]}.plist'
        require(plistlib.loads(path.read_bytes()) == config,'Generated plist/config mismatch')
    digest = hashlib.sha256()
    for path in sorted((root/'runtime/decision_runtime').glob('*.py')):
        digest.update(path.name.encode()+b'\0'+path.read_bytes()+b'\0')
    require(digest.hexdigest() == profile['model']['implementationSha256'],'Resident runtime implementation changed')
    model,_ = verify_manifest(root/'config/decider-2b.json')
    require(model['artifactSha256'] == profile['model']['artifactSha256'],'Resident model changed')
    return bundle,port,monitor_port


def gate_evidence(path,bundle):
    evidence = verify_seal(read_json(path),'agat.decision.resident-package-verification.v1')
    require(evidence['status'] == 'verified' and evidence['bundleSeal'] == bundle['sha256']
            and evidence['profileSha256'] == bundle['profileSha256'] and evidence['routingEnabled'] is False
            and set(evidence['checks']) == PACKAGE_CHECKS and all(v is True for v in evidence['checks'].values())
            and evidence['identicalToPreviousNativeBaseline'] is True,
            'Resident package gate is not verified for this bundle')
    return evidence


def plist_paths(root):
    return [(root/'launchd'/f'{label}.plist',Path.home()/'Library/LaunchAgents'/f'{label}.plist') for label in LABELS]


def preflight(root,port,monitor_port):
    require(not (root/'registration.json').exists() and not (root/'registration.json').is_symlink(),
            'Registration marker already exists; inspect the owned deployment')
    for source,path in plist_paths(root):
        require(not path.exists() and not path.is_symlink() and service_info(f'gui/{os.getuid()}/{source.stem}') is None,
                'Refusing to replace an existing plist or registered service')
    for candidate in (port,monitor_port):
        with socket.socket() as check: check.bind(('127.0.0.1',candidate))
    return {'status':'verified','labelsAvailable':True,'plistPathsAvailable':True,'portsAvailable':True}


def registered_ownership(root,bundle):
    marker = verify_seal(read_json(root/'registration.json'),'agat.decision.resident-registration.v1')
    require(marker['bundleSeal'] == bundle['sha256'] and marker['ownerUid'] == os.getuid()
            and marker['labels'] == list(LABELS) and marker['routingEnabled'] is False
            and marker['qualification'] == 'not_assessed','Foreign registration ownership')
    for source,path in plist_paths(root):
        require(path.is_file() and not path.is_symlink() and path.stat().st_uid == os.getuid()
                and path.read_bytes() == source.read_bytes(),'Installed plist is missing or belongs to another configuration')
    return marker


def owned_service_info(root,label):
    target = f'gui/{os.getuid()}/{label}'
    state = launchctl('print',target)
    if state.returncode:
        require(state.returncode == 113 and f'Could not find service "{label}"' in state.stderr,
                'Cannot inspect the resident LaunchAgent')
        return None
    match = re.search(r'^\s*path = (.+)\s*$',state.stdout,re.MULTILINE)
    expected = Path.home()/'Library/LaunchAgents'/f'{label}.plist'
    require(match is not None and Path(match[1].strip()) == expected,'Registered label uses a foreign plist path')
    result = {'plistPath':str(expected)}
    for key in ('pid','runs','last exit code'):
        suffix = r'(?:: [A-Z][A-Z0-9_]*)?' if key == 'last exit code' else ''
        match = re.search(r'^\s*'+re.escape(key)+r' = (\d+)'+suffix+r'\s*$',state.stdout,re.MULTILINE)
        if match: result[key] = int(match[1])
    return result


def file_record(path,expected=None,complete=True,descriptor=None):
    handle = os.open(path,os.O_RDONLY|os.O_NOFOLLOW) if descriptor is None else os.dup(descriptor)
    try:
        info = os.fstat(handle)
        return {'path':path,'device':info.st_dev,'inode':info.st_ino,'ctimeNs':info.st_ctime_ns,'descriptor':handle,
                'bytes':path.read_bytes() if expected is None else expected,'complete':complete}
    except BaseException:
        os.close(handle);raise


def close_file_record(record):
    handle = record.pop('descriptor',None)
    if handle is not None: os.close(handle)


def exclusive_file(path,encoded,created):
    # Publish ownership before writing: an interruption can leave a partial file.
    descriptor = os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        record = file_record(path,encoded,complete=False,descriptor=descriptor);created.append(record)
        offset = 0
        while offset < len(encoded):
            written = os.write(descriptor,encoded[offset:]);require(written > 0,'Owned file write made no progress')
            offset += written
    finally:
        # Read identity from our descriptor even after a partial write; no
        # buffered close may change it afterward. ctime detects inode reuse.
        if 'record' in locals(): record['ctimeNs'] = os.fstat(descriptor).st_ctime_ns
        os.close(descriptor)
    require(path.read_bytes() == encoded,'Incomplete owned file write')
    record['complete'] = True


def remove_owned_file(record):
    path = record['path']
    require('descriptor' in record,'Owned file handle was already closed')
    require(path.is_file() and not path.is_symlink(),'Owned file was replaced')
    info = path.stat();raw = path.read_bytes()
    require((info.st_dev,info.st_ino,info.st_ctime_ns) == (record['device'],record['inode'],record['ctimeNs'])
            and (raw == record['bytes'] if record['complete'] else record['bytes'].startswith(raw)),
            'Refusing to remove a changed owned file')
    path.unlink()
    close_file_record(record)


def api(port,path,parameters=None):
    connection = http.client.HTTPConnection('127.0.0.1',port,timeout=3)
    try:
        connection.request('GET',path+('?' + urlencode(parameters) if parameters else ''))
        response = connection.getresponse();raw = response.read(1024*1024+1)
        if response.status != 200: raise OSError('Local monitoring API is not ready')
        require(len(raw) <= 1024*1024,'Local monitoring API exceeded the response bound')
        return json.loads(raw)
    finally: connection.close()


def fresh_target(targets,port,scrape_after):
    require(targets.get('status') == 'success' and not targets.get('warnings'),'Target inspection failed')
    active = targets['data']['activeTargets'];require(len(active) <= 1,'Unexpected active targets')
    if not active: return False
    target = active[0]
    require(target['labels'] == {'job':'agat-decision','instance':f'127.0.0.1:{port}'}
            and target['scrapeUrl'] == f'http://127.0.0.1:{port}/metrics','Foreign active target')
    last_scrape = datetime.fromisoformat(target['lastScrape'].replace('Z','+00:00'))
    require(last_scrape.tzinfo is not None,'Scrape timestamp has no timezone')
    age = time.time()-last_scrape.timestamp()
    return target['health'] == 'up' and not target['lastError'] and -1 <= age <= 35 and last_scrape.timestamp() >= scrape_after


def inspect_services(root,bundle,port,monitor_port,timeout=0,owned_pids=None,inventory=None,include_api=True,scrape_after=0):
    deadline = time.monotonic()+timeout;records = []
    observed_pids = set() if owned_pids is None else owned_pids
    inventory = {'complete':True} if inventory is None else inventory
    while True:
        records = [];healthy = True
        for label in LABELS:
            state = owned_service_info(root,label)
            row = {'label':label,'state':state}
            if state and state.get('pid'):
                observed_pids.add(state['pid'])
                if label == LABELS[0]:
                    try:
                        children = child_processes(state['pid']);row['children'] = children
                        observed_pids.update(c['pid'] for c in children)
                    except Exception:
                        inventory['complete'] = False
                        if include_api: raise
            else: healthy = False
            records.append(row)
        if not include_api:
            return {'status':'observed','services':records,'observedPids':sorted(observed_pids)}
        try:
            health = api(port,'/health');require(profile_from_health(health) == bundle['profile'],'Installed runtime/profile mismatch')
            up = api(monitor_port,'/api/v1/query',{'query':'up{job="agat-decision"}'})
            require(up.get('status') == 'success' and not up.get('warnings'),'Prometheus query failed')
            rows = up['data']['result'];require(up['data']['resultType'] == 'vector' and len(rows) <= 1,'Unexpected scrape targets')
            if rows:
                require(rows[0]['metric'] == {'__name__':'up','job':'agat-decision','instance':f'127.0.0.1:{port}'},'Foreign scrape target')
                value = float(rows[0]['value'][1]);require(value in (0,1),'Invalid scrape state')
                healthy = healthy and value == 1
            else: healthy = False
            targets = api(monitor_port,'/api/v1/targets')
            healthy = fresh_target(targets,port,scrape_after) and healthy
            if healthy:
                require(sum(c['role'] == 'inference' for c in records[0].get('children',[])) == 1,'Missing installed inference child')
                return {'status':'ready','services':records,'health':health,'up':up,'targets':targets,'observedPids':sorted(observed_pids)}
        except (OSError,http.client.HTTPException,json.JSONDecodeError):
            healthy = False
        if time.monotonic() >= deadline:
            return {'status':'unavailable','services':records,'observedPids':sorted(observed_pids)}
        time.sleep(0.2)


def rollback(root,registered,created,pids,inventory_complete=True):
    checks = {}
    for label in reversed(registered):
        # Inventory even when bootstrap raised before readiness polling began.
        state = owned_service_info(root,label)
        if state and state.get('pid'):
            pids.add(state['pid'])
            if label == LABELS[0]:
                try: pids.update(c['pid'] for c in child_processes(state['pid']))
                except Exception: inventory_complete = False
        target = f'gui/{os.getuid()}/{label}';launchctl('bootout',target)
        checks[label] = wait_for_removal(target,root/'logs'/f'{label}.rollback.txt')
    checks['ownedProcessesStopped'] = gone(sorted(pids),timeout=8) and inventory_complete
    for record in created: remove_owned_file(record)
    require(all(checks.values()),'Owned installation cleanup is incomplete')
    return checks


def computed_counter(port):
    result = api(port,'/api/v1/query',{'query':'sum(agat_decision_request_duration_seconds_count{job="agat-decision",class="computed"})'})
    require(result.get('status') == 'success' and not result.get('warnings') and result['data']['resultType'] == 'vector','Computed counter query failed')
    rows = result['data']['result'];require(len(rows) <= 1,'Unexpected computed counter result')
    value = 0
    if rows:
        require(rows[0]['metric'] == {},'Unexpected computed counter labels')
        value = float(rows[0]['value'][1]);require(math.isfinite(value) and value >= 0 and value.is_integer(),'Invalid computed counter')
    return result,int(value)


def wait_scored_counter(port,minimum=1,timeout=35):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        result,value = computed_counter(port)
        if value >= minimum: return result
        time.sleep(0.2)
    raise RuntimeError('Prometheus did not observe the installed diagnostic score')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('check','install','status','stop'))
    parser.add_argument('--bundle',type=Path,required=True)
    parser.add_argument('--expected-seal',required=True)
    parser.add_argument('--verification',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args(argv)
    registered = [];created = [];pids = set();result = {};failure = cleanup_failure = None
    inventory = {'complete':True};commit = None;sources = {}
    previous_sigterm = signal.getsignal(signal.SIGTERM)
    def interrupted(_signal,_frame): raise KeyboardInterrupt('Resident management interrupted')
    signal.signal(signal.SIGTERM,interrupted)
    try:
        require(platform.system() == 'Darwin','Resident service management requires macOS')
        require(not args.output.exists() and args.output.absolute().resolve().is_relative_to((ROOT/'docs/private').resolve()),'Use a new private output under docs/private')
        commit,sources = source_identity()
        root = args.bundle.absolute();bundle,port,monitor_port = validate_bundle(root,args.expected_seal)
        if args.action in ('check','install'):
            require(args.verification is not None,'Supply the verified package gate before registration')
            gate_evidence(args.verification,bundle)
            result = preflight(root,port,monitor_port)
        if args.action == 'install':
            installation_started = time.time()
            for source,path in plist_paths(root):
                path.parent.mkdir(parents=True,exist_ok=True)
                exclusive_file(path,source.read_bytes(),created)
                # Record the bootstrap intent first: cancellation inside launchctl
                # can still leave the new registration needing bootout.
                registered.append(source.stem)
                command = launchctl('bootstrap',f'gui/{os.getuid()}',str(path))
                require(command.returncode == 0,'Owned LaunchAgent bootstrap failed')
            result = inspect_services(root,bundle,port,monitor_port,timeout=90,owned_pids=pids,inventory=inventory,scrape_after=installation_started)
            require(result['status'] == 'ready','Installed runtime/scraper did not become ready')
            result['counterBefore'],counter_before = computed_counter(monitor_port)
            request = Request.from_dict(read_json(ROOT/'docs/qualification/local-decisions/request.example.json'))
            runtime = OwnedRuntime.__new__(OwnedRuntime);runtime.port = port;runtime.profile = bundle['profile']
            score = runtime.score(request);require(score['httpStatus'] == 200,'Installed scoring failed');validate_result(score['result'],request,bundle['profile'])
            require(score['result']['status'] in ('ok','abstain'),'Installed diagnostic was not computed')
            result['score'] = score
            result['computedCounter'] = wait_scored_counter(monitor_port,minimum=counter_before+1)
            marker = sealed({'schemaVersion':'agat.decision.resident-registration.v1','createdAt':datetime.now(timezone.utc).isoformat(),
                             'ownerUid':os.getuid(),'bundleSeal':bundle['sha256'],'qualification':'not_assessed','routingEnabled':False,
                             'labels':list(LABELS),'observedPids':sorted(pids)})
            exclusive_file(root/'registration.json',(canonical_json(marker)+'\n').encode(),created)
        elif args.action in ('status','stop'):
            marker = registered_ownership(root,bundle)
            result = inspect_services(root,bundle,port,monitor_port,owned_pids=pids,inventory=inventory,include_api=args.action != 'stop')
            if args.action == 'stop':
                for _,path in plist_paths(root): created.append(file_record(path))
                result['cleanup'] = rollback(root,list(LABELS),created,pids,inventory['complete'])
                result['registration'] = marker
                archived = root/f'registration.stopped.{time.time_ns()}.json'
                require(not archived.exists(),'Stopped registration archive already exists')
                (root/'registration.json').rename(archived)
                result['status'] = 'stopped'
    except (Exception,KeyboardInterrupt) as error:
        failure = {'type':type(error).__name__,'message':str(error)[:1000]}
        if args.action == 'install' and (registered or created):
            try: result['cleanup'] = rollback(args.bundle.absolute(),registered,created,pids,inventory['complete'])
            except (Exception,KeyboardInterrupt) as cleanup: cleanup_failure = {'type':type(cleanup).__name__,'message':str(cleanup)[:1000]}
    finally:
        signal.signal(signal.SIGTERM,previous_sigterm)
        for record in created: close_file_record(record)
    report = sealed({'schemaVersion':'agat.decision.resident-management.v1','createdAt':datetime.now(timezone.utc).isoformat(),
                     'action':args.action,'status':'failed' if failure or cleanup_failure else result.get('status','unavailable'),
                     'qualification':'not_assessed','routingEnabled':False,'bundleSeal':args.expected_seal,
                     'implementationCommit':commit,'sourceFiles':sources,
                     'result':result,'failure':failure,'cleanupFailure':cleanup_failure})
    try:
        require(not args.output.exists() and args.output.absolute().resolve().is_relative_to((ROOT/'docs/private').resolve()),'Invalid private output')
        write_new(args.output,report);args.output.chmod(0o600)
    except (OSError,RuntimeError) as error:
        print(f'Cannot save management evidence: {error}',file=sys.stderr);return 1
    print(f'Resident {args.action}: {report["status"]}; evidence: {args.output}',flush=True)
    return 0 if report['status'] in ('verified','ready','stopped') else 1


if __name__ == '__main__': raise SystemExit(main())
