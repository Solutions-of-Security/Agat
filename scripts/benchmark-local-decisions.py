#!/usr/bin/env python3
"""Measure bounded local decision latency and overload separately from quality."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from decision_runtime.artifacts import write_new
from decision_runtime.evaluation import load_dataset
from scripts.lib.decision_performance import benchmark


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--rounds", type=int, default=4)
    parser.add_argument("--concurrency", type=int, nargs="+", default=[1, 2, 4])
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--timeout-ms", type=int, default=10000)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Evidence already exists; select a new output path")
    result = benchmark(load_dataset(args.dataset), args.url, rounds=args.rounds, concurrency=args.concurrency,
                       warmup=args.warmup, timeout_ms=args.timeout_ms,
                       progress=lambda phase, done, count: print(f"{phase}: {done} requests; concurrency={count}", flush=True))
    write_new(args.output, result)
    for phase in result["phases"]:
        summary = phase["summary"]
        print(f"concurrency={phase['concurrency']} scored={summary['scored']}/{summary['attempts']} "
              f"scored_p95_ms={summary['scoredWallMs']['p95']} failures={summary['failures']}", flush=True)
    print(f"{result['status']}: {args.output}", flush=True)
    return int(result["status"] != "observed")


if __name__ == "__main__":
    raise SystemExit(main())
