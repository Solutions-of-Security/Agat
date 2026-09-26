"""Independent holdout gates. No routing authority is granted by this report."""

from __future__ import annotations

import math
from datetime import datetime, timezone

from .artifacts import sealed
from .calibration import Calibration
from .calibration_workflow import PROFILED_PLAN_SCHEMA, scoring_rows, validate_execution_evidence, validate_plan
from .contracts import Policy, Request, fingerprint
from .evaluation import metrics, replay_scores


def binomial_upper(errors: int, count: int, confidence: float) -> float:
    """One-sided exact Clopper–Pearson upper bound for Bernoulli group failures."""
    if (type(count) is not int or type(errors) is not int or not 0 <= errors <= count
            or not 0 < confidence < 1):
        raise ValueError("Invalid binomial inputs")
    if count == 0 or errors == count:
        return 1.0
    if errors == 0:
        return -math.expm1(math.log1p(-confidence) / count)
    log_alpha = math.log1p(-confidence)
    coefficients = [math.lgamma(count + 1) - math.lgamma(j + 1) - math.lgamma(count - j + 1)
                    for j in range(errors + 1)]
    lo, hi = errors / count, 1.0
    for _ in range(80):
        p = (lo + hi) / 2
        if p >= 1:
            break
        terms = [coef + j * math.log(p) + (count - j) * math.log1p(-p)
                 for j, coef in enumerate(coefficients)]
        top = max(terms)
        log_cdf = top + math.log(math.fsum(math.exp(t - top) for t in terms))
        if log_cdf > log_alpha:
            lo = p
        else:
            hi = p
    return hi


def expert_reviewed(case: dict) -> bool:
    """Check explicit review records; local IDs are declarations, not authenticated identities."""
    if case.get("labelSource") != "expert-reviewed" or case.get("provenance", {}).get("kind") != "real":
        return False
    review = case.get("review", {})
    if not isinstance(review, dict):
        return False
    records = review.get("records", [])
    if not isinstance(records, list) or len(records) not in (2, 3) or any(not isinstance(r, dict) for r in records):
        return False
    request = Request.from_dict(case["request"])
    reviewers = []
    for row in records:
        if (not isinstance(row.get("reviewerId"), str) or not row["reviewerId"].strip()
                or not isinstance(row.get("rationale"), str) or not row["rationale"].strip()
                or not isinstance(row.get("reviewedAt"), str) or not row["reviewedAt"].strip()
                or row.get("inputSha256") != request.input_sha256 or row.get("id") != case["id"]
                or not isinstance(row.get("expectedOptionId"), str)
                or row.get("expectedOptionId") not in {o.id for o in request.options}):
            return False
        reviewers.append(row["reviewerId"])
    if len(set(reviewers)) != len(records):
        return False
    if review.get("status") == "agreed":
        return len(records) == 2 and all(r["expectedOptionId"] == case["expectedOptionId"] for r in records)
    return (review.get("status") == "adjudicated" and len(records) == 3
            and records[0]["expectedOptionId"] != records[1]["expectedOptionId"]
            and records[-1]["expectedOptionId"] == case["expectedOptionId"])


def group_risk(rows: list[dict], confidence: float) -> dict:
    groups = {}
    for row in rows:
        if row["result"]["status"] == "ok":
            groups[row["groupId"]] = groups.get(row["groupId"], False) or not row["correct"]
    errors = sum(groups.values())
    return {"acceptedGroups": len(groups), "groupsWithErrors": errors,
            "riskUpperBound": binomial_upper(errors, len(groups), confidence), "confidence": confidence}


