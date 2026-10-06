from __future__ import annotations

import http.client
import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from agat_worker import LocalModelClient, _execute_lease_body
from local_decisions import CALLER_TIMING_VERSION, LocalDecisionClient, validate_decision_url
from telemetry import ExecutionMetrics, WorkerTelemetry
from decision_runtime.contracts import Request, fingerprint
from decision_runtime.engine import DecisionEngine
from decision_runtime.server import make_server
from decision_runtime.tests.test_decisions import Backend, request


class ClientTest(unittest.TestCase):
    def setUp(self):
        self.backend = Backend()
        self.engine = DecisionEngine(self.backend)
        self.server = make_server(self.engine, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = LocalDecisionClient(f"http://127.0.0.1:{self.server.server_port}")
        self.shadow = {"profile": "local_decision_shadow_v1", "timeoutMs": 1000,
                       "profileSha256": fingerprint(self.engine.profile()), "request": request()}

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_real_runtime_pin_and_input_roundtrip(self):
        result = self.client.decide(self.shadow)["result"]
        self.assertEqual(result["inputSha256"], Request.from_dict(request()).input_sha256)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(self.backend.calls, 1)

    def test_score_v2_roundtrip_preserves_version_and_numeric_zero(self):
        raw = request()
        raw.update(kind="score", inputFingerprintVersion="binary64-v1", options=[
            {"id": "low", "description": "Низкий", "value": -0.0},
            {"id": "high", "description": "Высокий", "value": 1e-7}])
        self.backend.logits = [1000, -1000]
        result = self.client.decide({**self.shadow, "profile": "local_decision_shadow_v2", "request": raw})["result"]
        self.assertEqual(result["inputSha256"], Request.from_dict(raw).input_sha256)
        self.assertEqual(result["inputFingerprintVersion"], "binary64-v1")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["value"], 0)
        self.assertIsNot(result["value"], False)

    def test_negotiated_timing_covers_local_http_and_legacy_envelope_is_unchanged(self):
        legacy = self.client.decide(self.shadow)
        self.assertEqual(set(legacy), {"result"})
        started = time.monotonic()
        measured = self.client.decide({**self.shadow, "callerTimingVersion": CALLER_TIMING_VERSION})
        wall_ms = (time.monotonic()-started)*1000
        timing = measured["callerTiming"]
        self.assertEqual({k: v for k, v in timing.items() if k != "durationMs"}, {
            "schemaVersion": CALLER_TIMING_VERSION, "clock": "monotonic", "boundary": "local_http_call"})
        self.assertGreaterEqual(timing["durationMs"], measured["result"]["durationMs"]-0.001)
        self.assertLessEqual(timing["durationMs"], wall_ms+0.001)
        future = self.client.decide({**self.shadow, "callerTimingVersion": "unknown"})
        self.assertEqual(set(future), {"result"})

    def test_negotiated_timeout_records_elapsed_time_instead_of_model_scoring_duration(self):
        score = self.backend.score
        def delayed(parsed):
            time.sleep(0.2)
            return score(parsed)
        with patch.object(self.backend, "score", side_effect=delayed):
            measured = self.client.decide({**self.shadow, "timeoutMs": 100, "callerTimingVersion": CALLER_TIMING_VERSION})
        self.assertEqual((measured["status"], measured["reason"]), ("unavailable", "timeout"))
        self.assertGreaterEqual(measured["callerTiming"]["durationMs"], 90)
        self.assertLess(measured["callerTiming"]["durationMs"], 600)

    def test_negotiated_cancelled_call_keeps_failure_timing_without_contacting_backend(self):
        cancelled = threading.Event(); cancelled.set()
        measured = self.client.decide({**self.shadow, "callerTimingVersion": CALLER_TIMING_VERSION}, cancelled)
        self.assertEqual((measured["status"], measured["reason"]), ("unavailable", "cancelled"))
        self.assertGreaterEqual(measured["callerTiming"]["durationMs"], 0)
        self.assertEqual(self.backend.calls, 0)

    def test_server_deadline_survives_http_as_bound_error_and_health_becomes_unavailable(self):
        from decision_runtime.isolated import IsolatedBackend
        from decision_runtime.tests.test_isolated import fixture_factory
        with IsolatedBackend(fixture_factory, {"mode": "hang"}, timeout_ms=100) as isolated:
            self.engine.backend = isolated
            envelope = self.client.decide({**self.shadow, "timeoutMs": 2000,
                                          "profileSha256": fingerprint(self.engine.profile())})
            result = envelope["result"]
            self.assertEqual((result["status"], result["reason"]), ("error", "inference_timeout"))
            self.assertEqual(result["inputSha256"], Request.from_dict(request()).input_sha256)
            self.assertFalse(isolated.is_available())
            connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
            try:
                connection.request("GET", "/health")
                response = connection.getresponse()
                self.assertEqual(response.status, 503)
                health = json.loads(response.read())
                self.assertEqual(health["status"], "unavailable")
                self.assertEqual(health["profileSha256"], fingerprint(self.engine.profile()))
            finally:
                connection.close()

    def test_mismatch_and_busy_are_not_retried(self):
        self.assertEqual(self.client.decide({**self.shadow, "profileSha256": "0" * 64})["reason"], "profile_mismatch")
        self.assertEqual(self.backend.calls, 0)
        entered, release = threading.Event(), threading.Event()
        score = self.backend.score

        def blocked(parsed):
            entered.set()
            release.wait(timeout=2)
            return score(parsed)

        self.backend.score = blocked
        task = threading.Thread(target=lambda: self.client.decide(self.shadow))
        task.start()
        try:
            self.assertTrue(entered.wait(timeout=1))
            self.assertEqual(self.client.decide(self.shadow)["reason"], "busy")
        finally:
            release.set()
            task.join(timeout=2)
        self.assertEqual(self.backend.calls, 1)

    def test_timeout_and_cancellation_discard_late_result(self):
        entered, release = threading.Event(), threading.Event()
        score = self.backend.score

        def blocked(parsed):
            entered.set()
            release.wait(timeout=2)
            return score(parsed)

        self.backend.score = blocked
        start = time.monotonic()
        try:
            self.assertEqual(self.client.decide({**self.shadow, "timeoutMs": 100})["reason"], "timeout")
            self.assertLess(time.monotonic() - start, 0.6)
        finally:
            release.set()
        cancelled = threading.Event(); cancelled.set()
        self.assertEqual(self.client.decide(self.shadow, cancelled)["reason"], "cancelled")

    def test_cancellation_interrupts_inflight_read(self):
        entered, release, cancelled = threading.Event(), threading.Event(), threading.Event()
        score = self.backend.score

        def blocked(parsed):
            entered.set()
            release.wait(timeout=2)
            return score(parsed)

        self.backend.score = blocked
        results = []
        task = threading.Thread(target=lambda: results.append(self.client.decide(self.shadow, cancelled)))
        task.start()
        try:
            self.assertTrue(entered.wait(timeout=1))
            cancelled.set()
            task.join(timeout=0.5)
            self.assertFalse(task.is_alive())
            self.assertEqual(results[0]["reason"], "cancelled")
        finally:
            release.set()
            task.join(timeout=2)


