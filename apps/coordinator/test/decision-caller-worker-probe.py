"""Observe real Python lease execution; replace only primary with fixed fixture text."""
import json,os,sys,threading,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
if not (ROOT/'workers').is_dir():ROOT=Path.cwd()
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'workers'))
from agat_worker import ApiError,CoordinatorClient,LocalModelClient,_execute_lease_body
from local_decisions import LocalDecisionClient
from telemetry import ExecutionMetrics,WorkerTelemetry
settings=json.loads(sys.stdin.readline());requests=[];completed=[];checkpointed=False
def emit(kind,body):print(kind+' '+json.dumps(body),flush=True)
def checkpoint(phase):
    global checkpointed
    if not checkpointed and settings['operation']==phase:
        checkpointed=True;emit('AGAT_CALLER_WORKER_CHECKPOINT',{'pid':os.getpid(),'phase':phase})
        assert json.loads(sys.stdin.readline())=={'continue':True}
class ObservedClient(CoordinatorClient):
    def request(self,method,path,body=None,**kwargs):
        started=time.monotonic_ns();status=200
        try:return super().request(method,path,body,**kwargs)
        except ApiError as error:
            status=error.status;raise
        finally:
            row={'method':method,'path':path,'status':status,'startedNs':started,'finishedNs':time.monotonic_ns(),
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
    _execute_lease_body(client,model,lease,settings['model'],False,ExecutionMetrics.start(settings['model'],'none'),WorkerTelemetry(enabled=False))
    live=[thread.name for thread in threading.enumerate() if thread.ident not in before]
    assert live==[],f'Lease left worker threads: {live}'
    completed.append(lease['leaseId']);emit('AGAT_CALLER_WORKER_COMPLETED',{'leaseId':lease['leaseId'],'liveThreads':live})
else:raise RuntimeError('Probe stdin ended without explicit stop')
emit('AGAT_CALLER_WORKER_PROBE',{'pid':os.getpid(),'python':sys.version.split()[0],'completed':completed,
  'primaryCalls':primary_calls,'requests':requests,'liveThreads':[t.name for t in threading.enumerate() if t is not threading.main_thread()]})
