import copy
import unittest

from scripts.lib.decision_shadow_sli import analyze
from scripts.lib.decision_stage_inventory import census
from decision_runtime.contracts import fingerprint


class StageInventoryTest(unittest.TestCase):
    def setUp(self):
        from scripts.test.test_decision_shadow_sli import CallerSliTest as _Fixture
        fixture = _Fixture(); fixture.setUp()
        self.fixture = fixture; self.profile = fixture.profile

    def trace(self): return self.fixture.trace([self.fixture.row()])

    def add(self, trace, stage, status, assigned=True):
        row = {"stageId": stage, "stageStatus": status, "assigned": assigned, "observationRecorded": False,
               "profileSha256": fingerprint(self.profile) if assigned else None,
               "inputSha256": "a"*64 if assigned else None, "callerTimeoutMs": 2000 if assigned else None}
        trace["decisionStageInventory"]["stages"].append(row)
        return row

    def test_pending_and_terminal_missing_are_distinct_from_http_observations(self):
        trace = self.trace(); self.add(trace, "cancelled", "cancelled"); self.add(trace, "pending", "running")
        result = analyze([trace], self.profile, 1000); inventory = result["stageInventory"]
        self.assertEqual(result["counts"]["observedChecks"], 1); self.assertEqual(result["callerLatencyMs"]["count"], 1)
        self.assertEqual(result["boundResultRatio"], 1)
        self.assertEqual(inventory["counts"]["assignedStoredStages"], 3)
        self.assertEqual(inventory["counts"]["missingTerminalResults"], 1)
        self.assertEqual(inventory["counts"]["pendingAssignedStages"], 1)
        self.assertEqual(inventory["assignedStageBoundResultRatio"], {"lower": 1/3, "upper": 2/3})
        self.assertEqual(inventory["assignedStageTimelyResultRatio"], {"lower": 1/3, "upper": 2/3})
        self.assertTrue(inventory["storedStageCoverageVerified"])
        self.assertFalse(inventory["httpAttemptInventoryVerified"]); self.assertFalse(result["populationCoverageVerified"])
        self.assertIn("pending_assigned_stages", result["dataGaps"])

    def test_unassigned_stages_do_not_become_eligible_calls(self):
        trace = self.trace(); self.add(trace, "not-assigned", "queued", False)
        result = analyze([trace], self.profile, 1000)
        self.assertEqual(result["stageInventory"]["counts"]["unassignedStoredStages"], 1)
        self.assertEqual(result["stageInventory"]["counts"]["assignedStoredStages"], 1)
        self.assertEqual(result["stageInventory"]["assignedStageBoundResultRatio"], {"lower": 1.0, "upper": 1.0})

    def test_terminal_missing_only_is_a_settled_stage_failure_with_no_caller_sample(self):
        trace = self.fixture.trace([]); self.add(trace, "failed", "failed")
        result = analyze([trace], self.profile, 1000)
        self.assertEqual(result["stageInventory"]["assignedStageBoundResultRatio"], {"lower": 0.0, "upper": 0.0})
        self.assertEqual(result["callerLatencyMs"]["count"], 0); self.assertIsNone(result["boundResultRatio"])

    def test_safe_replay_preserves_provenance_without_new_assignment_denominator(self):
        row = self.fixture.row(); row["observation"]["reusedFromStageId"] = "original"
        result = analyze([self.fixture.trace([row])], self.profile, 1000)
        self.assertEqual(result["stageInventory"]["counts"]["replayedStoredStages"], 1)
        self.assertEqual(result["stageInventory"]["counts"]["assignedStoredStages"], 0)
        self.assertIsNone(result["stageInventory"]["assignedStageBoundResultRatio"]["lower"])

    def test_legacy_inventory_gap_is_visible(self):
        trace = self.trace(); del trace["decisionStageInventory"]
        result = analyze([trace], self.profile, 1000)
        self.assertEqual(result["boundResultRatio"], 1)
        self.assertIn("missing_stage_inventory", result["dataGaps"])
        self.assertFalse(result["stageInventory"]["storedStageCoverageVerified"])

    def test_omitted_or_fabricated_observation_markers_are_rejected(self):
        mutations = [lambda t: t["decisionStageInventory"].update(stages=[]),
                     lambda t: t["decisionStageInventory"]["stages"][0].update(observationRecorded=False),
                     lambda t: t["decisionObservations"].clear(),
                     lambda t: t["decisionObservations"][0].update(observation=None)]
        for mutate in mutations:
            trace = self.trace(); mutate(trace)
            with self.assertRaises(ValueError): census([trace], fingerprint(self.profile))

    def test_duplicate_pending_stages_across_inputs_and_inside_inventory_are_rejected(self):
        trace = self.fixture.trace([]); self.add(trace, "pending", "running")
        with self.assertRaisesRegex(ValueError, "Duplicate"): census([trace, copy.deepcopy(trace)], fingerprint(self.profile))
        trace["decisionStageInventory"]["stages"].append(copy.deepcopy(trace["decisionStageInventory"]["stages"][0]))
        with self.assertRaisesRegex(ValueError, "Duplicate"): census([trace], fingerprint(self.profile))

    def test_binding_and_assignment_aliases_cannot_enter_inventory(self):
        mutations = [("assigned", 1), ("observationRecorded", "true"), ("stageStatus", "unknown"),
                     ("callerTimeoutMs", True), ("callerTimeoutMs", 1000.0), ("callerTimeoutMs", 99),
                     ("inputSha256", "bad"), ("profileSha256", False), ("extra", "private")]
        for key, value in mutations:
            trace = self.trace(); trace["decisionStageInventory"]["stages"][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError): census([trace], fingerprint(self.profile))
        trace = self.trace(); trace["decisionStageInventory"]["stages"][0]["inputSha256"] = "b"*64
        with self.assertRaisesRegex(ValueError, "bindings differ"): census([trace], fingerprint(self.profile))

    def test_pending_profile_drift_is_kept_in_denominator_and_flagged(self):
        trace = self.trace(); row = self.add(trace, "pending", "running"); row["profileSha256"] = "b"*64
        result = analyze([trace], self.profile, 1000)
        self.assertEqual(result["stageInventory"]["counts"]["assignedStoredStages"], 2)
        self.assertIn("inventory_profile_binding_mismatch", result["dataGaps"])
        self.assertFalse(result["stageInventory"]["inventoryBindingsVerified"])


if __name__ == "__main__": unittest.main()
