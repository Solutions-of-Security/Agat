#!/usr/bin/env python3
"""Profile synthetic long contexts and exact context-limit rejection offline."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from decision_runtime.artifacts import write_new
from scripts.lib.decision_context import profile_context


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--max-tokens", type=int, default=2048, choices=(512, 1024, 2048, 4096))
    parser.add_argument("--targets", nargs="+", type=int)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--time-budget-s", type=int, default=120)
    parser.add_argument("--cache-limit-mib", type=int, default=128)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Evidence already exists; select a new output path")
    targets = args.targets if args.targets is not None else [t for t in (256, 512, 1024, 2048, 4096) if t <= args.max_tokens]

    def backend():
        from decision_runtime.mlx_backend import MlxBackend
        return MlxBackend(args.manifest, args.max_tokens, cache_limit_mib=args.cache_limit_mib)

    report = profile_context(backend, max_tokens=args.max_tokens, targets=targets, rounds=args.rounds,
                             time_budget_s=args.time_budget_s,
                             progress=lambda target, position, count: print(f"tokens={target} position={position} measured={count}", flush=True))
    write_new(args.output, report)
    print(f"{report['status']}: {args.output}", flush=True)
    return int(report["status"] != "observed")


if __name__ == "__main__":
    raise SystemExit(main())
