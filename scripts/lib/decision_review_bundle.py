"""Portable immutable inputs for a full finalized-review replay, with one external pin."""
import hashlib
import os

from decision_runtime.artifacts import sealed, verify_seal
from decision_runtime.contracts import canonical_json, fields, parse_json
from scripts.lib.decision_public_sources import pinned_input, private_directory, write_json_new
from scripts.lib.decision_review_finalization import verify_finalization
from scripts.lib.decision_review_finalization_cli import REVIEW_INPUTS, ADJUDICATION_INPUTS
from scripts.lib.decision_review_session import AUTHORITY_FLAGS, MAX_REVIEW_BYTES
from scripts.lib.decision_shadow_pilot import require

SCHEMA = 'agat.decision.review-finalization-bundle.v1'
MAX_MANIFEST_BYTES = 256 * 1024
MAX_BUNDLE_BYTES = 128 * 1024 * 1024


def artifact_names(has_adjudication):
    names = {'comparison.json', 'finalization.json', 'dataset.json'}
    for prefix, inputs in (('first', REVIEW_INPUTS), ('second', REVIEW_INPUTS),
                            ('adjudication', ADJUDICATION_INPUTS)):
        if prefix != 'adjudication' or has_adjudication:
            names.update(prefix+'.'+name+'.json' for name in inputs)
    return names


def file_bindings(first, second, comparison, comparison_sha, report, report_sha, dataset, dataset_sha, adjudication=None):
    result = {'comparison.json': (comparison, comparison_sha), 'finalization.json': (report, report_sha),
              'dataset.json': (dataset, dataset_sha)}
    for prefix, names, binding in (('first', REVIEW_INPUTS, first), ('second', REVIEW_INPUTS, second),
                                   ('adjudication', ADJUDICATION_INPUTS, adjudication)):
        if binding is not None:
            require(len(binding) == len(names)*2, 'Incomplete bundle artifact bindings')
            for offset, name in enumerate(names): result[prefix+'.'+name+'.json'] = binding[offset*2:offset*2+2]
    return result


