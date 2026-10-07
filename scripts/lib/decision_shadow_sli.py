"""Descriptive caller SLIs over provided coordinator traces; no SLO adoption."""
from __future__ import annotations

import hashlib
import math
import re
from decision_runtime.contracts import fingerprint, parse_json
from scripts.lib.decision_stage_inventory import census
from scripts.lib.decision_caller_inventory import census as caller_census

TIMING_SCHEMA = "agat.decision.caller-timing.v1"


def require(value, message):
    if not value: raise ValueError(message)


def duration(value):
    require(type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 86_400_000, "Invalid caller duration")
    return float(value)


def timing(raw):
    require(isinstance(raw, dict) and set(raw) == {"schemaVersion", "clock", "boundary", "durationMs"}, "Invalid caller timing fields")
    require(raw["schemaVersion"] == TIMING_SCHEMA and raw["clock"] == "monotonic" and raw["boundary"] == "local_http_call", "Caller timing identity differs")
    return duration(raw["durationMs"])


def quantiles(values):
    ordered = sorted(values)
    return {"count": len(ordered), **{name: ordered[max(0, math.ceil(len(ordered)*ratio)-1)] if ordered else None
                                     for name, ratio in (("p50", .5), ("p95", .95), ("max", 1))}}


def same_json(expected, actual):
    # Coordinator JSON preserves binary64 values but writes 1.0 as 1. Booleans remain distinct.
    if isinstance(expected, dict):
        return isinstance(actual, dict) and set(expected) == set(actual) and all(same_json(v, actual[k]) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(expected) == len(actual) and all(same_json(a, b) for a, b in zip(expected, actual))
    if type(expected) in (int, float):
        return type(actual) in (int, float) and math.isfinite(actual) and expected == actual
    return type(expected) is type(actual) and expected == actual


def analyze(traces, profile, latency_threshold_ms, *, profile_json_bytes=None):
    require(isinstance(profile, dict) and set(profile) in ({"schemaVersion", "runtimeVersion", "model", "policy", "calibration"},
             {"schemaVersion", "runtimeVersion", "model", "policy", "calibration", "inputFingerprintVersions"}), "Invalid expected profile fields")
    profile_sha256 = fingerprint(profile)
    if profile_json_bytes is not None:
        require(type(profile_json_bytes) is bytes and 0 < len(profile_json_bytes) <= 1024*1024,
                "Use bounded UTF-8 coordinator profile bytes")
        parsed = parse_json(profile_json_bytes.decode("utf-8"))
        require(same_json(profile, parsed), "Profile JSON bytes differ from parsed profile")
        profile_sha256 = hashlib.sha256(profile_json_bytes).hexdigest()
    require(isinstance(profile_sha256, str) and re.fullmatch(r"[a-f0-9]{64}", profile_sha256), "Pin the expected profile SHA")
    require(type(latency_threshold_ms) is int and 1 <= latency_threshold_ms <= 86_400_000, "Invalid latency threshold")
    require(isinstance(traces, list) and 1 <= len(traces) <= 32, "Use one to 32 complete trace inputs")
    inventory = census(traces, profile_sha256)
    assigned_bound = assigned_unknown_bound = assigned_timely = assigned_unknown_timely = 0
    counts = {"observedChecks": 0, "reusedChecks": 0, "safeReplayUnavailable": 0, "boundResults": 0,
              "unassignedChecks": 0, "missingLeaseBindings": 0, "boundResultsWithinCallerDeadline": 0,
              "lateBoundResults": 0, "boundResultsWithUnknownCallerDeadline": 0,
              "ok": 0, "abstain": 0, "error": 0, "unavailable": 0, "knownCallerTimings": 0,
              "missingCallerTimings": 0, "profileBindingMismatches": 0, "timelyBoundResults": 0,
              "boundResultsWithUnknownTimeliness": 0, "truncatedTraces": 0}
    seen = set(); latencies = []; scoring = []
    for trace in traces:
        require(isinstance(trace, dict) and isinstance(trace.get("run"), dict), "Invalid coordinator trace")
        run_id = trace["run"].get("id")
        require(isinstance(run_id, str) and 0 < len(run_id) <= 200, "Invalid trace run identity")
        require(type(trace.get("truncated")) is bool, "Trace completeness marker is missing")
        counts["truncatedTraces"] += int(trace["truncated"])
        rows = trace.get("decisionObservations")
        require(isinstance(rows, list) and len(rows) <= 10_000, "Invalid decision observation inventory")
        for row in rows:
            require(isinstance(row, dict) and isinstance(row.get("stageId"), str) and 0 < len(row["stageId"]) <= 200, "Invalid stage identity")
            identity = (run_id, row["stageId"])
            require(identity not in seen, "Duplicate run/stage observation across provided traces")
            seen.add(identity)
            observation = row.get("observation")
            require(isinstance(observation, dict) and observation.get("mode") == "shadow" and observation.get("fallback") == "primary", "Invalid shadow observation")
            status = observation.get("status")
            require(status in ("ok", "abstain", "error", "unavailable") and isinstance(observation.get("reason"), str), "Unknown observation status/reason")
            if "reusedFromStageId" in observation:
                require(isinstance(observation["reusedFromStageId"], str) and observation["reusedFromStageId"], "Invalid replay provenance")
                counts["reusedChecks"] += 1
                continue
            if status == "unavailable" and observation["reason"] == "safe_replay_unavailable":
                counts["safeReplayUnavailable"] += 1
                continue
            if status == "unavailable" and observation["reason"] in ("disabled", "unsupported_worker", "invalid_input") and row.get("profileSha256") is None:
                require(row.get("inputSha256") is None and row.get("callerTimeoutMs") is None and "callerTiming" not in observation, "Unassigned check has caller evidence")
                counts["unassignedChecks"] += 1
                continue
            counts["observedChecks"] += 1; counts[status] += 1
            observed_profile_sha = row.get("profileSha256")
            require(observed_profile_sha is None or isinstance(observed_profile_sha, str) and re.fullmatch(r"[a-f0-9]{64}", observed_profile_sha), "Invalid observation profile SHA")
            matching = observed_profile_sha == profile_sha256
            input_sha = row.get("inputSha256")
            require(input_sha is None or isinstance(input_sha, str) and re.fullmatch(r"[a-f0-9]{64}", input_sha), "Invalid lease input SHA")
            caller_timeout = row.get("callerTimeoutMs")
            require(caller_timeout is None or type(caller_timeout) is int and 100 <= caller_timeout <= 10_000, "Invalid lease caller timeout")
            counts["missingLeaseBindings"] += int(input_sha is None or caller_timeout is None)
            caller_ms = timing(observation["callerTiming"]) if "callerTiming" in observation else None
            if caller_ms is None: counts["missingCallerTimings"] += 1
            else:
                latencies.append(caller_ms); counts["knownCallerTimings"] += 1
            if status in ("ok", "abstain", "error"):
                result = observation.get("result")
                require(isinstance(result, dict) and result.get("mode") == "shadow" and result.get("status") == status
                        and result.get("reason") == observation["reason"], "Stored result/observation semantics differ")
                scoring.append(duration(result.get("durationMs")))
                matching = matching and result.get("id") == row["stageId"] and all(key in result and same_json(value, result[key]) for key, value in profile.items())
                matching = matching and (input_sha is None or result.get("inputSha256") == input_sha)
            counts["profileBindingMismatches"] += int(not matching)
            good = matching and status in ("ok", "abstain")
            counts["boundResults"] += int(good)
            within_deadline = good and caller_ms is not None and caller_timeout is not None and caller_ms <= caller_timeout
            counts["boundResultsWithinCallerDeadline"] += int(within_deadline)
            counts["lateBoundResults"] += int(good and caller_ms is not None and caller_timeout is not None and caller_ms > caller_timeout)
            counts["boundResultsWithUnknownCallerDeadline"] += int(good and (caller_ms is None or caller_timeout is None))
            counts["timelyBoundResults"] += int(within_deadline and caller_ms <= latency_threshold_ms)
            counts["boundResultsWithUnknownTimeliness"] += int(good and (caller_ms is None or caller_timeout is None)
                                                               and (caller_ms is None or caller_ms <= latency_threshold_ms))
            if identity in inventory.assigned_recorded:
                complete_binding = input_sha is not None and caller_timeout is not None
                assigned_bound += int(good and complete_binding)
                assigned_unknown_bound += int(good and not complete_binding)
                assigned_timely += int(within_deadline and complete_binding and caller_ms <= latency_threshold_ms)
                assigned_unknown_timely += int(good and (not complete_binding or caller_ms is None)
                                              and (caller_ms is None or caller_ms <= latency_threshold_ms)
                                              and (caller_ms is None or caller_timeout is None or caller_ms <= caller_timeout))
    total = counts["observedChecks"]
    reasons = []
    for condition, name in ((total == 0, "no_observed_checks"), (counts["missingCallerTimings"] > 0, "missing_caller_timings"),
                            (counts["missingLeaseBindings"] > 0, "missing_lease_bindings"),
                            (counts["profileBindingMismatches"] > 0, "profile_binding_mismatch"), (counts["truncatedTraces"] > 0, "truncated_trace")):
        if condition: reasons.append(name)
    reasons.extend(inventory.gaps)
    stage_total = inventory.counts["assignedStoredStages"]
    pending = inventory.counts["pendingAssignedStages"]
    stage_summary = {
        "scope": "provided_stored_shadow_stages", "counts": inventory.counts,
        "storedStageCoverageVerified": inventory.counts["tracesMissingInventory"] == 0 and counts["truncatedTraces"] == 0,
        "inventoryBindingsVerified": inventory.counts["missingInventoryBindings"] == 0 and inventory.counts["inventoryProfileMismatches"] == 0,
        "httpAttemptInventoryVerified": False,
        "boundAssignedResults": assigned_bound, "unknownBoundAssignedResults": assigned_unknown_bound,
        "timelyAssignedResults": assigned_timely, "unknownTimelyAssignedResults": assigned_unknown_timely,
        "assignedStageBoundResultRatio": {"lower": assigned_bound/stage_total if stage_total else None,
                                          "upper": (assigned_bound+assigned_unknown_bound+pending)/stage_total if stage_total else None},
        "assignedStageTimelyResultRatio": {"lower": assigned_timely/stage_total if stage_total else None,
                                           "upper": (assigned_timely+assigned_unknown_timely+pending)/stage_total if stage_total else None},
    }
    caller_summary = caller_census(traces, profile, profile_sha256, latency_threshold_ms, timing, same_json)
    caller_summary["callerLatencyMs"] = quantiles(caller_summary.pop("latencySamplesMs"))
    if caller_summary["counts"]["tracesWithAccounting"]:
        reasons.extend(caller_summary["dataGaps"])
    return {"measurementStatus": "insufficient_data" if reasons else "provided_observations_measured", "dataGaps": reasons,
            "scope": "provided_traces_only", "populationCoverageVerified": False, "counts": counts,
            "leaseBindingCoverageVerified": counts["missingLeaseBindings"] == 0,
            "boundResultRatio": counts["boundResults"]/total if total else None,
            "withinCallerDeadlineRatio": {"lower": counts["boundResultsWithinCallerDeadline"]/total if total else None,
                                          "upper": (counts["boundResultsWithinCallerDeadline"]+counts["boundResultsWithUnknownCallerDeadline"])/total if total else None},
            "timelyBoundResultRatio": {"lower": counts["timelyBoundResults"]/total if total else None,
                                       "upper": (counts["timelyBoundResults"]+counts["boundResultsWithUnknownTimeliness"])/total if total else None},
            "latencyThresholdMs": latency_threshold_ms, "callerLatencyMs": quantiles(latencies), "scoringLatencyMs": quantiles(scoring),
            "stageInventory": stage_summary, "callerAccounting": caller_summary,
            "sloAccepted": False, "routingEnabled": False, "qualification": "not_assessed"}
