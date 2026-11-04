#!/usr/bin/env python3
"""One read-only boot/login evidence collection from a pinned resident snapshot."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "agat.decision.session-observer-bundle.v1"
REPORT_SCHEMA = "agat.decision.session-observer-run.v1"


def require(value, message):
    if not value: raise ValueError(message)


def checksum(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024*1024): digest.update(block)
    return digest.hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def sealed(value):
    return {**value, "sha256": hashlib.sha256(canonical(value).encode()).hexdigest()}


def read_sealed(path, schema):
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 2*1024*1024, "Invalid sealed input file")
    def pairs(rows):
        result = {}
        for name, value in rows:
            require(name not in result, "Duplicate artifact key")
            result[name] = value
        return result
    raw = json.loads(path.read_text(), object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))
    require(isinstance(raw, dict) and raw.get("schemaVersion") == schema, "Unexpected input schema")
    body = {key: value for key, value in raw.items() if key != "sha256"}
    require(raw.get("sha256") == sealed(body)["sha256"], "Input seal differs")
    return raw


def validate_package(root, expected_seal):
    root = root.absolute()
    require(root.is_dir() and not root.is_symlink() and root.stat().st_uid == os.getuid()
            and root.stat().st_mode & 0o777 == 0o700, "Use the private owned observer package")
    for name in ("deployment.json", "baseline.json", "observer.py"):
        member = root / name
        require(member.is_file() and not member.is_symlink() and member.stat().st_uid == os.getuid()
                and member.stat().st_mode & 0o777 == 0o600, "Unexpected private observer file owner/mode")
    config = read_sealed(root / "deployment.json", SCHEMA)
    require(config["sha256"] == expected_seal and config["destination"] == str(root)
            and type(config["ownerUid"]) is int and config["ownerUid"] == os.getuid()
            and config["routingEnabled"] is False and config["qualification"] == "not_assessed", "Observer package binding differs")
    source = root / "source"
    require(source.is_dir() and not source.is_symlink() and source.resolve().parent == root.resolve(), "Invalid frozen source location")
    repository = source / ".git"
    require(repository.is_dir() and not repository.is_symlink() and repository.resolve().parent == source.resolve(), "Frozen Git metadata must remain local")
    for name, expected in config["sourceFiles"].items():
        path = Path(name)
        require(not path.is_absolute() and ".." not in path.parts, "Source path escapes snapshot")
        target = source / path
        require(target.is_file() and not target.is_symlink() and target.resolve().is_relative_to(source.resolve())
                and checksum(target) == expected, "Frozen source differs")
    require(checksum(root / "observer.py") == config["collectorSha256"], "Collector source differs")
    require(checksum(root / "baseline.json") == config["baselineFileSha256"], "Baseline pin differs")
    baseline = read_sealed(root / "baseline.json", "agat.decision.resident-session-baseline.v1")
    require(baseline["status"] == "baseline_recorded" and baseline["routingEnabled"] is False
            and baseline["qualification"] == "not_assessed" and baseline["sourceFiles"] == config["sourceFiles"]
            and baseline["sourceCommit"] == config["snapshotCommit"] and baseline["bundleRoot"] == config["residentRoot"]
            and baseline["bundleSeal"] == config["residentSeal"], "Baseline/snapshot/resident binding differs")
    head = subprocess.check_output(["/usr/bin/git", "rev-parse", "HEAD"], cwd=source, text=True, timeout=5).strip()
    require(head == config["snapshotCommit"], "Frozen Git revision differs")
    top = subprocess.check_output(["/usr/bin/git", "rev-parse", "--show-toplevel"], cwd=source, text=True, timeout=5).strip()
    require(Path(top).resolve() == source.resolve(), "Frozen Git worktree escaped its package")
    changes = subprocess.check_output(["/usr/bin/git", "status", "--porcelain", "--untracked-files=all"], cwd=source, text=True, timeout=10)
    require(not changes.strip() and not (source / ".git/objects/info/alternates").exists(), "Frozen checkout changed or depends on external Git objects")
    for name, expected in config["sourceFiles"].items():
        raw = subprocess.check_output(["/usr/bin/git", "show", f"{head}:{name}"], cwd=source, timeout=5)
        require(hashlib.sha256(raw).hexdigest() == expected, "Frozen Git source binding differs")
    return config


def accepted_verification(path, code, baseline_checksum, event):
    report = read_sealed(path, "agat.decision.resident-session-verification.v1")
    status = report["status"]
    require(report["routingEnabled"] is False and report["qualification"] == "not_assessed"
            and report.get("baselineFileSha256") == baseline_checksum, "Event verification binding differs")
    require(type(code) is int and (status, code) in (("verified", 0), ("awaiting_event", 2), ("failed", 1)), "Event exit/status differs")
    if status != "failed":
        eligible = status == "verified"
        require(report["event"]["expectedEvent"] == event and report["checks"]["actualEventObserved"] is eligible
                and report["checks"]["newProcessStarts"] is eligible, "Event was not independently observed")
    return {"status": status, "exitCode": code, "reportFileSha256": checksum(path), "reportSeal": report["sha256"],
            "actualEventAccepted": status == "verified", "path": str(path)}


def collect(root, config):
    source = root / "source"
    parent = source / "docs/private/session-events"
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    require(not parent.is_symlink() and parent.resolve().is_relative_to(source.resolve()), "Invalid private event directory")
    directory = parent / (datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex)
    directory.mkdir(mode=0o700)
    report = {"schemaVersion": REPORT_SCHEMA, "createdAt": datetime.now(timezone.utc).isoformat(), "status": "failed",
              "observerSeal": config["sha256"], "baselineFileSha256": config["baselineFileSha256"],
              "snapshotCommit": config["snapshotCommit"], "events": {}, "routingEnabled": False, "qualification": "not_assessed", "failure": None}
    handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    def interrupted(_sig, _frame): raise KeyboardInterrupt("Session evidence collection interrupted")
    for sig in handlers: signal.signal(sig, interrupted)
    try:
        for event in ("boot", "login"):
            output = directory / f"{event}.json"
            command = [config["python"], "-B", str(source / "scripts/check-decision-resident-session.py"), "verify",
                       "--bundle", config["residentRoot"], "--expected-seal", config["residentSeal"],
                       "--baseline", str(root / "baseline.json"), "--baseline-sha256", config["baselineFileSha256"],
                       "--event", event, "--output", str(output), "--wait-s", "90"]
            # Child does only metadata, pinned-file and loopback reads; no inference or job mutation.
            result = subprocess.run(command, cwd=source, capture_output=True, text=True, timeout=240,
                                    env={**os.environ, "PYTHONUNBUFFERED": "1", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
            require(len(result.stdout + result.stderr) <= 64*1024, "Verifier diagnostics exceeded their bound")
            log = directory / f"{event}.log"
            with log.open("x") as stream: stream.write(result.stdout + result.stderr)
            log.chmod(0o600)
            report["events"][event] = accepted_verification(output, result.returncode, config["baselineFileSha256"], event)
        require(all(row["status"] != "failed" for row in report["events"].values()), "Event verification failed")
        require(validate_package(root, config["sha256"]) == config, "Observer package changed during collection")
        report["status"] = "recorded"
    except (Exception, KeyboardInterrupt) as error:
        report["failure"] = {"type": type(error).__name__, "message": str(error)[:1000]}
    finally:
        for sig, handler in handlers.items(): signal.signal(sig, handler)
    output = directory / "observer-run.json"
    with output.open("x") as stream: stream.write(canonical(sealed(report)) + "\n")
    output.chmod(0o600)
    # A pending event is a successfully recorded observation; KeepAlive is false.
    print(json.dumps({"status": report["status"], "events": {key: value["status"] for key, value in report["events"].items()}, "output": str(output)}), flush=True)
    return 0 if report["status"] == "recorded" else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--expected-seal", required=True)
    args = parser.parse_args(argv)
    try:
        require(re.fullmatch(r"[a-f0-9]{64}", args.expected_seal), "Pin the observer package seal")
        config = validate_package(args.package, args.expected_seal)
        require(checksum(Path(__file__)) == config["collectorSha256"], "Executing collector differs from its pin")
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f"Cannot admit session observer: {error}", file=sys.stderr)
        return 1
    return collect(args.package.absolute(), config)


if __name__ == "__main__": raise SystemExit(main())
