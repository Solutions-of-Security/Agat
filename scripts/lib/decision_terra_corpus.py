"""Pinned external Russian development references; no local reviewer or routing authority."""
import hashlib
import io
from pathlib import Path
import re
import unicodedata
import urllib.request
import zipfile

from decision_runtime.artifacts import sealed, verify_seal
from decision_runtime.contracts import Request, canonical_json, fields, fingerprint, parse_json, string
from decision_runtime.evaluation import validate_dataset
from scripts.lib.decision_public_sources import pinned_input
from scripts.lib.decision_shadow_pilot import require

ROOT = Path(__file__).resolve().parents[2]
DESCRIPTOR = 'docs/qualification/local-decisions/external/terra-source.json'
SCHEMA = 'agat.decision.external-terra-development.v1'
ARCHIVE_SHA = 'fc7320210b5b6f7087615f13558868de55f46d5e0e365d9d82968c66e6e0dba7'
DEVELOPMENT_SHA = '304c52ac57c648fc57afb8c3d3ec0cf9a7a3f5f481d4654b3a8202126b4cad47'
LICENSE_SHA = 'e69f12e8fa47f0568071cd6f6a472e01dcde4c183ecb89dfea86932bd8fe4107'
LABELS = ('entailment', 'not_entailment')
AUTHORITY = dict(humanLabelsVerified=False, localIndependentHumanReviews=0, qualifiedForRouting=False,
    routingEnabled=False, representativeAgatTraffic=False, statisticalIndependenceVerified=False,
    classificationAccuracyMeasured=False, classificationAccuracyQualified=False,
    calibrationFrozen=False, holdoutEvaluated=False, sloAccepted=False, qualification='not_assessed')
SOURCE_PATHS = ('decision_runtime', 'scripts/lib', 'workers/local_decisions.py',
    'scripts/import-terra-development.py', 'scripts/test/test_decision_terra_corpus.py', DESCRIPTOR)
OPTIONS = [{'id': 'entailment', 'description': 'Утверждение следует из текста.'},
           {'id': 'not_entailment', 'description': 'Утверждение не следует из текста.'}]


def descriptor(root=ROOT):
    value = parse_json((root / DESCRIPTOR).read_bytes())
    fields(value, {'schemaVersion', 'repository', 'revision', 'archive', 'development', 'license', 'references'})
    fields(value['archive'], {'url', 'bytes', 'sha256'})
    fields(value['development'], {'member', 'bytes', 'sha256', 'cases', 'groups', 'labelCounts'})
    fields(value['license'], {'name', 'repository', 'revision', 'url', 'bytes', 'sha256'})
    require(value['schemaVersion'] == 'agat.decision.terra-source.v1'
        and value['repository'] == 'RussianNLP/russian_super_glue'
        and value['revision'] == '53cb9d7f38b34c308a2eea3f4797f9edc7947d8b', 'Different immutable TERRa source')
    archive, development, license = value['archive'], value['development'], value['license']
    require(archive == dict(url='https://huggingface.co/datasets/RussianNLP/russian_super_glue/resolve/'
        + value['revision'] + '/data/TERRa.zip', bytes=907346, sha256=ARCHIVE_SHA), 'Different publisher archive pin')
    require(development == dict(member='TERRa/val.jsonl', bytes=178095, sha256=DEVELOPMENT_SHA,
        cases=307, groups=282, labelCounts={'entailment': 153, 'not_entailment': 154}), 'Different development inventory')
    require(license == dict(name='MIT', repository='RussianNLP/RussianSuperGLUE',
        revision='1aff0918f226252eb304bdcdadf9eef5926cc924',
        url='https://raw.githubusercontent.com/RussianNLP/RussianSuperGLUE/1aff0918f226252eb304bdcdadf9eef5926cc924/LICENSE',
        bytes=1075, sha256=LICENSE_SHA), 'Different original license pin')
    require(type(archive['bytes']) is type(development['bytes']) is type(development['cases']) is type(development['groups'])
        is type(license['bytes']) is int and all(type(v) is int for v in development['labelCounts'].values()), 'Boolean numeric pin alias')
    require(isinstance(value['references'], list) and value['references'] == [
        'https://russiansuperglue.com/tasks/task_info/TERRa', 'https://russiansuperglue.com/faq/',
        'https://aclanthology.org/2020.emnlp-main.381/'], 'Different publisher attribution')
    return value


def verify_bytes(raw, record):
    require(len(raw) == record['bytes'] and hashlib.sha256(raw).hexdigest() == record['sha256'], 'Pinned source bytes differ')


def acquire(root=ROOT):
    """Only an explicit CLI acquisition downloads the two immutable public artifacts."""
    source = descriptor(root); result = {}
    for name, record in (('TERRa.zip', source['archive']), ('LICENSE', source['license'])):
        with urllib.request.urlopen(record['url'], timeout=45) as incoming:
            raw = incoming.read(record['bytes'] + 1)
        verify_bytes(raw, record); result[name] = raw
    return result


def development_bytes(archive_raw, source):
    verify_bytes(archive_raw, source['archive'])
    with zipfile.ZipFile(io.BytesIO(archive_raw)) as archive:
        entries = archive.infolist(); names = [v.filename for v in entries]
        require(len(names) == len(set(names)), 'Duplicate archive member')
        require(all(not Path(name).is_absolute() and '..' not in Path(name).parts and '\\' not in name
                    for name in names), 'Unsafe archive member')
        matches = [v for v in entries if v.filename == source['development']['member']]
        require(len(matches) == 1 and matches[0].file_size == source['development']['bytes']
            and not matches[0].is_dir() and ((matches[0].external_attr >> 16) & 0o170000) != 0o120000,
            'Missing, oversized or linked development member')
        # No extractall, remote dataset loader, or reading train/test members.
        raw = archive.read(matches[0]); verify_bytes(raw, source['development']); return raw


