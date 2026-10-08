import copy
import hashlib
import http.client
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from decision_runtime.artifacts import sealed
from decision_runtime.contracts import Policy, Request, fingerprint
from decision_runtime.engine import DecisionEngine, Scores
from decision_runtime.metrics import DecisionMetrics, outcome
from scripts.lib import decision_public_permutation_diagnostic as diagnostic
from scripts.lib.decision_public_permutations import SOURCE_PATHS, variants
from scripts.test.test_decision_public_load import context_fixture
from scripts.test.test_decision_public_permutations import receipt

ROOT=Path(__file__).resolve().parents[2]
SPEC=importlib.util.spec_from_file_location('permutation_diagnostic_cli',ROOT/'scripts/run-public-support-permutation-diagnostic.py')
cli=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(cli)


def encoded(value):return (json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode()
def digest(raw):return hashlib.sha256(raw).hexdigest()
def reseal(value):return sealed({k:v for k,v in value.items() if k!='sha256'})


class Clock:
    now=0.
    def __call__(self):return self.now


def corpus():
    context=context_fixture();raw=(ROOT/diagnostic.PROFILE_PATH).read_bytes();profile=json.loads(raw)
    packages=dict(line.split('==') for line in (ROOT/'decision_runtime/requirements-mlx.txt').read_text().splitlines() if line and not line.startswith('#'))
    context.update(profile=profile,profileSha256=fingerprint(profile),profileFileSha256=digest(raw),
                   model={k:profile['model'][k] for k in ('repository','revision','artifactSha256')},
                   tokenizerEnvironment={'python':'3.13.12','machine':'arm64','packages':packages})
    context=reseal(context);cases={case['id']:case for case in context['inputs']}
    rows=[{'id':row['id'],'inputSha256':row['inputSha256'],'partTokens':cases[row['sourceCaseId']]['partTokens'],
           'inputTokens':cases[row['sourceCaseId']]['inputTokens'],'labelContinuationsVerified':True} for row in variants(context)]
    return context,receipt(context,rows)


def measurements(inputs,profile,clock,*,position_bias=False,repeat_changes=False):
    cases={row['id']:row for row in inputs}
    stable={row['sourceCaseId']:row['optionOrder'][0] for row in inputs if row['orderId']=='rotation_0'}
    class SyntheticBackend:
        identity=profile['model']
        def score(self,request):
            case=cases[request.id];selected=request.options[0].id if position_bias else stable[case['sourceCaseId']]
            if repeat_changes and case['orderId']=='original_repeat':selected=request.options[1].id
            return Scores([8. if option.id==selected else 0. for option in request.options],case['inputTokens'])
    engine=DecisionEngine(SyntheticBackend(),Policy.from_dict({k:v for k,v in profile['policy'].items() if k!='sha256'}))
    def measure(_client,request,_profile,_timeout):
        case=cases[request.id]
        body=engine.decide(case['request']) if case['contextEligible'] else engine.error('context_too_long',request)
        body['durationMs']=5.;caller=30. if case['contextEligible'] else 5.;clock.now+=(caller+1)/1000
        return {'status':body['status'],'reason':body['reason'],'callerMs':caller,'wallMs':caller+1,
            'observation':{'result':body,'callerTiming':{'schemaVersion':'agat.decision.caller-timing.v1','clock':'monotonic','boundary':'local_http_call','durationMs':caller}}}
    return measure


def source_pins(root,commit,paths):
    raw=subprocess.check_output(['git','archive',commit,'--',*paths],cwd=root)
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:return {row.name:digest(archive.extractfile(row).read()) for row in archive if row.isfile()}


def repository(root):
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    names=subprocess.check_output(['git','ls-tree','-r','--name-only',commit,'--',*diagnostic.PATHS],cwd=ROOT,text=True).splitlines()
    raw=subprocess.check_output(['git','archive',commit,'--',*names],cwd=ROOT)
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:archive.extractall(root,filter='data')
    # Include these new contributors even before the parent branch commits them.
    for name in ('scripts/run-public-support-permutation-diagnostic.py','scripts/lib/decision_public_permutation_diagnostic.py',
                 'scripts/test/test_decision_public_permutation_diagnostic.py'):
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes((ROOT/name).read_bytes())
    for command in (['git','init','--quiet'],['git','add','.'],['git','-c','user.name=Synthetic Fixture','-c','user.email=fixture@example.invalid','commit','--quiet','-m','Synthetic offline verification source fixture']):
        subprocess.run(command,cwd=root,check=True,capture_output=True)
    return subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()


def write_fixture(root,commit,*,mutation=None,journal_mutation=None):
    directory=root/'docs/private/evidence';directory.mkdir(parents=True,exist_ok=True)
    context,permutation=corpus();context.update(sourceCommit=commit,sourceFiles=source_pins(root,commit,diagnostic.CONTEXT_PATHS));context=reseal(context)
    permutation.update(sourceCommit=commit,sourceFiles=source_pins(root,commit,SOURCE_PATHS),originalContextFileSha256=digest(encoded(context)),originalContextSealSha256=context['sha256']);permutation=reseal(permutation)
    clock=Clock();measure=measurements(permutation['inputs'],context['profile'],clock)
    first=next(row for row in permutation['inputs'] if row['contextEligible'])
    warmup=[{'variantId':first['id'],'inputSha256':first['inputSha256'],'inputTokens':first['inputTokens'],'iteration':n,
             **measure(None,Request.from_dict(first['request']),context['profile'],10000)} for n in range(2)]
    phase=diagnostic.run_phase(None,permutation['inputs'],context['profile'],clock=clock,measure=measure)
    plan={'schemaVersion':diagnostic.PLAN_SCHEMA,'createdAt':'2026-10-08T03:00:00.000Z','sourceCommit':commit,'sourceFiles':source_pins(root,commit,diagnostic.PATHS),
        'contextProfileFileSha256':digest(encoded(context)),'contextProfileSealSha256':context['sha256'],
        'permutationContextFileSha256':digest(encoded(permutation)),'permutationContextSealSha256':permutation['sha256'],
        **{k:context[k] for k in ('profile','profileFileSha256','profileSha256','manifestFileSha256','model')},'runtime':context['tokenizerEnvironment'],
        'inputs':permutation['inputs'],'budget':dict(diagnostic.BUDGET),'scope':'whole_unlabelled_public_development_option_order_diagnostic','backgroundWorkloadControlled':False,**diagnostic.AUTHORITY}
    metrics=DecisionMetrics();samples=[]
    health={'status':'ready','mode':'shadow','profileJson':json.dumps(context['profile'],sort_keys=True,separators=(',',':')),'profileSha256':context['profileSha256']}
    for label,elapsed,batch in (('ready_before_scoring',5,[]),('after_warmup',80,warmup),('after_inventory',phase['elapsedMs']+80,phase['rows'])):
        for row in batch:metrics.begin();metrics.finish(outcome(row['observation']['result']),.005)
        samples.append({'label':label,'elapsedMs':elapsed,'health':copy.deepcopy(health),'metricsRaw':metrics.render(ready=True).decode(),
                        'ownedPids':[42],'processRaw':'Synthetic fixture, never a live cleanup claim.'})
    result={'schemaVersion':diagnostic.RESULT_SCHEMA,'status':'observed','planSha256':'pending','warmup':warmup,'phase':phase,'samples':samples,
        'failure':None,'cancelled':False,'ownedPids':[42],'remainingOwnedPids':[],'cleanupErrors':[],'runtimeExitCode':0,
        'elapsedMs':phase['elapsedMs']+100,'logSha256':{},**diagnostic.AUTHORITY}
    if mutation:mutation(plan,result)
    plan=reseal(plan);result['planSha256']=plan['sha256'];records=copy.deepcopy(result['phase']['rows'])
    if journal_mutation:journal_mutation(records)
    artifacts={'runtime.log':b'Synthetic model-free test fixture.\n','requests.jsonl':b''.join(json.dumps(row,ensure_ascii=False).encode()+b'\n' for row in records),
               'phase.json':encoded(sealed(result['phase']))}
    result['logSha256']={name:digest(raw) for name,raw in artifacts.items()};result=reseal(result)
    for name,raw in {**artifacts,'context.json':encoded(context),'permutation.json':encoded(permutation),'plan.json':encoded(plan),'result.json':encoded(result)}.items():(directory/name).write_bytes(raw)
    return directory,{'context_sha':digest(encoded(context)),'permutation_sha':digest(encoded(permutation)),'plan_sha':digest(encoded(plan)),'result_sha':digest(encoded(result))}


class PublicPermutationDiagnosticTest(unittest.TestCase):
    def test_whole_serial_inventory_remaps_semantics_and_retains_overlong_cases(self):
        context,permutation=corpus();clock=Clock();saved=[]
        phase=diagnostic.run_phase(None,permutation['inputs'],context['profile'],clock=clock,measure=measurements(permutation['inputs'],context['profile'],clock),journal=saved.append)
        summary,known=diagnostic.verify_phase(phase,permutation['inputs'],context['profile'])
        self.assertEqual(saved,phase['rows']);self.assertEqual(summary['scheduledVariants'],len(permutation['inputs']))
        self.assertEqual(summary['sourceCases'],context['developmentCases']);self.assertEqual(summary['sourceGroups'],context['developmentGroups'])
        self.assertEqual(summary['fullyComputedSourceCases'],context['contextEligibleCases']);self.assertEqual(summary['fullyContextRejectedSourceCases'],context['contextTooLongCases'])
        self.assertEqual(summary['changedSemanticOutcomeSourceCases'],0);self.assertEqual(summary['originalRepeatChangedSemanticOutcomeSourceCases'],0)
        self.assertEqual(summary['maxProbabilityDeltaFromOriginal'],0);self.assertEqual(sum(known.values()),len(permutation['inputs']))

    def test_position_bias_is_counted_by_source_case_and_repeat_can_stay_equal(self):
        context,permutation=corpus();clock=Clock()
        phase=diagnostic.run_phase(None,permutation['inputs'],context['profile'],clock=clock,measure=measurements(permutation['inputs'],context['profile'],clock,position_bias=True))
        summary,_=diagnostic.verify_phase(phase,permutation['inputs'],context['profile'])
        self.assertEqual(summary['changedSemanticOutcomeSourceCases'],context['contextEligibleCases'])
        self.assertEqual(summary['originalRepeatChangedSemanticOutcomeSourceCases'],0);self.assertGreater(summary['maxProbabilityDeltaFromOriginal'],.9)
        self.assertEqual(summary['cyclicArgmaxByPosition'],{'0':sum(row['contextEligible'] and row['orderId'].startswith('rotation_') for row in permutation['inputs'])})

    def test_repeat_control_changes_are_reported_without_assigning_them_to_permutation(self):
        context,permutation=corpus();clock=Clock()
        phase=diagnostic.run_phase(None,permutation['inputs'],context['profile'],clock=clock,measure=measurements(permutation['inputs'],context['profile'],clock,repeat_changes=True))
        summary,_=diagnostic.verify_phase(phase,permutation['inputs'],context['profile'])
        self.assertEqual(summary['originalRepeatChangedSemanticOutcomeSourceCases'],context['contextEligibleCases'])
        self.assertGreater(summary['originalRepeatMaxProbabilityDelta'],.9)

    def test_cancellation_budget_and_first_bad_response_keep_unattempted_denominator_without_retry(self):
        context,permutation=corpus()
        for mode in ('cancelled','budget','bad_response'):
            clock=Clock();measure=measurements(permutation['inputs'],context['profile'],clock);calls=[]
            def counted(*args):
                calls.append(args[1].id);value=measure(*args)
                if mode=='budget':clock.now=600.
                if mode=='bad_response':value['observation']['result']['inputTokens']+=1
                return value
            phase=diagnostic.run_phase(None,permutation['inputs'],context['profile'],clock=clock,measure=counted,cancelled=lambda:mode=='cancelled')
            self.assertFalse(phase['complete']);self.assertEqual(len(phase['rows']),len(permutation['inputs']))
            self.assertEqual(len(calls),0 if mode=='cancelled' else 1)
            with self.assertRaises(ValueError):diagnostic.verify_phase(phase,permutation['inputs'],context['profile'])

    def test_rehashed_order_binding_clock_budget_and_probability_mutations_fail(self):
        context,permutation=corpus();clock=Clock()
        original=diagnostic.run_phase(None,permutation['inputs'],context['profile'],clock=clock,measure=measurements(permutation['inputs'],context['profile'],clock))
        for change in (lambda p:p['rows'].pop(),lambda p:p['rows'].reverse(),lambda p:p['rows'][0].update(inputTokens=True),
                       lambda p:p['rows'][1].update(startedMs=0),lambda p:p['budget'].update(retries=1),
                       lambda p:p['summary'].update(sourceCases=1),lambda p:p['rows'][0]['observation']['result']['distribution'][0].update(probability=.5)):
            value=copy.deepcopy(original);change(value)
            with self.assertRaises(ValueError):diagnostic.verify_phase(value,permutation['inputs'],context['profile'])

    def test_actual_git_offline_replay_and_resealed_counter_authority_journal_source_mutations(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);commit=repository(root)
            directory,pins=write_fixture(root,commit)
            actual_popen=subprocess.Popen
            def only_git(command,**kwargs):
                self.assertEqual(command[0],'git');self.assertIn(command[1],{'ls-tree','archive'});self.assertFalse(kwargs.get('shell',False))
                return actual_popen(command,**kwargs)
            with patch.object(http.client,'HTTPConnection') as network,patch.object(subprocess,'Popen',side_effect=only_git) as process:
                report=diagnostic.verify(root,directory,directory/'context.json',directory/'permutation.json',**pins)
                network.assert_not_called();self.assertGreater(process.call_count,0)
            self.assertEqual(report['status'],'pass');self.assertEqual(report['physicalScheduledHttpHandlers'],report['summary']['scheduledVariants'])
            self.assertTrue(report['reportedCleanupComplete']);self.assertFalse(report['liveCleanupVerified']);self.assertFalse(report['classificationAccuracyMeasured'])
            changes=(lambda p,r:r['phase']['summary'].update(changedSemanticOutcomeSourceCases=1),lambda p,r:r.update(ownersAppointed=True),
                     lambda p,r:p['budget'].update(timeBudgetSeconds=601),lambda p,r:p['sourceFiles'].pop('scripts/lib/decision_public_permutation_diagnostic.py'),
                     lambda p,r:r['samples'][0].update(ownedPids=[True]),lambda p,r:r['samples'][0].update(ownedPids=[42,42]),
                     lambda p,r:r['samples'][1].update(elapsedMs=10),lambda p,r:r['samples'][-1].update(elapsedMs=81),
                     lambda p,r:r['warmup'][0].update(wallMs=10002))
            for change in changes:
                directory,pins=write_fixture(root,commit,mutation=change)
                with self.assertRaises(ValueError):diagnostic.verify(root,directory,directory/'context.json',directory/'permutation.json',**pins)
            directory,pins=write_fixture(root,commit,journal_mutation=lambda rows:rows.pop())
            with self.assertRaises(ValueError):diagnostic.verify(root,directory,directory/'context.json',directory/'permutation.json',**pins)
            directory,pins=write_fixture(root,commit,mutation=lambda p,r:r['samples'][-1].update(metricsRaw=r['samples'][1]['metricsRaw']))
            with self.assertRaises(ValueError):diagnostic.verify(root,directory,directory/'context.json',directory/'permutation.json',**pins)
            directory,pins=write_fixture(root,commit);(directory/'requests.jsonl').write_bytes(b'changed')
            with self.assertRaises(ValueError):diagnostic.verify(root,directory,directory/'context.json',directory/'permutation.json',**pins)

    def test_mid_replay_consumed_journal_mutation_cannot_publish_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);commit=repository(root);directory,pins=write_fixture(root,commit)
            original=diagnostic.verify_phase
            def drift(*args):
                result=original(*args);(directory/'requests.jsonl').write_bytes(b'Changed after artifact bytes were consumed.');return result
            with patch.object(diagnostic,'verify_phase',side_effect=drift),self.assertRaises(ValueError):
                diagnostic.verify(root,directory,directory/'context.json',directory/'permutation.json',**pins)

    def test_cli_rejects_missing_pins_or_existing_output_before_native_process(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);private=root/'docs/private';private.mkdir(parents=True)
            args=['--context-profile','missing','--context-profile-file-sha256','0'*64,'--permutation-context','missing','--permutation-context-file-sha256','0'*64,
                  '--runtime-python','/synthetic-python','--manifest','missing','--evidence-dir',str(private/'fresh')]
            with patch.object(cli,'ROOT',root),patch.object(cli.subprocess,'Popen') as process,patch.object(cli,'verify_manifest') as model:
                self.assertEqual(cli.main(args),1);process.assert_not_called();model.assert_not_called();self.assertFalse((private/'fresh').exists())
                args[-1]=str(private);self.assertEqual(cli.main(args),1);process.assert_not_called()


if __name__=='__main__':unittest.main()
