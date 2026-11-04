import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from decision_runtime.annotations import group_split
from decision_runtime.artifacts import sealed
from decision_runtime.contracts import Request, fingerprint
from scripts.lib import decision_public_load as load
from scripts.lib.decision_public_context import context_result
from scripts.test import test_decision_public_context as fixtures

ROOT = Path(__file__).resolve().parents[2]
SPEC=importlib.util.spec_from_file_location("public_load_cli",ROOT/"scripts/run-public-support-load.py")
cli=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(cli)


def context_fixture():
    pool=fixtures.fixture_pool()[3]["pool.json"];profile,manifest=fixtures.profile_fixture()
    selected=fixtures.context.development_cases(pool);tokens=fixtures.token_fixture(pool)
    tokens[-1].update(partTokens=[1949,100],inputTokens=2049)
    value=context_result(pool,selected,tokens,profile)
    return sealed({"schemaVersion":fixtures.context.PROFILE_SCHEMA,"status":"observed","createdAt":"2026-10-08T01:00:00.000Z",
        "sourceCommit":"a"*40,"sourceFiles":{"fixture.py":"b"*64},"importFileSha256":"c"*64,"importSha256":"d"*64,
        "acquisitionFileSha256":"e"*64,"acquisitionSha256":"f"*64,"profileFileSha256":"1"*64,"profileSha256":fingerprint(profile),
        "profile":profile,"manifestFileSha256":"2"*64,"model":{k:manifest[k] for k in ("repository","revision","artifactSha256")},
        "tokenizerEnvironment":{"python":"3.13.12","machine":"arm64","packages":{"fixture":"1"}},**value})


def instant_driver(_rate,count,_slots,_lag,dispatch,**kwargs):
    for index in range(count):dispatch(index,index*2,index*2,None)


