import http.client
import json
import threading
import time
import unittest

from decision_runtime.contracts import DecisionError
from decision_runtime.engine import DecisionEngine
from decision_runtime.metrics import DecisionMetrics, OUTCOMES, outcome
from decision_runtime.server import make_server
from decision_runtime.tests.test_decisions import Backend, request


def samples(text):
    """Read only the numeric samples for assertions; official parser is checked separately."""
    return {line.rsplit(' ',1)[0]:float(line.rsplit(' ',1)[1]) for line in text.splitlines() if line and not line.startswith('#')}


class MetricsTest(unittest.TestCase):
    def test_fixed_outcomes_cannot_include_document_or_exception_labels(self):
        for result in ({'status':'error','reason':'PRIVATE DOCUMENT'}, {'status':['bad'],'reason':{}},
                       {'reason':None}, {'status':'ok','reason':'PRIVATE DOCUMENT'}):
            self.assertIn(outcome(result),OUTCOMES)
        self.assertEqual(outcome({'status':'error','reason':'inference_cancelled'}),'cancelled')
        self.assertEqual(outcome({'status':'abstain'}),'abstain')

    def test_cumulative_histograms_separate_computed_rejections_and_failures(self):
        metrics=DecisionMetrics()
        for value,seconds in [('ok',.1),('abstain',.7),('busy',.001),('timeout',11.0)]:
            metrics.begin();metrics.finish(value,seconds)
        raw=metrics.render(ready=False).decode();rows=samples(raw)
        self.assertTrue(raw.endswith('\n'))
        self.assertEqual(rows['agat_decision_backend_ready'],0)
        self.assertEqual(rows['agat_decision_requests_in_progress'],0)
        self.assertEqual(rows['agat_decision_request_duration_seconds_bucket{class="computed",le="0.05"}'],0)
        self.assertEqual(rows['agat_decision_request_duration_seconds_bucket{class="computed",le="0.1"}'],1)
        self.assertEqual(rows['agat_decision_request_duration_seconds_bucket{class="computed",le="1"}'],2)
        self.assertEqual(rows['agat_decision_request_duration_seconds_count{class="computed"}'],2)
        self.assertAlmostEqual(rows['agat_decision_request_duration_seconds_sum{class="computed"}'],.8)
        self.assertEqual(rows['agat_decision_request_duration_seconds_bucket{class="failed",le="10"}'],0)
        self.assertEqual(rows['agat_decision_request_duration_seconds_bucket{class="failed",le="+Inf"}'],1)
        self.assertEqual(len(rows),46)
        reset=samples(DecisionMetrics().render(ready=True).decode())
        self.assertEqual(reset['agat_decision_requests_total{outcome="ok"}'],0)
        self.assertEqual(reset['agat_decision_request_duration_seconds_count{class="computed"}'],0)

    def test_concurrent_observations_do_not_lose_counts(self):
        metrics=DecisionMetrics()
        def work():
            for _ in range(100):metrics.begin();metrics.finish('ok',.25)
        threads=[threading.Thread(target=work) for _ in range(8)]
        for thread in threads:thread.start()
        for thread in threads:thread.join(timeout=2);self.assertFalse(thread.is_alive())
        rows=samples(metrics.render(ready=True).decode())
        self.assertEqual(rows['agat_decision_requests_total{outcome="ok"}'],800)
        self.assertEqual(rows['agat_decision_request_duration_seconds_count{class="computed"}'],800)
        self.assertEqual(rows['agat_decision_request_duration_seconds_sum{class="computed"}'],200)
        self.assertEqual(rows['agat_decision_requests_in_progress'],0)

    def test_invalid_internal_observation_cannot_add_labels_or_nonfinite_values(self):
        metrics=DecisionMetrics();metrics.begin()
        for value,seconds in [('private',1),('ok',-1),('ok',float('nan')),('ok',True)]:
            with self.assertRaises(ValueError):metrics.finish(value,seconds)
        self.assertEqual(samples(metrics.render(ready=True).decode())['agat_decision_requests_in_progress'],1)
        metrics.finish('ok',0)
        with self.assertRaises(ValueError):metrics.finish('ok',0)


