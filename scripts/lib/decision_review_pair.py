"""Compare two complete pinned review artifacts without assigning expert labels."""
from __future__ import annotations

from decision_runtime.artifacts import sealed
from decision_runtime.contracts import parse_json
from scripts.lib.decision_public_sources import pinned_input
from scripts.lib.decision_review_session import MAX_REVIEW_BYTES, verify_session
from scripts.lib.decision_shadow_pilot import require


def compare_pair(first, second):
    require(len(first) == len(second) == 6, "Use two complete artifact bindings")
    verifications = [verify_session(*binding) for binding in (first, second)]
    require(all(item["completeReviewArtifact"] for item in verifications),
            "Use two explicitly submitted complete review artifacts")
    reviews = [parse_json(pinned_input(binding[4], binding[5], MAX_REVIEW_BYTES)) for binding in (first, second)]
    a, b = reviews
    require(a["reviewerId"] != b["reviewerId"], "Use two distinct reviewer IDs")
    require(a["pool"] == b["pool"] and a["poolSha256"] == b["poolSha256"], "Review pools differ")
    require(a["splitSeed"] == b["splitSeed"], "Review split seeds differ")
    groups = {}; disagreements = []; agreement_count = 0
    for case, first_label, second_label in zip(a["pool"]["cases"], a["labels"], b["labels"]):
        group = groups.setdefault(case["groupId"], {"groupId": case["groupId"], "caseCount": 0,
                                                   "agreementCount": 0, "disagreementCount": 0})
        group["caseCount"] += 1
        if first_label["expectedOptionId"] == second_label["expectedOptionId"]:
            agreement_count += 1; group["agreementCount"] += 1
        else:
            group["disagreementCount"] += 1
            disagreements.append({"id": case["id"], "groupId": case["groupId"], "inputSha256": first_label["inputSha256"],
                                  "firstOptionId": first_label["expectedOptionId"], "secondOptionId": second_label["expectedOptionId"],
                                  "firstRationale": first_label["rationale"], "secondRationale": second_label["rationale"]})
    total = len(a["labels"])
    return sealed({"schemaVersion": "agat.decision.review-pair-comparison.v1", "status": "compared",
                   "poolSha256": a["poolSha256"], "splitSeed": a["splitSeed"],
                   "reviewerIds": [a["reviewerId"], b["reviewerId"]], "caseCount": total, "groupCount": len(groups),
                   "agreementCount": agreement_count, "disagreementCount": len(disagreements),
                   "agreementFraction": round(agreement_count / total, 6), "requiresAdjudication": bool(disagreements),
                   "groups": [groups[key] for key in sorted(groups)], "disagreements": disagreements,
                   "verifications": verifications, "reviewerIdentityVerified": False, "humanExecutionVerified": False,
                   "independentReviewVerified": False, "expertQualificationsVerified": False,
                   "classificationAccuracyMeasured": False, "referenceLabelsCreated": 0,
                   "modelCallsDuringComparison": 0, "routingEnabled": False, "qualification": "not_assessed"})
