import copy
import http.client
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from decision_runtime.contracts import Policy,Request
from decision_runtime.engine import DecisionEngine,Scores
from scripts.lib import decision_public_outcome_sensitivity as sensitivity
from scripts.lib.decision_public_permutation_diagnostic import run_phase
from scripts.test import test_decision_public_permutation_diagnostic as fixtures
from workers.local_decisions import LocalDecisionClient
from decision_runtime import model_store

ROOT=Path(__file__).resolve().parents[2]
SPEC=importlib.util.spec_from_file_location('public_option_diagnostic_replay_cli',ROOT/'scripts/verify-public-support-permutation-diagnostic.py')
cli=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(cli)


def phase(*,bias=False):
    context,permutation=fixtures.corpus();clock=fixtures.Clock()
    value=run_phase(None,permutation['inputs'],context['profile'],clock=clock,
                    measure=fixtures.measurements(permutation['inputs'],context['profile'],clock,position_bias=bias))
    return context,permutation,value


def args(directory,pins,output):
    return ['--evidence-dir',str(directory),'--context-profile',str(directory/'context.json'),
            '--permutation-context',str(directory/'permutation.json'),'--context-profile-file-sha256',pins['context_sha'],
            '--permutation-context-file-sha256',pins['permutation_sha'],'--plan-file-sha256',pins['plan_sha'],
            '--result-file-sha256',pins['result_sha'],'--output-dir',str(output)]


