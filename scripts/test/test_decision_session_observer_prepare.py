import hashlib
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("observer_prepare", ROOT / "scripts/prepare-decision-session-observer.py")
prepare = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(prepare)


class ObserverPreparationTest(unittest.TestCase):
    def test_only_a_new_direct_application_support_destination_is_accepted(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(prepare.Path, "home", return_value=Path(temporary)):
            base = Path(temporary) / "Library/Application Support/Agat/decision-shadow/session-observers"
            accepted = base / "observer-new"
            self.assertEqual(prepare.destination(accepted), accepted)
            for path in (base, base / "nested/observer", Path(temporary) / "public"):
                with self.subTest(path=path), self.assertRaises(ValueError): prepare.destination(path)
            base.mkdir(parents=True); accepted.mkdir()
            with self.assertRaises(ValueError): prepare.destination(accepted)
            accepted.rmdir(); accepted.symlink_to(Path(temporary))
            with self.assertRaises(ValueError): prepare.destination(accepted)

    def test_one_shot_agent_preserves_process_group_cleanup_and_independent_seal(self):
        root = Path("/fixture/observer")
        config = prepare.agent(root, "/fixture/resident/venv/bin/python", "a"*64)
        self.assertIs(config["KeepAlive"], False); self.assertIs(config["RunAtLoad"], True)
        self.assertIs(config["AbandonProcessGroup"], False)
        self.assertEqual(config["ProgramArguments"][-1], "a"*64)
        self.assertEqual(config["EnvironmentVariables"]["PATH"], "/usr/bin:/bin:/usr/sbin:/sbin")
        self.assertEqual(config["EnvironmentVariables"]["HF_HUB_OFFLINE"], "1")

    def test_builder_sources_must_exist_in_the_measured_commit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = b"committed observer builder fixture"
            for name in prepare.SOURCES:
                (root/name).parent.mkdir(parents=True, exist_ok=True); (root/name).write_bytes(raw)
            with patch.object(prepare, "ROOT", root), patch.object(prepare.subprocess, "check_output", side_effect=["a"*40,raw,raw,raw]):
                commit, sources = prepare.source_identity()
                self.assertEqual(commit, "a"*40); self.assertEqual(len(sources), 3)
            with patch.object(prepare, "ROOT", root), patch.object(prepare.subprocess, "check_output", side_effect=["a"*40,b"changed"]):
                with self.assertRaises(ValueError): prepare.source_identity()

    def test_real_local_git_snapshot_survives_removal_of_original_repository(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); original = root / "original"; original.mkdir(); package = root / "package"; package.mkdir()
            def git(*arguments):
                return subprocess.check_output(["/usr/bin/git", "-C", str(original), *arguments], text=True, stderr=subprocess.STDOUT).strip()
            git("init", "--quiet", "-b", "codex/fixture")
            (original/"sample.py").write_text("frozen source\n"); git("add", "sample.py")
            git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "--no-gpg-sign", "--quiet", "-m", "fixture")
            commit = git("rev-parse", "HEAD")
            with patch.object(prepare, "ROOT", original): observations = prepare.snapshot(package, commit)
            self.assertTrue(all(row["returncode"] == 0 for row in observations))
            moved = root / "original-removed"; original.rename(moved)
            copy = package / "source"
            self.assertEqual(subprocess.check_output(["/usr/bin/git", "show", f"{commit}:sample.py"], cwd=copy, text=True), "frozen source\n")
            self.assertFalse((copy / ".git/objects/info/alternates").exists())
            self.assertEqual((copy / "sample.py").read_text(), "frozen source\n")


if __name__ == "__main__": unittest.main()
