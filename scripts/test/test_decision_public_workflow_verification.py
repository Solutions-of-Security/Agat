import copy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import importlib.util

from decision_runtime.artifacts import sealed
from scripts.lib import decision_public_workflow as workflow
from scripts.lib import decision_public_workflow_verification as verification
from scripts.lib.decision_public_load_verification import CONTEXT_PATHS, counters
from scripts.test import test_decision_public_workflow as fixtures
from scripts.test import test_decision_public_load_verification as load_fixtures

ROOT = Path(__file__).resolve().parents[2]
MEASURED = "23249ec85bde5af7a3ec53f9e6496583f94a02ce"
SPEC = importlib.util.spec_from_file_location("public_workflow_verification_cli", ROOT/"scripts/verify-public-support-workflow.py")
cli = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(cli)
def encoded(value): return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+"\n").encode()
def sha(raw): return hashlib.sha256(raw).hexdigest()
def reseal(value): return sealed({key: value for key, value in value.items() if key != "sha256"})


class WorkflowVerificationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(); cls.root = Path(cls.temporary.name)
        raw = subprocess.check_output(["git", "archive", MEASURED, "--", *workflow.SOURCE_PATHS, *CONTEXT_PATHS], cwd=ROOT)
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            for item in archive:
                if item.isfile():
                    target = cls.root/item.name; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(archive.extractfile(item).read())
        for args in (["init", "--quiet"], ["add", "."], ["-c", "user.name=Synthetic fixture", "-c", "user.email=fixture@example.invalid", "commit", "--quiet", "-m", "Synthetic workflow receipts"]):
            subprocess.run(["git", *args], cwd=cls.root, check=True)
        cls.commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=cls.root, text=True).strip()
    @classmethod
    def tearDownClass(cls): cls.temporary.cleanup()
    def pins(self, paths):
        names = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", self.commit, "--", *paths], cwd=self.root, text=True).splitlines()
        return {name: sha((self.root/name).read_bytes()) for name in names}
    def setUp(self):
        self.context, self.recipe, self.cohort, self.routes = fixtures.fixture()
        self.context.update(sourceCommit=self.commit, sourceFiles=self.pins(CONTEXT_PATHS)); self.context = reseal(self.context)
        self.recipe.update(scopeEndRule="after_full_input_inventory_and_worker_drain", graphSha256="1"*64)
        self.cohort.update(snapshotId="fixture-snapshot", limits={"instances": 1000, "stages": 10000, "activityBytes": 16777216, "exportBytes": 16777216},
                           populationCoverageVerified=False, eligibleWorkloadVerified=False, httpAttemptInventoryVerified=False,
                           runIdsSha256=sha(json.dumps([r["runId"] for r in self.cohort["instances"]], separators=(",", ":")).encode()))
        self.cohort["counts"]["storedStages"] = len(self.routes)*2
        self.cohort["snapshot"]["dialect"] = "sqlite"
        _, _, old = load_fixtures.fixture()
        samples = copy.deepcopy(old["samples"])
        for sample in samples:
            values, start = counters(sample); sample.update(counters=values, serverStart=start)
        self.plan = {"schemaVersion": verification.PLAN_SCHEMA, "createdAt": "2026-10-08T01:00:30.000Z", "sourceCommit": self.commit,
            "sourceFiles": self.pins(workflow.SOURCE_PATHS), "contextProfileFileSha256": sha(encoded(self.context)), "context": self.context,
            "config": workflow.shared_config(self.context), "runtime": self.context["tokenizerEnvironment"], "manifestFileSha256": self.context["manifestFileSha256"],
            "mode": "serial_closed_model_integration", "primary": "fixture_chat_completions", "warmupCount": 2, "ownersAppointed": False,
            "referenceLabels": 0, "sloAccepted": False, "routingEnabled": False, "qualification": "not_assessed"}
        self.driver = {"status": "observed", "primary": "fixture_chat_completions", "primaryCalls": len(self.routes), "routes": self.routes,
            "nodeVersion": "v22.14.0", "ownedPids": [42, 43], "workerExitCode": 0, "actualWindow": {k: self.cohort["scope"][k] for k in ("startAt", "endAt")},
            "unauthenticatedStatus": 401, "authenticatedStatus": 200, "ownersAppointed": False, "routingEnabled": False, "qualification": "not_assessed"}
        self.result = {"schemaVersion": verification.RESULT_SCHEMA, "status": "observed", "planSha256": "pending",
            "evidence": workflow.verify_inventory(self.context, self.recipe, self.cohort, self.routes),
            "warmup": [{k: value for k, value in row.items() if k not in {"inputSha256", "inputTokens"}} for row in old["warmup"]],
            "samples": samples, "failure": None, "ownedPids": [42, 43], "remainingOwnedPids": [], "cleanupErrors": [], "runtimeExitCode": 0,
            "driverExitCode": 0, "artifactSha256": {}, "elapsedMs": old["elapsedMs"], "referenceLabels": 0, "classificationAccuracyMeasured": False,
            "ownersAppointed": False, "sloAccepted": False, "routingEnabled": False, "qualification": "not_assessed"}
        self.directory = self.root/"docs/private/fixture"; self.directory.mkdir(parents=True, exist_ok=True)
    def write(self):
        artifacts = {"workflow-plan.json": encoded(self.recipe), "workflow-driver.json": encoded(self.driver), "cohort.http.json": encoded(self.cohort),
                     "workflow-routes.jsonl": b"".join(json.dumps(row).encode()+b"\n" for row in self.routes),
                     **{name: b"Synthetic fixture; no model inference.\n" for name in ("runtime.log", "worker.log", "driver.log")}}
        self.plan["context"] = self.context; self.plan["contextProfileFileSha256"] = sha(encoded(self.context))
        plan = reseal(self.plan); self.result.update(planSha256=plan["sha256"], artifactSha256={name: sha(raw) for name, raw in artifacts.items()})
        for name, raw in artifacts.items(): (self.directory/name).write_bytes(raw)
        raw = {"context": encoded(self.context), "plan": encoded(plan), "result": encoded(reseal(self.result))}
        for name, value in raw.items(): (self.directory/(name+".json")).write_bytes(value)
        return {"context_sha": sha(raw["context"]), "plan_sha": sha(raw["plan"]), "result_sha": sha(raw["result"])}
    def verify(self):
        pins = self.write(); return verification.verify(self.root, self.directory, self.directory/"context.json", **pins)
    def test_full_actual_git_replay_is_offline_and_never_claims_live_cleanup(self):
        with patch("http.client.HTTPConnection") as http:
            result = self.verify(); http.assert_not_called()
        self.assertEqual(result["status"], "pass"); self.assertEqual(result["inventory"]["boundCallerReturns"], 6)
        self.assertEqual(result["physicalScheduledHttpHandlers"], 6); self.assertFalse(result["liveCleanupVerified"])
        self.assertFalse(result["ownersAppointed"]); self.assertFalse(result["classificationAccuracyMeasured"])
    def test_rehashed_partial_journal_missing_intent_bad_physical_counts_and_authority_fail(self):
        mutations = (lambda: self.routes.pop(), lambda: self.cohort["traces"][0]["decisionCallerAccounting"]["stages"][0]["assignments"][0].update(intent=False),
                     lambda: self.result["samples"][-1]["counters"].update(ok=0), lambda: self.result.update(referenceLabels=False),
                     lambda: self.recipe.update(ownersAppointed=True), lambda: self.result.update(ownedPids=[True]),
                     lambda: self.plan["sourceFiles"].update({next(iter(self.plan["sourceFiles"])): "0"*64}),
                     lambda: self.cohort.update(runIdsSha256="0"*64), lambda: self.result["evidence"].update(computed=4),
                     lambda: self.driver.update(ownedPids=[]), lambda: self.cohort["limits"].update(exportBytes=0))
        for mutate in mutations:
            self.setUp(); mutate()
            with self.assertRaises(ValueError): self.verify()
    def test_changed_bytes_or_mid_replay_mutation_cannot_publish_valid_report(self):
        pins = self.write(); (self.directory/"worker.log").write_bytes(b"changed")
        with self.assertRaises(ValueError): verification.verify(self.root, self.directory, self.directory/"context.json", **pins)
        original = verification.verify_inventory
        def change(*args):
            result = original(*args); (self.directory/"worker.log").write_bytes(b"changed after consumption"); return result
        pins = self.write()
        with patch.object(verification, "verify_inventory", side_effect=change), self.assertRaises(ValueError):
            verification.verify(self.root, self.directory, self.directory/"context.json", **pins)
    def test_cli_exclusive_private_output_and_rejection_before_replay(self):
        pins = self.write(); report = verification.verify(self.root, self.directory, self.directory/"context.json", **pins)
        output = self.root/"docs/private/verified"
        args = ["--evidence-dir", str(self.directory), "--context-profile", str(self.directory/"context.json"), "--context-profile-file-sha256", pins["context_sha"],
                "--plan-file-sha256", pins["plan_sha"], "--result-file-sha256", pins["result_sha"], "--output-dir", str(output)]
        with patch.object(cli, "ROOT", self.root), patch.object(cli.launcher, "frozen_sources", return_value=(self.commit, {})), patch.object(cli, "verify", return_value=report) as replay:
            self.assertEqual(cli.main(args), 0); self.assertEqual(output.stat().st_mode&0o777, 0o700)
            self.assertEqual((output/"verification.json").stat().st_mode&0o777, 0o600)
            self.assertEqual(cli.main(args), 1); self.assertEqual(replay.call_count, 1)


if __name__ == "__main__": unittest.main()
