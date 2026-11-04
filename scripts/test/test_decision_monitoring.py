import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.lib.decision_monitoring import PrometheusObservation, summarize_snapshot, vector


INSTANCE = '127.0.0.1:38766'


def fixture():
    rows = []
    def sample(name,value=0,**labels):
        rows.append({'metric':{'__name__':name,'job':'agat-decision','instance':INSTANCE,**labels},'value':[1234567890,str(value)]})
    sample('agat_decision_backend_ready',1)
    sample('agat_decision_requests_in_progress')
    sample('agat_decision_server_start_time_seconds',1234567880)
    for outcome in ('ok','abstain','busy','invalid','profile_mismatch','context_rejected','backend_error','timeout','cancelled','unavailable'):
        sample('agat_decision_requests_total',outcome=outcome)
    for kind in ('computed','rejected','failed'):
        for upper in ('0.05','0.1','0.25','0.5','1','2','5','10','+Inf'):
            sample('agat_decision_request_duration_seconds_bucket',**{'class':kind,'le':upper})
        sample('agat_decision_request_duration_seconds_sum',**{'class':kind})
        sample('agat_decision_request_duration_seconds_count',**{'class':kind})
    return {'status':'success','data':{'resultType':'vector','result':rows}}


class MonitoringDataTest(unittest.TestCase):
    def test_full_scrape_has_bounded_labels_and_zero_initial_counters(self):
        summary = summarize_snapshot(fixture(),INSTANCE)
        self.assertEqual(summary['seriesCount'],46)
        self.assertEqual(summary['histogramCounts'],{'computed':0,'rejected':0,'failed':0})
        self.assertEqual(sum(summary['requests'].values()),0)
        self.assertEqual(summary['backendReady'],1)

    def test_foreign_target_document_labels_or_duplicate_series_fail(self):
        for change in ('foreign','private','duplicate','missing'):
            body = fixture();rows = body['data']['result']
            if change == 'foreign': rows[0]['metric']['instance'] = 'foreign:1234'
            elif change == 'private': rows[0]['metric']['request_id'] = 'private'
            elif change == 'duplicate': rows[1] = copy.deepcopy(rows[0])
            else: rows.pop()
            with self.subTest(change=change), self.assertRaises(RuntimeError): summarize_snapshot(body,INSTANCE)

    def test_nonfinite_negative_fractional_and_invalid_gauge_values_fail(self):
        for index,value in ((0,'NaN'),(0,'2'),(1,'-1'),(1,'0.5'),(2,'0'),(3,'-1'),(3,'0.5')):
            body = fixture();body['data']['result'][index]['value'][1] = value
            with self.subTest(index=index,value=value), self.assertRaises(RuntimeError): summarize_snapshot(body,INSTANCE)

    def test_counter_and_histogram_must_agree(self):
        body = fixture();body['data']['result'][3]['value'][1] = '1'
        with self.assertRaisesRegex(RuntimeError,'Counter/histogram mismatch'): summarize_snapshot(body,INSTANCE)

    def test_buckets_must_be_cumulative_and_end_at_count(self):
        body = fixture();body['data']['result'][13]['value'][1] = '1'
        with self.assertRaisesRegex(RuntimeError,'cumulative histogram'): summarize_snapshot(body,INSTANCE)
        body = fixture();body['data']['result'][13]['metric']['le'] = '0.075'
        with self.assertRaisesRegex(RuntimeError,'histogram buckets'): summarize_snapshot(body,INSTANCE)

    def test_prometheus_three_normalized_bucket_labels_preserve_numeric_boundaries(self):
        body = fixture()
        for row in body['data']['result']:
            labels = row['metric']
            if labels.get('le') in {'1','2','5','10'}: labels['le'] += '.0'
        self.assertEqual(summarize_snapshot(body,INSTANCE)['seriesCount'],46)

    def test_numerically_duplicate_bucket_boundaries_are_rejected(self):
        body = fixture();body['data']['result'][17]['metric']['le'] = '0.50'
        with self.assertRaisesRegex(RuntimeError,'histogram buckets'): summarize_snapshot(body,INSTANCE)

    def test_api_errors_warnings_and_wrong_result_types_are_rejected(self):
        for body in ({'status':'error'}, {'status':'success','warnings':['partial'],'data':{'resultType':'vector','result':[]}},
                     {'status':'success','data':{'resultType':'matrix','result':[]}}):
            with self.subTest(body=body), self.assertRaises(RuntimeError): vector(body)

    def test_reset_waits_for_the_new_server_even_if_old_counters_look_ready(self):
        monitor = PrometheusObservation.__new__(PrometheusObservation)
        monitor.instance = INSTANCE;monitor.snapshots = [{'summary':{'serverStartTime':1234567880}}]
        old = fixture();new = fixture();new['data']['result'][2]['value'][1] = '1234567890'
        with patch.object(monitor,'wait_value'), patch.object(monitor,'api',side_effect=[old,new]), patch('scripts.lib.decision_monitoring.time.sleep'):
            summary = monitor.capture('ready-2',0,new_start=True)
        self.assertEqual(summary['serverStartTime'],1234567890)
        self.assertEqual(len(monitor.snapshots),2)

    def test_missing_scrape_is_bounded_and_does_not_become_healthy(self):
        monitor = PrometheusObservation.__new__(PrometheusObservation);monitor.instance = INSTANCE
        empty = {'status':'success','data':{'resultType':'vector','result':[]}}
        with patch.object(monitor,'api',return_value=empty), patch('scripts.lib.decision_monitoring.time.monotonic',side_effect=[0,0,13]), patch('scripts.lib.decision_monitoring.time.sleep'):
            with self.assertRaisesRegex(RuntimeError,'within 12 seconds'): monitor.wait_value('up',1)


