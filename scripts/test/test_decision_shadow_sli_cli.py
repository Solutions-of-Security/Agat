import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from decision_runtime.artifacts import read_json, verify_seal
from decision_runtime.contracts import fingerprint

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("shadow_sli_cli", ROOT / "scripts/summarize-decision-shadow-sli.py")
cli = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(cli)


class CallerSliCliTest(unittest.TestCase):
    def setUp(self):
        from scripts.test.test_decision_shadow_sli import CallerSliTest
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name); self.private = self.root / "docs/private"; self.private.mkdir(parents=True)
        fixture = CallerSliTest(); fixture.setUp(); self.profile = fixture.profile
        self.trace = self.private / "trace.json"; self.trace.write_text(json.dumps(fixture.trace([fixture.row()])))
        self.profile_file = self.private / "profile.json"; self.profile_file.write_text(json.dumps(self.profile))
        self.output = self.private / "result.json"
        self.base = ["--trace", str(self.trace), "--trace-sha256", self.sha(self.trace), "--profile", str(self.profile_file),
                     "--profile-file-sha256", self.sha(self.profile_file), "--profile-sha256", fingerprint(self.profile),
                     "--latency-threshold-ms", "1000", "--traffic-kind", "diagnostic_fixture", "--output", str(self.output)]
        self.identity = ("a"*40, {"fixture": "b"*64})

    def sha(self, path): return hashlib.sha256(path.read_bytes()).hexdigest()

    def invoke(self, args=None):
        with patch.object(cli, "ROOT", self.root), patch.object(cli, "source_identity", return_value=self.identity):
            return cli.main(args or self.base)

    def test_diagnostic_receipt_is_sealed_private_and_never_an_accepted_slo(self):
        self.assertEqual(self.invoke(), 0)
        report = verify_seal(read_json(self.output), "agat.decision.shadow-caller-sli.v1")
        self.assertEqual(report["status"], "diagnostic_only"); self.assertFalse(report["sloAccepted"])
        self.assertEqual(report["profileIdentity"], "runtime_fingerprint")
        self.assertEqual(report["profileFingerprintSha256"], fingerprint(self.profile))
        self.assertFalse(report["routingEnabled"]); self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.invoke(), 1)

    def test_independent_trace_and_profile_pins_detect_rehashed_changes(self):
        self.trace.write_text(self.trace.read_text()+" ")
        self.assertEqual(self.invoke(), 1); self.assertEqual(read_json(self.output)["status"], "failed")
        self.output.unlink(); self.base[self.base.index("--trace-sha256")+1] = self.sha(self.trace)
        self.profile_file.write_text(self.profile_file.read_text()+" ")
        self.assertEqual(self.invoke(), 1); self.assertIn("Profile file pin", read_json(self.output)["failure"]["message"])

    def test_public_output_is_rejected_before_source_inspection(self):
        args = self.base.copy(); args[-1] = str(self.root / "public.json")
        with patch.object(cli, "ROOT", self.root), patch.object(cli, "source_identity") as identity:
            self.assertEqual(cli.main(args), 1); identity.assert_not_called()
        self.assertFalse((self.root / "public.json").exists())

    def test_source_drift_during_measurement_saves_failed_receipt(self):
        with patch.object(cli, "ROOT", self.root), patch.object(cli, "source_identity", side_effect=[self.identity, ("c"*40, {})]):
            self.assertEqual(cli.main(self.base), 1)
        self.assertIn("sources changed", read_json(self.output)["failure"]["message"])

    def test_no_traffic_returns_insufficient_data_even_for_a_diagnostic(self):
        trace = read_json(self.trace); trace["decisionObservations"] = []; trace["decisionStageInventory"]["stages"] = []
        self.trace.write_text(json.dumps(trace))
        self.base[self.base.index("--trace-sha256")+1] = self.sha(self.trace)
        self.assertEqual(self.invoke(), 2); self.assertIsNone(read_json(self.output)["boundResultRatio"])

    def test_source_identity_requires_committed_transport_and_validator_bytes(self):
        raw = b"committed source fixture"
        for name in cli.SOURCES:
            (self.root / name).parent.mkdir(parents=True, exist_ok=True); (self.root / name).write_bytes(raw)
        with patch.object(cli, "ROOT", self.root), patch.object(cli.subprocess, "check_output", side_effect=["a"*40, *([raw]*len(cli.SOURCES))]):
            self.assertEqual(len(cli.source_identity()[1]), len(cli.SOURCES))
        with patch.object(cli, "ROOT", self.root), patch.object(cli.subprocess, "check_output", side_effect=["a"*40, b"changed"]), self.assertRaises(ValueError):
            cli.source_identity()

    def test_input_growth_and_symlink_are_rejected_before_analysis(self):
        with self.assertRaisesRegex(ValueError, "bounded size"):
            cli.bounded_bytes(self.trace, 1)
        linked = self.private / "linked.json"; linked.symlink_to(self.trace)
        args = self.base.copy(); args[args.index("--trace")+1] = str(linked)
        self.assertEqual(self.invoke(args), 1)
        self.assertIn("bounded regular trace", read_json(self.output)["failure"]["message"])

    def test_legacy_trace_keeps_observed_ratio_but_cannot_verify_stage_coverage(self):
        trace = read_json(self.trace); del trace["decisionStageInventory"]; self.trace.write_text(json.dumps(trace))
        self.base[self.base.index("--trace-sha256")+1] = self.sha(self.trace)
        self.assertEqual(self.invoke(), 2)
        report = read_json(self.output)
        self.assertEqual(report["boundResultRatio"], 1)
        self.assertIn("missing_stage_inventory", report["dataGaps"])
        self.assertFalse(report["stageInventory"]["storedStageCoverageVerified"])

    def test_pending_stage_is_not_a_synthetic_http_result(self):
        trace = read_json(self.trace); original = trace["decisionStageInventory"]["stages"][0]
        trace["decisionStageInventory"]["stages"].append({**original, "stageId": "pending", "stageStatus": "running", "observationRecorded": False})
        self.trace.write_text(json.dumps(trace)); self.base[self.base.index("--trace-sha256")+1] = self.sha(self.trace)
        self.assertEqual(self.invoke(), 2); report = read_json(self.output)
        self.assertEqual(report["callerLatencyMs"]["count"], 1)
        self.assertEqual(report["stageInventory"]["assignedStageBoundResultRatio"], {"lower": .5, "upper": 1.0})
        self.assertFalse(report["stageInventory"]["httpAttemptInventoryVerified"])

    def test_contradictory_inventory_saves_failed_receipt(self):
        trace = read_json(self.trace); trace["decisionStageInventory"]["stages"][0]["observationRecorded"] = False
        self.trace.write_text(json.dumps(trace)); self.base[self.base.index("--trace-sha256")+1] = self.sha(self.trace)
        self.assertEqual(self.invoke(), 1)
        self.assertIn("marker differ", read_json(self.output)["failure"]["message"])

    def configured_bytes_args(self):
        args = self.base.copy(); sha = self.sha(self.profile_file)
        args[args.index("--profile-sha256")+1] = sha
        trace = read_json(self.trace)
        trace["decisionObservations"][0]["profileSha256"] = sha
        trace["decisionStageInventory"]["stages"][0]["profileSha256"] = sha
        self.trace.write_text(json.dumps(trace))
        args[args.index("--trace-sha256")+1] = self.sha(self.trace)
        return args + ["--profile-identity", "coordinator_json_bytes"]

    def test_explicit_bytes_mode_matches_noncanonical_coordinator_config(self):
        self.assertEqual(self.invoke(self.configured_bytes_args()), 0)
        report = verify_seal(read_json(self.output), "agat.decision.shadow-caller-sli.v1")
        self.assertEqual(report["profileIdentity"], "coordinator_json_bytes")
        self.assertEqual(report["profileSha256"], self.sha(self.profile_file))
        self.assertEqual(report["profileFingerprintSha256"], fingerprint(self.profile))
        self.assertNotEqual(report["profileSha256"], report["profileFingerprintSha256"])
        self.assertEqual(report["counts"]["profileBindingMismatches"], 0)
        self.assertFalse(report["populationCoverageVerified"]); self.assertFalse(report["sloAccepted"])

    def test_raw_hash_requires_explicit_mode_and_correct_independent_pin(self):
        args = self.configured_bytes_args()[:-2]
        self.assertEqual(self.invoke(args), 1)
        self.assertIn("profile content SHA", read_json(self.output)["failure"]["message"])
        self.output.unlink(); args += ["--profile-identity", "coordinator_json_bytes"]
        args[args.index("--profile-sha256")+1] = fingerprint(self.profile)
        self.assertEqual(self.invoke(args), 1)
        self.assertIn("coordinator profile bytes SHA", read_json(self.output)["failure"]["message"])

    def test_whitespace_changes_need_a_new_pin_and_do_not_match_old_assignments(self):
        args = self.configured_bytes_args(); self.profile_file.write_bytes(self.profile_file.read_bytes()+b"\n")
        args[args.index("--profile-file-sha256")+1] = self.sha(self.profile_file)
        self.assertEqual(self.invoke(args), 1)
        self.assertIn("coordinator profile bytes SHA", read_json(self.output)["failure"]["message"])
        self.output.unlink(); args[args.index("--profile-sha256")+1] = self.sha(self.profile_file)
        self.assertEqual(self.invoke(args), 2)
        self.assertEqual(read_json(self.output)["counts"]["profileBindingMismatches"], 1)

    def test_bytes_mode_rejects_utf16_even_with_matching_file_and_content_hashes(self):
        self.profile_file.write_bytes(json.dumps(self.profile).encode("utf-16"))
        args = self.configured_bytes_args()
        args[args.index("--profile-file-sha256")+1] = self.sha(self.profile_file)
        self.assertEqual(self.invoke(args), 1)
        self.assertEqual(read_json(self.output)["status"], "failed")


if __name__ == "__main__": unittest.main()
