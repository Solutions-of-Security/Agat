#!/usr/bin/env python3
"""Install, inspect or stop only Agat's owned one-shot GUI login observer."""
from __future__ import annotations

import argparse
import os
import platform
import plistlib
import re
import runpy
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_runtime.artifacts import read_json, sealed, verify_seal, write_new
from decision_runtime.model_store import sha256_file
from scripts.lib.decision_resident_session import require

prepare = runpy.run_path(str(ROOT / "scripts/prepare-decision-session-observer.py"))
collector = runpy.run_path(str(ROOT / "scripts/observe-decision-resident-session.py"))
native = runpy.run_path(str(ROOT / "scripts/manage-decision-resident-deployment.py"))
LABEL = prepare["LABEL"]
SCHEMA = "agat.decision.session-observer-registration.v1"


def file_identity(path):
    require(path.is_file() and not path.is_symlink() and path.stat().st_uid == os.getuid()
            and path.stat().st_mode & 0o777 == 0o600, "Unexpected installed observer file owner/mode")
    info = path.stat()
    return {"device": info.st_dev, "inode": info.st_ino, "ctimeNs": info.st_ctime_ns, "fileSha256": sha256_file(path)}


def registration(root, config, installed):
    marker = verify_seal(read_json(root / "registration.json"), SCHEMA)
    require(type(marker["ownerUid"]) is int and marker["ownerUid"] == os.getuid() and marker["observerRoot"] == str(root)
            and marker["observerSeal"] == config["sha256"] and marker["label"] == LABEL
            and marker["installedPath"] == str(installed) and marker["installedIdentity"] == file_identity(installed), "Observer registration ownership differs")
    require(installed.read_bytes() == (root / f"{LABEL}.plist").read_bytes(), "Installed observer plist differs")
    return marker


def service(installed, diagnostic):
    state = native["launchctl"]("print", f"gui/{os.getuid()}/{LABEL}")
    diagnostic.write_text(state.stdout + state.stderr); diagnostic.chmod(0o600)
    if state.returncode:
        require(state.returncode == 113 and f'Could not find service "{LABEL}"' in state.stderr, "Cannot inspect observer registration")
        return None
    match = re.search(r"^\s*path = (.+)\s*$", state.stdout, re.MULTILINE)
    require(match is not None and Path(match[1].strip()) == installed, "Observer label belongs to a foreign plist")
    found = {}
    for key in ("pid", "runs", "last exit code"):
        suffix = r"(?:: [A-Z][A-Z0-9_]*)?" if key == "last exit code" else ""
        match = re.search(r"^\s*" + re.escape(key) + r" = (\d+)" + suffix + r"\s*$", state.stdout, re.MULTILINE)
        if match: found[key] = int(match[1])
    return found


