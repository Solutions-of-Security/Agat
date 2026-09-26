"""Frozen experiments and score provenance, shared by fitting and qualification."""

from __future__ import annotations

import copy
from collections import Counter
from datetime import datetime, timezone

from .artifacts import sealed, verify_seal
from .calibration import CALIBRATION_SCHEMA, fit_temperature
from .contracts import INPUT_FINGERPRINT_VERSIONS, SCHEMA_VERSION, Policy, Request, canonical_json, fields, fingerprint, number, probabilities, string
from .evaluation import validate_dataset

PLAN_SCHEMA = "agat.decision.experiment.v1"
PROFILED_PLAN_SCHEMA = "agat.decision.experiment.v2"
CRITERIA_SCHEMA = "agat.decision.criteria.v1"


def validate_criteria(raw: dict) -> dict:
    fields(raw, {"schemaVersion", "id", "minAccuracy", "minCoverage", "maxGroupRisk",
                 "confidence", "minAcceptedGroups", "minAcceptedGroupsPerFamily"})
    if raw["schemaVersion"] != CRITERIA_SCHEMA:
        raise ValueError("Unsupported qualification criteria")
    string(raw["id"], 100, identifier=True)
    for key in ("minAccuracy", "minCoverage", "maxGroupRisk"):
        number(raw[key], 0, 1)
    number(raw["confidence"], 0.5, 0.9999)
    for key in ("minAcceptedGroups", "minAcceptedGroupsPerFamily"):
        if type(raw[key]) is not int or raw[key] < 1:
            raise ValueError("Group minimums must be positive integers")
    return raw


def validate_execution_profile(profile: dict, policy: Policy, model: dict) -> dict:
    fields(profile, {"schemaVersion", "runtimeVersion", "model", "policy", "calibration"}, {"inputFingerprintVersions"})
    if profile["schemaVersion"] != SCHEMA_VERSION or not isinstance(profile["model"], dict):
        raise ValueError("Invalid execution profile")
    string(profile["runtimeVersion"], 80)
    if any(profile["model"].get(key) != value for key, value in model.items()):
        raise ValueError("Execution profile belongs to different model weights")
    expected_policy = {**policy.to_dict(), "sha256": fingerprint(policy.to_dict())}
    if canonical_json(profile["policy"]) != canonical_json(expected_policy):
        raise ValueError("Execution profile has a different policy")
    calibration = fields(profile["calibration"], {"status", "temperature", "semantics"})
    if (calibration["status"] != "uncalibrated" or number(calibration["temperature"], .01, 100) != 1
            or calibration["semantics"] != "softmax_over_allowed_options"):
        raise ValueError("Freeze the raw uncalibrated execution profile")
    if "inputFingerprintVersions" in profile:
        versions = profile["inputFingerprintVersions"]
        if (not isinstance(versions, list) or not versions or any(not isinstance(v, str) or v not in INPUT_FINGERPRINT_VERSIONS for v in versions)
                or len(set(versions)) != len(versions)):
            raise ValueError("Unsupported execution fingerprint versions")
    if len(canonical_json(profile).encode()) > 16_000:
        raise ValueError("Execution profile is too large")
    return profile


def freeze_plan(dataset: dict, policy: Policy, criteria: dict, model: dict, *, execution_profile: dict | None = None) -> dict:
    validate_dataset(dataset)
    if dataset.get("usage") == "development-only":
        raise ValueError("Development-only data cannot be used for a calibration/holdout experiment")
    validate_criteria(criteria)
    fields(model, {"repository", "revision", "artifactSha256", "tokenizerSha256"})
    for value in model.values():
        string(value, 300)
    calibration = [c for c in dataset["cases"] if c["split"] == "calibration"]
    holdout = [c for c in dataset["cases"] if c["split"] == "holdout"]
    if not calibration or not holdout:
        raise ValueError("Both calibration and holdout splits are required")
    scopes = {Request.from_dict(c["request"]).schema_sha256 for c in calibration}
    if any(Request.from_dict(c["request"]).schema_sha256 not in scopes for c in holdout):
        raise ValueError("Holdout contains a question/candidate schema absent from calibration")
    profile = validate_execution_profile(execution_profile, policy, model) if execution_profile is not None else None
    return sealed({"schemaVersion": PROFILED_PLAN_SCHEMA if profile is not None else PLAN_SCHEMA,
                   "createdAt": datetime.now(timezone.utc).isoformat(),
                   "datasetSha256": fingerprint(dataset), "datasetId": dataset["id"],
                   "policy": policy.to_dict(), "criteria": criteria, "model": model,
                   "splitCounts": dict(Counter(c["split"] for c in dataset["cases"])),
                   "schemaSha256s": sorted(scopes), **({"executionProfile": copy.deepcopy(profile),
                       "executionProfileSha256": fingerprint(profile)} if profile is not None else {})})


