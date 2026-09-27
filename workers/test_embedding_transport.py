"""Real HTTP, pipe backpressure and ownership failures for the worker session transport."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, redirect_stderr
from http.server import BaseHTTPRequestHandler
from pathlib import Path
import json
import io
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'workers'))
import embedding_transport as embedding_session
from embedding_transport import EmbeddingSession, EmbeddingSessionPool
from test_embedding_http_deadline import endpoint, slow_endpoint
import agat_worker
from telemetry import WorkerTelemetry


class Echo(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if self.path == '/error':
            code, body = 503, b'synthetic refusal' * 1000
        elif self.path == '/oversized':
            code, body = 200, b'x' * 10000
        elif self.path == '/wide':
            code, body = 200, json.dumps({'data': [
                {'index': i, 'embedding': [0.12345678901234567] * 4096} for i in range(32)
            ]}).encode()
        else:
            code, body = 200, json.dumps({'request': request, 'authorization': self.headers.get('Authorization'),
                                         'origin': self.server.server_port}).encode()
        self.send_response(code)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


@contextmanager
def observed_processes():
    children = []
    spawn = subprocess.Popen

    def start(*args, **kwargs):
        process = spawn(*args, **kwargs)
        children.append(process)
        return process

    try:
        with patch.object(embedding_session.subprocess, 'Popen', side_effect=start):
            yield children
    finally:
        leaked = []
        for process in children:
            if process.poll() is None:
                process.kill()
                process.wait()
                leaked.append(process.pid)
            if not process.stdin.closed or not process.stdout.closed:
                process.stdin.close()
                process.stdout.close()
                leaked.append(process.pid)
        if leaked:
            raise AssertionError(f'Unreaped helper or unclosed pipes: {leaked}')


def request(session, url, **kwargs):
    return session.request(url, {'model': 'synthetic', 'input': ['private-test-input']},
                           {'Authorization': 'Bearer private-test-key', 'Content-Type': 'application/json'},
                           **{'timeout': 3, **kwargs})


class WorkerEmbeddingSessionTests(unittest.TestCase):
    def test_reuses_ready_helper_and_keeps_headers_inputs_and_origins_separate(self):
        with endpoint(Echo) as first, endpoint(Echo) as second, observed_processes() as children:
            with EmbeddingSession() as session:
                session.warmup()
                pid = session.process_id
                for index, port in enumerate([first, second, first]):
                    payload = {'input': [f'new-input-{index}'], 'model': f'model-{index}'}
                    result = json.loads(session.request(f'http://127.0.0.1:{port}/echo', payload,
                        {'Authorization': f'Bearer key-{index}'}, timeout=3))
                    self.assertEqual(result, {'request': payload, 'authorization': f'Bearer key-{index}', 'origin': port})
                    self.assertEqual(session.process_id, pid)
                self.assertEqual(session.starts, 1)
            self.assertEqual(session.reaps, 1)
            self.assertEqual(children[0].returncode, 0)
            self.assertNotIn('private-test', ' '.join(children[0].args))
            session.close()  # Idempotent close.

    def test_http_errors_and_byte_limits_do_not_contaminate_following_reply(self):
        with endpoint(Echo) as port, observed_processes() as children, EmbeddingSession() as session:
            base = f'http://127.0.0.1:{port}'
            session.warmup()
            for path, message in [('/error', 'HTTP 503'), ('/oversized', 'exceeds 4096')]:
                with self.assertRaisesRegex(RuntimeError, message) as raised:
                    request(session, base + path, max_response_bytes=4096, max_error_bytes=20)
                self.assertLess(len(str(raised.exception)), 100)
                self.assertIn('request', json.loads(request(session, base + '/echo')))
            self.assertEqual(len(children), 1)

    def test_full_batch_crosses_many_partial_pipe_reads(self):
        with endpoint(Echo) as port, observed_processes(), EmbeddingSession() as session:
            body = request(session, f'http://127.0.0.1:{port}/wide')
            vectors = json.loads(body)['data']
            self.assertGreater(len(body), 2_000_000)
            self.assertEqual(len(vectors), 32)
            self.assertTrue(all(row['embedding'] == [0.12345678901234567] * 4096 for row in vectors))

    def test_cancelled_active_headers_body_and_error_reap_before_tail_release_and_recover(self):
        for mode in ('headers', 'body', 'error'):
            with self.subTest(mode=mode), endpoint(Echo) as port, observed_processes() as children:
                with EmbeddingSession() as session, slow_endpoint(mode) as (url, entered, release), ThreadPoolExecutor(max_workers=1) as pool:
                    session.warmup()
                    cancelled = threading.Event()
                    future = pool.submit(request, session, url, cancelled=cancelled)
                    self.assertTrue(entered.wait(2))
                    cancelled.set()
                    with self.assertRaisesRegex(RuntimeError, 'cancelled'):
                        future.result(timeout=2)
                    self.assertFalse(release.is_set())
                    self.assertIsNone(session.process_id)
                    self.assertIsNotNone(children[0].returncode)
                    self.assertTrue(children[0].stdin.closed and children[0].stdout.closed)
                    self.assertIn('request', json.loads(request(session, f'http://127.0.0.1:{port}/echo')))
                    self.assertEqual(session.starts, 2)
                    release.set()

    def test_total_deadline_covers_trickling_success_and_error_body(self):
        for mode in ('body', 'error'):
            with self.subTest(mode=mode), observed_processes(), EmbeddingSession() as session:
                session.warmup()
                with slow_endpoint(mode) as (url, entered, release):
                    began = time.monotonic()
                    with self.assertRaisesRegex(RuntimeError, 'deadline'):
                        request(session, url, timeout=0.2)
                    self.assertTrue(entered.is_set())
                    self.assertLess(time.monotonic() - began, 2)
                    self.assertFalse(release.is_set())
                    self.assertIsNone(session.process_id)

    def test_queued_timeout_and_cancellation_leave_current_owner_alive(self):
        for cancelled_waiter in (False, True):
            with self.subTest(cancelled=cancelled_waiter), observed_processes() as children:
                with EmbeddingSession() as session, slow_endpoint('headers') as (url, entered, release), ThreadPoolExecutor(max_workers=1) as pool:
                    session.warmup()
                    pid = session.process_id
                    owner = pool.submit(request, session, url)
                    self.assertTrue(entered.wait(2))
                    cancelled = threading.Event()
                    timer = threading.Timer(0.05, cancelled.set) if cancelled_waiter else None
                    try:
                        if timer:
                            timer.start()
                        with self.assertRaisesRegex(RuntimeError, 'cancelled' if cancelled_waiter else 'deadline'):
                            request(session, url, timeout=1 if cancelled_waiter else 0.05, cancelled=cancelled)
                        self.assertEqual(session.process_id, pid)
                        self.assertIsNone(children[0].poll())
                    finally:
                        release.set()
                        if timer:
                            timer.join()
                    self.assertEqual(json.loads(owner.result(timeout=2))['data'][0]['embedding'], [1, 0])
                    self.assertEqual(len(children), 1)

    def test_separate_sessions_cancel_only_their_own_request(self):
        with observed_processes(), EmbeddingSession() as one, EmbeddingSession() as two:
            one.warmup()
            two.warmup()
            second_pid = two.process_id
            with slow_endpoint('headers') as (url1, entered1, release1), slow_endpoint('body') as (url2, entered2, release2), ThreadPoolExecutor(max_workers=2) as pool:
                cancelled = threading.Event()
                failed = pool.submit(request, one, url1, cancelled=cancelled)
                healthy = pool.submit(request, two, url2)
                self.assertTrue(entered1.wait(2) and entered2.wait(2))
                cancelled.set()
                with self.assertRaisesRegex(RuntimeError, 'cancelled'):
                    failed.result(timeout=2)
                self.assertEqual(two.process_id, second_pid)
                self.assertFalse(healthy.done())
                release2.set()
                self.assertEqual(json.loads(healthy.result(timeout=2))['data'][0]['embedding'], [1, 0])
                release1.set()

    def test_close_aborts_active_request_and_rejects_following_work(self):
        with observed_processes(), EmbeddingSession() as session, slow_endpoint('headers') as (url, entered, release), ThreadPoolExecutor(max_workers=1) as pool:
            session.warmup()
            future = pool.submit(request, session, url)
            self.assertTrue(entered.wait(2))
            session.close()
            with self.assertRaisesRegex(RuntimeError, 'closed'):
                future.result(timeout=2)
            self.assertFalse(release.is_set())
            with self.assertRaisesRegex(RuntimeError, 'closed'):
                request(session, url)
            with self.assertRaisesRegex(RuntimeError, 'closed'):
                session.warmup()
            self.assertEqual(session.starts, session.reaps)
            release.set()

    def test_precancel_invalid_timeout_and_limits_do_not_spawn(self):
        with observed_processes() as children, EmbeddingSession() as session:
            cancelled = threading.Event()
            cancelled.set()
            with self.assertRaisesRegex(RuntimeError, 'cancelled'):
                request(session, 'http://unused.invalid', cancelled=cancelled)
            for value in (True, 0, -1, float('nan'), float('inf'), 901):
                with self.subTest(timeout=value), self.assertRaises(ValueError):
                    request(session, 'http://unused.invalid', timeout=value)
            for key, value in [('max_response_bytes', -1), ('max_error_bytes', 0), ('max_response_bytes', True),
                               ('max_response_bytes', 8388609), ('max_error_bytes', 4097)]:
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    request(session, 'http://unused.invalid', **{key: value})
            with self.assertRaisesRegex(ValueError, '2 MiB'):
                session.request('http://unused.invalid', {'input': ['x' * 2_097_152]}, {}, timeout=1)
            self.assertEqual(children, [])

    def test_backpressure_on_stalled_helper_still_obeys_deadline(self):
        with tempfile.TemporaryDirectory() as directory, observed_processes() as children:
            helper = Path(directory) / 'blocked.py'
            helper.write_text('import signal,time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(30)\n')
            with patch.object(embedding_session, '__file__', str(helper)), EmbeddingSession() as session:
                began = time.monotonic()
                with self.assertRaisesRegex(RuntimeError, 'deadline'):
                    session.request('http://unused.invalid', {'input': ['private-test-input' * 60000]},
                                    {'Authorization': 'private-test-key'}, timeout=0.2)
                self.assertLess(time.monotonic() - began, 2)
                self.assertEqual(session.starts, session.reaps)
                self.assertNotIn('private-test', ' '.join(children[0].args))

    def test_protocol_faults_retire_and_next_request_uses_fresh_helper(self):
        variants = {
            'wrong-id': "send(b'12345678S{}')",
            'kind': "send(identity + b'X{}')",
            'length': "out.write(struct.pack('!Q', 999999999));out.flush();time.sleep(30)",
            'short': "out.write(struct.pack('!Q', 100) + identity + b'Sx');out.flush();time.sleep(30)",
            'exit': 'sys.exit(3)',
            'extra-success': "send(identity + b'S' + b'x' * 100)",
        }
        for name, fault in variants.items():
            with self.subTest(fault=name), tempfile.TemporaryDirectory() as directory, endpoint(Echo) as port, observed_processes(), EmbeddingSession() as session:
                helper = Path(directory) / 'fault.py'
                helper.write_text("import sys,struct,time\nsource=sys.stdin.buffer;out=sys.stdout.buffer\n"
                                  "size,=struct.unpack('!Q',source.read(8));frame=source.read(size);identity=frame[:8]\n"
                                  "def send(body):\n out.write(struct.pack('!Q',len(body))+body);out.flush()\n" + fault + '\n')
                with patch.object(embedding_session, '__file__', str(helper)):
                    with self.assertRaises(RuntimeError):
                        request(session, f'http://127.0.0.1:{port}/echo', timeout=0.5, max_response_bytes=50)
                self.assertIsNone(session.process_id)
                self.assertEqual(session.reaps, 1)
                self.assertIn('request', json.loads(request(session, f'http://127.0.0.1:{port}/echo')))
                self.assertEqual(session.starts, 2)

    def test_readiness_rejects_wrong_body_and_reaps_helper(self):
        with tempfile.TemporaryDirectory() as directory, observed_processes(), EmbeddingSession() as session:
            helper = Path(directory) / 'fake-ready.py'
            helper.write_text("import sys,struct,time\ni=sys.stdin.buffer;o=sys.stdout.buffer\n"
                              "n,=struct.unpack('!Q',i.read(8));f=i.read(n);b=f[:8]+b'Sother'\n"
                              "o.write(struct.pack('!Q',len(b))+b);o.flush();time.sleep(30)\n")
            with patch.object(embedding_session, '__file__', str(helper)):
                with self.assertRaisesRegex(RuntimeError, 'readiness'):
                    session.warmup()
            self.assertIsNone(session.process_id)

    def test_cancelled_owner_hands_fresh_helper_to_queued_successor(self):
        with endpoint(Echo) as port, observed_processes() as children, EmbeddingSession() as session:
            session.warmup()
            with slow_endpoint('headers') as (url, entered, release), ThreadPoolExecutor(max_workers=2) as pool:
                cancelled = threading.Event()
                owner = pool.submit(request, session, url, cancelled=cancelled)
                self.assertTrue(entered.wait(2))
                waiting = threading.Event()

                def successor():
                    waiting.set()
                    return request(session, f'http://127.0.0.1:{port}/echo')

                following = pool.submit(successor)
                self.assertTrue(waiting.wait(2))
                self.assertFalse(following.done())
                cancelled.set()
                with self.assertRaisesRegex(RuntimeError, 'cancelled'):
                    owner.result(timeout=2)
                self.assertIn('request', json.loads(following.result(timeout=2)))
                self.assertFalse(release.is_set())
                self.assertEqual(session.starts, 2)
                self.assertIsNotNone(children[0].returncode)
                self.assertIsNone(children[1].poll())
                release.set()

    def test_idle_close_kills_helper_that_ignores_pipe_eof(self):
        with tempfile.TemporaryDirectory() as directory, observed_processes() as children, EmbeddingSession() as session:
            helper = Path(directory) / 'ignore-eof.py'
            helper.write_text("import sys,struct,time\ni=sys.stdin.buffer;o=sys.stdout.buffer\n"
                              "n,=struct.unpack('!Q',i.read(8));f=i.read(n);b=f[:8]+b'Sready-v1'\n"
                              "o.write(struct.pack('!Q',len(b))+b);o.flush();time.sleep(30)\n")
            with patch.object(embedding_session, '__file__', str(helper)):
                session.warmup()
            began = time.monotonic()
            session.close()
            self.assertLess(time.monotonic() - began, 2)
            self.assertNotEqual(children[0].returncode, 0)
            self.assertEqual(session.starts, session.reaps)

    def test_idle_helper_exit_is_reaped_and_next_request_recovers(self):
        with endpoint(Echo) as port, observed_processes() as children, EmbeddingSession() as session:
            session.warmup()
            children[0].kill()
            children[0].wait()
            self.assertIn('request', json.loads(request(session, f'http://127.0.0.1:{port}/echo')))
            self.assertEqual(session.starts, 2)
            self.assertEqual(session.reaps, 1)

    def test_repeated_deadlines_return_file_descriptors_and_guard_threads(self):
        directory = '/proc/self/fd' if Path('/proc/self/fd').exists() else '/dev/fd'
        before = len(os.listdir(directory))
        with observed_processes(), slow_endpoint('body') as (url, _entered, _release):
            with EmbeddingSession() as session:
                for _ in range(4):
                    session.warmup()
                    with self.assertRaisesRegex(RuntimeError, 'deadline'):
                        request(session, url, timeout=0.08)
                self.assertEqual(session.starts, session.reaps)
        self.assertEqual(len(os.listdir(directory)), before)
        self.assertFalse(any(thread.name == 'embedding-session-deadline' for thread in threading.enumerate()))



class WorkerEmbeddingPoolTests(unittest.TestCase):
    def config(self, *options):
        with patch.dict(os.environ, {'AGAT_EMBEDDING_TRANSPORT': 'isolated'}), patch.object(sys, 'argv',
                ['agat_worker.py', '--models', 'local', '--model-discovery', 'off', '--no-web', *options]):
            return agat_worker.parse_args()

    def test_config_default_env_cli_and_invalid_value(self):
        self.assertEqual(self.config().embedding_transport, 'isolated')
        self.assertEqual(self.config('--embedding-transport', 'session').embedding_transport, 'session')
        with patch.dict(os.environ, {'AGAT_EMBEDDING_TRANSPORT': 'session'}), patch.object(sys, 'argv', ['agat_worker.py']):
            self.assertEqual(agat_worker.parse_args().embedding_transport, 'session')
        for value in ('typo', ''):
            with patch.dict(os.environ, {'AGAT_EMBEDDING_TRANSPORT': value}), patch.object(sys, 'argv', ['agat_worker.py']), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    agat_worker.parse_args()

    def test_lazy_pool_reuses_one_helper_for_serial_work(self):
        with observed_processes() as children, endpoint(Echo) as port, EmbeddingSessionPool(4) as pool:
            self.assertEqual(children, [])
            for _ in range(5):
                self.assertIn('request', json.loads(request(pool, f'http://127.0.0.1:{port}/echo')))
            self.assertEqual(len(children), 1)

    def test_pool_capacity_is_enforced_for_more_callers(self):
        entered, release = threading.Event(), threading.Event()
        hits = []
        lock = threading.Lock()

        class Blocked(Echo):
            def do_POST(self):
                with lock:
                    hits.append(self.path)
                    if len(hits) == 2:
                        entered.set()
                release.wait(3)
                super().do_POST()

        with observed_processes() as children, endpoint(Blocked) as port, EmbeddingSessionPool(2) as pool, ThreadPoolExecutor(max_workers=6) as executor:
            futures = [executor.submit(request, pool, f'http://127.0.0.1:{port}/echo', timeout=4) for _ in range(6)]
            try:
                self.assertTrue(entered.wait(2))
                self.assertEqual(len(hits), 2)
                self.assertEqual(len(children), 2)
            finally:
                release.set()
            self.assertTrue(all('request' in json.loads(future.result(timeout=2)) for future in futures))
            self.assertEqual(len(hits), 6)
            self.assertEqual(len(children), 2)

    def test_pool_waiter_deadline_or_cancel_does_not_affect_owner(self):
        for cancel in (False, True):
            with self.subTest(cancel=cancel), observed_processes() as children, EmbeddingSessionPool(1) as pool:
                with slow_endpoint('headers') as (url, entered, release), ThreadPoolExecutor(max_workers=1) as executor:
                    owner = executor.submit(request, pool, url)
                    self.assertTrue(entered.wait(2))
                    cancelled = threading.Event()
                    timer = threading.Timer(.04, cancelled.set) if cancel else None
                    try:
                        if timer:
                            timer.start()
                        with self.assertRaisesRegex(RuntimeError, 'cancelled' if cancel else 'deadline'):
                            request(pool, url, timeout=1 if cancel else .04, cancelled=cancelled)
                        self.assertIsNone(children[0].poll())
                    finally:
                        release.set()
                        if timer:
                            timer.join()
                    self.assertIn('data', json.loads(owner.result(timeout=2)))
                    self.assertEqual(len(children), 1)

    def test_pool_close_aborts_active_and_waiting_requests(self):
        with observed_processes(), EmbeddingSessionPool(1) as pool, slow_endpoint('headers') as (url, entered, release), ThreadPoolExecutor(max_workers=2) as executor:
            owner = executor.submit(request, pool, url)
            self.assertTrue(entered.wait(2))
            waiting = executor.submit(request, pool, url)
            pool.close()
            for future in (owner, waiting):
                with self.assertRaisesRegex(RuntimeError, 'closed'):
                    future.result(timeout=2)
            self.assertFalse(release.is_set())
            with self.assertRaisesRegex(RuntimeError, 'closed'):
                request(pool, url)
            release.set()

    def test_waiting_time_is_not_added_again_to_transport_deadline(self):
        with observed_processes(), EmbeddingSessionPool(1) as pool:
            with slow_endpoint('headers') as (first, entered1, release1), slow_endpoint('body') as (second, entered2, _release2), ThreadPoolExecutor(max_workers=1) as executor:
                owner = executor.submit(request, pool, first)
                self.assertTrue(entered1.wait(2))
                timer = threading.Timer(.18, release1.set)
                began = time.monotonic()
                timer.start()
                try:
                    with self.assertRaisesRegex(RuntimeError, 'deadline'):
                        request(pool, second, timeout=.3)
                    elapsed = time.monotonic() - began
                    self.assertTrue(entered2.is_set())
                    self.assertLess(elapsed, .43, 'Queue time must reduce the HTTP budget')
                    owner.result(timeout=2)
                finally:
                    release1.set()
                    timer.join()

    def test_worker_closes_transport_on_success_and_failure(self):
        for fail in (False, True):
            with self.subTest(fail=fail), observed_processes() as children, endpoint(Echo) as port:
                config = self.config('--embedding-transport', 'session', '--concurrency', '4')
                telemetry = Mock()

                def work(actual_config, actual_telemetry, transport):
                    self.assertIs(actual_config, config)
                    self.assertIs(actual_telemetry, telemetry)
                    self.assertIsNotNone(transport)
                    result = transport(f'http://127.0.0.1:{port}/echo', {'input': ['source']}, {}, timeout=3, cancelled=None,
                                       max_response_bytes=8388608, max_error_bytes=4096)
                    self.assertIn('request', json.loads(result))
                    self.assertIsNone(children[0].poll())
                    if fail:
                        raise RuntimeError('synthetic worker failure')
                    return 0

                with patch.object(agat_worker, 'WorkerTelemetry', return_value=telemetry), patch.object(agat_worker, '_worker_loop_with_telemetry', side_effect=work):
                    if fail:
                        with self.assertRaisesRegex(RuntimeError, 'synthetic worker failure'):
                            agat_worker.worker_loop(config)
                    else:
                        self.assertEqual(agat_worker.worker_loop(config), 0)
                self.assertEqual(children[0].returncode, 0)
                self.assertTrue(children[0].stdin.closed and children[0].stdout.closed)
                telemetry.shutdown.assert_called_once()

    def test_default_worker_does_not_allocate_a_pool(self):
        config = self.config()
        with observed_processes() as children, patch.object(agat_worker, 'WorkerTelemetry'), patch.object(agat_worker, '_worker_loop_with_telemetry', return_value=0) as work:
            self.assertEqual(agat_worker.worker_loop(config), 0)
            self.assertIsNone(work.call_args.args[2])
            self.assertEqual(children, [])

    def test_supported_chunk_and_query_budgets_fit_framed_transport(self):
        # Coordinator caps chunks at 4000 characters x 32; retrieval query is
        # capped at 50000 characters. NUL has worst-case JSON escaping (6 bytes).
        class Vectors(Echo):
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                body = json.dumps({'data': [{'index': index, 'embedding': [1, 0]} for index in range(len(payload['input']))]}).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        with observed_processes() as children, endpoint(Vectors) as port, EmbeddingSessionPool(1) as pool:
            client = agat_worker.LocalModelClient(f'http://127.0.0.1:{port}', '', telemetry=WorkerTelemetry(enabled=False), embedding_request=pool.request)
            self.assertEqual(client.embed('local', ['\0' * 4000] * 32), [[1.0, 0.0]] * 32)
            self.assertEqual(client.embed('local', ['\0' * 50000]), [[1.0, 0.0]])
            self.assertEqual(len(children), 1)

    def test_invalid_pool_capacity_rejects_before_starting_process(self):
        with observed_processes() as children:
            for value in (True, 0, -1, 33, 1.5):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    EmbeddingSessionPool(value)
            self.assertEqual(children, [])


if __name__ == '__main__':
    unittest.main()
