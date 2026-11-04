"""Synthetic continuous-reference fixtures; no real labels, reviews or model inference are claimed."""
import copy,hashlib,importlib.util,json,shutil,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from decision_runtime.artifacts import sealed
from decision_runtime.contracts import canonical_json,Request
from scripts.lib import decision_rustsb_corpus as corpus
SPEC=importlib.util.spec_from_file_location('rustsb_cli',ROOT/'scripts/import-ru-stsb-development.py');cli=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(cli)
sha=lambda raw:hashlib.sha256(raw).hexdigest()
reseal=lambda value:sealed({k:v for k,v in value.items() if k!='sha256'})

def fixture(rows=None):
 rows=rows or [dict(sentence1='А.',sentence2='Б.',score=.25),dict(sentence1='Б.',sentence2='В.',score=3.4),
  dict(sentence1='Б.',sentence2='А.',score=.75),dict(sentence1='Г.',sentence2='Д.',score=5.0)]
 raw=(canonical_json(rows)+'\n').encode();declaration=b'synthetic declaration';license=b'synthetic license'
 source=dict(repository='synthetic-corpus',development=dict(url='https://example.invalid/development',bytes=len(raw),sha256=sha(raw),
  cases=4,groups=2,distinctNormalizedSentences=5,uniqueUnorderedNormalizedPairs=3,additionalDuplicatePairRows=1,
  conflictingDuplicateScorePairs=1,fractionalScoreRows=3,minimumScore=.25,maximumScore=5.0),
  declaration=dict(url='https://example.invalid/declaration',bytes=len(declaration),sha256=sha(declaration)),
  license=dict(url='https://example.invalid/license',canonicalUrl='https://example.invalid/synthetic-license',bytes=len(license),sha256=sha(license)))
 return raw,declaration,license,source

