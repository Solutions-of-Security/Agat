"""Bind a complete stored cohort to a prospective plan, without accepting an SLO."""
from __future__ import annotations

import hashlib
import json
import re
from decision_runtime.contracts import fields
from scripts.lib.decision_shadow_pilot import profile_identity, require, timestamp, utc_now, verify_plan
from scripts.lib.decision_shadow_sli import analyze, same_json

COHORT_SCHEMA = "agat.decision.shadow-cohort.v1"
LIMITS = {"instances": 1000, "stages": 10000, "activityBytes": 16 * 1024 * 1024, "exportBytes": 16 * 1024 * 1024}
TRACE_FIELDS = {"run", "truncated", "decisionStageInventory", "decisionAssignmentHistory", "decisionCallerAccounting", "decisionObservations"}


def identifier(value):
    require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", value), "Invalid cohort identity")
    return value


def verify_cohort(cohort, plan):
    fields(cohort, {"schemaVersion", "snapshotId", "observedAt", "scope", "snapshot", "limits", "counts", "runIdsSha256",
        "instances", "traces", "dataPolicy", "populationCoverageVerified", "eligibleWorkloadVerified", "httpAttemptInventoryVerified",
        "sloAccepted", "routingEnabled", "qualification"})
    require(cohort["schemaVersion"] == COHORT_SCHEMA, "Unsupported cohort schema")
    identifier(cohort["snapshotId"])
    config = plan["config"]
    expected_scope = {k: config[k] for k in ("projectId", "processId", "processVersion")}
    expected_scope.update(config["window"], boundary=config["cohortBoundary"])
    require(same_json(cohort["scope"], expected_scope), "Cohort and prospective plan scope differ")
    observed = timestamp(cohort["observedAt"], "cohort.observedAt")
    start = timestamp(config["window"]["startAt"], "startAt"); end = timestamp(config["window"]["endAt"], "endAt")
    require(end <= observed <= timestamp(utc_now(), "analysisTime"), "Cohort capture time is before window completion or in the future")
    snapshot = fields(cohort["snapshot"], {"dialect", "consistency", "storedCohortComplete", "truncated"})
    require(snapshot["dialect"] in ("sqlite", "postgresql") and snapshot["consistency"] == "single_database_snapshot"
            and snapshot["storedCohortComplete"] is True and snapshot["truncated"] is False, "Cohort is not a complete declared snapshot")
    fields(cohort["limits"], set(LIMITS))
    require(cohort["limits"] == LIMITS and all(type(v) is int for v in cohort["limits"].values()), "Cohort bounds differ")
    require(all(cohort[k] is False for k in ("populationCoverageVerified", "eligibleWorkloadVerified", "httpAttemptInventoryVerified", "sloAccepted", "routingEnabled"))
            and cohort["qualification"] == "not_assessed", "Cohort cannot grant qualification or SLO acceptance")
    policy = {"taskInputsIncluded": False, "questionOptionsIncluded": False, "primaryOutputsIncluded": False,
              "eventsIncluded": False, "artifactsIncluded": False, "decisionProfileAndResultMetadataIncluded": True}
    fields(cohort["dataPolicy"], set(policy))
    require(cohort["dataPolicy"] == policy and all(type(v) is bool for v in cohort["dataPolicy"].values()), "Cohort data policy differs")
    counts = fields(cohort["counts"], {"instances", "runs", "storedStages", "storedShadowStages"})
    require(all(type(v) is int and v >= 0 for v in counts.values()) and counts["instances"] == counts["runs"] <= 1000
            and counts["storedShadowStages"] <= counts["storedStages"] <= 10000, "Invalid cohort counts")
    instances = cohort["instances"]; traces = cohort["traces"]
    require(isinstance(instances, list) and isinstance(traces, list) and len(instances) == len(traces) == counts["instances"], "Cohort omitted an instance or trace")
    instance_ids = set(); run_ids = []; last = None
    for instance in instances:
        fields(instance, {"instanceId", "runId", "processVersion", "createdAt", "status", "replayOfInstanceId", "replayMode"})
        identity = identifier(instance["instanceId"]); run = identifier(instance["runId"])
        require(identity not in instance_ids and run not in run_ids, "Duplicate cohort instance/run")
        instance_ids.add(identity); run_ids.append(run)
        created = timestamp(instance["createdAt"], "instance.createdAt")
        require(start <= created < end and type(instance["processVersion"]) is int and instance["processVersion"] == config["processVersion"],
                "Cohort instance is outside the planned version/window")
        order = (instance["createdAt"], identity)
        require(last is None or last <= order, "Cohort order differs"); last = order
        require(isinstance(instance["status"], str) and instance["status"] in ("queued", "running", "waiting_approval", "waiting_external", "compensating", "completed", "failed", "cancelled")
                and instance["replayMode"] in ("live", "safe"), "Unknown cohort instance state")
        if instance["replayOfInstanceId"] is not None: identifier(instance["replayOfInstanceId"])
        require(instance["replayMode"] != "safe" or instance["replayOfInstanceId"] is not None, "Safe replay lacks its source")
    run_digest = hashlib.sha256(json.dumps(run_ids, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    require(cohort["runIdsSha256"] == run_digest, "Ordered cohort run inventory SHA differs")
    stages_seen = set(); total = 0
    for trace, run in zip(traces, run_ids):
        fields(trace, TRACE_FIELDS); fields(trace["run"], {"id"})
        require(trace["run"]["id"] == run and trace["truncated"] is False, "Trace run/completeness binding differs")
        stage_sets = []
        for key in ("decisionStageInventory", "decisionAssignmentHistory", "decisionCallerAccounting"):
            value = fields(trace[key], {"schemaVersion", "scope", "stages"})
            require(isinstance(value["stages"], list) and len(value["stages"]) <= 10000, "Invalid cohort stage count")
            ids = [identifier(s["stageId"]) for s in value["stages"]]
            require(len(ids) == len(set(ids)), "Repeated cohort stage")
            stage_sets.append(set(ids))
        require(stage_sets[0] == stage_sets[1] == stage_sets[2] and not stages_seen.intersection(stage_sets[0]), "Cohort omitted or repeated stage ledgers")
        stages_seen.update(stage_sets[0]); total += len(stage_sets[0])
    require(total == counts["storedShadowStages"], "Cohort shadow stage count differs")
    return cohort


def compare_target(interval, target, denominator_verified, intents):
    if not denominator_verified or intents == 0: status = "not_evaluable"
    elif interval["lower"] >= target: status = "conservative_target_met"
    elif interval["upper"] < target: status = "target_not_met"
    else: status = "indeterminate"
    return {"status": status, "target": target, "measuredRatio": interval, "sloAccepted": False}


def evaluate(plan, cohort, profile, raw_profile):
    verify_plan(plan)
    require(plan["status"] == "ready_for_review" and not plan["missingFields"], "Complete the prospective technical plan before cohort evaluation")
    pin = profile_identity(profile, raw_profile, plan["profile"]["fileSha256"], plan["profile"]["identity"])
    require(same_json(pin, plan["profile"]), "Pilot profile declaration differs from pinned bytes")
    verify_cohort(cohort, plan)
    config = plan["config"]; expected_sha = plan["profile"]["sha256"]
    summary = analyze(cohort["traces"], profile, config["targets"]["latencyThresholdMs"],
        profile_json_bytes=raw_profile if pin["identity"] == "coordinator_json_bytes" else None, trace_limit=1000, allow_empty=True)
    caller = summary["callerAccounting"]
    profile_mismatches = timeout_mismatches = 0
    for trace in cohort["traces"]:
        for stage in trace["decisionAssignmentHistory"]["stages"]:
            for assignment in stage["assignments"]:
                profile_mismatches += int(assignment["profileSha256"] != expected_sha)
                timeout_mismatches += int(assignment["callerTimeoutMs"] != config["targets"]["callerTimeoutMs"])
    gaps = list(dict.fromkeys(summary["dataGaps"] + (["assignment_profile_differs_from_plan"] if profile_mismatches else [])
                            + (["caller_timeout_differs_from_plan"] if timeout_mismatches else [])))
    intents = caller["counts"]["intents"]
    if intents == 0: gaps.append("no_negotiated_caller_intents")
    denominator_verified = caller["callerIntentInventoryVerified"] and not profile_mismatches and not timeout_mismatches
    targets = {"withinCallerDeadline": compare_target(caller["withinCallerDeadlineRatio"], config["targets"]["boundResultRatio"], denominator_verified, intents),
               "timelyBoundResult": compare_target(caller["timelyBoundResultRatio"], config["targets"]["timelyBoundResultRatio"], denominator_verified, intents)}
    return {"measurementStatus": "insufficient_data" if gaps else "stored_cohort_measured", "dataGaps": gaps,
        "scope": "planned_stored_process_cohort", "cohortBindingVerified": True, "serverDeclaredStoredCohortComplete": True,
        "snapshotId": cohort["snapshotId"], "observedAt": cohort["observedAt"], "cohortScope": cohort["scope"],
        "cohortCounts": cohort["counts"], "runIdsSha256": cohort["runIdsSha256"], "planSha256": plan["sha256"],
        "profileSha256": expected_sha, "assignmentProfileMismatches": profile_mismatches, "callerTimeoutMismatches": timeout_mismatches,
        "callerDenominatorBindingVerified": denominator_verified, "targetComparison": targets, "sli": summary,
        "agreementVerified": False, "sloAccepted": False, "populationCoverageVerified": False,
        "httpAttemptInventoryVerified": False, "routingEnabled": False, "qualification": "not_assessed"}
