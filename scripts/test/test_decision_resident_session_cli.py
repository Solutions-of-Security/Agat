import copy
import hashlib
import importlib.util
import json
import re
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from decision_runtime.artifacts import sealed, verify_seal
from decision_runtime.contracts import canonical_json

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("resident_session_probe", ROOT / "scripts/check-decision-resident-session.py")
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class ResidentSessionCliTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.private = self.root / "docs/private"
        self.private.mkdir(parents=True)
        self.baseline_path = self.private / "baseline.json"
        self.output = self.private / "verification.json"
        self.release = self.root / "release"
        self.raw_source = b"fixture committed source\n"
        self.sources = {"scripts/check-decision-resident-session.py": hashlib.sha256(self.raw_source).hexdigest()}
        self.bundle = {"sha256": "a" * 64, "profileSha256": "b" * 64, "installedDependencies": {"mlx": "0.32.2"}}
        self.manager = {"validate_bundle": Mock(return_value=(self.bundle, 8766, 9095)),
                        "prepare": {"normalized": lambda name: re.sub(r"[-_.]+", "-", name).lower()}}
        self.current = {"bundleRoot": str(self.release), "bundleSeal": "a" * 64, "profileSha256": "b" * 64,
                        "registrationFileSha256": "c" * 64, "registrationSeal": "d" * 64,
                        "installedPlists": [{"label": "org.agat.decision-shadow", "ownerUid": 501, "mode": "0600", "fileSha256": "e" * 64}],
                        "osSession": {"hostFingerprint": "f" * 64, "bootSessionUuid": "12345678-1234-1234-1234-123456789abc",
                                      "bootTimeEpoch": 1791320000, "guiSessionId": 100023, "ownerUid": 501, "capturedEpoch": 1791321000.25},
                        "processes": [{"role": role, "pid": 400 + i, "startedEpoch": 1791320010}
                                      for i, role in enumerate(("runtime", "prometheus", "inference", "resource_tracker"))],
                        "observation": {"targets": {"data": {"activeTargets": [{"lastScrape": datetime.fromtimestamp(1791321990, timezone.utc).isoformat()}]}}},
                        "serviceStates": [], "environment": {"pythonVersion": [3, 13, 12], "machine": "arm64", "installedDependencies": {"mlx": "0.32.2"}}}
        for item in (patch.object(probe, "ROOT", self.root), patch.object(probe.platform, "system", return_value="Darwin"),
                     patch.object(probe.runpy, "run_path", return_value=self.manager),
                     patch.object(probe, "source_identity", return_value=("1" * 40, self.sources)),
                     patch.object(probe.subprocess, "check_output", return_value=self.raw_source)):
            item.start(); self.addCleanup(item.stop)
        self.collect = patch.object(probe, "collect", side_effect=lambda *args: copy.deepcopy(self.current)).start()
        self.addCleanup(patch.stopall)

    def args(self, action, output=None):
        return [action, "--bundle", str(self.release), "--expected-seal", "a" * 64,
                "--output", str(output or self.output), "--wait-s", "0"]

    def capture(self):
        self.assertEqual(probe.main(self.args("capture", self.baseline_path)), 0)
        raw = self.baseline_path.read_bytes()
        self.assertEqual(self.baseline_path.stat().st_mode & 0o777, 0o600)
        self.current["osSession"]["capturedEpoch"] = 1791322000.25
        return hashlib.sha256(raw).hexdigest()

    def verify(self, checksum, event="boot"):
        return probe.main(self.args("verify") + ["--baseline", str(self.baseline_path), "--baseline-sha256", checksum, "--event", event])

    def test_same_session_is_pending_with_explicit_nonzero_exit(self):
        checksum = self.capture()
        self.assertEqual(self.verify(checksum), 2)
        report = verify_seal(json.loads(self.output.read_text()), probe.VERIFICATION_SCHEMA)
        self.assertEqual(report["status"], "awaiting_event")
        self.assertFalse(report["checks"]["actualEventObserved"])
        self.assertFalse(report["checks"]["newProcessStarts"])
        self.assertFalse(report["routingEnabled"])
        self.assertEqual(hashlib.sha256(self.baseline_path.read_bytes()).hexdigest(), checksum)

    def test_real_login_requires_new_gui_and_new_process_births(self):
        checksum = self.capture()
        self.current["osSession"]["guiSessionId"] += 1
        for process in self.current["processes"]: process["startedEpoch"] = 1791321500
        self.assertEqual(self.verify(checksum, "login"), 0)
        report = json.loads(self.output.read_text())
        self.assertEqual(report["status"], "verified")
        self.assertEqual(report["event"]["event"], "login")
        self.assertTrue(all(report["checks"].values()))

    def test_reboot_can_reuse_pid_and_session_numbers(self):
        checksum = self.capture()
        self.current["osSession"].update(bootSessionUuid="22345678-1234-1234-1234-123456789abc", bootTimeEpoch=1791321490)
        for process in self.current["processes"]: process["startedEpoch"] = 1791321500
        self.assertEqual(self.verify(checksum), 0)
        self.assertEqual(json.loads(self.output.read_text())["event"]["event"], "boot_and_login")

    def test_new_session_with_old_process_births_keeps_failed_observation(self):
        checksum = self.capture(); self.current["osSession"]["guiSessionId"] += 1
        self.assertEqual(self.verify(checksum, "login"), 1)
        report = json.loads(self.output.read_text())
        self.assertEqual(report["status"], "failed")
        self.assertIn("predates", report["failure"]["message"])
        self.assertEqual(report["current"]["processes"], self.current["processes"])

    def test_changed_registration_profile_plist_or_environment_rejects(self):
        checksum = self.capture()
        original = copy.deepcopy(self.current)
        changes = {"registrationFileSha256": "9" * 64, "profileSha256": "9" * 64,
                   "installedPlists": [], "environment": {"pythonVersion": [3, 13, 13]}}
        for index, (field, value) in enumerate(changes.items()):
            with self.subTest(field=field):
                self.output = self.private / f"changed-{index}.json"
                self.current = {**copy.deepcopy(original), field: value}
                self.assertEqual(self.verify(checksum), 1)
                self.assertEqual(json.loads(self.output.read_text())["status"], "failed")

    def test_rehashed_baseline_cannot_bypass_pinned_file_sha(self):
        checksum = self.capture()
        baseline = json.loads(self.baseline_path.read_text()); baseline.pop("sha256")
        baseline["osSession"]["guiSessionId"] -= 1
        self.baseline_path.write_text(canonical_json(sealed(baseline)) + "\n")
        self.collect.reset_mock()
        self.assertEqual(self.verify(checksum), 1)
        self.collect.assert_not_called()
        self.assertIn("pinned SHA", json.loads(self.output.read_text())["failure"]["message"])

    def test_rehashed_boolean_alias_is_rejected_even_with_new_file_pin(self):
        self.capture()
        baseline = json.loads(self.baseline_path.read_text()); baseline.pop("sha256"); baseline["routingEnabled"] = 0
        self.baseline_path.write_text(canonical_json(sealed(baseline)) + "\n")
        checksum = hashlib.sha256(self.baseline_path.read_bytes()).hexdigest()
        self.collect.reset_mock()
        self.assertEqual(self.verify(checksum), 1)
        self.collect.assert_not_called()

    def test_existing_and_outside_private_outputs_reject_before_native_collection(self):
        self.output.write_text("retained original\n")
        self.assertEqual(probe.main(self.args("capture")), 1)
        self.assertEqual(self.output.read_text(), "retained original\n")
        self.assertEqual(probe.main(self.args("capture", self.root / "outside.json")), 1)
        self.collect.assert_not_called()

    def test_readiness_error_retains_a_sealed_failed_receipt(self):
        self.collect.side_effect = OSError("fixture: runtime is unavailable")
        self.assertEqual(probe.main(self.args("capture")), 1)
        report = verify_seal(json.loads(self.output.read_text()), probe.BASELINE_SCHEMA)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["failure"]["type"], "OSError")

    def test_verify_requires_the_independently_pinned_sha_and_explicit_event(self):
        with self.assertRaises(SystemExit) as raised:
            probe.main(self.args("verify") + ["--baseline", str(self.baseline_path)])
        self.assertEqual(raised.exception.code, 2)
        self.collect.assert_not_called()


