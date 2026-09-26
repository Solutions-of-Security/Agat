import copy
import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from decision_runtime.annotations import finalize_reviews, prepare_review
from decision_runtime.artifacts import sealed, verify_seal, write_new
from decision_runtime.calibration import Calibration, fit_temperature
from decision_runtime.calibration_workflow import fit_calibration, freeze_plan
from decision_runtime.contracts import Policy, Request, fingerprint
from decision_runtime.engine import DecisionEngine, Scores
from decision_runtime.evaluation import evaluate, validate_dataset
from decision_runtime.qualification import binomial_upper, expert_reviewed, group_risk, qualify
from decision_runtime.tests.test_decisions import request


class FixtureBackend:
    identity = {"repository": "unit-fixture", "revision": "unit-fixture",
                "artifactSha256": "a" * 64, "tokenizerSha256": "b" * 64}

    def __init__(self):
        self.calls = 0

    def score(self, request):
        self.calls += 1
        winner = "no" if "wrong" in request.state else "yes"
        return Scores([4.0 if o.id == winner else 0.0 for o in request.options], 30)


def criteria():
    return {"schemaVersion": "agat.decision.criteria.v1", "id": "unit-fixture",
            "minAccuracy": 0.7, "minCoverage": 0.5, "maxGroupRisk": 0.8, "confidence": 0.95,
            "minAcceptedGroups": 2, "minAcceptedGroupsPerFamily": 2}


def reviewed_case(case):
    case["labelSource"] = "expert-reviewed"
    case["provenance"]["kind"] = "real"
    case["review"] = {"status": "agreed", "records": [
        {"reviewerId": reviewer, "reviewedAt": "2026-09-26T00:00:00+00:00", "id": case["id"],
         "inputSha256": Request.from_dict(case["request"]).input_sha256,
         "expectedOptionId": case["expectedOptionId"], "rationale": "Unit test only; not real expert evidence."}
        for reviewer in ("unit-reviewer-a", "unit-reviewer-b")]}
    return case


def dataset(*, expert=False, holdout_count=8):
    cases = []
    for split, count in (("calibration", 8), ("holdout", holdout_count)):
        for i in range(count):
            raw = request()
            raw["id"] = f"{split}-{i}"
            raw["state"] = f"{split} source {i}: {'wrong' if split == 'calibration' and i % 4 == 3 else 'correct'}"
            case = {"id": raw["id"], "family": "evidence", "groupId": f"group-{split}-{i}",
                    "labelSource": "synthetic-authored", "split": split, "expectedOptionId": "yes",
                    "provenance": {"kind": "synthetic", "sourceId": raw["id"], "reference": "unit fixture"},
                    "request": raw}
            cases.append(reviewed_case(case) if expert else case)
    return {"schemaVersion": "agat.decision.dataset.v1", "id": "unit-dataset", "cases": cases}


def pipeline(data=None, policy=None):
    data = data or dataset()
    policy = policy or Policy("test", 0.5, 0.1)
    backend = FixtureBackend()
    plan = freeze_plan(data, policy, criteria(), backend.identity,
                       execution_profile=DecisionEngine(backend, policy).profile())
    scored = evaluate(DecisionEngine(backend, policy), data, "calibration")
    artifact = fit_calibration(data, scored, plan)
    heldout = evaluate(DecisionEngine(backend, policy), data, "holdout", reverse_options=True)
    return data, plan, scored, artifact, heldout


