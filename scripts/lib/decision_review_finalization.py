"""Bind a finalized dataset to submitted reviews and an optional disputed-case session."""
import hashlib

from decision_runtime.annotations import finalize_reviews
from decision_runtime.artifacts import sealed
from decision_runtime.contracts import canonical_json, fingerprint, parse_json
from scripts.lib.decision_adjudication_verification import verify_session as verify_adjudication
from scripts.lib.decision_public_sources import pinned_input
from scripts.lib.decision_review_adjudication import encoded, prepare_handoff
from scripts.lib.decision_review_pair import compare_pair
from scripts.lib.decision_review_session import AUTHORITY_FLAGS, MAX_REVIEW_BYTES
from scripts.lib.decision_shadow_pilot import require


def artifact_binding(binding):
    return {'sessionFileSha256': binding[1], 'inputReviewFileSha256': binding[3],
            'outputReviewFileSha256': binding[5]}


def prepare_finalization(first, second, comparison_path, comparison_sha, adjudication=None):
    comparison = parse_json(pinned_input(comparison_path, comparison_sha, MAX_REVIEW_BYTES))
    expected = compare_pair(first, second)
    require(canonical_json(comparison) == canonical_json(expected),
            'Comparison differs from the complete pinned source reviews')
    reviews = [parse_json(pinned_input(binding[4], binding[5], MAX_REVIEW_BYTES)) for binding in (first, second)]
    final_adjudication = None; adjudication_verification = None; adjudication_bindings = None
    if expected['requiresAdjudication']:
        require(adjudication is not None and len(adjudication) == 12,
                'Disagreements require all six pinned adjudication artifacts')
        adjudication_verification = verify_adjudication(*adjudication)
        require(adjudication_verification['completeAdjudicationArtifact'],
                'Use an explicitly submitted completed adjudication session')
        # Reconstruct the original handoff from the actual pair, not its declared source hashes.
        handoff = prepare_handoff(first, second, comparison_path, comparison_sha)
        for name, offset in (('packet.json', 6), ('adjudication.review.blank.json', 8), ('case-notes.json', 10)):
            require(pinned_input(adjudication[offset], adjudication[offset+1],
                                 256 * 1024 if offset == 6 else MAX_REVIEW_BYTES) == encoded(handoff[name]),
                    'Adjudication handoff differs from the pinned source pair')
        final_adjudication = parse_json(pinned_input(adjudication[4], adjudication[5], MAX_REVIEW_BYTES))
        adjudication_bindings = {**artifact_binding(adjudication), 'packetFileSha256': adjudication[7],
                                'blankReviewFileSha256': adjudication[9], 'caseNotesFileSha256': adjudication[11]}
    else:
        require(adjudication is None, 'Adjudication supplied without disagreements')
    dataset = finalize_reviews(*reviews, final_adjudication)
    raw = encoded(dataset)
    require(len(raw) <= MAX_REVIEW_BYTES, 'Finalized dataset exceeds the review byte bound')
    report = sealed({'schemaVersion': 'agat.decision.review-finalization.v1', 'status': 'finalized',
        'comparisonFileSha256': comparison_sha, 'comparisonSha256': expected['sha256'],
        'sourceReviewBindings': [artifact_binding(binding) for binding in (first, second)],
        'adjudicationBindings': adjudication_bindings,
        'poolSha256': expected['poolSha256'], 'splitSeed': expected['splitSeed'],
        'caseCount': expected['caseCount'], 'groupCount': expected['groupCount'],
        'agreedCases': expected['agreementCount'], 'adjudicatedCases': expected['disagreementCount'],
        'splitCounts': dataset['annotation']['splitCounts'],
        'datasetFileSha256': hashlib.sha256(raw).hexdigest(), 'datasetSha256': fingerprint(dataset),
        'datasetBytes': len(raw), 'reviewVerifications': expected['verifications'],
        'adjudicationVerification': adjudication_verification,
        'sourceReviewsRevalidated': True, 'handoffReconstructedFromSourceReviews': bool(adjudication),
        'labelsMaterialized': len(dataset['cases']),
        **{flag: False for flag in AUTHORITY_FLAGS},
        'expertiseInferredFromLabelSource': False, 'classificationAccuracyMeasured': False,
        'modelCallsDuringFinalization': 0, 'qualification': 'not_assessed'})
    return {'dataset.json': dataset, 'finalization.json': report}


def verify_finalization(first, second, comparison_path, comparison_sha,
                        report_path, report_sha, dataset_path, dataset_sha, adjudication=None):
    report_raw = pinned_input(report_path, report_sha, MAX_REVIEW_BYTES)
    dataset_raw = pinned_input(dataset_path, dataset_sha, MAX_REVIEW_BYTES)
    # Strict parsing rejects malformed/duplicate JSON before comparing expected canonical bytes.
    parse_json(report_raw); parse_json(dataset_raw)
    expected = prepare_finalization(first, second, comparison_path, comparison_sha, adjudication)
    require(report_raw == encoded(expected['finalization.json']) and dataset_raw == encoded(expected['dataset.json']),
            'Finalization receipt or dataset differs from submitted source artifacts')
    report = expected['finalization.json']
    return sealed({'schemaVersion': 'agat.decision.review-finalization-verification.v1', 'status': 'pass',
        'finalizationFileSha256': report_sha, 'datasetFileSha256': dataset_sha,
        'finalizationSha256': report['sha256'], 'datasetSha256': report['datasetSha256'],
        'caseCount': report['caseCount'], 'groupCount': report['groupCount'],
        'agreedCases': report['agreedCases'], 'adjudicatedCases': report['adjudicatedCases'],
        'sourceReviewsRevalidated': True, 'handoffReconstructedFromSourceReviews': bool(adjudication),
        **{flag: False for flag in AUTHORITY_FLAGS}, 'classificationAccuracyMeasured': False,
        'modelCallsDuringVerification': 0, 'qualification': 'not_assessed'})
