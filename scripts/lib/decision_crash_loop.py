"""Validate a bounded native restart experiment independently of pass flags."""
from __future__ import annotations

from decision_runtime.contracts import fingerprint, number, parse_json
from scripts.lib.decision_monitoring import summarize_snapshot
from scripts.lib.decision_performance import validate_result

FAILURES = 3
THROTTLE_SECONDS = 30
STABLE_SECONDS = 30


def require(condition, message):
    if not condition:
        raise ValueError(message)


def integer(value, lower=1, upper=2**31-1):
    require(type(value) is int and lower <= value <= upper, "Invalid native generation/PID integer")
    return value


def failure_state(state, generation):
    """An old exit 75 on a still-running generation cannot prove another exit."""
    integer(generation, 1, FAILURES)
    require(isinstance(state, dict), "Temporary registration disappeared during failure")
    require(integer(state.get("runs"), 1, FAILURES+1) == generation, "Unexpected start during failure observation")
    if state.get("pid") is not None:
        integer(state["pid"])
        return False
    if "last exit code" not in state:
        return False
    require(type(state["last exit code"]) is int and state["last exit code"] == 75, "Unexpected runtime exit code")
    return True


def runtime_inventory(run):
    parent = integer(run["pid"])
    children = run["children"]
    require(isinstance(children, list) and len(children) == 2
            and {row["role"] for row in children} == {"inference", "resource_tracker"}, "Incomplete owned child inventory")
    pids = [parent] + [integer(row["pid"]) for row in children]
    require(len(set(pids)) == 3 and all(type(row["parentPid"]) is int and row["parentPid"] == parent for row in children), "Invalid owned process relationship")
    return pids


def metric_snapshot(record, instance, count):
    summary = summarize_snapshot(record["raw"], instance)
    require(summary == record["summary"], "Recorded metric summary differs from raw series")
    require(summary["backendReady"] == 1 and summary["requestsInProgress"] == 0
            and summary["histogramCounts"] == {"computed": count, "rejected": 0, "failed": 0}, "Unexpected generation counters/readiness")
    targets = record["targets"]["data"]["activeTargets"]
    require(record["targets"].get("status") == "success" and not record["targets"].get("warnings")
            and len(targets) == 1 and targets[0]["scrapeUrl"] == f"http://{instance}/metrics"
            and targets[0]["health"] == "up" and not targets[0]["lastError"], "Foreign or unhealthy native scrape")
    from datetime import datetime
    scraped = datetime.fromisoformat(targets[0]["lastScrape"].replace("Z", "+00:00")).timestamp()
    require(scraped >= summary["serverStartTime"] and scraped <= number(record["capturedEpoch"], 1, 10**11) + 1,
            "Native scrape predates generation or lies in the future")
    return summary


def up_sequence(body, instance):
    require(body.get("status") == "success" and not body.get("warnings"), "Failed up history query")
    data = body["data"]
    require(data["resultType"] == "matrix" and len(data["result"]) == 1, "Expected one up history")
    row = data["result"][0]
    require(row["metric"] == {"__name__": "up", "job": "agat-decision", "instance": instance}, "Foreign up history")
    samples = row["values"]
    times = [number(value[0], 1, 10**11) for value in samples]
    values = [float(value[1]) for value in samples]
    require(len(samples) >= 3 and all(a < b for a,b in zip(times,times[1:]))
            and set(values) == {0.0,1.0} and values[0] == values[-1] == 1 and 0 in values[1:-1], "Missing healthy/down/recovered sequence")
    # These are query evaluations, not a count of independent scrapes.
    return True


def retirement_events(raw):
    require(isinstance(raw, str) and len(raw) <= 4*1024*1024, "Retirement log exceeded its bound")
    events = []
    for line in raw.splitlines():
        if line.startswith("{"):
            event = parse_json(line)
            if event.get("eventName") == "decision.backend_retired":
                events.append(event)
    return events


