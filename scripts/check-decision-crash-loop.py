#!/usr/bin/env python3
"""Three controlled native launchd retirements, automatic recovery and bounded cleanup."""
from __future__ import annotations

import argparse
import copy
import hashlib
import http.client
import os
import platform
import runpy
import signal
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_runtime.artifacts import read_json, sealed, write_new
from decision_runtime.contracts import Request, fingerprint
from decision_runtime.model_store import sha256_file
from scripts.lib.decision_crash_loop import (
    FAILURES, STABLE_SECONDS, THROTTLE_SECONDS, failure_state, integer, metric_snapshot,
    require, retirement_events, runtime_inventory, up_sequence, validate_history,
)
from scripts.lib.decision_monitoring import PrometheusObservation, summarize_snapshot
from scripts.lib.decision_performance import profile_from_health
from scripts.lib.decision_service import write_launch_agent

launchd = runpy.run_path(str(ROOT / "scripts/check-decision-launchd.py"))
session = runpy.run_path(str(ROOT / "scripts/check-decision-resident-session.py"))
OwnedRuntime = launchd["OwnedRuntime"]
child_processes, gone = launchd["child_processes"], launchd["gone"]
service_info, launchctl = launchd["service_info"], launchd["launchctl"]
SOURCES = ("scripts/check-decision-crash-loop.py", "scripts/lib/decision_crash_loop.py")


def source_identity(manager, bundle):
    commit, sources = session["source_identity"](manager, bundle)
    for name in SOURCES:
        checksum = sha256_file(ROOT / name)
        raw = subprocess.check_output(["git", "show", f"{commit}:{name}"], cwd=ROOT, timeout=5)
        require(hashlib.sha256(raw).hexdigest() == checksum, "Commit crash-loop sources before measurement")
        sources[name] = checksum
    return commit, sources


def evidence_directory(path):
    private = (ROOT / "docs/private").resolve()
    path = path.absolute()
    require(private.is_relative_to(ROOT.resolve()) and path.resolve() != private
            and path.resolve().is_relative_to(private), "Use a new evidence directory under docs/private")
    require(not path.exists() and not path.is_symlink(), "Previous evidence must be preserved")
    return path


def birth_observation(pid, path):
    integer(pid)
    raw = subprocess.check_output(["/bin/ps", "-p", str(pid), "-o", "lstart="], text=True, timeout=5,
                                  env={**os.environ, "LC_ALL": "C", "TZ": "UTC"}).strip()
    require(0 < len(raw) <= 64 and "\n" not in raw, "Cannot read one native process birth")
    with path.open("x") as stream: stream.write(raw + "\n")
    path.chmod(0o600)
    return datetime.strptime(raw, "%a %b %d %H:%M:%S %Y").replace(tzinfo=timezone.utc).timestamp()


def wait_for_generation_exit(target, generation, path, timeout=5):
    deadline = time.monotonic() + timeout
    observations = []
    while time.monotonic() < deadline:
        state = service_info(target, path)
        observations.append({"at": time.time(), "state": state})
        if failure_state(state, generation):
            return state, observations
        time.sleep(0.1)
    raise RuntimeError("Current generation did not publish its exit within five seconds")


def capture_metrics(monitor, phase, count, previous_start=None):
    summary = monitor.capture(phase, count, new_start=previous_start is not None)
    raw = monitor.queries[-1]["response"]
    require(previous_start is None or summary["serverStartTime"] > previous_start,
            "Scrape belongs to a previous generation")
    record = {"raw": raw, "summary": summary, "targets": monitor.api("targets", {"state": "active"}),
              "capturedEpoch": time.time()}
    metric_snapshot(record, monitor.instance, count)
    return record


def resident_metrics(manager, port, monitoring_port):
    raw = manager["api"](monitoring_port, "/api/v1/query", {"query": '{job="agat-decision",__name__=~"agat_decision_.*"}'})
    return {"raw": raw, "summary": summarize_snapshot(raw, f"127.0.0.1:{port}")}


def resident_unchanged(before, after, metrics_before, metrics_after):
    for key in ("bundleRoot", "bundleSeal", "profileSha256", "registrationFileSha256", "registrationSeal",
                "installedPlists", "environment", "processes"):
        require(before[key] == after[key], f"Permanent resident binding changed: {key}")
    for key in ("hostFingerprint", "bootSessionUuid", "bootTimeEpoch", "guiSessionId", "ownerUid"):
        require(before["osSession"][key] == after["osSession"][key], "OS/GUI session changed during experiment")
    # Duration sums and metric timestamps are read separately; compare all logical counters/gauges.
    require(metrics_before["summary"] == metrics_after["summary"], "Permanent resident counters/generation changed")


