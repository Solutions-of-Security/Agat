"""Actual coordinator/worker matched fixtures and corruption checks; no model calls."""
import copy
import base64
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from decision_runtime.artifacts import sealed
from decision_runtime.contracts import Request
from decision_runtime.metrics import OUTCOMES, outcome
from scripts.lib import decision_public_real_primary as diagnostic
from scripts.lib import decision_public_real_primary_verification as verification
from scripts.lib.decision_public_load_verification import CONTEXT_PATHS, counters
from scripts.test import test_decision_public_workflow as fixture

ROOT = Path(__file__).resolve().parents[2]


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, separators=(',', ':'))+'\n').encode()


@unittest.skipUnless(shutil.which('node') and (ROOT/'node_modules/tsx').exists(), 'Requires installed Node diagnostic dependencies')
class MatchedWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls, suite=diagnostic):
        if hasattr(cls, 'artifacts'): return
        diagnostic = suite; cls.suite = suite
        context, _, cohort, _ = fixture.fixture(); cls.context = context
        cls.primary_calls = []; cls.native_calls = []
        typed = {c['inputSha256']: t['decisionObservations'][0]['observation']['result'] for c, t in zip(context['inputs'], cohort['traces'])}
        paired = suite.PROTOCOL['workerConcurrency'] == 2; native_gate = threading.Lock(); counts = Counter({outcome(typed[context['inputs'][0]['inputSha256']]): 2})
        progress = [0]; metrics_origin = time.time()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def respond(self, status, value):
                body = value.encode() if isinstance(value, str) else encoded(value); self.send_response(status)
                self.send_header('Content-Type', 'text/plain' if isinstance(value, str) else 'application/json')
                self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
            def do_GET(self):
                if self.path == '/health':
                    self.respond(200, {'status': 'ready', 'mode': 'shadow', 'profileSha256': context['profileSha256'],
                        'profileJson': json.dumps(context['profile'], ensure_ascii=False, sort_keys=True, separators=(',', ':'))})
                elif paired and self.path == '/metrics':
                    self.respond(200, '\n'.join(f'agat_decision_requests_total{{outcome="{k}"}} {counts[k]}' for k in OUTCOMES)
                        +f'\nagat_decision_backend_ready 1\nagat_decision_requests_in_progress {progress[0]}\nagat_decision_server_start_time_seconds {metrics_origin}\n')
                else: self.send_error(404)
            def do_POST(self):
                value = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if self.path == '/api/chat':
                    cls.primary_calls.append(value)
                    if paired: time.sleep(.6)
                    indices = [i for i, c in enumerate(context['inputs']) if c['request']['state'] in value['messages'][1]['content']]
                    assert len(indices) == 1
                    self.respond(200, {'model': 'qwen3:8b', 'done': True, 'done_reason': 'stop',
                        'message': {'role': 'assistant', 'content': '  FIXTURE_PRIMARY_'+str(indices[0])+'  '},
                        'prompt_eval_count': 100, 'eval_count': 4, 'total_duration': 1000000, 'load_duration': 0,
                        'prompt_eval_duration': 500000, 'eval_duration': 500000})
                elif self.path == '/v1/decisions':
                    request = Request.from_dict(value); cls.native_calls.append(request)
                    if paired and not native_gate.acquire(blocking=False):
                        counts['busy'] += 1
                        self.respond(503, {**context['profile'], 'id': None, 'mode': 'shadow', 'inputSha256': None, 'status': 'error', 'reason': 'busy',
                            'selectedOptionId': None, 'value': None, 'distribution': []}); return
                    try:
                        if paired: progress[0] = 1; time.sleep(.3)
                        result = copy.deepcopy(typed[request.input_sha256]); result['id'] = request.id; result['durationMs'] = 0.0
                        if paired: counts[outcome(result)] += 1; progress[0] = 0
                        self.respond(200 if result['status'] in ('ok', 'abstain') else 422, result)
                    finally:
                        if paired: native_gate.release()
                else: self.send_error(404)
        servers = [ThreadingHTTPServer(('127.0.0.1', 0), Handler) for _ in range(2)]
        threads = [threading.Thread(target=s.serve_forever, kwargs={'poll_interval': .01}) for s in servers]
        for thread in threads: thread.start()
        private = ROOT/'docs/private'; private.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.TemporaryDirectory(dir=private, prefix='real-primary-fixture-') as temporary:
                directory = Path(temporary)
                plan = {'schemaVersion': diagnostic.PLAN_SCHEMA, 'createdAt': (datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(),
                    'context': context, 'config': diagnostic.shared_config(context), 'primary': {'model': 'qwen3:8b', 'generation': diagnostic.GENERATION},
                    'protocol': diagnostic.PROTOCOL}
                (directory/'plan.json').write_bytes(encoded(plan))
                # Force the initial scheduling timer to wake early; scoring must
                # still wait for the prospective wall-clock census boundary.
                early_timer = '''const timer = globalThis.setTimeout; let fired = false;
globalThis.setTimeout = (callback, delay, ...args) => {
  if (!fired && delay > 500 && delay <= 1000) {
    fired = true; console.log("fixture: early start timer");
    return timer(callback, 0, ...args);
  }
  return timer(callback, delay, ...args);
};'''
                timer_import = 'data:text/javascript;base64,'+base64.b64encode(early_timer.encode()).decode()
                result = subprocess.run(['node', '--import', 'tsx', '--import', timer_import, diagnostic.DRIVER_PATH,
                    '--decision-url', f'http://127.0.0.1:{servers[0].server_port}', '--primary-url', f'http://127.0.0.1:{servers[1].server_port}',
                    '--evidence-dir', str(directory)], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
                if result.returncode != 0:
                    raise AssertionError(result.stdout.decode()[-5000:]+'\n'+(directory/'worker.log').read_text()[-3000:])
                assert b'fixture: early start timer' in result.stdout
                assert not (directory/'worker-credentials.json').exists()
                cls.artifacts = {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()}
                cls.recipe = json.loads(cls.artifacts['workflow-plan.json']); cls.driver = json.loads(cls.artifacts['workflow-driver.json'])
                cls.artifacts['primary.log'] = b'fixture: no native inference\n' + (b'-np 2\nn_seq_max = 2\nn_ctx = 65536\nn_ctx_seq = 32768\n' if paired else b'')
                diagnostic.verify_inventory(context, diagnostic.PROTOCOL, cls.recipe, cls.driver, cls.artifacts)
        finally:
            for server in servers: server.shutdown(); server.server_close()
            for thread in threads: thread.join(2)

    def check(self, artifacts=None, recipe=None, driver=None, protocol=None):
        return diagnostic.verify_inventory(self.context, protocol or diagnostic.PROTOCOL, recipe or self.recipe, driver or self.driver, artifacts or self.artifacts)

    def mutate_rows(self, name, mutate):
        data = copy.deepcopy(self.artifacts); rows = diagnostic.journal(data[name]); mutate(rows)
        data[name] = b''.join(encoded(row) for row in rows); return data

    def test_full_original_inputs_real_worker_both_conditions_and_exact_output(self):
        evidence = self.check()
        self.assertEqual(evidence['actualWorkflows'], 12); self.assertEqual(evidence['primaryOutputsPreserved'], 12)
        self.assertEqual(evidence['matchedPrimaryRequests'], 6); self.assertEqual(evidence['matchedOutputPairs'], 6)
        self.assertEqual(evidence['shadowOutcomes'], {'ok': 5, 'context_rejected': 1})
        self.assertEqual(len(self.primary_calls), 12); self.assertEqual(len(self.native_calls), 6)
        self.assertEqual([r.input_sha256 for r in self.native_calls], [c['inputSha256'] for c in self.context['inputs']])
        self.assertFalse(evidence['classificationAccuracyMeasured']); self.assertFalse(evidence['sloAccepted'])

    def test_early_timer_wake_does_not_start_workflow_before_census(self):
        first = self.driver['routes'][0]
        self.assertGreaterEqual(datetime.fromisoformat(first['startedAt']), datetime.fromisoformat(self.recipe['startAt']))
        self.assertEqual(self.check()['actualWorkflows'], 12)

    def test_missing_primary_response_and_repeated_call_fail(self):
        for mutate in (lambda r: r.pop(), lambda r: r.append(copy.deepcopy(r[0]))):
            with self.assertRaises(ValueError): self.check(self.mutate_rows('primary-http.jsonl', mutate))

    def test_matching_response_hash_cannot_hide_changed_native_output(self):
        def alter(rows):
            value = json.loads(rows[0]['nativeResponseBody']); value['message']['content'] = 'ALTERED'
            rows[0]['nativeResponseBody'] = encoded(value).decode(); rows[0]['nativeResponseBodySha256'] = hashlib.sha256(rows[0]['nativeResponseBody'].encode()).hexdigest()
        with self.assertRaises(ValueError): self.check(self.mutate_rows('primary-http.jsonl', alter))

    def test_matching_native_request_hash_cannot_hide_changed_input_or_context(self):
        for key, value in (('messages', [{'role': 'user', 'content': 'short replacement'}]), ('options', {'num_ctx': 8192})):
            def alter(rows):
                body = json.loads(rows[0]['nativeRequestBody']); body[key] = value
                rows[0]['nativeRequestBody'] = encoded(body).decode(); rows[0]['nativeRequestBodySha256'] = hashlib.sha256(rows[0]['nativeRequestBody'].encode()).hexdigest()
            with self.subTest(field=key), self.assertRaises(ValueError): self.check(self.mutate_rows('primary-http.jsonl', alter))

    def test_truncation_warning_fails_even_if_counts_and_outputs_match(self):
        data = copy.deepcopy(self.artifacts); data['primary.log'] += b'WARN truncating input prompt limit=8192\n'
        with self.assertRaises(ValueError): self.check(data)

    def test_reordered_counterbalanced_control_is_rejected(self):
        def swap(rows): rows[0], rows[1] = rows[1], rows[0]
        with self.assertRaises(ValueError): self.check(self.mutate_rows('primary-http.jsonl', swap))

    def test_missing_durable_intent_and_wrong_complete_output_are_rejected(self):
        def missing(rows): rows[:] = [r for r in rows if not r['path'].endswith('/decision-shadow/intent')]
        def altered(rows):
            row = next(r for r in rows if r['path'].endswith('/complete')); body = json.loads(row['requestBody']); body['output'] = 'ALTERED'
            row['requestBody'] = encoded(body).decode(); row['requestBodySha256'] = hashlib.sha256(row['requestBody'].encode()).hexdigest()
        for mutate in (missing, altered):
            with self.assertRaises(ValueError): self.check(self.mutate_rows('coordinator-http.jsonl', mutate))

    def test_rehashed_wrong_raw_return_and_intent_assignment_fail(self):
        for suffix in ('/decision-shadow/intent', '/decision-shadow'):
            def alter(rows):
                row = next(r for r in rows if r['path'].endswith(suffix)); value = json.loads(row['requestBody'])
                if suffix.endswith('/intent'): value['assignmentId'] = 'changed-assignment'
                else: value['result']['reason'] = 'altered-reason'
                row['requestBody'] = encoded(value).decode(); row['requestBodySha256'] = hashlib.sha256(row['requestBody'].encode()).hexdigest()
            with self.subTest(path=suffix), self.assertRaises(ValueError): self.check(self.mutate_rows('coordinator-http.jsonl', alter))

    def test_unknown_or_revoked_durable_return_fails_after_trace_rehash(self):
        data = copy.deepcopy(self.artifacts); driver = copy.deepcopy(self.driver)
        route = next(r for r in driver['routes'] if r['condition'] == 'shadow'); trace = json.loads(data[route['traceFile']])
        trace['decisionCallerAccounting']['stages'][0]['assignments'][0]['outcome'] = 'intent_pending'
        data[route['traceFile']] = encoded(trace); route['traceFileSha256'] = hashlib.sha256(data[route['traceFile']]).hexdigest()
        data['workflow-routes.jsonl'] = b''.join(encoded(r) for r in driver['routes'])
        with self.assertRaises(ValueError): self.check(data, driver=driver)

    def test_posthoc_protocol_cannot_change_retry_and_context(self):
        for key, value in (('retryCount', 1), ('primaryContextLength', 8192), ('workerConcurrency', 2)):
            protocol = copy.deepcopy(diagnostic.PROTOCOL); protocol[key] = value
            with self.subTest(field=key), self.assertRaises(ValueError): self.check(protocol=protocol)


@unittest.skipUnless(shutil.which('node') and (ROOT/'node_modules/tsx').exists(), 'Requires installed Node diagnostic dependencies')
class ReplayVerificationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls, suite=diagnostic, actor=MatchedWorkflowTest):
        diagnostic = suite; cls.suite = suite
        actor.setUpClass(); cls.temporary = tempfile.TemporaryDirectory(); cls.root = Path(cls.temporary.name)/'sources'; cls.root.mkdir()
        paths = list(dict.fromkeys([*diagnostic.SOURCE_PATHS, *CONTEXT_PATHS]))
        names = set(subprocess.check_output(['git', 'ls-files', '--', *paths], cwd=ROOT, text=True).splitlines())
        names.update(p for p in paths if (ROOT/p).is_file()); names.add(Path(suite.__file__).relative_to(ROOT).as_posix())
        for name in names:
            target = cls.root/name; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes((ROOT/name).read_bytes())
        for args in (['init', '--quiet'], ['add', '.'], ['-c', 'user.name=Synthetic fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '--quiet', '-m', 'Synthetic real-primary receipt sources']):
            subprocess.run(['git', *args], cwd=cls.root, check=True)
        commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=cls.root, text=True).strip()
        def pins(paths):
            names = subprocess.check_output(['git', 'ls-files', '--', *paths], cwd=cls.root, text=True).splitlines()
            return {name: hashlib.sha256((cls.root/name).read_bytes()).hexdigest() for name in names}
        context = copy.deepcopy(actor.context); context.update(sourceCommit=commit, sourceFiles=pins(CONTEXT_PATHS))
        context = sealed({k: v for k, v in context.items() if k != 'sha256'}); artifacts = copy.deepcopy(actor.artifacts)
        recipe = json.loads(artifacts['workflow-plan.json']); start = datetime.fromisoformat(recipe['startAt'].replace('Z', '+00:00'))
        native_origin = (start-timedelta(milliseconds=500)).timestamp(); created = (start-timedelta(seconds=1)).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
        if hasattr(suite, 'metrics'):
            native_origin = suite.metrics(json.loads(artifacts['admission-metrics.jsonl'].splitlines()[0])['metricsRaw'])[0]
            created = datetime.fromtimestamp(native_origin-.5, timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
        elif hasattr(suite, 'native_counters'):
            native_origin = suite.native_counters(json.loads(artifacts['native-background-metrics.jsonl'].splitlines()[0])['metricsRaw'])[1]
            created = datetime.fromtimestamp(native_origin-.5, timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
        primary_pin = json.loads((ROOT/diagnostic.primary.PRIMARY_SOURCES[2]).read_text())
        profile = {'model': 'qwen3:8b', 'manifestSha256': diagnostic.primary.DIGEST, 'blobCount': 5, 'blobBytes': 5_225_000_000,
            'release': primary_pin, 'releaseFileSha256': hashlib.sha256((ROOT/diagnostic.primary.PRIMARY_SOURCES[2]).read_bytes()).hexdigest(),
            'generation': diagnostic.GENERATION, 'settings': diagnostic.SETTINGS, 'warmupRequest': diagnostic.WARMUP_REQUEST}
        authority = {'referenceLabels': 0, 'classificationAccuracyMeasured': False, 'ownersAppointed': False, 'sloAccepted': False, 'routingEnabled': False, 'qualification': 'not_assessed'}
        plan = sealed({'schemaVersion': diagnostic.PLAN_SCHEMA, 'createdAt': created, 'sourceCommit': commit, 'sourceFiles': pins(diagnostic.SOURCE_PATHS),
            'contextProfileFileSha256': hashlib.sha256(encoded(context)).hexdigest(), 'context': context, 'config': diagnostic.shared_config(context),
            'runtime': context['tokenizerEnvironment'], 'manifestFileSha256': context['manifestFileSha256'], 'primary': profile, 'protocol': diagnostic.PROTOCOL, **authority})
        primary_response = json.loads(diagnostic.journal(artifacts['primary-http.jsonl'])[0]['nativeResponseBody'])
        artifacts['primary-warmup.json'] = encoded({'request': diagnostic.WARMUP_REQUEST, 'response': primary_response, 'wallMs': 1})
        for name, when in (('primary-before.json', created), ('primary-after.json', actor.driver['actualWindow']['endAt'])):
            artifacts[name] = encoded({'capturedAt': when, 'version': {'version': '0.35.1'}, 'tags': {'models': [{'name': 'qwen3:8b', 'digest': diagnostic.primary.DIGEST}]},
                'residence': {'models': [{'digest': diagnostic.primary.DIGEST, 'context_length': 32768}]}, 'ownedPids': [43]})
        artifacts['runtime.log'] = b'synthetic native snapshot fixture\n'; artifacts['driver.log'] = b'synthetic primary diagnostic fixture\n'
        first = context['inputs'][0]
        if suite.PROTOCOL['workerConcurrency'] == 2: raw = copy.deepcopy(fixture.fixture()[2]['traces'][0]['decisionObservations'][0]['observation']['result'])
        else: raw = json.loads(diagnostic.journal(artifacts['decision-http.jsonl'])[0]['responseBody'])
        raw['id'] = first['id']
        warmup = [{'iteration': i, 'caseId': first['id'], 'status': raw['status'], 'observation': {'result': raw}} for i in range(2)]
        counts = Counter(); samples = []; health = {'status': 'ready', 'mode': 'shadow', 'profileJson': json.dumps(context['profile'], sort_keys=True, separators=(',', ':')), 'profileSha256': context['profileSha256']}
        for label, elapsed, batch in (('ready_before_scoring', 1, []), ('after_warmup', 2, [raw, raw]),
            ('after_inventory', 3, [json.loads(r['responseBody']) for r in diagnostic.journal(artifacts['decision-http.jsonl'])])):
            counts.update(outcome(r) for r in batch)
            metrics = '\n'.join(f'agat_decision_requests_total{{outcome="{k}"}} {counts[k]}' for k in OUTCOMES)+f'\nagat_decision_backend_ready 1\nagat_decision_requests_in_progress 0\nagat_decision_server_start_time_seconds {native_origin}\n'
            sample = {'label': label, 'elapsedMs': elapsed, 'health': health, 'metricsRaw': metrics, 'ownedPids': [42], 'processRaw': 'synthetic PID fixture'}
            parsed, origin = counters(sample); sample.update(counters=parsed, serverStart=origin); samples.append(sample)
        cls.context = context; cls.plan = plan; cls.artifacts = {n: b for n, b in artifacts.items() if n in diagnostic.ARTIFACTS or n.startswith('trace-')}
        owned_pids = [42, 43, *actor.driver['ownedPids']]
        cls.artifacts['owned-pids.jsonl'] = encoded({'recordedAt': created, 'ownedPids': owned_pids})
        cls.result = sealed({'schemaVersion': diagnostic.RESULT_SCHEMA, 'status': 'observed', 'planSha256': plan['sha256'],
            'evidence': diagnostic.verify_inventory(context, diagnostic.PROTOCOL, recipe, actor.driver, cls.artifacts),
            'warmup': warmup, 'samples': samples, 'failure': None, 'ownedPids': owned_pids,
            'remainingOwnedPids': [], 'cleanupErrors': [], 'runtimeExitCode': -15, 'primaryExitCode': -15, 'driverExitCode': 0,
            'artifactSha256': {n: hashlib.sha256(b).hexdigest() for n, b in cls.artifacts.items()}, 'elapsedMs': 10000, **authority})
        cls().replay()

    @classmethod
    def tearDownClass(cls): cls.temporary.cleanup()

    def replay(self, plan=None, result=None, artifacts=None):
        plan = copy.deepcopy(plan or self.plan); result = copy.deepcopy(result or self.result); artifacts = artifacts or self.artifacts
        plan = sealed({k: v for k, v in plan.items() if k != 'sha256'}); result['planSha256'] = plan['sha256']
        result = sealed({k: v for k, v in result.items() if k != 'sha256'})
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for name, body in {**artifacts, 'context.json': encoded(self.context), 'plan.json': encoded(plan), 'result.json': encoded(result)}.items(): (directory/name).write_bytes(body)
            return verification.verify(self.root, directory, directory/'context.json', suite=self.suite, context_sha=hashlib.sha256(encoded(self.context)).hexdigest(),
                plan_sha=hashlib.sha256(encoded(plan)).hexdigest(), result_sha=hashlib.sha256(encoded(result)).hexdigest())

    def test_full_replay_uses_no_network_or_model(self):
        with patch('http.client.HTTPConnection') as network:
            report = self.replay(); network.assert_not_called()
        self.assertEqual(report['status'], 'pass'); self.assertEqual(report['modelCallsDuringVerification'], 0); self.assertEqual(report['nativeCalls'], 8)

    def test_rehashed_plan_after_native_origin_fails(self):
        plan = copy.deepcopy(self.plan); plan['createdAt'] = datetime.fromtimestamp(self.result['samples'][0]['serverStart']+.1, timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
        with self.assertRaises(ValueError): self.replay(plan=plan)

    def test_rehashed_extra_native_call_and_epoch_drift_fail(self):
        for drift in (False, True):
            result = copy.deepcopy(self.result); sample = result['samples'][-1]
            if drift: sample['metricsRaw'] = sample['metricsRaw'].replace(str(sample['serverStart']), str(sample['serverStart']+1)); sample['serverStart'] += 1
            else:
                old = sample['counters']['ok']; sample['metricsRaw'] = sample['metricsRaw'].replace(f'outcome="ok"}} {old}', f'outcome="ok"}} {old+1}'); sample['counters']['ok'] += 1
            with self.subTest(epoch=drift), self.assertRaises(ValueError): self.replay(result=result)

    def test_omitted_measurement_source_and_primary_settings_fail(self):
        for field in ('sourceFiles', 'primary'):
            plan = copy.deepcopy(self.plan)
            if field == 'sourceFiles': del plan['sourceFiles']['scripts/run-public-support-real-primary.mts']
            else: plan['primary']['generation']['options']['num_ctx'] = 8192
            with self.subTest(field=field), self.assertRaises(ValueError): self.replay(plan=plan)

    def test_reported_unknown_cleanup_and_unpinned_body_fail(self):
        result = copy.deepcopy(self.result); result['remainingOwnedPids'] = [42]
        with self.assertRaises(ValueError): self.replay(result=result)
        artifacts = copy.deepcopy(self.artifacts); artifacts['primary-http.jsonl'] += b'{}\n'
        with self.assertRaises(ValueError): self.replay(artifacts=artifacts)


class LauncherFailureTest(unittest.TestCase):
    def exercise(self, unknown_cleanup=False):
        spec = importlib.util.spec_from_file_location('real_primary_cli_fixture', ROOT/'scripts/run-public-support-real-primary.py')
        cli = importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)
        context, _, _, _ = fixture.fixture()
        private = ROOT/'docs/private'; private.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=private, prefix='real-primary-launcher-failure-') as temporary:
            root = Path(temporary); manifest = root/'manifest.json'; manifest.write_bytes(b'synthetic manifest; no model\n')
            context['manifestFileSha256'] = hashlib.sha256(manifest.read_bytes()).hexdigest()
            context = sealed({k: v for k, v in context.items() if k != 'sha256'}); context_path = root/'context.json'; context_path.write_bytes(encoded(context))
            directory = root/'run'; process = Mock(pid=888888888, returncode=1); process.poll.return_value = 1
            def remaining(_owned, errors):
                if unknown_cleanup: errors.append('inventory:SyntheticFailure'); return None
                return []
            with patch.object(cli, 'historical_context_sources'), patch.object(cli.launcher, 'frozen_sources', return_value=('0'*40, {})), \
                patch.object(cli, 'verify_manifest', return_value=({}, None)), patch.object(cli, 'verify_profile'), \
                patch.object(cli.subprocess, 'check_output', return_value=encoded(context['tokenizerEnvironment'])), \
                patch.object(cli.diagnostic, 'prepare', return_value={'fixture': 'no model'}), patch.object(cli.subprocess, 'Popen', return_value=process) as child, \
                patch.object(cli.runtime, 'stop_owned_process') as stop, patch.object(cli.runtime, 'remaining_owned_processes', side_effect=remaining), \
                patch.object(cli.runtime, 'request') as network:
                status = cli.main(['--context-profile', str(context_path), '--context-profile-file-sha256', hashlib.sha256(context_path.read_bytes()).hexdigest(),
                    '--runtime-python', sys.executable, '--manifest', str(manifest), '--primary-binaries', str(root/'missing-binaries'),
                    '--primary-models', str(root/'missing-models'), '--evidence-dir', str(directory)])
                self.assertEqual(status, 1); child.assert_called_once(); network.assert_not_called(); stop.assert_called_once()
            result = json.loads((directory/'result.json').read_text()); self.assertEqual(result['status'], 'measurement_error')
            self.assertEqual(result['failure']['type'], 'ValueError'); self.assertEqual(result['ownedPids'], [888888888])
            self.assertEqual(result['remainingOwnedPids'], None if unknown_cleanup else [])
            self.assertIn('owned-pids.jsonl', result['artifactSha256']); self.assertIsNone(result['evidence'])

    def test_startup_failure_still_writes_failure_receipt_and_owned_pid_ledger(self): self.exercise()
    def test_unknown_cleanup_is_preserved_as_unknown_and_cannot_pass(self): self.exercise(unknown_cleanup=True)


if __name__ == '__main__':
    unittest.main()
