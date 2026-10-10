"""Actual two-slot worker/socket regressions with explicitly synthetic native epochs."""
import base64
from collections import Counter
import copy
from datetime import datetime, timedelta, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import select
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from decision_runtime.artifacts import sealed
from decision_runtime.contracts import Request, parse_json
from decision_runtime.metrics import OUTCOMES, outcome
from scripts.lib import decision_two_slot_cancellation as diagnostic
from scripts.lib import decision_public_workflow_active_cancellation as active
from scripts.lib import decision_public_workflow_active_integration as integration
from scripts.lib.decision_public_load_verification import CONTEXT_PATHS
from scripts.test import test_decision_public_workflow as fixture

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('two_slot_cli_test', ROOT/'scripts/run-two-slot-native-cancellation.py')
cli = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(cli)
encoded = lambda value: (json.dumps(value, ensure_ascii=False, separators=(',', ':'))+'\n').encode()
digest = lambda raw: hashlib.sha256(raw).hexdigest()
now = lambda: datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


@unittest.skipUnless(shutil.which('node') and (ROOT/'node_modules/tsx').exists(), 'Requires Node diagnostic dependencies')
class TwoSlotActorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls, suite=diagnostic):
        if hasattr(cls, 'artifacts'): return
        diagnostic = suite
        context, _, cohort, _ = fixture.fixture(); cls.context = context; cls.protocol = diagnostic.protocol(context, 1)
        projected = diagnostic.projection(context, 1)
        typed = {c['inputSha256']: t['decisionObservations'][0]['observation']['result'] for c, t in zip(context['inputs'], cohort['traces'])}
        cls.native_calls = []; cls.eof = threading.Event(); stop = threading.Event(); lock = threading.Lock()
        first = context['inputs'][0]; warm_result = copy.deepcopy(typed[first['inputSha256']]); warm_result['id'] = first['request']['id']; warm_result['durationMs'] = 0.0
        epoch = [0]; origins = [(datetime.now(timezone.utc)-timedelta(milliseconds=500)).timestamp(), 0]
        counts = Counter({outcome(warm_result): 2}); in_progress = [0]

        def metrics():
            with lock:
                return '\n'.join(f'agat_decision_requests_total{{outcome="{key}"}} {counts[key]}' for key in OUTCOMES)+f'\nagat_decision_backend_ready 1\nagat_decision_requests_in_progress {in_progress[0]}\nagat_decision_server_start_time_seconds {origins[epoch[0]]}\n'

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def respond(self, status, value):
                body = value.encode() if isinstance(value, str) else encoded(value)
                self.send_response(status); self.send_header('Content-Type', 'text/plain' if isinstance(value, str) else 'application/json')
                self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
            def do_GET(self):
                if self.path == '/health': self.respond(200, {'status': 'ready', 'mode': 'shadow', 'profileSha256': context['profileSha256'],
                    'profileJson': json.dumps(context['profile'], sort_keys=True, separators=(',', ':'))})
                elif self.path == '/metrics': self.respond(200, metrics())
                else: self.send_error(404)
            def do_POST(self):
                assert self.path == '/v1/decisions'
                request = Request.from_dict(parse_json(self.rfile.read(int(self.headers['Content-Length']))))
                cls.native_calls.append(request); index = next(i for i, c in enumerate(context['inputs']) if c['inputSha256'] == request.input_sha256)
                if index == 1:
                    with lock: in_progress[0] = 1
                    self.connection.setblocking(False)
                    while not stop.is_set():
                        readable, _, _ = select.select([self.connection], [], [], .01)
                        if readable:
                            assert self.connection.recv(1) == b''
                            with lock: in_progress[0] = 0
                            cls.eof.set(); return
                    return
                response = copy.deepcopy(typed[request.input_sha256]); response.update(id=request.id, durationMs=0.0)
                with lock: counts[outcome(response)] += 1
                self.respond(200, response)

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler); thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}); thread.start()
        proxy = diagnostic.make_proxy(server.server_port, projected, cls.protocol['nativeFault'])
        proxy.bind_warmups({outcome(warm_result): 2}, origins[0]); samples = []; warmup = []
        native = [[42, 43, 44], [45, 46, 47]]; start = time.monotonic()
        private = ROOT/'docs/private'; private.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.TemporaryDirectory(dir=private, prefix='two-slot-fixture-') as temporary:
                directory = Path(temporary)
                def save(name, value): (directory/name).write_bytes(encoded(value))
                def sample(label):
                    raw = metrics(); values, origin = diagnostic.recovery.http_metrics(raw, in_progress=0)
                    samples.append({'label': label, 'epoch': epoch[0], 'capturedAt': now(), 'runtimePid': native[epoch[0]][0], 'metricsRaw': raw,
                        'counters': values, 'serverStart': origin, 'health': {'status': 'ready', 'mode': 'shadow', 'profileJson': json.dumps(context['profile'], sort_keys=True, separators=(',', ':')),
                        'profileSha256': context['profileSha256']}, 'ownedPids': native[epoch[0]], 'elapsedMs': (time.monotonic()-start)*1000})
                counts.clear(); sample('ready_before_scoring'); counts.update({outcome(warm_result): 2}); sample('after_warmup')
                for e in range(2):
                    warmup.extend({'epoch': e, 'iteration': i, 'caseId': first['id'], 'status': warm_result['status'], 'reason': warm_result['reason'],
                        'observation': {'result': copy.deepcopy(warm_result), 'callerTiming': {'schemaVersion': 'agat.decision.caller-timing.v1',
                            'clock': 'monotonic', 'boundary': 'local_http_call', 'durationMs': 1}}, 'callerMs': 1, 'wallMs': 1} for i in range(2))
                created = (datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
                cls.plan = {'schemaVersion': diagnostic.PLAN_SCHEMA, 'createdAt': created, 'context': context, 'config': diagnostic.shared_config(context), 'protocol': cls.protocol}
                save('plan.json', cls.plan)
                early_timer = 'const timer=globalThis.setTimeout;let fired=false;globalThis.setTimeout=(callback,delay,...args)=>{if(!fired&&delay>500&&delay<=1000){fired=true;console.log("fixture: early start timer");return timer(callback,0,...args);}return timer(callback,delay,...args);};'
                timer_import = 'data:text/javascript;base64,'+base64.b64encode(early_timer.encode()).decode()
                process = subprocess.Popen(['node', '--import', 'tsx', '--import', timer_import, diagnostic.DRIVER_PATH,
                    '--decision-url', f'http://127.0.0.1:{proxy.port}', '--evidence-dir', str(directory)], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                armed = prepared = ready = drained = recovered = None; deadline = time.monotonic()+45
                try:
                    while process.poll() is None:
                        assert time.monotonic() < deadline, 'Synthetic epoch fixture deadline'; assert not proxy.errors, proxy.errors
                        if armed is None and (directory/'native-prefix-ready.json').exists():
                            sample('before_target'); prefix = json.loads((directory/'native-prefix-ready.json').read_bytes())
                            armed = {'prefixFileSha256': digest((directory/'native-prefix-ready.json').read_bytes()), 'runtimePid': 42, 'nativePids': native[0], 'serverStartText': str(origins[0]), 'armedAt': now()}
                            cli.publish(directory, 'native-prefix-armed.json', armed)
                        if prepared is None and (directory/diagnostic.PREPARED_FILE).exists():
                            prepared = json.loads((directory/diagnostic.PREPARED_FILE).read_bytes())
                            diagnostic.validate_preparation(projected, prepared, {n: (directory/n).read_bytes() for n in ('trace-target-before.http.json', 'trace-peer-before.http.json', 'peer-primary-held.json')})
                            proxy.prepared.set()
                        if ready is None:
                            ready = proxy.ready_receipt()
                            if ready is not None: cli.publish(directory, diagnostic.READY_FILE, ready)
                        if drained is None:
                            drained = proxy.target_receipt()
                            if drained is not None:
                                assert cls.eof.wait(2), 'Synthetic upstream never received actual EOF'; cli.publish(directory, diagnostic.DRAINED_FILE, drained)
                                log = encoded({'schemaVersion': 'agat.decision.retirement.v1', 'eventName': 'decision.backend_retired', 'runtimeVersion': '0.12.3',
                                    'profileSha256': context['profileSha256'], 'exitCode': 75, 'reason': 'inference_cancelled', 'childPid': 44, 'childExitCode': -15})
                                (directory/'runtime.log').write_bytes(log)
                                retired = active.retired_receipt(cls.protocol['nativeFault'], ready, drained, runtime_pid=42, native_pids=native[0], exit_code=75, remaining=[], log_raw=log, observed_at=now())
                                cli.publish(directory, 'native-retired.json', retired)
                                epoch[0] = 1; origins[1] = time.time(); counts.clear(); sample('ready_before_scoring'); counts.update({outcome(warm_result): 2}); sample('after_warmup')
                                save('recovery-warmup.json', warmup[2:]); (directory/'runtime-recovered.log').write_bytes(b'Synthetic epochs; no GPU inference or live PID retirement.\n')
                                recovered = active.recovered_receipt(cls.protocol['nativeFault'], retired, runtime_pid=45, profile_sha=context['profileSha256'], server_start=origins[1],
                                    warmup_file_sha=digest((directory/'recovery-warmup.json').read_bytes()), applied_at=now())
                                cli.publish(directory, 'native-recovered.json', recovered)
                        time.sleep(.005)
                    output = process.communicate(timeout=2)[0]
                    if process.returncode != 0: raise AssertionError(output.decode()[-6000:]+'\n'+(directory/'worker.log').read_text()[-5000:])
                    assert b'fixture: early start timer' in output; assert not (directory/'worker-credentials.json').exists()
                    (directory/'driver.log').write_bytes(output); sample('after_inventory'); proxy.close(); save('active-transport.json', proxy.receipt())
                    driver = json.loads((directory/'workflow-driver.json').read_bytes()); cls.driver = driver; cls.recipe = json.loads((directory/'workflow-plan.json').read_bytes())
                    owned = sorted({*native[0], *native[1], *driver['ownedPids']}); save('owned-pids.jsonl', {'recordedAt': now(), 'ownedPids': owned})
                    cls.artifacts = {n: (directory/n).read_bytes() for n in diagnostic.ARTIFACTS}
                    cls.evidence = diagnostic.verify_actor(context, cls.protocol, cls.recipe, driver, cls.artifacts)
                    cls.result = {'schemaVersion': diagnostic.RESULT_SCHEMA, 'status': 'observed', 'evidence': cls.evidence, 'warmup': warmup, 'samples': samples,
                        'failure': None, 'ownedPids': owned, 'remainingOwnedPids': [], 'cleanupErrors': [], 'runtimeExitCodes': [75, 130], 'driverExitCode': 0,
                        'artifactSha256': {n: digest(b) for n, b in cls.artifacts.items()}, 'elapsedMs': (time.monotonic()-start)*1000, **diagnostic.AUTHORITY}
                    cls.result['physical'] = diagnostic.verify_physical(projected, cls.result, proxy.receipt(), ready, retired, recovered, cls.artifacts)
                finally:
                    if process.poll() is None: process.terminate(); process.communicate(timeout=10)
        finally:
            stop.set(); proxy.close(); server.shutdown(); server.server_close(); thread.join(2)

    def check(self, artifacts=None, protocol=None):
        return diagnostic.verify_actor(self.context, protocol or self.protocol, self.recipe, self.driver, artifacts or self.artifacts)

    def alter_json(self, name, mutate):
        artifacts = copy.deepcopy(self.artifacts); value = json.loads(artifacts[name]); mutate(value); artifacts[name] = encoded(value); return artifacts

    def alter_journal(self, name, mutate):
        artifacts = copy.deepcopy(self.artifacts); rows = diagnostic.journal(artifacts[name]); mutate(rows); artifacts[name] = b''.join(encoded(r) for r in rows); return artifacts

    def test_same_actual_worker_two_running_assignments_and_held_peer_output_survive(self):
        evidence = self.check()
        self.assertEqual(evidence['actualWorkflows'], 4); self.assertEqual(evidence['knownCallerReturns'], 3); self.assertEqual(evidence['unknownCallerReturns'], 1)
        self.assertTrue(evidence['peerLeasePreserved']); self.assertTrue(evidence['peerPrimaryConnectionPreserved']); self.assertTrue(self.eof.is_set())
        self.assertEqual(len(self.native_calls), 4); self.assertEqual(self.result['physical']['knownCompletedPhysicalCalls'], 7)
        self.assertFalse(evidence['classificationAccuracyMeasured']); self.assertFalse(evidence['routingEnabled'])

    def test_closed_or_already_answered_peer_socket_fails_even_after_raw_rehash(self):
        for key, value in (('socketClosedBeforeRelease', True), ('socketOpenAtRelease', False), ('responseBytesWrittenBeforeRelease', 1)):
            with self.subTest(key=key), self.assertRaises(ValueError): self.check(self.alter_json('peer-primary-released.json', lambda r: r.update({key: value})))

    def test_peer_reassigned_cancelled_or_shortened_input_fails(self):
        for mutate in (lambda t: t['run'].update(status='cancelled'), lambda t: t['run'].update(input='short replacement'),
            lambda t: next(s for s in t['run']['stages'] if s['processNodeId'] == 'agent').update(nodeId='another-worker', attempt=2)):
            with self.assertRaises(ValueError): self.check(self.alter_json('trace-peer-after-cancel.http.json', mutate))

    def test_missing_lease_intent_late_fence_and_primary_return_fail(self):
        for suffix in ('/decision-shadow/intent', '/complete'):
            with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                self.check(self.alter_journal('coordinator-http.jsonl', lambda r: r.remove(next(x for x in r if x['path'].endswith(suffix)))))
        with self.assertRaises(ValueError): self.check(self.alter_journal('coordinator-http.jsonl', lambda r: next(x for x in r if x['path'].endswith('/complete') and x['httpStatus'] == 400).update(httpStatus=200)))

    def test_wrong_cancellation_target_or_early_peer_release_fails(self):
        for name, mutate in (('coordinator-cancellation-applied.json', lambda r: r.update(runId=self.driver['routes'][2]['runId'])),
            ('peer-primary-released.json', lambda r: r.update(releasedAt=json.loads(self.artifacts['peer-primary-held.json'])['heldAt']))):
            with self.subTest(name=name), self.assertRaises(ValueError): self.check(self.alter_json(name, mutate))

    def test_fabricated_known_target_or_missing_authenticated_trace_fails(self):
        with self.assertRaises(ValueError): self.check(self.alter_json('trace-target.http.json', lambda t: t['decisionCallerAccounting']['stages'][0]['assignments'][0].update(returned={'status': 'ok'})))
        with self.assertRaises(ValueError): self.check(self.alter_journal('trace-http.jsonl', lambda r: r.pop()))

    def test_shortened_primary_with_matching_request_sha_fails(self):
        def shorten(rows):
            row = next(r for r in rows if r['index'] == 2); value = json.loads(row['requestBody']); value['messages'][1]['content'] = 'replacement'
            row['requestBody'] = encoded(value).decode(); row['requestBodySha256'] = digest(row['requestBody'].encode())
        with self.assertRaises(ValueError): self.check(self.alter_journal('primary-http.jsonl', shorten))

    def test_prospective_whole_context_and_four_original_indices_cannot_be_reduced(self):
        protocol = copy.deepcopy(self.protocol); protocol.update(workerConcurrency=1)
        with self.assertRaises(ValueError): self.check(protocol=protocol)


class PreparationTest(unittest.TestCase):
    def test_existing_destination_rejected_before_model_or_source_actions(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(cli.launcher, 'frozen_sources') as sources, patch.object(cli.subprocess, 'Popen') as process:
            self.assertEqual(cli.main(['--context-profile', 'missing', '--context-profile-file-sha256', '0'*64, '--runtime-python', 'missing',
                '--manifest', 'missing', '--evidence-dir', temporary, '--cancel-at-original-index', '1']), 1)
            sources.assert_not_called(); process.assert_not_called()

    def failure(self, unknown_cleanup=False):
        context, _, _, _ = fixture.fixture()
        private = ROOT/'docs/private'; private.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=private, prefix='two-slot-startup-failure-') as temporary:
            root = Path(temporary); manifest = root/'manifest.json'; manifest.write_bytes(b'Synthetic manifest; no model.\n')
            context['manifestFileSha256'] = digest(manifest.read_bytes()); context = sealed({k: v for k, v in context.items() if k != 'sha256'})
            context_path = root/'context.json'; context_path.write_bytes(encoded(context)); directory = root/'run'
            process = Mock(pid=888888888, returncode=1); process.poll.return_value = 1
            def remaining(_owned, errors):
                if unknown_cleanup: errors.append('inventory:SyntheticFailure'); return None
                return []
            with patch.object(cli, 'historical_context_sources'), patch.object(cli.launcher, 'frozen_sources', return_value=('0'*40, {})), \
                patch.object(cli, 'verify_manifest', return_value=({}, None)), patch.object(cli, 'verify_profile'), \
                patch.object(cli.subprocess, 'check_output', return_value=encoded(context['tokenizerEnvironment'])), \
                patch.object(cli.subprocess, 'Popen', return_value=process) as child, patch.object(cli.runtime, 'request') as network, \
                patch.object(cli.runtime, 'remaining_owned_processes', side_effect=remaining):
                status = cli.main(['--context-profile', str(context_path), '--context-profile-file-sha256', digest(context_path.read_bytes()),
                    '--runtime-python', sys.executable, '--manifest', str(manifest), '--evidence-dir', str(directory), '--cancel-at-original-index', '1'])
                self.assertEqual(status, 1); child.assert_called_once(); network.assert_not_called()
            result = json.loads((directory/'result.json').read_bytes())
            self.assertEqual(result['status'], 'failed'); self.assertEqual(result['failure']['type'], 'ValueError')
            self.assertEqual(result['ownedPids'], [888888888]); self.assertIn('owned-pids.jsonl', result['artifactSha256'])
            self.assertEqual(result['remainingOwnedPids'], None if unknown_cleanup else []); self.assertIsNone(result['evidence'])

    def test_startup_failure_retains_owned_pid_journal_and_failure_receipt(self): self.failure()
    def test_unknown_cleanup_cannot_be_reported_as_success(self): self.failure(unknown_cleanup=True)


@unittest.skipUnless(shutil.which('node') and (ROOT/'node_modules/tsx').exists(), 'Requires Node diagnostic dependencies')
class ReplayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls, suite=diagnostic, actor=TwoSlotActorTest):
        diagnostic = suite; cls.suite = suite
        actor.setUpClass(); cls.temporary = tempfile.TemporaryDirectory(); cls.root = Path(cls.temporary.name)/'sources'; cls.root.mkdir()
        paths = list(dict.fromkeys([*diagnostic.SOURCE_PATHS, *CONTEXT_PATHS]))
        names = set(subprocess.check_output(['git', 'ls-files', '--', *paths], cwd=ROOT, text=True).splitlines())
        # New files may still be untracked during the first development replay.
        names.update(p for p in paths if (ROOT/p).is_file()); names.add('scripts/lib/decision_two_slot_cancellation.py')
        names.add(Path(suite.__file__).relative_to(ROOT).as_posix())
        for name in sorted(names):
            target = cls.root/name; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes((ROOT/name).read_bytes())
        for args in (['init', '--quiet'], ['add', '.'], ['-c', 'user.name=Synthetic fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '--quiet', '-m', 'Synthetic receipt replay sources']):
            subprocess.run(['git', *args], cwd=cls.root, check=True)
        commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=cls.root, text=True).strip()
        def pins(paths):
            selected = subprocess.check_output(['git', 'ls-files', '--', *paths], cwd=cls.root, text=True).splitlines()
            return {name: digest((cls.root/name).read_bytes()) for name in selected}
        cls.context = copy.deepcopy(actor.context); cls.context.update(sourceCommit=commit, sourceFiles=pins(CONTEXT_PATHS))
        cls.context = sealed({k: v for k, v in cls.context.items() if k != 'sha256'})
        cls.plan = sealed({**actor.plan, 'context': cls.context, 'contextProfileFileSha256': digest(encoded(cls.context)), 'sourceCommit': commit,
            'sourceFiles': pins(diagnostic.SOURCE_PATHS), 'runtime': cls.context['tokenizerEnvironment'], 'profileFileSha256': cls.context['profileFileSha256'],
            'manifestFileSha256': cls.context['manifestFileSha256'], **diagnostic.AUTHORITY})
        cls.result = sealed({**actor.result, 'planSha256': cls.plan['sha256']}); cls.artifacts = copy.deepcopy(actor.artifacts)
        cls().replay()

    @classmethod
    def tearDownClass(cls): cls.temporary.cleanup()

    def replay(self, plan=None, result=None, artifacts=None):
        plan = sealed({k: v for k, v in (plan or self.plan).items() if k != 'sha256'}); result = copy.deepcopy(result or self.result)
        artifacts = artifacts or self.artifacts; result['planSha256'] = plan['sha256']; result['artifactSha256'] = {n: digest(b) for n, b in artifacts.items()}
        result = sealed({k: v for k, v in result.items() if k != 'sha256'})
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary); (directory/'plan.json').write_bytes(encoded(plan)); (directory/'result.json').write_bytes(encoded(result))
            context_path = directory/'context.json'; context_path.write_bytes(encoded(self.context))
            for name, body in artifacts.items(): (directory/name).write_bytes(body)
            original_popen = subprocess.Popen
            def git_only(args, *positional, **kwargs):
                if args[0] != 'git': raise AssertionError('Replay attempted model/worker process')
                return original_popen(args, *positional, **kwargs)
            with patch.object(cli.subprocess, 'Popen', side_effect=git_only), patch('socket.create_connection', side_effect=AssertionError('Replay attempted network')):
                return self.suite.verify(self.root, directory, context_path, context_sha=digest(encoded(self.context)), plan_sha=digest(encoded(plan)), result_sha=digest(encoded(result)))

    def test_complete_historical_source_and_raw_replay_makes_no_model_calls(self):
        receipt = self.replay(); self.assertEqual(receipt['status'], 'pass')
        self.assertEqual(receipt['inventory']['modelCallsDuringVerification'], 0); self.assertFalse(receipt['inventory']['liveCleanupVerified'])
        self.assertFalse(receipt['inventory']['gpuKernelPreemptionEstablished'])

    def test_missing_source_or_raw_artifact_fails_after_resealing(self):
        plan = copy.deepcopy(self.plan); plan['sourceFiles'].pop('scripts/run-two-slot-native-cancellation.mts')
        with self.assertRaises(ValueError): self.replay(plan=plan)
        artifacts = copy.deepcopy(self.artifacts); artifacts.pop('trace-peer-after-cancel.http.json')
        with self.assertRaises(ValueError): self.replay(artifacts=artifacts)

    def test_hidden_physical_call_and_unknown_cleanup_fail_after_resealing(self):
        for alter in (lambda r: r.update(remainingOwnedPids=None), lambda r: r['samples'][-1]['counters'].update(ok=999),
            lambda r: r['warmup'].pop(), lambda r: r.update(runtimeExitCodes=[0, 130])):
            result = copy.deepcopy(self.result); alter(result)
            with self.assertRaises(ValueError): self.replay(result=result)


if __name__ == '__main__': unittest.main()
