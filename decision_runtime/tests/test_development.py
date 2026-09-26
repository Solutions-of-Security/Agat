import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from decision_runtime.annotations import group_split, prepare_review
from decision_runtime.artifacts import sealed, verify_seal
from decision_runtime.calibration_workflow import freeze_plan
from decision_runtime.contracts import Policy, Request, fingerprint
from decision_runtime.development import compare_development, prepare_development
from decision_runtime.engine import DecisionEngine, Scores
from decision_runtime.evaluation import evaluate, validate_dataset
from decision_runtime.qualification import expert_reviewed
from decision_runtime.tests.test_calibration import FixtureBackend, criteria
from decision_runtime.tests.test_decisions import request

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "docs/qualification/local-decisions/source-review"
FILES = ("pool.v2.json", "approved-labels.v2.json", "research-review.v2.json", "review.first.blank.v2.json")


class DevelopmentExportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in FILES:
            (self.root / name).write_bytes((SOURCE / name).read_bytes())

    def read(self, name):
        return json.loads((self.root / name).read_text())

    def write(self, name, value):
        (self.root / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")

    def export(self):
        return prepare_development(*(self.root / name for name in FILES))

    def rebind_fixture(self):
        """Deliberately approve modified test data; never write these artifacts to the project."""
        pool, approval, review, reference = map(self.read, FILES)
        reference = prepare_review(pool, reference["splitSeed"])
        self.write(FILES[3], reference)
        approval["poolSha256"] = review["sourceVersion"]["poolSha256"] = fingerprint(pool)
        for binding in review["sourceVersion"]["files"]:
            if binding["path"] in (FILES[0], FILES[3]):
                binding["bytesSha256"] = hashlib.sha256((self.root / binding["path"]).read_bytes()).hexdigest()
        ids = {case["id"] for case in pool["cases"]}
        approval["labels"] = [row for row in approval["labels"] if row["id"] in ids]
        review["cases"] = [row for row in review["cases"] if row["id"] in ids]
        review["counts"].update(reviewedCases=len(ids), confirmedLabels=len(ids))
        self.write(FILES[2], review)
        approval["sourceReview"]["bytesSha256"] = hashlib.sha256((self.root / FILES[2]).read_bytes()).hexdigest()
        self.write(FILES[1], approval)

    def test_approved_v2_exports_only_fifteen_development_cases_and_no_human_records(self):
        data = self.export()
        verify_seal(data, "agat.decision.dataset.v1")
        self.assertEqual(data["usage"], "development-only")
        self.assertEqual(len(data["cases"]), 15)
        self.assertEqual({c["groupId"] for c in data["cases"]},
                         {"agat-internal-report-2026-09-20", "agat-planning-2026-09"})
        self.assertEqual(data["annotation"]["poolSplitCounts"], {"development": 15, "holdout": 9})
        self.assertEqual(len(data["annotation"]["reservedCases"]), 9)
        for reserved in data["annotation"]["reservedCases"]:
            self.assertEqual(set(reserved), {"id", "groupId", "split"})
        for case in data["cases"]:
            self.assertEqual(case["labelSource"], "assistant-reviewed")
            self.assertFalse(expert_reviewed(case))
        self.assertFalse(data["routingEnabled"])
        self.assertFalse(data["qualifiedForRouting"])
        self.assertEqual(self.export(), data)
        with self.assertRaisesRegex(ValueError, "Development-only"):
            freeze_plan(data, Policy(), criteria(), FixtureBackend.identity)

    def test_reserved_requests_never_reach_backend(self):
        data = self.export()
        seen = []

        class Recorder(FixtureBackend):
            def score(self, request):
                seen.append(request.id)
                return Scores([10.0] + [0.0] * (len(request.options) - 1), 30)

        evaluate(DecisionEngine(Recorder()), data, "development", reverse_options=True)
        self.assertEqual(len(seen), 30)
        self.assertEqual(set(seen), {c["id"] for c in data["cases"]})
        self.assertFalse(set(seen) & {c["id"] for c in data["annotation"]["reservedCases"]})
        with self.assertRaisesRegex(ValueError, "empty"):
            evaluate(DecisionEngine(Recorder()), data, "holdout")
        self.assertEqual(len(seen), 30)

    def test_stale_pool_unapproved_review_and_changed_seed_fail_closed(self):
        original = {name: (self.root / name).read_bytes() for name in FILES}
        for mutation in ("pool", "approval", "review", "seed"):
            with self.subTest(mutation=mutation):
                for name, content in original.items():
                    (self.root / name).write_bytes(content)
                if mutation == "pool":
                    (self.root / FILES[0]).write_bytes((SOURCE / "pool.v1.json").read_bytes())
                else:
                    name = {"approval": FILES[1], "review": FILES[2], "seed": FILES[3]}[mutation]
                    value = self.read(name)
                    value["splitSeed" if mutation == "seed" else "status"] = "changed"
                    self.write(name, value)
                with self.assertRaises(ValueError):
                    self.export()

    def test_mismatched_approved_label_and_rationale_are_rejected(self):
        original = self.read(FILES[1])
        for key, value in (("expectedOptionId", "supported"), ("rationale", "different explanation"),
                           ("inputSha256", "0" * 64)):
            with self.subTest(key=key):
                changed = copy.deepcopy(original)
                changed["labels"][0][key] = value
                self.write(FILES[1], changed)
                with self.assertRaisesRegex(ValueError, "source-bound"):
                    self.export()

    def test_incomplete_duplicate_or_nonexpert_approval_escalation_is_rejected(self):
        original = self.read(FILES[1])
        for mutation in ("missing", "duplicate", "human", "routing"):
            changed = copy.deepcopy(original)
            if mutation == "missing":
                changed["labels"].pop()
            elif mutation == "duplicate":
                changed["labels"].append(changed["labels"][0])
            elif mutation == "human":
                changed["humanReviewCompleted"] = True
            else:
                changed["routingEnabled"] = True
            with self.subTest(mutation=mutation):
                self.write(FILES[1], changed)
                with self.assertRaises(ValueError):
                    self.export()

    def test_rebound_review_still_rejects_unresolved_or_unconfirmed_decisions(self):
        original = self.read(FILES[2])
        for mutation in ("finding", "case"):
            changed = copy.deepcopy(original)
            if mutation == "finding":
                changed["findings"][0]["status"] = "open"
            else:
                changed["cases"][0]["verdict"] = "changes_requested"
            with self.subTest(mutation=mutation):
                self.write(FILES[2], changed)
                self.rebind_fixture()
                with self.assertRaises(ValueError):
                    self.export()

    def test_source_leak_is_detected_before_reserved_cases_are_removed(self):
        pool = self.read(FILES[0])
        reference = self.read(FILES[3])
        dev = next(c for c in pool["cases"] if group_split(reference["splitSeed"], c["groupId"]) == "development")
        heldout = next(c for c in pool["cases"] if group_split(reference["splitSeed"], c["groupId"]) == "holdout")
        dev["provenance"]["sourceId"] = heldout["provenance"]["sourceId"]
        self.write(FILES[0], pool)
        self.rebind_fixture()
        with self.assertRaisesRegex(ValueError, "leaks across splits"):
            self.export()

    def test_no_development_groups_does_not_reassign_holdout(self):
        pool, _, _, reference = map(self.read, FILES)
        pool["cases"] = [c for c in pool["cases"] if group_split(reference["splitSeed"], c["groupId"]) == "holdout"]
        self.write(FILES[0], pool)
        self.rebind_fixture()
        with self.assertRaisesRegex(ValueError, "no development cases"):
            self.export()

    def test_tampered_export_or_resealed_split_promotion_is_rejected(self):
        data = self.export()
        data["cases"][0]["split"] = "holdout"
        with self.assertRaisesRegex(ValueError, "checksum"):
            validate_dataset(data)
        data = sealed({k: v for k, v in data.items() if k != "sha256"})
        with self.assertRaisesRegex(ValueError, "Development-only"):
            validate_dataset(data)

    def test_cli_export_is_dependency_free_and_refuses_overwrite(self):
        output = self.root / "development.json"
        command = [sys.executable, "-m", "decision_runtime", "prepare-development"]
        for flag, name in zip(("--pool", "--approval", "--review", "--split-reference"), FILES, strict=True):
            command.extend([flag, str(self.root / name)])
        command.extend(["--output", str(output)])
        first = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(first.returncode, 0, first.stderr)
        before = output.read_bytes()
        second = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(second.returncode, 1)
        self.assertEqual(output.read_bytes(), before)

    def test_cli_invalid_approval_creates_no_dataset(self):
        approval = self.read(FILES[1])
        approval["labels"][0]["expectedOptionId"] = "supported"
        self.write(FILES[1], approval)
        output = self.root / "rejected.json"
        command = [sys.executable, "-m", "decision_runtime", "prepare-development"]
        for flag, name in zip(("--pool", "--approval", "--review", "--split-reference"), FILES, strict=True):
            command.extend([flag, str(self.root / name)])
        result = subprocess.run(command + ["--output", str(output)], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(output.exists())


def diagnostic_dataset():
    cases = []
    for index, label in enumerate(("yes", "no", "unknown")):
        raw = request()
        raw.update(id=f"case-{index}", state=f"Unit source {index}")
        cases.append({"id": raw["id"], "family": "evidence", "groupId": f"group-{index // 2}",
                      "request": raw, "split": "development", "labelSource": "test-fixture",
                      "expectedOptionId": label})
    return {"schemaVersion": "agat.decision.dataset.v1", "id": "comparison-fixture", "cases": cases}


class Candidate:
    def __init__(self, name, winners, *, order_sensitive=False, broken=False):
        self.identity = {**FixtureBackend.identity, "repository": f"unit/{name}"}
        self.winners, self.order_sensitive, self.broken = winners, order_sensitive, broken

    def score(self, request):
        if self.broken and request.id == "case-1":
            raise RuntimeError("test backend failure")
        winner = self.winners[request.id]
        if self.order_sensitive and request.id == "case-0" and request.options[0].id == "unknown":
            winner = "no"
        return Scores([8.0 if option.id == winner else 0.0 for option in request.options], 30)


def comparison_reports(*, broken=False):
    data = diagnostic_dataset()
    left = Candidate("left", {"case-0": "yes", "case-1": "yes", "case-2": "unknown"},
                     order_sensitive=True, broken=broken)
    right = Candidate("right", {c["id"]: c["expectedOptionId"] for c in data["cases"]})
    return data, [evaluate(DecisionEngine(backend), data, "development", reverse_options=True)
                  for backend in (left, right)]


class DevelopmentComparisonTest(unittest.TestCase):
    def test_known_errors_order_changes_and_paired_counts(self):
        data, reports = comparison_reports()
        result = compare_development(data, reports)
        verify_seal(result, "agat.decision.development-comparison.v1")
        left, right = result["models"]
        self.assertEqual(left["original"]["metrics"]["correct"], 2)
        self.assertEqual(left["reversed"]["metrics"]["correct"], 1)
        self.assertEqual(left["original"]["metrics"]["selectiveRisk"], 0.5)
        self.assertEqual(left["reversed"]["metrics"]["selectiveRisk"], 1)
        self.assertEqual(left["optionOrder"]["changedCaseIds"], ["case-0"])
        self.assertEqual(left["original"]["groupsWithAcceptedErrors"], ["group-0"])
        self.assertEqual(left["original"]["groupCount"], 2)
        self.assertEqual(result["pairwise"][0]["original"]["rightOnlyCorrect"], 1)
        self.assertEqual(result["pairwise"][0]["reversed"]["rightOnlyCorrect"], 2)
        self.assertEqual(right["original"]["metrics"]["correct"], 3)
        self.assertFalse(result["routingEnabled"])
        self.assertFalse(result["qualifiedForRouting"])
        self.assertEqual(result["modelSelection"], "not_performed")
        self.assertNotIn("Unit source", json.dumps(result))
        self.assertEqual(result["labelCoverage"][0]["counts"], {"yes": 1, "no": 1, "unknown": 1})

    def test_cached_metrics_probabilities_and_selected_labels_are_not_trusted(self):
        data, reports = comparison_reports()
        report = reports[0]
        report["metrics"] = {"accuracyAllAttempts": 1.0}
        report["families"] = {}
        for row in report["cases"]:
            row["correct"] = True
            row["orderAgreement"] = True
            for key in ("result", "reversedResult"):
                row[key].update(selectedOptionId=row["expectedOptionId"], selectedProbability=1.0, status="ok")
                for value in row[key]["distribution"]:
                    value["probability"] = int(value["id"] == row["expectedOptionId"])
        left = compare_development(data, reports)["models"][0]
        self.assertEqual(left["original"]["metrics"]["correct"], 2)
        self.assertEqual(left["original"]["metrics"]["abstained"], 1)
        self.assertEqual(left["optionOrder"]["changedCaseIds"], ["case-0"])

    def test_errors_stay_in_denominators_and_never_count_as_order_agreement(self):
        data, reports = comparison_reports(broken=True)
        result = compare_development(data, reports)
        left = result["models"][0]
        self.assertEqual(left["original"]["metrics"]["backendErrors"], 1)
        self.assertAlmostEqual(left["original"]["metrics"]["accuracyAllAttempts"], 2 / 3)
        self.assertAlmostEqual(left["original"]["metrics"]["coverage"], 1 / 3)
        self.assertEqual(left["optionOrder"]["scoredPairs"], 2)
        self.assertEqual(result["pairwise"][0]["original"]["pairsWithBackendErrors"], 1)
        self.assertIn({"family": "evidence", "expectedOptionId": "no", "selectedOptionId": None, "count": 1},
                      left["original"]["confusion"])

    def test_family_label_coverage_keeps_missing_options_visible(self):
        data = diagnostic_dataset()
        data["cases"][2]["family"] = "another-family"
        winners = {c["id"]: c["expectedOptionId"] for c in data["cases"]}
        reports = [evaluate(DecisionEngine(Candidate(name, winners)), data, "development", reverse_options=True)
                   for name in ("first", "second")]
        result = compare_development(data, reports)
        coverage = {item["family"]: item for item in result["labelCoverage"]}
        self.assertEqual(coverage["evidence"]["missingOptionIds"], ["unknown"])
        self.assertEqual(coverage["another-family"]["missingOptionIds"], ["no", "yes"])
        self.assertEqual({cell["family"] for cell in result["models"][0]["original"]["confusion"]},
                         {"evidence", "another-family"})

    def test_evidence_mismatch_incomplete_and_calibrated_reports_are_rejected(self):
        for mutation in ("holdout", "dataset", "missing", "duplicate", "id", "reverse", "input", "policy", "calibration", "model"):
            data, reports = comparison_reports()
            changed = reports[0]
            if mutation in ("holdout", "dataset"):
                changed["dataset"]["split" if mutation == "holdout" else "sha256"] = "holdout"
            elif mutation == "missing":
                changed["cases"].pop()
            elif mutation == "duplicate":
                changed["cases"].append(changed["cases"][0])
            elif mutation == "id":
                changed["cases"][0]["id"] = ["malformed"]
            elif mutation == "reverse":
                changed["cases"][0].pop("reversedResult")
            elif mutation == "input":
                changed["cases"][0]["result"]["inputSha256"] = "0" * 64
            elif mutation == "policy":
                changed["cases"][0]["result"]["policy"]["minProbability"] = 0
            elif mutation == "calibration":
                changed["cases"][0]["result"]["calibration"]["temperature"] = 2.0
            else:
                changed["cases"][0]["result"]["model"] = {"repository": "different"}
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                compare_development(data, reports)

    def test_candidate_reports_require_two_distinct_models_and_common_policy(self):
        data, reports = comparison_reports()
        for candidates in (reports[:1], [reports[0], reports[0]]):
            with self.assertRaises(ValueError):
                compare_development(data, candidates)
        backend = Candidate("third", {c["id"]: c["expectedOptionId"] for c in data["cases"]})
        other = evaluate(DecisionEngine(backend, Policy("lower", 0.5, 0.1)), data, "development", reverse_options=True)
        with self.assertRaisesRegex(ValueError, "different policies"):
            compare_development(data, [reports[0], other])

    def test_report_case_order_does_not_change_pairing(self):
        data, reports = comparison_reports()
        expected = compare_development(data, reports)
        reports[1]["cases"].reverse()
        result = compare_development(data, reports)
        self.assertEqual(result["pairwise"], expected["pairwise"])

    def test_cli_comparison_writes_error_evidence_but_returns_failure(self):
        data, reports = comparison_reports(broken=True)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, value in (("data.json", data), ("left.json", reports[0]), ("right.json", reports[1])):
                (root / name).write_text(json.dumps(value))
            output = root / "comparison.json"
            command = [sys.executable, "-m", "decision_runtime", "compare-development",
                       "--dataset", str(root / "data.json"), "--scores", str(root / "left.json"),
                       str(root / "right.json"), "--output", str(output)]
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1, result.stderr)
            report = json.loads(output.read_text())
            self.assertEqual(report["status"], "diagnostic_only")
            self.assertEqual(report["models"][0]["original"]["metrics"]["backendErrors"], 1)


if __name__ == "__main__":
    unittest.main()
