import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from decision_runtime.artifacts import sealed
from scripts.lib import decision_public_workflow as workflow
from scripts.lib import decision_public_workflow_loss as loss
from scripts.test import test_decision_public_workflow as fixtures
from workers.local_decisions import LocalDecisionClient, PROFILE, CALLER_TIMING_VERSION


def fixture():
    context, plan, cohort, routes = fixtures.fixture(); spec = loss.loss_spec(context, 2)
    plan.update(schemaVersion=loss.PLAN_SCHEMA, runtimeLoss=spec)
    for trace in cohort['traces'][2:]:
        observation = trace['decisionObservations'][0]['observation']; observation.pop('result')
        observation.update(status='unavailable', reason='unreachable')
        trace['decisionAssignmentHistory']['stages'][0]['assignments'][0]['observation'] = copy.deepcopy(observation)
        trace['decisionCallerAccounting']['stages'][0]['assignments'][0]['returned'].update(status='unavailable', reason='unreachable')
    request = {'schemaVersion':loss.REQUEST_SCHEMA, 'beforeIndex':2, 'afterCaseId':routes[1]['caseId'], 'afterRunId':routes[1]['runId'], 'createdAt':'2026-10-08T01:01:00.001Z'}
    applied = sealed({'schemaVersion':loss.APPLIED_SCHEMA, 'beforeIndex':2, 'requestFileSha256':'1'*64, 'runtimePid':42, 'runtimeExitCode':130,
        'runtimeExited':True, 'endpointGuardedWithTcpReset':True, 'appliedAt':'2026-10-08T01:01:00.002Z'})
    return context, plan, cohort, routes, request, applied