class HttpMetricsTest(unittest.TestCase):
    def setUp(self):
        self.backend=Backend();self.ready=True
        self.backend.is_available=lambda:self.ready
        self.server=make_server(DecisionEngine(self.backend),0)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=2)

    def call(self,method='GET',path='/metrics',body=None,headers=None):
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        try:
            conn.request(method,path,body=body,headers={'Content-Type':'application/json',**(headers or {})})
            res=conn.getresponse();data=res.read().decode()
            self.assertEqual(res.getheader('Content-Length'),str(len(data.encode())))
            return res.status,res.getheader('Content-Type'),data
        finally:conn.close()

    def metric(self,name,expected):
        deadline=time.monotonic()+1
        while True:
            rows=samples(self.call()[2])
            if rows[name]==expected:return rows
            if time.monotonic()>=deadline:self.fail(f'{name}: {rows[name]} != {expected}')
            time.sleep(.005)

    def test_ready_metrics_do_not_run_inference_and_scrapes_are_not_decisions(self):
        status,content,raw=self.call()
        self.assertEqual(status,200);self.assertEqual(content,'text/plain; version=0.0.4; charset=utf-8')
        self.assertEqual(samples(raw)['agat_decision_backend_ready'],1)
        self.ready=False
        self.assertEqual(self.call(path='/health')[0],503)
        self.assertEqual(self.call()[0],200)
        self.assertEqual(samples(self.call()[2])['agat_decision_backend_ready'],0)
        self.assertEqual(self.backend.calls,0)
        self.assertTrue(all(v==0 for k,v in samples(self.call()[2]).items() if k.startswith('agat_decision_requests_total')))

    def test_post_outcomes_are_counted_without_source_or_request_id(self):
        private={**request(),'id':'PRIVATE_REQUEST_ID','state':'PRIVATE_STATE','question':'PRIVATE_QUESTION'}
        self.assertEqual(self.call('POST','/v1/decisions',json.dumps(private))[0],200)
        self.assertEqual(self.call('POST','/v1/decisions','{')[0],400)
        self.assertEqual(self.call('POST','/v1/decisions','{}',{'X-Agat-Decision-Profile':'0'*64})[0],409)
        self.backend.score=lambda _:(_ for _ in ()).throw(DecisionError('inference_timeout','PRIVATE_ERROR'))
        self.assertEqual(self.call('POST','/v1/decisions',json.dumps(private))[0],504)
        rows=self.metric('agat_decision_requests_total{outcome="timeout"}',1)
        for key in ('ok','invalid','profile_mismatch','timeout'):
            self.assertEqual(rows[f'agat_decision_requests_total{{outcome="{key}"}}'],1)
        self.assertEqual(rows['agat_decision_request_duration_seconds_count{class="computed"}'],1)
        self.assertEqual(rows['agat_decision_request_duration_seconds_count{class="rejected"}'],2)
        self.assertEqual(rows['agat_decision_request_duration_seconds_count{class="failed"}'],1)
        self.assertNotIn('PRIVATE',self.call()[2])

    def test_busy_and_inprogress_are_visible_while_metrics_remain_responsive(self):
        entered=threading.Event();release=threading.Event();original=self.backend.score;results=[]
        def blocked(parsed):
            entered.set();release.wait(timeout=3);return original(parsed)
        self.backend.score=blocked
        thread=threading.Thread(target=lambda:results.append(self.call('POST','/v1/decisions',json.dumps(request()))));thread.start()
        try:
            self.assertTrue(entered.wait(timeout=1))
            self.assertEqual(samples(self.call()[2])['agat_decision_requests_in_progress'],1)
            self.assertEqual(self.call('POST','/v1/decisions',json.dumps(request()))[0],503)
            rows=self.metric('agat_decision_requests_total{outcome="busy"}',1)
            self.assertEqual(rows['agat_decision_requests_in_progress'],1)
            self.assertEqual(rows['agat_decision_request_duration_seconds_count{class="computed"}'],0)
        finally:release.set();thread.join(timeout=2)
        self.assertEqual(results[0][0],200)
        self.metric('agat_decision_requests_in_progress',0)

    def test_origin_and_host_guards_also_apply_to_metrics(self):
        for header in ({'Host':'example.com'},{'Origin':'https://example.com'}):self.assertEqual(self.call(headers=header)[0],403)
        self.assertEqual(self.call(path='/metrics?state=PRIVATE')[0],404)
        self.assertEqual(self.call('POST','/metrics','{}')[0],404)
        self.assertEqual(self.backend.calls,0)


if __name__=='__main__':unittest.main()
