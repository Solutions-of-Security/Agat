"""Offline equal-length batch prototype. It does not change the serving runtime."""

from __future__ import annotations

import copy
import hashlib
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from decision_runtime.artifacts import sealed, verify_seal
from decision_runtime.contracts import Policy, Request, fingerprint, probabilities
from decision_runtime.engine import DecisionEngine, Scores
from decision_runtime.mlx_backend import encode_request
from scripts.lib.decision_resources import MlxMemory

ROOT = Path(__file__).resolve().parents[2]
PLAN_SCHEMA = 'agat.decision.batching-plan.v1'
SCHEMA = 'agat.decision.batching-diagnostic.v1'


def source_requests(source):
    verify_seal(source, 'agat.decision.synthetic-robustness-plan.v1')
    if source.get('generator') != 'agat.synthetic-behavior.v1' or source.get('labelSource') != 'synthetic-authored':
        raise ValueError('Only the existing authored synthetic probe is accepted')
    cases = source.get('cases')
    if not isinstance(cases, list) or not 4 <= len(cases) <= 48:
        raise ValueError('Use 4..48 synthetic requests')
    groups = defaultdict(list)
    for case in cases:
        request = Request.from_dict(case['request'])
        target = case['targetTokens']
        if (request.kind != 'choice' or request.input_sha256 != case['inputSha256']
                or type(target) is not int or not 64 <= target <= 4096):
            raise ValueError('Invalid synthetic request or binding')
        groups[target].append(request)
    requests = [r for rows in groups.values() for r in rows]
    if (len(groups) > 3 or any(len(rows) % 4 for rows in groups.values())
            or len({r.id for r in requests}) != len(requests)):
        raise ValueError('Each token-length group must contain a multiple of four unique requests')
    return dict(sorted(groups.items()))


def prepare_batch(backend, requests):
    if not isinstance(requests, (tuple, list)) or not 1 <= len(requests) <= 4:
        raise ValueError('Batch must contain 1..4 independent requests')
    if any(not isinstance(r, Request) or r.kind != 'choice' for r in requests):
        raise ValueError('The offline prototype accepts Choice only')
    encoded = [encode_request(backend.tokenizer, r, backend.max_tokens) for r in requests]
    if len({len(ids) for ids, _ in encoded}) != 1:
        raise ValueError('Only exact equal token lengths; no padding or silent truncation')
    labels = max((labels for _, labels in encoded), key=len)
    if any(row_labels != labels[:len(row_labels)] for _, row_labels in encoded):
        raise ValueError('Incompatible letter token mapping')
    return [ids for ids, _ in encoded], labels


def score_batch(backend, requests):
    tokens, labels = prepare_batch(backend, requests)
    mx = backend.mx
    # Batch dimension isolates rows; no concatenated questions, padding or reused
    # recurrent/KV cache. The installed Qwen3.5 backbone accepts [B, S] inputs.
    hidden = backend.text_model.model(mx.array(tokens), cache=None)[:, -1, :]
    weights = backend.head.weight[mx.array(labels)]
    logits = (hidden @ weights.T).astype(mx.float32)
    mx.eval(logits)
    rows = logits.tolist()
    if len(rows) != len(requests):
        raise ValueError('Backend returned the wrong batch dimension')
    return [Scores(row[:len(request.options)], len(tokens[index])) for index, (request, row) in enumerate(zip(requests, rows))]


def outcome(request, scores, policy):
    ps = probabilities(scores.logits, len(request.options))
    ranked = sorted(range(len(ps)), key=lambda i: ps[i], reverse=True)
    selected = request.options[ranked[0]]
    margin = ps[ranked[0]]-ps[ranked[1]]
    reason = ('abstain_option' if selected.abstain else 'below_threshold'
              if ps[ranked[0]] < policy.min_probability or margin < policy.min_margin else 'accepted')
    return {'logits': [float(z) for z in scores.logits], 'probabilities': ps, 'inputTokens': scores.input_tokens,
            'selectedOptionId': selected.id, 'status': 'ok' if reason == 'accepted' else 'abstain', 'reason': reason}


def compare(left, right, tolerance):
    if len(left['logits']) != len(right['logits']) or left['inputTokens'] != right['inputTokens']:
        raise ValueError('Incompatible batch comparison')
    logit_delta = max(abs(a-b) for a,b in zip(left['logits'],right['logits']))
    probability_delta = max(abs(a-b) for a,b in zip(left['probabilities'],right['probabilities']))
    same_outcome = all(left[key] == right[key] for key in ('selectedOptionId','status','reason'))
    return {'maxAbsoluteLogitDelta': logit_delta, 'maxAbsoluteProbabilityDelta': probability_delta,
            'sameOutcome': same_outcome, 'withinTolerance': logit_delta <= tolerance and probability_delta <= tolerance,
            'equivalentUnderCriterion': same_outcome and logit_delta <= tolerance and probability_delta <= tolerance}


