"""Bounded native Prometheus observation of an owned loopback runtime."""

from __future__ import annotations

import hashlib
import http.client
import json
import math
import os
import socket
import subprocess
import time
from pathlib import Path
from urllib.parse import urlencode

from decision_runtime.artifacts import read_json


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def vector(body, expected_name=None):
    require(body.get('status') == 'success' and not body.get('warnings'), 'Prometheus query failed or returned warnings')
    data = body.get('data', {})
    require(data.get('resultType') == 'vector' and isinstance(data.get('result'), list), 'Expected a Prometheus vector')
    for row in data['result']:
        metric, value = row.get('metric', {}), row.get('value', [])
        require(metric.get('job') == 'agat-decision' and isinstance(metric.get('instance'), str), 'Unexpected scrape target labels')
        require(expected_name is None or metric.get('__name__') == expected_name, 'Unexpected metric name')
        require(len(value) == 2 and all(math.isfinite(float(x)) for x in value), 'Invalid Prometheus sample')
    return data['result']


def summarize_snapshot(body, instance):
    rows = vector(body)
    require(len(rows) == 46, 'Expected exactly 46 decision series')
    found = {}
    allowed = {'agat_decision_backend_ready': set(), 'agat_decision_requests_in_progress': set(),
               'agat_decision_server_start_time_seconds': set(), 'agat_decision_requests_total': {'outcome'},
               'agat_decision_request_duration_seconds_bucket': {'class','le'},
               'agat_decision_request_duration_seconds_sum': {'class'},
               'agat_decision_request_duration_seconds_count': {'class'}}
    for row in rows:
        labels = row['metric'];name = labels.get('__name__')
        require(name in allowed and labels['instance'] == instance, 'Unexpected runtime series')
        require(set(labels) == {'__name__','job','instance'} | allowed[name], 'Unexpected metric labels')
        key = (name, tuple(sorted((k,v) for k,v in labels.items() if k in allowed[name])))
        require(key not in found, 'Duplicate runtime series')
        found[key] = float(row['value'][1])
    def gauge(name):
        require((name,()) in found, 'Missing runtime gauge')
        return found[(name,())]
    requests = {dict(labels)['outcome']:v for (name,labels),v in found.items() if name == 'agat_decision_requests_total'}
    counts = {dict(labels)['class']:v for (name,labels),v in found.items() if name == 'agat_decision_request_duration_seconds_count'}
    require(set(requests) == {'ok','abstain','busy','invalid','profile_mismatch','context_rejected','backend_error','timeout','cancelled','unavailable'}, 'Unexpected request outcomes')
    require(set(counts) == {'computed','rejected','failed'}, 'Unexpected histogram classes')
    require(all(v >= 0 and v.is_integer() for v in [*requests.values(),*counts.values()]), 'Invalid counters')
    require(counts['computed'] == requests['ok']+requests['abstain'] and
            counts['rejected'] == sum(requests[x] for x in ('busy','invalid','profile_mismatch','context_rejected')) and
            counts['failed'] == sum(requests[x] for x in ('backend_error','timeout','cancelled','unavailable')), 'Counter/histogram mismatch')
    for kind, count in counts.items():
        # Prometheus 3 normalizes le labels (e.g. "1" becomes "1.0").
        # Validate numerical boundaries while retaining the original wire labels.
        entries = [(float(dict(labels)['le']),v) for (name,labels),v in found.items()
                   if name == 'agat_decision_request_duration_seconds_bucket' and dict(labels)['class'] == kind]
        buckets = dict(entries)
        require(len(entries) == len(buckets) == 9 and set(buckets) == {0.05,0.1,0.25,0.5,1,2,5,10,math.inf}, 'Unexpected histogram buckets')
        ordered = [buckets[k] for k in (0.05,0.1,0.25,0.5,1,2,5,10,math.inf)]
        require(all(v >= 0 and v.is_integer() for v in ordered) and ordered == sorted(ordered) and ordered[-1] == count,
                'Invalid cumulative histogram')
        require(found.get(('agat_decision_request_duration_seconds_sum',(('class',kind),)), -1) >= 0,
                'Invalid histogram sum')
    require(gauge('agat_decision_backend_ready') in (0,1) and gauge('agat_decision_requests_in_progress') >= 0 and
            gauge('agat_decision_requests_in_progress').is_integer() and gauge('agat_decision_server_start_time_seconds') > 0,
            'Invalid runtime gauges')
    return {'seriesCount':len(rows),'backendReady':gauge('agat_decision_backend_ready'),
            'requestsInProgress':gauge('agat_decision_requests_in_progress'),
            'serverStartTime':gauge('agat_decision_server_start_time_seconds'),
            'requests':requests,'histogramCounts':counts}


