"""Exact published Russian yes/no development references; Boolean false remains typed and unqualified."""
import hashlib
from pathlib import Path
import urllib.request
from decision_runtime.artifacts import sealed, verify_seal
from decision_runtime.contracts import Request, canonical_json, fields, fingerprint, parse_json, string
from decision_runtime.evaluation import validate_dataset
from scripts.lib.decision_public_sources import pinned_input
from scripts.lib.decision_shadow_pilot import require
from scripts.lib.decision_terra_corpus import AUTHORITY, development_bytes, normalized_premise, verify_bytes

ROOT=Path(__file__).resolve().parents[2]
DESCRIPTOR='docs/qualification/local-decisions/external/danetqa-source.json'
SCHEMA='agat.decision.external-danetqa-development.v1'
ARCHIVE_SHA='b5b4bcfe17e1eb16aa13a7aab4ca088871e27b0851468e9a07b9b528bb42fb96'
DEVELOPMENT_SHA='92d983265e7900e33454f70c5f1f088e2d9b180858edb4fcc8f7defab816dc3e'
LICENSE_SHA='e69f12e8fa47f0568071cd6f6a472e01dcde4c183ecb89dfea86932bd8fe4107'
SOURCE_PATHS=('decision_runtime','scripts/lib','workers/local_decisions.py','scripts/import-danetqa-development.py',
 'scripts/test/test_decision_danetqa_corpus.py',DESCRIPTOR)
OPTIONS=[dict(id='false',description='Нет.',value=False),dict(id='true',description='Да.',value=True)]

def descriptor(root=ROOT):
 v=parse_json((root/DESCRIPTOR).read_bytes())
 fields(v,{'schemaVersion','repository','revision','archive','development','license','references'})
 fields(v['archive'],{'url','bytes','sha256'});fields(v['development'],{'member','bytes','sha256','cases','groups','labelCounts',
  'normalizedDuplicatePairAdditionalRows','conflictingDuplicateReferencePairs'})
 fields(v['license'],{'name','repository','revision','url','bytes','sha256'})
 require(v['schemaVersion']=='agat.decision.danetqa-source.v1' and v['repository']=='RussianNLP/russian_super_glue'
  and v['revision']=='53cb9d7f38b34c308a2eea3f4797f9edc7947d8b','Different immutable DaNetQA source')
 require(v['archive']==dict(url='https://huggingface.co/datasets/RussianNLP/russian_super_glue/resolve/'+v['revision']+'/data/DaNetQA.zip',
  bytes=1293761,sha256=ARCHIVE_SHA),'Different publisher archive pin')
 require(v['development']==dict(member='DaNetQA/val.jsonl',bytes=1108765,sha256=DEVELOPMENT_SHA,cases=821,groups=779,
  labelCounts={'true':412,'false':409},normalizedDuplicatePairAdditionalRows=2,conflictingDuplicateReferencePairs=0),'Different development inventory')
 require(v['license']==dict(name='MIT',repository='RussianNLP/RussianSuperGLUE',revision='1aff0918f226252eb304bdcdadf9eef5926cc924',
  url='https://raw.githubusercontent.com/RussianNLP/RussianSuperGLUE/1aff0918f226252eb304bdcdadf9eef5926cc924/LICENSE',bytes=1075,sha256=LICENSE_SHA),
  'Different original license pin')
 numeric=[v['archive']['bytes'],v['license']['bytes'],*[v['development'][k] for k in ('bytes','cases','groups','normalizedDuplicatePairAdditionalRows','conflictingDuplicateReferencePairs')],
  *v['development']['labelCounts'].values()]
 require(all(type(x) is int for x in numeric),'Boolean numeric pin alias')
 require(v['references']==['https://russiansuperglue.com/tasks/task_info/DaNetQA','https://russiansuperglue.com/faq/',
  'https://arxiv.org/abs/2010.02605v2','https://aclanthology.org/2020.emnlp-main.381/'],'Different original attribution')
 return v

def acquire(root=ROOT):
 source=descriptor(root);payload={}
 for name,record in [('DaNetQA.zip',source['archive']),('LICENSE',source['license'])]:
  with urllib.request.urlopen(record['url'],timeout=45) as response:raw=response.read(record['bytes']+1)
  verify_bytes(raw,record);payload[name]=raw
 return payload

