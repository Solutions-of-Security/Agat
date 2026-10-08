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

SCHEMA = "agat.decision.public-workflow-verification.v1"
PLAN_SCHEMA = "agat.decision.public-workflow-launch-plan.v1"
RESULT_SCHEMA = "agat.decision.public-workflow-launch-result.v1"
ARTIFACTS = {"workflow-plan.json", "workflow-driver.json", "workflow-routes.jsonl", "cohort.http.json", "worker.log", "runtime.log", "driver.log"}


def verify(root, directory, context_path, *, context_sha, plan_sha, result_sha):
    raw = {"context": pinned_input(context_path, context_sha, 32*1024*1024),
           "plan": pinned_input(directory/"plan.json", plan_sha, 32*1024*1024),
           "result": pinned_input(directory/"result.json", result_sha, 64*1024*1024)}
    context = validate_context(parse_json(raw["context"]))
    plan = verify_seal(parse_json(raw["plan"]), PLAN_SCHEMA); result = verify_seal(parse_json(raw["result"]), RESULT_SCHEMA)
    fields(plan, {"schemaVersion", "sha256", "createdAt", "sourceCommit", "sourceFiles", "contextProfileFileSha256", "context", "config", "runtime",
                  "manifestFileSha256", "mode", "primary", "warmupCount", "ownersAppointed", "referenceLabels", "sloAccepted", "routingEnabled", "qualification"})
    fields(result, {"schemaVersion", "sha256", "status", "planSha256", "evidence", "warmup", "samples", "failure", "ownedPids", "remainingOwnedPids",
                    "cleanupErrors", "runtimeExitCode", "driverExitCode", "artifactSha256", "elapsedMs", "referenceLabels", "classificationAccuracyMeasured",
                    "ownersAppointed", "sloAccepted", "routingEnabled", "qualification"})
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
    sources = sources_at(root, plan["sourceCommit"], plan["sourceFiles"], SOURCE_PATHS)
    require(hashlib.sha256(sources[PROFILE_PATH]).hexdigest() == context["profileFileSha256"], "Historical profile bytes differ")
    same(parse_json(sources[PROFILE_PATH]), context["profile"], "Historical profile semantics differ")
    implementation = hashlib.sha256()
    for name in sorted(name for name in sources if Path(name).parent == Path("decision_runtime") and name.endswith(".py")):
        implementation.update(Path(name).name.encode()+b"\0"+sources[name]+b"\0")
    require(implementation.hexdigest() == context["profile"]["model"]["implementationSha256"], "Historical runtime implementation differs")
    requirements = dict(line.split("==") for line in sources["decision_runtime/requirements-mlx.txt"].decode().splitlines() if line and not line.startswith("#"))
    same(plan["runtime"], {"python": "3.13.12", "machine": "arm64", "packages": requirements}, "Historical runtime pins differ")
    fields(result["artifactSha256"], ARTIFACTS)
    artifacts = {name: pinned_input(directory/name, result["artifactSha256"][name], 16*1024*1024) for name in ARTIFACTS}
    recipe = parse_json(artifacts["workflow-plan.json"]); driver = parse_json(artifacts["workflow-driver.json"]); cohort = parse_json(artifacts["cohort.http.json"])
    fields(recipe, {"schemaVersion", "mode", "primary", "processId", "processVersion", "projectId", "startAt", "scopeEndRule", "config",
                    "profileSha256", "inputs", "ownersAppointed", "routingEnabled", "qualification", "graphSha256"})
    fields(driver, {"status", "primary", "primaryCalls", "routes", "nodeVersion", "ownedPids", "workerExitCode", "actualWindow",
                    "unauthenticatedStatus", "authenticatedStatus", "ownersAppointed", "routingEnabled", "qualification"})
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
    evidence = verify_inventory(context, recipe, cohort, records)
    same(result["evidence"], evidence, "Embedded inventory result differs from independently replayed cohort")
    warmup = result["warmup"]; require(isinstance(warmup, list) and len(warmup) == 2, "Warmup denominator differs")
    case = next(row for row in context["inputs"] if row["contextEligible"]); warm_outcomes = Counter()
    for index, row in enumerate(warmup):
        fields(row, {"iteration", "caseId", "status", "reason", "observation", "callerMs", "wallMs"})
        require(type(row["iteration"]) is int and row["iteration"] == index and row["caseId"] == case["id"]
                and row["status"] in {"ok", "abstain"}, "Warmup input, order or result differs")
        warm_outcomes[observation(row, case, context["profile"])] += 1
    samples = result["samples"]; require(isinstance(samples, list) and len(samples) == 3, "Missing physical snapshots")
    values = []; starts = []; elapsed = -1
    for label, sample in zip(("ready_before_scoring", "after_warmup", "after_inventory"), samples):
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
    return sealed({"schemaVersion": SCHEMA, "status": "pass", "contextProfileFileSha256": context_sha,
        "planFileSha256": plan_sha, "resultFileSha256": result_sha, "sourceCommit": plan["sourceCommit"], "sourceFilesCount": len(sources),
        "inventory": evidence, "warmupCalls": 2, "physicalScheduledHttpHandlers": len(context["inputs"]), "physicalHttpCounters": values[2],
        "physicalHttpAccounting": "exact", "reportedCleanupComplete": True, "liveCleanupVerified": False, "ownersAppointed": False,
        "classificationAccuracyMeasured": False, "referenceLabels": 0, "sloAccepted": False, "routingEnabled": False, "qualification": "not_assessed"})