class PublicWorkflowLossTest(unittest.TestCase):
    def test_whole_inventory_preserves_primary_and_durable_unavailable_without_physical_calls(self):
        context, plan, cohort, routes, request, applied = fixture()
        result = workflow.verify_inventory(context, plan, cohort, routes)
        loss.verify_boundary(context, plan['runtimeLoss'], request, applied, cohort, routes)
        self.assertEqual(result['scheduled'], 6); self.assertEqual(result['boundCallerReturns'], 6)
        self.assertEqual(result['computed'], 2); self.assertEqual(result['unavailableReturns'], 4)
        self.assertEqual(result['physicalScheduledOutcomes'], {'ok':2}); self.assertEqual(result['callerMsUnavailable']['count'],4)
        self.assertFalse(result['routingEnabled']); self.assertFalse(result['ownersAppointed'])
        # A formerly over-context input remains unadmitted, not a fabricated rejection.
        self.assertFalse(context['inputs'][-1]['contextEligible']); self.assertNotIn('context_rejected',result['physicalScheduledOutcomes'])

    def test_v1_and_unsupported_or_posthoc_faults_cannot_accept_unavailability(self):
        for change in (lambda p: p.update(schemaVersion=workflow.PLAN_SCHEMA), lambda p:p['runtimeLoss'].update(beforeIndex=True),
                       lambda p:p['runtimeLoss'].update(restart=0), lambda p:p['runtimeLoss'].update(beforeIndex=0),
                       lambda p:p['runtimeLoss'].update(beforeIndex=6), lambda p:p['runtimeLoss'].update(extra='ignored')):
            context, plan, cohort, routes, _, _ = fixture(); change(plan)
            with self.assertRaises(ValueError): workflow.verify_inventory(context, plan, cohort, routes)
        context, plan, cohort, routes = fixtures.fixture(); plan.update(schemaVersion=loss.PLAN_SCHEMA,runtimeLoss=loss.loss_spec(context,2))
        with self.assertRaises(ValueError): workflow.verify_inventory(context,plan,cohort,routes)

    def test_unavailable_result_reason_missing_intent_and_changed_primary_fail(self):
        for mutate in (lambda t,r:t[2]['decisionObservations'][0]['observation'].update(result={}),
                       lambda t,r:t[2]['decisionObservations'][0]['observation'].update(reason='disabled'),
                       lambda t,r:t[2]['decisionCallerAccounting']['stages'][0]['assignments'][0].update(intent=False),
                       lambda t,r:r[2].update(primaryBranch=False), lambda t,r:r.pop()):
            context, plan, cohort, routes, _, _ = fixture(); mutate(cohort['traces'],routes)
            with self.assertRaises(ValueError): workflow.verify_inventory(context,plan,cohort,routes)

    def test_loss_barrier_rejects_wrong_completed_prefix_or_acknowledgement(self):
        for mutate in (lambda r,a,c:r.update(afterRunId='other'), lambda r,a,c:r.update(beforeIndex=True),
                       lambda r,a,c:r.update(createdAt='2026-10-08T01:01:00.003Z'),
                       lambda r,a,c:a.update(endpointGuardedWithTcpReset=False), lambda r,a,c:a.update(runtimeExitCode=True),
                       lambda r,a,c:c['instances'][2].update(createdAt='2026-10-08T01:01:00.001Z')):
            context, plan, cohort, routes, request, applied = fixture(); mutate(request,applied,cohort)
            applied = sealed({k:v for k,v in applied.items() if k!='sha256'})
            with self.assertRaises(ValueError):loss.verify_boundary(context,plan['runtimeLoss'],request,applied,cohort,routes)

    def test_completed_receipt_published_exclusively_and_without_pending_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary); _,_,_,_,_,applied=fixture(); loss.publish_applied(directory,applied)
            self.assertEqual(json.loads((directory/'runtime-loss-applied.json').read_bytes()),applied)
            self.assertEqual((directory/'runtime-loss-applied.json').stat().st_mode&0o777,0o600)
            self.assertFalse((directory/'runtime-loss-applied.pending.json').exists())
            with self.assertRaises(FileExistsError):loss.publish_applied(directory,applied)
            self.assertEqual(json.loads((directory/'runtime-loss-applied.json').read_bytes()),applied)

    @unittest.skipUnless(os.name=='posix','Owned POSIX process session required')
    def test_actual_owned_stop_leaves_other_listener_running_and_client_records_unreachable(self):
        context,_,_,_=fixtures.fixture(); server=None; reservation=None; owned=set();errors=[]
        with socket.socket() as other:
            other.bind(('127.0.0.1',0)); other.listen(); other_port=other.getsockname()[1]
            try:
                server=subprocess.Popen([sys.executable,'-B','-u','-c',
                    'import socket,time;s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);s.bind(("127.0.0.1",0));s.listen();print(s.getsockname()[1],flush=True);time.sleep(30)'],
                    stdout=subprocess.PIPE,start_new_session=True,text=True)
                port=int(server.stdout.readline())
                reservation=loss.stop_and_reserve(server,port,fixtures.cli.runtime,owned,errors)
                self.assertIsNotNone(server.poll());self.assertEqual(errors,[])
                with socket.create_connection(('127.0.0.1',other_port),timeout=1):pass
                request=context['inputs'][0]['request']
                response=LocalDecisionClient(f'http://127.0.0.1:{port}').decide({'profile':PROFILE,'profileSha256':context['profileSha256'],
                    'request':request,'timeoutMs':10000,'callerTimingVersion':CALLER_TIMING_VERSION})
                self.assertEqual(response['status'],'unavailable');self.assertEqual(response['reason'],'unreachable')
                self.assertNotIn('result',response);self.assertLess(response['callerTiming']['durationMs'],10000)
                self.assertEqual(reservation.receipt(),{'schemaVersion':'agat.decision.public-workflow-reset-guard.v1','acceptedConnections':1,'resetConnections':1,'errors':[],'payloadsRead':False})
                with self.assertRaises(ValueError):loss.stop_and_reserve(server,port,fixtures.cli.runtime,owned,errors)
                with socket.socket() as replacement, self.assertRaises(OSError):replacement.bind(('127.0.0.1',port))
            finally:
                if reservation is not None:reservation.close()
                if server is not None:
                    if server.poll() is None:server.kill();server.wait(5)
                    server.stdout.close()

    def test_existing_output_or_bad_boundary_rejected_before_native_process(self):
        with tempfile.TemporaryDirectory() as temporary:
            args=['--context-profile','missing','--context-profile-file-sha256','0'*64,'--runtime-python','missing','--manifest','missing',
                  '--evidence-dir',temporary,'--stop-runtime-before-index','5']
            with patch.object(fixtures.cli.subprocess,'Popen') as process:
                self.assertEqual(fixtures.cli.main(args),1);process.assert_not_called()


if __name__=='__main__':unittest.main()
