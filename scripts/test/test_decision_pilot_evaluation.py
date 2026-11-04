import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from decision_runtime.artifacts import sealed, verify_seal
from decision_runtime.contracts import fingerprint
from scripts.lib.decision_pilot_evaluation import LIMITS, evaluate
from scripts.lib.decision_shadow_pilot import prepare
from scripts.lib.decision_shadow_sli import analyze
from scripts.test import test_decision_caller_inventory as caller_fixtures

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("pilot_evaluator_cli", ROOT / "scripts/evaluate-decision-shadow-pilot.py")
cli = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(cli)


class PilotEvaluationTest(unittest.TestCase):
    def setUp(self):
        self.fixture = caller_fixtures.CallerInventoryTest(); self.fixture.setUp(); self.profile = self.fixture.profile
        self.raw = json.dumps(self.profile, indent=2).encode(); self.sha = hashlib.sha256(self.raw).hexdigest()
        self.config = json.loads((ROOT / "docs/qualification/local-decisions/shadow/observability/pilot-config.blank.json").read_text())
        self.config.update(pilotId="fixture-pilot", projectId="fixture-project", processId="fixture-process", processVersion=1,
            owners={"runtime": "fixture-runtime", "business": "fixture-business"}, scenarioDescription="Synthetic fixture only",
            dataUseReference="Synthetic test fixture", window={"startAt": "2026-09-29T00:00:00.000Z", "endAt": "2026-09-30T00:00:00.000Z"})
        self.config["targets"].update(callerTimeoutMs=2000, latencyThresholdMs=1000)
        self.plan = sealed(prepare(self.config, self.profile, self.raw, self.sha, "coordinator_json_bytes",
            prepared_at="2026-09-28T00:00:00.000Z", source_commit="a"*40, source_files={"fixture": "b"*64}))

    def cohort(self, variants=None, count=1):
        variants = ["returned"] if variants is None else variants
        traces = []; instances = []
        for number in range(count):
            trace = self.fixture.trace(variants); trace["run"]["id"] = f"run-{number:04d}"
            for i, item in enumerate(trace["decisionStageInventory"]["stages"]):
                stage = f"stage-{number}-{i}"; assignment = str(uuid4())
                item.update(stageId=stage, profileSha256=self.sha)
                history = trace["decisionAssignmentHistory"]["stages"][i]; history["stageId"] = stage
                history["assignments"][0].update(assignmentId=assignment, profileSha256=self.sha)
                caller = trace["decisionCallerAccounting"]["stages"][i]; caller["stageId"] = stage
                caller["assignments"][0]["assignmentId"] = assignment
                observation = history["assignments"][0]["observation"]
                if observation and "result" in observation: observation["result"]["id"] = stage
            for row in trace["decisionObservations"]:
                i = int(row["stageId"].split("-")[-1]); row.update(stageId=f"stage-{number}-{i}", profileSha256=self.sha)
            traces.append(trace)
            instances.append({"instanceId": f"instance-{number:04d}", "runId": trace["run"]["id"], "processVersion": 1,
                "createdAt": self.config["window"]["startAt"], "status": "completed", "replayOfInstanceId": None, "replayMode": "live"})
        scope = {k: self.config[k] for k in ("projectId", "processId", "processVersion")}; scope.update(self.config["window"], boundary=self.config["cohortBoundary"])
        return {"schemaVersion": "agat.decision.shadow-cohort.v1", "snapshotId": "fixture-snapshot", "observedAt": "2026-10-01T00:00:00.000Z",
            "scope": scope, "snapshot": {"dialect": "sqlite", "consistency": "single_database_snapshot", "storedCohortComplete": True, "truncated": False},
            "limits": dict(LIMITS), "counts": {"instances": count, "runs": count, "storedStages": count*len(variants), "storedShadowStages": count*len(variants)},
            "runIdsSha256": hashlib.sha256(json.dumps([i["runId"] for i in instances], separators=(",", ":")).encode()).hexdigest(),
            "instances": instances, "traces": traces, "dataPolicy": {"taskInputsIncluded": False, "questionOptionsIncluded": False,
                "primaryOutputsIncluded": False, "eventsIncluded": False, "artifactsIncluded": False, "decisionProfileAndResultMetadataIncluded": True},
            "populationCoverageVerified": False, "eligibleWorkloadVerified": False, "httpAttemptInventoryVerified": False,
            "sloAccepted": False, "routingEnabled": False, "qualification": "not_assessed"}

    def test_good_cohort_compares_targets_without_granting_owner_agreement(self):
        result = evaluate(self.plan, self.cohort(), self.profile, self.raw)
        self.assertEqual(result["measurementStatus"], "stored_cohort_measured")
        self.assertEqual(result["targetComparison"]["withinCallerDeadline"]["status"], "conservative_target_met")
        self.assertEqual(result["targetComparison"]["timelyBoundResult"]["measuredRatio"], {"lower": 1.0, "upper": 1.0})
        for name in ("sloAccepted", "agreementVerified", "populationCoverageVerified", "httpAttemptInventoryVerified", "routingEnabled"):
            self.assertIs(result[name], False)
        self.assertEqual(result["qualification"], "not_assessed")

    def test_errors_missing_and_pending_returns_stay_in_the_denominator(self):
        result = evaluate(self.plan, self.cohort(["returned", "error", "missing"]), self.profile, self.raw)
        self.assertEqual(result["sli"]["callerAccounting"]["counts"]["intents"], 3)
        self.assertEqual(result["targetComparison"]["withinCallerDeadline"]["measuredRatio"], {"lower": 1/3, "upper": 2/3})
        self.assertEqual(result["targetComparison"]["withinCallerDeadline"]["status"], "target_not_met")
        pending = evaluate(self.plan, self.cohort(["returned", "pending"]), self.profile, self.raw)
        self.assertEqual(pending["targetComparison"]["timelyBoundResult"]["status"], "indeterminate")
        self.assertEqual(pending["sli"]["callerAccounting"]["callerLatencyMs"]["count"], 1)
        self.assertIn("unknown_caller_returns", pending["dataGaps"])

    def test_empty_or_legacy_cohort_cannot_pass_an_slo(self):
        for cohort in (self.cohort(count=0), self.cohort(["unnegotiated"]), self.cohort(["untracked"])):
            result = evaluate(self.plan, cohort, self.profile, self.raw)
            self.assertEqual(result["measurementStatus"], "insufficient_data")
            self.assertEqual(result["targetComparison"]["withinCallerDeadline"]["status"], "not_evaluable")

    def test_scope_population_and_ledger_omissions_are_rejected(self):
        for mutate in (lambda c: c["scope"].update(projectId="other"), lambda c: c["scope"].update(processVersion=True),
                       lambda c: c["scope"].update(status="completed"), lambda c: c["snapshot"].update(truncated=True),
                       lambda c: c["snapshot"].update(storedCohortComplete=1), lambda c: c.update(sloAccepted=True),
                       lambda c: c["instances"][0].update(createdAt=self.config["window"]["endAt"]),
                       lambda c: c["traces"].clear(), lambda c: c["counts"].update(instances=True),
                       lambda c: c["traces"][0]["decisionCallerAccounting"]["stages"].clear(),
                       lambda c: c["traces"][0].update(events=[]), lambda c: c.update(runIdsSha256="c"*64)):
            cohort = self.cohort(); mutate(cohort)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError): evaluate(self.plan, cohort, self.profile, self.raw)

    def test_prospective_plan_and_exact_raw_profile_pins_are_required(self):
        changed = copy.deepcopy(self.plan); changed["preparedAt"] = self.config["window"]["startAt"]
        changed = sealed({k:v for k,v in changed.items() if k != "sha256"})
        with self.assertRaises(ValueError): evaluate(changed, self.cohort(), self.profile, self.raw)
        with self.assertRaises(ValueError): evaluate(self.plan, self.cohort(), self.profile, self.raw+b"\n")
        cohort = self.cohort(); cohort["observedAt"] = "2026-09-29T23:59:59.999Z"
        with self.assertRaises(ValueError): evaluate(self.plan, cohort, self.profile, self.raw)

    def test_changed_assignment_profile_or_timeout_blocks_plan_comparison(self):
        for key, value, gap in (("profileSha256", "d"*64, "assignment_profile_differs_from_plan"),
                                ("callerTimeoutMs", 1000, "caller_timeout_differs_from_plan")):
            cohort = self.cohort(); cohort["traces"][0]["decisionAssignmentHistory"]["stages"][0]["assignments"][0][key] = value
            result = evaluate(self.plan, cohort, self.profile, self.raw)
            self.assertIn(gap, result["dataGaps"]); self.assertFalse(result["callerDenominatorBindingVerified"])
            self.assertEqual(result["targetComparison"]["withinCallerDeadline"]["status"], "not_evaluable")

    def test_complete_cohort_can_exceed_32_without_relaxing_default_trace_cli(self):
        cohort = self.cohort(count=40)
        result = evaluate(self.plan, cohort, self.profile, self.raw)
        self.assertEqual(result["sli"]["callerAccounting"]["counts"]["intents"], 40)
        self.assertEqual(result["sli"]["callerAccounting"]["callerLatencyMs"]["p95"], 400)
        with self.assertRaises(ValueError): analyze(cohort["traces"], self.profile, 1000)
        for bound in (True, 0, 1001):
            with self.assertRaises(ValueError): analyze(cohort["traces"], self.profile, 1000, trace_limit=bound)