def temporary_config(bundle, directory, label, port):
    require(label.startswith("org.agat.decision-crash-probe-") and label != bundle["serviceConfigs"][0]["Label"], "Unsafe temporary label")
    config = copy.deepcopy(bundle["serviceConfigs"][0])
    require(config["ThrottleInterval"] == THROTTLE_SECONDS and config["RunAtLoad"] is True
            and config["KeepAlive"] == {"SuccessfulExit": False}, "Resident restart policy differs from this gate")
    config["Label"] = label
    arguments = config["ProgramArguments"]
    arguments[arguments.index("--port")+1] = str(port)
    for key, suffix in (("StandardOutPath", "out.log"), ("StandardErrorPath", "err.log")):
        config[key] = str(directory / f"{label}.{suffix}")
    return config


def run_probe(args, manager, bundle, port, monitoring_port, commit, sources):
    directory = args.evidence_dir
    directory.parent.mkdir(parents=True, exist_ok=True)
    directory.mkdir(mode=0o700)
    report = {"schemaVersion": "agat.decision.launchd-crash-loop.v1", "createdAt": datetime.now(timezone.utc).isoformat(),
              "status": "failed", "qualification": "not_assessed", "routingEnabled": False,
              "sourceCommit": commit, "sourceFiles": sources, "bundleSeal": bundle["sha256"],
              "profileSha256": bundle["profileSha256"], "inputSha256": args.request.input_sha256, "request": args.request.to_dict(),
              "failureBudget": FAILURES, "throttleSeconds": THROTTLE_SECONDS, "stableSeconds": STABLE_SECONDS,
              "runs": [], "failures": [], "quiet": {"samples": []}, "ownedPids": [], "retirementEvents": [],
              "checks": {key: False for key in ("nativePlistValidated", "historyVerified", "residentUnchanged",
                          "temporaryServiceRemoved", "allOwnedProcessesStopped", "sourcesStable")},
              "failure": None, "cleanupFailure": None,
              "limitations": ["Three controlled idle backend deaths in this GUI session; actual boot/login is separate.",
                              "Throttle limits start frequency; it is not a production retry-count circuit breaker.",
                              "Four diagnostic calls and a 30-second stable window do not qualify correctness or production SLO.",
                              "Endpoint alerts become pending then clear before the unchanged two-minute firing delay."]}
    root = args.bundle.absolute()
    label = "org.agat.decision-crash-probe-" + uuid.uuid4().hex
    target = f"gui/{os.getuid()}/{label}"
    monitor = None; inventory_complete = True; claimed = False
    started = time.monotonic()

    def ready(generation):
        nonlocal inventory_complete
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            state = service_info(target, directory / f"ready-{generation}.txt")
            if state is None:
                inventory_complete = False
            require(state is not None, "Temporary job disappeared before readiness")
            if state.get("pid"):
                pid = integer(state["pid"])
                if pid not in report["ownedPids"]: report["ownedPids"].append(pid)
                # Inventory even a hung/failed startup, before requesting HTTP health.
                try:
                    children = child_processes(pid)
                except Exception:
                    inventory_complete = False
                    raise
                for child in children:
                    if child["pid"] not in report["ownedPids"]: report["ownedPids"].append(child["pid"])
                if type(state.get("runs")) is not int or state["runs"] != generation:
                    inventory_complete = False
                    raise RuntimeError("Unexpected launchd generation before readiness; process inventory incomplete")
                try:
                    runtime = OwnedRuntime.__new__(OwnedRuntime); runtime.port = runtime_port
                    status, health = runtime.call("GET", "/health")
                except (OSError, http.client.HTTPException, ValueError):
                    time.sleep(0.2); continue
                if status == 200:
                    runtime.profile = profile_from_health(health)
                    require(runtime.profile == bundle["profile"], "Probe profile differs from the resident package")
                    record = {"generation": generation, "pid": pid, "children": children, "service": state,
                              "profile": runtime.profile, "profileSha256": fingerprint(runtime.profile),
                              "readyElapsedMs": round((time.monotonic()-started)*1000, 3)}
                    try:
                        pids = runtime_inventory(record)
                    except ValueError:
                        inventory_complete = False
                        raise
                    record["processStarts"] = {str(pid): birth_observation(pid, directory / f"birth-{generation}-{pid}.txt") for pid in pids}
                    require(service_info(target) == state and child_processes(pid) == children, "Process set changed during readiness")
                    report["runs"].append(record)
                    return runtime, record
            time.sleep(0.2)
        raise RuntimeError("Launchd startup exceeded its bounded 90-second deadline")

    previous_handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    def interrupted(_signum, _frame):
        raise KeyboardInterrupt("Crash-loop gate interrupted")
    for sig in previous_handlers: signal.signal(sig, interrupted)
    try:
        before = session["collect"](manager, root, bundle, port, monitoring_port, 90)
        report["residentBefore"] = before
        report["residentMetricsBefore"] = resident_metrics(manager, port, monitoring_port)
        require(service_info(target) is None, "Refusing to register an existing label")
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0)); runtime_port = reservation.getsockname()[1]
        require(runtime_port not in (port, monitoring_port), "Temporary port conflicts with the resident")
        config = temporary_config(bundle, directory, label, runtime_port)
        report.update(label=label, target=target, serviceConfig=config, instance=f"127.0.0.1:{runtime_port}")
        plist = directory / f"{label}.plist"
        write_launch_agent(plist, config); plist.chmod(0o600)
        for key in ("StandardOutPath", "StandardErrorPath"):
            os.close(os.open(config[key], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        lint = subprocess.run(["/usr/bin/plutil", "-lint", str(plist)], capture_output=True, text=True, timeout=5)
        require(lint.returncode == 0, "Native plist validation failed")
        report["checks"]["nativePlistValidated"] = True
        monitor = PrometheusObservation(ROOT, directory, root / "bin/prometheus", root / "bin/promtool")
        monitor.start(runtime_port)
        # Mark ownership immediately before bootstrap, including interrupted/failing submission.
        claimed = True
        require(launchctl("bootstrap", f"gui/{os.getuid()}", str(plist)).returncode == 0, "Temporary bootstrap failed")
        previous_start = None
        for generation in range(1, FAILURES+2):
            runtime, run = ready(generation)
            run["readyMetrics"] = capture_metrics(monitor, f"ready-{generation}", 0, previous_start)
            current_start = run["readyMetrics"]["summary"]["serverStartTime"]
            run["decision"] = runtime.score(args.request)
            require(run["decision"]["httpStatus"] == 200, "Diagnostic scoring failed")
            run["scoredMetrics"] = capture_metrics(monitor, f"scored-{generation}", 1, previous_start)
            monitor.wait_alert(False)
            if report["failures"]:
                failure = report["failures"][-1]
                failure["alertCleared"] = True
                failure["clearedAlerts"] = monitor.api("alerts")
                failure["failureToReadyMs"] = round(run["readyElapsedMs"]-failure["failedElapsedMs"], 3)
                failure.pop("failedMonotonic")
                failure["upHistory"] = monitor.api("query_range", {"query": 'up{job="agat-decision"}',
                    "start": failure["healthyEpoch"], "end": time.time(), "step": "1s"})
                up_sequence(failure["upHistory"], monitor.instance)
            if generation == FAILURES+1: break
            child = next(row for row in run["children"] if row["role"] == "inference")
            require(service_info(target) == run["service"] and child_processes(run["pid"]) == run["children"], "Ownership changed before signal")
            failure = {"generation": generation, "signaledChildPid": child["pid"], "healthyEpoch": time.time(),
                       "failedMonotonic": time.monotonic(), "failedElapsedMs": round((time.monotonic()-started)*1000, 3),
                       "ownedProcessesGone": False, "endpointPending": False, "alertCleared": False}
            report["failures"].append(failure)
            os.kill(child["pid"], signal.SIGKILL)
            require(gone(runtime_inventory(run), timeout=5), "Retired generation left an owned process")
            failure["ownedProcessesGone"] = True
            failure["failureToExitMs"] = round((time.monotonic()-failure["failedMonotonic"])*1000, 3)
            failure["exitState"], failure["exitObservations"] = wait_for_generation_exit(target, generation, directory / f"failure-{generation}.txt")
            monitor.failed(); failure["endpointPending"] = True
            failure["downTargets"] = monitor.api("targets", {"state": "active"})
            failure["pendingAlerts"] = monitor.api("alerts")
            print(f"Controlled failure {generation}/{FAILURES}: exit 75, native scrape down; awaiting launchd", flush=True)
            previous_start = current_start
        quiet_started = time.monotonic()
        while True:
            state = service_info(target, directory / f"stable-{len(report['quiet']['samples'])+1}.txt")
            require(state is not None and state.get("pid") == run["pid"] and state.get("runs") == FAILURES+1, "Unexpected restart during stable window")
            children = child_processes(run["pid"])
            require(children == run["children"], "Owned children changed during stable window")
            metrics = capture_metrics(monitor, "stable", 1, previous_start)
            elapsed = time.monotonic()-quiet_started
            report["quiet"]["samples"].append({"elapsedSeconds": elapsed, "service": state, "children": children, "metrics": metrics})
            if elapsed >= STABLE_SECONDS: break
            time.sleep(min(2, STABLE_SECONDS-elapsed))
        report["quiet"]["elapsedSeconds"] = time.monotonic()-quiet_started
        report["retirementEvents"] = retirement_events(Path(config["StandardErrorPath"]).read_text())
        report["historySummary"] = validate_history(report["runs"], report["failures"], report["quiet"], report["retirementEvents"], bundle["profile"], monitor.instance, args.request)
        report["checks"]["historyVerified"] = True
    except (Exception, KeyboardInterrupt) as error:
        report["failure"] = {"type": type(error).__name__, "message": str(error)[:1000]}
    finally:
        # Cleanup cannot be interrupted by a second signal; only our random label is touched.
        for sig in previous_handlers: signal.signal(sig, signal.SIG_IGN)
        try:
            if claimed:
                launchctl("bootout", target)
                report["checks"]["temporaryServiceRemoved"] = launchd["wait_for_removal"](target, directory / "cleanup.txt")
            else:
                report["checks"]["temporaryServiceRemoved"] = service_info(target) is None
            report["checks"]["allOwnedProcessesStopped"] = gone(report["ownedPids"], timeout=8) and inventory_complete
            require(report["checks"]["temporaryServiceRemoved"] and report["checks"]["allOwnedProcessesStopped"], "Temporary job/process cleanup incomplete")
        except Exception as error:
            report["cleanupFailure"] = {"type": type(error).__name__, "message": str(error)[:1000]}
        try:
            if monitor: monitor.close()
        except Exception as error:
            report["cleanupFailure"] = {"type": type(error).__name__, "message": str(error)[:1000]}
        try:
            after = session["collect"](manager, root, bundle, port, monitoring_port, 90)
            report["residentAfter"] = after
            report["residentMetricsAfter"] = resident_metrics(manager, port, monitoring_port)
            resident_unchanged(report["residentBefore"], after, report["residentMetricsBefore"], report["residentMetricsAfter"])
            report["checks"]["residentUnchanged"] = True
            require(source_identity(manager, bundle)[1] == sources, "Measurement sources changed")
            report["checks"]["sourcesStable"] = True
        except Exception as error:
            report["cleanupFailure"] = {"type": type(error).__name__, "message": str(error)[:1000]}
        finally:
            for sig, handler in previous_handlers.items(): signal.signal(sig, handler)
    if monitor:
        # The per-cycle raw observations are authoritative; do not reuse the two-start pass flags.
        report["monitoring"] = {key: value for key, value in monitor.report().items() if key != "checks"}
        report["monitoring"]["prometheusStopped"] = monitor.checks["prometheusStopped"]
    report["retainedFilesSha256"] = {path.name: sha256_file(path) for path in sorted(directory.iterdir()) if path.is_file()}
    success = report["failure"] is None and report["cleanupFailure"] is None and all(report["checks"].values())
    success = success and monitor is not None and report["monitoring"]["prometheusStopped"] is True
    report.update(status="observed" if success else "failed", elapsedMs=round((time.monotonic()-started)*1000, 3))
    output = directory / "crash-loop.json"
    write_new(output, sealed(report)); output.chmod(0o600)
    print(f"Bounded crash-loop {report['status']}; evidence: {output}", flush=True)
    return 0 if success else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--expected-seal", required=True)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        require(platform.system() == "Darwin", "Native crash-loop gate requires macOS")
        args.evidence_dir = evidence_directory(args.evidence_dir)
        args.request = Request.from_dict(read_json(args.request))
        require(args.request.kind in ("choice", "boolean"), "Use a Choice/Boolean diagnostic")
        manager = runpy.run_path(str(ROOT / "scripts/manage-decision-resident-deployment.py"))
        bundle, port, monitoring_port = manager["validate_bundle"](args.bundle.absolute(), args.expected_seal)
        commit, sources = source_identity(manager, bundle)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f"Cannot prepare native crash-loop gate: {error}", file=sys.stderr, flush=True)
        return 1
    return run_probe(args, manager, bundle, port, monitoring_port, commit, sources)


if __name__ == "__main__":
    raise SystemExit(main())
