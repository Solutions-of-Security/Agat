#!/usr/bin/env python3
"""Replay pinned real-primary matched workflow receipts without model calls."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.lib.decision_public_real_primary_verification import verify
from scripts.lib.decision_public_sources import private_directory, write_json_new


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('evidence-dir', 'context-profile', 'output-dir'):
        parser.add_argument('--'+name, type=Path, required=True)
    for name in ('context-profile-file-sha256', 'plan-file-sha256', 'result-file-sha256'):
        parser.add_argument('--'+name, required=True)
    args = parser.parse_args(argv)
    try:
        receipt = verify(ROOT, args.evidence_dir.resolve(), args.context_profile.resolve(), context_sha=args.context_profile_file_sha256,
            plan_sha=args.plan_file_sha256, result_sha=args.result_file_sha256)
        directory = private_directory(ROOT, args.output_dir); write_json_new(directory/'verification.json', receipt)
    except Exception as error:
        print('Cannot verify real-primary workflow: '+type(error).__name__+': '+str(error)[:200], file=sys.stderr); return 1
    print('status=pass modelCallsDuringVerification=0'); return 0


if __name__ == '__main__':
    raise SystemExit(main())