class TemperatureTest(unittest.TestCase):
    def test_fits_known_binary_optimum_and_lowers_nll(self):
        # Eight of ten have class 0: sigmoid(4 / T) = 0.8 at the optimum.
        fitted = fit_temperature([([4.0, 0.0], int(i >= 8)) for i in range(10)])
        self.assertAlmostEqual(fitted["temperature"], 4 / math.log(4), places=8)
        self.assertLess(fitted["nllAfter"], fitted["nllBefore"])

    def test_flat_logits_keep_identity_temperature_and_extremes_stay_finite(self):
        self.assertEqual(fit_temperature([([0.0, 0.0], 0)])["temperature"], 1.0)
        result = fit_temperature([([10000.0, -10000.0], 1)])
        self.assertTrue(math.isfinite(result["nllAfter"]))
        self.assertTrue(result["atBound"])

    def test_invalid_logits_targets_and_empty_set_are_rejected(self):
        for examples in ([], [([0.0, float("nan")], 0)], [([1.0], 0)], [([1.0, 2.0], 2)]):
            with self.subTest(examples=examples), self.assertRaises(ValueError):
                fit_temperature(examples)

    def test_fitted_engine_changes_probability_and_abstention_not_label(self):
        data, _, _, artifact, _ = pipeline()
        raw = data["cases"][0]["request"]
        backend = FixtureBackend()
        before = DecisionEngine(backend).decide(raw)
        after = DecisionEngine(backend, calibration=Calibration(artifact)).decide(raw)
        self.assertEqual(before["selectedOptionId"], after["selectedOptionId"])
        self.assertGreater(before["selectedProbability"], after["selectedProbability"])
        self.assertEqual(before["status"], "ok")
        self.assertEqual(after["status"], "abstain")
        self.assertEqual(after["calibration"]["status"], "fitted")
        self.assertFalse(after["calibration"]["qualifiedForRouting"])

    def test_model_binding_and_request_scope_fail_closed_before_inference(self):
        data, _, _, artifact, _ = pipeline()
        backend = FixtureBackend()
        backend.identity = {**backend.identity, "revision": "changed"}
        with self.assertRaisesRegex(ValueError, "different model"):
            DecisionEngine(backend, calibration=Calibration(artifact))
        backend = FixtureBackend()
        raw = copy.deepcopy(data["cases"][0]["request"])
        raw["question"] = "A new question"
        result = DecisionEngine(backend, calibration=Calibration(artifact)).decide(raw)
        self.assertEqual(result["reason"], "calibration_out_of_scope")
        self.assertEqual(backend.calls, 0)

    def test_tampered_temperature_is_detected_and_option_order_is_in_scope(self):
        data, _, _, artifact, _ = pipeline()
        changed = copy.deepcopy(artifact)
        changed["temperature"] *= 2
        with self.assertRaisesRegex(ValueError, "checksum"):
            Calibration(changed)
        raw = copy.deepcopy(data["cases"][0]["request"])
        raw["options"].reverse()
        result = DecisionEngine(FixtureBackend(), calibration=Calibration(artifact)).decide(raw)
        self.assertNotEqual(result["status"], "error")


class FrozenExperimentTest(unittest.TestCase):
    def test_fit_rejects_holdout_wrong_model_changed_gold_and_missing_cases(self):
        data, plan, scores, artifact, holdout = pipeline()
        for mutation in ("holdout", "model", "gold", "missing"):
            wrong = copy.deepcopy(scores)
            if mutation == "holdout":
                wrong = holdout
            elif mutation == "model":
                wrong["model"] = {**wrong["model"], "revision": "different"}
            elif mutation == "gold":
                wrong["cases"][0]["expectedOptionId"] = "no"
            else:
                wrong["cases"].pop()
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                fit_calibration(data, wrong, plan)

    def test_dataset_or_policy_changes_require_new_plan_and_calibration(self):
        data, plan, scores, artifact, heldout = pipeline()
        data["cases"][-1]["expectedOptionId"] = "no"
        with self.assertRaisesRegex(ValueError, "Dataset differs"):
            fit_calibration(data, scores, plan)
        data, plan, scores, artifact, heldout = pipeline()
        body = {k: v for k, v in plan.items() if k != "sha256"}
        body["policy"] = Policy("different", 0.1, 0).to_dict()
        changed = sealed(body)
        with self.assertRaisesRegex(ValueError, "different frozen|different policy"):
            qualify(data, heldout, artifact, changed)

    def test_normalized_source_and_document_ids_cannot_cross_splits(self):
        for mutation in ("text", "document"):
            data = dataset()
            a, b = data["cases"][0], data["cases"][-1]
            if mutation == "text":
                b["request"]["state"] = "  " + a["request"]["state"].upper().replace(" ", "\n") + " "
            else:
                b["provenance"]["sourceId"] = a["provenance"]["sourceId"]
            with self.subTest(mutation=mutation), self.assertRaisesRegex(ValueError, "leaks"):
                validate_dataset(data)

    def test_dropped_backend_errors_and_reused_holdout_never_pass(self):
        data, plan, scores, artifact, heldout = pipeline()
        scores["cases"][0]["result"]["status"] = "error"
        with self.assertRaisesRegex(ValueError, "backend errors"):
            fit_calibration(data, scores, plan)
        heldout["startedAt"] = "2000-01-01T00:00:00+00:00"
        with self.assertRaisesRegex(ValueError, "after the plan"):
            qualify(data, heldout, artifact, plan)


