"""Offline consistency verification; checksums do not authenticate human agreement."""
from collections import Counter
import hashlib
import io
import math
from pathlib import Path
import re
import subprocess
import tarfile

from decision_runtime.artifacts import sealed, verify_seal
from decision_runtime.contracts import Request, fields, fingerprint, number, parse_json
from decision_runtime.metrics import OUTCOMES, outcome
from scripts.lib.decision_arrival_rate import SOURCE_PATHS
from scripts.lib.decision_performance import profile_from_health, validate_result
from scripts.lib.decision_public_load import PLAN_SCHEMA, RESULT_SCHEMA, PHASE_SCHEMA, validate_context
from scripts.lib import decision_public_primary as companion
from scripts.lib import decision_public_schedule as extended
from scripts.lib.decision_public_sources import pinned_input
from scripts.lib.decision_shadow_pilot import require, timestamp
from workers.local_decisions import CALLER_TIMING_VERSION

SCHEMA = "agat.decision.public-support-load-verification.v1"
PROFILE_PATH = "docs/qualification/local-decisions/performance/profiles/runtime-0.12.3-wired-4096.json"
CONTEXT_PATHS = ["decision_runtime", "scripts/profile-public-support-context.py", "scripts/lib/decision_public_context.py",
                 "scripts/lib/decision_public_sources.py", "scripts/lib/decision_shadow_pilot.py"]
LOAD_PATHS = [*SOURCE_PATHS, "scripts/run-public-support-load.py", "scripts/lib/decision_public_load.py",
    "scripts/lib/decision_public_context.py", "scripts/profile-public-support-context.py", "scripts/lib/decision_public_sources.py",
    "scripts/lib/decision_shadow_pilot.py", "scripts/test/test_decision_public_load.py", PROFILE_PATH,
    "docs/qualification/local-decisions/policy.shadow.v1.json",
    "docs/qualification/local-decisions/performance/evidence/2026-09-28/rag-http-isolation/resident-isolated/rag-workflow-plan.json"]
PLAN_FIELDS = {"schemaVersion", "sha256", "createdAt", "sourceCommit", "sourceFiles", "contextProfileFileSha256",
    "contextProfileSealSha256", "profile", "profileFileSha256", "profileSha256", "manifestFileSha256", "model", "runtime", "inputs",
    "schedule", "warmupCount", "scope", "overLimitBehavior", "primaryCompanionStarted", "backgroundWorkloadControlled",
    "referenceLabels", "calibrationRequests", "holdoutRequests", "sloAccepted", "representativeAgatTraffic", "routingEnabled", "qualification"}
RESULT_FIELDS = {"schemaVersion", "sha256", "status", "planSha256", "warmup", "phase", "samples", "failure", "cancelled", "ownedPids",
    "remainingOwnedPids", "cleanupErrors", "runtimeExitCode", "elapsedMs", "logSha256", "referenceLabels", "classificationAccuracyMeasured",
    "calibrationRequests", "holdoutRequests", "sloAccepted", "representativeAgatTraffic", "routingEnabled", "qualification"}
ROW_BASE = {"index", "caseId", "inputSha256", "inputTokens", "contextEligible", "scheduledMs", "dispatchMs", "status", "reason"}


def same(actual, expected, message):
    require(fingerprint(actual) == fingerprint(expected), message)


def sources_at(root, commit, pins, paths):
    require(isinstance(commit, str) and re.fullmatch(r"[a-f0-9]{40}", commit) and isinstance(pins, dict)
            and 1 <= len(pins) <= 500, "Invalid measured source identity")
    require(all(isinstance(name, str) and not Path(name).is_absolute() and ".." not in Path(name).parts
                and isinstance(sha, str) and re.fullmatch(r"[a-f0-9]{64}", sha) for name, sha in pins.items()), "Unsafe source pin")
    names = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", commit, "--", *paths], cwd=root, text=True, timeout=5).splitlines()
    require(all(name in names or any(member.startswith(name + "/") for member in names) for name in paths)
            and set(names) == set(pins), "Measured source inventory omits or adds contributors")
    raw = subprocess.check_output(["git", "archive", commit, "--", *paths], cwd=root, timeout=15)
    require(len(raw) <= 64 * 1024 * 1024, "Source archive exceeds the verification bound")
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        sources = {item.name: archive.extractfile(item).read() for item in archive if item.isfile()}
    same({name: hashlib.sha256(value).hexdigest() for name, value in sources.items()}, pins, "Historical source bytes differ")
    return sources


