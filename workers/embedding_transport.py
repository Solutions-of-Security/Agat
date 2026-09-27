"""Owned embedding helper used by the explicit worker session transport.

Each session owns one process and admits one request at a time. Its caller must
close the session. Cancellation discards this process and both private pipes;
there is no retry, shared queue, or promise of stopping backend computation.
"""
from __future__ import annotations

import json
import queue
from pathlib import Path
import struct
import subprocess
import sys
import threading
import time
from typing import BinaryIO

from embedding_http import _fetch, validate_timeout

MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_ERROR_BYTES = 4096
MAX_DIAGNOSTIC_BYTES = 4800
POLL_SECONDS = 0.02


def _limits(response: int, error: int) -> None:
    if type(response) is not int or not 1 <= response <= MAX_RESPONSE_BYTES:
        raise ValueError('Invalid embedding response byte limit')
    if type(error) is not int or not 1 <= error <= MAX_ERROR_BYTES:
        raise ValueError('Invalid embedding error byte limit')


def _read_exact(stream: BinaryIO, size: int, *, allow_eof: bool = False) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = stream.read(size - len(chunks))
        if not chunk:
            if allow_eof and not chunks:
                return b''
            raise EOFError('Incomplete embedding helper frame')
        chunks.extend(chunk)
    return bytes(chunks)


def _write_all(stream: BinaryIO, data: bytes) -> None:
    view = memoryview(data)
    while view:
        size = stream.write(view)
        if not size:
            raise BrokenPipeError('Embedding helper pipe stopped accepting data')
        view = view[size:]
    stream.flush()


def _read_frame(stream: BinaryIO, maximum: int, *, allow_eof: bool = False) -> bytes:
    header = _read_exact(stream, 8, allow_eof=allow_eof)
    if not header:
        return b''
    size, = struct.unpack('!Q', header)
    if not 9 <= size <= maximum:
        raise RuntimeError('Invalid embedding helper frame length')
    return _read_exact(stream, size)


def _write_frame(stream: BinaryIO, body: bytes) -> None:
    _write_all(stream, struct.pack('!Q', len(body)))
    _write_all(stream, body)


