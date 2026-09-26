"""Bounded, opt-in loopback shadow inference. Never supplies the primary output."""

from __future__ import annotations

import http.client
import json
import math
import socket
import threading
import time
from urllib.parse import urlsplit

PROFILE = "local_decision_shadow_v2"
SUPPORTED_PROFILES = ("local_decision_shadow_v1", PROFILE)
MAX_RESPONSE = 64 * 1024


def validate_decision_url(url: str) -> str:
    parsed = urlsplit(url)
    # A literal address avoids DNS rebinding and http.client never uses proxies.
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in ("", "/")):
        raise ValueError("Decision URL must be http://127.0.0.1:<port> without a path")
    return url.rstrip("/")


def unavailable(reason: str) -> dict:
    return {"status": "unavailable", "reason": reason}


class LocalDecisionClient:
    def __init__(self, url: str):
        self.url = validate_decision_url(url)

    def decide(self, shadow: dict, cancelled: threading.Event | None = None) -> dict:
        """A watchdog closes the transport at a wall-clock deadline, including slow reads.

        A timeout discards the response. Isolated runtimes supporting the explicit
        disconnect opt-in stop their inference process; direct runtimes may finish later.
        A busy service is never queued or retried inside this lease attempt.
        """
        if cancelled is not None and cancelled.is_set():
            return unavailable("cancelled")
        try:
            timeout_ms = shadow["timeoutMs"]
            if (shadow.get("profile") not in SUPPORTED_PROFILES or type(timeout_ms) is not int
                    or not 100 <= timeout_ms <= 10_000):
                return unavailable("invalid_response")
            payload = json.dumps(shadow["request"], ensure_ascii=False, allow_nan=False).encode("utf-8")
            if len(payload) > 128 * 1024:
                return unavailable("invalid_response")
            profile_sha = shadow["profileSha256"]
            if not isinstance(profile_sha, str) or len(profile_sha) != 64 or any(c not in "0123456789abcdef" for c in profile_sha):
                return unavailable("invalid_response")
        except (KeyError, TypeError, ValueError, UnicodeError):
            return unavailable("invalid_response")

        parsed = urlsplit(self.url)
        deadline = time.monotonic() + timeout_ms / 1000
        connection = http.client.HTTPConnection("127.0.0.1", parsed.port, timeout=timeout_ms / 1000)
        done = threading.Event()
        interrupted = threading.Event()
        transport: list[socket.socket] = []

        def guard():
            while not done.wait(min(0.02, max(0.001, deadline - time.monotonic()))):
                if time.monotonic() >= deadline or (cancelled is not None and cancelled.is_set()):
                    interrupted.set()
                    for sock in transport:
                        try:
                            sock.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
                    connection.close()
                    return

        watchdog = threading.Thread(target=guard, daemon=True)
        watchdog.start()
        try:
            connection.connect()
            if connection.sock is not None:
                transport.append(connection.sock)
            if interrupted.is_set():
                return unavailable("cancelled" if cancelled and cancelled.is_set() else "timeout")
            connection.request("POST", "/v1/decisions", body=payload, headers={
                "Content-Type": "application/json", "Accept": "application/json",
                "X-Agat-Decision-Profile": profile_sha,
                # This client never half-closes while awaiting a response. EOF
                # therefore authorizes the isolated server to cancel this request.
                "X-Agat-Decision-Cancel-On-Disconnect": "1",
            })
            response = connection.getresponse()
            if response.status in (409, 503):
                return unavailable("profile_mismatch" if response.status == 409 else "busy")
            if response.status not in (200, 400, 422, 500, 504) or response.getheader("Content-Type", "").split(";")[0] != "application/json":
                return unavailable("invalid_response")
            data = response.read(MAX_RESPONSE + 1)
            if interrupted.is_set() or time.monotonic() >= deadline:
                return unavailable("cancelled" if cancelled and cancelled.is_set() else "timeout")
            if len(data) > MAX_RESPONSE:
                return unavailable("invalid_response")

            def strict_pairs(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("Duplicate key")
                    result[key] = value
                return result

            def finite_float(raw):
                value = float(raw)
                if not math.isfinite(value):
                    raise ValueError("Non-finite number")
                return value

            result = json.loads(data, object_pairs_hook=strict_pairs, parse_float=finite_float,
                                parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Invalid constant")))
            if not isinstance(result, dict):
                return unavailable("invalid_response")
            if interrupted.is_set() or time.monotonic() >= deadline:
                return unavailable("cancelled" if cancelled and cancelled.is_set() else "timeout")
            return {"result": result}
        except (socket.timeout, TimeoutError):
            return unavailable("timeout")
        except (OSError, http.client.HTTPException):
            if cancelled and cancelled.is_set():
                return unavailable("cancelled")
            return unavailable("timeout" if interrupted.is_set() else "unreachable")
        except (ValueError, UnicodeError, RecursionError):
            if cancelled and cancelled.is_set():
                return unavailable("cancelled")
            if interrupted.is_set() or time.monotonic() >= deadline:
                return unavailable("timeout")
            return unavailable("invalid_response")
        finally:
            done.set()
            connection.close()
            watchdog.join(timeout=0.1)
