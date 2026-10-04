#!/usr/bin/env python3
"""Verify a completed shared endurance experiment without invoking either model."""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_runtime.artifacts import read_json, write_new
from scripts.lib.decision_shared_soak_verification import committed_inputs, verify_run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    allowed = (ROOT / 'docs/private').resolve()
    if not args.directory.resolve().is_relative_to(allowed) or not args.output.resolve().is_relative_to(allowed) or args.output.exists():
        parser.error('Use private evidence and a new private verification output')
    plan = read_json(args.directory / 'plan.json')
    dataset, profile = committed_inputs(ROOT, plan)
    proof = verify_run(args.directory, plan, dataset, profile)
    write_new(args.output, proof);args.output.chmod(0o600)
    print(f"verified: blocks={proof['counts']['blocks']} measured_ms={proof['measuredMs']} calls={proof['counts']['measuredCalls']}")


if __name__ == '__main__': raise SystemExit(main())
