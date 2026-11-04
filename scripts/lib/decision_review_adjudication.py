"""Prepare unresolved review tasks without creating reference labels or owners."""
from copy import deepcopy

from decision_runtime.annotations import prepare_review
from decision_runtime.artifacts import sealed
from decision_runtime.contracts import canonical_json, parse_json
from scripts.lib.decision_public_sources import pinned_input
from scripts.lib.decision_review_pair import compare_pair
from scripts.lib.decision_review_session import MAX_REVIEW_BYTES
from scripts.lib.decision_shadow_pilot import require
import hashlib


def encoded(value):
    return (canonical_json(value) + "\n").encode("utf-8")


def prepare_handoff(first, second, comparison_path, comparison_file_sha256):
    comparison = parse_json(pinned_input(comparison_path, comparison_file_sha256, MAX_REVIEW_BYTES))
    expected = compare_pair(first, second)
    require(comparison == expected, "Comparison differs from the complete pinned source reviews")
    require(comparison["requiresAdjudication"] and comparison["disagreements"],
            "No disagreements require an adjudication handoff")
    original = parse_json(pinned_input(first[4], first[5], MAX_REVIEW_BYTES))
    disputed = {item["id"] for item in comparison["disagreements"]}
    blank = prepare_review(original["pool"], original["splitSeed"])
    blank["labels"] = [label for label in blank["labels"] if label["id"] in disputed]
    sources = {case["id"]: case for case in original["pool"]["cases"]}
    notes = {"schemaVersion": "agat.decision.review-adjudication-notes.v1",
             "comparisonSha256": comparison["sha256"], "poolSha256": original["poolSha256"],
             "splitSeed": original["splitSeed"], "declaredReviewerIds": comparison["reviewerIds"],
             "cases": [{"case": deepcopy(sources[item["id"]]), "inputSha256": item["inputSha256"],
                        "firstOptionId": item["firstOptionId"], "secondOptionId": item["secondOptionId"],
                        "firstRationale": item["firstRationale"], "secondRationale": item["secondRationale"]}
                       for item in comparison["disagreements"]],
             "reviewerIdentityVerified": False, "humanExecutionVerified": False,
             "expertQualificationsVerified": False, "referenceLabelsCreated": 0,
             "routingEnabled": False, "qualification": "not_assessed"}
    outputs = {"adjudication.review.blank.json": blank, "case-notes.json": notes}
    bindings = [{"sessionFileSha256": binding[1], "initialReviewFileSha256": binding[3],
                 "savedReviewFileSha256": binding[5]} for binding in (first, second)]
    packet = sealed({"schemaVersion": "agat.decision.review-adjudication-handoff.v1",
                     "status": "awaiting_adjudication", "comparisonFileSha256": comparison_file_sha256,
                     "comparisonSha256": comparison["sha256"], "sourceReviewBindings": bindings,
                     "poolSha256": original["poolSha256"], "splitSeed": original["splitSeed"],
                     "sourceCaseCount": comparison["caseCount"], "sourceGroupCount": comparison["groupCount"],
                     "disputedCaseCount": len(disputed), "disputedGroupCount": len({item["groupId"] for item in comparison["disagreements"]}),
                     "disputedCaseIds": [item["id"] for item in comparison["disagreements"]],
                     "declaredReviewerIds": comparison["reviewerIds"], "adjudicatorId": None,
                     "reviewedAt": None, "resolvedCases": 0,
                     "outputFiles": {name: {"sha256": hashlib.sha256(encoded(value)).hexdigest(),
                                             "bytes": len(encoded(value))} for name, value in outputs.items()},
                     "reviewerIdentityVerified": False, "humanExecutionVerified": False,
                     "independentReviewVerified": False, "expertQualificationsVerified": False,
                     "ownersAppointed": False, "referenceLabelsCreated": 0, "modelCallsDuringPreparation": 0,
                     "classificationAccuracyMeasured": False, "routingEnabled": False, "qualification": "not_assessed"})
    return {"packet.json": packet, **outputs}
