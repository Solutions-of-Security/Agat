#!/usr/bin/env python3
"""Profile model load, first/warm inference and memory in a fresh local process."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from decision_runtime.artifacts import read_json, write_new
from decision_runtime.contracts import Policy
from decision_runtime.evaluation import load_dataset
from scripts.lib.decision_resources import profile_resources


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--max-tokens", type=int, default=2048)
    parser.add_argument("--rounds", type=int, default=4)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--time-budget-s", type=int, default=120)
    parser.add_argument("--cache-limit-mib", type=int, help="Runtime MLX free-cache limit, 0..4096 MiB; recorded in the execution profile")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Evidence already exists; select a new output path")
    policy = Policy.from_dict(read_json(args.policy)) if args.policy else Policy()
    dataset = load_dataset(args.dataset)

    def backend():
        from decision_runtime.mlx_backend import MlxBackend
        return MlxBackend(args.manifest, args.max_tokens, cache_limit_mib=args.cache_limit_mib)

    report = profile_resources(dataset, backend, policy=policy, rounds=args.rounds, warmup=args.warmup,
                               time_budget_s=args.time_budget_s,
                               progress=lambda phase, value, count: print(f"{phase}: {value}; measured={count}", flush=True))
    write_new(args.output, report)
    print(f"{report['status']}: load_ms={report['loadWallMs']} warm_p95_ms={report['summary']['scoredWallMs']['p95']} "
          f"peak_active_bytes={report['memory']['peakActiveBytes']}; {args.output}", flush=True)
    return int(report["status"] != "observed")


if __name__ == "__main__":
    raise SystemExit(main())
