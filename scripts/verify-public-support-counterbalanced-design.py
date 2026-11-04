#!/usr/bin/env python3
"""Replay a sealed prospective replication design without model or network calls."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from scripts.lib.decision_public_counterbalance import verify_plan
from scripts.lib.decision_public_sources import private_directory, write_json_new


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('plan', 'context-profile', 'output-dir'): parser.add_argument('--'+name, type=Path, required=True)
    for name in ('plan-file-sha256', 'context-profile-file-sha256'): parser.add_argument('--'+name, required=True)
    args = parser.parse_args(argv)
    try:
        receipt = verify_plan(ROOT, args.plan.resolve(), args.plan_file_sha256,
            args.context_profile.resolve(), args.context_profile_file_sha256)
        directory = private_directory(ROOT, args.output_dir); write_json_new(directory/'verification.json', receipt)
    except Exception as error:
        print('Cannot verify counterbalanced design: '+type(error).__name__+': '+str(error)[:200], file=sys.stderr); return 1
    print('status=pass modelCallsDuringVerification=0'); return 0


if __name__ == '__main__': raise SystemExit(main())
