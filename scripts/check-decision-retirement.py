#!/usr/bin/env python3
"""Verify retirement events with four owned real MLX runtimes; keep evidence local."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import importlib.util
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_runtime.artifacts import read_json, sealed, write_new
from decision_runtime.contracts import Request, canonical_json, fingerprint, parse_json

spec = importlib.util.spec_from_file_location("service_recovery", ROOT / "scripts/check-decision-service-recovery.py")
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)
ensure = recovery.ensure


def verify_event(raw, profile, child_pid, reason, child_exit=None):
    ensure(len(raw) <= 512 and len(raw.splitlines()) == 1, "Retirement log is not one bounded line")
    event = parse_json(raw)
    expected = {"schemaVersion": "agat.decision.retirement.v1", "eventName": "decision.backend_retired",
                "runtimeVersion": profile["runtimeVersion"], "profileSha256": fingerprint(profile),
                "exitCode": 75, "reason": reason, "childPid": child_pid}
    ensure(type(event) is dict and set(event) == set(expected) | {"childExitCode"}, "Unexpected retirement fields")
    ensure(all(type(event[key]) is type(value) and event[key] == value for key, value in expected.items()),
           "Retirement event does not match the owned runtime")
    ensure(type(event["childExitCode"]) is int and -255 <= event["childExitCode"] <= 255,
           "Inference child was not reaped")
    if child_exit is not None:
        ensure(event["childExitCode"] == child_exit, "Unexpected child exit code")
    return event


def finish(runtime, directory, reason, child_exit=None):
    runtime.failed_exit()
    child_pid = next(row["pid"] for row in runtime.children if row["role"] == "inference")
    event = verify_event((directory / f'{runtime.record["name"]}.stderr').read_bytes(),
                         runtime.profile, child_pid, reason, child_exit)
    runtime.record["retirementEvent"] = event


def cancel(runtime, request):
    body = canonical_json(request.to_dict()).encode("utf-8")
    headers = (f"POST /v1/decisions HTTP/1.1\r\nHost: 127.0.0.1:{runtime.port}\r\n"
               f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n"
               f"X-Agat-Decision-Profile: {fingerprint(runtime.profile)}\r\n"
               "X-Agat-Decision-Cancel-On-Disconnect: 1\r\n\r\n").encode("ascii")
    with socket.create_connection(("127.0.0.1", runtime.port), timeout=5) as connection:
        connection.sendall(headers + body)
        # A completed or pre-dispatch-cancelled request must fail this experiment;
        # only an inflight cancellation retires the child and produces exit 75.
        time.sleep(.05)
        connection.shutdown(socket.SHUT_WR)
        response = http.client.HTTPResponse(connection); response.begin(); raw = response.read(131073)
        ensure(len(raw) <= 131072 and response.getheader("Content-Length") == str(len(raw)), "Incomplete cancellation response")
        result = parse_json(raw)
        ensure(response.status == 500 and result["reason"] == "inference_cancelled"
               and result["status"] == "error" and result["inputSha256"] == request.input_sha256
               and result["value"] is None and result["selectedOptionId"] is None and result["distribution"] == []
               and all(result[key] == value for key, value in runtime.profile.items()), "Cancellation was not confirmed")
        return {"httpStatus": response.status, "completeResponse": True, "result": result}


def source_hashes():
    paths = sorted((ROOT / "decision_runtime").glob("*.py")) + [
        Path(__file__).resolve(), ROOT / "scripts/check-decision-service-recovery.py",
        ROOT / "scripts/lib/decision_performance.py", ROOT / "scripts/lib/decision_baselines.py",
        ROOT / "workers/local_decisions.py"]
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def run_scenarios(args, request, directory, owned):
    first = recovery.OwnedRuntime(args, directory, "idle_failure", 5000); owned.append(first)
    baseline = first.score(request); ensure(baseline["httpStatus"] == 200, "Baseline inference failed")
    first.record["beforeFailure"] = baseline
    child = next(row for row in first.children if row["role"] == "inference")
    ensure(first.process.poll() is None and child in recovery.child_processes(first.process.pid), "Owned child changed")
    os.kill(child["pid"], signal.SIGKILL)
    finish(first, directory, "backend_unavailable", -signal.SIGKILL)
    print("Idle child loss: verified event, exit 75 and cleanup", flush=True)

    second = recovery.OwnedRuntime(args, directory, "explicit_restart", 5000, first.port); owned.append(second)
    ensure(second.profile == first.profile, "Restart changed runtime profile")
    after = second.score(request); ensure(after["httpStatus"] == 200, "Restart inference failed")
    stable = ("status", "reason", "selectedOptionId", "value", "distribution", "inputTokens", "generatedTokens")
    ensure(all(after["result"][key] == baseline["result"][key] for key in stable), "Restart changed decision")
    second.record["afterRestart"] = after
    second.close()
    ensure(second.process.returncode == 130 and recovery.gone([row["pid"] for row in second.children]), "Normal shutdown failed")
    ensure((directory / "explicit_restart.stderr").read_bytes() == b"", "Normal shutdown emitted a retirement event")
    second.record.update(exitCode=130, ownedChildrenGone=True, retirementEvent=None)
    print("Explicit restart: identical profile/decision; normal shutdown has no retirement event", flush=True)

    third = recovery.OwnedRuntime(args, directory, "inference_timeout", 100, first.port); owned.append(third)
    failure = third.score(request)
    ensure(failure["httpStatus"] == 504 and failure["result"]["reason"] == "inference_timeout", "Expected deadline failure")
    third.record["failedRequest"] = failure
    finish(third, directory, "inference_timeout")
    print("Server deadline: complete 504 then verified timeout event and exit 75", flush=True)

    fourth = recovery.OwnedRuntime(args, directory, "inference_cancelled", 5000, first.port); owned.append(fourth)
    ensure(fourth.profile == first.profile, "Cancellation runtime changed the 5-second profile")
    fourth.record["failedRequest"] = cancel(fourth, request)
    finish(fourth, directory, "inference_cancelled")
    print("Opted-in disconnect: complete cancellation response then verified event and exit 75", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    ensure(args.output.resolve().is_relative_to((ROOT / "docs/private").resolve()),
           "Retirement probe evidence must stay under ignored docs/private")
    ensure(not args.output.exists(), "Output already exists")
    request = Request.from_dict(read_json(args.request))
    ensure(request.kind in {"choice", "boolean"}, "Probe accepts Choice/Boolean diagnostics only")
    sources = source_hashes()
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    for name, digest in sources.items():
        frozen = subprocess.check_output(["git", "show", f"{commit}:{name}"], cwd=ROOT)
        ensure(hashlib.sha256(frozen).hexdigest() == digest, "Commit probe/runtime sources before inference")
    args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory = args.output.with_name(args.output.stem + ".runtime-logs")
    directory.mkdir(mode=0o700)  # Preserve stderr even if startup or a later assertion fails.
    owned, cleanup_errors = [], []
    succeeded, failure_type = False, None
    try:
        run_scenarios(args, request, directory, owned)
        ensure(source_hashes() == sources, "Sources changed during inference")
        succeeded = True
    except BaseException as error:
        failure_type = type(error).__name__
        raise
    finally:
        for runtime in owned:
            try: runtime.close()
            except Exception as error: cleanup_errors.append(type(error).__name__)
        stopped = not cleanup_errors and recovery.gone([row["pid"] for runtime in owned for row in runtime.children])
        report = sealed({"schemaVersion": "agat.decision.retirement-probe.v1",
                         "status": "pass" if succeeded and stopped else "fail", "failureType": failure_type,
                         "sourceCommit": commit, "sourceSha256": sources, "inputSha256": request.input_sha256,
                         "runs": [runtime.record for runtime in owned], "allOwnedProcessesStopped": stopped,
                         "cleanupErrors": cleanup_errors, "qualification": "not_assessed", "routingEnabled": False,
                         "limitations": ["Controlled foreground faults; no diagnosis of prior unplanned failures.",
                                         "Fresh runtime fingerprints; earlier qualification does not transfer.",
                                         "No native service manager, sustained load or performance SLO assessment."]})
        write_new(args.output, report)
        if failure_type is None: ensure(stopped, "Owned child cleanup failed")
    print("Retirement diagnostics verified; private evidence saved", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
