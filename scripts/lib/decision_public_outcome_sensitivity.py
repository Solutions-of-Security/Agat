"""Posthoc descriptive decomposition; observed values never become gold labels."""
from collections import defaultdict

from decision_runtime.artifacts import sealed
from decision_runtime.contracts import Request
from scripts.lib.decision_public_permutation_diagnostic import AUTHORITY, verify_phase
from scripts.lib.decision_shadow_pilot import require

SCHEMA = "agat.decision.public-option-outcome-sensitivity.v1"
FLAGS = {"argmaxChanged": "argmaxChangedSourceCases", "statusChanged": "statusChangedSourceCases",
         "reasonChanged": "reasonChangedSourceCases", "valueChanged": "valueChangedSourceCases",
         "conflictingAcceptedValues": "conflictingAcceptedValuesSourceCases",
         "acceptedAndAbstained": "acceptedAndAbstainedSourceCases", "allOrdersAcceptedSameValue": "allOrdersAcceptedSameValueSourceCases",
         "allOrdersAbstained": "allOrdersAbstainedSourceCases", "originalAccepted": "originalAcceptedSourceCases",
         "anyOrderAccepted": "anyOrderAcceptedSourceCases", "originalAcceptedLaterAbstained": "originalAcceptedLaterAbstainedSourceCases",
         "originalAbstainedLaterAccepted": "originalAbstainedLaterAcceptedSourceCases"}


def decompose(phase, inputs, profile):
    observed,_ = verify_phase(phase,inputs,profile)
    require(all(Request.from_dict(case["request"]).kind=="choice" for case in inputs), "This decomposition concerns semantic choice values")
    grouped = defaultdict(list)
    for row in phase["rows"]: grouped[row["sourceCaseId"]].append(row)
    cases = []
    for case_id,rows in grouped.items():
        computed = all(row["status"] in {"ok","abstain"} for row in rows)
        excluded = all(row["status"]=="error" and row["reason"]=="context_too_long" for row in rows)
        detail = {"sourceCaseId":case_id,"sourceGroupId":rows[0]["sourceGroupId"],"scheduledVariants":len(rows),
                  "comparisonSupported":computed,"wholeContextRejected":excluded}
        if computed:
            bodies = [row["observation"]["result"] for row in rows]
            original = next(row["observation"]["result"] for row in rows if row["orderId"]=="rotation_0")
            ids = {body["selectedOptionId"] for body in bodies};statuses = {body["status"] for body in bodies}
            reasons = {body["reason"] for body in bodies};values = {body["value"] for body in bodies}
            accepted = {body["value"] for body in bodies if body["status"]=="ok"}
            detail.update(selectedSemanticIds=sorted(ids),acceptedSemanticValues=sorted(accepted),statuses=sorted(statuses),reasons=sorted(reasons),
                argmaxChanged=len(ids)>1,statusChanged=len(statuses)>1,reasonChanged=len(reasons)>1,valueChanged=len(values)>1,
                conflictingAcceptedValues=len(accepted)>1,acceptedAndAbstained=statuses=={"ok","abstain"},
                allOrdersAcceptedSameValue=statuses=={"ok"} and len(accepted)==1,allOrdersAbstained=statuses=={"abstain"},
                originalAccepted=original["status"]=="ok",anyOrderAccepted=bool(accepted),
                originalAcceptedLaterAbstained=original["status"]=="ok" and "abstain" in statuses,
                originalAbstainedLaterAccepted=original["status"]=="abstain" and "ok" in statuses)
        else:
            detail.update({key:None for key in FLAGS})
        cases.append(detail)
    compared = [case for case in cases if case["comparisonSupported"]]
    changed = [case for case in compared if any(case[key] for key in ("argmaxChanged","statusChanged","reasonChanged","valueChanged"))]
    require(len(compared)==observed["fullyComputedSourceCases"] and len(changed)==observed["changedSemanticOutcomeSourceCases"], "Decomposition lost its original denominator")
    counts = {public:sum(case[flag] is True for case in compared) for flag,public in FLAGS.items()}
    counts.update(sourceCases=len(cases),sourceGroups=observed["sourceGroups"],scheduledVariants=observed["scheduledVariants"],
        comparedSourceCases=len(compared),sourceGroupsWithComparedCases=len({case["sourceGroupId"] for case in compared}),
        wholeContextRejectedSourceCases=sum(case["wholeContextRejected"] for case in cases),
        mixedAdmissionSourceCases=sum(not case["comparisonSupported"] and not case["wholeContextRejected"] for case in cases),
        unsupportedComparisonSourceCases=len(cases)-len(compared),observedSemanticOutcomeChangedSourceCases=len(changed),
        conflictingAcceptedValuesSourceGroups=len({case["sourceGroupId"] for case in compared if case["conflictingAcceptedValues"]}))
    return sealed({"schemaVersion":SCHEMA,"analysisKind":"posthoc_descriptive_decomposition","summary":counts,"cases":cases,
                   "causalPositionBiasEstablished":False,"newModelCalls":0,**AUTHORITY})
