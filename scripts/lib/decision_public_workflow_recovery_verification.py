"""Independent raw-pinned replay of an owned active-handler crash and replacement."""
import hashlib
import json
from pathlib import Path

from decision_runtime.artifacts import sealed, verify_seal
from decision_runtime.contracts import fields, number, parse_json
from scripts.lib.decision_public_load import validate_context
from scripts.lib.decision_public_load_verification import CONTEXT_PATHS, same, sources_at
from scripts.lib.decision_public_sources import pinned_input
from scripts.lib.decision_public_workflow import PROFILE_PATH, SOURCE_PATHS, shared_config, verify_inventory
from scripts.lib.decision_public_workflow_verification import ARTIFACTS as BASE_ARTIFACTS
from scripts.lib import decision_public_workflow_recovery as recovery
from scripts.lib.decision_shadow_pilot import require, timestamp

SCHEMA = "agat.decision.public-workflow-verification.v3"


def verify(root, directory, context_path, *, context_sha, plan_sha, result_sha):
    raw = {"context": pinned_input(context_path, context_sha, 32*1024*1024),
           "plan": pinned_input(directory/"plan.json", plan_sha, 32*1024*1024),
           "result": pinned_input(directory/"result.json", result_sha, 64*1024*1024)}
    context = validate_context(parse_json(raw["context"])); plan = verify_seal(parse_json(raw["plan"]), recovery.LAUNCH_PLAN)
    result = verify_seal(parse_json(raw["result"]), recovery.LAUNCH_RESULT)
    fields(plan, {"schemaVersion", "sha256", "createdAt", "sourceCommit", "sourceFiles", "contextProfileFileSha256", "context", "config", "runtime",
        "manifestFileSha256", "mode", "primary", "warmupCount", "runtimeRecovery", "ownersAppointed", "referenceLabels", "sloAccepted", "routingEnabled", "qualification"})
    fields(result, {"schemaVersion", "sha256", "status", "planSha256", "evidence", "runtimeRecovery", "warmup", "samples", "failure", "ownedPids", "remainingOwnedPids",
        "cleanupErrors", "runtimeExitCodes", "driverExitCode", "artifactSha256", "elapsedMs", "referenceLabels", "classificationAccuracyMeasured",
        "ownersAppointed", "sloAccepted", "routingEnabled", "qualification"})
    for value in (plan, result):
        require(type(value["referenceLabels"]) is int and value["referenceLabels"] == 0 and value["ownersAppointed"] is False
                and value["sloAccepted"] is False and value["routingEnabled"] is False and value["qualification"] == "not_assessed",
                "Lab crash/recovery receipt grants authority or human labels")
    require(plan["contextProfileFileSha256"] == context_sha and plan["manifestFileSha256"] == context["manifestFileSha256"]
            and plan["mode"] == "serial_closed_model_integration" and plan["primary"] == "fixture_chat_completions"
            and type(plan["warmupCount"]) is int and plan["warmupCount"] == 4, "Wrong scope, context or two-epoch warmup count")
    same(plan["context"], context, "Embedded context differs from pinned original"); same(plan["config"], shared_config(context), "Published decision configuration differs")
    same(plan["runtime"], context["tokenizerEnvironment"], "Frozen runtime dependencies differ")
    same(plan["runtimeRecovery"], recovery.recovery_spec(context, plan["runtimeRecovery"]["targetIndex"]), "Posthoc or unsupported crash/recovery specification")
    require(timestamp(context["createdAt"], "context.createdAt") <= timestamp(plan["createdAt"], "plan.createdAt"), "Plan predates its context")
    require(result["status"] == "observed" and result["failure"] is None and result["planSha256"] == plan["sha256"]
            and result["remainingOwnedPids"] == [] and result["cleanupErrors"] == [] and result["classificationAccuracyMeasured"] is False
            and type(result["driverExitCode"]) is int and result["driverExitCode"] == 0
            and isinstance(result["runtimeExitCodes"], list) and len(result["runtimeExitCodes"]) == 2, "Run or reported cleanup failed")
    number(result["elapsedMs"], 0, plan["runtimeRecovery"]["totalDeadlineMs"])
    require(isinstance(result["ownedPids"], list) and result["ownedPids"] and len(set(result["ownedPids"])) == len(result["ownedPids"])
            and all(type(pid) is int and 0 < pid < 2**31 for pid in result["ownedPids"]), "Invalid process ownership record")
    sources_at(root, context["sourceCommit"], context["sourceFiles"], CONTEXT_PATHS)
    sources = sources_at(root, plan["sourceCommit"], plan["sourceFiles"], SOURCE_PATHS+recovery.SOURCE_PATHS)
    require({"scripts/lib/decision_public_workflow_recovery.py", "scripts/lib/decision_public_workflow_recovery_verification.py",
             *recovery.SOURCE_PATHS} <= set(sources), "Crash/recovery contributors omitted")
    require(hashlib.sha256(sources[PROFILE_PATH]).hexdigest() == context["profileFileSha256"], "Historical profile bytes differ")
    same(parse_json(sources[PROFILE_PATH]), context["profile"], "Historical profile semantics differ")
    implementation = hashlib.sha256()
    for name in sorted(name for name in sources if Path(name).parent == Path("decision_runtime") and name.endswith(".py")):
        implementation.update(Path(name).name.encode()+b"\0"+sources[name]+b"\0")
    require(implementation.hexdigest() == context["profile"]["model"]["implementationSha256"], "Frozen runtime implementation differs")
    requirements = dict(line.split("==") for line in sources["decision_runtime/requirements-mlx.txt"].decode().splitlines() if line and not line.startswith("#"))
    same(plan["runtime"], {"python": "3.13.12", "machine": "arm64", "packages": requirements}, "Historical native runtime pins differ")
    fields(result["artifactSha256"], BASE_ARTIFACTS|recovery.ARTIFACTS)
    artifacts = {name: pinned_input(directory/name, result["artifactSha256"][name], 16*1024*1024) for name in result["artifactSha256"]}
    recipe = parse_json(artifacts["workflow-plan.json"]); driver = parse_json(artifacts["workflow-driver.json"]); cohort = parse_json(artifacts["cohort.http.json"])
    fields(recipe, {"schemaVersion", "mode", "primary", "processId", "processVersion", "projectId", "startAt", "scopeEndRule", "config", "runtimeRecovery",
        "profileSha256", "inputs", "ownersAppointed", "routingEnabled", "qualification", "graphSha256"})
    fields(driver, {"status", "primary", "primaryCalls", "routes", "nodeVersion", "ownedPids", "workerExitCode", "actualWindow", "runtimeRecovery",
        "unauthenticatedStatus", "authenticatedStatus", "ownersAppointed", "routingEnabled", "qualification"})
    fields(cohort, {"schemaVersion", "snapshotId", "observedAt", "scope", "snapshot", "limits", "counts", "runIdsSha256", "instances", "traces", "dataPolicy",
        "populationCoverageVerified", "eligibleWorkloadVerified", "httpAttemptInventoryVerified", "sloAccepted", "routingEnabled", "qualification"})
    fields(cohort["snapshot"], {"dialect", "consistency", "storedCohortComplete", "truncated"})
    require(cohort["snapshot"]["dialect"] in {"sqlite", "postgresql"} and all(cohort[key] is False for key in
            ("populationCoverageVerified", "eligibleWorkloadVerified", "httpAttemptInventoryVerified")), "Lab census promoted to customer coverage")
    same(cohort["limits"], {"instances": 1000, "stages": 10000, "activityBytes": 16777216, "exportBytes": 16777216}, "Census limits differ")
    require(type(cohort["counts"]["storedStages"]) is int and len(context["inputs"]) <= cohort["counts"]["storedStages"] <= 10000
            and cohort["runIdsSha256"] == hashlib.sha256(json.dumps([row["runId"] for row in cohort["instances"]], separators=(",", ":")).encode()).hexdigest(),
            "Raw cohort stored-stage count/run-set digest differs")
    require(isinstance(recipe["graphSha256"], str) and len(recipe["graphSha256"]) == 64
            and all(c in "0123456789abcdef" for c in recipe["graphSha256"]), "Invalid graph pin")
    require(timestamp(recipe["startAt"], "recipe.startAt") > timestamp(plan["createdAt"], "plan.createdAt")
            and recipe["scopeEndRule"] == "after_full_input_inventory_and_worker_drain", "Recipe was not prospectively scoped")
    records = [parse_json(line) for line in artifacts["workflow-routes.jsonl"].splitlines()]
    same(driver["routes"], records, "Raw workflow journal differs or is reordered")
    require(driver["status"] == "observed" and driver["primary"] == "fixture_chat_completions"
            and type(driver["primaryCalls"]) is int and driver["primaryCalls"] == len(context["inputs"])
            and type(driver["workerExitCode"]) is int and driver["workerExitCode"] == 0
            and type(driver["unauthenticatedStatus"]) is int and driver["unauthenticatedStatus"] == 401
            and type(driver["authenticatedStatus"]) is int and driver["authenticatedStatus"] == 200
            and driver["ownersAppointed"] is False and driver["routingEnabled"] is False and driver["qualification"] == "not_assessed", "Driver audit/auth/authority failed")
    same(driver["actualWindow"], {key: cohort["scope"][key] for key in ("startAt", "endAt")}, "Driver and census windows differ")
    require(isinstance(driver["ownedPids"], list) and len(set(driver["ownedPids"])) == len(driver["ownedPids"]) == 2
            and all(type(pid) is int and pid in result["ownedPids"] for pid in driver["ownedPids"]), "Missing worker/driver ownership record")
    same(recipe["runtimeRecovery"], plan["runtimeRecovery"], "Recipe changed prospective crash/recovery")
    evidence = verify_inventory(context, recipe, cohort, records); same(result["evidence"], evidence, "Embedded inventory disagrees with independent durable replay")
    fields(result["runtimeRecovery"], {"spec", "armed", "crashed", "recovered"}); fields(driver["runtimeRecovery"], {"armed", "crashed", "recovered"})
    same(result["runtimeRecovery"]["spec"], plan["runtimeRecovery"], "Result changed prospective crash/recovery")
    receipts = recovery.verify_boundary(context, plan["runtimeRecovery"], artifacts, cohort, records, result["ownedPids"])
    require(receipts["armed"]["runtimePid"] not in driver["ownedPids"] and receipts["recovered"]["runtimePid"] not in driver["ownedPids"], "Worker/driver misrepresented as a model runtime")
    for key in ("armed", "crashed", "recovered"):
        same(result["runtimeRecovery"][key], receipts[key], "Result barrier receipt differs"); same(driver["runtimeRecovery"][key], receipts[key], "Driver barrier receipt differs")
    physical = recovery.verify_physical(context, plan["runtimeRecovery"], result, cohort, records, receipts)
    require(raw == {"context": pinned_input(context_path, context_sha, 32*1024*1024), "plan": pinned_input(directory/"plan.json", plan_sha, 32*1024*1024),
                    "result": pinned_input(directory/"result.json", result_sha, 64*1024*1024)}, "Artifact changed during replay")
    for name, expected in artifacts.items():
        require(pinned_input(directory/name, result["artifactSha256"][name], 16*1024*1024) == expected, "Consumed receipt, journal or log changed")
    return sealed({"schemaVersion": SCHEMA, "status": "pass", "contextProfileFileSha256": context_sha, "planFileSha256": plan_sha,
        "resultFileSha256": result_sha, "sourceCommit": plan["sourceCommit"], "sourceFilesCount": len(sources), "inventory": evidence,
        **physical, "transportUnavailableReturns": evidence["unavailableReturns"], "transportResetConnections": receipts["transport"]["resetConnections"],
        "reportedCleanupComplete": True, "liveCleanupVerified": False, "verificationModelCalls": 0, "gpuForwardInterruptionEstablished": False,
        "ownersAppointed": False, "classificationAccuracyMeasured": False, "referenceLabels": 0, "sloAccepted": False,
        "routingEnabled": False, "qualification": "not_assessed"})
