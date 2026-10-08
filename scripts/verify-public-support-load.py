#!/usr/bin/env python3
"""Verify whole public HTTP load against independently pinned files and Git sources."""
import argparse
import importlib.util
import json
from pathlib import Path
import platform
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_runtime.artifacts import sealed
from scripts.lib.decision_public_load_verification import verify
from scripts.lib.decision_public_sources import private_directory, write_json_new

VERIFIER_PATHS = ["scripts/verify-public-support-load.py", "scripts/lib/decision_public_load_verification.py",
    "scripts/test/test_decision_public_load_verification.py", "decision_runtime", "scripts/run-decision-arrival-rate.py",
    "scripts/run-temporal-real-rag.py", "scripts/profile-embedding-rag.py", "scripts/test/test_decision_public_primary.py"]
SPEC = importlib.util.spec_from_file_location("public_verification_sources", ROOT/"scripts/run-decision-arrival-rate.py")
launcher = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(launcher)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--context-profile", type=Path, required=True)
    parser.add_argument("--context-profile-file-sha256", required=True)
    parser.add_argument("--plan-file-sha256", required=True)
    parser.add_argument("--result-file-sha256", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        private = (ROOT/"docs/private").resolve()
        if not args.output_dir.resolve().is_relative_to(private) or args.output_dir.resolve() == private \
                or args.output_dir.exists() or args.output_dir.is_symlink():
            raise ValueError("Use a new private verification directory")
        # Freeze the verifier and its imports; historical measured code is read only.
        imports = {str(Path(module.__file__).resolve().relative_to(ROOT)) for module in list(sys.modules.values())
                   if getattr(module, "__file__", None) and Path(module.__file__).resolve().is_relative_to(ROOT)
                   and str(module.__file__).endswith(".py")}
        paths = sorted(set(VERIFIER_PATHS) | imports)
        commit, sources = launcher.frozen_sources(paths)
        result = verify(ROOT, args.evidence_dir, args.context_profile, context_sha=args.context_profile_file_sha256,
                        plan_sha=args.plan_file_sha256, result_sha=args.result_file_sha256)
        if launcher.frozen_sources(paths) != (commit, sources): raise ValueError("Verification source drift")
        result = sealed({key:value for key,value in result.items() if key != "sha256"} | {"verifierCommit":commit,"verifierSources":sources,
                        "verifierRuntime":{"python":platform.python_version(),"system":platform.system()}})
        directory = private_directory(ROOT, args.output_dir)
        write_json_new(directory/"verification.json", result)
        print(json.dumps({"status":result["status"],"summary":result["summary"],"physicalHttpAccounting":result["physicalHttpAccounting"]}))
        return 0 if result["status"] == "pass" else 2
    except Exception as error:
        print(f"Public load verification failed: {type(error).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__": raise SystemExit(main())
