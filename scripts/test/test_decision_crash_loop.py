import copy
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from decision_runtime.contracts import Request, fingerprint, probabilities
from scripts.lib.decision_crash_loop import failure_state, retirement_events, validate_history
from scripts.lib.decision_monitoring import summarize_snapshot
from scripts.test.test_decision_monitoring import INSTANCE, fixture as metric_fixture

ROOT = Path(__file__).resolve().parents[2]
EPOCH = 1791321000


def diagnostic(profile, request):
    logits = [4.0, 0.0, -4.0]
    ps = probabilities(logits, 3, 1.0)
    return {"httpStatus": 200, "wallMs": 200, "completeResponse": True,
            "result": {**profile, "id": request.id, "mode": "shadow", "inputSha256": request.input_sha256,
                       "status": "ok", "reason": "accepted", "selectedOptionId": "supported", "value": "supported",
                       "selectedProbability": ps[0], "margin": ps[0]-ps[1], "inputTokens": 117, "generatedTokens": 0,
                       "durationMs": 199, "distribution": [{"id": option.id, "probability": p, "logit": logit}
                           for option,p,logit in zip(request.options, ps, logits)]}}


def metrics(start, count=0):
    raw = metric_fixture()
    for row in raw["data"]["result"]:
        name, labels = row["metric"]["__name__"], row["metric"]
        if name == "agat_decision_server_start_time_seconds": row["value"][1] = str(start)
        if (name == "agat_decision_requests_total" and labels["outcome"] == "ok") or (
                name in {"agat_decision_request_duration_seconds_bucket", "agat_decision_request_duration_seconds_count"}
                and labels["class"] == "computed"):
            row["value"][1] = str(count)
    target = {"scrapeUrl": f"http://{INSTANCE}/metrics", "health": "up", "lastError": "",
              "lastScrape": datetime.fromtimestamp(start+5, timezone.utc).isoformat()}
    return {"raw": raw, "summary": summarize_snapshot(raw, INSTANCE), "capturedEpoch": start+6,
            "targets": {"status": "success", "data": {"activeTargets": [target]}}}


def history_fixture():
    profile = json.loads((ROOT / "docs/qualification/local-decisions/performance/profiles/runtime-0.12.3-wired-4096.json").read_text())
    request = Request.from_dict(json.loads((ROOT / "docs/qualification/local-decisions/request.example.json").read_text()))
    runs, failures, events = [], [], []
    for generation in range(1, 5):
        pid = generation*100
        children = [{"pid": pid+1, "parentPid": pid, "role": "inference"}, {"pid": pid+2, "parentPid": pid, "role": "resource_tracker"}]
        birth = EPOCH+30*(generation-1)
        run = {"generation": generation, "pid": pid, "children": children, "service": {"pid": pid, "runs": generation},
               "processStarts": {str(pid): birth, str(pid+1): birth+1, str(pid+2): birth+1},
               "profile": profile, "profileSha256": fingerprint(profile), "readyMetrics": metrics(birth+2),
               "scoredMetrics": metrics(birth+2, 1), "decision": diagnostic(profile, request)}
        runs.append(run)
        if generation == 4: continue
        pending = {"state": "pending", "labels": {"alertname": "AgatDecisionEndpointUnavailable", "job": "agat-decision", "instance": INSTANCE}}
        failures.append({"generation": generation, "signaledChildPid": pid+1, "ownedProcessesGone": True,
                         "exitState": {"runs": generation, "last exit code": 75}, "endpointPending": True, "alertCleared": True,
                         "downTargets": {"status": "success", "data": {"activeTargets": [{"scrapeUrl": f"http://{INSTANCE}/metrics", "health": "down", "lastError": "connection refused"}]}},
                         "pendingAlerts": {"status": "success", "data": {"alerts": [pending]}},
                         "clearedAlerts": {"status": "success", "data": {"alerts": []}},
                         "upHistory": {"status": "success", "data": {"resultType": "matrix", "result": [{"metric": {"__name__": "up", "job": "agat-decision", "instance": INSTANCE},
                                       "values": [[birth+10, "1"], [birth+15, "0"], [birth+35, "1"]]}]}}})
        events.append({"schemaVersion": "agat.decision.retirement.v1", "eventName": "decision.backend_retired",
                       "runtimeVersion": profile["runtimeVersion"], "profileSha256": fingerprint(profile),
                       "exitCode": 75, "childPid": pid+1, "childExitCode": -9, "reason": "backend_unavailable"})
    quiet = {"elapsedSeconds": 30.5, "samples": [{"elapsedSeconds": seconds, "service": runs[-1]["service"],
                                               "children": runs[-1]["children"], "metrics": runs[-1]["scoredMetrics"]}
                                              for seconds in (0.1, 3, 6, 9, 12, 15, 18, 21, 24, 27, 30)]}
    return {"runs": runs, "failures": failures, "quiet": quiet, "events": events, "profile": profile, "instance": INSTANCE, "request": request}


