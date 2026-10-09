"""Synthetic deadline receipts and actual model-free coordinator/worker HTTP tests."""
import copy
from collections import Counter
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from decision_runtime.metrics import OUTCOMES, outcome
from decision_runtime.contracts import Request

from scripts.lib import decision_public_workflow as workflow
from scripts.lib import decision_public_workflow_active_cancellation as active
from scripts.lib import decision_public_workflow_active_integration as integration
from scripts.lib import decision_public_workflow_active_deadline as deadline
from scripts.lib import decision_public_workflow_active_deadline_verification as verification
from scripts.lib import decision_public_workflow_verification as common_verification
from scripts.lib.decision_public_load_verification import CONTEXT_PATHS
from scripts.lib.decision_public_peer_cancellation_verification import verify_physical
from scripts.test import test_decision_public_workflow_active_integration as prior

ROOT=Path(__file__).resolve().parents[2]; encoded=prior.encoded; sha=prior.sha; reseal=prior.reseal


@lru_cache(maxsize=1)
def prepared_fixture():
    context,recipe,cohort,routes,transport,old_bundle,plan,result,old_raw=prior.fixture()
    spec=deadline.deadline_spec(context,2,250)
    for value in (recipe,plan,result):
        value.pop('coordinatorCancellation'); value.pop('coordinatorTrigger'); value['callerDeadline']=spec
    recipe['schemaVersion']=deadline.PLAN_SCHEMA; plan['schemaVersion']=deadline.LAUNCH_PLAN; result['schemaVersion']=deadline.LAUNCH_RESULT
    shift=lambda text:(datetime.fromisoformat(text.replace('Z','+00:00'))+timedelta(milliseconds=200)).isoformat(timespec='milliseconds').replace('+00:00','Z')
    for key in ('clientEofObservedAt','upstreamShutdownAt','finishedAt'): transport['rows'][2][key]=shift(transport['rows'][2][key])
    transport['rows'][2]['elapsedMs']=250.0
    for row in transport['rows'][3:]:
        for key in ('acceptedAt','upstreamCompletedAt','finishedAt'): row[key]=shift(row[key])
    transport['closedAt']=shift(transport['closedAt']); transport['spec']=spec; transport['schemaVersion']=deadline.TRANSPORT_SCHEMA; transport=reseal(transport)
    for instance in cohort['instances'][3:]: instance['createdAt']=shift(instance['createdAt'])
    cohort['scope']['endAt']=shift(cohort['scope']['endAt']); cohort['observedAt']=shift(cohort['observedAt'])
    trace=cohort['traces'][2]; route=routes[2]; route.update(primaryBranch=True,runStatus='completed')
    cohort['instances'][2]['status']='completed'; trace['run']['status']='completed'
    timing={'schemaVersion':'agat.decision.caller-timing.v1','clock':'monotonic','boundary':'local_http_call','durationMs':250.0}
    obs={'mode':'shadow','fallback':'primary','status':'unavailable','reason':'timeout','callerTiming':timing}
    stage=trace['decisionStageInventory']['stages'][0]; stage.update(stageStatus='completed',observationRecorded=True,callerTimeoutMs=250)
    assigned=trace['decisionAssignmentHistory']['stages'][0]['assignments'][0]; assigned.update(outcome='recorded',callerTimeoutMs=250,observation=copy.deepcopy(obs))
    caller=trace['decisionCallerAccounting']['stages'][0]['assignments'][0]; caller.update(outcome='returned',returned={k:obs[k] for k in ('status','reason','callerTiming')})
    trace['decisionObservations']=[{'stageId':route['stageId'],'profileSha256':context['profileSha256'],'inputSha256':route['inputSha256'],'callerTimeoutMs':250,'observation':copy.deepcopy(obs)}]
    row=transport['rows'][2]; ready=active.ready_receipt(row,{'capturedAt':old_bundle['ready']['activeObservedAt'],'metricsRaw':old_bundle['ready']['activeMetricsRaw']}); drained=active.drain_receipt(row)
    retired=active.retired_receipt(spec,ready,drained,runtime_pid=101,native_pids=[101,102,103],exit_code=75,remaining=[],
        log_raw=old_raw['runtime.log'],observed_at=shift(old_bundle['retired']['observedExitedAt']))
    recovered=active.recovered_receipt(spec,retired,runtime_pid=201,profile_sha=context['profileSha256'],server_start=1001.125,
        warmup_file_sha=sha(old_raw['recovery-warmup.json']),applied_at=shift(old_bundle['recovered']['appliedAt']))
    http=[copy.deepcopy(r) for r in old_bundle['http'] if not r['path'].endswith('/fail')]
    assignment=caller['assignmentId']; intent=next(r for r in http if r['path'].endswith('/decision-shadow/intent') and json.loads(r['requestBody'])['assignmentId']==assignment)
    lease=intent['path'].removesuffix('/decision-shadow/intent')
    for record in http:
        if record['path'].startswith(lease+'/'):
            record['httpStatus']=204 if record['path'].endswith('/renew') else 200
            if record['path'].endswith('/decision-shadow'):
                body={k:obs[k] for k in ('status','reason','callerTiming')}; raw=encoded(body); record.update(requestBody=raw.decode(),requestBodySha256=sha(raw))
        if record['path']==lease+'/decision-shadow' or record['path']==lease+'/complete':
            for key in ('startedAt','finishedAt'): record[key]=shift(record[key])
    # Actual intent/return/completion order is explicit in this synthetic journal.
    for index,current in enumerate(routes):
        ledger=cohort['traces'][index]['decisionCallerAccounting']['stages'][0]['assignments'][0]
        begin=next(r for r in http if r['path'].endswith('/decision-shadow/intent') and json.loads(r['requestBody'])['assignmentId']==ledger['assignmentId'])
        lease_path=begin['path'].removesuffix('/decision-shadow/intent')
        posted=next(r for r in http if r['path']==lease_path+'/decision-shadow'); complete=next(r for r in http if r['path']==lease_path+'/complete')
        begin['startedAt']=begin['finishedAt']=transport['rows'][index]['acceptedAt']
        posted['startedAt']=posted['finishedAt']=transport['rows'][index]['finishedAt']; complete['startedAt']=complete['finishedAt']=posted['finishedAt']
    ids=['start','input','deadline-selector','agent','deadline-agent','condition','primary','wrong','end']
    types=['start','transform','condition','agent','agent','condition','transform','transform','end']
    shadow={'mode':'shadow','profileJson':json.dumps(context['profile'],ensure_ascii=False),'timeoutMs':10000,**recipe['config']}
    shadow['options']=Request.from_dict(context['inputs'][0]['request']).to_dict()['options']
    configs=[{}, {'template':spec['inputTemplate']},{'condition':spec['targetSelector']},
        {'agentId':'fixture-agent','approvalRequired':False,'decisionShadow':shadow},
        {'agentId':'fixture-agent','approvalRequired':False,'decisionShadow':{**shadow,'timeoutMs':250}},
        {'condition':{'source':'last_output','operator':'equals','value':'PRIMARY_OUTPUT','caseSensitive':True}},
        {'template':'PRIMARY_BRANCH'},{'template':'WRONG_BRANCH'},{}]
    edge_values=[('start','input','default'),('input','deadline-selector','default'),('deadline-selector','deadline-agent','true'),('deadline-selector','agent','false'),
        ('agent','condition','default'),('deadline-agent','condition','default'),('condition','primary','true'),('condition','wrong','false'),('primary','end','default'),('wrong','end','default')]
    graph={'nodes':[{'id':id,'type':type,'name':id,'position':{'x':0,'y':0},'config':config} for id,type,config in zip(ids,types,configs)],
        'edges':[{'id':str(i),'source':a,'target':b,'branch':branch} for i,(a,b,branch) in enumerate(edge_values)]}
    recipe['graphSha256']=sha(encoded(graph))
    artifacts={name:old_raw[name] for name in ('worker.log','driver.log','runtime.log','runtime-recovered.log','recovery-warmup.json','active-native-arm-request.json','active-native-armed.json')}
    artifacts.update({name:encoded(value) for name,value in {'workflow-plan.json':recipe,'cohort.http.json':cohort,'caller-deadline-transport.json':transport,
        'active-native-ready.json':ready,'active-native-drained.json':drained,'native-retired.json':retired,'native-recovered.json':recovered,
        'coordinator-deadline-http.json':http,'workflow-graph.json':graph}.items()})
    artifacts['workflow-routes.jsonl']=b''.join(encoded(r).replace(b'\n',b'')+b'\n' for r in routes)
    bundle=deadline.receipt_bundle(artifacts)
    evidence=workflow.verify_inventory(context,recipe,cohort,routes,transport,bundle)
    driver=json.loads(old_raw['workflow-driver.json'])
    for key in ('coordinatorCancellationReady','coordinatorCancellationApplied','coordinatorCancellationDrained','coordinatorCancellationPrepared'): driver.pop(key)
    driver.update(routes=routes,actualWindow={k:cohort['scope'][k] for k in ('startAt','endAt')},activeNativeRecovered=recovered,activeDeadlineReady=ready,activeDeadlineDrained=drained)
    artifacts['workflow-driver.json']=encoded(driver)
    for sample in result['samples'][3:]: sample.update(capturedAt=shift(sample['capturedAt']),elapsedMs=sample['elapsedMs']+200)
    plan=reseal(plan); result.update(planSha256=plan['sha256'],evidence=evidence,nativeRetired=retired,nativeRecovered=recovered,elapsedMs=1500,
        artifactSha256={name:sha(raw) for name,raw in artifacts.items()})
    result['physical']=verify_physical(context,result,transport,ready,retired,recovered,artifacts); result=reseal(result)
    return context,recipe,cohort,routes,transport,bundle,plan,result,artifacts


