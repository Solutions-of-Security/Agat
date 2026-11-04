"""Separate observed HTTP replies from source-inferred logical forward calls."""
from collections import Counter
import hashlib

from decision_runtime.artifacts import sealed, verify_seal
from decision_runtime.contracts import number, parse_json
from scripts.lib import decision_public_counterbalanced_real_primary as native
from scripts.lib import decision_public_paired_real_primary as paired
from scripts.lib import decision_public_replication_sensitivity as sensitivity
from scripts.lib.decision_public_latency_decomposition import private_path
from scripts.lib.decision_public_load_verification import same, sources_at
from scripts.lib.decision_public_real_primary_verification import verify as verify_native
from scripts.lib.decision_public_sources import pinned_input
from scripts.lib.decision_shadow_pilot import require, timestamp

ANALYSIS_SCHEMA='agat.decision.public-native-execution-accounting.v1'
VERIFICATION_SCHEMA='agat.decision.public-native-execution-accounting-verification.v1'
SOURCE_PATHS=[*sensitivity.SOURCE_PATHS,'scripts/analyze-public-support-native-execution.py',
    'scripts/verify-public-support-native-execution.py','scripts/test/test_decision_public_native_execution_accounting.py']
IMPLEMENTATION='e403f87db306a727bf25fa319d03f5a66f96a7cc3606a3c0d26cf1d929a6c9d8'
CONTRACT_FILES={
 'decision_runtime/engine.py':'c5b3aeeaa364f1abc22424bbf4691b58bf1b336776afccab15a8029de580027a',
 'decision_runtime/server.py':'e8562edc91b8664947ad4c33802822c814690c0c15504927009af585f22ff5b9',
 'decision_runtime/isolated.py':'0156691cc3f6a7c683fd349a3cc5e0ad9e7a216238f584232985c7478dd4ce7c',
 'decision_runtime/mlx_backend.py':'bcceb65e5949112290c64815e68bd67845519ed18ba4085f667060f4be5428be'}
FLAGS= sensitivity.FLAGS
SPEC={'schemaVersion':'agat.decision.native-execution-accounting-protocol.v1','analysisTiming':'post_measurement_source_contract_accounting',
 'dataset':'complete_original_counterbalanced_native_inventory','runtimeVersion':'0.12.3','implementationSha256':IMPLEMENTATION,
 'sourceContractFiles':CONTRACT_FILES,'successfulScoreReplies':['ok','abstain'],
 'logicalForwardBasis':'one_MlxBackend_score_model_call_and_mx_eval_before_each_successful_reply',
 'contextRejectionBasis':'context_too_long_from_encode_request_before_model_call',
 'busyBasis':'HTTP_503_before_engine_decide_and_backend_score',
 'allocatorCacheMeaning':'reusable_free_buffers_not_request_result_memoization',
 'modelForwardCompletionsAreSourceInferred':True,'gpuKernelInvocationsMeasured':False,
 'gpuInferenceDurationMeasured':False,'hardwareConcurrencyEstablished':False,'newModelCalls':0}


def source_contract(model,sources):
    require(model['implementationSha256']==IMPLEMENTATION,'Unsupported runtime implementation; no inferred execution counts')
    for name,pin in CONTRACT_FILES.items():
        require(name in sources and hashlib.sha256(sources[name]).hexdigest()==pin,'Unsupported execution-path source '+name)
    number(model['allocatorCacheLimitBytes'],0,4096*1024**2)
    require(model['maxInputTokens']==2048 and isinstance(model['inferenceExecution'],dict)
        and model['inferenceExecution']['kind']=='isolated-process','Unsupported native execution mode or token gate')
    return {'runtimeVersion':'0.12.3','implementationSha256':IMPLEMENTATION,'sourceFiles':dict(CONTRACT_FILES),
        'allocatorCacheLimitBytes':model['allocatorCacheLimitBytes'],'allocatorCacheSemantics':'reusable_free_buffers',
        'requestResultMemoizationImplemented':False,'crossRequestKvStateSuppliedByScore':False,
        'logicalForwardCompletionsAreSourceInferred':True,'gpuKernelInvocationsMeasured':False}


