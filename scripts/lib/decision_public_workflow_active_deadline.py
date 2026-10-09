"""Accepted caller timeout during active native HTTP, with primary completion and recovery."""
import hashlib
import re

from decision_runtime.artifacts import sealed, verify_seal
from decision_runtime.contracts import Request, fields, number, parse_json
from scripts.lib import decision_public_workflow_active_cancellation as active
from scripts.lib import decision_public_workflow_active_integration as integration
from scripts.lib import decision_public_workflow_cancellation as cancellation
from scripts.lib.decision_public_load_verification import same
from scripts.lib.decision_shadow_pilot import require, timestamp
from scripts.lib.decision_shadow_sli import same_json

PLAN_SCHEMA="agat.decision.public-workflow-plan.v7"
RESULT_SCHEMA="agat.decision.public-workflow-result.v7"
LAUNCH_PLAN="agat.decision.public-workflow-launch-plan.v7"
LAUNCH_RESULT="agat.decision.public-workflow-launch-result.v7"
TRANSPORT_SCHEMA="agat.decision.public-workflow-active-deadline-transport.v1"
SOURCE_PATHS=["scripts/run-public-support-active-deadline.py","scripts/test/test_decision_public_workflow_active_deadline.py"]
ARTIFACTS={"caller-deadline-transport.json","active-native-ready.json","active-native-drained.json",
    "native-retired.json","native-recovered.json","runtime-recovered.log","recovery-warmup.json",
    "active-native-arm-request.json","active-native-armed.json","coordinator-deadline-http.json","workflow-graph.json"}


def deadline_spec(context,index,caller_timeout_ms=250):
    original=active.active_spec(context,index)
    require(type(caller_timeout_ms) is int and 100<=caller_timeout_ms<10000,"Caller deadline must use the published supported range")
    state=context['inputs'][index]['request']['state']; selector=None
    # Freeze a unique literal from the original state; no state wrapping, labels or model output.
    for length in range(32,513,32):
        for offset in range(0,len(state),32):
            value=state[offset:offset+length].strip()
            if value and [i for i,c in enumerate(context['inputs']) if value in c['request']['state']]==[index]:
                selector={"source":"last_output","operator":"contains","value":value,"caseSensitive":True}; break
        if selector is not None: break
    require(selector is not None,"Deadline target must have a unique original-state selector")
    return {**{k:v for k,v in original.items() if k not in {'sampleToCancelMaxMs','cancelToEofDeadlineMs'}},
        "kind":"local_caller_deadline_during_active_native_http_handler","trigger":"worker_monotonic_deadline_from_published_shadow_config",
        "callerTimeoutMs":caller_timeout_ms,"healthyCallerTimeoutMs":10000,"deadlineOverrunMaxMs":250,
        "relayCallerTimingDifferenceMaxMs":50,"inputTemplate":"{{ input }}","targetSelector":selector,
        "acceptedDurableReturn":"unavailable/timeout","coordinatorRunCancelled":False}


class ActiveDeadlineProxy(active.ActiveCancellationProxy):
    def __init__(self,upstream_port,context,spec):
        super().__init__(upstream_port,context,spec,spec_factory=lambda c,i:deadline_spec(c,i,spec['callerTimeoutMs']))

    def receipt(self):
        original=super().receipt()
        return sealed({**{k:v for k,v in original.items() if k not in {'schemaVersion','sha256'}},"schemaVersion":TRANSPORT_SCHEMA})


def receipt_bundle(raw):
    require(ARTIFACTS|{'runtime.log'}<=set(raw),"Missing deadline graph, lease HTTP or native receipts")
    names={'ready':'active-native-ready.json','drained':'active-native-drained.json','retired':'native-retired.json',
        'recovered':'native-recovered.json','armed':'active-native-armed.json','armRequest':'active-native-arm-request.json',
        'http':'coordinator-deadline-http.json','graph':'workflow-graph.json'}
    return {**{key:parse_json(raw[name]) for key,name in names.items()},'runtimeLog':raw['runtime.log'],
        'recoveryWarmup':raw['recovery-warmup.json'],'graphRaw':raw['workflow-graph.json'],
        'rawFileSha256':{name:hashlib.sha256(raw[name]).hexdigest() for name in ARTIFACTS|{'runtime.log'}}}


