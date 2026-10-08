#!/usr/bin/env python3
"""Capture a bounded authenticated stored cohort matching a pinned pilot plan."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import quote, urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_runtime.artifacts import sealed
from decision_runtime.contracts import parse_json
from scripts.lib.decision_pilot_evaluation import identifier, verify_cohort
from scripts.lib.decision_pilot_transport import MAX_BODY_BYTES, origin
from scripts.lib.decision_public_sources import pinned_input, private_directory, write_json_new, write_raw_new
from scripts.lib.decision_shadow_pilot import require, timestamp, utc_now, verify_plan

SOURCES = ("scripts/collect-decision-shadow-cohort.py", "scripts/lib/decision_pilot_transport.py",
    "scripts/evaluate-decision-shadow-pilot.py", "scripts/lib/decision_pilot_evaluation.py", "scripts/lib/decision_shadow_pilot.py",
    "scripts/lib/decision_shadow_sli.py", "scripts/lib/decision_stage_inventory.py", "scripts/lib/decision_caller_inventory.py",
    "scripts/lib/decision_public_sources.py", "decision_runtime/annotations.py", "decision_runtime/contracts.py", "decision_runtime/artifacts.py",
    "apps/coordinator/src/decision-shadow-cohort.ts", "apps/coordinator/src/database.ts", "apps/coordinator/src/server.ts",
    "apps/coordinator/src/postgres-database.ts", "apps/coordinator/src/postgres-worker.ts",
    "apps/coordinator/src/decision-shadow-assignments.ts", "apps/coordinator/src/decision-caller-accounting.ts")


def source_identity():
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, timeout=5).strip()
    files = {}
    for name in SOURCES:
        raw = (ROOT / name).read_bytes()
        require(raw == subprocess.check_output(["git", "show", f"{commit}:{name}"], cwd=ROOT, timeout=5), "Commit collector sources before capture")
        files[name] = hashlib.sha256(raw).hexdigest()
    return commit, files


def capture(coordinator, plan, token, timeout_s):
    origin(coordinator)
    config = plan["config"]
    identifier(config["processId"]); identifier(config["projectId"])
    require(isinstance(token, str) and 1 <= len(token) <= 4096 and all(33 <= ord(c) <= 126 for c in token),
            "Use a nonempty ASCII admin token without whitespace")
    path = "/api/v1/processes/" + quote(config["processId"], safe="") + "/decision-shadow-cohort?" + urlencode(
        {"processVersion": str(config["processVersion"]), **config["window"]})
    payload = {"origin": coordinator, "path": path, "token": token, "project": config["projectId"], "timeoutS": timeout_s}
    environment = os.environ.copy()
    # ssl.create_default_context otherwise honors this ambient TLS-secret log file.
    environment.pop("SSLKEYLOGFILE", None)
    try:
        response = subprocess.run([sys.executable, str(ROOT / "scripts/lib/decision_pilot_transport.py")],
            input=json.dumps(payload).encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout_s, cwd=ROOT, env=environment)
    except subprocess.TimeoutExpired:
        # subprocess.run kills and waits for this owned child, including blocked DNS/TLS/read.
        raise ValueError("Cohort transport exceeded its overall deadline") from None
    require(len(response.stdout) <= MAX_BODY_BYTES * 2, "Transport output exceeds its bound")
    data = parse_json(response.stdout)
    if response.returncode != 0 or data.get("status") != "captured":
        status = data.get("httpStatus")
        status = str(status) if type(status) is int and 100 <= status <= 599 else "unknown"
        raise ValueError(f"Cohort transport failed (HTTP {status}); no redirect or retry")
    require(set(data) == {"status", "response", "bodyBase64"}, "Invalid transport receipt")
    raw = base64.b64decode(data["bodyBase64"], validate=True)
    require(0 < len(raw) <= MAX_BODY_BYTES, "Cohort body exceeds its bound")
    verify_cohort(parse_json(raw), plan)
    return raw, {"origin": coordinator, "path": path, **data["response"], "requestCount": 1, "redirectsFollowed": 0, "retries": 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--plan-file-sha256", required=True)
    parser.add_argument("--coordinator-url", required=True)
    parser.add_argument("--admin-token-env", default="AGAT_ADMIN_TOKEN")
    parser.add_argument("--timeout-s", type=float, default=30)
    parser.add_argument("--traffic-kind", choices=("observed_workflow", "diagnostic_fixture"), required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        directory = private_directory(ROOT, args.output_dir)
    except Exception as error:
        print(f"Cannot prepare cohort output: {error}", file=sys.stderr); return 1
    token = None
    report = {"schemaVersion": "agat.decision.cohort-acquisition.v1", "status": "failed", "trafficKind": args.traffic_kind,
        "sloAccepted": False, "routingEnabled": False, "qualification": "not_assessed", "failure": None}
    try:
        require(0.1 <= args.timeout_s <= 30 and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", args.admin_token_env), "Invalid timeout or credential environment name")
        token = os.environ.get(args.admin_token_env)
        raw_plan = pinned_input(args.plan, args.plan_file_sha256, 2 * 1024 * 1024)
        plan = verify_plan(parse_json(raw_plan))
        require(plan["status"] == "ready_for_review" and not plan["missingFields"], "Complete the prospective technical plan before collection")
        require(timestamp(plan["config"]["window"]["endAt"], "endAt") <= timestamp(utc_now(), "captureTime"), "Wait until the planned window is complete")
        commit, files = source_identity()
        raw, request = capture(args.coordinator_url, plan, token, args.timeout_s)
        require(source_identity() == (commit, files), "Collector sources changed during capture")
        cohort = parse_json(raw)
        write_raw_new(directory / "cohort.http.json", raw)
        report.update(status="diagnostic_only" if args.traffic_kind == "diagnostic_fixture" else "captured",
            sourceCommit=commit, sourceFiles=files, planFileSha256=args.plan_file_sha256, planSha256=plan["sha256"],
            cohortFile={"file": "cohort.http.json", "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)},
            request=request, snapshotId=cohort["snapshotId"], observedAt=cohort["observedAt"], scope=cohort["scope"],
            counts=cohort["counts"], runIdsSha256=cohort["runIdsSha256"], scopeAndCensusBindingVerified=True,
            serverDeclaredStoredCohortComplete=True, serverDeploymentSourceVerified=False, eligibleWorkloadVerified=False,
            populationCoverageVerified=False, httpAttemptInventoryVerified=False, agreementVerified=False)
    except Exception as error:
        message = str(error)[:1000]
        if token: message = message.replace(token, "[redacted]")
        report["failure"] = {"type": type(error).__name__, "message": message}
    try:
        write_json_new(directory / "acquisition.json", sealed(report))
    except Exception as error:
        print(f"Cannot save cohort receipt: {type(error).__name__}", file=sys.stderr); return 1
    print(f"Cohort acquisition: {report['status']}; {directory}")
    return 1 if report["status"] == "failed" else 0


if __name__ == "__main__": raise SystemExit(main())
