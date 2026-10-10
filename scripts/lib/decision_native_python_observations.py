"""Scoped Python call observations for an owned, sequential native backend.

These records describe Python entries and host spans, never GPU kernels/time.
Install only in a disposable inference process, after its backend is loaded.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time


SCHEMA = "agat.decision.native-python-observation.v1"
_INSTRUMENTATION_LOCK = threading.Lock()


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


class ObservedNativeBackend:
    """Delegate scoring unchanged; temporarily tap the exact text-model instance.

    The process-wide lock rejects concurrent/nested observers. Other instances
    and threads delegate directly while the tap is installed. Each JSONL record
    is written immediately to an exclusively created 0600 file. A killed worker
    can leave an unfinished span; absence of a return record is not a completion.
    """

    def __init__(self, backend, event_path):
        self._backend = backend
        self._model = backend.text_model.model
        self._mx = backend.mx
        if not callable(self._model) or not callable(self._mx.eval):
            raise ValueError("Unsupported native Python call path")
        self._observer_sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        self.identity = {**deepcopy(backend.identity), "pythonCallObservation": {
            "schemaVersion": SCHEMA, "observerSourceSha256": self._observer_sha,
            "kind": "scoped-python-hooks", "gpuKernelsMeasured": False,
            "gpuTimeMeasured": False, "instrumentationOverheadExcluded": False}}
        self.load_ms = backend.load_ms
        self._fd = os.open(event_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        self._sequence = 0
        self._previous = None
        self._score_index = 0
        self._span_index = 0
        self._active = None
        try:
            self._write({"event": "header", "pid": os.getpid(),
                         "identity": self.identity, "clocks": {
                             "host": "perf_counter_ns", "threadCpu": "thread_time_ns"},
                         "capturesRequestText": False, "modelLoadObserved": False})
        except BaseException:
            self.close()
            raise

    def _write(self, body):
        if self._fd is None:
            raise ValueError("Observation journal is closed")
        record = {"schemaVersion": SCHEMA, "sequence": self._sequence,
                  "previousSha256": self._previous, **body}
        record["sha256"] = hashlib.sha256(_canonical(record)).hexdigest()
        remaining = memoryview(_canonical(record) + b"\n")
        while remaining:
            written = os.write(self._fd, remaining)
            if written <= 0:
                raise OSError("Observation journal write failed")
            remaining = remaining[written:]
        self._sequence += 1
        self._previous = record["sha256"]

    def _span(self, kind, delegate, *args, **kwargs):
        self._span_index += 1
        binding = {**self._active, "spanId": self._span_index, "kind": kind}
        host_start = time.perf_counter_ns()
        cpu_start = time.thread_time_ns()
        self._write({**binding, "event": "call_start", "hostNs": host_start,
                     "threadCpuNs": cpu_start})
        status = "returned"
        exception_type = None
        try:
            return delegate(*args, **kwargs)
        except BaseException as error:
            status = "raised"
            exception_type = type(error).__name__
            raise
        finally:
            host_end = time.perf_counter_ns()
            cpu_end = time.thread_time_ns()
            self._write({**binding, "event": "call_end", "status": status,
                         "exceptionType": exception_type, "hostNs": host_end,
                         "threadCpuNs": cpu_end, "hostElapsedNs": host_end - host_start,
                         "threadCpuElapsedNs": cpu_end - cpu_start})

    def _with_hooks(self, request):
        model = self._model
        model_class = type(model)
        original_call = model_class.__call__
        had_own_call = "__call__" in model_class.__dict__
        original_eval = self._mx.eval
        owner_thread = self._active["threadId"]

        def model_call(instance, *args, **kwargs):
            if instance is model and threading.get_ident() == owner_thread:
                return self._span("text_backbone", original_call, instance, *args, **kwargs)
            return original_call(instance, *args, **kwargs)

        def evaluate(*args, **kwargs):
            if threading.get_ident() == owner_thread:
                return self._span("mx_eval", original_eval, *args, **kwargs)
            return original_eval(*args, **kwargs)

        model_class.__call__ = model_call
        try:
            self._mx.eval = evaluate
            try:
                return self._backend.score(request)
            finally:
                self._mx.eval = original_eval
        finally:
            if had_own_call:
                model_class.__call__ = original_call
            else:
                delattr(model_class, "__call__")
            self._write({**self._active, "event": "hooks_restored",
                         "modelObjectPreserved": self._backend.text_model.model is model,
                         "modelCallRestored": model_class.__call__ is original_call,
                         "evalRestored": self._mx.eval is original_eval})

    def score(self, request):
        input_sha = request.input_sha256
        if not isinstance(input_sha, str) or re.fullmatch(r"[0-9a-f]{64}", input_sha) is None:
            raise ValueError("A canonical input SHA is required")
        if self._fd is None:
            raise ValueError("Observation journal is closed")
        if not _INSTRUMENTATION_LOCK.acquire(blocking=False):
            raise RuntimeError("Concurrent or nested Python call observation")
        self._score_index += 1
        self._active = {"scoreId": self._score_index, "inputSha256": input_sha,
                        "threadId": threading.get_ident(), "pid": os.getpid()}
        try:
            return self._span("backend_score", self._with_hooks, request)
        finally:
            self._active = None
            _INSTRUMENTATION_LOCK.release()

    def close(self):
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
