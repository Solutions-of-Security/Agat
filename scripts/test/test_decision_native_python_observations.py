import hashlib
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest

from scripts.lib.decision_native_python_observations import ObservedNativeBackend, SCHEMA


class Model:
    def __call__(self, value):
        return value + 1


class InheritedModel(Model):
    pass


class NativePythonObservationsTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "events.jsonl"
        self.model = Model()
        self.mx = SimpleNamespace(eval=lambda value: value)
        self.request = SimpleNamespace(input_sha256="a" * 64)
        self.backend = SimpleNamespace(identity={"implementationSha256": "b" * 64}, load_ms=12,
                                       text_model=SimpleNamespace(model=self.model), mx=self.mx)
        self.backend.score = lambda request: self.mx.eval(self.model(7))
        self.original_call = Model.__call__
        self.original_eval = self.mx.eval
        self.observer = ObservedNativeBackend(self.backend, self.path)
        self.addCleanup(self.observer.close)

    def records(self):
        return [json.loads(line) for line in self.path.read_bytes().splitlines()]

    def assert_restored(self):
        self.assertIs(Model.__call__, self.original_call)
        self.assertIs(self.mx.eval, self.original_eval)
        self.assertIs(self.backend.text_model.model, self.model)

    def test_delegates_scores_and_observes_all_three_python_entries(self):
        self.assertEqual(self.observer.score(self.request), 8)
        records = self.records()
        self.assertEqual([r["kind"] for r in records if r["event"] == "call_start"],
                         ["backend_score", "text_backbone", "mx_eval"])
        self.assertEqual(sum(r["event"] == "call_end" for r in records), 3)
        restored = next(r for r in records if r["event"] == "hooks_restored")
        self.assertTrue(all(restored[k] for k in ("modelObjectPreserved", "modelCallRestored", "evalRestored")))
        self.assert_restored()

    def test_repeated_inputs_are_separate_observed_calls(self):
        self.observer.score(self.request); self.observer.score(self.request)
        starts = [r for r in self.records() if r["event"] == "call_start"]
        self.assertEqual([r["scoreId"] for r in starts if r["kind"] == "backend_score"], [1, 2])
        self.assertEqual(len(starts), 6)
        self.assert_restored()

    def test_pre_model_refusal_records_no_forward_or_eval(self):
        def refuse(request):
            raise ValueError("No model call")
        self.backend.score = refuse
        with self.assertRaises(ValueError): self.observer.score(self.request)
        ends = [r for r in self.records() if r["event"] == "call_end"]
        self.assertEqual([(r["kind"], r["status"], r["exceptionType"]) for r in ends],
                         [("backend_score", "raised", "ValueError")])
        self.assert_restored()

    def test_model_exception_restores_methods_and_does_not_evaluate(self):
        def broken(instance, value):
            raise RuntimeError("Synthetic model failure")
        Model.__call__ = broken
        try:
            with self.assertRaises(RuntimeError): self.observer.score(self.request)
            self.assertIs(Model.__call__, broken)
        finally:
            Model.__call__ = self.original_call
        ends = [r for r in self.records() if r["event"] == "call_end"]
        self.assertEqual([r["kind"] for r in ends], ["text_backbone", "backend_score"])
        self.assertTrue(all(r["status"] == "raised" for r in ends))
        self.assert_restored()

    def test_eval_exception_restores_methods(self):
        def broken(value): raise RuntimeError("Synthetic eval failure")
        self.mx.eval = broken
        try:
            with self.assertRaises(RuntimeError): self.observer.score(self.request)
            self.assertIs(self.mx.eval, broken)
        finally:
            self.mx.eval = self.original_eval
        self.assert_restored()

    def test_other_model_instance_is_delegated_without_count(self):
        other = Model()
        self.backend.score = lambda request: self.mx.eval(self.model(other(5)))
        self.assertEqual(self.observer.score(self.request), 7)
        self.assertEqual(sum(r["event"] == "call_start" and r["kind"] == "text_backbone"
                             for r in self.records()), 1)
        self.assert_restored()

    def test_other_thread_is_delegated_without_count(self):
        values = []
        def score(request):
            thread = threading.Thread(target=lambda: values.append(self.mx.eval(self.model(5))))
            thread.start(); thread.join(2)
            self.assertFalse(thread.is_alive())
            return self.mx.eval(self.model(7))
        self.backend.score = score
        self.assertEqual(self.observer.score(self.request), 8)
        self.assertEqual(values, [6])
        self.assertEqual(sum(r["event"] == "call_start" for r in self.records()), 3)
        self.assert_restored()

    def test_inherited_call_is_restored_without_new_class_attribute(self):
        self.backend.text_model.model = InheritedModel()
        path = self.path.with_name("inherited.jsonl")
        observer = ObservedNativeBackend(self.backend, path)
        try:
            self.backend.score = lambda request: self.mx.eval(self.backend.text_model.model(7))
            self.assertEqual(observer.score(self.request), 8)
            self.assertNotIn("__call__", InheritedModel.__dict__)
        finally: observer.close()
        self.assert_restored_after_inherited()

    def assert_restored_after_inherited(self):
        self.assertIs(Model.__call__, self.original_call)
        self.assertIs(self.mx.eval, self.original_eval)

    def test_nested_observation_is_refused_and_outer_hooks_restored(self):
        other = ObservedNativeBackend(self.backend, self.path.with_name("other.jsonl"))
        try:
            self.backend.score = lambda request: other.score(request)
            with self.assertRaisesRegex(RuntimeError, "Concurrent or nested"):
                self.observer.score(self.request)
            self.assertEqual(len(other._backend.identity), 1)
            self.assertEqual(len(other_path_records := self.path.with_name("other.jsonl").read_bytes().splitlines()), 1)
        finally: other.close()
        self.assert_restored()

    def test_journal_is_exclusive_private_and_rejects_use_after_close(self):
        with self.assertRaises(FileExistsError): ObservedNativeBackend(self.backend, self.path)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.observer.close(); self.observer.close()
        with self.assertRaises(ValueError): self.observer.score(self.request)
        self.assertEqual(len(self.records()), 1)

    def test_chain_bindings_clocks_and_identity_are_explicit(self):
        self.observer.score(self.request)
        previous = None
        for index, record in enumerate(self.records()):
            self.assertEqual(record["schemaVersion"], SCHEMA)
            self.assertEqual(record["sequence"], index)
            self.assertEqual(record["previousSha256"], previous)
            raw = json.dumps({k: v for k, v in record.items() if k != "sha256"}, sort_keys=True,
                             separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
            self.assertEqual(record["sha256"], hashlib.sha256(raw).hexdigest())
            previous = record["sha256"]
            if record["event"] == "call_end":
                self.assertGreaterEqual(record["hostElapsedNs"], 0)
                self.assertGreaterEqual(record["threadCpuElapsedNs"], 0)
        header = self.records()[0]
        self.assertFalse(header["capturesRequestText"])
        self.assertFalse(header["modelLoadObserved"])
        observation = self.observer.identity["pythonCallObservation"]
        self.assertTrue(all(observation[k] is False for k in
                            ("gpuKernelsMeasured", "gpuTimeMeasured", "instrumentationOverheadExcluded")))
        self.assertNotIn("pythonCallObservation", self.backend.identity)

    def test_invalid_fingerprint_is_refused_before_any_call(self):
        self.request.input_sha256 = "invalid"
        with self.assertRaises(ValueError): self.observer.score(self.request)
        self.assertEqual(len(self.records()), 1)
        self.assert_restored()
