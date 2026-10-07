import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from decision_runtime.annotations import finalize_reviews
from decision_runtime.artifacts import sealed, verify_seal
from decision_runtime.contracts import canonical_json
from scripts.lib.decision_public_sources import (ACQUISITION_SCHEMA, MODEL_REPOSITORY, MODEL_REVISION, MODEL_URL,
    SITES, SOURCE_PATHS, SPLIT_SEED, build_pool, observation_window, source_identity, source_url)

ROOT = Path(__file__).resolve().parents[2]


def load_cli(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


acquire = load_cli("acquire-public-support-sources")
importer = load_cli("import-public-support-pool")


def raw(value):
    return (canonical_json(value) + "\n").encode()


def question(site, number, author, body="<p>Synthetic fixture: a service stopped.</p>"):
    host = SITES[site][0]
    start, _ = observation_window({"startAt": "2026-09-29T00:00:00.000Z", "endAt": "2026-10-07T00:00:00.000Z"})
    return {"question_id": number, "creation_date": int(start) + number, "link": f"https://{host}/questions/{number}/fixture",
        "title": f"Synthetic fixture {number}", "body": body, "content_license": "CC BY-SA 4.0",
        "owner": {"user_id": author, "display_name": "Fixture &amp; author", "link": f"https://{host}/users/{author}/fixture"},
        "tags": ["not-a-reference-label"], "is_answered": True, "score": 99}


class PublicSourcesTest(unittest.TestCase):
    def setUp(self):
        self.model = {"id": MODEL_REPOSITORY, "sha": MODEL_REVISION, "lastModified": "2026-09-20T09:40:04.000Z"}
        self.window = {"startAt": "2026-09-29T00:00:00.000Z", "endAt": "2026-10-07T00:00:00.000Z"}
        self.sites = ["ru.stackoverflow", "askubuntu"]
        # One author's different requests and a cross-site duplicate form one connected group.
        ru = [question(self.sites[0], 1, 1), question(self.sites[0], 2, 1, "<p>Grant access to this account.</p>"), question(self.sites[0], 5, 9)]
        ru[-1].pop("content_license")
        au = [question(self.sites[1], 3, 77, "<p>GRANT   access to this account.</p>"),
              question(self.sites[1], 4, 88, "<p>How do I install a new package?</p>"), question(self.sites[1], 6, 99)]
        au[-1]["owner"].pop("link")
        self.rows = [ru, au]
        self.values = [{"items": rows, "has_more": False} for rows in self.rows]
        self.snapshots = [raw(v) for v in self.values]
        start, end = observation_window(self.window)
        self.manifest = {"schemaVersion": ACQUISITION_SCHEMA, "status": "completed", "capturedAt": "2026-10-08T00:00:00.000Z",
            "window": self.window, "sites": self.sites, "sourceCommit": "a" * 40, "sourceFiles": {s: "b" * 64 for s in SOURCE_PATHS},
            "apiVersion": "2.3", "sort": "creation", "order": "asc", "filter": "withbody", "pageSize": 100,
            "maxPagesPerSite": 5, "pages": [{"site": site, "page": 1, "file": f"{site}-page-1.json",
                "url": source_url(site, 1, start, end), "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
                for site, body in zip(self.sites, self.snapshots)],
            "modelMetadata": {"file": "model-revision.json", "url": MODEL_URL, "bytes": len(raw(self.model)),
                "sha256": hashlib.sha256(raw(self.model)).hexdigest(), "revision": MODEL_REVISION, "lastModified": self.model["lastModified"]},
            "failure": None, "routingEnabled": False, "qualification": "not_assessed"}

    def build(self, manifest=None, snapshots=None, model=None):
        return build_pool(sealed(manifest or self.manifest), snapshots or self.snapshots, model or self.model)

    def changed_page(self, index, value):
        snapshots = list(self.snapshots); snapshots[index] = raw(value)
        manifest = copy.deepcopy(self.manifest)
        manifest["pages"][index].update(bytes=len(snapshots[index]), sha256=hashlib.sha256(snapshots[index]).hexdigest())
        return manifest, snapshots

    def test_no_gold_inference_and_blank_reviews_cannot_finalize(self):
        artifacts = self.build(); pool = artifacts["pool.json"]; ready = verify_seal(artifacts["readiness.json"], "agat.decision.public-support-readiness.v1")
        self.assertEqual(ready["counts"]["includedQuestions"], 4)
        self.assertEqual(ready["counts"]["excludedQuestions"], 2)
        self.assertEqual(ready["counts"]["exclusionsByReason"], {"missing_explicit_supported_license": 1, "missing_author_profile": 1})
        for key in ("labelsVerified", "statisticalIndependenceVerified", "representativeAgatTraffic", "modelTrainingOverlapVerifiedAbsent", "routingEnabled"):
            self.assertIs(ready[key], False)
        self.assertEqual(ready["modelCalls"], 0); self.assertEqual(ready["qualification"], "not_assessed")
        for name in ("review.first.blank.json", "review.second.blank.json"):
            review = artifacts[name]
            self.assertIsNone(review["reviewerId"]); self.assertIsNone(review["reviewedAt"])
            self.assertEqual(review["splitSeed"], SPLIT_SEED)
            for label in review["labels"]:
                self.assertIsNone(label["expectedOptionId"]); self.assertIsNone(label["rationale"])
            for case in review["pool"]["cases"]:
                self.assertNotIn("split", case); self.assertNotIn("expectedOptionId", case)
                self.assertNotIn("not-a-reference-label", case["request"]["state"])
        self.assertEqual(len(pool["cases"]), 4)
        with self.assertRaises(ValueError): finalize_reviews(artifacts["review.first.blank.json"], artifacts["review.second.blank.json"])

    def test_known_dependencies_stay_together_and_attribution_is_in_each_review(self):
        artifacts = self.build(); cases = artifacts["pool.json"]["cases"]
        groups = {c["id"]: c["groupId"] for c in cases}
        self.assertEqual(groups["support-ru-so-1"], groups["support-ru-so-2"])
        self.assertEqual(groups["support-ru-so-2"], groups["support-au-3"])
        self.assertNotEqual(groups["support-au-3"], groups["support-au-4"])
        bindings = artifacts["source-bindings.json"]["bindings"]
        self.assertEqual(len({b["split"] for b in bindings if b["groupId"] == groups["support-au-3"]}), 1)
        self.assertEqual(len({b["inputSha256"] for b in bindings}), 4)
        for case in cases:
            self.assertIn("CC BY-SA 4.0", case["provenance"]["reference"])
            self.assertIn("Fixture & author", case["provenance"]["reference"])
            self.assertIn("/users/", case["provenance"]["reference"])
            self.assertIn("/questions/", case["provenance"]["reference"])

    def test_rehashed_manifest_cannot_change_the_query_qualification_or_model_binding(self):
        for mutate in (lambda m: m.update(status="failed"), lambda m: m.update(routingEnabled=True),
                       lambda m: m.update(qualification="passed"), lambda m: m.update(sort="votes"),
                       lambda m: m.update(pageSize=True), lambda m: m.update(filter="default"),
                       lambda m: m["pages"][0].update(url=m["pages"][0]["url"] + "&tagged=incident"),
                       lambda m: m["pages"][0].update(file="../public.json"),
                       lambda m: m["modelMetadata"].update(revision="c" * 40),
                       lambda m: m["modelMetadata"].update(lastModified="2026-09-21T09:40:04.000Z"),
                       lambda m: m.update(capturedAt="2026-10-06T23:59:59.999Z"),
                       lambda m: m["window"].update(startAt="2026-09-29T00:00:00.001Z")):
            manifest = copy.deepcopy(self.manifest); mutate(manifest)
            with self.subTest(manifest=manifest), self.assertRaises(ValueError): self.build(manifest)
        model = {**self.model, "sha": "c" * 40}
        with self.assertRaises(ValueError): self.build(model=model)

    def test_incomplete_error_and_backoff_pages_never_import(self):
        for change in ({"has_more": True}, {"error_id": 502}, {"backoff": 5}, {"has_more": 0}):
            value = {**self.values[0], **change}; manifest, snapshots = self.changed_page(0, value)
            with self.subTest(change=change), self.assertRaises(ValueError): self.build(manifest, snapshots)
        with self.assertRaises(ValueError): self.build(snapshots=[self.snapshots[0] + b" ", self.snapshots[1]])

    def test_page_gaps_duplicates_order_and_half_open_dates_fail_closed(self):
        start, end = observation_window(self.window)
        for change in (lambda r: r.update(creation_date=int(end)), lambda r: r.update(creation_date=int(start) - 1),
                       lambda r: r.update(link="https://example.org/questions/1/fixture")):
            value = copy.deepcopy(self.values[0]); change(value["items"][0]); manifest, snapshots = self.changed_page(0, value)
            with self.subTest(change=change), self.assertRaises(ValueError): self.build(manifest, snapshots)
        for rows in ([self.rows[0][1], self.rows[0][0]], [self.rows[0][0], self.rows[0][0]]):
            manifest, snapshots = self.changed_page(0, {"items": rows, "has_more": False})
            with self.assertRaises(ValueError): self.build(manifest, snapshots)
        manifest = copy.deepcopy(self.manifest); manifest["pages"][0].update(page=2, file="ru.stackoverflow-page-2.json")
        manifest["pages"][0]["url"] = source_url(self.sites[0], 2, start, end)
        with self.assertRaisesRegex(ValueError, "pagination"): self.build(manifest)

    def test_rendering_preserves_source_text_and_source_ids_not_translation(self):
        value = copy.deepcopy(self.values[0]); value["items"][0].update(title="Сбой &amp; доступ", body="<p>Откройте &lt;config&gt;.</p><pre>  a\n b</pre>")
        manifest, snapshots = self.changed_page(0, value); artifacts = self.build(manifest, snapshots)
        case = artifacts["pool.json"]["cases"][0]
        self.assertEqual(case["request"]["state"], "Title:\nСбой & доступ\n\nQuestion:\nОткройте <config>.\n\n  a\n b")
        binding = artifacts["source-bindings.json"]["bindings"][0]
        self.assertEqual(binding["bodyHtmlSha256"], hashlib.sha256(value["items"][0]["body"].encode()).hexdigest())


class PublicSourcesCliTest(unittest.TestCase):
    setUp = PublicSourcesTest.setUp
    def test_real_cli_pins_private_immutable_outputs_and_rejects_input_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); private = root / "docs/private"; private.mkdir(parents=True)
            for page, body in zip(self.manifest["pages"], self.snapshots): (private / page["file"]).write_bytes(body)
            (private / "model-revision.json").write_bytes(raw(self.model))
            manifest_path = private / "acquisition.json"; manifest_path.write_bytes(raw(sealed(self.manifest)))
            output = private / "pool"
            args = ["--acquisition", str(manifest_path), "--acquisition-file-sha256", hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                    "--output-dir", str(output)]
            with patch.object(importer, "ROOT", root), patch.object(importer, "source_identity", return_value=("a" * 40, self.manifest["sourceFiles"])):
                self.assertEqual(importer.main(args), 0)
                self.assertEqual(output.stat().st_mode & 0o777, 0o700)
                for file in output.iterdir(): self.assertEqual(file.stat().st_mode & 0o777, 0o600)
                before = {p.name: p.read_bytes() for p in output.iterdir()}
                self.assertEqual(importer.main(args), 1)
                self.assertEqual(before, {p.name: p.read_bytes() for p in output.iterdir()})
                receipt = verify_seal(json.loads((output / "import.json").read_text()), "agat.decision.public-support-import.v1")
                for name, pin in receipt["outputFileSha256"].items(): self.assertEqual(hashlib.sha256((output / name).read_bytes()).hexdigest(), pin)
                args[-1] = str(private / "bad-pool"); manifest_path.write_bytes(manifest_path.read_bytes() + b" ")
                self.assertEqual(importer.main(args), 1); self.assertFalse((private / "bad-pool").exists())

    def test_acquire_pages_exact_query_and_stop_on_backoff_or_page_bound(self):
        for response, limit, expected in (({"items": [], "has_more": False}, 1, 0),
                                          ({"items": [], "has_more": True}, 1, 1),
                                          ({"items": [], "has_more": True, "backoff": 5}, 5, 1)):
            with self.subTest(response=response), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary); calls = []; output = root / "docs/private/acquisition"
                def fetch(url):
                    calls.append(url); return raw(self.model if len(calls) == 1 else response)
                args = ["--start-at", self.window["startAt"], "--end-at", self.window["endAt"], "--sites", "askubuntu",
                        "--max-pages-per-site", str(limit), "--output-dir", str(output)]
                with patch.object(acquire, "ROOT", root), patch.object(acquire, "source_identity", return_value=("a" * 40, self.manifest["sourceFiles"])), \
                        patch.object(acquire, "fetch", side_effect=fetch), patch.object(acquire, "utc_now", return_value="2026-10-08T00:00:00.000Z"):
                    self.assertEqual(acquire.main(args), expected)
                self.assertEqual(len(calls), 2); self.assertEqual(calls[0], MODEL_URL)
                query = parse_qs(urlparse(calls[1]).query)
                self.assertEqual(query["sort"], ["creation"]); self.assertEqual(query["order"], ["asc"])
                self.assertNotIn("tagged", query)
                report = verify_seal(json.loads((output / "acquisition.json").read_text()), ACQUISITION_SCHEMA)
                self.assertEqual(len(report["pages"]), 1)
                if expected:
                    with self.assertRaises(ValueError): build_pool(report, [raw(response)], self.model)

    def test_cli_source_drift_and_future_or_fractional_window_create_no_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); output = root / "docs/private/acquisition"
            args = ["--start-at", self.window["startAt"], "--end-at", self.window["endAt"], "--output-dir", str(output)]
            with patch.object(acquire, "ROOT", root), patch.object(acquire, "utc_now", return_value="2026-10-06T00:00:00.000Z"), patch.object(acquire, "fetch") as fetch:
                self.assertEqual(acquire.main(args), 1); fetch.assert_not_called(); self.assertFalse(output.exists())
            for name in SOURCE_PATHS:
                (root / name).parent.mkdir(parents=True, exist_ok=True); (root / name).write_bytes(b"fixture")
            with patch("scripts.lib.decision_public_sources.subprocess.check_output", side_effect=["a" * 40, b"changed"]):
                with self.assertRaisesRegex(ValueError, "Commit acquisition"): source_identity(root)


if __name__ == "__main__": unittest.main()