def diagnose_batching(source, backend_factory, save_plan, *, policy=None, tolerance=0.0001, time_budget_s=300,
                      batch_scorer=score_batch, memory_factory=MlxMemory, progress=None):
    groups = source_requests(source)
    if (type(tolerance) not in (int,float) or not 0 <= tolerance <= 0.01
            or type(time_budget_s) is not int or not 1 <= time_budget_s <= 600):
        raise ValueError('Invalid comparison tolerance or time budget')
    policy = policy or Policy()
    backend = backend_factory();memory = memory_factory(backend)
    if backend.identity.get('tokenizerSha256') != source['profile']['model'].get('tokenizerSha256'):
        raise ValueError('Tokenizer changed; rebuild exact-length synthetic requests explicitly')
    for target, requests in groups.items():
        for request in requests:
            tokens, _ = prepare_batch(backend,[request])
            if len(tokens[0]) != target:
                raise ValueError('Exact input token count changed')
    files = ['scripts/lib/decision_batching.py','scripts/diagnose-decision-batching.py']
    plan = sealed({'schemaVersion':PLAN_SCHEMA,'createdAt':datetime.now(timezone.utc).isoformat(),
                   'sourcePlanSha256':source['sha256'],'serialProfile':copy.deepcopy(DecisionEngine(backend,policy).profile()),
                   'batchExecution':{'kind':'offline-equal-length-prototype','sizes':[2,4],
                                     'rowOrders':['forward','reversed'],'padding':False,'sharedCache':False},
                   'tolerance':{'absoluteLogit':tolerance,'absoluteProbability':tolerance,'sameOutcomeRequired':True},
                   'timeBudgetSeconds':time_budget_s,'warmupRows':[1,2,4],
                   'cases':[{'request':r.to_dict(),'inputSha256':r.input_sha256,'targetTokens':target}
                            for target, requests in groups.items() for r in requests],
                   'harnessFiles':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in files}})
    save_plan(plan)
    started = time.perf_counter();rows=[];warm=[];serial={};batch_results={};stopped=None;comparisons=[]
    def one(requests, variant, order, scorer):
        begin = time.perf_counter();scores = scorer(requests);elapsed = (time.perf_counter()-begin)*1000
        if len(scores) != len(requests): raise ValueError('Wrong score count')
        values = [outcome(request,score,policy) for request,score in zip(requests,scores)]
        for request,value in zip(requests,values):
            if value['inputTokens'] != len(prepare_batch(backend,[request])[0][0]):
                raise ValueError('Scorer token count mismatch')
        return {'variant':variant,'rowOrder':order,'batchSize':len(requests),'wallMs':round(elapsed,3),
                'rows':[{'caseId':request.id,'inputSha256':request.input_sha256,**value}
                        for request,value in zip(requests,values)],'memoryAfter':memory.sample()}
    try:
        first = next(iter(groups.values()))
        for count in (1,2,4):
            if time.perf_counter()-started >= time_budget_s: stopped='time_budget';break
            warm.append(one(first[:count],'warmup','forward',lambda requests:batch_scorer(backend,requests)))
        if not stopped:
            for target,requests in groups.items():
                for request in requests:
                    if time.perf_counter()-started >= time_budget_s: stopped='time_budget';break
                    row=one([request],'serial','forward',lambda batch:[backend.score(batch[0])]);rows.append(row)
                    serial[request.id]=row['rows'][0]
                if progress: progress('serial',target,len(rows))
                if stopped: break
        if not stopped:
            for size in (2,4):
                for target,requests in groups.items():
                    for offset in range(0,len(requests),size):
                        for order in ('forward','reversed'):
                            if time.perf_counter()-started >= time_budget_s: stopped='time_budget';break
                            batch=requests[offset:offset+size]
                            if order=='reversed': batch=list(reversed(batch))
                            row=one(batch,f'batch_{size}',order,lambda values:batch_scorer(backend,values));rows.append(row)
                            for result in row['rows']:
                                comparisons.append({'kind':'versus_serial','caseId':result['caseId'],'batchSize':size,'rowOrder':order,
                                                    **compare(serial[result['caseId']],result,tolerance)})
                                if order=='forward': batch_results[(size,result['caseId'])]=result
                                else: comparisons.append({'kind':'row_permutation','caseId':result['caseId'],'batchSize':size,
                                                          **compare(batch_results[(size,result['caseId'])],result,tolerance)})
                        if stopped: break
                    if progress: progress(f'batch_{size}',target,len(rows))
                    if stopped: break
                if stopped: break
    except Exception:
        stopped='scoring_or_validation_failed'
    profile_stable = DecisionEngine(backend,policy).profile() == plan['serialProfile']
    complete = not stopped and profile_stable and len(rows)==sum(len(group) for group in groups.values())*5//2
    return sealed({'schemaVersion':SCHEMA,'createdAt':datetime.now(timezone.utc).isoformat(),'planSha256':plan['sha256'],
                   'status':'diagnostic_only' if complete else 'incomplete','stoppedReason':stopped,
                   'batchingEnabled':False,'qualifiedForRouting':False,'profileStable':profile_stable,
                   'elapsedMs':round((time.perf_counter()-started)*1000,3),'warmup':warm,'calls':rows,'comparisons':comparisons,
                   'equivalentUnderCriterion':complete and all(row['equivalentUnderCriterion'] for row in comparisons),
                   'summary':{'measuredForwardCalls':len(rows),'measuredRows':sum(len(row['rows']) for row in rows),
                              'comparisonViolations':sum(not row['equivalentUnderCriterion'] for row in comparisons),
                              'changedOutcomes':sum(not row['sameOutcome'] for row in comparisons)},
                   'limitations':['Offline prototype only; serving API and runtime implementation are unchanged.',
                                  'Equal exact lengths, Choice only, no padding, no mixed-length queue or shared prefix cache.',
                                  'Repeated authored synthetic inputs test numerical behavior, not independent correctness.',
                                  'Time budget is checked between forward calls, not a hard GPU cancellation deadline.',
                                  'Warmup covers one sequence length; ordered phases and allocator/compiler reuse affect timing.']})