class QualificationTest(unittest.TestCase):
    def test_exact_bound_matches_published_reference_and_zero_error_rule(self):
        # SciPy documentation's 95% two-sided upper = one-sided upper at 97.5%.
        self.assertAlmostEqual(binomial_upper(7, 50, 0.975), 0.26739600249700846, places=10)
        self.assertAlmostEqual(binomial_upper(0, 300, 0.95), 1 - 0.05**(1 / 300), places=12)
        self.assertLess(binomial_upper(0, 300, 0.95), 0.01)
        self.assertEqual(binomial_upper(0, 0, 0.95), 1)
        self.assertEqual(binomial_upper(8, 8, 0.95), 1)

    def test_group_bounds_never_count_variants_as_independent_examples(self):
        rows = [{"groupId": "one-document", "correct": True, "result": {"status": "ok"}} for _ in range(300)]
        result = group_risk(rows, 0.95)
        self.assertEqual(result["acceptedGroups"], 1)
        self.assertAlmostEqual(result["riskUpperBound"], 0.95)
        rows[1]["correct"] = False
        self.assertEqual(group_risk(rows, 0.95)["groupsWithErrors"], 1)

    def test_synthetic_labels_never_become_expert_qualification(self):
        data, plan, _, artifact, holdout = pipeline()
        result = qualify(data, holdout, artifact, plan)
        self.assertEqual(result["status"], "not_qualified")
        self.assertFalse(next(g for g in result["gates"] if g["name"] == "real_expert_review")["passed"])
        self.assertFalse(result["routingEnabled"])
        self.assertEqual(result["baseline"]["correct"], result["calibrated"]["correct"])
        self.assertEqual(result["risk"]["acceptedGroups"], 8)

    def test_pass_path_requires_review_records_and_explicit_risk_criteria(self):
        # Fabricated expert identities are confined to unit fixtures; never exported as evidence.
        data, plan, _, artifact, holdout = pipeline(dataset(expert=True))
        result = qualify(data, holdout, artifact, plan)
        self.assertEqual(result["status"], "pass")
        self.assertTrue(all(g["passed"] for g in result["gates"]))
        self.assertFalse(result["routingEnabled"])
        case = data["cases"][0]
        self.assertTrue(expert_reviewed(case))
        case["review"]["records"][1]["inputSha256"] = "wrong"
        self.assertFalse(expert_reviewed(case))

    def test_missing_reverse_scores_and_strict_risk_do_not_pass(self):
        data, plan, _, artifact, holdout = pipeline(dataset(expert=True))
        del holdout["cases"][0]["reversedResult"]
        with self.assertRaisesRegex(ValueError, "Missing result"):
            qualify(data, holdout, artifact, plan)
        strict = criteria()
        strict.update(maxGroupRisk=0.01, minAcceptedGroups=300)
        policy = Policy("test", 0.5, 0.1)
        plan = freeze_plan(data, policy, strict, FixtureBackend.identity,
                           execution_profile=DecisionEngine(FixtureBackend(), policy).profile())
        scores = evaluate(DecisionEngine(FixtureBackend(), policy), data, "calibration")
        artifact = fit_calibration(data, scores, plan)
        holdout = evaluate(DecisionEngine(FixtureBackend(), policy), data, "holdout", reverse_options=True)
        self.assertEqual(qualify(data, holdout, artifact, plan)["status"], "not_qualified")


