from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from decision_runtime.annotations import finalize_reviews
from decision_runtime.artifacts import sealed
from decision_runtime.contracts import canonical_json
from scripts.lib.decision_review_adjudication import encoded, prepare_handoff
from scripts.lib.decision_review_pair import compare_pair
import scripts.test.test_decision_review_pair as review_pair_fixtures

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("adjudication_cli", ROOT / "scripts/prepare-review-adjudication.py")
cli = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(cli)
sha = lambda raw: hashlib.sha256(raw).hexdigest()


class ReviewAdjudicationTest(unittest.TestCase):
    def setUp(self):
        self.helper = review_pair_fixtures.ReviewPairTest(); self.helper.setUp(); self.addCleanup(self.helper.doCleanups)
        self.root = self.helper.root.resolve()
        self.first = self.helper.binding("first", self.helper.a, answers=["yes", "yes", "yes"])
        self.second = self.helper.binding("second", self.helper.b, answers=["no", "yes", "yes"])
        self.comparison = compare_pair(self.first, self.second)
        self.path = self.root / "comparison.json"; self.path.write_bytes(encoded(self.comparison))

    def outputs(self):
        return prepare_handoff(self.first, self.second, self.path, sha(self.path.read_bytes()))

    def args(self, output):
        args = ["--comparison", str(self.path), "--comparison-file-sha256", sha(self.path.read_bytes()),
                "--output-dir", str(output)]
        for prefix, binding in (("first", self.first), ("second", self.second)):
            for index, name in enumerate(("session", "input-review", "output-review")):
                args.extend(["--" + prefix + "-" + name, str(binding[index * 2]),
                             "--" + prefix + "-" + name + "-file-sha256", binding[index * 2 + 1]])
        return args

    def test_blank_uses_whole_original_pool_and_only_disputed_labels(self):
        outputs = self.outputs(); blank = outputs["adjudication.review.blank.json"]
        original = json.loads(Path(self.first[4]).read_bytes())
        self.assertEqual(blank["pool"], original["pool"])
        self.assertEqual(blank["poolSha256"], original["poolSha256"])
        self.assertEqual(blank["splitSeed"], original["splitSeed"])
        self.assertEqual([label["id"] for label in blank["labels"]], [self.comparison["disagreements"][0]["id"]])
        self.assertIsNone(blank["reviewerId"]); self.assertIsNone(blank["reviewedAt"])
        self.assertTrue(all(label["expectedOptionId"] is None and label["rationale"] is None for label in blank["labels"]))
        self.assertEqual(outputs["packet.json"]["resolvedCases"], 0)

    def test_notes_preserve_whole_case_and_both_rationales(self):
        note = self.outputs()["case-notes.json"]["cases"][0]
        original = json.loads(Path(self.first[4]).read_bytes())
        self.assertEqual(note["case"], original["pool"]["cases"][0])
        for key in ("inputSha256", "firstOptionId", "secondOptionId", "firstRationale", "secondRationale"):
            self.assertEqual(note[key], self.comparison["disagreements"][0][key])

    def test_order_and_group_counts_follow_original_disagreements(self):
        self.second = self.helper.binding("two-disputes", self.helper.b, answers=["no", "yes", "no"])
        self.comparison = compare_pair(self.first, self.second); self.path.write_bytes(encoded(self.comparison))
        outputs = self.outputs(); packet = outputs["packet.json"]
        self.assertEqual(packet["disputedCaseIds"], [item["id"] for item in self.comparison["disagreements"]])
        self.assertEqual([item["id"] for item in outputs["adjudication.review.blank.json"]["labels"]], packet["disputedCaseIds"])
        self.assertEqual(packet["disputedCaseCount"], 2)
        self.assertEqual(packet["disputedGroupCount"], len({item["groupId"] for item in self.comparison["disagreements"]}))

    def test_packet_pins_exact_output_bytes_and_assigns_no_owner_or_labels(self):
        outputs = self.outputs(); packet = outputs["packet.json"]
        for name, pin in packet["outputFiles"].items():
            self.assertEqual(pin, {"sha256": sha(encoded(outputs[name])), "bytes": len(encoded(outputs[name]))})
        self.assertIsNone(packet["adjudicatorId"])
        for key in ("reviewerIdentityVerified", "humanExecutionVerified", "independentReviewVerified",
                    "expertQualificationsVerified", "ownersAppointed", "classificationAccuracyMeasured", "routingEnabled"):
            self.assertIs(packet[key], False)
        self.assertEqual(packet["referenceLabelsCreated"], 0)
        self.assertEqual(packet["modelCallsDuringPreparation"], 0)

    def test_unresolved_draft_cannot_finalize_reference_labels(self):
        first = json.loads(Path(self.first[4]).read_bytes()); second = json.loads(Path(self.second[4]).read_bytes())
        with self.assertRaises(ValueError):
            finalize_reviews(first, second, self.outputs()["adjudication.review.blank.json"])

    def test_changed_resealed_comparison_is_refused(self):
        changed = deepcopy(self.comparison); changed["disagreements"][0]["firstRationale"] = "Changed control"
        changed.pop('sha256')
        self.path.write_bytes(encoded(sealed(changed)))
        with self.assertRaises(ValueError): self.outputs()

    def test_no_disagreements_and_wrong_raw_pin_are_refused(self):
        self.second = self.helper.binding("agree", self.helper.b, answers=["yes", "yes", "yes"])
        self.comparison = compare_pair(self.first, self.second); self.path.write_bytes(encoded(self.comparison))
        with self.assertRaises(ValueError): self.outputs()
        with self.assertRaises(ValueError): prepare_handoff(self.first, self.second, self.path, "0" * 64)

    def test_cli_creates_three_private_outputs_and_refuses_reuse(self):
        output = self.root / "docs/private/handoff"
        with patch.object(cli, "ROOT", self.root):
            self.assertEqual(cli.main(self.args(output)), 0)
            saved = {path.name: path.read_bytes() for path in output.iterdir()}
            self.assertEqual(cli.main(self.args(output)), 1)
        self.assertEqual(set(saved), {"packet.json", "adjudication.review.blank.json", "case-notes.json"})
        packet = json.loads(saved["packet.json"])
        for name, pin in packet["outputFiles"].items(): self.assertEqual(sha(saved[name]), pin["sha256"])
        self.assertEqual(saved, {path.name: path.read_bytes() for path in output.iterdir()})
        self.assertTrue(all(path.stat().st_mode & 0o777 == 0o600 for path in output.iterdir()))
