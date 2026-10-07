#!/usr/bin/env python3
"""Own a temporary pinned 4096-MiB runtime for bounded synthetic open arrivals."""
import argparse
import hashlib
import http.client
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tarfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_runtime.artifacts import sealed, write_new
from decision_runtime.contracts import Request, fingerprint, parse_json
from decision_runtime.model_store import verify_manifest
from scripts.lib.decision_arrival_rate import PLAN_SCHEMA, RESULT_SCHEMA, SOURCE_PATHS, measure_one, run_phase
from scripts.lib.decision_performance import profile_from_health
from workers.local_decisions import LocalDecisionClient

spec = importlib.util.spec_from_file_location('arrival_runtime', ROOT / 'scripts/run-temporal-real-rag.py')
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


def frozen_sources(paths):
    commit = runtime.command(['git', 'rev-parse', 'HEAD'])
    raw = subprocess.check_output(['git', 'archive', commit, '--', *paths], cwd=ROOT, timeout=15)
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        files = {item.name: archive.extractfile(item).read() for item in archive if item.isfile()}
    runtime.require(all((ROOT / name).read_bytes() == value for name, value in files.items()),
                    'Commit all measured sources before running')
    runtime.require(all(p in files or any(k.startswith(p + '/') for k in files) for p in paths),
                    'Incomplete measurement source snapshot')
    runtime.require(not runtime.command(['git', 'ls-files', '--others', '--exclude-standard', '--', *paths]),
                    'Untracked measurement sources')
    return commit, {name: hashlib.sha256(value).hexdigest() for name, value in files.items()}


