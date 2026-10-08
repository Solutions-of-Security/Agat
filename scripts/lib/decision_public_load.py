"""Whole unlabelled development inventory; every scheduled request remains accountable."""
import hashlib
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess

from decision_runtime.annotations import group_split
from decision_runtime.artifacts import verify_seal
from decision_runtime.contracts import Request, fields, fingerprint, number, string
from scripts.lib.decision_arrival_rate import drive_arrivals, measure_one, summarize
from scripts.lib.decision_public_context import PROFILE_SCHEMA
from scripts.lib.decision_public_sources import SPLIT_SEED
from scripts.lib.decision_shadow_pilot import require

PLAN_SCHEMA = "agat.decision.public-support-load-plan.v1"
RESULT_SCHEMA = "agat.decision.public-support-load-result.v1"
PHASE_SCHEMA = "agat.decision.public-support-load-phase.v1"
CONTEXT_FIELDS = {"schemaVersion", "sha256", "status", "createdAt", "sourceCommit", "sourceFiles", "importFileSha256",
    "importSha256", "acquisitionFileSha256", "acquisitionSha256", "profileFileSha256", "profileSha256", "profile",
    "manifestFileSha256", "model", "tokenizerEnvironment", "poolSha256", "splitSeed", "selectedSplit", "selectionOrder",
    "poolCasesBySplit", "developmentCases", "developmentGroups", "contextEligibleCases", "contextTooLongCases",
    "inputTokenRange", "inputs", "inputTruncationApplied", "sourceTranslationApplied", "calibrationRequestsTokenized",
    "holdoutRequestsTokenized", "referenceLabels", "predictions", "modelCalls", "statisticalIndependenceVerified",
    "representativeAgatTraffic", "sloAccepted", "routingEnabled", "qualification", "proposedDiagnosticSchedule"}


def validate_context(value):
    verify_seal(value, PROFILE_SCHEMA); fields(value, CONTEXT_FIELDS)
    require(value["status"] == "observed" and value["selectedSplit"] == "development" and value["splitSeed"] == SPLIT_SEED
            and value["selectionOrder"] == "original_pool_order" and value["qualification"] == "not_assessed",
            "Use the frozen unlabelled public development context profile")
    for key in ("referenceLabels", "predictions", "modelCalls", "calibrationRequestsTokenized", "holdoutRequestsTokenized"):
        require(type(value[key]) is int and value[key] == 0, "Context profile grants unsupported labels or model scope")
    for key in ("sloAccepted", "routingEnabled", "representativeAgatTraffic", "statisticalIndependenceVerified",
                "inputTruncationApplied", "sourceTranslationApplied"):
        require(value[key] is False, "Context profile grants unsupported qualification or transformations")
    require(fingerprint(value["profile"]) == value["profileSha256"], "Context profile semantic SHA differs")
    fields(value["model"], {"repository", "revision", "artifactSha256"})
    require(all(value["model"][key] == value["profile"]["model"][key] for key in value["model"]),
            "Context model identity differs from serving profile")
    for key in ("importFileSha256", "importSha256", "acquisitionFileSha256", "acquisitionSha256",
                "profileFileSha256", "profileSha256", "manifestFileSha256", "poolSha256"):
        require(isinstance(value[key], str) and re.fullmatch(r"[a-f0-9]{64}", value[key]), "Invalid context artifact SHA")
    fields(value["poolCasesBySplit"], {"development", "calibration", "holdout"})
    require(all(type(n) is int and n >= 0 for n in value["poolCasesBySplit"].values()), "Invalid source split inventory")
    inputs = value["inputs"]
    require(isinstance(inputs, list) and 1 <= len(inputs) <= 60, "Use the whole bounded development inventory")
    ids = set(); groups = set(); tokens = []; eligible = 0
    for row in inputs:
        fields(row, {"id", "groupId", "provenance", "request", "inputSha256", "partTokens", "inputTokens",
                     "labelContinuationsVerified", "contextEligible", "contextExclusionReason"})
        request = Request.from_dict(row["request"])
        string(row["groupId"], 100, identifier=True)
        require(request.id == row["id"] and request.input_sha256 == row["inputSha256"] and row["id"] not in ids
                and group_split(SPLIT_SEED, row["groupId"]) == "development", "Input binding, uniqueness or split differs")
        ids.add(row["id"]); groups.add(row["groupId"])
        require(type(row["inputTokens"]) is int and 0 < row["inputTokens"] <= 200000
                and isinstance(row["partTokens"], list) and len(row["partTokens"]) == 2
                and all(type(n) is int and 0 < n <= 100000 for n in row["partTokens"])
                and sum(row["partTokens"]) == row["inputTokens"] and row["labelContinuationsVerified"] is True,
                "Invalid exact token inventory")
        expected = row["inputTokens"] <= 2048
        require(row["contextEligible"] is expected and row["contextExclusionReason"] == (None if expected else "context_too_long"),
                "Context admission differs")
        eligible += expected; tokens.append(row["inputTokens"])
    for name, expected in (("developmentCases", len(inputs)), ("developmentGroups", len(groups)),
                           ("contextEligibleCases", eligible), ("contextTooLongCases", len(inputs) - eligible)):
        require(type(value[name]) is int and value[name] == expected, "Context inventory count differs")
    require(value["poolCasesBySplit"]["development"] == len(inputs), "Development inventory is not whole")
    require(eligible > 0 and fingerprint(value["inputTokenRange"]) == fingerprint({"min": min(tokens), "max": max(tokens)}),
            "No warmable case or token range differs")
    require(fingerprint(value["proposedDiagnosticSchedule"]) == fingerprint({"ratePerSecond": .5, "clientSlots": 1, "maxSchedulerLagMs": 100,
        "scheduledCases": len(inputs), "windowSeconds": len(inputs) * 2, "callerTimeoutMs": 10000, "thresholdMs": 5000,
        "ineligibleCasesRetainedInInventory": True, "retries": 0}), "Context schedule changed")
    return value


