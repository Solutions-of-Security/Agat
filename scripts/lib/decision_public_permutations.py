"""Whole unlabelled development variants; semantic options stay unchanged."""
from collections import Counter
from copy import deepcopy
import hashlib
from pathlib import PurePosixPath
import re

from decision_runtime.artifacts import sealed, verify_seal
from decision_runtime.contracts import Request, fields, fingerprint
from scripts.lib.decision_public_load import validate_context
from scripts.lib.decision_shadow_pilot import require, timestamp

SCHEMA = "agat.decision.public-permutation-context.v1"
RULE = "cyclic_positions_reverse_original_repeat.v1"
BUDGET = {"mode": "serial_closed_model_diagnostic", "callerTimeoutMs": 10000, "inferenceDeadlineMs": 5000,
          "warmupCount": 2, "timeBudgetSeconds": 600, "retries": 0, "primaryCompanionStarted": False}
SOURCE_PATHS = ["decision_runtime", "scripts/lib", "scripts/profile-public-support-permutations.py",
               "scripts/profile-public-support-context.py", "scripts/test/test_decision_public_permutations.py",
               "scripts/run-decision-arrival-rate.py", "scripts/verify-decision-arrival-rate.py",
               "scripts/run-temporal-real-rag.py", "scripts/profile-embedding-rag.py", "workers/local_decisions.py",
               "docs/qualification/local-decisions/performance/profiles/runtime-0.12.3-wired-4096.json"]


def orders(count):
    require(type(count) is int and 2 <= count <= 10, "Use 2 to 10 semantic choice options")
    original = list(range(count)); rows = [("rotation_"+str(index), original[index:]+original[:index]) for index in range(count)]
    reverse = list(reversed(original))
    if all(order != reverse for _, order in rows): rows.append(("reverse", reverse))
    rows.append(("original_repeat", original))
    return rows


def variants(context):
    validate_context(context); rows = []
    for case in context["inputs"]:
        raw = deepcopy(case["request"]); Request.from_dict(raw)
        require(raw["kind"] == "choice", "This diagnostic changes only choice option order")
        for order_id, indices in orders(len(raw["options"])):
            request = deepcopy(raw)
            request["id"] = "permutation-"+hashlib.sha256((case["id"]+"\0"+order_id).encode()).hexdigest()[:24]
            request["options"] = [deepcopy(raw["options"][index]) for index in indices]
            parsed = Request.from_dict(request)
            rows.append({"index": len(rows), "id": parsed.id, "sourceCaseId": case["id"], "sourceGroupId": case["groupId"],
                         "orderId": order_id, "optionOrder": [row["id"] for row in request["options"]],
                         "request": request, "inputSha256": parsed.input_sha256})
    require(len(rows) <= 720 and len({row["id"] for row in rows}) == len(rows), "Excessive or duplicate variant inventory")
    return rows


def token_inventory(context, tokenized):
    expected = variants(context)
    require(isinstance(tokenized, list) and len(tokenized) == len(expected), "Whole variant token inventory is missing")
    cases = {row["id"]: row for row in context["inputs"]}; rows = []
    for variant, actual in zip(expected, tokenized):
        fields(actual, {"id", "inputSha256", "partTokens", "inputTokens", "labelContinuationsVerified"})
        require(actual["id"] == variant["id"] and actual["inputSha256"] == variant["inputSha256"], "Reordered or changed tokenizer input")
        parts = actual["partTokens"]
        require(isinstance(parts, list) and len(parts) == 2 and all(type(n) is int and 0 < n <= 100000 for n in parts)
                and type(actual["inputTokens"]) is int and actual["inputTokens"] == sum(parts)
                and actual["labelContinuationsVerified"] is True, "Invalid variant prompt/tokenizer continuation")
        if variant["orderId"] in {"rotation_0", "original_repeat"}:
            original = cases[variant["sourceCaseId"]]
            require(parts == original["partTokens"] and actual["inputTokens"] == original["inputTokens"]
                    and variant["inputSha256"] == original["inputSha256"], "Unchanged original prompt tokenization drifted")
        eligible = actual["inputTokens"] <= 2048
        rows.append({**variant, **{key: actual[key] for key in ("partTokens", "inputTokens", "labelContinuationsVerified")},
                     "contextEligible": eligible, "contextExclusionReason": None if eligible else "context_too_long"})
    return rows


