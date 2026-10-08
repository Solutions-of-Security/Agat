import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from decision_runtime.artifacts import sealed
from scripts.lib import decision_public_workflow as workflow
from scripts.test import test_decision_public_load_verification as fixtures

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("public_workflow_cli", ROOT/"scripts/run-public-support-workflow.py")
cli = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(cli)


def fixture():
    context, _, result = fixtures.fixture(); config = workflow.shared_config(context)
    plan = {"schemaVersion": workflow.PLAN_SCHEMA, "mode": "serial_closed_model_integration", "primary": "fixture_chat_completions",
            "ownersAppointed": False, "routingEnabled": False, "qualification": "not_assessed", "projectId": "default",
            "processId": "synthetic-public-workflow", "processVersion": 1, "profileSha256": context["profileSha256"], "config": config,
            "startAt": "2026-10-08T01:01:00.000Z", "inputs": [{"caseId": row["id"], "inputSha256": row["inputSha256"]} for row in context["inputs"]]}
    cohort = {"schemaVersion": "agat.decision.shadow-cohort.v1", "snapshot": {"storedCohortComplete": True, "truncated": False, "consistency": "single_database_snapshot"},
              "counts": {"instances": len(context["inputs"]), "runs": len(context["inputs"]), "storedShadowStages": len(context["inputs"])},
              "scope": {"projectId": "default", "processId": plan["processId"], "processVersion": 1, "startAt": plan["startAt"],
                        "endAt": "2026-10-08T01:01:01.000Z", "boundary": "process_instance_created_at_half_open"},
              "observedAt": "2026-10-08T01:02:00.000Z", "dataPolicy": {"taskInputsIncluded": False, "questionOptionsIncluded": False,
              "primaryOutputsIncluded": False, "eventsIncluded": False, "artifactsIncluded": False, "decisionProfileAndResultMetadataIncluded": True},
              "sloAccepted": False, "routingEnabled": False, "qualification": "not_assessed", "instances": [], "traces": []}
    routes = []
    for index, (case, measured) in enumerate(zip(context["inputs"], result["phase"]["rows"])):
        run = f"fixture_run_{index}"; stage = f"fixture_stage_{index}"; instance = f"fixture_instance_{index}"
        observation = {"mode": "shadow", "fallback": "primary", "status": measured["status"], "reason": measured["reason"], **copy.deepcopy(measured["observation"])}
        observation["result"]["id"] = stage
        binding = {"stageId": stage, "profileSha256": context["profileSha256"], "inputSha256": case["inputSha256"], "callerTimeoutMs": 10000}
        caller = {"assignmentId": f"fixture_assignment_{index}", "stageAttempt": 1, "negotiated": True, "intent": True,
                  "returned": {"callerTiming": observation["callerTiming"], "status": observation["status"], "reason": observation["reason"]}, "outcome": "returned"}
        assigned = {"assignmentId": caller["assignmentId"], "stageAttempt": 1, **{key: binding[key] for key in binding if key != "stageId"},
                    "observation": copy.deepcopy(observation), "outcome": "recorded"}
        cohort["instances"].append({"instanceId": instance, "runId": run, "processVersion": 1, "status": "completed", "replayOfInstanceId": None,
            "replayMode": "normal", "createdAt": f"2026-10-08T01:01:00.00{index}Z"})
        cohort["traces"].append({"run": {"id": run}, "truncated": False,
            "decisionStageInventory": {"stages": [{**binding, "stageStatus": "completed", "assigned": True, "observationRecorded": True}]},
            "decisionCallerAccounting": {"stages": [{"stageId": stage, "coverage": "complete", "assignments": [caller]}]},
            "decisionAssignmentHistory": {"stages": [{"stageId": stage, "coverage": "complete", "assignments": [assigned]}]},
            "decisionObservations": [{**binding, "observation": observation}]})
        routes.append({"index": index, "caseId": case["id"], "inputSha256": case["inputSha256"], "instanceId": instance, "runId": run,
            "stageId": stage, "primaryCalls": 1, "primaryBranch": True, "wrongBranch": False, "runStatus": "completed"})
    return context, plan, cohort, routes


