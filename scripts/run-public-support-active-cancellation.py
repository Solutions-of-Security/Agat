#!/usr/bin/env python3
"""Run actual coordinator cancellation at the active native HTTP boundary and recover."""
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
from scripts.lib.decision_public_workflow import PROFILE_PATH, SOURCE_PATHS, shared_config, verify_inventory
from scripts.lib.decision_public_peer_cancellation_verification import verify_physical
from scripts.lib import decision_public_workflow_active_integration as integration

PATHS = SOURCE_PATHS+integration.SOURCE_PATHS
BASE_ARTIFACTS = {"workflow-plan.json","workflow-driver.json","workflow-routes.jsonl","cohort.http.json","worker.log","runtime.log","driver.log"}
ARTIFACTS = BASE_ARTIFACTS|integration.ARTIFACTS
from scripts.lib.decision_public_workflow_recovery import http_metrics
from workers.local_decisions import LocalDecisionClient

SPEC = importlib.util.spec_from_file_location("active_workflow_sources",ROOT/"scripts/run-decision-arrival-rate.py")
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
        context = validate_context(parse_json(context_raw)); historical_context_sources(ROOT,context); config = shared_config(context)
        spec = active.active_spec(context,args.cancel_at_index); commit,sources = launcher.frozen_sources(PATHS)
        profile_raw = pinned_input(ROOT/PROFILE_PATH,context["profileFileSha256"],1048576); profile = parse_json(profile_raw)
        manifest,_ = verify_manifest(args.manifest.resolve()); verify_profile(profile,manifest)
        runtime.require(sha256_file(args.manifest) == context["manifestFileSha256"] and fingerprint(profile) == context["profileSha256"],"Frozen profile/model differs")
        requirements = dict(line.split("==") for line in (ROOT/"decision_runtime/requirements-mlx.txt").read_text().splitlines() if line and not line.startswith("#"))
        code = 'import importlib.metadata,json,platform;print(json.dumps({"python":platform.python_version(),"machine":platform.machine(),"packages":{k:importlib.metadata.version(k) for k in '+repr(list(requirements))+'}}))'
        environment = parse_json(subprocess.check_output([str(args.runtime_python.absolute()),"-B","-c",code],cwd=ROOT,timeout=15))
        runtime.require(environment == context["tokenizerEnvironment"],"Frozen native dependencies differ")
        directory = private_directory(ROOT,args.evidence_dir)
        plan = sealed({"schemaVersion":active.LAUNCH_PLAN,"createdAt":now(),"sourceCommit":commit,"sourceFiles":sources,
            "contextProfileFileSha256":args.context_profile_file_sha256,"context":context,"coordinatorCancellation":spec,"coordinatorTrigger":integration.TRIGGER,"config":config,"runtime":environment,
            "profileFileSha256":context["profileFileSha256"],"manifestFileSha256":context["manifestFileSha256"],
            "mode":"serial_closed_model_integration","primary":"fixture_chat_completions","warmupCount":4,"ownersAppointed":False,"referenceLabels":0,
            "sloAccepted":False,"routingEnabled":False,"qualification":"not_assessed"})
        write_json_new(directory/"plan.json",plan); plan_sha = sha256_file(directory/"plan.json")
    except Exception as error:
        print("Cannot prepare active native workflow cancellation: "+type(error).__name__,file=sys.stderr); return 1
    stopped=threading.Event(); old_signals={sig:signal.signal(sig,lambda *_:stopped.set()) for sig in (signal.SIGINT,signal.SIGTERM)}
    processes=[]; owned=set(); errors=[]; samples=[]; warmup=[]; proxy=None; driver=None; failure=None
    ready=drained=retired=recovered=armed=prepared=evidence=transport=None; native_pids=[]
    started=time.monotonic(); old_umask=os.umask(0o077)
    try:
        with socket.socket() as bound: bound.bind(("127.0.0.1",0)); port=bound.getsockname()[1]
        runtime.require(port!=8766,"Owned runtime collides with protected resident")
        def launch(epoch):
            with runtime.open_private_log(directory/("runtime.log" if epoch==0 else "runtime-recovered.log")) as log:
                child=subprocess.Popen([str(args.runtime_python.absolute()),"-B","-m","decision_runtime","serve","--manifest",str(args.manifest.resolve()),
                    "--policy",str(ROOT/runtime.SHADOW_POLICY),"--max-tokens","2048","--cache-limit-mib","128","--wired-limit-mib","4096",
                    "--inference-timeout-ms","5000","--exit-on-backend-unavailable","--port",str(port)],cwd=ROOT,start_new_session=True,
                    env={**os.environ,"HF_HUB_OFFLINE":"1","TRANSFORMERS_OFFLINE":"1","HF_HUB_DISABLE_TELEMETRY":"1"},stdout=log,stderr=subprocess.STDOUT)
            processes.append(child); owned.add(child.pid); deadline=time.monotonic()+60
            while True:
                runtime.require(child.poll() is None and not stopped.is_set(),"Owned native startup failed or cancelled")
                try: health=runtime.request(port,"/health"); break
                except (OSError,ValueError,http.client.HTTPException):
                    runtime.require(time.monotonic()<deadline,"Native startup deadline exceeded"); time.sleep(.05)
            runtime.require(profile_from_health(health)==profile and health['profileSha256']==context['profileSha256'],"Native ready profile differs")
            owned.update(runtime.shared.inventory(child.pid)[0]); return child
        def sample(child,epoch,label):
            raw=active.bounded_metrics(port); counts,server_start=http_metrics(raw,in_progress=0)
            owned.update(runtime.shared.inventory(child.pid)[0]); health=runtime.request(port,"/health")
            runtime.require(profile_from_health(health)==profile,"Native sample profile drift")
            item={"label":label,"epoch":epoch,"capturedAt":now(),"runtimePid":child.pid,"metricsRaw":raw,"counters":counts,
                "serverStart":server_start,"health":health,"ownedPids":sorted(owned),"elapsedMs":round((time.monotonic()-started)*1000,3)}
            samples.append(item); return item
        def warm(child,epoch):
            client=LocalDecisionClient(f"http://127.0.0.1:{port}"); case=next(c for c in context['inputs'] if c['contextEligible'])
            origin=sample(child,epoch,"ready_before_scoring")
            runtime.require(all(v==0 for v in origin['counters'].values()),"Native epoch did not begin empty")
            for iteration in range(2):
                observed=measure_one(client,Request.from_dict(case['request']),profile,10000)
                runtime.require(observed['status'] in {'ok','abstain'},"Native warmup failed")
                warmup.append({"epoch":epoch,"iteration":iteration,"caseId":case['id'],**observed})
            return sample(child,epoch,"after_warmup")
        child=launch(0); initial=warm(child,0)
        proxy=integration.PreparedActiveCancellationProxy(port,context,spec)
        proxy.bind_warmups(dict(Counter(row['status'] for row in warmup)),initial['serverStart'])
        env={key:value for key,value in os.environ.items() if not key.startswith(('AGAT_','OTEL_'))}
        with runtime.open_private_log(directory/'driver.log') as log:
            driver=subprocess.Popen(['node','--import','tsx','scripts/run-public-support-workflow.mts','--decision-url',
                f'http://127.0.0.1:{proxy.port}','--evidence-dir',str(directory)],cwd=ROOT,env={**env,'OTEL_SDK_DISABLED':'true'},
                stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        owned.add(driver.pid); next_inventory=0
        while driver.poll() is None:
            runtime.require(not stopped.is_set() and time.monotonic()-started<440 and not proxy.errors,"Workflow cancelled, exceeded budget or relay failed")
            if time.monotonic()>=next_inventory:
                owned.update(runtime.shared.inventory(driver.pid)[0]); next_inventory=time.monotonic()+1
            if armed is None and (directory/'active-native-arm-request.json').exists():
                arm_raw=(directory/'active-native-arm-request.json').read_bytes(); runtime.require(len(arm_raw)<=65536,"Arm request excessive")
                arm=parse_json(arm_raw); routes=[parse_json(line) for line in (directory/'workflow-routes.jsonl').read_bytes().splitlines()]
                integration.arm_request(context,spec,arm,routes); prefix=sample(child,0,'before_target')
                native_pids=sorted(runtime.shared.inventory(child.pid)[0]); owned.update(native_pids)
                armed=integration.armed_receipt(spec,sha256_file(directory/'active-native-arm-request.json'),child.pid,prefix['serverStart'],now())
                integration.publish_barrier(directory,'active-native-armed.json',armed)
            if prepared is None and (directory/'coordinator-cancellation-prepared.json').exists():
                raw=(directory/'coordinator-cancellation-prepared.json').read_bytes(); runtime.require(len(raw)<=65536,"Preparation receipt excessive")
                before_raw=(directory/'coordinator-cancellation-before.http.json').read_bytes(); runtime.require(len(before_raw)<=16*1024*1024,"Prepared trace excessive")
                prepared=integration.validate_preparation(context,spec,parse_json(raw),before_raw)
                runtime.require(armed is not None,"Active preparation bypassed original prefix arming")
                proxy.prepared.set()
            if ready is None:
                ready=proxy.ready_receipt()
                if ready is not None:
                    runtime.require(prepared is not None and ready['stageId']==prepared['stageId'],"Active target bypassed original preparation barrier")
                    integration.publish_barrier(directory,'coordinator-cancellation-ready.json',ready)
            if drained is None:
                drained=proxy.target_receipt()
                if drained is not None:
                    integration.publish_barrier(directory,'coordinator-cancellation-drained.json',drained)
                    retirement_deadline=time.monotonic()+spec['retirementDeadlineMs']/1000
                    runtime.require(child.wait(max(.001,retirement_deadline-time.monotonic()))==75,"Native runtime did not retire by active cancellation")
                    remaining_native=integration.await_native_cleanup(native_pids,lambda pids:runtime.remaining_owned_processes(pids,errors),deadline=retirement_deadline)
                    retired=active.retired_receipt(spec,ready,drained,runtime_pid=child.pid,native_pids=native_pids,exit_code=child.returncode,
                        remaining=remaining_native,log_raw=(directory/'runtime.log').read_bytes(),observed_at=now())
                    integration.publish_barrier(directory,'native-retired.json',retired)
                    child=launch(1); warm(child,1); write_json_new(directory/'recovery-warmup.json',warmup[2:])
                    recovered=active.recovered_receipt(spec,retired,runtime_pid=child.pid,profile_sha=context['profileSha256'],
                        server_start=samples[-1]['serverStart'],warmup_file_sha=sha256_file(directory/'recovery-warmup.json'),applied_at=now())
                    integration.publish_barrier(directory,'native-recovered.json',recovered)
            runtime.require(child.poll() is None,"Owned native runtime exited outside declared cancellation")
            time.sleep(.005)
        runtime.require(driver.returncode==0 and recovered is not None,"Active workflow driver failed or omitted recovery")
        recipe=parse_json((directory/'workflow-plan.json').read_bytes()); cohort=parse_json((directory/'cohort.http.json').read_bytes())
        observed=parse_json((directory/'workflow-driver.json').read_bytes()); owned.update(observed['ownedPids'])
        proxy.close(); transport=proxy.receipt(); write_json_new(directory/'caller-cancellation-transport.json',transport)
        raw_artifacts={name:(directory/name).read_bytes() for name in ARTIFACTS}
        evidence=verify_inventory(context,recipe,cohort,observed['routes'],transport,integration.receipt_bundle(raw_artifacts))
        runtime.require(observed['status']=='observed' and observed['primaryCalls']==len(context['inputs']) and observed['workerExitCode']==0
            and observed['unauthenticatedStatus']==401 and observed['activeNativeArmed']==armed and observed['activeNativeRecovered']==recovered
            and observed['coordinatorCancellationPrepared']==prepared
            and observed['coordinatorCancellationReady']==ready and observed['coordinatorCancellationDrained']==drained,"Driver audit or barriers differ")
        sample(child,1,'after_inventory')
        runtime.require(launcher.frozen_sources(PATHS)==(commit,sources) and sha256_file(directory/'plan.json')==plan_sha
            and pinned_input(args.context_profile,args.context_profile_file_sha256,32*1024*1024)==context_raw
            and pinned_input(ROOT/PROFILE_PATH,context['profileFileSha256'],1048576)==profile_raw
            and verify_manifest(args.manifest)[0]==manifest,"Frozen workflow sources or model changed")
    except Exception as error: failure={'type':type(error).__name__,'reason':str(error)[:200]}
    finally:
        if driver is not None: runtime.stop_owned_process(driver,owned,errors)
        try: (directory/'worker-credentials.json').unlink(missing_ok=True)
        except OSError as error: errors.append('workerCredentialStop:'+type(error).__name__)
        for child in processes:
            if child.poll() is None: runtime.stop_owned_process(child,owned,errors)
        if proxy is not None and not proxy.closed:
            try: proxy.close(); transport=proxy.receipt(); write_json_new(directory/'caller-cancellation-transport.json',transport)
            except Exception as error: errors.append('relayCleanup:'+type(error).__name__)
        remaining=runtime.remaining_owned_processes(owned,errors)
        for sig,handler in old_signals.items(): signal.signal(sig,handler)
        os.umask(old_umask)
    complete=evidence is not None and failure is None and not errors and remaining==[] and not stopped.is_set()
    physical=None
    result={"schemaVersion":active.LAUNCH_RESULT,"status":"observed" if complete else "failed","planSha256":plan['sha256'],
        "coordinatorCancellation":spec,"coordinatorTrigger":integration.TRIGGER,"evidence":evidence,"physical":physical,
        "warmup":warmup,"samples":samples,"nativeRetired":retired,"nativeRecovered":recovered,"failure":failure,"ownedPids":sorted(owned),
        "remainingOwnedPids":remaining,"cleanupErrors":errors,"runtimeExitCodes":[child.returncode for child in processes],
        "driverExitCode":driver.returncode if driver else None,"artifactSha256":{name:sha256_file(directory/name) if (directory/name).exists() else None for name in sorted(ARTIFACTS)},
        "elapsedMs":round((time.monotonic()-started)*1000,3),"primary":"fixture_chat_completions","ownersAppointed":False,
        "referenceLabels":0,"classificationAccuracyMeasured":False,"sloAccepted":False,"routingEnabled":False,"qualification":"not_assessed"}
    if complete:
        try:
            runtime.require(0<=result['elapsedMs']<=440000,"Overall workflow budget exceeded")
            runtime.require(result['runtimeExitCodes']==[75,130],"Runtime cleanup exits differ")
            runtime.require(set(retired['nativePids'])<=owned and recovered['runtimePid'] in owned,"Native retirement/recovery ownership missing")
            result['physical']=verify_physical(context,result,transport,ready,retired,recovered,raw_artifacts)
        except Exception as error: complete=False; failure={'type':type(error).__name__,'reason':str(error)[:200]}; result.update(status='failed',failure=failure)
    write_json_new(directory/'result.json',sealed(result))
    print(json.dumps({'status':result['status'],'evidence':evidence,'physical':result['physical'],'failure':failure,'remainingOwnedPids':remaining}))
    return 0 if complete else 1


if __name__=='__main__': raise SystemExit(main())
