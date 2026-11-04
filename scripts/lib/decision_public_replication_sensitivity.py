"""Post-measurement descriptive sensitivity of a complete replicated inventory."""
from collections import Counter, defaultdict
import math
import re
import statistics

from decision_runtime.artifacts import sealed, verify_seal
from decision_runtime.contracts import number, parse_json
from scripts.lib import decision_public_counterbalanced_real_primary as native
from scripts.lib import decision_public_paired_real_primary as paired
from scripts.lib.decision_public_latency_decomposition import private_path
from scripts.lib.decision_public_load_verification import distribution, same, sources_at
from scripts.lib.decision_public_real_primary_verification import verify as verify_native
from scripts.lib.decision_public_sources import pinned_input
from scripts.lib.decision_shadow_pilot import require, timestamp

ANALYSIS_SCHEMA = 'agat.decision.public-replication-sensitivity.v1'
VERIFICATION_SCHEMA = 'agat.decision.public-replication-sensitivity-verification.v1'
SOURCE_PATHS = [*native.SOURCE_PATHS, 'scripts/analyze-public-support-replication-sensitivity.py',
    'scripts/verify-public-support-replication-sensitivity.py', 'scripts/test/test_decision_public_replication_sensitivity.py']
COMPONENTS = ('workflowObservedMs', 'prePrimaryWallMs', 'primaryProxyMonotonicMs',
    'postPrimaryWallMs', 'clockClosureResidualMs')
TIMINGS = ('total_duration', 'load_duration', 'prompt_eval_duration', 'eval_duration')
OUTCOMES = {'ok': 200, 'abstain': 200, 'busy': 503, 'context_rejected': 422}
FLAGS = {'newModelCalls': 0, 'referenceLabels': 0, 'classificationAccuracyMeasured': False,
    'ownersAppointed': False, 'sloAccepted': False, 'routingEnabled': False, 'qualification': 'not_assessed'}
SPEC = {'schemaVersion': 'agat.decision.replication-sensitivity-protocol.v1',
    'analysisTiming': 'post_measurement_descriptive', 'denominator': 'all_original_inputs_and_all_four_periods',
    'caseContrast': 'mean_two_shadow_minus_mean_two_control', 'caseWeights': 'equal_original_input_weight',
    'groupWeights': 'equal_original_group_weight_after_averaging_all_member_cases',
    'blockWeights': 'equal_original_adjacent_pair_block_weight_including_singleton',
    'leaveOneGroupOut': 'every_original_group_once_remaining_cases_equal_weight',
    'outcomeContrast': 'matched_replica_delta_observation_weighted_descriptive_partition',
    'periodCells': 'absolute_components_and_reported_primary_timings_by_ordinal_period_and_condition',
    'quantileMethod': 'nearest_rank', 'medianMethod': 'midpoint_of_middle_values',
    'meanAndContrastRoundingDecimals': 6, 'quantileRoundingDecimals': 3,
    'allCasesRetained': True, 'differentOutputsRetained': True,
    'independentCasesAssumed': False, 'independentGroupsAssumed': False, 'independentBlocksAssumed': False,
    'groupDependenceRemoved': False, 'cacheHitsInferredFromTimings': False,
    'outcomeStrataAreCausal': False, 'causalOverheadEstablished': False,
    'populationEstimateEstablished': False, 'uncertaintyIntervalEstablished': False}


def stats(values):
    values = list(values)
    require(all(type(v) in (int, float) and math.isfinite(v) for v in values), 'Non-finite or untyped statistic')
    return {**distribution(values), 'min': min(values) if values else None,
        'mean': round(math.fsum(values)/len(values), 6) if values else None,
        'median': round(statistics.median(values), 6) if values else None}


def mean_vector(rows):
    require(rows, 'Empty contrast denominator')
    vector = {k: round(math.fsum(number(r[k], -210000, 210000) for r in rows)/len(rows), 6) for k in COMPONENTS}
    require(abs(vector[COMPONENTS[0]]-math.fsum(vector[k] for k in COMPONENTS[1:])) <= .005,
        'Weighted contrast components do not close')
    return vector


def component_stats(rows):
    return {k: stats(r[k] for r in rows) for k in COMPONENTS}