def classify_reply(http_status,body,model):
    require(type(http_status) is int and body['runtimeVersion']=='0.12.3'
        and body['model']['implementationSha256']==model['implementationSha256'],'Unexpected native reply implementation')
    if http_status==200:
        require(body['status'] in ('ok','abstain') and type(body['generatedTokens']) is int and body['generatedTokens']==0
            and type(body['inputTokens']) is int and 0<body['inputTokens']<=model['maxInputTokens'],'Invalid completed decision score')
        kind='completed_score';outcome=body['status'];backend=forward=1
    elif http_status==422:
        require(body['status']=='error' and body['reason']=='context_too_long' and 'generatedTokens' not in body,'Unsupported context refusal path')
        kind=outcome='context_rejected';backend=1;forward=0
    elif http_status==503:
        require(body['status']=='error' and body['reason']=='busy' and body['id'] is None and body['inputSha256'] is None
            and 'generatedTokens' not in body,'Unsupported admission refusal path')
        kind=outcome='busy';backend=forward=0
    else:raise ValueError('Unhandled native HTTP outcome; no partial accounting')
    return {'httpStatus':http_status,'outcome':outcome,'executionKind':kind,'backendScoreEntriesSourceInferred':backend,
        'logicalForwardCompletionsSourceInferred':forward,'generatedDecisionTokensObserved':body.get('generatedTokens'),
        'inputTokensObserved':body.get('inputTokens')}


def account(model,sources,case_rows,warmups,expected_outcomes,count):
    contract=source_contract(model,sources)
    require(type(count) is int and count>0 and len(case_rows)==2*count,'Incomplete original native attempts')
    identities=[(r['index'],r['replica']) for r in case_rows]
    require(all(type(r['index']) is int and type(r['replica']) is int for r in case_rows)
        and len(set(identities))==len(identities) and set(identities)=={(i,r) for i in range(count) for r in (0,1)},'Missing/repeated native replica')
    rows=[]
    for row in case_rows:
        body=paired.raw_body(row,'responseBody');classification=classify_reply(row['httpStatus'],body,model)
        rows.append({'scope':'case','index':row['index'],'replica':row['replica'],**classification})
    require(len(warmups)==2 and all(w['status'] in ('ok','abstain') for w in warmups),'Missing successful warmup attempts')
    for ordinal,warmup in enumerate(warmups):
        body=warmup['observation']['result'];classification=classify_reply(200,body,model)
        require(classification['executionKind']=='completed_score' and warmup['status']==body['status'],'Warmup did not complete a score')
        rows.append({'scope':'warmup','warmupOrdinal':ordinal,**classification})
    case_outcomes=dict(sorted(Counter(row['outcome'] for row in rows if row['scope']=='case').items()))
    same(case_outcomes,expected_outcomes,'Native outcome denominator changed')
    counts={}
    for scope in ('case','warmup','all'):
        selected=[row for row in rows if scope=='all' or row['scope']==scope]
        counts[scope]={'httpAttemptsObserved':len(selected),'successfulScoreRepliesObserved':sum(row['executionKind']=='completed_score' for row in selected),
            'busyRepliesObserved':sum(row['outcome']=='busy' for row in selected),'contextRejectionsObserved':sum(row['outcome']=='context_rejected' for row in selected),
            'backendScoreEntriesSourceInferred':sum(row['backendScoreEntriesSourceInferred'] for row in selected),
            'logicalForwardCompletionsSourceInferred':sum(row['logicalForwardCompletionsSourceInferred'] for row in selected)}
        require(counts[scope]['httpAttemptsObserved']==counts[scope]['successfulScoreRepliesObserved']+counts[scope]['busyRepliesObserved']+counts[scope]['contextRejectionsObserved'],
            'Execution partition does not cover every HTTP attempt')
    return {'schemaVersion':'agat.decision.native-execution-accounting-inventory.v1','originalCases':count,
        'caseOutcomes':case_outcomes,'counts':counts,'sourceContract':contract,'requests':rows,'allAttemptsRetained':True,
        'generatedDecisionTokensFromSuccessfulScores':0,'modelForwardCompletionsAreSourceInferred':True,
        'gpuKernelInvocationsMeasured':False,'gpuInferenceDurationMeasured':False,'hardwareConcurrencyEstablished':False}


