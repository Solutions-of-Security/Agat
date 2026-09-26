import copy
import json
import unittest

from decision_runtime.artifacts import sealed,verify_seal
from scripts.lib.decision_baselines import BaselineError
from scripts.lib.decision_robustness import diagnose
from scripts.lib.decision_robustness_baseline import compare_synthetic
from scripts.test.test_decision_robustness import CorrectBackend


class Generator:
    identity={'name':'unit-fixture-only'}
    def __init__(self): self.inputs=[]
    def predict(self,request):
        self.inputs.append(request.to_dict())
        expected='insufficient' if 'Запись Б:' in request.state or 'его контрольный код не указан' in request.state else 'code_42'
        return {'selectedOptionId':expected,'inputTokens':100,'outputTokens':10,'rawContentSha256':'a'*64}


class BaselineTest(unittest.TestCase):
    def setUp(self):
        plans=[]
        self.direct=diagnose(CorrectBackend,plans.append,targets=[512],max_tokens=512)
        self.plan=plans[0]

    def test_same_inputs_both_orders_and_no_gold_or_decoded_probabilities(self):
        backend=Generator(); plans=[]
        def save(p): self.assertEqual(backend.inputs,[]);plans.append(p)
        result=compare_synthetic(self.plan,self.direct,lambda:backend,save)
        verify_seal(result,result['schemaVersion'])
        self.assertEqual(result['summary']['labelMatches'],24)
        self.assertEqual(result['directSummary']['labelMatches'],24)
        self.assertEqual(len(backend.inputs),24)
        self.assertNotIn('expectedOptionId',json.dumps(backend.inputs))
        self.assertTrue(all(r['id'].startswith('probe-') and len(r['id'])==26 for r in backend.inputs))
        self.assertTrue(all(not any(s in r['id'] for s in ('single','conflict','missing','injection')) for r in backend.inputs))
        self.assertEqual(backend.inputs[0]['id'],backend.inputs[1]['id'])
        self.assertEqual(backend.inputs[0]['state'],self.plan['cases'][0]['request']['state'])
        # Runtime fingerprints intentionally exclude the transport ID.
        self.assertEqual(result['rows'][0]['sourceInputSha256'],result['rows'][0]['result']['inputSha256'])
        self.assertEqual(result['probabilities'],'unavailable_not_estimated')
        self.assertFalse(result['qualifiedForRouting'])

    def test_tampered_or_missing_evidence_rejected_before_backend_load(self):
        loads=[]
        def backend(): loads.append(True);return Generator()
        for mutate in (lambda r:r['rows'].pop(),lambda r:r['rows'][0].update(expectedOptionId='code_24'),
                       lambda r:r['rows'][0]['result'].update(inputSha256='f'*64)):
            r={k:copy.deepcopy(v) for k,v in self.direct.items() if k!='sha256'};mutate(r)
            with self.assertRaises(ValueError): compare_synthetic(self.plan,sealed(r),backend,lambda _:None)
        self.assertEqual(loads,[])

    def test_transport_failure_stops_and_sanitizes_exception_without_retry(self):
        backend=Generator();calls=[]
        def failure(_): calls.append(True);raise BaselineError('timeout')
        backend.predict=failure
        result=compare_synthetic(self.plan,self.direct,lambda:backend,lambda _:None)
        self.assertEqual(len(calls),1)
        self.assertEqual(result['status'],'incomplete')
        self.assertEqual(result['rows'][0]['result']['reason'],'timeout')


if __name__=='__main__': unittest.main()
