import json
import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from decision_runtime.contracts import fingerprint
from decision_runtime.tests.test_decisions import request

module = runpy.run_path(str(Path(__file__).resolve().parents[1] / "check-decision-retirement.py"))
verify = module["verify_event"]


class RetirementProbeTest(unittest.TestCase):
    def setUp(self):
        self.profile = {"runtimeVersion": "synthetic-test-version"}
        self.event = {"schemaVersion": "agat.decision.retirement.v1", "eventName": "decision.backend_retired",
                      "runtimeVersion": "synthetic-test-version", "profileSha256": fingerprint(self.profile),
                      "exitCode": 75, "childPid": 123, "childExitCode": -9, "reason": "backend_unavailable"}

    def check(self, event):
        return verify(json.dumps(event).encode(), self.profile, 123, "backend_unavailable", -9)

    def test_accepts_exact_owned_child_and_profile(self):
        self.assertEqual(self.check(self.event), self.event)

    def test_rejects_missing_extra_cross_profile_and_cross_process_data(self):
        for key, value in (("request", "private"), ("profileSha256", "0" * 64), ("childPid", 124),
                           ("childPid", True), ("childExitCode", None), ("childExitCode", 0),
                           ("childExitCode", True), ("exitCode", 130), ("reason", "inference_timeout"),
                           ("schemaVersion", "unknown"), ("eventName", "unknown")):
            with self.subTest(key=key, value=value), self.assertRaises(RuntimeError):
                self.check({**self.event, key: value})
        for key in self.event:
            with self.subTest(missing=key), self.assertRaises(RuntimeError):
                self.check({name: value for name, value in self.event.items() if name != key})

    def test_rejects_unbounded_duplicate_or_non_object_records(self):
        raw = json.dumps(self.event).encode()
        for value in (raw + b"\n" + raw, b" " * 513, b"[]", b"null"):
            with self.subTest(raw=value), self.assertRaises((RuntimeError, ValueError)):
                verify(value, self.profile, 123, "backend_unavailable")

    def test_probe_rejects_public_output_before_model_or_file_access(self):
        with patch.object(sys, "argv", ["probe", "--python", "missing", "--manifest", "missing",
                                       "--policy", "missing", "--request", "missing", "--output", "/tmp/public.json"]), \
             patch.object(module["recovery"], "OwnedRuntime") as runtime, \
             self.assertRaisesRegex(RuntimeError, "ignored docs/private"):
            module["main"]()
        runtime.assert_not_called()

    def test_failed_probe_preserves_local_stderr_and_failure_record(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "docs/private/probe.json"
            input_path = root / "request.json"
            input_path.write_text(json.dumps(request()))
            def fail(_args, _request, directory, _owned):
                (directory / "failure.stderr").write_text("synthetic diagnostic")
                raise RuntimeError("controlled failure")
            with patch.dict(module["main"].__globals__, {"ROOT": root, "source_hashes": lambda: {}, "run_scenarios": fail}), \
                 patch.object(module["subprocess"], "check_output", return_value="a" * 40), \
                 patch.object(sys, "argv", ["probe", "--python", "missing", "--manifest", "missing",
                                            "--policy", "missing", "--request", str(input_path), "--output", str(output)]), \
                 self.assertRaisesRegex(RuntimeError, "controlled failure"):
                module["main"]()
            result = json.loads(output.read_text())
            self.assertEqual((result["status"], result["failureType"]), ("fail", "RuntimeError"))
            self.assertEqual((root / "docs/private/probe.runtime-logs/failure.stderr").read_text(), "synthetic diagnostic")


if __name__ == "__main__": unittest.main()
