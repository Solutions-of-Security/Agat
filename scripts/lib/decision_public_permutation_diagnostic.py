"""Source-bound full option-order observation, without gold or qualification."""
from collections import Counter, defaultdict
import hashlib
from pathlib import Path
import time

from decision_runtime.artifacts import sealed, verify_seal
from decision_runtime.contracts import Request, fields, number, parse_json
from decision_runtime.metrics import OUTCOMES
from scripts.lib.decision_arrival_rate import measure_one
from scripts.lib.decision_performance import profile_from_health
from scripts.lib.decision_public_load import validate_context
from scripts.lib.decision_public_load_verification import CONTEXT_PATHS, counters, distribution, observation, same, sources_at
from scripts.lib.decision_public_permutations import BUDGET, SOURCE_PATHS, validate_profile
from scripts.lib.decision_public_sources import pinned_input
from scripts.lib.decision_shadow_pilot import require, timestamp

PLAN_SCHEMA = "agat.decision.public-permutation-diagnostic-plan.v1"
RESULT_SCHEMA = "agat.decision.public-permutation-diagnostic-result.v1"
PHASE_SCHEMA = "agat.decision.public-permutation-diagnostic-phase.v1"
VERIFY_SCHEMA = "agat.decision.public-permutation-diagnostic-verification.v1"
PROFILE_PATH = "docs/qualification/local-decisions/performance/profiles/runtime-0.12.3-wired-4096.json"
PATHS = [*SOURCE_PATHS, "scripts/run-public-support-permutation-diagnostic.py",
         "scripts/test/test_decision_public_permutation_diagnostic.py",
         "docs/qualification/local-decisions/policy.shadow.v1.json",
         "docs/qualification/local-decisions/performance/evidence/2026-09-28/rag-http-isolation/resident-isolated/rag-workflow-plan.json"]
AUTHORITY = {"referenceLabels": 0, "calibrationRequests": 0, "holdoutRequests": 0,
             "classificationAccuracyMeasured": False, "statisticalIndependenceVerified": False, "ownersAppointed": False,
             "sloAccepted": False, "representativeAgatTraffic": False, "routingEnabled": False, "qualification": "not_assessed"}
META = {"index", "variantId", "sourceCaseId", "sourceGroupId", "orderId", "inputSha256", "inputTokens", "contextEligible"}
TIMED = {"startedMs", "finishedMs", "status", "reason", "observation", "callerMs", "wallMs"}


def authority(value):
    for key, expected in AUTHORITY.items():
        require(type(value[key]) is type(expected) and value[key] == expected, "Diagnostic promoted to qualification: "+key)


def binding(case):
    return {key: case[key] for key in META - {"variantId"}} | {"variantId": case["id"]}


def semantic(result):
    return (result["status"], result["reason"], result["selectedOptionId"], result["value"])


def probabilities_by_id(result):
    return {row["id"]: row["probability"] for row in result["distribution"]}


