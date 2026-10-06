import copy
import hashlib
import importlib.util
import json
import os
import signal
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("session_observer", ROOT / "scripts/observe-decision-resident-session.py")
observer = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(observer)


def write(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(observer.canonical(observer.sealed(body))+"\n"); path.chmod(0o600)


class ObserverTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name); self.root.chmod(0o700)
        self.source = self.root / "source"; self.source.mkdir()
        (self.source / ".git").mkdir()
        self.raw = b"committed source fixture\n"; (self.source / "sample.py").write_bytes(self.raw)
        (self.root / "observer.py").write_bytes(Path(observer.__file__).read_bytes())
        (self.root / "observer.py").chmod(0o600)
        self.sources = {"sample.py": hashlib.sha256(self.raw).hexdigest()}
        self.baseline = {"schemaVersion": "agat.decision.resident-session-baseline.v1", "status": "baseline_recorded",
                         "sourceFiles": self.sources, "sourceCommit": "a"*40, "bundleRoot": "/fixture/resident", "bundleSeal": "b"*64,
                         "routingEnabled": False, "qualification": "not_assessed"}
        write(self.root / "baseline.json", self.baseline)
        self.config = {"schemaVersion": observer.SCHEMA, "destination": str(self.root), "ownerUid": os.getuid(),
                       "sourceFiles": self.sources, "snapshotCommit": "a"*40, "residentRoot": "/fixture/resident", "residentSeal": "b"*64,
                       "python": "/fixture/python", "collectorSha256": observer.checksum(self.root / "observer.py"),
                       "baselineFileSha256": observer.checksum(self.root / "baseline.json"), "routingEnabled": False, "qualification": "not_assessed"}
        self.publish()
        self.git = patch.object(observer.subprocess, "check_output", side_effect=self.git_output).start()
        self.addCleanup(patch.stopall)
        self.states = {"boot": ("awaiting_event", 2), "login": ("awaiting_event", 2)}
        self.commands = []

    def publish(self):
        self.sealed_config = observer.sealed(self.config)
        write(self.root / "deployment.json", self.config)

    def git_output(self, command, **_kwargs):
        if command[1:] == ["rev-parse", "--show-toplevel"]: return str(self.source)+"\n"
        return "a"*40+"\n" if command[1] == "rev-parse" else "" if command[1] == "status" else self.raw

    def run_verification(self, command, **kwargs):
        self.commands.append((command, kwargs))
        event = command[command.index("--event")+1]; output = Path(command[command.index("--output")+1])
        status, code = self.states[event]
        write(output, {"schemaVersion": "agat.decision.resident-session-verification.v1", "status": status,
                       "baselineFileSha256": self.config["baselineFileSha256"], "event": {"expectedEvent": event},
                       "checks": {"actualEventObserved": status == "verified", "newProcessStarts": status == "verified"},
                       "routingEnabled": False, "qualification": "not_assessed"})
        return SimpleNamespace(returncode=code, stdout="fixture observation\n", stderr="")

    def collect(self):
        with patch.object(observer.subprocess, "run", side_effect=self.run_verification):
            return observer.collect(self.root, self.sealed_config)

    def result(self):
        files = list((self.source / "docs/private/session-events").glob("*/observer-run.json"))
        self.assertEqual(len(files), 1)
        return observer.read_sealed(files[0], observer.REPORT_SCHEMA)

    def test_pinned_snapshot_and_same_session_are_successfully_recorded_as_pending(self):
        self.assertEqual(observer.validate_package(self.root, self.sealed_config["sha256"]), self.sealed_config)
        baseline = (self.root / "baseline.json").read_bytes()
        self.assertEqual(self.collect(), 0)
        report = self.result(); self.assertEqual(report["status"], "recorded")
        self.assertEqual([v["status"] for v in report["events"].values()], ["awaiting_event", "awaiting_event"])
        self.assertFalse(any(v["actualEventAccepted"] for v in report["events"].values()))
        self.assertEqual((self.root / "baseline.json").read_bytes(), baseline)
        self.assertEqual([command[command.index("--event")+1] for command,_ in self.commands], ["boot", "login"])
        self.assertTrue(all(kwargs["timeout"] == 240 for _,kwargs in self.commands))
        self.assertTrue(all(kwargs["env"]["HF_HUB_OFFLINE"] == "1" for _,kwargs in self.commands))

    def test_real_login_receipt_can_pass_while_boot_remains_pending(self):
        self.states["login"] = ("verified", 0)
        self.assertEqual(self.collect(), 0)
        self.assertFalse(self.result()["events"]["boot"]["actualEventAccepted"])
        self.assertTrue(self.result()["events"]["login"]["actualEventAccepted"])

    def test_status_code_contradiction_does_not_become_a_completed_event(self):
        self.states["boot"] = ("awaiting_event", 0)
        self.assertEqual(self.collect(), 1)
        self.assertEqual(self.result()["status"], "failed")

    def test_failed_child_receipt_is_preserved_and_aggregate_fails(self):
        self.states["login"] = ("failed", 1)
        self.assertEqual(self.collect(), 1)
        self.assertEqual(self.result()["events"]["login"]["status"], "failed")

    def test_sigterm_records_failure_and_restores_both_handlers(self):
        handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
        def interrupt(*_args, **_kwargs): signal.raise_signal(signal.SIGTERM)
        with patch.object(observer.subprocess, "run", side_effect=interrupt):
            self.assertEqual(observer.collect(self.root, self.sealed_config), 1)
        self.assertEqual(self.result()["failure"]["type"], "KeyboardInterrupt")
        for sig, handler in handlers.items(): self.assertEqual(signal.getsignal(sig), handler)

    def test_changed_source_and_dirty_git_snapshot_are_rejected(self):
        (self.source / "sample.py").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "Frozen source"): observer.validate_package(self.root, self.sealed_config["sha256"])
        (self.source / "sample.py").write_bytes(self.raw)
        def dirty(command, **kwargs): return "?? unreviewed.py\n" if command[1] == "status" else self.git_output(command, **kwargs)
        with patch.object(observer.subprocess, "check_output", side_effect=dirty), self.assertRaisesRegex(ValueError, "checkout changed"):
            observer.validate_package(self.root, self.sealed_config["sha256"])

    def test_changed_revision_historical_bytes_or_external_git_objects_fail(self):
        def wrong_head(command, **kwargs): return "c"*40 if command[1:] == ["rev-parse", "HEAD"] else self.git_output(command, **kwargs)
        with patch.object(observer.subprocess, "check_output", side_effect=wrong_head), self.assertRaisesRegex(ValueError, "revision differs"):
            observer.validate_package(self.root, self.sealed_config["sha256"])
        def wrong_object(command, **kwargs): return b"changed object" if command[1] == "show" else self.git_output(command, **kwargs)
        with patch.object(observer.subprocess, "check_output", side_effect=wrong_object), self.assertRaisesRegex(ValueError, "Git source binding"):
            observer.validate_package(self.root, self.sealed_config["sha256"])
        alternate = self.source / ".git/objects/info/alternates"; alternate.parent.mkdir(parents=True); alternate.write_text("/temporary/objects")
        with self.assertRaisesRegex(ValueError, "external Git objects"): observer.validate_package(self.root, self.sealed_config["sha256"])

    def test_external_git_metadata_and_worktree_are_rejected(self):
        def external(command, **kwargs):
            return "/external/worktree\n" if command[1:] == ["rev-parse", "--show-toplevel"] else self.git_output(command, **kwargs)
        with patch.object(observer.subprocess, "check_output", side_effect=external), self.assertRaisesRegex(ValueError, "worktree escaped"):
            observer.validate_package(self.root, self.sealed_config["sha256"])
        metadata = self.source / ".git"; metadata.rmdir(); metadata.symlink_to(self.root)
        with self.assertRaisesRegex(ValueError, "metadata must remain local"):
            observer.validate_package(self.root, self.sealed_config["sha256"])

    def test_collector_link_or_public_private_file_mode_is_rejected(self):
        member = self.root / "observer.py"; member.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "file owner/mode"): observer.validate_package(self.root, self.sealed_config["sha256"])
        member.chmod(0o600); member.unlink(); member.symlink_to(Path(observer.__file__))
        with self.assertRaisesRegex(ValueError, "file owner/mode"): observer.validate_package(self.root, self.sealed_config["sha256"])

    def test_foreign_owner_numeric_alias_routing_and_changed_baseline_fail(self):
        original = copy.deepcopy(self.config)
        for field, value in (("ownerUid", os.getuid()+1), ("ownerUid", float(os.getuid())), ("routingEnabled", 0)):
            self.config = {**copy.deepcopy(original), field: value}; self.publish()
            with self.subTest(field=field), self.assertRaises(ValueError): observer.validate_package(self.root, self.sealed_config["sha256"])
        self.config = original; self.publish(); (self.root / "baseline.json").write_text("changed baseline")
        with self.assertRaisesRegex(ValueError, "Baseline pin"): observer.validate_package(self.root, self.sealed_config["sha256"])

    def test_duplicate_keys_nonfinite_json_wrong_seal_and_symlinks_are_rejected(self):
        path = self.root / "bad.json"
        for raw in ('{"schemaVersion":"x","schemaVersion":"x"}', '{"number":NaN}', '{"schemaVersion":"x","sha256":"wrong"}'):
            path.write_text(raw)
            with self.subTest(raw=raw), self.assertRaises(ValueError): observer.read_sealed(path, "x")
        path.unlink(); path.symlink_to(self.root / "deployment.json")
        with self.assertRaises(ValueError): observer.read_sealed(path, observer.SCHEMA)


if __name__ == "__main__": unittest.main()
