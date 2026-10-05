#!/usr/bin/env python3
"""Run two frozen real-model RAG workflows against owned PostgreSQL and Temporal."""
import argparse
import http.client
import importlib.util
import io
import json
import os
from pathlib import Path
import platform
import re
import shlex
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_runtime.mlx_backend import wired_limit_bytes
spec = importlib.util.spec_from_file_location('embedding_profile', ROOT / 'scripts/profile-embedding-rag.py')
shared = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shared)
require, sha, write, command = shared.require, shared.sha, shared.write, shared.command
SOURCES = ['apps/coordinator/src', 'apps/coordinator/test', 'apps/coordinator/package.json', 'apps/coordinator/tsconfig.json',
           'apps/temporal-worker/src', 'apps/temporal-worker/package.json', 'apps/temporal-worker/tsconfig.json',
           'workers', 'package.json', 'package-lock.json', 'scripts/run-temporal-real-rag.py',
           'scripts/profile-embedding-rag.py', 'scripts/test-temporal-postgres-rag.sh', 'scripts/test-temporal-rag.sh',
           'scripts/lib/decision-primary-workflow.ts', 'scripts/lib/decision-rag.ts', 'scripts/lib/decision-shadow-proxy.ts',
           'deploy/k8s/docker-desktop/postgres-init.sh', shared.FIXTURE]
SHADOW_POLICY = 'docs/qualification/local-decisions/policy.shadow.v1.json'
SHADOW_REFERENCE = 'docs/qualification/local-decisions/performance/evidence/2026-09-28/rag-http-isolation/resident-isolated/rag-workflow-plan.json'
SHADOW_SOURCES = ['decision_runtime', SHADOW_POLICY, SHADOW_REFERENCE]
SHADOW_RECOVERY_SOURCES = ['scripts/lib/temporal_shadow_control.py', 'scripts/test/test_temporal_shadow_control.py']


def shadow_profile(path, *, wired_limit_mib=None):
    """Admit an explicit export of this runtime with the original experiment settings."""
    from decision_runtime import VERSION, implementation_sha256
    from decision_runtime.contracts import canonical_json, parse_json
    wired_bytes = wired_limit_bytes(wired_limit_mib)
    path = path.resolve()
    docs = (ROOT / 'docs').resolve()
    require(path.is_relative_to(docs) and not path.is_relative_to(docs / 'private') and path.is_file(),
            'Shadow profile must be a public file under docs')
    expected = json.loads(json.loads((ROOT / SHADOW_REFERENCE).read_text())['decision']['profileJson'])
    expected['runtimeVersion'] = VERSION
    expected['model']['implementationSha256'] = implementation_sha256()
    if wired_bytes is not None:
        expected['model']['allocatorWiredLimitBytes'] = wired_bytes
    profile = parse_json(path.read_bytes())
    raw = canonical_json(profile)
    require(raw == canonical_json(expected), 'Explicit shadow profile differs from current runtime or frozen experiment settings')
    return path.relative_to(ROOT).as_posix(), {'profileJson': raw, 'profileSha256': sha(raw.encode())}


def request(port, path, body=None, timeout=5, capture_status=False):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=timeout)
    try:
        connection.request('GET' if body is None else 'POST', path,
                           body=None if body is None else json.dumps(body), headers={'Content-Type': 'application/json'})
        response = connection.getresponse()
        raw = response.read(524289)
        require(len(raw) <= 524288 and (capture_status or response.status == 200), 'Owned model request failed')
        value = json.loads(raw)
        return {'httpStatus': response.status, 'result': value} if capture_status else value
    finally:
        connection.close()


def owned_container_names(names, pids):
    return {name for name in names if (match := re.fullmatch(r'agat-temporal-(?:postgres-)?rag-(\d+)-\d+', name))
            and int(match[1]) in pids}


def own_containers(pids):
    names = command(['docker', 'ps', '--all', '--format', '{{.Names}}', '--filter', 'name=agat-temporal-']).splitlines()
    return owned_container_names(names, pids)


