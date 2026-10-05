#!/usr/bin/env python3
"""Generate an opt-in launchd plist without registering it with macOS."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.lib.decision_service import launch_agent, write_launch_agent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--python', type=Path, default=Path('.venv/decision/bin/python'))
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--policy', type=Path, required=True)
    parser.add_argument('--calibration', type=Path)
    parser.add_argument('--log-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--label', default='org.agat.decision-shadow')
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--max-tokens', type=int, default=2048)
    parser.add_argument('--cache-limit-mib', type=int, default=128)
    parser.add_argument('--wired-limit-mib', type=int)
    parser.add_argument('--inference-timeout-ms', type=int, default=2000)
    parser.add_argument('--throttle-s', type=int, default=30)
    args = parser.parse_args()
    try:
        config = launch_agent(**{k: v for k, v in vars(args).items() if k != 'output'})
        write_launch_agent(args.output, config)
    except (OSError, ValueError) as error:
        parser.exit(1, f'Cannot render service: {error}\n')
    print(f'Rendered {args.output}; service not installed or started')
    return 0


if __name__ == '__main__': raise SystemExit(main())
