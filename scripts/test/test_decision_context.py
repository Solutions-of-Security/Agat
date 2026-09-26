import itertools
import unittest
from unittest.mock import patch

from decision_runtime.artifacts import verify_seal
from decision_runtime.contracts import DecisionError
from decision_runtime.engine import Scores
from decision_runtime.mlx_backend import MlxBackend
from decision_runtime.tests.test_decisions import Backend
from scripts.lib.decision_context import FACT, POSITIONS, SCHEMA, make_probe, profile_context, token_count
from scripts.test.test_decision_resources import Memory


class Tokenizer:
    def encode(self, text, **_kwargs):
        return list(range(10 + text.count(" x")))


class ContextBackend(Backend):
    def __init__(self):
        super().__init__([3, 0, -2])
        self.identity = {**self.identity, "maxInputTokens": 512}
        self.tokenizer = Tokenizer()
        self.requests = []

    def score(self, request):
        self.requests.append(request)
        count = token_count(self.tokenizer, request)
        if count > self.identity["maxInputTokens"]:
            raise DecisionError("context_too_long", "No truncation")
        return Scores(self.logits, count)


class ContextTest(unittest.TestCase):
    def run_probe(self, backend=None, **kwargs):
        return profile_context(lambda: backend or ContextBackend(), max_tokens=512, targets=[256, 512],
                               memory_factory=Memory, **kwargs)

    def test_padding_is_exact_and_preserves_fact_question_options_and_position(self):
        tokenizer = Tokenizer()
        for target in (256, 512, 2048, 4096, 4097):
            for position in POSITIONS:
                request = make_probe(tokenizer, target, position)
                self.assertEqual(token_count(tokenizer, request), target)
                self.assertEqual(request.state.count(FACT), 1)
                before, after = request.state.split(FACT)
                if position == "front": self.assertEqual(before.count(" x"), 0)
                elif position == "end": self.assertEqual(after.count(" x"), 0)
                else: self.assertLessEqual(abs(before.count(" x") - after.count(" x")), 1)
                self.assertEqual([o.id for o in request.options], ["code_42", "code_24", "missing"])

    def test_boundary_rejections_are_expected_and_excluded_from_success_latency(self):
        report = self.run_probe(rounds=2)
        verify_seal(report, SCHEMA)
        self.assertEqual(report["status"], "observed")
        self.assertEqual((report["summary"]["attempts"], report["summary"]["scored"]), (12, 12))
        boundary = [r for r in report["rows"] if r["phase"].startswith("over_limit")]
        self.assertEqual(len(boundary), 2)
        self.assertTrue(all(r["expectationMet"] and r["reason"] == "context_too_long" for r in boundary))
        self.assertEqual(report["repeatDecisionChanges"], [])
        self.assertFalse(report["qualifiedForRouting"])
        self.assertFalse(report["workload"]["datasetUsed"])
        self.assertEqual(len(report["workload"]["requests"]), 7)

    def test_invalid_plans_are_rejected_before_model_load(self):
        loads = []
        def factory(): loads.append(True); return ContextBackend()
        for args in ({"max_tokens": 500}, {"max_tokens": True}, {"targets": [512]},
                     {"targets": [2048, 1024]}, {"targets": [256, 2048, 2048]},
                     {"targets": [True, 2048]}, {"rounds": 6}, {"time_budget_s": 301}):
            with self.assertRaises(ValueError): profile_context(factory, memory_factory=Memory, **args)
        self.assertEqual(loads, [])
        with self.assertRaisesRegex(ValueError, "Backend context"):
            profile_context(factory, memory_factory=Memory)

    def test_incorrect_token_counts_or_missing_rejection_are_degraded(self):
        class Truncated(ContextBackend):
            def score(self, request):
                result = super().score(request)
                return Scores(result.logits, result.input_tokens - 1)
        result = self.run_probe(Truncated(), rounds=1)
        self.assertEqual(result["status"], "degraded")
        self.assertEqual(result["summary"]["failures"], {"invalid_response": 6})
        class AcceptsOversize(ContextBackend):
            def score(self, request): return Scores(self.logits, token_count(self.tokenizer, request))
        result = self.run_probe(AcceptsOversize(), rounds=1)
        self.assertEqual(result["status"], "degraded")
        self.assertTrue(all(not r["expectationMet"] for r in result["rows"] if r["phase"].startswith("over_limit")))

    def test_repeated_result_changes_and_profile_changes_are_visible(self):
        class Drifting(ContextBackend):
            def score(self, request):
                result = super().score(request)
                return Scores([0, 3, -2] if len(self.requests) % 2 else result.logits, result.input_tokens)
        result = self.run_probe(Drifting(), rounds=2)
        self.assertEqual(result["status"], "degraded")
        self.assertTrue(result["repeatDecisionChanges"])
        class Changed(ContextBackend):
            def score(self, request):
                self.identity["revision"] = "changed"
                return super().score(request)
        self.assertFalse(self.run_probe(Changed(), rounds=1)["profileStable"])

    def test_budget_exhaustion_preserves_empty_measurement_without_inference(self):
        backend = ContextBackend()
        with patch("scripts.lib.decision_context.time.perf_counter", side_effect=itertools.count()):
            result = self.run_probe(backend, time_budget_s=1)
        self.assertEqual(result["status"], "degraded")
        self.assertEqual(result["stoppedReason"], "time_budget")
        self.assertEqual(backend.requests, [])
        self.assertIsNone(result["summary"]["scoredWallMs"]["p95"])

    def test_mlx_backend_rejects_over_limit_before_any_gpu_access(self):
        class NoGpu:
            def __getattr__(self, _name): raise AssertionError("GPU must not be touched")
        backend = MlxBackend.__new__(MlxBackend)
        backend.tokenizer = Tokenizer(); backend.max_tokens = 512; backend.mx = NoGpu()
        with self.assertRaises(DecisionError) as caught:
            backend.score(make_probe(backend.tokenizer, 513, "end"))
        self.assertEqual(caught.exception.code, "context_too_long")

    def test_unreachable_token_length_is_rejected_without_slicing(self):
        class Unreachable:
            def encode(self, _text, **_kwargs): return [1]
        with self.assertRaisesRegex(ValueError, "exact synthetic"):
            make_probe(Unreachable(), 256, "front")


if __name__ == "__main__": unittest.main()