def summary(context, rows):
    tokens = [row["inputTokens"] for row in rows]
    return {"sourceCases": len(context["inputs"]), "sourceGroups": context["developmentGroups"], "scheduledVariants": len(rows),
            "variantsByOrder": dict(Counter(row["orderId"] for row in rows)), "contextEligibleVariants": sum(row["contextEligible"] for row in rows),
            "contextTooLongVariants": sum(not row["contextEligible"] for row in rows), "inputTokenRange": {"min": min(tokens), "max": max(tokens)}}


def validate_profile(value, context, original_file_sha):
    validate_context(context); verify_seal(value, SCHEMA)
    fields(value, {"schemaVersion", "sha256", "status", "createdAt", "sourceCommit", "sourceFiles", "originalContextFileSha256",
                   "originalContextSealSha256", "originalPoolSha256", "profileFileSha256", "profileSha256", "manifestFileSha256",
                   "model", "tokenizerEnvironment", "rule", "budget", "summary", "inputs", "stateTranslationApplied", "inputTruncationApplied",
                   "semanticOptionsChanged", "statisticalIndependenceVerified", "classificationAccuracyMeasured", "referenceLabels", "predictions",
                   "modelCalls", "calibrationRequestsTokenized", "holdoutRequestsTokenized", "ownersAppointed", "sloAccepted", "routingEnabled", "qualification"})
    require(isinstance(value["sourceCommit"], str) and re.fullmatch(r"[a-f0-9]{40}", value["sourceCommit"])
            and isinstance(value["sourceFiles"], dict) and 1 <= len(value["sourceFiles"]) <= 500, "Invalid variant source identity")
    for name, digest in value["sourceFiles"].items():
        require(isinstance(name, str) and not PurePosixPath(name).is_absolute() and ".." not in PurePosixPath(name).parts
                and isinstance(digest, str) and re.fullmatch(r"[a-f0-9]{64}", digest), "Invalid variant source pin")
    require(timestamp(value["createdAt"], "variant.createdAt") >= timestamp(context["createdAt"], "context.createdAt"), "Variant recipe predates its original context")
    require(value["status"] == "tokenized_without_inference" and value["originalContextFileSha256"] == original_file_sha
            and value["originalContextSealSha256"] == context["sha256"] and value["originalPoolSha256"] == context["poolSha256"]
            and value["rule"] == RULE and value["qualification"] == "not_assessed", "Variant recipe is rebound or grants qualification")
    for key in ("referenceLabels", "predictions", "modelCalls", "calibrationRequestsTokenized", "holdoutRequestsTokenized"):
        require(type(value[key]) is int and value[key] == 0, "Variant profiler grants predictions, labels or holdout access")
    for key in ("stateTranslationApplied", "inputTruncationApplied", "semanticOptionsChanged", "statisticalIndependenceVerified",
                "classificationAccuracyMeasured", "ownersAppointed", "sloAccepted", "routingEnabled"):
        require(value[key] is False, "Variant profiler changes semantics or authority")
    for key in ("profileFileSha256", "profileSha256", "manifestFileSha256", "model", "tokenizerEnvironment"):
        require(fingerprint(value[key]) == fingerprint(context[key]), "Variant model/profile/tokenizer identity differs")
    require(fingerprint(value["budget"]) == fingerprint(BUDGET), "Diagnostic execution budget changed")
    actual = value["inputs"]; expected = variants(context)
    require(isinstance(actual, list) and len(actual) == len(expected), "Variant denominator changed")
    tokens = []
    for row, original in zip(actual, expected):
        fields(row, set(original)|{"partTokens", "inputTokens", "labelContinuationsVerified", "contextEligible", "contextExclusionReason"})
        require(fingerprint({key: row[key] for key in original}) == fingerprint(original), "Variant lost original state, semantic candidate or order binding")
        tokens.append({key: row[key] for key in ("id", "inputSha256", "partTokens", "inputTokens", "labelContinuationsVerified")})
    rebuilt = token_inventory(context, tokens)
    require(fingerprint(rebuilt) == fingerprint(actual) and fingerprint(value["summary"]) == fingerprint(summary(context, rebuilt)), "Variant admission or summary differs")
    return value
