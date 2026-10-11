"""Blind review packs and explicit consensus; never manufacture human labels."""

from __future__ import annotations

import copy
from collections import Counter

from .contracts import Request, fields, fingerprint, string

POOL_SCHEMA = "agat.decision.pool.v1"
REVIEW_SCHEMA = "agat.decision.review.v1"


def group_split(seed: str, group: str) -> str:
    """Keep group assignment identical for annotation and development diagnostics."""
    string(seed, 100)
    string(group, 100, identifier=True)
    fraction = int(fingerprint([seed, group])[:16], 16) / 2**64
    return "development" if fraction < 0.4 else "calibration" if fraction < 0.7 else "holdout"


def validate_pool(pool: dict) -> dict:
    fields(pool, {"schemaVersion", "id", "cases"})
    if pool["schemaVersion"] != POOL_SCHEMA or not isinstance(pool["cases"], list) or not pool["cases"]:
        raise ValueError("Expected a nonempty annotation pool")
    string(pool["id"], 100, identifier=True)
    seen = set()
    for case in pool["cases"]:
        fields(case, {"id", "family", "groupId", "provenance", "request"})
        for key in ("id", "family", "groupId"):
            string(case[key], 100, identifier=True)
        request = Request.from_dict(case["request"])
        if request.id != case["id"] or request.id in seen:
            raise ValueError("Duplicate or mismatched pool case ID")
        seen.add(request.id)
        source = fields(case["provenance"], {"kind", "sourceId", "reference"})
        if source["kind"] not in ("real", "synthetic"):
            raise ValueError("Source kind must explicitly be real or synthetic")
        string(source["sourceId"], 100, identifier=True)
        string(source["reference"], 1000)
    return pool


def prepare_review(pool: dict, seed: str) -> dict:
    validate_pool(pool)
    string(seed, 100)
    # No gold labels, predictions, probabilities or split names appear in the review tasks.
    return {
        "schemaVersion": REVIEW_SCHEMA, "pool": copy.deepcopy(pool), "poolSha256": fingerprint(pool),
        "splitSeed": seed, "reviewerId": None, "reviewedAt": None,
        "labels": [{"id": case["id"], "inputSha256": Request.from_dict(case["request"]).input_sha256,
                    "expectedOptionId": None, "rationale": None} for case in pool["cases"]],
    }


def _review_labels(review: dict, pool: dict, expected_ids: set[str]) -> dict:
    fields(review, {"schemaVersion", "pool", "poolSha256", "splitSeed", "reviewerId", "reviewedAt", "labels"})
    if (review["schemaVersion"] != REVIEW_SCHEMA or review["pool"] != pool
            or review["poolSha256"] != fingerprint(pool)):
        raise ValueError("Review source was modified or belongs to another pool")
    string(review["reviewerId"], 100, identifier=True)
    string(review["reviewedAt"], 100)
    string(review["splitSeed"], 100)
    if not isinstance(review["labels"], list):
        raise ValueError("Expected review labels")
    by_id = {}
    source = {c["id"]: c for c in pool["cases"]}
    for item in review["labels"]:
        fields(item, {"id", "inputSha256", "expectedOptionId", "rationale"})
        case_id = string(item["id"], 100, identifier=True)
        if case_id not in expected_ids or case_id in by_id:
            raise ValueError("Duplicate or unexpected reviewed case")
        request = Request.from_dict(source[case_id]["request"])
        if item["inputSha256"] != request.input_sha256:
            raise ValueError("Review label is bound to different input")
        if not isinstance(item["expectedOptionId"], str) or item["expectedOptionId"] not in {o.id for o in request.options}:
            raise ValueError("Review is incomplete or label is not an allowed option")
        string(item["rationale"], 4000)
        by_id[case_id] = item
    if set(by_id) != expected_ids:
        raise ValueError("Missing reviewed cases")
    return by_id


def finalize_reviews(first: dict, second: dict, adjudication: dict | None = None) -> dict:
    if not isinstance(first, dict) or "pool" not in first:
        raise ValueError("Missing review pool")
    pool = validate_pool(first["pool"])
    ids = {c["id"] for c in pool["cases"]}
    a, b = _review_labels(first, pool, ids), _review_labels(second, pool, ids)
    if first["reviewerId"] == second["reviewerId"]:
        raise ValueError("Two distinct reviewers are required for consensus")
    if first["splitSeed"] != second["splitSeed"]:
        raise ValueError("Reviews use different split seeds")
    disputed = {key for key in ids if a[key]["expectedOptionId"] != b[key]["expectedOptionId"]}
    resolved = {}
    if disputed:
        if adjudication is None:
            raise ValueError(f"Unresolved disagreements: {', '.join(sorted(disputed))}")
        resolved = _review_labels(adjudication, pool, disputed)
        if (adjudication["reviewerId"] in {first["reviewerId"], second["reviewerId"]}
                or adjudication["splitSeed"] != first["splitSeed"]):
            raise ValueError("Adjudication needs a distinct reviewer and the same split seed")
    elif adjudication is not None:
        raise ValueError("Adjudication supplied without disagreements")

    # Stable group-level assignment. Freeze the seed before annotation; never search seeds by accuracy.
    group_splits = {}
    for case in pool["cases"]:
        group = case["groupId"]
        group_splits[group] = group_split(first["splitSeed"], group)
    cases = []
    for original in pool["cases"]:
        case = copy.deepcopy(original)
        case_id = case["id"]
        decision = resolved.get(case_id, a[case_id])
        reviews = [{"reviewerId": r["reviewerId"], "reviewedAt": r["reviewedAt"],
                    **labels[case_id]} for r, labels in ((first, a), (second, b))]
        if case_id in disputed:
            reviews.append({"reviewerId": adjudication["reviewerId"],
                            "reviewedAt": adjudication["reviewedAt"], **resolved[case_id]})
        case.update(split=group_splits[case["groupId"]], expectedOptionId=decision["expectedOptionId"],
                    labelSource="expert-reviewed", review={"status": "adjudicated" if case_id in disputed else "agreed",
                                                          "records": reviews})
        cases.append(case)
    dataset = {"schemaVersion": "agat.decision.dataset.v1", "id": pool["id"] + ".reviewed",
               "annotation": {"poolSha256": fingerprint(pool), "splitSeed": first["splitSeed"],
                              "reviewSha256": [fingerprint(first), fingerprint(second)],
                              "adjudicationSha256": fingerprint(adjudication) if adjudication else None,
                              "splitCounts": dict(Counter(c["split"] for c in cases))}, "cases": cases}
    from .evaluation import validate_dataset

    return validate_dataset(dataset)