def latest_observation(root, config, after=0):
    paths = sorted((root / "source/docs/private/session-events").glob("*/observer-run.json"))
    valid = []
    for path in paths:
        report = verify_seal(read_json(path), collector["REPORT_SCHEMA"])
        created = datetime.fromisoformat(report["createdAt"]).timestamp()
        if created >= after: valid.append((created, path, report))
    require(valid, "Observer has not saved a completed observation")
    _, path, report = max(valid, key=lambda value: (value[0], str(value[1])))
    require(report["status"] == "recorded" and report["routingEnabled"] is False
            and report["qualification"] == "not_assessed" and set(report["events"]) == {"boot", "login"}
            and report["observerSeal"] == config["sha256"] and report["baselineFileSha256"] == config["baselineFileSha256"]
            and report["snapshotCommit"] == config["snapshotCommit"], "Observer run did not record both bound event checks")
    for event, expected in report["events"].items():
        event_path = Path(expected["path"])
        require(event_path.parent == path.parent and event_path.name == f"{event}.json", "Event receipt escapes observation")
        require(collector["accepted_verification"](event_path, expected["exitCode"], report["baselineFileSha256"], event) == expected, "Observer event receipt differs")
    return {"path": str(path), "fileSha256": sha256_file(path), "report": report}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "install", "status", "stop"))
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--expected-seal", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = {"schemaVersion": "agat.decision.session-observer-management.v1", "createdAt": datetime.now(timezone.utc).isoformat(),
              "action": args.action, "status": "failed", "routingEnabled": False, "qualification": "not_assessed", "failure": None, "cleanupFailure": None}
    root = args.package.absolute()
    installed = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
    target = f"gui/{os.getuid()}/{LABEL}"
    created = []; claimed = False
    try:
        session = runpy.run_path(str(ROOT / "scripts/check-decision-resident-session.py"))
        session["private_output"](args.output)
    except (OSError, ValueError) as error:
        print(f"Cannot prepare observer output: {error}", file=sys.stderr); return 1
    handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    def interrupted(_sig, _frame): raise KeyboardInterrupt("Observer management interrupted")
    for sig in handlers: signal.signal(sig, interrupted)
    try:
        require(platform.system() == "Darwin", "Session observer management requires macOS")
        base = Path.home() / "Library/Application Support/Agat/decision-shadow/session-observers"
        require(not root.is_symlink() and root.resolve().parent == base.resolve(), "Use the owned Application Support observer package")
        config = collector["validate_package"](root, args.expected_seal)
        require(config["ownerUid"] == os.getuid(), "Foreign observer owner")
        commit, sources = prepare["source_identity"]()
        report.update(observerRoot=str(root), observerSeal=config["sha256"], sourceCommit=commit, sourceFiles=sources)
        expected = prepare["agent"](root, config["python"], config["sha256"])
        source = root / f"{LABEL}.plist"
        require(plistlib.loads(source.read_bytes()) == expected, "Observer job recipe differs")
        lint = subprocess.run(["/usr/bin/plutil", "-lint", str(source)], capture_output=True, text=True, timeout=5)
        require(lint.returncode == 0, "Observer plist failed native validation")
        diagnostic = args.output.with_suffix(".native-state.txt")
        require(not diagnostic.exists(), "Preserve previous native state evidence")
        if args.action == "check":
            report["status"] = "verified"
        elif args.action == "install":
            require(service(installed, diagnostic) is None and not installed.exists() and not installed.is_symlink()
                    and not (root / "registration.json").exists(), "Observer label/plist/registration already exists")
            installed.parent.mkdir(parents=True, exist_ok=True)
            native["exclusive_file"](installed, source.read_bytes(), created)
            marker = sealed({"schemaVersion": SCHEMA, "ownerUid": os.getuid(), "observerRoot": str(root), "observerSeal": config["sha256"],
                             "label": LABEL, "installedPath": str(installed), "installedIdentity": file_identity(installed)})
            from decision_runtime.contracts import canonical_json
            native["exclusive_file"](root / "registration.json", (canonical_json(marker)+"\n").encode(), created)
            claimed = True; started = time.time()
            require(native["launchctl"]("bootstrap", f"gui/{os.getuid()}", str(installed)).returncode == 0, "Observer bootstrap failed")
            deadline = time.monotonic()+520
            while time.monotonic() < deadline:
                state = service(installed, diagnostic)
                require(state is not None, "Observer job disappeared after bootstrap")
                if not state.get("pid") and state.get("last exit code") is not None:
                    require(state["last exit code"] == 0, "Observer job failed its current-session collection")
                    report["observation"] = latest_observation(root, config, started)
                    break
                time.sleep(0.2)
            else: raise RuntimeError("Observer did not complete within its two bounded verification budgets")
            registration(root, config, installed); report.update(status="installed", serviceState=state)
        else:
            marker = registration(root, config, installed)
            state = service(installed, diagnostic)
            if args.action == "status":
                require(state is not None, "Owned observer is not registered")
                report.update(status="registered", serviceState=state, observation=latest_observation(root, config))
            else:
                if state is not None: native["launchctl"]("bootout", target)
                require(native["wait_for_removal"](target, diagnostic, timeout=8), "Observer job remains registered")
                # Recheck original inode/ctime/bytes before removing our two files.
                registration(root, config, installed)
                installed.unlink(); (root / "registration.json").unlink()
                report.update(status="stopped", registration=marker)
    except (Exception, KeyboardInterrupt) as error:
        report["failure"] = {"type": type(error).__name__, "message": str(error)[:1000]}
        if args.action == "install" and created:
            for sig in handlers: signal.signal(sig, signal.SIG_IGN)
            try:
                if claimed:
                    state = service(installed, args.output.with_suffix(".native-state.txt"))
                    if state is not None: native["launchctl"]("bootout", target)
                    require(native["wait_for_removal"](target, args.output.with_suffix(".native-state.txt"), timeout=8), "Observer rollback job removal failed")
                for record in reversed(created): native["remove_owned_file"](record)
            except Exception as cleanup:
                report["cleanupFailure"] = {"type": type(cleanup).__name__, "message": str(cleanup)[:1000]}
    finally:
        for record in created: native["close_file_record"](record)
        for sig, handler in handlers.items(): signal.signal(sig, handler)
    try:
        write_new(args.output, sealed(report)); args.output.chmod(0o600)
    except (OSError, ValueError) as error:
        print(f"Cannot save observer management evidence: {error}", file=sys.stderr); return 1
    print(f"Observer {args.action}: {report['status']}; evidence {args.output}")
    return 0 if report["status"] != "failed" else 1


if __name__ == "__main__": raise SystemExit(main())
