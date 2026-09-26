#!/usr/bin/env python3
"""Run actual worker/coordinator fallback after a 100 ms MLX client timeout."""

import argparse
import hashlib
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from decision_runtime.artifacts import read_json,sealed,verify_seal,write_new
from decision_runtime.contracts import Policy,Request,fingerprint
from decision_runtime.engine import DecisionEngine
from decision_runtime.isolated import IsolatedBackend,mlx_factory
from decision_runtime.server import make_server


def ensure(value,message):
    if not value:raise RuntimeError(message)


def wait_for_retirement(backend, timeout_s=1.5):
    started=time.monotonic();initial=backend.diagnostics();current=initial
    while current['childExitCode'] is None and time.monotonic()-started<timeout_s:
        time.sleep(.01);current=backend.diagnostics()
    ensure(not current['available'] and current['stopReason']=='inference_cancelled',
           'Inference was not cancelled after the worker disconnected')
    ensure(current['childExitCode'] is not None,'Inference process was not reaped within the probe bound')
    return {'initial':initial,'retired':current,'waitMs':round((time.monotonic()-started)*1000,3)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--policy',type=Path,required=True)
    parser.add_argument('--source-plan',type=Path,required=True)
    parser.add_argument('--plan-output',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    ensure(not args.output.exists() and not args.plan_output.exists() and args.output.resolve()!=args.plan_output.resolve(),
           'Use two distinct new evidence files')
    source=verify_seal(read_json(args.source_plan),'agat.decision.synthetic-robustness-plan.v1')
    ensure(source.get('generator')=='agat.synthetic-behavior.v1' and source.get('labelSource')=='synthetic-authored',
           'Only authored synthetic inputs are supported')
    case=next(c for c in source['cases'] if c['request']['id']=='robust-single-2048-front')
    request=Request.from_dict(case['request']);ensure(request.input_sha256==case['inputSha256'],'Source binding mismatch')
    policy=Policy.from_dict(read_json(args.policy));started=time.monotonic();failure=None;smoke=None;plan=None;diagnostics=None;retirement=None
    with IsolatedBackend(mlx_factory,{'manifest':str(args.manifest.resolve()),'max_tokens':2048,'cache_limit_mib':128},
                         timeout_ms=10000) as backend:
        engine=DecisionEngine(backend,policy)
        with make_server(engine,0) as server:
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            with tempfile.TemporaryDirectory(prefix='agat-cancellation-workflow-') as temporary:
                tmp=Path(temporary);input_path=tmp/'request.json';output_path=tmp/'smoke.json'
                write_new(input_path,request.to_dict())
                files=['scripts/check-decision-cancellation-workflow.py','scripts/qualify-decision-shadow.ts',
                       'scripts/lib/decision-shadow-smoke.ts','workers/local_decisions.py']
                try:
                    plan=sealed({'schemaVersion':'agat.decision.cancellation-workflow-plan.v1',
                                 'createdAt':datetime.now(timezone.utc).isoformat(),'sourcePlanSha256':source['sha256'],
                                 'inputSha256':request.input_sha256,'request':request.to_dict(),
                                 'profile':engine.profile(),'profileSha256':fingerprint(engine.profile()),
                                 'workerTimeoutMs':100,'serverInferenceDeadlineMs':10000,
                                 'retirementWaitAfterSmokeMs':1500,
                                 'primary':'fixture-chat-completions','expectedPrimaryRoute':'PRIMARY_BRANCH',
                                 'expectedObservation':{'status':'unavailable','reason':'timeout','fallback':'primary'},
                                 'harnessFiles':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in files}})
                    write_new(args.plan_output,plan)
                    process=subprocess.Popen(['node','--import','tsx','scripts/qualify-decision-shadow.ts',
                                              '--url',f'http://127.0.0.1:{server.server_port}',
                                              '--request',str(input_path),'--output',str(output_path),'--expect-worker-timeout'],
                                             cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
                    try:
                        process.communicate(timeout=65)
                        ensure(process.returncode==0,'Worker/coordinator smoke failed')
                    finally:
                        if process.poll() is None:
                            os.killpg(process.pid,signal.SIGTERM)
                            try:process.communicate(timeout=2)
                            except subprocess.TimeoutExpired:
                                os.killpg(process.pid,signal.SIGKILL);process.communicate(timeout=2)
                    smoke=read_json(output_path)
                    ensure(smoke['status']=='integration_pass' and smoke['primaryCalls']==1,'Unexpected smoke result')
                    ensure(smoke['profileSha256']==plan['profileSha256'],'Smoke profile mismatch')
                    retirement=wait_for_retirement(backend)
                except Exception as exc:
                    failure={'type':type(exc).__name__}
                finally:
                    server.shutdown();thread.join(timeout=2)
                    backend.close();diagnostics=backend.diagnostics()
    child_gone=False
    try:os.kill(diagnostics['childPid'],0)
    except ProcessLookupError:child_gone=True
    result=sealed({'schemaVersion':'agat.decision.cancellation-workflow.v1','createdAt':datetime.now(timezone.utc).isoformat(),
                   'status':'integration_pass' if failure is None and child_gone else 'incomplete','failure':failure,
                   'qualification':'not_assessed','routingEnabled':False,'planSha256':plan['sha256'] if plan else None,
                   'elapsedMs':round((time.monotonic()-started)*1000,3),'smoke':smoke,'backendDiagnostics':diagnostics,
                   'retirement':retirement,
                   'inferenceChildGone':child_gone,'managerPid':os.getpid(),
                   'limitations':['Primary chat-completions endpoint is an explicit fixture; decision MLX, worker and coordinator are real.',
                                  'One client timeout and one synthetic state do not qualify model quality, production SLO or restart policy.']})
    write_new(args.output,result)
    print(f"{result['status']}; inferenceChildGone={child_gone}; failure={failure}",flush=True)
    return 0 if result['status']=='integration_pass' else 1


if __name__=='__main__':raise SystemExit(main())