def unqualified(value):
    require(value["qualification"] == "not_assessed" and all(value[key] is False for key in
        ("sloAccepted", "representativeAgatTraffic", "routingEnabled")) and all(type(value[key]) is int and value[key] == 0
        for key in ("referenceLabels", "calibrationRequests", "holdoutRequests")), "Diagnostic evidence promoted to qualification")


def observation(row, case, profile):
    raw = fields(row["observation"], {"callerTiming"}, {"result", "status", "reason"})
    timing = fields(raw["callerTiming"], {"schemaVersion", "clock", "boundary", "durationMs"})
    require(timing["schemaVersion"] == CALLER_TIMING_VERSION and timing["clock"] == "monotonic"
            and timing["boundary"] == "local_http_call", "Unsupported caller clock/boundary")
    same(row["callerMs"], timing["durationMs"], "Caller duration differs from raw observation")
    caller = number(row["callerMs"], 0, 86_400_000); wall = number(row["wallMs"], 0, 86_400_000)
    require(caller <= wall + .01, "Caller duration exceeds measurement wall")
    body = {key: value for key, value in raw.items() if key != "callerTiming"}
    if set(body) == {"result"}:
        result = body["result"]; request = Request.from_dict(case["request"])
        validate_result(result, request, profile)
        same({key: result[key] for key in profile}, profile, "Response execution profile differs")
        require(row["status"] == result["status"] and row["reason"] == result["reason"]
                and result["durationMs"] <= caller + .1, "Typed status or server/caller duration differs")
        if result["status"] != "error":
            require(case["contextEligible"] is True and type(result.get("inputTokens")) is int
                    and result["inputTokens"] == case["inputTokens"] and type(result.get("generatedTokens")) is int
                    and result["generatedTokens"] == 0, "Computed tokens or context admission differ")
        if case["contextEligible"] is False:
            require(result["status"] == "error" and result["reason"] == "context_too_long", "Missing whole-input context rejection")
        return outcome(result)
    require(set(body) == {"status", "reason"} and body["status"] == row["status"] == "unavailable"
            and body["reason"] == row["reason"] and row["reason"] in
            {"busy", "timeout", "cancelled", "unreachable", "invalid_response", "profile_mismatch"}
            and case["contextEligible"] is True, "Unsupported unavailable observation")
    return row["reason"] if row["reason"] in {"busy", "profile_mismatch"} else None


def distribution(values):
    ordered = sorted(values)
    return {"count": len(ordered), "p50": round(ordered[math.ceil(len(ordered)*.5)-1], 3) if ordered else None,
            "p95": round(ordered[math.ceil(len(ordered)*.95)-1], 3) if ordered else None,
            "max": round(ordered[-1], 3) if ordered else None}


