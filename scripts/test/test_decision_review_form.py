import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from decision_runtime.annotations import prepare_review
from decision_runtime.artifacts import sealed
from decision_runtime.contracts import canonical_json, fingerprint
from scripts.lib.decision_review_form import (ASSETS, EXPORT_SCHEMA, IMPORT_SCHEMA, INSTRUCTION_RU,
    LOCALIZATION_SCHEMA, OPTIONS, QUESTION_RU, ROLES, SOURCE_OPTIONS, SOURCE_QUESTION, export_review, initial_blank, make_manifest,
    render_form, sha, validate_localization, verify_any_session, verify_import_values)
from scripts.lib.decision_review_pair import compare_pair
from scripts.lib.decision_review_session import AUTHORITY_FLAGS
from scripts.lib.decision_review_io import checkpoint

ROOT = Path(__file__).resolve().parents[2]
ASSET_DIR = ROOT / "scripts/review-form"
TIME = "2026-10-10T10:00:00.000Z"


def fixture(count=3):
    """Only synthetic source requests; this fixture is never an expert review."""
    cases = []
    for i in range(count):
        prose = f"Это синтетический вопрос для проверки формы {i + 1}."
        if count == 49 and i == 0:
            prose = " ".join([prose] * 32)
        request = {"schemaVersion": "agat.decision.v1", "id": f"form-fixture-{i}", "kind": "choice",
                   "question": SOURCE_QUESTION,
                   "state": f"Title:\nСинтетическое обращение {i + 1}\n\nQuestion:\n{prose}\n\n$ printf '<script>not executed</script>'",
                   "options": copy.deepcopy(SOURCE_OPTIONS)}
        cases.append({"id": request["id"], "groupId": f"form-fixture-group-{i}", "family": "classification",
                      "provenance": {"kind": "synthetic", "sourceId": f"form-fixture-source-{i}",
                                     "reference": "Synthetic UI fixture, never real human evidence."}, "request": request})
    blank = prepare_review({"schemaVersion": "agat.decision.pool.v1", "id": "review-form-fixture", "cases": cases}, "form-fixture-seed")
    translated = []
    for case, label in zip(cases, blank["labels"]):
        parts = re.split(r"\n{2,}", case["request"]["state"].split("\n\nQuestion:\n", 1)[1])
        translated.append({"id": case["id"], "inputSha256": label["inputSha256"], "title": case["request"]["state"].split("\n")[1],
                           "paragraphs": [{"sourceSha256": sha(p.encode()), "kind": "text" if i == 0 else "code", "text": p}
                                          for i, p in enumerate(parts)]})
    localization = {"schemaVersion": LOCALIZATION_SCHEMA, "language": "ru", "poolSha256": blank["poolSha256"],
                    "question": QUESTION_RU, "instruction": INSTRUCTION_RU, "options": copy.deepcopy(OPTIONS), "cases": translated}
    return blank, localization


def bundle_fixture(count=3):
    blank, localization = fixture(count)
    initial_raw = (canonical_json(blank) + "\n").encode()
    localization_raw = (canonical_json(localization) + "\n").encode()
    assets = {name: (ASSET_DIR / name).read_text() for name in ASSETS}
    manifest = make_manifest(blank, sha(initial_raw), localization, sha(localization_raw), assets)
    return {"blank": blank, "localization": localization, "manifest": manifest}


def exported(bundle, role="first", complete=False):
    m = bundle["manifest"]
    return {"schemaVersion": EXPORT_SCHEMA, "formId": m["formId"], "language": "ru",
            **{key: m[key] for key in ("initialReviewFileSha256", "poolSha256", "splitSeed", "localizationFileSha256")},
            "participantRole": role, "participantName": "Синтетический участник" if complete else "",
            "reviewerId": ROLES[role], "startedAt": TIME, "updatedAt": TIME,
            "submittedAt": TIME if complete else None, "status": "submitted" if complete else "draft",
            "submissionConfirmed": complete, "position": 0,
            "answers": [{"id": c["id"], "inputSha256": c["inputSha256"], "titleRu": c["title"],
                         "optionId": "other" if complete else None, "optionLabelRu": "Прочая помощь" if complete else None,
                         "rationale": "Синтетическое обоснование для теста." if complete else ""} for c in m["cases"]]}


