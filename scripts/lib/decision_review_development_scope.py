"""Standalone scope for submitted development labels, outside the serving package."""
from copy import deepcopy

from decision_runtime.annotations import finalize_reviews, prepare_review
from decision_runtime.artifacts import sealed, verify_seal
from decision_runtime.contracts import canonical_json, fields, fingerprint
from decision_runtime.evaluation import validate_dataset
from scripts.lib.decision_review_session import AUTHORITY_FLAGS
from scripts.lib.decision_shadow_pilot import require

SCOPE_FIELDS = {'usage', 'developmentLabelKind', 'qualifiedForRouting',
                'classificationAccuracyMeasured', 'qualification', 'sha256', *AUTHORITY_FLAGS}
SOURCE_FIELDS = ('id', 'family', 'groupId', 'provenance', 'request')


def for_native_diagnostics(dataset):
    """Validate and expose original cases only to the pinned model-free diagnostic."""
    require(isinstance(dataset, dict), 'Expected a finalized review dataset')
    if dataset.get('usage') != 'development-only':
        return validate_dataset(dataset)
    verify_seal(dataset, 'agat.decision.dataset.v1')
    require(dataset.get('developmentLabelKind') == 'submitted-review'
            and dataset.get('qualifiedForRouting') is False
            and dataset.get('classificationAccuracyMeasured') is False
            and dataset.get('qualification') == 'not_assessed'
            and all(dataset.get(key) is False for key in AUTHORITY_FLAGS),
            'Development-only submitted reviews cannot infer human or routing authority')
    core = {key: deepcopy(value) for key, value in dataset.items() if key not in SCOPE_FIELDS}
    validate_dataset(core)
    require(core['id'].endswith('.reviewed') and all(case['split'] == 'development'
            and case['labelSource'] == 'expert-reviewed' for case in core['cases']),
            'Development-only submitted reviews cannot become calibration, holdout or training data')
    annotation = core.get('annotation')
    fields(annotation, {'poolSha256', 'splitSeed', 'reviewSha256', 'adjudicationSha256', 'splitCounts'})
    pool = {'schemaVersion': 'agat.decision.pool.v1', 'id': core['id'][:-len('.reviewed')],
            'cases': [{key: case[key] for key in SOURCE_FIELDS} for case in core['cases']]}
    prepare_review(pool, annotation['splitSeed'])
    require(fingerprint(pool) == annotation['poolSha256']
            and canonical_json(annotation['splitCounts']) == canonical_json({'development': len(core['cases'])}),
            'Development-only review annotation differs from the complete original pool')
    # Reuse the existing strict consensus/adjudication validation on each case.
    # Review IDs and timestamps stay declarations, not authenticated identities.
    for case, original in zip(core['cases'], pool['cases'], strict=True):
        review = case.get('review')
        fields(review, {'status', 'records'})
        records = review['records']
        require(isinstance(records, list) and len(records) in (2, 3), 'Use complete declared review records')
        single = {**pool, 'cases': [original]}
        inputs = []
        for record in records:
            fields(record, {'reviewerId', 'reviewedAt', 'id', 'inputSha256', 'expectedOptionId', 'rationale'})
            submitted = prepare_review(single, annotation['splitSeed'])
            submitted.update(reviewerId=record['reviewerId'], reviewedAt=record['reviewedAt'],
                             labels=[{key: record[key] for key in ('id', 'inputSha256', 'expectedOptionId', 'rationale')}])
            inputs.append(submitted)
        expected = finalize_reviews(inputs[0], inputs[1], inputs[2] if len(inputs) == 3 else None)['cases'][0]
        require(canonical_json(expected) == canonical_json(case), 'Development review record or split changed')
    return core


def scope_development_reviews(dataset):
    if dataset.get('usage') == 'development-only':
        for_native_diagnostics(dataset)
        return deepcopy(dataset)
    validate_dataset(dataset)
    require(all(case['split'] == 'development' and case['labelSource'] == 'expert-reviewed'
                for case in dataset['cases']), 'Scope only a complete finalized development review dataset')
    result = sealed({**{key: deepcopy(value) for key, value in dataset.items() if key != 'sha256'},
        'usage': 'development-only',
        'developmentLabelKind': 'submitted-review', 'qualifiedForRouting': False,
        **{key: False for key in AUTHORITY_FLAGS}, 'classificationAccuracyMeasured': False,
        'qualification': 'not_assessed'})
    for_native_diagnostics(result)
    return result
