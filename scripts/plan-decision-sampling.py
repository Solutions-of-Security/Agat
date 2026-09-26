#!/usr/bin/env python3
"""Estimate optimistic holdout group capacity from a fixed blank review and declared criteria."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from decision_runtime.artifacts import read_json, write_new
from scripts.lib.decision_sampling import sampling_plan


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--blank-review',required=True,type=Path)
    parser.add_argument('--criteria',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    if args.output.exists(): parser.error('Evidence already exists; select a new output path')
    report=sampling_plan(read_json(args.blank_review),read_json(args.criteria))
    write_new(args.output,report)
    print(f"{report['status']}: zero-error groups={report['method']['independentAcceptedGroupsRequired']['count']}; {args.output}")
    return 0


if __name__=='__main__': raise SystemExit(main())
