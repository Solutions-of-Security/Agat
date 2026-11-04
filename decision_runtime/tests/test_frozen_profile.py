import copy
import unittest

from decision_runtime.artifacts import sealed, verify_seal
from decision_runtime.calibration_workflow import PROFILED_PLAN_SCHEMA, fit_calibration, freeze_plan, validate_plan
from decision_runtime.contracts import Policy, fingerprint
from decision_runtime.engine import DecisionEngine
from decision_runtime.evaluation import evaluate
from decision_runtime.qualification import qualify
from decision_runtime.tests.test_calibration import FixtureBackend, criteria, dataset, pipeline


class FrozenProfileTest(unittest.TestCase):
    def test_complete_profile_is_copied_and_cannot_change_with_the_callers_object(self):
        data, policy, backend = dataset(), Policy(), FixtureBackend()
        profile = DecisionEngine(backend, policy).profile()
        profile["model"] = {**profile["model"], "inferenceExecution": {"deadlineMs": 2000}}
        plan = freeze_plan(data, policy, criteria(), FixtureBackend.identity, execution_profile=profile)
        verify_seal(plan, PROFILED_PLAN_SCHEMA)
        profile["model"]["inferenceExecution"]["deadlineMs"] = 100
        self.assertEqual(plan["executionProfile"]["model"]["inferenceExecution"]["deadlineMs"], 2000)
        validate_plan(plan, data)

    def test_model_policy_fitted_or_unknown_profile_fields_are_rejected(self):
        for mutate in (lambda p: p["model"].update(revision="other"),
                       lambda p: p["policy"].update(minProbability=.1),
                       lambda p: p["calibration"].update(status="fitted"),
                       lambda p: p["calibration"].update(temperature=True),
                       lambda p: p.update(unexpected=True),
                       lambda p: p.update(inputFingerprintVersions=["unknown"]),
                       lambda p: p.update(inputFingerprintVersions=[{}])):
            profile = copy.deepcopy(DecisionEngine(FixtureBackend()).profile()); mutate(profile)
            with self.assertRaises(ValueError):
                freeze_plan(dataset(), Policy(), criteria(), FixtureBackend.identity, execution_profile=profile)

    def test_fit_rejects_pre_freeze_calibration_even_when_dataset_and_weights_match(self):
        data, plan, scores, _, _ = pipeline()
        for start in ("2000-01-01T00:00:00+00:00", "2026-09-26T00:00:00", "invalid", None):
            changed = copy.deepcopy(scores); changed["startedAt"] = start
            with self.subTest(start=start), self.assertRaisesRegex(ValueError, "Calibration must be scored after"):
                fit_calibration(data, changed, plan)

    def test_fit_rejects_missing_naive_future_or_reversed_completion(self):
        data, plan, scores, _, _ = pipeline()
        for end in (None, "2026-09-26T00:00:00", "9999-01-01T00:00:00+00:00", plan["createdAt"]):
            changed = copy.deepcopy(scores); changed["createdAt"] = end
            with self.subTest(end=end), self.assertRaisesRegex(ValueError, "completion timestamp"):
                fit_calibration(data, changed, plan)

    def test_fit_rejects_consistently_changed_execution_metadata_and_per_row_version(self):
        data, plan, scores, _, _ = pipeline()
        for key, value in (("maxInputTokens", 4096), ("allocatorCacheLimitBytes", 0),
                           ("allocatorWiredLimitBytes", 4294967296),
                           ("implementationSha256", "f" * 64), ("inferenceExecution", {"deadlineMs": 1})):
            changed = copy.deepcopy(scores)
            changed["model"][key] = value
            for row in changed["cases"]: row["result"]["model"][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "execution profile"):
                fit_calibration(data, changed, plan)
        for top in (True, False):
            changed = copy.deepcopy(scores)
            if top: changed["runtimeVersion"] = "changed"
            else: changed["cases"][0]["result"]["runtimeVersion"] = "changed"
            with self.assertRaisesRegex(ValueError, "execution profile"):
                fit_calibration(data, changed, plan)

    def test_resealed_plan_cannot_hide_changed_profile_digest(self):
        data, plan, _, _, _ = pipeline()
        body = {k: copy.deepcopy(v) for k, v in plan.items() if k != "sha256"}
        body["executionProfile"]["runtimeVersion"] = "changed"
        with self.assertRaisesRegex(ValueError, "profile checksum"):
            validate_plan(sealed(body), data)

    def test_consistent_policy_change_and_holdout_runtime_change_are_rejected(self):
        data, plan, scores, artifact, holdout = pipeline()
        changed = copy.deepcopy(scores)
        changed["policy"] = Policy("different", .1, .01).to_dict()
        for row in changed["cases"]:
            row["result"]["policy"] = {**changed["policy"], "sha256": fingerprint(changed["policy"])}
        with self.assertRaisesRegex(ValueError, "Scored policy"):
            fit_calibration(data, changed, plan)
        changed = copy.deepcopy(holdout)
        changed["cases"][0]["reversedResult"]["runtimeVersion"] = "other-runtime"
        with self.assertRaisesRegex(ValueError, "execution profile"):
            qualify(data, changed, artifact, plan)

    def test_legacy_plan_can_be_replayed_but_cannot_pass_qualification(self):
        # Expert identities here are explicit unit fixtures, never real evidence.
        data, policy, backend = dataset(expert=True), Policy("test", .5, .1), FixtureBackend()
        plan = freeze_plan(data, policy, criteria(), backend.identity)
        scores = evaluate(DecisionEngine(backend, policy), data, "calibration")
        artifact = fit_calibration(data, scores, plan)
        holdout = evaluate(DecisionEngine(backend, policy), data, "holdout", reverse_options=True)
        result = qualify(data, holdout, artifact, plan)
        self.assertEqual(result["status"], "not_qualified")
        self.assertEqual([g["name"] for g in result["gates"] if not g["passed"]], ["execution_profile_frozen"])
        self.assertFalse(result["routingEnabled"])


if __name__ == "__main__": unittest.main()
