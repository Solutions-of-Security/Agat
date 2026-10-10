"""HTTP/source-contract accounting and unsupported implementation regressions."""
import copy
from datetime import datetime,timedelta,timezone
import hashlib,json
from pathlib import Path
import tempfile,unittest
from unittest.mock import patch
from decision_runtime.artifacts import sealed
from scripts.lib import decision_public_native_execution_accounting as diagnostic

ROOT=Path(__file__).resolve().parents[2]
encoded=lambda v:(json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n').encode()
sha=lambda raw:hashlib.sha256(raw).hexdigest()
moment=lambda seconds:(datetime(2026,10,10,tzinfo=timezone.utc)+timedelta(seconds=seconds)).isoformat(timespec='milliseconds').replace('+00:00','Z')


def fixture():
    profile=json.loads((ROOT/'docs/qualification/local-decisions/performance/profiles/runtime-0.12.3-wired-4096.json').read_bytes())
    model=profile['model'];sources={name:(ROOT/name).read_bytes() for name in diagnostic.CONTRACT_FILES}
    def body(status):
        value={'runtimeVersion':'0.12.3','model':model,'id':'stage','inputSha256':'a'*64}
        if status in ('ok','abstain'):value.update(status=status,reason='accepted' if status=='ok' else 'below_threshold',generatedTokens=0,inputTokens=480)
        else:value.update(status='error',reason='busy' if status=='busy' else 'context_too_long')
        if status=='busy':value.update(id=None,inputSha256=None)
        return value
    rows=[];statuses=('ok','busy','abstain','context_rejected');outcomes={s:1 for s in statuses}
    for ordinal,status in enumerate(statuses):
        raw=encoded(body(status)).decode();rows.append({'index':ordinal//2,'replica':ordinal%2,'httpStatus':{'busy':503,'context_rejected':422}.get(status,200),
            'responseBody':raw,'responseBodySha256':sha(raw.encode())})
    warmups=[{'status':'abstain','observation':{'result':body('abstain')}} for _ in range(2)]
    return model,sources,rows,warmups,outcomes


class AccountingTests(unittest.TestCase):
    def setUp(self):self.model,self.sources,self.rows,self.warmups,self.outcomes=fixture()
    def account(self):return diagnostic.account(self.model,self.sources,self.rows,self.warmups,self.outcomes,2)

    def test_observed_http_partition_and_source_inferred_forward_counts_are_distinct(self):
        v=self.account();self.assertEqual(v['counts']['case'],{'httpAttemptsObserved':4,'successfulScoreRepliesObserved':2,
            'busyRepliesObserved':1,'contextRejectionsObserved':1,'backendScoreEntriesSourceInferred':3,'logicalForwardCompletionsSourceInferred':2})
        self.assertEqual(v['counts']['all']['httpAttemptsObserved'],6);self.assertEqual(v['counts']['all']['logicalForwardCompletionsSourceInferred'],4)

    def test_two_identical_warmup_scores_are_two_logical_forward_completions(self):
        v=self.account();self.assertEqual(v['counts']['warmup']['logicalForwardCompletionsSourceInferred'],2)
        self.assertEqual(len([r for r in v['requests'] if r['scope']=='warmup']),2)

    def test_allocator_buffer_cache_does_not_supply_request_result_memoization(self):
        v=self.account()['sourceContract'];self.assertEqual(v['allocatorCacheLimitBytes'],128*1024**2)
        self.assertEqual(v['allocatorCacheSemantics'],'reusable_free_buffers');self.assertIs(v['requestResultMemoizationImplemented'],False)

    def test_successful_abstention_still_completes_a_fresh_forward(self):
        reply=next(r for r in self.account()['requests'] if r['scope']=='case' and r['outcome']=='abstain')
        self.assertEqual(reply['logicalForwardCompletionsSourceInferred'],1);self.assertEqual(reply['generatedDecisionTokensObserved'],0)

    def test_busy_bypasses_backend_and_context_rejection_precedes_forward(self):
        rows=self.account()['requests'];busy=next(r for r in rows if r['outcome']=='busy');context=next(r for r in rows if r['outcome']=='context_rejected')
        self.assertEqual(busy['backendScoreEntriesSourceInferred'],0);self.assertEqual(context['backendScoreEntriesSourceInferred'],1)
        self.assertEqual(context['logicalForwardCompletionsSourceInferred'],0);self.assertIsNone(context['inputTokensObserved'])

    def test_no_hardware_or_gpu_time_measurement_is_inferred(self):
        v=self.account();self.assertIs(v['modelForwardCompletionsAreSourceInferred'],True)
        for key in ('gpuKernelInvocationsMeasured','gpuInferenceDurationMeasured','hardwareConcurrencyEstablished'):self.assertIs(v[key],False)

    def test_any_execution_path_source_change_fails_closed(self):
        for name in diagnostic.CONTRACT_FILES:
            with self.subTest(name=name):
                sources=dict(self.sources);sources[name]+=b'\n'
                with self.assertRaises(ValueError):diagnostic.account(self.model,sources,self.rows,self.warmups,self.outcomes,2)

    def test_unsupported_implementation_and_execution_mode_are_rejected(self):
        for key,value in (('implementationSha256','0'*64),('inferenceExecution',{'kind':'thread'}),('maxInputTokens',4096)):
            with self.subTest(key=key):
                model=copy.deepcopy(self.model);model[key]=value
                with self.assertRaises(ValueError):diagnostic.account(model,self.sources,self.rows,self.warmups,self.outcomes,2)

    def test_duplicate_missing_and_boolean_replica_identities_are_rejected(self):
        for mode in ('duplicate','missing','boolean'):
            with self.subTest(mode=mode):
                rows=copy.deepcopy(self.rows)
                if mode=='duplicate':rows[1]['replica']=0
                elif mode=='missing':rows.pop()
                else:rows[1]['replica']=True
                with self.assertRaises(ValueError):diagnostic.account(self.model,self.sources,rows,self.warmups,self.outcomes,2)

    def test_raw_body_hash_and_declared_outcome_counts_cannot_be_substituted(self):
        self.rows[0]['responseBody']+=' '
        with self.assertRaises(ValueError):self.account()
        self.setUp();self.outcomes['busy']=2
        with self.assertRaises(ValueError):self.account()

    def test_unhandled_error_response_cannot_be_omitted(self):
        self.rows[0]['httpStatus']=500
        with self.assertRaises(ValueError):self.account()

    def test_missing_or_failed_warmup_is_not_a_completed_forward(self):
        self.warmups.pop()
        with self.assertRaises(ValueError):self.account()
        self.setUp();self.warmups[0]['status']='error'
        with self.assertRaises(ValueError):self.account()


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup);self.root=Path(self.temporary.name).resolve()
        self.evidence={'counts':{'all':{'httpAttemptsObserved':100}},'modelForwardCompletionsAreSourceInferred':True}
        self.receipt=sealed({'schemaVersion':diagnostic.native.VERIFICATION_SCHEMA,'status':'pass','verifiedAt':moment(0),
            'modelCallsDuringVerification':0,'nativeCalls':100})
        self.inputs={'context':{'path':'docs/private/context.json','sha256':'a'*64},'replicated':{'evidenceDir':'docs/private/raw','planFileSha256':'b'*64,'resultFileSha256':'c'*64}}
        self.source=patch.object(diagnostic,'sources_at',return_value={'source.py':b'pinned'});self.source.start();self.addCleanup(self.source.stop)
        self.replay=patch.object(diagnostic,'analyze_receipts',return_value=(self.evidence,self.receipt));self.mock_replay=self.replay.start();self.addCleanup(self.replay.stop)
        self.clock=patch.object(diagnostic.paired,'now',return_value=moment(1));self.clock.start();self.addCleanup(self.clock.stop)
    def analysis(self):return diagnostic.create_analysis(self.root,self.inputs,'d'*40,{'source.py':'e'*64})
    def verify(self,value):
        path=self.root/'docs/private/analysis.json';path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(encoded(sealed({k:v for k,v in value.items() if k!='sha256'})))
        return diagnostic.verify_analysis(self.root,path,sha(path.read_bytes()))

    def test_sealed_model_free_replay_preserves_inference_scope(self):
        r=self.verify(self.analysis());self.assertEqual(r['status'],'pass');self.assertEqual(r['modelCallsDuringVerification'],0)
        self.assertIs(r['evidence']['modelForwardCompletionsAreSourceInferred'],True)

    def test_resealed_hardware_claim_is_rejected_by_original_recomputation(self):
        value=copy.deepcopy(self.analysis());value['evidence']['gpuKernelInvocationsMeasured']=True
        with self.assertRaises(ValueError):self.verify(value)

    def test_unsupported_qualification_and_contract_edits_are_rejected(self):
        for key,value in (('routingEnabled',True),('referenceLabels',1),('newModelCalls',False)):
            with self.subTest(key=key):
                result=copy.deepcopy(self.analysis());result[key]=value
                with self.assertRaises(ValueError):self.verify(result)
        result=copy.deepcopy(self.analysis());result['protocol']['allocatorCacheMeaning']='response_cache'
        with self.assertRaises(ValueError):self.verify(result)

    def test_source_replay_failure_and_future_embedded_timestamp_fail_closed(self):
        value=copy.deepcopy(self.analysis());value['datasetReplay']['verifiedAt']=moment(10)
        with self.assertRaises(ValueError):self.verify(value)
        self.mock_replay.side_effect=ValueError('Native source mismatch')
        with self.assertRaises(ValueError):self.analysis()


if __name__=='__main__':unittest.main()
