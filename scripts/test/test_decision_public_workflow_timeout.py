import copy
from datetime import datetime, timedelta, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from decision_runtime.artifacts import sealed
from scripts.lib import decision_public_workflow as workflow
from scripts.lib import decision_public_workflow_timeout as timeout
from scripts.lib import decision_public_workflow_verification as verification
from scripts.test import test_decision_public_workflow as fixtures
from scripts.test import test_decision_public_workflow_verification as offline
from workers.local_decisions import LocalDecisionClient, PROFILE, CALLER_TIMING_VERSION

ROOT = Path(__file__).resolve().parents[2]
encoded = offline.encoded
sha = offline.sha
reseal = offline.reseal


def fixture():
    context, plan, cohort, routes = fixtures.fixture()
    spec = timeout.timeout_spec(context, 2)
    plan.update(schemaVersion=timeout.PLAN_SCHEMA, callerTimeout=spec)
    started = datetime(2026, 10, 8, 1, 1, tzinfo=timezone.utc)
    clock = started+timedelta(milliseconds=100)
    stamp = lambda value: value.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    rows = []
    for index, (case, route, trace) in enumerate(zip(context["inputs"], routes, cohort["traces"])):
        cohort["instances"][index]["createdAt"] = stamp(clock-timedelta(milliseconds=50))
        result = copy.deepcopy(trace["decisionObservations"][0]["observation"]["result"])
        body = encoded({**case["request"], "id": route["stageId"]}); response = encoded(result)
        elapsed = 10000 if index == spec["targetIndex"] else 6
        row = {"index":index, "caseId":case["id"], "stageId":route["stageId"], "inputSha256":case["inputSha256"],
            "requestBody":body.decode(), "requestBodySha256":sha(body), "profileSha256":context["profileSha256"],
            "cancelOnDisconnect":True, "acceptedAt":stamp(clock), "upstreamCompletedAt":stamp(clock+timedelta(milliseconds=6)),
            "finishedAt":stamp(clock+timedelta(milliseconds=elapsed)), "upstreamMs":6.0, "elapsedMs":float(elapsed),
            "upstreamStatus":200 if case["contextEligible"] else 422, "upstreamContentType":"application/json",
            "responseBody":response.decode(), "responseBodySha256":sha(response), "withheld":index==2,
            "downstreamWriteCompleted":index!=2, "responseBytesWritten":0 if index==2 else len(response), "clientEofObserved":index==2}
        rows.append(row); clock += timedelta(milliseconds=elapsed+100)
        if index == 2:
            observation = trace["decisionObservations"][0]["observation"]
            observation.pop("result"); observation.update(status="unavailable", reason="timeout")
            observation["callerTiming"]["durationMs"] = 10000.0
            trace["decisionAssignmentHistory"]["stages"][0]["assignments"][0]["observation"] = copy.deepcopy(observation)
            trace["decisionCallerAccounting"]["stages"][0]["assignments"][0]["returned"] = {
                "callerTiming":copy.deepcopy(observation["callerTiming"]), "status":"unavailable", "reason":"timeout"}
    cohort["scope"]["endAt"] = stamp(clock+timedelta(milliseconds=100))
    transport = sealed({"schemaVersion":timeout.TRANSPORT_SCHEMA, "spec":spec, "proxyPort":23456, "upstreamPort":23457,
        "startedAt":stamp(started), "closedAt":stamp(clock+timedelta(milliseconds=200)), "rows":rows, "acceptedPosts":len(rows),
        "completedUpstreamPosts":len(rows), "withheldResponses":1, "errors":[], "closed":True, "activeHandlers":0})
    return context, plan, cohort, routes, transport