def validate_plan(plan: dict, dataset: dict) -> None:
    schema = plan.get("schemaVersion") if isinstance(plan, dict) else None
    if schema not in {PLAN_SCHEMA, PROFILED_PLAN_SCHEMA}:
        raise ValueError("Unsupported experiment schema")
    verify_seal(plan, schema)
    fields(plan, {"schemaVersion", "createdAt", "datasetSha256", "datasetId", "policy", "criteria",
                  "model", "splitCounts", "schemaSha256s", "sha256"}
           | ({"executionProfile", "executionProfileSha256"} if schema == PROFILED_PLAN_SCHEMA else set()))
    validate_dataset(dataset)
    validate_criteria(plan["criteria"])
    Policy.from_dict(plan["policy"])
    if plan["datasetSha256"] != fingerprint(dataset):
        raise ValueError("Dataset differs from the frozen experiment")
    if schema == PROFILED_PLAN_SCHEMA:
        profile = validate_execution_profile(plan["executionProfile"], Policy.from_dict(plan["policy"]), plan["model"])
        if fingerprint(profile) != plan["executionProfileSha256"]:
            raise ValueError("Frozen execution profile checksum mismatch")


def validate_execution_evidence(plan: dict, report: dict, *, phase: str) -> None:
    if report.get("policy") != plan["policy"]:
        raise ValueError("Scored policy differs from the frozen experiment")
    try:
        frozen, started = (datetime.fromisoformat(value) for value in (plan["createdAt"], report["startedAt"]))
        if frozen.tzinfo is None or started.tzinfo is None or started < frozen:
            raise ValueError
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"{phase} must be scored after the plan has been frozen") from error
    try:
        finished = datetime.fromisoformat(report["createdAt"])
        if finished.tzinfo is None or not started <= finished <= datetime.now(timezone.utc):
            raise ValueError
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"{phase} has an invalid scoring completion timestamp") from error
    if plan["schemaVersion"] == PROFILED_PLAN_SCHEMA:
        profile = plan["executionProfile"]
        if (report.get("runtimeVersion") != profile["runtimeVersion"]
                or canonical_json(report.get("model")) != canonical_json(profile["model"])):
            raise ValueError("Scored execution profile differs from the frozen experiment")
        for row in report.get("cases", []):
            for key in ("result", "reversedResult"):
                if key in row and fingerprint({field: row[key].get(field) for field in profile}) != plan["executionProfileSha256"]:
                    raise ValueError("Result execution profile differs from the frozen experiment")