class ReviewTest(unittest.TestCase):
    def pool(self):
        return {"schemaVersion": "agat.decision.pool.v1", "id": "unit-pool", "cases": [
            {k: c[k] for k in ("id", "family", "groupId", "provenance", "request")} for c in dataset()["cases"]]}

    def completed(self):
        first = prepare_review(self.pool(), "fixed-seed")
        self.assertNotIn("expectedOptionId", first["pool"]["cases"][0])
        self.assertIsNone(first["reviewerId"])
        for r in first["labels"]:
            r.update(expectedOptionId="yes", rationale="Unit fixture explanation")
        first.update(reviewerId="reviewer-a", reviewedAt="2026-09-26T00:00:00Z")
        second = copy.deepcopy(first)
        second["reviewerId"] = "reviewer-b"
        return first, second

    def test_incomplete_and_same_reviewer_fail_and_consensus_is_traceable(self):
        pending = prepare_review(self.pool(), "fixed-seed")
        with self.assertRaises(ValueError):
            finalize_reviews(pending, pending)
        first, second = self.completed()
        with self.assertRaisesRegex(ValueError, "distinct"):
            finalize_reviews(first, first)
        output = finalize_reviews(first, second)
        self.assertTrue(all(c["labelSource"] == "expert-reviewed" for c in output["cases"]))
        self.assertTrue(all(c["provenance"]["kind"] == "synthetic" for c in output["cases"]))
        self.assertTrue(all(not expert_reviewed(c) for c in output["cases"]))
        self.assertEqual(output, finalize_reviews(first, second))

    def test_disagreement_requires_separate_adjudication_and_rationale(self):
        first, second = self.completed()
        second["labels"][0]["expectedOptionId"] = "no"
        with self.assertRaisesRegex(ValueError, "Unresolved"):
            finalize_reviews(first, second)
        adjudication = copy.deepcopy(first)
        adjudication["reviewerId"] = "adjudicator"
        adjudication["labels"] = adjudication["labels"][:1]
        output = finalize_reviews(first, second, adjudication)
        self.assertEqual(output["cases"][0]["review"]["status"], "adjudicated")
        self.assertEqual(output["cases"][0]["expectedOptionId"], "yes")

    def test_editing_source_after_annotation_invalidates_review(self):
        first, second = self.completed()
        first["pool"]["cases"][0]["request"]["state"] = "altered"
        with self.assertRaisesRegex(ValueError, "modified"):
            finalize_reviews(first, second)


class ArtifactTest(unittest.TestCase):
    def test_existing_experiment_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "artifact.json"
            value = sealed({"schemaVersion": "test", "temperature": 2})
            write_new(target, value)
            with self.assertRaises(FileExistsError):
                write_new(target, {"different": True})
            self.assertEqual(json.loads(target.read_text()), value)
            verify_seal(value, "test")

    def test_cli_pipeline_exit_codes_and_artifacts_without_gpu(self):
        root = Path(__file__).resolve().parents[2]
        data, plan, scores, artifact, holdout = pipeline()
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)
            for name, value in (("data", data), ("plan", plan), ("scores", scores)):
                write_new(p / f"{name}.json", value)
            command = [sys.executable, "-m", "decision_runtime", "calibrate", "--dataset", str(p / "data.json"),
                       "--plan", str(p / "plan.json"), "--scores", str(p / "scores.json"),
                       "--output", str(p / "fit.json")]
            result = subprocess.run(command, cwd=root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            # Score only after the CLI-produced fit exists: holdout cannot predate fitting.
            holdout = evaluate(DecisionEngine(FixtureBackend(), Policy.from_dict(plan["policy"])), data, "holdout", reverse_options=True)
            write_new(p / "holdout.json", holdout)
            result = subprocess.run([sys.executable, "-m", "decision_runtime", "qualify",
                                     "--dataset", str(p / "data.json"), "--plan", str(p / "plan.json"),
                                     "--scores", str(p / "holdout.json"), "--calibration", str(p / "fit.json"),
                                     "--output", str(p / "qualification.json")], cwd=root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertEqual(json.loads((p / "qualification.json").read_text())["status"], "not_qualified")
            duplicate = subprocess.run(command, cwd=root, capture_output=True, text=True)
            self.assertEqual(duplicate.returncode, 1)
            self.assertIn("already exists", duplicate.stderr)


if __name__ == "__main__":
    unittest.main()
