import copy
import io
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from scripts.lib import decision_public_workflow as workflow
from scripts.lib import decision_public_workflow_verification as verification
from scripts.lib import decision_public_workflow_loss as loss
from scripts.lib.decision_public_load_verification import CONTEXT_PATHS, counters
from scripts.test import test_decision_public_workflow_verification as original
from scripts.test import test_decision_public_workflow_loss as loss_fixtures

MEASURED='c5b92686db13c224e318d5efcd01ba4a69bc33a2'


class LossVerificationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();cls.root=Path(cls.temporary.name)
        raw=subprocess.check_output(['git','archive',MEASURED,'--',*workflow.SOURCE_PATHS,*CONTEXT_PATHS,'scripts/test/test_decision_public_workflow_loss.py'],cwd=original.ROOT)
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            for item in archive:
                if item.isfile():
                    target=cls.root/item.name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(archive.extractfile(item).read())
        for args in (['init','--quiet'],['add','.'],['-c','user.name=Synthetic fixture','-c','user.email=fixture@example.invalid','commit','--quiet','-m','Synthetic loss receipts']):
            subprocess.run(['git',*args],cwd=cls.root,check=True)
        cls.commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=cls.root,text=True).strip()
    @classmethod
    def tearDownClass(cls):cls.temporary.cleanup()
    pins=original.WorkflowVerificationTest.pins
    def setUp(self):
        original.WorkflowVerificationTest.setUp(self)
        spec=loss.loss_spec(self.context,2)
        self.plan.update(schemaVersion='agat.decision.public-workflow-launch-plan.v2',runtimeLoss=spec,
                         sourceFiles=self.pins(workflow.SOURCE_PATHS+['scripts/test/test_decision_public_workflow_loss.py']))
        self.recipe.update(schemaVersion=loss.PLAN_SCHEMA,runtimeLoss=spec)
        _,_,lost,_,self.request,self.applied=loss_fixtures.fixture()
        # Only synthetic response metadata changes; real Git source bytes are retained.
        for target,source in zip(self.cohort['traces'][2:],lost['traces'][2:]):
            observation=copy.deepcopy(source['decisionObservations'][0]['observation'])
            target['decisionObservations'][0]['observation']=observation
            target['decisionAssignmentHistory']['stages'][0]['assignments'][0]['observation']=copy.deepcopy(observation)
            target['decisionCallerAccounting']['stages'][0]['assignments'][0]['returned'].update(status='unavailable',reason='unreachable')
        self.request.update(afterRunId=self.routes[1]['runId'],afterCaseId=self.routes[1]['caseId'])
        self.applied.update(runtimePid=44,requestFileSha256=original.sha(original.encoded(self.request)))
        self.applied=original.reseal(self.applied)
        self.driver['runtimeLossApplied']=self.applied
        self.result.update(schemaVersion='agat.decision.public-workflow-launch-result.v2',runtimeLoss={'spec':spec,'applied':self.applied},
                           runtimeExitCode=130,ownedPids=[42,43,44],evidence=workflow.verify_inventory(self.context,self.recipe,self.cohort,self.routes))
        sample=self.result['samples'][-1];sample['label']='before_runtime_loss'
        sample['metricsRaw']=sample['metricsRaw'].replace('outcome="ok"} 7','outcome="ok"} 4').replace('outcome="context_rejected"} 1','outcome="context_rejected"} 0')
        sample['counters'],sample['serverStart']=counters({k:v for k,v in sample.items() if k not in {'counters','serverStart'}})
        for entry in self.result['samples']:entry['ownedPids']=[44]
        self.transport={'schemaVersion':'agat.decision.public-workflow-reset-guard.v1','acceptedConnections':4,'resetConnections':4,'errors':[],'payloadsRead':False}
    def write(self):
        pins=original.WorkflowVerificationTest.write(self)
        extras={'runtime-loss-request.json':original.encoded(self.request),'runtime-loss-applied.json':original.encoded(self.applied),
                'runtime-loss-transport.json':original.encoded(self.transport)}
        for name,raw in extras.items():(self.directory/name).write_bytes(raw)
        self.result['artifactSha256'].update({name:original.sha(raw) for name,raw in extras.items()})
        raw=original.encoded(original.reseal(self.result));(self.directory/'result.json').write_bytes(raw);pins['result_sha']=original.sha(raw)
        return pins
    def verify(self):
        return verification.verify(self.root,self.directory,self.directory/'context.json',**self.write())
    def test_complete_loss_replay_distinguishes_physical_and_transport_denominators_offline(self):
        with patch('http.client.HTTPConnection') as http, patch.object(loss,'ResetGuard') as guard:
            report=self.verify();http.assert_not_called();guard.assert_not_called()
        self.assertEqual(report['schemaVersion'],'agat.decision.public-workflow-verification.v2')
        self.assertEqual(report['physicalScheduledHttpHandlers'],2);self.assertEqual(sum(report['physicalHttpCounters'].values()),4)
        self.assertEqual(report['transportUnavailableReturns'],4);self.assertEqual(report['transportResetConnections'],4)
        self.assertEqual(report['inventory']['scheduled'],6);self.assertFalse(report['liveCleanupVerified'])
    def test_rehashed_reset_ownership_barrier_source_and_counter_corruptions_fail(self):
        for mutate in (lambda:self.transport.update(resetConnections=3),lambda:self.transport.update(payloadsRead=True),
                       lambda:self.transport.update(acceptedConnections=True),lambda:self.applied.update(runtimePid=42),
                       lambda:self.applied.update(requestFileSha256='0'*64),lambda:self.request.update(afterRunId='wrong'),
                       lambda:self.cohort['instances'][2].update(createdAt='2026-10-08T01:01:00.001Z'),
                       lambda:self.plan['sourceFiles'].pop('scripts/test/test_decision_public_workflow_loss.py'),
                       lambda:self.result['samples'][-1]['counters'].update(ok=7),
                       lambda:self.result.update(referenceLabels=False),lambda:self.routes.pop()):
            self.setUp();mutate()
            # The attacker can reseal the receipt, but cannot change its accounting contract.
            self.applied=original.reseal(self.applied);self.driver['runtimeLossApplied']=self.applied;self.result['runtimeLoss']['applied']=self.applied
            with self.assertRaises(ValueError):self.verify()
    def test_raw_reset_receipt_and_mid_replay_mutation_cannot_publish_success(self):
        pins=self.write();(self.directory/'runtime-loss-transport.json').write_bytes(b'changed')
        with self.assertRaises(ValueError):verification.verify(self.root,self.directory,self.directory/'context.json',**pins)
        pins=self.write();actual=verification.verify_boundary
        def change(*args):
            actual(*args);(self.directory/'runtime-loss-transport.json').write_bytes(b'changed after consumption')
        with patch.object(verification,'verify_boundary',side_effect=change),self.assertRaises(ValueError):
            verification.verify(self.root,self.directory,self.directory/'context.json',**pins)


if __name__=='__main__':unittest.main()
