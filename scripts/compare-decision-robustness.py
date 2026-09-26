#!/usr/bin/env python3
"""Compare a local generative model with frozen synthetic direct-logit diagnostics."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from decision_runtime.artifacts import read_json,write_new
from scripts.lib.decision_baselines import LoopbackJson,OllamaBaseline
from scripts.lib.decision_robustness_baseline import compare_synthetic


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-plan',required=True,type=Path)
    parser.add_argument('--direct-result',required=True,type=Path)
    parser.add_argument('--url',required=True)
    parser.add_argument('--model',default='qwen3:8b')
    parser.add_argument('--expected-digest',required=True)
    parser.add_argument('--timeout-s',type=int,default=30,choices=range(1,61))
    parser.add_argument('--time-budget-s',type=int,default=300)
    parser.add_argument('--plan-output',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    if args.plan_output.exists() or args.output.exists() or args.plan_output.resolve()==args.output.resolve():
        parser.error('Choose two distinct new evidence paths')
    result=compare_synthetic(read_json(args.source_plan),read_json(args.direct_result),
               lambda:OllamaBaseline(LoopbackJson(args.url,args.timeout_s),args.model,args.expected_digest),
               lambda p:write_new(args.plan_output,p),time_budget_s=args.time_budget_s,
               progress=lambda case,order,status:print(f'{case} {order}: {status}',flush=True))
    write_new(args.output,result)
    print(f"{result['status']}: {result['summary']}; {args.output}")
    return int(result['status']=='incomplete')


if __name__=='__main__': raise SystemExit(main())
