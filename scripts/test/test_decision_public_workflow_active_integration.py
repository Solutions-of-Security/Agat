"""Synthetic v6 fixtures; model-free HTTP/coordinator evidence is not native evidence."""
import copy
from collections import Counter
from datetime import datetime,timedelta,timezone
from functools import lru_cache
import hashlib
import importlib.util
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import signal
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from decision_runtime.artifacts import sealed
from decision_runtime.metrics import OUTCOMES,outcome
from scripts.lib import decision_public_workflow as workflow
from scripts.lib import decision_public_workflow_active_cancellation as active
from scripts.lib import decision_public_workflow_active_integration as integration
from scripts.lib import decision_public_workflow_active_verification as verification
from scripts.lib import decision_public_workflow_verification as common_verification
from scripts.lib.decision_public_load_verification import CONTEXT_PATHS
from scripts.lib.decision_public_peer_cancellation_verification import verify_physical
from scripts.test import test_decision_public_peer_cancellation_verification as peer
from scripts.test import test_decision_public_workflow_cancellation as prior
from scripts.test import test_decision_public_workflow_verification as offline

ROOT=Path(__file__).resolve().parents[2]; encoded=peer.encoded; sha=peer.sha; reseal=peer.reseal


@lru_cache(maxsize=1)
def prepared_fixture():
    context,peer_plan,result,raw=peer.fixture(); _,recipe,cohort,routes,_,old_bundle=prior.fixture()
    spec=active.active_spec(context,2); recipe.update(schemaVersion=active.PLAN_SCHEMA,coordinatorCancellation=spec,coordinatorTrigger=integration.TRIGGER)
    base=datetime(2026,10,9,3,tzinfo=timezone.utc); stamp=lambda ms:(base+timedelta(milliseconds=ms)).isoformat(timespec='milliseconds').replace('+00:00','Z')
    recipe['startAt']=stamp(0); cohort['scope'].update(startAt=stamp(0),endAt=stamp(1210)); cohort['observedAt']=stamp(1220)
    transport=json.loads(raw['relay-transport.json'])
    for index,(case,route,trace,wire) in enumerate(zip(context['inputs'],routes,cohort['traces'],transport['rows'])):
        request=encoded({**case['request'],'id':route['stageId']}); wire.update(stageId=route['stageId'],requestBody=request.decode(),requestBodySha256=sha(request))
        cohort['instances'][index]['createdAt']=stamp(499 if index==2 else 50+index*200)
        if index==2: continue
        typed=json.loads(wire['responseBody']); typed['id']=route['stageId']; response=encoded(typed)
        wire.update(responseBody=response.decode(),responseBodySha256=sha(response),responseBytesWritten=len(response))
        obs={'mode':'shadow','fallback':'primary','status':typed['status'],'reason':typed['reason'],'result':typed,
            'callerTiming':result['rows'][index]['observation']['callerTiming']}
        trace['decisionObservations'][0]['observation']=copy.deepcopy(obs)
        trace['decisionCallerAccounting']['stages'][0]['assignments'][0]['returned']={k:obs[k] for k in ('status','reason','callerTiming')}
        trace['decisionAssignmentHistory']['stages'][0]['assignments'][0]['observation']=copy.deepcopy(obs)
    transport=reseal(transport); row=transport['rows'][2]
    ready=active.ready_receipt(row,{'capturedAt':stamp(502),'metricsRaw':json.loads(raw['active-ready.json'])['activeMetricsRaw']}); drained=active.drain_receipt(row)
    retired=active.retired_receipt(spec,ready,drained,runtime_pid=101,native_pids=[101,102,103],exit_code=75,remaining=[],log_raw=raw['runtime.log'],observed_at=stamp(560))
    recovered=active.recovered_receipt(spec,retired,runtime_pid=201,profile_sha=context['profileSha256'],server_start=1001.125,
        warmup_file_sha=sha(raw['recovery-warmup.json']),applied_at=stamp(630))
    arm={'schemaVersion':integration.ARM_REQUEST_SCHEMA,'targetIndex':2,'afterCaseId':routes[1]['caseId'],'afterRunId':routes[1]['runId'],'createdAt':stamp(498)}
    armed=integration.armed_receipt(spec,sha(encoded(arm)),101,1000.125,stamp(499))
    before=old_bundle['before']; applied=copy.deepcopy(old_bundle['applied'])
    applied.update(readyFileSha256=sha(encoded(ready)),beforeTraceFileSha256=sha(encoded(before)),requestStartedAt=stamp(503),responseCompletedAt=stamp(505),cancelRequestElapsedMs=2.0)
    prepared={'schemaVersion':integration.PREPARED_SCHEMA,'targetIndex':2,'caseId':routes[2]['caseId'],'runId':routes[2]['runId'],
        'stageId':routes[2]['stageId'],'inputSha256':routes[2]['inputSha256'],'profileSha256':context['profileSha256'],
        'beforeTraceFileSha256':sha(encoded(before)),'unauthenticatedStatus':401,'preparedAt':stamp(501)}
    http=[]
    def record(path,status,body,ms):
        body=encoded(body); http.append({'method':'POST','path':path,'startedAt':stamp(ms),'finishedAt':stamp(ms+1),'startedMs':float(ms),'finishedMs':float(ms+1),
            'requestBody':body.decode(),'requestBodySha256':sha(body),'requestBodyComplete':True,'httpStatus':status})
    for index,(route,trace,wire) in enumerate(zip(routes,cohort['traces'],transport['rows'])):
        ms=100+index*200; lease='/api/v1/leases/synthetic-lease-'+str(index); assignment=trace['decisionCallerAccounting']['stages'][0]['assignments'][0]
        record(lease+'/renew',204,{},ms-20); record(lease+'/decision-shadow/intent',200,{'schemaVersion':'agat.decision.caller-accounting.v1','assignmentId':assignment['assignmentId']},ms-10)
        if index==2:
            record(lease+'/renew',404,{},544); posted=result['rows'][2]['observation']; finish=550
        else: posted={k:trace['decisionObservations'][0]['observation'][k] for k in ('result','callerTiming')}; finish=ms+6
        record(lease+'/decision-shadow',400 if index==2 else 200,posted,finish+1); record(lease+'/complete',400 if index==2 else 200,{'output':'PRIMARY_OUTPUT'},finish+3)
        if index==2: record(lease+'/fail',400,{'error':'Synthetic fixture revoked lease'},finish+5)
    artifacts={name:raw[name] for name in ('runtime.log','runtime-recovered.log','recovery-warmup.json')}
    artifacts.update({'caller-cancellation-transport.json':encoded(transport),'coordinator-cancellation-ready.json':encoded(ready),
        'coordinator-cancellation-drained.json':encoded(drained),'coordinator-cancellation-applied.json':encoded(applied),'coordinator-cancellation-http.json':encoded(http),
        'coordinator-cancellation-before.http.json':encoded(before),'coordinator-cancellation-prepared.json':encoded(prepared),
        'native-retired.json':encoded(retired),'native-recovered.json':encoded(recovered),
        'active-native-arm-request.json':encoded(arm),'active-native-armed.json':encoded(armed),'worker.log':b'Synthetic worker fixture.\n','driver.log':b'Synthetic driver fixture.\n'})
    bundle=integration.receipt_bundle(artifacts); evidence=workflow.verify_inventory(context,recipe,cohort,routes,transport,bundle)
    physical=verify_physical(context,result,transport,ready,retired,recovered,artifacts)
    recipe.update(graphSha256='a'*64,scopeEndRule='after_full_input_inventory_and_worker_drain')
    cohort.update(snapshotId='synthetic-snapshot',limits={'instances':1000,'stages':10000,'activityBytes':16777216,'exportBytes':16777216},
        populationCoverageVerified=False,eligibleWorkloadVerified=False,httpAttemptInventoryVerified=False,
        runIdsSha256=sha(json.dumps([i['runId'] for i in cohort['instances']],separators=(',',':')).encode()))
    cohort['snapshot']['dialect']='sqlite'; cohort['counts']['storedStages']=12
    driver={'status':'observed','primary':'fixture_chat_completions','primaryCalls':6,'routes':routes,'nodeVersion':'synthetic-node',
        'ownedPids':[301,302],'workerExitCode':0,'actualWindow':{k:cohort['scope'][k] for k in ('startAt','endAt')},'unauthenticatedStatus':401,'authenticatedStatus':200,
        'ownersAppointed':False,'routingEnabled':False,'qualification':'not_assessed','coordinatorCancellationReady':ready,'coordinatorCancellationDrained':drained,
        'coordinatorCancellationApplied':applied,'coordinatorCancellationPrepared':prepared,'activeNativeArmed':armed,'activeNativeRecovered':recovered}
    artifacts.update({'workflow-plan.json':encoded(recipe),'workflow-driver.json':encoded(driver),'cohort.http.json':encoded(cohort),
        'workflow-routes.jsonl':b''.join(json.dumps(r).encode()+b'\n' for r in routes)})
    plan=sealed({'schemaVersion':active.LAUNCH_PLAN,'createdAt':stamp(-1),'sourceCommit':peer_plan['sourceCommit'],'sourceFiles':{},
        'contextProfileFileSha256':sha(encoded(context)),'context':context,'config':workflow.shared_config(context),'runtime':context['tokenizerEnvironment'],
        'manifestFileSha256':context['manifestFileSha256'],'profileFileSha256':context['profileFileSha256'],'mode':'serial_closed_model_integration',
        'primary':'fixture_chat_completions','warmupCount':4,'coordinatorCancellation':spec,'coordinatorTrigger':integration.TRIGGER,
        'ownersAppointed':False,'referenceLabels':0,'sloAccepted':False,'routingEnabled':False,'qualification':'not_assessed'})
    launch=sealed({'schemaVersion':active.LAUNCH_RESULT,'status':'observed','planSha256':plan['sha256'],'coordinatorCancellation':spec,'coordinatorTrigger':integration.TRIGGER,
        'evidence':evidence,'physical':physical,'warmup':result['warmup'],'samples':result['samples'],'nativeRetired':retired,'nativeRecovered':recovered,
        'failure':None,'ownedPids':[101,102,103,201,202,203,301,302],'remainingOwnedPids':[],'cleanupErrors':[],'runtimeExitCodes':[75,130],'driverExitCode':0,
        'artifactSha256':{name:sha(raw) for name,raw in artifacts.items()},'elapsedMs':1300.0,'primary':'fixture_chat_completions','ownersAppointed':False,
        'referenceLabels':0,'classificationAccuracyMeasured':False,'sloAccepted':False,'routingEnabled':False,'qualification':'not_assessed'})
    return context,recipe,cohort,routes,transport,bundle,plan,launch,artifacts


