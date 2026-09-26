#!/usr/bin/env python3
"""Rebuild a saved snapshot from its copied CSV and verify its optional previous-bundle comparison."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_runtime.artifacts import read_json
from scripts.lib.report_snapshot import verify_snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--previous-bundle', type=Path)
    args = parser.parse_args()
    try:
        previous = read_json(args.previous_bundle) if args.previous_bundle else None
        receipt = verify_snapshot(args.snapshot, previous=previous)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print('verified', receipt['comparison']['status'], receipt['bundleSha256'])


if __name__ == '__main__':
    main()
