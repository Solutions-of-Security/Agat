"""Require real timing and persisted intent/return binding in the v5 gate."""
import copy
import importlib.util
import json
import math
import re
from pathlib import Path
import unittest
from unittest.mock import patch
from decision_runtime.contracts import Request, canonical_json, fingerprint
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('temporal_caller_verifier',ROOT/'scripts/verify-temporal-real-rag.py')
verifier=importlib.util.module_from_spec(spec);spec.loader.exec_module(verifier)


class TemporalCallerAccountingTests(unittest.TestCase):
    def setUp(self):
        self.profile={'profileJson':'{"fixture":true}'};self.profile['profileSha256']=verifier.sha(self.profile['profileJson'])
        trace={'decisionObservations':[], 'events':[],
          'decisionAssignmentHistory':{'schemaVersion':'agat.decision.shadow-assignment-inventory.v1','scope':'coordinator_shadow_assignments','stages':[]},
          'decisionCallerAccounting':{'schemaVersion':'agat.decision.caller-inventory.v1','scope':'caller_operation_intents','stages':[]}}
        self.phase={'trace':trace,'decisionCalls':[],'callerAccountingSql':[]}
        for index in range(3):
            stage=f'stage-{index}';assignment_id=f'{index+1:08x}-0000-4000-8000-000000000000'
            request=Request.from_dict({'schemaVersion':'agat.decision.v1','id':stage,'state':'fixture state','question':'Is evidence present?','kind':'boolean',
              'options':[{'id':'yes','description':'Evidence is present','value':True},{'id':'no','description':'Evidence is absent','value':False}]}).to_dict()
            timing={'schemaVersion':'agat.decision.caller-timing.v1','clock':'monotonic','boundary':'local_http_call','durationMs':20+index}
            observation={'mode':'shadow','fallback':'primary','status':'unavailable','reason':'busy','callerTiming':timing}
            assigned={'assignmentId':assignment_id,'stageAttempt':1,'profileSha256':self.profile['profileSha256'],
              'inputSha256':Request.from_dict(request).input_sha256,'callerTimeoutMs':1000,'observation':observation}
            caller={'assignmentId':assignment_id,'stageAttempt':1,'negotiated':True,'intent':True,
              'returned':{k:observation[k] for k in ('status','reason','callerTiming')}}
            activity={'decisionShadowConfig':{'profileJson':self.profile['profileJson']},
              'decisionShadowLease':{'assignmentId':assignment_id,'callerAccountingVersion':'agat.decision.caller-accounting.v1','callerTimingVersion':'agat.decision.caller-timing.v1',
                'profileSha256':self.profile['profileSha256'],'inputSha256':assigned['inputSha256'],'timeoutMs':1000,'request':request},
              'decisionShadowAssignmentHistory':{'schemaVersion':'agat.decision.shadow-assignment-history.v1','coverage':'complete','assignments':[assigned]},
              'decisionShadowCallerAccounting':{'schemaVersion':'agat.decision.caller-accounting.v1','coverage':'complete','assignments':[caller]},'decisionShadowObservation':observation}
            self.phase['callerAccountingSql'].append({'id':stage,'status':'completed','attempt':1,'activity_json':canonical_json(activity)})
            self.phase['decisionCalls'].append({'stageId':stage,'request':request})
            trace['events'].append({'type':'decision.shadow'})
            trace['decisionObservations'].append({'stageId':stage,'profileSha256':assigned['profileSha256'],'inputSha256':assigned['inputSha256'],'callerTimeoutMs':1000,'observation':copy.deepcopy(observation)})
            trace['decisionAssignmentHistory']['stages'].append({'stageId':stage,'coverage':'complete','assignments':[{**copy.deepcopy(assigned),'outcome':'recorded'}]})
            trace['decisionCallerAccounting']['stages'].append({'stageId':stage,'coverage':'complete','assignments':[{**copy.deepcopy(caller),'outcome':'returned'}]})

    def mutate_sql(self,phase,change):
        row=phase['callerAccountingSql'][0];activity=json.loads(row['activity_json']);change(activity);row['activity_json']=canonical_json(activity)

    def test_three_complete_receipts_are_bound_to_sql_request_and_profile(self):
        self.assertEqual(verifier.verify_caller_accounting(self.phase,self.profile),{'assignments':3,'intents':3,'returns':3,'knownCallerTimings':3,'unknownReturns':0})

    def test_inventory_fixture_matches_the_actual_coordinator_contract(self):
        source=(ROOT/'apps/coordinator/src/decision-caller-accounting.ts').read_text()
        exported=re.search(r"DECISION_CALLER_INVENTORY\s*=\s*['\"]([^'\"]+)['\"]",source).group(1)
        self.assertEqual(self.phase['trace']['decisionCallerAccounting']['schemaVersion'],exported)

    def test_missing_duplicate_or_foreign_sql_stage_is_rejected(self):
        for change in (lambda p:p['callerAccountingSql'].pop(),lambda p:p['callerAccountingSql'].append(p['callerAccountingSql'][0]),
                       lambda p:p['callerAccountingSql'][0].update(id='foreign')):
            phase=copy.deepcopy(self.phase);change(phase)
            with self.subTest(change=change),self.assertRaises((AssertionError,KeyError)):verifier.verify_caller_accounting(phase,self.profile)

    def test_lost_intent_changed_return_and_profile_input_substitution_are_rejected(self):
        changes=(lambda a:a['decisionShadowCallerAccounting']['assignments'][0].update(intent=False),
          lambda a:a['decisionShadowCallerAccounting']['assignments'][0].update(returned=None),
          lambda a:a['decisionShadowCallerAccounting']['assignments'][0]['returned'].update(reason='changed'),
          lambda a:a['decisionShadowConfig'].update(profileJson='{"fixture":false}'),
          lambda a:a['decisionShadowLease']['request'].update(state='foreign state'))
        for change in changes:
            phase=copy.deepcopy(self.phase);self.mutate_sql(phase,change)
            with self.subTest(change=change),self.assertRaises(AssertionError):verifier.verify_caller_accounting(phase,self.profile)

    def test_timing_contract_rejects_scalar_aliases_extra_fields_and_deadline_overrun(self):
        valid=self.phase['trace']['decisionObservations'][0]['observation']['callerTiming']
        for key,value in (('durationMs',True),('durationMs',-1),('durationMs',math.nan),('durationMs',1001),('clock','wall'),('boundary','scoring'),('extra',1)):
            with self.subTest(key=key,value=value),self.assertRaises(AssertionError):verifier.verify_caller_timing({**valid,key:value},1000)
        self.assertEqual(verifier.verify_caller_timing({**valid,'durationMs':0},1000),0)

    def test_lease_requires_negotiated_capabilities_and_exact_assignment_input(self):
        for field,value in (('callerAccountingVersion','unknown'),('callerTimingVersion','unknown'),('assignmentId','foreign'),('inputSha256','a'*64)):
            phase=copy.deepcopy(self.phase);self.mutate_sql(phase,lambda a:a['decisionShadowLease'].update({field:value}))
            with self.subTest(field=field),self.assertRaises(AssertionError):verifier.verify_caller_accounting(phase,self.profile)

    def test_cli_caller_gate_requires_resource_recovery_before_side_effects(self):
        launcher=verifier.launcher_module
        for options in (['--caller-accounting'],['--caller-accounting','--shadow-python','fixture-python','--shadow-manifest','fixture-manifest','--shadow-recovery']):
            with self.subTest(options=options),patch('sys.argv',['run-temporal-real-rag.py','--evidence-dir',str(ROOT/'docs/private/uncreated-caller-test'),*options]), \
                 patch.object(launcher.subprocess,'Popen') as spawn,patch.object(launcher,'command') as command,self.assertRaises(ValueError):
                launcher.main()
            spawn.assert_not_called();command.assert_not_called()
        self.assertFalse((ROOT/'docs/private/uncreated-caller-test').exists())


if __name__=='__main__':unittest.main()
