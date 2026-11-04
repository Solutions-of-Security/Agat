#!/usr/bin/env python3
"""Verify a complete continuous soak using its committed sources and private journal."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_runtime.artifacts import verify_seal, write_new
from scripts.lib.decision_soak_verification import PLAN_SCHEMA, committed_sources, verify_run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    plan = verify_seal(json.loads((args.directory / 'plan.json').read_bytes()), PLAN_SCHEMA)
    dataset = committed_sources(ROOT, plan)
    result = verify_run(args.directory, plan, json.loads((args.directory / 'launcher.json').read_bytes()), dataset)
    write_new(args.output, result)
    print(f"pass: blocks={result['blocks']} measured_ms={result['measuredMs']} calls={result['attempts']}")


if __name__ == '__main__':
    raise SystemExit(main())
