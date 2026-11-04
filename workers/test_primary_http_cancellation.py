"""Real primary HTTP cancellation, ownership, bounds and runtime propagation."""
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler
from pathlib import Path
import base64
import json
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import embedding_http
from agat_worker import LocalModelClient, MAX_MODEL_RESPONSE_BYTES, supported_agent_runtimes
from test_embedding_http_deadline import endpoint, observed_processes, slow_endpoint


def lease(runtime="single", profile="tool_loop_v1", text="synthetic input"):
    agent = {"name": "primary", "model": "local", "systemPrompt": "Answer the task.", "runtime": runtime,
             "runtimeConfig": {"profile": profile, "maxIterations": 2}}
    if profile == "specialist_team_v1":
        agent["runtimeConfig"].update(maxHandoffs=2, stateSchema="specialist_team_state_v1",
                                      specialistAgentIds=["first", "second"])
        agent["specialists"] = [{"schemaVersion": 1, "id": name, "name": name, "role": "Check facts",
            "systemPrompt": "Check facts", "model": "local", "runtimeConfig": {"profile": "tool_loop_v1", "maxIterations": 2},
            "promptVersion": "a" * 64, "definitionVersion": "b" * 64} for name in ("first", "second")]
    return {"leaseId": "lease", "agent": agent, "run": {"name": "synthetic", "input": text}}


class QuietHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def reply(self, body, status=200):
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


