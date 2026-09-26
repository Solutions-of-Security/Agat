import multiprocessing
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

from decision_runtime.contracts import DecisionError, Request
from decision_runtime.engine import DecisionEngine, Scores
from decision_runtime.isolated import IsolatedBackend, _receive
from decision_runtime.tests.test_decisions import Backend, request


class IsolatedFixture(Backend):
    def __init__(self, config):
        super().__init__([3, 0, -2])
        self.identity = {**self.identity, "maxInputTokens": 2048}
        self.config = config
        if config.get("ignore_term"):
            signal.signal(signal.SIGTERM, signal.SIG_IGN)

    def score(self, parsed):
        mode = self.config.get("mode")
        if self.config.get("entered"):
            Path(self.config["entered"]).write_text("entered")
        if mode in {"hang", "slow"}:
            time.sleep(10 if mode == "hang" else .25)
            if self.config.get("completed"):
                Path(self.config["completed"]).write_text("completed")
        if mode == "crash": os._exit(45)
        if mode == "raise": raise RuntimeError("PRIVATE SOURCE ERROR")
        if mode == "identity": self.identity["revision"] = "changed"
        if mode == "nan": return Scores([float("nan"), 0, -2], 12)
        if mode == "bad_count": return Scores([3, 0, -2], True)
        if parsed.state == "oversized": raise DecisionError("context_too_long", "PRIVATE INPUT")
        return Scores([3, 0, -2], 12)


def fixture_factory(config):
    if config.get("mode") == "startup_failure": raise RuntimeError("PRIVATE STARTUP ERROR")
    if config.get("mode") == "startup_crash": os._exit(46)
    if config.get("mode") == "startup_hang": time.sleep(10)
    return IsolatedFixture(config)


