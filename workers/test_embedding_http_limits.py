from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
import threading
import unittest
from unittest.mock import Mock, patch

import agat_worker
from agat_worker import CoordinatorClient, LocalModelClient, execute_knowledge_lease
from telemetry import WorkerTelemetry


@contextmanager
def embedding_endpoint(body: bytes, *, status: int = 200, chunked: bool = False, hold_after: int | None = None):
    sent, release = threading.Event(), threading.Event()
    errors: list[str] = []
    requests: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass

        def do_POST(self):
            try:
                if self.path != "/v1/embeddings":
                    raise AssertionError("Unexpected model route")
                requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Connection", "close")
                self.send_header("Transfer-Encoding" if chunked else "Content-Length", "chunked" if chunked else str(len(body)))
                self.end_headers()

                def write(part: bytes):
                    if chunked:
                        self.wfile.write(f"{len(part):x}\r\n".encode() + part + b"\r\n")
                    else:
                        self.wfile.write(part)
                    self.wfile.flush()

                if hold_after is not None:
                    write(body[:hold_after])
                    sent.set()
                    if not release.wait(5):
                        raise AssertionError("The fixture tail was not released")
                    write(body[hold_after:])
                else:
                    write(body)
                if chunked:
                    self.wfile.write(b"0\r\n\r\n")
            except (BrokenPipeError, ConnectionResetError):
                # Expected when the client refuses an oversized/prefix-only body.
                pass
            except Exception as error:
                errors.append(repr(error))
            finally:
                self.close_connection = True

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with patch.dict(os.environ, {"NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}):
            yield f"http://127.0.0.1:{server.server_port}/v1", sent, release, requests
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        if thread.is_alive() or errors:
            raise AssertionError(f"Embedding fixture did not close cleanly: {errors}")


class EmbeddingHttpLimitTests(unittest.TestCase):
    @staticmethod
    def body(**metadata) -> bytes:
        return json.dumps({"data": [{"index": 0, "embedding": [1.0, 0.0]}], **metadata}, ensure_ascii=False).encode()

    @staticmethod
    def client(url: str) -> LocalModelClient:
        return LocalModelClient(url, "", telemetry=WorkerTelemetry(enabled=False))

    def test_accepts_valid_utf8_json_at_the_exact_byte_limit(self) -> None:
        body = self.body(note="Пример")
        with embedding_endpoint(body) as (url, _sent, _release, requests), patch.object(agat_worker, "MAX_EMBEDDING_RESPONSE_BYTES", len(body), create=True):
            self.assertEqual(self.client(url).embed("local", ["input"]), [[1.0, 0.0]])
            self.assertEqual(requests, [{"model": "local", "input": ["input"]}])

    def test_rejects_fixed_length_response_one_byte_over_the_limit(self) -> None:
        body = self.body()
        with embedding_endpoint(body) as (url, _sent, _release, _requests), patch.object(agat_worker, "MAX_EMBEDDING_RESPONSE_BYTES", len(body) - 1, create=True):
            with self.assertRaisesRegex(RuntimeError, "response exceeds"):
                self.client(url).embed("local", ["input"])

    def test_rejects_chunked_response_without_relying_on_content_length(self) -> None:
        body = self.body() + b" " * 2048
        with embedding_endpoint(body, chunked=True) as (url, _sent, _release, _requests), patch.object(agat_worker, "MAX_EMBEDDING_RESPONSE_BYTES", 256, create=True):
            with self.assertRaisesRegex(RuntimeError, "response exceeds"):
                self.client(url).embed("local", ["input"])

    def test_measures_response_bytes_before_utf8_decoding(self) -> None:
        body = self.body(note="ю" * 100)
        self.assertGreater(len(body), len(body.decode()))
        with embedding_endpoint(body) as (url, _sent, _release, _requests), patch.object(agat_worker, "MAX_EMBEDDING_RESPONSE_BYTES", len(body.decode()), create=True):
            with self.assertRaisesRegex(RuntimeError, "response exceeds"):
                self.client(url).embed("local", ["input"])

    def test_error_prefix_does_not_wait_for_an_unfinished_body(self) -> None:
        for chunked in (False, True):
            with self.subTest(chunked=chunked), embedding_endpoint(b"\xff" * 2048, status=500, chunked=chunked, hold_after=256) as (url, sent, release, _requests), patch.object(agat_worker, "MAX_EMBEDDING_ERROR_BYTES", 64, create=True):
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(self.client(url).embed, "local", ["input"])
                    try:
                        self.assertTrue(sent.wait(2))
                        with self.assertRaisesRegex(RuntimeError, "HTTP 500") as caught:
                            future.result(timeout=2)
                        self.assertLessEqual(len(str(caught.exception)), 120)
                        self.assertFalse(release.is_set(), "Client must reject from the bounded prefix before the server releases the rest")
                    finally:
                        release.set()

    def test_full_supported_batch_fits_the_production_limit(self) -> None:
        vector = [-1.7976931348623157e308, -2.2250738585072014e-308] * 2048
        body = json.dumps({"data": [{"index": index, "embedding": vector} for index in range(32)]}).encode()
        self.assertLess(len(body), getattr(agat_worker, "MAX_EMBEDDING_RESPONSE_BYTES", 8 * 1024 * 1024))
        with embedding_endpoint(body) as (url, _sent, _release, _requests):
            self.assertEqual(self.client(url).embed("local", ["input"] * 32), [vector] * 32)

    def test_worker_reports_oversize_as_failure_without_completing_the_lease(self) -> None:
        body = self.body() + b" " * 2048
        coordinator = Mock(spec=CoordinatorClient)
        coordinator.knowledge_fail.return_value = {"retrying": True}
        coordinator.knowledge_complete.return_value = {"completed": True, "remainingChunks": 0}
        lease = {"leaseId": "lease", "collection": {"embeddingModel": "local"},
                 "document": {"name": "Synthetic"}, "chunks": [{"id": "chunk", "content": "input"}]}
        with embedding_endpoint(body) as (url, _sent, _release, _requests), patch.object(agat_worker, "MAX_EMBEDDING_RESPONSE_BYTES", 256, create=True), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            execute_knowledge_lease(coordinator, self.client(url), lease, dry_run=False)
        coordinator.knowledge_complete.assert_not_called()
        coordinator.knowledge_fail.assert_called_once()
        self.assertIn("response exceeds", coordinator.knowledge_fail.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
