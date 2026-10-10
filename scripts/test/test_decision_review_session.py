import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from decision_runtime.artifacts import sealed
from decision_runtime.contracts import canonical_json
from scripts.lib.decision_review_session import verify_session
from scripts.test.test_decision_blind_review import fixture

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("review_session_cli", ROOT / "scripts/verify-decision-review-session.py")
cli = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(cli)


def raw(value):
    return (canonical_json(value) + "\n").encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


class ReviewSessionVerificationTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.initial = fixture(); self.saved = copy.deepcopy(self.initial); self.saved["reviewerId"] = "fixture-reviewer"
        self.session = {"schemaVersion": "agat.decision.blind-review-session.v2", "status": "partial",
                        "startedAt": "2026-10-10T10:00:00.000Z", "finishedAt": "2026-10-10T10:00:01.000Z",
                        "endReason": "quit", "sourceCommit": "a" * 40,
                        "sourceFiles": {"scripts/review-decision-pool.py": "b" * 64},
                        "poolSha256": self.initial["poolSha256"], "reviewerId": "fixture-reviewer",
                        "existingAnswers": 0, "newAnswers": 0, "revisedAnswers": 0, "clearedAnswers": 0,
                        "remainingAnswers": 3, "submissionConfirmed": False, "modelCalls": 0,
                        "qualification": "not_assessed", "failureType": None,
                        **{key: False for key in ("reviewerIdentityVerified", "humanExecutionVerified",
                                                 "independentReviewVerified", "expertQualificationsVerified", "routingEnabled")}}

    def write_artifacts(self):
        self.paths = [self.directory / name for name in ("session.json", "initial.json", "saved.json")]
        self.paths[1].write_bytes(raw(self.initial)); self.paths[2].write_bytes(raw(self.saved))
        self.session.update(inputReviewFileSha256=digest(self.paths[1].read_bytes()), outputReviewFileSha256=digest(self.paths[2].read_bytes()))
        self.paths[0].write_bytes(raw(sealed(self.session)))
        self.pins = [digest(path.read_bytes()) for path in self.paths]

    def verify(self):
        self.write_artifacts()
        return self.verify_written()

    def verify_written(self):
        return verify_session(self.paths[0], self.pins[0], self.paths[1], self.pins[1], self.paths[2], self.pins[2])

    def fill(self, value):
        value["reviewerId"] = "fixture-reviewer"
        for label in value["labels"]: label.update(expectedOptionId="yes", rationale="Synthetic fixture only.")

    def test_blank_partial_passes_without_completion_or_authority(self):
        receipt = self.verify()
        self.assertFalse(receipt["completeReviewArtifact"]); self.assertFalse(receipt["allAnswersPresent"])
        self.assertFalse(receipt["reviewSourcesRevalidated"]); self.assertFalse(receipt["humanExecutionVerified"])
        self.assertEqual(receipt["modelCallsDuringVerification"], 0)

    def test_complete_draft_stays_partial_without_submit(self):
        self.fill(self.saved); self.session.update(newAnswers=3, remainingAnswers=0)
        receipt = self.verify()
        self.assertTrue(receipt["allAnswersPresent"]); self.assertFalse(receipt["completeReviewArtifact"])

    def test_explicit_completed_submission_passes(self):
        self.fill(self.saved); self.saved["reviewedAt"] = self.session["finishedAt"]
        self.session.update(status="completed", endReason="submitted", newAnswers=3, remainingAnswers=0, submissionConfirmed=True)
        receipt = self.verify(); self.assertTrue(receipt["completeReviewArtifact"])
        self.assertFalse(receipt["expertQualificationsVerified"])

    def test_net_new_revised_and_cleared_answers_reconstructed(self):
        self.fill(self.initial); self.initial["labels"][0].update(expectedOptionId=None, rationale=None)
        self.saved = copy.deepcopy(self.initial); self.saved["labels"][0].update(expectedOptionId="yes", rationale="Fixture new")
        self.saved["labels"][1].update(expectedOptionId="no", rationale="Fixture revision")
        self.saved["labels"][2].update(expectedOptionId=None, rationale=None)
        self.session.update(existingAnswers=2, newAnswers=1, revisedAnswers=1, clearedAnswers=1, remainingAnswers=1)
        self.assertEqual(self.verify()["counts"], {"existingAnswers": 2, "newAnswers": 1, "revisedAnswers": 1, "clearedAnswers": 1, "remainingAnswers": 1})

    def test_failed_placeholders_do_not_erase_saved_answer_count(self):
        self.saved["labels"][0].update(expectedOptionId="yes", rationale="Saved before failure")
        self.session.update(status="failed", endReason=None, remainingAnswers=None, failureType="ValueError")
        receipt = self.verify()
        self.assertEqual(receipt["counts"]["newAnswers"], 1); self.assertFalse(receipt["reportedCountsReconciled"])
        self.assertFalse(receipt["completeReviewArtifact"])

    def test_failed_receipt_after_submit_remains_failed(self):
        self.fill(self.saved); self.saved["reviewedAt"] = "2026-10-10T10:00:00.500Z"
        self.session.update(status="failed", endReason="submitted", submissionConfirmed=True, newAnswers=3, remainingAnswers=0, failureType="OSError")
        receipt = self.verify(); self.assertFalse(receipt["completeReviewArtifact"]); self.assertTrue(receipt["submissionConfirmed"])

    def test_resealed_receipt_cannot_forge_completion_or_identity(self):
        changes = [{"status": "completed"}, {"humanExecutionVerified": True}, {"modelCalls": True}, {"newAnswers": True},
                   {"newAnswers": 1}, {"remainingAnswers": False}, {"submissionConfirmed": True}, {"failureType": "ValueError"},
                   {"finishedAt": "2026-10-10T09:00:00.000Z"}, {"sourceCommit": "branch"},
                   {"sourceFiles": {"../escape.py": "a" * 64}}, {"sourceFiles": {"scripts/review-decision-pool.py": "bad"}},
                   {"schemaVersion": "agat.decision.blind-review-session.v1"}, {"extra": "unknown"}]
        original = copy.deepcopy(self.session)
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.session = {**copy.deepcopy(original), **change}; self.verify()

    def test_changed_pool_seed_owner_labels_or_timestamp_are_refused(self):
        original = copy.deepcopy(self.saved)
        for change in [{"splitSeed": "other-seed"}, {"reviewerId": "different"}, {"reviewedAt": "2026-10-10T10:00:01.000Z"}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.saved = {**copy.deepcopy(original), **change}; self.verify()
        self.saved = original; self.saved["labels"].reverse()
        with self.assertRaises(ValueError): self.verify()

    def test_wrong_inner_bindings_or_canonical_seal_are_refused(self):
        self.write_artifacts(); value = json.loads(self.paths[0].read_bytes())
        for key in ("inputReviewFileSha256", "outputReviewFileSha256", "poolSha256"):
            changed = {**value, key: "a" * 64}; changed.pop("sha256"); self.paths[0].write_bytes(raw(sealed(changed)))
            self.pins[0] = digest(self.paths[0].read_bytes())
            with self.subTest(key=key), self.assertRaises(ValueError): self.verify_written()
        self.paths[0].write_bytes(raw({**value, "sha256": "0" * 64})); self.pins[0] = digest(self.paths[0].read_bytes())
        with self.assertRaises(ValueError): self.verify_written()

    def test_wrong_external_sha_and_duplicate_json_fields_are_refused(self):
        self.write_artifacts(); self.paths[2].write_bytes(self.paths[2].read_bytes() + b" ")
        with self.assertRaises(ValueError): self.verify_written()
        self.write_artifacts(); content = self.paths[0].read_bytes(); self.paths[0].write_bytes(b'{"status":"completed",' + content[1:])
        self.pins[0] = digest(self.paths[0].read_bytes())
        with self.assertRaises(ValueError): self.verify_written()

    def test_symlink_and_oversized_input_are_refused(self):
        self.write_artifacts(); original = self.paths[1]; target = self.directory / "target.json"; target.write_bytes(original.read_bytes())
        original.unlink(); original.symlink_to(target)
        with self.assertRaises(ValueError): self.verify_written()
        original.unlink(); original.write_bytes(b" " * 20); self.pins[1] = digest(original.read_bytes())
        with patch("scripts.lib.decision_review_session.MAX_REVIEW_BYTES", 10), self.assertRaises(ValueError): self.verify_written()

    def test_cli_verifies_before_creating_private_output_and_refuses_reuse(self):
        self.write_artifacts(); output = self.directory / "docs/private/result"
        arguments = []
        for name, path, pin in zip(("session", "input-review", "output-review"), self.paths, self.pins):
            arguments.extend(["--" + name, str(path), "--" + name + "-file-sha256", pin])
        arguments.extend(["--output-dir", str(output)])
        with patch.object(cli, "ROOT", self.directory):
            self.assertEqual(cli.main(arguments), 0)
            original = (output / "verification.json").read_bytes()
            self.assertEqual(cli.main(arguments), 1); self.assertEqual((output / "verification.json").read_bytes(), original)
        other = self.directory / "docs/private/refused"; arguments[-1] = str(other); self.paths[2].write_bytes(b"changed")
        with patch.object(cli, "ROOT", self.directory): self.assertEqual(cli.main(arguments), 1)
        self.assertFalse(other.exists())