def fixture(): return copy.deepcopy(prepared_fixture())


class ActiveDeadlineInventoryTest(unittest.TestCase):
    def test_accepted_timeout_retains_all_primary_routes_and_unknown_native_terminal(self):
        context,recipe,cohort,routes,transport,bundle,*_=fixture()
        with patch('http.client.HTTPConnection') as http:
            result=workflow.verify_inventory(context,recipe,cohort,routes,transport,bundle); http.assert_not_called()
        self.assertEqual(result['boundCallerReturns'],6); self.assertEqual(result['completedInstances'],6)
        self.assertEqual(result['unavailableReturns'],1); self.assertEqual(result['localTimedOutCallerMs'],250)
        self.assertTrue(result['primaryRoutePreserved']); self.assertIsNone(result['targetTypedResult']); self.assertTrue(result['targetTerminalCounterUnknown'])
        self.assertEqual(result['acceptedTimeoutHttpStatus'],200); self.assertFalse(result['coordinatorRunCancelled'])

    def test_budget_result_reason_primary_and_raw_graph_mutations_fail(self):
        for change in ('stage_budget','assignment_budget','observed_budget','cancelled','typed','primary','early_eof','graph_budget','graph_input','selector'):
            context,recipe,cohort,routes,transport,bundle,*_=fixture(); trace=cohort['traces'][2]
            if change=='stage_budget': trace['decisionStageInventory']['stages'][0]['callerTimeoutMs']=10000
            elif change=='assignment_budget': trace['decisionAssignmentHistory']['stages'][0]['assignments'][0]['callerTimeoutMs']=10000
            elif change=='observed_budget': trace['decisionObservations'][0]['callerTimeoutMs']=True
            elif change=='cancelled': trace['decisionObservations'][0]['observation']['reason']='cancelled'
            elif change=='typed': trace['decisionObservations'][0]['observation']['result']={}
            elif change=='primary': routes[2]['primaryBranch']=False
            elif change=='early_eof': transport['rows'][2]['elapsedMs']=100; transport=reseal(transport)
            else:
                graph=json.loads(bundle['graphRaw'])
                if change=='graph_budget': graph['nodes'][4]['config']['decisionShadow']['timeoutMs']=10000
                elif change=='graph_input': graph['nodes'][1]['config']['template']='CHANGED_INPUT'
                else: graph['nodes'][2]['config']['condition']['value']='common'
                bundle['graphRaw']=encoded(graph); recipe['graphSha256']=sha(bundle['graphRaw'])
            with self.subTest(change=change),self.assertRaises(ValueError): workflow.verify_inventory(context,recipe,cohort,routes,transport,bundle)

    def test_http_ownership_status_completion_body_order_and_foreign_lease_fail(self):
        for change in ('revocation','late_return','missing_return','retry','missing_body','foreign_renewal','primary_output','return_order'):
            context,recipe,cohort,routes,transport,bundle,*_=fixture(); rows=bundle['http']
            if change=='revocation': next(r for r in rows if r['path'].endswith('/renew'))['httpStatus']=404
            elif change=='late_return': next(r for r in rows if r['path'].endswith('/decision-shadow'))['httpStatus']=400
            elif change=='missing_return': rows.remove(next(r for r in rows if r['path'].endswith('/decision-shadow')))
            elif change=='retry': rows.append(copy.deepcopy(next(r for r in rows if r['path'].endswith('/decision-shadow'))))
            elif change=='missing_body': next(r for r in rows if r['path'].endswith('/complete'))['requestBodyComplete']=False
            elif change=='foreign_renewal': next(r for r in rows if r['path'].endswith('/renew'))['path']='/api/v1/leases/foreign/renew'
            elif change=='primary_output':
                row=next(r for r in rows if r['path'].endswith('/complete')); raw=encoded({'output':'WRONG'}); row.update(requestBody=raw.decode(),requestBodySha256=sha(raw))
            else: next(r for r in rows if r['path'].endswith('/complete'))['startedAt']=recipe['startAt']
            with self.subTest(change=change),self.assertRaises(ValueError): workflow.verify_inventory(context,recipe,cohort,routes,transport,bundle)

    def test_selector_is_unique_and_limits_are_strict_without_boolean_budget(self):
        context,*_=fixture()
        for value in (True,99,10000,250.0):
            with self.subTest(value=value),self.assertRaises(ValueError): deadline.deadline_spec(context,2,value)
        context['inputs'][1]['request']['state']=context['inputs'][2]['request']['state']
        with self.assertRaises(ValueError): deadline.deadline_spec(context,2,250)


class ActiveDeadlineOfflineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original=fixture(); cls.temporary=tempfile.TemporaryDirectory(); cls.root=Path(cls.temporary.name)/'sources'; cls.root.mkdir()
        names=subprocess.check_output(['git','ls-files','--',*sorted(set(workflow.SOURCE_PATHS+deadline.SOURCE_PATHS+CONTEXT_PATHS))],cwd=ROOT,text=True).splitlines()
        for name in names:
            path=cls.root/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes((ROOT/name).read_bytes())
        for args in (['init','--quiet'],['add','.'],['-c','user.name=Synthetic fixture','-c','user.email=fixture@example.invalid','commit','--quiet','-m','Synthetic deadline sources']): subprocess.run(['git',*args],cwd=cls.root,check=True)
        cls.commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=cls.root,text=True).strip()
    @classmethod
    def tearDownClass(cls): cls.temporary.cleanup()
    pins=prior.ActiveWorkflowOfflineTest.pins
    def write(self,directory):
        context,*_,plan,result,artifacts=copy.deepcopy(self.original)
        context.update(sourceCommit=self.commit,sourceFiles=self.pins(CONTEXT_PATHS)); context=reseal(context)
        plan.update(sourceCommit=self.commit,sourceFiles=self.pins(workflow.SOURCE_PATHS+deadline.SOURCE_PATHS),context=context,contextProfileFileSha256=sha(encoded(context))); plan=reseal(plan)
        result['planSha256']=plan['sha256']; result=reseal(result)
        for name,raw in {'context.json':encoded(context),'plan.json':encoded(plan),'result.json':encoded(result),**artifacts}.items(): (directory/name).write_bytes(raw)
        return {'context_sha':sha(encoded(context)),'plan_sha':sha(encoded(plan)),'result_sha':sha(encoded(result))}
    def test_generic_offline_v7_uses_complete_historical_sources_without_model_calls(self):
        with tempfile.TemporaryDirectory() as temp,patch('http.client.HTTPConnection') as http:
            directory=Path(temp); pins=self.write(directory); report=common_verification.verify(self.root,directory,directory/'context.json',**pins); http.assert_not_called()
            self.assertEqual(report['status'],'pass'); self.assertEqual(report['schemaVersion'],'agat.decision.public-workflow-verification.v7')
            self.assertEqual(report['verificationModelCalls'],0); self.assertEqual(report['knownCompletedPhysicalCalls'],9)
            self.assertFalse(report['gpuKernelPreemptionEstablished']); self.assertFalse(report['liveCleanupVerified'])
    def test_rehashed_source_omission_and_raw_pin_change_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp); pins=self.write(directory)
            for key in pins:
                with self.subTest(key=key),self.assertRaises(ValueError): verification.verify(self.root,directory,directory/'context.json',**{**pins,key:'0'*64})
            plan=json.loads((directory/'plan.json').read_bytes()); plan['sourceFiles'].pop('scripts/lib/decision_public_workflow_active_deadline.py'); plan=reseal(plan)
            result=json.loads((directory/'result.json').read_bytes()); result['planSha256']=plan['sha256']; result=reseal(result)
            (directory/'plan.json').write_bytes(encoded(plan)); (directory/'result.json').write_bytes(encoded(result))
            with self.assertRaises(ValueError): verification.verify(self.root,directory,directory/'context.json',**{**pins,'plan_sha':sha(encoded(plan)),'result_sha':sha(encoded(result))})
    def test_rehashed_posthoc_plan_is_rejected_before_inventory_replay(self):
        for created in ('2026-10-08T00:00:00.000Z','2026-10-09T03:00:01.000Z'):
            with self.subTest(created=created),tempfile.TemporaryDirectory() as temporary:
                directory=Path(temporary); pins=self.write(directory)
                plan=json.loads((directory/'plan.json').read_bytes()); plan['createdAt']=created; plan=reseal(plan)
                (directory/'plan.json').write_bytes(encoded(plan))
                result=json.loads((directory/'result.json').read_bytes()); result['planSha256']=plan['sha256']; result=reseal(result)
                (directory/'result.json').write_bytes(encoded(result))
                with self.assertRaisesRegex(ValueError,'Plan was not fixed before the first native origin and scoring'):
                    verification.verify(self.root,directory,directory/'context.json',**{**pins,'plan_sha':sha(encoded(plan)),'result_sha':sha(encoded(result))})


