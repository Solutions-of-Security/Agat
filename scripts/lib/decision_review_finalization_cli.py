"""Shared argument binding for finalization and its offline replay."""
from pathlib import Path

from scripts.lib.decision_shadow_pilot import require

REVIEW_INPUTS = ('session', 'input-review', 'output-review')
ADJUDICATION_INPUTS = (*REVIEW_INPUTS, 'packet', 'blank-review', 'case-notes')


def add_inputs(parser):
    for prefix, names, required in (('first', REVIEW_INPUTS, True), ('second', REVIEW_INPUTS, True),
                                     ('adjudication', ADJUDICATION_INPUTS, False)):
        for name in names:
            parser.add_argument('--' + prefix + '-' + name, type=Path, required=required)
            parser.add_argument('--' + prefix + '-' + name + '-file-sha256', required=required)
    parser.add_argument('--comparison', type=Path, required=True)
    parser.add_argument('--comparison-file-sha256', required=True)
    parser.add_argument('--output-dir', type=Path, required=True)


def bindings(args):
    def read(prefix, names):
        return tuple(getattr(args, prefix + '_' + name.replace('-', '_') + suffix)
                     for name in names for suffix in ('', '_file_sha256'))
    first = read('first', REVIEW_INPUTS); second = read('second', REVIEW_INPUTS)
    adjudication = read('adjudication', ADJUDICATION_INPUTS)
    require(all(value is None for value in adjudication) or all(value is not None for value in adjudication),
            'Supply all six adjudication files and their external SHA values together')
    return first, second, args.comparison, args.comparison_file_sha256, adjudication if adjudication[0] is not None else None
