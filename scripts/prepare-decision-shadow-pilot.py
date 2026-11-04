#!/usr/bin/env python3
"""Prepare a private, immutable shadow pilot plan with explicit missing inputs."""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_runtime.artifacts import sealed
from decision_runtime.contracts import canonical_json, parse_json
from scripts.lib.decision_shadow_pilot import prepare, require, utc_now

SOURCES = ("scripts/prepare-decision-shadow-pilot.py", "scripts/lib/decision_shadow_pilot.py",
           "scripts/lib/decision_shadow_sli.py", "decision_runtime/artifacts.py", "decision_runtime/contracts.py")


def source_identity():
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, timeout=5).strip()
    files = {}
    for name in SOURCES:
        current = (ROOT / name).read_bytes()
        committed = subprocess.check_output(["git", "show", f"{commit}:{name}"], cwd=ROOT, timeout=5)
        require(current == committed, "Commit pilot sources before preparing a plan")
        files[name] = hashlib.sha256(current).hexdigest()
    return commit, files


def bounded_file(path, limit):
    require(path.is_file() and not path.is_symlink(), "Use a regular input file")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    require(len(raw) <= limit, "Pilot input exceeds its size limit")
    return raw


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--config-sha256", required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--profile-file-sha256", required=True)
    parser.add_argument("--profile-identity", choices=("coordinator_json_bytes", "runtime_fingerprint"), default="coordinator_json_bytes")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        private = (ROOT / "docs/private").resolve()
        output = args.output.absolute()
        require(private.is_relative_to(ROOT.resolve()) and output.resolve().is_relative_to(private)
                and output.resolve() != private and not output.exists() and not output.is_symlink(),
                "Preserve evidence; use a new private output under docs/private")
        config_raw = bounded_file(args.config, 64 * 1024)
        require(hashlib.sha256(config_raw).hexdigest() == args.config_sha256, "Config file pin differs")
        profile_raw = bounded_file(args.profile, 1024 * 1024)
        config, profile = parse_json(config_raw), parse_json(profile_raw.decode("utf-8"))
        commit, files = source_identity()
        report = sealed(prepare(config, profile, profile_raw, args.profile_file_sha256, args.profile_identity,
                                prepared_at=utc_now(), source_commit=commit, source_files=files))
        require(source_identity() == (commit, files), "Pilot sources changed during preparation")
        encoded = (canonical_json(report) + "\n").encode("utf-8")
        output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
        print(f"Pilot plan: {report['status']}; {len(report['missingFields'])} missing fields; {output}")
        return 2 if report["missingFields"] else 0
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"Cannot prepare pilot plan: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