def summary(rows, inputs):
    by_case = defaultdict(list); expected = defaultdict(list)
    for case in inputs: expected[case["sourceCaseId"]].append(case)
    for row in rows: by_case[row["sourceCaseId"]].append(row)
    measured = [row for row in rows if "callerMs" in row]
    computed = [row for row in rows if row["status"] in {"ok", "abstain"}]
    excluded = [row for row in rows if row["status"] == "error" and row["reason"] == "context_too_long"]
    details = []; positions = Counter(); accepted_positions = Counter()
    for case_id, cases in expected.items():
        case_rows = by_case[case_id]; scored = [row for row in case_rows if row["status"] in {"ok", "abstain"}]
        complete = len(scored) == len(cases)
        original = next((row for row in scored if row["orderId"] == "rotation_0"), None)
        repeat = next((row for row in scored if row["orderId"] == "original_repeat"), None)
        original_result = original["observation"]["result"] if original else None
        deltas = []
        if original_result:
            baseline = probabilities_by_id(original_result)
            for row in scored:
                probability = probabilities_by_id(row["observation"]["result"])
                require(set(probability) == set(baseline), "Semantic candidate disappeared")
                deltas.append(max(abs(probability[key] - baseline[key]) for key in baseline))
        repeat_delta = None
        if original and repeat:
            baseline = probabilities_by_id(original_result); repeated = probabilities_by_id(repeat["observation"]["result"])
            repeat_delta = max(abs(repeated[key] - baseline[key]) for key in baseline)
        details.append({"sourceCaseId": case_id, "sourceGroupId": cases[0]["sourceGroupId"], "scheduledVariants": len(cases),
            "computedVariants": len(scored), "allVariantsComputed": complete,
            "allVariantsContextRejected": len(case_rows) == len(cases) and all(row["status"] == "error" and row["reason"] == "context_too_long" for row in case_rows),
            "distinctSemanticOutcomes": len({semantic(row["observation"]["result"]) for row in scored}),
            "allOrdersSemanticOutcomeEqual": len({semantic(row["observation"]["result"]) for row in scored}) == 1 if complete else None,
            "originalRepeatComparable": original is not None and repeat is not None,
            "originalRepeatSemanticOutcomeEqual": semantic(original_result) == semantic(repeat["observation"]["result"]) if original and repeat else None,
            "originalRepeatMaxProbabilityDelta": repeat_delta,
            "maxProbabilityDeltaFromOriginal": max(deltas) if deltas else None})
        for row in scored:
            if not row["orderId"].startswith("rotation_"): continue
            case = next(case for case in cases if case["id"] == row["variantId"])
            position = case["optionOrder"].index(row["observation"]["result"]["selectedOptionId"])
            positions[str(position)] += 1
            if row["status"] == "ok": accepted_positions[str(position)] += 1
    whole = [row for row in details if row["allVariantsComputed"]]
    paired = [row for row in details if row["originalRepeatComparable"]]
    return {"sourceCases": len(expected), "sourceGroups": len({case["sourceGroupId"] for case in inputs}),
        "scheduledVariants": len(inputs), "returnedVariants": len(measured), "computedVariants": len(computed), "contextRejectedVariants": len(excluded),
        "notAttemptedVariants": sum(row["status"] == "not_attempted" for row in rows), "statuses": dict(Counter(row["status"] for row in rows)),
        "fullyComputedSourceCases": len(whole), "fullyContextRejectedSourceCases": sum(row["allVariantsContextRejected"] for row in details),
        "sourceGroupsWithFullyComputedCases": len({row["sourceGroupId"] for row in whole}),
        "allOrdersSemanticOutcomeEqualSourceCases": sum(row["allOrdersSemanticOutcomeEqual"] is True for row in whole),
        "changedSemanticOutcomeSourceCases": sum(row["allOrdersSemanticOutcomeEqual"] is False for row in whole),
        "changedSemanticOutcomeSourceGroups": len({row["sourceGroupId"] for row in whole if row["allOrdersSemanticOutcomeEqual"] is False}),
        "originalRepeatComparableSourceCases": len(paired),
        "originalRepeatChangedSemanticOutcomeSourceCases": sum(row["originalRepeatSemanticOutcomeEqual"] is False for row in paired),
        "originalRepeatMaxProbabilityDelta": max((row["originalRepeatMaxProbabilityDelta"] for row in paired), default=None),
        "maxProbabilityDeltaFromOriginal": max((row["maxProbabilityDeltaFromOriginal"] for row in details if row["maxProbabilityDeltaFromOriginal"] is not None), default=None),
        "cyclicArgmaxVariants": sum(positions.values()), "cyclicArgmaxByPosition": dict(positions),
        "cyclicAcceptedVariants": sum(accepted_positions.values()), "cyclicAcceptedByPosition": dict(accepted_positions),
        "callerMsComputed": distribution([row["callerMs"] for row in computed]),
        "callerMsContextRejected": distribution([row["callerMs"] for row in excluded]), "cases": details}


