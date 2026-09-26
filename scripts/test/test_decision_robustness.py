import unittest

from decision_runtime.artifacts import verify_seal
from decision_runtime.contracts import Request
from decision_runtime.engine import Scores
from scripts.lib.decision_context import POSITIONS, token_count
from scripts.lib.decision_robustness import SCENARIOS, SCHEMA, diagnose, make_case
from scripts.test.test_decision_context import ContextBackend, Tokenizer


class CorrectBackend(ContextBackend):
    def score(self,request):
        self.requests.append(request)
        expected='insufficient' if '-conflict-' in request.id or '-missing-' in request.id else 'code_42'
        return Scores([4.0 if o.id==expected else -2 for o in request.options],token_count(self.tokenizer,request))


class RobustnessTest(unittest.TestCase):
    def test_generation_preserves_expectations_and_exact_both_order_lengths(self):
        for scenario,(block,expected) in SCENARIOS.items():
            for position in POSITIONS:
                case=make_case(Tokenizer(),512,position,scenario)
                self.assertEqual(case['expectedOptionId'],expected)
                self.assertEqual(case['request']['state'].count(block),1)
                request=Request.from_dict(case['request'])
                self.assertEqual(token_count(Tokenizer(),request),512)
                reverse=Request.from_dict({**case['request'],'options':list(reversed(case['request']['options']))})
                self.assertEqual(token_count(Tokenizer(),reverse),512)
                self.assertNotEqual(request.input_sha256,reverse.input_sha256)

    def test_plan_written_before_inference_and_success_is_still_diagnostic(self):
        backend=CorrectBackend(); plans=[]
        def save(plan): self.assertEqual(backend.requests,[]); plans.append(plan)
        result=diagnose(lambda:backend,save,targets=[512],max_tokens=512)
        verify_seal(result,SCHEMA)
        self.assertEqual(result['planSha256'],plans[0]['sha256'])
        self.assertEqual(result['status'],'diagnostic_only')
        self.assertEqual(result['summary']['labelMatches'],24)
        self.assertEqual(result['summary']['correctInsufficient'],12)
        self.assertEqual(result['summary']['acceptedWrong'],0)
        self.assertFalse(result['qualifiedForRouting'])
        self.assertTrue(all(r['labelAgreement'] for r in result['orderAgreement']))

    def test_wrong_accepted_labels_and_order_disagreement_are_not_hidden(self):
        result=diagnose(ContextBackend,lambda _:None,targets=[512],max_tokens=512)
        self.assertEqual(result['summary']['acceptedWrong'],6)
        self.assertEqual(result['summary']['labelMatches'],12)
        self.assertTrue(any(not r['labelAgreement'] for r in result['orderAgreement']))
        self.assertEqual(result['status'],'diagnostic_only')

    def test_invalid_plan_or_failed_freeze_never_scores(self):
        loads=[]
        def factory(): loads.append(True); return CorrectBackend()
        for config in ({'targets':[512,512]},{'targets':[2048,512]},{'max_tokens':True},{'time_budget_s':301}):
            with self.assertRaises(ValueError): diagnose(factory,lambda _:None,**config)
        self.assertEqual(loads,[])
        backend=CorrectBackend()
        def failed(_): raise OSError('fixture write failure')
        with self.assertRaises(OSError): diagnose(lambda:backend,failed,targets=[512],max_tokens=512)
        self.assertEqual(backend.requests,[])


if __name__=='__main__': unittest.main()
