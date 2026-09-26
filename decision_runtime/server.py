"""Development loopback endpoint. It does not grant workflow authority."""

from __future__ import annotations

import socket
import select
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .contracts import DecisionError, MAX_BODY_BYTES, canonical_json, fingerprint, parse_json
from .engine import DecisionEngine
from .metrics import CONTENT_TYPE as METRICS_CONTENT_TYPE, DecisionMetrics, outcome as metric_outcome

CANCEL_ON_DISCONNECT_HEADER = "X-Agat-Decision-Cancel-On-Disconnect"


def _watch_opted_in_peer(connection, done, cancelled):
    """The request explicitly defines peer EOF as cancellation; ordinary HTTP does not."""
    while not done.is_set():
        try:
            readable, _, _ = select.select([connection], [], [], 0.02)
            if done.is_set():
                return
            if readable:
                if not connection.recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT):
                    cancelled.set()
                    return
                # Pipelining is not supported. Do not consume any bytes belonging
                # to another request, or spin if extra bytes precede peer EOF.
                done.wait(0.02)
        except (BlockingIOError, socket.timeout):
            continue
        except OSError:
            if not done.is_set():
                cancelled.set()
            return


def make_server(engine: DecisionEngine, port: int = 8766, *, exit_on_backend_unavailable: bool = False) -> ThreadingHTTPServer:
    inference_lock = threading.Lock()
    backend_failed = threading.Event()
    metrics = DecisionMetrics()

    def available():
        return getattr(engine.backend, "is_available", lambda: True)()

    class DecisionHTTPServer(ThreadingHTTPServer):
        # A failed backend can be noticed while a handler is still constructing
        # its error response. server_close must drain that handler before exit.
        daemon_threads = not exit_on_backend_unavailable

        def service_actions(self):
            if exit_on_backend_unavailable and not backend_failed.is_set() and not available():
                backend_failed.set()
                # service_actions also runs during idle polls. shutdown must run
                # outside serve_forever's thread to avoid waiting on itself.
                threading.Thread(target=self.shutdown, name="decision-backend-shutdown", daemon=True).start()

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, _format, *_args):
            pass  # No state, question, model exception or request body in access logs.

        def reply(self, status, body):
            if getattr(self, "_measure_decision", False):
                self._decision_outcome = metric_outcome(body)
            encoded = canonical_json(body).encode("utf-8")
            self.reply_bytes(status, encoded, "application/json; charset=utf-8")

        def reply_bytes(self, status, encoded, content_type):
            self.close_connection = True
            try:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(encoded)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(encoded)
            except (BrokenPipeError, ConnectionResetError, socket.timeout):
                pass

        def local_request(self):
            host = self.headers.get("Host", "")
            allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            # Prevent a web page from using loopback inference through CORS / DNS rebinding.
            return host in allowed and not self.headers.get("Origin")

        def do_GET(self):
            if not self.local_request():
                return self.reply(403, engine.error("local_clients_only"))
            if self.path == "/metrics":
                return self.reply_bytes(200, metrics.render(ready=available()), METRICS_CONTENT_TYPE)
            if self.path != "/health":
                return self.reply(404, engine.error("not_found"))
            ready = available()
            self.reply(200 if ready else 503, {"status": "ready" if ready else "unavailable", "mode": "shadow", "model": engine.backend.identity,
                             "profileJson": canonical_json(engine.profile()), "profileSha256": fingerprint(engine.profile())})

        def do_POST(self):
            if self.path != "/v1/decisions":
                return self._decision_post()
            self._measure_decision = True
            self._decision_outcome = "backend_error"
            started = time.monotonic()
            metrics.begin()
            try:
                return self._decision_post()
            finally:
                metrics.finish(self._decision_outcome, time.monotonic() - started)
                self._measure_decision = False

        def _decision_post(self):
            if not self.local_request():
                return self.reply(403, engine.error("local_clients_only"))
            if self.path != "/v1/decisions":
                return self.reply(404, engine.error("not_found"))
            cancellation = self.headers.get_all(CANCEL_ON_DISCONNECT_HEADER, [])
            if cancellation not in ([], ["1"]):
                return self.reply(400, engine.error("invalid_request"))
            expected_profile = self.headers.get("X-Agat-Decision-Profile")
            if expected_profile is not None and expected_profile != fingerprint(engine.profile()):
                return self.reply(409, engine.error("profile_mismatch"))
            if self.headers.get_content_type() != "application/json":
                return self.reply(415, engine.error("invalid_content_type"))
            if self.headers.get("Transfer-Encoding") or len(self.headers.get_all("Content-Length", [])) != 1:
                return self.reply(400, engine.error("invalid_content_length"))
            try:
                length = int(self.headers["Content-Length"])
            except (ValueError, TypeError):
                return self.reply(400, engine.error("invalid_content_length"))
            if not 0 < length <= MAX_BODY_BYTES:
                return self.reply(413, engine.error("body_too_large"))
            if not inference_lock.acquire(blocking=False):
                return self.reply(503, engine.error("busy"))
            try:
                try:
                    raw = self.rfile.read(length)
                    if len(raw) != length:
                        return self.reply(400, engine.error("invalid_content_length"))
                    body = parse_json(raw)
                except socket.timeout:
                    return self.reply(408, engine.error("read_timeout"))
                except DecisionError:
                    return self.reply(400, engine.error("invalid_request"))
                if cancellation == ["1"] and callable(getattr(engine.backend, "score_with_cancellation", None)):
                    done, cancelled = threading.Event(), threading.Event()
                    watcher = threading.Thread(target=_watch_opted_in_peer, args=(self.connection, done, cancelled),
                                               name="decision-peer-cancellation", daemon=True)
                    watcher.start()
                    try:
                        result = engine.decide(body, cancelled=cancelled)
                    finally:
                        # Scope this signal to the current request before releasing
                        # the inference lock; it must never cancel a later lease.
                        done.set()
                        watcher.join(timeout=0.1)
                else:
                    result = engine.decide(body)
                status = 200
                if result["status"] == "error":
                    status = (504 if result["reason"] == "inference_timeout" else 400 if result["reason"] == "invalid_request" else 422
                              if result["reason"] in {"context_too_long", "calibration_out_of_scope"} else 500)
                self.reply(status, result)
            finally:
                inference_lock.release()

    server = DecisionHTTPServer(("127.0.0.1", port), Handler)
    server.backend_failed = backend_failed
    return server