def fixture(): return copy.deepcopy(prepared_fixture())


class ActiveWorkflowInventoryTest(unittest.TestCase):
    def test_preparation_requires_raw_pending_trace_and_precedes_fresh_active_sample(self):
        for change in ('late','raw_pin','stage','no_intent','unauthorized'):
            context,recipe,cohort,routes,transport,bundle,*_=fixture()
            if change=='late': bundle['prepared']['preparedAt']=bundle['applied']['requestStartedAt']
            elif change=='raw_pin': bundle['prepared']['beforeTraceFileSha256']='f'*64
            elif change=='stage': bundle['prepared']['stageId']='foreign-stage'
            elif change=='no_intent':
                before=json.loads(bundle['beforeRaw']); before['decisionCallerAccounting']['stages'][0]['assignments'][0]['intent']=False
                bundle['beforeRaw']=encoded(before); bundle['prepared']['beforeTraceFileSha256']=sha(bundle['beforeRaw'])
            else: bundle['prepared']['unauthenticatedStatus']=204
            with self.subTest(change=change),self.assertRaises(ValueError): workflow.verify_inventory(context,recipe,cohort,routes,transport,bundle)
    def test_native_tracker_cleanup_may_lag_parent_but_cannot_exceed_original_deadline(self):
        now=[0.0]; observed=[]; values=iter(([102],[102],[]))
        def inspect(pids): self.assertIsInstance(pids,set); observed.append(sorted(pids)); return next(values)
        def sleep(delay): self.assertGreater(delay,0); now[0]+=delay
        self.assertEqual(integration.await_native_cleanup([101,102],inspect,deadline=.1,clock=lambda:now[0],sleep=sleep),[])
        self.assertEqual(len(observed),3); self.assertLessEqual(now[0],.1)
        now[0]=0
        with self.assertRaises(ValueError): integration.await_native_cleanup([101,102],lambda _:[102],deadline=.025,clock=lambda:now[0],sleep=sleep)
        self.assertLessEqual(now[0],.025)
        with self.assertRaises(ValueError): integration.await_native_cleanup([101,102],lambda _:[999],deadline=.1)
        spec=importlib.util.spec_from_file_location('active_native_cleanup_cli',ROOT/'scripts/run-public-support-active-cancellation.py')
        cli=importlib.util.module_from_spec(spec); spec.loader.exec_module(cli); errors=[]; now[0]=0
        with patch.object(cli.runtime.shared,'inventory',side_effect=[(set(),{102}),(set(),{102}),(set(),set())]):
            self.assertEqual(integration.await_native_cleanup([101,102],lambda pids:cli.runtime.remaining_owned_processes(pids,errors),
                deadline=.1,clock=lambda:now[0],sleep=sleep),[])
        self.assertEqual(errors,[])
    def test_barrier_publishes_complete_bytes_once_and_removes_pending_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary); value={'schemaVersion':'synthetic-complete-barrier','body':'x'*32768}
            real_link=os.link
            def observe(source,target,**kwargs):
                self.assertFalse(Path(target).exists()); self.assertEqual(json.loads(Path(source).read_bytes()),value)
                return real_link(source,target,**kwargs)
            with patch.object(integration.os,'link',side_effect=observe): integration.publish_barrier(directory,'coordinator-cancellation-ready.json',value)
            self.assertEqual(json.loads((directory/'coordinator-cancellation-ready.json').read_bytes()),value)
            self.assertFalse((directory/'coordinator-cancellation-ready.pending.json').exists())
            with self.assertRaises(FileExistsError): integration.publish_barrier(directory,'coordinator-cancellation-ready.json',{'changed':True})
            self.assertEqual(json.loads((directory/'coordinator-cancellation-ready.json').read_bytes()),value)
            self.assertFalse((directory/'coordinator-cancellation-ready.pending.json').exists())
            with self.assertRaises(ValueError): integration.publish_barrier(directory,'../outside.json',value)
    def test_full_fixture_preserves_unknown_durable_return_and_interrupted_target(self):
        context,recipe,cohort,routes,transport,bundle,*_=fixture()
        with patch('http.client.HTTPConnection') as network: result=workflow.verify_inventory(context,recipe,cohort,routes,transport,bundle); network.assert_not_called()
        self.assertEqual(result['computed'],4); self.assertEqual(result['boundCallerReturns'],5); self.assertEqual(result['unknownCallerReturns'],1)
        self.assertEqual(result['physicalScheduledOutcomes'],{'ok':4,'context_rejected':1}); self.assertEqual(result['physicalScheduledPostStarts'],6)
        self.assertEqual(result['completedPhysicalScheduledPosts'],5); self.assertIsNone(result['targetTypedResult']); self.assertTrue(result['targetTerminalCounterUnknown'])
        self.assertEqual(result['localCancelledCallerMs'],50.0); self.assertEqual(result['cancelRequestToEofMs'],46.0)
    def test_missing_durable_intent_invented_return_and_protocol_mixing_fail(self):
        for change in ('return','intent','trigger','old_protocol','suffix','early_target'):
            c,p,h,r,t,b,*_=fixture()
            if change=='return': h['traces'][2]['decisionCallerAccounting']['stages'][0]['assignments'][0]['returned']={}
            elif change=='intent': h['traces'][2]['decisionCallerAccounting']['stages'][0]['assignments'][0]['intent']=False
            elif change=='trigger': p['coordinatorTrigger']={'kind':'local_caller_event'}
            elif change=='old_protocol': p['schemaVersion']='agat.decision.public-workflow-plan.v5'
            elif change=='suffix': h['instances'][3]['createdAt']=t['rows'][2]['finishedAt']
            else: h['instances'][2]['createdAt']=b['armRequest']['createdAt']
            with self.subTest(change=change),self.assertRaises(ValueError): workflow.verify_inventory(c,p,h,r,t,b)
    def test_rehashed_native_bytes_epoch_retirement_recovery_and_closed_lease_journal_fail(self):
        for change in ('target_response','native_bytes','retirement','recovery','raw_arm','foreign_lease','cancel_auth','cancel_order'):
            c,p,h,r,t,b,*_=fixture()
            if change=='target_response': t['rows'][2]['upstreamResponseBytesObserved']=1
            elif change=='native_bytes': t['rows'][0]['responseBodySha256']='0'*64
            elif change=='retirement': b['retired']['event']['reason']='inference_timeout'; b['retired']=reseal(b['retired'])
            elif change=='recovery': b['recovered']['runtimePid']=101; b['recovered']=reseal(b['recovered'])
            elif change=='raw_arm': b['rawFileSha256']['active-native-arm-request.json']='0'*64
            elif change=='foreign_lease': row=copy.deepcopy(b['http'][0]); row['path']='/api/v1/leases/foreign/renew'; b['http'].append(row)
            elif change=='cancel_auth': b['applied']['httpStatus']=200
            else: b['applied']['requestStartedAt']=t['rows'][2]['acceptedAt']
            with self.subTest(change=change),self.assertRaises(ValueError): workflow.verify_inventory(c,p,h,r,reseal(t),b)
    def test_launcher_rejects_existing_output_before_sources_or_model(self):
        spec=importlib.util.spec_from_file_location('active_workflow_cli',ROOT/'scripts/run-public-support-active-cancellation.py'); cli=importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)
        with tempfile.TemporaryDirectory() as directory,patch.object(cli.launcher,'frozen_sources') as sources,patch.object(cli.subprocess,'Popen') as child:
            self.assertEqual(cli.main(['--context-profile','missing','--context-profile-file-sha256','0'*64,'--runtime-python','missing','--manifest','missing','--evidence-dir',directory,'--cancel-at-index','2']),1)
            sources.assert_not_called(); child.assert_not_called()


class ActiveWorkflowOfflineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original=fixture(); cls.temporary=tempfile.TemporaryDirectory(); cls.root=Path(cls.temporary.name)/'sources'; cls.root.mkdir()
        names=subprocess.check_output(['git','ls-files','--',*sorted(set(workflow.SOURCE_PATHS+integration.SOURCE_PATHS+CONTEXT_PATHS))],cwd=ROOT,text=True).splitlines()
        for name in names:
            path=cls.root/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes((ROOT/name).read_bytes())
        for args in (['init','--quiet'],['add','.'],['-c','user.name=Synthetic fixture','-c','user.email=fixture@example.invalid','commit','--quiet','-m','Synthetic active workflow sources']): subprocess.run(['git',*args],cwd=cls.root,check=True)
        cls.commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=cls.root,text=True).strip()
    @classmethod
    def tearDownClass(cls): cls.temporary.cleanup()
    def pins(self,paths):
        names=subprocess.check_output(['git','ls-tree','-r','--name-only',self.commit,'--',*paths],cwd=self.root,text=True).splitlines()
        return {name:sha((self.root/name).read_bytes()) for name in names}
    def write(self,directory):
        context,*_,plan,result,artifacts=copy.deepcopy(self.original)
        context.update(sourceCommit=self.commit,sourceFiles=self.pins(CONTEXT_PATHS)); context=reseal(context)
        plan.update(sourceCommit=self.commit,sourceFiles=self.pins(workflow.SOURCE_PATHS+integration.SOURCE_PATHS),context=context,contextProfileFileSha256=sha(encoded(context))); plan=reseal(plan)
        result['planSha256']=plan['sha256']; result=reseal(result)
        for name,raw in {'context.json':encoded(context),'plan.json':encoded(plan),'result.json':encoded(result),**artifacts}.items(): (directory/name).write_bytes(raw)
        return {'context_sha':sha(encoded(context)),'plan_sha':sha(encoded(plan)),'result_sha':sha(encoded(result))}
    def test_generic_dispatch_replays_complete_v6_without_network_or_gpu_claim(self):
        with tempfile.TemporaryDirectory() as temporary,patch('http.client.HTTPConnection') as network:
            directory=Path(temporary); pins=self.write(directory); result=common_verification.verify(self.root,directory,directory/'context.json',**pins); network.assert_not_called()
            self.assertEqual(result['status'],'pass'); self.assertEqual(result['knownCompletedPhysicalCalls'],9)
            self.assertFalse(result['gpuKernelPreemptionEstablished']); self.assertFalse(result['liveCleanupVerified']); self.assertEqual(result['verificationModelCalls'],0)
    def test_raw_pins_missing_source_and_modified_counter_cannot_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary); pins=self.write(directory)
            for key in pins:
                with self.subTest(key=key),self.assertRaises(ValueError): verification.verify(self.root,directory,directory/'context.json',**{**pins,key:'0'*64})
            result=json.loads((directory/'result.json').read_bytes()); result['samples'][5]['counters']['ok']=999; result=reseal(result); (directory/'result.json').write_bytes(encoded(result))
            with self.assertRaises(ValueError): verification.verify(self.root,directory,directory/'context.json',**{**pins,'result_sha':sha(encoded(result))})
    def test_rehashed_historical_source_omission_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary); pins=self.write(directory); plan=json.loads((directory/'plan.json').read_bytes())
            plan['sourceFiles'].pop('scripts/lib/decision_public_workflow_active_integration.py'); plan=reseal(plan); (directory/'plan.json').write_bytes(encoded(plan))
            result=json.loads((directory/'result.json').read_bytes()); result['planSha256']=plan['sha256']; result=reseal(result); (directory/'result.json').write_bytes(encoded(result))
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


