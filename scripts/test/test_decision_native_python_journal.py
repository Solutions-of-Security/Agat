from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.lib.decision_native_python_journal import canonical, verify_journal
from scripts.lib.decision_native_python_observations import ObservedNativeBackend

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("native_python_journal_cli", ROOT / "scripts/verify-native-python-journal.py")
cli = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(cli)
sha = lambda raw: hashlib.sha256(raw).hexdigest()


class SyntheticModel:
    def __call__(self, value):
        return value + 1


class NativePythonJournalTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve(); self.path = self.root / "original.jsonl"
        mx = SimpleNamespace(eval=lambda value: value); model = SyntheticModel()
        backend = SimpleNamespace(identity={"implementationSha256": "b" * 64}, load_ms=0,
                                  text_model=SimpleNamespace(model=model), mx=mx)
        def score(request):
            if request.input_sha256 == "c" * 64: raise ValueError("Synthetic pre-model refusal")
            return mx.eval(model(7))
        backend.score = score
        observer = ObservedNativeBackend(backend, self.path)
        try:
            for input_sha in ("a" * 64, "a" * 64, "b" * 64, "c" * 64):
                try: observer.score(SimpleNamespace(input_sha256=input_sha))
                except ValueError: pass
        finally: observer.close()
        self.raw = self.path.read_bytes()
        self.events = [json.loads(line) for line in self.raw.splitlines()]
        self.observer_sha = self.events[0]["identity"]["pythonCallObservation"]["observerSourceSha256"]
        self.identity_sha = sha(canonical(self.events[0]["identity"]))
        self.inputs = ["a" * 64, "a" * 64, "b" * 64, "c" * 64]

    def verify(self, path=None, **kwargs):
        path = path or self.path
        return verify_journal(path, sha(path.read_bytes()), expected_observer_sha256=self.observer_sha, **kwargs)

    def modified(self, events, name="changed.jsonl"):
        previous = None; result = []
        for sequence, event in enumerate(deepcopy(events)):
            event.update(sequence=sequence, previousSha256=previous)
            event.pop("sha256", None); event["sha256"] = sha(canonical(event))
            previous = event["sha256"]; result.append(canonical(event))
        path = self.root / name; path.write_bytes(b"\n".join(result) + b"\n")
        return path

    def args(self, path, output, extra=()):
        return ["--journal", str(path), "--journal-file-sha256", sha(path.read_bytes()),
                "--expected-observer-sha256", self.observer_sha, "--output-dir", str(output), *extra]

    def test_original_completed_calls_include_repeat_and_pre_model_exception(self):
        receipt = self.verify(expected_identity_sha256=self.identity_sha, expected_input_sha256s=self.inputs)
        self.assertEqual(receipt["status"], "verified")
        self.assertTrue(receipt["completeScoringTrace"])
        self.assertEqual(receipt["entries"], {"backend_score": 4, "text_backbone": 3, "mx_eval": 3})
        self.assertEqual(receipt["returns"], {"backend_score": 3, "text_backbone": 3, "mx_eval": 3})
        self.assertEqual(receipt["raises"], {"backend_score": 1, "text_backbone": 0, "mx_eval": 0})
        self.assertEqual(receipt["unfinished"], {"backend_score": 0, "text_backbone": 0, "mx_eval": 0})
        self.assertEqual([s["inputSha256"] for s in receipt["scores"]], self.inputs)
        self.assertEqual(receipt["scores"][-1]["entries"]["text_backbone"], 0)

    def test_open_model_span_is_counted_as_entry_and_unfinished(self):
        receipt = self.verify(self.modified(self.events[:3]), expected_input_sha256s=self.inputs)
        self.assertEqual(receipt["status"], "incomplete")
        self.assertFalse(receipt["completeScoringTrace"])
        self.assertEqual(receipt["entries"], {"backend_score": 1, "text_backbone": 1, "mx_eval": 0})
        self.assertEqual(receipt["returns"]["text_backbone"], 0)
        self.assertEqual(receipt["unfinished"], {"backend_score": 1, "text_backbone": 1, "mx_eval": 0})
        self.assertEqual(receipt["unstartedExpectedInputs"], 3)

    def test_model_return_without_backend_end_is_not_complete_inference(self):
        receipt = self.verify(self.modified(self.events[:4]))
        self.assertEqual(receipt["returns"]["text_backbone"], 1)
        self.assertEqual(receipt["unfinished"]["backend_score"], 1)
        self.assertFalse(receipt["completeScoringTrace"])

    def test_header_only_is_not_a_completed_scoring_trace(self):
        receipt = self.verify(self.modified(self.events[:1]))
        self.assertTrue(receipt["allRecordedSpansEnded"])
        self.assertFalse(receipt["completeScoringTrace"])
        self.assertEqual(receipt["recordedInputCount"], 0)

    def test_ended_first_score_is_incomplete_for_longer_bound_schedule(self):
        receipt = self.verify(self.modified(self.events[:8]), expected_input_sha256s=self.inputs)
        self.assertTrue(receipt["recordedScoringTraceComplete"])
        self.assertFalse(receipt["completeScoringTrace"])
        self.assertFalse(receipt["expectedInputSequenceFullyRecorded"])
        self.assertEqual(receipt["unstartedExpectedInputs"], 3)

    def test_wrong_or_shorter_bound_sequence_is_refused(self):
        for expected in (["b" * 64, *self.inputs[1:]], self.inputs[:3]):
            with self.subTest(expected=expected), self.assertRaises(ValueError):
                self.verify(expected_input_sha256s=expected)

    def test_false_hook_confirmation_remains_incomplete(self):
        events = deepcopy(self.events)
        next(e for e in events if e["event"] == "hooks_restored")["modelObjectPreserved"] = False
        receipt = self.verify(self.modified(events))
        self.assertTrue(receipt["allRecordedSpansEnded"])
        self.assertFalse(receipt["recordedHooksConfirmedRestored"])
        self.assertFalse(receipt["completeScoringTrace"])

    def test_changed_call_binding_is_refused_even_with_new_seals(self):
        for field, value in (("inputSha256", "d" * 64), ("pid", 1), ("threadId", 1), ("scoreId", 2)):
            events = deepcopy(self.events); events[2][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.verify(self.modified(events, field + ".jsonl"))

    def test_unpaired_end_and_invalid_call_order_are_refused(self):
        for order in ([0, 1, 3, 2, *range(4, len(self.events))], [0, 2, 1, *range(3, len(self.events))]):
            with self.subTest(order=order), self.assertRaises(ValueError):
                self.verify(self.modified([self.events[i] for i in order]))

    def test_bad_clock_delta_boolean_timer_and_backwards_clock_are_refused(self):
        for index, field, value in ((3, "hostElapsedNs", -1), (2, "hostNs", True),
                                    (2, "threadCpuNs", 0), (3, "threadCpuElapsedNs", True)):
            events = deepcopy(self.events); events[index][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.verify(self.modified(events, field + str(index) + ".jsonl"))

    def test_wrong_outer_identity_and_observer_pins_are_refused(self):
        with self.assertRaises(ValueError):
            verify_journal(self.path, "0" * 64, expected_observer_sha256=self.observer_sha)
        with self.assertRaises(ValueError):
            verify_journal(self.path, sha(self.raw), expected_observer_sha256="0" * 64)
        with self.assertRaises(ValueError): self.verify(expected_identity_sha256="0" * 64)

    def test_duplicate_member_truncated_record_and_nonfinite_number_are_refused(self):
        first = self.raw.splitlines()[0]
        for raw in (self.raw[:-1], first[:-1] + b',"sequence":0}\n', first[:-1] + b',"extra":NaN}\n'):
            path = self.root / "invalid.jsonl"; path.write_bytes(raw)
            with self.subTest(raw=raw[:20]), self.assertRaises(ValueError): self.verify(path)

    def test_changed_seal_and_previous_hash_are_refused(self):
        for field in ("sha256", "previousSha256"):
            events = deepcopy(self.events); events[2][field] = "0" * 64
            path = self.root / (field + ".jsonl"); path.write_bytes(b"\n".join(canonical(e) for e in events) + b"\n")
            with self.subTest(field=field), self.assertRaises(ValueError): self.verify(path)

    def test_boolean_sequence_and_measurement_claims_are_refused(self):
        events = deepcopy(self.events); events[0]["identity"]["pythonCallObservation"]["gpuTimeMeasured"] = True
        with self.assertRaises(ValueError): self.verify(self.modified(events))
        events = deepcopy(self.events); events[1]["sequence"] = True
        events[1].pop("sha256"); events[1]["sha256"] = sha(canonical(events[1]))
        path = self.root / "boolean.jsonl"; path.write_bytes(b"\n".join(canonical(e) for e in events[:2]) + b"\n")
        with self.assertRaises(ValueError): self.verify(path)

    def test_size_bound_and_final_symlink_are_refused(self):
        with patch("scripts.lib.decision_native_python_journal.MAX_JOURNAL_BYTES", len(self.raw) - 1):
            with self.assertRaises(ValueError): self.verify()
        link = self.root / "link.jsonl"; link.symlink_to(self.path)
        with self.assertRaises(ValueError): self.verify(link)

    def test_verification_does_not_assert_execution_or_quality(self):
        receipt = self.verify()
        for key in ("observerCodeRevalidated", "runtimeSourcesRevalidated", "modelExecutionAuthenticated",
                    "gpuKernelsMeasured", "gpuTimeMeasured", "instrumentationOverheadExcluded",
                    "classificationAccuracyMeasured", "routingEnabled", "identityBindingMatched", "inputSequenceBindingMatched"):
            self.assertIs(receipt[key], False)
        self.assertEqual(receipt["modelCallsDuringVerification"], 0)
        self.assertEqual(self.path.read_bytes(), self.raw)

    def test_cli_creates_only_new_private_output_and_preserves_original(self):
        output = self.root / "docs/private/verified"
        with patch.object(cli, "ROOT", self.root):
            self.assertEqual(cli.main(self.args(self.path, output)), 0)
            self.assertEqual(cli.main(self.args(self.path, output)), 1)
        receipt = json.loads((output / "verification.json").read_bytes())
        self.assertTrue(receipt["completeScoringTrace"])
        self.assertEqual((output / "verification.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.path.read_bytes(), self.raw)

    def test_cli_partial_requires_explicit_diagnostic_output(self):
        path = self.modified(self.events[:3]); output = self.root / "docs/private/partial"
        with patch.object(cli, "ROOT", self.root):
            self.assertEqual(cli.main(self.args(path, output)), 1)
            self.assertFalse(output.exists())
            self.assertEqual(cli.main(self.args(path, output, ["--allow-incomplete"])), 0)
        receipt = json.loads((output / "verification.json").read_bytes())
        self.assertFalse(receipt["completeScoringTrace"])
        self.assertEqual(receipt["status"], "incomplete")

    def test_cli_refuses_public_directory_before_writing(self):
        output = self.root / "docs/public-output"
        with patch.object(cli, "ROOT", self.root): self.assertEqual(cli.main(self.args(self.path, output)), 1)
        self.assertFalse(output.exists())
