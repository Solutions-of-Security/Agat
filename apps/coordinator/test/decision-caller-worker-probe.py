"""Observe real Python lease execution; replace only primary with fixed fixture text."""
import contextlib,json,os,sys,threading,time,urllib.error
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
if not (ROOT/'workers').is_dir():ROOT=Path.cwd()
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'workers'))
from agat_worker import ApiError,CoordinatorClient,LocalModelClient,_execute_lease_body
from local_decisions import LocalDecisionClient
from telemetry import ExecutionMetrics,WorkerTelemetry
settings=json.loads(sys.stdin.readline());requests=[];completed=[];checkpointed=False
print_lock=threading.Lock()
response_status=threading.local()
request_code=CoordinatorClient.request.__code__;error_code=ApiError.__init__.__code__
def observe_http_status(frame,event,_result):
    if event=='return' and frame.f_code is request_code:
        response=frame.f_locals.get('response')
        if response is not None:response_status.code=response.status
    elif event=='call' and frame.f_code is error_code:
        parent=frame.f_back
        if parent is not None and parent.f_code is request_code:
            error=parent.f_locals.get('error')
            if isinstance(error,urllib.error.HTTPError):response_status.code=error.code
sys.setprofile(observe_http_status);threading.setprofile(observe_http_status)
def emit(kind,body):
    with print_lock:print(kind+' '+json.dumps(body),file=sys.__stdout__,flush=True)
def checkpoint(phase):
    global checkpointed
    if not checkpointed and settings['operation']==phase:
        checkpointed=True;emit('AGAT_CALLER_WORKER_CHECKPOINT',{'pid':os.getpid(),'phase':phase})
        assert json.loads(sys.stdin.readline())=={'continue':True}
class ObservedClient(CoordinatorClient):
    def request(self,method,path,body=None,**kwargs):
        started=time.monotonic_ns();response_status.code=None
        try:return super().request(method,path,body,**kwargs)
        finally:
            row={'method':method,'path':path,'status':response_status.code,'responseReceived':response_status.code is not None,
                 'startedNs':started,'finishedNs':time.monotonic_ns(),
                 'threadId':threading.get_ident()}
            requests.append(row);emit('AGAT_CALLER_WORKER_REQUEST',row)
    def begin_decision_shadow(self,lease_id,assignment_id):
        checkpoint('intent');return super().begin_decision_shadow(lease_id,assignment_id)
    def record_decision_shadow(self,lease_id,observation):
        checkpoint('return');return super().record_decision_shadow(lease_id,observation)
client=ObservedClient(settings['coordinator'],settings['token'])
model=LocalModelClient('http://unused.invalid','',decision_client=LocalDecisionClient(settings['decisionUrl']))
model.retrieve_knowledge=lambda *_args,**_kwargs:''
primary_calls=[]
def primary(lease,*_args,**_kwargs):
    primary_calls.append(lease['leaseId']);return 'PRIMARY '+str(lease['run']['id'])
model.complete=primary
for line in sys.stdin:
    command=json.loads(line)
    if command=={'stop':True}:break
    assert set(command)=={'lease'}
    lease=command['lease'];before={thread.ident for thread in threading.enumerate()}
    with contextlib.redirect_stdout(sys.stderr):
        _execute_lease_body(client,model,lease,settings['model'],False,ExecutionMetrics.start(settings['model'],'none'),WorkerTelemetry(enabled=False))
    live=[thread.name for thread in threading.enumerate() if thread.ident not in before]
    assert live==[],f'Lease left worker threads: {live}'
    completed.append(lease['leaseId']);emit('AGAT_CALLER_WORKER_COMPLETED',{'leaseId':lease['leaseId'],'liveThreads':live})
else:raise RuntimeError('Probe stdin ended without explicit stop')
emit('AGAT_CALLER_WORKER_PROBE',{'pid':os.getpid(),'python':sys.version.split()[0],'completed':completed,
  'primaryCalls':primary_calls,'requests':requests,'liveThreads':[t.name for t in threading.enumerate() if t is not threading.main_thread()]})
