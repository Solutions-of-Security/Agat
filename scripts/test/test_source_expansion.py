import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from decision_runtime.annotations import finalize_reviews, group_split
from decision_runtime.contracts import parse_json

ROOT = Path(__file__).resolve().parents[2] / "docs/qualification/local-decisions/source-review"
SPEC = importlib.util.spec_from_file_location("source_pack_builder", ROOT / "build.py")
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


class SourceExpansionTest(unittest.TestCase):
    def test_all_revisions_reconstruct_and_previous_inputs_and_splits_are_preserved(self):
        for revision in ("v1", "v2", "v3"):
            for name, contents in BUILDER.build(revision).items():
                self.assertEqual((ROOT / name).read_bytes(), contents.encode(), name)
        previous = parse_json((ROOT / "pool.v2.json").read_bytes())
        expanded = parse_json((ROOT / "pool.v3.json").read_bytes())
        self.assertEqual(expanded["cases"][:24], previous["cases"])
        self.assertEqual(len(expanded["cases"]), 48)
        seed = parse_json((ROOT / "review.first.blank.v3.json").read_bytes())["splitSeed"]
        old_splits = parse_json((ROOT / "readiness.v2.json").read_bytes())["prospectiveGroupSplits"]
        self.assertTrue(all(group_split(seed, c["groupId"]) == old_splits[c["groupId"]] for c in expanded["cases"][:24]))

    def test_blind_expansion_cannot_finalize_or_claim_human_reviews(self):
        first = parse_json((ROOT / "review.first.blank.v3.json").read_bytes())
        second = parse_json((ROOT / "review.second.blank.v3.json").read_bytes())
        for review in (first, second):
            self.assertIsNone(review["reviewerId"])
            self.assertIsNone(review["reviewedAt"])
            self.assertTrue(all(row["expectedOptionId"] is None and row["rationale"] is None for row in review["labels"]))
        with self.assertRaises(ValueError): finalize_reviews(first, second)
        readiness = parse_json((ROOT / "readiness.v3.json").read_bytes())
        self.assertEqual(readiness["humanReviewedCases"], 0)
        self.assertEqual(readiness["observedIssueRequests"], 2)
        self.assertFalse(readiness["qualifiedForRouting"])
        self.assertFalse(readiness["routingEnabled"])

    def test_changed_previous_case_seed_snapshot_or_frozen_group_is_rejected(self):
        mutations = [
            ("case-specs.v3.json", lambda d: d["cases"][0].update(claim="Changed input")),
            ("case-specs.v3.json", lambda d: d.update(splitSeed="different-seed")),
            ("sources.v3.json", lambda d: d["sources"][-1].update(groupId="different-group")),
        ]
        for name, mutate in mutations:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / "pack"
                shutil.copytree(ROOT, target)
                value = parse_json((target / name).read_bytes()); mutate(value)
                (target / name).write_text(json.dumps(value))
                with patch.object(BUILDER, "ROOT", target), self.assertRaises(ValueError): BUILDER.build("v3")
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "pack"; shutil.copytree(ROOT, target)
            (target / "sources/a2a-contract.v3.txt").write_text("Changed source")
            with patch.object(BUILDER, "ROOT", target), self.assertRaises(ValueError): BUILDER.build("v3")


if __name__ == "__main__": unittest.main()
