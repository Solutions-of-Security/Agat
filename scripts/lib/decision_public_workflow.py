"""Whole public inventory through worker/coordinator; primary is a fixture."""
from collections import Counter
from copy import deepcopy

from decision_runtime.contracts import Request, fields, fingerprint, number
from decision_runtime.artifacts import sealed
from decision_runtime.metrics import outcome
from scripts.lib.decision_performance import validate_result
from scripts.lib.decision_public_load import validate_context
from scripts.lib.decision_public_load_verification import distribution
from scripts.lib.decision_shadow_pilot import require, timestamp
from scripts.lib.decision_public_workflow_loss import PLAN_SCHEMA as LOSS_PLAN_SCHEMA, RESULT_SCHEMA as LOSS_RESULT_SCHEMA, loss_spec
from scripts.lib import decision_public_workflow_recovery as recovery
from scripts.lib import decision_public_workflow_timeout as timeout
from scripts.lib import decision_public_workflow_cancellation as cancellation

PLAN_SCHEMA = "agat.decision.public-workflow-plan.v1"
RESULT_SCHEMA = "agat.decision.public-workflow-result.v1"
PROFILE_PATH = "docs/qualification/local-decisions/performance/profiles/runtime-0.12.3-wired-4096.json"
SOURCE_PATHS = ["decision_runtime", "workers", "apps/coordinator/src", "scripts/lib",
    "scripts/run-public-support-workflow.py", "scripts/run-public-support-workflow.mts",
    "scripts/test/test_decision_public_workflow.py", "scripts/run-public-support-load.py",
    "scripts/run-decision-arrival-rate.py", "scripts/verify-decision-arrival-rate.py",
    "scripts/run-temporal-real-rag.py", "scripts/profile-embedding-rag.py", "package.json", "package-lock.json",
    "apps/coordinator/package.json", PROFILE_PATH, "docs/qualification/local-decisions/policy.shadow.v1.json",
    "docs/qualification/local-decisions/performance/evidence/2026-09-28/rag-http-isolation/resident-isolated/rag-workflow-plan.json"]


def shared_config(context):
    validate_context(context)
    first = context["inputs"][0]["request"]
    config = {key: deepcopy(first[key]) for key in ("kind", "question", "options")}
    require(all(fingerprint({key: row["request"][key] for key in config}) == fingerprint(config)
                for row in context["inputs"]), "One published workflow requires identical decision configuration")
    return config


