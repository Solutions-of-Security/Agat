import copy
import json
import unittest

from decision_runtime.annotations import group_split, prepare_review
from decision_runtime.artifacts import verify_seal
from decision_runtime.tests.test_calibration import criteria, dataset
from scripts.lib.decision_sampling import SCHEMA, minimum_zero_error_groups, sampling_plan


def blank():
    cases=[{k:c[k] for k in ('id','family','groupId','provenance','request')} for c in dataset()['cases']]
    return prepare_review({'schemaVersion':'agat.decision.pool.v1','id':'sampling-fixture','cases':cases},'fixed-seed')


class SamplingTest(unittest.TestCase):
    def test_analytical_zero_error_boundary_and_bonferroni_minimum(self):
        basic=minimum_zero_error_groups(.01,.95)
        self.assertEqual(basic['count'],299)
        adjusted=minimum_zero_error_groups(.01,.9875)
        self.assertEqual(adjusted['count'],437)
        self.assertLessEqual(adjusted['upperBound'],.01)
        self.assertGreater(adjusted['previousUpperBound'],.01)
        self.assertAlmostEqual(adjusted['upperBound'],1-(1-.9875)**(1/437),places=14)

    def test_zero_risk_and_computational_bound_do_not_claim_finite_evidence(self):
        self.assertEqual(minimum_zero_error_groups(0,.95)['status'],'no_finite_sample')
        self.assertEqual(minimum_zero_error_groups(1e-100,.95)['status'],'above_calculation_limit')
        self.assertEqual(minimum_zero_error_groups(1,.95)['count'],1)
        for risk,confidence in [(True,.95),(.1,1),(-.1,.95),(.1,float('nan'))]:
            with self.assertRaises(ValueError): minimum_zero_error_groups(risk,confidence)

    def test_report_preserves_seed_sources_and_has_no_labels_or_inference(self):
        review=blank(); original=copy.deepcopy(review)
        report=sampling_plan(review,criteria()); verify_seal(report,SCHEMA)
        self.assertEqual(review,original)
        self.assertEqual(report['splitSeed'],'fixed-seed')
        self.assertEqual(report['modelCalls'],0)
        self.assertNotIn('expectedOptionId',json.dumps(report))
        self.assertFalse(report['qualifiedForRouting'])
        self.assertEqual(report['criteriaApproval'],'not_asserted')
        self.assertEqual(sum(r['cases'] for r in report['splits'].values()),len(review['pool']['cases']))

    def test_completed_or_modified_review_is_rejected(self):
        for mutate in (lambda r:r.update(reviewerId='someone'),
                       lambda r:r['labels'][0].update(expectedOptionId='yes'),
                       lambda r:r.update(poolSha256='a'*64)):
            review=blank(); mutate(review)
            with self.assertRaises(ValueError): sampling_plan(review,criteria())

    def test_repeated_cases_do_not_increase_independent_group_capacity(self):
        review=blank(); initial=sampling_plan(review,criteria())
        pool=copy.deepcopy(review['pool'])
        for i,c in enumerate(copy.deepcopy(pool['cases'])):
            c['id']=f'repeated-{i}'; c['request']['id']=c['id']; pool['cases'].append(c)
        repeated=sampling_plan(prepare_review(pool,review['splitSeed']),criteria())
        self.assertEqual(initial['overall'],repeated['overall'])
        self.assertEqual(initial['method'],repeated['method'])
        self.assertEqual(repeated['splits']['holdout']['cases'],initial['splits']['holdout']['cases']*2)

    def test_three_families_use_four_bounds_and_do_not_sum_overlapping_groups(self):
        review=blank(); pool=review['pool']
        for index,case in enumerate(pool['cases']): case['family']=['evidence','classification','clarification'][index%3]
        rule=criteria(); rule.update(maxGroupRisk=.01,minAcceptedGroups=300,minAcceptedGroupsPerFamily=30)
        report=sampling_plan(prepare_review(pool,review['splitSeed']),rule)
        self.assertEqual(report['method']['comparisons'],4)
        self.assertEqual(report['method']['boundConfidence'],.9875)
        self.assertEqual(report['method']['globalMinimumWithCountGate'],437)
        self.assertEqual(report['method']['perFamilyMinimumWithCountGate'],437)

    def test_same_source_in_separate_splits_is_a_blocking_capacity_issue(self):
        review=blank(); pool=review['pool']; first=pool['cases'][0]
        other=next(c for c in pool['cases'] if group_split(review['splitSeed'],c['groupId']) != group_split(review['splitSeed'],first['groupId']))
        other['provenance']['sourceId']=first['provenance']['sourceId']
        report=sampling_plan(prepare_review(pool,review['splitSeed']),criteria())
        self.assertIn('source_crosses_splits',report['blockingCapacityIssues'])
        self.assertEqual(report['sourceIdsAcrossSplits'],[first['provenance']['sourceId']])


if __name__=='__main__': unittest.main()
