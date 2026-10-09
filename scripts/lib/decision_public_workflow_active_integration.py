"""Actual coordinator cancellation before any native response, preserving unknown return."""
from collections import Counter
import hashlib
import os
import threading
import time

from decision_runtime.artifacts import sealed,verify_seal
from decision_runtime.contracts import Request,fields,fingerprint,number,parse_json
from decision_runtime.metrics import outcome
from scripts.lib import decision_public_workflow_active_cancellation as active
from scripts.lib import decision_public_workflow_cancellation as cancellation
from scripts.lib.decision_performance import validate_result
from scripts.lib.decision_public_load_verification import same
from scripts.lib.decision_public_sources import write_json_new
from scripts.lib.decision_shadow_pilot import require,timestamp
from scripts.lib.decision_shadow_sli import same_json

TRIGGER={"kind":"authenticated_coordinator_run_cancel","cancelRequestDeadlineMs":5000,"cancelledDurableReturn":"unknown"}
SOURCE_PATHS=["scripts/run-public-support-active-cancellation.py","scripts/test/test_decision_public_workflow_active_integration.py"]
ARTIFACTS=cancellation.ARTIFACTS|{"native-retired.json","native-recovered.json","runtime-recovered.log","recovery-warmup.json",
    "active-native-arm-request.json","active-native-armed.json","coordinator-cancellation-prepared.json"}
ARM_REQUEST_SCHEMA="agat.decision.public-workflow-active-native-arm-request.v1"
ARMED_SCHEMA="agat.decision.public-workflow-active-native-armed.v1"
PREPARED_SCHEMA="agat.decision.public-workflow-active-cancellation-prepared.v1"


class PreparedActiveCancellationProxy(active.ActiveCancellationProxy):
    """Continue selecting both peers while preparation precedes the fresh active snapshot."""
    def __init__(self,*args,**kwargs):
        self.prepared=threading.Event()
        super().__init__(*args,**kwargs)

    def _snapshot(self):
        return super()._snapshot() if self.prepared.is_set() else None


def validate_preparation(context,spec,prepared,before_raw):
    fields(prepared,{'schemaVersion','targetIndex','caseId','runId','stageId','inputSha256','profileSha256',
        'beforeTraceFileSha256','unauthenticatedStatus','preparedAt'})
    case=context['inputs'][spec['targetIndex']]
    require(prepared['schemaVersion']==PREPARED_SCHEMA and type(prepared['targetIndex']) is int and prepared['targetIndex']==spec['targetIndex']
        and prepared['caseId']==case['id'] and prepared['inputSha256']==case['inputSha256'] and prepared['profileSha256']==context['profileSha256']
        and type(prepared['unauthenticatedStatus']) is int and prepared['unauthenticatedStatus']==401
        and hashlib.sha256(before_raw).hexdigest()==prepared['beforeTraceFileSha256'],"Active cancellation preparation changed")
    before=parse_json(before_raw)
    require(before['run']['id']==prepared['runId'] and before['run']['status']=='running' and before['decisionObservations']==[],"Prepared target was not pending")
    assignment=cancellation._single_assignment(before,prepared['stageId'])
    require(assignment['intent'] is True and assignment['negotiated'] is True and assignment['returned'] is None
        and assignment['outcome']=='intent_pending',"Preparation lacks the actual authorized pending assignment")
    timestamp(prepared['preparedAt'],'preparedAt')
    return prepared


def publish_barrier(directory,name,value):
    """Publish completed bytes without exposing partial JSON or replacing an existing receipt."""
    require(name in {'active-native-armed.json','active-native-ready.json','active-native-drained.json','coordinator-cancellation-ready.json','coordinator-cancellation-drained.json',
        'native-retired.json','native-recovered.json'},"Unknown active barrier filename")
    pending=directory/(name[:-5]+'.pending.json')
    write_json_new(pending,value)
    try: os.link(pending,directory/name,follow_symlinks=False)
    finally: pending.unlink()


