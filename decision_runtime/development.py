"""Development diagnostics from assistant approvals, with reserved groups kept blind."""

from __future__ import annotations

import copy
import hashlib
from collections import Counter
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path

from .annotations import group_split, prepare_review, validate_pool
from .artifacts import read_json, sealed
from .calibration_workflow import scoring_rows
from .contracts import Policy, Request, fingerprint, parse_json, string
from .evaluation import metrics, replay_scores, validate_dataset


def _mapping(value, description: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"Expected {description}")
    return value


def _index(rows, key: str, description: str) -> dict:
    if not isinstance(rows, list):
        raise ValueError(f"Expected {description}")
    result = {}
    for row in rows:
        _mapping(row, description)
        identifier = string(row.get(key), 300)
        if identifier in result:
            raise ValueError(f"Duplicate entry in {description}")
        result[identifier] = row
    return result


def prepare_development(pool_path: Path, approval_path: Path, review_path: Path,
                        split_reference_path: Path) -> dict:
    """Import the exact approved revision; do not turn assistant labels into human reviews.

    All paths are explicit caller inputs. Paths embedded in JSON are never opened.
    The original blank review is the seed authority and must be bound by the approved review.
    """
    pool_bytes, review_bytes, split_bytes = (p.read_bytes() for p in
                                           (pool_path, review_path, split_reference_path))
    pool = validate_pool(parse_json(pool_bytes))
    approval = _mapping(read_json(approval_path), "annotation approval")
    review = _mapping(parse_json(review_bytes), "source review")
    reference = _mapping(parse_json(split_bytes), "split reference")
    pool_sha = fingerprint(pool)
    if (approval.get("schemaVersion") != "agat.decision.annotation-approval.v1"
            or approval.get("status") != "approved"
            or approval.get("scope") != "assistant_review_of_source_bound_labels"
            or approval.get("labelSource") != "assistant-reviewed"
            or approval.get("poolSha256") != pool_sha):
        raise ValueError("Expected an assistant approval for this exact pool")
    if (review.get("schemaVersion") != "agat.decision.research-review.v1"
            or review.get("status") != "approved"):
        raise ValueError("Source review is not approved")
    source_review = _mapping(approval.get("sourceReview"), "source review binding")
    review_sha = hashlib.sha256(review_bytes).hexdigest()
    if (source_review.get("path") != review_path.name
            or source_review.get("bytesSha256") != review_sha):
        raise ValueError("Source review bytes differ from the approval")
    reviewer = _mapping(review.get("reviewer"), "reviewer identity")
    if (reviewer.get("kind") != "assistant" or reviewer.get("independentHumanReview") is not False
            or reviewer != approval.get("approvedBy") or approval.get("humanReviewCompleted") is not False):
        raise ValueError("Assistant approval must not claim independent human review")
    for artifact in (approval, review):
        if any(artifact.get(key) is not False for key in ("expertReviewed", "routingEnabled", "qualifiedForRouting")):
            raise ValueError("Annotation approval does not grant qualification or routing authority")
    annotation_approval = _mapping(review.get("annotationApproval"), "annotation approval scope")
    if (annotation_approval.get("approved") is not True
            or annotation_approval.get("scope") != approval["scope"]
            or annotation_approval.get("blockingFindingIds") != []):
        raise ValueError("Annotation review has unresolved findings")
    findings = _index(review.get("findings"), "id", "review findings")
    if any(f.get("status") != "closed" or f.get("blocking") is not False for f in findings.values()):
        raise ValueError("Annotation review has unresolved findings")
    source = _mapping(review.get("sourceVersion"), "source version")
    if source.get("poolSha256") != pool_sha:
        raise ValueError("Source review belongs to another pool")
    files = _index(source.get("files"), "path", "source file bindings")
    for path, raw in ((pool_path, pool_bytes), (split_reference_path, split_bytes)):
        if files.get(path.name, {}).get("bytesSha256") != hashlib.sha256(raw).hexdigest():
            raise ValueError("Pool or split reference bytes differ from the approved source version")
    seed = string(reference.get("splitSeed"), 100)
    if reference != prepare_review(pool, seed):
        raise ValueError("Split reference must be the unchanged blank review package")
    labels = _index(approval.get("labels"), "id", "approved labels")
    reviewed = _index(review.get("cases"), "id", "reviewed cases")
    ids = {case["id"] for case in pool["cases"]}
    if set(labels) != ids or set(reviewed) != ids:
        raise ValueError("Approval and source review must cover every pool case exactly once")
    if review.get("counts") != {"reviewedCases": len(ids), "confirmedLabels": len(ids),
                                "changesRequested": 0, "humanReviewedCases": 0}:
        raise ValueError("Source review counts do not match the pool")
    cases = []
    for original in pool["cases"]:
        request = Request.from_dict(original["request"])
        label, decision = labels[original["id"]], reviewed[original["id"]]
        expected = label.get("expectedOptionId")
        if (not isinstance(expected, str) or expected not in {o.id for o in request.options}
                or label.get("inputSha256") != request.input_sha256
                or decision.get("inputSha256") != request.input_sha256
                or decision.get("schemaSha256") != request.schema_sha256
                or decision.get("family") != original["family"]
                or decision.get("verdict") != "confirmed" or decision.get("confirmedOptionId") != expected
                or decision.get("rationale") != label.get("rationale")):
            raise ValueError("Approved label differs from the source-bound reviewed case")
        rationale = string(label.get("rationale"), 4000)
        cases.append({**copy.deepcopy(original), "split": group_split(seed, original["groupId"]),
                      "expectedOptionId": expected, "labelSource": "assistant-reviewed",
                      "annotationRationale": rationale})
    # Validate the whole pool before excluding reserved cases: duplicated sources must not leak.
    validate_dataset({"schemaVersion": "agat.decision.dataset.v1", "id": pool["id"], "cases": cases})
    development = [c for c in cases if c["split"] == "development"]
    if not development:
        raise ValueError("Fixed seed assigns no development cases; add independent sources, do not search seeds")
    result = sealed({
        "schemaVersion": "agat.decision.dataset.v1", "id": pool["id"] + ".development",
        "usage": "development-only", "routingEnabled": False, "qualifiedForRouting": False,
        "annotation": {"scope": "assistant-reviewed-development", "poolSha256": pool_sha,
                       "approvalSha256": fingerprint(approval), "reviewBytesSha256": review_sha,
                       "splitReferenceBytesSha256": hashlib.sha256(split_bytes).hexdigest(),
                       "splitSeed": seed, "poolSplitCounts": dict(Counter(c["split"] for c in cases)),
                       "reservedCases": [{k: c[k] for k in ("id", "groupId", "split")}
                                         for c in cases if c["split"] != "development"]},
        "cases": development,
    })
    return validate_dataset(result)


