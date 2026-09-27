#!/usr/bin/env python3
"""Own a bounded ABBA RAG experiment with real workers and installed local models."""
import argparse
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import platform
import shlex
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
MODELS = {'qwen3:8b': '500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41',
          'embeddinggemma:latest': '85462619ee721b466c5927d109d4cb765861907d5417b9109caebc4e614679f1'}
FIXTURE = 'docs/qualification/local-decisions/performance/rag-workflow.fixture.json'
PROFILE = 'docs/qualification/local-decisions/calibration/evidence/2026-09-26-frozen-profile/runtime-profile.json'
SOURCE_PATHS = ['apps/coordinator/src', 'workers', 'package.json', 'package-lock.json', 'apps/coordinator/package.json',
                'scripts/profile-embedding-rag.py', 'scripts/embedding-rag-worker-probe.py', 'scripts/benchmark-embedding-rag.ts',
                'scripts/lib/decision-primary-workflow.ts', 'scripts/lib/decision-rag.ts', 'scripts/lib/decision-shadow-proxy.ts', FIXTURE, PROFILE]


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def command(args):
    return subprocess.check_output(args, cwd=ROOT, timeout=10, text=True).strip()


def request(port, path, body=None):
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    try:
        conn.request('GET' if body is None else 'POST', path, body=None if body is None else json.dumps(body),
                     headers={'Content-Type': 'application/json'})
        response = conn.getresponse()
        raw = response.read(524289)
        require(response.status == 200 and len(raw) <= 524288, 'Owned Ollama control request failed')
        return json.loads(raw)
    finally:
        conn.close()


def inventory(parent):
    table = {int(parts[0]): int(parts[1]) for line in command(['ps', '-axo', 'pid=,ppid=']).splitlines()
             if len(parts := line.split()) == 2}
    found = {parent}
    while True:
        extra = {pid for pid, ppid in table.items() if ppid in found} - found
        if not extra:
            return found, set(table)
        found.update(extra)


def stop(process):
    if process is None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(5)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(3)


