#!/usr/bin/env python3
"""Rebuild finalization from pinned submitted reviews and compare both output files."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.lib.decision_public_sources import private_directory, write_json_new
from scripts.lib.decision_review_finalization import verify_finalization
from scripts.lib.decision_review_finalization_cli import add_inputs, bindings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    add_inputs(parser)
    for name in ('finalization', 'dataset'):
        parser.add_argument('--' + name, type=Path, required=True)
        parser.add_argument('--' + name + '-file-sha256', required=True)
    args = parser.parse_args(argv)
    try:
        first, second, comparison, comparison_sha, adjudication = bindings(args)
        report = verify_finalization(first, second, comparison, comparison_sha,
            args.finalization, args.finalization_file_sha256, args.dataset, args.dataset_file_sha256, adjudication)
        directory = private_directory(ROOT, args.output_dir)
        write_json_new(directory / 'verification.json', report)
    except Exception as error:
        print('Cannot verify review finalization: ' + type(error).__name__ + ': ' + str(error)[:200], file=sys.stderr)
        return 1
    print('status=pass cases=' + str(report['caseCount']) + ' modelCallsDuringVerification=0 qualification=not_assessed')
    return 0


if __name__ == '__main__': raise SystemExit(main())
