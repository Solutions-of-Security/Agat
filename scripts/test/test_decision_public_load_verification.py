import copy
import hashlib
import http.client
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from decision_runtime.artifacts import sealed
from decision_runtime.contracts import Policy, Request, fingerprint
from decision_runtime.engine import DecisionEngine, Scores
from decision_runtime.metrics import DecisionMetrics, outcome
from scripts.lib.decision_arrival_rate import summarize
from scripts.lib import decision_public_load_verification as verifier
from scripts.test.test_decision_public_load import context_fixture

ROOT = Path(__file__).resolve().parents[2]
MEASURED = "a642578df03812d32d299614204ad467ebe8490e"
SPEC = importlib.util.spec_from_file_location("public_load_verification_cli", ROOT/"scripts/verify-public-support-load.py")
cli = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(cli)


def encoded(value): return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
def digest(raw): return hashlib.sha256(raw).hexdigest()
def reseal(value): return sealed({key:item for key,item in value.items() if key != "sha256"})
def source_pins(paths):
    names = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", MEASURED, "--", *paths], cwd=ROOT, text=True).splitlines()
    return {name:digest(subprocess.check_output(["git", "show", MEASURED+":"+name], cwd=ROOT)) for name in names}


def fixture():
    # Every question/label/log in this fixture is synthetic; no native inference.
    context = context_fixture()
    raw_profile = (ROOT/verifier.PROFILE_PATH).read_bytes(); profile = json.loads(raw_profile)
    requirements = dict(line.split("==") for line in (ROOT/"decision_runtime/requirements-mlx.txt").read_text().splitlines() if line and not line.startswith("#"))
    context.update(profile=profile, profileSha256=fingerprint(profile), profileFileSha256=digest(raw_profile),
                   model={key:profile["model"][key] for key in ("repository", "revision", "artifactSha256")},
                   tokenizerEnvironment={"python":"3.13.12", "machine":"arm64", "packages":requirements},
                   sourceCommit=MEASURED, sourceFiles=source_pins(verifier.CONTEXT_PATHS))
    context = reseal(context)
    plan = {"schemaVersion":verifier.PLAN_SCHEMA, "createdAt":"2026-10-08T01:01:00.000Z",
        "sourceCommit":MEASURED,"sourceFiles":source_pins(verifier.LOAD_PATHS),
        "contextProfileFileSha256":digest(encoded(context)),"contextProfileSealSha256":context["sha256"],
        **{key:context[key] for key in ("profile", "profileFileSha256", "profileSha256", "manifestFileSha256", "model", "inputs")},
        "runtime":context["tokenizerEnvironment"],"schedule":context["proposedDiagnosticSchedule"],"warmupCount":2,
        "scope":"whole_unlabelled_public_development_http_inventory","overLimitBehavior":"send_full_input_expect_context_too_long",
        "primaryCompanionStarted":False,"backgroundWorkloadControlled":False,"referenceLabels":0,"calibrationRequests":0,
        "holdoutRequests":0,"sloAccepted":False,"representativeAgatTraffic":False,"routingEnabled":False,"qualification":"not_assessed"}
    class FixtureBackend:
        identity = profile["model"]
        def score(self, request):
            case = next(case for case in plan["inputs"] if case["id"] == request.id)
            return Scores([8.] + [0.]*(len(request.options)-1), case["inputTokens"])
    engine = DecisionEngine(FixtureBackend(), Policy.from_dict({key:value for key,value in profile["policy"].items() if key != "sha256"}))
    def measured(case):
        body = engine.decide(case["request"]) if case["contextEligible"] else engine.error("context_too_long", Request.from_dict(case["request"]))
        body["durationMs"] = 5.; caller = 30. if case["contextEligible"] else 5.
        return {"status":body["status"],"reason":body["reason"],"observation":{"result":body,
            "callerTiming":{"schemaVersion":"agat.decision.caller-timing.v1","clock":"monotonic","boundary":"local_http_call","durationMs":caller}},
            "callerMs":caller,"wallMs":caller+1}
    rows = []
    for index, case in enumerate(plan["inputs"]):
        value = measured(case)
        rows.append({"index":index,"caseId":case["id"],"inputSha256":case["inputSha256"],"inputTokens":case["inputTokens"],
            "contextEligible":case["contextEligible"],"scheduledMs":index*2000.,"dispatchMs":index*2000.+1.,
            "startedMs":index*2000.+2.,"finishedMs":index*2000.+2.+value["wallMs"],**value})
    phase = {"schemaVersion":verifier.PHASE_SCHEMA, **{key:plan["schedule"][key] for key in
        ("ratePerSecond", "clientSlots", "maxSchedulerLagMs", "callerTimeoutMs", "thresholdMs")},
        "elapsedMs":rows[-1]["finishedMs"]+1,"rows":rows,"summary":summarize(rows,5000)}
    first = next(case for case in plan["inputs"] if case["contextEligible"])
    warmup = [{"caseId":first["id"],"inputSha256":first["inputSha256"],"inputTokens":first["inputTokens"],"iteration":iteration,**measured(first)} for iteration in range(2)]
    health = {"status":"ready","mode":"shadow","profileJson":json.dumps(profile,ensure_ascii=False,sort_keys=True,separators=(",",":")),"profileSha256":fingerprint(profile)}
    metrics = DecisionMetrics(); samples = []
    for label, elapsed, batch in (("ready_before_scoring",5,[]), ("after_warmup",80,warmup), ("after_inventory",phase["elapsedMs"]+80,rows)):
        for row in batch: metrics.begin(); metrics.finish(outcome(row["observation"]["result"]),.005)
        samples.append({"label":label,"elapsedMs":elapsed,"health":copy.deepcopy(health),"metricsRaw":metrics.render(ready=True).decode(),
            "ownedPids":[42],"processRaw":"synthetic fixture PID, not a live process claim"})
    result = {"schemaVersion":verifier.RESULT_SCHEMA,"status":"observed","planSha256":"pending","warmup":warmup,"phase":phase,
        "samples":samples,"failure":None,"cancelled":False,"ownedPids":[42],"remainingOwnedPids":[],"cleanupErrors":[],"runtimeExitCode":0,
        "elapsedMs":phase["elapsedMs"]+100,"logSha256":{},"referenceLabels":0,"classificationAccuracyMeasured":False,
        "calibrationRequests":0,"holdoutRequests":0,"sloAccepted":False,"representativeAgatTraffic":False,"routingEnabled":False,"qualification":"not_assessed"}
    return context, plan, result


