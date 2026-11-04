"""Lease revocation must reach a blocked shadow caller before its deadline."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import socket
import threading
import time
import unittest
from unittest.mock import Mock, patch

import agat_worker
from agat_worker import ApiError, CoordinatorClient, LocalModelClient, execute_lease, lease_renewer
from local_decisions import CALLER_TIMING_VERSION, LocalDecisionClient
from test_embedding_transport import observed_processes


def lease():
    return {"leaseId": "owned", "run": {"name": "Fixture", "input": "Fixture"},
            "stage": {"attempt": 1}, "agent": {"name": "Primary", "model": "fixture"},
            "decisionShadow": {"profile": "local_decision_shadow_v1", "timeoutMs": 10000,
                               "callerTimingVersion": CALLER_TIMING_VERSION,
                               "callerAccountingVersion": agat_worker.DECISION_CALLER_ACCOUNTING,
                               "assignmentId": "assignment", "profileSha256": "0" * 64,
                               "request": {"id": "fixture"}}}


@contextmanager
def held_decision_endpoint():
    entered, eof, release = threading.Event(), threading.Event(), threading.Event()
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            calls.append(json.loads(self.rfile.read(int(self.headers["content-length"]))))
            if len(calls) == 1:
                entered.set()
                self.connection.settimeout(0.05)
                while not release.is_set():
                    try:
                        if not self.connection.recv(1):
                            eof.set()
                            return
                    except socket.timeout:
                        pass
                return
            body = b'{"fixture":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", entered, eof, calls
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(2)


class ShadowLeaseCancellationTest(unittest.TestCase):
    def test_bounded_renewal_preserves_worker_authentication_and_authoritative_http_status(self):
        status, captured = [204], []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args): pass
            def do_POST(self):
                captured.append((self.path, self.headers.get("Authorization"),
                                 self.rfile.read(int(self.headers["content-length"]))))
                body = b'{"error":"fixture lease rejection"}' if status[0] != 204 else b""
                self.send_response(status[0])
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = CoordinatorClient(f"http://127.0.0.1:{server.server_port}", node_token="fixture-token")
            with observed_processes() as helpers:
                for code in (204, 401, 403, 404, 409, 429, 503):
                    with self.subTest(code=code):
                        status[0] = code
                        if code == 204:
                            self.assertIsNone(client.renew("owned", timeout=1, cancelled=threading.Event()))
                        else:
                            with self.assertRaises(ApiError) as raised:
                                client.renew("owned", timeout=1, cancelled=threading.Event())
                            self.assertEqual(raised.exception.status, code)
                            self.assertEqual(str(raised.exception), "fixture lease rejection")
                self.assertEqual(len(helpers), 7)
                for helper in helpers:
                    self.assertIsNotNone(helper.poll())
                    self.assertNotIn("fixture-token", " ".join(helper.args))
            self.assertEqual(captured, [("/api/v1/leases/owned/renew", "Bearer fixture-token", b"{}")]*7)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)

    def test_bounded_renewal_deadline_does_not_depend_on_header_progress(self):
        release = threading.Event()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args): pass
            def do_POST(self):
                self.rfile.read(int(self.headers["content-length"]))
                try:
                    self.connection.sendall(b"HTTP/1.1 204 No Content\r\n")
                    while not release.wait(0.02): self.connection.sendall(b"x")
                except OSError:
                    pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = CoordinatorClient(f"http://127.0.0.1:{server.server_port}", node_token="fixture-token")
            cancelled = threading.Event()
            with observed_processes() as helpers:
                started = time.monotonic()
                with self.assertRaises(ApiError) as raised:
                    client.renew("owned", timeout=0.2, cancelled=cancelled)
                self.assertEqual(raised.exception.status, 0)
                self.assertFalse(cancelled.is_set())
                self.assertLess(time.monotonic() - started, 0.8)
                self.assertEqual(len(helpers), 1)
                self.assertIsNotNone(helpers[0].poll())
        finally:
            release.set()
            server.shutdown()
            server.server_close()
            thread.join(2)

    def test_completed_shadow_reaps_watcher_even_when_renewal_headers_keep_trickling(self):
        entered, release = threading.Event(), threading.Event()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args): pass
            def do_POST(self):
                self.rfile.read(int(self.headers["content-length"]))
                self.connection.sendall(b"HTTP/1.1 204 No Content\r\n")
                entered.set()
                try:
                    while not release.wait(0.05): self.connection.sendall(b"x")
                    self.connection.sendall(b": 1\r\nContent-Length: 0\r\n\r\n")
                except OSError:
                    pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        serving = threading.Thread(target=server.serve_forever, daemon=True)
        serving.start()
        try:
            client = CoordinatorClient(f"http://127.0.0.1:{server.server_port}")
            client.node_token = "fixture-token"
            decision = Mock(spec=LocalDecisionClient)
            def complete_after_renewal_started(*_args):
                self.assertTrue(entered.wait(2))
                return {"result": {"fixture": True}}
            decision.decide.side_effect = complete_after_renewal_started
            with observed_processes() as helpers, redirect_stderr(io.StringIO()):
                result = agat_worker._decide_with_lease_renewal(client, decision, "owned", {}, threading.Event())
                self.assertEqual(result, {"result": {"fixture": True}})
                self.assertFalse(any(t.name == "agat-shadow-lease" for t in threading.enumerate()))
                self.assertEqual(len(helpers), 1)
                self.assertIsNotNone(helpers[0].poll())
        finally:
            release.set()
            server.shutdown()
            server.server_close()
            serving.join(2)
            for thread in threading.enumerate():
                if thread.name == "agat-shadow-lease": thread.join(2)

    def test_revoked_lease_closes_real_transport_and_same_executor_accepts_next_lease(self):
        with held_decision_endpoint() as (url, entered, eof, calls), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()), \
                ThreadPoolExecutor(max_workers=1) as executor:
            revoked = threading.Event()
            captured_events = []
            observations, completions = [], []

            class Client:
                def event(self, *_args, **_kwargs): pass
                def begin_decision_shadow(self, *_args): return True
                def renew(self, _lease, **_kwargs):
                    if revoked.is_set(): raise ApiError(404, "Lease revoked")
                def record_decision_shadow(self, _lease, observation):
                    observations.append(observation)
                    if revoked.is_set(): raise ApiError(404, "Lease revoked")
                def complete(self, _lease, output, **_kwargs):
                    if revoked.is_set(): raise ApiError(404, "Lease revoked")
                    completions.append(output)
                def fail(self, *_args): raise ApiError(404, "Lease revoked")

            actual = LocalDecisionClient(url)
            class Decision:
                def decide(self, shadow, cancelled):
                    captured_events.append(cancelled)
                    return actual.decide(shadow, cancelled)

            client = Client()
            model = LocalModelClient("http://unused.invalid", "", decision_client=Decision())
            model.retrieve_knowledge = lambda *_args, **_kwargs: ""
            model.complete = lambda *_args, **_kwargs: "PRIMARY"
            future = executor.submit(execute_lease, client, model, lease(), "fixture", False)
            try:
                self.assertTrue(entered.wait(2))
                started = time.monotonic()
                revoked.set()
                future.result(timeout=1.5)
                self.assertTrue(eof.wait(0.2))
                self.assertLess(time.monotonic() - started, 1.5)
                self.assertEqual((observations[0]["status"], observations[0]["reason"]),
                                 ("unavailable", "cancelled"))
                self.assertLess(observations[0]["callerTiming"]["durationMs"], 1500)
                self.assertEqual(completions, [])  # Revocation still fences primary writes.
                self.assertEqual(len(calls), 1)
            finally:
                for event in captured_events: event.set()
                future.result(timeout=2)
            revoked.clear()
            executor.submit(execute_lease, client, model, lease(), "fixture", False).result(timeout=2)
            self.assertEqual(completions, ["PRIMARY"])
            self.assertEqual(observations[1]["result"], {"fixture": True})
            self.assertEqual(len(calls), 2)

    def test_definitive_rejection_cancels_but_transient_errors_keep_ownership_unknown(self):
        for status in (401, 403, 404, 409, 0, 429, 500, 503):
            with self.subTest(status=status), redirect_stderr(io.StringIO()):
                client = Mock(spec=CoordinatorClient)
                client.renew.side_effect = ApiError(status, "Fixture")
                stop = Mock(spec=threading.Event)
                stop.wait.side_effect = [False, True]
                stop.is_set.return_value = False
                cancelled = threading.Event()
                lease_renewer(client, "owned", stop, cancelled, interval=0.5, timeout=1)
                self.assertEqual(cancelled.is_set(), status in (401, 403, 404, 409))
                client.renew.assert_called_once_with("owned", timeout=1, cancelled=stop)

    def test_ordinary_primary_renewal_retains_its_existing_interval_and_timeout(self):
        client = Mock(spec=CoordinatorClient)
        stop = Mock(spec=threading.Event)
        stop.wait.side_effect = [False, True]
        lease_renewer(client, "owned", stop, threading.Event())
        client.renew.assert_called_once_with("owned")
        self.assertEqual([call.args for call in stop.wait.call_args_list], [(45,), (45,)])

    def test_stop_during_renewal_does_not_cancel_a_completed_shadow_call(self):
        client = Mock(spec=CoordinatorClient)
        stop, cancelled = threading.Event(), threading.Event()
        def revoked_after_completion(*_args, **_kwargs):
            stop.set()
            raise ApiError(404, "Lease ended after the caller returned")
        client.renew.side_effect = revoked_after_completion
        with redirect_stderr(io.StringIO()):
            lease_renewer(client, "owned", stop, cancelled, interval=0.001, timeout=1)
        self.assertFalse(cancelled.is_set())
        client.renew.assert_called_once()

    def test_watcher_is_scoped_to_authorized_shadow_and_stops_on_success_or_error(self):
        real_renewer = lease_renewer
        for variant in ("success", "decision_error", "legacy", "disabled", "dry_run", "denied", "intent_error"):
            with self.subTest(variant=variant), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                watchers = []
                entered = threading.Event()
                def observe(client, lease_id, stop, cancelled, **kwargs):
                    if kwargs.get("interval") == 0.5:
                        watchers.append((threading.current_thread(), stop))
                        entered.set()
                    real_renewer(client, lease_id, stop, cancelled, **kwargs)
                client = Mock(spec=CoordinatorClient)
                client.begin_decision_shadow.return_value = variant != "denied"
                if variant == "intent_error": client.begin_decision_shadow.side_effect = ApiError(503, "Fixture")
                decision = Mock(spec=LocalDecisionClient)
                def decide(_shadow, cancelled):
                    self.assertTrue(entered.wait(1))
                    self.assertFalse(cancelled.is_set())
                    if variant == "decision_error": raise RuntimeError("Fixture")
                    return {"result": {"fixture": True}}
                decision.decide.side_effect = decide
                model = LocalModelClient("http://unused.invalid", "",
                                         decision_client=None if variant == "disabled" else decision)
                model.retrieve_knowledge = lambda *_args, **_kwargs: ""
                model.complete = lambda *_args, **_kwargs: "PRIMARY"
                assignment = lease()
                if variant == "legacy": assignment["decisionShadow"].pop("callerAccountingVersion")
                with patch("agat_worker.lease_renewer", side_effect=observe):
                    execute_lease(client, model, assignment, "fixture", variant == "dry_run")
                expected = variant in ("success", "decision_error", "legacy")
                self.assertEqual(len(watchers), int(expected))
                for thread, stop in watchers:
                    self.assertTrue(stop.is_set())
                    self.assertFalse(thread.is_alive())
                self.assertEqual(decision.decide.call_count, int(expected))
                client.complete.assert_called_once()
                if variant != "dry_run": self.assertEqual(client.complete.call_args.args, ("owned", "PRIMARY"))
                client.fail.assert_not_called()

    def test_precancelled_shadow_does_not_start_an_extra_renewal(self):
        client, decision = Mock(spec=CoordinatorClient), Mock(spec=LocalDecisionClient)
        cancelled = threading.Event()
        cancelled.set()
        decision.decide.return_value = {"status": "unavailable", "reason": "cancelled"}
        actual = agat_worker._decide_with_lease_renewal(client, decision, "owned", {}, cancelled)
        self.assertEqual(actual["reason"], "cancelled")
        client.renew.assert_not_called()
        decision.decide.assert_called_once_with({}, cancelled)


if __name__ == "__main__":
    unittest.main()