def verify_phase(phase, plan):
    reduced = plan.get("schemaVersion") == extended.SCHEMAS[0]
    mixed = reduced or plan.get("schemaVersion") == companion.SCHEMAS[0]
    fields(phase, {"schemaVersion", "ratePerSecond", "clientSlots", "maxSchedulerLagMs", "callerTimeoutMs", "thresholdMs", "elapsedMs", "rows", "summary"}
           | ({"condition", "phaseOriginMonotonicMs", "primaryRows"} if mixed else set()))
    require(phase["schemaVersion"] == (extended.SCHEMAS[2] if reduced else companion.SCHEMAS[2] if mixed else PHASE_SCHEMA), "Unsupported public load phase")
    same({key: phase[key] for key in ("ratePerSecond", "clientSlots", "maxSchedulerLagMs", "callerTimeoutMs", "thresholdMs")},
         {key: plan["schedule"][key] for key in ("ratePerSecond", "clientSlots", "maxSchedulerLagMs", "callerTimeoutMs", "thresholdMs")}, "Phase budget differs")
    interval = 4000 if reduced else 2000
    elapsed = number(phase["elapsedMs"], 0, len(plan["inputs"])*interval + (31000 if mixed else 11000))
    rows = phase["rows"]
    require(isinstance(rows, list) and len(rows) == len(plan["inputs"]), "Incomplete scheduled denominator")
    admitted = []; scored = []; good = []; drops = Counter(); intervals = []; known = Counter(); unknown = 0
    for index, (row, case) in enumerate(zip(rows, plan["inputs"])):
        fields(row, ROW_BASE, {"startedMs", "finishedMs", "observation", "callerMs", "wallMs"})
        same({key: row[key] for key in ("index", "caseId", "inputSha256", "inputTokens", "contextEligible")},
             {"index": index, "caseId": case["id"], "inputSha256": case["inputSha256"], "inputTokens": case["inputTokens"],
              "contextEligible": case["contextEligible"]}, "Reordered or rebound input row")
        require(abs(number(row["scheduledMs"], 0, 240000 if reduced else 120000) - index * interval) <= .0011, "Response-paced arrival schedule")
        lag = number(row["dispatchMs"], 0, elapsed + .0011) - row["scheduledMs"]
        require(lag >= -.0011, "Arrival dispatched early")
        if row["status"] == "dropped":
            require(set(row) == ROW_BASE and row["reason"] in {"client_capacity", "scheduler_lag"}, "Invented drop metadata or cancellation in observed run")
            require((lag > 100 - .0011) if row["reason"] == "scheduler_lag" else lag <= 100 + .0011,
                    "Drop reason contradicts scheduler lag")
            drops[row["reason"]] += 1; continue
        require(set(row) == ROW_BASE | {"startedMs", "finishedMs", "observation", "callerMs", "wallMs"}
                and row["status"] != "measurement_error" and lag <= 100 + .0011, "Invalid admitted measurement or catch-up")
        began = number(row["startedMs"], row["dispatchMs"] - .0011, elapsed + .0011)
        finished = number(row["finishedMs"], began, elapsed + .0011)
        require(finished - began + .02 >= number(row["wallMs"], 0, 86_400_000)
                and (not intervals or began + .0011 >= intervals[-1][1]), "Unbounded client slot or inconsistent interval")
        intervals.append((began, finished)); measured_outcome = observation(row, case, plan["profile"])
        if measured_outcome is None: unknown += 1
        else: known[measured_outcome] += 1
        admitted.append(row)
        if row["status"] in {"ok", "abstain"}:
            scored.append(row)
            if row["callerMs"] <= 5000: good.append(row)
    expected = {"scheduled": len(rows), "admitted": len(admitted), "scored": len(scored), "dropped": dict(drops),
        "statuses": dict(Counter(row["status"] for row in admitted)),
        "failures": dict(Counter(row["reason"] for row in admitted if row["status"] not in {"ok", "abstain"})),
        "goodWithinThreshold": len(good), "goodPerScheduled": len(good)/len(rows), "goodPerAdmitted": len(good)/len(admitted) if admitted else None,
        "callerMsAllMeasured": distribution([row["callerMs"] for row in admitted]),
        "callerMsScored": distribution([row["callerMs"] for row in scored]),
        "dispatchLagMs": distribution([row["dispatchMs"]-row["scheduledMs"] for row in rows])}
    same(phase["summary"], expected, "Summary changed denominator, quantiles or failures")
    return expected, known, unknown


