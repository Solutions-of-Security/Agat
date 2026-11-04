#!/usr/bin/env python3
"""Observe every frozen public option-order variant on one owned wired runtime."""
import argparse
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
from decision_runtime.contracts import Request, parse_json
from decision_runtime.model_store import verify_manifest, sha256_file
from scripts.lib.decision_arrival_rate import measure_one
from scripts.lib.decision_performance import profile_from_health
from scripts.lib.decision_public_context import verify_profile
from scripts.lib.decision_public_load import validate_context
from scripts.lib.decision_public_load_verification import CONTEXT_PATHS, sources_at
from scripts.lib.decision_public_permutations import SOURCE_PATHS, BUDGET, validate_profile
from scripts.lib.decision_public_permutation_diagnostic import PLAN_SCHEMA, RESULT_SCHEMA, PATHS, PROFILE_PATH, AUTHORITY, run_phase, verify, verify_phase
from scripts.lib.decision_public_sources import pinned_input, private_directory, write_json_new
from scripts.lib.decision_shadow_pilot import require, utc_now
from workers.local_decisions import LocalDecisionClient

SPEC = importlib.util.spec_from_file_location("permutation_arrival_launcher", ROOT/"scripts/run-decision-arrival-rate.py")
launcher = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(launcher)
runtime = launcher.runtime


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("context-profile", "permutation-context", "runtime-python", "manifest", "evidence-dir"):
        parser.add_argument("--"+name, type=Path, required=True)
    for name in ("context-profile-file-sha256", "permutation-context-file-sha256"):
        parser.add_argument("--"+name, required=True)
    args = parser.parse_args(argv)
    try:
        private = (ROOT/"docs/private").resolve()
        require(args.evidence_dir.resolve().is_relative_to(private) and args.evidence_dir.resolve() != private
                and not args.evidence_dir.exists() and not args.evidence_dir.is_symlink(), "Use a new private evidence directory")
        context_raw = pinned_input(args.context_profile,args.context_profile_file_sha256,32*1024*1024)
        permutation_raw = pinned_input(args.permutation_context,args.permutation_context_file_sha256,32*1024*1024)
        context = validate_context(parse_json(context_raw))
        permutation = validate_profile(parse_json(permutation_raw),context,args.context_profile_file_sha256)
        sources_at(ROOT,context["sourceCommit"],context["sourceFiles"],CONTEXT_PATHS)
        sources_at(ROOT,permutation["sourceCommit"],permutation["sourceFiles"],SOURCE_PATHS)
        profile_raw = pinned_input(ROOT/PROFILE_PATH,context["profileFileSha256"],1024*1024)
        profile = parse_json(profile_raw); require(profile==context["profile"],"Frozen serving profile differs")
        commit,sources = launcher.frozen_sources(PATHS)
        runtime.require_resident_shadow_model(args.manifest.resolve())
        manifest,_ = verify_manifest(args.manifest.resolve()); verify_profile(profile,manifest)
        require(sha256_file(args.manifest)==context["manifestFileSha256"],"Manifest differs from tokenized inputs")
        requirements = dict(line.split("==") for line in (ROOT/"decision_runtime/requirements-mlx.txt").read_text().splitlines() if line and not line.startswith("#"))
        code = 'import importlib.metadata,json,platform,sys;print(json.dumps({"python":platform.python_version(),"machine":platform.machine(),"packages":{k:importlib.metadata.version(k) for k in sys.argv[1:]}}))'
        environment = parse_json(subprocess.check_output([str(args.runtime_python.absolute()),"-B","-c",code,*requirements],cwd=ROOT,timeout=15))
        require(environment==context["tokenizerEnvironment"],"Pinned runtime environment differs")
        directory = private_directory(ROOT,args.evidence_dir)
        plan = sealed({"schemaVersion":PLAN_SCHEMA,"createdAt":utc_now(),"sourceCommit":commit,"sourceFiles":sources,
            "contextProfileFileSha256":args.context_profile_file_sha256,"contextProfileSealSha256":context["sha256"],
            "permutationContextFileSha256":args.permutation_context_file_sha256,"permutationContextSealSha256":permutation["sha256"],
            "profile":profile,**{key:context[key] for key in ("profileFileSha256","profileSha256","manifestFileSha256","model")},
            "runtime":environment,"inputs":permutation["inputs"],"budget":dict(BUDGET),
            "scope":"whole_unlabelled_public_development_option_order_diagnostic","backgroundWorkloadControlled":False,**AUTHORITY})
        write_json_new(directory/"plan.json",plan)
    except Exception as error:
        print("Cannot prepare option-order diagnostic: "+type(error).__name__,file=sys.stderr); return 1
    process = None; owned = set(); errors = []; samples = []; warmup = []; phase = None; failure = None
    stopped = [False]; start = time.monotonic()
    previous = {sig:signal.signal(sig,lambda *_:stopped.__setitem__(0,True)) for sig in (signal.SIGINT,signal.SIGTERM)}
    try:
        with runtime.open_private_log(directory/"runtime.log") as log, runtime.open_private_log(directory/"requests.jsonl") as journal:
            with socket.socket() as bound:
                bound.bind(("127.0.0.1",0)); port = bound.getsockname()[1]
            require(port!=8766,"Experimental port cannot target resident")
            process = subprocess.Popen([str(args.runtime_python.absolute()),"-B","-m","decision_runtime","serve",
                "--manifest",str(args.manifest.resolve()),"--policy",str(ROOT/runtime.SHADOW_POLICY),"--max-tokens","2048",
                "--cache-limit-mib","128","--wired-limit-mib","4096","--inference-timeout-ms","5000",
                "--exit-on-backend-unavailable","--port",str(port)],cwd=ROOT,
                env={**os.environ,"HF_HUB_OFFLINE":"1","TRANSFORMERS_OFFLINE":"1","HF_HUB_DISABLE_TELEMETRY":"1"},
                stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            owned.add(process.pid); deadline = time.monotonic()+60
            while True:
                require(process.poll() is None and not stopped[0],"Owned runtime exited or startup cancelled")
                try:health = runtime.request(port,"/health"); break
                except (OSError,ValueError,http.client.HTTPException):
                    require(time.monotonic()<deadline,"Runtime startup timeout");time.sleep(.1)
            require(profile_from_health(health)==profile and health["profileSha256"]==context["profileSha256"],"Ready profile differs")
            client = LocalDecisionClient(f"http://127.0.0.1:{port}")
            def sample(label):
                require(process.poll() is None,"Owned runtime exited during diagnostic")
                owned.update(runtime.shared.inventory(process.pid)[0])
                connection = http.client.HTTPConnection("127.0.0.1",port,timeout=5)
                try:
                    connection.request("GET","/metrics");response=connection.getresponse();raw=response.read(1048577)
                    require(response.status==200 and len(raw)<=1048576,"Metrics failed")
                finally:connection.close()
                health=runtime.request(port,"/health"); require(profile_from_health(health)==profile,"Runtime profile drift")
                samples.append({"label":label,"elapsedMs":round((time.monotonic()-start)*1000,3),"health":health,
                    "metricsRaw":raw.decode(),"ownedPids":sorted(owned),
                    "processRaw":runtime.command(["ps","-o","pid=,ppid=,rss=,lstart=","-p",",".join(map(str,sorted(owned)))])})
            sample("ready_before_scoring")
            first = next(case for case in plan["inputs"] if case["contextEligible"])
            for iteration in range(2):
                require(not stopped[0],"Warmup cancelled")
                row=measure_one(client,Request.from_dict(first["request"]),profile,BUDGET["callerTimeoutMs"])
                warmup.append({"variantId":first["id"],"inputSha256":first["inputSha256"],"inputTokens":first["inputTokens"],"iteration":iteration,**row})
                require(row["status"] in {"ok","abstain"} and row["observation"]["result"].get("inputTokens")==first["inputTokens"],"Warmup failed")
            sample("after_warmup")
            def record(row):
                journal.write((json.dumps(row,ensure_ascii=False,allow_nan=False)+"\n").encode());journal.flush()
            phase=run_phase(client,plan["inputs"],profile,cancelled=lambda:stopped[0] or process.poll() is not None,journal=record)
            write_json_new(directory/"phase.json",sealed(phase));sample("after_inventory")
            verify_phase(phase,plan["inputs"],profile)
            require(launcher.frozen_sources(PATHS)==(commit,sources),"Diagnostic sources changed")
            require(pinned_input(args.context_profile,args.context_profile_file_sha256,32*1024*1024)==context_raw
                    and pinned_input(args.permutation_context,args.permutation_context_file_sha256,32*1024*1024)==permutation_raw,"Input receipts changed")
            require(pinned_input(ROOT/PROFILE_PATH,context["profileFileSha256"],1024*1024)==profile_raw
                    and sha256_file(args.manifest)==context["manifestFileSha256"] and verify_manifest(args.manifest)[0]==manifest,"Model/profile artifacts changed")
    except Exception as error:
        failure={"type":type(error).__name__,"reason":str(error)[:300]}
    finally:
        if process is not None:runtime.stop_owned_process(process,owned,errors)
        remaining=runtime.remaining_owned_processes(owned,errors)
        for sig,handler in previous.items():signal.signal(sig,handler)
    complete=phase is not None and phase["complete"] is True and failure is None and not stopped[0] and not errors and remaining==[]
    result=sealed({"schemaVersion":RESULT_SCHEMA,"status":"observed" if complete else "failed","planSha256":plan["sha256"],
        "warmup":warmup,"phase":phase,"samples":samples,"failure":failure,"cancelled":stopped[0],"ownedPids":sorted(owned),
        "remainingOwnedPids":remaining,"cleanupErrors":errors,"runtimeExitCode":process.returncode if process else None,
        "elapsedMs":round((time.monotonic()-start)*1000,3),
        "logSha256":{name:sha256_file(directory/name) if (directory/name).is_file() else None for name in ("runtime.log","requests.jsonl","phase.json")},**AUTHORITY})
    write_json_new(directory/"result.json",result)
    if complete:
        try:
            verification=verify(ROOT,directory,args.context_profile,args.permutation_context,
                context_sha=args.context_profile_file_sha256,permutation_sha=args.permutation_context_file_sha256,
                plan_sha=sha256_file(directory/"plan.json"),result_sha=sha256_file(directory/"result.json"))
            require(launcher.frozen_sources(PATHS)==(commit,sources),"Verification sources changed")
            write_json_new(directory/"verification.json",verification)
        except Exception as error:
            print("Option-order receipt verification failed: "+type(error).__name__,file=sys.stderr);return 1
    print(json.dumps({"status":result["status"],"summary":{key:value for key,value in phase["summary"].items() if key!="cases"} if phase else None,
                      "failure":failure,"remainingOwnedPids":remaining}));return 0 if complete else 1


if __name__=="__main__":raise SystemExit(main())
