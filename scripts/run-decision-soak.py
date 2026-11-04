#!/usr/bin/env python3
"""Own one pinned local runtime for a private, bounded continuous endurance run."""
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
from decision_runtime.contracts import Policy, fingerprint
from decision_runtime.evaluation import load_dataset
from decision_runtime.model_store import verify_manifest
from scripts.lib.decision_soak import continuous_soak, validate_plan
from scripts.lib.decision_soak_verification import SOURCE_PATHS

spec = importlib.util.spec_from_file_location('soak_runtime', ROOT / 'scripts/run-temporal-real-rag.py')
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)
SOURCES = SOURCE_PATHS


def frozen_sources(paths):
    commit = runtime.command(['git', 'rev-parse', 'HEAD'])
    archive = subprocess.check_output(['git', 'archive', commit, '--', *paths], cwd=ROOT, timeout=15)
    sources = {}
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        for item in tar:
            if item.isfile():
                raw = tar.extractfile(item).read()
                runtime.require((ROOT / item.name).read_bytes() == raw, 'Commit measured soak sources before running')
                sources[item.name] = hashlib.sha256(raw).hexdigest()
    runtime.require(all(p in sources for p in paths if not (ROOT / p).is_dir()), 'Incomplete soak source snapshot')
    runtime.require(not runtime.command(['git', 'ls-files', '--others', '--exclude-standard', '--', *paths]),
                    'Untracked soak sources')
    return commit, sources


