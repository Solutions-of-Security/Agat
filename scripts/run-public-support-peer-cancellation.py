#!/usr/bin/env python3
"""Run the frozen inventory through owned native peer cancellation and recovery."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import http.client, importlib.util, json, os, signal, socket, subprocess, sys, threading, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from decision_runtime.artifacts import sealed
from decision_runtime.contracts import Request, fingerprint, parse_json
from decision_runtime.model_store import sha256_file, verify_manifest
from scripts.lib import decision_public_workflow_active_cancellation as active
from scripts.lib.decision_arrival_rate import measure_one
from scripts.lib.decision_performance import profile_from_health
from scripts.lib.decision_public_context import verify_profile
from scripts.lib.decision_public_load import historical_context_sources, validate_context
from scripts.lib.decision_public_sources import pinned_input, private_directory, write_json_new
from scripts.lib.decision_public_workflow import PROFILE_PATH
from scripts.lib.decision_public_peer_cancellation_verification import ARTIFACTS, PATHS, inventory
from scripts.lib.decision_public_workflow_recovery import http_metrics
from workers.local_decisions import LocalDecisionClient

SPEC = importlib.util.spec_from_file_location("peer_cancellation_sources",ROOT/"scripts/run-decision-arrival-rate.py")
launcher = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(launcher); runtime = launcher.runtime
now = lambda: datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00","Z")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("context-profile","runtime-python","manifest","evidence-dir"): parser.add_argument("--"+name,type=Path,required=True)
    parser.add_argument("--context-profile-file-sha256",required=True); parser.add_argument("--cancel-at-index",type=int,required=True)
    args = parser.parse_args(argv)
    try:
        private = (ROOT/"docs/private").resolve()
        runtime.require(args.evidence_dir.resolve().is_relative_to(private) and args.evidence_dir.resolve() != private
            and not args.evidence_dir.exists() and not args.evidence_dir.is_symlink(),"Use a new private evidence directory")
        context_raw = pinned_input(args.context_profile,args.context_profile_file_sha256,32*1024*1024)
        context = validate_context(parse_json(context_raw)); historical_context_sources(ROOT,context)
        spec = active.active_spec(context,args.cancel_at_index); commit,sources = launcher.frozen_sources(PATHS)
        profile_raw = pinned_input(ROOT/PROFILE_PATH,context["profileFileSha256"],1048576); profile = parse_json(profile_raw)
        manifest,_ = verify_manifest(args.manifest.resolve()); verify_profile(profile,manifest)
        runtime.require(sha256_file(args.manifest) == context["manifestFileSha256"] and fingerprint(profile) == context["profileSha256"],"Frozen profile/model differs")
        requirements = dict(line.split("==") for line in (ROOT/"decision_runtime/requirements-mlx.txt").read_text().splitlines() if line and not line.startswith("#"))
        code = 'import importlib.metadata,json,platform;print(json.dumps({"python":platform.python_version(),"machine":platform.machine(),"packages":{k:importlib.metadata.version(k) for k in '+repr(list(requirements))+'}}))'
        environment = parse_json(subprocess.check_output([str(args.runtime_python.absolute()),"-B","-c",code],cwd=ROOT,timeout=15))
        runtime.require(environment == context["tokenizerEnvironment"],"Frozen native dependencies differ")
        directory = private_directory(ROOT,args.evidence_dir)
        plan = sealed({"schemaVersion":"agat.decision.public-peer-cancellation-plan.v1","createdAt":now(),"sourceCommit":commit,"sourceFiles":sources,
            "contextProfileFileSha256":args.context_profile_file_sha256,"context":context,"spec":spec,"runtime":environment,
            "profileFileSha256":context["profileFileSha256"],"manifestFileSha256":context["manifestFileSha256"],
            "mode":"serial_direct_native_peer_cancellation","primary":"not_exercised","coordinatorExercised":False,
            "durableAccountingExercised":False,"warmupCount":4,"ownersAppointed":False,"referenceLabels":0,
            "sloAccepted":False,"routingEnabled":False,"qualification":"not_assessed"})
        write_json_new(directory/"plan.json",plan); plan_sha = sha256_file(directory/"plan.json")
    except Exception as error:
        print("Cannot prepare native peer cancellation: "+type(error).__name__,file=sys.stderr); return 1
    stopped = threading.Event(); old_signals = {sig:signal.signal(sig,lambda *_:stopped.set()) for sig in (signal.SIGINT,signal.SIGTERM)}
    processes = []; owned = set(); errors = []; samples = []; rows = []; warmup = []; proxy = None; task = None; failure = None
    target_cancel = threading.Event(); ready = None; drained = None; retired = None; recovered = None
    started = time.monotonic(); old_umask = os.umask(0o077); port = None
    try:
        with socket.socket() as bound: bound.bind(("127.0.0.1",0)); port = bound.getsockname()[1]
        runtime.require(port != 8766,"Owned runtime collides with protected resident")
        def launch(epoch):
            log_path = directory/("runtime.log" if epoch == 0 else "runtime-recovered.log")
            with runtime.open_private_log(log_path) as log:
                child = subprocess.Popen([str(args.runtime_python.absolute()),"-B","-m","decision_runtime","serve","--manifest",str(args.manifest.resolve()),
                    "--policy",str(ROOT/runtime.SHADOW_POLICY),"--max-tokens","2048","--cache-limit-mib","128","--wired-limit-mib","4096",
                    "--inference-timeout-ms","5000","--exit-on-backend-unavailable","--port",str(port)],cwd=ROOT,start_new_session=True,
                    env={**os.environ,"HF_HUB_OFFLINE":"1","TRANSFORMERS_OFFLINE":"1","HF_HUB_DISABLE_TELEMETRY":"1"},stdout=log,stderr=subprocess.STDOUT)
            processes.append(child); owned.add(child.pid); deadline = time.monotonic()+60
            while True:
                runtime.require(child.poll() is None and not stopped.is_set(),"Owned native startup failed or was cancelled")
                try: health = runtime.request(port,"/health"); break
                except (OSError,ValueError,http.client.HTTPException):
                    runtime.require(time.monotonic()<deadline,"Native startup deadline exceeded"); time.sleep(.05)
            runtime.require(profile_from_health(health) == profile and health["profileSha256"] == context["profileSha256"],"Native ready profile differs")
            owned.update(runtime.shared.inventory(child.pid)[0]); return child
        def sample(child,epoch,label):
            raw = active.bounded_metrics(port); counts,server_start = http_metrics(raw,in_progress=0)
            owned.update(runtime.shared.inventory(child.pid)[0]); health = runtime.request(port,"/health")
            runtime.require(profile_from_health(health) == profile,"Native sample profile drift")
            item = {"label":label,"epoch":epoch,"capturedAt":now(),"runtimePid":child.pid,"metricsRaw":raw,"counters":counts,
                "serverStart":server_start,"health":health,"ownedPids":sorted(owned),"elapsedMs":round((time.monotonic()-started)*1000,3)}
            samples.append(item); return item
        def warm(child,epoch):
            client = LocalDecisionClient(f"http://127.0.0.1:{port}"); case = next(c for c in context["inputs"] if c["contextEligible"])
            origin = sample(child,epoch,"ready_before_scoring")
            runtime.require(all(v == 0 for v in origin["counters"].values()),"Native epoch did not begin empty")
            for iteration in range(2):
                row = measure_one(client,Request.from_dict(case["request"]),profile,10000)
                runtime.require(row["status"] in {"ok","abstain"},"Native warmup failed")
                warmup.append({"epoch":epoch,"iteration":iteration,"caseId":case["id"],**row})
            return sample(child,epoch,"after_warmup")
        child = launch(0); initial = warm(child,0)
        proxy = active.ActiveCancellationProxy(port,context,spec)
        proxy.bind_warmups(dict(Counter(row["status"] for row in warmup)),initial["serverStart"])
        client = LocalDecisionClient(f"http://127.0.0.1:{proxy.port}")
        for index,case in enumerate(context["inputs"]):
            runtime.require(not stopped.is_set() and time.monotonic()-started < 440 and not proxy.errors,"Peer experiment exceeded budget or relay failed")
            request = Request.from_dict(case["request"])
            if index != spec["targetIndex"]: observed = measure_one(client,request,profile,10000)
            else:
                sample(child,0,"before_target"); native_pids = runtime.shared.inventory(child.pid)[0]; owned.update(native_pids)
                class CancelClient:
                    def decide(self,config): return client.decide(config,target_cancel)
                result_box = []
                task = threading.Thread(target=lambda:result_box.append(measure_one(CancelClient(),request,profile,10000)))
                task.start(); deadline = time.monotonic()+spec["activeObserveDeadlineMs"]/1000
                while proxy.ready_receipt() is None:
                    runtime.require(task.is_alive() and not stopped.is_set() and not proxy.errors and time.monotonic()<deadline,"Native target did not reach its active barrier")
                    time.sleep(.005)
                ready = proxy.ready_receipt(); write_json_new(directory/"active-ready.json",ready)
                requested = now(); target_cancel.set(); task.join(3)
                runtime.require(not task.is_alive() and len(result_box) == 1,"Cancelled local caller did not drain")
                observed = result_box[0]
                runtime.require(observed["status"] == "unavailable" and observed["reason"] == "cancelled","Target accepted a result or timed out")
                deadline = time.monotonic()+3
                while proxy.target_receipt() is None:
                    runtime.require(not proxy.errors and time.monotonic()<deadline,"Native EOF was not propagated"); time.sleep(.005)
                drained = proxy.target_receipt(); write_json_new(directory/"active-drained.json",drained)
                write_json_new(directory/"caller-cancellation-applied.json",{"schemaVersion":"agat.decision.public-peer-cancellation-applied.v1",
                    "targetIndex":index,"caseId":case["id"],"inputSha256":case["inputSha256"],"requestedAt":requested,
                    "readyFileSha256":sha256_file(directory/"active-ready.json"),"method":"local_caller_event","coordinatorExercised":False})
                runtime.require(child.wait(spec["retirementDeadlineMs"]/1000) == 75,"Native runtime did not retire by cancellation")
                remaining = runtime.remaining_owned_processes(native_pids,errors)
                retired = active.retired_receipt(spec,ready,drained,runtime_pid=child.pid,native_pids=sorted(native_pids),exit_code=child.returncode,
                    remaining=remaining,log_raw=(directory/"runtime.log").read_bytes(),observed_at=now())
                write_json_new(directory/"native-retired.json",retired)
                child = launch(1); warm(child,1)
                write_json_new(directory/"recovery-warmup.json",warmup[2:])
                recovered = active.recovered_receipt(spec,retired,runtime_pid=child.pid,profile_sha=context["profileSha256"],
                    server_start=samples[-1]["serverStart"],warmup_file_sha=sha256_file(directory/"recovery-warmup.json"),applied_at=now())
                write_json_new(directory/"native-recovered.json",recovered)
            runtime.require(observed["status"] != "measurement_error","Local peer measurement failed")
            rows.append({"index":index,"caseId":case["id"],"inputSha256":case["inputSha256"],**observed})
        sample(child,1,"after_inventory"); proxy.close(); write_json_new(directory/"relay-transport.json",proxy.receipt())
        runtime.require(launcher.frozen_sources(PATHS) == (commit,sources) and sha256_file(directory/"plan.json") == plan_sha
            and pinned_input(args.context_profile,args.context_profile_file_sha256,32*1024*1024) == context_raw
            and pinned_input(ROOT/PROFILE_PATH,context["profileFileSha256"],1048576) == profile_raw
            and verify_manifest(args.manifest)[0] == manifest,"Frozen peer sources or model changed")
    except Exception as error: failure = {"type":type(error).__name__,"reason":str(error)[:200]}
    finally:
        target_cancel.set()
        if task is not None: task.join(3)
        for child in processes:
            if child.poll() is None: runtime.stop_owned_process(child,owned,errors)
        if proxy is not None and not proxy.closed:
            try: proxy.close(); write_json_new(directory/"relay-transport.json",proxy.receipt())
            except Exception as error: errors.append("relayCleanup:"+type(error).__name__)
        remaining = runtime.remaining_owned_processes(owned,errors)
        for sig,handler in old_signals.items(): signal.signal(sig,handler)
        os.umask(old_umask)
    artifacts = {path.name:sha256_file(path) for path in directory.iterdir() if path.is_file() and path.name != "plan.json"}
    result = sealed({"schemaVersion":"agat.decision.public-peer-cancellation-result.v1","status":"observed" if failure is None and not errors and remaining == [] else "failed",
        "planSha256":plan["sha256"],"rows":rows,"warmup":warmup,"samples":samples,"retired":retired,"recovered":recovered,
        "failure":failure,"ownedPids":sorted(owned),"remainingOwnedPids":remaining,"cleanupErrors":errors,
        "runtimeExitCodes":[child.returncode for child in processes],"artifactSha256":artifacts,"elapsedMs":round((time.monotonic()-started)*1000,3),
        "primary":"not_exercised","coordinatorExercised":False,"durableAccountingExercised":False,"ownersAppointed":False,
        "referenceLabels":0,"classificationAccuracyMeasured":False,"sloAccepted":False,"routingEnabled":False,"qualification":"not_assessed"})
    if result["status"] == "observed":
        try: inventory(context,plan,result,{name:(directory/name).read_bytes() for name in ARTIFACTS})
        except Exception as error:
            failure = {"type":type(error).__name__,"reason":str(error)[:200]}
            result = sealed({key:value for key,value in result.items() if key != "sha256"} | {"status":"failed","failure":failure})
    write_json_new(directory/"result.json",result)
    print(json.dumps({"status":result["status"],"rows":len(rows),"failure":failure,"remainingOwnedPids":remaining}))
    return 0 if result["status"] == "observed" else 1


if __name__ == "__main__": raise SystemExit(main())
