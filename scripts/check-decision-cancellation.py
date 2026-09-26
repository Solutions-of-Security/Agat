#!/usr/bin/env python3
"""Bounded real-MLX worker cancellation, process cleanup and explicit restart probe."""

import argparse
import hashlib
import importlib.util
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from decision_runtime.artifacts import read_json,sealed,verify_seal,write_new
from decision_runtime.contracts import Request,fingerprint
from scripts.lib.decision_performance import validate_result
from workers.local_decisions import LocalDecisionClient

spec=importlib.util.spec_from_file_location('service_probe',ROOT/'scripts/check-decision-service-recovery.py')
service=importlib.util.module_from_spec(spec);spec.loader.exec_module(service)
ensure=service.ensure


def healthy_call(runtime,request):
    shadow={'profile':'local_decision_shadow_v2','profileSha256':fingerprint(runtime.profile),
            'request':request.to_dict(),'timeoutMs':10000}
    begin=time.monotonic()
    envelope=LocalDecisionClient(f'http://127.0.0.1:{runtime.port}').decide(shadow)
    ensure(set(envelope)=={'result'},'Expected a complete worker response')
    validate_result(envelope['result'],request,runtime.profile)
    ensure(envelope['result']['status'] in ('ok','abstain'),'Healthy inference failed')
    return {'wallMs':round((time.monotonic()-begin)*1000,3),'result':envelope['result']}


