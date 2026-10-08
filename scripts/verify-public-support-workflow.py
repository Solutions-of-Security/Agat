#!/usr/bin/env python3
"""Replay public workflow receipts offline against independently pinned bytes."""
import argparse
import importlib.util
import json
from pathlib import Path
import platform
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_runtime.artifacts import sealed
from scripts.lib.decision_public_sources import private_directory, write_json_new
from scripts.lib.decision_public_workflow_verification import verify

SPEC = importlib.util.spec_from_file_location("workflow_verifier_sources", ROOT/"scripts/run-decision-arrival-rate.py")
launcher = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(launcher)
PATHS = ["decision_runtime", "scripts/lib", "scripts/verify-public-support-workflow.py",
    "scripts/test/test_decision_public_workflow_verification.py", "scripts/test/test_decision_public_workflow_loss_verification.py", "scripts/run-decision-arrival-rate.py",
    "scripts/run-temporal-real-rag.py", "scripts/profile-embedding-rag.py"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("evidence-dir", "context-profile", "output-dir"): parser.add_argument("--"+name, type=Path, required=True)
    for name in ("context-profile-file-sha256", "plan-file-sha256", "result-file-sha256"): parser.add_argument("--"+name, required=True)
    args = parser.parse_args(argv)
    try:
        private = (ROOT/"docs/private").resolve()
        if not args.output_dir.resolve().is_relative_to(private) or args.output_dir.resolve() == private or args.output_dir.exists() or args.output_dir.is_symlink():
            raise ValueError("Use a new private verifier directory")
        imports = {str(Path(module.__file__).resolve().relative_to(ROOT)) for module in list(sys.modules.values())
                   if getattr(module, "__file__", None) and Path(module.__file__).resolve().is_relative_to(ROOT) and str(module.__file__).endswith(".py")}
        paths = sorted(set(PATHS)|imports); commit, sources = launcher.frozen_sources(paths)
        report = verify(ROOT, args.evidence_dir, args.context_profile, context_sha=args.context_profile_file_sha256,
                        plan_sha=args.plan_file_sha256, result_sha=args.result_file_sha256)
        if launcher.frozen_sources(paths) != (commit, sources): raise ValueError("Verifier source drift")
        report = sealed({key: value for key, value in report.items() if key != "sha256"} | {"verifierCommit": commit, "verifierSources": sources,
                        "verifierRuntime": {"python": platform.python_version(), "system": platform.system()}})
        directory = private_directory(ROOT, args.output_dir); write_json_new(directory/"verification.json", report)
        print(json.dumps({"status": report["status"], "scheduled": report["inventory"]["scheduled"], "physicalHttpAccounting": report["physicalHttpAccounting"]})); return 0
    except Exception as error:
        print("Workflow receipt verification failed: "+type(error).__name__, file=sys.stderr); return 1


if __name__ == "__main__": raise SystemExit(main())