def verify_graph(context,spec,recipe,raw):
    graph=parse_json(raw); fields(graph,{'nodes','edges'})
    require(hashlib.sha256(raw).hexdigest()==recipe['graphSha256'],"Published graph raw bytes differ")
    ids=['start','input','deadline-selector','agent','deadline-agent','condition','primary','wrong','end']
    require(isinstance(graph['nodes'],list) and [n['id'] for n in graph['nodes']]==ids,"Deadline graph omits or adds nodes")
    nodes={n['id']:n for n in graph['nodes']}
    types=['start','transform','condition','agent','agent','condition','transform','transform','end']
    require([n['type'] for n in graph['nodes']]==types,"Published node types differ")
    for node in graph['nodes']: fields(node,{'id','type','name','position','config'})
    require(nodes['start']['config']==nodes['end']['config']=={},"Deadline graph introduced start/end work")
    same(nodes['input']['config'],{'template':spec['inputTemplate']},"Original input transformed")
    same(nodes['deadline-selector']['config'],{'condition':spec['targetSelector']},"Published deadline selector changed")
    same(nodes['condition']['config'],{'condition':{'source':'last_output','operator':'equals','value':'PRIMARY_OUTPUT','caseSensitive':True}},"Primary branch selector changed")
    for key,value in (('primary','PRIMARY_BRANCH'),('wrong','WRONG_BRANCH')):
        same(nodes[key]['config'],{'template':value},"Published primary route changed")
    agent_id=nodes['agent']['config'].get('agentId')
    require(isinstance(agent_id,str) and 0<len(agent_id)<=100 and nodes['deadline-agent']['config'].get('agentId')==agent_id,"Deadline uses another primary agent")
    for key,budget in (('agent',10000),('deadline-agent',spec['callerTimeoutMs'])):
        fields(nodes[key]['config'],{'agentId','decisionShadow','approvalRequired'})
        require(nodes[key]['config']['approvalRequired'] is False,"Deadline primary unexpectedly requires approval")
        configured=fields(nodes[key]['config']['decisionShadow'],{'mode','profileJson','timeoutMs','kind','question','options'})
        same(parse_json(configured['profileJson']),context['profile'],"Published deadline profile differs")
        expected={**recipe['config'],'options':Request.from_dict(context['inputs'][0]['request']).to_dict()['options']}
        same({k:configured[k] for k in ('kind','question','options')},expected,"Published native question/options differ")
        require(configured['mode']=='shadow' and type(configured['timeoutMs']) is int and configured['timeoutMs']==budget,"Deadline was not published on the actual agent")
    expected=[('start','input','default'),('input','deadline-selector','default'),('deadline-selector','deadline-agent','true'),
        ('deadline-selector','agent','false'),('agent','condition','default'),('deadline-agent','condition','default'),
        ('condition','primary','true'),('condition','wrong','false'),('primary','end','default'),('wrong','end','default')]
    require(len(graph['edges'])==len(expected) and len({e['id'] for e in graph['edges']})==len(expected),"Graph edges omitted or repeated")
    for edge in graph['edges']: fields(edge,{'id','source','target','branch'})
    same([(e['source'],e['target'],e['branch']) for e in graph['edges']],expected,"Deadline graph bypasses original primary")
    require([i for i,c in enumerate(context['inputs']) if spec['targetSelector']['value'] in c['request']['state']]==[spec['targetIndex']],"Short deadline selects another case")


