"""Exercise the real CLI, HTTP handlers and spawned inference process together."""

import contextlib
import http.client
import io
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from decision_runtime import VERSION
from decision_runtime.__main__ import main
from decision_runtime.contracts import fingerprint
from decision_runtime.engine import DecisionEngine
from decision_runtime.isolated import IsolatedBackend
from decision_runtime.lifecycle import log_retirement, retirement_event
from decision_runtime.server import CANCEL_ON_DISCONNECT_HEADER, make_server
from decision_runtime.tests.test_decisions import Backend, request
from decision_runtime.tests.test_isolated import fixture_factory

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = "PRIVATE REQUEST AND MODEL EXCEPTION"


def fixture_cli(mode, directory, retire):
    """Replace weights only; process, HTTP, cancellation and CLI remain real."""
    config = {"mode": "hang" if mode in {"timeout", "cancel"} else mode,
              "ignore_term": mode in {"timeout", "cancel"},
              "entered": str(directory / "entered")}

    def backend(_factory, _config, **options):
        return IsolatedBackend(fixture_factory, config, **options)

    def server(engine, port, **options):
        service = make_server(engine, port, **options)
        ready = {"port": service.server_port, "childPid": engine.backend.diagnostics()["childPid"],
                 "profileSha256": fingerprint(engine.profile())}
        (directory / "ready.tmp").write_text(json.dumps(ready))
        (directory / "ready.tmp").rename(directory / "ready.json")
        return service

    args = ["decision_runtime", "serve", "--manifest", PRIVATE,
            "--inference-timeout-ms", "100" if mode == "timeout" else "10000", "--port", "0"]
    if retire:
        args.append("--exit-on-backend-unavailable")
    with patch.object(sys, "argv", args), \
         patch("decision_runtime.isolated.IsolatedBackend", side_effect=backend), \
         patch("decision_runtime.__main__.make_server", side_effect=server):
        return main()


