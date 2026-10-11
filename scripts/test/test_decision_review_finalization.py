from copy import deepcopy
import hashlib
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

from decision_runtime.annotations import finalize_reviews
from decision_runtime.artifacts import sealed
from decision_runtime.contracts import parse_json
from scripts.lib.decision_review_adjudication import encoded
from scripts.lib.decision_review_finalization import prepare_finalization, verify_finalization
from scripts.lib.decision_review_finalization_cli import REVIEW_INPUTS, ADJUDICATION_INPUTS
from scripts.lib.decision_review_pair import compare_pair
import scripts.test.test_decision_adjudication_session as fixtures
import scripts.test.test_decision_review_form as form_fixtures

ROOT = Path(__file__).resolve().parents[2]
def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), ROOT/'scripts'/name)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module
cli = load('finalize-decision-reviews.py'); verifier = load('verify-decision-review-finalization.py')
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
reseal = lambda value: sealed({key:item for key,item in value.items() if key != 'sha256'})


class ReviewFinalizationTest(unittest.TestCase):
    def setUp(self):
        self.helper = fixtures.AdjudicationSessionTest(); self.helper.setUp(); self.addCleanup(self.helper.doCleanups)
        self.root = self.helper.root; self.private = self.helper.private
        self.first = self.helper.first; self.second = self.helper.second
        self.comparison = self.root/'comparison.json'
        code, directory, _, _, _ = self.helper.invoke('1\nSynthetic A\ny\n2\nSynthetic B\ny\n:submit\ny\n')
        self.assertEqual(code, 0)
        paths = [directory/'session.json', self.helper.paths['adjudication.review.blank.json'], directory/'review.json',
                 self.helper.paths['packet.json'], self.helper.paths['adjudication.review.blank.json'], self.helper.paths['case-notes.json']]
        self.adjudication = tuple(value for path in paths for value in (path, sha(path)))

    def prepare(self, adjudication=None):
        return prepare_finalization(self.first, self.second, self.comparison, sha(self.comparison),
                                    self.adjudication if adjudication is None else adjudication)

    def written(self, outputs=None):
        outputs = outputs or self.prepare()
        report = self.private/'finalization.json'; dataset = self.private/'dataset.json'
        report.write_bytes(encoded(outputs['finalization.json'])); dataset.write_bytes(encoded(outputs['dataset.json']))
        return report, dataset

    def verify(self, report, dataset, adjudication=None):
        return verify_finalization(self.first, self.second, self.comparison, sha(self.comparison),
            report, sha(report), dataset, sha(dataset), self.adjudication if adjudication is None else adjudication)

    def test_completed_adjudication_reconstructs_pair_and_preserves_every_record(self):
        outputs = self.prepare(); dataset = outputs['dataset.json']; report = outputs['finalization.json']
        reviews = [parse_json(binding[4].read_bytes()) for binding in (self.first,self.second,self.adjudication)]
        self.assertEqual(dataset, finalize_reviews(*reviews))
        self.assertEqual([case['id'] for case in dataset['cases']], ['fixture-0','fixture-1','fixture-2'])
        self.assertEqual([case['expectedOptionId'] for case in dataset['cases']], ['yes','yes','no'])
        self.assertEqual([len(case['review']['records']) for case in dataset['cases']], [3,2,3])
        self.assertEqual(report['agreedCases'],1); self.assertEqual(report['adjudicatedCases'],2)
        self.assertTrue(report['sourceReviewsRevalidated']); self.assertTrue(report['handoffReconstructedFromSourceReviews'])
        for key in ('reviewerIdentityVerified','humanExecutionVerified','independentReviewVerified','expertQualificationsVerified',
                    'routingEnabled','classificationAccuracyMeasured','expertiseInferredFromLabelSource'):
            self.assertIs(report[key],False)
        result = self.verify(*self.written(outputs)); self.assertEqual(result['status'],'pass')
        self.assertTrue(result['sourceReviewsRevalidated']); self.assertEqual(result['modelCallsDuringVerification'],0)

    def test_agreement_needs_no_adjudication_and_refuses_extra_artifacts(self):
        self.second = self.helper.helper.binding('agreeing', self.helper.helper.b)
        self.comparison.write_bytes(encoded(compare_pair(self.first,self.second)))
        outputs = prepare_finalization(self.first,self.second,self.comparison,sha(self.comparison))
        self.assertEqual(outputs['finalization.json']['adjudicatedCases'],0)
        self.assertIsNone(outputs['finalization.json']['adjudicationVerification'])
        self.assertIsNone(outputs['finalization.json']['adjudicationBindings'])
        report,dataset=self.written(outputs)
        self.assertEqual(verify_finalization(self.first,self.second,self.comparison,sha(self.comparison),
                         report,sha(report),dataset,sha(dataset))['status'],'pass')
        with self.assertRaises(ValueError):self.prepare()

    def test_browser_import_and_terminal_review_interoperate_without_translation_authority(self):
        bundle=form_fixtures.bundle_fixture()
        receipt,review=form_fixtures.receipt(bundle,form_fixtures.exported(bundle,complete=True))
        first=[]
        for name,value in [('browser-session',receipt),('browser-initial',bundle['blank']),('browser-saved',review)]:
            path=self.private/(name+'.json');path.write_bytes(encoded(value));first.extend((path,sha(path)))
        second=self.helper.helper.binding('terminal-other',bundle['blank'],answers=['other']*3)
        comparison=self.private/'mixed-comparison.json';comparison.write_bytes(encoded(compare_pair(first,second)))
        output=prepare_finalization(first,second,comparison,sha(comparison))
        self.assertEqual(output['finalization.json']['adjudicatedCases'],0)
        self.assertFalse(output['finalization.json']['reviewVerifications'][0]['translationEquivalenceVerified'])
        self.assertFalse(output['finalization.json']['expertQualificationsVerified'])
        self.assertTrue(all(case['expectedOptionId']=='other' for case in output['dataset.json']['cases']))

    def test_missing_or_partial_adjudication_bindings_are_refused(self):
        for binding in (None,self.adjudication[:-2]):
            with self.subTest(binding=binding),self.assertRaises(ValueError):
                prepare_finalization(self.first,self.second,self.comparison,sha(self.comparison),binding)

    def test_complete_unsubmitted_and_failed_adjudication_are_refused(self):
        for status, dialogue in [('partial','1\nSynthetic A\ny\n2\nSynthetic B\ny\n:quit\n'),
                                  ('failed','1\nSynthetic A\ny\n2\nSynthetic B\ny\n:quit\n')]:
            _, directory, receipt, saved, _ = self.helper.invoke(dialogue,name=status)
            if status=='failed':
                receipt.update(status='failed',failureType='OSError')
                (directory/'session.json').write_bytes(encoded(reseal(receipt)))
            binding=list(self.adjudication)
            binding[0:2]=[directory/'session.json',sha(directory/'session.json')]
            binding[4:6]=[directory/'review.json',sha(directory/'review.json')]
            with self.subTest(status=status),self.assertRaises(ValueError):self.prepare(binding)

    def test_partial_or_failed_source_review_is_refused_even_with_final_adjudication(self):
        for state in ('partial','failed'):
            second = self.helper.helper.binding(state,self.helper.helper.b,state=state)
            with self.subTest(state=state),self.assertRaises(ValueError):
                prepare_finalization(self.first,second,self.comparison,sha(self.comparison),self.adjudication)

    def test_different_submitted_pair_cannot_reuse_old_valid_handoff(self):
        self.second=self.helper.helper.binding('other-second',self.helper.helper.b,answers=['no','yes','no'],
                                               rationales=['Different rationale.']*3)
        self.comparison.write_bytes(encoded(compare_pair(self.first,self.second)))
        with self.assertRaisesRegex(ValueError,'handoff differs'):self.prepare()

    def test_comparison_cannot_change_counts_authority_or_scalar_types(self):
        original=self.comparison.read_bytes()
        for updates,seal in [({'agreementCount':True},False),({'humanExecutionVerified':True},True),
                             ({'agreementCount':3},True),({'sourceReviewsRevalidated':True},True)]:
            value=parse_json(original);value.update(updates)
            self.comparison.write_bytes(encoded(reseal(value) if seal else value))
            with self.subTest(updates=updates),self.assertRaises(ValueError):self.prepare()

    def test_all_external_source_and_adjudication_pins_are_rechecked(self):
        for label,binding in [('first',self.first),('second',self.second),('adjudication',self.adjudication)]:
            for offset in range(1,len(binding),2):
                changed=list(binding);changed[offset]='0'*64
                args=[self.first,self.second,self.comparison,sha(self.comparison),self.adjudication]
                args[{'first':0,'second':1,'adjudication':4}[label]]=changed
                with self.subTest(label=label,offset=offset),self.assertRaises(ValueError):prepare_finalization(*args)

    def test_dataset_label_provenance_record_split_or_order_drift_is_refused(self):
        report,dataset=self.written();original=dataset.read_bytes()
        mutations=[lambda value:value['cases'][0].update(expectedOptionId='no'),
                   lambda value:value['cases'][0]['review']['records'].pop(),
                   lambda value:value['cases'][0].update(split='development' if value['cases'][0]['split'] != 'development' else 'holdout'),
                   lambda value:value['cases'].reverse(),
                   lambda value:value['cases'][0]['provenance'].update(reference='Changed source')]
        for mutation in mutations:
            value=parse_json(original);mutation(value);dataset.write_bytes(encoded(value))
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):self.verify(report,dataset)

    def test_resealed_report_cannot_claim_authentication_or_forge_dataset_binding(self):
        report,dataset=self.written();original=report.read_bytes()
        for updates in ({'humanExecutionVerified':True},{'datasetFileSha256':'0'*64},
                        {'labelsMaterialized':True},{'caseCount':99},{'classificationAccuracyMeasured':True}):
            value=parse_json(original);value.update(updates);report.write_bytes(encoded(reseal(value)))
            with self.subTest(updates=updates),self.assertRaises(ValueError):self.verify(report,dataset)

    def test_duplicate_members_and_wrong_output_pins_are_refused(self):
        report,dataset=self.written();original=report.read_bytes()
        report.write_bytes(b'{"status":"finalized",'+original[1:])
        with self.assertRaises(ValueError):self.verify(report,dataset)
        report.write_bytes(original)
        for offset in (5,7):
            args=[self.first,self.second,self.comparison,sha(self.comparison),report,sha(report),dataset,sha(dataset),self.adjudication]
            args[offset]='0'*64
            with self.subTest(offset=offset),self.assertRaises(ValueError):verify_finalization(*args)

    def test_deterministic_output_and_input_bytes_are_preserved(self):
        paths=set([*self.first[::2],*self.second[::2],*self.adjudication[::2],self.comparison])
        before={path:path.read_bytes() for path in paths}
        a=self.prepare();b=self.prepare();self.assertEqual(encoded(a['dataset.json']),encoded(b['dataset.json']))
        self.assertEqual(encoded(a['finalization.json']),encoded(b['finalization.json']))
        self.assertEqual(before,{path:path.read_bytes() for path in paths})

    def arguments(self,output):
        args=['--output-dir',str(output),'--comparison',str(self.comparison),'--comparison-file-sha256',sha(self.comparison)]
        for prefix,binding,names in [('first',self.first,REVIEW_INPUTS), ('second',self.second,REVIEW_INPUTS),
                                    ('adjudication',self.adjudication,ADJUDICATION_INPUTS)]:
            for name,path,pin in zip(names,binding[::2],binding[1::2]):
                args+=['--'+prefix+'-'+name,str(path),'--'+prefix+'-'+name+'-file-sha256',pin]
        return args

    def test_real_cli_finalizes_then_verifies_without_overwriting(self):
        output=self.private/'finalized';args=self.arguments(output)
        with patch.object(cli,'ROOT',self.root):
            self.assertEqual(cli.main(args),0)
            original={p.name:p.read_bytes() for p in output.iterdir()}
            self.assertEqual(cli.main(args),1)
            self.assertEqual(original,{p.name:p.read_bytes() for p in output.iterdir()})
        verify_args=self.arguments(self.private/'verified')
        for name in ('finalization','dataset'):
            path=output/(name+'.json');verify_args+=['--'+name,str(path),'--'+name+'-file-sha256',sha(path)]
        with patch.object(verifier,'ROOT',self.root):self.assertEqual(verifier.main(verify_args),0)
        self.assertEqual(output.stat().st_mode&0o777,0o700)
        for path in output.iterdir():self.assertEqual(path.stat().st_mode&0o777,0o600)

    def test_partial_cli_binding_refuses_before_creating_directory(self):
        output=self.private/'refused';args=self.arguments(output)
        position=args.index('--adjudication-case-notes-file-sha256');args[position:position+2]=[]
        with patch.object(cli,'ROOT',self.root):self.assertEqual(cli.main(args),1)
        self.assertFalse(output.exists())

    def test_bad_source_cli_pin_refuses_before_creating_directory(self):
        output=self.private/'refused';args=self.arguments(output)
        args[args.index('--first-output-review-file-sha256')+1]='0'*64
        with patch.object(cli,'ROOT',self.root):self.assertEqual(cli.main(args),1)
        self.assertFalse(output.exists())

    def test_failed_second_write_leaves_no_completed_finalization_receipt(self):
        output=self.private/'interrupted';args=self.arguments(output)
        write=cli.write_json_new
        def interrupted(path,value):
            if path.name=='finalization.json':raise OSError('Synthetic receipt write failure')
            write(path,value)
        with patch.object(cli,'ROOT',self.root),patch.object(cli,'write_json_new',side_effect=interrupted):
            self.assertEqual(cli.main(args),1)
        self.assertTrue((output/'dataset.json').is_file())
        self.assertFalse((output/'finalization.json').exists())


if __name__=='__main__':unittest.main()