def run_phase(client, inputs, profile, *, clock=time.monotonic, cancelled=lambda: False, journal=None, measure=None):
    require(isinstance(inputs, list) and 1 <= len(inputs) <= 720, "Whole bounded variant inventory required")
    measure = measure_one if measure is None else measure
    rows = []; origin = clock(); halted = None
    for case in inputs:
        base = binding(case)
        if halted is None:
            halted = "cancelled" if cancelled() else "time_budget" if (clock()-origin)*1000 + BUDGET["callerTimeoutMs"] > BUDGET["timeBudgetSeconds"]*1000 else None
        if halted:
            row = {**base, "status": "not_attempted", "reason": halted}
        else:
            began = clock(); measured = measure(client, Request.from_dict(case["request"]), profile, BUDGET["callerTimeoutMs"])
            row = {**base, "startedMs": round((began-origin)*1000, 3), "finishedMs": round((clock()-origin)*1000, 3), **measured}
            try:
                observation(row, case, profile)
                require(row["status"] in {"ok", "abstain"} if case["contextEligible"] else row["status"] == "error" and row["reason"] == "context_too_long", "Unexpected diagnostic response")
            except (ValueError, KeyError, TypeError):
                row.update(status="measurement_error", reason="invalid_or_unexpected_response"); halted = "previous_failure"
        rows.append(row)
        if journal: journal(row)
    phase = {"schemaVersion": PHASE_SCHEMA, "budget": dict(BUDGET), "elapsedMs": round((clock()-origin)*1000, 3),
             "complete": halted is None, "rows": rows, "summary": summary(rows, inputs)}
    if phase["elapsedMs"] > BUDGET["timeBudgetSeconds"]*1000+.1: phase["complete"] = False
    return phase


def verify_phase(phase, inputs, profile):
    fields(phase, {"schemaVersion", "budget", "elapsedMs", "complete", "rows", "summary"})
    require(phase["schemaVersion"] == PHASE_SCHEMA and phase["complete"] is True, "Incomplete diagnostic is not a passing observation")
    same(phase["budget"], BUDGET, "Diagnostic budget changed")
    elapsed = number(phase["elapsedMs"], 0, BUDGET["timeBudgetSeconds"]*1000+.1)
    rows = phase["rows"]; require(isinstance(rows, list) and len(rows) == len(inputs), "Partial diagnostic denominator")
    known = Counter(); previous = 0
    for row, case in zip(rows, inputs):
        fields(row, META | TIMED); same({key:row[key] for key in META}, binding(case), "Rebound or reordered variant")
        began = number(row["startedMs"], previous-.0011, elapsed+.0011)
        finished = number(row["finishedMs"], began, elapsed+.0011); previous = finished
        require(finished-began+.03 >= number(row["wallMs"],0,10001) and row["callerMs"] <= 10001, "Overlapping slot or inconsistent call duration")
        require(began+BUDGET["callerTimeoutMs"] <= BUDGET["timeBudgetSeconds"]*1000+.1, "Call started without its reserved timeout budget")
        outcome = observation(row, case, profile)
        require(row["status"] in {"ok", "abstain"} if case["contextEligible"] else row["status"] == "error" and row["reason"] == "context_too_long", "Unexpected diagnostic response")
        require(outcome is not None, "Missing physical handler outcome"); known[outcome] += 1
    rebuilt = summary(rows, inputs); same(phase["summary"], rebuilt, "Changed stability summary or denominator")
    return rebuilt, known