def scoring_rows(report: dict, dataset: dict, split: str, *, require_reverse: bool = False) -> list[tuple[dict, dict]]:
    """Validate raw evidence against the dataset, never trust cached aggregate metrics or gold labels."""
    if not isinstance(report, dict) or report.get("schemaVersion") != "agat.decision.evaluation.v1":
        raise ValueError("Unsupported scoring report")
    info = report.get("dataset", {})
    if not isinstance(info, dict) or info.get("sha256") != fingerprint(dataset) or info.get("split") != split:
        raise ValueError("Scoring report has the wrong dataset or split")
    if not isinstance(report.get("model"), dict) or not report["model"]:
        raise ValueError("Scoring report has no model identity")
    policy = Policy.from_dict(report.get("policy")).to_dict()
    expected = {c["id"]: c for c in dataset["cases"] if c["split"] == split}
    if not expected or not isinstance(report.get("cases"), list):
        raise ValueError("Scoring split is empty")
    found, output = set(), []
    for row in report["cases"]:
        if (not isinstance(row, dict) or not isinstance(row.get("id"), str)
                or row["id"] not in expected or row["id"] in found):
            raise ValueError("Unexpected or duplicate scored case")
        case = expected[row["id"]]
        found.add(row["id"])
        for key in ("family", "groupId", "labelSource", "expectedOptionId"):
            if row.get(key) != case[key]:
                raise ValueError("Score metadata or gold label differs from the dataset")
        pairs = [("result", case["request"])]
        if require_reverse or "reversedResult" in row:
            pairs.append(("reversedResult", {**case["request"], "options": list(reversed(case["request"]["options"]))}))
        for key, raw_request in pairs:
            result = row.get(key)
            request = Request.from_dict(raw_request)
            if not isinstance(result, dict) or result.get("inputSha256") != request.input_sha256:
                raise ValueError("Missing result or result bound to different input/options/order")
            if result.get("model") != report["model"] or result.get("id") != case["id"]:
                raise ValueError("Mixed model identities or case IDs in scoring report")
            if (result.get("schemaVersion") != SCHEMA_VERSION or result.get("mode") != "shadow"
                    or result.get("policy") != {**policy, "sha256": fingerprint(policy)}):
                raise ValueError("Scored result has a different contract or policy")
            if result.get("calibration") != {"status": "uncalibrated", "temperature": 1.0,
                                              "semantics": "softmax_over_allowed_options"}:
                raise ValueError("Fit/qualification requires raw, uncalibrated score evidence")
            if result.get("status") not in ("ok", "abstain", "error"):
                raise ValueError("Unknown result status")
            if result["status"] != "error":
                distribution = result.get("distribution")
                if (not isinstance(distribution, list) or any(not isinstance(x, dict) for x in distribution)
                        or [x.get("id") for x in distribution] != [o.id for o in request.options]):
                    raise ValueError("Score labels do not match the request order")
                probabilities([x.get("logit") for x in distribution], len(request.options))
                if result.get("generatedTokens") != 0 or type(result.get("inputTokens")) is not int or result["inputTokens"] < 1:
                    raise ValueError("Invalid direct scoring token counts")
            number(result.get("durationMs"), 0, 1e12)
        output.append((case, row))
    if found != set(expected):
        raise ValueError("Scoring report dropped cases")
    return output


def fit_calibration(dataset: dict, report: dict, plan: dict) -> dict:
    validate_plan(plan, dataset)
    rows = scoring_rows(report, dataset, "calibration")
    validate_execution_evidence(plan, report, phase="Calibration")
    if any(report["model"].get(key) != value for key, value in plan["model"].items()):
        raise ValueError("Scored model differs from the frozen candidate")
    if len(rows) < 2 or len({case["groupId"] for case, _ in rows}) < 2:
        raise ValueError("Calibration requires at least two cases from different groups")
    if any(row["result"]["status"] == "error" for _, row in rows):
        raise ValueError("Calibration scoring has backend errors; no silent case dropping")
    examples = []
    for case, row in rows:
        result = row["result"]
        ids = [v["id"] for v in result["distribution"]]
        examples.append(([v["logit"] for v in result["distribution"]], ids.index(case["expectedOptionId"])))
    fit = fit_temperature(examples)
    return sealed({"schemaVersion": CALIBRATION_SCHEMA, "method": "temperature-scaling.v1",
                   "createdAt": datetime.now(timezone.utc).isoformat(), "temperature": fit["temperature"],
                   "fitSplit": "calibration", "datasetSha256": fingerprint(dataset),
                   "planSha256": plan["sha256"], "scoreReportSha256": fingerprint(report),
                   "model": report["model"], "schemaSha256s": plan["schemaSha256s"],
                   "fit": fit, "fitCaseIds": sorted(c["id"] for c, _ in rows),
                   "fitGroupIds": sorted({c["groupId"] for c, _ in rows}),
                   "labelSources": sorted({c["labelSource"] for c, _ in rows})})
