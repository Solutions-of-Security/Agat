"""Bound disputed-label sessions; declared participants are not authenticated humans."""
from copy import deepcopy
import hashlib
import re

from decision_runtime.annotations import REVIEW_SCHEMA, validate_pool
from decision_runtime.artifacts import sealed
from decision_runtime.contracts import Request, canonical_json, fields, fingerprint, string
from scripts.lib.decision_blind_review import MAX_CASES, interact, task_text, terminal_text, validate_progress
from scripts.lib.decision_review_session import AUTHORITY_FLAGS, verify_values
from scripts.lib.decision_shadow_pilot import require

SESSION_SCHEMA = 'agat.decision.adjudication-session.v1'
SESSION_EXTRA_FIELDS = {'packetFileSha256', 'blankReviewFileSha256', 'caseNotesFileSha256',
                        'disputedCaseIds', 'distinctDeclaredReviewerId', 'handoffSourcesRevalidated'}
PACKET_FIELDS = {'schemaVersion', 'status', 'comparisonFileSha256', 'comparisonSha256', 'sourceReviewBindings',
                 'poolSha256', 'splitSeed', 'sourceCaseCount', 'sourceGroupCount', 'disputedCaseCount',
                 'disputedGroupCount', 'disputedCaseIds', 'declaredReviewerIds', 'adjudicatorId', 'reviewedAt',
                 'resolvedCases', 'outputFiles', 'reviewerIdentityVerified', 'humanExecutionVerified',
                 'independentReviewVerified', 'expertQualificationsVerified', 'ownersAppointed',
                 'referenceLabelsCreated', 'modelCallsDuringPreparation', 'classificationAccuracyMeasured',
                 'routingEnabled', 'qualification', 'sha256'}
REVIEW_FIELDS = {'schemaVersion', 'pool', 'poolSha256', 'splitSeed', 'reviewerId', 'reviewedAt', 'labels'}


def digest(value):
    require(isinstance(value, str) and re.fullmatch(r'[a-f0-9]{64}', value), 'Invalid handoff SHA')


def projected(review, disputed_ids):
    view = deepcopy(review)
    source = {case['id']: case for case in review['pool']['cases']}
    view['pool']['cases'] = [deepcopy(source[case_id]) for case_id in disputed_ids]
    view['poolSha256'] = fingerprint(view['pool'])
    return view