def counters(sample, mixed=False):
    fields(sample, {"label", "elapsedMs", "health", "metricsRaw", "ownedPids", "processRaw"} | ({"primaryResidence"} if mixed else set()))
    require(isinstance(sample["metricsRaw"], str) and len(sample["metricsRaw"].encode()) <= 1024*1024, "Invalid metrics body")
    values = {}; gauges = {}
    for line in sample["metricsRaw"].splitlines():
        match = re.fullmatch(r'agat_decision_requests_total\{outcome="([a-z_]+)"\} ([0-9]+)', line)
        if match:
            require(match[1] not in values, "Duplicate physical outcome counter")
            values[match[1]] = int(match[2])
        elif line.startswith("agat_decision_requests_total"):
            raise ValueError("Unsupported physical outcome counter")
        for name in ("agat_decision_backend_ready", "agat_decision_requests_in_progress", "agat_decision_server_start_time_seconds"):
            if line.startswith(name + " "):
                require(name not in gauges, "Duplicate metrics gauge")
                gauges[name] = number(float(line.split(" ", 1)[1]), 0, 86_400_000_000)
    require(set(values) == set(OUTCOMES) and set(gauges) == {"agat_decision_backend_ready", "agat_decision_requests_in_progress", "agat_decision_server_start_time_seconds"}
            and gauges["agat_decision_backend_ready"] == 1 and gauges["agat_decision_requests_in_progress"] == 0, "Incomplete or non-quiescent metrics")
    return values, gauges["agat_decision_server_start_time_seconds"]