def verify(root, directory, context_path, permutation_path, *, context_sha, permutation_sha, plan_sha, result_sha):
    raw = {"context": pinned_input(context_path, context_sha, 32*1024*1024),
           "permutation": pinned_input(permutation_path, permutation_sha, 32*1024*1024),
           "plan": pinned_input(directory/"plan.json", plan_sha, 32*1024*1024),
           "result": pinned_input(directory/"result.json", result_sha, 64*1024*1024)}
    context = validate_context(parse_json(raw["context"])); permutation = validate_profile(parse_json(raw["permutation"]), context, context_sha)
    plan = verify_seal(parse_json(raw["plan"]), PLAN_SCHEMA); result = verify_seal(parse_json(raw["result"]), RESULT_SCHEMA)
    fields(plan, {"schemaVersion", "sha256", "createdAt", "sourceCommit", "sourceFiles", "contextProfileFileSha256",
        "contextProfileSealSha256", "permutationContextFileSha256", "permutationContextSealSha256", "profile", "profileFileSha256",
        "profileSha256", "manifestFileSha256", "model", "runtime", "inputs", "budget", "scope", "backgroundWorkloadControlled"} | set(AUTHORITY))
    fields(result, {"schemaVersion", "sha256", "status", "planSha256", "warmup", "phase", "samples", "failure", "cancelled",
        "ownedPids", "remainingOwnedPids", "cleanupErrors", "runtimeExitCode", "elapsedMs", "logSha256"} | set(AUTHORITY))
    authority(plan); authority(result)
    require(plan["scope"] == "whole_unlabelled_public_development_option_order_diagnostic"
            and plan["backgroundWorkloadControlled"] is False and plan["contextProfileFileSha256"] == context_sha
            and plan["contextProfileSealSha256"] == context["sha256"] and plan["permutationContextFileSha256"] == permutation_sha
            and plan["permutationContextSealSha256"] == permutation["sha256"], "Changed diagnostic scope or independent input binding")
    require(timestamp(plan["createdAt"], "plan.createdAt") >= timestamp(permutation["createdAt"], "permutation.createdAt"), "Plan predates the frozen recipe")
    for key in ("profile", "profileFileSha256", "profileSha256", "manifestFileSha256", "model"):
        same(plan[key], context[key], "Model or profile changed")
    same(plan["inputs"], permutation["inputs"], "Whole prospective variant inventory changed")
    same(plan["budget"], permutation["budget"], "Prospective diagnostic budget changed")
    same(plan["runtime"], context["tokenizerEnvironment"], "Runtime differs from pinned tokenizer environment")
    sources_at(root, context["sourceCommit"], context["sourceFiles"], CONTEXT_PATHS)
    sources_at(root, permutation["sourceCommit"], permutation["sourceFiles"], SOURCE_PATHS)
    sources = sources_at(root, plan["sourceCommit"], plan["sourceFiles"], PATHS)
    require(hashlib.sha256(sources[PROFILE_PATH]).hexdigest() == plan["profileFileSha256"], "Committed profile bytes differ")
    same(parse_json(sources[PROFILE_PATH]), plan["profile"], "Committed serving profile differs")
    implementation = hashlib.sha256()
    for name in sorted(name for name in sources if Path(name).parent == Path("decision_runtime") and name.endswith(".py")):
        implementation.update(Path(name).name.encode()+b"\0"+sources[name]+b"\0")
    require(implementation.hexdigest() == plan["profile"]["model"]["implementationSha256"], "Historical implementation binding differs")
    requirements = dict(line.split("==") for line in sources["decision_runtime/requirements-mlx.txt"].decode().splitlines() if line and not line.startswith("#"))
    same(plan["runtime"], {"python":"3.13.12", "machine":"arm64", "packages":requirements}, "Historical native dependencies differ")
    require(result["status"] == "observed" and result["planSha256"] == plan["sha256"] and result["failure"] is None
            and result["cancelled"] is False and result["cleanupErrors"] == [] and result["remainingOwnedPids"] == []
            and type(result["runtimeExitCode"]) is int, "Run or reported cleanup failed")
    number(result["elapsedMs"], 0, 700000)
    require(isinstance(result["ownedPids"],list) and result["ownedPids"] and all(type(pid) is int and pid>0 for pid in result["ownedPids"])
            and result["ownedPids"] == sorted(set(result["ownedPids"])), "Invalid owned process inventory")
    fields(result["logSha256"], {"runtime.log", "requests.jsonl", "phase.json"})
    artifacts = {name:pinned_input(directory/name, digest, 32*1024*1024) for name,digest in result["logSha256"].items()}
    phase_file = verify_seal(parse_json(artifacts["phase.json"]), PHASE_SCHEMA)
    same({key:value for key,value in phase_file.items() if key != "sha256"}, result["phase"], "Stored phase differs from embedded phase")
    measured, known = verify_phase(result["phase"], plan["inputs"], plan["profile"])
    records = [parse_json(line) for line in artifacts["requests.jsonl"].splitlines()]
    same(records, result["phase"]["rows"], "Immediate journal omitted, duplicated or changed rows")
    warmup = result["warmup"]; require(isinstance(warmup,list) and len(warmup)==2, "Warmup denominator changed")
    first = next(case for case in plan["inputs"] if case["contextEligible"])
    warmup_known = Counter()
    for iteration,row in enumerate(warmup):
        fields(row,{"variantId", "inputSha256", "inputTokens", "iteration", "status", "reason", "observation", "callerMs", "wallMs"})
        same({key:row[key] for key in ("variantId","inputSha256","inputTokens","iteration")},
             {"variantId":first["id"],"inputSha256":first["inputSha256"],"inputTokens":first["inputTokens"],"iteration":iteration}, "Warmup binding changed")
        require(row["status"] in {"ok","abstain"}, "Warmup failed")
        warmup_known[observation(row,first,plan["profile"])] += 1
    samples = result["samples"]; require(isinstance(samples,list) and len(samples)==3, "Missing quiescent physical snapshots")
    counts = []; start_times = []; previous = -1
    for label,sample in zip(("ready_before_scoring","after_warmup","after_inventory"),samples):
        require(sample["label"]==label and number(sample["elapsedMs"],0,result["elapsedMs"]+.1)>=previous, "Snapshot order differs")
        previous = sample["elapsedMs"]; same(profile_from_health(sample["health"]),plan["profile"],"Health profile changed")
        require(sample["health"]["profileSha256"]==plan["profileSha256"] and isinstance(sample["processRaw"],str)
                and isinstance(sample["ownedPids"],list) and sample["ownedPids"]
                and all(type(pid) is int and pid>0 for pid in sample["ownedPids"])
                and sample["ownedPids"]==sorted(set(sample["ownedPids"]))
                and set(sample["ownedPids"])<=set(result["ownedPids"]), "Snapshot identity differs")
        value,start = counters(sample); counts.append(value); start_times.append(start)
    require(len(set(start_times))==1 and all(value==0 for value in counts[0].values()), "Non-zero origin or runtime restart")
    same(counts[1],{key:warmup_known[key] for key in OUTCOMES},"Physical warmup accounting differs")
    same(counts[2],{key:warmup_known[key]+known[key] for key in OUTCOMES},"Physical scheduled accounting differs")
    require(sum(counts[2].values())==len(plan["inputs"])+2 and samples[-1]["elapsedMs"]>=result["phase"]["elapsedMs"], "Whole handler denominator or elapsed time differs")
    for name,path,digest in (("context",context_path,context_sha),("permutation",permutation_path,permutation_sha),
                             ("plan",directory/"plan.json",plan_sha),("result",directory/"result.json",result_sha)):
        require(pinned_input(path,digest,64*1024*1024)==raw[name], "Consumed evidence changed during replay")
    for name,data in artifacts.items():require(pinned_input(directory/name,result["logSha256"][name],32*1024*1024)==data,"Consumed journal or log changed")
    return sealed({"schemaVersion":VERIFY_SCHEMA,"status":"pass","sourceCommit":plan["sourceCommit"],"sourceFilesCount":len(plan["sourceFiles"]),
        "contextProfileFileSha256":context_sha,"permutationContextFileSha256":permutation_sha,"planFileSha256":plan_sha,"resultFileSha256":result_sha,
        "summary":{key:value for key,value in measured.items() if key!="cases"},"physicalHttpAccounting":"exact",
        "physicalScheduledHttpHandlers":len(plan["inputs"]),"physicalHttpCounters":counts[2],"warmupCalls":2,
        "reportedCleanupComplete":True,"liveCleanupVerified":False,"modelCallsPerformedByVerifier":0,**AUTHORITY})
