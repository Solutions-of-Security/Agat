"""An owned HTTP-handler crash and a gated replacement on the same endpoint."""
import hashlib
import os
import re
import signal
import time
from collections import Counter

from decision_runtime.artifacts import verify_seal
from decision_runtime.contracts import fields, number
from decision_runtime.metrics import OUTCOMES
from decision_runtime.metrics import outcome
from scripts.lib.decision_performance import profile_from_health
from scripts.lib.decision_public_load_verification import observation
from scripts.lib.decision_public_load_verification import same
from scripts.lib.decision_public_sources import write_json_new
from scripts.lib.decision_shadow_pilot import require, timestamp

PLAN_SCHEMA = "agat.decision.public-workflow-plan.v3"
RESULT_SCHEMA = "agat.decision.public-workflow-result.v3"
LAUNCH_PLAN = "agat.decision.public-workflow-launch-plan.v3"
LAUNCH_RESULT = "agat.decision.public-workflow-launch-result.v3"
REQUEST_SCHEMA = "agat.decision.public-workflow-crash-request.v1"
ARMED_SCHEMA = "agat.decision.public-workflow-crash-armed.v1"
STARTED_SCHEMA = "agat.decision.public-workflow-crash-started.v1"
SNAPSHOT_SCHEMA = "agat.decision.public-workflow-active-http-snapshot.v1"
CRASH_SCHEMA = "agat.decision.public-workflow-crash-applied.v1"
RECOVERY_REQUEST = "agat.decision.public-workflow-recovery-request.v1"
RECOVERED_SCHEMA = "agat.decision.public-workflow-recovery-applied.v1"
ARTIFACTS = {"runtime-crash-request.json", "runtime-crash-armed.json", "runtime-crash-started.json",
    "runtime-crash-inflight.json", "runtime-crash-applied.json", "runtime-recovery-request.json",
    "runtime-recovery-applied.json", "runtime-recovery-transport.json", "runtime-recovered.log"}
SOURCE_PATHS = ["scripts/run-public-support-workflow-recovery.py", "scripts/test/test_decision_public_workflow_recovery.py"]


def recovery_spec(context, index):
    require(type(index) is int and 1 <= index < len(context["inputs"])-1
            and context["inputs"][index]["contextEligible"] is True,
            "Crash requires an eligible target, completed prefix and recovery suffix")
    case = context["inputs"][index]
    return {"kind": "crash_owned_runtime_during_active_http_handler", "targetIndex": index,
        "targetCaseId": case["id"], "targetInputSha256": case["inputSha256"], "signal": "SIGKILL",
        "trigger": "exactly_one_active_http_handler", "restart": True,
        "endpoint": "same_loopback_port_same_frozen_profile", "warmupPerRuntime": 2, "retryCount": 0,
        "pollMs": 10, "observeDeadlineMs": 10000, "armDeadlineMs": 30000, "recoveryDeadlineMs": 90000,
        "sampleToSignalMaxMs": 250, "startupPerRuntimeMs": 60000, "driverDeadlineMs": 270000,
        "workflowDeadlineMs": 240000, "totalDeadlineMs": 440000, "callerTimeoutMs": 10000, "inferenceTimeoutMs": 5000}


def validate_prefix(context, routes, length):
    require(len(routes) == length, "Wrong completed prefix length")
    for index, (case, route) in enumerate(zip(context["inputs"], routes)):
        require(type(route["index"]) is int and route["index"] == index and route["caseId"] == case["id"]
                and route["inputSha256"] == case["inputSha256"] and route["runStatus"] == "completed"
                and type(route["primaryCalls"]) is int and route["primaryCalls"] == 1
                and route["primaryBranch"] is True and route["wrongBranch"] is False,
                "Changed, incomplete or replayed primary prefix")


