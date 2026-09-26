#!/usr/bin/env python3
"""Run a bounded sustained decision HTTP probe on approved development inputs."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from decision_runtime.artifacts import write_new
from decision_runtime.evaluation import load_dataset
from scripts.lib.decision_endurance import endurance


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--duration-s', type=int, default=600)
    parser.add_argument('--window-s', type=int, default=30)
    parser.add_argument('--max-requests', type=int, default=5000)
    parser.add_argument('--warmup', type=int, default=3)
    parser.add_argument('--timeout-ms', type=int, default=10000)
    parser.add_argument('--process-pid', type=int, action='append', default=[])
    args = parser.parse_args()
    if args.output.exists(): parser.error('Evidence already exists; select a new output path')
    report = endurance(load_dataset(args.dataset), args.url, duration_s=args.duration_s, window_s=args.window_s,
                       max_requests=args.max_requests, warmup=args.warmup, timeout_ms=args.timeout_ms,
                       process_pids=args.process_pid, progress=lambda w, _: print(
                           f"window={w['index']} attempts={w['summary']['attempts']} p95_ms={w['summary']['scoredWallMs']['p95']}", flush=True))
    write_new(args.output, report)
    print(f"{report['status']}: {report['stoppedReason']}; calls={report['summary']['attempts']}; {args.output}", flush=True)
    return int(report['status'] != 'observed')


if __name__ == '__main__': raise SystemExit(main())
