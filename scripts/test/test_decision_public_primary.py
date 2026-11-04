import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from scripts.lib import decision_public_primary as companion
from scripts.lib import decision_public_load as load
from scripts.test import test_decision_arrival_primary as fixtures
from scripts.test.test_decision_public_load import context_fixture

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("public_primary_cli", ROOT/"scripts/run-public-support-load.py")
cli = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(cli)


def active_phase():
    response = fixtures.response()
    rows = [{"index":index,"scheduledMs":index*2000.,"dispatchMs":index*2000.+1.,"startedMs":index*2000.+2.,
             "finishedMs":index*2000.+502.,"status":"returned","wallMs":500.,"response":copy.deepcopy(response)} for index in range(2)]
    return {"schemaVersion":companion.SCHEMAS[2],"condition":"primary_active","phaseOriginMonotonicMs":100000.,
            "elapsedMs":4000.,"primaryRows":rows,"rows":[{"status":"ok","startedMs":0.,"finishedMs":1000.}]}


class PublicPrimaryTest(unittest.TestCase):
    def test_count_bound_precedes_thread_creation_and_old_primary_limit_is_unchanged(self):
        for count in (False,0,61,1.5):
            with self.subTest(count=count),patch.object(companion.threading,"Thread") as thread,self.assertRaises(ValueError):
                companion.PrimaryInventory(lambda *_:None,time.monotonic(),count,lambda:False)
            thread.assert_not_called()
        value=companion.PrimaryInventory(lambda *_:None,time.monotonic(),49,lambda:False)
        self.assertEqual(len(value.rows),49)
        with self.assertRaises(ValueError):companion.primary.PrimaryArrivals(lambda *_:None,time.monotonic(),98,lambda:False)

    def test_busy_capacity_drops_are_journalled_without_queue_or_retry(self):
        entered=threading.Event();release=threading.Event();saved=[];calls=[]
        def transport(*args):
            calls.append(args);entered.set();self.assertTrue(release.wait(2));return fixtures.response()
        def driver(rate,count,slots,lag,dispatch,**kwargs):
            self.assertEqual((rate,count,slots,lag),(.5,3,1,100));dispatch(0,0,0,None);self.assertTrue(entered.wait(2))
            dispatch(1,2,2,None);dispatch(2,4,4,None);release.set()
        value=companion.PrimaryInventory(transport,time.monotonic(),3,lambda:False,journal=saved.append,driver=driver)
        value.start();rows=value.finish()
        self.assertEqual(len(calls),1);self.assertEqual(len(saved),3)
        self.assertEqual([row["status"] for row in rows],["returned","dropped","dropped"])
        self.assertEqual([row["reason"] for row in rows[1:]],["client_capacity"]*2)

    def test_real_two_scheduler_clocks_share_origin_and_http_intervals_overlap(self):
        context=context_fixture();origin=time.monotonic()
        def transport(*_):
            time.sleep(.1);value=fixtures.response();value["total_duration"]=1_000_000;return value
        primary=companion.PrimaryInventory(transport,origin,2,lambda:False);primary.start()
        def measured(_client,request,_profile,_timeout):
            start=time.monotonic();time.sleep(.15)
            return {"status":"ok","reason":"accepted","observation":{"result":{"inputTokens":200}},
                    "callerMs":(time.monotonic()-start)*1000,"wallMs":(time.monotonic()-start)*1000}
        with patch.object(load,"measure_one",side_effect=measured):
            phase=load.run_inventory(None,context["inputs"][:2],context["profile"],origin=origin)
        phase.update(schemaVersion=companion.SCHEMAS[2],condition="primary_active",phaseOriginMonotonicMs=origin*1000,
                     primaryRows=primary.finish(),elapsedMs=(time.monotonic()-origin)*1000)
        summary=companion.verify_phase(phase,2)
        self.assertEqual(summary["returned"],2);self.assertEqual(summary["httpOverlapPairs"],2)
        self.assertEqual([row["scheduledMs"] for row in phase["rows"]],[0,2000])

    def test_missing_overlap_corrupt_budget_and_denominator_fail(self):
        phase=active_phase();self.assertEqual(companion.verify_phase(phase,2)["httpOverlapPairs"],1)
        mutations=(lambda p:p["primaryRows"].pop(),lambda p:p["primaryRows"][0].update(index=True),
                   lambda p:p["primaryRows"][0].update(scheduledMs=100),lambda p:p["primaryRows"][1].update(startedMs=400),
                   lambda p:p["primaryRows"][0].update(dispatchMs=101),lambda p:p["primaryRows"][0]["response"].update(eval_count=129),
                   lambda p:p["rows"][0].update(startedMs=3000,finishedMs=3500),lambda p:p.update(condition="primary_idle"))
        for mutation in mutations:
            value=copy.deepcopy(phase);mutation(value)
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):companion.verify_phase(value,2)

    def test_failed_primary_call_remains_failure_without_fabricated_response(self):
        def transport(*_):raise OSError("synthetic")
        def driver(_rate,_count,_slots,_lag,dispatch,**_):dispatch(0,0,0,None)
        primary=companion.PrimaryInventory(transport,time.monotonic(),1,lambda:False,driver=driver);primary.start()
        rows=primary.finish();self.assertEqual(rows[0]["status"],"measurement_error");self.assertNotIn("response",rows[0])
        phase=active_phase();phase["primaryRows"]=rows
        with self.assertRaises(ValueError):companion.verify_phase(phase,1)

    def test_cancelled_future_slots_keep_full_primary_inventory(self):
        def driver(_rate,count,_slots,_lag,dispatch,**_):
            for index in range(count):dispatch(index,index*2,0,"cancelled")
        with patch.object(companion.primary,"measure_primary") as model:
            value=companion.PrimaryInventory(lambda *_:None,time.monotonic(),49,lambda:True,driver=driver)
            value.start();rows=value.finish();model.assert_not_called()
        self.assertEqual(len(rows),49);self.assertTrue(all(row["reason"]=="cancelled" for row in rows))

    def test_primary_pin_settings_and_artifact_census_are_strict(self):
        from decision_runtime.contracts import parse_json,fingerprint
        pin_path=companion.primary.PRIMARY_SOURCES[2];raw=(ROOT/pin_path).read_bytes()
        value={"model":companion.primary.MODEL,"manifestSha256":companion.primary.DIGEST,"blobCount":3,"blobBytes":123,
            "release":parse_json(raw),"releaseFileSha256":hashlib.sha256(raw).hexdigest(),"request":companion.primary.REQUEST,
            "requestSha256":fingerprint(companion.primary.REQUEST),"settings":companion.primary.SETTINGS,
            "ratePerSecond":.5,"clientSlots":1,"timeoutSeconds":30}
        companion.verify_plan(value,{pin_path:raw})
        for mutation in (lambda p:p.update(clientSlots=True),lambda p:p.update(blobCount=True),lambda p:p.update(requestSha256="0"*64),
                         lambda p:p["request"]["options"].update(num_predict=129),lambda p:p.update(manifestSha256="0"*64)):
            config=copy.deepcopy(value);mutation(config)
            with self.assertRaises(ValueError):companion.verify_plan(config,{pin_path:raw})

    def test_unpaired_primary_paths_fail_before_model_or_source_output(self):
        args=["--context-profile","missing","--context-profile-file-sha256","0"*64,"--runtime-python","missing",
              "--manifest","missing","--evidence-dir","docs/private/never-created-primary-fixture","--primary-binaries","missing"]
        with patch.object(cli.launcher,"frozen_sources") as sources,patch.object(cli.subprocess,"Popen") as process:
            self.assertEqual(cli.main(args),1);sources.assert_not_called();process.assert_not_called()

    def test_full_v2_offline_chain_reads_actual_git_and_rejects_rehashed_primary_corruption(self):
        from scripts.test import test_decision_public_load_verification as vf
        from scripts.lib import decision_public_load_verification as verification
        context,plan,result=vf.fixture()
        names=set(plan["sourceFiles"])|set(companion.SOURCE_PATHS)
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            for name in names:
                destination=root/name;destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes((ROOT/name).read_bytes())
            subprocess.run(["git","init","--quiet"],cwd=root,check=True)
            subprocess.run(["git","add","."],cwd=root,check=True)
            subprocess.run(["git","-c","user.name=Synthetic fixture","-c","user.email=fixture@example.invalid","commit","--quiet","-m","Synthetic source fixture"],cwd=root,check=True)
            commit=subprocess.check_output(["git","rev-parse","HEAD"],cwd=root,text=True).strip()
            context.update(sourceCommit=commit,sourceFiles={name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in context["sourceFiles"]})
            config={"model":companion.primary.MODEL,"manifestSha256":companion.primary.DIGEST,"blobCount":3,"blobBytes":123,
                "release":json.loads((ROOT/companion.primary.PRIMARY_SOURCES[2]).read_bytes()),
                "releaseFileSha256":hashlib.sha256((ROOT/companion.primary.PRIMARY_SOURCES[2]).read_bytes()).hexdigest(),
                "request":companion.primary.REQUEST,"requestSha256":vf.fingerprint(companion.primary.REQUEST),"settings":companion.primary.SETTINGS,
                "ratePerSecond":.5,"clientSlots":1,"timeoutSeconds":30}
            plan.update(schemaVersion=companion.SCHEMAS[0],sourceCommit=commit,sourceFiles={name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in names},
                        primaryCompanionStarted=True,condition="primary_active",primary=config)
            primary_rows=[{"index":i,"scheduledMs":i*2000.,"dispatchMs":i*2000.+1.,"startedMs":i*2000.+2.,"finishedMs":i*2000.+502.,
                           "wallMs":500.,"status":"returned","response":fixtures.response()} for i in range(len(plan["inputs"]))]
            result["phase"].update(schemaVersion=companion.SCHEMAS[2],condition="primary_active",phaseOriginMonotonicMs=100000.,
                                   primaryRows=primary_rows,elapsedMs=primary_rows[-1]["finishedMs"]+1)
            result.update(schemaVersion=companion.SCHEMAS[1],primaryRows=primary_rows,
                          primaryWarmup={"status":"returned","wallMs":500.,"response":fixtures.response()},elapsedMs=result["phase"]["elapsedMs"]+100)
            for sample in result["samples"]:sample["primaryResidence"]={"models":[{"digest":companion.primary.DIGEST,"context_length":8192}]}
            result["samples"][-1]["elapsedMs"]=result["phase"]["elapsedMs"]+80
            directory=root/"docs/private/fixture";directory.mkdir(parents=True)
            def write(value):
                pins=vf.write_fixture(directory,context,plan,value)
                stored=json.loads((directory/"result.json").read_bytes())
                logs={"primary.log":b"Synthetic primary log.\n","primary-requests.jsonl":b"".join(json.dumps(row).encode()+b"\n" for row in value["primaryRows"])}
                for name,raw in logs.items():
                    (directory/name).write_bytes(raw);stored["logSha256"][name]=hashlib.sha256(raw).hexdigest()
                raw=vf.encoded(vf.reseal(stored));(directory/"result.json").write_bytes(raw);pins["result_sha"]=hashlib.sha256(raw).hexdigest()
                return pins
            pins=write(result)
            observed=verification.verify(root,directory,directory/"context.json",**pins)
            self.assertEqual(observed["primarySummary"]["scheduled"],len(plan["inputs"]))
            self.assertEqual(observed["primarySummary"]["httpOverlapPairs"],len(plan["inputs"]))
            for mutation in (lambda r:r["primaryRows"][0]["response"].update(eval_count=129),
                             lambda r:r["samples"][0]["primaryResidence"]["models"][0].update(context_length=True),
                             lambda r:r["primaryWarmup"]["response"].update(done=False)):
                changed=copy.deepcopy(result);mutation(changed);pins=write(changed)
                with self.assertRaises(ValueError):verification.verify(root,directory,directory/"context.json",**pins)


if __name__=="__main__":unittest.main()
