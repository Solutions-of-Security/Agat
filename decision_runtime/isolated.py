"""Optional single inference process with an operator-owned wall-clock deadline.

A timeout retires the process and its private socket. It is never reused/restarted
inside a request; the operator must restart the runtime after examining the fault.
"""

from __future__ import annotations

import multiprocessing
import os
import socket
import struct
import threading
import time
from pathlib import Path

from .contracts import DecisionError, MAX_BODY_BYTES, Request, canonical_json, fields, fingerprint, parse_json
from .engine import Scores

MAX_MESSAGE = MAX_BODY_BYTES + 16 * 1024
ERRORS = {"invalid_request", "context_too_long", "unsupported_tokenizer", "invalid_scores", "backend_error"}
CANCELLATION_POLL_SECONDS = 0.02


class _Cancelled(Exception):
    pass


def _check_cancelled(cancelled):
    if cancelled is not None and cancelled.is_set():
        raise _Cancelled()


def _timeout(sock, deadline, cancelled=None):
    _check_cancelled(cancelled)
    if deadline is None:
        sock.settimeout(None if cancelled is None else CANCELLATION_POLL_SECONDS)
    else:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Inference deadline expired")
        sock.settimeout(remaining if cancelled is None else min(remaining, CANCELLATION_POLL_SECONDS))


def _send(sock, value, deadline=None, cancelled=None):
    raw = canonical_json(value).encode("utf-8")
    if len(raw) > MAX_MESSAGE:
        raise ValueError("Oversized private message")
    frame = memoryview(struct.pack("!I", len(raw)) + raw)
    if cancelled is None:
        _timeout(sock, deadline)
        sock.sendall(frame)
        return
    while frame:
        _timeout(sock, deadline, cancelled)
        try:
            sent = sock.send(frame)
        except socket.timeout:
            continue  # The next iteration checks the absolute deadline and cancellation.
        if not sent:
            raise EOFError("Inference process disconnected")
        frame = frame[sent:]
    _check_cancelled(cancelled)


def _receive(sock, deadline=None, cancelled=None):
    def exact(length):
        parts = bytearray()
        while len(parts) < length:
            _timeout(sock, deadline, cancelled)
            try:
                part = sock.recv(length - len(parts))
            except socket.timeout:
                if cancelled is None:
                    raise
                continue
            if not part:
                raise EOFError("Inference process disconnected")
            parts.extend(part)
        return bytes(parts)
    length = struct.unpack("!I", exact(4))[0]
    if not 0 < length <= MAX_MESSAGE:
        raise ValueError("Invalid private message length")
    raw = exact(length)
    _check_cancelled(cancelled)
    return parse_json(raw)


def _child(sock, factory, config, owner_pid):
    stopped = threading.Event()

    def watch_parent():
        # Also retire an orphan after SIGKILL/OOM of the parent, including during load.
        # Normal shutdown closes the socket; the process never launches grandchildren.
        while not stopped.wait(0.25):
            if os.getppid() != owner_pid:
                os._exit(70)

    threading.Thread(target=watch_parent, daemon=True).start()
    try:
        try:
            backend = factory(config)
            identity = fingerprint(backend.identity)
            _send(sock, {"status": "ready", "identity": backend.identity})
        except Exception:
            _send(sock, {"status": "startup_failed"})
            return
        while True:
            raw = _receive(sock)
            request = Request.from_dict(raw)
            try:
                scores = backend.score(request)
                if fingerprint(backend.identity) != identity:
                    raise ValueError("Backend identity changed")
                reply = {"inputSha256": request.input_sha256, "logits": scores.logits, "inputTokens": scores.input_tokens}
            except DecisionError as error:
                reply = {"error": error.code if error.code in ERRORS else "backend_error"}
            except Exception:
                reply = {"error": "backend_error"}
            _send(sock, reply)
    except (EOFError, OSError, ValueError):
        pass  # No source text, traceback or exception message crosses this boundary.
    finally:
        stopped.set()
        sock.close()


def mlx_factory(config):
    from .mlx_backend import MlxBackend
    return MlxBackend(Path(config["manifest"]), config["max_tokens"], cache_limit_mib=config["cache_limit_mib"])


