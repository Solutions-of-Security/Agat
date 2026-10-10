"""Full-denominator weighting, descriptive strata and sealed replay regressions."""
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from decision_runtime.artifacts import sealed
from scripts.lib import decision_public_replication_sensitivity as diagnostic

ROOT=Path(__file__).resolve().parents[2]
WHEN=datetime(2026,10,10,tzinfo=timezone.utc)
encoded=lambda value:(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n').encode()
sha=lambda raw:hashlib.sha256(raw).hexdigest()
moment=lambda seconds:(WHEN+timedelta(seconds=seconds)).isoformat(timespec='milliseconds').replace('+00:00','Z')


def fixture():
    context={'inputs':[]};cases=[];outcomes={};group_names=['A','A','B','C','C']
    for i,delta in enumerate((2,10,40,-6,14)):
        context['inputs'].append({'id':f'whole-{i}','groupId':group_names[i],'contextEligible':i != 2})
        orientation='ABBA' if i//2 != 1 else 'BAAB';replicas=[]
        periods={'control':[0,3],'shadow':[1,2]} if orientation == 'ABBA' else {'control':[1,2],'shadow':[0,3]}
        equal=i % 2 == 0
        for r in (0,1):
            phases={}
            for condition in ('control','shadow'):
                is_shadow=condition == 'shadow';primary=60+4*r+delta/2*is_shadow;post=30+delta/2*is_shadow
                phase={'workflowObservedMs':2+primary+post+.125,'prePrimaryWallMs':2,
                    'primaryProxyMonotonicMs':primary,'postPrimaryWallMs':post,'clockClosureResidualMs':.125,
                    'primaryReportedTimingMs':{'total_duration':primary-1,'load_duration':0,'prompt_eval_duration':2,'eval_duration':primary-3},
                    'promptTokens':100+i,'decodeTokens':128,'doneReason':'length','period':periods[condition][r],
                    'outputSha256':sha((str(i) if equal else f'{i}-{condition}-{r}').encode())}
                if is_shadow:
                    outcome='busy' if r else ('ok','abstain','context_rejected','ok','abstain')[i]
                    outcomes[outcome]=outcomes.get(outcome,0)+1
                    phase['shadow']={'outcome':outcome,'httpStatus':diagnostic.OUTCOMES[outcome],
                        'preDecisionWallMs':1,'postDecisionWallMs':1,'decisionRelayWallMs':post-2,
                        'decisionRelayMonotonicMs':post-2,'callerMonotonicMs':post-2,'callerMinusRelayMs':0}
                phases[condition]=phase
            contrast={k:round(phases['shadow'][k]-phases['control'][k],3) for k in diagnostic.COMPONENTS}
            replicas.append({'replica':r,**phases,'shadowMinusControlMs':contrast})
        cases.append({'index':i,'caseId':f'whole-{i}','groupId':group_names[i],'orientation':orientation,
            'allFourPrimaryRequestsEqual':True,'allFourPrimaryOutputsEqual':equal,'replicas':replicas,
            'meanShadowMinusControlMs':{k:round(sum(r['shadowMinusControlMs'][k] for r in replicas)/2,6) for k in diagnostic.COMPONENTS}})
    inventory={'originalCases':5,'originalGroups':3,'actualWorkflows':20,'observationsPerCondition':2,
        'allCasesFourPeriods':True,'differentOutputsRetained':True,'cases':cases,'shadowOutcomes':outcomes}
    return context,inventory


class SensitivityTests(unittest.TestCase):
    def setUp(self):self.context,self.inventory=fixture()
    def analyze(self):return diagnostic.aggregate(self.context,self.inventory)

    def test_case_group_and_block_weights_have_distinct_hand_calculated_means(self):
        v=self.analyze();k='workflowObservedMs'
        self.assertEqual(v['allCaseMeanMs'][k],12)
        self.assertEqual(v['equalGroupMeanMs'][k],16.666667)
        self.assertEqual(v['equalOriginalBlockMeanMs'][k],12.333333)
        self.assertEqual([g['caseCount'] for g in v['groups']],[2,1,2])

    def test_every_group_is_omitted_once_in_explicit_sensitivity_only(self):
        v=self.analyze();rows=v['leaveOneGroupOut'];k='workflowObservedMs'
        self.assertEqual([r['omittedGroupId'] for r in rows],['A','B','C'])
        self.assertEqual([r['remainingCaseMeanMs'][k] for r in rows],[16,5,17.333333])
        self.assertEqual(v['originalCases'],5);self.assertTrue(v['allCasesRetained'])

    def test_singleton_block_and_groups_crossing_blocks_remain_visible(self):
        v=self.analyze();self.assertEqual([b['caseCount'] for b in v['originalBlocks']],[2,2,1])
        self.assertEqual(v['groups'][2]['blockIds'],[1,2]);self.assertEqual(v['originalBlocks'][1]['groupIds'],['B','C'])

    def test_period_cells_retain_every_observation_without_condition_averaging(self):
        cells=self.analyze()['periodConditionCells'];self.assertEqual(len(cells),8)
        self.assertEqual(sum(c['observations'] for c in cells),20)
        self.assertEqual([c['observations'] for c in cells],[3,2,2,3,2,3,3,2])
        self.assertEqual(sum(c['doneReasons'].get('length',0) for c in cells),20)

    def test_outcome_partition_reconstructs_original_mean_with_observation_weights(self):
        cells=self.analyze()['outcomeCells'];k='workflowObservedMs'
        self.assertEqual(sum(c['observations'] for c in cells),10)
        self.assertEqual(sum(c['observations']*c['matchedReplicaDeltaMs'][k]['mean'] for c in cells)/10,12)
        self.assertEqual(next(c for c in cells if c['outcome']=='busy')['distinctCases'],5)

    def test_negative_contrasts_and_different_outputs_are_preserved(self):
        v=self.analyze();self.assertEqual(v['allCaseDistributionMs']['workflowObservedMs']['min'],-6)
        cells=[r for r in v['casePartitions'] if r['axis']=='allFourPrimaryOutputsEqual']
        self.assertEqual([r['caseCount'] for r in cells],[3,2]);self.assertTrue(v['differentOutputsRetained'])

    def test_weighted_component_means_close_for_all_three_units(self):
        v=self.analyze()
        for name in ('allCaseMeanMs','equalGroupMeanMs','equalOriginalBlockMeanMs'):
            row=v[name];self.assertAlmostEqual(row['workflowObservedMs'],sum(row[k] for k in diagnostic.COMPONENTS[1:]),places=5)

    def test_empty_strata_have_explicit_zero_denominator_and_null_statistics(self):
        self.inventory['shadowOutcomes']={'ok':10}
        for c in self.inventory['cases']:
            for r in c['replicas']:r['shadow']['shadow'].update(outcome='ok',httpStatus=200)
        cells=self.analyze()['outcomeCells'];empty=next(c for c in cells if c['outcome']=='busy')
        self.assertEqual(empty['observations'],0);self.assertIsNone(empty['callerMs']['mean']);self.assertEqual(empty['callerMs']['count'],0)

    def test_descriptive_output_does_not_grant_independence_or_cache_or_causal_claims(self):
        v=self.analyze()
        for k in ('independentGroupsAssumed','independentBlocksAssumed','groupDependenceRemoved','outcomeStrataAreCausal',
            'cacheHitsInferredFromTimings','causalOverheadEstablished','populationEstimateEstablished','uncertaintyIntervalEstablished'):
            self.assertIs(v[k],False)

    def test_incomplete_or_mutated_identity_timing_and_outcome_contracts_fail_closed(self):
        def remove_case(c,v):v['cases'].pop()
        def repeat_case(c,v):v['cases'][1]=copy.deepcopy(v['cases'][0])
        def change_group(c,v):v['cases'][0]['groupId']='other'
        def swap_period(c,v):v['cases'][0]['replicas'][0]['shadow']['period']=3
        def false_output(c,v):v['cases'][0]['allFourPrimaryOutputsEqual']=False
        def lose_outcome(c,v):v['shadowOutcomes']['busy']-=1
        def bad_closure(c,v):v['cases'][0]['replicas'][0]['shadow']['workflowObservedMs']+=1
        def bad_clock(c,v):v['cases'][0]['replicas'][0]['shadow']['clockClosureResidualMs']=21
        def non_finite(c,v):v['cases'][0]['replicas'][0]['control']['primaryProxyMonotonicMs']=float('nan')
        def malformed_context(c,v):c['inputs'][0]['contextEligible']=1
        def crossed_block(c,v):v['cases'][1]['orientation']='BAAB'
        def changed_contrast(c,v):v['cases'][0]['meanShadowMinusControlMs']['workflowObservedMs']+=1
        def missing_replica(c,v):v['cases'][0]['replicas'].pop()
        def wrong_status(c,v):v['cases'][0]['replicas'][1]['shadow']['shadow']['httpStatus']=200
        for mutation in (remove_case,repeat_case,change_group,swap_period,false_output,lose_outcome,bad_closure,bad_clock,
            non_finite,malformed_context,crossed_block,changed_contrast,missing_replica,wrong_status):
            with self.subTest(mutation=mutation.__name__):
                c,v=copy.deepcopy((self.context,self.inventory));mutation(c,v)
                with self.assertRaises(ValueError):diagnostic.aggregate(c,v)


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup);self.root=Path(self.temporary.name).resolve()
        self.context,self.inventory=fixture();directory=self.root/'docs/private/raw';directory.mkdir(parents=True)
        context_path=self.root/'docs/private/context.json';context_path.write_bytes(encoded(self.context))
        result_path=directory/'result.json';result_path.write_bytes(encoded({'evidence':self.inventory}))
        self.inputs={'context':{'path':'docs/private/context.json','sha256':sha(context_path.read_bytes())},
            'replicated':{'evidenceDir':'docs/private/raw','planFileSha256':'1'*64,'resultFileSha256':sha(result_path.read_bytes())}}
        self.receipt=sealed({'schemaVersion':diagnostic.native.VERIFICATION_SCHEMA,'verifiedAt':moment(0),
            'status':'pass','modelCallsDuringVerification':0,'evidence':self.inventory})
        self.sources=patch.object(diagnostic,'sources_at',return_value={'source.py':b'pinned'});self.sources.start();self.addCleanup(self.sources.stop)
        self.replay=patch.object(diagnostic,'verify_native',return_value=self.receipt);self.mock_replay=self.replay.start();self.addCleanup(self.replay.stop)
        self.clock=patch.object(diagnostic.paired,'now',return_value=moment(1));self.clock.start();self.addCleanup(self.clock.stop)

    def analysis(self):return diagnostic.create_analysis(self.root,self.inputs,'a'*40,{'source.py':'b'*64})
    def verify(self,v):
        p=self.root/'docs/private/analysis.json';p.write_bytes(encoded(sealed({k:x for k,x in v.items() if k!='sha256'})))
        return diagnostic.verify_analysis(self.root,p,sha(p.read_bytes()))

    def test_analysis_and_replay_are_pinned_and_model_free(self):
        v=self.analysis();r=self.verify(v);self.assertEqual(r['status'],'pass');self.assertEqual(r['modelCallsDuringVerification'],0)
        self.assertEqual(self.mock_replay.call_count,2);self.assertEqual(r['verifiedSourceFiles'],1)
        self.assertEqual(self.mock_replay.call_args.kwargs['suite'],diagnostic.native)

    def test_resealed_statistic_tampering_is_recomputed_from_original_receipts(self):
        v=self.analysis();v['evidence']['equalGroupMeanMs']['workflowObservedMs']+=1
        with self.assertRaises(ValueError):self.verify(v)

    def test_resealed_routing_authority_and_posthoc_protocol_changes_are_rejected(self):
        for key,value in (('routingEnabled',True),('newModelCalls',False),('referenceLabels',1)):
            with self.subTest(key=key):
                v=self.analysis();v[key]=value
                with self.assertRaises(ValueError):self.verify(v)
        v=copy.deepcopy(self.analysis());v['protocol']['analysisTiming']='prospective'
        with self.assertRaises(ValueError):self.verify(v)

    def test_input_raw_hash_and_private_path_boundaries_are_enforced(self):
        v=self.analysis();(self.root/'docs/private/raw/result.json').write_bytes(b'changed')
        with self.assertRaises(ValueError):self.verify(v)
        inputs=copy.deepcopy(self.inputs);inputs['replicated']['evidenceDir']='docs/../private/raw'
        with self.assertRaises(ValueError):diagnostic.analyze_receipts(self.root,inputs)

    def test_native_replay_failures_propagate_without_aggregation(self):
        self.mock_replay.side_effect=ValueError('Source mismatch')
        with self.assertRaises(ValueError):self.analysis()

    def test_replayed_inventory_and_replay_timestamp_cannot_drift(self):
        v=self.analysis();v['datasetReplay']['verifiedAt']=moment(10)
        with self.assertRaises(ValueError):self.verify(v)
        self.receipt['evidence']=copy.deepcopy(self.inventory);self.receipt['evidence']['shadowOutcomes']['busy']-=1
        with self.assertRaises(ValueError):self.analysis()


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup);self.root=Path(self.temporary.name).resolve()
        path=ROOT/'scripts/analyze-public-support-replication-sensitivity.py'
        spec=importlib.util.spec_from_file_location('replication_sensitivity_cli_test',path)
        self.module=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.module)
        self.args=['--context-profile',str(self.root/'docs/private/context.json'),'--evidence-dir',str(self.root/'docs/private/raw'),
            '--output-dir',str(self.root/'docs/private/out'),'--context-profile-file-sha256','1'*64,
            '--plan-file-sha256','2'*64,'--result-file-sha256','3'*64]

    def test_cli_pins_relative_original_paths_and_writes_private_output(self):
        frozen=('a'*40,{'source.py':'b'*64})
        with patch.object(self.module,'ROOT',self.root),patch.object(self.module.launcher,'frozen_sources',return_value=frozen),\
            patch.object(diagnostic,'create_analysis',return_value={'private':'receipt'}) as create:
            self.assertEqual(self.module.main(self.args),0)
        self.assertEqual(create.call_args.args[1]['replicated']['planFileSha256'],'2'*64)
        self.assertEqual(create.call_args.args[1]['context']['path'],'docs/private/context.json')
        self.assertEqual(json.loads((self.root/'docs/private/out/analysis.json').read_bytes()),{'private':'receipt'})

    def test_cli_source_drift_prevents_any_published_analysis(self):
        with patch.object(self.module,'ROOT',self.root),patch.object(self.module.launcher,'frozen_sources',
            side_effect=[('a'*40,{}),('c'*40,{})]),patch.object(diagnostic,'create_analysis',return_value={'private':'receipt'}):
            self.assertEqual(self.module.main(self.args),1)
        self.assertFalse((self.root/'docs/private/out').exists())


if __name__ == '__main__':unittest.main()