class ResidentEnvironmentTest(unittest.TestCase):
    def setUp(self):
        self.manager = {"prepare": {"normalized": lambda name: re.sub(r"[-_.]+", "-", name).lower()}}
        self.root = Path("/fixture/release")
        self.bundle = {"installedDependencies": {"mlx": "0.32.2", "mlx-lm": "0.31.3"}}
        self.data = {"version": [3, 13, 12], "machine": "arm64", "packages": [["MLX", "0.32.2"], ["mlx_lm", "0.31.3"], ["pip", "26.3"]]}

    def check(self, data, code=0):
        with patch.object(probe.subprocess, "check_output", return_value=json.dumps(data)) as command, \
             patch.object(probe.subprocess, "run", return_value=SimpleNamespace(returncode=code, stdout="No broken requirements found.\n", stderr="")):
            result = probe.resident_environment(self.manager, self.root, self.bundle)
            self.assertIn("-B", command.call_args.args[0])
            return result

    def test_exact_pins_normalize_names_and_ignore_pip(self):
        self.assertEqual(self.check(self.data)["installedDependencies"], self.bundle["installedDependencies"])

    def test_changed_python_platform_version_and_extra_package_reject(self):
        for field, value in (("version", [3, 13, 13]), ("version", [3, 13, 12.0]), ("machine", "x86_64"),
                             ("packages", self.data["packages"] + [["unlocked", "1.0"]])):
            with self.subTest(field=field):
                with self.assertRaises(ValueError): self.check({**self.data, field: value})

    def test_duplicate_normalized_name_and_dependency_conflict_reject(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.check({**self.data, "packages": self.data["packages"] + [["mlx-lm", "0.31.3"]]})
        with self.assertRaisesRegex(ValueError, "compatibility"):
            self.check(self.data, code=1)


if __name__ == "__main__":
    unittest.main()
