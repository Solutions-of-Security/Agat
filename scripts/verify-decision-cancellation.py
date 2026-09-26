#!/usr/bin/env python3
"""Verify cancellation evidence, input/profile bindings and owned-process cleanup."""

import argparse
import hashlib
import os
import sys
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from decision_runtime import VERSION,implementation_sha256
from decision_runtime.artifacts import read_json,sealed,verify_seal,write_new
from decision_runtime.contracts import Request,fingerprint
from scripts.lib.decision_performance import validate_result


def require(value,message):
    if not value:raise ValueError(message)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();artifacts={}
    def read(name,schema):
        value=verify_seal(read_json(args.evidence_dir/f'{name}.json'),f'agat.decision.{schema}.v1')
        artifacts[name]=value['sha256'];return value
    plan=read('cancellation-plan','cancellation-plan');result=read('cancellation-result','cancellation-probe')
    old_plan=read('cancellation-workflow-plan','cancellation-workflow-plan')
    old_result=read('cancellation-workflow-result','cancellation-workflow')
    workflow_plan=read('cancellation-workflow-retirement-plan','cancellation-workflow-plan')
    workflow_result=read('cancellation-workflow-retirement-result','cancellation-workflow')
    archive=read('cancellation-workflow-initial-source','source-snapshot')
    profile=plan['profile'];request=Request.from_dict(plan['request'])
    require(request.input_sha256==plan['inputSha256'],'Input binding changed')
    require(profile['runtimeVersion']==VERSION and profile['model']['implementationSha256']==implementation_sha256(),'Runtime changed')
    require(profile['model']['inferenceExecution']['deadlineMs']==10000
            and profile['model']['inferenceExecution']['cancellationAction']=='stop_process_require_restart'
            and profile['model']['inferenceExecution']['cancellationPollIntervalMs']==20,'Wrong execution mode')
    for frozen in (plan,old_plan,workflow_plan):
        require(frozen['profile']==profile and frozen['profileSha256']==fingerprint(profile),'Profile mismatch')
        require(Request.from_dict(frozen['request']).input_sha256==request.input_sha256,'Workflow input mismatch')
        require(frozen['sourcePlanSha256']==plan['sourcePlanSha256'],'Source plan mismatch')
        for name,digest in frozen['harnessFiles'].items():
            path=(ROOT/name).resolve();require(path.is_relative_to(ROOT),'Invalid harness path')
            if hashlib.sha256(path.read_bytes()).hexdigest()==digest:continue
            saved=archive['files'].get(name)
            require(frozen is old_plan and saved and saved['sha256']==digest
                    and hashlib.sha256(saved['source'].encode()).hexdigest()==digest,'Frozen harness unavailable')
    for frozen,report in ((plan,result),(old_plan,old_result),(workflow_plan,workflow_result)):
        require(report['planSha256']==frozen['sha256'],'Wrong report binding')
        require(report['routingEnabled'] is False,'Unexpected routing enablement')
    require(result['status']=='observed' and result['failure'] is None and result['allOwnedProcessesStopped'] is True,'Incomplete transport probe')
    require([r['mode'] for r in result['runs']]==['cancel','timeout','explicit_restart'],'Missing phase')
    pids=set(result['ownedProcessIds'])
    for run in result['runs']:
        runtime=run['runtime'];pids.add(runtime['parentPid']);pids.update(c['pid'] for c in runtime['children'])
        require(runtime['profile']==profile and runtime['profileSha256']==fingerprint(profile),'Run profile mismatch')
        require(runtime['ownedChildrenGone'] is True,'Unclean child process')
        if run['mode']=='explicit_restart':
            require(runtime['exitCode']==130,'Unexpected healthy shutdown')
        else:
            reason='cancelled' if run['mode']=='cancel' else 'timeout'
            require(runtime['exitCode']==75 and run['workerOutcome']=={'status':'unavailable','reason':reason},'Wrong cancellation outcome')
            require(run['workerReturnedMs']>=run['triggerMs']
                    and 0<=run['triggerToObservedExitMs']<plan['maximumObservedExitAfterTriggerMs'],'Timing bound failed')
            require(run['triggerMs']>=plan['workerCancelAfterMs'] if run['mode']=='cancel'
                    else run['triggerMs']==plan['workerTimeoutMs'],'Wrong trigger')
    require(len(result['afterExplicitRestart'])==2,'Incomplete restart probes')
    for row in [result['baseline'],*result['afterExplicitRestart']]:
        validate_result(row['result'],request,profile)
        require(row['result']['inputTokens']==plan['inputTokens']==2048,'Input truncated')
        require(all(row['result'][key]==result['baseline']['result'][key]
                    for key in ('status','reason','value','selectedOptionId','distribution')),'Restart changed decision')
    require(old_result['status']=='incomplete' and old_result['failure'] is not None,'Original incomplete probe was rewritten')
    require(workflow_result['status']=='integration_pass' and workflow_result['failure'] is None,'Workflow not complete')
    for report in (old_result,workflow_result):
        pids.add(report['managerPid']);pids.add(report['backendDiagnostics']['childPid'])
        require(report['inferenceChildGone'] is True,'Workflow left inference process')
        require(report['backendDiagnostics']['stopReason']=='inference_cancelled'
                and report['backendDiagnostics']['childExitCode'] is not None,'Wrong workflow stop reason')
        smoke=report['smoke']
        require(smoke['status']=='integration_pass' and smoke['primaryCalls']==1 and smoke['primaryRoute']=='PRIMARY_BRANCH'
                and smoke['primary']=='fixture-chat-completions','Primary route not preserved')
        require(smoke['observation']=={'fallback':'primary','mode':'shadow','reason':'timeout','status':'unavailable'},'Wrong persisted observation')
        require(smoke['profileSha256']==fingerprint(profile) and all(smoke['checks'].values()),'Workflow check/profile mismatch')
        for name,digest in smoke['integrationImplementation']['files'].items():
            require(hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest,'Integration implementation changed')
    retirement=workflow_result['retirement']
    require(retirement['retired']==workflow_result['backendDiagnostics']
            and 0<=retirement['waitMs']<=workflow_plan['retirementWaitAfterSmokeMs']+20,'Invalid bounded retirement observation')
    for pid in sorted(pids):
        try:os.kill(pid,0)
        except ProcessLookupError:continue
        raise ValueError(f'Owned PID {pid} still exists; inspect before claiming cleanup')
    report=sealed({'schemaVersion':'agat.decision.cancellation-verification.v1','createdAt':datetime.now(timezone.utc).isoformat(),
                   'status':'verified','artifacts':artifacts,'runtimeVersion':VERSION,'implementationSha256':implementation_sha256(),
                   'profileSha256':fingerprint(profile),'verifiedGonePids':sorted(pids),
                   'verifierSha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                   'checks':{'boundInputAndProfile':True,'workerCancellationAndTimeout':True,'exitBeforeServerDeadline':True,
                             'explicitRestartSameDistribution':True,'primaryRoutePreserved':True,'firstIncompleteRunPreserved':True,
                             'allRecordedOwnedPidsGone':True},
                   'limitations':['Integrity and protocol verification of small local probes; not model-quality or SLO qualification.',
                                  'The primary model in workflow probes was explicitly a fixture.']})
    write_new(args.output,report);print({k:report[k] for k in ('status','runtimeVersion','profileSha256','verifiedGonePids')})


if __name__=='__main__':main()
