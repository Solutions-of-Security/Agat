import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from decision_runtime.artifacts import sealed
from decision_runtime.contracts import Request
from scripts.lib import decision_public_permutations as permutations
from scripts.test.test_decision_public_load import context_fixture

ROOT=Path(__file__).resolve().parents[2]
SPEC=importlib.util.spec_from_file_location('permutation_profiler_cli',ROOT/'scripts/profile-public-support-permutations.py')
cli=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(cli)


def fixture():
    context=context_fixture();variants=permutations.variants(context);cases={r['id']:r for r in context['inputs']}
    rows=[{'id':r['id'],'inputSha256':r['inputSha256'],'partTokens':cases[r['sourceCaseId']]['partTokens'][:],
           'inputTokens':cases[r['sourceCaseId']]['inputTokens'],'labelContinuationsVerified':True} for r in variants]
    return context,variants,rows


def receipt(context,rows):
    inputs=permutations.token_inventory(context,rows)
    return sealed({'schemaVersion':permutations.SCHEMA,'status':'tokenized_without_inference','createdAt':'2026-10-08T02:00:00.000Z',
        'sourceCommit':'a'*40,'sourceFiles':{'synthetic-fixture.py':'b'*64},'originalContextFileSha256':'c'*64,
        'originalContextSealSha256':context['sha256'],'originalPoolSha256':context['poolSha256'],
        **{k:context[k] for k in ('profileFileSha256','profileSha256','manifestFileSha256','model','tokenizerEnvironment')},
        'rule':permutations.RULE,'budget':permutations.BUDGET.copy(),'summary':permutations.summary(context,inputs),'inputs':inputs,
        'stateTranslationApplied':False,'inputTruncationApplied':False,'semanticOptionsChanged':False,'statisticalIndependenceVerified':False,
        'classificationAccuracyMeasured':False,'referenceLabels':0,'predictions':0,'modelCalls':0,'calibrationRequestsTokenized':0,
        'holdoutRequestsTokenized':0,'ownersAppointed':False,'sloAccepted':False,'routingEnabled':False,'qualification':'not_assessed'})