def verify(root, directory, context_path, *, context_sha, plan_sha, result_sha):
    raw = {"context": pinned_input(context_path, context_sha, 32*1024*1024),
           "plan": pinned_input(directory/"plan.json", plan_sha, 32*1024*1024),
           "result": pinned_input(directory/"result.json", result_sha, 64*1024*1024)}
    context = validate_context(parse_json(raw["context"]))
    parsed_plan = parse_json(raw["plan"]); reduced = parsed_plan.get("schemaVersion") == extended.SCHEMAS[0]
    mixed = reduced or parsed_plan.get("schemaVersion") == companion.SCHEMAS[0]
    schemas = extended.SCHEMAS if reduced else companion.SCHEMAS if mixed else (PLAN_SCHEMA, RESULT_SCHEMA, PHASE_SCHEMA)
    plan = verify_seal(parsed_plan, schemas[0])
    fields(plan, PLAN_FIELDS | ({"primary", "condition"} if mixed else set()) | ({"scheduleAdjustment"} if reduced else set()))
    result = verify_seal(parse_json(raw["result"]), schemas[1])
    fields(result, RESULT_FIELDS | ({"primaryWarmup", "primaryRows"} if mixed else set()))
    unqualified(plan); unqualified(result)
    require(result["status"] == "observed" and result["planSha256"] == plan["sha256"] and result["failure"] is None
            and result["cancelled"] is False and result["cleanupErrors"] == [] and result["remainingOwnedPids"] == []
            and type(result["runtimeExitCode"]) is int and result["classificationAccuracyMeasured"] is False, "Run or reported cleanup failed")
    number(result["elapsedMs"], 0, 86_400_000)
    require(timestamp(context["createdAt"], "context.createdAt") <= timestamp(plan["createdAt"], "plan.createdAt"), "Load plan predates context")
    require(plan["contextProfileFileSha256"] == context_sha and plan["contextProfileSealSha256"] == context["sha256"]
            and type(plan["warmupCount"]) is int and plan["warmupCount"] == 2 and plan["scope"] == "whole_unlabelled_public_development_http_inventory"
            and plan["overLimitBehavior"] == "send_full_input_expect_context_too_long" and plan["primaryCompanionStarted"] is mixed
            and plan["backgroundWorkloadControlled"] is False, "Context binding or workload scope differs")
    for key, source_key in (("profile", "profile"), ("profileFileSha256", "profileFileSha256"), ("profileSha256", "profileSha256"),
                            ("manifestFileSha256", "manifestFileSha256"), ("model", "model"), ("runtime", "tokenizerEnvironment"),
                            ("inputs", "inputs")):
        same(plan[key], context[source_key], "Plan differs from pinned context inventory/profile")
    primary_count = extended.verify_plan(plan, context) if reduced else len(plan["inputs"])
    if not reduced: same(plan["schedule"], context["proposedDiagnosticSchedule"], "Plan schedule differs from pinned context")
    sources_at(root, context["sourceCommit"], context["sourceFiles"], CONTEXT_PATHS)
    sources = sources_at(root, plan["sourceCommit"], plan["sourceFiles"], LOAD_PATHS + (companion.SOURCE_PATHS if mixed else []) + (extended.SOURCE_PATHS if reduced else []))
    if mixed:
        require(plan["condition"] == "primary_active", "Wrong primary load condition")
        companion.verify_plan(plan["primary"], sources)
    profile_raw = sources[PROFILE_PATH]
    require(hashlib.sha256(profile_raw).hexdigest() == plan["profileFileSha256"]
            and fingerprint(parse_json(profile_raw)) == plan["profileSha256"], "Committed profile bytes differ")
    same(parse_json(profile_raw), plan["profile"], "Committed profile semantics differ")
    implementation = hashlib.sha256()
    for name in sorted(name for name in sources if Path(name).parent == Path("decision_runtime") and name.endswith(".py")):
        implementation.update(Path(name).name.encode() + b"\0" + sources[name] + b"\0")
    require(implementation.hexdigest() == plan["profile"]["model"]["implementationSha256"], "Historical runtime implementation differs")
    requirements = dict(line.split("==") for line in sources["decision_runtime/requirements-mlx.txt"].decode().splitlines() if line and not line.startswith("#"))
    same(plan["runtime"], {"python":"3.13.12", "machine":"arm64", "packages":requirements}, "Historical runtime dependencies differ")
    artifacts = {}
    fields(result["logSha256"], {"runtime.log", "requests.jsonl"} | ({"primary.log", "primary-requests.jsonl"} if mixed else set()))
    for name, digest in result["logSha256"].items(): artifacts[name] = pinned_input(directory/name, digest, 16*1024*1024)
    with (directory/"phase.json").open("rb") as stream:
        artifacts["phase.json"] = stream.read(32*1024*1024+1)
    require(0 < len(artifacts["phase.json"]) <= 32*1024*1024, "Phase file exceeds verification bound")
    phase_file = verify_seal(parse_json(artifacts["phase.json"]), schemas[2])
    same({key:value for key,value in phase_file.items() if key != "sha256"}, result["phase"], "Embedded phase differs from phase file")
    summary, known, unknown = verify_phase(result["phase"], plan)
    records = [parse_json(line) for line in artifacts["requests.jsonl"].splitlines()]
    require(len(records) == len(plan["inputs"]) and all(isinstance(row, dict) and type(row.get("index")) is int for row in records), "Raw journal denominator differs")
    same(sorted(records, key=lambda row:row["index"]), result["phase"]["rows"], "Raw journal rows drift, duplicate or disappear")
    primary_summary = None
    if mixed:
        same(result["primaryRows"], result["phase"]["primaryRows"], "Primary result/phase inventories differ")
        primary_records = [parse_json(line) for line in artifacts["primary-requests.jsonl"].splitlines()]
        require(len(primary_records) == primary_count and all(isinstance(row,dict) and type(row.get("index")) is int for row in primary_records), "Primary raw journal denominator differs")
        same(sorted(primary_records,key=lambda row:row["index"]), result["primaryRows"], "Primary raw journal rows differ")
        primary_summary = companion.verify_phase(result["phase"],primary_count)
        fields(result["primaryWarmup"], {"status", "response", "wallMs"})
        require(result["primaryWarmup"]["status"] == "returned", "Primary warmup did not return")
        companion.primary.validate_response(result["primaryWarmup"]["response"])
        require(result["primaryWarmup"]["response"]["total_duration"]/1_000_000 <= number(result["primaryWarmup"]["wallMs"],0,30000.1)+.1,
                "Primary warmup server duration exceeds caller wall")
    require(isinstance(result["warmup"], list) and len(result["warmup"]) == 2, "Incomplete separate warmup")
    first = next(case for case in plan["inputs"] if case["contextEligible"])
    warmup_outcomes = Counter()
    for index, row in enumerate(result["warmup"]):
        fields(row, {"caseId", "inputSha256", "inputTokens", "iteration", "status", "reason", "observation", "callerMs", "wallMs"})
        same({key:row[key] for key in ("caseId", "inputSha256", "inputTokens", "iteration")},
             {"caseId":first["id"], "inputSha256":first["inputSha256"], "inputTokens":first["inputTokens"], "iteration":index}, "Warmup binding differs")
        require(row["status"] in {"ok", "abstain"}, "Warmup did not compute")
        warmup_outcomes[observation(row, first, plan["profile"])] += 1
    pids = result["ownedPids"]
    require(isinstance(pids, list) and 1 <= len(pids) <= 128 and all(type(pid) is int and 0 < pid < 2**31 for pid in pids)
            and pids == sorted(set(pids)), "Invalid owned process census")
    samples = result["samples"]
    require(isinstance(samples, list) and len(samples) == 3 and [sample["label"] for sample in samples]
            == ["ready_before_scoring", "after_warmup", "after_inventory"], "Incomplete readiness/metrics sequence")
    values = []; starts = []; previous = 0
    for sample in samples:
        elapsed = number(sample["elapsedMs"], previous, result["elapsedMs"]); previous = elapsed
        same(profile_from_health(sample["health"]), plan["profile"], "Runtime profile drift")
        require(isinstance(sample["ownedPids"], list) and sample["ownedPids"] and all(type(pid) is int for pid in sample["ownedPids"])
                and sample["ownedPids"] == sorted(set(sample["ownedPids"])) and set(sample["ownedPids"]) <= set(pids), "Sample process census differs")
        if mixed:
            models = sample["primaryResidence"]["models"]
            require(isinstance(models,list) and len(models) == 1 and models[0].get("digest") == companion.primary.DIGEST
                    and type(models[0].get("context_length")) is int and models[0]["context_length"] == 8192, "Primary residence/model/context differs")
        counter, start = counters(sample,mixed); values.append(counter); starts.append(start)
    require(len(set(starts)) == 1 and starts[0] > 0, "Runtime restarted during measurements")
    same(values[0], dict.fromkeys(OUTCOMES, 0), "Owned runtime received prior traffic")
    same(values[1], {key:warmup_outcomes[key] for key in OUTCOMES}, "Warmup physical HTTP counters differ")
    delta = {key:values[2][key]-values[1][key] for key in OUTCOMES}
    require(all(delta[key] >= known[key] for key in OUTCOMES) and sum(delta.values()) - sum(known.values()) <= unknown,
            "Physical HTTP outcomes contradict admitted inventory")
    require(unknown > 0 or sum(delta.values()) == summary["admitted"], "Physical HTTP denominator differs")
    # Re-read every consumed artifact before publishing; no source code is executed.
    require(raw == {"context":pinned_input(context_path, context_sha, 32*1024*1024),
                    "plan":pinned_input(directory/"plan.json", plan_sha, 32*1024*1024),
                    "result":pinned_input(directory/"result.json", result_sha, 64*1024*1024)}, "Artifacts changed during verification")
    for name, expected in artifacts.items():
        with (directory/name).open("rb") as stream:
            require(stream.read(len(expected)+1) == expected, "Journal/phase/log changed during verification")
    return sealed({"schemaVersion":SCHEMA,"status":"pass" if not unknown else "insufficient_data",
        "contextProfileFileSha256":context_sha,"planFileSha256":plan_sha,"resultFileSha256":result_sha,
        "planSealSha256":plan["sha256"],"resultSealSha256":result["sha256"],"sourceCommit":plan["sourceCommit"],"sourceFiles":len(sources),
        "summary":summary,"warmupCalls":2,"physicalHttpAccounting":"exact" if not unknown else "bounded_unknown",
        "unknownPhysicalOutcomes":unknown,"physicalScheduledHttpHandlers":sum(delta.values()),"physicalHttpCounters":values[2],
        "reportedCleanupComplete":True,"liveCleanupVerified":False,"classificationAccuracyMeasured":False,
        "referenceLabels":0,"calibrationRequests":0,"holdoutRequests":0,"representativeAgatTraffic":False,
        "sloAccepted":False,"routingEnabled":False,"qualification":"not_assessed",
        **({"primarySummary":primary_summary,"primaryWarmupCalls":1,"condition":"primary_active"} if mixed else {}),
        **({"scheduleAdjustment":plan["scheduleAdjustment"]} if reduced else {})})