class ExitDiagnosticsTest(unittest.TestCase):
    def test_record_excludes_unknown_fields_invalid_scalars_and_untrusted_reason(self):
        for invalid in (True, PRIVATE, {"source": PRIVATE}, [PRIVATE], float("inf"), 2**64):
            with self.subTest(value=invalid):
                event = retirement_event(PRIVATE, {"stopReason": invalid, "childPid": invalid,
                                                  "childExitCode": invalid, "request": PRIVATE})
                self.assertEqual(event["reason"], "backend_unavailable")
                self.assertIsNone(event["childPid"])
                self.assertIsNone(event["childExitCode"])
                self.assertIsNone(event["profileSha256"])
                self.assertNotIn(PRIVATE, json.dumps(event))
                self.assertLess(len(json.dumps(event)), 512)
        self.assertEqual(retirement_event("a" * 64, None)["reason"], "backend_unavailable")

    def test_logger_keeps_exit_semantics_when_diagnostics_or_stderr_fail(self):
        class BrokenBackend:
            def diagnostics(self):
                raise RuntimeError(PRIVATE)

        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            log_retirement(BrokenBackend(), "a" * 64)
        self.assertNotIn(PRIVATE, output.getvalue())
        self.assertEqual(json.loads(output.getvalue())["reason"], "backend_unavailable")
        for error in (OSError("disk full"), ValueError("closed stream")):
            with self.subTest(error=error), patch("decision_runtime.lifecycle.sys.stderr") as stream:
                stream.write.side_effect = error
                log_retirement(BrokenBackend(), "a" * 64)

    def test_cli_records_only_after_handler_drain_and_backend_close(self):
        order = []
        backend = Backend()
        backend.close = lambda: order.append("reaped")
        backend.diagnostics = lambda: {"stopReason": "inference_timeout", "childPid": 123,
                                       "childExitCode": -9}
        expected_profile = fingerprint(DecisionEngine(backend).profile())

        class Stopped:
            server_port = 0
            backend_failed = threading.Event()
            def __enter__(self): return self
            def __exit__(self, *_): order.append("drained")
            def serve_forever(self): self.backend_failed.set()

        class Log(io.StringIO):
            def write(self, value):
                if value.startswith("{"):
                    order.append("logged")
                return super().write(value)
            def flush(self): order.append("flushed")

        output = Log()
        with patch.object(sys, "argv", ["decision_runtime", "serve", "--manifest", PRIVATE,
                                      "--inference-timeout-ms", "100", "--exit-on-backend-unavailable"]), \
             patch("decision_runtime.isolated.IsolatedBackend", return_value=backend), \
             patch("decision_runtime.__main__.make_server", return_value=Stopped()), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(output):
            self.assertEqual(main(), 75)
        self.assertEqual(order, ["drained", "reaped", "logged", "flushed"])
        event = json.loads(output.getvalue())
        self.assertEqual(event, {
            "schemaVersion": "agat.decision.retirement.v1", "eventName": "decision.backend_retired",
            "runtimeVersion": VERSION, "profileSha256": expected_profile, "exitCode": 75,
            "reason": "inference_timeout", "childPid": 123, "childExitCode": -9,
        })

    def test_failed_http_drain_does_not_claim_exit_75(self):
        backend = Backend(); backend.close = lambda: None
        class Interrupted:
            server_port = 0
            backend_failed = threading.Event()
            def __enter__(self): return self
            def __exit__(self, *_): raise KeyboardInterrupt
            def serve_forever(self): self.backend_failed.set()
        output = io.StringIO()
        with patch.object(sys, "argv", ["decision_runtime", "serve", "--manifest", "fixture",
                                      "--inference-timeout-ms", "100", "--exit-on-backend-unavailable"]), \
             patch("decision_runtime.isolated.IsolatedBackend", return_value=backend), \
             patch("decision_runtime.__main__.make_server", return_value=Interrupted()), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(output):
            self.assertEqual(main(), 130)
        self.assertEqual(output.getvalue(), "")

    @contextlib.contextmanager
    def runtime(self, mode, retire=True):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with (directory / "stdout").open("wb") as stdout, (directory / "stderr").open("wb") as stderr:
                process = subprocess.Popen([sys.executable, "-m", "decision_runtime.tests.test_exit_diagnostics", "--fixture", mode,
                                            str(directory), "1" if retire else "0"], cwd=ROOT,
                                           stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr)
                try:
                    deadline = time.monotonic() + 10
                    while not (directory / "ready.json").exists():
                        self.assertIsNone(process.poll(), (directory / "stderr").read_text())
                        self.assertLess(time.monotonic(), deadline, "fixture startup timed out")
                        time.sleep(.01)
                    ready = json.loads((directory / "ready.json").read_text())
                    yield process, directory, ready
                finally:
                    if process.poll() is None:
                        process.terminate()
                        try: process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill(); process.wait(timeout=3)
                    # Parent death also stops the inference child; bound cleanup on failures.
                    if (directory / "ready.json").exists():
                        pid = json.loads((directory / "ready.json").read_text())["childPid"]
                        deadline = time.monotonic() + 3
                        while time.monotonic() < deadline:
                            try: os.kill(pid, 0)
                            except ProcessLookupError: break
                            time.sleep(.02)
                        else: self.fail("fixture left an inference child")

    def call(self, ready, raw=None, method="POST"):
        connection = http.client.HTTPConnection("127.0.0.1", ready["port"], timeout=3)
        try:
            connection.request(method, "/health" if method == "GET" else "/v1/decisions",
                               body=None if method == "GET" else json.dumps(raw or {**request(), "state": PRIVATE}),
                               headers={"Content-Type": "application/json"})
            response = connection.getresponse(); body = response.read()
            self.assertEqual(response.getheader("Content-Length"), str(len(body)))
            return response.status, json.loads(body)
        finally: connection.close()

    def assert_retired(self, process, directory, ready, reason, child_exit=None):
        self.assertEqual(process.wait(timeout=5), 75)
        raw = (directory / "stderr").read_text()
        self.assertEqual(len(raw.splitlines()), 1)
        self.assertLess(len(raw.encode()), 512)
        self.assertNotIn("PRIVATE", raw)
        event = json.loads(raw)
        self.assertEqual((event["reason"], event["profileSha256"], event["childPid"]),
                         (reason, ready["profileSha256"], ready["childPid"]))
        self.assertEqual(event["exitCode"], 75)
        if child_exit is not None: self.assertEqual(event["childExitCode"], child_exit)
        self.assertIsInstance(event["childExitCode"], int)
        with self.assertRaises(ProcessLookupError): os.kill(ready["childPid"], 0)

    def test_real_timeout_crash_and_backend_exception_keep_complete_http_response(self):
        for mode, status, reason, child_exit in (("timeout", 504, "inference_timeout", -signal.SIGKILL),
                                                ("crash", 500, "backend_error", 45),
                                                ("raise", 500, "backend_error", None)):
            with self.subTest(mode=mode), self.runtime(mode) as (process, directory, ready):
                actual_status, result = self.call(ready)
                self.assertEqual((actual_status, result["reason"]), (status, reason))
                self.assertIsNone(result["value"])
                self.assertEqual(result["distribution"], [])
                self.assert_retired(process, directory, ready, reason, child_exit)

    def test_real_opted_in_disconnect_preserves_cancellation_reason(self):
        with self.runtime("cancel") as (process, directory, ready):
            body = json.dumps({**request(), "state": PRIVATE}).encode()
            header = (f"POST /v1/decisions HTTP/1.1\r\nHost: 127.0.0.1:{ready['port']}\r\n"
                      f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n"
                      f"{CANCEL_ON_DISCONNECT_HEADER}: 1\r\n\r\n").encode()
            with socket.create_connection(("127.0.0.1", ready["port"]), timeout=3) as connection:
                connection.sendall(header + body)
                deadline = time.monotonic() + 3
                while not (directory / "entered").exists():
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(.005)
                connection.shutdown(socket.SHUT_WR)
                response = http.client.HTTPResponse(connection); response.begin(); raw = response.read()
                self.assertEqual(response.getheader("Content-Length"), str(len(raw)))
                self.assertEqual((response.status, json.loads(raw)["reason"]), (500, "inference_cancelled"))
            self.assert_retired(process, directory, ready, "inference_cancelled", -signal.SIGKILL)

    def test_real_idle_child_death_is_reaped_and_reported_without_http(self):
        with self.runtime("normal") as (process, directory, ready):
            os.kill(ready["childPid"], signal.SIGKILL)
            self.assert_retired(process, directory, ready, "backend_unavailable", -signal.SIGKILL)

    def test_real_input_rejection_and_normal_shutdown_do_not_log_retirement(self):
        with self.runtime("normal") as (process, directory, ready):
            self.assertEqual(self.call(ready, {"invalid": True})[0], 400)
            self.assertEqual(self.call(ready, {**request(), "state": "oversized"})[0], 422)
            self.assertEqual(self.call(ready)[0], 200)
            process.terminate(); self.assertEqual(process.wait(timeout=5), 130)
            self.assertEqual((directory / "stderr").read_text(), "")

    def test_real_default_mode_keeps_unavailable_service_online_without_retirement(self):
        with self.runtime("timeout", retire=False) as (process, directory, ready):
            self.assertEqual(self.call(ready)[0], 504)
            self.assertEqual(self.call(ready, method="GET")[0], 503)
            self.assertIsNone(process.poll())
            process.terminate(); self.assertEqual(process.wait(timeout=5), 130)
            self.assertEqual((directory / "stderr").read_text(), "")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--fixture":
        raise SystemExit(fixture_cli(sys.argv[2], Path(sys.argv[3]), sys.argv[4] == "1"))
    unittest.main()
