"""Prepare whole-development review tasks without consuming model predictions."""
from copy import deepcopy
import hashlib

from decision_runtime.annotations import POOL_SCHEMA, prepare_review, validate_pool
from decision_runtime.artifacts import sealed, verify_seal
from decision_runtime.contracts import Request, canonical_json, fields, fingerprint, parse_json
from scripts.lib.decision_blind_review import validate_progress
from scripts.lib.decision_public_load import validate_context
from scripts.lib.decision_public_load_verification import CONTEXT_PATHS, same, sources_at
from scripts.lib.decision_public_latency_decomposition import private_path
from scripts.lib.decision_public_native_execution_accounting import SOURCE_PATHS as PREVIOUS_PATHS
from scripts.lib.decision_public_sources import pinned_input
from scripts.lib.decision_shadow_pilot import require, timestamp, utc_now

PACKET_SCHEMA = 'agat.decision.public-development-review-packet.v1'
VERIFICATION_SCHEMA = 'agat.decision.public-development-review-verification.v1'
SOURCE_PATHS = [*PREVIOUS_PATHS, 'scripts/prepare-public-support-development-review.py',
    'scripts/verify-public-support-development-review.py',
    'scripts/test/test_decision_public_development_review.py']
OPTIONS = {'incident', 'enhancement', 'access', 'other', 'insufficient'}
FLAGS = {'modelCalls': 0, 'referenceLabels': 0, 'completedHumanReviews': 0,
    'calibrationCasesDisclosed': 0, 'holdoutCasesDisclosed': 0,
    'reviewerIdentityVerified': False, 'humanExecutionVerified': False,
    'independentReviewVerified': False, 'expertQualificationsVerified': False,
    'classificationAccuracyMeasured': False, 'labelsVerified': False,
    'representativeAgatTraffic': False, 'ownersAppointed': False,
    'sloAccepted': False, 'routingEnabled': False, 'qualification': 'not_assessed'}
PROTOCOL = {'schemaVersion': 'agat.decision.public-development-review-protocol.v1',
    'scope': 'whole_original_public_development_context_inventory',
    'selection': 'all_original_inputs_in_original_order',
    'family': 'classification', 'contextTooLongCasesRetained': True,
    'inputTruncationApplied': False, 'sourceTranslationApplied': False,
    'predictionArtifactsConsumed': False, 'nativeObservationsRequired': False,
    'reviewTaskFormat': 'agat.decision.review.v1', 'initialLabels': 'all_null',
    'independentHumanReviewsRequired': 2, 'disagreementsRequireAdjudication': True,
    'reviewerIdentityAuthenticatedByPacket': False,
    'submittedReviewsStoredSeparately': True, 'reviewTerminalHidesGroupsAndSplitSeed': True}


def encoded(value):
    return (canonical_json(value) + '\n').encode('utf-8')


def review_outputs(context):
    """Projection only; never obtain an answer from observed runtime outcomes."""
    validate_context(context)
    cases = []
    for row in context['inputs']:
        request = Request.from_dict(row['request'])
        require(request.kind == 'choice' and {o.id for o in request.options} == OPTIONS,
                'Unsupported public classification rubric')
        require(row['provenance']['kind'] == 'real', 'Public source provenance changed')
        cases.append({'id': row['id'], 'family': 'classification', 'groupId': row['groupId'],
                      'provenance': deepcopy(row['provenance']), 'request': deepcopy(row['request'])})
    pool = {'schemaVersion': POOL_SCHEMA,
            'id': 'agat-public-support-development-' + fingerprint(cases)[:12], 'cases': cases}
    validate_pool(pool)
    review = prepare_review(pool, context['splitSeed'])
    same(validate_progress(review, 'packet-validation'), review,
         'Blank packet is incompatible with the existing terminal reviewer')
    return {'pool.json': pool, 'review.first.blank.json': deepcopy(review),
            'review.second.blank.json': deepcopy(review)}