def validate_request(context, spec, request, routes):
    same(spec, recovery_spec(context, spec["targetIndex"]), "Unsupported prospective crash")
    fields(request, {"schemaVersion", "targetIndex", "afterCaseId", "afterRunId", "createdAt"})
    index = spec["targetIndex"]; validate_prefix(context, routes, index)
    require(request["schemaVersion"] == REQUEST_SCHEMA and type(request["targetIndex"]) is int
            and request["targetIndex"] == index and request["afterCaseId"] == context["inputs"][index-1]["id"]
            and request["afterRunId"] == routes[-1]["runId"], "Crash was not armed after the declared prefix")
    timestamp(request["createdAt"], "crash.requestedAt")


def validate_recovery_request(context, spec, request, routes):
    fields(request, {"schemaVersion", "targetIndex", "afterCaseId", "afterRunId", "stageId", "observation", "createdAt"})
    index = spec["targetIndex"]; validate_prefix(context, routes, index+1)
    require(request["schemaVersion"] == RECOVERY_REQUEST and type(request["targetIndex"]) is int
            and request["targetIndex"] == index and request["afterCaseId"] == spec["targetCaseId"]
            and request["afterRunId"] == routes[-1]["runId"] and request["stageId"] == routes[-1]["stageId"],
            "Recovery was not requested after the completed interrupted target")
    observation = fields(request["observation"], {"mode", "fallback", "status", "reason", "callerTiming"})
    timing = fields(observation["callerTiming"], {"schemaVersion", "clock", "boundary", "durationMs"})
    require(observation["mode"] == "shadow" and observation["fallback"] == "primary"
            and observation["status"] == "unavailable" and observation["reason"] == "unreachable"
            and timing["schemaVersion"] == "agat.decision.caller-timing.v1" and timing["clock"] == "monotonic"
            and timing["boundary"] == "local_http_call", "Interrupted caller did not record actual transport unavailability")
    number(timing["durationMs"], 0, 10001); timestamp(request["createdAt"], "recovery.requestedAt")


def publish(directory, name, value):
    """Expose only a completed exclusive private receipt."""
    require(name in ARTIFACTS and name.endswith(".json"), "Unsupported barrier artifact")
    pending = directory/(name.removesuffix(".json")+".pending.json")
    write_json_new(pending, value)
    try: os.link(pending, directory/name)
    finally: pending.unlink()


def http_metrics(raw, *, in_progress):
    """Keep completed counters separate from an attested active handler."""
    require(type(in_progress) is int and in_progress in (0, 1) and isinstance(raw, str)
            and len(raw.encode()) <= 1048576, "Invalid bounded HTTP snapshot")
    values = {}; gauges = {}
    gauge_names = {"agat_decision_backend_ready", "agat_decision_requests_in_progress", "agat_decision_server_start_time_seconds"}
    for line in raw.splitlines():
        match = re.fullmatch(r'agat_decision_requests_total\{outcome="([a-z_]+)"\} ([0-9]+)', line)
        if match:
            require(match[1] not in values, "Duplicate physical outcome")
            values[match[1]] = int(match[2])
        elif line.startswith("agat_decision_requests_total"):
            raise ValueError("Unsupported physical outcome")
        name, _, value = line.partition(" ")
        if name in gauge_names:
            require(name not in gauges, "Duplicate HTTP gauge")
            gauges[name] = number(float(value), 0, 86400000000)
    require(set(values) == set(OUTCOMES) and set(gauges) == gauge_names
            and gauges["agat_decision_backend_ready"] == 1 and gauges["agat_decision_requests_in_progress"] == in_progress,
            "Snapshot is not the declared ready/active boundary")
    return values, gauges["agat_decision_server_start_time_seconds"]


