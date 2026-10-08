import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from decision_runtime.annotations import finalize_reviews, prepare_review
from decision_runtime.artifacts import verify_seal
from decision_runtime.contracts import fingerprint
from scripts.lib.decision_blind_review import interact, task_text, terminal_text, validate_progress

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("blind_review_cli", ROOT / "scripts/review-decision-pool.py")
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)


class Terminal(io.StringIO):
    def isatty(self):
        return True


def fixture():
    cases = []
    for index, kind in enumerate(("choice", "boolean", "score")):
        case_id = f"fixture-{index}"
        options = [{"id": "yes", "description": "Explicitly yes"},
                   {"id": "no", "description": "Explicitly no"}]
        if kind == "choice":
            options.append({"id": "insufficient", "description": "Insufficient source", "abstain": True})
        elif kind == "boolean":
            for item, value in zip(options, (True, False)): item["value"] = value
        else:
            for item, value in zip(options, (0.0, 1.0)): item["value"] = value
        request = {"schemaVersion": "agat.decision.v1", "id": case_id, "kind": kind,
                   "state": f"Synthetic fixture {index} only; не human evidence.", "question": "Choose from the source only.", "options": options}
        cases.append({"id": case_id, "family": "classification", "groupId": f"hidden-group-{index}",
                      "provenance": {"kind": "synthetic", "sourceId": f"fixture-source-{index}",
                                     "reference": "Synthetic test fixture; no real expert labels."}, "request": request})
    return prepare_review({"schemaVersion": "agat.decision.pool.v1", "id": "fixture", "cases": cases}, "hidden-seed")


class ReviewTest(unittest.TestCase):
    def test_progress_rejects_rebound_incomplete_unowned_and_foreign_answers(self):
        blank = fixture()
        self.assertEqual(validate_progress(blank, "fixture-reviewer"), blank)
        mutations = [lambda v: v.update(poolSha256="a" * 64),
                     lambda v: v["labels"].reverse(),
                     lambda v: v["labels"][0].update(inputSha256="b" * 64),
                     lambda v: v["labels"].pop(),
                     lambda v: v["labels"][0].update(expectedOptionId="unknown", rationale="fixture"),
                     lambda v: v["labels"][0].update(expectedOptionId="yes"),
                     lambda v: v["labels"][0].update(rationale="fixture"),
                     lambda v: v["labels"][0].update(expectedOptionId="yes", rationale="fixture"),
                     lambda v: v.update(reviewerId="other-reviewer"),
                     lambda v: v.update(reviewedAt="already submitted"),
                     lambda v: v.update(extra="prediction")]
        for mutate in mutations:
            value = copy.deepcopy(blank); mutate(value)
            with self.subTest(mutation=mutate), self.assertRaises(ValueError): validate_progress(value, "fixture-reviewer")
        value = copy.deepcopy(blank); value["reviewerId"] = "fixture-reviewer"
        value["labels"][0].update(expectedOptionId="yes", rationale="fixture")
        self.assertEqual(validate_progress(value, "fixture-reviewer"), value)
        value["pool"]["cases"][0]["request"]["state"] = "changed source"
        value["poolSha256"] = fingerprint(value["pool"])
        with self.assertRaises(ValueError): validate_progress(value, "fixture-reviewer")

    def test_task_renderer_hides_split_metadata_and_neutralizes_all_controls(self):
        value = fixture(); case = value["pool"]["cases"][0]
        hostile = "Русский\n\ttext\x1b]52;c;YXR0YWNr\x07\x1b[2J\r\b\x9b\u202e\u2028"
        case["request"]["state"] = hostile
        case["request"]["question"] += "\x1b[H"
        case["provenance"]["reference"] += "\x1b]8;;https://example.invalid\x07"
        case["request"]["options"][0]["description"] += "\x1b[31m"
        text = task_text(case, 1, 3)
        self.assertIn("Русский\n\ttext", text)
        for control in ("\x1b", "\x07", "\r", "\b", "\x9b", "\u202e", "\u2028"):
            self.assertNotIn(control, text)
        self.assertIn("\\x1b]52", text); self.assertIn("\\u202e", text)
        self.assertIn("[abstain]", text)
        for hidden in ("hidden-group", "hidden-seed", "splitSeed", "poolSha256", "expectedOptionId"):
            self.assertNotIn(hidden, text)
        self.assertEqual(case["request"]["state"], hostile)
        self.assertEqual(terminal_text("plain code $() `ls`\\n"), "plain code $() `ls`\\n")

    def test_no_default_skip_eof_and_unconfirmed_answers_remain_blank(self):
        for answers in ("\ninvalid\n:skip\n:quit\n", "1\n", "1\nfixture reason\n", "1\nfixture\nn\n:quit\n", "1\nfixture\n\n:quit\n"):
            value = fixture(); saved = []
            result, reason = interact(value, Terminal(answers), Terminal(), saved.append)
            self.assertFalse(saved)
            self.assertTrue(all(x["expectedOptionId"] is None and x["rationale"] is None for x in result["labels"]))
            self.assertIn(reason, ("quit", "eof"))

    def test_confirmed_answer_survives_interrupt_at_checkpoint_boundary(self):
        def interrupted(_value): raise KeyboardInterrupt()
        result, reason = interact(fixture(), Terminal("3\nfixture-only abstention\ny\n"), Terminal(), interrupted)
        self.assertEqual(reason, "interrupted")
        self.assertEqual(result["labels"][0]["expectedOptionId"], "insufficient")
        self.assertTrue(all(x["expectedOptionId"] is None for x in result["labels"][1:]))

    def test_oversized_paste_is_drained_instead_of_becoming_a_later_answer(self):
        saved = []
        output = Terminal()
        result, _reason = interact(fixture(), Terminal("1" * 12000 + "\n:quit\n"), output, saved.append)
        self.assertFalse(saved); self.assertIn("exceeds 4000", output.getvalue())
        self.assertIsNone(result["labels"][0]["expectedOptionId"])


class ReviewCliTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.private = self.root / "docs/private"; self.private.mkdir(parents=True)
        self.base = self.private / "blank.json"; self.base.write_text(json.dumps(fixture()))
        self.identity = ("a" * 40, {name: "b" * 64 for name in cli.SOURCES})

    def invoke(self, answers, name="session", source=None, reviewer="fixture-reviewer", identity=None, tty=True, pin=None):
        source = source or self.base; directory = self.private / name
        args = ["--review", str(source), "--review-file-sha256", pin or hashlib.sha256(source.read_bytes()).hexdigest(),
                "--reviewer-id", reviewer, "--output-dir", str(directory)]
        output = Terminal()
        with patch.object(cli, "ROOT", self.root), patch.object(cli, "source_identity", side_effect=identity if identity else None,
                                                                  return_value=self.identity):
            code = cli.main(args, input_stream=Terminal(answers) if tty else io.StringIO(answers), output_stream=output)
        report = verify_seal(json.loads((directory / "session.json").read_text()), "agat.decision.blind-review-session.v1") if directory.exists() else None
        review = json.loads((directory / "review.json").read_text()) if directory.exists() else None
        return code, directory, report, review, output.getvalue()

    def test_partial_resume_fingerprints_private_files_and_finalize_interoperate(self):
        original = self.base.read_bytes()
        code, directory, report, review, _text = self.invoke("1\nfixture first reason\ny\n:quit\n")
        self.assertEqual(code, 2); self.assertEqual(report["status"], "partial")
        self.assertEqual((report["newAnswers"], report["remainingAnswers"]), (1, 2))
        self.assertIsNone(review["reviewedAt"])
        self.assertEqual(review["pool"], fixture()["pool"])
        self.assertEqual(self.base.read_bytes(), original)
        self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        for item in directory.iterdir(): self.assertEqual(item.stat().st_mode & 0o777, 0o600)
        for name in ("reviewerIdentityVerified", "humanExecutionVerified", "independentReviewVerified", "expertQualificationsVerified", "routingEnabled"):
            self.assertIs(report[name], False)
        code, final_dir, report, final, text = self.invoke("2\nfixture boolean reason\ny\n1\nfixture score reason\ny\n", name="resume", source=directory / "review.json")
        self.assertEqual(code, 0); self.assertEqual(report["status"], "completed")
        self.assertEqual((report["existingAnswers"], report["newAnswers"], report["remainingAnswers"]), (1, 2, 0))
        self.assertNotIn("Task 1/3", text); self.assertIn("[value=False]", text); self.assertIn("[value=0.0]", text)
        self.assertEqual(final["labels"][0], review["labels"][0]); self.assertIsNotNone(final["reviewedAt"])
        self.assertEqual(report["outputReviewFileSha256"], hashlib.sha256((final_dir / "review.json").read_bytes()).hexdigest())
        other_fixture = copy.deepcopy(final); other_fixture["reviewerId"] = "second-fixture-reviewer"
        dataset = finalize_reviews(final, other_fixture)
        self.assertEqual(len(dataset["cases"]), 3)
        with self.assertRaises(ValueError): finalize_reviews(final, final)
        with self.assertRaises(ValueError): finalize_reviews(fixture(), fixture())
        self.assertEqual(report["modelCalls"], 0); self.assertEqual(report["qualification"], "not_assessed")

    def test_nonterminal_bad_pin_foreign_reviewer_and_existing_directory_fail_before_input(self):
        for name, kwargs in (("notty", {"tty": False}), ("badpin", {"pin": "c" * 64})):
            code, directory, _r, _v, text = self.invoke("1\nfixture\ny\n", name=name, **kwargs)
            self.assertEqual(code, 1); self.assertFalse(directory.exists()); self.assertFalse(text)
        code, directory, _r, _v, _t = self.invoke("1\nfixture\ny\n:quit\n")
        original = (directory / "review.json").read_bytes()
        code, wrong_dir, _r, _v, text = self.invoke("", name="foreign", source=directory / "review.json", reviewer="other-fixture")
        self.assertEqual(code, 1); self.assertFalse(wrong_dir.exists()); self.assertFalse(text)
        self.assertEqual(self.invoke("anything")[0], 1)
        self.assertEqual((directory / "review.json").read_bytes(), original)

    def test_source_drift_keeps_failed_receipt_and_unmodified_blank_checkpoint(self):
        changed = ("c" * 40, self.identity[1])
        code, _d, report, review, _t = self.invoke("1\nfixture\ny\n", identity=[self.identity, changed])
        self.assertEqual(code, 1); self.assertEqual(report["status"], "failed")
        self.assertEqual(report["failureType"], "ValueError")
        self.assertTrue(all(x["expectedOptionId"] is None for x in review["labels"]))

    def test_atomic_replace_failure_does_not_corrupt_existing_checkpoint(self):
        directory = self.private / "checkpoint"; directory.mkdir(mode=0o700)
        cli.checkpoint(directory, fixture())
        original = (directory / "review.json").read_bytes()
        value = fixture(); value["reviewerId"] = "fixture-reviewer"
        with patch.object(cli.os, "replace", side_effect=OSError("fixture interruption")), self.assertRaises(OSError):
            cli.checkpoint(directory, value)
        self.assertEqual((directory / "review.json").read_bytes(), original)
        self.assertEqual([item.name for item in directory.iterdir()], ["review.json"])

    def test_last_saved_answer_can_resume_after_interruption_before_submission_stamp(self):
        value = fixture(); value["reviewerId"] = "fixture-reviewer"
        for label in value["labels"]:
            label.update(expectedOptionId="yes", rationale="Synthetic fixture, previously confirmed answer")
        source = self.private / "unstamped.json"; source.write_text(json.dumps(value))
        code, _d, report, review, text = self.invoke("", source=source)
        self.assertEqual(code, 0); self.assertEqual(report["existingAnswers"], 3)
        self.assertEqual(report["newAnswers"], 0); self.assertIsNotNone(review["reviewedAt"])
        self.assertNotIn("Task 1/3", text)

    def test_source_identity_requires_actual_committed_bytes(self):
        for name in cli.SOURCES:
            target = self.root / name; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(b"fixture")
        with patch.object(cli.subprocess, "check_output", side_effect=["a" * 40, b"changed"]), self.assertRaises(ValueError):
            cli.source_identity(self.root)


if __name__ == "__main__": unittest.main()