class TimeoutInventoryTest(unittest.TestCase):
    def test_explicit_default_abstain_flags_preserve_contract_input_identity(self):
        context, plan, cohort, routes, transport = fixture()
        for row in transport["rows"]:
            request=json.loads(row["requestBody"])
            for option in request["options"]: option.setdefault("abstain",False)
            body=encoded(request);row.update(requestBody=body.decode(),requestBodySha256=sha(body))
        result=workflow.verify_inventory(context,plan,cohort,routes,reseal(transport))
        self.assertEqual(result["physicalScheduledHttpHandlers"],6)
        self.assertTrue(result["caseInputsUnchanged"])

    def test_full_denominator_separates_computed_delivered_and_completed_undelivered(self):
        context, plan, cohort, routes, transport = fixture()
        result = workflow.verify_inventory(context, plan, cohort, routes, transport)
        self.assertEqual(result["scheduled"], 6); self.assertEqual(result["boundCallerReturns"], 6)
        self.assertEqual(result["computed"], 4); self.assertEqual(result["unavailableReturns"], 1)
        self.assertEqual(result["completedUndeliveredResponses"], 1)
        self.assertEqual(result["physicalDeliveredOutcomes"], {"ok":4, "context_rejected":1})
        self.assertEqual(result["physicalScheduledOutcomes"], {"ok":5, "context_rejected":1})
        self.assertEqual(result["healthySuffixCases"], 3)
        self.assertFalse(result["routingEnabled"]); self.assertFalse(result["runtimeRestarted"])

    def test_fault_must_be_prospective_exclusive_and_context_eligible(self):
        for index in (True, 0, 5, 6, -1):
            context, _, _, _, _ = fixture()
            with self.assertRaises(ValueError): timeout.timeout_spec(context, index)
        for mutate in (lambda p,t:p.update(schemaVersion=workflow.PLAN_SCHEMA),
                       lambda p,t:p.update(runtimeLoss={"beforeIndex":2}),
                       lambda p,t:p.update(runtimeRecovery={"targetIndex":2}),
                       lambda p,t:p["callerTimeout"].update(restart=True),
                       lambda p,t:p["callerTimeout"].update(callerTimeoutMs=1000)):
            context, plan, cohort, routes, transport = fixture(); mutate(plan, transport)
            with self.assertRaises(ValueError): workflow.verify_inventory(context, plan, cohort, routes, transport)

    def test_rehashed_missing_posts_body_drift_early_eof_extra_response_and_bad_delivery_fail(self):
        mutations = (lambda t:t["rows"].pop(), lambda t:t.update(acceptedPosts=7),
                     lambda t:t["rows"][2].update(responseBytesWritten=1),
                     lambda t:t["rows"][2].update(clientEofObserved=False),
                     lambda t:t["rows"][2].update(elapsedMs=100.0),
                     lambda t:t["rows"][2].update(upstreamMs=10001.0),
                     lambda t:t["rows"][1].update(responseBodySha256="0"*64),
                     lambda t:t["rows"][1].update(requestBodySha256="0"*64),
                     lambda t:t["rows"][1].update(stageId="other"),
                     lambda t:t["rows"][3].update(downstreamWriteCompleted=False),
                     lambda t:t.update(proxyPort=8766), lambda t:t.update(activeHandlers=True),
                     lambda t:t.update(closed=False), lambda t:t.update(errors=["TimeoutError"]))
        for mutate in mutations:
            context, plan, cohort, routes, transport = fixture(); mutate(transport); transport=reseal(transport)
            with self.assertRaises(ValueError): workflow.verify_inventory(context, plan, cohort, routes, transport)

    def test_rehashed_caller_return_result_deadline_missing_intent_primary_and_suffix_fail(self):
        mutations = (lambda c,r:c["traces"][2]["decisionObservations"][0]["observation"].update(result={}),
                     lambda c,r:c["traces"][2]["decisionObservations"][0]["observation"].update(reason="unreachable"),
                     lambda c,r:c["traces"][2]["decisionCallerAccounting"]["stages"][0]["assignments"][0].update(intent=False),
                     lambda c,r:r[2].update(primaryBranch=False), lambda c,r:r[2].update(primaryCalls=2),
                     lambda c,r:c["instances"][3].update(createdAt=c["instances"][2]["createdAt"]))
        for mutate in mutations:
            context, plan, cohort, routes, transport = fixture(); mutate(cohort,routes)
            with self.assertRaises(ValueError): workflow.verify_inventory(context, plan, cohort, routes, transport)

    def test_existing_output_and_conflicting_faults_rejected_before_native_calls(self):
        with tempfile.TemporaryDirectory() as temporary:
            args=["--context-profile","missing","--context-profile-file-sha256","0"*64,"--runtime-python","missing",
                  "--manifest","missing","--evidence-dir",temporary,"--withhold-response-at-index","2"]
            with patch.object(fixtures.cli.launcher,"frozen_sources") as sources, patch.object(fixtures.cli.subprocess,"Popen") as child:
                self.assertEqual(fixtures.cli.main(args),1); sources.assert_not_called(); child.assert_not_called()
                with self.assertRaises(SystemExit): fixtures.cli.main(args+["--stop-runtime-before-index","2"])
                child.assert_not_called()


