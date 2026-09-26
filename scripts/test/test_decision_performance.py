import copy
import json
import threading
import unittest

from decision_runtime.artifacts import sealed, verify_seal
from decision_runtime.contracts import canonical_json, fingerprint
from decision_runtime.engine import DecisionEngine
from decision_runtime.tests.test_decisions import Backend
from scripts.lib.decision_performance import SCHEMA, benchmark, profile_from_health, summarize, validate_result
from scripts.test.test_decision_baselines import dataset


class Fixture:
    def __init__(self):
        self.engine = DecisionEngine(Backend([3, 0]))
        self.requests = []
        self.lock = threading.Lock()
        self.calls = 0
        self.health_calls = 0
        self.busy = False
        self.invalid = False
        self.changed = False

    def health(self, method, path):
        self.health_calls += 1
        profile = self.engine.profile()
        if self.changed and self.health_calls > 1:
            profile["runtimeVersion"] = "changed"
        return {"status": "ready", "mode": "shadow", "profileJson": canonical_json(profile),
                "profileSha256": fingerprint(profile)}

    def decide(self, shadow):
        with self.lock:
            self.requests.append(shadow)
            self.calls += 1
            if self.busy and self.calls % 2 == 0:
                return {"status": "unavailable", "reason": "busy"}
            result = self.engine.decide(shadow["request"])
            if self.invalid:
                result["distribution"][0]["probability"] = 0
            return {"result": result}


class PerformanceTest(unittest.TestCase):
    def test_probe_uses_worker_descriptor_and_never_emits_source_or_gold(self):
        fixture = Fixture()
        data = dataset()
        report = benchmark(data, "http://127.0.0.1:1", rounds=4, concurrency=(1, 2), warmup=1,
                           health_transport=fixture.health, client=fixture)
        verify_seal(report, SCHEMA)
        self.assertEqual(report["status"], "observed")
        self.assertEqual(fixture.calls, 9)
        self.assertEqual(len(report["warmup"]), 1)
        for phase in report["phases"]:
            self.assertEqual((phase["summary"]["attempts"], phase["summary"]["scored"]), (4, 4))
        self.assertNotIn("GOLD NEVER SENT", json.dumps(fixture.requests))
        self.assertNotIn("expectedOptionId", json.dumps(fixture.requests))
        self.assertNotIn("Input only", json.dumps(report))
        self.assertFalse(report["qualifiedForRouting"])
        self.assertTrue(all(r["profileSha256"] == report["profileSha256"] for r in fixture.requests))

    def test_holdout_score_and_excessive_load_are_rejected_before_any_network(self):
        fixture = Fixture()
        variants = []
        data = dataset(); data.pop("sha256"); data["cases"][0]["split"] = "holdout"; variants.append(sealed(data))
        data = dataset(); data.pop("sha256"); data["cases"][0]["request"]["kind"] = "score"
        for i, option in enumerate(data["cases"][0]["request"]["options"]): option["value"] = i
        variants.append(sealed(data))
        for data in variants:
            with self.assertRaises(ValueError):
                benchmark(data, "http://127.0.0.1:1", health_transport=fixture.health, client=fixture)
        for config in ({"rounds": 1000}, {"concurrency": (1, 1)}, {"concurrency": (100,)}, {"timeout_ms": 50}):
            with self.assertRaises(ValueError):
                benchmark(dataset(), "http://127.0.0.1:1", health_transport=fixture.health, client=fixture, **config)
        self.assertEqual((fixture.health_calls, fixture.calls), (0, 0))

    def test_fast_failures_cannot_make_scored_percentiles_look_better(self):
        rows = [{"status": "ok", "reason": "accepted", "wallMs": 500, "runtimeMs": 490, "inputTokens": 100},
                {"status": "abstain", "reason": "below_threshold", "wallMs": 1500, "runtimeMs": 1490, "inputTokens": 500}]
        rows.extend({"status": "unavailable", "reason": "busy", "wallMs": 1} for _ in range(98))
        result = summarize(rows, 2000)
        self.assertEqual(result["scoredWallMs"], {"count": 2, "p50": 500, "p95": 1500, "max": 1500})
        self.assertEqual(result["failedWallMs"]["count"], 98)
        self.assertEqual(result["failures"], {"busy": 98})
        self.assertEqual(result["completedPerSecond"], 1)
        self.assertEqual(result["scoredAboveBudgetMs"]["1000"], 1)

    def test_malformed_success_and_changed_profile_are_reported_as_degraded(self):
        for kind in ("invalid", "changed"):
            fixture = Fixture(); setattr(fixture, kind, True)
            report = benchmark(dataset(), "http://127.0.0.1:1", rounds=1, concurrency=(1,), warmup=0,
                               health_transport=fixture.health, client=fixture)
            self.assertEqual(report["status"], "degraded")
            if kind == "invalid":
                self.assertEqual(report["phases"][0]["summary"]["scored"], 0)
                self.assertEqual(report["phases"][0]["summary"]["failures"], {"invalid_response": 1})
            else:
                self.assertFalse(report["profileStable"])

    def test_profile_and_typed_outcomes_reject_tampering(self):
        from decision_runtime.contracts import Request
        fixture = Fixture(); health = fixture.health("GET", "/health")
        profile = profile_from_health(health)
        request = Request.from_dict(dataset()["cases"][0]["request"])
        result = fixture.engine.decide(request.to_dict())
        validate_result(result, request, profile)
        mutations = [lambda r: r.update(inputSha256="0" * 64), lambda r: r.update(value=0),
                     lambda r: r.update(inputTokens=True), lambda r: r.update(generatedTokens=False),
                     lambda r: r["distribution"][0].update(logit=float("nan"))]
        for mutation in mutations:
            changed = copy.deepcopy(result); mutation(changed)
            with self.assertRaises(ValueError): validate_result(changed, request, profile)
        health["profileSha256"] = "0" * 64
        with self.assertRaises(ValueError): profile_from_health(health)


if __name__ == "__main__": unittest.main()
