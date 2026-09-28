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
import subprocess
import sys
import tarfile
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
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


def own_containers(pids):
    names = command(['docker', 'ps', '--all', '--format', '{{.Names}}', '--filter', 'name=agat-temporal-']).splitlines()
    return {name for name in names if (match := re.fullmatch(r'agat-temporal-(?:postgres-)?rag-(\d+)-\d+', name))
            and int(match[1]) in pids}


def main():
    if not __debug__:
        raise RuntimeError('Assertions must be enabled')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    parser.add_argument('--shadow-python', type=Path, help='Resident MLX Python; requires --shadow-manifest')
    parser.add_argument('--shadow-manifest', type=Path, help='Verified resident decider manifest; enables the v2 shadow gate')
    args = parser.parse_args()
    require(bool(args.shadow_python) == bool(args.shadow_manifest), 'Both shadow runtime arguments are required')
    shadow_enabled = args.shadow_manifest is not None
    directory = args.evidence_dir.resolve()
    require(directory.is_relative_to(ROOT / 'docs') and not directory.exists(), 'Use a new evidence directory under docs')
    require(platform.system() == 'Darwin', 'This installed-model experiment requires the macOS host')
    commit = command(['git', 'rev-parse', 'HEAD'])
    measured_paths = [*SOURCES, *(SHADOW_SOURCES if shadow_enabled else [])]
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
        sys.path.insert(0, str(ROOT))
        from decision_runtime.model_store import verify_manifest
        manifest, _ = verify_manifest(args.shadow_manifest.resolve())
        expected = json.loads((ROOT / SHADOW_REFERENCE).read_text())['decision']
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
                    'referencePath': SHADOW_REFERENCE, 'policyPath': SHADOW_POLICY, 'warmupCalls': 1}
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
    plan = {'schema': 'agat.temporal.real-rag-plan.v2' if shadow_enabled else 'agat.temporal.real-rag-plan.v1', 'implementationCommit': commit, 'sourceSha256': sources,
            'host': host, 'models': shared.MODELS, 'ollamaSettings': settings, 'fixturePath': shared.FIXTURE,
            'fixture': json.loads((ROOT / shared.FIXTURE).read_text()), 'shadow': shadow_enabled,
            'transports': ['isolated', 'session'], 'concurrency': 1, 'workloadBudgetSeconds': 600,
            'databaseIsolation': 'one_owned_server_per_transport',
            'postgresImage': 'postgres:17.6-alpine', 'temporalImage': 'temporalio/temporal:1.8.1',
            'qualification': 'not_assessed', 'routingEnabled': False}
    if decision:
        plan['decision'] = decision
    directory.mkdir(parents=True)
    write(directory / 'plan.json', plan)
    started = time.monotonic()
    ollama = workload = shadow_runtime = None
    pids, containers, container_details = set(), set(), {}
    samples, cleanup_errors, warmup, workloads = [], [], [], []
    version = initial = unloaded = failure = None
    decision_before = decision_after = None
    decision_warmup = None
    with tempfile.TemporaryDirectory(prefix='agat-temporal-real-rag-') as temporary:
        temporary = Path(temporary)
        shim = temporary / 'python3'
        shim.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' "$@"\n')
        shim.chmod(0o700)
        with (temporary / 'ollama.log').open('wb') as model_log, (temporary / 'decision.log').open('wb') as decision_log, (directory / 'tests.log').open('xb') as test_log:
            with socket.socket() as bound:
                bound.bind(('127.0.0.1', 0))
                port = bound.getsockname()[1]
            try:
                shadow_environment = {}
                if shadow_enabled:
                    with socket.socket() as shadow_bound:
                        shadow_bound.bind(('127.0.0.1', 0)); shadow_port = shadow_bound.getsockname()[1]
                    shadow_runtime = subprocess.Popen([str(args.shadow_python.absolute()), '-m', 'decision_runtime', 'serve',
                        '--manifest', str(args.shadow_manifest.resolve()), '--policy', str(ROOT / SHADOW_POLICY),
                        '--max-tokens', '2048', '--cache-limit-mib', '128', '--inference-timeout-ms', '5000',
                        '--exit-on-backend-unavailable', '--port', str(shadow_port)], cwd=ROOT,
                        stdout=decision_log, stderr=subprocess.STDOUT, start_new_session=True,
                        env={**os.environ, 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1'})
                    pids.add(shadow_runtime.pid)
                    shadow_deadline = time.monotonic() + 30
                    while True:
                        require(shadow_runtime.poll() is None, 'Owned shadow runtime exited during startup')
                        try:
                            decision_before = request(shadow_port, '/health')
                            break
                        except (OSError, ValueError, http.client.HTTPException):
                            require(time.monotonic() < shadow_deadline, 'Owned shadow runtime startup timeout')
                            time.sleep(.1)
                    require(decision_before.get('status') == 'ready' and decision_before.get('mode') == 'shadow', 'Shadow runtime not ready')
                    require(all(decision_before[key] == decision['profile'][key] for key in ['profileJson', 'profileSha256']), 'Live shadow profile changed')
                    warmup_request = {'schemaVersion': 'agat.decision.v1', 'id': 'temporal-shadow-warmup',
                                      'state': plan['fixture']['input'], **plan['fixture']['shadow']}
                    decision_warmup = request(shadow_port, '/v1/decisions', warmup_request, 12, capture_status=True)
                    require(decision_warmup['httpStatus'] == 200 and decision_warmup['result'].get('status') in ['ok', 'abstain'],
                            'Shadow warmup failed: ' + str(decision_warmup['result'].get('reason')))
                    shadow_environment['AGAT_TEMPORAL_REAL_DECISION_URL'] = f'http://127.0.0.1:{shadow_port}'
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
                environment = {key: value for key, value in os.environ.items() if not key.startswith(('AGAT_', 'OTEL_'))}
                deadline, next_sample = time.monotonic() + plan['workloadBudgetSeconds'], 0
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
                        pids.update(shared.inventory(ollama.pid)[0]); pids.update(shared.inventory(workload.pid)[0])
                        if shadow_runtime is not None:
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
            except Exception as error:
                failure = {'type': type(error).__name__, 'reason': str(error)[:300]}
            finally:
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
                    if shadow_runtime is not None:
                        pids.update(shared.inventory(shadow_runtime.pid)[0])
                        was_running = shadow_runtime.poll() is None
                        shared.stop(shadow_runtime)
                        require(not was_running or shadow_runtime.returncode == 130, 'Shadow runtime did not drain on SIGTERM')
                except Exception as error:
                    cleanup_errors.append(type(error).__name__)
                try:
                    if ollama is not None:
                        pids.update(shared.inventory(ollama.pid)[0])
                    shared.stop(ollama)
                    # Names are admitted only when their embedded owner PID was
                    # observed in this launcher's process tree.
                    remaining_containers = own_containers(pids)
                    containers.update(remaining_containers)
                    for name in remaining_containers:
                        command(['docker', 'rm', '--force', name])
                    require(not own_containers(pids), 'Owned containers remain')
                except Exception as error:
                    cleanup_errors.append(type(error).__name__)
        model_log_sha = sha((temporary / 'ollama.log').read_bytes())
        decision_log_sha = sha((temporary / 'decision.log').read_bytes())
    phase_hashes = {}
    for transport in plan['transports']:
        phase_path = directory / f'{transport}.json'
        if phase_path.exists():
            phase_hashes[phase_path.name] = sha(phase_path.read_bytes())
            pids.update(child['pid'] for child in json.loads(phase_path.read_text()).get('children', []))
    remaining = sorted(pids & shared.inventory(os.getpid())[1])
    if (cleanup_errors or remaining) and failure is None:
        failure = {'type': 'CleanupError', 'reason': 'Owned resources did not close'}
    report = {'schema': 'agat.temporal.real-rag-launcher.v2' if shadow_enabled else 'agat.temporal.real-rag-launcher.v1', 'status': 'fail' if failure else 'pass', 'failure': failure,
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
    write(directory / 'launcher.json', report)
    print(report['status'], failure, flush=True)
    return 1 if failure else 0


if __name__ == '__main__':
    raise SystemExit(main())
