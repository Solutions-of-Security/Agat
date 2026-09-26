"""One-shot, immutable preparation of a report from a stable local CSV export."""

import io
import json
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path

from decision_runtime.artifacts import read_json, sealed, verify_seal
from decision_runtime.contracts import fields, fingerprint
from scripts.lib.report_process import prepare_process, verify_process
from scripts.lib.report_request_records import MAPPING_SCHEMA, MAX_LINES, MAX_SOURCE_BYTES, require, sha, validate_mapping

RECIPE_SCHEMA = 'agat.report.request-snapshot-recipe.v1'
SNAPSHOT_SCHEMA = 'agat.report.request-snapshot.v1'
ROOT = Path(__file__).resolve().parents[2]
FILES = {'source.csv', 'mapping.json', 'report.md', 'calculation.json', 'bundle.json', 'process.json'}


def _json(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')


def validate_recipe(raw):
    recipe = fields(raw, {'schemaVersion', 'name', 'decimalPlaces', 'table', 'mapping'})
    require(recipe['schemaVersion'] == RECIPE_SCHEMA, 'Unsupported snapshot recipe')
    name = recipe['name']
    require(isinstance(name, str) and name == name.strip() and 0 < len(name.encode('utf-16-le')) // 2 <= 80
            and not re.search(r'[\x00-\x1f\x7f]', name), 'Process name must contain 1 to 80 characters without control characters')
    require(type(recipe['decimalPlaces']) is int and 0 <= recipe['decimalPlaces'] <= 6, 'Invalid display precision')
    table = fields(recipe['table'], {'firstLine', 'through'})
    require(type(table['firstLine']) is int and 1 <= table['firstLine'] < MAX_LINES and table['through'] == 'end_of_file',
            'Declare a first header line and end_of_file; no table detection or footer filtering')
    mapping = fields(recipe['mapping'], {'schemaVersion', 'sourceId', 'sourceUri', 'columns', 'periods', 'units', 'population', 'deduplication'})
    require(mapping['schemaVersion'] == MAPPING_SCHEMA, 'Snapshot requires the request-level CSV contract')
    validated = validate_mapping({**mapping, 'expectedSha256': '0' * 64,
                                  'table': {'firstLine': table['firstLine'], 'lastLine': table['firstLine'] + 1}})
    return {**recipe, 'table': dict(table), 'mapping': {k: v for k, v in validated.items() if k not in ('table', 'expectedSha256')}}


def _metadata(value):
    require(stat.S_ISREG(value.st_mode), 'Source must be a regular file')
    require(0 < value.st_size <= MAX_SOURCE_BYTES, 'Source must contain at most 2 MiB')
    return {'device': value.st_dev, 'inode': value.st_ino, 'sizeBytes': value.st_size,
            'modifiedNs': value.st_mtime_ns, 'changedNs': value.st_ctime_ns}


def _read_bytes(descriptor):
    chunks = []
    size = 0
    while size <= MAX_SOURCE_BYTES:
        chunk = os.read(descriptor, min(65536, MAX_SOURCE_BYTES + 1 - size))
        if not chunk:
            return b''.join(chunks)
        chunks.append(chunk)
        size += len(chunk)
    raise ValueError('Source grew beyond the supported size')


def read_stable_source(path: Path):
    # O_NONBLOCK prevents a swapped FIFO from blocking open before fstat rejects it.
    require(hasattr(os, 'O_NOFOLLOW') and hasattr(os, 'O_NONBLOCK'), 'This reader requires POSIX no-follow/nonblocking file opens')
    path = path.absolute()
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = _metadata(os.fstat(descriptor))
        first = _read_bytes(descriptor)
        between = _metadata(os.fstat(descriptor))
        os.lseek(descriptor, 0, os.SEEK_SET)
        second = _read_bytes(descriptor)
        after = _metadata(os.fstat(descriptor))
        current = _metadata(path.stat(follow_symlinks=False))
        require(before == between == after == current and len(first) == before['sizeBytes'] and first == second,
                'Source changed during capture; publish a completed export and retry')
        return first, {'path': str(path), **before, 'sha256': sha(first), 'matchingReads': 2,
                       'method': 'same_descriptor_two_reads_and_metadata'}
    finally:
        os.close(descriptor)


def _mapping(recipe, source):
    try:
        text = source.decode('utf-8')
    except UnicodeDecodeError:
        raise ValueError('Source must be UTF-8') from None
    count = len(list(io.StringIO(text, newline='')))
    return {**recipe['mapping'], 'expectedSha256': sha(source),
            'table': {'firstLine': recipe['table']['firstLine'], 'lastLine': count}}


def _scope(mapping):
    return {**{k: v for k, v in mapping.items() if k not in ('expectedSha256', 'table')},
            'firstLine': mapping['table']['firstLine']}


def _comparison(bundle, previous):
    current = bundle['calculation']['binding']
    if previous is None:
        return {'status': 'initial', 'previousBundleSha256': None, 'sourceBytesChanged': None, 'tableBytesChanged': None,
                'aggregateValuesChanged': None, 'before': None,
                'after': {'sourceSha256': bundle['source']['sha256'], 'counts': current['counts'], 'periods': current['arithmeticInput']['periods']}}
    verify_process(previous)
    require(previous['mapping']['schemaVersion'] == MAPPING_SCHEMA and _scope(previous['mapping']) == _scope(bundle['mapping'])
            and previous['process']['name'] == bundle['process']['name']
            and previous['calculation']['calculation']['rounding'] == bundle['calculation']['calculation']['rounding'],
            'Previous bundle uses a different recipe; start a separate reviewed baseline')
    before = previous['calculation']['binding']
    changed = previous['source']['sha256'] != bundle['source']['sha256']
    return {'status': 'changed' if changed else 'unchanged', 'previousBundleSha256': previous['sha256'], 'sourceBytesChanged': changed,
            'tableBytesChanged': before['source']['tableSha256'] != current['source']['tableSha256'],
            'aggregateValuesChanged': before['arithmeticInput']['periods'] != current['arithmeticInput']['periods'],
            'before': {'sourceSha256': previous['source']['sha256'], 'counts': before['counts'], 'periods': before['arithmeticInput']['periods']},
            'after': {'sourceSha256': bundle['source']['sha256'], 'counts': current['counts'], 'periods': current['arithmeticInput']['periods']}}


def _files(bundle):
    payload = json.loads(bundle['process']['graph']['nodes'][1]['config']['template'])
    return {'source.csv': payload['source'].encode('utf-8'), 'mapping.json': _json(bundle['mapping']),
            'report.md': payload['markdown'].encode('utf-8'), 'calculation.json': payload['calculationJson'].encode('utf-8'),
            'bundle.json': _json(bundle), 'process.json': _json(bundle['process'])}


def prepare_snapshot(source_path: Path, raw_recipe, *, previous=None):
    recipe = validate_recipe(raw_recipe)
    if previous is not None:
        verify_process(previous)
    source, observed = read_stable_source(source_path)
    bundle = prepare_process(source, _mapping(recipe, source), recipe['name'], decimal_places=recipe['decimalPlaces'])
    comparison = _comparison(bundle, previous)
    if comparison['status'] == 'unchanged':
        return {'status': 'unchanged', 'sourceSha256': sha(source), 'previousBundleSha256': previous['sha256']}, None
    files = _files(bundle)
    receipt = sealed({'schemaVersion': SNAPSHOT_SCHEMA, 'status': 'prepared_requires_review',
                      'createdAt': datetime.now(timezone.utc).isoformat(), 'recipe': recipe, 'recipeSha256': fingerprint(recipe),
                      'sourceRead': observed, 'bundleSha256': bundle['sha256'], 'comparison': comparison,
                      'files': {name: {'sha256': sha(content), 'sizeBytes': len(content)} for name, content in files.items()},
                      'implementationSha256': sha(Path(__file__).read_bytes()),
                      'installedInUserDeployment': False, 'humanReviewed': False, 'sourceTruthVerified': False,
                      'sourceCompletenessVerified': False, 'qualifiedForRouting': False, 'routingEnabled': False})
    return receipt, files


def write_snapshot(directory: Path, receipt, files):
    require(directory.resolve().is_relative_to(ROOT / 'docs'), 'Snapshot artifacts belong under docs')
    verify_seal(receipt, SNAPSHOT_SCHEMA)
    require(set(files) == FILES and receipt['files'] == {name: {'sha256': sha(data), 'sizeBytes': len(data)} for name, data in files.items()},
            'Prepared snapshot files changed')
    # Reserve a new directory. A failure leaves no valid receipt; existing paths are never replaced.
    directory.mkdir(parents=True, exist_ok=False)
    for name, content in files.items():
        with (directory / name).open('xb') as handle:
            handle.write(content)
    with (directory / 'snapshot.json').open('xb') as handle:
        handle.write(_json(receipt))


def verify_snapshot(directory: Path, *, previous=None):
    receipt = verify_seal(read_json(directory / 'snapshot.json'), SNAPSHOT_SCHEMA)
    recipe = validate_recipe(receipt['recipe'])
    require(receipt['recipeSha256'] == fingerprint(recipe) and receipt['implementationSha256'] == sha(Path(__file__).read_bytes()),
            'Snapshot recipe or implementation changed')
    require(set(receipt['files']) == FILES, 'Unexpected snapshot file set')
    files = {}
    for name in FILES:
        with (directory / name).open('rb') as handle:
            files[name] = handle.read(4 * MAX_SOURCE_BYTES + 1)
        require(receipt['files'][name] == {'sha256': sha(files[name]), 'sizeBytes': len(files[name])}, f'Snapshot file changed: {name}')
    source = files['source.csv']
    bundle = prepare_process(source, _mapping(recipe, source), recipe['name'], decimal_places=recipe['decimalPlaces'])
    require(_files(bundle) == files and receipt['bundleSha256'] == bundle['sha256'], 'Snapshot does not reproduce the prepared process')
    require(receipt['sourceRead']['sha256'] == sha(source) and receipt['sourceRead']['sizeBytes'] == len(source)
            and receipt['sourceRead']['matchingReads'] == 2 and receipt['sourceRead']['method'] == 'same_descriptor_two_reads_and_metadata',
            'Source read record does not match copied bytes')
    if receipt['comparison']['previousBundleSha256'] is not None:
        require(previous is not None, 'Previous bundle is required to verify this comparison')
    require(receipt['comparison'] == _comparison(bundle, previous), 'Snapshot comparison changed')
    require(receipt['status'] == 'prepared_requires_review' and all(receipt[key] is False for key in
            ('installedInUserDeployment', 'humanReviewed', 'sourceTruthVerified', 'sourceCompletenessVerified', 'qualifiedForRouting', 'routingEnabled')),
            'Snapshot does not establish source truth, review or deployment')
    return receipt
