import copy
import unittest

from decision_runtime.artifacts import sealed,verify_seal
from decision_runtime.contracts import Policy,Request
from decision_runtime.engine import Scores
from decision_runtime.mlx_backend import encode_request
from decision_runtime.tests.test_decisions import request
from scripts.lib.decision_batching import SCHEMA,compare,diagnose_batching,outcome,prepare_batch,score_batch,source_requests
from scripts.test.test_decision_resources import Memory


class Tokenizer:
    def encode(self,text,**_kwargs): return list(map(ord,text))
    def decode(self,tokens): return ''.join(map(chr,tokens))


class Backend:
    tokenizer=Tokenizer()
    max_tokens=512
    identity={'repository':'fixture','revision':'fixture','maxInputTokens':512,'tokenizerSha256':'1'*64}
    def __init__(self): self.calls=[]
    def score(self,r):
        self.calls.append(r.id)
        winner=int(r.state[-1])%3
        return Scores([3.0 if i==winner else 0.0 for i in range(3)],len(encode_request(self.tokenizer,r,self.max_tokens)[0]))


def source():
    cases=[]
    for index in range(4):
        raw=request();raw.update(id=f'batch-{index}',state=f'Исходный документ {index}')
        r=Request.from_dict(raw);tokens=len(encode_request(Tokenizer(),r,512)[0])
        cases.append({'request':raw,'inputSha256':r.input_sha256,'targetTokens':tokens})
    return sealed({'schemaVersion':'agat.decision.synthetic-robustness-plan.v1','generator':'agat.synthetic-behavior.v1',
                   'labelSource':'synthetic-authored','profile':{'model':Backend.identity},'cases':cases,'maxInputTokens':512})


class BatchDiagnosticTest(unittest.TestCase):
    def run_probe(self,backend=None,save_plan=lambda _:None,**kwargs):
        backend=backend or Backend()
        return diagnose_batching(source(),lambda:backend,save_plan,memory_factory=Memory,
                                 batch_scorer=kwargs.pop('batch_scorer',lambda b,rs:[b.score(r) for r in rs]),**kwargs)

    def test_invalid_source_or_limits_never_load_a_model(self):
        calls=[]
        for mutate in [lambda p:p.update(labelSource='human-holdout'),lambda p:p['cases'].pop(),
                       lambda p:p['cases'][0].update(inputSha256='0'*64)]:
            raw=copy.deepcopy(source());raw.pop('sha256');mutate(raw)
            with self.assertRaises(ValueError): diagnose_batching(sealed(raw),lambda:calls.append(True),lambda _:None)
        for config in [{'tolerance':True},{'tolerance':float('nan')},{'tolerance':0.1},{'time_budget_s':601}]:
            with self.assertRaises(ValueError): diagnose_batching(source(),lambda:calls.append(True),lambda _:None,**config)
        self.assertEqual(calls,[])

    def test_batch_shape_is_rejected_before_accessing_mlx(self):
        requests=next(iter(source_requests(source()).values()));backend=Backend()
        tokens,_=prepare_batch(backend,requests)
        self.assertEqual(len({len(row) for row in tokens}),1)
        changed=Request.from_dict({**requests[0].to_dict(),'state':requests[0].state+'more'})
        for batch in [[],requests+[requests[0]],[requests[0],changed]]:
            with self.assertRaises(ValueError): score_batch(backend,batch)
        self.assertEqual(backend.calls,[])

    def test_full_independent_rows_and_permutations_freeze_before_inference(self):
        backend=Backend();plans=[]
        def freeze(plan): self.assertEqual(backend.calls,[]);plans.append(plan)
        result=self.run_probe(backend,freeze)
        verify_seal(result,SCHEMA)
        self.assertEqual(result['planSha256'],plans[0]['sha256'])
        self.assertEqual(result['status'],'diagnostic_only');self.assertTrue(result['equivalentUnderCriterion'])
        self.assertEqual(result['summary']['measuredForwardCalls'],10)
        self.assertEqual(result['summary']['measuredRows'],20)
        self.assertEqual(len(result['comparisons']),24)
        self.assertEqual(len(result['warmup']),3)
        self.assertFalse(result['batchingEnabled']);self.assertFalse(result['qualifiedForRouting'])
        self.assertNotIn('expectedOptionId',str(plans[0]['cases']))

    def test_freeze_failure_prevents_inference(self):
        backend=Backend()
        def fail(_): raise OSError('fixture output failure')
        with self.assertRaises(OSError): self.run_probe(backend,fail)
        self.assertEqual(backend.calls,[])

    def test_numeric_drift_and_wrong_row_mapping_are_reported_separately(self):
        def drift(backend,requests):
            scores=[backend.score(r) for r in requests]
            for score in scores: score.logits[0]+=0.001
            return scores
        result=self.run_probe(batch_scorer=drift)
        self.assertFalse(result['equivalentUnderCriterion'])
        self.assertGreater(result['summary']['comparisonViolations'],0)
        self.assertEqual(result['summary']['changedOutcomes'],0)
        result=self.run_probe(batch_scorer=lambda b,rs:[b.score(rs[0]) for _ in rs])
        self.assertGreater(result['summary']['changedOutcomes'],0)
        self.assertTrue(any(not row['sameOutcome'] for row in result['comparisons'] if row['kind']=='row_permutation'))

    def test_tolerance_cannot_hide_a_changed_winner(self):
        r=next(iter(source_requests(source()).values()))[0]
        left=outcome(r,Scores([0,0,-2],20),Policy())
        right=outcome(r,Scores([0,0.000001,-2],20),Policy())
        result=compare(left,right,0.0001)
        self.assertTrue(result['withinTolerance']);self.assertFalse(result['sameOutcome'])
        self.assertFalse(result['equivalentUnderCriterion'])


if __name__=='__main__': unittest.main()
