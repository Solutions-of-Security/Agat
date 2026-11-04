#!/usr/bin/env python3
"""Prepare a stable read-only login observer; do not register a LaunchAgent."""
from __future__ import annotations

import argparse
import hashlib
import os
import platform
import plistlib
import runpy
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_runtime.artifacts import sealed, write_new
from decision_runtime.model_store import sha256_file
from scripts.lib.decision_resident_session import observed_event, require

LABEL = "org.agat.decision-session-observer"
SOURCES = ("scripts/observe-decision-resident-session.py", "scripts/prepare-decision-session-observer.py",
           "scripts/manage-decision-session-observer.py")


def source_identity():
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, timeout=5).strip()
    sources = {name: sha256_file(ROOT/name) for name in SOURCES}
    for name, checksum in sources.items():
        raw = subprocess.check_output(["git", "show", f"{commit}:{name}"], cwd=ROOT, timeout=5)
        require(hashlib.sha256(raw).hexdigest() == checksum, "Commit observer package sources before preparation")
    return commit, sources


def destination(value):
    base = Path.home() / "Library/Application Support/Agat/decision-shadow/session-observers"
    root = value.absolute()
    require(not base.is_symlink() and not root.exists() and not root.is_symlink() and root.resolve().parent == base.resolve(),
            "Use a new direct observer package under Application Support")
    return root


def snapshot(root, commit):
    source = root / "source"
    source.mkdir(mode=0o700)
    commands = (["/usr/bin/git", "init", "--quiet", str(source)],
                ["/usr/bin/git", "-C", str(source), "fetch", "--no-tags", "--depth=1", str(ROOT), commit],
                ["/usr/bin/git", "-C", str(source), "checkout", "--detach", "--quiet", "FETCH_HEAD"],
                ["/usr/bin/git", "-C", str(source), "fsck", "--full"])
    observations = []
    for command in commands:
        result = subprocess.run(command, capture_output=True, text=True, timeout=180)
        observations.append({"command": command, "returncode": result.returncode, "output": (result.stdout+result.stderr)[-8192:]})
        require(result.returncode == 0, "Cannot create a self-contained frozen Git snapshot")
    require(not (source / ".git/objects/info/alternates").exists(), "Snapshot must not reference temporary Git objects")
    return observations


def agent(root, python, seal):
    private = root / "docs/private"
    return {"Label": LABEL, "ProgramArguments": [python, "-B", str(root / "observer.py"), "--package", str(root), "--expected-seal", seal],
            "WorkingDirectory": str(root), "RunAtLoad": True, "KeepAlive": False, "ProcessType": "Standard", "ExitTimeOut": 30,
            "AbandonProcessGroup": False,
            "EnvironmentVariables": {"PYTHONUNBUFFERED": "1", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                                     "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
            "StandardOutPath": str(private / "observer.out.log"), "StandardErrorPath": str(private / "observer.err.log")}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--expected-resident-seal", required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--baseline-sha256", required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        require(platform.system() == "Darwin", "Observer preparation requires macOS")
        session = runpy.run_path(str(ROOT / "scripts/check-decision-resident-session.py"))
        session["private_output"](args.output)
        root = destination(args.destination)
        baseline = session["read_baseline"](args.baseline, args.baseline_sha256)
        manager = runpy.run_path(str(ROOT / "scripts/manage-decision-resident-deployment.py"))
        bundle, port, monitoring_port = manager["validate_bundle"](args.bundle.absolute(), args.expected_resident_seal)
        commit, sources = source_identity()
        _, session_sources = session["source_identity"](manager, bundle)
        current = session["collect"](manager, args.bundle.absolute(), bundle, port, monitoring_port, 90)
        session["verify_bindings"](baseline, current, session_sources)
        observed_event(baseline["osSession"], current["osSession"], "boot")
        root.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        root.mkdir(mode=0o700)
        private = root / "docs/private"; private.mkdir(parents=True, mode=0o700)
        git_observations = snapshot(root, baseline["sourceCommit"])
        shutil.copyfile(args.baseline, root / "baseline.json"); (root / "baseline.json").chmod(0o600)
        shutil.copyfile(ROOT / SOURCES[0], root / "observer.py"); (root / "observer.py").chmod(0o600)
        config = sealed({"schemaVersion": "agat.decision.session-observer-bundle.v1", "createdAt": datetime.now(timezone.utc).isoformat(),
                         "destination": str(root), "ownerUid": os.getuid(), "residentRoot": str(args.bundle.absolute()), "residentSeal": args.expected_resident_seal,
                         "profileSha256": bundle["profileSha256"], "python": str(args.bundle.absolute() / "venv/bin/python"),
                         "snapshotCommit": baseline["sourceCommit"], "sourceFiles": baseline["sourceFiles"],
                         "builderCommit": commit, "builderSources": sources, "baselineFileSha256": args.baseline_sha256,
                         "collectorSha256": sources[SOURCES[0]], "routingEnabled": False, "qualification": "not_assessed"})
        write_new(root / "deployment.json", config); (root / "deployment.json").chmod(0o600)
        recipe = agent(root, config["python"], config["sha256"])
        plist = root / f"{LABEL}.plist"
        with plist.open("xb") as stream: stream.write(plistlib.dumps(recipe, fmt=plistlib.FMT_XML, sort_keys=False))
        plist.chmod(0o600)
        for key in ("StandardOutPath", "StandardErrorPath"):
            with Path(recipe[key]).open("x"): pass
            Path(recipe[key]).chmod(0o600)
        collector = runpy.run_path(str(ROOT / SOURCES[0]))
        require(collector["validate_package"](root, config["sha256"]) == config, "Frozen observer package failed admission")
        require(source_identity() == (commit, sources), "Observer builder sources changed")
        report = sealed({"schemaVersion": "agat.decision.session-observer-preparation.v1", "status": "prepared",
                         "observerRoot": str(root), "observerSeal": config["sha256"], "sourceCommit": commit, "sourceFiles": sources,
                         "snapshotCommit": baseline["sourceCommit"], "snapshotSourceCount": len(baseline["sourceFiles"]), "gitObservations": git_observations,
                         "residentObservation": current, "serviceConfig": recipe, "nativeJobInstalled": False,
                         "actualBootLoginAccepted": False, "routingEnabled": False, "qualification": "not_assessed"})
        write_new(args.output, report); args.output.chmod(0o600)
        print(f"Observer prepared; seal {config['sha256']}; evidence {args.output}")
        return 0
    except (Exception, KeyboardInterrupt) as error:
        # A partial destination is preserved for diagnosis; never recursively remove it.
        print(f"Cannot prepare session observer: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__": raise SystemExit(main())
