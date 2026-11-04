"""Bounded service retirement diagnostics, independent of request contents."""

from __future__ import annotations

import re
import sys

from . import VERSION
from .contracts import canonical_json

RETIREMENT_REASONS = frozenset({
    "inference_timeout", "inference_cancelled", "backend_error", "backend_unavailable",
})


def retirement_event(profile_sha256, diagnostics):
    """Only allowlisted scalars cross into the service log; never exception text."""
    values = diagnostics if type(diagnostics) is dict else {}
    reason = values.get("stopReason")
    if type(reason) is not str or reason not in RETIREMENT_REASONS:
        # Idle child death is first reaped by close(), whose reason is "closed".
        reason = "backend_unavailable"

    def integer(name, lower, upper):
        value = values.get(name)
        return value if type(value) is int and lower <= value <= upper else None

    return {
        "schemaVersion": "agat.decision.retirement.v1",
        "eventName": "decision.backend_retired",
        "runtimeVersion": VERSION,
        "profileSha256": profile_sha256 if type(profile_sha256) is str
        and re.fullmatch(r"[0-9a-f]{64}", profile_sha256) else None,
        "exitCode": 75,
        "reason": reason,
        "childPid": integer("childPid", 1, 2**31 - 1),
        "childExitCode": integer("childExitCode", -255, 255),
    }


def log_retirement(backend, profile_sha256):
    """Called after HTTP draining and backend.close(); logging cannot undo exit 75."""
    try:
        diagnostics = backend.diagnostics()
    except Exception:
        diagnostics = None
    try:
        print(canonical_json(retirement_event(profile_sha256, diagnostics)), file=sys.stderr, flush=True)
    except (OSError, ValueError):
        pass  # A closed/full log sink must not trigger inference or change retirement.