def check_phase(phase, condition):
    require(type(phase['period']) is int and phase['period'] in range(4), 'Invalid ordinal period')
    for k in COMPONENTS:
        number(phase[k], -20 if k == 'clockClosureResidualMs' else 0, 20 if k == 'clockClosureResidualMs' else 210000)
    require(abs(phase[COMPONENTS[0]]-math.fsum(phase[k] for k in COMPONENTS[1:])) <= .005, 'Absolute phase components do not close')
    require(set(phase['primaryReportedTimingMs']) == set(TIMINGS), 'Incomplete reported primary timings')
    for value in phase['primaryReportedTimingMs'].values(): number(value, 0, 180000)
    for key, high in (('promptTokens', 32768), ('decodeTokens', 128)):
        require(type(phase[key]) is int and 0 < phase[key] <= high, 'Invalid reported primary token count')
    require(phase['doneReason'] in ('stop', 'length') and isinstance(phase['outputSha256'],str)
        and re.fullmatch('[0-9a-f]{64}', phase['outputSha256']), 'Invalid original primary return')
    if condition == 'control':
        require('shadow' not in phase, 'Control has a decision timing'); return
    shadow = phase['shadow'];require(shadow['outcome'] in OUTCOMES and shadow['httpStatus'] == OUTCOMES[shadow['outcome']], 'Outcome/HTTP mismatch')
    for k in ('preDecisionWallMs', 'postDecisionWallMs', 'decisionRelayWallMs', 'decisionRelayMonotonicMs', 'callerMonotonicMs'):
        number(shadow[k], 0, 210000 if k in ('preDecisionWallMs', 'postDecisionWallMs') else 10000)
    number(shadow['callerMinusRelayMs'], -250, 250)
    require(abs(shadow['decisionRelayMonotonicMs']-shadow['decisionRelayWallMs']) <= 10
        and abs(shadow['callerMinusRelayMs']) <= 250 and shadow['callerMonotonicMs'] <= phase['postPrimaryWallMs']+20,
        'Relay/caller clocks exceed original accounting tolerances')
    require(abs(shadow['callerMinusRelayMs']-shadow['callerMonotonicMs']+shadow['decisionRelayMonotonicMs']) <= .001, 'Caller/relay difference changed')
    require(abs(phase['postPrimaryWallMs']-math.fsum(shadow[k] for k in ('preDecisionWallMs', 'decisionRelayWallMs', 'postDecisionWallMs'))) <= .001,
        'Shadow post-primary partition does not close')