class ActiveWorkflowDriverTest(unittest.TestCase):
    @unittest.skipIf(os.name=='nt','Requires owned POSIX process group cleanup')
    def test_actual_coordinator_worker_revoke_active_fixture_and_wait_before_healthy_suffix(self):
        context,_,_,_,templates,_=prior.fixture(); spec=active.active_spec(context,2)
        state={'requests':[],'completed':Counter(ok=2),'active':False,'epoch':1000.125}; lock=threading.Lock(); eof=threading.Event(); stop=threading.Event()
        profile_json=json.dumps(context['profile'],ensure_ascii=False,sort_keys=True,separators=(',',':'))
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*_args): pass
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
                raw=self.rfile.read(int(self.headers['Content-Length'])); request=json.loads(raw)
                with lock: index=len(state['requests']); state['requests'].append(request)
                if index==2:
                    with lock: state['active']=True
                    self.connection.settimeout(.02); deadline=time.monotonic()+5
                    while not stop.is_set() and time.monotonic()<deadline:
                        try:
                            if self.connection.recv(1)==b'': eof.set(); return
                            raise AssertionError('Unexpected bytes after original target')
                        except socket.timeout: continue
                    raise TimeoutError('Fixture never received upstream EOF')
                typed=json.loads(templates['rows'][index]['responseBody']); typed.update(id=request['id'],durationMs=0.0)
                with lock: state['completed'][outcome(typed)]+=1
                self.respond(200 if context['inputs'][index]['contextEligible'] else 422,encoded(typed),'application/json')
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler); serving=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01}); serving.start()
        proxy=child=None
        try:
            proxy=integration.PreparedActiveCancellationProxy(server.server_port,context,spec); proxy.bind_warmups({'ok':2},1000.125)
            private=ROOT/'docs/private'; private.mkdir(mode=0o700,parents=True,exist_ok=True)
            with tempfile.TemporaryDirectory(dir=private,prefix='active-cancellation-fixture-') as temporary:
                directory=Path(temporary)
                def publish(name,value):
                    pending=directory/(name+'.pending'); pending.write_bytes(encoded(value)); pending.rename(directory/name)
                publish('plan.json',{'schemaVersion':active.LAUNCH_PLAN,'context':context,'config':workflow.shared_config(context),
                    'coordinatorCancellation':spec,'coordinatorTrigger':integration.TRIGGER})
                armed=prepared=retired=recovered=None
                with (directory/'driver.log').open('wb') as log:
                    child=subprocess.Popen(['node','--import','tsx','scripts/run-public-support-workflow.mts','--decision-url',f'http://127.0.0.1:{proxy.port}',
                        '--evidence-dir',str(directory)],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                    budget=time.monotonic()+40
                    while child.poll() is None and time.monotonic()<budget:
                        if armed is None and (directory/'active-native-arm-request.json').exists():
                            request=json.loads((directory/'active-native-arm-request.json').read_bytes()); routes=[json.loads(line) for line in (directory/'workflow-routes.jsonl').read_bytes().splitlines()]
                            integration.arm_request(context,spec,request,routes); armed=integration.armed_receipt(spec,sha((directory/'active-native-arm-request.json').read_bytes()),101,1000.125,datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z'))
                            publish('active-native-armed.json',armed)
                        if prepared is None and (directory/'coordinator-cancellation-prepared.json').exists():
                            prepared=integration.validate_preparation(context,spec,json.loads((directory/'coordinator-cancellation-prepared.json').read_bytes()),
                                (directory/'coordinator-cancellation-before.http.json').read_bytes())
                            self.assertIsNone(proxy.ready_receipt())
                            # Keep preparation out of the sample-to-cancel budget; the held fixture remains active.
                            time.sleep(.3); self.assertIsNone(proxy.ready_receipt()); proxy.prepared.set()
                        ready=proxy.ready_receipt()
                        if ready is not None and not (directory/'coordinator-cancellation-ready.json').exists(): publish('coordinator-cancellation-ready.json',ready)
                        drained=proxy.target_receipt()
                        if drained is not None and recovered is None:
                            self.assertTrue(eof.wait(1)); self.assertEqual(len(state['requests']),3)
                            publish('coordinator-cancellation-drained.json',drained)
                            event={'schemaVersion':'agat.decision.retirement.v1','eventName':'decision.backend_retired','runtimeVersion':'0.12.3',
                                'profileSha256':context['profileSha256'],'exitCode':75,'reason':'inference_cancelled','childPid':102,'childExitCode':-15}
                            runtime_log=b'Synthetic retirement fixture; no native processes or model.\n'+json.dumps(event).encode()+b'\n'
                            (directory/'runtime.log').write_bytes(runtime_log)
                            stamp=lambda:datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
                            retired=active.retired_receipt(spec,ready,drained,runtime_pid=101,native_pids=[101,102,103],exit_code=75,remaining=[],log_raw=runtime_log,observed_at=stamp())
                            publish('native-retired.json',retired)
                            publish('recovery-warmup.json',{'fixture':'Synthetic warmup pin; no native calls'})
                            with lock: state.update(completed=Counter(ok=2),active=False,epoch=1001.125)
                            recovered=active.recovered_receipt(spec,retired,runtime_pid=201,profile_sha=context['profileSha256'],server_start=1001.125,
                                warmup_file_sha=sha((directory/'recovery-warmup.json').read_bytes()),applied_at=stamp())
                            publish('native-recovered.json',recovered)
                        time.sleep(.005)
                    if child.poll() is None: os.killpg(child.pid,signal.SIGTERM); child.wait(timeout=8)
                self.assertEqual(child.returncode,0,(directory/'driver.log').read_text()[-4000:]); proxy.close(); transport=proxy.receipt()
                raw={name:(directory/name).read_bytes() for name in integration.ARTIFACTS|{'runtime.log'} if name not in {'caller-cancellation-transport.json','runtime-recovered.log'}}
                raw['runtime-recovered.log']=b'Synthetic replacement metadata; no native processes.\n'; raw['caller-cancellation-transport.json']=encoded(transport)
                driver=json.loads((directory/'workflow-driver.json').read_bytes()); cohort=json.loads((directory/'cohort.http.json').read_bytes()); recipe=json.loads((directory/'workflow-plan.json').read_bytes())
                evidence=workflow.verify_inventory(context,recipe,cohort,driver['routes'],transport,integration.receipt_bundle(raw))
                self.assertEqual(evidence['unknownCallerReturns'],1); self.assertEqual(evidence['boundCallerReturns'],5); self.assertEqual(evidence['healthySuffixCases'],3)
                self.assertEqual(len(state['requests']),6); self.assertEqual(transport['completedUpstreamPosts'],5); self.assertEqual(transport['interruptedActiveUpstreamPosts'],1)
                self.assertEqual(driver['activeNativeRecovered'],recovered); self.assertFalse((directory/'worker-credentials.json').exists())
                self.assertEqual(driver['coordinatorCancellationPrepared'],prepared)
                self.assertGreaterEqual((datetime.fromisoformat(driver['coordinatorCancellationReady']['activeObservedAt'].replace('Z','+00:00'))
                    -datetime.fromisoformat(prepared['preparedAt'].replace('Z','+00:00'))).total_seconds(),.29)
        finally:
            stop.set()
            if child is not None:
                try: os.killpg(child.pid,signal.SIGKILL)
                except ProcessLookupError: pass
                child.wait()
            if proxy is not None and not proxy.closed: proxy.close()
            server.shutdown(); server.server_close(); serving.join(2); self.assertFalse(serving.is_alive())


if __name__=='__main__': unittest.main()