def crash_owned(process, port, runtime, owned, errors):
    """SIGKILL only a live child session whose current group is wholly owned."""
    require(os.name == "posix" and process.poll() is None and process.pid != os.getpid()
            and os.getpgid(process.pid) == process.pid and os.getsid(process.pid) == process.pid
            and type(port) is int and 1 <= port <= 65535 and port != 8766,
            "Crash requires a live owned child session and experimental port")
    family = runtime.shared.inventory(process.pid)[0]
    table = [tuple(map(int, line.split())) for line in runtime.command(["ps", "-axo", "pid=,ppid=,pgid="]).splitlines() if len(line.split()) == 3]
    members = {pid for pid, _, pgid in table if pgid == process.pid}
    require(process.pid in members and members == family and os.getpid() not in members,
            "Child escaped its group or group contains an unowned process")
    owned.update(members)
    require(process.poll() is None and os.getpgid(process.pid) == process.pid, "Owned child exited before signal")
    sent_at = time.time(); os.killpg(process.pid, signal.SIGKILL)
    require(process.wait(3) == -int(signal.SIGKILL), "Owned crash did not exit by SIGKILL")
    deadline = time.monotonic()+3
    while True:
        remaining = runtime.remaining_owned_processes(members, errors)
        require(remaining is not None and not errors, "Unknown crash cleanup")
        if not remaining: break
        require(time.monotonic() < deadline, "Crashed process group remains present")
        time.sleep(.01)
    return {"runtimePid": process.pid, "processGroupId": process.pid, "memberPids": sorted(members),
        "signal": "SIGKILL", "signalSentAtEpoch": sent_at, "runtimeExitCode": process.returncode,
        "runtimeExited": True, "remainingOwnedPids": []}


def _pin(raw, expected, message):
    require(hashlib.sha256(raw).hexdigest() == expected, message)