def receipt(bundle, export):
    raw = (json.dumps(export, ensure_ascii=False, indent=2) + "\n").encode()
    review = export_review(export, bundle["manifest"], bundle["blank"], bundle["manifest"]["initialReviewFileSha256"])
    output_raw = (canonical_json(review) + "\n").encode()
    remaining = sum(x["expectedOptionId"] is None for x in review["labels"])
    submitted = export["status"] == "submitted"
    report = sealed({"schemaVersion": IMPORT_SCHEMA, "status": "completed" if submitted else "partial",
        "startedAt": export["startedAt"], "finishedAt": export["updatedAt"], "endReason": "submitted" if submitted else "quit",
        "sourceCommit": "a" * 40, "sourceFiles": {"scripts/import-decision-review-form.py": "b" * 64},
        "inputReviewFileSha256": bundle["manifest"]["initialReviewFileSha256"], "outputReviewFileSha256": sha(output_raw),
        "poolSha256": bundle["blank"]["poolSha256"], "reviewerId": export["reviewerId"], "existingAnswers": 0,
        "newAnswers": len(review["labels"]) - remaining, "revisedAnswers": 0, "clearedAnswers": 0,
        "remainingAnswers": remaining, "submissionConfirmed": submitted, "modelCalls": 0,
        "qualification": "not_assessed", "failureType": None, **{flag: False for flag in AUTHORITY_FLAGS},
        "exportRawUtf8": raw.decode(), "exportFileSha256": sha(raw), "manifest": bundle["manifest"],
        "importedAt": "2026-10-10T10:01:00.000Z"})
    return report, review