def _confusion(rows: list[dict]) -> list[dict]:
    counts = Counter((r["family"], r["expectedOptionId"], r["result"]["selectedOptionId"]) for r in rows)
    return [{"family": family, "expectedOptionId": gold, "selectedOptionId": selected, "count": count}
            for (family, gold, selected), count in sorted(counts.items(), key=lambda pair: (*pair[0][:2], pair[0][2] or ""))]


def _summary(rows: list[dict]) -> dict:
    groups = {r["groupId"] for r in rows}
    accepted = {r["groupId"] for r in rows if r["result"]["status"] == "ok"}
    wrong = {r["groupId"] for r in rows if r["result"]["status"] == "ok" and not r["correct"]}
    return {"metrics": metrics(rows), "groupCount": len(groups), "acceptedGroupCount": len(accepted),
            "groupsWithAcceptedErrors": sorted(wrong), "confusion": _confusion(rows)}


def _outcome(row: dict) -> dict:
    result = row["result"]
    return {"correct": row["correct"], **{key: result.get(key) for key in
            ("status", "reason", "selectedOptionId", "selectedProbability", "margin")}}


def _paired(left: list[dict], right: list[dict]) -> dict:
    pairs = [(a, b) for a, b in zip(left, right, strict=True)
             if a["result"]["status"] != "error" and b["result"]["status"] != "error"]
    return {"attemptedPairs": len(left), "scoredPairs": len(pairs),
            "pairsWithBackendErrors": len(left) - len(pairs),
            "bothCorrect": sum(a["correct"] and b["correct"] for a, b in pairs),
            "leftOnlyCorrect": sum(a["correct"] and not b["correct"] for a, b in pairs),
            "rightOnlyCorrect": sum(not a["correct"] and b["correct"] for a, b in pairs),
            "bothIncorrect": sum(not a["correct"] and not b["correct"] for a, b in pairs),
            "labelDisagreements": sum(a["result"]["selectedOptionId"] != b["result"]["selectedOptionId"]
                                      for a, b in pairs)}


