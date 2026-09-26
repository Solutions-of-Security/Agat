#!/usr/bin/env python3
"""Prepare an exact CSV report and an Agat process with mandatory approval."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_runtime.artifacts import read_json, write_new
from scripts.lib.report_process import prepare_process, verify_process
from scripts.lib.report_source_records import MAX_SOURCE_BYTES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--mapping', type=Path, required=True)
    parser.add_argument('--name', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--process-json', type=Path)
    parser.add_argument('--decimal-places', type=int, default=3)
    args = parser.parse_args()
    outputs = [args.output] + ([args.process_json] if args.process_json else [])
    if len({path.resolve() for path in outputs}) != len(outputs) or any(path.exists() for path in outputs):
        parser.error('Use distinct new output paths')
    if any(not path.resolve().is_relative_to((ROOT / 'docs').resolve()) for path in outputs):
        parser.error('Prepared reports belong under docs')
    with args.source.open('rb') as handle: source = handle.read(MAX_SOURCE_BYTES + 1)
    bundle = prepare_process(source, read_json(args.mapping), args.name, decimal_places=args.decimal_places)
    process = verify_process(bundle)
    write_new(args.output, bundle)
    if args.process_json: write_new(args.process_json, process)
    print(bundle['status'], bundle['source']['sha256'])


if __name__ == '__main__': main()