class RuStsbCorpusTests(unittest.TestCase):
 def test_closed_descriptor_rejects_test_split_wrong_pins_and_numeric_type_alias(self):
  original=corpus.descriptor(ROOT)
  changes=[lambda v:v['development'].update(path='data/sts_rumteb_test.json'),lambda v:v['development'].update(sha256='a'*64),
   lambda v:v['development'].update(minimumScore=False),lambda v:v['development'].update(minimumScore=0),
   lambda v:v['license'].update(name='MIT'),lambda v:v['declaration'].update(sha256='a'*64),lambda v:v.update(revision='a'*40),lambda v:v.update(humanLabelsVerified=True)]
  for change in changes:
   bad=copy.deepcopy(original);change(bad)
   with tempfile.TemporaryDirectory() as temp:
    root=Path(temp);p=root/corpus.DESCRIPTOR;p.parent.mkdir(parents=True);p.write_text(json.dumps(bad))
    with self.assertRaises(ValueError):corpus.descriptor(root)

 def test_fractional_references_preserved_without_invented_class_targets_or_request_gold(self):
  inputs=fixture()
  with patch.object(corpus,'descriptor',return_value=inputs[-1]):data=corpus.build_dataset(*inputs[:3])
  self.assertEqual([c['publishedScoreReference'] for c in data['cases']],[.25,3.4,.75,5.0])
  for case in data['cases']:
   self.assertIs(type(case['publishedScoreReference']),float);self.assertNotIn('expectedOptionId',case)
   self.assertEqual(set(case['request']),{'schemaVersion','id','state','question','kind','options'});self.assertEqual(case['request']['kind'],'score')
   self.assertEqual([o['value'] for o in Request.from_dict(case['request']).to_dict()['options']],[0.,1.,2.,3.,4.,5.])
   self.assertNotIn('publishedScoreReference',case['request'])
  self.assertFalse(data['classificationAccuracyMeasured']);self.assertFalse(data['regressionQualityMeasured']);self.assertEqual(data['localIndependentHumanReviews'],0)
  self.assertFalse(data['summary']['referenceRoundingApplied']);self.assertFalse(data['summary']['classificationLabelsInvented'])

 def test_shared_sentences_connect_across_roles_and_transitive_pairs(self):
  inputs=fixture()
  with patch.object(corpus,'descriptor',return_value=inputs[-1]):data=corpus.build_dataset(*inputs[:3])
  ids=[c['groupId'] for c in data['cases']];self.assertEqual(ids[:3],[ids[0]]*3);self.assertNotEqual(ids[0],ids[3])
  self.assertEqual(data['summary']['sentenceConnectedComponents'],2);self.assertEqual(data['summary']['distinctNormalizedSentences'],5)
  groups,_=corpus.grouped_rows(list(reversed(json.loads(inputs[0]))));original,_=corpus.grouped_rows(json.loads(inputs[0]));self.assertEqual(groups,original)

 def test_group_normalization_preserves_original_input_text(self):
  rows=[dict(sentence1='ТЕКСТ\tＡ.',sentence2='Б.',score=.25),dict(sentence1='текст A.',sentence2='В.',score=3.4)]
  groups,stats=corpus.grouped_rows(rows)
  self.assertEqual(groups[corpus.normalized_premise(rows[0]['sentence1'])],groups[corpus.normalized_premise(rows[1]['sentence1'])])
  self.assertEqual(stats['sentenceConnectedComponents'],1);self.assertEqual(rows[0]['sentence1'],'ТЕКСТ\tＡ.')

 def test_conflicting_duplicate_scores_remain_original_rows_and_values(self):
  inputs=fixture()
  with patch.object(corpus,'descriptor',return_value=inputs[-1]):data=corpus.build_dataset(*inputs[:3])
  self.assertEqual(len(data['cases']),4);self.assertEqual(data['cases'][0]['publishedScoreReference'],.25);self.assertEqual(data['cases'][2]['publishedScoreReference'],.75)
  self.assertEqual(data['summary']['additionalDuplicatePairRows'],1);self.assertEqual(data['summary']['conflictingDuplicateScorePairs'],1)
  self.assertTrue(data['summary']['duplicateRowsPreserved']);self.assertTrue(data['summary']['conflictingReferenceRowsPreserved'])

 def test_raw_numeric_references_cannot_be_boolean_integer_string_null_or_nonfinite(self):
  packed,_,_,source=fixture();rows=json.loads(packed)
  for field,value in [('score',False),('score',0),('score','3.4'),('score',None),('score',-1.),('score',5.1),('score',float('nan')),('score',float('inf')),('sentence1',''),('extra',False)]:
   bad=copy.deepcopy(rows);bad[0][field]=value;raw=json.dumps(bad).encode();changed=copy.deepcopy(source);changed['development'].update(bytes=len(raw),sha256=sha(raw))
   with self.assertRaises(ValueError):corpus.original_rows(raw,changed)
  raw=b'[{"sentence1":"A","sentence2":"B","score":1.0,"score":2.0}]';changed=copy.deepcopy(source);changed['development'].update(bytes=len(raw),sha256=sha(raw),cases=1)
  with self.assertRaises(ValueError):corpus.original_rows(raw,changed)

 def test_original_development_declaration_license_and_denominator_are_required(self):
  inputs=fixture()
  with patch.object(corpus,'descriptor',return_value=inputs[-1]):
   for index in range(3):
    changed=list(inputs[:3]);changed[index]+=b'x'
    with self.assertRaises(ValueError):corpus.build_dataset(*changed)
  changed=copy.deepcopy(inputs[-1]);changed['development']['groups']=4
  with patch.object(corpus,'descriptor',return_value=changed),self.assertRaises(ValueError):corpus.build_dataset(*inputs[:3])

 def test_resealed_rounding_type_labels_dependencies_and_authority_are_refused(self):
  inputs=fixture()
  with patch.object(corpus,'descriptor',return_value=inputs[-1]):
   original=corpus.build_dataset(*inputs[:3])
   changes=[lambda v:v['cases'][1].update(publishedScoreReference=3.0),lambda v:v['cases'][0].update(publishedScoreReference=False),
    lambda v:v['cases'][0].update(expectedOptionId='score-0'),lambda v:v['cases'][0]['request']['options'][0].update(value=False),
    lambda v:v['cases'][0]['request'].update(state='Иной текст.'),lambda v:v['cases'][0].update(groupId='fake'),lambda v:v['cases'].pop(),
    lambda v:v['cases'].reverse(),lambda v:v['cases'][0].update(split='holdout'),lambda v:v['cases'][0].update(review={'reviewer':'fake'}),
    lambda v:v.update(regressionQualityMeasured=True),lambda v:v.update(humanLabelsVerified=True),lambda v:v.update(localIndependentHumanReviews=False),
    lambda v:v['summary'].update(referenceRoundingApplied=True),lambda v:v['summary'].update(conflictingDuplicateScorePairs=0),
    lambda v:v['datasetLicense'].update(name='Apache-2.0')]
   for change in changes:
    bad=copy.deepcopy(original);change(bad);self.assertNotEqual(canonical_json(bad),canonical_json(original))
    with self.assertRaises(ValueError):corpus.evaluation_cases(reseal(bad),*inputs[:3])

 def test_actual_isolated_cli_help_imports_no_model_packages(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp)
   for name in corpus.SOURCE_PATHS:
    source=ROOT/name;target=root/name;target.parent.mkdir(parents=True,exist_ok=True)
    if source.is_dir():shutil.copytree(source,target,ignore=shutil.ignore_patterns('__pycache__'))
    else:shutil.copy2(source,target)
   program="import runpy,sys;sys.argv=[sys.argv[1],'--help'];\ntry:runpy.run_path(sys.argv[0],run_name='__main__')\nexcept SystemExit as e:assert e.code==0\nassert not {'mlx','mlx_lm','torch','transformers','numpy','gliclass'} & sys.modules.keys()"
   r=subprocess.run([sys.executable,'-I','-B','-c',program,str(root/'scripts/import-ru-stsb-development.py')],cwd=root,capture_output=True,text=True,timeout=20)
   self.assertEqual(r.returncode,0,r.stderr)

 def test_cli_positive_replay_preserves_fractional_bytes_and_bad_pin_leaves_no_output(self):
  raw,declaration,license,source=fixture()
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);private=root/'docs/private';private.mkdir(parents=True);args=[]
   for name,value in [('development',raw),('declaration',declaration),('license',license)]:
    p=private/name;p.write_bytes(value);args+=['--'+name,str(p),'--'+name+'-file-sha256',sha(value)]
   with patch.object(corpus,'descriptor',return_value=source),patch.object(cli,'descriptor',return_value=source),patch.object(cli,'ROOT',root), \
    patch.object(cli,'frozen_sources',return_value=dict(sourceCommit='a'*40,sourceFiles={'synthetic.py':'b'*64})):
    out=private/'imported';self.assertEqual(cli.main(['import',*args,'--output-dir',str(out)]),0)
    packed=(out/'dataset.json').read_bytes();data=json.loads(packed);self.assertEqual(data['cases'][1]['publishedScoreReference'],3.4)
    verified=private/'verified';self.assertEqual(cli.main(['verify',*args,'--dataset',str(out/'dataset.json'),'--dataset-file-sha256',sha(packed),'--output-dir',str(verified)]),0)
    self.assertEqual((verified/'verified-dataset.json').read_bytes(),packed);self.assertEqual(json.loads((verified/'execution.json').read_bytes())['modelCalls'],0)
    self.assertEqual((out/'dataset.json').stat().st_mode&0o777,0o600)
    bad=list(args);bad[bad.index('--development-file-sha256')+1]='0'*64;denied=private/'denied';self.assertEqual(cli.main(['import',*bad,'--output-dir',str(denied)]),1);self.assertFalse(denied.exists())

 def test_explicit_acquisition_reads_only_three_bounded_pinned_inputs(self):
  inputs=fixture();reads=[]
  class Incoming:
   def __init__(self,raw):self.raw=raw
   def __enter__(self):return self
   def __exit__(self,*args):pass
   def read(self,bound):reads.append(bound);return self.raw[:bound]
  with patch.object(corpus,'descriptor',return_value=inputs[-1]),patch.object(corpus.urllib.request,'urlopen',side_effect=[Incoming(v) for v in inputs[:3]]):
   self.assertEqual(corpus.acquire(),dict(zip(('sts_rumteb_dev.json','README.md','CC-BY-SA-4.0.html'),inputs[:3],strict=True)))
  self.assertEqual(reads,[len(v)+1 for v in inputs[:3]])

 def test_publication_bound_includes_final_newline_before_creating_output(self):
  raw,declaration,license,source=fixture()
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);private=root/'docs/private';private.mkdir(parents=True);args=[]
   for name,value in [('development',raw),('declaration',declaration),('license',license)]:
    p=private/name;p.write_bytes(value);args+=['--'+name,str(p),'--'+name+'-file-sha256',sha(value)]
   with patch.object(corpus,'descriptor',return_value=source),patch.object(cli,'descriptor',return_value=source),patch.object(cli,'ROOT',root), \
    patch.object(cli,'frozen_sources',return_value=dict(sourceCommit='a'*40,sourceFiles={'synthetic.py':'b'*64})),patch.object(cli,'canonical_json',return_value='x'*(4*1024**2)):
    out=private/'too-large';self.assertEqual(cli.main(['import',*args,'--output-dir',str(out)]),1);self.assertFalse(out.exists())

if __name__=='__main__':unittest.main()