def main():
    if not __debug__:
        raise RuntimeError('Assertions must be enabled')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    parser.add_argument('--runtime-python', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--policy', type=Path, required=True)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--duration-s', type=int, default=7200)
    parser.add_argument('--block-s', type=int, default=900)
    args = parser.parse_args()
    directory = args.evidence_dir.resolve()
    runtime.require(directory.is_relative_to(ROOT / 'docs/private') and not directory.exists(),
                    'Select a new evidence directory under ignored docs/private')
    paths = [*SOURCES]
    for path in [args.dataset, args.profile, args.policy]:
        resolved = path.resolve()
        runtime.require(resolved.is_relative_to(ROOT / 'docs'), 'Soak inputs must be committed under docs')
        paths.append(resolved.relative_to(ROOT).as_posix())
    dataset = load_dataset(args.dataset)
    validate_plan(dataset, args.duration_s, args.block_s)
    commit, sources = frozen_sources(paths)
    runtime.require_resident_shadow_model(args.manifest.resolve())
    manifest, _ = verify_manifest(args.manifest.resolve())
    profile = json.loads(args.profile.read_text())
    runtime.require(all(profile['model'][key] == manifest[key]
                        for key in ('repository', 'revision', 'artifactSha256')), 'Soak model changed')
    policy = Policy.from_dict(json.loads(args.policy.read_text())).to_dict()
    runtime.require(profile['policy'] == {**policy, 'sha256': fingerprint(policy)}, 'Soak policy changed')
    requirements = dict(line.split('==') for line in (ROOT / 'decision_runtime/requirements-mlx.txt').read_text().splitlines()
                        if line and not line.startswith('#'))
    code = 'import importlib.metadata,json,platform,sys; print(json.dumps({"python":platform.python_version(),"packages":{k:importlib.metadata.version(k) for k in sys.argv[1:]}}))'
    environment = json.loads(subprocess.check_output([str(args.runtime_python.absolute()), '-c', code, *requirements],
                                                    cwd=ROOT, timeout=15))
    runtime.require(environment['packages'] == requirements, 'Soak dependencies differ from pinned requirements')
    profile_sha = fingerprint(profile)
    plan = sealed({'schemaVersion': 'agat.decision.continuous-soak-plan.v1', 'implementationCommit': commit,
            'sourceSha256': sources, 'profile': profile, 'profileSha256': profile_sha,
            'datasetPath': args.dataset.resolve().relative_to(ROOT).as_posix(),
            'profilePath': args.profile.resolve().relative_to(ROOT).as_posix(),
            'policyPath': args.policy.resolve().relative_to(ROOT).as_posix(),
            'model': {key: value for key, value in manifest.items() if key != 'snapshot'}, 'runtime': environment,
            'datasetSha256': dataset['sha256'], 'durationSeconds': args.duration_s,
            'blockSeconds': args.block_s, 'routingEnabled': False, 'qualification': 'not_assessed'})
    directory.mkdir(parents=True, mode=0o700)
    write_new(directory / 'plan.json', plan)
    process, health, report, failure = None, None, None, None
    owned, errors = set(), []
    stopping = [False]
    previous = {}

    def stop_requested(_signal, _frame):
        stopping[0] = True

    for signum in (signal.SIGINT, signal.SIGTERM):
        previous[signum] = signal.signal(signum, stop_requested)
    started = time.monotonic()
    try:
        with runtime.open_private_log(directory / 'runtime.log') as log, \
             runtime.open_private_log(directory / 'observations.jsonl') as journal:
            with socket.socket() as bound:
                bound.bind(('127.0.0.1', 0)); port = bound.getsockname()[1]
            process = subprocess.Popen([str(args.runtime_python.absolute()), '-m', 'decision_runtime', 'serve',
                '--manifest', str(args.manifest.resolve()), '--policy', str(args.policy.resolve()),
                '--max-tokens', '2048', '--cache-limit-mib', '128', '--inference-timeout-ms', '5000',
                '--exit-on-backend-unavailable', '--port', str(port)], cwd=ROOT, stdout=log,
                stderr=subprocess.STDOUT, start_new_session=True,
                env={**os.environ, 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1'})
            owned.add(process.pid)
            deadline = time.monotonic() + 30
            while True:
                runtime.require(not stopping[0], 'Soak cancelled during startup')
                runtime.require(process.poll() is None, 'Owned soak runtime exited during startup')
                try:
                    health = runtime.request(port, '/health')
                    break
                except (OSError, ValueError, http.client.HTTPException):
                    runtime.require(time.monotonic() < deadline, 'Owned soak runtime startup timeout')
                    time.sleep(.1)
            runtime.require(health.get('status') == 'ready' and health.get('mode') == 'shadow'
                            and health['profileSha256'] == profile_sha
                            and json.loads(health['profileJson']) == profile, 'Live soak profile changed')
            owned.update(runtime.shared.inventory(process.pid)[0])
            runtime.require(2 <= len(owned) <= 4, 'Expected owned HTTP and isolated inference processes')

            def observe(row):
                journal.write((json.dumps(row, allow_nan=False, separators=(',', ':')) + '\n').encode())
                journal.flush()

            report = continuous_soak(dataset, f'http://127.0.0.1:{port}', directory, profile_sha,
                duration_s=args.duration_s, block_s=args.block_s, process_pids=sorted(owned),
                cancel_requested=lambda: stopping[0], journal=observe,
                progress=lambda index, block: print(f"block={index + 1} status={block['status']} calls={block['summary']['attempts']}", flush=True))
    except Exception as error:
        failure = {'type': type(error).__name__, 'reason': str(error)[:300]}
    finally:
        if process is not None and process.poll() is None:
            runtime.stop_owned_process(process, owned, errors)
        remaining = runtime.remaining_owned_processes(owned, errors)
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    status = 'pass' if report and report['status'] == 'observed' and failure is None and not stopping[0] and not errors and remaining == [] else 'fail'
    result = sealed({'schemaVersion': 'agat.decision.continuous-soak-launcher.v1', 'status': status,
              'planSha256': runtime.sha((directory / 'plan.json').read_bytes()), 'failure': failure,
              'cancelled': stopping[0], 'elapsedMs': (time.monotonic() - started) * 1000,
              'observed': report, 'ownedPids': sorted(owned), 'remainingOwnedPids': remaining,
              'cleanupErrors': errors, 'runtimeExitCode': process.returncode if process else None,
              'logSha256': {name: runtime.sha((directory / name).read_bytes())
                            for name in ['runtime.log', 'observations.jsonl'] if (directory / name).exists()},
              'qualification': 'not_assessed', 'routingEnabled': False})
    write_new(directory / 'launcher.json', result)
    print(status, failure, flush=True)
    return 0 if status == 'pass' else 1


if __name__ == '__main__':
    raise SystemExit(main())
