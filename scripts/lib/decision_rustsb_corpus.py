"""Original continuous Russian STS development references, without invented class labels or reviews."""
import hashlib
import math
from pathlib import Path
import urllib.request
from decision_runtime.artifacts import sealed, verify_seal
from decision_runtime.contracts import Request, canonical_json, fields, fingerprint, parse_json, string
from scripts.lib.decision_public_sources import pinned_input
from scripts.lib.decision_shadow_pilot import require
from scripts.lib.decision_terra_corpus import AUTHORITY as IMPORT_AUTHORITY, normalized_premise, verify_bytes

ROOT=Path(__file__).resolve().parents[2]
DESCRIPTOR='docs/qualification/local-decisions/external/ru-stsb-source.json'
SCHEMA='agat.decision.external-ru-stsb-development.v1'
AUTHORITY={**IMPORT_AUTHORITY,'regressionQualityMeasured':False,'regressionQualityQualified':False}
SOURCE_PATHS=('decision_runtime','scripts/lib','workers/local_decisions.py','scripts/import-ru-stsb-development.py',
 'scripts/test/test_decision_rustsb_corpus.py',DESCRIPTOR)
QUESTION='Оцени сходство смысла двух предложений по шкале от 0 до 5. Одинаковая тема не означает одинаковый смысл; учитывай отрицание, роли участников и существенные детали.'
DESCRIPTIONS=('0: Смыслы не совпадают.','1: Есть общая тема, но смысл различается.',
 '2: Есть общие детали, но важный смысл различается.','3: Главный смысл в основном общий, но есть существенные различия.',
 '4: Главный смысл одинаков, различаются несущественные детали.','5: Смысл полностью совпадает.')
OPTIONS=[dict(id='score-'+str(i),description=text,value=float(i)) for i,text in enumerate(DESCRIPTIONS)]

def descriptor(root=ROOT):
 value=parse_json((root/DESCRIPTOR).read_bytes())
 fields(value,{'schemaVersion','repository','revision','development','declaration','license','references'})
 fields(value['development'],{'path','url','bytes','sha256','gitBlobSha1','cases','groups','distinctNormalizedSentences',
  'uniqueUnorderedNormalizedPairs','additionalDuplicatePairRows','conflictingDuplicateScorePairs','fractionalScoreRows','minimumScore','maximumScore'})
 fields(value['declaration'],{'url','bytes','sha256','gitBlobSha1'})
 fields(value['license'],{'name','repository','revision','file','url','canonicalUrl','bytes','sha256','gitBlobSha1'})
 require(value['schemaVersion']=='agat.decision.ru-stsb-source.v1' and value['repository']=='ai-forever/ru-stsbenchmark-sts'
  and value['revision']=='1ee9c2ca22b69ccfffa9df9f8f4ae40702c8a6e9','Different immutable numeric corpus')
 base='https://huggingface.co/datasets/'+value['repository']+'/resolve/'+value['revision']+'/'
 expected_development=dict(path='data/sts_rumteb_dev.json',url=base+'data/sts_rumteb_dev.json',bytes=441982,
  sha256='9769640432908f3925d63070e4f3ca1f779bece4757d91d0558067a5115fe769',gitBlobSha1='3d599ee896738ace233855a4b0aea8d9346cfabf',
  cases=1336,groups=1242,distinctNormalizedSentences=2561,uniqueUnorderedNormalizedPairs=1333,additionalDuplicatePairRows=3,
  conflictingDuplicateScorePairs=2,fractionalScoreRows=943,minimumScore=0.0,maximumScore=5.0)
 expected_declaration=dict(url=base+'README.md',bytes=349,sha256='877171016b7e4224bf711093d940a9a76696ddf169e58d5df9c2f6bdbd308907',gitBlobSha1='8263e2a0a2decc28e68223bd733ec1dad907a3de')
 expected_license=dict(name='CC-BY-SA-4.0',repository='creativecommons/creativecommons.org',revision='7f5253347532fc0cb6cf51d78a7085d081fb4216',
  file='docroot/legalcode/by-sa_4.0.html',url='https://raw.githubusercontent.com/creativecommons/creativecommons.org/7f5253347532fc0cb6cf51d78a7085d081fb4216/docroot/legalcode/by-sa_4.0.html',
  canonicalUrl='https://creativecommons.org/licenses/by-sa/4.0/',bytes=44855,sha256='d48546c6161cd2698445d184a859363a2436a6a3bcda655564730e3861e74a9e',gitBlobSha1='c8fbbe1d343762e8381ebbb58b697dbdf09cbca1')
 require(canonical_json(value['development'])==canonical_json(expected_development)
  and canonical_json(value['declaration'])==canonical_json(expected_declaration)
  and canonical_json(value['license'])==canonical_json(expected_license),'Different source inventory, typed numeric pins or license')
 require(value['references']==['https://huggingface.co/datasets/ai-forever/ru-stsbenchmark-sts','https://arxiv.org/abs/2408.12503',
  'https://docs.mteb.org/overview/available_tasks/semantic-similarity/','https://aclanthology.org/S17-2001/'],'Different attribution')
 return value

def acquire(root=ROOT):
 source=descriptor(root);payload={}
 for name,record in [('sts_rumteb_dev.json',source['development']),('README.md',source['declaration']),('CC-BY-SA-4.0.html',source['license'])]:
  with urllib.request.urlopen(record['url'],timeout=45) as incoming:raw=incoming.read(record['bytes']+1)
  verify_bytes(raw,record);payload[name]=raw
 return payload