def cases_from_rows(raw,source):
 rows=raw.splitlines();require(len(rows)==source['development']['cases'] and all(r.strip() for r in rows),'Incomplete development rows')
 cases=[];seen=set();counts={'true':0,'false':0};pairs={};duplicates=0
 for row in rows:
  item=parse_json(row);fields(item,{'passage','question','label','idx'})
  require(type(item['idx']) is int and 0<=item['idx']<source['development']['cases'] and item['idx'] not in seen,'Duplicate, Boolean or invalid publisher index')
  require(type(item['label']) is bool,'Publisher answer must be a real Boolean')
  string(item['passage'],24000);string(item['question'],2000);seen.add(item['idx'])
  label='true' if item['label'] else 'false';counts[label]+=1;identifier='danetqa-val-'+str(item['idx'])
  request=Request.from_dict(dict(schemaVersion='agat.decision.v1',id=identifier,state=item['passage'],question=item['question'],kind='boolean',options=OPTIONS)).to_dict()
  pair=fingerprint([normalized_premise(item['passage']),normalized_premise(item['question'])])
  require(pair not in pairs or pairs[pair] is item['label'],'Conflicting duplicate published answers')
  duplicates+=int(pair in pairs);pairs[pair]=item['label']
  cases.append(dict(id=identifier,family='external-russian-yes-no',groupId='danetqa-passage-'+fingerprint(normalized_premise(item['passage'])),
   split='development',labelSource='published-corpus',expectedOptionId=label,publishedBooleanReference=item['label'],request=request,
   provenance=dict(kind='real',sourceId=identifier,reference=source['archive']['url'],publisherIndex=item['idx'],
    sourceRowSha256=hashlib.sha256(row).hexdigest(),questionSha256=hashlib.sha256(item['question'].encode()).hexdigest()),requestInputSha256=Request.from_dict(request).input_sha256))
 require(seen==set(range(source['development']['cases'])) and counts==source['development']['labelCounts']
  and len({c['groupId'] for c in cases})==source['development']['groups'] and duplicates==source['development']['normalizedDuplicatePairAdditionalRows']
  and source['development']['conflictingDuplicateReferencePairs']==0,'Published denominator, groups, typed answers or dependencies differ')
 return cases

def build_dataset(archive_raw,license_raw,root=ROOT):
 source=descriptor(root);verify_bytes(license_raw,source['license']);cases=cases_from_rows(development_bytes(archive_raw,source),source)
 return sealed(dict(schemaVersion=SCHEMA,id='danetqa-validation-53cb9d7',usage='external-development-only',
  referenceScope='original published DaNetQA development Boolean answers; no local blind review',**AUTHORITY,
  sourceDescriptorSha256=fingerprint(source),source=source,cases=cases,
  summary=dict(caseCount=len(cases),normalizedPassageGroups=len({c['groupId'] for c in cases}),publishedReferenceLabels=len(cases),
   publishedBooleanLabelCounts=source['development']['labelCounts'],normalizedDuplicatePairAdditionalRows=source['development']['normalizedDuplicatePairAdditionalRows'],
   conflictingDuplicateReferencePairs=0,duplicateRowsPreserved=True,modelCalls=0,trainOrTestMembersRead=False,
   calibrationOrHoldoutUsed=False,inputTranslationApplied=False,inputTruncationApplied=False)))

def verify_dataset(value,archive_raw,license_raw,root=ROOT):
 verify_seal(value,SCHEMA)
 require(canonical_json(value)==canonical_json(build_dataset(archive_raw,license_raw,root)),'Dataset differs from complete publisher Boolean inputs/references or authority')
 _evaluation_shape(value,root);return value

def _evaluation_shape(value,root=ROOT):
 verify_seal(value,SCHEMA)
 fields(value,{'schemaVersion','sha256','id','usage','referenceScope','sourceDescriptorSha256','source','cases','summary',*AUTHORITY})
 require(value['usage']=='external-development-only' and value['source']==descriptor(root) and value['sourceDescriptorSha256']==fingerprint(value['source'])
  and all(type(value[k]) is type(expected) and value[k]==expected for k,expected in AUTHORITY.items()),'External corpus cannot invent local qualification')
 require(len(value['cases'])==value['source']['development']['cases'] and all(c['split']=='development' and c['labelSource']=='published-corpus'
  and 'review' not in c and c['request']['kind']=='boolean' and type(c['publishedBooleanReference']) is bool for c in value['cases']),
  'Wrong published Boolean/split/reference scope')
 return validate_dataset(dict(schemaVersion='agat.decision.dataset.v1',id=value['id'],usage='external-development-only',cases=value['cases']))

def evaluation_dataset(value,archive_raw,license_raw,root=ROOT):return _evaluation_shape(verify_dataset(value,archive_raw,license_raw,root),root)

def pinned_sources(archive_path,archive_sha,license_path,license_sha,root=ROOT):
 source=descriptor(root);archive_raw=pinned_input(archive_path,archive_sha,source['archive']['bytes']);license_raw=pinned_input(license_path,license_sha,source['license']['bytes'])
 verify_bytes(archive_raw,source['archive']);verify_bytes(license_raw,source['license']);return archive_raw,license_raw