def qualify(dataset: dict, raw_report: dict, artifact: dict, plan: dict) -> dict:
    validate_plan(plan, dataset)
    calibration = Calibration(artifact)
    calibration.validate_model(raw_report.get("model", {}))
    if artifact.get("planSha256") != plan["sha256"] or artifact.get("datasetSha256") != fingerprint(dataset):
        raise ValueError("Calibration and qualification use different frozen experiments")
    try:
        times = [datetime.fromisoformat(value) for value in
                 (plan["createdAt"], artifact["createdAt"], raw_report["startedAt"])]
        if any(t.tzinfo is None for t in times) or times != sorted(times):
            raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Holdout must be scored after the plan and calibration have been frozen") from exc
    fit_cases = [c for c in dataset["cases"] if c["split"] == "calibration"]
    if (artifact.get("fitCaseIds") != sorted(c["id"] for c in fit_cases)
            or artifact.get("fitGroupIds") != sorted({c["groupId"] for c in fit_cases})
            or artifact.get("schemaSha256s") != plan["schemaSha256s"]):
        raise ValueError("Calibration fit provenance differs from the frozen split")
    rows = scoring_rows(raw_report, dataset, "holdout", require_reverse=True)
    validate_execution_evidence(plan, raw_report, phase="Holdout")
    policy = Policy.from_dict(plan["policy"])
    baseline, adjusted, reversed_adjusted = [], [], []
    for case, recorded in rows:
        baseline.append(replay_scores(case, recorded["result"], raw_report["model"], policy, None))
        adjusted.append(replay_scores(case, recorded["result"], raw_report["model"], policy, calibration))
        reversed_adjusted.append(replay_scores(case, recorded["reversedResult"], raw_report["model"], policy, calibration, reverse=True))
    baseline_metrics, fitted_metrics, reversed_metrics = metrics(baseline), metrics(adjusted), metrics(reversed_adjusted)
    families = sorted({c["family"] for c, _ in rows})
    criteria = plan["criteria"]
    # Bonferroni for the overall bound and each family bound. Variants never add independent samples.
    bound_confidence = 1 - (1 - criteria["confidence"]) / (len(families) + 1)
    risk = group_risk(adjusted + reversed_adjusted, bound_confidence)
    gates = []

    def gate(name, passed, observed, required):
        gates.append({"name": name, "passed": bool(passed), "observed": observed, "required": required})

    target_cases = fit_cases + [case for case, _ in rows]
    gate("execution_profile_frozen", plan["schemaVersion"] == PROFILED_PLAN_SCHEMA,
         plan["schemaVersion"], PROFILED_PLAN_SCHEMA)
    reviewed = sum(expert_reviewed(c) for c in target_cases)
    gate("real_expert_review", reviewed == len(target_cases), reviewed, len(target_cases))
    errors = fitted_metrics["backendErrors"] + reversed_metrics["backendErrors"]
    gate("no_backend_errors", errors == 0, errors, 0)
    gate("minimum_accepted_groups", risk["acceptedGroups"] >= criteria["minAcceptedGroups"],
         risk["acceptedGroups"], criteria["minAcceptedGroups"])
    gate("group_risk_bound", risk["riskUpperBound"] <= criteria["maxGroupRisk"],
         risk["riskUpperBound"], criteria["maxGroupRisk"])
    gate("accuracy_both_orders", min(fitted_metrics["accuracyAllAttempts"], reversed_metrics["accuracyAllAttempts"]) >= criteria["minAccuracy"],
         min(fitted_metrics["accuracyAllAttempts"], reversed_metrics["accuracyAllAttempts"]), criteria["minAccuracy"])
    gate("coverage_both_orders", min(fitted_metrics["coverage"], reversed_metrics["coverage"]) >= criteria["minCoverage"],
         min(fitted_metrics["coverage"], reversed_metrics["coverage"]), criteria["minCoverage"])
    family_metrics = {}
    for family in families:
        original_rows = [r for r in adjusted if r["family"] == family]
        reversed_rows = [r for r in reversed_adjusted if r["family"] == family]
        fr = group_risk(original_rows + reversed_rows, bound_confidence)
        fm, rm = metrics(original_rows), metrics(reversed_rows)
        family_metrics[family] = {"risk": fr, "original": fm, "reversed": rm}
        gate(f"{family}:accepted_groups", fr["acceptedGroups"] >= criteria["minAcceptedGroupsPerFamily"],
             fr["acceptedGroups"], criteria["minAcceptedGroupsPerFamily"])
        gate(f"{family}:risk_bound", fr["riskUpperBound"] <= criteria["maxGroupRisk"], fr["riskUpperBound"], criteria["maxGroupRisk"])
        gate(f"{family}:coverage", min(fm["coverage"], rm["coverage"]) >= criteria["minCoverage"],
             min(fm["coverage"], rm["coverage"]), criteria["minCoverage"])
    passed = all(g["passed"] for g in gates)
    return sealed({"schemaVersion": "agat.decision.qualification.v1", "createdAt": datetime.now(timezone.utc).isoformat(),
                   "status": "pass" if passed else "not_qualified", "routingEnabled": False,
                   "datasetSha256": fingerprint(dataset), "planSha256": plan["sha256"],
                   "calibrationSha256": artifact["sha256"], "rawReportSha256": fingerprint(raw_report),
                   "model": raw_report["model"], "policy": policy.to_dict(), "criteria": criteria,
                   "method": "one-sided-clopper-pearson; group-any-error; bonferroni-global-and-families",
                   "baseline": baseline_metrics, "calibrated": fitted_metrics, "reversed": reversed_metrics,
                   "risk": risk, "families": family_metrics, "gates": gates,
                   "timingSource": "recorded_raw_inference; postprocessing is not timed as inference",
                   "cases": [{"id": a["id"], "original": a, "reversed": b}
                             for a, b in zip(adjusted, reversed_adjusted)]})