def verify_http(context,spec,cohort,routes,bundle):
    records=bundle['http']; require(isinstance(records,list) and len(routes)*3<=len(records)<=2000,"Incomplete deadline lease journal")
    for row in records:
        fields(row,{'method','path','startedAt','finishedAt','startedMs','finishedMs','requestBody','requestBodySha256','requestBodyComplete','httpStatus'})
        require(row['method']=='POST' and isinstance(row['path'],str) and re.fullmatch(r'/api/v1/leases/[^/?#]+/(renew|decision-shadow(?:/intent)?|complete|fail)',row['path']),"Unexpected deadline lease HTTP request")
        require(isinstance(row['requestBody'],str) and type(row['requestBodyComplete']) is bool
            and (row['requestBodyComplete'] or row['path'].endswith('/renew')),"Required deadline HTTP body incomplete")
        raw=row['requestBody'].encode(); require(len(raw)<=128*1024 and hashlib.sha256(raw).hexdigest()==row['requestBodySha256'],"Actual deadline HTTP body changed")
        require(timestamp(row['startedAt'],'http.startedAt')<=timestamp(row['finishedAt'],'http.finishedAt')
            and number(row['startedMs'],0,600000)<=number(row['finishedMs'],0,600000)
            and type(row['httpStatus']) is int and row['httpStatus']==(204 if row['path'].endswith('/renew') else 200),"Deadline revoked a lease or rejected a write")
    intents=[r for r in records if r['path'].endswith('/decision-shadow/intent')]
    returns=[r for r in records if r['path'].endswith('/decision-shadow')]
    completions=[r for r in records if r['path'].endswith('/complete')]
    require(len(intents)==len(returns)==len(completions)==len(routes) and not any(r['path'].endswith('/fail') for r in records),"Missing or extra deadline intent/return/completion")
    traces={t['run']['id']:t for t in cohort['traces']}; leases=set(); target_return=None
    for index,route in enumerate(routes):
        assigned=cancellation._single_assignment(traces[route['runId']],route['stageId'])
        matches=[]
        for row in intents:
            body=fields(parse_json(row['requestBody']),{'schemaVersion','assignmentId'})
            require(body['schemaVersion']=='agat.decision.caller-accounting.v1' and isinstance(body['assignmentId'],str),"Unknown deadline caller intent")
            if body['assignmentId']==assigned['assignmentId']: matches.append(row)
        require(len(matches)==1,"Deadline intent does not bind original assignment")
        lease=matches[0]['path'].removesuffix('/decision-shadow/intent'); require(lease not in leases,"Deadline lease reused")
        leases.add(lease)
        posted=[r for r in returns if r['path']==lease+'/decision-shadow']; primary=[r for r in completions if r['path']==lease+'/complete']
        require(len(posted)==len(primary)==1,"Missing actual durable deadline return")
        require(parse_json(primary[0]['requestBody'])['output']=='PRIMARY_OUTPUT'
            and timestamp(matches[0]['finishedAt'],'intent.finishedAt')<=timestamp(posted[0]['startedAt'],'return.startedAt')
            and timestamp(posted[0]['finishedAt'],'return.finishedAt')<=timestamp(primary[0]['startedAt'],'primary.startedAt'),"Deadline bypassed intent, return or primary ordering")
        stored=traces[route['runId']]['decisionObservations'][0]['observation']; body=parse_json(posted[0]['requestBody'])
        if index==spec['targetIndex']:
            fields(body,{'status','reason','callerTiming'}); require(body['status']=='unavailable' and body['reason']=='timeout',"Deadline return has a model result or cancelled reason")
            target_return=posted[0]
        else: fields(body,{'result','callerTiming'})
        require(same_json({k:stored[k] for k in body},body),"Actual HTTP return differs from stored observation")
    require(all(r['path'].removesuffix('/renew') in leases for r in records if r['path'].endswith('/renew')),"Foreign deadline lease renewal")
    return target_return


