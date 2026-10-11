#!/usr/bin/env python3
"""Verify all fixed-role finalization artifacts from one externally pinned manifest."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.lib.decision_public_sources import private_directory, write_json_new
from scripts.lib.decision_review_bundle import verify_bundle


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--bundle-file-sha256', required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = verify_bundle(args.bundle, args.bundle_file_sha256)
        directory = private_directory(ROOT, args.output_dir)
        write_json_new(directory/'verification.json', report)
    except Exception as error:
        print('Cannot verify review bundle: '+type(error).__name__+': '+str(error)[:200], file=sys.stderr)
        return 1
    print('status=pass cases='+str(report['caseCount'])+' modelCallsDuringVerification=0 qualification=not_assessed')
    return 0


if __name__ == '__main__': raise SystemExit(main())
