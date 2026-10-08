"""Reconstruct public source provenance and select the frozen development split."""
from collections import Counter
import hashlib
from pathlib import Path
import re
import subprocess

from decision_runtime.annotations import group_split, validate_pool
from decision_runtime.artifacts import verify_seal
from decision_runtime.contracts import Request, fields, fingerprint, parse_json
from scripts.lib.decision_public_sources import (
    MODEL_REPOSITORY, MODEL_REVISION, SOURCE_PATHS, SPLIT_SEED,
    build_pool, pinned_input, validate_acquisition,
)
from scripts.lib.decision_shadow_pilot import require

IMPORT_SCHEMA = "agat.decision.public-support-import.v1"
PROFILE_SCHEMA = "agat.decision.public-support-context.v1"


def historical_sources(root, manifest):
    require(isinstance(manifest["sourceCommit"], str) and re.fullmatch(r"[a-f0-9]{40}", manifest["sourceCommit"]),
            "Invalid historical source commit")
    fields(manifest["sourceFiles"], set(SOURCE_PATHS))
    for name, expected in manifest["sourceFiles"].items():
        require(isinstance(expected, str) and re.fullmatch(r"[a-f0-9]{64}", expected), "Invalid historical source SHA")
        raw = subprocess.check_output(["git", "show", f"{manifest['sourceCommit']}:{name}"], cwd=root, timeout=5)
        require(hashlib.sha256(raw).hexdigest() == expected, "Historical source SHA differs")


def reconstruct(root, import_path, import_sha, acquisition_path):
    imported = verify_seal(parse_json(pinned_input(import_path, import_sha, 1024 * 1024)), IMPORT_SCHEMA)
    fields(imported, {"schemaVersion", "sha256", "acquisitionFileSha256", "acquisitionSha256", "sourceCommit", "sourceFiles",
                      "outputFileSha256", "routingEnabled", "qualification", "modelCalls"})
    require(imported["routingEnabled"] is False and imported["qualification"] == "not_assessed"
            and type(imported["modelCalls"]) is int and imported["modelCalls"] == 0, "Import grants unsupported evidence")
    acquisition = validate_acquisition(parse_json(pinned_input(acquisition_path, imported["acquisitionFileSha256"], 1024 * 1024)))
    require(acquisition["sha256"] == imported["acquisitionSha256"], "Import acquisition binding differs")
    historical_sources(root, imported); historical_sources(root, acquisition)
    directory = acquisition_path.absolute().parent
    def snapshot(item):
        raw = pinned_input(directory / item["file"], item["sha256"], 4 * 1024 * 1024)
        require(len(raw) == item["bytes"], "Source snapshot size differs")
        return raw
    model = snapshot(acquisition["modelMetadata"])
    pages = [snapshot(item) for item in acquisition["pages"]]
    expected = build_pool(acquisition, pages, parse_json(model))
    fields(imported["outputFileSha256"], set(expected))
    for name, value in expected.items():
        actual = parse_json(pinned_input(import_path.absolute().parent / name, imported["outputFileSha256"][name], 32 * 1024 * 1024))
        require(fingerprint(actual) == fingerprint(value), "Imported public pool differs from reconstructed raw sources")
    return imported, acquisition, expected["pool.json"]


def development_cases(pool):
    validate_pool(pool)
    selected = [case for case in pool["cases"] if group_split(SPLIT_SEED, case["groupId"]) == "development"]
    require(1 <= len(selected) <= 60, "Use a whole development split of 1 to 60 cases; no implicit sampling")
    return selected


def verify_profile(profile, manifest):
    require(profile.get("schemaVersion") == "agat.decision.v1" and profile.get("runtimeVersion") == "0.12.3",
            "Use the pinned 0.12.3 runtime profile")
    model = profile.get("model", {})
    require(model.get("repository") == MODEL_REPOSITORY and model.get("revision") == MODEL_REVISION
            and all(model.get(key) == manifest.get(key) for key in ("repository", "revision", "artifactSha256"))
            and model.get("tokenizerSha256") == manifest["files"].get("tokenizer.json"), "Profile/model/tokenizer binding differs")
    require(model.get("maxInputTokens") == 2048 and model.get("allocatorCacheLimitBytes") == 128 * 1024**2
            and model.get("allocatorWiredLimitBytes") == 4096 * 1024**2, "Use the frozen wired 4096-MiB / 2048-token envelope")


def context_result(pool, selected, tokenized, profile):
    require(selected == development_cases(pool), "Development selection changed")
    require(isinstance(tokenized, list) and len(tokenized) == len(selected), "Token inventory differs")
    inputs = []
    for case, row in zip(selected, tokenized):
        fields(row, {"id", "inputSha256", "partTokens", "inputTokens", "labelContinuationsVerified"})
        request = Request.from_dict(case["request"])
        require(row["id"] == case["id"] and row["inputSha256"] == request.input_sha256,
                "Tokenization is bound to a different or reordered input")
        parts = row["partTokens"]
        require(isinstance(parts, list) and len(parts) == 2 and all(type(n) is int and 0 < n <= 100000 for n in parts)
                and type(row["inputTokens"]) is int and row["inputTokens"] == sum(parts)
                and row["labelContinuationsVerified"] is True, "Invalid prompt/tokenizer contract")
        eligible = row["inputTokens"] <= profile["model"]["maxInputTokens"]
        inputs.append({"id": case["id"], "groupId": case["groupId"], "provenance": case["provenance"],
                       "request": case["request"], **row, "contextEligible": eligible,
                       "contextExclusionReason": None if eligible else "context_too_long"})
    splits = Counter(group_split(SPLIT_SEED, c["groupId"]) for c in pool["cases"])
    tokens = [item["inputTokens"] for item in inputs]
    return {"poolSha256": fingerprint(pool), "splitSeed": SPLIT_SEED, "selectedSplit": "development",
            "selectionOrder": "original_pool_order", "poolCasesBySplit": dict(splits),
            "developmentCases": len(inputs), "developmentGroups": len({x["groupId"] for x in inputs}),
            "contextEligibleCases": sum(x["contextEligible"] for x in inputs),
            "contextTooLongCases": sum(not x["contextEligible"] for x in inputs),
            "inputTokenRange": {"min": min(tokens), "max": max(tokens)}, "inputs": inputs,
            "inputTruncationApplied": False, "sourceTranslationApplied": False,
            "calibrationRequestsTokenized": 0, "holdoutRequestsTokenized": 0,
            "referenceLabels": 0, "predictions": 0, "modelCalls": 0,
            "statisticalIndependenceVerified": False, "representativeAgatTraffic": False,
            "sloAccepted": False, "routingEnabled": False, "qualification": "not_assessed",
            "proposedDiagnosticSchedule": {"ratePerSecond": 0.5, "clientSlots": 1, "maxSchedulerLagMs": 100,
                "scheduledCases": len(inputs), "windowSeconds": 2 * len(inputs), "callerTimeoutMs": 10000,
                "thresholdMs": 5000, "ineligibleCasesRetainedInInventory": True, "retries": 0}}
