#!/usr/bin/env python3
"""Prepare all original development cases for independent review, without predictions."""
import argparse
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.lib import decision_public_development_review as review
from scripts.lib.decision_public_sources import private_directory, write_json_new

SPEC = importlib.util.spec_from_file_location('development_review_source_launcher', ROOT / 'scripts/run-decision-arrival-rate.py')
launcher = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(launcher)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context-profile', type=Path, required=True)
    parser.add_argument('--context-profile-file-sha256', required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        context_input = {'path': args.context_profile.absolute().relative_to(ROOT).as_posix(),
                         'sha256': args.context_profile_file_sha256}
        commit, files = launcher.frozen_sources(review.SOURCE_PATHS)
        packet, outputs = review.create_packet(ROOT, context_input, commit, files)
        if launcher.frozen_sources(review.SOURCE_PATHS) != (commit, files):
            raise ValueError('Review builder source drift')
        directory = private_directory(ROOT, args.output_dir)
        for name, value in outputs.items():
            write_json_new(directory / name, value)
        write_json_new(directory / 'packet.json', packet)
    except Exception as error:
        print('Cannot prepare development review: ' + type(error).__name__ + ': ' + str(error)[:200], file=sys.stderr)
        return 1
    print('status=awaiting_independent_human_review modelCalls=0 referenceLabels=0')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
