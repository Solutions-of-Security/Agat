#!/usr/bin/env python3
"""Bind a pinned CSV source and explicit mapping, then calculate exact report metrics."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_runtime.artifacts import read_json, write_new
from scripts.lib.report_source_records import MAX_SOURCE_BYTES, calculate_source, source_markdown


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--mapping', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--markdown', type=Path)
    parser.add_argument('--decimal-places', type=int, default=3)
    args = parser.parse_args()
    outputs = [args.output] + ([args.markdown] if args.markdown else [])
    if len({p.resolve() for p in outputs}) != len(outputs) or any(p.exists() for p in outputs):
        parser.error('Use distinct new output paths')
    if any(not p.resolve().is_relative_to((ROOT / 'docs').resolve()) for p in outputs):
        parser.error('Report artifacts belong under docs')
    with args.source.open('rb') as handle: source = handle.read(MAX_SOURCE_BYTES + 1)
    report = calculate_source(source, read_json(args.mapping), decimal_places=args.decimal_places)
    rendered = source_markdown(report, source) if args.markdown else None
    write_new(args.output, report)
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        with args.markdown.open('x', encoding='utf-8') as handle: handle.write(rendered)
    print(report['status'], report['binding']['source']['sha256'])


if __name__ == '__main__': main()
