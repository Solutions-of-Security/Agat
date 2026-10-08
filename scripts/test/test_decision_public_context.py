import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from decision_runtime.annotations import group_split
from decision_runtime.artifacts import sealed, verify_seal
from decision_runtime.contracts import Request, canonical_json
from scripts.lib import decision_public_context as context
from scripts.test import test_decision_public_sources as fixtures

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("public_context_cli", ROOT / "scripts/profile-public-support-context.py")
cli = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(cli)


def fixture_pool():
    value = fixtures.PublicSourcesTest(); value.setUp()
    rows = [fixtures.question("ru.stackoverflow", index, index, f"<p>Synthetic source fixture {index}, never gold.</p>") for index in range(10, 30)]
    pages = [fixtures.raw({"items": rows, "has_more": False}), fixtures.raw(value.values[1])]
    manifest = copy.deepcopy(value.manifest)
    manifest["pages"][0].update(bytes=len(pages[0]), sha256=hashlib.sha256(pages[0]).hexdigest())
    artifacts = value.build(manifest=manifest, snapshots=pages)
    return value, sealed(manifest), pages, artifacts


def profile_fixture():
    manifest = {"repository": fixtures.MODEL_REPOSITORY, "revision": fixtures.MODEL_REVISION,
                "artifactSha256": "c" * 64, "files": {"tokenizer.json": "d" * 64}}
    profile = {"schemaVersion": "agat.decision.v1", "runtimeVersion": "0.12.3", "model": {**{k:v for k,v in manifest.items() if k != "files"},
        "tokenizerSha256": "d" * 64, "maxInputTokens": 2048, "allocatorCacheLimitBytes": 128 * 1024**2,
        "allocatorWiredLimitBytes": 4096 * 1024**2}}
    return profile, manifest


def token_fixture(pool):
    return [{"id": c["id"], "inputSha256": Request.from_dict(c["request"]).input_sha256,
             "partTokens": [100, 100], "inputTokens": 200, "labelContinuationsVerified": True}
            for c in context.development_cases(pool)]


class ContextTest(unittest.TestCase):
    def test_whole_frozen_development_split_keeps_overlimit_inputs_and_exact_text(self):
        _f, _a, _pages, artifacts = fixture_pool(); pool = artifacts["pool.json"]
        selected = context.development_cases(pool); rows = token_fixture(pool); profile, manifest = profile_fixture()
        context.verify_profile(profile, manifest)
        self.assertGreaterEqual(len(selected), 2)
        rows[0].update(partTokens=[1949, 100], inputTokens=2049)
        result = context.context_result(pool, selected, rows, profile)
        self.assertEqual(result["developmentCases"], len(selected))
        self.assertEqual(result["contextTooLongCases"], 1)
        self.assertEqual(result["inputs"][0]["request"], selected[0]["request"])
        self.assertEqual(result["inputs"][0]["contextExclusionReason"], "context_too_long")
        self.assertFalse(result["inputTruncationApplied"])
        self.assertEqual(result["proposedDiagnosticSchedule"]["scheduledCases"], len(selected))
        for item in result["inputs"]:
            self.assertEqual(group_split(fixtures.SPLIT_SEED, item["groupId"]), "development")
        for key in ("calibrationRequestsTokenized", "holdoutRequestsTokenized", "referenceLabels", "predictions", "modelCalls"):
            self.assertEqual(result[key], 0)
        self.assertFalse(result["routingEnabled"]); self.assertFalse(result["sloAccepted"])

    def test_token_inventory_cannot_omit_reorder_rebind_or_invent_lengths(self):
        pool = fixture_pool()[3]["pool.json"]; selected = context.development_cases(pool)
        profile, _manifest = profile_fixture(); original = token_fixture(pool)
        for mutation in (lambda r: r.pop(), lambda r: r.reverse(), lambda r: r[0].update(inputSha256="0" * 64),
                         lambda r: r[0].update(inputTokens=True), lambda r: r[0].update(inputTokens=201),
                         lambda r: r[0].update(partTokens=[True, 100]), lambda r: r[0].update(partTokens=[0, 200]),
                         lambda r: r[0].update(labelContinuationsVerified=1), lambda r: r[0].update(expectedOptionId="other")):
            rows = copy.deepcopy(original); mutation(rows)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): context.context_result(pool, selected, rows, profile)
        with self.assertRaises(ValueError): context.context_result(pool, selected[1:], original[1:], profile)

    def test_profile_rejects_foreign_revision_tokenizer_and_resource_envelope(self):
        profile, manifest = profile_fixture()
        for key, value in (("revision", "0" * 40), ("tokenizerSha256", "e" * 64), ("maxInputTokens", 4096),
                           ("allocatorWiredLimitBytes", 8192 * 1024**2), ("allocatorCacheLimitBytes", 0)):
            changed = copy.deepcopy(profile); changed["model"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): context.verify_profile(changed, manifest)
        profile["runtimeVersion"] = "fixture-other-version"
        with self.assertRaises(ValueError): context.verify_profile(profile, manifest)

    def test_reconstruction_checks_actual_raw_pages_and_every_import_output(self):
        fixture, acquisition, pages, artifacts = fixture_pool()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); imported_dir = root / "import"; acquisition_dir = root / "acquisition"
            imported_dir.mkdir(); acquisition_dir.mkdir()
            for item, raw in zip(acquisition["pages"], pages): (acquisition_dir / item["file"]).write_bytes(raw)
            (acquisition_dir / "model-revision.json").write_bytes(fixtures.raw(fixture.model))
            acquisition_path = acquisition_dir / "acquisition.json"; acquisition_path.write_bytes(fixtures.raw(acquisition))
            for name, value in artifacts.items(): (imported_dir / name).write_bytes(fixtures.raw(value))
            imported = sealed({"schemaVersion": context.IMPORT_SCHEMA, "acquisitionFileSha256": hashlib.sha256(acquisition_path.read_bytes()).hexdigest(),
                "acquisitionSha256": acquisition["sha256"], "sourceCommit": "a" * 40, "sourceFiles": acquisition["sourceFiles"],
                "outputFileSha256": {name: hashlib.sha256((imported_dir / name).read_bytes()).hexdigest() for name in artifacts},
                "routingEnabled": False, "qualification": "not_assessed", "modelCalls": 0})
            path = imported_dir / "import.json"; path.write_bytes(fixtures.raw(imported)); pin = hashlib.sha256(path.read_bytes()).hexdigest()
            with patch.object(context, "historical_sources") as historical:
                result = context.reconstruct(root, path, pin, acquisition_path)
                self.assertEqual(result[2], artifacts["pool.json"]); self.assertEqual(historical.call_count, 2)
                (imported_dir / "review.first.blank.json").write_bytes(b"changed")
                with self.assertRaises(ValueError): context.reconstruct(root, path, pin, acquisition_path)
                (imported_dir / "review.first.blank.json").write_bytes(fixtures.raw(artifacts["review.first.blank.json"]))
                (acquisition_dir / acquisition["pages"][0]["file"]).write_bytes(b"changed")
                with self.assertRaises(ValueError): context.reconstruct(root, path, pin, acquisition_path)

    def test_historical_source_binding_checks_commit_bytes(self):
        source = fixture_pool()[1]
        with patch.object(context.subprocess, "check_output", return_value=b"changed"), self.assertRaises(ValueError):
            context.historical_sources(ROOT, source)

    def test_empty_or_excessive_development_split_fails_without_sampling(self):
        pool = fixture_pool()[3]["pool.json"]
        development = context.development_cases(pool)[0]
        other = next(c for c in pool["cases"] if group_split(fixtures.SPLIT_SEED, c["groupId"]) != "development")
        empty = {**pool, "cases": [other]}
        with self.assertRaises(ValueError): context.development_cases(empty)
        many = []
        for index in range(61):
            item = copy.deepcopy(development); item["id"] = item["request"]["id"] = f"fixture-{index}"
            many.append(item)
        with self.assertRaises(ValueError): context.development_cases({**pool, "cases": many})


