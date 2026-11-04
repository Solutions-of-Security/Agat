#!/usr/bin/env python3
"""Finalize explicitly submitted reviews with source-bound handoff and dataset receipts."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.lib.decision_public_sources import private_directory, write_json_new
from scripts.lib.decision_review_finalization import prepare_finalization
from scripts.lib.decision_review_finalization_cli import add_inputs, bindings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    add_inputs(parser)
    args = parser.parse_args(argv)
    try:
        outputs = prepare_finalization(*bindings(args))
        directory = private_directory(ROOT, args.output_dir)
        for name, value in outputs.items(): write_json_new(directory / name, value)
    except Exception as error:
        print('Cannot finalize reviews: ' + type(error).__name__ + ': ' + str(error)[:200], file=sys.stderr)
        return 1
    report = outputs['finalization.json']
    print('status=finalized cases=' + str(report['caseCount']) + ' adjudicated=' + str(report['adjudicatedCases'])
          + ' modelCallsDuringFinalization=0 qualification=not_assessed')
    return 0


if __name__ == '__main__': raise SystemExit(main())
