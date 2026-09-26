"""Compare decoded labels on the already frozen synthetic robustness requests."""

import copy
import hashlib
import time
from datetime import datetime, timezone
from pathlib import Path

from decision_runtime.artifacts import sealed, verify_seal
from decision_runtime.contracts import Request, canonical_json, fingerprint
from scripts.lib.decision_baselines import BaselineError, label_result
from scripts.lib.decision_performance import validate_result
from scripts.lib.decision_robustness import SCHEMA, SCENARIOS, diagnostic_summary

ROOT=Path(__file__).resolve().parents[2]


def blind_request(request):
    # Direct MLX ignores id; a chat model sees the JSON. Do not leak scenario names to it.
    return Request.from_dict({**request.to_dict(),'id':'probe-'+fingerprint(request.id)[:20]})


def validate_synthetic_evidence(plan, direct):
    verify_seal(plan,'agat.decision.synthetic-robustness-plan.v1'); verify_seal(direct,SCHEMA)
    if (plan.get('generator')!='agat.synthetic-behavior.v1' or plan.get('labelSource')!='synthetic-authored'
            or direct.get('planSha256')!=plan['sha256'] or direct.get('status')!='diagnostic_only'
            or direct.get('qualifiedForRouting') is not False or direct.get('datasetUsed') is not False
            or fingerprint(plan['profile'])!=plan['profileSha256']
            or not isinstance(plan.get('cases'),list) or not 1<=len(plan['cases'])<=36):
        raise ValueError('Use a complete frozen synthetic diagnostic, not a qualification dataset')
    cases={}; expected_rows={}
    for case in plan['cases']:
        request=Request.from_dict(case['request'])
        if (request.id in cases or request.kind!='choice' or case['scenario'] not in SCENARIOS
                or case['expectedOptionId']!=SCENARIOS[case['scenario']][1]
                or case['inputSha256']!=request.input_sha256):
            raise ValueError('Invalid synthetic case binding')
        if len(canonical_json(request.to_dict()).encode())>6144:
            raise ValueError('Synthetic input exceeds the conservative generative context budget')
        cases[request.id]=case
        for order in ('original','reversed'):
            raw=copy.deepcopy(case['request'])
            if order=='reversed': raw['options'].reverse()
            q=Request.from_dict(raw)
            if q.input_sha256!=case['reversedInputSha256' if order=='reversed' else 'inputSha256']:
                raise ValueError('Reversed synthetic input binding mismatch')
            expected_rows[(request.id,order)]=q
    seen=set()
    if not isinstance(direct.get('rows'),list): raise ValueError('Missing direct result rows')
    for row in direct['rows']:
        key=(row['caseId'],row['order'])
        if key in seen or key not in expected_rows: raise ValueError('Duplicate or unknown direct result')
        seen.add(key); case=cases[row['caseId']]
        if any(row[k]!=case[k] for k in ('scenario','position','targetTokens','expectedOptionId')):
            raise ValueError('Direct result metadata differs from the synthetic plan')
        validate_result(row['result'],expected_rows[key],plan['profile'])
    if seen!=set(expected_rows): raise ValueError('Incomplete direct evidence')
    return cases,expected_rows


def compare_synthetic(plan, direct, backend_factory, save_plan, *, time_budget_s=300, progress=None):
    if type(time_budget_s) is not int or not 1<=time_budget_s<=600: raise ValueError('Invalid comparison time budget')
    cases,requests=validate_synthetic_evidence(plan,direct)
    backend=backend_factory()
    files=['scripts/compare-decision-robustness.py','scripts/lib/decision_robustness_baseline.py',
           'scripts/lib/decision_robustness.py','scripts/lib/decision_baselines.py','scripts/lib/decision_performance.py']
    comparison_plan=sealed({'schemaVersion':'agat.decision.synthetic-baseline-plan.v1','createdAt':datetime.now(timezone.utc).isoformat(),
                            'sourcePlanSha256':plan['sha256'],'directResultSha256':direct['sha256'],
                            'model':copy.deepcopy(backend.identity),'requestCount':len(requests),'timeBudgetSeconds':time_budget_s,
                            'probabilities':'unavailable_not_estimated','retry':False,
                            'requestIdMapping':'probe- plus first 20 hex of SHA256(canonical JSON source id); task fields unchanged',
                            'harnessFiles':{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in files}})
    save_plan(comparison_plan)
    rows=[]; started=time.monotonic(); stopped=None
    for (case_id,order),request in requests.items():
        if time.monotonic()-started>=time_budget_s: stopped='time_budget'; break
        begin=time.monotonic()
        submitted=blind_request(request)
        try:
            predicted=backend.predict(submitted)
            result={**label_result(request,predicted['selectedOptionId']),**predicted}
        except Exception as error:
            result={'status':'error','reason':error.reason if isinstance(error,BaselineError) else 'backend_error',
                    'selectedOptionId':None,'value':None}
        row={k:cases[case_id][k] for k in ('scenario','position','targetTokens','expectedOptionId')}
        row.update(caseId=case_id,order=order,sourceInputSha256=request.input_sha256,
                   submittedRequestId=submitted.id,result={**result,'inputSha256':submitted.input_sha256,
                   'durationMs':round((time.monotonic()-begin)*1000,3)})
        rows.append(row)
        if progress: progress(case_id,order,result['status'])
        if result['status']=='error': stopped='backend_error'; break
    return sealed({'schemaVersion':'agat.decision.synthetic-baseline.v1','createdAt':datetime.now(timezone.utc).isoformat(),
                   'status':'incomplete' if stopped else 'diagnostic_only','qualifiedForRouting':False,'routingEnabled':False,
                   'comparisonPlanSha256':comparison_plan['sha256'],'sourcePlanSha256':plan['sha256'],
                   'model':backend.identity,'probabilities':'unavailable_not_estimated','stoppedReason':stopped,
                   'elapsedMs':round((time.monotonic()-started)*1000,3),'summary':diagnostic_summary(rows),
                   'byScenario':{s:diagnostic_summary([r for r in rows if r['scenario']==s]) for s in SCENARIOS},
                   'directSummary':diagnostic_summary(direct['rows']),
                   'directByScenario':{s:diagnostic_summary([r for r in direct['rows'] if r['scenario']==s]) for s in SCENARIOS},
                   'rows':rows,'limits':['Same authored dependent variants; no real holdout, human labels, or qualification.',
                                        'Only request IDs are replaced with opaque hashes; state, question, options and their order are unchanged.',
                                        'Generated labels have no logits calibration or confidence threshold.',
                                        'ok means a valid decoded non-abstain label, unlike thresholded direct logits.',
                                        'Different model sizes, quantization, prompt and runtime prevent attributing differences to one factor.',
                                        'No instruction, threshold, model weight, or automatic routing changes.',
                                        'Stops on first backend failure; duration checked between separately bounded HTTP calls.']})
