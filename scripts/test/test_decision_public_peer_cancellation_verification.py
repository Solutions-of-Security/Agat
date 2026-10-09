"""Synthetic offline cancellation ledger; never native/model performance evidence."""
from collections import Counter
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from decision_runtime.artifacts import sealed
from decision_runtime.metrics import OUTCOMES, outcome
from scripts.lib import decision_public_peer_cancellation_verification as verifier
from scripts.lib import decision_public_workflow_active_cancellation as active
from scripts.test import test_decision_public_workflow_timeout as fixtures

ROOT = Path(__file__).resolve().parents[2]
def encoded(value): return (json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+"\n").encode()
def sha(raw): return hashlib.sha256(raw).hexdigest()
def reseal(value): return sealed({key:item for key,item in value.items() if key != "sha256"})


def fixture():
    context,_,_,_,old = fixtures.fixture(); spec = active.active_spec(context,2)
    origin = datetime(2026,10,9,3,tzinfo=timezone.utc)
    stamp = lambda ms:(origin+timedelta(milliseconds=ms)).isoformat(timespec="milliseconds").replace("+00:00","Z")
    timing = lambda ms:{"schemaVersion":"agat.decision.caller-timing.v1","clock":"monotonic","boundary":"local_http_call","durationMs":ms}
    def measured(typed,ms=10.0):
        return {"status":typed["status"],"reason":typed["reason"],"observation":{"result":typed,"callerTiming":timing(ms)},"callerMs":ms,"wallMs":ms+.1}
    rows = []; wire = []
    for index,(case,old_row) in enumerate(zip(context["inputs"],old["rows"])):
        accepted = 100+index*200; request = encoded(case["request"])
        item = {"index":index,"caseId":case["id"],"stageId":case["request"]["id"],"inputSha256":case["inputSha256"],
            "profileSha256":context["profileSha256"],"requestBody":request.decode(),"requestBodySha256":sha(request),
            "cancelOnDisconnect":True,"acceptedAt":stamp(accepted),"finishedAt":stamp(accepted+6),"elapsedMs":6.0}
        if index == 2:
            observed={"status":"unavailable","reason":"cancelled","observation":{"status":"unavailable","reason":"cancelled",
                "callerTiming":timing(50.0)},"callerMs":50.0,"wallMs":50.1}
            item.update(upstreamRequestSentAt=stamp(accepted+1),clientEofObservedAt=stamp(accepted+49),upstreamShutdownAt=stamp(accepted+50),
                finishedAt=stamp(accepted+50),elapsedMs=50.0,clientEofObserved=True,upstreamShutdownApplied=True,
                upstreamResponseBytesObserved=0,responseBytesWritten=0,downstreamWriteCompleted=False,upstreamCompletedNormally=False)
        else:
            typed=json.loads(old_row["responseBody"]); typed["id"]=case["request"]["id"]; response=encoded(typed); observed=measured(typed)
            item.update(upstreamCompletedAt=stamp(accepted+6),upstreamMs=6.0,upstreamStatus=200 if case["contextEligible"] else 422,
                upstreamContentType="application/json",responseBody=response.decode(),responseBodySha256=sha(response),withheld=False,
                downstreamWriteCompleted=True,responseBytesWritten=len(response),clientEofObserved=False)
        wire.append(item); rows.append({"index":index,"caseId":case["id"],"inputSha256":case["inputSha256"],**observed})
    def metrics(counts,epoch,in_progress=0):
        return "\n".join(f'agat_decision_requests_total{{outcome="{key}"}} {counts[key]}' for key in sorted(OUTCOMES))+f"\nagat_decision_backend_ready 1\nagat_decision_requests_in_progress {in_progress}\nagat_decision_server_start_time_seconds {epoch}\n"
    health={"status":"ready","mode":"shadow","profileJson":json.dumps(context["profile"],ensure_ascii=False,sort_keys=True,separators=(",",":")),"profileSha256":context["profileSha256"]}
    counts=[Counter(),Counter(ok=2),Counter(ok=4),Counter(),Counter(ok=2),Counter(ok=4,context_rejected=1)]
    samples=[]
    for index,(label,ms) in enumerate(zip(("ready_before_scoring","after_warmup","before_target","ready_before_scoring","after_warmup","after_inventory"),(10,80,499,600,620,1200))):
        epoch=1000.125 if index<3 else 1001.125; pid=101 if index<3 else 201
        samples.append({"label":label,"epoch":index//3,"capturedAt":stamp(ms),"runtimePid":pid,"metricsRaw":metrics(counts[index],epoch),
            "counters":{key:counts[index][key] for key in OUTCOMES},"serverStart":epoch,"health":copy.deepcopy(health),"ownedPids":[pid,pid+1,pid+2],"elapsedMs":float(ms)})
    typed=json.loads(old["rows"][0]["responseBody"]); typed["id"]=context["inputs"][0]["request"]["id"]
    warmup=[{"epoch":index//2,"iteration":index%2,"caseId":context["inputs"][0]["id"],**measured(copy.deepcopy(typed))} for index in range(4)]
    ready=active.ready_receipt(wire[2],{"capturedAt":stamp(502),"metricsRaw":metrics(counts[2],1000.125,1)}); drained=active.drain_receipt(wire[2])
    event={"schemaVersion":"agat.decision.retirement.v1","eventName":"decision.backend_retired","runtimeVersion":"0.12.3",
        "profileSha256":context["profileSha256"],"exitCode":75,"reason":"inference_cancelled","childPid":102,"childExitCode":-15}
    log=b"Synthetic fixture; never native/model evidence.\n"+encoded(event).replace(b"\n",b"")+b"\n"
    retired=active.retired_receipt(spec,ready,drained,runtime_pid=101,native_pids=[101,102,103],exit_code=75,remaining=[],log_raw=log,observed_at=stamp(560))
    recovery_raw=encoded(warmup[2:])
    recovered=active.recovered_receipt(spec,retired,runtime_pid=201,profile_sha=context["profileSha256"],server_start=1001.125,
        warmup_file_sha=sha(recovery_raw),applied_at=stamp(630))
    transport=sealed({"schemaVersion":active.TRANSPORT_SCHEMA,"spec":spec,"proxyPort":23456,"upstreamPort":23457,"startedAt":stamp(90),
        "closedAt":stamp(1210),"rows":wire,"acceptedPosts":6,"completedUpstreamPosts":5,"interruptedActiveUpstreamPosts":1,"errors":[],"closed":True,"activeHandlers":0})
    artifacts={"active-ready.json":encoded(ready),"active-drained.json":encoded(drained),"native-retired.json":encoded(retired),
        "native-recovered.json":encoded(recovered),"runtime.log":log,"runtime-recovered.log":b"Synthetic recovered fixture.\n",
        "recovery-warmup.json":recovery_raw,"relay-transport.json":encoded(transport),"caller-cancellation-applied.json":encoded({
            "schemaVersion":"agat.decision.public-peer-cancellation-applied.v1","targetIndex":2,"caseId":spec["targetCaseId"],
            "inputSha256":spec["targetInputSha256"],"requestedAt":stamp(503),"readyFileSha256":sha(encoded(ready)),"method":"local_caller_event","coordinatorExercised":False})}
    authority={"primary":"not_exercised","coordinatorExercised":False,"durableAccountingExercised":False,"ownersAppointed":False,
        "referenceLabels":0,"sloAccepted":False,"routingEnabled":False,"qualification":"not_assessed"}
    plan=sealed({"schemaVersion":"agat.decision.public-peer-cancellation-plan.v1","createdAt":stamp(0),"sourceCommit":"a"*40,"sourceFiles":{},
        "contextProfileFileSha256":sha(encoded(context)),"context":context,"spec":spec,"runtime":context["tokenizerEnvironment"],
        "profileFileSha256":context["profileFileSha256"],"manifestFileSha256":context["manifestFileSha256"],"mode":"serial_direct_native_peer_cancellation",
        "warmupCount":4,**authority})
    result=sealed({"schemaVersion":"agat.decision.public-peer-cancellation-result.v1","status":"observed","planSha256":plan["sha256"],"rows":rows,
        "warmup":warmup,"samples":samples,"retired":retired,"recovered":recovered,"failure":None,"ownedPids":[101,102,103,201,202,203],
        "remainingOwnedPids":[],"cleanupErrors":[],"runtimeExitCodes":[75,130],"artifactSha256":{name:sha(raw) for name,raw in artifacts.items()},
        "elapsedMs":1300.0,"classificationAccuracyMeasured":False,**authority})
    return context,plan,result,artifacts


class PeerInventoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.original=fixture()
    def test_complete_fixture_separates_completed_and_interrupted_without_workflow_claims(self):
        context,plan,result,artifacts=copy.deepcopy(self.original)
        with patch("http.client.HTTPConnection") as network:
            report=verifier.inventory(context,plan,result,artifacts); network.assert_not_called()
        self.assertEqual(report["scheduled"],6); self.assertEqual(report["computed"],4)
        self.assertEqual(report["knownCompletedPhysicalCalls"],9); self.assertEqual(report["healthySuffixCases"],3)
        self.assertEqual(report["runtimeEpochs"],2); self.assertTrue(report["targetTerminalCounterUnknown"])
        self.assertIsNone(report["targetTypedResult"]); self.assertFalse(report["coordinatorExercised"])
        self.assertFalse(report["durableAccountingExercised"]); self.assertEqual(report["primary"],"not_exercised")
    def test_rehashed_omissions_foreign_pids_epoch_counters_and_invented_results_fail(self):
        changes=(lambda r:r["rows"].pop(),lambda r:r["rows"].reverse(),lambda r:r["samples"][5]["counters"].update(ok=3),
            lambda r:r["samples"][3].update(serverStart=1000.125),lambda r:r["samples"][0]["ownedPids"].append(999),
            lambda r:r["samples"][1].update(epoch=True),lambda r:r["rows"][2]["observation"].update(result={}),
            lambda r:r["rows"][2].update(reason="timeout"),lambda r:r["warmup"].pop(),lambda r:r.update(remainingOwnedPids=[102]),
            lambda r:r.update(runtimeExitCodes=[75,0]),lambda r:r.update(primary="fixture_chat_completions"),lambda r:r.update(coordinatorExercised=True),
            lambda r:r.update(durableAccountingExercised=True),lambda r:r.update(referenceLabels=False),lambda r:r.update(routingEnabled=True))
        for mutate in changes:
            context,plan,result,artifacts=copy.deepcopy(self.original); mutate(result)
            with self.subTest(mutation=mutate),self.assertRaises(ValueError): verifier.inventory(context,plan,reseal(result),artifacts)
    def test_resealed_transport_body_order_target_delivery_and_resident_port_fail(self):
        changes=(lambda t:t["rows"].pop(),lambda t:t["rows"].reverse(),lambda t:t.update(acceptedPosts=7),lambda t:t.update(activeHandlers=False),
            lambda t:t.update(upstreamPort=8766),lambda t:t["rows"][2].update(responseBody="{}"),lambda t:t["rows"][2].update(upstreamResponseBytesObserved=1),
            lambda t:t["rows"][2].update(upstreamCompletedNormally=True),lambda t:t["rows"][0].update(responseBodySha256="0"*64),
            lambda t:t["rows"][0].update(requestBodySha256="0"*64),lambda t:t["rows"][0].update(index=False),lambda t:t["rows"][3].update(acceptedAt=t["rows"][2]["acceptedAt"]))
        for mutate in changes:
            context,plan,result,artifacts=copy.deepcopy(self.original); value=json.loads(artifacts["relay-transport.json"]); mutate(value)
            artifacts["relay-transport.json"]=encoded(reseal(value)); result["artifactSha256"]["relay-transport.json"]=sha(artifacts["relay-transport.json"])
            with self.subTest(mutation=mutate),self.assertRaises(ValueError): verifier.inventory(context,plan,reseal(result),artifacts)
    def test_rehashed_event_recovery_and_applier_cannot_be_retargeted(self):
        for name,key,value in (("caller-cancellation-applied.json","method","coordinator_cancel"),("caller-cancellation-applied.json","requestedAt","2026-10-09T03:00:00.100Z"),
            ("caller-cancellation-applied.json","readyFileSha256","0"*64),("native-retired.json","runtimePid",999),
            ("native-retired.json","targetTypedResult",{}),("native-recovered.json","runtimePid",101),("native-recovered.json","warmupFileSha256","0"*64)):
            context,plan,result,artifacts=copy.deepcopy(self.original); item=json.loads(artifacts[name]); item[key]=value
            artifacts[name]=encoded(reseal(item) if "sha256" in item else item); result["artifactSha256"][name]=sha(artifacts[name])
            with self.subTest(name=name,key=key),self.assertRaises(ValueError): verifier.inventory(context,plan,reseal(result),artifacts)
    def test_plan_seal_and_artifact_raw_pins_are_required_before_inventory(self):
        context,plan,result,artifacts=copy.deepcopy(self.original)
        for key in ("plan","result","artifact"):
            p=copy.deepcopy(plan); r=copy.deepcopy(result); a=copy.deepcopy(artifacts)
            if key=="plan": p["spec"]["targetIndex"]=3
            elif key=="result": r["status"]="failed"
            else: a["runtime.log"]+=b"extra"
            with self.subTest(key=key),self.assertRaises(ValueError): verifier.inventory(context,p,r,a)


class PeerOfflineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original=fixture(); cls.temporary=tempfile.TemporaryDirectory(); cls.root=Path(cls.temporary.name)/"sources"; cls.root.mkdir()
        paths=sorted(set(verifier.PATHS+verifier.CONTEXT_PATHS))
        names=subprocess.check_output(["git","ls-files","--",*paths],cwd=ROOT,text=True).splitlines()
        for name in names:
            path=cls.root/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes((ROOT/name).read_bytes())
        for args in (["init","--quiet"],["add","."],["-c","user.name=Synthetic fixture","-c","user.email=fixture@example.invalid","commit","--quiet","-m","Synthetic peer proof sources"]):
            subprocess.run(["git",*args],cwd=cls.root,check=True)
        cls.commit=subprocess.check_output(["git","rev-parse","HEAD"],cwd=cls.root,text=True).strip()
    @classmethod
    def tearDownClass(cls): cls.temporary.cleanup()
    def pins(self,paths):
        names=subprocess.check_output(["git","ls-tree","-r","--name-only",self.commit,"--",*paths],cwd=self.root,text=True).splitlines()
        return {name:sha((self.root/name).read_bytes()) for name in names}
    def write(self,directory):
        context,plan,result,artifacts=copy.deepcopy(self.original)
        context.update(sourceCommit=self.commit,sourceFiles=self.pins(verifier.CONTEXT_PATHS)); context=reseal(context)
        plan.update(sourceCommit=self.commit,sourceFiles=self.pins(verifier.PATHS),context=context,contextProfileFileSha256=sha(encoded(context))); plan=reseal(plan)
        result["planSha256"]=plan["sha256"]; result=reseal(result)
        for name,raw in {"context.json":encoded(context),"plan.json":encoded(plan),"result.json":encoded(result),**artifacts}.items(): (directory/name).write_bytes(raw)
        return {"context_sha":sha(encoded(context)),"plan_sha":sha(encoded(plan)),"result_sha":sha(encoded(result))}
    def test_historical_sources_raw_pins_and_implementation_replay_without_network(self):
        with tempfile.TemporaryDirectory() as temporary,patch("http.client.HTTPConnection") as network:
            directory=Path(temporary); pins=self.write(directory); result=verifier.verify(self.root,directory,directory/"context.json",**pins); network.assert_not_called()
            self.assertEqual(result["status"],"pass"); self.assertEqual(result["verificationModelCalls"],0)
            self.assertFalse(result["liveCleanupVerified"]); self.assertFalse(result["gpuKernelPreemptionEstablished"])
    def test_wrong_raw_pin_and_changed_historical_source_fail(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary); pins=self.write(directory)
            for key in pins:
                with self.subTest(key=key),patch.object(verifier,"sources_at") as sources,self.assertRaises(ValueError):
                    verifier.verify(self.root,directory,directory/"context.json",**{**pins,key:"0"*64})
                sources.assert_not_called()
            plan=json.loads((directory/"plan.json").read_bytes()); plan["sourceFiles"].pop(next(iter(plan["sourceFiles"])))
            plan=reseal(plan); (directory/"plan.json").write_bytes(encoded(plan)); result=json.loads((directory/"result.json").read_bytes()); result["planSha256"]=plan["sha256"]
            result=reseal(result); (directory/"result.json").write_bytes(encoded(result))
            with self.assertRaises(ValueError): verifier.verify(self.root,directory,directory/"context.json",**{**pins,"plan_sha":sha(encoded(plan)),"result_sha":sha(encoded(result))})
    def test_launcher_and_verifier_reject_existing_output_before_sources_model_or_network(self):
        for filename,extra,output_name in (("run-public-support-peer-cancellation.py",["--runtime-python","missing","--manifest","missing","--cancel-at-index","2"],"--evidence-dir"),
            ("verify-public-support-peer-cancellation.py",["--evidence-dir","missing","--plan-file-sha256","0"*64,"--result-file-sha256","0"*64],"--output-dir")):
            spec=importlib.util.spec_from_file_location("peer_admission_cli",ROOT/"scripts"/filename); cli=importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)
            with tempfile.TemporaryDirectory() as temporary,patch.object(cli.launcher,"frozen_sources") as sources,patch("subprocess.Popen") as child:
                args=["--context-profile","missing","--context-profile-file-sha256","0"*64,output_name,temporary,*extra]
                self.assertEqual(cli.main(args),1); sources.assert_not_called(); child.assert_not_called()


if __name__ == "__main__": unittest.main()
