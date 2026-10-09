"""Bounded worker HTTP in disposable owned processes."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request


def parent_watch_arguments() -> list[str]:
    # Unix changes PPID after reparenting; Windows retains the original number.
    return ["--parent-pid", str(os.getpid())] if os.name == "posix" else []


def watch_parent(arguments: list[str]) -> None:
    """Install a child-only Unix guard before accepting any model request.

    The expected PID comes from the spawning worker, not getppid() at startup:
    the owner may already have exited while this interpreter was importing.
    No preexec_fn runs in the multithreaded worker. Only this disposable helper
    exits on owner loss; it never signals another process or submits a result.
    """
    if not arguments:
        return
    if (os.name != "posix" or len(arguments) != 2 or arguments[0] != "--parent-pid"
            or not arguments[1].isascii() or not arguments[1].isdecimal()
            or len(arguments[1]) > 10 or not 0 < int(arguments[1]) <= 2**31 - 1):
        raise SystemExit("Invalid embedding helper parent binding")
    expected = int(arguments[1])
    if os.getppid() != expected:
        raise SystemExit("Embedding helper parent exited before startup")

    def guard() -> None:
        while os.getppid() == expected:
            time.sleep(0.05)
        # The owner cannot consume this response or reap the helper. Exit even
        # if urllib or stdout is blocked, closing only our private descriptors.
        # The adopting init/subreaper is responsible for collecting exit status.
        os._exit(1)

    threading.Thread(target=guard, name="embedding-parent-watch", daemon=True).start()


class HttpResponseError(RuntimeError):
    def __init__(self, status: int, detail: str, endpoint_name: str) -> None:
        super().__init__(f"{endpoint_name} endpoint returned HTTP {status}: {detail[:1000]}")
        self.status, self.detail = status, detail


def validate_timeout(timeout: float, *, endpoint_name: str = "Embedding") -> None:
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout) or not 0 < timeout <= 900):
        raise ValueError(f"{endpoint_name} timeout must be finite, positive and at most 900 seconds")


def request_embedding_response(url: str, payload: dict, headers: dict[str, str], *, timeout: float,
                               cancelled: threading.Event | None, max_response_bytes: int,
                               max_error_bytes: int) -> bytes:
    return _request_response(url, payload, headers, timeout=timeout, cancelled=cancelled,
                             max_response_bytes=max_response_bytes, max_error_bytes=max_error_bytes,
                             endpoint_name="Embedding")


def request_model_response(url: str, payload: dict, headers: dict[str, str], *, timeout: float,
                           cancelled: threading.Event | None, max_response_bytes: int,
                           max_error_bytes: int) -> bytes:
    return _request_response(url, payload, headers, timeout=timeout, cancelled=cancelled,
                             max_response_bytes=max_response_bytes, max_error_bytes=max_error_bytes,
                             endpoint_name="Model")


def request_knowledge_response(url: str, payload: dict, headers: dict[str, str], *, timeout: float,
                               cancelled: threading.Event | None, max_response_bytes: int,
                               max_error_bytes: int) -> bytes:
    return _request_response(url, payload, headers, timeout=timeout, cancelled=cancelled,
                             max_response_bytes=max_response_bytes, max_error_bytes=max_error_bytes,
                             endpoint_name="Knowledge")


def request_coordinator_response(url: str, payload: dict, headers: dict[str, str], *, timeout: float,
                                cancelled: threading.Event | None, max_response_bytes: int,
                                max_error_bytes: int) -> bytes:
    return _request_response(url, payload, headers, timeout=timeout, cancelled=cancelled,
                             max_response_bytes=max_response_bytes, max_error_bytes=max_error_bytes,
                             endpoint_name="Coordinator")


def _request_response(url: str, payload: dict, headers: dict[str, str], *, timeout: float,
                      cancelled: threading.Event | None, max_response_bytes: int,
                      max_error_bytes: int, endpoint_name: str) -> bytes:
    """Bound DNS, connect, redirects and response reads without orphaning a thread.

    Only the helper's private pipes are discarded on cancellation. No request is
    retried here, and disconnect does not promise cancellation of backend compute.
    """
    validate_timeout(timeout, endpoint_name=endpoint_name)
    deadline = time.monotonic() + timeout

    def check_deadline() -> None:
        if cancelled is not None and cancelled.is_set():
            raise RuntimeError(f"{endpoint_name} request cancelled")
        if time.monotonic() >= deadline:
            raise RuntimeError(f"{endpoint_name} endpoint exceeded its {timeout:g}s deadline")

    check_deadline()
    # Credentials and source text stay off the command line and filesystem.
    pending = json.dumps({"url": url, "payload": payload, "headers": headers,
                          "timeout": timeout, "maxResponseBytes": max_response_bytes,
                          "maxErrorBytes": max_error_bytes, "endpointName": endpoint_name}, ensure_ascii=False).encode("utf-8")
    check_deadline()
    with subprocess.Popen([sys.executable, "-u", str(Path(__file__).resolve()), *parent_watch_arguments()],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL) as process:
        try:
            while True:
                check_deadline()
                try:
                    output, _ = process.communicate(pending, timeout=min(0.05, max(0.001, deadline - time.monotonic())))
                except subprocess.TimeoutExpired:
                    # communicate retains partially written input and output.
                    pending = None
                    continue
                check_deadline()
                if process.returncode != 0 or output[:1] not in (b"S", b"E", b"H"):
                    raise RuntimeError(f"{endpoint_name} transport subprocess failed")
                if output[:1] == b"H":
                    error = json.loads(output[1:])
                    raise HttpResponseError(error["status"], error["detail"], endpoint_name)
                if output[:1] == b"E":
                    raise RuntimeError(output[1:].decode("utf-8", errors="replace"))
                return output[1:]
        finally:
            if process.poll() is None:
                # The helper owns no shared locks, queues, files or descendants.
                # Killing it closes even a DNS/TLS/header/body wait; reap it before
                # allowing the worker's lease slot to be reused.
                process.kill()
                process.communicate()


def _fetch(request: dict) -> bytes:
    endpoint_name = request.get("endpointName", "Embedding")
    http_request = urllib.request.Request(request["url"],
        data=json.dumps(request["payload"], ensure_ascii=False).encode("utf-8"),
        headers=request["headers"], method="POST")
    try:
        with urllib.request.urlopen(http_request, timeout=request["timeout"]) as response:
            body = response.read(request["maxResponseBytes"] + 1)
        if len(body) > request["maxResponseBytes"]:
            raise RuntimeError(f"{endpoint_name} endpoint response exceeds {request['maxResponseBytes']} bytes")
        return body
    except urllib.error.HTTPError as error:
        with error:
            detail = error.read(request["maxErrorBytes"]).decode("utf-8", errors="replace")
        raise HttpResponseError(error.code, detail, endpoint_name) from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"{endpoint_name} endpoint unavailable: {error.reason}") from error


def main() -> None:
    try:
        request = json.load(sys.stdin)
        output = b"S" + _fetch(request)
    except HttpResponseError as error:
        # Keep the embedding/session string protocol stable. Primary tools need
        # the HTTP status without parsing a human-readable error message. Knowledge
        # requests also retain the bounded JSON error body for the coordinator API.
        output = (b"H" + json.dumps({"status": error.status, "detail": error.detail}).encode("utf-8")
                  if request.get("endpointName") in {"Model", "Knowledge", "Coordinator"}
                  else b"E" + str(error)[:1200].encode("utf-8", errors="replace"))
    except Exception as error:
        output = b"E" + str(error)[:1200].encode("utf-8", errors="replace")
    sys.stdout.buffer.write(output)
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    watch_parent(sys.argv[1:])
    main()