def aggregate(context, inventory):
    count=len(context['inputs']);cases=inventory['cases']
    require(count >= 2 and len(cases) == count and [c['index'] for c in cases] == list(range(count)), 'Missing/repeated/reordered original cases')
    require(inventory['actualWorkflows'] == 4*count and inventory['observationsPerCondition'] == 2
        and inventory['allCasesFourPeriods'] is True and inventory['differentOutputsRetained'] is True, 'Incomplete replicated denominator')
    groups=defaultdict(list);blocks=defaultdict(list);observations=[];matched=[];contrasts=[]
    for i,(case,original) in enumerate(zip(cases,context['inputs'])):
        require(type(case['index']) is int and case['caseId'] == original['id'] and case['groupId'] == original['groupId']
            and isinstance(case['groupId'],str) and case['groupId'] and type(original['contextEligible']) is bool, 'Original case/group binding changed')
        require(case['orientation'] in ('ABBA','BAAB') and case['allFourPrimaryRequestsEqual'] is True
            and type(case['allFourPrimaryOutputsEqual']) is bool, 'Invalid original replication metadata')
        replicas=case['replicas'];require(len(replicas) == 2 and [r['replica'] for r in replicas] == [0,1]
            and all(type(r['replica']) is int for r in replicas), 'Missing/repeated replicas')
        periods={'control': [0,3], 'shadow': [1,2]} if case['orientation'] == 'ABBA' else {'control': [1,2], 'shadow': [0,3]}
        deltas=[];outputs=set()
        for r,replica in enumerate(replicas):
            for condition in ('control','shadow'):
                phase=replica[condition];check_phase(phase,condition)
                require(phase['period'] == periods[condition][r], 'Replica crossed its prespecified period')
                observations.append({'index':i,'condition':condition,'replica':r,**phase});outputs.add(phase['outputSha256'])
            delta={k:round(replica['shadow'][k]-replica['control'][k],3) for k in COMPONENTS}
            same(delta,replica['shadowMinusControlMs'],'Matched replica contrast changed');mean_vector([delta]);deltas.append(delta)
            matched.append({'index':i,'replica':r,'outcome':replica['shadow']['shadow']['outcome'],'delta':delta,'shadow':replica['shadow']})
        require(case['allFourPrimaryOutputsEqual'] == (len(outputs) == 1), 'Primary output equality metadata changed')
        contrast=mean_vector(deltas);same(contrast,case['meanShadowMinusControlMs'],'Case contrast changed')
        contrasts.append(contrast);groups[case['groupId']].append(i);blocks[i//2].append(i)
    require(len(groups) >= 2 and inventory['originalCases'] == count and inventory['originalGroups'] == len(groups), 'Original group denominator changed')
    outcome_counts=Counter(r['outcome'] for r in matched);same(dict(outcome_counts),inventory['shadowOutcomes'],'Original outcomes lost or changed')
    all_mean=mean_vector(contrasts)
    group_rows=[{'groupId':g,'indices':indices,'caseCount':len(indices),'blockIds':sorted({i//2 for i in indices}),
        'meanShadowMinusControlMs':mean_vector([contrasts[i] for i in indices])} for g,indices in sorted(groups.items())]
    block_rows=[]
    for block,indices in sorted(blocks.items()):
        require(len({cases[i]['orientation'] for i in indices}) == 1, 'Original pair block orientation changed')
        block_rows.append({'block':block,'indices':indices,'caseCount':len(indices),'groupIds':sorted({cases[i]['groupId'] for i in indices}),
            'orientation':cases[indices[0]]['orientation'],'meanShadowMinusControlMs':mean_vector([contrasts[i] for i in indices])})
    leave_out=[]
    for group,indices in sorted(groups.items()):
        remaining=mean_vector([v for i,v in enumerate(contrasts) if i not in indices])
        leave_out.append({'omittedGroupId':group,'omittedIndices':indices,'remainingCases':count-len(indices),
            'remainingCaseMeanMs':remaining,'differenceFromAllCaseMeanMs':{k:round(remaining[k]-all_mean[k],6) for k in COMPONENTS}})
    cells=[]
    for period in range(4):
        for condition in ('control','shadow'):
            rows=[o for o in observations if o['period'] == period and o['condition'] == condition]
            cells.append({'period':period,'condition':condition,'observations':len(rows),
                'componentsMs':component_stats(rows),'reportedPrimaryTimingMs':{k:stats(o['primaryReportedTimingMs'][k] for o in rows) for k in TIMINGS},
                'promptTokens':stats(o['promptTokens'] for o in rows),'decodeTokens':stats(o['decodeTokens'] for o in rows),
                'doneReasons':dict(sorted(Counter(o['doneReason'] for o in rows).items()))})
    outcomes=[]
    for outcome in sorted(OUTCOMES):
        rows=[r for r in matched if r['outcome'] == outcome]
        outcomes.append({'outcome':outcome,'observations':len(rows),'distinctCases':len({r['index'] for r in rows}),
            'matchedReplicaDeltaMs':component_stats([r['delta'] for r in rows]),
            'postPrimaryMs':stats(r['shadow']['postPrimaryWallMs'] for r in rows),
            'relayMs':stats(r['shadow']['shadow']['decisionRelayMonotonicMs'] for r in rows),
            'callerMs':stats(r['shadow']['shadow']['callerMonotonicMs'] for r in rows)})
    partitions=[]
    for axis,levels in (('orientation',('ABBA','BAAB')),('allFourPrimaryOutputsEqual',(True,False)),('contextEligible',(True,False))):
        for level in levels:
            indices=[i for i,c in enumerate(cases) if (context['inputs'][i][axis] if axis == 'contextEligible' else c[axis]) == level]
            partitions.append({'axis':axis,'value':level,'caseCount':len(indices),'componentsMs':component_stats([contrasts[i] for i in indices])})
    require(sum(c['observations'] for c in cells) == 4*count and sum(c['observations'] for c in outcomes) == 2*count,
        'Strata do not preserve the full denominator')
    return {'schemaVersion':'agat.decision.replication-sensitivity-inventory.v1','originalCases':count,'originalGroups':len(groups),
        'originalPairBlocks':len(blocks),'actualWorkflows':4*count,'matchedReplicaContrasts':2*count,
        'shadowOutcomes':dict(sorted(outcome_counts.items())),'allCasesRetained':True,'differentOutputsRetained':True,
        'allCaseMeanMs':all_mean,'equalGroupMeanMs':mean_vector([r['meanShadowMinusControlMs'] for r in group_rows]),
        'equalOriginalBlockMeanMs':mean_vector([r['meanShadowMinusControlMs'] for r in block_rows]),
        'allCaseDistributionMs':component_stats(contrasts),'groups':group_rows,'originalBlocks':block_rows,
        'leaveOneGroupOut':leave_out,'leaveOneGroupOutRemainingMeanMs':component_stats([r['remainingCaseMeanMs'] for r in leave_out]),
        'periodConditionCells':cells,'outcomeCells':outcomes,'casePartitions':partitions,
        'independentGroupsAssumed':False,'independentBlocksAssumed':False,'groupDependenceRemoved':False,
        'outcomeStrataAreCausal':False,'cacheHitsInferredFromTimings':False,'causalOverheadEstablished':False,
        'populationEstimateEstablished':False,'uncertaintyIntervalEstablished':False}


def analyze_receipts(root, inputs):
    require(set(inputs) == {'context','replicated'} and set(inputs['context']) == {'path','sha256'}
        and set(inputs['replicated']) == {'evidenceDir','planFileSha256','resultFileSha256'}, 'Unpinned/extra analysis dataset')
    context_path=private_path(root,inputs['context']['path']);context_sha=inputs['context']['sha256'];dataset=inputs['replicated']
    directory=private_path(root,dataset['evidenceDir'])
    receipt=verify_native(root,directory,context_path,context_sha=context_sha,plan_sha=dataset['planFileSha256'],result_sha=dataset['resultFileSha256'],suite=native)
    require(receipt['status'] == 'pass' and receipt['modelCallsDuringVerification'] == 0, 'Original native replay failed')
    context=parse_json(pinned_input(context_path,context_sha,32*1024*1024))
    result=parse_json(pinned_input(directory/'result.json',dataset['resultFileSha256'],64*1024*1024))
    same(receipt['evidence'],result['evidence'],'Native replay inventory differs')
    return aggregate(context,receipt['evidence']),receipt


def create_analysis(root, inputs, commit, source_files):
    sources_at(root,commit,source_files,SOURCE_PATHS);evidence,receipt=analyze_receipts(root,inputs)
    return sealed({'schemaVersion':ANALYSIS_SCHEMA,'createdAt':paired.now(),'sourceCommit':commit,'sourceFiles':source_files,
        'protocol':SPEC,'inputs':inputs,'datasetReplay':receipt,'evidence':evidence,**FLAGS})


def verify_analysis(root, path, file_sha):
    analysis=verify_seal(parse_json(pinned_input(path,file_sha,64*1024*1024)),ANALYSIS_SCHEMA)
    require(set(analysis) == {'schemaVersion','sha256','createdAt','sourceCommit','sourceFiles','protocol','inputs','datasetReplay','evidence',*FLAGS}, 'Analysis schema changed')
    same(analysis['protocol'],SPEC,'Post-measurement descriptive protocol changed')
    require(all(type(analysis[k]) is type(v) and analysis[k] == v for k,v in FLAGS.items()), 'Analysis grants unsupported qualification')
    require(timestamp(analysis['datasetReplay']['verifiedAt'],'native.verifiedAt') <= timestamp(analysis['createdAt'],'analysis.createdAt'), 'Analysis predates source replay')
    sources=sources_at(root,analysis['sourceCommit'],analysis['sourceFiles'],SOURCE_PATHS)
    evidence,receipt=analyze_receipts(root,analysis['inputs']);same(evidence,analysis['evidence'],'Sensitivity differs from complete original receipts')
    verify_seal(analysis['datasetReplay'],receipt['schemaVersion'])
    same({k:v for k,v in receipt.items() if k not in ('sha256','verifiedAt')},
        {k:v for k,v in analysis['datasetReplay'].items() if k not in ('sha256','verifiedAt')}, 'Embedded source replay changed')
    return sealed({'schemaVersion':VERIFICATION_SCHEMA,'status':'pass','verifiedAt':paired.now(),
        'sourceCommit':analysis['sourceCommit'],'verifiedSourceFiles':len(sources),'analysisFileSha256':file_sha,
        'evidence':evidence,'modelCallsDuringVerification':0,**FLAGS})
