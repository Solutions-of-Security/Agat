#!/usr/bin/env python3
"""Own bounded Ollama/MLX foreground services for the synthetic RAG workload."""

import argparse
import hashlib
import http.client
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_runtime.artifacts import read_json, sealed, write_new
from decision_runtime.contracts import Policy, parse_json
from decision_runtime.engine import DecisionEngine
from decision_runtime.isolated import IsolatedBackend, mlx_factory
from decision_runtime.server import make_server
from scripts.lib.decision_performance import hardware

MODELS = {'qwen3:8b': ('qwen3/8b', '500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41'),
          'embeddinggemma:latest': ('embeddinggemma/latest', '85462619ee721b466c5927d109d4cb765861907d5417b9109caebc4e614679f1')}


def require(value, message):
    if not value: raise ValueError(message)


def request(port, path, body=None):
    import json
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
    try:
        conn.request('POST' if body is not None else 'GET', path, body=json.dumps(body) if body is not None else None,
                     headers={'Content-Type': 'application/json'})
        response = conn.getresponse();raw = response.read(524289)
        require(response.status == 200 and len(raw) <= 524288, 'Local Ollama control endpoint failed')
        return parse_json(raw)
    finally: conn.close()


def descendants(parent):
    probe = subprocess.Popen(['/bin/ps', '-axo', 'pid=,ppid='], text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try: raw, _ = probe.communicate(timeout=3)
    except subprocess.TimeoutExpired:
        probe.kill();probe.wait();raise
    require(probe.returncode == 0, 'Process inventory failed')
    parents = {int(line.split()[0]): int(line.split()[1]) for line in raw.splitlines() if len(line.split()) == 2}
    found = {parent}
    while True:
        extra = {pid for pid, ppid in parents.items() if ppid in found and pid != probe.pid} - found
        if not extra: return found
        found.update(extra)


def stop_group(process):
    if process is None or getattr(process, '_agat_group_stopped', False): return
    # Every managed foreground command starts its own session. These signals
    # can reach only that explicitly owned group, including surviving workers.
    try: os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError: pass
    try: process.wait(timeout=5)
    except subprocess.TimeoutExpired: pass
    try: os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError: pass
    process.wait(timeout=3)
    process._agat_group_stopped = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('manifest', 'policy', 'fixture', 'evidence-dir'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--design', choices=['sequence', 'paired-rag'], default='sequence')
    args = parser.parse_args()
    require(args.evidence_dir.resolve().is_relative_to((ROOT / 'docs').resolve()), 'Evidence belongs under docs')
    paths = {name: args.evidence_dir / f'rag-workflow-{name}.json' for name in ('plan', 'result', 'launcher-plan', 'launcher-result')}
    require(not any(p.exists() for p in paths.values()), 'Use new experiment paths')
    fixture = read_json(args.fixture)
    require(fixture.get('rag', {}).get('embeddingModel') == 'embeddinggemma:latest', 'Expected the bounded RAG fixture')
    model_root = Path(os.environ.get('OLLAMA_MODELS', str(Path.home() / '.ollama/models')))
    for name, (relative, digest) in MODELS.items():
        path = model_root / 'manifests/registry.ollama.ai/library' / relative
        require(hashlib.sha256(path.read_bytes()).hexdigest() == digest, 'Installed model manifest changed')
    host = hardware();require(host.get('memoryBytes', 0) >= 24 * 1024**3, 'This diagnostic expects at least 24 GiB unified memory')
    config = {'OLLAMA_NO_CLOUD': '1', 'OLLAMA_MAX_LOADED_MODELS': '2', 'OLLAMA_NUM_PARALLEL': '1',
              'OLLAMA_CONTEXT_LENGTH': '8192', 'OLLAMA_KEEP_ALIVE': '5m'}
    launcher_plan = sealed({'schemaVersion': 'agat.decision.rag-launcher-plan.v1', 'createdAt': datetime.now(timezone.utc).isoformat(),
                            'models': {name: digest for name, (_relative, digest) in MODELS.items()}, 'ollamaSettings': config,
                            'host': host, 'design': args.design, 'nodeBudgetMs': 660000, 'startupBudgetMs': 30000,
                            'runtime': {'maxTokens': 2048, 'cacheLimitMiB': 128, 'inferenceTimeoutMs': 5000},
                            'files': {str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                                      for p in (Path(__file__).resolve(), ROOT / 'scripts/benchmark-decision-workflow.ts', args.fixture.resolve(), args.manifest.resolve(), args.policy.resolve())},
                            'routingEnabled': False, 'limitations': ['One owned local server with two cached models; no production SLO or business connector acceptance.']})
    write_new(paths['launcher-plan'], launcher_plan)
    started = time.monotonic();stage = 'startup';failure = None;ollama = node = None;backend_diag = None
    samples = [];owned_pids = {os.getpid()};runtime_status = None;unloaded = None;version = None
    inventory_errors = [];cleanup_errors = []
    def inventory():
        try: owned_pids.update(descendants(os.getpid()))
        except Exception as exc:
            if len(inventory_errors) < 20: inventory_errors.append(type(exc).__name__)
    def cleanup_process(process):
        try: stop_group(process)
        except Exception as exc: cleanup_errors.append(type(exc).__name__)
    with tempfile.TemporaryDirectory(prefix='agat-rag-models-') as temporary:
        with open(Path(temporary) / 'ollama.log', 'wb') as ollama_log, open(Path(temporary) / 'workflow.log', 'wb') as node_log:
            with socket.socket() as port_socket:
                port_socket.bind(('127.0.0.1', 0));ollama_port = port_socket.getsockname()[1]
            try:
                ollama = subprocess.Popen(['ollama', 'serve'], cwd=ROOT, stdout=ollama_log, stderr=subprocess.STDOUT,
                                           start_new_session=True, env={**os.environ, **config, 'OLLAMA_HOST': f'127.0.0.1:{ollama_port}'})
                owned_pids.add(ollama.pid);deadline = time.monotonic() + 30
                while True:
                    require(ollama.poll() is None, 'Owned Ollama exited during startup')
                    try: version = request(ollama_port, '/api/version');break
                    except (OSError, ValueError, http.client.HTTPException):
                        require(time.monotonic() < deadline, 'Ollama startup timeout');time.sleep(.1)
                with IsolatedBackend(mlx_factory, {'manifest': str(args.manifest.resolve()), 'max_tokens': 2048, 'cache_limit_mib': 128},
                                     timeout_ms=5000) as backend:
                    engine = DecisionEngine(backend, Policy.from_dict(read_json(args.policy)))
                    with make_server(engine, 0) as server:
                        thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
                        try:
                            stage = 'workflow'
                            command = ['node', '--import', 'tsx', 'scripts/benchmark-decision-workflow.ts',
                                       '--design', args.design,
                                       '--decision-url', f'http://127.0.0.1:{server.server_port}', '--primary-url', f'http://127.0.0.1:{ollama_port}',
                                       '--primary-model', 'qwen3:8b', '--expected-primary-digest', MODELS['qwen3:8b'][1],
                                       '--expected-embedding-digest', MODELS['embeddinggemma:latest'][1], '--fixture', str(args.fixture.resolve()),
                                       '--plan-output', str(paths['plan'].resolve()), '--output', str(paths['result'].resolve())]
                            node = subprocess.Popen(command, cwd=ROOT, stdout=node_log, stderr=subprocess.STDOUT, start_new_session=True)
                            owned_pids.add(node.pid);deadline = time.monotonic() + 660;next_sample = 0;log_offset = 0
                            print('Owned Ollama and MLX ready; bounded RAG workload started', flush=True)
                            while node.poll() is None:
                                require(time.monotonic() < deadline, 'Workflow time budget exceeded')
                                inventory()
                                with open(Path(temporary) / 'workflow.log', encoding='utf-8', errors='replace') as progress_log:
                                    progress_log.seek(log_offset)
                                    for line in progress_log:
                                        if line.startswith(('control_c', 'shadow_c')): print(line.strip(), flush=True)
                                    log_offset = progress_log.tell()
                                if time.monotonic() >= next_sample:
                                    sample = {'elapsedMs': round((time.monotonic() - started) * 1000, 3)}
                                    try: sample['loadedModels'] = request(ollama_port, '/api/ps')
                                    except Exception as exc: sample['error'] = type(exc).__name__
                                    samples.append(sample)
                                    next_sample = time.monotonic() + 10
                                time.sleep(.5)
                            require(node.returncode == 0, 'Workflow CLI failed')
                            node_result = read_json(paths['result'])
                            require(node_result['status'] == 'observed' and node_result['planSha256'] == hashlib.sha256(paths['plan'].read_bytes()).hexdigest(),
                                    'Unbound/incomplete workflow result')
                            runtime_status = backend.is_available();require(runtime_status, 'MLX backend became unavailable')
                        finally:
                            inventory()
                            cleanup_process(node);server.shutdown();thread.join(timeout=2);backend.close()
                            backend_diag = backend.diagnostics()
                stage = 'unload'
                for name in MODELS: request(ollama_port, '/api/generate', {'model': name, 'stream': False, 'keep_alive': 0})
                unloaded = request(ollama_port, '/api/ps');require(unloaded.get('models') == [], 'Owned models remain loaded')
            except Exception as exc:
                failure = {'stage': stage, 'type': type(exc).__name__}
            finally:
                inventory();cleanup_process(node);cleanup_process(ollama)
        log_hashes = {name: hashlib.sha256((Path(temporary) / name).read_bytes()).hexdigest() for name in ('ollama.log', 'workflow.log')}
        # Preserve only the harness's short fixed progress/error lines, never the
        # model server startup environment or arbitrary stderr output.
        progress = [line for line in (Path(temporary) / 'workflow.log').read_text(errors='replace').splitlines()
                    if line.startswith(('control_c', 'shadow_c', 'observed:', 'incomplete:', 'experiment_failed', 'experiment_assertion_failed'))]
    if cleanup_errors and failure is None: failure = {'stage': 'cleanup', 'type': 'CleanupError'}
    report = sealed({'schemaVersion': 'agat.decision.rag-launcher-result.v1', 'createdAt': datetime.now(timezone.utc).isoformat(),
                     'status': 'observed' if failure is None else 'incomplete', 'failure': failure,
                     'launcherPlanSha256': launcher_plan['sha256'], 'elapsedMs': round((time.monotonic() - started) * 1000, 3),
                     'ollamaPid': ollama.pid if ollama else None, 'ollamaExitCode': ollama.returncode if ollama else None,
                     'nodePid': node.pid if node else None, 'nodeExitCode': node.returncode if node else None,
                     'managerPid': os.getpid(), 'observedOwnedPids': sorted(owned_pids), 'backendDiagnostics': backend_diag,
                     'runtimeAvailableBeforeCleanup': runtime_status, 'ollamaVersion': version, 'modelsAfterUnload': unloaded,
                     'processInventoryErrors': inventory_errors, 'cleanupErrors': cleanup_errors,
                     'loadedModelSamples': samples, 'logSha256': log_hashes,
                     'progress': progress, 'routingEnabled': False, 'qualifiedForRouting': False,
                     'limitations': ['Verify owned PID absence after this launcher exits; a resource-tracker can remain until parent exit.',
                                     'Loaded-model snapshots are not a continuous RSS/VRAM peak or evidence of parallel GPU kernels.']})
    write_new(paths['launcher-result'], report);print(report['status'], failure, flush=True)
    return 0 if failure is None else 1


if __name__ == '__main__': raise SystemExit(main())