class PrimaryCancellationTests(unittest.TestCase):
    def cancel_pending(self, mode, runtime="single", profile="tool_loop_v1"):
        with slow_endpoint(mode) as (url, entered, release), observed_processes() as children:
            cancelled = threading.Event()
            client = LocalModelClient(url, "private-test-key")
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(client.complete, lease(runtime, profile), "local", cancelled=cancelled)
                try:
                    self.assertTrue(entered.wait(3))
                    cancelled.set()
                    with self.assertRaisesRegex(RuntimeError, "^Model request cancelled$"):
                        pending.result(timeout=3)
                    self.assertFalse(release.is_set())
                    self.assertEqual(len(children), 1)
                    self.assertIsNotNone(children[0].poll())
                    self.assertNotIn("private-test-key", " ".join(children[0].args))
                finally:
                    release.set()

    def test_cancel_during_headers_success_body_and_error_body(self):
        for mode in ("headers", "body", "error"):
            with self.subTest(mode=mode):
                self.cancel_pending(mode)

    @unittest.skipUnless("langgraph" in supported_agent_runtimes(), "Install workers/requirements.txt for real LangGraph")
    def test_cancel_propagates_through_real_langgraph_and_specialist_supervisor(self):
        for profile in ("tool_loop_v1", "specialist_team_v1"):
            with self.subTest(profile=profile):
                self.cancel_pending("headers", "langgraph", profile)

    @unittest.skipUnless("langgraph" in supported_agent_runtimes(), "Install workers/requirements.txt for real LangGraph")
    def test_cancellation_reaches_nested_specialist_graph(self):
        entered, release = threading.Event(), threading.Event()
        received = []

        class Handler(QuietHandler):
            def do_POST(self):
                received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                if len(received) == 1:
                    content = json.dumps({"action": "delegate", "specialistId": "first", "task": "Check facts"})
                    self.reply(json.dumps({"choices": [{"message": {"content": content}}]}).encode())
                else:
                    entered.set()
                    release.wait(4)
                    self.reply(b'{"choices":[{"message":{"content":"late specialist"}}]}')

        with endpoint(Handler) as port, observed_processes() as children, ThreadPoolExecutor(max_workers=1) as pool:
            cancelled = threading.Event()
            client = LocalModelClient(f"http://127.0.0.1:{port}/v1", "")
            future = pool.submit(client.complete, lease("langgraph", "specialist_team_v1"), "local", cancelled=cancelled)
            try:
                self.assertTrue(entered.wait(3))
                cancelled.set()
                with self.assertRaisesRegex(RuntimeError, "Model request cancelled"):
                    future.result(timeout=2)
                self.assertFalse(release.is_set())
                self.assertEqual(len(received), 2)
                self.assertEqual(len(children), 2)
                self.assertTrue(all(child.poll() is not None for child in children))
            finally:
                release.set()

    def test_wall_clock_deadline_covers_headers_and_dripping_success_or_error_body(self):
        for mode in ("headers", "body", "error"):
            with self.subTest(mode=mode), slow_endpoint(mode) as (url, entered, release), observed_processes(), \
                    patch("agat_worker.MODEL_HTTP_TIMEOUT", 0.75), ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(LocalModelClient(url, "").complete, lease(), "local")
                try:
                    self.assertTrue(entered.wait(2))
                    with self.assertRaisesRegex(RuntimeError, "Model endpoint exceeded its 0.75s deadline"):
                        pending.result(timeout=2)
                    self.assertFalse(release.is_set())
                finally:
                    release.set()

    def test_precancelled_call_spawns_nothing_and_restores_context(self):
        cancelled = threading.Event()
        cancelled.set()
        client = LocalModelClient("http://unused.invalid/v1", "")
        with observed_processes() as children:
            with self.assertRaisesRegex(RuntimeError, "Model request cancelled"):
                client.complete(lease(), "local", cancelled=cancelled)
            self.assertEqual(children, [])
        with patch("agat_worker.request_model_response", return_value=b'{"choices":[{"message":{"content":"healthy"}}]}') as send:
            self.assertEqual(client.complete(lease(), "local"), "healthy")
            self.assertIsNone(send.call_args.kwargs["cancelled"])

    def test_cancelled_lease_does_not_cancel_parallel_lease_on_shared_client(self):
        entered = [threading.Event(), threading.Event()]
        release = threading.Event()

        class Handler(QuietHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                first = "first request" in body["messages"][-1]["content"]
                entered[0 if first else 1].set()
                release.wait(4)
                self.reply(b'{"choices":[{"message":{"content":"healthy"}}]}')

        with endpoint(Handler) as port, observed_processes() as children, ThreadPoolExecutor(max_workers=2) as pool:
            client = LocalModelClient(f"http://127.0.0.1:{port}/v1", "")
            cancelled = threading.Event()
            first = pool.submit(client.complete, lease(text="first request"), "local", cancelled=cancelled)
            second = pool.submit(client.complete, lease(text="second request"), "local", cancelled=threading.Event())
            try:
                self.assertTrue(all(event.wait(3) for event in entered))
                cancelled.set()
                with self.assertRaisesRegex(RuntimeError, "Model request cancelled"):
                    first.result(timeout=2)
                self.assertFalse(second.done())
                self.assertEqual(sum(child.poll() is None for child in children), 1)
                release.set()
                self.assertEqual(second.result(timeout=3), "healthy")
            finally:
                release.set()

    def test_stalled_helper_is_killed_and_reaped_without_credentials_on_argv(self):
        with tempfile.TemporaryDirectory() as directory:
            helper = Path(directory) / "blocked-helper.py"
            helper.write_text("import signal,time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(30)\n")
            with patch.object(embedding_http, "__file__", str(helper)), observed_processes() as children, \
                    patch("agat_worker.MODEL_HTTP_TIMEOUT", 0.25):
                started = time.monotonic()
                with self.assertRaisesRegex(RuntimeError, "deadline"):
                    LocalModelClient("http://unused.invalid/v1", "private-test-key").complete(lease(text="private-test-input"), "local")
                self.assertLess(time.monotonic() - started, 2)
            self.assertEqual(len(children), 1)
            self.assertNotEqual(children[0].returncode, 0)
            self.assertNotIn("private-test", " ".join(children[0].args))

    def test_http_status_preserves_tools_diagnostic_and_does_not_retry(self):
        received = []

        class Handler(QuietHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                received.append(self.path)
                self.reply(b"unsupported tools " + b"x" * 8000, status)

        with endpoint(Handler) as port, observed_processes():
            client = LocalModelClient(f"http://127.0.0.1:{port}/v1", "")
            for status in (400, 404, 422, 503):
                with self.subTest(status=status):
                    expected = "rejected OpenAI-compatible tools" if status != 503 else "returned HTTP 503"
                    with self.assertRaisesRegex(RuntimeError, expected) as caught:
                        client._chat("local", [{"role": "user", "content": "test"}], with_tools=True)
                    self.assertIn(f"HTTP {status}", str(caught.exception))
                    self.assertLess(len(str(caught.exception)), 1200)
            self.assertEqual(len(received), 4)

    def test_primary_response_has_a_finite_byte_limit(self):
        class Handler(QuietHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                self.reply(b" " * (MAX_MODEL_RESPONSE_BYTES + 1))

        with endpoint(Handler) as port, observed_processes():
            with self.assertRaisesRegex(RuntimeError, f"Model endpoint response exceeds {MAX_MODEL_RESPONSE_BYTES} bytes"):
                LocalModelClient(f"http://127.0.0.1:{port}/v1", "").complete(lease(), "local")

    def test_model_payload_credentials_and_proxy_auth_are_preserved(self):
        received = []

        class Proxy(QuietHandler):
            def do_POST(self):
                received.append((self.path, dict(self.headers), json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
                self.reply(b'{"choices":[{"message":{"content":"healthy"}}]}')

        with endpoint(Proxy) as port, patch.dict(os.environ, {
                "http_proxy": f"http://proxy-user:proxy-password@127.0.0.1:{port}", "HTTP_PROXY": "", "NO_PROXY": "", "no_proxy": ""}), observed_processes():
            result = LocalModelClient("http://model.agat.invalid/v1", "private-key").complete(lease(text="Пример"), "local")
        self.assertEqual(result, "healthy")
        url, headers, body = received[0]
        self.assertEqual(url, "http://model.agat.invalid/v1/chat/completions")
        self.assertEqual(headers["Authorization"], "Bearer private-key")
        self.assertEqual(headers["Proxy-Authorization"], "Basic " + base64.b64encode(b"proxy-user:proxy-password").decode())
        self.assertIn("Пример", body["messages"][-1]["content"])
        self.assertEqual(body["model"], "local")
        self.assertFalse(body["stream"])


if __name__ == "__main__":
    unittest.main()
