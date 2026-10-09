"""Cancellation receipts preserve the original denominator and unknown return."""
import copy
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import signal
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from decision_runtime.artifacts import sealed
from scripts.lib import decision_public_workflow as workflow
from scripts.lib import decision_public_workflow_cancellation as cancellation
from scripts.lib import decision_public_workflow_verification as verification
from scripts.test import test_decision_public_workflow_timeout as deadline
from scripts.test import test_decision_public_workflow_verification as offline
from workers.local_decisions import CALLER_TIMING_VERSION, LocalDecisionClient, PROFILE

ROOT = Path(__file__).resolve().parents[2]
encoded, sha, reseal = offline.encoded, offline.sha, offline.reseal


@lru_cache(maxsize=1)
def prepared_fixture():
    context, recipe, cohort, routes, transport = deadline.fixture()
    spec = cancellation.cancellation_spec(context, 2)
    recipe.pop("callerTimeout"); recipe.update(schemaVersion=cancellation.PLAN_SCHEMA, coordinatorCancellation=spec)
    target = cohort["traces"][2]
    before = copy.deepcopy(target)
    before["run"]["status"] = "running"; before["decisionObservations"] = []
    before["decisionStageInventory"]["stages"][0].update(stageStatus="running", observationRecorded=False)
    before["decisionCallerAccounting"]["stages"][0]["assignments"][0].update(returned=None, outcome="intent_pending")
    before["decisionAssignmentHistory"]["stages"][0]["assignments"][0].update(observation=None, outcome="pending")
    for trace in cohort["traces"]: trace["run"]["status"] = "completed"
    cohort["instances"][2]["status"] = "cancelled"
    routes[2].update(runStatus="cancelled", primaryBranch=False)
    target["run"]["status"] = "cancelled"; target["decisionObservations"] = []
    target["decisionStageInventory"]["stages"][0].update(stageStatus="cancelled", observationRecorded=False)
    target["decisionCallerAccounting"]["stages"][0]["assignments"][0].update(returned=None, outcome="return_missing")
    target["decisionAssignmentHistory"]["stages"][0]["assignments"][0].update(observation=None, outcome="ended_without_observation")
    row = transport["rows"][2]; row["elapsedMs"] = 500.0
    stamp = lambda value: value.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    accepted = datetime.fromisoformat(row["acceptedAt"].replace("Z", "+00:00"))
    row["finishedAt"] = stamp(accepted+timedelta(milliseconds=500))
    transport.update(schemaVersion=cancellation.TRANSPORT_SCHEMA, spec=spec)
    transport = reseal(transport)
    ready = cancellation.ready_receipt(row); drained = cancellation.drain_receipt(row)
    applied = {"schemaVersion": cancellation.APPLIED_SCHEMA, "targetIndex": 2,
        **{key: routes[2][key] for key in ("caseId", "runId", "instanceId", "stageId", "inputSha256")}, "profileSha256": context["profileSha256"],
        "readyFileSha256": sha(encoded(ready)), "beforeTraceFileSha256": sha(encoded(before)),
        "requestMethod": "POST", "requestPath": "/api/v1/runs/"+routes[2]["runId"]+"/cancel", "requestBody": "", "requestBodySha256": sha(b""),
        "unauthenticatedStatus": 401, "httpStatus": 204, "responseBody": "", "responseBodySha256": sha(b""),
        "requestStartedAt": stamp(accepted+timedelta(milliseconds=16)), "responseCompletedAt": stamp(accepted+timedelta(milliseconds=18)),
        "cancelRequestElapsedMs": 2.0}
    http = []
    origin = datetime(2026, 10, 8, 1, 1, tzinfo=timezone.utc)
    def record(path, status, body, start):
        raw = json.dumps(body, ensure_ascii=False).encode()
        finish = start+timedelta(milliseconds=1)
        http.append({"method": "POST", "path": path, "startedAt": stamp(start), "finishedAt": stamp(finish),
            "startedMs": (start-origin).total_seconds()*1000, "finishedMs": (finish-origin).total_seconds()*1000,
            "requestBody": raw.decode(), "requestBodySha256": sha(raw), "requestBodyComplete": True, "httpStatus": status})
    for index, (route, trace, physical) in enumerate(zip(routes, cohort["traces"], transport["rows"])):
        clock = datetime.fromisoformat(physical["acceptedAt"].replace("Z", "+00:00"))
        lease_path = "/api/v1/leases/lease-"+str(index)
        intent = trace["decisionCallerAccounting"]["stages"][0]["assignments"][0]
        record(lease_path+"/renew", 204, {}, clock-timedelta(milliseconds=20))
        record(lease_path+"/decision-shadow/intent", 200,
            {"assignmentId": intent["assignmentId"], "schemaVersion": "agat.decision.caller-accounting.v1"}, clock-timedelta(milliseconds=10))
        if index == 2:
            record(lease_path+"/renew", 404, {}, clock+timedelta(milliseconds=50))
            posted = {"status": "unavailable", "reason": "cancelled", "callerTiming": {
                "schemaVersion": CALLER_TIMING_VERSION, "clock": "monotonic", "boundary": "local_http_call", "durationMs": 500.5}}
        else:
            observed = trace["decisionObservations"][0]["observation"]
            posted = {key: observed[key] for key in ("result", "callerTiming")}
        finish = datetime.fromisoformat(physical["finishedAt"].replace("Z", "+00:00"))
        record(lease_path+"/decision-shadow", 400 if index == 2 else 200, posted, finish+timedelta(milliseconds=1))
        record(lease_path+"/complete", 400 if index == 2 else 200, {"output": "PRIMARY_OUTPUT"}, finish+timedelta(milliseconds=3))
        if index == 2:
            record(lease_path+"/fail", 400, {"error": "ApiError: revoked lease"}, finish+timedelta(milliseconds=5))
    raw = {"caller-cancellation-transport.json": encoded(transport), "coordinator-cancellation-ready.json": encoded(ready),
        "coordinator-cancellation-drained.json": encoded(drained), "coordinator-cancellation-applied.json": encoded(applied),
        "coordinator-cancellation-http.json": encoded(http), "coordinator-cancellation-before.http.json": encoded(before)}
    return context, recipe, cohort, routes, transport, cancellation.receipt_bundle(raw)