def validate_handoff(packet, blank, notes, *, blank_raw, notes_raw):
    fields(packet, PACKET_FIELDS)
    require(packet['schemaVersion'] == 'agat.decision.review-adjudication-handoff.v1'
            and packet['sha256'] == fingerprint({key: value for key, value in packet.items() if key != 'sha256'})
            and packet['status'] == 'awaiting_adjudication', 'Use a sealed awaiting-adjudication handoff')
    for flag in (*AUTHORITY_FLAGS, 'ownersAppointed', 'classificationAccuracyMeasured'):
        require(packet[flag] is False, 'Handoff grants unsupported authority')
    for key in ('referenceLabelsCreated', 'modelCallsDuringPreparation', 'resolvedCases'):
        require(type(packet[key]) is int and packet[key] == 0, 'Handoff already grants decisions or model work')
    require(packet['adjudicatorId'] is None and packet['reviewedAt'] is None
            and packet['qualification'] == 'not_assessed', 'Handoff is not an unassigned draft')
    for key in ('comparisonFileSha256', 'comparisonSha256', 'poolSha256'): digest(packet[key])
    bindings = packet['sourceReviewBindings']
    require(isinstance(bindings, list) and len(bindings) == 2, 'Expected two declared source bindings')
    for binding in bindings:
        fields(binding, {'sessionFileSha256', 'initialReviewFileSha256', 'savedReviewFileSha256'})
        for value in binding.values(): digest(value)
    reviewers = packet['declaredReviewerIds']
    require(isinstance(reviewers, list) and len(reviewers) == 2, 'Expected two declared reviewer IDs')
    for reviewer in reviewers: string(reviewer, 100, identifier=True)
    require(reviewers[0] != reviewers[1], 'Source reviewer IDs must differ')
    fields(blank, REVIEW_FIELDS); pool = validate_pool(blank['pool'])
    require(blank['schemaVersion'] == REVIEW_SCHEMA and blank['poolSha256'] == packet['poolSha256'] == fingerprint(pool)
            and blank['splitSeed'] == packet['splitSeed'] and len(pool['cases']) <= MAX_CASES,
            'Handoff pool or split binding differs')
    string(blank['splitSeed'], 100)
    require(blank['reviewerId'] is None and blank['reviewedAt'] is None, 'Use the original unassigned blank')
    ids = packet['disputedCaseIds']; source_order = [case['id'] for case in pool['cases']]
    require(isinstance(ids, list) and ids and all(isinstance(value, str) for value in ids)
            and len(ids) == len(set(ids)) and set(ids) <= set(source_order)
            and ids == [value for value in source_order if value in set(ids)], 'Disputed inventory or order differs')
    require(isinstance(blank['labels'], list) and len(blank['labels']) == len(ids)
            and all(isinstance(label, dict) and label.get('id') == case_id
                    and label.get('expectedOptionId') is None and label.get('rationale') is None
                    for label, case_id in zip(blank['labels'], ids)), 'Handoff contains decisions or another inventory')
    view = projected(blank, ids)
    # Existing validator checks exact label keys, input SHA, options and review metadata on the projected tasks.
    validate_progress(view, 'unassigned-adjudication-validation')
    source = {case['id']: case for case in pool['cases']}
    expected_counts = {'sourceCaseCount': len(pool['cases']), 'sourceGroupCount': len({case['groupId'] for case in pool['cases']}),
                       'disputedCaseCount': len(ids), 'disputedGroupCount': len({source[case_id]['groupId'] for case_id in ids})}
    for key, count in expected_counts.items():
        require(type(packet[key]) is int and packet[key] == count, 'Handoff case or group count differs')
    fields(packet['outputFiles'], {'adjudication.review.blank.json', 'case-notes.json'})
    for name, raw, value in (('adjudication.review.blank.json', blank_raw, blank), ('case-notes.json', notes_raw, notes)):
        pin = fields(packet['outputFiles'][name], {'sha256', 'bytes'})
        digest(pin['sha256'])
        require(type(pin['bytes']) is int and pin['bytes'] == len(raw) and pin['sha256'] == hashlib.sha256(raw).hexdigest()
                and raw == (canonical_json(value) + '\n').encode(), 'Original handoff output bytes differ')
    fields(notes, {'schemaVersion', 'comparisonSha256', 'poolSha256', 'splitSeed', 'declaredReviewerIds', 'cases',
                   'reviewerIdentityVerified', 'humanExecutionVerified', 'expertQualificationsVerified',
                   'referenceLabelsCreated', 'routingEnabled', 'qualification'})
    require(notes['schemaVersion'] == 'agat.decision.review-adjudication-notes.v1'
            and all(notes[key] == packet[key] for key in ('comparisonSha256', 'poolSha256', 'splitSeed', 'declaredReviewerIds')),
            'Case notes handoff binding differs')
    for flag in ('reviewerIdentityVerified', 'humanExecutionVerified', 'expertQualificationsVerified', 'routingEnabled'):
        require(notes[flag] is False, 'Case notes grant unsupported authority')
    require(type(notes['referenceLabelsCreated']) is int and notes['referenceLabelsCreated'] == 0
            and notes['qualification'] == 'not_assessed', 'Case notes grant labels or qualification')
    require(isinstance(notes['cases'], list) and len(notes['cases']) == len(ids), 'Case notes inventory differs')
    for note, case_id, label in zip(notes['cases'], ids, blank['labels']):
        fields(note, {'case', 'inputSha256', 'firstOptionId', 'secondOptionId', 'firstRationale', 'secondRationale'})
        require(note['case'] == source[case_id] and note['inputSha256'] == label['inputSha256'], 'Case notes source changed')
        options = {option.id for option in Request.from_dict(source[case_id]['request']).options}
        require(isinstance(note['firstOptionId'], str) and isinstance(note['secondOptionId'], str)
                and note['firstOptionId'] in options and note['secondOptionId'] in options
                and note['firstOptionId'] != note['secondOptionId'], 'Case notes do not describe a disagreement')
        string(note['firstRationale'], 4000); string(note['secondRationale'], 4000)
    return deepcopy(packet), deepcopy(blank), deepcopy(notes)


