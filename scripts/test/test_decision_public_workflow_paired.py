"""Synthetic closed receipts and real model-free paired worker/coordinator execution."""
import copy
from collections import Counter
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from decision_runtime.contracts import Request
from decision_runtime.metrics import DecisionMetrics, outcome
from scripts.lib import decision_public_workflow as workflow
from scripts.lib import decision_public_workflow_paired as paired
from scripts.lib import decision_public_workflow_verification as verification
from scripts.lib.decision_public_load_verification import CONTEXT_PATHS, counters
from scripts.test import test_decision_public_workflow as basic
from scripts.test import test_decision_public_workflow_verification as prior

ROOT=Path(__file__).resolve().parents[2]
encoded=prior.encoded; sha=prior.sha; reseal=prior.reseal


def metrics(values,active=False,epoch=1000.125):
    with patch('decision_runtime.metrics.time.time',return_value=epoch): result=DecisionMetrics()
    for key,count in values.items():
        for _ in range(count): result.begin(); result.finish(key,.005)
    if active: result.begin()
    return result.render(ready=True).decode()


def augment(context,recipe,cohort,routes):
    spec=paired.paired_spec(context); recipe.update(schemaVersion=paired.PLAN_SCHEMA,mode='paired_closed_model_integration',pairedConcurrency=spec)
    base=datetime.fromisoformat(recipe['startAt'].replace('Z','+00:00'))
    at=lambda ms:(base+timedelta(milliseconds=ms)).isoformat(timespec='milliseconds').replace('+00:00','Z')
    rows=[]; http=[]; primary=[]; batches=[]
    for index,(case,route,trace) in enumerate(zip(context['inputs'],routes,cohort['traces'])):
        start=10+(index//2)*250; busy=index%2==1; accepted=start+(20 if busy else 10); finish=start+(30 if busy else 130)
        obs=trace['decisionObservations'][0]['observation']; timing=copy.deepcopy(obs['callerTiming']); timing['durationMs']=15.0 if busy else 125.0
        if busy:
            obs={'mode':'shadow','fallback':'primary','status':'unavailable','reason':'busy','callerTiming':timing}
            trace['decisionObservations'][0]['observation']=obs
            response={**context['profile'],'id':None,'mode':'shadow','inputSha256':None,'status':'error','reason':'busy','selectedOptionId':None,'value':None,'distribution':[]}
        else:
            obs['callerTiming']=timing; response=copy.deepcopy(obs['result'])
        caller=trace['decisionCallerAccounting']['stages'][0]['assignments'][0]
        assigned=trace['decisionAssignmentHistory']['stages'][0]['assignments'][0]
        assigned['observation']=copy.deepcopy(obs); caller['returned']={k:obs[k] for k in ('status','reason','callerTiming')}
        cohort['instances'][index]['createdAt']=at(start)
        request=encoded(Request.from_dict({**case['request'],'id':route['stageId']}).to_dict()); raw_response=encoded(response)
        rows.append({'index':index,'acceptedOrdinal':index,'caseId':case['id'],'stageId':route['stageId'],'inputSha256':case['inputSha256'],
            'profileSha256':context['profileSha256'],'requestBody':request.decode(),'requestBodySha256':sha(request),'cancelOnDisconnect':True,
            'acceptedAt':at(accepted),'upstreamRequestSentAt':at(accepted),'upstreamCompletedAt':at(finish),'finishedAt':at(finish),'upstreamMs':float(finish-accepted),
            'elapsedMs':float(finish-accepted),'upstreamStatus':503 if busy else 200,'upstreamContentType':'application/json',
            'responseBody':raw_response.decode(),'responseBodySha256':sha(raw_response),'downstreamWriteCompleted':True})
        lease=f'/api/v1/leases/synthetic-{index}'
        for suffix,body,begin in (('/decision-shadow/intent',{'schemaVersion':'agat.decision.caller-accounting.v1','assignmentId':caller['assignmentId']},start+5),
            ('/decision-shadow',{k:obs[k] for k in ('status','reason','callerTiming')} if busy else {k:obs[k] for k in ('result','callerTiming')},finish+1),
            ('/complete',{'output':'PRIMARY_OUTPUT'},finish+5)):
            raw=encoded(body); http.append({'method':'POST','path':lease+suffix,'startedAt':at(begin),'finishedAt':at(begin+1),
                'startedMs':float(begin),'finishedMs':float(begin+1),'requestBody':raw.decode(),'requestBodySha256':sha(raw),'requestBodyComplete':True,'httpStatus':200})
        request=encoded({'messages':[{'role':'user','content':case['request']['state']}]})
        response=encoded({'choices':[{'message':{'role':'assistant','content':'PRIMARY_OUTPUT'}}]})
        primary.append({'index':index,'caseId':case['id'],'requestBody':request.decode(),'requestBodySha256':sha(request),'startedAt':at(start+1),
            'completedAt':at(start+2),'httpStatus':200,'responseBody':response.decode(),'responseBodySha256':sha(response)})
    for offset in range(0,len(routes),2):
        start=10+(offset//2)*250; indices=list(range(offset,min(offset+2,len(routes))))
        batches.append({'index':offset//2,'inputIndices':indices,'runIds':[routes[i]['runId'] for i in indices],
            'startedAt':at(start),'completedAt':at(start+200),'elapsedMs':200.0})
    transport=reseal({'schemaVersion':paired.TRANSPORT_SCHEMA,'spec':spec,'proxyPort':12345,'upstreamPort':12346,'startedAt':at(0),
        'closedAt':cohort['scope']['endAt'],'rows':rows,'acceptedPosts':len(rows),'completedUpstreamPosts':len(rows),'maximumActiveHandlers':2,
        'errors':[],'closed':True,'activeHandlers':0})
    artifacts={name:encoded(value) for name,value in {'paired-transport.json':transport,'paired-batches.json':batches,
        'coordinator-paired-http.json':http,'primary-http.json':primary}.items()}
    return transport,paired.receipt_bundle(artifacts),artifacts


def fixture():
    context,recipe,cohort,routes=basic.fixture(); transport,bundle,raw=augment(context,recipe,cohort,routes)
    return context,recipe,cohort,routes,transport,bundle,raw


class PairedInventoryTest(unittest.TestCase):
    def test_busy_before_context_check_has_no_typed_result_and_preserves_every_primary(self):
        context,recipe,cohort,routes,transport,bundle,_=fixture()
        with patch('http.client.HTTPConnection') as http:
            result=workflow.verify_inventory(context,recipe,cohort,routes,transport,bundle); http.assert_not_called()
        self.assertEqual(result['completedInstances'],6); self.assertEqual(result['boundCallerReturns'],6)
        self.assertEqual(result['computed'],3); self.assertEqual(result['nativeBusyRefusals'],3)
        self.assertEqual(result['physicalScheduledOutcomes'],{'ok':3,'busy':3})
        self.assertEqual(result['contextIneligibleInputsRejectedBusyBeforeContextCheck'],1)
        self.assertTrue(result['primaryRoutePreserved']); self.assertFalse(result['routingEnabled'])

    def test_busy_native_durable_primary_pair_and_http_corruptions_rejected(self):
        for change in ('status','typed','busy_id','native_status','no_overlap','pair_order','creation_skew','missing_primary','wrong_primary_input',
            'early_primary','revocation','missing_return','retry','transformed_request','extra_native','caller_budget','partial_body'):
            context,recipe,cohort,routes,transport,bundle,_=fixture()
            if change=='status': cohort['traces'][1]['decisionObservations'][0]['observation']['reason']='timeout'
            elif change=='typed': cohort['traces'][1]['decisionObservations'][0]['observation']['result']={}
            elif change=='busy_id':
                body=json.loads(transport['rows'][1]['responseBody']); body['id']=routes[1]['stageId']; raw=encoded(body)
                transport['rows'][1].update(responseBody=raw.decode(),responseBodySha256=sha(raw))
            elif change=='native_status': transport['rows'][1]['upstreamStatus']=429
            elif change=='no_overlap': transport['maximumActiveHandlers']=1
            elif change=='pair_order': bundle['batches'][0]['inputIndices'].reverse()
            elif change=='creation_skew': cohort['instances'][1]['createdAt']=cohort['scope']['endAt']
            elif change=='missing_primary': bundle['primary'].pop()
            elif change=='wrong_primary_input':
                raw=encoded({'messages':[{'role':'user','content':'Changed input'}]}); bundle['primary'][0].update(requestBody=raw.decode(),requestBodySha256=sha(raw))
            elif change=='early_primary': bundle['primary'][0]['startedAt']=recipe['startAt']
            elif change=='revocation': bundle['http'][0]['httpStatus']=404
            elif change=='missing_return': bundle['http'].pop(1)
            elif change=='retry': bundle['http'].append(copy.deepcopy(bundle['http'][1]))
            elif change=='transformed_request':
                body=json.loads(transport['rows'][0]['requestBody']); body['state']+=' changed'; raw=encoded(body)
                transport['rows'][0].update(requestBody=raw.decode(),requestBodySha256=sha(raw))
            elif change=='extra_native': transport['acceptedPosts']+=1
            elif change=='caller_budget': recipe['pairedConcurrency']['callerTimeoutMs']=250
            else: bundle['http'][0]['requestBodyComplete']=False
            transport=reseal(transport)
            with self.subTest(change=change),self.assertRaises(ValueError): workflow.verify_inventory(context,recipe,cohort,routes,transport,bundle)

    def test_active_sample_requires_busy_count_original_epoch_and_same_pair_still_active(self):
        context,recipe,_,_,transport,*_=fixture()
        sample={'capturedAt':'2026-10-08T01:01:00.060Z','metricsRaw':metrics({'ok':2,'busy':1},True),'runtimePid':42}
        sample['metricsSha256']=sha(sample['metricsRaw'].encode()); result={'activeBusySample':sample,'samples':[{'serverStart':1000.125}],'ownedPids':[42]}
        self.assertEqual(paired.verify_overlap(context,result,transport)['activeBusyOverlapPairs'],[[0,1]])
        for change in ('idle','no_busy','epoch','late','foreign_pid','raw_pin'):
            current=copy.deepcopy(result)
            if change=='idle': current['activeBusySample']['metricsRaw']=metrics({'ok':2,'busy':1})
            elif change=='no_busy': current['activeBusySample']['metricsRaw']=metrics({'ok':2},True)
            elif change=='epoch': current['samples'][0]['serverStart']=2000.125
            elif change=='late': current['activeBusySample']['capturedAt']='2026-10-08T01:01:00.200Z'
            elif change=='foreign_pid': current['activeBusySample']['runtimePid']=True
            else: current['activeBusySample']['metricsSha256']='0'*64
            if change!='raw_pin': current['activeBusySample']['metricsSha256']=sha(current['activeBusySample']['metricsRaw'].encode())
            with self.subTest(change=change),self.assertRaises(ValueError): paired.verify_overlap(context,current,transport)


class PairedReplayTest(prior.WorkflowVerificationTest):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory(); cls.root=Path(cls.temporary.name)
        paths=workflow.SOURCE_PATHS+paired.SOURCE_PATHS+CONTEXT_PATHS
        for name in subprocess.check_output(['git','ls-files','--',*sorted(set(paths))],cwd=ROOT,text=True).splitlines():
            target=cls.root/name; target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes((ROOT/name).read_bytes())
        for args in (['init','--quiet'],['add','.'],['-c','user.name=Synthetic fixture','-c','user.email=fixture@example.invalid','commit','--quiet','-m','Synthetic paired sources']):
            subprocess.run(['git',*args],cwd=cls.root,check=True)
        cls.commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=cls.root,text=True).strip()
    def setUp(self):
        super().setUp()
        self.transport,self.bundle,self.extra=augment(self.context,self.recipe,self.cohort,self.routes)
        self.plan.update(schemaVersion=paired.LAUNCH_PLAN,pairedConcurrency=paired.paired_spec(self.context),mode='paired_closed_model_integration',sourceFiles=self.pins(workflow.SOURCE_PATHS+paired.SOURCE_PATHS))
        native_epoch = datetime.fromisoformat('2026-10-08T01:00:40+00:00').timestamp()
        self.result.update(schemaVersion=paired.LAUNCH_RESULT,pairedConcurrency=self.plan['pairedConcurrency'],ownedPids=[42,43,44],
            activeBusySample={'capturedAt':'2026-10-08T01:01:00.060Z','metricsRaw':metrics({'ok':2,'busy':1},True,epoch=native_epoch),'runtimePid':42},
            evidence=workflow.verify_inventory(self.context,self.recipe,self.cohort,self.routes,self.transport,self.bundle))
        self.result['activeBusySample']['metricsSha256']=sha(self.result['activeBusySample']['metricsRaw'].encode()); self.driver['ownedPids']=[43,44]
        self.driver['pairedExecution']={k:self.plan['pairedConcurrency'][k] for k in ('workerConcurrency','schedulerMode','globalMaxConcurrency')}
        for sample,values in zip(self.result['samples'],({}, {'ok':2},{'ok':5,'busy':3})):
            sample['metricsRaw']=metrics(values,epoch=native_epoch); sample['counters'],sample['serverStart']=counters({k:v for k,v in sample.items() if k not in {'counters','serverStart'}})
    def write(self):
        pins=super().write(); result=json.loads((self.directory/'result.json').read_bytes())
        for name,raw in self.extra.items(): (self.directory/name).write_bytes(raw); result['artifactSha256'][name]=sha(raw)
        raw=encoded(reseal(result)); (self.directory/'result.json').write_bytes(raw); pins['result_sha']=sha(raw); return pins
    def test_v8_offline_report_retains_exact_physical_inventory_and_zero_inference(self):
        with patch('http.client.HTTPConnection') as http:
            report=self.verify(); http.assert_not_called()
        self.assertEqual(report['schemaVersion'],'agat.decision.public-workflow-verification.v8')
        self.assertEqual(report['verificationModelCalls'],0); self.assertEqual(report['nativeBusyRefusals'],3)
        self.assertEqual(report['physicalScheduledHttpHandlers'],6); self.assertFalse(report['liveCleanupVerified'])
    def test_plan_rehashed_after_native_origin_is_rejected(self):
        self.plan['createdAt'] = '2026-10-08T01:00:45.000Z'
        with self.assertRaisesRegex(ValueError, 'before the native origin'):
            self.verify()

    def test_rehashed_pair_source_omission_and_extra_raw_body_change_rejected(self):
        pins=self.write(); (self.directory/'primary-http.json').write_bytes(b'[]\n')
        with self.assertRaises(ValueError): verification.verify(self.root,self.directory,self.directory/'context.json',**pins)
        self.setUp(); self.plan['sourceFiles'].pop('scripts/lib/decision_public_workflow_paired.py')
        with self.assertRaises(ValueError): self.verify()


class PairedDriverTest(unittest.TestCase):
    def test_actual_two_slot_worker_busy_returns_all_original_primary_inputs_without_retry(self):
        context,_,cohort,routes,*_=fixture(); lock=threading.Lock(); admission=threading.Lock(); seen=[]
        _,_,healthy,_=basic.fixture()
        typed_by_sha={case['inputSha256']:trace['decisionObservations'][0]['observation']['result'] for case,trace in zip(context['inputs'],healthy['traces'])}
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*_): pass
            def respond(self,status,body):
                self.send_response(status); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(body)))
                self.end_headers(); self.wfile.write(body)
            def do_GET(self):
                if self.path=='/health': self.respond(200,encoded({'status':'ready','mode':'shadow','profileSha256':context['profileSha256'],'profileJson':json.dumps(context['profile'],ensure_ascii=False,sort_keys=True,separators=(',',':'))}))
                else: self.send_error(404)
            def do_POST(self):
                raw=self.rfile.read(int(self.headers['Content-Length'])); request=Request.from_dict(json.loads(raw))
                with lock: seen.append(request.input_sha256)
                if not admission.acquire(False):
                    body={**context['profile'],'id':None,'mode':'shadow','inputSha256':None,'status':'error','reason':'busy','selectedOptionId':None,'value':None,'distribution':[]}
                    self.respond(503,encoded(body)); return
                try:
                    # Explicitly synthetic delay gives deterministic worker overlap, without any model or native process.
                    time.sleep(.4); body=copy.deepcopy(typed_by_sha[request.input_sha256]); body.update(id=request.id,durationMs=0.0)
                    case=next(c for c in context['inputs'] if c['inputSha256']==request.input_sha256)
                    self.respond(200 if case['contextEligible'] else 422,encoded(body))
                finally: admission.release()
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler); serving=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01}); serving.start()
        proxy=child=None
        try:
            proxy=paired.PairedProxy(server.server_port,context,paired.paired_spec(context))
            private=ROOT/'docs/private'; private.mkdir(parents=True,exist_ok=True,mode=0o700)
            with tempfile.TemporaryDirectory(dir=private,prefix='paired-fixture-') as temporary:
                directory=Path(temporary); (directory/'plan.json').write_bytes(encoded({'schemaVersion':paired.LAUNCH_PLAN,'context':context,
                    'config':workflow.shared_config(context),'pairedConcurrency':paired.paired_spec(context)}))
                with (directory/'driver.log').open('wb') as log:
                    child=subprocess.Popen(['node','--import','tsx','scripts/run-public-support-workflow.mts','--decision-url',f'http://127.0.0.1:{proxy.port}',
                        '--evidence-dir',str(directory)],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                    try: child.wait(timeout=40)
                    except subprocess.TimeoutExpired: os.killpg(child.pid,signal.SIGTERM); child.wait(timeout=8)
                self.assertEqual(child.returncode,0,(directory/'driver.log').read_text()[-4000:]); proxy.close(); transport=proxy.receipt()
                self.assertEqual({k:transport[k] for k in ('acceptedPosts','completedUpstreamPosts','maximumActiveHandlers','errors')},
                    {'acceptedPosts':6,'completedUpstreamPosts':6,'maximumActiveHandlers':2,'errors':[]})
                raw={name:(directory/name).read_bytes() for name in paired.ARTIFACTS if name!='paired-transport.json'}; raw['paired-transport.json']=encoded(transport)
                driver=json.loads((directory/'workflow-driver.json').read_bytes()); captured=json.loads((directory/'cohort.http.json').read_bytes()); recipe=json.loads((directory/'workflow-plan.json').read_bytes())
                by_run={t['run']['id']:t for t in captured['traces']}
                for case,route in zip(context['inputs'],driver['routes']):
                    stage=by_run[route['runId']]['decisionStageInventory']['stages'][0]
                    self.assertEqual({k:stage[k] for k in ('inputSha256','profileSha256','callerTimeoutMs')},
                        {'inputSha256':case['inputSha256'],'profileSha256':context['profileSha256'],'callerTimeoutMs':10000})
                result=workflow.verify_inventory(context,recipe,captured,driver['routes'],transport,paired.receipt_bundle(raw))
                self.assertEqual(result['scheduled'],6); self.assertEqual(result['boundCallerReturns'],6); self.assertEqual(result['primaryFixtureCalls'],6)
                self.assertGreaterEqual(result['nativeBusyRefusals'],1); self.assertEqual(len(seen),len(set(seen))); self.assertEqual(len(seen),6)
                self.assertEqual(result['workerConcurrency'],2); self.assertEqual(result['workflowBatches'],3); self.assertFalse(result['runtimeRestarted'])
                self.assertFalse((directory/'worker-credentials.json').exists())
        finally:
            if child is not None:
                try: os.killpg(child.pid,signal.SIGKILL)
                except ProcessLookupError: pass
                child.wait()
            if proxy is not None and not proxy.closed: proxy.close()
            server.shutdown(); server.server_close(); serving.join(2); self.assertFalse(serving.is_alive())


if __name__=='__main__': unittest.main()
