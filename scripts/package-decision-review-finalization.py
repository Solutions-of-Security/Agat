#!/usr/bin/env python3
"""Package pinned finalization inputs and outputs for portable offline replay."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.lib.decision_review_bundle import prepare_bundle, write_bundle
from scripts.lib.decision_review_finalization_cli import add_inputs, bindings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    add_inputs(parser)
    for name in ('finalization', 'dataset'):
        parser.add_argument('--'+name, type=Path, required=True)
        parser.add_argument('--'+name+'-file-sha256', required=True)
    args = parser.parse_args(argv)
    try:
        first, second, comparison, comparison_sha, adjudication = bindings(args)
        manifest, files = prepare_bundle(first, second, comparison, comparison_sha,
            args.finalization, args.finalization_file_sha256, args.dataset, args.dataset_file_sha256, adjudication)
        write_bundle(ROOT, args.output_dir, manifest, files)
    except Exception as error:
        print('Cannot package review finalization: '+type(error).__name__+': '+str(error)[:200], file=sys.stderr)
        return 1
    print('status=packaged cases='+str(manifest['caseCount'])+' modelCallsDuringPackaging=0 qualification=not_assessed')
    return 0


if __name__ == '__main__': raise SystemExit(main())