class IsolatedTest(unittest.TestCase):
    def backend(self, config=None, timeout=1000):
        return IsolatedBackend(fixture_factory, config or {}, timeout_ms=timeout)

    def test_fresh_process_reused_and_profile_pins_execution_policy(self):
        with self.backend() as backend:
            pid = backend.diagnostics()["childPid"]
            self.assertNotEqual(pid, os.getpid())
            engine = DecisionEngine(backend)
            first = engine.decide(request()); second = engine.decide(request())
            self.assertEqual(first["distribution"], second["distribution"])
            self.assertEqual(first["value"], "yes")
            self.assertEqual(backend.diagnostics()["childPid"], pid)
            self.assertEqual(backend.identity["inferenceExecution"], {
                "kind": "isolated-process", "startMethod": "spawn", "deadlineMs": 1000,
                "timeoutAction": "stop_process_require_restart",
                "cancellationAction": "stop_process_require_restart", "cancellationPollIntervalMs": 20})
        self.assertFalse(backend.is_available())
        with self.assertRaises(ProcessLookupError): os.kill(pid, 0)
        backend.close()  # Explicit cleanup is idempotent.

    def test_deadline_kills_noncooperative_process_and_never_runs_late_result(self):
        with tempfile.TemporaryDirectory() as directory:
            entered, completed = Path(directory) / "entered", Path(directory) / "completed"
            with self.backend({"mode": "hang", "ignore_term": True, "entered": str(entered), "completed": str(completed)}, 100) as backend:
                started = time.monotonic()
                engine = DecisionEngine(backend)
                result = engine.decide(request())
                self.assertLess(time.monotonic() - started, 2)
                self.assertEqual((result["status"], result["reason"]), ("error", "inference_timeout"))
                self.assertTrue(entered.exists()); self.assertFalse(completed.exists())
                self.assertEqual(backend.diagnostics()["childExitCode"], -signal.SIGKILL)
                self.assertFalse(backend.is_available())
                self.assertEqual(engine.decide(request())["reason"], "backend_unavailable")
                self.assertIsNone(result["value"]); self.assertEqual(result["distribution"], [])
                with self.assertRaises(ProcessLookupError): os.kill(backend.diagnostics()["childPid"], 0)

    def test_ordinary_input_rejection_does_not_retire_healthy_process(self):
        with self.backend() as backend:
            engine = DecisionEngine(backend)
            result = engine.decide({**request(), "state": "oversized"})
            self.assertEqual(result["reason"], "context_too_long")
            self.assertTrue(backend.is_available())
            self.assertEqual(engine.decide(request())["status"], "ok")

    def test_crash_exceptions_invalid_scores_and_changed_identity_retire_process(self):
        for mode in ("crash", "raise", "nan", "bad_count", "identity"):
            with self.subTest(mode=mode), self.backend({"mode": mode}) as backend:
                result = DecisionEngine(backend).decide(request())
                self.assertEqual(result["reason"], "backend_error")
                self.assertFalse(backend.is_available())
                self.assertNotIn("PRIVATE", str(result))
                self.assertIsNotNone(backend.diagnostics()["childExitCode"])

    def test_concurrent_call_is_rejected_without_queue_or_retiring_active_work(self):
        with tempfile.TemporaryDirectory() as directory:
            entered = Path(directory) / "entered"
            with self.backend({"mode": "slow", "entered": str(entered)}) as backend:
                engine = DecisionEngine(backend); results = []
                thread = threading.Thread(target=lambda: results.append(engine.decide(request())))
                thread.start()
                for _ in range(100):
                    if entered.exists(): break
                    time.sleep(.005)
                self.assertTrue(entered.exists())
                started = time.monotonic()
                self.assertEqual(engine.decide(request())["reason"], "backend_unavailable")
                self.assertLess(time.monotonic() - started, .1)
                thread.join(timeout=2)
                self.assertEqual(results[0]["status"], "ok")
                self.assertTrue(backend.is_available())

    def test_startup_failure_and_timeout_leave_no_child_and_no_exception_content(self):
        before = {p.pid for p in multiprocessing.active_children()}
        with self.assertRaisesRegex(ValueError, "startup failed") as error:
            self.backend({"mode": "startup_failure"})
        self.assertNotIn("PRIVATE", str(error.exception))
        with self.assertRaises(TimeoutError):
            IsolatedBackend(fixture_factory, {"mode": "startup_hang"}, timeout_ms=100, startup_timeout_s=.2)
        with self.assertRaisesRegex(RuntimeError, "exited during startup"):
            self.backend({"mode": "startup_crash"})
        self.assertEqual({p.pid for p in multiprocessing.active_children()}, before)

    @unittest.skipUnless(os.name == "posix", "The MLX service uses POSIX process supervision")
    def test_abrupt_parent_exit_does_not_leave_its_inference_process_running(self):
        with tempfile.TemporaryDirectory() as directory:
            source = '''
import os, sys, threading, time
from pathlib import Path
from decision_runtime.contracts import Request
from decision_runtime.isolated import IsolatedBackend
from decision_runtime.tests.test_isolated import fixture_factory
from decision_runtime.tests.test_decisions import request
entered = Path(sys.argv[1]) / "entered"
backend = IsolatedBackend(fixture_factory, {"mode": "hang", "entered": str(entered)}, timeout_ms=10000)
threading.Thread(target=lambda: backend.score(Request.from_dict(request())), daemon=True).start()
for _ in range(200):
    if entered.exists(): break
    time.sleep(.005)
assert entered.exists()
print(backend.diagnostics()["childPid"], flush=True)
os._exit(0)
'''
            result = subprocess.run([sys.executable, "-c", source, directory], capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            pid = int(result.stdout.strip())
            for _ in range(100):
                state = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True).stdout.strip()
                if not state or state.startswith("Z"): break
                time.sleep(.01)
            self.assertTrue(not state or state.startswith("Z"), f"Orphan {pid} is still executing")

    def test_invalid_deadline_is_rejected_before_start(self):
        for value in (99, 10001, True, None, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError): self.backend(timeout=value)

    def test_partial_private_frame_cannot_extend_wall_clock_deadline(self):
        reader, writer = socket.socketpair()
        try:
            writer.sendall(b"\x00\x00")
            started = time.monotonic()
            with self.assertRaises(TimeoutError): _receive(reader, started + .05)
            self.assertLess(time.monotonic() - started, .2)
        finally:
            reader.close(); writer.close()


if __name__ == "__main__": unittest.main()