class MonitoringLifecycleTest(unittest.TestCase):
    def test_binary_sha_is_checked_before_any_process_or_version_probe(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary);binary = root/'prometheus';binary.write_bytes(b'foreign executable');binary.chmod(0o700)
            pin = root/'docs/qualification/local-decisions/shadow/observability/prometheus-3.13.4.json';pin.parent.mkdir(parents=True)
            pin.write_text(json.dumps({'schemaVersion':'agat.prometheus-release.v1','binariesSha256':{'prometheus':'a'*64}}))
            with patch('scripts.lib.decision_monitoring.subprocess.run') as run, patch('scripts.lib.decision_monitoring.subprocess.Popen') as spawn:
                with self.assertRaisesRegex(RuntimeError,'binary SHA mismatch'): PrometheusObservation(root,root,binary,binary)
                run.assert_not_called();spawn.assert_not_called()

    def test_wrong_version_or_platform_is_rejected_despite_matching_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary);binary = root/'prometheus';binary.write_bytes(b'fixture executable');binary.chmod(0o700)
            pin = root/'docs/qualification/local-decisions/shadow/observability/prometheus-3.13.4.json';pin.parent.mkdir(parents=True)
            pin.write_text(json.dumps({'schemaVersion':'agat.prometheus-release.v1','version':'3.13.4','platform':'darwin/arm64',
                                      'binariesSha256':{'prometheus':hashlib.sha256(binary.read_bytes()).hexdigest()}}))
            for output in ('prometheus, version 3.13.3 (revision fixture)\n  platform:         darwin/arm64',
                           'prometheus, version 3.13.4 (revision fixture)\n  platform:         linux/arm64'):
                with self.subTest(output=output), patch('scripts.lib.decision_monitoring.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=output,stderr='')):
                    with self.assertRaisesRegex(RuntimeError,'version/platform mismatch'): PrometheusObservation(root,root,binary,binary)

    def test_cleanup_verifies_that_the_owned_prometheus_pid_disappeared(self):
        monitor = PrometheusObservation.__new__(PrometheusObservation)
        process = unittest.mock.Mock(pid=1234,returncode=0);process.poll.return_value = None
        monitor.process = process;monitor.log = None;monitor.checks = {'prometheusStopped':False}
        with patch('scripts.lib.decision_monitoring.os.kill',side_effect=ProcessLookupError): monitor.close()
        process.terminate.assert_called_once();process.wait.assert_called_once_with(timeout=10)
        self.assertTrue(monitor.checks['prometheusStopped'])
        with patch('scripts.lib.decision_monitoring.os.kill'), self.assertRaisesRegex(RuntimeError,'remains'): monitor.close()


if __name__ == '__main__': unittest.main()
