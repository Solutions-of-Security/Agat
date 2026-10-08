#!/usr/bin/env python3
"""Own a temporary runtime and exercise every public case through a real worker."""
import argparse
from datetime import datetime, timezone
import http.client
import hashlib
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
from decision_runtime.model_store import sha256_file, verify_manifest
from decision_runtime.metrics import OUTCOMES
from scripts.lib.decision_arrival_rate import measure_one
from scripts.lib.decision_performance import profile_from_health
from scripts.lib.decision_public_context import verify_profile
from scripts.lib.decision_public_load import historical_context_sources, validate_context
from scripts.lib.decision_public_load_verification import counters
from scripts.lib.decision_public_sources import pinned_input, private_directory, write_json_new
from scripts.lib.decision_public_workflow import PROFILE_PATH, SOURCE_PATHS, shared_config, verify_inventory
from scripts.lib.decision_public_workflow_loss import loss_spec, validate_request, verify_boundary, APPLIED_SCHEMA, stop_and_reserve, publish_applied
from workers.local_decisions import LocalDecisionClient

SPEC = importlib.util.spec_from_file_location("public_workflow_sources", ROOT/"scripts/run-decision-arrival-rate.py")
launcher = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(launcher)
runtime = launcher.runtime


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("context-profile", "runtime-python", "manifest", "evidence-dir"):
        parser.add_argument("--"+name, type=Path, required=True)
    parser.add_argument("--context-profile-file-sha256", required=True)
    parser.add_argument("--stop-runtime-before-index", type=int, default=None, help="Prospectively stop only this launcher-owned runtime before this zero-based case")
    args = parser.parse_args(argv)
    try:
        private = (ROOT/"docs/private").resolve()
        runtime.require(args.evidence_dir.resolve().is_relative_to(private) and args.evidence_dir.resolve() != private
                        and not args.evidence_dir.exists() and not args.evidence_dir.is_symlink(), "Use a new private evidence directory")
        raw = pinned_input(args.context_profile, args.context_profile_file_sha256, 32*1024*1024)
        context = validate_context(parse_json(raw)); historical_context_sources(ROOT, context); config = shared_config(context)
        loss = loss_spec(context, args.stop_runtime_before_index) if args.stop_runtime_before_index is not None else None
        source_paths = SOURCE_PATHS + (["scripts/test/test_decision_public_workflow_loss.py"] if loss else [])
        commit, sources = launcher.frozen_sources(source_paths)
        profile_raw = pinned_input(ROOT/PROFILE_PATH, context["profileFileSha256"], 1024*1024)
        profile = parse_json(profile_raw); manifest, _ = verify_manifest(args.manifest.resolve()); verify_profile(profile, manifest)
        runtime.require(sha256_file(args.manifest) == context["manifestFileSha256"] and fingerprint(profile) == context["profileSha256"], "Frozen model/profile differs")
        requirements = dict(line.split("==") for line in (ROOT/"decision_runtime/requirements-mlx.txt").read_text().splitlines() if line and not line.startswith("#"))
        code = 'import importlib.metadata,json,platform,sys;print(json.dumps({"python":platform.python_version(),"machine":platform.machine(),"packages":{k:importlib.metadata.version(k) for k in sys.argv[1:]}}))'
        environment = parse_json(subprocess.check_output([str(args.runtime_python.absolute()), "-B", "-c", code, *requirements], cwd=ROOT, timeout=15))
        runtime.require(environment == context["tokenizerEnvironment"], "Frozen runtime dependencies differ")
        directory = private_directory(ROOT, args.evidence_dir)
        plan = sealed({"schemaVersion": "agat.decision.public-workflow-launch-plan.v2" if loss else "agat.decision.public-workflow-launch-plan.v1", **({"runtimeLoss": loss} if loss else {}), "createdAt": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "sourceCommit": commit, "sourceFiles": sources, "contextProfileFileSha256": args.context_profile_file_sha256,
            "context": context, "config": config, "runtime": environment, "manifestFileSha256": context["manifestFileSha256"],
            "mode": "serial_closed_model_integration", "primary": "fixture_chat_completions", "warmupCount": 2,
            "ownersAppointed": False, "referenceLabels": 0, "sloAccepted": False, "routingEnabled": False, "qualification": "not_assessed"})
        write_json_new(directory/"plan.json", plan)
        plan_file_sha = sha256_file(directory/"plan.json")
    except Exception as error:
        print("Cannot prepare public workflow: "+type(error).__name__, file=sys.stderr); return 1
    stopped = [False]; previous = {sig: signal.signal(sig, lambda *_: stopped.__setitem__(0, True)) for sig in (signal.SIGINT, signal.SIGTERM)}
    process = None; driver = None; owned = set(); errors = []; samples = []; warmup = []; evidence = None; failure = None
    loss_applied = None; reservation = None; reservation_closed = False
    old_umask = os.umask(0o077); start = time.monotonic()
    try:
        with runtime.open_private_log(directory/"runtime.log") as log, runtime.open_private_log(directory/"driver.log") as driver_log:
            with socket.socket() as bound: bound.bind(("127.0.0.1", 0)); port = bound.getsockname()[1]
            process = subprocess.Popen([str(args.runtime_python.absolute()), "-B", "-m", "decision_runtime", "serve",
                "--manifest", str(args.manifest.resolve()), "--policy", str(ROOT/runtime.SHADOW_POLICY), "--max-tokens", "2048",
                "--cache-limit-mib", "128", "--wired-limit-mib", "4096", "--inference-timeout-ms", "5000",
                "--exit-on-backend-unavailable", "--port", str(port)], cwd=ROOT,
                env={**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1"},
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            owned.add(process.pid); deadline = time.monotonic()+60
            while True:
                runtime.require(process.poll() is None and not stopped[0], "Owned runtime exited or was cancelled")
                try: health = runtime.request(port, "/health"); break
                except (OSError, ValueError, http.client.HTTPException):
                    runtime.require(time.monotonic()<deadline, "Owned runtime startup timeout"); time.sleep(.1)
            runtime.require(profile_from_health(health) == profile and health["profileSha256"] == context["profileSha256"], "Wrong owned runtime profile")
            def sample(label):
                owned.update(runtime.shared.inventory(process.pid)[0])
                c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                try:
                    c.request("GET", "/metrics"); response = c.getresponse(); body = response.read(1048577)
                    runtime.require(response.status == 200 and len(body)<=1048576, "Metrics unavailable")
                finally: c.close()
                current = runtime.request(port, "/health")
                runtime.require(profile_from_health(current) == profile, "Runtime profile drift")
                entry = {"label": label, "elapsedMs": round((time.monotonic()-start)*1000, 3), "health": current,
                         "metricsRaw": body.decode(), "ownedPids": sorted(owned),
                         "processRaw": runtime.command(["ps", "-o", "pid=,ppid=,rss=,lstart=", "-p", ",".join(map(str, sorted(owned)))])}
                values, server_start = counters(entry)
                samples.append({**entry, "counters": values, "serverStart": server_start})
            sample("ready_before_scoring")
            runtime.require(all(value == 0 for value in samples[0]["counters"].values()), "Owned runtime has earlier calls")
            case = next(row for row in context["inputs"] if row["contextEligible"])
            client = LocalDecisionClient(f"http://127.0.0.1:{port}")
            for iteration in range(2):
                row = measure_one(client, Request.from_dict(case["request"]), profile, 10000)
                runtime.require(row["status"] in {"ok", "abstain"} and row["observation"]["result"]["inputTokens"] == case["inputTokens"], "Warmup failed")
                warmup.append({"iteration": iteration, "caseId": case["id"], **row})
            sample("after_warmup")
            runtime.require(sum(samples[1]["counters"].values()) == 2, "Warmup physical calls differ")
            env = {key: value for key, value in os.environ.items() if not key.startswith(("AGAT_", "OTEL_"))}
            driver = subprocess.Popen(["node", "--import", "tsx", "scripts/run-public-support-workflow.mts", "--decision-url",
                f"http://127.0.0.1:{port}", "--evidence-dir", str(directory)], cwd=ROOT, env={**env, "OTEL_SDK_DISABLED": "true"},
                stdout=driver_log, stderr=subprocess.STDOUT, start_new_session=True)
            owned.add(driver.pid); deadline = time.monotonic()+270
            while driver.poll() is None:
                owned.update(runtime.shared.inventory(driver.pid)[0])
                runtime.require((process.poll() is None or loss_applied is not None) and not stopped[0] and time.monotonic()<deadline, "Workflow child exited, cancelled or exceeded deadline")
                if loss and loss_applied is None and (directory/"runtime-loss-request.json").exists():
                    request_raw = (directory/"runtime-loss-request.json").read_bytes()
                    runtime.require(len(request_raw) <= 65536, "Loss request is excessive")
                    request_sha = hashlib.sha256(request_raw).hexdigest()
                    request = parse_json(request_raw)
                    prefix = [parse_json(line) for line in (directory/"workflow-routes.jsonl").read_bytes().splitlines()]
                    validate_request(context, loss, request, prefix)
                    sample("before_runtime_loss")
                    reservation = stop_and_reserve(process, port, runtime, owned, errors)
                    loss_applied = sealed({"schemaVersion": APPLIED_SCHEMA, "beforeIndex": loss["beforeIndex"],
                        "requestFileSha256": request_sha, "runtimePid": process.pid,
                        "runtimeExitCode": process.returncode, "runtimeExited": True, "endpointGuardedWithTcpReset": True,
                        "appliedAt": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")})
                    runtime.require(pinned_input(directory/"runtime-loss-request.json", request_sha, 65536) == request_raw, "Loss request changed")
                    publish_applied(directory, loss_applied)
                time.sleep(.1)
            runtime.require(driver.returncode == 0, "Workflow driver failed")
            recipe = parse_json((directory/"workflow-plan.json").read_bytes())
            cohort = parse_json((directory/"cohort.http.json").read_bytes())
            observed = parse_json((directory/"workflow-driver.json").read_bytes())
            owned.update(observed["ownedPids"])
            runtime.require((recipe.get("runtimeLoss") is None and loss is None) or fingerprint(recipe.get("runtimeLoss")) == fingerprint(loss), "Recipe changed the prospective loss")
            evidence = verify_inventory(context, recipe, cohort, observed["routes"])
            runtime.require(observed["status"] == "observed" and observed["primaryCalls"] == len(context["inputs"])
                            and observed["workerExitCode"] == 0 and observed["unauthenticatedStatus"] == 401, "Driver audit failed")
            if loss:
                runtime.require(loss_applied is not None and fingerprint(observed.get("runtimeLossApplied")) == fingerprint(loss_applied), "Driver did not acknowledge the owned loss")
                verify_boundary(context, loss, request, loss_applied, cohort, observed["routes"])
                reservation.close(); reservation_closed = True
                transport = reservation.receipt()
                runtime.require(transport["acceptedConnections"] == transport["resetConnections"] == evidence["unavailableReturns"]
                                and not transport["errors"], "TCP resets differ from durable unavailability")
                runtime.require(pinned_input(directory/"runtime-loss-request.json", request_sha, 65536) == request_raw
                                and fingerprint(parse_json((directory/"runtime-loss-applied.json").read_bytes())) == fingerprint(loss_applied), "Loss receipts changed")
                write_json_new(directory/"runtime-loss-transport.json", transport)
            else: sample("after_inventory")
            runtime.require(len({entry["serverStart"] for entry in samples}) == 1, "Runtime counters restarted")
            delta = {key: samples[2]["counters"][key]-samples[1]["counters"][key] for key in OUTCOMES}
            runtime.require(all(delta[key] == evidence["physicalScheduledOutcomes"].get(key, 0) for key in OUTCOMES), "Physical handlers differ from durable cohort")
            runtime.require(launcher.frozen_sources(source_paths) == (commit, sources)
                and pinned_input(args.context_profile, args.context_profile_file_sha256, 32*1024*1024) == raw
                and sha256_file(directory/"plan.json") == plan_file_sha
                and pinned_input(ROOT/PROFILE_PATH, context["profileFileSha256"], 1024*1024) == profile_raw
                and sha256_file(args.manifest) == context["manifestFileSha256"] and verify_manifest(args.manifest)[0] == manifest,
                "Sources, context or model changed")
    except Exception as error:
        failure = {"type": type(error).__name__, "reason": str(error)[:200]}
    finally:
        if driver is not None: runtime.stop_owned_process(driver, owned, errors)
        if process is not None and process.poll() is None: runtime.stop_owned_process(process, owned, errors)
        if reservation is not None and not reservation_closed:
            try: reservation.close()
            except Exception as error: errors.append("transportGuardStop:"+type(error).__name__)
        remaining = runtime.remaining_owned_processes(owned, errors)
        for sig, handler in previous.items(): signal.signal(sig, handler)
        os.umask(old_umask)
    complete = evidence is not None and failure is None and not errors and not remaining and not stopped[0]
    artifacts = ("workflow-plan.json", "workflow-driver.json", "workflow-routes.jsonl", "cohort.http.json", "worker.log", "runtime.log", "driver.log")
    if loss: artifacts += ("runtime-loss-request.json", "runtime-loss-applied.json", "runtime-loss-transport.json")
    result = sealed({"schemaVersion": "agat.decision.public-workflow-launch-result.v2" if loss else "agat.decision.public-workflow-launch-result.v1",
        **({"runtimeLoss": {"spec": loss, "applied": loss_applied}} if loss else {}), "status": "observed" if complete else "failed",
        "planSha256": plan["sha256"], "evidence": evidence, "warmup": warmup, "samples": samples, "failure": failure,
        "ownedPids": sorted(owned), "remainingOwnedPids": remaining, "cleanupErrors": errors,
        "runtimeExitCode": process.returncode if process else None, "driverExitCode": driver.returncode if driver else None,
        "artifactSha256": {name: sha256_file(directory/name) if (directory/name).exists() else None for name in artifacts},
        "elapsedMs": round((time.monotonic()-start)*1000, 3), "referenceLabels": 0, "classificationAccuracyMeasured": False,
        "ownersAppointed": False, "sloAccepted": False, "routingEnabled": False, "qualification": "not_assessed"})
    write_json_new(directory/"result.json", result)
    print(json.dumps({"status": result["status"], "evidence": evidence, "failure": failure, "remainingOwnedPids": remaining}))
    return 0 if complete else 1


if __name__ == "__main__": raise SystemExit(main())
