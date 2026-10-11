"""CLI preflight: do not consume calibration/holdout inputs with an unfrozen setup."""

from datetime import datetime, timezone

from .calibration import Calibration
from .calibration_workflow import PROFILED_PLAN_SCHEMA, validate_plan
from .contracts import fingerprint


def validate_evaluation_start(dataset: dict, split: str, plan: dict | None, artifact: dict | None,
                              *, reverse_options: bool, calibrated: bool) -> None:
    if split == "development":
        if plan is not None or artifact is not None:
            raise ValueError("Development evaluation does not use a qualification plan or frozen calibration")
        return
    if plan is None:
        raise ValueError("Calibration/holdout evaluation requires --plan before inference")
    validate_plan(plan, dataset)
    if plan["schemaVersion"] != PROFILED_PLAN_SCHEMA:
        raise ValueError("Evaluation requires a v2 plan with a frozen execution profile")
    if calibrated:
        raise ValueError("Qualification evaluation requires raw logits; do not apply --calibration")
    try:
        frozen = datetime.fromisoformat(plan["createdAt"])
        if frozen.tzinfo is None or frozen > datetime.now(timezone.utc):
            raise ValueError
    except (TypeError, ValueError) as error:
        raise ValueError("Invalid plan creation timestamp") from error
    if split == "calibration":
        if artifact is not None:
            raise ValueError("Calibration scores must precede --frozen-calibration")
        return
    if split != "holdout":
        raise ValueError("Unknown evaluation split")
    if artifact is None or not reverse_options:
        raise ValueError("Holdout requires --frozen-calibration and --reverse-options before inference")
    calibration = Calibration(artifact)
    calibration.validate_model(plan["executionProfile"]["model"])
    if artifact["planSha256"] != plan["sha256"] or artifact["datasetSha256"] != fingerprint(dataset):
        raise ValueError("Frozen calibration belongs to a different experiment")
    cases = [case for case in dataset["cases"] if case["split"] == "calibration"]
    if (artifact["fitCaseIds"] != sorted(case["id"] for case in cases)
            or artifact["fitGroupIds"] != sorted({case["groupId"] for case in cases})
            or artifact["schemaSha256s"] != plan["schemaSha256s"]):
        raise ValueError("Frozen calibration provenance differs from the plan")
    try:
        fitted = datetime.fromisoformat(artifact["createdAt"])
        if fitted.tzinfo is None or not frozen <= fitted <= datetime.now(timezone.utc):
            raise ValueError
    except (TypeError, ValueError) as error:
        raise ValueError("Frozen calibration must follow the plan and precede holdout") from error


def validate_evaluation_profile(plan: dict | None, profile: dict) -> None:
    if plan is not None and fingerprint(profile) != plan["executionProfileSha256"]:
        raise ValueError("Runtime differs from the frozen execution profile; no evaluation inputs were scored")
