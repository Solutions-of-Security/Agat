import copy
import itertools
import json
import threading
import unittest
from unittest.mock import patch

from decision_runtime.artifacts import sealed, verify_seal
from scripts.lib.decision_baselines import BaselineError
from scripts.lib.decision_shared_load import PHASES, SCHEMA, benchmark_shared, summarize
from scripts.test.test_decision_baselines import dataset
from scripts.test.test_decision_performance import Fixture


class Primary:
    def __init__(self):
        self.model = "test-only"
        self.identity = {"digest": "1" * 64, "provider": "fixture"}
        self.requests = []
        self.changed = False
    def tag(self): return {"digest": "2" * 64 if self.changed else self.identity["digest"]}
    def predict(self, request):
        self.requests.append(request.to_dict())
        return {"selectedOptionId": request.options[0].id, "inputTokens": 100, "outputTokens": 12}
    def transport(self, _method, _path):
        return {"models": [{"name": self.model, "digest": self.identity["digest"], "size": 100, "size_vram": 80}]}


def sized_dataset(count):
    data = dataset(); data.pop('sha256'); original = data['cases'][0]
    data['cases'] = []
    for index in range(count):
        case = copy.deepcopy(original)
        case['id'] = case['request']['id'] = f'fixture-bounded-{index}'
        data['cases'].append(case)
    return sealed(data)