def await_native_cleanup(pids,inspect,*,deadline,clock=time.monotonic,sleep=time.sleep):
    """Parent exit can precede an owned resource tracker; wait only inside the original budget."""
    require(isinstance(pids,list) and pids==sorted(set(pids)) and pids
        and all(type(pid) is int and 0<pid<2**31 for pid in pids),"Invalid owned native cleanup inventory")
    while True:
        remaining=inspect(set(pids))
        require(isinstance(remaining,list) and all(type(pid) is int and pid in pids for pid in remaining),"Cleanup inspector returned a foreign process")
        if not remaining: return []
        budget=deadline-clock()
        require(budget>0,"Owned native processes exceeded retirement cleanup deadline")
        sleep(min(.01,budget))


def arm_request(context,spec,request,routes):
    fields(request,{"schemaVersion","targetIndex","afterCaseId","afterRunId","createdAt"})
    require(request["schemaVersion"]==ARM_REQUEST_SCHEMA and type(request["targetIndex"]) is int
        and request["targetIndex"]==spec["targetIndex"] and len(routes)==spec["targetIndex"]
        and [r["caseId"] for r in routes]==[c["id"] for c in context["inputs"][:spec["targetIndex"]]]
        and request["afterCaseId"]==routes[-1]["caseId"] and request["afterRunId"]==routes[-1]["runId"],"Active arm request does not bind original completed prefix")
    timestamp(request["createdAt"],"arm.createdAt")


def armed_receipt(spec,request_sha,runtime_pid,server_start,armed_at):
    require(type(runtime_pid) is int and 0<runtime_pid<2**31 and isinstance(request_sha,str) and len(request_sha)==64
        and all(c in '0123456789abcdef' for c in request_sha),"Invalid native arm ownership or raw pin")
    number(server_start,0,86400000000); timestamp(armed_at,"armedAt")
    return sealed({"schemaVersion":ARMED_SCHEMA,"targetIndex":spec["targetIndex"],"requestFileSha256":request_sha,
        "runtimePid":runtime_pid,"serverStartText":str(server_start),"armedAt":armed_at})


def receipt_bundle(raw):
    require(ARTIFACTS|{"runtime.log"}<=set(raw),"Missing active workflow receipts")
    value=cancellation.receipt_bundle(raw)
    value.update(retired=parse_json(raw['native-retired.json']),recovered=parse_json(raw['native-recovered.json']),
        armed=parse_json(raw['active-native-armed.json']),armRequest=parse_json(raw['active-native-arm-request.json']),
        prepared=parse_json(raw['coordinator-cancellation-prepared.json']),beforeRaw=raw['coordinator-cancellation-before.http.json'],
        runtimeLog=raw['runtime.log'],recoveryWarmup=raw['recovery-warmup.json'])
    value['rawFileSha256']={name:hashlib.sha256(raw[name]).hexdigest() for name in ARTIFACTS|{'runtime.log'}}
    return value