def private_write(path, value):
    # The private directory and restrictive umask also protect partial files.
    write_new(path, value)
    path.chmod(0o600)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    parser.add_argument('--runtime-python', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--phase-seconds', type=int, default=12, choices=range(4, 31))
    args = parser.parse_args()
    directory = args.evidence_dir.resolve()
    runtime.require(directory.is_relative_to(ROOT / 'docs/private') and not directory.exists(),
                    'Choose new ignored private evidence directory')
    profile_path, identity = runtime.shadow_profile(args.profile, wired_limit_mib=4096)
    profile = parse_json(identity['profileJson'])
    policy_path = runtime.SHADOW_POLICY
    paths = [*SOURCE_PATHS, profile_path, policy_path, runtime.SHADOW_REFERENCE]
    commit, sources = frozen_sources(paths)
    runtime.require_resident_shadow_model(args.manifest.resolve())
    manifest, snapshot = verify_manifest(args.manifest.resolve())
    runtime.require(all(profile['model'][k] == manifest[k] for k in ('repository', 'revision', 'artifactSha256')),
                    'Model artifact differs from pinned profile')
    requirements = dict(line.split('==') for line in (ROOT / 'decision_runtime/requirements-mlx.txt').read_text().splitlines()
                        if line and not line.startswith('#'))
    env_code = 'import importlib.metadata,json,platform,sys; print(json.dumps({"python":platform.python_version(),"packages":{k:importlib.metadata.version(k) for k in sys.argv[1:]}}))'
    environment = json.loads(subprocess.check_output([str(args.runtime_python.absolute()), '-c', env_code, *requirements],
                                                    cwd=ROOT, timeout=15))
    runtime.require(environment['packages'] == requirements, 'Pinned dependency mismatch')
    # Load only the local tokenizer in a separate process, with the same runtime prompt wrapper.
    code = '''import json,sys
from transformers import AutoTokenizer
from scripts.lib.decision_context import make_probe,token_count
tokenizer=AutoTokenizer.from_pretrained(sys.argv[1],local_files_only=True,trust_remote_code=False)
cases=[]
for target in (256,2048):
 request=make_probe(tokenizer,target,'end')
 assert token_count(tokenizer,request)==target
 cases.append({'targetTokens':target,'request':request.to_dict(),'inputSha256':request.input_sha256})
print(json.dumps(cases,ensure_ascii=False))'''
    offline = {**os.environ, 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1'}
    cases = json.loads(subprocess.check_output([str(args.runtime_python.absolute()), '-c', code, str(snapshot)],
                                              cwd=ROOT, env=offline, timeout=30))
    schedules = [{'caseIndex': i, 'ratePerSecond': rate, 'count': int(rate * args.phase_seconds),
                  'clientSlots': slots, 'maxSchedulerLagMs': 100}
                 for i in range(2) for rate, slots in ((.5, 1), (1, 1), (2, 1), (2, 2))]
    plan = sealed({'schemaVersion': PLAN_SCHEMA, 'implementationCommit': commit, 'sourceSha256': sources,
        'profilePath': profile_path, 'policyPath': policy_path, 'profile': profile,
        'profileSha256': identity['profileSha256'], 'runtime': environment,
        'model': {k: v for k, v in manifest.items() if k != 'snapshot'},
        'cases': cases, 'schedules': schedules, 'phaseSeconds': args.phase_seconds,
        'callerTimeoutMs': 10000, 'thresholdMs': 5000, 'warmupPerCase': 2,
        'arrivalPattern': 'fixed monotonic offsets; no waiting for previous result; no catch-up burst',
        'scope': 'controlled_synthetic_local_http_arrivals', 'sloAccepted': False,
        'customerPopulationMeasured': False, 'routingEnabled': False, 'qualification': 'not_assessed'})
    os.umask(0o077)
    directory.mkdir(parents=True, mode=0o700)
    private_write(directory / 'plan.json', plan)  # Must succeed before any inference.
    process, report, failure = None, None, None
    owned, errors, samples, warmup, phases = set(), [], [], [], []
    stopped = [False]
    previous = {s: signal.signal(s, lambda *_: stopped.__setitem__(0, True)) for s in (signal.SIGINT, signal.SIGTERM)}
    start = time.monotonic()
    try:
        with runtime.open_private_log(directory / 'runtime.log') as log, runtime.open_private_log(directory / 'arrivals.jsonl') as journal:
            with socket.socket() as bound:
                bound.bind(('127.0.0.1', 0)); port = bound.getsockname()[1]
            process = subprocess.Popen([str(args.runtime_python.absolute()), '-m', 'decision_runtime', 'serve',
                '--manifest', str(args.manifest.resolve()), '--policy', str(ROOT / policy_path),
                '--max-tokens', '2048', '--cache-limit-mib', '128', '--wired-limit-mib', '4096',
                '--inference-timeout-ms', '5000', '--exit-on-backend-unavailable', '--port', str(port)],
                cwd=ROOT, env=offline, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            owned.add(process.pid)
            deadline = time.monotonic() + 60
            while True:
                runtime.require(process.poll() is None and not stopped[0], 'Runtime exited or cancelled during startup')
                try:
                    health = runtime.request(port, '/health')
                    break
                except (OSError, ValueError, http.client.HTTPException):
                    runtime.require(time.monotonic() < deadline, 'Runtime startup timeout')
                    time.sleep(.1)
            runtime.require(profile_from_health(health) == profile and health['profileSha256'] == plan['profileSha256'],
                            'Ready profile mismatch')
            client = LocalDecisionClient(f'http://127.0.0.1:{port}')
            def sample(label):
                owned.update(runtime.shared.inventory(process.pid)[0])
                connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
                try:
                    connection.request('GET', '/metrics')
                    response = connection.getresponse(); raw = response.read(1048577)
                    runtime.require(response.status == 200 and len(raw) <= 1048576, 'Runtime metrics request failed')
                    metrics = raw.decode('utf-8')
                finally:
                    connection.close()
                value = {'label': label, 'elapsedMs': round((time.monotonic() - start) * 1000, 3),
                         'health': runtime.request(port, '/health'), 'ownedPids': sorted(owned),
                         'metricsRaw': metrics,
                         'processRaw': runtime.command(['ps', '-o', 'pid=,ppid=,rss=,lstart=', '-p', ','.join(map(str, sorted(owned)))]),
                         'systemMemoryRaw': {'swap': runtime.command(['/usr/sbin/sysctl', 'vm.swapusage']),
                                             'vmStat': runtime.command(['/usr/bin/vm_stat'])}}
                runtime.require(profile_from_health(value['health']) == profile, 'Live profile drift')
                samples.append(value)
            sample('ready_before_first_scoring')
            for case in cases:
                request = Request.from_dict(case['request'])
                for iteration in range(2):
                    row = measure_one(client, request, profile, 10000)
                    result = (row.get('observation') or {}).get('result')
                    warmup.append({'caseId': request.id, 'targetTokens': case['targetTokens'], 'iteration': iteration, **row})
                    runtime.require(row['status'] in {'ok', 'abstain'} and result['inputTokens'] == case['targetTokens'],
                                    'Warmup failed or input token mismatch')
                sample(f'warmup-{case["targetTokens"]}')
            for index, schedule in enumerate(schedules):
                runtime.require(not stopped[0], 'Arrival measurement cancelled')
                case = cases[schedule['caseIndex']]
                def record(row):
                    journal.write((json.dumps({'phase': index, **row}, ensure_ascii=False, allow_nan=False) + '\n').encode())
                    journal.flush()
                phase = run_phase(client, case, profile, rate=schedule['ratePerSecond'], count=schedule['count'],
                    slots=schedule['clientSlots'], late_ms=schedule['maxSchedulerLagMs'],
                    cancelled=lambda: stopped[0], journal=record)
                phases.append(phase)
                private_write(directory / f'phase-{index}.json', sealed(phase))
                sample(f'phase-{index}')
                print(json.dumps({'phase': index, 'tokens': case['targetTokens'], 'rate': schedule['ratePerSecond'],
                    'slots': schedule['clientSlots'], 'summary': phase['summary']}), flush=True)
            runtime.require(runtime.command(['git', 'rev-parse', 'HEAD']) == commit and
                            all(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest for name, digest in sources.items()),
                            'Measurement source changed')
            report = {'warmup': warmup, 'phases': phases, 'healthSamples': samples}
    except Exception as error:
        failure = {'type': type(error).__name__, 'reason': str(error)[:300]}
    finally:
        if process is not None:
            runtime.stop_owned_process(process, owned, errors)
        remaining = runtime.remaining_owned_processes(owned, errors)
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    complete = (report is not None and failure is None and not stopped[0] and not errors and remaining == []
                and not any(row['status'] == 'measurement_error' for phase in phases for row in phase['rows']))
    result = sealed({'schemaVersion': RESULT_SCHEMA, 'status': 'observed' if complete else 'failed',
        'planSha256': plan['sha256'], 'report': report or {'warmup': warmup, 'phases': phases, 'healthSamples': samples},
        'failure': failure, 'cancelled': stopped[0],
        'ownedPids': sorted(owned), 'remainingOwnedPids': remaining, 'cleanupErrors': errors,
        'runtimeExitCode': process.returncode if process else None,
        'elapsedMs': round((time.monotonic() - start) * 1000, 3),
        'logSha256': {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
                     for name in ('runtime.log', 'arrivals.jsonl') if (directory / name).exists()},
        'sloAccepted': False, 'customerPopulationMeasured': False, 'routingEnabled': False, 'qualification': 'not_assessed'})
    private_write(directory / 'result.json', result)
    print(json.dumps({'status': result['status'], 'failure': failure, 'remainingOwnedPids': remaining}), flush=True)
    return 0 if complete else 1


if __name__ == '__main__':
    raise SystemExit(main())