def fixture(): return copy.deepcopy(prepared_fixture())


class CancellationInventoryTest(unittest.TestCase):
    def test_integral_float_serialization_preserves_results_but_boolean_drift_fails(self):
        def numbers(value):
            if type(value) is float and value.is_integer(): return int(value)
            if isinstance(value, dict): return {k: numbers(v) for k,v in value.items()}
            if isinstance(value, list): return [numbers(v) for v in value]
            return value
        args = fixture(); args = (*args[:2], numbers(args[2]), *args[3:])
        self.assertEqual(workflow.verify_inventory(*args)["boundCallerReturns"], 5)
        observation = args[2]["traces"][0]["decisionObservations"][0]["observation"]
        observation["result"]["calibration"]["temperature"] = True
        args[2]["traces"][0]["decisionAssignmentHistory"]["stages"][0]["assignments"][0]["observation"] = copy.deepcopy(observation)
        with self.assertRaises(ValueError): workflow.verify_inventory(*args)

    def test_early_renewal_response_may_have_partial_body_but_required_intent_or_return_may_not(self):
        args = fixture(); row = next(r for r in args[-1]["http"] if r["path"].endswith("/renew"))
        row.update(requestBody="", requestBodySha256=sha(b""), requestBodyComplete=False)
        self.assertEqual(workflow.verify_inventory(*args)["incompleteRenewalRequestBodies"], 1)
        for suffix in ("/decision-shadow/intent", "/decision-shadow", "/complete"):
            args = fixture(); row = next(r for r in args[-1]["http"] if r["path"].endswith(suffix))
            row["requestBodyComplete"] = False
            with self.assertRaises(ValueError): workflow.verify_inventory(*args)

    def test_full_inventory_has_one_unknown_return_without_reviving_the_cancelled_process(self):
        result = workflow.verify_inventory(*fixture())
        self.assertEqual(result["scheduled"], 6); self.assertEqual(result["completedInstances"], 5)
        self.assertEqual(result["cancelledInstances"], 1); self.assertEqual(result["boundCallerReturns"], 5)
        self.assertEqual(result["unknownCallerReturns"], 1); self.assertEqual(result["unavailableReturns"], 0)
        self.assertEqual(result["computed"], 4); self.assertEqual(result["healthySuffixCases"], 3)
        self.assertEqual(result["physicalScheduledOutcomes"], {"ok": 5, "context_rejected": 1})
        self.assertEqual(result["physicalDeliveredOutcomes"], {"ok": 4, "context_rejected": 1})
        self.assertEqual(result["localCancelledCallerMs"], 500.5); self.assertEqual(result["cancelRequestToEofMs"], 484)
        self.assertTrue(result["completedPrimaryRoutesPreserved"]); self.assertTrue(result["operatorCancellationPreserved"])
        self.assertNotIn("primaryRoutePreserved", result); self.assertFalse(result["routingEnabled"])

        for suffix, status, foreign in (("renew", 204, True), ("fail", 400, True), ("fail", 200, False), ("fail", 400, False)):
            args = fixture()
            row = copy.deepcopy(next(r for r in args[-1]["http"] if r["path"].endswith("/"+suffix)))
            row["httpStatus"] = status
            if foreign: row["path"] = "/api/v1/leases/unrelated-lease/"+suffix
            args[-1]["http"].append(row)
            with self.assertRaises(ValueError): workflow.verify_inventory(*args)
        for mutation in ("omit", "status", "body", "order"):
            args = fixture(); failure = next(r for r in args[-1]["http"] if r["path"].endswith("/fail"))
            if mutation == "omit": args[-1]["http"].remove(failure)
            elif mutation == "status": failure["httpStatus"] = 200
            elif mutation == "body":
                failure["requestBody"] = '{"error":true}'; failure["requestBodySha256"] = sha(failure["requestBody"].encode())
            else: failure.update(startedAt=args[-1]["http"][0]["startedAt"], finishedAt=args[-1]["http"][0]["finishedAt"])
            with self.assertRaises(ValueError): workflow.verify_inventory(*args)

    def test_false_durable_cancellation_return_late_observation_and_primary_revival_fail(self):
        mutations = (
            lambda c,r: c["traces"][2]["decisionCallerAccounting"]["stages"][0]["assignments"][0].update(returned={"status": "unavailable", "reason": "cancelled"}),
            lambda c,r: c["traces"][2]["decisionAssignmentHistory"]["stages"][0]["assignments"][0].update(outcome="recorded"),
            lambda c,r: c["traces"][2]["decisionStageInventory"]["stages"][0].update(observationRecorded=True),
            lambda c,r: r[2].update(primaryBranch=True), lambda c,r: r[2].update(runStatus="completed"),
            lambda c,r: c["instances"][2].update(status="completed"), lambda c,r: r[2].update(primaryCalls=2),
            lambda c,r: c["traces"][2]["decisionCallerAccounting"]["stages"][0]["assignments"][0].update(intent=False),
            lambda c,r: c["instances"][3].update(status="cancelled"))
        for mutate in mutations:
            args = fixture(); mutate(args[2], args[3])
            with self.assertRaises(ValueError): workflow.verify_inventory(*args)

    def test_cancel_request_auth_binding_before_trace_and_drain_cannot_be_rehashed_away(self):
        mutations = (lambda b: b["applied"].update(httpStatus=401), lambda b: b["applied"].update(runId="other"),
            lambda b: b["applied"].update(readyFileSha256="0"*64), lambda b: b["ready"].update(stageId="other"),
            lambda b: b["drained"].update(rowSha256="0"*64), lambda b: b["before"]["run"].update(status="completed"),
            lambda b: b["before"]["decisionCallerAccounting"]["stages"][0]["assignments"][0].update(returned={}),
            lambda b: b["applied"].update(requestStartedAt="2026-10-08T01:00:00.000Z"),
            lambda b: b["applied"].update(requestBody="{}"), lambda b: b["applied"].update(cancelRequestElapsedMs=5001))
        for mutate in mutations:
            args = fixture(); mutate(args[-1]); args[-1]["ready"] = reseal(args[-1]["ready"]); args[-1]["drained"] = reseal(args[-1]["drained"])
            with self.assertRaises(ValueError): workflow.verify_inventory(*args)

    def test_missing_actual_renewal_late_acceptance_timeout_or_repeated_post_fail(self):
        for mutation in ("missing_renewal", "accepted_late", "timeout", "repeated", "wrong_input", "raw_drift"):
            args = fixture(); records = args[-1]["http"]
            late = next(r for r in records if r["path"] == "/api/v1/leases/lease-2/decision-shadow")
            if mutation == "missing_renewal": records[:] = [r for r in records if r["httpStatus"] != 404]
            elif mutation == "accepted_late": late["httpStatus"] = 200
            elif mutation == "repeated": records.append(copy.deepcopy(late))
            elif mutation == "raw_drift": late["requestBodySha256"] = "0"*64
            else:
                body = json.loads(late["requestBody"])
                if mutation == "timeout": body["reason"] = "timeout"
                else: body["callerTiming"]["durationMs"] = 1.0
                raw = json.dumps(body).encode(); late.update(requestBody=raw.decode(), requestBodySha256=sha(raw))
            with self.assertRaises(ValueError): workflow.verify_inventory(*args)

    def test_full_completed_upstream_and_healthy_suffix_are_required(self):
        for mutate in (lambda t:t["rows"].pop(), lambda t:t["rows"][2].update(responseBytesWritten=1),
                       lambda t:t["rows"][2].update(clientEofObserved=False), lambda t:t.update(upstreamPort=8766),
                       lambda t:t["rows"][0].update(requestBodySha256="0"*64), lambda t:t.update(errors=["TimeoutError"])):
            args = fixture(); mutate(args[4]); args = (*args[:4], reseal(args[4]), args[-1])
            with self.assertRaises(ValueError): workflow.verify_inventory(*args)
        args = fixture(); args[2]["instances"][3]["createdAt"] = args[2]["instances"][2]["createdAt"]
        with self.assertRaises(ValueError): workflow.verify_inventory(*args)

    def test_cancellation_is_prospective_exclusive_and_context_eligible(self):
        for index in (True, 0, 5, -1):
            with self.assertRaises(ValueError): cancellation.cancellation_spec(fixture()[0], index)
        for mutate in (lambda p:p.update(schemaVersion=workflow.PLAN_SCHEMA), lambda p:p.update(callerTimeout={}),
                       lambda p:p["coordinatorCancellation"].update(restart=True), lambda p:p["coordinatorCancellation"].update(cancelledDurableReturn="cancelled")):
            args = fixture(); mutate(args[1])
            with self.assertRaises(ValueError): workflow.verify_inventory(*args)
        with tempfile.TemporaryDirectory() as output:
            args = ["--context-profile", "missing", "--context-profile-file-sha256", "0"*64, "--runtime-python", "missing",
                    "--manifest", "missing", "--evidence-dir", output, "--cancel-run-at-index", "2"]
            with patch.object(deadline.fixtures.cli.subprocess, "Popen") as child:
                self.assertEqual(deadline.fixtures.cli.main(args), 1)
                with self.assertRaises(SystemExit): deadline.fixtures.cli.main(args+["--withhold-response-at-index", "2"])
                child.assert_not_called()