class TimeoutOfflineTest(unittest.TestCase):
    pins = offline.WorkflowVerificationTest.pins

    @classmethod
    def setUpClass(cls):
        offline.WorkflowVerificationTest.setUpClass.__func__(cls)
        for name in ("scripts/lib/decision_public_workflow_timeout.py", "scripts/test/test_decision_public_workflow_timeout.py",
                     "scripts/lib/decision_public_workflow.py", "scripts/lib/decision_public_workflow_verification.py",
                     "scripts/run-public-support-workflow.py", "scripts/run-public-support-workflow.mts"):
            path=cls.root/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes((ROOT/name).read_bytes())
        for args in (["add","."],["-c","user.name=Synthetic fixture","-c","user.email=fixture@example.invalid","commit","--quiet","-m","Synthetic v4 proof sources"]):
            subprocess.run(["git",*args],cwd=cls.root,check=True)
        cls.commit=subprocess.check_output(["git","rev-parse","HEAD"],cwd=cls.root,text=True).strip()

    @classmethod
    def tearDownClass(cls): cls.temporary.cleanup()

    def setUp(self):
        offline.WorkflowVerificationTest.setUp(self)
        context, recipe, cohort, routes, transport = fixture()
        context.update(sourceCommit=self.commit,sourceFiles=self.pins(verification.CONTEXT_PATHS))
        self.context=reseal(context); self.recipe.update(schemaVersion=recipe["schemaVersion"],callerTimeout=recipe["callerTimeout"])
        self.cohort["instances"]=cohort["instances"]; self.cohort["traces"]=cohort["traces"]; self.cohort["scope"]=cohort["scope"]
        self.transport=transport; self.drained=timeout.drain_receipt(transport["rows"][2])
        self.plan.update(schemaVersion="agat.decision.public-workflow-launch-plan.v4",callerTimeout=recipe["callerTimeout"],
            sourceFiles=self.pins(workflow.SOURCE_PATHS+["scripts/test/test_decision_public_workflow_timeout.py"]))
        self.driver.update(actualWindow={k:self.cohort["scope"][k] for k in ("startAt","endAt")},callerTimeoutDrained=self.drained)
        self.result.update(schemaVersion="agat.decision.public-workflow-launch-result.v4",callerTimeout=recipe["callerTimeout"],
            evidence=workflow.verify_inventory(self.context,self.recipe,self.cohort,self.routes,self.transport))

    def write(self):
        pins=offline.WorkflowVerificationTest.write(self)
        for name, value in (("caller-timeout-transport.json",self.transport),("caller-timeout-drained.json",self.drained)):
            body=encoded(value); (self.directory/name).write_bytes(body); self.result["artifactSha256"][name]=sha(body)
        body=encoded(reseal(self.result)); (self.directory/"result.json").write_bytes(body); pins["result_sha"]=sha(body)
        return pins

    def verify(self):
        return verification.verify(self.root,self.directory,self.directory/"context.json",**self.write())

    def test_actual_git_replay_is_offline_and_preserves_completed_undelivered_denominator(self):
        with patch("http.client.HTTPConnection") as http, patch("os.kill") as signal:
            report=self.verify(); http.assert_not_called(); signal.assert_not_called()
        self.assertEqual(report["schemaVersion"],"agat.decision.public-workflow-verification.v4")
        self.assertEqual(report["physicalScheduledHttpHandlers"],6); self.assertEqual(report["completedUndeliveredResponses"],1)
        self.assertEqual(report["transportTimeoutReturns"],1); self.assertEqual(report["proxyPosts"],6)
        self.assertFalse(report["liveCleanupVerified"]); self.assertFalse(report["runtimeRestarted"])

    def test_resealed_drift_partial_proxy_counts_barrier_and_mixed_protocol_fail(self):
        mutations=(lambda:self.transport["rows"][2].update(responseBytesWritten=1),
                   lambda:self.drained.update(rowSha256="0"*64),
                   lambda:self.driver["callerTimeoutDrained"].update(stageId="other"),
                   lambda:self.result["samples"][-1]["counters"].update(ok=0),
                   lambda:self.result.update(schemaVersion=verification.RESULT_SCHEMA),
                   lambda:self.plan.update(runtimeRecovery={}),
                   lambda:self.plan["sourceFiles"].pop("scripts/lib/decision_public_workflow_timeout.py"))
        for mutate in mutations:
            self.setUp(); mutate(); self.transport=reseal(self.transport); self.drained=reseal(self.drained)
            with self.assertRaises(ValueError):self.verify()

    def test_pinned_raw_proxy_drift_rejected_and_no_output_published(self):
        pins=self.write(); (self.directory/"caller-timeout-transport.json").write_bytes(b"changed")
        with self.assertRaises(ValueError): verification.verify(self.root,self.directory,self.directory/"context.json",**pins)
        self.assertFalse((self.directory/"verification.json").exists())


