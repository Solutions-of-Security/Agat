"""Experimental idle retirement for production embedding sessions; no worker opt-in.

A pool has one maintenance thread. It retires only an unowned session after its
last completed exchange. The next caller starts a new helper through the normal
transport, with no internal retry and the original request deadline.
"""
from __future__ import annotations

import math
import queue
import threading
import time

from embedding_http import validate_timeout
from embedding_transport import EmbeddingSession, EmbeddingSessionPool, POLL_SECONDS


def _idle_timeout(value: float) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 3600:
        raise ValueError('Idle timeout must be finite, positive and at most 3600 seconds')
    return float(value)


class _IdleSession(EmbeddingSession):
    def __init__(self, idle_timeout: float) -> None:
        super().__init__()
        self._idle_timeout = idle_timeout
        self._idle_ownership = threading.Lock()
        self._idle_since: float | None = None
        self.idle_reaps = 0

    def _exchange(self, envelope: dict, timeout: float, cancelled: threading.Event | None,
                  max_response_bytes: int) -> bytes:
        validate_timeout(timeout)
        deadline = time.monotonic() + timeout

        def check() -> None:
            reason = self._reason(deadline, cancelled)
            if reason:
                raise RuntimeError(reason)

        check()
        # Retirement and admission own the same lock. Waiting for a retire is
        # part of the request's budget; cancellation is still observed here.
        while not self._idle_ownership.acquire(timeout=min(POLL_SECONDS, max(.001, deadline - time.monotonic()))):
            check()
        started = False
        try:
            check()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError('Embedding endpoint exceeded its deadline')
            self._idle_since = None
            started = True
            return super()._exchange(envelope, remaining, cancelled, max_response_bytes)
        finally:
            if started:
                self._idle_since = time.monotonic()
            self._idle_ownership.release()

    def expire_idle(self) -> bool:
        # Maintenance never waits for, or interrupts, an active exchange.
        if not self._idle_ownership.acquire(blocking=False):
            return False
        try:
            if (self._stopping.is_set() or self._process is None or self._idle_since is None
                    or time.monotonic() - self._idle_since < self._idle_timeout):
                return False
            if not self._lock.acquire(blocking=False):
                return False
            try:
                if self._stopping.is_set():
                    return False
                self._retire(force=False)
                self._idle_since = None
                self.idle_reaps += 1
                return True
            finally:
                self._lock.release()
        finally:
            self._idle_ownership.release()


class IdleEmbeddingSessionPool(EmbeddingSessionPool):
    """Qualification-only pool; timeout is explicit and independent of HTTP timeout."""
    def __init__(self, capacity: int, *, idle_timeout: float) -> None:
        timeout = _idle_timeout(idle_timeout)
        super().__init__(capacity)
        # Base construction is lazy; these original sessions have no resources.
        self._sessions = tuple(_IdleSession(timeout) for _ in range(capacity))
        self._available = queue.LifoQueue(maxsize=capacity)
        for session in self._sessions:
            self._available.put_nowait(session)
        self._maintenance_stop = threading.Event()
        self.maintenance_failure: str | None = None
        self._sweep_interval = min(1.0, max(.005, timeout / 4))
        self._maintenance = threading.Thread(target=self._maintain, name='embedding-idle-maintenance', daemon=True)
        try:
            self._maintenance.start()
        except Exception:
            super().close()
            raise

    def _maintain(self) -> None:
        try:
            while not self._maintenance_stop.wait(self._sweep_interval):
                for session in self._sessions:
                    if self._maintenance_stop.is_set():
                        return
                    session.expire_idle()
        except Exception as error:
            # Do not silently keep an unmaintained cache alive. The owner sees
            # closed admission and releases all resources in its normal close.
            self.maintenance_failure = type(error).__name__
            self._closed.set()
            for session in self._sessions:
                session._stopping.set()

    def close(self) -> None:
        self._maintenance_stop.set()
        try:
            super().close()
        finally:
            self._maintenance.join()
