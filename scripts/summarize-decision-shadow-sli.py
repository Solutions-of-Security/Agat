#!/usr/bin/env python3
"""Summarize caller SLIs from pinned private coordinator trace exports."""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_runtime.artifacts import sealed
from decision_runtime.contracts import canonical_json, fingerprint, parse_json
from decision_runtime.model_store import sha256_file
from scripts.lib.decision_shadow_sli import analyze, require

SOURCES = ("scripts/summarize-decision-shadow-sli.py", "scripts/lib/decision_shadow_sli.py",
           "scripts/lib/decision_stage_inventory.py",
           "scripts/lib/decision_caller_inventory.py", "workers/agat_worker.py", "apps/coordinator/src/server.ts",
           "workers/local_decisions.py", "apps/coordinator/src/local-decisions.ts", "apps/coordinator/src/database.ts",
           "apps/coordinator/src/decision-shadow-assignments.ts",
           "apps/coordinator/src/decision-caller-accounting.ts",
           "decision_runtime/contracts.py", "decision_runtime/artifacts.py", "decision_runtime/model_store.py")


def source_identity():
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, timeout=5).strip()
    files = {name: sha256_file(ROOT / name) for name in SOURCES}
    for name, expected in files.items():
        raw = subprocess.check_output(["git", "show", f"{commit}:{name}"], cwd=ROOT, timeout=5)
        require(hashlib.sha256(raw).hexdigest() == expected, "Commit SLI/transport/validation sources before measurement")
    return commit, files


def bounded_bytes(path, limit):
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    require(len(raw) <= limit, "Input grew beyond its bounded size")
    return raw


def write_private_new(path, report):
    encoded = (canonical_json(sealed(report))+"\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(encoded)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", type=Path, action="append", required=True)
    parser.add_argument("--trace-sha256", action="append", required=True)
    parser.add_argument("--profile-sha256", required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--profile-file-sha256", required=True)
    parser.add_argument("--latency-threshold-ms", type=int, required=True)
    parser.add_argument("--traffic-kind", choices=("observed_workflow", "diagnostic_fixture"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    private = (ROOT / "docs/private").resolve()
    output = args.output.absolute()
    try:
        require(output.resolve().is_relative_to(private) and output.resolve() != private and private.is_relative_to(ROOT.resolve()), "Use a private SLI output under docs/private")
        require(not output.exists() and not output.is_symlink(), "Preserve previous SLI evidence; output must be new")
    except (OSError, ValueError) as error:
        print(f"Cannot prepare SLI output: {error}", file=sys.stderr); return 1
    report = {"schemaVersion": "agat.decision.shadow-caller-sli.v1", "status": "failed", "trafficKind": args.traffic_kind,
              "sloAccepted": False, "routingEnabled": False, "qualification": "not_assessed", "failure": None}
    try:
        require(len(args.trace) == len(args.trace_sha256) and 1 <= len(args.trace) <= 32, "Match each trace with its independent file SHA")
        commit, files = source_identity()
        require(re.fullmatch(r"[a-f0-9]{64}", args.profile_file_sha256) and args.profile.is_file() and not args.profile.is_symlink()
                and args.profile.stat().st_size <= 1024*1024, "Pin a bounded regular profile file")
        profile_raw = bounded_bytes(args.profile, 1024*1024)
        require(hashlib.sha256(profile_raw).hexdigest() == args.profile_file_sha256, "Profile file pin differs")
        profile = parse_json(profile_raw)
        require(fingerprint(profile) == args.profile_sha256, "Expected profile content SHA differs")
        traces = []; inputs = []; total = 0
        for path, expected in zip(args.trace, args.trace_sha256):
            require(isinstance(expected, str) and re.fullmatch(r"[a-f0-9]{64}", expected), "Pin every trace file SHA")
            require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 16*1024*1024, "Use bounded regular trace files")
            raw = bounded_bytes(path, 16*1024*1024); total += len(raw)
            require(total <= 128*1024*1024 and hashlib.sha256(raw).hexdigest() == expected, "Trace size or independent SHA differs")
            traces.append(parse_json(raw)); inputs.append({"path": str(path.absolute()), "fileSha256": expected})
        summary = analyze(traces, profile, args.latency_threshold_ms)
        report.update(summary, sourceCommit=commit, sourceFiles=files, inputs=inputs, profileSha256=args.profile_sha256,
                      profileFileSha256=args.profile_file_sha256,
                      status="diagnostic_only" if args.traffic_kind == "diagnostic_fixture" else summary["measurementStatus"])
        require(source_identity() == (commit, files), "SLI measurement sources changed")
    except Exception as error:
        report["status"] = "failed"
        report["failure"] = {"type": type(error).__name__, "message": str(error)[:1000]}
    try:
        write_private_new(output, report)
    except (OSError, ValueError) as error:
        print(f"Cannot save SLI evidence: {error}", file=sys.stderr); return 1
    print(f"Caller SLI: {report['status']}; evidence {output}")
    return 1 if report["status"] == "failed" else 2 if report["measurementStatus"] == "insufficient_data" else 0


if __name__ == "__main__": raise SystemExit(main())