class IsolatedBackend:
    def __init__(self, factory, config, *, timeout_ms, startup_timeout_s=60):
        if (type(timeout_ms) is not int or not 100 <= timeout_ms <= 10000
                or type(startup_timeout_s) not in (int, float) or not 0 < startup_timeout_s <= 120):
            raise ValueError("Invalid isolated inference deadline")
        self.timeout_ms = timeout_ms
        self._lock = threading.Lock()
        self._available = False
        self._process = None
        self._diagnostics = {"childPid": None, "childExitCode": None, "stopReason": None}
        self._socket, child_socket = socket.socketpair()
        process = multiprocessing.get_context("spawn").Process(
            target=_child, args=(child_socket, factory, config, os.getpid()), daemon=True,
            name="agat-decision-inference")
        started = time.monotonic()
        try:
            process.start()
            self._process = process
            self._diagnostics["childPid"] = process.pid
            child_socket.close()
            hello = fields(_receive(self._socket, started + startup_timeout_s), {"status"}, {"identity"})
            if hello["status"] != "ready" or not isinstance(hello.get("identity"), dict):
                raise ValueError("Isolated model startup failed")
            self.identity = {**hello["identity"], "inferenceExecution": {
                "kind": "isolated-process", "startMethod": "spawn", "deadlineMs": timeout_ms,
                "timeoutAction": "stop_process_require_restart",
                "cancellationAction": "stop_process_require_restart", "cancellationPollIntervalMs": 20}}
            self.load_ms = round((time.monotonic() - started) * 1000, 3)
            self._available = True
        except BaseException as error:
            child_socket.close()
            self._stop("startup_failed")
            if isinstance(error, EOFError):
                raise RuntimeError("Isolated model exited during startup") from None
            raise

    def is_available(self):
        process = self._process
        try:
            return self._available and process is not None and process.is_alive()
        except ValueError:  # A simultaneous explicit close may already have released it.
            return False

    def diagnostics(self):
        return {**self._diagnostics, "available": self.is_available()}

    def _stop(self, reason):
        self._available = False
        if self._diagnostics["stopReason"] is None:
            self._diagnostics["stopReason"] = reason
        self._socket.close()
        process = self._process
        if process is None:
            return
        if process.is_alive():
            process.terminate()
            process.join(timeout=0.2)
        if process.is_alive():
            process.kill()
            process.join(timeout=0.5)
        if not process.is_alive():
            process.join(timeout=0)
            self._diagnostics["childExitCode"] = process.exitcode
            process.close()
            self._process = None

    def score(self, request):
        return self._score(request)

    def score_with_cancellation(self, request, cancelled):
        return self._score(request, cancelled)

    def _score(self, request, cancelled=None):
        if not self._lock.acquire(blocking=False):
            raise DecisionError("backend_unavailable", "Inference process is already in use")
        try:
            if not self.is_available():
                self._stop("backend_unavailable")
                raise DecisionError("backend_unavailable", "Restart the unavailable runtime")
            if cancelled is not None and cancelled.is_set():
                # Nothing was dispatched, so this healthy process can still be reused.
                raise DecisionError("inference_cancelled", "Request cancelled before inference")
            deadline = time.monotonic() + self.timeout_ms / 1000
            reported_error = False
            try:
                _send(self._socket, request.to_dict(), deadline, cancelled)
                result = _receive(self._socket, deadline, cancelled)
                _check_cancelled(cancelled)
                if time.monotonic() >= deadline:
                    raise TimeoutError("Late inference result")
                if isinstance(result, dict) and set(result) == {"error"} and result["error"] in ERRORS:
                    reported_error = True
                    if result["error"] == "backend_error":
                        self._stop("backend_error")
                    raise DecisionError(result["error"], "Isolated inference failed")
                fields(result, {"inputSha256", "logits", "inputTokens"})
                if (result["inputSha256"] != request.input_sha256 or type(result["inputTokens"]) is not int
                        or not 0 < result["inputTokens"] <= self.identity["maxInputTokens"]):
                    raise ValueError("Invalid isolated scoring response")
                # Validate finite values/count here as well as in the policy engine.
                from .contracts import probabilities
                probabilities(result["logits"], len(request.options))
                return Scores(result["logits"], result["inputTokens"])
            except _Cancelled:
                self._stop("inference_cancelled")
                raise DecisionError("inference_cancelled", "Inference process stopped; restart required") from None
            except TimeoutError:
                self._stop("inference_timeout")
                raise DecisionError("inference_timeout", "Inference process stopped; restart required") from None
            except DecisionError:
                if reported_error:
                    raise
                self._stop("backend_error")
                raise DecisionError("backend_error", "Invalid isolated inference response") from None
            except (OSError, EOFError, ValueError, KeyError, TypeError):
                self._stop("backend_error")
                raise DecisionError("backend_error", "Isolated inference process failed") from None
        finally:
            self._lock.release()

    def close(self):
        with self._lock:
            self._stop("closed")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
