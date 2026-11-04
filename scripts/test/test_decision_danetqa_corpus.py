"""Synthetic parser/Boolean/CLI contracts; no real human review or model inference."""
import copy, hashlib, importlib.util, io, json, shutil, subprocess, sys, tempfile, unittest, zipfile
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from decision_runtime.artifacts import sealed
from decision_runtime.contracts import canonical_json,Request
from scripts.lib import decision_danetqa_corpus as corpus
SPEC=importlib.util.spec_from_file_location('danetqa_cli',ROOT/'scripts/import-danetqa-development.py')
cli=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(cli)
sha=lambda raw:hashlib.sha256(raw).hexdigest()
reseal=lambda v:sealed({k:x for k,x in v.items() if k!='sha256'})

def fixture(rows=None):
 rows=rows if rows is not None else [dict(passage='Документ не подписан.',question='Документ подписан?',label=False,idx=0),
  dict(passage='Заявка зарегистрирована.',question='Есть заявка?',label=True,idx=1)]
 raw=''.join(json.dumps(v,ensure_ascii=False)+'\n' for v in rows).encode();buffer=io.BytesIO()
 with zipfile.ZipFile(buffer,'w',compression=zipfile.ZIP_DEFLATED) as archive:
  archive.writestr('DaNetQA/val.jsonl',raw);archive.writestr('DaNetQA/test.jsonl',b'not JSON; forbidden semantic input');archive.writestr('DaNetQA/train.jsonl',b'not JSON; forbidden semantic input')
 packed=buffer.getvalue();license=b'synthetic license fixture; no publisher claim'
 keys=[(corpus.normalized_premise(r['passage']),corpus.normalized_premise(r['question'])) for r in rows]
 source=dict(schemaVersion='synthetic-source',archive=dict(url='https://example.invalid/synthetic',bytes=len(packed),sha256=sha(packed)),
  development=dict(member='DaNetQA/val.jsonl',bytes=len(raw),sha256=sha(raw),cases=len(rows),groups=len({k[0] for k in keys}),
   labelCounts={name:sum(r['label'] is val for r in rows) for name,val in [('true',True),('false',False)]},
   normalizedDuplicatePairAdditionalRows=len(keys)-len(set(keys)),conflictingDuplicateReferencePairs=0),license=dict(url='https://example.invalid/synthetic-license',bytes=len(license),sha256=sha(license)))
 return packed,license,source

