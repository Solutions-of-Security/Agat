import copy
import hashlib
import importlib.util
import json
import signal
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from decision_runtime.artifacts import verify_seal
from decision_runtime.contracts import canonical_json, fingerprint
from scripts.test.test_decision_crash_loop import EPOCH, INSTANCE, history_fixture, metrics

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("crash_loop_probe", ROOT / "scripts/check-decision-crash-loop.py")
probe = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(probe)


class CrashLoopCliTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name); self.directory = self.root / "docs/private/probe"
        self.fixture = history_fixture(); self.profile = self.fixture["profile"]
        self.request_path = self.root / "request.json"; self.request_path.write_text(canonical_json(self.fixture["request"].to_dict()))
        self.release = self.root / "release"
        self.config = {"Label": "org.agat.decision-shadow", "ThrottleInterval": 30, "RunAtLoad": True,
                       "KeepAlive": {"SuccessfulExit": False}, "ProgramArguments": ["/fixture/python", "-m", "decision_runtime", "serve", "--port", "8766"],
                       "WorkingDirectory": "/fixture/runtime", "StandardOutPath": "/fixture/old.out", "StandardErrorPath": "/fixture/old.err",
                       "EnvironmentVariables": {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}}
        self.bundle = {"sha256": "a"*64, "profileSha256": fingerprint(self.profile), "profile": self.profile, "serviceConfigs": [self.config]}
        self.generation = 0; self.phase = "absent"; self.clock = 0.0; self.counter = 0; self.signals = []; self.scores = []
        self.native_config = None; self.health_profile = self.profile; self.complete_inventory = True; self.cleanup_ok = True
        self.owner_change = False; self.after_drift = False
        self.resident = {"bundleRoot": str(self.release), "bundleSeal": "a"*64, "profileSha256": fingerprint(self.profile),
                         "registrationFileSha256": "b"*64, "registrationSeal": "c"*64, "installedPlists": [], "environment": {},
                         "processes": [{"pid": 900}], "osSession": {key: 1 for key in ("hostFingerprint", "bootSessionUuid", "bootTimeEpoch", "guiSessionId", "ownerUid")}}
        self.collect_count = 0
        owner = self

        def monotonic():
            owner.clock += 0.01
            return owner.clock

        def state(target, diagnostic=None):
            if owner.phase == "exited":
                value = {"runs": owner.generation, "last exit code": 75}; owner.phase = "down"
            else:
                if owner.phase == "down": owner.generation += 1; owner.phase = "up"; owner.counter = 0
                value = None if owner.phase == "absent" else {"pid": owner.generation*100, "runs": owner.generation}
            if diagnostic is not None:
                diagnostic.write_text(json.dumps(value)); diagnostic.chmod(0o600)
            return value

        def control(command, *arguments):
            if command == "bootstrap":
                import plistlib
                owner.native_config = plistlib.loads(Path(arguments[-1]).read_bytes())
                owner.phase = "up"; owner.generation = 1
            elif command == "bootout": owner.phase = "absent"
            else: raise AssertionError(f"Unexpected mutation: {command}")
            return SimpleNamespace(returncode=0)

        def children(pid):
            if not owner.complete_inventory: raise RuntimeError("fixture unknown child")
            rows = copy.deepcopy(owner.fixture["runs"][owner.generation-1]["children"])
            if owner.owner_change and owner.scores: rows[0]["parentPid"] += 1
            return rows

        def kill(pid, sig):
            owner.signals.append((pid, sig)); owner.phase = "exited"
            event = owner.fixture["events"][owner.generation-1]
            with Path(owner.native_config["StandardErrorPath"]).open("a") as stream: stream.write(canonical_json(event)+"\n")

        def collect(*_arguments):
            owner.collect_count += 1
            data = copy.deepcopy(owner.resident)
            if owner.after_drift and owner.collect_count > 1: data["processes"][0]["pid"] += 1
            return data

        class Runtime:
            def call(self, *_args):
                return 200, {"status": "ready", "mode": "shadow", "profileJson": canonical_json(owner.health_profile), "profileSha256": fingerprint(owner.health_profile)}
            def score(self, request):
                owner.counter += 1; owner.scores.append(owner.generation)
                return copy.deepcopy(owner.fixture["runs"][owner.generation-1]["decision"])

        class Monitor:
            def __init__(self, *_args):
                self.queries = []; self.instance = INSTANCE; self.checks = {"prometheusStopped": False}
            def start(self, port): self.port = port
            def capture(self, phase, count, new_start=False):
                if owner.counter != count: raise RuntimeError("counter mismatch")
                record = metrics(EPOCH+30*(owner.generation-1)+2, count)
                self.queries.append({"response": record["raw"]})
                return record["summary"]
            def api(self, path, parameters=None):
                if path == "targets":
                    if owner.phase == "down": return owner.fixture["failures"][owner.generation-1]["downTargets"]
                    return metrics(EPOCH+30*(owner.generation-1)+2)["targets"]
                if path == "alerts":
                    if owner.phase == "down": return owner.fixture["failures"][owner.generation-1]["pendingAlerts"]
                    return {"status": "success", "data": {"alerts": []}}
                if path == "query_range": return owner.fixture["failures"][owner.generation-2]["upHistory"]
                raise AssertionError(path)
            def failed(self): pass
            def wait_alert(self, pending): pass
            def close(self): self.checks["prometheusStopped"] = True
            def report(self): return {"pid": 999, "checks": {}, "queries": self.queries}

        self.manager = {"validate_bundle": lambda *_args: (self.bundle, 8766, 9095)}
        changes = [patch.object(probe, "ROOT", self.root), patch.object(probe.platform, "system", return_value="Darwin"),
                   patch.object(probe.runpy, "run_path", return_value=self.manager),
                   patch.object(probe, "source_identity", return_value=("1"*40, {"fixture": "a"*64})),
                   patch.dict(probe.session, {"collect": collect}), patch.object(probe, "resident_metrics", return_value={"summary": {"computed": 1}}),
                   patch.object(probe, "service_info", side_effect=state), patch.object(probe, "launchctl", side_effect=control),
                   patch.dict(probe.launchd, {"wait_for_removal": lambda *_args: owner.phase == "absent"}),
                   patch.object(probe, "child_processes", side_effect=children), patch.object(probe, "gone", side_effect=lambda *_args, **_kwargs: owner.cleanup_ok),
                   patch.object(probe, "birth_observation", side_effect=lambda pid, path: EPOCH+30*(owner.generation-1)+(0 if pid%100 == 0 else 1)),
                   patch.object(probe, "OwnedRuntime", Runtime), patch.object(probe, "PrometheusObservation", Monitor),
                   patch.object(probe.os, "kill", side_effect=kill), patch.object(probe.subprocess, "run", return_value=SimpleNamespace(returncode=0)),
                   patch.object(probe.time, "monotonic", side_effect=monotonic), patch.object(probe.time, "time", side_effect=lambda: EPOCH+30*max(owner.generation-1,0)+100+owner.clock),
                   patch.object(probe.time, "sleep", side_effect=lambda seconds: setattr(owner, "clock", owner.clock+seconds)),
                   patch.object(probe.socket, "socket")]
        for item in changes: item.start(); self.addCleanup(item.stop)
        reservation = probe.socket.socket.return_value
        reservation.__enter__.return_value = reservation; reservation.getsockname.return_value = ("127.0.0.1", 38766)
        self.args = ["--bundle", str(self.release), "--expected-seal", "a"*64, "--request", str(self.request_path), "--evidence-dir", str(self.directory)]

    def report(self): return verify_seal(json.loads((self.directory / "crash-loop.json").read_text()), "agat.decision.launchd-crash-loop.v1")

    def test_full_bounded_cycle_preserves_resident_and_cleans_only_random_job(self):
        self.assertEqual(probe.main(self.args), 0)
        report = self.report()
        self.assertTrue(all(report["checks"].values()))
        self.assertEqual(self.signals, [(101, signal.SIGKILL), (201, signal.SIGKILL), (301, signal.SIGKILL)])
        self.assertEqual(self.scores, [1,2,3,4]); self.assertEqual(len(report["ownedPids"]), 12)
        self.assertEqual(self.phase, "absent"); self.assertEqual(self.config, self.bundle["serviceConfigs"][0])
        self.assertEqual(self.native_config["ProgramArguments"][-1], "38766")
        self.assertEqual(self.native_config["EnvironmentVariables"], self.config["EnvironmentVariables"])
        self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.directory / "crash-loop.json").stat().st_mode & 0o777, 0o600)
        for name, checksum in report["retainedFilesSha256"].items(): self.assertEqual(hashlib.sha256((self.directory/name).read_bytes()).hexdigest(), checksum)

    def test_wrong_profile_is_rejected_before_scoring_or_fault(self):
        self.health_profile = {**self.profile, "runtimeVersion": "0.12.2"}
        self.assertEqual(probe.main(self.args), 1)
        self.assertEqual(self.scores, []); self.assertEqual(self.signals, [])
        self.assertTrue(self.report()["checks"]["temporaryServiceRemoved"])

    def test_ownership_change_before_signal_never_kills_the_new_child(self):
        self.owner_change = True
        self.assertEqual(probe.main(self.args), 1)
        self.assertEqual(self.signals, [])
        self.assertIn("Ownership changed", self.report()["failure"]["message"])

    def test_incomplete_inventory_prevents_a_cleanup_pass(self):
        self.complete_inventory = False
        self.assertEqual(probe.main(self.args), 1)
        self.assertFalse(self.report()["checks"]["allOwnedProcessesStopped"])
        self.assertTrue(self.report()["checks"]["temporaryServiceRemoved"])
        self.assertEqual(self.signals, [])

    def test_unobserved_early_start_cannot_claim_a_complete_process_inventory(self):
        original = probe.service_info.side_effect
        def skipped(target, diagnostic=None):
            state = original(target, diagnostic)
            if state is not None and state.get("pid") == 100: state["runs"] = 2
            return state
        with patch.object(probe, "service_info", side_effect=skipped): self.assertEqual(probe.main(self.args), 1)
        self.assertFalse(self.report()["checks"]["allOwnedProcessesStopped"])
        self.assertEqual(self.scores, []); self.assertEqual(self.signals, [])

    def test_a_remaining_owned_process_is_reported_as_failed_cleanup(self):
        self.cleanup_ok = False
        self.assertEqual(probe.main(self.args), 1)
        self.assertFalse(self.report()["checks"]["allOwnedProcessesStopped"])
        self.assertIsNotNone(self.report()["cleanupFailure"])

    def test_sources_changed_after_successful_history_cannot_pass(self):
        with patch.object(probe, "source_identity", side_effect=[("1"*40, {"fixture": "a"*64}), ("1"*40, {"fixture": "b"*64})]):
            self.assertEqual(probe.main(self.args), 1)
        self.assertTrue(self.report()["checks"]["historyVerified"])
        self.assertFalse(self.report()["checks"]["sourcesStable"])

    def test_second_signal_during_cleanup_does_not_interrupt_the_owned_cleanup(self):
        monitor = probe.PrometheusObservation
        close = monitor.close
        def interrupted_cleanup(instance):
            signal.raise_signal(signal.SIGTERM)
            return close(instance)
        before = signal.getsignal(signal.SIGTERM)
        with patch.object(monitor, "close", interrupted_cleanup): self.assertEqual(probe.main(self.args), 0)
        self.assertTrue(self.report()["monitoring"]["prometheusStopped"])
        self.assertEqual(signal.getsignal(signal.SIGTERM), before)

    def test_sigterm_saves_failed_evidence_and_restores_signal_handlers(self):
        handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
        def cancel(*_args): signal.raise_signal(signal.SIGTERM)
        with patch.object(probe.OwnedRuntime, "score", side_effect=cancel): self.assertEqual(probe.main(self.args), 1)
        self.assertEqual(self.report()["failure"]["type"], "KeyboardInterrupt")
        self.assertTrue(self.report()["checks"]["temporaryServiceRemoved"])
        self.assertEqual(self.signals, [])
        for sig, handler in handlers.items(): self.assertEqual(signal.getsignal(sig), handler)

    def test_original_resident_process_change_cannot_pass(self):
        self.after_drift = True
        self.assertEqual(probe.main(self.args), 1)
        self.assertFalse(self.report()["checks"]["residentUnchanged"])
        self.assertEqual(self.report()["status"], "failed")

    def test_existing_or_public_evidence_is_preserved_before_registration(self):
        self.directory.mkdir(parents=True); (self.directory / "old").write_text("original")
        self.assertEqual(probe.main(self.args), 1)
        self.assertEqual((self.directory / "old").read_text(), "original"); self.assertIsNone(self.native_config)
        self.args[-1] = str(self.root / "public")
        self.assertEqual(probe.main(self.args), 1); self.assertIsNone(self.native_config)

    def test_wrong_throttle_never_submits_a_job(self):
        self.config["ThrottleInterval"] = 10
        self.assertEqual(probe.main(self.args), 1)
        self.assertIsNone(self.native_config); self.assertEqual(self.signals, [])

    def test_prometheus_preparation_failure_never_registers_or_signals_a_runtime(self):
        with patch.object(probe, "PrometheusObservation", side_effect=ValueError("binary SHA mismatch")):
            self.assertEqual(probe.main(self.args), 1)
        self.assertIsNone(self.native_config); self.assertEqual(self.signals, [])
        self.assertTrue(self.report()["checks"]["temporaryServiceRemoved"])

    def test_exit_publication_is_bounded_and_ignores_a_stale_live_exit(self):
        with patch.object(probe, "service_info", return_value={"pid": 200, "runs": 2, "last exit code": 75}), \
             patch.object(probe.time, "monotonic", side_effect=[0,0,6]):
            with self.assertRaisesRegex(RuntimeError, "five seconds"):
                probe.wait_for_generation_exit("fixture", 2, self.root / "exit.txt")


class NativeBirthEvidenceTest(unittest.TestCase):
    def test_native_time_is_retained_privately_with_utc_and_c_locale(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/"birth.txt"
            with patch.object(probe.subprocess, "check_output", return_value="Tue Oct  6 22:00:00 2026\n") as command:
                self.assertEqual(probe.birth_observation(123, path), 1791324000)
            self.assertEqual(command.call_args.kwargs["env"]["LC_ALL"], "C")
            self.assertEqual(command.call_args.kwargs["env"]["TZ"], "UTC")
            self.assertEqual(path.read_text(), "Tue Oct  6 22:00:00 2026\n")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_invalid_native_birth_keeps_diagnostic_and_cannot_overwrite_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/"birth.txt"
            with patch.object(probe.subprocess, "check_output", return_value="invalid native time"):
                with self.assertRaises(ValueError): probe.birth_observation(123, path)
                self.assertEqual(path.read_text(), "invalid native time\n")
                with self.assertRaises(FileExistsError): probe.birth_observation(123, path)


if __name__ == "__main__": unittest.main()