class ContextCliTest(unittest.TestCase):
    def test_actual_cli_filters_child_requests_and_preserves_private_zero_inference_result(self):
        _fixture, acquisition, _pages, artifacts = fixture_pool(); pool = artifacts["pool.json"]
        profile, manifest = profile_fixture()
        identity = ("a" * 40, {"fixture.py": "b" * 64})
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); private = root / "docs/private"; private.mkdir(parents=True)
            requirements = root / "decision_runtime/requirements-mlx.txt"; requirements.parent.mkdir(); requirements.write_text("fixture==1\n")
            profile_path = private / "profile.json"; profile_path.write_bytes(fixtures.raw(profile))
            manifest_path = private / "manifest.json"; manifest_path.write_text("fixture model manifest")
            imported = {"sha256": "c" * 64, "acquisitionFileSha256": "d" * 64}
            output = private / "context"
            args = ["--import-receipt", str(private / "import.json"), "--import-file-sha256", "e" * 64,
                    "--acquisition", str(private / "acquisition.json"), "--profile", str(profile_path),
                    "--profile-file-sha256", hashlib.sha256(profile_path.read_bytes()).hexdigest(),
                    "--manifest", str(manifest_path), "--runtime-python", "/fixture-python", "--output-dir", str(output)]
            def tokenize(command, **kwargs):
                child_requests = json.loads(kwargs["input"])
                self.assertEqual(child_requests, [c["request"] for c in context.development_cases(pool)])
                self.assertTrue(all(group_split(fixtures.SPLIT_SEED, c["groupId"]) == "development"
                                    for c in pool["cases"] if c["request"] in child_requests))
                self.assertIn("local_files_only=True", command[3]); self.assertIn("trust_remote_code=False", command[3])
                self.assertEqual(kwargs["env"]["HF_HUB_OFFLINE"], "1")
                payload = {"rows": token_fixture(pool), "environment": {"python": "3.13.12", "machine": "arm64", "packages": {"fixture": "1"}}}
                return subprocess.CompletedProcess(command, 0, fixtures.raw(payload), b"")
            with patch.object(cli, "ROOT", root), patch.object(cli, "source_identity", return_value=identity), \
                    patch.object(cli, "reconstruct", return_value=(imported, acquisition, pool)), \
                    patch.object(cli, "verify_manifest", return_value=(manifest, private)), patch.object(cli.subprocess, "run", side_effect=tokenize):
                self.assertEqual(cli.main(args), 0)
                report = verify_seal(json.loads((output / "context-profile.json").read_text()), context.PROFILE_SCHEMA)
                self.assertEqual(report["modelCalls"], 0); self.assertEqual(report["predictions"], 0)
                self.assertEqual(report["profileFileSha256"], hashlib.sha256(profile_path.read_bytes()).hexdigest())
                self.assertEqual(output.stat().st_mode & 0o777, 0o700)
                self.assertEqual((output / "context-profile.json").stat().st_mode & 0o777, 0o600)
                self.assertEqual(cli.main(args), 1)
                args[-1] = str(private / "failed")
                with patch.object(cli.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, b"", b"fixture failure")):
                    self.assertEqual(cli.main(args), 1); self.assertFalse((private / "failed").exists())


if __name__ == "__main__": unittest.main()