class DaNetQACorpusTests(unittest.TestCase):
 def test_closed_immutable_descriptor_rejects_test_member_pins_and_boolean_count_alias(self):
  original=corpus.descriptor(ROOT)
  changes=[lambda v:v['development'].update(member='DaNetQA/test.jsonl'),lambda v:v['archive'].update(sha256='a'*64),
   lambda v:v['license'].update(name='unknown'),lambda v:v.update(revision='a'*40),lambda v:v.update(references=[]),
   lambda v:v['development'].update(conflictingDuplicateReferencePairs=False),lambda v:v.update(routingEnabled=True)]
  for change in changes:
   bad=copy.deepcopy(original);change(bad)
   with tempfile.TemporaryDirectory() as temp:
    root=Path(temp);p=root/corpus.DESCRIPTOR;p.parent.mkdir(parents=True);p.write_text(json.dumps(bad))
    with self.assertRaises(ValueError):corpus.descriptor(root)

 def test_false_and_true_references_remain_typed_and_never_enter_requests(self):
  packed,license,source=fixture()
  with patch.object(corpus,'descriptor',return_value=source):
   data=corpus.build_dataset(packed,license);evaluation=corpus.evaluation_dataset(data,packed,license)
  self.assertEqual(len(evaluation['cases']),2)
  self.assertEqual([c['expectedOptionId'] for c in data['cases']],['false','true'])
  self.assertIs(data['cases'][0]['publishedBooleanReference'],False);self.assertIs(data['cases'][1]['publishedBooleanReference'],True)
  for c in data['cases']:
   request=c['request'];self.assertEqual(request['kind'],'boolean')
   self.assertEqual(set(request),{'schemaVersion','id','state','question','kind','options'})
   self.assertEqual([o['value'] for o in Request.from_dict(request).to_dict()['options']],[False,True])
   self.assertTrue(all(type(o['value']) is bool for o in request['options']))
  self.assertFalse(data['humanLabelsVerified']);self.assertEqual(data['localIndependentHumanReviews'],0)
  self.assertFalse(data['classificationAccuracyMeasured']);self.assertFalse(data['summary']['trainOrTestMembersRead'])

 def test_exact_duplicate_rows_are_preserved_and_share_passage_group(self):
  rows=[dict(passage='Общий текст.',question='Есть подтверждение?',label=False,idx=0),dict(passage='Общий текст.',question='Есть подтверждение?',label=False,idx=1),
   dict(passage='Другой текст.',question='Есть документ?',label=True,idx=2)]
  packed,license,source=fixture(rows)
  with patch.object(corpus,'descriptor',return_value=source):data=corpus.build_dataset(packed,license)
  self.assertEqual(len(data['cases']),3);self.assertEqual(data['summary']['normalizedDuplicatePairAdditionalRows'],1)
  self.assertEqual(data['cases'][0]['groupId'],data['cases'][1]['groupId']);self.assertNotEqual(data['cases'][0]['id'],data['cases'][1]['id'])
  self.assertTrue(data['summary']['duplicateRowsPreserved'])

 def test_normalized_grouping_preserves_original_passage_and_question(self):
  rows=[dict(passage='ТЕКСТ\tＡ.',question='Первый вопрос?',label=False,idx=0),dict(passage='текст A.',question='Второй вопрос?',label=True,idx=1)]
  packed,license,source=fixture(rows)
  with patch.object(corpus,'descriptor',return_value=source):data=corpus.build_dataset(packed,license)
  self.assertEqual([c['request']['state'] for c in data['cases']],[r['passage'] for r in rows])
  self.assertEqual([c['request']['question'] for c in data['cases']],[r['question'] for r in rows]);self.assertEqual(data['cases'][0]['groupId'],data['cases'][1]['groupId'])

 def test_conflicting_normalized_published_answers_refuse_instead_of_silent_selection(self):
  rows=[dict(passage='ТЕКСТ.',question='ВОПРОС?',label=False,idx=0),dict(passage='текст.',question='вопрос?',label=True,idx=1)]
  packed,license,source=fixture(rows)
  with patch.object(corpus,'descriptor',return_value=source),self.assertRaisesRegex(ValueError,'Conflicting duplicate'):corpus.build_dataset(packed,license)

 def test_raw_labels_cannot_be_numbers_strings_null_or_duplicate_json(self):
  _,_,source=fixture();original=[dict(passage='А.',question='Б?',label=False,idx=0),dict(passage='В.',question='Г?',label=True,idx=1)]
  for field,value in [('label',0),('label',1),('label','false'),('label',None),('idx',False),('idx',1),('idx',2),('passage',''),('question','x'*2001)]:
   bad=copy.deepcopy(original);bad[0][field]=value
   with self.assertRaises(ValueError):corpus.cases_from_rows('\n'.join(json.dumps(r) for r in bad).encode(),source)
  duplicate=b'{"passage":"A","question":"B","label":false,"label":true,"idx":0}\n{}'
  with self.assertRaises(ValueError):corpus.cases_from_rows(duplicate,source)

 def test_exact_original_archive_license_member_and_complete_inventory_are_required(self):
  packed,license,source=fixture()
  with patch.object(corpus,'descriptor',return_value=source):
   for archive,lic in [(packed+b'x',license),(packed,license+b'x')]:
    with self.assertRaises(ValueError):corpus.build_dataset(archive,lic)
  bad=copy.deepcopy(source);bad['development']['sha256']='a'*64
  with patch.object(corpus,'descriptor',return_value=bad),self.assertRaises(ValueError):corpus.build_dataset(packed,license)
  bad=copy.deepcopy(source);bad['development']['cases']=3
  with patch.object(corpus,'descriptor',return_value=bad),self.assertRaises(ValueError):corpus.build_dataset(packed,license)

 def test_resealed_reference_false_alias_and_scope_mutations_are_refused(self):
  packed,license,source=fixture()
  with patch.object(corpus,'descriptor',return_value=source):
   original=corpus.build_dataset(packed,license)
   changes=[lambda v:v['cases'][0].update(publishedBooleanReference=0),lambda v:v['cases'][0].update(expectedOptionId='true'),
    lambda v:v['cases'][0]['request']['options'][0].update(value=0),lambda v:v['cases'][0]['request'].update(kind='choice'),
    lambda v:v['cases'][0]['request'].update(state='Иной текст.'),lambda v:v['cases'][0].update(groupId='fake'),
    lambda v:v['cases'].reverse(),lambda v:v['cases'].pop(),lambda v:v['cases'][0].update(split='calibration'),
    lambda v:v['cases'][0].update(labelSource='assistant-reviewed'),lambda v:v['cases'][0].update(review={'reviewer':'fake'}),
    lambda v:v.update(localIndependentHumanReviews=False),lambda v:v.update(humanLabelsVerified=True),lambda v:v.update(routingEnabled=True),
    lambda v:v['summary'].update(duplicateRowsPreserved=False)]
   for change in changes:
    bad=copy.deepcopy(original);change(bad);self.assertNotEqual(canonical_json(bad),canonical_json(original))
    with self.assertRaises(ValueError):corpus.evaluation_dataset(reseal(bad),packed,license)

 def test_actual_isolated_cli_help_imports_no_model_packages(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp)
   for name in corpus.SOURCE_PATHS:
    source=ROOT/name;target=root/name;target.parent.mkdir(parents=True,exist_ok=True)
    if source.is_dir():shutil.copytree(source,target,ignore=shutil.ignore_patterns('__pycache__'))
    else:shutil.copy2(source,target)
   program="import runpy,sys;sys.argv=[sys.argv[1],'--help'];\ntry:runpy.run_path(sys.argv[0],run_name='__main__')\nexcept SystemExit as e:assert e.code==0\nassert not {'mlx','mlx_lm','torch','transformers','numpy','gliclass'} & sys.modules.keys()"
   result=subprocess.run([sys.executable,'-I','-B','-c',program,str(root/'scripts/import-danetqa-development.py')],cwd=root,capture_output=True,text=True,timeout=20)
   self.assertEqual(result.returncode,0,result.stderr)

 def test_cli_import_replay_preserves_false_bytes_and_bad_pin_leaves_no_output(self):
  packed,license,source=fixture()
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);private=root/'docs/private';private.mkdir(parents=True)
   archive=private/'DaNetQA.zip';archive.write_bytes(packed);lic=private/'LICENSE';lic.write_bytes(license)
   args=['--archive',str(archive),'--archive-file-sha256',sha(packed),'--license',str(lic),'--license-file-sha256',sha(license)]
   with patch.object(corpus,'descriptor',return_value=source),patch.object(cli,'descriptor',return_value=source),patch.object(cli,'ROOT',root), \
    patch.object(cli,'frozen_sources',return_value=dict(sourceCommit='a'*40,sourceFiles={'synthetic.py':'b'*64})):
    out=private/'imported';self.assertEqual(cli.main(['import',*args,'--output-dir',str(out)]),0)
    raw=(out/'dataset.json').read_bytes();data=json.loads(raw);self.assertIs(data['cases'][0]['publishedBooleanReference'],False)
    self.assertIs(data['cases'][0]['request']['options'][0]['value'],False)
    replay=private/'verified';self.assertEqual(cli.main(['verify',*args,'--dataset',str(out/'dataset.json'),'--dataset-file-sha256',sha(raw),'--output-dir',str(replay)]),0)
    self.assertEqual((replay/'verified-dataset.json').read_bytes(),raw);self.assertEqual(json.loads((replay/'execution.json').read_bytes())['modelCalls'],0)
    self.assertEqual(out.stat().st_mode&0o777,0o700);self.assertEqual((out/'dataset.json').stat().st_mode&0o777,0o600)
    bad=list(args);bad[bad.index('--archive-file-sha256')+1]='0'*64;denied=private/'wrong-pin'
    self.assertEqual(cli.main(['import',*bad,'--output-dir',str(denied)]),1);self.assertFalse(denied.exists())

 def test_explicit_acquisition_reads_only_bounded_pinned_archive_and_license(self):
  packed,license,source=fixture();reads=[]
  class Response:
   def __init__(self,raw):self.raw=raw
   def __enter__(self):return self
   def __exit__(self,*args):pass
   def read(self,n):reads.append(n);return self.raw[:n]
  with patch.object(corpus,'descriptor',return_value=source),patch.object(corpus.urllib.request,'urlopen',side_effect=[Response(packed),Response(license)]):
   self.assertEqual(corpus.acquire(),{'DaNetQA.zip':packed,'LICENSE':license})
  self.assertEqual(reads,[len(packed)+1,len(license)+1])
  with patch.object(corpus,'descriptor',return_value=source),patch.object(corpus.urllib.request,'urlopen',side_effect=[Response(packed),Response(license+b'x')]),self.assertRaises(ValueError):corpus.acquire()

 def test_publication_bound_includes_newline_and_refuses_before_directory_creation(self):
  packed,license,source=fixture()
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);private=root/'docs/private';private.mkdir(parents=True)
   archive=private/'DaNetQA.zip';archive.write_bytes(packed);lic=private/'LICENSE';lic.write_bytes(license)
   args=['import','--archive',str(archive),'--archive-file-sha256',sha(packed),'--license',str(lic),'--license-file-sha256',sha(license),
    '--output-dir',str(private/'oversized')]
   with patch.object(corpus,'descriptor',return_value=source),patch.object(cli,'descriptor',return_value=source),patch.object(cli,'ROOT',root), \
    patch.object(cli,'frozen_sources',return_value=dict(sourceCommit='a'*40,sourceFiles={'synthetic.py':'b'*64})), \
    patch.object(cli,'canonical_json',return_value='x'*(4*1024**2)):
    self.assertEqual(cli.main(args),1);self.assertFalse((private/'oversized').exists())

if __name__=='__main__':unittest.main()