def verify_boundary(context,spec,transport,cohort,routes,bundle):
    fields(bundle,{'ready','drained','retired','recovered','armed','armRequest','http','graph','graphRaw','runtimeLog','recoveryWarmup','rawFileSha256'})
    fields(bundle['rawFileSha256'],ARTIFACTS|{'runtime.log'})
    index=spec['targetIndex']; row=transport['rows'][index]; route=routes[index]
    ready=verify_seal(bundle['ready'],active.READY_SCHEMA); drained=verify_seal(bundle['drained'],active.DRAIN_SCHEMA)
    same(ready,active.ready_receipt(row,{'capturedAt':ready['activeObservedAt'],'metricsRaw':ready['activeMetricsRaw']}),'Active deadline ready proof differs')
    same(drained,active.drain_receipt(row),'Deadline EOF proof differs')
    eof=timestamp(row['clientEofObservedAt'],'eofAt')
    require(timestamp(row['acceptedAt'],'acceptedAt')<=timestamp(row['upstreamRequestSentAt'],'sentAt')<=timestamp(ready['activeObservedAt'],'activeAt')<eof
        <=timestamp(row['upstreamShutdownAt'],'shutdownAt')<=timestamp(row['finishedAt'],'finishedAt'),"Deadline did not cross the active native boundary")
    returned=verify_http(context,spec,cohort,routes,bundle)
    caller_ms=number(parse_json(returned['requestBody'])['callerTiming']['durationMs'],spec['callerTimeoutMs']-1,spec['callerTimeoutMs']+spec['deadlineOverrunMaxMs'])
    require(spec['callerTimeoutMs']-spec['relayCallerTimingDifferenceMaxMs']<=row['elapsedMs']<=spec['callerTimeoutMs']+spec['deadlineOverrunMaxMs']
        and abs(caller_ms-row['elapsedMs'])<=spec['relayCallerTimingDifferenceMaxMs']
        and eof<=timestamp(returned['startedAt'],'return.startedAt'),"Caller EOF missed its published monotonic deadline")
    retired=verify_seal(bundle['retired'],active.RETIREMENT_SCHEMA)
    same(retired,active.retired_receipt(spec,ready,drained,runtime_pid=retired['runtimePid'],native_pids=retired['nativePids'],exit_code=75,
        remaining=[],log_raw=bundle['runtimeLog'],observed_at=retired['observedExitedAt']),'Deadline native retirement differs')
    require(0<=(timestamp(retired['observedExitedAt'],'retiredAt')-eof).total_seconds()*1000<=spec['retirementDeadlineMs'],"Deadline retirement budget exceeded")
    recovered=verify_seal(bundle['recovered'],active.RECOVERED_SCHEMA)
    same(recovered,active.recovered_receipt(spec,retired,runtime_pid=recovered['runtimePid'],profile_sha=context['profileSha256'],server_start=float(recovered['readyServerStartText']),
        warmup_file_sha=hashlib.sha256(bundle['recoveryWarmup']).hexdigest(),applied_at=recovered['appliedAt']),'Deadline recovery differs')
    suffix=next(i for i in cohort['instances'] if i['runId']==routes[index+1]['runId'])
    require(timestamp(recovered['appliedAt'],'recoveredAt')<=timestamp(suffix['createdAt'],'suffixAt')
        and (timestamp(recovered['appliedAt'],'recoveredAt')-timestamp(retired['observedExitedAt'],'retiredAt')).total_seconds()*1000<=spec['recoveryDeadlineMs'],"Deadline suffix bypasses owned recovery")
    integration.arm_request(context,spec,bundle['armRequest'],routes[:index]); armed=verify_seal(bundle['armed'],integration.ARMED_SCHEMA)
    same(armed,integration.armed_receipt(spec,bundle['rawFileSha256']['active-native-arm-request.json'],retired['runtimePid'],float(retired['retiredServerStartText']),armed['armedAt']),'Deadline prefix arming differs')
    target=next(i for i in cohort['instances'] if i['runId']==route['runId'])
    require(timestamp(bundle['armRequest']['createdAt'],'armAt')<=timestamp(armed['armedAt'],'armedAt')<=timestamp(target['createdAt'],'createdAt')
        <=timestamp(row['acceptedAt'],'acceptedAt'),"Deadline target preceded prefix/native arming")
    return {'localTimedOutCallerMs':caller_ms,'acceptedTimeoutHttpStatus':200,'acceptedPrimaryCompletionHttpStatus':200,
        'timeoutAssignmentOutcome':'recorded','coordinatorRunCancelled':False,'attemptedCallerReturns':len(routes),
        'incompleteRenewalRequestBodies':sum(r['requestBodyComplete'] is False for r in bundle['http'])}


def verify_transport(context,spec,transport,cohort,routes,bundle):
    return integration.verify_transport(context,spec,transport,cohort,routes,bundle,
        spec_factory=lambda c,i:deadline_spec(c,i,spec['callerTimeoutMs']),transport_schema=TRANSPORT_SCHEMA,boundary_check=verify_boundary)