def compare_development(dataset: dict, reports: list[dict]) -> dict:
    """Compare candidates on the same development inputs and policy, recomputing from logits."""
    validate_dataset(dataset)
    if not isinstance(reports, list) or len(reports) < 2:
        raise ValueError("At least two candidate reports are required")
    cases = [c for c in dataset["cases"] if c["split"] == "development"]
    if not cases:
        raise ValueError("Development split is empty")
    models, outcomes, policy_dict = [], {}, None
    families = sorted({c["family"] for c in cases})
    for report in reports:
        scored = scoring_rows(report, dataset, "development", require_reverse=True)
        policy = Policy.from_dict(report.get("policy"))
        if policy_dict is not None and policy.to_dict() != policy_dict:
            raise ValueError("Candidate reports use different policies")
        policy_dict = policy.to_dict()
        model_id = fingerprint(report["model"])
        if model_id in outcomes:
            raise ValueError("Duplicate candidate model identity")
        by_id = {case["id"]: row for case, row in scored}
        original, reversed_rows = [], []
        for case in cases:
            row = by_id[case["id"]]
            original.append(replay_scores(case, row["result"], report["model"], policy, None))
            reversed_rows.append(replay_scores(case, row["reversedResult"], report["model"], policy, None, reverse=True))
        outcomes[model_id] = (original, reversed_rows)
        diagnostics = []
        paired = [(a, b) for a, b in zip(original, reversed_rows, strict=True)
                  if a["result"]["status"] != "error" and b["result"]["status"] != "error"]
        changed = [a["id"] for a, b in paired if a["result"]["selectedOptionId"] != b["result"]["selectedOptionId"]]
        for a, b in zip(original, reversed_rows, strict=True):
            tags = set()
            for order, row in (("original", a), ("reversed", b)):
                status = row["result"]["status"]
                if status == "error":
                    tags.add(f"{order}:backend_error")
                else:
                    if not row["correct"]:
                        tags.add(f"{order}:incorrect_label")
                    if status == "ok" and not row["correct"]:
                        tags.add(f"{order}:accepted_error")
                    if status == "abstain":
                        tags.add(f"{order}:abstained")
            if a["id"] in changed:
                tags.add("order_disagreement")
            if tags:
                diagnostics.append({k: a[k] for k in ("id", "family", "groupId", "expectedOptionId")} |
                                   {"inputSha256": a["result"]["inputSha256"], "tags": sorted(tags),
                                    "original": _outcome(a), "reversed": _outcome(b)})
        models.append({"id": model_id, "model": report["model"], "rawReportSha256": fingerprint(report),
                       "original": _summary(original), "reversed": _summary(reversed_rows),
                       "families": {f: {"original": _summary([r for r in original if r["family"] == f]),
                                        "reversed": _summary([r for r in reversed_rows if r["family"] == f])}
                                    for f in families},
                       "optionOrder": {"attemptedPairs": len(cases), "scoredPairs": len(paired),
                                       "changedCaseIds": changed,
                                       "agreement": 1 - len(changed) / len(paired) if paired else None},
                       "diagnostics": diagnostics})
    pairwise = [{"leftModelId": a, "rightModelId": b,
                 "original": _paired(outcomes[a][0], outcomes[b][0]),
                 "reversed": _paired(outcomes[a][1], outcomes[b][1])}
                for a, b in combinations(outcomes, 2)]
    schemas = {}
    for case in cases:
        request = Request.from_dict(case["request"])
        key = (case["family"], request.schema_sha256)
        counts = schemas.setdefault(key, {o.id: 0 for o in request.options})
        counts[case["expectedOptionId"]] += 1
    return sealed({
        "schemaVersion": "agat.decision.development-comparison.v1",
        "createdAt": datetime.now(timezone.utc).isoformat(), "status": "diagnostic_only",
        "routingEnabled": False, "qualifiedForRouting": False, "modelSelection": "not_performed",
        "dataset": {"id": dataset["id"], "sha256": fingerprint(dataset), "split": "development",
                    "caseIds": [c["id"] for c in cases], "groupCount": len({c["groupId"] for c in cases}),
                    "labelSources": sorted({c["labelSource"] for c in cases})},
        "labelCoverage": [{"family": f, "schemaSha256": s, "counts": counts,
                           "missingOptionIds": sorted(k for k, v in counts.items() if v == 0)}
                          for (f, s), counts in sorted(schemas.items())],
        "policy": policy_dict, "models": models, "pairwise": pairwise,
        "timingSource": "recorded_raw_inference; postprocessing is not timed as inference",
        "limitations": ["Development comparison is not an independent holdout or model qualification.",
                        "Label provenance is retained; assistant approvals are not human expert reviews.",
                        "Dependent cases and option permutations are not independent observations.",
                        "No threshold tuning, calibration fitting or automatic model promotion is performed."],
    })