class ActiveDeadlineDriverTest(unittest.TestCase):
    def test_published_short_budget_actual_worker_timeout_is_accepted_without_run_cancellation(self):
        context,_,_,_,templates,*_=fixture(); spec=deadline.deadline_spec(context,2,250)
        lock=threading.Lock(); stop=threading.Event(); eof=threading.Event()
        state={'requests':[],'completed':Counter(ok=2),'active':False,'epoch':1000.125}
        profile_json=json.dumps(context['profile'],ensure_ascii=False,sort_keys=True,separators=(',',':'))
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*_): pass
            def respond(self,status,raw,content_type):
                self.send_response(status); self.send_header('Content-Type',content_type); self.send_header('Content-Length',str(len(raw)))
                self.end_headers(); self.wfile.write(raw)
            def do_GET(self):
                if self.path=='/health':
                    self.respond(200,encoded({'status':'ready','mode':'shadow','profileSha256':context['profileSha256'],'profileJson':profile_json}),'application/json'); return
                if self.path!='/metrics': self.send_error(404); return
                with lock:
                    raw='\n'.join(f'agat_decision_requests_total{{outcome="{key}"}} {state["completed"][key]}' for key in sorted(OUTCOMES))
                    raw+=f'\nagat_decision_backend_ready 1\nagat_decision_requests_in_progress {int(state["active"])}\nagat_decision_server_start_time_seconds {state["epoch"]}\n'
                self.respond(200,raw.encode(),'text/plain')
            def do_POST(self):
                request=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                with lock: index=len(state['requests']); state['requests'].append(request)
                if index==2:
                    with lock: state['active']=True
                    self.connection.settimeout(.02); until=time.monotonic()+5
                    while not stop.is_set() and time.monotonic()<until:
                        try:
                            if self.connection.recv(1)==b'': eof.set(); return
                            raise AssertionError('Unexpected bytes after original target')
                        except socket.timeout: continue
                    raise TimeoutError('Synthetic handler never received actual upstream EOF')
                typed=json.loads(templates['rows'][index]['responseBody']); typed.update(id=request['id'],durationMs=0.0)
                with lock: state['completed'][outcome(typed)]+=1
                self.respond(200 if context['inputs'][index]['contextEligible'] else 422,encoded(typed),'application/json')
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler); serving=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01}); serving.start()
        proxy=child=None
        try:
            proxy=deadline.ActiveDeadlineProxy(server.server_port,context,spec); proxy.bind_warmups({'ok':2},1000.125)
            private=ROOT/'docs/private'; private.mkdir(mode=0o700,parents=True,exist_ok=True)
            with tempfile.TemporaryDirectory(dir=private,prefix='active-deadline-fixture-') as temporary:
                directory=Path(temporary)
                def publish(name,value):
                    if name in {'active-native-armed.json','native-retired.json','native-recovered.json'}: integration.publish_barrier(directory,name,value)
                    else:
                        with (directory/name).open('xb') as file: file.write(encoded(value))
                publish('plan.json',{'schemaVersion':deadline.LAUNCH_PLAN,'context':context,'config':workflow.shared_config(context),'callerDeadline':spec})
                armed=retired=recovered=None
                with (directory/'driver.log').open('wb') as log:
                    child=subprocess.Popen(['node','--import','tsx','scripts/run-public-support-workflow.mts','--decision-url',f'http://127.0.0.1:{proxy.port}',
                        '--evidence-dir',str(directory)],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                    until=time.monotonic()+40
                    while child.poll() is None and time.monotonic()<until:
                        if armed is None and (directory/'active-native-arm-request.json').exists():
                            raw=(directory/'active-native-arm-request.json').read_bytes(); arm=json.loads(raw)
                            routes=[json.loads(line) for line in (directory/'workflow-routes.jsonl').read_bytes().splitlines()]
                            integration.arm_request(context,spec,arm,routes)
                            armed=integration.armed_receipt(spec,sha(raw),101,1000.125,datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z'))
                            publish('active-native-armed.json',armed)
                        ready=proxy.ready_receipt()
                        if ready is not None and not (directory/'active-native-ready.json').exists(): publish('active-native-ready.json',ready)
                        drained=proxy.target_receipt()
                        if drained is not None and recovered is None:
                            self.assertTrue(eof.wait(1)); self.assertEqual(len(state['requests']),3)
                            publish('active-native-drained.json',drained)
                            event={'schemaVersion':'agat.decision.retirement.v1','eventName':'decision.backend_retired','runtimeVersion':'0.12.3',
                                'profileSha256':context['profileSha256'],'exitCode':75,'reason':'inference_cancelled','childPid':102,'childExitCode':-15}
                            raw_log=b'Synthetic retirement fixture; no model or native processes.\n'+json.dumps(event).encode()+b'\n'
                            (directory/'runtime.log').write_bytes(raw_log)
                            stamp=lambda:datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
                            retired=active.retired_receipt(spec,ready,drained,runtime_pid=101,native_pids=[101,102,103],exit_code=75,remaining=[],log_raw=raw_log,observed_at=stamp())
                            publish('native-retired.json',retired); publish('recovery-warmup.json',{'fixture':'Synthetic warmup pin; no native calls'})
                            with lock: state.update(completed=Counter(ok=2),active=False,epoch=1001.125)
                            recovered=active.recovered_receipt(spec,retired,runtime_pid=201,profile_sha=context['profileSha256'],server_start=1001.125,
                                warmup_file_sha=sha((directory/'recovery-warmup.json').read_bytes()),applied_at=stamp())
                            publish('native-recovered.json',recovered)
                        time.sleep(.005)
                    if child.poll() is None: os.killpg(child.pid,signal.SIGTERM); child.wait(timeout=8)
                self.assertEqual(child.returncode,0,(directory/'driver.log').read_text()[-4000:]); proxy.close(); transport=proxy.receipt()
                raw={name:(directory/name).read_bytes() for name in deadline.ARTIFACTS|{'runtime.log'} if name not in {'caller-deadline-transport.json','runtime-recovered.log'}}
                raw['runtime-recovered.log']=b'Synthetic recovered metadata; no native processes.\n'; raw['caller-deadline-transport.json']=encoded(transport)
                driver=json.loads((directory/'workflow-driver.json').read_bytes()); cohort=json.loads((directory/'cohort.http.json').read_bytes()); recipe=json.loads((directory/'workflow-plan.json').read_bytes())
                result=workflow.verify_inventory(context,recipe,cohort,driver['routes'],transport,deadline.receipt_bundle(raw))
                self.assertEqual(result['boundCallerReturns'],6); self.assertEqual(result['completedInstances'],6); self.assertEqual(result['healthySuffixCases'],3)
                self.assertEqual(result['timeoutAssignmentOutcome'],'recorded'); self.assertFalse(result['coordinatorRunCancelled'])
                self.assertEqual(len(state['requests']),6); self.assertEqual(transport['completedUpstreamPosts'],5)
                self.assertEqual(driver['activeNativeRecovered'],recovered); self.assertFalse((directory/'worker-credentials.json').exists())
                self.assertGreaterEqual(result['localTimedOutCallerMs'],249); self.assertLessEqual(result['localTimedOutCallerMs'],500)
                for index,trace in enumerate(cohort['traces']): self.assertEqual(trace['decisionStageInventory']['stages'][0]['callerTimeoutMs'],250 if index==2 else 10000)
        finally:
            stop.set()
            if child is not None:
                try: os.killpg(child.pid,signal.SIGKILL)
                except ProcessLookupError: pass
                child.wait()
            if proxy is not None and not proxy.closed: proxy.close()
            server.shutdown(); server.server_close(); serving.join(2); self.assertFalse(serving.is_alive())


if __name__=='__main__': unittest.main()