class EmbeddingSession:
    """Explicitly owned, serial transport; use multiple sessions for concurrency."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stopping = threading.Event()
        self._process: subprocess.Popen | None = None
        self._sequence = 0
        self.starts = 0
        self.reaps = 0

    def __enter__(self) -> EmbeddingSession:
        if self._stopping.is_set():
            raise RuntimeError('Embedding session is closed')
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    @property
    def process_id(self) -> int | None:
        """Diagnostic snapshot, not a lease or an ownership guarantee."""
        process = self._process
        return process.pid if process is not None else None

    def _reason(self, deadline: float, cancelled: threading.Event | None) -> str | None:
        if self._stopping.is_set():
            return 'Embedding session is closed'
        if cancelled is not None and cancelled.is_set():
            return 'Embedding request cancelled'
        if time.monotonic() >= deadline:
            return 'Embedding endpoint exceeded its deadline'
        return None

    def _retire(self, *, force: bool) -> None:
        process = self._process
        if process is None:
            return
        try:
            if force and process.poll() is None:
                process.kill()
            # Raw unbuffered pipes: close cannot wait on a buffered write.
            process.stdin.close()
            try:
                process.wait(timeout=0.25)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        finally:
            process.stdout.close()
            self._process = None
            self.reaps += 1

    def close(self) -> None:
        # An active guard observes this before close waits for ownership.
        self._stopping.set()
        with self._lock:
            self._retire(force=False)

    def warmup(self, *, timeout: float = 5) -> None:
        self._exchange({'operation': 'ping'}, timeout, None, MAX_DIAGNOSTIC_BYTES)

    def request(self, url: str, payload: dict, headers: dict[str, str], *, timeout: float,
                cancelled: threading.Event | None = None,
                max_response_bytes: int = MAX_RESPONSE_BYTES,
                max_error_bytes: int = MAX_ERROR_BYTES) -> bytes:
        _limits(max_response_bytes, max_error_bytes)
        return self._exchange({'operation': 'request', 'request': {
            'url': url, 'payload': payload, 'headers': headers, 'timeout': timeout,
            'maxResponseBytes': max_response_bytes, 'maxErrorBytes': max_error_bytes,
        }}, timeout, cancelled, max_response_bytes)

    def _exchange(self, envelope: dict, timeout: float, cancelled: threading.Event | None,
                  max_response_bytes: int) -> bytes:
        validate_timeout(timeout)
        deadline = time.monotonic() + timeout

        def check() -> None:
            reason = self._reason(deadline, cancelled)
            if reason:
                raise RuntimeError(reason)

        check()
        request = json.dumps(envelope, ensure_ascii=False, allow_nan=False).encode('utf-8')
        if len(request) + 8 > MAX_REQUEST_BYTES:
            raise ValueError('Embedding helper request exceeds 2 MiB')
        check()
        # A queued cancellation never touches the current owner's helper.
        while not self._lock.acquire(timeout=min(POLL_SECONDS, max(0.001, deadline - time.monotonic()))):
            check()
        try:
            check()
            if self._process is not None and self._process.poll() is not None:
                self._retire(force=True)
            if self._process is None:
                self._process = subprocess.Popen(
                    [sys.executable, '-u', str(Path(__file__).resolve()), '--serve'],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, bufsize=0)
                self.starts += 1
            process = self._process
            self._sequence += 1
            identity = struct.pack('!Q', self._sequence)
            done = threading.Event()
            interruption: list[str] = []

            def guard() -> None:
                while not done.is_set():
                    reason = self._reason(deadline, cancelled)
                    if reason:
                        interruption.append(reason)
                        try:
                            process.kill()
                        except ProcessLookupError:
                            pass
                        return
                    done.wait(min(POLL_SECONDS, max(0.001, deadline - time.monotonic())))

            watcher = threading.Thread(target=guard, name='embedding-session-deadline', daemon=True)
            synchronized = False
            started = False
            try:
                watcher.start()
                started = True
                _write_frame(process.stdin, identity + request)
                response = _read_frame(process.stdout, max(max_response_bytes, MAX_DIAGNOSTIC_BYTES) + 9)
                # Join before accepting a reply or handing the process to another caller.
                done.set()
                watcher.join()
                if interruption:
                    raise RuntimeError(interruption[0])
                check()
                if response[:8] != identity or response[8:9] not in (b'S', b'E'):
                    raise RuntimeError('Invalid embedding helper response identity or kind')
                kind, body = response[8:9], response[9:]
                if len(body) > (max_response_bytes if kind == b'S' else MAX_DIAGNOSTIC_BYTES):
                    raise RuntimeError('Embedding helper response exceeds byte limit')
                if envelope['operation'] == 'ping' and kind == b'S' and body != b'ready-v1':
                    raise RuntimeError('Invalid embedding helper readiness reply')
                synchronized = True
                if kind == b'E':
                    raise RuntimeError(body.decode('utf-8', errors='replace'))
                return body
            except (OSError, EOFError) as error:
                if interruption:
                    raise RuntimeError(interruption[0]) from error
                check()
                raise RuntimeError('Embedding transport subprocess failed') from error
            finally:
                done.set()
                if started:
                    watcher.join()
                if not synchronized or process.poll() is not None:
                    self._retire(force=True)
        finally:
            self._lock.release()



class EmbeddingSessionPool:
    """Bounded lazy helpers, owned by one worker and closed after its lease drain.

    An idle session is reused first (LIFO), so sequential work starts one helper
    even when the worker permits more concurrency. No per-thread cache grows.
    """

    def __init__(self, capacity: int) -> None:
        if type(capacity) is not int or not 1 <= capacity <= 32:
            raise ValueError('Embedding session capacity must be between 1 and 32')
        self._closed = threading.Event()
        self._sessions = tuple(EmbeddingSession() for _ in range(capacity))
        self._available: queue.LifoQueue[EmbeddingSession] = queue.LifoQueue(maxsize=capacity)
        for session in self._sessions:
            self._available.put_nowait(session)

    def __enter__(self) -> EmbeddingSessionPool:
        if self._closed.is_set():
            raise RuntimeError('Embedding transport is closed')
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def close(self) -> None:
        self._closed.set()
        # Signal every active guard before waiting for any individual session.
        for session in self._sessions:
            session._stopping.set()
        for session in self._sessions:
            session.close()

    def request(self, url: str, payload: dict, headers: dict[str, str], *, timeout: float,
                cancelled: threading.Event | None = None,
                max_response_bytes: int = MAX_RESPONSE_BYTES,
                max_error_bytes: int = MAX_ERROR_BYTES) -> bytes:
        validate_timeout(timeout)
        deadline = time.monotonic() + timeout

        def remaining() -> float:
            if self._closed.is_set():
                raise RuntimeError('Embedding transport is closed')
            if cancelled is not None and cancelled.is_set():
                raise RuntimeError('Embedding request cancelled')
            value = deadline - time.monotonic()
            if value <= 0:
                raise RuntimeError('Embedding endpoint exceeded its deadline')
            return value

        while True:
            try:
                session = self._available.get(timeout=min(POLL_SECONDS, remaining()))
                break
            except queue.Empty:
                continue
        try:
            return session.request(url, payload, headers, timeout=remaining(), cancelled=cancelled,
                                   max_response_bytes=max_response_bytes, max_error_bytes=max_error_bytes)
        finally:
            self._available.put_nowait(session)


def _serve_one(frame: bytes) -> bytes:
    identity, raw = frame[:8], frame[8:]
    try:
        envelope = json.loads(raw)
        if envelope['operation'] == 'ping':
            body = b'ready-v1'
        elif envelope['operation'] == 'request':
            request = envelope['request']
            validate_timeout(request['timeout'])
            _limits(request['maxResponseBytes'], request['maxErrorBytes'])
            body = _fetch(request)
        else:
            raise ValueError('Unknown embedding helper operation')
        return identity + b'S' + body
    except Exception as error:
        return identity + b'E' + str(error)[:1200].encode('utf-8', errors='replace')


def main() -> None:
    while True:
        frame = _read_frame(sys.stdin.buffer, MAX_REQUEST_BYTES, allow_eof=True)
        if not frame:
            return
        response = _serve_one(frame)
        _write_frame(sys.stdout.buffer, response)
        # Do not retain the last request's source/token or response while idle.
        del frame, response


if __name__ == '__main__':
    if sys.argv[1:] != ['--serve']:
        raise SystemExit('This transport module is not a worker entry point')
    main()