def original_rows(raw,source):
 verify_bytes(raw,source['development']);rows=parse_json(raw)
 require(isinstance(rows,list) and len(rows)==source['development']['cases'],'Incomplete numeric corpus')
 for row in rows:
  fields(row,{'sentence1','sentence2','score'});string(row['sentence1'],10000);string(row['sentence2'],10000)
  require(type(row['score']) is float and math.isfinite(row['score']) and 0<=row['score']<=5,'Original reference must be a finite float, not Boolean, integer or rounded class')
 return rows

def grouped_rows(rows):
 parent={}
 def find(value):
  parent.setdefault(value,value);path=[]
  while parent[value]!=value:path.append(value);value=parent[value]
  for item in path:parent[item]=value
  return value
 pairs={}
 for row in rows:
  a,b=normalized_premise(row['sentence1']),normalized_premise(row['sentence2']);ra,rb=find(a),find(b)
  if ra!=rb:parent[max(ra,rb)]=min(ra,rb)
  pairs.setdefault(tuple(sorted((a,b))),[]).append(row['score'])
 components={}
 for sentence in parent:components.setdefault(find(sentence),[]).append(sentence)
 group_ids={sentence:'ru-stsb-sentences-'+fingerprint(sorted(members)) for members in components.values() for sentence in members}
 stats=dict(caseCount=len(rows),sentenceConnectedComponents=len(components),distinctNormalizedSentences=len(parent),
  uniqueUnorderedNormalizedPairs=len(pairs),additionalDuplicatePairRows=sum(len(v)-1 for v in pairs.values()),
  conflictingDuplicateScorePairs=sum(len(set(v))>1 for v in pairs.values()),fractionalScoreRows=sum(v['score']!=int(v['score']) for v in rows),
  minimumScore=min(v['score'] for v in rows),maximumScore=max(v['score'] for v in rows))
 return group_ids,stats

def build_dataset(development_raw,declaration_raw,license_raw,root=ROOT):
 source=descriptor(root);verify_bytes(declaration_raw,source['declaration']);verify_bytes(license_raw,source['license'])
 rows=original_rows(development_raw,source);groups,stats=grouped_rows(rows)
 expected={k:source['development'][field] for k,field in [('caseCount','cases'),('sentenceConnectedComponents','groups')]}|{k:source['development'][k] for k in stats if k not in ('caseCount','sentenceConnectedComponents')}
 require(canonical_json(stats)==canonical_json(expected),'Published numeric denominator or dependencies differ')
 cases=[]
 for index,row in enumerate(rows):
  identifier='ru-stsb-dev-'+str(index)
  request=Request.from_dict(dict(schemaVersion='agat.decision.v1',id=identifier,state='Предложение 1:\n'+row['sentence1']+'\nПредложение 2:\n'+row['sentence2'],question=QUESTION,kind='score',options=OPTIONS)).to_dict()
  cases.append(dict(id=identifier,family='external-russian-sentence-similarity-score',groupId=groups[normalized_premise(row['sentence1'])],
   split='development',labelSource='published-corpus',publishedScoreReference=row['score'],request=request,
   provenance=dict(kind='real',sourceId=identifier,reference=source['development']['url'],publisherRowIndex=index,sourceCanonicalRowSha256=fingerprint(row)),
   requestInputSha256=Request.from_dict(request).input_sha256))
 return sealed(dict(schemaVersion=SCHEMA,id='ru-stsb-validation-1ee9c2c',usage='external-development-only',**AUTHORITY,
  referenceScope='original published continuous Russian STS development scores; no local blind review',
  sourceDescriptorSha256=fingerprint(source),source=source,
  datasetLicense=dict(name='CC-BY-SA-4.0',url=source['license']['canonicalUrl'],publisher=source['repository'],
   sourceUrl='https://huggingface.co/datasets/'+source['repository'],modifications='Request framing and deterministic dependency grouping; original texts, float scores and every row preserved.'),
  cases=cases,summary={**stats,**dict(publishedReferenceScores=len(cases),duplicateRowsPreserved=True,conflictingReferenceRowsPreserved=True,
   referenceRoundingApplied=False,classificationLabelsInvented=False,connectedSentenceDependenciesPreserved=True,modelCalls=0,
   trainOrTestRead=False,calibrationOrHoldoutUsed=False,inputTranslationApplied=False,inputTruncationApplied=False)}))

def verify_dataset(value,development_raw,declaration_raw,license_raw,root=ROOT):
 verify_seal(value,SCHEMA)
 require(canonical_json(value)==canonical_json(build_dataset(development_raw,declaration_raw,license_raw,root)),
  'Numeric corpus differs from original references, complete texts, dependency graph or scope')
 return value

def evaluation_cases(value,development_raw,declaration_raw,license_raw,root=ROOT):
 return verify_dataset(value,development_raw,declaration_raw,license_raw,root)['cases']

def pinned_sources(development_path,development_sha,declaration_path,declaration_sha,license_path,license_sha,root=ROOT):
 source=descriptor(root);payload=[]
 for path,pin,record in [(development_path,development_sha,source['development']),(declaration_path,declaration_sha,source['declaration']),
  (license_path,license_sha,source['license'])]:
  raw=pinned_input(path,pin,record['bytes']);verify_bytes(raw,record);payload.append(raw)
 return tuple(payload)
