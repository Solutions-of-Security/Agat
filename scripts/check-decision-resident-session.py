#!/usr/bin/env python3
"""Capture or verify a real boot/login of the two owned resident LaunchAgents."""
from __future__ import annotations

import argparse
import hashlib
import os
import platform
import re
import runpy
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_runtime.artifacts import sealed, verify_seal, write_new
from decision_runtime.contracts import fields, parse_json
from decision_runtime.model_store import sha256_file
from scripts.lib.decision_resident_session import (
    integer, observed_event, process_start_epoch, require, session_snapshot, validate_process_starts,
)

BASELINE_SCHEMA = "agat.decision.resident-session-baseline.v1"
VERIFICATION_SCHEMA = "agat.decision.resident-session-verification.v1"
SOURCES = ("scripts/check-decision-resident-session.py", "scripts/lib/decision_resident_session.py")


def source_identity(manager, bundle):
    commit, sources = manager["source_identity"](bundle.get("profileSourcePath", manager["prepare"]["LEGACY_PROFILE"]))
    for name in SOURCES:
        checksum = sha256_file(ROOT / name)
        raw = subprocess.check_output(["git", "show", f"{commit}:{name}"], cwd=ROOT, timeout=5)
        require(hashlib.sha256(raw).hexdigest() == checksum, "Commit session acceptance sources before execution")
        sources[name] = checksum
    return commit, sources


def private_output(path):
    private = (ROOT / "docs/private").resolve()
    require(private.is_relative_to(ROOT.resolve()) and path.absolute().resolve() != private
            and path.absolute().resolve().is_relative_to(private), "Use a private output under docs/private")
    require(not path.exists() and not path.is_symlink(), "Use a new output; previous evidence is preserved")


def read_baseline(path, expected_checksum):
    require(isinstance(expected_checksum, str) and re.fullmatch(r"[a-f0-9]{64}", expected_checksum), "Pin the baseline file SHA")
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 1024 * 1024, "Invalid baseline file")
    raw = path.read_bytes()
    require(len(raw) <= 1024 * 1024 and hashlib.sha256(raw).hexdigest() == expected_checksum, "Baseline file differs from its pinned SHA")
    baseline = verify_seal(parse_json(raw), BASELINE_SCHEMA)
    fields(baseline, {"schemaVersion", "createdAt", "status", "sourceCommit", "sourceFiles", "bundleRoot", "bundleSeal",
                      "profileSha256", "registrationFileSha256", "registrationSeal", "installedPlists", "osSession",
                      "processes", "observation", "serviceStates", "environment", "routingEnabled", "qualification", "sha256"})
    require(baseline["status"] == "baseline_recorded" and baseline["routingEnabled"] is False
            and baseline["qualification"] == "not_assessed", "Invalid resident baseline state")
    require(isinstance(baseline["sourceCommit"], str) and re.fullmatch(r"[a-f0-9]{40}", baseline["sourceCommit"]), "Invalid baseline source commit")
    validate_process_starts(baseline["processes"], baseline["osSession"])
    return baseline


