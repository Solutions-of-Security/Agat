#!/usr/bin/env python3
"""Capture one stable local CSV export and prepare a new immutable report snapshot."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_runtime.artifacts import read_json
from scripts.lib.report_snapshot import prepare_snapshot, verify_snapshot, write_snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--recipe', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--previous-bundle', type=Path)
    args = parser.parse_args()
    if not args.output_dir.resolve().is_relative_to(ROOT / 'docs') or args.output_dir.exists() or args.output_dir.is_symlink():
        parser.error('Use a new output directory under docs')
    try:
        previous = read_json(args.previous_bundle) if args.previous_bundle else None
        receipt, files = prepare_snapshot(args.source, read_json(args.recipe), previous=previous)
        if files is None:
            print('unchanged', receipt['sourceSha256'], 'no output created')
            return
        write_snapshot(args.output_dir, receipt, files)
        verify_snapshot(args.output_dir, previous=previous)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print(receipt['status'], receipt['comparison']['status'], receipt['sourceRead']['sha256'])


if __name__ == '__main__':
    main()
