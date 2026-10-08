#!/usr/bin/env python3
"""Own a temporary wired runtime for the whole unlabelled public development corpus."""
import argparse
from datetime import datetime, timezone
import http.client
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_runtime.artifacts import sealed
from decision_runtime.contracts import Request, fingerprint, parse_json
from decision_runtime.model_store import verify_manifest, sha256_file
from scripts.lib.decision_arrival_rate import SOURCE_PATHS, measure_one
from scripts.lib.decision_performance import profile_from_health
from scripts.lib.decision_public_context import verify_profile
from scripts.lib.decision_public_load import PLAN_SCHEMA, RESULT_SCHEMA, historical_context_sources, run_inventory, validate_context
from scripts.lib.decision_public_sources import pinned_input, private_directory, write_json_new
from workers.local_decisions import LocalDecisionClient

SPEC = importlib.util.spec_from_file_location("public_arrival_launcher", ROOT/"scripts/run-decision-arrival-rate.py")
launcher = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(launcher)
runtime = launcher.runtime
PROFILE_PATH = "docs/qualification/local-decisions/performance/profiles/runtime-0.12.3-wired-4096.json"
EXTRA_SOURCES = ["scripts/run-public-support-load.py", "scripts/lib/decision_public_load.py", "scripts/lib/decision_public_context.py",
                 "scripts/profile-public-support-context.py", "scripts/lib/decision_public_sources.py", "scripts/lib/decision_shadow_pilot.py",
                 "scripts/test/test_decision_public_load.py"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context-profile", type=Path, required=True)
    parser.add_argument("--context-profile-file-sha256", required=True)
    parser.add_argument("--runtime-python", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        private = (ROOT/"docs/private").resolve()
        runtime.require(args.evidence_dir.resolve().is_relative_to(private) and args.evidence_dir.resolve() != private
                        and not args.evidence_dir.exists() and not args.evidence_dir.is_symlink(), "Use a new private evidence directory")
        context_raw = pinned_input(args.context_profile,args.context_profile_file_sha256,32*1024*1024)
        context = validate_context(parse_json(context_raw)); historical_context_sources(ROOT,context)
        profile_raw = pinned_input(ROOT/PROFILE_PATH,context["profileFileSha256"],1024*1024)
        profile = parse_json(profile_raw)
        runtime.require(fingerprint(profile) == context["profileSha256"], "Serving profile differs from frozen context")
        paths = [*SOURCE_PATHS,*EXTRA_SOURCES,PROFILE_PATH,runtime.SHADOW_POLICY,runtime.SHADOW_REFERENCE]
        commit,sources = launcher.frozen_sources(paths)
        manifest,_ = verify_manifest(args.manifest.resolve())
        verify_profile(profile,manifest)
        runtime.require(sha256_file(args.manifest)==context["manifestFileSha256"], "Use the exact context model manifest")
        requirements = dict(line.split("==") for line in (ROOT/"decision_runtime/requirements-mlx.txt").read_text().splitlines() if line and not line.startswith("#"))
        code = 'import importlib.metadata,json,platform,sys;print(json.dumps({"python":platform.python_version(),"machine":platform.machine(),"packages":{k:importlib.metadata.version(k) for k in sys.argv[1:]}}))'
        environment = parse_json(subprocess.check_output([str(args.runtime_python.absolute()),"-B","-c",code,*requirements],cwd=ROOT,timeout=15))
        runtime.require(environment==context["tokenizerEnvironment"], "Runtime dependencies differ from frozen tokenization")
        directory = private_directory(ROOT,args.evidence_dir)
        plan = sealed({"schemaVersion":PLAN_SCHEMA,"createdAt":datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00","Z"),
            "sourceCommit":commit,"sourceFiles":sources,
            "contextProfileFileSha256":args.context_profile_file_sha256,"contextProfileSealSha256":context["sha256"],
            "profile":profile,"profileFileSha256":context["profileFileSha256"],"profileSha256":context["profileSha256"],
            "manifestFileSha256":context["manifestFileSha256"],"model":context["model"],"runtime":environment,
            "inputs":context["inputs"],"schedule":context["proposedDiagnosticSchedule"],"warmupCount":2,
            "scope":"whole_unlabelled_public_development_http_inventory","overLimitBehavior":"send_full_input_expect_context_too_long",
            "primaryCompanionStarted":False,"backgroundWorkloadControlled":False,"referenceLabels":0,
            "calibrationRequests":0,"holdoutRequests":0,"sloAccepted":False,"representativeAgatTraffic":False,
            "routingEnabled":False,"qualification":"not_assessed"})
        write_json_new(directory/"plan.json",plan)
    except Exception as error:
        print(f"Cannot prepare public HTTP load: {type(error).__name__}",file=sys.stderr); return 1
    process = None; owned = set(); errors = []; samples = []; warmup = []; phase = None; failure = None
    stopped = [False]; start = time.monotonic()
    previous = {sig:signal.signal(sig,lambda *_:stopped.__setitem__(0,True)) for sig in (signal.SIGINT,signal.SIGTERM)}
    try:
        with runtime.open_private_log(directory/"runtime.log") as log, runtime.open_private_log(directory/"requests.jsonl") as journal:
            with socket.socket() as bound:
                bound.bind(("127.0.0.1",0)); port = bound.getsockname()[1]
            process = subprocess.Popen([str(args.runtime_python.absolute()),"-B","-m","decision_runtime","serve",
                "--manifest",str(args.manifest.resolve()),"--policy",str(ROOT/runtime.SHADOW_POLICY),"--max-tokens","2048",
                "--cache-limit-mib","128","--wired-limit-mib","4096","--inference-timeout-ms","5000",
                "--exit-on-backend-unavailable","--port",str(port)],cwd=ROOT,
                env={**os.environ,"HF_HUB_OFFLINE":"1","TRANSFORMERS_OFFLINE":"1","HF_HUB_DISABLE_TELEMETRY":"1"},
                stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            owned.add(process.pid); deadline = time.monotonic()+60
            while True:
                runtime.require(process.poll() is None and not stopped[0], "Owned runtime exited or startup cancelled")
                try:
                    health = runtime.request(port,"/health"); break
                except (OSError,ValueError,http.client.HTTPException):
                    runtime.require(time.monotonic()<deadline,"Runtime startup timeout");time.sleep(.1)
            runtime.require(profile_from_health(health)==profile and health["profileSha256"]==context["profileSha256"], "Ready profile differs")
            client = LocalDecisionClient(f"http://127.0.0.1:{port}")
            def sample(label):
                runtime.require(process.poll() is None,"Owned runtime exited during corpus load")
                owned.update(runtime.shared.inventory(process.pid)[0])
                connection = http.client.HTTPConnection("127.0.0.1",port,timeout=5)
                try:
                    connection.request("GET","/metrics"); response=connection.getresponse(); raw=response.read(1048577)
                    runtime.require(response.status==200 and len(raw)<=1048576,"Runtime metrics failed")
                finally:connection.close()
                health=runtime.request(port,"/health")
                runtime.require(profile_from_health(health)==profile,"Runtime profile drift")
                samples.append({"label":label,"elapsedMs":round((time.monotonic()-start)*1000,3),"health":health,
                    "metricsRaw":raw.decode(),"ownedPids":sorted(owned),"processRaw":runtime.command(["ps","-o","pid=,ppid=,rss=,lstart=","-p",",".join(map(str,sorted(owned)))])})
            sample("ready_before_scoring")
            case = next(row for row in context["inputs"] if row["contextEligible"])
            for iteration in range(2):
                runtime.require(not stopped[0], "Warmup cancelled")
                row=measure_one(client,Request.from_dict(case["request"]),profile,10000)
                warmup.append({"caseId":case["id"],"inputSha256":case["inputSha256"],"inputTokens":case["inputTokens"],"iteration":iteration,**row})
                runtime.require(row["status"] in {"ok","abstain"} and row["observation"]["result"].get("inputTokens")==case["inputTokens"],"Warmup failed")
            sample("after_warmup")
            def record(row):
                journal.write((json.dumps(row,ensure_ascii=False,allow_nan=False)+"\n").encode());journal.flush()
            phase=run_inventory(client,context["inputs"],profile,cancelled=lambda:stopped[0],journal=record)
            write_json_new(directory/"phase.json",sealed(phase));sample("after_inventory")
            runtime.require(launcher.frozen_sources(paths)==(commit,sources),"Load sources changed")
            runtime.require(pinned_input(args.context_profile,args.context_profile_file_sha256,32*1024*1024)==context_raw,
                            "Context artifact changed during load")
            runtime.require(pinned_input(ROOT/PROFILE_PATH,context["profileFileSha256"],1024*1024)==profile_raw
                            and sha256_file(args.manifest)==context["manifestFileSha256"] and verify_manifest(args.manifest)[0]==manifest,
                            "Model/profile artifacts changed")
    except Exception as error:
        failure={"type":type(error).__name__,"reason":str(error)[:300]}
    finally:
        if process is not None:runtime.stop_owned_process(process,owned,errors)
        remaining=runtime.remaining_owned_processes(owned,errors)
        for sig,handler in previous.items():signal.signal(sig,handler)
    complete=phase is not None and failure is None and not stopped[0] and not errors and not remaining
    complete=complete and not any(row["status"]=="measurement_error" for row in phase["rows"])
    result=sealed({"schemaVersion":RESULT_SCHEMA,"status":"observed" if complete else "failed","planSha256":plan["sha256"],
        "warmup":warmup,"phase":phase,"samples":samples,"failure":failure,"cancelled":stopped[0],"ownedPids":sorted(owned),
        "remainingOwnedPids":remaining,"cleanupErrors":errors,"runtimeExitCode":process.returncode if process else None,
        "elapsedMs":round((time.monotonic()-start)*1000,3),"logSha256":{
            name:sha256_file(directory/name) if (directory/name).is_file() else None for name in ("runtime.log","requests.jsonl")},
        "referenceLabels":0,"classificationAccuracyMeasured":False,"calibrationRequests":0,"holdoutRequests":0,
        "sloAccepted":False,"representativeAgatTraffic":False,"routingEnabled":False,"qualification":"not_assessed"})
    write_json_new(directory/"result.json",result)
    print(json.dumps({"status":result["status"],"summary":phase["summary"] if phase else None,"failure":failure,"remainingOwnedPids":remaining}))
    return 0 if complete else 1


if __name__=="__main__":raise SystemExit(main())