def _manifest(files, verification, has_adjudication):
    return sealed({'schemaVersion': SCHEMA, 'status': 'packaged', 'hasAdjudication': has_adjudication,
        'artifacts': {name: {'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)} for name, raw in files.items()},
        'caseCount': verification['caseCount'], 'groupCount': verification['groupCount'],
        'agreedCases': verification['agreedCases'], 'adjudicatedCases': verification['adjudicatedCases'],
        'sourceReviewsRevalidated': True, 'handoffReconstructedFromSourceReviews': has_adjudication,
        **{flag: False for flag in AUTHORITY_FLAGS}, 'classificationAccuracyMeasured': False,
        'modelCallsDuringPackaging': 0, 'qualification': 'not_assessed'})


def prepare_bundle(first, second, comparison, comparison_sha, report, report_sha, dataset, dataset_sha, adjudication=None):
    files = {}; total = 0
    for name, (path, sha) in file_bindings(first, second, comparison, comparison_sha, report, report_sha,
                                        dataset, dataset_sha, adjudication).items():
        raw = pinned_input(path, sha, min(MAX_REVIEW_BYTES, MAX_BUNDLE_BYTES-total)); total += len(raw)
        require(total <= MAX_BUNDLE_BYTES, 'Review bundle exceeds its total byte bound')
        files[name] = raw
    verification = verify_finalization(first, second, comparison, comparison_sha, report, report_sha,
                                      dataset, dataset_sha, adjudication)
    return _manifest(files, verification, adjudication is not None), files


def write_bundle(root, output, manifest, files):
    """Write the completion manifest last; never overwrite another bundle."""
    verify_seal(manifest, SCHEMA)
    require(type(manifest['hasAdjudication']) is bool and set(files) == artifact_names(manifest['hasAdjudication']),
            'Unexpected bundle output filenames')
    expected = {name: {'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)} for name, raw in files.items()}
    require(canonical_json(manifest['artifacts']) == canonical_json(expected), 'Bundle output bytes differ from manifest')
    directory = private_directory(root, output)
    for name, raw in files.items():
        with os.fdopen(os.open(directory/name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
            stream.write(raw)
    write_json_new(directory/'bundle.json', manifest)
    return directory


def verify_bundle(path, file_sha):
    require(path.name == 'bundle.json', 'Use the immutable bundle manifest')
    manifest = verify_seal(parse_json(pinned_input(path, file_sha, MAX_MANIFEST_BYTES)), SCHEMA)
    fields(manifest, {'schemaVersion', 'sha256', 'status', 'hasAdjudication', 'artifacts', 'caseCount', 'groupCount',
        'agreedCases', 'adjudicatedCases', 'sourceReviewsRevalidated', 'handoffReconstructedFromSourceReviews',
        *AUTHORITY_FLAGS, 'classificationAccuracyMeasured', 'modelCallsDuringPackaging', 'qualification'})
    require(type(manifest['hasAdjudication']) is bool, 'Invalid adjudication presence flag')
    has_adjudication = manifest['hasAdjudication']
    # Names are fixed by this implementation; manifest strings never choose filesystem paths.
    names = artifact_names(has_adjudication)
    fields(manifest['artifacts'], names)
    seen = set()
    for item in path.parent.iterdir():
        require(item.name in names|{'bundle.json'}, 'Extra artifact in immutable review bundle')
        seen.add(item.name)
    require(seen == names|{'bundle.json'}, 'Missing artifact in review bundle')
    total = 0
    for name, pin in manifest['artifacts'].items():
        fields(pin, {'sha256', 'bytes'})
        require(type(pin['bytes']) is int and 0 < pin['bytes'] <= MAX_REVIEW_BYTES, 'Invalid bundle file byte count')
        total += pin['bytes']; require(total <= MAX_BUNDLE_BYTES, 'Review bundle exceeds its total byte bound')
        raw = pinned_input(path.parent/name, pin['sha256'], pin['bytes'])
        require(len(raw) == pin['bytes'], 'Bundle artifact byte count differs')
    def read(prefix, inputs):
        return tuple(value for name in inputs for value in
                     (path.parent/(prefix+'.'+name+'.json'), manifest['artifacts'][prefix+'.'+name+'.json']['sha256']))
    first, second = read('first', REVIEW_INPUTS), read('second', REVIEW_INPUTS)
    adjudication = read('adjudication', ADJUDICATION_INPUTS) if has_adjudication else None
    def binding(name): return path.parent/name, manifest['artifacts'][name]['sha256']
    comparison, comparison_sha = binding('comparison.json')
    report, report_sha = binding('finalization.json'); dataset, dataset_sha = binding('dataset.json')
    expected, _ = prepare_bundle(first, second, comparison, comparison_sha, report, report_sha, dataset, dataset_sha, adjudication)
    require(canonical_json(manifest) == canonical_json(expected), 'Bundle claims differ from its revalidated source artifacts')
    return sealed({'schemaVersion': 'agat.decision.review-finalization-bundle-verification.v1', 'status': 'pass',
        'bundleFileSha256': file_sha, 'bundleSha256': manifest['sha256'], 'artifactFilesVerified': len(names),
        'artifactBytesVerified': total, 'datasetFileSha256': manifest['artifacts']['dataset.json']['sha256'],
        'finalizationFileSha256': manifest['artifacts']['finalization.json']['sha256'],
        'caseCount': expected['caseCount'], 'groupCount': expected['groupCount'],
        'agreedCases': expected['agreedCases'], 'adjudicatedCases': expected['adjudicatedCases'],
        'sourceReviewsRevalidated': True, 'handoffReconstructedFromSourceReviews': has_adjudication,
        **{flag: False for flag in AUTHORITY_FLAGS}, 'classificationAccuracyMeasured': False,
        'modelCallsDuringVerification': 0, 'qualification': 'not_assessed'})