def analyze_receipts(root,inputs):
    require(set(inputs)=={'context','replicated'} and set(inputs['context'])=={'path','sha256'}
        and set(inputs['replicated'])=={'evidenceDir','planFileSha256','resultFileSha256'},'Unpinned/extra execution dataset')
    context_path=private_path(root,inputs['context']['path']);context_sha=inputs['context']['sha256'];dataset=inputs['replicated']
    directory=private_path(root,dataset['evidenceDir'])
    receipt=verify_native(root,directory,context_path,context_sha=context_sha,plan_sha=dataset['planFileSha256'],result_sha=dataset['resultFileSha256'],suite=native)
    require(receipt['status']=='pass' and receipt['modelCallsDuringVerification']==0,'Original native inventory replay failed')
    context=parse_json(pinned_input(context_path,context_sha,32*1024*1024));plan=parse_json(pinned_input(directory/'plan.json',dataset['planFileSha256'],64*1024*1024))
    result=parse_json(pinned_input(directory/'result.json',dataset['resultFileSha256'],64*1024*1024))
    sources=sources_at(root,plan['sourceCommit'],plan['sourceFiles'],native.SOURCE_PATHS)
    decisions=native.journal(pinned_input(directory/'decision-http.jsonl',result['artifactSha256']['decision-http.jsonl'],32*1024*1024))
    evidence=account(context['profile']['model'],sources,decisions,result['warmup'],result['evidence']['shadowOutcomes'],len(context['inputs']))
    require(evidence['counts']['all']['httpAttemptsObserved']==receipt['nativeCalls'],'Warmup plus case counters differ from native epoch')
    return evidence,receipt


def create_analysis(root,inputs,commit,source_files):
    sources_at(root,commit,source_files,SOURCE_PATHS);evidence,receipt=analyze_receipts(root,inputs)
    return sealed({'schemaVersion':ANALYSIS_SCHEMA,'createdAt':paired.now(),'sourceCommit':commit,'sourceFiles':source_files,
        'protocol':SPEC,'inputs':inputs,'datasetReplay':receipt,'evidence':evidence,**FLAGS})


def verify_analysis(root,path,file_sha):
    analysis=verify_seal(parse_json(pinned_input(path,file_sha,64*1024*1024)),ANALYSIS_SCHEMA)
    require(set(analysis)=={'schemaVersion','sha256','createdAt','sourceCommit','sourceFiles','protocol','inputs','datasetReplay','evidence',*FLAGS},'Execution analysis schema changed')
    same(analysis['protocol'],SPEC,'Execution accounting contract changed')
    require(all(type(analysis[k]) is type(v) and analysis[k]==v for k,v in FLAGS.items()),'Unsupported execution qualification')
    require(timestamp(analysis['datasetReplay']['verifiedAt'],'native.verifiedAt')<=timestamp(analysis['createdAt'],'analysis.createdAt'),'Analysis predates its source replay')
    sources=sources_at(root,analysis['sourceCommit'],analysis['sourceFiles'],SOURCE_PATHS)
    evidence,receipt=analyze_receipts(root,analysis['inputs']);same(evidence,analysis['evidence'],'Execution counts differ from original receipts')
    verify_seal(analysis['datasetReplay'],receipt['schemaVersion'])
    same({k:v for k,v in receipt.items() if k not in ('sha256','verifiedAt')},{k:v for k,v in analysis['datasetReplay'].items() if k not in ('sha256','verifiedAt')},'Embedded native replay changed')
    return sealed({'schemaVersion':VERIFICATION_SCHEMA,'status':'pass','verifiedAt':paired.now(),'sourceCommit':analysis['sourceCommit'],
        'verifiedSourceFiles':len(sources),'analysisFileSha256':file_sha,'evidence':evidence,'modelCallsDuringVerification':0,**FLAGS})
