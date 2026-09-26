#!/usr/bin/env python3
"""Independently check persisted prefix-cache numerical evidence without inference."""

import argparse
import hashlib
import importlib.util
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from decision_runtime.artifacts import read_json,sealed,verify_seal,write_new
from decision_runtime.contracts import Request

spec = importlib.util.spec_from_file_location('batch_evidence_verifier',ROOT/'scripts/verify-decision-batching.py')
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)
require = checks.require


def verify(source, plan, result, batch_plan, batch_result):
    for value,schema in [(source,'synthetic-robustness-plan'),(plan,'prefix-plan'),(result,'prefix-diagnostic'),
                         (batch_plan,'batching-plan'),(batch_result,'batching-diagnostic')]:
        verify_seal(value,f'agat.decision.{schema}.v1')
    require(plan['sourcePlanSha256']==batch_plan['sourcePlanSha256']==source['sha256'],'Wrong source binding')
    require(result['planSha256']==plan['sha256'] and batch_result['planSha256']==batch_plan['sha256'],'Wrong plan binding')
    require(plan['serialProfile']==batch_plan['serialProfile'],'Changed serial profile')
    require(plan['absoluteTolerance']==0.0001 and plan['sameOutcomeRequired'] is True,'Changed criterion')
    require(plan['serialProfile']['calibration']['temperature']==1,'Only raw logits are supported')
    require(plan['variants']==['serial','full_cache','split_fresh','shared_prefix']
            and plan['sharedOrders']==['forward','reversed'],'Changed protocol')
    for name,digest in plan['harnessFiles'].items():
        path=(ROOT/name).resolve()
        require(path.is_relative_to(ROOT) and hashlib.sha256(path.read_bytes()).hexdigest()==digest,'Changed harness')
    by_target=defaultdict(list)
    for case in source['cases']: by_target[case['targetTokens']].append(case)
    selected=[c for target in sorted(by_target) for c in by_target[target][:4]]
    require(len(selected)==len(plan['groups']),'Wrong source selection')
    cases=[];targets={};group_cases={}
    for group,original in zip(plan['groups'],selected):
        request=Request.from_dict(original['request'])
        require(group['sourceCaseId']==request.id and group['sourceTargetTokens']==original['targetTokens'],'Wrong source group')
        require(len(group['cases'])==3 and len({c['request']['question'] for c in group['cases']})==3,'Wrong question variants')
        require(len({c['prefixTokenSha256'] for c in group['cases']})==1,'Inconsistent shared prefix')
        for index,case in enumerate(group['cases']):
            r=Request.from_dict(case['request'])
            require(r.input_sha256==case['inputSha256'],'Wrong input fingerprint')
            require((r.state,r.kind,r.options)==(request.state,request.kind,request.options),'Source state/options changed')
            if index==0: require(r.input_sha256==request.input_sha256 and case['inputTokens']==original['targetTokens'],'Original changed')
            require(0<case['prefixTokens'] and 0<case['suffixTokens']
                    and case['prefixTokens']+case['suffixTokens']==case['inputTokens']<=plan['serialProfile']['model']['maxInputTokens'],
                    'Invalid token partition')
            targets[r.id]=group['sourceTargetTokens'];cases.append(case)
        group_cases[request.id]=group['cases']
    lookup={c['request']['id']:c for c in cases}
    require(len(lookup)==len(cases),'Duplicate case IDs')
    def check_call(row,variant,order,case):
        require((row['variant'],row['order'],row['caseId'],row['inputSha256'])==
                (variant,order,case['request']['id'],case['inputSha256']),'Call binding/order mismatch')
        require(math.isfinite(row['wallMs']) and row['wallMs']>0,'Invalid duration')
        checks.check_outcome(row,{'request':case['request'],'targetTokens':case['inputTokens']},plan['serialProfile']['policy'])
    require(len(result['warmup'])==3,'Incomplete warmup')
    for row,variant in zip(result['warmup'],plan['variants'][:3]):check_call(row,variant,'forward',cases[0])
    expected=[(variant,'forward',case) for variant in plan['variants'][:3] for case in cases]
    expected += [('shared_prefix',order,case) for group in plan['groups'] for order in plan['sharedOrders']
                 for case in (group['cases'] if order=='forward' else list(reversed(group['cases'])))]
    require(len(expected)==len(result['calls']),'Incomplete calls')
    rows={}; comparisons=[]
    for row,(variant,order,case) in zip(result['calls'],expected):
        check_call(row,variant,order,case);case_id=case['request']['id']
        references=[] if variant=='serial' else [('serial',rows[('serial','forward',case_id)])]
        if variant=='shared_prefix':
            references += [('split_fresh',rows[('split_fresh','forward',case_id)])]
            if order=='reversed': references += [('forward_order',rows[('shared_prefix','forward',case_id)])]
        for name,reference in references:
            comparisons.append({'variant':variant,'reference':name,'caseId':case_id,'order':order,
                                **checks.comparison(reference,row,0.0001)})
        rows[(variant,order,case_id)]=row
    require(comparisons==result['comparisons'],'Comparison mismatch')
    require([p['sourceCaseId'] for p in result['prefixes']]==list(group_cases),'Incomplete prefix builds')
    for prefix in result['prefixes']:
        group=next(g for g in plan['groups'] if g['sourceCaseId']==prefix['sourceCaseId'])
        require(prefix['sourceTargetTokens']==group['sourceTargetTokens'] and math.isfinite(prefix['wallMs']) and prefix['wallMs']>0,
                'Invalid prefill binding/time')
        require(prefix['signature']['logicalBytes']>0 and set(prefix['signature']['layerTypes'])=={'KVCache','ArraysCache'},'Wrong cache types')
    integrity=[{'sourceCaseId':g['sourceCaseId'],'caseId':c['request']['id'],'order':order,'unchanged':True}
               for g in plan['groups'] for order in plan['sharedOrders']
               for c in (g['cases'] if order=='forward' else list(reversed(g['cases'])))]
    require(result['prefixIntegrity']==integrity,'Missing or failed cache-integrity check')
    summary={'scoredRequests':len(expected),'prefixPrefills':len(plan['groups']),'comparisons':len(comparisons),
             'criterionViolations':sum(not c['equivalentUnderCriterion'] for c in comparisons),
             'changedOutcomes':sum(not c['sameOutcome'] for c in comparisons),'prefixIntegrityChecks':len(integrity),'prefixMutations':0}
    require(result['summary']==summary and result['profileStable'] is True and result['failure'] is None,'Invalid completion summary')
    require(result['status']=='diagnostic_only' and result['prefixCacheEnabled'] is False
            and result['qualifiedForRouting'] is False,'Unexpected qualification claim')
    require(result['equivalentUnderCriterion']==all(c['equivalentUnderCriterion'] for c in comparisons),'Wrong numerical conclusion')
    previous={r['inputSha256']:r for call in batch_result['calls'] if call['variant']=='serial' for r in call['rows']}
    for group in plan['groups']:
        case=group['cases'][0]; row=rows[('serial','forward',case['request']['id'])]
        comparison=checks.comparison(previous[case['inputSha256']],row,0.0001)
        require(comparison['maxAbsoluteLogitDelta']==0 and comparison['maxAbsoluteProbabilityDelta']==0
                and comparison['sameOutcome'],'Serial baseline changed between processes')
    timing=[]
    for target in sorted(by_target):
        for variant in plan['variants']:
            calls=[r for r in result['calls'] if r['variant']==variant and r['order']=='forward' and targets[r['caseId']]==target]
            prefill=sum(p['wallMs'] for p in result['prefixes'] if p['sourceTargetTokens']==target) if variant=='shared_prefix' else 0
            total=sum(c['wallMs'] for c in calls)+prefill
            timing.append({'sourceTargetTokens':target,'variant':variant,'requests':len(calls),'prefillMs':round(prefill,3),
                           'totalMeasuredMs':round(total,3),'amortizedMsPerQuestion':round(total/len(calls),3)})
    deltas=[]
    for variant,reference in [('full_cache','serial'),('split_fresh','serial'),('shared_prefix','serial'),
                              ('shared_prefix','split_fresh'),('shared_prefix','forward_order')]:
        checks_for_pair=[c for c in comparisons if c['variant']==variant and c['reference']==reference]
        deltas.append({'variant':variant,'reference':reference,'comparisons':len(checks_for_pair),
                       'violations':sum(not c['equivalentUnderCriterion'] for c in checks_for_pair),
                       'maxAbsoluteLogitDelta':max(c['maxAbsoluteLogitDelta'] for c in checks_for_pair),
                       'maxAbsoluteProbabilityDelta':max(c['maxAbsoluteProbabilityDelta'] for c in checks_for_pair)})
    return {'summary':summary,'deltas':deltas,'timingForwardOrderIncludingOnePrefillPerThreeQuestions':timing,
            'unchangedSerialBaselinesFromEarlierProcess':len(plan['groups'])}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    names=['robustness-plan','prefix-plan','prefix-result','batching-plan','batching-result']
    artifacts=[read_json(args.evidence_dir/f'{name}.json') for name in names]
    verified=verify(*artifacts)
    report=sealed({'schemaVersion':'agat.decision.prefix-verification.v1','createdAt':datetime.now(timezone.utc).isoformat(),
                   'status':'verified','artifacts':{n:a['sha256'] for n,a in zip(names,artifacts)},**verified,
                   'verifierFiles':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
                                    for name in ['scripts/verify-decision-prefix.py','scripts/verify-decision-batching.py']},
                   'limitations':['Cache tensors and token arrays are not persisted; tensor integrity and exact tokenizer boundaries were checked inside the probe, not re-executed here.',
                                  'Timing sums use only forward order and include a prefill for every group of three questions; validation/hash overhead is excluded.',
                                  'Verification does not qualify the cache path or approve transferring calibration.']})
    write_new(args.output,report)
    print(verified)


if __name__=='__main__': main()