class CancellationOfflineTest(unittest.TestCase):
    pins = offline.WorkflowVerificationTest.pins
    @classmethod
    def setUpClass(cls):
        offline.WorkflowVerificationTest.setUpClass.__func__(cls)
        names = ("scripts/lib/decision_public_workflow_cancellation.py", "scripts/lib/decision_public_workflow_timeout.py",
                 "scripts/lib/decision_public_workflow.py", "scripts/lib/decision_public_workflow_verification.py",
                 "scripts/run-public-support-workflow.py", "scripts/run-public-support-workflow.mts", __file__)
        for name in names:
            relative = str(Path(name).relative_to(ROOT)) if Path(name).is_absolute() else name
            target = cls.root/relative; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes((ROOT/relative).read_bytes())
        for args in (["add", "."], ["-c", "user.name=Synthetic fixture", "-c", "user.email=fixture@example.invalid", "commit", "--quiet", "-m", "Synthetic v5 proof sources"]):
            subprocess.run(["git", *args], cwd=cls.root, check=True)
        cls.commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=cls.root, text=True).strip()
    @classmethod
    def tearDownClass(cls): cls.temporary.cleanup()
    def setUp(self):
        offline.WorkflowVerificationTest.setUp(self)
        context, recipe, cohort, routes, self.transport, self.bundle = fixture()
        context.update(sourceCommit=self.commit, sourceFiles=self.pins(verification.CONTEXT_PATHS)); self.context = reseal(context)
        self.recipe.update(schemaVersion=cancellation.PLAN_SCHEMA, coordinatorCancellation=recipe["coordinatorCancellation"])
        self.cohort.update(instances=cohort["instances"], traces=cohort["traces"], scope=cohort["scope"]); self.routes = routes
        self.plan.update(schemaVersion="agat.decision.public-workflow-launch-plan.v5", coordinatorCancellation=recipe["coordinatorCancellation"],
                         sourceFiles=self.pins(workflow.SOURCE_PATHS+["scripts/test/test_decision_public_workflow_cancellation.py"]))
        self.driver.update(routes=self.routes, actualWindow={k: self.cohort["scope"][k] for k in ("startAt", "endAt")},
            coordinatorCancellationReady=self.bundle["ready"], coordinatorCancellationApplied=self.bundle["applied"], coordinatorCancellationDrained=self.bundle["drained"])
        self.result.update(schemaVersion="agat.decision.public-workflow-launch-result.v5", coordinatorCancellation=recipe["coordinatorCancellation"],
            evidence=workflow.verify_inventory(self.context, self.recipe, self.cohort, self.routes, self.transport, self.bundle))
    def write(self):
        pins = offline.WorkflowVerificationTest.write(self)
        values = {"caller-cancellation-transport.json": self.transport, "coordinator-cancellation-ready.json": self.bundle["ready"],
            "coordinator-cancellation-drained.json": self.bundle["drained"], "coordinator-cancellation-applied.json": self.bundle["applied"],
            "coordinator-cancellation-http.json": self.bundle["http"], "coordinator-cancellation-before.http.json": self.bundle["before"]}
        for name, value in values.items():
            raw = encoded(value); (self.directory/name).write_bytes(raw); self.result["artifactSha256"][name] = sha(raw)
        raw = encoded(reseal(self.result)); (self.directory/"result.json").write_bytes(raw); pins["result_sha"] = sha(raw)
        return pins
    def verify(self): return verification.verify(self.root, self.directory, self.directory/"context.json", **self.write())
    def test_historical_git_replay_is_offline_and_preserves_unknown_returns(self):
        with patch("http.client.HTTPConnection") as http, patch("os.kill") as signal:
            report = self.verify(); http.assert_not_called(); signal.assert_not_called()
        self.assertEqual(report["schemaVersion"], "agat.decision.public-workflow-verification.v5")
        self.assertEqual(report["unknownCallerReturns"], 1); self.assertEqual(report["physicalScheduledHttpHandlers"], 6)
        self.assertEqual(report["completedUndeliveredResponses"], 1); self.assertFalse(report["liveCleanupVerified"])
    def test_raw_http_drift_and_mixed_protocol_do_not_publish_a_report(self):
        pins = self.write(); (self.directory/"coordinator-cancellation-http.json").write_bytes(b"changed")
        with self.assertRaises(ValueError): verification.verify(self.root, self.directory, self.directory/"context.json", **pins)
        self.setUp(); self.plan["schemaVersion"] = "agat.decision.public-workflow-launch-plan.v4"
        with self.assertRaises(ValueError): self.verify()
    def test_resealed_counter_source_and_driver_barrier_drift_fail(self):
        for mutate in (lambda:self.result["samples"][-1]["counters"].update(ok=0),
                       lambda:self.plan["sourceFiles"].pop("scripts/lib/decision_public_workflow_cancellation.py"),
                       lambda:self.driver["coordinatorCancellationApplied"].update(httpStatus=200)):
            self.setUp(); mutate()
            with self.assertRaises(ValueError): self.verify()


