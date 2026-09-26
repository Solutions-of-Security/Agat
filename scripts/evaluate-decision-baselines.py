#!/usr/bin/env python3
"""Measure a local generative baseline on the sealed development export."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.lib.decision_baselines import LoopbackJson, OllamaBaseline, compare_baselines, evaluate_generative
from decision_runtime.artifacts import read_json, write_new
from decision_runtime.evaluation import load_dataset


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    evaluate = commands.add_parser("evaluate")
    evaluate.add_argument("--url", required=True)
    evaluate.add_argument("--model", default="qwen3:8b")
    evaluate.add_argument("--expected-digest")
    evaluate.add_argument("--timeout", type=float, default=90)
    compare = commands.add_parser("compare")
    compare.add_argument("--generative", type=Path, required=True)
    compare.add_argument("--logits", type=Path, nargs="+", required=True)
    for command in (evaluate, compare):
        command.add_argument("--dataset", type=Path, required=True)
        command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; choose a new evidence path")
    dataset = load_dataset(args.dataset)
    if args.command == "evaluate":
        backend = OllamaBaseline(LoopbackJson(args.url, args.timeout), args.model, args.expected_digest)
        result = evaluate_generative(dataset, backend,
                                    lambda case, order, status: print(f"{case} {order}: {status}", flush=True))
        failures = sum(r[order]["status"] == "error" for r in result["cases"] for order in ("original", "reversed"))
    else:
        result = compare_baselines(dataset, read_json(args.generative), [read_json(p) for p in args.logits])
        failures = sum(m[order]["backendErrors"] for m in result["models"] for order in ("original", "reversed"))
    write_new(args.output, result)
    print(f"{result['status']}: {args.output}; backendErrors={failures}", flush=True)
    return int(failures > 0)


if __name__ == "__main__":
    raise SystemExit(main())