def verify_boundary(context, spec, artifacts, cohort, routes, owned):
    """Replay both barriers; checksums do not establish a GPU-forward boundary."""
    from decision_runtime.contracts import parse_json
    request, armed, started, snapshot, crash, recovery, recovered, transport = (
        parse_json(artifacts[name]) for name in ("runtime-crash-request.json", "runtime-crash-armed.json", "runtime-crash-started.json",
            "runtime-crash-inflight.json", "runtime-crash-applied.json", "runtime-recovery-request.json",
            "runtime-recovery-applied.json", "runtime-recovery-transport.json"))
    index = spec["targetIndex"]; validate_request(context, spec, request, routes[:index])
    validate_recovery_request(context, spec, recovery, routes[:index+1])
    verify_seal(armed, ARMED_SCHEMA); verify_seal(crash, CRASH_SCHEMA); verify_seal(recovered, RECOVERED_SCHEMA)
    fields(armed, {"schemaVersion", "sha256", "targetIndex", "requestFileSha256", "runtimePid", "port", "profileSha256", "armedAt"})
    fields(started, {"schemaVersion", "targetIndex", "caseId", "inputSha256", "instanceId", "runId", "createdAt"})
    fields(snapshot, {"schemaVersion", "capturedAt", "elapsedMs", "runtimePid", "profileSha256", "metricsRaw", "counters", "serverStart"})
    fields(crash, {"schemaVersion", "sha256", "targetIndex", "requestFileSha256", "startedFileSha256", "snapshotFileSha256", "port", "profileSha256",
        "runtimePid", "processGroupId", "memberPids", "signal", "signalSentAtEpoch", "runtimeExitCode", "runtimeExited", "remainingOwnedPids", "appliedAt"})
    fields(recovered, {"schemaVersion", "sha256", "targetIndex", "requestFileSha256", "crashSealSha256", "runtimePid", "port", "profileSha256",
        "readyServerStart", "warmupCount", "appliedAt"})
    fields(transport, {"schemaVersion", "acceptedConnections", "resetConnections", "errors", "payloadsRead"})
    require(all(type(row["targetIndex"]) is int and row["targetIndex"] == index for row in (armed, started, crash, recovered)), "Changed crash target")
    require(all(type(row["runtimePid"]) is int for row in (armed, crash, snapshot, recovered))
            and armed["runtimePid"] in owned and armed["runtimePid"] == crash["runtimePid"] == snapshot["runtimePid"]
            and type(recovered["runtimePid"]) is int and recovered["runtimePid"] in owned and recovered["runtimePid"] != armed["runtimePid"], "Crash/replacement ownership differs")
    require(type(armed["port"]) is int and 1 <= armed["port"] <= 65535 and armed["port"] != 8766
            and type(crash["port"]) is int and type(recovered["port"]) is int and armed["port"] == crash["port"] == recovered["port"]
            and all(row["profileSha256"] == context["profileSha256"] for row in (armed, snapshot, crash, recovered)), "Recovery changed endpoint or frozen profile")
    require(started["schemaVersion"] == STARTED_SCHEMA and started["caseId"] == spec["targetCaseId"]
            and started["inputSha256"] == spec["targetInputSha256"] and started["instanceId"] == routes[index]["instanceId"]
            and started["runId"] == routes[index]["runId"] and snapshot["schemaVersion"] == SNAPSHOT_SCHEMA,
            "Active HTTP handler not bound to the sole scheduled target")
    require(type(crash["runtimeExitCode"]) is int and crash["runtimeExitCode"] == -9 and crash["signal"] == "SIGKILL"
            and crash["runtimeExited"] is True and crash["remainingOwnedPids"] == [] and type(crash["processGroupId"]) is int
            and crash["processGroupId"] == crash["runtimePid"] and isinstance(crash["memberPids"], list)
            and crash["runtimePid"] in crash["memberPids"] and len(set(crash["memberPids"])) == len(crash["memberPids"])
            and all(type(pid) is int and pid in owned for pid in crash["memberPids"]), "Crash lacked owned SIGKILL exit/cleanup")
    require(type(recovered["warmupCount"]) is int and recovered["warmupCount"] == spec["warmupPerRuntime"]
            and recovered["crashSealSha256"] == crash["sha256"], "Replacement warmup or crash binding differs")
    _pin(artifacts["runtime-crash-request.json"], armed["requestFileSha256"], "Armed request pin differs")
    _pin(artifacts["runtime-crash-request.json"], crash["requestFileSha256"], "Crash request pin differs")
    _pin(artifacts["runtime-crash-started.json"], crash["startedFileSha256"], "Started target pin differs")
    _pin(artifacts["runtime-crash-inflight.json"], crash["snapshotFileSha256"], "Active HTTP snapshot pin differs")
    _pin(artifacts["runtime-recovery-request.json"], recovered["requestFileSha256"], "Recovery request pin differs")
    values, server_start = http_metrics(snapshot["metricsRaw"], in_progress=1)
    same(values, snapshot["counters"], "Active snapshot cached counters differ"); same(server_start, snapshot["serverStart"], "Active snapshot server epoch differs")
    require(transport["schemaVersion"] == "agat.decision.public-workflow-reset-guard.v1"
            and type(transport["acceptedConnections"]) is int and type(transport["resetConnections"]) is int
            and transport["acceptedConnections"] == transport["resetConnections"] == 0 and transport["errors"] == []
            and transport["payloadsRead"] is False, "Unexpected calls crossed the paused recovery endpoint")
    inst = {row["runId"]: row for row in cohort["instances"]}
    times = [timestamp(request["createdAt"], "requestedAt"), timestamp(armed["armedAt"], "armedAt"),
        timestamp(inst[routes[index]["runId"]]["createdAt"], "target.createdAt"), timestamp(started["createdAt"], "startedAt"),
        timestamp(snapshot["capturedAt"], "snapshot.capturedAt")]
    sent = number(crash["signalSentAtEpoch"], 0, 86400000000)
    require(times == sorted(times) and times[-1].timestamp() <= sent
            and 0 <= sent-times[-1].timestamp() <= spec["sampleToSignalMaxMs"]/1000, "Crash missed the declared active-handler timing boundary")
    applied = timestamp(crash["appliedAt"], "crash.appliedAt"); requested_recovery = timestamp(recovery["createdAt"], "recovery.createdAt")
    ready = timestamp(recovered["appliedAt"], "recovered.appliedAt")
    require(sent <= applied.timestamp() <= requested_recovery.timestamp() <= ready.timestamp()
            and (times[1]-times[0]).total_seconds()*1000 <= spec["armDeadlineMs"]
            and (times[-1]-times[1]).total_seconds()*1000 <= spec["observeDeadlineMs"]
            and (ready-requested_recovery).total_seconds()*1000 <= spec["recoveryDeadlineMs"], "Recovery violated a prospective barrier or budget")
    require(all(timestamp(inst[row["runId"]]["createdAt"], "prefix.createdAt") <= times[0] for row in routes[:index])
            and all(timestamp(inst[row["runId"]]["createdAt"], "suffix.createdAt") >= ready for row in routes[index+1:]),
            "A workflow instance crossed an arming/recovery barrier")
    trace = next(row for row in cohort["traces"] if row["run"]["id"] == routes[index]["runId"])
    same(trace["decisionObservations"][0]["observation"], recovery["observation"], "Pre-recovery target return differs from durable census")
    return {"armed": armed, "crashed": crash, "recovered": recovered, "inflight": snapshot, "transport": transport}


