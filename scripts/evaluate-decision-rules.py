#!/usr/bin/env python3
"""Freeze and run the explicit title heuristic on a sealed development-only export."""

import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_runtime.artifacts import write_new
from decision_runtime.evaluation import load_dataset
from scripts.lib.decision_rules import evaluate, make_plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--plan-output', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.plan_output.exists() or args.output.exists() or args.plan_output.resolve() == args.output.resolve():
        parser.error('Choose distinct new evidence paths')
    dataset = load_dataset(args.dataset)
    files = ['scripts/evaluate-decision-rules.py', 'scripts/lib/decision_rules.py', 'scripts/lib/decision_baselines.py']
    plan = make_plan(dataset, {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in files})
    write_new(args.plan_output, plan)
    result = evaluate(dataset, plan)
    write_new(args.output, result)
    for order, summary in result['summary'].items():
        print(order, {k: summary[k] for k in ('attempted', 'correct', 'accepted', 'acceptedErrors', 'coverage')})


if __name__ == '__main__': main()
