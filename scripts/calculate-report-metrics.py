#!/usr/bin/env python3
"""Calculate exact report metrics from two structured records and optionally render Markdown."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_runtime.artifacts import read_json, write_new
from scripts.lib.report_arithmetic import calculate, markdown


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--markdown', type=Path)
    parser.add_argument('--decimal-places', type=int, default=3)
    args = parser.parse_args()
    outputs = [args.output] + ([args.markdown] if args.markdown else [])
    if len({p.resolve() for p in outputs}) != len(outputs) or any(p.exists() for p in outputs):
        parser.error('Choose distinct new output paths')
    if any(not p.resolve().is_relative_to((ROOT / 'docs').resolve()) for p in outputs):
        parser.error('Report artifacts must be stored under docs')
    result = calculate(read_json(args.input), decimal_places=args.decimal_places)
    rendered = markdown(result) if args.markdown else None
    write_new(args.output, result)
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        with args.markdown.open('x', encoding='utf-8') as handle: handle.write(rendered)
    print(result['status'], result['inputSha256'])


if __name__ == '__main__': main()
