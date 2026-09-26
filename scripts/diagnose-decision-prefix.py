#!/usr/bin/env python3
"""Compare fresh scoring with isolated continuations of a shared state prefix."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from decision_runtime.artifacts import read_json,write_new
from decision_runtime.contracts import Policy
from decision_runtime.mlx_backend import MlxBackend
from scripts.lib.decision_prefix import diagnose_prefix


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-plan',type=Path,required=True)
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--policy',type=Path,required=True)
    parser.add_argument('--plan-output',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--time-budget-s',type=int,default=300)
    args=parser.parse_args()
    if args.output.exists() or args.plan_output.exists() or args.output.resolve()==args.plan_output.resolve():
        parser.error('Use two distinct new evidence paths')
    source=read_json(args.source_plan)
    result=diagnose_prefix(source,lambda:MlxBackend(args.manifest,source['maxInputTokens'],cache_limit_mib=128),
                          lambda plan:write_new(args.plan_output,plan),policy=Policy.from_dict(read_json(args.policy)),
                          time_budget_s=args.time_budget_s,
                          progress=lambda variant,case,count:print(f'{variant}, {case}: {count} scored requests',flush=True))
    write_new(args.output,result)
    print(f"{result['status']}; equivalent={result['equivalentUnderCriterion']}; {result['summary']}",flush=True)
    return 1 if result['status']=='incomplete' else 0 if result['equivalentUnderCriterion'] else 2


if __name__=='__main__': raise SystemExit(main())
