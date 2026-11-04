import copy
import ast
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
from scripts.lib import decision_review_io as review_io

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

    def test_edit_can_revisit_own_confirmed_answer_without_changing_source_or_options(self):
        original = fixture(); saved = []; output = Terminal()
        answers = "1\nfixture initial\ny\n:edit 1\n2\nfixture correction\ny\n:quit\n"
        result, reason = interact(original, Terminal(answers), output, lambda value: saved.append(copy.deepcopy(value)))
        self.assertEqual(reason, "quit"); self.assertEqual(result["labels"][0]["expectedOptionId"], "no")
        self.assertEqual(result["labels"][0]["rationale"], "fixture correction")
        self.assertEqual(len(saved), 2); self.assertEqual(saved[0]["labels"][0]["expectedOptionId"], "yes")
        self.assertEqual(result["pool"], original["pool"]); self.assertIn("Your confirmed answer: yes", output.getvalue())

    def test_unconfirmed_edit_and_skip_preserve_previous_answer(self):
        original = fixture(); original["labels"][0].update(expectedOptionId="yes", rationale="fixture original")
        for ending in ("2\nfixture correction\nn\n:quit\n", "2\nfixture correction\n:quit\n", ":skip\n:quit\n"):
            with self.subTest(ending=ending):
                saved = []; result, _reason = interact(copy.deepcopy(original), Terminal(":edit 1\n" + ending), Terminal(), saved.append)
                self.assertEqual(result["labels"][0], original["labels"][0]); self.assertFalse(saved)

    def test_clear_requires_confirmation_and_clears_label_and_rationale_together(self):
        original = fixture(); original["labels"][0].update(expectedOptionId="yes", rationale="fixture original")
        saved = []; result, _ = interact(copy.deepcopy(original), Terminal(":clear 1\nn\n:quit\n"), Terminal(), saved.append)
        self.assertEqual(result["labels"][0], original["labels"][0]); self.assertFalse(saved)
        result, _ = interact(copy.deepcopy(original), Terminal(":clear 1\ny\n:quit\n"), Terminal(), saved.append)
        self.assertIsNone(result["labels"][0]["expectedOptionId"]); self.assertIsNone(result["labels"][0]["rationale"])
        self.assertEqual(len(saved), 1)

    def test_invalid_edit_clear_and_incomplete_submit_never_create_labels(self):
        saved = []; output = Terminal()
        answers = ":edit 0\n:edit 4\n:edit １\n:edit 999999999999\n:clear true\n:clear 1 extra\n:clear 1\n:submit\n:quit\n"
        result, reason = interact(fixture(), Terminal(answers), output, saved.append)
        self.assertEqual(reason, "quit"); self.assertFalse(saved)
        self.assertTrue(all(label["expectedOptionId"] is None for label in result["labels"]))
        self.assertIn("Cannot submit: 3 tasks remain blank", output.getvalue())

    def test_all_confirmed_answers_require_a_separate_confirmed_submission(self):
        answered = fixture()
        for label in answered["labels"]: label.update(expectedOptionId="yes", rationale="fixture only")
        for answers, expected_reason in (("", "eof"), (":quit\n", "quit"), (":submit\nn\n:quit\n", "quit"), (":submit\ny\n", "submitted")):
            with self.subTest(answers=answers):
                result, reason = interact(copy.deepcopy(answered), Terminal(answers), Terminal(), lambda _value: self.fail("No label changes"))
                self.assertEqual(reason, expected_reason); self.assertIsNone(result["reviewedAt"])

    def test_editing_the_last_task_of_a_pass_keeps_skipped_tasks_unanswered(self):
        saved = []; output = Terminal()
        result, reason = interact(fixture(), Terminal(":skip\n:edit 3\n1\nfixture only\ny\n:submit\n:quit\n"), output, saved.append)
        self.assertEqual(reason, "quit"); self.assertEqual(len(saved), 1)
        self.assertTrue(all(label["expectedOptionId"] is None for label in result["labels"][:2]))
        self.assertIn("Blank tasks: 1, 2", output.getvalue())

    def test_own_rationale_controls_are_escaped_when_revisiting_an_answer(self):
        original = fixture(); original["labels"][0].update(expectedOptionId="yes", rationale="fixture\x1b[2J\u202e")
        output = Terminal(); interact(original, Terminal(":edit 1\n:quit\n"), output, lambda _value: self.fail("No changes"))
        self.assertNotIn("\x1b", output.getvalue()); self.assertNotIn("\u202e", output.getvalue())
        self.assertIn("fixture\\x1b[2J\\u202e", output.getvalue())


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
        report = verify_seal(json.loads((directory / "session.json").read_text()), cli.SESSION_SCHEMA) if directory.exists() else None
        review = json.loads((directory / "review.json").read_text()) if (directory / "review.json").is_file() else None
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
        code, final_dir, report, final, text = self.invoke("2\nfixture boolean reason\ny\n1\nfixture score reason\ny\n:submit\ny\n", name="resume", source=directory / "review.json")
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
        changed = ("c" * 40, {**self.identity[1], "scripts/lib/decision_blind_review.py": "c" * 64})
        code, _d, report, review, _t = self.invoke("1\nfixture\ny\n", identity=[self.identity, changed])
        self.assertEqual(code, 1); self.assertEqual(report["status"], "failed")
        self.assertEqual(report["failureType"], "ValueError")
        self.assertTrue(all(x["expectedOptionId"] is None for x in review["labels"]))

    def test_document_only_head_advance_does_not_abort_the_same_frozen_code(self):
        advanced = ("c" * 40, self.identity[1])
        code, _d, report, review, _t = self.invoke("1\nfixture\ny\n:quit\n", identity=[self.identity, advanced, advanced])
        self.assertEqual(code, 2); self.assertEqual(report["status"], "partial")
        self.assertEqual(report["sourceCommit"], self.identity[0]); self.assertEqual(report["sourceFiles"], self.identity[1])
        self.assertEqual(review["labels"][0]["expectedOptionId"], "yes")

    def test_transitive_dependency_change_aborts_before_saving_a_label(self):
        changed = ("c" * 40, {**self.identity[1], "scripts/lib/decision_shadow_sli.py": "c" * 64})
        code, _d, report, review, _t = self.invoke("1\nfixture\ny\n", identity=[self.identity, changed])
        self.assertEqual(code, 1); self.assertEqual(report["status"], "failed")
        self.assertTrue(all(label["expectedOptionId"] is None for label in review["labels"]))

    def test_recorded_sources_cover_every_static_local_import_and_package_initializer(self):
        pending = ["scripts/review-decision-pool.py"]; seen = set()
        while pending:
            name = pending.pop()
            if name in seen: continue
            seen.add(name); parts = list(Path(name).with_suffix("").parts)
            for index in range(1, len(parts)):
                initializer = Path(*parts[:index]) / "__init__.py"
                if (ROOT / initializer).is_file(): pending.append(initializer.as_posix())
            for node in ast.walk(ast.parse((ROOT / name).read_text())):
                modules = []
                if isinstance(node, ast.Import): modules = [alias.name.split(".") for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    prefix = parts[:-node.level] if node.level else []
                    module = prefix + (node.module.split(".") if node.module else [])
                    modules = [module, *[module + alias.name.split(".") for alias in node.names]]
                for module in modules:
                    path = Path(*module).with_suffix(".py") if module else None
                    if path and (ROOT / path).is_file(): pending.append(path.as_posix())
        self.assertEqual(set(cli.SOURCES), seen)

    def test_startup_checkpoint_failure_has_failed_receipt_without_usable_review(self):
        with patch.object(cli, "checkpoint", side_effect=OSError("fixture startup failure")):
            code, _d, report, review, text = self.invoke("1\nfixture\ny\n")
        self.assertEqual(code, 1); self.assertEqual(report["status"], "failed")
        self.assertEqual(report["failureType"], "OSError"); self.assertIsNone(review)
        self.assertIsNone(report["outputReviewFileSha256"]); self.assertFalse(text)

    def test_submission_receipt_write_failure_cannot_claim_completed_status(self):
        original = cli.write_json_new; count = 0
        def failing_once(*args):
            nonlocal count
            count += 1
            if count == 1: raise OSError("fixture submission failure")
            return original(*args)
        with patch.object(cli, "write_json_new", side_effect=failing_once):
            code, _d, report, review, _text = self.invoke("1\nfixture\ny\n2\nfixture\ny\n1\nfixture\ny\n:submit\ny\n")
        self.assertEqual(code, 1); self.assertEqual(report["status"], "failed")
        self.assertEqual(report["failureType"], "OSError"); self.assertIsNotNone(review["reviewedAt"])
        self.assertEqual(report["newAnswers"], 3)

    def test_atomic_replace_failure_does_not_corrupt_existing_checkpoint(self):
        directory = self.private / "checkpoint"; directory.mkdir(mode=0o700)
        cli.checkpoint(directory, fixture())
        original = (directory / "review.json").read_bytes()
        value = fixture(); value["reviewerId"] = "fixture-reviewer"
        with patch.object(review_io.os, "replace", side_effect=OSError("fixture interruption")), self.assertRaises(OSError):
            cli.checkpoint(directory, value)
        self.assertEqual((directory / "review.json").read_bytes(), original)
        self.assertEqual([item.name for item in directory.iterdir()], ["review.json"])

    def test_last_saved_answer_can_resume_after_interruption_before_submission_stamp(self):
        value = fixture(); value["reviewerId"] = "fixture-reviewer"
        for label in value["labels"]:
            label.update(expectedOptionId="yes", rationale="Synthetic fixture, previously confirmed answer")
        source = self.private / "unstamped.json"; source.write_text(json.dumps(value))
        code, _d, report, review, text = self.invoke(":submit\ny\n", source=source)
        self.assertEqual(code, 0); self.assertEqual(report["existingAnswers"], 3)
        self.assertEqual(report["newAnswers"], 0); self.assertIsNotNone(review["reviewedAt"])
        self.assertNotIn("Task 1/3", text)

    def test_complete_draft_on_eof_remains_unsubmitted_and_can_resume_with_edits(self):
        code, directory, report, draft, _ = self.invoke("1\nfixture\ny\n2\nfixture\ny\n1\nfixture\ny\n")
        self.assertEqual(code, 2); self.assertEqual(report["status"], "partial")
        self.assertEqual(report["remainingAnswers"], 0); self.assertFalse(report["submissionConfirmed"])
        self.assertIsNone(draft["reviewedAt"])
        with self.assertRaises(ValueError): finalize_reviews(draft, draft)
        original = (directory / "review.json").read_bytes()
        code, _d, report, final, _ = self.invoke(":edit 1\n2\nfixture revision\ny\n:submit\ny\n", name="edited", source=directory / "review.json")
        self.assertEqual(code, 0); self.assertTrue(report["submissionConfirmed"])
        self.assertEqual((report["newAnswers"], report["revisedAnswers"], report["clearedAnswers"]), (0, 1, 0))
        self.assertEqual(final["labels"][0]["expectedOptionId"], "no")
        self.assertEqual((directory / "review.json").read_bytes(), original)

    def test_clearing_existing_answers_has_explicit_nonnegative_net_counts(self):
        code, directory, _report, _review, _ = self.invoke("1\nfixture\ny\n:quit\n")
        self.assertEqual(code, 2)
        code, _d, report, draft, _ = self.invoke(":clear 1\ny\n:quit\n", name="cleared", source=directory / "review.json")
        self.assertEqual(code, 2)
        self.assertEqual((report["existingAnswers"], report["newAnswers"], report["revisedAnswers"], report["clearedAnswers"], report["remainingAnswers"]), (1, 0, 0, 1, 3))
        self.assertIsNone(draft["labels"][0]["rationale"])

    def test_edit_checkpoint_failure_keeps_original_persisted_answer(self):
        _, directory, _report, _review, _ = self.invoke("1\nfixture\ny\n:quit\n")
        original = cli.checkpoint; calls = 0
        def fail_second(*args):
            nonlocal calls
            calls += 1
            if calls == 2: raise OSError("fixture edit checkpoint failure")
            return original(*args)
        with patch.object(cli, "checkpoint", side_effect=fail_second):
            code, _d, report, draft, _ = self.invoke(":edit 1\n2\nfixture revision\ny\n", name="failed-edit", source=directory / "review.json")
        self.assertEqual(code, 1); self.assertEqual(report["status"], "failed")
        self.assertEqual(draft["labels"][0]["expectedOptionId"], "yes")
        self.assertIsNone(draft["reviewedAt"]); self.assertFalse(report["submissionConfirmed"])

    def test_source_identity_requires_actual_committed_bytes(self):
        for name in cli.SOURCES:
            target = self.root / name; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(b"fixture")
        with patch.object(review_io.subprocess, "check_output", side_effect=["a" * 40, b"changed"]), self.assertRaises(ValueError):
            cli.source_identity(self.root)


if __name__ == "__main__": unittest.main()