def normalized_premise(value):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', value)).strip().casefold()


def cases_from_rows(raw, source):
    rows = raw.splitlines()
    require(len(rows) == source['development']['cases'] and all(row.strip() for row in rows), 'Incomplete development rows')
    cases, seen, labels, identical_inputs = [], set(), {label: 0 for label in LABELS}, {}
    for row in rows:
        item = parse_json(row); fields(item, {'premise', 'hypothesis', 'label', 'idx'})
        require(type(item['idx']) is int and 0 <= item['idx'] < source['development']['cases'] and item['idx'] not in seen,
            'Duplicate, boolean or invalid publisher index')
        require(type(item['label']) is str and item['label'] in LABELS, 'Unknown publisher reference label')
        string(item['premise'], 24000); string(item['hypothesis'], 1800)
        seen.add(item['idx']); labels[item['label']] += 1
        identifier = 'terra-val-' + str(item['idx'])
        request = Request.from_dict(dict(schemaVersion='agat.decision.v1', id=identifier, state=item['premise'],
            question='Можно ли вывести следующее утверждение из текста?\n' + item['hypothesis'], kind='choice', options=OPTIONS)).to_dict()
        key = fingerprint([normalized_premise(item['premise']), normalized_premise(item['hypothesis'])])
        require(key not in identical_inputs or identical_inputs[key] == item['label'], 'Conflicting duplicate publisher references')
        identical_inputs[key] = item['label']
        cases.append(dict(id=identifier, family='external-russian-textual-entailment',
            groupId='terra-premise-' + fingerprint(normalized_premise(item['premise'])), split='development',
            labelSource='published-corpus', expectedOptionId=item['label'], request=request,
            provenance=dict(kind='real', sourceId=identifier, reference=source['archive']['url'], publisherIndex=item['idx'],
                sourceRowSha256=hashlib.sha256(row).hexdigest(), hypothesisSha256=hashlib.sha256(item['hypothesis'].encode()).hexdigest()),
            requestInputSha256=Request.from_dict(request).input_sha256))
    require(seen == set(range(source['development']['cases'])) and labels == source['development']['labelCounts']
        and len({v['groupId'] for v in cases}) == source['development']['groups'], 'Published denominator, groups or labels differ')
    return cases


def build_dataset(archive_raw, license_raw, root=ROOT):
    source = descriptor(root); verify_bytes(license_raw, source['license'])
    raw = development_bytes(archive_raw, source); cases = cases_from_rows(raw, source)
    return sealed(dict(schemaVersion=SCHEMA, id='terra-validation-53cb9d7', usage='external-development-only',
        referenceScope='original published TERRa development labels; no local blind review', **AUTHORITY,
        sourceDescriptorSha256=fingerprint(source), source=source, cases=cases,
        summary=dict(caseCount=len(cases), normalizedPremiseGroups=len({v['groupId'] for v in cases}),
            publishedReferenceLabels=len(cases), labelCounts=source['development']['labelCounts'],
            completePublishedDevelopment=True, sourceOrderPreserved=True, translationApplied=False,
            truncationApplied=False, calibrationInputsUsed=0, holdoutInputsUsed=0, trainOrTestMembersRead=False,
            modelCalls=0, duplicateNormalizedPairs=len(cases) - len({fingerprint([normalized_premise(v['request']['state']),
                normalized_premise(v['request']['question'])]) for v in cases}))))


def verify_dataset(value, archive_raw, license_raw, root=ROOT):
    verify_seal(value, SCHEMA)
    require(canonical_json(value) == canonical_json(build_dataset(archive_raw, license_raw, root)),
        'Dataset differs from complete pinned publisher inputs, references or authority')
    _evaluation_shape(value, root); return value


def _evaluation_shape(value, root=ROOT):
    verify_seal(value, SCHEMA)
    fields(value, {'schemaVersion', 'sha256', 'id', 'usage', 'referenceScope', 'sourceDescriptorSha256', 'source', 'cases', 'summary', *AUTHORITY})
    require(value['usage'] == 'external-development-only' and value['source'] == descriptor(root)
        and value['sourceDescriptorSha256'] == fingerprint(value['source'])
        and all(type(value[k]) is type(v) and value[k] == v for k, v in AUTHORITY.items()), 'External corpus cannot claim local qualification')
    require(len(value['cases']) == value['source']['development']['cases'] and all(v['split'] == 'development'
        and v['labelSource'] == 'published-corpus' and 'review' not in v for v in value['cases']), 'Wrong source label/split scope')
    return validate_dataset(dict(schemaVersion='agat.decision.dataset.v1', id=value['id'],
        usage='external-development-only', cases=value['cases']))


def evaluation_dataset(value, archive_raw, license_raw, root=ROOT):
    """Only original byte-pinned corpus references can reach the existing metric evaluator."""
    return _evaluation_shape(verify_dataset(value, archive_raw, license_raw, root), root)


def pinned_sources(archive_path, archive_sha, license_path, license_sha, root=ROOT):
    source = descriptor(root)
    archive_raw = pinned_input(archive_path, archive_sha, source['archive']['bytes'])
    license_raw = pinned_input(license_path, license_sha, source['license']['bytes'])
    verify_bytes(archive_raw, source['archive']); verify_bytes(license_raw, source['license']); return archive_raw, license_raw