def write_fixture(directory, context, plan, result, *, records=None):
    directory.mkdir(exist_ok=True)
    context = reseal(context); plan = copy.deepcopy(plan); result = copy.deepcopy(result)
    plan.update(contextProfileFileSha256=digest(encoded(context)), contextProfileSealSha256=context["sha256"])
    plan = reseal(plan)
    records = result["phase"]["rows"] if records is None else records
    journal = b"".join(encoded(row).replace(b"\n",b"")+b"\n" for row in records)
    runtime_log = b"Synthetic verification fixture; never a native model run.\n"
    result.update(planSha256=plan["sha256"], logSha256={"runtime.log":digest(runtime_log),"requests.jsonl":digest(journal)})
    result = reseal(result)
    for name, raw in (("context.json",encoded(context)),("plan.json",encoded(plan)),("result.json",encoded(result)),
                      ("phase.json",encoded(sealed(result["phase"]))),("requests.jsonl",journal),("runtime.log",runtime_log)):
        (directory/name).write_bytes(raw)
    return {"context_sha":digest(encoded(context)),"plan_sha":digest(encoded(plan)),"result_sha":digest(encoded(result))}


class PublicLoadVerificationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.original = fixture()
    def setUp(self): self.context,self.plan,self.result = copy.deepcopy(self.original)
    def verify(self, directory, **kwargs): return verifier.verify(ROOT,directory,directory/"context.json",**kwargs)

    def test_complete_fixture_preserves_long_rejection_and_has_no_live_or_accuracy_claim(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(http.client,"HTTPConnection") as network:
            directory = Path(temporary); pins = write_fixture(directory,self.context,self.plan,self.result)
            value = self.verify(directory,**pins); network.assert_not_called()
        self.assertEqual(value["status"],"pass"); self.assertEqual(value["summary"]["failures"],{"context_too_long":1})
        self.assertEqual(value["summary"]["scheduled"],len(self.plan["inputs"]))
        self.assertFalse(value["liveCleanupVerified"]); self.assertFalse(value["classificationAccuracyMeasured"])
        self.assertEqual(value["physicalScheduledHttpHandlers"],len(self.plan["inputs"]))

    def test_external_raw_pin_required_even_when_seal_is_valid(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary); pins = write_fixture(directory,self.context,self.plan,self.result)
            for key in pins:
                changed = dict(pins); changed[key] = "0"*64
                with self.subTest(key=key), patch.object(verifier,"sources_at") as sources, self.assertRaises(ValueError):self.verify(directory,**changed)
                sources.assert_not_called()

    def test_rehashed_plan_scope_budget_input_and_qualification_cannot_bypass_validation(self):
        mutations = (lambda p:p.update(referenceLabels=1),lambda p:p.update(sloAccepted=True),lambda p:p.update(referenceLabels=False),
                     lambda p:p.update(primaryCompanionStarted=True),lambda p:p.update(warmupCount=True),
                     lambda p:p["schedule"].update(clientSlots=True),lambda p:p["inputs"].reverse(),
                     lambda p:p.update(createdAt="2026-10-07T01:00:00.000Z"),lambda p:p.update(profileSha256="0"*64))
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary)
            for mutation in mutations:
                plan=copy.deepcopy(self.plan);mutation(plan);pins=write_fixture(directory,self.context,plan,self.result)
                with self.subTest(mutation=mutation),self.assertRaises(ValueError):self.verify(directory,**pins)

    def test_source_inventory_and_actual_historical_bytes_are_required(self):
        for operation in ("omit","bad_sha","unsafe"):
            plan=copy.deepcopy(self.plan)
            name=next(iter(plan["sourceFiles"]))
            if operation=="omit":plan["sourceFiles"].pop(name)
            elif operation=="bad_sha":plan["sourceFiles"][name]="0"*64
            else:plan["sourceFiles"]["../foreign"]="0"*64
            with tempfile.TemporaryDirectory() as temporary:
                directory=Path(temporary);pins=write_fixture(directory,self.context,plan,self.result)
                with self.subTest(operation=operation),self.assertRaises(ValueError):self.verify(directory,**pins)

    def test_phase_omissions_reorder_offsets_fingerprints_and_false_quantiles_fail(self):
        mutations=(lambda p:p["rows"].pop(),lambda p:p["rows"].reverse(),lambda p:p["rows"][0].update(index=True),
                   lambda p:p["rows"][0].update(caseId="foreign"),lambda p:p["rows"][0].update(scheduledMs=100),
                   lambda p:p["rows"][0].update(inputSha256="0"*64),lambda p:p["summary"].update(goodPerScheduled=1),
                   lambda p:p["summary"]["callerMsScored"].update(p95=0),lambda p:p.update(elapsedMs=999999))
        for mutation in mutations:
            phase=copy.deepcopy(self.result["phase"]);mutation(phase)
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):verifier.verify_phase(phase,self.plan)

    def test_response_timing_probabilities_generation_and_overlimit_are_bound(self):
        mutations=(lambda r:r[0].update(callerMs=0),lambda r:r[0]["observation"]["callerTiming"].update(clock="wall"),
                   lambda r:r[0]["observation"]["result"].update(inputTokens=201),lambda r:r[0]["observation"]["result"].update(generatedTokens=False),
                   lambda r:r[0]["observation"]["result"]["distribution"][0].update(probability=.1),
                   lambda r:r[-1]["observation"]["result"].update(reason="backend_error"),lambda r:r[1].update(startedMs=2),
                   lambda r:r[0].update(finishedMs=1))
        for mutation in mutations:
            phase=copy.deepcopy(self.result["phase"]);mutation(phase["rows"])
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):verifier.verify_phase(phase,self.plan)

    def test_capacity_and_scheduler_drops_keep_denominator_without_http_metadata(self):
        phase=copy.deepcopy(self.result["phase"])
        row=phase["rows"][1];phase["rows"][1]={key:value for key,value in row.items() if key in verifier.ROW_BASE}
        phase["rows"][1].update(status="dropped",reason="client_capacity")
        phase["summary"]=summarize(phase["rows"],5000)
        summary,known,unknown=verifier.verify_phase(phase,self.plan)
        self.assertEqual(summary["admitted"],len(self.plan["inputs"])-1);self.assertEqual(summary["dropped"],{"client_capacity":1})
        self.assertEqual(unknown,0)
        for mutation in (lambda p:p["rows"][1].update(callerMs=0),lambda p:p["rows"][1].update(dispatchMs=2200),
                         lambda p:p["rows"][1].update(reason="scheduler_lag")):
            changed=copy.deepcopy(phase);mutation(changed)
            with self.assertRaises(ValueError):verifier.verify_phase(changed,self.plan)
        phase["rows"][1].update(reason="scheduler_lag",dispatchMs=2200)
        phase["summary"]=summarize(phase["rows"],5000);verifier.verify_phase(phase,self.plan)

    def test_unknown_transport_outcome_retains_failure_and_bounds_physical_count(self):
        row=self.result["phase"]["rows"][0]
        row.update(status="unavailable",reason="unreachable",observation={"status":"unavailable","reason":"unreachable",
            "callerTiming":row["observation"]["callerTiming"]})
        self.result["phase"]["summary"]=summarize(self.result["phase"]["rows"],5000)
        count=len(self.plan["inputs"])+1
        self.result["samples"][-1]["metricsRaw"]=self.result["samples"][-1]["metricsRaw"].replace(
            f'agat_decision_requests_total{{outcome="ok"}} {count}',f'agat_decision_requests_total{{outcome="ok"}} {count-1}')
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary);pins=write_fixture(directory,self.context,self.plan,self.result)
            value=self.verify(directory,**pins)
        self.assertEqual(value["status"],"insufficient_data");self.assertEqual(value["unknownPhysicalOutcomes"],1)
        self.assertEqual(value["physicalHttpAccounting"],"bounded_unknown")
        self.assertEqual(value["summary"]["failures"],{"unreachable":1,"context_too_long":1})

    def test_corrupt_or_duplicate_metrics_and_restart_cannot_claim_physical_parity(self):
        mutations=(lambda r:r["samples"][-1].update(metricsRaw=r["samples"][-1]["metricsRaw"].replace('outcome="context_rejected"} 1','outcome="context_rejected"} 0')),
                   lambda r:r["samples"][-1].update(metricsRaw=r["samples"][-1]["metricsRaw"]+'agat_decision_requests_total{outcome="ok"} 1\n'),
                   lambda r:r["samples"][-1].update(metricsRaw=r["samples"][-1]["metricsRaw"].replace('agat_decision_requests_in_progress 0','agat_decision_requests_in_progress 1')),
                   lambda r:r["samples"][-1].update(metricsRaw=r["samples"][-1]["metricsRaw"].replace('agat_decision_server_start_time_seconds ','agat_decision_server_start_time_seconds 1')),
                   lambda r:r["samples"][0]["health"].update(status="busy"))
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary)
            for mutation in mutations:
                result=copy.deepcopy(self.result);mutation(result);pins=write_fixture(directory,self.context,self.plan,result)
                with self.subTest(mutation=mutation),self.assertRaises(ValueError):self.verify(directory,**pins)

    def test_rehashed_journal_omission_duplicate_or_row_drift_rejected(self):
        original=self.result["phase"]["rows"]
        for records in (original[:-1],original+[original[0]],original[:-1]+[original[0]]):
            with tempfile.TemporaryDirectory() as temporary:
                directory=Path(temporary);pins=write_fixture(directory,self.context,self.plan,self.result,records=records)
                with self.assertRaises(ValueError):self.verify(directory,**pins)

    def test_warmup_cleanup_and_accuracy_claims_fail_even_with_new_result_pin(self):
        mutations=(lambda r:r["warmup"].pop(),lambda r:r["warmup"][0].update(iteration=True),
                   lambda r:r["warmup"][0].update(caseId="foreign"),lambda r:r.update(remainingOwnedPids=[42]),
                   lambda r:r.update(cleanupErrors=["fixture"]),lambda r:r.update(classificationAccuracyMeasured=True),
                   lambda r:r.update(referenceLabels=False),lambda r:r.update(runtimeExitCode=False),lambda r:r.update(ownedPids=[True]))
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary)
            for mutation in mutations:
                result=copy.deepcopy(self.result);mutation(result);pins=write_fixture(directory,self.context,self.plan,result)
                with self.subTest(mutation=mutation),self.assertRaises(ValueError):self.verify(directory,**pins)

    def test_consumed_artifact_drift_cannot_publish_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary);pins=write_fixture(directory,self.context,self.plan,self.result)
            original=verifier.verify_phase
            def changed(*args):
                value=original(*args);(directory/"requests.jsonl").write_bytes(b"changed after consumption\n");return value
            with patch.object(verifier,"verify_phase",side_effect=changed),self.assertRaises(ValueError):self.verify(directory,**pins)

    def test_cli_private_exclusive_output_and_early_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);private=root/"docs/private";private.mkdir(parents=True);directory=private/"fixture"
            pins=write_fixture(directory,self.context,self.plan,self.result);output=private/"verified"
            args=["--evidence-dir",str(directory),"--context-profile",str(directory/"context.json"),
                  "--context-profile-file-sha256",pins["context_sha"],"--plan-file-sha256",pins["plan_sha"],
                  "--result-file-sha256",pins["result_sha"],"--output-dir",str(output)]
            value=self.verify(directory,**pins)
            with patch.object(cli,"ROOT",root),patch.object(cli.launcher,"frozen_sources",return_value=(MEASURED,{})),patch.object(cli,"verify",return_value=value) as verify:
                self.assertEqual(cli.main(args),0);self.assertEqual(output.stat().st_mode&0o777,0o700)
                self.assertEqual((output/"verification.json").stat().st_mode&0o777,0o600)
                self.assertEqual(cli.main(args),1);self.assertEqual(verify.call_count,1)
                output=private/"bad";args[-1]=str(output)
                verify.side_effect=ValueError("fixture rejection")
                self.assertEqual(cli.main(args),1);self.assertFalse(output.exists())


if __name__ == "__main__":unittest.main()