def verify_physical(context, spec, result, cohort, routes, receipts):
    """Account completed handlers per process epoch, without assigning a crash outcome."""
    warmup = result["warmup"]; samples = result["samples"]
    require(isinstance(warmup, list) and len(warmup) == 4 and isinstance(samples, list) and len(samples) == 6,
            "Two independent runtime origins/warmups were not retained")
    labels = ("ready_before_scoring", "after_warmup", "before_crash_armed", "recovered_before_scoring", "recovered_after_warmup", "after_inventory")
    values = []; starts = []; elapsed = -1; last_wall = None
    pids = (receipts["armed"]["runtimePid"], receipts["recovered"]["runtimePid"])
    for position, (label, sample) in enumerate(zip(labels, samples)):
        fields(sample, {"label", "elapsedMs", "capturedAt", "epoch", "runtimePid", "health", "metricsRaw", "ownedPids", "processRaw", "counters", "serverStart"})
        current_wall = timestamp(sample["capturedAt"], "sample.capturedAt")
        require(sample["label"] == label and type(sample["epoch"]) is int and sample["epoch"] == position//3
                and type(sample["runtimePid"]) is int and sample["runtimePid"] == pids[position//3]
                and number(sample["elapsedMs"], 0, result["elapsedMs"]) > elapsed
                and (last_wall is None or current_wall >= last_wall), "Reordered snapshots or substituted process epoch")
        require(sample["health"]["status"] == "ready" and sample["health"]["mode"] == "shadow"
                and sample["health"]["profileSha256"] == context["profileSha256"], "Recovery snapshot is not the frozen ready profile")
        same(profile_from_health(sample["health"]), context["profile"], "Snapshot profile changed")
        parsed, started = http_metrics(sample["metricsRaw"], in_progress=0)
        same(parsed, sample["counters"], "Cached completed counters differ"); same(started, sample["serverStart"], "Cached server epoch differs")
        require(isinstance(sample["ownedPids"], list) and len(set(sample["ownedPids"])) == len(sample["ownedPids"])
                and sample["runtimePid"] in sample["ownedPids"] and all(type(pid) is int and pid in result["ownedPids"] for pid in sample["ownedPids"])
                and isinstance(sample["processRaw"], str) and len(sample["processRaw"].encode()) <= 1048576, "Snapshot process ownership differs")
        values.append(parsed); starts.append(started); elapsed = sample["elapsedMs"]; last_wall = current_wall
    require(len(set(starts[:3])) == len(set(starts[3:])) == 1 and starts[3] > starts[0]
            and receipts["inflight"]["serverStart"] == starts[0] and receipts["recovered"]["readyServerStart"] == starts[3], "Missing distinct runtime counter origins")
    require(all(value == 0 for row in (values[0], values[3]) for value in row.values()), "Nonzero runtime origin")
    case = next(row for row in context["inputs"] if row["contextEligible"]); warm_outcomes = [Counter(), Counter()]
    previous_end = [-1, -1]
    for position, row in enumerate(warmup):
        fields(row, {"epoch", "iteration", "caseId", "status", "reason", "observation", "callerMs", "wallMs", "startedMs", "finishedMs"})
        epoch = position//2
        require(type(row["epoch"]) is int and row["epoch"] == epoch and type(row["iteration"]) is int and row["iteration"] == position%2
                and row["caseId"] == case["id"] and row["status"] in {"ok", "abstain"}, "Warmup epoch/order/input differs")
        started = number(row["startedMs"], samples[epoch*3]["elapsedMs"], samples[epoch*3+1]["elapsedMs"])
        finished = number(row["finishedMs"], started, samples[epoch*3+1]["elapsedMs"])
        require(started >= previous_end[epoch] and number(row["callerMs"], 0, 10001) <= row["wallMs"]+.01
                and row["wallMs"] <= finished-started+.01 and finished-started <= 10002, "Warmup was retried, exceeded budget or crossed an epoch boundary")
        warm_outcomes[epoch][observation(row, case, context["profile"])] += 1; previous_end[epoch] = finished
    traces = {row["run"]["id"]: row for row in cohort["traces"]}; index = spec["targetIndex"]
    scheduled = []
    for records in (routes[:index], routes[index+1:]):
        scheduled.append(Counter(outcome(traces[row["runId"]]["decisionObservations"][0]["observation"]["result"]) for row in records))
    for epoch in range(2):
        base = epoch*3
        same(values[base+1], {key: warm_outcomes[epoch].get(key, 0) for key in OUTCOMES}, "Warmup counters differ from typed returns")
        same({key: values[base+2][key]-values[base+1][key] for key in OUTCOMES},
             {key: scheduled[epoch].get(key, 0) for key in OUTCOMES}, "Completed physical handlers differ from the declared prefix/suffix")
    same(receipts["inflight"]["counters"], values[2], "Active target had already completed or other handlers intervened")
    snapshot = receipts["inflight"]; number(snapshot["elapsedMs"], samples[2]["elapsedMs"], samples[3]["elapsedMs"])
    require(timestamp(samples[2]["capturedAt"], "armedSnapshotAt") <= timestamp(receipts["armed"]["armedAt"], "armedAt")
            and timestamp(samples[3]["capturedAt"], "readySnapshotAt") >= timestamp(receipts["crashed"]["appliedAt"], "crashedAt")
            and timestamp(samples[4]["capturedAt"], "warmSnapshotAt") <= timestamp(receipts["recovered"]["appliedAt"], "recoveredAt")
            and timestamp(samples[5]["capturedAt"], "finalSnapshotAt") >= timestamp(cohort["scope"]["endAt"], "cohort.endAt"),
            "Physical snapshots crossed the crash/recovery/drain barriers")
    require(type(result["runtimeExitCodes"][0]) is int and result["runtimeExitCodes"][0] == -9
            and type(result["runtimeExitCodes"][1]) is int, "Runtime exits do not attest two owned epochs")
    scheduled_count = sum(sum(row.values()) for row in scheduled)
    require(scheduled_count == len(context["inputs"])-1, "Interrupted request disappeared from the caller denominator")
    return {"physicalScheduledCompletedHandlers": scheduled_count, "interruptedScheduledHandlers": 1, "warmupCalls": 4,
        "physicalCompletedHttpHandlers": sum(values[2].values())+sum(values[5].values()),
        "completedHttpCountersByEpoch": [values[2], values[5]], "interruptedTerminalOutcome": "unknown_after_crash",
        "physicalHttpAccounting": "completed_counters_exact_interrupted_handler_terminal_unknown"}
