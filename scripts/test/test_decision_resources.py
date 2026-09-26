import copy
import itertools
import json
import types
import unittest
from unittest.mock import patch

from decision_runtime.artifacts import sealed, verify_seal
from decision_runtime.engine import Scores
from decision_runtime.tests.test_decisions import Backend
from scripts.lib.decision_resources import SCHEMA, process_usage, profile_resources, summarize_requests
from scripts.test.test_decision_baselines import dataset


class Memory:
    def __init__(self, _backend): self.calls = 0
    def sample(self):
        self.calls += 1
        return {"activeBytes": 100, "cacheBytes": self.calls * 10, "peakActiveBytes": 200,
                "process": {"peakRssBytes": 500}}
    def device(self): return {"device_name": "test-only"}


class RecordingBackend(Backend):
    def __init__(self):
        super().__init__([3, 0])
        self.identity = dict(self.identity)
        self.requests = []
    def score(self, request):
        self.requests.append(request.to_dict())
        return super().score(request)


class ResourceTest(unittest.TestCase):
    def profile(self, backend=None, **kwargs):
        backend = backend or RecordingBackend()
        return profile_resources(dataset(), lambda: backend, memory_factory=Memory, **kwargs)

    def test_load_first_warmup_measurements_and_memory_stay_distinct_without_gold(self):
        backend = RecordingBackend()
        report = self.profile(backend, rounds=4, warmup=2)
        verify_seal(report, SCHEMA)
        self.assertEqual(report["status"], "observed")
        self.assertEqual(backend.calls, 7)
        self.assertEqual(report["firstRequest"]["phase"], "first_request")
        self.assertEqual(len(report["warmup"]), 2)
        self.assertEqual((report["summary"]["attempts"], report["summary"]["scored"]), (4, 4))
        self.assertEqual(len(report["roundSummaries"]), 4)
        self.assertEqual(report["memory"]["peakActiveBytes"], 200)
        self.assertEqual(report["memory"]["maxObservedActivePlusCacheBytes"], 170)
        self.assertEqual(report["memory"]["samples"][0]["process"]["peakRssBytes"], 500)
        self.assertEqual(report["repeatDecisionChanges"], [])
        self.assertFalse(report["qualifiedForRouting"])
        self.assertNotIn("GOLD NEVER SENT", json.dumps(backend.requests))
        self.assertNotIn("expectedOptionId", json.dumps(backend.requests))
        self.assertNotIn("Input only", json.dumps(report))

    def test_unapproved_splits_and_excessive_plans_fail_before_loading_weights(self):
        loads = []
        def factory(): loads.append(True); return RecordingBackend()
        data = dataset(); data.pop("sha256"); data["cases"][0]["split"] = "holdout"
        with self.assertRaises(ValueError): profile_resources(sealed(data), factory, memory_factory=Memory)
        for config in ({"rounds": 100}, {"warmup": -1}, {"time_budget_s": 301}, {"rounds": True},
                       {"cache_limit_mib": -1}, {"cache_limit_mib": True}, {"cache_limit_mib": 4097}):
            with self.assertRaises(ValueError): profile_resources(dataset(), factory, memory_factory=Memory, **config)
        self.assertEqual(loads, [])

    def test_allocator_setting_is_explicit_and_zero_is_not_treated_as_missing(self):
        calls = []
        class Limited(Memory):
            def set_cache_limit(self, limit): calls.append(limit); return 2**30
        for limit in (0, 512, None):
            report = profile_resources(dataset(), RecordingBackend, rounds=1, warmup=0,
                                       memory_factory=Limited, cache_limit_mib=limit)
            self.assertEqual(report["plan"]["allocatorCacheLimitBytes"], None if limit is None else limit * 2**20)
            self.assertEqual(report["plan"]["cacheLimitAppliedAfterLoad"], limit is not None)
        self.assertEqual(calls, [0, 512 * 2**20])
        backend = RecordingBackend(); backend.identity["allocatorCacheLimitBytes"] = 128 * 2**20
        report = profile_resources(dataset(), lambda: backend, rounds=1, warmup=0, memory_factory=Memory)
        self.assertEqual(report["plan"]["allocatorCacheControl"], "runtime_profile")
        self.assertEqual(report["plan"]["allocatorCacheLimitBytes"], 128 * 2**20)

    def test_failures_do_not_make_successful_latency_percentiles_look_faster(self):
        rows = [{"status": "ok", "reason": "accepted", "wallMs": 500, "runtimeMs": 490, "inputTokens": 20},
                {"status": "error", "reason": "backend_error", "wallMs": 1}]
        summary = summarize_requests(rows)
        self.assertEqual(summary["scoredWallMs"]["p95"], 500)
        self.assertEqual(summary["failedWallMs"]["p95"], 1)
        class Broken(RecordingBackend):
            def score(self, _request): raise RuntimeError("SOURCE TEXT SECRET")
        report = self.profile(Broken(), rounds=1, warmup=0)
        self.assertEqual(report["status"], "degraded")
        self.assertEqual(report["summary"]["scored"], 0)
        self.assertIsNone(report["summary"]["scoredWallMs"]["p95"])
        self.assertNotIn("SECRET", json.dumps(report))

    def test_changing_results_and_profiles_are_recorded_as_degraded(self):
        class Drifting(RecordingBackend):
            def score(self, request):
                super().score(request)
                return Scores([3, 0] if self.calls % 2 else [0, 3], 20)
        report = self.profile(Drifting(), rounds=2, warmup=0)
        self.assertEqual(report["status"], "degraded")
        self.assertEqual(report["repeatDecisionChanges"], [dataset()["cases"][0]["request"]["id"]])
        class Changing(RecordingBackend):
            def score(self, request):
                self.identity["revision"] = "changed"
                return super().score(request)
        report = self.profile(Changing(), rounds=1, warmup=0)
        self.assertFalse(report["profileStable"])
        self.assertEqual(report["profile"]["model"]["revision"], "fixture")
        self.assertEqual(report["status"], "degraded")

    def test_time_budget_stops_new_requests_and_does_not_fabricate_first_latency(self):
        backend = RecordingBackend()
        with patch("scripts.lib.decision_resources.time.perf_counter", side_effect=itertools.count()):
            report = self.profile(backend, rounds=4, warmup=2, time_budget_s=1)
        self.assertEqual(backend.calls, 0)
        self.assertIsNone(report["firstRequest"])
        self.assertEqual(report["stoppedReason"], "time_budget")
        self.assertEqual(report["summary"]["attempts"], 0)
        self.assertEqual(report["status"], "degraded")

    def test_rss_units_are_explicit_and_unknown_platform_is_not_zero(self):
        usage = types.SimpleNamespace(ru_maxrss=100, ru_utime=1.2, ru_stime=0.5)
        for system, expected in [("Darwin", 100), ("Linux", 102400), ("Unknown", None)]:
            with patch("scripts.lib.decision_resources.resource.getrusage", return_value=usage), \
                 patch("scripts.lib.decision_resources.platform.system", return_value=system):
                self.assertEqual(process_usage()["peakRssBytes"], expected)


if __name__ == "__main__": unittest.main()