def validate_adjudication_progress(review, reviewer_id, packet, blank):
    fields(review, REVIEW_FIELDS); string(reviewer_id, 100, identifier=True)
    require(reviewer_id not in packet['declaredReviewerIds'], 'Adjudication needs a third declared reviewer ID')
    for key in ('schemaVersion', 'pool', 'poolSha256', 'splitSeed'):
        require(review[key] == blank[key], 'Adjudication changed the full source pool or split')
    validate_progress(projected(review, packet['disputedCaseIds']), reviewer_id)
    return deepcopy(review)


def interact_adjudication(review, packet, notes, stream, output, save):
    view = projected(review, packet['disputedCaseIds'])
    by_id = {note['case']['id']: note for note in notes['cases']}
    def restore(candidate):
        return {**deepcopy(review), 'reviewerId': candidate['reviewerId'], 'reviewedAt': candidate['reviewedAt'],
                'labels': deepcopy(candidate['labels'])}
    def render(case, position, total):
        note = by_id[case['id']]
        return task_text(case, position, total) + (
            'FIRST REVIEW OPTION: ' + terminal_text(note['firstOptionId']) + '\nFIRST RATIONALE: '
            + terminal_text(note['firstRationale']) + '\nSECOND REVIEW OPTION: '
            + terminal_text(note['secondOptionId']) + '\nSECOND RATIONALE: '
            + terminal_text(note['secondRationale']) + '\n')
    introduction = ('Adjudication review. Source controls are displayed as literal escapes.\n'
                    'Choose an option number; :skip keeps the current answer; :quit saves and exits.\n'
                    'Use :edit N, :clear N and :submit; confirmations require y. No default label.\n'
                    'Only disputed tasks are editable; both prior opinions are shown for consideration.\n')
    final, reason = interact(view, stream, output, lambda candidate: save(restore(candidate)),
                             render_task=render, introduction=introduction)
    return restore(final), reason


def verify_adjudication_values(session, initial, saved, packet, blank, *, input_sha, output_sha,
                               packet_sha, blank_sha, notes_sha):
    require(session.get('packetFileSha256') == packet_sha and session.get('blankReviewFileSha256') == blank_sha
            and session.get('caseNotesFileSha256') == notes_sha and session.get('disputedCaseIds') == packet['disputedCaseIds'],
            'Adjudication receipt handoff binding differs')
    require(session.get('distinctDeclaredReviewerId') is True and session.get('handoffSourcesRevalidated') is False,
            'Adjudication receipt grants unsupported source authority')
    details = verify_values(session, initial, saved, input_sha=input_sha, output_sha=output_sha,
        progress_validator=lambda value, reviewer: validate_adjudication_progress(value, reviewer, packet, blank),
        session_schema=SESSION_SCHEMA, source_entrypoint='scripts/adjudicate-decision-reviews.py',
        extra_fields=SESSION_EXTRA_FIELDS)
    return sealed({'schemaVersion': 'agat.decision.adjudication-session-verification.v1', 'status': 'pass',
                   **details, 'completeAdjudicationArtifact': details['completeReviewArtifact'],
                   'packetFileSha256': packet_sha, 'blankReviewFileSha256': blank_sha, 'caseNotesFileSha256': notes_sha,
                   'inputReviewFileSha256': input_sha, 'outputReviewFileSha256': output_sha,
                   'disputedCaseIds': packet['disputedCaseIds'], 'distinctDeclaredReviewerId': True,
                   'handoffSourcesRevalidated': False, 'reviewSourcesRevalidated': False,
                   **{flag: False for flag in AUTHORITY_FLAGS}, 'modelCallsDuringVerification': 0,
                   'referenceLabelsCreated': 0, 'qualification': 'not_assessed'})