class CrashLoopHistoryTest(unittest.TestCase):
    def setUp(self): self.data = history_fixture()

    def check(self): return validate_history(**self.data)

    def test_three_native_retirements_four_starts_and_stable_window(self):
        self.assertEqual(self.check()["startIntervalsSeconds"], [30, 30, 30])
        self.assertEqual(self.check()["ownedRuntimePids"], 12)

    def test_previous_exit_75_does_not_accept_a_live_generation(self):
        self.assertFalse(failure_state({"runs": 2, "pid": 200, "last exit code": 75}, 2))
        self.assertTrue(failure_state({"runs": 2, "last exit code": 75}, 2))
        for state in (None, {"runs": 3, "last exit code": 75}, {"runs": 2, "last exit code": 78}, {"runs": 2.0, "last exit code": 75}):
            with self.subTest(state=state), self.assertRaises(ValueError): failure_state(state, 2)

    def test_failure_budget_and_generation_skips_are_rejected(self):
        for change in (lambda d: d["failures"].pop(), lambda d: d["runs"][2]["service"].update(runs=4),
                       lambda d: d["runs"][1].update(generation=2.0)):
            self.data = history_fixture(); change(self.data)
            with self.assertRaises(ValueError): self.check()

    def test_start_spacing_uses_process_births_and_allows_only_resolution_tolerance(self):
        self.data["runs"][1]["processStarts"]["200"] = EPOCH+29
        self.assertEqual(self.check()["startIntervalsSeconds"][0], 29)
        self.data["runs"][1]["processStarts"]["200"] = EPOCH+28
        with self.assertRaisesRegex(ValueError, "throttle"): self.check()

    def test_reused_pids_foreign_parent_and_incomplete_inventory_are_rejected(self):
        for change in (lambda d: d["runs"][1]["children"][0].update(pid=101),
                       lambda d: d["runs"][1]["children"][0].update(parentPid=300),
                       lambda d: d["runs"][1]["children"].pop()):
            self.data = history_fixture(); change(self.data)
            with self.assertRaises(ValueError): self.check()

    def test_rehashed_metric_summary_cannot_hide_raw_series_or_old_server_start(self):
        for change in (lambda d: d["runs"][1]["readyMetrics"]["summary"].update(serverStartTime=EPOCH+300),
                       lambda d: d["runs"][1].update(readyMetrics=copy.deepcopy(d["runs"][0]["readyMetrics"])),
                       lambda d: d["runs"][1]["readyMetrics"]["raw"]["data"]["result"].pop()):
            self.data = history_fixture(); change(self.data)
            with self.assertRaises((ValueError, RuntimeError)): self.check()

    def test_changed_response_binding_distribution_tokens_and_profile_fail(self):
        for field,value in (("inputSha256", "0"*64), ("generatedTokens", 1), ("inputTokens", 118), ("value", True), ("runtimeVersion", "0.12.2")):
            self.data = history_fixture(); self.data["runs"][2]["decision"]["result"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): self.check()
        self.data = history_fixture(); self.data["runs"][2]["decision"]["result"]["distribution"][0]["probability"] = 1
        with self.assertRaises(ValueError): self.check()

    def test_retirement_must_match_each_signaled_child_profile_and_exit(self):
        for field,value in (("childPid", 101), ("childExitCode", -15), ("exitCode", 0), ("profileSha256", "0"*64), ("reason", "inference_timeout")):
            self.data = history_fixture(); self.data["events"][1][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): self.check()

    def test_endpoint_pass_flags_cannot_replace_native_down_and_alert_bodies(self):
        for change in (lambda d: d["failures"][1]["pendingAlerts"]["data"].update(alerts=[]),
                       lambda d: d["failures"][1]["downTargets"]["data"]["activeTargets"][0].update(lastError=""),
                       lambda d: d["failures"][1]["clearedAlerts"].update(warnings=["partial"]),
                       lambda d: d["failures"][1]["upHistory"]["data"]["result"][0]["values"].__setitem__(1, [EPOCH+45,"1"])):
            self.data = history_fixture(); change(self.data)
            with self.assertRaises(ValueError): self.check()

    def test_thirty_second_stable_window_requires_no_gaps_or_new_generations(self):
        for change in (lambda d: d["quiet"].update(elapsedSeconds=29), lambda d: d["quiet"]["samples"].pop(),
                       lambda d: d["quiet"]["samples"].__delitem__(slice(1,3)),
                       lambda d: d["quiet"]["samples"][1].update(service={"pid": 500, "runs": 5})):
            self.data = history_fixture(); change(self.data)
            with self.assertRaises(ValueError): self.check()

    def test_retirement_log_preserves_order_and_rejects_malformed_json(self):
        raw = "other startup text\n" + "\n".join(json.dumps(event) for event in self.data["events"])
        self.assertEqual(retirement_events(raw), self.data["events"])
        with self.assertRaises(ValueError): retirement_events('{"eventName": invalid}')


if __name__ == "__main__": unittest.main()