def cancellation_call(runtime,request,mode):
    ensure(mode in ('cancel','timeout'),'Invalid probe mode')
    shadow={'profile':'local_decision_shadow_v2','profileSha256':fingerprint(runtime.profile),
            'request':request.to_dict(),'timeoutMs':100 if mode=='timeout' else 10000}
    client=LocalDecisionClient(f'http://127.0.0.1:{runtime.port}')
    cancelled=threading.Event();envelopes=[];started=threading.Event()
    timestamps={}
    def work():
        timestamps['started']=time.monotonic();started.set()
        envelopes.append(client.decide(shadow,cancelled))
        timestamps['returned']=time.monotonic()
    thread=threading.Thread(target=work,name='cancellation-probe-client',daemon=True)
    thread.start()
    try:
        ensure(started.wait(timeout=1),'Worker did not start')
        if mode=='cancel':
            # This delay is part of the frozen plan, not selected from output.
            time.sleep(.15);timestamps['triggered']=time.monotonic();cancelled.set()
        thread.join(timeout=2)
        ensure(not thread.is_alive(),'Worker did not return within probe bound')
        expected={'status':'unavailable','reason':'timeout' if mode=='timeout' else 'cancelled'}
        ensure(envelopes==[expected],'Unexpected worker outcome')
        runtime.failed_exit()
        exited=time.monotonic()
        trigger=timestamps.get('triggered',timestamps['started']+.1)
        ensure(exited-trigger<2,'Process exit exceeded the frozen functional bound')
        return {'mode':mode,'timeoutMs':shadow['timeoutMs'],'cancelAfterMs':150 if mode=='cancel' else None,
                'triggerMs':round((trigger-timestamps['started'])*1000,3),
                'workerReturnedMs':round((timestamps['returned']-timestamps['started'])*1000,3),
                'triggerToObservedExitMs':round((exited-trigger)*1000,3),'workerOutcome':envelopes[0],
                'runtime':runtime.record}
    finally:
        cancelled.set();thread.join(timeout=2)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python',type=Path,default=ROOT/'.venv/decision/bin/python')
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--policy',type=Path,required=True)
    parser.add_argument('--source-plan',type=Path,required=True)
    parser.add_argument('--plan-output',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    ensure(not args.output.exists() and not args.plan_output.exists() and args.output.resolve()!=args.plan_output.resolve(),
           'Use two distinct new output files')
    source=verify_seal(read_json(args.source_plan),'agat.decision.synthetic-robustness-plan.v1')
    ensure(source.get('generator')=='agat.synthetic-behavior.v1' and source.get('labelSource')=='synthetic-authored',
           'Only the existing synthetic plan is supported')
    case=next(c for c in source['cases'] if c['request']['id']=='robust-single-2048-front')
    request=Request.from_dict(case['request'])
    ensure(request.input_sha256==case['inputSha256'] and case['targetTokens']==2048,'Invalid source binding')
    owned=[];runs=[];failure=None;baseline=None;restored=[];plan=None;stage='startup';created=datetime.now(timezone.utc).isoformat()
    started=time.monotonic()
    with tempfile.TemporaryDirectory(prefix='agat-cancellation-') as temporary:
        try:
            first=service.OwnedRuntime(args,Path(temporary),'worker_cancel',10000);owned.append(first)
            files=['scripts/check-decision-cancellation.py','scripts/check-decision-service-recovery.py','workers/local_decisions.py']
            plan=sealed({'schemaVersion':'agat.decision.cancellation-plan.v1','createdAt':created,
                         'sourcePlanSha256':source['sha256'],'request':request.to_dict(),'inputSha256':request.input_sha256,
                         'inputTokens':2048,'profile':first.profile,'profileSha256':fingerprint(first.profile),
                         'phases':['healthy_call','worker_cancel','worker_timeout','restart_two_healthy_calls'],
                         'inferenceDeadlineMs':10000,'workerCancelAfterMs':150,'workerTimeoutMs':100,
                         'maximumObservedExitAfterTriggerMs':2000,'clientOptInHeader':'X-Agat-Decision-Cancel-On-Disconnect: 1',
                         'restart':'explicit owned foreground processes, no retry of cancelled work',
                         'harnessFiles':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in files}})
            write_new(args.plan_output,plan)
            stage='baseline';baseline=healthy_call(first,request)
            ensure(baseline['result']['inputTokens']==2048,'Full input not scored')
            stage='worker_cancel';runs.append(cancellation_call(first,request,'cancel'))
            print('Worker cancellation: exit 75 and all owned children gone',flush=True)
            stage='timeout_startup';second=service.OwnedRuntime(args,Path(temporary),'worker_timeout',10000,first.port);owned.append(second)
            ensure(second.profile==first.profile,'Profile changed on second process')
            stage='worker_timeout';runs.append(cancellation_call(second,request,'timeout'))
            print('Worker 100 ms timeout: inference process stopped before its 10 second deadline',flush=True)
            stage='restart';third=service.OwnedRuntime(args,Path(temporary),'explicit_restart',10000,first.port);owned.append(third)
            ensure(third.profile==first.profile,'Profile changed after restart')
            for _ in range(2):
                response=healthy_call(third,request);restored.append(response)
                ensure(all(response['result'][key]==baseline['result'][key]
                           for key in ('status','reason','value','selectedOptionId','distribution','inputTokens')),
                       'Decision changed after restart')
                status,health=third.call('GET','/health');ensure(status==200 and health['status']=='ready','Completed connection cancelled later work')
            third.close();third.record.update(exitCode=third.process.returncode,ownedChildrenGone=service.gone([c['pid'] for c in third.children]))
            ensure(third.record['ownedChildrenGone'],'Restarted runtime left children')
            runs.append({'mode':'explicit_restart','runtime':third.record})
        except Exception as exc:
            failure={'stage':stage,'type':type(exc).__name__}
        finally:
            for runtime in owned:runtime.close()
            cleaned=all(r.process.poll() is not None for r in owned) and service.gone([c['pid'] for r in owned for c in r.children])
    report=sealed({'schemaVersion':'agat.decision.cancellation-probe.v1','createdAt':created,
                   'status':'observed' if failure is None and cleaned else 'incomplete',
                   'planSha256':plan['sha256'] if plan else None,'failure':failure,'qualifiedForRouting':False,
                   'routingEnabled':False,'elapsedMs':round((time.monotonic()-started)*1000,3),
                   'baseline':baseline,'runs':runs,'afterExplicitRestart':restored,
                   'allOwnedProcessesStopped':cleaned,'ownedProcessIds':[r.process.pid for r in owned],
                   'limitations':['One synthetic long input and three owned foreground services; not production load or a latency SLO.',
                                  'The cancellation delays are functional probe bounds, not a measured user-facing guarantee.',
                                  'No native service manager is installed and no cancelled request is retried.',
                                  'Primary workflow routing is not exercised by this standalone worker transport probe.']})
    write_new(args.output,report)
    print(f"{report['status']}; cleanup={cleaned}; failure={failure}; {args.output}",flush=True)
    return 0 if report['status']=='observed' else 1


if __name__=='__main__':raise SystemExit(main())
