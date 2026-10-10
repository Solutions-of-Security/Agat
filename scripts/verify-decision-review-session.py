#!/usr/bin/env python3
"""Verify three pinned review artifacts without executing models or asserting human identity."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.lib.decision_review_session import verify_session
from scripts.lib.decision_public_sources import private_directory, write_json_new


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("session", "input-review", "output-review"):
        parser.add_argument("--" + name, type=Path, required=True)
        parser.add_argument("--" + name + "-file-sha256", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        receipt = verify_session(args.session, args.session_file_sha256, args.input_review,
                                 args.input_review_file_sha256, args.output_review, args.output_review_file_sha256)
        directory = private_directory(ROOT, args.output_dir)
        write_json_new(directory / "verification.json", receipt)
    except Exception as error:
        print("Cannot verify review session: " + type(error).__name__ + ": " + str(error)[:200], file=sys.stderr)
        return 1
    print("status=pass completeReviewArtifact=" + str(receipt["completeReviewArtifact"]).lower()
          + " modelCallsDuringVerification=0 qualification=not_assessed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
