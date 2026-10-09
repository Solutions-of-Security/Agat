#!/usr/bin/env python3
"""Run every original public case through a pinned real primary, with matched control."""
import argparse
import hashlib
import http.client
import importlib.util
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_runtime.artifacts import sealed
from decision_runtime.contracts import Request, fingerprint, parse_json
from decision_runtime.model_store import sha256_file, verify_manifest
from scripts.lib.decision_arrival_rate import measure_one
from scripts.lib.decision_performance import profile_from_health
from scripts.lib.decision_public_context import verify_profile
from scripts.lib.decision_public_load import historical_context_sources, validate_context
from scripts.lib.decision_public_load_verification import counters
from scripts.lib.decision_public_sources import pinned_input, private_directory, write_json_new
from scripts.lib.decision_public_workflow import PROFILE_PATH, shared_config
from scripts.lib import decision_public_real_primary as diagnostic
from workers.local_decisions import LocalDecisionClient

SPEC = importlib.util.spec_from_file_location('public_primary_launcher', ROOT/'scripts/run-decision-arrival-rate.py')
launcher = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(launcher)
runtime = launcher.runtime


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('context-profile', 'runtime-python', 'manifest', 'primary-binaries', 'primary-models', 'evidence-dir'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--context-profile-file-sha256', required=True)
    args = parser.parse_args(argv)
    try:
        private = (ROOT/'docs/private').resolve()
        runtime.require(args.evidence_dir.resolve().is_relative_to(private) and args.evidence_dir.resolve() != private
            and not args.evidence_dir.exists() and not args.evidence_dir.is_symlink(), 'Use a new private evidence directory')
        context = validate_context(parse_json(pinned_input(args.context_profile, args.context_profile_file_sha256, 32*1024*1024)))
        historical_context_sources(ROOT, context); config = shared_config(context)
        commit, sources = launcher.frozen_sources(diagnostic.SOURCE_PATHS)
        profile = parse_json(pinned_input(ROOT/PROFILE_PATH, context['profileFileSha256'], 1024*1024))
        manifest, _ = verify_manifest(args.manifest.resolve()); verify_profile(profile, manifest)
        runtime.require(sha256_file(args.manifest) == context['manifestFileSha256'] and fingerprint(profile) == context['profileSha256'], 'Frozen model/profile differs')
        requirements = dict(line.split('==') for line in (ROOT/'decision_runtime/requirements-mlx.txt').read_text().splitlines() if line and not line.startswith('#'))
        code = 'import importlib.metadata,json,platform,sys;print(json.dumps({"python":platform.python_version(),"machine":platform.machine(),"packages":{k:importlib.metadata.version(k) for k in sys.argv[1:]}}))'
        environment = parse_json(subprocess.check_output([str(args.runtime_python.absolute()), '-B', '-c', code, *requirements], cwd=ROOT, timeout=15))
        runtime.require(environment == context['tokenizerEnvironment'], 'Frozen runtime dependencies differ')
        primary = diagnostic.prepare(ROOT, args.primary_binaries.resolve(), args.primary_models.resolve())
        directory = private_directory(ROOT, args.evidence_dir)
        plan = sealed({'schemaVersion': diagnostic.PLAN_SCHEMA, 'createdAt': diagnostic.now(), 'sourceCommit': commit, 'sourceFiles': sources,
            'contextProfileFileSha256': args.context_profile_file_sha256, 'context': context, 'config': config, 'runtime': environment,
            'manifestFileSha256': context['manifestFileSha256'], 'primary': primary, 'protocol': diagnostic.PROTOCOL,
            'referenceLabels': 0, 'classificationAccuracyMeasured': False, 'ownersAppointed': False, 'sloAccepted': False,
            'routingEnabled': False, 'qualification': 'not_assessed'})
        write_json_new(directory/'plan.json', plan); plan_file_sha = sha256_file(directory/'plan.json')
    except Exception as error:
        print('Cannot prepare real-primary workflow: '+type(error).__name__+': '+str(error)[:200], file=sys.stderr); return 1
    stopped = [False]; previous = {sig: signal.signal(sig, lambda *_: stopped.__setitem__(0, True)) for sig in (signal.SIGINT, signal.SIGTERM)}
    process = None; driver = None; owned_primary = None; owned = set(); errors = []; samples = []; warmup = []; evidence = None; failure = None
    old_umask = os.umask(0o077); start = time.monotonic()
    try:
        with runtime.open_private_log(directory/'runtime.log') as log, runtime.open_private_log(directory/'primary.log') as primary_log, runtime.open_private_log(directory/'driver.log') as driver_log:
            with socket.socket() as bound: bound.bind(('127.0.0.1', 0)); port = bound.getsockname()[1]
            runtime.require(port not in (8766, 9095, 11434), 'Experimental port collides with protected service')
            process = subprocess.Popen([str(args.runtime_python.absolute()), '-B', '-m', 'decision_runtime', 'serve', '--manifest', str(args.manifest.resolve()),
                '--policy', str(ROOT/runtime.SHADOW_POLICY), '--max-tokens', '2048', '--cache-limit-mib', '128', '--wired-limit-mib', '4096',
                '--inference-timeout-ms', '5000', '--exit-on-backend-unavailable', '--port', str(port)], cwd=ROOT,
                env={**os.environ, 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'HF_HUB_DISABLE_TELEMETRY': '1'},
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            owned.add(process.pid); deadline = time.monotonic()+60
            while True:
                runtime.require(process.poll() is None and not stopped[0], 'Owned runtime exited or cancelled')
                try: health = runtime.request(port, '/health'); break
                except (OSError, ValueError, http.client.HTTPException):
                    runtime.require(time.monotonic() < deadline, 'Owned runtime startup timeout'); time.sleep(.1)
            runtime.require(profile_from_health(health) == profile, 'Owned runtime profile differs')
            def sample(label):
                owned.update(runtime.shared.inventory(process.pid)[0])
                c = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
                try:
                    c.request('GET', '/metrics'); response = c.getresponse(); body = response.read(1048577)
                    runtime.require(response.status == 200 and len(body) <= 1048576, 'Metrics unavailable')
                finally: c.close()
                current = runtime.request(port, '/health'); runtime.require(profile_from_health(current) == profile, 'Runtime profile drift')
                entry = {'label': label, 'elapsedMs': round((time.monotonic()-start)*1000, 3), 'health': current, 'metricsRaw': body.decode(),
                    'ownedPids': sorted(runtime.shared.inventory(process.pid)[0]),
                    'processRaw': runtime.command(['ps', '-o', 'pid=,ppid=,rss=,lstart=', '-p', ','.join(map(str, sorted(owned)))])}
                values, server_start = counters(entry); samples.append({**entry, 'counters': values, 'serverStart': server_start})
            sample('ready_before_scoring'); runtime.require(all(v == 0 for v in samples[0]['counters'].values()), 'Earlier native calls')
            case = next(row for row in context['inputs'] if row['contextEligible']); client = LocalDecisionClient(f'http://127.0.0.1:{port}')
            for iteration in range(2):
                row = measure_one(client, Request.from_dict(case['request']), profile, 10000)
                runtime.require(row['status'] in ('ok', 'abstain') and row['observation']['result']['inputTokens'] == case['inputTokens'], 'Warmup failed')
                warmup.append({'iteration': iteration, 'caseId': case['id'], **row})
            sample('after_warmup'); runtime.require(sum(samples[1]['counters'].values()) == 2, 'Warmup denominator differs')
            owned_primary = diagnostic.OwnedPrimary(runtime, ROOT, args.primary_binaries.resolve(), args.primary_models.resolve(), primary_log, owned, lambda: stopped[0])
            owned_primary.start(); write_json_new(directory/'primary-warmup.json', owned_primary.warmup); write_json_new(directory/'primary-before.json', owned_primary.sample())
            environment = {k: v for k, v in os.environ.items() if not k.startswith(('AGAT_', 'OTEL_'))}
            driver = subprocess.Popen(['node', '--import', 'tsx', 'scripts/run-public-support-real-primary.mts', '--decision-url', f'http://127.0.0.1:{port}',
                '--primary-url', f'http://127.0.0.1:{owned_primary.port}', '--evidence-dir', str(directory)], cwd=ROOT,
                env={**environment, 'OTEL_SDK_DISABLED': 'true'}, stdout=driver_log, stderr=subprocess.STDOUT, start_new_session=True)
            owned.add(driver.pid); deadline = time.monotonic()+3700
            while driver.poll() is None:
                owned.update(runtime.shared.inventory(driver.pid)[0]); owned.update(runtime.shared.inventory(process.pid)[0]); owned.update(runtime.shared.inventory(owned_primary.process.pid)[0])
                runtime.require(process.poll() is None and owned_primary.process.poll() is None and not stopped[0] and time.monotonic() < deadline,
                    'Owned workflow child exited, cancelled or exceeded deadline'); time.sleep(.5)
            runtime.require(driver.returncode == 0, 'Real-primary workflow driver failed')
            observed = parse_json((directory/'workflow-driver.json').read_bytes()); owned.update(observed['ownedPids'])
            write_json_new(directory/'primary-after.json', owned_primary.sample()); sample('after_inventory')
            runtime.require(diagnostic.prepare(ROOT, args.primary_binaries.resolve(), args.primary_models.resolve()) == primary, 'Primary files/settings changed')
        names = diagnostic.ARTIFACTS | {r['traceFile'] for r in observed['routes']}
        artifacts = {name: (directory/name).read_bytes() for name in names}
        evidence = diagnostic.verify_inventory(context, plan['protocol'], parse_json(artifacts['workflow-plan.json']), observed, artifacts)
        runtime.require(launcher.frozen_sources(diagnostic.SOURCE_PATHS) == (commit, sources)
            and pinned_input(args.context_profile, args.context_profile_file_sha256, 32*1024*1024) == (args.context_profile).read_bytes()
            and sha256_file(args.manifest) == context['manifestFileSha256'], 'Measurement source/context/model drift')
    except Exception as error:
        failure = {'type': type(error).__name__, 'reason': str(error)[:200]}
    finally:
        if driver is not None: runtime.stop_owned_process(driver, owned, errors)
        try: (directory/'worker-credentials.json').unlink(missing_ok=True)
        except OSError: errors.append('credentialCleanup')
        if owned_primary is not None: owned_primary.close(errors)
        if process is not None: runtime.stop_owned_process(process, owned, errors)
        remaining = sorted(pid for pid in owned if runtime.shared.alive(pid))
        for sig, handler in previous.items(): signal.signal(sig, handler)
        os.umask(old_umask)
    artifact_sha = {p.name: sha256_file(p) for p in directory.iterdir() if p.is_file() and p.name not in ('plan.json', 'result.json')}
    status = 'observed' if failure is None and not errors and not remaining else 'measurement_error'
    result = sealed({'schemaVersion': diagnostic.RESULT_SCHEMA, 'status': status, 'planSha256': plan['sha256'], 'evidence': evidence, 'warmup': warmup,
        'samples': samples, 'failure': failure, 'ownedPids': sorted(owned), 'remainingOwnedPids': remaining, 'cleanupErrors': errors,
        'runtimeExitCode': process.returncode if process else None, 'primaryExitCode': owned_primary.process.returncode if owned_primary and owned_primary.process else None,
        'driverExitCode': driver.returncode if driver else None, 'artifactSha256': artifact_sha, 'elapsedMs': round((time.monotonic()-start)*1000, 3),
        'referenceLabels': 0, 'classificationAccuracyMeasured': False, 'ownersAppointed': False, 'sloAccepted': False, 'routingEnabled': False, 'qualification': 'not_assessed'})
    write_json_new(directory/'result.json', result)
    print('status='+status+' planFileSha256='+plan_file_sha+' resultFileSha256='+sha256_file(directory/'result.json'))
    return 0 if status == 'observed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
