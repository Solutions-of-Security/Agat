import copy
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import select
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from decision_runtime.artifacts import sealed
from decision_runtime.metrics import DecisionMetrics
from scripts.lib import decision_public_workflow as workflow
from scripts.lib import decision_public_workflow_recovery as recovery
from scripts.lib import decision_public_workflow_recovery_verification as verification
from scripts.lib.decision_public_workflow_loss import ResetGuard
from scripts.lib.decision_public_load_verification import CONTEXT_PATHS
from scripts.test import test_decision_public_workflow_verification as original
from scripts.test import test_decision_public_load_verification as load_fixtures
from scripts.test import test_decision_public_workflow as workflow_fixtures
from workers.local_decisions import LocalDecisionClient, CALLER_TIMING_VERSION, PROFILE

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("public_workflow_recovery_cli", ROOT/"scripts/run-public-support-workflow-recovery.py")
cli = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(cli)


class RecoveryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(); cls.root = Path(cls.temporary.name)
        cls.original_support = workflow_fixtures.fixture(); cls.original_load = load_fixtures.fixture()
        paths = workflow.SOURCE_PATHS+recovery.SOURCE_PATHS+CONTEXT_PATHS
        names = subprocess.check_output(["git", "ls-files", "--", *paths], cwd=ROOT, text=True).splitlines()
        names = set(names)|set(recovery.SOURCE_PATHS)|{"scripts/lib/decision_public_workflow_recovery.py", "scripts/lib/decision_public_workflow_recovery_verification.py"}
        for name in names:
            target = cls.root/name; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes((ROOT/name).read_bytes())
        for args in (["init", "--quiet"], ["add", "."], ["-c", "user.name=Synthetic fixture", "-c", "user.email=fixture@example.invalid", "commit", "--quiet", "-m", "Synthetic crash/recovery receipts"]):
            subprocess.run(["git", *args], cwd=cls.root, check=True)
        cls.commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=cls.root, text=True).strip()
    @classmethod
    def tearDownClass(cls): cls.temporary.cleanup()
    pins = original.WorkflowVerificationTest.pins

    def setUp(self):
        with patch.object(original.fixtures, "fixture", return_value=copy.deepcopy(self.original_support)), patch.object(original.load_fixtures, "fixture", return_value=copy.deepcopy(self.original_load)):
            original.WorkflowVerificationTest.setUp(self)
        self.spec = recovery.recovery_spec(self.context, 2)
        self.plan.update(schemaVersion=recovery.LAUNCH_PLAN, runtimeRecovery=self.spec, warmupCount=4,
                         sourceFiles=self.pins(workflow.SOURCE_PATHS+recovery.SOURCE_PATHS))
        self.recipe.update(schemaVersion=recovery.PLAN_SCHEMA, runtimeRecovery=self.spec)
        timing = {"schemaVersion": CALLER_TIMING_VERSION, "clock": "monotonic", "boundary": "local_http_call", "durationMs": 20.}
        unavailable = {"mode": "shadow", "fallback": "primary", "status": "unavailable", "reason": "unreachable", "callerTiming": timing}
        target = self.cohort["traces"][2]
        target["decisionObservations"][0]["observation"] = copy.deepcopy(unavailable)
        target["decisionAssignmentHistory"]["stages"][0]["assignments"][0]["observation"] = copy.deepcopy(unavailable)
        target["decisionCallerAccounting"]["stages"][0]["assignments"][0]["returned"] = {"status": "unavailable", "reason": "unreachable", "callerTiming": timing}
        self.request = {"schemaVersion": recovery.REQUEST_SCHEMA, "targetIndex": 2, "afterCaseId": self.routes[1]["caseId"], "afterRunId": self.routes[1]["runId"], "createdAt": self.at(150)}
        self.armed = {"schemaVersion": recovery.ARMED_SCHEMA, "targetIndex": 2, "requestFileSha256": "pending", "runtimePid": 44, "port": 18888,
                      "profileSha256": self.context["profileSha256"], "armedAt": self.at(160)}
        self.started = {"schemaVersion": recovery.STARTED_SCHEMA, "targetIndex": 2, "caseId": self.routes[2]["caseId"], "inputSha256": self.routes[2]["inputSha256"],
                        "instanceId": self.routes[2]["instanceId"], "runId": self.routes[2]["runId"], "createdAt": self.at(180)}
        self.crashed = {"schemaVersion": recovery.CRASH_SCHEMA, "targetIndex": 2, "requestFileSha256": "pending", "startedFileSha256": "pending", "snapshotFileSha256": "pending",
                        "port": 18888, "profileSha256": self.context["profileSha256"], "runtimePid": 44, "processGroupId": 44, "memberPids": [44, 46],
                        "signal": "SIGKILL", "signalSentAtEpoch": datetime.fromisoformat(self.at(200).replace("Z", "+00:00")).timestamp(),
                        "runtimeExitCode": -9, "runtimeExited": True, "remainingOwnedPids": [], "appliedAt": self.at(210)}
        self.recovery_request = {"schemaVersion": recovery.RECOVERY_REQUEST, "targetIndex": 2, "afterCaseId": self.routes[2]["caseId"], "afterRunId": self.routes[2]["runId"],
                                 "stageId": self.routes[2]["stageId"], "observation": copy.deepcopy(unavailable), "createdAt": self.at(220)}
        self.recovered = {"schemaVersion": recovery.RECOVERED_SCHEMA, "targetIndex": 2, "requestFileSha256": "pending", "crashSealSha256": "pending", "runtimePid": 45,
                          "port": 18888, "profileSha256": self.context["profileSha256"], "readyServerStart": 1001., "warmupCount": 2, "appliedAt": self.at(340)}
        self.transport = {"schemaVersion": "agat.decision.public-workflow-reset-guard.v1", "acceptedConnections": 0, "resetConnections": 0, "errors": [], "payloadsRead": False}
        for row, when in zip(self.cohort["instances"], (100, 110, 170, 350, 360, 370)): row["createdAt"] = self.at(when)
        self.cohort["scope"]["endAt"] = self.at(500); self.driver["actualWindow"]["endAt"] = self.at(500)
        self.result.update(schemaVersion=recovery.LAUNCH_RESULT, runtimeRecovery={}, ownedPids=[42, 43, 44, 45, 46, 47], runtimeExitCodes=[-9, 130], elapsedMs=600,
                           evidence=workflow.verify_inventory(self.context, self.recipe, self.cohort, self.routes))
        self.result.pop("runtimeExitCode")
        health = self.result["samples"][0]["health"]; samples = []
        with patch("time.time", return_value=1000.): first = DecisionMetrics()
        with patch("time.time", return_value=1001.): second = DecisionMetrics()
        def completed(metrics, value, count):
            for _ in range(count): metrics.begin(); metrics.finish(value, .005)
        def snapshot(metrics, epoch, label, when):
            raw = metrics.render(ready=True).decode(); values, start = recovery.http_metrics(raw, in_progress=0)
            samples.append({"label": label, "elapsedMs": when, "capturedAt": self.at(when), "epoch": epoch, "runtimePid": 44+epoch, "health": copy.deepcopy(health),
                            "metricsRaw": raw, "ownedPids": [44+epoch], "processRaw": "Synthetic fixture, no live PID claim", "counters": values, "serverStart": start})
        snapshot(first, 0, "ready_before_scoring", 10); completed(first, "ok", 2); snapshot(first, 0, "after_warmup", 80)
        completed(first, "ok", 2); snapshot(first, 0, "before_crash_armed", 140)
        first.begin(); raw = first.render(ready=True).decode(); values, start = recovery.http_metrics(raw, in_progress=1)
        self.inflight = {"schemaVersion": recovery.SNAPSHOT_SCHEMA, "capturedAt": self.at(190), "elapsedMs": 190, "runtimePid": 44,
                        "profileSha256": self.context["profileSha256"], "metricsRaw": raw, "counters": values, "serverStart": start}
        snapshot(second, 1, "recovered_before_scoring", 250); completed(second, "ok", 2); snapshot(second, 1, "recovered_after_warmup", 330)
        completed(second, "ok", 2); completed(second, "context_rejected", 1); snapshot(second, 1, "after_inventory", 510)
        self.result["samples"] = samples
        _, _, original_load = copy.deepcopy(self.original_load); warmups = []
        for epoch in range(2):
            for iteration, row in enumerate(original_load["warmup"]):
                began = (15 if epoch == 0 else 255)+iteration*32
                warmups.append({k: copy.deepcopy(value) for k, value in row.items() if k not in {"inputSha256", "inputTokens"}} |
                               {"epoch": epoch, "startedMs": began, "finishedMs": began+31})
        self.result["warmup"] = warmups
    @staticmethod
    def at(milliseconds): return f"2026-10-08T01:01:00.{milliseconds:03d}Z"

    def write(self):
        artifacts = {"runtime-crash-request.json": original.encoded(self.request), "runtime-crash-started.json": original.encoded(self.started),
                     "runtime-crash-inflight.json": original.encoded(self.inflight), "runtime-recovery-request.json": original.encoded(self.recovery_request),
                     "runtime-recovery-transport.json": original.encoded(self.transport), "runtime-recovered.log": b"Synthetic replacement; no native model.\n"}
        self.armed["requestFileSha256"] = original.sha(artifacts["runtime-crash-request.json"]); self.armed = original.reseal(self.armed)
        self.crashed.update(requestFileSha256=self.armed["requestFileSha256"], startedFileSha256=original.sha(artifacts["runtime-crash-started.json"]), snapshotFileSha256=original.sha(artifacts["runtime-crash-inflight.json"]))
        self.crashed = original.reseal(self.crashed); self.recovered.update(requestFileSha256=original.sha(artifacts["runtime-recovery-request.json"]), crashSealSha256=self.crashed["sha256"])
        self.recovered = original.reseal(self.recovered)
        artifacts.update({"runtime-crash-armed.json": original.encoded(self.armed), "runtime-crash-applied.json": original.encoded(self.crashed), "runtime-recovery-applied.json": original.encoded(self.recovered)})
        self.result["runtimeRecovery"] = {"spec": self.spec, "armed": self.armed, "crashed": self.crashed, "recovered": self.recovered}
        self.driver["runtimeRecovery"] = {key: value for key, value in self.result["runtimeRecovery"].items() if key != "spec"}
        pins = original.WorkflowVerificationTest.write(self)
        for name, raw in artifacts.items(): (self.directory/name).write_bytes(raw)
        self.result["artifactSha256"].update({name: original.sha(raw) for name, raw in artifacts.items()})
        raw = original.encoded(original.reseal(self.result)); (self.directory/"result.json").write_bytes(raw); pins["result_sha"] = original.sha(raw)
        return pins
    def verify(self): return verification.verify(self.root, self.directory, self.directory/"context.json", **self.write())

    def test_actual_git_replay_retains_two_origins_and_unknown_crash_outcome_offline(self):
        actual = subprocess.Popen
        def git_only(args, *a, **kw):
            if args[0] != "git" or args[1] not in {"ls-tree", "archive"}: raise AssertionError("Offline replay started a native process")
            return actual(args, *a, **kw)
        pins = self.write()
        with patch("http.client.HTTPConnection") as http, patch.object(recovery, "crash_owned") as crash, patch.object(subprocess, "Popen", side_effect=git_only):
            report = verification.verify(self.root, self.directory, self.directory/"context.json", **pins); http.assert_not_called(); crash.assert_not_called()
        self.assertEqual(report["inventory"]["scheduled"], 6); self.assertEqual(report["inventory"]["computed"], 4)
        self.assertEqual(report["physicalScheduledCompletedHandlers"], 5); self.assertEqual(report["physicalCompletedHttpHandlers"], 9)
        self.assertEqual(report["warmupCalls"], 4); self.assertEqual(report["interruptedScheduledHandlers"], 1)
        self.assertEqual(report["interruptedTerminalOutcome"], "unknown_after_crash"); self.assertEqual(report["transportResetConnections"], 0)
        self.assertFalse(report["liveCleanupVerified"]); self.assertFalse(report["gpuForwardInterruptionEstablished"])

    def test_rehashed_origin_ownership_counter_timing_and_authority_corruptions_fail(self):
        changes = (lambda: self.crashed.update(runtimeExitCode=130), lambda: self.crashed.update(processGroupId=42),
            lambda: self.crashed.update(memberPids=[44, True]), lambda: self.crashed.update(remainingOwnedPids=[46]),
            lambda: self.crashed.update(signalSentAtEpoch=self.crashed["signalSentAtEpoch"]+1),
            lambda: self.recovered.update(runtimePid=44), lambda: self.recovered.update(port=8766), lambda: self.recovered.update(warmupCount=True),
            lambda: self.recovered.update(readyServerStart=1000), lambda: self.inflight.update(metricsRaw=self.inflight["metricsRaw"].replace("requests_in_progress 1", "requests_in_progress 0")),
            lambda: self.inflight["counters"].update(ok=5), lambda: self.request.update(afterRunId="foreign"),
            lambda: self.recovery_request["observation"].update(reason="timeout"), lambda: self.transport.update(resetConnections=1, acceptedConnections=1),
            lambda: self.cohort["instances"][3].update(createdAt=self.at(300)), lambda: self.result["warmup"][2].update(epoch=0),
            lambda: self.result["samples"][-1]["counters"].update(context_rejected=0), lambda: self.result.update(referenceLabels=False),
            lambda: self.result["runtimeExitCodes"].pop(), lambda: self.plan["sourceFiles"].pop("scripts/run-public-support-workflow-recovery.py"))
        for change in changes:
            self.setUp(); change()
            with self.assertRaises(ValueError): self.verify()

    def test_missing_or_extra_unavailable_and_primary_changes_cannot_pass(self):
        for mutate in (lambda: self.routes.pop(), lambda: self.routes[2].update(primaryBranch=False),
                       lambda: self.cohort["traces"][3]["decisionObservations"][0].update(observation=copy.deepcopy(self.recovery_request["observation"])),
                       lambda: self.recipe.update(schemaVersion=workflow.PLAN_SCHEMA), lambda: self.spec.update(retryCount=1)):
            self.setUp(); mutate()
            with self.assertRaises(ValueError): self.verify()

    def test_raw_receipt_and_mid_replay_journal_mutations_fail_before_publication(self):
        pins = self.write(); (self.directory/"runtime-crash-inflight.json").write_bytes(b"changed")
        with self.assertRaises(ValueError): verification.verify(self.root, self.directory, self.directory/"context.json", **pins)
        pins = self.write(); actual = recovery.verify_physical
        def change(*args):
            report = actual(*args); (self.directory/"workflow-routes.jsonl").write_bytes(b"changed after consumption"); return report
        with patch.object(recovery, "verify_physical", side_effect=change), self.assertRaises(ValueError):
            verification.verify(self.root, self.directory, self.directory/"context.json", **pins)

    def test_spec_rejects_over_context_edge_and_boolean_targets(self):
        for index in (0, True, -1, 5, 6):
            with self.assertRaises(ValueError): recovery.recovery_spec(self.context, index)
        for mutate in (lambda: self.spec.update(restart=1), lambda: self.spec.update(extra="ignored"), lambda: self.spec.update(targetInputSha256="0"*64)):
            self.setUp(); mutate()
            with self.assertRaises(ValueError): workflow.verify_inventory(self.context, self.recipe, self.cohort, self.routes)

    def test_barrier_publication_is_private_exclusive_and_has_no_partial_final(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary); value = sealed(self.armed)
            recovery.publish(directory, "runtime-crash-armed.json", value)
            self.assertEqual(json.loads((directory/"runtime-crash-armed.json").read_bytes()), value)
            self.assertEqual((directory/"runtime-crash-armed.json").stat().st_mode&0o777, 0o600)
            self.assertFalse((directory/"runtime-crash-armed.pending.json").exists())
            with self.assertRaises(FileExistsError): recovery.publish(directory, "runtime-crash-armed.json", value)

    @unittest.skipUnless(os.name == "posix", "Owned POSIX process session required")
    def test_actual_active_socket_crash_recovers_same_port_and_preserves_other_listener(self):
        code = '''import http.server,json,sys,time
body=json.load(open(sys.argv[3]));old=sys.argv[2]=="old"
class Handler(http.server.BaseHTTPRequestHandler):
 def log_message(self,*a):pass
 def do_POST(self):
  self.rfile.read(int(self.headers["Content-Length"]))
  if old:print("ACTIVE",flush=True);time.sleep(30)
  else:
   time.sleep(.01);raw=json.dumps(body).encode();self.send_response(200);self.send_header("Content-Type","application/json");self.send_header("Content-Length",str(len(raw)));self.end_headers();self.wfile.write(raw)
class Server(http.server.ThreadingHTTPServer):allow_reuse_address=True
s=Server(("127.0.0.1",int(sys.argv[1])),Handler);print(s.server_port,flush=True);s.serve_forever()
'''
        old = None; new = None; guard = None; owned = set(); errors = []; returned = []; thread = None
        _, _, synthetic = copy.deepcopy(self.original_load); request = self.context["inputs"][0]["request"]
        shadow = {"profile": PROFILE, "profileSha256": self.context["profileSha256"], "request": request,
                  "timeoutMs": 10000, "callerTimingVersion": CALLER_TIMING_VERSION}
        with tempfile.TemporaryDirectory() as temporary, socket.socket() as other:
            body = Path(temporary)/"body.json"; body.write_bytes(original.encoded(synthetic["warmup"][0]["observation"]["result"]))
            other.bind(("127.0.0.1", 0)); other.listen(); other_port = other.getsockname()[1]
            def line(child):
                self.assertTrue(select.select([child.stdout], [], [], 5)[0], "Owned fixture did not expose its socket barrier")
                return child.stdout.readline()
            try:
                old = subprocess.Popen([sys.executable, "-B", "-u", "-c", code, "0", "old", str(body)], stdout=subprocess.PIPE, start_new_session=True, text=True)
                port = int(line(old)); client = LocalDecisionClient(f"http://127.0.0.1:{port}")
                thread = threading.Thread(target=lambda: returned.append(client.decide(shadow))); thread.start()
                self.assertEqual(line(old).strip(), "ACTIVE")
                receipt = recovery.crash_owned(old, port, cli.runtime, owned, errors)
                self.assertEqual(receipt["runtimeExitCode"], -9); self.assertEqual(errors, [])
                guard = ResetGuard(port)
                thread.join(3); self.assertFalse(thread.is_alive()); self.assertEqual(returned[0]["status"], "unavailable"); self.assertEqual(returned[0]["reason"], "unreachable")
                with socket.create_connection(("127.0.0.1", other_port), timeout=1): pass
                with self.assertRaises(ValueError): recovery.crash_owned(old, port, cli.runtime, owned, errors)
                drained = guard; guard.close(); guard = None; self.assertEqual(drained.receipt()["acceptedConnections"], 0)
                new = subprocess.Popen([sys.executable, "-B", "-u", "-c", code, str(port), "new", str(body)], stdout=subprocess.PIPE, start_new_session=True, text=True)
                self.assertEqual(int(line(new)), port)
                result = client.decide(shadow); self.assertEqual(result["result"]["status"], "ok")
            finally:
                if guard is not None: guard.close()
                for process in (new, old):
                    if process is not None:
                        if process.poll() is None: process.kill(); process.wait(3)
                        process.stdout.close()
                if thread is not None: thread.join(3)

    def test_existing_output_is_rejected_before_sources_weights_or_processes(self):
        with tempfile.TemporaryDirectory() as temporary:
            args = ["--context-profile", "missing", "--context-profile-file-sha256", "0"*64, "--runtime-python", "missing", "--manifest", "missing",
                    "--evidence-dir", temporary, "--crash-at-index", "3"]
            with patch.object(cli.launcher, "frozen_sources") as sources, patch.object(cli.subprocess, "Popen") as process:
                self.assertEqual(cli.main(args), 1); sources.assert_not_called(); process.assert_not_called()


if __name__ == "__main__": unittest.main()
