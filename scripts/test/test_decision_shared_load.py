import itertools
import json
import threading
import unittest
from unittest.mock import patch

from decision_runtime.artifacts import sealed, verify_seal
from scripts.lib.decision_baselines import BaselineError
from scripts.lib.decision_shared_load import PHASES, SCHEMA, benchmark_shared, summarize
from scripts.test.test_decision_baselines import dataset
from scripts.test.test_decision_performance import Fixture


class Primary:
    def __init__(self):
        self.model = "test-only"
        self.identity = {"digest": "1" * 64, "provider": "fixture"}
        self.requests = []
        self.changed = False
    def tag(self): return {"digest": "2" * 64 if self.changed else self.identity["digest"]}
    def predict(self, request):
        self.requests.append(request.to_dict())
        return {"selectedOptionId": request.options[0].id, "inputTokens": 100, "outputTokens": 12}
    def transport(self, _method, _path):
        return {"models": [{"name": self.model, "digest": self.identity["digest"], "size": 100, "size_vram": 80}]}


class SharedTest(unittest.TestCase):
    def run_probe(self, fixture=None, primary=None, **kwargs):
        fixture = fixture or Fixture(); primary = primary or Primary()
        return benchmark_shared(dataset(), "http://127.0.0.1:1", lambda: primary, warmup=1,
                                client=fixture, health_transport=fixture.health, **kwargs)

    def test_separate_sequential_and_overlapping_calls_are_distinct_and_do_not_send_gold(self):
        entered_primary, entered_decision = threading.Event(), threading.Event()
        class ConcurrentPrimary(Primary):
            def predict(self, request):
                result = super().predict(request)
                if len(self.requests) == 4:
                    entered_primary.set()
                    if not entered_decision.wait(1): raise RuntimeError("Overlap was serialized")
                return result
        class ConcurrentDecision(Fixture):
            def decide(self, shadow):
                if self.calls == 3:
                    entered_decision.set()
                    if not entered_primary.wait(1): raise RuntimeError("Overlap was serialized")
                return super().decide(shadow)
        primary, fixture = ConcurrentPrimary(), ConcurrentDecision()
        report = self.run_probe(fixture, primary)
        verify_seal(report, SCHEMA)
        self.assertEqual(report["status"], "observed")
        self.assertEqual([p["name"] for p in report["phases"]], list(PHASES))
        self.assertEqual(len(report["warmup"]), 2)
        self.assertEqual(sum(len(p["rows"]) for p in report["phases"]), 8)
        self.assertEqual(report["phases"][2]["pairs"][0]["requestOverlapMs"], 0)
        self.assertGreater(report["phases"][3]["pairs"][0]["requestOverlapMs"], 0)
        self.assertEqual(report["repeatDecisionChanges"], {"primary": [], "decision": []})
        self.assertNotIn("GOLD NEVER SENT", json.dumps(primary.requests + fixture.requests))
        self.assertNotIn("expectedOptionId", json.dumps(primary.requests + fixture.requests))
        self.assertNotIn("Input only", json.dumps(report))
        self.assertFalse(report["qualifiedForRouting"])

    def test_invalid_data_and_plans_fail_before_contacting_services(self):
        fixture, calls = Fixture(), []
        def primary(): calls.append(True); return Primary()
        data = dataset(); data.pop("sha256"); data["cases"][0]["split"] = "holdout"
        with self.assertRaises(ValueError):
            benchmark_shared(sealed(data), "http://127.0.0.1:1", primary, client=fixture, health_transport=fixture.health)
        for plan in ({"rounds": 3}, {"warmup": 0}, {"time_budget_s": 601}, {"timeout_ms": 10}):
            with self.assertRaises(ValueError):
                benchmark_shared(dataset(), "http://127.0.0.1:1", primary, client=fixture, health_transport=fixture.health, **plan)
        self.assertEqual((fixture.health_calls, fixture.calls, calls), (0, 0, []))

    def test_failures_and_busy_are_not_successful_fast_latency_samples(self):
        rows = [{"status": "ok", "reason": "accepted", "wallMs": 500, "inputTokens": 10, "outputTokens": 0},
                {"status": "unavailable", "reason": "busy", "wallMs": 1}]
        result = summarize(rows)
        self.assertEqual(result["computedWallMs"]["p95"], 500)
        self.assertEqual(result["failedWallMs"]["p95"], 1)
        fixture = Fixture(); fixture.busy = True
        report = self.run_probe(fixture)
        self.assertEqual(report["status"], "degraded")
        self.assertTrue(any(p["summary"]["decision"]["failures"].get("busy") for p in report["phases"]))

    def test_changed_or_unavailable_final_profile_retains_evidence_as_degraded(self):
        fixture = Fixture(); fixture.changed = True
        self.assertFalse(self.run_probe(fixture)["decisionProfileStable"])
        class Lost(Fixture):
            def health(self, *args):
                if self.health_calls: raise BaselineError("unreachable")
                return super().health(*args)
        report = self.run_probe(Lost())
        self.assertEqual(report["status"], "degraded")
        self.assertEqual(len(report["phases"]), 6)
        primary = Primary(); primary.changed = True
        self.assertFalse(self.run_probe(primary=primary)["primaryStable"])

    def test_unknown_backend_exception_does_not_leak_input_or_secrets(self):
        class Broken(Primary):
            def predict(self, request): raise BaselineError("PRIVATE SOURCE CONTENT")
        report = self.run_probe(primary=Broken())
        self.assertEqual(report["status"], "degraded")
        self.assertNotIn("PRIVATE SOURCE", json.dumps(report))

    def test_time_budget_preserves_partial_evidence_without_new_inference(self):
        primary, fixture = Primary(), Fixture()
        with patch("scripts.lib.decision_shared_load.time.perf_counter", side_effect=itertools.count()):
            report = self.run_probe(fixture, primary, time_budget_s=1)
        self.assertEqual(report["stoppedReason"], "time_budget")
        self.assertEqual((fixture.calls, len(primary.requests)), (0, 0))
        self.assertEqual(report["status"], "degraded")


if __name__ == "__main__": unittest.main()