def validate_history(runs, failures, quiet, events, profile, instance, request):
    require(len(runs) == FAILURES+1 and len(failures) == len(events) == FAILURES, "Expected four starts and three controlled retirements")
    seen, births, starts = set(), [], []
    for index, run in enumerate(runs, 1):
        require(integer(run["generation"], 1, FAILURES+1) == index
                and integer(run["service"].get("pid")) == run["pid"]
                and integer(run["service"].get("runs"), 1, FAILURES+1) == index, "Launchd generation skipped or changed")
        pids = runtime_inventory(run)
        require(not seen.intersection(pids), "Process identity reused within the bounded experiment")
        seen.update(pids)
        require(run["profile"] == profile and run["profileSha256"] == fingerprint(profile), "Generation changed the pinned profile")
        process_starts = run["processStarts"]
        require(set(process_starts) == {str(pid) for pid in pids}, "Process birth inventory incomplete")
        birth = number(process_starts[str(run["pid"])], 1, 10**11)
        require(all(birth-1 <= number(value, 1, 10**11) <= run["readyMetrics"]["capturedEpoch"]+1
                    for value in process_starts.values()), "Invalid process birth chronology")
        ready = metric_snapshot(run["readyMetrics"], instance, 0)
        scored = metric_snapshot(run["scoredMetrics"], instance, 1)
        require(birth-1 <= ready["serverStartTime"] <= run["readyMetrics"]["capturedEpoch"]
                and scored["serverStartTime"] == ready["serverStartTime"], "Metric generation changed during scoring")
        if births:
            # ps lstart has whole-second resolution; permit one second quantization.
            require(birth-births[-1] >= THROTTLE_SECONDS-1, "Starts violate the configured throttle")
            require(ready["serverStartTime"] > starts[-1], "Counters came from an earlier generation")
        births.append(birth); starts.append(ready["serverStartTime"])
        result = run["decision"]
        validate_result(result["result"], request, profile)
        require(type(result["httpStatus"]) is int and result["httpStatus"] == 200 and result["completeResponse"] is True,
                "Incomplete or unsuccessful diagnostic")
        for key in ("status", "reason", "selectedOptionId", "value", "distribution", "inputTokens", "generatedTokens"):
            require(result["result"][key] == runs[0]["decision"]["result"][key], "Decision/tokens changed after restart")
    for index, (failure, event) in enumerate(zip(failures, events), 1):
        run = runs[index-1]
        child = next(row["pid"] for row in run["children"] if row["role"] == "inference")
        require(integer(failure["generation"], 1, FAILURES) == index and integer(failure["signaledChildPid"]) == child
                and failure["ownedProcessesGone"] is True and failure_state(failure["exitState"], index), "Failure is not bound to its retired generation")
        require(event.get("schemaVersion") == "agat.decision.retirement.v1" and event.get("eventName") == "decision.backend_retired"
                and event.get("runtimeVersion") == profile["runtimeVersion"] and event.get("profileSha256") == run["profileSha256"]
                and type(event.get("exitCode")) is int and event["exitCode"] == 75
                and type(event.get("childExitCode")) is int and event["childExitCode"] == -9
                and type(event.get("childPid")) is int and event["childPid"] == child
                and event.get("reason") == "backend_unavailable", "Retirement event does not match the controlled child death")
        require(failure["endpointPending"] is True and failure["alertCleared"] is True, "Endpoint alert did not follow each failure")
        down = failure["downTargets"]
        require(down.get("status") == "success" and not down.get("warnings"), "Failed native down-target query")
        targets = down["data"]["activeTargets"]
        require(len(targets) == 1 and targets[0]["scrapeUrl"] == f"http://{instance}/metrics"
                and targets[0]["health"] == "down" and bool(targets[0]["lastError"]), "Missing native scrape error")
        for key, pending in (("pendingAlerts", True), ("clearedAlerts", False)):
            body = failure[key]
            require(body.get("status") == "success" and not body.get("warnings"), "Failed endpoint alert query")
            alerts = [row for row in body["data"]["alerts"] if row["labels"].get("alertname") == "AgatDecisionEndpointUnavailable"]
            require((len(alerts) == 1 and alerts[0]["state"] == "pending" and alerts[0]["labels"].get("instance") == instance
                     and alerts[0]["labels"].get("job") == "agat-decision") if pending else not alerts, "Raw endpoint alert state does not match failure/recovery")
        up_sequence(failure["upHistory"], instance)
    samples = quiet["samples"]
    require(len(samples) >= 2 and number(quiet["elapsedSeconds"], STABLE_SECONDS, STABLE_SECONDS+15) >= STABLE_SECONDS,
            "Missing bounded stable observation window")
    times = [number(row["elapsedSeconds"], 0, STABLE_SECONDS+15) for row in samples]
    require(times[0] < 1 and times[-1] >= STABLE_SECONDS and all(0 < b-a <= 5 for a,b in zip(times,times[1:])),
            "Stable observation has gaps or is shorter than the required window")
    last = runs[-1]
    for row in samples:
        require(integer(row["service"].get("pid")) == last["pid"] and integer(row["service"].get("runs"), 1, FAILURES+1) == FAILURES+1
                and row["children"] == last["children"], "Unexpected restart/child drift during stable window")
        require(metric_snapshot(row["metrics"], instance, 1)["serverStartTime"] == starts[-1], "Stable scrape came from another generation")
    return {"starts": len(runs), "controlledFailures": len(failures), "scoredCalls": len(runs),
            "ownedRuntimePids": len(seen), "startIntervalsSeconds": [b-a for a,b in zip(births,births[1:])],
            "stableWindowSeconds": quiet["elapsedSeconds"], "stableSamples": len(samples)}
