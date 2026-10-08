"""Prospective quarter-rate protocol; legacy arrival windows stay capped at 120 s."""
import time

from decision_runtime.contracts import fields, fingerprint, number
from scripts.lib.decision_arrival_rate import drive_arrivals
from scripts.lib.decision_shadow_pilot import require

SCHEMAS = tuple(f"agat.decision.public-support-load-{kind}.v3" for kind in ("plan", "result", "phase"))
SOURCE_PATHS = ["scripts/lib/decision_public_schedule.py", "scripts/test/test_decision_public_schedule.py"]


def lower_schedule(original, count):
    require(type(count) is int and 1 <= count <= 60, "Unsupported whole public inventory")
    expected = {"ratePerSecond": .5, "clientSlots": 1, "maxSchedulerLagMs": 100,
                "scheduledCases": count, "windowSeconds": count * 2, "callerTimeoutMs": 10000,
                "thresholdMs": 5000, "ineligibleCasesRetainedInInventory": True, "retries": 0}
    require(fingerprint(original) == fingerprint(expected), "Original context schedule differs")
    return {**original, "ratePerSecond": .25, "windowSeconds": count * 4}


def adjustment(original, count):
    lower_schedule(original, count)
    return {"reason": "prospective_lower_arrival_rate_probe", "originalScheduleSha256": fingerprint(original),
            "decisionRatePerSecond": .25, "primaryScheduledCases": count * 2, "maxWindowSeconds": 240}


def verify_plan(plan, context):
    fields(plan["scheduleAdjustment"], {"reason", "originalScheduleSha256", "decisionRatePerSecond",
                                       "primaryScheduledCases", "maxWindowSeconds"})
    count = len(context["inputs"])
    require(plan["schemaVersion"] == SCHEMAS[0]
            and fingerprint(plan["schedule"]) == fingerprint(lower_schedule(context["proposedDiagnosticSchedule"], count))
            and fingerprint(plan["scheduleAdjustment"]) == fingerprint(adjustment(context["proposedDiagnosticSchedule"], count)),
            "Prospective schedule binding differs")
    return count * 2


def drive_extended_arrivals(rate, count, slots, late_ms, dispatch, *, clock=time.monotonic,
                            sleep=time.sleep, cancelled=lambda: False, start=None):
    """Two bounded legacy segments share fixed global offsets, capacity and clock."""
    require(type(rate) in (int, float) and rate in (.25, .5) and type(count) is int and 1 <= count <= 120
            and count / rate <= 240 and type(slots) is int and slots == 1
            and type(late_ms) is int and late_ms == 100 and callable(dispatch), "Unsupported extended public schedule")
    origin = clock() if start is None else number(start, 0, 86_400_000_000)
    segment_count = int(120 * rate)
    for first in range(0, count, segment_count):
        offset = first / rate
        def forward(index, due, observed, reason):
            dispatch(first + index, offset + due, offset + observed, reason)
        drive_arrivals(rate, min(segment_count, count-first), slots, late_ms, forward,
                       clock=clock, sleep=sleep, cancelled=cancelled, start=origin+offset)
    return origin
