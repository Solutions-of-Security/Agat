import copy
import unittest

from scripts.lib.decision_shadow_sli import analyze
from scripts.test import test_decision_shadow_sli as fixtures


class CallerInventoryTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.CallerSliTest(); self.fixture.setUp(); self.profile = self.fixture.profile

    def trace(self, variants):
        observations = []; stages = []; history = []; accounting = []
        for i, variant in enumerate(variants):
            stage = f"stage-{i}"; row = self.fixture.row(stage)
            assignment_id = f"00000000-0000-4000-8000-{i+1:012x}"
            pending = variant == "pending"; observation = None
            if variant in ("returned", "error", "untracked", "unnegotiated", "old_writer"):
                if variant == "error": row = self.fixture.row(stage, "unavailable", 100)
                observation = row["observation"]; observations.append(row)
            returned = ({key: copy.deepcopy(observation[key]) for key in ("callerTiming", "status", "reason")}
                        if variant in ("returned", "error") else None)
            intent = variant in ("returned", "error", "missing", "pending", "old_writer")
            outcome = {"returned": "returned", "error": "returned", "untracked": "data_gap", "old_writer": "data_gap",
                       "unnegotiated": "unnegotiated", "missing": "return_missing", "pending": "intent_pending", "not_started": "no_intent_recorded"}[variant]
            stages.append({"stageId": stage, "stageStatus": "running" if pending else "completed", "assigned": True,
                           "observationRecorded": observation is not None, **{k: row[k] for k in ("profileSha256", "inputSha256", "callerTimeoutMs")}})
            history.append({"stageId": stage, "coverage": "complete", "assignments": [{"assignmentId": assignment_id, "stageAttempt": 1,
                            **{k: row[k] for k in ("profileSha256", "inputSha256", "callerTimeoutMs")}, "observation": observation,
                            "outcome": "recorded" if observation is not None else "pending" if pending else "ended_without_observation"}]})
            accounting.append({"stageId": stage, "coverage": "legacy_gap" if outcome == "data_gap" else "complete", "assignments": [{
                "assignmentId": assignment_id, "stageAttempt": 1, "negotiated": variant != "unnegotiated", "intent": intent, "returned": returned, "outcome": outcome}]})
        return {"run": {"id": "run"}, "truncated": False, "decisionObservations": observations,
                "decisionStageInventory": {"schemaVersion": "agat.decision.shadow-stage-inventory.v1", "scope": "stored_shadow_stages", "stages": stages},
                "decisionAssignmentHistory": {"schemaVersion": "agat.decision.shadow-assignment-inventory.v1", "scope": "coordinator_shadow_assignments", "stages": history},
                "decisionCallerAccounting": {"schemaVersion": "agat.decision.caller-inventory.v1", "scope": "caller_operation_intents", "stages": accounting}}

    def test_lost_returns_expand_denominator_without_fabricated_durations(self):
        summary = analyze([self.trace(["returned", "error", "missing", "not_started", "unnegotiated"])], self.profile, 1000)["callerAccounting"]
        counts = summary["counts"]
        self.assertEqual((counts["assignments"], counts["intents"], counts["returned"], counts["unknownReturns"]), (5, 3, 2, 1))
        self.assertEqual(summary["withinCallerDeadlineRatio"], {"lower": 1/3, "upper": 2/3})
        self.assertEqual(summary["callerLatencyMs"]["count"], 2)
        self.assertFalse(summary["callerIntentInventoryVerified"]); self.assertFalse(summary["httpAttemptInventoryVerified"])

    def test_known_intents_and_unknown_returns_have_distinct_coverage(self):
        summary = analyze([self.trace(["returned", "pending", "missing", "not_started"])], self.profile, 1000)["callerAccounting"]
        self.assertTrue(summary["callerIntentInventoryVerified"]); self.assertFalse(summary["callerReturnCoverageVerified"])
        self.assertEqual(summary["counts"]["pendingIntents"], 1); self.assertEqual(summary["counts"]["missingReturns"], 1)
        self.assertEqual(summary["timelyBoundResultRatio"], {"lower": 1/3, "upper": 1.0})
        self.assertFalse(summary["populationCoverageVerified"])

    def test_old_writer_untracked_callbacks_and_legacy_traces_never_claim_complete_coverage(self):
        trace = self.trace(["untracked", "old_writer"])
        summary = analyze([trace], self.profile, 1000)["callerAccounting"]
        self.assertEqual(summary["counts"]["dataGapAssignments"], 2)
        self.assertEqual(summary["counts"]["unknownReturns"], 1)
        self.assertFalse(summary["callerIntentInventoryVerified"])
        legacy = copy.deepcopy(trace); del legacy["decisionCallerAccounting"]
        self.assertEqual(analyze([legacy], self.profile, 1000)["callerAccounting"]["counts"]["tracesMissingAccounting"], 1)
        complete = self.trace(["returned"]); complete["truncated"] = True
        self.assertFalse(analyze([complete], self.profile, 1000)["callerAccounting"]["callerIntentInventoryVerified"])

    def test_receipt_aliases_misbindings_and_contradictory_outcomes_are_rejected(self):
        mutations = [lambda t: t["decisionCallerAccounting"].update(scope="physical_http_calls"),
                     lambda t: t["decisionCallerAccounting"]["stages"][0].update(coverage=["complete"]),
                     lambda t: t["decisionCallerAccounting"]["stages"][0]["assignments"][0].update(intent=1),
                     lambda t: t["decisionCallerAccounting"]["stages"][0]["assignments"][0].update(stageAttempt=True),
                     lambda t: t["decisionCallerAccounting"]["stages"][0]["assignments"][0].update(outcome="intent_pending"),
                     lambda t: t["decisionCallerAccounting"]["stages"][0]["assignments"][0].update(negotiated=False),
                     lambda t: t["decisionCallerAccounting"]["stages"][0]["assignments"][0]["returned"]["callerTiming"].update(durationMs=False),
                     lambda t: t["decisionCallerAccounting"]["stages"][0]["assignments"][0]["returned"]["callerTiming"].update(durationMs=900),
                     lambda t: t["decisionCallerAccounting"]["stages"][0]["assignments"].clear(),
                     lambda t: t["decisionCallerAccounting"]["stages"].clear()]
        for mutate in mutations:
            trace = self.trace(["returned"]); mutate(trace)
            with self.subTest(mutation=mutate), self.assertRaises(ValueError): analyze([trace], self.profile, 1000)

    def test_assignment_snapshot_omissions_and_duplicate_ids_are_rejected(self):
        trace = self.trace(["returned", "missing"])
        trace["decisionCallerAccounting"]["stages"][1]["assignments"][0]["assignmentId"] = trace["decisionCallerAccounting"]["stages"][0]["assignments"][0]["assignmentId"]
        with self.assertRaises(ValueError): analyze([trace], self.profile, 1000)
        trace = self.trace(["returned"]); trace["decisionAssignmentHistory"]["stages"][0]["assignments"][0]["stageAttempt"] = 2
        with self.assertRaisesRegex(ValueError, "attempt binding"): analyze([trace], self.profile, 1000)
        trace = self.trace(["returned"]); trace["decisionAssignmentHistory"]["stages"][0]["assignments"][0]["observation"]["result"]["inputSha256"] = "b"*64
        summary = analyze([trace], self.profile, 1000)["callerAccounting"]
        self.assertEqual(summary["withinCallerDeadlineRatio"], {"lower": 0.0, "upper": 0.0})
        self.assertEqual(summary["counts"]["profileBindingMismatches"], 1)
