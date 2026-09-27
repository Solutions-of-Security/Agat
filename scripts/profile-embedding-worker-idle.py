#!/usr/bin/env python3
"""Own and pin the ordinary-worker idle/resume model qualification."""
import argparse
import gzip
import importlib.util
import io
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import sys
import tarfile
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('worker_idle_guard', ROOT / 'scripts/profile-embedding-parent-guard.py')
guard = importlib.util.module_from_spec(spec); spec.loader.exec_module(guard)
SOURCES = ['apps/coordinator/src', 'workers', 'package.json', 'package-lock.json', 'apps/coordinator/package.json',
           'scripts/profile-embedding-worker-idle.py', 'scripts/benchmark-embedding-worker-idle.ts',
           'scripts/embedding-idle-worker-probe.py', 'scripts/verify-embedding-worker-idle.py', *guard.SOURCES]
SOURCES = sorted(set(SOURCES))


def phases():
    return [{'id': f'b{batch}-{i}', 'batch': batch, 'idleTimeout': idle, 'idleSeconds': 4}
            for batch in (1, 32) for i, idle in enumerate((0, 3, 3, 0))]


def main():
    if not __debug__:
        raise RuntimeError('Assertions must be enabled')
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--output', type=Path, required=True)
    directory = parser.parse_args().output.resolve()
    assert directory.is_relative_to(ROOT / 'docs') and not directory.exists() and platform.system() == 'Darwin'
    commit = guard.owned.command(['git', 'rev-parse', 'HEAD'])
    sources = {}
    archive_bytes = subprocess.check_output(['git', 'archive', commit, '--', *SOURCES], cwd=ROOT)
    with tarfile.open(fileobj=io.BytesIO(archive_bytes)) as archive:
        for entry in archive:
            if entry.isfile():
                raw = archive.extractfile(entry).read()
                assert raw == (ROOT / entry.name).read_bytes(), 'Commit measured sources first'
                sources[entry.name] = guard.support.sha(raw)
    assert all(name in sources for name in SOURCES if not (ROOT / name).is_dir())
    assert not guard.owned.command(['git', 'ls-files', '--others', '--exclude-standard', '--', 'workers', 'apps/coordinator/src'])
    model_root = Path(os.environ.get('OLLAMA_MODELS', str(Path.home() / '.ollama/models')))
    assert guard.support.sha((model_root / 'manifests/registry.ollama.ai/library/embeddinggemma/latest').read_bytes()) == guard.model.DIGEST
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    assert port != 11434
    plan = {'schema': 'agat.embedding.worker-idle-model.v1', 'implementationCommit': commit, 'sourceSha256': sources,
            'phases': phases(), 'concurrency': 1, 'callsPerBurst': 2, 'model': guard.model.MODEL, 'modelDigest': guard.model.DIGEST,
            'dimensions': 768, 'ollamaSettings': guard.SETTINGS, 'endpoint': f'http://127.0.0.1:{port}/v1',
            'platform': platform.system(), 'machine': platform.machine(), 'python': platform.python_version(),
            'node': guard.owned.command(['node', '--version']), 'memoryBytes': int(guard.owned.command(['/usr/sbin/sysctl', '-n', 'hw.memsize'])),
            'budgetSeconds': 240, 'timeoutSeconds': 30, 'maxComponentDifference': 1e-6, 'maxCosineDistance': 1e-10,
            'warmupInput': 'Проверка готовности локальной embedding модели.'}
    assert plan['memoryBytes'] >= 24 * 1024**3
    directory.mkdir(parents=True); guard.support.write(directory / 'plan.json', plan)
    environment = {k: v for k, v in os.environ.items() if not k.startswith(('AGAT_', 'OTEL_', 'OLLAMA_'))}
    environment.update(guard.SETTINGS, OLLAMA_MODELS=str(model_root), NO_PROXY='127.0.0.1', no_proxy='127.0.0.1',
                       AGAT_PROBE_PYTHON=sys.executable, AGAT_OTEL_ENABLED='false', OTEL_SDK_DISABLED='true')
    result = {'status': 'incomplete', 'failure': None, 'cleanupErrors': [], 'planSha256': guard.support.sha((directory / 'plan.json').read_bytes())}
    started, server, node, owned = time.monotonic(), None, None, set()
    guard.model.BASE = f'http://127.0.0.1:{port}'
    with tempfile.TemporaryDirectory(prefix='agat-worker-idle-model-') as folder:
        folder = Path(folder)
        with (folder / 'ollama.log').open('wb') as server_log, (folder / 'worker.log').open('wb') as node_log:
            try:
                server = subprocess.Popen(['ollama', 'serve'], cwd=ROOT, env={**environment, 'OLLAMA_HOST': f'127.0.0.1:{port}'},
                                          stdout=server_log, stderr=subprocess.STDOUT, start_new_session=True)
                owned.add(server.pid); deadline = time.monotonic() + 30
                while True:
                    assert server.poll() is None
                    try:
                        result['metadataBefore'] = guard.model.metadata(); break
                    except (OSError, ValueError):
                        assert time.monotonic() < deadline; time.sleep(.1)
                assert result['metadataBefore']['loaded'] == []
                result['warmup'] = guard.owned.request(port, '/api/embed', {'model': plan['model'], 'input': [plan['warmupInput']]})
                assert result['warmup']['model'] == plan['model'] and len(result['warmup']['embeddings']) == 1
                node = subprocess.Popen(['node', '--import', 'tsx', 'scripts/benchmark-embedding-worker-idle.ts', str(directory / 'plan.json')],
                                        cwd=ROOT, env=environment, stdout=node_log, stderr=subprocess.STDOUT, start_new_session=True)
                owned.add(node.pid); deadline = time.monotonic() + plan['budgetSeconds']; offset = 0
                while node.poll() is None:
                    assert time.monotonic() < deadline, 'Worker model qualification exceeded its budget'
                    owned.update(guard.owned.inventory(server.pid)[0]); owned.update(guard.owned.inventory(node.pid)[0])
                    with (folder / 'worker.log').open() as stream:
                        stream.seek(offset)
                        for line in stream:
                            if line.startswith('phase '): print(line.strip(), flush=True)
                        offset = stream.tell()
                    time.sleep(.2)
                assert node.returncode == 0, 'Worker model harness failed'
                result['metadataAfter'] = guard.model.metadata()
                assert result['metadataBefore']['version'] == result['metadataAfter']['version']
                assert result['metadataBefore']['model'] == result['metadataAfter']['model']
                guard.owned.request(port, '/api/generate', {'model': plan['model'], 'stream': False, 'keep_alive': 0})
                result['modelsAfterUnload'] = guard.owned.request(port, '/api/ps'); assert result['modelsAfterUnload'] == {'models': []}
            except Exception as error:
                result['failure'] = f'{type(error).__name__}: {error}'
            finally:
                for process in (node, server):
                    try:
                        if process: owned.update(guard.owned.inventory(process.pid)[0])
                        guard.owned.stop(process)
                    except Exception as error: result['cleanupErrors'].append(type(error).__name__)
        result['logSha256'] = {name: guard.support.sha((folder / name).read_bytes()) for name in ('ollama.log', 'worker.log')}
        result['progress'] = [line for line in (folder / 'worker.log').read_text(errors='replace').splitlines() if line.startswith('phase ')]
    result['files'] = {}
    for file in sorted(directory.glob('*.json.gz')):
        raw = gzip.decompress(file.read_bytes())
        result['files'][file.name] = {'sha256': guard.support.sha(file.read_bytes()), 'bytes': file.stat().st_size, 'decodedBytes': len(raw)}
        if file.name.endswith('.worker.json.gz'):
            probe = json.loads(raw); owned.add(probe['pid']); owned.update(c['pid'] for c in probe['children'])
    result.update(elapsedSeconds=time.monotonic() - started, ownedPids=sorted(owned),
                  remainingOwnedPids=sorted(owned & guard.owned.inventory(os.getpid())[1]),
                  ollamaPid=server.pid if server else None, ollamaExitCode=server.returncode if server else None,
                  nodePid=node.pid if node else None, nodeExitCode=node.returncode if node else None)
    if result['remainingOwnedPids'] or result['cleanupErrors']:
        result['failure'] = result['failure'] or 'Owned process cleanup failed'
    if result['failure'] is None: result['status'] = 'observed'
    guard.support.write(directory / 'result.json', result)
    print(result['status'], result['failure'], flush=True)
    return int(result['status'] != 'observed')


if __name__ == '__main__':
    raise SystemExit(main())