class SharedTest(unittest.TestCase):
    def run_probe(self, fixture=None, primary=None, **kwargs):
        fixture = fixture or Fixture(); primary = primary or Primary()
        return benchmark_shared(dataset(), "http://127.0.0.1:1", lambda: primary, warmup=1,
                                client=fixture, health_transport=fixture.health, **kwargs)

    def test_separate_sequential_and_overlapping_calls_are_distinct_and_do_not_send_gold(self):
        entered_primary, entered_decision = threading.Event(), threading.Event()
        class ConcurrentPrimary(Primary):
            def predict(self, request):
                result = super().predict(request)
                if len(self.requests) == 4:
                    entered_primary.set()
                    if not entered_decision.wait(1): raise RuntimeError("Overlap was serialized")
                return result
        class ConcurrentDecision(Fixture):
            def decide(self, shadow):
                if self.calls == 3:
                    entered_decision.set()
                    if not entered_primary.wait(1): raise RuntimeError("Overlap was serialized")
                return super().decide(shadow)
        primary, fixture = ConcurrentPrimary(), ConcurrentDecision()
        report = self.run_probe(fixture, primary)
        verify_seal(report, SCHEMA)
        self.assertEqual(report["status"], "observed")
        self.assertEqual([p["name"] for p in report["phases"]], list(PHASES))
        self.assertEqual(len(report["warmup"]), 2)
        self.assertEqual(sum(len(p["rows"]) for p in report["phases"]), 8)
        self.assertEqual(report["phases"][2]["pairs"][0]["requestOverlapMs"], 0)
        self.assertGreater(report["phases"][3]["pairs"][0]["requestOverlapMs"], 0)
        self.assertEqual(report["repeatDecisionChanges"], {"primary": [], "decision": []})
        self.assertNotIn("GOLD NEVER SENT", json.dumps(primary.requests + fixture.requests))
        self.assertNotIn("expectedOptionId", json.dumps(primary.requests + fixture.requests))
        self.assertNotIn("Input only", json.dumps(report))
        self.assertFalse(report["qualifiedForRouting"])

    def test_invalid_data_and_plans_fail_before_contacting_services(self):
        fixture, calls = Fixture(), []
        def primary(): calls.append(True); return Primary()
        data = dataset(); data.pop("sha256"); data["cases"][0]["split"] = "holdout"
        with self.assertRaises(ValueError):
            benchmark_shared(sealed(data), "http://127.0.0.1:1", primary, client=fixture, health_transport=fixture.health)
        for plan in ({"rounds": 3}, {"warmup": 0}, {"time_budget_s": 601}, {"timeout_ms": 10}):
            with self.assertRaises(ValueError):
                benchmark_shared(dataset(), "http://127.0.0.1:1", primary, client=fixture, health_transport=fixture.health, **plan)
        self.assertEqual((fixture.health_calls, fixture.calls, calls), (0, 0, []))

    def test_two_rounds_support_the_declared_thirty_case_bound(self):
        for count in (16, 30):
            with self.subTest(cases=count):
                fixture, primary = Fixture(), Primary()
                report = benchmark_shared(sized_dataset(count), 'http://127.0.0.1:1',
                                          lambda: primary, rounds=2, warmup=2,
                                          client=fixture, health_transport=fixture.health,
                                          stop_on_failure=True, expected_profile=fixture.engine.profile())
                self.assertEqual((report['status'], report['stoppedReason']), ('observed', None))
                measured = sum(len(phase['rows']) for phase in report['phases'])
                self.assertEqual((measured, report['plan']['expectedMeasuredRequests']), (count * 16, count * 16))
                self.assertEqual((fixture.calls, len(primary.requests)), (count * 8 + 2, count * 8 + 2))
                self.assertEqual(report['plan']['maxConcurrentCallsPerModel'], 1)
                self.assertFalse(report['plan']['retry'])

    def test_thirty_one_cases_fail_before_health_or_primary_creation(self):
        fixture = Fixture(); primary_calls = []
        with self.assertRaisesRegex(ValueError, 'Unsupported or excessive'):
            benchmark_shared(sized_dataset(31), 'http://127.0.0.1:1',
                             lambda: primary_calls.append(True), rounds=2,
                             client=fixture, health_transport=fixture.health)
        self.assertEqual((fixture.calls, fixture.health_calls, primary_calls), (0, 0, []))

    def test_failures_and_busy_are_not_successful_fast_latency_samples(self):
        rows = [{"status": "ok", "reason": "accepted", "wallMs": 500, "inputTokens": 10, "outputTokens": 0},
                {"status": "unavailable", "reason": "busy", "wallMs": 1}]
        result = summarize(rows)
        self.assertEqual(result["computedWallMs"]["p95"], 500)
        self.assertEqual(result["failedWallMs"]["p95"], 1)
        fixture = Fixture(); fixture.busy = True
        report = self.run_probe(fixture)
        self.assertEqual(report["status"], "degraded")
        self.assertTrue(any(p["summary"]["decision"]["failures"].get("busy") for p in report["phases"]))

    def test_changed_or_unavailable_final_profile_retains_evidence_as_degraded(self):
        fixture = Fixture(); fixture.changed = True
        self.assertFalse(self.run_probe(fixture)["decisionProfileStable"])
        class Lost(Fixture):
            def health(self, *args):
                if self.health_calls: raise BaselineError("unreachable")
                return super().health(*args)
        report = self.run_probe(Lost())
        self.assertEqual(report["status"], "degraded")
        self.assertEqual(len(report["phases"]), 6)
        primary = Primary(); primary.changed = True
        self.assertFalse(self.run_probe(primary=primary)["primaryStable"])

    def test_unknown_backend_exception_does_not_leak_input_or_secrets(self):
        class Broken(Primary):
            def predict(self, request): raise BaselineError("PRIVATE SOURCE CONTENT")
        report = self.run_probe(primary=Broken())
        self.assertEqual(report["status"], "degraded")
        self.assertNotIn("PRIVATE SOURCE", json.dumps(report))

    def test_time_budget_preserves_partial_evidence_without_new_inference(self):
        primary, fixture = Primary(), Fixture()
        with patch("scripts.lib.decision_shared_load.time.perf_counter", side_effect=itertools.count()):
            report = self.run_probe(fixture, primary, time_budget_s=1)
        self.assertEqual(report["stoppedReason"], "time_budget")
        self.assertEqual((fixture.calls, len(primary.requests)), (0, 0))
        self.assertEqual(report["status"], "degraded")

    def test_journal_copies_every_completed_pair_and_cannot_mutate_report(self):
        seen=[]
        def observer(phase,index,rows):
            seen.append((phase,index,json.loads(json.dumps(rows))))
            for row in rows: row['caseId']='MUTATED'
        report=self.run_probe(pair_observer=observer)
        self.assertEqual(report['status'],'observed');self.assertEqual(len(seen),7)
        self.assertEqual([r for _,_,rows in seen for r in rows],report['warmup']+[r for p in report['phases'] for r in p['rows']])
        self.assertNotIn('MUTATED',json.dumps(report))

    def test_primary_failure_does_not_start_sequential_decision(self):
        class Broken(Primary):
            def predict(self,request): self.requests.append(request.to_dict());raise BaselineError('backend_error')
        fixture=Fixture();primary=Broken();seen=[]
        report=self.run_probe(fixture,primary,stop_on_failure=True,pair_observer=lambda *args:seen.append(args))
        self.assertEqual(report['stoppedReason'],'request_failed');self.assertEqual(report['phases'],[])
        self.assertEqual((fixture.calls,len(primary.requests),len(report['warmup'])),(0,1,1))
        self.assertEqual(len(seen),1)

    def test_first_measured_busy_is_retained_without_next_primary_or_decision(self):
        fixture=Fixture();fixture.busy=True;primary=Primary();seen=[]
        report=self.run_probe(fixture,primary,stop_on_failure=True,pair_observer=lambda *args:seen.append(args))
        self.assertEqual(report['stoppedReason'],'request_failed');self.assertEqual((fixture.calls,len(primary.requests)),(2,1))
        self.assertEqual(report['phases'][0]['rows'][0]['reason'],'busy')
        self.assertEqual(len(report['phases']),1);self.assertEqual(len(seen),2)

    def test_observer_io_failure_retains_completed_pair_and_hides_exception(self):
        def fail(*_): raise OSError('PRIVATE SOURCE CONTENT')
        fixture=Fixture();primary=Primary();report=self.run_probe(fixture,primary,pair_observer=fail)
        self.assertEqual(report['stoppedReason'],'observation_failed');self.assertEqual(len(report['warmup']),2)
        self.assertEqual((fixture.calls,len(primary.requests)),(1,1));self.assertEqual(report['phases'],[])
        self.assertNotIn('PRIVATE SOURCE',json.dumps(report))

    def test_cancel_before_warmup_never_starts_inference(self):
        fixture=Fixture();primary=Primary();report=self.run_probe(fixture,primary,cancel_requested=lambda:True)
        self.assertEqual(report['stoppedReason'],'cancelled');self.assertEqual((fixture.calls,len(primary.requests)),(0,0))

    def test_cancel_after_pair_keeps_it_and_starts_no_new_calls(self):
        state={'cancel':False};fixture=Fixture();primary=Primary()
        def observe(phase,*_):
            if phase!='warmup':state['cancel']=True
        report=self.run_probe(fixture,primary,pair_observer=observe,cancel_requested=lambda:state['cancel'])
        self.assertEqual(report['stoppedReason'],'cancelled');self.assertEqual((fixture.calls,len(primary.requests)),(2,1))
        self.assertEqual(len(report['phases'][0]['rows']),1)

    def test_cancel_observer_failure_is_degraded_without_inference(self):
        def fail():raise RuntimeError('SECRET')
        fixture=Fixture();primary=Primary();report=self.run_probe(fixture,primary,cancel_requested=fail)
        self.assertEqual(report['stoppedReason'],'observation_failed');self.assertEqual((fixture.calls,len(primary.requests)),(0,0))
        self.assertNotIn('SECRET',json.dumps(report))

    def test_bad_controls_rejected_before_health_and_primary_factory(self):
        for controls in ({'pair_observer':3},{'cancel_requested':True},{'stop_on_failure':1}):
            fixture=Fixture();calls=[]
            with self.assertRaises(ValueError):
                benchmark_shared(dataset(),'http://127.0.0.1:1',lambda:calls.append(True),client=fixture,health_transport=fixture.health,**controls)
            self.assertEqual((fixture.calls,fixture.health_calls,calls),(0,0,[]))

    def test_expected_profile_mismatch_stops_before_primary_factory_or_inference(self):
        fixture=Fixture();calls=[];profile=fixture.engine.profile();profile['runtimeVersion']='different'
        with self.assertRaisesRegex(ValueError,'frozen experiment'):
            benchmark_shared(dataset(),'http://127.0.0.1:1',lambda:calls.append(True),expected_profile=profile,
                             client=fixture,health_transport=fixture.health)
        self.assertEqual((fixture.health_calls,fixture.calls,calls),(1,0,[]))

    def test_changed_decision_stops_after_recording_the_changed_row(self):
        from decision_runtime.engine import DecisionEngine
        from decision_runtime.tests.test_decisions import Backend
        fixture=Fixture();primary=Primary();seen=[]
        def observe(phase,index,rows):
            seen.append(rows)
            if phase=='warmup':fixture.engine=DecisionEngine(Backend([0,3]))
        report=self.run_probe(fixture,primary,pair_observer=observe,stop_on_failure=True)
        self.assertEqual(report['stoppedReason'],'decision_changed')
        self.assertEqual((fixture.calls,len(primary.requests),len(seen)),(2,1,2))
        self.assertTrue(report['repeatDecisionChanges']['decision'])
        self.assertEqual(len(report['phases']),1)

    def test_overlap_failure_keeps_both_active_calls_and_starts_no_next_phase(self):
        entered_primary=threading.Event();entered_decision=threading.Event()
        class OverlapPrimary(Primary):
            def predict(self,request):
                result=super().predict(request)
                if len(self.requests)==4:
                    entered_primary.set()
                    if not entered_decision.wait(1):raise RuntimeError('Missing concurrent decision')
                return result
        class OverlapFailure(Fixture):
            def decide(self,shadow):
                if self.calls==3:
                    entered_decision.set()
                    if not entered_primary.wait(1):raise RuntimeError('Missing concurrent primary')
                    self.busy=True
                return super().decide(shadow)
        fixture=OverlapFailure();primary=OverlapPrimary()
        report=self.run_probe(fixture,primary,stop_on_failure=True)
        self.assertEqual(report['stoppedReason'],'request_failed')
        self.assertEqual((fixture.calls,len(primary.requests)),(4,4))
        self.assertEqual([p['name'] for p in report['phases']],list(PHASES[:4]))
        rows=report['phases'][-1]['rows'];self.assertEqual(len(rows),2)
        self.assertEqual(rows[1]['reason'],'busy')
        self.assertGreater(report['phases'][-1]['pairs'][0]['requestOverlapMs'],0)


if __name__ == "__main__": unittest.main()
