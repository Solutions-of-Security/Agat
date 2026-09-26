#!/usr/bin/env python3
"""Freeze and measure authored synthetic conflicts, missing facts and embedded instructions."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from decision_runtime.artifacts import write_new
from scripts.lib.decision_robustness import diagnose


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',required=True,type=Path)
    parser.add_argument('--plan-output',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--targets',type=int,nargs='+',default=[512,1024,2048])
    parser.add_argument('--max-tokens',type=int,default=2048)
    parser.add_argument('--cache-limit-mib',type=int,default=128)
    parser.add_argument('--time-budget-s',type=int,default=180)
    args=parser.parse_args()
    if args.plan_output.exists() or args.output.exists() or args.plan_output.resolve()==args.output.resolve():
        parser.error('Choose two distinct new evidence files')
    def backend():
        from decision_runtime.mlx_backend import MlxBackend
        return MlxBackend(args.manifest,args.max_tokens,cache_limit_mib=args.cache_limit_mib)
    result=diagnose(backend,lambda p:write_new(args.plan_output,p),targets=args.targets,max_tokens=args.max_tokens,
                    time_budget_s=args.time_budget_s,progress=lambda s,t,n:print(f'scenario={s} tokens={t} calls={n}',flush=True))
    write_new(args.output,result)
    print(f"{result['status']}: {result['summary']}; {args.output}")
    return int(result['status']=='incomplete')


if __name__=='__main__': raise SystemExit(main())