def verify_transport(context,spec,transport,cohort,routes,bundle,*,spec_factory=active.active_spec,
                     transport_schema=active.TRANSPORT_SCHEMA,boundary_check=None):
    same(spec,spec_factory(context,spec['targetIndex']),"Posthoc active boundary")
    transport=verify_seal(transport,transport_schema)
    fields(transport,{"schemaVersion","sha256","spec","proxyPort","upstreamPort","startedAt","closedAt","rows","acceptedPosts",
        "completedUpstreamPosts","interruptedActiveUpstreamPosts","errors","closed","activeHandlers"})
    same(transport['spec'],spec,"Active relay changed its bound specification")
    count=len(context['inputs']); target=spec['targetIndex']; delivered=Counter(); traces={t['run']['id']:t for t in cohort['traces']}
    require(transport['closed'] is True and transport['errors']==[] and all(type(transport[k]) is int for k in
        ('acceptedPosts','completedUpstreamPosts','interruptedActiveUpstreamPosts','activeHandlers'))
        and transport['acceptedPosts']==len(transport['rows'])==count and transport['completedUpstreamPosts']==count-1
        and transport['interruptedActiveUpstreamPosts']==1 and transport['activeHandlers']==0,"Incomplete active relay inventory")
    ports=[transport[k] for k in ('proxyPort','upstreamPort')]
    require(all(type(p) is int and 0<p<65536 and p!=8766 for p in ports) and ports[0]!=ports[1],"Active relay targets protected resident")
    last=timestamp(transport['startedAt'],'relay.startedAt')
    for index,(case,route,row) in enumerate(zip(context['inputs'],routes,transport['rows'])):
        require(type(row['index']) is int and row['index']==index and row['caseId']==route['caseId']==case['id']
            and row['stageId']==route['stageId'] and row['inputSha256']==case['inputSha256']
            and row['profileSha256']==context['profileSha256'] and row['cancelOnDisconnect'] is True,"Original active request identity changed")
        raw=row['requestBody'].encode(); request=Request.from_dict(parse_json(raw)); expected=Request.from_dict({**case['request'],'id':route['stageId']})
        require(0<len(raw)<=128*1024 and hashlib.sha256(raw).hexdigest()==row['requestBodySha256'] and request==expected,"Native request transformed or truncated")
        require(last<=timestamp(row['acceptedAt'],'acceptedAt')<=timestamp(row['finishedAt'],'finishedAt')<=timestamp(transport['closedAt'],'closedAt'),"Active requests overlap or are reordered")
        last=timestamp(row['finishedAt'],'finishedAt'); number(row['elapsedMs'],0,10250)
        if index==target:
            fields(row,{"index","caseId","stageId","inputSha256","requestBody","requestBodySha256","profileSha256","cancelOnDisconnect","acceptedAt",
                "upstreamRequestSentAt","clientEofObservedAt","upstreamShutdownAt","finishedAt","elapsedMs","clientEofObserved","upstreamShutdownApplied",
                "upstreamResponseBytesObserved","responseBytesWritten","downstreamWriteCompleted","upstreamCompletedNormally"})
            require(row['clientEofObserved'] is True and row['upstreamShutdownApplied'] is True and row['downstreamWriteCompleted'] is False
                and row['upstreamCompletedNormally'] is False and type(row['upstreamResponseBytesObserved']) is type(row['responseBytesWritten']) is int
                and row['upstreamResponseBytesObserved']==row['responseBytesWritten']==0,"Active target has completed work or response bytes")
            continue
        fields(row,{"index","caseId","stageId","inputSha256","requestBody","requestBodySha256","profileSha256","cancelOnDisconnect","acceptedAt",
            "upstreamCompletedAt","finishedAt","upstreamMs","elapsedMs","upstreamStatus","upstreamContentType","responseBody","responseBodySha256",
            "withheld","downstreamWriteCompleted","responseBytesWritten","clientEofObserved"})
        raw=row['responseBody'].encode(); typed=parse_json(raw); validate_result(typed,request,context['profile'])
        stored=traces[route['runId']]['decisionObservations'][0]['observation']
        require(0<len(raw)<=64*1024 and hashlib.sha256(raw).hexdigest()==row['responseBodySha256'] and same_json(typed,stored['result'])
            and type(row['upstreamStatus']) is int and row['upstreamStatus']==(200 if case['contextEligible'] else 422)
            and row['upstreamContentType'].split(';')[0]=='application/json' and row['withheld'] is False and row['clientEofObserved'] is False
            and row['downstreamWriteCompleted'] is True and type(row['responseBytesWritten']) is int and row['responseBytesWritten']==len(raw),"Actual delivered native response differs")
        require(timestamp(row['acceptedAt'],'acceptedAt')<=timestamp(row['upstreamCompletedAt'],'upstreamCompletedAt')<=timestamp(row['finishedAt'],'finishedAt')
            and number(row['upstreamMs'],0,spec['upstreamTimeoutMs'])>=typed['durationMs']-.1
            and abs(stored['callerTiming']['durationMs']-row['elapsedMs'])<=250,"Healthy active-workflow timing incomplete")
        delivered[outcome(typed)]+=1
    boundary=verify_boundary if boundary_check is None else boundary_check
    return {**boundary(context,spec,transport,cohort,routes,bundle),'physicalScheduledOutcomes':dict(delivered),
        'physicalScheduledPostStarts':count,'completedPhysicalScheduledPosts':count-1,'interruptedActiveUpstreamPosts':1,
        'targetTypedResult':None,'targetTerminalCounterUnknown':True,'runtimeRestarted':True}