def inventory(context, pool):
    return {'originalContextPoolSha256': context['poolSha256'],
        'reviewPoolSha256': fingerprint(pool), 'selectedSplit': 'development',
        'selectionOrder': 'original_pool_order', 'cases': len(context['inputs']),
        'groups': len({row['groupId'] for row in context['inputs']}),
        'contextEligibleCases': context['contextEligibleCases'],
        'contextTooLongCases': context['contextTooLongCases'],
        'contextProfileSealSha256': context['sha256'], 'allOriginalInputsRetained': True,
        'caseBindings': [{'index': index, 'id': row['id'], 'groupId': row['groupId'],
                          'inputSha256': row['inputSha256']} for index, row in enumerate(context['inputs'])]}


def reconstruct(root, context_input):
    fields(context_input, {'path', 'sha256'})
    path = private_path(root, context_input['path'])
    context = validate_context(parse_json(pinned_input(path, context_input['sha256'], 32 * 1024 * 1024)))
    sources = sources_at(root, context['sourceCommit'], context['sourceFiles'], CONTEXT_PATHS)
    outputs = review_outputs(context)
    return context, outputs, len(sources)


def create_packet(root, context_input, commit, source_files):
    sources_at(root, commit, source_files, SOURCE_PATHS)
    context, outputs, context_sources = reconstruct(root, context_input)
    packet = sealed({'schemaVersion': PACKET_SCHEMA, 'status': 'awaiting_independent_human_review',
        'createdAt': utc_now(), 'sourceCommit': commit, 'sourceFiles': source_files,
        'protocol': deepcopy(PROTOCOL), 'contextInput': deepcopy(context_input),
        'verifiedContextSourceFiles': context_sources,
        'artifactFileSha256': {name: hashlib.sha256(encoded(value)).hexdigest() for name, value in outputs.items()},
        'inventory': inventory(context, outputs['pool.json']), **FLAGS})
    return packet, outputs


def verify_packet(root, path, file_sha):
    path = path.absolute()
    require(path.name == 'packet.json', 'Use the original immutable packet receipt')
    packet = verify_seal(parse_json(pinned_input(path, file_sha, 4 * 1024 * 1024)), PACKET_SCHEMA)
    fields(packet, {'schemaVersion', 'sha256', 'status', 'createdAt', 'sourceCommit', 'sourceFiles',
        'protocol', 'contextInput', 'verifiedContextSourceFiles', 'artifactFileSha256', 'inventory', *FLAGS})
    require(packet['status'] == 'awaiting_independent_human_review', 'Packet invents completed review')
    timestamp(packet['createdAt'], 'packet.createdAt')
    require(all(type(packet[key]) is type(value) and packet[key] == value for key, value in FLAGS.items()),
            'Packet grants unsupported human labels or qualification')
    same(packet['protocol'], PROTOCOL, 'Review selection or blinding contract changed')
    sources = sources_at(root, packet['sourceCommit'], packet['sourceFiles'], SOURCE_PATHS)
    context, outputs, context_sources = reconstruct(root, packet['contextInput'])
    require(type(packet['verifiedContextSourceFiles']) is int
            and packet['verifiedContextSourceFiles'] == context_sources, 'Context source denominator changed')
    same(packet['inventory'], inventory(context, outputs['pool.json']), 'Whole original input binding changed')
    expected = {name: hashlib.sha256(encoded(value)).hexdigest() for name, value in outputs.items()}
    same(packet['artifactFileSha256'], expected, 'Review output content is not the frozen blank projection')
    require({item.name for item in path.parent.iterdir()} == {'packet.json', *outputs},
            'Extra or missing artifacts in immutable review packet')
    for name, value in outputs.items():
        raw = pinned_input(path.parent / name, expected[name], 32 * 1024 * 1024)
        require(raw == encoded(value), 'Review tasks differ from their original full inputs')
    return sealed({'schemaVersion': VERIFICATION_SCHEMA, 'status': 'pass', 'verifiedAt': utc_now(),
        'packetFileSha256': file_sha, 'sourceCommit': packet['sourceCommit'],
        'verifiedSourceFiles': len(sources), 'verifiedContextSourceFiles': context_sources,
        'inventory': packet['inventory'], 'modelCallsDuringVerification': 0, **FLAGS})
