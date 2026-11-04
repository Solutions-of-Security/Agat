import copy
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from decision_runtime.annotations import prepare_review
from decision_runtime.artifacts import sealed
from decision_runtime.contracts import canonical_json, fingerprint
from scripts.lib.decision_review_pair import compare_pair
from scripts.test.test_decision_blind_review import fixture

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("review_pair_cli", ROOT / "scripts/compare-decision-reviews.py")
cli = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(cli)


class ReviewPairTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.a = fixture(); self.b = fixture()

    def binding(self, name, blank, *, reviewer=None, answers=None, state="completed", rationales=None):
        reviewer = reviewer or "fixture-" + name
        blank = copy.deepcopy(blank); saved = copy.deepcopy(blank); saved["reviewerId"] = reviewer
        answers = answers or ["yes"] * 3
        for index, (label, answer) in enumerate(zip(saved["labels"], answers)):
            label.update(expectedOptionId=answer, rationale=(rationales[index] if rationales else "Synthetic fixture only.") if answer else None)
        submitted = state == "completed"; stamp = "2026-10-10T10:00:01.000Z"
        saved["reviewedAt"] = stamp if submitted else None
        initial_raw = (canonical_json(blank) + "\n").encode(); saved_raw = (canonical_json(saved) + "\n").encode()
        sha = lambda raw: hashlib.sha256(raw).hexdigest()
        count = sum(answer is not None for answer in answers)
        session = {"schemaVersion": "agat.decision.blind-review-session.v2", "status": state,
                   "startedAt": "2026-10-10T10:00:00.000Z", "finishedAt": stamp,
                   "endReason": "submitted" if submitted else None if state == "failed" else "quit",
                   "sourceCommit": "a" * 40, "sourceFiles": {"scripts/review-decision-pool.py": "b" * 64},
                   "inputReviewFileSha256": sha(initial_raw), "outputReviewFileSha256": sha(saved_raw),
                   "poolSha256": blank["poolSha256"], "reviewerId": reviewer, "existingAnswers": 0,
                   "newAnswers": count if state != "failed" else 0, "revisedAnswers": 0, "clearedAnswers": 0,
                   "remainingAnswers": 3 - count if state != "failed" else None, "submissionConfirmed": submitted,
                   "failureType": "ValueError" if state == "failed" else None, "modelCalls": 0, "qualification": "not_assessed",
                   **{key: False for key in ("reviewerIdentityVerified", "humanExecutionVerified", "independentReviewVerified",
                                            "expertQualificationsVerified", "routingEnabled")}}
        values = [(canonical_json(sealed(session)) + "\n").encode(), initial_raw, saved_raw]
        result = []
        for suffix, raw in zip(("session", "input", "saved"), values):
            path = self.root / (name + "-" + suffix + ".json"); path.write_bytes(raw); result.extend((path, sha(raw)))
        return tuple(result)

    def test_same_options_with_different_rationales_are_descriptive_agreements(self):
        first = self.binding("first", self.a); second = self.binding("second", self.b, rationales=["Different rationale."] * 3)
        result = compare_pair(first, second)
        self.assertEqual(result["agreementCount"], 3); self.assertEqual(result["agreementFraction"], 1.0)
        self.assertEqual(result["disagreements"], []); self.assertFalse(result["requiresAdjudication"])

    def test_disagreements_keep_case_order_and_both_rationales(self):
        result = compare_pair(self.binding("first", self.a), self.binding("second", self.b, answers=["no", "yes", "no"], rationales=["One", "Two", "Three"]))
        self.assertEqual(result["agreementCount"], 1); self.assertEqual(result["disagreementCount"], 2)
        self.assertEqual([item["id"] for item in result["disagreements"]], ["fixture-0", "fixture-2"])
        self.assertEqual(result["disagreements"][1]["secondRationale"], "Three")
        self.assertTrue(result["requiresAdjudication"])

    def test_group_counts_preserve_multiple_cases_in_one_group(self):
        pool = copy.deepcopy(self.a["pool"]); pool["cases"][1]["groupId"] = pool["cases"][0]["groupId"]
        self.a = prepare_review(pool, self.a["splitSeed"]); self.b = copy.deepcopy(self.a)
        result = compare_pair(self.binding("first", self.a), self.binding("second", self.b, answers=["yes", "no", "yes"]))
        self.assertEqual(result["groupCount"], 2); self.assertEqual(sum(item["caseCount"] for item in result["groups"]), 3)
        self.assertEqual(result["groups"][0]["caseCount"], 2); self.assertEqual(result["groups"][0]["disagreementCount"], 1)

    def test_distinct_ids_do_not_grant_human_expert_or_accuracy_authority(self):
        result = compare_pair(self.binding("first", self.a), self.binding("second", self.b))
        for key in ("reviewerIdentityVerified", "humanExecutionVerified", "independentReviewVerified", "expertQualificationsVerified", "classificationAccuracyMeasured", "routingEnabled"):
            self.assertIs(result[key], False)
        self.assertEqual(result["referenceLabelsCreated"], 0); self.assertEqual(result["qualification"], "not_assessed")
        self.assertEqual(result["sha256"], fingerprint({key: value for key, value in result.items() if key != "sha256"}))

    def test_same_reviewer_ids_are_refused(self):
        with self.assertRaises(ValueError): compare_pair(self.binding("first", self.a), self.binding("second", self.b, reviewer="fixture-first"))

    def test_full_partial_and_failed_drafts_are_refused(self):
        first = self.binding("first", self.a)
        for state in ("partial", "failed"):
            with self.subTest(state=state), self.assertRaises(ValueError): compare_pair(first, self.binding(state, self.b, state=state))

    def test_missing_answer_cannot_claim_completion(self):
        with self.assertRaises(ValueError): compare_pair(self.binding("first", self.a), self.binding("second", self.b, answers=["yes", None, "yes"]))

    def test_separately_valid_reviews_with_different_seeds_are_refused(self):
        self.b["splitSeed"] = "different-seed"
        with self.assertRaises(ValueError): compare_pair(self.binding("first", self.a), self.binding("second", self.b))

    def test_separately_valid_reviews_with_different_frozen_pools_are_refused(self):
        pool = copy.deepcopy(self.b["pool"]); pool["cases"][0]["request"]["state"] = "Different synthetic source."
        self.b = prepare_review(pool, self.b["splitSeed"])
        with self.assertRaises(ValueError): compare_pair(self.binding("first", self.a), self.binding("second", self.b))

    def test_original_artifacts_are_unchanged_and_wrong_sha_is_refused(self):
        first = self.binding("first", self.a); second = self.binding("second", self.b)
        before = {path: path.read_bytes() for path in (*first[::2], *second[::2])}
        compare_pair(first, second); self.assertEqual(before, {path: path.read_bytes() for path in before})
        second[4].write_bytes(second[4].read_bytes() + b" ")
        with self.assertRaises(ValueError): compare_pair(first, second)

    def test_cli_creates_new_private_comparison_and_never_overwrites(self):
        arguments = []
        for prefix, binding in (("first", self.binding("first", self.a)), ("second", self.binding("second", self.b))):
            for name, path, pin in zip(("session", "input-review", "output-review"), binding[::2], binding[1::2]):
                arguments.extend(["--" + prefix + "-" + name, str(path), "--" + prefix + "-" + name + "-file-sha256", pin])
        output = self.root / "docs/private/pair"; arguments += ["--output-dir", str(output)]
        with patch.object(cli, "ROOT", self.root):
            self.assertEqual(cli.main(arguments), 0); original = (output / "comparison.json").read_bytes()
            self.assertEqual(cli.main(arguments), 1); self.assertEqual((output / "comparison.json").read_bytes(), original)
            second = self.root / "docs/private/refused"; arguments[-1] = str(second); arguments[3] = "a" * 64
            self.assertEqual(cli.main(arguments), 1); self.assertFalse(second.exists())