class UntrustedServerTest(unittest.TestCase):
    def test_wall_clock_deadline_covers_a_continuously_dripping_response_body(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", "200")
                self.end_headers()
                try:
                    for _ in range(200):
                        self.wfile.write(b" "); self.wfile.flush(); time.sleep(0.02)
                except (BrokenPipeError, ConnectionResetError): pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            client = LocalDecisionClient(f"http://127.0.0.1:{server.server_port}")
            started = time.monotonic()
            result = client.decide({"profile": "local_decision_shadow_v1", "timeoutMs": 100,
                                    "profileSha256": "1" * 64, "request": request()})
            self.assertEqual(result["reason"], "timeout")
            self.assertLess(time.monotonic() - started, 0.6)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)

    def test_no_redirect_proxy_or_unbounded_read(self):
        responses = [(302, b"{}"), (200, b'{"x":1,"x":2}'), (200, b'{"x":1e999}'),
                     (200, b"x" * (65536 + 1)), (200, b"[1]")]
        for status, body in responses:
            with self.subTest(status=status, length=len(body)):
                class Handler(BaseHTTPRequestHandler):
                    def log_message(self, *_): pass
                    def do_POST(self):
                        self.rfile.read(int(self.headers["Content-Length"]))
                        self.send_response(status)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Location", "http://example.invalid")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)

                server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
                thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
                try:
                    client = LocalDecisionClient(f"http://127.0.0.1:{server.server_port}")
                    with patch.dict("os.environ", {"HTTP_PROXY": "http://example.invalid:8080"}):
                        self.assertEqual(client.decide({"profile": "local_decision_shadow_v1", "timeoutMs": 1000,
                                                       "profileSha256": "1" * 64, "request": request()})["reason"], "invalid_response")
                finally:
                    server.shutdown(); server.server_close(); thread.join(timeout=2)

    def test_loopback_only_configuration(self):
        for url in ["http://localhost:8766", "http://127.0.0.2:8766", "https://127.0.0.1:8766",
                    "http://127.0.0.1:8766/api", "http://user@127.0.0.1:8766", "http://127.0.0.1:8766?x=1"]:
            with self.subTest(url=url), self.assertRaises(ValueError): validate_decision_url(url)


class WorkerFallbackTest(unittest.TestCase):
    def test_shadow_fault_never_changes_primary_output_or_fails_lease(self):
        class Client:
            def __init__(self): self.outputs = []; self.failures = []
            def event(self, *_args, **_kwargs): pass
            def renew(self, _lease_id): pass
            def record_decision_shadow(self, *_args): raise RuntimeError("private internal error")
            def complete(self, _lease_id, output, **_kwargs): self.outputs.append(output)
            def fail(self, *_args): self.failures.append(_args)

        client = Client()
        model = LocalModelClient("http://unused.invalid", "")
        model.retrieve_knowledge = lambda *args, **kwargs: ""
        model.complete = lambda *args, **kwargs: "PRIMARY"
        lease = {"leaseId": "lease", "run": {"name": "Test", "input": "private"}, "stage": {"attempt": 1},
                 "agent": {"name": "Test", "model": "local"}, "decisionShadow": {"profile": "local_decision_shadow_v1"}}
        _execute_lease_body(client, model, lease, "local", False, ExecutionMetrics.start("local", "none"), WorkerTelemetry(enabled=False))
        self.assertEqual(client.outputs, ["PRIMARY"])
        self.assertEqual(client.failures, [])


if __name__ == "__main__":
    unittest.main()