def cleanup_owned_containers(pids, containers, errors):
    # Previously observed owned names remain usable if Docker listing fails.
    targets = owned_container_names(containers, pids)
    try:
        targets = own_containers(pids)
        containers.update(targets)
    except Exception as error:
        errors.append('containerInventory:' + type(error).__name__)
    for name in sorted(targets):
        try:
            command(['docker', 'rm', '--force', '--volumes', name])
        except Exception as error:
            errors.append('containerRemove:' + type(error).__name__)
    # Verification runs even after discovery or individual deletion failed.
    try:
        remaining = own_containers(pids)
        containers.update(remaining)
        if remaining:
            errors.append('containerRemove:ContainersStillPresent')
    except Exception as error:
        errors.append('containerVerification:' + type(error).__name__)


def stop_owned_process(process, owned, errors):
    """Observation failures must not skip teardown of an already-owned handle."""
    if process is None:
        return
    owned.add(process.pid)
    try:
        owned.update(shared.inventory(process.pid)[0])
    except Exception as error:
        errors.append('inventory:' + type(error).__name__)
    try:
        shared.stop(process)
        if process.poll() is None:
            errors.append('stop:ProcessStillRunning')
    except Exception as error:
        errors.append('stop:' + type(error).__name__)


def remaining_owned_processes(owned, errors):
    try:
        return sorted(owned & shared.inventory(os.getpid())[1])
    except Exception as error:
        errors.append('inventory:' + type(error).__name__)
        return None  # Unknown is not evidence that every child has exited.


def open_private_log(path):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        return os.fdopen(descriptor, 'wb')
    except Exception:
        os.close(descriptor)
        raise


def require_resident_shadow_model(manifest_path):
    """Reject macOS cloud placeholders before opening or hashing model files."""
    def resident(path):
        require(not (getattr(path.stat(), 'st_flags', 0) & getattr(stat, 'SF_DATALESS', 0)),
                'Shadow model has cloud-only files; restore pinned weights in a local store before running')
    resident(manifest_path)
    manifest = json.loads(manifest_path.read_bytes())
    require(isinstance(manifest, dict) and isinstance(manifest.get('snapshot'), str)
            and bool(manifest['snapshot']) and isinstance(manifest.get('files'), dict)
            and bool(manifest['files']), 'Invalid shadow model manifest')
    snapshot = Path(manifest['snapshot'])
    resident(snapshot)
    for name in manifest['files']:
        require(isinstance(name, str) and bool(name) and Path(name).name == name,
                'Invalid shadow model manifest')
        resident(snapshot / name)


