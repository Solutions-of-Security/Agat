"""Embedding probes must track the shared HTTP owner and exclude primary calls."""
from contextlib import ExitStack, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "workers"))
import agat_worker
from embedding_transport import EmbeddingSessionPool


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rag = load("rag_probe_tracking", "scripts/embedding-rag-worker-probe.py")
knowledge = load("knowledge_probe_tracking", "apps/coordinator/test/embedding-worker-probe.py")


verifier = load("owned_http_verifier", "scripts/verify-embedding-rag.py")


class WorkerProbeTrackingTests(unittest.TestCase):
    def exercise(self, kind, *, mode="isolated", all_http=False, fail_model=False):
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
                # Keep each owner alive across at least one RSS observation.
                if all_http:
                    time.sleep(.15)
                self.send_response(500 if fail_model and self.path.endswith("/chat/completions") else 200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}"

        def loop(_config):
            with ExitStack() as stack:
                pool = stack.enter_context(EmbeddingSessionPool(2)) if mode == "session" else None
                client = agat_worker.LocalModelClient(f"{url}/v1", "", embedding_request=pool.request if pool else None)
                self.assertEqual(client.embed("local", ["synthetic"]), [[1, 0]])
                # A second exchange must reuse the same session owner.
                if all_http:
                    self.assertEqual(client.embed("local", ["synthetic again"]), [[1, 0]])
                lease = {"agent": {"systemPrompt": "Answer.", "model": "local"}, "run": {"name": "test", "input": "synthetic"}}
                if fail_model:
                    with self.assertRaisesRegex(RuntimeError, "HTTP 500"):
                        client.complete(lease, "local")
                else:
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
                            "--concurrency", "2", "--embedding-transport", mode, "--credentials", str(Path(directory) / "credentials.json")]
                    with patch.object(sys, "argv", argv), patch.dict(os.environ, {"AGAT_EMBEDDING_PROBE_OUTPUT": str(target), "AGAT_HTTP_HELPER_PROBE": "1" if all_http else "0"}):
                        self.assertEqual(rag.main(), 0)
                    report = json.loads(target.read_text())
                    expected = 2 if all_http else 1
                    self.assertEqual(len(report["calls"]), expected)
                    self.assertEqual(len(report["requests"]), expected)
                    self.assertEqual(len(report["children"]), 1 if mode == "session" else expected)
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
                self.assertEqual(received, ["/v1/embeddings"] * (2 if all_http else 1) +
                                 ["/v1/chat/completions", "/api/v1/leases/lease/knowledge/search"])
                if all_http:
                    http = report["ownedHttp"]
                    self.assertEqual([call["kind"] for call in http["calls"]], ["Embedding", "Embedding", "Model", "Knowledge"])
                    self.assertEqual(http["errors"], [])
                    self.assertEqual(http["activeCalls"], 0)
                    for call in http["calls"]:
                        self.assertEqual(call["completed"], not (fail_model and call["kind"] == "Model"))
                    self.assertEqual(len(http["children"]), 3 if mode == "session" else 4)
                    for item in http["children"]:
                        self.assertEqual(item["returncode"], 0)
                        self.assertTrue(item["stdinClosed"] and item["stdoutClosed"])
                    owned = {item["pid"] for item in http["children"]}
                    counts = {"Embedding": 2, "Model": 1, "Knowledge": 1}
                    if fail_model:
                        with self.assertRaises(AssertionError):
                            verifier.verify_owned_http(report, counts, owned)
                    else:
                        checked, pids = verifier.verify_owned_http(report, counts, owned)
                        self.assertEqual(pids, owned)
                        model = checked["kinds"]["Model"]
                        self.assertEqual(model["sampledHelperCount"] + model["unsampledHelperCount"], 1)
                        self.check_mutations(report, counts, owned)
                elif kind == "rag":
                    self.assertNotIn("ownedHttp", report)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)

    def check_mutations(self, report, counts, owned):
        mutations = {
            "missing owner": lambda h: h["calls"].pop(),
            "duplicate owner": lambda h: h["calls"].append(dict(h["calls"][0])),
            "misclassified owner": lambda h: h["calls"][2].update(kind="Embedding"),
            "wrong child binding": lambda h: h["children"][-1].update(createdByCallId=1),
            "unclosed pipe": lambda h: h["children"][-1].update(stdoutClosed=False),
            "unreaped helper": lambda h: h["children"][-1].update(returncode=None),
            "unknown RSS pid": lambda h: h["samples"][0]["helperRssBytes"].update({"1": 4096}),
            "invalid RSS": lambda h: h["samples"][0].update(workerRssBytes=float("nan")),
            "missing sample": lambda h: h["samples"].pop(),
            "unfinished owner": lambda h: h.update(activeCalls=1),
            "probe diagnostic": lambda h: h["errors"].append("unbound_http_helper"),
        }
        for label, mutate in mutations.items():
            with self.subTest(mutation=label):
                changed = copy.deepcopy(report)
                mutate(changed["ownedHttp"])
                with self.assertRaises((AssertionError, KeyError, ValueError)):
                    verifier.verify_owned_http(changed, counts, owned)
        with self.assertRaises(AssertionError):
            verifier.verify_owned_http(report, counts, owned - {report["ownedHttp"]["children"][-1]["pid"]})

    def test_all_http_isolated_owners_are_bound_and_reaped(self):
        self.exercise("rag", all_http=True)

    def test_all_http_session_owners_reuse_only_embedding_helper(self):
        self.exercise("rag", mode="session", all_http=True)

    def test_failed_http_is_not_counted_as_completed_and_still_closes(self):
        self.exercise("rag", all_http=True, fail_model=True)

    def test_rag_probe_observes_embedding_owner_and_excludes_primary_helper(self):
        self.exercise("rag")

    def test_knowledge_worker_probe_observes_embedding_owner_and_excludes_primary_helper(self):
        self.exercise("knowledge")


if __name__ == "__main__":
    unittest.main()
