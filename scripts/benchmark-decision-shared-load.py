#!/usr/bin/env python3
"""Compare separate, sequential and overlapping local generation/decision calls."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from decision_runtime.artifacts import write_new
from decision_runtime.evaluation import load_dataset
from scripts.lib.decision_baselines import LoopbackJson, OllamaBaseline
from scripts.lib.decision_shared_load import benchmark_shared


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decision-url", required=True)
    parser.add_argument("--primary-url", required=True)
    parser.add_argument("--primary-model", default="qwen3:8b")
    parser.add_argument("--expected-primary-digest", required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--time-budget-s", type=int, default=300)
    parser.add_argument("--decision-timeout-ms", type=int, default=10000)
    args = parser.parse_args()
    if args.output.exists(): parser.error("Evidence already exists; select a new output path")
    def primary():
        return OllamaBaseline(LoopbackJson(args.primary_url, 30), args.primary_model, args.expected_primary_digest)
    report = benchmark_shared(load_dataset(args.dataset), args.decision_url, primary, rounds=args.rounds,
                              warmup=args.warmup, time_budget_s=args.time_budget_s, timeout_ms=args.decision_timeout_ms,
                              progress=lambda phase, done: print(f"{phase}: {done} pairs", flush=True))
    write_new(args.output, report)
    print(f"{report['status']}: {args.output}", flush=True)
    return int(report["status"] != "observed")


if __name__ == "__main__": raise SystemExit(main())