def verify_inventory(context, plan, cohort, routes, transport=None, cancellation_receipts=None):
    """Account against the original case list, not just returned observations."""
    validate_context(context); config = shared_config(context); count = len(context["inputs"])
    loss = plan["schemaVersion"] == LOSS_PLAN_SCHEMA
    recovering = plan["schemaVersion"] == recovery.PLAN_SCHEMA
    timing_out = plan["schemaVersion"] == timeout.PLAN_SCHEMA
    active_cancelling = plan["schemaVersion"] == "agat.decision.public-workflow-plan.v6"
    cancelling = active_cancelling or plan["schemaVersion"] == cancellation.PLAN_SCHEMA
    if active_cancelling:
        from scripts.lib import decision_public_workflow_active_integration as active_integration
        from scripts.lib.decision_public_workflow_active_cancellation import active_spec
        require(fingerprint(plan.get("coordinatorTrigger")) == fingerprint(active_integration.TRIGGER), "Active caller trigger differs")
    if loss:
        require(fingerprint(plan.get("runtimeLoss")) == fingerprint(loss_spec(context, plan["runtimeLoss"]["beforeIndex"])), "Runtime loss was not prospectively specified")
    else:
        require("runtimeLoss" not in plan, "Historical v1 cannot admit a runtime loss")
    if recovering:
        require(fingerprint(plan.get("runtimeRecovery")) == fingerprint(recovery.recovery_spec(context, plan["runtimeRecovery"]["targetIndex"])),
                "Runtime recovery was not prospectively specified")
    else:
        require("runtimeRecovery" not in plan, "Historical v1/v2 cannot admit crash/recovery")
    if timing_out:
        require(fingerprint(plan.get("callerTimeout")) == fingerprint(timeout.timeout_spec(context, plan["callerTimeout"]["targetIndex"])),
                "Caller timeout was not prospectively specified")
    else:
        require("callerTimeout" not in plan and (transport is None or cancelling), "Historical protocols cannot admit a caller timeout")
    if cancelling:
        require(fingerprint(plan.get("coordinatorCancellation")) == fingerprint((active_spec if active_cancelling else cancellation.cancellation_spec)(context, plan["coordinatorCancellation"]["targetIndex"]))
                and cancellation_receipts is not None and transport is not None, "Coordinator cancellation was not prospectively specified")
    else:
        require("coordinatorCancellation" not in plan and cancellation_receipts is None, "Historical protocols cannot admit coordinator cancellation")
    require(plan["schemaVersion"] in {PLAN_SCHEMA, LOSS_PLAN_SCHEMA, recovery.PLAN_SCHEMA, timeout.PLAN_SCHEMA, cancellation.PLAN_SCHEMA, "agat.decision.public-workflow-plan.v6"} and plan["mode"] == "serial_closed_model_integration"
            and plan["primary"] == "fixture_chat_completions" and plan["ownersAppointed"] is False
            and plan["routingEnabled"] is False and plan["qualification"] == "not_assessed"
            and type(plan["processVersion"]) is int and plan["processVersion"] == 1
            and plan["profileSha256"] == context["profileSha256"] and fingerprint(plan["config"]) == fingerprint(config)
            and plan["inputs"] == [{"caseId": row["id"], "inputSha256": row["inputSha256"]} for row in context["inputs"]],
            "Workflow plan changed inventory, profile, primary or authority")
    start = timestamp(plan["startAt"], "plan.startAt")
    require(cohort["schemaVersion"] == "agat.decision.shadow-cohort.v1" and cohort["snapshot"]["storedCohortComplete"] is True
            and cohort["snapshot"]["truncated"] is False and cohort["snapshot"]["consistency"] == "single_database_snapshot"
            and all(type(cohort["counts"][key]) is int for key in ("instances", "runs", "storedShadowStages"))
            and cohort["counts"]["instances"] == cohort["counts"]["runs"] == cohort["counts"]["storedShadowStages"] == count,
            "Whole stored cohort was not captured")
    scope = cohort["scope"]
    require(scope["projectId"] == plan["projectId"] and scope["processId"] == plan["processId"]
            and type(scope["processVersion"]) is int and scope["processVersion"] == 1 and scope["startAt"] == plan["startAt"]
            and scope["boundary"] == "process_instance_created_at_half_open", "Wrong workflow scope")
    end = timestamp(scope["endAt"], "scope.endAt")
    require(start < end <= timestamp(cohort["observedAt"], "observedAt") and (end-start).total_seconds() <= 300,
            "Incomplete or excessive actual creation window")
    require(cohort["sloAccepted"] is False and cohort["routingEnabled"] is False and cohort["qualification"] == "not_assessed",
            "Lab cohort promoted to customer qualification")
    require(fingerprint(cohort["dataPolicy"]) == fingerprint({"taskInputsIncluded": False, "questionOptionsIncluded": False,
            "primaryOutputsIncluded": False, "eventsIncluded": False, "artifactsIncluded": False,
            "decisionProfileAndResultMetadataIncluded": True}), "Cohort exports task or primary content")
    require(len(routes) == len(cohort["instances"]) == len(cohort["traces"]) == count
            and len({row["runId"] for row in routes}) == len({row["instanceId"] for row in routes}) == count,
            "Missing, extra or repeated workflow records")
    instances = {row["runId"]: row for row in cohort["instances"]}; traces = {row["run"]["id"]: row for row in cohort["traces"]}
    require(set(instances) == set(traces) == {row["runId"] for row in routes}, "Cohort run identities differ")
    statuses = Counter(); physical = Counter(); timings = []; unavailable_timings = []; expected_profiles = context["profile"]
    for index, (case, route) in enumerate(zip(context["inputs"], routes)):
        cancelled = cancelling and index == plan["coordinatorCancellation"]["targetIndex"]
        expected_state = "cancelled" if cancelled else "completed"
        fields(route, {"index", "caseId", "inputSha256", "instanceId", "runId", "stageId", "primaryCalls", "primaryBranch", "wrongBranch", "runStatus"})
        require(type(route["index"]) is int and route["index"] == index and route["caseId"] == case["id"]
                and route["inputSha256"] == case["inputSha256"] and type(route["primaryCalls"]) is int and route["primaryCalls"] == 1
                and route["primaryBranch"] is (not cancelled) and route["wrongBranch"] is False and route["runStatus"] == expected_state,
                "Reordered input, retried primary or changed primary branch")
        instance = instances[route["runId"]]
        require(instance["instanceId"] == route["instanceId"] and type(instance["processVersion"]) is int and instance["processVersion"] == 1
                and instance["status"] == expected_state and instance["replayOfInstanceId"] is None and instance["replayMode"] != "safe"
                and start <= timestamp(instance["createdAt"], "instance.createdAt") < end, "Wrong instance or replay in creation cohort")
        trace = traces[route["runId"]]
        require(trace["truncated"] is False, "Truncated cohort trace")
        inventories = [trace[key]["stages"] for key in ("decisionStageInventory", "decisionCallerAccounting", "decisionAssignmentHistory")]
        require(all(len(rows) == 1 and rows[0]["stageId"] == route["stageId"] for rows in inventories)
                and len(trace["decisionObservations"]) == (0 if cancelled else 1), "Shadow stage omitted, repeated or rebound")
        stage, caller, history = (rows[0] for rows in inventories)
        require(stage["stageStatus"] == expected_state and stage["assigned"] is True and stage["observationRecorded"] is (not cancelled)
                and stage["inputSha256"] == case["inputSha256"] and stage["profileSha256"] == context["profileSha256"]
                and type(stage["callerTimeoutMs"]) is int and stage["callerTimeoutMs"] == 10000,
                "Input was transformed, profile changed or shadow not recorded")
        require(caller["coverage"] == history["coverage"] == "complete" and len(caller["assignments"]) == len(history["assignments"]) == 1,
                "Incomplete or retried assignment ledger")
        intent = caller["assignments"][0]; assigned = history["assignments"][0]
        require(intent["assignmentId"] == assigned["assignmentId"] and type(intent["stageAttempt"]) is int and intent["stageAttempt"] == 1
                and type(assigned["stageAttempt"]) is int and assigned["stageAttempt"] == 1
                and intent["negotiated"] is True and intent["intent"] is True, "Missing actual caller intent or assignment")
        if cancelled:
            require(intent["returned"] is None and intent["outcome"] == "return_missing"
                    and assigned["outcome"] == "ended_without_observation" and assigned["observation"] is None
                    and assigned["inputSha256"] == case["inputSha256"] and assigned["profileSha256"] == context["profileSha256"]
                    and type(assigned["callerTimeoutMs"]) is int and assigned["callerTimeoutMs"] == 10000,
                    "Cancelled lease accepted a late result, lost its input or fabricated a caller return")
            continue
        require(intent["outcome"] == "returned" and assigned["outcome"] == "recorded", "Actual durable caller return is missing")
        exported = trace["decisionObservations"][0]
        fields(exported, {"stageId", "profileSha256", "inputSha256", "callerTimeoutMs", "observation"})
        require(exported["stageId"] == route["stageId"] and exported["inputSha256"] == assigned["inputSha256"] == case["inputSha256"]
                and exported["profileSha256"] == assigned["profileSha256"] == context["profileSha256"]
                and exported["callerTimeoutMs"] == assigned["callerTimeoutMs"] == 10000
                and fingerprint(exported["observation"]) == fingerprint(assigned["observation"]), "Observation binding differs")
        timed_out = timing_out and index == plan["callerTimeout"]["targetIndex"]
        lost = (loss and index >= plan["runtimeLoss"]["beforeIndex"]) or (recovering and index == plan["runtimeRecovery"]["targetIndex"]) or timed_out
        observation = fields(exported["observation"], {"mode", "fallback", "status", "reason", "callerTiming"} | (set() if lost else {"result"}))
        require(observation["mode"] == "shadow" and observation["fallback"] == "primary", "Shadow routing changed")
        if lost:
            require(observation["status"] == "unavailable" and observation["reason"] == ("timeout" if timed_out else "unreachable"), "Fault return was not recorded as actual transport unavailability")
            result = observation
        else:
            request = Request.from_dict({**case["request"], "id": route["stageId"]})
            validate_result(observation["result"], request, expected_profiles)
            result = observation["result"]
            require(observation["status"] == result["status"] and observation["reason"] == result["reason"], "Stored status differs")
            require((result["status"] in {"ok", "abstain"} and case["contextEligible"] is True and result["inputTokens"] == case["inputTokens"])
                    or (result["status"] == "error" and result["reason"] == "context_too_long" and case["contextEligible"] is False),
                    "Unexpected computation or missing whole-input rejection")
        timing = fields(observation["callerTiming"], {"schemaVersion", "clock", "boundary", "durationMs"})
        require(timing["schemaVersion"] == "agat.decision.caller-timing.v1" and timing["clock"] == "monotonic"
                and timing["boundary"] == "local_http_call" and number(timing["durationMs"], 0, 15000 if timing_out else 10001) >= (0 if lost else result["durationMs"]-.1)
                and fingerprint(intent["returned"]) == fingerprint({"callerTiming": timing, "status": result["status"], "reason": result["reason"]}),
                "Caller timing or durable return differs")
        statuses[result["status"]] += 1
        if lost: unavailable_timings.append(timing["durationMs"])
        else: physical[outcome(result)] += 1
        if result["status"] in {"ok", "abstain"}: timings.append(timing["durationMs"])
    transport_evidence = timeout.verify_transport(context, plan["callerTimeout"], transport, cohort, routes) if timing_out else {}
    if cancelling: transport_evidence = (active_integration if active_cancelling else cancellation).verify_transport(context, plan["coordinatorCancellation"], transport, cohort, routes, cancellation_receipts)
    return sealed({"schemaVersion": "agat.decision.public-workflow-result.v6" if active_cancelling else cancellation.RESULT_SCHEMA if cancelling else timeout.RESULT_SCHEMA if timing_out else recovery.RESULT_SCHEMA if recovering else LOSS_RESULT_SCHEMA if loss else RESULT_SCHEMA, "status": "integration_pass", "scheduled": count,
        "completedInstances": count-int(cancelling), "boundCallerReturns": count-int(cancelling), "computed": len(timings), "statuses": dict(statuses),
        **({"runtimeLoss": plan["runtimeLoss"], "unavailableReturns": len(unavailable_timings), "callerMsUnavailable": distribution(unavailable_timings)} if loss else {}),
        **({"runtimeRecovery": plan["runtimeRecovery"], "unavailableReturns": len(unavailable_timings), "callerMsUnavailable": distribution(unavailable_timings)} if recovering else {}),
        **({"callerTimeout": plan["callerTimeout"], "unavailableReturns": len(unavailable_timings), "callerMsUnavailable": distribution(unavailable_timings),
            "physicalDeliveredOutcomes": dict(physical), "healthySuffixCases": count-plan["callerTimeout"]["targetIndex"]-1} if timing_out else {}),
        **({"coordinatorCancellation": plan["coordinatorCancellation"], "cancelledInstances": 1, "unknownCallerReturns": 1,
            "unavailableReturns": 0, "physicalDeliveredOutcomes": dict(physical),
            "healthySuffixCases": count-plan["coordinatorCancellation"]["targetIndex"]-1} if cancelling else {}),
        "callerMsComputed": distribution(timings), "physicalScheduledOutcomes": dict(physical), **transport_evidence, "primaryFixtureCalls": count,
        **({"completedPrimaryRoutesPreserved": True, "operatorCancellationPreserved": True} if cancelling else {"primaryRoutePreserved": True}),
        "caseInputsUnchanged": True, "referenceLabels": 0, "classificationAccuracyMeasured": False,
        "primary": "fixture_chat_completions", "mode": "serial_closed_model_integration", "ownersAppointed": False,
        "sloAccepted": False, "representativeAgatTraffic": False, "routingEnabled": False, "qualification": "not_assessed"})
