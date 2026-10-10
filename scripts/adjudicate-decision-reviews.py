#!/usr/bin/env python3
"""Edit a pinned disputed-label handoff in a terminal and explicitly submit it."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_runtime.artifacts import sealed
from decision_runtime.contracts import parse_json
from scripts.lib.decision_adjudication_session import (
    SESSION_SCHEMA, interact_adjudication, validate_adjudication_progress, validate_handoff, verify_adjudication_values)
from scripts.lib.decision_blind_review import terminal_text
from scripts.lib.decision_public_sources import pinned_input, private_directory, write_json_new
from scripts.lib.decision_review_io import MAX_REVIEW_BYTES, checkpoint, source_identity as committed_source_identity
from scripts.lib.decision_review_session import MAX_SESSION_BYTES
from scripts.lib.decision_shadow_pilot import require, utc_now

SOURCES = ('scripts/adjudicate-decision-reviews.py', 'scripts/lib/decision_adjudication_session.py',
           'scripts/lib/decision_blind_review.py', 'scripts/lib/decision_review_io.py',
           'scripts/lib/decision_review_session.py', 'scripts/lib/decision_public_sources.py',
           'scripts/lib/decision_shadow_pilot.py', 'scripts/lib/decision_shadow_sli.py',
           'scripts/lib/decision_stage_inventory.py', 'scripts/lib/decision_caller_inventory.py',
           'decision_runtime/__init__.py', 'decision_runtime/annotations.py', 'decision_runtime/artifacts.py',
           'decision_runtime/contracts.py', 'decision_runtime/evaluation.py', 'decision_runtime/engine.py',
           'decision_runtime/calibration.py')


def source_identity(root):
    return committed_source_identity(root, SOURCES)


def main(argv=None, *, input_stream=None, output_stream=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('packet', 'blank-review', 'case-notes', 'review'):
        parser.add_argument('--' + name, type=Path, required=True)
        parser.add_argument('--' + name + '-file-sha256', required=True)
    parser.add_argument('--reviewer-id', required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    stream = input_stream if input_stream is not None else sys.stdin
    output = output_stream if output_stream is not None else sys.stdout
    directory = None; report = None
    try:
        require(stream.isatty() and output.isatty(), 'Use interactive input and output terminals')
        bindings = [(getattr(args, name), getattr(args, name + '_file_sha256'),
                     MAX_SESSION_BYTES if name == 'packet' else MAX_REVIEW_BYTES)
                    for name in ('packet', 'blank_review', 'case_notes', 'review')]
        originals = [pinned_input(*binding) for binding in bindings]
        packet, blank, notes = validate_handoff(*[parse_json(raw) for raw in originals[:3]],
                                                blank_raw=originals[1], notes_raw=originals[2])
        initial = parse_json(originals[3])
        review = validate_adjudication_progress(initial, args.reviewer_id, packet, blank)
        initial_labels = deepcopy(review['labels']); identity = source_identity(ROOT)
        started = utc_now(); directory = private_directory(ROOT, args.output_dir)
        review['reviewerId'] = args.reviewer_id
        report = {'schemaVersion': SESSION_SCHEMA, 'status': 'failed', 'startedAt': started,
                  'finishedAt': None, 'endReason': None, 'sourceCommit': identity[0], 'sourceFiles': identity[1],
                  'inputReviewFileSha256': args.review_file_sha256, 'poolSha256': review['poolSha256'],
                  'reviewerId': args.reviewer_id,
                  'existingAnswers': sum(label['expectedOptionId'] is not None for label in review['labels']),
                  'newAnswers': 0, 'revisedAnswers': 0, 'clearedAnswers': 0, 'remainingAnswers': None,
                  'submissionConfirmed': False, 'outputReviewFileSha256': None,
                  'reviewerIdentityVerified': False, 'humanExecutionVerified': False,
                  'independentReviewVerified': False, 'expertQualificationsVerified': False,
                  'modelCalls': 0, 'routingEnabled': False, 'qualification': 'not_assessed', 'failureType': None,
                  'packetFileSha256': args.packet_file_sha256, 'blankReviewFileSha256': args.blank_review_file_sha256,
                  'caseNotesFileSha256': args.case_notes_file_sha256, 'disputedCaseIds': packet['disputedCaseIds'],
                  'distinctDeclaredReviewerId': True, 'handoffSourcesRevalidated': False}
        checkpoint(directory, review)
        def unchanged():
            require(source_identity(ROOT)[1] == identity[1], 'Adjudication sources changed during the session')
            require([pinned_input(*binding) for binding in bindings] == originals,
                    'Adjudication input bytes changed during the session')
        def save(candidate):
            unchanged(); validate_adjudication_progress(candidate, args.reviewer_id, packet, blank)
            checkpoint(directory, candidate)
        review, reason = interact_adjudication(review, packet, notes, stream, output, save)
        unchanged(); validate_adjudication_progress(review, args.reviewer_id, packet, blank)
        remaining = sum(label['expectedOptionId'] is None for label in review['labels'])
        finished = utc_now(); submitted = reason == 'submitted'
        require(not submitted or remaining == 0, 'Submission cannot contain blank answers')
        if submitted: review['reviewedAt'] = finished
        checkpoint(directory, review)
        changes = list(zip(initial_labels, review['labels']))
        report.update(status='completed' if submitted else 'partial', finishedAt=finished, endReason=reason,
            newAnswers=sum(before['expectedOptionId'] is None and after['expectedOptionId'] is not None for before, after in changes),
            revisedAnswers=sum(before['expectedOptionId'] is not None and after['expectedOptionId'] is not None
                               and before != after for before, after in changes),
            clearedAnswers=sum(before['expectedOptionId'] is not None and after['expectedOptionId'] is None for before, after in changes),
            remainingAnswers=remaining, submissionConfirmed=submitted,
            outputReviewFileSha256=hashlib.sha256((directory / 'review.json').read_bytes()).hexdigest())
        receipt = sealed(report)
        verify_adjudication_values(receipt, initial, review, packet, blank,
            input_sha=args.review_file_sha256, output_sha=report['outputReviewFileSha256'],
            packet_sha=args.packet_file_sha256, blank_sha=args.blank_review_file_sha256, notes_sha=args.case_notes_file_sha256)
        write_json_new(directory / 'session.json', receipt)
        output.write(f"Adjudication {report['status']}: {remaining} remaining. Output: {terminal_text(str(directory))}\n")
        output.flush(); return 0 if submitted else 2
    except Exception as error:
        if directory is not None and report is not None:
            report.update(status='failed', finishedAt=utc_now(), failureType=type(error).__name__)
            if (directory / 'review.json').is_file():
                report['outputReviewFileSha256'] = hashlib.sha256((directory / 'review.json').read_bytes()).hexdigest()
            if not (directory / 'session.json').exists(): write_json_new(directory / 'session.json', sealed(report))
        print(f'Cannot complete adjudication: {terminal_text(str(error))[:1000]}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
