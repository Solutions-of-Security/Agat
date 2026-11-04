"""Raw-pinned offline replay of accepted caller deadline during active native HTTP."""
import hashlib
import json
from pathlib import Path

from decision_runtime.artifacts import sealed,verify_seal
from decision_runtime.contracts import fields,number,parse_json
from scripts.lib.decision_public_load import validate_context
from scripts.lib.decision_public_load_verification import CONTEXT_PATHS,same,sources_at
from scripts.lib.decision_public_sources import pinned_input
from scripts.lib.decision_public_workflow import PROFILE_PATH,SOURCE_PATHS,shared_config,verify_inventory
from scripts.lib.decision_public_workflow_verification import ARTIFACTS as BASE_ARTIFACTS
from scripts.lib.decision_public_peer_cancellation_verification import verify_physical
from scripts.lib import decision_public_workflow_active_deadline as deadline
from scripts.lib.decision_shadow_pilot import require,timestamp

ARTIFACTS=BASE_ARTIFACTS|deadline.ARTIFACTS


def verify(root,directory,context_path,*,context_sha,plan_sha,result_sha):
    raw={'context':pinned_input(context_path,context_sha,32*1024*1024),'plan':pinned_input(directory/'plan.json',plan_sha,32*1024*1024),
        'result':pinned_input(directory/'result.json',result_sha,64*1024*1024)}
    context=validate_context(parse_json(raw['context'])); plan=verify_seal(parse_json(raw['plan']),deadline.LAUNCH_PLAN)
    result=verify_seal(parse_json(raw['result']),deadline.LAUNCH_RESULT)
    fields(plan,{'schemaVersion','sha256','createdAt','sourceCommit','sourceFiles','contextProfileFileSha256','context','config','runtime','manifestFileSha256',
        'profileFileSha256','mode','primary','warmupCount','callerDeadline','ownersAppointed','referenceLabels','sloAccepted','routingEnabled','qualification'})
    fields(result,{'schemaVersion','sha256','status','planSha256','evidence','physical','callerDeadline','warmup','samples','nativeRetired','nativeRecovered',
        'failure','ownedPids','remainingOwnedPids','cleanupErrors','runtimeExitCodes','driverExitCode','artifactSha256','elapsedMs','primary','referenceLabels',
        'classificationAccuracyMeasured','ownersAppointed','sloAccepted','routingEnabled','qualification'})
    for value in (plan,result):
        require(type(value['referenceLabels']) is int and value['referenceLabels']==0 and value['ownersAppointed'] is False and value['sloAccepted'] is False
            and value['routingEnabled'] is False and value['qualification']=='not_assessed' and value['primary']=='fixture_chat_completions',"Lab deadline receipt grants authority or quality")
        same(value['callerDeadline'],deadline.deadline_spec(context,plan['callerDeadline']['targetIndex'],plan['callerDeadline']['callerTimeoutMs']),"Active deadline not prospectively bound")
    require(plan['contextProfileFileSha256']==context_sha and plan['manifestFileSha256']==context['manifestFileSha256']
        and plan['profileFileSha256']==context['profileFileSha256'] and plan['mode']=='serial_closed_model_integration'
        and type(plan['warmupCount']) is int and plan['warmupCount']==4,"Wrong active native context/profile/warmup")
    same(plan['context'],context,"Embedded raw-pinned context changed"); same(plan['config'],shared_config(context),"Published configuration changed")
    same(plan['runtime'],context['tokenizerEnvironment'],"Frozen native environment changed")
    require(isinstance(result['samples'],list) and len(result['samples'])==6,"Missing native origins for the prospective plan")
    require(timestamp(context['createdAt'],'context.createdAt')<=timestamp(plan['createdAt'],'plan.createdAt')
        <timestamp(result['samples'][0]['capturedAt'],'firstOrigin.capturedAt'),"Plan was not fixed before the first native origin and scoring")
    require(result['status']=='observed' and result['failure'] is None and result['planSha256']==plan['sha256']
        and result['remainingOwnedPids']==result['cleanupErrors']==[] and result['classificationAccuracyMeasured'] is False
        and type(result['driverExitCode']) is int and result['driverExitCode']==0 and result['runtimeExitCodes']==[75,130]
        and all(type(code) is int for code in result['runtimeExitCodes']),"Active run or recorded cleanup failed")
    number(result['elapsedMs'],0,440000)
    require(isinstance(result['ownedPids'],list) and result['ownedPids']==sorted(set(result['ownedPids']))
        and all(type(pid) is int and 0<pid<2**31 for pid in result['ownedPids']),"Invalid active process ownership")
    sources_at(root,context['sourceCommit'],context['sourceFiles'],CONTEXT_PATHS)
    sources=sources_at(root,plan['sourceCommit'],plan['sourceFiles'],SOURCE_PATHS+deadline.SOURCE_PATHS)
    require(hashlib.sha256(sources[PROFILE_PATH]).hexdigest()==context['profileFileSha256'],"Historical native profile bytes differ")
    same(parse_json(sources[PROFILE_PATH]),context['profile'],"Historical native profile semantics differ")
    implementation=hashlib.sha256()
    for name in sorted(name for name in sources if Path(name).parent==Path('decision_runtime') and name.endswith('.py')):
        implementation.update(Path(name).name.encode()+b'\0'+sources[name]+b'\0')
    require(implementation.hexdigest()==context['profile']['model']['implementationSha256'],"Historical native implementation differs")
    requirements=dict(line.split('==') for line in sources['decision_runtime/requirements-mlx.txt'].decode().splitlines() if line and not line.startswith('#'))
    same(plan['runtime'],{'python':'3.13.12','machine':'arm64','packages':requirements},"Historical native dependency pins differ")
    fields(result['artifactSha256'],ARTIFACTS)
    artifacts={name:pinned_input(directory/name,result['artifactSha256'][name],16*1024*1024) for name in ARTIFACTS}
    recipe=parse_json(artifacts['workflow-plan.json']); driver=parse_json(artifacts['workflow-driver.json']); cohort=parse_json(artifacts['cohort.http.json'])
    fields(recipe,{'schemaVersion','mode','primary','processId','processVersion','projectId','startAt','scopeEndRule','config','callerDeadline',
        'profileSha256','inputs','ownersAppointed','routingEnabled','qualification','graphSha256'})
    fields(driver,{'status','primary','primaryCalls','routes','nodeVersion','ownedPids','workerExitCode','actualWindow','activeDeadlineReady','activeDeadlineDrained','activeNativeArmed','activeNativeRecovered','unauthenticatedStatus','authenticatedStatus',
        'ownersAppointed','routingEnabled','qualification'})
    fields(cohort,{'schemaVersion','snapshotId','observedAt','scope','snapshot','limits','counts','runIdsSha256','instances','traces','dataPolicy',
        'populationCoverageVerified','eligibleWorkloadVerified','httpAttemptInventoryVerified','sloAccepted','routingEnabled','qualification'})
    fields(cohort['snapshot'],{'dialect','consistency','storedCohortComplete','truncated'})
    require(cohort['snapshot']['dialect'] in {'sqlite','postgresql'} and all(cohort[k] is False for k in
        ('populationCoverageVerified','eligibleWorkloadVerified','httpAttemptInventoryVerified')),"Lab census promoted to actual workload coverage")
    same(cohort['limits'],{'instances':1000,'stages':10000,'activityBytes':16777216,'exportBytes':16777216},"Census limits differ")
    require(type(cohort['counts']['storedStages']) is int and len(context['inputs'])<=cohort['counts']['storedStages']<=10000
        and cohort['runIdsSha256']==hashlib.sha256(json.dumps([i['runId'] for i in cohort['instances']],separators=(',',':')).encode()).hexdigest(),"Raw stored-stage/run-set inventory differs")
    require(isinstance(recipe['graphSha256'],str) and len(recipe['graphSha256'])==64 and all(c in '0123456789abcdef' for c in recipe['graphSha256']),"Invalid published graph pin")
    require(timestamp(recipe['startAt'],'recipe.startAt')>timestamp(plan['createdAt'],'plan.createdAt')
        and recipe['scopeEndRule']=='after_full_input_inventory_and_worker_drain',"Recipe not prospectively scoped")
    records=[parse_json(line) for line in artifacts['workflow-routes.jsonl'].splitlines()]; same(driver['routes'],records,"Raw route journal differs")
    require(driver['status']=='observed' and driver['primary']=='fixture_chat_completions' and type(driver['primaryCalls']) is int
        and driver['primaryCalls']==len(context['inputs']) and type(driver['workerExitCode']) is int and driver['workerExitCode']==0
        and type(driver['unauthenticatedStatus']) is int and driver['unauthenticatedStatus']==401 and type(driver['authenticatedStatus']) is int
        and driver['authenticatedStatus']==200 and driver['ownersAppointed'] is False and driver['routingEnabled'] is False and driver['qualification']=='not_assessed',"Actual driver audit/auth/authority failed")
    same(driver['actualWindow'],{k:cohort['scope'][k] for k in ('startAt','endAt')},"Driver/census windows differ")
    require(isinstance(driver['ownedPids'],list) and len(driver['ownedPids'])==len(set(driver['ownedPids']))==2
        and all(type(pid) is int and pid in result['ownedPids'] for pid in driver['ownedPids']),"Single driver/worker ownership missing")
    same(recipe['callerDeadline'],plan['callerDeadline'],"Recipe retargeted active deadline")
    transport=parse_json(artifacts['caller-deadline-transport.json']); bundle=deadline.receipt_bundle(artifacts)
    require(timestamp(result['samples'][0]['capturedAt'],'firstOrigin.capturedAt')<=timestamp(transport['startedAt'],'relay.startedAt'),
        "Active relay predates the original native origin")
    evidence=verify_inventory(context,recipe,cohort,records,transport,bundle); same(result['evidence'],evidence,"Embedded durable inventory differs")
    for field,key in (('activeDeadlineReady','ready'),('activeDeadlineDrained','drained'),('activeNativeArmed','armed'),('activeNativeRecovered','recovered')): same(driver[field],bundle[key],"Driver bypassed deadline barrier: "+key)
    same(result['nativeRetired'],bundle['retired'],"Result retirement receipt differs"); same(result['nativeRecovered'],bundle['recovered'],"Result recovery receipt differs")
    require(set(bundle['retired']['nativePids'])<=set(result['ownedPids']) and not set(bundle['retired']['nativePids'])&set(driver['ownedPids'])
        and bundle['recovered']['runtimePid'] in result['ownedPids'] and bundle['recovered']['runtimePid'] not in driver['ownedPids'],"Driver/worker substituted for native runtime")
    physical=verify_physical(context,result,transport,bundle['ready'],bundle['retired'],bundle['recovered'],artifacts)
    same(physical,result['physical'],"Embedded physical origins/counters differ")
    require(timestamp(result['samples'][2]['capturedAt'],'prefixAt')<=timestamp(bundle['armed']['armedAt'],'armedAt'),"Arming predates physical prefix sample")
    require(raw=={'context':pinned_input(context_path,context_sha,32*1024*1024),'plan':pinned_input(directory/'plan.json',plan_sha,32*1024*1024),
        'result':pinned_input(directory/'result.json',result_sha,64*1024*1024)},"Consumed launch bytes changed")
    for name,expected in artifacts.items(): require(pinned_input(directory/name,result['artifactSha256'][name],16*1024*1024)==expected,"Consumed active artifact changed")
    return sealed({'schemaVersion':'agat.decision.public-workflow-verification.v7','status':'pass','contextProfileFileSha256':context_sha,'planFileSha256':plan_sha,
        'resultFileSha256':result_sha,'sourceCommit':plan['sourceCommit'],'sourceFilesCount':len(sources),'inventory':evidence,**physical,
        'physicalHttpAccounting':'one_active_target_terminal_counter_unknown','reportedCleanupComplete':True,'liveCleanupVerified':False,'verificationModelCalls':0,
        'gpuKernelPreemptionEstablished':False,'ownersAppointed':False,'referenceLabels':0,'classificationAccuracyMeasured':False,'sloAccepted':False,
        'routingEnabled':False,'qualification':'not_assessed'})