class CancellationProxySocketTest(unittest.TestCase):
    def test_ready_precedes_actual_client_disconnect_and_proxy_serves_the_suffix_without_retry(self):
        context, _, _, routes, expected, _ = fixture()
        requests = []; results = [json.loads(row["responseBody"]) for row in expected["rows"]]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args): pass
            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers["content-length"]))); index = len(requests); requests.append(request)
                body = encoded({**results[index], "id": request["id"], "durationMs": 0.0})
                self.send_response(200 if context["inputs"][index]["contextEligible"] else 422)
                self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body)))
                self.end_headers(); self.wfile.write(body)
        upstream = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=upstream.serve_forever, daemon=True); thread.start(); proxy = None
        try:
            proxy = cancellation.CoordinatorCancellationProxy(upstream.server_port, context, cancellation.cancellation_spec(context, 2))
            client = LocalDecisionClient(f"http://127.0.0.1:{proxy.port}"); received = []
            for index, (case, route) in enumerate(zip(context["inputs"], routes)):
                stop = threading.Event()
                def invoke(): received.append(client.decide({"profile": PROFILE, "profileSha256": context["profileSha256"],
                    "request": {**case["request"], "id": route["stageId"]}, "timeoutMs": 10000, "callerTimingVersion": CALLER_TIMING_VERSION}, stop))
                if index == 2:
                    task = threading.Thread(target=invoke); task.start()
                    try:
                        budget = time.monotonic()+2
                        while proxy.ready_receipt() is None and time.monotonic()<budget: time.sleep(0.01)
                        self.assertIsNotNone(proxy.ready_receipt()); self.assertEqual(len(received), 2)
                        self.assertIsNone(proxy.target_receipt()); stop.set(); task.join(1)
                        self.assertFalse(task.is_alive()); self.assertEqual(received[-1]["reason"], "cancelled")
                    finally: stop.set(); task.join(2)
                    budget = time.monotonic()+1
                    while proxy.target_receipt() is None and time.monotonic()<budget: time.sleep(0.01)
                    self.assertIsNotNone(proxy.target_receipt())
                else: invoke()
            proxy.close(); transport = proxy.receipt()
            self.assertEqual(len(requests), 6); self.assertEqual(transport["completedUpstreamPosts"], 6)
            self.assertEqual(transport["rows"][2]["responseBytesWritten"], 0); self.assertTrue(transport["rows"][2]["clientEofObserved"])
            self.assertEqual(transport["errors"], []); self.assertFalse(transport["activeHandlers"])
        finally:
            if proxy is not None and not proxy.closed: proxy.close()
            upstream.shutdown(); upstream.server_close(); thread.join(2)