class PublicLoadTest(unittest.TestCase):
    def test_context_binding_counts_splits_and_unverified_flags_fail_closed(self):
        original=context_fixture();load.validate_context(original)
        mutations=(lambda v:v.update(sloAccepted=True),lambda v:v.update(modelCalls=False),
                   lambda v:v.update(selectedSplit="holdout"),lambda v:v["inputs"][0].update(inputSha256="0"*64),
                   lambda v:v["inputs"][0].update(contextEligible=1),lambda v:v["inputs"].pop(),
                   lambda v:v["inputs"][0].update(inputTokens=True),lambda v:v.update(developmentGroups=0),
                   lambda v:v["proposedDiagnosticSchedule"].update(retries=1),lambda v:v.update(predictions=1),
                   lambda v:v["proposedDiagnosticSchedule"].update(clientSlots=True),
                   lambda v:v["proposedDiagnosticSchedule"].update(retries=False),
                   lambda v:v["poolCasesBySplit"].update(development=1),
                   lambda v:v["model"].update(revision="0"*40))
        for mutation in mutations:
            value=copy.deepcopy(original);mutation(value);value=sealed({k:x for k,x in value.items() if k!="sha256"})
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):load.validate_context(value)

    def test_variable_inputs_shorter_than_synthetic_minimum_and_long_rejection_stay_in_denominator(self):
        context=context_fixture();inputs=context["inputs"][:1]+context["inputs"][-1:]
        saved=[]
        def measured(_client,request,_profile,_timeout):
            case=next(x for x in inputs if x["id"]==request.id)
            if not case["contextEligible"]:
                return {"status":"error","reason":"context_too_long","observation":{"result":{}},"callerMs":5,"wallMs":6}
            return {"status":"abstain","reason":"low_confidence","observation":{"result":{"inputTokens":200}},"callerMs":30,"wallMs":31}
        def driven(rate,count,slots,lag,dispatch,**kwargs):
            self.assertEqual((rate,count,slots,lag),(.5,2,1,100))
            for index in range(count):
                dispatch(index,index*2,index*2,None)
                # Wait for the explicit journal receipt, without adding fake HTTP sleep.
                for _spin in range(10000):
                    if len(saved)>index:break
                    threading.Event().wait(.0001)
                self.assertGreater(len(saved),index)
        with patch.object(load,"measure_one",side_effect=measured):
            phase=load.run_inventory(None,inputs,context["profile"],journal=saved.append,driver=driven)
        self.assertEqual([r["caseId"] for r in phase["rows"]],[r["id"] for r in inputs])
        self.assertEqual(phase["summary"]["scheduled"],2);self.assertEqual(phase["summary"]["scored"],1)
        self.assertEqual(phase["summary"]["failures"],{"context_too_long":1})
        self.assertEqual(phase["summary"]["goodPerScheduled"],.5)

    def test_busy_client_drops_arrivals_without_waiting_or_retrying(self):
        context=context_fixture();inputs=context["inputs"][:3]
        entered=threading.Event();release=threading.Event();calls=[]
        def measured(_client,request,_profile,_timeout):
            calls.append(request.id);entered.set();self.assertTrue(release.wait(2))
            return {"status":"ok","reason":None,"observation":{"result":{"inputTokens":200}},"callerMs":100,"wallMs":100}
        def driven(_rate,count,_slots,_lag,dispatch,**kwargs):
            dispatch(0,0,0,None);self.assertTrue(entered.wait(2))
            for index in range(1,count):dispatch(index,index*2,index*2,None)
            release.set()
        with patch.object(load,"measure_one",side_effect=measured):
            phase=load.run_inventory(None,inputs,context["profile"],driver=driven)
        self.assertEqual(len(calls),1);self.assertEqual(phase["summary"]["scheduled"],3)
        self.assertEqual(phase["summary"]["dropped"],{"client_capacity":2})
        self.assertEqual([row["index"] for row in phase["rows"]],[0,1,2])

    def test_cancelled_and_late_rows_never_call_model_and_keep_ids(self):
        context=context_fixture();inputs=context["inputs"][:2]
        def driven(_rate,count,_slots,_lag,dispatch,**kwargs):
            dispatch(0,0,.2,"scheduler_lag");dispatch(1,2,2,"cancelled")
        with patch.object(load,"measure_one") as measured:
            phase=load.run_inventory(None,inputs,context["profile"],driver=driven)
            measured.assert_not_called()
        self.assertEqual(phase["summary"]["goodPerScheduled"],0)
        self.assertEqual(phase["summary"]["dropped"],{"scheduler_lag":1,"cancelled":1})

    def test_wrong_token_count_or_missing_long_rejection_is_measurement_error(self):
        context=context_fixture()
        for case,result in ((context["inputs"][0],{"status":"ok","reason":None,"observation":{"result":{"inputTokens":201}}}),
                            (context["inputs"][-1],{"status":"unavailable","reason":"timeout","observation":{}})):
            with patch.object(load,"measure_one",return_value={**result,"callerMs":100,"wallMs":100}):
                phase=load.run_inventory(None,[case],context["profile"],driver=instant_driver)
            self.assertEqual(phase["rows"][0]["status"],"measurement_error")
            self.assertEqual(phase["summary"]["scored"],0)

    def test_historical_source_pins_cannot_escape_or_use_wrong_commit_bytes(self):
        value=context_fixture()
        with patch.object(load.subprocess,"check_output",return_value=b"different"),self.assertRaises(ValueError):
            load.historical_context_sources(ROOT,value)
        value["sourceFiles"]={"../foreign.py":"b"*64}
        with patch.object(load.subprocess,"check_output") as git,self.assertRaises(ValueError):
            load.historical_context_sources(ROOT,value)
        git.assert_not_called()

    def test_actual_cli_rejects_bad_context_before_owned_process_or_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);private=root/"docs/private";private.mkdir(parents=True)
            source=private/"context.json";source.write_text(json.dumps(context_fixture()))
            output=private/"output"
            args=["--context-profile",str(source),"--context-profile-file-sha256","0"*64,
                  "--runtime-python","/fixture-python","--manifest","/fixture-manifest","--evidence-dir",str(output)]
            with patch.object(cli,"ROOT",root),patch.object(cli.subprocess,"Popen") as process:
                self.assertEqual(cli.main(args),1);process.assert_not_called();self.assertFalse(output.exists())


if __name__=="__main__":unittest.main()
