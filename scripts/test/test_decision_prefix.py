import copy
import unittest

from decision_runtime.artifacts import sealed, verify_seal
from decision_runtime.contracts import DecisionError, Request, fingerprint
from decision_runtime.engine import Scores
from scripts.lib.decision_prefix import PrefixState, diagnose_prefix, prepare_prefix, selected_requests, validate_prefix_binding
from scripts.test.test_decision_batching import Backend, source
from scripts.test.test_decision_resources import Memory


class Probe:
    def __init__(self, backend): self.backend = backend
    def prefill(self, request):
        prefix, _, _ = prepare_prefix(self.backend,request)
        return PrefixState(fingerprint(self.backend.identity),prefix,[[]])
    def signature(self, state): return {'sha256':fingerprint(state.cache), 'logicalBytes':0,'layerTypes':['fixture']}
    def score(self, variant, request, state=None):
        if variant == 'shared_prefix':
            validate_prefix_binding(self.backend,prepare_prefix(self.backend,request)[0],state)
            branch = copy.deepcopy(state.cache)
            branch[0].append(request.question)
        return self.backend.score(request)


class PrefixTest(unittest.TestCase):
    def run_probe(self, backend=None, save_plan=lambda _:None, **kwargs):
        backend = backend or Backend()
        return diagnose_prefix(source(),lambda:backend,save_plan,memory_factory=Memory,
                               probe_factory=kwargs.pop('probe_factory',Probe),**kwargs)

    def test_boundary_preserves_tokens_and_guard_rejects_different_state_or_model(self):
        backend=Backend(); requests=selected_requests(source())[0][2]
        values=[prepare_prefix(backend,r) for r in requests]
        self.assertEqual(len({tuple(v[0]) for v in values}),1)
        self.assertEqual(len({tuple(v[1]) for v in values}),3)
        state=Probe(backend).prefill(requests[0])
        validate_prefix_binding(backend,values[1][0],state)
        with self.assertRaises(ValueError): validate_prefix_binding(backend,values[0][0]+[1],state)
        backend.identity={**backend.identity,'revision':'changed'}
        with self.assertRaises(ValueError): validate_prefix_binding(backend,values[0][0],state)

    def test_invalid_source_or_budget_fails_before_model_load(self):
        calls=[]; raw=source();raw.pop('sha256');raw['labelSource']='human-holdout'
        with self.assertRaises(ValueError): diagnose_prefix(sealed(raw),lambda:calls.append(True),lambda _:None)
        for limit in (0,601,True,float('nan')):
            with self.assertRaises(ValueError): diagnose_prefix(source(),lambda:calls.append(True),lambda _:None,time_budget_s=limit)
        self.assertEqual(calls,[])

    def test_rejects_oversized_questions_and_freeze_failure_before_forward(self):
        backend=Backend(); backend.max_tokens=200
        with self.assertRaises(DecisionError): self.run_probe(backend)
        self.assertEqual(backend.calls,[])
        backend=Backend()
        def fail(_): raise OSError('cannot freeze')
        with self.assertRaises(OSError): self.run_probe(backend,fail)
        self.assertEqual(backend.calls,[])

    def test_complete_ordered_comparison_and_prefix_integrity(self):
        backend=Backend();plans=[]
        def freeze(plan): self.assertEqual(backend.calls,[]);plans.append(plan)
        result=self.run_probe(backend,freeze)
        verify_seal(result,'agat.decision.prefix-diagnostic.v1')
        self.assertEqual(result['planSha256'],plans[0]['sha256'])
        self.assertEqual(result['status'],'diagnostic_only');self.assertTrue(result['equivalentUnderCriterion'])
        self.assertEqual(result['summary'],{'scoredRequests':60,'prefixPrefills':4,'comparisons':84,
                                           'criterionViolations':0,'changedOutcomes':0,
                                           'prefixIntegrityChecks':24,'prefixMutations':0})
        self.assertFalse(result['prefixCacheEnabled']);self.assertFalse(result['qualifiedForRouting'])
        self.assertEqual(len(result['warmup']),3)
        self.assertEqual(len(plans[0]['groups']),4)
        self.assertTrue(all('expectedOptionId' not in c for g in plans[0]['groups'] for c in g['cases']))

    def test_chunking_drift_does_not_masquerade_as_branch_contamination(self):
        class Drift(Probe):
            def score(self,variant,request,state=None):
                value=super().score(variant,request,state)
                return Scores([z+0.01 for z in value.logits],value.input_tokens) if variant in ('split_fresh','shared_prefix') else value
        result=self.run_probe(probe_factory=Drift)
        self.assertFalse(result['equivalentUnderCriterion'])
        self.assertEqual(result['summary']['changedOutcomes'],0)
        self.assertEqual(result['summary']['prefixMutations'],0)
        self.assertTrue(all(c['equivalentUnderCriterion'] for c in result['comparisons'] if c['reference'] in ('split_fresh','forward_order')))
        self.assertTrue(any(not c['equivalentUnderCriterion'] for c in result['comparisons'] if c['reference']=='serial'))

    def test_mutated_shared_state_stops_the_probe(self):
        class Broken(Probe):
            def score(self,variant,request,state=None):
                value=super().score(variant,request,state)
                if variant=='shared_prefix': state.cache[0].append(request.id)
                return value
        result=self.run_probe(probe_factory=Broken)
        self.assertEqual(result['status'],'incomplete')
        self.assertEqual(result['failure'],{'stage':'shared_prefix','type':'ValueError'})
        self.assertEqual(result['summary']['prefixMutations'],1)
        self.assertFalse(result['equivalentUnderCriterion'])
        self.assertEqual(len(result['prefixIntegrity']),1)


if __name__=='__main__': unittest.main()