class CancellationDriverTest(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "Requires isolated POSIX process-group cleanup")
    def test_actual_coordinator_and_worker_keep_cancelled_assignment_unknown_and_complete_suffix(self):
        context, _, _, _, expected, _ = fixture()
        requests = []; templates = [json.loads(row["responseBody"]) for row in expected["rows"]]
        profile_json = json.dumps(context["profile"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        self.assertEqual(sha(profile_json.encode()), context["profileSha256"])
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args): pass
            def respond(self, status, value):
                body = encoded(value); self.send_response(status); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            def do_GET(self):
                if self.path != "/health": self.send_error(404); return
                self.respond(200, {"status": "ready", "mode": "shadow", "profileSha256": context["profileSha256"], "profileJson": profile_json})
            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers["content-length"]))); index = len(requests); requests.append(request)
                self.respond(200 if context["inputs"][index]["contextEligible"] else 422, {**templates[index], "id": request["id"], "durationMs": 0.0})
        upstream = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        serving = threading.Thread(target=upstream.serve_forever, daemon=True); serving.start(); proxy = None; child = None
        try:
            spec = cancellation.cancellation_spec(context, 2)
            proxy = cancellation.CoordinatorCancellationProxy(upstream.server_port, context, spec)
            with tempfile.TemporaryDirectory(dir=ROOT/"docs/private", prefix="cancellation-fixture-") as temporary:
                directory = Path(temporary)
                (directory/"plan.json").write_bytes(encoded({"schemaVersion": "agat.decision.public-workflow-launch-plan.v5",
                    "context": context, "config": workflow.shared_config(context), "coordinatorCancellation": spec}))
                with (directory/"driver.log").open("wb") as log:
                    child = subprocess.Popen(["node", "--import", "tsx", "scripts/run-public-support-workflow.mts", "--decision-url",
                        f"http://127.0.0.1:{proxy.port}", "--evidence-dir", str(directory)], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                    budget = time.monotonic()+40
                    while child.poll() is None and time.monotonic()<budget:
                        for name, receipt in (("coordinator-cancellation-ready.json", proxy.ready_receipt()),
                                              ("coordinator-cancellation-drained.json", proxy.target_receipt())):
                            if receipt is not None and not (directory/name).exists():
                                pending = directory/(name+".pending")
                                pending.write_bytes(encoded(receipt)); pending.rename(directory/name)
                        time.sleep(0.01)
                    if child.poll() is None: os.killpg(child.pid, signal.SIGTERM); child.wait(timeout=8)
                self.assertEqual(child.returncode, 0, (directory/"driver.log").read_text()[-4000:])
                proxy.close(); transport = proxy.receipt()
                raw = {name: (directory/name).read_bytes() for name in cancellation.ARTIFACTS if name != "caller-cancellation-transport.json"}
                raw["caller-cancellation-transport.json"] = encoded(transport)
                bundle = cancellation.receipt_bundle(raw)
                cohort = json.loads((directory/"cohort.http.json").read_bytes())
                recipe = json.loads((directory/"workflow-plan.json").read_bytes())
                driver = json.loads((directory/"workflow-driver.json").read_bytes())
                result = workflow.verify_inventory(context, recipe, cohort, driver["routes"], transport, bundle)
                self.assertEqual(result["unknownCallerReturns"], 1); self.assertEqual(result["completedInstances"], 5)
                self.assertEqual(result["healthySuffixCases"], 3); self.assertEqual(result["attemptedCallerReturns"], 6)
                self.assertEqual(len(requests), 6); self.assertFalse((directory/"worker-credentials.json").exists())
        finally:
            if child is not None:
                # This test created a separate group containing only its Node
                # driver, Python worker and disposable HTTP helpers.
                try: os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError: pass
                child.wait()
            if proxy is not None and not proxy.closed: proxy.close()
            upstream.shutdown(); upstream.server_close(); serving.join(2)


if __name__ == "__main__": unittest.main()
