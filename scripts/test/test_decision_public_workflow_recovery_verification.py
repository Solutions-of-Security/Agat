import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from scripts.lib import decision_public_workflow_recovery as recovery
from scripts.lib import decision_public_workflow_recovery_verification as specialized
from scripts.lib import decision_public_workflow_verification as verification
from scripts.test import test_decision_public_workflow_recovery as fixtures
from scripts.test import test_decision_public_workflow_verification as original


class RecoveryVerificationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.RecoveryTest.setUpClass.__func__(cls)
        paths = ["scripts/verify-public-support-workflow.py",
                 "scripts/test/test_decision_public_workflow_verification.py",
                 "scripts/test/test_decision_public_workflow_loss_verification.py",
                 "scripts/test/test_decision_public_workflow_recovery_verification.py"]
        for name in paths:
            target = cls.root/name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((original.ROOT/name).read_bytes())
        subprocess.run(["git", "add", "."], cwd=cls.root, check=True)
        subprocess.run(["git", "-c", "user.name=Synthetic fixture", "-c", "user.email=fixture@example.invalid",
                        "commit", "--quiet", "-m", "Synthetic offline verifier sources"], cwd=cls.root, check=True)
        cls.commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=cls.root, text=True).strip()

    @classmethod
    def tearDownClass(cls):
        fixtures.RecoveryTest.tearDownClass.__func__(cls)

    pins = fixtures.RecoveryTest.pins
    setUp = fixtures.RecoveryTest.setUp
    write = fixtures.RecoveryTest.write
    at = staticmethod(fixtures.RecoveryTest.at)

    def replay(self, **pins):
        return verification.verify(self.root, self.directory, self.directory/"context.json", **pins)

    def arguments(self, output, pins):
        return ["--evidence-dir", str(self.directory), "--context-profile", str(self.directory/"context.json"),
                "--context-profile-file-sha256", pins["context_sha"], "--plan-file-sha256", pins["plan_sha"],
                "--result-file-sha256", pins["result_sha"], "--output-dir", str(output)]

    def command(self, args):
        return subprocess.run([sys.executable, "-B", str(self.root/"scripts/verify-public-support-workflow.py"), *args],
                              capture_output=True, text=True, timeout=30)

    def test_dispatch_replays_actual_git_v3_offline_with_identical_accounting(self):
        pins = self.write()
        actual = subprocess.Popen
        def git_only(args, *a, **kw):
            if args[0] != "git" or args[1] not in {"ls-tree", "archive"}:
                raise AssertionError("Offline receipt replay started a live process")
            return actual(args, *a, **kw)
        with patch("http.client.HTTPConnection") as http, patch.object(recovery, "crash_owned") as crash, \
                patch("os.killpg") as signal, patch.object(subprocess, "Popen", side_effect=git_only):
            report = self.replay(**pins)
            expected = specialized.verify(self.root, self.directory, self.directory/"context.json", **pins)
            http.assert_not_called(); crash.assert_not_called(); signal.assert_not_called()
        self.assertEqual(report, expected)
        self.assertEqual(report["schemaVersion"], specialized.SCHEMA)
        self.assertEqual(report["physicalScheduledCompletedHandlers"], 5)
        self.assertEqual(report["physicalCompletedHttpHandlers"], 9)
        self.assertEqual(report["interruptedTerminalOutcome"], "unknown_after_crash")
        self.assertEqual(report["verificationModelCalls"], 0)
        self.assertFalse(report["liveCleanupVerified"])

    def test_actual_cli_freezes_its_sources_and_publishes_exclusive_private_report(self):
        pins = self.write(); output = self.root/"docs/private/replayed"
        args = self.arguments(output, pins); completed = self.command(args)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads((output/"verification.json").read_bytes())
        self.assertEqual(report["verifierCommit"], self.commit)
        self.assertEqual(report["inventory"]["scheduled"], 6)
        self.assertTrue({"scripts/verify-public-support-workflow.py",
                         "scripts/lib/decision_public_workflow_recovery_verification.py",
                         "scripts/test/test_decision_public_workflow_recovery_verification.py"} <= set(report["verifierSources"]))
        self.assertEqual(output.stat().st_mode & 0o777, 0o700)
        self.assertEqual((output/"verification.json").stat().st_mode & 0o777, 0o600)
        before = (output/"verification.json").read_bytes()
        self.assertEqual(self.command(args).returncode, 1)
        self.assertEqual((output/"verification.json").read_bytes(), before)

    def test_independent_raw_plan_pin_is_checked_before_v3_dispatch(self):
        pins = self.write()
        (self.directory/"plan.json").write_bytes(original.encoded(original.reseal(self.plan)))
        # Change raw bytes without changing the sealed JSON semantics.
        with (self.directory/"plan.json").open("ab") as stream: stream.write(b" ")
        with patch.object(specialized, "verify") as replay, self.assertRaises(ValueError):
            self.replay(**pins)
        replay.assert_not_called()

    def test_resealed_unknown_or_mixed_protocol_versions_are_rejected(self):
        for version in (verification.PLAN_SCHEMA, "agat.decision.public-workflow-launch-plan.v2",
                        "agat.decision.public-workflow-launch-plan.v4", False):
            with self.subTest(version=version):
                self.setUp(); self.plan["schemaVersion"] = version
                with self.assertRaises(ValueError): self.replay(**self.write())
        self.setUp(); self.result["schemaVersion"] = verification.RESULT_SCHEMA
        with self.assertRaises(ValueError): self.replay(**self.write())

    def test_cli_rejects_source_and_artifact_drift_without_creating_report(self):
        pins = self.write(); source = self.root/"scripts/lib/decision_public_workflow_recovery_verification.py"
        before = source.read_bytes()
        try:
            source.write_bytes(before+b"\n# Uncommitted verifier change\n")
            output = self.root/"docs/private/source-drift"
            self.assertEqual(self.command(self.arguments(output, pins)).returncode, 1)
            self.assertFalse(output.exists())
        finally:
            source.write_bytes(before)
        (self.directory/"runtime-crash-inflight.json").write_bytes(b"changed receipt")
        output = self.root/"docs/private/artifact-drift"
        self.assertEqual(self.command(self.arguments(output, pins)).returncode, 1)
        self.assertFalse(output.exists())

    def test_cli_rejects_nonprivate_and_symlink_output_before_freezing_or_replay(self):
        pins = self.write()
        targets = [self.root/"docs/public-report", self.root/"docs/private"]
        link = self.root/"docs/private/report-link"; link.symlink_to(self.directory, target_is_directory=True)
        targets.append(link)
        cli = original.cli
        for output in targets:
            with self.subTest(output=output), patch.object(cli, "ROOT", self.root), \
                    patch.object(cli.launcher, "frozen_sources") as freeze, patch.object(cli, "verify") as replay, \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(self.arguments(output, pins)), 1)
                freeze.assert_not_called(); replay.assert_not_called()


if __name__ == "__main__": unittest.main()
