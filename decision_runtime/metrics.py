"""Small fixed-cardinality Prometheus exporter with no document-derived labels."""

from __future__ import annotations

import math
import threading
import time

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
OUTCOMES = ("ok", "abstain", "busy", "invalid", "profile_mismatch", "context_rejected",
            "backend_error", "timeout", "cancelled", "unavailable")
CLASSES = ("computed", "rejected", "failed")
BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0)


def outcome(result):
    """Map even unexpected backend errors to a bounded, source-free vocabulary."""
    if result.get("status") in ("ok", "abstain"):
        return result["status"]
    reason = result.get("reason")
    if not isinstance(reason, str):
        return "backend_error"
    return ({"busy": "busy", "profile_mismatch": "profile_mismatch",
             "context_too_long": "context_rejected", "calibration_out_of_scope": "context_rejected",
             "inference_timeout": "timeout", "read_timeout": "timeout",
             "inference_cancelled": "cancelled", "backend_unavailable": "unavailable",
             "invalid_request": "invalid", "invalid_content_type": "invalid", "invalid_content_length": "invalid",
             "body_too_large": "invalid", "local_clients_only": "invalid"}).get(reason, "backend_error")


def duration_class(value):
    return ("computed" if value in ("ok", "abstain") else "rejected"
            if value in ("busy", "invalid", "profile_mismatch", "context_rejected") else "failed")


class DecisionMetrics:
    """Per-server counters reset on restart; rendering never calls model inference."""

    def __init__(self):
        self._lock = threading.Lock()
        self._started = time.time()
        self._in_progress = 0
        self._outcomes = {value: 0 for value in OUTCOMES}
        self._histograms = {value: {"buckets": [0] * len(BUCKETS), "count": 0, "sum": 0.0} for value in CLASSES}

    def begin(self):
        with self._lock:
            self._in_progress += 1

    def finish(self, value, seconds):
        if value not in OUTCOMES or type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
            raise ValueError("Invalid internal observation")
        with self._lock:
            if self._in_progress <= 0:
                raise ValueError("No matching request start")
            self._in_progress -= 1
            self._outcomes[value] += 1
            histogram = self._histograms[duration_class(value)]
            histogram["count"] += 1
            histogram["sum"] += seconds
            for i, upper in enumerate(BUCKETS):
                if seconds <= upper:
                    histogram["buckets"][i] += 1

    def render(self, *, ready):
        # All names, labels and documentation are constants. Values are generated
        # locally; there is deliberately no API for user-provided label values.
        with self._lock:
            lines = [
                "# HELP agat_decision_backend_ready Whether the inference backend is operational; it may be busy.",
                "# TYPE agat_decision_backend_ready gauge",
                f"agat_decision_backend_ready {int(bool(ready))}",
                "# HELP agat_decision_requests_in_progress Decision HTTP handlers currently running, including body reads.",
                "# TYPE agat_decision_requests_in_progress gauge",
                f"agat_decision_requests_in_progress {self._in_progress}",
                "# HELP agat_decision_server_start_time_seconds Unix time when this HTTP server was created.",
                "# TYPE agat_decision_server_start_time_seconds gauge",
                f"agat_decision_server_start_time_seconds {self._started:.17g}",
                "# HELP agat_decision_requests_total Completed POST /v1/decisions handlers by logical outcome.",
                "# TYPE agat_decision_requests_total counter",
            ]
            lines += [f'agat_decision_requests_total{{outcome="{value}"}} {self._outcomes[value]}' for value in OUTCOMES]
            lines += [
                "# HELP agat_decision_request_duration_seconds Server HTTP handler duration; computed, rejected and failed requests are separate.",
                "# TYPE agat_decision_request_duration_seconds histogram",
            ]
            for value in CLASSES:
                histogram = self._histograms[value]
                for upper, count in zip(BUCKETS, histogram["buckets"]):
                    lines.append(f'agat_decision_request_duration_seconds_bucket{{class="{value}",le="{upper:g}"}} {count}')
                lines += [
                    f'agat_decision_request_duration_seconds_bucket{{class="{value}",le="+Inf"}} {histogram["count"]}',
                    f'agat_decision_request_duration_seconds_sum{{class="{value}"}} {histogram["sum"]:.17g}',
                    f'agat_decision_request_duration_seconds_count{{class="{value}"}} {histogram["count"]}',
                ]
        return ("\n".join(lines) + "\n").encode("utf-8")
