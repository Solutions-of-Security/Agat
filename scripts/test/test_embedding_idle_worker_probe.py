"""Real worker/store/HTTP smoke for the model harness without model weights."""
import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[2]


class WorkerIdleProbeTests(unittest.TestCase):
    def test_real_worker_lease_vectors_and_idle_snapshots(self):
        inputs = []
        class Endpoint(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                inputs.append(body['input'])
                raw = json.dumps({'data': [{'index': i, 'embedding': [i + 1, 2]} for i in range(len(body['input']))]}).encode()
                self.send_response(200); self.send_header('Content-Length', str(len(raw)))
                self.end_headers(); self.wfile.write(raw)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Endpoint)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            for batch in (1, 32):
                for idle in (0, .8):
                    with self.subTest(batch=batch, idle=idle), tempfile.TemporaryDirectory(prefix='idle-worker-smoke-', dir=ROOT / 'docs') as folder:
                        env = {k: v for k, v in os.environ.items() if not k.startswith(('AGAT_', 'OTEL_'))}
                        env.update(AGAT_PROBE_PYTHON=sys.executable, AGAT_OTEL_ENABLED='false')
                        code = '''import { runPhase } from './scripts/benchmark-embedding-worker-idle.ts';
await runPhase({ id: 'smoke', batch: Number(process.argv[3]), idleTimeout: Number(process.argv[4]), idleSeconds: 1.2 }, process.argv[2], 'fixture', process.argv[1]);'''
                        done = subprocess.run(['node', '--import', 'tsx', '--input-type=module', '-e', code, folder,
                                               f'http://127.0.0.1:{server.server_port}/v1', str(batch), str(idle)],
                                              cwd=ROOT, env=env, text=True, capture_output=True, timeout=30)
                        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                        report = json.loads(gzip.decompress((Path(folder) / 'smoke.worker.json.gz').read_bytes()))
                        saved = json.loads(gzip.decompress((Path(folder) / 'smoke.store.json.gz').read_bytes()))
                        self.assertEqual(len(report['calls']), 4)
                        self.assertEqual(len(report['children']), 2 if idle else 1)
                        self.assertEqual(report['activeCalls'], 0)
                        self.assertEqual(report['liveThreads'], [])
                        self.assertEqual(report['controlErrors'], [])
                        self.assertEqual(report['fdBefore'], report['fdAfter'])
                        self.assertEqual(len(report['snapshots']['afterIdle']['helperRssBytes']), 0 if idle else 1)
                        self.assertTrue(all(c['returncode'] == 0 and c['stdinClosed'] and c['stdoutClosed'] for c in report['children']))
                        for row, document in zip(report['calls'], [d for r in saved['rounds'] for d in r], strict=True):
                            self.assertEqual(row['vectors'], [[i + 1, 2] for i in range(batch)])
                            self.assertEqual(row['inputs'], [c['content'] for c in document['chunks']])
                            self.assertEqual(row['vectors'], [json.loads(c['embedding_json']) for c in document['chunks']])
                        self.assertEqual([c['status'] for c in report['completions']], [200] * 4)
            self.assertEqual(len(inputs), 16)
        finally:
            server.shutdown(); server.server_close(); thread.join(2)


if __name__ == '__main__':
    unittest.main()
