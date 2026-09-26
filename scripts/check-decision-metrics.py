#!/usr/bin/env python3
"""Exercise real MLX metrics and parse every scrape with the official Prometheus parser."""

import argparse
import hashlib
import http.client
import os
import sys
import threading
import time
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from decision_runtime.artifacts import read_json,sealed,verify_seal,write_new
from decision_runtime.contracts import Policy,Request,canonical_json,fingerprint,parse_json
from decision_runtime.engine import DecisionEngine
from decision_runtime.isolated import IsolatedBackend,mlx_factory
from decision_runtime.metrics import CONTENT_TYPE,OUTCOMES
from decision_runtime.server import make_server
from scripts.lib.decision_performance import validate_result
from workers.local_decisions import LocalDecisionClient

PARSER_SHA256='fa93d06737aa02bacd05794768508bb97d2fbee28cb3bca04eaae92f0ca953d6'


def ensure(value,message):
    if not value:raise RuntimeError(message)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--policy',type=Path,required=True)
    parser.add_argument('--source-plan',type=Path,required=True)
    parser.add_argument('--parser-wheel',type=Path,required=True)
    parser.add_argument('--plan-output',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    ensure(not args.output.exists() and not args.plan_output.exists() and args.output.resolve()!=args.plan_output.resolve(),
           'Use distinct new evidence paths')
    ensure(hashlib.sha256(args.parser_wheel.read_bytes()).hexdigest()==PARSER_SHA256,'Unverified parser wheel')
    sys.path.insert(0,str(args.parser_wheel.resolve()))
    from prometheus_client.parser import text_string_to_metric_families
    source=verify_seal(read_json(args.source_plan),'agat.decision.synthetic-robustness-plan.v1')
    ensure(source.get('generator')=='agat.synthetic-behavior.v1' and source.get('labelSource')=='synthetic-authored','Wrong source plan')
    cases=[next(c for c in source['cases'] if c['request']['id']==f'robust-single-{target}-front') for target in (512,2048)]
    requests=[Request.from_dict(c['request']) for c in cases]
    ensure(all(r.input_sha256==c['inputSha256'] for r,c in zip(requests,cases)),'Input binding mismatch')
    policy=Policy.from_dict(read_json(args.policy));started=time.monotonic();stage='startup';failure=None
    snapshots=[];responses=[];long_results=[];long_thread=None;plan=None;client_outcome=None
    with IsolatedBackend(mlx_factory,{'manifest':str(args.manifest.resolve()),'max_tokens':2048,'cache_limit_mib':128},
                         timeout_ms=5000) as backend:
        engine=DecisionEngine(backend,policy)
        with make_server(engine,0) as server:
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            def call(method,path,body=None,headers=None):
                conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=6)
                try:
                    conn.request(method,path,body=body,headers={'Content-Type':'application/json',
                                 'X-Agat-Decision-Profile':fingerprint(engine.profile()),**(headers or {})})
                    response=conn.getresponse();raw=response.read(131073)
                    ensure(len(raw)<=131072 and response.getheader('Content-Length')==str(len(raw)),'Incomplete or oversized response')
                    return response.status,response.getheader('Content-Type'),raw
                finally:conn.close()
            def scrape():
                status,content,raw=call('GET','/metrics');ensure(status==200 and content==CONTENT_TYPE,'Metrics endpoint failure')
                families=list(text_string_to_metric_families(raw.decode()))
                samples=[{'name':s.name,'labels':s.labels,'value':s.value} for f in families for s in f.samples]
                keys=[(s['name'],tuple(sorted(s['labels'].items()))) for s in samples]
                ensure(len(families)==5 and len(samples)==len(set(keys))==46,'Changed/unbounded sample cardinality')
                ensure(all(r.state not in raw.decode() and r.id not in raw.decode() and r.question not in raw.decode() for r in requests),
                       'Input leaked into metrics')
                return {'raw':raw.decode(),'samples':samples,'elapsedMs':round((time.monotonic()-started)*1000,3)}
            def value(snapshot,name,**labels):
                return next(s['value'] for s in snapshot['samples'] if s['name']==name and s['labels']==labels)
            def capture(name,condition=lambda _s:True):
                deadline=time.monotonic()+2
                while True:
                    snapshot=scrape()
                    if condition(snapshot):break
                    ensure(time.monotonic()<deadline,'Expected metric state did not arrive');time.sleep(.01)
                snapshot['phase']=name;snapshots.append(snapshot);return snapshot
            def score(request):
                begin=time.monotonic();status,_,raw=call('POST','/v1/decisions',canonical_json(request.to_dict()).encode())
                result=parse_json(raw)
                if status==503:
                    # Admission rejects busy requests before parsing the body,
                    # so this response deliberately has no input binding.
                    ensure(result==engine.error('busy'),'Unexpected admission failure')
                else:validate_result(result,request,engine.profile())
                return {'httpStatus':status,'wallMs':round((time.monotonic()-begin)*1000,3),'result':result}
            try:
                files=['scripts/check-decision-metrics.py','decision_runtime/metrics.py','decision_runtime/server.py','workers/local_decisions.py']
                plan=sealed({'schemaVersion':'agat.decision.metrics-plan.v1','createdAt':datetime.now(timezone.utc).isoformat(),
                             'sourcePlanSha256':source['sha256'],'profile':engine.profile(),'profileSha256':fingerprint(engine.profile()),
                             'cases':[{'request':r.to_dict(),'inputSha256':r.input_sha256,'inputTokens':c['targetTokens']} for r,c in zip(requests,cases)],
                             'parser':{'name':'prometheus-client','version':'0.26.0','wheelSha256':PARSER_SHA256},
                             'phases':['initial','invalid','profile_mismatch','computed','busy_while_computing','computed_long','cancelled','unavailable'],
                             'expectedFinalCounters':{name:2 if name=='ok' else 1 if name in ('invalid','profile_mismatch','busy','cancelled','unavailable') else 0 for name in OUTCOMES},
                             'harnessFiles':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in files}})
                write_new(args.plan_output,plan)
                stage='initial';initial=capture(stage)
                ensure(value(initial,'agat_decision_backend_ready')==1,'Initial backend not ready')
                ensure(all(value(initial,'agat_decision_requests_total',outcome=name)==0 for name in OUTCOMES),'Counters not initially zero')
                stage='invalid';status,_,raw=call('POST','/v1/decisions',b'{');ensure(status==400,'Invalid JSON accepted')
                responses.append({'phase':stage,'httpStatus':status,'result':parse_json(raw)})
                stage='profile_mismatch';status,_,raw=call('POST','/v1/decisions',b'{}',{'X-Agat-Decision-Profile':'0'*64})
                ensure(status==409,'Profile mismatch not rejected');responses.append({'phase':stage,'httpStatus':status,'result':parse_json(raw)})
                stage='computed';responses.append({'phase':stage,**score(requests[0])})
                ensure(responses[-1]['httpStatus']==200 and responses[-1]['result']['status']=='ok','Control did not compute')
                capture(stage,lambda s:value(s,'agat_decision_requests_total',outcome='ok')==1)
                stage='busy_while_computing';long_thread=threading.Thread(target=lambda:long_results.append(score(requests[1])),daemon=True)
                long_thread.start();capture('in_progress',lambda s:value(s,'agat_decision_requests_in_progress')==1)
                busy=score(requests[0]);ensure(busy['httpStatus']==503 and busy['result']['reason']=='busy','Expected an overlapping busy response')
                responses.append({'phase':stage,**busy});capture(stage,lambda s:value(s,'agat_decision_requests_total',outcome='busy')==1)
                long_thread.join(timeout=6);ensure(not long_thread.is_alive() and len(long_results)==1,'Long request did not complete')
                ensure(long_results[0]['httpStatus']==200,'Long control failed');responses.append({'phase':'computed_long',**long_results[0]})
                capture('computed_long',lambda s:value(s,'agat_decision_requests_total',outcome='ok')==2 and value(s,'agat_decision_requests_in_progress')==0)
                stage='cancelled'
                client_outcome=LocalDecisionClient(f'http://127.0.0.1:{server.server_port}').decide({
                    'profile':'local_decision_shadow_v2','timeoutMs':100,'profileSha256':fingerprint(engine.profile()),'request':requests[1].to_dict()})
                ensure(client_outcome=={'status':'unavailable','reason':'timeout'},'Worker did not time out')
                capture(stage,lambda s:value(s,'agat_decision_requests_total',outcome='cancelled')==1 and value(s,'agat_decision_backend_ready')==0)
                ensure(call('GET','/health')[0]==503,'Health did not report unavailable')
                stage='unavailable';after=score(requests[0]);ensure(after['result']['reason']=='backend_unavailable','Unexpected post-cancellation inference')
                responses.append({'phase':stage,**after})
                final=capture(stage,lambda s:value(s,'agat_decision_requests_total',outcome='unavailable')==1)
                ensure(all(value(final,'agat_decision_requests_total',outcome=name)==count for name,count in plan['expectedFinalCounters'].items()),
                       'Final request counters mismatch')
                for cls,count in [('computed',2),('rejected',3),('failed',2)]:
                    ensure(value(final,'agat_decision_request_duration_seconds_count',**{'class':cls})==count,'Histogram count mismatch')
                    ensure(value(final,'agat_decision_request_duration_seconds_bucket',**{'class':cls,'le':'+Inf'})==count,'Histogram infinity mismatch')
                ensure(fingerprint(engine.profile())==plan['profileSha256'],'Metric scraping changed the profile')
            except Exception as exc:failure={'stage':stage,'type':type(exc).__name__}
            finally:
                if long_thread:long_thread.join(timeout=6)
                server.shutdown();thread.join(timeout=2);backend.close()
            diagnostics=backend.diagnostics()
    child_gone=False
    try:os.kill(diagnostics['childPid'],0)
    except ProcessLookupError:child_gone=True
    result=sealed({'schemaVersion':'agat.decision.metrics-probe.v1','createdAt':datetime.now(timezone.utc).isoformat(),
                   'status':'observed' if failure is None and child_gone else 'incomplete','failure':failure,
                   'planSha256':plan['sha256'] if plan else None,'qualifiedForRouting':False,'routingEnabled':False,
                   'elapsedMs':round((time.monotonic()-started)*1000,3),'snapshots':snapshots,'responses':responses,
                   'workerOutcome':client_outcome,'backendDiagnostics':diagnostics,'inferenceChildGone':child_gone,'managerPid':os.getpid(),
                   'limitations':['One bounded local synthetic workload; counters describe execution outcomes, not model correctness.',
                                  'No monitoring service or notification receiver was installed.',
                                  'Prometheus parser runs only in this diagnostic; runtime dependencies are unchanged.']})
    write_new(args.output,result);print(f"{result['status']}; failure={failure}; childGone={child_gone}",flush=True)
    return 0 if result['status']=='observed' else 1


if __name__=='__main__':raise SystemExit(main())