class PublicWorkflowTest(unittest.TestCase):
    def test_complete_inventory_counts_context_rejection_and_keeps_primary_route(self):
        context, plan, cohort, routes = fixture(); result = workflow.verify_inventory(context, plan, cohort, routes)
        self.assertEqual(result["boundCallerReturns"], 6); self.assertEqual(result["computed"], 5)
        self.assertEqual(result["physicalScheduledOutcomes"], {"ok": 5, "context_rejected": 1})
        self.assertTrue(result["primaryRoutePreserved"]); self.assertFalse(result["routingEnabled"])
        self.assertEqual(result["qualification"], "not_assessed"); self.assertFalse(result["ownersAppointed"])

    def test_missing_return_and_extra_replayed_instance_cannot_shrink_denominator(self):
        for mutate in (lambda c, r: c["traces"][0]["decisionCallerAccounting"]["stages"][0]["assignments"].clear(),
                       lambda c, r: c["traces"][0]["decisionObservations"].clear(), lambda c, r: r.pop(),
                       lambda c, r: c["instances"].append(copy.deepcopy(c["instances"][0])),
                       lambda c, r: c["instances"][0].update(replayOfInstanceId="source"),
                       lambda c, r: c["counts"].update(instances=True)):
            context, plan, cohort, routes = fixture(); mutate(cohort, routes)
            with self.assertRaises(ValueError): workflow.verify_inventory(context, plan, cohort, routes)

    def test_input_profile_ledger_and_primary_corruptions_fail(self):
        for mutate in (lambda c, r: c["traces"][0]["decisionStageInventory"]["stages"][0].update(inputSha256="0"*64),
                       lambda c, r: c["traces"][0]["decisionCallerAccounting"]["stages"][0].update(coverage="legacy_gap"),
                       lambda c, r: c["traces"][0]["decisionCallerAccounting"]["stages"][0]["assignments"][0].update(intent=False),
                       lambda c, r: c["traces"][0]["decisionAssignmentHistory"]["stages"][0]["assignments"][0].update(profileSha256="0"*64),
                       lambda c, r: r[0].update(primaryBranch=False), lambda c, r: r[0].update(primaryCalls=2),
                       lambda c, r: r[0].update(wrongBranch=True), lambda c, r: r[0].update(index=True)):
            context, plan, cohort, routes = fixture(); mutate(cohort, routes)
            with self.assertRaises(ValueError): workflow.verify_inventory(context, plan, cohort, routes)

    def test_cohort_scope_privacy_and_qualification_claims_rejected(self):
        for mutate in (lambda c: c["scope"].update(processVersion=True), lambda c: c["scope"].update(processId="wrong"),
                       lambda c: c["instances"][0].update(createdAt=c["scope"]["endAt"]),
                       lambda c: c["scope"].update(endAt="2026-10-08T01:03:00.000Z"),
                       lambda c: c["dataPolicy"].update(taskInputsIncluded=True), lambda c: c.update(routingEnabled=True),
                       lambda c: c["traces"][0]["decisionObservations"][0].update(context={"state": "raw fixture state"})):
            context, plan, cohort, routes = fixture(); mutate(cohort)
            with self.assertRaises(ValueError): workflow.verify_inventory(context, plan, cohort, routes)

    def test_mixed_decision_config_rejected_before_a_workflow_is_published(self):
        context, _, _, _ = fixture(); context["inputs"][0]["request"]["question"] += " changed"
        from decision_runtime.contracts import Request
        context["inputs"][0]["inputSha256"] = Request.from_dict(context["inputs"][0]["request"]).input_sha256
        context = sealed({key: value for key, value in context.items() if key != "sha256"})
        with self.assertRaises(ValueError): workflow.shared_config(context)

    def test_existing_output_rejected_before_sources_model_or_http(self):
        with tempfile.TemporaryDirectory() as temporary:
            args = ["--context-profile", "missing", "--context-profile-file-sha256", "0"*64, "--runtime-python", "missing",
                    "--manifest", "missing", "--evidence-dir", temporary]
            with patch.object(cli.launcher, "frozen_sources") as source, patch.object(cli.subprocess, "Popen") as process:
                self.assertEqual(cli.main(args), 1); source.assert_not_called(); process.assert_not_called()


if __name__ == "__main__": unittest.main()
