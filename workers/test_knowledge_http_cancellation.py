"""Bounded coordinator retrieval HTTP preserves auth/errors and observes lease loss."""
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler
import json
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agat_worker import ApiError, CoordinatorClient
from test_embedding_http_deadline import endpoint, observed_processes, slow_endpoint

QUERIES = [{"embeddingModel": "local", "collectionIds": ["collection"], "vector": [1, 0], "topK": 2}]


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


class KnowledgeHttpCancellationTests(unittest.TestCase):
    def test_cancellation_closes_headers_success_and_error_wait_before_return(self):
        for mode in ("headers", "body", "error"):
            with self.subTest(mode=mode), slow_endpoint(mode) as (url, entered, release), observed_processes() as children, \
                    ThreadPoolExecutor(max_workers=1) as pool:
                cancelled = threading.Event()
                client = CoordinatorClient(url, "test-token")
                pending = pool.submit(client.knowledge_search, "lease", QUERIES, cancelled=cancelled)
                try:
                    self.assertTrue(entered.wait(3))
                    cancelled.set()
                    with self.assertRaisesRegex(RuntimeError, "^Knowledge request cancelled$"):
                        pending.result(timeout=2)
                    self.assertFalse(release.is_set())
                    self.assertEqual(len(children), 1)
                    self.assertIsNotNone(children[0].poll())
                    self.assertNotIn("test-token", " ".join(children[0].args))
                finally:
                    release.set()

    def test_total_deadline_bounds_success_and_error_drips(self):
        for mode in ("headers", "body", "error"):
            with self.subTest(mode=mode), slow_endpoint(mode) as (url, entered, release), observed_processes(), \
                    patch("agat_worker.KNOWLEDGE_HTTP_TIMEOUT", .75), ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(CoordinatorClient(url, "test-token").knowledge_search, "lease", QUERIES)
                try:
                    self.assertTrue(entered.wait(3))
                    with self.assertRaisesRegex(ApiError, "Knowledge endpoint exceeded its 0.75s deadline") as caught:
                        pending.result(timeout=2)
                    self.assertEqual(caught.exception.status, 0)
                finally:
                    release.set()

    def test_missing_auth_and_precancel_never_spawn(self):
        cancelled = threading.Event()
        cancelled.set()
        with observed_processes() as children:
            with self.assertRaises(ApiError) as caught:
                CoordinatorClient("http://unused.invalid").knowledge_search("lease", QUERIES)
            self.assertEqual(caught.exception.status, 401)
            with self.assertRaisesRegex(RuntimeError, "Knowledge request cancelled"):
                CoordinatorClient("http://unused.invalid", "test-token").knowledge_search("lease", QUERIES, cancelled=cancelled)
            self.assertEqual(children, [])

    def test_success_preserves_auth_trace_and_query_body(self):
        received = []

        class Handler(QuietHandler):
            def do_POST(self):
                received.append((self.path, dict(self.headers), json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
                self.reply(b'{"hits":[]}')

        telemetry = SimpleNamespace(inject=lambda headers: headers.update(traceparent="test-trace"))
        with endpoint(Handler) as port, observed_processes():
            client = CoordinatorClient(f"http://127.0.0.1:{port}", "test-token", telemetry=telemetry)
            self.assertEqual(client.knowledge_search("lease", QUERIES), {"hits": []})
        path, headers, body = received[0]
        self.assertEqual(path, "/api/v1/leases/lease/knowledge/search")
        self.assertEqual(headers["Authorization"], "Bearer test-token")
        self.assertEqual(headers["Traceparent"], "test-trace")
        self.assertTrue(headers["User-Agent"].startswith("agat-worker/"))
        self.assertEqual(body, {"queries": QUERIES})

    def test_http_errors_keep_status_and_structured_message_without_retry(self):
        received = []
        message = "Ошибка" * 200

        class Handler(QuietHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                received.append(self.path)
                self.reply(json.dumps({"error": message}, ensure_ascii=False).encode(), status)

        with endpoint(Handler) as port, observed_processes():
            client = CoordinatorClient(f"http://127.0.0.1:{port}", "test-token")
            for status in (401, 403, 404, 409, 429, 503):
                with self.subTest(status=status), self.assertRaises(ApiError) as caught:
                    client.knowledge_search("lease", QUERIES)
                self.assertEqual(caught.exception.status, status)
                self.assertEqual(str(caught.exception), message)
        self.assertEqual(len(received), 6)

    def test_response_byte_limit_and_malformed_error_do_not_lose_status(self):
        class Handler(QuietHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                self.reply(body, status)

        with endpoint(Handler) as port, observed_processes(), patch("agat_worker.MAX_KNOWLEDGE_RESPONSE_BYTES", 512):
            client = CoordinatorClient(f"http://127.0.0.1:{port}", "test-token")
            status, body = 200, b" " * 513
            with self.assertRaisesRegex(ApiError, "response exceeds 512 bytes") as caught:
                client.knowledge_search("lease", QUERIES)
            self.assertEqual(caught.exception.status, 0)
            status, body = 503, b"unstructured error " * 1000
            with self.assertRaises(ApiError) as caught:
                client.knowledge_search("lease", QUERIES)
            self.assertEqual(caught.exception.status, 503)
            self.assertLessEqual(len(str(caught.exception)), 4096)


if __name__ == "__main__":
    unittest.main()
