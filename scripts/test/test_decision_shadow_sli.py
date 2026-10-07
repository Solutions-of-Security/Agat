import copy
import math
import unittest

from decision_runtime.contracts import fingerprint
from scripts.lib.decision_shadow_sli import analyze


class CallerSliTest(unittest.TestCase):
    def setUp(self):
        self.profile = {"schemaVersion": "agat.decision.v1", "runtimeVersion": "fixture", "model": {"repository": "fixture"},
                        "policy": {"minProbability": .9, "minMargin": .1}, "calibration": {"temperature": 1.0}}

    def row(self, stage="stage", status="ok", caller_ms=400):
        reason = {"ok": "accepted", "abstain": "below_threshold", "error": "backend_error", "unavailable": "timeout"}[status]
        observation = {"mode": "shadow", "fallback": "primary", "status": status, "reason": reason}
        if status != "unavailable": observation["result"] = {**copy.deepcopy(self.profile), "id": stage, "inputSha256": "a"*64, "mode": "shadow", "status": status, "reason": reason, "durationMs": 2.0}
        if caller_ms is not None:
            observation["callerTiming"] = {"schemaVersion": "agat.decision.caller-timing.v1", "clock": "monotonic", "boundary": "local_http_call", "durationMs": caller_ms}
        return {"stageId": stage, "profileSha256": fingerprint(self.profile), "inputSha256": "a"*64,
                "callerTimeoutMs": 2000, "observation": observation}

    def trace(self, rows, run="run", truncated=False):
        return {"run": {"id": run}, "decisionObservations": rows, "truncated": truncated}

    def test_failed_fast_calls_do_not_improve_successful_timeliness_ratio(self):
        result = analyze([self.trace([self.row("a"), self.row("b", "abstain", 1500), self.row("c", "error", 10), self.row("d", "unavailable", 100)])], self.profile, 1000)
        self.assertEqual(result["counts"]["observedChecks"], 4)
        self.assertEqual(result["boundResultRatio"], .5)
        self.assertEqual(result["timelyBoundResultRatio"], {"lower": .25, "upper": .25})
        self.assertEqual(result["callerLatencyMs"], {"count": 4, "p50": 100.0, "p95": 1500.0, "max": 1500.0})
        self.assertEqual(result["scoringLatencyMs"]["count"], 3)
        self.assertFalse(result["sloAccepted"]); self.assertFalse(result["populationCoverageVerified"])

    def test_safe_replay_does_not_count_as_a_new_call_or_duplicate_old_latency(self):
        reused = self.row("replay", caller_ms=9999); reused["observation"]["reusedFromStageId"] = "original"
        skipped = self.row("skipped", "unavailable", None); skipped["observation"]["reason"] = "safe_replay_unavailable"
        result = analyze([self.trace([self.row("live"), reused, skipped])], self.profile, 1000)
        self.assertEqual(result["counts"]["observedChecks"], 1)
        self.assertEqual(result["counts"]["reusedChecks"], 1)
        self.assertEqual(result["counts"]["safeReplayUnavailable"], 1)
        self.assertEqual(result["callerLatencyMs"]["max"], 400)

    def test_legacy_successes_have_unknown_timeliness_instead_of_scoring_latency(self):
        result = analyze([self.trace([self.row("old", caller_ms=None), self.row("missing", "unavailable", None)])], self.profile, 1000)
        self.assertEqual(result["measurementStatus"], "insufficient_data")
        self.assertEqual(result["timelyBoundResultRatio"], {"lower": 0.0, "upper": .5})
        self.assertIsNone(result["callerLatencyMs"]["p95"])
        self.assertEqual(result["boundResultRatio"], .5)

    def test_empty_and_replay_only_inputs_keep_ratios_undefined(self):
        for rows in ([], [{**self.row(), "observation": {**self.row()["observation"], "reusedFromStageId": "old"}}]):
            result = analyze([self.trace(rows)], self.profile, 1000)
            self.assertEqual(result["measurementStatus"], "insufficient_data")
            self.assertIsNone(result["boundResultRatio"])
            self.assertEqual(result["timelyBoundResultRatio"], {"lower": None, "upper": None})

    def test_truncation_and_declared_or_actual_profile_drift_are_visible(self):
        changed = self.row(); changed["profileSha256"] = "b"*64
        result = analyze([self.trace([changed], truncated=True)], self.profile, 1000)
        self.assertEqual(result["boundResultRatio"], 0)
        self.assertEqual(set(result["dataGaps"]), {"profile_binding_mismatch", "truncated_trace"})
        for mutate in (lambda row: row["observation"]["result"]["model"].update(repository="changed"),
                       lambda row: row["observation"]["result"]["calibration"].update(temperature=True),
                       lambda row: row["observation"]["result"].update(id="foreign-stage")):
            row = self.row(); mutate(row)
            self.assertEqual(analyze([self.trace([row])], self.profile, 1000)["counts"]["profileBindingMismatches"], 1)

    def test_json_number_rendering_is_compatible_but_boolean_aliases_are_not(self):
        row = self.row(); row["observation"]["result"]["calibration"]["temperature"] = 1
        self.assertEqual(analyze([self.trace([row])], self.profile, 1000)["boundResultRatio"], 1)
        row["observation"]["result"]["calibration"]["temperature"] = True
        self.assertEqual(analyze([self.trace([row])], self.profile, 1000)["boundResultRatio"], 0)

    def test_duplicate_source_observations_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"): analyze([self.trace([self.row()]), self.trace([self.row()])], self.profile, 1000)
        self.assertEqual(analyze([self.trace([self.row()]), self.trace([self.row()], "other")], self.profile, 1000)["counts"]["observedChecks"], 2)

    def test_corrupt_timing_does_not_become_zero_or_missing(self):
        for value in (False, "0", -1, math.nan, math.inf, 86_400_001):
            with self.subTest(value=value), self.assertRaises(ValueError): analyze([self.trace([self.row(caller_ms=value)])], self.profile, 1000)
        for key, value in (("clock", "wall"), ("boundary", "scoring"), ("schemaVersion", "unknown"), ("extra", "private")):
            row = self.row(); row["observation"]["callerTiming"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): analyze([self.trace([row])], self.profile, 1000)

    def test_threshold_and_completeness_scalar_aliases_are_rejected(self):
        for value in (True, 0, -1, 1.0, 86_400_001):
            with self.subTest(value=value), self.assertRaises(ValueError): analyze([self.trace([self.row()])], self.profile, value)
        with self.assertRaises(ValueError): analyze([self.trace([self.row()], truncated=0)], self.profile, 1000)

    def test_lease_input_drift_and_late_success_do_not_pass_deadline_accounting(self):
        late = self.row("late", caller_ms=2001)
        changed = self.row("changed"); changed["observation"]["result"]["inputSha256"] = "b"*64
        result = analyze([self.trace([late, changed])], self.profile, 5000)
        self.assertEqual(result["counts"]["profileBindingMismatches"], 1)
        self.assertEqual(result["counts"]["lateBoundResults"], 1)
        self.assertEqual(result["withinCallerDeadlineRatio"], {"lower": 0.0, "upper": 0.0})
        self.assertEqual(result["timelyBoundResultRatio"], {"lower": 0.0, "upper": 0.0})

    def test_unassigned_checks_and_legacy_lease_bindings_are_explicit(self):
        disabled = self.row("disabled", "unavailable", None)
        disabled.update(profileSha256=None, inputSha256=None, callerTimeoutMs=None)
        disabled["observation"]["reason"] = "disabled"
        legacy = self.row("legacy"); del legacy["inputSha256"]; del legacy["callerTimeoutMs"]
        result = analyze([self.trace([disabled, legacy])], self.profile, 1000)
        self.assertEqual(result["counts"]["observedChecks"], 1); self.assertEqual(result["counts"]["unassignedChecks"], 1)
        self.assertIn("missing_lease_bindings", result["dataGaps"])
        self.assertFalse(result["leaseBindingCoverageVerified"])
        self.assertEqual(result["withinCallerDeadlineRatio"], {"lower": 0.0, "upper": 1.0})


if __name__ == "__main__": unittest.main()