def historical_context_sources(root, value):
    require(isinstance(value["sourceCommit"], str) and re.fullmatch(r"[a-f0-9]{40}", value["sourceCommit"])
            and isinstance(value["sourceFiles"], dict) and 1 <= len(value["sourceFiles"]) <= 500, "Invalid context source identity")
    for name, digest in value["sourceFiles"].items():
        require(isinstance(name, str) and not Path(name).is_absolute() and ".." not in Path(name).parts
                and isinstance(digest, str) and re.fullmatch(r"[a-f0-9]{64}", digest), "Invalid context source pin")
        raw = subprocess.check_output(["git", "show", value["sourceCommit"] + ":" + name], cwd=root, timeout=5)
        require(hashlib.sha256(raw).hexdigest() == digest, "Context historical source bytes differ")


def run_inventory(client, inputs, profile, *, cancelled=lambda: False, journal=None, driver=drive_arrivals, origin=None):
    require(isinstance(inputs, list) and 1 <= len(inputs) <= 60, "Unsupported corpus arrival inventory")
    requests = [Request.from_dict(row["request"]) for row in inputs]
    require(all(request.id == case["id"] and request.input_sha256 == case["inputSha256"]
                for request, case in zip(requests, inputs)), "Corpus request binding differs")
    rows = [None] * len(inputs); capacity = threading.BoundedSemaphore(1); lock = threading.Lock(); futures = []
    origin = time.monotonic() if origin is None else number(origin, 0, 86_400_000_000)
    def save(index, row):
        with lock:
            rows[index] = row
            if journal: journal(row)
    def one(index, base):
        try:
            began = time.monotonic(); row = measure_one(client, requests[index], profile, 10000)
            case = inputs[index]
            if row["status"] in {"ok", "abstain"} and (not case["contextEligible"]
                    or row["observation"]["result"].get("inputTokens") != case["inputTokens"]):
                row.update(status="measurement_error", reason="input_token_mismatch")
            if not case["contextEligible"] and row["status"] != "measurement_error" and not (
                    row["status"] == "error" and row["reason"] == "context_too_long"):
                row.update(status="measurement_error", reason="missing_context_rejection")
            save(index, {**base, "startedMs": round((began-origin)*1000,3),
                         "finishedMs": round((time.monotonic()-origin)*1000,3), **row})
        finally: capacity.release()
    with ThreadPoolExecutor(max_workers=1) as pool:
        def dispatch(index, due, observed, reason):
            case = inputs[index]
            base = {"index": index, "caseId": case["id"], "inputSha256": case["inputSha256"],
                    "inputTokens": case["inputTokens"], "contextEligible": case["contextEligible"],
                    "scheduledMs": round(due*1000,3), "dispatchMs": round(observed*1000,3)}
            if reason or not capacity.acquire(blocking=False):
                save(index, {**base, "status": "dropped", "reason": reason or "client_capacity"})
            else: futures.append(pool.submit(one,index,base))
        driver(.5,len(inputs),1,100,dispatch,cancelled=cancelled,start=origin)
        for future in futures: future.result()
    return {"schemaVersion": PHASE_SCHEMA, "ratePerSecond": .5, "clientSlots": 1, "maxSchedulerLagMs": 100,
            "callerTimeoutMs": 10000, "thresholdMs": 5000, "elapsedMs": round((time.monotonic()-origin)*1000,3),
            "rows": rows, "summary": summarize(rows,5000)}
