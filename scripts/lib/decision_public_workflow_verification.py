"""Source-bound workflow receipt replay; a passed receipt does not appoint owners."""
from collections import Counter
import hashlib
import json
from pathlib import Path

from decision_runtime.artifacts import sealed, verify_seal
from decision_runtime.contracts import fields, number, parse_json
from scripts.lib.decision_public_workflow import SOURCE_PATHS, PROFILE_PATH, shared_config, verify_inventory
from scripts.lib.decision_public_sources import pinned_input
from scripts.lib.decision_public_load_verification import CONTEXT_PATHS, sources_at, observation, counters, same
from scripts.lib.decision_public_load import validate_context
from scripts.lib.decision_performance import profile_from_health
from scripts.lib.decision_shadow_pilot import require, timestamp
from scripts.lib.decision_public_workflow_loss import loss_spec, verify_boundary
from scripts.lib import decision_public_workflow_timeout as timeout
from scripts.lib import decision_public_workflow_cancellation as cancellation

SCHEMA = "agat.decision.public-workflow-verification.v1"
PLAN_SCHEMA = "agat.decision.public-workflow-launch-plan.v1"
RESULT_SCHEMA = "agat.decision.public-workflow-launch-result.v1"
ARTIFACTS = {"workflow-plan.json", "workflow-driver.json", "workflow-routes.jsonl", "cohort.http.json", "worker.log", "runtime.log", "driver.log"}