def verify_boundary(context,spec,transport,cohort,routes,bundle):
    fields(bundle,{'ready','drained','applied','http','before','beforeRaw','rawFileSha256','retired','recovered','armed','armRequest','prepared','runtimeLog','recoveryWarmup'})
    fields(bundle['rawFileSha256'],ARTIFACTS|{'runtime.log'})
    target=spec['targetIndex']; row=transport['rows'][target]; route=routes[target]
    ready=verify_seal(bundle['ready'],active.READY_SCHEMA); drained=verify_seal(bundle['drained'],active.DRAIN_SCHEMA)
    prepared=validate_preparation(context,spec,bundle['prepared'],bundle['beforeRaw'])
    require(all(prepared[k]==route[k] for k in ('caseId','runId','stageId','inputSha256'))
        and timestamp(prepared['preparedAt'],'preparedAt')<=timestamp(ready['activeObservedAt'],'activeAt'),"Active snapshot preceded original target preparation")
    same(ready,active.ready_receipt(row,{'capturedAt':ready['activeObservedAt'],'metricsRaw':ready['activeMetricsRaw']}),'Active ready proof changed')
    same(drained,active.drain_receipt(row),'Active target EOF proof changed')
    applied=fields(bundle['applied'],{'schemaVersion','targetIndex','caseId','runId','instanceId','stageId','inputSha256','profileSha256',
        'readyFileSha256','beforeTraceFileSha256','requestMethod','requestPath','requestBody','requestBodySha256','unauthenticatedStatus','httpStatus',
        'responseBody','responseBodySha256','requestStartedAt','responseCompletedAt','cancelRequestElapsedMs'})
    require(applied['schemaVersion']==cancellation.APPLIED_SCHEMA and type(applied['targetIndex']) is int and applied['targetIndex']==target
        and all(applied[k]==route[k] for k in ('caseId','runId','instanceId','stageId','inputSha256')) and applied['profileSha256']==context['profileSha256'],"Active cancel retargeted")
    require(applied['readyFileSha256']==bundle['rawFileSha256']['coordinator-cancellation-ready.json']
        and applied['beforeTraceFileSha256']==bundle['rawFileSha256']['coordinator-cancellation-before.http.json'],"Cancel raw barriers changed")
    empty=hashlib.sha256(b'').hexdigest()
    require(applied['requestMethod']=='POST' and applied['requestPath']=='/api/v1/runs/'+route['runId']+'/cancel'
        and applied['requestBody']==applied['responseBody']=='' and applied['requestBodySha256']==applied['responseBodySha256']==empty
        and type(applied['unauthenticatedStatus']) is int and applied['unauthenticatedStatus']==401
        and type(applied['httpStatus']) is int and applied['httpStatus']==204,"Authenticated active cancellation contract changed")
    observed=timestamp(ready['activeObservedAt'],'activeAt'); started=timestamp(applied['requestStartedAt'],'cancelAt')
    completed=timestamp(applied['responseCompletedAt'],'completedAt'); eof=timestamp(row['clientEofObservedAt'],'eofAt')
    require(timestamp(row['upstreamRequestSentAt'],'sentAt')<=observed<=started<=completed and started<=eof
        <=timestamp(row['upstreamShutdownAt'],'shutdownAt')<=timestamp(row['finishedAt'],'finishedAt'),"Active HTTP cancellation order changed")
    api_ms=number(applied['cancelRequestElapsedMs'],0,TRIGGER['cancelRequestDeadlineMs'])
    require(abs((completed-started).total_seconds()*1000-api_ms)<=10 and (started-observed).total_seconds()*1000<=spec['sampleToCancelMaxMs']
        and (eof-started).total_seconds()*1000<=spec['cancelToEofDeadlineMs'],"Active coordinator cancellation missed prospective deadlines")
    before=bundle['before']; same(parse_json(bundle['beforeRaw']),before,"Prepared raw trace semantics differ")
    current=next(t for t in cohort['traces'] if t['run']['id']==route['runId'])
    require(before['run']['id']==route['runId'] and before['run']['status']=='running' and before['decisionObservations']==[],"Target was not pending before cancel")
    original=cancellation._single_assignment(before,route['stageId']); ended=cancellation._single_assignment(current,route['stageId'])
    require(original['assignmentId']==ended['assignmentId'] and original['negotiated'] is True and original['intent'] is True
        and original['returned'] is None and original['outcome']=='intent_pending' and ended['returned'] is None and ended['outcome']=='return_missing',"Unknown active caller return invented")
    caller_ms,late=cancellation._verify_http(bundle,cohort,routes,spec)
    require(abs(caller_ms-row['elapsedMs'])<=250 and started<=timestamp(late['startedAt'],'lateAt'),"Local caller return omits active work")
    revoked=next(r for r in bundle['http'] if r['path'].endswith('/renew') and r['httpStatus']==404)
    require(timestamp(revoked['finishedAt'],'revokedAt')<=eof,"Caller EOF preceded observed lease revocation")
    retired=verify_seal(bundle['retired'],active.RETIREMENT_SCHEMA)
    same(retired,active.retired_receipt(spec,ready,drained,runtime_pid=retired['runtimePid'],native_pids=retired['nativePids'],exit_code=75,
        remaining=[],log_raw=bundle['runtimeLog'],observed_at=retired['observedExitedAt']),'Active native retirement differs')
    require(0<=(timestamp(retired['observedExitedAt'],'retiredAt')-eof).total_seconds()*1000<=spec['retirementDeadlineMs'],"Active retirement deadline exceeded")
    recovered=verify_seal(bundle['recovered'],active.RECOVERED_SCHEMA)
    same(recovered,active.recovered_receipt(spec,retired,runtime_pid=recovered['runtimePid'],profile_sha=context['profileSha256'],
        server_start=float(recovered['readyServerStartText']),warmup_file_sha=hashlib.sha256(bundle['recoveryWarmup']).hexdigest(),applied_at=recovered['appliedAt']),'Active recovery differs')
    suffix=next(i for i in cohort['instances'] if i['runId']==routes[target+1]['runId'])
    require(timestamp(recovered['appliedAt'],'recoveredAt')<=timestamp(suffix['createdAt'],'suffixAt')
        and (timestamp(recovered['appliedAt'],'recoveredAt')-timestamp(retired['observedExitedAt'],'retiredAt')).total_seconds()*1000<=spec['recoveryDeadlineMs'],"Suffix crossed recovery or recovery deadline exceeded")
    arm_request(context,spec,bundle['armRequest'],routes[:target]); armed=verify_seal(bundle['armed'],ARMED_SCHEMA)
    same(armed,armed_receipt(spec,bundle['rawFileSha256']['active-native-arm-request.json'],retired['runtimePid'],float(retired['retiredServerStartText']),armed['armedAt']),'Active prefix arming changed')
    require(timestamp(bundle['armRequest']['createdAt'],'armRequestedAt')<=timestamp(armed['armedAt'],'armedAt')<=timestamp(row['acceptedAt'],'acceptedAt'),"Target started before empty-prefix accounting")
    target_instance=next(i for i in cohort['instances'] if i['runId']==route['runId'])
    require(timestamp(armed['armedAt'],'armedAt')<=timestamp(target_instance['createdAt'],'target.createdAt')<=timestamp(row['acceptedAt'],'acceptedAt'),
        "Target instance created before native prefix was armed")
    require(timestamp(target_instance['createdAt'],'target.createdAt')<=timestamp(prepared['preparedAt'],'preparedAt'),
        "Active preparation predates original target creation")
    return {'localCancelledCallerMs':caller_ms,'cancelRequestToEofMs':round((eof-started).total_seconds()*1000,3),
        'lateObservationHttpStatus':400,'latePrimaryCompletionHttpStatus':400,'cancelledDurableReturn':None,'cancelledAssignmentOutcome':'return_missing',
        'attemptedCallerReturns':len(routes),'incompleteRenewalRequestBodies':sum(r['requestBodyComplete'] is False for r in bundle['http'])}