class PublicOptionDiagnosticReplayTest(unittest.TestCase):
    def test_invariant_and_conflicting_accepted_values_keep_original_case_group_denominators(self):
        for bias in (False,True):
            context,permutation,value=phase(bias=bias);report=sensitivity.decompose(value,permutation['inputs'],context['profile']);s=report['summary']
            self.assertEqual(s['sourceCases'],context['developmentCases']);self.assertEqual(s['comparedSourceCases'],context['contextEligibleCases'])
            self.assertEqual(s['wholeContextRejectedSourceCases'],context['contextTooLongCases']);self.assertEqual(s['scheduledVariants'],len(permutation['inputs']))
            self.assertEqual(s['argmaxChangedSourceCases'],context['contextEligibleCases'] if bias else 0)
            self.assertEqual(s['conflictingAcceptedValuesSourceCases'],context['contextEligibleCases'] if bias else 0)
            self.assertEqual(s['mixedAdmissionSourceCases'],0);self.assertEqual(report['newModelCalls'],0);self.assertFalse(report['classificationAccuracyMeasured'])
            self.assertFalse(report['causalPositionBiasEstablished']);self.assertFalse(report['routingEnabled'])
            for row in report['cases']:
                if row['wholeContextRejected']:self.assertIsNone(row['argmaxChanged']);self.assertFalse(row['comparisonSupported'])

    def test_confidence_threshold_switch_keeps_argmax_and_has_one_accepted_value(self):
        context,permutation=fixtures.corpus();inputs=permutation['inputs'];cases={row['id']:row for row in inputs};clock=fixtures.Clock()
        selected={row['sourceCaseId']:row['optionOrder'][0] for row in inputs if row['orderId']=='rotation_0'}
        class SyntheticBackend:
            identity=context['profile']['model']
            def score(self,request):
                case=cases[request.id];high=2. if case['orderId']=='rotation_1' else 8.
                return Scores([high if option.id==selected[case['sourceCaseId']] else 0. for option in request.options],case['inputTokens'])
        engine=DecisionEngine(SyntheticBackend(),Policy.from_dict({k:v for k,v in context['profile']['policy'].items() if k!='sha256'}))
        def measure(_client,request,_profile,_timeout):
            case=cases[request.id];body=engine.decide(case['request']) if case['contextEligible'] else engine.error('context_too_long',request)
            body['durationMs']=5.;clock.now+=.031
            return {'status':body['status'],'reason':body['reason'],'callerMs':30.,'wallMs':31.,'observation':{'result':body,
                'callerTiming':{'schemaVersion':'agat.decision.caller-timing.v1','clock':'monotonic','boundary':'local_http_call','durationMs':30.}}}
        value=run_phase(None,inputs,context['profile'],clock=clock,measure=measure);s=sensitivity.decompose(value,inputs,context['profile'])['summary']
        self.assertEqual(s['argmaxChangedSourceCases'],0);self.assertEqual(s['conflictingAcceptedValuesSourceCases'],0)
        self.assertEqual(s['statusChangedSourceCases'],context['contextEligibleCases']);self.assertEqual(s['valueChangedSourceCases'],context['contextEligibleCases'])
        self.assertEqual(s['acceptedAndAbstainedSourceCases'],context['contextEligibleCases']);self.assertEqual(s['originalAcceptedLaterAbstainedSourceCases'],context['contextEligibleCases'])

    def test_partial_or_rewritten_phase_cannot_be_decomposed_as_complete(self):
        context,permutation,original=phase()
        for change in (lambda v:v['rows'].pop(),lambda v:v['summary'].update(sourceCases=1),lambda v:v.update(complete=False)):
            value=copy.deepcopy(original);change(value)
            with self.assertRaises(ValueError):sensitivity.decompose(value,permutation['inputs'],context['profile'])
        # A real tokenizer may move one reordered prompt across the admission limit.
        inputs=copy.deepcopy(permutation['inputs']);inputs[1].update(partTokens=[1949,100],inputTokens=2049,contextEligible=False,contextExclusionReason='context_too_long')
        clock=fixtures.Clock();value=run_phase(None,inputs,context['profile'],clock=clock,measure=fixtures.measurements(inputs,context['profile'],clock))
        projected=sensitivity.decompose(value,inputs,context['profile'])['summary']
        self.assertEqual(projected['mixedAdmissionSourceCases'],1);self.assertEqual(projected['unsupportedComparisonSourceCases'],2)
        self.assertEqual(projected['sourceCases'],context['developmentCases']);self.assertEqual(projected['scheduledVariants'],len(inputs))

    def test_actual_offline_cli_double_replay_private_publication_without_model_network_or_native_process(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);commit=fixtures.repository(root);directory,pins=fixtures.write_fixture(root,commit);output=root/'docs/private/replay'
            actual_popen=subprocess.Popen
            def only_git(command,**kwargs):
                self.assertEqual(command[0],'git');self.assertIn(command[1],{'ls-tree','archive'});self.assertFalse(kwargs.get('shell',False));return actual_popen(command,**kwargs)
            identity=('a'*40,{'synthetic-verifier.py':'b'*64})
            with (patch.object(cli,'ROOT',root),patch.object(cli.launcher,'frozen_sources',return_value=identity) as source,
                 patch.object(http.client,'HTTPConnection') as network,patch.object(LocalDecisionClient,'decide') as model,
                 patch.object(model_store,'verify_manifest') as weights,patch.object(subprocess,'Popen',side_effect=only_git)):
                self.assertEqual(cli.main(args(directory,pins,output)),0);network.assert_not_called();model.assert_not_called();weights.assert_not_called()
                self.assertEqual(source.call_count,2)
            value=json.loads((output/'verification.json').read_bytes());self.assertEqual(value['status'],'pass')
            self.assertFalse(value['liveCleanupVerified']);self.assertEqual(value['outcomeSensitivity']['analysisKind'],'posthoc_descriptive_decomposition')
            self.assertEqual(output.stat().st_mode&0o777,0o700);self.assertEqual((output/'verification.json').stat().st_mode&0o777,0o600)

    def test_artifact_mutation_after_first_replay_and_current_source_drift_cannot_publish(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);commit=fixtures.repository(root);identity=('a'*40,{'synthetic-verifier.py':'b'*64})
            for drift in ('artifact','source'):
                directory,pins=fixtures.write_fixture(root,commit);output=root/'docs/private'/drift
                original=cli.decompose
                def mutate(*values):
                    result=original(*values);(directory/'requests.jsonl').write_bytes(b'changed after first replay');return result
                sources=[identity,('c'*40,identity[1])] if drift=='source' else [identity,identity]
                with patch.object(cli,'ROOT',root),patch.object(cli.launcher,'frozen_sources',side_effect=sources):
                    if drift=='artifact':
                        with patch.object(cli,'decompose',side_effect=mutate):self.assertEqual(cli.main(args(directory,pins,output)),1)
                    else:self.assertEqual(cli.main(args(directory,pins,output)),1)
                self.assertFalse(output.exists())

    def test_existing_or_nonprivate_output_rejected_before_replay(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);private=root/'docs/private';private.mkdir(parents=True)
            pins={key:'0'*64 for key in ('context_sha','permutation_sha','plan_sha','result_sha')}
            with patch.object(cli,'ROOT',root),patch.object(cli,'verify') as verifier,patch.object(cli.launcher,'frozen_sources') as sources:
                for output in (private,root/'outside'):
                    self.assertEqual(cli.main(args(private,pins,output)),1);verifier.assert_not_called();sources.assert_not_called()


if __name__=='__main__':unittest.main()
