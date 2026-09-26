#!/usr/bin/env python3
"""Decompose batch numerical drift into backbone and final letter projection."""

import argparse
import hashlib
import sys
import time
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from decision_runtime.artifacts import read_json,sealed,verify_seal,write_new
from decision_runtime.contracts import Policy
from decision_runtime.engine import DecisionEngine,Scores
from decision_runtime.mlx_backend import MlxBackend
from scripts.lib.decision_batching import compare,outcome,prepare_batch,source_requests


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-plan',type=Path,required=True)
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--policy',type=Path,required=True)
    parser.add_argument('--plan-output',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists() or args.plan_output.exists() or args.output.resolve()==args.plan_output.resolve():
        parser.error('Use two distinct new evidence paths')
    source=read_json(args.source_plan);groups=source_requests(source)
    # First four source rows at each length, selected before computing any result.
    chosen={target:requests[:4] for target,requests in groups.items()}
    policy=Policy.from_dict(read_json(args.policy))
    backend=MlxBackend(args.manifest,source['maxInputTokens'],cache_limit_mib=128)
    if backend.identity['tokenizerSha256']!=source['profile']['model']['tokenizerSha256']:
        raise ValueError('Tokenizer changed')
    for target,requests in chosen.items():
        tokens,_=prepare_batch(backend,requests)
        if len(tokens[0])!=target: raise ValueError('Token length changed')
    files=['scripts/diagnose-decision-batch-head.py','scripts/lib/decision_batching.py']
    plan=sealed({'schemaVersion':'agat.decision.batch-head-plan.v1','createdAt':datetime.now(timezone.utc).isoformat(),
                 'sourcePlanSha256':source['sha256'],'serialProfile':DecisionEngine(backend,policy).profile(),
                 'selection':'first four rows in source order at each token length','batchSize':4,
                 'hypothesis':'separate batch backbone differences from matrix-versus-rowwise letter projection',
                 'absoluteTolerance':0.0001,'sameOutcomeRequired':True,
                 'cases':[{'request':r.to_dict(),'inputSha256':r.input_sha256,'targetTokens':target}
                          for target,requests in chosen.items() for r in requests],
                 'harnessFiles':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in files}})
    write_new(args.plan_output,plan)
    mx=backend.mx;rows=[];started=time.perf_counter()
    for target,requests in chosen.items():
        tokens,labels=prepare_batch(backend,requests)
        weights=backend.head.weight[mx.array(labels)]
        hidden=backend.text_model.model(mx.array(tokens),cache=None)[:,-1,:]
        matrix=(hidden @ weights.T).astype(mx.float32)
        rowwise=[(hidden[index:index+1] @ weights.T).astype(mx.float32)[0] for index in range(len(requests))]
        mx.eval(hidden,matrix,*rowwise)
        matrix_values=matrix.tolist();row_values=[r.tolist() for r in rowwise]
        for index,request in enumerate(requests):
            independent_hidden=backend.text_model.model(mx.array([tokens[index]]),cache=None)[:,-1,:]
            independent_logits=(independent_hidden @ weights.T).astype(mx.float32)[0]
            hidden_delta=mx.max(mx.abs(hidden[index:index+1].astype(mx.float32)-independent_hidden.astype(mx.float32)))
            mx.eval(independent_logits,hidden_delta)
            reference=outcome(request,Scores(independent_logits.tolist(),target),policy)
            batched=outcome(request,Scores(matrix_values[index],target),policy)
            same_backbone_row_head=outcome(request,Scores(row_values[index],target),policy)
            rows.append({'caseId':request.id,'inputSha256':request.input_sha256,'inputTokens':target,
                         'hiddenMaxAbsoluteDelta':hidden_delta.item(),'serial':reference,
                         'batchMatrixHead':batched,'batchRowwiseHead':same_backbone_row_head,
                         'matrixVersusSerial':compare(reference,batched,0.0001),
                         'rowwiseVersusSerial':compare(reference,same_backbone_row_head,0.0001),
                         'matrixVersusRowwise':compare(same_backbone_row_head,batched,0.0001)})
        print(f'{target} tokens: compared one 4-row backbone and four independent backbones',flush=True)
    result=sealed({'schemaVersion':'agat.decision.batch-head-diagnostic.v1','createdAt':datetime.now(timezone.utc).isoformat(),
                   'status':'diagnostic_only','planSha256':plan['sha256'],'elapsedMs':round((time.perf_counter()-started)*1000,3),
                   'qualifiedForRouting':False,'batchingEnabled':False,'rows':rows,
                   'summary':{'cases':len(rows),'backboneDifferent':sum(r['hiddenMaxAbsoluteDelta']!=0 for r in rows),
                              'matrixViolations':sum(not r['matrixVersusSerial']['equivalentUnderCriterion'] for r in rows),
                              'rowwiseViolations':sum(not r['rowwiseVersusSerial']['equivalentUnderCriterion'] for r in rows)},
                   'limitations':['Small preselected numerical diagnostic, not a deployment or calibration approval.',
                                  'Only batch size four and source-first rows; no latency inference from this decomposition.',
                                  'Runtime serving code is unchanged.']})
    write_new(args.output,result)
    print(result['summary'],flush=True)
    return 0


if __name__=='__main__': raise SystemExit(main())