def start_shadow_runtime(state, args, port, decision, warmup_request, log):
    wired_mib = getattr(args, 'shadow_wired_limit_mib', None)
    wired_bytes = wired_limit_bytes(wired_mib)
    model = json.loads(decision['profile']['profileJson'])['model']
    require(('allocatorWiredLimitBytes' in model) == (wired_bytes is not None)
            and model.get('allocatorWiredLimitBytes') == wired_bytes
            and (wired_bytes is None or type(model['allocatorWiredLimitBytes']) is int),
            'Shadow wired budget differs from pinned profile')
    require('wiredLimitMiB' not in decision if wired_bytes is None else
            type(decision.get('wiredLimitMiB')) is int and decision['wiredLimitMiB'] == wired_mib,
            'Shadow wired budget differs from recorded plan')
    arguments = [str(args.shadow_python.absolute()), '-m', 'decision_runtime', 'serve',
        '--manifest', str(args.shadow_manifest.resolve()), '--policy', str(ROOT / SHADOW_POLICY),
        '--max-tokens', '2048', '--cache-limit-mib', '128', '--inference-timeout-ms', '5000',
        '--exit-on-backend-unavailable', '--port', str(port)]
    if wired_mib is not None:
        arguments += ['--wired-limit-mib', str(wired_mib)]
    process = subprocess.Popen(arguments, cwd=ROOT,
        stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        env={**os.environ, 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1'})
    state['process'] = process
    deadline = time.monotonic() + 30
    while True:
        require(process.poll() is None, 'Owned shadow runtime exited during startup')
        try:
            state['health'] = request(port, '/health')
            break
        except (OSError, ValueError, http.client.HTTPException):
            require(time.monotonic() < deadline, 'Owned shadow runtime startup timeout')
            time.sleep(.1)
    health = state['health']
    require(health.get('status') == 'ready' and health.get('mode') == 'shadow', 'Shadow runtime not ready')
    require(all(health[key] == decision['profile'][key] for key in ['profileJson', 'profileSha256']), 'Live shadow profile changed')
    state['warmup'] = request(port, '/v1/decisions', warmup_request, 12, capture_status=True)
    warmup = state['warmup']
    require(warmup['httpStatus'] == 200 and warmup['result'].get('status') in ['ok', 'abstain'],
            'Shadow warmup failed: ' + str(warmup['result'].get('reason')))


def main():
    if not __debug__:
        raise RuntimeError('Assertions must be enabled')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    parser.add_argument('--shadow-python', type=Path, help='Resident MLX Python; requires --shadow-manifest')
    parser.add_argument('--shadow-manifest', type=Path, help='Verified resident decider manifest; enables the v2 shadow gate')
    parser.add_argument('--shadow-profile', type=Path, help='Committed runtime profile export; default retains the historical profile')
    parser.add_argument('--shadow-wired-limit-mib', type=int, help='Explicit per-process wired budget; requires a matching committed shadow profile')
    parser.add_argument('--shadow-recovery', action='store_true', help='Kill/restart the owned shadow server during each workflow')
    parser.add_argument('--shadow-resources', action='store_true', help='Observe owned-process and system memory during shadow recovery')
    args = parser.parse_args()
    wired_limit_bytes(args.shadow_wired_limit_mib)
    require(bool(args.shadow_python) == bool(args.shadow_manifest), 'Both shadow runtime arguments are required')
    shadow_enabled = args.shadow_manifest is not None
    require(not args.shadow_profile or shadow_enabled, 'An explicit shadow profile requires both shadow runtime arguments')
    require(args.shadow_wired_limit_mib is None or (shadow_enabled and args.shadow_profile is not None),
            'An explicit wired budget requires both shadow runtime arguments and a matching shadow profile')
    reference_path, explicit_profile = shadow_profile(args.shadow_profile, wired_limit_mib=args.shadow_wired_limit_mib) if args.shadow_profile else (SHADOW_REFERENCE, None)
    require(not args.shadow_recovery or shadow_enabled, 'Shadow recovery requires both shadow runtime arguments')
    require(not args.shadow_resources or args.shadow_recovery, 'Shadow resource diagnostics requires recovery mode')
    version_number = 4 if args.shadow_resources else 3 if args.shadow_recovery else 2 if shadow_enabled else 1
    resource_sources = []
    if args.shadow_resources:
        from scripts.lib.shadow_resource_sample import ResourceSampler, PLAN as RESOURCE_PLAN, SOURCE_PATHS
        resource_sources = SOURCE_PATHS
    directory = args.evidence_dir.resolve()
    require(directory.is_relative_to(ROOT / 'docs') and not directory.exists(), 'Use a new evidence directory under docs')
    if args.shadow_resources:
        require(directory.is_relative_to(ROOT / 'docs/private'), 'Resource diagnostics must stay under ignored docs/private')
    require(platform.system() == 'Darwin', 'This installed-model experiment requires the macOS host')
    commit = command(['git', 'rev-parse', 'HEAD'])
    measured_paths = [*SOURCES, *(SHADOW_SOURCES if shadow_enabled else []), *(SHADOW_RECOVERY_SOURCES if args.shadow_recovery else []),
                      *resource_sources, *([reference_path] if explicit_profile else [])]
    snapshot = subprocess.check_output(['git', 'archive', commit, '--', *measured_paths], cwd=ROOT, timeout=15)
    sources = {}
    with tarfile.open(fileobj=io.BytesIO(snapshot)) as archive:
        for member in archive:
            if member.isfile():
                data = archive.extractfile(member).read()
                require((ROOT / member.name).read_bytes() == data, 'Commit measured sources before running')
                sources[member.name] = sha(data)
    require(all(name in sources for name in measured_paths if not (ROOT / name).is_dir()), 'Incomplete frozen source snapshot')
    require(not command(['git', 'ls-files', '--others', '--exclude-standard', '--', *measured_paths]), 'Untracked measured sources')
    decision = None
    if shadow_enabled:
        require_resident_shadow_model(args.shadow_manifest.resolve())
        sys.path.insert(0, str(ROOT))
        from decision_runtime.model_store import verify_manifest
        manifest, _ = verify_manifest(args.shadow_manifest.resolve())
        expected = explicit_profile or json.loads((ROOT / SHADOW_REFERENCE).read_text())['decision']
        profile = json.loads(expected['profileJson'])
        require(sha(expected['profileJson'].encode()) == expected['profileSha256'], 'Invalid reference decision profile')
        require(all(manifest[key] == profile['model'][key] for key in ['repository', 'revision', 'artifactSha256']), 'Shadow model changed')
        requirements = dict(line.split('==') for line in (ROOT / 'decision_runtime/requirements-mlx.txt').read_text().splitlines()
                            if line and not line.startswith('#'))
        code = 'import importlib.metadata,json,platform,sys; print(json.dumps({"python":platform.python_version(),"packages":{k:importlib.metadata.version(k) for k in sys.argv[1:]}}))'
        runtime = json.loads(subprocess.check_output([str(args.shadow_python.absolute()), '-c', code, *requirements], cwd=ROOT, timeout=15))
        require(runtime['packages'] == requirements, 'Shadow dependencies differ from the pinned requirements')
        decision = {'profile': expected, 'manifest': {key: value for key, value in manifest.items() if key != 'snapshot'},
                    'policy': json.loads((ROOT / SHADOW_POLICY).read_text()), 'runtime': runtime,
                    'referencePath': reference_path, 'policyPath': SHADOW_POLICY, 'warmupCalls': 3 if args.shadow_recovery else 1}
        if explicit_profile:
            decision['referenceFormat'] = 'runtime-profile-v1'
        if args.shadow_wired_limit_mib is not None:
            decision['wiredLimitMiB'] = args.shadow_wired_limit_mib
    model_root = Path(os.environ.get('OLLAMA_MODELS', str(Path.home() / '.ollama/models')))
    for name, digest in shared.MODELS.items():
        require(sha((model_root / 'manifests/registry.ollama.ai/library' / name.replace(':', '/')).read_bytes()) == digest,
                'Installed model weights changed')
    host = {'system': platform.system(), 'release': platform.release(), 'architecture': platform.machine(),
            'python': platform.python_version(), 'node': command(['node', '--version']),
            'cpu': command(['/usr/sbin/sysctl', '-n', 'machdep.cpu.brand_string']),
            'memoryBytes': int(command(['/usr/sbin/sysctl', '-n', 'hw.memsize'])),
            'docker': command(['docker', 'version', '--format', '{{.Server.Version}}'])}
    require(host['memoryBytes'] >= 24 * 1024**3, 'At least 24 GiB unified memory required')
    settings = {'OLLAMA_NO_CLOUD': '1', 'OLLAMA_MAX_LOADED_MODELS': '2', 'OLLAMA_NUM_PARALLEL': '1',
                'OLLAMA_CONTEXT_LENGTH': '8192', 'OLLAMA_KEEP_ALIVE': '5m'}
    plan = {'schema': f'agat.temporal.real-rag-plan.v{version_number}', 'implementationCommit': commit, 'sourceSha256': sources,
            'host': host, 'models': shared.MODELS, 'ollamaSettings': settings, 'fixturePath': shared.FIXTURE,
            'fixture': json.loads((ROOT / shared.FIXTURE).read_text()), 'shadow': shadow_enabled,
            'transports': ['isolated', 'session'], 'concurrency': 1, 'workloadBudgetSeconds': 600,
            'databaseIsolation': 'one_owned_server_per_transport',
            'postgresImage': 'postgres:17.6-alpine', 'temporalImage': 'temporalio/temporal:1.8.1',
            'qualification': 'not_assessed', 'routingEnabled': False}
    if decision:
        plan['decision'] = decision
    if args.shadow_recovery:
        plan['shadowRecovery'] = {'protocol': 'private-files-v1', 'signal': 'SIGKILL',
            'actions': [f'{transport}/{action}' for transport in plan['transports'] for action in ['kill', 'restart']]}
    if args.shadow_resources:
        plan['resources'] = RESOURCE_PLAN
    directory.mkdir(parents=True)
    write(directory / 'plan.json', plan)
    started = time.monotonic()
    ollama = workload = shadow_runtime = None
    pids, containers, container_details = set(), set(), {}
    samples, cleanup_errors, warmup, workloads = [], [], [], []
    version = initial = unloaded = failure = None
    decision_before = decision_after = None
    decision_warmup = None
    shadow_control = None
    resources = None
    def sample_resources(phase):
        if resources is not None:
            roots = {name: process.pid for name, process in [('shadow', shadow_runtime), ('ollama', ollama), ('workload', workload)]
                     if process is not None and process.poll() is None}
            row = resources.sample(phase, roots)
            pids.update(resources.owned)
            return row
    # Allocate private destinations before any owned process starts. Writing
    # directly avoids a late copy that can fail and lose temporary diagnostics.
    private_logs = ROOT / 'docs/private/temporal-real-rag' / directory.relative_to(ROOT / 'docs')
    private_logs.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix='agat-temporal-real-rag-') as temporary:
        temporary = Path(temporary)
        shim = temporary / 'python3'
        shim.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' "$@"\n')
        shim.chmod(0o700)
        with open_private_log(private_logs / 'ollama.log') as model_log, open_private_log(private_logs / 'decision.log') as decision_log, (directory / 'tests.log').open('xb') as test_log:
            with socket.socket() as bound:
                bound.bind(('127.0.0.1', 0))
                port = bound.getsockname()[1]
            try:
                shadow_environment = {}
                if args.shadow_resources:
                    resources = ResourceSampler(started)
                    before = sample_resources('before_models')
                    require(not resources.errors, 'Resource counters unavailable')
                    require(before['pressureDispatchLevel'] != 4, 'Critical memory pressure before model admission')
                    shadow_environment['AGAT_TEMPORAL_SHADOW_RESOURCES'] = 'true'
                if shadow_enabled:
                    with socket.socket() as shadow_bound:
                        shadow_bound.bind(('127.0.0.1', 0)); shadow_port = shadow_bound.getsockname()[1]
                    warmup_request = {'schemaVersion': 'agat.decision.v1', 'id': 'temporal-shadow-warmup',
                                      'state': plan['fixture']['input'], **plan['fixture']['shadow']}
                    initial_state = {}
                    try:
                        if args.shadow_recovery:
                            from scripts.lib.temporal_shadow_control import ShadowRecoveryControl
                            shadow_control = ShadowRecoveryControl(temporary / 'control',
                                lambda state: start_shadow_runtime(state, args, shadow_port, decision, warmup_request, decision_log),
                                shared.inventory, shared.stop, started)
                            shadow_control.start()
                            shadow_environment['AGAT_TEMPORAL_SHADOW_CONTROL'] = str(shadow_control.directory)
                        else:
                            start_shadow_runtime(initial_state, args, shadow_port, decision, warmup_request, decision_log)
                    finally:
                        initial_state = shadow_control.state if shadow_control else initial_state
                        shadow_runtime = initial_state.get('process')
                        decision_before, decision_warmup = initial_state.get('health'), initial_state.get('warmup')
                        if shadow_runtime is not None:
                            pids.add(shadow_runtime.pid)
                    shadow_environment['AGAT_TEMPORAL_REAL_DECISION_URL'] = f'http://127.0.0.1:{shadow_port}'
                    sample_resources('after_shadow_warmup')
                ollama = subprocess.Popen(['ollama', 'serve'], cwd=ROOT, stdout=model_log, stderr=subprocess.STDOUT,
                                          start_new_session=True, env={**os.environ, **settings, 'OLLAMA_HOST': f'127.0.0.1:{port}'})
                pids.add(ollama.pid)
                deadline = time.monotonic() + 30
                while True:
                    require(ollama.poll() is None, 'Owned model server exited')
                    try:
                        version = request(port, '/api/version')
                        break
                    except (OSError, ValueError, http.client.HTTPException):
                        require(time.monotonic() < deadline, 'Owned model server startup timeout')
                        time.sleep(.1)
                initial = request(port, '/api/ps')
                require(initial.get('models') == [], 'Owned model server must start empty')
                for endpoint, payload in [('/api/embed', {'model': 'embeddinggemma:latest', 'input': ['Локальная проверка'],
                        'truncate': False, 'keep_alive': '5m', 'options': {'num_ctx': 2048}}),
                    ('/api/chat', {'model': 'qwen3:8b', 'messages': [{'role': 'user', 'content': 'Ответь одним словом: готово.'}],
                        'stream': False, 'think': False, 'keep_alive': '5m',
                        'options': {'temperature': .2, 'seed': 0, 'num_ctx': 8192, 'num_predict': 8}})]:
                    result = request(port, endpoint, payload, 60)
                    require(result.get('model') == payload['model'], 'Warmup model mismatch')
                    warmup.append({'model': payload['model'], 'nativeTotalMs': result['total_duration'] / 1e6,
                                   'nativeLoadMs': result['load_duration'] / 1e6})
                sample_resources('after_ollama_warmup')
                environment = {key: value for key, value in os.environ.items() if not key.startswith(('AGAT_', 'OTEL_'))}
                deadline, next_sample, next_resources = time.monotonic() + plan['workloadBudgetSeconds'], 0, 0
                print('Owned models warmed; real PostgreSQL/Temporal RAG started', flush=True)
                for transport in plan['transports']:
                    workload = subprocess.Popen(['bash', 'scripts/test-temporal-postgres-rag.sh',
                        '--test-skip-pattern=Temporal RAG (sqlite|postgresql)/'], cwd=ROOT, stdout=test_log,
                        stderr=subprocess.STDOUT, start_new_session=True, env={**environment, **shadow_environment,
                            'PATH': str(temporary) + os.pathsep + os.environ['PATH'], 'AGAT_OTEL_ENABLED': 'false', 'OTEL_SDK_DISABLED': 'true',
                            'AGAT_TEMPORAL_REAL_MODEL_URL': f'http://127.0.0.1:{port}',
                            'AGAT_TEMPORAL_REAL_RAG_PLAN': str(directory / 'plan.json'), 'AGAT_TEMPORAL_REAL_RAG_TRANSPORT': transport})
                    pids.add(workload.pid)
                    while workload.poll() is None:
                        require(time.monotonic() < deadline, 'Workload budget exceeded')
                        if resources is not None and time.monotonic() >= next_resources:
                            sample_resources(transport)
                            next_resources = time.monotonic() + RESOURCE_PLAN['intervalSeconds']
                        pids.update(shared.inventory(ollama.pid)[0]); pids.update(shared.inventory(workload.pid)[0])
                        if shadow_control is not None:
                            shadow_control.poll(transport)
                            pids.update(shadow_control.sample())
                            shadow_runtime = shadow_control.state['process']
                        elif shadow_runtime is not None:
                            require(shadow_runtime.poll() is None, 'Owned shadow runtime exited')
                            pids.update(shared.inventory(shadow_runtime.pid)[0])
                        containers.update(own_containers(pids))
                        for name in containers - container_details.keys():
                            inspected = json.loads(command(['docker', 'inspect', name]))[0]
                            container_details[name] = {'id': inspected['Id'], 'image': inspected['Config']['Image'], 'imageId': inspected['Image']}
                        if time.monotonic() >= next_sample:
                            samples.append({'elapsedMs': (time.monotonic() - started) * 1000, 'models': request(port, '/api/ps')['models']})
                            next_sample = time.monotonic() + 10
                        time.sleep(.5)
                    workloads.append({'transport': transport, 'pid': workload.pid, 'exitCode': workload.returncode})
                    require(workload.returncode == 0, 'Live integration tests failed; inspect tests.log')
                    require(json.loads((directory / f'{transport}.json').read_text()).get('status') == 'pass', 'Incomplete transport evidence')
                    print(transport + ': recovery and native replay passed', flush=True)
                if shadow_enabled:
                    decision_after = request(shadow_port, '/health')
                    require(decision_after == decision_before, 'Shadow runtime health/profile changed during workload')
                if shadow_control:
                    require(shadow_control.failure is None and len(shadow_control.events) == 4, 'Incomplete shadow failure/recovery sequence')
                if resources:
                    require(not resources.errors, 'Resource observations incomplete')
            except Exception as error:
                failure = {'type': type(error).__name__, 'reason': str(error)[:300]}
                # Let the integration harness persist the failed response and
                # close its children before forcing the wrapper to stop.
                drain_deadline = time.monotonic() + 5
                while workload is not None and workload.poll() is None and time.monotonic() < drain_deadline:
                    time.sleep(.1)
            finally:
                sample_resources('before_cleanup')
                try:
                    shared.stop(workload)
                except Exception as error:
                    cleanup_errors.append(type(error).__name__)
                if ollama is not None and ollama.poll() is None:
                    try:
                        for name in shared.MODELS:
                            request(port, '/api/generate', {'model': name, 'stream': False, 'keep_alive': 0}, 30)
                        unloaded = request(port, '/api/ps')
                        require(unloaded.get('models') == [], 'Owned models failed to unload')
                    except Exception as error:
                        cleanup_errors.append(type(error).__name__)
                try:
                    if shadow_control is not None:
                        try:
                            shadow_control.close()
                        finally:
                            pids.update(shadow_control.owned)
                            if shadow_control.state and 'process' in shadow_control.state:
                                shadow_runtime = shadow_control.state['process']
                    elif shadow_runtime is not None:
                        was_running = shadow_runtime.poll() is None
                        stop_owned_process(shadow_runtime, pids, cleanup_errors)
                        require(not was_running or shadow_runtime.returncode == 130, 'Shadow runtime did not drain on SIGTERM')
                except Exception as error:
                    cleanup_errors.append(type(error).__name__)
                stop_owned_process(ollama, pids, cleanup_errors)
                cleanup_owned_containers(pids, containers, cleanup_errors)
                sample_resources('after_cleanup')
        model_log_sha = sha((private_logs / 'ollama.log').read_bytes())
        decision_log_sha = sha((private_logs / 'decision.log').read_bytes())
    phase_hashes = {}
    for transport in plan['transports']:
        phase_path = directory / f'{transport}.json'
        if phase_path.exists():
            phase_hashes[phase_path.name] = sha(phase_path.read_bytes())
            pids.update(child['pid'] for child in json.loads(phase_path.read_text()).get('children', []))
    remaining = remaining_owned_processes(pids, cleanup_errors)
    if (cleanup_errors or remaining) and failure is None:
        failure = {'type': 'CleanupError', 'reason': 'Owned resources did not close or could not be verified'}
    if resources is not None and resources.errors and failure is None:
        failure = {'type': 'ObservationError', 'reason': 'Resource observations incomplete'}
    report = {'schema': f'agat.temporal.real-rag-launcher.v{version_number}', 'status': 'fail' if failure else 'pass', 'failure': failure,
              'planSha256': sha((directory / 'plan.json').read_bytes()), 'phaseSha256': phase_hashes,
              'elapsedMs': (time.monotonic() - started) * 1000, 'ollamaVersion': version, 'warmup': warmup,
              'modelsBefore': initial, 'modelsAfterUnload': unloaded, 'ownedPids': sorted(pids), 'remainingOwnedPids': remaining,
              'containers': container_details, 'cleanupErrors': cleanup_errors,
              'ollamaPid': ollama.pid if ollama else None, 'ollamaExitCode': ollama.returncode if ollama else None,
              'workloadPid': workload.pid if workload else None, 'workloadExitCode': workload.returncode if workload else None,
              'workloads': workloads,
              'loadedModelSamples': samples, 'logSha256': {'ollama.log': model_log_sha, 'tests.log': sha((directory / 'tests.log').read_bytes())}}
    if shadow_enabled:
        report['shadowRuntime'] = {'pid': shadow_runtime.pid if shadow_runtime else None,
            'exitCode': shadow_runtime.returncode if shadow_runtime else None, 'before': decision_before, 'after': decision_after,
            'warmup': decision_warmup}
        report['logSha256']['decision.log'] = decision_log_sha
    if shadow_control:
        report['shadowRecovery'] = shadow_control.report()
        report['shadowJournalSha256'] = {f'{transport}.shadow.jsonl': sha((directory / f'{transport}.shadow.jsonl').read_bytes())
            for transport in plan['transports'] if (directory / f'{transport}.shadow.jsonl').exists()}
    if resources is not None:
        report['resources'] = resources.report()
    write(directory / 'launcher.json', report)
    print(report['status'], failure, flush=True)
    return 1 if failure else 0


if __name__ == '__main__':
    raise SystemExit(main())
