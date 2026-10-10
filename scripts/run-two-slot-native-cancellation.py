#!/usr/bin/env python3
"""Exercise two actual worker slots while one native shadow task is cancelled."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import http.client
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
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
from scripts.lib.decision_public_sources import pinned_input, private_directory, write_json_new
from scripts.lib.decision_public_workflow import PROFILE_PATH, shared_config
from scripts.lib.decision_public_workflow_recovery import http_metrics
from scripts.lib.decision_public_peer_cancellation_verification import verify_physical
from scripts.lib import decision_public_workflow_active_cancellation as active
from scripts.lib import decision_public_workflow_active_integration as integration
from scripts.lib import decision_two_slot_cancellation as diagnostic
from workers.local_decisions import LocalDecisionClient

SPEC = importlib.util.spec_from_file_location('two_slot_runtime', ROOT/'scripts/run-decision-arrival-rate.py')
launcher = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(launcher)
runtime = launcher.runtime
now = lambda: datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def publish(directory, name, value):
    runtime.require(name in {'native-prefix-armed.json', 'coordinator-cancellation-ready.json', 'coordinator-cancellation-drained.json',
        'active-native-ready.json', 'active-native-drained.json', 'native-retired.json', 'native-recovered.json'}, 'Unknown two-slot barrier')
    pending = directory/(name+'.pending')
    write_json_new(pending, value)
    try: os.link(pending, directory/name, follow_symlinks=False)
    finally: pending.unlink()


def main(argv=None, *, suite=diagnostic, target_flag='cancel-at-original-index'):
    diagnostic = suite
    parser = argparse.ArgumentParser(description=suite.__doc__)
    for name in ('context-profile', 'runtime-python', 'manifest', 'evidence-dir'):
        parser.add_argument('--'+name, type=Path, required=True)
    real_primary = getattr(suite, 'REAL_PRIMARY', False)
    if real_primary:
        for name in ('primary-binaries', 'primary-models'): parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--context-profile-file-sha256', required=True)
    parser.add_argument('--'+target_flag, dest='target_original_index', type=int, required=True)
    args = parser.parse_args(argv)
    try:
        private = (ROOT/'docs/private').resolve()
        runtime.require(args.evidence_dir.resolve().is_relative_to(private) and args.evidence_dir.resolve() != private
            and not args.evidence_dir.exists() and not args.evidence_dir.is_symlink(), 'Use a new private evidence directory')
        context_raw = pinned_input(args.context_profile, args.context_profile_file_sha256, 32*1024*1024)
        context = validate_context(parse_json(context_raw)); historical_context_sources(ROOT, context)
        projected = diagnostic.projection(context, args.target_original_index)
        protocol = diagnostic.protocol(context, args.target_original_index); fault = protocol['nativeFault']
        commit, sources = launcher.frozen_sources(diagnostic.SOURCE_PATHS)
        profile_raw = pinned_input(ROOT/PROFILE_PATH, context['profileFileSha256'], 1024*1024); profile = parse_json(profile_raw)
        manifest, _ = verify_manifest(args.manifest.resolve()); verify_profile(profile, manifest)
        runtime.require(sha256_file(args.manifest) == context['manifestFileSha256'] and fingerprint(profile) == context['profileSha256'], 'Frozen profile/model differs')
        requirements = dict(line.split('==') for line in (ROOT/'decision_runtime/requirements-mlx.txt').read_text().splitlines() if line and not line.startswith('#'))
        code = 'import importlib.metadata,json,platform,sys;print(json.dumps({"python":platform.python_version(),"machine":platform.machine(),"packages":{k:importlib.metadata.version(k) for k in sys.argv[1:]}}))'
        environment = parse_json(subprocess.check_output([str(args.runtime_python.absolute()), '-B', '-c', code, *requirements], cwd=ROOT, timeout=15))
        runtime.require(environment == context['tokenizerEnvironment'], 'Frozen native dependencies differ')
        primary_profile = diagnostic.prepare_primary(ROOT, args.primary_binaries.resolve(), args.primary_models.resolve()) if real_primary else None
        directory = private_directory(ROOT, args.evidence_dir)
        plan = sealed({'schemaVersion': diagnostic.PLAN_SCHEMA, 'createdAt': now(), 'sourceCommit': commit, 'sourceFiles': sources,
            'contextProfileFileSha256': args.context_profile_file_sha256, 'context': context, 'config': shared_config(context), 'runtime': environment,
            'profileFileSha256': context['profileFileSha256'], 'manifestFileSha256': context['manifestFileSha256'], 'protocol': protocol,
            **({'primary': primary_profile} if real_primary else {}), **diagnostic.AUTHORITY})
        write_json_new(directory/'plan.json', plan); plan_file_sha = sha256_file(directory/'plan.json')
    except Exception as error:
        print('Cannot prepare two-slot native gate: '+type(error).__name__+': '+str(error)[:200], file=sys.stderr); return 1
    stopped = threading.Event(); previous = {sig: signal.signal(sig, lambda *_: stopped.set()) for sig in (signal.SIGINT, signal.SIGTERM)}
    processes = []; owned = set(); errors = []; samples = []; warmup = []; proxy = driver = evidence = physical = failure = None
    ready = drained = retired = recovered = prefix = prepared = None; native_pids = []; child = None
    owned_primary = None; primary_log = None
    recorded_owned = []; started = time.monotonic(); old_umask = os.umask(0o077)

    def record_owned():
        nonlocal recorded_owned
        current = sorted(owned)
        if current != recorded_owned:
            with (directory/'owned-pids.jsonl').open('a') as stream:
                stream.write(json.dumps({'recordedAt': now(), 'ownedPids': current})+'\n')
            recorded_owned = current

    try:
        with socket.socket() as bound: bound.bind(('127.0.0.1', 0)); port = bound.getsockname()[1]
        runtime.require(port not in (8766, 9095, 11434), 'Owned runtime collides with protected service')

        def launch(epoch):
            with runtime.open_private_log(directory/('runtime.log' if epoch == 0 else 'runtime-recovered.log')) as log:
                process = subprocess.Popen([str(args.runtime_python.absolute()), '-B', '-m', 'decision_runtime', 'serve', '--manifest', str(args.manifest.resolve()),
                    '--policy', str(ROOT/runtime.SHADOW_POLICY), '--max-tokens', '2048', '--cache-limit-mib', '128', '--wired-limit-mib', '4096',
                    '--inference-timeout-ms', '5000', '--exit-on-backend-unavailable', '--port', str(port)], cwd=ROOT, start_new_session=True,
                    env={**os.environ, 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'HF_HUB_DISABLE_TELEMETRY': '1'}, stdout=log, stderr=subprocess.STDOUT)
            processes.append(process); owned.add(process.pid); record_owned(); deadline = time.monotonic()+60
            while True:
                runtime.require(process.poll() is None and not stopped.is_set(), 'Owned native startup failed or cancelled')
                try: health = runtime.request(port, '/health'); break
                except (OSError, ValueError, http.client.HTTPException):
                    runtime.require(time.monotonic() < deadline, 'Native startup deadline exceeded'); time.sleep(.05)
            runtime.require(profile_from_health(health) == profile and health['profileSha256'] == context['profileSha256'], 'Native ready profile differs')
            owned.update(runtime.shared.inventory(process.pid)[0]); record_owned(); return process

        def sample(process, epoch, label):
            raw = active.bounded_metrics(port); counts, origin = http_metrics(raw, in_progress=0)
            owned.update(runtime.shared.inventory(process.pid)[0]); record_owned(); health = runtime.request(port, '/health')
            runtime.require(profile_from_health(health) == profile, 'Native sample profile drift')
            value = {'label': label, 'epoch': epoch, 'capturedAt': now(), 'runtimePid': process.pid, 'metricsRaw': raw, 'counters': counts,
                'serverStart': origin, 'health': health, 'ownedPids': sorted(owned), 'elapsedMs': round((time.monotonic()-started)*1000, 3)}
            samples.append(value); return value

        def warm(process, epoch):
            origin = sample(process, epoch, 'ready_before_scoring')
            runtime.require(all(v == 0 for v in origin['counters'].values()), 'Native epoch did not begin empty')
            client = LocalDecisionClient(f'http://127.0.0.1:{port}'); case = projected['inputs'][0]
            for iteration in range(2):
                observed = measure_one(client, Request.from_dict(case['request']), profile, 10000)
                runtime.require(observed['status'] in ('ok', 'abstain') and observed['observation']['result']['inputTokens'] == case['inputTokens'], 'Native warmup failed')
                warmup.append({'epoch': epoch, 'iteration': iteration, 'caseId': case['id'], **observed})
            return sample(process, epoch, 'after_warmup')

        child = launch(0); initial = warm(child, 0)
        primary_args = []
        if real_primary:
            primary_log = runtime.open_private_log(directory/'primary.log')
            owned_primary = diagnostic.OwnedPrimary(runtime, ROOT, args.primary_binaries.resolve(), args.primary_models.resolve(), primary_log, owned, stopped.is_set)
            owned_primary.start(); record_owned(); write_json_new(directory/'primary-warmup.json', owned_primary.warmup)
            write_json_new(directory/'primary-before.json', owned_primary.sample()); primary_args = ['--primary-url', f'http://127.0.0.1:{owned_primary.port}']
        proxy = diagnostic.make_proxy(port, projected, fault)
        proxy.bind_warmups(dict(Counter(row['status'] for row in warmup)), initial['serverStart'])
        env = {key: value for key, value in os.environ.items() if not key.startswith(('AGAT_', 'OTEL_'))}
        with runtime.open_private_log(directory/'driver.log') as log:
            driver = subprocess.Popen(['node', '--import', 'tsx', diagnostic.DRIVER_PATH, '--decision-url',
                f'http://127.0.0.1:{proxy.port}', '--evidence-dir', str(directory), *primary_args], cwd=ROOT, env={**env, 'OTEL_SDK_DISABLED': 'true'},
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        owned.add(driver.pid); record_owned(); next_inventory = 0
        while driver.poll() is None:
            runtime.require(not stopped.is_set() and time.monotonic()-started < 260 and not proxy.errors, 'Workflow cancelled, exceeded budget or relay failed')
            if owned_primary is not None: runtime.require(owned_primary.process.poll() is None, 'Owned real primary exited during native cancellation/recovery')
            if time.monotonic() >= next_inventory:
                owned.update(runtime.shared.inventory(driver.pid)[0]); record_owned(); next_inventory = time.monotonic()+.5
                if owned_primary is not None: owned.update(runtime.shared.inventory(owned_primary.process.pid)[0]); record_owned()
            if prefix is None and (directory/'native-prefix-ready.json').exists():
                prefix = parse_json(pinned_input(directory/'native-prefix-ready.json', sha256_file(directory/'native-prefix-ready.json'), 65536))
                trace_raw = pinned_input(directory/'trace-prefix.http.json', prefix['traceFileSha256'], 16*1024*1024); trace = parse_json(trace_raw)
                runtime.require(trace['run']['id'] == prefix['runId'] and trace['run']['status'] == 'completed', 'Original prefix did not complete')
                diagnostic.agent_stage(trace, projected['inputs'][0]); sample(child, 0, 'before_target')
                native_pids = sorted(runtime.shared.inventory(child.pid)[0]); owned.update(native_pids); record_owned()
                publish(directory, 'native-prefix-armed.json', {'prefixFileSha256': sha256_file(directory/'native-prefix-ready.json'),
                    'runtimePid': child.pid, 'nativePids': native_pids, 'serverStartText': str(samples[-1]['serverStart']), 'armedAt': now()})
            if prepared is None and (directory/diagnostic.PREPARED_FILE).exists():
                prepared = parse_json(pinned_input(directory/diagnostic.PREPARED_FILE, sha256_file(directory/diagnostic.PREPARED_FILE), 65536))
                diagnostic.validate_preparation(projected, prepared, {name: (directory/name).read_bytes() for name in
                    ('trace-target-before.http.json', 'trace-peer-before.http.json', 'peer-primary-held.json')})
                runtime.require(prefix is not None, 'Target bypassed original prefix accounting'); proxy.prepared.set()
            if ready is None:
                ready = proxy.ready_receipt()
                if ready is not None:
                    runtime.require(prepared is not None and ready['stageId'] == prepared['stageId'], 'Target bypassed actual two-slot preparation')
                    publish(directory, diagnostic.READY_FILE, ready)
            if drained is None:
                drained = proxy.target_receipt()
                if drained is not None:
                    publish(directory, diagnostic.DRAINED_FILE, drained); deadline = time.monotonic()+fault['retirementDeadlineMs']/1000
                    runtime.require(child.wait(max(.001, deadline-time.monotonic())) == 75, 'Native runtime did not retire after actual EOF')
                    remaining_native = integration.await_native_cleanup(native_pids, lambda pids: runtime.remaining_owned_processes(pids, errors), deadline=deadline)
                    retired = active.retired_receipt(fault, ready, drained, runtime_pid=child.pid, native_pids=native_pids, exit_code=child.returncode,
                        remaining=remaining_native, log_raw=(directory/'runtime.log').read_bytes(), observed_at=now())
                    publish(directory, 'native-retired.json', retired)
                    child = launch(1); warm(child, 1); write_json_new(directory/'recovery-warmup.json', warmup[2:])
                    recovered = active.recovered_receipt(fault, retired, runtime_pid=child.pid, profile_sha=context['profileSha256'],
                        server_start=samples[-1]['serverStart'], warmup_file_sha=sha256_file(directory/'recovery-warmup.json'), applied_at=now())
                    publish(directory, 'native-recovered.json', recovered)
            runtime.require(child.poll() is None, 'Owned native runtime exited outside declared interruption'); time.sleep(.005)
        runtime.require(driver.returncode == 0 and recovered is not None, 'Actual two-slot driver failed or omitted recovery')
        observed = parse_json((directory/'workflow-driver.json').read_bytes()); owned.update(observed['ownedPids']); record_owned()
        proxy.close(); write_json_new(directory/'active-transport.json', proxy.receipt()); sample(child, 1, 'after_inventory')
        if owned_primary is not None:
            write_json_new(directory/'primary-after.json', owned_primary.sample())
            runtime.require(diagnostic.prepare_primary(ROOT, args.primary_binaries.resolve(), args.primary_models.resolve()) == primary_profile, 'Primary file/settings drift')
        runtime.require(launcher.frozen_sources(diagnostic.SOURCE_PATHS) == (commit, sources) and sha256_file(directory/'plan.json') == plan_file_sha
            and pinned_input(args.context_profile, args.context_profile_file_sha256, 32*1024*1024) == context_raw
            and pinned_input(ROOT/PROFILE_PATH, context['profileFileSha256'], 1024*1024) == profile_raw
            and verify_manifest(args.manifest)[0] == manifest, 'Frozen source/context/profile/model changed')
    except Exception as error: failure = {'type': type(error).__name__, 'reason': str(error)[:200]}
    finally:
        if driver is not None: runtime.stop_owned_process(driver, owned, errors)
        if owned_primary is not None: owned_primary.close(errors)
        if primary_log is not None: primary_log.close()
        try: (directory/'worker-credentials.json').unlink(missing_ok=True)
        except OSError as error: errors.append('workerCredentialCleanup:'+type(error).__name__)
        for process in processes:
            if process.poll() is None: runtime.stop_owned_process(process, owned, errors)
        if proxy is not None and not proxy.closed:
            try:
                proxy.close()
                if not (directory/'active-transport.json').exists(): write_json_new(directory/'active-transport.json', proxy.receipt())
            except Exception as error: errors.append('relayCleanup:'+type(error).__name__)
        record_owned(); remaining = runtime.remaining_owned_processes(owned, errors)
        for sig, handler in previous.items(): signal.signal(sig, handler)
        os.umask(old_umask)
    complete = failure is None and not errors and remaining == [] and not stopped.is_set()
    result = {'schemaVersion': diagnostic.RESULT_SCHEMA, 'status': 'observed' if complete else 'failed', 'planSha256': plan['sha256'], 'evidence': None, 'physical': None,
        'warmup': warmup, 'samples': samples, 'failure': failure, 'ownedPids': sorted(owned), 'remainingOwnedPids': remaining, 'cleanupErrors': errors,
        'runtimeExitCodes': [p.returncode for p in processes], 'driverExitCode': driver.returncode if driver else None,
        'artifactSha256': {p.name: sha256_file(p) for p in directory.iterdir() if p.is_file() and p.name not in ('plan.json', 'result.json')},
        'elapsedMs': round((time.monotonic()-started)*1000, 3), **diagnostic.AUTHORITY}
    if real_primary: result['primaryExitCode'] = owned_primary.process.returncode if owned_primary and owned_primary.process else None
    if complete:
        try:
            artifacts = {name: (directory/name).read_bytes() for name in diagnostic.ARTIFACTS}
            result['evidence'] = diagnostic.verify_actor(context, protocol, parse_json(artifacts['workflow-plan.json']), parse_json(artifacts['workflow-driver.json']), artifacts)
            result['physical'] = verify_physical(projected, result, parse_json(artifacts['active-transport.json']), ready, retired, recovered, artifacts)
            diagnostic.inventory(context, plan, sealed(result), artifacts)
        except Exception as error:
            complete = False; result.update(status='failed', failure={'type': type(error).__name__, 'reason': str(error)[:200]})
    write_json_new(directory/'result.json', sealed(result))
    print(json.dumps({'status': result['status'], 'failure': result['failure'], 'evidence': result['evidence'], 'physical': result['physical'],
        'planFileSha256': plan_file_sha, 'resultFileSha256': sha256_file(directory/'result.json'), 'remainingOwnedPids': remaining}))
    return 0 if complete else 1


if __name__ == '__main__': raise SystemExit(main())
