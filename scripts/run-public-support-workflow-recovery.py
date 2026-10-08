#!/usr/bin/env python3
"""Crash an owned active HTTP handler, recover its endpoint, retain the whole cohort."""
import argparse
from datetime import datetime, timezone
import hashlib
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
from decision_runtime.model_store import sha256_file, verify_manifest
from scripts.lib.decision_arrival_rate import measure_one
from scripts.lib.decision_performance import profile_from_health
from scripts.lib.decision_public_context import verify_profile
from scripts.lib.decision_public_load import historical_context_sources, validate_context
from scripts.lib.decision_public_sources import pinned_input, private_directory, write_json_new
from scripts.lib.decision_public_workflow import PROFILE_PATH, SOURCE_PATHS, shared_config, verify_inventory
from scripts.lib.decision_public_workflow_loss import ResetGuard
from scripts.lib import decision_public_workflow_recovery as recovery
from scripts.lib.decision_public_workflow_recovery_verification import verify

SPEC = importlib.util.spec_from_file_location("recovery_workflow_launcher", ROOT/"scripts/run-decision-arrival-rate.py")
launcher = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(launcher)
runtime = launcher.runtime
from workers.local_decisions import LocalDecisionClient


def now(): return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("context-profile", "runtime-python", "manifest", "evidence-dir"): parser.add_argument("--"+name, type=Path, required=True)
    parser.add_argument("--context-profile-file-sha256", required=True)
    parser.add_argument("--crash-at-index", type=int, required=True)
    args = parser.parse_args(argv)
    paths = SOURCE_PATHS + recovery.SOURCE_PATHS
    try:
        private = (ROOT/"docs/private").resolve()
        runtime.require(args.evidence_dir.resolve().is_relative_to(private) and args.evidence_dir.resolve() != private
                        and not args.evidence_dir.exists() and not args.evidence_dir.is_symlink(), "Use a new private evidence directory")
        raw = pinned_input(args.context_profile, args.context_profile_file_sha256, 32*1024*1024)
        context = validate_context(parse_json(raw)); historical_context_sources(ROOT, context)
        spec = recovery.recovery_spec(context, args.crash_at_index); config = shared_config(context)
        commit, sources = launcher.frozen_sources(paths)
        profile_raw = pinned_input(ROOT/PROFILE_PATH, context["profileFileSha256"], 1048576)
        profile = parse_json(profile_raw); manifest, _ = verify_manifest(args.manifest.resolve()); verify_profile(profile, manifest)
        runtime.require(sha256_file(args.manifest) == context["manifestFileSha256"] and fingerprint(profile) == context["profileSha256"], "Frozen model/profile differs")
        requirements = dict(line.split("==") for line in (ROOT/"decision_runtime/requirements-mlx.txt").read_text().splitlines() if line and not line.startswith("#"))
        code = 'import importlib.metadata,json,platform,sys;print(json.dumps({"python":platform.python_version(),"machine":platform.machine(),"packages":{k:importlib.metadata.version(k) for k in sys.argv[1:]}}))'
        environment = parse_json(subprocess.check_output([str(args.runtime_python.absolute()), "-B", "-c", code, *requirements], cwd=ROOT, timeout=15))
        runtime.require(environment == context["tokenizerEnvironment"], "Frozen runtime dependencies differ")
        directory = private_directory(ROOT, args.evidence_dir)
        plan = sealed({"schemaVersion": recovery.LAUNCH_PLAN, "runtimeRecovery": spec, "createdAt": now(), "sourceCommit": commit,
            "sourceFiles": sources, "contextProfileFileSha256": args.context_profile_file_sha256, "context": context, "config": config,
            "runtime": environment, "manifestFileSha256": context["manifestFileSha256"], "mode": "serial_closed_model_integration",
            "primary": "fixture_chat_completions", "warmupCount": 4, "ownersAppointed": False, "referenceLabels": 0,
            "sloAccepted": False, "routingEnabled": False, "qualification": "not_assessed"})
        write_json_new(directory/"plan.json", plan); plan_sha = sha256_file(directory/"plan.json")
    except Exception as error:
        print("Cannot prepare crash/recovery workflow: "+type(error).__name__, file=sys.stderr); return 1
    stopped = [False]; previous = {sig: signal.signal(sig, lambda *_: stopped.__setitem__(0, True)) for sig in (signal.SIGINT, signal.SIGTERM)}
    runtimes = []; driver = None; owned = set(); errors = []; samples = []; warmup = []; evidence = None; failure = None
    armed = None; crashed = None; recovered = None; guard = None; old_process = None; process = None
    old_umask = os.umask(0o077); start = time.monotonic()
    elapsed = lambda: round((time.monotonic()-start)*1000, 3)
    try:
        with runtime.open_private_log(directory/"runtime.log") as log, runtime.open_private_log(directory/"runtime-recovered.log") as recovered_log, runtime.open_private_log(directory/"driver.log") as driver_log:
            with socket.socket() as bound: bound.bind(("127.0.0.1", 0)); port = bound.getsockname()[1]
            runtime.require(port != 8766, "Experimental port collides with resident")
            def launch(target_log):
                child = subprocess.Popen([str(args.runtime_python.absolute()), "-B", "-m", "decision_runtime", "serve",
                    "--manifest", str(args.manifest.resolve()), "--policy", str(ROOT/runtime.SHADOW_POLICY), "--max-tokens", "2048",
                    "--cache-limit-mib", "128", "--wired-limit-mib", "4096", "--inference-timeout-ms", "5000",
                    "--exit-on-backend-unavailable", "--port", str(port)], cwd=ROOT,
                    env={**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1"},
                    stdout=target_log, stderr=subprocess.STDOUT, start_new_session=True)
                runtimes.append(child); owned.add(child.pid); deadline = time.monotonic()+spec["startupPerRuntimeMs"]/1000
                while True:
                    runtime.require(child.poll() is None and not stopped[0], "Owned runtime exited or was cancelled")
                    try: health = runtime.request(port, "/health"); break
                    except (OSError, ValueError, http.client.HTTPException):
                        runtime.require(time.monotonic()<deadline, "Owned runtime startup timeout"); time.sleep(.1)
                runtime.require(profile_from_health(health) == profile and health["profileSha256"] == context["profileSha256"], "Owned endpoint has wrong profile")
                return child
            def metrics_raw():
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
                try:
                    connection.request("GET", "/metrics"); response = connection.getresponse(); body = response.read(1048577)
                    runtime.require(response.status == 200 and len(body) <= 1048576, "Metrics unavailable")
                    return body.decode()
                finally: connection.close()
            def sample(child, epoch, label):
                owned.update(runtime.shared.inventory(child.pid)[0]); body = metrics_raw(); health = runtime.request(port, "/health")
                runtime.require(profile_from_health(health) == profile and health["profileSha256"] == context["profileSha256"], "Runtime profile drift")
                values, server_start = recovery.http_metrics(body, in_progress=0)
                entry = {"label": label, "elapsedMs": elapsed(), "capturedAt": now(), "epoch": epoch, "runtimePid": child.pid,
                    "health": health, "metricsRaw": body, "ownedPids": sorted(owned),
                    "processRaw": runtime.command(["ps", "-o", "pid=,ppid=,rss=,lstart=", "-p", ",".join(map(str, sorted(owned)))]),
                    "counters": values, "serverStart": server_start}
                samples.append(entry); return entry
            case = next(row for row in context["inputs"] if row["contextEligible"])
            client = LocalDecisionClient(f"http://127.0.0.1:{port}")
            def warm(epoch):
                for iteration in range(2):
                    runtime.require(not stopped[0], "Warmup was cancelled"); began = elapsed()
                    row = measure_one(client, Request.from_dict(case["request"]), profile, 10000)
                    ended = elapsed()
                    runtime.require(row["status"] in {"ok", "abstain"} and row["observation"]["result"]["inputTokens"] == case["inputTokens"], "Warmup failed")
                    warmup.append({"epoch": epoch, "iteration": iteration, "caseId": case["id"], "startedMs": began, "finishedMs": ended, **row})
            process = old_process = launch(log); first = sample(process, 0, "ready_before_scoring")
            runtime.require(all(value == 0 for value in first["counters"].values()), "Nonzero first runtime origin")
            warm(0); sample(process, 0, "after_warmup")
            env = {key: value for key, value in os.environ.items() if not key.startswith(("AGAT_", "OTEL_"))}
            driver = subprocess.Popen(["node", "--import", "tsx", "scripts/run-public-support-workflow.mts", "--decision-url",
                f"http://127.0.0.1:{port}", "--evidence-dir", str(directory)], cwd=ROOT, env={**env, "OTEL_SDK_DISABLED": "true"},
                stdout=driver_log, stderr=subprocess.STDOUT, start_new_session=True)
            owned.add(driver.pid); deadline = time.monotonic()+spec["driverDeadlineMs"]/1000; observation_deadline = None
            def journal(): return [parse_json(line) for line in (directory/"workflow-routes.jsonl").read_bytes().splitlines()]
            while driver.poll() is None:
                runtime.require(not stopped[0] and time.monotonic()<deadline, "Workflow cancelled or exceeded deadline")
                runtime.require(process.poll() is None if crashed is None or recovered is not None else old_process.poll() == -9, "Unexpected runtime exit")
                if armed is None and (directory/"runtime-crash-request.json").exists():
                    request_raw = (directory/"runtime-crash-request.json").read_bytes(); runtime.require(len(request_raw) <= 65536, "Crash request is excessive")
                    request_sha = hashlib.sha256(request_raw).hexdigest(); request = parse_json(request_raw)
                    recovery.validate_request(context, spec, request, journal()); sample(process, 0, "before_crash_armed")
                    armed = sealed({"schemaVersion": recovery.ARMED_SCHEMA, "targetIndex": spec["targetIndex"], "requestFileSha256": request_sha,
                        "runtimePid": process.pid, "port": port, "profileSha256": context["profileSha256"], "armedAt": now()})
                    recovery.publish(directory, "runtime-crash-armed.json", armed); observation_deadline = time.monotonic()+spec["observeDeadlineMs"]/1000
                elif armed is not None and crashed is None:
                    runtime.require(time.monotonic() < observation_deadline, "Declared target never exposed an active HTTP handler")
                    if (directory/"runtime-crash-started.json").exists():
                        started_raw = (directory/"runtime-crash-started.json").read_bytes(); runtime.require(len(started_raw) <= 65536, "Started receipt excessive")
                        body = metrics_raw(); captured_at = now(); captured_ms = elapsed()
                        try: values, server_start = recovery.http_metrics(body, in_progress=1)
                        except ValueError:
                            values, server_start = recovery.http_metrics(body, in_progress=0)
                            runtime.require(values == samples[2]["counters"], "Target completed before the declared crash trigger")
                            time.sleep(spec["pollMs"]/1000); continue
                        runtime.require(values == samples[2]["counters"] and server_start == samples[2]["serverStart"], "Active handler crossed the completed-prefix epoch")
                        snapshot = {"schemaVersion": recovery.SNAPSHOT_SCHEMA, "capturedAt": captured_at, "elapsedMs": captured_ms,
                            "runtimePid": process.pid, "profileSha256": context["profileSha256"], "metricsRaw": body, "counters": values, "serverStart": server_start}
                        write_json_new(directory/"runtime-crash-inflight.json", snapshot)
                        crash_receipt = recovery.crash_owned(process, port, runtime, owned, errors)
                        runtime.require(crash_receipt["signalSentAtEpoch"]-datetime.fromisoformat(captured_at.replace("Z", "+00:00")).timestamp() <= spec["sampleToSignalMaxMs"]/1000,
                                        "SIGKILL missed the active HTTP snapshot budget")
                        guard = ResetGuard(port)
                        crashed = sealed({"schemaVersion": recovery.CRASH_SCHEMA, "targetIndex": spec["targetIndex"], "requestFileSha256": request_sha,
                            "startedFileSha256": hashlib.sha256(started_raw).hexdigest(), "snapshotFileSha256": sha256_file(directory/"runtime-crash-inflight.json"),
                            "port": port, "profileSha256": context["profileSha256"], **crash_receipt, "appliedAt": now()})
                        recovery.publish(directory, "runtime-crash-applied.json", crashed)
                elif crashed is not None and recovered is None and (directory/"runtime-recovery-request.json").exists():
                    recovery_raw = (directory/"runtime-recovery-request.json").read_bytes(); runtime.require(len(recovery_raw) <= 65536, "Recovery request excessive")
                    recovery_request_sha = hashlib.sha256(recovery_raw).hexdigest()
                    recovery.validate_recovery_request(context, spec, parse_json(recovery_raw), journal())
                    guard.close(); transport = guard.receipt(); guard = None
                    runtime.require(transport["acceptedConnections"] == transport["resetConnections"] == 0 and not transport["errors"], "Unexpected endpoint calls during pause")
                    write_json_new(directory/"runtime-recovery-transport.json", transport)
                    process = launch(recovered_log); fresh = sample(process, 1, "recovered_before_scoring")
                    runtime.require(all(value == 0 for value in fresh["counters"].values()) and fresh["serverStart"] > first["serverStart"], "Replacement counter origin differs")
                    warm(1); sample(process, 1, "recovered_after_warmup")
                    recovered = sealed({"schemaVersion": recovery.RECOVERED_SCHEMA, "targetIndex": spec["targetIndex"], "requestFileSha256": recovery_request_sha,
                        "crashSealSha256": crashed["sha256"], "runtimePid": process.pid, "port": port, "profileSha256": context["profileSha256"],
                        "readyServerStart": fresh["serverStart"], "warmupCount": 2, "appliedAt": now()})
                    recovery.publish(directory, "runtime-recovery-applied.json", recovered)
                owned.update(runtime.shared.inventory(driver.pid)[0]); time.sleep(spec["pollMs"]/1000)
            runtime.require(driver.returncode == 0 and armed is not None and crashed is not None and recovered is not None, "Workflow driver or crash/recovery failed")
            recipe = parse_json((directory/"workflow-plan.json").read_bytes()); cohort = parse_json((directory/"cohort.http.json").read_bytes())
            observed = parse_json((directory/"workflow-driver.json").read_bytes()); owned.update(observed["ownedPids"])
            runtime.require(fingerprint(recipe.get("runtimeRecovery")) == fingerprint(spec), "Recipe changed prospective recovery")
            evidence = verify_inventory(context, recipe, cohort, observed["routes"])
            runtime.require(observed["status"] == "observed" and observed["primaryCalls"] == len(context["inputs"])
                and observed["workerExitCode"] == 0 and observed["unauthenticatedStatus"] == 401 and observed["authenticatedStatus"] == 200,
                "Driver audit failed")
            artifacts = {name: (directory/name).read_bytes() for name in recovery.ARTIFACTS}
            receipts = recovery.verify_boundary(context, spec, artifacts, cohort, observed["routes"], owned)
            runtime.require(fingerprint(observed["runtimeRecovery"]) == fingerprint({key: receipts[key] for key in ("armed", "crashed", "recovered")}), "Driver did not acknowledge exact barriers")
            sample(process, 1, "after_inventory")
            runtime.require(launcher.frozen_sources(paths) == (commit, sources)
                and pinned_input(args.context_profile, args.context_profile_file_sha256, 32*1024*1024) == raw
                and sha256_file(directory/"plan.json") == plan_sha and pinned_input(ROOT/PROFILE_PATH, context["profileFileSha256"], 1048576) == profile_raw
                and sha256_file(args.manifest) == context["manifestFileSha256"] and verify_manifest(args.manifest)[0] == manifest,
                "Sources, context, profile or model changed")
    except Exception as error:
        failure = {"type": type(error).__name__, "reason": str(error)[:200]}
    finally:
        if driver is not None and driver.poll() is None: runtime.stop_owned_process(driver, owned, errors)
        for child in reversed(runtimes):
            if child.poll() is None: runtime.stop_owned_process(child, owned, errors)
        if guard is not None:
            try: guard.close()
            except Exception as error: errors.append("guardStop:"+type(error).__name__)
        remaining = runtime.remaining_owned_processes(owned, errors)
        for sig, handler in previous.items(): signal.signal(sig, handler)
        os.umask(old_umask)
    complete = evidence is not None and failure is None and not errors and remaining == [] and not stopped[0]
    names = {"workflow-plan.json", "workflow-driver.json", "workflow-routes.jsonl", "cohort.http.json", "worker.log", "runtime.log", "driver.log"} | recovery.ARTIFACTS
    result = sealed({"schemaVersion": recovery.LAUNCH_RESULT, "status": "observed" if complete else "failed", "planSha256": plan["sha256"],
        "runtimeRecovery": {"spec": spec, "armed": armed, "crashed": crashed, "recovered": recovered}, "evidence": evidence,
        "warmup": warmup, "samples": samples, "failure": failure, "ownedPids": sorted(owned), "remainingOwnedPids": remaining, "cleanupErrors": errors,
        "runtimeExitCodes": [child.returncode for child in runtimes], "driverExitCode": driver.returncode if driver else None,
        "artifactSha256": {name: sha256_file(directory/name) if (directory/name).exists() else None for name in sorted(names)},
        "elapsedMs": elapsed(), "referenceLabels": 0, "classificationAccuracyMeasured": False,
        "ownersAppointed": False, "sloAccepted": False, "routingEnabled": False, "qualification": "not_assessed"})
    write_json_new(directory/"result.json", result)
    if complete:
        try:
            report = verify(ROOT, directory, args.context_profile, context_sha=args.context_profile_file_sha256,
                            plan_sha=plan_sha, result_sha=sha256_file(directory/"result.json"))
            runtime.require(launcher.frozen_sources(paths) == (commit, sources), "Verification source drift")
            write_json_new(directory/"verification.json", report)
        except Exception as error:
            complete = False; write_json_new(directory/"verification-failure.json", {"type": type(error).__name__, "reason": str(error)[:200]})
    print(json.dumps({"status": "observed" if complete else "failed", "evidence": evidence, "failure": failure, "remainingOwnedPids": remaining}))
    return 0 if complete else 1


if __name__ == "__main__": raise SystemExit(main())