def verify(root, directory, context_path, *, context_sha, plan_sha, result_sha):
    raw = {"context": pinned_input(context_path, context_sha, 32*1024*1024),
           "plan": pinned_input(directory/"plan.json", plan_sha, 32*1024*1024),
           "result": pinned_input(directory/"result.json", result_sha, 64*1024*1024)}
    plan = parse_json(raw["plan"])
    if plan.get("schemaVersion") == "agat.decision.public-workflow-launch-plan.v7":
        from scripts.lib.decision_public_workflow_active_deadline_verification import verify as verify_deadline
        return verify_deadline(root, directory, context_path, context_sha=context_sha, plan_sha=plan_sha, result_sha=result_sha)
    if plan.get("schemaVersion") == "agat.decision.public-workflow-launch-plan.v6":
        from scripts.lib.decision_public_workflow_active_verification import verify as verify_active
        return verify_active(root, directory, context_path, context_sha=context_sha, plan_sha=plan_sha, result_sha=result_sha)
    if plan.get("schemaVersion") == "agat.decision.public-workflow-launch-plan.v3":
        from scripts.lib.decision_public_workflow_recovery_verification import verify as verify_recovery
        return verify_recovery(root, directory, context_path, context_sha=context_sha, plan_sha=plan_sha, result_sha=result_sha)
    context = validate_context(parse_json(raw["context"]))
    loss = plan.get("schemaVersion") == "agat.decision.public-workflow-launch-plan.v2"
    timing_out = plan.get("schemaVersion") == "agat.decision.public-workflow-launch-plan.v4"
    cancelling = plan.get("schemaVersion") == "agat.decision.public-workflow-launch-plan.v5"
    extra = {"coordinatorCancellation"} if cancelling else {"callerTimeout"} if timing_out else {"runtimeLoss"} if loss else set()
    plan = verify_seal(plan, "agat.decision.public-workflow-launch-plan.v5" if cancelling else "agat.decision.public-workflow-launch-plan.v4" if timing_out else "agat.decision.public-workflow-launch-plan.v2" if loss else PLAN_SCHEMA)
    result = verify_seal(parse_json(raw["result"]), "agat.decision.public-workflow-launch-result.v5" if cancelling else "agat.decision.public-workflow-launch-result.v4" if timing_out else "agat.decision.public-workflow-launch-result.v2" if loss else RESULT_SCHEMA)
    fields(plan, {"schemaVersion", "sha256", "createdAt", "sourceCommit", "sourceFiles", "contextProfileFileSha256", "context", "config", "runtime",
                  "manifestFileSha256", "mode", "primary", "warmupCount", "ownersAppointed", "referenceLabels", "sloAccepted", "routingEnabled", "qualification"} | extra)
    fields(result, {"schemaVersion", "sha256", "status", "planSha256", "evidence", "warmup", "samples", "failure", "ownedPids", "remainingOwnedPids",
                    "cleanupErrors", "runtimeExitCode", "driverExitCode", "artifactSha256", "elapsedMs", "referenceLabels", "classificationAccuracyMeasured",
                    "ownersAppointed", "sloAccepted", "routingEnabled", "qualification"} | extra)
    for value in (plan, result):
        require(type(value["referenceLabels"]) is int and value["referenceLabels"] == 0 and value["ownersAppointed"] is False
                and value["sloAccepted"] is False and value["routingEnabled"] is False and value["qualification"] == "not_assessed",
                "Workflow receipt grants labels, authority or customer qualification")
    require(plan["contextProfileFileSha256"] == context_sha and plan["manifestFileSha256"] == context["manifestFileSha256"]
            and plan["mode"] == "serial_closed_model_integration" and plan["primary"] == "fixture_chat_completions"
            and type(plan["warmupCount"]) is int and plan["warmupCount"] == 2, "Wrong context, scope or warmup binding")
    same(plan["context"], context, "Embedded context differs from independently pinned original")
    same(plan["config"], shared_config(context), "Published workflow configuration differs")
    same(plan["runtime"], context["tokenizerEnvironment"], "Runtime dependency record differs")
    require(timestamp(context["createdAt"], "context.createdAt") <= timestamp(plan["createdAt"], "plan.createdAt"), "Plan predates context")
    require(result["status"] == "observed" and result["failure"] is None and result["planSha256"] == plan["sha256"]
            and result["remainingOwnedPids"] == [] and result["cleanupErrors"] == [] and result["classificationAccuracyMeasured"] is False
            and type(result["runtimeExitCode"]) is int and type(result["driverExitCode"]) is int and result["driverExitCode"] == 0,
            "Run or reported cleanup failed")
    number(result["elapsedMs"], 0, 86_400_000)
    require(isinstance(result["ownedPids"], list) and result["ownedPids"] and len(set(result["ownedPids"])) == len(result["ownedPids"])
            and all(type(pid) is int and 0 < pid < 2**31 for pid in result["ownedPids"]), "Invalid reported process ownership")
    sources_at(root, context["sourceCommit"], context["sourceFiles"], CONTEXT_PATHS)
    if loss:
        same(plan["runtimeLoss"], loss_spec(context, plan["runtimeLoss"]["beforeIndex"]), "Unsupported prospective runtime loss")
    if timing_out:
        same(plan["callerTimeout"], timeout.timeout_spec(context, plan["callerTimeout"]["targetIndex"]), "Unsupported prospective caller timeout")
    if cancelling:
        same(plan["coordinatorCancellation"], cancellation.cancellation_spec(context, plan["coordinatorCancellation"]["targetIndex"]), "Unsupported prospective coordinator cancellation")
    paths = SOURCE_PATHS + (["scripts/test/test_decision_public_workflow_loss.py"] if loss else []) + (["scripts/test/test_decision_public_workflow_timeout.py"] if timing_out else []) + (["scripts/test/test_decision_public_workflow_cancellation.py"] if cancelling else [])
    sources = sources_at(root, plan["sourceCommit"], plan["sourceFiles"], paths)
    if loss:
        require({"scripts/lib/decision_public_workflow_loss.py", "scripts/test/test_decision_public_workflow_loss.py"} <= set(sources), "Runtime-loss contributors omitted")
    if timing_out:
        require({"scripts/lib/decision_public_workflow_timeout.py", "scripts/test/test_decision_public_workflow_timeout.py"} <= set(sources), "Caller-timeout contributors omitted")
    if cancelling:
        require({"scripts/lib/decision_public_workflow_cancellation.py", "scripts/test/test_decision_public_workflow_cancellation.py"} <= set(sources), "Coordinator-cancellation contributors omitted")
    require(hashlib.sha256(sources[PROFILE_PATH]).hexdigest() == context["profileFileSha256"], "Historical profile bytes differ")
    same(parse_json(sources[PROFILE_PATH]), context["profile"], "Historical profile semantics differ")
    implementation = hashlib.sha256()
    for name in sorted(name for name in sources if Path(name).parent == Path("decision_runtime") and name.endswith(".py")):
        implementation.update(Path(name).name.encode()+b"\0"+sources[name]+b"\0")
    require(implementation.hexdigest() == context["profile"]["model"]["implementationSha256"], "Historical runtime implementation differs")
    requirements = dict(line.split("==") for line in sources["decision_runtime/requirements-mlx.txt"].decode().splitlines() if line and not line.startswith("#"))
    same(plan["runtime"], {"python": "3.13.12", "machine": "arm64", "packages": requirements}, "Historical runtime pins differ")
    artifact_names = ARTIFACTS | ({"runtime-loss-request.json", "runtime-loss-applied.json", "runtime-loss-transport.json"} if loss else set())
    if timing_out: artifact_names |= {"caller-timeout-transport.json", "caller-timeout-drained.json"}
    if cancelling: artifact_names |= cancellation.ARTIFACTS
    fields(result["artifactSha256"], artifact_names)
    artifacts = {name: pinned_input(directory/name, result["artifactSha256"][name], 16*1024*1024) for name in artifact_names}
    recipe = parse_json(artifacts["workflow-plan.json"]); driver = parse_json(artifacts["workflow-driver.json"]); cohort = parse_json(artifacts["cohort.http.json"])
    fields(recipe, {"schemaVersion", "mode", "primary", "processId", "processVersion", "projectId", "startAt", "scopeEndRule", "config",
                    "profileSha256", "inputs", "ownersAppointed", "routingEnabled", "qualification", "graphSha256"} | extra)
    fields(driver, {"status", "primary", "primaryCalls", "routes", "nodeVersion", "ownedPids", "workerExitCode", "actualWindow",
                    "unauthenticatedStatus", "authenticatedStatus", "ownersAppointed", "routingEnabled", "qualification"} | ({"coordinatorCancellationReady", "coordinatorCancellationApplied", "coordinatorCancellationDrained"} if cancelling else {"callerTimeoutDrained"} if timing_out else {"runtimeLossApplied"} if loss else set()))
    fields(cohort, {"schemaVersion", "snapshotId", "observedAt", "scope", "snapshot", "limits", "counts", "runIdsSha256", "instances", "traces", "dataPolicy",
                    "populationCoverageVerified", "eligibleWorkloadVerified", "httpAttemptInventoryVerified", "sloAccepted", "routingEnabled", "qualification"})
    require(all(cohort[key] is False for key in ("populationCoverageVerified", "eligibleWorkloadVerified", "httpAttemptInventoryVerified")),
            "Lab census promoted to customer coverage")
    fields(cohort["snapshot"], {"dialect", "consistency", "storedCohortComplete", "truncated"})
    require(cohort["snapshot"]["dialect"] in {"sqlite", "postgresql"}, "Unknown snapshot dialect")
    same(cohort["limits"], {"instances": 1000, "stages": 10000, "activityBytes": 16777216, "exportBytes": 16777216}, "Census limits differ")
    require(isinstance(recipe["graphSha256"], str) and len(recipe["graphSha256"]) == 64
            and all(c in "0123456789abcdef" for c in recipe["graphSha256"]), "Invalid reported graph pin")
    require(type(cohort["counts"]["storedStages"]) is int and len(context["inputs"]) <= cohort["counts"]["storedStages"] <= 10000,
            "Invalid stored stage count")
    require(cohort["runIdsSha256"] == hashlib.sha256(json.dumps([row["runId"] for row in cohort["instances"]],separators=(",", ":")).encode()).hexdigest(),
            "Raw cohort run-set digest differs")
    require(timestamp(recipe["startAt"], "recipe.startAt") > timestamp(plan["createdAt"], "plan.createdAt")
            and recipe["scopeEndRule"] == "after_full_input_inventory_and_worker_drain", "Recipe was not prospectively scoped")
    records = [parse_json(line) for line in artifacts["workflow-routes.jsonl"].splitlines()]
    same(driver["routes"], records, "Workflow raw journal differs or is reordered")
    require(driver["status"] == "observed" and driver["primary"] == "fixture_chat_completions"
            and type(driver["primaryCalls"]) is int and driver["primaryCalls"] == len(context["inputs"])
            and type(driver["workerExitCode"]) is int and driver["workerExitCode"] == 0
            and type(driver["unauthenticatedStatus"]) is int and driver["unauthenticatedStatus"] == 401
            and type(driver["authenticatedStatus"]) is int and driver["authenticatedStatus"] == 200
            and driver["ownersAppointed"] is False and driver["routingEnabled"] is False and driver["qualification"] == "not_assessed",
            "Driver failure, auth bypass or unsupported authority")
    same(driver["actualWindow"], {key: cohort["scope"][key] for key in ("startAt", "endAt")}, "Driver/census windows differ")
    require(isinstance(driver["ownedPids"], list) and len(driver["ownedPids"]) == len(set(driver["ownedPids"])) == 2
            and all(type(pid) is int and pid in result["ownedPids"] for pid in driver["ownedPids"]), "Missing driver/worker ownership record")
    transport = parse_json(artifacts["caller-cancellation-transport.json"]) if cancelling else parse_json(artifacts["caller-timeout-transport.json"]) if timing_out else None
    bundle = cancellation.receipt_bundle(artifacts) if cancelling else None
    evidence = verify_inventory(context, recipe, cohort, records, transport, bundle)
    same(result["evidence"], evidence, "Embedded inventory result differs from independently replayed cohort")
    if timing_out:
        same(recipe["callerTimeout"], plan["callerTimeout"], "Recipe changed prospective timeout")
        same(result["callerTimeout"], plan["callerTimeout"], "Result changed prospective timeout")
        drained = verify_seal(parse_json(artifacts["caller-timeout-drained.json"]), timeout.DRAIN_SCHEMA)
        expected = timeout.drain_receipt(transport["rows"][plan["callerTimeout"]["targetIndex"]])
        same(drained, expected, "Drain barrier does not bind the actually withheld response")
        same(driver["callerTimeoutDrained"], expected, "Driver did not wait for target handler drain")
    if cancelling:
        same(recipe["coordinatorCancellation"], plan["coordinatorCancellation"], "Recipe changed prospective coordinator cancellation")
        same(result["coordinatorCancellation"], plan["coordinatorCancellation"], "Result changed prospective coordinator cancellation")
        for field, key in (("coordinatorCancellationReady", "ready"), ("coordinatorCancellationApplied", "applied"), ("coordinatorCancellationDrained", "drained")):
            same(driver[field], bundle[key], "Driver did not retain its actual cancellation barrier: "+key)
    if loss:
        same(recipe["runtimeLoss"], plan["runtimeLoss"], "Recipe changed the prospective runtime loss")
        fields(result["runtimeLoss"], {"spec", "applied"})
        same(result["runtimeLoss"]["spec"], plan["runtimeLoss"], "Result changed prospective runtime loss")
        request = parse_json(artifacts["runtime-loss-request.json"])
        applied = parse_json(artifacts["runtime-loss-applied.json"])
        transport = fields(parse_json(artifacts["runtime-loss-transport.json"]), {"schemaVersion", "acceptedConnections", "resetConnections", "errors", "payloadsRead"})
        same(applied, result["runtimeLoss"]["applied"], "Result loss acknowledgement differs")
        same(applied, driver["runtimeLossApplied"], "Driver loss acknowledgement differs")
        verify_boundary(context, plan["runtimeLoss"], request, applied, cohort, records)
        require(applied["requestFileSha256"] == hashlib.sha256(artifacts["runtime-loss-request.json"]).hexdigest()
                and applied["runtimePid"] in result["ownedPids"] and applied["runtimePid"] not in driver["ownedPids"]
                and applied["runtimeExitCode"] == result["runtimeExitCode"], "Loss ownership, exit or request pin differs")
        require(transport["schemaVersion"] == "agat.decision.public-workflow-reset-guard.v1"
                and type(transport["acceptedConnections"]) is int and type(transport["resetConnections"]) is int
                and transport["acceptedConnections"] == transport["resetConnections"] == evidence["unavailableReturns"]
                and transport["errors"] == [] and transport["payloadsRead"] is False, "TCP resets disagree with durable unavailable returns")
    warmup = result["warmup"]; require(isinstance(warmup, list) and len(warmup) == 2, "Warmup denominator differs")
    case = next(row for row in context["inputs"] if row["contextEligible"]); warm_outcomes = Counter()
    for index, row in enumerate(warmup):
        fields(row, {"iteration", "caseId", "status", "reason", "observation", "callerMs", "wallMs"})
        require(type(row["iteration"]) is int and row["iteration"] == index and row["caseId"] == case["id"]
                and row["status"] in {"ok", "abstain"}, "Warmup input, order or result differs")
        warm_outcomes[observation(row, case, context["profile"])] += 1
    samples = result["samples"]; require(isinstance(samples, list) and len(samples) == 3, "Missing physical snapshots")
    values = []; starts = []; elapsed = -1
    for label, sample in zip(("ready_before_scoring", "after_warmup", "before_runtime_loss" if loss else "after_inventory"), samples):
        fields(sample, {"label", "elapsedMs", "health", "metricsRaw", "ownedPids", "processRaw", "counters", "serverStart"})
        number(sample["elapsedMs"], 0, result["elapsedMs"])
        require(sample["label"] == label and sample["elapsedMs"] > elapsed
                and sample["elapsedMs"] <= result["elapsedMs"] and sample["health"]["status"] == "ready"
                and sample["health"]["mode"] == "shadow" and sample["health"]["profileSha256"] == context["profileSha256"], "Invalid or reordered physical samples")
        same(profile_from_health(sample["health"]), context["profile"], "Physical snapshot profile differs")
        parsed, server_start = counters({key: value for key, value in sample.items() if key not in {"counters", "serverStart"}})
        same(parsed, sample["counters"], "Cached counters differ from raw metrics")
        same(server_start, sample["serverStart"], "Cached server origin differs")
        require(all(type(pid) is int and pid in result["ownedPids"] for pid in sample["ownedPids"]), "Unowned physical sample PID")
        values.append(parsed); starts.append(server_start); elapsed = sample["elapsedMs"]
    require(len(set(starts)) == 1 and all(value == 0 for value in values[0].values()), "Restarted or nonzero counter origin")
    same(values[1], {key: warm_outcomes.get(key, 0) for key in values[1]}, "Physical warmup handlers differ")
    same({key: values[2][key]-values[1][key] for key in values[2]},
         {key: evidence["physicalScheduledOutcomes"].get(key, 0) for key in values[2]}, "Physical handlers disagree with durable returns")
    require(raw == {"context": pinned_input(context_path, context_sha, 32*1024*1024),
                    "plan": pinned_input(directory/"plan.json", plan_sha, 32*1024*1024),
                    "result": pinned_input(directory/"result.json", result_sha, 64*1024*1024)}, "Artifact changed during replay")
    for name, expected in artifacts.items():
        require(pinned_input(directory/name, result["artifactSha256"][name], 16*1024*1024) == expected, "Consumed artifact changed")
    return sealed({"schemaVersion": "agat.decision.public-workflow-verification.v5" if cancelling else "agat.decision.public-workflow-verification.v4" if timing_out else "agat.decision.public-workflow-verification.v2" if loss else SCHEMA, "status": "pass", "contextProfileFileSha256": context_sha,
        "planFileSha256": plan_sha, "resultFileSha256": result_sha, "sourceCommit": plan["sourceCommit"], "sourceFilesCount": len(sources),
        "inventory": evidence, "warmupCalls": 2, "physicalScheduledHttpHandlers": sum(evidence["physicalScheduledOutcomes"].values()),
        **({"transportUnavailableReturns": evidence["unavailableReturns"], "transportResetConnections": transport["resetConnections"]} if loss else {}), "physicalHttpCounters": values[2],
        **({"transportTimeoutReturns": evidence["unavailableReturns"], "completedUndeliveredResponses": evidence["completedUndeliveredResponses"],
            "proxyPosts": evidence["proxyPosts"], "clientEofObserved": True, "runtimeRestarted": False} if timing_out else {}),
        **({"unknownCallerReturns": evidence["unknownCallerReturns"], "localCancelledCallerMs": evidence["localCancelledCallerMs"],
            "cancelRequestToEofMs": evidence["cancelRequestToEofMs"], "completedUndeliveredResponses": 1, "proxyPosts": evidence["proxyPosts"],
            "clientEofObserved": True, "runtimeRestarted": False, "lateObservationHttpStatus": 400} if cancelling else {}),
        "physicalHttpAccounting": "exact", "reportedCleanupComplete": True, "liveCleanupVerified": False, "ownersAppointed": False,
        "classificationAccuracyMeasured": False, "referenceLabels": 0, "sloAccepted": False, "routingEnabled": False, "qualification": "not_assessed"})
