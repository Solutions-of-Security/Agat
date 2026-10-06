import importlib.util
import json
import os
import plistlib
import signal
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from decision_runtime.artifacts import read_json, sealed, write_new

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("observer_manager", ROOT / "scripts/manage-decision-session-observer.py")
manager = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(manager)


class ObserverManagementTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name); self.project = self.home / "project"
        self.private = self.project / "docs/private"; self.private.mkdir(parents=True)
        self.package = self.home / "Library/Application Support/Agat/decision-shadow/session-observers/package"
        self.package.mkdir(parents=True)
        self.config = {"sha256": "a"*64, "ownerUid": os.getuid(), "python": "/fixture/python",
                       "baselineFileSha256": "b"*64, "snapshotCommit": "c"*40}
        self.recipe = manager.prepare["agent"](self.package, self.config["python"], self.config["sha256"])
        self.source_plist = self.package / f"{manager.LABEL}.plist"
        self.source_plist.write_bytes(plistlib.dumps(self.recipe))
        self.installed = self.home / "Library/LaunchAgents" / self.source_plist.name
        self.registered = False; self.exit_code = 0; self.bootstrap_failure = False; self.foreign = False
        self.commands = []; self.outputs = 0
        owner = self

        def native(command, *arguments):
            owner.commands.append((command, arguments))
            if command == "bootstrap":
                owner.registered = True
                if owner.bootstrap_failure: return SimpleNamespace(returncode=5, stdout="", stderr="fixture bootstrap failed")
                owner.observation()
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            if command == "bootout":
                owner.registered = False
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            if not owner.registered:
                return SimpleNamespace(returncode=113, stdout="", stderr=f'Could not find service "{manager.LABEL}"')
            path = "/foreign/observer.plist" if owner.foreign else str(owner.installed)
            return SimpleNamespace(returncode=0, stdout=f"\tpath = {path}\n\truns = 1\n\tlast exit code = {owner.exit_code}: EX_OK\n", stderr="")

        def private_output(path):
            if not path.resolve().is_relative_to(owner.private.resolve()) or path.exists(): raise ValueError("Use a new private output")

        changes = [patch.object(manager, "ROOT", self.project), patch.object(manager.Path, "home", return_value=self.home),
                   patch.object(manager.platform, "system", return_value="Darwin"),
                   patch.object(manager.runpy, "run_path", return_value={"private_output": private_output}),
                   patch.dict(manager.collector, {"validate_package": lambda *_args: self.config}),
                   patch.dict(manager.prepare, {"source_identity": lambda: ("d"*40, {"fixture": "e"*64})}),
                   patch.dict(manager.native, {"launchctl": native, "wait_for_removal": lambda *_args, **_kwargs: not owner.registered}),
                   patch.object(manager.subprocess, "run", return_value=SimpleNamespace(returncode=0))]
        for item in changes: item.start(); self.addCleanup(item.stop)

    def observation(self):
        parent = self.package / "source/docs/private/session-events/fixture"
        parent.mkdir(parents=True, exist_ok=True)
        events = {}
        for event in ("boot", "login"):
            path = parent / f"{event}.json"
            write_new(path, sealed({"schemaVersion": "agat.decision.resident-session-verification.v1", "status": "awaiting_event",
                                   "baselineFileSha256": self.config["baselineFileSha256"], "event": {"expectedEvent": event},
                                   "checks": {"actualEventObserved": False, "newProcessStarts": False}, "routingEnabled": False, "qualification": "not_assessed"}))
            path.chmod(0o600)
            events[event] = manager.collector["accepted_verification"](path, 2, self.config["baselineFileSha256"], event)
        write_new(parent / "observer-run.json", sealed({"schemaVersion": manager.collector["REPORT_SCHEMA"], "status": "recorded",
                    "createdAt": datetime.now(timezone.utc).isoformat(), "observerSeal": self.config["sha256"], "snapshotCommit": self.config["snapshotCommit"],
                    "baselineFileSha256": self.config["baselineFileSha256"], "events": events, "routingEnabled": False, "qualification": "not_assessed"}))

    def invoke(self, action):
        self.outputs += 1
        self.output = self.private / f"{action}-{self.outputs}.json"
        return manager.main([action, "--package", str(self.package), "--expected-seal", "a"*64, "--output", str(self.output)])

    def test_install_and_status_keep_both_real_event_gates_pending(self):
        self.assertEqual(self.invoke("install"), 0)
        report = read_json(self.output)
        self.assertEqual(report["status"], "installed")
        self.assertTrue(self.registered); self.assertEqual(self.installed.stat().st_mode & 0o777, 0o600)
        self.assertTrue(all(not row["actualEventAccepted"] for row in report["observation"]["report"]["events"].values()))
        self.assertEqual(self.invoke("status"), 0)
        self.assertEqual(read_json(self.output)["status"], "registered")

    def test_stop_removes_only_the_unchanged_owned_registration_and_plist(self):
        self.assertEqual(self.invoke("install"), 0)
        self.assertEqual(self.invoke("stop"), 0)
        self.assertFalse(self.registered); self.assertFalse(self.installed.exists())
        self.assertFalse((self.package / "registration.json").exists()); self.assertTrue(self.source_plist.exists())

    def test_existing_foreign_label_or_plist_is_never_booted_out(self):
        self.registered = True; self.foreign = True
        self.assertEqual(self.invoke("install"), 1)
        self.assertFalse(any(command == "bootout" for command,_ in self.commands))
        self.assertFalse(self.installed.exists())

    def test_bootstrap_failure_rolls_back_our_new_label_and_exact_files(self):
        self.bootstrap_failure = True
        self.assertEqual(self.invoke("install"), 1)
        self.assertFalse(self.registered); self.assertFalse(self.installed.exists())
        self.assertFalse((self.package / "registration.json").exists())
        self.assertIsNone(read_json(self.output)["cleanupFailure"])

    def test_foreign_label_winning_bootstrap_race_is_never_booted_out(self):
        original = manager.native["launchctl"]
        def raced(command, *arguments):
            result = original(command, *arguments)
            if command == "bootstrap":
                self.foreign = True
                return SimpleNamespace(returncode=5)
            return result
        with patch.dict(manager.native, {"launchctl": raced}): self.assertEqual(self.invoke("install"), 1)
        self.assertTrue(self.registered)
        self.assertFalse(any(command == "bootout" for command,_ in self.commands))
        self.assertIsNotNone(read_json(self.output)["cleanupFailure"])
        self.assertTrue(self.installed.exists())

    def test_changed_file_during_failed_bootstrap_is_preserved_by_rollback(self):
        original = manager.native["launchctl"]
        def replaced(command, *arguments):
            result = original(command, *arguments)
            if command == "bootstrap":
                self.installed.write_bytes(b"foreign replacement during bootstrap")
                return SimpleNamespace(returncode=5)
            return result
        with patch.dict(manager.native, {"launchctl": replaced}): self.assertEqual(self.invoke("install"), 1)
        self.assertEqual(self.installed.read_bytes(), b"foreign replacement during bootstrap")
        self.assertIsNotNone(read_json(self.output)["cleanupFailure"])

    def test_sigterm_during_submission_rolls_back_and_restores_handlers(self):
        original = manager.native["launchctl"]
        handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
        def interrupted(command, *arguments):
            result = original(command, *arguments)
            if command == "bootstrap": signal.raise_signal(signal.SIGTERM)
            return result
        with patch.dict(manager.native, {"launchctl": interrupted}): self.assertEqual(self.invoke("install"), 1)
        self.assertFalse(self.registered); self.assertFalse(self.installed.exists())
        self.assertEqual(read_json(self.output)["failure"]["type"], "KeyboardInterrupt")
        for sig, handler in handlers.items(): self.assertEqual(signal.getsignal(sig), handler)

    def test_other_observer_seal_cannot_be_reused_for_this_package(self):
        self.assertEqual(self.invoke("install"), 0)
        report = self.package / "source/docs/private/session-events/fixture/observer-run.json"
        data = read_json(report); data.pop("sha256"); data["observerSeal"] = "9"*64
        report.write_text(json.dumps(sealed(data)))
        self.assertEqual(self.invoke("status"), 1)
        self.assertTrue(self.registered)

    def test_changed_installed_inode_or_bytes_prevents_status_and_stop(self):
        self.assertEqual(self.invoke("install"), 0)
        self.installed.write_bytes(b"foreign replacement")
        self.assertEqual(self.invoke("stop"), 1)
        self.assertTrue(self.registered); self.assertEqual(self.installed.read_bytes(), b"foreign replacement")

    def test_failed_collector_job_cannot_be_accepted_as_installed(self):
        self.exit_code = 1
        self.assertEqual(self.invoke("install"), 1)
        self.assertFalse(self.registered); self.assertFalse(self.installed.exists())

    def test_public_output_is_rejected_before_any_native_mutation(self):
        output = self.project / "public.json"
        result = manager.main(["install", "--package", str(self.package), "--expected-seal", "a"*64, "--output", str(output)])
        self.assertEqual(result, 1); self.assertFalse(output.exists()); self.assertEqual(self.commands, [])


if __name__ == "__main__": unittest.main()