class PrometheusObservation:
    def __init__(self, root, directory, prometheus, promtool):
        self.root, self.directory = root, directory
        self.process = None;self.log = None;self.started = time.time();self.snapshots = [];self.queries = []
        self.checks = {'nativeScrape':False,'firstCounters':False,'scrapeFailureObserved':False,
                       'endpointAlertPending':False,'counterReset':False,'recoveredCounters':False,
                       'alertCleared':False,'upSequence':False,'prometheusStopped':False}
        self.release = read_json(root/'docs/qualification/local-decisions/shadow/observability/prometheus-3.13.4.json')
        require(self.release['schemaVersion'] == 'agat.prometheus-release.v1', 'Unsupported Prometheus release pin')
        self.commands = {}
        for name, path in [('prometheus',prometheus),('promtool',promtool)]:
            path = path.absolute()
            require(path.is_file() and os.access(path,os.X_OK), f'Missing executable {name}')
            require(hashlib.sha256(path.read_bytes()).hexdigest() == self.release['binariesSha256'][name], f'{name} binary SHA mismatch')
            result = subprocess.run([str(path),'--version'],capture_output=True,text=True,timeout=10)
            output = result.stdout+result.stderr
            require(result.returncode == 0 and f'version {self.release["version"]} ' in output and
                    f'platform:         {self.release["platform"]}' in output, f'{name} version/platform mismatch')
            self.commands[name] = str(path)
        self.validation = []

    def start(self, runtime_port):
        self.instance = f'127.0.0.1:{runtime_port}'
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1',0));self.port = reservation.getsockname()[1]
        observability = self.root/'docs/qualification/local-decisions/shadow/observability'
        config = self.directory/'prometheus.yml'
        # JSON strings are valid quoted YAML scalars; no shell or interpolation is used.
        config.write_text('global:\n  scrape_interval: 2s\n  evaluation_interval: 2s\nrule_files:\n  - '+json.dumps(str(observability/'alerts.yml'))+
                          '\nscrape_configs:\n  - job_name: agat-decision\n    scrape_timeout: 1s\n    metrics_path: /metrics\n    static_configs:\n      - targets: ['+json.dumps(self.instance)+']\n')
        config.chmod(0o600)
        for arguments in [('check','config',str(config)), ('test','rules',str(observability/'alerts.test.yml'))]:
            result = subprocess.run([self.commands['promtool'],*arguments],capture_output=True,text=True,timeout=30)
            self.validation.append({'arguments':list(arguments),'returncode':result.returncode,'output':result.stdout+result.stderr})
            require(result.returncode == 0, 'Pinned promtool validation failed')
        self.log = (self.directory/'prometheus.log').open('xb');os.chmod(self.log.name,0o600)
        self.process = subprocess.Popen([self.commands['prometheus'],f'--config.file={config}',
                                        f'--web.listen-address=127.0.0.1:{self.port}',
                                        f'--storage.tsdb.path={self.directory/"prometheus-data"}',
                                        '--storage.tsdb.retention.time=2h','--storage.tsdb.retention.size=128MB'],
                                       cwd=self.directory,stdin=subprocess.DEVNULL,stdout=self.log,stderr=subprocess.STDOUT)
        deadline = time.monotonic()+20
        while time.monotonic() < deadline:
            require(self.process.poll() is None, 'Prometheus exited before readiness')
            try:
                connection = http.client.HTTPConnection('127.0.0.1',self.port,timeout=2)
                try:
                    connection.request('GET','/-/ready');response = connection.getresponse();response.read()
                    if response.status == 200: return
                finally: connection.close()
            except (OSError,http.client.HTTPException): pass
            time.sleep(0.1)
        raise RuntimeError('Prometheus startup deadline exceeded')

    def api(self, path, parameters=None):
        require(self.process is not None and self.process.poll() is None, 'Prometheus is unavailable')
        connection = http.client.HTTPConnection('127.0.0.1',self.port,timeout=3)
        try:
            connection.request('GET','/api/v1/'+path+('?' + urlencode(parameters) if parameters else ''))
            response = connection.getresponse();raw = response.read(2*1024*1024+1)
            require(response.status == 200 and len(raw) <= 2*1024*1024, 'Prometheus HTTP API failed')
            body = json.loads(raw)
            require(body.get('status') == 'success' and not body.get('warnings'), 'Prometheus API error or warnings')
            self.queries.append({'at':time.time(),'path':path,'parameters':parameters,'response':body})
            return body
        finally: connection.close()

    def wait_value(self, name, value, timeout=12):
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            rows = vector(self.api('query',{'query':name+'{job="agat-decision"}'}),name)
            require(len(rows) <= 1, 'Duplicate runtime target')
            if rows and rows[0]['metric']['instance'] == self.instance and float(rows[0]['value'][1]) == value:
                return
            time.sleep(0.2)
        raise RuntimeError(f'Expected scraped {name}={value} within {timeout} seconds')

    def capture(self, phase, count, new_start=False):
        self.wait_value('up',1)
        deadline = time.monotonic()+12
        while time.monotonic() < deadline:
            body = self.api('query',{'query':'{job="agat-decision",__name__=~"agat_decision_.*"}'})
            if not body['data']['result']:
                time.sleep(0.2);continue
            summary = summarize_snapshot(body,self.instance)
            if summary['backendReady'] == 1 and summary['requestsInProgress'] == 0 and sum(summary['requests'].values()) == count:
                if new_start and summary['serverStartTime'] <= self.snapshots[0]['summary']['serverStartTime']:
                    time.sleep(0.2);continue
                require(summary['histogramCounts'] == {'computed':count,'rejected':0,'failed':0}, 'Unexpected measured outcomes')
                self.snapshots.append({'phase':phase,'at':time.time(),'summary':summary})
                return summary
            time.sleep(0.2)
        raise RuntimeError(f'Scraped counters did not match phase {phase}')

    def ready(self, second=False):
        self.capture('ready-2' if second else 'ready-1',0,new_start=second)
        self.checks['counterReset' if second else 'nativeScrape'] = True

    def scored(self, second=False):
        self.capture('scored-2' if second else 'scored-1',1,new_start=second)
        self.checks['recoveredCounters' if second else 'firstCounters'] = True
        if second:
            self.wait_alert(False)
            self.checks['alertCleared'] = True
            data = self.api('query_range',{'query':'up{job="agat-decision"}', 'start':self.snapshots[0]['at'],
                                         'end':time.time(),'step':'1s'})['data']
            require(data['resultType'] == 'matrix' and len(data['result']) == 1, 'Expected one up history')
            values = [float(v) for _,v in data['result'][0]['values']]
            require(set(values) == {0.0,1.0} and values[0] == values[-1] == 1 and 0 in values[1:-1], 'Missing healthy/down/recovered up sequence')
            self.checks['upSequence'] = True

    def wait_alert(self, pending, timeout=10):
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            alerts = self.api('alerts')['data']['alerts']
            endpoint = [a for a in alerts if a['labels'].get('alertname') == 'AgatDecisionEndpointUnavailable']
            require(len(endpoint) <= 1, 'Duplicate endpoint alert')
            if (pending and endpoint and endpoint[0]['state'] == 'pending') or (not pending and not endpoint): return
            time.sleep(0.2)
        raise RuntimeError('Endpoint alert state did not follow scrape availability')

    def failed(self):
        self.wait_value('up',0)
        targets = self.api('targets',{'state':'active'})['data']['activeTargets']
        require(len(targets) == 1 and targets[0]['scrapeUrl'] == f'http://{self.instance}/metrics' and
                targets[0]['health'] == 'down' and bool(targets[0]['lastError']), 'Scraper did not observe an endpoint failure')
        self.checks['scrapeFailureObserved'] = True
        self.wait_alert(True);self.checks['endpointAlertPending'] = True

    def close(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try: self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.process.kill();self.process.wait(timeout=5)
            try: os.kill(self.process.pid,0)
            except ProcessLookupError: self.checks['prometheusStopped'] = True
            else: raise RuntimeError('Owned Prometheus PID remains after cleanup')
        if self.log is not None: self.log.close()

    def report(self):
        return {'schemaVersion':'agat.decision.prometheus-observation.v1','release':self.release,
                'scrapeIntervalSeconds':2,'evaluationIntervalSeconds':2,'scrapeTimeoutSeconds':1,
                'pid':self.process.pid if self.process else None,
                'exitCode':self.process.returncode if self.process else None,
                'checks':self.checks,'validation':self.validation,'snapshots':self.snapshots,'queries':self.queries,
                'limitations':['Temporary native scraper; no persistent monitoring or receiver installed.',
                               'Live endpoint alert became pending and cleared before its unchanged two-minute firing delay.',
                               'Development fixture and counters are not accuracy, calibration or production SLO.']}