class FormTest(unittest.TestCase):
    def setUp(self):
        self.bundle = bundle_fixture()

    def test_render_is_self_contained_and_neutralizes_source_markup(self):
        b = self.bundle
        html, manifest = render_form(b["blank"], b["localization"], b["manifest"]["initialReviewFileSha256"],
                                     b["manifest"]["localizationFileSha256"], ASSET_DIR)
        self.assertEqual(manifest, b["manifest"])
        self.assertIn('<html lang="ru">', html); self.assertIn("connect-src 'none'", html)
        self.assertEqual(html.count("<script"), 2)
        self.assertNotIn("<script>not executed</script>", html)
        self.assertIn("\\u003cscript\\u003e", html)
        self.assertNotRegex(html, r'<(?:script|link)[^>]+(?:src|href)="https?')
        self.assertTrue(all(x["expectedOptionId"] is None for x in b["blank"]["labels"]))

    def test_translation_must_cover_every_paragraph_and_keep_technical_quotes_exact(self):
        b = self.bundle
        mutations = [lambda v: v["cases"].reverse(), lambda v: v["cases"][0]["paragraphs"].pop(),
                     lambda v: v["cases"][0]["paragraphs"][1].update(text="changed command"),
                     lambda v: v["cases"][0]["paragraphs"][0].update(text="Untranslated source prose"),
                     lambda v: v["cases"][0].update(title="English title"),
                     lambda v: v["cases"][0].update(inputSha256="0" * 64),
                     lambda v: v["options"][0].update(description="Изменённая рубрика")]
        for mutation in mutations:
            value = copy.deepcopy(b["localization"]); mutation(value)
            with self.subTest(mutation=mutation), self.assertRaises(Exception): validate_localization(value, b["blank"])

    def test_generator_refuses_owned_or_labelled_inputs_and_unsupported_rubric(self):
        for mutation in [lambda v: v.update(reviewerId=ROLES["first"]),
                         lambda v: v["labels"][0].update(expectedOptionId="other", rationale="Тест"),
                         lambda v: v["pool"]["cases"][0]["request"]["options"].reverse()]:
            value = copy.deepcopy(self.bundle["blank"]); mutation(value)
            with self.assertRaises(Exception): initial_blank(value)

    def test_changed_source_rubric_is_refused_even_with_consistent_new_input_pins(self):
        for field in ("description", "question"):
            pool = copy.deepcopy(self.bundle["blank"]["pool"])
            request = pool["cases"][0]["request"]
            if field == "description": request["options"][0][field] = "A different meaning with the same option ID."
            else: request[field] = "A different classification question."
            value = prepare_review(pool, "repinned-fixture")
            with self.subTest(field=field), self.assertRaisesRegex(Exception, "five-category"):
                initial_blank(value)

    def test_incomplete_browser_text_survives_export_but_never_becomes_a_canonical_answer(self):
        export = exported(self.bundle)
        export["answers"][0].update(optionId="incident", optionLabelRu="Инцидент")
        export["answers"][1].update(rationale="Ещё не выбран вариант.")
        review = export_review(export, self.bundle["manifest"], self.bundle["blank"], self.bundle["manifest"]["initialReviewFileSha256"])
        self.assertTrue(all(x["expectedOptionId"] is None for x in review["labels"]))
        self.assertIsNone(review["reviewedAt"])
        self.assertEqual(export["answers"][0]["optionId"], "incident")

    def test_complete_draft_requires_a_separate_submission(self):
        export = exported(self.bundle, complete=True)
        export.update(status="draft", submissionConfirmed=False, submittedAt=None)
        report, review = receipt(self.bundle, export)
        verification = verify_import_values(report, self.bundle["blank"], review,
            input_sha=report["inputReviewFileSha256"], output_sha=report["outputReviewFileSha256"])
        self.assertFalse(verification["completeReviewArtifact"]); self.assertTrue(verification["allAnswersPresent"])
        self.assertIsNone(review["reviewedAt"])

    def test_submitted_import_preserves_the_original_pool_and_uses_russian_answers(self):
        report, review = receipt(self.bundle, exported(self.bundle, complete=True))
        details = verify_import_values(report, self.bundle["blank"], review,
            input_sha=report["inputReviewFileSha256"], output_sha=report["outputReviewFileSha256"])
        self.assertTrue(details["completeReviewArtifact"])
        self.assertEqual(review["pool"], self.bundle["blank"]["pool"])
        self.assertEqual(review["splitSeed"], self.bundle["blank"]["splitSeed"])
        self.assertFalse(details["translationEquivalenceVerified"])
        self.assertTrue(all(report[flag] is False for flag in AUTHORITY_FLAGS))

    def test_export_refuses_rebinding_missing_duplicate_or_foreign_answers_and_english_rationale(self):
        mutations = [lambda v: v.update(formId="0" * 64), lambda v: v.update(poolSha256="0" * 64),
                     lambda v: v.update(localizationFileSha256="0" * 64), lambda v: v.update(language="en"),
                     lambda v: v.update(reviewerId="another-reviewer"), lambda v: v.update(participantRole=["first"]),
                     lambda v: v.update(position=True), lambda v: v.update(submissionConfirmed=False),
                     lambda v: v.update(submittedAt=None), lambda v: v.update(participantName=" "),
                     lambda v: v["answers"].reverse(), lambda v: v["answers"].pop(),
                     lambda v: v["answers"].__setitem__(1, copy.deepcopy(v["answers"][0])),
                     lambda v: v["answers"][0].update(optionId="default"),
                     lambda v: v["answers"][0].update(optionLabelRu="Incident"),
                     lambda v: v["answers"][0].update(rationale="English rationale"),
                     lambda v: v["answers"][0].update(rationale="я" * 4001), lambda v: v.update(humanExecutionVerified=True)]
        for mutate in mutations:
            value = exported(self.bundle, complete=True); mutate(value)
            with self.subTest(mutation=mutate), self.assertRaises(Exception):
                export_review(value, self.bundle["manifest"], self.bundle["blank"], self.bundle["manifest"]["initialReviewFileSha256"])

    def test_receipt_reconciles_the_raw_export_answers_counts_authority_and_timestamps(self):
        report, review = receipt(self.bundle, exported(self.bundle, complete=True))
        mutations = [lambda v: v.update(exportFileSha256="0" * 64), lambda v: v.update(newAnswers=2),
                     lambda v: v.update(humanExecutionVerified=True), lambda v: v.update(importedAt="2026-10-10T09:00:00Z"),
                     lambda v: v.update(exportRawUtf8=v["exportRawUtf8"].replace("Синтетическое обоснование", "Другое обоснование")),
                     lambda v: v.update(modelCalls=1), lambda v: v.update(submissionConfirmed=False)]
        for mutation in mutations:
            changed = {k: copy.deepcopy(v) for k, v in report.items() if k != "sha256"}; mutation(changed); changed = sealed(changed)
            with self.subTest(mutation=mutation), self.assertRaises(Exception):
                verify_import_values(changed, self.bundle["blank"], review, input_sha=report["inputReviewFileSha256"], output_sha=report["outputReviewFileSha256"])

    def test_existing_pair_comparison_accepts_two_imported_browser_reviews_without_authority(self):
        with tempfile.TemporaryDirectory() as temp:
            bindings = []
            for role in ROLES:
                path = Path(temp) / role; path.mkdir()
                report, review = receipt(self.bundle, exported(self.bundle, role, complete=True))
                files = [(path / "session.json", json.dumps(report).encode()),
                         (path / "initial.json", (canonical_json(self.bundle["blank"]) + "\n").encode()),
                         (path / "review.json", (canonical_json(review) + "\n").encode())]
                for name, raw in files: name.write_bytes(raw)
                bindings.append(tuple(x for name, raw in files for x in (name, sha(raw))))
            result = compare_pair(*bindings)
            self.assertEqual(result["agreementCount"], 3); self.assertEqual(result["disagreementCount"], 0)
            self.assertEqual(result["referenceLabelsCreated"], 0); self.assertFalse(result["humanExecutionVerified"])
            self.assertTrue(all(v["reviewInterface"] == "offline_browser_ru" for v in result["verifications"]))


class FormCliTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.private = self.root / "docs/private"; self.private.mkdir(parents=True)
        self.bundle = bundle_fixture()
        spec = importlib.util.spec_from_file_location("review_form_import_cli", ROOT / "scripts/import-decision-review-form.py")
        self.cli = importlib.util.module_from_spec(spec); spec.loader.exec_module(self.cli)
        self.identity = ("a" * 40, {name: "b" * 64 for name in self.cli.SOURCES})

    def invoke(self, export, name="output", broken_pin=False):
        files = {"export": export, "manifest": self.bundle["manifest"], "input-review": self.bundle["blank"]}
        args = []
        for key, value in files.items():
            path = self.private / (key + ".json")
            raw = (canonical_json(value) + "\n").encode(); path.write_bytes(raw)
            args += ["--" + key, str(path), "--" + key + "-file-sha256", "0" * 64 if broken_pin and key == "export" else sha(raw)]
        directory = self.private / name; args += ["--output-dir", str(directory)]
        with patch.object(self.cli, "ROOT", self.root), patch.object(self.cli, "source_identity", return_value=self.identity):
            code = self.cli.main(args)
        return code, directory

    def test_import_cli_output_verifies_with_existing_artifact_bindings(self):
        code, directory = self.invoke(exported(self.bundle, complete=True))
        self.assertEqual(code, 0)
        session = directory / "session.json"; initial = self.private / "input-review.json"; output = directory / "review.json"
        verified = verify_any_session(session, sha(session.read_bytes()), initial, sha(initial.read_bytes()), output, sha(output.read_bytes()))
        self.assertTrue(verified["completeReviewArtifact"])
        self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)

    def test_bad_export_pin_or_incomplete_final_never_creates_an_output_directory(self):
        code, directory = self.invoke(exported(self.bundle, complete=True), broken_pin=True)
        self.assertEqual(code, 1); self.assertFalse(directory.exists())
        export = exported(self.bundle, complete=True); export["answers"][0]["rationale"] = ""
        code, directory = self.invoke(export)
        self.assertEqual(code, 1); self.assertFalse(directory.exists())

    def test_import_does_not_reuse_an_existing_directory(self):
        code, directory = self.invoke(exported(self.bundle))
        self.assertEqual(code, 0); original = (directory / "review.json").read_bytes()
        self.assertEqual(self.invoke(exported(self.bundle, complete=True))[0], 1)
        self.assertEqual((directory / "review.json").read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
