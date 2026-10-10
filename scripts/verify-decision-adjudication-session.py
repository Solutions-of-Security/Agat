#!/usr/bin/env python3
"""Verify six pinned adjudication artifacts; preserve draft, failure and submission status."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.lib.decision_adjudication_verification import verify_session
from scripts.lib.decision_public_sources import private_directory, write_json_new

INPUTS = ('session', 'input-review', 'output-review', 'packet', 'blank-review', 'case-notes')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in INPUTS:
        parser.add_argument('--' + name, type=Path, required=True)
        parser.add_argument('--' + name + '-file-sha256', required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    bindings = tuple(getattr(args, name.replace('-', '_') + suffix)
                     for name in INPUTS for suffix in ('', '_file_sha256'))
    try:
        report = verify_session(*bindings)
        directory = private_directory(ROOT, args.output_dir)
        write_json_new(directory / 'verification.json', report)
    except Exception as error:
        print('Cannot verify adjudication session: ' + type(error).__name__ + ': ' + str(error)[:200],
              file=sys.stderr)
        return 1
    print('status=pass sessionStatus=' + report['sessionStatus']
          + ' completeAdjudicationArtifact=' + str(report['completeAdjudicationArtifact']).lower()
          + ' modelCallsDuringVerification=0 qualification=not_assessed')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