def main():
    if not __debug__:
        raise RuntimeError('Assertions must be enabled')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    args = parser.parse_args()
    directory = args.evidence_dir.resolve()
    require(directory.is_relative_to(ROOT / 'docs') and not directory.exists(), 'Use a new evidence directory under docs')
    require(platform.system() == 'Darwin', 'This installed-model experiment is scoped to the macOS host')
    commit = command(['git', 'rev-parse', 'HEAD'])
    archived = subprocess.check_output(['git', 'archive', commit, '--', *SOURCE_PATHS], cwd=ROOT, timeout=10)
    sources = {}
    with tarfile.open(fileobj=io.BytesIO(archived)) as archive:
        for item in archive:
            if item.isfile():
                content = archive.extractfile(item).read()
                require((ROOT / item.name).read_bytes() == content, 'Commit measured source files before running')
                sources[item.name] = sha(content)
    require(all(name in sources for name in SOURCE_PATHS if not (ROOT / name).is_dir()), 'Incomplete source snapshot')
    untracked = command(['git', 'ls-files', '--others', '--exclude-standard', '--', 'workers', 'apps/coordinator/src'])
    require(not untracked, 'Untracked runtime sources are not allowed')
    model_root = Path(os.environ.get('OLLAMA_MODELS', str(Path.home() / '.ollama/models')))
    for name, digest in MODELS.items():
        require(sha((model_root / 'manifests/registry.ollama.ai/library' / name.replace(':', '/')).read_bytes()) == digest,
                'Installed weights changed')
    host = {'system': platform.system(), 'release': platform.release(), 'architecture': platform.machine(),
            'python': platform.python_version(), 'node': command(['node', '--version']),
            'cpu': command(['/usr/sbin/sysctl', '-n', 'machdep.cpu.brand_string']),
            'memoryBytes': int(command(['/usr/sbin/sysctl', '-n', 'hw.memsize']))}
    require(host['memoryBytes'] >= 24 * 1024**3, 'At least 24 GiB unified memory required')
    settings = {'OLLAMA_NO_CLOUD': '1', 'OLLAMA_MAX_LOADED_MODELS': '2', 'OLLAMA_NUM_PARALLEL': '1',
                'OLLAMA_CONTEXT_LENGTH': '8192', 'OLLAMA_KEEP_ALIVE': '5m'}
    plan = {'schema': 'agat.embedding.rag-plan.v1', 'implementationCommit': commit, 'sourceSha256': sources,
            'host': host, 'models': MODELS, 'ollamaSettings': settings, 'fixturePath': FIXTURE,
            'fixture': json.loads((ROOT / FIXTURE).read_text()), 'profilePath': PROFILE,
            'metadataId': 'embedding_transport_rag', 'samplePeriodMs': 100, 'nodeBudgetSeconds': 660,
            'blocks': [{'transport': mode, 'phase': {'id': f'{index}_{mode}', 'concurrency': 2, 'runs': 3, 'shadow': False}}
                       for index, mode in enumerate(('isolated', 'session', 'session', 'isolated'))],
            'qualification': 'not_assessed', 'routingEnabled': False}
    directory.mkdir(parents=True)
    write(directory / 'plan.json', plan)
    started = time.monotonic()
    ollama = node = None
    owned = set()
    loaded, progress, cleanup_errors = [], [], []
    version = unloaded = initial = failure = None
    with tempfile.TemporaryDirectory(prefix='agat-embedding-rag-') as temporary:
        temporary = Path(temporary)
        shim = temporary / 'python3'
        shim.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' ' + shlex.quote(str(ROOT / 'scripts/embedding-rag-worker-probe.py')) + ' "$@"\n')
        shim.chmod(0o700)
        with (temporary / 'ollama.log').open('wb') as ollama_log, (temporary / 'workflow.log').open('wb') as node_log:
            with socket.socket() as bound:
                bound.bind(('127.0.0.1', 0))
                port = bound.getsockname()[1]
            try:
                ollama = subprocess.Popen(['ollama', 'serve'], cwd=ROOT, stdout=ollama_log, stderr=subprocess.STDOUT,
                                           start_new_session=True, env={**os.environ, **settings, 'OLLAMA_HOST': f'127.0.0.1:{port}'})
                owned.add(ollama.pid)
                deadline = time.monotonic() + 30
                while True:
                    require(ollama.poll() is None, 'Owned Ollama exited')
                    try:
                        version = request(port, '/api/version')
                        break
                    except (OSError, ValueError, http.client.HTTPException):
                        require(time.monotonic() < deadline, 'Owned Ollama startup timeout')
                        time.sleep(.1)
                initial = request(port, '/api/ps')
                require(initial.get('models') == [], 'Owned server must start without loaded models')
                node = subprocess.Popen(['node', '--import', 'tsx', 'scripts/benchmark-embedding-rag.ts',
                                         '--plan', str(directory / 'plan.json'), '--primary-url', f'http://127.0.0.1:{port}'],
                                        cwd=ROOT, stdout=node_log, stderr=subprocess.STDOUT, start_new_session=True,
                                        env={**os.environ, 'PATH': str(temporary) + os.pathsep + os.environ['PATH'],
                                             'AGAT_OTEL_ENABLED': 'false', 'OTEL_SDK_DISABLED': 'true'})
                owned.add(node.pid)
                deadline = time.monotonic() + plan['nodeBudgetSeconds']
                next_sample = offset = 0
                print('Owned Ollama ready; ABBA RAG workload started', flush=True)
                while node.poll() is None:
                    require(time.monotonic() < deadline, 'Workload budget exceeded')
                    owned.update(inventory(ollama.pid)[0])
                    owned.update(inventory(node.pid)[0])
                    with (temporary / 'workflow.log').open() as stream:
                        stream.seek(offset)
                        for line in stream:
                            if line.startswith(('block ', 'observed:', 'incomplete')):
                                progress.append(line.strip())
                                print(line.strip(), flush=True)
                        offset = stream.tell()
                    if time.monotonic() >= next_sample:
                        loaded.append({'elapsedMs': (time.monotonic() - started) * 1000, 'models': request(port, '/api/ps')['models']})
                        next_sample = time.monotonic() + 10
                    time.sleep(.5)
                require(node.returncode == 0, 'Workflow CLI failed')
                for name in MODELS:
                    request(port, '/api/generate', {'model': name, 'stream': False, 'keep_alive': 0})
                unloaded = request(port, '/api/ps')
                require(unloaded.get('models') == [], 'Owned models failed to unload')
            except Exception as error:
                failure = {'type': type(error).__name__, 'reason': str(error)[:300]}
            finally:
                for process in (node, ollama):
                    try:
                        if process is not None:
                            owned.update(inventory(process.pid)[0])
                        stop(process)
                    except Exception as error:
                        cleanup_errors.append(type(error).__name__)
        log_hashes = {name: sha((temporary / name).read_bytes()) for name in ('ollama.log', 'workflow.log')}
        # Publish only fixed harness progress, never Ollama's environment dump.
        progress = [line for line in (temporary / 'workflow.log').read_text(errors='replace').splitlines()
                    if line.startswith(('block ', 'observed:', 'incomplete'))]
        if failure:
            print('\n'.join(progress[-4:]), flush=True)
    probe_hashes = {}
    for block in plan['blocks']:
        probe_path = directory / (block['phase']['id'] + '.worker.json')
        if probe_path.exists():
            probe_hashes[probe_path.name] = sha(probe_path.read_bytes())
            probe = json.loads(probe_path.read_text())
            owned.add(probe['pid'])
            owned.update(child['pid'] for child in probe['children'])
    remaining = sorted(owned & inventory(os.getpid())[1])
    if (cleanup_errors or remaining) and failure is None:
        failure = {'type': 'CleanupError', 'reason': 'Owned processes did not close'}
    workflow_path = directory / 'workflow.json'
    report = {'schema': 'agat.embedding.rag-launcher.v1', 'status': 'incomplete' if failure else 'observed', 'failure': failure,
              'planSha256': sha((directory / 'plan.json').read_bytes()), 'elapsedMs': (time.monotonic() - started) * 1000,
              'workflowSha256': sha(workflow_path.read_bytes()) if workflow_path.exists() else None,
              'probeSha256': probe_hashes, 'ollamaVersion': version, 'modelsBefore': initial, 'modelsAfterUnload': unloaded,
              'ownedPids': sorted(owned), 'remainingOwnedPids': remaining, 'cleanupErrors': cleanup_errors,
              'ollamaPid': ollama.pid if ollama else None, 'ollamaExitCode': ollama.returncode if ollama else None,
              'nodePid': node.pid if node else None, 'nodeExitCode': node.returncode if node else None,
              'loadedModelSamples': loaded, 'progress': progress, 'logSha256': log_hashes}
    write(directory / 'launcher.json', report)
    print(report['status'], failure, flush=True)
    return 1 if failure else 0


if __name__ == '__main__':
    raise SystemExit(main())