class PublicPermutationTest(unittest.TestCase):
    def test_whole_original_cases_and_groups_preserve_state_question_semantic_options_and_repeat(self):
        context,variants,rows=fixture();self.assertEqual({r['sourceCaseId'] for r in variants},{r['id'] for r in context['inputs']})
        for case in context['inputs']:
            case_rows=[r for r in variants if r['sourceCaseId']==case['id']]
            self.assertEqual(len(case_rows),len(permutations.orders(len(case['request']['options']))))
            for row in case_rows:
                self.assertEqual(row['request']['state'],case['request']['state']);self.assertEqual(row['request']['question'],case['request']['question'])
                self.assertEqual({r['id']:r for r in row['request']['options']},{r['id']:r for r in case['request']['options']})
                self.assertEqual(row['sourceGroupId'],case['groupId'])
            self.assertEqual(case_rows[0]['inputSha256'],case_rows[-1]['inputSha256']);self.assertNotEqual(case_rows[0]['id'],case_rows[-1]['id'])
        report=receipt(context,rows);self.assertEqual(permutations.validate_profile(report,context,'c'*64),report)
        self.assertEqual(report['summary']['sourceCases'],len(context['inputs']));self.assertEqual(report['summary']['sourceGroups'],context['developmentGroups'])
        self.assertGreater(report['summary']['contextTooLongVariants'],0);self.assertEqual(report['modelCalls'],0)
        self.assertFalse(report['statisticalIndependenceVerified']);self.assertFalse(report['routingEnabled'])

    def test_cyclic_positions_are_balanced_and_control_repeat_is_explicit(self):
        for count in range(2,11):
            rows=permutations.orders(count);cyclic=[indices for name,indices in rows if name.startswith('rotation_')]
            self.assertEqual(len(cyclic),count)
            for semantic in range(count):self.assertEqual(sorted(order.index(semantic) for order in cyclic),list(range(count)))
            self.assertEqual(rows[-1],('original_repeat',list(range(count))))
            self.assertEqual(len({tuple(order) for name,order in rows[:-1]}),len(rows)-1)
        for count in (True,1,11):
            with self.assertRaises(ValueError):permutations.orders(count)

    def test_tokenizer_cannot_omit_reorder_rebind_or_hide_original_token_drift(self):
        context,variants,rows=fixture()
        for change in (lambda r:r.pop(),lambda r:r.reverse(),lambda r:r[0].update(inputSha256='0'*64),
                       lambda r:r[0].update(inputTokens=True),lambda r:r[0].update(partTokens=[True,100]),
                       lambda r:r[0].update(labelContinuationsVerified=1),lambda r:r[0].update(partTokens=[r[0]['partTokens'][0]+1,r[0]['partTokens'][1]],inputTokens=r[0]['inputTokens']+1)):
            actual=copy.deepcopy(rows);change(actual)
            with self.assertRaises(ValueError):permutations.token_inventory(context,actual)
        # New order lengths are measured individually; they need not equal the original.
        actual=copy.deepcopy(rows);actual[1]['partTokens'][0]+=1;actual[1]['inputTokens']+=1
        result=permutations.token_inventory(context,actual);self.assertEqual(result[1]['inputTokens'],rows[1]['inputTokens']+1)

    def test_resealed_partial_transformed_or_qualified_recipe_rejected(self):
        context,_,rows=fixture();original=receipt(context,rows)
        for change in (lambda r:r['inputs'].pop(),lambda r:r['inputs'][1]['request'].update(state='transformed source'),
                       lambda r:r['inputs'][1]['request']['options'][0].update(description='changed meaning'),
                       lambda r:r.update(referenceLabels=False),lambda r:r.update(ownersAppointed=True),
                       lambda r:r.update(statisticalIndependenceVerified=True),lambda r:r['summary'].update(sourceCases=1),
                       lambda r:r['budget'].update(retries=1),lambda r:r['sourceFiles'].update({'../escape.py':'0'*64}),
                       lambda r:r.update(createdAt='2020-01-01T00:00:00.000Z')):
            value=copy.deepcopy(original);change(value);value=sealed({k:v for k,v in value.items() if k!='sha256'})
            with self.assertRaises(ValueError):permutations.validate_profile(value,context,'c'*64)

    def test_existing_output_rejected_before_any_model_tokenizer_or_process(self):
        with tempfile.TemporaryDirectory() as temporary:
            args=['--context-profile','missing','--context-profile-file-sha256','0'*64,'--manifest','missing','--runtime-python','missing','--output-dir',temporary]
            with patch.object(cli.subprocess,'run') as child,patch.object(cli,'verify_manifest') as model:
                self.assertEqual(cli.main(args),1);child.assert_not_called();model.assert_not_called()

    def test_cli_child_sees_whole_unlabelled_variants_offline_and_cannot_publish_after_context_drift(self):
        context,_,rows=fixture()
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);private=root/'docs/private';private.mkdir(parents=True)
            requirement=root/'decision_runtime/requirements-mlx.txt';requirement.parent.mkdir()
            requirement.write_text('\n'.join(name+'=='+version for name,version in context['tokenizerEnvironment']['packages'].items())+'\n')
            profile=root/cli.PROFILE_PATH;profile.parent.mkdir(parents=True);profile.write_text(json.dumps(context['profile']));context['profileFileSha256']=hashlib.sha256(profile.read_bytes()).hexdigest()
            manifest=private/'manifest.json';manifest.write_bytes(b'Synthetic manifest, never model weights.');context['manifestFileSha256']=hashlib.sha256(manifest.read_bytes()).hexdigest()
            context=sealed({k:v for k,v in context.items() if k!='sha256'});context_path=private/'context.json';context_path.write_text(json.dumps(context))
            digest=hashlib.sha256(context_path.read_bytes()).hexdigest();output=private/'variants';identity=('a'*40,{'synthetic-fixture.py':'b'*64})
            model={**context['model'],'files':{'tokenizer.json':context['profile']['model']['tokenizerSha256']}}
            args=['--context-profile',str(context_path),'--context-profile-file-sha256',digest,'--manifest',str(manifest),'--runtime-python','/synthetic-python','--output-dir',str(output)]
            def tokenize(command,**kwargs):
                self.assertEqual(json.loads(kwargs['input']),[row['request'] for row in permutations.variants(context)])
                self.assertIn('local_files_only=True',command[3]);self.assertIn('trust_remote_code=False',command[3]);self.assertEqual(kwargs['env']['HF_HUB_OFFLINE'],'1')
                return type('Child',(),{'returncode':0,'stdout':json.dumps({'rows':rows,'environment':context['tokenizerEnvironment']}).encode()})()
            with patch.object(cli,'ROOT',root),patch.object(cli,'sources_at'),patch.object(cli.launcher,'frozen_sources',return_value=identity),patch.object(cli,'verify_manifest',return_value=(model,private)),patch.object(cli.subprocess,'run',side_effect=tokenize):
                self.assertEqual(cli.main(args),0);report=json.loads((output/'permutation-context.json').read_bytes());self.assertEqual(report['modelCalls'],0)
                self.assertEqual(output.stat().st_mode&0o777,0o700);self.assertEqual((output/'permutation-context.json').stat().st_mode&0o777,0o600)
                args[-1]=str(private/'drift')
                def drift(command,**kwargs):
                    result=tokenize(command,**kwargs);context_path.write_bytes(b'changed during tokenization');return result
                with patch.object(cli.subprocess,'run',side_effect=drift):self.assertEqual(cli.main(args),1)
                self.assertFalse((private/'drift').exists())


if __name__=='__main__':unittest.main()
