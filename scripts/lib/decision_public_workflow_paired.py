"""Original inventory in bounded pairs; busy admission refusals preserve actual primary."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import re
import socket
import threading
import time

from decision_runtime.artifacts import sealed, verify_seal
from decision_runtime.contracts import Request, fields, number, parse_json
from decision_runtime.metrics import outcome
from scripts.lib import decision_public_workflow_cancellation as cancellation
from scripts.lib import decision_public_workflow_recovery as recovery
from scripts.lib.decision_performance import validate_result
from scripts.lib.decision_public_load_verification import same
from scripts.lib.decision_shadow_pilot import require, timestamp
from scripts.lib.decision_shadow_sli import same_json
from scripts.lib.decision_workflow_batch_clock import BATCH_CLOCK_SCHEMA, verify_batch_clock

PLAN_SCHEMA='agat.decision.public-workflow-plan.v8'
RESULT_SCHEMA='agat.decision.public-workflow-result.v8'
LAUNCH_PLAN='agat.decision.public-workflow-launch-plan.v8'
LAUNCH_RESULT='agat.decision.public-workflow-launch-result.v8'
TRANSPORT_SCHEMA='agat.decision.public-workflow-paired-transport.v1'
SOURCE_PATHS=['scripts/test/test_decision_public_workflow_paired.py']
ARTIFACTS={'paired-transport.json','paired-batches.json','coordinator-paired-http.json','primary-http.json'}


def paired_spec(context):
    require(len({r['inputSha256'] for r in context['inputs']})==len(context['inputs']),"Paired inventory requires unambiguous original fingerprints")
    states=[r['request']['state'] for r in context['inputs']]
    require(all(a not in b for i,a in enumerate(states) for j,b in enumerate(states) if i!=j),"Primary raw input cannot identify a unique original case")
    return {'kind':'original_order_batches_of_two_actual_workflows','workerConcurrency':2,'schedulerMode':'sequential','globalMaxConcurrency':2,'batchSize':2,'callerTimeoutMs':10000,
        'upstreamTimeoutMs':8000,'warmupCount':2,'retryCount':0,'restart':False,'busyHttpStatus':503,'durableBusyReturn':'unavailable/busy',
        'minimumBusyReturns':1,'requireObservedActiveBusyOverlap':True,'batchCompletionDeadlineMs':20000,'pairCreationSkewMaxMs':1000,
        'workflowDeadlineMs':240000,'metricsPollMs':20,'metricsObservationLimit':1,'primary':'fixture_chat_completions'}


def busy_result(body,profile):
    fields(body,set(profile)|{'id','mode','inputSha256','status','reason','selectedOptionId','value','distribution'})
    require(all(same_json(body[k],v) for k,v in profile.items()) and body['id'] is None and body['inputSha256'] is None
        and body['mode']=='shadow' and body['status']=='error' and body['reason']=='busy' and body['selectedOptionId'] is body['value'] is None
        and body['distribution']==[],"Busy refusal contains a request-bound model result or changed profile")


class PairedProxy:
    """Immediate relay with independently retained native error bytes; no request gate."""
    def __init__(self,upstream_port,context,spec):
        same(spec,paired_spec(context),'Unsupported prospective paired schedule')
        require(type(upstream_port) is int and 0<upstream_port<65536 and upstream_port!=8766,'Use a temporary native endpoint')
        self.upstream_port=upstream_port; self.context=context; self.spec=spec; self.lock=threading.Lock()
        self.accepted=0; self.active=0; self.maximum_active=0; self.errors=[]; self.rows=[]; self.seen=set(); self.closed=False; self.closed_at=None
        self.started_at=self.now(); self.by_sha={r['inputSha256']:(i,r) for i,r in enumerate(context['inputs'])}; owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*_): pass
            def do_GET(self):
                try:
                    require(self.path=='/health','Only health is allowed on the relay')
                    status,content_type,body,_=owner.forward('GET',self.path); owner.send(self,status,content_type,body)
                except Exception as error: owner.fail(error); self.close_connection=True
            def do_POST(self):
                with owner.lock:
                    ordinal=owner.accepted; owner.accepted+=1; owner.active+=1; owner.maximum_active=max(owner.maximum_active,owner.active)
                    active=owner.active
                try:
                    require(active<=2 and ordinal<len(context['inputs']),'Unbounded concurrent or extra native POST')
                    owner.handle(self,ordinal)
                except Exception as error: owner.fail(error); self.close_connection=True
                finally:
                    with owner.lock: owner.active-=1
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler); self.server.daemon_threads=False; self.port=self.server.server_port
        try:
            require(self.port not in (8766,upstream_port),'Relay endpoint collides with native or resident')
            self.thread=threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':.02},daemon=True); self.thread.start()
        except Exception:
            self.server.server_close(); raise

    @staticmethod
    def now(): return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
    def fail(self,error):
        with self.lock: self.errors.append(type(error).__name__)
    def forward(self,method,path,body=None,headers=None):
        c=http.client.HTTPConnection('127.0.0.1',self.upstream_port,timeout=self.spec['upstreamTimeoutMs']/1000)
        try:
            c.request(method,path,body=body,headers=headers or {}); sent_at=self.now()
            response=c.getresponse(); raw=response.read(64*1024+1)
            require(len(raw)<=64*1024,'Excessive native HTTP body'); return response.status,response.getheader('Content-Type',''),raw,sent_at
        finally: c.close()
    @staticmethod
    def send(handler,status,content_type,body):
        handler.send_response(status); handler.send_header('Content-Type',content_type); handler.send_header('Content-Length',str(len(body)))
        handler.send_header('Connection','close'); handler.end_headers(); handler.wfile.write(body); handler.wfile.flush(); handler.close_connection=True
    def handle(self,handler,ordinal):
        began=time.monotonic(); accepted=self.now()
        require(handler.path=='/v1/decisions' and not handler.headers.get('Transfer-Encoding') and len(handler.headers.get_all('Content-Length',[]))==1
            and handler.headers.get('Content-Type','').split(';')[0]=='application/json' and handler.headers.get('X-Agat-Decision-Profile')==self.context['profileSha256']
            and handler.headers.get_all('X-Agat-Decision-Cancel-On-Disconnect',[])==['1'],'Invalid paired native request')
        size=int(handler.headers['Content-Length']); require(0<size<=128*1024,'Invalid paired POST size')
        handler.connection.settimeout(8); raw=handler.rfile.read(size); require(len(raw)==size,'Incomplete original paired POST')
        request=Request.from_dict(parse_json(raw)); require(request.input_sha256 in self.by_sha,'Unknown paired original input')
        index,case=self.by_sha[request.input_sha256]
        require(request==Request.from_dict({**case['request'],'id':request.id}),'Paired input transformed')
        with self.lock:
            require(index not in self.seen,'Repeated original paired POST'); self.seen.add(index)
        status,content_type,response,sent_at=self.forward('POST',handler.path,raw,{'Content-Type':'application/json','Accept':'application/json',
            'X-Agat-Decision-Profile':self.context['profileSha256'],'X-Agat-Decision-Cancel-On-Disconnect':'1','Connection':'close'})
        upstream_ms=round((time.monotonic()-began)*1000,3); completed=self.now(); typed=parse_json(response)
        require(content_type.split(';')[0]=='application/json' and upstream_ms<8000,'Paired native response crossed profile/deadline')
        if status==503: busy_result(typed,self.context['profile'])
        else:
            validate_result(typed,request,self.context['profile']); require(status==(200 if case['contextEligible'] else 422),'Unexpected paired native status')
        body_written=True
        try: self.send(handler,status,content_type,response)
        except (BrokenPipeError,ConnectionResetError,socket.timeout):
            require(status==503,'Normal paired response was not completely delivered'); body_written=False; handler.close_connection=True
        row={'index':index,'acceptedOrdinal':ordinal,'caseId':case['id'],'stageId':request.id,'inputSha256':request.input_sha256,
            'profileSha256':self.context['profileSha256'],'requestBody':raw.decode(),'requestBodySha256':hashlib.sha256(raw).hexdigest(),
            'cancelOnDisconnect':True,'acceptedAt':accepted,'upstreamCompletedAt':completed,'finishedAt':self.now(),'upstreamMs':upstream_ms,
            'elapsedMs':round((time.monotonic()-began)*1000,3),'upstreamRequestSentAt':sent_at,'upstreamStatus':status,'upstreamContentType':content_type,
            'responseBody':response.decode(),'responseBodySha256':hashlib.sha256(response).hexdigest(),'downstreamWriteCompleted':body_written}
        with self.lock: self.rows.append(row)
    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(2)
        require(not self.thread.is_alive() and self.active==0,'Paired relay did not drain'); self.closed=True; self.closed_at=self.now()
    def receipt(self):
        require(self.closed and self.active==0,'Paired relay accounting must be closed')
        return sealed({'schemaVersion':TRANSPORT_SCHEMA,'spec':self.spec,'proxyPort':self.port,'upstreamPort':self.upstream_port,'startedAt':self.started_at,
            'closedAt':self.closed_at,'rows':sorted(self.rows,key=lambda r:r['index']),'acceptedPosts':self.accepted,'completedUpstreamPosts':len(self.rows),
            'maximumActiveHandlers':self.maximum_active,'errors':self.errors,'closed':self.closed,'activeHandlers':self.active})


def receipt_bundle(raw):
    require(ARTIFACTS<=set(raw),'Missing paired lease, primary or native accounting')
    return {'batches':parse_json(raw['paired-batches.json']),'http':parse_json(raw['coordinator-paired-http.json']),
        'primary':parse_json(raw['primary-http.json']),'rawFileSha256':{name:hashlib.sha256(raw[name]).hexdigest() for name in ARTIFACTS}}


def verify_transport(context,spec,transport,cohort,routes,bundle):
    same(spec,paired_spec(context),'Posthoc paired schedule'); verify_seal(transport,TRANSPORT_SCHEMA)
    fields(transport,{'schemaVersion','sha256','spec','proxyPort','upstreamPort','startedAt','closedAt','rows','acceptedPosts','completedUpstreamPosts','maximumActiveHandlers','errors','closed','activeHandlers'})
    same(transport['spec'],spec,'Paired relay changed prospective schedule'); count=len(context['inputs'])
    require(transport['closed'] is True and transport['errors']==[] and all(type(transport[k]) is int for k in ('acceptedPosts','completedUpstreamPosts','maximumActiveHandlers','activeHandlers'))
        and transport['activeHandlers']==0 and transport['acceptedPosts']==transport['completedUpstreamPosts']==len(transport['rows'])==count
        and transport['maximumActiveHandlers']==2,'Paired native attempts were missing, retried or serial')
    require(all(type(transport[k]) is int and 0<transport[k]<65536 and transport[k]!=8766 for k in ('proxyPort','upstreamPort'))
        and transport['proxyPort']!=transport['upstreamPort'],'Paired relay addresses protected resident')
    fields(bundle,{'batches','http','primary','rawFileSha256'}); fields(bundle['rawFileSha256'],ARTIFACTS)
    traces={t['run']['id']:t for t in cohort['traces']}; instances={i['runId']:i for i in cohort['instances']}
    physical=Counter(); busy=0; busy_context_ineligible=0
    require(sorted(row['acceptedOrdinal'] for row in transport['rows'])==list(range(count)) and all(type(row['acceptedOrdinal']) is int for row in transport['rows']),'Missing original native arrival order')
    for index,(case,route,row) in enumerate(zip(context['inputs'],routes,transport['rows'])):
        fields(row,{'index','acceptedOrdinal','caseId','stageId','inputSha256','profileSha256','requestBody','requestBodySha256','cancelOnDisconnect','acceptedAt',
            'upstreamRequestSentAt','upstreamCompletedAt','finishedAt','upstreamMs','elapsedMs','upstreamStatus','upstreamContentType','responseBody','responseBodySha256','downstreamWriteCompleted'})
        require(type(row['index']) is int and row['index']==index and row['caseId']==case['id'] and row['stageId']==route['stageId']
            and row['inputSha256']==case['inputSha256'] and row['profileSha256']==context['profileSha256'] and row['cancelOnDisconnect'] is True,'Paired input identity changed')
        require(isinstance(row['requestBody'],str) and 0<len(row['requestBody'].encode())<=128*1024
            and isinstance(row['responseBody'],str) and 0<len(row['responseBody'].encode())<=64*1024,'Invalid bounded paired HTTP bodies')
        request=Request.from_dict(parse_json(row['requestBody'])); expected=Request.from_dict({**case['request'],'id':route['stageId']})
        require(request==expected and hashlib.sha256(row['requestBody'].encode()).hexdigest()==row['requestBodySha256']
            and hashlib.sha256(row['responseBody'].encode()).hexdigest()==row['responseBodySha256'],'Paired raw request/result changed')
        require(timestamp(transport['startedAt'],'startedAt')<=timestamp(row['acceptedAt'],'acceptedAt')<=timestamp(row['upstreamRequestSentAt'],'sentAt')<=timestamp(row['upstreamCompletedAt'],'nativeAt')
            <=timestamp(row['finishedAt'],'finishedAt')<=timestamp(transport['closedAt'],'closedAt'),'Paired HTTP chronology invalid')
        require(number(row['upstreamMs'],0,8000)<=number(row['elapsedMs'],0,10001) and type(row['upstreamStatus']) is int
            and row['upstreamContentType'].split(';')[0]=='application/json' and type(row['downstreamWriteCompleted']) is bool,'Paired delivery/timing invalid')
        typed=parse_json(row['responseBody']); obs=traces[route['runId']]['decisionObservations'][0]['observation']
        if row['upstreamStatus']==503:
            busy_result(typed,context['profile']); require(obs['status']=='unavailable' and obs['reason']=='busy' and 'result' not in obs,'Native busy was not accepted as caller unavailability')
            busy+=1; busy_context_ineligible+=int(not case['contextEligible']); physical['busy']+=1
        else:
            validate_result(typed,request,context['profile']); require(row['upstreamStatus']==(200 if case['contextEligible'] else 422)
                and row['downstreamWriteCompleted'] is True and same_json(typed,obs['result']),'Normal paired native response differed from durable observation')
            physical[outcome(typed)]+=1
        require(abs(obs['callerTiming']['durationMs']-row['elapsedMs'])<=250,'Paired caller omits native HTTP work')
    require(busy>=spec['minimumBusyReturns'],'No actual native busy admission refusal')
    verify_lease_http(context,cohort,routes,bundle)
    batches=bundle['batches']; require(isinstance(batches,list) and len(batches)==(count+1)//2,'Paired batches omitted')
    previous=timestamp(cohort['scope']['startAt'],'scope.startAt'); previous_monotonic=None; clock_diagnostics=[]
    versioned=all(isinstance(batch,dict) and batch.get('schemaVersion')==BATCH_CLOCK_SCHEMA for batch in batches)
    require(versioned or all(isinstance(batch,dict) and 'schemaVersion' not in batch for batch in batches),
            'Mixed or unknown paired batch clock schemas')
    for ordinal,batch in enumerate(batches):
        if versioned:
            previous_monotonic,diagnostic=verify_batch_clock(batch,previous_monotonic); clock_diagnostics.append(diagnostic)
        else: fields(batch,{'index','inputIndices','runIds','startedAt','completedAt','elapsedMs'})
        indices=list(range(ordinal*2,min(ordinal*2+2,count))); require(type(batch['index']) is int and batch['index']==ordinal
            and batch['inputIndices']==indices and batch['runIds']==[routes[i]['runId'] for i in indices],'Original pair reordered or repeated')
        started=timestamp(batch['startedAt'],'batch.startedAt'); completed=timestamp(batch['completedAt'],'batch.completedAt')
        wall_ms=(completed-started).total_seconds()*1000
        require(previous<=started<=completed and wall_ms<=20001
            and (versioned or abs(number(batch['elapsedMs'],0,20000)-wall_ms)<=2),
            f"Pair crossed completion barrier/deadline: index={ordinal}, previous={previous.isoformat()}, "
            f"started={started.isoformat()}, completed={completed.isoformat()}, wallMs={wall_ms}, monotonicMs={batch['elapsedMs']}")
        previous=completed
        creates=[timestamp(instances[routes[i]['runId']]['createdAt'],'instance.createdAt') for i in indices]
        require((max(creates)-min(creates)).total_seconds()*1000<=1000 and all(started<=v<=completed for v in creates),'Pair creation skew exceeded prospective bound')
        require(all(started<=timestamp(transport['rows'][i]['acceptedAt'],'acceptedAt')<=timestamp(transport['rows'][i]['finishedAt'],'finishedAt')<=completed for i in indices),'Pair moved beyond original workflow boundary')
    verify_primary(context,cohort,routes,bundle['primary'],bundle['http'])
    return {'nativeBusyRefusals':busy,'durableBusyReturns':busy,'contextIneligibleInputsRejectedBusyBeforeContextCheck':busy_context_ineligible,
        'physicalScheduledOutcomes':dict(physical),'physicalScheduledPostStarts':count,'completedPhysicalScheduledPosts':count,
        'runtimeRestarted':False,'workerConcurrency':2,'workflowBatches':len(batches),'pairedPrimaryRoutesPreserved':True,
        **({'batchClockSchemaVersion':BATCH_CLOCK_SCHEMA,'batchClockSamplesVerified':True,
            'maximumClockSamplingMs':max(ms for row in clock_diagnostics for ms in row['samplingMs'])} if versioned else {})}


def verify_lease_http(context,cohort,routes,bundle):
    records=bundle['http']
    require(isinstance(records,list) and len(routes)*3<=len(records)<=2000,'Incomplete paired lease journal')
    for row in records:
        fields(row,{'method','path','startedAt','finishedAt','startedMs','finishedMs','requestBody','requestBodySha256','requestBodyComplete','httpStatus'})
        require(row['method']=='POST' and re.fullmatch(r'/api/v1/leases/[^/?#]+/(renew|decision-shadow(?:/intent)?|complete)',row['path'])
            and type(row['httpStatus']) is int and row['httpStatus']==(204 if row['path'].endswith('/renew') else 200),'Paired lease revoked or rejected')
        require(isinstance(row['requestBody'],str) and len(row['requestBody'].encode())<=128*1024
            and type(row['requestBodyComplete']) is bool and (row['requestBodyComplete'] or row['path'].endswith('/renew'))
            and hashlib.sha256(row['requestBody'].encode()).hexdigest()==row['requestBodySha256'],'Paired actual body incomplete or changed')
        require(timestamp(row['startedAt'],'startedAt')<=timestamp(row['finishedAt'],'finishedAt') and number(row['startedMs'],0,600000)<=number(row['finishedMs'],0,600000),'Paired lease timing invalid')
    groups=[[r for r in records if r['path'].endswith(suffix)] for suffix in ('/decision-shadow/intent','/decision-shadow','/complete')]
    require(all(len(g)==len(routes) for g in groups),'Paired actual POST omitted/retried')
    leases=set(); traces={t['run']['id']:t for t in cohort['traces']}
    for route in routes:
        trace=traces[route['runId']]; assigned=cancellation._single_assignment(trace,route['stageId'])
        intent=[r for r in groups[0] if parse_json(r['requestBody']).get('assignmentId')==assigned['assignmentId']]
        require(len(intent)==1,'Paired actual intent missing'); fields(parse_json(intent[0]['requestBody']),{'schemaVersion','assignmentId'})
        require(parse_json(intent[0]['requestBody'])['schemaVersion']=='agat.decision.caller-accounting.v1','Wrong paired intent schema')
        lease=intent[0]['path'].removesuffix('/decision-shadow/intent'); require(lease not in leases,'Paired lease reused'); leases.add(lease)
        returned=[r for r in groups[1] if r['path']==lease+'/decision-shadow']; completed=[r for r in groups[2] if r['path']==lease+'/complete']
        require(len(returned)==len(completed)==1 and parse_json(completed[0]['requestBody'])['output']=='PRIMARY_OUTPUT','Paired actual return/primary missing')
        obs=trace['decisionObservations'][0]['observation']; body=parse_json(returned[0]['requestBody'])
        fields(body,{'status','reason','callerTiming'} if obs['status']=='unavailable' else {'result','callerTiming'})
        require(same_json(body,{k:obs[k] for k in body}) and timestamp(intent[0]['finishedAt'],'intentAt')<=timestamp(returned[0]['startedAt'],'returnAt')
            and timestamp(returned[0]['finishedAt'],'returnedAt')<=timestamp(completed[0]['startedAt'],'primaryAt'),'Paired actual return differs or follows primary')
    require(all(r['path'].removesuffix('/renew') in leases for r in records if r['path'].endswith('/renew')),'Foreign paired lease renewal')


def verify_primary(context,cohort,routes,records,http_records):
    require(isinstance(records,list) and len(records)==len(routes),'Actual primary calls omitted/retried')
    seen=set()
    for row in records:
        fields(row,{'index','caseId','requestBody','requestBodySha256','startedAt','completedAt','httpStatus','responseBody','responseBodySha256'})
        require(type(row['index']) is int and 0<=row['index']<len(routes) and row['index'] not in seen,'Primary input repeated'); seen.add(row['index'])
        case=context['inputs'][row['index']]; require(row['caseId']==case['id'] and type(row['httpStatus']) is int and row['httpStatus']==200,'Primary bound to another case')
        for key,limit in (('request',128*1024),('response',64*1024)):
            require(isinstance(row[key+'Body'],str) and 0<len(row[key+'Body'].encode())<=limit
                and hashlib.sha256(row[key+'Body'].encode()).hexdigest()==row[key+'BodySha256'],'Actual primary body changed')
        body=parse_json(row['requestBody']); messages=body['messages']
        matches=[i for i,c in enumerate(context['inputs']) if any(m.get('role')=='user' and isinstance(m.get('content'),str)
            and c['request']['state'] in m['content'] for m in messages)]
        require(matches==[row['index']],'Primary did not receive a unique original task input')
        response=parse_json(row['responseBody']); require(response['choices'][0]['message']['content']=='PRIMARY_OUTPUT'
            and timestamp(row['startedAt'],'startedAt')<=timestamp(row['completedAt'],'completedAt'),'Primary response or time changed')
        route=routes[row['index']]; trace=next(t for t in cohort['traces'] if t['run']['id']==route['runId'])
        assignment=cancellation._single_assignment(trace,route['stageId'])['assignmentId']
        intent=next(r for r in http_records if r['path'].endswith('/decision-shadow/intent') and parse_json(r['requestBody'])['assignmentId']==assignment)
        lease=intent['path'].removesuffix('/decision-shadow/intent')
        returned=next(r for r in http_records if r['path']==lease+'/decision-shadow')
        complete=next(r for r in http_records if r['path']==lease+'/complete')
        # The existing worker performs primary first, then the observational shadow call.
        instance=next(i for i in cohort['instances'] if i['runId']==route['runId'])
        require(timestamp(instance['createdAt'],'instance.createdAt')<=timestamp(row['startedAt'],'primary.startedAt')
            <=timestamp(row['completedAt'],'primary.completedAt')<=timestamp(intent['startedAt'],'intentAt')
            and timestamp(returned['finishedAt'],'returnedAt')<=timestamp(complete['startedAt'],'completeAt'),
            'Actual primary did not precede observational shadow intent or stage completion preceded its return')


def verify_overlap(context,result,transport):
    sample=fields(result['activeBusySample'],{'capturedAt','metricsRaw','metricsSha256','runtimePid'})
    require(hashlib.sha256(sample['metricsRaw'].encode()).hexdigest()==sample['metricsSha256'],'Active busy raw metrics changed')
    counts,epoch=recovery.http_metrics(sample['metricsRaw'],in_progress=1); at=timestamp(sample['capturedAt'],'capturedAt')
    require(counts['busy']>=1 and epoch==result['samples'][0]['serverStart'] and type(sample['runtimePid']) is int
        and sample['runtimePid'] in result['ownedPids'],'Active busy sample lacks original epoch/refusal')
    overlaps=[]
    for busy in transport['rows']:
        if busy['upstreamStatus']!=503: continue
        partners=[r for r in transport['rows'] if r['index']//2==busy['index']//2 and r['index']!=busy['index'] and r['upstreamStatus']==200]
        for admitted in partners:
            if timestamp(admitted['upstreamRequestSentAt'],'sentAt')<=timestamp(busy['upstreamCompletedAt'],'busyAt')<=at<=timestamp(admitted['upstreamCompletedAt'],'admittedAt'):
                overlaps.append([admitted['index'],busy['index']])
    require(overlaps,'Busy raw sample did not cross an admitted partner in its original pair')
    return {'activeBusyOverlapPairs':overlaps,'activeBusySampleEpoch':epoch,'busyAdmissionOccursBeforeNativeInputParsing':True}