class PilotEvaluationCliTest(unittest.TestCase):
    def setUp(self):
        self.fixture = PilotEvaluationTest(); self.fixture.setUp()
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name); self.private = self.root / "docs/private"; self.private.mkdir(parents=True)
        self.profile = self.private / "profile.json"; self.profile.write_bytes(self.fixture.raw)
        self.plan = self.private / "plan.json"; self.plan.write_text(json.dumps(self.fixture.plan))
        self.cohort = self.private / "cohort.json"; self.cohort.write_text(json.dumps(self.fixture.cohort()))
        self.output = self.private / "result.json"
        self.args = []
        for name, path in (("plan",self.plan),("cohort",self.cohort),("profile",self.profile)):
            self.args += ["--"+name,str(path),"--"+name+"-file-sha256",hashlib.sha256(path.read_bytes()).hexdigest()]
        self.args += ["--traffic-kind","diagnostic_fixture","--output",str(self.output)]
        self.identity = ("a"*40, {"fixture":"b"*64})

    def invoke(self):
        with patch.object(cli,"ROOT",self.root),patch.object(cli,"source_identity",return_value=self.identity): return cli.main(self.args)

    def test_private_immutable_receipt_and_independent_file_pins(self):
        self.assertEqual(self.invoke(),0)
        report = verify_seal(json.loads(self.output.read_text()),"agat.decision.shadow-pilot-evaluation.v1")
        self.assertEqual(report["status"],"diagnostic_only"); self.assertFalse(report["sloAccepted"])
        self.assertEqual(self.output.stat().st_mode & 0o777,0o600)
        before = self.output.read_bytes(); self.assertEqual(self.invoke(),1); self.assertEqual(before,self.output.read_bytes())
        self.output.unlink(); self.cohort.write_text(self.cohort.read_text()+" ")
        self.assertEqual(self.invoke(),1); self.assertEqual(json.loads(self.output.read_text())["status"],"failed")

    def test_source_drift_and_public_outputs_cannot_claim_measurement(self):
        with patch.object(cli,"ROOT",self.root),patch.object(cli,"source_identity",side_effect=[self.identity,("c"*40,{})]): self.assertEqual(cli.main(self.args),1)
        self.assertIn("sources changed",json.loads(self.output.read_text())["failure"]["message"])
        self.args[-1]=str(self.root/"public.json")
        with patch.object(cli,"ROOT",self.root),patch.object(cli,"source_identity") as identity:
            self.assertEqual(cli.main(self.args),1); identity.assert_not_called()
        self.assertFalse((self.root/"public.json").exists())


if __name__ == "__main__": unittest.main()
