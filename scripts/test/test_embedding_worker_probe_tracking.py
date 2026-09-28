"""Embedding probes must track the shared HTTP owner and exclude primary calls."""
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "workers"))
import agat_worker


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rag = load("rag_probe_tracking", "scripts/embedding-rag-worker-probe.py")
knowledge = load("knowledge_probe_tracking", "apps/coordinator/test/embedding-worker-probe.py")


class WorkerProbeTrackingTests(unittest.TestCase):
    def exercise(self, kind):
        received = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                received.append(self.path)
                data = ({"data": [{"index": 0, "embedding": [1, 0]}]} if self.path.endswith("/embeddings")
                        else {"hits": []} if self.path.endswith("/knowledge/search")
                        else {"choices": [{"message": {"content": "synthetic primary"}}]})
                body = json.dumps(data).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}"

        def loop(_config):
            client = agat_worker.LocalModelClient(f"{url}/v1", "")
            self.assertEqual(client.embed("local", ["synthetic"]), [[1, 0]])
            lease = {"agent": {"systemPrompt": "Answer.", "model": "local"}, "run": {"name": "test", "input": "synthetic"}}
            self.assertEqual(client.complete(lease, "local"), "synthetic primary")
            coordinator = agat_worker.CoordinatorClient(url, "test-node-token")
            self.assertEqual(coordinator.knowledge_search("lease", []), {"hits": []})
            return 0

        try:
            with tempfile.TemporaryDirectory(dir=ROOT / "docs", prefix="embedding-probe-tracking-") as directory, \
                    patch.object(agat_worker, "worker_loop", side_effect=loop), redirect_stdout(io.StringIO()) as output, \
                    patch.dict(os.environ, {"NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}):
                if kind == "rag":
                    target = Path(directory) / "result.json"
                    argv = ["probe", str(ROOT / "workers/agat_worker.py"), "--coordinator", url, "--model-url", f"{url}/v1",
                            "--models", "local", "--embedding-models", "local", "--model-discovery", "off", "--no-web",
                            "--concurrency", "2", "--embedding-transport", "isolated", "--credentials", str(Path(directory) / "credentials.json")]
                    with patch.object(sys, "argv", argv), patch.dict(os.environ, {"AGAT_EMBEDDING_PROBE_OUTPUT": str(target)}):
                        self.assertEqual(rag.main(), 0)
                    report = json.loads(target.read_text())
                    self.assertEqual(len(report["calls"]), 1)
                    self.assertEqual(len(report["requests"]), 1)
                    self.assertEqual(len(report["children"]), 1)
                    child = report["children"][0]
                    self.assertEqual(child["callerStartedNs"], report["calls"][0]["startedNs"])
                    self.assertEqual(report["requests"][0]["pid"], child["pid"])
                else:
                    supplied = {"coordinator": url, "artifacts": directory, "nodeId": "test-node", "token": "test-token",
                                "modelUrl": f"{url}/v1", "model": "local", "region": "local", "residencyDomain": "local",
                                "dryRun": False, "embeddingTransport": "isolated"}
                    with patch.object(sys, "stdin", io.StringIO(json.dumps(supplied))), patch.object(sys, "argv", ["probe"]):
                        self.assertEqual(knowledge.main(), 0)
                    report = json.loads(next(line.removeprefix("AGAT_EMBEDDING_WORKER_PROBE ")
                                             for line in output.getvalue().splitlines() if line.startswith("AGAT_EMBEDDING_WORKER_PROBE ")))
                    self.assertEqual(len(report["helpers"]), 1)
                    self.assertEqual(len(report["transports"]), 1)
                    child = report["transports"][0]
                    self.assertEqual(report["helpers"][0]["pid"], child["pid"])
                self.assertEqual(child["returncode"], 0)
                self.assertTrue(child["stdinClosed"] and child["stdoutClosed"])
                self.assertEqual(received, ["/v1/embeddings", "/v1/chat/completions", "/api/v1/leases/lease/knowledge/search"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)

    def test_rag_probe_observes_embedding_owner_and_excludes_primary_helper(self):
        self.exercise("rag")

    def test_knowledge_worker_probe_observes_embedding_owner_and_excludes_primary_helper(self):
        self.exercise("knowledge")


if __name__ == "__main__":
    unittest.main()