class TimeoutProxySocketTest(unittest.TestCase):
    def test_real_caller_ten_second_deadline_completed_upstream_and_healthy_suffix_without_retry(self):
        context,_,cohort,routes,expected=fixture()
        requests=[]; results=[json.loads(row["responseBody"]) for row in expected["rows"]]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*_):pass
            def do_POST(self):
                body=self.rfile.read(int(self.headers["Content-Length"])); request=json.loads(body)
                index=len(requests); requests.append((body,self.headers["X-Agat-Decision-Cancel-On-Disconnect"]))
                result={**results[index],"id":request["id"],"durationMs":0.0}; response=encoded(result)
                self.send_response(200 if context["inputs"][index]["contextEligible"] else 422)
                self.send_header("Content-Type","application/json"); self.send_header("Content-Length",str(len(response)))
                self.end_headers();self.wfile.write(response)
        upstream=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        thread=threading.Thread(target=upstream.serve_forever,daemon=True);thread.start();proxy=None
        try:
            proxy=timeout.DeadlineProxy(upstream.server_address[1],context,timeout.timeout_spec(context,2))
            client=LocalDecisionClient(f"http://127.0.0.1:{proxy.port}")
            received=[]
            for case,route in zip(context["inputs"],routes):
                request={**case["request"],"id":route["stageId"],"options":[{**option,"abstain":option.get("abstain",False)} for option in case["request"]["options"]]}
                received.append(client.decide({"profile":PROFILE,"profileSha256":context["profileSha256"],
                    "request":request,"timeoutMs":10000,"callerTimingVersion":CALLER_TIMING_VERSION}))
                if len(received)==3:
                    stop=time.monotonic()+2
                    while proxy.target_receipt() is None and time.monotonic()<stop:time.sleep(.01)
                    self.assertIsNotNone(proxy.target_receipt())
            proxy.close(); transport=proxy.receipt()
            self.assertEqual(transport["acceptedPosts"],6);self.assertEqual(len(requests),6)
            self.assertEqual(transport["completedUpstreamPosts"],6);self.assertEqual(transport["withheldResponses"],1)
            self.assertEqual(transport["errors"],[]);self.assertTrue(all(header=="1" for _,header in requests))
            self.assertEqual(received[2]["status"],"unavailable");self.assertEqual(received[2]["reason"],"timeout")
            self.assertNotIn("result",received[2]);self.assertGreaterEqual(received[2]["callerTiming"]["durationMs"],9999)
            self.assertTrue(transport["rows"][2]["clientEofObserved"]);self.assertEqual(transport["rows"][2]["responseBytesWritten"],0)
            self.assertEqual(json.loads(transport["rows"][2]["responseBody"])["status"],"ok")
            self.assertTrue(all("result" in row for row in received[3:]))
        finally:
            if proxy is not None and not proxy.closed:proxy.close()
            upstream.shutdown();upstream.server_close();thread.join(2)

    def test_resident_port_or_bad_prospective_spec_never_binds(self):
        context,_,_,_,_=fixture();spec=timeout.timeout_spec(context,2)
        with patch.object(timeout,"ThreadingHTTPServer") as server:
            with self.assertRaises(ValueError):timeout.DeadlineProxy(8766,context,spec)
            spec["retryCount"]=1
            with self.assertRaises(ValueError):timeout.DeadlineProxy(12345,context,spec)
            server.assert_not_called()


if __name__=="__main__":unittest.main()