def resident_environment(manager, root, bundle):
    code = ('import importlib.metadata,json,platform,sys;'
            'print(json.dumps({"version":list(sys.version_info[:3]),"machine":platform.machine(),'
            '"packages":[[d.metadata["Name"],d.version] for d in importlib.metadata.distributions()]}))')
    task_env = {**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
    python = str(root / "venv/bin/python")
    raw = subprocess.check_output([python, "-I", "-B", "-c", code], text=True, timeout=10, env=task_env)
    require(len(raw) <= 64 * 1024, "Resident dependency inspection exceeded its bound")
    data = parse_json(raw)
    fields(data, {"version", "machine", "packages"})
    require(isinstance(data["version"], list) and all(type(value) is int for value in data["version"])
            and data["version"] == [3, 13, 12] and data["machine"] == "arm64", "Resident Python/platform differs from the tested package")
    require(isinstance(data["packages"], list) and 1 <= len(data["packages"]) <= 100, "Invalid resident package inventory")
    installed = {}
    for row in data["packages"]:
        require(isinstance(row, list) and len(row) == 2 and all(isinstance(value, str) and value for value in row), "Invalid resident dependency entry")
        name = manager["prepare"]["normalized"](row[0])
        if name == "pip":
            continue
        require(name not in installed, "Duplicate resident dependency name")
        installed[name] = row[1]
    require(installed == bundle["installedDependencies"], "Resident dependencies differ from the sealed package")
    check = subprocess.run([python, "-I", "-B", "-m", "pip", "check"], capture_output=True, text=True, timeout=15, env=task_env)
    require(check.returncode == 0 and len(check.stdout + check.stderr) <= 64 * 1024, "Resident dependency compatibility check failed")
    return {"pythonVersion": data["version"], "machine": data["machine"], "installedDependencies": installed,
            "pipCheck": {"returncode": check.returncode, "output": check.stdout + check.stderr}}


def collect(manager, root, bundle, port, monitoring_port, wait_s):
    marker = manager["registered_ownership"](root, bundle)
    marker_checksum = sha256_file(root / "registration.json")
    environment = resident_environment(manager, root, bundle)
    plists = []
    for source, path in manager["plist_paths"](root):
        require(path.stat().st_uid == os.getuid() and path.stat().st_mode & 0o777 == 0o600, "Unexpected installed plist owner/mode")
        plists.append({"label": source.stem, "fileSha256": sha256_file(path), "ownerUid": path.stat().st_uid, "mode": "0600"})
    observation = manager["inspect_services"](root, bundle, port, monitoring_port, timeout=wait_s)
    require(observation["status"] == "ready", "Owned resident services are not ready with a fresh scrape")
    states = []
    for label in manager["LABELS"]:
        state = manager["launchctl"]("print", f"gui/{os.getuid()}/{label}")
        require(state.returncode == 0, "Cannot inspect the owned GUI service")
        states.append({"label": label, "body": state.stdout})
    session = session_snapshot([state["body"] for state in states], os.getuid())
    services = observation["services"]
    require([row["label"] for row in services] == list(manager["LABELS"]), "Foreign resident service inventory")
    processes = [{"role": role, "pid": row["state"]["pid"]}
                 for role, row in zip(("runtime", "prometheus"), services)]
    processes += [{"role": child["role"], "pid": child["pid"]} for child in services[0].get("children", [])]
    for process in processes:
        process["startedEpoch"] = process_start_epoch(process["pid"])
    validate_process_starts(processes, session)
    again = manager["inspect_services"](root, bundle, port, monitoring_port)
    require(again["status"] == "ready" and again["observedPids"] == observation["observedPids"], "Resident process set changed during capture")
    require(manager["registered_ownership"](root, bundle) == marker
            and sha256_file(root / "registration.json") == marker_checksum, "Registration changed during capture")
    checked, _, _ = manager["validate_bundle"](root, bundle["sha256"])
    require(checked == bundle, "Resident bundle changed during capture")
    return {"bundleRoot": str(root), "bundleSeal": bundle["sha256"], "profileSha256": bundle["profileSha256"],
            "registrationSeal": marker["sha256"], "registrationFileSha256": marker_checksum, "installedPlists": plists,
            "osSession": session, "processes": processes, "observation": again, "serviceStates": states, "environment": environment}


def verify_bindings(baseline, current, sources):
    for key in ("bundleRoot", "bundleSeal", "profileSha256", "registrationSeal", "registrationFileSha256", "installedPlists", "environment"):
        require(baseline[key] == current[key], f"Resident baseline binding changed: {key}")
    require(baseline["sourceFiles"] == sources, "Session/runtime sources changed; capture a new baseline")
    for name, checksum in sources.items():
        raw = subprocess.check_output(["git", "show", f"{baseline['sourceCommit']}:{name}"], cwd=ROOT, timeout=5)
        require(hashlib.sha256(raw).hexdigest() == checksum, "Historical baseline source binding failed")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("capture", "verify"))
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--expected-seal", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--baseline-sha256")
    parser.add_argument("--event", choices=("boot", "login"))
    parser.add_argument("--wait-s", type=int, default=90)
    args = parser.parse_args(argv)
    if args.action == "verify" and not (args.baseline and args.baseline_sha256 and args.event):
        parser.error("verify requires --baseline, --baseline-sha256 and --event")
    if args.action == "capture" and (args.baseline or args.baseline_sha256 or args.event):
        parser.error("baseline/event arguments apply only to verify")
    try:
        private_output(args.output)
        require(platform.system() == "Darwin", "Resident session acceptance requires macOS")
        integer(args.wait_s, 0, 180)
    except (OSError, ValueError) as error:
        print(f"Cannot prepare resident session check: {error}", file=sys.stderr, flush=True)
        return 1
    report = {"schemaVersion": BASELINE_SCHEMA if args.action == "capture" else VERIFICATION_SCHEMA,
              "createdAt": datetime.now(timezone.utc).isoformat(), "status": "failed",
              "routingEnabled": False, "qualification": "not_assessed"}
    try:
        baseline = read_baseline(args.baseline, args.baseline_sha256) if args.action == "verify" else None
        manager = runpy.run_path(str(ROOT / "scripts/manage-decision-resident-deployment.py"))
        root = args.bundle.absolute()
        bundle, port, monitoring_port = manager["validate_bundle"](root, args.expected_seal)
        commit, sources = source_identity(manager, bundle)
        current = collect(manager, root, bundle, port, monitoring_port, args.wait_s)
        report.update(sourceCommit=commit, sourceFiles=sources)
        if args.action == "capture":
            report.update(current, status="baseline_recorded")
        else:
            report.update(current=current, baselineFileSha256=args.baseline_sha256,
                          baselineSourceCommit=baseline["sourceCommit"])
            verify_bindings(baseline, current, sources)
            event = observed_event(baseline["osSession"], current["osSession"], args.event)
            report["event"] = event
            eligible = event["status"] == "event_observed"
            if eligible:
                validate_process_starts(current["processes"], current["osSession"], after=baseline["osSession"]["capturedEpoch"])
                target = current["observation"]["targets"]["data"]["activeTargets"][0]
                last_scrape = datetime.fromisoformat(target["lastScrape"].replace("Z", "+00:00")).timestamp()
                require(last_scrape > baseline["osSession"]["capturedEpoch"], "Scrape predates the required event")
            report.update(status="verified" if eligible else "awaiting_event", event=event, current=current,
                          baselineFileSha256=args.baseline_sha256, baselineSourceCommit=baseline["sourceCommit"],
                          checks={"unchangedBundleProfileAndRegistration": True, "committedSourceBindings": True,
                                  "ownedPlistsAndReadyFreshScrape": True, "actualEventObserved": eligible,
                                  "newProcessStarts": eligible})
    except (Exception, KeyboardInterrupt) as error:
        report.update(status="failed", failure={"type": type(error).__name__, "message": str(error)[:1000]})
    try:
        write_new(args.output, sealed(report))
        args.output.chmod(0o600)
    except (OSError, ValueError) as error:
        print(f"Cannot save resident session evidence: {error}", file=sys.stderr, flush=True)
        return 1
    print(f"Resident session {args.action}: {report['status']}; file SHA {sha256_file(args.output)}; evidence: {args.output}", flush=True)
    return 0 if report["status"] in ("baseline_recorded", "verified") else 2 if report["status"] == "awaiting_event" else 1


if __name__ == "__main__":
    raise SystemExit(main())
