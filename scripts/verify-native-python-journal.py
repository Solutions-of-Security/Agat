#!/usr/bin/env python3
"""Verify pinned native Python records, with explicit opt-in for incomplete traces."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.lib.decision_native_python_journal import canonical, verify_journal


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--journal-file-sha256", required=True)
    parser.add_argument("--expected-observer-sha256", required=True)
    parser.add_argument("--expected-identity-sha256")
    parser.add_argument("--expected-input-sha256", action="append")
    parser.add_argument("--allow-incomplete", action="store_true")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        receipt = verify_journal(args.journal, args.journal_file_sha256,
                                 expected_observer_sha256=args.expected_observer_sha256,
                                 expected_identity_sha256=args.expected_identity_sha256,
                                 expected_input_sha256s=args.expected_input_sha256)
        if not receipt["completeScoringTrace"] and not args.allow_incomplete:
            raise ValueError("Incomplete scoring trace requires --allow-incomplete for diagnostic output")
        directory = args.output_dir.resolve()
        if not directory.is_relative_to(ROOT / "docs/private") or directory == ROOT / "docs/private":
            raise ValueError("Use a new output directory under docs/private")
        directory.mkdir(parents=True, mode=0o700, exist_ok=False)
        path = directory / "verification.json"
        with path.open("xb") as stream:
            stream.write(canonical(receipt) + b"\n")
        path.chmod(0o600)
    except Exception as error:
        print("Cannot verify native Python journal: " + type(error).__name__ + ": " + str(error)[:200], file=sys.stderr)
        return 1
    print("status=" + receipt["status"] + " completeScoringTrace=" + str(receipt["completeScoringTrace"]).lower()
          + " modelCallsDuringVerification=0 qualification=not_assessed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
