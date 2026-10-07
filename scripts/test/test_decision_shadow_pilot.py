import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from decision_runtime.artifacts import sealed
from decision_runtime.contracts import fingerprint
from scripts.lib.decision_shadow_pilot import prepare, validate_config, verify_plan

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("pilot_cli", ROOT / "scripts/prepare-decision-shadow-pilot.py")
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)


class PilotTest(unittest.TestCase):
    def setUp(self):
        self.blank = json.loads((ROOT / "docs/qualification/local-decisions/shadow/observability/pilot-config.blank.json").read_text())
        self.config = {**copy.deepcopy(self.blank), "pilotId": "fixture-pilot", "projectId": "fixture-project",
            "processId": "fixture-process", "processVersionId": "fixture-version", "scenarioDescription": "Synthetic test only",
            "dataUseReference": "Synthetic test fixture", "owners": {"runtime": "fixture-runtime", "business": "fixture-business"},
            "window": {"startAt": "2026-10-09T00:00:00.000Z", "endAt": "2026-10-16T00:00:00.000Z"}}
        self.profile = {"schemaVersion": "agat.decision.v1", "runtimeVersion": "fixture",
                        "model": {"repository": "fixture"}, "policy": {"id": "fixture"}, "calibration": {"temperature": 1.0}}
        self.raw = json.dumps(self.profile).encode()
        self.pin = hashlib.sha256(self.raw).hexdigest()
        self.kwargs = {"prepared_at": "2026-10-08T00:00:00.000Z", "source_commit": "a" * 40, "source_files": {"fixture": "b" * 64}}

    def plan(self, config=None, **kwargs):
        return sealed(prepare(config or self.config, self.profile, self.raw, self.pin, "coordinator_json_bytes",
                              **{**self.kwargs, **kwargs}))

    def test_complete_plan_binds_terms_and_raw_profile_without_claiming_agreement(self):
        plan = verify_plan(self.plan())
        self.assertEqual(plan["status"], "ready_for_review")
        self.assertEqual(plan["profile"]["sha256"], self.pin)
        self.assertNotEqual(self.pin, fingerprint(self.profile))
        self.assertEqual(plan["configSha256"], fingerprint(self.config))
        for name in ("agreementVerified", "sloAccepted", "populationCoverageVerified", "routingEnabled"):
            self.assertIs(plan[name], False)

    def test_blank_template_preserves_missing_owners_scope_and_dates(self):
        plan = verify_plan(self.plan(self.blank))
        self.assertEqual(plan["status"], "draft_incomplete")
        self.assertEqual(len(plan["missingFields"]), 10)
        self.assertIn("owners.business", plan["missingFields"])

    def test_future_window_is_half_open_canonical_and_bounded_to_seven_days(self):
        for start, end in (("2026-10-09T00:00:00Z", self.config["window"]["endAt"]),
                           ("2026-10-09T03:00:00.000+03:00", self.config["window"]["endAt"]),
                           ("2026-02-30T00:00:00.000Z", self.config["window"]["endAt"]),
                           ("2026-10-09T00:00:00.000Z", "2026-10-09T00:00:00.000Z"),
                           ("2026-10-09T00:00:00.000Z", "2026-10-16T00:00:00.001Z")):
            config = copy.deepcopy(self.config); config["window"] = {"startAt": start, "endAt": end}
            with self.subTest(start=start, end=end), self.assertRaises(ValueError): self.plan(config)
        with self.assertRaisesRegex(ValueError, "before.*starts"):
            self.plan(prepared_at=self.config["window"]["startAt"])

    def test_targets_reject_boolean_aliases_nonfinite_and_larger_than_timeout(self):
        for key, value in (("boundResultRatio", True), ("timelyBoundResultRatio", float("nan")),
                           ("boundResultRatio", 0), ("latencyThresholdMs", 10001), ("callerTimeoutMs", 10001),
                           ("callerTimeoutMs", False), ("latencyThresholdMs", 5000.0)):
            config = copy.deepcopy(self.config); config["targets"][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError): validate_config(config)

    def test_rehashed_artifact_cannot_hide_missing_scope_or_grant_acceptance(self):
        for mutate in (lambda p: p.update(sloAccepted=True), lambda p: p.update(agreementVerified=True),
                       lambda p: p["config"].update(trafficKind="diagnostic_fixture"),
                       lambda p: p["config"].update(exclusions=["failed"]),
                       lambda p: p.update(status="ready_for_review", missingFields=[]),
                       lambda p: p["profile"].update(sha256=fingerprint(self.profile)),
                       lambda p: p.update(preparedAt="not-a-timestamp")):
            plan = self.plan(self.blank); mutate(plan)
            plan = sealed({k: v for k, v in plan.items() if k != "sha256"})
            with self.assertRaises(ValueError): verify_plan(plan)

    def test_changed_bytes_need_new_pin_and_changed_semantics_cannot_share_bytes(self):
        with self.assertRaisesRegex(ValueError, "pin differs"):
            prepare(self.config, self.profile, self.raw + b"\n", self.pin, "coordinator_json_bytes", **self.kwargs)
        changed = copy.deepcopy(self.profile); changed["calibration"]["temperature"] = True
        with self.assertRaisesRegex(ValueError, "bytes differ"):
            prepare(self.config, changed, self.raw, self.pin, "coordinator_json_bytes", **self.kwargs)


class PilotCliTest(unittest.TestCase):
    setUp = PilotTest.setUp
    def invoke(self, config=None):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); private = root / "docs/private"; private.mkdir(parents=True)
            config_file = private / "config.json"; config_file.write_text(json.dumps(config or self.blank))
            profile_file = private / "profile.json"; profile_file.write_bytes(self.raw)
            output = private / "plan.json"
            args = ["--config", str(config_file), "--config-sha256", hashlib.sha256(config_file.read_bytes()).hexdigest(),
                    "--profile", str(profile_file), "--profile-file-sha256", self.pin, "--output", str(output)]
            with patch.object(cli, "ROOT", root), patch.object(cli, "source_identity", return_value=(self.kwargs["source_commit"], self.kwargs["source_files"])), \
                    patch.object(cli, "utc_now", return_value=self.kwargs["prepared_at"]):
                code = cli.main(args)
                report = verify_plan(json.loads(output.read_text()))
                self.assertEqual(output.stat().st_mode & 0o777, 0o600)
                self.assertEqual(cli.main(args), 1)
                output.unlink(); args[args.index("--config-sha256") + 1] = "c" * 64
                self.assertEqual(cli.main(args), 1); self.assertFalse(output.exists())
                args[-1] = str(root / "public.json")
                self.assertEqual(cli.main(args), 1); self.assertFalse((root / "public.json").exists())
            return code, report

    def test_actual_cli_draft_and_complete_exit_codes_preserve_private_immutable_output(self):
        self.assertEqual(self.invoke()[0], 2)
        self.assertEqual(self.invoke(self.config)[0], 0)

    def test_source_drift_is_rejected_by_real_git_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in cli.SOURCES:
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_bytes(b"fixture")
            with patch.object(cli, "ROOT", root), patch.object(cli.subprocess, "check_output", side_effect=["a" * 40, b"changed"]), \
                    self.assertRaisesRegex(ValueError, "Commit pilot sources"):
                cli.source_identity()


if __name__ == "__main__": unittest.main()
