#!/usr/bin/env python3
"""Reconstruct every blank development review task from its pinned context profile."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.lib import decision_public_development_review as review
from scripts.lib.decision_public_sources import private_directory, write_json_new


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--packet', type=Path, required=True)
    parser.add_argument('--packet-file-sha256', required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        receipt = review.verify_packet(ROOT, args.packet, args.packet_file_sha256)
        directory = private_directory(ROOT, args.output_dir)
        write_json_new(directory / 'verification.json', receipt)
    except Exception as error:
        print('Cannot verify development review: ' + type(error).__name__ + ': ' + str(error)[:200], file=sys.stderr)
        return 1
    print('status=pass modelCallsDuringVerification=0 referenceLabels=0')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
