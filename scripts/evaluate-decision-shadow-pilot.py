#!/usr/bin/env python3
"""Evaluate pinned stored-cohort caller SLIs against a prospective pilot plan."""
from __future__ import annotations
import argparse
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_runtime.artifacts import sealed
from decision_runtime.contracts import parse_json
from scripts.lib.decision_pilot_evaluation import evaluate
from scripts.lib.decision_public_sources import pinned_input, write_json_new
from scripts.lib.decision_shadow_pilot import require

SOURCES = ("scripts/evaluate-decision-shadow-pilot.py", "scripts/lib/decision_pilot_evaluation.py", "scripts/lib/decision_shadow_pilot.py",
    "scripts/lib/decision_shadow_sli.py", "scripts/lib/decision_stage_inventory.py", "scripts/lib/decision_caller_inventory.py",
    "scripts/lib/decision_public_sources.py", "decision_runtime/annotations.py", "decision_runtime/contracts.py", "decision_runtime/artifacts.py",
    "apps/coordinator/src/decision-shadow-cohort.ts", "apps/coordinator/src/database.ts", "apps/coordinator/src/server.ts",
    "apps/coordinator/src/postgres-database.ts", "apps/coordinator/src/postgres-worker.ts",
    "apps/coordinator/src/decision-shadow-assignments.ts", "apps/coordinator/src/decision-caller-accounting.ts")


def source_identity():
    import subprocess
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, timeout=5).strip()
    files = {}
    for name in SOURCES:
        raw = (ROOT / name).read_bytes()
        require(raw == subprocess.check_output(["git", "show", f"{commit}:{name}"], cwd=ROOT, timeout=5), "Commit pilot evaluation sources before analysis")
        files[name] = hashlib.sha256(raw).hexdigest()
    return commit, files


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("plan", "cohort", "profile"):
        parser.add_argument("--" + name, type=Path, required=True)
        parser.add_argument("--" + name + "-file-sha256", required=True)
    parser.add_argument("--traffic-kind", choices=("observed_workflow", "diagnostic_fixture"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    output = args.output.absolute(); private = (ROOT / "docs/private").resolve()
    try:
        require(private.is_relative_to(ROOT.resolve()) and output.resolve().is_relative_to(private) and output.resolve() != private
                and not output.exists() and not output.is_symlink(), "Use a new private pilot evaluation output")
    except Exception as error:
        print(f"Cannot prepare pilot output: {error}", file=sys.stderr); return 1
    report = {"schemaVersion": "agat.decision.shadow-pilot-evaluation.v1", "status": "failed", "trafficKind": args.traffic_kind,
        "sloAccepted": False, "routingEnabled": False, "qualification": "not_assessed", "failure": None}
    try:
        commit, files = source_identity()
        plan_raw = pinned_input(args.plan, args.plan_file_sha256, 2 * 1024 * 1024)
        cohort_raw = pinned_input(args.cohort, args.cohort_file_sha256, 16 * 1024 * 1024)
        profile_raw = pinned_input(args.profile, args.profile_file_sha256, 1024 * 1024)
        summary = evaluate(parse_json(plan_raw), parse_json(cohort_raw), parse_json(profile_raw.decode("utf-8")), profile_raw)
        require(source_identity() == (commit, files), "Pilot evaluation sources changed")
        report.update(summary, sourceCommit=commit, sourceFiles=files,
            inputFileSha256={"plan": args.plan_file_sha256, "cohort": args.cohort_file_sha256, "profile": args.profile_file_sha256},
            status="diagnostic_only" if args.traffic_kind == "diagnostic_fixture" else summary["measurementStatus"])
    except Exception as error:
        report["failure"] = {"type": type(error).__name__, "message": str(error)[:1000]}
    try:
        output.parent.mkdir(parents=True, exist_ok=True, mode=0o700); write_json_new(output, sealed(report))
    except Exception as error:
        print(f"Cannot save pilot evaluation: {error}", file=sys.stderr); return 1
    print(f"Pilot evaluation: {report['status']}; {output}")
    return 1 if report["status"] == "failed" else 2 if report["measurementStatus"] == "insufficient_data" else 0


if __name__ == "__main__": raise SystemExit(main())
